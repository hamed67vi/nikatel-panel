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
    from nikatel.services import facts

    pulse._miss.clear()
    pulse._asked.clear()
    asked = []
    _server(online=False, last_error="SSH login failed: Permission denied (publickey)")
    monkeypatch.setattr(pulse, "tcp_open", lambda h, p, timeout=3.0: True)
    monkeypatch.setattr(facts, "sample_server", lambda sid: asked.append(sid))        # the question over SSH (it would answer "permission denied" again)
    pulse.run()
    assert _state() == (False, "SSH login failed: Permission denied (publickey)")


def test_a_server_the_full_sample_called_off_is_asked_over_ssh_at_once_when_its_port_answers(client, monkeypatch):
    import time as _t

    from nikatel.services import facts

    pulse._miss.clear()
    pulse._asked.clear()
    pulse._ASKING.clear()
    asked = []
    _server(online=False, last_error="ssh connection lost (network)")
    monkeypatch.setattr(pulse, "tcp_open", lambda h, p, timeout=3.0: True)
    monkeypatch.setattr(facts, "sample_server", lambda sid: asked.append(sid))
    pulse.run()
    _t.sleep(0.3)
    assert len(asked) == 1
    pulse._ASKING.clear()
    pulse.run()                                                 # not again within the retry time
    _t.sleep(0.3)
    assert len(asked) == 1


def test_a_server_whose_ssh_port_answers_is_never_called_offline_by_a_failed_sample(client, monkeypatch):
    from nikatel.services import facts

    pulse._miss.clear()
    _server(online=True)

    with session() as db:
        sid = db.scalar(select(Server)).id

    def boom(node, name, params=None, timeout=40):
        raise facts.SSHError("ssh connection lost (network)", 255)

    monkeypatch.setattr(facts, "run_script", boom)
    pulse._seen[sid] = __import__("time").time()                # the pulse saw the port answer just now
    facts.sample_server(sid)
    online, err = _state()
    assert online is True and err.startswith("SSH does not answer, the server is up")
    pulse._seen[sid] = 0.0                                      # the port did not answer for a long time: now it is offline
    facts.sample_server(sid)
    assert _state()[0] is False
