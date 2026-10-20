"""Every Backhaul transport over every link model can be tested (the owner, 2026-10-20: "test the kinds of GRE with the Backhaul tunnel").

What is tested: the 9 links x 4 transports are all kinds of the pair test, each one is read back as (builder kind, transport, link) correctly, the old keys and groups are what they were, and the night survey's own list
is not widened by it."""
from nikatel.services import builder, knowledge, linkmodels
from nikatel.services import tunnel_test as tt

TRANSPORTS = ("tcpmux", "tcp", "wsmux", "wssmux")


def test_the_whole_grid_of_links_and_transports_is_a_list_of_test_kinds():
    keys = {k[0] for k in tt.KINDS}
    grid = {(t if link == "gre" else f"{link}:{t}") for link in linkmodels.LINKS for t in TRANSPORTS}
    assert len(grid) == 36 and grid <= keys
    assert len([k for k in tt.KINDS if k[0] in grid]) == 36                                     # (no key twice)
    assert len(tt.KINDS) == len({k[0] for k in tt.KINDS})


def test_every_kind_of_the_grid_is_read_back_as_its_link_and_its_transport():
    for link in linkmodels.LINKS:
        for tr in TRANSPORTS:
            key = tr if link == "gre" else f"{link}:{tr}"
            assert tt.kind_parts(key) == ("backhaul", tr) and tt.kind_link(key) == link, key


def test_the_new_kinds_are_in_the_group_of_the_other_links_and_are_not_a_default():
    old = {"wg:tcpmux", "wg:tcp", "fou:tcpmux", "gue:tcpmux", "ipip:tcpmux", "ipipfou:tcpmux", "ipipgue:tcpmux", "ipsec:tcpmux", "ipsecnat:tcpmux"}
    new = [k for k in tt.KINDS if k[3] == "Backhaul over another link" and k[0] not in old]
    assert len(new) == 8 * 4 - len(old)
    assert all(k[4] is False and "Backhaul" in k[1] and linkmodels.TITLE[k[0].split(":")[0]] in k[1] for k in new)
    assert {k[3] for k in tt.KINDS} == {"Backhaul over GRE", "Backhaul over another link", "BackPack", "BackPack Direct", "Paqet", "Chisel"}


def test_the_night_survey_keeps_its_own_short_list():
    assert not any(k in knowledge.READY for k in ("fou:wsmux", "ipsec:wssmux", "gue:tcp"))             # (the long grid is tested when the owner asks)


def test_the_kinds_only_the_grid_adds_are_not_in_the_standard_order_of_a_repair():
    assert len(tt.GRID_ONLY) == 23 and tt.GRID_ONLY <= tt.KIND_KEYS
    assert not set(knowledge.PRIOR) & tt.GRID_ONLY and set(knowledge.PRIOR) == tt.KIND_KEYS - tt.GRID_ONLY      # (a cut tunnel is never repaired by walking 23 more kinds)


def test_the_message_about_ipip_says_the_standby_may_hold_it():
    import inspect

    from nikatel.services import linkprobe

    assert "warm standby" in inspect.getsource(builder._prepare_link) and "warm standby" in inspect.getsource(linkprobe)
