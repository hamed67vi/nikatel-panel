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
    monkeypatch.setattr(builder, "delete_tunnel", lambda tid, keep_record=False, trash=False: gone.append(tid) or ["Iran: removed", "foreign: removed"])
    tunnel_test._cleanup(aid, "nkt1", ia, ib)

    with session() as db:
        assert {db.get(Tunnel, t).name for t in gone} == {"f1-i1-2"}
        assert "Removed the temporary tunnel" in db.get(ActionLog, aid).story[-1]


def test_loss_levels_and_the_full_check_report(client, monkeypatch):
    from nikatel.config import get_settings
    from nikatel.db import session
    from nikatel.models import Server, Tunnel
    from nikatel.services import actions, tunnel_test

    assert [tunnel_test.loss_level(x) for x in (None, 0, 2.9, 3, 9.9, 10, 40)] == ["", "", "", "some", "some", "high", "high"]

    with session() as db:
        a, b = Server(name="i1", host="10.0.0.1", role="iran"), Server(name="f1", host="10.0.0.2", role="foreign")
        db.add_all([a, b])
        db.flush()
        ids = []

        for n, st in (("t-up", "up"), ("t-down", "down"), ("t-lossy", "up")):
            t = Tunnel(name=n, type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, state=st, detail={})
            db.add(t)
            db.flush()
            ids.append(t.id)

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(actions, "health_all", lambda: [{"id": i, "name": n, "state": s, "why": "x" if s == "down" else ""} for i, n, s in zip(ids, ("t-up", "t-down", "t-lossy"), ("up", "down", "up"))])
    quality = {ids[0]: {"latency_ms": 90.0, "jitter_ms": 1.0, "loss_pct": 0.0}, ids[2]: {"latency_ms": 120.0, "jitter_ms": 9.0, "loss_pct": 22.0}}
    monkeypatch.setattr(tunnel_test, "quality", lambda tid: quality[tid] if tid in quality else (_ for _ in ()).throw(ValueError("no link")))
    r = client.post("/api/tunnels/full-check").json()
    assert (r["up"], r["down"], r["high_loss"]) == (2, 1, 1)
    assert [x["name"] for x in r["rows"]] == ["t-down", "t-lossy", "t-up"]                                   # worst first: down, then the lossy one, then the clean one
    lossy = next(x for x in r["rows"] if x["name"] == "t-lossy")
    assert lossy["loss"] == "high" and lossy["loss_pct"] == 22.0
    assert next(x for x in r["rows"] if x["name"] == "t-down")["measured"] is False


def test_purge_test_leftovers_action_is_scoped_to_test_units_and_the_test_port():
    import pytest

    from nikatel.ssh import render_action

    s = render_action("purge_test_leftovers", {"TESTPORT": "39400"})
    assert "nikatel-gre-test-*" in s and "backpack-test-*" in s and "39400" in s and "daemon-reload" in s
    assert "/etc/backhaul/*.toml" in s          # a backhaul unit is removed only when its config forwards the test port

    with pytest.raises(ValueError):
        render_action("purge_test_leftovers", {"TESTPORT": "39400; reboot"})

def test_failed_kinds_are_explained_in_plain_words():
    from nikatel.services.tunnel_test import explain_failure

    assert "TLS variant" in explain_failure("bp:ws", "Build failed: no data passes through it")
    assert "QUIC is blocked" in explain_failure("bpd:quic", "the tunnel network does not come up")
    assert explain_failure("tcpmux", "port busy") == "port busy" and explain_failure("bp:ws", "") == ""

def test_stop_cancels_a_build_at_its_next_step():
    import pytest

    from nikatel.services import builder

    builder.CANCEL.add(987654)

    try:
        with pytest.raises(builder.BuildCancelled):
            builder._say(987654, "any step")
    finally:
        builder.CANCEL.discard(987654)