"""The sentinel: a tunnel that is UP and loses a unit, its GRE card or its GRE peer is looked at within seconds, not at the next turn of the minute cycle.
(The owner, 2026-10-17: "bring the delay down to the minimum" - the cycle that checks every tunnel runs once a minute, so a tunnel that was up and fell could stand there, called healthy, for up to a minute.)

  what it asks      every 10 s, ONE light command per server for ALL its tunnels (ssh.py `sentinel`): the state of every unit, the state of every GRE card (Iran side) and three quick pings of every GRE peer, in parallel.
                    It never changes a state itself.
  what it decides   a tunnel that is up (or degraded) and shows one of these signs gets a full check at once (`tunnels.check_tunnel`, with a fresh look from another Iran server), which judges by the usual rules:
                    a unit that is not active (either end), a GRE card that is not up, a GRE peer that answers none of the three pings (a tunnel whose provider drops ICMP is left out: its pings say nothing)
  what it leaves    the tunnels that are not up (the fast lane already looks at them every ~8 s), the tunnels a repair is working on right now, the test tunnels, a server that is off (the pulse knows),
                    a server that could not be asked (no sign is read from nothing)
  how often         a tunnel is looked at for these signs at most once in COOLDOWN seconds: a sign that stays while the check says "up" (a card renamed, an ICMP filter not seen yet) costs one check a half minute, not six

The pure helpers (`parse`, `suspects`, `plan_reads`) carry the decisions and are tested."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import select

from .. import jobs
from ..db import session
from ..log import get_logger
from ..models import Tunnel, is_temp
from ..ssh import Node, SSHError, run_light
from . import tunnels

log = get_logger("sentinel")
TIMEOUT = 8                  # one read of one server
COOLDOWN = 30                # a tunnel is looked at for these signs at most this often
MAX_CHECKS = 6               # full checks started in one turn (the rest wait for the next one)
WORKERS = 4                  # ... at most this many at once
PER_SERVER = 40              # units asked of one server in one command
_LAST: dict[int, float] = {}         # tunnel -> when it was last looked at because of a sign
_HEARD: dict[int, float] = {}        # server -> when it last answered the sentinel


@dataclass
class Watch:
    """What the sentinel needs to know of one tunnel."""
    id: int
    name: str
    iran_id: int
    foreign_id: int
    units_i: list[str] = field(default_factory=list)
    units_f: list[str] = field(default_factory=list)
    iface: str = ""
    peer: str = ""
    ping_blocked: bool = False


# ------------------------------------------------------------------ pure
def parse(out: str) -> dict:
    """Pure: the answer of the `sentinel` script -> {"units": {unit: state}, "ifaces": {card: state ("" = no such card)}, "peers": {ip: loss %}}."""
    units: dict[str, str] = {}
    ifaces: dict[str, str] = {}
    peers: dict[str, float] = {}

    for line in str(out).splitlines():
        p = line.split()

        if len(p) >= 3 and p[0] == "UNIT":
            units[p[1]] = p[2]
        elif len(p) >= 2 and p[0] == "IFACE":
            ifaces[p[1]] = p[2] if len(p) >= 3 and p[2].isalpha() else ""
        elif len(p) == 3 and p[0] == "PEER":
            try:
                peers[p[1]] = float(p[2])
            except ValueError:
                continue

    return {"units": units, "ifaces": ifaces, "peers": peers}


def suspects(w: Watch, iran: dict | None, foreign: dict | None) -> list[str]:
    """Pure: the signs that say a tunnel that is up is not (empty: nothing to look at). A side that could not be read says nothing: the pulse and the check handle a server that does not answer."""
    out: list[str] = []

    for side, label, units in ((iran, "Iran", w.units_i), (foreign, "foreign", w.units_f)):
        if side is None:
            continue

        for u in units:
            st = side["units"].get(u)

            if st is not None and st != "active":
                out.append(f"{label}: {u} is {st}")

    if iran is not None and w.iface:
        st = iran["ifaces"].get(w.iface)

        if st is not None and st not in ("UP", "UNKNOWN"):
            out.append(f"the GRE interface {w.iface} is {st or 'missing'} on the Iran server")

    if iran is not None and w.iface and w.peer and not w.ping_blocked:
        loss = iran["peers"].get(w.peer)

        if loss is not None and loss >= 100:
            out.append("no answer from the foreign end over the GRE link")

    return out


def plan_reads(watches: list[Watch]) -> dict[int, dict[str, list[str]]]:
    """Pure: what to ask each server - {server id: {"units": [...], "ifaces": [...], "peers": [...]}} - the units of both ends of every tunnel on the server that runs them, the GRE cards and peers on the Iran
    side. Every list is without repeats and in order; a server is asked for at most PER_SERVER units (the tunnels beyond that are looked at by the minute cycle only)."""
    need: dict[int, dict[str, list[str]]] = {}

    def add(sid: int, key: str, items: list[str]) -> None:
        d = need.setdefault(sid, {"units": [], "ifaces": [], "peers": []})

        for x in items:
            if x and x not in d[key]:
                d[key].append(x)

    for w in watches:
        add(w.iran_id, "units", w.units_i)
        add(w.foreign_id, "units", w.units_f)

        if w.iface:
            add(w.iran_id, "ifaces", [w.iface])

            if w.peer and not w.ping_blocked:
                add(w.iran_id, "peers", [w.peer])

    for d in need.values():
        d["units"] = d["units"][:PER_SERVER]

    return need


# ------------------------------------------------------------------ the job
def _load() -> tuple[list[Watch], dict[int, Node]]:
    with session() as db:
        watches: list[Watch] = []
        nodes: dict[int, Node] = {}

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), Tunnel.state.in_(("up", "degraded")))):
            if is_temp(t.name) or t.iran is None or t.foreign is None:
                continue

            if any(s.enabled and s.online is False for s in (t.iran, t.foreign)):
                continue                                      # a server that is off: the pulse knows, there is nothing to read from it

            plan = tunnels.plan_for(t)
            det = t.detail or {}
            watches.append(Watch(t.id, t.name, t.iran_server_id, t.foreign_server_id, list(plan.iran_units), list(plan.foreign_units), plan.iface,
                                 plan.iran_peer if plan.iface else "", bool(det.get("ping_blocked")) or det.get("ping_over") == "udp"))

            for s in (t.iran, t.foreign):
                nodes.setdefault(s.id, Node(s.host, s.ssh_user, s.ssh_port))

    return watches, nodes


def _read_one(node: Node, ask: dict[str, list[str]]) -> dict | None:
    try:
        return parse(run_light(node, "sentinel", {"UNITS": " ".join(ask["units"]), "IFACES": " ".join(ask["ifaces"]), "PEERS": " ".join(ask["peers"])}, timeout=TIMEOUT))
    except (SSHError, ValueError) as e:
        log.debug("sentinel: a server could not be read: %s", str(e)[:100])
        return None


def run() -> None:
    """The job (every 10 s): read every server once, and look at once at the tunnels that show a sign."""
    if jobs.stopping():
        return

    watches, nodes = _load()

    if not watches:
        return

    asks = plan_reads(watches)

    if not asks:
        return

    with ThreadPoolExecutor(min(8, len(asks))) as ex:
        sids = list(asks)
        reads = dict(zip(sids, ex.map(lambda sid: _read_one(nodes[sid], asks[sid]) if asks[sid]["units"] else None, sids)))

    now = time.time()

    for sid, r in reads.items():
        if r is not None:
            _HEARD[sid] = now

    from . import autopilot

    busy = set(autopilot.repairing_names())
    todo: list[tuple[Watch, list[str]]] = []

    for w in watches:
        if w.name in busy or now - _LAST.get(w.id, 0.0) < COOLDOWN:
            continue

        signs = suspects(w, reads.get(w.iran_id), reads.get(w.foreign_id))

        if signs:
            todo.append((w, signs))

    todo = todo[:MAX_CHECKS]

    if not todo:
        return

    for w, signs in todo:
        _LAST[w.id] = now
        log.info("sentinel: %s: %s - checking it now", w.name, "; ".join(signs[:3]))

    with ThreadPoolExecutor(min(WORKERS, len(todo))) as ex:
        list(ex.map(lambda x: tunnels.check_tunnel(x[0].id, fresh_ext=True), todo))
