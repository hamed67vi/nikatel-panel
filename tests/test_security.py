"""Sign-in security: two-step codes, recovery, throttling, sessions that end, the secret entry path, headers."""
import time

from fastapi.testclient import TestClient

from nikatel import auth
from nikatel.main import create_app

PW = "test-password-1"


def _code(secret: str) -> str:
    return auth.totp_at(secret, int(time.time() // 30))


def _enable_2fa(client) -> tuple[str, list[str]]:
    s = client.post("/api/auth/2fa/setup", json={"password": PW}).json()
    assert s["secret"] and s["uri"].startswith("otpauth://totp/Nikatel:admin?secret=")
    r = client.post("/api/auth/2fa/enable", json={"password": "", "code": _code(s["secret"])})
    assert r.status_code == 200, r.text
    return s["secret"], r.json()["recovery_codes"]


def test_totp_matches_the_rfc_vector_and_refuses_replay():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"          # "12345678901234567890"
    assert auth.totp_at(secret, 1, digits=8) == "94287082"
    now = 59.0
    assert auth.totp_check(secret, "287082", now=now) == 1
    assert auth.totp_check(secret, "287082", now=now, last_counter=1) is None      # the same code twice
    assert auth.totp_check(secret, "000000", now=now) is None and auth.totp_check(secret, "12", now=now) is None
    assert auth.totp_check(secret, "287082", now=now + 30) == 1                    # one step of clock drift is fine
    assert auth.totp_check(secret, "287082", now=now + 120) is None


def test_two_step_sign_in_and_recovery_codes(client):
    _secret, codes = _enable_2fa(client)
    assert len(codes) == 10 and client.get("/api/auth/security").json()["totp_enabled"]
    other = TestClient(client.app)
    r = other.post("/api/auth/login", json={"user": "admin", "password": PW}).json()
    assert r["need_2fa"] and "ticket" not in other.cookies and other.get("/api/auth/me").status_code == 401
    assert other.post("/api/auth/login/2fa", json={"ticket": r["ticket"], "code": "000000"}).status_code == 401
    # the code the setup used is spent (replay); a recovery code works once
    assert other.post("/api/auth/login/2fa", json={"ticket": r["ticket"], "code": codes[0]}).status_code == 200
    assert other.get("/api/auth/me").status_code == 200
    third = TestClient(client.app)
    t = third.post("/api/auth/login", json={"user": "admin", "password": PW}).json()["ticket"]
    assert third.post("/api/auth/login/2fa", json={"ticket": t, "code": codes[0]}).status_code == 401
    assert client.get("/api/auth/security").json()["recovery_left"] == 9
    # switching it off needs the password and a code
    assert client.post("/api/auth/2fa/disable", json={"password": "wrong-password", "code": codes[1]}).status_code == 400
    assert client.post("/api/auth/2fa/disable", json={"password": PW, "code": codes[1]}).status_code == 200
    assert not client.get("/api/auth/security").json()["totp_enabled"]


def test_wrong_attempts_lock_the_address(client):
    other = TestClient(client.app)

    for _ in range(5):
        assert other.post("/api/auth/login", json={"user": "admin", "password": "nope-nope-nope"}).status_code == 401

    r = other.post("/api/auth/login", json={"user": "admin", "password": PW})
    assert r.status_code == 429 and "try again" in r.json()["detail"]


def test_changing_the_password_ends_other_sessions(client):
    other = TestClient(client.app)
    assert other.post("/api/auth/login", json={"user": "admin", "password": PW}).status_code == 200
    assert client.post("/api/auth/password", json={"current": PW, "new": "a-much-better-password"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200           # this browser stays in
    assert other.get("/api/auth/me").status_code == 401            # the other one is out
    assert client.post("/api/auth/password", json={"current": "a-much-better-password", "new": "short"}).status_code == 400


def test_forgotten_password_reset_with_a_recovery_code(client):
    _, codes = _enable_2fa(client)
    anon = TestClient(client.app)
    assert anon.post("/api/auth/recover", json={"code": "abcdef-abcdef", "new": "brand-new-password"}).status_code == 400
    assert anon.post("/api/auth/recover", json={"code": codes[2], "new": "short"}).status_code == 400
    assert anon.post("/api/auth/recover", json={"code": codes[2], "new": "brand-new-password"}).status_code == 200
    assert anon.post("/api/auth/recover", json={"code": codes[2], "new": "another-new-password"}).status_code == 400      # used once
    r = anon.post("/api/auth/login", json={"user": "admin", "password": "brand-new-password"}).json()
    assert r["need_2fa"]                                                                                                   # two-step stays on
    assert anon.post("/api/auth/login/2fa", json={"ticket": r["ticket"], "code": codes[3]}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401                                                                   # the old session ended


def test_telegram_reset_code(client, monkeypatch):
    from nikatel.api import auth as api_auth

    sent = []
    monkeypatch.setattr(api_auth, "send_text", lambda text, **k: (sent.append(text) or True, "sent"))
    anon = TestClient(client.app)
    assert anon.post("/api/auth/recover/telegram").status_code == 200
    code = sent[0].split(": ")[1].split("\n")[0]
    assert len(code) == 8 and anon.post("/api/auth/recover", json={"code": "00000000", "new": "brand-new-password"}).status_code == 400
    assert anon.post("/api/auth/recover", json={"code": code, "new": "brand-new-password"}).status_code == 200
    assert anon.post("/api/auth/login", json={"user": "admin", "password": "brand-new-password"}).status_code == 200


def test_secret_entry_path(client):
    assert client.put("/api/auth/entry-path", json={"path": "bad path!", "password": PW}).status_code == 400
    assert client.put("/api/auth/entry-path", json={"path": "short", "password": PW}).status_code == 400
    assert client.put("/api/auth/entry-path", json={"path": "my-secret-door-9", "password": "wrong-password"}).status_code == 400
    assert client.put("/api/auth/entry-path", json={"path": "my-secret-door-9", "password": PW}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200               # the browser that set it keeps working
    stranger = TestClient(client.app)

    for url in ("/", "/api/auth/me", "/static/app.js", "/api/docs", "/api/auth/recovery-options", "/anything"):
        r = stranger.get(url)
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, url

    assert stranger.post("/api/auth/login", json={"user": "admin", "password": PW}).status_code == 404
    door = stranger.get("/my-secret-door-9")
    assert door.status_code == 200 and "NIKATEL" in door.text and "nikatel_gate" in door.headers["set-cookie"]
    assert stranger.get("/").status_code == 200 and stranger.get("/static/app.js").status_code == 200
    assert stranger.post("/api/auth/login", json={"user": "admin", "password": PW}).status_code == 200
    # changing the path closes the old door for browsers that had the old cookie
    assert client.put("/api/auth/entry-path", json={"path": "another-secret-door", "password": PW}).status_code == 200
    assert stranger.get("/api/auth/me").status_code == 404
    assert client.put("/api/auth/entry-path", json={"path": "", "password": PW}).status_code == 200
    assert TestClient(client.app).get("/").status_code == 200


def test_security_headers_and_no_store(client):
    r = client.get("/api/auth/me")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store" and "max-age" in r.headers["strict-transport-security"]
    assert client.get("/").headers["x-frame-options"] == "DENY"


def test_password_rules_and_first_start_file(tmp_path):
    import pytest

    for bad in ("short", "admin", "1234567890"):
        with pytest.raises(ValueError):
            auth.check_strength(bad)

    auth.check_strength("long-enough-password")
    f = auth.get_settings().data_dir / "initial-admin-password.txt"
    f.write_text("x")
    auth.set_password("long-enough-password")
    assert not f.exists()                   # the first-start password file goes when a password is set
    assert create_app(run_jobs=False) is not None
