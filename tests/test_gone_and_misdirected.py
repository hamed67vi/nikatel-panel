"""Two configs were down for five hours because their DNS names stayed on a dead Iran server after a failover had rebuilt their tunnels on another one (the owner, 2026-11-07: "why did the Doctor not change their IPs? fix these").

1. The failover of a dead server counted a tunnel that ANOTHER move had already taken away as a failure ("tunnel not found"), so "2 of 3 moved" and it changed no DNS record at all. Now a tunnel that is gone is done, and when a dead
   server's tunnels moved only in part the records that serve nothing but the ones that moved follow them. A record that no longer points at the old server is never overwritten.
2. The DNS heal starts from a tunnel that has a config down; a config whose name points at an Iran server that has lost its tunnel rides NO tunnel, so nobody looked at it. Now `dnsheal.misdirected` finds it."""
import time

import pytest
from sqlalchemy import select
from test_failover import FakeProv, _dns_world, _servers_and_tunnels

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Config, Server, Tunnel
from nikatel.services import autopilot, dnscache, dnsheal, doctor, failover

OLD, NEW = "203.0.113.5", "203.0.113.9"


# ------------------------------------------------------------------ the failover
def _dead_world(monkeypatch):
    """ir1 (t1: a, t2: b, t3: a) is off; the records a, b, c carry its address; ir2 is the target. Returns (ids, the story lines)."""
    ids = _servers_and_tunnels()

    with session() as db:
        db.get(Server, ids["ir1"]).online = False

    _dns_world(monkeypatch)
    monkeypatch.setattr(get_settings(), "read_only", False)
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    return ids, said


def _plan(ids):
    p = failover._make_plan(ids["ir1"], None, "down", target_id=ids["ir2"])
    p["id"] = "gone-1"
    return p


def _updated():
    return sorted(c[3] for c in FakeProv.calls if c[0] == "update")


def test_the_plan_tells_which_tunnels_each_record_serves(monkeypatch):
    ids, _ = _dead_world(monkeypatch)
    p = _plan(ids)
    assert {d["name"]: sorted(d["serves"]) for d in p["dns"]} == {"a": sorted([ids["t1"], ids["t3"]]), "b": [ids["t2"]], "c": []}


def test_a_tunnel_that_another_move_took_away_is_done_not_failed_and_the_dns_follows(monkeypatch):
    ids, said = _dead_world(monkeypatch)
    p = _plan(ids)
    built = []
    monkeypatch.setattr(autopilot, "_replace", lambda aid, tunnel_id, *a, **k: built.append(tunnel_id) or True)

    with session() as db:                                                           # (a second story moved t1 between the plan and its turn)
        db.delete(db.get(Tunnel, ids["t1"]))

    assert failover._work(7, p) is True
    assert sorted(built) == sorted([ids["t2"], ids["t3"]])                          # (the vanished one is not built again)
    assert any("t1: it is not on ir1 any more" in x for x in said)
    assert _updated() == ["r-a", "r-b", "r-c"]                                      # (a whole dead server: every record follows)
    h = failover.state()["history"][0]
    assert h["moved"] == 2 and h["gone"] == 1 and h["tunnels"] == 3 and h["ok"] is True


def test_a_tunnel_that_vanishes_in_the_middle_is_gone_too(monkeypatch):
    ids, _ = _dead_world(monkeypatch)
    p = _plan(ids)

    def replace(aid, tunnel_id, *a, **k):
        if tunnel_id == ids["t2"]:
            raise ValueError("tunnel not found")

        return True

    monkeypatch.setattr(autopilot, "_replace", replace)
    assert failover._work(7, p) is True
    assert failover.state()["history"][0]["gone"] == 1


class Seeing(FakeProv):
    """A provider that can also be asked what a record points at now."""
    now: dict = {}       # noqa: RUF012  record id -> addresses

    def records(self, domain):
        return [{"id": rid, "ips": list(ips)} for rid, ips in Seeing.now.items()]


def test_a_record_that_another_move_already_pointed_elsewhere_is_left_alone(monkeypatch):
    ids, said = _dead_world(monkeypatch)
    monkeypatch.setattr(failover.dnsprov, "provider", lambda acc: Seeing(acc))
    Seeing.now = {"r-a": [OLD], "r-b": ["203.0.113.77"], "r-c": [OLD]}              # (b was moved by the other story)
    p = _plan(ids)
    monkeypatch.setattr(autopilot, "_replace", lambda *a, **k: True)
    assert failover._work(7, p) is True
    assert _updated() == ["r-a", "r-c"] and any("b.example.test: it points at 203.0.113.77 now" in x for x in said)


def test_when_a_dead_servers_tunnels_moved_only_in_part_the_records_that_serve_only_those_follow(monkeypatch):
    ids, said = _dead_world(monkeypatch)
    p = _plan(ids)

    def replace(aid, tunnel_id, *a, **k):
        if tunnel_id == ids["t2"]:
            raise OSError("disk full")

        return True

    monkeypatch.setattr(autopilot, "_replace", replace)
    assert failover._work(7, p) is False
    assert _updated() == ["r-a"]                                                    # a serves t1 and t3 (both moved); b serves t2 (failed); c serves nothing we know of
    assert any("only the 1 DNS record(s)" in x for x in said)
    assert any("b.example.test" in x and "also serves a tunnel that stays" in x for x in said) and any("c.example.test" in x and "nothing tells which tunnel" in x for x in said)
    h = failover.state()["history"][0]
    assert h["moved"] == 2 and h["gone"] == 0 and h["ok"] is False and [d["fqdn"] for d in h["dns"]] == ["a.example.test"]


def test_follows_is_pure_and_keeps_a_record_with_no_known_tunnel():
    dns = [{"fqdn": "x", "serves": [1, 2]}, {"fqdn": "y", "serves": [3]}, {"fqdn": "z", "serves": []}, {"fqdn": "old-plan"}]
    follow, stay = failover._follows(dns, {1, 2})
    assert [d["fqdn"] for d in follow] == ["x"] and [d["fqdn"] for d in stay] == ["y", "z", "old-plan"]


# ------------------------------------------------------------------ the DNS heal and a config that rides no tunnel
def _rec(name, ips, domain="example.test", cloud=False, port=None):
    return {"account": "cf1", "domain": domain, "id": "r-" + name, "type": "A", "name": name, "fqdn": f"{name}.{domain}", "ips": ips, "ttl": 120, "cloud": cloud, "port": port}


def _cfg(name="Cfg", address="x.example.test", port=11422, down_for=3600):
    return {"name": name, "address": address, "port": port, "down_for": down_for}


def _tun(tid, iran_id, ports=(11422,), state="up"):
    return {"id": tid, "iran_id": iran_id, "ports": set(ports), "state": state}


SERVERS = [{"id": 1, "host": OLD}, {"id": 2, "host": NEW}]


def test_a_down_config_whose_name_points_at_a_server_that_lost_its_tunnel_is_found():
    got = dnsheal.misdirected([_cfg()], [_tun(10, 2)], SERVERS, [_rec("x", [OLD])])
    assert got == {10: ["Cfg"]}


@pytest.mark.parametrize("why,configs,tunnels,records", [
    ("down for a short while", [_cfg(down_for=60)], [_tun(10, 2)], [_rec("x", [OLD])]),
    ("the name points at no Iran server we know", [_cfg()], [_tun(10, 2)], [_rec("x", ["198.51.100.7"])]),
    ("the name points at a server that forwards the port", [_cfg()], [_tun(10, 2), _tun(11, 1)], [_rec("x", [OLD])]),
    ("two tunnels forward the port: nothing proves which one", [_cfg()], [_tun(10, 2), _tun(12, 2)], [_rec("x", [OLD])]),
    ("the only tunnel that forwards it is down", [_cfg()], [_tun(10, 2, state="down")], [_rec("x", [OLD])]),
    ("the address is a number", [_cfg(address="203.0.113.5")], [_tun(10, 2)], [_rec("x", [OLD])]),
    ("no tunnel forwards the port at all", [_cfg(port=9999)], [_tun(10, 2)], [_rec("x", [OLD])]),
    ("no record carries the name", [_cfg()], [_tun(10, 2)], []),
])
def test_what_is_not_a_misdirected_name_is_left_out(why, configs, tunnels, records):
    assert dnsheal.misdirected(configs, tunnels, SERVERS, records) == {}, why


@pytest.fixture
def _heal_world(monkeypatch):
    dnsheal._LOOKED.clear()
    monkeypatch.setattr(doctor, "mode", lambda: "move")
    monkeypatch.setattr(doctor, "can_move", lambda: True)
    monkeypatch.setattr(dnsheal, "_dialled", dict)
    monkeypatch.setitem(dnscache.CACHE, "at", time.time())
    monkeypatch.setitem(dnscache.CACHE, "rows", [_rec("tr2ss", [OLD])])
    s = doctor._state()
    s["dns_heal"] = {}
    doctor._save(s)
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: started.append((label, fn, a, kw)) or 5)
    return started


def _world(down_since=None, second_tunnel=False):
    """ir1 (OLD) lost its tunnel; ir2 (NEW) carries `tr3` on 11422; the config Turkey-2 asks tr2ss.example.test:11422, which still points at ir1."""
    with session() as db:
        ir1, ir2, fi1 = Server(name="ir1", host=OLD, role="iran", online=True), Server(name="ir2", host=NEW, role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir1, ir2, fi1])
        db.flush()
        t = Tunnel(name="tr3", type="backpack", transport="tcpmux", iran_server_id=ir2.id, foreign_server_id=fi1.id, state="up", state_since=int(time.time()) - 3600, forwarded_ports=[11422], detail={"listen": {"11422": True}})
        db.add_all([t, Config(name="Turkey-2", enabled=True, status="down", status_since=down_since or int(time.time()) - 3600, meta={"address": "tr2ss.example.test", "port": 11422})])

        if second_tunnel:
            db.add(Tunnel(name="tr4", type="backpack", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi1.id, state="up", state_since=int(time.time()) - 3600, forwarded_ports=[11422, 8075]))

        db.flush()
        return t.id, ir2.id


def test_the_heal_repairs_the_name_of_a_config_that_rides_no_tunnel(_heal_world):
    tid, sid = _world()
    assert dnsheal._orphans_now() == {tid: ["Turkey-2"]}
    dnsheal.look({})                                                                # (no tunnel has a graded config: the old look would do nothing)
    assert len(_heal_world) == 1 and _heal_world[0][0] == "tr3" and _heal_world[0][1] is dnsheal._fix
    assert [(f["fqdn"], f["new"], f["server_id"]) for f in _heal_world[0][2][1]] == [("tr2ss.example.test", NEW, sid)]
    assert "tr2ss.example.test" in doctor._state()["dns_heal"]
    dnsheal.look({})                                                                # (the same name again: once in six hours)
    assert len(_heal_world) == 1


def test_a_config_that_has_been_down_only_a_few_minutes_is_not_repaired(_heal_world):
    _world(down_since=int(time.time()) - 120)
    dnsheal.look({})
    assert _heal_world == []


def test_a_name_that_points_at_a_server_that_still_forwards_the_port_is_not_this_looks_business(_heal_world):
    _world(second_tunnel=True)                                                      # (ir1 has a tunnel on the port too: the config rides it, the fault is elsewhere)
    assert dnsheal._orphans_now() == {}
    dnsheal.look({})
    assert _heal_world == []


def test_the_graded_config_and_the_orphan_of_one_tunnel_are_looked_at_together(_heal_world):
    tid, _ = _world()
    ux = {"grade": "bad", "bad": [{"config": "Turkey-2", "issue": "down", "text": "does not connect"}]}
    dnsheal.look({tid: ux})
    assert len(_heal_world) == 1                                                    # (one story, not two)


def test_a_failing_orphan_look_does_not_stop_the_graded_ones(_heal_world, monkeypatch):
    tid, _ = _world()
    monkeypatch.setattr(dnsheal, "_orphans_now", lambda now=None: (_ for _ in ()).throw(RuntimeError("boom")))
    ux = {"grade": "bad", "bad": [{"config": "Turkey-2", "issue": "down", "text": "does not connect"}]}
    dnsheal.look({tid: ux})
    assert len(_heal_world) == 1


# ------------------------------------------------------------------ the name follows the tunnel to its new Iran server
# (the owner, 2026-11-07, a list of tunnels the Doctor had moved to another Iran node that still carried the old node's name: "when a tunnel changes, its name must change too")
IRAN = ["ir1", "ir2"]


@pytest.mark.parametrize("name,custom,foreign,new_iran,taken,want", [
    ("fi1-ir1", False, "fi1", "ir2", {"fi1-ir1"}, "fi1-ir2"),                       # made by the panel for the old server: follows
    ("fi1-ir1-2", False, "fi1", "ir2", {"fi1-ir1-2"}, "fi1-ir2"),                   # ... with the -2 of a second tunnel on the pair
    ("fi1-ir1", False, "fi1", "ir2", {"fi1-ir1", "fi1-ir2"}, "fi1-ir2-2"),          # the new name is taken: -2
    ("fi1-ir2", False, "fi1", "ir2", {"fi1-ir2"}, "fi1-ir2"),                       # already right
    ("fi1-ir1", True, "fi1", "ir2", {"fi1-ir1"}, "fi1-ir1"),                        # chosen by hand: stays
    ("my-own-name", False, "fi1", "ir2", {"my-own-name"}, "my-own-name"),           # not a name the panel makes: stays
    ("fi1-gone", False, "fi1", "ir2", {"fi1-gone"}, "fi1-gone"),                    # (the name of a server that is not in the list: not recognised, stays)
    ("fi1-ir2-3", False, "fi1", "ir2", {"fi1-ir2-3"}, "fi1-ir2-3"),                 # (a -3 of its own server is not "tidied" to -2)
    ("se-ir1", False, "fi1", "ir2", {"se-ir1"}, "se-ir1"),                          # (the foreign part is another server's: not this tunnel's)
])
def test_the_name_a_tunnel_gets_when_it_moves(name, custom, foreign, new_iran, taken, want):
    from nikatel.services import builder

    assert builder.moved_name(name, custom, foreign, IRAN, new_iran, taken) == want


def _named_world():
    """ir1 and ir2 (Iran), fi1 (foreign); `fi1-ir1` runs on ir2 now (it was moved), a config names it, the ledger knows it."""
    with session() as db:
        ir1, ir2, fi1 = Server(name="ir1", host=OLD, role="iran", online=True), Server(name="ir2", host=NEW, role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir1, ir2, fi1])
        db.flush()
        t = Tunnel(name="fi1-ir1", type="backhaul", transport="tcpmux", iran_server_id=ir2.id, foreign_server_id=fi1.id, state="up", forwarded_ports=[8075], record={"slug": "fi1-ir1"})
        db.add_all([t, Config(name="cfg", enabled=True, tunnel="fi1-ir1", meta={})])
        db.flush()
        return {"ir1": ir1.id, "ir2": ir2.id, "fi1": fi1.id, "t": t.id}


def test_a_moved_tunnel_takes_the_name_of_its_new_server_and_everything_that_names_it_follows():
    from nikatel.services import builder

    ids = _named_world()
    doctor.record_move({"id": "m1", "server_id": ids["ir1"], "server": "ir1", "ip": OLD, "tunnels": [{"id": ids["t"], "name": "fi1-ir1"}], "target": {"id": ids["ir2"], "name": "ir2", "ip": NEW}, "dns": []}, ["fi1-ir1"], [], True)
    assert builder.follow_move("fi1-ir1") == "fi1-ir2"

    with session() as db:
        t = db.get(Tunnel, ids["t"])
        assert t.name == "fi1-ir2" and t.record["slug"] == "fi1-ir1" and not t.record.get("custom_name")       # (what the servers know it as does not change; it is not a name chosen by hand)
        assert db.scalar(select(Config.tunnel).where(Config.name == "cfg")) == "fi1-ir2"

    assert doctor.state()["moves"][0]["tunnels"] == ["fi1-ir2"]                                                # (a return finds the tunnel by the name the ledger holds)
    assert builder.follow_move("fi1-ir2") == "fi1-ir2"                                                          # (nothing more to do)


def test_a_name_chosen_by_hand_stays_even_when_the_rebuilt_row_lost_the_mark():
    from nikatel.services import builder

    _named_world()
    assert builder.follow_move("fi1-ir1", custom=True) == "fi1-ir1"
    assert builder.follow_move("no-such-tunnel") == "no-such-tunnel"


def test_a_rename_by_hand_updates_the_ledger_and_marks_the_name_as_chosen():
    from nikatel.services import builder

    ids = _named_world()
    doctor.record_move({"id": "m1", "server_id": ids["ir1"], "server": "ir1", "ip": OLD, "tunnels": [], "target": {"id": ids["ir2"], "name": "ir2", "ip": NEW}, "dns": []}, ["fi1-ir1"], [], True)
    assert builder.rename_tunnel(ids["t"], "mine") == "mine"
    assert doctor.state()["moves"][0]["tunnels"] == ["mine"]

    with session() as db:
        assert db.get(Tunnel, ids["t"]).record["custom_name"] is True


def test_the_tidy_round_renames_only_tunnels_that_carry_the_name_of_another_server():
    from nikatel.services import builder

    ids = _named_world()

    with session() as db:
        def add(name, ports, enabled=True, **rec):
            db.add(Tunnel(name=name, type="backhaul", transport="tcpmux", iran_server_id=ids["ir2"], foreign_server_id=ids["fi1"], state="up", forwarded_ports=ports, record=rec, enabled=enabled))

        add("fi1-ir2-3", [8076])                                          # (right already: -N of its own server)
        add("fi1-ir1-4", [8077], custom_name=True)                         # (chosen by hand)
        add("fi1-ir1-5", [8078], protected=True)                          # (something stands on it)
        add("fi1-ir1-6", [8079], standby_of="x")                          # (a standby)
        add("fi1-ir1-7", [8080], enabled=False)                           # (switched off: the standby job's)
        add("test-ir1", [8081])                                           # (a temporary test tunnel)

    assert builder.tidy_names() == ["fi1-ir1 → fi1-ir2"]
    assert builder.tidy_names() == []


def test_the_tidy_round_is_limited_and_waits_while_a_failover_runs(monkeypatch):
    from nikatel.services import builder

    ids = _named_world()                                                   # (fi1-ir1 runs on ir2)

    with session() as db:
        for i in range(2, 5):
            db.add(Tunnel(name=f"fi1-ir1-{i}", type="backhaul", transport="tcpmux", iran_server_id=ids["ir2"], foreign_server_id=ids["fi1"], state="up", forwarded_ports=[8100 + i]))

    assert len(builder.tidy_names(limit=2)) == 2                           # (four to put right: two in a round)
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_TIDY_AT", [0.0])
    monkeypatch.setattr(autopilot, "quiet_now", lambda: True)
    assert failover.tidy_names(now=1_000_000.0) == []                      # (a failover runs: not now)
    assert failover.tidy_names(now=1_000_001.0) == []                      # (and not again within ten minutes)
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert failover.tidy_names(now=1_001_000.0) == []                      # (a read-only panel changes nothing)
    monkeypatch.setattr(get_settings(), "read_only", False)
    assert len(failover.tidy_names(now=1_002_000.0)) == 2                  # (the rest, when nothing stands in the way)


def _finished(ok):
    return lambda aid, limit=900: (ok, ["Built." if ok else "the build failed: boom"])


def _auto_names_on_ir1():
    ids = _servers_and_tunnels()

    with session() as db:
        for tid, name in ((ids["t1"], "fi1-ir1"), (ids["t2"], "fi1-ir1-2"), (ids["t3"], "fi1-ir1-3")):
            db.get(Tunnel, tid).name = name

    return ids


def test_the_tunnels_of_a_dead_server_are_renamed_for_their_new_server_and_the_ledger_has_the_new_names(monkeypatch):
    ids = _auto_names_on_ir1()

    with session() as db:
        db.get(Server, ids["ir1"]).online = False

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))

    def replace(aid, tunnel_id, iran_id, *a, **k):                      # (the rebuilt tunnel runs on the target and keeps its old name)
        with session() as db:
            db.get(Tunnel, tunnel_id).iran_server_id = iran_id

        return True

    monkeypatch.setattr(autopilot, "_replace", replace)
    p = failover._make_plan(ids["ir1"], None, "down", target_id=ids["ir2"])
    p["id"] = "ren-1"
    assert failover._work(7, p) is True

    with session() as db:
        assert sorted(t.name for t in db.scalars(select(Tunnel))) == ["fi1-ir2", "fi1-ir2-2", "fi1-ir2-3"]      # (three at the same time, three different names)

    assert sorted(doctor.state()["moves"][0]["tunnels"]) == ["fi1-ir2", "fi1-ir2-2", "fi1-ir2-3"]
    assert sum("renamed" in x for x in said) == 3


def test_a_tunnel_moved_beside_its_old_one_takes_the_name_of_its_new_server(monkeypatch):
    from nikatel.services import builder

    ids = _auto_names_on_ir1()
    _dns_world(monkeypatch)
    monkeypatch.setattr(get_settings(), "read_only", False)
    said, built = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))

    def build(snap, iran_id, foreign_id, kind, opts=None):
        built.append(snap["name"])

        with session() as db:
            db.add(Tunnel(name=snap["name"], type="backhaul", transport="tcpmux", iran_server_id=iran_id, foreign_server_id=foreign_id, state="up", forwarded_ports=[8075]))

        return 0

    def delete(tid, keep_record=False, trash=False, skip_offline=False):
        with session() as db:
            db.delete(db.get(Tunnel, tid))

        return ["Iran: removed"]

    monkeypatch.setattr(autopilot, "_build_kind", build)
    monkeypatch.setattr(autopilot, "_wait", _finished(True))
    monkeypatch.setattr(builder, "delete_tunnel", delete)
    monkeypatch.setattr(failover, "_sleep", lambda secs: None)
    monkeypatch.setattr("nikatel.ssh.run_action", lambda node, name, params=None, timeout=90: "CARRIES 8075\n")
    p = failover._make_plan(ids["ir1"], {ids["t2"]}, "manual", target_id=ids["ir2"])
    p["id"] = "ren-2"
    assert failover._work(7, p) is True

    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == "fi1-ir2"))
        assert t is not None and t.iran_server_id == ids["ir2"] and not (t.record or {}).get("custom_name")
        assert db.scalar(select(Tunnel).where(Tunnel.name == "fi1-ir1-2")) is None

    assert doctor.state()["moves"][0]["tunnels"] == ["fi1-ir2"] and any("renamed fi1-ir2" in x for x in said)
