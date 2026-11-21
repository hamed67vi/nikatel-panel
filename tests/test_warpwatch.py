"""The WARP watch (services/warpwatch.py, 1.69.228; no daily cap and a move to another WARP account since 1.69.292): an AI config down for 7 minutes with its tunnel and node up gets that node reconnected - as long as it is down, a quarter of an hour apart,
never a guess; when the reconnects do not hold (three in six hours) the config is moved to another WARP account in the core; the Doctor page carries a flag for every config it works on, and a config that stays up drops its flag."""
import copy
import time

import pytest

from nikatel.db import session
from nikatel.models import Config, ConfigSample, Panel, Server, Tunnel
from nikatel.services import dnscache, panels, pg_cores, warpwatch
from nikatel.services import settings as st

NOW = 1_800_000_000
PLACED = {"AI-2": {"key": "1:11", "panel_id": 1, "node_id": 11, "node_name": "DE2", "node_status": "connected", "tunnel_state": "up"}}


def down(name="AI-2", for_s=600):
    return {"name": name, "since": NOW - for_s}


def test_a_config_is_acted_on_only_after_it_was_down_long_enough_and_everything_around_it_is_up():
    assert warpwatch.decide([down(for_s=180)], PLACED, {}, NOW) == ({}, [], {})                              # three minutes: a lost test, nothing to say
    assert warpwatch.decide([down(for_s=480)], PLACED, {}, NOW) == ({"1:11": ["AI-2"]}, [], {})
    assert warpwatch.is_ai("AI-1") and warpwatch.is_ai("ai-2") and warpwatch.is_ai("AI2") and not warpwatch.is_ai("TCP-1") and not warpwatch.is_ai("Maintain") and not warpwatch.is_ai("Cloud-3")

    todo, held, moves = warpwatch.decide([down()], {}, {}, NOW)
    assert todo == {} and moves == {} and "exactly one node" in held[0][1] and held[0][2] == "held"        # not placed for sure: not touched, and flagged: nobody watches it
    todo, held, _ = warpwatch.decide([down()], {"AI-2": {**PLACED["AI-2"], "node_status": "disconnected"}}, {}, NOW)
    assert todo == {} and "not connected" in held[0][1] and held[0][2] == ""                                # the panel's own reconnect handles a node that dropped: nothing to flag
    todo, held, _ = warpwatch.decide([down()], {"AI-2": {**PLACED["AI-2"], "tunnel_state": "down"}}, {}, NOW)
    assert todo == {} and "tunnel is down" in held[0][1] and held[0][2] == ""                               # a dead tunnel is not the node's fault


def test_a_node_waits_a_quarter_of_an_hour_after_a_reconnect_and_there_is_no_cap_per_day():
    todo, held, _ = warpwatch.decide([down()], PLACED, {"1:11": [NOW - 600]}, NOW)
    assert todo == {} and "10 minutes ago" in held[0][1] and held[0][2] == "working"
    again = {"1:11": ["AI-2"]}
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 901]}, NOW)[0] == again                       # fifteen minutes after the last one, whatever came before
    eight = {"1:11": [NOW - 80000, NOW - 70000, NOW - 60000, NOW - 50000, NOW - 40000, NOW - 30000, NOW - 25000, NOW - 901]}
    assert warpwatch.decide([down()], PLACED, eight, NOW) == (again, [], {})                                # eight tries in a day are no reason to stop (the old cap would have said "needs a hand"): only the last six hours count for a move
    assert warpwatch.decide([down()], PLACED, {"1:11": [*eight["1:11"][:-1], NOW - 22000]}, NOW) == (again, [], {})


def test_three_reconnects_that_did_not_hold_in_six_hours_make_the_next_step_a_move_to_another_account():
    three = {"1:11": [NOW - 20000, NOW - 12000, NOW - 4000]}
    assert warpwatch.decide([down()], PLACED, three, NOW) == ({}, [], {"AI-2": "1:11"})
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 20000, NOW - 12000, NOW - 500]}, NOW)[2] == {}          # the last one is fresh: wait and see whether it holds
    assert warpwatch.decide([down()], PLACED, {"1:11": [NOW - 40000, NOW - 12000, NOW - 4000]}, NOW)[0] == {"1:11": ["AI-2"]}   # only two in the last six hours: reconnect again
    assert warpwatch.decide([down()], PLACED, three, NOW, {"AI-2": {"at": NOW - 3600}})[0] == {"1:11": ["AI-2"]}      # moved an hour ago: no second move, the reconnects go on
    assert warpwatch.decide([down()], PLACED, three, NOW, {"AI-2": {"at": NOW - warpwatch.SWITCH_GAP - 1}})[2] == {"AI-2": "1:11"}      # a move six hours ago: another one is allowed


def test_the_flag_says_the_tries_used_and_when_the_next_one_is_allowed():
    f = warpwatch.flag_of("working", "", PLACED["AI-2"], [NOW - 5000, NOW - 100], NOW - 7000)
    assert f == {"state": "working", "node": "DE2", "since": NOW - 7000, "tries": 2, "max": 0, "last": NOW - 100, "next": NOW - 100 + 900, "why": ""}
    assert warpwatch.flag_of("held", "y", None, [], NOW) == {"state": "held", "node": "", "since": NOW, "tries": 0, "max": 0, "last": 0, "next": 0, "why": "y"}


def test_two_configs_down_on_one_node_reconnect_it_once():
    both = {"AI-1": PLACED["AI-2"], "AI-2": PLACED["AI-2"]}
    todo, _, _ = warpwatch.decide([down("AI-1"), down("AI-2")], both, {}, NOW)
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
    assert f["state"] == "working" and f["node"] == "DE2" and f["tries"] == 1 and f["max"] == 0 and 880 < f["next"] - int(time.time()) <= 900


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


def test_the_watch_keeps_reconnecting_after_six_tries_in_a_day_and_tells_once_a_day_that_the_cure_does_not_hold(client, watch):
    now = int(time.time())
    st.put("warpwatch", {"done": {"1:11": [now - h * 3600 for h in (23, 20, 17, 14, 11, 8)]}})              # six in the last day, none in the last six hours: the old cap said "needs a hand" here
    warpwatch.run()
    assert watch == [(1, 11)]                                                                 # the seventh goes out
    f = st.get("warpwatch")["flags"]["AI-2"]
    assert f["state"] == "working" and f["tries"] == 7 and f["max"] == 0
    chronic = [t for t in events(client) if "does not hold" in t and "7 times" in t]
    assert len(chronic) == 1                                                                  # said once
    warpwatch.run()
    assert len([t for t in events(client) if "does not hold" in t]) == 1


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
    st.put("warpwatch", {"flags": {"AI-9": warpwatch.flag_of("working", "x", PLACED["AI-2"], [], NOW)}})
    warpwatch.run()
    assert "AI-9" not in st.get("warpwatch")["flags"] and "AI-2" in st.get("warpwatch")["flags"]


def test_the_switch_reports_the_reconnects_of_the_last_day_and_the_flags_and_the_doctor_page_gets_them(client):
    now = time.time()
    st.put("warpwatch", {"done": {"1:11": [now - 100, now - 90000]}})
    r = client.get("/api/settings/warpwatch").json()
    assert r["on"] is True and r["reconnects_24h"] == {"1:11": 1} and r["flags"] == []
    st.put("warpwatch", {"flags": {"AI-2": warpwatch.flag_of("working", "x", PLACED["AI-2"], [int(now) - 100], int(now) - 3000)}})
    r = client.get("/api/settings/warpwatch").json()
    assert [f["name"] for f in r["flags"]] == ["AI-2"] and r["flags"][0]["state"] == "working" and r["flags"][0]["since"] == int(now) - 3000
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


# ---------------------------------------------------------------- the move to another WARP account
def core_cfg():
    """The shape of the real core: AI-1 and AI-2 share the inbound on 1830 (rule -> warp-02), 1818 and the Shadowsocks 1819 go to warp-01, an Instagram rule goes direct."""
    return {"inbounds": [{"tag": "VLESS TCP REALITY 1818", "port": 1818}, {"tag": "SHADOWSOCKS TCP 1819", "port": 1819}, {"tag": "VLESS TCP TLS AI 1830", "port": 1830}],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "warp-01", "protocol": "wireguard", "settings": {"secretKey": "k1"}}, {"tag": "warp-02", "protocol": "wireguard", "settings": {"secretKey": "k2"}}],
            "routing": {"rules": [{"type": "field", "domain": ["domain:instagram.com"], "outboundTag": "direct"},
                                  {"type": "field", "inboundTag": ["VLESS TCP REALITY 1818"], "outboundTag": "warp-01"},
                                  {"type": "field", "inboundTag": ["SHADOWSOCKS TCP 1819"], "outboundTag": "warp-01"},
                                  {"type": "field", "inboundTag": ["VLESS TCP TLS AI 1830"], "outboundTag": "warp-02"}]}}


def test_the_move_points_the_rule_of_the_inbound_at_the_other_account_and_touches_nothing_else():
    new, old, pick = warpwatch._plan_move(core_cfg(), 1830)
    assert (old, pick) == ("warp-02", "warp-01")
    want = core_cfg()
    want["routing"]["rules"][3]["outboundTag"] = "warp-01"
    assert new == want                                                                         # only that one rule changed; the keys of the accounts are in it, untouched
    third = core_cfg()
    third["outbounds"].append({"tag": "warp-03", "protocol": "wireguard"})
    assert warpwatch._plan_move(third, 1830)[2] == "warp-03"                                    # an account that no rule uses is preferred to one that three rules use
    _, old2, pick2 = warpwatch._plan_move(new, 1830)
    assert (old2, pick2) == ("warp-01", "warp-02")                                              # and it can go back later

    for bad, why in ((lambda c: c["inbounds"].append({"tag": "x", "port": 1830}), "2 inbounds"), (lambda c: c["inbounds"].pop(), "0 inbounds"),
                     (lambda c: c["routing"]["rules"].pop(), "no single routing rule"), (lambda c: c["routing"]["rules"][3].update(outboundTag="direct"), "no single routing rule"),
                     (lambda c: c["outbounds"].pop(1), "no other WARP account")):
        c = core_cfg()
        bad(c)

        with pytest.raises(warpwatch.MoveError, match=why):
            warpwatch._plan_move(c, 1830)


@pytest.fixture
def core(monkeypatch):
    """A fake panel core: what `pg_cores.save` was given, what `restore` was asked for, and whether the node comes back."""
    state = {"cfg": core_cfg(), "saved": [], "restored": [], "back": True, "id": 1}
    cur = lambda: {"id": 1, "name": "Nikatel core", "type": "xray", "config": copy.deepcopy(state["cfg"]), "exclude": [], "fallbacks": []}
    monkeypatch.setattr(pg_cores, "_get_core", lambda c, core_id: cur())

    def save(c, core_id, config=None, confirm=False, restart_after=False, base_rev=None, note=""):
        assert confirm and not restart_after and base_rev == pg_cores.rev_of(cur())                # a move never restarts every node
        state["saved"].append((copy.deepcopy(config), note))
        state["cfg"] = config
        return {"backup": "cores-1-1-1800000000.json"}

    monkeypatch.setattr(pg_cores, "save", save)
    monkeypatch.setattr(pg_cores, "restore", lambda c, core_id, name, confirm=False, restart_after=False: state["restored"].append(name))
    monkeypatch.setattr(warpwatch, "_core_id_of", lambda c, nid: 1)
    monkeypatch.setattr(warpwatch, "_node_back", lambda c, nid: state["back"])
    return state


def three_tries():
    now = int(time.time())
    st.put("warpwatch", {"done": {"1:11": [now - 20000, now - 12000, now - 4000]}})


def test_a_cure_that_does_not_hold_ends_in_a_move_to_the_other_account_with_one_reconnect_of_that_node(client, watch, core):
    three_tries()
    warpwatch.run()
    assert len(core["saved"]) == 1 and core["saved"][0][0]["routing"]["rules"][3]["outboundTag"] == "warp-01"
    assert "moved from warp-02 to warp-01" in core["saved"][0][1]
    assert watch == [(1, 11)]                                                                   # the node was reconnected ONCE (that applies the core there), not once more as a plain reconnect
    d = st.get("warpwatch")
    assert d["done"]["1:11"] == [] and d["moved"]["AI-2"]["from"] == "warp-02" and d["moved"]["AI-2"]["to"] == "warp-01" and d["moved"]["AI-2"]["backup"].startswith("cores-1-1-")
    f = d["flags"]["AI-2"]
    assert f["state"] == "working" and "moved from warp-02 to warp-01" in f["why"] and f["tries"] == 0
    assert any("moved the config from warp-02 to warp-01" in t and "reconnected only that node" in t for t in events(client))
    warpwatch.run()
    assert len(core["saved"]) == 1 and watch == [(1, 11)]                                       # a minute later: nothing more (the new account is given a chance first)


def test_a_node_that_does_not_come_back_gets_the_old_core_back(client, watch, core):
    three_tries()
    core["back"] = False
    warpwatch.run()
    assert core["restored"] == ["cores-1-1-1800000000.json"]
    assert watch == [(1, 11), (1, 11)]                                                          # one reconnect to apply the move, one after the old core was put back - and no third at once
    d = st.get("warpwatch")
    assert d["moved"]["AI-2"]["failed"] and "put back" in d["moved"]["AI-2"]["failed"] and len(d["done"]["1:11"]) == 4          # the three before and the one that put the old core back
    assert any("could not" in t and "the old core was put back" in t for t in events(client))
    warpwatch.run()
    assert len(core["saved"]) == 1                                                              # a failed try counts for the gap: no new try every minute


def test_a_move_that_is_not_possible_says_so_and_the_watch_reconnects_as_before(client, watch, core):
    three_tries()
    core["cfg"]["outbounds"].pop(1)                                                             # one account only (warp-02 stays: the rule uses it)
    warpwatch.run()
    assert core["saved"] == [] and watch == [(1, 11)]
    assert any("could not" in t and "no other WARP account" in t for t in events(client))
    assert "failed" in st.get("warpwatch")["moved"]["AI-2"] and len(st.get("warpwatch")["done"]["1:11"]) == 4


def test_a_panel_that_refuses_the_core_is_not_asked_again_and_again(client, watch, core, monkeypatch):
    three_tries()

    def refuse(*a, **k):
        raise ValueError("this panel is read-only")

    monkeypatch.setattr(pg_cores, "save", refuse)
    warpwatch.run()
    assert watch == [(1, 11)] and "read-only" in st.get("warpwatch")["moved"]["AI-2"]["failed"]
    st.put("warpwatch", {"done": {"1:11": [int(time.time()) - 20000, int(time.time()) - 12000, int(time.time()) - 1000]}})
    warpwatch.run()
    assert watch == [(1, 11), (1, 11)]                                                          # the gap holds: this minute it is a plain reconnect again, no second try of the core
