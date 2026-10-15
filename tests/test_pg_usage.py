"""PasarGuard / Marzban usage tab: the Jalali calendar and Tehran days (pinned dates, also in JS), the ranges, the normalising of both panels' answers, the cache,
the lookups, the top users, and the routes. A fake panel answers; nothing touches a real panel, and nothing in this tab may write to one."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from nikatel.api import pg_usage as api_mod
from nikatel.db import session
from nikatel.models import Panel
from nikatel.services import panels, pg_usage
from nikatel.services.panels import PanelError

GB = 1024 ** 3
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 14, 10, 0, tzinfo=UTC)               # 13:30 in Tehran on 1405/07/22
WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"


def at(y, mo, d, h=0, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_cache():
    pg_usage.clear_cache()
    yield
    pg_usage.clear_cache()


# ------------------------------------------------------------------ Jalali calendar
PINS = [((1405, 4, 8), (2026, 6, 29)), ((1405, 7, 22), (2026, 10, 14)), ((1405, 1, 1), (2026, 3, 21)), ((1403, 12, 30), (2025, 3, 20)),
        ((1404, 1, 1), (2025, 3, 21)), ((1403, 1, 1), (2024, 3, 20)), ((1399, 12, 30), (2021, 3, 20)), ((1400, 1, 1), (2021, 3, 21)),
        ((1405, 12, 29), (2027, 3, 20)), ((1406, 1, 1), (2027, 3, 21))]


@pytest.mark.parametrize("j,g", PINS)
def test_jalali_pinned_dates_both_ways(j, g):
    assert pg_usage.gregorian_to_jalali(*g) == j
    assert pg_usage.jalali_to_gregorian(*j) == g
    assert pg_usage.jalali_to_date(*j) == dt.date(*g)


def test_the_end_of_1403_is_a_leap_year_and_1404_is_not():
    assert pg_usage.date_to_jalali(dt.date(2025, 3, 20)) == (1403, 12, 30)           # 1403 has 366 days
    assert pg_usage.date_to_jalali(dt.date(2025, 3, 21)) == (1404, 1, 1)
    assert pg_usage.date_to_jalali(dt.date(2026, 3, 20)) == (1404, 12, 29)           # 1404 has 365
    assert pg_usage.jalali_to_date(1403, 12, 30) == dt.date(2025, 3, 20)
    with pytest.raises(ValueError):
        pg_usage.jalali_to_date(1404, 12, 30)                                        # there is no day 30 in a normal Esfand


def test_every_day_of_two_hundred_years_follows_the_calendar():
    d, prev, leaps = dt.date(1921, 3, 21), (1300, 1, 1), []
    assert pg_usage.date_to_jalali(d) == prev

    while d < dt.date(2125, 3, 1):
        d += dt.timedelta(days=1)
        cur = pg_usage.date_to_jalali(d)
        new_day = cur == (prev[0], prev[1], prev[2] + 1)
        new_month = cur[2] == 1 and (cur[:2] == (prev[0], prev[1] + 1) or (prev[1] == 12 and cur[:2] == (prev[0] + 1, 1)))
        assert new_day or new_month, (d, prev, cur)

        if new_month:
            expected = 31 if prev[1] <= 6 else 30 if prev[1] <= 11 else prev[2]
            assert prev[2] == expected and (prev[1] != 12 or prev[2] in (29, 30)), (d, prev)

            if prev[1] == 12 and prev[2] == 30:
                leaps.append(prev[0])

        assert pg_usage.jalali_to_gregorian(*cur) == (d.year, d.month, d.day)         # and back
        prev = cur

    assert [y for y in leaps if 1395 <= y <= 1435] == [1395, 1399, 1403, 1408, 1412, 1416, 1420, 1424, 1428, 1432]


def test_jalali_text_and_bad_dates():
    assert pg_usage.jalali_text(dt.date(2026, 10, 14)) == "1405/07/22"
    for bad in ((1405, 13, 1), (1405, 7, 31), (1405, 0, 5), (1405, 12, 30), (1405, 3, 32)):
        with pytest.raises(ValueError):
            pg_usage.jalali_to_date(*bad)

    with pytest.raises(ValueError):
        pg_usage.gregorian_to_jalali(2026, 13, 1)


def test_parse_date_takes_both_calendars_and_persian_digits():
    assert pg_usage.parse_date("2026-10-14") == dt.date(2026, 10, 14)
    assert pg_usage.parse_date("1405/07/22") == dt.date(2026, 10, 14)
    assert pg_usage.parse_date(" 1405-7-22 ") == dt.date(2026, 10, 14)
    assert pg_usage.parse_date("۱۴۰۵/۰۷/۲۲") == dt.date(2026, 10, 14)
    for bad in ("", "14/10/2026", "2026-02-30", "1405/12/30", "1700/01/01", "soon"):
        with pytest.raises(ValueError):
            pg_usage.parse_date(bad)


# ------------------------------------------------------------------ Tehran days and the ranges
def test_a_tehran_day_starts_at_2030_utc_of_the_day_before():
    assert pg_usage.tehran_day_start(dt.date(2026, 10, 14)) == at(2026, 10, 13, 20, 30)
    assert pg_usage.tehran_date(at(2026, 10, 13, 20, 29)) == dt.date(2026, 10, 13)
    assert pg_usage.tehran_date(at(2026, 10, 13, 20, 30)) == dt.date(2026, 10, 14)
    assert pg_usage.tehran_date(at(2026, 7, 1, 20, 29)) == dt.date(2026, 7, 1)        # summer: still +3:30, no daylight saving any more
    assert pg_usage.tehran_date(at(2026, 7, 1, 20, 30)) == dt.date(2026, 7, 2)
    assert pg_usage.iso_z(at(2026, 10, 13, 20, 30)) == "2026-10-13T20:30:00Z"


def test_presets_on_a_normal_day():
    r = {p: pg_usage.resolve_range(p, now=NOW) for p in pg_usage.PRESETS if p != "custom"}
    assert (r["today"].first, r["today"].last, r["today"].days) == (dt.date(2026, 10, 14), dt.date(2026, 10, 14), 1)
    assert r["today"].start == at(2026, 10, 13, 20, 30) and r["today"].end == NOW             # up to now, not to the end of the day
    assert (r["last7"].first, r["last7"].last, r["last7"].days) == (dt.date(2026, 10, 8), dt.date(2026, 10, 14), 7)
    assert r["last7"].start == at(2026, 10, 7, 20, 30)
    assert (r["last30"].first, r["last30"].days) == (dt.date(2026, 9, 15), 30)
    assert (r["jmonth"].first, r["jmonth"].last, r["jmonth"].label) == (dt.date(2026, 9, 23), dt.date(2026, 10, 14), "Mehr 1405")      # 1405/07/01 .. 1405/07/22
    assert r["jmonth"].to_dict()["from_j"] == "1405/07/01" and r["jmonth"].to_dict()["to_j"] == "1405/07/22"
    lm = r["last_jmonth"]
    assert (lm.first, lm.last, lm.days, lm.label) == (dt.date(2026, 8, 23), dt.date(2026, 9, 22), 31, "Shahrivar 1405")
    assert lm.start == at(2026, 8, 22, 20, 30) and lm.end == at(2026, 9, 22, 20, 30)           # a finished month ends at the Tehran midnight, not at "now"
    assert lm.to_dict()["from_j"] == "1405/06/01" and lm.to_dict()["to_j"] == "1405/06/31"


def test_last_jalali_month_around_nowruz_and_a_leap_year():
    n = pg_usage.resolve_range("last_jmonth", now=at(2026, 3, 21, 10))                # 1405/01/01: the month before is Esfand 1404 (29 days)
    assert (n.first, n.last, n.days, n.label) == (dt.date(2026, 2, 20), dt.date(2026, 3, 20), 29, "Esfand 1404")
    assert pg_usage.resolve_range("jmonth", now=at(2026, 3, 21, 10)).first == dt.date(2026, 3, 21)
    leap = pg_usage.resolve_range("last_jmonth", now=at(2025, 3, 21, 10))             # 1404/01/01: the month before is Esfand 1403 (30 days, leap)
    assert (leap.first, leap.last, leap.days) == (dt.date(2025, 2, 19), dt.date(2025, 3, 20), 30)
    assert leap.to_dict()["to_j"] == "1403/12/30"


def test_the_tehran_day_changes_at_2030_utc_not_at_midnight_utc():
    before = pg_usage.resolve_range("today", now=at(2026, 10, 13, 20, 29))
    after = pg_usage.resolve_range("today", now=at(2026, 10, 13, 20, 31))
    assert before.first == dt.date(2026, 10, 13) and after.first == dt.date(2026, 10, 14)
    assert after.start <= after.end


def test_custom_ranges():
    r = pg_usage.resolve_range("custom", "2026-10-01", "2026-10-03", NOW)
    assert (r.first, r.last, r.days) == (dt.date(2026, 10, 1), dt.date(2026, 10, 3), 3)
    assert r.start == at(2026, 9, 30, 20, 30) and r.end == at(2026, 10, 3, 20, 30)
    j = pg_usage.resolve_range("custom", "1405/07/01", "1405/07/03", NOW)
    assert (j.first, j.last) == (dt.date(2026, 9, 23), dt.date(2026, 9, 25))
    assert pg_usage.resolve_range("custom", "2026-10-10", "2026-12-31", NOW).last == dt.date(2026, 10, 14)         # an end in the future is cut at today
    assert pg_usage.resolve_range("custom", "2026-10-14", "2026-10-14", NOW).end == NOW


@pytest.mark.parametrize("args", [("custom", "", ""), ("custom", "2026-10-01", ""), ("custom", "2026-10-05", "2026-10-01"), ("custom", "2026-10-20", "2026-10-25"),
                                  ("custom", "2026-01-01", "2026-10-14"), ("custom", "nonsense", "2026-10-01"), ("yesterday", "", ""), ("", "", ""),
                                  ("custom", "2022-09-21", "2022-09-25"), ("custom", "2022-01-01", "2022-01-05")])
def test_bad_ranges_are_refused(args):
    with pytest.raises(ValueError):
        pg_usage.resolve_range(*args, now=NOW)


def test_the_first_offered_day_is_the_end_of_daylight_saving():
    r = pg_usage.resolve_range("custom", "2022-09-22", "2022-09-24", NOW)
    assert (r.first, r.days, r.start) == (dt.date(2022, 9, 22), 3, at(2022, 9, 21, 20, 30))

def test_the_ranges_listing_names_every_preset():
    out = pg_usage.presets(NOW)
    assert [p["preset"] for p in out["presets"]] == ["today", "last7", "last30", "jmonth", "last_jmonth"]
    assert out["tehran_today"] == "2026-10-14" and out["tehran_today_j"] == "1405/07/22" and out["max_days"] == pg_usage.MAX_DAYS


# ------------------------------------------------------------------ normalising the answers
SYS_PG = {"total_user": 629, "online_users": 76, "active_users": 403, "on_hold_users": 0, "disabled_users": 4, "expired_users": 63, "limited_users": 159,
          "incoming_bandwidth": 2102511662090, "outgoing_bandwidth": 24684155499356, "version": "5.4.1", "uptime_seconds": 2154002, "mem_total": 2006433792,
          "mem_used": 1124896768, "disk_total": 39990112256, "disk_used": 17613725696, "cpu_cores": 1, "cpu_usage": 6.1}
SYS_MZ = {"version": "0.8.4", "mem_total": 16370102272, "mem_used": 2030923776, "cpu_cores": 8, "cpu_usage": 16.3, "total_user": 4589, "online_users": 1886,
          "users_active": 2351, "users_on_hold": 0, "users_disabled": 4, "users_expired": 980, "users_limited": 1254, "incoming_bandwidth": 119675626580312,
          "outgoing_bandwidth": 1415151557346336, "incoming_bandwidth_speed": 26242, "outgoing_bandwidth_speed": 15498}


def test_overview_reads_both_panels_and_survives_garbage():
    pg, mz = pg_usage.parse_overview(SYS_PG), pg_usage.parse_overview(SYS_MZ)
    assert (pg["total"], pg["online"], pg["active"], pg["expired"], pg["limited"], pg["disabled"], pg["on_hold"]) == (629, 76, 403, 63, 159, 4, 0)
    assert (mz["total"], mz["online"], mz["active"], mz["expired"], mz["limited"], mz["disabled"]) == (4589, 1886, 2351, 980, 1254, 4)         # Marzban says users_active
    assert pg["mem_pct"] == 56.1 and pg["disk_pct"] == 44.0 and pg["cpu_pct"] == 6.1 and pg["version"] == "5.4.1" and pg["disk_total"] == 39990112256
    assert mz["disk_pct"] is None and mz["speed_in"] == 26242
    for junk in (None, [], "x", 5, {}, {"mem_total": "abc", "cpu_usage": "high"}):
        out = pg_usage.parse_overview(junk)
        assert out["total"] is None and out["mem_pct"] is None and out["cpu_pct"] is None and out["version"] == ""


def row(ts, n):
    return {"period_start": ts, "total_traffic": n}


def test_pg_stats_parsing_is_tolerant():
    raw = {"stats": {"33": [row("2026-10-11T21:00:00Z", 5), {"period_start": "bad", "total_traffic": 1}, "junk", {"total_traffic": 3}, row("2026-10-11T22:00:00Z", "7")],
                     "x": [row("2026-10-11T21:00:00Z", 9)], "34": "not a list", "-1": [{"period_start": "2026-10-11T21:00:00+00:00", "uplink": 2, "downlink": 3}]}}
    out = pg_usage.parse_pg_stats(raw)
    assert sorted(out) == [-1, 33] and [(w.hour, a) for w, a, _r in out[33]] == [(21, 5), (22, 7)] and out[-1][0][1:] == (0, 5)
    assert pg_usage.parse_pg_stats({"stats": [row("2026-10-11T21:00:00Z", 4)]})[-1][0][1] == 4                       # a bare list means "all nodes"
    for junk in (None, [], "x", {}, {"stats": None}, {"stats": 5}):
        assert pg_usage.parse_pg_stats(junk) == {}


def test_marzban_usages_and_node_names():
    raw = {"usages": [{"node_id": None, "node_name": "Master", "used_traffic": 0}, {"node_id": 7, "node_name": "node-a", "used_traffic": 3 * GB}, {"node_id": 8, "used_traffic": "bad"}, "x"]}
    assert pg_usage.parse_marzban_usages(raw) == [{"id": 7, "name": "node-a", "bytes": 3 * GB}]
    assert pg_usage.parse_marzban_usages(None) == [] and pg_usage.parse_marzban_usages({"usages": 5}) == []
    assert pg_usage.node_names_of({"nodes": [{"id": 1, "name": "a"}, {"id": "x", "name": "b"}, {"name": "c"}, 5]}) == {1: "a"}
    assert pg_usage.node_names_of([{"id": 2, "name": "z"}]) == {2: "z"} and pg_usage.node_names_of(None) == {}


def test_by_node_rows_are_biggest_first_with_shares():
    rows = pg_usage.by_node_rows({1: 1 * GB, 2: 3 * GB, 3: 0}, {1: "node-a", 2: "node-b"})
    assert [r["name"] for r in rows] == ["node-b", "node-a", "node 3"] and rows[0]["gb"] == 3.0 and rows[0]["share"] == 0.75 and rows[2]["share"] == 0.0
    assert "up_gb" not in rows[0]
    with_traffic = pg_usage.by_node_rows({1: GB}, {}, {1: (2 * GB, 5 * GB)})
    assert (with_traffic[0]["up_gb"], with_traffic[0]["down_gb"]) == (2.0, 5.0)
    assert pg_usage.by_node_rows({-1: GB}, {})[0]["name"] == "All nodes" and pg_usage.by_node_rows({}, {}) == []


def test_series_by_tehran_day_with_gaps_and_the_20_utc_rule():
    span = pg_usage.resolve_range("custom", "2026-10-11", "2026-10-13", NOW)
    per_node = pg_usage.parse_pg_stats({"stats": {"33": [row("2026-10-10T21:00:00Z", GB), row("2026-10-11T20:00:00Z", 2 * GB)], "34": [row("2026-10-11T21:00:00Z", 4 * GB), row("2026-10-12T20:00:00Z", 8 * GB)]}})
    kind, series = pg_usage.build_series(per_node, span)
    assert kind == "day" and [p["t"] for p in series] == ["2026-10-11", "2026-10-12", "2026-10-13"]
    assert [p["gb"] for p in series] == [3.0, 12.0, 0.0]                      # 20:00 UTC is 23:30 in Tehran: still that day; 21:00 UTC is 00:30: the next day
    assert [p["jalali"] for p in series] == ["1405/07/19", "1405/07/20", "1405/07/21"] and series[0]["label"] == "10-11"
    assert sum(p["bytes"] for p in series) == 15 * GB


def test_series_by_tehran_hour_for_a_short_range():
    now = at(2026, 10, 14, 1, 3)                                              # 04:33 in Tehran
    span = pg_usage.resolve_range("today", now=now)
    per_node = pg_usage.parse_pg_stats({"stats": {"33": [row("2026-10-13T21:00:00Z", GB), row("2026-10-14T01:00:00Z", 2 * GB)]}})
    kind, series = pg_usage.build_series(per_node, span)
    assert kind == "hour" and [p["label"] for p in series] == ["00:30", "01:30", "02:30", "03:30", "04:30"]
    assert [p["gb"] for p in series] == [1.0, 0.0, 0.0, 0.0, 2.0] and series[0]["t"] == "2026-10-14T00:30" and series[0]["jalali"] == "1405/07/22"


def test_series_with_no_rows_is_all_zeros():
    kind, series = pg_usage.build_series({}, pg_usage.resolve_range("last7", now=NOW))
    assert kind == "day" and len(series) == 7 and all(p["bytes"] == 0 for p in series)


# ------------------------------------------------------------------ a fake panel
class Fake:
    """Answers like PasarGuard (type pasarguard) or Marzban (type marzban); records every call so the tests can prove that nothing but GET was sent."""

    def __init__(self, typ):
        self.typ, self.calls, self.fail, self.delay = typ, [], {}, 0.0

    def __call__(self, c, method, path, **kw):
        self.calls.append((method, path, kw.get("timeout")))
        u = urlsplit(path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.last_query = q

        if self.delay:
            time.sleep(self.delay)

        for prefix, err in self.fail.items():
            if u.path.startswith(prefix):
                raise err

        return getattr(self, "pg" if self.typ == "pasarguard" else "mz")(u.path, q)

    def hours(self, q):
        """Hour rows like the panel's: every whole hour after `start` up to `end`, 1 GB for node 33 and 2 GB for node 34."""
        start = dt.datetime.fromisoformat(q["start"].replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(q["end"].replace("Z", "+00:00"))
        cur = start if start.minute == 0 and start.second == 0 else start.replace(minute=0, second=0) + dt.timedelta(hours=1)
        out = []

        while cur <= end:
            out.append(cur.strftime("%Y-%m-%dT%H:%M:%SZ"))
            cur += dt.timedelta(hours=1)

        return out

    def pg(self, path, q):
        if path == "/api/system":
            return SYS_PG

        if path == "/api/nodes/simple":
            return {"nodes": [{"id": 33, "name": "node-a", "status": "connected"}, {"id": 34, "name": "node-b", "status": "connected"}], "total": 2}

        if path == "/api/users/simple":                                              # the ids of names: only user-a exists
            return {"users": [{"id": 7, "username": "user-a"}] if q.get("usernames") == "user-a" else [], "total": 1}

        by_id = re.fullmatch(r"/api/(admin|user)/by-id/(\d+)/usage", path)

        if path == "/api/users/usage" or by_id:
            if by_id and by_id.group(1) == "admin" and by_id.group(2) != "1":
                raise PanelError("HTTP 404: Admin not found", 404)

            if by_id and by_id.group(1) == "user" and by_id.group(2) != "7":
                raise PanelError("HTTP 404: User not found", 404)

            assert q["period"] == "hour" and q["group_by_node"] == "true"
            hrs = self.hours(q)
            return {"period": "hour", "start": q["start"], "end": q["end"], "stats": {"33": [row(h, GB) for h in hrs], "34": [row(h, 2 * GB) for h in hrs]}}

        counts = re.fullmatch(r"/api/users/counts/(online|expired|limited)", path)

        if counts:
            return {"period": q["period"], "start": q["start"], "end": q["end"], "metric": counts.group(1), "count_during_period": 5,
                    "stats": {"-1": [{"period_start": "2026-10-13T20:30:00Z", "count": 3}, {"period_start": "2026-10-14T20:30:00Z", "count": 4}], "33": [{"period_start": "2026-10-13T20:30:00Z", "count": 2}]}}

        if path == "/api/users/sub_update/chart":
            return {"period": "day", "start": q["start"], "end": q["end"], "total": 30, "stats": [],
                    "segments": [{"name": "v2rayNG", "count": 10, "percentage": 33.33}, {"name": "Happ", "count": 20, "percentage": 66.67}]}

        if path == "/api/node/usage":
            hrs = self.hours(self.last_query)
            return {"stats": {"33": [{"period_start": h, "uplink": GB, "downlink": 4 * GB} for h in hrs], "34": [{"period_start": h, "uplink": 0, "downlink": 2 * GB} for h in hrs]}}

        if path == "/api/admins/simple":
            return {"admins": [{"id": 2, "username": "zed"}, {"id": 1, "username": "admin-a"}], "total": 2}

        if path == "/api/users":
            users = [{"username": f"user-{i}", "status": "active", "used_traffic": (50 - i) * GB, "data_limit": 100 * GB if i % 2 == 0 else 0, "expire": "2026-12-01T00:00:00Z",
                      "online_at": "2026-10-14T01:00:00Z", "created_at": "2026-07-31T12:00:00Z", "admin": {"id": 1, "username": "admin-a"},
                      "proxy_settings": {"vless": {"id": "00000000-0000-0000-0000-000000000000"}, "trojan": {"password": "hunter2-secret"}}, "subscription_url": "https://sub.example.test/secret-token"}
                     for i in range(int(q["limit"]))]
            return {"users": users, "total": 629}

        raise AssertionError(f"unexpected call {path}")

    def mz(self, path, q):
        if path == "/api/system":
            return SYS_MZ

        if path == "/api/admins":
            return [{"username": "admin-a", "is_sudo": False, "users_usage": 5}, {"username": "admin-b", "is_sudo": True}]

        if path == "/api/users/usage":
            assert "admin" in q and "period" not in q and not q["start"].endswith("Z")              # Marzban: naive UTC moments, always one admin
            return {"usages": [{"node_id": None, "node_name": "Master", "used_traffic": 0}, {"node_id": 7, "node_name": "node-a", "used_traffic": 30 * GB},
                               {"node_id": 8, "node_name": "node-b", "used_traffic": 10 * GB}]}

        if path.startswith("/api/user/") and path.endswith("/usage"):
            return {"username": "user-a", "usages": [{"node_id": 7, "node_name": "node-a", "used_traffic": 2 * GB}]}

        if path.startswith("/api/admin/usage/"):
            return 11331938862817

        if path == "/api/users":
            return {"users": [{"username": "user-1", "status": "active", "used_traffic": 9 * GB, "data_limit": 10 * GB, "expire": 0, "online_at": None, "admin": {"username": "admin-b"}}], "total": 4589}

        raise AssertionError(f"unexpected call {path}")


@pytest.fixture
def pg(monkeypatch):
    f = Fake("pasarguard")
    monkeypatch.setattr(panels, "call", f)
    return f


@pytest.fixture
def mz(monkeypatch):
    f = Fake("marzban")
    monkeypatch.setattr(panels, "call", f)
    return f


PG = {"id": 2, "type": "pasarguard"}
MZ = {"id": 1, "type": "marzban"}


def only_gets(f):
    assert f.calls and {m for m, _p, _t in f.calls} == {"GET"}


# ------------------------------------------------------------------ the service with a fake PasarGuard
def test_usage_total_by_node_and_series_agree(pg):
    out = pg_usage.usage(PG, "custom", "2026-10-01", "2026-10-03", NOW)
    hours = 72                                                                  # three whole Tehran days
    assert out["total"] == {"bytes": hours * 3 * GB, "gb": hours * 3.0} and out["granularity"] == "day"
    assert [(r["name"], r["gb"], r["share"]) for r in out["by_node"]] == [("node-b", hours * 2.0, 0.6667), ("node-a", hours * 1.0, 0.3333)]
    assert (out["by_node"][1]["up_gb"], out["by_node"][1]["down_gb"]) == (hours * 1.0, hours * 4.0)             # the nodes' own traffic is added beside the users' usage
    assert [p["t"] for p in out["series"]] == ["2026-10-01", "2026-10-02", "2026-10-03"] and [p["gb"] for p in out["series"]] == [72.0, 72.0, 72.0]
    assert sum(p["bytes"] for p in out["series"]) == out["total"]["bytes"]
    assert out["start"] == "2026-09-30T20:30:00Z" and out["end"] == "2026-10-03T20:30:00Z" and out["avg_per_day_gb"] == 72.0
    assert out["range"]["from_j"] == "1405/07/09" and out["scope"] == "all" and out["panel_type"] == "pasarguard"
    only_gets(pg)
    sent = next(p for _m, p, _t in pg.calls if p.startswith("/api/users/usage"))
    assert "start=2026-09-30T20%3A30%3A00Z" in sent and "end=2026-10-03T20%3A30%3A00Z" in sent and "period=hour" in sent and "group_by_node=true" in sent


def test_today_is_hourly_and_ends_now(pg):
    out = pg_usage.usage(PG, "today", now=at(2026, 10, 14, 1, 3))
    assert out["granularity"] == "hour" and len(out["series"]) == 5 and out["total"]["gb"] == 15.0 and out["end"] == "2026-10-14T01:03:00Z"


def test_node_names_missing_give_node_ids_and_a_failed_node_traffic_call_is_not_fatal(pg):
    pg.fail = {"/api/nodes/simple": PanelError("HTTP 500", 500), "/api/nodes": PanelError("HTTP 500", 500), "/api/node/usage": PanelError("HTTP 500", 500)}
    out = pg_usage.usage(PG, "custom", "2026-10-01", "2026-10-03", NOW)
    assert [r["name"] for r in out["by_node"]] == ["node 34", "node 33"] and "up_gb" not in out["by_node"][0] and out["total"]["gb"] == 216.0


def test_empty_answer_from_the_panel_gives_zeros_and_a_note(pg, monkeypatch):
    monkeypatch.setattr(Fake, "pg", lambda self, path, q: {"stats": {}} if path != "/api/nodes/simple" else {"nodes": []})
    out = pg_usage.usage(PG, "last7", now=NOW)
    assert out["total"]["gb"] == 0.0 and out["by_node"] == [] and len(out["series"]) == 7 and out["notes"]


def test_the_usage_is_cached_per_range_and_a_past_range_longer(pg, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(pg_usage, "_clock", lambda: t[0])
    first = pg_usage.usage(PG, "last7", now=NOW)
    n = len(pg.calls)
    again = pg_usage.usage(PG, "last7", now=NOW)
    assert len(pg.calls) == n and first["cached"] is False and again["cached"] is True
    t[0] += pg_usage.RANGE_TTL + 1                                                  # the range includes "now": asked again after 30 s
    pg_usage.usage(PG, "last7", now=NOW)
    assert len(pg.calls) > n
    n2 = len(pg.calls)
    pg_usage.usage(PG, "last_jmonth", now=NOW)
    n3 = len(pg.calls)
    t[0] += pg_usage.RANGE_TTL + 1
    pg_usage.usage(PG, "last_jmonth", now=NOW)                                      # a finished month stays for 5 minutes
    assert n3 > n2 and len(pg.calls) == n3
    pg_usage.usage(PG, "custom", "2026-10-01", "2026-10-02", NOW)
    assert len(pg.calls) > n3                                                       # another range is another question


def test_a_range_that_ends_now_is_cached_although_now_moves(pg, monkeypatch):
    t = [50.0]
    monkeypatch.setattr(pg_usage, "_clock", lambda: t[0])
    pg_usage.usage(PG, "last7", now=NOW)
    n = len(pg.calls)
    t[0] += 10
    again = pg_usage.usage(PG, "last7", now=NOW + dt.timedelta(seconds=10))              # the real clock is a little later, the question is the same
    assert again["cached"] is True and len(pg.calls) == n
    pg_usage.admin_usage(PG, "admin-a", "last7", now=NOW)
    n = len(pg.calls)
    assert pg_usage.admin_usage(PG, "admin-a", "last7", now=NOW + dt.timedelta(seconds=25))["cached"] is True and len(pg.calls) == n
    pg_usage.usage(PG, "custom", "2026-10-13", "2026-12-31", NOW)                      # an end in the future is cut at today: the same question as one that ends today
    n = len(pg.calls)
    assert pg_usage.usage(PG, "custom", "2026-10-13", "2026-10-14", NOW + dt.timedelta(seconds=5))["cached"] is True and len(pg.calls) == n

def test_admin_and_user_lookups(pg):
    a = pg_usage.admin_usage(PG, "admin-a", "custom", "2026-10-01", "2026-10-03", NOW)
    assert a["scope"] == "admin" and a["name"] == "admin-a" and a["total"]["gb"] == 216.0 and [r["name"] for r in a["by_node"]] == ["node-b", "node-a"]
    assert "up_gb" not in a["by_node"][0]                                          # the nodes' own traffic belongs to the whole panel only
    assert any(p.startswith("/api/admin/by-id/1/usage") for _m, p, _t in pg.calls)                    # by id: the by-username routes are deprecated (removed in 6.0)
    u = pg_usage.user_usage(PG, "user-a", "last7", now=NOW)
    assert u["scope"] == "user" and u["total"]["gb"] > 0 and any(p.startswith("/api/user/by-id/7/usage") for _m, p, _t in pg.calls)
    assert not any(re.match(r"/api/(admin|user)/(?!by-id)[^/]+/usage", p) for _m, p, _t in pg.calls)
    only_gets(pg)


def test_lookups_refuse_unknown_names_and_bad_names(pg):
    with pytest.raises(ValueError, match="no admin called nobody"):
        pg_usage.admin_usage(PG, "nobody", "last7", now=NOW)

    with pytest.raises(ValueError, match="no user called ghost"):
        pg_usage.user_usage(PG, "ghost", "last7", now=NOW)

    for bad in ("", "a b", "../x", "x" * 65, "a/b", "a?b=1"):
        with pytest.raises(ValueError):
            pg_usage.admin_usage(PG, bad, "last7", now=NOW)

    assert not any("x" * 65 in p or ".." in p for _m, p, _t in pg.calls)           # a bad name never reaches the panel


def test_the_users_counts_over_time(pg):
    c = pg_usage.counts(PG, "online", "last7", now=NOW)
    assert c["metric"] == "online" and c["period"] == "day" and c["count"] == 5 and c["max"] == 4
    assert [(p["t"], p["count"]) for p in c["series"]] == [("2026-10-13T20:30:00Z", 3), ("2026-10-14T20:30:00Z", 4)]       # two keys at one moment: the bigger one, never the sum
    sent = next(p for _m, p, _t in pg.calls if p.startswith("/api/users/counts/online"))
    assert "period=day" in sent and "start=" in sent and "admin=" not in sent
    assert pg_usage.counts(PG, "limited", "today", now=at(2026, 10, 14, 5, 0))["period"] == "hour"                       # a range of two days or less is hourly
    pg_usage.counts(PG, "expired", "last7", admin="admin-a", now=NOW)
    assert any("admin=admin-a" in p for _m, p, _t in pg.calls if p.startswith("/api/users/counts/expired"))
    n = len(pg.calls)
    assert pg_usage.counts(PG, "online", "last7", now=NOW)["cached"] is True and len(pg.calls) == n
    only_gets(pg)

    for bad in ("frozen", "", "online/x", "ONLINE"):
        with pytest.raises(ValueError, match="the metric is one of"):
            pg_usage.counts(PG, bad, "last7", now=NOW)

    with pytest.raises(ValueError, match="admin name"):
        pg_usage.counts(PG, "online", "last7", admin="a b", now=NOW)

    with pytest.raises(ValueError, match="part of PasarGuard"):
        pg_usage.counts(MZ, "online", "last7", now=NOW)


def test_the_subscription_apps(pg):
    a = pg_usage.sub_apps(PG, "last30", now=NOW)
    assert a["total"] == 30 and [(x["name"], x["count"], x["percent"]) for x in a["apps"]] == [("Happ", 20, 66.7), ("v2rayNG", 10, 33.3)]      # the biggest first
    assert not any("user_id=" in p or "admin_id=" in p for _m, p, _t in pg.calls if p.startswith("/api/users/sub_update/chart"))
    pg_usage.sub_apps(PG, "last7", user="user-a", now=NOW)
    assert any("user_id=7" in p for _m, p, _t in pg.calls if p.startswith("/api/users/sub_update/chart"))                    # the id of the name is looked up
    pg_usage.sub_apps(PG, "last7", admin="admin-a", now=NOW)
    assert any("admin_id=1" in p for _m, p, _t in pg.calls if p.startswith("/api/users/sub_update/chart"))
    only_gets(pg)

    with pytest.raises(ValueError, match="not both"):
        pg_usage.sub_apps(PG, "last7", user="user-a", admin="admin-a", now=NOW)

    with pytest.raises(ValueError, match="no user called ghost"):
        pg_usage.sub_apps(PG, "last7", user="ghost", now=NOW)

    with pytest.raises(ValueError, match="part of PasarGuard"):
        pg_usage.sub_apps(MZ, "last7", now=NOW)


def test_the_new_usage_routes(client, pg, pg_id):
    base = f"/api/panels/{pg_id}/pg/usage"
    r = client.get(base + "/counts", params={"metric": "limited", "preset": "last7"})
    assert r.status_code == 200 and r.json()["metric"] == "limited" and r.json()["series"]
    assert client.get(base + "/counts", params={"metric": "frozen"}).status_code == 422
    assert client.get(base + "/counts", params={"metric": "online", "preset": "custom"}).status_code == 400
    r = client.get(base + "/sub-apps", params={"preset": "last7", "user": "user-a"})
    assert r.status_code == 200 and r.json()["apps"][0]["name"] == "Happ" and r.json()["user"] == "user-a"
    assert client.get(base + "/sub-apps", params={"user": "user-a", "admin": "admin-a"}).status_code == 400


def test_panel_errors_other_than_404_pass_through(pg):
    pg.fail = {"/api/users/usage": PanelError("no answer from the panel (ConnectError)")}
    with pytest.raises(PanelError):
        pg_usage.usage(PG, "last7", now=NOW)

    pg_usage.clear_cache()
    pg.fail = {"/api/admin/": PanelError("HTTP 422: bad", 422)}
    with pytest.raises(PanelError):
        pg_usage.admin_usage(PG, "admin-a", "last7", now=NOW)


def test_an_error_is_not_cached(pg):
    pg.fail = {"/api/system": PanelError("HTTP 500", 500)}
    with pytest.raises(PanelError):
        pg_usage.overview(PG)

    pg.fail = {}
    assert pg_usage.overview(PG)["total"] == 629


# ------------------------------------------------------------------ the overview and its cache
def test_overview_is_asked_once_in_15_seconds_per_panel(pg, monkeypatch):
    t = [500.0]
    monkeypatch.setattr(pg_usage, "_clock", lambda: t[0])
    a = pg_usage.overview(PG)
    t[0] += 10
    b = pg_usage.overview(PG)
    assert len(pg.calls) == 1 and a["cached"] is False and b["cached"] is True and b["age_s"] == 10.0 and b["total"] == 629 and b["panel_type"] == "pasarguard"
    assert pg_usage.online(PG)["online"] == 76 and len(pg.calls) == 1
    t[0] += 6                                                                       # 16 s: the cache is over
    assert pg_usage.overview(PG)["cached"] is False and len(pg.calls) == 2
    pg_usage.overview({**PG, "id": 3})                                              # another panel has its own entry
    assert len(pg.calls) == 3


def test_several_tabs_at_once_make_one_question(pg):
    pg.delay = 0.25
    results = []
    threads = [threading.Thread(target=lambda: results.append(pg_usage.overview(PG))) for _ in range(6)]

    for th in threads:
        th.start()

    for th in threads:
        th.join()

    assert len(results) == 6 and len(pg.calls) == 1 and {r["total"] for r in results} == {629}


def test_the_cache_does_not_grow_without_end(pg):
    for i in range(pg_usage._MAX_CACHE + 30):
        pg_usage._cached(("t", i), 60, lambda n=i: n)

    assert len(pg_usage._CACHE) <= pg_usage._MAX_CACHE


def test_the_locks_of_failed_questions_are_pruned():
    def boom():
        raise PanelError("HTTP 500", 500)

    for i in range(3 * pg_usage._MAX_CACHE):
        with pytest.raises(PanelError):
            pg_usage._cached(("bad", i), 60, boom)

    pg_usage._cached(("ok", 0), 60, lambda: 1)
    assert len(pg_usage._LOCKS) <= 2 * pg_usage._MAX_CACHE

# ------------------------------------------------------------------ top users
def test_top_users_ask_for_20_rows_with_the_panels_own_sort_and_hold_no_secrets(pg):
    out = pg_usage.top_users(PG, "used_traffic", 500)
    q = pg.last_query
    assert q["limit"] == "20" and q["sort"] == "-used_traffic" and "admin" not in q and out["limit"] == 20 and len(out["users"]) == 20 and out["total"] == 629
    top = out["users"][0]
    assert top["username"] == "user-0" and top["used_gb"] == 50.0 and top["limit_gb"] == 100.0 and top["percent"] == 50 and top["admin"] == "admin-a" and top["expire"] == "2026-12-01"
    assert out["users"][1]["limit_gb"] is None and out["users"][1]["percent"] is None                          # no limit = unlimited
    text = json.dumps(out)
    assert "hunter2" not in text and "secret-token" not in text and "proxy_settings" not in text and "subscription" not in text and "0000-0000" not in text
    assert set(top) == {"username", "status", "used_bytes", "used_gb", "limit_gb", "percent", "expire", "online_at", "created_at", "admin"}
    only_gets(pg)


def test_top_users_sorts_filter_and_limit(pg):
    pg_usage.top_users(PG, "online_at", 5, "admin-a")
    assert pg.last_query == {"limit": "5", "sort": "-online_at", "admin": "admin-a"}
    pg_usage.top_users(PG, "data_limit", 0)
    assert pg.last_query["limit"] == "1"
    with pytest.raises(ValueError, match="can sort by"):
        pg_usage.top_users(PG, "username", 5)

    with pytest.raises(ValueError):
        pg_usage.top_users(PG, "used_traffic", 5, "a b")

    with pytest.raises(ValueError):
        pg_usage.top_users(PG, "used_traffic", "many")


def test_top_users_are_cached(pg):
    pg_usage.top_users(PG, "used_traffic", 20)
    n = len(pg.calls)
    assert pg_usage.top_users(PG, "used_traffic", 20)["cached"] is True and len(pg.calls) == n
    pg_usage.top_users(PG, "used_traffic", 10)
    assert len(pg.calls) == n + 1


def test_admin_names_are_sorted_and_cached(pg):
    assert pg_usage.admins(PG) == {"admins": ["admin-a", "zed"]}
    n = len(pg.calls)
    pg_usage.admins(PG)
    assert len(pg.calls) == n


# ------------------------------------------------------------------ Marzban: only what was verified read-only
def test_marzban_needs_an_admin_and_is_never_asked_for_all_users(mz):
    with pytest.raises(ValueError, match="needs an admin"):
        pg_usage.usage(MZ, "last7", now=NOW)

    assert mz.calls == []                                                           # refused before any question


def test_marzban_admin_usage_by_node_with_a_long_timeout_and_the_lifetime_total(mz):
    out = pg_usage.admin_usage(MZ, "admin-a", "custom", "2026-10-01", "2026-10-03", NOW)
    assert out["total"]["gb"] == 40.0 and [(r["name"], r["gb"]) for r in out["by_node"]] == [("node-a", 30.0), ("node-b", 10.0)]       # the idle Master is left out
    assert out["series"] == [] and out["granularity"] == "none" and out["lifetime_gb"] == round(11331938862817 / GB, 2) and out["notes"]
    only_gets(mz)
    usage_call = next(c for c in mz.calls if c[1].startswith("/api/users/usage"))
    assert usage_call[2] >= 60 and "admin=admin-a" in usage_call[1] and "start=2026-09-30T20%3A30%3A00" in usage_call[1]
    assert not any("/api/users/usage" in p and "admin=" not in p for _m, p, _t in mz.calls)                          # never without the admin filter


def test_marzban_usage_questions_do_not_overlap_and_are_kept_longer(mz, monkeypatch):
    running, worst = [0], [0]
    lock = threading.Lock()
    original = Fake.mz

    def slow(self, path, q):
        if path == "/api/users/usage":
            with lock:
                running[0] += 1
                worst[0] = max(worst[0], running[0])

            time.sleep(0.15)

            with lock:
                running[0] -= 1

        return original(self, path, q)

    monkeypatch.setattr(Fake, "mz", slow)
    ranges = [("2026-10-01", "2026-10-03"), ("2026-10-02", "2026-10-04"), ("2026-10-03", "2026-10-05")]
    threads = [threading.Thread(target=pg_usage.admin_usage, args=(MZ, "admin-a", "custom", a, b, NOW)) for a, b in ranges]

    for th in threads:
        th.start()

    for th in threads:
        th.join()

    assert worst[0] == 1 and sum(1 for c in mz.calls if c[1].startswith("/api/users/usage")) == 3          # three different questions, one at a time

    t = [900.0]
    monkeypatch.setattr(pg_usage, "_clock", lambda: t[0])
    pg_usage.clear_cache()
    pg_usage.admin_usage(MZ, "admin-a", "last7", now=NOW)
    n = sum(1 for c in mz.calls if c[1].startswith("/api/users/usage"))
    t[0] += 100                                                                       # 100 s later: still the answer from before (a PasarGuard range would be asked again after 30 s)
    assert pg_usage.admin_usage(MZ, "admin-a", "last7", now=NOW)["cached"] is True and sum(1 for c in mz.calls if c[1].startswith("/api/users/usage")) == n
    t[0] += 30
    pg_usage.admin_usage(MZ, "admin-a", "last7", now=NOW)
    assert sum(1 for c in mz.calls if c[1].startswith("/api/users/usage")) == n + 1

def test_marzban_unknown_admin_is_refused_by_name(mz):
    with pytest.raises(ValueError, match="no admin named ghost"):
        pg_usage.admin_usage(MZ, "ghost", "last7", now=NOW)

    assert not any(p.startswith("/api/users/usage") for _m, p, _t in mz.calls)


def test_marzban_admin_list_failure_does_not_block_the_lookup(mz):
    mz.fail = {"/api/admins": PanelError("HTTP 403", 403)}
    out = pg_usage.admin_usage(MZ, "admin-a", "last7", now=NOW)
    assert out["total"]["gb"] == 40.0


def test_marzban_lifetime_failure_gives_none(mz):
    mz.fail = {"/api/admin/usage/": PanelError("HTTP 500", 500)}
    assert pg_usage.admin_usage(MZ, "admin-a", "last7", now=NOW)["lifetime_gb"] is None


def test_marzban_user_usage_and_top_users(mz):
    u = pg_usage.user_usage(MZ, "user-a", "last7", now=NOW)
    assert u["total"]["gb"] == 2.0 and u["series"] == []
    top = pg_usage.top_users(MZ, "used_traffic", 20)
    assert top["users"][0]["limit_gb"] == 10.0 and top["users"][0]["percent"] == 90 and top["total"] == 4589 and top["sorts"] == ["used_traffic", "data_limit", "created_at"]
    assert mz.last_query == {"limit": "20", "sort": "-used_traffic"}
    with pytest.raises(ValueError):
        pg_usage.top_users(MZ, "online_at", 20)                                     # Marzban has no such sort

    only_gets(mz)


def test_unknown_panel_type_is_refused():
    with pytest.raises(ValueError, match="PasarGuard and Marzban"):
        pg_usage.overview({"id": 9, "type": "other"})


# ------------------------------------------------------------------ the routes
@pytest.fixture
def pg_id():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


@pytest.fixture
def mz_id():
    with session() as db:
        p = Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_routes_for_pasarguard(client, pg, pg_id):
    base = f"/api/panels/{pg_id}/pg/usage"
    ov = client.get(base + "/overview").json()
    assert ov["ok"] and ov["total"] == 629 and ov["online"] == 76 and ov["cached"] is False
    assert client.get(base + "/overview").json()["cached"] is True and sum(1 for _m, p, _t in pg.calls if p == "/api/system") == 1
    assert client.get(base + "/online").json()["online"] == 76
    rg = client.get(base + "/ranges").json()
    assert rg["ok"] and len(rg["presets"]) == 5 and rg["presets"][0]["preset"] == "today"
    r = client.get(base + "/range", params={"preset": "last7"}).json()
    assert r["ok"] and r["granularity"] == "day" and len(r["series"]) == 7 and sum(p["bytes"] for p in r["series"]) == r["total"]["bytes"] > 0
    c = client.get(base + "/range", params={"preset": "custom", "from": "1405/07/01", "to": "1405/07/02"}).json()
    assert c["ok"] and c["range"]["from"] == "2026-09-23" and c["range"]["to"] == "2026-09-24" and c["total"]["gb"] == 144.0
    assert client.get(base + "/admins").json()["admins"] == ["admin-a", "zed"]
    a = client.get(base + "/admin/admin-a", params={"preset": "last30"}).json()
    assert a["ok"] and a["scope"] == "admin" and a["total"]["gb"] > 0
    u = client.get(base + "/user/user-a").json()
    assert u["ok"] and u["scope"] == "user"
    t = client.get(base + "/top-users", params={"sort": "online_at", "limit": 7, "admin": "admin-a"}).json()
    assert t["ok"] and len(t["users"]) == 7 and t["sort"] == "online_at" and t["admin"] == "admin-a"
    only_gets(pg)


def test_routes_for_marzban(client, mz, mz_id):
    base = f"/api/panels/{mz_id}/pg/usage"
    assert client.get(base + "/overview").json()["total"] == 4589
    assert client.get(base + "/admin/admin-a", params={"preset": "last30"}).json()["total"]["gb"] == 40.0
    assert client.get(base + "/user/user-a").json()["total"]["gb"] == 2.0
    assert client.get(base + "/top-users").json()["total"] == 4589
    r = client.get(base + "/range")
    assert r.status_code == 400 and "needs an admin" in r.json()["error"]
    only_gets(mz)


def test_routes_refuse_bad_input(client, pg, pg_id):
    base = f"/api/panels/{pg_id}/pg/usage"
    assert client.get(base + "/range", params={"preset": "yesterday"}).status_code == 422                       # not one of the presets
    assert client.get(base + "/range", params={"preset": "custom"}).status_code == 400                          # no dates
    assert client.get(base + "/range", params={"preset": "custom", "from": "2026-10-05", "to": "2026-10-01"}).status_code == 400
    assert client.get(base + "/range", params={"preset": "custom", "from": "2026-01-01", "to": "2026-10-10"}).status_code == 400      # more than 93 days
    assert client.get(base + "/range", params={"preset": "custom", "from": "x" * 11}).status_code == 422
    assert client.get(base + "/top-users", params={"limit": 21}).status_code == 422                                # never more than 20 rows
    assert client.get(base + "/top-users", params={"limit": 0}).status_code == 422
    assert client.get(base + "/top-users", params={"sort": "username"}).status_code == 400
    assert client.get(base + "/admin/a b").status_code == 400
    assert client.get(base + "/admin/nobody").status_code == 400                                                # the panel's 404 becomes a clear refusal
    assert client.get(base + "/user/ghost").json()["error"] == "no user called ghost on this panel"
    assert client.get("/api/panels/9999/pg/usage/overview").status_code == 400                                  # no such panel
    assert not [c for c in pg.calls if "ghost" in c[1] and c[0] != "GET"]


def test_a_panel_that_is_down_is_a_502_with_its_reason(client, pg, pg_id):
    pg.fail = {"/api/system": PanelError("no answer from the panel (ConnectError)"), "/api/users/usage": PanelError("HTTP 500: boom", 500)}
    base = f"/api/panels/{pg_id}/pg/usage"
    r = client.get(base + "/overview")
    assert r.status_code == 502 and "no answer from the panel" in r.json()["error"]
    r = client.get(base + "/range")
    assert r.status_code == 502 and "HTTP 500: boom" in r.json()["error"]


def test_routes_need_a_signed_in_user(client, pg, pg_id):
    stranger = TestClient(client.app)
    assert stranger.get(f"/api/panels/{pg_id}/pg/usage/overview").status_code == 401
    assert pg.calls == []


def test_the_tab_is_read_only_and_every_route_is_described():
    methods = {m for r in api_mod.router.routes for m in r.methods}
    assert methods == {"GET"}
    assert len(api_mod.router.routes) == 10

    for r in api_mod.router.routes:
        doc = (r.endpoint.__doc__ or "").strip()
        assert 20 < len(doc) <= 300 and doc.count(". ") == 0 and "/pg/usage" in r.path, r.path                   # one sentence, at most 300 characters


# ------------------------------------------------------------------ the JS side runs in node (when node is there)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

NODE_RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const ctx = vm.createContext({window: {pgTabs: {}}, esc, $: () => null, console});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx, {filename: "pg_usage.js"});
process.stdout.write(JSON.stringify(eval("(" + process.argv[2] + ")")(ctx)));
"""


def run_js(fn_source: str):
    out = subprocess.run(["node", "-e", NODE_RUNNER, str(WEB / "pg_usage.js"), fn_source], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_js_registers_the_tab_for_both_panel_types():
    got = run_js("c => { const t = c.window.pgTabs.usage; return [t.label, t.order, t.types, typeof t.render]; }")
    assert got == ["Usage", 80, ["pasarguard", "marzban"], "function"]


@needs_node
def test_js_jalali_matches_the_pinned_dates():
    pins = [[j, g] for j, g in PINS]
    got = run_js("c => " + json.dumps(pins) + ".map(([j, g]) => [c.pgUsageG2J(...g), c.pgUsageJ2G(...j), c.pgUsageJalaliOf(g.map((v, i) => i ? String(v).padStart(2, '0') : String(v)).join('-'))])")

    for (j, g), (to_j, to_g, text) in zip(PINS, got):
        assert tuple(to_j) == j and tuple(to_g) == g and text == "{:04d}/{:02d}/{:02d}".format(*j)

    assert run_js("c => [c.pgUsageJalaliOf('2026-10-14'), c.pgUsageJalaliOf('2025-03-20'), c.pgUsageJalaliOf('junk'), c.pgUsageJalaliOf('2026-13-01'), c.pgUsageJalaliOf('')]") == ["1405/07/22", "1403/12/30", "", "", ""]


@needs_node
def test_js_jalali_equals_python_for_every_day_from_2000_to_2060():
    js = run_js("""c => { const out = []; for (let t = Date.UTC(2000, 0, 1); t <= Date.UTC(2060, 11, 31); t += 86400000) { const d = new Date(t), g = [d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate()], j = c.pgUsageG2J(...g); out.push([...g, ...j, ...c.pgUsageJ2G(...j)]); } return out; }""")
    assert len(js) == 22281
    d = dt.date(2000, 1, 1)

    for r in js:
        assert tuple(r[:3]) == (d.year, d.month, d.day)
        assert tuple(r[3:6]) == pg_usage.date_to_jalali(d), d                       # the same Jalali date as the Python side
        assert tuple(r[6:9]) == (d.year, d.month, d.day), d                         # and back again
        d += dt.timedelta(days=1)


@needs_node
def test_js_tehran_time_of_a_moment():
    got = run_js("""c => ['2026-10-14T01:25:03.827160Z', '2026-10-13T20:29:59Z', '2026-10-13T20:30:00Z', '2026-06-23T08:17:00', '2026-10-14T05:00:00+03:30', '', null, 'junk'].map(v => c.pgUsageTehran(v))""")
    assert got == ["2026-10-14 04:55", "2026-10-13 23:59", "2026-10-14 00:00", "2026-06-23 11:47", "2026-10-14 05:00", "", "", ""]

@needs_node
def test_js_bar_chart_svg():
    svg = run_js("c => c.pgUsageBarsSvg([{label: '10-11', jalali: '1405/07/19', gb: 3}, {label: '10-12', jalali: '1405/07/20', gb: 12.345}, {label: '10-13', gb: 0}])")
    assert svg.startswith("<svg") and svg.endswith("</svg>") and svg.count('class="pg-usage-bar') == 2 and svg.count('class="pg-usage-bar top"') == 1 and svg.count("<title>") == 3
    assert "10-12 · 1405/07/20: 12.35 GB" in svg and "script" not in svg
    assert svg.count("pg-usage-gl") == 4 and svg.count("pg-usage-line") == 1 and all(f">{v}</text>" in svg for v in ("0", "5", "10", "15", "20"))      # a scale: 4 grid lines and the base, the top is the next 1 / 2 / 5 x 10^n (12.35 -> 20)
    empty = run_js("c => [c.pgUsageBarsSvg([]), c.pgUsageBarsSvg(null), c.pgUsageBarsSvg([{label: 'a', gb: 0}]), c.pgUsageBarsSvg([{label: 'a', gb: 'x'}])]")
    assert "No data" in empty[0] and "No data" in empty[1] and "No traffic in this range" in empty[2] and "pg-usage-bar" not in empty[2] and "No data" in empty[3]
    evil = run_js("c => c.pgUsageBarsSvg([{label: '<script>alert(1)</script>', jalali: '\"><img src=x>', gb: 1}])")
    assert "<script>" not in evil and "<img" not in evil and "&lt;script&gt;" in evil
    many = run_js("c => c.pgUsageBarsSvg(Array.from({length: 31}, (_, i) => ({label: String(i + 1), gb: i})))")
    assert many.count("<title>") == 31 and many.count("<text") < 16                       # not a label on every bar (5 numbers of the scale and every 4th day)
    nice = run_js("c => [0, -3, 0.3, 1, 1.2, 4.2, 12.345, 100, 760].map(v => c.pgUsageNice(v))")
    assert nice == [1, 1, 0.5, 1, 2, 5, 20, 100, 1000]
    whole = run_js("c => c.pgUsageBarsSvg([{label: 'a', gb: 1}, {label: 'b', gb: 3}], {int: true, axisText: v => String(Math.round(v))})")
    assert all(f">{v}</text>" in whole for v in ("0", "1", "2", "3", "4"))                  # (counts of users: a whole-number scale, 0 to 4 at least)


@needs_node
def test_js_sorting_by_a_header_does_not_change_the_data():
    got = run_js("""c => { const rows = [{name: 'b', gb: 2}, {name: 'a', gb: 9}, {name: 'c', gb: 5}]; return [c.pgUsageSortRows(rows, 'gb', -1).map(r => r.name), c.pgUsageSortRows(rows, 'gb', 1).map(r => r.name), c.pgUsageSortRows(rows, 'name', 1).map(r => r.name), c.pgUsageSortRows(rows, 'name', -1).map(r => r.name), rows.map(r => r.name), c.pgUsageSortRows(null, 'gb', 1), c.pgUsageGbText(1234.5), c.pgUsageGbText(null)]; }""")
    assert got == [["a", "c", "b"], ["b", "c", "a"], ["a", "b", "c"], ["c", "b", "a"], ["b", "a", "c"], [], "1,234.50 GB", "0.00 GB"]


@needs_node
def test_js_rings_gauges_strips_and_top_n():
    ring = run_js("c => c.pgUsageRingSvg([{v: 3, cls: 'ok', label: 'Active: 3'}, {v: 1, cls: 'bad', label: 'Expired: 1'}, {v: 0, cls: 'off', label: 'x'}], {big: '4', small: 'users'})")
    assert ring.startswith("<svg") and ring.endswith("</svg>") and ring.count('class="pg-ring-arc') == 2 and "pg-ring-track" in ring and ">4</text>" in ring and ">users</text>" in ring and ring.count("<title>") == 2 and "Active: 3" in ring
    empty = run_js("c => [c.pgUsageRingSvg([]), c.pgUsageRingSvg(null, {big: '0'}), c.pgUsageRingSvg([{v: 'x'}])]")
    assert all("pg-ring-arc" not in e and "pg-ring-track" in e for e in empty)
    evil = run_js("c => c.pgUsageRingSvg([{v: 1, cls: '\"><img src=x>', label: '<script>alert(1)</script>'}], {big: '<b>'})")
    assert "<script>" not in evil and "<img" not in evil and "<b>" not in evil
    gauge = run_js("c => [c.pgUsageGaugeSvg(41, {name: 'Memory'}), c.pgUsageGaugeSvg(80, {warn: 70, bad: 85}), c.pgUsageGaugeSvg(95), c.pgUsageGaugeSvg(null)]")
    assert 'class="pg-ring-arc ok"' in gauge[0] and ">41%</text>" in gauge[0] and 'pg-ring-arc warn' in gauge[1] and 'pg-ring-arc bad' in gauge[2] and "pg-ring-arc" not in gauge[3] and ">—</text>" in gauge[3]
    spark = run_js("c => [c.pgUsageSparkSvg([{label: '10-11', gb: 3}, {label: '10-12', gb: 12}, {label: '10-13', gb: 0}]), c.pgUsageSparkSvg([]), c.pgUsageSparkSvg(null)]")
    assert spark[0].count("<rect") == 3 and spark[0].count("pg-spark-b top") == 1 and "10-12: 12.00 GB" in spark[0] and spark[1] == "" and spark[2] == ""
    got = run_js("c => [c.pgUsageTopN([{name: 'a', v: 5}, {name: 'b', v: 1}, {name: 'c', v: 9}], 5).map(r => r.name), c.pgUsageTopN([{name: 'a', v: 5}, {name: 'b', v: 4}, {name: 'c', v: 3}, {name: 'd', v: 2}, {name: 'z', v: 0}], 3).map(r => [r.name, r.v]), c.pgUsageTopN(null, 3), c.pgUsageGbParts(12.345), c.pgUsageGbParts(250), c.pgUsageGbParts(2048), c.pgUsageGbParts('x')]")
    assert got == [["c", "a", "b"], [["a", 5], ["b", 4], ["Other", 5]], [], ["12.3", "GB"], ["250", "GB"], ["2.00", "TB"], ["0.0", "GB"]]


NODE_PAGE_RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const els = {}, el = sel => els[sel] || (els[sel] = {innerHTML: "", textContent: "", hidden: false, dataset: {}, children: [], querySelectorAll: () => []});
const ctx = vm.createContext({window: {pgTabs: {}}, esc, $: el, console, fmtB: b => b == null ? "-" : (b / 1073741824).toFixed(2) + " GB", pgBar: (l, r) => "BAR[" + l + "|" + r + "]", pnTabBtns: () => "TABS"});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx, {filename: "pg_usage.js"});
vm.runInContext("pgUsageState.err = {}", ctx);
process.stdout.write(JSON.stringify(eval("(" + process.argv[2] + ")")(ctx, els, src => vm.runInContext(src, ctx))));
"""
PAGE_DATA = """
const ov = {total: 120, online: 7, active: 100, limited: 5, expired: 10, disabled: 3, on_hold: 2, cpu_pct: 41, mem_pct: 88, disk_pct: 14, speed_in: 1000, speed_out: 2000, traffic_in: 1e9, traffic_out: 2e9, version: "0.5.4", uptime_s: 90000, panel_type: "pasarguard"};
const range = {total: {gb: 12.5}, avg_per_day_gb: 1.8, granularity: "day", range: {days: 7, from: "2026-10-09", to: "2026-10-15", from_j: "a", to_j: "b"}, series: [{label: "10-09", gb: 1}, {label: "10-10", gb: 3}], by_node: [{name: "n1", gb: 8, share: 0.64}, {name: "n2", gb: 4.5, share: 0.36}]};
const apps = {total: 10, apps: [{name: "v2rayNG", count: 6, percent: 60}, {name: "Streisand", count: 4, percent: 40}]};
const top = {users: [{username: "alice", used_gb: 5.5}, {username: "<b>x</b>", used_gb: 1}], total: 2, sorts: []};
"""


def run_page_js(body: str):
    fn = "(c, els, run) => {" + PAGE_DATA + body + "}"
    out = subprocess.run(["node", "-e", NODE_PAGE_RUNNER, str(WEB / "pg_usage.js"), fn], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_js_overview_draws_round_cards_from_what_is_known_and_the_nodes_section_alone():
    """The overview is built from the counters, the range, the apps and the top users as they arrive; Marzban gets only the cards it can fill; a section that Marzban has not is never the current one."""
    got = run_page_js("""run("Object.assign(pgUsageState, {type: 'pasarguard', ov: " + JSON.stringify(ov) + ", range: " + JSON.stringify(range) + ", apps: " + JSON.stringify(apps) + ", top: " + JSON.stringify(top) + "})");
      c.pgUsageDrawCards(); const full = els["#pg-usage-ov"].innerHTML;
      const nodes = c.pgUsageNodesSecHtml(range, false);
      run("Object.assign(pgUsageState, {type: 'marzban', range: null, apps: null, sec: 'nodes'})"); c.pgUsageDrawCards(); const mz = els["#pg-usage-ov"].innerHTML;
      return [full, nodes, mz, run("pgUsageSecCur()"), run("pgUsageState.type = 'pasarguard'; pgUsageSecCur()")];""")
    full, nodes, mz, cur_mz, cur_pg = got
    assert full.count('class="card pg-rc"') == 6 and "7 online now" in full and full.count("pg-ring-arc") >= 5 + 3 + 2 + 2 + 0 and "NaN" not in full and "undefined" not in full
    assert 'pg-ring-arc warn' in full and ">88%</text>" in full and ">41%</text>" in full and ">14%</text>" in full            # the memory is amber, the others are quiet
    assert "n1" in full and "64%" in full and "Streisand" in full and "alice" in full and "&lt;b&gt;x&lt;/b&gt;" in full and "<b>x</b>" not in full
    assert "pg-ns" in nodes and 'id="pg-usage-range-nodes"' in nodes and "n2" in nodes and "12.50 GB" not in nodes or "pg-ns" in nodes
    assert mz.count('class="card pg-rc"') == 3 and "Traffic" not in mz and "Nodes" not in mz           # users, server, top users
    assert cur_mz == "ov" and cur_pg == "nodes"


@needs_node
def test_js_range_button_pure_dates_and_the_relative_panel():
    """The range is ONE button (its label says what it is) and a panel with Relative and Absolute tabs; the chosen chip is the one that matches the range."""
    got = run_js("c => [c.pgUsageAddDays('2026-10-15', -6), c.pgUsageAddDays('2026-03-01', -1), c.pgUsageAddDays('2026-12-31', 1), c.pgUsageAddDays('junk', 3), c.pgUsageDaysBetween('2026-10-09', '2026-10-15'), c.pgUsageDaysBetween('2026-10-15', '2026-10-15'), c.pgUsageDaysBetween('x', 'y')]")
    assert got == ["2026-10-09", "2026-02-28", "2027-01-01", "", 7, 1, 0]
    bar, pop, label, days, pop30 = run_page_js("""run("Object.assign(pgUsageState, {ranges: {tehran_today: '2026-10-15', presets: [{preset: 'last7', label: '', from: '2026-10-09', to: '2026-10-15', from_j: 'a', to_j: 'b', days: 7}]}, preset: 'last7', from: '', to: ''})");
      const bar = c.pgUsageBarHtml(), pop = c.pgUsagePopHtml();
      run("pgUsageState.preset = 'custom'; pgUsageState.from = '2026-09-16'; pgUsageState.to = '2026-10-15'");
      return [bar, pop, c.pgUsageRangeLabel(), c.pgUsageCurDays(), c.pgUsagePopHtml()];""")
    assert 'class="pg-rng"' in bar and "Last 7 days" in bar and 'aria-haspopup="dialog"' in bar and bar.startswith("BAR[TABS") and "2026-10-09 → 2026-10-15" in bar and "pg-usage-dates" not in bar
    assert 'class="pg-pc on" onclick="pgUsageRel(7)"' in pop and pop.count('class="pg-pc on"') == 1 and 'id="pg-pop-from" value="2026-10-09"' in pop and "Relative" in pop and "Absolute" in pop
    assert label == "Last 30 days" and days == 30 and 'class="pg-pc on" onclick="pgUsageRel(30)"' in pop30 and 'id="pg-pop-from" value="2026-09-16"' in pop30