"""A server that is on but refuses the pulse is shown on (the owner, 2026-10-29: "this server is on and it shows off"; "inside a profile do not show that profile's tag - only under All").

What happened: the server BOT answered ping and ports 80 and 443; its firewall turns away new connections to port 22 that come too fast, and the pulse connected every 3 s. Every connection was refused, and a refusal was
counted as "no answer", so the row said Offline. A refusal is the machine itself replying: it is ON. A switched-off machine times out."""
import socket
import time
from pathlib import Path

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import facts, pulse

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def _clear():
    for d in (pulse._miss, pulse._seen, pulse._asked, pulse._slow_until, pulse._refusing):
        d.clear()


def _server(tag, host, online=True, error=""):
    with session() as db:
        s = Server(name=f"rf-{tag}", host=host, role="foreign", online=online, last_error=error, enabled=True, ssh_port=22)
        db.add(s)
        db.flush()
        return s.id


def _row(sid):
    with session() as db:
        s = db.get(Server, sid)
        return s.online, s.last_error


def test_a_refused_connection_is_told_from_no_answer_for_real():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()                                                                       # (nothing listens on it now: the kernel answers with a refusal)
    assert pulse.tcp_open("127.0.0.1", port, timeout=1.0) == pulse.REFUSED

    live = socket.socket()
    live.bind(("127.0.0.1", 0))
    live.listen(1)
    assert pulse.tcp_open("127.0.0.1", live.getsockname()[1], timeout=1.0) is True
    live.close()


def test_a_server_that_was_marked_off_by_the_pulse_comes_back_on_when_it_only_refuses(monkeypatch):
    _clear()
    sid = _server("a", "198.51.100.91", online=False, error=f"{pulse.TAG} 22: 47 quick checks in a row got no connection")
    monkeypatch.setattr(pulse, "tcp_open", lambda host, port, timeout=2.0: pulse.REFUSED if host == "198.51.100.91" else True)
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    pulse.run()
    online, err = _row(sid)
    assert online is True and err == pulse.REFUSAL and pulse.refusing(sid) and pulse.tcp_ok_recently(sid)


def test_a_server_that_refuses_is_looked_at_every_twenty_seconds_not_every_three(monkeypatch):
    _clear()
    sid = _server("b", "198.51.100.92")
    calls = []
    monkeypatch.setattr(pulse, "tcp_open", lambda host, port, timeout=2.0: (calls.append(host), pulse.REFUSED if host == "198.51.100.92" else True)[1])
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    pulse.run()
    first = calls.count("198.51.100.92")
    pulse.run()
    pulse.run()
    assert first == 1 and calls.count("198.51.100.92") == 1                           # (the next rounds leave it alone: a limit of a few connections per half minute is not kept tripped)
    assert _row(sid) == (True, pulse.REFUSAL)                                         # (an online server that refuses gets its reason in Problems)
    pulse._slow_until[sid] = time.time() - 1                                          # (the 20 s are over)
    pulse.run()
    assert calls.count("198.51.100.92") == 2


def test_the_note_goes_when_it_takes_connections_again_and_silence_still_marks_it_off(monkeypatch):
    _clear()
    sid = _server("c", "198.51.100.93")
    state = {"v": pulse.REFUSED}
    monkeypatch.setattr(pulse, "tcp_open", lambda host, port, timeout=2.0: state["v"] if host == "198.51.100.93" else True)
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    pulse.run()
    assert _row(sid) == (True, pulse.REFUSAL)
    pulse._slow_until.clear()
    state["v"] = True
    pulse.run()
    assert _row(sid) == (True, "") and not pulse.refusing(sid)                        # (the note is gone with the refusals)
    state["v"] = False
    pulse.run()
    pulse.run()
    online, err = _row(sid)
    assert online is False and err.startswith(pulse.TAG)                              # (a machine that does not answer at all is still off after two misses)


def test_the_full_sample_says_the_refusal_when_that_is_what_it_met(monkeypatch):
    _clear()
    sid = _server("d", "198.51.100.94", online=False, error="old")
    pulse._seen[sid] = time.time()
    pulse._refusing[sid] = time.time()
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: (_ for _ in ()).throw(facts.SSHError("Connection refused")))
    facts.sample_server(sid)
    online, err = _row(sid)
    assert online is True and err.startswith(pulse.REFUSAL) and "Connection refused" in err


def test_the_folder_name_is_drawn_under_all_only():
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert 'const labTagChip = (t, quiet) => t && !(quiet && labKindNow() !== "any") ? ' in lab
    assert lab.count("${labTagChip(p.tag, 1)}</b>") == 2                              # (both rows of the page use it)
