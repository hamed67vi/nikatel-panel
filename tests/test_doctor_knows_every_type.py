"""Every tunnel type the panel can build is taught to the Doctor (the owner, 2026-11-02: "every new type we add to the panel, the Doctor must take it into account in its tests - for example the ones we added last night").

A type is added in `tunnel_test.KINDS` (and in the builder); the Doctor has its own lists - the standard order, the tiers of the night survey, who dials, the link fall-backs, what Trident knows of a link. Forgetting one of them used
to be silent: the Doctor simply never tried the type. These tests fail, with the name of the type and of the list, the day a type is added and not taught."""
from nikatel.services import builder, knowledge, linkmodels, trident, tunnel_test

HOW = "teach the Doctor the new type (services/knowledge.py: PRIOR, READY, LINK_KINDS, FORWARD_KINDS / REVERSE_KINDS; builder.FALLBACK_LINKS; trident._LINK_FAMILY / _OVER_LINK)"


def problems(kinds, links):
    """Pure: what the Doctor does not know of these types and link models - a list of sentences (empty when it knows them all)."""
    out = []
    variants = {f"{k.split(':', 1)[0]}:{tr}" for k in knowledge.LINK_KINDS for tr in knowledge.VARIANT_TRANSPORTS}
    carried = {f"{link}:{tr}" for link in linkmodels.CARRIER_LINKS for tr in linkmodels.TRANSPORTS}

    for k in kinds:
        sides = [k in knowledge.FORWARD_KINDS or k in knowledge.CARRIED_FORWARD, k in knowledge.REVERSE_KINDS or k in knowledge.CARRIED_REVERSE, k in knowledge.RAW_KINDS]

        if sum(sides) != 1:
            out.append(f"{k}: it must stand in exactly one of FORWARD_KINDS (the Iran server dials) / REVERSE_KINDS (the foreign server dials) / RAW_KINDS (a kernel link), it stands in {sum(sides)}")

        if k not in knowledge.PRIOR and k not in variants and k not in carried:
            out.append(f"{k}: the Doctor never tries it (not in PRIOR, not a transport of a link in LINK_KINDS, not a carried link)")

    for link in links:
        if link == "gre" or link in linkmodels.CARRIER_LINKS:
            continue

        kind = f"{link}:tcpmux"

        for name, lst in (("PRIOR", knowledge.PRIOR), ("READY (the night survey's first tier)", knowledge.READY), ("LINK_KINDS", knowledge.LINK_KINDS), ("CUT_LINKS", knowledge.CUT_LINKS)):
            if kind not in lst:
                out.append(f"{kind}: missing in {name}")

        if link not in builder.FALLBACK_LINKS:
            out.append(f"{link}: missing in builder.FALLBACK_LINKS")

        if link not in trident._LINK_FAMILY or link not in trident._OVER_LINK:
            out.append(f"{link}: Trident does not know its family or its overhead (trident._LINK_FAMILY / _OVER_LINK)")

    return out


def test_the_doctor_knows_every_type_and_every_link_model_the_panel_can_build():
    assert problems([k[0] for k in tunnel_test.KINDS], linkmodels.ALL_LINKS) == [], HOW


def test_the_four_l2_carriers_added_on_the_night_of_2026_11_01_are_in_every_list():
    for link in ("l2tpv3", "gretap", "vxlan", "geneve"):
        assert f"{link}:tcpmux" in knowledge.READY and f"{link}:tcpmux" in knowledge.CUT_LINKS and link in builder.FALLBACK_LINKS
        assert knowledge.dial_side(f"{link}:tcpmux") == "" and all(knowledge.dial_side(f"{link}:{tr}") == "" for tr in knowledge.VARIANT_TRANSPORTS)      # a kernel link: nobody dials across the cut


def test_the_variants_of_a_link_and_the_carried_links_have_a_side_too():
    assert knowledge.dial_side("vxlan:wssmux") == "" and knowledge.dial_side("ipsec:wsmux") == ""                                                              # (the third tier of the survey: a link that passed, with another transport)
    assert knowledge.dial_side("wgchisel:tcpmux") == "foreign" and knowledge.dial_side("wgchiselfwd:tcpmux") == "iran"                                      # WireGuard in Chisel: whoever opens the TCP connection of the carrier
    assert all(knowledge.dial_side(k) == "foreign" for k in knowledge.REVERSE_KINDS | knowledge.CARRIED_REVERSE) and all(knowledge.dial_side(k) == "iran" for k in knowledge.FORWARD_KINDS | knowledge.CARRIED_FORWARD)


def test_a_type_added_to_the_panel_and_not_taught_to_the_doctor_is_named():
    said = problems([k[0] for k in tunnel_test.KINDS] + ["newlink:tcpmux", "newlink:tcp"], list(linkmodels.ALL_LINKS) + ["newlink"])
    assert any(s.startswith("newlink:tcpmux: it must stand in exactly one of") for s in said)                                                                    # (nobody says where it dials)
    assert any(s.startswith("newlink:tcpmux: the Doctor never tries it") for s in said)
    assert "newlink:tcpmux: missing in READY (the night survey's first tier)" in said and "newlink: missing in builder.FALLBACK_LINKS" in said
    assert any(s.startswith("newlink: Trident does not know") for s in said)
