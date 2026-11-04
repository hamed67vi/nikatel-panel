"""Only the SSH port is blocked from the panel's host (the owner, 2026-11-05, an Iran server: TCP 22 never arrives from abroad, every other port does): the row must NOT say "Iran only" (cut from abroad) when the machine answers
on other ports; it says so only when nothing answers. Jobs still go through another Iran server in both cases, the Doctor treats only the real cut as a cut."""
import json
import time

import pytest
from sqlalchemy import select
from test_ui_regression_walk import APP_JS, LAB_JS, _function, _line, _node, needs_node

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import facts, failover, nodes, pulse
from nikatel.ssh import SSHError

HOST = "203.0.113.131"


@pytest.fixture(autouse=True)
def _mine_only():
    """The database is shared by the tests of a run: the other servers are switched off for each test here (and put back), mine are removed."""
    def clean(db):
        for s in list(db.scalars(select(Server))):
            if s.name.startswith("po-"):
                db.delete(s)

    with session() as db:
        clean(db)
        was = [s.id for s in db.scalars(select(Server)) if s.enabled]

        for s in db.scalars(select(Server)):
            s.enabled = False

    _clear()
    yield
    _clear()

    with session() as db:
        clean(db)

        for i in was:
            s = db.get(Server, i)

            if s is not None:
                s.enabled = True


def _clear():
    for d in (pulse._miss, pulse._seen, pulse._asked, pulse._slow_until, pulse._refusing, pulse._via_seen, pulse._missed_at, pulse._proof, pulse._probe):
        d.clear()


def _server(tag, online=True, error=""):
    with session() as db:
        s = Server(name=f"po-{tag}", host=HOST, role="iran", online=online, last_error=error, enabled=True, ssh_port=22, last_seen_at=int(time.time() - 9999))
        db.add(s)
        db.flush()
        return s.id


def _row(sid):
    with session() as db:
        s = db.get(Server, sid)
        return s.online, s.last_error


def _net(monkeypatch, answers):
    """tcp_open as a fake: the SSH port never answers; `answers` maps another port to True / REFUSED, all else is silent. The calls are written down."""
    calls = []

    def fake(host, port, timeout=2.0):
        calls.append(port)
        return False if port == 22 else answers.get(port, False)

    monkeypatch.setattr(pulse, "tcp_open", fake)
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    monkeypatch.setattr(pulse, "_wake_vantage", lambda: None)
    monkeypatch.setattr("nikatel.ssh.via_of", lambda host_: "")
    return calls


def test_only_the_ssh_port_blocked_is_not_iran_only(monkeypatch):
    sid = _server("a")
    _net(monkeypatch, {443: pulse.REFUSED, 2222: pulse.REFUSED, 8080: True})
    pulse.prove(sid, "seen from Iran by iran-b")
    pulse.run()
    online, err = _row(sid)
    assert online is True and err.startswith(pulse.PORT_BLOCKED) and "443" in err and "iran-b" in err and not err.startswith(pulse.IRAN_ONLY)

    with session() as db:
        assert failover.is_cut(db.get(Server, sid)) is False          # (it is not cut from abroad: the Doctor does not take it for a cut Iran server)

    assert nodes._cut_from_abroad(sid) is True                      # (the direct SSH try is still skipped: it would only wait)
    assert pulse.needs_jump(err) is True


def test_a_server_nothing_answers_on_is_still_iran_only(monkeypatch):
    sid = _server("b")
    _net(monkeypatch, {})
    pulse.prove(sid, "seen from Iran by iran-b")
    pulse.run()
    online, err = _row(sid)
    assert online is True and err.startswith(pulse.IRAN_ONLY)

    with session() as db:
        assert failover.is_cut(db.get(Server, sid)) is True

    assert nodes._cut_from_abroad(sid) is True


def test_the_probe_is_kept_not_repeated_at_every_pulse(monkeypatch):
    sid = _server("c")
    calls = _net(monkeypatch, {443: pulse.REFUSED})
    pulse.prove(sid, "seen from Iran by iran-b")

    for _ in range(3):
        pulse._slow_until.clear()
        pulse.run()

    assert sum(1 for p in calls if p != 22) == len(pulse.PROBE_PORTS)          # one probe of every port, not three
    assert pulse.answering_ports(sid, HOST, 22) == [443]


def test_a_server_whose_ssh_answers_clears_the_port_note(monkeypatch):
    sid = _server("d", error=pulse.PORT_BLOCKED + " (it answers on 443; seen from Iran by iran-b)")
    monkeypatch.setattr(pulse, "tcp_open", lambda host, port, timeout=2.0: True)
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    pulse.run()
    assert _row(sid) == (True, "")


def test_the_full_sample_writes_the_same_split(monkeypatch):
    sid = _server("e")
    _net(monkeypatch, {443: pulse.REFUSED})
    pulse.prove(sid, "seen from Iran by iran-b")
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: (_ for _ in ()).throw(SSHError("ssh connection lost (Connection timed out)")))
    facts.sample_server(sid)
    online, err = _row(sid)
    assert online is True and err.startswith(pulse.PORT_BLOCKED) and "the SSH session of the panel fails" in err


def test_the_helpers_read_the_notes():
    assert pulse.needs_jump(pulse.IRAN_ONLY + " (x)") and pulse.needs_jump(pulse.PORT_BLOCKED + " (x)")
    assert not pulse.needs_jump("") and not pulse.needs_jump(None) and not pulse.needs_jump("SSH does not answer: timeout")
    assert pulse.IRAN_ONLY not in pulse.PORT_BLOCKED and not pulse.PORT_BLOCKED.startswith(pulse.IRAN_ONLY)


# ---------------------------------------------------------------- the row tag, run with node
TAG_HARNESS = r"""
globalThis.window = {};
const noLogin = () => false;
globalThis.state = {serversCache: [], tunnelsCache: []};
%(src)s
const base = {id: 1, name: "ir-a", enabled: true, online: true, facts: {}, extra: {}};
const names = s => labIssues(s).map(i => i.txt);
console.log(JSON.stringify({cut: names({...base, last_error: %(cut)s}), port: names({...base, last_error: %(port)s}), fine: names(base)}));
"""


@needs_node
def test_the_tag_says_ssh_port_blocked_for_the_mild_note_and_iran_only_for_the_real_cut():
    src = "\n".join([_line(APP_JS, "const srvBusy = s =>"), _line(LAB_JS, "const labPeerRebooting = "), _function(LAB_JS, "function labIssues(s) {")])
    r = _node(TAG_HARNESS.replace("%(src)s", src).replace("%(cut)s", json.dumps(pulse.IRAN_ONLY + " (seen from Iran by iran-b)")).replace("%(port)s", json.dumps(pulse.PORT_BLOCKED + " (it answers on 443; seen from Iran by iran-b)")))
    assert r["cut"] == ["Iran only"] and r["port"] == ["SSH port blocked"] and r["fine"] == []
    assert "Move SSH to another port" in LAB_JS
