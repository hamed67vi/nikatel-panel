"""Smart repair: the diagnosis rules, the ladder, kind names, the API guards."""
from nikatel.services import autopilot as ap
from nikatel.services import tunnel_test as tt


def test_kind_of_existing_tunnels():
    assert ap.kind_of("backhaul", "tcpmux", {}) == "tcpmux" and ap.kind_of("", "wssmux", {}) == "wssmux"
    assert ap.kind_of("backpack", "stealth", {"kind": "backpack"}) == "bp:stealth"
    assert ap.kind_of("backpack", "l3:udp", {"kind": "backpack_direct"}) == "bpd:udp" and ap.kind_of("backpack", "udp", {"kind": "backpack_direct"}) == "bpd:udp"
    assert ap.kind_of("paqet", "kcp", {}) == "paqet" and ap.kind_of("chisel", "wss", {}) == "chisel:tls" and ap.kind_of("chisel", "ws", {}) == "chisel"
    assert all(k in tt.KIND_KEYS for k in ap.CANDIDATES + ap.NO_GRE)


def test_classification_picks_the_real_cause():
    assert ap.classify({"state": "up"})[0] == "healthy"
    assert ap.classify({"iran_error": "timeout"})[0] == "server_unreachable"
    assert ap.classify({"state": "down", "units_bad": ["backhaul-x (failed)"]})[0] == "units_down"
    assert ap.classify({"state": "down", "under_loss_i": 100, "under_loss_f": 100})[0] == "route_cut"
    assert ap.classify({"state": "down", "under_loss_i": 0, "under_loss_f": 0, "gre": True, "gre_loss": 100})[0] == "gre_blocked"
    assert ap.classify({"state": "down", "under_loss_i": 0, "under_loss_f": 0, "gre": True, "gre_loss": None})[0] == "gre_blocked"
    assert ap.classify({"state": "down", "under_loss_i": 0, "under_loss_f": 0, "gre": False, "port_open": False, "port": 9601})[0] == "port_blocked"
    assert ap.classify({"state": "down", "under_loss_i": 0, "under_loss_f": 0, "gre": False, "port_open": True})[0] == "unknown"
    assert ap.classify({"state": "degraded", "under_loss_i": 3, "under_loss_f": 2, "gre": True, "gre_loss": 33})[0] == "lossy_route"      # lossy but not cut: restarting cannot help
    assert ap.classify({"state": "degraded", "under_loss_i": 0, "under_loss_f": 0, "gre": True, "gre_loss": 5})[0] != "lossy_route"


def test_ladder_is_limited_by_the_level():
    assert ap.ladder_for("units_down", "tcpmux", "restart") == ["restart"]
    assert ap.ladder_for("units_down", "tcpmux", "rebuild") == ["restart", "rebuild"]
    assert ap.ladder_for("gre_blocked", "tcpmux", "restart") == []
    assert ap.ladder_for("gre_blocked", "tcpmux", "rebuild") == ["relink"]                                  # the link again from scratch: new inner network, new GRE key
    assert ap.ladder_for("gre_blocked", "tcpmux", "switch") == ["relink", "switch"] and ap.ladder_for("port_blocked", "bp:tcp", "switch") == ["relink", "switch"]
    assert ap.ladder_for("route_cut", "tcpmux", "switch") == ["move"]          # the move goes through the failover, which asks first
    assert ap.ladder_for("lossy_route", "tcpmux", "switch") == ["switch", "move"] and ap.ladder_for("lossy_route", "tcpmux", "restart") == ["move"]      # a lossy tunnel is never cut for a relink
    assert ap.ladder_for("healthy", "tcpmux") == [] and ap.ladder_for("server_unreachable", "tcpmux") == []


def test_ranking_keeps_only_working_types():
    got = ap.rank_results([{"kind": "a", "ok": True, "score": 60}, {"kind": "b", "ok": False, "score": 90}, {"kind": "c", "ok": True, "score": 75}, {"kind": "d", "error": "x"}])
    assert [r["kind"] for r in got] == ["c", "a"]


def test_api_guards(client):
    assert client.post("/api/autopilot/smart-fix", json={"tunnel_id": 1, "level": "everything"}).status_code == 422
    assert client.post("/api/autopilot/smart-fix", json={"tunnel_id": 1, "level": "switch"}).status_code == 400      # read-only / no such tunnel
    assert client.post("/api/autopilot/replace", json={"tunnel_id": 1, "kind": "rocket"}).status_code == 400
    assert client.post("/api/autopilot/move", json={"tunnel_id": 1, "iran_id": 2}).status_code == 400
    assert client.get("/api/autopilot/diagnose/999").status_code == 400
    assert client.put("/api/settings/repair", json={"auto": False, "after_seconds": 180, "level": "switch"}).json()["level"] == "switch"
    assert client.put("/api/settings/repair", json={"auto": False, "after_seconds": 180, "level": "nuke"}).status_code == 422


def test_diagnose_config_names_the_likely_cause(client):
    from nikatel.db import session
    from nikatel.models import Config, Server, Tunnel
    from nikatel.services import autopilot

    with session() as db:
        a, b = Server(name="i1", host="10.0.0.1", role="iran"), Server(name="f1", host="10.0.0.2", role="foreign")
        db.add_all([a, b])
        db.flush()
        db.add(Tunnel(name="tun1", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, state="down"))

        for n, st, addr, tun in (("c1", "down", "x.example", "tun1"), ("c2", "down", "y.example", ""), ("c3", "down", "y.example", ""), ("c4", "up", "z.example", ""), ("c5", "down", "z.example", "")):
            db.add(Config(name=n, status=st, meta={"address": addr, "port": 80, "network": "ws", "security": "none"}, tunnel=tun))

        ids = {c.name: c.id for c in db.scalars(__import__("sqlalchemy").select(Config))}

    d = autopilot.diagnose_config(ids["c1"])
    assert d["tunnel_info"]["name"] == "tun1" and "tun1" in d["summary"] and "fix the tunnel" in d["summary"]
    assert "address is cut" in autopilot.diagnose_config(ids["c2"])["summary"]       # every config on that address fails
    assert "only this config fails" in autopilot.diagnose_config(ids["c5"])["summary"]  # a neighbour on the same address works
    assert autopilot.diagnose_config(ids["c4"])["summary"] == "this config works now"
    assert client.get(f"/api/autopilot/diagnose-config/{ids['c1']}").json()["ok"] is True
    assert client.get("/api/autopilot/diagnose-config/9999").status_code == 400


def test_diagnose_config_tells_a_filtered_name_from_a_filtered_address(client, monkeypatch):
    from sqlalchemy import select

    from nikatel.db import session
    from nikatel.models import Config, Server
    from nikatel.services import autopilot

    with session() as db:
        db.add(Server(name="i1", host="10.0.0.1", role="iran"))
        db.add(Config(name="cdn1", status="down", meta={"address": "x.example", "port": 2087, "network": "ws", "security": "tls"}))
        cid = db.scalar(select(Config)).id

    for out, word in (("SNI 203.0.113.7 own=0 control=1", "is blocked from Iran"), ("SNI 203.0.113.7 own=0 control=0", "nothing else shares this address"), ("SNI 203.0.113.7 own=1 control=1", "nothing else shares this address")):
        monkeypatch.setattr(autopilot, "run_script", lambda node, name, params=None, timeout=25, o=out: o)
        d = autopilot.diagnose_config(cid)
        assert word in d["summary"], (out, d["summary"])

    monkeypatch.setattr(autopilot, "run_script", lambda node, name, params=None, timeout=25: "SNI 203.0.113.7 own=0 control=1")
    assert any("the name is filtered" in f["text"] for f in autopilot.diagnose_config(cid)["findings"])


def test_fix_all_runs_every_broken_tunnel_in_one_story(client, monkeypatch):
    from nikatel.db import session
    from nikatel.models import ActionLog, Server, Tunnel
    from nikatel.services import autopilot as ap

    with session() as db:
        a, b = Server(name="i1", host="10.0.0.1", role="iran"), Server(name="f1", host="10.0.0.2", role="foreign")
        db.add_all([a, b])
        db.flush()
        db.add(Tunnel(name="t-down", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, state="down", last_error="x"))
        db.add(Tunnel(name="t-deg", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, state="degraded"))
        db.add(Tunnel(name="t-ok", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, state="up"))

    done = []

    def fake_fix(aid, tid, level, source="manual", finish=True):
        done.append((tid, level, finish))
        ap._say(aid, f"tunnel {tid}: fixed")
        return tid % 2 == 1

    monkeypatch.setattr(ap, "smart_fix", fake_fix)
    monkeypatch.setattr(ap.get_settings(), "read_only", False)
    aid = ap.fix_all_start("restart")
    import time
    for _ in range(50):
        with session() as db:
            e = db.get(ActionLog, aid)
            if e.finished_at:
                break
        time.sleep(0.1)
    with session() as db:
        e = db.get(ActionLog, aid)
        assert e.finished_at and len(done) == 2 and all(not f for _, _, f in done) and e.story[1].startswith("2 tunnel(s) need attention")
        assert e.story[-1].startswith("Done:")

    assert client.post("/api/autopilot/fix-all", json={"level": "nope"}).status_code == 422
    s = client.get("/api/autopilot/situation").json()
    assert {t["name"] for t in s["tunnels"]} == {"t-down", "t-deg"} and s["totals"]["tunnels"] == 3


def test_explain_needs_a_key_and_hands_the_facts_to_the_ai(client, monkeypatch):
    from nikatel.services import ai
    from nikatel.services import autopilot as ap

    assert client.post("/api/autopilot/explain", json={}).status_code == 400          # no key saved
    monkeypatch.setattr(ai, "conf", lambda: {"enabled": True, "api_key": "k"})
    seen = {}
    monkeypatch.setattr(ai, "ask", lambda system, user, max_tokens=700, timeout=45: seen.update({"user": user}) or "Handle the down tunnel first.")
    r = client.post("/api/autopilot/explain", json={}).json()
    assert r["text"].startswith("Handle") and "<facts>" in seen["user"] and "configs_down" in seen["user"]
    assert ap.situation()["totals"]["tunnels"] == 0


def test_classify_flapping_as_a_lossy_route():
    cause, why = ap.classify({"state": "degraded", "flap_fails": 4, "flap_n": 10, "gre": True, "gre_loss": 0.0, "under_loss_i": 0, "under_loss_f": 0})
    assert cause == "lossy_route" and "flaps" in why
    assert ap.classify({"state": "up", "flap_fails": 1, "flap_n": 10, "gre": True, "gre_loss": 0.0})[0] == "healthy"
    assert ap.classify({"state": "down", "flap_fails": 5, "flap_n": 10, "gre": True, "gre_loss": 100.0, "under_loss_i": 0, "under_loss_f": 0})[0] == "gre_blocked"


def test_classify_a_port_blocked_in_front_of_the_server_as_a_cut_route():
    cause, why = ap.classify({"state": "degraded", "ext_blocked": True, "user_port": "8075", "gre": True, "gre_loss": 0.0, "under_loss_i": 0, "under_loss_f": 0})
    assert cause == "route_cut" and "8075" in why and "blocked in front" in why


def test_pathscan_locates_the_loss():
    from nikatel.services import pathscan

    ok, bad = 0.0, 40.0
    assert pathscan.locate({"pair_ab": bad, "pair_ba": ok, "iran_to_other_foreign": [ok, ok, ok], "other_iran_to_foreign": [ok, ok, ok], "iran_anchor": [ok, ok], "foreign_anchor": [ok, ok], "iran_domestic": [ok, ok]})["where"] == "pair_route"
    assert pathscan.locate({"pair_ab": bad, "pair_ba": bad, "iran_to_other_foreign": [bad, bad, ok], "other_iran_to_foreign": [ok, ok], "iran_anchor": [ok, ok], "foreign_anchor": [ok, ok], "iran_domestic": [ok]})["where"] == "iran_side"
    assert pathscan.locate({"pair_ab": bad, "pair_ba": ok, "iran_to_other_foreign": [ok, ok], "other_iran_to_foreign": [bad, bad, ok], "iran_anchor": [ok, ok], "foreign_anchor": [ok, ok], "iran_domestic": [ok]})["where"] == "foreign_side"
    assert pathscan.locate({"pair_ab": bad, "pair_ba": bad, "iran_to_other_foreign": [bad, bad], "other_iran_to_foreign": [bad, bad], "iran_anchor": [ok, ok], "foreign_anchor": [ok, ok]})["where"] == "both"
    assert pathscan.locate({"pair_ab": ok, "pair_ba": ok, "iran_to_other_foreign": [ok], "other_iran_to_foreign": [ok]})["where"] == "none"
    assert pathscan.locate({"pair_ab": None, "pair_ba": None, "iran_to_other_foreign": [], "other_iran_to_foreign": []})["where"] == "unknown"
    assert pathscan.locate({"pair_ab": bad, "pair_ba": ok, "iran_to_other_foreign": [ok, ok, ok], "other_iran_to_foreign": [ok, ok], "iran_anchor": [bad, bad], "foreign_anchor": [ok, ok]})["where"] == "iran_side"      # even toward the internet
    r = pathscan.parse_loss("LOSS 203.0.113.9 20 17 93.512\nLOSS 1.1.1.1 20 0 -\nnoise")
    assert r["203.0.113.9"] == {"loss": 15.0, "avg_ms": 93.512} and r["1.1.1.1"]["loss"] == 100.0 and r["1.1.1.1"]["avg_ms"] is None


def test_clean_ping_but_dead_gre_is_a_gre_filter_not_a_flapping_route():
    cause, why = ap.classify({"state": "down", "flap_fails": 9, "flap_n": 10, "gre": True, "gre_loss": 100.0, "under_loss_i": 0.0, "under_loss_f": 0.0})
    assert cause == "gre_blocked" and "protocol 47" in why
    assert ap.classify({"state": "degraded", "flap_fails": 4, "flap_n": 10, "gre": True, "gre_loss": 0.0, "under_loss_i": 0.0, "under_loss_f": 0.0})[0] == "lossy_route"      # a flapping link without a dead GRE ping stays a route problem


def test_a_swap_needs_a_clearly_better_candidate_and_a_quiet_moment():
    res = [{"kind": "tcpmux", "ok": True, "score": 62}, {"kind": "bp:tcpmux", "ok": True, "score": 55}, {"kind": "chisel:tls", "ok": False, "score": 90}]
    assert ap.choose_better(None, res)["kind"] == "tcpmux"                      # a dead tunnel: the best working candidate
    assert ap.choose_better(50, res)["kind"] == "tcpmux"                        # 62 beats 50 by 12
    assert ap.choose_better(55, res) is None                                    # only 7 better: it stays
    assert ap.choose_better(None, [{"kind": "x", "ok": True, "score": 40}]) is None                              # nothing good enough
    assert ap.quiet_ok(0.4, 0.3, 12.0) is True and ap.quiet_ok(9.0, 1.0, 12.0) is False       # busy: wait
    assert ap.quiet_ok(9.0, 1.0, 35.0) is True and ap.quiet_ok(None, None, 20.0) is True        # very bad loss, or no traffic data: go


def test_mass_disruption_is_detected_and_holds_the_repair_back(client, monkeypatch):
    from nikatel.config import get_settings
    from nikatel.db import session
    from nikatel.models import Server, Tunnel

    monkeypatch.setattr(get_settings(), "read_only", False)

    with session() as db:
        iran = [Server(name=f"i{n}", host=f"10.0.1.{n}", role="iran", online=True) for n in range(1, 5)]
        foreign = [Server(name=f"f{n}", host=f"10.0.2.{n}", role="foreign", online=True) for n in range(1, 5)]
        db.add_all(iran + foreign)
        db.flush()

        for n in range(4):
            db.add(Tunnel(name=f"t{n}", type="backhaul", iran_server_id=iran[n].id, foreign_server_id=foreign[n].id, state="up"))

        db.flush()
        assert ap.outage_state()["storm"] is False
        iran[0].online = iran[1].online = False                          # two servers gone: still a local trouble
        db.flush()

    assert ap.outage_state()["storm"] is False

    with session() as db:
        db.get(Server, iran[2].id).online = False                        # the third: a mass disruption

    o = client.get("/api/autopilot/outage").json()
    assert o["storm"] is True and len(o["servers_offline"]) == 3
    called = []
    monkeypatch.setattr(ap, "smart_fix_start", lambda *a, **k: called.append(a))
    monkeypatch.setattr(ap.st, "get", lambda key: {"auto": True, "level": "switch", "after_seconds": 180} if key == "repair" else {"muted": []})

    with session() as db:                                                # a tunnel that has been down a long time would be repaired in a normal day
        t = db.scalar(__import__("sqlalchemy").select(Tunnel).where(Tunnel.name == "t3"))
        t.state, t.state_since = "down", 1

    ap.auto_escalate()
    assert called == []                                                  # nothing is started in a storm


def test_a_busy_tunnel_test_makes_the_repair_wait_not_fail(monkeypatch):
    calls = []

    def fake_start(iran_id, foreign_id, kinds, measure="latency"):
        calls.append(1)

        if len(calls) < 3:
            raise ValueError("another tunnel test is still running")

        return "job1"

    monkeypatch.setattr(ap.tunnel_test, "compare_start", fake_start)
    monkeypatch.setattr(ap.tunnel_test, "compare_status", lambda jid: {"done": True, "results": [{"kind": "tcpmux", "ok": True, "score": 70}]})
    monkeypatch.setattr(ap, "_say", lambda *a, **k: None)
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    assert ap._recommend_now(1, 1, 2, ["tcpmux"])[0]["score"] == 70 and len(calls) == 3
    monkeypatch.setattr(ap.tunnel_test, "compare_start", lambda *a, **k: (_ for _ in ()).throw(ValueError("the pair has no tunnel")))
    try:
        ap._recommend_now(1, 1, 2, ["tcpmux"])
        raised = False
    except ValueError:
        raised = True

    assert raised
