"""The automatic change of a tunnel's type WITHOUT a cut (1.69.78, the owner, 2026-10-21: "make the tunnels steady fast, without the customer noticing"): the new type is built beside the old one as its warm standby,
measured, given the users' new connections, and the old tunnel is removed last (`services/swap.py`). The servers, the standby machinery and the build are stand-ins that write what happens into one list."""
import itertools
import time

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import (
    autopilot,
    bandwidth,
    doctor,
    failover,
    knowledge,
    standby,
    swap,
)
from nikatel.services import settings as st

KIND = "bp:wssmux"
HOSTS = itertools.count(10)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(swap, "POLL", 0)
    monkeypatch.setattr(swap, "SETTLE_STEP", 0)
    monkeypatch.setattr(swap, "_TRIED", {})
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    monkeypatch.setattr(get_settings(), "read_only", False)
    st.put("repair", {"mode": "fix"})


def _world(name="sw-a", state="up", ports=(8076,)):
    n = next(HOSTS)

    with session() as db:
        ir, fr = Server(name=f"ir-{name}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fr-{name}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, forwarded_ports=list(ports), record={"bw": {"down": 14.0, "state": "drop"}}, secrets={})
        db.add(t)
        db.flush()
        return t.id


class Stage:
    """The standby machinery and the servers, faked: `events` is what happened, in order."""

    def __init__(self, monkeypatch, tid, standby_kind=None, *, sb_down=80.0, new_down=90.0, quiet=True, replaced=True, samples=None, plan_error="", link=True, p_state="up", in_use=False):
        self.events, self.said, self.kept = [], [], []
        self.tid, self.quiet, self.replaced, self.plan_error = tid, quiet, replaced, plan_error
        self.s = {"id": 900, "name": "sw-a-sb", "kind": standby_kind, "state": "up", "stale": "", "use": ({"manual": True} if in_use else None), "iran": ("203.0.113.7", "root", 22)} if standby_kind else None
        self.samples = list(samples) if samples is not None else [{"state": "up", "rx": 4.0, "tx": 1.0, "loss": 0.1}]
        ev = self.events
        mp = monkeypatch.setattr
        mp(autopilot, "_say", lambda aid, line, done=None, ok=None: self.said.append((line, done, ok)))
        mp(autopilot, "_probe_links", lambda aid, snap, kinds: kinds if link else [])
        mp(autopilot, "_wait_quiet", lambda aid, t, limit=1800, loss_counts=True: ev.append(("quiet", limit, loss_counts)) or self.quiet)
        mp(autopilot, "_replace", lambda aid, t, i, f, kind, why, **kw: ev.append(("replace", kind)) or self.replaced)
        mp(standby, "_view", lambda name: {"p": {"state": p_state}, "s": self.s})
        mp(standby, "build_plan", lambda name, kind="": (_ for _ in ()).throw(ValueError(self.plan_error)) if self.plan_error else {"kind": kind})
        mp(standby, "build", self._build)
        mp(standby, "remove_standby", lambda name: ev.append(("remove_standby",)) or setattr(self, "s", None) or [])
        mp(standby, "want", lambda name, kind: ev.append(("want", kind)))
        mp(standby, "unwant", lambda name: ev.append(("unwant",)))
        mp(standby, "borrow_mark", lambda name: {"marked": True, "optout": False})
        mp(standby, "return_mark", lambda name, was: ev.append(("return_mark",)))
        mp(standby, "handover", lambda name, why="", manual=False: ev.append(("handover", manual)) or {"ok": True})
        mp(standby, "give_back", lambda name, why="": ev.append(("give_back",)) or {"ok": True})
        mp(swap, "_sample", lambda sb_id: self.samples.pop(0) if len(self.samples) > 1 else self.samples[0])
        mp(bandwidth, "measure", lambda i: ev.append(("measure", i)) or ({"down": sb_down if i == 900 else new_down}, ""))
        mp(knowledge, "record", lambda a, b, result, measure: self.kept.append((result, measure)))

    def _build(self, name, kind="", by_swap=False):
        self.events.append(("build", kind, by_swap))
        self.s = {"id": 900, "name": "sw-a-sb", "kind": kind, "state": "up", "stale": "", "use": None, "iran": ("203.0.113.7", "root", 22)}
        return 5

    def run(self, before=14.0):
        snap = autopilot._snapshot(self.tid)
        swap.beside(7, snap, KIND, "tcpmux dropped to 14 Mb/s; bp:wssmux gave 95", before)

    def names(self):
        return [e[0] for e in self.events]


# ------------------------------------------------------------------ pure
def test_the_speed_through_the_new_tunnel_must_clearly_beat_the_dropped_one():
    assert swap.confirm_ok(80.0, 14.0) == "" and swap.confirm_ok(21.0, 14.0) == ""
    assert "less than the 21 Mb/s" in swap.confirm_ok(20.0, 14.0)                                  # (1.5 times what it gives now)
    assert "less than the 20 Mb/s" in swap.confirm_ok(15.0, 2.0)                                   # (never under the lowest line)
    assert "no number" in swap.confirm_ok(None, 14.0)


def test_the_new_tunnel_is_judged_by_its_state_its_loss_and_the_data_that_flows():
    v = swap.carry_verdict
    up = {"state": "up", "rx": 3.0, "tx": 1.0, "loss": 0.2}
    assert v([up])[0] == "carries" and v([{"state": "up", "rx": 0.0, "tx": 0.0, "loss": 0.0}])[0] == "idle" and v([])[0] == "idle"
    assert v([up, {**up, "state": "down"}]) == ("bad", "it is down")                                # the last look decides
    assert v([{**up, "loss": 9.0}, {**up, "loss": 8.0}])[0] == "bad" and v([{**up, "loss": 30.0}, up, up, up])[0] == "carries"      # (the last three looks that knew)
    assert v([{"state": "up", "rx": None, "tx": None, "loss": None}])[0] == "idle"                  # (counters that cannot be read: not "worse")


# ------------------------------------------------------------------ the story
def test_the_new_type_is_built_beside_measured_given_the_new_connections_and_the_old_one_is_removed_last(monkeypatch):
    tid = _world()
    s = Stage(monkeypatch, tid)
    s.run()
    assert s.names() == ["want", "build", "measure", "handover", "quiet", "replace", "give_back", "measure", "unwant"]          # (the users stand on a tunnel that works at every step)
    assert ("build", KIND, True) in s.events and ("handover", True) in s.events and ("quiet", swap.DRAIN_LIMIT, False) in s.events and ("replace", KIND) in s.events
    assert s.events.index(("handover", True)) < s.events.index(("replace", KIND)) < s.events.index(("give_back",))                   # the old tunnel is removed only after the new one carried
    assert s.events[2] == ("measure", 900)                                                           # the speed test goes through the NEW tunnel, before any user is sent to it
    assert s.said[-1][1:] == (True, True) and s.said[-1][0].startswith("sw-a runs as bp:wssmux now.") and "90" in s.said[-1][0]
    assert doctor._state()["retype_done"].get("sw-a")                                                 # (counts as the type change of the day)
    assert s.kept == [({"kind": KIND, "ok": True, "works": True, "down_mbps": 90.0}, "full")]
    assert not any("Replacing" in x[0] for x in s.said)

    with session() as db:
        assert any("changed it without a cut" in e.text for e in db.scalars(select(Event).where(Event.domain == "tunnels")))


def test_a_standby_that_already_is_the_new_type_is_used_and_one_of_another_type_is_built_again(monkeypatch):
    tid = _world()
    s = Stage(monkeypatch, tid, standby_kind=KIND)
    s.run()
    assert "build" not in s.names() and "remove_standby" not in s.names() and s.names()[:2] == ["measure", "handover"]

    s = Stage(monkeypatch, tid, standby_kind="chisel:fwd")
    swap._TRIED.clear()
    s.run()
    assert s.names()[:3] == ["remove_standby", "want", "build"] and s.s["kind"] == KIND                 # (a spare that carries nobody: the tunnel keeps its users meanwhile)


def test_a_new_tunnel_that_does_not_give_clearly_more_never_gets_a_user_and_the_old_one_is_not_touched(monkeypatch):
    tid = _world()
    s = Stage(monkeypatch, tid, sb_down=18.0)
    s.run()
    assert "handover" not in s.names() and "replace" not in s.names() and "less than the 21 Mb/s" in s.said[-1][0] and s.said[-1][1:] == (True, False)
    assert not doctor._state()["retype_done"]                                                         # (the users were not touched: not "the change of the day")


def test_a_new_tunnel_that_carries_worse_sends_the_new_connections_back_and_the_old_one_was_never_touched(monkeypatch):
    for bad in ([{"state": "down", "rx": 0.0, "tx": 0.0, "loss": None}], [{"state": "up", "rx": 3.0, "tx": 1.0, "loss": 12.0}]):
        tid = _world(name=f"sw-w{len(bad)}{int(bad[0]['loss'] or 0)}")
        s = Stage(monkeypatch, tid, samples=bad)
        s.run()
        assert s.names() == ["want", "build", "measure", "handover", "give_back", "unwant"] and "does not carry the users well" in s.said[-1][0] and s.said[-1][1:] == (True, False)

        with session() as db:
            assert any("did not carry well" in e.text for e in db.scalars(select(Event).where(Event.domain == "tunnels")))


def test_when_the_open_connections_do_not_end_nothing_is_cut_and_the_new_connections_go_back(monkeypatch):
    tid = _world()
    s = Stage(monkeypatch, tid, quiet=False)
    s.run()
    assert s.names() == ["want", "build", "measure", "handover", "quiet", "give_back", "unwant"] and "NOT touched" in s.said[-1][0] and s.said[-1][1:] == (True, False)


def test_when_the_new_type_does_not_build_as_the_tunnel_the_old_type_is_back_and_the_users_go_back_to_the_tunnel(monkeypatch):
    tid = _world()
    s = Stage(monkeypatch, tid, replaced=False)
    s.run()
    assert s.names()[-3:] == ["replace", "give_back", "unwant"] and "Nobody was cut" in s.said[-1][0] and s.said[-1][1:] == (True, False)


def test_whatever_ends_the_story_the_new_connections_are_given_back(monkeypatch):
    tid = _world()
    s = Stage(monkeypatch, tid)

    def boom(*a, **k):
        raise ValueError("tunnel not found")                                                        # (the tunnel was deleted while it was waited for)

    monkeypatch.setattr(autopilot, "_replace", boom)

    with pytest.raises(ValueError):
        s.run()

    assert "give_back" in s.names() and s.names()[-1] == "unwant"


def test_a_swap_that_cannot_start_is_left_alone_with_no_suggestion_and_never_a_cut(monkeypatch):
    for kw in ({"plan_error": "this tunnel forwards no port, so there is nothing to hand over"}, {"p_state": "down"}, {"standby_kind": "chisel:fwd", "in_use": True}, {"plan_error": "server ir-sw is busy (CPU 91 %)"}):
        st.put("doctor", {"suggestions": []})
        swap._TRIED.clear()
        tid = _world(name=f"sw-c{len(kw)}{len(str(kw))}")
        s = Stage(monkeypatch, tid, **kw)
        s.run()
        assert s.names() == [] and "NOT changed to bp:wssmux by itself" in s.said[-1][0] and s.said[-1][1:] == (True, True)
        assert doctor.state()["suggestions"] == []                                                    # (no suggestion any more, 1.69.84: the Doctor looks again later)


def test_the_link_that_does_not_pass_builds_nothing_and_a_swap_is_not_tried_again_within_hours(monkeypatch):
    tid = _world()
    s = Stage(monkeypatch, tid, link=False)
    s.run()
    assert s.names() == [] and "link of bp:wssmux does not pass" in s.said[-1][0]

    s = Stage(monkeypatch, tid)
    s.run()
    assert s.names().count("handover") == 1
    s2 = Stage(monkeypatch, tid)
    s2.run()
    assert s2.names() == [] and "is not tried again before 3 hours" in s2.said[-1][0]


def test_the_panel_read_only_changes_nothing(monkeypatch):
    tid = _world()
    s = Stage(monkeypatch, tid)
    monkeypatch.setattr(get_settings(), "read_only", True)
    s.run()
    assert s.names() == [] and "read-only" in s.said[-1][0]


# ------------------------------------------------------------------ the seam: the Doctor's own change goes beside, the owner's click keeps its way
def test_the_doctors_own_change_goes_beside_and_the_owners_click_still_rebuilds(monkeypatch):
    tid = _world()
    s = Stage(monkeypatch, tid)
    doctor._run_retype(7, tid, KIND, "tcpmux", "why", auto=True)
    assert "handover" in s.names() and ("replace", KIND) in s.events

    s = Stage(monkeypatch, tid)
    monkeypatch.setattr(swap, "beside", lambda *a, **k: pytest.fail("the owner's click does not go beside"))
    doctor._run_retype(8, tid, KIND, "tcpmux", "why", auto=False)
    assert s.names()[:1] == ["replace"] and "handover" not in s.names()                              # (the click: the old way, the owner knows it cuts)


def test_the_standby_job_of_a_tunnel_is_not_asked_to_wait_for_a_repair_that_is_the_swap_itself(monkeypatch):
    tid = _world()
    seen = {}
    monkeypatch.setattr(autopilot, "repairing_names", lambda: ["sw-a"])
    monkeypatch.setattr(standby, "build_plan", lambda name, kind="": seen.setdefault("kind", kind) and (_ for _ in ()).throw(ValueError("stop here")))

    with pytest.raises(ValueError, match="repair works"):
        standby.build("sw-a")

    with pytest.raises(ValueError, match="stop here"):
        standby.build("sw-a", KIND, by_swap=True)

    assert seen["kind"] == KIND and tid


# ------------------------------------------------------------------ the standby machinery that the swap uses
def test_a_standby_of_a_type_that_is_asked_for_is_planned_as_that_type(monkeypatch):
    _world()
    plan = standby.build_plan("sw-a", KIND)
    assert plan["kind"] == KIND and plan["name"] == "sw-a-sb" and "asked for by the swap" in plan["note"]

    with pytest.raises(ValueError, match="no spare"):
        standby.build_plan("sw-a", "tcpmux")

    monkeypatch.setattr(standby.license, "tunnel_kind_allowed", lambda k: False)

    with pytest.raises(ValueError, match="licence"):
        standby.build_plan("sw-a", KIND)

    monkeypatch.setattr(standby.license, "tunnel_kind_allowed", lambda k: True)
    standby.want("sw-a", "chisel:fwd")                                                               # (the standby job, if it starts the build first, builds the same type)
    assert standby.build_plan("sw-a")["kind"] == "chisel:fwd"
    standby.unwant("sw-a")
    standby.want("sw-a", "chisel:fwd")
    standby._WANT["sw-a"] = ("chisel:fwd", int(time.time()) - 1)                                    # (expired)
    assert standby._wanted("sw-a") == ""


def test_the_mark_is_borrowed_for_a_tunnel_without_a_standby_and_given_back_when_the_owner_had_switched_it_off(monkeypatch):
    cleaned = []
    monkeypatch.setattr(standby, "_safe_cleanup", lambda name: cleaned.append(name))
    st.put("standby", {"marked": {}, "optout": {"sw-a": 1}})
    was = standby.borrow_mark("sw-a")
    assert was == {"marked": False, "optout": True} and "sw-a" in standby._marks()
    standby.return_mark("sw-a", was)
    assert "sw-a" not in standby._marks() and "sw-a" in st.get("standby")["optout"]                  # (the owner's switch is respected again)

    for _ in range(50):
        if cleaned:
            break

        time.sleep(0.05)

    assert cleaned == ["sw-a"]
    st.put("standby", {"marked": {"sw-b": {"since": 1}}, "optout": {}})
    was = standby.borrow_mark("sw-b")
    standby.return_mark("sw-b", was)
    assert "sw-b" in standby._marks()                                                                # (a tunnel that had its standby keeps it)


def test_a_wait_for_the_old_connections_is_not_cut_short_by_the_loss_of_the_old_line(monkeypatch):
    tid = _world()

    with session() as db:
        t = db.get(Tunnel, tid)
        t.rx_mbps, t.tx_mbps, t.detail = 10.0, 1.0, {"loss_pct": 50.0}

    monkeypatch.setattr(autopilot, "_say", lambda *a, **k: None)
    assert autopilot._wait_quiet(1, tid, limit=1800) is True                                         # (the capacity swap: at 50 % loss waiting costs more than a swap)
    assert autopilot._wait_quiet(1, tid, limit=-1, loss_counts=False) is False                        # (the swap beside: nobody is cut, the connections are waited for)
