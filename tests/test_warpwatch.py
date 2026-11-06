"""The WARP watch (services/warpwatch.py, 1.69.228; 1.69.239 patient and loud): an AI config down for 7 minutes with its tunnel and node up gets that node reconnected - up to six times a day, each wait longer
(15, 30, 60, then 120 minutes), never a guess; the Doctor page carries a flag for every config it works on, the "needs a look by hand" event is repeated every three hours, and a config that stays up drops its flag."""
import time

import pytest

from nikatel.db import session
from nikatel.models import Config, ConfigSample, Panel, Server, Tunnel
from nikatel.services import dnscache, panels, warpwatch
from nikatel.services import settings as st

NOW = 1_800_000_000
PLACED = {"AI-2": {"key": "1:11", "panel_id": 1, "node_id": 11, "node_name": "DE2", "node_status": "connected", "tunnel_state": "up"}}


def down(name="AI-2", for_s=600):
    return {"name": name, "since": NOW - for_s}


def test_a_config_is_acted_on_only_after_it_was_down_long_enough_and_everything_around_it_is_up():
    assert warpwatch.decide([down(for_s=180)], PLACED, {}, NOW) == ({}, [])                              # three minutes: a lost test, nothing to say
    assert warpwatch.decide([down(for_s=480)], PLACED, {}, NOW) == ({"1:11": ["AI-2"]}, [])
    assert warpwatch.is_ai("AI-1") and warpwatch.is_ai("ai-2") and warpwatch.is_ai("AI2") and not warpwatch.is_ai("TCP-1") and not warpwatch.is_ai("Maintain") and not warpwatch.is_ai("Cloud-3")

    todo, held = warpwatch.decide([down()], {}, {}, NOW)
    assert todo == {} and "exactly one node" in held[0][1] and held[0][2] == "held"                    # not placed for sure: not touched, and flagged: nobody watches it
    todo, held = warpwatch.decide([down()], {"AI-2": {**PLACED["AI-2"], "node_status": "disconnected"}}, {}, NOW)
    assert todo == {} and "not connected" in held[0][1] and held[0][2] == ""                            # the panel's own reconnect handles a node that dropped: nothing to flag
    todo, held = warpwatch.decide([down()], {"AI-2": {**PLACED["AI-2"], "tunnel_state": "down"}}, {}, NOW)
    assert todo == {} and "tunnel is down" in held[0][1] and held[0][2] == ""                           # a dead tunnel is not the node's fault


def test_a_node_waits_longer_after_each_reconnect_and_is_reconnected_six_times_a_day_at_most():
    assert [warpwatch.cooldown_for(n) for n in (1, 2, 3, 4, 5, 6, 9)] == [900, 1800, 3600, 7200, 7200, 7200, 7200]
    todo, held = warpwatch.decide([down()], PLACED, {"1:11": [NOW - 600]}, NOW)
    assert todo == {} and "10 minutes ago" in held[0][1] and held[0][2] == "working"
    again = {"1:11": ["AI-2"]}
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 901]}, NOW)[0] == again                                           # after the first: fifteen minutes
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 9000, NOW - 1000]}, NOW)[0] == {}                                 # after the second: thirty
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 9000, NOW - 1801]}, NOW)[0] == again
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 20000, NOW - 10000, NOW - 3000]}, NOW)[0] == {}                  # after the third: an hour
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 20000, NOW - 10000, NOW - 3601]}, NOW)[0] == again
    four = [NOW - 40000, NOW - 30000, NOW - 20000]
    assert warpwatch.decide([down()], PLACED, {"1:11": [*four, NOW - 5000]}, NOW)[0] == {}                                      # after the fourth and later: two hours
    assert warpwatch.decide([down()], PLACED, {"1:11": [*four, NOW - 7201]}, NOW)[0] == again
    five = [NOW - 80000, NOW - 70000, NOW - 60000, NOW - 50000, NOW - 40000]
    assert warpwatch.decide([down()], PLACED, {"1:11": five}, NOW)[0] == again                                                  # the sixth is still allowed
    todo, held = warpwatch.decide([down()], PLACED, {"1:11": [*five, NOW - 600]}, NOW)
    assert todo == {} and held[0][2] == "working"                                                                               # the sixth was just made: wait and see whether it holds
    todo, held = warpwatch.decide([down()], PLACED, {"1:11": [*five, NOW - 30000]}, NOW)
    assert todo == {} and "6 times in the last 24 hours" in held[0][1] and held[0][2] == "hand"                                 # the day's tries are used
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 90000, NOW - 89000, NOW - 88000, NOW - 87000, NOW - 86500, NOW - 86450]}, NOW)[0] == again      # older than a day does not count


def test_the_flag_says_the_tries_used_and_when_the_next_one_is_allowed():
    f = warpwatch.flag_of("working", "", PLACED["AI-2"], [NOW - 5000, NOW - 100], NOW - 7000)
    assert f == {"state": "working", "node": "DE2", "since": NOW - 7000, "tries": 2, "max": 6, "last": NOW - 100, "next": NOW - 100 + 1800, "why": ""}
    assert warpwatch.flag_of("hand", "x", PLACED["AI-2"], [NOW - t for t in range(100, 700, 100)], NOW)["next"] == 0               # six used: none will follow
    assert warpwatch.flag_of("held", "y", None, [], NOW) == {"state": "held", "node": "", "since": NOW, "tries": 0, "max": 6, "last": 0, "next": 0, "why": "y"}


def test_two_configs_down_on_one_node_reconnect_it_once():
    both = {"AI-1": PLACED["AI-2"], "AI-2": PLACED["AI-2"]}
    todo, _ = warpwatch.decide([down("AI-1"), down("AI-2")], both, {}, NOW)
    assert todo == {"1:11": ["AI-1", "AI-2"]}


def _tables():
    with session() as db:
        iran, foreign = Server(name="ir-w", host="203.0.113.1", role="iran", online=True), Server(name="fx-w", host="203.0.113.2", role="foreign", online=True)
        db.add_all([iran, foreign])
        db.flush()
        db.add(Tunnel(name="de-ir-w", type="backhaul", transport="tcpmux", iran_server_id=iran.id, foreign_server_id=foreign.id, state="up", forwarded_ports=[1830, 1819]))
        db.add(Panel(name="main-w", type="pasarguard", url="https://p.example.net", nodes=[
            {"id": 11, "name": "DE2", "address": "203.0.113.2", "status": "connected"},
            {"id": 27, "name": "ECO-DE2", "address": "203.0.113.2", "status": "connected"},          # the economy node of the same host is another Xray
            {"id": 5, "name": "US", "address": "203.0.113.99", "status": "connected"}]))


def test_the_placement_finds_the_one_non_economy_node_at_the_end_of_the_tunnel(monkeypatch):
    _tables()
    monkeypatch.setitem(dnscache.CACHE, "rows", [{"fqdn": "ai-de.example.com", "type": "A", "ips": ["203.0.113.1"], "port": None}])
    cfg = {"name": "AI-2", "tunnel": "", "meta": {"address": "ai-de.example.com", "port": 1830}}
    got = warpwatch._where([cfg])
    assert got["AI-2"]["node_id"] == 11 and got["AI-2"]["node_name"] == "DE2" and got["AI-2"]["tunnel_state"] == "up" and got["AI-2"]["key"].endswith(":11")
    assert warpwatch._where([{**cfg, "meta": {"address": "ai-de.example.com", "port": 9999}}]) == {}          # no tunnel forwards that port: not placed
    monkeypatch.setitem(dnscache.CACHE, "rows", [])
    assert warpwatch._where([cfg]) == {}                                                                     # no DNS records read: nothing guessed

    with session() as db:                                                                                   # two plain nodes on one address: ambiguous, not touched
        p = db.query(Panel).one()
        p.nodes = [*p.nodes, {"id": 30, "name": "DE2-b", "address": "203.0.113.2", "status": "connected"}]

    monkeypatch.setitem(dnscache.CACHE, "rows", [{"fqdn": "ai-de.example.com", "type": "A", "ips": ["203.0.113.1"], "port": None}])
    assert warpwatch._where([cfg]) == {}


@pytest.fixture
def watch(client, monkeypatch):
    """A panel with AI-2 down for ten minutes, the placement fixed, the reconnect recorded."""
    class S:
        read_only = False

    calls = []
    monkeypatch.setattr(warpwatch.jobs, "stopping", lambda: False)                          # (an earlier test's app shutdown leaves the stop flag set)
    monkeypatch.setattr(warpwatch, "get_settings", lambda: S())
    monkeypatch.setattr(warpwatch, "_where", lambda cfgs: {c["name"]: PLACED["AI-2"] for c in cfgs})
    monkeypatch.setattr(panels, "cred_for", lambda pid: {"id": pid})
    monkeypatch.setattr(panels, "node_reconnect", lambda c, nid: calls.append((c["id"], nid)))

    with session() as db:
        db.add(Config(name="AI-2", status="down", status_since=int(time.time()) - 600, enabled=True, meta={"address": "ai-de.example.com", "port": 1830}))
        db.add(Config(name="TCP-1", status="down", status_since=int(time.time()) - 3600, enabled=True, meta={}))        # down, but no AI config: never touched

    return calls


def events(client):
    return [e["text"] for e in client.get("/api/events?domain=configs").json()["events"]]


def test_an_ai_config_down_for_ten_minutes_gets_its_node_reconnected_once(client, watch):
    warpwatch.run()
    assert watch == [(1, 11)]
    assert any("Node DE2 reconnected" in t and "AI-2" in t for t in events(client))
    warpwatch.run()
    warpwatch.run()
    assert watch == [(1, 11)]                                                                 # the cooldown holds: one blip, not one a minute
    assert len(st.get("warpwatch")["done"]["1:11"]) == 1
    f = st.get("warpwatch")["flags"]["AI-2"]                                                  # the Doctor page shows what the watch does about it
    assert f["state"] == "working" and f["node"] == "DE2" and f["tries"] == 1 and f["max"] == 6 and 880 < f["next"] - int(time.time()) <= 900


def test_nothing_happens_for_a_short_outage_a_read_only_panel_or_a_switch_that_is_off(client, watch, monkeypatch):
    with session() as db:
        db.query(Config).filter(Config.name == "AI-2").one().status_since = int(time.time()) - 120

    warpwatch.run()
    assert watch == []

    with session() as db:
        db.query(Config).filter(Config.name == "AI-2").one().status_since = int(time.time()) - 900

    class RO:
        read_only = True

    monkeypatch.setattr(warpwatch, "get_settings", lambda: RO())
    warpwatch.run()
    assert watch == []
    monkeypatch.setattr(warpwatch, "get_settings", lambda: type("S", (), {"read_only": False})())
    assert client.put("/api/settings/warpwatch", json={"on": False}).json()["on"] is False
    warpwatch.run()
    assert watch == []
    assert client.put("/api/settings/warpwatch", json={"on": True}).json()["on"] is True
    warpwatch.run()
    assert watch == [(1, 11)]


def test_a_failing_reconnect_is_not_recorded_and_is_tried_again(client, watch, monkeypatch):
    def broken(c, nid):
        raise panels.PanelError("the panel said no")

    monkeypatch.setattr(panels, "node_reconnect", broken)
    warpwatch.run()
    assert st.get("warpwatch")["done"] == {} and not any("reconnected" in t for t in events(client))
    monkeypatch.setattr(panels, "node_reconnect", lambda c, nid: watch.append((c["id"], nid)))
    warpwatch.run()
    assert watch == [(1, 11)]


def told(client):
    return [t for t in events(client) if "still down" in t and "needs a look by hand" in t]


def test_a_config_still_down_after_six_reconnects_is_flagged_and_told_again_every_three_hours(client, watch):
    now = int(time.time())
    st.put("warpwatch", {"done": {"1:11": [now - h * 3600 for h in (9, 8, 7, 6, 5, 4)]}})
    warpwatch.run()
    warpwatch.run()
    assert watch == []
    f = st.get("warpwatch")["flags"]["AI-2"]
    assert f["state"] == "hand" and f["tries"] == 6 and f["next"] == 0 and "6 times" in f["why"]
    assert len(told(client)) == 1 and "every 3 hours" in told(client)[0]                         # two runs, one event
    st.put("warpwatch", {"told": {"AI-2": now - warpwatch.REMIND + 600}})
    warpwatch.run()
    assert len(told(client)) == 1                                                                 # not yet
    st.put("warpwatch", {"told": {"AI-2": now - warpwatch.REMIND - 5}})
    warpwatch.run()
    assert len(told(client)) == 2 and watch == []                                                 # three hours later it says so again; still no seventh reconnect


def test_the_sixth_reconnect_is_given_time_to_hold_before_the_watch_gives_up(client, watch):
    now = int(time.time())
    st.put("warpwatch", {"done": {"1:11": [now - 9 * 3600, now - 8 * 3600, now - 7 * 3600, now - 6 * 3600, now - 5 * 3600, now - 600]}})
    warpwatch.run()
    assert watch == [] and st.get("warpwatch")["flags"]["AI-2"]["state"] == "working" and told(client) == []


def test_a_config_that_stays_up_drops_its_flag_and_an_event_says_it_recovered(client, watch):
    warpwatch.run()
    assert "AI-2" in st.get("warpwatch")["flags"]

    with session() as db:
        c = db.query(Config).filter(Config.name == "AI-2").one()
        c.status, c.status_since = "up", int(time.time()) - 120

    warpwatch.run()
    assert "AI-2" in st.get("warpwatch")["flags"]                                                 # up for two minutes: it may fall again, the flag stays

    with session() as db:
        db.query(Config).filter(Config.name == "AI-2").one().status_since = int(time.time()) - warpwatch.BACK_FOR - 5

    warpwatch.run()
    warpwatch.run()
    d = st.get("warpwatch")
    assert d["flags"] == {} and d["told"] == {}
    assert sum("AI-2 is back up" in t and "reconnected 1 time(s)" in t for t in events(client)) == 1       # said once


def test_a_config_the_watch_cannot_place_is_flagged_as_not_watched_but_a_dead_tunnel_is_not_flagged(client, watch, monkeypatch):
    monkeypatch.setattr(warpwatch, "_where", lambda cfgs: {})
    warpwatch.run()
    f = st.get("warpwatch")["flags"]["AI-2"]
    assert watch == [] and f["state"] == "held" and "exactly one node" in f["why"] and f["node"] == ""
    st.put("warpwatch", {"flags": {}})
    monkeypatch.setattr(warpwatch, "_where", lambda cfgs: {"AI-2": {**PLACED["AI-2"], "tunnel_state": "down"}})
    warpwatch.run()
    assert watch == [] and st.get("warpwatch")["flags"] == {}                                    # the tunnel's own repair deals with that


def test_the_flag_of_a_config_that_is_gone_is_dropped(client, watch):
    st.put("warpwatch", {"flags": {"AI-9": warpwatch.flag_of("hand", "x", PLACED["AI-2"], [], NOW)}})
    warpwatch.run()
    assert "AI-9" not in st.get("warpwatch")["flags"] and "AI-2" in st.get("warpwatch")["flags"]


def test_the_switch_reports_the_reconnects_of_the_last_day_and_the_flags_and_the_doctor_page_gets_them(client):
    now = time.time()
    st.put("warpwatch", {"done": {"1:11": [now - 100, now - 90000]}})
    r = client.get("/api/settings/warpwatch").json()
    assert r["on"] is True and r["reconnects_24h"] == {"1:11": 1} and r["flags"] == []
    st.put("warpwatch", {"flags": {"AI-2": warpwatch.flag_of("hand", "x", PLACED["AI-2"], [int(now) - 100], int(now) - 3000)}})
    r = client.get("/api/settings/warpwatch").json()
    assert [f["name"] for f in r["flags"]] == ["AI-2"] and r["flags"][0]["state"] == "hand" and r["flags"][0]["since"] == int(now) - 3000
    assert client.get("/api/doctor").json()["warp"] == r["flags"]                                  # the Tunnel Doctor page draws them
    client.put("/api/settings/warpwatch", json={"on": False})
    assert client.get("/api/doctor").json()["warp"] == [] and client.get("/api/settings/warpwatch").json()["flags"] == []          # off: no stale flag on the page


def samples(ups, step=60):
    """[(time, up)] one every `step` seconds, the newest at NOW, ups[0] the oldest"""
    return [(NOW - (len(ups) - 1 - i) * step, u) for i, u in enumerate(ups)]


def test_a_config_that_goes_up_and_down_counts_as_down_when_most_of_a_quarter_of_an_hour_is_down():
    # (1.69.229: on 2026-11-05 AI-2 passed 6-8 % of its tests from 17:00, an odd good one reset the seven minutes every time, and nothing happened until 17:53)
    assert warpwatch.flapping(samples([False] * 5 + [True] + [False] * 4 + [True] + [False] * 4), NOW)                  # 2 of 15 up
    assert not warpwatch.flapping(samples([True, True, True, True, False] * 3), NOW)                                    # 12 of 15 up: a mild dip, not an outage
    assert warpwatch.flapping(samples([False] * 6), NOW) and not warpwatch.flapping(samples([False] * 5), NOW)       # at least six samples
    assert warpwatch.flapping(samples([False, False, True] * 5), NOW)                                                   # 5 of 15 = 33 %: the edge
    assert not warpwatch.flapping(samples([False, False, True, False, True] * 3), NOW)                                  # 6 of 15 = 40 %
    assert not warpwatch.flapping(samples([False] * 12 + [True] * 3), NOW)                                              # the three newest are up: it just came back
    assert warpwatch.flapping(samples([False] * 12 + [True, True, False]), NOW)
    assert not warpwatch.flapping(samples([False] * 10, step=300), NOW)                                                 # older than the window does not count
    assert not warpwatch.flapping([], NOW)


def flap_samples(ups, name="AI-2"):
    """A config that shows `up` right now (a lucky test: the seven minutes never run out) with one sample a minute, the newest a few seconds ago."""
    now = int(time.time())

    with session() as db:
        c = db.query(Config).filter(Config.name == name).one()
        c.status, c.status_since = "up", now - 30
        db.query(ConfigSample).filter(ConfigSample.config_id == c.id).delete()
        db.add_all([ConfigSample(config_id=c.id, ts=now - 5 - (len(ups) - 1 - i) * 60, up=u) for i, u in enumerate(ups)])


def test_an_ai_config_that_goes_up_and_down_gets_its_node_reconnected_once(client, watch):
    flap_samples([False] * 5 + [True] + [False] * 4 + [True] + [False] * 4)
    warpwatch.run()
    assert watch == [(1, 11)]
    assert any("going up and down" in t and "Node DE2 reconnected" in t and "AI-2" in t for t in events(client))
    warpwatch.run()
    assert watch == [(1, 11)]                                                                 # the cooldown holds


def test_a_config_that_is_mostly_up_just_came_back_or_is_no_ai_config_is_left_alone(client, watch):
    flap_samples([True, True, True, False] * 3 + [True, True, False])                         # mostly up
    warpwatch.run()
    assert watch == []
    flap_samples([False] * 12 + [True] * 3)                                                   # it just came back
    warpwatch.run()
    assert watch == []
    flap_samples([False] * 15)
    flap_samples([False] * 15, name="TCP-1")                                                  # (TCP-1 is down in the same way but is no AI config)
    warpwatch.run()
    assert watch == [(1, 11)]                                                                 # once, for AI-2 only
    assert not any("TCP-1" in t for t in events(client))
