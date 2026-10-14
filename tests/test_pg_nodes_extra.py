"""The rest of the node API in the Nodes tab: actions on several nodes, the resource curves, the outbound latency, the user counts, restart of every node, and emptying the usage tables.
A fake PasarGuard 5.4 panel stands in; no test reaches a real one."""
import copy
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Panel
from nikatel.services import panels, pg_nodes
from nikatel.services import pg_nodes_extra as nx
from nikatel.services.panels import PanelError

C = {"id": 5, "type": "pasarguard"}


def node(i, name, **kw):
    return {"id": i, "name": name, "address": f"203.0.113.{i}", "port": 62050, "api_port": 62051, "usage_coefficient": 1.0, "connection_type": "grpc", "server_ca": "CERT-TEXT", "keep_alive": 60, "core_config_id": 1,
            "api_key": "KEY-SECRET-123", "data_limit": 0, "data_limit_reset_strategy": "no_reset", "reset_time": -1, "default_timeout": 10, "internal_timeout": 15, "proxy_url": None, "status": "connected",
            "uplink": 5, "downlink": 9, **kw}


class Fake:
    def __init__(self):
        self.nodes = {1: node(1, "n1"), 2: node(2, "n2"), 3: node(3, "n3")}
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.fail: dict[tuple[str, str], PanelError] = {}

    def __call__(self, c, method, path, **kw):
        u = urlsplit(path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.calls.append((method, u.path, copy.deepcopy(kw.get("body")), q))

        if (method, u.path) in self.fail:
            raise self.fail[(method, u.path)]

        m = re.fullmatch(r"/api/nodes/bulk/(\w+)", u.path)

        if m and method == "POST":
            ids = kw["body"]["ids"]
            return {"nodes": [self.nodes[i]["name"] for i in ids], "count": len(ids)}

        if u.path == "/api/nodes/reconnect" and method == "POST":
            return None

        m = re.fullmatch(r"/api/nodes/clear_usage_data/(\w+)", u.path)

        if m and method == "DELETE":
            return {"detail": "ok"}

        m = re.fullmatch(r"/api/node/(\d+)(/\w+)?", u.path)

        if m:
            nid = int(m.group(1))

            if nid not in self.nodes:
                raise PanelError("HTTP 404: Node not found", 404)

            if not m.group(2) and method == "GET":
                return copy.deepcopy(self.nodes[nid])

            if m.group(2) == "/stats":
                return {"period": q["period"], "start": q["start"], "end": q["end"], "stats": [
                    {"period_start": "2026-10-14T00:00:00Z", "cpu_usage_percentage": 10.0, "mem_usage_percentage": 40.0, "incoming_bandwidth_speed": 1000.0, "outgoing_bandwidth_speed": 2000.0},
                    {"period_start": "2026-10-14T01:00:00Z", "cpu_usage_percentage": 30.0, "mem_usage_percentage": 60.0, "incoming_bandwidth_speed": 3000.0, "outgoing_bandwidth_speed": 4000.0}, "junk"]}

            if m.group(2) == "/outbounds_latency":
                return {"latencies": [{"name": "prov-a", "alive": True, "delay": 120, "link": "vless://00000000-0000-4000-8000-000000000000@exit.example.test:443?security=reality&pbk=SECRETKEY#x", "last_seen_time": 5, "last_try_time": 6, "source": "xray"},
                                      {"name": "prov-b", "alive": False, "delay": 0, "link": "not a link", "last_seen_time": 0, "last_try_time": 0, "source": "xray"}]}

        if u.path.startswith("/api/node/user_counts/"):
            return {"period": q["period"], "start": q["start"], "end": q["end"], "metric": u.path.rsplit("/", 1)[1], "count_during_period": 7, "stats": {"-1": [{"period_start": "2026-10-14T00:00:00Z", "count": 3}, {"period_start": "2026-10-14T01:00:00Z", "count": 4}]}}

        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self):
        return [x for x in self.calls if x[0] != "GET"]


@pytest.fixture(autouse=True)
def clean_backups():
    d = get_settings().data_dir / "pg-backups"

    if d.exists():
        for f in d.glob("nodes-p5-*"):
            f.unlink()


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    return f


# ------------------------------------------------------------------ pure
def test_the_ids_of_a_bulk_call_are_whole_numbers_without_doubles():
    assert nx.check_ids([3, 1, 3, 2]) == [3, 1, 2]
    for bad in (None, [], "1", [1.5], [0], [-2], [True], ["1"], list(range(1, 202))):
        with pytest.raises(ValueError):
            nx.check_ids(bad)


def test_a_link_leaves_only_its_scheme_host_and_port():
    assert nx.safe_link("vless://uuid@exit.example.test:443?pbk=KEY#x") == "vless://exit.example.test:443" and nx.safe_link("ss://pass@[2001:db8::1]:8388") == "ss://2001:db8::1:8388"
    for bad in ("", None, "not a link", "vless://", 5):
        assert nx.safe_link(bad) == ""


def test_the_rows_of_the_panel_are_read_tolerantly():
    assert nx.stat_rows({"stats": [{"period_start": "t", "cpu_usage_percentage": 12.34, "mem_usage_percentage": "x", "incoming_bandwidth_speed": 5}, 5]}) == [{"t": "t", "cpu": 12.3, "mem": None, "rx": 5.0, "tx": None}]
    assert nx.stat_rows(None) == [] and nx.stat_rows({"stats": "x"}) == []
    assert nx.latency_rows({"latencies": [{"name": "a", "alive": 1, "delay": 5, "link": "vless://u@h.test:1"}, "x"]})[0]["target"] == "vless://h.test:1" and nx.latency_rows([]) == []
    c = nx.count_rows({"count_during_period": 9, "stats": {"1": [{"period_start": "b", "count": 2}], "2": [{"period_start": "b", "count": 3}, {"period_start": "a", "count": 1}]}}, "online")
    assert c == {"metric": "online", "count": 9, "rows": [{"t": "a", "n": 1}, {"t": "b", "n": 5}]}                          # grouped by node: the nodes of one moment are added up
    assert nx.count_rows(None, "online") == {"metric": "online", "count": 0, "rows": []}


# ------------------------------------------------------------------ reads
def test_the_curves_of_a_node_come_with_the_highest_and_the_average(fake):
    j = nx.node_stats(C, 2, "hour", 12)
    assert j["node_id"] == 2 and len(j["rows"]) == 2 and j["summary"]["cpu"] == {"max": 30.0, "avg": 20.0} and j["summary"]["tx"] == {"max": 4000.0, "avg": 3000.0}
    call = fake.calls[-1]
    assert call[1] == "/api/node/2/stats" and call[3]["period"] == "hour" and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", call[3]["start"])
    with pytest.raises(ValueError, match="period must be one of"):
        nx.node_stats(C, 2, "minute")
    with pytest.raises(PanelError, match="not found"):
        nx.node_stats(C, 99)
    assert nx.node_stats(C, 2, "hour", 99999)["hours"] == 99999 and fake.writes() == []                                      # a huge range is cut by the panel's limits on our side, never an error here


def test_the_latency_never_shows_a_credential(fake):
    j = nx.node_latency(C, 1, "prov-a", 5)
    assert j["alive"] == 1 and j["total"] == 2 and j["rows"][0]["target"] == "vless://exit.example.test:443" and j["rows"][1]["target"] == ""
    assert "00000000-0000" not in str(j) and "SECRETKEY" not in str(j)
    assert fake.calls[-1][3] == {"name": "prov-a", "timeout": "5"} and nx.node_latency(C, 1, timeout=999) and fake.calls[-1][3]["timeout"] == "30"


def test_the_user_counts_are_for_the_panel_or_one_node(fake):
    j = nx.user_counts(C, "online", "day", 48, 3)
    assert j["count"] == 7 and [r["n"] for r in j["rows"]] == [3, 4] and fake.calls[-1][1] == "/api/node/user_counts/online" and fake.calls[-1][3]["node_id"] == "3"
    for kw, why in (({"metric": "fast"}, "metric is one of"), ({"period": "week"}, "period must be one of")):
        with pytest.raises(ValueError, match=why):
            nx.user_counts(C, **{"metric": "online", "period": "hour", **kw})


# ------------------------------------------------------------------ writes
def test_several_nodes_are_handled_with_one_call_and_the_ok_where_users_are_cut(fake):
    out = nx.bulk(C, "enable", [1, 2])
    assert out["count"] == 2 and out["nodes"] == ["n1", "n2"] and out["backups"] == [] and fake.writes()[-1][:3] == ("POST", "/api/nodes/bulk/enable", {"ids": [1, 2]})
    for action in ("disable", "delete", "reconnect", "update", "reset"):
        with pytest.raises(ValueError, match="confirm: true"):
            nx.bulk(C, action, [1])
    assert len(fake.writes()) == 1
    with pytest.raises(ValueError, match="action is one of"):
        nx.bulk(C, "explode", [1], True)
    with pytest.raises(PanelError, match="not found"):
        nx.bulk(C, "reconnect", [1, 99], True)
    assert len(fake.writes()) == 1                                                                                           # a missing node stopped the whole call before it did anything


def test_a_bulk_delete_saves_every_node_first_without_leaking_its_key(fake):
    out = nx.bulk(C, "delete", [1, 3], True)
    assert out["nodes"] == ["n1", "n3"] and len(out["backups"]) == 2 and all(b.startswith("nodes-p5-") and b.endswith("-delete.json") for b in out["backups"])
    assert "KEY-SECRET-123" not in str(out) and [b["node"] for b in pg_nodes.backups(C)] == ["n3", "n1"]                    # the files hold the nodes (to make them again), the answer never the key
    with session() as db:
        assert [e.text for e in db.query(Event).all()] == ["Delete done for 2 nodes (n1, n3)."]                              # one sentence in the events, names only


def test_every_node_is_restarted_only_with_the_ok(fake):
    with pytest.raises(ValueError, match="confirm: true"):
        nx.reconnect_all(C)
    assert fake.writes() == []
    assert nx.reconnect_all(C, True)["restarted"] is True and fake.writes()[-1][1] == "/api/nodes/reconnect" and fake.writes()[-1][3] == {}
    assert nx.reconnect_all(C, True, 4)["core_id"] == 4 and fake.writes()[-1][3] == {"core_id": "4"}


def test_a_usage_table_is_emptied_only_when_its_name_is_typed_back(fake):
    for typed in ("", "yes", "node_usages", "NODE_USER_USAGES"):
        with pytest.raises(ValueError, match="type the name of the table"):
            nx.clear_usage(C, "node_user_usages", typed)
    with pytest.raises(ValueError, match="table is one of"):
        nx.clear_usage(C, "users", "users")
    assert fake.writes() == []
    out = nx.clear_usage(C, "node_usages", "node_usages", "2026-01-01", "2026-02-01T10:00:00Z")
    assert out == {"cleared": "node_usages", "start": "2026-01-01T00:00:00Z", "end": "2026-02-01T10:00:00Z"} and fake.writes()[-1][3] == {"start": "2026-01-01T00:00:00Z", "end": "2026-02-01T10:00:00Z"}
    assert nx.clear_usage(C, "node_usages", " node_usages ")["start"] is None and fake.writes()[-1][3] == {}
    with pytest.raises(ValueError, match="must look like"):
        nx.clear_usage(C, "node_usages", "node_usages", "yesterday")


# ------------------------------------------------------------------ the API
@pytest.fixture
def pid():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="boss", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_the_api_runs_the_node_extras_and_guards_every_write(client, fake, pid):
    base = f"/api/panels/{pid}/pg/nodes"
    assert client.post(base + "/bulk", json={"action": "enable", "ids": [1, 2]}).json()["count"] == 2
    assert client.post(base + "/bulk", json={"action": "delete", "ids": [1]}).status_code == 400
    assert client.post(base + "/bulk", json={"action": "delete", "ids": []}).status_code == 422 and client.post(base + "/bulk", json={"action": "fly", "ids": [1]}).status_code == 422
    assert client.post(base + "/bulk", json={"action": "delete", "ids": [2], "confirm": True}).json()["backups"]
    with session() as db:
        assert any("Delete done for 1 node" in e.text for e in db.query(Event).all())
    assert client.post(base + "/reconnect-all", json={}).status_code == 400 and client.post(base + "/reconnect-all", json={"confirm": True}).json()["restarted"] is True
    assert client.post(base + "/clear-usage", json={"table": "node_usages", "typed": "no"}).status_code == 400 and client.post(base + "/clear-usage", json={"table": "other"}).status_code == 422
    assert client.post(base + "/clear-usage", json={"table": "node_usages", "typed": "node_usages"}).json()["cleared"] == "node_usages"
    assert client.get(base + "/1/stats?period=hour&hours=6").json()["summary"]["cpu"]["max"] == 30.0 and client.get(base + "/1/stats?period=week").status_code == 422
    assert client.get(base + "/1/latency").json()["rows"][0]["target"] == "vless://exit.example.test:443" and client.get(base + "/99/stats").status_code == 502
    assert client.get(base + "/user-counts?metric=expired").json()["metric"] == "expired" and client.get(base + "/user-counts?metric=fast").status_code == 422


def test_the_api_is_for_signed_in_owners_of_pasarguard_panels_only(client, fake, pid):
    from fastapi.testclient import TestClient

    stranger = TestClient(client.app)
    assert stranger.post(f"/api/panels/{pid}/pg/nodes/bulk", json={"action": "enable", "ids": [1]}).status_code == 401 and stranger.get(f"/api/panels/{pid}/pg/nodes/1/stats").status_code == 401
    with session() as db:
        mz = Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(mz)
        db.flush()
        mid = mz.id
    assert client.post(f"/api/panels/{mid}/pg/nodes/bulk", json={"action": "enable", "ids": [1]}).status_code == 400
