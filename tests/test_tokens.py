"""API tokens (docs/api.md section 3): made once with the password, shown once, scoped, revocable; the gate lets a valid token in."""
from fastapi.testclient import TestClient

from nikatel.services import tokens

PW = "test-password-1"


def test_allowed_is_the_scope_rule():
    assert tokens.allowed("read", "GET", "/api/servers") and not tokens.allowed("read", "POST", "/api/tunnels/health-all")
    assert tokens.allowed("act", "POST", "/api/tunnels/health-all") and not tokens.allowed("act", "PUT", "/api/settings/repair")
    assert not tokens.allowed("read", "GET", "/api/settings") and not tokens.allowed("act", "GET", "/api/logs") and not tokens.allowed("act", "DELETE", "/api/tokens/1")
    assert tokens.allowed("admin", "DELETE", "/api/tokens/1") and tokens.allowed("admin", "GET", "/api/auth/security-log")
    assert tokens.allowed("read", "GET", "/api/settingsx")          # only the prefix as a path, not as a string
    # the credentials of a panel: read and act may look and may use the panel, only admin may add, change or remove one
    assert tokens.allowed("read", "GET", "/api/panels") and tokens.allowed("act", "GET", "/api/panels/3") and tokens.allowed("act", "POST", "/api/panels/3/check")
    assert not tokens.allowed("act", "POST", "/api/panels") and not tokens.allowed("act", "PATCH", "/api/panels/3") and not tokens.allowed("act", "DELETE", "/api/panels/3") and not tokens.allowed("act", "POST", "/api/panels/3/forget-login")
    assert tokens.allowed("admin", "PATCH", "/api/panels/3") and tokens.allowed("admin", "POST", "/api/panels")


def test_tokens_are_made_with_the_password_shown_once_and_scoped(client):
    assert client.post("/api/tokens", json={"name": "bot", "scope": "read", "password": "wrong"}).status_code == 400
    r = client.post("/api/tokens", json={"name": "bot", "scope": "read", "password": PW})
    assert r.status_code == 201 and r.json()["token"].startswith("nkt_") and r.json()["prefix"] == r.json()["token"][:10]
    tok = r.json()["token"]
    lst = client.get("/api/tokens").json()["tokens"]
    assert len(lst) == 1 and "token" not in lst[0] and "token_hash" not in lst[0] and lst[0]["prefix"] == tok[:10]
    assert client.post("/api/tokens", json={"name": "bot", "scope": "read", "password": PW}).status_code == 400       # the name is taken

    m = TestClient(client.app)                                     # a machine: no cookie, only the header
    h = {"Authorization": "Bearer " + tok}
    assert m.get("/api/servers").status_code == 401                # no token: nothing
    assert m.get("/api/servers", headers=h).status_code == 200
    assert m.get("/api/auth/me", headers=h).json()["user"] == "token:bot"
    r = m.post("/api/tunnels/health-all", headers=h)
    assert r.status_code == 403 and r.json()["code"] == "token_scope"
    assert m.get("/api/settings", headers=h).status_code == 403
    assert m.get("/api/tokens", headers=h).status_code == 403
    assert m.get("/api/servers", headers={"Authorization": "Bearer nkt_not-a-real-token-at-all"}).status_code == 401

    act = client.post("/api/tokens", json={"name": "doctor", "scope": "act", "password": PW}).json()["token"]
    ha = {"Authorization": "Bearer " + act}
    assert m.post("/api/tunnels/health-all", headers=ha).status_code == 202
    assert m.put("/api/settings/repair", headers=ha, json={}).status_code == 403

    adm = client.post("/api/tokens", json={"name": "ops", "scope": "admin", "password": PW}).json()["token"]
    hd = {"Authorization": "Bearer " + adm}
    assert m.get("/api/settings", headers=hd).status_code == 200
    made = m.post("/api/tokens", headers=hd, json={"name": "child", "scope": "read"})                      # an admin token makes tokens without a password
    assert made.status_code == 201 and made.json()["created_by"] == "token:ops"

    assert [t["last_used_at"] > 0 for t in client.get("/api/tokens").json()["tokens"] if t["name"] == "bot"] == [True]
    tid = next(t["id"] for t in client.get("/api/tokens").json()["tokens"] if t["name"] == "bot")
    assert client.delete(f"/api/tokens/{tid}").status_code == 200
    assert m.get("/api/servers", headers=h).status_code == 401                                             # revoked: gone at once
    assert client.delete(f"/api/tokens/{tid}").status_code == 404
    log = client.get("/api/auth/security-log").json()
    texts = " ".join(str(x) for x in (log.get("log") or log.get("events") or log.get("items") or []))
    assert "made by" in texts and "revoked by" in texts


def test_a_valid_token_passes_the_secret_door(client):
    tok = client.post("/api/tokens", json={"name": "bot", "scope": "read", "password": PW}).json()["token"]
    assert client.put("/api/auth/entry-path", json={"path": "my-secret-door-9", "password": PW}).status_code == 200
    stranger = TestClient(client.app)
    assert stranger.get("/api/servers").status_code == 404
    assert stranger.get("/api/servers", headers={"Authorization": "Bearer " + tok}).status_code == 200
    assert stranger.get("/api/servers", headers={"Authorization": "Bearer nkt_wrong-wrong-wrong-wrong"}).status_code == 404     # a wrong token sees a missing page, like everyone else
