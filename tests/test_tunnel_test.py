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
