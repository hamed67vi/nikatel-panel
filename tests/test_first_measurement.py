"""The FIRST measurement of a tunnel with no number (1.69.276; the owner, 2026-11-15, shown a tunnel row with a dash and "measuring": "تو ببین مشکل این چیه؟ این خط تیره برای چی افتاده"). The day's budget of the Doctor's own tests was
at 184 % (the surveys and the moves had filled it) and the level `first` stopped at 150 %: a tunnel rebuilt that afternoon kept its dash until the next Tehran day. Now a tunnel with no number still gets a few light first
tests over FIRST_SHARE - FIRST_TRIES of its own a day and FIRST_TOTAL of all - and a test that fails again and again still has an end."""
from nikatel.config import get_settings
from nikatel.services import bandwidth, testbudget
from nikatel.services import settings as st

NOW = 1_800_000_000
GB = 1_000_000_000


def _fill(share: float, now: int = NOW) -> None:
    st.put("testbudget", {"daily_gb": 10})
    testbudget.spend(share * 10 * GB, "probe", now)


def test_over_the_first_share_a_new_tunnel_gets_its_tries_and_the_others_theirs():
    _fill(1.84)                                                                                               # (the day of 2026-11-15: 184 %)
    assert not testbudget.allow("first", NOW)                                                                 # (the old level says no ...)

    for _ in range(testbudget.FIRST_TRIES):
        assert testbudget.first_allowed(794, NOW)                                                             # (... a new tunnel has its tries)
        testbudget.note_first(794, NOW)

    assert not testbudget.first_allowed(794, NOW)                                                             # (a test that fails again and again has an end)
    assert testbudget.first_allowed(795, NOW)                                                                 # (another new tunnel is not held by it)
    assert testbudget.state(NOW)["firsts"] == testbudget.FIRST_TRIES


def test_all_the_tries_of_the_day_have_an_end_and_the_next_tehran_day_starts_again():
    _fill(1.84)

    for i in range(testbudget.FIRST_TOTAL):
        assert testbudget.first_allowed(1000 + i, NOW)
        testbudget.note_first(1000 + i, NOW)

    assert not testbudget.first_allowed(2000, NOW)                                                            # (a loop that builds new tunnels again and again is held at FIRST_TOTAL tests, about 7 GB at the most)
    testbudget.spend(18.4 * GB, "probe", NOW + 86400)                                                         # (tomorrow the surveys fill the day again ...)
    assert testbudget.first_allowed(1000, NOW + 86400) and testbudget.state(NOW + 86400)["firsts"] == 0      # (... and the tries are counted again)


def test_under_the_first_share_nothing_is_counted():
    _fill(1.2)
    assert testbudget.first_allowed(794, NOW) and testbudget.state(NOW)["firsts"] == 0


def test_garbage_is_never_an_error():
    _fill(1.84)
    testbudget.note_first("x", NOW)                                                                           # (never raises: a speed test is not stopped by its own bookkeeping)
    testbudget.note_first(None, NOW)
    assert testbudget.state(NOW)["firsts"] == 0


def _run(monkeypatch, ids, trouble=()):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(bandwidth.jobs, "stopping", lambda: False)
    monkeypatch.setattr(bandwidth, "_zero_watch", lambda: None)
    monkeypatch.setattr(bandwidth, "_due", lambda now: list(ids))
    monkeypatch.setattr(bandwidth, "_no_sample", lambda i: i not in trouble)
    monkeypatch.setattr(bandwidth, "_in_trouble", lambda i: i in trouble)
    monkeypatch.setattr(bandwidth, "_server_busy_for", lambda i, now: False)
    monkeypatch.setattr(bandwidth, "_upgrade_check", lambda i: None)
    measured = []
    monkeypatch.setattr(bandwidth, "measure", lambda i: measured.append(i) or ({"down": 1, "ref": 1, "state": "ok"}, ""))
    bandwidth.run()
    return measured


def test_the_job_measures_a_new_tunnel_on_a_day_that_is_184_percent_used_three_times_at_the_most(monkeypatch):
    import time as _t

    from nikatel.services import autopilot

    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    st.put("testbudget", {"daily_gb": 10})
    testbudget.spend(18.4 * GB, "probe", int(_t.time()))
    assert _run(monkeypatch, [794]) == [794]                                                                  # (the row with the dash: it is measured at last)
    assert _run(monkeypatch, [794]) == [794]
    assert _run(monkeypatch, [794]) == [794]
    assert _run(monkeypatch, [794]) == []                                                                     # (a fourth try the same day waits for tomorrow)
    assert _run(monkeypatch, [795]) == [795]                                                                  # (another new tunnel still goes)
    assert _run(monkeypatch, [6], trouble=(6,)) == []                                                         # (a tunnel that dropped before waits: the whole budget is used)
