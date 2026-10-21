"""The Doctor looks fully into a tunnel that is up but that nobody uses and, if it really has a problem, fixes it (the owner, 2026-10-21: "a tunnel nobody uses may be at an hour when the users are truly not there - check
and test it completely, and if it really has a problem, fix it"; a config was cut and its tunnel said connected).

What is tested: the turn of the watch (the silence starts, lasts, a look once per ZERO_GAP, ends when the users are back), what counts as a problem (the pure rule), the full look (a fresh check, the pings, the capacity,
the mark of what a user gets), a quiet tunnel with nothing wrong is said to be fine and the problem is beyond it, a tunnel with a problem is fixed, the mark and its word in the list, and the Doctor off."""
import time
from pathlib import Path

from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import actions, autopilot, bandwidth, livebw, tunnel_test

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"
N = iter(range(120, 200))


def test_the_silence_is_counted_a_look_comes_once_in_a_while_and_the_users_coming_back_ends_it():
    step = bandwidth.zero_step
    e, look = step(None, 1000, 0.0, True)
    assert e == {"since": 1000} and look is False
    e, look = step(e, 1000 + 299, 0.0, True)
    assert look is False
    e, look = step(e, 1000 + 300, 0.01, True)
    assert look is True and e["looked"] == 1300 and e["since"] == 1000
    e, look = step(e, 1000 + 600, 0.0, True)
    assert look is False
    e, look = step(e, 1300 + bandwidth.ZERO_GAP, 0.0, True)
    assert look is True
    assert step(e, 5000, 2.5, True) == (None, False) and step(e, 5000, None, True) == (None, False) and step(e, 5000, 0.0, False) == (None, False)


def test_what_counts_as_a_problem_of_a_tunnel_nobody_uses():
    ok = {"state": "up", "data_fails": 0, "ext_code": "404", "loss": 0.0, "ping": 41.0, "usual_ping": 40.0, "bw_state": "ok", "carries": 869.0}
    assert bandwidth.silent_problems(ok) == []
    assert any("is down" in p for p in bandwidth.silent_problems({**ok, "state": "down", "last_error": "no answer"}))
    assert any("test through its port failed" in p for p in bandwidth.silent_problems({**ok, "data_fails": 2}))
    assert any("another Iran server" in p for p in bandwidth.silent_problems({**ok, "ext_code": "000"}))
    assert any("12 % of the packets" in p for p in bandwidth.silent_problems({**ok, "loss": 12.0}))
    assert any("almost nothing" in p for p in bandwidth.silent_problems({**ok, "carries": 0.2})) and any("dropped" in p for p in bandwidth.silent_problems({**ok, "bw_state": "drop"}))
    assert any("ping is 150 ms, usually 40 ms" in p for p in bandwidth.silent_problems({**ok, "ping": 150.0}))
    assert bandwidth.silent_problems({**ok, "ping": 70.0}) == [] and bandwidth.silent_problems({**ok, "usual_ping": None, "ping": 400.0}) == []          # (not twice AND 60 ms more; no usual: no judgement)


def _tunnel(tag, record=None):
    n = next(N)

    with session() as db:
        i, f = Server(name=f"ir-{tag}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fo-{tag}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        t = Tunnel(name=f"t-{tag}", type="backhaul", transport="tcpmux", iran_server_id=i.id, foreign_server_id=f.id, state="up", enabled=True, forwarded_ports=[8075], record=record or {},
                   detail={"data_fails": 0, "ext": {"code": "404", "ms": 41.0, "from": "x"}, "loss_pct": 0.0}, rtt_ms=41.0)
        db.add(t)
        db.flush()
        return t.id, t.name


def _wire(monkeypatch, tid, quality, bw, mode="fix"):
    from nikatel.config import get_settings
    from nikatel.services import tunnels

    calls ={"checked": [], "fixed": [], "looked": []}
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: mode)
    monkeypatch.setattr(livebw, "view", lambda: {str(tid): {"down": 0.0, "up": 0.0, "age": 3}})
    monkeypatch.setattr(tunnels, "check_tunnel", lambda i, fresh_ext=False, quick=False: calls["checked"].append((i, fresh_ext)))
    monkeypatch.setattr(tunnel_test, "quality", lambda i: quality)
    monkeypatch.setattr(bandwidth, "measure", lambda i: bw)
    monkeypatch.setattr(bandwidth, "_investigate_later", lambda i: calls["looked"].append(i))
    monkeypatch.setattr(autopilot, "smart_fix_start", lambda i, level="switch", source="manual": calls["fixed"].append((i, level, source)) or 7)
    monkeypatch.setattr(bandwidth, "_usual_ping", lambda i: 40.0)
    bandwidth._ZERO.clear()
    bandwidth._ZERO[tid] = {"since": int(time.time()) - 400}
    return calls


def test_a_quiet_tunnel_with_nothing_wrong_is_said_to_be_fine_with_the_numbers_and_the_problem_is_beyond_it(monkeypatch):
    tid, name = _tunnel("s1")
    calls = _wire(monkeypatch, tid, {"latency_ms": 41.0, "jitter_ms": 0.5, "loss_pct": 0.0}, ({"down": 869.0, "state": "ok"}, ""))
    bandwidth._zero_watch()
    assert calls["checked"] == [(tid, True)] and calls["fixed"] == [] and calls["looked"] == []

    with session() as db:
        rec = db.get(Tunnel, tid).record["silent"]
        ev = [(e.level, e.text) for e in db.query(Event).all() if name in e.text]

    assert rec["bad"] is False and rec["problems"] == [] and rec["fixing"] is False and rec["carries"] == 869.0 and rec["ping"] == 41.0 and 0 < rec["user"] <= 100
    assert "nothing is wrong with the tunnel" in rec["note"] and "quiet hour" in rec["note"] and "beyond it" in rec["note"] and "ping 41 ms" in rec["note"] and "carries 869 Mb/s" in rec["note"]
    assert any(lv == "info" and "Nobody has used it for 6 minutes" in tx for lv, tx in ev)
    bandwidth._zero_watch()
    assert len(calls["checked"]) == 1                                                                 # (the same silence again: not looked at twice)


def test_a_quiet_tunnel_with_a_real_problem_is_fixed_now(monkeypatch):
    tid, name = _tunnel("s2")
    calls = _wire(monkeypatch, tid, {"latency_ms": 180.0, "jitter_ms": 9.0, "loss_pct": 14.0}, ({"down": 0.2, "state": "drop"}, "drop"))
    bandwidth._zero_watch()
    assert calls["fixed"] == [(tid, "switch", "doctor")]

    with session() as db:
        rec = db.get(Tunnel, tid).record["silent"]
        ev = [(e.level, e.text) for e in db.query(Event).all() if name in e.text]

    assert rec["bad"] is True and rec["fixing"] is True and any("14 % of the packets" in p for p in rec["problems"]) and any("ping is 180 ms" in p for p in rec["problems"])
    assert any(lv == "warn" and "It is fixing it now" in tx for lv, tx in ev)


def test_the_users_coming_back_clear_the_mark_and_a_failed_ping_test_does_not_stop_the_look(monkeypatch):
    tid, _name = _tunnel("s3")
    view = {str(tid): {"down": 0.0, "up": 0.0, "age": 3}}
    calls = _wire(monkeypatch, tid, {}, ({"down": 500.0, "state": "ok"}, ""))
    monkeypatch.setattr(livebw, "view", lambda: view)
    monkeypatch.setattr(tunnel_test, "quality", lambda i: (_ for _ in ()).throw(ValueError("no GRE link to measure")))
    bandwidth._zero_watch()

    with session() as db:
        assert db.get(Tunnel, tid).record["silent"]["ping"] == 41.0                                   # (the ping of the last check stands in)

    view[str(tid)] = {"down": 12.0, "up": 1.0, "age": 3}
    bandwidth._zero_watch()

    with session() as db:
        assert "silent" not in (db.get(Tunnel, tid).record or {})

    assert calls["fixed"] == []


def test_nothing_is_done_while_the_doctor_is_off(monkeypatch):
    tid, _name = _tunnel("s4")
    calls = _wire(monkeypatch, tid, {"latency_ms": 41.0}, ({"down": 500.0, "state": "ok"}, ""), mode="off")
    bandwidth._zero_watch()
    assert calls["checked"] == [] and calls["fixed"] == []


def test_the_list_says_no_users_only_for_a_tunnel_the_doctor_checked_and_found_fine():
    js, css = (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "lab.css").read_text(encoding="utf-8")
    fn = js.split("const bwMeterFor = t => {", 1)[1].split("const bwTone", 1)[0]
    assert "idle = !prob && !full && sil && !sil.bad && sil.looked_at && Date.now() / 1000 - sil.looked_at < 7200" in fn and 'full ? "nearly full" : idle ? "no users" : ""' in fn and "idle ? sil.note" in fn
    assert "html body #page .tun-tbl .bwm.ok .bwm-t small:not(:empty)" in css
