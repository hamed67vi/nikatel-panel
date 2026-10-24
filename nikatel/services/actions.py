"""Repairs. The only code that changes a server, and it does so through named actions only.

A *fix* is: look at both sides (the same read-only check the monitor uses) → derive a plan →
run the steps one by one → re-check after each → stop at the first that heals the tunnel.
Every fix, dry or real, is written to action_log with the full story. Limits:

* refused while the panel is read-only (`NIKATEL_READ_ONLY=true`);
* at most `MAX_PER_HOUR` real fixes per tunnel per hour;
* never when one of the two servers is unreachable (restarting the other side cannot help);
* auto-repair (from the alerts job) only when the operator switched it on in Settings.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Tunnel
from ..ssh import Node, SSHError, render_action, run_action
from . import ai
from . import settings as st
from .events import emit
from .tunnels import (
    Plan,
    _query,
    check_tunnel,
    external_probe_one,
    judge,
    plan_for,
    servers_off,
)

log = get_logger("actions")
MAX_PER_HOUR = 2
_RUNNING: dict[str, int] = {}
_LOCK = threading.Lock()


ROUTE_FAULT = re.compile(r"no answer from the foreign end over the GRE link|blocked in front of the server")


def is_route_fault(last_error: str) -> bool:
    """The route is silent (the GRE link does not answer, or the port is cut in front of the server): when it was like this before, no restart is tried first - the Doctor goes to the tunnel type that connects."""
    return bool(ROUTE_FAULT.search(last_error or ""))


def grace_for(last_error: str, after_seconds: int) -> int:
    """Pure: how long a down tunnel waits before its plain restart. A tunnel service that is not running (inactive, failed, dead) or a GRE
    interface that is missing is no network blip - it is restarted at the next minute; anything else waits `after_seconds` so a short
    cut is not answered with a restart."""
    return 0 if re.search(r" is (inactive|failed|dead|missing)\b", last_error or "") else int(after_seconds)


RECHECK_FIRST = 4            # seconds before the first look after a restart: a tunnel service dials in within a few seconds ...
RECHECK_EVERY = 5            # ... and between two looks (a look takes a few seconds more); it was 8 and 10, so a healed tunnel was seen up to ten seconds late
RECHECK_GIVE_UP = 2          # a cut that is still the SILENT ROUTE after this many looks is not one a restart cures: the smart repair goes on at once instead of waiting for the rest of the time (about 30 s saved)


def _wake_repair() -> None:
    """Run the repair job now: a restart has ended, so what is due next (the smart repair of this tunnel, the restart of another) starts now."""
    try:
        jobs.trigger("repair")
    except KeyError:
        pass                      # not registered (tests)


def _recheck(tunnel_id: int, plan: Plan, iran_host: str, owner_id: int, tries: int = 7) -> tuple[str, str]:
    """After a restart: wait, ask another Iran server about the users' port again (the cycle's answer is from BEFORE the restart and
    would call a healed tunnel still down), check the tunnel; up to `tries` times, about 40 s in all - a tunnel service needs a few
    seconds to dial in again. The tunnel must be up in TWO checks in a row: one that answers for an instant (the services dial in, a stale connection still carries a
    request) is not healed - the owner, 2026-10-16: "it says fixed and the tunnel is not connected". A tunnel that is still the silent route after RECHECK_GIVE_UP looks stops waiting.
    Returns (state, reason) of the last check."""
    port = int(plan.ports[0]) if plan.ports and str(plan.ports[0]).isdigit() else 0
    state, why, ups = "unknown", "", 0

    for i in range(tries + 1):
        time.sleep(RECHECK_FIRST if i == 0 else RECHECK_EVERY)

        if port:
            external_probe_one(iran_host, port, owner_id)

        from . import bandwidth

        bandwidth.recheck(tunnel_id)       # (a tunnel whose last tests moved no data is measured again now: it is healed when the users get data, not when a check answers)
        check_tunnel(tunnel_id)

        with session() as db:
            t = db.get(Tunnel, tunnel_id)

            if t is None:
                return "removed", "the tunnel was removed meanwhile"

            state, why = t.state, t.last_error or ""

        ups = ups + 1 if state == "up" else 0

        if ups >= 2:
            return state, ""

        if state == "down" and i >= RECHECK_GIVE_UP and is_route_fault(why):
            return state, why

    return ("degraded", "it answered once but not steadily (the next check failed again)") if state == "up" else (state, why)


def _plan_steps(plan: Plan, iran, foreign) -> list[dict]:
    """Which actions make sense, in order. Each step: {action, side, units, why}."""
    steps: list[dict] = []
    gre_i = [u for u in plan.iran_units if u.startswith("nikatel-gre-")]
    gre_f = [u for u in plan.foreign_units if u.startswith("nikatel-gre-")]
    main_i = [u for u in plan.iran_units if not u.startswith("nikatel-gre-")]
    main_f = [u for u in plan.foreign_units if not u.startswith("nikatel-gre-")]
    bad_i = [u for u in plan.iran_units if iran.units.get(u) != "active"]
    bad_f = [u for u in plan.foreign_units if foreign.units.get(u) != "active"]

    if bad_i:
        steps.append({"action": "restart_units", "side": "iran", "units": bad_i, "why": f"{', '.join(bad_i)} not active on the Iran server"})

    if bad_f:
        steps.append({"action": "restart_units", "side": "foreign", "units": bad_f, "why": f"{', '.join(bad_f)} not active on the foreign server"})

    gre_problem = plan.iface and (iran.iface_state not in ("UP", "UNKNOWN") or iran.loss_pct is None or iran.loss_pct >= 100)

    if gre_problem and gre_i and not any(s["units"] == gre_i for s in steps):
        steps.append({"action": "restart_units", "side": "iran", "units": gre_i, "why": "the GRE link does not pass traffic"})
        steps.append({"action": "restart_units", "side": "foreign", "units": gre_f, "why": "the GRE link does not pass traffic"})

    # last resort: the tunnel services on both sides, Iran first (the foreign side dials in)
    if main_i and not any(s["units"] == main_i and s["side"] == "iran" for s in steps):
        steps.append({"action": "restart_units", "side": "iran", "units": main_i, "why": "restart the tunnel service on the Iran side"})

    if main_f and not any(s["units"] == main_f and s["side"] == "foreign" for s in steps):
        steps.append({"action": "restart_units", "side": "foreign", "units": main_f, "why": "restart the tunnel service on the foreign side"})

    return steps


def recent_fixes(ref: str, seconds: int = 3600) -> int:
    """Real repair attempts in the window - a fix that did nothing (refused, nothing to do, a server unreachable) is a "fix-skip"
    and does not count, or a tunnel that stays down would never be restarted again."""
    with session() as db:
        return len(list(db.scalars(select(ActionLog.id).where(ActionLog.ref == ref, ActionLog.dry_run.is_(False),
                                                              ActionLog.action == "fix", ActionLog.ts >= int(time.time()) - seconds))))


def _skip(aid: int) -> None:
    """This fix did nothing: it is not counted against the hourly budget."""
    with session() as db:
        e = db.get(ActionLog, aid)

        if e is not None:
            e.action = "fix-skip"


def fix(tunnel_id: int, source: str = "manual", dry_run: bool = False) -> int:
    """Start a fix; returns the action_log id. The work runs in a thread; poll `status(id)`."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        name = t.name

    with _LOCK:
        if name in _RUNNING:
            return _RUNNING[name]

        with session() as db:
            entry = ActionLog(ref=name, action="fix", source=source, dry_run=dry_run, story=["Looking at both servers…"])
            db.add(entry)
            db.flush()
            aid = entry.id

        _RUNNING[name] = aid

    threading.Thread(target=_fix_worker, args=(tunnel_id, aid, dry_run, source), name=f"fix-{name}", daemon=True).start()
    return aid


def _say(aid: int, line: str, done: bool | None = None, ok: bool | None = None) -> None:
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

    log.info("fix #%s: %s", aid, line)


def _fix_worker(tunnel_id: int, aid: int, dry_run: bool, source: str) -> None:
    try:
        _fix(tunnel_id, aid, dry_run, source)
    except Exception as e:  # the boundary of the thread
        log.exception("fix #%s crashed", aid)
        _say(aid, f"Stopped: {str(e)[:160]}", done=True, ok=False)
    finally:
        with _LOCK:
            for k, v in list(_RUNNING.items()):
                if v == aid:
                    _RUNNING.pop(k, None)

        _wake_repair()                # the restart has ended: the smart repair (or the next restart) is due now, not at the next tick


def _fix(tunnel_id: int, aid: int, dry_run: bool, source: str) -> None:
    s = get_settings()

    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        name, label = t.name, f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"
        plan = plan_for(t)
        prev = dict(t.detail or {})
        bw = dict((t.record or {}).get("bw") or {})
        iran_node = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port)
        foreign_node = Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port)
        iran_host, owner_id = t.iran.host, t.iran_server_id
        off = servers_off(t)

    if off:          # (a server that is off: neither server is asked - an SSH question to a server that is off costs its whole timeout - and nothing is restarted; the owner, 2026-10-24: the Doctor must not do extra work for it)
        _skip(aid)
        _say(aid, "; ".join(off) + ": nothing is asked of the servers and nothing is restarted until it is back.", done=True, ok=False)
        return

    if not dry_run and s.read_only:
        _say(aid, "The panel is read-only: nothing is changed. Set NIKATEL_READ_ONLY=false on the panel server to allow repairs. Showing the plan as a dry run instead.")
        dry_run = True

    if not dry_run and recent_fixes(name) > MAX_PER_HOUR:          # this entry is already in the count
        _skip(aid)
        _say(aid, f"Not repaired: this tunnel was already repaired {MAX_PER_HOUR} times in the last hour. A human should look at it.", done=True, ok=False)
        return

    with ThreadPoolExecutor(2) as ex:
        fi, ff = ex.submit(_query, iran_node, plan, True), ex.submit(_query, foreign_node, plan, False)
        iran, foreign = fi.result(), ff.result()

    # the same rule as the monitor: a tunnel that once passed data and now passes none is not healthy, even when every service is active
    data_seen = bool(prev.get("data_seen")) or iran.data_code not in ("", "000")
    data_fails = int(prev.get("data_fails", 0)) + 1 if iran.data_code == "000" else 0
    from . import bandwidth

    state, reason = judge(plan, iran, foreign, data_seen, data_fails, stalled=bandwidth.stalled(bw, int(time.time())))
    _say(aid, f"Found: {reason or 'everything looks fine on both servers'} (state {state})")

    if iran.error or foreign.error:
        _skip(aid)
        _say(aid, "A server does not answer over SSH; restarting the other side cannot help. Nothing done.", done=True, ok=False)
        return

    if state == "up":
        _skip(aid)
        _say(aid, "The tunnel is healthy: nothing to repair.", done=True, ok=True)
        return

    steps = _plan_steps(plan, iran, foreign)

    # the AI assistant (when switched on in Settings) reads the same facts and may add a safe step
    advice = ai.advise(json.dumps({"tunnel": {"name": name, "type": plan.iface and "backhaul-gre"}, "reason": reason, "state": state,
                                   "iran": {"units": iran.units, "iface": iran.iface_state, "loss_pct": iran.loss_pct, "rtt_ms": iran.rtt_ms, "listen": iran.listen},
                                   "foreign": {"units": foreign.units}}, ensure_ascii=False))

    if advice:
        _say(aid, "AI: " + (advice["diagnosis"] or "no diagnosis"))
        gre_i = [u for u in plan.iran_units if u.startswith("nikatel-gre-")]
        gre_f = [u for u in plan.foreign_units if u.startswith("nikatel-gre-")]

        if ai.conf().get("actions"):
            for a in advice["actions"]:
                if a["action"] in ("restart_gre", "restart_both") and gre_i and not any(s_["units"] == gre_i for s_ in steps):
                    steps.insert(0, {"action": "restart_units", "side": "iran", "units": gre_i, "why": "AI: " + a["reason"]})
                    steps.insert(1, {"action": "restart_units", "side": "foreign", "units": gre_f, "why": "AI: " + a["reason"]})
                elif a["action"] in ("restart_services", "restart_both"):
                    for side, units in (("iran", [u for u in plan.iran_units if u not in gre_i]), ("foreign", [u for u in plan.foreign_units if u not in gre_f])):
                        if units and not any(s_["units"] == units and s_["side"] == side for s_ in steps):
                            steps.append({"action": "restart_units", "side": side, "units": units, "why": "AI: " + a["reason"]})
                elif a["action"] in ("rebuild", "notify"):
                    _say(aid, f"AI suggests {a['action']}: {a['reason']} (not done automatically)")

    if not steps:
        _skip(aid)
        _say(aid, "No safe step applies to this problem. A human should look at it.", done=True, ok=False)
        return

    _say(aid, "Plan: " + " → ".join(f"{st_['side']}: restart {', '.join(st_['units'])}" for st_ in steps))

    if dry_run:
        for st_ in steps:
            _say(aid, f"[dry run] on the {st_['side']} server:\n" + render_action(st_["action"], {"UNITS": " ".join(st_["units"])}))

        _say(aid, "Dry run finished: nothing was changed.", done=True, ok=True)
        return

    with session() as db:
        emit(db, "warn", "actions", f"Repair of {label} started ({source}): {reason}", name)

    batch = state == "down"        # a tunnel that is cut loses nothing by being restarted whole: every step is run, then it is checked once (four restarts with a look after each took three minutes)

    for st_ in steps:
        node = iran_node if st_["side"] == "iran" else foreign_node

        try:
            out = run_action(node, st_["action"], {"UNITS": " ".join(st_["units"])})
            states = ", ".join(line.split()[1] + "=" + line.split()[2] for line in out.splitlines() if line.startswith("UNIT ") and len(line.split()) >= 3)
            _say(aid, f"{st_['side']}: restarted {', '.join(st_['units'])} because {st_['why']} → {states or 'no answer'}")
        except (SSHError, ValueError) as e:
            _say(aid, f"{st_['side']}: restart of {', '.join(st_['units'])} failed: {e}")
            continue

        if batch:
            continue

        now_state, now_reason = _recheck(tunnel_id, plan, iran_host, owner_id)

        if now_state == "up":
            _say(aid, "Checked again: the tunnel works.", done=True, ok=True)

            with session() as db:
                emit(db, "ok", "actions", f"Repair of {label} succeeded.", name)

            return

        _say(aid, f"Checked again: still {now_state} ({now_reason}). Next step.")

    if batch:
        now_state, now_reason = _recheck(tunnel_id, plan, iran_host, owner_id, tries=6)

        if now_state == "up":
            _say(aid, "Checked again: the tunnel works.", done=True, ok=True)

            with session() as db:
                emit(db, "ok", "actions", f"Repair of {label} succeeded.", name)

            return

        _say(aid, f"Checked again: still {now_state} ({now_reason}).")

    _say(aid, "Every step was tried; the tunnel is still not up. The smart repair goes on from here.", done=True, ok=False)

    with session() as db:
        emit(db, "bad", "actions", f"Repair of {label} did not help.", name)


def status(aid: int) -> dict | None:
    with session() as db:
        e = db.get(ActionLog, aid)

        if e is None:
            return None

        from .diagnosis import summarize

        return {"id": e.id, "ref": e.ref, "action": e.action, "source": e.source, "dry_run": e.dry_run, "ok": e.ok,
                "done": e.finished_at > 0, "story": list(e.story or []), "ts": e.ts, "finished_at": e.finished_at,
                "summary": summarize(list(e.story or []), e.ok, e.action, e.ref)}


def history(ref: str | None = None, limit: int = 50, kinds: list[str] | None = None) -> list[dict]:
    with session() as db:
        q = select(ActionLog).order_by(ActionLog.id.desc()).limit(limit)

        if ref:
            q = q.where(ActionLog.ref == ref)

        if kinds:
            q = q.where(ActionLog.action.in_(kinds))      # the dozens of "look" runs would otherwise push the repairs out of the newest 80

        return [status(e.id) for e in db.scalars(q)]


def doctor_mode() -> str:
    """The Tunnel Doctor's mode: off | fix | move (an old setting without a mode: "fix" when the automatic repair was on, else "off")."""
    conf = st.get("repair")
    m = conf.get("mode")

    if m in ("off", "fix", "move"):
        return m

    return "fix" if conf.get("auto", True) else "off"


DOWN_GRACE = 0               # seconds a tunnel that is down waits before its plain restart: NONE. The Doctor starts at the first moment a tunnel is down (the owner, 2026-10-17: "remove the 60 seconds - the Doctor must
                             # start from the first moment the tunnel has trouble"). What stays is what already protected a blip: a tunnel is *down* only when a check found a unit stopped, the card gone, the GRE link
                             # silent over twenty pings, or the users' path cut for four checks - an SSH hiccup is "unknown", never "down" - and ONE plain restart is made per cut


def auto_repair() -> None:
    """Job hook: when the Doctor is not off, start a fix for tunnels that are down and not muted."""
    if doctor_mode() == "off" or get_settings().read_only:
        return

    muted = set(st.get("alerts").get("muted") or [])

    from . import knowledge

    now = int(time.time())

    with session() as db:
        down = [(t.id, t.name, t.state_since, t.last_error or "") for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), Tunnel.state == "down")) if not servers_off(t)       # a server that is off: nothing to restart until it is back
                and not (is_route_fault(t.last_error) and knowledge.cut_before(t.iran_server_id, t.foreign_server_id, ("gre_blocked", "route_cut"), now))]       # a route that was filtered before: no restart, the smart repair builds what connects

    down.sort(key=lambda r: r[2])

    for tid, name, since, why in down:
        if len(_RUNNING) >= 4:                       # servers coming back together: a few restarts at a time, the rest next minute
            break

        if name in muted or now - since < grace_for(why, DOWN_GRACE) or name in _RUNNING or recent_fixes(name, max(3600, now - since + 60)) >= 1:        # ONE plain restart per cut (a second one only delayed the smart repair by a minute and a half in a real test)
            continue

        log.info("auto repair of %s (down for %ss)", name, now - since)
        fix(tid, source="auto")


def restart_now(tunnel_id: int, source: str = "manual") -> dict:
    """Restart the tunnel service on both sides (the Iran end first, then the foreign end; the GRE link is left alone), wait a few
    seconds and check the tunnel again. Refused while the panel is read-only or when a server does not answer."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only: nothing is changed")

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        plan, name = plan_for(t), t.name
        iran_node, foreign_node = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port)
        iran_host, owner_id = t.iran.host, t.iran_server_id

    def main(units):
        return [u for u in units if not u.startswith("nikatel-gre-")] or list(units)

    done = []

    for side, node, units in (("Iran", iran_node, main(plan.iran_units)), ("foreign", foreign_node, main(plan.foreign_units))):
        try:
            out = run_action(node, "restart_units", {"UNITS": " ".join(units)})
            bad = [ln.split()[1] for ln in out.splitlines() if ln.startswith("UNIT ") and len(ln.split()) >= 3 and ln.split()[2] != "active"]
            done.append(f"{side}: " + ("restarted" if not bad else "not active after the restart: " + ", ".join(bad)))
        except (SSHError, ValueError) as e:
            done.append(f"{side}: {str(e)[:80]}")

    state, why = _recheck(tunnel_id, plan, iran_host, owner_id, tries=3)

    with session() as db:
        emit(db, "ok" if state == "up" else "warn", "actions", f"Restart of {name} ({source}): {state}" + (f" - {why}" if why else ""), name)

    return {"id": tunnel_id, "name": name, "state": state, "why": why, "steps": done}


def restart_all() -> list[dict]:
    """restart_now on every enabled tunnel, four at a time (every server is touched by only a few of them at once)."""
    with session() as db:
        ids = [t.id for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))]

    def one(tid):
        try:
            return restart_now(tid, "restart all")
        except (ValueError, SSHError) as e:
            return {"id": tid, "name": str(tid), "state": "unknown", "why": str(e)[:100], "steps": []}

    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, ids))


def _states(rows: list[dict]) -> str:
    return " · ".join(f"{sum(1 for r in rows if r.get(chr(115) + chr(116) + chr(97) + chr(116) + chr(101)) == k)} {label}" for k, label in (("up", "up"), ("degraded", "unstable"), ("down", "down"), ("unknown", "unknown")))


def health_all_start(source: str = "manual") -> int:
    """The health check of every tunnel as an action (202 now; the states in the story when it is done). Read only."""
    from . import autopilot

    def work(aid: int) -> None:
        rows = health_all()

        for r in rows:
            if r["state"] != "up":
                _say(aid, f"{r[chr(110) + chr(97) + chr(109) + chr(101)]}: {r[chr(115) + chr(116) + chr(97) + chr(116) + chr(101)]}" + (" - " + r["why"] if r.get("why") else ""))

        _say(aid, f"Checked {len(rows)} tunnel" + ("s" if len(rows) != 1 else "") + f": {_states(rows)}.", done=True, ok=True)

    return autopilot._start("health of all tunnels", work, action="health", source=source, allow_read_only=True)


def restart_all_start(source: str = "manual") -> int:
    """Restart every tunnel on both servers, four at a time, as an action (needs read-only off)."""
    from . import autopilot

    def work(aid: int) -> None:
        rows = restart_all()

        for r in rows:
            if r["state"] != "up":
                _say(aid, f"{r[chr(110) + chr(97) + chr(109) + chr(101)]}: {r[chr(115) + chr(116) + chr(97) + chr(116) + chr(101)]}" + (" - " + r["why"] if r.get("why") else ""))

        _say(aid, f"Restarted {len(rows)} tunnel" + ("s" if len(rows) != 1 else "") + f": {_states(rows)}.", done=True, ok=not any(r["state"] == "down" for r in rows))

    return autopilot._start("restart of all tunnels", work, action="restart", source=source)


def health_all() -> list[dict]:
    """check_tunnel on every enabled tunnel, eight at a time; the list of states afterwards."""
    with session() as db:
        ids = [t.id for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))]

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(check_tunnel, ids))

    with session() as db:
        return [{"id": t.id, "name": t.name, "state": t.state, "why": t.last_error or ""} for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))]
