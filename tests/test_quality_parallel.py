"""The quality test of every tunnel in parallel (1.69.291; the owner, 2026-11-19, «a complete parallelization of the Doctor's tests»). On the live fleet the job `quality` took 156 seconds for 24 tunnels, one after the other - the
only background test that was fully serial. Now every Iran server is a lane: its tunnels one after the other (a small server is never asked for two measurements at once), the servers in parallel, the answer in the old order."""
import threading
import time

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import tunnel_test


def _world(per_server):
    """One Iran server per entry of `per_server` (the number of tunnels on it), one foreign server for all; returns [(tunnel id, iran server id)] in creation order."""
    out = []

    with session() as db:
        fo = Server(name="fo", host="203.0.113.99", role="foreign", online=True)
        db.add(fo)
        db.flush()

        for si, n in enumerate(per_server):
            ir = Server(name=f"ir{si}", host=f"203.0.113.{10 + si}", role="iran", online=True)
            db.add(ir)
            db.flush()

            for k in range(n):
                t = Tunnel(name=f"t{si}x{k}", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fo.id, state="up", forwarded_ports=[11000 + si * 10 + k])
                db.add(t)
                db.flush()
                out.append((t.id, ir.id))

    return out


def test_the_tunnels_of_one_iran_server_make_one_lane_in_the_order_they_were_seen():
    assert tunnel_test.lanes_by_server([(1, 10), (2, 11), (3, 10), (4, 12), (5, 11)]) == [[1, 3], [2, 5], [4]]
    assert tunnel_test.lanes_by_server([]) == []
    assert tunnel_test.lanes_by_server([(7, 3)]) == [[7]]


def test_servers_are_measured_at_once_but_never_two_tunnels_of_the_same_server(monkeypatch):
    rows = _world([3, 2, 2])
    sid_of = dict(rows)
    lock = threading.Lock()
    live, peak_server, peak_all = {}, {}, [0]

    def fake(tid, speed=True):
        sid = sid_of[tid]

        with lock:
            live[sid] = live.get(sid, 0) + 1
            peak_server[sid] = max(peak_server.get(sid, 0), live[sid])
            peak_all[0] = max(peak_all[0], sum(live.values()))

        time.sleep(0.05)

        with lock:
            live[sid] -= 1

        return {"name": f"t{tid}", "score": 80}

    monkeypatch.setattr(tunnel_test, "quality", fake)
    out = tunnel_test.quality_all(speed=False)
    assert [r["name"] for r in out] == [f"t{tid}" for tid, _sid in rows]                          # (the old order: the order of the tunnels, not of whoever finished first)
    assert max(peak_server.values()) == 1                                                          # (one measurement at a time on a server)
    assert peak_all[0] >= 2                                                                        # (and the servers really ran at once)


def test_a_tunnel_that_cannot_be_measured_is_a_result_and_the_others_still_are(monkeypatch):
    rows = _world([2, 1])

    def fake(tid, speed=True):
        if tid == rows[0][0]:
            raise ValueError("this tunnel has no GRE link or test door to measure")

        if tid == rows[2][0]:
            raise RuntimeError("no data passes through the tunnel right now")

        return {"name": f"t{tid}", "score": 70}

    monkeypatch.setattr(tunnel_test, "quality", fake)
    out = tunnel_test.quality_all(speed=False)
    assert len(out) == 3
    assert out[0] == {"id": rows[0][0], "error": "this tunnel has no GRE link or test door to measure"}
    assert out[1] == {"name": f"t{rows[1][0]}", "score": 70}
    assert out[2]["id"] == rows[2][0] and "no data passes" in out[2]["error"]


def test_no_tunnel_is_no_work_and_a_disabled_one_is_not_measured(monkeypatch):
    seen = []
    monkeypatch.setattr(tunnel_test, "quality", lambda tid, speed=True: seen.append(tid) or {"name": "x"})
    assert tunnel_test.quality_all(speed=False) == [] and seen == []
    rows = _world([2])

    with session() as db:
        db.get(Tunnel, rows[1][0]).enabled = False

    assert [r["name"] for r in tunnel_test.quality_all(speed=False)] == ["x"] and seen == [rows[0][0]]


def test_the_speed_flag_reaches_every_measurement(monkeypatch):
    got = []
    _world([2, 1])
    monkeypatch.setattr(tunnel_test, "quality", lambda tid, speed=True: got.append(speed) or {"name": "x"})
    tunnel_test.quality_all(speed=False)
    tunnel_test.quality_all(speed=True)
    assert got == [False] * 3 + [True] * 3
