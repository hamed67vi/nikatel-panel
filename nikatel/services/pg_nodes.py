"""PasarGuard: the nodes of a PasarGuard panel, joined with the servers of this panel (roadmap #94). Written against the REST API of the panel through services/panels.py (panels.call).

WHY one list. A PasarGuard node IS one of our foreign servers: the Install view puts the node on it over SSH. The PasarGuard panel knows the node by a name and an
address, this panel knows the server by a name, a host and its samples. Showing them apart makes the operator match them by eye, so every node row here carries the
server it runs on (matched by address, see `match_servers`), and the foreign servers that are not a node yet are listed beside them with the one action that makes them one.
Since 1.48.0 the page is ONE list as well: the Servers page lists the servers, the node of each (`placement`: from the health job's copy of every panel's nodes, no call to a
panel) is part of its row, and the window of the server has the Node view that `overview` / `node_get` and the actions below serve. The nodes that run on no server of ours are
`placement`'s second answer. Reading the list of a panel (`overview`) also refreshes that copy (`keep_cache`).

What the real panel offers (PasarGuard 5.4, every route read or probed on 2026-10-14 - a GET on a route that takes only POST / PUT / DELETE answers 405 when it exists):
  GET /api/nodes, /api/node/{id}, /api/node/settings, /api/nodes/realtime_stats, /api/node/usage, /api/cores/simple and GET /api/node/{id}/logs (server-sent events);
  POST /api/node (add), PUT /api/node/{id} (edit), DELETE /api/node/{id}; POST /api/node/{id}/reconnect | reset | update | core_update | geofiles; PUT /api/node/{id}/sync;
  POST /api/nodes/bulk/enable | disable.
What its source does that matters for the customers (app/operation/node.py, app/db/crud/node.py, app/models/node.py):
  * saving a node (PUT), even only its usage coefficient, makes the panel START the core on an active node again: its users drop for a moment and reconnect;
  * the body of PUT is a model whose `api_port` has a default (62051) that is NOT optional: a PUT without it writes 62051. Every edit here sends the current api_port;
  * a delete removes the node's usage history with it, and an enable / disable is a status change that the bulk routes do properly;
  * GET /api/node/settings gives only `min_node_version`: the certificate the panel needs is the one the NODE makes (its own self-signed certificate), so it is read
    from the server over SSH (extras.pasarguard_cert) - there is no panel certificate to fetch.
Secrets. The API key of a node, its certificate and its proxy address are in the panel's answers. They stay on this server: the rows say only "set / not set". The
backups are files in the data folder (mode 0600) and are never returned.

The unified flow ("make this server a node"): install the node on the server with extras.start (a long action of its own), read the certificate the node made, add the
node to the panel, wait until it connects. The API key is not asked for and not kept: it is derived from this panel's secret (`node_key`), so any step can be done again
later and gives the same key."""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import socket
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..models import ActionLog, Panel, Server, ServerSample
from ..ssh import SSHError
from . import autopilot, extras, panels
from .events import emit

Resolver = Callable[[list[str]], dict[str, list[str]]]

NODE_PORT = 62050                    # the ports the official installer uses when it is not told otherwise
API_PORT = 62051
CONNECTIONS = ("grpc", "rest")
RESETS = ("no_reset", "day", "week", "month", "year")
PERIODS = ("hour", "day", "month")   # the panel also has "minute": too fine for a table
REGIONS = ("iran", "china", "russia")
MAINTENANCE = ("node", "core", "geofiles")
KEEP_BACKUPS = 20
POLL = 3.0                           # seconds between two looks at a running install / a node that connects
INSTALL_LIMIT = 1500.0               # extras waits 1200 s for the install itself
CONNECT_LIMIT = 120.0
DNS_TTL = 600.0
_CORE_VERSION = re.compile(r"^(latest|v?\d+\.\d+\.\d+)$")
_DOMAIN = re.compile(r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,14}$")      # the panel's own rule for an address that is not an IP
_NAME = re.compile(r"^[\w .\-]{1,40}$")
_CERT = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.DOTALL)
_KEY = re.compile(r"-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----")
_BACKUP = re.compile(r"^nodes-p(\d+)-(\d{9,12})-n(\d+)-(edit|delete|cert|restore)\.json$")
_PUT_KEEP = ("name", "address", "port", "api_port", "usage_coefficient", "connection_type", "keep_alive", "core_config_id", "data_limit",
             "data_limit_reset_strategy", "reset_time", "default_timeout", "internal_timeout")      # sent back on every PUT, see the note about api_port above
_DNS: dict[str, tuple[float, list[str]]] = {}


class ServerSilent(ValueError):
    """The server did not answer over SSH (as opposed to: it answered, and the node is not there)."""


# ------------------------------------------------------------------ pure: small things
def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _num(v, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default

    return f if math.isfinite(f) else default


def _text(v, n: int) -> str:
    return str(v if v is not None else "")[:n]


def _pct(v) -> float | None:
    f = _num(v, -1.0)
    return round(f, 1) if f >= 0 else None


def norm(addr) -> str:
    """An address or host as it is compared: trimmed, lower case, no trailing dot, no brackets around an IPv6 address."""
    a = str(addr or "").strip().lower().rstrip(".")

    if a.startswith("[") and a.endswith("]"):
        a = a[1:-1]

    return a


def ip_of(addr) -> str:
    """The canonical text of an IP address ('2001:0db8::1' and '2001:db8::1' are one), '' when `addr` is not an IP address."""
    try:
        return ipaddress.ip_address(norm(addr)).compressed
    except ValueError:
        return ""


def check_address(addr) -> str:
    """The panel's rule for a node address: an IPv4 / IPv6 address, localhost or a domain name. Refused here with a sentence instead of as a 422 from the panel."""
    a = norm(addr)

    if not a:
        raise ValueError("the node needs an address (an IP address or a domain name)")

    if len(a) > 253 or not (ip_of(a) or a == "localhost" or _DOMAIN.match(a)):
        raise ValueError("the address must be an IPv4 or IPv6 address or a domain name")

    return a


def check_name(name) -> str:
    n = str(name or "").strip()

    if not _NAME.match(n):
        raise ValueError("the node name has 1 to 40 letters, digits, spaces or - _ .")

    return n


def check_key(key) -> str:
    """A node API key is a UUID (the panel refuses anything else)."""
    try:
        return str(uuid.UUID(str(key).strip()))
    except (ValueError, AttributeError):
        raise ValueError("the API key must be a UUID (like 123e4567-e89b-12d3-a456-426614174000)")


def node_key(panel_id: int, server_id: int) -> str:
    """The API key of the node that this panel installs on a server: derived from the secret of this panel, so it is never asked for, shown or stored, and the same
    on every try. It looks like a version 4 UUID, which is what the node's installer makes for itself."""
    mac = hmac.new(get_settings().secret_key(), f"pg-node-api-key:{int(panel_id)}:{int(server_id)}".encode(), hashlib.sha256).digest()
    return str(uuid.UUID(bytes=mac[:16], version=4))


def _port(v, what: str) -> int:
    p = _int(v, -1)

    if not 1 <= p <= 65535:
        raise ValueError(f"{what} must be a number from 1 to 65535")

    return p


def _one_of(v, allowed: tuple[str, ...], what: str) -> str:
    s = str(v or "").strip().lower()

    if s not in allowed:
        raise ValueError(f"{what} must be one of: {', '.join(allowed)}")

    return s


def check_field(key: str, v):
    """One field of a node, checked the way the panel checks it."""
    if key == "name":
        return check_name(v)

    if key == "address":
        return check_address(v)

    if key == "port":
        return _port(v, "the port")

    if key == "api_port":
        return _port(v, "the API port")

    if key == "usage_coefficient":
        f = _num(v, -1.0)

        if not 0 <= f <= 1000:
            raise ValueError("the usage coefficient must be a number from 0 to 1000 (1 = traffic counted as it is)")

        return f

    if key == "connection_type":
        return _one_of(v, CONNECTIONS, "the connection type")

    if key == "keep_alive":
        n = _int(v, -1)

        if not 0 <= n <= 86400:
            raise ValueError("keep-alive must be a number of seconds from 0 to 86400")

        return n

    if key == "core_config_id":
        n = _int(v, 0)

        if n < 1:
            raise ValueError("choose a core config")

        return n

    if key == "data_limit":
        n = _int(v, -1)

        if n < 0:
            raise ValueError("the data limit cannot be negative (0 = none)")

        return n

    if key == "data_limit_reset_strategy":
        return _one_of(v, RESETS, "the reset period")

    if key == "reset_time":
        n = _int(v, -2)

        if n < -1:
            raise ValueError("reset_time is -1 (the default) or a number of seconds")

        return n

    if key == "default_timeout":
        n = _int(v, 0)

        if not 3 <= n <= 600:
            raise ValueError("the default timeout must be from 3 to 600 seconds")

        return n

    if key == "internal_timeout":
        n = _int(v, 0)

        if not 3 <= n <= 60:
            raise ValueError("the internal timeout must be from 3 to 60 seconds")

        return n

    raise ValueError(f"cannot set {key}")


# ------------------------------------------------------------------ pure: certificates
def _not_after(cert: x509.Certificate) -> datetime:
    v = getattr(cert, "not_valid_after_utc", None)
    return v if v is not None else cert.not_valid_after.replace(tzinfo=timezone.utc)


def _san(cert: x509.Certificate) -> list[str]:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []

    return [str(v) for v in ext.get_values_for_type(x509.DNSName)] + [str(v) for v in ext.get_values_for_type(x509.IPAddress)]


def _cert_info(cert: x509.Certificate, now: float | None = None) -> dict:
    end = _not_after(cert)
    left = (end.timestamp() - (now if now is not None else time.time())) / 86400
    return {"subject": cert.subject.rfc4514_string()[:120], "not_after": int(end.timestamp()), "days_left": math.floor(left),
            "fingerprint": cert.fingerprint(hashes.SHA256()).hex(), "san": _san(cert)[:12]}


def read_cert(pem) -> tuple[str, dict]:
    """(the certificate as the panel wants it, what it says about itself) - or a ValueError that says what is wrong. Mirrors the panel: PEM, no private key, at most 2048 characters."""
    text = str(pem or "").strip()

    if _KEY.search(text):
        raise ValueError("that text holds a private key: only the public certificate may be given")

    m = _CERT.search(text)

    if not m:
        raise ValueError("paste the whole certificate (from -----BEGIN CERTIFICATE----- to -----END CERTIFICATE-----)")

    block = m.group(0)

    if len(block) > 2048:
        raise ValueError("the panel accepts a certificate of at most 2048 characters")

    try:
        cert = x509.load_pem_x509_certificate(block.encode())
    except ValueError:
        raise ValueError("this is not a valid certificate")

    return block, _cert_info(cert)


def cert_summary(pem) -> dict | None:
    """What a stored certificate says about itself, None when there is none or it cannot be read. Never the text itself."""
    try:
        return read_cert(pem)[1]
    except ValueError:
        return None


def same_cert(a, b) -> bool:
    """Two certificates are the same when their SHA-256 fingerprints are (text that cannot be read is compared as normalised text)."""
    ia, ib = cert_summary(a), cert_summary(b)

    if ia and ib:
        return ia["fingerprint"] == ib["fingerprint"]

    return bool(str(a or "").strip()) and "".join(str(a).split()) == "".join(str(b or "").split())


# ------------------------------------------------------------------ pure: rows
def public_node(n) -> dict:
    """A node of the panel as the page sees it. The API key, the certificate and the proxy address become 'set / not set'."""
    n = n if isinstance(n, dict) else {}
    nid = n.get("id")
    return {"id": nid if isinstance(nid, int) and not isinstance(nid, bool) else None, "name": _text(n.get("name"), 80), "address": _text(n.get("address"), 253),
            "port": _int(n.get("port")), "api_port": _int(n.get("api_port")), "status": _text(n.get("status"), 20).lower(), "message": _text(n.get("message"), 300),
            "xray": _text(n.get("xray_version") or n.get("core_version"), 24), "version": _text(n.get("node_version"), 24),
            "coefficient": _num(n.get("usage_coefficient"), 1.0), "connection_type": _text(n.get("connection_type"), 8), "keep_alive": _int(n.get("keep_alive")),
            "core_config_id": n.get("core_config_id") if isinstance(n.get("core_config_id"), int) else None, "data_limit": _int(n.get("data_limit")),
            "reset_strategy": _text(n.get("data_limit_reset_strategy") or "no_reset", 12), "reset_time": _int(n.get("reset_time"), -1),
            "default_timeout": _int(n.get("default_timeout")), "internal_timeout": _int(n.get("internal_timeout")),
            "uplink": _int(n.get("uplink")), "downlink": _int(n.get("downlink")), "lifetime_uplink": _int(n.get("lifetime_uplink")), "lifetime_downlink": _int(n.get("lifetime_downlink")),
            "has_key": bool(n.get("api_key")), "has_ca": bool(n.get("server_ca")), "has_proxy": bool(n.get("proxy_url"))}


def live_row(raw) -> dict | None:
    """The numbers a node reports about itself (/api/nodes/realtime_stats): CPU and memory in percent, the speed in bytes per second, how long it runs. None when it reports nothing."""
    if not isinstance(raw, dict):
        return None

    total, used = _num(raw.get("mem_total")), raw.get("mem_used")
    return {"cpu": _pct(raw.get("cpu_usage")), "ram": round(_num(used) * 100.0 / total, 1) if total > 0 and used is not None else None, "mem_total": _int(total),
            "cores": _int(raw.get("cpu_cores")), "rx": _int(raw.get("incoming_bandwidth_speed")), "tx": _int(raw.get("outgoing_bandwidth_speed")), "uptime": _int(raw.get("uptime"))}


def server_row(s: dict, sample: dict | None = None) -> dict:
    """One of our servers with its newest sample."""
    sm = sample or {}
    row = {k: s.get(k) for k in ("id", "name", "host", "role", "enabled", "online", "last_seen_at", "provider", "country", "last_error")}
    row.update({"cpu": _pct(sm.get("cpu")), "ram": _pct(sm.get("ram")), "disk": _pct(sm.get("disk")), "load1": sm.get("load1"), "rx_mbps": sm.get("rx_mbps"),
                "tx_mbps": sm.get("tx_mbps"), "conn": sm.get("conn"), "sample_ts": sm.get("ts")})
    return row


def names_to_resolve(nodes: list[dict], servers: list[dict]) -> list[str]:
    """Pure: the host names that must be looked up in the DNS to match nodes and servers: node addresses that are names and equal no server host, and server hosts that are names."""
    hosts = {norm(s.get("host")) for s in servers}
    names = set()

    for n in nodes:
        a = norm(n.get("address"))

        if a and not ip_of(a) and a not in hosts:
            names.add(a)

    for h in hosts:
        if h and not ip_of(h):
            names.add(h)

    return sorted(names)[:60]


def match_servers(nodes: list[dict], servers: list[dict], resolved: dict[str, list[str]] | None = None) -> tuple[list[dict], list[dict]]:
    """Pure. Which server of this panel does each node run on? A node and a server are one when the node's address equals the server's host (an IP in any spelling, or
    the same name), or when one of them is a name and the DNS says it points at the other (`resolved`: name -> IP addresses, looked up by the caller). An exact match wins
    over a DNS match, the lowest server id wins a tie, and several nodes may sit on one server (different ports). Returns (one {server, how, hint} per node in the order
    of `nodes`, the servers no node runs on). `how` is "address", "dns" or "". `hint` is a server with the same NAME as an unmatched node: only a hint, never a match."""
    res = {norm(k): sorted({ip_of(x) for x in v if ip_of(x)}) for k, v in (resolved or {}).items()}
    by_host: dict[str, dict] = {}
    by_ip: dict[str, dict] = {}

    for s in sorted(servers, key=lambda x: x["id"]):
        h = norm(s.get("host"))

        if not h:
            continue

        by_host.setdefault(h, s)

        for ip in ([ip_of(h)] if ip_of(h) else res.get(h, [])):
            by_ip.setdefault(ip, s)

    pairs: list[dict] = []
    used: set[int] = set()

    for n in nodes:
        a = norm(n.get("address"))
        s = by_host.get(a) if a else None
        how = "address" if s is not None else ""

        if s is None and a:
            s = next((by_ip[ip] for ip in ([ip_of(a)] if ip_of(a) else res.get(a, [])) if ip in by_ip), None)

            if s is not None:
                both_ips = bool(ip_of(a)) and bool(ip_of(s.get("host")))
                how = "address" if both_ips else "dns"

        if s is not None:
            used.add(s["id"])

        pairs.append({"server": s, "how": how, "hint": None})

    free = [s for s in servers if s["id"] not in used]

    for n, p in zip(nodes, pairs):
        if p["server"] is None:
            p["hint"] = next((s for s in free if str(s.get("name", "")).casefold() == str(n.get("name", "")).casefold()), None)

    return pairs, free


def others_on(others: list[dict], servers: list[dict], resolved: dict[str, list[str]] | None = None) -> dict[int, list[dict]]:
    """Pure. The nodes the OTHER panels of this installation hold (rows with panel, type, name, address, status), put on the servers they run on: {server id: [{panel, type, node,
    status}]}. A server that carries one of them is not free for a new node: that node listens on the ports a new one would use."""
    pairs, _free = match_servers(others, servers, resolved)
    out: dict[int, list[dict]] = {}

    for o, p in zip(others, pairs):
        if p["server"] is not None:
            out.setdefault(p["server"]["id"], []).append({"panel": o.get("panel", ""), "type": o.get("type", ""), "node": o.get("name", ""), "status": o.get("status", "")})

    return out


# ------------------------------------------------------------------ pure: bodies for the panel
def create_body(f: dict) -> dict:
    """The POST body of a new node from the fields given (name, address, core_config_id, api_key and server_ca are needed; the rest has the panel's defaults). Every
    field is checked the way the panel checks it, so a mistake comes back as a sentence."""
    for need in ("name", "address", "core_config_id", "api_key", "server_ca"):
        if f.get(need) in (None, ""):
            raise ValueError(f"{need.replace('_', ' ')} is needed")

    return {"name": check_field("name", f["name"]), "address": check_field("address", f["address"]), "port": check_field("port", f.get("port", NODE_PORT)),
            "api_port": check_field("api_port", f.get("api_port", API_PORT)), "usage_coefficient": check_field("usage_coefficient", f.get("usage_coefficient", 1.0)),
            "connection_type": check_field("connection_type", f.get("connection_type", "grpc")), "keep_alive": check_field("keep_alive", f.get("keep_alive", 60)),
            "core_config_id": check_field("core_config_id", f["core_config_id"]), "data_limit": check_field("data_limit", f.get("data_limit", 0)),
            "data_limit_reset_strategy": check_field("data_limit_reset_strategy", f.get("data_limit_reset_strategy", "no_reset")), "reset_time": check_field("reset_time", f.get("reset_time", -1)),
            "default_timeout": check_field("default_timeout", f.get("default_timeout", 10)), "internal_timeout": check_field("internal_timeout", f.get("internal_timeout", 15)),
            "api_key": check_key(f["api_key"]), "server_ca": read_cert(f["server_ca"])[0]}


def edit_body(current: dict, changes: dict) -> tuple[dict, list[str]]:
    """The PUT body for `changes` on a node as the panel has it now, and the names of the fields that really change. Every editable field of the node is sent back (the
    panel would write its default over a missing api_port). The API key and the certificate are sent only when they differ. Nothing to change is a ValueError: a PUT
    that changes nothing still makes the panel restart the core on the node."""
    body = {k: current[k] for k in _PUT_KEEP if current.get(k) is not None}
    changed: list[str] = []

    for k, v in changes.items():
        if v is None:
            continue

        if k == "api_key":
            new = check_key(v)

            if new != str(current.get("api_key") or "").strip().lower():
                body["api_key"] = new
                changed.append(k)
        elif k == "server_ca":
            pem = read_cert(v)[0]

            if not same_cert(current.get("server_ca"), pem):
                body["server_ca"] = pem
                changed.append(k)
        elif k in _PUT_KEEP:
            new = check_field(k, v)

            if body.get(k) != new:
                body[k] = new
                changed.append(k)
        else:
            raise ValueError(f"cannot change {k}")

    if not changed:
        raise ValueError("nothing to change")

    return body, changed


def snapshot_body(raw: dict, create: bool) -> dict:
    """A node saved in a backup as the body that puts it back: PUT (an edit undone) or POST (a deleted node made again, with its key, certificate and proxy)."""
    body = {k: raw[k] for k in _PUT_KEEP if raw.get(k) is not None}

    for k in ("api_key", "server_ca"):
        if raw.get(k):
            body[k] = raw[k]

    if create and raw.get("proxy_url"):
        body["proxy_url"] = raw["proxy_url"]

    return body


def usage_rows(answer, node_id: int) -> list[dict]:
    """Pure: the rows of /api/node/usage for one node: [{t, up, down}] (the answer is {stats: {"<node id>": [{period_start, uplink, downlink}]}})."""
    stats = answer.get("stats") if isinstance(answer, dict) else None
    rows = None

    if isinstance(stats, dict):
        rows = stats.get(str(node_id))

        if rows is None and len(stats) == 1:
            rows = next(iter(stats.values()))
    elif isinstance(stats, list):
        rows = stats

    return [{"t": _text(r.get("period_start"), 25), "up": _int(r.get("uplink")), "down": _int(r.get("downlink"))} for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def parse_sse(lines: Iterable[str]) -> Iterator[str]:
    """Pure: the text of the `data:` lines of a server-sent-events stream (comments, other fields and blank lines are skipped)."""
    for ln in lines:
        if ln.startswith("data:"):
            yield ln[5:].removeprefix(" ")


# ------------------------------------------------------------------ the DNS and the database
def _lookup(name: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
    except OSError:
        return []

    return sorted({ip_of(i[4][0]) for i in infos if ip_of(i[4][0])})


def resolve_names(names: list[str], timeout: float = 3.0) -> dict[str, list[str]]:
    """Host name -> IP addresses, for the names the matching needs. A few at a time, at most `timeout` seconds in all, remembered for ten minutes (a name that did not answer, for one)."""
    now = time.time()
    out: dict[str, list[str]] = {}
    todo = []

    for n in names:
        hit = _DNS.get(n)

        if hit and hit[0] > now:
            out[n] = hit[1]
        else:
            todo.append(n)

    if todo:
        ex = ThreadPoolExecutor(max_workers=min(8, len(todo)))
        futs = {ex.submit(_lookup, n): n for n in todo}
        done, _late = wait(futs, timeout=timeout)
        ex.shutdown(wait=False, cancel_futures=True)       # a lookup that hangs must not hold the page

        for f, n in futs.items():
            ips = f.result() if f in done else []
            out[n] = ips
            _DNS[n] = (now + (DNS_TTL if ips else 60.0), ips)

    return out


def _servers() -> list[dict]:
    with session() as db:
        return [{"id": s.id, "name": s.name, "host": s.host, "role": s.role, "enabled": bool(s.enabled), "online": s.online, "last_seen_at": s.last_seen_at or 0,
                 "provider": s.provider or "", "country": s.country or "", "last_error": (s.last_error or "")[:200]}
                for s in db.scalars(select(Server).order_by(Server.role, Server.name))]


def _server_by_id(server_id: int) -> dict:
    s = next((x for x in _servers() if x["id"] == int(server_id)), None)

    if s is None:
        raise ValueError("server not found")

    return s


def _latest_samples(ids: Iterable[int]) -> dict[int, dict]:
    """The newest sample of each server: one row per server, only the columns the table shows (the samples table is never read in bulk)."""
    out: dict[int, dict] = {}

    with session() as db:
        for sid in ids:
            row = db.execute(select(ServerSample.ts, ServerSample.cpu, ServerSample.ram, ServerSample.disk, ServerSample.load1, ServerSample.rx_mbps, ServerSample.tx_mbps, ServerSample.conn)
                             .where(ServerSample.server_id == sid).order_by(ServerSample.ts.desc()).limit(1)).first()

            if row is not None:
                out[sid] = dict(row._mapping)

    return out


def _note(c: dict, text: str, level: str = "info") -> None:
    """One line in the event log for what was done to a node (who changed what is worth knowing later)."""
    with session() as db:
        p = db.get(Panel, int(c.get("id") or 0))
        emit(db, level, "panels", text, p.name if p else "")


# ------------------------------------------------------------------ reading the panel
def _nodes_raw(c: dict) -> list[dict]:
    r = panels.call(c, "GET", "/api/nodes")
    lst = r.get("nodes") if isinstance(r, dict) else r
    return [n for n in lst if isinstance(n, dict)] if isinstance(lst, list) else []


def _npath(node_id: int) -> str:
    return f"/api/node/{int(node_id)}"


def _node_raw(c: dict, node_id: int) -> dict:
    r = panels.call(c, "GET", _npath(node_id))

    if not isinstance(r, dict):
        raise panels.PanelError("the panel did not answer with a node")

    return r


def _live_all(c: dict, warnings: list[str]) -> dict[str, dict | None]:
    try:
        r = panels.call(c, "GET", "/api/nodes/realtime_stats")
    except (panels.PanelError, ValueError) as e:
        warnings.append(f"The numbers the nodes report about themselves could not be read ({e}).")
        return {}

    return {str(k): live_row(v) for k, v in r.items()} if isinstance(r, dict) else {}


def _min_version(c: dict, warnings: list[str]) -> str:
    try:
        r = panels.call(c, "GET", "/api/node/settings")
    except (panels.PanelError, ValueError) as e:
        warnings.append(f"The node settings of the panel could not be read ({e}).")
        return ""

    return _text(r.get("min_node_version"), 20) if isinstance(r, dict) else ""


def _cores(c: dict, warnings: list[str] | None = None) -> list[dict]:
    try:
        r = panels.call(c, "GET", "/api/cores/simple")
    except (panels.PanelError, ValueError) as e:
        if warnings is not None:
            warnings.append(f"The core configs of the panel could not be read ({e}).")

        return []

    lst = r.get("cores") if isinstance(r, dict) else r
    return [{"id": x["id"], "name": _text(x.get("name"), 60), "type": _text(x.get("type"), 12)} for x in (lst if isinstance(lst, list) else [])
            if isinstance(x, dict) and isinstance(x.get("id"), int)]


def _others(c: dict) -> list[dict]:
    """The nodes the OTHER panels of this installation hold, as their health job saw them last (no call to those panels): a server that carries one is not free for a new node.
    A foreign server that no node of this panel runs on is very often a node of the Marzban panel, and both kinds of node listen on 62050 and 62051."""
    with session() as db:
        rows = db.execute(select(Panel.id, Panel.name, Panel.type, Panel.nodes)).all()

    out = []

    for pid, name, typ, nodes in rows:
        if pid == int(c.get("id") or 0):
            continue

        for n in nodes if isinstance(nodes, list) else []:
            if isinstance(n, dict) and str(n.get("address") or "").strip():
                out.append({"panel": _text(name, 40), "type": _text(typ, 12), "name": _text(n.get("name"), 60), "address": _text(n.get("address"), 253), "status": _text(n.get("status"), 20).lower()})

    return out


def panel_nodes() -> list[dict]:
    """Every node of EVERY panel of this installation (Marzban and PasarGuard) as the health job of the panels saw it last - no call to any panel - one row each:
    {panel_id, panel, type, node_id, name, address, status, xray, message}. The Servers page puts them on the servers they run on."""
    with session() as db:
        rows = db.execute(select(Panel.id, Panel.name, Panel.type, Panel.nodes).order_by(Panel.id)).all()

    out = []

    for pid, name, typ, nodes in rows:
        for n in nodes if isinstance(nodes, list) else []:
            if isinstance(n, dict) and str(n.get("address") or "").strip():
                out.append({"panel_id": pid, "panel": _text(name, 40), "type": _text(typ, 12), "node_id": n.get("id") if isinstance(n.get("id"), int) else None, "name": _text(n.get("name"), 60),
                            "address": _text(n.get("address"), 253), "status": _text(n.get("status"), 20).lower(), "xray": _text(n.get("xray"), 24), "message": _text(n.get("message"), 120)})

    return out


def placement(resolver: Resolver | None = None) -> tuple[dict[int, list[dict]], list[dict]]:
    """Every node of every panel put where it runs, matched the way `match_servers` matches (the address of the node is the host of the server, an IP in any spelling, or a name the DNS says
    points at the other): ({server id: [the rows of the nodes on it, `match` = "address" or "dns"]}, [the rows of the nodes that run on NO server of ours, with `hint`: a server that has the
    same NAME but another address, or None]). A server that carries no node is not in the first. One server, one list: the Servers page shows the node with its server."""
    servers, nodes = _servers(), panel_nodes()
    names = names_to_resolve(nodes, servers)
    resolved = (resolver or resolve_names)(names) if names else {}
    pairs, _free = match_servers(nodes, servers, resolved)
    on: dict[int, list[dict]] = {}
    out: list[dict] = []

    for n, p in zip(nodes, pairs):
        if p["server"] is not None:
            on.setdefault(p["server"]["id"], []).append({**n, "match": p["how"]})
        else:
            h = p["hint"]
            out.append({**n, "match": "", "hint": {"id": h["id"], "name": h["name"]} if h else None})

    return on, out


def by_server(resolver: Resolver | None = None) -> dict[int, list[dict]]:
    """{server id: the nodes of the panels that run on it} (see `placement`). A server that carries no node is not in the answer."""
    return placement(resolver)[0]


def _match(rows: list[dict], resolver: Resolver | None = None, others: list[dict] | None = None) -> tuple[list[dict], list[dict], dict[int, list[dict]]]:
    """(one {server, how, hint} per row of nodes, the servers no node of THIS panel runs on, the nodes of the other panels by server id) - the plain server dicts, without samples."""
    servers, others = _servers(), others or []
    names = names_to_resolve(rows + others, servers)
    resolved = (resolver or resolve_names)(names) if names else {}
    pairs, free = match_servers(rows, servers, resolved)
    return pairs, free, others_on(others, servers, resolved)


def _join(rows: list[dict], resolver: Resolver | None = None, others: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """(one {server row, match, hint} per node row, the foreign servers that are not a node of this panel) - the servers with their newest sample and the nodes of other panels they carry.
    The servers that carry no other node come first: they are the ones a new node can go to."""
    pairs, free, elsewhere = _match(rows, resolver, others)
    free = [s for s in free if s["role"] == "foreign"]
    samples = _latest_samples({p["server"]["id"] for p in pairs if p["server"]} | {s["id"] for s in free})

    def row(s: dict) -> dict:
        r = server_row(s, samples.get(s["id"]))
        r["elsewhere"] = elsewhere.get(s["id"], [])
        return r

    out = []

    for p in pairs:
        s, h = p["server"], p["hint"]
        out.append({"server": row(s) if s else None, "match": p["how"], "hint": {"id": h["id"], "name": h["name"]} if h else None})

    return out, sorted((row(s) for s in free), key=lambda r: (bool(r["elsewhere"]), str(r["name"]).casefold()))


def keep_cache(c: dict, raw: list[dict]) -> None:
    """The nodes just read are also what the health job keeps for the Servers page (`Panel.nodes`: the chips of the server rows and the Node view start from it). A window that changes a node reads
    the list again at once, so the page shows the new state and not the one from two minutes ago. A node that is fine has no failed check; one that is not keeps the count it had."""
    nodes = panels.parse_nodes(raw)

    with session() as db:
        p = db.get(Panel, int(c.get("id") or 0))

        if p is None:
            return

        old = {str(n.get("id")): n for n in (p.nodes or []) if isinstance(n, dict)}
        p.nodes = [dict(n, fails=0 if n["status"] in panels.NODE_OK else int((old.get(str(n.get("id"))) or {}).get("fails", 0))) for n in nodes]


def overview(c: dict, resolver: Resolver | None = None) -> dict:
    """Every node of the panel in one answer, for the Node view of the Servers page: each node with the server of ours it runs on, the foreign servers that are not a node of it (and which of those
    are nodes of another panel), the minimum node version, the core configs. The list read is kept as the copy the Servers page shows (`keep_cache`)."""
    raw = _nodes_raw(c)
    keep_cache(c, raw)
    warnings: list[str] = []
    live = _live_all(c, warnings)
    rows = [public_node(n) for n in raw]
    joined, free = _join(rows, resolver, _others(c))

    for r, j in zip(rows, joined):
        r.update(j)
        r["live"] = live.get(str(r["id"]))

    return {"nodes": rows, "free": free, "min_node_version": _min_version(c, warnings), "cores": _cores(c, warnings), "warnings": warnings, "read_only": get_settings().read_only,
            "totals": {"nodes": len(rows), "connected": sum(1 for r in rows if r["status"] == "connected"), "with_server": sum(1 for r in rows if r["server"]), "free": len(free),
                       "candidates": sum(1 for s in free if not s["elsewhere"])}}


def node_get(c: dict, node_id: int, resolver: Resolver | None = None) -> dict:
    """One node in full (without its secrets), the server it runs on and the numbers it reports about itself."""
    row = public_node(_node_raw(c, node_id))
    joined, _free = _join([row], resolver)
    row.update(joined[0])

    try:
        row["live"] = live_row(panels.call(c, "GET", f"/api/node/{int(node_id)}/realtime_stats"))
    except (panels.PanelError, ValueError):
        row["live"] = None                      # a node that is not connected reports nothing: not an error of this call

    return row


# ------------------------------------------------------------------ logs and usage
def _sse_lines(c: dict, path: str, quiet: float = 0.8, limit: float = 4.0) -> Iterator[str]:
    """The raw lines of a server-sent-events stream of the panel, until it has been quiet for `quiet` seconds or `limit` seconds have passed. The node sends what it kept at once (read
    on 2026-10-14: 15 lines on a quiet node, 10,000 in two seconds on a busy one), then only news - so a busy node never goes quiet and `limit` ends it. panels.call cannot do this:
    it waits for a body that never ends."""
    for fresh in (False, True):
        headers = {**panels.auth_headers(panels._token(c, fresh=fresh)), "Accept": "text/event-stream", "User-Agent": "nikatel-panel"}      # a PasarGuard API key is X-Api-Key, a token is Bearer
        t0 = time.time()

        try:
            with httpx.stream("GET", c["url"] + path, headers=headers, timeout=httpx.Timeout(10.0, read=quiet), verify=bool(c.get("verify_tls", True))) as r:
                if r.status_code == 401 and not fresh:
                    continue                                       # the token ended: log in again once

                if r.status_code >= 400:
                    raise panels.PanelError(f"HTTP {r.status_code}", r.status_code)

                for ln in r.iter_lines():
                    yield ln

                    if time.time() - t0 > limit:
                        return

                return
        except httpx.ReadTimeout:
            return
        except httpx.HTTPError as e:
            raise panels.PanelError(panels.no_answer(e))


def node_logs(c: dict, node_id: int, lines: int = 150) -> dict:
    """The last lines of a node's log as the panel streams it (it sends what the node kept, then the news; this reads until it is quiet)."""
    n = min(500, max(10, int(lines)))
    tail: deque[str] = deque(maxlen=n)

    for ln in parse_sse(_sse_lines(c, f"{_npath(node_id)}/logs")):
        tail.append(ln[:600])

    return {"node_id": int(node_id), "lines": list(tail)}


def node_usage(c: dict, node_id: int, period: str = "day", days: int = 7) -> dict:
    """The traffic of one node per hour, day or month for the last `days` days (hours: at most 14 days), with the totals."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of: {', '.join(PERIODS)}")

    days = min(14 if period == "hour" else 366, max(1, int(days)))
    end = datetime.now(timezone.utc).replace(microsecond=0)
    q = httpx.QueryParams({"period": period, "node_id": int(node_id), "start": (end - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
    rows = usage_rows(panels.call(c, "GET", f"/api/node/usage?{q}"), node_id)
    return {"node_id": int(node_id), "period": period, "days": days, "rows": rows, "total_up": sum(r["up"] for r in rows), "total_down": sum(r["down"] for r in rows)}


# ------------------------------------------------------------------ backups (the node as it was: with its key and certificate, in a file only this server can read)
def _bdir() -> Path:
    d = get_settings().data_dir / "pg-backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _kept(pid: int) -> list[tuple[int, Path]]:
    """The backup files of one panel, oldest first, as (time in their name, path)."""
    found = []

    for p in _bdir().glob(f"nodes-p{pid}-*.json"):
        m = _BACKUP.match(p.name)

        if m:
            found.append((int(m.group(2)), p))

    return sorted(found)


def backup_node(c: dict, raw: dict, kind: str) -> str:
    """Save a node as the panel has it (edit | delete | cert | restore - what is about to be done to it). The file is readable by this server only; the last 20 per panel are kept.
    The time in the name only ever goes up (a burst of backups runs a few seconds ahead of the clock), so the newest are the ones that stay."""
    pid, nid = int(c.get("id") or 0), _int(raw.get("id"))
    files = _kept(pid)
    ts = max(int(time.time()), files[-1][0] + 1 if files else 0)
    path = _bdir() / f"nodes-p{pid}-{ts}-n{nid}-{kind}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"kind": kind, "panel_id": pid, "at": ts, "node": raw}, f, ensure_ascii=False)

    for _t, old in (files + [(ts, path)])[:-KEEP_BACKUPS]:
        old.unlink()

    return path.name


def backups(c: dict) -> list[dict]:
    """The kept backups of this panel's nodes, newest first: what was about to be done, to which node. Never the key or the certificate."""
    out = []

    for ts, p in reversed(_kept(int(c.get("id") or 0))):
        try:
            n = json.loads(p.read_text(encoding="utf-8")).get("node") or {}
        except (OSError, ValueError):
            continue

        m = _BACKUP.match(p.name)
        out.append({"name": p.name, "at": ts, "kind": m.group(4), "node_id": int(m.group(3)), "node": _text(n.get("name"), 80), "address": _text(n.get("address"), 253)})

    return out


def restore(c: dict, name: str) -> dict:
    """Put a node back as a backup holds it: a deleted node is made again (a new id, its history is gone for good), an edited one gets its old values back (the state before is saved first)."""
    m = _BACKUP.match(str(name or ""))

    if not m or int(m.group(1)) != int(c.get("id") or 0) or not (_bdir() / name).is_file():
        raise ValueError("no such backup")

    try:
        raw = json.loads((_bdir() / name).read_text(encoding="utf-8")).get("node")
    except (OSError, ValueError):
        raise ValueError("this backup cannot be read")

    if not isinstance(raw, dict) or not raw.get("name"):
        raise ValueError("this backup holds no node")

    if m.group(4) == "delete":
        if any(norm(n.get("name")) == norm(raw["name"]) for n in _nodes_raw(c)):
            raise ValueError(f"a node called {raw['name']} exists now: rename or delete it first")

        made = panels.call(c, "POST", "/api/node", body=snapshot_body(raw, True))
        _note(c, f"Node {raw['name']} made again from the backup {name}.")
        return {"node": public_node(made), "recreated": True, "backup": ""}

    try:
        cur = _node_raw(c, _int(raw.get("id")))
    except panels.PanelError as e:
        if e.code == 404:
            raise ValueError("this node is not in the panel any more: restore its 'delete' backup instead, if there is one")

        raise

    saved = backup_node(c, cur, "restore")
    done = panels.call(c, "PUT", _npath(raw["id"]), body=snapshot_body(raw, False))
    _note(c, f"Node {raw['name']} put back as in the backup {name}.")
    return {"node": public_node(done), "recreated": False, "backup": saved}


# ------------------------------------------------------------------ actions on one node
def node_add(c: dict, fields: dict) -> dict:
    """Add a node to the panel (the panel connects to it in the background; its first status is 'connecting')."""
    body = create_body(fields)

    if any(norm(n.get("name")) == norm(body["name"]) for n in _nodes_raw(c)):
        raise ValueError(f"a node called {body['name']} exists already")

    made = panels.call(c, "POST", "/api/node", body=body)
    _note(c, f"Node {body['name']} ({body['address']}) added.")
    return public_node(made)


def node_edit(c: dict, node_id: int, changes: dict) -> dict:
    """Change a node. The panel then starts the core on it again (its users drop for a moment); the node is saved to a file first."""
    cur = _node_raw(c, node_id)
    body, changed = edit_body(cur, changes)
    saved = backup_node(c, cur, "edit")
    done = panels.call(c, "PUT", _npath(node_id), body=body)
    _note(c, f"Node {cur.get('name')} changed ({', '.join(changed)}).")
    return {"node": public_node(done), "changed": changed, "backup": saved}


def node_delete(c: dict, node_id: int, confirm: bool) -> dict:
    """Delete a node: the panel stops using it and drops its traffic history; the users stay, but whoever is connected through it is cut. Saved to a file first."""
    if not confirm:
        raise ValueError("deleting a node needs confirm=true")

    cur = _node_raw(c, node_id)
    saved = backup_node(c, cur, "delete")
    panels.call(c, "DELETE", _npath(node_id))
    _note(c, f"Node {cur.get('name')} ({cur.get('address')}) deleted.", "warn")
    return {"deleted": _text(cur.get("name"), 80), "backup": saved, "uplink": _int(cur.get("uplink")), "downlink": _int(cur.get("downlink"))}


def node_reconnect(c: dict, node_id: int) -> dict:
    """Restart the core on the node and connect again (a forced start: connections through the node drop for a moment)."""
    cur = _node_raw(c, node_id)
    panels.call(c, "POST", f"{_npath(node_id)}/reconnect", body={})
    _note(c, f"Node {cur.get('name')} restarted from the panel.")
    return {"name": _text(cur.get("name"), 80)}


def node_sync(c: dict, node_id: int, flush_users: bool = True) -> dict:
    """Send the user list to the node again (no restart); the panel refuses it while the node is not connected."""
    cur = _node_raw(c, node_id)
    panels.call(c, "PUT", f"{_npath(node_id)}/sync?flush_users={'true' if flush_users else 'false'}")
    _note(c, f"Users of node {cur.get('name')} sent again.")
    return {"name": _text(cur.get("name"), 80)}


def node_state(c: dict, node_id: int, enabled: bool) -> dict:
    """Enable or disable a node with the panel's own bulk routes (a disabled node is disconnected: whoever is on it is cut; enabling connects it)."""
    cur = _node_raw(c, node_id)
    panels.call(c, "POST", f"/api/nodes/bulk/{'enable' if enabled else 'disable'}", body={"ids": [int(node_id)]})
    _note(c, f"Node {cur.get('name')} {'enabled' if enabled else 'disabled'}.", "info" if enabled else "warn")
    return {"name": _text(cur.get("name"), 80), "enabled": bool(enabled)}


def node_reset_usage(c: dict, node_id: int) -> dict:
    """Set the node's uplink and downlink counters to zero (the panel keeps a log row with the old numbers; a node stopped by its data limit connects again)."""
    cur = _node_raw(c, node_id)
    panels.call(c, "POST", f"{_npath(node_id)}/reset", body={})
    _note(c, f"Traffic counters of node {cur.get('name')} set to zero.")
    return {"name": _text(cur.get("name"), 80), "uplink": _int(cur.get("uplink")), "downlink": _int(cur.get("downlink"))}


def node_maintenance(c: dict, node_id: int, what: str, core_version: str = "latest", region: str = "iran") -> dict:
    """Ask the node to update itself (`node`: its own software and a restart), its Xray core (`core`: "latest" or a version) or its geo files (`geofiles`: iran, china or russia)."""
    if what not in MAINTENANCE:
        raise ValueError(f"what must be one of: {', '.join(MAINTENANCE)}")

    if what == "core" and not _CORE_VERSION.match(str(core_version)):
        raise ValueError('the core version is "latest" or a version like 25.8.31')

    if what == "geofiles":
        region = _one_of(region, REGIONS, "the region")

    cur = _node_raw(c, node_id)

    if what == "node":
        panels.call(c, "POST", f"{_npath(node_id)}/update", body={})
        said = "software update asked"
    elif what == "core":
        panels.call(c, "POST", f"{_npath(node_id)}/core_update", body={"core_version": str(core_version)})
        said = f"Xray {core_version} asked"
    else:
        panels.call(c, "POST", f"{_npath(node_id)}/geofiles", body={"region": region})
        said = f"geo files ({region}) asked"

    _note(c, f"Node {cur.get('name')}: {said}.", "warn")
    return {"name": _text(cur.get("name"), 80), "said": said}


# ------------------------------------------------------------------ the certificate of a node and the node on our server
def _server_cert(server_id: int) -> str:
    """The public certificate the node on a server made (read-only over SSH). ValueError says why it cannot be had; ServerSilent when the server did not answer."""
    try:
        return extras.pasarguard_cert(server_id)["cert"]
    except SSHError as e:
        raise ServerSilent(f"the server did not answer over SSH: {str(e)[:120]}")


def server_cert(server_id: int) -> dict:
    """The certificate of the PasarGuard node installed on one of our servers (public; to copy into the panel by hand) and what it says about itself."""
    srv = _server_by_id(server_id)
    pem = _server_cert(server_id)
    return {"server": {"id": srv["id"], "name": srv["name"], "host": srv["host"]}, "cert": pem, **(cert_summary(pem) or {})}


def cert_compare(c: dict, node_id: int, resolver: Resolver | None = None) -> dict:
    """Is the certificate the panel holds for a node the one the node on its server made? (the node's server is found by address; read-only on the server)"""
    raw = _node_raw(c, node_id)
    row = public_node(raw)
    joined, _free = _join([row], resolver)
    srv = joined[0]["server"]
    out = {"node": {"id": row["id"], "name": row["name"]}, "in_panel": cert_summary(raw.get("server_ca")), "server": {"id": srv["id"], "name": srv["name"]} if srv else None,
           "on_server": None, "same": None, "problem": ""}

    if srv is None:
        out["problem"] = "No server of this panel has the address of this node, so its certificate cannot be read."
        return out

    try:
        pem = _server_cert(srv["id"])
    except ValueError as e:
        out["problem"] = str(e)
        return out

    out["on_server"] = cert_summary(pem)
    out["same"] = same_cert(raw.get("server_ca"), pem)
    return out


def cert_apply(c: dict, node_id: int, resolver: Resolver | None = None) -> dict:
    """Put the certificate the node made (read from its server) into the panel, when the two differ. This saves the node, so the panel starts the core on it again."""
    cmp = cert_compare(c, node_id, resolver)

    if cmp["problem"]:
        raise ValueError(cmp["problem"])

    if cmp["same"]:
        return {"changed": False, "backup": "", "node": cmp["node"]["name"]}

    srv = cmp["server"]
    pem = _server_cert(srv["id"])
    cur = _node_raw(c, node_id)
    body, _changed = edit_body(cur, {"server_ca": pem})
    saved = backup_node(c, cur, "cert")
    panels.call(c, "PUT", _npath(node_id), body=body)
    _note(c, f"Node {cur.get('name')}: the certificate of the panel replaced by the one on server {srv['name']}.")
    return {"changed": True, "backup": saved, "node": cmp["node"]["name"]}


# ------------------------------------------------------------------ the unified flow: a server of ours becomes a node
def make_options(opts: dict | None, default_name: str, installing: bool) -> dict:
    """The choices for a new node of one of our servers, checked. While this panel installs the node itself the ports and the connection type are the installer's defaults."""
    o = dict(opts or {})
    out = {"name": check_name(o.get("name") or default_name), "usage_coefficient": check_field("usage_coefficient", o.get("usage_coefficient", 1.0)),
           "core_config_id": check_field("core_config_id", o["core_config_id"]) if o.get("core_config_id") else None}

    if installing:
        out.update(port=NODE_PORT, api_port=API_PORT, connection_type="grpc")
    else:
        out.update(port=check_field("port", o.get("port", NODE_PORT)), api_port=check_field("api_port", o.get("api_port", API_PORT)),
                   connection_type=check_field("connection_type", o.get("connection_type", "grpc")))

    return out


def _pick_core(cores: list[dict], wanted) -> tuple[int | None, str]:
    """(the core config to use, why not): the one asked for, else the only Xray core there is."""
    if wanted:
        return (int(wanted), "") if any(x["id"] == int(wanted) for x in cores) else (None, f"the panel has no core config {wanted}")

    xray = [x for x in cores if x.get("type") in ("xray", "")]

    if len(xray) == 1:
        return xray[0]["id"], ""

    return None, "the panel has no core config: make one in the Cores tab first" if not xray else "the panel has several core configs: choose which one the node runs"


def _sentence(why: str) -> str:
    return why[:1].upper() + why[1:] + "."


def _node_on(c: dict, srv: dict, resolver: Resolver | None = None) -> tuple[list[dict], dict | None, list[dict]]:
    """(the nodes of the panel, the one that already runs on this server of ours - if any, the nodes of OTHER panels that run on it)."""
    nodes = [public_node(n) for n in _nodes_raw(c)]
    pairs, _free, elsewhere = _match(nodes, resolver, _others(c))
    return nodes, next((n for n, p in zip(nodes, pairs) if p["server"] and p["server"]["id"] == srv["id"]), None), elsewhere.get(srv["id"], [])


def _where(elsewhere: list[dict]) -> str:
    return ", ".join(f"{e['node'] or 'a node'} of {e['panel']} ({e['type']})" for e in elsewhere[:3])


def pick_ports(busy: dict[int, str]) -> tuple[int, int] | None:
    """Pure: the ports of a new node - the usual pair when it is free, else the first free pair 62060/62061, 62070/62071 ... (None when there is none up to 62299)."""
    if NODE_PORT not in busy and API_PORT not in busy:
        return NODE_PORT, API_PORT

    for base in range(62060, 62300, 10):
        if base not in busy and base + 1 not in busy:
            return base, base + 1

    return None

def make_plan(c: dict, server_id: int, opts: dict | None = None, resolver: Resolver | None = None) -> dict:
    """What making one of our servers a node would do, and what stands in the way. Reads the panel and the server (is the node installed there already?), changes nothing."""
    srv = _server_by_id(server_id)
    problems: list[str] = []
    warnings: list[str] = []
    installed: bool | None = None

    if not srv["enabled"]:
        problems.append("The server is turned off in this panel.")
    elif srv["online"] is False:
        problems.append("The server does not answer: it is offline.")
    else:
        try:
            _server_cert(server_id)
            installed = True

            if not extras.pgnode_running(server_id):          # (a failed install leaves its certificate behind: that is no node)
                installed = False
                warnings.append("A certificate of a PasarGuard node is on the server, but no node is running: an earlier install did not finish. It is done again.")
        except ServerSilent as e:
            problems.append(str(e))
        except ValueError:
            installed = False                      # it answered: there is no node certificate, so no node yet

    busy: dict[int, str] = {}

    if installed is not True and not problems:          # the node is not there: its two ports must be free (the owner, 2026-10-15: "when a port is busy it goes to a new port by itself")
        try:
            busy = extras.ports_busy(server_id)
        except (SSHError, ValueError):
            busy = {}

    o = make_options(opts, srv["name"], installing=installed is not True)

    if installed is not True and (NODE_PORT in busy or API_PORT in busy):
        pair = pick_ports(busy)
        who = "; ".join(f"{p} by {busy[p]}" for p in (NODE_PORT, API_PORT) if p in busy)

        if pair is None:
            problems.append(f"The ports {NODE_PORT} and {API_PORT} are taken on this server ({who}) and no free pair was found between 62060 and 62299. Free them, or install the node by hand and add it with Add node.")
        else:
            o["port"], o["api_port"] = pair
            warnings.append(f"The ports {NODE_PORT} and {API_PORT} are taken on this server ({who}): the node will use {pair[0]} and {pair[1]} - open them in the server's firewall if there is one.")
    nodes, clash, elsewhere = _node_on(c, srv, resolver)

    if clash:
        problems.append(f"This server is already the node {clash['name']} of this panel.")

    if elsewhere and installed is True:
        warnings.append(f"This server is also a node of another panel: {_where(elsewhere)}.")
    elif elsewhere:
        problems.append(f"This server already carries a node of another panel: {_where(elsewhere)}. That node listens on the ports a new one would use ({NODE_PORT}, {API_PORT}), so installing here "
                        "would break it. If you really want both, install the second node by hand on other ports and add it with Add node.")

    if any(norm(n["name"]) == norm(o["name"]) for n in nodes):
        problems.append(f"A node called {o['name']} exists already: choose another name.")

    cores = _cores(c)
    o["core_config_id"], why = _pick_core(cores, o["core_config_id"])
    min_version = _min_version(c, warnings)

    if why:
        problems.append(_sentence(why))

    if installed is False and get_settings().read_only:
        problems.append("The panel is read-only: set NIKATEL_READ_ONLY=false on the panel server to let it install on a server.")

    if srv["role"] != "foreign":
        warnings.append("PasarGuard nodes normally run on foreign servers; this one is an Iran server.")

    first = "The node is installed on the server already: the install is skipped." if installed else "Install the PasarGuard node on the server (the official installer, Docker; a few minutes)."
    steps = [first, "Read the certificate the node made from the server.", f"Add the node {o['name']} to the panel ({srv['host']}:{o['port']}).", "Wait until the panel is connected to it."]
    return {"ok": not problems, "problems": problems, "warnings": warnings, "installed": installed, "steps": steps, "options": o, "cores": cores, "min_node_version": min_version,
            "server": {"id": srv["id"], "name": srv["name"], "host": srv["host"], "role": srv["role"]}, "read_only": get_settings().read_only}


def _step(server_id: int | None, text: str, what: str = "node") -> None:
    """What the panel is doing on this server now, as a short line for its row on the Lab page (the owner: "show the steps of the install there"); "" clears it."""
    if server_id is None:
        return

    from . import nodes as srv_nodes

    with contextlib.suppress(Exception):          # a line for the row must never stop the install
        srv_nodes.set_busy(server_id, what if text else "", text.strip()[:80])


def _follow(aid: int, inner: int, limit: float, server_id: int | None = None) -> tuple[bool, str]:
    """Follow the action of the install (extras.start) and repeat its lines in ours until it ends: (did it end well, its last line)."""
    end, seen = time.time() + limit, 0

    while True:
        with session() as db:
            e = db.get(ActionLog, inner)
            story = list(e.story or []) if e is not None else []
            done, ok = bool(e is not None and e.finished_at), bool(e is not None and e.ok)

        for line in story[seen:]:
            if line.strip() and line.strip() != "Done." and "Next: in this server's Install view" not in line:
                autopilot._say(aid, "    " + line)

        seen = len(story)

        if done:
            useful = [ln for ln in story if ln.strip() and not ln.startswith("Done")]                # "Done." / "Done with problems" say nothing the line before did not
            return ok, (useful[-1] if useful else "")

        if time.time() >= end:
            return False, "it did not finish in time; it keeps running on the server"

        time.sleep(POLL)


def _wait_connected(c: dict, node_id: int, limit: float | None = None) -> dict:
    """Look at the node until the panel says connected, it has said error three times in a row, or the time is up; returns the last row."""
    end, bad, last = time.time() + (CONNECT_LIMIT if limit is None else limit), 0, {}

    while True:
        try:
            last = public_node(_node_raw(c, node_id))
        except (panels.PanelError, ValueError):
            last = last or {}

        bad = bad + 1 if last.get("status") in ("error", "disabled", "limited") else 0

        if last.get("status") == "connected" or bad >= 3 or time.time() >= end:
            return last

        time.sleep(POLL)


def _cert_with_retry(server_id: int, tries: int = 5) -> str:
    """The node needs a moment to write its certificate after the install."""
    last = ""

    for i in range(tries):
        try:
            return _server_cert(server_id)
        except ValueError as e:
            last = str(e)

        if i + 1 < tries:
            time.sleep(POLL)

    raise ValueError(last)


NOT_CONNECTED_HELP = ("The node is in the panel but the panel could not connect to it yet. Look at the status above. If it is about the key: the node on the server uses another API key than the one "
                      "this panel made - read it on the server (command: pg-node edit-env, line API_KEY) and set it in Edit. If it is about the certificate: use Certificate - compare and copy "
                      "it from the server. If nothing answers: the ports {port} and {api_port} must be open in the server's firewall.")


def _make_story(aid: int, panel_id: int, server_id: int, opts: dict) -> None:
    """The action: install (unless the node is there), read the certificate, add the node to the panel, wait for it to connect. Every step says what it does (and says it on the row of the server); a stop says what is left to do."""
    try:
        _make_story_steps(aid, panel_id, server_id, opts)
    finally:
        _step(server_id, "")


def _make_story_steps(aid: int, panel_id: int, server_id: int, opts: dict) -> None:
    say = autopilot._say

    try:
        c = panels.cred_for(panel_id)
        srv = _server_by_id(server_id)
    except ValueError as e:
        say(aid, f"Stopped: {e}", done=True, ok=False)
        return

    key, name = node_key(panel_id, server_id), opts["name"]
    say(aid, f"Making {srv['name']} a node of the panel: install the node, read its certificate, add it to the panel, wait until it connects.")

    if opts.get("installed"):
        say(aid, "1/4 The PasarGuard node is on the server already: the install is skipped.")
        _step(server_id, "1/4 The node is on the server already")
    else:
        say(aid, f"1/4 Installing the PasarGuard node on {srv['name']} (the official installer, Docker and the node; a few minutes)…")
        _step(server_id, "1/4 Installing Docker and the node (minutes)")

        try:
            inner = extras.start(server_id, ["pasarguard_node"], key if (opts["port"], opts["api_port"]) == (NODE_PORT, API_PORT) else f"{key}:{opts['port']}:{opts['api_port']}")
        except ValueError as e:
            say(aid, f"Stopped at step 1: the install could not start - {e}", done=True, ok=False)
            return

        ok, last = _follow(aid, inner, INSTALL_LIMIT, server_id)

        if not ok:
            say(aid, f"Stopped at step 1: the install did not end well ({last[:160]}). Nothing was added to the panel. Fix the server and make it a node again - the install is skipped when it worked.", done=True, ok=False)
            return

    say(aid, "2/4 Reading the certificate the node made…")
    _step(server_id, "2/4 Reading the certificate")

    try:
        pem = _cert_with_retry(server_id)
    except ValueError as e:
        say(aid, f"Stopped at step 2: {e}. The node is not in the panel yet: make the server a node again when the node runs, or add it by hand with the certificate.", done=True, ok=False)
        return

    say(aid, f"3/4 Adding the node {name} to the panel ({srv['host']}:{opts['port']})…")
    _step(server_id, "3/4 Adding the node to the panel")

    try:
        made = node_add(c, {"name": name, "address": srv["host"], "port": opts["port"], "api_port": opts["api_port"], "usage_coefficient": opts["usage_coefficient"],
                            "connection_type": opts["connection_type"], "core_config_id": opts["core_config_id"], "api_key": key, "server_ca": pem})
    except (ValueError, panels.PanelError) as e:
        say(aid, f"Stopped at step 3: the panel did not take the node - {e}. The node runs on the server: fix that and make the server a node again (the install is skipped, the node is found), or use Add node.", done=True, ok=False)
        return

    say(aid, "4/4 Waiting until the panel is connected to the node (up to two minutes)…")
    _step(server_id, "4/4 Waiting for the panel to connect (up to 2 min)")
    last = _wait_connected(c, made["id"])

    if last.get("status") == "connected":
        say(aid, f"Node {name} is connected (Xray {last.get('xray') or '?'}, node {last.get('version') or '?'}). {srv['name']} is a node now.", done=True, ok=True)
        _note(c, f"Server {srv['name']} made the node {name}.", "ok")
        return

    why = f"{last.get('status') or 'no answer'}" + (f": {last['message']}" if last.get("message") else "")
    say(aid, f"Node {name} is in the panel, status {why}. " + NOT_CONNECTED_HELP.format(port=opts["port"], api_port=opts["api_port"]), done=True, ok=False)


def make_start(c: dict, server_id: int, opts: dict | None = None) -> int:
    """Start 'make this server a node' as an action (returns its id; the story is read with /api/actions/{id}). Refused with the first problem the plan finds."""
    plan = make_plan(c, server_id, opts)

    if plan["problems"]:
        raise ValueError(plan["problems"][0])

    o = dict(plan["options"], installed=plan["installed"] is True)
    label = f"node {plan['server']['name']}"[:60]

    if label in autopilot._BUSY:                                    # a second click while the first run is still going
        raise ValueError(f"{plan['server']['name']} is being made a node right now: follow it in the history of the Tunnel Doctor")

    return autopilot._start(label, _make_story, int(c["id"]), int(server_id), o, action="pgnode", source="manual",
                            allow_read_only=o["installed"])          # nothing on the server changes when the node is there already


def register(c: dict, server_id: int, opts: dict | None = None, api_key: str = "") -> dict:
    """Add a node that is installed on one of our servers already: read its certificate from the server and register it in the panel. The API key is the one the operator gives (a node
    installed by hand has its own) or, empty, the one this panel would have made."""
    srv = _server_by_id(server_id)
    _nodes, clash, elsewhere = _node_on(c, srv)

    if clash:
        raise ValueError(f"This server is already the node {clash['name']} of this panel.")

    o = make_options(opts, srv["name"], installing=False)
    core_id, why = _pick_core(_cores(c), o["core_config_id"])

    if why:
        raise ValueError(_sentence(why))

    given = bool(str(api_key or "").strip())
    made = node_add(c, {"name": o["name"], "address": srv["host"], "port": o["port"], "api_port": o["api_port"], "usage_coefficient": o["usage_coefficient"], "connection_type": o["connection_type"],
                        "core_config_id": core_id, "api_key": check_key(api_key) if given else node_key(int(c["id"]), server_id), "server_ca": _server_cert(server_id)})
    _note(c, f"Server {srv['name']} registered as the node {o['name']}.")
    return {"node": made, "key": "given" if given else "made by this panel", "warnings": [f"This server is also a node of another panel: {_where(elsewhere)}."] if elsewhere else []}
