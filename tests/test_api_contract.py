"""The API contract (docs/api.md): one error shape from one handler on every route, the request id on every answer, long work as actions."""
import time

from fastapi.testclient import TestClient


def _shape(r, status, code):
    j = r.json()
    assert r.status_code == status, (r.status_code, j)
    assert j["ok"] is False and isinstance(j["error"], str) and j["error"] and j["code"] == code, j
    assert r.headers.get("x-request-id")


def test_every_kind_of_error_has_the_one_shape(client):
    _shape(client.get("/api/no/such/route"), 404, "not_found")
    _shape(client.get("/api/actions/999999"), 404, "not_found")
    _shape(client.post("/api/servers", json={"name": "x"}), 422, "invalid")                    # the body does not fit the model
    assert "host" in client.post("/api/servers", json={"name": "x"}).json()["error"]
    _shape(client.post("/api/tunnels/999/restart"), 409, "conflict")
    _shape(client.post("/api/tunnels", json={"iran_id": 1, "foreign_id": 2, "ports": "443", "transport": "nope"}), 400, "bad_request")
    stranger = TestClient(client.app)
    _shape(stranger.get("/api/servers"), 401, "not_signed_in")


def test_an_unexpected_failure_is_internal_with_the_request_id_and_no_traceback(client):
    def boom():
        raise RuntimeError("a secret traceback line")

    client.app.add_api_route("/api/_boom", boom, methods=["GET"])
    r = client.get("/api/_boom")
    _shape(r, 500, "internal")
    assert "Traceback" not in r.text and "File " not in r.text


def test_long_work_answers_at_once_with_an_action_id(client, monkeypatch):
    from nikatel.services import actions

    rows = [{"id": 1, "name": "a", "state": "up", "why": ""}]
    monkeypatch.setattr(actions, "health_all", lambda: (time.sleep(1.0), rows)[1])
    t0 = time.time()
    r = client.post("/api/tunnels/health-all")
    assert r.status_code == 202 and r.json()["ok"] and time.time() - t0 < 0.8
    aid = r.json()["action_id"]

    for _ in range(60):
        s = client.get(f"/api/actions/{aid}").json()

        if s["done"]:
            break

        time.sleep(0.1)

    assert s["done"] and s["ok"] and s["story"][-1].startswith("Checked 1 tunnel")
