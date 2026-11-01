"""Every Backhaul transport over every link model can be tested (the owner, 2026-10-20: "test the kinds of GRE with the Backhaul tunnel").

What is tested: the 9 links x 4 transports are all kinds of the pair test, each one is read back as (builder kind, transport, link) correctly, the old keys and groups are what they were, and the night survey's own list
is not widened by it."""
from nikatel.services import builder, knowledge, linkmodels
from nikatel.services import tunnel_test as tt

TRANSPORTS = ("tcpmux", "tcp", "wsmux", "wssmux")


def test_the_whole_grid_of_links_and_transports_is_a_list_of_test_kinds():
    keys = {k[0] for k in tt.KINDS}
    grid = {(t if link == "gre" else f"{link}:{t}") for link in linkmodels.LINKS + linkmodels.L2_LINKS for t in TRANSPORTS}         # the nine plain models and the four L2 carriers (C-Tunnel)
    assert len(grid) == 13 * 4 and grid <= keys
    assert len([k for k in tt.KINDS if k[0] in grid]) == 13 * 4                                     # (no key twice)
    assert len(tt.KINDS) == len({k[0] for k in tt.KINDS})


def test_every_kind_of_the_grid_is_read_back_as_its_link_and_its_transport():
    for link in linkmodels.LINKS + linkmodels.L2_LINKS:
        for tr in TRANSPORTS:
            key = tr if link == "gre" else f"{link}:{tr}"
            assert tt.kind_parts(key) == ("backhaul", tr) and tt.kind_link(key) == link, key


def test_the_l2_carriers_are_kinds_in_the_group_of_the_other_links_and_are_not_a_default():
    for link in linkmodels.L2_LINKS:          # l2tpv3, vxlan, geneve, gretap (C-Tunnel's kernel L2 carriers)
        for tr in TRANSPORTS:
            row = next((k for k in tt.KINDS if k[0] == f"{link}:{tr}"), None)
            assert row is not None and row[3] == "Backhaul over another link" and row[4] is False
            assert "Backhaul" in row[1] and linkmodels.NAMES[link] in row[1]
    assert {k[3] for k in tt.KINDS} == {"Backhaul over GRE", "Backhaul over another link", "BackPack", "BackPack Direct", "Paqet", "Chisel"}


def test_the_night_survey_keeps_its_own_short_list():
    assert not any(k in knowledge.READY for k in ("fou:wsmux", "ipsec:wssmux", "gue:tcp", "l2tpv3:wssmux", "vxlan:tcp"))             # (the long grid is tested when the owner asks)
    assert "l2tpv3:tcpmux" in knowledge.READY and "gretap:tcpmux" in knowledge.READY                                               # (the L2 carriers' TCP Mux is surveyed per pair)


def test_the_kinds_only_the_grid_adds_are_not_in_the_standard_order_of_a_repair():
    # 23 (the eight non-GRE plain links x the 3 extra transports, less wg:tcp which is a default) + 12 (the four L2 carriers x the 3 extra transports) + 8 (the two carried links x 4 transports)
    assert len(tt.GRID_ONLY) == 23 + 12 + 8 and tt.GRID_ONLY <= tt.KIND_KEYS
    assert not set(knowledge.PRIOR) & tt.GRID_ONLY and set(knowledge.PRIOR) == tt.KIND_KEYS - tt.GRID_ONLY      # (a cut tunnel is never repaired by walking the grid)


def test_the_message_about_ipip_says_the_standby_may_hold_it():
    import inspect

    from nikatel.services import linkprobe

    assert "warm standby" in inspect.getsource(builder._prepare_link) and "warm standby" in inspect.getsource(linkprobe)
