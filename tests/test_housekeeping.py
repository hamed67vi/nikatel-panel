"""Housekeeping: old events and finished repair stories go, recent ones and running ones stay."""
import time

from nikatel.db import session
from nikatel.models import ActionLog, Event
from nikatel.services import housekeeping


def test_old_rows_go_and_running_or_recent_rows_stay(client):
    now = int(time.time())

    with session() as db:
        db.add(Event(ts=now - 61 * 86400, level="info", domain="system", text="old"))
        db.add(Event(ts=now - 3600, level="info", domain="system", text="new"))
        db.add(ActionLog(ts=now - 100 * 86400, ref="t1", action="fix", source="auto", ok=True, finished_at=now - 100 * 86400 + 30, story=["old, finished"]))
        db.add(ActionLog(ts=now - 100 * 86400, ref="t2", action="build", source="manual", ok=None, finished_at=0, story=["old but still running"]))
        db.add(ActionLog(ts=now - 86400, ref="t3", action="fix", source="auto", ok=False, finished_at=now - 86400 + 30, story=["recent"]))

    r = housekeeping.trim(now)
    assert r["events_removed"] == 1 and r["actions_removed"] == 1

    with session() as db:
        assert [e.text for e in db.query(Event).all() if e.text in ("old", "new")] == ["new"]
        assert sorted(a.ref for a in db.query(ActionLog).all() if a.ref in ("t1", "t2", "t3")) == ["t2", "t3"]

    housekeeping.run()          # the job itself: trim + checkpoint, never raises
