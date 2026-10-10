"""Build, delete and rebuild tunnels (backhaul over GRE). Read-write mode only.

Everything is derived from what the servers actually run today: GRE through small up/down/check
scripts + a oneshot unit + a health timer; backhaul v0.7 server on the Iran inner IP, client on
the foreign side dialling it over GRE. All files go through the `put_file` action, all service
changes through `unit_enable_restart`; a failed build removes everything it created.
"""
from __future__ import annotations

import base64
import ipaddress
import random
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Server, Tunnel
from ..ssh import Node, SSHError, run_action, run_script
from . import settings as st
from .events import emit
from .tunnels import check_tunnel

log = get_logger("builder")
GRE_HOLD_WAIT = 10
_LOCK = threading.Lock()
_BUILDING: set[str] = set()


# ------------------------------------------------------------------ pure helpers (tested)
@dataclass
class PortMap:
    listen: int
    target: int | None = None       # None = same port on the foreign side

    def entry(self) -> str:         # backhaul "ports" line
        return f"{self.listen}=127.0.0.1:{self.target}" if self.target else str(self.listen)

    def spec(self) -> str:
        return f"{self.listen}:{self.target}" if self.target else str(self.listen)


def parse_ports(text: str) -> list[PortMap]:
    """'8072, 443=2096, 10000-10003' -> port maps. Raises ValueError with a sentence."""
    out: list[PortMap] = []

    for raw in str(text or "").split(","):
        raw = raw.strip()

        if not raw:
            continue

        m = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?(?:[=:](\d{1,5}))?", raw)

        if not m:
            raise ValueError(f"port '{raw}': use 443, 443=2096 or 10000-10009")

        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        tgt = int(m.group(3)) if m.group(3) else None

        if not (1 <= a <= 65535 and a <= b <= 65535) or b - a > 200 or (tgt is not None and not 1 <= tgt <= 65535):
            raise ValueError(f"port '{raw}' is out of range")

        for p in range(a, b + 1):
            if all(x.listen != p for x in out):
                out.append(PortMap(p, tgt if tgt and tgt != p else None))

    if not out:
        raise ValueError("enter at least one port to forward, for example 8072")

    return out


def pretty_name(foreign_name: str, iran_name: str, taken: set[str]) -> str:
    """<foreign>-<iran> style (e.g. fi-ir1), max 15 chars (Linux interface name), unique."""
    def slug(v: str, n: int) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())[:n]

    f, i = slug(foreign_name, 15), slug(iran_name, 15)

    if len(f) + len(i) + 1 > 15:
        f, i = f[:5], i[:9]

    base = (f"{f}-{i}".strip("-") or "tunnel")[:15]
    name, n = base, 2

    while name in taken:
        suf = f"-{n}"
        name = base[:15 - len(suf)] + suf
        n += 1

    return name


def allocate_network(taken_cidrs: list[str], rnd=random) -> tuple[str, str, str]:
    """A free 10.x.y.z/30 that overlaps nothing. Returns (network, iran_ip, foreign_ip)."""
    taken = []

    for c in taken_cidrs:
        try:
            taken.append(ipaddress.ip_network(c, strict=False))
        except ValueError:
            pass

    for _ in range(20000):
        net = ipaddress.ip_network(f"10.{rnd.randint(1, 254)}.{rnd.randint(0, 255)}.{rnd.randrange(0, 256, 4)}/30")

        if not any(net.overlaps(t) for t in taken):
            h = list(net.hosts())
            return str(net), str(h[0]), str(h[1])

    raise RuntimeError("no free tunnel network found")


def ping_stats(text: str) -> tuple[int, int, float | None]:
    m = re.search(r"(\d+) packets transmitted, (\d+) received", str(text))
    r = re.search(r"= [\d.]+/([\d.]+)/", str(text))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0), (float(r.group(1)) if r else None)  # type: ignore[return-value]


def gre_scripts(name: str, local: str, remote: str, key: int, addr_cidr: str, mtu: int, ttl: int = 255) -> dict[str, str]:
    """up / down / check scripts + unit files, byte-compatible with what runs on the servers today."""
    peer = next(str(h) for h in ipaddress.ip_interface(addr_cidr).network.hosts() if str(h) != addr_cidr.split("/")[0])
    up = f"""#!/bin/sh
# Nikatel GRE {name}  (idempotent: also used by the health-check timer)
NAME={name}
LOCAL={local}
REMOTE={remote}
KEY={key}
ADDR={addr_cidr}
MTU={mtu}
TTL={ttl}
TAG="nikatel-gre-$NAME"

modprobe ip_gre 2>/dev/null || true

if command -v iptables >/dev/null 2>&1; then
  iptables -C INPUT  -s "$REMOTE" -d "$LOCAL" -p gre -m comment --comment "$TAG" -j ACCEPT 2>/dev/null || \\
    iptables -I INPUT 1  -s "$REMOTE" -d "$LOCAL" -p gre -m comment --comment "$TAG" -j ACCEPT
  iptables -C OUTPUT -s "$LOCAL" -d "$REMOTE" -p gre -m comment --comment "$TAG" -j ACCEPT 2>/dev/null || \\
    iptables -I OUTPUT 1 -s "$LOCAL" -d "$REMOTE" -p gre -m comment --comment "$TAG" -j ACCEPT
  iptables -C INPUT -i "$NAME" -m comment --comment "$TAG" -j ACCEPT 2>/dev/null || \\
    iptables -I INPUT 1 -i "$NAME" -m comment --comment "$TAG" -j ACCEPT
  iptables -C OUTPUT -o "$NAME" -m comment --comment "$TAG" -j ACCEPT 2>/dev/null || \\
    iptables -I OUTPUT 1 -o "$NAME" -m comment --comment "$TAG" -j ACCEPT
  iptables -C FORWARD -i "$NAME" -m comment --comment "$TAG" -j ACCEPT 2>/dev/null || \\
    iptables -I FORWARD 1 -i "$NAME" -m comment --comment "$TAG" -j ACCEPT
  iptables -C FORWARD -o "$NAME" -m comment --comment "$TAG" -j ACCEPT 2>/dev/null || \\
    iptables -I FORWARD 1 -o "$NAME" -m comment --comment "$TAG" -j ACCEPT
  for dir in -o -i; do
    iptables -t mangle -C FORWARD $dir "$NAME" -p tcp --tcp-flags SYN,RST SYN -m comment --comment "$TAG" -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \\
      iptables -t mangle -A FORWARD $dir "$NAME" -p tcp --tcp-flags SYN,RST SYN -m comment --comment "$TAG" -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
  done
fi

if ! ip link show "$NAME" >/dev/null 2>&1; then
  ip tunnel add "$NAME" mode gre local "$LOCAL" remote "$REMOTE" key "$KEY" ttl "$TTL"
fi
ip link set "$NAME" mtu "$MTU"
ip addr show dev "$NAME" | grep -q "inet ${{ADDR%/*}}/" || ip addr add "$ADDR" dev "$NAME"
ip link set "$NAME" up

sysctl -qw net.ipv4.ip_forward=1 2>/dev/null || true
sysctl -qw "net.ipv4.conf.$NAME.rp_filter=0" 2>/dev/null || true
sysctl -qw net.ipv4.conf.all.rp_filter=0 2>/dev/null || true
exit 0
"""
    down = f"""#!/bin/sh
NAME={name}
TAG="nikatel-gre-$NAME"
ip link set "$NAME" down 2>/dev/null || true
ip tunnel del "$NAME" 2>/dev/null || true
if command -v iptables >/dev/null 2>&1; then
  for chain in INPUT OUTPUT FORWARD; do
    iptables -S "$chain" 2>/dev/null | grep -- "--comment $TAG" | sed "s/^-A/-D/" | while read -r rule; do
      iptables $rule 2>/dev/null || true
    done
  done
  iptables -t mangle -S FORWARD 2>/dev/null | grep -- "--comment $TAG" | sed "s/^-A/-D/" | while read -r rule; do
    iptables -t mangle $rule 2>/dev/null || true
  done
fi
exit 0
"""
    check = f"""#!/bin/sh
# nikatel-gre-check-script
NAME={name}
PEER={peer}
/usr/local/sbin/nikatel-gre-up-$NAME
F=/run/nikatel-gre-fail-$NAME
if ping -c 2 -W 1 -q "$PEER" >/dev/null 2>&1; then
  rm -f "$F"
  exit 0
fi
n=$(cat "$F" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$F"
if [ "$n" -ge 3 ]; then
  rm -f "$F"
  logger -t nikatel-gre "$NAME: the peer did not answer 3 times in a row, restarting the GRE link"
  systemctl restart nikatel-gre-$NAME.service
fi
exit 0
"""
    unit = f"""[Unit]
Description=Nikatel GRE tunnel {name}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/nikatel-gre-up-{name}
ExecStop=/usr/local/sbin/nikatel-gre-down-{name}

[Install]
WantedBy=multi-user.target
"""
    check_unit = f"""[Unit]
Description=Nikatel GRE health-check {name}

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/nikatel-gre-check-{name}
"""
    timer = f"""[Unit]
Description=Nikatel GRE health-check timer {name}

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=5s

[Install]
WantedBy=timers.target
"""
    return {f"/usr/local/sbin/nikatel-gre-up-{name}": up, f"/usr/local/sbin/nikatel-gre-down-{name}": down,
            f"/usr/local/sbin/nikatel-gre-check-{name}": check, f"/etc/systemd/system/nikatel-gre-{name}.service": unit,
            f"/etc/systemd/system/nikatel-gre-check-{name}.service": check_unit, f"/etc/systemd/system/nikatel-gre-check-{name}.timer": timer}


BH_LOG = ("debug", "info", "warn", "error")


def clean_bh_opts(o: dict | None) -> dict:
    """The tunnel options the operator may change (Advanced settings), range-checked. Missing = the many-users defaults."""
    o = dict(o or {})
    out: dict = {}

    def num(key: str, lo: int, hi: int) -> None:
        if o.get(key) not in (None, ""):
            try:
                v = int(o[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number")

            if not lo <= v <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")

            out[key] = v

    num("heartbeat", 5, 120)
    num("channel_size", 1024, 65536)
    num("mux_con", 1, 64)
    num("mux_version", 1, 2)
    num("mux_framesize", 1024, 131072)
    num("mux_recievebuffer", 65536, 67108864)
    num("mux_streambuffer", 16384, 33554432)
    num("connection_pool", 1, 256)
    num("dial_timeout", 3, 60)
    num("retry_interval", 1, 30)

    if o.get("log_level") not in (None, ""):
        if o["log_level"] not in BH_LOG:
            raise ValueError("log_level must be debug, info, warn or error")

        out["log_level"] = o["log_level"]

    if "aggressive_pool" in o:
        out["aggressive_pool"] = bool(o["aggressive_pool"])

    if "tune" in o:
        out["tune"] = bool(o["tune"])

    return out


def backhaul_texts(iran_ip: str, port: int, token: str, transport: str, ports: list[PortMap], keepalive: int = 75, nodelay: bool = True, opts: dict | None = None) -> tuple[str, str]:
    """(iran server toml, foreign client toml) - tuned for many users. `opts` are the Advanced settings (see clean_bh_opts)."""
    o = opts or {}
    mux = transport != "tcp"
    nd = "true" if nodelay else "false"
    mv, fs, rb, sb = o.get("mux_version", 2), o.get("mux_framesize", 32768), o.get("mux_recievebuffer", 8388608), o.get("mux_streambuffer", 262144)
    mux_server = (f"mux_con = {o.get('mux_con', 8)}\nmux_version = {mv}\nmux_framesize = {fs}\nmux_recievebuffer = {rb}\nmux_streambuffer = {sb}\n") if mux else ""
    mux_client = (f"mux_version = {mv}\nmux_framesize = {fs}\nmux_recievebuffer = {rb}\nmux_streambuffer = {sb}\n") if mux else ""
    port_lines = "\n".join(f'    "{p.entry()}",' for p in ports)
    iran = f"""[server]
bind_addr = "{iran_ip}:{port}"
transport = "{transport}"
token = "{token}"
keepalive_period = {keepalive}
nodelay = {nd}
heartbeat = {o.get('heartbeat', 20)}
channel_size = {o.get('channel_size', 8192)}
log_level = "{o.get('log_level', 'warn')}"
web_port = 0
sniffer = false
{mux_server}
ports = [
{port_lines}
]
"""
    foreign = f"""[client]
remote_addr = "{iran_ip}:{port}"
transport = "{transport}"
token = "{token}"
connection_pool = {o.get('connection_pool', 16 if mux else 32)}
aggressive_pool = {"true" if o.get('aggressive_pool') else "false"}
keepalive_period = {keepalive}
dial_timeout = {o.get('dial_timeout', 10)}
retry_interval = {o.get('retry_interval', 2)}
nodelay = {nd}
log_level = "{o.get('log_level', 'warn')}"
web_port = 0
sniffer = false
{mux_client}
"""
    return iran, foreign


def backhaul_unit(config_path: str) -> str:
    return f"""[Unit]
Description=Nikatel Backhaul Tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/backhaul -c {config_path}
Restart=always
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
"""


def parse_facts(out: str) -> dict:
    kv = {}

    for line in str(out).splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            kv[k.strip()] = v.strip()

    return {"ips": kv.get("IPS", "").split(), "links": kv.get("LINKS", "").split(), "listen": {int(x) for x in kv.get("LISTEN", "").split() if x.isdigit()},
            "units": set(kv.get("UNITS", "").split()), "broken": kv.get("BROKEN", "").split(), "gre_mod": kv.get("GRE_MOD") == "1",
            "backhaul": kv.get("BACKHAUL", "none") != "none", "backhaul_version": kv.get("BACKHAUL", ""), "iptables": kv.get("IPTABLES") == "1"}


# ------------------------------------------------------------------ build job
@dataclass
class Build:
    aid: int
    iran: Server
    foreign: Server
    ports: list[PortMap]
    transport: str
    mtu: int = 0           # 0 = measure
    keepalive: int = 75
    nodelay: bool = True
    opts: dict = field(default_factory=dict)
    name: str = ""
    created: list[tuple[str, Node]] = field(default_factory=list)   # (what, where) for rollback


def _say(aid: int, line: str, done: bool | None = None, ok: bool | None = None) -> None:
    with session() as db:
        e = db.get(ActionLog, aid)

        if e is None:
            return

        story = list(e.story or [])
        story.append(line)
        e.story = story

        if done:
            e.ok = ok
            e.finished_at = int(time.time())

    log.info("build #%s: %s", aid, line)


def _put(node: Node, path: str, text: str, mode: str) -> None:
    run_action(node, "put_file", {"PATH": path, "B64": base64.b64encode(text.encode()).decode(), "MODE": mode})


def _unit(node: Node, unit: str) -> str:
    out = run_action(node, "unit_enable_restart", {"UNIT": unit})
    m = re.search(rf"UNIT {re.escape(unit)} (\S+)", out)
    return m.group(1) if m else "unknown"


def _node(s: Server) -> Node:
    return Node(s.host, s.ssh_user, s.ssh_port)


def ensure_backhaul(iran: Node, foreign: Node, fi: dict, ff: dict, aid: int) -> None:
    """Both servers need /usr/local/bin/backhaul. Copy it from the one that has it, through the panel."""
    if fi["backhaul"] and ff["backhaul"]:
        return

    if not fi["backhaul"] and not ff["backhaul"]:
        raise RuntimeError("the backhaul binary is missing on both servers; install it on one of them first")

    src, dst = (iran, foreign) if fi["backhaul"] else (foreign, iran)
    s = get_settings()
    cache = s.data_dir.parent / "engines"
    cache.mkdir(parents=True, exist_ok=True)
    local = cache / "backhaul"
    opts = ["-i", str(s.ssh_key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={s.known_hosts}", "-o", "ConnectTimeout=15"]
    _say(aid, f"copying the backhaul binary from {src.host} to {dst.host} through the panel")

    if not local.exists() or local.stat().st_size < 1024:
        r = subprocess.run(["scp", *opts, "-P", str(src.port), f"{src.user}@{src.host}:/usr/local/bin/backhaul", str(local)], capture_output=True, text=True, timeout=240, check=False)

        if r.returncode != 0 or local.stat().st_size < 1024:
            raise RuntimeError("could not fetch the backhaul binary: " + r.stderr.strip()[:120])

    r = subprocess.run(["scp", *opts, "-P", str(dst.port), str(local), f"{dst.user}@{dst.host}:/usr/local/bin/.backhaul.nikatel-tmp"], capture_output=True, text=True, timeout=240, check=False)

    if r.returncode != 0:
        raise RuntimeError("could not copy the backhaul binary to the server: " + r.stderr.strip()[:120])

    run_action(dst, "restart_units", {"UNITS": "nikatel-noop.service"})   # no-op touch to keep the action path; harmless if absent
    _put(dst, "/usr/local/bin/backhaul-install.sh", "#!/bin/sh\nset -e\nchmod 755 /usr/local/bin/.backhaul.nikatel-tmp\n/usr/local/bin/.backhaul.nikatel-tmp -v >/dev/null 2>&1\nmv -f /usr/local/bin/.backhaul.nikatel-tmp /usr/local/bin/backhaul\necho installed\n", "0755")
    out = run_action(dst, "restart_units", {"UNITS": "nikatel-noop.service"})
    del out
    # run the installer through a oneshot unit so no raw command is needed
    _put(dst, "/etc/systemd/system/nikatel-backhaul-install.service", "[Unit]\nDescription=Nikatel backhaul install\n\n[Service]\nType=oneshot\nExecStart=/usr/local/bin/backhaul-install.sh\n", "0644")
    state = _unit(dst, "nikatel-backhaul-install.service")

    if state not in ("active", "inactive"):
        raise RuntimeError(f"backhaul install unit ended {state}")


def set_ports(tunnel_id: int, ports_text: str) -> dict:
    """Change the forwarded ports of a backhaul tunnel: the Iran toml is rewritten, the service restarted,
    every port must listen, otherwise the server puts the old file back. The record follows."""
    import base64

    ports = parse_ports(ports_text)

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        if (t.type or "").lower() != "backhaul":
            raise ValueError("only backhaul tunnels can change their ports here")

        node, name, cfg = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), t.name, f"iran-{t.transport or 'tcpmux'}-{int(t.port)}"

    body = "\n".join(f'    "{p.entry()}",' for p in ports)
    out = run_action(node, "set_ports", {"CFG": cfg, "B64": base64.b64encode(body.encode()).decode(), "CHECK": " ".join(str(p.listen) for p in ports)}, timeout=60)

    if "PORTS_OK" not in out:
        raise RuntimeError("the ports were not changed (the old file was put back): " + out.strip()[-160:])

    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        t.forwarded_ports = [p.listen for p in ports]
        rec = dict(t.record or {})
        rec["port_mapping"] = ",".join(p.spec() for p in ports)
        t.record = rec
        emit(db, "info", "tunnels", f"Ports of tunnel {name} changed to {', '.join(p.spec() for p in ports)}.", name)

    return {"ports": [p.listen for p in ports], "mapping": ",".join(p.spec() for p in ports)}


def start_build(iran_id: int, foreign_id: int, ports_text: str, transport: str = "tcpmux", mtu: int = 0, keepalive: int = 75, forced_name: str = "", nodelay: bool = True, opts: dict | None = None) -> int:
    """Validate, create the action_log entry, run the build in a thread. Returns the action id."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to build tunnels")

    if transport not in ("tcp", "tcpmux", "wsmux", "wssmux"):
        raise ValueError("transport must be tcp, tcpmux, wsmux or wssmux")

    ports = parse_ports(ports_text)
    opts = clean_bh_opts(opts)

    if mtu and not 1200 <= mtu <= 1476:
        raise ValueError("MTU must be between 1200 and 1476 (0 = measure)")

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

        entry = ActionLog(ref=forced_name or pair, action="build", source="manual", story=[f"Building a {transport} tunnel {pair}, ports {', '.join(p.spec() for p in ports)}"])
        db.add(entry)
        db.flush()
        aid = entry.id
        b = Build(aid, iran, foreign, ports, transport, mtu, keepalive, nodelay=bool(nodelay), opts=opts, name=forced_name)
        db.expunge(iran)
        db.expunge(foreign)

    threading.Thread(target=_build_worker, args=(b, pair), name=f"build-{pair}", daemon=True).start()
    return aid


def pending_put(item: dict) -> None:
    """Remember a rebuild before the old tunnel is removed: if the panel restarts before the new one is built, `resume_pending` builds it."""
    items = [i for i in st.get("pending_builds").get("items", []) if i.get("name") != item["name"]] + [item]
    st.put("pending_builds", {"items": items})


def pending_drop(name: str) -> None:
    if name:
        st.put("pending_builds", {"items": [i for i in st.get("pending_builds").get("items", []) if i.get("name") != name]})


def resume_pending() -> int:
    """At start: build again every rebuild that a restart interrupted (its tunnel does not exist, the old one was already removed)."""
    started = 0

    for it in list(st.get("pending_builds").get("items", [])):
        name, a = it.get("name", ""), it.get("args") or {}

        with session() as db:
            exists = db.scalar(select(Tunnel).where(Tunnel.name == name)) is not None

        if exists:
            pending_drop(name)
            continue

        try:
            if it.get("kind") == "other":
                from . import build_other

                build_other.start_build(a["iran_id"], a["foreign_id"], a["ports_text"], a["kind"], a["transport"], a.get("opts") or {}, forced_name=name)
            else:
                start_build(a["iran_id"], a["foreign_id"], a["ports_text"], a["transport"], a.get("mtu", 0), a.get("keepalive", 75), forced_name=name, nodelay=bool(a.get("nodelay", True)), opts=a.get("opts") or {})

            started += 1

            with session() as db:
                emit(db, "warn", "tunnels", f"The rebuild of {name} was interrupted by a restart of the panel: building it again.", name)
        except (ValueError, KeyError) as e:
            pending_drop(name)

            with session() as db:
                emit(db, "bad", "tunnels", f"The rebuild of {name} was interrupted and could not be resumed: {str(e)[:120]}. Build it again from Create tunnel.", name)

    return started


def _build_worker(b: Build, pair: str) -> None:
    try:
        _build(b)
    except Exception as e:  # thread boundary
        log.exception("build #%s crashed", b.aid)
        _rollback(b)
        _say(b.aid, f"Stopped: {str(e)[:200]}. Everything created was removed again.", done=True, ok=False)
    finally:
        pending_drop(b.name)

        with _LOCK:
            _BUILDING.discard(pair)


def _rollback(b: Build) -> None:
    if not b.name or not b.created:
        return

    for node in {n for _, n in b.created}:
        try:
            run_action(node, "remove_tunnel_files", {"NAME": b.name, "PORT": str(getattr(b, "port", 0) or 0), "TRANSPORT": b.transport})
        except (SSHError, ValueError) as e:
            log.warning("rollback on %s failed: %s", node.host, e)


def _build(b: Build) -> None:
    aid, iran, foreign = b.aid, _node(b.iran), _node(b.foreign)

    # 1. preflight
    fi, ff = parse_facts(run_script(iran, "build_facts", timeout=40)), parse_facts(run_script(foreign, "build_facts", timeout=40))

    for label, f, srv in (("Iran", fi, b.iran), ("Foreign", ff, b.foreign)):
        if f["broken"]:
            raise RuntimeError(f"{label} server {srv.name} has failed tunnel units: {', '.join(f['broken'][:4])}. Repair or delete them first.")

        if not f["gre_mod"]:
            raise RuntimeError(f"{label} server {srv.name} cannot load the GRE kernel module")

        if srv.host not in [c.split("/")[0] for c in f["ips"]]:
            raise RuntimeError(f"{label} server {srv.name}: {srv.host} is not an address of the server itself (behind NAT?) - GRE needs the real interface address")

    _say(aid, "Preflight: both servers inspected, GRE available, no broken tunnel units")
    ensure_backhaul(iran, foreign, fi, ff, aid)

    with session() as db:
        used_ports = {t.port for t in db.scalars(select(Tunnel))}
        taken_names = {t.name for t in db.scalars(select(Tunnel))} | set(fi["links"]) | set(ff["links"])
        taken_nets = [t.record.get("network") for t in db.scalars(select(Tunnel)) if t.record.get("network")]

    port = next((p for p in range(9900, 10000) if p not in fi["listen"] and p not in used_ports
                 and f"backhaul-iran-{b.transport}-{p}.service" not in fi["units"] and f"backhaul-kharej-{b.transport}-{p}.service" not in ff["units"]), 0)

    if not port:
        raise RuntimeError("no free control port (9900-9999) on the Iran server")

    b.port = port  # type: ignore[attr-defined]

    for p in b.ports:
        if p.listen in fi["listen"]:
            raise RuntimeError(f"port {p.listen} is already in use on the Iran server; choose another port")

    _say(aid, f"Control port {port}")

    # 2. mtu + tuning
    if not b.mtu:
        a = int((re.search(r"MTU=(\d+)", run_script(iran, "mtu_probe", {"PEER": b.foreign.host}, timeout=70)) or [0, "0"])[1])
        c = int((re.search(r"MTU=(\d+)", run_script(foreign, "mtu_probe", {"PEER": b.iran.host}, timeout=70)) or [0, "0"])[1])
        b.mtu = max(1200, min(a or 1400, c or 1400, 1360))
        _say(aid, f"Path MTU measured: Iran side {a or '?'}, foreign side {c or '?'} → GRE MTU {b.mtu}")

    for label, node in (("Iran", iran), ("foreign", foreign)) if b.opts.get("tune", True) else ():
        try:
            _say(aid, f"Kernel tuning on the {label} server: " + run_action(node, "sysctl_tune").strip().splitlines()[-1])
        except (SSHError, ValueError) as e:
            _say(aid, f"Kernel tuning on the {label} server skipped: {e}")

    # 3. plan
    if not b.name:
        b.name = pretty_name(b.foreign.name, b.iran.name, taken_names)

    avoid = [str(b.opts.pop("avoid_net"))] if b.opts.get("avoid_net") else []          # a rebuild for repair: never the inner network the broken link had
    network, iran_ip, foreign_ip = allocate_network(taken_nets + fi["ips"] + ff["ips"] + avoid)
    key = random.randint(1, 2147483647)
    token = secrets.token_hex(16)
    _say(aid, f"Plan: {b.name}, inner network {network} (Iran {iran_ip}, foreign {foreign_ip})")

    # 4. GRE both sides
    for label, node, local, remote, addr in (("Iran", iran, b.iran.host, b.foreign.host, f"{iran_ip}/30"), ("foreign", foreign, b.foreign.host, b.iran.host, f"{foreign_ip}/30")):
        b.created.append(("gre", node))

        for path, text in gre_scripts(b.name, local, remote, key, addr, b.mtu).items():
            _put(node, path, text, "0700" if "/sbin/" in path else "0644")

        st = _unit(node, f"nikatel-gre-{b.name}.service")
        _unit(node, f"nikatel-gre-check-{b.name}.timer")

        if st != "active":
            raise RuntimeError(f"the GRE unit on the {label} server ended {st}")

    _say(aid, "GRE: interfaces up on both servers")
    time.sleep(1)

    (_, ga), rtt = ping_stats(run_script(iran, "gre_verify", {"IFACE": b.name, "PEER": foreign_ip}, timeout=40))
    (_, gb), _ = ping_stats(run_script(foreign, "gre_verify", {"IFACE": b.name, "PEER": iran_ip}, timeout=40))

    if ga < 8 or gb < 8:
        raise RuntimeError(f"the GRE tunnel is up but the inner addresses cannot ping each other (Iran→foreign {ga}/10, foreign→Iran {gb}/10). "
                           "GRE is probably filtered between these two servers; try another Iran server for this foreign server.")

    _say(aid, f"GRE answers both ways (Iran→foreign {ga}/10, foreign→Iran {gb}/10, {rtt} ms). Waiting {GRE_HOLD_WAIT} s to see that it holds…")
    time.sleep(GRE_HOLD_WAIT)
    (sh, gh), _ = ping_stats(run_script(iran, "gre_hold", {"IFACE": b.name, "PEER": foreign_ip}, timeout=45))

    if not sh or gh < int(sh * 0.85):
        raise RuntimeError(f"the GRE tunnel answered at first but then lost its packets ({gh}/{sh} after {GRE_HOLD_WAIT} s): the path filters GRE a few seconds into a flow. Another Iran server is needed for this foreign server.")

    _say(aid, f"GRE holds: {gh}/{sh} pings answered after {GRE_HOLD_WAIT} s")

    # 5. backhaul both sides
    iran_toml, foreign_toml = backhaul_texts(iran_ip, port, token, b.transport, b.ports, b.keepalive, b.nodelay, b.opts)
    iran_cfg, foreign_cfg = f"/etc/backhaul/iran-{b.transport}-{port}.toml", f"/etc/backhaul/kharej-{b.transport}-{port}.toml"
    iran_unit, foreign_unit = f"backhaul-iran-{b.transport}-{port}.service", f"backhaul-kharej-{b.transport}-{port}.service"

    for label, node, cfg, text, unit in (("Iran", iran, iran_cfg, iran_toml, iran_unit), ("foreign", foreign, foreign_cfg, foreign_toml, foreign_unit)):
        b.created.append(("backhaul", node))
        _put(node, cfg, text, "0600")
        _put(node, f"/etc/systemd/system/{unit}", backhaul_unit(cfg), "0644")
        st = _unit(node, unit)

        if st != "active":
            logs = run_script(node, "unit_log", {"UNIT": unit}, timeout=20)
            raise RuntimeError(f"the backhaul service on the {label} server ended {st}: {logs[-300:]}")

    _say(aid, "Backhaul: services running on both servers")
    connected = False

    for _ in range(10):
        time.sleep(1.5)

        if foreign_ip in run_script(iran, "estab_check", {"PORT": str(port)}, timeout=20):
            connected = True
            break

    if not connected:
        logs = run_script(foreign, "unit_log", {"UNIT": foreign_unit}, timeout=20)
        raise RuntimeError("the backhaul client did not connect to the Iran inner address over GRE. " + logs[-300:])

    _say(aid, "Backhaul: control connection established over GRE")

    # 6. data test through the first port
    first = b.ports[0]
    test_port = first.target or first.listen
    data = "skipped (port in use on the foreign server)"

    if test_port not in ff["listen"]:
        run_action(foreign, "data_test_server", {"PORT": str(test_port)})
        time.sleep(1)
        code = run_script(iran, "http_probe", {"PORT": str(first.listen)}, timeout=20).strip()
        data = f"OK (HTTP {code})" if code.startswith("2") else f"failed ({code[-40:]})"

    _say(aid, f"Data path through port {first.listen}: {data}")

    # 7. record
    with session() as db:
        t = Tunnel(name=b.name, type="backhaul", transport=b.transport, engine="backhaul", iran_server_id=b.iran.id, foreign_server_id=b.foreign.id,
                   port=port, iran_tunnel_ip=iran_ip, foreign_tunnel_ip=foreign_ip, forwarded_ports=[p.listen for p in b.ports],
                   record={"network": network, "prefix": 30, "mtu": b.mtu, "ttl": 255, "direction": "reverse", "keepalive": b.keepalive, "nodelay": b.nodelay, "opts": b.opts,
                           "port_mapping": ",".join(p.spec() for p in b.ports), "data_test": data, "built_by": "nikatel-panel"},
                   secrets={"token": token, "gre_key": key}, state="starting", state_since=int(time.time()))
        db.add(t)
        db.flush()
        tid = t.id
        emit(db, "ok", "tunnels", f"Tunnel {b.name} ({b.iran.name} ⇄ {b.foreign.name}) built: ports {', '.join(p.spec() for p in b.ports)}, {rtt} ms over GRE.", b.name)

    check_tunnel(tid)
    _say(aid, f"Tunnel {b.name} is ready.", done=True, ok=True)


# ------------------------------------------------------------------ delete / rebuild
def delete_tunnel(tunnel_id: int, keep_record: bool = False) -> list[str]:
    """Remove the tunnel from both servers (and the row). Returns per-side results."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to delete tunnels")

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        name, port, tr, label = t.name, int(t.port or 0), (t.transport or "tcpmux"), f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"
        nodes = [("Iran", _node(t.iran)), ("foreign", _node(t.foreign))]
        other = (t.type or "").lower() in ("backpack", "paqet", "chisel")
        entry = ActionLog(ref=name, action="delete", source="manual", story=[f"Removing {label} from both servers"])
        db.add(entry)
        db.flush()
        aid = entry.id
        results = []

        if other:
            from . import build_other

            results = build_other.remove(t)

    if not other:
        for side, node in nodes:
            try:
                run_action(node, "remove_tunnel_files", {"NAME": name, "PORT": str(port), "TRANSPORT": tr if tr in ("tcp", "tcpmux", "wsmux", "wssmux") else "tcpmux"}, timeout=90)
                results.append(f"{side}: removed")
            except (SSHError, ValueError) as e:
                results.append(f"{side}: not removed ({e})")

    with session() as db:
        if not keep_record:
            t = db.get(Tunnel, tunnel_id)

            if t is not None:
                db.delete(t)

        e = db.get(ActionLog, aid)

        if e is not None:
            e.story = list(e.story or []) + results
            e.ok = all("removed" in r and "not" not in r for r in results)
            e.finished_at = int(time.time())

        emit(db, "warn" if all("not" not in r for r in results) else "bad", "tunnels", f"Tunnel {label} removed: " + "; ".join(results), name)

    return results


def rebuild_tunnel(tunnel_id: int, fresh_link: bool = False) -> int:
    """Same pair, same name, same ports: remove, then build again. `fresh_link`: a GRE tunnel gets a different inner network (and, as
    always, a new GRE key) - a repair step for a link that a filter has learnt. Returns the build action id."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        name, iran_id, foreign_id, typ, rec = t.name, t.iran_server_id, t.foreign_server_id, (t.type or "").lower(), dict(t.record or {})
        ports_text = rec.get("port_mapping") or ",".join(str(p) for p in (t.forwarded_ports or []))
        transport, mtu, keepalive = t.transport or "tcpmux", int(rec.get("mtu") or 0), int(rec.get("keepalive") or 75)

    if not ports_text:
        raise ValueError("the saved record has no ports")

    other = typ in ("backpack", "paqet", "chisel")
    kind = rec.get("kind") or ("backpack_direct" if transport.startswith("l3:") else typ)
    opts = dict(rec.get("opts") or {})

    if fresh_link and not other and rec.get("network"):
        opts["avoid_net"] = rec["network"]

    pending_put({"name": name, "kind": "other" if other else "backhaul",
                 "args": {"iran_id": iran_id, "foreign_id": foreign_id, "ports_text": ports_text, "transport": transport.replace("l3:", "") if other else transport, "kind": kind, "mtu": mtu,
                          "keepalive": keepalive, "nodelay": bool(rec.get("nodelay", True)), "opts": opts}})
    delete_tunnel(tunnel_id)

    if other:
        from . import build_other

        return build_other.start_build(iran_id, foreign_id, ports_text, kind, transport.replace("l3:", ""), opts, forced_name=name)

    return start_build(iran_id, foreign_id, ports_text, transport, mtu, keepalive, forced_name=name, nodelay=bool(rec.get("nodelay", True)), opts=opts)
