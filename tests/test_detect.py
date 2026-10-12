import re

import pytest

from nikatel import ssh
from nikatel.services import builder, detect


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


def test_gre_not_ok_is_always_last_and_a_cut_tunnel_to_the_same_foreign_goes_after_clean_ones(client, monkeypatch):
    for n, h in (("ir1", "1.1.1.1"), ("ir2", "3.3.3.3"), ("ir3", "4.4.4.4")):
        client.post("/api/servers", json={"name": n, "host": h, "role": "iran"})

    client.post("/api/servers", json={"name": "de3", "host": "2.2.2.2", "role": "foreign"})
    from sqlalchemy import select

    from nikatel.db import session
    from nikatel.models import Server, Tunnel

    good = "4 packets transmitted, 4 received, 0% packet loss\nrtt min/avg/max/mdev = 50/60/70/5 ms"
    meh = "4 packets transmitted, 4 received, 0% packet loss\nrtt min/avg/max/mdev = 150/160/170/5 ms"
    pings = {"1.1.1.1": good, "3.3.3.3": good, "4.4.4.4": meh}      # ir1 and ir2 are better than ir3 in every way

    with session() as db:
        for s in db.scalars(select(Server)):
            s.online = True
            s.facts = {"cpu": 5 if s.name == "ir1" else 10 if s.name == "ir2" else 60, "ram": 30, "load1": 0.5, "cores": 4}

            if s.name == "ir1":
                s.extra = {"gre": {"ok": False, "links": 2, "bad": ["gre1"]}}          # GRE failing

        ids = {s.name: s.id for s in db.scalars(select(Server))}
        db.add(Tunnel(name="de3-ir2", type="backhaul", transport="tcpmux", iran_server_id=ids["ir2"], foreign_server_id=ids["de3"], port=9905, forwarded_ports=[443], state="down", last_error="peer does not answer"))

    monkeypatch.setattr(detect, "run_script", lambda node, name, params=None, timeout=30: pings[node.host])
    r = detect.auto_iran(ids["de3"], "8443")
    assert [x["name"] for x in r] == ["ir3", "ir2", "ir1"]                                       # clean first, then the cut path, GRE not OK always last
    assert r[2]["gre_bad"] and r[1]["prev_cut"] and not r[0]["gre_bad"] and "GRE not OK" in r[2]["reason"] and "is down" in r[1]["reason"]
    assert detect.rank_key({"score": 90, "gre_bad": True, "quality": 100, "resources": 100}) > detect.rank_key({"score": 10, "gre_bad": False, "quality": 10, "resources": 0})


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
