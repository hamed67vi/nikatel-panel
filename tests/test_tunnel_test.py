from nikatel.services.tunnel_test import jitter_from_pings, score
from nikatel.ssh import render_action

PING = """64 bytes from 10.9.9.2: icmp_seq=1 ttl=64 time=80.1 ms
64 bytes from 10.9.9.2: icmp_seq=2 ttl=64 time=82.3 ms
64 bytes from 10.9.9.2: icmp_seq=3 ttl=64 time=78.9 ms
64 bytes from 10.9.9.2: icmp_seq=4 ttl=64 time=81.0 ms
5 packets transmitted, 4 received, 20% packet loss, time 804ms
"""


def test_jitter_and_loss():
    avg, jitter, loss = jitter_from_pings(PING)
    assert avg == 80.6 and 1.0 <= jitter <= 1.3 and loss == 20.0
    assert jitter_from_pings("5 packets transmitted, 0 received, 100% packet loss") == (None, None, 100.0)


def test_score_shape():
    assert score(None, None, None, None) == 0
    assert score(40, 0, 0, 800) == 100
    assert score(40, 0, 0, 0) == 60
    assert score(300, 20, 50, 10) < 30


def test_speed_sink_is_validated():
    assert "sendall" in render_action("speed_sink", {"PORT": "39400"})
    try:
        render_action("speed_sink", {"PORT": "39400; id"})
        raise AssertionError("should refuse")
    except ValueError:
        pass


def test_tunnel_test_api_read_only(client):
    r = client.post("/api/tunnel-test", json={"iran_id": 1, "foreign_id": 2})
    assert r.status_code == 400 and "read-only" in r.json()["detail"]


def test_cleanup_also_removes_a_renamed_temporary_tunnel(monkeypatch):
    from nikatel.db import session
    from nikatel.models import ActionLog, Server, Tunnel
    from nikatel.services import builder, tunnel_test

    with session() as db:
        a, b = Server(name="i1", host="10.0.0.1", role="iran"), Server(name="f1", host="10.0.0.2", role="foreign")
        db.add_all([a, b])
        db.flush()
        db.add(Tunnel(name="f1-i1-2", type="backpack", iran_server_id=a.id, foreign_server_id=b.id, forwarded_ports=[tunnel_test.TEST_PORT]))   # the test's tunnel under another name
        db.add(Tunnel(name="f1-i1", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, forwarded_ports=[80]))                           # a real one
        e = ActionLog(ref="nkt1", action="tunnel_test", source="manual", story=[])
        db.add(e)
        db.flush()
        aid, ia, ib = e.id, a.id, b.id

    gone = []
    monkeypatch.setattr(builder, "delete_tunnel", lambda tid, keep_record=False: gone.append(tid) or ["Iran: removed", "foreign: removed"])
    tunnel_test._cleanup(aid, "nkt1", ia, ib)

    with session() as db:
        assert {db.get(Tunnel, t).name for t in gone} == {"f1-i1-2"}
        assert "Removed the temporary tunnel" in db.get(ActionLog, aid).story[-1]
