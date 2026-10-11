"""Disturbances of the network itself: the level of an Iran server's connectivity, what the tunnels show together, the hidden temporary tunnels."""
from nikatel.models import is_temp
from nikatel.services import netwatch


def test_net_level():
    assert netwatch.net_level(0, [0, 0], [0, 0]) == "ok" and netwatch.net_level(0, [3, 0], [5, 2]) == "ok"
    assert netwatch.net_level(0, [9, 0], [2, 0]) == "weak" and netwatch.net_level(25, [0], [0, 0]) == "bad"
    assert netwatch.net_level(0, [0, 0], [30, 40]) == "bad" and netwatch.net_level(0, [40, 50], [0]) == "bad"              # most targets lossy
    assert netwatch.net_level(0, [0, 0], [100, 0]) == "weak"                                                                # one dead target of two is that target's own trouble
    assert netwatch.net_level(0, [0, 0], [100, 0, 60]) == "bad" and netwatch.net_level(0, [], [70]) == "bad" and netwatch.net_level(0, [], [40]) == "weak"
    assert netwatch.net_level(100, [0], [0]) == "down" and netwatch.net_level(None, [], []) == "unknown"
    assert netwatch.net_level(None, [0], [2]) == "ok"                                                                       # an anchor that did not answer ping is not a cut by itself


def test_the_tunnels_together_tell_which_server_is_disturbed():
    servers = [{"id": 1, "name": "i1", "role": "iran"}, {"id": 2, "name": "i2", "role": "iran"}, {"id": 10, "name": "f1", "role": "foreign"}, {"id": 11, "name": "f2", "role": "foreign"}]
    t = lambda n, i, f, loss, st="up": {"name": n, "iran_id": i, "foreign_id": f, "loss": loss, "state": st}
    tunnels = [t("a", 1, 10, 25), t("b", 1, 11, 30), t("c", 1, 10, 12), t("d", 2, 11, 0), t("e", 2, 10, 2)]
    out = netwatch.passive(servers, tunnels)
    assert sorted((o["kind"], o["server"]) for o in out) == [("foreign_server", "f1"), ("iran_server", "i1")]            # i1: 3 of 3 lossy; f1: 2 of its 3 (67 %); f2: 1 of 2 is not enough
    assert next(o for o in out if o["server"] == "i1")["tunnels"] == ["a", "b", "c"]
    assert netwatch.passive(servers, [t("a", 1, 10, 25), t("d", 2, 11, 0)]) == []                                                # one lossy tunnel alone is its own route
    down = netwatch.passive(servers, [t("a", 1, 10, 0, "down"), t("b", 2, 10, 0, "down"), t("c", 2, 11, 0)])
    assert any(o["server"] == "f1" and o["kind"] == "foreign_server" for o in down)                                              # both tunnels to f1 are down


def test_temporary_test_tunnels_are_hidden(client):
    from nikatel.db import session
    from nikatel.models import Server, Tunnel

    assert is_temp("test-1") and not is_temp("fi1-ir1")

    with session() as db:
        a, b = Server(name="i1", host="10.0.0.1", role="iran"), Server(name="f1", host="10.0.0.2", role="foreign")
        db.add_all([a, b])
        db.flush()
        db.add(Tunnel(name="real", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id))
        db.add(Tunnel(name="test-1", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, enabled=False))

    assert [t["name"] for t in client.get("/api/tunnels").json()] == ["real"]
    assert client.get("/api/netwatch").json()["items"] == []


def test_a_foreign_server_is_called_bad_only_when_most_iran_servers_lose_packets_toward_it():
    from nikatel.services.netwatch import reach_level

    now = 1_000_000
    s = [{"iran": "ir1", "loss": 85.0, "at": now - 60}, {"iran": "ir2", "loss": 90.0, "at": now - 300}, {"iran": "ir3", "loss": 0.0, "at": now - 200}]
    v = reach_level(s, now)
    assert v["level"] == "bad" and v["bad"] == 2 and v["total"] == 3 and v["from"] == ["ir1", "ir2"] and v["worst"] == 90.0
    assert reach_level([{"iran": "ir1", "loss": 85.0, "at": now}], now)["level"] != "bad"                # one Iran server alone: its own trouble, never "bad"
    assert reach_level([{"iran": "ir1", "loss": 85.0, "at": now - 2000}, {"iran": "ir2", "loss": 85.0, "at": now - 2000}], now)["level"] == "unknown"   # stale
    assert reach_level([{"iran": "ir1", "loss": 10.0, "at": now}, {"iran": "ir2", "loss": 0.0, "at": now}], now)["level"] == "weak"