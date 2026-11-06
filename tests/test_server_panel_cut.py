"""A server the panel's own check cannot reach, but whose PasarGuard node is connected, is ON (the owner, 2026-11-06 19:25: TR2, TR3 and tr-test showed a red Offline for 80 seconds while their configs stayed connected - the
nodes talk to their own panel, not to ours), and a FOREIGN server that is on but cut from the panel's host wears "Cut from panel", never "Iran only" (that tag is for an Iran server)."""
import json
import time

import pytest
from sqlalchemy import select
from test_ui_regression_walk import APP_JS, LAB_JS, _function, _line, _node, needs_node

from nikatel.db import session
from nikatel.models import Panel, Server
from nikatel.services import facts, pulse
from nikatel.ssh import SSHError

HOST = "203.0.113.141"


@pytest.fixture(autouse=True)
def _mine_only():
    """The database is shared by the tests of a run: the other servers are switched off for each test here (and put back), mine and my panel are removed."""
    def clean(db):
        for s in list(db.scalars(select(Server))):
            if s.name.startswith("pc-"):
                db.delete(s)

        for p in list(db.scalars(select(Panel))):
            if p.name == "pc-main":
                db.delete(p)

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


def _server(tag, online=True, error="", role="foreign", host=HOST):
    with session() as db:
        s = Server(name=f"pc-{tag}", host=host, role=role, online=online, last_error=error, enabled=True, ssh_port=22, last_seen_at=int(time.time() - 9999))
        db.add(s)
        db.flush()
        return s.id


def _row(sid):
    with session() as db:
        s = db.get(Server, sid)
        return s.online, s.last_error


def _panel(status="connected", fails=0, address=HOST, age=0, panel_status="up", enabled=True):
    with session() as db:
        for p in list(db.scalars(select(Panel))):
            if p.name == "pc-main":
                db.delete(p)

        db.flush()
        db.add(Panel(name="pc-main", type="pasarguard", url="https://p.example.net", enabled=enabled, status=panel_status, checked_at=int(time.time()) - age,
                     nodes=[{"id": 7, "name": "TRX", "address": address, "status": status, "fails": fails}]))


def _net(monkeypatch):
    """The panel's own check never gets through (every port is silent)."""
    monkeypatch.setattr(pulse, "tcp_open", lambda host, port, timeout=2.0: False)
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    monkeypatch.setattr(pulse, "_wake_vantage", lambda: None)
    monkeypatch.setattr("nikatel.ssh.via_of", lambda host_: "")


def _pulse(times):
    for _ in range(times):
        pulse._slow_until.clear()
        pulse.run()


# ---------------------------------------------------------------- the second opinion
def test_a_connected_node_read_a_moment_ago_is_a_second_opinion_and_nothing_else_is():
    _panel()
    assert pulse.node_connected(HOST) is True
    assert pulse.node_connected("203.0.113.9") is False                          # another address
    _panel(status="error")
    assert pulse.node_connected(HOST) is False                                   # the node is not connected
    _panel(fails=2)
    assert pulse.node_connected(HOST) is False                                   # a node that failed its last checks
    _panel(age=pulse.NODE_FRESH + 30)
    assert pulse.node_connected(HOST) is False                                   # the panel was not read lately: an old answer proves nothing
    _panel(panel_status="down")
    assert pulse.node_connected(HOST) is False                                   # the panel itself does not answer
    _panel(enabled=False)
    assert pulse.node_connected(HOST) is False


def test_two_misses_do_not_call_a_server_off_while_its_node_is_connected(monkeypatch):
    sid = _server("a")
    _net(monkeypatch)
    _panel()
    _pulse(3)
    online, err = _row(sid)
    assert online is True and err.startswith(pulse.IRAN_ONLY) and pulse.NODE_HOW in err
    assert pulse.needs_jump(err) is True                                         # (the jobs go the other way, as for any server cut from the panel's host)


def test_without_the_second_opinion_the_same_two_misses_still_call_it_off(monkeypatch):
    sid = _server("b")
    _net(monkeypatch)
    _pulse(2)
    online, err = _row(sid)
    assert online is False and err.startswith(pulse.TAG)                         # (nothing changed for a server with no node, or an unread panel)


def test_the_server_is_called_off_as_soon_as_its_node_is_not_connected_any_more(monkeypatch):
    sid = _server("c")
    _net(monkeypatch)
    _panel()
    _pulse(2)
    assert _row(sid)[0] is True
    _panel(status="error")                                                       # the power really went: PasarGuard drops the node, the next read says so
    _pulse(2)
    online, err = _row(sid)
    assert online is False and err.startswith(pulse.TAG)


def test_a_server_the_pulse_called_off_is_put_back_when_its_node_is_connected(monkeypatch):
    sid = _server("d", online=False, error=pulse.TAG + " 22: 2 quick checks in a row got no connection")
    _net(monkeypatch)
    _panel()
    _pulse(1)
    online, err = _row(sid)
    assert online is True and pulse.NODE_HOW in err


def test_a_failed_full_sample_keeps_a_server_with_a_connected_node_on(monkeypatch):
    sid = _server("e")
    _net(monkeypatch)
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: (_ for _ in ()).throw(SSHError("ssh connection lost (Connection timed out)")))
    _panel()
    facts.sample_server(sid)
    online, err = _row(sid)
    assert online is True and pulse.NODE_HOW in err and "the SSH session of the panel fails" in err
    _panel(status="error")
    facts.sample_server(sid)
    assert _row(sid)[0] is False                                                 # (the node is gone too: the failed sample calls it off, as before)
    sid2 = _server("f", online=False, host="203.0.113.142")
    _panel(address="203.0.113.142")
    facts.sample_server(sid2)
    assert _row(sid2)[0] is True                                                 # (and a server marked off that its node speaks for is put back)


# ---------------------------------------------------------------- the tag
TAG_HARNESS = r"""
globalThis.window = {};
const noLogin = () => false;
globalThis.state = {serversCache: [], tunnelsCache: []};
%(src)s
const base = {id: 1, name: "x", enabled: true, online: true, facts: {}, extra: {}};
const names = s => labIssues(s).map(i => [i.txt, i.sev, !!i.path, i.tip]);
console.log(JSON.stringify({foreign: names({...base, role: "foreign", last_error: %(cut)s}), foreign_node: names({...base, role: "foreign", last_error: %(node)s}), iran: names({...base, role: "iran", last_error: %(cut)s}),
                            off: names({...base, role: "foreign", online: false, last_error: "no answer on the SSH port 22"}), fine: names({...base, role: "foreign"})}));
"""


@needs_node
def test_a_foreign_server_cut_from_the_panel_says_cut_from_panel_and_only_an_iran_server_says_iran_only():
    src = "\n".join([_line(APP_JS, "const srvBusy = s =>"), _line(LAB_JS, "const labPeerRebooting = "), _function(LAB_JS, "function labIssues(s) {")])
    cut = pulse.IRAN_ONLY + " (seen from Iran by iran-b)"
    node = pulse.IRAN_ONLY + f" ({pulse.NODE_HOW}); the SSH session of the panel fails: timeout"
    r = _node(TAG_HARNESS.replace("%(src)s", src).replace("%(cut)s", json.dumps(cut)).replace("%(node)s", json.dumps(node)))
    assert [x[:3] for x in r["foreign"]] == [["Cut from panel", "warn", True]] and "it still answers another way" in r["foreign"][0][3]
    assert [x[:3] for x in r["foreign_node"]] == [["Cut from panel", "warn", True]] and "its PasarGuard node is connected" in r["foreign_node"][0][3]
    assert [x[:3] for x in r["iran"]] == [["Iran only", "bad", True]]
    assert [x[0] for x in r["off"]] == ["Offline"] and r["fine"] == []
