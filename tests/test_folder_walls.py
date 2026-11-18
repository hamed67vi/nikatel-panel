"""A Servers folder is a wall (1.69.286; the owner, 2026-11-18, two servers of a friend's panel in a folder of their own: "keep their folder separate from our panel / nothing of our servers may be tunneled onto them /
the Doctor must watch"): no automatic choice of an Iran server - a new tunnel, a resource move, a quality move, a return, a test tunnel - crosses a folder, and a server of such a folder runs none of the fleet's probes.
While the Iran servers are in one folder and the foreign servers in others (the fleet), nothing changes."""
import time

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Profile, Server, Tunnel
from nikatel.services import autopilot, commander, detect, doctor, failover, folders
from nikatel.services import settings as st
from nikatel.ssh import Node


# ------------------------------------------------------------------ the pure rules
def rows(*items):
    """(kind, server id, tag) as the profiles list them."""
    return list(items)


FLEET = rows(("iran", 1, "Iran"), ("iran", 2, "Iran"), ("foreign", 10, "PasarGuard"), ("foreign", 11, "main"), ("foreign", 12, ""))
WITH_FRIEND = FLEET + rows(("iran", 20, "friend"), ("foreign", 21, "friend"))


def test_while_the_iran_servers_share_one_folder_every_pair_is_allowed_and_nothing_is_private():
    f = folders.build(FLEET)
    assert f.private == frozenset()
    assert all(f.compatible(i, x) for i in (1, 2) for x in (10, 11, 12)) and f.same_pool(1, 2) and not f.same_pool(1, 12)
    assert all(f.may_probe(i, t) for i in (1, 2) for t in (None, 10, 2)) and folders.build([]).private == frozenset()


def test_a_folder_that_holds_an_iran_server_and_a_foreign_server_is_private_and_walls_its_servers():
    f = folders.build(WITH_FRIEND)
    assert f.private == frozenset({"friend"}) and f.is_private(20) and f.is_private(21) and not f.is_private(1)
    assert f.compatible(20, 21) and f.compatible(21, 20)                                       # (inside the folder: yes)
    assert not f.compatible(20, 10) and not f.compatible(1, 21) and not f.compatible(2, 21)    # (the fleet never meets them in a tunnel the panel chooses)
    assert f.compatible(1, 10) and f.compatible(2, 12)                                         # (the fleet among itself: as before)
    assert not f.same_pool(1, 20) and f.same_pool(20, 20)


def test_a_private_iran_server_probes_only_its_own_folder_and_the_fleet_may_still_look_at_it():
    f = folders.build(WITH_FRIEND)
    assert not f.may_probe(20) and not f.may_probe(20, 10) and not f.may_probe(20, 1) and f.may_probe(20, 21)      # (it runs none of our probes)
    assert f.may_probe(1, 20) and f.may_probe(1, 21) and f.may_probe(2)                                           # (the Doctor watches it: the fleet looks at it)


def test_a_folder_is_one_folder_whatever_its_case_and_the_empty_tag_is_never_private():
    f = folders.build(rows(("iran", 1, "Friend"), ("foreign", 2, " friend "), ("iran", 3, ""), ("foreign", 4, "")))
    assert f.private == frozenset({"friend"}) and f.compatible(1, 2) and not f.is_private(3) and not f.is_private(4)
    assert folders.build(rows(("node", 9, "friend"), ("iran", None, "friend"))).tag == {}


# ------------------------------------------------------------------ the world: a fleet and a friend's pair
def _world(with_profiles=True):
    """fleet: ir-a, ir-b (Iran) + fx (foreign); friend: ir-f (Iran) + fx-f (foreign) in the folder "friend"."""
    with session() as db:
        ira, irb = Server(name="ir-a", host="192.0.2.11", role="iran", online=True), Server(name="ir-b", host="192.0.2.12", role="iran", online=True)
        irf, fx, fxf = Server(name="ir-f", host="192.0.2.21", role="iran", online=True), Server(name="fx", host="192.0.2.31", role="foreign", online=True), Server(name="fx-f", host="192.0.2.41", role="foreign", online=True)
        db.add_all([ira, irb, irf, fx, fxf])
        db.flush()
        ids = {"a": ira.id, "b": irb.id, "f": irf.id, "fx": fx.id, "fxf": fxf.id}

        if with_profiles:
            db.add_all([Profile(kind="iran", server_id=ids["a"], tag="Iran"), Profile(kind="iran", server_id=ids["b"], tag="Iran"), Profile(kind="iran", server_id=ids["f"], tag="friend"),
                        Profile(kind="foreign", server_id=ids["fx"], tag="PasarGuard"), Profile(kind="foreign", server_id=ids["fxf"], tag="friend")])

        t = Tunnel(name="t-fleet", type="backhaul", transport="tcpmux", iran_server_id=ids["a"], foreign_server_id=ids["fx"], state="up", forwarded_ports=[80])
        db.add(t)
        db.flush()
        ids["t"] = t.id

    return ids


def row(sid, name, score=80, resources=80, quality=85):
    return {"id": sid, "name": name, "score": score, "resources": resources, "quality": quality, "reason": "40 ms"}


def test_the_folder_of_a_server_and_the_rows_of_a_ranking_in_its_pool():
    ids = _world()
    assert folders.pool_of(ids["f"]) == "friend" and autopilot.pool_of(ids["a"]) == "Iran" and folders.pool_of(999999) == ""
    assert [r["id"] for r in folders.in_pool([row(ids["b"], "ir-b"), row(ids["f"], "ir-f")], ids["a"])] == [ids["b"]]
    assert folders.compatible(ids["f"], ids["fxf"]) and not folders.compatible(ids["f"], ids["fx"]) and not folders.compatible(ids["a"], ids["fxf"])


# ------------------------------------------------------------------ a new tunnel: the automatic choice of the Iran server
def _pings(monkeypatch):
    good = "4 packets transmitted, 4 received, 0% packet loss\nrtt min/avg/max/mdev = 50/60/70/5 ms"
    monkeypatch.setattr(detect, "run_script", lambda node, name, params=None, timeout=30: good)


def test_the_automatic_choice_for_a_fleet_foreign_server_never_lists_a_server_of_the_friends_folder(monkeypatch):
    ids = _world()
    _pings(monkeypatch)
    assert sorted(r["name"] for r in detect.auto_iran(ids["fx"], "8443")) == ["ir-a", "ir-b"]
    assert [r["name"] for r in detect.auto_iran(ids["fxf"], "8443")] == ["ir-f"]                  # (and the friend's foreign server is offered only the friend's Iran server)


def test_without_folders_the_automatic_choice_lists_every_iran_server_as_before(monkeypatch):
    ids = _world(with_profiles=False)
    _pings(monkeypatch)
    assert sorted(r["name"] for r in detect.auto_iran(ids["fx"], "8443")) == ["ir-a", "ir-b", "ir-f"]


def test_the_ranking_api_and_the_smart_test_of_create_use_the_same_walls(client, monkeypatch):
    ids = _world()
    _pings(monkeypatch)
    r = client.post("/api/tunnels/auto-iran", json={"foreign_id": ids["fx"], "ports": "443"})
    assert r.status_code == 200 and sorted(x["name"] for x in r.json()["ranking"]) == ["ir-a", "ir-b"]
    r = client.post("/api/tunnels/auto-iran", json={"foreign_id": ids["fxf"], "ports": "443"})
    assert [x["name"] for x in r.json()["ranking"]] == ["ir-f"]


# ------------------------------------------------------------------ the Doctor: a resource move stays in the tunnel's folder
def _saturated(monkeypatch, ids, ranking):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(doctor, "saturated_servers", lambda now=None: [{"id": ids["a"], "name": "ir-a", "why": "CPU at or above 90 % for 15 minutes"}])
    monkeypatch.setattr(detect, "auto_iran", lambda foreign_id, ports="": ranking)
    moved = []
    monkeypatch.setattr(doctor, "move_now", lambda tid, iran_id, reason="manual", why="": moved.append((tid, iran_id, reason)) or 1)
    monkeypatch.setattr(doctor, "can_move", lambda: True)
    st.put("repair", {"mode": "move"})
    st.put("doctor", {"res_at": {}, "suggestions": [], "moves": []})
    return moved


def test_an_exhausted_fleet_server_gives_its_busiest_tunnel_to_the_fleet_never_to_the_friends_server(monkeypatch):
    ids = _world()
    moved = _saturated(monkeypatch, ids, [row(ids["f"], "ir-f", score=99, resources=99, quality=99), row(ids["b"], "ir-b")])           # (the friend's server ranks FIRST: it is idle)
    doctor.resources()
    assert moved == [(ids["t"], ids["b"], "resources")]


def test_when_only_the_friends_server_has_room_the_resource_move_is_a_suggestion_with_no_target_and_nothing_moves(monkeypatch):
    ids = _world()
    moved = _saturated(monkeypatch, ids, [row(ids["f"], "ir-f", score=99, resources=99, quality=99)])
    doctor.resources()
    sug = doctor.state()["suggestions"]
    assert moved == [] and len(sug) == 1 and sug[0]["kind"] == "resources" and not sug[0].get("to_id") and "no other Iran server has room" in sug[0]["why"]


# ------------------------------------------------------------------ the Doctor: the temporary test tunnel of a cut tunnel
def test_the_test_of_a_cut_tunnel_from_another_iran_server_is_built_only_by_a_server_of_its_own_folder(monkeypatch):
    ids = _world()
    said, built = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(detect, "auto_iran", lambda foreign_id, ports="": [row(ids["f"], "ir-f", score=99), row(ids["b"], "ir-b", score=70)])
    monkeypatch.setattr(autopilot.tunnel_test, "compare_start", lambda iran_id, foreign_id, kinds, measure="latency", **k: built.append(iran_id) or "job")
    monkeypatch.setattr(autopilot.tunnel_test, "compare_status", lambda jid: {"done": True, "results": [{"score": 80, "latency_ms": 40, "loss_pct": 0}]})
    monkeypatch.setattr(time, "sleep", lambda s: None)
    autopilot.probe_other_iran(1, {"foreign_id": ids["fx"], "ports": "80", "iran_id": ids["a"]}, "tcpmux")
    assert built == [ids["b"]]

    said.clear(), built.clear()
    monkeypatch.setattr(detect, "auto_iran", lambda foreign_id, ports="": [row(ids["f"], "ir-f", score=99)])             # (only the friend's server is left: no test at all)
    autopilot.probe_other_iran(1, {"foreign_id": ids["fx"], "ports": "80", "iran_id": ids["a"]}, "tcpmux")
    assert built == [] and any("No other Iran server is free" in x for x in said)


def test_the_ranking_of_a_tunnel_still_leaves_out_another_folder(monkeypatch):
    ids = _world()
    monkeypatch.setattr(autopilot.detect, "auto_iran", lambda foreign_id, ports: [row(ids["f"], "ir-f"), row(ids["b"], "ir-b"), row(ids["a"], "ir-a")])
    assert [r["id"] for r in autopilot.ranking(ids["t"])] == [ids["b"]]


# ------------------------------------------------------------------ every move of one tunnel: the door of move_now and the plan
def test_a_move_of_one_tunnel_to_a_server_of_another_folder_is_refused_for_every_reason(monkeypatch):
    ids = _world()
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda *a, **k: started.append(a) or 1)

    for reason in ("resources", "quality", "capacity", "return", "manual"):
        with pytest.raises(ValueError, match="another Servers folder"):
            doctor.move_now(ids["t"], ids["f"], reason, "why", rails=False)

    assert started == []
    assert doctor.move_now(ids["t"], ids["b"], "manual", "why") == 1 and len(started) == 1              # (a server of the same folder: as before)


def test_a_plan_the_panel_makes_with_a_target_of_another_folder_is_refused_but_the_strict_plan_judges_by_the_ranking(monkeypatch):
    ids = _world()
    monkeypatch.setattr(failover, "_fetch_records", lambda ip: ([], ""))

    with pytest.raises(ValueError, match="another Servers folder"):
        failover._make_plan(ids["a"], {ids["t"]}, "quality", target_id=ids["f"])

    monkeypatch.setattr(autopilot.detect, "auto_iran", lambda foreign_id, ports: [row(ids["f"], "ir-f"), row(ids["b"], "ir-b")])
    plan = failover._make_plan(ids["a"], {ids["t"]}, "quality", target_id=ids["b"])
    assert plan["target"]["id"] == ids["b"]


def test_the_ai_commander_cannot_order_a_move_to_another_folder():
    ids = _world()

    with pytest.raises(commander.Refusal, match="another Servers folder"):
        commander.target_of("move", {"id": ids["t"], "iran_id": ids["f"]})

    assert commander.target_of("move", {"id": ids["t"], "iran_id": ids["b"]})["to"] == "ir-b"


# ------------------------------------------------------------------ the second foreign server of a relay strand
def test_a_relay_strand_never_goes_through_the_friends_foreign_server():
    from nikatel.services import trident

    ids = _world()
    _tunnel("t-odd", ids["a"], ids["fxf"])                                               # (a tunnel that is up from this fleet server to the friend's foreign server: the surest sign of a reachable one - and still not a choice)
    assert trident.relay_target(ids["a"], ids["fx"], int(time.time()))[0] == 0
    ids["extra"] = _add_server("fx2", "192.0.2.32", "foreign", "PasarGuard")
    _tunnel("t-ok", ids["a"], ids["extra"])
    assert trident.relay_target(ids["a"], ids["fx"], int(time.time()))[0] == ids["extra"]


def _tunnel(name, iran_id, foreign_id):
    with session() as db:
        db.add(Tunnel(name=name, type="backhaul", transport="tcpmux", iran_server_id=iran_id, foreign_server_id=foreign_id, state="up", forwarded_ports=[8000 + len(name)]))


def _add_server(name, host, role, tag):
    with session() as db:
        s = Server(name=name, host=host, role=role, online=True)
        db.add(s)
        db.flush()
        db.add(Profile(kind=role, server_id=s.id, tag=tag))
        return s.id


# ------------------------------------------------------------------ the probes: a server of the friend's folder runs none of ours
def test_the_friends_iran_server_is_not_a_vantage_of_the_fleet_and_the_fleet_may_ask_about_it():
    from nikatel.services import tunnels, vantage

    ids = _world()
    assert {n for n, _ in vantage._vantages(set())} == {"ir-a", "ir-b"} and {n for n, _ in vantage._vantages(set(), ids["a"])} == {"ir-a", "ir-b"}
    assert {n for n, _ in vantage._vantages({ids["f"]}, ids["f"])} == {"ir-a", "ir-b"}              # (about the friend's server: the fleet looks)
    assert {n for _, n, _ in tunnels._vantages()} == {"ir-a", "ir-b"}


def test_an_ssh_session_of_ours_is_not_carried_through_the_friends_server(monkeypatch):
    from nikatel.services import nodes

    ids = _world()
    assert {n.host for n in nodes.jump_candidates(Node("192.0.2.99", "root", 22))} == {"192.0.2.11", "192.0.2.12"}
    assert {n.host for n in nodes.jump_candidates(Node("192.0.2.41", "root", 22))} == {"192.0.2.11", "192.0.2.12", "192.0.2.21"}      # (to the friend's own foreign server it may: it is the folder's)
    assert ids


def test_the_filtered_in_iran_check_and_the_tcp_reach_do_not_run_from_the_friends_server(monkeypatch):
    from nikatel.services import nodes

    _world()
    asked = []
    monkeypatch.setattr(nodes, "run_script", lambda node, name, params=None, timeout=30: asked.append(node.host) or "4 packets transmitted, 4 received, 0% packet loss\nrtt min/avg/max/mdev = 50/60/70/5 ms")
    rows_ = nodes._own_rows("192.0.2.77")
    assert sorted(r["node"] for r in rows_) == ["ir-a", "ir-b"] and "192.0.2.21" not in asked
    asked.clear()
    assert nodes._tcp_rows("192.0.2.77", 443)[1] == 2 and "192.0.2.21" not in asked


def test_the_site_check_and_the_sni_check_use_a_server_of_the_fleet(monkeypatch):
    from nikatel.services import dcs

    ids = _world()
    with session() as db:                                                                  # (the friend's server has the lowest id and the lowest name: it would have been the first)
        db.get(Server, ids["f"]).name = "aaa-first"

    seen = []
    monkeypatch.setattr(dcs, "run_script", lambda node, name, params=None, timeout=30: seen.append(node.host) or "REACH x.test:443 1 12\n")
    dcs.check_from_iran(["x.test"])
    assert seen and "192.0.2.21" not in seen
    monkeypatch.setattr(autopilot, "run_script", lambda node, name, params=None, timeout=30: seen.append(node.host) or "own=1 control=0")
    seen.clear()
    autopilot._sni_check({"address": "x.test", "port": 443})
    assert seen == ["192.0.2.11"]


# ------------------------------------------------------------------ the foreign servers the panel picks for itself
def test_the_relay_the_panel_suggests_and_the_bale_watch_never_use_a_foreign_server_of_the_friends_folder():
    from nikatel.services import bale, outbound

    ids = _world()
    with session() as db:
        db.get(Server, ids["fx"]).enabled = False                                          # (the friend's foreign server is the only one left, and it is idle: it would be "the quietest")

    assert outbound.suggest_relay() is None
    assert [t[0] for t in bale.watch_targets() if t[0].startswith("192.0.2.")] == []
    ids2 = _add_server("fx3", "192.0.2.33", "foreign", "PasarGuard")
    assert outbound.suggest_relay()["id"] == ids2 and [t[0] for t in bale.watch_targets() if t[0].startswith("192.0.2.")] == ["192.0.2.33"]


def test_the_config_reach_is_asked_from_servers_of_the_fleet_only(monkeypatch):
    from nikatel.models import Config
    from nikatel.services import configs

    ids = _world()

    with session() as db:
        db.get(Server, ids["a"]).enabled = False                                           # (the first two enabled Iran servers by id are now ir-b and the friend's ir-f: only ir-b may ask)
        db.add(Config(name="c1", status="down", meta={"network": "tcp", "address": "x.test", "port": 443}))

    asked = []
    monkeypatch.setattr(configs, "run_script", lambda node, name, params=None, timeout=30: asked.append(node.host) or "REACH x.test:443 1 12\n")
    assert configs.refresh_reach(["c1"]) is True and asked == ["192.0.2.12"]


def test_the_ping_round_sends_the_friends_iran_server_only_toward_its_own_folder(monkeypatch):
    from nikatel.services import netwatch

    ids = _world()
    jobs = []
    monkeypatch.setattr(netwatch, "probe_one", lambda job: jobs.append(job) or {"error": "stub"})
    netwatch.probe_all()
    by = {j[1]: j[3] for j in jobs}
    assert set(by) == {"ir-a", "ir-b", "ir-f"} and ids
    assert by["ir-f"]["domestic"] == [] and by["ir-f"]["foreign"] == ["192.0.2.41"]            # (its own folder's foreign server only, no fleet server)
    assert "192.0.2.41" not in by["ir-f"]["domestic"] and by["ir-a"]["foreign"] and "192.0.2.31" in by["ir-a"]["foreign"]


def test_the_loss_locator_of_a_fleet_tunnel_asks_no_probe_from_the_friends_server(monkeypatch):
    from nikatel.services import pathscan

    ids = _world()
    probers = []
    monkeypatch.setattr(pathscan, "run_script", lambda node, name, params=None, timeout=30: probers.append(node.host) or "")
    pathscan.scan(ids["t"])
    assert probers and "192.0.2.21" not in probers and "192.0.2.11" in probers
