"""BackPack 2.0.0 (2026-11-20) removed the UDP and QUIC carriers of its direct tunnel and made its direct PCK a port forwarder without an interface: the panel never offers, picks or builds the three kinds
(the owner, 2026-11-22: "update every BackPack tunnel to version 2" - the nightly update had stopped on a standby of the UDP kind, and three standby builds of the PCK kind failed on the new core)."""
import pytest

from nikatel.api import tunnels as api_tunnels
from nikatel.config import get_settings
from nikatel.services import build_other, knowledge, license, standby, tunnel_test

NOW = 1_800_000_000


def _ok(kind, score=70, at=NOW):
    return knowledge.merge(None, {"kind": kind, "ok": True, "score": score, "latency_ms": 90.0, "loss_pct": 0.0}, "latency", at)


def test_the_three_direct_kinds_backpack_2_cannot_run_pass_the_gate_nowhere_and_the_others_stay():
    assert license.RETIRED_KINDS == {"bpd:udp", "bpd:quic", "bpd:pck"}
    assert not any(license.tunnel_kind_allowed(k) for k in license.RETIRED_KINDS)
    assert all(license.tunnel_kind_allowed(k) for k in ("bpd:xdi", "bp:pck", "bp:quic", "bp:kcp", "bp:wssmux", "bp:tcpmux", "paqet", "chisel:fwd", "tcpmux", "ipsec:tcpmux"))          # (the REVERSE transports of the same names are as before: BackPack 2 changed the direct tunnel)
    assert {k[0] for k in tunnel_test.KINDS} >= license.RETIRED_KINDS and set(build_other.BP_DIRECT) >= {"udp", "quic", "pck"}          # (the tables keep them: a row that still names one has its label)


def test_the_menus_do_not_offer_them():
    offered = {k[0] for k in tunnel_test.buildable_kinds()}
    assert not offered & license.RETIRED_KINDS and len(tunnel_test.buildable_kinds()) == len(tunnel_test.KINDS) - 3 and "bpd:xdi" in offered
    assert set(build_other.direct_carriers()) == set(build_other.BP_DIRECT) - {"udp", "quic", "pck"} and {"sni", "spoof", "xdi"} <= set(build_other.direct_carriers())
    listed = {k["key"] for k in api_tunnels.test_kinds()["kinds"]}
    assert not listed & license.RETIRED_KINDS and "bp:wssmux" in listed and "paqet" in listed


def test_the_survey_and_the_standby_leave_them_out():
    assert not {k for k in knowledge.READY + knowledge.MORE if license.tunnel_kind_allowed(k)} & license.RETIRED_KINDS          # (what the night survey builds)
    kind, _note = standby.choose_kind(["bpd:pck", "bpd:udp", "bpd:quic", "paqet", "chisel:fwd"], "bp:wssmux", {}, NOW, allowed=license.tunnel_kind_allowed)
    assert kind in ("paqet", "chisel:fwd")          # (the first three stood first in the list: on 2026-11-22 a PCK standby was tried first and lost a minute, three times)
    assert standby.choose_kind(["bpd:pck", "bpd:udp"], "bp:wssmux", {}, NOW, allowed=license.tunnel_kind_allowed)[0] == ""          # (nothing else known: no standby rather than one that cannot start)


def test_the_doctors_order_names_none_of_them_for_a_cut_or_a_slow_tunnel():
    for lossy in (False, True):
        kinds, _lines = knowledge.plan_for(990001, 990002, "tcpmux", False, lossy, now=NOW, dial_i=True, dial_f=True)
        assert kinds and not set(kinds) & license.RETIRED_KINDS, (lossy, kinds)

    cut, _lines = knowledge.plan_for(990001, 990002, "tcpmux", False, False, now=NOW, dial_i=True, dial_f=True)
    assert "bpd:xdi" in cut and "paqet" in cut and "chisel:fwd" in cut          # (the forward kinds that still work are all there)


def test_a_retired_kind_that_tested_well_is_no_way_out_of_a_cut():
    pair = {"kinds": {"bpd:pck": _ok("bpd:pck", 95), "bpd:udp": _ok("bpd:udp", 90), "paqet": _ok("paqet", 72), "bp:wssmux": _ok("bp:wssmux", 80)}}
    r = knowledge.readiness(pair, NOW)
    assert (r["iran_cut"]["level"], r["iran_cut"]["kind"], r["iran_cut"]["n"]) == ("ready", "paqet", 1)
    assert (r["foreign_cut"]["level"], r["foreign_cut"]["kind"]) == ("ready", "bp:wssmux")
    assert knowledge.readiness({"kinds": {"bpd:pck": _ok("bpd:pck", 95), "bpd:quic": _ok("bpd:quic", 88)}}, NOW)["iran_cut"]["level"] == "none"


def test_a_build_asked_for_by_hand_is_refused_at_once_with_the_reason(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)

    for carrier in ("udp", "quic", "pck", ""):          # ("": the old default of the form was UDP)
        with pytest.raises(ValueError, match="BackPack 2 cannot run it"):
            build_other.start_build(1, 2, "8080", "backpack_direct", carrier)

    with pytest.raises(ValueError, match="bpd:udp, bpd:pck: BackPack 2 cannot run it"):
        tunnel_test.compare_start(1, 2, ["bpd:udp", "bpd:pck"])
