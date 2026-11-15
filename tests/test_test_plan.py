"""The test plan (the owner, 2026-11-15: "the Doctor must not run pointless tests ... the best tests, so that its capacity for the important tests is not used up"):
every kind is tested once on every pair; how soon it is tested AGAIN depends on what the test is worth (needed / spare / poor / failing / its link does not pass / a server has no wireguard-tools)."""
import time

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import knowledge, tunnel_test
from nikatel.services import settings as st

NOW = 1_800_000_000
DAY = 86400
HOSTS = iter(range(10, 250))


def e(kind, ok, score=None, at=NOW, error="", **nums):
    return knowledge.merge(None, {"kind": kind, "ok": ok, "score": score, "error": error, **nums}, "latency", at)


def pair_of(**scores):
    """A pair whose kinds passed with these scores (a key with `_` for `:`)."""
    return {"kinds": {k.replace("_", ":"): e(k.replace("_", ":"), True, s, latency_ms=50.0, loss_pct=0.0) for k, s in scores.items()}}


def servers(name):
    with session() as db:
        i = Server(name=f"ir-{name}", host=f"203.0.113.{next(HOSTS)}", role="iran", online=True)
        f = Server(name=f"fr-{name}", host=f"203.0.113.{next(HOSTS)}", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        return i.id, f.id


def live(name, ir, fr):
    with session() as db:
        db.add(Tunnel(name=f"{name}-t", type="backhaul", transport="tcpmux", port=9905, iran_server_id=ir, foreign_server_id=fr, state="up", forwarded_ports=[8075], rx_mbps=0.0, tx_mbps=0.0))


def test_the_needed_kinds_are_the_running_ones_the_best_of_the_pair_and_the_best_of_every_cut():
    p = pair_of(bpd_pck=95, chisel_fwd=93, bp_wssmux=91, chisel_tls=89, tcpmux=87, ipsec_tcpmux=85, paqet=30, bp_wss=28, gue_tcpmux=26, bpd_udp=24)
    need = knowledge.needed_kinds(p, NOW, {"bp:kcp"})
    assert {"bpd:pck", "chisel:fwd", "bp:wssmux", "chisel:tls", "tcpmux"} <= need                    # the best five: what a switch reaches for first
    assert "ipsec:tcpmux" in need                                                                     # the second kernel link: the reserve of the cut where nobody can dial
    assert "bp:kcp" in need                                                                           # the type that runs, whatever the survey knows of it
    assert not need & {"paqet", "bp:wss", "gue:tcpmux", "bpd:udp"}                                    # they work, but nothing would reach for them
    assert knowledge.needed_kinds(None, NOW) == set() and knowledge.needed_kinds({}, NOW, ["tcp"]) == {"tcp"}


def test_a_needed_kind_is_tested_again_after_a_week_and_a_spare_one_after_a_month():
    p = pair_of(bpd_pck=95, paqet=30)
    need = {"bpd:pck"}
    assert knowledge.plan_kinds(p, ["bpd:pck", "paqet"], NOW + 6 * DAY, need) == ([], {})            # nothing is due inside the week
    due, spared = knowledge.plan_kinds(p, ["bpd:pck", "paqet", "chisel"], NOW + 8 * DAY, need)
    assert due == ["bpd:pck", "chisel"] and spared == {"spare": 1}                                   # the needed one and the one nobody tested; the spare one is left out (the old rule would have built it)
    assert knowledge.plan_kinds(p, ["paqet"], NOW + 31 * DAY, need)[0] == ["paqet"]                   # ... and comes back after a month
    assert knowledge.plan_kinds(p, ["bpd:pck"], NOW + 8 * DAY)[0] == []                               # (without a list of needed kinds everything that works is spare)


def test_a_poor_kind_waits_a_month_and_a_failing_one_twice_as_long_after_every_failure():
    poor = e("bp:wss", False, 42, latency_ms=150.0, loss_pct=0.0)
    once = e("bpd:udp", False, None, error="handshake did not complete")
    twice = knowledge.merge(once, {"kind": "bpd:udp", "ok": False, "error": "handshake did not complete"}, "latency", NOW)
    thrice = knowledge.merge(twice, {"kind": "bpd:udp", "ok": False, "error": "handshake did not complete"}, "latency", NOW)
    many = thrice

    for _ in range(4):
        many = knowledge.merge(many, {"kind": "bpd:udp", "ok": False, "error": "handshake did not complete"}, "latency", NOW)

    assert [knowledge.fail_streak(x) for x in (None, poor, once, twice, thrice)] == [0, 0, 1, 2, 3]
    assert [knowledge.fail_wait(x) // DAY for x in (once, twice, thrice, many)] == [14, 28, 56, 56]   # doubled after every failure in a row, eight weeks at most
    back = knowledge.merge(thrice, {"kind": "bpd:udp", "ok": True, "score": 70, "latency_ms": 60.0}, "latency", NOW)
    again = knowledge.merge(back, {"kind": "bpd:udp", "ok": False, "error": "handshake did not complete"}, "latency", NOW)
    assert knowledge.fail_streak(again) == 1                                                          # a test that passed in between starts the count again
    p = {"kinds": {"bp:wss": poor, "bpd:udp": twice}}
    assert knowledge.plan_kinds(p, ["bp:wss", "bpd:udp"], NOW + 15 * DAY) == ([], {"poor": 1, "failing": 1})
    assert knowledge.plan_kinds(p, ["bp:wss", "bpd:udp"], NOW + 29 * DAY)[0] == ["bpd:udp"] and knowledge.plan_kinds(p, ["bp:wss"], NOW + 31 * DAY)[0] == ["bp:wss"]
    assert knowledge.blocked(p, NOW + 20 * DAY) == ["bpd:udp"] and knowledge.blocked(p, NOW + 29 * DAY) == []       # ... and a repair leaves it alone as long as the survey does


def test_the_other_transports_of_a_link_are_not_built_while_the_link_itself_does_not_pass():
    assert [knowledge.family_head(k) for k in ("tcpmux", "wssmux", "tcp", "ipip:wsmux", "wg:tcp", "gretap:tcpmux", "bp:wss", "paqet")] == ["tcpmux", "tcpmux", "tcpmux", "ipip:tcpmux", "wg:tcpmux", "gretap:tcpmux", "bp:wss", "paqet"]
    p = {"kinds": {"tcpmux": e("tcpmux", False, None, error="GRE is filtered on this route"), "wg:tcpmux": knowledge.merge(None, {"kind": "wg:tcpmux", "ok": False, "skipped": "tools"}, "latency", NOW)}}
    due, spared = knowledge.plan_kinds(p, ["wssmux", "tcp", "wsmux", "wg:tcp", "bp:wss"], NOW + DAY // 2)
    assert due == ["bp:wss"] and spared == {"link": 4}                                               # GRE did not pass, WireGuard could not be tried: their other transports would end the same way
    assert knowledge.plan_kinds(p, ["wssmux", "wg:tcp"], NOW + 15 * DAY)[0] == ["wssmux", "wg:tcp"]   # the heads are due again: so are they
    ok = {"kinds": {"tcpmux": e("tcpmux", True, 70, latency_ms=40.0)}}
    assert knowledge.plan_kinds(ok, ["wssmux", "tcp"], NOW)[0] == ["wssmux", "tcp"]                   # a link that passes: every transport of it is tested once


def test_the_build_names_the_server_that_lacks_the_tools():
    assert tunnel_test.tools_side(["Building…", "Build failed: Stopped: Foreign server fr-a: wireguard-tools is not installed (Servers -> its menu -> Install -> WireGuard tools)"]) == "foreign"
    assert tunnel_test.tools_side(["Stopped: Iran server ir-b: wireguard-tools is not installed (Servers -> ...)"]) == "iran"
    assert tunnel_test.tools_side(["Stopped: the tunnel started but no data passes through it"]) == "" and tunnel_test.tools_side(None) == ""
    assert knowledge.needs_tools("wg:tcpmux") and knowledge.needs_tools("wg:wssmux") and knowledge.needs_tools("wgchisel:tcpmux")
    assert not any(knowledge.needs_tools(k) for k in ("tcpmux", "bp:wssmux", "ipsec:tcpmux", "paqet"))


def test_a_server_seen_without_the_tools_keeps_every_pair_of_it_from_building_a_wireguard_kind(monkeypatch):
    from nikatel.services import license

    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    ir1, fr = servers("w1")

    with session() as db:
        other = Server(name="ir-w2", host=f"203.0.113.{next(HOSTS)}", role="iran", online=True)
        db.add(other)
        db.flush()
        ir2 = other.id

    live("w1", ir1, fr)
    live("w2", ir2, fr)
    now = int(time.time())
    plan = lambda: {p[:2]: p[2] for p in knowledge.pending_pairs(now)}
    assert "wg:tcpmux" in plan()[(ir2, fr)]                                                           # nobody knows anything yet: every kind of the first tier
    knowledge.record(ir1, fr, {"kind": "wg:tcpmux", "ok": False, "skipped": "tools", "tools_side": "foreign", "error": "not tested: wireguard-tools is not installed on a server"}, "latency")
    assert knowledge.no_tools_on(st.get("knowledge"), ir2, fr, now) and not knowledge.no_tools_on(st.get("knowledge"), ir2, ir1, now)
    got = plan()
    assert "wg:tcpmux" not in got[(ir2, fr)] and "wg:tcpmux" not in got[(ir1, fr)] and "bp:wssmux" in got[(ir2, fr)]      # ONE build said it: no pair of that server builds a WireGuard kind
    assert knowledge.plan_kinds({}, ["wg:tcpmux", "wg:tcp", "paqet"], now, no_tools=True) == (["paqet"], {"tools": 2})
    assert not knowledge.no_tools_on(st.get("knowledge"), ir2, fr, now + 8 * DAY)                    # asked again after a week ...
    knowledge.forget_tool_skips(fr)
    assert "wg:tcpmux" in plan()[(ir2, fr)] and st.get("knowledge")["no_tools"] == {}                # ... and at once when the owner installed the tools
    knowledge.record(ir1, fr, {"kind": "wg:tcpmux", "ok": False, "skipped": "tools", "error": "not tested"}, "latency")
    assert st.get("knowledge")["no_tools"] == {}                                                      # (a story that does not name the server marks none)


def test_the_page_counts_what_the_plan_tests_and_what_it_leaves_out():
    ir, fr = servers("pg")
    live("pg", ir, fr)
    old = int(time.time()) - 8 * DAY

    for k, s in (("bpd:pck", 95), ("chisel:fwd", 93), ("bp:wssmux", 91), ("chisel:tls", 89), ("bp:tcpmux", 87), ("bp:wss", 30), ("bp:tcp", 28)):
        knowledge.record(ir, fr, {"kind": k, "ok": True, "score": s, "latency_ms": 50.0, "loss_pct": 0.0}, "latency", now=old)

    row = next(r for r in knowledge.state()["pairs"] if r["iran_id"] == ir)
    assert row["spared"] == {"spare": 2}                                                              # a week old: the two that nothing would use are not built again
    assert row["due"] == len(knowledge.READY) - 2                                                     # the five needed ones and the fifteen nobody tested
    total = knowledge.state()["plan"]
    assert total["due"] >= row["due"] and total["spared"]["spare"] == 2 and set(total["spared"]) == set(knowledge.SPARED)
