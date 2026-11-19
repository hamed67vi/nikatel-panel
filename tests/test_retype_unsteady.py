"""The rule of 1.69.288 (a better type beside a tunnel whose users suffer while the tunnel itself is healthy) could not fire for the very tunnel it was made for (the owner, 2026-11-19, the Austria config): it wanted a type at least five
times FASTER, and the tunnel's type tested 778 Mb/s on its pair - a speed test of one big connection does not see a tunnel that stalls or resets NEW connections - while the Doctor's own memory called that type UNSTEADY (27 % of its tests).
Now the memory's word is enough when no faster type is known, and the change is judged by what the new tunnel carries, not by a speed gain over a number that hides the fault."""
import pytest
from test_retype_for_users import NOW, _world, calm  # noqa: F401  (the fixture and the world of 1.69.288's tests: the tunnel runs as `tcpmux`)

from nikatel.services import (
    autopilot,
    bandwidth,
    doctor,
    knowledge,
    license,
    swap,
    userq,
)


def _rows(*items):
    return lambda pair, now, include_poor=False: [{"kind": k, "score": 60, "steady": s, "down_mbps": 100.0} for k, s in items]


@pytest.fixture
def unsteady(calm, monkeypatch):  # noqa: F811
    """No faster type is known; the type the tunnel runs as (tcpmux) is UNSTEADY (27 %); the pair knows `paqet` (30 %, unsteady too), `wssmux` (not proven yet) and `tcpmux` itself."""
    monkeypatch.setattr(bandwidth, "_faster_row", lambda snap, cur, f, now: (None, []))
    monkeypatch.setattr(autopilot, "_steady_pct", lambda snap, kind: 27)
    monkeypatch.setattr(knowledge, "ranked", _rows(("paqet", 30), ("wssmux", None), ("tcpmux", 27)))
    monkeypatch.setattr(knowledge, "pair", lambda i, f: {})
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    return calm


def test_a_type_the_memory_calls_unsteady_is_changed_for_the_next_type_that_works_even_when_nothing_is_faster(unsteady):
    started = unsteady
    _world()
    assert userq.retype_for_users(NOW) == ["t1"]
    (label, fn, args, kw), = started
    assert label == "t1" and fn == "_run_retype" and args[1:3] == ("wssmux", "tcpmux")
    assert args[4] is True and args[5] is False and args[6] == 0.0 and kw == {"action": "autopilot", "source": "auto"}      # (auto, not a revert, and a speed gain over the old number is not asked: it hides the fault)
    assert "UNSTEADY" in args[3] and "27 %" in args[3] and "works here" in args[3]


def test_the_type_it_changes_to_is_not_itself_known_to_be_unsteady(unsteady):
    started = unsteady
    _world()
    userq.retype_for_users(NOW)
    assert started[0][2][1] == "wssmux"                                                                              # (paqet ranks first but its steadiness is 30 %: passed over)


def test_nothing_changes_when_the_memory_does_not_call_the_type_unsteady_or_has_no_other_type(unsteady, monkeypatch):
    started = unsteady
    _world()

    for pct in (None, 80):                                                                                           # (not known / steady)
        monkeypatch.setattr(autopilot, "_steady_pct", lambda snap, kind, p=pct: p)
        assert userq.retype_for_users(NOW) == [] and started == []

    monkeypatch.setattr(autopilot, "_steady_pct", lambda snap, kind: 27)
    monkeypatch.setattr(knowledge, "ranked", _rows(("paqet", 30), ("tcpmux", 27)))
    assert userq.retype_for_users(NOW) == [] and started == []                                                       # (no other type that is not unsteady too)
    monkeypatch.setattr(knowledge, "ranked", _rows(("tcpmux", 27)))
    assert userq.retype_for_users(NOW) == [] and started == []                                                       # (only the type it runs as)
    monkeypatch.setattr(knowledge, "ranked", _rows())
    assert userq.retype_for_users(NOW) == [] and started == []


def test_a_faster_type_still_comes_first_and_is_asked_for_its_speed_as_before(unsteady, monkeypatch):
    started = unsteady
    _world()
    monkeypatch.setattr(bandwidth, "_faster_row", lambda snap, cur, f, now: (None, [{"kind": "paqet2", "down_mbps": 120.0, "steady": None, "at": NOW - 3600}]))
    assert userq.retype_for_users(NOW) == ["t1"]
    args = started[0][2]
    assert args[1] == "paqet2" and args[5] is False and args[6] is None and "paqet2 gave 120 Mb/s" in args[3]        # (None: the swap asks the usual 1.5 times)


def test_the_retype_story_takes_the_speed_bar_from_the_caller_when_it_is_given(monkeypatch):
    seen = {}
    snap = {"id": 7, "name": "t1", "type": "backhaul", "transport": "tcpmux", "record": {"bw": {"down": 778.0}}, "iran_name": "ir1", "foreign_name": "fi1"}
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: snap)
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, **k: None)
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    monkeypatch.setattr(swap, "beside", lambda aid, s, kind, why, before, revert=False, **k: seen.update(before=before, kind=kind) or swap.DONE)
    doctor._run_retype(1, 7, "wssmux", "tcpmux", "why", True)
    assert seen == {"before": 778.0, "kind": "wssmux"}                                                               # (as before: the dropped tunnel's own number)
    doctor._run_retype(1, 7, "wssmux", "tcpmux", "why", True, False, 0.0)
    assert seen["before"] == 0.0
