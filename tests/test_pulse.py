from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import pulse


def _server(**kw):
    with session() as db:
        db.add(Server(name="s1", host="203.0.113.5", role="iran", **kw))


def _state():
    with session() as db:
        s = db.scalar(select(Server))
        return s.online, s.last_error


def test_two_misses_mark_the_server_off_and_one_hit_puts_it_back(client, monkeypatch):
    pulse._miss.clear()
    _server(online=True)
    monkeypatch.setattr(pulse, "tcp_open", lambda h, p, timeout=3.0: False)
    pulse.run()
    assert _state()[0] is True                  # one miss says nothing (a lost packet is not a dead server)
    pulse.run()
    online, err = _state()
    assert online is False and err.startswith(pulse.TAG)
    monkeypatch.setattr(pulse, "tcp_open", lambda h, p, timeout=3.0: True)
    pulse.run()
    assert _state() == (True, "")


def test_a_server_whose_login_is_broken_is_not_put_back_by_the_pulse(client, monkeypatch):
    pulse._miss.clear()
    _server(online=False, last_error="SSH login failed: Permission denied (publickey)")
    monkeypatch.setattr(pulse, "tcp_open", lambda h, p, timeout=3.0: True)
    pulse.run()
    assert _state() == (False, "SSH login failed: Permission denied (publickey)")
