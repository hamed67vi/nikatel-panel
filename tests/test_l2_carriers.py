"""The L2 (ethernet pseudowire) carriers of C-Tunnel (github.com/CIAUB/C-Tunnel), 1.69.x: L2TPv3 (IP protocol 115), VXLAN (UDP 4789), Geneve (UDP 6081) and GRE-TAP (protocol 47).

What is tested here needs no server: the registry and the kinds, the scripts of one end (the right `ip link add` / `ip l2tp` command, a firewall rule that names the exact peer and protocol or port, the interface deleted only when its
alias says it is ours, valid shell, the inner /30 on the interface), the mirror-derived L2TPv3 ids, that a build routes each link to its scripts and keeps the names the rest of the panel knows, and that the pair test and the Doctor
name them. A real build and a netns / real-pair proof are run on the servers (docs/l2-carriers.md)."""
import re

import pytest

from nikatel.services import builder, knowledge, linkmodels
from nikatel.services import tunnel_test as tt

IRAN, FOREIGN = "203.0.113.1", "198.51.100.2"
KEY = 1234567


def files(link, local=IRAN, remote=FOREIGN, name="l2-ir1", addr="10.9.8.1/30", mtu=1360, port=0):
    if link in linkmodels.PORTS and not port:
        port = linkmodels.PORTS[link][0]
    return builder.link_scripts(link, name, local, remote, KEY, addr, mtu, port=port)


UP = "/usr/local/sbin/nikatel-gre-up-l2-ir1"
DOWN = "/usr/local/sbin/nikatel-gre-down-l2-ir1"
UNIT = "/etc/systemd/system/nikatel-gre-l2-ir1.service"


# ------------------------------------------------------------------ the registry
def test_the_four_l2_carriers_are_registered_beside_the_nine_plain_models():
    assert linkmodels.L2_LINKS == ("l2tpv3", "vxlan", "geneve", "gretap") and len(linkmodels.LINKS) == 9
    assert linkmodels.ALL_LINKS == linkmodels.LINKS + linkmodels.L2_LINKS + linkmodels.CARRIER_LINKS
    assert not set(linkmodels.L2_LINKS) & linkmodels.ENCRYPTED                                   # none is encrypted by itself
    assert {k: linkmodels.needs(k) for k in linkmodels.L2_LINKS} == {"l2tpv3": "l2tp", "vxlan": "udp", "geneve": "udp", "gretap": "gre"}
    assert linkmodels.is_udp("vxlan") and linkmodels.is_udp("geneve") and not linkmodels.is_udp("l2tpv3") and not linkmodels.is_udp("gretap")
    assert linkmodels.PORTS["vxlan"][0] == 4789 and linkmodels.PORTS["geneve"][0] == 6081          # the standard ports are the base of the range
    assert {k: linkmodels.NAMES[k] for k in linkmodels.L2_LINKS} == {"l2tpv3": "L2TPv3", "vxlan": "VXLAN", "geneve": "Geneve", "gretap": "GRE-TAP"}
    assert linkmodels.firewall_match("gretap", 0) == "-p 47" and linkmodels.firewall_match("l2tpv3", 0) == "-p 115"
    assert linkmodels.firewall_match("vxlan", 4789) == "-p udp --dport 4789" and linkmodels.firewall_match("geneve", 6081) == "-p udp --dport 6081"
    assert linkmodels.kernel_of("l2tpv3") == ("l2tp_eth", "l2tp_ip") and linkmodels.kernel_of("vxlan") == ("vxlan",) and linkmodels.kernel_of("gretap") == ("ip_gre",)


def test_a_kind_of_an_l2_carrier_is_split_and_read_like_any_other():
    for link in linkmodels.L2_LINKS:
        assert linkmodels.split_kind(f"{link}:tcpmux") == (link, "tcpmux") and linkmodels.split_kind(f"{link}:wssmux") == (link, "wssmux")
        assert tt.kind_link(f"{link}:tcpmux") == link and tt.kind_parts(f"{link}:tcp") == ("backhaul", "tcp")
    assert not any(k in linkmodels.all_kinds() for k in ("l2tpv3:tcpmux", "vxlan:tcpmux"))          # the plain nine keep their own list


# ------------------------------------------------------------------ VXLAN / Geneve / GRE-TAP (one `ip link add type`)
def test_vxlan_geneve_gretap_each_make_their_interface_with_the_right_command_and_firewall():
    vx = files("vxlan")[UP]
    assert 'ip link add "$NAME" type vxlan id "$VNI" local "$LOCAL" remote "$REMOTE" dstport "$PORT" ttl "$TTL" nolearning' in vx
    assert "VNI=" in vx and "--dport 4789" in vx and "modprobe vxlan" in vx and 'alias "nikatel:$NAME"' in vx
    gn = files("geneve")[UP]
    assert 'ip link add "$NAME" type geneve id "$VNI" remote "$REMOTE" dstport "$PORT" ttl "$TTL"' in gn and "local" not in gn.split("type geneve", 1)[1].split("\n", 1)[0]
    assert "--dport 6081" in gn and "modprobe geneve" in gn
    gt = files("gretap")[UP]
    assert 'ip link add "$NAME" type gretap local "$LOCAL" remote "$REMOTE" key "$KEY" ttl "$TTL"' in gt and "-p 47" in gt and "modprobe ip_gre" in gt
    for link in ("vxlan", "geneve", "gretap"):
        up = files(link)[UP]
        assert 'ip addr add "$ADDR" dev "$NAME"' in up and 'ip link set "$NAME" up' in up and "ADDR=10.9.8.1/30" in up and "MTU=1360" in up


def test_the_l2_down_script_deletes_only_an_interface_whose_alias_is_ours():
    for link in ("vxlan", "geneve", "gretap"):
        d = files(link)[DOWN]
        assert 'grep -q "nikatel:$NAME"' in d and 'ip link del "$NAME"' in d and d.rstrip().endswith("exit 0") and '--comment $TAG' in d


def test_the_vni_is_the_low_24_bits_of_the_key_and_the_same_both_ends():
    a = files("vxlan", local=IRAN, remote=FOREIGN)[UP]
    b = files("vxlan", local=FOREIGN, remote=IRAN, addr="10.9.8.2/30")[UP]
    vni = re.search(r"VNI=(\d+)", a).group(1)
    assert vni == re.search(r"VNI=(\d+)", b).group(1) == str(KEY % 0x1000000)          # symmetric: both ends share the VNI


# ------------------------------------------------------------------ L2TPv3 (ip l2tp, IP protocol 115)
def test_l2tpv3_makes_a_static_pseudowire_over_ip_and_names_the_interface():
    up = files("l2tpv3")[UP]
    assert "ip l2tp add tunnel tunnel_id " in up and "encap ip" in up and 'ip l2tp add session name "$NAME" tunnel_id ' in up
    assert "modprobe l2tp_eth" in up and "modprobe l2tp_ip" in up and "-p 115" in up and 'ip addr add "$ADDR" dev "$NAME"' in up and 'ip link set "$NAME" mtu "$MTU" up' in up
    assert "TID=" in up and "PTID=" in up and "SID=" in up and "PSID=" in up
    d = files("l2tpv3")[DOWN]
    assert "ip l2tp del session tunnel_id" in d and "ip l2tp del tunnel tunnel_id" in d and 'grep -q "nikatel:$NAME"' in d and d.rstrip().endswith("exit 0")


def test_the_l2tpv3_ids_mirror_between_the_two_ends_and_are_distinct_and_unique():
    low = linkmodels.l2_ids(KEY, IRAN, FOREIGN)          # IRAN < FOREIGN numerically
    high = linkmodels.l2_ids(KEY, FOREIGN, IRAN)
    tid, ptid, sid, psid = low
    assert high == (ptid, tid, psid, sid)                # the other end is the mirror
    assert tid != ptid and sid != psid and 0 < tid < 2**31 and 0 < ptid < 2**31       # distinct and inside the kernel's range
    # the two ends' up scripts carry the swapped ids
    a = files("l2tpv3", local=IRAN, remote=FOREIGN)[UP]
    b = files("l2tpv3", local=FOREIGN, remote=IRAN, addr="10.9.8.2/30")[UP]
    assert f"TID={tid}" in a and f"PTID={ptid}" in a and f"TID={ptid}" in b and f"PTID={tid}" in b


def test_two_l2tpv3_links_of_one_server_get_different_tunnel_ids():
    assert linkmodels.l2_ids(1, IRAN, FOREIGN)[0] != linkmodels.l2_ids(2, IRAN, FOREIGN)[0]


# ------------------------------------------------------------------ a build routes each link and keeps the names the rest of the panel knows
def test_a_build_keeps_the_gre_names_the_check_the_units_and_renames_the_unit():
    for link in linkmodels.L2_LINKS:
        f = files(link)
        base = set(builder.link_scripts("gre", "l2-ir1", IRAN, FOREIGN, KEY, "10.9.8.1/30", 1360))
        assert base <= set(f) and UP in f and DOWN in f and UNIT in f         # the check script, the units and the timer ARE those of a GRE link
        assert f"Nikatel {linkmodels.NAMES[link]} link" in f[UNIT]
        assert "/usr/local/sbin/nikatel-gre-up-$NAME" in f["/usr/local/sbin/nikatel-gre-check-l2-ir1"]    # the GRE ping health check


def test_bad_input_is_refused():
    for bad in ({"name": "BAD NAME"}, {"local": "not-an-ip"}, {"mtu": 100}):
        with pytest.raises(ValueError):
            files("vxlan", **bad)
    with pytest.raises(ValueError):
        builder.link_scripts("vxlan", "l2-ir1", IRAN, FOREIGN, KEY, "10.9.8.1/30", 1360, port=99)      # a port outside the VXLAN range


# ------------------------------------------------------------------ the pair test and the Doctor know them
def test_the_pair_test_and_the_doctor_name_the_l2_carriers():
    for link in linkmodels.L2_LINKS:
        key = f"{link}:tcpmux"
        assert key in tt.KIND_KEYS and key in knowledge.PRIOR and key in knowledge.READY and key in knowledge.CUT_LINKS and key in knowledge.LINK_KINDS
        for tr in ("tcp", "wsmux", "wssmux"):
            assert f"{link}:{tr}" in tt.GRID_ONLY            # the other transports are only in the grid (the owner tests them by hand)
    assert set(linkmodels.L2_LINKS) <= set(builder.FALLBACK_LINKS) and "vxlan:tcpmux" not in builder.FALLBACK_LINKS      # FALLBACK_LINKS holds link names, not kinds
