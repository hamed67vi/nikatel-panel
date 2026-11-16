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
    trident,
    tunnel_test,
)
from nikatel.services import settings as st

KIND = "bp:wssmux"
HOSTS = itertools.count(10)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(swap, "POLL", 0)
    monkeypatch.setattr(swap, "SETTLE_STEP", 0)
    monkeypatch.setattr(swap, "_TRIED", {})
    monkeypatch.setattr(swap, "_RUNNING", set())
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

    def __init__(self, monkeypatch, tid, standby_kind=None, *, sb_down=80.0, new_down=90.0, quiet=True, replaced=True, samples=None, plan_error="", link=True, p_state="up", in_use=False, q=None):
        self.events, self.said, self.kept, self.remembered = [], [], [], []
        self.q = q if q is not None else {"latency_ms": 60.0, "jitter_ms": 2.0, "loss_pct": 0.2}          # (what the delay and loss through the new tunnel read: a clean line)
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
        mp(knowledge, "remember", lambda *a, **k: self.remembered.append(a))
        mp(tunnel_test, "quality", lambda i, speed=True: ev.append(("quality", i, speed)) or self.q)

    def _build(self, name, kind="", by_swap=False):
        self.events.append(("build", kind, by_swap))
        self.s = {"id": 900, "name": "sw-a-sb", "kind": kind, "state": "up", "stale": "", "use": None, "iran": ("203.0.113.7", "root", 22)}
        return 5

    def run(self, before=14.0):
        snap = autopilot._snapshot(self.tid)
        swap.beside(7, snap, KIND, "tcpmux dropped to 14 Mb/s; bp:wssmux gave 95", before)

    def run_lossy(self, before=100.0, score=30.0, loss=22.0, close=False):
        """The swap of a BUSY tunnel that loses packets (1.69.283): `score` / `loss` are what the old line scores and loses now; returns what became of it."""
        snap = autopilot._snapshot(self.tid)
        return swap.beside(7, snap, KIND, "the line loses packets", before, lossy={"score": score, "loss": loss}, close=close)

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


# ------------------------------------------------------------------ a BUSY tunnel that loses packets (1.69.283, the owner, 2026-11-16, «راه a»): the same swap, judged by the line, started even though the tunnel is only degraded
def test_the_new_tunnel_must_be_clean_clearly_better_by_its_line_and_not_slower_than_what_the_users_have():
    ok = {"latency_ms": 60.0, "jitter_ms": 2.0, "loss_pct": 0.2}
    assert swap.confirm_lossy(ok, 80.0, 100.0, 30.0) == "" and swap.confirm_lossy(ok, 80.0, 100.0, None) == ""                      # (a score that is not known: the loss bar alone)
    assert "loses 9 % of the packets itself" in swap.confirm_lossy({**ok, "loss_pct": 9.0}, 80.0, 100.0, 30.0)
    assert "loses 5 % of the packets itself" in swap.confirm_lossy({**ok, "loss_pct": 5.0}, 80.0, 100.0, 30.0)                      # (the bar is LOSSY_CLEAN: under 5)
    assert "not 10 points more than the 60 of the tunnel now" in swap.confirm_lossy(ok, 80.0, 100.0, 60.0)                         # (48 by delay and loss: clean, but not clearly better than a line of 60)
    assert "less than the 50 Mb/s that keep the users' speed" in swap.confirm_lossy(ok, 40.0, 100.0, 30.0)                         # (half of what the lossy tunnel gave in its last speed test)
    assert "less than the 20 Mb/s" in swap.confirm_lossy(ok, 15.0, 10.0, 30.0) and swap.confirm_lossy(ok, 25.0, 10.0, 30.0) == ""   # (never under the lowest line)
    assert "no number" in swap.confirm_lossy(ok, None, 100.0, 30.0)
    assert "could not be measured" in swap.confirm_lossy(None, 80.0, 100.0, 30.0) and "could not be measured" in swap.confirm_lossy({"latency_ms": 60.0}, 80.0, 100.0, 30.0)


def test_a_tunnel_is_busy_from_the_quiet_bar_up_and_its_loss_does_not_matter():
    assert autopilot.is_busy(1.0, 0.6) and not autopilot.is_busy(1.0, 0.4) and autopilot.is_busy(None, 5.0) and not autopilot.is_busy(None, None)
    assert autopilot.quiet_ok(10.0, 10.0, 40.0) and autopilot.is_busy(10.0, 10.0)                                                  # (at 40 % loss `quiet_ok` says "do not wait" - the swap beside is for exactly this tunnel)


def test_a_busy_lossy_tunnel_goes_over_to_a_clean_new_tunnel_and_the_old_one_is_replaced_last(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded")
    assert s.run_lossy() == swap.DONE
    assert s.names() == ["want", "build", "measure", "quality", "handover", "quiet", "replace", "give_back", "measure", "unwant"]
    assert s.events.index(("quality", 900, False)) < s.events.index(("handover", True)) < s.events.index(("replace", KIND)) < s.events.index(("give_back",))       # (the line is read through the NEW tunnel before any user is sent to it)
    assert ("quiet", swap.LOSSY_DRAIN_LIMIT, False) in s.events                                       # (the new connections already run on the clean line: waiting for the old ones costs the users nothing)
    assert all(x[1] is None for x in s.said) and s.said[-1][0].startswith("sw-a runs as bp:wssmux now.")        # (the story is the repair's: no line of the swap ends it)
    assert s.remembered == [] and doctor._state()["retype_done"].get("sw-a")                           # (the repair writes the incident under its real cause; the swap counts as the type change of the day)
    assert "60.0 ms and 0.2 % loss through itself" in " ".join(x[0] for x in s.said) and "loses 22.0 %" in " ".join(x[0] for x in s.said)


def test_a_new_tunnel_that_loses_packets_itself_never_gets_a_user(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded", q={"latency_ms": 60.0, "jitter_ms": 2.0, "loss_pct": 9.0})
    assert s.run_lossy() == swap.REJECTED
    assert s.names() == ["want", "build", "measure", "quality", "unwant"] and "loses 9 % of the packets itself" in s.said[-1][0] and "the users stay on tcpmux" in s.said[-1][0]
    assert not doctor._state()["retype_done"]                                                         # (nobody was touched)


@pytest.mark.parametrize("kw,run,why", [
    ({}, {"score": 60.0}, "not 10 points more"),                                                      # (clean, but the old line scores 60: not clearly better)
    ({"sb_down": 60.0}, {"before": 300.0}, "less than the 150 Mb/s"),                                 # (clean, but it crawls next to what the users had)
    ({"q": {"latency_ms": None, "jitter_ms": None, "loss_pct": None}}, {}, "could not be measured"),
])
def test_a_new_tunnel_that_is_not_clearly_better_through_itself_is_not_used_and_the_old_one_is_kept(monkeypatch, kw, run, why):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded", **kw)
    assert s.run_lossy(**run) == swap.REJECTED
    assert "handover" not in s.names() and "replace" not in s.names() and why in s.said[-1][0] and s.names()[-1] == "unwant"


def test_when_the_delay_and_loss_cannot_be_read_through_the_new_tunnel_the_users_stay(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded")
    monkeypatch.setattr(tunnel_test, "quality", lambda i, speed=True: (_ for _ in ()).throw(RuntimeError("no data passes through the tunnel right now")))
    assert s.run_lossy() == swap.REJECTED and "handover" not in s.names() and "could not be measured" in s.said[-1][0]


def test_a_new_tunnel_that_carries_the_users_badly_sends_them_back_and_the_old_tunnel_is_kept(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded", samples=[{"state": "up", "rx": 3.0, "tx": 1.0, "loss": 12.0}])
    assert s.run_lossy() == swap.ROLLED_BACK
    assert s.names() == ["want", "build", "measure", "quality", "handover", "give_back", "unwant"] and "replace" not in s.names() and "does not carry the users well" in s.said[-1][0]


def test_when_the_old_connections_do_not_end_the_old_tunnel_is_not_cut_and_the_new_connections_go_back(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded", quiet=False)
    assert s.run_lossy() == swap.KEPT
    assert s.names() == ["want", "build", "measure", "quality", "handover", "quiet", "give_back", "unwant"] and "NOT touched" in s.said[-1][0] and "after 60 minutes" in s.said[-1][0]


def test_when_the_old_tunnel_cannot_be_built_again_as_the_new_type_the_old_type_is_back_and_nobody_was_cut(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded", replaced=False)
    assert s.run_lossy() == swap.KEPT and s.names()[-3:] == ["replace", "give_back", "unwant"] and "Nobody was cut" in s.said[-1][0]


def test_a_swap_that_cannot_start_builds_nothing_and_leaves_the_old_way_to_the_repair(monkeypatch):
    for kw, outcome in (({"plan_error": "server ir-sw is busy (CPU 91 %)"}, swap.CANNOT), ({"plan_error": "a standby of the tunnel's own type is no spare"}, swap.CANNOT), ({"p_state": "down"}, swap.CANNOT),
                        ({"standby_kind": "chisel:fwd", "in_use": True}, swap.HOLD), ({"link": False}, swap.HOLD)):
        swap._TRIED.clear()
        tid = _world(name=f"sw-l{len(str(kw))}{len(kw)}")
        s = Stage(monkeypatch, tid, **{"p_state": "degraded", **kw})
        assert s.run_lossy() == outcome and s.names() == [] and "NOT changed" not in s.said[-1][0] and all(x[1] is None for x in s.said), kw


def test_a_protected_tunnel_a_read_only_panel_and_a_swap_tried_lately_are_held_not_cut(monkeypatch):
    tid = _world(state="degraded")

    with session() as db:
        t = db.get(Tunnel, tid)
        t.record = {**(t.record or {}), "protected": True}

    s = Stage(monkeypatch, tid, p_state="degraded")
    assert s.run_lossy() == swap.HOLD and s.names() == [] and "is protected" in s.said[-1][0]

    tid = _world(name="sw-b", state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded")
    assert s.run_lossy() == swap.DONE
    s = Stage(monkeypatch, tid, p_state="degraded")
    assert s.run_lossy() == swap.HOLD and s.names() == [] and "is not tried again before 3 hours" in s.said[-1][0]               # (a rejected or rolled back swap is not tried again at once - and the repair does not fall back to the cut for it)

    monkeypatch.setattr(get_settings(), "read_only", True)
    assert s.run_lossy() == swap.HOLD


def test_a_degraded_tunnel_may_start_a_swap_only_when_it_is_changed_for_its_loss(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded")
    s.run()                                                                                           # (the drop swap: the tunnel must be up)
    assert s.names() == [] and "NOT changed to bp:wssmux by itself" in s.said[-1][0] and s.said[-1][1:] == (True, True)


def test_the_repair_hands_the_swap_the_line_it_replaces_and_gets_back_what_became_of_it(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded")
    snap = autopilot._snapshot(tid)
    assert autopilot._lossy_beside(7, snap, {"kind": KIND, "score": 70}, 30.0, "auto") == swap.DONE
    assert s.names()[:4] == ["want", "build", "measure", "quality"] and "replace" in s.names() and all(x[1] is None for x in s.said)
    assert any("carries users now" in x[0] and "BESIDE it" in x[0] for x in s.said)


def test_a_standby_is_built_beside_a_degraded_tunnel_only_for_the_swap(monkeypatch):
    _world(state="degraded")
    monkeypatch.setattr(standby, "build_plan", lambda name, kind="": (_ for _ in ()).throw(ValueError("stop here")))

    with pytest.raises(ValueError, match="is degraded"):
        standby.build("sw-a", KIND)

    with pytest.raises(ValueError, match="stop here"):
        standby.build("sw-a", KIND, by_swap=True)


def test_a_swap_is_counted_while_it_goes_on_and_not_after_however_it_ends(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded")
    seen = []
    monkeypatch.setattr(standby, "handover", lambda name, why="", manual=False: seen.append(swap.at_once()) or {"ok": True})
    assert swap.at_once() == 0 and s.run_lossy() == swap.DONE and seen == [1] and swap.at_once() == 0                    # (counted from the first build to the last line)

    tid = _world(name="sw-c", state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded")
    monkeypatch.setattr(autopilot, "_replace", lambda *a, **k: (_ for _ in ()).throw(ValueError("tunnel not found")))

    with pytest.raises(ValueError):
        s.run_lossy()

    assert swap.at_once() == 0                                                                        # (a crash does not leave a place taken for ever)


def test_a_tunnel_whose_users_ports_the_trident_writes_is_not_given_a_second_tunnel_built_for_nothing(monkeypatch):
    tid = _world(state="degraded")
    s = Stage(monkeypatch, tid, p_state="degraded")
    monkeypatch.setattr(trident, "owns", lambda name: True)
    assert s.run_lossy() == swap.CANNOT and s.names() == [] and "Trident" in s.said[-1][0]
