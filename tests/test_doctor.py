"""The Tunnel Doctor's modes and moves: the pure decisions, a provider-wide outage handled server by server, an exhausted server
(a suggestion in mode fix, a move in mode move), the return after three healthy hours, and make before break."""
import time

from sqlalchemy import select
from test_failover import FakeProv, _dns_world, _servers_and_tunnels

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot, doctor, failover
from nikatel.services import settings as st


def test_pure_decisions():
    assert doctor.is_saturated([(95, 10)] * 5).startswith("CPU") and doctor.is_saturated([(10, 95)] * 5).startswith("RAM")
    assert doctor.is_saturated([(95, 10)] * 4) == "" and doctor.is_saturated([(95, 10)] * 4 + [(50, 10)]) == ""     # too few samples / one calm sample
    now = 1_000_000
    moves = [{"at": now - 3600, "tunnels": ["a"], "ok": True}, {"at": now - 7200 * 3, "tunnels": ["a"], "ok": True}, {"at": now - 10, "tunnels": ["b"], "ok": False}]
    assert "hours" in doctor.may_move("a", moves, now)                                       # the cooldown
    assert doctor.may_move("b", moves, now) == ""                                            # a move that failed does not count
    assert "limit" in doctor.may_move("c", [{"at": now - 3600 * i, "tunnels": ["c"], "ok": True} for i in (3, 6, 9)], now)
    assert doctor.pick_busiest([{"name": "x", "state": "up", "rx": 1, "tx": 1}, {"name": "y", "state": "up", "rx": 5, "tx": 0}, {"name": "z", "state": "down", "rx": 99, "tx": 99}])["name"] == "y"
    assert doctor.pick_busiest([]) is None
    e = {"reason": "down", "returned": False, "ok": True}
    assert doctor.ready_to_return(e, None, now, "ok") == "the server is still off"
    assert "minutes" in doctor.ready_to_return(e, now - 600, now, "ok")
    assert "disturbed" in doctor.ready_to_return(e, now - 4 * 3600, now, "bad")
    assert doctor.ready_to_return(e, now - 4 * 3600, now, "ok") == ""
    assert doctor.ready_to_return({**e, "reason": "resources"}, now - 4 * 3600, now, "ok") != ""     # a resource move does not come back by itself
    assert failover._temp_name("a-very-long-tunnel") == "a-very-long-mv" and len(failover._temp_name("x" * 15)) <= 15


def test_mode_and_the_settings_api(client, monkeypatch):
    from nikatel.config import get_settings

    assert doctor.mode() == "fix"                                                            # the default
    st.put("repair", {"auto": False})
    assert doctor.mode() == "off"                                                            # an old setting without a mode
    assert client.put("/api/settings/repair", json={"mode": "move", "after_seconds": 60, "server_minutes": 10}).status_code == 400       # read-only
    assert client.put("/api/settings/repair", json={"mode": "off", "after_seconds": 90, "server_minutes": 15}).json()["server_minutes"] == 15
    assert client.put("/api/settings/repair", json={"mode": "nuke"}).status_code == 422
    monkeypatch.setattr(get_settings(), "read_only", False)
    j = client.put("/api/settings/repair", json={"mode": "move", "after_seconds": 60, "server_minutes": 10}).json()
    assert j["mode"] == "move" and j["auto"] is True and doctor.can_move() is True
    j = client.get("/api/doctor").json()
    assert j["mode"] == "move" and j["moves"] == [] and j["rails"]["max_per_day"] == 3 and j["server_minutes"] == 10
    assert client.post("/api/doctor/moves/nope/return").status_code == 400 and client.post("/api/doctor/suggestions/nope/apply").status_code == 400


def _outage_world():
    with session() as db:
        p1, p2, ok_, f = (Server(name="prov-a1", host="203.0.113.1", role="iran", online=False), Server(name="prov-a2", host="203.0.113.2", role="iran", online=False),
                          Server(name="other", host="203.0.113.3", role="iran", online=True), Server(name="fx", host="203.0.113.50", role="foreign", online=True))
        db.add_all([p1, p2, ok_, f])
        db.flush()

        for n, s in (("a", p1), ("b", p1), ("c", p2)):
            db.add(Tunnel(name=n, type="backhaul", transport="tcpmux", iran_server_id=s.id, foreign_server_id=f.id, state="down", forwarded_ports=[80]))

        return {"p1": p1.id, "p2": p2.id, "ok": ok_.id}


def _fake_plan(ids, target=True):
    def plan_for(sid):
        with session() as db:
            s = db.get(Server, sid)
            tunnels = [{"id": t.id, "name": t.name} for t in db.scalars(select(Tunnel).where(Tunnel.iran_server_id == sid))]

        return {"server_id": sid, "server": s.name, "ip": s.host, "reason": "down", "why": "off", "tunnels": tunnels, "dns": [], "pulled": [],
                "target": {"id": ids["ok"], "name": "other", "ip": "203.0.113.3"} if target else None, "problems": [] if target else ["no other Iran server can carry all of these tunnels"]}

    return plan_for


def test_a_provider_wide_outage_is_handled_server_by_server_without_asking(monkeypatch):
    from nikatel.config import get_settings
    from nikatel.services import netwatch

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(netwatch, "report", lambda: {"items": [], "iran": []})
    told, ran = [], []
    monkeypatch.setattr(failover, "_notice", lambda text: told.append(text))
    st.put("repair", {"mode": "move", "server_minutes": 10})
    ids = _outage_world()
    monkeypatch.setattr(failover, "plan_for", _fake_plan(ids))
    monkeypatch.setattr(failover, "run_plan", lambda p: ran.append(p["server"]) or 1)
    st.put("failover", {"seen": {str(ids["p1"]): 1, str(ids["p2"]): 1}})
    doctor.run()                                                      # the job: both servers have been off for long
    failover.check()
    assert ran == ["prov-a1", "prov-a2"]                                # the one with more tunnels first, one per tick, nobody asked
    failover.check()
    assert ran == ["prov-a1", "prov-a2"]                                # tried a moment ago: not again
    assert any("prov-a1" in t and "move 2 tunnel" in t for t in told)
    assert doctor.state()["back"] == {str(ids["ok"]): doctor.state()["back"][str(ids["ok"])]}      # the clock of the server that is on


def test_without_a_healthy_server_the_doctor_tells_and_tries_again_later(monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    told, ran = [], []
    monkeypatch.setattr(failover, "_notice", lambda text: told.append(text))
    st.put("repair", {"mode": "move", "server_minutes": 10})
    ids = _outage_world()
    monkeypatch.setattr(failover, "plan_for", _fake_plan(ids, target=False))
    monkeypatch.setattr(failover, "run_plan", lambda p: ran.append(p["server"]) or 1)
    st.put("failover", {"seen": {str(ids["p1"]): 1}})
    failover.check()
    assert ran == [] and any("no healthy Iran server" in t and "10 minutes" in t for t in told) and str(ids["p1"]) in st.get("failover")["tried"]


def test_an_exhausted_server_is_a_suggestion_in_mode_fix_and_a_move_in_mode_move(monkeypatch):
    from nikatel.config import get_settings
    from nikatel.services import detect

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(doctor, "saturated_servers", lambda now=None: [{"id": 1, "name": "ir1", "why": "CPU at or above 90 % for 15 minutes"}])
    monkeypatch.setattr(doctor, "_tunnels_on", lambda sid: [{"id": 11, "name": "t-busy", "state": "up", "rx": 50, "tx": 5, "foreign_id": 9, "ports": "80"}, {"id": 12, "name": "t-idle", "state": "up", "rx": 1, "tx": 1, "foreign_id": 9, "ports": "81"}])
    monkeypatch.setattr(detect, "auto_iran", lambda foreign_id, ports="": [{"id": 1, "name": "ir1", "score": 50, "resources": 5, "quality": 90}, {"id": 2, "name": "ir2", "score": 80, "resources": 80, "quality": 85, "reason": "40 ms"}])
    st.put("repair", {"mode": "fix"})

    with session() as db:                                                                    # (the tunnel the suggestion is about must exist: a suggestion for a tunnel that is gone is withdrawn)
        ir, fx = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="fx", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir, fx])
        db.flush()
        db.add(Tunnel(name="t-busy", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fx.id, state="up", forwarded_ports=[80]))

    doctor.resources()
    sug = doctor.state()["suggestions"]
    assert len(sug) == 1 and sug[0]["tunnel"] == "t-busy" and sug[0]["to"] == "ir2" and sug[0]["kind"] == "resources" and "busiest" in sug[0]["why"]
    st.put("repair", {"mode": "move"})
    st.put("doctor", {"res_at": {}, "suggestions": []})
    moved = []
    monkeypatch.setattr(doctor, "move_now", lambda tid, iran_id, reason="manual", why="": moved.append((tid, iran_id, reason)) or 1)
    doctor.resources()
    assert moved == [(11, 2, "resources")]
    doctor.resources()
    assert moved == [(11, 2, "resources")]                                 # one per hour per server


def test_the_tunnels_go_back_after_the_server_has_been_healthy_for_three_hours(monkeypatch):
    from nikatel.config import get_settings
    from nikatel.services import netwatch

    monkeypatch.setattr(get_settings(), "read_only", False)
    st.put("repair", {"mode": "move"})
    now = int(time.time())

    with session() as db:
        ir1, ir2, f = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="ir2", host="203.0.113.9", role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir1, ir2, f])
        db.flush()
        t = Tunnel(name="t1", type="backhaul", transport="tcpmux", iran_server_id=ir2.id, foreign_server_id=f.id, state="up", forwarded_ports=[80])
        db.add(t)
        db.flush()
        ids = {"ir1": ir1.id, "ir2": ir2.id, "t1": t.id}

    entry = {"id": "m1", "at": now - 5 * 3600, "reason": "down", "why": "off", "tunnels": ["t1"], "from_id": ids["ir1"], "from": "ir1", "from_ip": "203.0.113.5", "to_id": ids["ir2"], "to": "ir2", "to_ip": "203.0.113.9", "dns": 1, "ok": True, "returned": False, "returned_at": None, "leftover": []}
    st.put("doctor", {"moves": [entry], "back": {str(ids["ir1"]): now - 600}})
    monkeypatch.setattr(netwatch, "report", lambda: {"items": [], "iran": [{"name": "ir1", "level": "ok"}]})
    moved = []
    monkeypatch.setattr(doctor, "move_now", lambda tid, iran_id, reason="manual", why="": moved.append((tid, iran_id, reason)) or 1)
    doctor.returns()
    assert moved == []                                                    # back for 10 minutes only
    st.put("doctor", {"back": {str(ids["ir1"]): now - 4 * 3600}})
    doctor.returns()
    assert moved == [(ids["t1"], ids["ir1"], "return")]
    plan = {"id": "r1", "server_id": ids["ir2"], "server": "ir2", "ip": "203.0.113.9", "reason": "return", "why": "back", "tunnels": [{"id": ids["t1"], "name": "t1"}], "target": {"id": ids["ir1"], "name": "ir1", "ip": "203.0.113.5"}, "dns": []}
    doctor.record_move(plan, ["t1"], [], True)
    moves = doctor.state()["moves"]
    assert moves[0]["reason"] == "return" and next(m for m in moves if m["id"] == "m1")["returned"] is True


def _finished(ok: bool):
    def wait(aid, limit=900):
        return ok, ["Built." if ok else "the build failed: boom"]

    return wait


def test_a_move_from_a_server_that_is_on_builds_first_then_the_dns_waits_the_ttl_and_removes_the_old(monkeypatch):
    from nikatel.config import get_settings
    from nikatel.services import builder

    monkeypatch.setattr(get_settings(), "read_only", False)
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    st.put("repair", {"mode": "move"})
    events, said = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))

    def build(snap, iran_id, foreign_id, kind, opts=None):
        events.append(("build", snap["name"], iran_id))

        with session() as db:
            db.add(Tunnel(name=snap["name"], type="backhaul", transport="tcpmux", iran_server_id=iran_id, foreign_server_id=foreign_id, state="up", forwarded_ports=[8075]))

        return 0

    def delete(tid, keep_record=False, trash=False, skip_offline=False):
        with session() as db:
            t = db.get(Tunnel, tid)
            events.append(("delete", t.name, trash))
            db.delete(t)

        return ["Iran: removed", "foreign: removed"]

    monkeypatch.setattr(autopilot, "_build_kind", build)
    monkeypatch.setattr(autopilot, "_wait", _finished(True))
    monkeypatch.setattr(builder, "delete_tunnel", delete)
    monkeypatch.setattr(failover, "_sleep", lambda secs: events.append(("sleep", secs)))
    p = failover._make_plan(ids["ir1"], {ids["t2"]}, "manual", target_id=ids["ir2"])
    p["id"] = "m-1"
    assert failover._work(7, p) is True
    assert events == [("build", "t2-mv", ids["ir2"]), ("sleep", 420), ("delete", "t2", True)]           # new first, the DNS (TTL 120 + 300 s), then the old
    assert FakeProv.calls == [("update", "arvan", "example.test", "r-b", ["203.0.113.9"], 120, True)]

    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == "t2"))
        assert t is not None and t.iran_server_id == ids["ir2"] and db.scalar(select(Tunnel).where(Tunnel.name == "t2-mv")) is None      # the new one carries the old name

    m = doctor.state()["moves"][0]
    assert m["tunnels"] == ["t2"] and m["from"] == "ir1" and m["to"] == "ir2" and m["dns"] == 1 and m["ok"] is True
    log = st.get("failover")["dns_log"][-1]
    assert log["account"] == "arvan" and log["fqdn"] == "b.example.test" and log["before"] == {"id": "r-b", "was": "old"}
    assert any("old tunnels stay for 7 more minutes" in x for x in said)


def test_when_the_new_tunnel_does_not_come_up_the_old_one_stays_and_the_dns_is_not_touched(monkeypatch):
    from nikatel.config import get_settings
    from nikatel.services import builder

    monkeypatch.setattr(get_settings(), "read_only", False)
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    said, events = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "_build_kind", lambda snap, iran_id, foreign_id, kind, opts=None: events.append(("build", snap["name"])) or 0)
    monkeypatch.setattr(autopilot, "_wait", _finished(False))
    monkeypatch.setattr(builder, "delete_tunnel", lambda tid, **kw: events.append(("delete", tid)) or [])
    monkeypatch.setattr(failover, "_sleep", lambda secs: events.append(("sleep", secs)))
    p = failover._make_plan(ids["ir1"], {ids["t2"]}, "manual", target_id=ids["ir2"])
    p["id"] = "m-2"
    assert failover._work(7, p) is False
    assert events == [("build", "t2-mv")] and FakeProv.calls == [] and any("the old one stays" in x for x in said)

    with session() as db:
        assert db.get(Tunnel, ids["t2"]).iran_server_id == ids["ir1"]

    assert doctor.state()["moves"][0]["ok"] is False


def test_a_suggestion_stands_only_while_its_problem_does():
    """The owner, 2026-10-16: three suggestions of two days earlier ("the route loses too many packets") stood on the Doctor's page for tunnels that had been up and clean for a day."""
    now = 1_000_000
    route = {"id": "s-1", "kind": "route", "tunnel": "t1", "from_id": 4, "from": "ir1"}
    up_long = {"state": "up", "state_since": now - 3600, "loss": 0.0}
    assert "not in the list" in doctor.suggestion_valid(route, None, set(), now)                                       # the tunnel is gone
    assert "problem is over" in doctor.suggestion_valid(route, up_long, set(), now)                                    # up and clean for an hour: withdrawn
    assert "problem is over" in doctor.suggestion_valid(route, {**up_long, "loss": None}, set(), now)                  # (a loss that cannot be measured is not a loss)
    assert doctor.suggestion_valid(route, {**up_long, "state_since": now - 120}, set(), now) == ""                     # only two minutes up: it may fall again
    assert doctor.suggestion_valid(route, {**up_long, "state": "degraded"}, set(), now) == ""
    assert doctor.suggestion_valid(route, {**up_long, "state": "down"}, set(), now) == ""
    assert doctor.suggestion_valid(route, {**up_long, "loss": 30.0}, set(), now) == ""                                 # up, but it still loses what users feel
    res = {"id": "s-2", "kind": "resources", "tunnel": "t1", "from_id": 4, "from": "ir1"}
    assert doctor.suggestion_valid(res, {"state": "down", "state_since": 0, "loss": None}, {4}, now) == ""            # the server is still exhausted: the suggestion stands whatever the tunnel does
    assert "no longer exhausted" in doctor.suggestion_valid(res, up_long, set(), now) and "no longer exhausted" in doctor.suggestion_valid(res, up_long, {9}, now)


def test_the_page_and_the_job_withdraw_a_suggestion_whose_tunnel_is_healthy_again(monkeypatch):
    from nikatel.models import Event

    ids = _servers_and_tunnels()                                                                                       # t1 is down, t2 and t3 are up
    now = int(time.time())

    with session() as db:
        for n in ("t2", "t3"):
            t = db.scalar(select(Tunnel).where(Tunnel.name == n))
            t.state_since, t.detail = now - 3 * 3600, {"loss_pct": 0.0}

    monkeypatch.setattr(doctor, "saturated_servers", lambda now=None: [])
    for n, tid in (("t1", ids["t1"]), ("t2", ids["t2"]), ("t3", ids["t3"])):
        doctor.suggest("route", tid, n, ids["ir1"], "ir1", ids["ir2"], "ir2", "the route loses too many packets")

    assert [x["tunnel"] for x in doctor.state()["suggestions"]] == ["t1"]                                              # the page: t2 and t3 are fine now, only the one that is down stays
    assert doctor.refresh_suggestions() == []                                                                          # nothing more to withdraw
    doctor.suggest("route", ids["t2"], "t2", ids["ir1"], "ir1", ids["ir2"], "ir2", "again")
    st.put("repair", {"mode": "off"})
    doctor.run()                                                                                                       # the job withdraws it too, in every mode
    assert [x["tunnel"] for x in doctor.state()["suggestions"]] == ["t1"]

    with session() as db:
        said = [e.text for e in db.scalars(select(Event).where(Event.domain == "actions"))]

    assert any("suggestion was withdrawn" in m and "t2" in m and "problem is over" in m for m in said)


# ------------------------------------------------------------------ a suggestion to change the TYPE of a very slow tunnel (1.69.14)
def test_a_type_suggestion_stands_while_the_tunnel_is_slow_even_when_it_is_up_and_clean():
    """The "move" rule withdraws a suggestion when the tunnel has been up without loss for ten minutes; a very slow tunnel is up and clean all the time - that is what is wrong with it."""
    now = 1_000_000
    sug = {"id": "s-9", "kind": "type", "tunnel": "t1", "now_kind": "tcpmux", "to_kind": "tcp"}
    slow_up = {"state": "up", "state_since": now - 7200, "loss": 0.0, "kind": "tcpmux", "bw_state": "slow"}
    assert doctor.suggestion_valid(sug, slow_up, set(), now) == ""
    assert doctor.suggestion_valid(sug, {**slow_up, "bw_state": "drop"}, set(), now) == ""                                  # (a stall is the worse word for the same fault)
    assert "no longer very slow" in doctor.suggestion_valid(sug, {**slow_up, "bw_state": "ok"}, set(), now) and "no longer very slow" in doctor.suggestion_valid(sug, {**slow_up, "bw_state": None}, set(), now)
    assert "runs as paqet now, not as tcpmux" in doctor.suggestion_valid(sug, {**slow_up, "kind": "paqet"}, set(), now)      # somebody changed the type already
    assert "not in the list" in doctor.suggestion_valid(sug, None, set(), now)
    assert doctor.suggestion_valid({**sug, "now_kind": None}, {**slow_up, "kind": "paqet"}, set(), now) == ""               # (an old entry that does not say its type is judged by the speed alone)


def test_the_page_withdraws_a_type_suggestion_when_the_tunnel_is_no_longer_slow(monkeypatch):
    from nikatel.models import Event

    ids = _servers_and_tunnels()
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, ids["t2"])
        t.state_since, t.detail, t.record = now - 3 * 3600, {"loss_pct": 0.0}, {"bw": {"down": 0.8, "state": "slow"}}

    monkeypatch.setattr(doctor, "saturated_servers", lambda now=None: [])
    e = doctor.suggest("type", ids["t2"], "t2", ids["ir1"], "tcpmux", None, "tcp", "tcpmux gives 0.8 Mb/s; tcp 119", extra={"now_kind": "tcpmux", "to_kind": "tcp", "now_mbps": 0.8, "memory_mbps": 119.0})
    assert e["kind"] == "type" and e["to_kind"] == "tcp" and e["memory_mbps"] == 119.0 and e["from"] == "tcpmux" and e["to"] == "tcp" and e["to_id"] is None
    assert [x["tunnel"] for x in doctor.state()["suggestions"]] == ["t2"]                                                   # up and clean for three hours: still there

    with session() as db:
        t = db.get(Tunnel, ids["t2"])
        t.record = {"bw": {"down": 40.0, "state": "ok"}}

    assert doctor.state()["suggestions"] == []

    with session() as db:
        said = [x.text for x in db.scalars(select(Event).where(Event.domain == "actions"))]

    assert any("suggestion was withdrawn" in m and "t2" in m and "no longer very slow" in m for m in said)


def _retype_world(monkeypatch, replaced_ok=True, links=True):
    from nikatel.config import get_settings
    from nikatel.services import bandwidth

    monkeypatch.setattr(get_settings(), "read_only", False)
    ids = _servers_and_tunnels()

    with session() as db:
        db.get(Tunnel, ids["t2"]).record = {"bw": {"down": 0.8, "state": "slow"}}

    said, replaced = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append((line, done, ok)))
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: fn(1, *a) or 1)                                      # (run the work here, not in a thread)
    monkeypatch.setattr(autopilot, "_probe_links", lambda aid, snap, kinds: kinds if links else [])
    monkeypatch.setattr(autopilot, "_replace", lambda aid, tid, iran_id, foreign_id, kind, why, snap=None, **kw: replaced.append((tid, kind, why)) or replaced_ok)
    monkeypatch.setattr(bandwidth, "measure", lambda tid: ({"down": 118.0}, ""))
    e = doctor.suggest("type", ids["t2"], "t2", ids["ir1"], "tcpmux", None, "tcp", "tcpmux gives 0.8 Mb/s; the memory has tcp at 119", extra={"now_kind": "tcpmux", "to_kind": "tcp"})
    return ids, e, said, replaced


def test_one_click_builds_the_tunnel_again_as_the_suggested_type_and_tells_what_it_gives_now(monkeypatch):
    from nikatel.models import Event

    ids, e, said, replaced = _retype_world(monkeypatch)
    assert doctor.apply_suggestion(e["id"]) == 1
    assert replaced == [(ids["t2"], "tcp", "The Doctor suggested it and you asked for it.")]
    assert said[0][0].startswith("t2: changing the type tcpmux → tcp. tcpmux gives 0.8 Mb/s")
    assert said[-1] == ("t2 runs as tcp now. It gives 118.0 Mb/s in the Doctor's test now (before: 0.8).", True, True)
    assert doctor.state()["suggestions"] == []                                                                             # done: the suggestion is gone

    with session() as db:
        said_events = [x.text for x in db.scalars(select(Event).where(Event.domain == "tunnels"))]

    assert any("Tunnel t2 is now tcp (it was tcpmux): changed by your click on the Doctor's suggestion. It gives 118.0 Mb/s" in m for m in said_events)


def test_a_type_that_does_not_build_or_whose_link_does_not_pass_leaves_the_tunnel_and_the_suggestion_alone(monkeypatch):
    _ids, e, said, replaced = _retype_world(monkeypatch, replaced_ok=False)
    doctor.apply_suggestion(e["id"])
    assert replaced and said[-1] == ("The build of tcp failed; tcpmux was put back.", True, False)                      # (the swap itself puts the old type back)
    assert [x["tunnel"] for x in doctor.state()["suggestions"]] == ["t2"]                                                   # it stands: the owner may try again or dismiss it
    replaced.clear()
    monkeypatch.setattr(autopilot, "_probe_links", lambda aid, snap, kinds: [])                                          # the link of the new type does not pass between the two servers
    doctor.apply_suggestion(e["id"])
    assert replaced == [] and said[-1][1:] == (True, False) and "link of tcp does not pass between ir1 and fi1: nothing is built, t2 is not touched" in said[-1][0]
    assert [x["tunnel"] for x in doctor.state()["suggestions"]] == ["t2"]


def test_a_type_suggestion_that_is_out_of_date_or_not_allowed_changes_nothing(monkeypatch):
    import pytest

    ids, e, said, replaced = _retype_world(monkeypatch)

    with session() as db:
        db.get(Tunnel, ids["t2"]).transport = "wssmux"                                                                      # somebody changed the type after the suggestion was written

    with pytest.raises(ValueError, match="runs as wssmux now, not as tcpmux"):
        doctor.apply_suggestion(e["id"])

    with session() as db:
        db.get(Tunnel, ids["t2"]).transport = "tcpmux"

    monkeypatch.setattr(doctor.license, "tunnel_kind_allowed", lambda kind: False)

    with pytest.raises(ValueError, match="not allowed here"):
        doctor.apply_suggestion(e["id"])

    monkeypatch.setattr(doctor.license, "tunnel_kind_allowed", lambda kind: True)
    doctor.suggest("type", ids["t2"], "t2", ids["ir1"], "tcpmux", None, "tcpmux", "same", extra={"now_kind": "tcpmux", "to_kind": "tcpmux"})
    doctor.apply_suggestion(next(x["id"] for x in doctor.state()["suggestions"]))
    assert replaced == [] and said[-1][0] == "t2 runs as tcpmux already: nothing to change."                               # (the type it should become is the type it is)

    with pytest.raises(ValueError, match="gone"):
        doctor.apply_suggestion("s-0-0")

    with pytest.raises(ValueError, match="not found"):
        doctor.retype_now(98765, "tcp")


def test_the_apply_button_of_the_api_starts_the_type_change_as_an_action(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    ids = _servers_and_tunnels()
    e = doctor.suggest("type", ids["t2"], "t2", ids["ir1"], "tcpmux", None, "tcp", "why", extra={"now_kind": "tcpmux", "to_kind": "tcp"})
    called = []
    monkeypatch.setattr(doctor, "retype_now", lambda tid, kind, was="", why="": called.append((tid, kind, was, why)) or 55)
    assert client.post(f"/api/doctor/suggestions/{e['id']}/apply").json() == {"ok": True, "id": 55}
    assert called == [(ids["t2"], "tcp", "tcpmux", "why")]


# ------------------------------------------------------------------ the Doctor changes the type of a very slow tunnel by itself (1.69.17)
def test_the_doctor_changes_the_type_by_itself_once_in_a_day_and_never_when_it_is_off():
    now = 1_000_000
    st.put("repair", {"mode": "fix"})
    assert doctor.may_retype("t1", {}, now) == "" and doctor.may_retype("t1", {"t1": now - 3 * 3600}, now) == "t1 was changed by the Doctor 3 hours ago (once in 24 hours)"
    assert doctor.may_retype("t1", {"t1": now - 25 * 3600}, now) == "" and doctor.may_retype("t2", {"t1": now - 3600}, now) == ""            # (another tunnel is not held back)
    st.put("repair", {"mode": "move"})
    assert doctor.may_retype("t1", {}, now) == ""
    st.put("repair", {"mode": "off"})
    assert doctor.may_retype("t1", {}, now) == "the Doctor is off"
    doctor.mark_retyped("t1", int(time.time()))
    assert "t1" in doctor._state()["retyped"]


def test_an_automatic_change_waits_for_a_quiet_moment_keeps_what_it_measured_and_goes_back_when_it_is_worse(monkeypatch):
    from nikatel.models import Event
    from nikatel.services import bandwidth, knowledge

    ids, _e, said, replaced = _retype_world(monkeypatch)
    quiet = [False]
    monkeypatch.setattr(autopilot, "_wait_quiet", lambda aid, tid, limit=1800: quiet[0])
    doctor._run_retype(1, ids["t2"], "tcp", "tcpmux", "why", auto=True)
    assert replaced == [] and "waits for a quiet moment" in said[-1][0]                                                    # busy: nothing is touched, the suggestion stands
    quiet[0] = True
    kept = []
    monkeypatch.setattr(knowledge, "record", lambda i, f, result, measure: kept.append((result, measure)))
    doctor._run_retype(1, ids["t2"], "tcp", "tcpmux", "why", auto=True)
    assert replaced == [(ids["t2"], "tcp", "The Doctor changed it by itself: the tunnel was very slow.")] and kept == [({"kind": "tcp", "ok": True, "works": True, "down_mbps": 118.0}, "full")]
    assert said[-1][0].startswith("t2 runs as tcp now.")

    with session() as db:
        assert any("changed by the Doctor itself (the tunnel was very slow)" in x.text for x in db.scalars(select(Event).where(Event.domain == "tunnels")))

    replaced.clear()
    said.clear()
    monkeypatch.setattr(bandwidth, "measure", lambda tid: ({"down": 0.3}, ""))                                              # the new type is clearly WORSE than the 0.8 it replaced
    doctor.suggest("type", ids["t2"], "t2", ids["ir1"], "tcpmux", None, "tcp", "again", extra={"now_kind": "tcpmux", "to_kind": "tcp"})
    doctor._run_retype(1, ids["t2"], "tcp", "tcpmux", "why", auto=True)
    assert [r[1] for r in replaced] == ["tcp", "tcpmux"] and replaced[1][2] == "It was slower than the old type."
    assert any("gave 0.3 Mb/s, less than tcpmux (0.8): the old type is put back." in s[0] for s in said) and said[-1][0] == "t2 runs as tcpmux again."

    with session() as db:
        assert any("went back to tcpmux" in x.text for x in db.scalars(select(Event).where(Event.domain == "tunnels")))
