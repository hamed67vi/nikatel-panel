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
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Tunnel
from ..ssh import Node, SSHError, render_action, run_action
from . import ai
from . import settings as st
from .events import emit
from .tunnels import Plan, _query, check_tunnel, judge, plan_for

log = get_logger("actions")
MAX_PER_HOUR = 2
_RUNNING: dict[str, int] = {}
_LOCK = threading.Lock()


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


def _fix(tunnel_id: int, aid: int, dry_run: bool, source: str) -> None:
    s = get_settings()

    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        name, label = t.name, f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"
        plan = plan_for(t)
        prev = dict(t.detail or {})
        iran_node = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port)
        foreign_node = Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port)

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
    state, reason = judge(plan, iran, foreign, data_seen, data_fails)
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

    for st_ in steps:
        node = iran_node if st_["side"] == "iran" else foreign_node

        try:
            out = run_action(node, st_["action"], {"UNITS": " ".join(st_["units"])})
            states = ", ".join(line.split()[1] + "=" + line.split()[2] for line in out.splitlines() if line.startswith("UNIT ") and len(line.split()) >= 3)
            _say(aid, f"{st_['side']}: restarted {', '.join(st_['units'])} because {st_['why']} → {states or 'no answer'}")
        except (SSHError, ValueError) as e:
            _say(aid, f"{st_['side']}: restart of {', '.join(st_['units'])} failed: {e}")
            continue

        time.sleep(8)
        check_tunnel(tunnel_id)

        with session() as db:
            t = db.get(Tunnel, tunnel_id)
            now_state, now_reason = t.state, t.last_error

        if now_state == "up":
            _say(aid, "Checked again: the tunnel works.", done=True, ok=True)

            with session() as db:
                emit(db, "ok", "actions", f"Repair of {label} succeeded.", name)

            return

        _say(aid, f"Checked again: still {now_state} ({now_reason}). Next step.")

    _say(aid, "Every step was tried; the tunnel is still not up. A human should look at it.", done=True, ok=False)

    with session() as db:
        emit(db, "bad", "actions", f"Repair of {label} did not help.", name)


def status(aid: int) -> dict | None:
    with session() as db:
        e = db.get(ActionLog, aid)

        if e is None:
            return None

        return {"id": e.id, "ref": e.ref, "action": e.action, "source": e.source, "dry_run": e.dry_run, "ok": e.ok,
                "done": e.finished_at > 0, "story": list(e.story or []), "ts": e.ts, "finished_at": e.finished_at}


def history(ref: str | None = None, limit: int = 50) -> list[dict]:
    with session() as db:
        q = select(ActionLog).order_by(ActionLog.id.desc()).limit(limit)

        if ref:
            q = q.where(ActionLog.ref == ref)

        return [status(e.id) for e in db.scalars(q)]


def auto_repair() -> None:
    """Job hook: when the operator enabled it, start a fix for tunnels that are down and not muted."""
    conf = st.get("repair")

    if not conf.get("auto") or get_settings().read_only:
        return

    muted = set(st.get("alerts").get("muted") or [])

    with session() as db:
        down = [(t.id, t.name, t.state_since) for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), Tunnel.state == "down"))]

    now = int(time.time())

    for tid, name, since in down:
        if name in muted or now - since < int(conf.get("after_seconds", 180)) or name in _RUNNING or recent_fixes(name) >= MAX_PER_HOUR:
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

    time.sleep(6)
    check_tunnel(tunnel_id)

    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        state, why = t.state, t.last_error or ""
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


def health_all() -> list[dict]:
    """check_tunnel on every enabled tunnel, eight at a time; the list of states afterwards."""
    with session() as db:
        ids = [t.id for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))]

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(check_tunnel, ids))

    with session() as db:
        return [{"id": t.id, "name": t.name, "state": t.state, "why": t.last_error or ""} for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))]
