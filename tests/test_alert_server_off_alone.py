"""One message for a server that goes off (the owner, 2026-10-23, a test server switched off sent five messages: the server, a node of a PasarGuard panel, a tunnel ...). Only the server is told; its tunnels, the configs
that point at its address and the nodes that point at it stay quiet while it is off. A tunnel that is cut with both of its servers up is still told with its reason, and the way back is the server's UP only."""
import pytest

from nikatel.db import session
from nikatel.models import Config, Panel, Server, Tunnel
from nikatel.services import alerts


@pytest.fixture
def telegram(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: sent.append(text) or (True, "sent"))
    alerts._OUTBOX.clear()
    alerts._NEXT_TRY["at"] = 0.0
    yield sent
    alerts._OUTBOX.clear()


def _world(n):
    with session() as db:
        ir, fo = Server(name=f"ir-so{n}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fo-so{n}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fo])
        db.flush()
        db.add(Tunnel(name=f"t-so{n}", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fo.id, state="up", enabled=True, forwarded_ports=[8075]))
        db.add(Config(name=f"cfg-so{n}", status="up", status_since=1, meta={"address": f"198.51.100.{n}"}))
        db.add(Panel(name=f"pg-so{n}", type="pasarguard", url="https://panel.example.com", status="up", status_since=1, enabled=True,
                     nodes=[{"name": "node1", "address": f"198.51.100.{n}", "status": "connected", "message": "", "fails": 0}]))
        return {"ir": ir.id, "fo": fo.id}


def _fall(n, ids):
    """The foreign server goes off and everything that depends on it falls with it."""
    with session() as db:
        s = db.get(Server, ids["fo"])
        s.online, s.last_error = False, "no answer on the SSH port 22: 2 quick checks in a row got no connection"
        t = db.query(Tunnel).filter(Tunnel.name == f"t-so{n}").one()
        t.state, t.state_since, t.last_error = "down", 1000, "the foreign server is off or does not answer"
        c = db.query(Config).filter(Config.name == f"cfg-so{n}").one()
        c.status, c.status_since, c.last_error = "down", 1000, "no answer through it"
        p = db.query(Panel).filter(Panel.name == f"pg-so{n}").one()
        p.nodes = [{"name": "node1", "address": f"198.51.100.{n}", "status": "error", "message": "Request timed out", "fails": 3}]


def test_a_server_that_goes_off_is_ONE_message_and_its_tunnel_config_and_node_say_nothing(telegram):
    ids = _world(141)
    alerts.run(now=900)
    _fall(141, ids)
    alerts.run(now=1000)
    alerts.run(now=1015)
    alerts.run(now=1030)
    alerts.run(now=1060)
    assert len(telegram) == 1 and telegram[0].startswith("🔴 DOWN (1)\n") and "server fo-so141" in telegram[0]
    assert "t-so141" not in telegram[0] and "cfg-so141" not in telegram[0] and "node1" not in telegram[0] and "pg-so141" not in telegram[0]


def test_the_way_back_is_the_server_only_and_a_thing_that_is_still_bad_is_told_when_the_server_is_back(telegram):
    ids = _world(142)
    alerts.run(now=900)
    _fall(142, ids)

    for now in (1000, 1015, 1030):
        alerts.run(now=now)

    telegram.clear()

    with session() as db:
        db.get(Server, ids["fo"]).online = True
        c = db.query(Config).filter(Config.name == "cfg-so142").one()
        c.status = "up"
        p = db.query(Panel).filter(Panel.name == "pg-so142").one()
        p.nodes = [{"name": "node1", "address": "198.51.100.142", "status": "connected", "message": "", "fails": 0}]
        # the tunnel is still down after the server is back: it is judged again now and told with its own reason

    for now in (1100, 1115, 1130, 1160):
        alerts.run(now=now)

    text = "\n".join(telegram)
    assert "UP again" in text and "server fo-so142" in text and "t-so142" in text and "the foreign server is off or does not answer" in text
    assert "node1" not in text and "cfg-so142" not in text


def test_a_tunnel_that_is_cut_with_both_servers_up_is_still_told_with_its_reason(telegram):
    ids = _world(143)
    alerts.run(now=900)

    with session() as db:
        t = db.query(Tunnel).filter(Tunnel.name == "t-so143").one()
        t.state, t.state_since, t.last_error = "down", 1000, "port 8075 accepts connections but no data comes back through the tunnel"

    for now in (1000, 1015, 1030, 1060):
        alerts.run(now=now)

    assert len(telegram) == 1 and "t-so143" in telegram[0] and "no data comes back through the tunnel" in telegram[0]
    assert ids["fo"] > 0
