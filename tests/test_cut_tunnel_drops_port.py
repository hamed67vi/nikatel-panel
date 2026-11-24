"""A tunnel that is CUT carries nothing on any port. When the foreign server is filtered from most Iran servers, the few that still reach it may all have a port of the tunnel taken by another tunnel - the move used to find
"no Iran server can carry it". Now such a port is left off the move when no probe config dials it (the owner, 2026-11-24, the Canada tunnel)."""
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Config, Server, Tunnel
from nikatel.services import autopilot, builder, detect, failover


def _world(state="down", ports=(8075, 11444), other_ports=(8075,)):
    with session() as db:
        src = Server(name="src", host="203.0.113.11", role="iran", online=True)
        tgt = Server(name="tgt", host="203.0.113.12", role="iran", online=True)
        fo = Server(name="fo", host="203.0.113.50", role="foreign", online=True)
        db.add_all([src, tgt, fo])
        db.flush()
        cut = Tunnel(name="cut", type="backpack", transport="wss", port=9906, iran_server_id=src.id, foreign_server_id=fo.id, state=state, forwarded_ports=list(ports), record={"port_mapping": ",".join(f"{p}:{p + 1}" for p in ports)})
        other = Tunnel(name="other", type="backhaul", transport="tcpmux", port=9907, iran_server_id=tgt.id, foreign_server_id=fo.id, state="up", forwarded_ports=list(other_ports), record={})
        db.add_all([cut, other])
        db.flush()
        return {"src": src.id, "tgt": tgt.id, "fo": fo.id, "cut": cut.id, "other": other.id}


def _row(w, **kw):
    return {"id": w["tgt"], "name": "tgt", "score": 77, "quality": 90, "resources": 80, "tunnels": 1, "reason": "ok", **kw}


def _rank(monkeypatch, w, **kw):
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [_row(w, **kw)])


# ------------------------------------------------------------------ the pure rules
def test_the_port_list_without_some_ports():
    assert builder.without_ports("8075,1840:1820,11444:8680", {8075}) == "1840:1820,11444:8680"
    assert builder.without_ports("8075, 8076-8078", [8077]) == "8075,8076,8078"
    assert builder.without_ports("8075", {8075}) == "" and builder.without_ports("8075", set()) == "8075"


def test_a_clash_is_allowed_only_for_ports_the_move_may_leave_and_never_for_all_of_them():
    f = failover.clash_allowed
    assert f({}, {8075, 11444}, set()) and f({"clash": []}, {8075}, set())                        # (no clash: always)
    assert f({"clash": [8075]}, {8075, 11444}, {8075}) is True
    assert f({"clash": [8075]}, {8075, 11444}, set()) is False                                    # (the move may not leave it: a config dials it, or the tunnel is up)
    assert f({"clash": [8075, 11444]}, {8075, 11444}, {8075, 11444}) is False                    # (nothing would be left of the tunnel)
    assert f({"clash": [8075, 11444]}, {8075, 11444, 1840}, {8075}) is False                     # (one of the two is dialled)


def test_only_a_cut_tunnel_may_leave_a_port_that_no_config_dials():
    tunnels = [{"id": 1, "name": "a", "state": "down", "ports": {8075, 11444}}, {"id": 2, "name": "b", "state": "up", "ports": {8075}}, {"id": 3, "name": "c", "state": "degraded", "ports": {1840}}]
    got = failover.spare_ports(tunnels, {1: {11444}, 2: set(), 3: {1840}})
    assert got == {1: {8075}, 2: set(), 3: set()}


# ------------------------------------------------------------------ the ranking that tolerates a taken port
def _ranking_world(monkeypatch):
    with session() as db:
        for n, h in (("ir1", "203.0.113.21"), ("ir2", "203.0.113.22")):
            db.add(Server(name=n, host=h, role="iran", online=True, facts={"cpu": 10, "ram": 30, "load1": 0.3, "cores": 4}))

        fo = Server(name="fo9", host="203.0.113.59", role="foreign", online=True)
        db.add(fo)
        db.flush()
        ir1 = db.scalar(select(Server).where(Server.name == "ir1"))
        db.add(Tunnel(name="taken", type="backhaul", transport="tcpmux", port=9905, iran_server_id=ir1.id, foreign_server_id=fo.id, state="up", forwarded_ports=[8075], record={}))
        db.flush()
        fid = fo.id

    good = "4 packets transmitted, 4 received, 0% packet loss\nrtt min/avg/max/mdev = 50/60/70/5 ms"
    monkeypatch.setattr(detect, "run_script", lambda node, name, params=None, timeout=30: good)
    return fid


def test_the_strict_ranking_scores_a_taken_port_zero_and_the_tolerant_one_says_which_port(monkeypatch):
    fid = _ranking_world(monkeypatch)
    strict = {r["name"]: r for r in detect.auto_iran(fid, "8075,11444")}
    assert strict["ir1"]["score"] == 0 and "already used" in strict["ir1"]["reason"] and "clash" not in strict["ir1"] and strict["ir2"]["score"] > 0

    with detect.tolerant(True):
        soft = {r["name"]: r for r in detect.auto_iran(fid, "8075,11444")}

    assert soft["ir1"]["score"] > 0 and soft["ir1"]["clash"] == [8075] and "8075 is used here" in soft["ir1"]["reason"]
    assert soft["ir1"]["score"] < soft["ir2"]["score"] and "clash" not in soft["ir2"]              # (a server with every port free still ranks first)
    assert {r["name"]: r["score"] for r in detect.auto_iran(fid, "8075,11444")}["ir1"] == 0         # (and the block ended: strict again)


# ------------------------------------------------------------------ the plan
def test_a_cut_tunnel_moves_without_the_port_that_the_target_has_and_no_config_dials(monkeypatch):
    w = _world()
    _rank(monkeypatch, w, clash=[8075])

    with session() as db:
        db.add(Config(name="cfg", enabled=True, tunnel="cut", meta={"address": "x.example.test", "port": 11444}))

    p = failover._make_plan(w["src"], {w["cut"]}, "route_cut")
    assert p["target"]["id"] == w["tgt"] and p["problems"] == [] and p["drop"] == {w["cut"]: [8075]}
    assert "8075" in p["ports_note"] and "cut" in p["ports_note"] and p["tunnels"] == [{"id": w["cut"], "name": "cut"}]
    snap = {"ports": "8075:8076,11444:11445", "name": "cut"}
    assert failover.plan_snap(p, w["cut"], snap)["ports"] == "11444:11445" and failover.plan_snap({**p, "drop": {str(w["cut"]): [8075]}}, w["cut"], snap)["ports"] == "11444:11445"          # (the plan survives JSON)
    assert failover.plan_snap({}, w["cut"], snap) is snap


def test_a_port_that_a_config_dials_is_never_left_behind(monkeypatch):
    w = _world()
    _rank(monkeypatch, w, clash=[8075])

    with session() as db:
        db.add(Config(name="cfg8075", enabled=True, tunnel="cut", meta={"address": "x.example.test", "port": 8075}))

    p = failover._make_plan(w["src"], {w["cut"]}, "route_cut")
    assert p["target"] is None and "drop" not in p and any("no other Iran server" in x for x in p["problems"])


def test_a_config_that_names_the_iran_server_dials_the_port_too(monkeypatch):
    w = _world()
    _rank(monkeypatch, w, clash=[8075])

    with session() as db:
        db.add(Config(name="byip", enabled=True, tunnel="", meta={"address": "203.0.113.11", "port": 8075}))          # (no label: it dials the Iran server's own address)

    assert failover._make_plan(w["src"], {w["cut"]}, "route_cut")["target"] is None


def test_a_tunnel_that_is_up_keeps_the_strict_rule(monkeypatch):
    w = _world(state="up")
    _rank(monkeypatch, w, clash=[8075])
    p = failover._make_plan(w["src"], {w["cut"]}, "manual")
    assert p["target"] is None and "drop" not in p


def test_a_whole_server_plan_keeps_the_strict_rule(monkeypatch):
    w = _world()
    _rank(monkeypatch, w, clash=[8075])
    p = failover._make_plan(w["src"], None, "down")
    assert p["target"] is None and "drop" not in p


def test_a_tunnel_is_never_moved_with_no_port_at_all(monkeypatch):
    w = _world(ports=(8075,), other_ports=(8075,))
    _rank(monkeypatch, w, clash=[8075])
    assert failover._make_plan(w["src"], {w["cut"]}, "route_cut")["target"] is None


def test_a_server_with_every_port_free_is_a_plain_plan(monkeypatch):
    w = _world(other_ports=(9000,))
    _rank(monkeypatch, w)
    p = failover._make_plan(w["src"], {w["cut"]}, "route_cut")
    assert p["target"]["id"] == w["tgt"] and "drop" not in p and "ports_note" not in p


def test_the_strict_plan_of_the_move_button_does_not_ask_for_the_port_the_cut_tunnel_leaves(monkeypatch):
    w = _world()
    _rank(monkeypatch, w, clash=[8075])
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: [])
    p = failover._make_plan(w["src"], {w["cut"]}, "manual", strict=True)
    assert p["target"] and p["target"]["id"] == w["tgt"] and p["drop"] == {w["cut"]: [8075]}


# ------------------------------------------------------------------ the ranking of the quality move
def test_the_quality_move_sees_a_server_with_a_port_to_leave_only_for_a_cut_tunnel(monkeypatch):
    w = _world()
    seen = []

    def ranking(tid):
        seen.append(detect._TOLERANT.get())
        return [_row(w, clash=[8075])]

    monkeypatch.setattr(autopilot, "ranking", ranking)
    cut_snap = {"row": {"state": "down"}}
    assert [r["id"] for r in failover._cut_ranking(cut_snap, w["cut"])] == [w["tgt"]] and seen == [True]
    seen.clear()
    assert failover._cut_ranking({"row": {"state": "up"}}, w["cut"]) and seen == [False]            # (an up tunnel: the plain ranking, the stubbed row comes back as it is)

    with session() as db:
        db.add(Config(name="cfg8075", enabled=True, tunnel="cut", meta={"address": "x.example.test", "port": 8075}))

    assert failover._cut_ranking(cut_snap, w["cut"]) == []                                           # (a config dials the port: the server is not offered)
