"""Is an Iran server ON when the panel's own host cannot connect to it? (the owner, 2026-10-31, with a check-host.net ping of an Iran server: Iranian locations 4/4, every other country 0/4 - "they may close every
connection from outside to the Iran servers: find a way that the panel tells on or off even then". And: "do not use check-host; in the cuts only the spoof tunnel answered, check-host did not show Iran at all".)

The pulse (services/pulse.py) knows one thing: whether the panel's HOST can open a TCP connection. A server that is cut from abroad but works for its users inside Iran is on, and the pulse called it off: the row said
Offline, its tunnel said down, the Doctor repaired it. This job asks for a proof of life, once a minute and at the first miss, for every Iran server whose connection from the panel's host failed. The evidence, in this order:
  1. its TUNNELS, seen from the FOREIGN end (the panel reaches the foreign servers): the reviewed script `peer_alive` counts the open TCP connections between the foreign server and the Iran server and, for a tunnel with
     an L3 link of its own (BackPack Direct - the IP-spoofing carrier is one), pings the inner address of the Iran end over it. In a cut of the line abroad -> Iran only what still passes can answer - the spoof tunnel -
     and that is exactly the evidence that counts then; nothing from the outside world is asked.
  2. the other Iran servers of the panel that the panel can reach open a TCP connection to its SSH port (for the day when one address is filtered from the panel's host and the others are not).
Any answer proves the server is on: the pulse keeps it on for PROOF_GRACE seconds and writes who saw it in its row. No answer proves nothing: the pulse's own rule (two misses in a row) stays, so a server that is
really off is still called off."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel
from ..ssh import Node, SSHError, run_script
from . import pulse

log = get_logger("servers")

ASK_LIMIT = 4          # servers asked in one round
VANTAGES = 3          # Iran servers that open the connection
FOREIGN_ENDS = 4          # foreign servers asked about one Iran server
_IPV4 = re.compile(r"\d{1,3}(\.\d{1,3}){3}")


def opened(out: str, host: str, port: int) -> bool:
    """Pure: did the script `tcp_reach` open the connection? ("REACH host:port 1 <ms>")"""
    return f"REACH {host}:{int(port)} 1 " in str(out)


def parse_view(out: str) -> tuple[int, int]:
    """Pure: (open connections to the Iran server, pings answered over the L3 link out of 3) from the script `peer_alive`."""
    est = ping = 0

    for line in str(out).splitlines():
        p = line.split()

        if len(p) >= 2 and p[0] == "PEER_EST" and p[1].isdigit():
            est = int(p[1])
        elif len(p) >= 2 and p[0] == "PEER_PING":
            m = re.match(r"(\d+)/", p[1])
            ping = int(m.group(1)) if m else 0

    return est, ping


def seen_by_tunnel(foreign: str, est: int, ping: int) -> str:
    """Pure: what the foreign end of a tunnel says about the Iran server ("" when nothing shows it is there)."""
    if est > 0:
        return f"{est} open connection{'s' if est > 1 else ''} of its tunnel to {foreign}"

    if ping > 0:
        return f"the link of its tunnel to {foreign} answers"

    return ""


def decide(tunnel_hits: list[str], opened_from: list[str]) -> tuple[bool, str]:
    """Pure: (it answered, who saw it). `tunnel_hits`: the sentences of foreign ends that see it; `opened_from`: the names of the Iran servers whose connection opened."""
    if tunnel_hits:
        return True, "seen by its tunnels: " + "; ".join(tunnel_hits[:2])

    if opened_from:
        return True, "seen from Iran by " + ", ".join(opened_from[:3])

    return False, ""


def _tunnel_ends(sid: int) -> list[tuple[str, Node, str, str]]:
    """(foreign server, node, L3 interface, inner address of the Iran end) of every ON foreign server that has a tunnel with this Iran server; a tunnel with an L3 link is preferred for its server."""
    out: dict[int, tuple[str, Node, str, str]] = {}

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.iran_server_id == sid)):
            f = t.foreign

            if f is None or not f.enabled or f.online is not True:
                continue

            rec = t.record or {}
            iface, inner = "", ""

            if str(t.transport or "").startswith("l3:") and rec.get("inner_net") and t.iran_tunnel_ip and _IPV4.fullmatch(str(t.iran_tunnel_ip)):
                iface, inner = f"bp{int(rec['inner_net'])}", str(t.iran_tunnel_ip)

            if f.id not in out or (iface and not out[f.id][2]):
                out[f.id] = (f.name, Node(f.host, f.ssh_user, f.ssh_port), iface, inner)

    return list(out.values())[:FOREIGN_ENDS]


def _vantages(skip: set[int]) -> list[tuple[str, Node]]:
    """The Iran servers that may be asked: on, enabled, and not one whose own connection from the panel's host fails (they are the problem, not the eyes)."""
    with session() as db:
        rows = [(s.id, s.name, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)).order_by(Server.name))]

    return [(n, node) for i, n, node in rows if i not in skip][:VANTAGES]


def ask(sid: int, skip: set[int]) -> tuple[bool, str]:
    """Is there a proof of life of this Iran server? (answer, who saw it)"""
    with session() as db:
        s = db.get(Server, sid)

        if s is None or s.role != "iran" or not s.enabled:
            return False, ""

        host, port = s.host, int(s.ssh_port or 22)

    def view(item: tuple[str, Node, str, str]) -> str:
        name, node, iface, inner = item

        try:
            est, ping = parse_view(run_script(node, "peer_alive", {"PEER": host, "IFACE": iface, "INNER": inner}, timeout=20))
        except (SSHError, ValueError):
            return ""          # (it could not be asked, or the Iran server is known by a name, not an address)

        return seen_by_tunnel(name, est, ping)

    ends = _tunnel_ends(sid)
    hits: list[str] = []

    if ends:
        with ThreadPoolExecutor(len(ends)) as ex:
            hits = [h for h in ex.map(view, ends) if h]

    names: list[str] = []

    if not hits:
        vs = _vantages(skip | {sid})

        def one(item: tuple[str, Node]) -> str:
            name, node = item

            try:
                return name if opened(run_script(node, "tcp_reach", {"TARGETS": f"{host}:{port}"}, timeout=20), host, port) else ""
            except (SSHError, ValueError):
                return ""

        if vs:
            with ThreadPoolExecutor(len(vs)) as ex:
                names = [n for n in ex.map(one, vs) if n]

    return decide(hits, names)


def run() -> None:
    want = pulse.missing()

    if not want:
        return

    with session() as db:
        iran = set(db.scalars(select(Server.id).where(Server.role == "iran", Server.enabled.is_(True))))

    for sid in [i for i in want if i in iran][:ASK_LIMIT]:
        try:
            alive, how = ask(sid, set(want))
        except Exception:          # the boundary of one item: a server that cannot be asked must not stop the others
            log.exception("vantage: server #%s could not be asked", sid)
            continue

        if alive and pulse.prove(sid, how):
            log.info("vantage: server #%s is on: %s", sid, how)
