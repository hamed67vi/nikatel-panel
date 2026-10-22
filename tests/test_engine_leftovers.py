"""The QA round of the tunnel engine (2026-10-22), part two: what a look at the live panel found left over.

  stories a restart left open        the housekeeping closes them (the live panel had 75, the oldest 12 days old)
  the standby of a removed tunnel    the owner's "replace" forgets the standby like the Delete button; the clock of an absent tunnel survives a restart of the panel
  the user's mark                    0 - 100 whatever the readings are; a reading from this very moment counts in the watch of the tunnels nobody uses
  the sentinel                       one check that crashes does not end the round for the other tunnels
"""
import time

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, Server, Tunnel
from nikatel.services import builder, housekeeping, standby, userscore
from nikatel.services import settings as st


def _servers(tag="qa"):
    with session() as db:
        n = len(db.query(Server).all()) + 40
        ir = Server(name=f"ir-{tag}", host=f"203.0.113.{n}", role="iran", online=True, enabled=True)
        fo = Server(name=f"fo-{tag}", host=f"198.51.100.{n}", role="foreign", online=True, enabled=True)
        db.add_all([ir, fo])
        db.flush()
        return ir.id, fo.id


def _writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)

def test_the_stories_a_restart_left_open_are_closed_after_half_a_day_and_the_young_ones_are_left_alone():
    now = int(time.time())

    with session() as db:
        db.add(ActionLog(ts=now - 13 * 3600, ref="old-build", action="build", source="manual", ok=None, finished_at=0, story=["Building"]))
        db.add(ActionLog(ts=now - 2 * 3600, ref="young-build", action="build", source="manual", ok=None, finished_at=0, story=["Building"]))
        db.add(ActionLog(ts=now - 20 * 86400, ref="fix-ok", action="fix", source="auto", ok=True, finished_at=now - 20 * 86400 + 9, story=["done"]))

    assert housekeeping.close_orphans(now) == 1

    with session() as db:
        old = db.query(ActionLog).filter(ActionLog.ref == "old-build").one()
        young = db.query(ActionLog).filter(ActionLog.ref == "young-build").one()
        assert old.finished_at == old.ts and old.ok is False and "restarted" in old.story[-1]          # (closed at its own start: no duration of half a day, the times the repair rules read stay as they were)
        assert young.finished_at == 0 and young.story == ["Building"]

    assert housekeeping.close_orphans(now) == 0          # (once)
    assert housekeeping.trim(now)["orphans_closed"] == 0


def test_an_open_story_of_a_hundred_days_ago_is_closed_and_then_trimmed_with_the_rest():
    now = int(time.time())

    with session() as db:
        db.add(ActionLog(ts=now - 100 * 86400, ref="ghost", action="build", source="manual", ok=None, finished_at=0, story=["Building"]))

    r = housekeeping.trim(now)
    assert r["orphans_closed"] == 1 and r["actions_removed"] == 1

    with session() as db:
        assert db.query(ActionLog).filter(ActionLog.ref == "ghost").count() == 0


# ------------------------------------------------------------------ the standby of a removed tunnel goes
def test_the_owners_replace_forgets_the_standby_of_the_replaced_tunnel_like_the_delete_button(monkeypatch):
    from nikatel.services import create

    _writable(monkeypatch)
    ir, fo = _servers("rep")

    with session() as db:
        t = Tunnel(name="qa-old", type="backhaul", transport="tcpmux", port=9301, iran_server_id=ir, foreign_server_id=fo, state="down", enabled=True, forwarded_ports=[8075])
        db.add(t)
        db.flush()
        old_id = t.id

    forgotten = []
    monkeypatch.setattr(builder, "delete_tunnel", lambda tid, keep_record=False, trash=False, skip_offline=False: ["removed"])
    monkeypatch.setattr(builder, "start_build", lambda *a, **k: 1)
    monkeypatch.setattr(standby, "forget", lambda name: forgotten.append(name))
    monkeypatch.setattr(create, "_follow", lambda aid, build_aid: True)
    create.start(ir, fo, "8075", "backhaul", "tcpmux", {}, [old_id], False)
    end = time.time() + 10

    while time.time() < end and not forgotten:
        time.sleep(0.05)

    assert forgotten == ["qa-old"]


def test_the_clock_of_a_missing_tunnel_survives_a_restart_of_the_panel(monkeypatch):
    _writable(monkeypatch)
    removed = []
    monkeypatch.setattr(standby, "remove_standby", lambda name: removed.append(name) or [])
    standby._mark("ghost-t", True)
    standby._MISSING.clear()
    standby._CHECKED.clear()
    now = int(time.time())
    standby._step("ghost-t")                                   # the row is not there: the clock starts, and it is written in the mark
    assert st.get("standby")["marked"]["ghost-t"]["gone"] >= now and removed == []

    st.put("standby", {"marked": {**st.get("standby")["marked"], "ghost-t": {"since": now - 5 * 3600, "gone": now - standby.MISSING_GRACE - 60}}})
    standby._MISSING.clear()                                   # a restart of the panel: nothing in memory
    standby._step("ghost-t")
    assert "ghost-t" not in standby._marks()          # (the mark went; a standby row, if there was one, is removed by `_cleanup_unmarked` - tests/test_standby.py)


def test_the_clock_of_a_missing_tunnel_is_forgotten_when_the_tunnel_is_back(monkeypatch):
    _writable(monkeypatch)
    ir, fo = _servers("back")
    monkeypatch.setattr(standby, "remove_standby", lambda name: [])
    monkeypatch.setattr(standby.actions, "doctor_mode", lambda: "off")
    standby._mark("qa-back", True)
    standby._MISSING.clear()
    standby._step("qa-back")
    assert "gone" in st.get("standby")["marked"]["qa-back"]

    with session() as db:
        db.add(Tunnel(name="qa-back", type="backhaul", transport="tcpmux", port=9302, iran_server_id=ir, foreign_server_id=fo, state="up", enabled=True, forwarded_ports=[8076]))

    standby._step("qa-back")
    assert "gone" not in st.get("standby")["marked"]["qa-back"] and "since" in st.get("standby")["marked"]["qa-back"]


# ------------------------------------------------------------------ the user's mark
@pytest.mark.parametrize("q", [
    {"latency_ms": 20, "jitter_ms": -3, "loss_pct": 0, "down_mbps": 500, "down1_mbps": 400, "up_mbps": 100},
    {"latency_ms": 20, "jitter_ms": 0, "loss_pct": -5, "down_mbps": 500},
    {"latency_ms": -4, "jitter_ms": 0, "loss_pct": 0, "down_mbps": -50, "down1_mbps": -10},
    {"latency_ms": 20, "jitter_ms": 1e9, "loss_pct": 1e9, "down_mbps": 1e12},
])
def test_the_mark_of_what_a_user_gets_stays_between_0_and_100(q):
    assert 0 <= userscore.user_score(q) <= 100


def test_a_reading_from_this_very_moment_counts_in_the_watch_of_the_tunnels_nobody_uses(monkeypatch):
    from nikatel.services import actions, bandwidth, livebw

    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    ir, fo = _servers("zero")

    with session() as db:
        t = Tunnel(name="qa-zero", type="backhaul", transport="tcpmux", port=9303, iran_server_id=ir, foreign_server_id=fo, state="up", enabled=True, forwarded_ports=[8077])
        db.add(t)
        db.flush()
        tid = t.id

    monkeypatch.setattr(livebw, "view", lambda: {str(tid): {"down": 0.0, "up": 0.0, "age": 0.0}})          # (age 0.0 is "now", not "no age")
    bandwidth._ZERO.clear()
    bandwidth._zero_watch()
    assert tid in bandwidth._ZERO
    bandwidth._ZERO.clear()

def test_one_check_of_the_sentinel_that_crashes_does_not_end_the_round_of_the_others(monkeypatch):
    from nikatel.services import sentinel, tunnels

    seen = []

    def check(tid, **kw):
        seen.append(tid)

        if tid == 1:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(tunnels, "check_tunnel", check)
    sentinel.look((sentinel.Watch(1, "a", 1, 2), ["Iran: u.service is inactive"]))
    sentinel.look((sentinel.Watch(2, "b", 1, 2), ["Iran: u.service is inactive"]))
    assert seen == [1, 2]
