"""The automatic choice of the Iran server for a new tunnel (ranked by quality, resources, load and free ports)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel
from ..ssh import Node, SSHError, run_script
from .nodes import parse_ping

log = get_logger("detect")


# ------------------------------------------------------------------ automatic choice of the Iran server
def rank_key(r: dict) -> tuple:
    """Pure: the order of the Iran servers for a new tunnel - the unusable last; then QUALITY first (ping and loss to the foreign server, in steps of 10 points,
    so that two servers of about the same quality count as equal); inside a step the one with MORE free resources; then the one that carries fewer tunnels."""
    return (r.get("score", 0) <= 0, -(r.get("quality", 0) // 10), -r.get("resources", 0), -r.get("unused", 0), r.get("name", ""))


def auto_iran(foreign_id: int, ports_text: str = "") -> list[dict]:
    """Rank the Iran servers for a new tunnel to this foreign server: ping quality from each one (loss,
    rtt), free resources, how many tunnels it already carries, and whether the wanted ports are free."""
    from .builder import parse_ports

    wanted = {p.listen for p in parse_ports(ports_text)} if ports_text.strip() else set()

    with session() as db:
        f = db.get(Server, foreign_id)

        if f is None or f.role != "foreign":
            raise ValueError("choose a foreign server")

        fhost = f.host
        iran = [(s.id, s.name, Node(s.host, s.ssh_user, s.ssh_port), dict(s.facts or {})) for s in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)))]
        load = {}

        for t in db.scalars(select(Tunnel)):
            load[t.iran_server_id] = load.get(t.iran_server_id, 0) + 1
            load.setdefault(("ports", t.iran_server_id), set()).update(int(x) for x in (t.forwarded_ports or []) if str(x).isdigit())

    def one(item):
        sid, name, node, facts = item
        row = {"id": sid, "name": name, "tunnels": load.get(sid, 0), "cpu": facts.get("cpu"), "ram": facts.get("ram"), "load1": facts.get("load1"), "cores": facts.get("cores") or 1}
        busy = wanted & load.get(("ports", sid), set())

        if busy:
            row.update(score=0, reason="port " + ", ".join(map(str, sorted(busy))) + " already used here")
            return row

        try:
            tx, rx, avg = parse_ping(run_script(node, "ping_host", {"HOST": fhost}, timeout=30))
        except (SSHError, ValueError) as e:
            row.update(score=0, reason="cannot ping: " + str(e)[:60])
            return row

        if rx == 0:
            row.update(score=0, reason="cannot reach the foreign server", rtt_ms=None, loss_pct=100)
            return row

        loss = round((tx - rx) * 100.0 / max(tx, 1))
        q = max(0.0, 100 - loss * 10) * 0.6 + max(0.0, 100 - max(0.0, (avg or 0) - 40) / 2) * 0.4
        r = max(0.0, 100 - (facts.get("cpu") or 0)) * 0.5 + max(0.0, 100 - (facts.get("ram") or 0)) * 0.3 + max(0.0, 100 - min(100, (facts.get("load1") or 0) / (facts.get("cores") or 1) * 50)) * 0.2
        u = max(0.0, 100 - load.get(sid, 0) * 12.5)
        row.update(rtt_ms=avg, loss_pct=loss, quality=round(q), resources=round(r), unused=round(u), score=round(q * 0.4 + r * 0.3 + u * 0.3),
                   reason=f"{avg} ms, {loss}% loss, {load.get(sid, 0)} tunnels, cpu {facts.get('cpu')}%")
        return row

    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, iran))

    return sorted(rows, key=rank_key)
