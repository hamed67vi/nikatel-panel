"""The usage of the CONFIGS of a PasarGuard panel (services/cfgmeter.py, cfgmap.py, cfgcdn.py, cfgstats.py, api/pg_configs.py): the nft ruleset and what the server's scripts answer, counters into hours (resets,
reboots, gaps), the map from hosts to counters (direct, CDN, node template, shared, unknown), the CDN report parsers and probe, the judgement (little used, falling, trend) and the routes. A fake panel, fake
servers and fake CDNs answer; nothing touches a real one. The ruleset itself is proved on a real kernel by the network-namespace script in the notes of the 1.70.0 work."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import CfgBucket, CfgMeter, Event, Panel, Server
from nikatel.services import (
    arvan,
    cfgcdn,
    cfgmap,
    cfgmeter,
    cfgstats,
    cloudflare,
    dnscache,
    dnsprov,
    panels,
    pg_nodes,
)
from nikatel.services import settings as st
from nikatel.ssh import SSHError

GB = 1024 ** 3
NOW = 1790002800                       # a whole hour
HOUR, DAY = 3600, 86400
F_IP, I_IP = "203.0.113.10", "198.51.100.20"


# ------------------------------------------------------------------ the ruleset and the scripts' answers
def test_ruleset_only_counts():
    r = cfgmeter.ruleset([8072, 443, 50325, 443, 0, 70000, "x", True])
    assert r.startswith("add table inet nikatel_meter\ndelete table inet nikatel_meter\ntable inet nikatel_meter {")
    assert r.count("chain p") == 3 and "chain p443 {" in r and "chain p8072 {" in r and "chain p50325 {" in r
    assert r.count('counter comment "rx"') == 3 and r.count('counter comment "tx"') == 3
    assert "443 : jump p443, 8072 : jump p8072, 50325 : jump p50325" in r
    assert r.count("policy accept") == 3                                    # input, output, forward: nothing is ever dropped
    assert not re.search(r"\b(drop|reject|masquerade|queue)\b|dnat to|snat to|redirect to|mark set|ct mark", r)
    assert "ct direction original meta l4proto { tcp, udp } ct original proto-dst vmap" in r      # the port the USER dialled
    assert "ct direction reply meta l4proto { tcp, udp } ct original proto-dst vmap" in r
    assert "ct status dnat" in r                                             # the forward chain counts only what was forwarded by a DNAT
    assert not re.search(r"chain (in|out|fwd) ", r)                          # `in` `out` `fwd` are nft keywords: the real kernel refused `fwd`


def test_ruleset_without_ports_is_an_empty_table_and_ports_are_cleaned():
    assert cfgmeter.ruleset([]) == "add table inet nikatel_meter\ndelete table inet nikatel_meter\ntable inet nikatel_meter {\n}\n"
    assert cfgmeter.clean_ports(["22", 22, 80.0, "x", None, 65535, 65536, -1, False]) == [22, 80, 65535]
    assert len(re.findall(r"chain p\d+ \{", cfgmeter.ruleset(range(1, 1000)))) == cfgmeter.MAX_PORTS


READ = """BOOT 7f3a
UP 5123.25
TABLE ok
C 8072 rx 1003336
C 8072 tx 2003440
C 443 rx 0
C 443 tx 0
C 50325 rx 1004688
C 50325 tx 3004012
L 8072
S 8072 2
T 50325 1
CT yes
junk line
C x rx 5
C 22 rx
DONE
"""


def test_parse_answer_of_a_read():
    r = cfgmeter.parse_answer(READ)
    assert r["boot"] == "7f3a" and r["up"] == 5123.25 and r["table"] is True and r["done"] is True and r["ct"] is True
    assert r["c"] == {8072: [1003336, 2003440], 443: [0, 0], 50325: [1004688, 3004012]}
    assert r["listen"] == [8072] and r["s"] == {8072: 2} and r["t"] == {50325: 1}
    assert cfgmeter.conns_of(r, [8072, 443, 50325]) == {8072: 2, 443: 0, 50325: 1}


def test_a_machine_that_cannot_see_conntrack_gives_no_number_for_a_forwarded_port():
    r = cfgmeter.parse_answer("BOOT a\nTABLE ok\nL 8072\nS 8072 3\nCT no\nDONE\n")
    assert r["ct"] is False
    assert cfgmeter.conns_of(r, [8072, 50325, 2096]) == {8072: 3}           # not a made-up zero


def test_parse_answer_of_apply_and_remove_keeps_the_old_counters_apart():
    out = "BOOT b1\nOLD\nC 8072 rx 10\nC 8072 tx 20\nENDOLD\nMETER_ON\n"
    r = cfgmeter.parse_answer(out)
    assert r["ok"] is True and r["old"] == {8072: [10, 20]} and r["c"] == {} and r["boot"] == "b1"
    f = cfgmeter.parse_answer("METER_FAIL this server has no nft (nftables is not installed)\n")
    assert f["ok"] is False and "no nft" in f["why"]
    assert cfgmeter.parse_answer("METER_OFF\n")["ok"] is True
    assert cfgmeter.parse_answer("")["ok"] is None and cfgmeter.parse_answer(None)["table"] is False


# ------------------------------------------------------------------ counters into differences, differences into hours
def test_step_first_read_is_a_baseline_and_then_differences():
    s1, d1, _ = cfgmeter.step(None, {8072: [100, 200]}, "b", 1000)
    assert d1 == {} and s1 == {"boot": "b", "ts": 1000, "c": {8072: [100, 200]}}
    s2, d2, since = cfgmeter.step({"boot": "b", "ts": 1000, "c": {"8072": [100, 200]}}, {8072: [150, 260], 443: [5, 6]}, "b", 1300)
    assert d2 == {8072: (50, 60), 443: (5, 6)} and since == 1000          # a port new in the table counts what it has
    assert s2["c"] == {8072: [150, 260], 443: [5, 6]}


def test_step_counter_reset_and_reboot_count_what_is_there():
    last = {"boot": "b", "ts": 1000, "c": {"8072": [1000, 2000]}}
    _, d, _ = cfgmeter.step(last, {8072: [30, 5000]}, "b", 1300)             # rx went back (the table was loaded again), tx did not
    assert d == {8072: (30, 3000)}
    _, d, since = cfgmeter.step(last, {8072: [700, 800]}, "other-boot", 1300)  # a reboot: everything counted since is new, and it began "now" (the time before the reboot is not known)
    assert d == {8072: (700, 800)} and since == 1300
    _, d, _ = cfgmeter.step(last, {8072: [1000, 2000]}, "b", 1300)
    assert d == {}                                                         # nothing moved


def test_step_with_a_clock_set_back_never_puts_bytes_into_a_future_hour():
    """Found by the fuzz run: the last read stamped LATER than now (the clock of the panel was stepped back) made the bytes begin in the future."""
    _, d, since = cfgmeter.step({"boot": "b", "ts": 5000, "c": {"1": [0, 0]}}, {1: [10, 20]}, "b", 1000)
    assert d == {1: (10, 20)} and since == 1000
    assert cfgmeter.spread(30, since, 1000) == [(0, 30)]

def test_step_gap_is_capped():
    _, _d, since = cfgmeter.step({"boot": "b", "ts": 10, "c": {}}, {1: [1, 1]}, "b", 10 + 10 * DAY)
    assert since == 10 + 10 * DAY - cfgmeter.MAX_SPREAD


def test_spread_keeps_the_sum_and_follows_the_seconds():
    assert cfgmeter.spread(100, 3600 * 5 + 10, 3600 * 5 + 900) == [(3600 * 5, 100)]
    got = cfgmeter.spread(1000, 3600 * 5 + 1800, 3600 * 7 + 1800)                # 30 min + 60 min + 30 min of 120
    assert [h for h, _ in got] == [3600 * 5, 3600 * 6, 3600 * 7] and sum(a for _, a in got) == 1000
    assert [a for _, a in got] == [250, 500, 250]
    assert cfgmeter.spread(7, 100, 100) == [(0, 7)] and cfgmeter.spread(0, 1, 2) == [] and cfgmeter.spread(-5, 1, 2) == []
    odd = cfgmeter.spread(10, 3600 * 5 + 1, 3600 * 8 - 1)
    assert sum(a for _, a in odd) == 10


# ------------------------------------------------------------------ the hours in the database
def mk_server(name, host, role="foreign", **kw):
    with session() as db:
        s = Server(name=name, host=host, role=role, **kw)
        db.add(s)
        db.flush()
        return s.id


def fill(src, days, per_day, now=NOW, down_share=0.8, conns=None):
    """Hours for `days` complete days before `now`: per_day(d) bytes for day d (0 = the newest day, counted back), spread evenly over the hours."""
    acc: dict = {}

    for d in range(days):
        for h in range(24):
            hour = now - (d * 24 + h) * HOUR
            amount = int(per_day(d) / 24)
            cfgmeter.add(acc, src, hour, int(amount * (1 - down_share)), int(amount * down_share), conns(d, h) if conns else None)

    cfgmeter.flush(acc)


def test_stats_windows_prev_peak_days_and_first():
    fill("p:1:8072", 20, lambda d: 24 * GB, conns=lambda d, h: 5 + (h if d == 3 else 0))
    s = cfgmeter.stats(["p:1:8072", "p:9:9"], NOW)
    assert list(s) == ["p:1:8072"]                                          # a source with no hours is not in the answer
    m = s["p:1:8072"]
    assert m["bytes"]["24h"] == pytest.approx(24 * GB, rel=1e-3) and m["prev"]["24h"] == pytest.approx(24 * GB, rel=1e-3)
    assert m["bytes"]["7d"] == pytest.approx(7 * 24 * GB, rel=1e-3) and m["prev"]["7d"] == pytest.approx(7 * 24 * GB, rel=1e-3)
    assert m["bytes"]["30d"] == pytest.approx(20 * 24 * GB, rel=1e-3) and m["prev"]["30d"] == 0
    assert m["down"]["7d"] == pytest.approx(7 * 24 * GB * 0.8, rel=1e-3) and m["up"]["7d"] == pytest.approx(7 * 24 * GB * 0.2, rel=1e-3)
    assert m["peak"]["24h"] == 5 and m["peak"]["7d"] == 5 + 23 and m["samples"]["24h"] == 24
    assert m["first"] == NOW - (20 * 24 - 1) * HOUR and m["last"] == NOW
    assert len(m["days"]) == 30 and sum(m["days"]) == m["bytes"]["30d"] and m["days"][:9] == [0] * 9 and all(v > 0 for v in m["days"][9:])
    assert cfgmeter.coverage_days(m["first"], NOW) == pytest.approx(20.0, rel=1e-6) and cfgmeter.coverage_days(None) == 0.0


def test_tehran_days_split_at_2030_utc():
    h = (NOW // DAY) * DAY + 20 * HOUR + 1800 - DAY                         # just after 00:00 Tehran
    assert cfgmeter.tehran_day(h) == cfgmeter.tehran_day(h - 1) + 1
    assert cfgmeter.tehran_day(0) == 0 and cfgmeter.tehran_day(DAY - 12600 - 1) == 0 and cfgmeter.tehran_day(DAY - 12600) == 1


def test_prune_removes_only_the_old_hours():
    fill("p:1:1", 70, lambda d: GB)
    n = cfgmeter.prune(NOW)
    assert n > 0
    with session() as db:
        left = [r.hour for r in db.query(CfgBucket).all()]

    assert min(left) >= NOW // HOUR * HOUR - cfgmeter.KEEP_DAYS * DAY and max(left) == NOW


def answer(boot="b1", c=None, listen=(), s=None, t=None, ct=True, table=True):
    lines = [f"BOOT {boot}", "UP 100"] + (["TABLE ok"] if table else ["TABLE missing"])

    for p, (rx, tx) in (c or {}).items():
        lines += [f"C {p} rx {rx}", f"C {p} tx {tx}"]

    lines += [f"L {p}" for p in listen] + [f"S {p} {n}" for p, n in (s or {}).items()] + [f"T {p} {n}" for p, n in (t or {}).items()] + [f"CT {'yes' if ct else 'no'}", "DONE"]
    return "\n".join(lines) + "\n"


class FakeBox:
    """A server that answers the meter scripts and keeps what was loaded."""

    def __init__(self, monkeypatch):
        self.reads: list[str] = []
        self.actions: list[tuple[str, dict]] = []
        self.apply_answer = "BOOT b1\nMETER_ON\n"
        self.remove_answer = "OLD\nC 8072 rx 7\nC 8072 tx 9\nENDOLD\nMETER_OFF\n"
        monkeypatch.setattr(cfgmeter, "run_script", self.script)
        monkeypatch.setattr(cfgmeter, "run_action", self.action)

    def script(self, node, name, params=None, timeout=40):
        assert name == "meter_read"
        return self.reads.pop(0)

    def action(self, node, name, params=None, timeout=90):
        self.actions.append((name, dict(params or {})))
        return self.apply_answer if name == "meter_apply" else self.remove_answer


def test_apply_tick_and_remove_turn_counters_into_hours(monkeypatch):
    sid = mk_server("fr-1", F_IP)
    box = FakeBox(monkeypatch)
    out = cfgmeter.apply(sid, [8072, 443], NOW)
    assert out["ports"] == [443, 8072] and box.actions[0][0] == "meter_apply"
    assert "chain p443" in __import__("base64").b64decode(box.actions[0][1]["B64"]).decode()
    m = cfgmeter.get(sid)
    assert m["enabled"] and m["ports"] == [443, 8072] and m["since"] == NOW and m["state"] == {"boot": "b1", "ts": NOW, "c": {}}

    # first read after the apply: everything counted so far is new (the table began at zero)
    box.reads.append(answer("b1", {8072: (1000, 4000), 443: (0, 0)}, listen=[8072], s={8072: 3}))
    assert cfgmeter.tick(sid, [443, 8072], NOW + 300)["action"] == "read"
    box.reads.append(answer("b1", {8072: (1500, 6000), 443: (10, 20)}, listen=[8072], s={8072: 7}))
    cfgmeter.tick(sid, [443, 8072], NOW + 600)
    s = cfgmeter.stats([cfgmeter.src_of(sid, 8072), cfgmeter.src_of(sid, 443)], NOW + 600)
    a, b = s[f"p:{sid}:8072"], s[f"p:{sid}:443"]
    assert a["up"]["24h"] == 1500 and a["down"]["24h"] == 6000 and a["peak"]["24h"] == 7 and a["samples"]["24h"] == 2
    assert b["bytes"]["24h"] == 30 and b["peak"]["24h"] == 0
    # a reboot: the counters start again, the new boot id says so
    box.reads.append(answer("b2", {8072: (100, 300), 443: (0, 0)}, listen=[8072], s={8072: 1}))
    cfgmeter.tick(sid, [443, 8072], NOW + 900)
    a = cfgmeter.stats([f"p:{sid}:8072"], NOW + 900)[f"p:{sid}:8072"]
    assert a["up"]["24h"] == 1600 and a["down"]["24h"] == 6300
    # the table is gone (reboot): the panel loads it again, nothing is lost or counted twice
    box.reads.append(answer("b3", table=False))
    assert cfgmeter.tick(sid, [443, 8072], NOW + 1200)["action"] == "applied" and [n for n, _ in box.actions].count("meter_apply") == 2
    # the ports to count changed: it reads, then loads the new table
    box.reads.append(answer("b1", {8072: (50, 60), 443: (0, 0)}, listen=[8072]))
    assert cfgmeter.tick(sid, [443, 8072, 2096], NOW + 1500)["action"] == "applied"
    assert cfgmeter.get(sid)["ports"] == [443, 2096, 8072]
    r = cfgmeter.remove(sid, NOW + 1800)
    assert r["server"] == "fr-1" and not cfgmeter.get(sid)["enabled"] and cfgmeter.get(sid)["ports"] == []
    a = cfgmeter.stats([f"p:{sid}:8072"], NOW + 1800)[f"p:{sid}:8072"]
    assert a["up"]["24h"] >= 1650 + 7                                       # the old table's last counters were added when it was removed


def test_a_server_that_refuses_is_not_recorded_as_on(monkeypatch):
    sid = mk_server("fr-2", "203.0.113.11")
    box = FakeBox(monkeypatch)
    box.apply_answer = "METER_FAIL this server has no nft (nftables is not installed)\n"

    with pytest.raises(RuntimeError, match="no nft"):
        cfgmeter.apply(sid, [8072])

    assert not cfgmeter.get(sid)["enabled"] and "no nft" in cfgmeter.get(sid)["last_error"]

    with pytest.raises(ValueError):
        cfgmeter.apply(sid, [])


def test_an_idle_port_with_no_connection_numbers_still_has_its_hour(monkeypatch):
    """Found on a real test server: a port that moved nothing, on a machine that cannot say anything about conntrack, had NO row at all, so its host stayed "counting has just begun" for ever."""
    sid = mk_server("fr-9", "203.0.113.19")
    box = FakeBox(monkeypatch)
    cfgmeter.apply(sid, [18072], NOW)
    box.reads.append(answer("b1", {18072: (0, 0)}, ct=False))
    cfgmeter.tick(sid, [18072], NOW + 300)
    m = cfgmeter.stats([f"p:{sid}:18072"], NOW + 300)[f"p:{sid}:18072"]
    assert m["bytes"]["24h"] == 0 and m["samples"]["24h"] == 0 and m["peak"]["24h"] == 0 and cfgmeter.coverage_days(m["first"], NOW + 300) > 0


def test_a_quiet_port_shows_zero_bytes_and_no_connection_number(world, monkeypatch):
    w = world
    box = FakeBox(monkeypatch)
    cfgmeter.apply(w["f"], [443, 2087, 8072, 8075], NOW)
    box.reads.append(answer("b1", {443: (0, 0), 2087: (0, 0), 8072: (0, 0), 8075: (0, 0)}, ct=False))
    cfgmeter.tick(w["f"], [443, 2087, 8072, 8075], NOW + 300)
    cfgstats.forget()
    r = cfgstats.snapshot(w["pid"], None, False, NOW + 300)["hosts"][0]
    assert r["state"] == "ok" and r["view"]["has"] and r["view"]["bytes"]["24h"] == 0 and r["view"]["peak"]["24h"] is None          # zero, and "no number" for the connections - not "just begun"

def test_tick_never_raises_for_a_server_that_does_not_answer(monkeypatch):
    sid = mk_server("fr-3", "203.0.113.12")
    FakeBox(monkeypatch)
    cfgmeter.apply(sid, [8072])

    def boom(*a, **k):
        raise SSHError("ssh connection lost (network)")

    monkeypatch.setattr(cfgmeter, "run_script", boom)
    r = cfgmeter.tick(sid, [8072], NOW)
    assert r["ok"] is False and "ssh connection lost" in r["error"] and "ssh connection lost" in cfgmeter.get(sid)["last_error"]
    assert cfgmeter.tick(9999, None, NOW)["action"] == "skipped"            # a server with no meter is left alone


# ------------------------------------------------------------------ how it lives with the rest of the panel
def events_of(ref):
    with session() as db:
        return [(e.level, e.domain, e.text) for e in db.query(Event).filter(Event.ref == ref).order_by(Event.id)]


def test_a_server_that_is_switched_off_is_left_alone_and_one_the_pulse_calls_off_is_not_asked(monkeypatch):
    sid = mk_server("fr-5", "203.0.113.15")
    box = FakeBox(monkeypatch)
    cfgmeter.apply(sid, [8072], NOW)
    box.reads.append(answer("b1", {8072: (5, 6)}, listen=[8072]))
    with session() as db:
        db.get(Server, sid).enabled = False

    assert cfgmeter.tick(sid, [8072], NOW + 300) == {"ok": True, "action": "skipped", "error": ""} and len(box.reads) == 1      # not asked, no error
    with session() as db:
        s = db.get(Server, sid)
        s.enabled, s.online = True, False

    r = cfgmeter.tick(sid, [8072], NOW + 600)
    assert r["ok"] is False and "server is off" in r["error"] and len(box.reads) == 1                                              # the pulse says off: no ssh, the reason is kept
    assert "server is off" in cfgmeter.get(sid)["last_error"]


def test_the_event_log_hears_what_was_done_to_a_server_and_each_failure_once(monkeypatch):
    sid = mk_server("fr-6", "203.0.113.16")
    box = FakeBox(monkeypatch)
    cfgmeter.apply(sid, [8072], NOW)
    assert events_of("fr-6")[-1][:2] == ("ok", "actions") and "config meter on - counting 1 port " in events_of("fr-6")[-1][2]
    box.reads.append(answer("b1", table=False))
    assert cfgmeter.tick(sid, [8072], NOW + 300)["action"] == "applied"
    assert "was gone" in events_of("fr-6")[-1][2] and events_of("fr-6")[-1][0] == "info"

    def boom(*a, **k):
        raise SSHError("ssh connection lost (network)")

    monkeypatch.setattr(cfgmeter, "run_script", boom)
    cfgmeter.tick(sid, [8072], NOW + 600)
    cfgmeter.tick(sid, [8072], NOW + 900)                                                                                           # the same failure again: no second line
    warns = [e for e in events_of("fr-6") if e[0] == "warn"]
    assert len(warns) == 1 and "could not be read" in warns[0][2]
    monkeypatch.setattr(cfgmeter, "run_script", box.script)
    box.reads.append(answer("b1", {8072: (1, 1)}, listen=[8072]))
    cfgmeter.tick(sid, [8072], NOW + 1200)
    assert "is read again" in events_of("fr-6")[-1][2]
    cfgmeter.remove(sid, NOW + 1500)
    assert "config meter off" in events_of("fr-6")[-1][2]


def test_a_server_that_leaves_the_panel_takes_its_table_and_its_hours_with_it(client, monkeypatch):
    sid, other = mk_server("fr-7", "203.0.113.17"), mk_server("fr-8", "203.0.113.18")
    box = FakeBox(monkeypatch)
    cfgmeter.apply(sid, [8072], NOW)
    cfgmeter.apply(other, [8072], NOW)
    fill(f"p:{sid}:8072", 3, lambda d: GB)
    fill(f"p:{other}:8072", 3, lambda d: GB)
    r = client.delete(f"/api/servers/{sid}")
    assert r.status_code == 200
    assert [n for n, _ in box.actions].count("meter_remove") == 1                                                                  # its table was taken off
    assert cfgmeter.stats([f"p:{sid}:8072"], NOW) == {} and cfgmeter.get(sid)["enabled"] is False
    assert cfgmeter.stats([f"p:{other}:8072"], NOW)[f"p:{other}:8072"]["bytes"]["24h"] > 0 and cfgmeter.get(other)["enabled"]       # another server is not touched
    with session() as db:
        assert db.query(CfgMeter).filter(CfgMeter.server_id == sid).count() == 0
    # a server that is off is not asked at all, and nothing stops its deletion
    with session() as db:
        db.get(Server, other).online = False

    box.actions.clear()
    assert client.delete(f"/api/servers/{other}").status_code == 200 and box.actions == []


def test_the_last_pasarguard_panel_leaving_takes_every_table_off(client, world, monkeypatch):
    w = world
    box = FakeBox(monkeypatch)
    cfgmeter.apply(w["f"], [443], NOW)
    box.actions.clear()
    with session() as db:
        db.add(Panel(name="pg-two", type="pasarguard", url="https://panel-pg2.example.test", username="a", secrets={"password": "x"}, status="up"))
        db.flush()
        second = db.query(Panel).filter(Panel.name == "pg-two").one().id

    assert client.delete(f"/api/panels/{second}").status_code == 200
    assert box.actions == [] and cfgmeter.get(w["f"])["enabled"]                                                                   # another PasarGuard panel is still there: the meters stay
    assert client.delete(f"/api/panels/{w['pid']}").status_code == 200
    assert [n for n, _ in box.actions] == ["meter_remove"] and not cfgmeter.get(w["f"])["enabled"]                                   # no panel left to switch them off on: they are taken off
    with session() as db:
        db.add(Panel(name="mz-one", type="marzban", url="https://panel-mz1.example.test", username="a", secrets={"password": "x"}, status="up"))
        db.flush()
        mz = db.query(Panel).filter(Panel.name == "mz-one").one().id

    cfgmeter.apply(w["f"], [443], NOW)
    box.actions.clear()
    assert client.delete(f"/api/panels/{mz}").status_code == 200 and box.actions == []                                              # a Marzban panel has nothing to do with it


def test_a_panel_that_stops_answering_leaves_the_last_map_and_says_so(world, monkeypatch):
    w = world
    meter_row(w["f"], [443, 2087, 8072, 8075])
    first = cfgstats.snapshot(w["pid"], None, False, NOW)
    assert first["panel_error"] == "" and first["read_at"] > 0

    def down(c, method, path, **kw):
        raise panels.PanelError("no answer from the panel (ConnectError)")

    monkeypatch.setattr(panels, "call", down)
    cfgstats.forget()                                                                                                               # (a meter change forgets the fresh map, never the last good one)
    again = cfgstats.snapshot(w["pid"], None, False, NOW)
    assert [h["remark"] for h in again["hosts"]] == [h["remark"] for h in first["hosts"]] and "no answer from the panel" in again["panel_error"]
    cfgstats.run()                                                                                                                  # the job goes on with it too (it must not die when the panel restarts)
    monkeypatch.setattr(panels, "call", w["fake"])
    cfgstats.forget()
    assert cfgstats.snapshot(w["pid"], None, False, NOW)["panel_error"] == ""


def test_a_panel_that_never_answered_is_a_clear_error_not_a_made_up_page(client, world, monkeypatch):
    def down(c, method, path, **kw):
        raise panels.PanelError("no answer from the panel (ConnectError)")

    monkeypatch.setattr(panels, "call", down)
    cfgstats._LAST.clear()
    cfgstats.forget()
    r = client.get(f"/api/panels/{world['pid']}/pg/configs")
    assert r.status_code == 502 and "no answer from the panel" in r.json()["error"]


def test_a_read_token_may_look_but_not_switch_a_server(client, world):
    PW = "test-password-1"
    tok = client.post("/api/tokens", json={"name": "bot", "scope": "read", "password": PW}).json()["token"]
    act = client.post("/api/tokens", json={"name": "doctor", "scope": "act", "password": PW}).json()["token"]
    from fastapi.testclient import TestClient

    m = TestClient(client.app)
    base = f"/api/panels/{world['pid']}/pg/configs"
    assert m.get(base, headers={"Authorization": "Bearer " + tok}).status_code == 200
    r = m.post(base + "/meter", headers={"Authorization": "Bearer " + tok}, json={"on": False})
    assert r.status_code == 403 and r.json()["code"] == "token_scope"
    assert m.post(base + "/cdn", headers={"Authorization": "Bearer " + tok}, json={"on": False}).status_code == 403
    assert m.post(base + "/cdn", headers={"Authorization": "Bearer " + act}, json={"on": True}).status_code == 200


def test_the_old_hours_go_once_a_day_even_when_no_meter_is_on(world):
    fill("p:1:1", 70, lambda d: GB, now=int(time.time()) // HOUR * HOUR)
    cfgstats._PRUNED[0] = 0
    cfgstats.run()
    with session() as db:
        oldest = min(r.hour for r in db.query(CfgBucket).all())

    assert oldest >= int(time.time()) // HOUR * HOUR - cfgmeter.KEEP_DAYS * DAY - HOUR
    before = len(cfgmeter.stats(["p:1:1"], int(time.time()))["p:1:1"]["days"])
    cfgstats.run()                                                                                                                  # not again within the day
    assert cfgstats._PRUNED[0] > 0 and before == 30

def test_a_read_and_the_loading_of_the_table_cannot_interleave(monkeypatch):
    """Found by reading: a read taken before Turn on / a change of ports and added to the hours after it counted the old table's bytes twice."""
    import threading

    sid = mk_server("fr-10", "203.0.113.30")
    FakeBox(monkeypatch)
    cfgmeter.apply(sid, [8072], NOW)
    gate, entered = threading.Event(), threading.Event()

    def slow(node, name, params=None, timeout=40):
        entered.set()
        gate.wait(5)
        return answer("b1", {8072: (1000, 2000)}, listen=[8072])

    monkeypatch.setattr(cfgmeter, "run_script", slow)
    t1 = threading.Thread(target=lambda: cfgmeter.tick(sid, [8072], NOW + 300))
    t1.start()
    assert entered.wait(5)
    done: list[int] = []
    t2 = threading.Thread(target=lambda: (cfgmeter.apply(sid, [8072, 443], NOW + 301), done.append(1)))
    t2.start()
    time.sleep(0.4)
    assert not done                                                                                                                 # the new table waits until the read is in the hours
    gate.set()
    t1.join(5)
    t2.join(5)
    assert done and cfgmeter.stats([f"p:{sid}:8072"], NOW + 400)[f"p:{sid}:8072"]["bytes"]["24h"] == 3000


def test_two_pasarguard_panels_on_one_server_do_not_take_each_others_ports(world, monkeypatch):
    w = world
    meter_row(w["f"], [443, 2087, 8072, 8075], enabled=False)
    with session() as db:
        db.add(Panel(name="pg-two", type="pasarguard", url="https://panel-pg2.example.test", username="a", secrets={"password": "x"}, status="up"))
        db.flush()
        second = db.query(Panel).filter(Panel.name == "pg-two").one().id

    class Two(FakePanel):
        def __call__(self, c, method, path, **kw):
            if c["id"] == second and path == "/api/hosts":
                return [host(50, F_IP, "VLESS WS 2053", 31000)]

            return super().__call__(c, method, path, **kw)

    two = Two()
    monkeypatch.setattr(panels, "call", two)
    cfgstats.forget()
    cfgstats._LAST.clear()
    cfgstats.snapshot(w["pid"], None, False, NOW)
    s2 = cfgstats.snapshot(second, None, False, NOW)
    assert 31000 in cfgstats.wanted(w["f"]) and 8072 in cfgstats.wanted(w["f"])                                                      # one table per server: the ports of both panels
    assert s2["servers"][0]["plan_ports"] == len(cfgstats.wanted(w["f"]))
    box = FakeBox(monkeypatch)
    out = cfgstats.meter_on(w["pid"], w["f"])
    assert 31000 in out["ports"] and 8072 in out["ports"]
    assert "chain p31000" in __import__("base64").b64decode(box.actions[0][1]["B64"]).decode()
    assert not cfgstats.snapshot(second, None, True, NOW)["servers"][0]["differs"] and not cfgstats.snapshot(w["pid"], None, True, NOW)["servers"][0]["differs"]

# ------------------------------------------------------------------ the map: hosts on counters
def core(inbounds):
    return {"id": 1, "name": "core", "config": {"inbounds": inbounds}}


def ib(tag, port, net="tcp", sec="none", proto="vless", listen=None):
    d = {"tag": tag, "port": port, "protocol": proto, "streamSettings": {"network": net, "security": sec}}

    if listen:
        d["listen"] = listen

    return d


SERVERS = [{"id": 1, "name": "fr-1", "host": F_IP, "role": "foreign", "enabled": True}, {"id": 2, "name": "ir-1", "host": I_IP, "role": "iran", "enabled": True}]
NODES = [{"id": 1, "name": "fr-1", "address": F_IP, "status": "connected", "core_config_id": 1}]
CORES = [core([ib("VLESS WS 2053", 8072, "ws"), ib("VLESS WS 2096", 8075, "ws"), ib("REALITY xhttp", 443, "xhttp", "reality"), ib("Trojan WS", 2087, "ws", "tls", "trojan"),
               ib("local fallback", 10001, listen="127.0.0.1"), ib("range", "1000-2000")])]
DNS = [{"account": "ar1", "domain": "cdn.example", "type": "A", "fqdn": f"{n}.cdn.example", "ips": [F_IP], "cloud": True, "port": 8072} for n in ("a", "b", "c")]
DNS += [{"account": "ar1", "domain": "cdn.example", "type": "A", "fqdn": "direct.cdn.example", "ips": [F_IP], "cloud": False, "port": None}]


def host(hid, address, tag, port=None, **kw):
    return {"id": hid, "remark": f"h{hid}", "address": address if isinstance(address, list) else [address], "port": port, "inbound_tag": tag, "is_disabled": False, "security": "inbound_default", **kw}


def build(hosts, dns=None, resolved=None, tunnels=None):
    return cfgmap.build(hosts, CORES, NODES, SERVERS, dns if dns is not None else DNS, resolved or {}, tunnels)


def by_id(m, hid):
    return next(h for h in m["hosts"] if h["id"] == hid)


def test_a_host_on_a_server_of_ours_is_its_port():
    m = build([host(1, F_IP, "REALITY xhttp"), host(2, F_IP, "VLESS WS 2053", 2053)])
    h = by_id(m, 1)
    assert h["kind"] == "direct" and h["srcs"] == ["p:1:443"] and h["server"] == "fr-1" and h["network"] == "xhttp" and h["security"] == "reality" and h["rare"] is True
    assert by_id(m, 2)["srcs"] == ["p:1:2053"]                                # the override is the port the user dials (a redirect on the server is counted by it)
    assert "redirect" in by_id(m, 2)["why"]
    assert m["servers"][1]["ports"] == [443, 2053, 2087, 8072, 8075]    # the inbounds that listen to the world or that a host uses, and the host's own port; the port range is left out


def test_a_name_that_resolves_to_a_server_and_an_iran_server_with_a_tunnel_port():
    m = build([host(1, "node.example", "REALITY xhttp"), host(2, I_IP, "REALITY xhttp", 50325), host(3, "direct.cdn.example", "Trojan WS")], resolved={"node.example": [F_IP]},
              tunnels=[{"name": "t-one", "iran_server_id": 2, "forwarded_ports": [50325, "50330", {"port": 1}, "x"]}])
    assert by_id(m, 1)["srcs"] == ["p:1:443"] and by_id(m, 1)["kind"] == "direct"
    h = by_id(m, 2)
    assert h["srcs"] == ["p:2:50325"] and h["server"] == "ir-1" and h["tunnel"] == "t-one"
    assert by_id(m, 3)["srcs"] == ["p:1:2087"]                                # a record that is not behind a CDN is its origin
    assert m["servers"][2]["ports"] == [50325]


def test_a_host_behind_a_cdn_is_its_domain_and_its_origin_port_is_shared():
    m = build([host(1, "a.cdn.example", "VLESS WS 2053", 2053), host(2, "b.cdn.example", "VLESS WS 2053", 2053), host(3, "c.cdn.example", "VLESS WS 2096", 2096)])
    a, b, c = by_id(m, 1), by_id(m, 2), by_id(m, 3)
    assert a["kind"] == "cdn" and a["srcs"] == ["c:ar1:a.cdn.example"] and a["origin_src"] == "p:1:8072" and a["cdn"]["origin_port"] == 8072 and a["cdn"]["domain"] == "cdn.example"
    assert c["origin_src"] == "p:1:8072"                                      # the DNS record says 8072 (the record's origin port wins over the inbound's)
    assert a["shared_with"] == [2, 3] and b["shared_with"] == [1, 3]
    assert [g["src"] for g in m["cdn"]] == ["c:ar1:a.cdn.example", "c:ar1:b.cdn.example", "c:ar1:c.cdn.example"]
    assert 8072 in m["servers"][1]["ports"]


def test_the_node_template_is_every_node_that_carries_the_inbound():
    m = build([host(1, "{SERVER_IP}", "VLESS WS 2096")])
    h = by_id(m, 1)
    assert h["kind"] == "direct" and h["srcs"] == ["p:1:8075"]
    m = build([host(2, "{SERVER_IP}", "no such inbound")])
    assert by_id(m, 2)["kind"] == "unknown" and "no node of ours carries" in by_id(m, 2)["why"]


def test_two_hosts_on_one_port_are_shared_and_unknown_addresses_say_why():
    m = build([host(1, F_IP, "Trojan WS"), host(2, "node.example", "Trojan WS"), host(3, "192.0.2.77", "Trojan WS"), host(4, "nowhere.example", "Trojan WS"), host(5, "", "Trojan WS"),
               host(6, F_IP, "no such inbound"), host(7, "wild.cdn.example", "VLESS WS 2053")], resolved={"node.example": [F_IP]},
              dns=DNS + [{"account": "ar1", "domain": "cdn.example", "type": "A", "fqdn": "*.cdn.example", "ips": [F_IP], "cloud": True, "port": 8075}])
    assert by_id(m, 1)["shared_with"] == [2] and by_id(m, 2)["shared_with"] == [1]
    assert by_id(m, 3)["kind"] == "unknown" and "not one of our servers" in by_id(m, 3)["why"]
    assert by_id(m, 4)["kind"] == "unknown" and "not in a DNS account" in by_id(m, 4)["why"]
    assert by_id(m, 5)["kind"] == "unknown" and by_id(m, 6)["kind"] == "unknown" and "no port of its own" in by_id(m, 6)["why"]
    w = by_id(m, 7)
    assert w["kind"] == "cdn" and w["cdn"]["origin_port"] == 8075                 # a wildcard record covers the name


def test_inbound_rows_list_their_ports_per_server():
    m = build([host(1, F_IP, "REALITY xhttp")])
    tags = {i["tag"]: i for i in m["inbounds"]}
    assert tags["REALITY xhttp"]["ports"] == [{"server_id": 1, "server": "fr-1", "port": 443, "src": "p:1:443"}] and tags["REALITY xhttp"]["used"] is True and tags["REALITY xhttp"]["rare"] is True
    assert tags["local fallback"]["ports"] == [] and tags["range"]["ports"] == []   # not public / no single port: no counter
    assert tags["VLESS WS 2053"]["ports"][0]["src"] == "p:1:8072"                    # public: counted even when no host uses it
    assert m["nodes"][0]["server_id"] == 1 and "REALITY xhttp" in m["nodes"][0]["tags"]


# ------------------------------------------------------------------ judgement
def test_trend_arrows_and_what_they_need():
    m = {"bytes": {"24h": 100, "7d": 600, "30d": 1000}, "prev": {"24h": 100, "7d": 1000, "30d": 0}}
    t = cfgstats.trends(m, 20.0)
    assert t["24h"] == {"pct": 0.0, "arrow": "flat", "needs": 0.0} and t["7d"]["arrow"] == "down" and t["7d"]["pct"] == -40.0
    assert t["30d"]["arrow"] == "" and t["30d"]["needs"] == 40.0                  # 60 days are needed
    assert cfgstats.trends(m, 0.5)["24h"]["needs"] == 1.5 and cfgstats.arrow_of(0.2) == "up" and cfgstats.arrow_of(None) == ""
    assert cfgstats.trends({"bytes": {"24h": 5, "7d": 5, "30d": 5}, "prev": {"24h": 0, "7d": 0, "30d": 0}}, 100)["24h"]["arrow"] == ""      # nothing to compare with


def test_falling_needs_the_days_and_something_that_fell_from():
    days = [0] * 30
    for i in range(15, 22):
        days[i] = 4 * GB
    for i in range(22, 29):
        days[i] = 1 * GB
    f = cfgstats.falling(days, 20.0)
    assert f["window"] == 7 and f["falling"] is True and f["pct"] == -75.0
    assert cfgstats.falling(days, 12.0)["window"] == 3 and cfgstats.falling(days, 5.0) is None
    tiny = [0] * 15 + [1000] * 7 + [10] * 7 + [0]
    assert cfgstats.falling(tiny, 20.0)["falling"] is False                          # fell, but from nothing worth speaking of
    flat = [0] * 15 + [4 * GB] * 14 + [0]
    assert cfgstats.falling(flat, 20.0)["falling"] is False
    assert cfgstats.little_threshold([{"daily": 10}, {"daily": 20}]) is None
    assert cfgstats.little_threshold([{"daily": 10}, {"daily": 20}, {"daily": 60}]) == pytest.approx(3.0)


class FakePanel:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.hosts = [host(i + 1, F_IP, tag) for i, tag in enumerate(["VLESS WS 2053", "VLESS WS 2096", "REALITY xhttp", "Trojan WS"])]
        self.cores = CORES
        self.nodes = NODES

    def __call__(self, c, method, path, **kw):
        self.calls.append((method, path))

        if path == "/api/hosts":
            return self.hosts

        if path == "/api/cores":
            return {"cores": self.cores}

        if path == "/api/nodes":
            return {"nodes": self.nodes}

        raise AssertionError(f"unexpected {path}")


@pytest.fixture
def world(monkeypatch):
    """A PasarGuard panel, a foreign server that carries its node and an Iran server, a fake panel, no DNS lookups, empty DNS cache."""
    with session() as db:
        p = Panel(name="pg-test", type="pasarguard", url="https://panel-pg.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        pid = p.id

    f, i = mk_server("fr-1", F_IP), mk_server("ir-1", I_IP, "iran")
    assert (f, i) == (1, 2)
    fake = FakePanel()
    monkeypatch.setattr(panels, "call", fake)
    monkeypatch.setattr(pg_nodes, "resolve_names", lambda names, timeout=3.0: {})
    monkeypatch.setattr(dnscache, "CACHE", {"at": 1.0, "rows": [], "accounts": [], "refreshing": False})
    monkeypatch.setattr(dnsprov, "accounts", list)
    cfgstats.forget()
    cfgstats._LAST.clear()                                                   # (the last good map of a panel lives in the process: a test starts without the last one of another test)
    cfgstats._PRUNED[0] = int(time.time())                                   # (the daily prune has its own test)
    return {"pid": pid, "fake": fake, "f": f, "i": i}


def meter_row(sid, ports, enabled=True, since=NOW - 30 * DAY):
    with session() as db:
        db.add(CfgMeter(server_id=sid, enabled=enabled, ports=ports, since=since, applied_at=since, last_ok=NOW - 100, state={}))


def seed_world(w):
    """Six hosts on six ports of the node (four heavy, one tiny, one that fell), a seventh and eighth on one shared port, a ninth that is nowhere; 25 days of hours; the meter of the node is on."""
    meter_row(w["f"], [443, 2087, 8072, 8075])
    w["fake"].cores = [core([ib(t, p) for t, p in (("A", 4001), ("B", 4002), ("C", 4003), ("D", 4004), ("E", 4005), ("F", 4006), ("G", 4007))])]
    w["fake"].hosts = [host(i + 1, F_IP, t, remark=f"config-{t}") for i, t in enumerate("ABCDEF")] + [host(7, F_IP, "G", remark="shared-one"), host(8, F_IP, "G", remark="shared-two"), host(9, "nowhere.example", "A", remark="<b>lost</b>")]
    meter_row(w["i"], [], enabled=False)
    with session() as db:
        db.query(CfgMeter).filter(CfgMeter.server_id == w["f"]).update({"ports": [4001, 4002, 4003, 4004, 4005, 4006, 4007]})

    rate = {"A": lambda d: 100 * GB, "B": lambda d: 80 * GB, "C": lambda d: 60 * GB, "D": lambda d: 50 * GB, "E": lambda d: 1 * GB, "F": lambda d: 10 * GB if d < 8 else 40 * GB, "G": lambda d: 30 * GB}

    for k, port in zip("ABCDEFG", range(4001, 4008)):
        fill(f"p:{w['f']}:{port}", 25, rate[k], conns=lambda d, h: 3)

    cfgstats.forget()


def test_snapshot_tells_little_used_falling_shared_and_no_data(world):
    w = world
    seed_world(w)
    snap = cfgstats.snapshot(w["pid"], None, False, NOW)
    rows = {r["id"]: r for r in snap["hosts"]}
    assert snap["summary"]["hosts"] == 9 and snap["summary"]["measured"] == 8
    assert "little" in rows[5]["badges"] and not any("little" in rows[i]["badges"] for i in (1, 2, 3, 4, 6))
    assert "falling" in rows[6]["badges"] and not any("falling" in rows[i]["badges"] for i in (1, 2, 3, 4, 5))
    assert rows[6]["view"]["fall"]["window"] == 7 and rows[6]["view"]["fall"]["pct"] == pytest.approx(-75.0, abs=4.0)
    assert rows[7]["badges"] == ["shared"] and rows[8]["badges"] == ["shared"] and rows[7]["shared_with"] == [8]       # no verdict on a number that is for two hosts
    assert rows[9]["state"] == "none" and "nodata" in rows[9]["badges"] and "not in a DNS account" in rows[9]["source_text"]
    r = rows[1]
    assert r["source"] == "port" and r["state"] == "ok" and r["meter"] == "on" and r["source_text"] == "port 4001 on fr-1"
    assert r["view"]["bytes"]["7d"] == pytest.approx(700 * GB, rel=1e-3) and r["view"]["peak"]["7d"] == 3 and r["view"]["trend"]["7d"]["arrow"] == "flat" and r["view"]["trend"]["30d"]["needs"] > 0
    assert snap["rules"]["little_threshold_bytes_per_day"] == pytest.approx(0.10 * (100 + 80 + 60 + 50 + 1 + 10) * GB / 6, rel=0.02)
    assert snap["total"]["has"] and snap["since"] == NOW - 25 * DAY + HOUR and snap["cov"] == pytest.approx(25.0, abs=0.1)
    s = {x["id"]: x for x in snap["servers"]}
    assert s[w["f"]]["enabled"] and s[w["f"]]["plan_ports"] == 7 and not s[w["f"]]["differs"]


def test_a_new_panel_with_no_history_says_off_and_wait(world):
    w = world
    snap = cfgstats.snapshot(w["pid"], None, False, NOW)
    assert snap["summary"]["measured"] == 0 and all(r["state"] == "off" and r["meter"] == "off" and "nodata" in r["badges"] for r in snap["hosts"])
    assert not snap["hosts"][0]["view"]["has"] and snap["since"] == 0 and snap["servers"][0]["enabled"] is False
    meter_row(w["f"], [443, 2087, 8072, 8075])
    snap = cfgstats.snapshot(w["pid"], None, True, NOW)
    assert all(r["state"] == "wait" for r in snap["hosts"]) and snap["servers"][0]["enabled"]


def test_a_cdn_host_uses_its_report_else_the_shared_origin_port(world, monkeypatch):
    w = world
    dns = [{"account": "ar1", "domain": "cdn.example", "type": "A", "fqdn": "a.cdn.example", "ips": [F_IP], "cloud": True, "port": 8072}]
    monkeypatch.setattr(dnscache, "CACHE", {"at": 1.0, "rows": dns, "accounts": [], "refreshing": False})
    w["fake"].hosts = [host(1, "a.cdn.example", "VLESS WS 2053", 2053)]
    cfgstats.forget()
    snap = cfgstats.snapshot(w["pid"], None, False, NOW)
    r = snap["hosts"][0]
    assert r["source"] == "origin" and r["state"] == "off" and r["cdn"]["origin_port"] == 8072
    meter_row(w["f"], [8072])
    fill(f"p:{w['f']}:8072", 10, lambda d: 50 * GB)
    cfgstats.forget()
    r = cfgstats.snapshot(w["pid"], None, False, NOW)["hosts"][0]
    assert r["source"] == "origin" and r["state"] == "ok" and "shared" in r["badges"] and r["view"]["bytes"]["24h"] > 0
    fill("c:ar1:a.cdn.example", 10, lambda d: 5 * GB)
    cfgstats.forget()
    r = cfgstats.snapshot(w["pid"], None, False, NOW)["hosts"][0]
    assert r["source"] == "cdn" and r["state"] == "ok" and r["view"]["bytes"]["24h"] == pytest.approx(5 * GB, rel=1e-3) and r["source_text"] == "the CDN report of a.cdn.example"


def test_snapshot_refuses_marzban(world):
    with session() as db:
        db.add(Panel(name="mz", type="marzban", url="https://panel-mz.example.test", username="a", secrets={"password": "x"}, status="up"))
        db.flush()
        mid = db.query(Panel).filter(Panel.name == "mz").one().id

    with pytest.raises(ValueError, match="PasarGuard"):
        cfgstats.snapshot(mid)


# ------------------------------------------------------------------ the CDN reports
ARVAN = {"data": {"charts": {"traffics": {"series": [{"name": "reports.traffics.total", "data": [100, 200, "300", None, "x"]}, {"name": "reports.traffics.saved", "data": [10, 20]}]},
                              "requests": {"series": []}}}}


def test_parse_arvan_adds_the_total_series_and_knows_a_shape_it_does_not_know():
    assert cfgcdn.parse_arvan(ARVAN) == {"total": 600, "saved": 30, "points": 3}
    assert cfgcdn.parse_arvan({"data": {"charts": {"traffics": {"series": [{"name": "x", "data": [5]}]}}}}) == {"total": 5, "saved": None, "points": 1}
    for bad in (None, {}, {"data": {}}, {"data": {"charts": {"traffics": {"series": []}}}}, [], "x", {"data": {"charts": {"traffics": {"series": [{"data": "x"}]}}}}):
        assert cfgcdn.parse_arvan(bad) in (None, {"total": 0, "saved": None, "points": 0})
    assert cfgcdn.parse_arvan({"data": {"charts": {"traffics": {"series": [{"name": "a.total", "data": [-5, float("nan")]}]}}}})["total"] == 0


def test_parse_cloudflare_groups_by_host_and_refuses_errors():
    j = {"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": [{"dimensions": {"clientRequestHTTPHost": "A.Example.com"}, "sum": {"edgeResponseBytes": 1500}},
                                                                   {"dimensions": {"clientRequestHTTPHost": "a.example.com"}, "sum": {"edgeResponseBytes": 500}}, {"dimensions": {}, "sum": {}}]}]}}}
    assert cfgcdn.parse_cf(j) == {"a.example.com": 2000}
    assert cfgcdn.parse_cf({"errors": [{"message": "not authorized"}]}) is None and cfgcdn.parse_cf({"data": {"viewer": {"zones": []}}}) is None and cfgcdn.parse_cf(None) is None
    assert cfgcdn.error_text({"errors": [{"message": "no access to analytics"}]}) == "no access to analytics"


def test_verdict_of_the_host_filter():
    assert cfgcdn.verdict(1000, 0, 400) == (True, "the host filter works")
    assert cfgcdn.verdict(1000, 1000, 1000)[0] is False
    assert cfgcdn.verdict(1000, 0, 2000)[0] is False
    assert cfgcdn.verdict(0, 0, 0)[0] is True and cfgcdn.verdict(None, 0, 0)[0] is None and cfgcdn.verdict(5, None, 1)[0] is None


class FakeArvan:
    """ArvanCloud's reports: the whole domain answers 1000, a host filter answers per host (or ignores the filter)."""

    def __init__(self, monkeypatch, by_host=None, ignore_filter=False, fail=None):
        self.by_host = by_host if by_host is not None else {"a.cdn.example": 400}
        self.ignore, self.fail, self.calls = ignore_filter, fail, []
        monkeypatch.setattr(arvan, "_req", self)

    def __call__(self, method, path, body=None, params=None):
        self.calls.append((method, path, dict(params or {})))
        assert method == "GET"

        if self.fail:
            raise RuntimeError(self.fail)

        if path.endswith("/reports/traffics"):
            sub = (params or {}).get("filter_subdomain")
            n = 1000 if (not sub or self.ignore) else self.by_host.get(sub, 0)
            return {"data": {"charts": {"traffics": {"series": [{"name": "reports.traffics.total", "data": [n]}]}}}}

        raise AssertionError(path)


def test_probe_arvan_finds_a_working_filter_or_says_it_is_ignored(monkeypatch):
    fa = FakeArvan(monkeypatch)
    p = cfgcdn.probe_arvan("k", "cdn.example", "a.cdn.example")
    assert p["ok"] and p["filter"] is True and p["total"] == 1000 and p["fake"] == 0 and p["host"] == 400 and p["style"] == "fqdn"
    assert all(c[0] == "GET" for c in fa.calls) and {"period": "24h", "filter_subdomain": "a.cdn.example"} in [c[2] for c in fa.calls]
    FakeArvan(monkeypatch, by_host={"a": 300})
    p = cfgcdn.probe_arvan("k", "cdn.example", "a.cdn.example")
    assert p["filter"] is True and p["style"] == "label" and p["host"] == 300             # the filter wants the label before the domain
    FakeArvan(monkeypatch, ignore_filter=True)
    p = cfgcdn.probe_arvan("k", "cdn.example", "a.cdn.example")
    assert p["ok"] and p["filter"] is False and "ignored" in p["text"]
    FakeArvan(monkeypatch, fail="ArvanCloud refused the key (403): check that the key is valid and has access to the CDN service")
    p = cfgcdn.probe_arvan("k", "cdn.example", "a.cdn.example")
    assert p["ok"] is False and p["filter"] is None and "refused" in p["text"]


def arvan_world(monkeypatch, **kw):
    acc = dnsprov.Account("ar1", "arvan", "Arvan", "key-1")
    monkeypatch.setattr(dnsprov, "accounts", lambda: [acc])
    return FakeArvan(monkeypatch, **kw), acc


GROUPS = [{"src": "c:ar1:a.cdn.example", "account": "ar1", "domain": "cdn.example", "fqdn": "a.cdn.example", "hosts": [1]},
          {"src": "c:ar1:b.cdn.example", "account": "ar1", "domain": "cdn.example", "fqdn": "b.cdn.example", "hosts": [2]}]


def test_collect_reads_each_domain_once_an_hour_and_remembers_the_account(monkeypatch):
    fa, _acc = arvan_world(monkeypatch, by_host={"a.cdn.example": 400, "b.cdn.example": 250})
    got: list = []
    add = lambda src, hour, rx, tx: got.append((src, hour, rx, tx))
    assert cfgcdn.collect(GROUPS, add, NOW) == {"ar1": "read"}
    hour = (NOW - 1800) // 3600 * 3600
    assert sorted(got) == [("c:ar1:a.cdn.example", hour, 0, 400), ("c:ar1:b.cdn.example", hour, 0, 250)]
    s = cfgcdn.state()["ar1"]
    assert s["ok"] and s["filter"] is True and s["verified"] is True and s["error"] == ""
    n = len(fa.calls)
    assert cfgcdn.collect(GROUPS, add, NOW + 600) == {"ar1": "skipped"} and len(fa.calls) == n          # not twice in an hour
    assert cfgcdn.collect(GROUPS, add, NOW + 3600)["ar1"] == "read"
    cfgcdn.set_enabled(False)
    assert cfgcdn.collect(GROUPS, add, NOW + 20000) == {}
    cfgcdn.set_enabled(True)


def test_collect_with_a_filter_that_does_nothing_stores_nothing(monkeypatch):
    arvan_world(monkeypatch, ignore_filter=True)
    got: list = []
    out = cfgcdn.collect(GROUPS, lambda *a: got.append(a), NOW)
    assert "ignored" in out["ar1"] and got == [] and cfgcdn.state()["ar1"]["filter"] is False
    assert cfgcdn.collect(GROUPS, lambda *a: got.append(a), NOW + 4000) == {"ar1": "skipped"}            # not tried again for a day
    assert got == []


def test_collect_survives_a_refused_key(monkeypatch):
    arvan_world(monkeypatch, fail="ArvanCloud refused the key (403)")
    out = cfgcdn.collect(GROUPS, lambda *a: None, NOW)
    assert "403" in out["ar1"] and cfgcdn.state()["ar1"]["ok"] is False


def test_collect_cloudflare_reads_one_query_per_zone(monkeypatch):
    acc = dnsprov.Account("cf1", "cloudflare", "CF", "tok-1")
    monkeypatch.setattr(dnsprov, "accounts", lambda: [acc])
    monkeypatch.setattr(cloudflare, "zones", lambda token: [{"id": "z1", "name": "cdn.example"}])
    seen = []

    def fake_req(token, method, path, body=None, params=None):
        seen.append((method, path, body["variables"]["z"]))
        return {"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": [{"dimensions": {"clientRequestHTTPHost": "a.cdn.example"}, "sum": {"edgeResponseBytes": 900}}]}]}}}

    monkeypatch.setattr(cloudflare, "req", fake_req)
    got: list = []
    groups = [{**g, "account": "cf1"} for g in GROUPS]
    assert cfgcdn.collect(groups, lambda *a: got.append(a), NOW) == {"cf1": "read"}
    assert seen == [("POST", "/graphql", "z1")]
    assert sorted(got) == [("c:ar1:a.cdn.example", (NOW - 1800) // 3600 * 3600, 0, 900), ("c:ar1:b.cdn.example", (NOW - 1800) // 3600 * 3600, 0, 0)]

    def refused(token, method, path, body=None, params=None):
        return {"errors": [{"message": "not authorized for that account"}], "data": None}

    monkeypatch.setattr(cloudflare, "req", refused)
    cfgcdn.set_enabled(True)
    out = cfgcdn.collect(groups, lambda *a: None, NOW + 4000)
    assert "not authorized" in out["cf1"] and cfgcdn.state()["cf1"]["ok"] is False


# ------------------------------------------------------------------ the routes
def test_routes_read_switch_and_refuse(client, world, monkeypatch):
    w = world
    base = f"/api/panels/{w['pid']}/pg/configs"
    r = client.get(base)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["summary"]["hosts"] == 4 and [s["name"] for s in r.json()["servers"]] == ["fr-1"]
    assert all(m == "GET" for m, _p in w["fake"].calls)                       # reading changes nothing on the panel

    # the process is read-only by default: the meter refuses with the reason
    r = client.post(base + "/meter", json={"server_id": w["f"], "on": True})
    assert r.status_code == 502 and "read-only" in r.json()["error"]
    monkeypatch.setattr(get_settings(), "read_only", False)
    box = FakeBox(monkeypatch)
    r = client.post(base + "/meter", json={"server_id": w["f"], "on": True})
    assert r.status_code == 200 and r.json()["server"] == "fr-1" and r.json()["ports"] == [443, 2087, 8072, 8075]
    assert client.get(base).json()["servers"][0]["enabled"] is True
    r = client.post(base + "/meter", json={"server_id": w["i"], "on": True})
    assert r.status_code == 400 and "no host or inbound" in r.json()["error"]
    r = client.post(base + "/meter", json={"server_id": w["f"], "on": False})
    assert r.status_code == 200 and not cfgmeter.get(w["f"])["enabled"]
    r = client.post(base + "/meter", json={"on": True})
    assert r.status_code == 200 and r.json()["results"] == {"fr-1": "ok"}
    r = client.post(base + "/meter", json={"on": False})
    assert r.json()["results"] == {"fr-1": "ok"} and not cfgmeter.get(w["f"])["enabled"]
    assert [n for n, _ in box.actions] == ["meter_apply", "meter_remove", "meter_apply", "meter_remove"]
    assert client.post(base + "/meter", json={"server_id": w["f"]}).status_code == 422
    assert client.get("/api/panels/9999/pg/configs").status_code == 400
    r = client.post(base + "/cdn", json={"on": False})
    assert r.json()["cdn_on"] is False and client.get(base).json()["cdn_on"] is False


def test_the_cdn_test_route(client, world, monkeypatch):
    w = world
    acc = dnsprov.Account("ar1", "arvan", "Arvan", "key-1")
    monkeypatch.setattr(dnsprov, "accounts", lambda: [acc])
    monkeypatch.setattr(dnsprov.ArvanProvider, "domains", lambda self: ["cdn.example"])
    FakeArvan(monkeypatch)
    r = client.post(f"/api/panels/{w['pid']}/pg/configs/cdn-test", json={"account": "ar1", "fqdn": "a.cdn.example"})
    j = r.json()
    assert r.status_code == 200 and j["ok"] and j["filter"] is True and j["domain"] == "cdn.example" and j["host"] == 400
    r = client.post(f"/api/panels/{w['pid']}/pg/configs/cdn-test", json={"account": "ar1", "fqdn": "a.other.example"})
    assert r.status_code == 400 and "not in a domain" in r.json()["error"]
    r = client.post(f"/api/panels/{w['pid']}/pg/configs/cdn-test", json={"account": "zz", "fqdn": "a.cdn.example"})
    assert r.status_code == 400


def test_routes_need_a_signed_in_user_and_are_described(client, world):
    from fastapi.testclient import TestClient

    from nikatel.api import pg_configs

    stranger = TestClient(client.app)
    assert stranger.get(f"/api/panels/{world['pid']}/pg/configs").status_code == 401
    assert stranger.post(f"/api/panels/{world['pid']}/pg/configs/meter", json={"on": False}).status_code == 401

    for r in pg_configs.router.routes:
        doc = (r.endpoint.__doc__ or "").strip()
        assert 20 < len(doc) <= 300 and doc.count(". ") == 0 and "/pg/configs" in r.path, r.path


# ------------------------------------------------------------------ the job
def test_the_job_gives_each_meter_its_turn_and_does_nothing_without_one(world, monkeypatch):
    w = world
    turns: list = []
    monkeypatch.setattr(cfgmeter, "tick", lambda sid, want, now: turns.append((sid, want)) or {"ok": True})
    cfgstats.run()
    assert turns == [] and not any(m == "POST" for m, _p in w["fake"].calls)
    n = len(w["fake"].calls)
    cfgstats.run()
    assert len(w["fake"].calls) == n                                         # no meter: the panel is not asked every five minutes (the CDN reports are due once an hour)
    cfgcdn.set_enabled(False)
    cfgstats._IDLE[0] = 0.0
    cfgstats.run()
    assert len(w["fake"].calls) == n                                         # and with the CDN reports off, not at all
    cfgcdn.set_enabled(True)
    meter_row(w["f"], [443])
    cfgstats.run()
    assert turns == [(w["f"], [443, 2087, 8072, 8075])]
    with session() as db:
        db.query(Panel).delete()

    turns.clear()
    cfgstats.forget()
    cfgstats.run()
    assert turns == []                                                       # no PasarGuard panel: nothing to do


def test_the_job_is_registered_and_the_tables_are_made():
    src = (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert 'jobs.register("cfgstats", 300, cfgstats.run)' in src
    from sqlalchemy import inspect

    from nikatel import db

    names = set(inspect(db.engine()).get_table_names())
    assert {"cfg_meters", "cfg_buckets"} <= names


# ------------------------------------------------------------------ the real shell script, with stubs for the commands and a fixture for conntrack
needs_bash = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("awk") is None, reason="bash and awk are needed")
CT_PROC = """ipv4     2 tcp      6 431996 ESTABLISHED src=10.9.1.2 dst=10.9.1.1 sport=44330 dport=50325 packets=5 bytes=300 src=10.9.2.2 dst=10.9.2.1 sport=9000 dport=44330 packets=4 bytes=500 [ASSURED] mark=0 use=1
ipv4     2 tcp      6 431996 ESTABLISHED src=10.9.1.3 dst=10.9.1.1 sport=44331 dport=50325 packets=5 bytes=300 src=10.9.2.2 dst=10.9.2.1 sport=9000 dport=44331 packets=4 bytes=500 [ASSURED] mark=0 use=1
ipv4     2 tcp      6 100 ESTABLISHED src=10.9.1.1 dst=198.51.100.77 sport=51000 dport=443 packets=5 bytes=300 src=198.51.100.77 dst=10.9.1.1 sport=443 dport=51000 packets=4 bytes=500 [ASSURED] mark=0 use=1
ipv4     2 tcp      6 60 TIME_WAIT src=10.9.1.4 dst=10.9.1.1 sport=44332 dport=50325 packets=5 bytes=300 src=10.9.2.2 dst=10.9.2.1 sport=9000 dport=44332 packets=4 bytes=500 [ASSURED] mark=0 use=1
ipv4     2 udp      17 20 src=10.9.1.5 dst=10.9.1.1 sport=44333 dport=50325 packets=1 bytes=60 [UNREPLIED] src=10.9.1.1 dst=10.9.1.5 sport=50325 dport=44333 packets=0 bytes=0 mark=0 use=1
"""
CT_TOOL = "\n".join(line.split(None, 2)[2] for line in CT_PROC.splitlines())          # `conntrack -L` has no l3 prefix


def run_read_script(tmp_path, ports, ct_text=None, ct_tool=None):
    from nikatel import ssh

    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "nft").write_text('#!/bin/bash\n[ "$1 $2 $3 $4" = "list table inet nikatel_meter" ] || exit 1\ncat <<\'EOF\'\ntable inet nikatel_meter {\n\tchain p8072 {\n\t\tct direction original counter packets 5 bytes 1234 comment "rx"\n'
                                '\t\tct direction reply counter packets 6 bytes 5678 comment "tx"\n\t}\n\tchain p50325 {\n\t\tct direction original counter packets 1 bytes 11 comment "rx"\n'
                                '\t\tct direction reply counter packets 2 bytes 22 comment "tx"\n\t}\n\tchain c_in {\n\t\ttype filter hook input priority -5; policy accept;\n\t}\n}\nEOF\n')
    (bindir / "ss").write_text('#!/bin/bash\ncase "$*" in\n *-Hltn*) printf "LISTEN 0 4096 0.0.0.0:8072 0.0.0.0:*\\nLISTEN 0 4096 [::]:8072 [::]:*\\nLISTEN 0 128 0.0.0.0:22 0.0.0.0:*\\n";;\n'
                               ' *established*) printf "0 0 10.9.1.1:8072 10.9.1.2:5000\\n0 0 [::ffff:10.9.1.1]:8072 [::ffff:10.9.1.9]:5001\\n0 0 10.9.1.1:51000 198.51.100.77:443\\n";;\nesac\n')
    (bindir / "ip").write_text('#!/bin/bash\nprintf "1: lo    inet 127.0.0.1/8 scope host lo\\n2: eth0    inet 10.9.1.1/24 brd 10.9.1.255 scope global eth0\\n"\n')

    if ct_tool:
        (bindir / "conntrack").write_text("#!/bin/bash\ncat <<'EOF'\n" + ct_tool + "EOF\n")

    for f in bindir.iterdir():
        f.chmod(0o755)

    env = {"PATH": f"{bindir}:/usr/bin:/bin", "NIKATEL_CT_FILE": str(tmp_path / "ct") if ct_text is not None else str(tmp_path / "no-such-file")}

    if ct_text is not None:
        (tmp_path / "ct").write_text(ct_text)

    script = ssh._script_text("meter_read", {"PORTS": " ".join(str(p) for p in ports)})
    return subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, timeout=30, env=env, check=False).stdout


@needs_bash
def test_the_read_script_with_stubbed_commands(tmp_path):
    out = cfgmeter.parse_answer(run_read_script(tmp_path, [8072, 443, 50325], ct_text=CT_PROC))
    assert out["done"] and out["table"] is True
    assert out["c"] == {8072: [1234, 5678], 50325: [11, 22]}
    assert out["listen"] == [8072] and out["s"] == {8072: 2}                                 # two sockets on the listener (an IPv4-mapped IPv6 one too); the outgoing :443 session is not counted
    assert out["ct"] is True and out["t"] == {50325: 2}                                    # two ESTABLISHED forwarded sessions; TIME_WAIT, UDP and the server's own :443 are not
    assert cfgmeter.conns_of(out, [8072, 443, 50325]) == {8072: 2, 443: 0, 50325: 2}


@needs_bash
def test_the_read_script_with_the_conntrack_tool_and_without_any(tmp_path):
    out = cfgmeter.parse_answer(run_read_script(tmp_path / "with-tool", [8072, 50325], ct_tool=CT_TOOL))
    assert out["ct"] is True and out["t"] == {50325: 2}
    out = cfgmeter.parse_answer(run_read_script(tmp_path / "bare", [8072, 50325]))
    assert out["ct"] is False and out["t"] == {} and cfgmeter.conns_of(out, [8072, 50325]) == {8072: 2}


def test_actions_are_declared_with_checked_parameters():
    from nikatel import ssh

    assert {"meter_apply", "meter_remove"} <= set(ssh.ACTIONS) and "meter_read" in ssh.SCRIPTS
    text = ssh.render_action("meter_apply", {"B64": cfgmeter.b64(cfgmeter.ruleset([8072]))})
    assert "nft -f -" in text and "nft delete" not in text and "@" not in text
    assert "nft delete table inet nikatel_meter" in ssh.render_action("meter_remove", {})
    with pytest.raises(ValueError):
        ssh.render_action("meter_apply", {"B64": "not base64!"})
    with pytest.raises(ValueError):
        ssh._script_text("meter_read", {"PORTS": "22; rm -rf /"})
    assert json.dumps(sorted(cfgstats.TREND_NEED)) == '["24h", "30d", "7d"]'
    assert st.get("cfgstats")["cdn"] is True


# ------------------------------------------------------------------ the page (pg_configs.js), run in node on the REAL answer of the service
WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
JS_RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const els = {}, calls = [], toasts = [], data = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const el = sel => els[sel] || (els[sel] = {innerHTML: "", textContent: "", hidden: false, dataset: {}, children: [], querySelectorAll: () => []});
const fmtB = b => { if (b == null) return "—"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; } return b.toFixed(i >= 3 ? 2 : 0) + " " + u[i]; };
const ctx = vm.createContext({window: {pgTabs: {}}, esc, $: el, console, document: {activeElement: null}, toast: (m, c) => toasts.push([m, c]), fmtB, pgBar: (l, r) => "BAR[" + l + "|" + r + "]", pnTabBtns: () => "TABS",
  pgApi: async (id, path, opts) => { calls.push([id, path, opts || null]); return path.startsWith("/configs") && !opts ? data : {ports: [1, 2], cdn_on: true, text: "tested", results: {"fr-1": "ok"}, ok: true}; }, gconfirm: async t => { calls.push(["confirm", t]); return true; }});
for (const f of [process.argv[1], process.argv[2]]) vm.runInContext(fs.readFileSync(f, "utf8"), ctx, {filename: f});
vm.runInContext("pgUsageState.err = {}", ctx);
Promise.resolve(eval("(" + process.argv[4] + ")")(ctx, els, src => vm.runInContext(src, ctx), data, toasts, calls)).then(v => process.stdout.write(JSON.stringify(v)));
"""


def run_cfg_js(fn_source: str, data=None, tmp: Path | None = None):
    f = (tmp or Path(__import__("tempfile").mkdtemp())) / "snap.json"
    f.write_text(json.dumps(data if data is not None else {}), encoding="utf-8")
    out = subprocess.run(["node", "-e", JS_RUNNER, str(WEB / "pg_usage.js"), str(WEB / "pg_configs.js"), str(f), fn_source], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_js_pure_helpers():
    got = run_cfg_js("""(c) => [c.pgCfgTrend({pct: 12.4, arrow: "up", needs: 0}), c.pgCfgTrend({pct: null, arrow: "", needs: 3.2}), c.pgCfgTrend(null), c.pgCfgTrend({pct: -35, arrow: "down", needs: 0}),
      c.pgCfgSort([{remark: "b", view: {has: true, bytes: {"7d": 5}}}, {remark: "a", view: {has: false}}, {remark: "c", view: {has: true, bytes: {"7d": 9}}}], "7d", -1).map(r => r.remark),
      c.pgCfgSort([{remark: "b", view: {has: true, bytes: {"7d": 5}}}, {remark: "a", view: {has: false}}, {remark: "c", view: {has: true, bytes: {"7d": 9}}}], "7d", 1).map(r => r.remark),
      c.pgCfgSort([{remark: "x", view: {has: true, bytes: {"24h": 1, "7d": 0}}}, {remark: "y", view: {has: true, bytes: {"24h": 0, "7d": 0}}}], "24h", -1).map(r => r.remark),
      c.pgCfgSort([{remark: "b"}, {remark: "A"}, {remark: "c"}], "7d", 1).map(r => r.remark), c.pgCfgSort(null, "7d", 1),
      c.pgCfgFilter([{remark: "One", badges: ["little"], tag: "t1"}, {remark: "Two", badges: ["falling", "shared"], tag: "t2"}], "falling").map(r => r.remark),
      c.pgCfgFilter([{remark: "One", badges: [], tag: "t1"}, {remark: "Two", badges: [], tag: "t2"}], "all").map(r => r.remark),
      c.pgCfgCounts([{badges: ["little"]}, {badges: ["falling", "shared"]}, {badges: []}]), c.pgCfgAgo(0, 100), c.pgCfgAgo(70, 100), c.pgCfgAgo(100, 400), c.pgCfgAgo(100, 100 + 7200), c.pgCfgAgo(100, 100 + 3 * 86400)]""")
    assert got[0] == {"glyph": "▲", "cls": "up", "text": "+12 %", "need": 0} and got[1]["need"] == 4 and got[1]["glyph"] == "" and got[2]["need"] == 0 and got[3] == {"glyph": "▼", "cls": "down", "text": "-35 %", "need": 0}
    assert got[4] == ["c", "b", "a"] and got[5] == ["b", "c", "a"]                                    # a row with no data is always last
    assert got[6] == ["x", "y"] and got[7] == ["A", "b", "c"] and got[8] == [] and got[9] == ["Two"] and got[10] == ["One", "Two"]
    assert got[11] == {"all": 3, "little": 1, "falling": 1, "nodata": 0, "shared": 1} and got[12] == "never" and got[13] == "just now" and got[14] == "5 min ago" and got[15] == "2 h ago" and got[16] == "3 days ago"
    row = run_cfg_js("""(c) => [c.pgCfgRow({remark: "<i>x</i>", badges: ["little", "shared"], shared_with: [2], rare: true, view: {has: true, bytes: {"24h": 5, "7d": 20, "30d": 40}, peak: {"7d": 9}, trend: {"7d": {pct: 12.4, arrow: "up", needs: 0}, "24h": {pct: null, arrow: "", needs: 1.2}}}, source_text: "port 443 on fr-1"}, {rules: {little_factor: 0.1}}, "7d", 40),
      c.pgCfgRow({remark: "none", badges: ["nodata"], view: {has: false}}, {}, "7d", 40), c.pgCfgRow({remark: "t", badges: ["falling"], view: {has: true, bytes: {"7d": 0}, trend: {}}}, {}, "7d", 0)]""")
    assert 'style="width:50.0%"' in row[0] and ">little used<" in row[0] and ">×2<" in row[0] and ">◆<" in row[0] and "▲ +12 %" in row[0] and "&lt;i&gt;x&lt;/i&gt;" in row[0] and "<i>x</i>" not in row[0]
    assert 'title="&lt;i&gt;x&lt;/i&gt; · 24 h: 5 B · 7 days: 20 B · 30 days: 40 B · Peak connections: 9 · port 443 on fr-1"' in row[0]
    assert "pg-cfg-r  none" in row[1] and ">—<" in row[1] and 'style="width:0.0%"' in row[1] and 'style="width:0.0%"' in row[2] and 'class="falling"' in row[2]


@pytest.fixture
def seeded(world):
    seed_world(world)
    with_apps = {"total": 100, "apps": [{"name": "V2Box", "count": 60, "percent": 60}, {"name": "v2rayNG", "count": 25, "percent": 25}, {"name": "Hiddify", "count": 15, "percent": 15}]}
    return world, cfgstats.snapshot(world["pid"], None, False, NOW), with_apps


@needs_node
def test_js_draws_the_real_answer_into_one_chart_with_marks_counters_and_apps(seeded, tmp_path):
    _w, snap, _apps = seeded
    got = run_cfg_js("""(c, els, run, d) => { run("Object.assign(pgUsageState, {id: 1, type: 'pasarguard', apps: null, sec: 'configs'})"); run("Object.assign(pgCfgState, {id: 1, d: " + JSON.stringify(d) + "})");
      const all = c.pgCfgSecHtml(); run("pgCfgState.filter = 'falling'"); const fall = c.pgCfgSecHtml(); run("pgCfgState.filter = 'all'; pgCfgState.win = '24h'; pgCfgState.dir = 1"); const day = c.pgCfgSecHtml();
      run("pgCfgState.win = '7d'; pgCfgState.dir = -1; pgCfgState.view = 'inbounds'"); const inb = c.pgCfgSecHtml(); run("pgCfgState.view = 'hosts'");
      return [all, fall, day, inb]; }""", snap, tmp_path)
    all_, fall, day, inb = got
    assert all_.count('class="pg-cfg-r ') == 9 and all_.count('<div class="pg-cfg-chart"') == 1 and "config-A" in all_ and "<table" not in all_ and "pg-cfg-spark" not in all_
    assert all_.count(">little used<") == 1 and all_.count(">falling<") == 1 and all_.count(">×2<") == 2 and "Reading the apps…" in all_
    assert "&lt;b&gt;lost&lt;/b&gt;" in all_ and "<b>lost</b>" not in all_                                                 # a name from the panel is never HTML
    assert "▲" not in all_ and "▬ " in all_ and "▼ -" in all_                                                              # the fall of config-F shows as a red arrow; the others are flat
    assert all_.index("config-A") < all_.index("config-B") < all_.index("config-C") < all_.index("config-D") < all_.index("lost")      # the most used first, a row with no data last
    assert 'style="width:100.0%"' in all_ and 'class="little"' in all_ and 'class="falling"' in all_ and 'class="shared"' in all_
    assert 'class="pg-cfg-chip on all"' in all_ and "All <b>9</b>" in all_ and "Little used <b>1</b>" in all_ and "Falling <b>1</b>" in all_ and "No data <b>1</b>" in all_ and "Shared" not in all_
    assert 'class="on" onclick="pgCfgWin(\'7d\')"' in all_ and "pgCfgWin('24h')" in all_ and "pgCfgWin('30d')" in all_ and 'onclick="pgCfgDir()"' in all_
    assert "Remove all meters" in all_ and "Turn on all" not in all_ and 'onclick="pgCfgMeter(1, false)"' in all_ and "counting 7 ports" in all_
    assert "the panel records which app" not in all_.lower() and "How it is judged" not in all_ and "A meter is ONE" not in all_       # no extra information on the page: the rest is in tips
    assert fall.count('class="pg-cfg-r ') == 1 and "config-F" in fall and "config-A" not in fall
    assert day.index("config-E") < day.index("config-A") and 'class="on" onclick="pgCfgWin(\'24h\')"' in day                         # the least used first, in the 24 hours
    assert "Usage of the inbounds" in inb and inb.count('class="pg-cfg-r ') == 7 and "pg-cfg-chips" not in inb


@needs_node
def test_js_a_switched_off_host_is_not_on_the_chart(seeded, tmp_path):
    _w, snap, _apps = seeded
    snap["hosts"][4]["disabled"] = True                                                                                  # config-E
    got = run_cfg_js("""(c, els, run, d) => { run("Object.assign(pgUsageState, {id: 1, type: 'pasarguard', apps: null})"); run("Object.assign(pgCfgState, {id: 1, d: " + JSON.stringify(d) + "})"); return [c.pgCfgSecHtml(), c.pgCfgRc(d)]; }""", snap, tmp_path)
    assert got[0].count('class="pg-cfg-r ') == 8 and "config-E" not in got[0] and "All <b>8</b>" in got[0] and "8 configs" in got[0] and "config-E" not in got[1]


@needs_node
def test_js_a_read_only_panel_shuts_the_buttons_and_a_silent_panel_is_told(seeded, tmp_path):
    _w, snap, _apps = seeded
    snap["read_only"] = True
    snap["panel_error"] = "no answer from the panel (ConnectError)"
    got = run_cfg_js("""(c, els, run, d) => { run("Object.assign(pgUsageState, {id: 1, type: 'pasarguard', apps: null})"); run("Object.assign(pgCfgState, {id: 1, d: " + JSON.stringify(d) + "})"); const ro = c.pgCfgSecHtml(); run("pgCfgState.d.read_only = false; pgCfgState.d.panel_error = ''"); return [ro, c.pgCfgSecHtml()]; }""", snap, tmp_path)
    ro, rw = got
    assert ro.count(" disabled ") >= 2 and "needs NIKATEL_READ_ONLY=false on the panel server" in ro and "The panel did not answer (no answer from the panel (ConnectError)): the numbers are those of the last reading" in ro
    assert " disabled" not in rw and "The panel did not answer" not in rw and "Delete the counting table from this server" in rw


@needs_node
def test_js_empty_fresh_and_switched_panel_states(seeded, tmp_path):
    _w, snap, _apps = seeded
    empty = {**snap, "hosts": [], "inbounds": [], "servers": [], "cdn": [], "total": {"has": False}, "since": 0, "summary": {**snap["summary"], "hosts": 0, "measured": 0}}
    fresh = {**snap, "since": 0, "total": {"has": False}, "servers": [{**snap["servers"][0], "enabled": False, "ports": 0}], "hosts": [{**h, "state": "off", "view": {"has": False}, "badges": ["nodata"]} for h in snap["hosts"]]}
    got = run_cfg_js("""(c, els, run, d, toasts, calls) => { const j = x => JSON.stringify(x); run("Object.assign(pgUsageState, {id: 1, type: 'pasarguard', apps: null})");
      run("Object.assign(pgCfgState, {id: 1, d: " + j(d.empty) + "})"); const e = c.pgCfgSecHtml(), ec = c.pgCfgRc(d.empty);
      run("Object.assign(pgCfgState, {id: 1, d: " + j(d.fresh) + "})"); const f = c.pgCfgSecHtml(), fc = c.pgCfgRc(d.fresh);
      run("Object.assign(pgUsageState, {id: 2})"); c.pgCfgDraw(); const other = run("pgCfgState.d"); const waits = els["#pg-usage-configs"].innerHTML;
      return [e, ec, f, fc, other, waits]; }""", {"empty": empty, "fresh": fresh}, tmp_path)
    e, ec, f, fc, other, waits = got
    assert "No config matches" in e and "No server of ours carries a node" in e and "undefined" not in e and "NaN" not in e and "0 configs" in e
    assert "Turn on the meters to see the usage" in ec and "<h3>Configs</h3>" in ec
    assert f.index("Counters") < f.index("Usage of the configs") and "Turn on all" in f and "Remove all meters" not in f and "NaN" not in f                # nothing counted yet: the counters come first
    assert "Turn on the meters to see the usage" in fc
    assert other is None and "Reading the configs" in waits                                                                                       # another panel starts from nothing

@needs_node
def test_js_the_apps_card_and_the_overview_card(seeded, tmp_path):
    _w, snap, apps = seeded
    got = run_cfg_js("""(c, els, run, d) => { run("Object.assign(pgUsageState, {id: 1, type: 'pasarguard', apps: " + JSON.stringify(__APPS__) + ", ov: null, range: null, top: null})"); run("Object.assign(pgCfgState, {id: 1, d: " + JSON.stringify(d) + "})");
      const card = c.pgCfgAppsHtml(); c.pgUsageDrawCards(); const ov = els["#pg-usage-ov"].innerHTML; c.pgCfgDraw(); const sec = els["#pg-usage-configs"].innerHTML;
      run("pgCfgState.d = null"); c.pgUsageDrawCards(); const wait = els["#pg-usage-ov"].innerHTML; run("Object.assign(pgUsageState, {type: 'marzban'})"); c.pgUsageDrawCards();
      return [card, ov, sec, wait, els["#pg-usage-ov"].innerHTML, run("pgUsageSecCur()")]; }""".replace("__APPS__", json.dumps(apps)), snap, tmp_path)
    card, ov, sec, wait, mz, _cur = got
    assert "V2Box" in card and "v2rayNG" in card and "Hiddify" in card and "60%" in card and "100" in card and "pg-ring" in card and "<small class=\"mut\">The" not in card and "cannot be split" not in card          # a ring and its legend, no note
    assert ov.count('class="card pg-rc') >= 3 and "<h3>Configs</h3>" in ov and "little used" in ov and "falling" in ov and "config-E" in ov and "config-F" in ov and "NaN" not in ov and "undefined" not in ov
    assert 'class="card pg-usage-card' in sec and "Usage of the configs" in sec and "Counters" in sec and "Client apps" in sec
    assert 'class="card pg-rc pg-rc-sk"' in wait and "<h3>Configs</h3>" in wait                                      # its place from the first frame, waiting
    assert "<h3>Configs</h3>" not in mz                                                                                # Marzban has no such card


@needs_node
def test_js_the_buttons_ask_first_and_post_what_they_say(seeded, tmp_path):
    _w, snap, _apps = seeded
    got = run_cfg_js("""async (c, els, run, d, toasts, calls) => { run("Object.assign(pgUsageState, {id: 7, type: 'pasarguard'})"); run("Object.assign(pgCfgState, {id: 7, d: " + JSON.stringify(d) + "})");
      await run("window.pgCfgMeter(1, false)"); await run("window.pgCfgMeterAll(true)"); await run("window.pgCfgCdnSet(false)"); await run("window.pgCfgCdnTest('ar1', 'a.cdn.example')");
      return [calls.map(x => x[0] === "confirm" ? ["confirm", x[1]] : [x[0], x[1], x[2]]), toasts.map(t => t[1])]; }""", snap, tmp_path)
    calls, kinds = got
    posts = [c for c in calls if c[0] == 7 and c[2]]
    assert [c[1] for c in posts] == ["/configs/meter", "/configs/meter", "/configs/cdn", "/configs/cdn-test"]
    assert json.loads(posts[0][2]["body"] if isinstance(posts[0][2]["body"], str) else json.dumps(posts[0][2]["body"])) == {"server_id": 1, "on": False}
    assert posts[1][2]["body"] == {"on": True} and posts[2][2]["body"] == {"on": False} and posts[3][2]["body"] == {"account": "ar1", "fqdn": "a.cdn.example"}
    assert all(c[2]["method"] == "POST" for c in posts)
    assert next(c for c in calls if c[0] == "confirm")[1].startswith("Remove the counting table from fr-1") and "numbers collected so far stay" in calls[0][1]
    assert set(kinds) == {"ok"} and any(c[1] == "/configs?refresh=true" for c in calls if c[0] == 7)                       # after each action the page asks for fresh numbers


@needs_node
def test_js_hooks_into_the_usage_tab_and_is_loaded():
    sec = run_cfg_js("""(c, els, run) => [run("PGU_SECS.map(x => x[0])"), run("PGU_SECS.find(x => x[0] === 'configs')"), run("pgUsageSkeleton()").includes('data-for="configs" id="pg-usage-configs"'), typeof c.pgCfgRc, typeof c.pgCfgKick]""")
    assert sec[0] == ["ov", "traffic", "nodes", "configs", "users", "apps", "look"] and sec[1] == ["configs", "Configs", False] and sec[2] is True and sec[3] == "function" and sec[4] == "function"
    index = (WEB / "index.html").read_text(encoding="utf-8")
    assert index.index("pg_usage.js") < index.index("pg_configs.js") < index.index("pg_update.js") and '<link rel="stylesheet" href="/static/pg_configs.css">' in index
    assert index.rindex('href="/static/uikit.css"') > index.rindex("pg_configs.css")                                       # the kit's look is laid last
    main = (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert '"pg_configs.js"' in main and '"pg_configs.css"' in main


# ------------------------------------------------------------------ Persian: every sentence of the section (and of the overview card) has a translation
PERSIAN_RUNNER = r"""
const fs = require("fs"), vm = require("vm"), path = require("path");
const web = process.argv[1];
class MO { observe() {} disconnect() {} takeRecords() { return []; } }
const fa = {localStorage: {getItem: k => k === "nk.lang" ? "fa" : null, setItem() {}}, document: {addEventListener() {}, documentElement: {}, querySelectorAll: () => [], body: {}}, MutationObserver: MO, NodeFilter: {}, queueMicrotask, console, setTimeout, clearTimeout};
fa.window = fa; vm.createContext(fa);
for (const f of ["i18n.js", "i18n.fa.ui.js", "i18n.fa.msg.js", "i18n.fa.doctor.js", "i18n.fa.ui2.js"]) vm.runInContext(fs.readFileSync(path.join(web, f), "utf8"), fa, {filename: f});
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const els = {}, el = sel => els[sel] || (els[sel] = {innerHTML: "", textContent: "", hidden: false, dataset: {}, children: [], querySelectorAll: () => []});
const fmtB = b => { if (b == null) return "—"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; } return b.toFixed(i >= 3 ? 2 : 0) + " " + u[i]; };
const ctx = vm.createContext({window: {pgTabs: {}}, esc, $: el, console, document: {activeElement: null}, toast: () => {}, fmtB, pgBar: () => "", pnTabBtns: () => "", pgApi: async () => ({}), gconfirm: async () => true});
for (const f of ["pg_usage.js", "pg_configs.js"]) vm.runInContext(fs.readFileSync(path.join(web, f), "utf8"), ctx, {filename: f});
const run = src => vm.runInContext(src, ctx), data = JSON.parse(fs.readFileSync(process.argv[2], "utf8")), apps = {total: 10, apps: [{name: "V2Box", count: 6, percent: 60}, {name: "Hiddify", count: 4, percent: 40}]};
run("Object.assign(pgUsageState, {id: 1, type: 'pasarguard', err: {}, apps: " + JSON.stringify(apps) + ", range: null, ov: null, top: null})");
run("Object.assign(pgCfgState, {id: 1, d: " + JSON.stringify(data) + "})");
const html = [];
for (const [filter, view] of [["all", "hosts"], ["little", "hosts"], ["falling", "hosts"], ["nodata", "hosts"], ["shared", "hosts"], ["all", "inbounds"]]) { run(`pgCfgState.filter = ${JSON.stringify(filter)}; pgCfgState.view = ${JSON.stringify(view)}`); html.push(ctx.pgCfgSecHtml()); }
run("pgCfgState.view = 'hosts'; pgCfgState.filter = 'all'; pgCfgState.d.servers.forEach(s => { s.enabled = false; }); pgCfgState.d.since = 0"); html.push(ctx.pgCfgSecHtml());
ctx.pgUsageDrawCards(); html.push(els["#pg-usage-ov"].innerHTML);
const dec = s => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
const texts = new Set();
for (const h of html) {
  for (const m of h.matchAll(/>([^<>]+)</g)) texts.add(dec(m[1]).trim());
  for (const m of h.matchAll(/\b(?:title|placeholder|aria-label)="([^"]*)"/g)) texts.add(dec(m[1]).trim());
}
const left = [];
for (const t of texts) { if (t && /[A-Za-z]{3}/.test(t) && fa.I18N.tr(t) === t) left.push(t); }
process.stdout.write(JSON.stringify({left, n: texts.size, sample: fa.I18N.tr("Little used"), dlg: fa.I18N.tr("Put the counting table on fr-3? It only counts the bytes and connections of its 28 ports: nothing is stopped, redirected or restarted, and Remove deletes it."), many: fa.I18N.tr("Put the counting table on 3 servers? It only counts the bytes and connections of the listed ports: nothing is stopped, redirected or restarted, and Remove deletes it."), ago: fa.I18N.tr("counting 9 ports · last read 6 min ago · last read never"), toast: fa.I18N.tr("✓ fr-3: counting 28 ports")}));
"""


@needs_node
def test_persian_has_every_text_of_the_section_and_the_card(seeded, tmp_path):
    _w, snap, _apps = seeded
    base = snap["servers"][0]
    snap["servers"] = [{**base, "stale": True}, {**base, "id": 2, "name": "fr-2", "differs": True, "last_error": "ssh connection lost (network)"}, {**base, "id": 3, "name": "fr-3", "enabled": False, "ports": 0, "last_ok": 0}]
    snap["cdn"] = [{"id": "ar1", "name": "ArvanCloud", "provider": "arvan", "domains": ["cdn.example"], "hosts": 3, "sample": "a.cdn.example", "state": {"ok": True, "verified": True, "at": NOW - 600}},
                   {"id": "ar2", "name": "Other", "provider": "arvan", "domains": ["x.example"], "hosts": 1, "sample": "b.x.example", "state": {"filter": False, "text": "ArvanCloud ignored the host filter (a host that does not exist got the whole domain): per-host numbers are not available"}},
                   {"id": "cf1", "name": "CF", "provider": "cloudflare", "domains": ["y.example"], "hosts": 2, "sample": "c.y.example", "state": {}}]
    f = tmp_path / "snap.json"
    f.write_text(json.dumps(snap), encoding="utf-8")
    out = subprocess.run(["node", "-e", PERSIAN_RUNNER, str(WEB), str(f)], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # what the data itself says (names, tags, addresses, numbers) stays as it is
    words: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            words.update(re.findall(r"[A-Za-z][A-Za-z0-9_.\-]*", v))

    walk(snap)
    allow = words | {"GB", "MB", "TB", "KB", "CDN", "xHTTP", "HTTPUpgrade", "nftables", "conntrack", "IP", "DNS", "nft", "REALITY", "vless", "tcp", "ws", "V2Box", "Hiddify"}
    other_cards = {"Apps", "Open Apps", "Subscription apps"}                                                 # the existing Apps card of the overview: not this section's text
    left = [t for t in got["left"] if t not in other_cards and not all(tok in allow for tok in re.findall(r"[A-Za-z][A-Za-z0-9_.\-]*", t))]
    assert got["n"] > 80, "the scan found too few texts"
    assert not left, "no Persian text for:\n" + "\n".join(sorted(left))
    assert got["sample"] == "کم‌مصرف" and got["dlg"].startswith("روی fr-3 جدول شمارش گذاشته شود؟") and "28 پورتش" in got["dlg"] and got["many"].startswith("روی 3 سرور جدول شمارش گذاشته شود؟")
    assert got["ago"] == "در حال شمارش 9 پورت · آخرین خواندن 6 دقیقه پیش · آخرین خواندن هرگز" and got["toast"].endswith("fr-3: شمارش 28 پورت")
