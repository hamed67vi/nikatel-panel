"""Create tunnel with the smart choices.

  precheck(iran_id, foreign_id, ports, live)   before the build: is a wanted port already used on the Iran server (by a tunnel of
                                                the panel or, with live=True, by any process on it), and does the foreign server
                                                already get this port through another tunnel - which the new one may replace
  start(...)                                   the build with the extras: iran_id 0 = pick the Iran server that ranks best for this
                                                foreign server (ping, free resources, how many tunnels it carries, free ports),
                                                replace_ids = tunnels removed first, dns_follow = the CDN records that served only a
                                                replaced tunnel follow the new Iran server (through the failover: asks first, or
                                                automatic). Everything is told in one story; returns its id."""
from __future__ import annotations

import time

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import ActionLog, Server, Tunnel
from ..ssh import Node, SSHError, run_script
from . import autopilot, build_other, builder, detect, failover

log = get_logger("create")
MAX_TRIES = 4          # Iran servers tried one after the other when the build on one fails
_POLL = 2.0


def _ports(text: str) -> list[int]:
    return [p.listen for p in builder.parse_ports(text)]


def precheck(iran_id: int, foreign_id: int, ports: str, live: bool = False) -> dict:
    wanted = set(_ports(ports))

    with session() as db:
        iran = db.get(Server, iran_id) if iran_id else None
        foreign = db.get(Server, foreign_id)

        if foreign is None or foreign.role != "foreign":
            raise ValueError("choose a foreign server")

        if iran_id and (iran is None or iran.role != "iran"):
            raise ValueError("choose an Iran server")

        busy, conflicts = [], []

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            ps = sorted({int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()} & wanted)

            if not ps:
                continue

            if iran is not None and t.iran_server_id == iran.id:
                busy.append({"port": ps[0], "ports": ps, "by": f"tunnel {t.name}", "tunnel": t.name, "state": t.state})

            if t.foreign_server_id == foreign.id:
                conflicts.append({"id": t.id, "name": t.name, "iran_id": t.iran_server_id, "iran": t.iran.name, "state": t.state, "since": t.state_since, "ports": ps, "replaceable": t.state != "up"})

        node = Node(iran.host, iran.ssh_user, iran.ssh_port) if iran is not None else None
        iran_name = iran.name if iran is not None else ""

    error = ""

    if live and node is not None:
        try:
            listening = build_other.parse_simple_facts(run_script(node, "simple_facts", timeout=45))["tcp"]
            known = {p for b in busy for p in b["ports"]}

            for p in sorted(wanted):
                if p in listening and p not in known:
                    busy.append({"port": p, "ports": [p], "by": "a process on the server", "tunnel": "", "state": ""})
        except (SSHError, ValueError) as e:
            error = f"{iran_name} could not be inspected: {str(e)[:120]}"

    return {"iran": iran_name, "busy": busy, "conflicts": conflicts, "error": error, "can_build": not busy}


def _follow(aid: int, build_aid: int, limit: int = 1500) -> bool:
    """Copy the build's story lines into story `aid` as they come; True when the build finished well."""
    seen, end = 0, time.time() + limit

    while time.time() < end:
        time.sleep(_POLL)

        with session() as db:
            e = db.get(ActionLog, build_aid)
            story, done, ok = (list(e.story or []), bool(e.finished_at), bool(e.ok)) if e is not None else ([], True, False)

        for line in story[seen:]:
            autopilot._say(aid, line)

        seen = len(story)

        if done:
            return ok

    autopilot._say(aid, "The build takes too long; look at its own entry in the history.")
    return False


def start(iran_id: int, foreign_id: int, ports: str, kind: str, transport: str, opts: dict, replace_ids: list[int], dns_follow: bool,
          mtu: int = 0, keepalive: int = 75, nodelay: bool = True, measure: str = "full", dns_names: list[str] | None = None) -> int:
    _ports(ports)                                   # a sentence for a bad list, before anything starts
    dns_names = [n.strip().lower() for n in (dns_names or []) if n.strip()] if failover.ENABLED else []
    dns_follow = dns_follow and failover.ENABLED

    with session() as db:
        f = db.get(Server, foreign_id)

        if f is None or f.role != "foreign":
            raise ValueError("choose a foreign server")

        if iran_id:
            i = db.get(Server, iran_id)

            if i is None or i.role != "iran":
                raise ValueError("choose an Iran server")

        fname = f.name
        replace = [(t.id, t.name) for t in db.scalars(select(Tunnel).where(Tunnel.id.in_(replace_ids)))] if replace_ids else []

    if len(replace) != len(set(replace_ids)):
        raise ValueError("a tunnel to replace is not in the list any more")

    def work(aid: int) -> None:
        candidates = [iran_id] if iran_id else []

        if not iran_id:
            autopilot._say(aid, f"Choosing the Iran server: pinging {fname} from every Iran server, looking at load, tunnels and free ports…")
            ranking = detect.auto_iran(foreign_id, ports)
            usable = [r for r in ranking if r.get("score", 0) > 0]

            if not usable:
                autopilot._say(aid, "No Iran server can carry this tunnel right now: " + ("; ".join(f"{r['name']}: {r.get('reason', '')}" for r in ranking[:6]) or "no Iran server is online"), done=True, ok=False)
                return

            candidates = [r["id"] for r in usable[:MAX_TRIES]]
            autopilot._say(aid, "Order (quality first, then free resources): " + ", ".join(f"{r['name']} ({r.get('reason', '')})" for r in usable[:MAX_TRIES]))

        plans = []

        for tid, name in replace:
            if dns_follow:
                try:
                    plans.append(failover.replace_plan(tid, candidates[0]))
                except (ValueError, RuntimeError) as e:
                    autopilot._say(aid, f"DNS of {name}: no plan: {str(e)[:120]}")

            res = builder.delete_tunnel(tid)
            autopilot._say(aid, f"Removed {name}: " + "; ".join(res))

        chosen = 0

        for n, cand in enumerate(candidates):
            with session() as db:
                srv = db.get(Server, cand)
                iname = srv.name if srv is not None else str(cand)

            autopilot._say(aid, f"{'Trying' if n else 'Building'} {kind} · {transport or 'default'} {iname} ⇄ {fname}, ports {ports}")

            try:
                if kind == "backhaul":
                    build_aid = builder.start_build(cand, foreign_id, ports, transport, mtu, keepalive, nodelay=nodelay, opts=opts)
                else:
                    build_aid = build_other.start_build(cand, foreign_id, ports, kind, transport, opts, measure=measure)
            except ValueError as e:
                autopilot._say(aid, f"{iname}: skipped - {str(e)[:140]}")
                continue

            if _follow(aid, build_aid):
                chosen = cand
                break

            autopilot._say(aid, f"{iname}: did not work" + (" - trying the next Iran server" if n < len(candidates) - 1 else ""))

        if not chosen:
            autopilot._say(aid, "The build did not succeed." + (" The removed tunnel(s) are not rebuilt: build again, or on another server." if replace else ""), done=True, ok=False)
            return

        with session() as db:
            final = db.get(Server, chosen)
            target = {"id": final.id, "name": final.name, "ip": final.host}

        for p in plans:
            p["target"] = target                               # the server that finally worked
            failover.after_replace(aid, p, immediate=True)      # the operator ticked it: no second approval

        if dns_names:
            failover.point_names(aid, dns_names, chosen)

        autopilot._say(aid, "Done.", done=True, ok=True)
    return autopilot._start(f"create {ports} → {fname}", work, action="create")
