"""The Investigate report of a PasarGuard user: the rules that judge it (pure), the message for the customer and the read of the panel."""
import datetime as dt

from nikatel.services import pg_investigate as inv
from nikatel.services import pg_usage, pg_users

GB = 1024 ** 3
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _hours(per_hour: dict[int, int], node: str = "iran-a") -> list[dict]:
    """48 hour rows (Tehran hour 0..23 twice) with `per_hour` bytes at the given Tehran hours."""
    out = []

    for i in range(48):
        h = i % 24
        out.append({"at": "x", "local": f"2026-11-0{2 + i // 24} {h:02d}:30", "hour": h, "bytes": per_hour.get(h, 0), "node": node if per_hour.get(h) else ""})

    return out


def _user(**kw) -> dict:
    return {"status": "active", "used": 10 * GB, "limit": 50 * GB, "hwid_limit": None, "age_days": 10.0, **kw}


def _codes(f: dict) -> list[str]:
    return [x["code"] for x in inv.judge(f)]


def test_hours_of_fills_the_empty_hours_and_names_the_busiest_node():
    start = dt.datetime(2026, 11, 2, 20, 30, tzinfo=UTC)
    per = {1: [(dt.datetime(2026, 11, 2, 22, 0, tzinfo=UTC), 3 * GB, 0)], 2: [(dt.datetime(2026, 11, 2, 22, 0, tzinfo=UTC), GB, 0)]}
    rows = inv.hours_of(per, {1: "iran-a", 2: "iran-b"}, start, start + dt.timedelta(hours=5))
    assert len(rows) == 6                                                     # 20:00 .. 01:00 UTC, empty hours as 0
    busy = [r for r in rows if r["bytes"]]
    assert busy == [{"at": "2026-11-02T22:00:00Z", "local": "2026-11-03 01:30", "hour": 1, "bytes": 4 * GB, "node": "iran-a"}]


def test_busiest_window_and_night_share():
    hrs = _hours({2: 4 * GB, 3: 3 * GB, 4: 3 * GB, 15: GB})
    b = inv.busiest(hrs)
    assert (b["bytes"], b["count"], b["node"]) == (10 * GB, 3, "iran-a") and 0.4 < b["share"] < 0.5          # (the same day twice: the best 3 hours are half of the whole)
    assert inv.night_share(hrs) > 0.8
    assert inv.busiest(_hours({})) is None and inv.night_share(_hours({})) == 0.0


def test_a_shared_link_is_flagged_by_devices_addresses_and_apps():
    f = {"user": _user(), "now": NOW, "devices": [{"last": "2026-11-03T10:00:00Z"}] * 5,
         "fetches": [{"agent": "appone/1.0", "ip": "192.0.2.1"}, {"agent": "apptwo/2.0", "ip": "192.0.2.2"}, {"agent": "appthree/3", "ip": "192.0.2.3"}]}
    got = _codes(f)
    assert {"devices_over", "devices_active", "fetch_ips", "fetch_apps"} <= set(got) and got[0] != "ok"
    assert inv.verdict(inv.judge(f))["level"] == "warn"


def test_the_device_limit_of_the_user_beats_the_rule_of_the_panel():
    devs = [{"last": "2020-01-01T00:00:00Z"}] * 4
    assert "devices_over" in _codes({"user": _user(), "now": NOW, "devices": devs})                       # the rule of the panel is 3
    assert "devices_over" not in _codes({"user": _user(hwid_limit=5), "now": NOW, "devices": devs})


def test_proxy_addresses_do_not_count_as_different_addresses():
    f = {"user": _user(), "now": NOW, "fetches": [{"agent": "appone/1", "ip": "127.0.0.1"}] * 6}
    got = _codes(f)
    assert "fetch_hidden" in got and "fetch_ips" not in got


def test_night_burst_and_spike():
    assert "night" in _codes({"user": _user(), "now": NOW, "hours": _hours({2: 2 * GB, 3: 2 * GB, 4: GB, 15: GB})})
    assert "night" not in _codes({"user": _user(), "now": NOW, "hours": _hours({2: 100 * 1024 ** 2, 15: 5 * GB})})        # small night usage is not a finding
    burst = _codes({"user": _user(), "now": NOW, "hours": _hours({14: 4 * GB, 15: 3 * GB, 16: 2 * GB})})
    assert "burst" in burst and "night" not in burst
    days = [{"t": f"2026-10-{20 + i}", "bytes": GB} for i in range(7)] + [{"t": "2026-10-27", "bytes": 9 * GB}]
    assert "spike" in _codes({"user": _user(), "now": NOW, "days": days})
    assert "spike" not in _codes({"user": _user(), "now": NOW, "days": days[:3]})                        # too little history to call it usual


def test_burn_left_and_status_notes():
    days = [{"t": f"2026-10-{20 + i}", "bytes": 2 * GB} for i in range(8)]
    got = _codes({"user": _user(used=45 * GB, age_days=3.0), "now": NOW, "days": days})
    assert "burn" in got and "left" in got and "ok" in got
    assert "limited" in _codes({"user": _user(status="limited", used=50 * GB), "now": NOW})
    assert "expired" in _codes({"user": _user(status="expired"), "now": NOW})
    by = [{"name": "iran-a", "bytes": 20 * GB, "share": 0.8}]
    assert "node" in _codes({"user": _user(), "now": NOW, "by_node_30d": by})


def test_nothing_unusual_is_said_plainly():
    f = {"user": _user(), "now": NOW, "devices": [{"last": "2026-11-03T10:00:00Z"}], "hours": _hours({14: GB, 15: GB}), "days": [{"t": f"2026-10-{20 + i}", "bytes": GB} for i in range(8)]}
    out = inv.judge(f)
    assert out[0]["code"] == "ok" or out[0]["level"] in ("info", "ok")
    assert inv.verdict(out) == {"level": "ok", "title": "Nothing unusual found"}


def test_the_message_for_the_customer_names_the_facts_and_no_address():
    f = {"user": _user(username="demo-1", used=47 * GB), "now": NOW, "hours": _hours({2: 4 * GB, 3: 3 * GB, 4: 3 * GB}), "devices": [{"last": "2026-11-03T10:00:00Z"}] * 4,
         "fetches": [{"agent": "appone/1", "ip": "192.0.2.9"}] * 2}
    text = inv.customer_text({"username": "demo-1", "used": 47 * GB, "limit": 50 * GB}, inv.judge(f))
    assert "demo-1" in text and "47" in text and "50" in text and "192.0.2" not in text
    assert text.count("\n") >= 3
    assert "نامحدود" in inv.customer_text({"username": "demo-2", "used": GB, "limit": 0}, [])


def test_investigate_reads_the_panel_and_leaves_out_what_it_cannot_read(monkeypatch):
    raw = {"id": 7, "username": "demo-1", "status": "active", "used_traffic": 20 * GB, "data_limit": 50 * GB, "created_at": "2026-10-30T10:00:00Z", "online_at": "2026-11-03T11:59:00Z", "hwid_limit": 2}
    monkeypatch.setattr(pg_users, "_id_of", lambda c, n: 7)
    monkeypatch.setattr(pg_users, "_call", lambda c, m, p, *a, **k: raw)
    monkeypatch.setattr(pg_usage, "node_names", lambda c: {1: "iran-a"})
    stats = {"stats": {"1": [{"period_start": "2026-11-03T00:00:00Z", "total_traffic": 3 * GB, "uplink": 0, "downlink": 0}]}}
    monkeypatch.setattr(pg_usage, "_get", lambda c, path, t=30: stats)
    monkeypatch.setattr(pg_usage, "user_usage", lambda c, n, p, **k: {"series": [{"t": f"2026-10-{20 + i}", "bytes": GB} for i in range(8)], "by_node": [{"name": "iran-a", "bytes": 8 * GB, "share": 1.0}]})
    monkeypatch.setattr(pg_users, "devices", lambda c, n: {"devices": [{"first": "2026-10-30T10:00:00Z", "last": "2026-11-03T08:00:00Z"}] * 3, "count": 3})
    monkeypatch.setattr(pg_users, "sub_updates", lambda c, n, o=0, l=10: {"updates": [{"agent": "appone/1", "ip": "127.0.0.1", "at": "x"}], "count": 9})

    def no_online(c, n):
        raise pg_users.PanelError("nothing", 404)

    monkeypatch.setattr(pg_users, "online_ips", no_online)
    r = inv.investigate({"id": 1, "type": "pasarguard"}, "demo-1", now=NOW)
    assert r["user"]["pct"] == 40 and r["user"]["age_days"] == 4.08 and r["devices"] == {"limit": 2, "own_limit": 2, "count": 3, "rows": r["devices"]["rows"]}
    assert r["online"] == [] and r["fetches"]["hidden"] is True and r["fetches"]["count"] == 9
    assert r["usage"]["by_node_48h"][0]["name"] == "iran-a" and r["usage"]["total_48h"] == 3 * GB
    assert any(x["code"] == "devices_over" for x in r["findings"]) and r["verdict"]["level"] == "warn" and r["text"] and r["notes"] == []


def test_the_live_addresses_are_read_as_last_seen_times(monkeypatch):
    monkeypatch.setattr(pg_users, "_id_of", lambda c, n: 7)
    answers = {"/api/node/online_stats/7/ip": {"nodes": {"17": {"ips": {"192.0.2.5": 1793664233, "192.0.2.6": 1793664251}}, "18": {"192.0.2.7": 3}}}, "/api/nodes/simple?all=true": {"nodes": [{"id": 17, "name": "iran-a"}]}}
    monkeypatch.setattr(pg_users, "_call", lambda c, m, p, *a, **k: answers[p])
    rows = pg_users.online_ips({"id": 1}, "demo-1")["online"]
    assert {"node": "iran-a", "ip": "192.0.2.5", "connections": 1, "last_seen": 1793664233} in rows
    assert {"node": "18", "ip": "192.0.2.7", "connections": 3, "last_seen": None} in rows          # an older answer with a count keeps the count


def test_live_addresses_flag_a_shared_link_and_name_the_entrance_server():
    live = [{"node": "n", "ip": f"192.0.2.{i}", "ours": "", "last_seen": 1} for i in range(10, 13)]
    got = _codes({"user": _user(), "now": NOW, "online": live})
    assert "live_ips" in got and "live_tunnel" not in got
    via = [{"node": "n", "ip": "198.51.100.9", "ours": "iran-a", "last_seen": 1}] * 3                    # three connections of one entrance server are ONE address
    got = _codes({"user": _user(), "now": NOW, "online": via})
    assert "live_tunnel" in got and "live_ips" not in got
    text = inv.customer_text({"username": "demo-1", "used": GB, "limit": 0}, inv.judge({"user": _user(), "now": NOW, "online": live}))
    assert "3 آی‌پی" in text and "192.0.2" not in text


def test_investigate_marks_the_addresses_that_are_our_own_servers(monkeypatch):
    raw = {"id": 7, "username": "demo-1", "status": "active", "used_traffic": GB, "data_limit": 50 * GB, "created_at": "2026-10-30T10:00:00Z", "online_at": None, "hwid_limit": None}
    monkeypatch.setattr(pg_users, "_id_of", lambda c, n: 7)
    monkeypatch.setattr(pg_users, "_call", lambda c, m, p, *a, **k: raw)
    monkeypatch.setattr(pg_usage, "node_names", lambda c: {})
    monkeypatch.setattr(pg_usage, "_get", lambda c, path, t=30: {"stats": {}})
    monkeypatch.setattr(pg_usage, "user_usage", lambda c, n, p, **k: {"series": [], "by_node": []})
    monkeypatch.setattr(pg_users, "devices", lambda c, n: {"devices": [], "count": 0})
    monkeypatch.setattr(pg_users, "sub_updates", lambda c, n, o=0, l=10: {"updates": [], "count": 0})
    monkeypatch.setattr(pg_users, "online_ips", lambda c, n: {"online": [{"node": "n", "ip": "198.51.100.9", "connections": 1, "last_seen": 5}, {"node": "n", "ip": "203.0.113.4", "connections": 1, "last_seen": 6}], "count": 2})
    monkeypatch.setattr(inv, "_ours", lambda: {"198.51.100.9": "iran-a"})
    r = inv.investigate({"id": 1, "type": "pasarguard"}, "demo-1", now=NOW)
    assert [(x["ip"], x["ours"]) for x in r["online"]] == [("198.51.100.9", "iran-a"), ("203.0.113.4", "")]
    assert any(x["code"] == "live_tunnel" for x in r["findings"])


def test_cloudflare_edge_addresses_are_not_the_customer():
    edge = [str(n.network_address + 7) for n in inv.CF_NETS if n.version == 4]
    assert len(edge) == 15 and all(inv.is_cf(x) for x in edge) and inv.is_cf("2606:4700::1") and not inv.is_cf("203.0.113.9") and not inv.is_cf("not an address") and not inv.is_cf("")
    edges = [{"agent": "", "ip": ip} for ip in edge[:4]]
    got = _codes({"user": _user(), "now": NOW, "fetches": edges})
    assert "fetch_ips" not in got and "fetch_hidden" in got                                               # four different edges are not four customers
    mixed = edges + [{"agent": "appone/1", "ip": "203.0.113.9"}]
    assert "fetch_hidden" not in _codes({"user": _user(), "now": NOW, "fetches": mixed})                    # one real address among them: the address is known
    live = [{"node": "n", "ip": edge[i], "ours": "", "cf": True, "last_seen": 1} for i in range(4)]
    assert "live_ips" not in _codes({"user": _user(), "now": NOW, "online": live})                          # a CDN config: the node sees the CDN's addresses
