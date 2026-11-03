"""Watching one PasarGuard user for a few hours: what the samples say (pure) and the start / read / stop / purge of the watch in the panel's own database."""
import datetime as dt

import pytest

from nikatel.db import session
from nikatel.models import UserWatch, UserWatchSample
from nikatel.services import pg_users, pg_watch
from nikatel.services.panels import PanelError

GB = 1024 ** 3
T0 = 1_793_600_000                                  # 2026-11-02, about 06:13 UTC = 09:43 in Tehran (daytime)
NIGHT = int(dt.datetime(2026, 11, 3, 0, 0, tzinfo=dt.timezone.utc).timestamp())          # 03:30 in Tehran


def _s(i, delta=0, ips=(), step=120, base=T0):
    return {"ts": base + i * step, "used": 0, "delta": delta, "online": [{"node": "n1", "ip": ip, "seen": base + i * step} for ip in ips]}


def _codes(samples, ours=None):
    return [f["code"] for f in pg_watch.summarize(samples, 120, ours)["findings"]]


def test_the_bars_the_addresses_and_the_figures():
    samples = [_s(0, 0, ["192.0.2.1"]), _s(1, 300 * 1024 ** 2, ["192.0.2.1"]), _s(2, 200 * 1024 ** 2, ["192.0.2.1", "192.0.2.2"]), _s(6, 100 * 1024 ** 2, ["192.0.2.2"])]
    r = pg_watch.summarize(samples, 120, {})
    assert r["total"] == 600 * 1024 ** 2 and r["samples"] == 4 and r["span"] == 720
    assert sum(b["bytes"] for b in r["bars"]) == r["total"] and max(b["ips"] for b in r["bars"]) == 2
    a = {x["ip"]: x for x in r["addresses"]}
    assert a["192.0.2.1"]["minutes"] == 6 and a["192.0.2.1"]["first"] == T0 and a["192.0.2.1"]["last"] == T0 + 240          # 3 reads of 2 minutes
    assert a["192.0.2.2"]["minutes"] == 4 and r["addresses"][0]["ip"] == "192.0.2.1"                                          # the longest first


def test_three_addresses_at_one_moment_are_a_shared_link_but_our_own_server_is_not_counted():
    three = [_s(0, 0, ["192.0.2.1", "192.0.2.2", "192.0.2.3"])]
    assert "simultaneous" in _codes(three)
    via_us = [_s(0, 0, ["198.51.100.1", "198.51.100.2", "198.51.100.3"])]
    got = _codes(via_us, {"198.51.100.1": "iran-a", "198.51.100.2": "iran-a", "198.51.100.3": "iran-b"})
    assert "simultaneous" not in got and "ours_only" in got


def test_night_burst_quiet_and_silence():
    night = [_s(i, GB, [], base=NIGHT) for i in range(2)]                                      # 03:30 in Tehran
    assert "night" in _codes(night)
    day = [_s(i, 100 * 1024 ** 2, ["192.0.2.1"]) for i in range(5)] + [_s(5, 3 * GB, ["192.0.2.1"])]
    assert "burst" in _codes(day) and "night" not in _codes(day)
    quiet = [_s(i, 1024 ** 2, ["192.0.2.1"], step=1800) for i in range(15)]                  # 7 hours, 14 MB
    assert "quiet" in _codes(quiet)
    assert _codes([_s(0), _s(1)]) == ["no_addresses"]
    many = [_s(i, 0, [f"192.0.2.{i + 1}"]) for i in range(5)]                                # one at a time: moving, not sharing
    assert "addresses" in _codes(many) and "simultaneous" not in _codes(many)
    assert pg_watch.summarize([], 120, {}) == {"bars": [], "addresses": [], "total": 0, "samples": 0, "span": 0, "peak": None, "findings": [], "night_share": 0.0, "simultaneous": 0}


@pytest.fixture
def panel(monkeypatch):
    """A PasarGuard panel that knows demo-1 .. demo-12 and answers with a used traffic that grows 100 MB a read."""
    state = {"used": 5 * GB, "online": [{"node": "n1", "ip": "192.0.2.1", "seen": T0}], "fail": False}

    def read(c, uid, username):
        if state["fail"]:
            raise PanelError("HTTP 502: down", 502)

        state["used"] += 100 * 1024 ** 2
        return state["used"], list(state["online"])

    monkeypatch.setattr(pg_watch.panels, "cred_for", lambda pid: {"id": pid, "type": "pasarguard"})
    monkeypatch.setattr(pg_users, "_id_of", lambda c, name: int(name.split("-")[1]))
    monkeypatch.setattr(pg_watch, "_read", read)
    monkeypatch.setattr("nikatel.services.pg_investigate._ours", dict)
    return state


def _count(model):
    with session() as db:
        return len(db.query(model).all())


def test_a_watch_starts_reads_at_once_then_every_two_minutes_and_ends_by_itself(panel):
    r = pg_watch.start(1, "demo-1", 2, ts=T0)
    assert r["active"] and r["watch"]["ends_at"] == T0 + 7200 and r["summary"]["samples"] == 1 and r["summary"]["total"] == 0          # the first read has nothing to compare with
    pg_watch.run(T0 + 30)
    assert _count(UserWatchSample) == 1                                                       # not due yet
    pg_watch.run(T0 + 125)
    pg_watch.run(T0 + 250)
    st = pg_watch.status(1, "demo-1", T0 + 250)
    assert st["summary"]["samples"] == 3 and st["summary"]["total"] == 200 * 1024 ** 2 and st["summary"]["addresses"][0]["ip"] == "192.0.2.1" and st["active"]
    pg_watch.run(T0 + 7300)                                                                  # past the end: switched off, no new read
    st = pg_watch.status(1, "demo-1", T0 + 7300)
    assert not st["active"] and st["summary"]["samples"] == 3 and _count(UserWatchSample) == 3


def test_starting_again_only_makes_the_watch_longer_and_stop_ends_it(panel):
    pg_watch.start(1, "demo-1", 1, ts=T0)
    r = pg_watch.start(1, "demo-1", 5, ts=T0 + 10)
    assert _count(UserWatch) == 1 and r["watch"]["ends_at"] == T0 + 10 + 5 * 3600
    r = pg_watch.start(1, "demo-1", 0.5, ts=T0 + 20)
    assert r["watch"]["ends_at"] == T0 + 10 + 5 * 3600                                       # never shorter
    r = pg_watch.stop(1, "demo-1", T0 + 100)
    assert not r["active"] and r["watch"]["ends_at"] == T0 + 100
    pg_watch.run(T0 + 500)
    assert _count(UserWatchSample) == 3                                                      # nothing is read after the stop (only the one read each of the three starts)
    assert pg_watch.status(1, "nobody-9")["watch"] is None


def test_the_limits_of_a_watch(panel):
    for bad in (0, 0.1, 73, "x", None):
        with pytest.raises(ValueError):
            pg_watch.start(1, "demo-1", bad, ts=T0)

    for i in range(1, pg_watch.MAX_ACTIVE + 1):
        pg_watch.start(1, f"demo-{i}", 1, ts=T0)

    with pytest.raises(ValueError, match="watched already"):
        pg_watch.start(1, "demo-11", 1, ts=T0)

    pg_watch.start(1, "demo-3", 2, ts=T0 + 5)                                                # one that is watched can still be made longer
    pg_watch.stop(1, "demo-4", T0 + 6)
    pg_watch.start(1, "demo-11", 1, ts=T0 + 7)                                               # a free place after a stop


def test_a_panel_that_does_not_answer_is_noted_and_tried_again(panel):
    pg_watch.start(1, "demo-1", 2, ts=T0)
    panel["fail"] = True
    pg_watch.run(T0 + 130)
    st = pg_watch.status(1, "demo-1", T0 + 130)
    assert st["summary"]["samples"] == 1 and "502" in st["watch"]["last_error"] and st["active"]
    panel["fail"] = False
    pg_watch.run(T0 + 260)
    st = pg_watch.status(1, "demo-1", T0 + 260)
    assert st["summary"]["samples"] == 2 and st["watch"]["last_error"] == ""


def test_a_reset_of_the_usage_is_not_counted_and_old_watches_are_thrown_away(panel):
    pg_watch.start(1, "demo-1", 1, ts=T0)
    panel["used"] = 1 * GB                                                                   # the panel reset the usage
    pg_watch.run(T0 + 130)
    assert pg_watch.status(1, "demo-1", T0 + 130)["summary"]["total"] == 0 or pg_watch.status(1, "demo-1", T0 + 130)["summary"]["total"] <= 100 * 1024 ** 2
    pg_watch.run(T0 + 3700)                                                                  # ended
    pg_watch.run(T0 + 3700 + pg_watch.KEEP_DAYS * 86400 + 10)
    assert _count(UserWatch) == 0 and _count(UserWatchSample) == 0
