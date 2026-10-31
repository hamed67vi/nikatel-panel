"""An Iran server that the panel reaches only through another Iran server stays on (the owner, 2026-10-31: "their tunnels show down but their configs are connected").

The panel's host (abroad) cannot connect to such a server - every TCP connection times out - but the full look goes through a jump server (ssh.py) and works. The pulse knew only its own connection: it put the server off three
seconds after every good look, so the row and the tunnel flapped and the Doctor repaired a tunnel that carried the users. A server that is really off must still be marked off."""
import time

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Server
from nikatel.services import pulse


def _clear():
    for d in (pulse._miss, pulse._seen, pulse._asked, pulse._slow_until, pulse._refusing, pulse._via_seen):
        d.clear()


def _server(tag, host, last_seen_ago, online=True):
    with session() as db:
        s = Server(name=f"vi-{tag}", host=host, role="iran", online=online, last_error="", enabled=True, ssh_port=22, last_seen_at=int(time.time() - last_seen_ago))
        db.add(s)
        db.flush()
        return s.id


def _row(sid):
    with session() as db:
        s = db.get(Server, sid)
        return s.online, s.last_error


def _silent(monkeypatch, host):
    monkeypatch.setattr(pulse, "tcp_open", lambda h, port, timeout=2.0: h != host)
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)


def test_a_server_reached_through_a_jump_and_looked_at_lately_stays_on_when_the_pulse_misses(monkeypatch):
    _clear()
    sid = _server("a", "198.51.100.101", last_seen_ago=40)
    _silent(monkeypatch, "198.51.100.101")
    monkeypatch.setattr(ssh, "via_of", lambda host: "198.51.100.5" if host == "198.51.100.101" else "")
    pulse.run()
    pulse._slow_until.clear()
    pulse.run()
    pulse._slow_until.clear()
    pulse.run()
    online, err = _row(sid)
    assert online is True and err.startswith(pulse.IRAN_ONLY) and pulse._miss.get(sid, 0) == 0          # (the row says why it is on)


def test_it_is_not_asked_every_three_seconds(monkeypatch):
    _clear()
    _server("b", "198.51.100.102", last_seen_ago=10)
    calls = []
    monkeypatch.setattr(pulse, "tcp_open", lambda h, port, timeout=2.0: (calls.append(h), h != "198.51.100.102")[1])
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    monkeypatch.setattr(ssh, "via_of", lambda host: "198.51.100.5" if host == "198.51.100.102" else "")
    pulse.run()
    pulse.run()
    pulse.run()
    assert calls.count("198.51.100.102") == 1                                         # (twenty seconds between two looks, like a server that refuses)


def test_a_server_with_no_jump_is_marked_off_after_two_misses_as_before(monkeypatch):
    _clear()
    sid = _server("c", "198.51.100.103", last_seen_ago=40)
    _silent(monkeypatch, "198.51.100.103")
    monkeypatch.setattr(ssh, "via_of", lambda host: "")
    pulse.run()
    pulse.run()
    online, err = _row(sid)
    assert online is False and err.startswith(pulse.TAG)                              # (a recent look alone is not enough: a switched-off server is still called off in ~8 s)


def test_a_carried_server_whose_full_look_is_old_is_marked_off(monkeypatch):
    _clear()
    sid = _server("d", "198.51.100.104", last_seen_ago=400)
    _silent(monkeypatch, "198.51.100.104")
    monkeypatch.setattr(ssh, "via_of", lambda host: "198.51.100.5" if host == "198.51.100.104" else "")
    pulse.run()
    pulse._slow_until.clear()
    pulse.run()
    online, err = _row(sid)
    assert online is False and err.startswith(pulse.TAG)                              # (the jump stopped working too: nobody has seen it for over two minutes)


def test_the_memory_of_the_jump_is_fifteen_minutes(monkeypatch):
    _clear()
    sid = _server("e", "198.51.100.105", last_seen_ago=30)
    pulse._via_seen[sid] = time.time() - 600                                           # (the jump record of ssh.py ended, the panel was reaching it that way ten minutes ago)
    assert pulse.carried(sid, time.time() - 30) is True
    pulse._via_seen[sid] = time.time() - 1000
    assert pulse.carried(sid, time.time() - 30) is False
    pulse._via_seen[sid] = time.time()
    assert pulse.carried(sid, time.time() - 151) is False and pulse.carried(sid, None) is False
