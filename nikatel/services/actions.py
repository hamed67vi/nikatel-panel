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

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Tunnel
from ..ssh import Node, SSHError, render_action, run_action
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
    with session() as db:
        return len(list(db.scalars(select(ActionLog.id).where(ActionLog.ref == ref, ActionLog.dry_run.is_(False),
                                                              ActionLog.action == "fix", ActionLog.ts >= int(time.time()) - seconds))))


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
        iran_node = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port)
        foreign_node = Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port)

    if not dry_run and s.read_only:
        _say(aid, "The panel is read-only: nothing is changed. Set NIKATEL_READ_ONLY=false on the panel server to allow repairs. Showing the plan as a dry run instead.")
        dry_run = True

    if not dry_run and recent_fixes(name) >= MAX_PER_HOUR:
        _say(aid, f"Not repaired: this tunnel was already repaired {MAX_PER_HOUR} times in the last hour. A human should look at it.", done=True, ok=False)
        return

    with ThreadPoolExecutor(2) as ex:
        fi, ff = ex.submit(_query, iran_node, plan, True), ex.submit(_query, foreign_node, plan, False)
        iran, foreign = fi.result(), ff.result()

    state, reason = judge(plan, iran, foreign)
    _say(aid, f"Found: {reason or 'everything looks fine on both servers'} (state {state})")

    if iran.error or foreign.error:
        _say(aid, "A server does not answer over SSH; restarting the other side cannot help. Nothing done.", done=True, ok=False)
        return

    if state == "up":
        _say(aid, "The tunnel is healthy: nothing to repair.", done=True, ok=True)
        return

    steps = _plan_steps(plan, iran, foreign)

    if not steps:
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
