"""The direct PCK of BackPack 2 (1.69.296): paqet - KCP over raw TCP segments, ports only, no tunnel interface, no inner addresses. The texts were tried between two real servers before this was written (an Iran server
with no tunnel and a foreign test server, 2026-11-22: the first byte after 2.4 s, 64 MB each way intact at 86 Mbit/s, back by itself after a restart of either end)."""
import pytest
import tomllib

from nikatel.services import build_other as bo
from nikatel.services.builder import parse_ports

TOKEN = "00ff" * 12


def _both(ports="8075, 443=8443, 10000-10001", opts=None):
    iran, foreign = bo.pck_texts("203.0.113.9", 9871, TOKEN, parse_ports(ports), 39871, opts)
    return tomllib.loads(iran)["l3"], tomllib.loads(foreign)["l3"], iran, foreign


def test_the_texts_are_toml_and_name_the_new_form_only():
    i, f, iran, foreign = _both()
    assert (i["mode"], i["carrier"], i["addr"], i["token"]) == ("dial", "pck", "203.0.113.9:9871", TOKEN)
    assert (f["mode"], f["carrier"], f["addr"], f["token"]) == ("listen", "pck", "0.0.0.0:9871", TOKEN)
    assert i["preset"] == f["preset"] == "turbo" and i["accept_udp"] is False and "accept_udp" not in f          # (the Iran end opens the ports; the foreign end only answers)
    assert i["ports"] == ["8075", "443=8443", "10000", "10001", "127.0.0.1:39871=39871"]          # (users reach THIS server's port; the foreign end dials ITS OWN 127.0.0.1: no host in a target)
    assert "ports" not in f

    for key in ("iface", "local_ip", "peer_ip", "mtu", "spoof_profile", "sni_domain"):          # (no tunnel network: the old keys are not written)
        assert key not in i and key not in f and f"{key} =" not in iran + foreign


def test_a_preset_and_the_fine_tune_keys_go_to_both_ends_and_udp_when_asked():
    opts = {"preset": "Aggressive", "accept_udp": True, "pck_conns": "3", "pck_kcp_mode": "fast3", "pck_kcp_sndwnd": 2048, "pck_kcp_rcvwnd": "2048", "pck_kcp_mtu": 1350, "pck_kcp_block": "aes-128-gcm", "connection_pool": 9}
    i, f, _a, _b = _both("8080", opts)

    for side in (i, f):
        assert side["preset"] == "aggressive" and side["pck_conns"] == 3 and side["pck_kcp_mode"] == "fast3" and (side["pck_kcp_sndwnd"], side["pck_kcp_rcvwnd"], side["pck_kcp_mtu"]) == (2048, 2048, 1350)
        assert side["pck_kcp_block"] == "aes-128-gcm" and "connection_pool" not in side          # (the cipher and the packet size must match on both ends; what is not a PCK key is not written)

    assert i["accept_udp"] is True


def test_the_settings_are_checked_before_anything_is_built():
    assert bo.pck_clean(None) == {"preset": "turbo"} and bo.pck_clean({"pck_conns": ""}) == {"preset": "turbo"}

    for bad, why in (({"preset": "fast"}, "balance, turbo or aggressive"), ({"pck_conns": 0}, "PCK sessions"), ({"pck_conns": 17}, "PCK sessions"), ({"pck_conns": "x"}, "PCK sessions"),
                     ({"pck_kcp_mtu": 100}, "packet size"), ({"pck_kcp_mtu": 1500}, "packet size"), ({"pck_kcp_sndwnd": 1}, "send window"), ({"pck_kcp_mode": "turbo"}, "speed mode"),
                     ({"pck_kcp_block": 'aes"\nmode = "x'}, "cipher")):
        with pytest.raises(ValueError, match=why):
            bo.pck_clean(bad)


def test_a_toml_cannot_be_broken_out_of_through_the_values():
    with pytest.raises(ValueError):
        bo.pck_texts("203.0.113.9", 9871, TOKEN, parse_ports("8080"), 39871, {"pck_kcp_block": 'none"\n[server]'})

    i, _f, _a, _b = _both("8080", {"preset": "balance"})
    assert set(tomllib.loads(_a)) == {"l3"} and i["preset"] == "balance"


def test_the_ports_of_an_existing_direct_pck_are_written_in_the_same_form():
    assert bo._pck_entries(parse_ports("8075, 443=8443"), 39871) == ["8075", "443=8443", "127.0.0.1:39871=39871"]
    assert bo._direct_entries(parse_ports("443=8443"), 39871, "10.10.12.2")[0] == "443=10.10.12.2:8443"          # (the carriers that keep a tunnel network are as before)


def test_the_direct_pck_is_the_first_carrier_of_the_form_and_the_label_stays():
    assert next(iter(bo.direct_carriers())) == "pck" and bo.BP_DIRECT["pck"][2] is True and bo.BP_DIRECT["pck"][3] is False          # (root: raw sockets; not a UDP carrier)
