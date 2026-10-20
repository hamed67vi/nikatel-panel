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

import re
import time

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import ActionLog, Server, Tunnel
from ..ssh import Node, SSHError, run_script
from . import autopilot, build_other, builder, detect, failover, tunnel_test

log = get_logger("create")
MAX_TRIES = 4          # Iran servers tried one after the other when the build on one fails
_POLL = 2.0


def _ports(text: str) -> list[int]:
    return [p.listen for p in builder.parse_ports(text)]


def parse_owner(out: str) -> dict:
    """Pure: the answer of the port_owner script -> {pid, process, unit} (empty when nobody listens or it can not be told)."""
    m = re.search(r"OWNER (\S+) (\S+) (\S+)", str(out))

    if not m:
        return {}

    pid, comm, unit = m.groups()
    return {"pid": pid if pid != "-" else "", "process": comm if comm != "-" else "", "unit": unit if unit != "-" else ""}


def parse_backhaul_unit(unit: str) -> tuple[str, int] | None:
    """Pure: the transport and the control port of an Iran-side backhaul unit (backhaul-iran-tcpmux-9921.service gives tcpmux and 9921); None for any other unit."""
    m = re.fullmatch(r"backhaul-iran-(tcp|tcpmux|wsmux|wssmux)-(\d{1,5})\.service", unit or "")
    return (m.group(1), int(m.group(2))) if m else None


def is_dead_tunnel(connected: bool, tunnel_state: str) -> bool:
    """Pure: an Iran-side backhaul server that holds a port is DEAD - safe to replace - when no client is connected to its control port and the panel does not
    list it as a tunnel that is up. (A panel tunnel that is down or unknown, or a tunnel the panel does not know at all, with nobody connected.)"""
    return not connected and tunnel_state not in ("up", "degraded", "starting")


def identify_owner(node: Node, server_id: int, port: int) -> dict:
    """Who holds this port on the Iran server, and - when it is a backhaul tunnel server - whether that tunnel is dead. Never raises."""
    try:
        info = parse_owner(run_script(node, "port_owner", {"PORT": str(port)}, timeout=30))
    except (SSHError, ValueError):
        return {}

    bh = parse_backhaul_unit(info.get("unit", ""))

    if not bh:
        return info

    tr, ctl = bh

    try:
        connected = bool(run_script(node, "estab_check", {"PORT": str(ctl)}, timeout=20).strip())
    except (SSHError, ValueError):
        return info                                   # can not tell: it is not called dead

    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.iran_server_id == server_id, Tunnel.port == ctl))
        name, state = (t.name, t.state) if t is not None else ("", "")

    info.update(transport=tr, ctl=ctl, connected=connected, tunnel=name, state=state, dead=is_dead_tunnel(connected, state))
    return info


def precheck(iran_id: int, foreign_id: int, ports: str, live: bool = False) -> dict:
    wanted = set(_ports(ports))

    with session() as db:
        iran = db.get(Server, iran_id) if iran_id else None
        foreign = db.get(Server, foreign_id)

        if foreign is None or foreign.role != "foreign":
            raise ValueError("choose a foreign server")

        if iran_id and (iran is None or iran.role != "iran"):
            raise ValueError("choose an Iran server")

        busy, conflicts, same_foreign, stale = [], [], [], []

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            if t.foreign_server_id == foreign.id:       # every tunnel that already ends on this foreign server, with or without a common port: the form asks what to do with it
                same_foreign.append({"id": t.id, "name": t.name, "iran": t.iran.name, "state": t.state, "ports": [int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()]})

            ps = sorted({int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()} & wanted)

            if not ps:
                continue

            if iran is not None and t.iran_server_id == iran.id:
                busy.append({"port": ps[0], "ports": ps, "by": f"tunnel {t.name}", "tunnel": t.name, "id": t.id, "state": t.state, "all_ports": sorted(int(x) for x in (t.forwarded_ports or []) if str(x).isdigit())})      # a tunnel of the panel: the form may offer to replace it

            if t.foreign_server_id == foreign.id:
                conflicts.append({"id": t.id, "name": t.name, "iran_id": t.iran_server_id, "iran": t.iran.name, "state": t.state, "since": t.state_since, "ports": ps, "replaceable": t.state != "up"})

        node = Node(iran.host, iran.ssh_user, iran.ssh_port) if iran is not None else None
        iran_name = iran.name if iran is not None else ""
        foreign_name = foreign.name

    error = ""

    if live and node is not None:
        try:
            listening = build_other.parse_simple_facts(run_script(node, "simple_facts", timeout=45))["tcp"]
            known = {p for b in busy for p in b["ports"]}

            for p in sorted(wanted):
                if p in listening and p not in known:
                    own = identify_owner(node, iran_id, p)

                    if own.get("dead"):             # an old tunnel of ours that nobody connects to any more: it can be replaced
                        stale.append({"port": p, "unit": own["unit"], "transport": own["transport"], "ctl": own["ctl"], "tunnel": own.get("tunnel", ""),
                                      "why": (f"the tunnel {own['tunnel']} is {own.get('state') or 'down'}" if own.get("tunnel") else "a tunnel the panel does not know") + " and nobody is connected to it"})
                    else:
                        who = (own.get("process") or "a process") + (f" ({own['unit']})" if own.get("unit") else "") + ("; its tunnel is connected" if own.get("connected") else "")
                        busy.append({"port": p, "ports": [p], "by": who if own else "a process on the server", "tunnel": "", "state": ""})
        except (SSHError, ValueError) as e:
            error = f"{iran_name} could not be inspected: {str(e)[:120]}"

    return {"iran": iran_name, "foreign": foreign_name, "busy": busy, "stale": stale, "conflicts": conflicts, "same_foreign": same_foreign, "error": error, "can_build": not busy}


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


PASS_SCORE = 50          # a pair that scores at least this is good enough: the Smart test stops there


def smart_test(foreign_id: int, ports: str, kind: str) -> int:
    """"Test this pair first" with Smart as the Iran server: the Iran servers are tried in the order of the ranking (quality first, then resources); a server whose
    test is poor is left and the next one is tested; the first that scores well is the answer (else the best of those tried). One story, nothing is built for real."""
    _ports(ports)

    with session() as db:
        f = db.get(Server, foreign_id)

        if f is None or f.role != "foreign":
            raise ValueError("choose a foreign server")

        fname = f.name

    def work(aid: int) -> None:
        autopilot._say(aid, f"Smart test: ranking the Iran servers for {fname} (ping, load, tunnels, free ports)…")
        usable = [r for r in detect.auto_iran(foreign_id, ports) if r.get("score", 0) > 0][:MAX_TRIES]

        if not usable:
            autopilot._say(aid, "No Iran server can carry this tunnel right now.", done=True, ok=False)
            return

        autopilot._say(aid, "Order (GRE not OK last; quality first, then free resources): " + ", ".join(f"{r['name']} ({r.get('reason', '')})" for r in usable))
        tried = []

        for n, r in enumerate(usable):
            autopilot._say(aid, f"Testing {r['name']} ({n + 1}/{len(usable)}): a temporary tunnel is built, measured and removed…")
            res = None

            for _ in range(60):                                   # only one tunnel test runs at a time: wait for the turn
                try:
                    jid = tunnel_test.compare_start(r["id"], foreign_id, [kind], measure="full", install_wg=True)      # (the owner's own Smart test: a WireGuard kind may install wireguard-tools)
                    break
                except (ValueError, RuntimeError) as e:
                    if "still running" not in str(e):
                        autopilot._say(aid, f"{r['name']}: not tested - {str(e)[:120]}")
                        jid = None
                        break

                    time.sleep(10)
            else:
                jid = None

            end = time.time() + 900

            while jid and time.time() < end:
                time.sleep(5)
                job = tunnel_test.compare_status(jid)

                if job["done"]:
                    res = (job.get("results") or [None])[0]
                    break

            if not res or res.get("score") is None:
                autopilot._say(aid, f"{r['name']}: the test gave no result" + (" - trying the next Iran server" if n < len(usable) - 1 else ""))
                continue

            tried.append((res.get("score") or 0, r["name"]))
            line = f"{r['name']}: {res.get('latency_ms')} ms, {res.get('loss_pct')} % loss" + (f", ↓{res['down_mbps']} Mb/s" if res.get("down_mbps") is not None else "") + f", score {res.get('score')}/100"

            if (res.get("score") or 0) >= PASS_SCORE:
                autopilot._say(aid, line + " - good")
                autopilot._say(aid, f"Best server: {r['name']}", done=True, ok=True)
                return

            autopilot._say(aid, line + (" - poor, trying the next Iran server" if n < len(usable) - 1 else " - poor"))

        if tried:
            score, name = max(tried)
            autopilot._say(aid, f"No server scored {PASS_SCORE}+; the best of those tested is {name} ({score}/100).")
            autopilot._say(aid, f"Best server: {name}", done=True, ok=False)
        else:
            autopilot._say(aid, "No server could be tested.", done=True, ok=False)

    return autopilot._start(f"smart test → {fname}", work, action="create")


def start(iran_id: int, foreign_id: int, ports: str, kind: str, transport: str, opts: dict, replace_ids: list[int], dns_follow: bool,
          mtu: int = 0, keepalive: int = 75, nodelay: bool = True, measure: str = "full", dns_names: list[str] | None = None, replace_units: list[str] | None = None, fallback_kinds: list[str] | None = None) -> int:
    _ports(ports)                                   # a sentence for a bad list, before anything starts
    replace_units = [u for u in (replace_units or []) if parse_backhaul_unit(u)]
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
            autopilot._say(aid, "Order (GRE not OK last; quality first, then free resources): " + ", ".join(f"{r['name']} ({r.get('reason', '')})" for r in usable[:MAX_TRIES]))

        for unit in replace_units if candidates and candidates[0] else []:          # an old Iran-side tunnel that holds a port and that nobody connects to: checked again now, then removed
            _, ctl = parse_backhaul_unit(unit)

            with session() as db:
                srv = db.get(Server, candidates[0])
                node = Node(srv.host, srv.ssh_user, srv.ssh_port)

            try:
                if run_script(node, "estab_check", {"PORT": str(ctl)}, timeout=20).strip():
                    autopilot._say(aid, f"{unit} is connected to a foreign server right now: it is NOT removed (the port stays busy).")
                    continue

                from ..ssh import run_action

                autopilot._say(aid, f"Removing the dead tunnel {unit}: " + run_action(node, "remove_backhaul_unit", {"UNIT": unit}, timeout=60).strip()[-60:])
            except (SSHError, ValueError) as e:
                autopilot._say(aid, f"Could not remove {unit}: {str(e)[:100]}")

        plans = []

        for tid, name in replace:
            if dns_follow:
                try:
                    plans.append(failover.replace_plan(tid, candidates[0]))
                except (ValueError, RuntimeError) as e:
                    autopilot._say(aid, f"DNS of {name}: no plan: {str(e)[:120]}")

            res = builder.delete_tunnel(tid, trash=True)
            autopilot._say(aid, f"Removed {name}: " + "; ".join(res))

        chosen = 0
        attempts = [(kind, transport, opts)] + [tunnel_test.build_args(k) for k in (fallback_kinds or [])]          # ("Best of the day": the next best types of the pair, when the first one does not work)

        for n, cand in enumerate(candidates):
            with session() as db:
                srv = db.get(Server, cand)
                iname = srv.name if srv is not None else str(cand)

            for k_n, (a_kind, a_transport, a_opts) in enumerate(attempts):
                autopilot._say(aid, f"{'Trying' if n or k_n else 'Building'} {a_kind} · {a_transport or 'default'} {iname} ⇄ {fname}, ports {ports}")

                try:
                    if a_kind == "backhaul":
                        build_aid = builder.start_build(cand, foreign_id, ports, a_transport, mtu, keepalive, nodelay=nodelay, opts=a_opts)
                    else:
                        build_aid = build_other.start_build(cand, foreign_id, ports, a_kind, a_transport, a_opts, measure=measure)
                except ValueError as e:
                    autopilot._say(aid, f"{iname}: skipped - {str(e)[:140]}")
                    break

                if _follow(aid, build_aid):
                    chosen = cand
                    break

                what = f" {a_kind} · {a_transport or 'default'}" if len(attempts) > 1 else ""
                autopilot._say(aid, f"{iname}:{what} did not work" + (" - trying the next type" if k_n < len(attempts) - 1 else " - trying the next Iran server" if n < len(candidates) - 1 else ""))

            if chosen:
                break

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
