"""The nine link models (1.69.0: the owner, 2026-10-17: "this project has nine GRE models: add them all and test which is better"): GRE, GRE over FOU, GRE over GUE, IPIP, IPIP over FOU, IPIP over GUE, WireGuard, IPsec (ESP) and
IPsec inside UDP (NAT-T). What is tested here is everything that does not need two servers: the registry, the kinds, the scripts of an end (the right kernel commands, nothing secret in a command line, a firewall rule that
names the exact peer, valid shell), what the build checks first, and what the Doctor does with the kinds."""
import itertools
import shutil
import subprocess

import pytest

from nikatel.services import autopilot as ap
from nikatel.services import builder, knowledge, linkmodels, tunnel_test

KEY_A = "a1" * 20
KEY_B = "b2" * 20
IPSEC = {"spi_tx": 4000000001, "spi_rx": 4000000002, "key_tx": KEY_A, "key_rx": KEY_B}


def files(link, **kw):
    args = {"name": "ca-ir1", "local": "203.0.113.10", "remote": "198.51.100.20", "key": 1234567, "addr_cidr": "10.9.8.1/30", "mtu": 1360, "port": linkmodels.PORTS.get(link, (0, 0))[0] + 5, "peer_pub": "B" * 43 + "="}
    args.update(kw)

    if link in ("ipsec", "ipsecnat"):
        args.setdefault("ipsec", IPSEC)

    return builder.link_scripts(link, **args)


def up(link, **kw):
    return files(link, **kw)["/usr/local/sbin/nikatel-gre-up-ca-ir1"]


def down(link, **kw):
    return files(link, **kw)["/usr/local/sbin/nikatel-gre-down-ca-ir1"]


# ------------------------------------------------------------------ the registry and the kinds
def test_there_are_nine_models_and_every_one_says_what_must_pass_between_the_servers():
    assert linkmodels.LINKS == ("gre", "fou", "gue", "ipip", "ipipfou", "ipipgue", "wg", "ipsec", "ipsecnat") and len(linkmodels.LINKS) == 9
    assert linkmodels.UDP_LINKS == ("fou", "gue", "ipipfou", "ipipgue", "wg", "ipsecnat") and linkmodels.RAW_LINKS == ("gre", "ipip", "ipsec")
    assert [linkmodels.needs(k) for k in linkmodels.LINKS] == ["gre", "udp", "udp", "ipip", "udp", "udp", "udp", "esp", "udp"]
    assert linkmodels.ENCRYPTED == {"wg", "ipsec", "ipsecnat"} and linkmodels.ONE_PER_PAIR == {"ipip", "ipipfou", "ipipgue"}
    ranges = sorted(linkmodels.PORTS.values())
    assert all(a[1] < b[0] for a, b in itertools.pairwise(ranges))                                          # no two models share a UDP port range
    assert set(linkmodels.TITLE) == set(linkmodels.LINKS) == set(linkmodels.OVERHEAD) == set(linkmodels.KERNEL)


def test_a_kind_is_the_link_and_the_transport_and_gre_keeps_the_bare_transport():
    assert linkmodels.split_kind("fou:tcpmux") == ("fou", "tcpmux") and linkmodels.split_kind("ipsecnat:wssmux") == ("ipsecnat", "wssmux") and linkmodels.split_kind("wg:tcp") == ("wg", "tcp")
    assert linkmodels.split_kind("tcpmux") == ("gre", "tcpmux") and linkmodels.split_kind("bp:tcpmux") == ("", "") and linkmodels.split_kind("chisel:fwd") == ("", "")
    assert linkmodels.split_kind("gre:tcpmux") == ("", "") and linkmodels.split_kind("ipip:ws") == ("", "") and linkmodels.split_kind("paqet") == ("", "")
    assert linkmodels.make_kind("gre", "tcpmux") == "tcpmux" and linkmodels.make_kind("gue", "tcp") == "gue:tcp"
    assert linkmodels.all_kinds() == ["tcpmux", "fou:tcpmux", "gue:tcpmux", "ipip:tcpmux", "ipipfou:tcpmux", "ipipgue:tcpmux", "wg:tcpmux", "ipsec:tcpmux", "ipsecnat:tcpmux"]


def test_the_pair_test_the_survey_and_the_doctor_know_every_link():
    for kind in linkmodels.all_kinds():
        assert kind in tunnel_test.KIND_KEYS and kind in knowledge.PRIOR and (kind in knowledge.READY or kind == "tcpmux"), kind
        assert tunnel_test.kind_parts(kind) == ("backhaul", "tcpmux")
        assert ap.kind_of("backhaul", "tcpmux", {"link": linkmodels.split_kind(kind)[0]}) == kind

    assert set(knowledge.LINK_KINDS) - {"wg:tcp"} == set(linkmodels.all_kinds()[1:]) and set(knowledge.LINK_KINDS) <= set(knowledge.CUT_LINKS)
    assert {tunnel_test.kind_link(k) for k in linkmodels.all_kinds()} == set(linkmodels.LINKS)
    assert set(knowledge.UDP_LINK_KINDS) == {"fou:tcpmux", "gue:tcpmux", "wg:tcpmux", "ipipfou:tcpmux", "ipipgue:tcpmux", "ipsecnat:tcpmux", "wg:tcp"}            # the ones that need a UDP port, not a raw protocol
    assert {k[3] for k in tunnel_test.KINDS if k[0] in knowledge.LINK_KINDS} == {"Backhaul over another link"}


# ------------------------------------------------------------------ GRE / IPIP, native or inside UDP
def test_every_model_keeps_the_names_of_a_gre_link_so_that_nothing_else_had_to_change():
    base = set(files("gre"))

    for link in linkmodels.LINKS:
        got = files(link)
        assert base <= set(got), link                                                                              # the check script, the units and the timer are those of every link
        assert (got["/usr/local/sbin/nikatel-gre-check-ca-ir1"] == files("gre")["/usr/local/sbin/nikatel-gre-check-ca-ir1"]) == (link != "wg")      # (WireGuard's check also reads the age of the handshake: test_wireguard_complete.py)
        assert f"Nikatel {linkmodels.TITLE[link]} link" in got["/etc/systemd/system/nikatel-gre-ca-ir1.service"] or link == "gre"

    assert set(files("ipsec")) - base == {"/etc/nikatel/ipsec/ca-ir1.keys"} and set(files("wg")) == base == set(files("fou"))


def test_gre_over_gue_is_the_same_gre_in_a_gue_header_on_its_own_port():
    u = up("gue")
    assert 'ip fou add port "$PORT" gue' in u and 'ipproto' not in u and 'type gre local "$LOCAL" remote "$REMOTE" key "$KEY" ttl "$TTL" encap gue encap-sport auto encap-dport "$PORT"' in u
    assert "PORT=5605" in u and "KEY=1234567" in u and "-p udp --dport 5605" in u and 'alias "nikatel:$NAME"' in u and "modprobe ip_gre" in u and "modprobe fou" in u
    d = down("gue")
    assert 'ip fou del port "$PORT"' in d and "grep -q 'gre '" in d and "ip tunnel del" not in d


def test_ipip_has_no_key_and_no_udp_port():
    u = up("ipip")
    assert 'type ipip local "$LOCAL" remote "$REMOTE" ttl "$TTL" ||' in u and 'key "$KEY"' not in u and "ip fou" not in u and "encap" not in u
    assert "modprobe ipip" in u and "-p 4 " in u and "--dport" not in u and "-p udp" not in u                       # the firewall rule names protocol 4
    d = down("ipip")
    assert "grep -q 'ipip'" in d and "ip fou" not in d


def test_ipip_inside_udp_runs_over_fou_or_gue():
    f = up("ipipfou")
    assert 'ip fou add port "$PORT" ipproto 4' in f and 'type ipip local "$LOCAL" remote "$REMOTE" ttl "$TTL" encap fou encap-sport auto encap-dport "$PORT"' in f and "-p udp --dport 5705" in f
    g = up("ipipgue")
    assert 'ip fou add port "$PORT" gue' in g and 'type ipip local "$LOCAL" remote "$REMOTE" ttl "$TTL" encap gue encap-sport auto encap-dport "$PORT"' in g and "-p udp --dport 5805" in g
    assert 'ip fou del port "$PORT"' in down("ipipfou") and 'ip fou del port "$PORT"' in down("ipipgue")


# ------------------------------------------------------------------ IPsec
def test_an_ipsec_end_uses_an_xfrm_interface_and_one_sa_per_direction_and_no_key_in_a_command_line():
    fs = files("ipsec")
    u = fs["/usr/local/sbin/nikatel-gre-up-ca-ir1"]
    assert 'ip link add "$NAME" type xfrm dev "$DEV" if_id "$IFID"' in u and "IFID=1234567" in u and "SPI_TX=ee6b2801" in u and "SPI_RX=ee6b2802" in u
    assert "xfrm state add src $LOCAL dst $REMOTE proto esp spi 0x$SPI_TX reqid $IFID mode tunnel aead rfc4106(gcm(aes)) 0x$KEY_TX 128 if_id $IFID replay-window 128 flag esn" in u
    assert "xfrm state add src $REMOTE dst $LOCAL proto esp spi 0x$SPI_RX reqid $IFID mode tunnel aead rfc4106(gcm(aes)) 0x$KEY_RX 128 if_id $IFID replay-window 128 flag esn" in u
    assert "ip -batch - <<EOF" in u and u.count("dir fwd") == 2 and "encap espinudp" not in u and "-p 50 " in u and "--dport" not in u         # keys through stdin, never argv; no UDP
    assert KEY_A not in u and KEY_B not in u and ". \"$KEYS\"" in u and "KEYS=/etc/nikatel/ipsec/ca-ir1.keys" in u                                    # the keys are in a FILE, not in the script
    assert fs["/etc/nikatel/ipsec/ca-ir1.keys"] == f"KEY_TX={KEY_A}\nKEY_RX={KEY_B}\n"
    d = fs["/usr/local/sbin/nikatel-gre-down-ca-ir1"]
    assert "grep -q 'xfrm'" in d and d.count("ip xfrm policy del src 0.0.0.0/0 dst 0.0.0.0/0 dir") == 3 and "ip xfrm state del" in d and "/etc/nikatel/ipsec" not in d and "nikatel-ipsec-sock" not in d


def test_ipsec_in_udp_opens_the_socket_that_takes_esp_out_of_udp():
    fs = files("ipsecnat", port=4512)
    u = fs["/usr/local/sbin/nikatel-gre-up-ca-ir1"]
    assert "PORT=4512" in u and "encap espinudp $PORT $PORT 0.0.0.0" in u and u.count("encap espinudp") == 2 and "-p udp --dport 4512" in u
    assert "systemd-run" in u and "nikatel-ipsec-sock-$NAME" in u and "setsockopt(socket.IPPROTO_UDP, 100, 2)" in u and 'bind((sys.argv[1], int(sys.argv[2])))' in u
    assert 'systemctl stop "nikatel-ipsec-sock-$NAME.service"' in fs["/usr/local/sbin/nikatel-gre-down-ca-ir1"]


def test_the_directions_swap_between_the_two_ends():
    a, b = files("ipsec", ipsec=IPSEC)["/etc/nikatel/ipsec/ca-ir1.keys"], files("ipsec", ipsec={"spi_tx": 4000000002, "spi_rx": 4000000001, "key_tx": KEY_B, "key_rx": KEY_A})["/etc/nikatel/ipsec/ca-ir1.keys"]
    assert a == f"KEY_TX={KEY_A}\nKEY_RX={KEY_B}\n" and b == f"KEY_TX={KEY_B}\nKEY_RX={KEY_A}\n"


# ------------------------------------------------------------------ valid shell, and nothing unchecked reaches a server
def test_every_script_of_every_model_is_valid_shell():
    sh = shutil.which("bash") or shutil.which("sh")

    if sh is None:
        pytest.skip("no shell on this machine")

    for link in linkmodels.LINKS:
        for path, text in files(link).items():
            if "/sbin/" in path:
                r = subprocess.run([sh, "-n"], input=text, capture_output=True, text=True, timeout=20, check=False)
                assert r.returncode == 0, f"{link} {path}: {r.stderr}"


def test_every_file_of_every_model_is_accepted_by_the_action_that_writes_it_to_a_server():
    """Found by the first live run of the link probe (2026-10-17): the IPsec key file was refused by `put_file` ("bad value for PATH") - the scripts were tested, the road to the server was not. Every file the builder
    and the probe write goes through the reviewed `put_file` action with the mode the builder gives it."""
    from nikatel import ssh as nssh

    for link in linkmodels.LINKS:
        for path in files(link):
            mode = "0700" if "/sbin/" in path else "0600" if path.endswith(".keys") else "0644"
            nssh.render_action("put_file", {"PATH": path, "B64": "", "MODE": mode})                    # raises ValueError when the path or the mode is not allowed

    assert "/etc/nikatel/ipsec/ca-ir1.keys" in files("ipsec") and "/etc/nikatel/ipsec/ca-ir1.keys" in files("ipsecnat")

    for bad in ("/etc/nikatel/ipsec/../shadow", "/etc/nikatel/ipsec/a/b.keys", "/etc/nikatel/other/x.keys", "/etc/nikatel/ipsec/"):
        with pytest.raises(ValueError):
            nssh.render_action("put_file", {"PATH": bad, "B64": "", "MODE": "0600"})


def test_a_link_refuses_what_it_would_run_as_root_if_it_were_not_checked():
    for link in ("gue", "ipip", "ipipfou", "ipipgue", "ipsec", "ipsecnat"):
        for bad in ({"name": "x;reboot"}, {"name": "UPPER"}, {"local": "203.0.113.4;id"}, {"remote": "$(id)"}, {"mtu": 100}):
            with pytest.raises(ValueError):
                files(link, **bad)

    for link in ("gue", "ipipfou", "ipipgue", "ipsecnat"):
        for bad in (80, 99999, 51830):                                                                             # (every UDP model has its own range)
            with pytest.raises(ValueError):
                files(link, port=bad)

    with pytest.raises(ValueError):
        files("gue", port=5505)                                                                                    # (5505 is the FOU range)

    for bad in ({"key_tx": "short"}, {"key_rx": "zz" * 20}, {"key_tx": KEY_A + "00"}, {"spi_tx": 10}, {"spi_rx": 2**32}):
        with pytest.raises(ValueError):
            files("ipsec", ipsec={**IPSEC, **bad})

    with pytest.raises(ValueError):
        files("ipsec", ipsec=None)

    with pytest.raises(ValueError):
        builder.link_scripts("sit", "ca-ir1", "203.0.113.10", "198.51.100.20", 1, "10.9.8.1/30", 1360)


# ------------------------------------------------------------------ what a build checks first
class FakeBuild:
    def __init__(self, opts):
        from nikatel.models import Server

        self.aid, self.opts, self.name, self.created = 1, opts, "t1", []
        self.iran, self.foreign = Server(id=1, name="ir1", host="203.0.113.1"), Server(id=2, name="fr1", host="198.51.100.2")


def prepare(monkeypatch, link, facts_i, facts_f):
    facts = {"203.0.113.1": facts_i, "198.51.100.2": facts_f}
    monkeypatch.setattr(builder, "_say", lambda *a, **k: None)
    monkeypatch.setattr(builder, "run_script", lambda node, name, params=None, timeout=40: facts[node.host])
    return builder._prepare_link(FakeBuild({"link": link}), builder.Node("203.0.113.1", "root", 22), builder.Node("198.51.100.2", "root", 22), link)


ALL_MODS = "WG_MOD=1\nWG_TOOL=1\nFOU_MOD=1\nUDP=53 5500\nFOU_PORTS=5600\nAPT=1\nMODS=ip_gre ipip fou xfrm_interface esp4 wireguard\nIPIP_PAIRS=\n"


def test_a_build_picks_a_port_in_the_range_of_the_model_that_nobody_holds_and_raw_links_have_none(monkeypatch):
    assert prepare(monkeypatch, "gue", ALL_MODS, ALL_MODS) == (5601, {})                                           # 5600 is held by a socket
    assert prepare(monkeypatch, "ipipfou", ALL_MODS, ALL_MODS)[0] == 5700 and prepare(monkeypatch, "ipsecnat", ALL_MODS, ALL_MODS)[0] == 4500
    port, pubs = prepare(monkeypatch, "ipsec", ALL_MODS, ALL_MODS)
    assert port == 0 and set(pubs) == {"key_a", "key_b", "spi_a", "spi_b"} and len(pubs["key_a"]) == 40 and len(pubs["key_b"]) == 40 and pubs["key_a"] != pubs["key_b"]
    assert 256 <= int(pubs["spi_a"]) < 2**32 and prepare(monkeypatch, "ipip", ALL_MODS, ALL_MODS) == (0, {})


def test_a_build_refuses_a_server_that_lacks_a_kernel_module_and_a_second_ipip_between_the_same_two_servers(monkeypatch):
    no_esp = ALL_MODS.replace(" esp4", "")

    with pytest.raises(RuntimeError, match=r"fr1 cannot load the esp4 kernel module \(IPsec \(ESP\)\)"):
        prepare(monkeypatch, "ipsec", ALL_MODS, no_esp)

    with pytest.raises(RuntimeError, match=r"ir1 cannot load the xfrm_interface kernel module"):
        prepare(monkeypatch, "ipsecnat", ALL_MODS.replace(" xfrm_interface", ""), ALL_MODS)

    with pytest.raises(RuntimeError, match=r"cannot load the ipip kernel module"):
        prepare(monkeypatch, "ipip", ALL_MODS.replace(" ipip", ""), ALL_MODS)

    with pytest.raises(RuntimeError, match="cannot load the FOU kernel module"):
        prepare(monkeypatch, "ipipgue", ALL_MODS.replace("FOU_MOD=1", "FOU_MOD=0"), ALL_MODS)

    held = ALL_MODS.replace("IPIP_PAIRS=", "IPIP_PAIRS=198.51.100.2>203.0.113.1 ")

    for link in ("ipip", "ipipfou", "ipipgue"):
        with pytest.raises(RuntimeError, match="IPIP has no key"):
            prepare(monkeypatch, link, held, ALL_MODS)

    assert prepare(monkeypatch, "fou", held, ALL_MODS)[0] == 5501 and prepare(monkeypatch, "gue", held, ALL_MODS)[0] == 5601          # (GRE keeps its key: many links between the same two servers)


def test_the_raw_protocol_links_name_the_protocol_in_the_firewall_and_the_udp_ones_the_port():
    assert linkmodels.firewall_match("ipip", 0) == "-p 4" and linkmodels.firewall_match("ipsec", 0) == "-p 50" and linkmodels.firewall_match("gue", 5610) == "-p udp --dport 5610"


# ------------------------------------------------------------------ the Doctor
def test_a_cut_link_gives_up_its_own_kinds_and_tries_the_rest_in_the_order_of_the_static_list():
    kinds, _ = knowledge.plan_for(811, 812, "ipsec:tcpmux", True, False, dial_i=True, dial_f=True, cause="gre_blocked")
    assert "ipsec:tcpmux" not in kinds and {"tcpmux", "fou:tcpmux", "gue:tcpmux", "ipip:tcpmux", "wg:tcpmux", "ipsecnat:tcpmux"} <= set(kinds)
    kinds, _ = knowledge.plan_for(811, 812, "gue:tcpmux", True, False, dial_i=False, dial_f=True, cause="gre_blocked")
    assert "gue:tcpmux" not in kinds and kinds.index("bp:wssmux") < kinds.index("fou:tcpmux")                       # the foreign server dials in: the robust reverse kinds first, the links after them


def test_a_survey_that_found_the_links_that_pass_on_a_pair_tries_them_first():
    pair = {"kinds": {}}
    now = 1_800_000_000

    for kind, ok in (("ipsecnat:tcpmux", True), ("ipip:tcpmux", False), ("wg:tcpmux", False), ("fou:tcpmux", True)):
        pair["kinds"][kind] = knowledge.merge(None, {"kind": kind, "ok": ok, "score": 70 if ok else None, "latency_ms": 90, "loss_pct": 0, "error": "" if ok else "no answer"}, "latency", now)

    got = knowledge.candidates(pair, "tcpmux", True, False, now, limit=len(knowledge.PRIOR), base=knowledge.cut_order(True, True, True))
    assert got[:2] == ["ipsecnat:tcpmux", "fou:tcpmux"]                                                            # what is known to connect here goes first (the same score: the standard order decides - the measured one)
    assert "ipip:tcpmux" not in got and "wg:tcpmux" not in got                                                    # and what failed here lately is not tried at all while something else is left


def test_the_measured_order_of_the_links_puts_the_raw_protocols_first_and_the_udp_ones_that_were_slow_last():
    """Measured on the test route (2026-10-17, Iran server -> foreign server, 4 TCP streams): IPIP 1307 Mb/s, IPIP in FOU 1228, IPsec ESP 1055, IPIP in GUE 43, IPsec in UDP 30, WireGuard 19, GRE in FOU 2.4, GRE in GUE 2.0."""
    assert list(knowledge.LINK_KINDS[:3]) == ["ipip:tcpmux", "ipipfou:tcpmux", "ipsec:tcpmux"] and list(knowledge.LINK_KINDS[-3:]) == ["fou:tcpmux", "gue:tcpmux", "wg:tcp"]
    assert knowledge.cut_order(True, True, True)[-len(knowledge.CUT_LINKS):] == list(knowledge.LINK_KINDS)


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("timeout") is None, reason="needs bash and timeout")
def test_the_tcp_ping_script_runs_for_real_and_answers_ten_of_ten_on_a_local_port():
    """The "ping" of a tunnel whose provider drops ICMP: ten handshakes to a port, the cost of starting a handshake (a connect to a closed local port) taken off - so a port next door reads about 0 ms."""
    import socket
    import threading

    from nikatel import ssh as nssh

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(64)
    srv.settimeout(0.2)
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                srv.accept()[0].close()
            except OSError:
                pass

    threading.Thread(target=accept, daemon=True).start()

    try:
        out = subprocess.run(["bash", "-s"], check=False, input=nssh._script_text("tcp_ping", {"HOST": "127.0.0.1", "PORT": str(srv.getsockname()[1])}), capture_output=True, text=True, timeout=60).stdout
    finally:
        stop.set()
        srv.close()

    line = next(ln for ln in out.splitlines() if ln.startswith("TPING"))
    sent, answered, avg = (int(x) for x in line.split()[2:5])
    assert (sent, answered) == (10, 10) and 0 <= avg <= 40

@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a shell")
@pytest.mark.parametrize("link", ["ipsec", "ipsecnat"])
def test_the_ipsec_up_script_run_again_leaves_the_sas_alone(link, tmp_path):
    """Found on the test panel (2026-10-17, the transitions run): the health-check timer runs the up script every minute, and the script took the SAs away and added them again - their sequence numbers start at 0
    and the other end drops everything as a replay until it passes its old count: a tunnel that carried a download for a few seconds and then nothing. The script is run for real here with stand-ins for `ip`,
    `iptables` and the rest: the second run must not add (or delete) anything."""
    bin_dir, keys_dir = tmp_path / "bin", tmp_path / "keys"
    bin_dir.mkdir()
    keys_dir.mkdir()
    ip = r"""#!/bin/sh
D="${STUB_DIR}"
echo "ip $*" >> "$D/log"
case "$1" in
  -o) echo "198.51.100.20 dev eth0 src 203.0.113.10 uid 0"; exit 0;;
  -batch) while read -r line; do echo "batch: $line" >> "$D/log"; case "$line" in "xfrm state add"*) echo "$line" | sed -E 's/.* spi (0x[0-9a-f]+) .*/src a dst b\n\tproto esp spi \1 reqid 1 mode tunnel/' >> "$D/states";; esac; done; exit 0;;
  link) case "$2" in show) [ -f "$D/link" ]; exit $?;; add) touch "$D/link"; exit 0;; esac; exit 0;;
  xfrm) if [ "$2" = state ] && [ -z "$3" ]; then cat "$D/states" 2>/dev/null; fi; exit 0;;
esac
exit 0
"""
    (bin_dir / "ip").write_text(ip)

    for tool in ("iptables", "sysctl", "modprobe", "logger", "systemctl", "systemd-run"):
        (bin_dir / tool).write_text("#!/bin/sh\n[ \"$1\" = is-active ] && exit 1\nexit 0\n")

    for p in bin_dir.iterdir():
        p.chmod(0o755)

    out = files(link, ipsec=IPSEC)
    (keys_dir / "ca-ir1.keys").write_text(out["/etc/nikatel/ipsec/ca-ir1.keys"])
    script = tmp_path / "up.sh"
    script.write_text(out["/usr/local/sbin/nikatel-gre-up-ca-ir1"].replace("/etc/nikatel/ipsec/", f"{keys_dir}/"))
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_DIR": str(tmp_path)}

    for _ in range(3):
        r = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, timeout=30, check=False)
        assert r.returncode == 0, r.stderr

    log = (tmp_path / "log").read_text()
    assert log.count("batch: xfrm state add") == 2 and log.count("batch: xfrm policy add") == 3                      # two SAs and three policies, added by the first run only
    assert log.count("xfrm state del") == 2                                                                         # (the first run clears what an older run of the same link might have left)