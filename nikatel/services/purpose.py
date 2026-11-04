"""What a tunnel is FOR (1.69.216): a tunnel that carries an economy (ECO) or an AI config says so on its row of the Tunnels page.
(The owner, 2026-11-04, looking at two tunnels of one foreign server and asking why there were two: "the tunnels that are for eco or AI - write it on them".)

The panel already knows which configs ride which tunnel (`userq.map_configs`: the address of a config - an Iran server, or a name whose A record points at one - and its port against the ports a tunnel forwards).
Here the hosts of the PasarGuard panels are placed the same way and every tunnel gets the purposes of the hosts that end on it:

  eco  a host whose address starts with "eco", or whose remark carries the traffic coefficient ("Turkey ضریب x2")
  ai   a host whose address starts with "ai-", or whose remark names an AI config ("AI-1", "AI-2")

Kept in the record of the tunnel (`purpose`, a sorted list). Nothing is guessed: a host that cannot be placed on a tunnel for sure labels nothing, and a look that reads no host or no DNS record changes nothing.
The pure helpers (`purpose_of`, `tunnel_purposes`) carry the decisions and are tested."""
from __future__ import annotations

import re
import time

from sqlalchemy import select

from .. import jobs
from ..db import session
from ..log import get_logger
from ..models import Panel, Server, Tunnel, is_temp
from . import dnscache, panels, userq

log = get_logger("purpose")
EVERY = 600
FRESH = 900                                  # the copy of the DNS records must be this fresh (the quality job renews it in the background)
COEFFICIENT = "ضریب"                         # the remark of an economy host: "Turkey ضریب x2"
_AI_REMARK = re.compile(r"(?<![A-Za-z])AI-?\d", re.IGNORECASE)


def purpose_of(remark, address) -> str:
    """Pure: "eco", "ai" or "" for a host from its remark and its address."""
    a = (address[0] if isinstance(address, list) and address else address) or ""
    a = str(a).strip().lower()
    r = str(remark or "")

    if a.startswith("eco") or COEFFICIENT in r:
        return "eco"

    if a.startswith("ai-") or _AI_REMARK.search(r):
        return "ai"

    return ""


def _port(v) -> int:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return 0

    return n if 0 < n < 65536 else 0


def tunnel_purposes(hosts: list[dict], tunnels: list[dict], servers: list[dict], records: list[dict]) -> dict[int, list[str]]:
    """Pure: {tunnel id: ["ai", "eco"]} - the purposes of the hosts that end on each tunnel. hosts: [{"remark", "address", "port", "disabled"}] of the panels; tunnels / servers / records as in `userq.map_configs`
    (the tunnels with their forwarded ports, the Iran servers, the A records of the DNS accounts). A disabled host, a host without a port and a host that cannot be placed for sure label nothing."""
    cfgs, kinds = [], {}

    for h in hosts:
        addr = h.get("address")
        addr = str((addr[0] if isinstance(addr, list) and addr else addr) or "").split(",")[0].strip()
        port, kind = _port(h.get("port")), purpose_of(h.get("remark"), addr)

        if h.get("disabled") or not kind or not port or not addr:
            continue

        key = f"{addr}:{port}"
        cfgs.append({"name": key, "tunnel": "", "address": addr, "port": port, "sni": "", "host": ""})
        kinds[key] = kind

    out: dict[int, set[str]] = {}

    for key, where in userq.map_configs(cfgs, tunnels, servers, records).items():
        out.setdefault(where["tunnel"], set()).add(kinds[key])

    return {tid: sorted(v) for tid, v in out.items()}


def store(got: dict[int, list[str]]) -> int:
    """Write the purposes into the record of every tunnel (the key goes when a tunnel has none any more); returns how many rows changed."""
    changed = 0

    with session() as db:
        for t in db.scalars(select(Tunnel)):
            rec = dict(t.record or {})
            want = got.get(t.id) or []

            if (rec.get("purpose") or []) == want:
                continue

            rec.pop("purpose", None)

            if want:
                rec["purpose"] = want

            t.record = rec
            changed += 1

    return changed


def _read_hosts() -> list[dict]:
    with session() as db:
        ids = [p.id for p in db.scalars(select(Panel).where(Panel.enabled.is_(True)))]

    out: list[dict] = []

    for pid in ids:
        try:
            with session() as db:
                p = db.get(Panel, pid)
                c = panels._cred(p) if p is not None else None

            if c is not None:
                out += panels.hosts(c)[0]
        except (panels.PanelError, ValueError, OSError) as e:
            log.warning("the hosts of panel %s could not be read: %s", pid, str(e)[:100])

    return out


def run() -> None:
    """Job (every ten minutes): the hosts of the panels are placed on the tunnels and every tunnel's row is told what it is for. A look that reads no host or no DNS record leaves the labels as they are."""
    if jobs.stopping():
        return

    if time.time() - float(dnscache.CACHE.get("at") or 0) > FRESH:
        dnscache.refresh_in_background()

    records = list(dnscache.CACHE.get("rows") or [])
    hosts = _read_hosts()

    if not hosts or not records:
        return

    with session() as db:
        tunnels = [{"id": t.id, "name": t.name, "iran_id": t.iran_server_id, "ports": {int(p) for p in (t.forwarded_ports or []) if str(p).isdigit()}}
                   for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name)]
        servers = [{"id": s.id, "name": s.name, "host": s.host} for s in db.scalars(select(Server).where(Server.role == "iran"))]

    n = store(tunnel_purposes(hosts, tunnels, servers, records))

    if n:
        log.info("purpose: %s tunnel(s) relabelled", n)
