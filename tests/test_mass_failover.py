"""A mass outage (1.69.230; the owner, 2026-11-05: "two or three servers off together, or half of them: after a minute start every move at once, not one by one; with only two servers left, balance the load yourself"):
every dead server is planned and started in the same round, the tunnels of one server are built together, the plans do not all choose the server that ranks first, and a server that came back and went off again is replaced again."""
import threading
import time

import pytest
from sqlalchemy import select
from test_failover import _servers_and_tunnels

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot, doctor, failover
from nikatel.services import settings as st


def _row(sid, name, quality=90, resources=80, tunnels=1, score=80):
    return {"id": sid, "name": name, "quality": quality, "resources": resources, "tunnels": tunnels, "unused": 100 - tunnels * 12.5, "score": score, "rtt_ms": 40, "loss_pct": 0}


def _plan(server_id, n, a=(101, "A", 90, 90), b=(102, "B", 90, 85)):
    rows = [_row(a[0], a[1], quality=a[2], resources=a[3]), _row(b[0], b[1], quality=b[2], resources=b[3])]
    return {"server_id": server_id, "server": f"dead{server_id}", "tunnels": [{"id": i, "name": f"t{server_id}-{i}"} for i in range(n)], "problems": [], "target": None,
            "_rankings": {i: [dict(r) for r in rows] for i in range(n)}}


def test_tunnels_on_their_way_take_room_from_a_server():
    rows = [_row(1, "A", resources=90), _row(2, "B", resources=85), {"id": 3, "name": "C", "score": 0}]
    assert [r["id"] for r in failover.with_reserved(rows, {})] == [1, 2, 3]
    out = failover.with_reserved(rows, {1: 3})
    assert [r["id"] for r in out] == [2, 1, 3]                                     # three tunnels are on their way to A: B ranks first now
    a = out[1]
    assert a["reserved"] == 3 and a["tunnels"] == 4 and a["resources"] == 90 - 3 * failover.RESERVE_COST and a["quality"] == 90 - 3 * failover.RESERVE_QUALITY
    assert out[2] == {"id": 3, "name": "C", "score": 0}                            # a server nobody can use is left as it is, and stays last


def test_the_plans_of_one_outage_spread_over_the_servers_that_are_left():
    book: dict = {}
    got = [failover.assign_target(_plan(sid, n), book)["target"]["id"] for sid, n in ((10, 2), (11, 2), (12, 1))]
    assert got == [101, 102, 101]                                                  # A, then B (A has two tunnels on their way), then A again (B has two as well, A less room taken)
    assert failover.held(book) == {101: 3, 102: 2}


def test_a_plan_that_has_its_target_or_finds_no_room_is_left_as_it_is():
    p = {"server_id": 10, "tunnels": [{"id": 1, "name": "a"}], "problems": [], "target": {"id": 5, "name": "x", "ip": "203.0.113.5"}}
    assert failover.assign_target(p) is p and p["target"]["id"] == 5 and failover.held() == {}      # no `_rankings`: nothing to pick, nothing reserved
    q = {"server_id": 10, "tunnels": [{"id": 1, "name": "a"}], "problems": [], "target": None, "_rankings": {}}
    assert failover.assign_target(q)["target"] is None and q["problems"] == ["no other Iran server can carry all of these tunnels"] and "_rankings" not in q


def test_a_reservation_is_given_back_and_a_forgotten_one_expires():
    p = failover.assign_target(_plan(10, 2))
    assert failover.held() == {101: 2} and p["reserved"]
    failover.release(p)
    failover.release(p)                                                            # twice is harmless
    assert failover.held() == {} and "reserved" not in p
    failover._RESERVED["old"] = (101, 3, time.time() - failover.RESERVE_TTL - 1)
    assert failover.held() == {}                                                   # a plan that died in the middle does not hold a server's room for ever


def test_a_server_whose_tunnels_went_back_to_it_is_replaced_again_when_it_goes_off_a_second_time():
    now = int(time.time())
    down = {"server_id": 5, "reason": "down", "at": now - 3600}
    s = {"pending": [], "history": [down]}
    assert failover._busy(s, 5, now) is True                                       # replaced an hour ago and nothing came back: the six hours stand
    s["history"].append({"server_id": 9, "reason": "return", "at": now - 600, "target": {"id": 5}})
    assert failover._busy(s, 5, now) is False                                      # its tunnels went back after that: a new outage is a new failover
    s["history"] = [{"server_id": 9, "reason": "return", "at": now - 7200, "target": {"id": 5}}, down]
    assert failover._busy(s, 5, now) is True                                       # a return from BEFORE the failover says nothing about it
    assert failover._busy({"pending": [{"server_id": 5}], "history": []}, 5, now) is True


def _outage(dead=(3, 2, 1), survivors=2):
    """Dead Iran servers with the given number of tunnels each, `survivors` Iran servers that are on (one tunnel each), one foreign server."""
    with session() as db:
        f = Server(name="fx", host="203.0.113.200", role="foreign", online=True)
        db.add(f)
        db.flush()
        out = {"dead": [], "alive": [], "f": f.id}

        for i, n in enumerate(dead):
            d = Server(name=f"dead{i}", host=f"203.0.113.{10 + i}", role="iran", online=False)
            db.add(d)
            db.flush()
            out["dead"].append(d.id)
            db.add_all([Tunnel(name=f"d{i}-t{k}", type="backhaul", transport="tcpmux", iran_server_id=d.id, foreign_server_id=f.id, state="down", forwarded_ports=[8000 + 10 * i + k]) for k in range(n)])

        for i in range(survivors):
            a = Server(name=f"alive{i}", host=f"203.0.113.{50 + i}", role="iran", online=True)
            db.add(a)
            db.flush()
            out["alive"].append(a.id)
            db.add(Tunnel(name=f"a{i}-t0", type="backhaul", transport="tcpmux", iran_server_id=a.id, foreign_server_id=f.id, state="up", forwarded_ports=[9000 + i]))

    return out


def _fake_dead(ids):
    def plan_dead(sid):
        with session() as db:
            s = db.get(Server, sid)
            ts = [{"id": t.id, "name": t.name} for t in db.scalars(select(Tunnel).where(Tunnel.iran_server_id == sid))]

        rows = [_row(ids["alive"][0], "alive0", resources=90), _row(ids["alive"][1], "alive1", resources=85)]
        return {"server_id": sid, "server": s.name, "ip": s.host, "reason": "down", "why": "off", "tunnels": ts, "dns": [], "pulled": [], "problems": [], "target": None, "_rankings": {t["id"]: [dict(r) for r in rows] for t in ts}}

    return plan_dead


def test_check_starts_every_dead_server_in_the_same_round_and_spreads_them(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    st.put("repair", {"mode": "move", "server_minutes": 5})
    ids = _outage()
    ran = []
    monkeypatch.setattr(failover, "plan_dead", _fake_dead(ids))
    monkeypatch.setattr(failover, "run_plan", lambda p: ran.append(p) or 1)
    st.put("failover", {"seen": {str(d): 1 for d in ids["dead"]}})
    failover.check()
    assert [p["server"] for p in ran] == ["dead0", "dead1", "dead2"]               # all three in ONE round, the one with the most tunnels first
    assert {p["target"]["id"] for p in ran} == set(ids["alive"]) and all(p["reserved"] for p in ran)      # and not all onto the same server
    assert sum(failover.held().values()) == 6
    failover.check()
    assert len(ran) == 3                                                           # tried a moment ago: not twice


def test_one_server_that_cannot_be_planned_or_started_does_not_hold_the_others_back(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    st.put("repair", {"mode": "move", "server_minutes": 5})
    ids = _outage()
    fake, ran = _fake_dead(ids), []

    def plan(sid):
        if sid == ids["dead"][0]:
            raise ValueError("boom")

        return fake(sid)

    def run(p):
        if p["server_id"] == ids["dead"][1]:
            raise RuntimeError("cannot start")

        ran.append(p["server"])
        return 1

    monkeypatch.setattr(failover, "plan_dead", plan)
    monkeypatch.setattr(failover, "run_plan", run)
    st.put("failover", {"seen": {str(d): 1 for d in ids["dead"]}})
    failover.check()
    assert ran == ["dead2"]                                                        # the first could not be planned, the second could not start: the third still moved
    assert sum(failover.held().values()) == 1                                      # and the one that could not start gave its room back


def test_a_server_that_is_off_is_looked_at_by_its_own_job_not_only_in_the_doctors_round(monkeypatch):
    called = []
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "check", lambda: called.append(1))
    st.put("repair", {"mode": "fix"})
    failover.tick()
    st.put("repair", {"mode": "off"})
    failover.tick()
    assert called == []                                                            # fix and off move nothing by themselves
    st.put("repair", {"mode": "move"})
    failover.tick()
    assert called == [1]


def test_a_second_round_that_starts_while_the_first_still_plans_goes_away(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    st.put("repair", {"mode": "move"})
    seen = []
    monkeypatch.setattr(failover, "_check", lambda: seen.append(1))
    assert failover._CHECKING.acquire(blocking=False)

    try:
        failover.check()
        assert seen == []
    finally:
        failover._CHECKING.release()

    failover.check()
    assert seen == [1]


def test_the_tunnels_of_a_dead_server_are_built_at_the_same_time(monkeypatch):
    ids = _servers_and_tunnels()                                                   # ir1: t1, t2, t3

    with session() as db:
        db.get(Server, ids["ir1"]).online = False

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: None)
    barrier, built = threading.Barrier(3, timeout=10), []

    def replace(aid, tunnel_id, iran_id, foreign_id, kind, why, skip_offline=False, **kw):
        barrier.wait()                                                             # a build that waits for the other two: one after the other it would never get past here
        built.append((tunnel_id, iran_id, skip_offline))
        return True

    monkeypatch.setattr(autopilot, "_replace", replace)
    p = failover._make_plan(ids["ir1"], None, "down", target_id=ids["ir2"])
    p["id"] = "mass-1"
    assert failover._work(7, p) is True
    assert sorted(b[0] for b in built) == sorted([ids["t1"], ids["t2"], ids["t3"]]) and {b[1] for b in built} == {ids["ir2"]} and all(b[2] for b in built)
    m = doctor.state()["moves"][0]
    assert sorted(m["tunnels"]) == ["t1", "t2", "t3"] and m["ok"] is True


def test_a_tunnel_that_fails_does_not_stop_the_ones_built_beside_it(monkeypatch):
    ids = _servers_and_tunnels()

    with session() as db:
        db.get(Server, ids["ir1"]).online = False

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))

    def replace(aid, tunnel_id, *a, **k):
        if tunnel_id == ids["t2"]:
            raise OSError("disk full")

        return True

    monkeypatch.setattr(autopilot, "_replace", replace)
    p = failover._make_plan(ids["ir1"], None, "down", target_id=ids["ir2"])
    p["id"] = "mass-2"
    assert failover._work(7, p) is False
    assert any("t2: not moved" in x for x in said) and sorted(doctor.state()["moves"][0]["tunnels"]) == ["t1", "t3"]      # the other two moved; the DNS stays where it was


def test_the_copies_of_tunnels_removed_at_the_same_time_are_all_kept():
    from nikatel.services import builder, trash

    names = [f"tx{i}" for i in range(12)]
    threads = [threading.Thread(target=trash.add, args=({"name": n, "type": "backhaul"},)) for n in names]
    threads += [threading.Thread(target=builder.pending_put, args=({"name": n, "iran_id": 1},)) for n in names]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    assert {i["name"] for i in st.get("trash")["items"]} == set(names)             # (a lost copy is a tunnel that cannot be brought back from 'Recently removed')
    assert {i["name"] for i in st.get("pending_builds")["items"]} == set(names)


# ------------------------------------------------------------------ the quiet time: the Doctor's own affairs wait, the configs come first
def test_a_failover_makes_the_doctors_own_affairs_wait_and_for_a_few_minutes_after_it():
    assert autopilot.mass_on() is False and autopilot.quiet_now() is False
    autopilot.mass_begin()
    assert autopilot.mass_on() is True and autopilot.quiet_now() is True           # a story runs
    autopilot.mass_end()
    assert autopilot.mass_on() is True                                             # it ended: the DNS settles and the old tunnels leave first
    assert autopilot._MASS["until"] - time.time() > autopilot.MASS_SETTLE - 5
    autopilot._MASS["until"] = time.time() - 1
    assert autopilot.mass_on() is False
    autopilot.mass_end()
    assert autopilot._MASS["running"] == 0                                         # an end without a begin does not make it negative
    autopilot.mass_note(60)
    assert autopilot.mass_on() is True


def test_the_minute_of_a_dead_server_that_carries_tunnels_is_already_quiet(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    st.put("repair", {"mode": "move", "server_minutes": 5})
    ids = _outage(dead=(2,))
    monkeypatch.setattr(failover, "plan_dead", _fake_dead(ids))
    monkeypatch.setattr(failover, "run_plan", lambda p: 1)
    st.put("failover", {"seen": {str(ids["dead"][0]): int(time.time()) - 10}})      # ten seconds into its minute: nothing is planned yet ...
    failover.check()
    assert autopilot.mass_on() is True                                             # ... but the Doctor's own affairs wait already


def test_a_dead_server_that_carries_nothing_does_not_make_the_doctor_quiet(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    st.put("repair", {"mode": "move", "server_minutes": 5})
    _outage(dead=(0,))
    failover.check()
    assert autopilot.mass_on() is False                                            # (a server that was switched off long ago and has no tunnels left is not an emergency)


def test_run_plan_keeps_the_quiet_time_while_the_story_runs_and_gives_it_up_when_it_cannot_start(monkeypatch):
    p = {"server": "ir1", "server_id": 1, "target": {"id": 2, "name": "ir2", "ip": "203.0.113.9"}, "tunnels": []}
    seen = []
    monkeypatch.setattr(failover, "_work", lambda aid, plan: seen.append(autopilot._MASS["running"]) or True)
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: fn(7, *a))
    failover.run_plan(p)
    assert seen == [1] and autopilot._MASS["running"] == 0 and autopilot.mass_on() is True          # running while the story ran, settling after it

    def refuse(*a, **k):
        raise autopilot.Busy("busy")

    monkeypatch.setattr(autopilot, "_start", refuse)

    with pytest.raises(autopilot.Busy):
        failover.run_plan(p)

    assert autopilot._MASS["running"] == 0


def test_the_jobs_that_are_not_about_connecting_the_configs_stand_still_while_a_failover_runs(monkeypatch):
    from nikatel.services import (
        balance,
        bandwidth,
        besttoday,
        engines,
        netwatch,
        nodedns,
        pg_auto,
        retypewatch,
        tunnel_test,
    )

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(bandwidth.jobs, "stopping", lambda: False)
    st.put("repair", {"mode": "move"})

    class Tripped(BaseException):                                                  # (not an Exception: a job that swallows errors must not hide it)
        pass

    def trip(*a, **k):
        raise Tripped()

    calls = [(bandwidth, "_zero_watch", bandwidth.run), (engines, "auto_due", engines.auto_run), (nodedns, "_servers", nodedns.run), (balance, "session", balance.run),
             (tunnel_test, "quality_all", besttoday.refresh), (retypewatch, "_doc", retypewatch.run), (doctor, "saturated_servers", doctor.resources),
             (netwatch, "report", doctor.returns), (pg_auto, "state", pg_auto.run)]

    for mod, name, fn in calls:
        monkeypatch.setattr(mod, name, trip)
        autopilot._MASS.update(running=0, until=0.0)

        with pytest.raises(Tripped):                                               # (the trip wire can be reached when nothing runs)
            fn()

        autopilot.mass_begin()
        fn()                                                                       # a failover runs: it returns before the trip wire

    monkeypatch.setattr(autopilot, "_snapshot", trip)
    monkeypatch.setattr(failover, "move_blocked", lambda: "")
    autopilot._MASS.update(running=0, until=0.0)

    with pytest.raises(Tripped):
        failover.quality_move(1, 1, "quality", 10.0, 50.0, lambda s: None)

    autopilot.mass_begin()
    assert failover.quality_move(1, 1, "quality", 10.0, 50.0, lambda s: None) is False             # no move for quality in the middle of a failover


def test_the_result_of_a_move_is_looked_at_and_told(monkeypatch):
    from nikatel.models import Config, Event

    told = []
    monkeypatch.setattr(failover, "_notice", told.append)

    with session() as db:
        db.add_all([Config(name="c1", status="up", meta={"address": "Tr1.example.test", "port": 1}), Config(name="c2", status="down", meta={"address": "tr1.example.test", "port": 2}),
                    Config(name="c3", status="down", meta={"address": "other.example.test", "port": 3})])

    failover._follow_later({"server": "ir1", "target": {"name": "ir2"}}, [{"fqdn": "tr1.example.test"}])
    failover._follow_later({"server": "ir1", "target": {"name": "ir2"}}, [])                                 # nothing moved: nothing to look at
    assert len(failover._FOLLOW) == 1
    failover.follow_up(now=time.time() + 10)
    assert told == [] and len(failover._FOLLOW) == 1                                                         # not yet
    failover.follow_up(now=time.time() + failover.FOLLOW_AFTER + 1)
    assert len(told) == 1 and "1 of 2 configs" in told[0] and "c2" in told[0] and "c3" not in told[0] and "looks again" in told[0]
    assert len(failover._FOLLOW) == 1 and failover._FOLLOW[0]["looks"] == 1                                  # one more look is kept

    with session() as db:
        db.scalar(select(Config).where(Config.name == "c2")).status = "up"

    failover.follow_up(now=time.time() + failover.FOLLOW_AFTER + failover.FOLLOW_AGAIN + 10)
    assert failover._FOLLOW == [] and len(told) == 1                                                         # all connect now: an event, no message

    with session() as db:
        texts = [e.text for e in db.scalars(select(Event).order_by(Event.id))]

    assert len(texts) == 2 and "1 of 2" in texts[0] and "2 of 2" in texts[1]


def test_a_name_no_config_rides_is_not_followed_up(monkeypatch):
    told = []
    monkeypatch.setattr(failover, "_notice", told.append)
    failover._follow_later({"server": "ir1", "target": {"name": "ir2"}}, [{"fqdn": "nobody.example.test"}])
    failover.follow_up(now=time.time() + failover.FOLLOW_AFTER + 1)
    assert told == [] and failover._FOLLOW == []


def _fake_ranking(ids):
    def ranking(tunnel_id):
        return [_row(ids["dead"][1], "dead1", resources=99), _row(ids["alive"][0], "alive0", resources=90), _row(ids["alive"][1], "alive1", resources=85)]

    return ranking


def test_the_simulation_shows_where_everything_would_go_and_changes_nothing(client, monkeypatch):
    ids = _outage(dead=(3, 2, 0))
    monkeypatch.setattr(autopilot, "ranking", _fake_ranking(ids))
    r = client.post("/api/failover/simulate", json={"servers": ids["dead"]})
    assert r.status_code == 200
    j = r.json()
    assert j["off"] == ["dead0", "dead1", "dead2"] and j["stuck"] == []
    moves = {m["server"]: m for m in j["moves"]}
    assert moves["dead0"]["target"] == "alive0" and moves["dead1"]["target"] == "alive1"                # the off server dead1 is never a target, although it ranks first in the fake; two plans, two servers
    assert moves["dead2"]["tunnels"] == [] and len(moves["dead0"]["tunnels"]) == 3
    assert j["load_after"] == {"alive0": 1 + 3, "alive1": 1 + 2}
    assert failover.held() == {}                                                                         # nothing stays reserved
    assert client.post("/api/failover/simulate", json={"servers": []}).status_code == 422

    with session() as db:
        assert db.scalar(select(Tunnel).where(Tunnel.name == "d0-t0")).iran_server_id == ids["dead"][0]    # no tunnel was touched


def test_a_server_with_no_room_anywhere_is_listed_as_stuck(monkeypatch):
    ids = _outage(dead=(2,), survivors=1)
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [])
    j = failover.simulate(ids["dead"])
    assert j["stuck"] == ["dead0"] and j["moves"][0]["target"] is None and j["moves"][0]["problems"]
