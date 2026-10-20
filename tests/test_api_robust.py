"""The API-robustness audit (2026-10-20): every route was called with no session, a wrong body, huge and odd strings, ids that are not there and numbers the database cannot hold.
What answered 500, hid a route, or left a job's round half done is tested here (each test failed before its fix)."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Event, Panel, Server, ServerSample, Tunnel, TunnelCheck


def _server(name="s1", host="203.0.113.5", role="foreign", **kw):
    with session() as db:
        s = Server(name=name, host=host, role=role, **kw)
        db.add(s)
        db.flush()
        return s.id


# ---------------------------------------------------------------- routes
def test_the_login_page_can_ask_what_the_forgotten_password_screen_offers(client):
    """A comment swallowed `@router.get("/auth/recovery-options")`: the route was a 404, so the login page never showed the Telegram way to a new password."""
    from nikatel.services import settings as st

    anon = TestClient(client.app)
    r = anon.get("/api/auth/recovery-options")
    assert r.status_code == 200 and r.json() == {"ok": True, "telegram": False, "recovery_codes": True}
    st.put("telegram", {"enabled": True, "token": "123:abc", "chat_id": "42"})
    assert anon.get("/api/auth/recovery-options").json()["telegram"] is True


def test_only_the_documented_pages_are_open_to_a_stranger(client):
    """FastAPI's own /redoc and /docs/oauth2-redirect were answered to everyone; nothing links to them."""
    anon = TestClient(client.app)
    assert anon.get("/redoc").status_code == 404 and anon.get("/docs/oauth2-redirect").status_code == 404
    assert anon.get("/api/docs").status_code == 401 and anon.get("/api/openapi.json").status_code == 401
    assert client.get("/api/docs").status_code == 200


def test_every_route_but_the_listed_ones_needs_a_session(client):
    """The ones that are open on purpose: the login steps, the health check (versionless for a stranger), the secret-token subscription link and the front page."""
    import fastapi.routing as fr

    open_on_purpose = {"/", "/api/auth/login", "/api/auth/login/2fa", "/api/auth/logout", "/api/auth/recover", "/api/auth/recover/telegram", "/api/auth/recovery-options", "/api/health", "/sub/{token}"}
    routes = []

    for r in client.app.routes:
        routes += list(r.effective_route_contexts()) if isinstance(r, fr._IncludedRouter) else [r]

    def names(d):
        out = [getattr(d.call, "__name__", "")]

        for x in d.dependencies:
            out += names(x)

        return out

    unguarded = set()

    for r in routes:
        dep = getattr(r, "dependant", None)         # (the documentation pages and the static mount are not API routes: main.py guards /api/docs and /api/openapi.json itself)

        if dep is not None and "current_user" not in [n for d in dep.dependencies for n in names(d)]:
            unguarded.add(r.path)

    assert unguarded == open_on_purpose, (unguarded - open_on_purpose, open_on_purpose - unguarded)


def test_the_demo_does_not_let_a_visitor_open_ssh_sessions_with_a_get(monkeypatch):
    """GET /api/autopilot/diagnose/{id} checks the tunnel over SSH (four commands) and GET /servers/{id}/pasarguard-cert reads a file on the server: in the public demo those are refused."""
    from nikatel import ssh as sshmod
    from nikatel.config import get_settings
    from nikatel.main import create_app

    def never(*a, **k):
        raise AssertionError("the demo opened an ssh session")

    monkeypatch.setattr(sshmod, "_exec_once", never)
    monkeypatch.setattr(get_settings(), "demo", True)

    with TestClient(create_app(run_jobs=False)) as c:
        for path in ("/api/autopilot/diagnose/1", "/api/autopilot/diagnose-config/1", "/api/servers/1/pasarguard-cert"):
            r = c.get(path)
            assert r.status_code == 403 and r.json()["code"] == "read_only_demo", (path, r.status_code)

        assert c.get("/api/servers").status_code == 200          # (the rest of the page is still shown)


# ---------------------------------------------------------------- numbers the database cannot hold
def test_an_id_beyond_what_the_database_holds_is_a_422_not_a_500(client):
    big = 2 ** 70
    for method, url in (("get", f"/api/servers/{big}"), ("delete", f"/api/servers/{big}"), ("get", f"/api/tunnels/{big}"), ("get", f"/api/actions/{big}"), ("get", f"/api/servers/{big}/samples")):
        r = getattr(client, method)(url)
        assert r.status_code == 422 and r.json() == {"ok": False, "error": "a number in the request is too big", "code": "invalid"}, (url, r.status_code, r.text)

    r = client.post("/api/autopilot/move", json={"tunnel_id": 2 ** 63, "iran_id": 1})          # (an id in a body, not in the address)
    assert r.status_code == 422 and r.json()["code"] == "invalid", r.text


def test_an_overflow_that_is_not_the_database_is_still_an_internal_error(client):
    def boom():
        raise OverflowError("math range error")

    client.app.add_api_route("/api/_overflow", boom, methods=["GET"])
    r = client.get("/api/_overflow")
    assert r.status_code == 500 and r.json()["code"] == "internal" and "math range" not in r.text


# ---------------------------------------------------------------- a double click
def test_the_second_start_of_the_same_work_is_a_409_not_a_500(client, monkeypatch):
    from nikatel.services import actions

    gate = threading.Event()
    monkeypatch.setattr(actions, "health_all", lambda: (gate.wait(10), [])[1])
    first = client.post("/api/tunnels/health-all")
    assert first.status_code == 202
    second = client.post("/api/tunnels/health-all")
    assert second.status_code == 409 and second.json()["code"] == "conflict" and "already running" in second.json()["error"]
    gate.set()

    for _ in range(60):
        if client.get(f"/api/actions/{first.json()['action_id']}").json()["done"]:
            break

        time.sleep(0.1)


def test_busy_is_still_a_value_error_for_the_callers_that_catch_those():
    from nikatel.services import autopilot

    assert issubclass(autopilot.Busy, ValueError)


# ---------------------------------------------------------------- the same thing made twice at one moment
def test_two_creates_of_one_name_at_one_moment_are_a_409_not_a_500(client, monkeypatch):
    """10 clicks at once on "Add server": the look "does the name exist?" passed for several, and the UNIQUE of the table refused them with a 500 (22 of 30 requests in the audit)."""
    from nikatel.api import servers as api_servers

    body = {"name": "twin", "host": "203.0.113.77", "role": "foreign"}
    assert client.post("/api/servers", json=body).status_code == 201
    monkeypatch.setattr(api_servers, "server_conflict", lambda *a, **k: "")          # (the second request looked before the first one had written)
    r = client.post("/api/servers", json=body)
    assert r.status_code == 409 and r.json()["code"] == "conflict" and "twin" not in r.text

    with session() as db:
        assert len(list(db.scalars(select(Server).where(Server.name == "twin")))) == 1


def test_a_database_refusal_that_is_not_a_clash_is_still_an_internal_error(client):
    from sqlalchemy.exc import IntegrityError

    def boom():
        raise IntegrityError("INSERT INTO x", {}, Exception("NOT NULL constraint failed: x.y"))

    client.app.add_api_route("/api/_notnull", boom, methods=["GET"])
    r = client.get("/api/_notnull")
    assert r.status_code == 500 and r.json()["code"] == "internal" and "NOT NULL" not in r.text


def test_two_tokens_of_one_name_made_at_one_moment_are_one_token(client, monkeypatch):
    """The name is looked up and then taken (no UNIQUE on it): 10 clicks at once made 11 tokens of three names in the audit."""
    from nikatel.models import ApiToken
    from nikatel.services import tokens

    real = tokens._hash
    gate = threading.Barrier(2)

    def meeting(t):
        try:
            gate.wait(0.6)                  # (both requests are between the look and the insert)
        except threading.BrokenBarrierError:
            pass

        return real(t)

    monkeypatch.setattr(tokens, "_hash", meeting)
    made, refused = [], []

    def make():
        try:
            made.append(tokens.create("same-name", "read")[1]["id"])
        except ValueError as e:
            refused.append(str(e))

    ts = [threading.Thread(target=make) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert len(made) == 1 and len(refused) == 1 and "exists" in refused[0]

    with session() as db:
        assert len(list(db.scalars(select(ApiToken).where(ApiToken.name == "same-name")))) == 1


def _signed_in_clients(app, n):
    out = []

    for _ in range(n):
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"user": "admin", "password": "test-password-1"}).status_code == 200
        out.append(c)

    return out


def test_two_clicks_on_add_profile_at_one_moment_make_one_profile(client, monkeypatch):
    """There is no UNIQUE on a profile: 10 requests at once for three servers made 13 rows in the audit (the Lab page listed a server twice)."""
    from nikatel.models import Profile
    from nikatel.services import profiles as svc

    sid = _server("p1", "203.0.113.31")

    with session() as db:
        for p in db.scalars(select(Profile).where(Profile.server_id == sid)):
            db.delete(p)                          # (adding a server put it on the page already)

    real = svc._count
    gate = threading.Barrier(2)

    def meeting(db):
        try:
            gate.wait(0.6)                          # (both requests are between the look and the insert)
        except threading.BrokenBarrierError:
            pass

        return real(db)

    monkeypatch.setattr(svc, "_count", meeting)
    codes = []
    clients = _signed_in_clients(client.app, 2)

    def add(c):
        codes.append(c.post("/api/profiles", json={"kind": "foreign", "server_ids": [sid], "tag": ""}).status_code)

    ts = [threading.Thread(target=add, args=(c,)) for c in clients]
    [t.start() for t in ts]
    [t.join(20) for t in ts]
    assert sorted(codes) == [201, 409]

    with session() as db:
        assert len(list(db.scalars(select(Profile).where(Profile.server_id == sid)))) == 1


def test_ten_clicks_on_add_maintenance_window_at_one_moment_make_one_window(client):
    clients = _signed_in_clients(client.app, 10)
    codes = []
    gate = threading.Barrier(10)

    def add(c):
        gate.wait(10)
        codes.append(c.post("/api/maintenance", json={"start": 1_800_000_000, "end": 1_800_003_600, "note": "burst"}).status_code)

    ts = [threading.Thread(target=add, args=(c,)) for c in clients]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert codes == [201] * 10 and len(client.get("/api/maintenance").json()["windows"]) == 1


def test_the_same_maintenance_window_sent_twice_is_one_window_and_the_times_are_bounded(client):
    w = {"start": 1_800_000_000, "end": 1_800_003_600, "note": "kernel update"}
    a, b = client.post("/api/maintenance", json=w), client.post("/api/maintenance", json=w)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert len(client.get("/api/maintenance").json()["windows"]) == 1
    assert client.post("/api/maintenance", json={**w, "note": "another"}).json()["id"] != a.json()["id"]

    for bad in ({"start": -5, "end": 100}, {"start": 1, "end": 2 ** 40}, {"start": 2 ** 63, "end": 2 ** 63 + 5}):
        assert client.post("/api/maintenance", json=bad).status_code == 422, bad


# ---------------------------------------------------------------- a missing thing is said in words
def test_a_dns_record_of_an_account_that_is_not_there_is_a_400(client):
    r = client.get("/api/dns/records/nobody/example.com/abc")
    assert r.status_code == 400 and r.json()["code"] == "bad_request" and "account" in r.json()["error"]


def test_the_pasarguard_certificate_of_a_server_that_does_not_answer_is_a_502(client, monkeypatch):
    from nikatel.services import extras

    sid = _server()

    def down(*a, **k):
        raise extras.SSHError("ssh connection lost")

    monkeypatch.setattr(extras, "run_script", down)
    r = client.get(f"/api/servers/{sid}/pasarguard-cert")
    assert r.status_code == 502 and "connection lost" in r.json()["error"]


def test_connecting_a_server_without_the_panel_key_says_so_and_names_no_path(client):
    sid = _server()
    r = client.post(f"/api/servers/{sid}/connect", json={"password": "secret-password"})
    assert r.status_code == 400 and "SSH public key" in r.json()["error"] and "no-key" not in r.text and "/tmp" not in r.text


def test_a_bot_server_made_from_a_panel_gets_the_login_of_the_panel(client, monkeypatch):
    """`Panel` has no `password` column (the login is in `secrets`): the button "add from a connected panel" was a 500 every time."""
    from nikatel.services import shopbot_admin as adm

    saved = []
    monkeypatch.setattr(adm, "server_save", lambda sid, info, conf: saved.append((info, conf)) or 7)

    with session() as db:
        db.add(Panel(name="p1", type="pasarguard", url="https://panel.example.test:8000/", username="admin", secrets={"password": "the-login-password", "token": "t"}))
        db.add(Panel(name="p2", type="marzban", url="https://p2.example.test", username="", secrets={"token": "only-a-token"}))
        ids = {p.name: p.id for p in db.scalars(select(Panel))}

    r = client.post("/api/shopbot/servers/from-panel", json={"panel_id": ids["p1"], "title": "", "ucount": 50})
    assert r.status_code == 201 and r.json() == {"ok": True, "id": 7}
    info, conf = saved[0]
    assert conf == {"type": "pasarguard", "panel_url": "https://panel.example.test:8000", "username": "admin", "password": "the-login-password"} and info["title"] == "p1"
    r = client.post("/api/shopbot/servers/from-panel", json={"panel_id": ids["p2"], "title": "x", "ucount": 5})
    assert r.status_code == 400 and "no saved login" in r.json()["error"] and len(saved) == 1


@pytest.mark.parametrize("bad", ["sk-\u202eabc", "\U0001f600" * 5, "key with a\x00nul"])
def test_a_key_with_a_hidden_or_non_english_character_is_answered_in_words(client, bad):
    """A key goes into an HTTP header: the character from a copy raised UnicodeEncodeError (a 500 on Save) in the AI and the ArvanCloud settings."""
    r = client.put("/api/settings/ai", json={"enabled": True, "api_key": bad})
    assert r.status_code == 200
    st = r.json()["status"]
    assert st["ok"] is False and "copy it again" in st["error"]
    r = client.put("/api/settings/arvan", json={"api_key": bad, "mode": "off", "confirm_minutes": 5, "domains": []})
    assert r.status_code == 200 and "copy it again" in r.json()["status"]["error"]


# ---------------------------------------------------------------- facts
BASE = """CPU=1000 800 1100 870
NET=1000000 2000000 1500000 2600000
MEM=2000000 1200000 1000000 900000
DISK=40000000 8000000
LOAD=0.50 0.40 0.30 4
UP=86400
CONN=42
HOST=ir5 6.8.0-31-generic
"""


@pytest.mark.parametrize("raw,want", [("1000", 1000), ("0", None), ("-1", None), ("Unknown!", None), ("", None), ("inf", None), ("nan", None), ("1e12", None), ("2500.5", 2500), ("1000 2000", 1000)])
def test_the_line_speed_of_a_card_in_every_shape_it_is_reported(raw, want):
    from nikatel.services.facts import parse_facts

    assert parse_facts(BASE + f"LINK={raw}\n")["link_mbps"] == want


def test_a_damaged_answer_is_a_failed_sample_and_a_cosmetic_field_is_not():
    from nikatel.services.facts import parse_facts

    for bad in ("NET=inf 2 3 4", "UP=nan", "MEM=1 inf 3 4", "LOAD=0.5 0.4 0.3 inf"):
        key = bad.split("=")[0]
        text = "\n".join(bad if ln.startswith(key + "=") else ln for ln in BASE.splitlines()) + "\n"
        with pytest.raises(ValueError):
            parse_facts(text)

    assert parse_facts(BASE + "CONN=inf\n")["conn"] == 0 and parse_facts(BASE.replace("CONN=42", "CONN=many"))["conn"] == 0
    assert parse_facts(BASE + "TOP=Web Content\n")["top"] == "" and parse_facts(BASE + "TOP=nginx 12.4\n")["top"] == "nginx 12%"


def test_a_week_old_peak_of_a_server_that_is_idle_now_is_dropped_and_a_damaged_peak_does_not_stop_the_sample():
    from types import SimpleNamespace

    from nikatel.services import facts

    srv = SimpleNamespace(extra={"peak": {"v": 80.0, "at": int(time.time()) - facts.PEAK_SECONDS - 5}, "geo": "x"})
    facts._peak(srv, {"rx_mbps": 0.0, "tx_mbps": 0.0})
    assert "peak" not in srv.extra and srv.extra["geo"] == "x"
    young = SimpleNamespace(extra={"peak": {"v": 80.0, "at": int(time.time()) - 60}})
    facts._peak(young, {"rx_mbps": 0.0, "tx_mbps": 0.0})
    assert young.extra["peak"]["v"] == 80.0                                    # a young peak stays while the server is idle

    for junk in ("fast", 5, {"v": "x", "at": None}, {"v": None}, [1, 2]):
        s = SimpleNamespace(extra={"peak": junk})
        facts._peak(s, {"rx_mbps": 3.0, "tx_mbps": 4.0})                       # (no exception)
        assert s.extra["peak"]["v"] == 4.0


def test_a_server_that_does_not_come_back_from_a_reboot_gives_the_alarm_after_the_give_up_time(client, monkeypatch):
    from nikatel.services import facts, nodes

    sid = _server(online=True, extra={})
    monkeypatch.setattr(nodes, "run_action", lambda *a, **k: "")
    nodes.reboot(sid)

    def down(*a, **k):
        raise facts.SSHError("ssh connection lost")

    monkeypatch.setattr(facts, "run_script", down)
    facts.sample_server(sid)
    facts.sample_server(sid)

    with session() as db:
        assert db.get(Server, sid).extra.get("busy", {}).get("what") == "Rebooting" and not [e for e in db.scalars(select(Event)) if e.level == "bad"]
        s = db.get(Server, sid)
        s.extra = {**s.extra, "busy": {**s.extra["busy"], "at": int(time.time()) - facts.REBOOT_GIVE_UP - 5}}

    facts.sample_server(sid)

    with session() as db:
        assert "busy" not in db.get(Server, sid).extra
        bad = [e for e in db.scalars(select(Event)) if e.level == "bad"]
        assert len(bad) == 1 and "did not come back" in bad[0].text

    facts.sample_server(sid)
    with session() as db:
        assert len([e for e in db.scalars(select(Event)) if e.level == "bad"]) == 1          # (said once)


def test_a_damaged_busy_mark_does_not_stop_the_sample(client, monkeypatch):
    from nikatel.services import facts

    sid = _server(online=True, extra={"busy": {"what": "Rebooting", "at": "yesterday"}})
    monkeypatch.setattr(facts, "run_script", lambda *a, **k: BASE + "UP=5\n")
    facts.sample_server(sid)

    with session() as db:
        assert db.get(Server, sid).online is True and "busy" not in db.get(Server, sid).extra


# ---------------------------------------------------------------- a job survives one bad item
def test_one_server_that_raises_does_not_stop_the_round_or_the_trimming_of_old_samples(client, monkeypatch):
    from nikatel.services import facts

    a, b = _server("s1", "203.0.113.5"), _server("s2", "203.0.113.6")
    old = int(time.time()) - facts.KEEP_SAMPLES_SECONDS - 100

    with session() as db:
        db.add(ServerSample(server_id=a, ts=old, cpu=1, ram=1, disk=1, load1=1, rx_mbps=1, tx_mbps=1, conn=1))

    seen = []

    def sample(sid):
        seen.append(sid)

        if sid == a:
            raise RuntimeError("a damaged answer")

    monkeypatch.setattr(facts, "sample_server", sample)
    facts.sample_all()
    assert sorted(seen) == sorted([a, b])

    with session() as db:
        assert db.scalar(select(ServerSample).where(ServerSample.ts == old)) is None          # (the old sample went all the same)


def test_one_tunnel_that_raises_does_not_stop_the_round_or_the_trimming_of_old_checks(client, monkeypatch):
    from nikatel.services import tunnels

    i, f = _server("ir", "203.0.113.5", "iran"), _server("fo", "203.0.113.6")

    with session() as db:
        db.add(Tunnel(name="t1", type="gre", iran_server_id=i, foreign_server_id=f))
        db.add(Tunnel(name="t2", type="gre", iran_server_id=i, foreign_server_id=f))
        db.flush()
        t1, t2 = [t.id for t in db.scalars(select(Tunnel).order_by(Tunnel.id))]
        db.add(TunnelCheck(tunnel_id=t1, ts=1, ok=True))

    seen = []

    def check(tid, *a, **k):
        seen.append(tid)

        if tid == t1:
            raise RuntimeError("an odd record")

    monkeypatch.setattr(tunnels, "check_tunnel", check)
    monkeypatch.setattr(tunnels, "external_probe_all", lambda: None)
    tunnels.check_all()
    assert sorted(seen) == [t1, t2]

    with session() as db:
        assert db.scalar(select(TunnelCheck).where(TunnelCheck.ts == 1)) is None

    seen.clear()
    tunnels.check_of_servers([i])
    assert sorted(seen) == [t1, t2]


def test_a_job_that_has_no_row_yet_is_run_and_recorded(client):
    """`nikatel run-job NAME` runs a job whose row start_all() has not made: marking the start raised `None += 1`, logged a traceback and the result was never recorded."""
    from nikatel import jobs
    from nikatel.models import Job

    ran = []
    jobs.register("robust-ok", 60, lambda: ran.append(1))

    def broken():
        raise RuntimeError("one item crashed")

    jobs.register("robust-bad", 60, broken)

    try:
        jobs.run_once("robust-ok")
        jobs.run_once("robust-bad")
    finally:
        jobs._SPECS.pop("robust-ok", None)
        jobs._SPECS.pop("robust-bad", None)

    with session() as db:
        ok, bad = db.get(Job, "robust-ok"), db.get(Job, "robust-bad")
        assert ran == [1] and ok.runs == 1 and ok.last_ok > 0 and ok.running is False and ok.last_error == "" and ok.failures == 0
        assert bad.runs == 1 and bad.failures == 1 and "one item crashed" in bad.last_error and bad.running is False


# ---------------------------------------------------------------- limits on what the settings pages keep
def test_the_keys_and_numbers_of_the_settings_pages_have_limits(client):
    """The fuzz saved a 10 kB 'key' into three settings documents, a negative SSH port and a server id of 2**63."""
    big = "k" * 10240
    assert client.put("/api/settings/ai", json={"api_key": big}).status_code == 422
    assert client.put("/api/settings/ai/providers/openai", json={"api_key": big}).status_code == 422
    assert client.put("/api/settings/arvan", json={"api_key": big, "mode": "off", "confirm_minutes": 5, "domains": []}).status_code == 422
    assert client.put("/api/settings/probe", json={"api_token": big}).status_code == 422
    assert client.put("/api/settings/probe", json={"server_id": 2 ** 63}).status_code == 422
    assert client.put("/api/settings/probe", json={"server_id": -1}).status_code == 422
    assert client.put("/api/settings/shopbot", json={"host": "x", "ssh_port": -1}).status_code == 422
    assert client.put("/api/settings/shopbot", json={"host": "x", "ssh_port": 70000}).status_code == 422


# ---------------------------------------------------------------- two writers of one settings document
def test_two_writers_of_one_settings_document_do_not_lose_each_others_field(client, monkeypatch):
    """put() read the document, changed one field and wrote the whole document back: a second put that ran in between was lost (the standby job writes `failed`, the page writes `marked`...)."""
    import copy
    from types import SimpleNamespace

    from nikatel.services import settings as st

    n = [0]

    def slow(x):
        n[0] += 1

        if n[0] == 1:
            time.sleep(0.4)             # (the window between the read and the write of the first put)

        return copy.deepcopy(x)

    monkeypatch.setattr(st, "copy", SimpleNamespace(deepcopy=slow))             # (only the copies of settings.py are slowed)
    first = threading.Thread(target=lambda: st.put("standby", {"after_seconds": 9}))
    first.start()
    time.sleep(0.1)
    st.put("standby", {"touched": [3]})
    first.join()
    doc = st.get("standby")
    assert doc["after_seconds"] == 9 and doc["touched"] == [3]
