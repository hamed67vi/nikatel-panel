import re

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
    assert verdict([ok, {"ok": 2, "total": 4, "pending": False}])[0] == "filtered"
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


def test_gre_links_are_read_from_the_scan():
    out = "BH iran-tcpmux-9900 server 10.1.2.1:9900 tcpmux 80,\nGRE fi-ir1 10.1.2.1/30 203.0.113.9 active\nGRE gre0 - - none\nEND\n"
    assert nodes.parse_gre_links(out) == [{"iface": "fi-ir1", "addr": "10.1.2.1", "peer": "10.1.2.2", "unit": "active"}]
    assert nodes.parse_gre_links("END") == []
