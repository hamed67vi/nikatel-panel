"""The Doctor changes a tunnel that runs far slower than its own memory knows another type to be, even when no type of the pair is proven steady (the owner, 2026-11-23: of two configs of the same kind one gave 188 Mb/s and
the other 73 - "the Doctor must diagnose this and fix it well"). Tested: `speedier_kinds` (the case, and every reason to leave a type out), the look for a better type takes it - with the pair test of the running type as the
speed bar of the swap - and leaves a tunnel that was cut lately to the repair."""
from nikatel.services import knowledge as kn

NOW = 1_800_000_000
DAY = 86400


def _test(pair, kind, ok, down, measure="full", lat=85.0, at=NOW, loss=0.0, score="auto"):
    score = (70 if ok else 30) if score == "auto" else score
    pair["kinds"][kind] = kn.merge(pair["kinds"].get(kind), {"kind": kind, "ok": ok, "score": score, "down_mbps": down, "latency_ms": lat, "jitter_ms": 1.0, "loss_pct": loss}, measure, at)


def _hard_pair():
    """A pair where no type is proven steady: the running type is slow (a stopgap), another one is fast in six full tests and then failed two latency-only tests in a row (the server was busy that minute)."""
    p = {"kinds": {}}
    _test(p, "tcpmux", True, 140.0, at=NOW - 5 * DAY, lat=88.0)
    _test(p, "tcpmux", True, 137.0, at=NOW - 4 * DAY, lat=88.0)
    _test(p, "tcpmux", False, None, measure="latency", at=NOW - 20 * 3600, score=None, lat=None)

    for i, d in enumerate((1012.0, 924.0, 910.0, 958.0, 1082.0, 964.0)):
        _test(p, "bp:wss", True, d, at=NOW - (6 - i) * DAY, lat=84.0)

    _test(p, "bp:wss", False, None, measure="latency", at=NOW - 3600, score=44, lat=84.0)
    _test(p, "bp:wss", False, None, measure="latency", at=NOW - 1800, score=44, lat=84.0)
    return p


def test_a_much_faster_type_is_known_though_no_type_of_the_pair_is_proven_steady():
    p = _hard_pair()
    run, fast = kn.stability(p["kinds"]["tcpmux"], NOW), kn.stability(p["kinds"]["bp:wss"], NOW)
    assert run["pct"] < kn.STEADY_OK and fast["pct"] < kn.STEADY_OK                       # (the case: nothing is proven steady - the old rule could never act)
    assert kn.verdict(p["kinds"]["bp:wss"]) == "poor"                                      # (two failed latency-only tests called it poor)
    assert kn.upgrade_kinds(p, NOW, "tcpmux") == []
    rows = kn.speedier_kinds(p, NOW, "tcpmux")
    assert [r["kind"] for r in rows] == ["bp:wss"] and rows[0]["worst"] == 910.0 and rows[0]["base"] == 137.0 and rows[0]["passes"] == 6


def test_the_best_worst_test_goes_first_and_the_licence_and_missing_numbers_are_obeyed():
    p = _hard_pair()

    for d in (700.0, 650.0, 690.0, 400.0):
        _test(p, "bp:tcpmux", True, d, at=NOW - 3 * DAY, lat=90.0)

    assert [r["kind"] for r in kn.speedier_kinds(p, NOW, "tcpmux")] == ["bp:wss", "bp:tcpmux"]
    assert [r["kind"] for r in kn.speedier_kinds(p, NOW, "tcpmux", allowed=lambda k: k != "bp:wss")] == ["bp:tcpmux"]
    assert kn.speedier_kinds(None, NOW, "tcpmux") == [] and kn.speedier_kinds({"kinds": {}}, NOW, "tcpmux") == []
    assert kn.speedier_kinds(p, NOW, "nothing-runs-like-this") == []                       # (the running type has no test: nothing to compare with, nothing is guessed)


def test_a_running_type_that_is_proven_steady_is_left_by_the_steady_rule_only():
    p = _hard_pair()
    _test(p, "tcpmux", True, 138.0, at=NOW - 3 * DAY, lat=88.0)
    _test(p, "tcpmux", True, 139.0, at=NOW - 2 * DAY, lat=88.0)
    _test(p, "tcpmux", True, 137.0, at=NOW - 1 * DAY, lat=88.0)
    assert kn.stability(p["kinds"]["tcpmux"], NOW)["pct"] >= kn.STEADY_OK
    assert kn.speedier_kinds(p, NOW, "tcpmux") == []


def test_a_type_is_left_out_when_its_proof_is_thin_or_it_failed_a_full_test_or_it_is_not_that_much_faster():
    p = _hard_pair()
    thin = {"kinds": {k: v for k, v in p["kinds"].items()}}
    thin["kinds"]["bp:wss"] = None
    _test(thin, "bp:wss", True, 900.0, at=NOW - 2 * DAY, lat=84.0)
    _test(thin, "bp:wss", True, 950.0, at=NOW - 1 * DAY, lat=84.0)
    assert kn.speedier_kinds(thin, NOW, "tcpmux") == []                                    # (two passed full tests are not enough)

    failed = _hard_pair()
    _test(failed, "bp:wss", False, None, measure="full", at=NOW - 2 * DAY, lat=84.0)
    assert kn.speedier_kinds(failed, NOW, "tcpmux") == []                                  # (a full test of the last week failed: a type that fell is no answer, however fast its best test was)

    slow_floor = _hard_pair()
    _test(slow_floor, "bp:wss", True, 220.0, at=NOW - 2 * DAY, lat=84.0)
    assert kn.speedier_kinds(slow_floor, NOW, "tcpmux") == []                              # (one test gave only 220: not twice and 100 more than 137 at its worst)

    lossy = _hard_pair()
    _test(lossy, "bp:wss", True, 960.0, at=NOW - 1000, lat=84.0, loss=3.0)
    assert kn.speedier_kinds(lossy, NOW, "tcpmux") == []

    slow_ping = _hard_pair()
    _test(slow_ping, "bp:wss", True, 960.0, at=NOW - 1000, lat=300.0)
    assert kn.speedier_kinds(slow_ping, NOW, "tcpmux") == []                               # (a ping three times the running one: the users feel that first)


def test_a_type_that_a_full_test_called_poor_is_left_out_but_one_that_a_latency_test_called_poor_is_not():
    p = _hard_pair()
    assert kn.verdict(p["kinds"]["bp:wss"]) == "poor" and p["kinds"]["bp:wss"]["last"]["measure"] == "latency" and [r["kind"] for r in kn.speedier_kinds(p, NOW, "tcpmux")] == ["bp:wss"]
    full_poor = _hard_pair()
    _test(full_poor, "bp:wss", False, 150.0, measure="full", at=NOW - 600, lat=84.0, score=30)          # (it carried data, but under the line, in a FULL test)
    assert kn.verdict(full_poor["kinds"]["bp:wss"]) == "poor" and full_poor["kinds"]["bp:wss"]["last"]["measure"] == "full"
    assert kn.speedier_kinds(full_poor, NOW, "tcpmux") == []


def _look(monkeypatch, pair, cuts_in):
    """Run the look for a better type of tunnel 7 on `pair`; `cuts_in(hours)`: how many cuts the tunnel had in that many hours. Returns what was started."""
    from nikatel.config import get_settings
    from nikatel.services import actions, autopilot, bandwidth, doctor, license

    seen = []
    snap = {"type": "backhaul", "transport": "tcpmux", "record": {}, "iran_id": 1, "foreign_id": 2, "name": "T1", "iran_name": "I", "foreign_name": "F"}
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: snap)
    monkeypatch.setattr(autopilot, "kind_of", lambda *a: "tcpmux")
    monkeypatch.setattr(kn, "pair", lambda i, f: pair)
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    monkeypatch.setattr(doctor, "_state", lambda: {"suggestions": []})
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: seen.append((label, fn, a, k)) or 1)
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(bandwidth.time, "time", lambda: NOW)
    monkeypatch.setattr(bandwidth, "_cuts_lately", lambda tid, now, hours=24: cuts_in(hours))
    bandwidth._UPGRADE_AT.clear()
    bandwidth._upgrade_check(7)
    return seen, doctor


def test_the_look_for_a_better_type_takes_the_much_faster_type_and_hands_the_swap_the_pair_test_as_its_speed_bar(monkeypatch):
    seen, doctor = _look(monkeypatch, _hard_pair(), lambda hours: 0)
    (_label, fn, args, kwargs), = seen
    assert fn is doctor._run_retype and args[0] == 7 and args[1] == "bp:wss" and args[2] == "tcpmux" and args[4] is True and args[5] is False and args[6] == 137.0
    assert "carried 137 Mb/s" in args[3] and "never less than 910 Mb/s" in args[3] and "not proven steady" in args[3] and "users are not cut" in args[3] and kwargs["source"] == "auto"


def test_a_tunnel_cut_in_the_last_six_hours_is_left_to_the_repair_even_when_a_much_faster_type_is_known(monkeypatch):
    seen, _ = _look(monkeypatch, _hard_pair(), lambda hours: 1 if hours <= 6 else 3)
    assert seen == []


def test_nothing_is_started_when_no_type_is_much_faster(monkeypatch):
    p = {"kinds": {}}

    for i, d in enumerate((130.0, 140.0, 150.0)):
        _test(p, "tcpmux", True, d, at=NOW - (3 - i) * DAY, lat=88.0)
        _test(p, "tcp", True, d + 20, at=NOW - (3 - i) * DAY, lat=88.0)

    seen, _ = _look(monkeypatch, p, lambda hours: 0)
    assert seen == []
