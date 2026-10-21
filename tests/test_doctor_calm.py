"""1.69.83 (the owner, 2026-10-22: "declutter this, make it MacBook-like" and "no more suggestions - when it sees a steady tunnel with better quality it does it by itself"): the Doctor does a better type by itself only
when it is PROVEN steady and puts nothing on its page; the Active Tasks rows are calm."""
import time
from pathlib import Path

from nikatel.config import get_settings
from nikatel.services import autopilot, bandwidth, doctor, knowledge
from nikatel.services import settings as st

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
NOW = 1_800_000_000


def _pair_world(monkeypatch, steady_tests=3, mode="fix"):
    from nikatel.db import session
    from nikatel.models import Server, Tunnel

    with session() as db:
        ir, fr = Server(name="ir-up", host="203.0.113.77", role="iran", online=True), Server(name="fr-up", host="198.51.100.77", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name="up-a", type="backhaul", transport="tcpmux", port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8076], record={"bw": {"state": "ok", "down": 90.0}}, secrets={})
        db.add(t)
        db.flush()
        tid = t.id

    st.put("repair", {"mode": mode})
    now = int(time.time())
    cur = knowledge.merge(None, {"kind": "tcpmux", "ok": True, "score": 40, "down_mbps": 90.0, "latency_ms": 100.0, "loss_pct": 0.0}, "full", now - 3600)
    other = None

    for i in range(steady_tests):
        other = knowledge.merge(other, {"kind": "tcp", "ok": True, "score": 70, "down_mbps": 500.0, "latency_ms": 100.0, "loss_pct": 0.0}, "full", now - 3600 + 60 * i)

    monkeypatch.setattr(knowledge, "pair", lambda i, f: {"kinds": {"tcpmux": cur, "tcp": other}})
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(bandwidth, "_UPGRADE_AT", {})
    return tid


def test_a_better_type_that_is_proven_steady_is_taken_by_itself_and_nothing_is_suggested(monkeypatch):
    tid = _pair_world(monkeypatch)
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn, a, k)) or 1)
    bandwidth._upgrade_check(tid)
    assert len(started) == 1 and started[0][0] == "up-a" and started[0][1] is doctor._run_retype and started[0][2][:3] == (tid, "tcp", "tcpmux") and started[0][2][4] is True and started[0][3]["source"] == "auto"
    assert "steady" in started[0][2][3] and doctor.state()["suggestions"] == []


def test_a_better_type_that_is_not_proven_steady_is_left_alone_and_nothing_is_suggested(monkeypatch):
    tid = _pair_world(monkeypatch, steady_tests=1)
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn, a, k)) or 1)
    bandwidth._upgrade_check(tid)
    assert started == [] and doctor.state()["suggestions"] == []


def test_nothing_is_changed_when_the_doctor_is_off_or_the_panel_is_read_only_or_it_changed_the_type_today(monkeypatch):
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append(1) or 1)
    tid = _pair_world(monkeypatch, mode="off")
    bandwidth._upgrade_check(tid)
    assert started == []
    st.put("repair", {"mode": "fix"})
    monkeypatch.setattr(bandwidth, "_UPGRADE_AT", {})
    doctor.mark_retyped("up-a", int(time.time()))
    bandwidth._upgrade_check(tid)
    assert started == []                                                                                      # once in 24 hours
    monkeypatch.setattr(bandwidth, "_UPGRADE_AT", {})
    doctor._state()["retype_done"].clear()
    doctor._save({**doctor._state(), "retype_done": {}})
    monkeypatch.setattr(get_settings(), "read_only", True)
    bandwidth._upgrade_check(tid)
    assert started == []


def test_the_active_tasks_rows_are_calm_and_no_type_suggestion_is_drawn():
    app, css = (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "doctor.css").read_text(encoding="utf-8")
    assert '.filter(s => s.kind !== "type")' in app and "&& bwHurts(t) && !lossy.includes(t)" in app          # no suggestion of a type; a drop the users do not feel is not an active task
    assert 'class="mut work-chip watch quiet"' in app and "work-chip.quiet" in css and ".ap-att .ap-row .ap-why { color: var(--mut)" in css and ".ap-att .ap-row { min-height: 46px" in css