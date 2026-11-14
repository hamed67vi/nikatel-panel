"""A port that does not ANSWER the Doctor's probe is not a port that "carries nothing" (1.69.272). The owner, 2026-11-15, asked for the five worries of the overall look to be done; the third was the Doctor's moves
for quality that never succeeded: the Iran servers with the SS fence drop loopback connects to the Shadowsocks ports, the probe was killed after six seconds and read as CLOSED."""
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot, carry, failover
from nikatel.ssh import render_action


def test_the_probe_script_tells_a_silent_port_from_a_closed_one():
    script = render_action("ports_carry", {"CHECK": "8072 1820"})
    assert '124) echo "NOANSWER $P"' in script and '0) echo "CARRIES $P"' in script and '99) echo "NOCONNECT $P"' in script and '*) echo "CLOSED $P"' in script
    assert script.index("124)") < script.index("*) echo")                      # (the specific answers come before the catch-all)


def test_the_reader_knows_the_new_answer():
    assert carry.parse("CARRIES 8072\nNOANSWER 1820\nCLOSED 11426\nNOCONNECT 1826\nnoise\nNOANSWER x\n") == {8072: "CARRIES", 1820: "NOANSWER", 11426: "CLOSED", 1826: "NOCONNECT"}


def test_the_monitors_verdict_skips_what_did_not_answer():
    maps = [(8072, 8072), (1820, 1820), (11426, 8680)]
    assert carry.verdict(maps, {8072: "CARRIES", 1820: "NOANSWER", 11426: "CARRIES"}, {1820: "CARRIES", 8680: "CARRIES"}) == ([], [])          # (the fence drops the probe: nothing is told)
    assert carry.verdict(maps, {8072: "CARRIES", 1820: "CLOSED", 11426: "CARRIES"}, {1820: "CARRIES", 8680: "CARRIES"}) == ([1820], [])        # (closed at once although the service answers: the tunnel's fault, as before)
    assert carry.verdict(maps, {11426: "CLOSED"}, {8680: "NOANSWER"}) == ([], [])                                                              # (the far end did not answer either: not the tunnel's fault, and not "the service does not answer")
    assert carry.verdict(maps, {11426: "CLOSED"}, {8680: "CLOSED"}) == ([], [11426])                                                           # (as before: the service closes a bare connection by itself)


def _world():
    with session() as db:
        ir, fr = Server(name="cn-ir-1", host="203.0.113.201", role="iran", online=True), Server(name="cn-fr-1", host="198.51.100.201", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        db.add(Tunnel(name="cn-t-mv", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8072, 1820]))


def test_a_move_is_not_stopped_by_a_port_that_did_not_answer(monkeypatch):
    _world()
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    built = [(0, "cn-t-mv", {"name": "cn-t"})]
    monkeypatch.setattr("nikatel.ssh.run_action", lambda node, name, params=None, timeout=90: "CARRIES 8072\nNOANSWER 1820\n")
    assert failover._not_carrying(7, built) == [] and not said
    monkeypatch.setattr("nikatel.ssh.run_action", lambda node, name, params=None, timeout=90: "CARRIES 8072\nCLOSED 1820\n")
    assert failover._not_carrying(7, built) == ["cn-t: 1820"] and any("carries nothing" in x for x in said)
    monkeypatch.setattr("nikatel.ssh.run_action", lambda node, name, params=None, timeout=90: "NOCONNECT 8072\nNOANSWER 1820\n")
    assert failover._not_carrying(7, built) == ["cn-t: 8072"]
