"""The engine check runs in the background: the API answers at once, one check at a time, and the result shows when it is done."""
import time

from nikatel.services import engines


def test_the_check_is_started_not_awaited(client, monkeypatch):
    calls = []

    def slow_check():
        calls.append(time.time())
        time.sleep(1.5)
        engines._STATE["result"], engines._STATE["checked_at"] = {"backhaul": {"title": "Backhaul", "nodes": []}}, int(time.time())
        return {"engines": engines._STATE["result"], "checked_at": engines._STATE["checked_at"]}

    monkeypatch.setattr(engines, "check", slow_check)
    engines._STATE.update(result=None, checked_at=0, checking=False)
    t0 = time.time()
    r = client.post("/api/engines/check")
    assert r.status_code == 202 and r.json()["started"] is True and time.time() - t0 < 1.0       # it did not wait for the servers (the fake takes 1.5 s)
    assert client.get("/api/engines").json()["checking"] is True
    assert client.post("/api/engines/check").json()["started"] is False                            # one check at a time
    time.sleep(1.8)
    d = client.get("/api/engines").json()
    assert d["checking"] is False and "backhaul" in d["engines"] and len(calls) == 1
