"""A working tunnel that showed "no ping" (the owner, 2026-10-17: BackPack wssmux, the provider drops ICMP between its two servers): the "ping" is ten TCP handshakes to the control port of the tunnel, made by the
server that dials it, whenever ICMP is dropped and the users' path works."""
import time

import pytest
from sqlalchemy import select

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import tunnels as tn


# ------------------------------------------------------------------ pure
def test_the_answer_of_the_tcp_ping_script_is_read():
    assert tn.parse_tping("TPING 203.0.113.9:9916 10 9 88\n") == (88.0, 10.0)                       # nine of ten answered, 88 ms
    assert tn.parse_tping("noise\nTPING 203.0.113.9:9916 10 10 0\n") == (0.0, 0.0)                   # sub-millisecond: 0 ms, nothing lost
    assert tn.parse_tping("TPING 203.0.113.9:9916 10 0 0\n") == (None, 100.0)                        # nothing answered: 100 % lost, no latency
    assert tn.parse_tping("") == (None, None) and tn.parse_tping("TPING x") == (None, None) and tn.parse_tping("TPING h:1 0 0 0") == (None, None)


def test_the_tcp_ping_goes_from_the_server_that_dials_the_control_port_and_only_for_kinds_with_a_tcp_port():
    for kind in ("bp:wssmux", "bp:tcpmux", "bp:wss", "bp:ws", "bp:wsmux", "bp:tcp", "bp:stealth", "chisel", "chisel:tls"):
        assert tn.tcp_target(kind, 9916, "203.0.113.5", "198.51.100.7") == ("foreign", "203.0.113.5", 9916), kind        # reverse: the foreign server dials the Iran server's port
    assert tn.tcp_target("chisel:fwd", 9610, "203.0.113.5", "198.51.100.7") == ("iran", "198.51.100.7", 9610)           # forward: the Iran server dials the foreign server's port
    assert tn.tcp_target("chisel:fwdtls", 9610, "203.0.113.5", "198.51.100.7") == ("iran", "198.51.100.7", 9610)

    for kind in ("paqet", "bp:kcp", "bp:quic", "bp:pck", "bpd:udp", "tcpmux", "wg:tcpmux", "fou:tcpmux"):                # UDP / raw / the GRE kinds (their own link is pinged): no TCP port to knock on
        assert tn.tcp_target(kind, 9916, "203.0.113.5", "198.51.100.7") is None, kind

    assert tn.tcp_target("bp:wssmux", 0, "203.0.113.5", "198.51.100.7") is None and tn.tcp_target("bp:wssmux", 70000, "203.0.113.5", "198.51.100.7") is None


def test_the_script_takes_only_a_host_and_a_port():
    text = ssh._script_text("tcp_ping", {"HOST": "203.0.113.5", "PORT": "9916"})
    assert "/dev/tcp/$h/$p" in text and "timeout 1" in text and "rm -rf" in text and "203.0.113.5" in text
    assert "systemctl" not in text and "kill" not in text

    for bad in ({"HOST": "203.0.113.5; reboot", "PORT": "9916"}, {"HOST": "203.0.113.5", "PORT": "99;id"}, {"HOST": "", "PORT": "1"}, {"HOST": "$(id)", "PORT": "1"}):
        with pytest.raises(ValueError):
            ssh._script_text("tcp_ping", bad)


# ------------------------------------------------------------------ the check
def world(name, typ="backpack", transport="wssmux", port=9916):
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type=typ, transport=transport, port=port, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", state_since=int(time.time()) - 300, forwarded_ports=[8300 + n])
        db.add(t)
        db.flush()
        return t.id, ir.host, fr.host


def fake_world(monkeypatch, tping, icmp="PEER {peer}\n"):
    """The servers answer: every unit active, the users' port answers (404), ICMP to the peer is dropped (`icmp`); the TCP ping answers `tping`. Returns the list of (host the script ran on, params)."""
    ran = []

    def query(node, plan, is_iran, quick=False):
        text = "".join(f"UNIT {u} active\n" for u in (plan.iran_units if is_iran else plan.foreign_units))

        if is_iran:
            text += icmp.format(peer=plan.ping or "x") + "DATA 8310 404 0.083\n"

        return tn.parse_side(text + "NOW 1000\n")

    def run(node, name, params=None, timeout=40):
        ran.append((node.host, name, dict(params or {})))
        return tping

    monkeypatch.setattr(tn, "_query", query)
    monkeypatch.setattr(tn, "run_script", run)
    monkeypatch.setattr(tn.jobs, "stopping", lambda: False)
    monkeypatch.setattr(tn.jobs, "trigger", lambda name: None)
    return ran


def detail_of(tid):
    with session() as db:
        t = db.get(Tunnel, tid)
        return dict(t.detail), t.rtt_ms, t.state


def test_a_reverse_tunnel_whose_icmp_is_dropped_is_measured_by_handshakes_from_the_foreign_server(monkeypatch):
    tid, iran_host, foreign_host = world("np1")
    ran = fake_world(monkeypatch, "TPING 203.0.113.11:9916 10 9 88\n")
    tn._check_tunnel(tid)
    d, rtt, state = detail_of(tid)
    assert state == "up" and d["loss_pct"] == 10.0 and d["ping_blocked"] is False and d["ping_over"] == "tcp"       # a number, not "no ping"
    assert rtt == 88.0                                                                                          # the handshake time is the latency between the servers (not the time of the users' request, 83 ms)
    assert ran == [(foreign_host, "tcp_ping", {"HOST": iran_host, "PORT": "9916"})]                              # made by the FOREIGN server, to the Iran server's control port


def test_a_forward_chisel_is_measured_from_the_iran_server(monkeypatch):
    tid, iran_host, foreign_host = world("np2", typ="chisel", transport="fws", port=9610)
    ran = fake_world(monkeypatch, "TPING x:9610 10 10 41\n")
    tn._check_tunnel(tid)
    d, rtt, _ = detail_of(tid)
    assert d["loss_pct"] == 0.0 and d["ping_over"] == "tcp" and rtt == 41.0 and ran == [(iran_host, "tcp_ping", {"HOST": foreign_host, "PORT": "9610"})]


def test_when_the_handshakes_do_not_answer_either_it_stays_honest(monkeypatch):
    tid, _, _ = world("np3")
    fake_world(monkeypatch, "TPING x:9916 10 0 0\n")
    tn._check_tunnel(tid)
    d, rtt, state = detail_of(tid)
    assert state == "up" and d["ping_blocked"] is True and d["loss_pct"] is None and d["ping_over"] == "servers"      # "no ping" - nothing else can be measured
    assert rtt == 83.0                                                                                           # the time of the users' request, as before


def test_a_tunnel_whose_icmp_works_is_not_knocked_on(monkeypatch):
    tid, _, _ = world("np4")
    ran = fake_world(monkeypatch, "TPING x:9916 10 10 5\n", icmp="PEER {peer} 87.5 0\n")
    tn._check_tunnel(tid)
    d, rtt, _ = detail_of(tid)
    assert d["loss_pct"] == 0.0 and d["ping_over"] == "servers" and rtt == 87.5 and ran == []                    # the ping answered: no handshakes at all


def test_a_kind_with_no_tcp_control_port_is_not_knocked_on(monkeypatch):
    tid, _, _ = world("np5", typ="paqet", transport="paqet", port=9650)
    ran = fake_world(monkeypatch, "TPING x:9650 10 10 5\n")
    tn._check_tunnel(tid)
    d, _, _ = detail_of(tid)
    assert d["ping_blocked"] is True and d["ping_over"] == "servers" and ran == []


def test_a_server_that_is_off_is_not_asked_for_the_handshakes(monkeypatch):
    tid, _, _ = world("np6")

    with session() as db:
        t = db.get(Tunnel, tid)
        t.foreign.online = False

    ran = fake_world(monkeypatch, "TPING x:9916 10 10 5\n")
    tn._check_tunnel(tid)
    assert ran == []
