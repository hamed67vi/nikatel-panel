"""A mass disruption is declared by the servers that CARRY tunnels, not by every server that is off (1.69.269).
The owner, 2026-11-14: tonight the panel said "Mass disruption: 3 servers offline, 1 of 20 tunnels down" - two of the three had been off for days (one for three days) and carried no tunnel - and the rule says no tunnel is
deleted or rebuilt while it lasts."""
import itertools

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot

HOSTS = itertools.count(100)


def server(name, role, online):
    n = next(HOSTS)

    with session() as db:
        s = Server(name=name, host=f"203.0.113.{n}" if role == "iran" else f"198.51.100.{n}", role=role, online=online)
        db.add(s)
        db.flush()
        return s.id


def tunnel(name, iran, foreign, state="up", enabled=True):
    with session() as db:
        db.add(Tunnel(name=name, type="backpack", transport="wssmux", port=9901 + next(HOSTS), iran_server_id=iran, foreign_server_id=foreign, state=state, forwarded_ports=[8076], foreign_tunnel_ip="10.2.9.2",
                      record={}, secrets={}, enabled=enabled))


def test_three_offline_servers_of_which_two_carry_nothing_are_no_mass_disruption():
    ok_i, ok_f = server("sc1-ir-ok", "iran", True), server("sc1-fr-ok", "foreign", True)
    dead_a, dead_b = server("sc1-dead-a", "foreign", False), server("sc1-dead-b", "iran", False)       # (off for days, no tunnel on them)
    flaky = server("sc1-flaky", "iran", False)                                                         # (carries a tunnel)
    tunnel("sc1-t-ok", ok_i, ok_f)
    tunnel("sc1-t-flaky", flaky, ok_f, state="down")
    o = autopilot.outage_state()
    assert {"sc1-dead-a", "sc1-dead-b", "sc1-flaky"} <= set(o["servers_offline"])                     # (all of them are still listed as off: the pages say so)
    assert o["servers_offline_carrying"].count("sc1-flaky") == 1 and "sc1-dead-a" not in o["servers_offline_carrying"] and "sc1-dead-b" not in o["servers_offline_carrying"]
    assert dead_a and dead_b


def test_three_offline_servers_that_each_carry_a_tunnel_are_a_mass_disruption(monkeypatch):
    monkeypatch.setattr(autopilot, "STORM_SERVERS", 3)
    f = server("sc2-fr-ok", "foreign", True)
    names = []

    for i in range(3):
        s = server(f"sc2-off-{i}", "iran", False)
        tunnel(f"sc2-t-{i}", s, f, state="down")
        names.append(f"sc2-off-{i}")

    o = autopilot.outage_state()
    assert set(names) <= set(o["servers_offline_carrying"])
    assert len(o["servers_offline_carrying"]) >= 3 and o["storm"] is True


def test_a_standby_that_is_off_does_not_make_its_server_a_carrier():
    f, i = server("sc3-fr", "foreign", True), server("sc3-ir", "iran", True)
    spare = server("sc3-spare", "iran", False)
    tunnel("sc3-t", i, f)
    tunnel("sc3-t-sb", spare, f, enabled=False)                                                        # (a warm standby is a switched-off row: it carries nobody)
    assert "sc3-spare" not in autopilot.outage_state()["servers_offline_carrying"]
