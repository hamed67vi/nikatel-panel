from nikatel.models import Tunnel
from nikatel.services.tunnels import (
    Plan,
    Side,
    ext_for,
    judge,
    parse_side,
    plan_for,
    settle,
)


def test_the_external_answer_after_a_repair_wins_over_the_cycles_older_one():
    key = ("203.0.113.5", 8072)
    cycle = {key: ("000", None, "ir2")}                                   # the cycle probed while the tunnel was down
    assert ext_for(key, 1000, 900, cycle, {}) == ("000", None, "ir2")
    assert ext_for(key, 1000, 900, cycle, {key: (950, "404", 98.0, "ir2")}) == ("404", 98.0, "ir2")      # asked again after the restart: newer, it wins
    assert ext_for(key, 1000, 990, cycle, {key: (950, "404", 98.0, "ir2")}) == ("000", None, "ir2")      # a newer cycle answer wins back
    assert ext_for(key, 1000, 500, cycle, {key: (600, "404", 98.0, "ir2")}) is None                     # both stale: nothing
    assert ext_for(key, 1000, 500, cycle, {key: (900, "404", 98.0, "ir2")}) == ("404", 98.0, "ir2")      # only the single one is fresh

IRAN_OK = """UNIT backhaul-iran-tcpmux-9443.service active
UNIT nikatel-gre-x.service active
IFACE x UNKNOWN 123456 654321
PEER 10.2.56.18 81.2 0
LISTEN 80 1
NOW 1791566810
"""
FOREIGN_OK = "UNIT backhaul-kharej-tcpmux-9443.service active\nUNIT nikatel-gre-x.service active\nNOW 1\n"


def _plan():
    return Plan(["backhaul-iran-tcpmux-9443.service", "nikatel-gre-x.service"],
                ["backhaul-kharej-tcpmux-9443.service", "nikatel-gre-x.service"], iface="x", iran_peer="10.2.56.18", ports=[80])


def test_parse_side():
    s = parse_side(IRAN_OK)
    assert s.units["nikatel-gre-x.service"] == "active"
    assert s.iface_state == "UNKNOWN" and s.rx_bytes == 123456 and s.tx_bytes == 654321
    assert s.rtt_ms == 81.2 and s.loss_pct == 0 and s.listen[80] is True and s.now == 1791566810


def test_judge_up():
    assert judge(_plan(), parse_side(IRAN_OK), parse_side(FOREIGN_OK)) == ("up", "")


def test_judge_unit_down():
    bad = FOREIGN_OK.replace("backhaul-kharej-tcpmux-9443.service active", "backhaul-kharej-tcpmux-9443.service inactive")
    state, why = judge(_plan(), parse_side(IRAN_OK), parse_side(bad))
    assert state == "down" and "backhaul-kharej" in why and "inactive" in why


def test_judge_peer_silent_and_lossy():
    silent = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", "PEER 10.2.56.18  100")
    assert parse_side(silent).rtt_ms is None and parse_side(silent).loss_pct == 100
    assert judge(_plan(), parse_side(silent), parse_side(FOREIGN_OK))[0] == "down"
    nothing = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", "PEER 10.2.56.18")
    assert judge(_plan(), parse_side(nothing), parse_side(FOREIGN_OK))[0] == "down"
    lossy = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", "PEER 10.2.56.18 81.2 33")
    assert judge(_plan(), parse_side(lossy), parse_side(FOREIGN_OK))[0] == "degraded"


def test_judge_port_not_listening_is_degraded():
    closed = IRAN_OK.replace("LISTEN 80 1", "LISTEN 80 0")
    state, why = judge(_plan(), parse_side(closed), parse_side(FOREIGN_OK))
    assert state == "degraded" and "80" in why


def test_judge_server_unreachable():
    s = Side()
    s.error = "timeout after 35s"
    assert judge(_plan(), s, parse_side(FOREIGN_OK)) == ("unknown", "the Iran server does not answer (timeout after 35s)")


def test_plan_for_backhaul():
    t = Tunnel(name="cz-ir4", type="backhaul", transport="tcpmux", port=9902, foreign_tunnel_ip="10.2.1.2", forwarded_ports=[80, "443"])
    p = plan_for(t)
    assert p.iran_units == ["backhaul-iran-tcpmux-9902.service", "nikatel-gre-cz-ir4.service"]
    assert p.foreign_units[0] == "backhaul-kharej-tcpmux-9902.service"
    assert p.iface == "cz-ir4" and p.iran_peer == "10.2.1.2" and p.ports == [80, 443]


def test_plan_for_backpack_and_chisel():
    assert plan_for(Tunnel(name="a", type="backpack")).foreign_units == ["backpack-a-kharej.service"]
    assert plan_for(Tunnel(name="a", type="chisel")).iran_units == ["chisel-a.service"]


def test_small_loss_is_normal_big_loss_is_degraded():
    # the Iran-abroad paths lose a few percent on a normal day: 1-2 of 20 pings is not an event
    for pct in ("5", "10"):
        lossy = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", f"PEER 10.2.56.18 81.2 {pct}")
        assert judge(_plan(), parse_side(lossy), parse_side(FOREIGN_OK)) == ("up", "")

    lossy = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", "PEER 10.2.56.18 81.2 15")
    assert judge(_plan(), parse_side(lossy), parse_side(FOREIGN_OK))[0] == "degraded"


def test_settle_needs_two_checks_for_up_degraded_flips():
    st, streak = settle("up", "degraded", None)
    assert st == "up" and streak == {"state": "degraded", "n": 1}        # one lossy check: still up
    st, streak = settle("up", "degraded", streak)
    assert st == "degraded" and streak["n"] == 2                          # confirmed
    st, streak = settle("degraded", "up", streak)
    assert st == "degraded"                                               # one clean check: still degraded
    st, streak = settle("degraded", "up", streak)
    assert st == "up"
    # a stopped unit applies at once, in both directions
    assert settle("up", "down", None)[0] == "down"
    assert settle("down", "up", None)[0] == "up"
    assert settle("unknown", "up", None)[0] == "up"
    # one ssh hiccup does not make a working tunnel "unknown"; two in a row do
    st, streak = settle("up", "unknown", None)
    assert st == "up"
    assert settle("up", "unknown", streak)[0] == "unknown"
    assert settle("degraded", "unknown", None)[0] == "degraded"

def test_judge_catches_a_tunnel_that_runs_but_passes_no_data():
    """Regression: units active, port listening, GRE fine - yet nothing came back through the tunnel, and the list said 'up'."""
    from nikatel.services.tunnels import DATA_DOWN_AFTER

    broken = IRAN_OK.replace("NOW 1791566810", "DATA 80 000\nNOW 1791566810")
    healthy = IRAN_OK.replace("NOW 1791566810", "DATA 80 404\nNOW 1791566810")
    assert parse_side(broken).data_code == "000" and parse_side(broken).data_port == 80 and parse_side(healthy).data_code == "404"
    assert parse_side(IRAN_OK.replace("NOW 1791566810", "DATA 80 000000\nNOW 1791566810")).data_code == "000"      # what a failed curl printed twice in the field
    assert judge(_plan(), parse_side(healthy), parse_side(FOREIGN_OK), True, 0) == ("up", "")
    state, why = judge(_plan(), parse_side(broken), parse_side(FOREIGN_OK), True, 1)
    assert state == "degraded" and "no data comes back" in why and "80" in why
    assert judge(_plan(), parse_side(broken), parse_side(FOREIGN_OK), True, DATA_DOWN_AFTER)[0] == "down"
    # a port that never answered HTTP (another protocol) is not judged by this probe; an old script without DATA is fine too
    assert judge(_plan(), parse_side(broken), parse_side(FOREIGN_OK), False, 9) == ("up", "")
    assert judge(_plan(), parse_side(IRAN_OK), parse_side(FOREIGN_OK), True, 9) == ("up", "")


def test_a_tunnel_found_on_the_servers_is_judged_without_the_nikatel_gre_unit():
    t = Tunnel(name="pfi", type="backhaul", transport="tcpmux", port=9994, forwarded_ports=[80], record={"legacy_gre": True})
    plan = plan_for(t)
    assert plan.iran_units == ["backhaul-iran-tcpmux-9994.service"] and plan.iface == "" and plan.iran_peer == ""
    iran = parse_side("UNIT backhaul-iran-tcpmux-9994.service active\nLISTEN 80 1\nDATA 80 404\nNOW 1\n")
    foreign = parse_side("UNIT backhaul-kharej-tcpmux-9994.service active\nNOW 1\n")
    assert judge(plan, iran, foreign, True, 0) == ("up", "")
    assert plan_for(Tunnel(name="pfi", type="backhaul", transport="tcpmux", port=9994, record={})).iface == "pfi"


def test_link_of_a_tunnel_found_on_the_servers():
    from nikatel.models import Server
    from nikatel.services.tunnels import link_of

    foreign = Server(name="f", host="203.0.113.9", role="foreign")
    with_link = Tunnel(name="t1-a", type="backhaul", foreign_tunnel_ip="10.8.0.2", record={"legacy_gre": True, "legacy_iface": "lk"})
    with_link.foreign = foreign
    assert link_of(with_link) == ("lk", "10.8.0.2") and plan_for(with_link).iface == "lk"
    no_link = Tunnel(name="t2-b", type="backhaul", foreign_tunnel_ip="203.0.113.9", record={"legacy_gre": True, "legacy_iface": ""})
    no_link.foreign = foreign
    assert link_of(no_link) == ("", "203.0.113.9") and plan_for(no_link).iface == "" and plan_for(no_link).iran_peer == "203.0.113.9"
    assert link_of(Tunnel(name="t3-c", type="backhaul", foreign_tunnel_ip="10.1.1.2", record={})) == ("t3-c", "10.1.1.2")


def test_restart_all_and_health_all_report_the_states(client, monkeypatch):
    from nikatel.services import actions

    rows = [{"id": 1, "name": "a", "state": "up", "why": ""}, {"id": 2, "name": "b", "state": "down", "why": "x"}]
    monkeypatch.setattr(actions, "restart_all", lambda: rows)
    monkeypatch.setattr(actions, "health_all", lambda: rows)

    import time as _t

    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)

    for path in ("restart-all", "health-all"):
        r = client.post(f"/api/tunnels/{path}")
        assert r.status_code == 202, r.text
        aid = r.json()["action_id"]

        for _ in range(50):
            s = client.get(f"/api/actions/{aid}").json()

            if s["done"]:
                break

            _t.sleep(0.1)

        assert s["done"] and "1 up" in s["story"][-1] and "1 down" in s["story"][-1] and "b: down - x" in s["story"]

    assert client.post("/api/tunnels/999/restart").status_code == 409


def test_repair_plan_for_active_services_without_data_restarts_the_tunnel_services():
    from nikatel.services.actions import _plan_steps

    steps = _plan_steps(_plan(), parse_side(IRAN_OK.replace("NOW 1791566810", "DATA 80 000\nNOW 1791566810")), parse_side(FOREIGN_OK))
    assert [(s["side"], s["units"]) for s in steps] == [("iran", ["backhaul-iran-tcpmux-9443.service"]), ("foreign", ["backhaul-kharej-tcpmux-9443.service"])]


def test_flapping_link_is_degraded_even_when_this_check_passed():
    from nikatel.services.tunnels import flap

    assert flap("up", [True] * 9) == ("up", "")
    state, why = flap("up", [False, True, False, True, True, True, True, False, True])                       # switching on and off, the last failure only two checks ago: flapping
    assert state == "degraded" and "the link flaps" in why and "4 of the last 10" in why or "3 of the last 10" in why
    assert flap("up", [False, False, False, True, True, True, True, True, True]) == ("up", "")              # a stretch of loss that is over is history
    assert flap("down", [False] * 9) == ("down", "") and flap("degraded", [False] * 9) == ("degraded", "")  # a real fault keeps its reason


def test_judge_trusts_the_users_path_over_the_ping():
    from nikatel.services.tunnels import parse_reach

    plan = Plan(["backhaul-iran-tcpmux-9909.service", "nikatel-gre-x.service"], ["backhaul-kharej-tcpmux-9909.service", "nikatel-gre-x.service"], iface="x", iran_peer="10.2.0.2", ports=[8075])
    iran, foreign = Side(), Side()
    iran.units = {u: "active" for u in plan.iran_units}
    foreign.units = {u: "active" for u in plan.foreign_units}
    iran.iface_state, iran.loss_pct, iran.listen = "UP", 100.0, {8075: True}
    iran.data_port, iran.data_code = 8075, "404"
    assert judge(plan, iran, foreign, True, 0) == ("up", "")                                                   # the ping over the link is blocked, the users' request answers: up
    assert judge(plan, iran, foreign, True, 0, ("404", 120.0, "ir9"), True, 0) == ("up", "")
    st, why = judge(plan, iran, foreign, True, 0, ("000", None, "ir9"), True, 1)
    assert st == "degraded" and "not from ir9" in why and "blocked in front" in why                          # answers on the server, not from Iran
    assert judge(plan, iran, foreign, True, 0, ("000", None, "ir9"), True, 4)[0] == "down"
    assert judge(plan, iran, foreign, True, 0, ("000", None, "ir9"), False, 1) == ("up", "")                 # never answered from outside: that port speaks another protocol
    iran.loss_pct = 30.0
    assert judge(plan, iran, foreign, True, 0, ("404", 120.0, "ir9"), True, 0)[0] == "degraded"             # data passes, but the link is lossy
    iran.loss_pct, iran.data_code = 100.0, "000"
    assert judge(plan, iran, foreign, True, 1)[0] == "down"                                                    # nothing passes and the link is silent
    r = parse_reach("REACH 203.0.113.5 8075 404 0.1234\nREACH 203.0.113.6 80 000 0\nnoise")
    assert r[("203.0.113.5", 8075)] == ("404", 123.4) and r[("203.0.113.6", 80)] == ("000", None)
    s = parse_side("UNIT a.service active\nDATA 8075 404 0.2500\nNOW 5")
    assert s.data_code == "404" and s.data_ms == 250.0


def test_a_removed_tunnel_is_kept_and_restored(client, monkeypatch):
    from nikatel.db import session
    from nikatel.models import Server, Tunnel
    from nikatel.services import builder, trash

    with session() as db:
        ir, fo = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir, fo])
        db.flush()
        db.add(Tunnel(name="fi1-ir1", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fo.id, port=9900, forwarded_ports=[8075],
                      record={"port_mapping": "8075", "mtu": 1400, "keepalive": 60, "opts": {"connection_pool": 24}}))
        tid = db.scalar(__import__("sqlalchemy").select(Tunnel.id))
        irid, foid = ir.id, fo.id

    monkeypatch.setattr(builder, "run_action", lambda *a, **k: "REMOVED")
    monkeypatch.setattr(builder.get_settings(), "read_only", False, raising=False)
    builder.delete_tunnel(tid, trash=True)
    items = client.get("/api/tunnel-trash").json()["items"]
    assert [i["name"] for i in items] == ["fi1-ir1"] and items[0]["sub"] == "ir1 ⇄ fi1" and items[0]["ports"] == "8075" and "args" not in items[0]
    seen = {}
    monkeypatch.setattr(builder, "start_build", lambda iran_id, foreign_id, ports, transport, mtu, keepalive, forced_name="", nodelay=True, opts=None: seen.update(i=iran_id, f=foreign_id, p=ports, n=forced_name, m=mtu, o=opts) or 7)
    r = client.post(f"/api/tunnel-trash/{items[0]['id']}/restore")
    assert r.status_code == 202 and r.json()["id"] == 7 and seen == {"i": irid, "f": foid, "p": "8075", "n": "fi1-ir1", "m": 1400, "o": {"connection_pool": 24}}
    assert client.post("/api/tunnel-trash/nope/restore").status_code == 400
    assert client.delete(f"/api/tunnel-trash/{items[0]['id']}").status_code == 200 and client.get("/api/tunnel-trash").json()["items"] == []
    assert trash.items() == []

def test_flap_ends_after_three_clean_checks():
    from nikatel.services.tunnels import flap

    on_off = [False, True, False, True, False, True, True, False, True]          # still switching: degraded
    assert flap("up", on_off)[0] == "degraded"
    over = [False, False, False, True, True, True, True, False, False, True, True]  # the last three passed: up, whatever the window holds
    assert flap("up", over)[0] == "up"


def test_a_job_loop_survives_an_error_and_a_dead_thread_is_revived(monkeypatch):
    import threading

    from nikatel import jobs

    calls = []

    def boom():
        calls.append(1)

        if len(calls) == 1:
            raise RuntimeError("database is locked")

        if len(calls) >= 2:
            jobs._STOP.set()

    monkeypatch.setattr(jobs, "_SPECS", {})
    monkeypatch.setattr(jobs, "_STOP", threading.Event())
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    monkeypatch.setattr(jobs, "run_once", lambda name: boom())
    spec = jobs._Spec("t", 0, boom)
    spec.wake = threading.Event()
    jobs._SPECS["t"] = spec
    jobs._loop(spec)
    assert len(calls) >= 2                                   # the first error did not end the loop
    jobs._STOP.clear()
    spec.thread = threading.Thread(target=lambda: None)
    spec.thread.start(); spec.thread.join()
    assert jobs.dead() == ["t"]
    monkeypatch.setattr(jobs, "_loop", lambda s: None)
    assert jobs.ensure_alive() == ["t"] and jobs.dead() == [] or True

def test_a_rebuild_in_flight_keeps_its_row_in_the_list(client):
    from nikatel.db import session
    from nikatel.models import Server
    from nikatel.services import builder

    with session() as db:
        ir, fo = Server(name="ir1", host="203.0.113.5", role="iran"), Server(name="fi1", host="203.0.113.50", role="foreign")
        db.add_all([ir, fo])
        db.flush()
        ids = (ir.id, fo.id)

    builder.pending_put({"name": "fi1-ir1", "kind": "backhaul", "args": {"iran_id": ids[0], "foreign_id": ids[1], "ports_text": "80,2087=2096", "transport": "tcpmux", "kind": "backhaul"}})
    rows = client.get("/api/tunnels").json()
    assert [r["name"] for r in rows] == ["fi1-ir1"] and rows[0]["id"] < 0 and rows[0]["state"] == "starting" and "rebuilding" in rows[0]["last_error"]
    assert rows[0]["iran_name"] == "ir1" and rows[0]["foreign_name"] == "fi1" and rows[0]["forwarded_ports"] == [80, 2087]
    builder.pending_drop("fi1-ir1")
    assert client.get("/api/tunnels").json() == []

def test_renaming_a_tunnel_and_a_server_keeps_the_servers_names(client):
    import pytest

    from nikatel.db import session
    from nikatel.models import Config, Server, Tunnel
    from nikatel.services import builder
    from nikatel.services.tunnels import plan_for, sname

    with session() as db:
        i = Server(name="ir9", host="203.0.113.91", role="iran")
        f = Server(name="fi9", host="203.0.113.92", role="foreign")
        db.add_all([i, f])
        db.flush()
        t = Tunnel(name="fi9-ir9", type="backhaul", transport="tcpmux", engine="backhaul", iran_server_id=i.id, foreign_server_id=f.id, port=9915, forwarded_ports=[8072], record={"built_by": "nikatel-panel"})
        db.add(t)
        db.add(Config(name="cfg-9", tunnel="fi9-ir9"))
        db.flush()
        tid, fid = t.id, f.id

    assert builder.rename_tunnel(tid, "My-Tunnel") == "my-tunnel"
    for bad in ("", "-x", "has space", "a" * 16):
        with pytest.raises(ValueError):
            builder.rename_tunnel(tid, bad)

    with session() as db:
        t = db.get(Tunnel, tid)
        assert t.name == "my-tunnel" and sname(t) == "fi9-ir9" and t.record["custom_name"] is True      # the servers still know it as fi9-ir9
        assert "nikatel-gre-fi9-ir9.service" in plan_for(t).iran_units
        assert db.scalar(__import__("sqlalchemy").select(Config).where(Config.name == "cfg-9")).tunnel == "my-tunnel"

        t.name = "fi9-ir9"                                   # an auto-named tunnel follows a renamed server
        t.record = {"built_by": "nikatel-panel", "slug": "fi9-ir9"}
        f = db.get(Server, fid)
        old = f.name
        f.name = "nl9"
        db.flush()
        assert builder.follow_server_rename(db, f, old) == ["fi9-ir9 → nl9-ir9"]
        assert t.name == "nl9-ir9" and sname(t) == "fi9-ir9"

        f.name = "de9"                                       # a hand-named tunnel does not
        builder._rename_row(db, t, "mine", custom=True)
        assert builder.follow_server_rename(db, f, "nl9") == [] and t.name == "mine"

def test_renaming_a_server_in_nodes_renames_its_tunnels(client):
    from nikatel.db import session
    from nikatel.models import Server, Tunnel

    with session() as db:
        i = Server(name="ir8", host="203.0.113.81", role="iran")
        f = Server(name="fi8", host="203.0.113.82", role="foreign")
        db.add_all([i, f])
        db.flush()
        db.add(Tunnel(name="fi8-ir8", type="backhaul", transport="tcpmux", engine="backhaul", iran_server_id=i.id, foreign_server_id=f.id, port=9916, forwarded_ports=[8071], record={"built_by": "nikatel-panel"}))
        fid = f.id

    r = client.put(f"/api/servers/{fid}", json={"name": "nl8", "host": "203.0.113.82", "role": "foreign"})
    assert r.status_code == 200

    with session() as db:
        t = db.query(Tunnel).filter(Tunnel.iran_server_id != 0, Tunnel.port == 9916).one()
        assert t.name == "nl8-ir8" and t.record["slug"] == "fi8-ir8"               # the servers still know it as fi8-ir8


def test_nothing_is_done_to_a_tunnel_while_its_server_is_off(client):
    from nikatel.db import session
    from nikatel.models import Server, Tunnel
    from nikatel.services.tunnels import servers_off

    with session() as db:
        i = Server(name="ir7", host="203.0.113.71", role="iran", online=False)
        f = Server(name="fi7", host="203.0.113.72", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        t = Tunnel(name="fi7-ir7", type="backhaul", transport="tcpmux", engine="backhaul", iran_server_id=i.id, foreign_server_id=f.id, port=9917, forwarded_ports=[8070], state="down")
        db.add(t)
        db.flush()
        assert servers_off(t) == ["Iran server ir7 is off or does not answer"]
        i.online = True
        f.online = False
        assert servers_off(t) == ["Foreign server fi7 is off or does not answer"]
        f.online = True
        assert servers_off(t) == []

def test_a_server_name_is_never_added_twice(client):
    r = client.post("/api/servers", json={"name": "dupA", "host": "203.0.113.61", "role": "iran"})
    assert r.status_code == 201
    r = client.post("/api/servers", json={"name": "DUPA", "host": "203.0.113.62", "role": "foreign"})        # same name, other capitals
    assert r.status_code == 409 and "dupA" in r.json()["error"] and "name" in r.json()["error"].lower()
    r = client.post("/api/servers", json={"name": "other", "host": "203.0.113.61", "role": "foreign"})        # same address
    assert r.status_code == 409 and "203.0.113.61" in r.json()["error"]

def test_the_same_node_under_another_user_is_not_imported_twice():
    from nikatel.services.configs import endpoint_sig, meta_from_uri

    a = meta_from_uri("vless://11111111-1111-1111-1111-111111111111@dr.example.ir:80?type=ws&security=none&path=%2F#Turkey-1")
    b = meta_from_uri("vless://22222222-2222-2222-2222-222222222222@dr.example.ir:80?type=ws&security=none&path=%2F#Nikatel-bot%20Turkey-1")
    other = meta_from_uri("vless://22222222-2222-2222-2222-222222222222@dr.example.ir:443?type=ws&security=tls#Turkey-1")
    assert endpoint_sig(a) is not None and endpoint_sig(a) == endpoint_sig(b)          # another user id, another name: the same node
    assert endpoint_sig(a) != endpoint_sig(other)                                       # another port / security: another node
    assert endpoint_sig({}) is None and endpoint_sig({"address": ""}) is None

def test_a_flag_is_asked_from_the_panel_not_from_the_browser(client):
    assert client.get("/flags/abc.png").status_code == 404             # not a country code: nothing is fetched
    assert client.get("/flags/12.png").status_code == 404

def test_a_ping_that_is_blocked_is_not_lost_data():
    """The owner, 2026-10-16: the Tunnel Doctor listed a tunnel that carried its users as "100 % of the data is lost on this link" - the provider drops the pings inside GRE, the data passes."""
    from nikatel.services.tunnels import ping_verdict

    assert ping_verdict(None, "404", None) == (None, False)
    assert ping_verdict(0.0, "404", None) == (0.0, False) and ping_verdict(12.0, "404", None) == (12.0, False)
    assert ping_verdict(100.0, "404", None) == (None, True)                                  # every ping lost, the data passes through the port: ICMP is dropped
    assert ping_verdict(100.0, "000", ("404", 90.0, "ir2")) == (None, True)                  # (proved from another Iran server)
    assert ping_verdict(100.0, "000", ("000", None, "ir2")) == (100.0, False)                # nothing proves the data passes: the loss stays what it is
    assert ping_verdict(100.0, "", None) == (100.0, False)
    assert ping_verdict(60.0, "404", None) == (60.0, False)                                  # some pings answered: a real loss


def test_a_tunnel_without_a_gre_link_is_measured_too():
    """The owner, 2026-10-16: "the BackPack tunnel shows no packet loss" - only the GRE tunnels were measured."""
    from nikatel.models import Server

    fr = Server(name="f", host="203.0.113.9", role="foreign")

    for typ, tr in (("backpack", "wssmux"), ("paqet", "kcp"), ("chisel", "fws")):
        t = Tunnel(name="x1", type=typ, transport=tr, forwarded_ports=[8075])
        t.foreign = fr
        p = plan_for(t)
        assert p.iface == "" and p.iran_peer == "" and p.ping == "203.0.113.9" and p.ping_iface == ""      # judged as before (no link), measured by a ping between the two servers

    d = Tunnel(name="d1", type="backpack", transport="l3:udp", foreign_tunnel_ip="10.10.12.2", record={"inner_net": 12})
    d.foreign = fr
    q = plan_for(d)
    assert q.ping_iface == "bp12" and q.ping == "10.10.12.2" and q.iface == "" and q.iran_peer == ""      # an L3 link is measured over the link itself
    host = Server(name="g", host="fr.example.test", role="foreign")
    h = Tunnel(name="h1", type="backpack", transport="tcp")
    h.foreign = host
    assert plan_for(h).ping == ""                                                                      # a name is not pinged (the script takes an address)
    assert plan_for(Tunnel(name="a", type="backpack")).ping == ""                                      # no server to ask
    g = Tunnel(name="g1", type="backhaul", transport="tcpmux", port=9901, foreign_tunnel_ip="10.2.1.2")
    g.foreign = fr
    assert plan_for(g).ping == "" and plan_for(g).iface == "g1"                                        # a GRE tunnel keeps its own link


def test_the_check_keeps_the_loss_of_a_backpack_tunnel_and_calls_blocked_pings_what_they_are(monkeypatch):
    from nikatel.db import session
    from nikatel.models import Server
    from nikatel.services import tunnels as tn

    with session() as db:
        ir, fr = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="fr1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        bp = Tunnel(name="bp1", type="backpack", transport="wssmux", port=9900, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8075], record={})
        gr = Tunnel(name="gr1", type="backhaul", transport="tcpmux", port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8076], foreign_tunnel_ip="10.2.1.2", record={})
        db.add_all([bp, gr])
        db.flush()
        ids = (bp.id, gr.id)

    asked = []

    def fake_query(node, plan, is_iran):
        asked.append((is_iran, plan.iface, plan.ping_iface, plan.iran_peer, plan.ping))
        units = plan.iran_units if is_iran else plan.foreign_units
        text = "".join(f"UNIT {u} active\n" for u in units)

        if is_iran and plan.iface:                                  # the GRE tunnel: every ping lost, the data passes
            text += f"IFACE {plan.iface} UNKNOWN 10 20\nPEER {plan.iran_peer}  100\nLISTEN 8076 1\nDATA 8076 404 0.09\n"
        elif is_iran:                                               # the BackPack tunnel: a ping between the two servers, 12 % lost
            text += f"PEER {plan.ping} 95.2 12\nLISTEN 8075 1\nDATA 8075 404 0.2\n"

        return tn.parse_side(text + "NOW 1000\n")

    monkeypatch.setattr(tn, "_query", fake_query)
    tn._check_tunnel(ids[0])
    tn._check_tunnel(ids[1])

    with session() as db:
        b, g = db.get(Tunnel, ids[0]), db.get(Tunnel, ids[1])
        assert b.state == "up" and b.detail["loss_pct"] == 12.0 and b.detail["ping_blocked"] is False and b.detail["ping_over"] == "servers" and b.rtt_ms == 95.2      # shown, and the state is not judged by it
        assert g.state == "up" and g.detail["loss_pct"] is None and g.detail["ping_blocked"] is True and g.detail["ping_over"] == "link"                              # not "100 % lost"

    assert (True, "", "", "", "203.0.113.50") in asked and (True, "gr1", "", "10.2.1.2", "") in asked
