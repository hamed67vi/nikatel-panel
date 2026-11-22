"""BackPack 2.0.0 (2026-11-20) removed the UDP and QUIC carriers of its direct tunnel: the panel never offers, picks or builds `bpd:udp` and `bpd:quic` (the owner, 2026-11-22: "update every BackPack tunnel to version 2" - the
nightly update had stopped on a standby of the UDP kind). Its direct PCK became a different engine (ports only, no interface) and is built the new way since 1.69.296: it is allowed (tests/test_pck_direct.py)."""
import pytest

from nikatel.api import tunnels as api_tunnels
from nikatel.config import get_settings
from nikatel.services import build_other, knowledge, license, standby, tunnel_test

NOW = 1_800_000_000


def _ok(kind, score=70, at=NOW):
    return knowledge.merge(None, {"kind": kind, "ok": True, "score": score, "latency_ms": 90.0, "loss_pct": 0.0}, "latency", at)


def test_the_two_direct_kinds_backpack_2_cannot_run_pass_the_gate_nowhere_and_the_others_stay():
    assert license.RETIRED_KINDS == {"bpd:udp", "bpd:quic"}
    assert not any(license.tunnel_kind_allowed(k) for k in license.RETIRED_KINDS)
    assert all(license.tunnel_kind_allowed(k) for k in ("bpd:pck", "bpd:xdi", "bp:pck", "bp:quic", "bp:kcp", "bp:wssmux", "bp:tcpmux", "paqet", "chisel:fwd", "tcpmux", "ipsec:tcpmux"))          # (the direct PCK is built the new way; the REVERSE transports of the same names were never touched)
    assert {k[0] for k in tunnel_test.KINDS} >= license.RETIRED_KINDS and set(build_other.BP_DIRECT) >= {"udp", "quic", "pck"}          # (the tables keep them: a row that still names one has its label)


def test_the_menus_do_not_offer_them():
    offered = {k[0] for k in tunnel_test.buildable_kinds()}
    assert not offered & license.RETIRED_KINDS and len(tunnel_test.buildable_kinds()) == len(tunnel_test.KINDS) - 2 and {"bpd:pck", "bpd:xdi"} <= offered
    carriers = build_other.direct_carriers()
    assert set(carriers) == set(build_other.BP_DIRECT) - {"udp", "quic"} and {"pck", "sni", "spoof", "xdi"} <= set(carriers) and next(iter(carriers)) == "pck"          # (PCK first: the form's default)
    listed = {k["key"] for k in api_tunnels.test_kinds()["kinds"]}
    assert not listed & license.RETIRED_KINDS and {"bp:wssmux", "paqet", "bpd:pck"} <= listed


def test_the_survey_and_the_standby_leave_them_out():
    assert not {k for k in knowledge.READY + knowledge.MORE if license.tunnel_kind_allowed(k)} & license.RETIRED_KINDS          # (what the night survey builds)
    kind, _note = standby.choose_kind(["bpd:udp", "bpd:quic", "bpd:pck", "paqet", "chisel:fwd"], "bp:wssmux", {}, NOW, allowed=license.tunnel_kind_allowed)
    assert kind == "bpd:pck"          # (the first two stood first in the list: on 2026-11-22 a standby of the UDP kind did not come back and stopped the update of the cores)
    assert standby.choose_kind(["bpd:udp", "bpd:quic"], "bp:wssmux", {}, NOW, allowed=license.tunnel_kind_allowed)[0] == ""          # (nothing else known: no standby rather than one that cannot start)


def test_the_doctors_order_names_none_of_them_for_a_cut_or_a_slow_tunnel():
    for lossy in (False, True):
        kinds, _lines = knowledge.plan_for(990001, 990002, "tcpmux", False, lossy, now=NOW, dial_i=True, dial_f=True)
        assert kinds and not set(kinds) & license.RETIRED_KINDS, (lossy, kinds)

    cut, _lines = knowledge.plan_for(990001, 990002, "tcpmux", True, False, now=NOW, dial_i=True, dial_f=True)          # (GRE is filtered: the cut order without the GRE kinds, the Iran server dials)
    assert cut[0] == "bpd:pck" and {"bpd:xdi", "paqet", "chisel:fwd"} <= set(cut)          # (the forward kinds that still work are all there - the direct PCK first)


def test_a_retired_kind_that_tested_well_is_no_way_out_of_a_cut_but_the_direct_pck_is():
    pair = {"kinds": {"bpd:pck": _ok("bpd:pck", 95), "bpd:udp": _ok("bpd:udp", 99), "paqet": _ok("paqet", 72), "bp:wssmux": _ok("bp:wssmux", 80)}}
    r = knowledge.readiness(pair, NOW)
    assert (r["iran_cut"]["level"], r["iran_cut"]["kind"], r["iran_cut"]["n"]) == ("ready", "bpd:pck", 2)          # (the UDP one scored best and is not counted)
    assert (r["foreign_cut"]["level"], r["foreign_cut"]["kind"]) == ("ready", "bp:wssmux")
    assert knowledge.readiness({"kinds": {"bpd:udp": _ok("bpd:udp", 95), "bpd:quic": _ok("bpd:quic", 88)}}, NOW)["iran_cut"]["level"] == "none"


def test_a_build_asked_for_by_hand_is_refused_at_once_with_the_reason_and_pck_is_the_default(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)

    for carrier in ("udp", "quic"):
        with pytest.raises(ValueError, match="BackPack 2 cannot run it"):
            build_other.start_build(1, 2, "8080", "backpack_direct", carrier)

    with pytest.raises(ValueError, match="bpd:udp, bpd:quic: BackPack 2 cannot run it"):
        tunnel_test.compare_start(1, 2, ["bpd:udp", "bpd:quic"])

    for carrier in ("", "pck"):          # (no carrier named: the default is PCK now - it gets past the checks of the carrier and stops at the servers, which do not exist)
        with pytest.raises(ValueError, match="choose an Iran server"):
            build_other.start_build(990001, 990002, "8080", "backpack_direct", carrier)
