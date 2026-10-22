"""Where the panel may be sent. A link typed by the operator (a subscription, for now) is downloaded by the panel itself, from the machine it runs on: an address that is this machine
(127.0.0.1, ::1), a link-local address (169.254.x.x - the metadata service of a cloud server) or an address nobody owns is never a subscription, and `GET` toward one is how an
outsider with a token that may "start work" would look around inside the host (the answer says "HTTP 404" or "could not download": an open port and a closed one differ).
Private ranges (10.x, 192.168.x) stay allowed: a panel of the owner can sit in one."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


def bad_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Pure: is this the machine itself, a link-local, an unspecified, a multicast or a reserved address?"""
    mapped = getattr(ip, "ipv4_mapped", None)

    if mapped is not None:          # ::ffff:127.0.0.1 is 127.0.0.1
        ip = mapped

    return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved


def check_public_url(url: str) -> None:
    """Raise ValueError when the host of `url` is, or resolves to, an address a download must not be sent to. A name that does not resolve passes: the download itself says so."""
    host = urlsplit(url).hostname or ""

    if not host:
        raise ValueError("the link has no host")

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return

    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError:
            continue

        if bad_address(ip):
            raise ValueError("this link points at the machine the panel runs on (or an internal address): a subscription link must be a public address")
