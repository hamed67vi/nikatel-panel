"""The other tunnel engines, ported from the old panel: BackPack (reverse, many transports), BackPack
Direct (an L3 link the Iran server dials: udp / quic / pck / sni / xdi), Paqet (KCP over raw sockets)
and Chisel (a tunnel over a WebSocket: reverse, or forward when the Iran server dials). Same shape as `builder`: validate, action_log entry,
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
from ..models import ActionLog, Server, Tunnel, is_temp
from ..ssh import Node, SSHError, run_action, run_script
from . import engines, livebw
from .builder import (
    PortMap,
    _node,
    _put,
    _say,
    _unit,
    parse_ports,
    pretty_name,
    server_locks,
)
from .events import emit
from .tunnels import check_tunnel, sname

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
    # (the owner, 2026-10-17: "SNI spoofing is not there at all - fix its name or its description"): this is BackPack's own "SNI Spoofing" carrier - raw TCP (PCK) that opens with a FAKE TLS hello naming a domain
    # your route lets through, so a filter that only reads the name in the hello lets it pass; the rest is not TLS at all (it is the tunnel's own sealed frames). It is NOT a spoofed source address - that is "IP spoofing" below.
    "sni": ("SNI spoofing", "raw TCP that opens with a fake TLS hello naming a domain your route allows - the filter reads the name and lets it pass (root)", True, False),
    "spoof": ("IP spoofing", "raw packets whose SOURCE ADDRESS you choose - for paths that block, throttle or count traffic by source address; the provider must let forged sources out: test it first (Servers → Tests → Spoof test) (root)", True, True),
    "xdi": ("xDi (ICMP)", "inside ping, for routes that filter UDP and TCP (root)", True, False),
}
SPOOF_PROFILES = ("udp", "icmp", "tcp", "icmpv6", "proto58", "ipip", "gre")      # the packet disguises of the IP spoofing carrier (both ends use the same one)
KINDS = ("backpack", "backpack_direct", "paqet", "chisel")
KIND_TITLE = {"backpack": "BackPack", "backpack_direct": "BackPack Direct", "paqet": "Paqet", "chisel": "Chisel"}
CHISEL_TRANSPORTS = ("ws", "wss", "fws", "fwss")          # ws / wss: reverse (the foreign server dials the Iran server); fws / fwss: forward (the Iran server dials the foreign server)
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
    """0-100: speed counts most, then latency, jitter and loss. The speed is what an Iranian CUSTOMER gets - the download (foreign -> Iran), eight connections together and one connection by itself - and the upload only up to the download
    (real tests, 2026-10-16: the "upload" of a reverse tunnel ran at 700 - 2700 Mb/s over a path that carries 100 and made BackPack look the best while a customer downloaded at 10 Mb/s per connection)."""
    if not q or q.get("error") or q.get("latency_ms") is None:
        return 0

    d8, d1, up = q.get("down_mbps") or 0, q.get("down1_mbps"), q.get("up_mbps") or 0
    down = d8 if d1 is None else (d8 + d1) / 2
    speed = min(100.0, (down * 0.85 + min(up, down) * 0.15) / 6.0)
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
    mux_s = (f"mux_con = {int(o.get('mux_con', 8))}\nmux_version = 2\nmux_framesize = 32768\nmux_recievebuffer = 16777216\nmux_streambuffer = 2097152\n") if mux else ""
    mux_c = ("mux_version = 2\nmux_framesize = 32768\nmux_recievebuffer = 16777216\nmux_streambuffer = 2097152\n") if mux else ""
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


def direct_texts(foreign_host: str, port: int, token: str, carrier: str, net: int, ports: list[PortMap], test_port: int, opts: dict | None = None, iran_host: str = "") -> tuple[str, str]:
    """(Iran toml - dials, foreign toml - listens) of a BackPack Direct (L3) tunnel on 10.10.<net>.0/30. The IP spoofing carrier also needs the real address of the Iran server on the listener (`spoof_peer_ip`: its
    packets do not tell where they came from), the same packet profile on both ends and - when a source is forged - the forged address on the Iran side and the address the listener should expect (`spoof_peer_src_ip`)."""
    o = opts or {}
    port_lines = "\n".join(f'    "{p}",' for p in _entries(ports, test_port))
    extra = ""
    iran_extra = foreign_extra = ""

    if carrier in ("pck", "sni"):
        extra += f"mtu = {int(o.get('mtu') or 1380)}\n"

    if carrier == "sni" and o.get("sni_domain"):
        extra += f'sni_domain = "{o["sni_domain"]}"\n'

    if carrier == "spoof":
        profile, forged, nic = str(o.get("spoof_profile") or "udp"), str(o.get("spoof_src_ip") or ""), str(o.get("spoof_interface") or "")
        extra += f'spoof_profile = "{profile}"\n'
        iran_extra = (f'spoof_src_ip = "{forged}"\n' if forged else "") + (f'spoof_interface = "{nic}"\n' if nic else "")
        foreign_extra = f'spoof_peer_ip = "{iran_host}"\n' + (f'spoof_peer_src_ip = "{forged}"\n' if forged else "")

    common = f'carrier = "{carrier}"\ntoken = "{token}"\niface = "bp{net}"\n{extra}'
    iran = f"""# Nikatel panel - BackPack direct tunnel (Iran dials)
[l3]
mode = "dial"
addr = "{foreign_host}:{port}"
{common}{iran_extra}local_ip = "10.10.{net}.1/30"
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
{common}{foreign_extra}local_ip = "10.10.{net}.2/30"
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


PAQET_MAX_MTU = 1400                                                   # real tests, 2026-10-16: 1200 and 1350 carry data, 1450 carries NOTHING (the KCP packet, its cipher and the IP / TCP headers no longer fit one 1500 byte frame)
PAQET_BLOCKS = ("aes-128-gcm", "aes", "none")                      # the cipher of the KCP stream (none = no encryption at all: for a measurement of what the cipher costs)
PAQET_TUNING = (("rcvwnd", "rcvwnd", 128, 65535), ("sndwnd", "sndwnd", 128, 65535), ("smuxbuf", "smuxbuf", 65536, 268435456), ("streambuf", "streambuf", 65536, 268435456))      # (option of the panel, key of Paqet, lowest, highest): the KCP windows (in packets) and the SMUX / stream buffers (in bytes)
# What the panel writes when nothing is asked. Real tests, 2026-10-16 (an Iran server -> a Hetzner server, 114 ms, door with random data, one connection / four connections together, Mb/s down):
#   Paqet's own defaults (window 1024, 4 MB / 2 MB)  52 / 184;   window 2048: 92 / 283 - 153 / 368;   window 4096 + 16 MB / 8 MB: 199 - 210 / 330 - 301;   window 8192 + 32 MB / 16 MB: 185 / 309 (no better).
# The window raised the speed of one connection to 92, the stream buffer (2 MB / 114 ms = 140 Mb/s at most) to 200. With them: mode fast 199 / 330, normal 153 / 193, fast3 127 / 155; 1 connection 165 / 172, 2: 193 / 308, 8: 191 / 327;
# MTU 1400 176 / 351 (1450 carries nothing). The cipher ("none") and the mode, with the old window, changed nothing - the cipher costs no speed.
PAQET_DEFAULTS = {"rcvwnd": 4096, "sndwnd": 4096, "smuxbuf": 16 * 1048576, "streambuf": 8 * 1048576}


def paqet_texts(net_i: dict, net_f: dict, port: int, key: str, foreign_host: str, ports: list[PortMap], test_port: int, opts: dict | None = None) -> tuple[str, str]:
    """(Iran client yaml, foreign server yaml)."""
    o = opts or {}
    mode = str(o.get("kcp_mode") or "fast")
    mode = mode if mode in ("normal", "fast", "fast2", "fast3") else "fast"
    block = str(o.get("kcp_block") or "aes-128-gcm")
    block = block if block in PAQET_BLOCKS else "aes-128-gcm"
    more = "".join(f"    {k}: {max(lo, min(hi, int(o[opt] if o.get(opt) not in (None, '', 0, False) else PAQET_DEFAULTS[opt])))}\n" for opt, k, lo, hi in PAQET_TUNING)
    kcp = f"""transport:
  protocol: "kcp"
  conn: {max(1, min(64, int(o.get("conn", 4))))}
  kcp:
    mode: "{mode}"
    block: "{block}"
    mtu: {max(500, min(PAQET_MAX_MTU, int(o.get("mtu", 1350))))}
{more}    key: "{key}"
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


def chisel_mode(transport: str) -> tuple[bool, bool]:
    """(tls, forward) of a Chisel transport. Reverse (ws / wss): the foreign server dials the Iran server and the Iran server opens the user ports.
    Forward (fws / fwss): the Iran server dials the foreign server and opens the user ports itself (real tests, 2026-10-16: the route slows the data that flows
    away from the side that opened the connection, so a customer's download runs at full speed only when the Iran side is the one that dials)."""
    return transport in ("wss", "fwss"), transport in ("fws", "fwss")


def chisel_remotes(ports: list[PortMap], test_port: int, udp: bool, forward: bool = False) -> list[str]:
    """The remotes of the client. Reverse: `R:` - the Iran server (the server there) opens every user port and sends it to the foreign side. Forward: no R: - the client IS the Iran server and opens the ports itself."""
    return [("" if forward else "R:") + f"{ls}:{t}" + ("/udp" if p == "udp" else "") for ls, t, p in forward_rows(ports, test_port, udp)]


def chisel_unit(name: str, side: str, port: int, remotes: list[str], server_host: str, tls: bool, fingerprint: str = "", forward: bool = False) -> str:
    """The unit of one side. `server_host` is the address the client dials: the Iran server for a reverse tunnel, the foreign one for a forward tunnel."""
    base = "/etc/nikatel-chisel"
    bin_ = "/usr/local/bin/nikatel-chisel"

    if (side == "iran") != forward:                                 # the server side: Iran when reverse, foreign when forward
        tls_args = f" --tls-key {base}/{name}.tls.key --tls-cert {base}/{name}.crt" if tls else ""
        exec_line = f"{bin_} server --host 0.0.0.0 --port {port}{'' if forward else ' --reverse'} --keyfile {base}/{name}.key --keepalive 25s{tls_args}"
        env = f"{base}/{name}.srv.env"
    else:
        fp = f" --fingerprint {fingerprint}" if fingerprint else ""
        skip = " --tls-skip-verify" if tls else ""
        rem = " ".join(f"'{r}'" for r in remotes)
        exec_line = f"{bin_} client --keepalive 25s --max-retry-interval 5s{fp}{skip} {'https' if tls else 'http'}://{server_host}:{port} {rem}"
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

    from . import license

    license.require("tunnels")                               # BackPack, Paqet and Chisel are not in the free tier

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

        if transport == "spoof":
            profile, forged, nic = str(opts.get("spoof_profile") or "udp").strip().lower(), str(opts.get("spoof_src_ip") or "").strip(), str(opts.get("spoof_interface") or "").strip()

            if profile not in SPOOF_PROFILES:
                raise ValueError("the packet profile of IP spoofing is one of: " + ", ".join(SPOOF_PROFILES))

            if forged and not re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", forged):
                raise ValueError("the forged source must be an IPv4 address, for example 10.10.10.10 (empty = do not forge: the tunnel uses the real address - the right first build)")

            if nic and not re.fullmatch(r"[A-Za-z0-9._-]{1,15}", nic):
                raise ValueError("the network interface name is not valid")

            opts.update(spoof_profile=profile, spoof_src_ip=forged, spoof_interface=nic)
    elif kind == "paqet":
        transport = "kcp"
    elif transport in CHISEL_TRANSPORTS:
        pass
    else:
        transport = "wss" if transport in ("tls", "1", "true") else "ws"

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
        with server_locks([b.iran.id, b.foreign.id], b.aid):
            _build(b)
    except Exception as e:  # thread boundary
        log.exception("build #%s crashed", b.aid)
        _rollback(b)
        _say(b.aid, f"Stopped: {str(e)[:220]}. Everything created was removed again.", done=True, ok=False)
    finally:
        from .builder import pending_drop

        pending_drop(b.name)

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


def _owns(unit: str, eng: str, name: str) -> bool:
    """Is this unit (with or without .service) one of the two units of the tunnel `name` built by engine `eng`?"""
    return unit in (f"{eng}-{name}", f"{eng}-{name}-kharej") or unit.startswith((f"{eng}-{name}.", f"{eng}-{name}-kharej."))


def _clear_leftovers(b: Build, iran: Node, foreign: Node, fi: dict, ff: dict) -> tuple[dict, dict]:
    """A forced name (a temporary test, a rebuild under the same name) must stay. Units of that name still on a server from an
    interrupted earlier run are orphans - no tunnel in the list carries the name - and are removed before the ports are checked,
    so that neither the name nor the ports they hold get in the way."""
    if not b.name:
        return fi, ff

    with session() as db:
        if db.scalar(select(Tunnel).where(Tunnel.name == b.name)) is not None:
            return fi, ff

    eng = _engine(b.kind)
    sides = [(label, node) for label, node, f in (("Iran", iran, fi), ("foreign", foreign, ff)) if any(_owns(u, eng, b.name) for u in f["units"])]

    if not sides:
        return fi, ff

    for _, node in sides:
        run_action(node, "other_remove", {"NAME": b.name, "KIND": eng, "PORT": "0", "PROTO": "tcp"}, timeout=90)

    _say(b.aid, "Leftovers of an interrupted earlier run removed first on the " + " and ".join(label for label, _ in sides) + " side")
    labels = {label for label, _ in sides}
    fi = parse_simple_facts(run_script(iran, "simple_facts", timeout=45)) if "Iran" in labels else fi
    ff = parse_simple_facts(run_script(foreign, "simple_facts", timeout=45)) if "foreign" in labels else ff
    return fi, ff


def _preflight(b: Build, iran: Node, foreign: Node) -> tuple[dict, dict]:
    fi, ff = parse_simple_facts(run_script(iran, "simple_facts", timeout=45)), parse_simple_facts(run_script(foreign, "simple_facts", timeout=45))
    fi, ff = _clear_leftovers(b, iran, foreign, fi, ff)

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

    eng = _engine(b.kind)

    if not b.name:          # a forced name (a test, a rebuild under the same name) is kept: its leftovers were removed in the preflight
        b.name = pretty_name(b.foreign.name, b.iran.name, taken_names)

        while any(_owns(u, eng, b.name) for u in fi["units"] + ff["units"]):
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
        b.port = free_port(9600, 9700, taken_f if chisel_mode(b.transport)[1] else taken_i)       # control port: on the Iran server, on the foreign one when the Iran server dials (forward)
        b.test_port = free_port(39000, 39100, taken_i | {b.port})

    if not b.port or not b.test_port:
        raise RuntimeError("no free control / test port")

    where = "the foreign server" if b.kind in ("backpack_direct", "paqet") or (b.kind == "chisel" and chisel_mode(b.transport)[1]) else "the Iran server"
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
    out = run_script(iran, "probe_run", {"PORT": str(test_port), "SECS": str(secs), "STREAMS": str(streams), "PINGS": str(pings), "KEY": key, "TO": str(to), "UDPN": "0"}, timeout=int(60 + secs * 4))
    return parse_probe(out)


def paqet_diagnosis(pkts_in: int, pkts_out: int, port: int) -> str:
    """Pure: why a Paqet tunnel carries nothing, from what the firewall of the foreign server counted on the raw port (real tests, 2026-10-16: the same Iran server passes it to one provider and not to another)."""
    if pkts_in <= 0:
        return (f"Not one packet of the tunnel reached the foreign server on port {port}: the route from this Iran server, or the provider of the foreign server, drops the raw packets Paqet sends "
                "(it works only where they pass; BackPack and Chisel do not need that). Another foreign server may work.")

    if pkts_out <= 0:
        return f"The foreign server receives the packets on port {port} but sends nothing back: the key must be the same on both sides - see the log of the Paqet unit."

    return f"Packets go both ways on port {port} but no data comes through: the answers are probably dropped on their way to the Iran server (its provider, or the route) - another foreign server may work."


def _paqet_why(b: Build, foreign: Node) -> str:
    try:
        out = run_script(foreign, "paqet_count", {"PORT": str(b.port)}, timeout=25)
    except (SSHError, ValueError):
        return ""

    m = re.search(r"PAQET_IN=(\d+) PAQET_OUT=(\d+)", out)
    return paqet_diagnosis(int(m.group(1)), int(m.group(2)), b.port) if m else ""


def _wait_for_data(b: Build, iran: Node, key: str, hint: str, log_node: Node, log_unit: str, why=None) -> dict:
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

    extra = ""

    if why is not None:
        try:
            extra = why()
        except (SSHError, ValueError, RuntimeError):
            extra = ""

    raise RuntimeError(f"the tunnel started but no data passes through it ({last}). {extra or hint}" + (f" Log: {logs}" if logs else ""))


def _measure(b: Build, iran: Node, key: str) -> None:
    try:
        if b.measure == "latency":
            _say(b.aid, "Measuring latency, jitter and loss through the tunnel")
            b.quality = _probe(iran, b.test_port, key, secs=0, pings=20, to=2.5)
        else:
            _say(b.aid, "Measuring speed through the tunnel (8 connections each way)")
            b.quality = _probe(iran, b.test_port, key, secs=4, streams=8, pings=20, to=2.5)

            try:      # ONE connection too (the owner, 2026-10-16: the closest to what a customer gets: a phone downloads over one connection)
                one = _probe(iran, b.test_port, key, secs=4, streams=1, pings=1, to=2.5)
                b.quality["down1_mbps"], b.quality["up1_mbps"] = one.get("down_mbps"), one.get("up_mbps")
            except (SSHError, RuntimeError, ValueError) as e:
                _say(b.aid, f"One connection not measured: {str(e)[:80]}")

        b.quality["score"] = quality_score(b.quality)
        b.quality["at"] = int(time.time())
        _say(b.aid, f"Quality: {b.quality.get('latency_ms')} ms, jitter {b.quality.get('jitter_ms')} ms, loss {b.quality.get('loss_pct')}%"
             + (f", ↓{b.quality.get('down_mbps')} ↑{b.quality.get('up_mbps')} Mb/s" if b.quality.get("down_mbps") is not None else "") + f" → score {b.quality['score']}")

        if b.quality.get("down_streams"):
            _say(b.aid, f"Per stream: ↓{b.quality['down_streams']} ↑{b.quality.get('up_streams')} Mb/s")

        if b.quality.get("down1_mbps") is not None:
            _say(b.aid, f"One connection: ↓{b.quality['down1_mbps']} ↑{b.quality.get('up1_mbps')} Mb/s")
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
        iran_toml, foreign_toml = direct_texts(b.foreign.host, b.port, token, b.transport, b.net, b.ports, b.test_port, b.opts, iran_host=b.iran.host)
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
        _wait_for_data(b, iran, key, f"Raw sockets can be blocked by the provider, or TCP port {b.port} is closed between the servers.", iran, f"paqet-{b.name}.service", why=lambda: _paqet_why(b, foreign))
        direction, tlabel = "direct", "Paqet · KCP"

    else:
        tls, fwd = chisel_mode(b.transport)
        wire = "TLS WebSocket" if tls else "HTTP WebSocket"
        _say(aid, f"Chisel · forward tunnel: the Iran server dials the foreign server, over {wire}" if fwd else f"Chisel · reverse tunnel over {wire}")
        remotes = chisel_remotes(b.ports, b.test_port, bool(b.opts.get("accept_udp")), fwd)
        _install_sink(b, foreign, key)
        # the server side holds the control port: the Iran server (reverse) or the foreign one (forward); the client side dials it
        srv, cli, srv_label, cli_label, srv_facts = (foreign, iran, "foreign", "Iran", ff) if fwd else (iran, foreign, "Iran", "foreign", fi)
        srv_unit, cli_unit = (f"chisel-{b.name}-kharej.service", f"chisel-{b.name}.service") if fwd else (f"chisel-{b.name}.service", f"chisel-{b.name}-kharej.service")
        server_host = b.foreign.host if fwd else b.iran.host
        b.created.append(srv)
        run_action(srv, "chisel_prepare", {"NAME": b.name, "TLS": "1" if tls else "0"}, timeout=60)
        _put(srv, f"/etc/nikatel-chisel/{b.name}.srv.env", f"AUTH=nikatel:{token}\n", "0600")
        _put(srv, f"/etc/systemd/system/{srv_unit}", chisel_unit(b.name, srv_label.lower(), b.port, remotes, server_host, tls, forward=fwd), "0644")

        if srv_facts["ufw"]:
            run_action(srv, "ufw_allow", {"PORT": str(b.port), "PROTO": "tcp"})

        _start(srv, srv_label, srv_unit, b)
        fp = (re.search(r"FP=(\S+)", run_script(srv, "chisel_fp", {"NAME": b.name}, timeout=20)) or [None, ""])[1]
        _say(aid, f"{srv_label} side running" + (", server key pinned on the client" if fp else ""))
        _put(cli, f"/etc/nikatel-chisel/{b.name}.cli.env", f"AUTH=nikatel:{token}\n", "0600")
        _put(cli, f"/etc/systemd/system/{cli_unit}", chisel_unit(b.name, cli_label.lower(), b.port, remotes, server_host, tls, fp, forward=fwd), "0644")
        _start(cli, cli_label, cli_unit, b)
        _wait_for_data(b, iran, key, f"The control port {b.port} may be closed between the servers (it is on the {srv_label.lower()} server).", cli, cli_unit)
        direction, tlabel = ("forward" if fwd else "reverse"), "Chisel · " + ("forward " if fwd else "") + ("TLS WebSocket" if tls else "WebSocket")

    _measure(b, iran, key)

    with session() as db:
        t = Tunnel(name=b.name, enabled=not is_temp(b.name), type=eng, transport=b.transport if b.kind != "backpack_direct" else f"l3:{b.transport}", engine=eng,
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
        if not is_temp(b.name):
            emit(db, "ok", "tunnels", f"Tunnel {b.name} ({b.iran.name} ⇄ {b.foreign.name}) built: {tlabel}, ports {', '.join(p.spec() for p in b.ports)}"
                 + (f", {b.quality.get('latency_ms')} ms" if b.quality.get("latency_ms") is not None else "") + ".", b.name)

    check_tunnel(tid)
    _say(aid, f"Tunnel {b.name} is ready.", done=True, ok=True)


# ------------------------------------------------------------------ remove / quality of an existing tunnel
def remove(t: Tunnel, skip: set[str] | None = None) -> list[str]:
    """Remove a BackPack / Paqet / Chisel tunnel from both servers. Returns per-side results. `skip`: sides whose server is off (not asked)."""
    rec = t.record or {}
    params = {"NAME": sname(t), "KIND": t.type, "PORT": str(int(t.port or 0)), "PROTO": "udp" if rec.get("udp_carrier") else "tcp"}
    results = []

    for side, node in (("Iran", _node(t.iran)), ("foreign", _node(t.foreign))):
        if side in (skip or set()):
            results.append(f"{side}: skipped (the server is off; its files are purged when it is back)")
            continue

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

        iran, foreign, name, port, key = _node(t.iran), _node(t.foreign), sname(t), int(rec["test_port"]), str(sec["test_key"])
        bind = "0.0.0.0" if str(t.transport or "").startswith("l3:") else ""

    try:   # keep the sink at the current version
        run_action(foreign, "sink_install", {"NAME": name, "PORT": str(port), "KEY": key, "BIND": bind}, timeout=60)
    except (SSHError, ValueError):
        pass

    with livebw.quiet(tunnel_id, 60):                     # (the test moves data over the link: that is not the users' - the live numbers wait for it)
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

    from . import bandwidth, knowledge

    bandwidth.note_quality(tunnel_id, q)          # (the download of this measurement is a sample of the tunnel's bandwidth: the Doctor watches it for a drop)

    try:
        knowledge.note_quality(tunnel_id, q)      # (and a full measurement of this kind on this pair, for what the Doctor knows)
    except Exception:
        log.exception("the quality of tunnel %s could not be kept in the Doctor's knowledge", tunnel_id)

    return {"name": name, **q}
