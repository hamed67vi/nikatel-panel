"""A type no test ever timed on a pair is timed there when other Iran servers' pairs to the same foreign server proved it much faster than the running type (the owner, 2026-11-24: the pair of the moved Germany-2
tunnel held no speed number of any BackPack type, so the rule of 1.69.300 had nothing to act on). Tested: `cousin_worsts` (whose proof counts), `scout_kinds` (which kinds are tried and which are not), `verify_set`
(the scout goes after the running type and its rivals), `speedier_kinds` with outside proof (one passed full test is enough), and the look for a better type that takes it."""
from nikatel.services import knowledge as kn

NOW = 1_800_000_000
DAY = 86400


def _test(pair, kind, ok, down, measure="full", lat=85.0, at=NOW, loss=0.0, score="auto"):
    score = (70 if ok else 30) if score == "auto" else score
    pair["kinds"][kind] = kn.merge(pair["kinds"].get(kind), {"kind": kind, "ok": ok, "score": score, "down_mbps": down, "latency_ms": lat, "jitter_ms": 1.0, "loss_pct": loss}, measure, at)


def _ours():
    """The pair of the moved tunnel: the running type gave 153 in its pair test, every BackPack type has ONE latency-only test that called it poor (no speed number)."""
    p = {"kinds": {}}
    _test(p, "ipsec:tcpmux", True, 150.0, at=NOW - 4 * DAY, lat=88.0)
    _test(p, "ipsec:tcpmux", True, 153.0, at=NOW - 3 * DAY, lat=88.0)
    _test(p, "ipsec:tcpmux", False, None, measure="latency", at=NOW - 2 * DAY, score=None, lat=None)

    for k in ("bp:wss", "bp:tcpmux", "bp:wssmux"):
        _test(p, k, False, None, measure="latency", at=NOW - 4 * DAY, score=44, lat=83.0)

    return p


def _cousin(kind="bp:wss", downs=(1012.0, 924.0, 910.0, 958.0), at=NOW - 3 * DAY):
    p = {"kinds": {}}

    for j, d in enumerate(downs):
        _test(p, kind, True, d, at=at - j * 3600, lat=84.0)

    return p


def _doc(**extra):
    return {"8:73": _ours(), "44:73": _cousin(), "11:73": _cousin("bp:tcpmux", (700.0, 650.0, 690.0)), "44:90": _cousin(), **extra}


def test_other_iran_servers_to_the_same_foreign_server_count_and_nothing_else_does():
    z = kn.cousin_worsts(_doc(), 8, 73, NOW)
    assert z["bp:wss"] == {"worst": 910.0, "passes": 4, "iran_id": 44} and z["bp:tcpmux"]["worst"] == 650.0
    assert "ipsec:tcpmux" not in z                                                         # (this pair's own tests are no outside proof)
    assert kn.cousin_worsts({"8:73": _ours(), "44:90": _cousin()}, 8, 73, NOW) == {}       # (another foreign server says nothing about this one)
    assert kn.cousin_worsts({"8:73": _cousin()}, 8, 73, NOW) == {}                         # (the same pair is not its own cousin)
    assert kn.cousin_worsts(None, 8, 73, NOW) == {} and kn.cousin_worsts({"bad-key": _cousin()}, 8, 73, NOW) == {}


def test_thin_old_or_failed_outside_proof_does_not_count():
    thin = _cousin(downs=(1000.0, 990.0))
    assert kn.cousin_worsts({"44:73": thin}, 8, 73, NOW) == {}                             # (two passed full tests are not enough)
    old = _cousin(at=NOW - 40 * DAY)
    assert kn.cousin_worsts({"44:73": old}, 8, 73, NOW) == {}                              # (a month old is only a hint)
    fell = _cousin()
    _test(fell, "bp:wss", False, None, measure="full", at=NOW - 1 * DAY, lat=84.0)
    assert kn.cousin_worsts({"44:73": fell}, 8, 73, NOW) == {}                             # (a full test of the last week failed there)
    better = kn.cousin_worsts({"44:73": _cousin(downs=(900.0, 905.0, 910.0)), "11:73": _cousin(downs=(990.0, 980.0, 970.0))}, 8, 73, NOW)
    assert better["bp:wss"]["worst"] == 970.0 and better["bp:wss"]["iran_id"] == 11        # (the best proof of the pairs)


def test_a_kind_without_a_speed_test_is_scouted_when_outside_proof_says_it_is_much_faster():
    z = kn.cousin_worsts(_doc(), 8, 73, NOW)
    assert kn.verdict(_ours()["kinds"]["bp:wss"]) == "poor"                                # (a latency-only test called it poor: nobody's rival, not looked at for a month)
    assert kn.speedier_kinds(_ours(), NOW, "ipsec:tcpmux") == []                           # (the rule of 1.69.300 alone has nothing to act on)
    assert kn.scout_kinds(_ours(), NOW, "ipsec:tcpmux", z) == ["bp:wss"]                   # (the best proof first, at most one)
    assert kn.scout_kinds(_ours(), NOW, "ipsec:tcpmux", z, allowed=lambda k: k != "bp:wss") == ["bp:tcpmux"]


def test_a_kind_is_not_scouted_when_it_has_a_speed_test_failed_or_is_not_that_much_faster_or_the_running_type_is_steady():
    z = kn.cousin_worsts(_doc(), 8, 73, NOW)
    tested = _ours()
    _test(tested, "bp:wss", True, 300.0, at=NOW - 3600)
    assert kn.scout_kinds(tested, NOW, "ipsec:tcpmux", z) == ["bp:tcpmux"]                 # (bp:wss has its speed number now: the rule of 1.69.300 judges it)

    failed = _ours()
    failed["kinds"]["bp:wss"] = {}
    _test(failed, "bp:wss", False, None, measure="latency", at=NOW - 3600, score=None, lat=None)
    assert "bp:wss" not in kn.scout_kinds(failed, NOW, "ipsec:tcpmux", z)                  # (it did not even connect: the failure rules wait, nothing is built for it here)

    slow = {"kinds": {}}

    for d in (150.0, 160.0, 170.0):
        _test(slow, "ipsec:tcpmux", True, d, at=NOW - 3 * DAY, lat=88.0)

    _test(slow, "ipsec:tcpmux", False, None, measure="latency", at=NOW - 2 * DAY, score=None, lat=None)
    _test(slow, "ipsec:tcpmux", False, None, measure="latency", at=NOW - DAY, score=None, lat=None)
    _test(slow, "bp:wss", False, None, measure="latency", at=NOW - DAY, score=44, lat=83.0)
    assert kn.scout_kinds(slow, NOW, "ipsec:tcpmux", {"bp:wss": {"worst": 280.0, "passes": 5, "iran_id": 44}}) == []          # (280 is not twice 150 and 100 more)
    assert kn.scout_kinds(slow, NOW, "ipsec:tcpmux", {"bp:wss": {"worst": 340.0, "passes": 5, "iran_id": 44}}) == ["bp:wss"]

    steady = _ours()

    for d in (152.0, 154.0, 151.0):
        _test(steady, "ipsec:tcpmux", True, d, at=NOW - 2 * DAY, lat=88.0)

    assert kn.stability(steady["kinds"]["ipsec:tcpmux"], NOW)["pct"] >= kn.STEADY_OK
    assert kn.scout_kinds(steady, NOW, "ipsec:tcpmux", z) == []                            # (a steady running type is left to the steady rule)
    assert kn.scout_kinds(_ours(), NOW, "no-such-type", z) == [] and kn.scout_kinds(_ours(), NOW, "ipsec:tcpmux", None) == [] and kn.scout_kinds(None, NOW, "ipsec:tcpmux", z) == []


def test_the_verify_survey_tests_the_scout_after_the_running_type_and_its_rivals():
    z = kn.cousin_worsts(_doc(), 8, 73, NOW)
    assert kn.verify_set(_ours(), NOW, {"ipsec:tcpmux"}, z) == ["bp:wss"]                  # (the running type was timed 3 days ago: it is not due, the scout is)
    old = _ours()
    old["kinds"]["ipsec:tcpmux"] = {}
    _test(old, "ipsec:tcpmux", True, 150.0, at=NOW - 12 * DAY, lat=88.0)
    _test(old, "ipsec:tcpmux", True, 153.0, at=NOW - 11 * DAY, lat=88.0)
    _test(old, "ipsec:tcpmux", False, None, measure="latency", at=NOW - 10 * DAY, score=None, lat=None)
    assert kn.verify_set(old, NOW, {"ipsec:tcpmux"}, z) == ["ipsec:tcpmux", "bp:wss"]      # (a running type whose speed test is old is timed first, the scout after it)
    assert kn.verify_set(_ours(), NOW, {"ipsec:tcpmux"}, {}) == [] and kn.verify_set(_ours(), NOW, {"ipsec:tcpmux"}, None) == []          # (no outside proof, nothing new)


def test_one_passed_full_test_is_enough_when_other_iran_servers_proved_the_type_and_not_otherwise():
    z = kn.cousin_worsts(_doc(), 8, 73, NOW)
    p = _ours()
    _test(p, "bp:wss", True, 940.0, at=NOW - 600, lat=84.0)                                # (the scout test passed on this pair)
    assert kn.speedier_kinds(p, NOW, "ipsec:tcpmux") == []                                 # (one test is thin proof alone)
    rows = kn.speedier_kinds(p, NOW, "ipsec:tcpmux", cousin=z)
    assert [r["kind"] for r in rows] == ["bp:wss"] and rows[0]["passes"] == 1 and rows[0]["worst"] == 940.0 and rows[0]["backed"]["worst"] == 910.0

    low = _ours()
    _test(low, "bp:wss", True, 200.0, at=NOW - 600, lat=84.0)
    assert kn.speedier_kinds(low, NOW, "ipsec:tcpmux", cousin=z) == []                     # (its own test did not clear the bar, whatever the others say)

    failed = _ours()
    _test(failed, "bp:wss", False, None, measure="full", at=NOW - 3600, lat=84.0)
    _test(failed, "bp:wss", True, 940.0, at=NOW - 600, lat=84.0)
    assert kn.speedier_kinds(failed, NOW, "ipsec:tcpmux", cousin=z) == []                  # (a failed full test of the last week still counts)


def _look(monkeypatch, pair, cousin):
    from nikatel.config import get_settings
    from nikatel.services import actions, autopilot, bandwidth, doctor, license

    seen = []
    snap = {"type": "backhaul", "transport": "tcpmux", "record": {}, "iran_id": 8, "foreign_id": 73, "name": "T1", "iran_name": "rapido", "foreign_name": "DE3"}
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: snap)
    monkeypatch.setattr(autopilot, "kind_of", lambda *a: "ipsec:tcpmux")
    monkeypatch.setattr(kn, "pair", lambda i, f: pair)
    monkeypatch.setattr(kn, "cousins", lambda i, f, now: cousin)
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    monkeypatch.setattr(doctor, "_state", lambda: {"suggestions": []})
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: seen.append((label, fn, a, k)) or 1)
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(bandwidth.time, "time", lambda: NOW)
    monkeypatch.setattr(bandwidth, "_cuts_lately", lambda tid, now, hours=24: 0)
    bandwidth._UPGRADE_AT.clear()
    bandwidth._upgrade_check(7)
    return seen, doctor


def test_the_look_for_a_better_type_takes_a_type_with_one_passed_test_and_outside_proof(monkeypatch):
    p = _ours()
    _test(p, "bp:wss", True, 940.0, at=NOW - 600, lat=84.0)
    seen, doctor = _look(monkeypatch, p, kn.cousin_worsts(_doc(), 8, 73, NOW))
    (_label, fn, args, kwargs), = seen
    assert fn is doctor._run_retype and args[0] == 7 and args[1] == "bp:wss" and args[2] == "ipsec:tcpmux" and args[4] is True and args[5] is False and args[6] == 153.0
    assert "pairs of other Iran servers to DE3" in args[3] and "910 Mb/s at the least" in args[3] and "users are not cut" in args[3] and kwargs["source"] == "auto"


def test_the_look_for_a_better_type_leaves_a_thin_pair_alone_without_outside_proof(monkeypatch):
    p = _ours()
    _test(p, "bp:wss", True, 940.0, at=NOW - 600, lat=84.0)
    seen, _ = _look(monkeypatch, p, {})
    assert seen == []
