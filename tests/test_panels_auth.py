"""How a panel is connected (docs/panels.md): an API key (PasarGuard) or an access token, "sign in once", the saved login that is forgotten on
request, and the warnings before a key / token ends. No network: the panel is a fake."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Panel
from nikatel.services import panels
from nikatel.services.panels import PanelError

KEY = "pg_key_11111111-2222-4333-8444-555555555555"
OTHER_KEY = "pg_key_99999999-8888-4777-8666-555555555555"
DAY = 86400
URL = "https://panel.example.test"


def jwt(sub: str = "boss", access: str = "sudo", exp: int | None = None, iat: int | None = None) -> str:
    def part(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    claims: dict = {"sub": sub, "access": access}

    if iat is not None:
        claims["iat"] = iat

    if exp is not None:
        claims["exp"] = exp

    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}.sig0"


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class PanelApi:
    """A panel in memory that knows the calls the connection layer makes: system, nodes, who the key is, the list of keys."""

    def __init__(self, key_exp: float | None = None, key_status: str = "active", refuse: tuple = ()):
        self.key_exp, self.key_status, self.refuse = key_exp, key_status, refuse
        self.calls: list[str] = []

    def __call__(self, c: dict, method: str, path: str, **kw):
        self.calls.append(path)

        if c.get("token") in self.refuse:
            raise PanelError("HTTP 401: Could not validate credentials", 401)

        if path == "/api/system":
            return {"version": "5.4.1", "total_user": 10, "users_active": 8, "online_users": 2}

        if path == "/api/nodes":
            return {"nodes": [], "total": 0}

        if path == "/api/admin":
            return {"username": "boss", "role": {"name": "owner", "is_owner": True}}

        if path.startswith("/api/api_keys"):
            return {"api_keys": [{"id": 3, "name": "test", "status": self.key_status, "api_key_trimmed": KEY[:10] + "***" + KEY[-3:], "inherit_permissions": True,
                                  "expire_date": iso(self.key_exp) if self.key_exp else None}], "total": 1}

        raise PanelError("HTTP 404", 404)


def secrets_of(pid: int) -> dict:
    with session() as db:
        return dict(db.get(Panel, pid).secrets or {})


def make_panel(name: str = "old", typ: str = "marzban", **secrets) -> int:
    with session() as db:
        p = Panel(name=name, type=typ, url=URL, username="boss", secrets=secrets, status="up", enabled=True)
        db.add(p)
        db.flush()
        return p.id


# ------------------------------------------------------------------ pure
def test_a_key_is_told_from_a_token_and_travels_in_its_own_header():
    assert panels.is_api_key(KEY)
    assert not panels.is_api_key("pg_key_not-a-uuid")
    assert not panels.is_api_key("pg_key_11111111-2222-1333-8444-555555555555")      # a version 1 uuid: the panel turns it away too
    assert not panels.is_api_key(jwt()) and not panels.is_api_key("")
    assert panels.auth_headers(KEY) == {"X-Api-Key": KEY}
    t = jwt()
    assert panels.auth_headers(t) == {"Authorization": "Bearer " + t}


def test_what_was_pasted_is_cleaned_and_judged():
    t = jwt(exp=int(time.time()) + 30 * DAY)
    assert panels.clean_token(f'  "{t}" ') == t
    assert panels.clean_token("Bearer " + t) == t
    assert panels.clean_token("ApiKey " + KEY) == KEY and panels.clean_token("X-Api-Key: " + KEY) == KEY
    assert panels.clean_token(jwt()) == jwt()                                         # no end date: fine

    for bad in ("", "hello", "aaaa.bbbb.cccc", "pg_key_nope"):
        with pytest.raises(ValueError, match="neither an API key"):
            panels.clean_token(bad)

    with pytest.raises(ValueError, match="already ended"):
        panels.clean_token(jwt(exp=int(time.time()) - 10))


def test_the_claims_of_a_token_are_read_without_trusting_them():
    now = 1_800_000_000
    info = panels.token_info(jwt(sub="boss", access="sudo", iat=now - DAY, exp=now + 5 * DAY), now)
    assert info["known"] and info["admin"] == "boss" and info["access"] == "sudo" and info["left_s"] == 5 * DAY and info["life_s"] == 6 * DAY
    assert panels.token_info(jwt(), now)["left_s"] is None                             # no exp: never ends
    assert panels.token_info("not a token", now)["known"] is False
    assert panels.token_claims("aaaa.%%%.cccc") is None
    assert [panels.span_text(s) for s in (35 * 60, 23 * 3600, DAY, 5 * DAY, -2 * DAY)] == ["35 min", "23 h", "1 day", "5 days", "2 days"]


def test_the_state_of_a_connection_for_the_page():
    now = 1_800_000_000
    st = panels.auth_of

    long = st(jwt(sub="boss", access="sudo", iat=now - DAY, exp=now + 694 * DAY), "", False, now=now)
    assert (long["kind"], long["level"], long["admin"], long["access"], long["short"]) == ("token", "ok", "boss", "sudo", False) and "694 days" in long["note"]

    assert st(jwt(exp=now + 5 * DAY), "", False, now=now)["level"] == "warn"
    soon = st(jwt(iat=now - 14 * 3600, exp=now + 10 * 3600), "", False, now=now)
    assert soon["level"] == "bad" and "10 h" in soon["note"] and soon["short"] is True
    assert st(jwt(exp=now - 3 * DAY), "", False, now=now)["level"] == "bad"
    assert st(jwt(exp=now + 10 * 3600), "boss", True, now=now)["level"] == "ok"        # a saved login renews it
    assert st(jwt(), "", False, now=now)["note"] == "the token never ends"
    assert st("", "boss", True, now=now)["kind"] == "login"
    assert st("", "", False, now=now)["kind"] == "none" and st("", "", False, now=now)["level"] == "bad"

    meta = {"found": True, "name": "test", "status": "active", "exp": now + 372 * DAY, "admin": "boss", "role": "owner"}
    k = st(KEY, "", False, meta, now=now)
    assert (k["kind"], k["level"], k["key_name"], k["admin"], k["access"], k["short"]) == ("api_key", "ok", "test", "boss", "owner", False) and "372 days" in k["note"]
    assert st(KEY, "", False, {**meta, "exp": None}, now=now)["note"] == "the API key never ends"
    assert st(KEY, "", False, None, now=now)["level"] == "ok"                           # asked nothing yet: not known, not a problem
    assert st(KEY, "", False, {**meta, "exp": now + 3 * DAY}, now=now)["level"] == "warn"
    assert st(KEY, "", False, {**meta, "exp": now + 5 * 3600}, now=now)["level"] == "bad"
    assert st(KEY, "", False, {**meta, "status": "revoked"}, now=now)["level"] == "bad"
    assert st(KEY, "boss", True, {**meta, "status": "revoked"}, now=now)["level"] == "ok"      # the saved login takes over


def test_the_cred_carries_the_trimmed_form_of_a_key_for_the_api_keys_tab():
    c = panels._cred(Panel(name="x", type="pasarguard", url=URL, username="boss", secrets={"token": KEY}))
    assert c["api_key_trimmed"] == "pg_key_111***555"                                  # the form the panel lists its keys in: enough to mark the key in use, not a secret
    assert panels._cred(Panel(name="y", type="marzban", url=URL, username="boss", secrets={"token": jwt()}))["api_key_trimmed"] == ""
    assert panels._cred(Panel(name="z", type="marzban", url=URL, username="boss", secrets={"password": "pw"}))["api_key_trimmed"] == ""


def test_the_http_call_sends_the_credential_in_the_right_header(monkeypatch):
    seen = []

    def fake_request(method, url, headers=None, **kw):
        seen.append(headers)
        return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", fake_request)
    c = {"url": URL, "verify_tls": True}
    panels._http(c, "GET", "/api/system", token=KEY)
    panels._http(c, "GET", "/api/system", token=jwt())
    assert seen[0]["X-Api-Key"] == KEY and "Authorization" not in seen[0]
    assert seen[1]["Authorization"].startswith("Bearer ") and "X-Api-Key" not in seen[1]


def test_the_credential_the_owner_gave_comes_first_the_saved_login_only_when_it_ends(monkeypatch):
    monkeypatch.setattr(panels, "_TOKENS", {})
    logins = []
    monkeypatch.setattr(panels, "_login", lambda c: logins.append(1) or "from-login")
    base = {"id": 7, "url": URL, "username": "", "password": "", "token": "", "verify_tls": True, "type": "pasarguard"}

    # an API key alone: used as it is, never a sign-in; a retry after 401 cannot help and says what to do
    assert panels._token({**base, "token": KEY}) == KEY and not logins

    with pytest.raises(PanelError, match="API key") as e:
        panels._token({**base, "token": KEY}, fresh=True)

    assert e.value.code == 401

    # an ended token with a saved login: the login makes a new one, kept in memory for the next calls
    c = {**base, "token": jwt(exp=int(time.time()) - 5), "username": "boss", "password": "pw"}
    assert panels._token(c) == "from-login" and len(logins) == 1
    assert panels._token(c) == "from-login" and len(logins) == 1

    with pytest.raises(PanelError, match="no API key, no token"):
        panels._token({**base, "id": 8})


# ------------------------------------------------------------------ add / change through the API
def test_add_a_pasarguard_panel_with_its_api_key(client, monkeypatch):
    f = PanelApi(key_exp=time.time() + 100 * DAY)
    monkeypatch.setattr(panels, "call", f)
    r = client.post("/api/panels", json={"name": "pg", "type": "pasarguard", "url": URL, "token": KEY})
    assert r.status_code == 200, r.text
    p = r.json()["panel"]
    a = p["auth"]
    assert (a["kind"], a["key_name"], a["admin"], a["access"], a["level"], a["login"]) == ("api_key", "test", "boss", "owner", "ok", False)
    assert p["username"] == "boss" and p["status"] == "up"
    assert KEY not in r.text and KEY not in client.get("/api/panels").text           # a secret never leaves the server
    sec = secrets_of(p["id"])
    assert sec["token"] == KEY and "password" not in sec and sec["key_meta"]["found"] is True


def test_what_cannot_be_a_connection_is_refused_and_nothing_is_added(client, monkeypatch):
    monkeypatch.setattr(panels, "call", PanelApi(refuse=(OTHER_KEY,)))
    body = {"name": "x", "type": "pasarguard", "url": URL}

    for extra, why in (({"token": KEY, "type": "marzban"}, "Marzban has no API keys"), ({}, "paste the panel's API key"), ({"token": "pg_key_nope"}, "neither an API key"),
                       ({"token": OTHER_KEY}, "did not accept it"), ({"username": "boss"}, "paste the panel's API key")):
        r = client.post("/api/panels", json={**body, **extra})
        assert r.status_code == 400 and why in r.json()["error"], (extra, r.text)

    assert client.get("/api/panels").json()["panels"] == []


def test_sign_in_once_keeps_only_the_token(client, monkeypatch):
    now = int(time.time())
    tok = jwt(sub="boss", access="sudo", iat=now, exp=now + 600 * DAY)
    monkeypatch.setattr(panels, "_login", lambda c: tok)
    monkeypatch.setattr(panels, "call", PanelApi())
    r = client.post("/api/panels", json={"name": "mz", "type": "marzban", "url": URL, "username": "boss", "password": "pw"})
    assert r.status_code == 200, r.text
    assert secrets_of(r.json()["panel"]["id"]) == {"token": tok}                       # the password is nowhere
    a = r.json()["panel"]["auth"]
    assert (a["kind"], a["login"], a["level"]) == ("token", False, "ok")
    assert "pw" not in r.text and tok not in r.text

    # asked to keep the login (to renew a token that ends): both are stored
    r = client.post("/api/panels", json={"name": "mz2", "type": "marzban", "url": URL, "username": "boss", "password": "pw", "keep_login": True})
    assert secrets_of(r.json()["panel"]["id"]) == {"token": tok, "password": "pw"} and r.json()["panel"]["auth"]["login"] is True

    # a sign-in the panel turns down is a plain 400
    monkeypatch.setattr(panels, "_login", lambda c: (_ for _ in ()).throw(PanelError("HTTP 401: Incorrect username or password", 401)))
    r = client.post("/api/panels", json={"name": "mz3", "type": "marzban", "url": URL, "username": "boss", "password": "bad"})
    assert r.status_code == 400 and "did not accept the sign-in" in r.json()["error"]


def test_a_new_key_replaces_the_old_one_only_when_the_panel_accepts_it(client, monkeypatch):
    monkeypatch.setattr(panels, "call", PanelApi(refuse=(OTHER_KEY,)))
    monkeypatch.setattr(panels, "_login", lambda c: jwt(exp=int(time.time()) + 600 * DAY))
    pid = client.post("/api/panels", json={"name": "pg", "type": "pasarguard", "url": URL, "token": KEY, "username": "", "keep_login": False}).json()["panel"]["id"]

    r = client.patch(f"/api/panels/{pid}", json={"token": OTHER_KEY})                 # the panel refuses it: the old key stays
    assert r.status_code == 400 and "did not accept" in r.json()["error"]
    assert secrets_of(pid)["token"] == KEY

    fresh = jwt(sub="boss", exp=int(time.time()) + 300 * DAY)
    r = client.patch(f"/api/panels/{pid}", json={"token": fresh})                      # a token instead of a key is fine on PasarGuard
    assert r.status_code == 200 and r.json()["panel"]["auth"]["kind"] == "token" and secrets_of(pid) == {"token": fresh}

    # the legacy way still works: a password typed in Edit is a saved login, tried first
    r = client.patch(f"/api/panels/{pid}", json={"username": "boss", "password": "pw"})
    assert r.status_code == 200 and secrets_of(pid).get("password") == "pw" and r.json()["panel"]["auth"]["login"] is True

    # a new token never wipes a saved login: that is what "forget the login" is for
    r = client.patch(f"/api/panels/{pid}", json={"token": KEY})
    assert r.status_code == 200 and secrets_of(pid)["token"] == KEY and secrets_of(pid)["password"] == "pw"
    assert r.json()["panel"]["name"] == "pg" and "enabled" in r.json()["panel"]


def test_forget_the_saved_login(client, monkeypatch):
    now = int(time.time())
    long_tok, short_tok = jwt(exp=now + 700 * DAY, iat=now), jwt(exp=now + DAY, iat=now)
    monkeypatch.setattr(panels, "call", PanelApi())
    pid = make_panel(password="pw")

    # the panel hands out 24 h tokens: refused, nothing changes
    monkeypatch.setattr(panels, "_login", lambda c: short_tok)
    r = client.post(f"/api/panels/{pid}/forget-login")
    assert r.status_code == 400 and "lasts only" in r.json()["error"] and secrets_of(pid) == {"password": "pw"}

    # a token that lasts: only the token is left, the password is gone
    monkeypatch.setattr(panels, "_login", lambda c: long_tok)
    r = client.post(f"/api/panels/{pid}/forget-login")
    assert r.status_code == 200, r.text
    assert secrets_of(pid) == {"token": long_tok} and r.json()["panel"]["auth"]["login"] is False
    assert client.post(f"/api/panels/{pid}/forget-login").status_code == 400            # nothing left to forget

    # the owner can force it for a short token (and then paste a new one by hand)
    pid2 = make_panel("old2", password="pw")
    monkeypatch.setattr(panels, "_login", lambda c: short_tok)
    r = client.post(f"/api/panels/{pid2}/forget-login", json={"force": True})
    assert r.status_code == 200 and secrets_of(pid2) == {"token": short_tok} and r.json()["panel"]["auth"]["level"] == "bad"

    # an API key already there is long enough by itself: no sign-in at all
    pid3 = make_panel("old3", "pasarguard", password="pw", token=KEY)
    monkeypatch.setattr(panels, "_login", lambda c: (_ for _ in ()).throw(AssertionError("no sign-in is needed")))
    r = client.post(f"/api/panels/{pid3}/forget-login")
    assert r.status_code == 200 and secrets_of(pid3) == {"token": KEY} and r.json()["panel"]["auth"]["kind"] == "api_key"


# ------------------------------------------------------------------ the job
def test_the_owner_is_warned_once_per_level_before_a_token_ends(monkeypatch):
    monkeypatch.setattr(panels, "call", PanelApi())
    monkeypatch.setattr(panels, "_ALERTED", {})
    pid = make_panel("soon", token=jwt(exp=int(time.time()) + 3 * DAY))

    def events():
        with session() as db:
            return [(e.level, e.text) for e in db.scalars(select(Event)) if e.domain == "panels" and "soon" in e.text]

    panels.check_all()
    panels.check_all()
    assert [lvl for lvl, _ in events()] == ["warn"]                                    # told once, not at every check
    assert "ends in" in events()[0][1] and "Edit" in events()[0][1]

    with session() as db:
        db.get(Panel, pid).secrets = {"token": jwt(exp=int(time.time()) + 5 * 3600)}
    panels.check_all()
    assert [lvl for lvl, _ in events()] == ["warn", "bad"]                              # worse: told again

    with session() as db:
        db.get(Panel, pid).secrets = {"token": jwt(exp=int(time.time()) + 700 * DAY)}
    panels.check_all()
    assert len(events()) == 2 and pid not in panels._ALERTED                            # fine again: silent, and ready for the next time


def test_a_saved_login_makes_the_warning_unnecessary(monkeypatch):
    monkeypatch.setattr(panels, "call", PanelApi())
    monkeypatch.setattr(panels, "_ALERTED", {})
    make_panel("renewed", token=jwt(exp=int(time.time()) + 3600), password="pw")
    panels.check_all()

    with session() as db:
        assert not [e for e in db.scalars(select(Event)) if "renewed" in e.text]


def test_the_panel_is_asked_about_its_key_at_most_twice_a_day(monkeypatch):
    f = PanelApi(key_exp=time.time() + 40 * DAY)
    monkeypatch.setattr(panels, "call", f)
    pid = make_panel("pg", "pasarguard", token=KEY)
    panels.check_all()
    panels.check_all()
    assert sum(1 for p in f.calls if p.startswith("/api/api_keys")) == 1               # the second round is inside the 12 hours
    meta = secrets_of(pid)["key_meta"]
    assert meta["found"] is True and meta["name"] == "test" and meta["admin"] == "boss" and meta["role"] == "owner" and meta["exp"] > time.time()


def test_a_revoked_key_is_a_problem_the_page_shows(monkeypatch):
    monkeypatch.setattr(panels, "call", PanelApi(key_status="revoked"))
    c = {"id": 0, "url": URL, "username": "", "password": "", "token": KEY, "verify_tls": True, "type": "pasarguard"}
    meta = panels.key_info(c)
    assert meta["found"] and meta["status"] == "revoked"
    assert panels.auth_of(KEY, "", False, meta)["level"] == "bad"


def test_a_key_that_may_not_read_the_key_list_stays_unknown(monkeypatch):
    def deny(c, method, path, **kw):
        if path.startswith("/api/api_keys"):
            raise PanelError("HTTP 403: Permission denied", 403)

        return {"username": "helper", "role": {"name": "support"}}

    monkeypatch.setattr(panels, "call", deny)
    meta = panels.key_info({"id": 0, "url": URL, "token": KEY, "type": "pasarguard"})
    assert "found" not in meta and meta["admin"] == "helper" and meta["role"] == "support"
    assert panels.auth_of(KEY, "", False, meta)["note"] == "the end of this API key is not known"


def test_panels_json_may_carry_a_key_or_a_token():
    d = Path(get_settings().bootstrap_dir)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "panels.json"
    t = jwt()
    f.write_text(json.dumps([{"name": "one", "type": "pasarguard", "url": URL, "api_key": KEY},
                             {"name": "two", "type": "marzban", "url": "https://panel-b.example.test", "token": t, "username": "boss"},
                             {"name": "three", "type": "marzban", "url": "https://panel-c.example.test", "username": "boss", "password": "pw"}]))

    try:
        panels.bootstrap()
    finally:
        f.unlink()

    with session() as db:
        rows = {p.name: dict(p.secrets or {}) for p in db.scalars(select(Panel))}

    assert rows == {"one": {"token": KEY}, "two": {"token": t}, "three": {"password": "pw"}}


def test_the_saved_key_is_never_sent_to_another_address(client, monkeypatch):
    """Review 2026-10-16: changing only the address of a panel kept the saved key and the next check sent it to the new machine."""
    monkeypatch.setattr(panels, "call", PanelApi())
    monkeypatch.setattr(panels, "_login", lambda c: jwt(exp=int(time.time()) + 600 * DAY))
    pid = client.post("/api/panels", json={"name": "pg", "type": "pasarguard", "url": URL, "token": KEY}).json()["panel"]["id"]
    assert client.patch(f"/api/panels/{pid}", json={"url": URL + "/dashboard"}).status_code == 200        # the same machine: a path is not another address
    assert client.patch(f"/api/panels/{pid}", json={"name": "pg2", "verify_tls": False}).status_code == 200

    for other in ("https://evil.example.test", "https://panel.example.test:8443", "http://panel.example.test"):         # another host, port or scheme
        r = client.patch(f"/api/panels/{pid}", json={"url": other})
        assert r.status_code == 400 and "another address" in r.json()["error"], other

    assert client.get("/api/panels").json()["panels"][0]["url"] == URL and secrets_of(pid)["token"] == KEY
    r = client.patch(f"/api/panels/{pid}", json={"url": "https://new.example.test", "token": OTHER_KEY})      # with the key typed again it is tried at the new address first
    assert r.status_code == 200 and r.json()["panel"]["url"] == "https://new.example.test" and secrets_of(pid)["token"] == OTHER_KEY
    assert panels.origin("https://A.example.test/x") == ("https", "a.example.test", 443) and panels.origin("http://h:81") == ("http", "h", 81)


def test_the_panels_are_checked_at_the_same_time(monkeypatch):
    """A panel that does not answer costs its own timeout, not the timeout of every panel after it."""
    import threading

    for i in range(4):
        make_panel(f"p{i}", "pasarguard", token=KEY)

    seen = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow(c):
        with lock:
            seen["now"] += 1
            seen["max"] = max(seen["max"], seen["now"])

        time.sleep(0.2)

        with lock:
            seen["now"] -= 1

        return {"ok": True, "error": "", "nodes": [], "stats": {"version": "5.4.1"}, "latency_ms": 5}

    monkeypatch.setattr(panels, "check", slow)
    t0 = time.time()
    panels.check_all()
    assert seen["max"] >= 3 and time.time() - t0 < 0.7          # four checks of 0.2 s one after the other would be 0.8 s
    with session() as db:
        assert [p.status for p in db.scalars(select(Panel))] == ["up"] * 4
