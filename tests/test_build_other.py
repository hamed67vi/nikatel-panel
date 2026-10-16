"""The other tunnel engines: pure text builders, parsers, kinds, the validated actions, read-only refusal."""
import json

import pytest

from nikatel.services import build_other as bo
from nikatel.services import tunnel_test as tt
from nikatel.services.builder import parse_ports
from nikatel.ssh import render_action


def test_backpack_texts_have_test_door_and_mux():
    ports = parse_ports("443, 8080=2096")
    s, c = bo.backpack_texts("203.0.113.5", 9901, "tok", "tcpmux", ports, 39101)
    assert 'bind_addr = "0.0.0.0:9901"' in s and '"443",' in s and '"8080=127.0.0.1:2096",' in s and '"127.0.0.1:39101=39101",' in s
    assert "mux_con = 8" in s and 'remote_addr = "203.0.113.5:9901"' in c and "mux_version = 2" in c
    s2, c2 = bo.backpack_texts("203.0.113.5", 9901, "tok", "udp", ports, 39101)
    assert "accept_udp = true" in s2 and "mux_con" not in s2 and "connection_pool = 24" in c2


def test_direct_texts_and_fw():
    ports = parse_ports("8072")
    i, f = bo.direct_texts("203.0.113.5", 9850, "tok", "sni", 12, ports, 39600, {"sni_domain": "cdn.example.com"})
    assert 'mode = "dial"' in i and 'local_ip = "10.10.12.1/30"' in i and 'sni_domain = "cdn.example.com"' in i and "mtu = 1380" in i
    assert 'mode = "listen"' in f and 'peer_ip = "10.10.12.1"' in f and 'iface = "bp12"' in f
    fw = bo.bp_fw_script("fi-ir1", "bp12")
    assert "IF=bp12" in fw and "nikatel-bp-fi-ir1" in fw
    unit = bo.bp_unit("fi-ir1", "/etc/backpack/fi-ir1.toml", "Iran", "nikatel-bpfw-fi-ir1")
    assert "ExecStartPre=/usr/local/sbin/nikatel-bpfw-fi-ir1 up" in unit and "ExecStopPost=" in unit


def test_paqet_and_chisel_texts():
    ports = parse_ports("443=2096, 8080")
    net = {"IF": "eth0", "IP": "203.0.113.5", "MAC": "aa:bb:cc:dd:ee:ff"}
    i, f = bo.paqet_texts(net, net, 9700, "k", "203.0.113.9", ports, 39200, {"kcp_mode": "bogus", "accept_udp": True})
    assert 'role: "server"' in f and 'addr: ":9700"' in f and 'router_mac: "aa:bb:cc:dd:ee:ff"' in f and 'mode: "fast"' in f
    assert 'role: "client"' in i and 'listen: "0.0.0.0:443"' in i and 'target: "127.0.0.1:2096"' in i and 'protocol: "udp"' in i and 'listen: "127.0.0.1:39200"' in i
    assert "NOTRACK" in bo.paqet_fw_script("x", 9700) and "ExecStartPre=/usr/local/sbin/nikatel-paqet-fw-x up" in bo.paqet_unit("x", "foreign", "nikatel-paqet-fw-x")
    rem = bo.chisel_remotes(ports, 39000, False)
    assert rem == ["R:0.0.0.0:443:127.0.0.1:2096", "R:0.0.0.0:8080:127.0.0.1:8080", "R:127.0.0.1:39000:127.0.0.1:39000"]
    srv = bo.chisel_unit("x", "iran", 9600, rem, "203.0.113.1", True)
    cli = bo.chisel_unit("x", "foreign", 9600, rem, "203.0.113.1", True, "ABC=")
    assert "--reverse --keyfile /etc/nikatel-chisel/x.key" in srv and "--tls-cert" in srv and "EnvironmentFile=/etc/nikatel-chisel/x.srv.env" in srv
    assert "--fingerprint ABC= --tls-skip-verify https://203.0.113.1:9600 'R:0.0.0.0:443:127.0.0.1:2096'" in cli


def test_parsers_and_score():
    f = bo.parse_simple_facts("LISTEN=22 443\nLISTENU=53\nUNITS=backpack-a.service\nIPS=203.0.113.5/24\nARCH=x86_64\nID=0\nUFW=1\nPY=1\n")
    assert f["tcp"] == {22, 443} and f["udp"] == {53} and f["root"] and f["ufw"] and f["python"] and f["arch"] == "x86_64"
    assert bo.parse_net("IF=eth0\nIP=203.0.113.5\nGW=203.0.113.1\nMAC=aa:bb\n")["MAC"] == "aa:bb"

    with pytest.raises(RuntimeError):
        bo.parse_net("IF=eth0\nIP=\n")

    q = bo.parse_probe("noise\nNKPROBE " + json.dumps({"latency_ms": 60, "jitter_ms": 2, "loss_pct": 0, "down_mbps": 400, "up_mbps": 200}))
    assert 65 <= bo.quality_score(q) <= 85 and bo.quality_score({"error": "x"}) == 0
    assert bo.quality_score({"latency_ms": 20, "jitter_ms": 0, "loss_pct": 0, "down_mbps": 900, "up_mbps": 900}) == 100

    with pytest.raises(RuntimeError):
        bo.parse_probe("nothing")

    assert bo.free_port(9900, 9903, {9900, 9901}) == 9902 and bo.free_port(9900, 9902, {9900, 9901}) == 0


def test_kinds_and_actions():
    assert tt.kind_parts("tcpmux") == ("backhaul", "tcpmux") and tt.kind_parts("bp:stealth") == ("backpack", "stealth")
    assert tt.kind_parts("bpd:xdi") == ("backpack_direct", "xdi") and tt.kind_parts("paqet") == ("paqet", "kcp") and tt.kind_parts("chisel:tls") == ("chisel", "wss")
    assert {k[3] for k in tt.KINDS} == {"Backhaul over GRE", "BackPack", "BackPack Direct", "Paqet", "Chisel"}
    assert "nikatel-sink-fi-ir1.py 39101 abcdef0123456789 " in render_action("sink_install", {"NAME": "fi-ir1", "PORT": "39101", "KEY": "abcdef0123456789", "BIND": ""})
    rm = render_action("other_remove", {"NAME": "fi-ir1", "KIND": "backpack", "PORT": "9901", "PROTO": "udp"})
    assert "n='fi-ir1'" in rm and "backpack-$n-kharej.service" in rm and "pr='udp'" in rm

    with pytest.raises(ValueError):
        render_action("other_remove", {"NAME": "fi-ir1", "KIND": "backhaul", "PORT": "9901", "PROTO": "tcp"})

    with pytest.raises(ValueError):
        render_action("sink_install", {"NAME": "fi-ir1", "PORT": "39101", "KEY": "x; id", "BIND": ""})

    for p in ("/etc/paqet/x.yaml", "/etc/backpack/x.toml", "/etc/backpack/x-kharej.toml", "/etc/nikatel-chisel/x.srv.env", "/usr/local/sbin/nikatel-bpfw-x"):
        assert p in render_action("put_file", {"PATH": p, "B64": "aGk=", "MODE": "0600"})


def test_build_other_refused_in_read_only(client):
    with pytest.raises(ValueError):
        bo.start_build(1, 2, "8072", "backpack", "tcpmux")

    r = client.post("/api/tunnels", json={"iran_id": 1, "foreign_id": 2, "ports": "8072", "kind": "paqet"})
    assert r.status_code == 400 and "read-only" in r.json()["error"]
    assert client.post("/api/tunnels", json={"iran_id": 1, "foreign_id": 2, "ports": "8072", "kind": "rocket"}).status_code == 422
    kinds = client.get("/api/tunnels/kinds").json()["kinds"]
    assert [k["key"] for k in kinds] == ["backhaul", "backpack", "backpack_direct", "paqet", "chisel"] and len(kinds[1]["transports"]) == 12
    groups = {k["group"] for k in client.get("/api/tunnel-test/kinds").json()["kinds"]}
    assert "Paqet" in groups and "BackPack Direct" in groups


def test_forced_name_leftovers_are_removed_before_the_port_check(monkeypatch):
    from nikatel.services import build_other as bo

    calls = []
    monkeypatch.setattr(bo, "run_action", lambda node, action, params, timeout=0: calls.append((node, action, params["NAME"])) or "REMOVED")
    monkeypatch.setattr(bo, "run_script", lambda node, script, timeout=0: "fresh")
    monkeypatch.setattr(bo, "parse_simple_facts", lambda out: {"units": [], "tcp": set(), "udp": set(), "root": True, "python": True, "arch": "x86_64", "ips": []})
    monkeypatch.setattr(bo, "_say", lambda aid, line, done=None, ok=None: None)
    fi = {"units": ["backpack-nkt1.service", "backpack-other.service"], "tcp": {39400}, "udp": set(), "root": True, "python": True, "arch": "x86_64", "ips": []}
    ff = {"units": [], "tcp": set(), "udp": set(), "root": True, "python": True, "arch": "x86_64", "ips": []}
    b = bo.Build(aid=1, kind="backpack", iran=None, foreign=None, ports=[], transport="tcpmux", name="nkt1")
    fi2, ff2 = bo._clear_leftovers(b, "iran-node", "foreign-node", fi, ff)
    assert calls == [("iran-node", "other_remove", "nkt1")]              # only the side that had the leftover, only this name
    assert fi2["units"] == [] and fi2["tcp"] == set() and ff2 is ff        # the Iran facts were read again, the foreign ones kept
    auto = bo.Build(aid=1, kind="backpack", iran=None, foreign=None, ports=[], transport="tcpmux", name="")
    assert bo._clear_leftovers(auto, "i", "f", fi, ff) == (fi, ff) and len(calls) == 1      # no forced name: nothing is touched
    assert bo._owns("backpack-nkt1.service", "backpack", "nkt1") and bo._owns("backpack-nkt1-kharej.service", "backpack", "nkt1") and bo._owns("backpack-nkt1", "backpack", "nkt1")
    assert not bo._owns("backpack-nkt10.service", "backpack", "nkt1") and not bo._owns("chisel-nkt1.service", "backpack", "nkt1")

def test_a_paqet_tunnel_that_carries_nothing_says_why_from_what_the_firewall_counted(monkeypatch):
    """Real tests, 2026-10-16: Paqet passed between an Iran server and a Hetzner server and not between the same Iran server and another provider. The count of the raw port says which it is."""
    from nikatel.ssh import SCRIPTS, SSHError

    assert "Not one packet" in bo.paqet_diagnosis(0, 0, 9700) and "9700" in bo.paqet_diagnosis(0, 0, 9700) and "Another foreign server" in bo.paqet_diagnosis(0, 0, 9700)
    assert "sends nothing back" in bo.paqet_diagnosis(40, 0, 9700) and "key must be the same" in bo.paqet_diagnosis(40, 0, 9700) and "both ways" in bo.paqet_diagnosis(40, 12, 9700)

    class B:
        port = 9701

    monkeypatch.setattr(bo, "run_script", lambda node, name, params=None, timeout=40: "PAQET_IN=0 PAQET_OUT=0\n" if (name, params) == ("paqet_count", {"PORT": "9701"}) else "")
    assert bo._paqet_why(B(), None).startswith("Not one packet")
    monkeypatch.setattr(bo, "run_script", lambda *a, **k: "no counters here")
    assert bo._paqet_why(B(), None) == ""
    monkeypatch.setattr(bo, "run_script", lambda *a, **k: (_ for _ in ()).throw(SSHError("no route")))
    assert bo._paqet_why(B(), None) == ""
    script = SCRIPTS["paqet_count"][0]
    assert "iptables -t raw -L PREROUTING -v -n -x" in script and "nikatel-paqet" in script and 'echo "PAQET_IN=$IN PAQET_OUT=$OUT"' in script


def test_the_wait_for_data_puts_the_diagnosis_in_place_of_the_general_hint(monkeypatch):
    monkeypatch.setattr(bo.time, "sleep", lambda s: None)
    monkeypatch.setattr(bo, "_probe", lambda *a, **k: {"error": "timed out"})
    monkeypatch.setattr(bo, "run_script", lambda *a, **k: "")
    monkeypatch.setattr(bo, "_say", lambda *a, **k: None)

    class B:
        aid, test_port = 1, 39100

    with pytest.raises(RuntimeError, match=r"timed out\)\. Not one packet of the tunnel"):
        bo._wait_for_data(B(), None, "k", "the general hint", None, "unit", why=lambda: bo.paqet_diagnosis(0, 0, 9700))

    with pytest.raises(RuntimeError, match=r"timed out\)\. the general hint"):
        bo._wait_for_data(B(), None, "k", "the general hint", None, "unit", why=lambda: "")

    with pytest.raises(RuntimeError, match=r"timed out\)\. the general hint"):
        bo._wait_for_data(B(), None, "k", "the general hint", None, "unit")

def test_paqet_windows_buffers_and_cipher_are_options_and_stay_off_by_default():
    """The owner, 2026-10-16: "reach the best settings, especially Paqet": the KCP windows (packets) and the SMUX / stream buffers are what a 80 ms route needs more of; nothing changes unless they are asked."""
    ni, nf = {"IF": "eth0", "IP": "203.0.113.5", "MAC": "aa:bb:cc:dd:ee:01"}, {"IF": "eth0", "IP": "198.51.100.9", "MAC": "aa:bb:cc:dd:ee:02"}
    ports = parse_ports("8072")
    iran0, foreign0 = bo.paqet_texts(ni, nf, 9700, "k", "198.51.100.9", ports, 39200)
    assert "rcvwnd" not in iran0 and "smuxbuf" not in foreign0 and 'block: "aes-128-gcm"' in iran0 and "conn: 4" in iran0 and 'mode: "fast"' in iran0
    iran, foreign = bo.paqet_texts(ni, nf, 9700, "k", "198.51.100.9", ports, 39200, {"rcvwnd": 4096, "sndwnd": "2048", "smuxbuf": 16777216, "streambuf": 8388608, "conn": 8, "kcp_block": "none", "kcp_mode": "fast3"})
    for text in (iran, foreign):
        assert "    rcvwnd: 4096\n" in text and "    sndwnd: 2048\n" in text and "    smuxbuf: 16777216\n" in text and "    streambuf: 8388608\n" in text and 'block: "none"' in text and "conn: 8" in text and 'mode: "fast3"' in text
        assert text.index("mtu: 1350") < text.index("rcvwnd: 4096") < text.index('key: "k"')                      # (inside transport.kcp, before the key)

    wild, _ = bo.paqet_texts(ni, nf, 9700, "k", "198.51.100.9", ports, 39200, {"rcvwnd": 1, "smuxbuf": 10**12, "conn": 5000, "kcp_block": "rot13"})
    assert "rcvwnd: 128\n" in wild and "smuxbuf: 268435456\n" in wild and "conn: 64" in wild and 'block: "aes-128-gcm"' in wild            # clamped, an unknown cipher falls back
