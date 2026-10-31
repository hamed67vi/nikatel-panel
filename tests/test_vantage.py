"""The panel tells an Iran server that is ON from one that is OFF even when it is reachable only from inside Iran (the owner, 2026-10-31, with a check-host.net ping: Iranian locations 4/4, every other country 0/4; and:
"do not use check-host - in the cuts only the spoof tunnel answered").

The pulse knew only whether the panel's host could connect. services/vantage.py asks for a proof of life: the FOREIGN end of the tunnels of the server (open connections, ping over an L3 link), then the other Iran servers
of the panel. An answer is a proof the pulse and the full look respect. A server nobody sees is still called off."""
import os
import shutil
import stat
import subprocess
import time

import pytest
from sqlalchemy import select

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import facts, pulse, vantage
from nikatel.ssh import SSHError


@pytest.fixture(autouse=True)
def _only_my_servers():
    """The database is shared by the tests of a run: the servers of the other tests are switched off for the length of each test here (and put back), and mine are removed at its end."""
    def mine(db):
        ids = [s.id for s in db.scalars(select(Server)) if s.name.startswith("vt-")]

        for t in list(db.scalars(select(Tunnel))):
            if t.iran_server_id in ids or t.foreign_server_id in ids:
                db.delete(t)

        db.flush()

        for s in list(db.scalars(select(Server))):
            if s.name.startswith("vt-"):
                db.delete(s)

    with session() as db:
        mine(db)
        was = [s.id for s in db.scalars(select(Server)) if s.enabled]

        for s in db.scalars(select(Server)):
            s.enabled = False

    yield

    with session() as db:
        mine(db)

        for i in was:
            s = db.get(Server, i)

            if s is not None:
                s.enabled = True


def _clear():
    for d in (pulse._miss, pulse._seen, pulse._asked, pulse._slow_until, pulse._refusing, pulse._via_seen, pulse._missed_at, pulse._proof):
        d.clear()


def _server(tag, host, role="iran", online=True, error="", last_seen_ago=9999):
    with session() as db:
        s = Server(name=f"vt-{tag}", host=host, role=role, online=online, last_error=error, enabled=True, ssh_port=22, last_seen_at=int(time.time() - last_seen_ago))
        db.add(s)
        db.flush()
        return s.id


def _tunnel(tag, iran, foreign, transport="tcp", record=None, inner=""):
    with session() as db:
        t = Tunnel(name=f"vt-{tag}", type="backpack" if transport.startswith("l3:") else "backhaul", transport=transport, iran_server_id=iran, foreign_server_id=foreign, state="down", enabled=True,
                   forwarded_ports=[8075], record=record or {}, iran_tunnel_ip=inner)
        db.add(t)
        db.flush()
        return t.id


def _row(sid):
    with session() as db:
        s = db.get(Server, sid)
        return s.online, s.last_error


def _pulse_cut(monkeypatch, host):
    monkeypatch.setattr(pulse, "tcp_open", lambda h, port, timeout=2.0: h != host)
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    monkeypatch.setattr(pulse, "_wake_vantage", lambda: None)
    monkeypatch.setattr("nikatel.ssh.via_of", lambda host_: "")


# ------------------------------------------------------------------ the pure parts
def test_the_answers_of_the_scripts_are_read():
    assert vantage.opened("REACH 203.0.113.5:22 1 12\n", "203.0.113.5", 22) is True
    assert vantage.opened("REACH 203.0.113.5:22 0 0\n", "203.0.113.5", 22) is False
    assert vantage.opened("", "203.0.113.5", 22) is False and vantage.opened("REACH 203.0.113.55:22 1 3", "203.0.113.5", 22) is False
    assert vantage.parse_view("PEER_EST 3\nPEER_PING 2/3\n") == (3, 2)
    assert vantage.parse_view("PEER_EST 0\n") == (0, 0) and vantage.parse_view("") == (0, 0) and vantage.parse_view("PEER_EST x\nPEER_PING ?\n") == (0, 0)


def test_what_a_foreign_end_says_and_who_saw_it():
    assert vantage.seen_by_tunnel("aaa", 3, 0) == "3 open connections of its tunnel to aaa"
    assert vantage.seen_by_tunnel("aaa", 1, 3) == "1 open connection of its tunnel to aaa"
    assert vantage.seen_by_tunnel("aaa", 0, 2) == "the link of its tunnel to aaa answers"
    assert vantage.seen_by_tunnel("aaa", 0, 0) == ""
    assert vantage.decide(["x", "y", "z"], ["irn-a"]) == (True, "seen by its tunnels: x; y")          # (the tunnels come first)
    assert vantage.decide([], ["irn-a", "irn-b"]) == (True, "seen from Iran by irn-a, irn-b")
    assert vantage.decide([], []) == (False, "")


def test_the_script_counts_connections_and_pings_over_the_link(tmp_path):
    """The real text of `peer_alive` run by bash against a fake `ss` and `ping` (nothing is read from the network)."""
    if shutil.which("bash") is None:
        pytest.skip("no bash")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ss").write_text("#!/bin/bash\necho 'ESTAB 0 0 10.1.1.1:1 198.51.100.9:2'\necho 'ESTAB 0 0 10.1.1.1:3 198.51.100.9:4'\n")
    (bindir / "ping").write_text("#!/bin/bash\necho '3 packets transmitted, 2 received, 33% packet loss'\n")

    for f in ("ss", "ping"):
        (bindir / f).chmod((bindir / f).stat().st_mode | stat.S_IEXEC)

    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}

    def run(params):
        text = ssh._script_text("peer_alive", params)
        return subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=20, check=False, env=env).stdout

    assert vantage.parse_view(run({"PEER": "198.51.100.9", "IFACE": "", "INNER": ""})) == (2, 0)                   # (no L3 link: connections only)
    assert vantage.parse_view(run({"PEER": "198.51.100.9", "IFACE": "bp10", "INNER": "10.10.10.1"})) == (2, 2)
    assert "PEER_PING" not in run({"PEER": "198.51.100.9", "IFACE": "", "INNER": ""})

    with pytest.raises(ValueError):
        ssh._script_text("peer_alive", {"PEER": "198.51.100.9; reboot", "IFACE": "", "INNER": ""})              # (the parameters are checked, as for every reviewed script)

    with pytest.raises(ValueError):
        ssh._script_text("peer_alive", {"PEER": "198.51.100.9", "IFACE": "bp10 -f", "INNER": ""})


# ------------------------------------------------------------------ asking
def test_the_foreign_end_of_a_tunnel_is_asked_first_with_its_l3_link_and_the_iran_servers_are_not_asked_then(monkeypatch):
    _clear()
    t = _server("t1", "198.51.100.111", online=False)
    f1 = _server("f1", "203.0.113.10", role="foreign")
    f2 = _server("f2", "203.0.113.11", role="foreign", online=False)          # (a foreign server that is off cannot be asked)
    _tunnel("a", t, f1, transport="l3:pck", record={"inner_net": 10}, inner="10.10.10.1")
    _tunnel("b", t, f2)
    _server("v1", "198.51.100.112")
    asked = []

    def script(node, name, params=None, timeout=0):
        asked.append((node.host, name, dict(params or {})))
        return "PEER_EST 0\nPEER_PING 3/3\n"

    monkeypatch.setattr(vantage, "run_script", script)
    alive, how = vantage.ask(t, set())
    assert alive is True and how == "seen by its tunnels: the link of its tunnel to vt-f1 answers"
    assert asked == [("203.0.113.10", "peer_alive", {"PEER": "198.51.100.111", "IFACE": "bp10", "INNER": "10.10.10.1"})]


def test_a_tcp_tunnel_is_seen_by_its_open_connections(monkeypatch):
    _clear()
    t = _server("t2", "198.51.100.121", online=False)
    f = _server("f3", "203.0.113.12", role="foreign")
    _tunnel("c", t, f)
    monkeypatch.setattr(vantage, "run_script", lambda node, name, params=None, timeout=0: "PEER_EST 4\n")
    assert vantage.ask(t, set()) == (True, "seen by its tunnels: 4 open connections of its tunnel to vt-f3")


def test_when_no_tunnel_sees_it_the_iran_servers_that_work_open_the_connection_and_the_cut_ones_are_not_asked(monkeypatch):
    _clear()
    t = _server("t3", "198.51.100.131", online=False)
    f = _server("f4", "203.0.113.13", role="foreign")
    _tunnel("d", t, f)
    _server("v2", "198.51.100.132")
    cut = _server("v3", "198.51.100.133")
    asked = []

    def script(node, name, params=None, timeout=0):
        asked.append((node.host, name))

        if name == "peer_alive":
            return "PEER_EST 0\n"

        return "REACH 198.51.100.131:22 1 12\n"

    monkeypatch.setattr(vantage, "run_script", script)
    alive, how = vantage.ask(t, {cut})
    assert alive is True and how == "seen from Iran by vt-v2"
    assert asked == [("203.0.113.13", "peer_alive"), ("198.51.100.132", "tcp_reach")]


def test_nobody_seeing_it_proves_nothing_and_a_server_that_cannot_be_asked_is_no_crash(monkeypatch):
    _clear()
    t = _server("t4", "198.51.100.141", online=False)
    f = _server("f5", "203.0.113.14", role="foreign")
    _tunnel("e", t, f)
    _server("v4", "198.51.100.142")

    def script(node, name, params=None, timeout=0):
        if name == "peer_alive":
            raise SSHError("ssh connection lost (Connection timed out)")

        return "REACH 198.51.100.141:22 0 0\n"

    monkeypatch.setattr(vantage, "run_script", script)
    assert vantage.ask(t, set()) == (False, "")


def test_a_foreign_server_is_never_asked(monkeypatch):
    _clear()
    f = _server("f6", "203.0.113.15", role="foreign", online=False)
    monkeypatch.setattr(vantage, "run_script", lambda *a, **k: pytest.fail("not for a foreign server"))
    assert vantage.ask(f, set()) == (False, "")


# ------------------------------------------------------------------ the job and the pulse
def test_the_job_proves_the_servers_the_pulse_misses_and_the_pulse_keeps_them_on(monkeypatch):
    _clear()
    t = _server("t5", "198.51.100.151", online=True)
    _pulse_cut(monkeypatch, "198.51.100.151")
    pulse.run()
    assert t in pulse.missing() and _row(t) == (True, "")                                # (one miss: not off yet)
    monkeypatch.setattr(vantage, "ask", lambda sid, skip: (True, "seen by its tunnels: 2 open connections of its tunnel to aaa"))
    vantage.run()
    assert pulse.proven(t)

    for _ in range(3):
        pulse._slow_until.clear()
        pulse.run()

    online, err = _row(t)
    assert online is True and err == f"{pulse.IRAN_ONLY} (seen by its tunnels: 2 open connections of its tunnel to aaa)" and pulse._miss.get(t, 0) == 0


def test_a_server_the_pulse_already_called_off_is_put_back_on_with_one_event_when_it_is_proven(monkeypatch):
    _clear()
    t = _server("t6", "198.51.100.161", online=False, error=f"{pulse.TAG} 22: 40 quick checks in a row got no connection")
    _pulse_cut(monkeypatch, "198.51.100.161")
    pulse.prove(t, "seen by its tunnels: the link of its tunnel to aaa answers")
    pulse.run()
    pulse._slow_until.clear()
    pulse.run()
    online, err = _row(t)
    assert online is True and err.startswith(pulse.IRAN_ONLY) and "its tunnel to aaa" in err

    with session() as db:
        ev = [e.text for e in db.scalars(select(Event).where(Event.ref == "vt-t6"))]

    assert len([x for x in ev if "is on" in x]) == 1                                   # (one event, not one per round)


def test_the_proof_ends_after_its_time_and_the_server_is_called_off_as_before(monkeypatch):
    _clear()
    t = _server("t7", "198.51.100.171", online=True)
    _pulse_cut(monkeypatch, "198.51.100.171")
    pulse._proof[t] = (time.time() - pulse.PROOF_GRACE - 5, "seen from Iran by irn-a")      # (nobody has seen it for over three minutes)
    pulse.run()
    pulse._slow_until.clear()
    pulse.run()
    online, err = _row(t)
    assert online is False and err.startswith(pulse.TAG)


def test_a_direct_connection_that_works_again_drops_the_proof_and_the_note(monkeypatch):
    _clear()
    t = _server("t8", "198.51.100.181", online=True, error=f"{pulse.IRAN_ONLY} (seen from Iran by irn-a)")
    pulse._proof[t] = (time.time(), "seen from Iran by irn-a")
    pulse._missed_at[t] = time.time()
    monkeypatch.setattr(pulse, "tcp_open", lambda h, port, timeout=2.0: True)
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    pulse.run()
    assert _row(t) == (True, "") and not pulse.proven(t) and t not in pulse.missing()


def test_the_first_miss_of_an_iran_server_wakes_the_job_and_a_foreign_one_does_not(monkeypatch):
    _clear()
    ir = _server("t9", "198.51.100.191")
    fo = _server("t10", "198.51.100.192", role="foreign")
    woke = []
    monkeypatch.setattr(pulse, "tcp_open", lambda h, port, timeout=2.0: h not in ("198.51.100.191", "198.51.100.192"))
    monkeypatch.setattr(pulse, "_wake_tunnels", lambda ids=None: None)
    monkeypatch.setattr(pulse, "_wake_vantage", lambda: woke.append(1))
    monkeypatch.setattr("nikatel.ssh.via_of", lambda host_: "")
    pulse.run()
    assert len(woke) == 1 and {ir, fo} <= set(pulse.missing())


# ------------------------------------------------------------------ the full look
def test_a_failed_ssh_look_of_a_proven_server_keeps_it_on(monkeypatch):
    _clear()
    t = _server("t11", "198.51.100.201", online=False, error="old")
    pulse.prove(t, "seen by its tunnels: the link of its tunnel to aaa answers")
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: (_ for _ in ()).throw(SSHError("ssh connection lost (Connection timed out)")))
    facts.sample_server(t)
    online, err = _row(t)
    assert online is True and err.startswith(pulse.IRAN_ONLY) and "Connection timed out" in err


def test_a_failed_ssh_look_of_a_server_nobody_sees_turns_it_off_as_before(monkeypatch):
    _clear()
    t = _server("t12", "198.51.100.211", online=True)
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: (_ for _ in ()).throw(SSHError("ssh connection lost (Connection timed out)")))
    facts.sample_server(t)
    assert _row(t)[0] is False
