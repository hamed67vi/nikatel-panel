"""A link CARRIED inside another tunnel (1.69.41): a WireGuard link whose UDP packets travel inside a Chisel TLS WebSocket on ONE TCP port, so that it passes where raw GRE, ESP and every UDP port between two servers
are filtered; Backhaul then runs over its 10.x addresses as over any link. Two models: `wgchisel` (the foreign server dials the Iran server) and `wgchiselfwd` (the Iran server dials the foreign one).

What is tested here needs no server: the registry and the kinds, the ports one number gives, the scripts / unit / password file of an end (the right commands, nothing secret in a command line, a firewall rule that names the
exact peer and the TCP port, valid shell), what a build checks first, the build itself against a faked pair of servers (the order of its steps, what it records, what it removes when it fails), the link probe, the walk of the
link models, the units the health check judges, and what the Doctor does as its last resort."""
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot as ap
from nikatel.services import (
    builder,
    knowledge,
    linkmodels,
    linkprobe,
    tunnel_test,
    tunnels,
)

PUB_I = "A" * 43 + "="
PUB_F = "B" * 43 + "="
TOKEN = "ab" * 24
FP = "gvoqCVMPXRzxdDXPoUXrJLM8hTGwugsoukvUSyrq+Tc="
IRAN, FOREIGN = "203.0.113.1", "198.51.100.2"


def files(link="wgchisel", role="iran", n=52005, fp=FP, **kw):
    carrier = {"role": role, "token": TOKEN, "fp": fp}
    args = {"name": "ca-ir1", "local": IRAN if role == "iran" else FOREIGN, "remote": FOREIGN if role == "iran" else IRAN, "key": 1234567, "addr_cidr": "10.9.8.1/30" if role == "iran" else "10.9.8.2/30",
            "mtu": linkmodels.CARRIER_MTU, "port": n, "peer_pub": PUB_F if role == "iran" else PUB_I, "carrier": carrier}
    args.update(kw)
    return builder.link_scripts(link, **args)


UP = "/usr/local/sbin/nikatel-gre-up-ca-ir1"
DOWN = "/usr/local/sbin/nikatel-gre-down-ca-ir1"
UNIT = "/etc/systemd/system/nikatel-carrier-ca-ir1.service"


# ------------------------------------------------------------------ the registry, the ports, the kinds
def test_the_nine_plain_models_are_what_they_were_and_two_carried_models_come_beside_them():
    assert len(linkmodels.LINKS) == 9 and linkmodels.CARRIER_LINKS == ("wgchisel", "wgchiselfwd") and linkmodels.ALL_LINKS == linkmodels.LINKS + linkmodels.CARRIER_LINKS
    assert not set(linkmodels.CARRIER_LINKS) & set(linkmodels.PORTS) and not any(linkmodels.is_udp(k) for k in linkmodels.CARRIER_LINKS)          # (what must pass is a TCP port, not a UDP one)
    assert [linkmodels.needs(k) for k in linkmodels.CARRIER_LINKS] == ["tcp", "tcp"] and linkmodels.needs("wg") == "udp"
    assert linkmodels.CARRIER_DIAL == {"wgchisel": "foreign", "wgchiselfwd": "iran"} and set(linkmodels.CARRIER_TITLE) == set(linkmodels.CARRIER_LINKS)
    assert linkmodels.NAMES["gre"] == "GRE" and linkmodels.title("wgchisel") == "WireGuard in Chisel (foreign dials)" and linkmodels.kernel_of("wgchiselfwd") == ("wireguard",) and linkmodels.kernel_of("fou") == ("ip_gre", "fou")
    assert linkmodels.carrier_server_role("wgchisel") == "iran" and linkmodels.carrier_server_role("wgchiselfwd") == "foreign"


def test_a_kind_of_a_carried_link_is_split_like_any_other_and_nothing_else_is_taken_for_one():
    assert linkmodels.split_kind("wgchisel:tcpmux") == ("wgchisel", "tcpmux") and linkmodels.split_kind("wgchiselfwd:wssmux") == ("wgchiselfwd", "wssmux")
    assert linkmodels.split_kind("wgchisel:ws") == ("", "") and linkmodels.split_kind("wgchisel") == ("", "") and linkmodels.split_kind("wgchisel:") == ("", "")
    assert linkmodels.make_kind("wgchisel", "tcp") == "wgchisel:tcp" and tunnel_test.kind_link("wgchiselfwd:tcpmux") == "wgchiselfwd" and tunnel_test.kind_parts("wgchisel:wsmux") == ("backhaul", "wsmux")
    assert ap.kind_of("backhaul", "tcpmux", {"link": "wgchisel"}) == "wgchisel:tcpmux" and ap.kind_of("backhaul", "tcp", {"link": "wgchiselfwd"}) == "wgchiselfwd:tcp"
    assert linkmodels.all_kinds()[-1] == "ipsecnat:tcpmux" and not any(k.startswith("wgchisel") for k in linkmodels.all_kinds())            # (the plain nine keep their list)


def test_one_number_gives_the_three_ports_and_a_number_whose_ports_are_free_is_picked():
    assert linkmodels.carrier_ports(52000) == (52000, 52100, 9400) and linkmodels.carrier_ports(52099) == (52099, 52199, 9499)

    for bad in (51999, 52100, 0):
        with pytest.raises(ValueError):
            linkmodels.carrier_ports(bad)

    assert linkmodels.pick_carrier_port(set(), set()) == 52000
    assert linkmodels.pick_carrier_port({52000}, set()) == 52001                      # the WireGuard port is held
    assert linkmodels.pick_carrier_port({52100}, set()) == 52001                      # the loopback door of the number 52000 is held
    assert linkmodels.pick_carrier_port(set(), {9400, 9401}) == 52002                 # the TCP port of the Chisel server is held
    assert linkmodels.pick_carrier_port(set(range(52000, 52100)), set()) == 0         # the range is full
    wg, door, tcp = linkmodels.carrier_ports(52000)
    assert len({wg, door}) == 2 and not 52000 <= door <= 52099 and not 9400 <= wg <= 9499 and tcp not in (9600, 9700, 9900)        # (the doors and ports of one number never meet the numbers or the ports of the other engines)
    assert linkmodels.CARRIER_MTU + linkmodels.CARRIER_OVERHEAD < linkmodels.CARRIER_MAX_PACKET and builder.mtu_limit({"link": "wgchisel"}) == linkmodels.CARRIER_MTU and builder.mtu_limit({"link": "wg"}) == 1476


def test_the_remote_the_dialling_side_gives_chisel_and_the_firewall_match_follow_who_dials():
    assert linkmodels.carrier_remote("wgchisel", 52005) == "R:127.0.0.1:52105:127.0.0.1:52005/udp"       # reverse: the Iran server listens at the door
    assert linkmodels.carrier_remote("wgchiselfwd", 52005) == "127.0.0.1:52105:127.0.0.1:52005/udp"      # forward: the Iran client listens at the door itself
    # the firewall: towards the Chisel server the TCP port is the destination, from it the source - the other way round on the other end
    assert linkmodels.carrier_fw_match("wgchisel", "iran", 52005) == ("-p tcp --dport 9405", "-p tcp --sport 9405")
    assert linkmodels.carrier_fw_match("wgchisel", "foreign", 52005) == ("-p tcp --sport 9405", "-p tcp --dport 9405")
    assert linkmodels.carrier_fw_match("wgchiselfwd", "foreign", 52005) == ("-p tcp --dport 9405", "-p tcp --sport 9405")


# ------------------------------------------------------------------ the scripts, the unit and the password file of one end
def test_every_end_has_the_files_of_a_link_the_names_the_rest_of_the_panel_knows_and_a_unit_for_the_carrier():
    for link in linkmodels.CARRIER_LINKS:
        for role in ("iran", "foreign"):
            f = files(link, role)
            base = set(files("gre"))
            assert base <= set(f) and UP in f and DOWN in f and UNIT in f                                        # the check script, the units and the timer ARE those of a GRE link
            assert f"/etc/nikatel-chisel/ca-ir1.{'srv' if role == linkmodels.carrier_server_role(link) else 'cli'}.env" in f
            assert "Nikatel WireGuard in Chisel" in f["/etc/systemd/system/nikatel-gre-ca-ir1.service"]


def test_the_iran_end_starts_the_talk_through_the_door_and_the_foreign_end_answers_whoever_the_carrier_brings():
    iran, foreign = files("wgchisel", "iran")[UP], files("wgchisel", "foreign")[UP]
    assert 'endpoint "127.0.0.1:$DOOR"' in iran and "DOOR=52105" in iran and "PORT=52005" in iran
    assert "endpoint" not in foreign.replace("# ", "") .split("wg set", 1)[1].split("\n", 1)[0] and "PORT=52005" in foreign                    # (no endpoint: it learns it from the first packet)
    assert 'persistent-keepalive 25' in iran and 'allowed-ips "$PEER_IP/32"' in iran and "PEER_IP=10.9.8.2" in iran and "PEER_IP=10.9.8.1" in foreign
    assert "ip link set \"$NAME\" mtu \"$MTU\" up" in iran and "MTU=8000" in iran
    assert 'systemctl is-active --quiet "$CARRIER" || systemctl start "$CARRIER"' in iran and "CARRIER=nikatel-carrier-ca-ir1.service" in iran


def test_the_down_script_takes_the_link_the_carrier_and_the_tagged_rules_away():
    d = files("wgchisel", "iran")[DOWN]
    assert 'systemctl stop "nikatel-carrier-$NAME.service"' in d and "ip link del" in d and "wireguard" in d and '--comment $TAG' in d and d.rstrip().endswith("exit 0")


def test_the_server_end_runs_chisel_server_and_the_dialling_end_dials_with_the_key_pinned():
    srv = files("wgchisel", "iran")[UNIT]                                                                       # wgchisel: the Iran server is the server (the foreign server dials)
    assert "nikatel-chisel server --host 0.0.0.0 --port 9405 --reverse --keyfile /etc/nikatel-chisel/ca-ir1.key --keepalive 25s --tls-key /etc/nikatel-chisel/ca-ir1.tls.key --tls-cert /etc/nikatel-chisel/ca-ir1.crt" in srv
    assert "EnvironmentFile=/etc/nikatel-chisel/ca-ir1.srv.env" in srv and "Restart=always" in srv and "[Install]" not in srv and "--fingerprint" not in srv
    cli = files("wgchisel", "foreign")[UNIT]
    assert f"nikatel-chisel client --keepalive 25s --max-retry-interval 5s --fingerprint {FP} --tls-skip-verify https://{IRAN}:9405 'R:127.0.0.1:52105:127.0.0.1:52005/udp'" in cli and "EnvironmentFile=/etc/nikatel-chisel/ca-ir1.cli.env" in cli
    fwd_srv, fwd_cli = files("wgchiselfwd", "foreign")[UNIT], files("wgchiselfwd", "iran")[UNIT]                # wgchiselfwd: the foreign server is the server (the Iran server dials)
    assert "server --host 0.0.0.0 --port 9405 --keyfile" in fwd_srv and "--reverse" not in fwd_srv                  # (forward: no reverse remotes)
    assert f"https://{FOREIGN}:9405 '127.0.0.1:52105:127.0.0.1:52005/udp'" in fwd_cli and "'R:" not in fwd_cli
    assert "--fingerprint" not in files("wgchisel", "foreign", fp="")[UNIT]                                      # (a key that could not be read: not pinned)


def test_the_password_is_in_a_file_of_0600_and_in_no_command_line_and_the_wireguard_key_is_never_in_a_script():
    for link in linkmodels.CARRIER_LINKS:
        for role in ("iran", "foreign"):
            f = files(link, role)
            env = next(t for p, t in f.items() if p.endswith(".env"))
            assert env == f"AUTH=nikatel:{TOKEN}\n"
            assert all(TOKEN not in t for p, t in f.items() if not p.endswith(".env"))                          # (not in a script, not in a unit)
            assert "/etc/nikatel/wg/ca-ir1.key" in f[UP] and "private-key \"$KEY\"" in f[UP] and "wg genkey" not in f[UP]
    assert builder._file_mode("/etc/nikatel-chisel/ca-ir1.srv.env") == "0600" and builder._file_mode(UP) == "0700" and builder._file_mode(UNIT) == "0644"


def test_the_firewall_rules_name_the_exact_peer_and_the_tcp_port_and_the_loopback_is_let_through():
    up = files("wgchisel", "iran")[UP]
    assert '-s "$REMOTE" -d "$LOCAL" -p tcp --dport 9405 -m comment --comment "$TAG" -j ACCEPT' in up and '-s "$LOCAL" -d "$REMOTE" -p tcp --sport 9405 -m comment --comment "$TAG" -j ACCEPT' in up
    assert 'iptables -I INPUT 1 -i lo -m comment --comment "$TAG" -j ACCEPT' in up and "@MATCH" not in up
    cli = files("wgchisel", "foreign")[UP]
    assert '-s "$REMOTE" -d "$LOCAL" -p tcp --sport 9405' in cli and '-s "$LOCAL" -d "$REMOTE" -p tcp --dport 9405' in cli          # (the dialling end: the port is the destination of what it sends)


def test_the_plain_links_firewall_text_is_what_it_was():
    up = builder.link_scripts("fou", "ca-ir1", IRAN, FOREIGN, 1, "10.9.8.1/30", 1360, port=5505)[UP]
    assert '-s "$REMOTE" -d "$LOCAL" -p udp --dport 5505 -m comment' in up and '-s "$LOCAL" -d "$REMOTE" -p udp --dport 5505 -m comment' in up and "@MATCH" not in up


@pytest.mark.skipif(shutil.which("sh") is None, reason="no sh")
def test_every_script_of_every_end_is_valid_shell(tmp_path):
    for link in linkmodels.CARRIER_LINKS:
        for role in ("iran", "foreign"):
            for path, text in files(link, role).items():
                if "/sbin/" in path:
                    p = tmp_path / path.rsplit("/", 1)[1]
                    p.write_text(text)
                    r = subprocess.run(["sh", "-n", str(p)], capture_output=True, text=True, check=False)
                    assert r.returncode == 0, (link, role, path, r.stderr)


def test_the_files_are_refused_when_anything_is_not_what_it_should_be():
    for bad in ({"carrier": None}, {"peer_pub": "short"}, {"carrier": {"role": "mars", "token": TOKEN, "fp": ""}}, {"carrier": {"role": "iran", "token": "xyz", "fp": ""}}, {"carrier": {"role": "iran", "token": TOKEN, "fp": "a b"}},
                {"mtu": 9000}, {"mtu": 1000}, {"local": "iran.example.com"}, {"name": "Bad Name"}, {"port": 52100}, {"port": 51999}):
        with pytest.raises(ValueError):
            files("wgchisel", "iran", **bad)


# ------------------------------------------------------------------ what a build checks first
def prepare(monkeypatch, link, facts_i, facts_f, **opts):
    from nikatel.services import engines

    facts = {IRAN: facts_i, FOREIGN: facts_f}
    ran = []
    monkeypatch.setattr(builder, "_say", lambda *a, **k: None)
    monkeypatch.setattr(builder, "run_script", lambda node, name, params=None, timeout=40: facts[node.host])
    monkeypatch.setattr(builder, "run_action", lambda node, name, params=None, timeout=60: ran.append((node.host, name)) or ("WG_OK installed\n" if name == "wg_install" else f"PUB={PUB_I if node.host == IRAN else PUB_F}\n"))
    monkeypatch.setattr(engines, "ensure_on_node", lambda node, engine, say=None, only_missing=False: ran.append((node.host, "ensure " + engine, only_missing)) or False)
    b = SimpleNamespace(aid=1, opts={"link": link, **opts}, name="t1", created=[], iran=Server(id=1, name="ir1", host=IRAN), foreign=Server(id=2, name="fr1", host=FOREIGN))
    return builder._prepare_link(b, builder.Node(IRAN, "root", 22), builder.Node(FOREIGN, "root", 22), link), b, ran


FACTS = "WG_MOD=1\nWG_TOOL=1\nFOU_MOD=1\nUDP=\nFOU_PORTS=\nAPT=1\nMODS=ip_gre wireguard\nIPIP_PAIRS=\nTCP=22\nCHISEL=1\n"


def test_a_carried_link_is_prepared_with_a_free_number_a_key_each_and_a_password(monkeypatch):
    (n, pubs), b, ran = prepare(monkeypatch, "wgchisel", FACTS.replace("UDP=", "UDP=52000"), FACTS.replace("TCP=22", "TCP=22 9401"))
    assert n == 52002 and pubs["iran"] == PUB_I and pubs["foreign"] == PUB_F                                       # 52000: a UDP port held on the Iran server; 52001: its TCP port held on the foreign one
    assert re.fullmatch(r"[0-9a-f]{48}", pubs["token"]) and [w for w, _ in b.created] == ["wg", "wg"]
    assert [r for r in ran if r[1] == "ensure chisel"] == [(IRAN, "ensure chisel", True), (FOREIGN, "ensure chisel", True)]          # Chisel is put on a server that lacks it - one that has it is left as it is
    assert sum(1 for r in ran if r[1] == "wg_keygen") == 2 and not any(r[1] == "wg_install" for r in ran)
    (n2, pubs2), _, _ = prepare(monkeypatch, "wgchiselfwd", FACTS, FACTS)
    assert n2 == 52000 and pubs2["token"] != pubs["token"]


def test_a_carried_link_needs_wireguard_on_both_servers_and_a_package_only_when_the_owner_asked(monkeypatch):
    no_tool = FACTS.replace("WG_TOOL=1", "WG_TOOL=0")

    with pytest.raises(RuntimeError, match="wireguard-tools is not installed"):
        prepare(monkeypatch, "wgchisel", FACTS, no_tool)

    (_, _), _, ran = prepare(monkeypatch, "wgchisel", no_tool, no_tool, install_wg=True)
    assert [r[1] for r in ran].count("wg_install") == 2

    with pytest.raises(RuntimeError, match="cannot load the WireGuard kernel module"):
        prepare(monkeypatch, "wgchiselfwd", FACTS.replace("WG_MOD=1", "WG_MOD=0"), FACTS)


def test_a_carried_link_with_no_free_number_says_so(monkeypatch):
    with pytest.raises(RuntimeError, match="no free port for the WireGuard in Chisel"):
        prepare(monkeypatch, "wgchisel", FACTS.replace("UDP=", "UDP=" + " ".join(str(p) for p in range(52000, 52100))), FACTS)


# ------------------------------------------------------------------ the build against a faked pair of servers
class Pair:
    """Two servers that answer the scripts and actions of a build (one with a carried link) and note every call."""

    def __init__(self, monkeypatch, ping_ok=True, unit_state="active"):
        from nikatel.services import engines

        self.calls, self.puts, self.units = [], {}, []
        self.ping_ok, self.unit_state, self.peer = ping_ok, unit_state, {}
        monkeypatch.setattr(builder, "run_script", self.script)
        monkeypatch.setattr(builder, "run_action", self.action)
        monkeypatch.setattr(builder, "_put", lambda node, path, text, mode: self.calls.append((node.host, "put", path)) or self.puts.__setitem__((node.host, path), (text, mode)))
        monkeypatch.setattr(builder, "_unit", self.unit)
        monkeypatch.setattr(builder, "ensure_backhaul", lambda *a, **k: None)
        monkeypatch.setattr(builder, "check_tunnel", lambda tid: None)
        monkeypatch.setattr(builder.time, "sleep", lambda s: None)
        monkeypatch.setattr(engines, "ensure_on_node", lambda *a, **k: False)
        self.said = []
        monkeypatch.setattr(builder, "_say", lambda aid, line, done=None, ok=None: self.said.append(line))

    def unit(self, node, unit):
        self.calls.append((node.host, "unit", unit))
        self.units.append((node.host, unit))
        return self.unit_state

    def script(self, node, name, params=None, timeout=40):
        params = params or {}
        self.calls.append((node.host, name))
        host = node.host

        if name == "build_facts":
            return f"IPS={host}/24\nLINKS=\nLISTEN=22\nUNITS=\nBROKEN=\nGRE_MOD=1\nBACKHAUL=v0.7\nIPTABLES=1\n"
        if name == "link_facts":
            return FACTS
        if name == "carrier_probe_ping":
            self.peer[host] = params["PEER"]
            return "10 packets transmitted, 10 received, 0% packet loss, time 3000ms\nrtt min/avg/max/mdev = 40.0/41.5/43.0/1.0 ms\n" if self.ping_ok else "10 packets transmitted, 0 received, 100% packet loss, time 9000ms\n"
        if name == "gre_hold":
            return "20 packets transmitted, 20 received, 0% packet loss\n"
        if name == "estab_check":
            return f"ESTAB 0 0 {self.peer.get(IRAN, '')}:9900 {self.peer.get(IRAN, '')}:41000\n"
        if name == "chisel_fp":
            return f"FP={FP}\n"
        if name == "http_probe":
            return "200"
        if name == "tunnel_side":
            return "".join(f"UNIT {u} active\n" for u in params["UNITS"].split())
        if name == "unit_log":
            return "carrier: connection refused\n"
        return ""

    def action(self, node, name, params=None, timeout=90):
        self.calls.append((node.host, name))
        return {"wg_keygen": f"PUB={PUB_I if node.host == IRAN else PUB_F}\n", "chisel_prepare": "PREPARED\n", "sysctl_tune": "tuned\n", "data_test_server": "started\n"}.get(name, "")


def make_build(opts, name="test-9"):
    with session() as db:
        i, f = Server(name="ir-car", host=IRAN, role="iran", online=True), Server(name="fr-car", host=FOREIGN, role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        db.expunge(i)
        db.expunge(f)

    return builder.Build(77, i, f, builder.parse_ports("8075"), "tcpmux", 0, 75, True, opts=opts, name=name)


@pytest.mark.parametrize("link,server_host", [("wgchisel", IRAN), ("wgchiselfwd", FOREIGN)])
def test_a_carried_link_is_built_server_end_first_then_the_dialling_end_with_the_key_pinned_and_recorded(monkeypatch, link, server_host):
    pair = Pair(monkeypatch)
    b = make_build({"link": link})
    builder._build(b)
    host_calls = [c for c in pair.calls if len(c) == 3]
    first_put = next(c for c in host_calls if c[1] == "put")
    assert [c for c in pair.calls if c[1] == "chisel_prepare"] == [(server_host, "chisel_prepare")]               # the key and certificate of the Chisel server are made on the server end ...
    assert pair.calls.index((server_host, "chisel_prepare")) < pair.calls.index(first_put) and first_put[0] == server_host        # ... before anything is written, and that end is the first to be written
    other = FOREIGN if server_host == IRAN else IRAN
    assert pair.calls.index((server_host, "chisel_fp")) < pair.calls.index(next(c for c in host_calls if c[0] == other and c[1] == "put"))       # the dialling end is written after the fingerprint was read
    unit_text = pair.puts[(other, "/etc/systemd/system/nikatel-carrier-test-9.service")][0]
    assert f"--fingerprint {FP}" in unit_text and "--fingerprint" not in pair.puts[(server_host, "/etc/systemd/system/nikatel-carrier-test-9.service")][0]
    assert pair.puts[(server_host, "/etc/nikatel-chisel/test-9.srv.env")][1] == "0600" and pair.puts[(other, "/etc/nikatel-chisel/test-9.cli.env")][1] == "0600"
    assert (IRAN, "nikatel-gre-test-9.service") in pair.units and (FOREIGN, "nikatel-gre-test-9.service") in pair.units
    assert "mtu_probe" not in [c[1] for c in pair.calls] and "link_probe_ping" not in [c[1] for c in pair.calls] and [c[1] for c in pair.calls].count("carrier_probe_ping") == 2        # no path MTU is measured: the packets travel in a TCP stream; the long warm-up is used
    assert any(f"MTU {linkmodels.CARRIER_MTU} for the {linkmodels.NAMES[link]} link" in s for s in pair.said) and any("established over " + linkmodels.NAMES[link] in s for s in pair.said) and "Tunnel test-9 is ready." in pair.said

    with session() as db:
        t = db.query(Tunnel).filter(Tunnel.name == "test-9").one()
        rec = dict(t.record)
        assert rec["link"] == link and 52000 <= rec["link_port"] <= 52099 and rec["mtu"] == linkmodels.CARRIER_MTU and t.engine == "backhaul"
        assert rec["carrier"] == {"kind": "chisel", "dial": linkmodels.CARRIER_DIAL[link], "tcp_port": 9400 + rec["link_port"] - 52000, "wg_port": rec["link_port"], "door": rec["link_port"] + 100, "pinned": True}
        assert rec["wg"] == {"iran_pub": PUB_I, "foreign_pub": PUB_F} and TOKEN not in str(rec) and "token" not in rec["carrier"]            # (public keys only; the password is on the servers)
        assert re.fullmatch(r"[0-9a-f]{32}", t.secrets["token"]) and t.secrets["token"] not in pair.puts[(server_host, "/etc/nikatel-chisel/test-9.srv.env")][0]       # (the Backhaul token is another one than the password of the carrier)


def test_a_carried_link_that_does_not_pass_says_which_port_to_suspect_and_everything_it_made_is_removed(monkeypatch):
    Pair(monkeypatch, ping_ok=False)
    b = make_build({"link": "wgchisel"})

    with pytest.raises(builder.LinkFailed, match=r"WireGuard in Chisel \(foreign dials\) link is up but the inner addresses cannot ping each other .*TCP port 94\d\d \(the carrier\) is probably filtered"):
        builder._build(b)

    assert {n.host for _, n in b.created} == {IRAN, FOREIGN}                                                      # both servers are in the rollback list
    removed = []
    monkeypatch.setattr(builder, "run_action", lambda node, name, params=None, timeout=90: removed.append((node.host, name, dict(params or {}))) or "REMOVED")
    builder._rollback(b)
    assert {(h, n, p["NAME"]) for h, n, p in removed} == {(IRAN, "remove_tunnel_files", "test-9"), (FOREIGN, "remove_tunnel_files", "test-9")}

    with session() as db:
        assert db.query(Tunnel).filter(Tunnel.name == "test-9").count() == 0


def test_a_carrier_that_does_not_start_is_a_failure_of_the_link_with_the_log_of_both_units(monkeypatch):
    pair = Pair(monkeypatch, unit_state="failed")
    b = make_build({"link": "wgchiselfwd"})

    with pytest.raises(builder.LinkFailed, match=r"unit on the foreign server ended failed: carrier: connection refused"):
        builder._build(b)

    assert {n.host for _, n in b.created} == {IRAN, FOREIGN} and not any(h == IRAN and k == "put" for h, k, *_ in (c for c in pair.calls if len(c) == 3))          # (both servers are in the rollback list from the preparation; the end that dials was never written)


def test_a_build_asked_for_more_mtu_than_chisel_carries_is_refused_and_the_carrier_may_have_a_big_one():
    assert builder.clean_bh_opts({"link": "wgchisel"}) == {"link": "wgchisel"} and builder.clean_bh_opts({"link": "wgchiselfwd", "install_wg": True}) == {"link": "wgchiselfwd", "install_wg": True}

    with pytest.raises(ValueError, match="link must be one of"):
        builder.clean_bh_opts({"link": "wgchisels"})


# ------------------------------------------------------------------ the walk of the link models, the kinds, the units, the Doctor
def test_the_carried_links_are_the_last_models_of_the_walk_and_only_where_the_owner_allowed_wireguard():
    tried = [k for k in builder.FALLBACK_LINKS if k not in ("wg", "wgchisel", "wgchiselfwd")]
    assert builder.FALLBACK_LINKS[-3:] == ("wg", "wgchisel", "wgchiselfwd") and set(builder.FALLBACK_LINKS) == set(linkmodels.ALL_LINKS)
    assert builder.next_link(tried) == "" and builder.next_link(tried, wg_ok=True) == "wg"
    assert builder.next_link(tried + ["wg"], wg_ok=True) == "wgchisel" and builder.next_link(tried + ["wg", "wgchisel"], wg_ok=True) == "wgchiselfwd" and builder.next_link(list(builder.FALLBACK_LINKS), wg_ok=True) == ""


def test_the_pair_test_has_the_carried_kinds_but_the_doctors_standard_order_does_not():
    kinds = {f"{k}:{t}" for k in linkmodels.CARRIER_LINKS for t in ("tcpmux", "tcp", "wsmux", "wssmux")}
    assert kinds <= tunnel_test.KIND_KEYS and kinds <= tunnel_test.GRID_ONLY
    assert not kinds & set(knowledge.PRIOR) and not kinds & set(knowledge.READY) and not kinds & set(knowledge.CUT_LINKS)            # (a cut tunnel is never repaired by walking them; the night survey does not build them)
    assert knowledge.CARRIER_KINDS == ("wgchisel:tcpmux", "wgchiselfwd:tcpmux") and set(knowledge.CARRIER_KINDS) <= kinds
    assert {k[3] for k in tunnel_test.KINDS if k[0] in kinds} == {"Backhaul over another link"}
    assert [k[1] for k in tunnel_test.KINDS if k[0] == "wgchisel:tcpmux"] == ["Backhaul · TCP Mux over WireGuard in Chisel (foreign dials)"]


def test_the_health_check_judges_the_carrier_unit_on_both_ends_and_a_plain_tunnel_has_none():
    t = SimpleNamespace(name="ca-ir1", type="backhaul", transport="tcpmux", port=9901, forwarded_ports=[8075], foreign_tunnel_ip="10.9.8.2", record={"link": "wgchisel"})
    p = tunnels.plan_for(t)
    assert p.iran_units == ["backhaul-iran-tcpmux-9901.service", "nikatel-gre-ca-ir1.service", "nikatel-carrier-ca-ir1.service"] and p.foreign_units[-1] == "nikatel-carrier-ca-ir1.service" and p.iface == "ca-ir1"
    t.record = {"link": "wg"}
    assert tunnels.plan_for(t).iran_units == ["backhaul-iran-tcpmux-9901.service", "nikatel-gre-ca-ir1.service"]


def test_the_doctor_names_the_carrier_when_the_plain_ping_passes_and_the_carried_link_does_not():
    cause, text = ap.classify({"state": "down", "gre": True, "gre_loss": 100, "under_loss_i": 0, "under_loss_f": 0, "link": "wgchisel", "port": 9901})
    assert cause == "gre_blocked" and "WireGuard in Chisel (foreign dials) link does not" in text and "TCP port of its carrier is filtered" in text


def test_the_api_builds_a_carried_link_asks_for_wireguard_tools_and_lists_it(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    seen = []
    monkeypatch.setattr(builder, "start_build", lambda iran_id, foreign_id, ports, transport, mtu=0, keepalive=75, forced_name="", nodelay=True, opts=None: seen.append((transport, dict(opts or {}))) or 7)
    body = {"iran_id": 1, "foreign_id": 2, "ports": "8075", "kind": "backhaul"}
    assert client.post("/api/tunnels", json={**body, "transport": "wgchiselfwd:tcpmux"}).json() == {"ok": True, "id": 7}
    assert seen == [("tcpmux", {"link": "wgchiselfwd", "install_wg": True, "link_fallback": True})]
    assert client.post("/api/tunnels", json={**body, "transport": "wgchisel:tcp"}).status_code == 202 and client.post("/api/tunnels", json={**body, "transport": "wgchisel2:tcp"}).status_code == 400
    kinds = client.get("/api/tunnels/kinds").json()["kinds"]
    assert {"wgchisel:tcpmux", "wgchiselfwd:tcpmux"} <= {t["key"] for k in kinds if k["key"] == "backhaul" for t in k["transports"]}


# ------------------------------------------------------------------ the link probe
def facts(**kw):
    base = {"wg_mod": True, "wg_tool": True, "fou_mod": True, "udp": set(), "fou_ports": set(), "apt": True, "mods": {"ip_gre", "wireguard"}, "ipip_pairs": set(), "tcp": {22}, "chisel": True}
    base.update(kw)
    return base


def test_the_probe_does_not_ask_for_the_carried_links_unless_they_are_named_and_asks_with_free_ports_when_they_are():
    p = linkprobe.plan(list(linkmodels.ALL_LINKS), facts(), facts(), ["10.4.5.0/30"], (IRAN, FOREIGN))
    assert set(p) == set(linkmodels.ALL_LINKS) and p["wgchisel"]["port"] != p["wgchiselfwd"]["port"] and all("net" in p[k] for k in linkmodels.CARRIER_LINKS)
    a, b = (linkmodels.carrier_ports(p[k]["port"]) for k in linkmodels.CARRIER_LINKS)
    assert not {a[0], a[1]} & {b[0], b[1]} and a[2] != b[2]                                                          # two probes at once never share a port
    assert not set(p["wg"]) & {"skip"} and p["wg"]["port"] not in (a[0], b[0])
    no_chisel = linkprobe.plan(["wgchisel"], facts(chisel=False), facts(), [], (IRAN, FOREIGN))
    assert no_chisel["wgchisel"]["skip"]["ok"] is None and "Chisel core" in no_chisel["wgchisel"]["skip"]["error"]
    no_tool = linkprobe.plan(["wgchisel", "wgchiselfwd"], facts(), facts(wg_tool=False), [], (IRAN, FOREIGN))
    assert all(v["skip"]["ok"] is None and "wireguard-tools" in v["skip"]["error"] for v in no_tool.values())              # (the probe never installs a package)
    no_mod = linkprobe.plan(["wgchiselfwd"], facts(wg_mod=False), facts(), [], (IRAN, FOREIGN))
    assert no_mod["wgchiselfwd"]["skip"]["ok"] is False
    held = linkprobe.plan(["wgchisel"], facts(udp={52000}, tcp={22, 9401}), facts(), [], (IRAN, FOREIGN))
    assert held["wgchisel"]["port"] == 52002


def test_the_summary_of_the_probe_names_the_carried_links():
    s = linkprobe.summary({"wgchisel": {"ok": True, "rtt_ms": 41.2}, "wgchiselfwd": {"ok": False}, "wg": {"ok": None}})
    assert s == "passes: WireGuard in Chisel (foreign dials) 41 ms; does not pass: WireGuard in Chisel (Iran dials); not known: WireGuard"


def test_the_facts_script_says_the_tcp_ports_and_whether_chisel_is_there():
    f = builder.parse_link_facts("WG_MOD=1\nTCP=22 443 9400\nCHISEL=1\n")
    assert f["tcp"] == {22, 443, 9400} and f["chisel"] is True and builder.parse_link_facts("WG_MOD=1\n")["chisel"] is False


def test_a_probe_of_a_carried_link_writes_the_same_files_as_a_build_runs_them_and_removes_them_again(monkeypatch):
    calls, puts = [], {}
    monkeypatch.setattr(linkprobe, "_put", lambda node, path, text, mode: calls.append((node.host, "put", path)) or puts.__setitem__((node.host, path), mode))
    monkeypatch.setattr(builder, "_unit", lambda node, unit: calls.append((node.host, "unit", unit)) or "active")
    monkeypatch.setattr(linkprobe.time, "sleep", lambda s: None)

    def action(node, name, params=None, timeout=60):
        calls.append((node.host, name, (params or {}).get("MODE", "")))
        return {"wg_keygen": f"PUB={PUB_I if node.host == IRAN else PUB_F}\n", "link_probe_run": "LINK_PROBE up lp-wgchisel rc=0\n"}.get(name, "")

    monkeypatch.setattr(linkprobe, "run_action", action)
    monkeypatch.setattr(linkprobe, "run_script", lambda node, name, params=None, timeout=40: calls.append((node.host, name)) or (f"FP={FP}\n" if name == "chisel_fp" else "10 packets transmitted, 9 received\nrtt min/avg/max/mdev = 40.0/42.0/43.0/1.0 ms\n"))
    nodes = (builder.Node(IRAN, "root", 22), builder.Node(FOREIGN, "root", 22))
    r = linkprobe._probe_one("wgchisel", "lp-wgchisel", nodes, (IRAN, FOREIGN), ("10.253.1.0/30", "10.253.1.1", "10.253.1.2"), 52010)
    assert r["ok"] is True and r["rtt_ms"] == 42.0
    assert calls.index((IRAN, "chisel_prepare", "")) < calls.index(next(c for c in calls if c[:2] == (IRAN, "put")))                # the server end first (wgchisel: the Iran server)
    assert calls.index((IRAN, "chisel_fp")) < calls.index(next(c for c in calls if c[:2] == (FOREIGN, "put")))
    assert (IRAN, "link_probe_run", "up") in calls and (FOREIGN, "link_probe_run", "up") in calls and [c for c in calls if c[1] == "link_probe_clean"] == [(IRAN, "link_probe_clean", ""), (FOREIGN, "link_probe_clean", "")]
    assert all(unit.startswith("nikatel-carrier-lp-") for _, kind, unit in (c for c in calls if len(c) == 3 and c[1] == "unit")) and not any(p.endswith(".timer") for _, p in puts)         # (nothing is enabled to start at boot, no timer)
    assert puts[(IRAN, "/etc/nikatel-chisel/lp-wgchisel.srv.env")] == "0600"


# ------------------------------------------------------------------ the Doctor: the last resort of a cut
def probe_says(monkeypatch, res):
    from nikatel.services import license

    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    seen = []
    monkeypatch.setattr(linkprobe, "probe", lambda iran_id, foreign_id, links, record=True: seen.append(list(links)) or res)
    return seen


def test_the_last_resort_builds_only_the_carried_kinds_the_probe_saw_pass(monkeypatch):
    said = []
    monkeypatch.setattr(ap, "_say", lambda aid, line, **k: said.append(line))
    seen = probe_says(monkeypatch, {"wgchisel": {"ok": False}, "wgchiselfwd": {"ok": True, "rtt_ms": 90.0}})
    got = ap._carrier_last_resort(1, {"iran_id": 1, "foreign_id": 2}, {"tcpmux"})
    assert got == ["wgchiselfwd:tcpmux"] and seen == [["wgchisel", "wgchiselfwd"]] and any("Carrier probe: passes: WireGuard in Chisel (Iran dials) 90 ms" in s for s in said)
    assert ap._carrier_last_resort(1, {"iran_id": 1, "foreign_id": 2}, {"wgchisel:tcpmux", "wgchiselfwd:tcpmux"}) == [] and len(seen) == 1                # (already tried: nothing is asked)


def test_the_last_resort_tells_the_owner_what_to_do_once_when_wireguard_tools_is_missing_and_never_installs_it(monkeypatch):
    said = []
    monkeypatch.setattr(ap, "_say", lambda aid, line, **k: said.append(line))
    probe_says(monkeypatch, {k: {"ok": None, "error": "wireguard-tools is not installed on a server (the probe never installs a package)"} for k in linkmodels.CARRIER_LINKS})
    assert ap._carrier_last_resort(1, {"iran_id": 1, "foreign_id": 2}, set()) == []
    assert any("build a WireGuard tunnel once from Create tunnel" in s for s in said)


def test_the_last_resort_survives_a_probe_that_cannot_run(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("both servers must be on")

    from nikatel.services import license

    said = []
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    monkeypatch.setattr(ap, "_say", lambda aid, line, **k: said.append(line))
    monkeypatch.setattr(linkprobe, "probe", boom)
    assert ap._carrier_last_resort(1, {"iran_id": 1, "foreign_id": 2}, set()) == [] and any("could not run" in s for s in said)
