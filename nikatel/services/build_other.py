"""The other tunnel engines, ported from the old panel: BackPack (reverse, many transports), BackPack
Direct (an L3 link the Iran server dials: udp / quic / pck / sni / xdi), Paqet (KCP over raw sockets)
and Chisel (reverse tunnel over a WebSocket). Same shape as `builder`: validate, action_log entry,
build in a thread, every file through `put_file`, every service through `unit_enable_restart`, a
failed build removes what it created. Every tunnel gets a *test door*: a port on the Iran side that
leads to a small sink on the foreign server, so quality (latency / jitter / loss / speed) can be
measured through the real tunnel at any time.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Server, Tunnel
from ..ssh import Node, SSHError, run_action, run_script
from . import engines
from .builder import PortMap, _node, _put, _say, _unit, parse_ports, pretty_name
from .events import emit
from .tunnels import check_tunnel

log = get_logger("build-other")

# key: (label, hint, needs root raw sockets, udp carrier)
BP_TRANSPORTS = {
    "tcpmux": ("TCP Mux", "many users, few connections (recommended)", False, False),
    "tcp": ("TCP", "plain, lowest overhead on a clean route", False, False),
    "stealth": ("TCP + Stealth", "looks like random bytes, for heavy filtering", False, False),
    "pck": ("TCP + PCK", "raw TCP segments, for routes that stall plain TCP (root)", True, False),
    "kcp": ("UDP + KCP + FEC", "lossy routes, games (UDP must be open)", False, True),
    "udp": ("UDP", "UDP services only, no reliability layer", False, True),
    "quic": ("UDP + QUIC", "TLS 1.3 over QUIC (test it on your route first)", False, True),
    "ws": ("WebSocket", "clear text, only behind your own TLS", False, False),
    "wsmux": ("WebSocket Mux", "WebSocket with multiplexing", False, False),
    "wss": ("WebSocket TLS", "TLS + decoy page, works behind a CDN", False, False),
    "wssmux": ("WebSocket TLS Mux", "TLS WebSocket with multiplexing", False, False),
    "xdi": ("xDi (ICMP)", "KCP inside ICMP echo, slow, last resort (root)", True, False),
}
BP_MUX = ("tcpmux", "wsmux", "wssmux")
BP_DIRECT = {
    "udp": ("UDP", "plain UDP, no root needed", False, True),
    "quic": ("QUIC", "a real QUIC session on UDP, no root needed", False, True),
    "pck": ("PCK", "looks like an ordinary TCP flow (root)", True, False),
    "sni": ("SNI spoofing", "PCK plus a TLS hello naming a domain your route allows", True, False),
    "xdi": ("xDi (ICMP)", "inside ping, for routes that filter UDP and TCP (root)", True, False),
}
KINDS = ("backpack", "backpack_direct", "paqet", "chisel")
KIND_TITLE = {"backpack": "BackPack", "backpack_direct": "BackPack Direct", "paqet": "Paqet", "chisel": "Chisel"}
_LOCK = threading.Lock()
_BUILDING: set[str] = set()


# ------------------------------------------------------------------ pure helpers (tested)
def parse_simple_facts(out: str) -> dict:
    f = {}

    for line in str(out).splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            f[k.strip()] = v.strip()

    return {"tcp": {int(x) for x in f.get("LISTEN", "").split() if x.isdigit()}, "udp": {int(x) for x in f.get("LISTENU", "").split() if x.isdigit()},
            "units": f.get("UNITS", "").split(), "arch": f.get("ARCH", ""), "ips": f.get("IPS", "").split(),
            "root": f.get("ID") == "0", "ufw": f.get("UFW") == "1", "python": f.get("PY") == "1"}


def parse_net(out: str) -> dict:
    d = {}

    for line in str(out).splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()

    if not (d.get("IF") and d.get("IP") and d.get("MAC")):
        raise RuntimeError(f"could not read the network interface / gateway MAC (interface '{d.get('IF')}', ip '{d.get('IP')}', gateway mac '{d.get('MAC')}')")

    return d


def parse_probe(out: str) -> dict:
    for line in str(out).splitlines():
        if line.startswith("NKPROBE "):
            return json.loads(line[8:])

    raise RuntimeError("the test door did not answer: " + str(out)[-160:])


def quality_score(q: dict) -> int:
    """0-100: speed counts most, then latency, jitter and loss."""
    if not q or q.get("error") or q.get("latency_ms") is None:
        return 0

    speed = min(100.0, ((q.get("down_mbps") or 0) * 0.7 + (q.get("up_mbps") or 0) * 0.3) / 8.0)
    lat = max(0.0, 100.0 - max(0.0, q["latency_ms"] - 40) / 2.0)
    jit = max(0.0, 100.0 - (q.get("jitter_ms") or 0) * 5)
    loss = max(0.0, 100.0 - (q.get("loss_pct") or 0) * 10)
    return round(speed * 0.40 + lat * 0.25 + jit * 0.10 + loss * 0.25)


def _entries(ports: list[PortMap], test_port: int) -> list[str]:
    return [p.entry() for p in ports] + [f"127.0.0.1:{test_port}={test_port}"]


def backpack_texts(iran_host: str, port: int, token: str, transport: str, ports: list[PortMap], test_port: int, preset: str = "turbo", opts: dict | None = None) -> tuple[str, str]:
    """(Iran server toml, foreign client toml) of a reverse BackPack tunnel."""
    o = opts or {}
    mux = transport in BP_MUX
    hb, ka, chan = int(o.get("heartbeat", 20)), int(o.get("keepalive", 75)), int(o.get("channel_size", 8192))
    nd = "true" if o.get("nodelay", True) else "false"
    pool = int(o.get("connection_pool", 16 if mux else 24))
    accept_udp = "true" if (o.get("accept_udp") or transport == "udp") else "false"
    port_lines = "\n".join(f'    "{p}",' for p in _entries(ports, test_port))
    mux_s = (f"mux_con = {int(o.get('mux_con', 8))}\nmux_version = 2\nmux_framesize = 32768\nmux_recievebuffer = 8388608\nmux_streambuffer = 262144\n") if mux else ""
    mux_c = ("mux_version = 2\nmux_framesize = 32768\nmux_recievebuffer = 8388608\nmux_streambuffer = 262144\n") if mux else ""
    server = f"""# Nikatel panel - BackPack server (Iran)
[server]
bind_addr = "0.0.0.0:{port}"
transport = "{transport}"
token = "{token}"
preset = "{preset}"
keepalive_period = {ka}
nodelay = {nd}
heartbeat = {hb}
channel_size = {chan}
log_level = "warn"
web_port = 0
sniffer = false
accept_udp = {accept_udp}
{mux_s}
ports = [
{port_lines}
]
"""
    client = f"""# Nikatel panel - BackPack client (foreign)
[client]
remote_addr = "{iran_host}:{port}"
transport = "{transport}"
token = "{token}"
preset = "{preset}"
connection_pool = {pool}
aggressive_pool = false
keepalive_period = {ka}
dial_timeout = 10
retry_interval = 2
nodelay = {nd}
log_level = "warn"
web_port = 0
sniffer = false
{mux_c}
"""
    return server, client


def direct_texts(foreign_host: str, port: int, token: str, carrier: str, net: int, ports: list[PortMap], test_port: int, opts: dict | None = None) -> tuple[str, str]:
    """(Iran toml - dials, foreign toml - listens) of a BackPack Direct (L3) tunnel on 10.10.<net>.0/30."""
    o = opts or {}
    port_lines = "\n".join(f'    "{p}",' for p in _entries(ports, test_port))
    extra = ""

    if carrier in ("pck", "sni"):
        extra += f"mtu = {int(o.get('mtu') or 1380)}\n"

    if carrier == "sni" and o.get("sni_domain"):
        extra += f'sni_domain = "{o["sni_domain"]}"\n'

    common = f'carrier = "{carrier}"\ntoken = "{token}"\niface = "bp{net}"\n{extra}'
    iran = f"""# Nikatel panel - BackPack direct tunnel (Iran dials)
[l3]
mode = "dial"
addr = "{foreign_host}:{port}"
{common}local_ip = "10.10.{net}.1/30"
peer_ip = "10.10.{net}.2"
accept_udp = {"true" if o.get("accept_udp") else "false"}
ports = [
{port_lines}
]
"""
    foreign = f"""# Nikatel panel - BackPack direct tunnel (foreign listens)
[l3]
mode = "listen"
addr = "0.0.0.0:{port}"
{common}local_ip = "10.10.{net}.2/30"
peer_ip = "10.10.{net}.1"
"""
    return iran, foreign


def bp_unit(name: str, cfg: str, side: str, fw: str = "") -> str:
    pre = f"ExecStartPre=/usr/local/sbin/{fw} up\n" if fw else ""
    post = f"ExecStopPost=/usr/local/sbin/{fw} down\n" if fw else ""
    return f"""[Unit]
Description=Nikatel BackPack tunnel {name} ({side})
After=network.target

[Service]
Type=simple
{pre}ExecStart=/usr/local/bin/backpack -c {cfg}
{post}Restart=always
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
"""


def bp_fw_script(name: str, iface: str) -> str:
    """Let the traffic of the tunnel interface through the server firewall (only our tagged rules)."""
    return f"""#!/bin/sh
# Nikatel BackPack {name}: firewall rules for the tunnel interface
command -v iptables >/dev/null 2>&1 || exit 0
IF={iface}
TAG="nikatel-bp-{name}"
rule() {{ A=$1; C=$2; shift 2
  if [ "$A" = up ]; then iptables -C "$C" "$@" -m comment --comment "$TAG" 2>/dev/null || iptables -I "$C" 1 "$@" -m comment --comment "$TAG"
  else while iptables -C "$C" "$@" -m comment --comment "$TAG" 2>/dev/null; do iptables -D "$C" "$@" -m comment --comment "$TAG"; done; fi
}}
ACT=${{1:-up}}
rule $ACT INPUT -i $IF -j ACCEPT
rule $ACT OUTPUT -o $IF -j ACCEPT
rule $ACT FORWARD -i $IF -j ACCEPT
rule $ACT FORWARD -o $IF -j ACCEPT
exit 0
"""


def forward_rows(ports: list[PortMap], test_port: int, udp: bool) -> list[tuple[str, str, str]]:
    """(listen, target, proto) rows: Paqet forwards and Chisel remotes share this shape."""
    rows = []

    for p in ports:
        rows.append((f"0.0.0.0:{p.listen}", f"127.0.0.1:{p.target or p.listen}", "tcp"))

        if udp:
            rows.append((f"0.0.0.0:{p.listen}", f"127.0.0.1:{p.target or p.listen}", "udp"))

    rows.append((f"127.0.0.1:{test_port}", f"127.0.0.1:{test_port}", "tcp"))
    return rows


def paqet_texts(net_i: dict, net_f: dict, port: int, key: str, foreign_host: str, ports: list[PortMap], test_port: int, opts: dict | None = None) -> tuple[str, str]:
    """(Iran client yaml, foreign server yaml)."""
    o = opts or {}
    mode = str(o.get("kcp_mode") or "fast")
    mode = mode if mode in ("normal", "fast", "fast2", "fast3") else "fast"
    kcp = f"""transport:
  protocol: "kcp"
  conn: {int(o.get("conn", 4))}
  kcp:
    mode: "{mode}"
    block: "aes-128-gcm"
    mtu: {int(o.get("mtu", 1350))}
    key: "{key}"
"""
    foreign = f"""# Nikatel panel - Paqet server (foreign)
role: "server"
log:
  level: "warn"
listen:
  addr: ":{port}"
network:
  interface: "{net_f['IF']}"
  ipv4:
    addr: "{net_f['IP']}:{port}"
    router_mac: "{net_f['MAC']}"
  tcp:
    local_flag: ["PA"]
{kcp}"""
    fw = "\n".join(f'  - listen: "{ls}"\n    target: "{t}"\n    protocol: "{p}"' for ls, t, p in forward_rows(ports, test_port, bool(o.get("accept_udp"))))
    iran = f"""# Nikatel panel - Paqet client (Iran)
role: "client"
log:
  level: "warn"
forward:
{fw}
network:
  interface: "{net_i['IF']}"
  ipv4:
    addr: "{net_i['IP']}:0"
    router_mac: "{net_i['MAC']}"
  tcp:
    local_flag: ["PA"]
    remote_flag: ["PA"]
server:
  addr: "{foreign_host}:{port}"
{kcp}"""
    return iran, foreign


def paqet_fw_script(name: str, port: int) -> str:
    """Keep the kernel out of the raw-socket port on the foreign server (only our tagged rules)."""
    return f"""#!/bin/sh
# Nikatel Paqet {name}: the kernel must not answer on the raw-socket port
P={port}
TAG="nikatel-paqet-{name}"
rule() {{ A=$1; T=$2; C=$3; shift 3
  if [ "$A" = up ]; then iptables -t "$T" -C "$C" "$@" -m comment --comment "$TAG" 2>/dev/null || iptables -t "$T" -I "$C" 1 "$@" -m comment --comment "$TAG"
  else while iptables -t "$T" -C "$C" "$@" -m comment --comment "$TAG" 2>/dev/null; do iptables -t "$T" -D "$C" "$@" -m comment --comment "$TAG"; done; fi
}}
ACT=${{1:-up}}
rule $ACT raw PREROUTING -p tcp --dport $P -j NOTRACK
rule $ACT raw OUTPUT -p tcp --sport $P -j NOTRACK
rule $ACT mangle OUTPUT -p tcp --sport $P --tcp-flags RST RST -j DROP
rule $ACT filter INPUT -p tcp --dport $P -j ACCEPT
rule $ACT filter OUTPUT -p tcp --sport $P -j ACCEPT
exit 0
"""


def paqet_unit(name: str, side: str, fw: str = "") -> str:
    pre = f"ExecStartPre=/usr/local/sbin/{fw} up\n" if fw else ""
    post = f"ExecStopPost=/usr/local/sbin/{fw} down\n" if fw else ""
    return f"""[Unit]
Description=Nikatel Paqet tunnel {name} ({side})
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
{pre}ExecStart=/usr/local/bin/paqet run -c /etc/paqet/{name}.yaml
{post}Restart=always
RestartSec=4
LimitNOFILE=1048576
Environment=GOMAXPROCS=0

[Install]
WantedBy=multi-user.target
"""


def chisel_remotes(ports: list[PortMap], test_port: int, udp: bool) -> list[str]:
    """R: remotes of the client - the Iran server opens every user port and forwards it to the foreign side."""
    return [f"R:{ls}:{t}" + ("/udp" if p == "udp" else "") for ls, t, p in forward_rows(ports, test_port, udp)]


def chisel_unit(name: str, side: str, port: int, remotes: list[str], iran_host: str, tls: bool, fingerprint: str = "") -> str:
    base = "/etc/nikatel-chisel"
    bin_ = "/usr/local/bin/nikatel-chisel"

    if side == "iran":
        tls_args = f" --tls-key {base}/{name}.tls.key --tls-cert {base}/{name}.crt" if tls else ""
        exec_line = f"{bin_} server --host 0.0.0.0 --port {port} --reverse --keyfile {base}/{name}.key --keepalive 25s{tls_args}"
        env = f"{base}/{name}.srv.env"
    else:
        fp = f" --fingerprint {fingerprint}" if fingerprint else ""
        skip = " --tls-skip-verify" if tls else ""
        rem = " ".join(f"'{r}'" for r in remotes)
        exec_line = f"{bin_} client --keepalive 25s --max-retry-interval 5s{fp}{skip} {'https' if tls else 'http'}://{iran_host}:{port} {rem}"
        env = f"{base}/{name}.cli.env"

    return f"""[Unit]
Description=Nikatel Chisel tunnel {name} ({side})
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
EnvironmentFile={env}
ExecStart={exec_line}
Restart=always
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
"""


def free_port(lo: int, hi: int, taken: set[int]) -> int:
    return next((p for p in range(lo, hi) if p not in taken), 0)


# ------------------------------------------------------------------ the build
@dataclass
class Build:
    aid: int
    kind: str
    iran: Server
    foreign: Server
    ports: list[PortMap]
    transport: str              # backpack transport | direct carrier | "kcp" | "ws" / "wss"
    opts: dict = field(default_factory=dict)
    name: str = ""
    port: int = 0
    test_port: int = 0
    net: int = 0
    udp: bool = False
    created: list[Node] = field(default_factory=list)
    measure: str = "full"       # full | latency
    quality: dict = field(default_factory=dict)


def start_build(iran_id: int, foreign_id: int, ports_text: str, kind: str, transport: str = "", opts: dict | None = None, forced_name: str = "", measure: str = "full") -> int:
    """Validate, create the action_log entry, build in a thread. Returns the action id."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to build tunnels")

    if kind not in KINDS:
        raise ValueError("unknown tunnel type")

    opts = dict(opts or {})
    transport = str(transport or "").strip().lower()

    if kind == "backpack":
        transport = transport or "tcpmux"

        if transport not in BP_TRANSPORTS:
            raise ValueError("unknown BackPack transport")
    elif kind == "backpack_direct":
        transport = transport or "udp"

        if transport not in BP_DIRECT:
            raise ValueError("unknown BackPack Direct carrier")

        dom = str(opts.get("sni_domain") or "").strip()

        if transport == "sni" and (not re.fullmatch(r"[A-Za-z0-9.\-]{3,80}", dom) or "." not in dom):
            raise ValueError("SNI spoofing needs a domain your route allows (for example a CDN domain)")
    elif kind == "paqet":
        transport = "kcp"
    else:
        transport = "wss" if transport in ("wss", "tls", "1", "true") else "ws"

    ports = parse_ports(ports_text)

    with session() as db:
        iran, foreign = db.get(Server, iran_id), db.get(Server, foreign_id)

        if iran is None or foreign is None:
            raise ValueError("choose an Iran server and a foreign server")

        if iran.role != "iran" or foreign.role != "foreign":
            raise ValueError("the first server must be an Iran server and the second a foreign one")

        if not iran.enabled or not foreign.enabled:
            raise ValueError("both servers must be enabled")

        pair = f"{iran.name} ⇄ {foreign.name}"

        with _LOCK:
            if pair in _BUILDING:
                raise ValueError("a tunnel between these two servers is already being built")

            _BUILDING.add(pair)

        entry = ActionLog(ref=forced_name or pair, action="build", source="manual",
                          story=[f"Building a {KIND_TITLE[kind]} · {transport} tunnel {pair}, ports {', '.join(p.spec() for p in ports)}"])
        db.add(entry)
        db.flush()
        aid = entry.id
        b = Build(aid, kind, iran, foreign, ports, transport, opts, name=forced_name, measure=measure)
        db.expunge(iran)
        db.expunge(foreign)

    threading.Thread(target=_worker, args=(b, pair), name=f"build-{pair}", daemon=True).start()
    return aid


def _worker(b: Build, pair: str) -> None:
    try:
        _build(b)
    except Exception as e:  # thread boundary
        log.exception("build #%s crashed", b.aid)
        _rollback(b)
        _say(b.aid, f"Stopped: {str(e)[:220]}. Everything created was removed again.", done=True, ok=False)
    finally:
        with _LOCK:
            _BUILDING.discard(pair)


def _engine(kind: str) -> str:
    return {"backpack": "backpack", "backpack_direct": "backpack", "paqet": "paqet", "chisel": "chisel"}[kind]


def _rollback(b: Build) -> None:
    if not b.name:
        return

    for node in {n for n in b.created}:
        try:
            run_action(node, "other_remove", {"NAME": b.name, "KIND": _engine(b.kind), "PORT": str(b.port or 0), "PROTO": "udp" if b.udp else "tcp"}, timeout=60)
        except (SSHError, ValueError) as e:
            log.warning("rollback on %s failed: %s", node.host, e)


def _preflight(b: Build, iran: Node, foreign: Node) -> tuple[dict, dict]:
    fi, ff = parse_simple_facts(run_script(iran, "simple_facts", timeout=45)), parse_simple_facts(run_script(foreign, "simple_facts", timeout=45))

    for label, f, srv in (("Iran", fi, b.iran), ("Foreign", ff, b.foreign)):
        if not f["root"]:
            raise RuntimeError(f"{label} server {srv.name}: the SSH user is not root")

        if not f["python"]:
            raise RuntimeError(f"{label} server {srv.name} has no python3 (needed for the test door)")

        if f["arch"] not in ("x86_64", "amd64"):
            raise RuntimeError(f"{label} server {srv.name}: architecture {f['arch']} is not supported (x86_64 only)")

    for p in b.ports:
        if p.listen in fi["tcp"]:
            raise RuntimeError(f"port {p.listen} is already in use on the Iran server; choose another port")

    _say(b.aid, "Preflight: both servers inspected (root, python3, x86_64, ports free)")
    return fi, ff


def _plan(b: Build, fi: dict, ff: dict) -> None:
    with session() as db:
        rows = list(db.scalars(select(Tunnel)))
        taken_names = {t.name for t in rows}
        used_ports = {int(t.port or 0) for t in rows} | {int((t.record or {}).get("test_port") or 0) for t in rows}
        used_nets = {int((t.record or {}).get("inner_net") or 0) for t in rows}

    if not b.name:
        b.name = pretty_name(b.foreign.name, b.iran.name, taken_names)

    eng = _engine(b.kind)

    while any(u.startswith(f"{eng}-{b.name}") for u in fi["units"] + ff["units"]):
        b.name = pretty_name(b.foreign.name, b.iran.name, taken_names | {b.name})

    taken_i = fi["tcp"] | fi["udp"] | used_ports | {p.listen for p in b.ports}
    taken_f = ff["tcp"] | ff["udp"] | used_ports

    if b.kind == "backpack":
        b.udp = BP_TRANSPORTS[b.transport][3]
        b.port = free_port(9900, 10000, taken_i)                  # control port on the Iran server
        b.test_port = free_port(39100, 39200, taken_i | {b.port})
    elif b.kind == "backpack_direct":
        b.udp = BP_DIRECT[b.transport][3]
        b.port = free_port(9850, 9950, taken_f)                   # carrier port on the foreign server
        b.test_port = free_port(39600, 39700, taken_i | taken_f | {b.port})
        seen = " ".join(fi["ips"] + ff["ips"])
        b.net = next((n for n in range(10, 250) if n not in used_nets and f"10.10.{n}." not in seen), 0)

        if not b.net:
            raise RuntimeError("no free 10.10.x.0/30 network for the tunnel")
    elif b.kind == "paqet":
        b.port = free_port(9700, 9800, taken_f)                   # raw port on the foreign server
        b.test_port = free_port(39200, 39300, taken_i | {b.port})
    else:
        b.port = free_port(9600, 9700, taken_i)                   # control port on the Iran server
        b.test_port = free_port(39000, 39100, taken_i | {b.port})

    if not b.port or not b.test_port:
        raise RuntimeError("no free control / test port")

    where = "the foreign server" if b.kind in ("backpack_direct", "paqet") else "the Iran server"
    _say(b.aid, f"Plan: {b.name}, {'UDP' if b.udp else 'TCP'} port {b.port} on {where}, test door {b.test_port}" + (f", tunnel network 10.10.{b.net}.0/30" if b.net else ""))


def _install_sink(b: Build, foreign: Node, key: str, bind: str = "") -> None:
    b.created.append(foreign)
    out = run_action(foreign, "sink_install", {"NAME": b.name, "PORT": str(b.test_port), "KEY": key, "BIND": bind}, timeout=60)

    if "SINK_OK" not in out:
        raise RuntimeError("the test door did not start on the foreign server: " + out.strip()[-160:])


def _start(node: Node, label: str, unit: str, b: Build) -> None:
    b.created.append(node)
    st = _unit(node, unit)

    if st != "active":
        logs = run_script(node, "unit_log", {"UNIT": unit}, timeout=20)
        raise RuntimeError(f"the {label} side did not start ({unit} is {st}): {logs.strip()[-300:]}")


def _probe(iran: Node, test_port: int, key: str, secs: float = 0, streams: int = 8, pings: int = 3, to: float = 1.2) -> dict:
    out = run_script(iran, "probe_run", {"PORT": str(test_port), "SECS": str(secs), "STREAMS": str(streams), "PINGS": str(pings), "KEY": key, "TO": str(to)}, timeout=int(60 + secs * 4))
    return parse_probe(out)


def _wait_for_data(b: Build, iran: Node, key: str, hint: str, log_node: Node, log_unit: str) -> dict:
    _say(b.aid, "Waiting for the tunnel to carry data")
    last = ""

    for attempt in range(12):
        time.sleep(3 if attempt == 0 else 2)

        try:
            q = _probe(iran, b.test_port, key)

            if q.get("latency_ms") is not None and q.get("loss_pct", 100) < 100:
                _say(b.aid, f"Connected, latency {q['latency_ms']} ms")
                return q

            last = q.get("error") or "no answer"
        except (SSHError, RuntimeError, ValueError) as e:
            last = str(e)[:100]

    logs = ""

    try:
        logs = run_script(log_node, "unit_log", {"UNIT": log_unit}, timeout=20).strip()[-300:]
    except (SSHError, ValueError):
        pass

    raise RuntimeError(f"the tunnel started but no data passes through it ({last}). {hint}" + (f" Log: {logs}" if logs else ""))


def _measure(b: Build, iran: Node, key: str) -> None:
    try:
        if b.measure == "latency":
            _say(b.aid, "Measuring latency, jitter and loss through the tunnel")
            b.quality = _probe(iran, b.test_port, key, secs=0, pings=20, to=2.5)
        else:
            _say(b.aid, "Measuring speed through the tunnel (8 connections each way)")
            b.quality = _probe(iran, b.test_port, key, secs=4, streams=8, pings=20, to=2.5)

        b.quality["score"] = quality_score(b.quality)
        b.quality["at"] = int(time.time())
        _say(b.aid, f"Quality: {b.quality.get('latency_ms')} ms, jitter {b.quality.get('jitter_ms')} ms, loss {b.quality.get('loss_pct')}%"
             + (f", ↓{b.quality.get('down_mbps')} ↑{b.quality.get('up_mbps')} Mb/s" if b.quality.get("down_mbps") is not None else "") + f" → score {b.quality['score']}")
    except (SSHError, RuntimeError, ValueError) as e:
        _say(b.aid, f"Measurement skipped: {str(e)[:100]}")


def _build(b: Build) -> None:
    aid, iran, foreign = b.aid, _node(b.iran), _node(b.foreign)
    fi, ff = _preflight(b, iran, foreign)
    _plan(b, fi, ff)
    eng = _engine(b.kind)

    for label, node in (("Iran", iran), ("foreign", foreign)):
        if engines.ensure_on_node(node, eng):
            _say(aid, f"{engines.ENGINES[eng]['title']} installed on the {label} server")

    token, key = secrets.token_hex(24), secrets.token_hex(8)
    proto = "udp" if b.udp else "tcp"

    if b.kind == "backpack":
        label = BP_TRANSPORTS[b.transport][0]
        _say(aid, f"BackPack · {label}: the foreign server dials the Iran server")
        server, client = backpack_texts(b.iran.host, b.port, token, b.transport, b.ports, b.test_port, str(b.opts.get("preset") or "turbo"), b.opts)
        _install_sink(b, foreign, key)
        _put(iran, f"/etc/backpack/{b.name}.toml", server, "0600")
        _put(iran, f"/etc/systemd/system/backpack-{b.name}.service", bp_unit(b.name, f"/etc/backpack/{b.name}.toml", "Iran"), "0644")

        if fi["ufw"]:
            run_action(iran, "ufw_allow", {"PORT": str(b.port), "PROTO": proto})

        _start(iran, "Iran", f"backpack-{b.name}.service", b)
        _put(foreign, f"/etc/backpack/{b.name}-kharej.toml", client, "0600")
        _put(foreign, f"/etc/systemd/system/backpack-{b.name}-kharej.service", bp_unit(b.name, f"/etc/backpack/{b.name}-kharej.toml", "foreign"), "0644")
        _start(foreign, "foreign", f"backpack-{b.name}-kharej.service", b)
        _say(aid, "BackPack: services running on both servers")
        _wait_for_data(b, iran, key, f"Is {proto.upper()} port {b.port} open between the servers?", foreign, f"backpack-{b.name}-kharej.service")
        direction, tlabel = "reverse", f"BackPack · {label}"

    elif b.kind == "backpack_direct":
        label = BP_DIRECT[b.transport][0]
        _say(aid, f"BackPack · Direct · {label}: the Iran server dials the foreign server")
        iran_toml, foreign_toml = direct_texts(b.foreign.host, b.port, token, b.transport, b.net, b.ports, b.test_port, b.opts)
        fw = f"nikatel-bpfw-{b.name}"
        _install_sink(b, foreign, key, bind="0.0.0.0")

        for label2, node, cfg, text, unit in (("foreign", foreign, f"/etc/backpack/{b.name}-kharej.toml", foreign_toml, f"backpack-{b.name}-kharej.service"),
                                              ("Iran", iran, f"/etc/backpack/{b.name}.toml", iran_toml, f"backpack-{b.name}.service")):
            _put(node, f"/usr/local/sbin/{fw}", bp_fw_script(b.name, f"bp{b.net}"), "0700")
            _put(node, cfg, text, "0600")
            _put(node, f"/etc/systemd/system/{unit}", bp_unit(b.name, cfg, label2, fw), "0644")

            if label2 == "foreign" and ff["ufw"]:
                run_action(foreign, "ufw_allow", {"PORT": str(b.port), "PROTO": proto})

            _start(node, label2, unit, b)

        _say(aid, "Waiting for the tunnel network to come up")
        up = False

        for attempt in range(12):
            time.sleep(3 if attempt == 0 else 2)

            try:
                out = run_script(iran, "gre_verify", {"IFACE": f"bp{b.net}", "PEER": f"10.10.{b.net}.2"}, timeout=30)
                m = re.search(r"(\d+) received", out)

                if m and int(m.group(1)) > 0:
                    up = True
                    break
            except (SSHError, ValueError):
                pass

        if not up:
            raise RuntimeError(f"the tunnel network does not come up: the {label} carrier cannot pass between these servers (is {proto.upper()} port {b.port} open on the foreign server?)")

        _say(aid, "Tunnel network is up, testing data through it")
        _wait_for_data(b, iran, key, "The interface is up but the forwarded ports do not answer.", iran, f"backpack-{b.name}.service")
        direction, tlabel = "direct", f"Direct · {label}"

    elif b.kind == "paqet":
        _say(aid, "Paqet · KCP over raw sockets: the foreign server listens, the Iran server opens the ports")
        net_i, net_f = parse_net(run_script(iran, "paqet_net", timeout=30)), parse_net(run_script(foreign, "paqet_net", timeout=30))
        iran_yaml, foreign_yaml = paqet_texts(net_i, net_f, b.port, token, b.foreign.host, b.ports, b.test_port, b.opts)
        fw = f"nikatel-paqet-fw-{b.name}"
        _install_sink(b, foreign, key)
        _put(foreign, f"/usr/local/sbin/{fw}", paqet_fw_script(b.name, b.port), "0700")
        _put(foreign, f"/etc/paqet/{b.name}.yaml", foreign_yaml, "0600")
        _put(foreign, f"/etc/systemd/system/paqet-{b.name}.service", paqet_unit(b.name, "foreign", fw), "0644")

        if ff["ufw"]:
            run_action(foreign, "ufw_allow", {"PORT": str(b.port), "PROTO": "tcp"})

        _start(foreign, "foreign", f"paqet-{b.name}.service", b)
        _put(iran, f"/etc/paqet/{b.name}.yaml", iran_yaml, "0600")
        _put(iran, f"/etc/systemd/system/paqet-{b.name}.service", paqet_unit(b.name, "Iran"), "0644")
        _start(iran, "Iran", f"paqet-{b.name}.service", b)
        _say(aid, "Paqet: services running on both servers")
        _wait_for_data(b, iran, key, f"Raw sockets can be blocked by the provider, or TCP port {b.port} is closed between the servers.", iran, f"paqet-{b.name}.service")
        direction, tlabel = "direct", "Paqet · KCP"

    else:
        tls = b.transport == "wss"
        _say(aid, "Chisel · reverse tunnel over " + ("TLS WebSocket" if tls else "HTTP WebSocket"))
        remotes = chisel_remotes(b.ports, b.test_port, bool(b.opts.get("accept_udp")))
        _install_sink(b, foreign, key)
        b.created.append(iran)
        run_action(iran, "chisel_prepare", {"NAME": b.name, "TLS": "1" if tls else "0"}, timeout=60)
        _put(iran, f"/etc/nikatel-chisel/{b.name}.srv.env", f"AUTH=nikatel:{token}\n", "0600")
        _put(iran, f"/etc/systemd/system/chisel-{b.name}.service", chisel_unit(b.name, "iran", b.port, remotes, b.iran.host, tls), "0644")

        if fi["ufw"]:
            run_action(iran, "ufw_allow", {"PORT": str(b.port), "PROTO": "tcp"})

        _start(iran, "Iran", f"chisel-{b.name}.service", b)
        fp = (re.search(r"FP=(\S+)", run_script(iran, "chisel_fp", {"NAME": b.name}, timeout=20)) or [None, ""])[1]
        _say(aid, "Iran side running" + (", server key pinned on the client" if fp else ""))
        _put(foreign, f"/etc/nikatel-chisel/{b.name}.cli.env", f"AUTH=nikatel:{token}\n", "0600")
        _put(foreign, f"/etc/systemd/system/chisel-{b.name}-kharej.service", chisel_unit(b.name, "foreign", b.port, remotes, b.iran.host, tls, fp), "0644")
        _start(foreign, "foreign", f"chisel-{b.name}-kharej.service", b)
        _wait_for_data(b, iran, key, f"The control port {b.port} may be closed between the servers.", foreign, f"chisel-{b.name}-kharej.service")
        direction, tlabel = "reverse", "Chisel · " + ("TLS WebSocket" if tls else "WebSocket")

    _measure(b, iran, key)

    with session() as db:
        t = Tunnel(name=b.name, type=eng, transport=b.transport if b.kind != "backpack_direct" else f"l3:{b.transport}", engine=eng,
                   iran_server_id=b.iran.id, foreign_server_id=b.foreign.id, port=b.port,
                   iran_tunnel_ip=f"10.10.{b.net}.1" if b.net else "", foreign_tunnel_ip=f"10.10.{b.net}.2" if b.net else "",
                   forwarded_ports=[p.listen for p in b.ports],
                   record={"kind": b.kind, "direction": direction, "transport_label": tlabel, "port_mapping": ",".join(p.spec() for p in b.ports),
                           "test_port": b.test_port, "inner_net": b.net, "udp_carrier": b.udp, "opts": {k: v for k, v in b.opts.items() if k != "sni_domain" or v},
                           "quality": b.quality, "built_by": "nikatel-panel"},
                   secrets={"token": token, "test_key": key}, state="starting", state_since=int(time.time()))
        db.add(t)
        db.flush()
        tid = t.id
        emit(db, "ok", "tunnels", f"Tunnel {b.name} ({b.iran.name} ⇄ {b.foreign.name}) built: {tlabel}, ports {', '.join(p.spec() for p in b.ports)}"
             + (f", {b.quality.get('latency_ms')} ms" if b.quality.get("latency_ms") is not None else "") + ".", b.name)

    check_tunnel(tid)
    _say(aid, f"Tunnel {b.name} is ready.", done=True, ok=True)


# ------------------------------------------------------------------ remove / quality of an existing tunnel
def remove(t: Tunnel) -> list[str]:
    """Remove a BackPack / Paqet / Chisel tunnel from both servers. Returns per-side results."""
    rec = t.record or {}
    params = {"NAME": t.name, "KIND": t.type, "PORT": str(int(t.port or 0)), "PROTO": "udp" if rec.get("udp_carrier") else "tcp"}
    results = []

    for side, node in (("Iran", _node(t.iran)), ("foreign", _node(t.foreign))):
        try:
            run_action(node, "other_remove", params, timeout=90)
            results.append(f"{side}: removed")
        except (SSHError, ValueError) as e:
            results.append(f"{side}: not removed ({e})")

    return results


def quality(tunnel_id: int, secs: float = 4) -> dict:
    """Latency / jitter / loss / speed through the test door of an existing tunnel (the real tunnel, not GRE)."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        rec, sec = t.record or {}, t.secrets or {}

        if not rec.get("test_port") or not sec.get("test_key"):
            raise ValueError("this tunnel has no test door")

        iran, foreign, name, port, key = _node(t.iran), _node(t.foreign), t.name, int(rec["test_port"]), str(sec["test_key"])
        bind = "0.0.0.0" if str(t.transport or "").startswith("l3:") else ""

    try:   # keep the sink at the current version
        run_action(foreign, "sink_install", {"NAME": name, "PORT": str(port), "KEY": key, "BIND": bind}, timeout=60)
    except (SSHError, ValueError):
        pass

    q = _probe(iran, port, key, secs=secs, streams=8, pings=30, to=2.5)

    if q.get("error") or q.get("latency_ms") is None:
        raise RuntimeError("no data passes through the tunnel right now" + (f" ({q['error']})" if q.get("error") else ""))

    q["score"] = quality_score(q)
    q["at"] = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            rec["quality"] = q
            t.record = rec

    return {"name": name, **q}
