"""A tunnel that is being removed is not judged, not repaired and not announced (the owner, 2026-11-11, the red event "Repair of us-a did not help" at the second he removed that tunnel).

What happened: a removal stops the services on the Iran end FIRST, a check a second later read "unit inactive" and called the tunnel down ("is down"), the Doctor started a plain repair (restarting units that were
being removed), the repair looked again, saw the tunnel gone ("removed meanwhile") and still ended with a RED event "did not help". The same ~18 times in five days: one for every removal of a live tunnel.

  the mark      `builder.delete_tunnel` marks its tunnel for as long as it runs (`tunnels.removal_started` / `removal_ended`; a mark expires after GOING_MAX; two removals of one name count twice)
  the check     a tunnel with the mark is not asked and not judged; a check that was already running when the mark came writes nothing
  the repair    the job starts none for it; a repair that began a second before stops between the steps, and one whose tunnel is gone ends quietly (no "did not help", not counted against the hourly budget)"""
import time

from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import actions, bandwidth, builder
from nikatel.services import tunnels as tn


def make(name: str, state: str = "up") -> int:
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 60
        ir = Server(name=f"ir-{name}", host=f"203.0.113.{n}", role="iran", online=True)
        fr = Server(name=f"fr-{name}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9907, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, forwarded_ports=[8072], record={})
        db.add(t)
        db.flush()
        return t.id


def stopped(node, plan, is_iran, *a, **k):
    """What the servers say a second after a removal began: every service of the tunnel is inactive."""
    units = plan.iran_units if is_iran else plan.foreign_units
    return tn.parse_side("".join(f"UNIT {u} inactive\n" for u in units) + "NOW 1000\n")


def events_of(name: str) -> list[tuple[str, str]]:
    with session() as db:
        return [(e.level, e.text) for e in db.scalars(select(Event).where(Event.ref == name).order_by(Event.id))]


def state_of(tid: int) -> str:
    with session() as db:
        return db.get(Tunnel, tid).state


def wait_done(aid: int) -> dict:
    end = time.time() + 20

    while time.time() < end and not actions.status(aid)["done"]:
        time.sleep(0.05)

    return actions.status(aid)


# ------------------------------------------------------------------ the mark
def test_a_removal_marks_its_tunnel_for_as_long_as_it_runs():
    assert not tn.being_removed("rm-x")
    tn.removal_started("rm-x")
    assert tn.being_removed("rm-x") and not tn.being_removed("rm-y")
    tn.removal_started("rm-x")                                    # a rebuild removes a name that is built again at once: two removals of one name
    tn.removal_ended("rm-x")
    assert tn.being_removed("rm-x")                               # one of them is still running
    tn.removal_ended("rm-x")
    assert not tn.being_removed("rm-x")
    tn.removal_ended("rm-x")                                      # an end without a start is harmless
    assert not tn.being_removed("rm-x")


def test_a_mark_that_never_ended_does_not_silence_a_tunnel_for_ever():
    with tn._GOING_LOCK:
        tn._GOING["rm-old"] = [time.time() - tn.GOING_MAX - 5]

    assert tn.being_removed("rm-old") is False and "rm-old" not in tn._GOING


def test_delete_tunnel_marks_its_tunnel_while_it_runs_and_not_after_a_refusal(monkeypatch):
    tid = make("rm-del")
    seen = []

    def fake(tunnel_id, keep_record=False, trash=False, skip_offline=False):
        seen.append(tn.being_removed("rm-del"))
        return ["Iran: removed", "foreign: removed"]

    monkeypatch.setattr(builder, "_delete_tunnel", fake)
    assert builder.delete_tunnel(tid, trash=True) == ["Iran: removed", "foreign: removed"]
    assert seen == [True] and not tn.being_removed("rm-del")

    def refuse(*a, **k):
        raise ValueError("rm-del is protected")

    monkeypatch.setattr(builder, "_delete_tunnel", refuse)

    try:
        builder.delete_tunnel(tid)
    except ValueError:
        pass
    else:
        raise AssertionError("the refusal must reach the caller")

    assert not tn.being_removed("rm-del")                         # a refused removal leaves no mark behind


# ------------------------------------------------------------------ the check
def test_a_tunnel_whose_services_are_stopped_is_down_when_nothing_removes_it(monkeypatch):
    """The control of the next two tests: the same answers, no removal - the tunnel IS down and the event is told."""
    tid = make("rm-ctl")
    monkeypatch.setattr(tn, "_query", stopped)
    tn.check_tunnel(tid)
    assert state_of(tid) == "down" and any(lvl == "bad" and "is down" in txt for lvl, txt in events_of("rm-ctl"))


def test_a_tunnel_that_is_being_removed_is_not_asked_not_judged_and_not_announced(monkeypatch):
    tid = make("rm-chk")
    monkeypatch.setattr(tn, "_query", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing is asked of the servers of a tunnel that is being removed")))
    tn.removal_started("rm-chk")
    tn.check_tunnel(tid)
    tn.removal_ended("rm-chk")
    assert state_of(tid) == "up" and events_of("rm-chk") == []


def test_a_check_that_was_already_running_when_the_removal_began_writes_nothing(monkeypatch):
    tid = make("rm-mid")

    def mid(node, plan, is_iran, *a, **k):
        tn.removal_started("rm-mid")                              # the removal begins while the servers are being asked
        return stopped(node, plan, is_iran)

    monkeypatch.setattr(tn, "_query", mid)
    tn.check_tunnel(tid)
    tn.removal_ended("rm-mid")
    assert state_of(tid) == "up" and events_of("rm-mid") == []


# ------------------------------------------------------------------ the repair
def test_the_repair_job_starts_none_for_a_tunnel_that_is_being_removed(monkeypatch):
    gone, other = make("rm-job1", "down"), make("rm-job2", "down")
    now = int(time.time())

    with session() as db:
        for n in (gone, other):
            t = db.get(Tunnel, n)
            t.state_since, t.last_error = now - 400, "Iran: backhaul-iran-tcpmux-9907.service is inactive"

    started = []
    monkeypatch.setattr(actions, "fix", lambda tunnel_id, source="manual", dry_run=False: started.append(tunnel_id) or 1)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(actions.get_settings(), "read_only", False)
    tn.removal_started("rm-job1")
    actions.auto_repair()
    tn.removal_ended("rm-job1")
    assert started == [other]


def fix_world(monkeypatch):
    monkeypatch.setattr(actions, "_query", stopped)
    monkeypatch.setattr(actions.get_settings(), "read_only", False)
    monkeypatch.setattr(actions.ai, "advise", lambda *a, **k: None)
    ran = []
    monkeypatch.setattr(actions, "run_action", lambda node, name, params=None, timeout=60: ran.append(params["UNITS"]) or "")
    return ran


def test_a_repair_that_begins_a_second_after_the_removal_did_ends_quietly(monkeypatch):
    tid = make("rm-fix1", "down")
    ran = fix_world(monkeypatch)
    tn.removal_started("rm-fix1")
    aid = actions.fix(tid, source="auto")
    s = wait_done(aid)
    tn.removal_ended("rm-fix1")
    assert s["done"] and s["ok"] is True and s["action"] == "fix-skip" and ran == []
    assert s["story"][-1] == "The tunnel was removed meanwhile: nothing more to repair."
    assert not any(lvl == "bad" or "started" in txt or "did not help" in txt for lvl, txt in events_of("rm-fix1"))
    assert actions.recent_fixes("rm-fix1") == 0                   # not counted against the hourly budget


def test_a_repair_stops_between_the_steps_when_its_tunnel_is_gone_and_says_nothing_red(monkeypatch):
    """The real case: the removal finished between the first restart and the second."""
    tid = make("rm-fix2", "down")
    fix_world(monkeypatch)
    ran = []

    def run(node, name, params=None, timeout=60):
        ran.append(params["UNITS"])

        with session() as db:                                     # the removal ends (the row is gone) while the first restart runs
            t = db.get(Tunnel, tid)

            if t is not None:
                db.delete(t)

        return ""

    monkeypatch.setattr(actions, "run_action", run)
    aid = actions.fix(tid, source="auto")
    s = wait_done(aid)
    assert s["done"] and s["ok"] is True and len(ran) == 1 and s["action"] == "fix-skip"                  # the second step was not run
    assert s["story"][-1] == "The tunnel was removed meanwhile: nothing more to repair."
    assert not any(lvl == "bad" or "did not help" in txt for lvl, txt in events_of("rm-fix2"))


def test_a_look_after_the_restart_that_finds_the_tunnel_removed_ends_quietly_in_both_ways_of_repairing(monkeypatch):
    for state in ("down", "degraded"):                            # down: every step, then ONE look; degraded: a look after each step
        name = f"rm-fix3-{state}"
        tid = make(name, state)
        fix_world(monkeypatch)
        monkeypatch.setattr(actions, "_recheck", lambda *a, **k: ("removed", "the tunnel was removed meanwhile"))

        if state == "degraded":
            monkeypatch.setattr(actions, "judge", lambda *a, **k: ("degraded", "slow"))

        aid = actions.fix(tid, source="auto")
        s = wait_done(aid)
        assert s["done"] and s["ok"] is True and s["action"] == "fix-skip", state
        assert s["story"][-1] == "The tunnel was removed meanwhile: nothing more to repair.", state
        assert not any(lvl == "bad" or "did not help" in txt for lvl, txt in events_of(name)), state


def test_a_repair_that_really_fails_still_ends_red(monkeypatch):
    """The other side of the same rule: only a tunnel that is gone is quiet - one that is there and still not up is the Doctor's to report."""
    tid = make("rm-fix4", "down")
    fix_world(monkeypatch)
    monkeypatch.setattr(actions, "_recheck", lambda *a, **k: ("down", "no data comes back"))
    aid = actions.fix(tid, source="auto")
    s = wait_done(aid)
    assert s["done"] and s["ok"] is False and s["action"] == "fix"
    assert any(lvl == "bad" and "did not help" in txt for lvl, txt in events_of("rm-fix4"))


def test_the_look_after_a_restart_calls_a_tunnel_in_removal_removed(monkeypatch):
    tid = make("rm-rck")
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)
    monkeypatch.setattr(actions, "external_probe_one", lambda *a, **k: None)
    monkeypatch.setattr(bandwidth, "recheck", lambda tunnel_id: None)
    monkeypatch.setattr(actions, "check_tunnel", lambda tunnel_id, fresh_ext=False: None)
    plan = actions.Plan([], [], ports=[])
    tn.removal_started("rm-rck")
    assert actions._recheck(tid, plan, "203.0.113.20", 1, tries=3) == ("removed", "the tunnel was removed meanwhile")
    tn.removal_ended("rm-rck")
