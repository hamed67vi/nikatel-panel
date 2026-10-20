"""The link under Backhaul: GRE (as always), GRE inside UDP and WireGuard (the owner, 2026-10-17: "I want WireGuard added", after the review of simple-tun-link - github.com/ach1992/simple-tun-link).

What is tested: the scripts of an end (names compatible with a GRE link, the key of WireGuard never in a script, the firewall rule that names the peer and the UDP port, a down script that deletes only its own kind of link),
the facts a server must give, the choice of the UDP port, the options, the kinds the Doctor knows, the way the API turns "wg:tcpmux" into a build over WireGuard, and what a build of the Doctor may and may not do
(it never installs a package; the owner's own build may)."""
import shutil
import subprocess

import pytest

from nikatel import ssh
from nikatel.services import autopilot as ap
from nikatel.services import builder, knowledge, tunnel_test

PUB_I = "A" * 43 + "="
PUB_F = "B" * 43 + "="


def files(link, **kw):
    args = {"name": "ca-ir1", "local": "203.0.113.10", "remote": "198.51.100.20", "key": 1234567, "addr_cidr": "10.9.8.1/30", "mtu": 1360, "port": 51830, "peer_pub": PUB_F}
    args.update(kw)
    return builder.link_scripts(link, **args)


# ------------------------------------------------------------------ the scripts of an end
def test_a_gre_link_is_exactly_what_it_was_and_the_other_links_keep_its_names():
    gre = files("gre")
    assert gre == builder.gre_scripts("ca-ir1", "203.0.113.10", "198.51.100.20", 1234567, "10.9.8.1/30", 1360)
    wg, fou = files("wg"), files("fou", port=5510)
    assert set(wg) == set(gre) == set(fou)                                                                             # the same six files: the units, the timer and the check keep the names every other part of the panel knows
    assert fou["/usr/local/sbin/nikatel-gre-check-ca-ir1"] == gre["/usr/local/sbin/nikatel-gre-check-ca-ir1"]      # (the check pings the inner peer and restarts the unit after three silences: the same for every link ...)
    chk = wg["/usr/local/sbin/nikatel-gre-check-ca-ir1"]
    assert chk != gre["/usr/local/sbin/nikatel-gre-check-ca-ir1"] and "PEER=10.9.8.2" in chk and "latest-handshakes" in chk      # ... but WireGuard's also reads the age of the handshake (1.69.44, test_wireguard_complete.py)
    assert "Nikatel WireGuard link" in wg["/etc/systemd/system/nikatel-gre-ca-ir1.service"] and "Nikatel GRE over UDP (FOU) link" in fou["/etc/systemd/system/nikatel-gre-ca-ir1.service"]


def test_a_wireguard_end_names_the_peer_and_the_port_and_never_holds_a_private_key():
    up = files("wg")["/usr/local/sbin/nikatel-gre-up-ca-ir1"]
    assert "ip link add \"$NAME\" type wireguard" in up and f"PEER_PUB={PUB_F}" in up and "KEY=/etc/nikatel/wg/ca-ir1.key" in up
    assert 'wg set "$NAME" listen-port "$PORT" private-key "$KEY" peer "$PEER_PUB" endpoint "$REMOTE:$PORT" allowed-ips "$PEER_IP/32" persistent-keepalive 25' in up      # the key is a FILE on the server, not an argument
    assert "PORT=51830" in up and "PEER_IP=10.9.8.2" in up and "ADDR=10.9.8.1/30" in up and "MTU=1360" in up and "LOCAL=203.0.113.10" in up and "REMOTE=198.51.100.20" in up
    assert '-s "$REMOTE" -d "$LOCAL" -p udp --dport 51830' in up and 'alias "nikatel:$NAME"' in up                 # a firewall rule for exactly this peer and port; the interface is tagged as ours
    assert "genkey" not in up and "private-key \"$KEY\"" in up and PUB_I not in up                                # (only the OTHER end's public key is in the script)
    down = files("wg")["/usr/local/sbin/nikatel-gre-down-ca-ir1"]
    assert 'grep -q wireguard; then ip link del "$NAME"' in down and "ip tunnel del" not in down and "--comment $TAG" in down       # it deletes a link only when it IS a WireGuard link, and takes only its own tagged rules away
    assert "/etc/nikatel/wg" not in down                                                                           # (a restart of the unit must not lose the key: the key goes with the tunnel, in remove_tunnel_files)


def test_a_gre_over_udp_end_opens_its_port_and_the_encapsulation():
    up = files("fou", port=5510)["/usr/local/sbin/nikatel-gre-up-ca-ir1"]
    assert 'ip fou add port "$PORT" ipproto 47' in up and 'type gre local "$LOCAL" remote "$REMOTE" key "$KEY" ttl "$TTL" encap fou encap-sport auto encap-dport "$PORT"' in up
    assert "PORT=5510" in up and "KEY=1234567" in up and '-p udp --dport 5510' in up and 'alias "nikatel:$NAME"' in up
    down = files("fou", port=5510)["/usr/local/sbin/nikatel-gre-down-ca-ir1"]
    assert 'ip fou del port "$PORT"' in down and "grep -q 'gre '" in down


def test_every_link_script_is_valid_shell():
    sh = shutil.which("bash") or shutil.which("sh")

    if sh is None:
        pytest.skip("no shell on this machine")

    for link, kw in (("gre", {}), ("wg", {}), ("fou", {"port": 5510})):
        for path, text in files(link, **kw).items():
            if "/sbin/" in path:
                r = subprocess.run([sh, "-n"], input=text, capture_output=True, text=True, timeout=20, check=False)
                assert r.returncode == 0, f"{link} {path}: {r.stderr}"


def test_a_link_refuses_what_it_would_run_as_root_if_it_were_not_checked():
    for bad in ({"name": "x;reboot"}, {"name": "UPPER"}, {"local": "203.0.113.4;id"}, {"remote": "$(id)"}, {"port": 80}, {"port": 99999}):
        with pytest.raises(ValueError):
            files("wg", **bad)

    for bad in ({"peer_pub": ""}, {"peer_pub": "short"}, {"peer_pub": "A" * 43 + "=;id"}):
        with pytest.raises(ValueError):
            files("wg", **bad)

    with pytest.raises(ValueError):
        files("fou", port=51830)                                                                                   # (a GRE-over-UDP port has its own range)

    with pytest.raises(ValueError):
        files("ipsec")


# ------------------------------------------------------------------ what a server says, the port, the key
def test_the_facts_of_a_server_are_read_and_the_port_is_one_nobody_holds():
    f = builder.parse_link_facts("WG_MOD=1\nWG_TOOL=0\nFOU_MOD=1\nUDP=53 51820 51821 \nFOU_PORTS=5500 \nAPT=1\nMODS=ip_gre fou ipip \nIPIP_PAIRS=203.0.113.1>198.51.100.2 \nnoise\n")
    assert f == {"wg_mod": True, "wg_tool": False, "fou_mod": True, "udp": {53, 51820, 51821}, "fou_ports": {5500}, "apt": True, "mods": {"ip_gre", "fou", "ipip"}, "ipip_pairs": {("203.0.113.1", "198.51.100.2")}, "tcp": set(), "chisel": False}
    assert builder.parse_link_facts("") == {"wg_mod": False, "wg_tool": False, "fou_mod": False, "udp": set(), "fou_ports": set(), "apt": False, "mods": set(), "ipip_pairs": set(), "tcp": set(), "chisel": False}
    assert builder.pick_link_port("wg", set()) == 51820 and builder.pick_link_port("wg", {51820, 51821}) == 51822 and builder.pick_link_port("fou", {5500}) == 5501
    assert builder.pick_link_port("wg", set(range(51820, 51920))) == 0                                              # a full range: none
    assert builder.parse_pub(f"PUB={PUB_I}\n") == PUB_I and builder.parse_pub("PUB=short") == "" and builder.parse_pub("") == ""


def test_the_options_of_a_link_are_checked():
    assert builder.clean_bh_opts({"link": "wg"}) == {"link": "wg"} and builder.clean_bh_opts({"link": "fou"}) == {"link": "fou"}
    assert builder.clean_bh_opts({"link": "gre"}) == {} and builder.clean_bh_opts({"link": ""}) == {}                  # GRE is the default: nothing is written for it
    assert builder.clean_bh_opts({"install_wg": True}) == {"install_wg": True}
    assert builder.clean_bh_opts({"avoid_net": "10.4.5.0/30"}) == {"avoid_net": "10.4.5.0/30"} and builder.clean_bh_opts({"avoid_net": "10.4.5.0/30; id"}) == {}      # (a fresh-link rebuild keeps the network to avoid: it was dropped here)
    assert builder.clean_bh_opts({"link": "ipsec"}) == {"link": "ipsec"} and builder.clean_bh_opts({"link": "gue"}) == {"link": "gue"}          # (all nine models are valid; the six that are not GRE are new in 1.69.0)

    with pytest.raises(ValueError):
        builder.clean_bh_opts({"link": "sit"})


# ------------------------------------------------------------------ the reviewed scripts of the servers
def test_the_reviewed_scripts_for_a_link_take_only_what_their_rules_allow():
    assert "modprobe wireguard" in ssh._script_text("link_facts", {}) and "ip fou show" in ssh._script_text("link_facts", {})
    k = ssh.render_action("wg_keygen", {"NAME": "ca-ir1"})
    assert 'wg genkey > "$k"' in k and "/etc/nikatel/wg" in k and 'echo "PUB=$(wg pubkey < "$k")"' in k and "cat " not in k          # the key is written and the public half printed: the private one is never printed
    for bad in ("x;reboot", "UPPER", "", "a" * 16):
        with pytest.raises(ValueError):
            ssh.render_action("wg_keygen", {"NAME": bad})

    i = ssh.render_action("wg_install", {})
    assert "apt-get install -y -qq wireguard-tools" in i and "WG_FAIL" in i
    r = ssh.render_action("remove_tunnel_files", {"NAME": "ca-ir1", "PORT": "9901", "TRANSPORT": "tcpmux"})
    assert 'rm -f "/etc/nikatel/wg/$n.key" "/etc/nikatel/ipsec/$n.keys"' in r and "grep -qE 'wireguard|gre |ipip|xfrm'" in r      # (the keys go with the tunnel; a WireGuard / IPIP / xfrm link is deleted with `ip link`)


# ------------------------------------------------------------------ the kinds the Doctor knows
def test_the_pair_test_knows_the_links_and_a_kind_says_which_link_it_runs_over():
    for key in ("wg:tcpmux", "wg:tcp", "fou:tcpmux"):
        assert key in tunnel_test.KIND_KEYS and key in knowledge.PRIOR and key in knowledge.CUT_LINKS and key in knowledge.UDP_LINK_KINDS
        assert tunnel_test.kind_parts(key)[0] == "backhaul"

    assert tunnel_test.kind_parts("wg:tcpmux") == ("backhaul", "tcpmux") and tunnel_test.kind_parts("fou:tcpmux") == ("backhaul", "tcpmux") and tunnel_test.kind_parts("wg:tcp") == ("backhaul", "tcp")
    assert tunnel_test.kind_link("wg:tcpmux") == "wg" and tunnel_test.kind_link("fou:tcpmux") == "fou" and tunnel_test.kind_link("tcpmux") == "gre" and tunnel_test.kind_link("bp:tcpmux") == "gre"
    assert tunnel_test.kind_parts("bp:tcpmux") == ("backpack", "tcpmux")                                           # (the other kinds are as they were)
    assert ap.kind_of("backhaul", "tcpmux", {}) == "tcpmux" and ap.kind_of("backhaul", "tcpmux", {"link": "wg"}) == "wg:tcpmux" and ap.kind_of("backhaul", "tcp", {"link": "fou"}) == "fou:tcp"
    assert ap.kind_of("backhaul", "tcpmux", {"link": "gre"}) == "tcpmux" and ap.kind_of("paqet", "kcp", {"link": "wg"}) == "paqet"
    assert "wg:tcpmux" in knowledge.READY and "fou:tcpmux" in knowledge.READY                                      # the night survey learns whether a pair passes them


def test_a_build_of_a_kind_runs_over_the_link_of_that_kind_whatever_the_old_options_said(monkeypatch):
    seen = []
    monkeypatch.setattr(ap.builder, "start_build", lambda iran_id, foreign_id, ports, transport, mtu=0, keepalive=75, forced_name="", nodelay=True, opts=None: seen.append((transport, dict(opts or {}))) or 5)
    snap = {"name": "t1", "ports": "8075"}
    ap._build_kind(snap, 1, 2, "wg:tcpmux", {"tune": True})
    ap._build_kind(snap, 1, 2, "tcpmux", {"tune": True, "link": "wg"})                                              # a switch from a WireGuard tunnel to plain GRE: no link left over
    ap._build_kind(snap, 1, 2, "fou:tcpmux", {"link": "wg"})
    assert seen == [("tcpmux", {"tune": True, "link": "wg"}), ("tcpmux", {"tune": True}), ("tcpmux", {"link": "fou"})]


def test_when_the_link_under_a_tunnel_is_cut_its_own_kinds_are_not_its_replacement_but_the_other_links_are():
    ir, fr = 801, 802
    kinds, _ = knowledge.plan_for(ir, fr, "wg:tcpmux", True, False, dial_i=True, dial_f=True, cause="gre_blocked")
    assert "wg:tcpmux" not in kinds and "wg:tcp" not in kinds                                                         # the WireGuard link is the one that is cut: its other transport would be cut the same way
    assert "fou:tcpmux" in kinds and "tcpmux" in kinds and "ipsec:tcpmux" in kinds                                   # GRE inside UDP on another port, native GRE, ESP ... may pass (1.69.0: it was every UDP link and GRE that were left out)
    kinds, _ = knowledge.plan_for(ir, fr, "tcpmux", True, False, dial_i=True, dial_f=True, cause="gre_blocked")
    assert "wg:tcpmux" in kinds and "fou:tcpmux" in kinds and "ipip:tcpmux" in kinds and "tcpmux" not in kinds       # a GRE tunnel cut by a protocol-47 filter: the other links may pass
    assert kinds.index("wg:tcpmux") > kinds.index("bp:wssmux")                                                     # (last in the static order; a pair whose survey found one connecting has it first)
    kinds, _ = knowledge.plan_for(ir, fr, "wg:tcpmux", True, False, dial_i=True, dial_f=True, cause="route_cut")
    assert "tcpmux" not in kinds                                                                                   # the two servers cannot reach each other at all: GRE cannot pass either


def test_the_diagnosis_says_udp_for_a_udp_link():
    facts = {"state": "down", "gre": True, "gre_loss": None, "under_loss_i": 0.0, "under_loss_f": 0.0, "flap_fails": 0}
    assert ap.classify(facts) == ("gre_blocked", "plain ping between the servers passes both ways (0.0% / 0.0% loss) but GRE packets do not (inner link None% loss): GRE (protocol 47) is filtered on this route")
    cause, why = ap.classify({**facts, "link": "wg"})
    assert cause == "gre_blocked" and "the WireGuard link does not" in why and "UDP port of the link is filtered" in why and "protocol 47" not in why
    cause, why = ap.classify({**facts, "link": "fou", "under_loss_i": 0.0})
    assert "GRE over UDP (FOU) link" in why and "UDP port of the link is filtered" in why
    cause, why = ap.classify({**facts, "link": "ipip"})
    assert cause == "gre_blocked" and "IPIP link does not" in why and "IPIP (protocol 4) is filtered" in why and "UDP port" not in why        # a link that is not in a UDP port: what is filtered is its IP protocol
    cause, why = ap.classify({**facts, "link": "ipsec"})
    assert "IPsec (ESP) link does not" in why and "ESP (protocol 50) is filtered" in why


# ------------------------------------------------------------------ the API and what a build may do
def test_the_api_turns_a_wireguard_transport_into_a_build_over_wireguard(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    seen = []
    monkeypatch.setattr(builder, "start_build", lambda iran_id, foreign_id, ports, transport, mtu=0, keepalive=75, forced_name="", nodelay=True, opts=None: seen.append((transport, dict(opts or {}))) or 7)
    body = {"iran_id": 1, "foreign_id": 2, "ports": "8075", "kind": "backhaul"}
    assert client.post("/api/tunnels", json={**body, "transport": "wg:tcpmux"}).json() == {"ok": True, "id": 7}
    assert client.post("/api/tunnels", json={**body, "transport": "fou:tcpmux"}).status_code == 202
    assert client.post("/api/tunnels", json={**body, "transport": "tcpmux"}).status_code == 202
    assert seen == [("tcpmux", {"link": "wg", "install_wg": True, "link_fallback": True}), ("tcpmux", {"link": "fou", "link_fallback": True}), ("tcpmux", {"link_fallback": True})]       # the owner asked for WireGuard: wireguard-tools may be installed; GRE over UDP needs no package
    assert client.post("/api/tunnels", json={**body, "transport": "ipsec:tcpmux"}).status_code == 202               # (1.69.0: ESP, GUE, IPIP ... are links too)
    assert seen[-1] == ("tcpmux", {"link": "ipsec", "link_fallback": True})
    assert client.post("/api/tunnels", json={**body, "transport": "sit:tcpmux"}).status_code == 400 and client.post("/api/tunnels", json={**body, "transport": "gre:tcpmux"}).status_code == 400
    kinds = client.get("/api/tunnels/kinds").json()["kinds"]
    assert {"wg:tcpmux", "wg:tcp", "fou:tcpmux", "gue:tcpmux", "ipip:tcpmux", "ipipfou:tcpmux", "ipipgue:tcpmux", "ipsec:tcpmux", "ipsecnat:tcpmux"} <= {t["key"] for k in kinds if k["key"] == "backhaul" for t in k["transports"]}


def test_a_build_the_doctor_makes_never_installs_a_package_but_the_owners_may(monkeypatch):
    ran = []
    monkeypatch.setattr(builder, "_say", lambda *a, **k: None)
    monkeypatch.setattr(builder, "run_action", lambda node, name, params=None, timeout=60: ran.append(name) or "WG_OK installed\n")
    node = builder.Node("203.0.113.1", "root", 22)
    builder._ensure_wg_tool(1, node, {"wg_tool": True}, "Iran", "ir1", False)
    assert ran == []                                                                                               # present: nothing to do
    with pytest.raises(RuntimeError, match="wireguard-tools is not installed"):
        builder._ensure_wg_tool(1, node, {"wg_tool": False}, "Iran", "ir1", False)

    assert ran == []                                                                                               # (the Doctor, the survey: no package is installed on a server by itself)
    builder._ensure_wg_tool(1, node, {"wg_tool": False}, "Iran", "ir1", True)
    assert ran == ["wg_install"]
    monkeypatch.setattr(builder, "run_action", lambda node, name, params=None, timeout=60: "WG_FAIL apt could not install wireguard-tools: no route\n")

    with pytest.raises(RuntimeError, match="could not be installed .*no route"):
        builder._ensure_wg_tool(1, node, {"wg_tool": False}, "Foreign", "fr1", True)


class FakeBuild:
    def __init__(self, opts):
        from nikatel.models import Server

        self.aid, self.opts, self.name, self.created = 1, opts, "t1", []
        self.iran, self.foreign = Server(id=1, name="ir1", host="203.0.113.1"), Server(id=2, name="fr1", host="198.51.100.2")


def test_a_wireguard_link_is_prepared_on_both_servers_with_a_free_port_and_a_key_each(monkeypatch):
    facts = {"203.0.113.1": "WG_MOD=1\nWG_TOOL=1\nFOU_MOD=1\nUDP=51820\nFOU_PORTS=\nAPT=1\n", "198.51.100.2": "WG_MOD=1\nWG_TOOL=1\nFOU_MOD=1\nUDP=51821\nFOU_PORTS=\nAPT=1\n"}
    monkeypatch.setattr(builder, "_say", lambda *a, **k: None)
    monkeypatch.setattr(builder, "run_script", lambda node, name, params=None, timeout=40: facts[node.host])
    keys = {"203.0.113.1": PUB_I, "198.51.100.2": PUB_F}
    monkeypatch.setattr(builder, "run_action", lambda node, name, params=None, timeout=60: f"PUB={keys[node.host]}\n")
    b = FakeBuild({"link": "wg"})
    port, pubs = builder._prepare_link(b, builder.Node("203.0.113.1", "root", 22), builder.Node("198.51.100.2", "root", 22), "wg")
    assert port == 51822 and pubs == {"iran": PUB_I, "foreign": PUB_F}                                             # 51820 and 51821 are held on one server each
    assert [w for w, _ in b.created] == ["wg", "wg"]                                                               # (both servers are in the rollback list before a key is made)

    facts["198.51.100.2"] = "WG_MOD=0\nWG_TOOL=1\nFOU_MOD=1\nUDP=\nFOU_PORTS=\nAPT=1\n"

    with pytest.raises(RuntimeError, match="cannot load the WireGuard kernel module"):
        builder._prepare_link(FakeBuild({"link": "wg"}), builder.Node("203.0.113.1", "root", 22), builder.Node("198.51.100.2", "root", 22), "wg")

    facts["198.51.100.2"] = "WG_MOD=1\nWG_TOOL=1\nFOU_MOD=0\nUDP=\nFOU_PORTS=\nAPT=1\n"

    with pytest.raises(RuntimeError, match="cannot load the FOU kernel module"):
        builder._prepare_link(FakeBuild({"link": "fou"}), builder.Node("203.0.113.1", "root", 22), builder.Node("198.51.100.2", "root", 22), "fou")
