"""The volume of the Doctor's own tests (1.69.223; the owner, 2026-11-05: "is there a way to save volume?" - the speed tests are traffic too). A daily budget: the routine measurements and surveys go on while the day has
used under 80 % of it and get slower from half of it, the urgent ones (a tunnel that dropped, a tunnel nobody has measured yet, a pair nobody has surveyed) go on until it is used, a cut is never held back."""
from nikatel.config import get_settings
from nikatel.services import autopilot, bandwidth, build_other, knowledge, testbudget
from nikatel.services import settings as st

NOW = 1_800_000_000
GB = 1_000_000_000


def test_what_the_tests_move_adds_up_per_tehran_day_and_a_new_day_starts_again():
    testbudget.spend(2 * GB, "probe", NOW)
    testbudget.spend(1 * GB, "survey", NOW + 60)
    assert testbudget.used(NOW) == 3 * GB
    s = testbudget.state(NOW)
    assert s["used_gb"] == 3.0 and s["by"] == {"probe": 2.0, "survey": 1.0} and s["daily_gb"] == 30.0 and s["share"] == 0.1
    assert testbudget.used(NOW + 86400) == 0                                                                  # (tomorrow nothing is used yet)
    testbudget.spend(5 * GB, "probe", NOW + 86400)
    s = testbudget.state(NOW + 86400)
    assert s["used_gb"] == 5.0 and s["by"] == {"probe": 5.0} and s["days"][-1]["gb"] == 3.0                    # (yesterday is kept for the page)


def test_garbage_is_not_counted_and_never_raises():
    for bad in (None, "x", -5, 0, [], {}):
        testbudget.spend(bad, "probe", NOW)

    assert testbudget.used(NOW) == 0


def test_the_levels_routine_stops_at_eighty_percent_urgent_at_the_whole_budget_and_a_cut_never():
    st.put("testbudget", {"daily_gb": 10})
    assert testbudget.allow("routine", NOW) and testbudget.stretch(NOW) == 1
    testbudget.spend(5.5 * GB, "probe", NOW)
    assert testbudget.allow("routine", NOW) and testbudget.stretch(NOW) == 2                                  # (over half: the 90 minutes are 3 hours)
    testbudget.spend(3 * GB, "probe", NOW)
    assert not testbudget.allow("routine", NOW) and testbudget.allow("urgent", NOW) and testbudget.stretch(NOW) == 4
    testbudget.spend(2 * GB, "probe", NOW)
    assert not testbudget.allow("urgent", NOW) and testbudget.allow("cut", NOW)


def test_a_speed_test_that_says_what_it_moved_is_counted_and_its_wire_cost_is_worked_out():
    out = 'noise\nNKPROBE {"down_mbps": 100.0, "w_moved": 100000000, "w_nic": 111150000, "w_dur": 6.0, "w_pre": 1000000, "w_post": 1050000}\n'
    d = build_other.parse_probe(out)
    assert d["wire_ratio"] == 1.05 and testbudget.used() == 100_000_000
    d = build_other.parse_probe('NKPROBE {"down_mbps": 100.0}')
    assert "wire_ratio" not in d and testbudget.used() == 100_000_000                                         # (an older script, or a test that moved nothing: nothing is added)


def _routine(monkeypatch, ids, trouble=()):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(bandwidth.jobs, "stopping", lambda: False)
    monkeypatch.setattr(bandwidth, "_zero_watch", lambda: None)
    monkeypatch.setattr(bandwidth, "_due", lambda now: list(ids))
    monkeypatch.setattr(bandwidth, "_no_sample", lambda i: False)
    monkeypatch.setattr(bandwidth, "_in_trouble", lambda i: i in trouble)
    monkeypatch.setattr(bandwidth, "_server_busy_for", lambda i, now: False)
    monkeypatch.setattr(bandwidth, "_upgrade_check", lambda i: None)
    measured = []
    monkeypatch.setattr(bandwidth, "measure", lambda i: measured.append(i) or ({"down": 1, "ref": 1, "state": "ok"}, ""))
    return measured


def test_the_routine_measurements_wait_when_the_day_is_used_but_a_tunnel_in_trouble_does_not(monkeypatch):
    st.put("testbudget", {"daily_gb": 10})
    measured = _routine(monkeypatch, [5], trouble=(6,))
    bandwidth.run()
    assert measured == [5]                                                                                    # (a quiet day: the longest waiting tunnel)
    import time as _t

    testbudget.spend(8.5 * GB, "probe", int(_t.time()))
    measured = _routine(monkeypatch, [5])
    bandwidth.run()
    assert measured == []                                                                                     # (85 % used: the routine measurement waits)
    measured = _routine(monkeypatch, [5, 6], trouble=(6,))
    bandwidth.run()
    assert measured == [6]                                                                                    # (a tunnel that dropped is still measured)
    testbudget.spend(2 * GB, "probe", int(_t.time()))
    measured = _routine(monkeypatch, [6], trouble=(6,))
    bandwidth.run()
    assert measured == []                                                                                     # (the whole budget is used: even that waits)


def _survey_world(monkeypatch, cold=(), verify=((1, 2, ["a"]),), window=True):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(knowledge.jobs, "stopping", lambda: False)
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(autopilot, "repairing_names", list)
    monkeypatch.setattr(knowledge, "in_window", lambda *a, **k: window)
    st.put("knowledge", {"on": True, "from_hour": 0, "to_hour": 0})
    started = []
    monkeypatch.setattr(knowledge, "pending_pairs", lambda now, cold_only=False: list(cold))
    monkeypatch.setattr(knowledge, "verify_pairs", lambda now: list(verify))
    monkeypatch.setattr(knowledge, "not_now", lambda i, f, now, quiet_mbps=knowledge.QUIET_MBPS: "")
    monkeypatch.setattr(knowledge, "survey_start", lambda i, f, source="survey-manual", kinds=None: started.append(source) or "job")
    monkeypatch.setattr(knowledge, "_running", lambda: None)
    return started


def test_the_surveys_wait_when_the_budget_is_used_a_pair_nobody_knows_goes_first(monkeypatch):
    import time as _t

    st.put("testbudget", {"daily_gb": 10})
    started = _survey_world(monkeypatch)
    knowledge.run()
    assert started == ["survey-verify"]                                                                       # (a quiet day: the running types are tested again)
    testbudget.spend(8.5 * GB, "survey", int(_t.time()))
    started = _survey_world(monkeypatch)
    knowledge.run()
    assert started == []                                                                                      # (85 %: the verify survey is routine and waits)
    started = _survey_world(monkeypatch, cold=[(5, 6, ["tcpmux"])])
    knowledge.run()
    assert started == []                                                                                      # (and so does the night survey)
    started = _survey_world(monkeypatch, cold=[(5, 6, ["tcpmux"])], window=False)
    knowledge.run()
    assert started == ["survey-cold"]                                                                         # (the first look at a pair nobody has surveyed still goes: urgent)
    testbudget.spend(2 * GB, "survey", int(_t.time()))
    started = _survey_world(monkeypatch, cold=[(5, 6, ["tcpmux"])], window=False)
    knowledge.run()
    assert started == []                                                                                      # (the budget is used: nothing)
    assert knowledge.state()["volume"]["used_gb"] >= 10.0                                                     # (the page says how much today's tests moved)


def test_the_level_first_goes_on_after_the_whole_budget_but_not_without_end():
    st.put("testbudget", {"daily_gb": 10})
    testbudget.spend(12.5 * GB, "probe", NOW)
    assert not testbudget.allow("urgent", NOW) and testbudget.allow("first", NOW) and testbudget.allow("cut", NOW)      # (125 %: a tunnel nobody has measured is still measured)
    testbudget.spend(3 * GB, "probe", NOW)
    assert not testbudget.allow("first", NOW) and testbudget.allow("cut", NOW)                                       # (155 %: even that waits, a test that fails again and again cannot go on for ever)


def test_a_tunnel_with_no_number_is_measured_when_the_day_is_used_one_that_dropped_waits(monkeypatch):
    import time as _t

    st.put("testbudget", {"daily_gb": 10})
    testbudget.spend(12.5 * GB, "probe", int(_t.time()))
    measured = _routine(monkeypatch, [7])
    monkeypatch.setattr(bandwidth, "_no_sample", lambda i: True)
    bandwidth.run()
    assert measured == [7]                                                                                    # (a new tunnel: its dash is not left for the next Tehran day)
    measured = _routine(monkeypatch, [6], trouble=(6,))
    bandwidth.run()
    assert measured == []                                                                                     # (a tunnel that was measured before and dropped: the whole budget is used, it waits)
    testbudget.spend(3 * GB, "probe", int(_t.time()))
    measured = _routine(monkeypatch, [7])
    monkeypatch.setattr(bandwidth, "_no_sample", lambda i: True)
    bandwidth.run()
    assert measured == []                                                                                     # (150 % used: nothing goes on)
