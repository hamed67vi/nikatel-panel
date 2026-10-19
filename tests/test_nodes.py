import re
import shutil
import subprocess

import pytest

from nikatel import ssh
from nikatel.services import nodes
from nikatel.services.facts import parse_facts
from nikatel.services.nodes import parse_ping, verdict

FACTS = "CPU=100 50 200 120\nNET=0 0 1250000 2500000\nMEM=4000000 1000000 2000000 1500000\nDISK=100000000 40000000\nLOAD=0.5 0.4 0.3 4\nUP=86400\nCONN=12\nHOST=ir1 5.15.0\nTOP=xray 37.5\nABUSE=1234\n"


def test_facts_has_top_and_abuse():
    f = parse_facts(FACTS)
    assert f["top"] == "xray 38%" and f["abuse_rules"] == 1234 and f["swap"] == 25.0


def test_ping_parse_and_verdict():
    assert parse_ping("4 packets transmitted, 4 received, 0% packet loss\nrtt min/avg/max/mdev = 80.1/85.2/90.0/3.0 ms") == (4, 4, 85.2)
    assert parse_ping("4 packets transmitted, 0 received, 100% packet loss") == (4, 0, None)
    ok = {"ok": 4, "total": 4, "pending": False}
    assert verdict([ok, ok])[0] == "clean"
    assert verdict([ok, {"ok": 2, "total": 4, "pending": False}])[0] == "filtered"                              # half of two locations is a lot
    many = [dict(ok, total=4, ok=4) for _ in range(18)] + [{"ok": 3, "total": 4, "pending": False}, {"ok": 2, "total": 4, "pending": False}, {"ok": 1, "total": 4, "pending": False}]
    assert verdict(many)[0] == "clean" and "not a filter" in verdict(many)[1]                                  # 3 of 21 with some loss: a clean address
    cut = [dict(ok, total=4, ok=4) for _ in range(12)] + [{"ok": 0, "total": 4, "pending": False} for _ in range(9)]
    assert verdict(cut)[0] == "filtered"                                                                        # 9 of 21 dead: filtered
    assert verdict([{"ok": 0, "total": 4, "pending": False}])[0] == "blocked"
    assert verdict([{"ok": 0, "total": 4, "pending": True}])[0] == "unknown"


def test_scripts_validate_their_params():
    assert re.match(ssh.SCRIPTS["ping_host"][1]["HOST"], "203.0.113.9")
    assert not re.match(ssh.SCRIPTS["ping_host"][1]["HOST"], "x; rm -rf /")
    assert re.match(ssh.ACTIONS["add_pubkey"][1]["PUB"], "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB" + "a" * 40 + " nikatel-user")
    assert not re.match(ssh.ACTIONS["add_pubkey"][1]["PUB"], "ssh-ed25519 AAAA'; echo pwned")
    assert re.match(ssh.ACTIONS["abuse_install"][1]["WL"], "1.1.1.1 2.2.2.2") and re.match(ssh.ACTIONS["abuse_install"][1]["WL"], "")
    assert not re.match(ssh.ACTIONS["abuse_install"][1]["URL"], "https://evil.example/x")


def test_console_presets_and_read_only(client):
    r = client.get("/api/nodes/presets").json()
    assert any(p["key"] == "logs" for p in r["presets"])
    client.post("/api/servers", json={"name": "ir1", "host": "1.1.1.1", "role": "iran"})
    r = client.post("/api/servers/1/console", json={"command": "logs"})
    assert r.status_code == 200 and r.json()["code"] != 0 and "read-only" in r.json()["output"]      # run_raw refused: tests run read-only


def test_add_many_parses_lines(client, monkeypatch):
    monkeypatch.setattr(nodes, "run_script", lambda node, name, params=None, timeout=40: "HOST=srv Linux 5.15")
    r = client.post("/api/servers/add-many", json={"role": "foreign", "lines": "de4 2.2.2.2\nnl2 3.3.3.3 root 2222\nbad name! 4.4.4.4\n"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["ok"] for i in items] == [True, True, False]
    assert items[1]["port"] == 2222 and items[0]["detail"].startswith("srv")
    lst = client.get("/api/servers?role=foreign").json()
    assert sorted(d["name"] for d in lst) == ["de4", "nl2"]
    # the same host again is refused per item, not as a whole
    r = client.post("/api/servers/add-many", json={"role": "foreign", "lines": "de4 2.2.2.2"})
    assert r.json()["items"][0]["ok"] is False and "already" in r.json()["items"][0]["error"]
    with pytest.raises(ValueError):
        nodes.add_many("foreign", "")


def test_launcher_and_abuse_guards(client, monkeypatch):
    client.post("/api/servers", json={"name": "ir1", "host": "1.1.1.1", "role": "iran"})
    client.post("/api/servers", json={"name": "fi1", "host": "2.2.2.2", "role": "foreign"})
    assert client.post("/api/servers/1/abuse", json={"action": "install"}).status_code == 400     # never on Iran
    monkeypatch.setattr(nodes, "run_action", lambda node, name, params=None, timeout=90: "ADDED\n")
    monkeypatch.setattr(nodes, "user_key_ensure", lambda: "ssh-ed25519 AAAAtest nikatel-user")
    r = client.get("/api/servers/2/launcher?app=putty")
    assert r.status_code == 200 and b"putty" in r.content and b"2.2.2.2" in r.content and "open-putty-fi1.bat" in r.headers["content-disposition"]
    assert client.get("/api/servers/2/launcher?app=telnet").status_code == 400
    monkeypatch.setattr(nodes, "run_action", lambda node, name, params=None, timeout=90: "ABUSE_OK=4321\n")
    r = client.post("/api/servers/2/abuse", json={"action": "install"})
    assert r.status_code == 200 and r.json()["rules"] == 4321
    s = client.get("/api/servers/2").json()
    assert s["extra"]["abuse"]["installed"] is True and s["extra"]["abuse"]["rules"] == 4321


def test_the_spoof_test_scripts_send_the_narrowest_rule_and_always_take_it_out():
    """The owner, 2026-10-17 (his method: an SNAT rule on the Iran server, pings to the other server, tcpdump there): the rule is for ICMP echo requests to the target only, tagged, taken out whatever happens."""
    base = {"TARGET": "203.0.113.9", "SPOOF": "10.10.10.10", "CONTROL": "5", "COUNT": "10"}
    text = ssh.render_action("spoof_send", base)
    assert "-d \"$T\" -p icmp --icmp-type echo-request" in text and "--comment \"$tag\"" in text and "-j SNAT --to-source \"$S\"" in text and "trap clean EXIT" in text     # (nothing else the server sends is touched)
    assert text.index("ping -c 5") < text.index("iptables -t nat -I POSTROUTING") < text.index("ping -c 10")                                                           # the ordinary pings first, then the rule, then the forged pings
    assert "-s 87" in text and text.rstrip().endswith('--comment $tag")"') and 'echo "LEFT ' in text                  # (it says at the end whether its rule is really gone)

    for bad in ({"TARGET": "203.0.113.9; reboot"}, {"SPOOF": "ten"}, {"COUNT": "100"}, {"CONTROL": "x"}):
        with pytest.raises(ValueError):
            ssh.render_action("spoof_send", {**base, **bad})

    assert "spoof_send" in ssh.ACTIONS and "spoof_send" not in ssh.SCRIPTS and "spoof_listen" in ssh.SCRIPTS            # (the sender changes a server: an action, refused while the panel is read-only; the listener only reads)
    listen = ssh._script_text("spoof_listen", {"SECS": "22"})
    assert "tcpdump" in listen and "ip[2:2] == 115" in listen and "timeout 22" in listen and "NOTCPDUMP" in listen        # (115 = 20 + 8 + 87: a size no other ping has)

    with pytest.raises(ValueError):
        ssh._script_text("spoof_listen", {"SECS": "22; id"})


def test_the_spoof_verdict_tells_a_provider_that_drops_forged_sources_from_one_that_drops_ping():
    s, real = "10.10.10.10", "198.51.100.1"
    assert nodes.spoof_verdict([s] * 10 + [real] * 5, s)[0] == "yes"
    assert nodes.spoof_verdict([s] * 3 + [real] * 5, s)[0] == "part"
    level, text, forged, got = nodes.spoof_verdict([real] * 5, s)
    assert level == "no" and forged == 0 and got == 5 and "drops packets with a forged source" in text            # the ordinary pings arrived, the forged did not: THIS is "the server does not eat spoofs"
    assert nodes.spoof_verdict([], s)[0] == "blind"                                                                 # nothing at all: ping is filtered (the test cannot say)
    assert nodes.spoof_verdict([real] * 15, s)[0] == "norewrite"                                                    # the source was not rewritten on the sender
    assert nodes.parse_spoof_listen("LISTENING\nPKT 10.10.10.10\nPKT 198.51.100.1\nnoise\nPKT x.y\nDONE\n") == (["10.10.10.10", "198.51.100.1"], "")
    assert "tcpdump is not installed" in nodes.parse_spoof_listen("NOTCPDUMP\n")[1] and "did not start" in nodes.parse_spoof_listen("")[1]


def test_the_spoof_test_runs_both_ends_keeps_the_answer_and_refuses_what_makes_no_sense(client, monkeypatch):
    client.post("/api/servers", json={"name": "ir1", "host": "203.0.113.1", "role": "iran"})
    client.post("/api/servers", json={"name": "fi1", "host": "198.51.100.2", "role": "foreign"})
    seen = []
    monkeypatch.setattr(nodes.time, "sleep", lambda s: None)
    monkeypatch.setattr(nodes, "run_script", lambda node, name, params=None, timeout=40: (seen.append(("listen", node.host, name, dict(params))), "LISTENING\n" + "PKT 10.10.10.10\n" * 9 + "PKT 203.0.113.1\n" * 5 + "DONE\n")[1])
    monkeypatch.setattr(nodes, "run_action", lambda node, name, params=None, timeout=90: (seen.append(("send", node.host, name, dict(params))), "SENT 5 10\nLEFT 0\n")[1])
    r = client.post("/api/servers/1/spoof-test", json={"target_id": 2, "spoof_ip": "10.10.10.10"})
    d = r.json()
    assert r.status_code == 200 and d["ok"] is True and d["can"] is True and d["level"] == "yes" and d["forged"] == 9 and d["real"] == 5 and d["target"] == "fi1" and d["spoof_ip"] == "10.10.10.10"
    assert ("listen", "198.51.100.2", "spoof_listen", {"SECS": "22"}) in seen                                                                   # the target listens ...
    assert ("send", "203.0.113.1", "spoof_send", {"TARGET": "198.51.100.2", "SPOOF": "10.10.10.10", "CONTROL": "5", "COUNT": "10"}) in seen       # ... the tested server sends
    kept = client.get("/api/servers/1").json()["extra"]["spoof"]
    assert kept["level"] == "yes" and kept["target"] == "fi1" and kept["at"] > 0

    for body, why in (({"target_id": 1, "spoof_ip": "10.10.10.10"}, "another server"), ({"target_id": 2, "spoof_ip": "127.0.0.1"}, "ordinary address"), ({"target_id": 2, "spoof_ip": "not-an-ip"}, "IPv4"),
                      ({"target_id": 2, "spoof_ip": "198.51.100.2"}, "one of the two servers"), ({"target_id": 99, "spoof_ip": "10.10.10.10"}, "not found")):
        r = client.post("/api/servers/1/spoof-test", json=body)
        assert r.status_code == 400 and why in r.json()["error"], (body, r.text)

    monkeypatch.setattr(nodes, "run_script", lambda node, name, params=None, timeout=40: "NOTCPDUMP\n")
    r = client.post("/api/servers/1/spoof-test", json={"target_id": 2, "spoof_ip": "10.10.10.10"})
    assert r.status_code == 400 and "tcpdump is not installed" in r.json()["error"]
    monkeypatch.setattr(nodes, "run_script", lambda node, name, params=None, timeout=40: "LISTENING\nPKT 198.51.100.9\nDONE\n")
    monkeypatch.setattr(nodes, "run_action", lambda node, name, params=None, timeout=90: "SENT 5 10\nLEFT 1\n")
    r = client.post("/api/servers/1/spoof-test", json={"target_id": 2, "spoof_ip": "10.10.10.10"})
    assert r.status_code == 200 and r.json()["level"] == "no" and r.json()["can"] is False and "could not be removed" in r.json()["verdict"] and "nikatel-spoof-test" in r.json()["verdict"]      # a rule that stayed is said, with the command


def test_a_new_server_gets_what_the_spoof_test_needs_before_the_test_starts(client, monkeypatch):
    """The owner, 2026-10-19 (tr-test, a new Debian 12 machine): "tr-test: iptables is not installed on the sending server". The test is something the owner starts, so what is missing (iptables and ping to send, tcpdump
    to listen) is installed with apt - BEFORE the listener starts, so that a slow apt does not eat its listening time; and what cannot be installed is said with the command."""
    client.post("/api/servers", json={"name": "ir1", "host": "203.0.113.1", "role": "iran"})
    client.post("/api/servers", json={"name": "fi1", "host": "198.51.100.2", "role": "foreign"})
    seen = []
    monkeypatch.setattr(nodes.time, "sleep", lambda s: None)
    monkeypatch.setattr(nodes, "run_script", lambda node, name, params=None, timeout=40: (seen.append(("listen", node.host)), "LISTENING\n" + "PKT 10.10.10.10\n" * 9 + "PKT 203.0.113.1\n" * 5 + "DONE\n")[1])

    def act(node, name, params=None, timeout=90):
        seen.append((name, node.host, (params or {}).get("ROLE")))
        return {"spoof_prepare": "TRIED iptables iputils-ping\nREADY\n" if (params or {}).get("ROLE") == "send" else "TRIED tcpdump\nREADY\n"}.get(name, "SENT 5 10\nLEFT 0\n")

    monkeypatch.setattr(nodes, "run_action", act)
    d = client.post("/api/servers/1/spoof-test", json={"target_id": 2, "spoof_ip": "10.10.10.10"}).json()
    assert d["ok"] is True and "iptables iputils-ping was missing on ir1 and was installed for this test" in d["verdict"] and "tcpdump was missing on fi1 and was installed for this test" in d["verdict"]
    assert [x[:2] for x in seen if x[0] == "spoof_prepare"] == [("spoof_prepare", "203.0.113.1"), ("spoof_prepare", "198.51.100.2")]
    assert seen.index(("spoof_prepare", "198.51.100.2", "listen")) < seen.index(("listen", "198.51.100.2")) < seen.index(("spoof_send", "203.0.113.1", None))           # both ends are ready before the listener starts and before the sender sends

    for answer, words in (("TRIED iptables\nNOIPTABLES\n", ("ir1: iptables is not installed", "could not be installed", "apt install iptables")), ("NOPING\n", ("ir1: ping is not installed", "iputils-ping")), ("NOTCPDUMP\n", ("tcpdump is not installed", "apt install tcpdump"))):
        monkeypatch.setattr(nodes, "run_action", lambda node, name, params=None, timeout=90, a=answer: a if name == "spoof_prepare" else "SENT 5 10\nLEFT 0\n")
        r = client.post("/api/servers/1/spoof-test", json={"target_id": 2, "spoof_ip": "10.10.10.10"})
        assert r.status_code == 400 and all(w in r.json()["error"] for w in words), (answer, r.text)


def test_the_spoof_prepare_action_installs_only_what_is_missing_for_its_role():
    send, listen = ssh.render_action("spoof_prepare", {"ROLE": "send"}), ssh.render_action("spoof_prepare", {"ROLE": "listen"})
    assert "role='send'" in send and "role='listen'" in listen and "apt-get install -y -qq $need" in send and "command -v iptables" in send and "command -v tcpdump" in send     # (the same text, the role decides)
    assert "spoof_prepare" in ssh.ACTIONS and "spoof_prepare" not in ssh.SCRIPTS                                            # (it can install a package: an action, refused while the panel is read-only)

    for bad in ("sender", "send; reboot", ""):
        with pytest.raises(ValueError):
            ssh.render_action("spoof_prepare", {"ROLE": bad})

def _run_prepare(tmp_path, name, role, have, apt):
    # the script of spoof_prepare in a real bash whose PATH holds nothing but stand-ins (the script itself uses builtins only); apt: None (no apt-get), "works" (it makes the tools it is asked for) or "fails"
    bin_ = tmp_path / name
    bin_.mkdir()

    def stub(tool, text):
        (bin_ / tool).write_text("#!/bin/sh\n" + text + "\n")
        (bin_ / tool).chmod(0o755)

    stub("ok", "exit 0")
    for tool in have:
        stub(tool, "exit 0")
    if apt:
        make = f'case "$p" in iptables|tcpdump) cp "{bin_}/ok" "{bin_}/$p";; iputils-ping) cp "{bin_}/ok" "{bin_}/ping";; esac' if apt == "works" else ":"
        stub("apt-get", f'PATH=/usr/bin:/bin\necho "$@" >> "{bin_}/apt.log"\nfor p in "$@"; do {make}; done\n' + ("exit 0" if apt == "works" else "exit 100"))
    r = subprocess.run([shutil.which("bash"), "-c", ssh.render_action("spoof_prepare", {"ROLE": role})], check=False, env={"PATH": str(bin_)}, capture_output=True, text=True, timeout=30)
    log = bin_ / "apt.log"
    return r.stdout.split(), (log.read_text() if log.exists() else "")


def test_the_spoof_prepare_script_run_in_a_real_shell_with_stand_ins_for_the_tools(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("no bash here")
    cases = [
        ("send", {"iptables", "ping"}, "works", ["READY"], None),                                                            # all there: apt is not even called
        ("send", {"ping"}, "works", ["TRIED", "iptables", "READY"], "install -y -qq iptables"),
        ("send", set(), "works", ["TRIED", "iptables", "iputils-ping", "READY"], "install -y -qq iptables iputils-ping"),    # a new Debian 12
        ("send", set(), None, ["NOIPTABLES"], None),                                                                         # no apt: told, not guessed
        ("send", {"iptables"}, None, ["NOPING"], None),
        ("listen", set(), "works", ["TRIED", "tcpdump", "READY"], "install -y -qq tcpdump"),
        ("listen", {"iptables", "ping"}, "works", ["TRIED", "tcpdump", "READY"], "install -y -qq tcpdump"),                  # the listener needs tcpdump only
        ("listen", {"tcpdump"}, "works", ["READY"], None),
        ("listen", set(), "fails", ["TRIED", "tcpdump", "NOTCPDUMP"], "install -y -qq tcpdump"),                             # apt tried and failed
    ]
    for i, (role, have, apt, answer, asked) in enumerate(cases):
        got, log = _run_prepare(tmp_path, f"c{i}", role, have, apt)
        assert got == answer, (i, role, got)
        assert (asked in log) if asked else (log == ""), (i, log)


def test_gre_links_are_read_from_the_scan():
    out = "BH iran-tcpmux-9900 server 10.1.2.1:9900 tcpmux 80,\nGRE fi-ir1 10.1.2.1/30 203.0.113.9 active\nGRE gre0 - - none\nEND\n"
    assert nodes.parse_gre_links(out) == [{"iface": "fi-ir1", "addr": "10.1.2.1", "peer": "10.1.2.2", "unit": "active"}]
    assert nodes.parse_gre_links("END") == []
