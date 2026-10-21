"""The Doctor chooses a tunnel type for MANY users: speed, ping, jitter AND steadiness (the owner, 2026-10-21: "the type must be steady, it must have passed all the tests - not one that works at first and later shows
low bandwidth or problems"). Tested: the memory keeps the last tests of a kind, `stability` is None under three tests, a type that failed or fell goes behind one that passed all of them (also when its best test
was faster), an unsteady type is no upgrade, `steadier_kinds`, and the Doctor offers a steadier type to a tunnel that was cut three times in a day."""
from nikatel.services import knowledge as kn

NOW = 1_800_000_000
DAY = 86400


def _add(kind, pair, ok, down, lat=40.0, at=None, score=70, loss=0.0, down1=None):
    pair["kinds"][kind] = kn.merge(pair["kinds"].get(kind), {"kind": kind, "ok": ok, "score": score if ok else 30, "down_mbps": down, "down1_mbps": down1 if down1 is not None else down / 4,
                                                             "latency_ms": lat, "jitter_ms": 3.0, "loss_pct": loss}, "full", at or NOW)


def _pair(runs):
    """runs: {kind: [(ok, down, lat), ...]} one test a day, the newest last."""
    p = {"kinds": {}}
    n = max(len(v) for v in runs.values())

    for kind, rows in runs.items():
        for i, (ok, down, lat) in enumerate(rows):
            _add(kind, p, ok, down, lat, at=NOW - (n - 1 - i) * DAY)

    return p


def test_the_memory_keeps_the_last_tests_of_a_kind_and_only_twelve():
    e = None

    for i in range(15):
        e = kn.merge(e, {"kind": "tcp", "ok": True, "score": 70, "down_mbps": 100.0 + i, "latency_ms": 40.0}, "full", NOW + i)

    assert len(e["hist"]) == kn.HIST == 12 and e["hist"][-1]["down"] == 114.0 and e["hist"][0]["down"] == 103.0 and e["last"]["ok"]
    skipped = kn.merge(e, {"kind": "tcp", "skipped": "no tools"}, "full", NOW + 99)
    assert len(skipped["hist"]) == 12                                      # (a test that was not made is no test)


def test_stability_is_unknown_under_three_tests_and_a_fall_or_a_failure_lowers_it():
    one = _pair({"tcpmux": [(True, 300, 40)]})
    assert kn.stability(one["kinds"]["tcpmux"], NOW) is None
    steady = _pair({"tcp": [(True, 300, 40)] * 5})
    s = kn.stability(steady["kinds"]["tcp"], NOW)
    assert s == {"n": 5, "passed": 5, "swing": 1.0, "pct": 100}
    fell = _pair({"x": [(True, 300, 40), (True, 290, 40), (True, 40, 40), (True, 300, 40)]})
    assert kn.stability(fell["kinds"]["x"], NOW)["swing"] == round(40 / 300, 2) and kn.stability(fell["kinds"]["x"], NOW)["pct"] < kn.STEADY_OK
    failed = _pair({"y": [(True, 300, 40), (False, 0.0, 40), (True, 300, 40), (True, 300, 40)]})
    assert kn.stability(failed["kinds"]["y"], NOW)["passed"] == 3
    old = _pair({"z": [(True, 300, 40)] * 4})
    assert kn.stability(old["kinds"]["z"], NOW + 40 * DAY) is None          # (tests older than a month are not counted)


def test_a_steady_type_goes_before_a_faster_one_that_fell_in_its_tests():
    p = _pair({"tcpmux": [(True, 90, 40), (True, 600, 40), (True, 80, 40), (True, 600, 40)],            # fast when it works, but it keeps falling
               "tcp": [(True, 400, 42)] * 4})
    rows = kn.ranked(p, NOW)
    assert [r["kind"] for r in rows] == ["tcp", "tcpmux"] and rows[0]["steady"] == 100 and rows[1]["steady"] < kn.STEADY_OK
    assert "tests passed" in kn._why_good(rows[0], NOW) and "UNSTEADY" in kn._why_good(rows[1], NOW)


def test_a_type_tested_too_few_times_counts_a_little_less_than_a_steady_one_of_the_same_speed():
    p = _pair({"tcpmux": [(True, 400, 40)], "tcp": [(True, 400, 40)] * 4})
    assert [r["kind"] for r in kn.ranked(p, NOW)] == ["tcp", "tcpmux"]
    assert "stability not proven yet" in kn._why_good(kn.ranked(p, NOW)[1], NOW)
    same = _pair({"tcpmux": [(True, 400, 40)], "tcp": [(True, 400, 40)]})
    assert [r["kind"] for r in kn.ranked(same, NOW)] == ["tcpmux", "tcp"]          # (nothing is proven for either: the old order stays)


def test_an_unsteady_type_is_no_upgrade_and_a_steady_one_is():
    p = _pair({"tcpmux": [(True, 100, 40)] * 4,
               "bpd:pck": [(True, 120, 40), (True, 900, 40), (True, 100, 40), (True, 880, 40)],
               "bp:tcp": [(True, 700, 42)] * 4})
    assert [r["kind"] for r in kn.upgrade_kinds(p, NOW, "tcpmux")] == ["bp:tcp"]


def test_steadier_kinds_wants_a_steady_type_that_keeps_most_of_the_users_speed():
    p = _pair({"tcpmux": [(True, 500, 40)] * 4,
               "tcp": [(True, 450, 41)] * 4,                                      # steady, about the same: the answer
               "paqet": [(True, 40, 40)] * 4,                                     # steady but 12 times slower: steadiness is not bought with a slow tunnel
               "bp:wss": [(True, 450, 150)] * 4,                                  # steady, but the ping is 4 times the running one
               "chisel:fwd": [(True, 450, 41), (False, 0.0, 41), (True, 450, 41), (True, 450, 41)]})
    got = kn.steadier_kinds(p, NOW, "tcpmux")
    assert [r["kind"] for r in got] == ["tcp"] and kn.steadier_kinds(p, NOW, "tcpmux", allowed=lambda k: k != "tcp") == [] and kn.steadier_kinds(None, NOW, "tcpmux") == []


def test_a_tunnel_cut_three_times_in_a_day_is_offered_the_steady_type(monkeypatch):
    from nikatel.services import bandwidth, doctor

    p = _pair({"tcpmux": [(True, 500, 40)] * 4, "tcp": [(True, 450, 41)] * 4})
    seen = []
    snap = {"type": "backhaul", "transport": "tcpmux", "record": {}, "iran_id": 1, "foreign_id": 2, "name": "T1", "iran_name": "I", "foreign_name": "F"}
    from nikatel.services import actions, autopilot, license

    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: snap)
    monkeypatch.setattr(autopilot, "kind_of", lambda *a: "tcpmux")
    monkeypatch.setattr(kn, "pair", lambda i, f: p)
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    monkeypatch.setattr(doctor, "_state", lambda: {"suggestions": []})
    monkeypatch.setattr(doctor, "suggest", lambda *a, **k: seen.append((a, k)))
    monkeypatch.setattr(bandwidth.time, "time", lambda: NOW)
    bandwidth._UPGRADE_AT.clear()
    monkeypatch.setattr(bandwidth, "_cuts_lately", lambda tid, now, hours=24: 0)
    bandwidth._upgrade_check(7)
    assert seen == []                                                          # (no cut, nothing much better: nothing is said)
    bandwidth._UPGRADE_AT.clear()
    monkeypatch.setattr(bandwidth, "_cuts_lately", lambda tid, now, hours=24: 3)
    bandwidth._upgrade_check(7)
    (args, kwargs), = seen
    assert args[6] == "tcp" and kwargs["extra"]["steadier"] and "cut 3 times" in args[7] and "4 of 4 tests passed" in args[7]


def test_cuts_lately_counts_the_runs_of_failed_checks_of_the_last_day(monkeypatch):
    from nikatel.services import analyst, bandwidth

    rows, t = [], NOW - 20 * 3600

    for run in range(3):
        for k in range(analyst.OUTAGE_AFTER):
            rows.append((t + run * 3600 + k * 30, 0))

        rows.append((t + run * 3600 + analyst.OUTAGE_AFTER * 30 + 30, 1))

    class _Db:
        def execute(self, q):
            return rows

    class _Ctx:
        def __enter__(self):
            return _Db()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bandwidth, "session", lambda: _Ctx())
    assert bandwidth._cuts_lately(5, NOW) == 3
