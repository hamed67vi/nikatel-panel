"""The engine check runs in the background: the API answers at once, one check at a time, and the result shows when it is done."""
import threading
import time

from nikatel.services import engines


def test_the_check_is_started_not_awaited(client, monkeypatch):
    calls, gate = [], threading.Event()

    def slow_check():
        calls.append(time.time())
        gate.wait(10)                                                                              # (held until the test lets it go: the proof does not depend on the speed of the machine)
        engines._STATE["result"], engines._STATE["checked_at"] = {"backhaul": {"title": "Backhaul", "nodes": []}}, int(time.time())
        return {"engines": engines._STATE["result"], "checked_at": engines._STATE["checked_at"]}

    monkeypatch.setattr(engines, "check", slow_check)
    engines._STATE.update(result=None, checked_at=0, checking=False)
    t0 = time.time()
    r = client.post("/api/engines/check")
    assert r.status_code == 202 and r.json()["started"] is True and not gate.is_set() and time.time() - t0 < 5       # it did not wait for the servers: the check is still held at the gate
    assert client.get("/api/engines").json()["checking"] is True
    assert client.post("/api/engines/check").json()["started"] is False                            # one check at a time
    gate.set()

    for _ in range(100):
        d = client.get("/api/engines").json()

        if not d["checking"]:
            break

        time.sleep(0.05)

    assert d["checking"] is False and "backhaul" in d["engines"] and len(calls) == 1
