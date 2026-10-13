"""Check the state of all the servers in one go: do they answer, does GRE work to them, are they filtered in Iran.

It runs as ONE action on the panel server (not in the browser): reloading the page does not stop it, the list asks `status()` and shows the progress,
and only Stop ends it (the servers being checked at that moment finish, no new one starts). Every server is checked by the same functions as the single
buttons, so the answers also reach the marks of the list (GRE, filtering)."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Server
from ..ssh import SSHError
from . import autopilot, nodes
from .facts import sample_server

log = get_logger("checkall")
LABEL = "check all servers"
WORKERS = 4
CANCEL: set[int] = set()
PROGRESS: dict[int, dict] = {}
_LOCK = threading.Lock()
_SAY = threading.Lock()      # the story of an action is read, extended and written back: four workers must not do that at the same moment


def _say(aid: int, line: str, **kw) -> None:
    with _SAY:
        autopilot._say(aid, line, **kw)


def status() -> dict:
    """Is a check running, how far is it - from the action log, so it is the same after a page reload."""
    with session() as db:
        e = db.scalar(select(ActionLog).where(ActionLog.action == "checkall").order_by(ActionLog.id.desc()).limit(1))

        if e is None:
            return {"running": False}

        pr = PROGRESS.get(e.id, {})
        running = not e.finished_at and (e.id in PROGRESS or time.time() - (e.ts or 0) < 20)      # (the first seconds: the worker has not registered yet)
        return {"running": running, "id": e.id, "ok": e.ok, "done": pr.get("done", 0), "total": pr.get("total", 0), "stopping": e.id in CANCEL,
                "line": (list(e.story or []) or [""])[-1], "finished_at": e.finished_at or 0}


def _one(aid: int, sid: int, name: str, role: str) -> bool | None:
    """One server: reachable, then GRE (an Iran server of the nodes tests it), then - for a foreign server - the filtering check. None = skipped (stopped)."""
    if aid in CANCEL:
        return None

    parts: list[str] = []
    good = True

    try:
        sample_server(sid)
    except Exception as e:  # noqa: BLE001  one server must never stop the others
        parts.append(f"not reachable ({str(e)[:50]})")
        good = False

    with session() as db:
        s = db.get(Server, sid)
        online = bool(s and s.online)

    if good and not online:
        parts.append("not reachable")
        good = False
    elif good:
        parts.append("online")

    if good and aid not in CANCEL:
        if get_settings().read_only:
            parts.append("GRE skipped (read-only)")
        else:
            try:
                r = nodes.gre_probe(sid)
                parts.append("GRE ok" if r["ok"] else "GRE not working")
                good = good and r["ok"]
            except (SSHError, ValueError) as e:
                parts.append("GRE: " + str(e)[:70])

    if good and role == "foreign" and aid not in CANCEL:
        try:
            f = nodes.filter_check(sid)
            parts.append("not filtered" if f.get("level") == "clean" else "filtered in Iran" if f.get("level") in ("filtered", "blocked") else "filtering unclear")
            good = good and f.get("level") == "clean"
        except (SSHError, ValueError, RuntimeError) as e:
            parts.append("filtering: " + str(e)[:60])

    _say(aid, f"{name}: " + " · ".join(parts))
    return good


def _work(aid: int, role: str) -> None:
    with session() as db:
        stmt = select(Server).where(Server.enabled.is_(True)).order_by(Server.role, Server.name)

        if role in ("iran", "foreign"):
            stmt = stmt.where(Server.role == role)

        rows = [(s.id, s.name, s.role) for s in db.scalars(stmt)]

    with _LOCK:
        PROGRESS[aid] = {"done": 0, "total": len(rows)}

    _say(aid, f"Checking {len(rows)} servers: do they answer, does GRE work, are they filtered in Iran…")
    results: list[bool | None] = []

    try:
        with ThreadPoolExecutor(WORKERS) as ex:
            for fut in as_completed([ex.submit(_one, aid, *r) for r in rows]):
                results.append(fut.result())

                with _LOCK:
                    PROGRESS[aid]["done"] = len(results)
    finally:
        stopped = aid in CANCEL
        done = [r for r in results if r is not None]
        bad = sum(1 for r in done if not r)

        with _LOCK:
            PROGRESS.pop(aid, None)

        CANCEL.discard(aid)

    msg = ("Stopped by you after " if stopped else "Done: ") + f"{len(done)} of {len(rows)} servers checked, " + (f"{bad} with a problem." if bad else "no problem found.")
    _say(aid, msg, done=True, ok=bad == 0 and not stopped)


def start(role: str = "") -> int:
    """Start the check of every enabled server (of one role when given); one at a time."""
    if status().get("running"):
        raise ValueError("a check of all servers is already running")

    return autopilot._start(LABEL, _work, role, action="checkall", allow_read_only=True)


def stop() -> bool:
    st = status()

    if not st.get("running"):
        return False

    CANCEL.add(st["id"])
    _say(st["id"], "Stopping: the servers being checked now finish, no new one starts…")
    return True
