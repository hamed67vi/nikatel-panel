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
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass, field

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Server, Tunnel, is_standby, is_temp
from ..ssh import Node, SSHError, run_action, run_script
from ..ssh import target as ssh_target
from . import linkmodels
from . import settings as st
from .events import emit
from .trash import snapshot as trash_snapshot
from .tunnels import check_tunnel, parse_side, sname

log = get_logger("builder")
GRE_HOLD_WAIT = 10
STABLE_WAIT = 5              # a built tunnel must still be running this many seconds later (was 8: the check after the build, the sentinel and the soak of a repair watch it afterwards)
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


def wg_check_script(name: str, peer: str, stale: int = 300) -> str:
    """The health-check script of a WireGuard link (plain or carried): the ping to the peer as in a GRE link's, and the age of the last handshake. Three missed checks in a row restart the link (as for GRE); when the last handshake is
    older than `stale` seconds - the link has no session, waiting does not bring one - TWO are enough. The private key stays where it is: nothing here reads it."""
    return f"""#!/bin/sh
# nikatel-gre-check-script
# Nikatel WireGuard link {name}: the ping to the peer, and the age of the last handshake (a link with no session is restarted one check sooner)
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
H=$(wg show "$NAME" latest-handshakes 2>/dev/null | awk '{{print $2}}' | head -1)
AGE=999999
[ -n "$H" ] && [ "$H" -gt 0 ] && AGE=$(( $(date +%s) - H ))
NEED=3
[ "$AGE" -gt {int(stale)} ] && NEED=2
if [ "$n" -ge "$NEED" ]; then
  rm -f "$F"
  logger -t nikatel-wg "$NAME: the peer did not answer $n times in a row (last handshake $AGE s ago), restarting the link"
  systemctl restart nikatel-gre-$NAME.service
fi
exit 0
"""


# ------------------------------------------------------------------ the link under Backhaul: GRE, GRE inside UDP, WireGuard
# Backhaul runs over the inner addresses of a point-to-point link between the two servers. The link is GRE (IP protocol 47) as always, or - where a route filters protocol 47, the commonest cut the Doctor meets - the same GRE
# inside UDP ("fou": Foo-over-UDP, one symmetric UDP port) or a WireGuard link (UDP, encrypted, authenticated). Everything above the link (the units, the checks, the bandwidth, the Doctor) is the same: the link keeps the
# names a GRE link has (the interface is called like the tunnel, the units are `nikatel-gre-<name>` ...). Idea and design taken from simple-tun-link (github.com/ach1992/simple-tun-link): a kernel link, keys that never
# leave their server, an owner tag on the interface, a firewall rule that names the exact peer and port.
LINKS = linkmodels.LINKS                  # (1.69.0: nine models - GRE, GRE over FOU / GUE, IPIP, IPIP over FOU / GUE, WireGuard, IPsec ESP, IPsec in UDP; the scripts of the new ones are in services/linkmodels.py)
LINK_TITLE = linkmodels.NAMES                 # (a name for every model, the carried ones too)
LINK_PORTS = linkmodels.PORTS             # the UDP port range of a link of that kind: ONE port, the same on both servers (the raw-protocol links - GRE, IPIP, ESP - have none)
_PUB_RE = re.compile(r"[A-Za-z0-9+/]{43}=")
_IPV4_RE = re.compile(r"(\d{1,3}\.){3}\d{1,3}")
_LINK_FW_DOWN = """if command -v iptables >/dev/null 2>&1; then
  iptables() { command iptables -w 5 "$@"; }
  for chain in INPUT OUTPUT FORWARD; do
    iptables -S "$chain" 2>/dev/null | grep -- "--comment $TAG" | sed "s/^-A/-D/" | while read -r rule; do
      iptables $rule 2>/dev/null || true
    done
  done
  iptables -t mangle -S FORWARD 2>/dev/null | grep -- "--comment $TAG" | sed "s/^-A/-D/" | while read -r rule; do
    iptables -t mangle $rule 2>/dev/null || true
  done
fi
"""
_LINK_FW_UP = """if command -v iptables >/dev/null 2>&1; then
  iptables() { command iptables -w 5 "$@"; }      # (wait up to 5 s for the xtables lock: two links made at the same moment on one server do not fail on it)
  iptables -C INPUT  -s "$REMOTE" -d "$LOCAL" @MATCH_IN@ -m comment --comment "$TAG" -j ACCEPT 2>/dev/null || \\
    iptables -I INPUT 1  -s "$REMOTE" -d "$LOCAL" @MATCH_IN@ -m comment --comment "$TAG" -j ACCEPT
  iptables -C OUTPUT -s "$LOCAL" -d "$REMOTE" @MATCH_OUT@ -m comment --comment "$TAG" -j ACCEPT 2>/dev/null || \\
    iptables -I OUTPUT 1 -s "$LOCAL" -d "$REMOTE" @MATCH_OUT@ -m comment --comment "$TAG" -j ACCEPT
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
"""
_LINK_SYSCTL = """sysctl -qw net.ipv4.ip_forward=1 2>/dev/null || true
sysctl -qw "net.ipv4.conf.$NAME.rp_filter=0" 2>/dev/null || true
sysctl -qw net.ipv4.conf.all.rp_filter=0 2>/dev/null || true
exit 0
"""
_WG_UP = """#!/bin/sh
# Nikatel WireGuard link @NAME@  (idempotent: also used by the health-check timer; the private key is in KEY, made on this server, and is never in a command line)
NAME=@NAME@
LOCAL=@LOCAL@
REMOTE=@REMOTE@
PORT=@PORT@
ADDR=@ADDR@
MTU=@MTU@
PEER_IP=@PEER_IP@
PEER_PUB=@PEER_PUB@
KEY=/etc/nikatel/wg/@NAME@.key
TAG="nikatel-gre-$NAME"

modprobe wireguard 2>/dev/null || true
[ -s "$KEY" ] || { logger -t nikatel-wg "$NAME: the key file $KEY is missing"; exit 1; }
@FIREWALL@
if ! ip link show "$NAME" >/dev/null 2>&1; then
  ip link add "$NAME" type wireguard || exit 1
fi
ip link set dev "$NAME" alias "nikatel:$NAME" 2>/dev/null || true
wg set "$NAME" listen-port "$PORT" private-key "$KEY" peer "$PEER_PUB" endpoint "$REMOTE:$PORT" allowed-ips "$PEER_IP/32" persistent-keepalive 25 || exit 1
ip addr show dev "$NAME" | grep -q "inet ${ADDR%/*}/" || ip addr add "$ADDR" dev "$NAME"
ip link set "$NAME" mtu "$MTU" up
@SYSCTL@"""
_WG_DOWN = """#!/bin/sh
NAME=@NAME@
TAG="nikatel-gre-$NAME"
ip link set "$NAME" down 2>/dev/null || true
if ip -d link show "$NAME" 2>/dev/null | grep -q wireguard; then ip link del "$NAME" 2>/dev/null || true; fi
@FIREWALL@exit 0
"""
def link_scripts(link: str, name: str, local: str, remote: str, key: int, addr_cidr: str, mtu: int, port: int = 0, peer_pub: str = "", ttl: int = 255, ipsec: dict | None = None, carrier: dict | None = None) -> dict[str, str]:
    """The files of the link of one end, byte-compatible in names with `gre_scripts` (the check script, the units and the timer ARE those of a GRE link): for "gre" exactly `gre_scripts`; for the others the up and
    down scripts differ - "wg" (WireGuard on `port`, the peer's public key `peer_pub`), "fou" / "gue" / "ipip" / "ipipfou" / "ipipgue" (GRE or IPIP, native or inside UDP: `port`), "ipsec" / "ipsecnat" (ESP, or ESP in UDP
    on `port`; `ipsec`: {"spi_tx", "spi_rx", "key_tx", "key_rx"} of THIS end - the other end gets them swapped), "wgchisel" / "wgchiselfwd" (WireGuard whose packets travel inside a Chisel carrier: `port` is the number of the
    carrier, `carrier`: {"role": "iran" | "foreign" (this end), "token": the carrier's password, "fp": the pinned fingerprint or ""}). Everything is checked first: the scripts run as root on a server."""
    if link not in linkmodels.ALL_LINKS:
        raise ValueError("the link is one of " + ", ".join(linkmodels.ALL_LINKS))

    files = gre_scripts(name, local, remote, key, addr_cidr, mtu, ttl)

    if link == "gre":
        return files

    if not NAME_RE.fullmatch(name) or not _IPV4_RE.fullmatch(local) or not _IPV4_RE.fullmatch(remote):
        raise ValueError("a link needs a tunnel name and two IPv4 addresses")

    if link in linkmodels.CARRIER_LINKS:
        if not carrier or not _PUB_RE.fullmatch(peer_pub or ""):
            raise ValueError("a carried WireGuard link needs the public key of the other end and its carrier (role, password)")

        m_in, m_out = linkmodels.carrier_fw_match(link, str(carrier["role"]), int(port))
        files.update(linkmodels.carrier_files(link, name, str(carrier["role"]), local, remote, addr_cidr, int(mtu), int(port), peer_pub, str(carrier["token"]), str(carrier.get("fp") or ""),
                                              _LINK_FW_UP.replace("@MATCH_IN@", m_in).replace("@MATCH_OUT@", m_out), _LINK_FW_DOWN, _LINK_SYSCTL))
        files[f"/usr/local/sbin/nikatel-gre-check-{name}"] = wg_check_script(name, linkmodels.peer_of(addr_cidr))
        unit = f"/etc/systemd/system/nikatel-gre-{name}.service"
        files[unit] = files[unit].replace("Nikatel GRE tunnel", f"Nikatel {LINK_TITLE[link]} link")
        return files

    if link in LINK_PORTS:
        lo, hi = LINK_PORTS[link]

        if not lo <= int(port) <= hi:
            raise ValueError("a link needs a UDP port of its range")

    if link == "wg" and not _PUB_RE.fullmatch(peer_pub or ""):
        raise ValueError("a WireGuard link needs the public key of the other end")

    iface = ipaddress.ip_interface(addr_cidr)
    peer_ip = next(str(h) for h in iface.network.hosts() if str(h) != str(iface.ip))
    match = linkmodels.firewall_match(link, port)
    fw_up, fw_down = _LINK_FW_UP.replace("@MATCH_IN@", match).replace("@MATCH_OUT@", match), _LINK_FW_DOWN
    up_path, down_path = f"/usr/local/sbin/nikatel-gre-up-{name}", f"/usr/local/sbin/nikatel-gre-down-{name}"

    if link == "wg":
        put = {"@NAME@": name, "@LOCAL@": local, "@REMOTE@": remote, "@PORT@": str(int(port)), "@ADDR@": addr_cidr, "@MTU@": str(int(mtu)), "@PEER_IP@": peer_ip, "@PEER_PUB@": peer_pub}

        def fill(text: str, firewall: str) -> str:
            text = text.replace("@FIREWALL@", firewall).replace("@SYSCTL@", _LINK_SYSCTL)

            for k, v in put.items():
                text = text.replace(k, v)

            return text

        files[up_path], files[down_path] = fill(_WG_UP, fw_up), fill(_WG_DOWN, fw_down)
        files[f"/usr/local/sbin/nikatel-gre-check-{name}"] = wg_check_script(name, peer_ip)
    elif link in ("ipsec", "ipsecnat"):
        if not ipsec:
            raise ValueError("an IPsec link needs its keys and SPIs")

        files.update(linkmodels.ipsec_files(link, name, local, remote, int(key), addr_cidr, int(mtu), int(port) if link in LINK_PORTS else 0, int(ipsec["spi_tx"]), int(ipsec["spi_rx"]), str(ipsec["key_tx"]), str(ipsec["key_rx"]), fw_up, fw_down, _LINK_SYSCTL))
    else:
        files.update(linkmodels.enc_files(link, name, local, remote, int(key), addr_cidr, int(mtu), int(port) if link in LINK_PORTS else 0, int(ttl), fw_up, fw_down, _LINK_SYSCTL))

    unit = f"/etc/systemd/system/nikatel-gre-{name}.service"
    files[unit] = files[unit].replace("Nikatel GRE tunnel", f"Nikatel {LINK_TITLE[link]} link")
    return files

def parse_link_facts(out: str) -> dict:
    """Pure: the answer of the `link_facts` script."""
    kv: dict[str, str] = {}

    for line in str(out).splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            kv[k.strip()] = v.strip()

    def ports(s: str) -> set[int]:
        return {int(x) for x in s.split() if x.isdigit()}

    pairs = {tuple(x.split(">", 1)) for x in kv.get("IPIP_PAIRS", "").split() if ">" in x}

    return {"wg_mod": kv.get("WG_MOD") == "1", "wg_tool": kv.get("WG_TOOL") == "1", "fou_mod": kv.get("FOU_MOD") == "1", "udp": ports(kv.get("UDP", "")), "fou_ports": ports(kv.get("FOU_PORTS", "")),
            "apt": kv.get("APT") == "1", "mods": set(kv.get("MODS", "").split()), "ipip_pairs": pairs, "tcp": ports(kv.get("TCP", "")), "chisel": kv.get("CHISEL") == "1"}


def pick_link_port(link: str, taken: set[int]) -> int:
    """Pure: the first UDP port of the range of this link that nobody holds (0 when the range is full)."""
    lo, hi = LINK_PORTS[link]
    return next((p for p in range(lo, hi + 1) if p not in taken), 0)


def parse_pub(out: str) -> str:
    """Pure: the public key in the answer of `wg_keygen` ('' when there is none)."""
    m = re.search(r"PUB=(" + _PUB_RE.pattern + ")", str(out))
    return m.group(1) if m else ""


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

    if "install_wg" in o:                                         # the owner asked for this build (Create tunnel, a test he started): wireguard-tools may be installed with apt on a server that lacks it
        out["install_wg"] = bool(o["install_wg"])

    if o.get("link") not in (None, "", "gre"):                  # what Backhaul runs over: GRE (the default, not written), GRE inside UDP, WireGuard, ... or a WireGuard link inside a Chisel carrier
        if o["link"] not in linkmodels.ALL_LINKS:
            raise ValueError("link must be one of: " + ", ".join(linkmodels.ALL_LINKS))

        out["link"] = o["link"]

    if o.get("link_fallback"):                                    # (not kept in the record: start_build takes it out)
        out["link_fallback"] = True

    if o.get("avoid_net") and re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}/\d{1,2}", str(o["avoid_net"])):          # a rebuild with a NEW link: never the inner network the broken link had (it was dropped here before)
        out["avoid_net"] = str(o["avoid_net"])

    return out


def backhaul_texts(iran_ip: str, port: int, token: str, transport: str, ports: list[PortMap], keepalive: int = 75, nodelay: bool = True, opts: dict | None = None, tls_name: str = "") -> tuple[str, str]:
    """(iran server toml, foreign client toml) - tuned for many users. `opts` are the Advanced settings (see clean_bh_opts). A TLS transport (wssmux) names the
    certificate the server listens with (`tls_name`: made on the server by the backhaul_tls action)."""
    o = opts or {}
    tls = f'tls_cert = "/etc/backhaul/{tls_name}.crt"\ntls_key = "/etc/backhaul/{tls_name}.key"\n' if transport in ("wss", "wssmux") and tls_name else ""
    mux = transport != "tcp"
    nd = "true" if nodelay else "false"
    mv, fs, rb, sb = o.get("mux_version", 2), o.get("mux_framesize", 32768), o.get("mux_recievebuffer", 16777216), o.get("mux_streambuffer", 2097152)   # the stream window caps every user's stream at window / RTT: 2 MB ≈ 160 Mb/s at 100 ms (512 KB gave 40 Mb/s in the real test)
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
{tls}{mux_server}
ports = [
{port_lines}
]
"""
    foreign = f"""[client]
remote_addr = "{iran_ip}:{port}"
transport = "{transport}"
token = "{token}"
connection_pool = {o.get('connection_pool', 24 if mux else 32)}
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
    link_fallback: bool = False        # the owner built it from Create tunnel: a link that does not pass is followed by the other link models (`_build_worker`)
    link_tried: list = field(default_factory=list)
    standby_of: str = ""   # the warm standby of this tunnel (services/standby.py): the row is made switched off and without an event, under its own lock (the repair of the real tunnel may build at the same time)


CANCEL: set[int] = set()   # build ids the operator stopped: the next step of the build raises and the build rolls everything back


class BuildCancelled(RuntimeError):
    pass


class LinkFailed(RuntimeError):
    """The LINK under Backhaul (GRE, GRE in UDP, IPIP, WireGuard, IPsec) could not be made or does not carry packets between these two servers: another link model may pass where this one does not."""


# The models tried after the one the owner chose, when it does not pass (the owner, 2026-10-20: "there are several GRE models - when the build finds that one does not answer it must test the others"). The ones in UDP come first (a route that
# filters IP protocol 47 passes UDP most of the time), then the ones in raw IP protocols. WireGuard is last and only where `wg` is on the servers already: a build never installs a package by itself for a fall-back.
FALLBACK_LINKS = ("gre", "fou", "gue", "ipsecnat", "ipipfou", "ipipgue", "ipip", "ipsec", "wg", "wgchisel", "wgchiselfwd")
NEEDS_WG_TOOL = ("wg", *linkmodels.CARRIER_LINKS)          # the models that need wireguard-tools on both servers (installed only when the owner asked: `install_wg`)


def next_link(tried: list[str], wg_ok: bool = False) -> str:
    """The next link model to try after the ones in `tried` (the order of FALLBACK_LINKS), or "" when none is left."""
    return next((k for k in FALLBACK_LINKS if k not in tried and k in linkmodels.ALL_LINKS and (k not in NEEDS_WG_TOOL or wg_ok)), "")


def _wg_ready(b: Build) -> bool:
    """Is WireGuard fully there on BOTH servers (the tool and the kernel module)? Then a link that does not pass may be followed by WireGuard without the owner having asked for an install: nothing is installed. A server that does not answer: no."""
    try:
        li, lf = _both(lambda: parse_link_facts(run_script(_node(b.iran), "link_facts", timeout=40)), lambda: parse_link_facts(run_script(_node(b.foreign), "link_facts", timeout=40)))
    except (SSHError, ValueError, RuntimeError):
        return False

    return all(f["wg_tool"] and f["wg_mod"] for f in (li, lf))


def _say(aid: int, line: str, done: bool | None = None, ok: bool | None = None) -> None:
    if aid in CANCEL and not done:
        raise BuildCancelled("stopped by you")

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


def _both(fn_iran, fn_foreign):
    """Run two independent per-server steps (one on the Iran server, one on the foreign server) at the SAME time and return their answers in that order. An error of either is raised once both have ended, so
    nothing is left running behind the exception. (The owner, 2026-10-17: "bring the delay down to the minimum" - a build asked the two servers one after the other for facts, MTU, tuning and the GRE check:
    the second answer was waited for for nothing.)"""
    with ThreadPoolExecutor(2) as ex:
        fi, ff = ex.submit(fn_iran), ex.submit(fn_foreign)
        futures_wait([fi, ff])

    return fi.result(), ff.result()           # (raises the Iran side's error first, else the foreign side's - after BOTH have ended)


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
        r = subprocess.run(["scp", *opts, "-P", str(src.port), "--", f"{ssh_target(src)}:/usr/local/bin/backhaul", str(local)], capture_output=True, text=True, timeout=240, check=False)

        if r.returncode != 0 or local.stat().st_size < 1024:
            raise RuntimeError("could not fetch the backhaul binary: " + r.stderr.strip()[:120])

    r = subprocess.run(["scp", *opts, "-P", str(dst.port), "--", str(local), f"{ssh_target(dst)}:/usr/local/bin/.backhaul.nikatel-tmp"], capture_output=True, text=True, timeout=240, check=False)

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


def mtu_limit(opts: dict | None) -> int:
    """Pure: the highest MTU a build may be asked for. The path between the servers holds a GRE / UDP link to 1476; a link inside a Chisel carrier travels in a TCP stream and is held only by what Chisel carries."""
    return linkmodels.CARRIER_MTU if (opts or {}).get("link") in linkmodels.CARRIER_LINKS else 1476


def start_build(iran_id: int, foreign_id: int, ports_text: str, transport: str = "tcpmux", mtu: int = 0, keepalive: int = 75, forced_name: str = "", nodelay: bool = True, opts: dict | None = None,
                standby_of: str = "") -> int:
    """Validate, create the action_log entry, run the build in a thread. Returns the action id. `standby_of`: the name of the tunnel this one is the warm standby of."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to build tunnels")

    if transport not in ("tcp", "tcpmux", "wsmux", "wssmux"):
        raise ValueError("transport must be tcp, tcpmux, wsmux or wssmux")

    from . import license

    if not license.tunnel_kind_allowed(transport):          # the free tier builds Backhaul TCP Mux only
        license.require("tunnels")

    ports = parse_ports(ports_text)
    opts = clean_bh_opts(opts)
    fallback = bool(opts.pop("link_fallback", False))

    top = mtu_limit(opts)
    if mtu and not 1200 <= mtu <= top:
        raise ValueError(f"MTU must be between 1200 and {top} (0 = measure)")

    with session() as db:
        iran, foreign = db.get(Server, iran_id), db.get(Server, foreign_id)

        if iran is None or foreign is None:
            raise ValueError("choose an Iran server and a foreign server")

        if iran.role != "iran" or foreign.role != "foreign":
            raise ValueError("the first server must be an Iran server and the second a foreign one")

        if not iran.enabled or not foreign.enabled:
            raise ValueError("both servers must be enabled")

        pair = f"{iran.name} ⇄ {foreign.name}"
        lock = f"{pair} (standby {forced_name})" if standby_of else pair          # a standby is built beside the real tunnel of its pair: its own key, the servers' locks still make the builds wait for each other

        with _LOCK:
            if lock in _BUILDING:
                raise ValueError("a tunnel between these two servers is already being built")

            _BUILDING.add(lock)

        entry = ActionLog(ref=forced_name or pair, action="standby" if standby_of else "build", source="manual", story=[f"Building a {transport} tunnel {pair}, ports {', '.join(p.spec() for p in ports)}"])
        db.add(entry)
        db.flush()
        aid = entry.id
        b = Build(aid, iran, foreign, ports, transport, mtu, keepalive, nodelay=bool(nodelay), opts=opts, name=forced_name, standby_of=standby_of, link_fallback=fallback)
        db.expunge(iran)
        db.expunge(foreign)

    threading.Thread(target=_build_worker, args=(b, lock), name=f"build-{lock}", daemon=True).start()
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


_SRV_LOCKS: dict[int, threading.Lock] = {}


class server_locks:
    """A build holds both of its servers for its whole run. Two builds that touch the same server (a test and a real build to the same foreign server) would
    choose the same free control port, write the same unit and config names and - worse - the one that finishes first (a temporary test removes its files
    by port) would delete the other's: the second build waits for the first."""

    def __init__(self, ids: list[int], aid: int):
        self.locks = [_SRV_LOCKS.setdefault(i, threading.Lock()) for i in sorted(set(ids))]
        self.aid, self.held = aid, []

    def __enter__(self):
        for lock in self.locks:
            if not lock.acquire(blocking=False):
                _say(self.aid, "Another build is using one of these servers: waiting for it to finish (builds on the same server run one after the other)…")
                lock.acquire()

            self.held.append(lock)

        return self

    def __exit__(self, *exc):
        for lock in reversed(self.held):
            lock.release()

        return False


def _build_worker(b: Build, pair: str) -> None:
    try:
        with server_locks([b.iran.id, b.foreign.id], b.aid):
            while True:
                try:
                    _build(b)
                    break
                except LinkFailed as e:
                    cur = str(b.opts.get("link") or "gre")
                    b.link_tried.append(cur)
                    nxt = next_link(b.link_tried, wg_ok=bool(b.opts.get("install_wg")) or _wg_ready(b)) if b.link_fallback and not b.standby_of else ""

                    if not nxt:
                        if len(b.link_tried) > 1:
                            _say(b.aid, "None of the link models passed between these two servers (tried: " + ", ".join(LINK_TITLE[k] for k in b.link_tried) + "). The path between them is filtered for all of them.")

                        raise

                    _say(b.aid, f"{LINK_TITLE[cur]} did not pass between these two servers: {str(e)[:160]}")
                    _rollback(b)
                    b.created = []
                    b.opts["link"] = nxt
                    _say(b.aid, f"Everything of it was removed. Trying the next link model: {LINK_TITLE[nxt]}…")
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


def _ensure_wg_tool(aid: int, node: Node, facts: dict, label: str, server: str, install: bool) -> None:
    """The server needs `wg` (wireguard-tools). When the OWNER asked for this build (`install`) it is installed with apt if it is missing - nothing is copied from another machine. A build the Doctor or the night
    survey makes by itself never installs a package on a server: it fails with the sentence that says what to do (and the Doctor tries the next tunnel type)."""
    if facts["wg_tool"]:
        return

    if not install:
        raise RuntimeError(f"{label} server {server}: wireguard-tools is not installed (Servers -> its menu -> Install -> WireGuard tools, or build a WireGuard tunnel once from Create tunnel: that installs it)")

    _say(aid, f"{label} server {server}: wireguard-tools is not installed - installing it with apt (a minute at most)")
    out = run_action(node, "wg_install", timeout=330)

    if "WG_OK" not in out:
        raise RuntimeError(f"{label} server {server}: wireguard-tools could not be installed ({out.split('WG_FAIL', 1)[-1].strip()[:200] or 'no answer'}). Install it by hand (apt install wireguard-tools) and build again.")


def _prepare_link(b: Build, iran: Node, foreign: Node, link: str) -> tuple[int, dict[str, str]]:
    """Before the scripts of a link that is not GRE are written: both servers can do it (the kernel module; for WireGuard also the tool), the UDP port is free on BOTH and in no other tunnel, and - WireGuard - each server
    makes its own key and says the public half (the private key never leaves its server). Returns (the UDP port, {"iran": public key, "foreign": public key})."""
    aid = b.aid
    raw_i, raw_f = _both(lambda: run_script(iran, "link_facts", timeout=40), lambda: run_script(foreign, "link_facts", timeout=40))
    li, lf = parse_link_facts(raw_i), parse_link_facts(raw_f)

    for label, f, srv in (("Iran", li, b.iran), ("Foreign", lf, b.foreign)):
        if link in NEEDS_WG_TOOL and not f["wg_mod"]:
            raise RuntimeError(f"{label} server {srv.name} cannot load the WireGuard kernel module (it needs Linux 5.6 or newer, or the wireguard module)")

        if "fou" in linkmodels.kernel_of(link) and not f["fou_mod"]:
            raise RuntimeError(f"{label} server {srv.name} cannot load the FOU kernel module (GRE / IPIP over UDP)")

        missing = [m for m in linkmodels.kernel_of(link) if m not in ("fou", "wireguard") and m not in f["mods"]] if f["mods"] else []          # (a server always says which modules it has: an answer without the list is checked by the fou / wireguard flags only)

        if missing:
            raise RuntimeError(f"{label} server {srv.name} cannot load the {', '.join(missing)} kernel module ({LINK_TITLE[link]})")

    if link in linkmodels.ONE_PER_PAIR and ((b.foreign.host, b.iran.host) in li["ipip_pairs"] or (b.iran.host, b.foreign.host) in lf["ipip_pairs"]):
        raise RuntimeError(f"an IPIP tunnel between {b.iran.name} and {b.foreign.name} exists already (a tunnel of this pair, or the warm standby of one, runs over IPIP): IPIP has no key, the kernel holds ONE per pair of addresses (use GRE, GRE over UDP or WireGuard for another tunnel between them)")

    if link in NEEDS_WG_TOOL:
        install = bool(b.opts.get("install_wg"))
        _both(lambda: _ensure_wg_tool(aid, iran, li, "Iran", b.iran.name, install), lambda: _ensure_wg_tool(aid, foreign, lf, "Foreign", b.foreign.name, install))

    carried = link in linkmodels.CARRIER_LINKS

    if carried:                  # the carrier is Chisel: a core the panel installs (the same copy from its cache that a Chisel tunnel gets)
        from . import engines

        for label, node, srv in (("Iran", iran, b.iran), ("Foreign", foreign, b.foreign)):
            if engines.ensure_on_node(node, "chisel", only_missing=True):
                _say(aid, f"Chisel installed on the {label.lower()} server {srv.name} (it carries the WireGuard packets)")

    with session() as db:
        used = {int((t.record or {}).get("link_port") or 0) for t in db.scalars(select(Tunnel))}

    if carried:                  # one number gives three ports: the WireGuard port, the loopback door of the Iran side, the TCP port of the Chisel server
        lo, hi = linkmodels.CARRIER_RANGE
        held = [u for u in used if lo <= u <= hi]
        taken_udp = li["udp"] | lf["udp"] | li["fou_ports"] | lf["fou_ports"] | {p for u in held for p in linkmodels.carrier_ports(u)[:2]}
        port = linkmodels.pick_carrier_port(taken_udp, li["tcp"] | lf["tcp"] | {linkmodels.carrier_ports(u)[2] for u in held})

        if not port:
            raise RuntimeError(f"no free port for the {LINK_TITLE[link]} link on the two servers (the numbers {lo}-{hi} give its UDP and TCP ports)")
    else:
        port = pick_link_port(link, li["udp"] | lf["udp"] | li["fou_ports"] | lf["fou_ports"] | used) if link in LINK_PORTS else 0

        if link in LINK_PORTS and not port:
            raise RuntimeError(f"no free UDP port for the {LINK_TITLE[link]} link ({LINK_PORTS[link][0]}-{LINK_PORTS[link][1]}) on the two servers")

    pubs: dict[str, str] = {}

    if link in ("ipsec", "ipsecnat"):          # no IKE daemon: the panel makes the keys and the SPIs of the two directions (random, 20 bytes = a 128-bit AES-GCM key and its salt) and writes them into a file of 0600 on each server
        pubs = {"key_a": secrets.token_hex(20), "key_b": secrets.token_hex(20), "spi_a": str(random.randint(256, 2**32 - 1)), "spi_b": str(random.randint(256, 2**32 - 1))}

    if link in NEEDS_WG_TOOL:
        b.created.append(("wg", iran))
        b.created.append(("wg", foreign))
        out_i, out_f = _both(lambda: run_action(iran, "wg_keygen", {"NAME": b.name}, timeout=40), lambda: run_action(foreign, "wg_keygen", {"NAME": b.name}, timeout=40))
        pubs = {"iran": parse_pub(out_i), "foreign": parse_pub(out_f)}

        if not pubs["iran"] or not pubs["foreign"]:
            raise RuntimeError("a server did not make its WireGuard key")

    if carried:
        pubs["token"] = secrets.token_hex(24)          # the password of the carrier: in a file of 0600 on each server

    wg_port, door, tcp = linkmodels.carrier_ports(port) if carried else (0, 0, 0)
    dials = "the foreign server dials the Iran server" if carried and linkmodels.CARRIER_DIAL[link] == "foreign" else "the Iran server dials the foreign server"
    carrier = (f"TCP port {tcp} (Chisel, TLS WebSocket; {dials}), WireGuard on UDP {wg_port} inside it, door 127.0.0.1:{door}" if carried else
               f"UDP port {port}" if port else {"ipip": "IP protocol 4", "ipsec": "IP protocol 50 (ESP)"}[link])
    _say(aid, f"{LINK_TITLE[link]} link: {carrier} on both servers" + (", each server made its own key (the private key stays on it)" if link in NEEDS_WG_TOOL else ", keys made for the two directions (a file of 0600 on each server)" if link in ("ipsec", "ipsecnat") else ""))
    return port, pubs


def _file_mode(path: str) -> str:
    return "0700" if "/sbin/" in path else "0600" if path.endswith((".keys", ".env")) else "0644"


def carrier_record(link: str, n: int, fp: str) -> dict:
    """What the tunnel record keeps of a carried link (no secret: the password stays in the files of the two servers)."""
    wg, door, tcp = linkmodels.carrier_ports(n)
    return {"kind": "chisel", "dial": linkmodels.CARRIER_DIAL[link], "tcp_port": tcp, "wg_port": wg, "door": door, "pinned": bool(fp)}


def carrier_ends(b: Build, link: str, iran: Node, foreign: Node, ihost: str, fhost: str, iran_addr: str, foreign_addr: str, n: int, pubs: dict, key: int) -> None:
    """Make the two ends of a WireGuard link carried inside Chisel. The end that runs the Chisel server comes FIRST (its key and certificate are made, its unit starts, its fingerprint is read); the other end then dials it
    with that fingerprint pinned. Each end is one link unit (`nikatel-gre-<name>`) that starts and stops the carrier unit beside it. Everything made is noted in `b.created` before it exists, so a failure removes it all."""
    title = LINK_TITLE[link]
    srv_role = linkmodels.carrier_server_role(link)
    ends = {"iran": (iran, ihost, fhost, iran_addr, pubs["foreign"], "Iran"), "foreign": (foreign, fhost, ihost, foreign_addr, pubs["iran"], "foreign")}

    def make(role: str, fp: str) -> None:
        node, local, remote, addr, peer_pub, label = ends[role]
        b.created.append(("gre", node))

        for path, text in link_scripts(link, b.name, local, remote, key, addr, b.mtu, port=n, peer_pub=peer_pub, carrier={"role": role, "token": pubs["token"], "fp": fp}).items():
            _put(node, path, text, _file_mode(path))

        st = _unit(node, f"nikatel-gre-{b.name}.service")
        _unit(node, f"nikatel-gre-check-{b.name}.timer")

        if st != "active":
            logs = "".join(run_script(node, "unit_log", {"UNIT": u}, timeout=20) for u in (f"nikatel-gre-{b.name}.service", f"nikatel-carrier-{b.name}.service"))
            raise LinkFailed(f"the {title} unit on the {label} server ended {st}" + (f": {logs.strip()[-300:]}" if logs.strip() else ""))

    srv_node = ends[srv_role][0]
    b.created.append(("gre", srv_node))
    run_action(srv_node, "chisel_prepare", {"NAME": b.name, "TLS": "1"}, timeout=60)
    make(srv_role, "")
    fp = (re.search(r"FP=(\S+)", run_script(srv_node, "chisel_fp", {"NAME": b.name}, timeout=20)) or [None, ""])[1]
    pubs["fp"] = fp
    _say(b.aid, f"The Chisel server of the carrier runs on the {ends[srv_role][5]} server" + (", its key is pinned on the other end" if fp else " (its key could not be read: the other end does not pin it)"))
    make("foreign" if srv_role == "iran" else "iran", fp)


def _build(b: Build) -> None:
    aid, iran, foreign = b.aid, _node(b.iran), _node(b.foreign)
    link = str(b.opts.get("link") or "gre")
    ihost, fhost = b.iran.host, b.foreign.host           # (read here, not inside the threads of `_both`)

    # 1. preflight (both servers asked at once)
    raw_i, raw_f = _both(lambda: run_script(iran, "build_facts", timeout=40), lambda: run_script(foreign, "build_facts", timeout=40))
    fi, ff = parse_facts(raw_i), parse_facts(raw_f)

    for label, f, srv in (("Iran", fi, b.iran), ("Foreign", ff, b.foreign)):
        if f["broken"]:
            raise RuntimeError(f"{label} server {srv.name} has failed tunnel units: {', '.join(f['broken'][:4])}. Repair or delete them first.")

        if link == "gre" and not f["gre_mod"]:
            raise LinkFailed(f"{label} server {srv.name} cannot load the GRE kernel module")

        if srv.host not in [c.split("/")[0] for c in f["ips"]]:
            raise RuntimeError(f"{label} server {srv.name}: {srv.host} is not an address of the server itself (behind NAT?) - GRE needs the real interface address")

    _say(aid, "Preflight: both servers inspected, GRE available, no broken tunnel units" if link == "gre" else f"Preflight: both servers inspected, no broken tunnel units; the {LINK_TITLE[link]} link is checked next")
    ensure_backhaul(iran, foreign, fi, ff, aid)

    if not is_temp(b.name):
        from . import onboard

        onboard.ensure_all_cores([b.iran.id, b.foreign.id], lambda line: _say(aid, line))      # (the first tunnel of a server brings every core to it - not the adding of the server)

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
    if link in linkmodels.CARRIER_LINKS:
        b.mtu = b.mtu or linkmodels.CARRIER_MTU
        _say(aid, f"MTU {b.mtu} for the {LINK_TITLE[link]} link: its packets travel in a TCP stream, so the path between the servers does not limit them (Chisel carries up to {linkmodels.CARRIER_MAX_PACKET} bytes, WireGuard adds {linkmodels.CARRIER_OVERHEAD})")
    elif not b.mtu:
        mtu_i, mtu_f = _both(lambda: run_script(iran, "mtu_probe", {"PEER": fhost}, timeout=70), lambda: run_script(foreign, "mtu_probe", {"PEER": ihost}, timeout=70))
        a = int((re.search(r"MTU=(\d+)", mtu_i) or [0, "0"])[1])
        c = int((re.search(r"MTU=(\d+)", mtu_f) or [0, "0"])[1])
        b.mtu = linkmodels.inner_mtu(link, a, c)
        _say(aid, f"Path MTU measured: Iran side {a or '?'}, foreign side {c or '?'} → {LINK_TITLE[link]} MTU {b.mtu}" + (f" (the link adds up to {linkmodels.OVERHEAD[link]} bytes)" if link in linkmodels.OVERHEAD else ""))

    def tune(label: str, node: Node) -> str:
        try:
            return f"Kernel tuning on the {label} server: " + run_action(node, "sysctl_tune").strip().splitlines()[-1]
        except (SSHError, ValueError) as e:
            return f"Kernel tuning on the {label} server skipped: {e}"

    if b.opts.get("tune", True):
        for line in _both(lambda: tune("Iran", iran), lambda: tune("foreign", foreign)):
            _say(aid, line)

    # 3. plan
    if not b.name:
        b.name = pretty_name(b.foreign.name, b.iran.name, taken_names)

    avoid = [str(b.opts.pop("avoid_net"))] if b.opts.get("avoid_net") else []          # a rebuild for repair: never the inner network the broken link had
    network, iran_ip, foreign_ip = allocate_network(taken_nets + fi["ips"] + ff["ips"] + avoid)
    key = random.randint(1, 2147483647)
    token = secrets.token_hex(16)
    _say(aid, f"Plan: {b.name}, inner network {network} (Iran {iran_ip}, foreign {foreign_ip})")
    try:
        link_port, pubs = _prepare_link(b, iran, foreign, link) if link != "gre" else (0, {})
    except BuildCancelled:
        raise
    except RuntimeError as e:
        raise LinkFailed(str(e)) from e
    title = LINK_TITLE[link]
    carried = link in linkmodels.CARRIER_LINKS
    filtered = (f"TCP port {linkmodels.carrier_ports(link_port)[2]} (the carrier) is probably filtered between these two servers, or Chisel does not run on one of them" if carried else
                f"UDP port {link_port} is probably filtered between these two servers" if link_port else
                {"gre": "GRE (protocol 47)", "ipip": "IPIP (protocol 4)", "ipsec": "ESP (protocol 50)"}[link] + " is probably filtered between these two servers")

    # 4. the link on both sides (GRE, GRE / IPIP inside UDP, WireGuard or IPsec)
    def link_side(label: str, node: Node, local: str, remote: str, addr: str, peer_pub: str, ipsec: dict | None = None) -> None:
        b.created.append(("gre", node))

        for path, text in link_scripts(link, b.name, local, remote, key, addr, b.mtu, port=link_port, peer_pub=peer_pub, ipsec=ipsec).items():
            _put(node, path, text, "0700" if "/sbin/" in path else "0600" if path.endswith(".keys") else "0644")

        st = _unit(node, f"nikatel-gre-{b.name}.service")
        _unit(node, f"nikatel-gre-check-{b.name}.timer")

        if st != "active":
            logs = run_script(node, "unit_log", {"UNIT": f"nikatel-gre-{b.name}.service"}, timeout=20) if link != "gre" else ""
            raise LinkFailed(f"the {title} unit on the {label} server ended {st}" + (f": {logs.strip()[-200:]}" if logs.strip() else ""))

    ips = ({"iran": {"spi_tx": pubs["spi_a"], "spi_rx": pubs["spi_b"], "key_tx": pubs["key_a"], "key_rx": pubs["key_b"]},
            "foreign": {"spi_tx": pubs["spi_b"], "spi_rx": pubs["spi_a"], "key_tx": pubs["key_b"], "key_rx": pubs["key_a"]}} if link in ("ipsec", "ipsecnat") else {})      # (the Iran end sends with A and receives with B; the foreign end the other way round)
    if carried:
        carrier_ends(b, link, iran, foreign, ihost, fhost, f"{iran_ip}/30", f"{foreign_ip}/30", link_port, pubs, key)
    else:
        _both(lambda: link_side("Iran", iran, ihost, fhost, f"{iran_ip}/30", pubs.get("foreign", ""), ips.get("iran")), lambda: link_side("foreign", foreign, fhost, ihost, f"{foreign_ip}/30", pubs.get("iran", ""), ips.get("foreign")))      # (an end does not wait for the other: both are made at once)
    _say(aid, "GRE: interfaces up on both servers" if link == "gre" else f"{title}: link up on both servers")
    time.sleep(1 if link == "gre" else 2)                  # (a WireGuard end shakes hands with the other first)

    verify = "gre_verify" if link == "gre" else "carrier_probe_ping" if carried else "link_probe_ping"          # (a link in UDP loses the first packets of a new flow for a few seconds on some routes - up to eight single pings first, then the ten that count; 1.69.3)
    ver_i, ver_f = _both(lambda: ping_stats(run_script(iran, verify, {"IFACE": b.name, "PEER": foreign_ip}, timeout=60)), lambda: ping_stats(run_script(foreign, verify, {"IFACE": b.name, "PEER": iran_ip}, timeout=60)))
    (_, ga), rtt = ver_i
    (_, gb), _ = ver_f

    if ga < 8 or gb < 8:
        raise LinkFailed(f"the {title} link is up but the inner addresses cannot ping each other (Iran→foreign {ga}/10, foreign→Iran {gb}/10). "
                           f"{filtered}; try another Iran server for this foreign server" + ("" if link == "gre" else " or another link type") + ".")

    _say(aid, f"{title} answers both ways (Iran→foreign {ga}/10, foreign→Iran {gb}/10, {rtt} ms). Waiting {GRE_HOLD_WAIT} s to see that it holds…")
    time.sleep(GRE_HOLD_WAIT)
    (sh, gh), _ = ping_stats(run_script(iran, "gre_hold", {"IFACE": b.name, "PEER": foreign_ip}, timeout=45))

    if not sh or gh < int(sh * 0.85):
        raise LinkFailed(f"the {title} link answered at first but then lost its packets ({gh}/{sh} after {GRE_HOLD_WAIT} s): the path filters {'GRE' if link == 'gre' else 'the TCP port of the carrier' if carried else 'this UDP port' if link_port else 'this IP protocol'} a few seconds into a flow. Another Iran server is needed for this foreign server.")

    _say(aid, f"{title} holds: {gh}/{sh} pings answered after {GRE_HOLD_WAIT} s")

    # 5. backhaul both sides
    if b.transport in ("wss", "wssmux"):          # the TLS WebSocket transport listens with a certificate: a self-signed one, made once on the Iran server
        run_action(iran, "backhaul_tls", {"NAME": b.name}, timeout=60)

    iran_toml, foreign_toml = backhaul_texts(iran_ip, port, token, b.transport, b.ports, b.keepalive, b.nodelay, b.opts, tls_name=b.name)
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

    _say(aid, f"Backhaul: control connection established over {title}")

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

    # 6b. still alive a few seconds later: a tunnel that was started and then stopped by something else is not ready
    time.sleep(STABLE_WAIT)

    for label, node, unit in (("Iran", iran, iran_unit), ("foreign", foreign, foreign_unit)):
        side = parse_side(run_script(node, "tunnel_side", {"UNITS": unit, "IFACE": "", "PEER": "", "PORTS": ""}, timeout=35))

        if side.units.get(unit) != "active":
            logs = run_script(node, "unit_log", {"UNIT": unit}, timeout=20)
            raise RuntimeError(f"the backhaul service on the {label} server stopped again after it had started ({side.units.get(unit, 'missing')}): {logs[-300:]}")

    if foreign_ip not in run_script(iran, "estab_check", {"PORT": str(port)}, timeout=20):
        raise RuntimeError("the control connection dropped again a few seconds after it was established: the tunnel is not stable")

    _say(aid, f"Still running {STABLE_WAIT} seconds later: stable")

    # 7. record the tunnel
    with session() as db:
        t = Tunnel(name=b.name, enabled=not is_temp(b.name) and not b.standby_of, type="backhaul", transport=b.transport, engine="backhaul", iran_server_id=b.iran.id, foreign_server_id=b.foreign.id,
                   port=port, iran_tunnel_ip=iran_ip, foreign_tunnel_ip=foreign_ip, forwarded_ports=[p.listen for p in b.ports],
                   record={"network": network, "prefix": 30, "mtu": b.mtu, "ttl": 255, "direction": "reverse", "keepalive": b.keepalive, "nodelay": b.nodelay, "opts": b.opts,
                           "port_mapping": ",".join(p.spec() for p in b.ports), "data_test": data, "built_by": "nikatel-panel",
                           **({"standby_of": b.standby_of} if b.standby_of else {}),
                           **({"link": link, "link_port": link_port, **({"wg": {"iran_pub": pubs["iran"], "foreign_pub": pubs["foreign"]}} if link in NEEDS_WG_TOOL else {}), **({"carrier": carrier_record(link, link_port, pubs.get("fp", ""))} if carried else {})} if link != "gre" else {})},        # (public keys only: the private ones never left their servers)
                   secrets={"token": token, "gre_key": key}, state="starting", state_since=int(time.time()))
        db.add(t)
        db.flush()
        tid = t.id
        if not is_temp(b.name) and not b.standby_of:          # a temporary test tunnel and a standby are not an event
            emit(db, "ok", "tunnels", f"Tunnel {b.name} ({b.iran.name} ⇄ {b.foreign.name}) built: ports {', '.join(p.spec() for p in b.ports)}, {rtt} ms over {title}.", b.name)

    check_tunnel(tid)
    _say(aid, f"Tunnel {b.name} is ready.", done=True, ok=True)


# ------------------------------------------------------------------ delete / rebuild
def delete_tunnel(tunnel_id: int, keep_record: bool = False, trash: bool = False, skip_offline: bool = False) -> list[str]:
    """Remove the tunnel from both servers (and the row). Returns per-side results. `trash`: keep a copy first, so that it can be restored.
    `skip_offline`: a server that is off is not asked over SSH (a 90 s timeout per tunnel would make a failover of ten tunnels wait a quarter of an hour)."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to delete tunnels")

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        name, port, tr, label = t.name, int(t.port or 0), (t.transport or "tcpmux"), f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"
        on_server = sname(t)           # what the servers know it as
        off = {side for side, s in (("Iran", t.iran), ("foreign", t.foreign)) if skip_offline and s is not None and s.online is False}
        nodes = [("Iran", _node(t.iran)), ("foreign", _node(t.foreign))]
        other = (t.type or "").lower() in ("backpack", "paqet", "chisel")
        rec_now = dict(t.record or {})          # (the bandwidth sink of a GRE tunnel is on the foreign server: it goes with the tunnel)
        quiet = is_standby(t)                   # (a warm standby that is removed or built again is not an event: nobody is told that a spare was replaced)
        snap = trash_snapshot(t) if trash and not keep_record else None
        entry = ActionLog(ref=name, action="delete", source="manual", story=[f"Removing {label} from both servers"])
        db.add(entry)
        db.flush()
        aid = entry.id
        results = []
        rm = None

        if other:
            from . import build_other

            rm = build_other.remove_args(t)          # (read here; the servers are asked below, with no session - and so no write lock - open)

    if other:
        from . import build_other

        results = build_other.remove_on(rm[0], rm[1], off)
    else:
        for side, node in nodes:
            if side in off:
                results.append(f"{side}: skipped (the server is off; its files are purged when it is back)")
                continue

            try:
                run_action(node, "remove_tunnel_files", {"NAME": on_server, "PORT": str(port), "TRANSPORT": tr if tr in ("tcp", "tcpmux", "wsmux", "wssmux") else "tcpmux"}, timeout=90)
                results.append(f"{side}: removed")
            except (SSHError, ValueError) as e:
                results.append(f"{side}: not removed ({e})")

            if side == "foreign" and rec_now.get("bw_sink"):
                from . import bandwidth

                bandwidth.remove_sink(tunnel_id, node, rec_now)

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

        if not quiet:
            emit(db, "warn" if all("not" not in r for r in results) else "bad", "tunnels", f"Tunnel {label} removed: " + "; ".join(results), name)

    if snap is not None:
        from . import trash as trash_store

        trash_store.add(snap)

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
    delete_tunnel(tunnel_id, trash=True)          # a copy is kept: a rebuild whose build fails can be restored from 'Recently removed'

    if other:
        from . import build_other

        return build_other.start_build(iran_id, foreign_id, ports_text, kind, transport.replace("l3:", ""), opts, forced_name=name)

    return start_build(iran_id, foreign_id, ports_text, transport, mtu, keepalive, forced_name=name, nodelay=bool(rec.get("nodelay", True)), opts=opts)


def follow_server_host(server_id: int, old_host: str, new_host: str) -> list[str]:
    """A server got a new public address (edited in the panel): the GRE links of its tunnels carry the old address in their scripts, so each
    tunnel is built again on the new one (same name, same ports; a build that fails puts the old record back in 'Recently removed'). For an
    Iran server the ArvanCloud records that pointed at the old address follow, when a key is saved. Returns the names of the tunnels started."""
    from . import autopilot, failover

    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            return []

        role, name = s.role, s.name
        ids = [(t.id, t.name) for t in db.scalars(select(Tunnel).where((Tunnel.iran_server_id == server_id) | (Tunnel.foreign_server_id == server_id), Tunnel.enabled.is_(True)))
               if not is_temp(t.name)]

    if get_settings().read_only or not ids:
        return []

    def run(aid: int) -> None:
        autopilot._say(aid, f"{name}: the address changed from {old_host} to {new_host}. Its {len(ids)} tunnel(s) are built again on the new address, one after the other.")
        healed = 0

        for tid, tname in ids:
            try:
                ok, story = autopilot._rebuild_safe(aid, tid)
                autopilot._say(aid, f"{tname}: " + (story[-1] if story else "no result"))
                healed += 1 if ok else 0
            except (ValueError, RuntimeError, SSHError) as e:
                autopilot._say(aid, f"{tname}: not rebuilt: {str(e)[:160]}")

        if role == "iran" and re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", old_host or "") and re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", new_host or ""):
            failover.follow_ip_change(aid, old_host, new_host, name)

        autopilot._say(aid, f"Done: {healed} of {len(ids)} tunnel(s) run on the new address.", done=True, ok=healed == len(ids))

    try:
        autopilot._start(f"address {name}", run, action="create")
    except ValueError:
        return []

    with session() as db:
        emit(db, "warn", "servers", f"Server {name}: address {old_host} → {new_host}; its {len(ids)} tunnel(s) are rebuilt on the new address.", name)

    return [n for _, n in ids]


# ------------------------------------------------------------------ names
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,14}$")


def _rename_row(db, t: Tunnel, new: str, custom: bool) -> None:
    """The row, the configs that name it and the alert mutes follow the new name. What the servers know it as does not change (see tunnels.sname)."""
    from ..models import Config

    old = t.name
    rec = dict(t.record or {})
    rec.setdefault("slug", old)

    if custom:
        rec["custom_name"] = True

    t.record = rec
    t.name = new

    for c in db.scalars(select(Config).where(Config.tunnel == old)):
        c.tunnel = new

    muted = list(st.get("alerts").get("muted") or [])

    if old in muted:
        st.put("alerts", {"muted": sorted({new if m == old else m for m in muted})})

    from . import standby  # (it imports this module: so it is imported here)

    standby.note_rename(old, new)      # the mark and the standby of the tunnel follow its new name (the standby job does it, outside this session)


def rename_tunnel(tunnel_id: int, new_name: str) -> str:
    """The operator renames a tunnel. Lowercase letters, digits and - (up to 15): the same shape a built tunnel always had, so a rebuild keeps working."""
    new = str(new_name or "").strip().lower()

    if not NAME_RE.match(new):
        raise ValueError("a tunnel name is lowercase letters, digits and - (1-15 characters, starting with a letter or digit)")

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        if new == t.name:
            return new

        if db.scalar(select(Tunnel).where(Tunnel.name == new, Tunnel.id != tunnel_id)) is not None:
            raise ValueError("another tunnel already has this name")

        old = t.name
        _rename_row(db, t, new, custom=True)
        emit(db, "info", "tunnels", f"Tunnel {old} renamed to {new}.", new)

    return new


def follow_server_rename(db, server: Server, old_name: str) -> list[str]:
    """A server was renamed: every tunnel that still carries the name the panel made for it (<foreign>-<iran>, with or without a -2) gets the new one.
    A tunnel the operator named by hand keeps its name."""
    renamed = []
    names = {t.name for t in db.scalars(select(Tunnel))}

    for t in list(db.scalars(select(Tunnel).where((Tunnel.iran_server_id == server.id) | (Tunnel.foreign_server_id == server.id)))):
        if (t.record or {}).get("custom_name"):
            continue

        f_old = old_name if server.role == "foreign" else t.foreign.name
        i_old = old_name if server.role == "iran" else t.iran.name
        auto = pretty_name(f_old, i_old, set())

        if t.name != auto and not re.fullmatch(re.escape(auto) + r"-\d+", t.name):
            continue

        new = pretty_name(t.foreign.name, t.iran.name, names - {t.name})

        if new == t.name:
            continue

        names.discard(t.name)
        names.add(new)
        old = t.name
        _rename_row(db, t, new, custom=False)
        emit(db, "info", "tunnels", f"Tunnel {old} renamed to {new}: the server {old_name} is now {server.name}.", new)
        renamed.append(f"{old} → {new}")

    return renamed
