"""Housekeeping: the tables that grow for ever are trimmed, so the database stays small and the pages that read them stay fast.

What is kept: server samples 7 days (facts.py), tunnel checks 14 days (tunnels.py), config samples 7 days (configs.py), repair cases the last
2,000 (analyst.py) - and here: events 60 days, finished repair stories 90 days. Nothing a running action needs is ever removed."""
from __future__ import annotations

import time

from sqlalchemy import delete, func, select, text

from ..db import engine, session
from ..log import get_logger
from ..models import ActionLog, Event

log = get_logger("housekeeping")

KEEP_EVENTS_SECONDS = 60 * 86400
KEEP_ACTIONS_SECONDS = 90 * 86400
ORPHAN_AFTER = 12 * 3600         # a story still open this long after it began belongs to a thread that is gone (the panel was restarted in the middle of it): the longest work of the panel (a smart repair that tries every type) takes about an hour


def close_orphans(now: int | None = None) -> int:
    """Close the stories that a restart of the panel left open. They never ended, so `trim` (which keeps what is not finished) kept them for ever and the page showed them as running; the live panel had 75, the oldest 12 days old.
    An open story younger than ORPHAN_AFTER is left alone: its thread may still be working (an install that a restart resumed is followed from its story)."""
    now = int(now or time.time())
    n = 0

    with session() as db:
        for e in db.scalars(select(ActionLog).where(ActionLog.finished_at == 0, ActionLog.ts < now - ORPHAN_AFTER)):
            e.story = list(e.story or []) + ["Stopped: the panel was restarted while this was running."]
            e.ok, e.finished_at = False, max(int(e.ts or 0), 1)          # (closed at its own start: the times the repair rules read - when its last story ended - stay as they were, and no duration of half a day is invented)
            n += 1

    return n


def trim(now: int | None = None) -> dict:
    """Remove what is older than the keep windows. Returns what was removed (so the log and the tests can say it)."""
    now = int(now or time.time())
    orphans = close_orphans(now)

    with session() as db:
        ev = db.execute(delete(Event).where(Event.ts < now - KEEP_EVENTS_SECONDS)).rowcount
        ac = db.execute(delete(ActionLog).where(ActionLog.ts < now - KEEP_ACTIONS_SECONDS, ActionLog.finished_at > 0)).rowcount
        left = {"events": db.scalar(select(func.count()).select_from(Event)) or 0, "actions": db.scalar(select(func.count()).select_from(ActionLog)) or 0}

    return {"events_removed": int(ev or 0), "actions_removed": int(ac or 0), "orphans_closed": orphans, **left}


def checkpoint() -> None:
    """Fold the write-ahead log back into the database file (it otherwise grows between restarts). Harmless when it cannot run right now."""
    try:
        with engine().connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            conn.execute(text("PRAGMA optimize"))
    except Exception as e:  # noqa: BLE001  a busy database is not an error here: the next turn does it
        log.info("checkpoint skipped: %s", str(e)[:100])


def run() -> None:
    r = trim()
    checkpoint()
    log.info("trimmed: %s events, %s repair stories removed, %s open stories of a stopped run closed; %s events, %s stories kept", r["events_removed"], r["actions_removed"], r["orphans_closed"], r["events"], r["actions"])
