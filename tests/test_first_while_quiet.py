"""A tunnel with NO number is measured while a failover or a move runs (1.69.306; the owner, 2026-11-25, a screenshot of the Tunnels list with dashes: «why are they dashes, fix it»). The speed-test job returned at once in the quiet time
of a failover (the story, plus 300 s after it), and a move waits up to 30 minutes for the old tunnel's users: the tunnel it had just built showed a dash for the whole move. Now ONE tunnel with no number is measured per turn in the quiet
time - and nothing more is done with it; in a mass disruption (storm) nothing is measured at all."""
import time

import pytest

from nikatel.config import get_settings
from nikatel.services import autopilot, bandwidth, testbudget
from nikatel.services import settings as st

GB = 1_000_000_000


@pytest.fixture(autouse=True)
def _calm():
    autopilot._MASS.update(running=0, until=0.0)
    st.put("testbudget", {"daily_gb": 10})
    yield
    autopilot._MASS.update(running=0, until=0.0)


def _run(monkeypatch, ids, nosample=(), storm=False):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(bandwidth.jobs, "stopping", lambda: False)
    watched, looked, measured = [], [], []
    monkeypatch.setattr(bandwidth, "_zero_watch", lambda: watched.append(1))
    monkeypatch.setattr(bandwidth, "_due", lambda now: list(ids))
    monkeypatch.setattr(bandwidth, "_no_sample", lambda i: i in nosample)
    monkeypatch.setattr(bandwidth, "_in_trouble", lambda i: False)
    monkeypatch.setattr(bandwidth, "_server_busy_for", lambda i, now: False)
    monkeypatch.setattr(autopilot, "storm_now", lambda: storm)
    monkeypatch.setattr(bandwidth, "_upgrade_check", lambda i: looked.append(("upgrade", i)))
    monkeypatch.setattr(bandwidth, "_investigate_later", lambda i, relook=False: looked.append(("investigate", i)))
    monkeypatch.setattr(bandwidth, "_retype_later", lambda i: looked.append(("retype", i)))
    monkeypatch.setattr(bandwidth, "measure", lambda i: measured.append(i) or ({"down": 300, "ref": None, "state": "ok"}, ""))
    bandwidth.run()
    return measured, looked, watched


def test_a_tunnel_with_no_number_is_measured_while_a_move_runs_and_nothing_more_is_done_with_it(monkeypatch):
    autopilot.mass_begin()                                                                                    # (a move or a failover story runs)
    assert autopilot.quiet_now()
    measured, looked, watched = _run(monkeypatch, [5, 794], nosample={794})
    assert measured == [794]                                                                                  # (the new tunnel; the old one with a number waits)
    assert looked == [] and watched == []                                                                     # (no upgrade look, no investigation, no watch of the idle tunnels)


def test_in_the_quiet_time_one_new_tunnel_is_measured_per_turn(monkeypatch):
    autopilot.mass_begin()
    assert _run(monkeypatch, [794, 795, 796], nosample={794, 795, 796})[0] == [794]


def test_in_the_quiet_time_a_tunnel_that_has_a_number_is_not_measured(monkeypatch):
    autopilot.mass_begin()
    assert _run(monkeypatch, [5, 6])[0] == []


def test_the_minutes_after_the_story_are_quiet_too(monkeypatch):
    autopilot.mass_begin()
    autopilot.mass_end()                                                                                      # (the story is over: MASS_SETTLE seconds of settling follow)
    assert autopilot._MASS["running"] == 0 and autopilot.quiet_now()
    measured, looked, watched = _run(monkeypatch, [5, 794], nosample={794})
    assert measured == [794] and looked == [] and watched == []


def test_in_a_mass_disruption_nothing_is_measured(monkeypatch):
    autopilot.mass_begin()
    measured, looked, watched = _run(monkeypatch, [794], nosample={794}, storm=True)
    assert measured == [] and looked == [] and watched == []


def test_when_all_is_calm_the_job_is_as_it_was(monkeypatch):
    assert not autopilot.quiet_now()
    measured, looked, watched = _run(monkeypatch, [794], nosample={794})
    assert measured == [794]
    assert looked == [("upgrade", 794)]                                                                       # (the upgrade look after a good test)
    assert watched == [1]                                                                                     # (the watch of the tunnels that nobody uses)


def test_a_tunnel_with_a_number_is_measured_in_calm_as_before(monkeypatch):
    assert _run(monkeypatch, [5, 6])[0] == [5]


def test_the_budget_still_holds_in_the_quiet_time(monkeypatch):
    autopilot.mass_begin()
    testbudget.spend(18.4 * GB, "probe", int(time.time()))                                                    # (the day is at 184 %: the tries of a new tunnel are counted)

    for _ in range(testbudget.FIRST_TRIES):
        assert _run(monkeypatch, [794], nosample={794})[0] == [794]

    assert _run(monkeypatch, [794], nosample={794})[0] == []                                                  # (a fourth try the same day waits for tomorrow)
    assert _run(monkeypatch, [795], nosample={795})[0] == [795]                                               # (another new tunnel still goes)
    assert _run(monkeypatch, [794, 796], nosample={794, 796})[0] == [796]                                     # (a tunnel that used its tries does not hold the one behind it)
