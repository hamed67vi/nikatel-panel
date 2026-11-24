"""A tunnel that stays DEGRADED because a port is shut (the foreign end cannot connect in, so the Iran side does not listen) is a tunnel whose users are cut: it must climb the smart repair like a flapping one
(the owner, 2026-11-24: the Canada tunnel sat degraded for 3 h 40 min at 100 % loss and no smart repair, move or suggestion was ever made)."""
import time

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot as ap

SHUT = "port 8075, 1840, 11444 is not listening on the Iran server"


def _tunnel(name, state, err, since_ago=600):
    with session() as db:
        ir = Server(name=f"{name}-ir", host="203.0.113.11", role="iran", online=True)
        fr = Server(name=f"{name}-fr", host="203.0.113.12", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backpack", transport="wss", port=9906, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, forwarded_ports=[8072], record={})
        t.state_since, t.last_error = int(time.time()) - since_ago, err
        db.add(t)
        db.flush()
        return t.id


def _arm(monkeypatch):
    started = []
    monkeypatch.setattr(ap, "smart_fix_start", lambda *a, **k: started.append(a))
    monkeypatch.setattr(ap.actions, "doctor_mode", lambda: "move")
    monkeypatch.setattr(ap.get_settings(), "read_only", False)
    monkeypatch.setattr(ap, "outage_state", lambda: {"storm": False})
    return started


def test_what_a_degraded_error_must_say_to_climb():
    f = ap.climbs_degraded
    assert f(SHUT) and f("port 8072 accepts connections but no data comes back through the tunnel") and f("port 8075 answers on the server but not from another Iran server: blocked in front of the server")
    assert f("40% loss on the GRE link") and f("the link flaps: 4 of 10 checks failed") and f("no data moves through the tunnel under load")        # (what always climbed)
    assert not f("") and not f(None) and not f("slow: 380 ms, usually 120 ms")                                                                       # (a slow line is only watched)


def test_a_degraded_tunnel_with_a_shut_port_starts_the_smart_repair(monkeypatch):
    tid = _tunnel("sp-shut", "degraded", SHUT)
    started = _arm(monkeypatch)
    ap.auto_escalate()
    assert [a[0] for a in started] == [tid]                                                  # (it was never started before: the state is degraded, the error names no loss)


def test_a_degraded_tunnel_that_is_only_slow_is_left_to_the_looks(monkeypatch):
    _tunnel("sp-slow", "degraded", "slow: 380 ms, usually 120 ms")
    started = _arm(monkeypatch)
    ap.auto_escalate()
    assert started == []


def test_a_young_degraded_tunnel_waits_the_first_two_minutes(monkeypatch):
    _tunnel("sp-new", "degraded", SHUT, since_ago=30)
    started = _arm(monkeypatch)
    ap.auto_escalate()
    assert started == []                                                                     # (`DEGRADED_AFTER`: a shut port can be a restart in progress)
