"""The wire cost of a tunnel type (1.69.223; the owner, 2026-11-05: "a type that is proven to carry a lot of overhead, like KCP, must be changed; the overhead is one of the Doctor's important benchmarks when it chooses
a type - but it must still look at the quality"). What a type costs on the wire for every byte its users move: measured by the speed test (the card of the Iran server before, during and after), or the design's own
number; a heavy running type is changed for a lighter, proven steady one that is nearly as good; the quality still counts."""
import time

from nikatel.config import get_settings
from nikatel.services import (
    actions,
    autopilot,
    bandwidth,
    doctor,
    knowledge,
    license,
    overhead,
)

NOW = 1_800_000_000
H = 3600


def entry(score=80, down=300.0, lat=90.0, tests=3, wire=None, ok=True):
    """One entry of the pair's memory: its last full test and `tests` kept full tests."""
    full = {"score": score, "at": NOW - 3 * H, "down_mbps": down, "latency_ms": lat, "loss_pct": 0.0, "jitter_ms": 2.0}
    e = {"last": {"ok": ok, "measured": True, "at": NOW - 3 * H, "measure": "full", "score": score, "error": ""}, "full": full,
         "hist": [{"at": NOW - h * H, "ok": ok, "m": "full", "down": down, "loss": 0.0} for h in (30, 20, 10)][:tests]}

    if wire:
        e["wire"] = list(wire)

    return e


def pair(**kinds):
    return {"at": NOW, "kinds": kinds}


# ------------------------------------------------------------------ the model
def test_every_kind_the_doctor_knows_has_a_cost_by_design():
    kinds = set(knowledge.PRIOR) | set(knowledge.CARRIER_KINDS) | {f"{k.split(':')[0]}:{tr}" for k in knowledge.LINK_KINDS for tr in knowledge.VARIANT_TRANSPORTS}
    assert all(overhead.model_cost(k) != overhead.UNKNOWN for k in kinds), [k for k in kinds if overhead.model_cost(k) == overhead.UNKNOWN]
    assert overhead.model_cost("something:new") == overhead.UNKNOWN and overhead.model_cost("") == overhead.UNKNOWN


def test_the_heavy_types_are_kcp_and_icmp_and_the_light_ones_are_plain_tunnels():
    heavy = {k for k in knowledge.PRIOR if overhead.model_cost(k) >= overhead.HEAVY}
    assert heavy == {"paqet", "bp:kcp", "bpd:xdi", "bp:xdi"}                                             # (KCP with its parity and resends, and KCP inside ICMP echo)
    assert all(overhead.model_cost(k) < 1.2 for k in ("tcpmux", "bp:tcpmux", "bpd:pck", "chisel:fwd", "ipip:tcpmux", "wg:tcpmux"))
    assert overhead.model_cost("wssmux") > overhead.model_cost("tcpmux") and overhead.model_cost("fou:wssmux") > overhead.model_cost("fou:tcpmux")          # (a heavier transport costs more)


def test_a_lighter_type_counts_more_and_a_cost_of_two_point_two_counts_a_quarter():
    assert overhead.lightness(1.0) == 1.0 and overhead.lightness(1.10) == 1.0 and abs(overhead.lightness(2.2) - 0.25) < 1e-9
    assert overhead.lightness(1.04) == overhead.lightness(1.09) == 1.0                                       # (the plain types count the same: the model is not that exact)
    assert 1.0 > overhead.lightness(1.13) > overhead.lightness(1.4) > overhead.lightness(1.6) > overhead.lightness(1.9)
    assert overhead.lightness(0.5) == 1.0                                                                   # (a card cannot carry less than was moved)


# ------------------------------------------------------------------ the measurement
def test_a_calm_card_gives_the_ratio_and_a_busy_one_gives_none():
    base = {"w_moved": 100e6, "w_nic": 111.15e6, "w_dur": 6.0, "w_pre": 1e6, "w_post": 1.05e6}
    assert overhead.wire_ratio(base) == 1.05                                                                # (111 MB crossed, 6 MB of it would have crossed anyway: the 100 MB cost 105 MB on the wire)
    assert overhead.wire_ratio({**base, "w_post": 6e6}) is None                                             # (the card was 5x busier after than before: no number)
    assert overhead.wire_ratio({**base, "w_moved": 5e6}) is None                                            # (a test that moved 5 MB says nothing)
    assert overhead.wire_ratio({**base, "w_pre": 30e6, "w_post": 30e6}) is None                             # (the card carried more on its own during the streams than the streams moved)
    assert overhead.wire_ratio({k: v for k, v in base.items() if k != "w_nic"}) is None and overhead.wire_ratio(None) is None and overhead.wire_ratio({}) is None


def test_one_baseline_is_enough_when_it_is_small_and_a_ratio_below_one_is_one():
    assert overhead.wire_ratio({"w_moved": 100e6, "w_nic": 108e6, "w_dur": 6.0, "w_post": 0.2e6}) == 1.07
    assert overhead.wire_ratio({"w_moved": 100e6, "w_nic": 99e6, "w_dur": 6.0, "w_pre": 0.0, "w_post": 0.0}) == 1.0
    assert overhead.wire_ratio({"w_moved": 100e6, "w_nic": 900e6, "w_dur": 6.0, "w_pre": 0.0, "w_post": 0.0}) is None          # (nine times: a reading gone wrong)
    assert overhead.wire_ratio({"w_moved": 100e6, "w_nic": 40e6, "w_dur": 6.0, "w_pre": 0.0, "w_post": 0.0}) is None           # (less than half crossed the card: the tunnel does not use it)
    assert overhead.wire_ratio({"w_moved": "x", "w_nic": 1, "w_dur": 1, "w_pre": 0}) is None


def test_the_measured_median_decides_and_a_link_only_test_leaves_it_to_the_model():
    assert overhead.cost("chisel:tls", [1.9, 2.0, 2.4]) == (2.0, "measured")
    assert overhead.cost("chisel:tls", None) == (1.13, "model") and overhead.cost("chisel:tls", ["x"]) == (1.13, "model")
    assert overhead.cost("tcpmux", [3.0], raw=True) == (1.06, "model")                                      # (Backhaul over GRE is tested over the link: its first layer only)


def test_the_memory_keeps_the_last_five_measured_ratios_of_full_tests_only():
    e = None

    for i in range(7):
        e = knowledge.merge(e, {"kind": "paqet", "ok": True, "score": 70, "down_mbps": 100.0, "latency_ms": 80.0, "wire_ratio": round(1.3 + i / 100, 2)}, "full", NOW + i)

    assert e["wire"] == [1.32, 1.33, 1.34, 1.35, 1.36] and e["full"]["wire_ratio"] == 1.36
    e = knowledge.merge(e, {"kind": "paqet", "ok": True, "score": 70, "latency_ms": 80.0, "wire_ratio": 9.0}, "latency", NOW + 9)
    assert e["wire"] == [1.32, 1.33, 1.34, 1.35, 1.36]                                                      # (a ping test has no streams)
    e = knowledge.merge(e, {"kind": "paqet", "ok": True, "score": 70, "down_mbps": 100.0, "latency_ms": 80.0}, "full", NOW + 10)
    assert e["wire"] == [1.32, 1.33, 1.34, 1.35, 1.36]                                                      # (a full test whose card was busy added no number and took none away)


# ------------------------------------------------------------------ the ranking
def test_at_equal_quality_the_lighter_type_ranks_first():
    rows = knowledge.ranked(pair(**{"bp:kcp": entry(), "bp:tcpmux": entry(), "paqet": entry()}), NOW)
    assert [r["kind"] for r in rows] == ["bp:tcpmux", "paqet", "bp:kcp"]
    assert rows[0]["wire"] == 1.04 and rows[0]["wire_src"] == "model" and rows[2]["wire"] == 1.6


def test_the_quality_still_counts_a_much_better_heavy_type_wins_over_a_poor_light_one():
    p = pair(**{"bp:kcp": entry(score=95, down=900.0, lat=50.0), "bp:tcpmux": entry(score=30, down=4.0, lat=400.0)})
    assert [r["kind"] for r in knowledge.ranked(p, NOW)] == ["bp:kcp", "bp:tcpmux"]
    p = pair(**{"bp:kcp": entry(score=85, down=300.0, lat=90.0), "bp:tcpmux": entry(score=80, down=250.0, lat=95.0)})
    assert [r["kind"] for r in knowledge.ranked(p, NOW)] == ["bp:tcpmux", "bp:kcp"]                          # (nearly the same quality: the cheaper one)


def test_a_measured_cost_replaces_the_design_cost_in_the_ranking():
    p = pair(**{"chisel:tls": entry(wire=[1.9, 2.0, 2.1]), "bp:wss": entry()})
    rows = knowledge.ranked(p, NOW)
    assert [r["kind"] for r in rows] == ["bp:wss", "chisel:tls"] and rows[1]["wire"] == 2.0 and rows[1]["wire_src"] == "measured"


# ------------------------------------------------------------------ the lighter type for a running heavy one
def test_a_heavy_running_type_gets_a_lighter_proven_steady_one_that_is_nearly_as_good():
    p = pair(**{"bp:kcp": entry(score=80, down=300.0), "bp:tcpmux": entry(score=78, down=280.0, lat=95.0)})
    assert [r["kind"] for r in knowledge.lighter_kinds(p, NOW, "bp:kcp")] == ["bp:tcpmux"]
    assert knowledge.lighter_kinds(p, NOW, "bp:tcpmux") == []                                               # (a plain type costs a few percent: nothing to gain)
    assert knowledge.lighter_kinds(p, NOW, "bp:kcp", allowed=lambda k: k != "bp:tcpmux") == []              # (the licence)


def test_the_lighter_type_must_be_proven_steady_clean_fast_enough_and_not_a_poorer_one():
    heavy = entry(score=80, down=300.0)
    assert knowledge.lighter_kinds(pair(**{"bp:kcp": heavy, "bp:tcpmux": entry(tests=2)}), NOW, "bp:kcp") == []                       # (two tests: not proven)
    assert knowledge.lighter_kinds(pair(**{"bp:kcp": heavy, "bp:tcpmux": entry(ok=False)}), NOW, "bp:kcp") == []                      # (it failed its tests)
    assert knowledge.lighter_kinds(pair(**{"bp:kcp": heavy, "bp:tcpmux": entry(score=40, down=20.0)}), NOW, "bp:kcp") == []           # (steadiness and lightness are not bought with a slow tunnel)
    assert knowledge.lighter_kinds(pair(**{"bp:kcp": heavy, "bp:tcpmux": entry(lat=400.0)}), NOW, "bp:kcp") == []                     # (its ping is much worse)
    assert knowledge.lighter_kinds(pair(**{"bp:kcp": heavy, "paqet": entry()}), NOW, "bp:kcp") == []                                  # (paqet costs 1.40: not enough lighter than 1.60)
    old = entry()
    old["full"]["at"] = NOW - 20 * 86400
    assert knowledge.lighter_kinds(pair(**{"bp:kcp": heavy, "bp:tcpmux": old}), NOW, "bp:kcp") == []                                  # (its last test is three weeks old)
    stale = entry()
    stale["full"]["at"] = NOW - 20 * 86400
    assert knowledge.lighter_kinds(pair(**{"bp:kcp": stale, "bp:tcpmux": entry()}), NOW, "bp:kcp") == []                              # (the running type has no fresh test to compare with)


def test_the_look_for_a_better_type_changes_a_heavy_one_for_a_lighter_one_by_itself(monkeypatch):
    now = int(time.time())
    assert autopilot.kind_of("backpack", "kcp", {}) == "bp:kcp"
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: {"type": "backpack", "transport": "kcp", "record": {}, "iran_id": 1, "foreign_id": 2, "iran_name": "ir", "foreign_name": "fr", "name": "fr-ir"})

    def fresh(e):
        e["full"]["at"] = now - 3 * H
        e["hist"] = [{"at": now - h * H, "ok": True, "m": "full", "down": e["full"]["down_mbps"], "loss": 0.0} for h in (30, 20, 10)]
        return e

    p = pair(**{"bp:kcp": fresh(entry(score=80, down=300.0)), "bp:tcpmux": fresh(entry(score=79, down=290.0, lat=92.0))})
    monkeypatch.setattr(knowledge, "pair", lambda i, f: p)
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    started = []
    monkeypatch.setattr(doctor, "suggest", lambda *a, **k: {})
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn, a, k)) or 1)
    bandwidth._UPGRADE_AT.clear()
    bandwidth._upgrade_check(7)
    assert len(started) == 1
    label, fn, a, k = started[0]
    assert label == "fr-ir" and fn is doctor._run_retype and a[:3] == (7, "bp:tcpmux", "bp:kcp") and a[4] is True and k["source"] == "auto"
    assert "1.60" in a[3] and "1.04" in a[3] and "wire" in a[3]                                              # (the sentence says what each costs on the wire)
    p["kinds"]["bp:tcpmux"]["hist"] = p["kinds"]["bp:tcpmux"]["hist"][:2]
    bandwidth._UPGRADE_AT.clear()
    bandwidth._upgrade_check(7)
    assert len(started) == 1                                                                                # (not proven steady: the heavy type stays)


def test_a_heavy_type_is_no_upgrade_for_a_lighter_running_one_so_the_tunnel_does_not_go_back_and_forth():
    p = pair(**{"bp:tcpmux": entry(score=80, down=300.0), "bp:kcp": entry(score=90, down=900.0)})
    assert knowledge.upgrade_kinds(p, NOW, "bp:tcpmux") == []                                               # (three times as fast, but it costs 1.6 against 1.04: the next look would take the tunnel back)
    p = pair(**{"bp:tcpmux": entry(score=80, down=300.0), "bp:wssmux": entry(score=90, down=900.0)})
    assert [r["kind"] for r in knowledge.upgrade_kinds(p, NOW, "bp:tcpmux")] == ["bp:wssmux"]              # (a light type that is much faster is still an upgrade)
    p = pair(**{"bp:kcp": entry(score=80, down=300.0), "paqet": entry(score=90, down=900.0)})
    assert [r["kind"] for r in knowledge.upgrade_kinds(p, NOW, "bp:kcp")] == ["paqet"]                     # (from KCP to a type that costs less is fine, heavy or not)
