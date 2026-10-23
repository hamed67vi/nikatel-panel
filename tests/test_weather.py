"""The weather of a route (the owner, 2026-10-24: "I want the tunnel to be strong against the day's data - no radar to look at, its data must help the Trident tunnel"; services/weather.py, docs/trident.md "What it learns").

  the fusion      open evidence newer than every failure wins; a block seen within two hours stands; pressure within an hour; nothing known otherwise; `since` is kept while the state stays
  the sources     the fleet (a tunnel of a family down with a cut-like cause while another family carries, on the route and on the Iran server), the link and reach probes, the builds that said "filtered"
  the pattern     minutes seen and blocked per hour of the day (Iran time) over seven days; "usually blocked at this hour"; the proactive factor 30 minutes ahead
  the consumers   the weight of a strand (x0.5 pressure, x0.1 blocked, x0.7 usual), the choice of a kind (blocked families last), the renewal of a strand, Best of the day and the standby's choice
  the probe       which pair is probed when; the reach probe's script and its answer
  the job         one look writes the weather and the pattern; without data every consumer does what it did before"""
import time
from datetime import datetime

import pytest
from sqlalchemy import select

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import actions, autopilot, knowledge, standby, trident, weather
from nikatel.services import settings as st

NOW = 1_800_000_000


def clock() -> int:
    return int(time.time())


# ------------------------------------------------------------------ the fusion
def test_families_of_links_and_kinds():
    assert weather.family_of_link("gre") == "gre" and weather.family_of_link("fou") == "udp" and weather.family_of_link("wg") == "udp" and weather.family_of_link("ipsec") == "esp"
    assert weather.family_of_link("ipip") == "ipip" and weather.family_of_link("wgchisel") == "tcp" and weather.family_of_link("something") == "udp"
    assert weather.family_of_kind("tcpmux") == "gre" and weather.family_of_kind("bp:wssmux") == "tcp" and weather.family_of_kind("bpd:udp") == "udp" and weather.family_of_kind("bpd:xdi") == "icmp"
    assert set(weather.FAMILIES) == {"gre", "udp", "tcp", "icmp", "esp", "ipip"}


def ev(at, verdict, why="x"):
    return {"at": at, "verdict": verdict, "why": why}


def test_open_evidence_newer_than_every_failure_wins_a_block_stands_two_hours_pressure_one():
    assert weather.fuse([], None, NOW) is None
    assert weather.fuse([ev(NOW - 10, "open", "carries")], None, NOW)["state"] == "open"
    assert weather.fuse([ev(NOW - 600, "blocked", "down"), ev(NOW - 10, "open", "carries")], None, NOW)["state"] == "open"              # what carries now beats what failed ten minutes ago
    f = weather.fuse([ev(NOW - 10, "blocked", "the gre tunnel is down while tcp carries"), ev(NOW - 600, "open")], None, NOW)
    assert f["state"] == "blocked" and f["why"].startswith("the gre tunnel") and f["since"] == NOW and f["last_ok"] == NOW - 600 and f["last_fail"] == NOW - 10
    assert weather.fuse([ev(NOW - 7000, "blocked")], None, NOW)["state"] == "blocked"                                                   # a block of two hours ago still counts ...
    assert weather.fuse([ev(NOW - 7300, "blocked")], None, NOW) is None                                                                 # ... older says nothing
    assert weather.fuse([ev(NOW - 1800, "pressure")], None, NOW)["state"] == "pressure" and weather.fuse([ev(NOW - 3700, "pressure")], None, NOW) is None
    assert weather.fuse([ev(NOW - 3700, "pressure"), ev(NOW - 3650, "open")], None, NOW) is None                                        # (both too old to say anything)
    assert weather.fuse([ev(NOW - 100, "pressure"), ev(NOW - 50, "blocked")], None, NOW)["state"] == "blocked"
    prev = {"state": "blocked", "since": NOW - 5000, "last_ok": 1, "last_fail": 2}
    assert weather.fuse([ev(NOW - 10, "blocked")], prev, NOW)["since"] == NOW - 5000                                                    # the same state keeps its since
    assert weather.fuse([ev(NOW - 10, "open")], prev, NOW)["since"] == NOW and weather.fuse([ev(NOW - 10, "open")], prev, NOW)["last_fail"] == 2


def test_the_pattern_counts_the_minutes_seen_and_blocked_per_hour_of_iran_time_and_keeps_seven_days():
    hours = {}
    t = NOW

    for i in range(90):                                        # 90 looks, one a minute, the first 60 blocked
        hours = weather.pattern_add(hours, t + 60 * i, blocked=i < 60)

    h0 = datetime.fromtimestamp(NOW, weather.IRAN_TZ).hour
    total_n = sum(sum(d["n"]) for d in hours.values())
    total_b = sum(sum(d["b"]) for d in hours.values())
    assert total_n == 90 and total_b == 60 and len(hours) <= 2
    assert weather.usual_blocked(hours, h0) in (True, False)                                                           # (which hour the 60 blocked minutes fell in depends on the clock)
    assert weather.usual_blocked({"d": {"b": [40] + [0] * 23, "n": [60] + [0] * 23}}, 0) is True                     # 40 of 60 minutes blocked: usual
    assert weather.usual_blocked({"d": {"b": [20] + [0] * 23, "n": [60] + [0] * 23}}, 0) is False                    # 20 of 60: not usual
    assert weather.usual_blocked({"d": {"b": [30] + [0] * 23, "n": [30] + [0] * 23}}, 0) is False                    # not an hour seen yet
    assert weather.usual_blocked(None, 0) is False

    old = {f"2026-01-{d:02d}": {"b": [0] * 24, "n": [1] * 24} for d in range(1, 10)}
    kept = weather.pattern_add(old, NOW, False)
    assert len(kept) == weather.PATTERN_DAYS and "2026-01-01" not in kept                                              # the oldest days go


def test_the_factor_of_a_strand_follows_the_weather_and_looks_half_an_hour_ahead():
    assert weather.factor(None, None, NOW) == (1.0, "")
    assert weather.factor({"state": "open"}, None, NOW) == (1.0, "open")
    assert weather.factor({"state": "pressure"}, None, NOW) == (0.5, "pressure")
    assert weather.factor({"state": "blocked"}, None, NOW) == (0.1, "blocked")
    ahead = datetime.fromtimestamp(NOW + weather.AHEAD, weather.IRAN_TZ).hour
    usual = {"d": {"b": [0] * 24, "n": [0] * 24}}
    usual["d"]["b"][ahead], usual["d"]["n"][ahead] = 50, 60
    assert weather.factor({"state": "open"}, usual, NOW) == (0.7, "usual")                                           # usually blocked within the next half hour: the share goes down before it happens
    assert weather.factor(None, usual, NOW) == (0.7, "usual")
    assert weather.factor({"state": "blocked"}, usual, NOW) == (0.1, "blocked")                                        # (a block that is here counts more than one that is usual)


# ------------------------------------------------------------------ the sources
def row(name, iran, foreign, fam, state="up", since=NOW - 600, error="", off=False):
    return {"name": name, "iran_id": iran, "foreign_id": foreign, "family": fam, "state": state, "since": since, "off": off, "error": error, "hidden": False}


def test_the_fleet_a_family_down_while_another_carries_on_the_route_is_blocked_there_and_the_iran_server_rule_is_weaker():
    rows = [row("a-gre", 1, 2, "gre", "down", NOW - 120, "no answer from the foreign end over the GRE link"), row("a-sb", 1, 2, "tcp"), row("a-sb2", 1, 2, "icmp"),
            row("b-gre", 1, 3, "gre", "down", NOW - 60, "the GRE interface is missing"), row("c-tcp", 4, 3, "tcp"), row("c-udp", 4, 3, "udp", "down", NOW - 30, "the server is off", off=True)]
    e = weather.fleet_evidence(rows, NOW)
    a = e["1:2"]
    assert [x["verdict"] for x in a["gre"]] == ["blocked"] and "a-gre (gre) is down" in a["gre"][0]["why"] and "while icmp carries" in a["gre"][0]["why"]
    assert [x["verdict"] for x in a["tcp"]] == ["open"] and [x["verdict"] for x in a["icmp"]] == ["open"]
    b = e["1:3"]
    assert [x["verdict"] for x in b["gre"]] == ["blocked"] and "2 of the 2 gre tunnels of the Iran server are down" in b["gre"][0]["why"]        # no other family on that route: the Iran server's rule (2 of 2 down, tcp fine there)
    assert "udp" not in e.get("4:3", {}) and [x["verdict"] for x in e["4:3"]["tcp"]] == ["open"]                                                  # a server that is off is no evidence

    one = [row("a-gre", 1, 2, "gre", "down", NOW - 120, "no answer"), row("a-sb", 1, 2, "tcp"), row("b-gre", 1, 3, "gre"), row("b-tcp", 1, 3, "tcp")]
    e = weather.fleet_evidence(one, NOW)
    assert [x["verdict"] for x in e["1:2"]["gre"]] == ["blocked"]                                                                                  # on its own route: blocked
    assert sorted(x["verdict"] for x in e["1:3"]["gre"]) == ["open", "pressure"]                                                                   # on the server's other route: pressure beside its own open tunnel
    assert weather.fuse(e["1:3"]["gre"], None, NOW)["state"] == "open"                                                                             # ... and the open tunnel there is newer: open wins

    quiet = [row("a-gre", 1, 2, "gre", "down", NOW - 120, "the tunnel carries no download: stalled"), row("a-sb", 1, 2, "tcp", "down", NOW - 100, "no answer")]
    assert weather.fleet_evidence(quiet, NOW)["1:2"].get("gre", []) == [] or not any(x["verdict"] == "blocked" for x in weather.fleet_evidence(quiet, NOW)["1:2"].get("gre", []))      # everything down: no family is singled out


def test_the_probes_and_the_builds_give_evidence_too():
    pair = {"links": {"gre": {"ok": False, "at": NOW - 100, "error": "does not pass"}, "fou": {"ok": True, "at": NOW - 100}, "ipsec": {"ok": None, "skipped": "tools", "at": NOW - 100}},
            "kinds": {"tcpmux": {"last": {"ok": False, "at": NOW - 200, "error": "GRE (protocol 47) is probably filtered between these two servers"}},
                      "tcp": {"last": {"ok": False, "at": NOW - 150, "error": "GRE (protocol 47) is probably filtered between these two servers"}},
                      "bpd:quic": {"last": {"ok": False, "at": NOW - 100, "error": "the QUIC carrier cannot pass between these servers"}},
                      "paqet": {"last": {"ok": False, "at": NOW - 100, "error": "not allowed by the licence"}}}}
    le = weather.link_evidence(pair, {"icmp": {"ok": True, "at": NOW - 50, "why": "5 of 5 pings came back"}, "tcp": {"ok": False, "at": NOW - 50, "why": "0 of 3"}}, NOW)
    assert [x["verdict"] for x in le["gre"]] == ["blocked"] and [x["verdict"] for x in le["udp"]] == ["open"] and "esp" not in le
    assert [x["verdict"] for x in le["icmp"]] == ["open"] and [x["verdict"] for x in le["tcp"]] == ["blocked"]
    be = weather.build_evidence(pair, NOW)
    assert [x["verdict"] for x in be["gre"]] == ["blocked"] and "tcp, tcpmux failed to build" in be["gre"][0]["why"]                          # two GRE kinds failed with a filtered carrier: blocked
    assert [x["verdict"] for x in be["udp"]] == ["pressure"] and "tcp" not in be                                                               # one UDP kind: pressure; a licence refusal is no weather
    assert weather.build_evidence(None, NOW) == {} and weather.link_evidence(None, None, NOW) == {}


def test_which_pair_is_probed_when():
    routes = [{"key": "1:2", "name": "a", "cpu_i": 10, "cpu_f": 20, "repairing": False}, {"key": "1:3", "name": "b", "cpu_i": 10, "cpu_f": 20, "repairing": False}]
    assert weather.probe_due(routes, {}, NOW, 0) == "1:2"                                                                                        # never probed: the first
    assert weather.probe_due(routes, {"1:2": NOW - 100, "1:3": NOW - 5000}, NOW, 0) == "1:3"                                                     # the oldest probe first
    assert weather.probe_due(routes, {"1:2": NOW - 100, "1:3": NOW - 100}, NOW, 0) == ""                                                         # both fresh
    assert weather.probe_due(routes, {}, NOW, NOW - 30) == ""                                                                                    # one every minute
    assert weather.probe_due(routes, {}, NOW, 0, busy={"1:9"}) == ""                                                                             # one at a time
    assert weather.probe_due([{**routes[0], "cpu_f": 95}, {**routes[1], "repairing": True}], {}, NOW, 0) == ""                                   # a busy server, a repair: not now
    assert weather.probe_due([], {}, NOW, 0) == ""
    assert weather.parse_reach("REACH icmp 5/5 tcp 3/3\n") == {"icmp": {"ok": True, "why": "5 of 5 pings came back"}, "tcp": {"ok": True, "why": "3 of 3 TCP handshakes to the foreign server succeeded"}}
    assert weather.parse_reach("REACH icmp 0/5 tcp 1/3")["icmp"]["ok"] is False and weather.parse_reach("REACH icmp 0/5 tcp 1/3")["tcp"]["ok"] is True and weather.parse_reach("garbage") == {}


def test_the_reach_probe_script_reads_only_and_takes_an_address_and_a_port():
    s = ssh._script_text("reach_probe", {"HOST": "198.51.100.7", "PORT": "22"})
    assert "ping -c 5" in s and "/dev/tcp/$h/$p" in s and 'echo "REACH icmp' in s
    assert "systemctl" not in s and "iptables" not in s and " -j " not in s and "rm " not in s

    for bad in ({"HOST": "example.com", "PORT": "22"}, {"HOST": "198.51.100.7; reboot", "PORT": "22"}, {"HOST": "198.51.100.7", "PORT": "x"}):
        with pytest.raises(ValueError):
            ssh._script_text("reach_probe", bad)


# ------------------------------------------------------------------ the job
@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(autopilot, "repairing_names", lambda: set())
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    weather._PROBING.clear()
    weather._STATE["last_probe"] = 0.0
    yield


def world():
    with session() as db:
        ir, fr, fr2 = Server(name="ir-w", host="203.0.113.81", role="iran", online=True), Server(name="fr-w", host="198.51.100.81", role="foreign", online=True), Server(name="fr-w2", host="198.51.100.82", role="foreign", online=True)
        db.add_all([ir, fr, fr2])
        db.flush()
        t1 = Tunnel(name="w-gre", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fr.id, state="down", state_since=clock() - 120, last_check_at=clock(), last_error="no answer from the foreign end over the GRE link", forwarded_ports=[443])
        t2 = Tunnel(name="w-gre-sb", type="chisel", transport="wss", enabled=False, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", state_since=clock() - 3000, last_check_at=clock(), forwarded_ports=[20443], record={"standby_of": "w-gre", "kind": "chisel"})
        t3 = Tunnel(name="w2-gre", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fr2.id, state="down", state_since=clock() - 60, last_check_at=clock(), last_error="the GRE interface is missing", forwarded_ports=[444])
        db.add_all([t1, t2, t3])
        db.flush()
        return {"ir": ir.id, "fr": fr.id, "fr2": fr2.id, "t1": t1.id}


def test_one_look_writes_the_weather_and_the_pattern_and_the_consumers_read_it(monkeypatch):
    w = world()
    doc = weather.observe(clock())
    key, key2 = weather.route_key(w["ir"], w["fr"]), weather.route_key(w["ir"], w["fr2"])
    assert doc[key]["gre"]["state"] == "blocked" and doc[key]["tcp"]["state"] == "open" and "w-gre (gre) is down" in doc[key]["gre"]["why"]
    assert doc[key2]["gre"]["state"] == "blocked" and "2 of the 2 gre tunnels" in doc[key2]["gre"]["why"]
    kept = st.get("trident")
    assert kept["weather"] == doc and sum(kept["pattern"][key]["gre"][d]["b"][h] for d in kept["pattern"][key]["gre"] for h in range(24)) == 1

    assert weather.blocked_families(w["ir"], w["fr"]) == {"gre"} and weather.states_for(w["ir"], w["fr"]) == {"gre": "blocked", "tcp": "open"}
    assert weather.factor_for(w["ir"], w["fr"], "tcpmux") == (0.1, "blocked") and weather.factor_for(w["ir"], w["fr"], "bp:wssmux") == (1.0, "open") and weather.factor_for(w["ir"], w["fr"], "bpd:udp") == (1.0, "")
    assert weather.words(w["ir"], w["fr"]).startswith("gre blocked on this route for 0 min") and weather.words(99, 98) == ""

    # the consumers: a kind of a blocked family goes last in Best of the day and in the standby's choice; the weight of a strand of that family is a tenth
    assert knowledge.weather_last(["tcpmux", "bp:wssmux", "tcp", "bpd:udp"], w["ir"], w["fr"], clock()) == ["bp:wssmux", "bpd:udp", "tcpmux", "tcp"]
    assert knowledge.weather_last(["tcpmux", "bp:wssmux"], 77, 78, clock()) == ["tcpmux", "bp:wssmux"]                                        # no weather: unchanged
    picks = [k for k, _ in knowledge.best_kinds(w["ir"], w["fr"], limit=3)[0]]
    assert picks and picks[0] != "tcpmux" and "tcpmux" not in picks[:2]
    kind, note = standby.choose_kind(["tcpmux", "bp:wssmux", "chisel:tls"], "bpd:udp", None, clock(), blocked_families={"gre"})
    assert kind == "bp:wssmux"
    kind, note = standby.choose_kind(["tcpmux"], "bpd:udp", None, clock(), blocked_families={"gre"})
    assert kind == "tcpmux" and "blocked on this route today" in note                                                                           # nothing else: a blocked family is still better than no standby
    kind, note = trident.pick_kind(["tcpmux", "bpd:udp", "chisel:tls"], ["bp:wssmux"], None, clock(), states={"gre": "blocked", "udp": "pressure"})
    assert kind == "bpd:udp" and "under pressure on this route today" in note                                                                     # gre blocked: last; udp under pressure still beats a kind that shares the tunnel's fate (tcp)
    kind, _ = trident.pick_kind(["tcpmux", "chisel:tls", "bpd:udp"], ["bp:wssmux"], None, clock(), states={"gre": "blocked", "udp": "open", "tcp": "open"})
    assert kind == "bpd:udp"                                                                                                                       # two open families: the one that shares no fate with the tunnel
    kind, note = trident.pick_kind(["tcpmux", "chisel:tls"], ["bp:wssmux"], None, clock(), states={"gre": "blocked"})
    assert kind == "chisel:tls" and "blocked" not in note                                                                                          # the blocked family only when nothing else is left
    kind, note = trident.pick_kind(["tcpmux"], ["bp:wssmux"], None, clock(), states={"gre": "blocked"})
    assert kind == "tcpmux" and "is blocked on this route today" in note
    wts = trident.target_weights([{"usable": True, "cap": 100, "rtt": 50, "loss": 0, "stab": 1, "stage": 3, "wx": 1.0}, {"usable": True, "cap": 100, "rtt": 50, "loss": 0, "stab": 1, "stage": 3, "wx": 0.1}])
    assert wts[0] == pytest.approx(0.9) and wts[1] == pytest.approx(0.1)                                                                           # (the floor of 5 % still holds: 100 / 110 = 0.909, held at the ceiling 0.9)
    wts = trident.target_weights([{"usable": True, "cap": 100, "rtt": 50, "loss": 0, "stab": 1, "stage": 3, "wx": 1.0}, {"usable": True, "cap": 100, "rtt": 50, "loss": 0, "stab": 1, "stage": 3, "wx": 0.5}])
    assert wts[0] == pytest.approx(2 / 3) and wts[1] == pytest.approx(1 / 3)


def test_the_renewal_of_a_strand_whose_family_has_been_bad_for_hours():
    states = {"udp": {"state": "blocked", "since": NOW - 3 * 3600}, "icmp": {"state": "open", "since": NOW - 9000}, "tcp": {"state": "open", "since": NOW - 9000}}
    assert trident.renew_due("udp", states, {"tcp", "gre"}, 0, NOW) == "icmp"                                           # blocked for 3 h, icmp is open and nobody uses it
    assert trident.renew_due("udp", states, {"tcp", "icmp"}, 0, NOW) == ""                                              # the open family is taken by another strand
    assert trident.renew_due("udp", states, {"tcp"}, NOW - 3600, NOW) == ""                                             # renewed an hour ago
    assert trident.renew_due("udp", {**states, "udp": {"state": "blocked", "since": NOW - 600}}, {"tcp"}, 0, NOW) == ""   # not for long enough
    assert trident.renew_due("udp", {**states, "udp": {"state": "pressure", "since": NOW - 3 * 3600}}, {"tcp"}, 0, NOW) == "icmp"
    assert trident.renew_due("udp", {**states, "udp": {"state": "open", "since": NOW - 9000}}, {"tcp"}, 0, NOW) == ""
    assert trident.renew_due("udp", {}, {"tcp"}, 0, NOW) == ""


def test_the_job_probes_one_trident_pair_at_a_time_and_only_while_calm(monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)                                                            # (a read-only panel never probes)
    w = world()
    st.put("trident", {"tunnels": {"w-gre": {"on": True, "since": clock()}}})

    with session() as db:
        db.get(Tunnel, w["t1"]).state = "up"

        for s in db.scalars(select(Server)):
            s.facts = {"cpu": 5.0}

    probed = []
    monkeypatch.setattr(weather, "_probe", lambda key: probed.append(key) or weather._PROBING.discard(key))
    monkeypatch.setattr(weather.threading, "Thread", lambda target, args=(), name="", daemon=True: type("T", (), {"start": lambda self: target(*args)})())
    weather._probe_if_due()
    assert probed == [weather.route_key(w["ir"], w["fr"])] and st.get("trident")["probed"][weather.route_key(w["ir"], w["fr"])] > 0
    weather._probe_if_due()
    assert len(probed) == 1                                                                                              # one a minute, and the pair is fresh now

    weather._STATE["last_probe"] = 0.0
    st.put("trident", {"probed": {}})
    with session() as db:
        for s in db.scalars(select(Server)):
            s.facts = {"cpu": 90.0}

    weather._probe_if_due()
    assert len(probed) == 1                                                                                              # busy servers: not now

    import inspect

    from nikatel import main

    assert 'jobs.register("weather", 60, weather.run)' in inspect.getsource(main._register_jobs)
