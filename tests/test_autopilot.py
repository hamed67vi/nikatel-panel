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


def test_ladder_is_limited_by_the_level():
    assert ap.ladder_for("units_down", "tcpmux", "restart") == ["restart"]
    assert ap.ladder_for("units_down", "tcpmux", "rebuild") == ["restart", "rebuild"]
    assert ap.ladder_for("gre_blocked", "tcpmux", "rebuild") == []
    assert ap.ladder_for("gre_blocked", "tcpmux", "switch") == ["switch"] and ap.ladder_for("port_blocked", "bp:tcp", "switch") == ["switch"]
    assert ap.ladder_for("route_cut", "tcpmux", "switch") == ["move"]          # moving is never automatic, but it is the advice
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
