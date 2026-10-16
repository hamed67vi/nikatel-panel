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

def test_paqet_windows_and_buffers_default_to_what_the_real_tests_found_best_and_are_options():
    """The owner, 2026-10-16: "reach the best settings, especially Paqet": on a 114 ms route the window of 1024 packets and the 2 MB stream buffer held one connection at 52 Mb/s;
    window 4096 with 16 MB / 8 MB gave 200 (and four connections 330 instead of 184). Written when nothing is asked, any of them can be changed."""
    ni, nf = {"IF": "eth0", "IP": "203.0.113.5", "MAC": "aa:bb:cc:dd:ee:01"}, {"IF": "eth0", "IP": "198.51.100.9", "MAC": "aa:bb:cc:dd:ee:02"}
    ports = parse_ports("8072")
    iran0, foreign0 = bo.paqet_texts(ni, nf, 9700, "k", "198.51.100.9", ports, 39200)

    for text in (iran0, foreign0):
        assert "    rcvwnd: 4096\n" in text and "    sndwnd: 4096\n" in text and "    smuxbuf: 16777216\n" in text and "    streambuf: 8388608\n" in text
        assert text.index("mtu: 1350") < text.index("rcvwnd: 4096") < text.index('key: "k"')

    assert 'block: "aes-128-gcm"' in iran0 and "conn: 4" in iran0 and 'mode: "fast"' in iran0
    assert bo.PAQET_DEFAULTS == {"rcvwnd": 4096, "sndwnd": 4096, "smuxbuf": 16777216, "streambuf": 8388608}
    iran1, _ = bo.paqet_texts(ni, nf, 9700, "k", "198.51.100.9", ports, 39200, {"rcvwnd": 0, "smuxbuf": "", "streambuf": None})
    assert "    rcvwnd: 4096\n" in iran1 and "    smuxbuf: 16777216\n" in iran1 and "    streambuf: 8388608\n" in iran1            # (an empty field of the form is "not asked")
    iran, foreign = bo.paqet_texts(ni, nf, 9700, "k", "198.51.100.9", ports, 39200, {"rcvwnd": 4096, "sndwnd": "2048", "smuxbuf": 16777216, "streambuf": 8388608, "conn": 8, "kcp_block": "none", "kcp_mode": "fast3"})
    for text in (iran, foreign):
        assert "    rcvwnd: 4096\n" in text and "    sndwnd: 2048\n" in text and "    smuxbuf: 16777216\n" in text and "    streambuf: 8388608\n" in text and 'block: "none"' in text and "conn: 8" in text and 'mode: "fast3"' in text
        assert text.index("mtu: 1350") < text.index("rcvwnd: 4096") < text.index('key: "k"')                      # (inside transport.kcp, before the key)

    wild, _ = bo.paqet_texts(ni, nf, 9700, "k", "198.51.100.9", ports, 39200, {"rcvwnd": 1, "smuxbuf": 10**12, "conn": 5000, "kcp_block": "rot13"})
    assert "rcvwnd: 128\n" in wild and "smuxbuf: 268435456\n" in wild and "conn: 64" in wild and 'block: "aes-128-gcm"' in wild            # clamped, an unknown cipher falls back


def test_the_paqet_mtu_stays_where_a_packet_still_fits():
    """Real tests, 2026-10-16: 1200 and 1350 carried data, 1450 carried nothing at all."""
    ni = {"IF": "eth0", "IP": "203.0.113.5", "MAC": "aa:bb:cc:dd:ee:01"}
    ports = parse_ports("8072")

    for asked, written in ((None, 1350), (1200, 1200), (1400, 1400), (1450, 1400), (9000, 1400), (100, 500)):
        iran, foreign = bo.paqet_texts(ni, ni, 9700, "k", "198.51.100.9", ports, 39200, {} if asked is None else {"mtu": asked})
        assert f"mtu: {written}\n" in iran and f"mtu: {written}\n" in foreign

    assert bo.PAQET_MAX_MTU == 1400


def test_a_forward_chisel_tunnel_is_dialled_by_the_iran_server():
    """The owner, 2026-10-16: the route throttles the data that flows away from the side that opened the connection (a reverse tunnel gave a customer about 10 Mb/s down,
    a forward one 109 Mb/s on one connection and 621 on eight). Forward = the foreign server is the Chisel server, the Iran server the client with its own listeners."""
    assert bo.chisel_mode("ws") == (False, False) and bo.chisel_mode("wss") == (True, False) and bo.chisel_mode("fws") == (False, True) and bo.chisel_mode("fwss") == (True, True)
    assert bo.CHISEL_TRANSPORTS == ("ws", "wss", "fws", "fwss")
    ports = parse_ports("443=2096")
    rem = bo.chisel_remotes(ports, 39000, False, True)
    assert rem == ["0.0.0.0:443:127.0.0.1:2096", "127.0.0.1:39000:127.0.0.1:39000"]                        # (no R: - the client opens the ports itself)
    assert bo.chisel_remotes(ports, 39000, True, True)[1] == "0.0.0.0:443:127.0.0.1:2096/udp"
    srv = bo.chisel_unit("x", "foreign", 9600, rem, "198.51.100.9", True, forward=True)
    cli = bo.chisel_unit("x", "iran", 9600, rem, "198.51.100.9", True, "ABC=", forward=True)
    assert " server --host 0.0.0.0 --port 9600 --keyfile /etc/nikatel-chisel/x.key" in srv and "--reverse" not in srv and "--tls-cert" in srv and "EnvironmentFile=/etc/nikatel-chisel/x.srv.env" in srv
    assert " client " in cli and "--fingerprint ABC= --tls-skip-verify https://198.51.100.9:9600 '0.0.0.0:443:127.0.0.1:2096'" in cli and "EnvironmentFile=/etc/nikatel-chisel/x.cli.env" in cli
    plain = bo.chisel_unit("x", "iran", 9600, rem, "198.51.100.9", False, forward=True)
    assert "http://198.51.100.9:9600" in plain and "--tls-skip-verify" not in plain
    reverse = bo.chisel_remotes(ports, 39000, False)                                                         # the reverse tunnel is what it was
    assert reverse[0] == "R:0.0.0.0:443:127.0.0.1:2096" and "--reverse" in bo.chisel_unit("x", "iran", 9600, reverse, "203.0.113.1", False)
    assert " client " in bo.chisel_unit("x", "foreign", 9600, reverse, "203.0.113.1", False)


def test_chisel_forward_kinds_in_the_tests_and_the_autopilot():
    from nikatel.services import autopilot as ap

    assert tt.kind_parts("chisel") == ("chisel", "ws") and tt.kind_parts("chisel:tls") == ("chisel", "wss")
    assert tt.kind_parts("chisel:fwd") == ("chisel", "fws") and tt.kind_parts("chisel:fwdtls") == ("chisel", "fwss")
    assert {"chisel:fwd", "chisel:fwdtls"} <= tt.KIND_KEYS
    assert ap.kind_of("chisel", "fws", {}) == "chisel:fwd" and ap.kind_of("chisel", "fwss", {}) == "chisel:fwdtls" and ap.kind_of("chisel", "wss", {}) == "chisel:tls" and ap.kind_of("chisel", "ws", {}) == "chisel"

    for key in ("chisel", "chisel:tls", "chisel:fwd", "chisel:fwdtls"):                                       # every kind of the test round-trips through the transport the tunnel is stored with
        k, tr = tt.kind_parts(key)
        assert ap.kind_of(k, tr, {}) == key


def _fake_build(monkeypatch, transport, taken_foreign=()):
    """A Chisel build with every remote call recorded: (calls, the Tunnel row, the story)."""
    from sqlalchemy import select

    from nikatel.db import session
    from nikatel.models import Server, Tunnel

    with session() as db:
        ir, fr = Server(name="ir1", host="203.0.113.7", role="iran"), Server(name="fr1", host="198.51.100.7", role="foreign")
        db.add_all([ir, fr])
        db.flush()
        db.expunge(ir)
        db.expunge(fr)

    calls, story = [], []
    node = lambda s: s.host                                                                                 # (a node is its address here)
    facts = {"units": [], "tcp": set(), "udp": set(), "ufw": True, "root": True, "python": True, "arch": "x86_64", "ips": []}
    monkeypatch.setattr(bo, "_node", node)
    monkeypatch.setattr(bo, "_preflight", lambda b, i, f: (dict(facts), {**facts, "tcp": set(taken_foreign)}))
    monkeypatch.setattr(bo.engines, "ensure_on_node", lambda n, e: False)
    monkeypatch.setattr(bo, "_install_sink", lambda b, f, k, bind="": b.created.append(f) or calls.append(("sink", f)))
    monkeypatch.setattr(bo, "run_action", lambda n, a, p=None, timeout=0: calls.append((a, n, dict(p or {}))) or "")
    monkeypatch.setattr(bo, "run_script", lambda n, s, p=None, timeout=0: "FP=PIN=\n" if s == "chisel_fp" else "")
    monkeypatch.setattr(bo, "_put", lambda n, path, text, mode: calls.append(("put", n, path, text)))
    monkeypatch.setattr(bo, "_start", lambda n, label, unit, b: calls.append(("start", n, label, unit)))
    monkeypatch.setattr(bo, "_wait_for_data", lambda b, i, k, hint, log_node, log_unit, why=None: calls.append(("wait", log_node, log_unit, hint)))
    monkeypatch.setattr(bo, "_measure", lambda b, i, k: None)
    monkeypatch.setattr(bo, "check_tunnel", lambda tid: None)
    monkeypatch.setattr(bo, "_say", lambda aid, line, done=None, ok=None: story.append(line))
    b = bo.Build(aid=1, kind="chisel", iran=ir, foreign=fr, ports=parse_ports("443=2096"), transport=transport, name="")
    bo._build(b)

    with session() as db:
        t = db.scalar(select(Tunnel))
        row = {"type": t.type, "transport": t.transport, "port": t.port, "record": dict(t.record)}

    return calls, row, story


def test_a_forward_chisel_build_puts_the_server_on_the_foreign_side(monkeypatch):
    calls, row, story = _fake_build(monkeypatch, "fwss", taken_foreign={9600, 9601})
    puts = {c[2]: c for c in calls if c[0] == "put"}
    name = next(p for p in puts if p.endswith(".srv.env")).split("/")[-1].removesuffix(".srv.env")
    server_unit, client_unit = f"/etc/systemd/system/chisel-{name}-kharej.service", f"/etc/systemd/system/chisel-{name}.service"
    assert puts[f"/etc/nikatel-chisel/{name}.srv.env"][1] == "198.51.100.7" and puts[f"/etc/nikatel-chisel/{name}.cli.env"][1] == "203.0.113.7"       # the key file of the server on the foreign side, the client's on the Iran side
    assert puts[server_unit][1] == "198.51.100.7" and " server " in puts[server_unit][3] and "--reverse" not in puts[server_unit][3]
    assert puts[client_unit][1] == "203.0.113.7" and " client " in puts[client_unit][3] and "https://198.51.100.7:9602" in puts[client_unit][3] and "--fingerprint PIN=" in puts[client_unit][3]
    prep = next(c for c in calls if c[0] == "chisel_prepare")
    assert prep[1] == "198.51.100.7" and prep[2]["TLS"] == "1"                                              # the key and the certificate are made where the server runs
    assert ("ufw_allow", "198.51.100.7", {"PORT": "9602", "PROTO": "tcp"}) in calls                         # the control port is opened on the foreign server (9600 and 9601 were taken there)
    assert [c[1:3] for c in calls if c[0] == "start"] == [("198.51.100.7", "foreign"), ("203.0.113.7", "Iran")]
    wait = next(c for c in calls if c[0] == "wait")
    assert wait[1] == "203.0.113.7" and wait[2] == f"chisel-{name}.service" and "foreign server" in wait[3]      # a tunnel that carries nothing: the log of the client (Iran) tells why
    assert row["type"] == "chisel" and row["transport"] == "fwss" and row["port"] == 9602
    assert row["record"]["direction"] == "forward" and row["record"]["transport_label"] == "Chisel · forward TLS WebSocket"
    assert any("forward tunnel" in line and "Iran server dials" in line for line in story) and "foreign side running, server key pinned on the client" in story


def test_a_reverse_chisel_build_is_what_it_was(monkeypatch):
    calls, row, story = _fake_build(monkeypatch, "wss", taken_foreign={9600})
    puts = {c[2]: c for c in calls if c[0] == "put"}
    name = next(p for p in puts if p.endswith(".srv.env")).split("/")[-1].removesuffix(".srv.env")
    assert puts[f"/etc/nikatel-chisel/{name}.srv.env"][1] == "203.0.113.7" and puts[f"/etc/nikatel-chisel/{name}.cli.env"][1] == "198.51.100.7"
    srv, cli = puts[f"/etc/systemd/system/chisel-{name}.service"], puts[f"/etc/systemd/system/chisel-{name}-kharej.service"]
    assert srv[1] == "203.0.113.7" and "--reverse" in srv[3] and cli[1] == "198.51.100.7" and "https://203.0.113.7:9600" in cli[3] and "'R:0.0.0.0:443:127.0.0.1:2096'" in cli[3]
    assert next(c for c in calls if c[0] == "chisel_prepare")[1] == "203.0.113.7"
    wait = next(c for c in calls if c[0] == "wait")
    assert wait[1] == "198.51.100.7" and wait[2] == f"chisel-{name}-kharej.service"
    assert row["transport"] == "wss" and row["port"] == 9600 and row["record"]["direction"] == "reverse" and "Iran side running, server key pinned on the client" in story
    assert "Chisel · reverse tunnel over TLS WebSocket" in story


def test_the_chisel_fingerprint_script_reads_the_server_unit_of_either_side():
    from nikatel.ssh import SCRIPTS

    script = SCRIPTS["chisel_fp"][0]
    assert "-u 'chisel-@NAME@.service'" in script and "-u 'chisel-@NAME@-kharej.service'" in script


def test_the_chisel_transports_the_api_takes(client):
    kinds = {k["key"]: k for k in client.get("/api/tunnels/kinds").json()["kinds"]}
    assert [t["key"] for t in kinds["chisel"]["transports"]] == ["ws", "wss", "fws", "fwss"]
    assert {"chisel:fwd", "chisel:fwdtls"} <= {k["key"] for k in client.get("/api/tunnel-test/kinds").json()["kinds"]}
    assert client.post("/api/tunnels", json={"iran_id": 1, "foreign_id": 2, "ports": "8072", "kind": "chisel", "transport": "fwss"}).status_code == 400      # (read-only: the transport itself passed the check)
