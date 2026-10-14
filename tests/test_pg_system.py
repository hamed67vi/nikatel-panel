"""The System tab of a PasarGuard panel: the machine, the users and the traffic, the workers, the WireGuard pools and the inbounds in one read-only answer. A fake panel stands in for PasarGuard."""
from urllib.parse import urlsplit

import pytest

from nikatel.db import session
from nikatel.models import Panel
from nikatel.services import panels, pg_system
from nikatel.services.panels import PanelError

C = {"id": 3, "type": "pasarguard"}
SYSTEM = {"total_user": 629, "online_users": 41, "active_users": 500, "on_hold_users": 3, "disabled_users": 20, "expired_users": 60, "limited_users": 46, "incoming_bandwidth": 5 * 1024 ** 3, "outgoing_bandwidth": 9 * 1024 ** 3,
          "version": "5.4.1", "uptime_seconds": 90061, "mem_total": 4_000_000_000, "mem_used": 1_000_000_000, "disk_total": 40_000_000_000, "disk_used": 30_000_000_000, "cpu_cores": 2, "cpu_usage": 12.34}


class Fake:
    def __init__(self):
        self.answers = {"/api/system": SYSTEM, "/api/workers/health": {"scheduler": {"status": "ok", "response_time_ms": 3, "error": None}, "node": {"status": "error", "response_time_ms": None, "error": "no answer"}},
                        "/api/wireguard/subnets": [{"subnet": "10.8.0.0/24", "interface_tags": ["wg0"], "capacity": 253, "used": 10, "free": 243, "free_ips": [f"10.8.0.{i}" for i in range(2, 12)]}],
                        "/api/inbounds/details": [{"tag": "b VLESS", "protocol": "vless", "network": "ws"}, {"tag": "A SS", "protocol": "shadowsocks", "network": "tcp"}]}
        self.fail: dict[str, PanelError] = {}
        self.calls: list[str] = []

    def __call__(self, c, method, path, **kw):
        p = urlsplit(path).path
        self.calls.append(method + " " + p)

        if p in self.fail:
            raise self.fail[p]

        if method == "GET" and p in self.answers:
            return self.answers[p]

        raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    return f


def test_the_machine_and_the_users_come_out_of_the_one_answer():
    m = pg_system.machine(SYSTEM)
    assert m == {"version": "5.4.1", "uptime_seconds": 90061, "cpu_cores": 2, "cpu_percent": 12.3, "mem_total": 4_000_000_000, "mem_used": 1_000_000_000, "mem_percent": 25.0, "disk_total": 40_000_000_000, "disk_used": 30_000_000_000, "disk_percent": 75.0}
    u = pg_system.users_of(SYSTEM)
    assert u["total"] == 629 and u["online"] == 41 and u["on_hold"] == 3 and u["limited"] == 46 and u["bytes_in"] == 5 * 1024 ** 3 and u["bytes_out"] == 9 * 1024 ** 3
    for junk in (None, {}, [], "x", {"cpu_usage": "high", "mem_total": 0, "mem_used": 5, "total_user": True}):
        m, u = pg_system.machine(junk), pg_system.users_of(junk)
        assert m["cpu_percent"] is None and m["mem_percent"] is None and u["total"] is None                                    # a strange answer gives empty fields, never a crash


def test_workers_pools_and_inbounds_are_read_tolerantly():
    assert pg_system.workers_of({"scheduler": {"status": "ok", "response_time_ms": 3}, "node": {"error": "x"}}) == [
        {"name": "scheduler", "status": "ok", "ms": 3, "error": ""}, {"name": "node", "status": "unknown", "ms": None, "error": "x"}]
    assert pg_system.workers_of(None) == [] and pg_system.workers_of({"other": {}}) == []
    pools = pg_system.pools_of([{"subnet": "10.8.0.0/24", "interface_tags": ["wg0"], "capacity": 200, "used": 50, "free": 150, "free_ips": list("abcdefgh")}, {"capacity": 5}, "junk"])
    assert len(pools) == 1 and pools[0]["percent"] == 25.0 and pools[0]["free_ips"] == list("abcde") and pools[0]["tags"] == ["wg0"]
    assert pg_system.inbounds_of([{"tag": "b", "protocol": "vless"}, "a", {"x": 1}, 5]) == [{"tag": "a", "protocol": "", "network": ""}, {"tag": "b", "protocol": "vless", "network": ""}]


def test_the_system_answer_has_everything_and_asks_the_panel_for_reads_only(fake):
    j = pg_system.system(C)
    assert j["machine"]["version"] == "5.4.1" and j["users"]["total"] == 629 and [w["status"] for w in j["workers"]] == ["ok", "error"] and j["wireguard"][0]["subnet"] == "10.8.0.0/24"
    assert [i["tag"] for i in j["inbounds"]] == ["A SS", "b VLESS"] and j["warnings"] == [] and all(c.startswith("GET ") for c in fake.calls)


def test_a_read_the_panel_refuses_is_named_and_the_rest_still_comes(fake):
    fake.fail["/api/workers/health"] = PanelError("HTTP 403: not allowed", 403)
    fake.fail["/api/wireguard/subnets"] = PanelError("HTTP 404: no wireguard", 404)
    fake.fail["/api/inbounds/details"] = PanelError("HTTP 404: old panel", 404)
    fake.answers["/api/inbounds"] = ["tag-a", "tag-b"]
    j = pg_system.system(C)
    assert j["machine"] and j["workers"] == [] and j["wireguard"] == [] and [i["tag"] for i in j["inbounds"]] == ["tag-a", "tag-b"]
    assert len(j["warnings"]) == 3 and "the workers" in j["warnings"][0] and "403" in j["warnings"][0]


def test_when_nothing_can_be_read_the_page_still_gets_an_answer(fake):
    for p in list(fake.answers):
        fake.fail[p] = PanelError("HTTP 500: down", 500)
    fake.answers["/api/inbounds"] = []
    j = pg_system.system(C)
    assert j["machine"] is None and j["users"] is None and j["inbounds"] == [] and len(j["warnings"]) >= 4


@pytest.fixture
def pid():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="boss", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_the_api_gives_the_system_view_to_owners_of_pasarguard_panels_only(client, fake, pid):
    from fastapi.testclient import TestClient

    j = client.get(f"/api/panels/{pid}/pg/system").json()
    assert j["ok"] is True and j["users"]["online"] == 41 and j["machine"]["disk_percent"] == 75.0
    assert TestClient(client.app).get(f"/api/panels/{pid}/pg/system").status_code == 401 and client.get("/api/panels/9999/pg/system").status_code == 404
    with session() as db:
        mz = Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(mz)
        db.flush()
        mid = mz.id
    assert client.get(f"/api/panels/{mid}/pg/system").status_code == 400
