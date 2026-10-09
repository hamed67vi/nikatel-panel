from nikatel.models import Tunnel
from nikatel.services.tunnels import Plan, Side, judge, parse_side, plan_for, settle

IRAN_OK = """UNIT backhaul-iran-tcpmux-9443.service active
UNIT nikatel-gre-x.service active
IFACE x UNKNOWN 123456 654321
PEER 10.2.56.18 81.2 0
LISTEN 80 1
NOW 1791566810
"""
FOREIGN_OK = "UNIT backhaul-kharej-tcpmux-9443.service active\nUNIT nikatel-gre-x.service active\nNOW 1\n"


def _plan():
    return Plan(["backhaul-iran-tcpmux-9443.service", "nikatel-gre-x.service"],
                ["backhaul-kharej-tcpmux-9443.service", "nikatel-gre-x.service"], iface="x", iran_peer="10.2.56.18", ports=[80])


def test_parse_side():
    s = parse_side(IRAN_OK)
    assert s.units["nikatel-gre-x.service"] == "active"
    assert s.iface_state == "UNKNOWN" and s.rx_bytes == 123456 and s.tx_bytes == 654321
    assert s.rtt_ms == 81.2 and s.loss_pct == 0 and s.listen[80] is True and s.now == 1791566810


def test_judge_up():
    assert judge(_plan(), parse_side(IRAN_OK), parse_side(FOREIGN_OK)) == ("up", "")


def test_judge_unit_down():
    bad = FOREIGN_OK.replace("backhaul-kharej-tcpmux-9443.service active", "backhaul-kharej-tcpmux-9443.service inactive")
    state, why = judge(_plan(), parse_side(IRAN_OK), parse_side(bad))
    assert state == "down" and "backhaul-kharej" in why and "inactive" in why


def test_judge_peer_silent_and_lossy():
    silent = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", "PEER 10.2.56.18  100")
    assert parse_side(silent).rtt_ms is None and parse_side(silent).loss_pct == 100
    assert judge(_plan(), parse_side(silent), parse_side(FOREIGN_OK))[0] == "down"
    nothing = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", "PEER 10.2.56.18")
    assert judge(_plan(), parse_side(nothing), parse_side(FOREIGN_OK))[0] == "down"
    lossy = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", "PEER 10.2.56.18 81.2 33")
    assert judge(_plan(), parse_side(lossy), parse_side(FOREIGN_OK))[0] == "degraded"


def test_judge_port_not_listening_is_degraded():
    closed = IRAN_OK.replace("LISTEN 80 1", "LISTEN 80 0")
    state, why = judge(_plan(), parse_side(closed), parse_side(FOREIGN_OK))
    assert state == "degraded" and "80" in why


def test_judge_server_unreachable():
    s = Side()
    s.error = "timeout after 35s"
    assert judge(_plan(), s, parse_side(FOREIGN_OK)) == ("unknown", "the Iran server does not answer (timeout after 35s)")


def test_plan_for_backhaul():
    t = Tunnel(name="cz-ir4", type="backhaul", transport="tcpmux", port=9902, foreign_tunnel_ip="10.2.1.2", forwarded_ports=[80, "443"])
    p = plan_for(t)
    assert p.iran_units == ["backhaul-iran-tcpmux-9902.service", "nikatel-gre-cz-ir4.service"]
    assert p.foreign_units[0] == "backhaul-kharej-tcpmux-9902.service"
    assert p.iface == "cz-ir4" and p.iran_peer == "10.2.1.2" and p.ports == [80, 443]


def test_plan_for_backpack_and_chisel():
    assert plan_for(Tunnel(name="a", type="backpack")).foreign_units == ["backpack-a-kharej.service"]
    assert plan_for(Tunnel(name="a", type="chisel")).iran_units == ["chisel-a.service"]


def test_small_loss_is_normal_big_loss_is_degraded():
    # the Iran-abroad paths lose a few percent on a normal day: 1-2 of 20 pings is not an event
    for pct in ("5", "10"):
        lossy = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", f"PEER 10.2.56.18 81.2 {pct}")
        assert judge(_plan(), parse_side(lossy), parse_side(FOREIGN_OK)) == ("up", "")

    lossy = IRAN_OK.replace("PEER 10.2.56.18 81.2 0", "PEER 10.2.56.18 81.2 15")
    assert judge(_plan(), parse_side(lossy), parse_side(FOREIGN_OK))[0] == "degraded"


def test_settle_needs_two_checks_for_up_degraded_flips():
    st, streak = settle("up", "degraded", None)
    assert st == "up" and streak == {"state": "degraded", "n": 1}        # one lossy check: still up
    st, streak = settle("up", "degraded", streak)
    assert st == "degraded" and streak["n"] == 2                          # confirmed
    st, streak = settle("degraded", "up", streak)
    assert st == "degraded"                                               # one clean check: still degraded
    st, streak = settle("degraded", "up", streak)
    assert st == "up"
    # a stopped unit applies at once, in both directions
    assert settle("up", "down", None)[0] == "down"
    assert settle("down", "up", None)[0] == "up"
    assert settle("unknown", "up", None)[0] == "up"