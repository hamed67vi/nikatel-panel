"""After a change of type the users are watched (services/retypewatch.py, 1.69.220; the owner, 2026-11-05: "check that the customers' use of the tunnel did not fall a lot afterwards: if it did, move the tunnel to a
better type or put the one before back")."""
import inspect
import time

from sqlalchemy import select
from test_failover import _servers_and_tunnels

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, TunnelCheck
from nikatel.services import autopilot, doctor, knowledge, retypewatch, swap
from nikatel.services import settings as st


# ------------------------------------------------------------------ the pure parts
def test_the_use_after_is_judged_against_the_use_before_and_the_fleets_own_movement():
    assert retypewatch.relative_use(10.0, 5.0, 100.0, 100.0) == 0.5                       # half of it left, the fleet did not move
    assert retypewatch.relative_use(10.0, 5.0, 100.0, 50.0) == 1.0                        # half of it left, but the fleet halved too (the night came): as expected
    assert retypewatch.relative_use(2.0, 0.1, 100.0, 100.0) is None                       # it carried under 3 Mb/s before: no fall can be shown
    assert retypewatch.relative_use(10.0, None, 100.0, 100.0) is None and retypewatch.relative_use(10.0, 5.0, 0.0, 10.0) is None


def test_a_fall_below_the_line_is_a_drop_and_the_rest_stays():
    assert retypewatch.verdict(0.54)[0] == "drop" and "left it" in retypewatch.verdict(0.54)[1]
    assert retypewatch.verdict(0.56)[0] == "ok" and retypewatch.verdict(1.4)[0] == "ok"
    assert retypewatch.verdict(None)[0] == "unknown"


def test_the_looks_come_at_thirty_minutes_two_hours_and_six_hours():
    now = 1_000_000
    e = {"at": now, "looks": 0}
    assert retypewatch.due_look(e, now + 600) is None and retypewatch.due_look(e, now + 31 * 60) == 0
    assert retypewatch.due_look({**e, "looks": 1}, now + 31 * 60) is None and retypewatch.due_look({**e, "looks": 1}, now + 2 * 3600 + 1) == 1
    assert retypewatch.due_look({**e, "looks": 3}, now + 9 * 3600) is None and retypewatch.due_look({**e, "closed": "kept"}, now + 9 * 3600) is None


# ------------------------------------------------------------------ the numbers and the job
def _checks(ids_mbps, now, minutes, start=0):
    with session() as db:
        for tid, mbps in ids_mbps.items():
            db.add_all([TunnelCheck(tunnel_id=tid, ts=now - (start + i) * 60, ok=True, rx_mbps=mbps * 0.8, tx_mbps=mbps * 0.2) for i in range(minutes)])


def _world(monkeypatch, mode="move"):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(retypewatch.jobs, "stopping", lambda: False)
    st.put("repair", {"mode": mode})
    st.put("retypewatch", {"watch": {}})
    return _servers_and_tunnels()


def test_the_use_before_the_change_is_taken_from_the_checks_of_the_tunnel_and_of_the_rest(monkeypatch):
    ids = _world(monkeypatch)
    now = int(time.time())
    _checks({ids["t1"]: 30.0, ids["t2"]: 10.0, ids["t3"]: 20.0}, now, 190)
    pre = retypewatch.snapshot(ids["t2"], now)
    assert round(pre["before"]) == 10 and round(pre["fleet_before"]) == 50 and pre["at"] == now                          # (the fleet: the other two tunnels together)
    assert retypewatch.snapshot(99999, now)["before"] is None                                                           # (no checks: nothing to say)


def test_a_change_leaves_a_note_and_an_event(monkeypatch):
    _world(monkeypatch)
    retypewatch.begin("t2", "tcpmux", "bp:wss", {"before": 10.0, "fleet_before": 50.0})
    e = st.get("retypewatch")["watch"]["t2"]
    assert e["was"] == "tcpmux" and e["to"] == "bp:wss" and e["before"] == 10.0 and e["looks"] == 0
    with session() as db:
        assert any("changed from tcpmux to bp:wss" in x.text for x in db.scalars(select(Event)))


def _watching(monkeypatch, after_t2, fleet_each=50.0, before=10.0, looks=0, age=31 * 60):
    ids = _world(monkeypatch)
    now = int(time.time())
    st.put("retypewatch", {"watch": {"t2": {"was": "tcpmux", "to": "bp:wss", "at": now - age, "before": before, "fleet_before": 100.0, "looks": looks}}})
    _checks({ids["t2"]: after_t2, ids["t1"]: fleet_each, ids["t3"]: fleet_each}, now, 25)
    started, recorded = [], []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn.__name__, a)) or 1)
    monkeypatch.setattr(knowledge, "record", lambda i, f, r, m, now=None: recorded.append((r["kind"], r["ok"], m)))
    return ids, started, recorded


def test_the_users_who_left_put_the_old_type_back_and_the_new_one_is_entered_as_failed(monkeypatch):
    ids, started, recorded = _watching(monkeypatch, after_t2=2.0)                                                         # 10 Mb/s before, 2 after, the fleet did not move
    swap._TRIED["t2"] = int(time.time())
    retypewatch.run()
    assert [(s[0], s[1]) for s in started] == [("t2", "_run_retype")] and started[0][2][:3] == (ids["t2"], "tcpmux", "bp:wss") and started[0][2][4:] == (True, True)     # (kind back, expected now, why, auto, revert)
    assert recorded == [("bp:wss", False, "full")] and "t2" not in swap._TRIED                                            # (the gap of a change forward does not hold a change back)
    assert st.get("retypewatch")["watch"]["t2"]["closed"] == "reverted"
    with session() as db:
        assert any("puts tcpmux back" in x.text for x in db.scalars(select(Event)))

    started.clear()
    retypewatch.run()
    assert started == []                                                                                                  # (a change that was put back is not watched again)


def test_users_who_stayed_are_left_alone_and_the_third_look_closes_the_note(monkeypatch):
    _ids, started, _recorded = _watching(monkeypatch, after_t2=9.0)
    retypewatch.run()
    assert started == [] and st.get("retypewatch")["watch"]["t2"]["looks"] == 1 and "closed" not in st.get("retypewatch")["watch"]["t2"]
    st.put("retypewatch", {"watch": {"t2": {**st.get("retypewatch")["watch"]["t2"], "looks": 2, "at": int(time.time()) - 7 * 3600}}})
    retypewatch.run()
    assert started == [] and st.get("retypewatch")["watch"]["t2"]["closed"] == "kept"
    with session() as db:
        assert any("The change stays" in x.text for x in db.scalars(select(Event)))


def test_a_tunnel_that_carried_little_before_cannot_show_a_fall_and_nothing_runs_in_the_mode_off(monkeypatch):
    _ids, started, _recorded = _watching(monkeypatch, after_t2=0.1, before=1.0)
    retypewatch.run()
    assert started == []                                                                                                  # (unknown is not a drop)
    st.put("repair", {"mode": "off"})
    st.put("retypewatch", {"watch": {"t2": {"was": "tcpmux", "to": "bp:wss", "at": int(time.time()) - 3600, "before": 10.0, "fleet_before": 100.0, "looks": 0}}})
    retypewatch.run()
    assert started == [] and st.get("retypewatch")["watch"]["t2"]["looks"] == 0


def test_the_change_back_skips_the_gain_a_change_forward_asks_and_the_gap_of_three_hours():
    assert "revert" in inspect.signature(swap.beside).parameters and "revert" in inspect.signature(swap._work).parameters and "revert" in inspect.signature(doctor._run_retype).parameters
    src = inspect.getsource(swap)
    assert "if not revert and now - _TRIED.get(name, 0) < RETRY_GAP:" in src and 'bad = "" if revert else confirm_ok(down, before)' in src
    assert "retypewatch.begin(name, cur, kind, pre)" in src and "retypewatch.begin(snap[\"name\"], cur, kind, pre)" in inspect.getsource(doctor)


def test_the_job_is_registered_and_the_module_is_in_the_architecture():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert 'jobs.register("retypewatch", retypewatch.EVERY, retypewatch.run)' in (root / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert "retypewatch.py" in (root / "ARCHITECTURE.md").read_text(encoding="utf-8")
