"""The WARP watch (services/warpwatch.py, 1.69.228): an AI config down for 7 minutes with its tunnel and node up gets that node reconnected - once in 30 minutes, three times a day, never a guess."""
import time

import pytest

from nikatel.db import session
from nikatel.models import Config, Panel, Server, Tunnel
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
    assert todo == {} and "exactly one node" in held[0][1]                                              # not placed for sure: not touched
    todo, held = warpwatch.decide([down()], {"AI-2": {**PLACED["AI-2"], "node_status": "disconnected"}}, {}, NOW)
    assert todo == {} and "not connected" in held[0][1]                                                 # the panel's own reconnect handles a node that dropped
    todo, held = warpwatch.decide([down()], {"AI-2": {**PLACED["AI-2"], "tunnel_state": "down"}}, {}, NOW)
    assert todo == {} and "tunnel is down" in held[0][1]                                                # a dead tunnel is not the node's fault


def test_a_node_is_reconnected_at_most_once_in_thirty_minutes_and_three_times_a_day():
    todo, held = warpwatch.decide([down()], PLACED, {"1:11": [NOW - 600]}, NOW)
    assert todo == {} and "10 minutes ago" in held[0][1]
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - warpwatch.COOLDOWN - 1]}, NOW)[0] == {"1:11": ["AI-2"]}
    three = [NOW - 3 * 3600, NOW - 2 * 3600, NOW - 3600]
    todo, held = warpwatch.decide([down()], PLACED, {"1:11": three}, NOW)
    assert todo == {} and "3 times in the last 24 hours" in held[0][1]
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 90000, NOW - 80000, NOW - 70000]}, NOW)[0] == {"1:11": ["AI-2"]}      # older than a day does not count


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


def test_a_config_still_down_after_three_reconnects_is_told_once_a_day_and_left_alone(client, watch):
    now = int(time.time())
    st.put("warpwatch", {"done": {"1:11": [now - 7 * 3600, now - 5 * 3600, now - 3 * 3600]}})
    warpwatch.run()
    warpwatch.run()
    assert watch == []
    assert sum("still down" in t and "needs a look by hand" in t for t in events(client)) == 1


def test_the_switch_reports_the_reconnects_of_the_last_day(client):
    now = time.time()
    st.put("warpwatch", {"done": {"1:11": [now - 100, now - 90000]}})
    r = client.get("/api/settings/warpwatch").json()
    assert r["on"] is True and r["reconnects_24h"] == {"1:11": 1}
