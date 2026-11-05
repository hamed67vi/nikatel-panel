"""An Iran server that is off is replaced in about a minute (1.69.215; the owner, 2026-11-04: "when the Doctor sees a server is off it must replace it at once"), and the Doctor wakes the moment the pulse says so.
`server_minutes` (5 for the owner) only counts when the panel itself cannot be sure: most servers off at the same moment is the panel's own network."""
import time

from nikatel.services import failover, pulse
from nikatel.services import settings as st


def _world(monkeypatch, extra_off=0):
    from nikatel.config import get_settings
    from nikatel.db import session
    from nikatel.models import Server, Tunnel

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    monkeypatch.setattr(failover, "_describe", lambda p: "moved")
    st.put("repair", {"mode": "move", "server_minutes": 5})

    with session() as db:
        s, f = Server(name="ir9", host="203.0.113.77", role="iran", online=False), Server(name="fx", host="203.0.113.78", role="foreign", online=True)
        db.add_all([s, f])

        for i in range(extra_off):
            db.add(Server(name=f"gone{i}", host=f"203.0.113.{100 + i}", role="foreign", online=False))

        db.flush()
        db.add(Tunnel(name="t9", type="backhaul", transport="tcpmux", iran_server_id=s.id, foreign_server_id=f.id, state="down", forwarded_ports=[80]))
        sid = s.id

    ran = []
    monkeypatch.setattr(failover, "plan_dead", lambda server_id: {"server_id": server_id, "server": "ir9", "tunnels": [{"id": 1, "name": "t9"}], "dns": [], "target": {"id": 2, "name": "ir2"}, "problems": [], "pulled": []})
    monkeypatch.setattr(failover, "run_plan", lambda p: ran.append(p) or 1)
    return sid, ran


def test_the_panel_is_blind_when_most_servers_are_off_at_once():
    assert failover.panel_blind(10, 6) is True
    assert failover.panel_blind(10, 3) is False                # three of ten: those are servers, not the panel
    assert failover.panel_blind(5, 3) is False                 # under BLIND_MIN
    assert failover.panel_blind(0, 0) is False


def test_an_off_server_waits_a_minute_unless_the_panel_is_blind():
    assert failover.off_wait(300, False) == failover.OFF_WAIT == 60
    assert failover.off_wait(30, False) == 30                  # the owner may ask for less
    assert failover.off_wait(300, True) == 300                 # the panel cannot be sure: the old waiting time


def test_an_off_iran_server_is_moved_after_a_minute_not_five(monkeypatch):
    sid, ran = _world(monkeypatch)
    st.put("failover", {"seen": {str(sid): int(time.time()) - 20}})
    failover.check()
    assert ran == []                                           # twenty seconds: the pulse may have been wrong, nothing moves
    st.put("failover", {"seen": {str(sid): int(time.time()) - 70}})
    failover.check()
    assert len(ran) == 1 and ran[0]["server_id"] == sid        # a minute and ten: moved, although `server_minutes` says five


def test_the_full_waiting_time_stands_when_most_servers_are_off_at_once(monkeypatch):
    sid, ran = _world(monkeypatch, extra_off=4)                # five of six servers off: the panel's own network
    st.put("failover", {"seen": {str(sid): int(time.time()) - 70}})
    failover.check()
    assert ran == []
    st.put("failover", {"seen": {str(sid): int(time.time()) - 400}})
    failover.check()
    assert len(ran) == 1


def _pulse_server(role):
    from nikatel.db import session
    from nikatel.models import Server

    with session() as db:
        db.add(Server(name=f"p-{role}", host="203.0.113.55", role=role, online=True))


def test_the_pulse_wakes_the_doctor_when_an_iran_server_goes_off(client, monkeypatch):
    woke = []
    monkeypatch.setattr(pulse.jobs, "trigger", lambda name: woke.append(name))
    pulse._miss.clear()
    pulse._missed_at.clear()
    _pulse_server("iran")
    monkeypatch.setattr(pulse, "tcp_open", lambda h, p, timeout=3.0: False)
    pulse.run()
    assert "doctor" not in woke                                # one miss says nothing
    pulse.run()
    assert woke.count("doctor") == 1                           # two misses: it is off, the waiting time starts now


def test_a_foreign_server_going_off_does_not_wake_the_doctor(client, monkeypatch):
    woke = []
    monkeypatch.setattr(pulse.jobs, "trigger", lambda name: woke.append(name))
    pulse._miss.clear()
    pulse._missed_at.clear()
    _pulse_server("foreign")
    monkeypatch.setattr(pulse, "tcp_open", lambda h, p, timeout=3.0: False)
    pulse.run()
    pulse.run()
    assert "doctor" not in woke
