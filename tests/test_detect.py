import re

import pytest

from nikatel import ssh
from nikatel.services import builder, detect
from nikatel.services.detect import parse_scan

IRAN = "BH iran-tcpmux-9905 server 10.5.5.1:9905 tcpmux 443,8443=127.0.0.1:2096\nGRE de3-ir1 10.5.5.1/30 2.2.2.2 active\nEND\n"
FOREIGN = "BH kharej-tcpmux-9905 client 10.5.5.1:9905 tcpmux none\nGRE de3-ir1 10.5.5.2/30 1.1.1.1 active\nEND\n"


def test_parse_scan():
    d = parse_scan(IRAN)
    assert d["bh"][0]["port"] == 9905 and d["bh"][0]["ports"] == ["443", "8443=127.0.0.1:2096"] and d["gre"][0]["ip"] == "10.5.5.1"


def test_pair_and_adopt(client, monkeypatch):
    client.post("/api/servers", json={"name": "ir1", "host": "1.1.1.1", "role": "iran"})
    client.post("/api/servers", json={"name": "de3", "host": "2.2.2.2", "role": "foreign"})
    monkeypatch.setattr(detect, "run_script", lambda node, name, params=None, timeout=30: IRAN if node.host == "1.1.1.1" else FOREIGN)
    d = client.get("/api/tunnels/detect").json()
    assert d["scanned"] == 2 and len(d["pairs"]) == 1
    p = d["pairs"][0]
    assert p["paired"] and p["name"] == "de3-ir1" and p["foreign_name"] == "de3" and p["iran_tunnel_ip"] == "10.5.5.1" and p["foreign_tunnel_ip"] == "10.5.5.2" and p["in_panel"] is False
    r = client.post("/api/tunnels/adopt", json=p)
    assert r.status_code == 200 and r.json()["name"] == "de3-ir1"
    t = client.get("/api/tunnels").json()[0]
    assert t["port"] == 9905 and t["forwarded_ports"] == [443, 8443] and t["transport"] == "tcpmux"
    assert client.get("/api/tunnels/detect").json()["pairs"][0]["in_panel"] is True
    assert client.post("/api/tunnels/adopt", json=p).status_code == 400


def test_set_ports_regexes_and_record(client, monkeypatch):
    assert re.match(ssh.ACTIONS["set_ports"][1]["CFG"], "iran-tcpmux-9905") and not re.match(ssh.ACTIONS["set_ports"][1]["CFG"], "../etc/passwd")
    client.post("/api/servers", json={"name": "ir1", "host": "1.1.1.1", "role": "iran"})
    client.post("/api/servers", json={"name": "de3", "host": "2.2.2.2", "role": "foreign"})
    from nikatel.db import session
    from nikatel.models import Tunnel

    with session() as db:
        db.add(Tunnel(name="de3-ir1", type="backhaul", transport="tcpmux", iran_server_id=1, foreign_server_id=2, port=9905, forwarded_ports=[443]))

    seen = {}
    monkeypatch.setattr(builder, "run_action", lambda node, name, params=None, timeout=90: seen.update(params) or "PORTS_OK\n")
    r = client.put("/api/tunnels/1/ports", json={"ports": "443, 8443=2096, 10000-10001"})
    assert r.status_code == 200 and r.json()["ports"] == [443, 8443, 10000, 10001] and seen["CFG"] == "iran-tcpmux-9905" and seen["CHECK"] == "443 8443 10000 10001"
    assert client.get("/api/tunnels").json()[0]["forwarded_ports"] == [443, 8443, 10000, 10001]
    monkeypatch.setattr(builder, "run_action", lambda node, name, params=None, timeout=90: "NOTLISTEN\n")
    assert client.put("/api/tunnels/1/ports", json={"ports": "9"}).status_code == 502
    assert client.put("/api/tunnels/1/ports", json={"ports": "abc"}).status_code == 400


def test_auto_iran_ranking(client, monkeypatch):
    client.post("/api/servers", json={"name": "ir1", "host": "1.1.1.1", "role": "iran"})
    client.post("/api/servers", json={"name": "ir2", "host": "3.3.3.3", "role": "iran"})
    client.post("/api/servers", json={"name": "de3", "host": "2.2.2.2", "role": "foreign"})
    from nikatel.db import session
    from nikatel.models import Server

    with session() as db:
        for s in db.scalars(__import__("sqlalchemy").select(Server)):
            s.online = True
            s.facts = {"cpu": 10 if s.name == "ir1" else 80, "ram": 30, "load1": 0.5, "cores": 4}

    pings = {"1.1.1.1": "4 packets transmitted, 4 received, 0% packet loss\nrtt min/avg/max/mdev = 50/60/70/5 ms", "3.3.3.3": "4 packets transmitted, 2 received, 50% packet loss\nrtt min/avg/max/mdev = 90/95/99/2 ms"}
    monkeypatch.setattr(detect, "run_script", lambda node, name, params=None, timeout=30: pings[node.host])
    r = client.post("/api/tunnels/auto-iran", json={"foreign_id": 3, "ports": "443"}).json()["ranking"]
    assert r[0]["name"] == "ir1" and r[0]["score"] > r[1]["score"] and r[1]["loss_pct"] == 50
    with pytest.raises(ValueError):
        detect.auto_iran(1)
