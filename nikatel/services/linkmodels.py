"""The links a Backhaul tunnel can run over - nine models (1.69.0), the ones of simple-tun-link (github.com/ach1992/simple-tun-link) that need nothing installed.

A link is a point-to-point kernel interface between the two servers with a /30 of inner addresses; the tunnel engine (Backhaul) runs over those inner addresses. The units, the checks, the bandwidth job and the Doctor treat every
link alike: it keeps the names a GRE link has (the interface is called like the tunnel, the units are `nikatel-gre-<name>`). What differs is what has to pass between the two servers, and so which cut a model survives:

    link       what the kernel makes                                                            must pass between the servers       encrypted
    gre        GRE (the panel's own scripts: builder.gre_scripts)                               IP protocol 47                      no
    fou        GRE inside one UDP port (Foo-over-UDP)                                           UDP 5500-5599                       no
    gue        GRE inside one UDP port, Generic UDP Encapsulation                               UDP 5600-5699                       no
    ipip       IP-in-IP (no key: ONE per pair of servers)                                       IP protocol 4                       no
    ipipfou    IP-in-IP inside one UDP port (FOU)                                               UDP 5700-5799                       no
    ipipgue    IP-in-IP inside one UDP port (GUE)                                               UDP 5800-5899                       no
    wg         WireGuard (the keys are made on each server: builder.py)                         UDP 51820-51919                     yes
    ipsec      ESP in tunnel mode on an xfrm interface, keys the panel made (no IKE daemon)     IP protocol 50                      yes
    ipsecnat   the same ESP inside UDP (NAT-T), a socket that decapsulates                      UDP 4500-4599                       yes

A kind of the pair test / the Doctor is "<link>:<transport>" for every link but GRE ("fou:tcpmux", "ipsec:tcp"), the bare transport for GRE ("tcpmux"). Everything here is pure: the text of the scripts of ONE end.
Everything that goes into a script is checked first: the scripts run as root on a server.
"""
from __future__ import annotations

import ipaddress
import re

LINKS = ("gre", "fou", "gue", "ipip", "ipipfou", "ipipgue", "wg", "ipsec", "ipsecnat")
TRANSPORTS = ("tcpmux", "tcp", "wsmux", "wssmux")          # the Backhaul transports that run over a link
TITLE = {"gre": "GRE", "fou": "GRE over UDP (FOU)", "gue": "GRE over UDP (GUE)", "ipip": "IPIP", "ipipfou": "IPIP over UDP (FOU)", "ipipgue": "IPIP over UDP (GUE)", "wg": "WireGuard",
         "ipsec": "IPsec (ESP)", "ipsecnat": "IPsec (ESP in UDP, NAT-T)"}
PORTS = {"fou": (5500, 5599), "gue": (5600, 5699), "ipipfou": (5700, 5799), "ipipgue": (5800, 5899), "wg": (51820, 51919), "ipsecnat": (4500, 4599)}      # the UDP port range of a link: ONE port, the same on both servers
NEEDS = {"gre": "gre", "ipip": "ipip", "ipsec": "esp"}                         # a link that is not in a UDP port needs a raw IP protocol to pass: "gre" = 47, "ipip" = 4, "esp" = 50
ENCRYPTED = frozenset({"wg", "ipsec", "ipsecnat"})
ONE_PER_PAIR = frozenset({"ipip", "ipipfou", "ipipgue"})                       # IPIP has no key: the kernel holds ONE tunnel per pair of addresses
UDP_LINKS = tuple(k for k in LINKS if k in PORTS)
RAW_LINKS = tuple(k for k in LINKS if k not in PORTS)
OVERHEAD = {"gre": 24, "fou": 52, "gue": 56, "ipip": 20, "ipipfou": 48, "ipipgue": 52, "wg": 80, "ipsec": 76, "ipsecnat": 84}      # bytes added to every packet (the worst case)
KERNEL = {"gre": ("ip_gre",), "fou": ("ip_gre", "fou"), "gue": ("ip_gre", "fou"), "ipip": ("ipip",), "ipipfou": ("ipip", "fou"), "ipipgue": ("ipip", "fou"), "wg": ("wireguard",),
          "ipsec": ("xfrm_interface", "esp4"), "ipsecnat": ("xfrm_interface", "esp4")}      # the modules a link needs (fou carries GUE too)
_IPV4 = re.compile(r"(\d{1,3}\.){3}\d{1,3}")
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,14}")
_HEX20 = re.compile(r"[0-9a-f]{40}")


def split_kind(kind: str) -> tuple[str, str]:
    """A kind of the pair test -> (link, transport): "fou:tcpmux" -> ("fou", "tcpmux"), "tcpmux" -> ("gre", "tcpmux"). A kind of another engine ("bp:wss") is ("", "")."""
    head, sep, tail = str(kind).partition(":")

    if sep and head in LINKS and head != "gre" and tail in TRANSPORTS:
        return head, tail

    return ("gre", kind) if kind in TRANSPORTS else ("", "")


def make_kind(link: str, transport: str) -> str:
    return transport if link in ("", "gre") else f"{link}:{transport}"


def all_kinds(transport: str = "tcpmux") -> list[str]:
    """The kind of every link with one transport (GRE first)."""
    return [make_kind(k, transport) for k in LINKS]


def needs(link: str) -> str:
    """What must pass between the two servers for this link: "gre", "ipip", "esp" (raw IP protocols) or "udp"."""
    return NEEDS.get(link, "udp")


def is_udp(link: str) -> bool:
    return link in PORTS


# ------------------------------------------------------------------ the scripts of one end
_ENC = {                    # link -> (kernel device type, the `ip fou add` arguments or "", the encap arguments of `ip link add`, the IP protocol inside, whether it has a key)
    "fou": ("gre", "ipproto 47", ' encap fou encap-sport auto encap-dport "$PORT"', "gre", True),
    "gue": ("gre", "gue", ' encap gue encap-sport auto encap-dport "$PORT"', "gre", True),
    "ipip": ("ipip", "", "", "ipip", False),
    "ipipfou": ("ipip", "ipproto 4", ' encap fou encap-sport auto encap-dport "$PORT"', "ipip", False),
    "ipipgue": ("ipip", "gue", ' encap gue encap-sport auto encap-dport "$PORT"', "ipip", False),
}

_ENC_UP = """#!/bin/sh
# Nikatel @TITLE@ link @NAME@  (idempotent: also used by the health-check timer)
NAME=@NAME@
LOCAL=@LOCAL@
REMOTE=@REMOTE@
PORT=@PORT@
KEY=@KEY@
ADDR=@ADDR@
MTU=@MTU@
TTL=@TTL@
TAG="nikatel-gre-$NAME"

@MODPROBE@
@FOU_ADD@
@FIREWALL@
if ! ip link show "$NAME" >/dev/null 2>&1; then
  ip link add "$NAME" type @TYPE@ local "$LOCAL" remote "$REMOTE" @KEYARG@ttl "$TTL"@ENCAP@ || exit 1
fi
ip link set dev "$NAME" alias "nikatel:$NAME" 2>/dev/null || true
ip link set "$NAME" mtu "$MTU"
ip addr show dev "$NAME" | grep -q "inet ${ADDR%/*}/" || ip addr add "$ADDR" dev "$NAME"
ip link set "$NAME" up
@SYSCTL@"""
_ENC_DOWN = """#!/bin/sh
NAME=@NAME@
PORT=@PORT@
TAG="nikatel-gre-$NAME"
ip link set "$NAME" down 2>/dev/null || true
if ip -d link show "$NAME" 2>/dev/null | grep -q '@GREP@'; then ip link del "$NAME" 2>/dev/null || true; fi
@FOU_DEL@
@FIREWALL@exit 0
"""
_IPSEC_UP = """#!/bin/sh
# Nikatel @TITLE@ link @NAME@  (idempotent: also used by the health-check timer). ESP in tunnel mode on an xfrm interface, one SA per direction, the keys in a file of 0600 (made by the panel) and given to `ip` through
# its standard input - never in a command line; 64-bit sequence numbers (ESN): the SAs never run out of numbers.
NAME=@NAME@
LOCAL=@LOCAL@
REMOTE=@REMOTE@
PORT=@PORT@
ADDR=@ADDR@
MTU=@MTU@
IFID=@IFID@
SPI_TX=@SPI_TX@
SPI_RX=@SPI_RX@
KEYS=/etc/nikatel/ipsec/@NAME@.keys
TAG="nikatel-gre-$NAME"

modprobe xfrm_interface 2>/dev/null || true
modprobe esp4 2>/dev/null || true
[ -s "$KEYS" ] || { logger -t nikatel-ipsec "$NAME: the key file $KEYS is missing"; exit 1; }
. "$KEYS"
@FIREWALL@
DEV=$(ip -o route get "$REMOTE" 2>/dev/null | sed -E 's/.* dev ([^ ]+).*/\\1/')
[ -n "$DEV" ] || { logger -t nikatel-ipsec "$NAME: no route to $REMOTE"; exit 1; }
@NATT_SOCKET@
if ! ip link show "$NAME" >/dev/null 2>&1; then
  ip link add "$NAME" type xfrm dev "$DEV" if_id "$IFID" || exit 1
fi
ip link set dev "$NAME" alias "nikatel:$NAME" 2>/dev/null || true
ip xfrm state del src "$LOCAL" dst "$REMOTE" proto esp spi "0x$SPI_TX" 2>/dev/null || true
ip xfrm state del src "$REMOTE" dst "$LOCAL" proto esp spi "0x$SPI_RX" 2>/dev/null || true
ip xfrm policy del src 0.0.0.0/0 dst 0.0.0.0/0 dir out if_id "$IFID" 2>/dev/null || true
ip xfrm policy del src 0.0.0.0/0 dst 0.0.0.0/0 dir in if_id "$IFID" 2>/dev/null || true
ip xfrm policy del src 0.0.0.0/0 dst 0.0.0.0/0 dir fwd if_id "$IFID" 2>/dev/null || true
ip -batch - <<EOF || exit 1
xfrm state add src $LOCAL dst $REMOTE proto esp spi 0x$SPI_TX reqid $IFID mode tunnel aead rfc4106(gcm(aes)) 0x$KEY_TX 128 if_id $IFID@ENCAP@ replay-window 128 flag esn
xfrm state add src $REMOTE dst $LOCAL proto esp spi 0x$SPI_RX reqid $IFID mode tunnel aead rfc4106(gcm(aes)) 0x$KEY_RX 128 if_id $IFID@ENCAP@ replay-window 128 flag esn
xfrm policy add src 0.0.0.0/0 dst 0.0.0.0/0 dir out if_id $IFID tmpl src $LOCAL dst $REMOTE proto esp reqid $IFID mode tunnel
xfrm policy add src 0.0.0.0/0 dst 0.0.0.0/0 dir in if_id $IFID tmpl src $REMOTE dst $LOCAL proto esp reqid $IFID mode tunnel
xfrm policy add src 0.0.0.0/0 dst 0.0.0.0/0 dir fwd if_id $IFID tmpl src $REMOTE dst $LOCAL proto esp reqid $IFID mode tunnel
EOF
ip addr show dev "$NAME" | grep -q "inet ${ADDR%/*}/" || ip addr add "$ADDR" dev "$NAME"
ip link set "$NAME" mtu "$MTU" up
@SYSCTL@"""
_IPSEC_DOWN = """#!/bin/sh
NAME=@NAME@
LOCAL=@LOCAL@
REMOTE=@REMOTE@
IFID=@IFID@
SPI_TX=@SPI_TX@
SPI_RX=@SPI_RX@
TAG="nikatel-gre-$NAME"
ip link set "$NAME" down 2>/dev/null || true
ip xfrm policy del src 0.0.0.0/0 dst 0.0.0.0/0 dir out if_id "$IFID" 2>/dev/null || true
ip xfrm policy del src 0.0.0.0/0 dst 0.0.0.0/0 dir in if_id "$IFID" 2>/dev/null || true
ip xfrm policy del src 0.0.0.0/0 dst 0.0.0.0/0 dir fwd if_id "$IFID" 2>/dev/null || true
ip xfrm state del src "$LOCAL" dst "$REMOTE" proto esp spi "0x$SPI_TX" 2>/dev/null || true
ip xfrm state del src "$REMOTE" dst "$LOCAL" proto esp spi "0x$SPI_RX" 2>/dev/null || true
if ip -d link show "$NAME" 2>/dev/null | grep -q 'xfrm'; then ip link del "$NAME" 2>/dev/null || true; fi
@NATT_STOP@
@FIREWALL@exit 0
"""
# the socket that makes the kernel take ESP out of UDP (NAT-T): a UDP socket with UDP_ENCAP = ESPINUDP bound to the port; a transient systemd unit holds it, the down script stops it
_NATT_SOCKET = """systemctl is-active --quiet "nikatel-ipsec-sock-$NAME.service" || systemd-run --quiet --unit="nikatel-ipsec-sock-$NAME" --description="Nikatel ESP-in-UDP socket $NAME" \\
  python3 -c 'import socket, sys, time; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.IPPROTO_UDP, 100, 2); s.bind((sys.argv[1], int(sys.argv[2]))); time.sleep(10 ** 9)' "$LOCAL" "$PORT" || exit 1"""
_NATT_STOP = """systemctl stop "nikatel-ipsec-sock-$NAME.service" 2>/dev/null || true"""


def peer_of(addr_cidr: str) -> str:
    iface = ipaddress.ip_interface(addr_cidr)
    return next(str(h) for h in iface.network.hosts() if str(h) != str(iface.ip))


def check(link: str, name: str, local: str, remote: str, port: int, mtu: int) -> None:
    if link not in LINKS or link in ("gre", "wg"):
        raise ValueError("this link has its own scripts")

    if not _NAME.fullmatch(name) or not _IPV4.fullmatch(local) or not _IPV4.fullmatch(remote) or not 576 <= int(mtu) <= 1500:
        raise ValueError("a link needs a tunnel name, two IPv4 addresses and an MTU")

    if link in PORTS:
        lo, hi = PORTS[link]

        if not lo <= int(port) <= hi:
            raise ValueError(f"the UDP port of a {TITLE[link]} link is {lo}-{hi}")


def firewall_match(link: str, port: int) -> str:
    """The iptables match of what the link sends between the two servers (the rules that let it in and out name the exact peer)."""
    if link in PORTS:
        return f"-p udp --dport {int(port)}"

    return {"ipip": "-p 4", "ipsec": "-p 50"}[link]


def enc_files(link: str, name: str, local: str, remote: str, key: int, addr_cidr: str, mtu: int, port: int, ttl: int, fw_up: str, fw_down: str, sysctl: str) -> dict[str, str]:
    """The up and down script of a GRE / IPIP link (native, FOU or GUE) - {path: text}."""
    check(link, name, local, remote, port, mtu)
    dev, fou_args, encap, _proto, keyed = _ENC[link]
    modules = "\n".join(f"modprobe {m} 2>/dev/null || true" for m in KERNEL[link])
    up = _ENC_UP.replace("@TITLE@", TITLE[link]).replace("@MODPROBE@", modules)
    up = up.replace("@FOU_ADD@", f'ip fou show 2>/dev/null | grep -q "port $PORT " || ip fou add port "$PORT" {fou_args} || exit 1' if fou_args else "").replace("@TYPE@", dev)
    up = up.replace("@KEYARG@", 'key "$KEY" ' if keyed else "").replace("@ENCAP@", encap)
    down = _ENC_DOWN.replace("@GREP@", "gre " if dev == "gre" else "ipip").replace("@FOU_DEL@", 'ip fou del port "$PORT" 2>/dev/null || true' if fou_args else "")
    put = {"@NAME@": name, "@LOCAL@": local, "@REMOTE@": remote, "@PORT@": str(int(port)), "@KEY@": str(int(key)), "@ADDR@": addr_cidr, "@MTU@": str(int(mtu)), "@TTL@": str(int(ttl))}

    def fill(text: str, firewall: str, with_sysctl: bool) -> str:
        text = text.replace("@FIREWALL@", firewall).replace("@SYSCTL@", sysctl if with_sysctl else "")

        for k, v in put.items():
            text = text.replace(k, v)

        return text

    return {f"/usr/local/sbin/nikatel-gre-up-{name}": fill(up, fw_up, True), f"/usr/local/sbin/nikatel-gre-down-{name}": fill(down, fw_down, False)}


def ipsec_files(link: str, name: str, local: str, remote: str, ifid: int, addr_cidr: str, mtu: int, port: int, spi_tx: int, spi_rx: int, key_tx: str, key_rx: str,
                fw_up: str, fw_down: str, sysctl: str) -> dict[str, str]:
    """The up and down script and the key file of an IPsec end (ESP, or ESP in UDP). `key_tx` / `key_rx`: 40 hex characters each (a 128-bit AES-GCM key and its 4-byte salt); the other end gets them swapped."""
    check(link, name, local, remote, port, mtu)

    if not _HEX20.fullmatch(key_tx or "") or not _HEX20.fullmatch(key_rx or "") or not 256 <= int(spi_tx) < 2**32 or not 256 <= int(spi_rx) < 2**32 or not 1 <= int(ifid) < 2**31:
        raise ValueError("an IPsec link needs two keys of 40 hex characters, two SPIs and an interface id")

    natt = link == "ipsecnat"
    up = _IPSEC_UP.replace("@TITLE@", TITLE[link]).replace("@ENCAP@", " encap espinudp $PORT $PORT 0.0.0.0" if natt else "").replace("@NATT_SOCKET@", _NATT_SOCKET if natt else "")
    down = _IPSEC_DOWN.replace("@NATT_STOP@", _NATT_STOP if natt else "")
    put = {"@NAME@": name, "@LOCAL@": local, "@REMOTE@": remote, "@PORT@": str(int(port) if natt else 0), "@ADDR@": addr_cidr, "@MTU@": str(int(mtu)), "@IFID@": str(int(ifid)), "@SPI_TX@": f"{int(spi_tx):08x}", "@SPI_RX@": f"{int(spi_rx):08x}"}

    def fill(text: str, firewall: str, with_sysctl: bool) -> str:
        text = text.replace("@FIREWALL@", firewall).replace("@SYSCTL@", sysctl if with_sysctl else "")

        for k, v in put.items():
            text = text.replace(k, v)

        return text

    keys = f"KEY_TX={key_tx}\nKEY_RX={key_rx}\n"
    return {f"/usr/local/sbin/nikatel-gre-up-{name}": fill(up, fw_up, True), f"/usr/local/sbin/nikatel-gre-down-{name}": fill(down, fw_down, False), f"/etc/nikatel/ipsec/{name}.keys": keys}
