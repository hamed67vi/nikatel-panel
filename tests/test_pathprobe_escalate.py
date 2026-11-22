"""The second rung of the layered look (1.69.291; the owner, 2026-11-19, «بریم فاز بعدی»): a tunnel the look restarted and that STILL resets new connections two looks later gets another type built beside it without a cut, and the tunnel
window shows what each layer answered. The restart of 1.69.290 cured the Austria tunnel once; a tunnel that stays broken after it must not wait three hours for the grade of its users."""
import json
import shutil
import subprocess
import time

import pytest
from test_pathprobe import R, _layers_of, _world, calm  # noqa: F401  (the fixture and the world of 1.69.290's tests)

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Tunnel
from nikatel.services import (
    actions,
    autopilot,
    doctor,
    knowledge,
    license,
    pathprobe,
    standby,
    swap,
    testbudget,
    userq,
)
from nikatel.services import settings as st

NOW = 1_795_000_000


# ------------------------------------------------------------------ the pure rule
def test_another_type_is_wanted_only_for_a_tunnel_that_was_restarted_and_is_still_broken_two_looks_later():
    tun = {"code": "tunnel"}
    assert pathprobe.escalate_wanted(NOW - 3600, tun, "tunnel", NOW) == ""
    assert "does not reset" in pathprobe.escalate_wanted(NOW - 3600, {"code": "ok"}, "tunnel", NOW)                 # (the settled state says it is fine)
    assert "does not reset" in pathprobe.escalate_wanted(NOW - 3600, tun, "ok", NOW)                                 # (this look says it is fine: the restart cured it)
    assert "does not reset" in pathprobe.escalate_wanted(NOW - 3600, tun, "node", NOW)                               # (another layer is to blame: not a matter of the tunnel's type)
    assert "has not restarted" in pathprobe.escalate_wanted(0, tun, "tunnel", NOW)
    assert "next look" in pathprobe.escalate_wanted(NOW - 1800, tun, "tunnel", NOW)                                   # (one look after the restart is not enough: it may be curing)
    assert pathprobe.escalate_wanted(NOW - pathprobe.ESCALATE_AFTER, tun, "tunnel", NOW) == ""
    assert "another type was tried" in pathprobe.escalate_wanted(NOW - 3600, tun, "tunnel", NOW, tried_at=NOW - 3 * 3600)
    assert pathprobe.escalate_wanted(NOW - 3600, tun, "tunnel", NOW, tried_at=NOW - pathprobe.ESCALATE_GAP - 1) == ""


def test_two_looks_are_enough_time_for_the_restart_to_have_cured_it():
    assert pathprobe.ESCALATE_AFTER > pathprobe.EVERY and pathprobe.ESCALATE_AFTER <= 2 * pathprobe.EVERY       # (the second look after the restart, not the first, and not a third)


def test_the_first_look_after_the_restart_is_enough_when_a_steadier_standby_is_ready():
    tun = {"code": "tunnel"}
    assert 0 < pathprobe.ESCALATE_FAST <= pathprobe.EVERY < pathprobe.ESCALATE_AFTER                                    # (the next look after the restart, not the second)
    assert pathprobe.escalate_wanted(NOW - pathprobe.ESCALATE_FAST, tun, "tunnel", NOW, fast=True) == ""
    assert "next look" in pathprobe.escalate_wanted(NOW - pathprobe.ESCALATE_FAST + 1, tun, "tunnel", NOW, fast=True)
    assert "next look" in pathprobe.escalate_wanted(NOW - pathprobe.ESCALATE_FAST, tun, "tunnel", NOW)                   # (no ready standby: the old wait stands)
    assert "does not reset" in pathprobe.escalate_wanted(NOW - 1800, tun, "ok", NOW, fast=True)                           # (a restart that cured it is still the end of it)
    assert "another type was tried" in pathprobe.escalate_wanted(NOW - 1800, tun, "tunnel", NOW, tried_at=NOW - 3 * 3600, fast=True)


# ------------------------------------------------------------------ the type that is taken
def _rows(*items):
    return lambda pair, now, include_poor=False: [{"kind": k, "score": 60, "steady": s, "down_mbps": 100.0} for k, s in items]


def test_the_steadier_kind_can_be_forced_when_the_memory_has_no_number_against_the_type(monkeypatch):
    monkeypatch.setattr(knowledge, "pair", lambda i, f: {})
    monkeypatch.setattr(knowledge, "ranked", _rows(("paqet", 30), ("wssmux", None), ("tcpmux", 80)))
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    snap = {"iran_id": 1, "foreign_id": 2}

    for pct in (None, 80):
        monkeypatch.setattr(autopilot, "_steady_pct", lambda s, kind, p=pct: p)
        assert userq._steadier_kind(snap, "tcpmux", NOW) == (None, pct)                                                  # (as before: only when the memory calls the type unsteady)
        cand, got = userq._steadier_kind(snap, "tcpmux", NOW, force=True)
        assert cand["kind"] == "wssmux" and got == pct                                                                   # (forced: the best OTHER type that is not itself unsteady)

    monkeypatch.setattr(knowledge, "ranked", _rows(("paqet", 30), ("tcpmux", 80)))
    assert userq._steadier_kind(snap, "tcpmux", NOW, force=True)[0] is None                                              # (no other type that works)


# ------------------------------------------------------------------ the job
@pytest.fixture
def escal(calm, monkeypatch):  # noqa: F811
    """The look finds the tunnel broken; the Doctor is in fix mode; the pair knows `wssmux` (not proven yet) and `paqet` (30 %, unsteady); the tunnel runs as tcpmux; `started` collects the stories."""
    layers, started, _spent, _calls = calm
    monkeypatch.setattr(doctor, "mode", lambda: "fix")
    monkeypatch.setattr(testbudget, "allow", lambda level="routine", now=None: True)
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: {"id": tid, "name": "t0", "type": "backhaul", "transport": "tcpmux", "record": {}, "iran_id": 1, "foreign_id": 2})
    monkeypatch.setattr(autopilot, "_steady_pct", lambda snap, kind: None)
    monkeypatch.setattr(knowledge, "ranked", _rows(("paqet", 30), ("wssmux", None), ("tcpmux", 80)))
    monkeypatch.setattr(knowledge, "pair", lambda i, f: {})
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    monkeypatch.setattr(userq, "_UX_TRIED", {})
    monkeypatch.setattr(standby, "status", lambda name: {"on": False, "state": "off", "why": "", "kind": "", "since": 0, "ports": "", "up": False})
    layers["tunnel"] = R(5, err="ConnectionResetError")
    return layers, started


def _restarted_long_ago(*names, ago=3700):
    st.put("pathprobe", {"restarted": {n: int(time.time()) - ago for n in names}, "escalated": {}})


def test_a_tunnel_that_is_still_broken_after_the_restart_gets_another_type_beside_it_once_in_a_day(escal):
    _layers, started = escal
    (tid, _fid), = _world(1)["t"]
    pathprobe.run()
    assert pathprobe.run() == ["t0"] and started[0][1] == "restart_story"                                           # (rung one: two looks in a row, then the restart)
    assert pathprobe.run() == [] and len(started) == 1                                                              # (one look after it: the restart may be curing)
    _restarted_long_ago("t0")
    assert pathprobe.run() == ["t0"] and len(started) == 2                                                          # (two looks after it, still broken: rung two)
    label, fn, args, kw = started[1]
    assert label == "t0" and fn == "_run_retype" and args[0] == tid and args[1:3] == ("wssmux", "tcpmux")           # (paqet ranks first but its steadiness is 30 %: passed over)
    assert args[4] is True and args[5] is False and args[6] == 0.0 and kw == {"action": "autopilot", "source": "auto"}   # (auto, not a revert, no speed gain asked)
    assert "ConnectionResetError" in args[3] and "still does" in args[3] and "without a cut" in args[3]
    assert "t0" in st.get("pathprobe")["escalated"] and userq._UX_TRIED["t0"] > 0                                    # (the users' rule of 1.69.288 does not start a second change for the same tunnel)
    assert pathprobe.run() == [] and len(started) == 2                                                              # (once in a day)


def test_a_restart_that_cured_it_is_the_end_of_it(escal):
    layers, started = escal
    (tid, _fid), = _world(1)["t"]
    pathprobe.run()
    pathprobe.run()
    layers["tunnel"] = R(8)
    _restarted_long_ago("t0")
    pathprobe.run()
    assert pathprobe.run() == [] and len(started) == 1 and _layers_of(tid)["state"]["code"] == "ok"                 # (two good looks: the verdict is ok, nothing is built)


def test_every_rail_holds_back_the_change_of_type(escal, monkeypatch):
    _layers, started = escal
    _world(1, protected=True)
    pathprobe.run()
    pathprobe.run()
    _restarted_long_ago("t0")
    assert pathprobe.run() == [] and started == []                                                                  # (a protected tunnel is never touched)

    with session() as db:
        t = db.query(Tunnel).one()
        t.record = {k: v for k, v in t.record.items() if k != "protected"}

    for mode in ("off", "observe"):
        monkeypatch.setattr(actions, "doctor_mode", lambda m=mode: m)
        assert pathprobe.run() == [] and started == []

    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "quiet_now", lambda: True)
    assert pathprobe.run() == [] and started == []
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert pathprobe.run() == [] and started == []
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(swap, "at_once", lambda: swap.MAX_AT_ONCE)
    assert pathprobe.run() == [] and started == []
    monkeypatch.setattr(swap, "at_once", lambda: 0)
    monkeypatch.setattr(testbudget, "allow", lambda level="routine", now=None: False)
    assert pathprobe.run() == [] and started == []                                                                  # (the day's volume of tests is spent)
    monkeypatch.setattr(testbudget, "allow", lambda level="routine", now=None: True)
    monkeypatch.setattr(doctor, "may_retype", lambda name, retyped, now, gap=0: "t0 was changed by the Doctor 2 hours ago")
    assert pathprobe.run() == [] and started == []                                                                  # (the swap's own once-in-24-hours rail)
    monkeypatch.setattr(doctor, "may_retype", lambda name, retyped, now, gap=0: "")
    assert pathprobe.run() == ["t0"] and started[-1][1] == "_run_retype"


def test_no_other_type_that_works_is_no_change(escal, monkeypatch):
    _layers, started = escal
    _world(1)
    pathprobe.run()
    pathprobe.run()
    _restarted_long_ago("t0")
    monkeypatch.setattr(knowledge, "ranked", _rows(("paqet", 30), ("tcpmux", 80)))
    assert pathprobe.run() == [] and len(started) == 1 and "t0" not in st.get("pathprobe")["escalated"]            # (nothing is marked: the look is made again next time, when the memory may know a type)


def test_when_a_third_of_the_fleet_says_tunnel_at_once_it_is_not_the_type_of_one_tunnel(escal):
    _layers, started = escal
    _world(3)
    pathprobe.run()
    pathprobe.run()
    n = len(started)
    _restarted_long_ago("t0", "t1", "t2")
    assert pathprobe.run() == [] and len(started) == n                                                              # (three of three: the Iran side or the panel's host, not a type)
    assert not any(s[1] == "_run_retype" for s in started)


def test_a_change_that_cannot_start_leaves_no_mark_and_is_made_again_at_the_next_look(escal, monkeypatch):
    _layers, started = escal
    _world(1)
    pathprobe.run()
    pathprobe.run()
    _restarted_long_ago("t0")

    def refuse(label, fn, *a, **k):
        raise ValueError("a swap of this tunnel is already going on")

    monkeypatch.setattr(autopilot, "_start", refuse)
    assert pathprobe.run() == [] and "t0" not in st.get("pathprobe")["escalated"]
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn.__name__, a, k)) or 1)
    assert pathprobe.run() == ["t0"]


def _ready(monkeypatch, kind, up=True, state="ready"):
    monkeypatch.setattr(standby, "status", lambda name: {"on": True, "state": state, "why": "", "kind": kind, "since": 1, "ports": "", "up": up})


def test_a_ready_steadier_standby_is_taken_at_the_first_look_after_a_restart_that_did_not_cure(escal, monkeypatch):
    _layers, started = escal
    (tid, _fid), = _world(1)["t"]
    pathprobe.run()
    assert pathprobe.run() == ["t0"] and started[0][1] == "restart_story"
    _restarted_long_ago("t0", ago=1700)                                                                                 # (the very next look: past ESCALATE_FAST, short of ESCALATE_AFTER)
    assert pathprobe.run() == [] and len(started) == 1                                                                  # (no standby: the old wait stands)

    for kind, up, state in (("tcpmux", True, "ready"), ("paqet", True, "ready"), ("wssmux", False, "mending"), ("wssmux", True, "building")):
        _ready(monkeypatch, kind, up, state)
        assert pathprobe.run() == [] and len(started) == 1, (kind, up, state)                                           # (the tunnel's own type; a type the memory calls unsteady; a standby that is not ready)

    _ready(monkeypatch, "wssmux")
    assert pathprobe.run() == ["t0"] and len(started) == 2
    label, fn, args, kw = started[1]
    assert label == "t0" and fn == "_run_retype" and args[0] == tid and args[1:3] == ("wssmux", "tcpmux")
    assert args[4] is True and args[5] is False and args[6] == 0.0 and kw == {"action": "autopilot", "source": "auto"}
    assert "warm standby" in args[3] and "ConnectionResetError" in args[3] and "without a cut" in args[3]
    assert "t0" in st.get("pathprobe")["escalated"] and pathprobe.run() == [] and len(started) == 2                      # (once in a day, as before)


def test_a_ready_standby_is_taken_before_the_ranking_even_after_the_long_wait(escal, monkeypatch):
    _layers, started = escal
    _world(1)
    pathprobe.run()
    pathprobe.run()
    _restarted_long_ago("t0")
    monkeypatch.setattr(knowledge, "ranked", _rows(("paqet", None), ("wssmux", None), ("tcpmux", 80)))
    _ready(monkeypatch, "wssmux")
    assert pathprobe.run() == ["t0"] and started[1][2][1] == "wssmux" and "warm standby" not in started[1][2][3]       # (past the long wait the sentence about the sooner start is not said)


def test_a_standby_that_cannot_be_read_is_no_reason_to_stop_the_look(escal, monkeypatch):
    _layers, started = escal
    _world(1)
    pathprobe.run()
    pathprobe.run()
    _restarted_long_ago("t0")

    def broken(name):
        raise RuntimeError("the standby table is locked")

    monkeypatch.setattr(standby, "status", broken)
    assert pathprobe.run() == ["t0"] and started[1][2][1] == "wssmux"                                                   # (the ranking of the memory decides, as before 1.69.298)


# ------------------------------------------------------------------ the tunnel window
def test_the_tunnel_window_has_a_layers_row(tmp_path):
    node = shutil.which("node")

    if node is None:
        pytest.skip("node is not installed here")

    from pathlib import Path

    web = Path(pathprobe.__file__).resolve().parents[1] / "web"
    app, css = (web / "app.js").read_text(encoding="utf-8"), (web / "app.css").read_text(encoding="utf-8")
    assert 'row("Layers", tunLayersCell(t), ' in app and ".lay-c" in css
    code = app[app.index("const LAYER_WORD ="): app.index("const UP_WORD =")]
    driver = tmp_path / "ly.js"
    driver.write_text(
        "const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\"/g, '&quot;'); const ago = () => '5m';\n" + code + "\n"
        "const r = (tls, n = 8, err = '') => ({n, conn: n, tls, p50: 110, p90: 130, err});\n"
        "const res = {ok: tunLayersCell({record: {layers: {at: 1, node: r(8), tunnel: r(8), path: null, state: {code: 'ok'}, text: ''}}}),\n"
        "  tun: tunLayersCell({record: {layers: {at: 1, node: r(8), tunnel: r(3, 8, 'ConnectionResetError'), path: null, state: {code: 'tunnel'}, text: 'the tunnel resets or stalls new connections'}}}),\n"
        "  none: tunLayersCell({}), odd: tunLayersCell({record: {layers: {at: 1, state: {code: 'whatever'}}}})};\n"
        "process.stdout.write(JSON.stringify(res));\n", encoding="utf-8")
    r = subprocess.run([node, str(driver)], capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert r.returncode == 0, r.stderr[-600:]
    res = json.loads(r.stdout)
    assert '<b class="gw-ok">Every layer is fine</b>' in res["ok"] and "Node <b>8/8</b>" in res["ok"] and "Tunnel <b>8/8</b>" in res["ok"] and "Iran path <b>-</b>" in res["ok"] and "5m ago" in res["ok"]
    assert '<b class="err" title="the tunnel resets or stalls new connections">The tunnel resets new connections</b>' in res["tun"] and 'class="lay-c err"' in res["tun"] and "Tunnel <b>3/8</b>" in res["tun"] and "ConnectionResetError" in res["tun"]
    assert "not looked at yet" in res["none"] and "not looked at yet" in res["odd"]                                  # (a tunnel with no config, or a verdict this panel does not know: no made-up answer)
