"""The platform audit (2026-10-22): the sign-in page, the bounds of the lists, what a token may do, what a log line may look like, what an error prints, a double press of Update.
Each test failed before its fix (see the changelog)."""
import json
import logging
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nikatel import __version__
from nikatel import log as logmod
from nikatel.db import session
from nikatel.models import ActionLog, Event
from nikatel.services import settings as st
from nikatel.services import tokens, updater

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"


# ---------------------------------------------------------------- the sign-in page
def test_a_wrong_password_shows_the_servers_sentence_not_not_logged_in():
    """api() turned EVERY 401 into the sign-in page and "not logged in": the form of the sign-in page itself said "not logged in" under a wrong password. The sign-in routes are answers, not a lost session."""
    js = (WEB / "app.js").read_text(encoding="utf-8")
    line = next(ln for ln in js.splitlines() if "r.status === 401" in ln and "showLogin()" in ln)
    assert "api\\/auth" in line and "login" in line and "recover" in line, line          # (the regex of the carve-out: /api/auth/(login|recover) )


def test_the_sign_in_form_never_sends_the_password_in_the_address():
    """A form with no method is a GET: when the script of the page did not start (blocked, an error before the form was wired) Enter sent `/?user=…&password=…` - into the history, the proxy logs, the Referer."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    form = next(ln for ln in html.splitlines() if 'id="login-form"' in ln)
    assert 'method="post"' in form, form
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'document.querySelector("#login-form [name=password]")' in js and "a === document.body" in js          # (the cursor waits in the password box, never taken from a box in use)


def test_a_test_of_telegram_that_is_not_set_up_is_the_operators_to_do_not_a_bad_gateway(client):
    """"Test" on a panel with no Telegram answered 502 and the page showed it with a request id - as if Telegram had failed."""
    r = client.post("/api/settings/telegram/test")
    assert r.status_code == 400 and "not configured" in r.json()["error"] and r.json()["code"] == "bad_request", r.text
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "r.status >= 500 && !msg.includes(rid)" in js          # (an internal error says "(request id …)" itself: the id was shown twice)
    assert "row.scrollLeft = Math.max(0, on.offsetLeft" in js          # (on a phone the tab that is open scrolls into view in the row of Settings tabs)


def test_the_throttle_says_how_long_to_wait(client):
    anon = TestClient(client.app)

    for _ in range(5):
        assert anon.post("/api/auth/login", json={"user": "admin", "password": "wrong-wrong-1"}).status_code == 401

    r = anon.post("/api/auth/login", json={"user": "admin", "password": "wrong-wrong-1"})
    assert r.status_code == 429 and r.json()["code"] == "too_many_requests"
    assert 800 <= int(r.headers["Retry-After"]) <= 900


# ---------------------------------------------------------------- lists have bounds
@pytest.mark.parametrize("url", ["/api/events?limit=-1", "/api/events?limit=0", "/api/events?limit=501", "/api/events?after=-1", "/api/actions?limit=-1", "/api/actions?limit=0", "/api/actions?limit=201", "/api/analyst?days=0", "/api/analyst?days=-3", "/api/analyst?days=100000"])
def test_a_limit_that_means_everything_or_nothing_is_refused(client, url):
    """`limit=-1` is SQLite's "no limit": the whole events table (19,000 rows on the live panel) or every action, each one read again by its own query."""
    r = client.get(url)
    assert r.status_code == 422 and r.json()["code"] == "invalid", (url, r.status_code, r.text[:100])


def test_the_limits_the_page_uses_still_work(client):
    with session() as db:
        for i in range(30):
            db.add(Event(level="info", domain="servers", text=f"e{i}"))

    assert len(client.get("/api/events?limit=500").json()["events"]) >= 30          # (the sign-in of the test client is an event too)
    assert len(client.get("/api/events?limit=7").json()["events"]) == 7
    assert client.get("/api/actions?limit=200&kinds=fix,autopilot").status_code == 200
    assert client.get("/api/analyst?days=7").status_code == 200 and client.get("/api/analyst?days=30").status_code == 200

    with session() as db:
        assert db.query(ActionLog).count() == 0          # (nothing is made by reading)


# ---------------------------------------------------------------- what a token may do
def test_a_token_that_starts_work_may_not_change_a_servers_password_or_pause_a_job():
    assert not tokens.allowed("act", "POST", "/api/servers/3/password") and not tokens.allowed("read", "POST", "/api/servers/3/password")
    assert not tokens.allowed("act", "POST", "/api/jobs/alerts/enabled") and not tokens.allowed("read", "POST", "/api/jobs/alerts/enabled")
    assert tokens.allowed("admin", "POST", "/api/servers/3/password") and tokens.allowed("admin", "POST", "/api/jobs/alerts/enabled")
    assert tokens.allowed("act", "POST", "/api/jobs/alerts/run") and tokens.allowed("act", "POST", "/api/servers/3/check") and tokens.allowed("read", "GET", "/api/jobs")


def test_an_act_token_gets_403_on_the_password_route(client, monkeypatch):
    monkeypatch.setattr(tokens, "_LAST_USED", {})          # (ids start at 1 in every test database: the "touched a minute ago" note of an earlier test must not carry over)
    tok =client.post("/api/tokens", json={"name": "bot", "scope": "act", "password": "test-password-1"}).json()["token"]
    anon = TestClient(client.app)
    h = {"Authorization": "Bearer " + tok}
    r = anon.post("/api/servers/1/password", json={"password": "a-new-password"}, headers=h)
    assert r.status_code == 403 and r.json()["code"] == "token_scope"
    assert anon.post("/api/jobs/alerts/enabled?enabled=false", headers=h).status_code == 403


# ---------------------------------------------------------------- the log
def _line(msg, exc=None):
    rec = logging.LogRecord("t", logging.WARNING, __file__, 1, "%s", (msg,), exc)
    return logmod._Formatter().format(rec)


def test_a_wrong_verb_has_its_own_code(client):
    r = client.delete("/api/health")
    assert r.status_code == 405 and r.json() == {"ok": False, "error": "Method Not Allowed", "code": "method_not_allowed"}


def test_a_stranger_cannot_forge_a_line_of_the_log():
    """A message the browser sends (`POST /client-error`: any text with a line break), a header, an answer of a server: all of it is written to the log, and the Logs page reads the file line by line - a forged
    line showed as a real ERROR (checked on the code before the fix: the row `jobs - job alerts failed` appeared). (A path with `%0a` loses the break before it is logged: not a way.)"""
    fake = "2026-10-22T00:00:00Z ERROR   jobs       job alerts failed rid=job:alerts"
    out = _line("GET /api/x\r\n" + fake + "\x0b" + fake + " " + fake)
    lines = out.split("\n")
    assert len(lines) == 2, out
    assert logmod._LOOKS_LIKE_A_RECORD.match(lines[0]) and not logmod._LOOKS_LIKE_A_RECORD.match(lines[1]) and lines[1].startswith("  "), lines
    assert "\r" not in out and "\x0b" not in out and " " not in out


def test_a_forged_line_inside_an_exception_text_is_pushed_in_too():
    try:
        raise ValueError("bad\n2026-10-22T00:00:00Z ERROR   x          fake rid=1")
    except ValueError as e:
        out = _line("failed", (type(e), e, e.__traceback__))

    assert not any(logmod._LOOKS_LIKE_A_RECORD.match(ln) for ln in out.split("\n")[1:]), out
    assert "Traceback" in out and "ValueError: bad" in out         # (the story is still all there)


def test_the_logs_page_does_not_show_a_forged_record(client, tmp_path, monkeypatch):
    from nikatel.config import get_settings

    logmod.setup("INFO", get_settings().log_dir)
    logging.getLogger("http").warning("%s", "GET /api/x\n2026-10-22T00:00:00Z ERROR   jobs       job alerts failed rid=job:alerts")

    for h in logging.getLogger().handlers:
        h.flush()

    rows = client.get("/api/logs?level=ERROR").json()["lines"]
    assert not any("job alerts failed" in r["text"] and r["domain"] == "jobs" for r in rows), rows


def test_the_secrets_a_log_line_can_carry_are_all_hidden():
    key = "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    secret_bot = "1234567890" + ":" + key          # (put together here: a whole token in a file is what the secrets check looks for)
    for text in (f"POST https://api.telegram.org/bot{secret_bot}/sendMessage failed", f"token {secret_bot} bad", "12345678901" + ":" + key,
                 "{'token': 'abcdef123456', 'chat_id': 5}", '{"api_key": "live-abcdefghijk", "x": 1}', '{"password": "hunter2hunter2"}', "payload: {'api_token': 'zzzzzzzzzz'}"):
        out = logmod.mask(text)
        assert not any(s in out for s in ("AAHdqTcv", "abcdef123456", "live-abcdefghijk", "hunter2hunter2", "zzzzzzzzzz")), (text, out)

    assert logmod.mask("tokens: 3, secrets are safe, the token is fine") == "tokens: 3, secrets are safe, the token is fine"          # (words that only look like it stay)
    assert logmod.mask("url=https://x.test/sub?token=abcdef&name=a") == "url=https://x.test/sub?token=<hidden>&name=a"


def test_a_failed_statement_does_not_print_its_values():
    """`UPDATE settings SET value=?` that fails ("database is locked") printed [parameters: ...] = the whole document: the bot token, the AI key. The engine hides the values now."""
    from sqlalchemy.exc import IntegrityError

    from nikatel.models import Setting

    secret = "9876543210" + ":" + "AAHsecretsecretsecretsecretsecret12345"

    with pytest.raises(IntegrityError) as ei, session() as db:
        db.add(Setting(key="dup", value={"token": secret}))
        db.flush()
        db.add(Setting(key="dup", value={"token": secret}))
        db.flush()

    assert secret not in str(ei.value) and "hidden" in str(ei.value)


def test_a_session_held_for_long_is_named_in_the_log(monkeypatch, caplog):
    """The live panel had a quarter of an hour of "database is locked" and the log could not say who held the connection. A session open longer than SLOW_SESSION is named with the line that opened it."""
    from nikatel import db as dbmod

    monkeypatch.setattr(dbmod, "SLOW_SESSION", 0.05)

    with caplog.at_level(logging.WARNING, logger="db"):
        with session():          # (this line is the one the log must name)
            time.sleep(0.12)

        with session():
            pass

    msgs = [r.getMessage() for r in caplog.records if "held for" in r.getMessage()]
    assert len(msgs) == 1 and "test_platform_qa.py" in msgs[0] and "test_a_session_held_for_long" in msgs[0], msgs


def test_the_backup_passphrase_is_one_line_with_twelve_real_characters(client, tmp_path, monkeypatch):
    from nikatel.services import backup

    monkeypatch.setattr(backup, "pass_file", lambda: tmp_path / "backup.pass")          # (the data dir of the tests is shared: a passphrase left there changes the next test, "no passphrase yet")

    for bad in ("abcdefghijkl\nmnop", "abcdefghijkl\r", "x" * 12 + "\x00", " " * 12, "abc def     "):
        r = client.post("/api/settings/backup/passphrase", json={"passphrase": bad})
        assert r.status_code == 400, (bad, r.status_code)

    assert not backup.has_passphrase()
    assert client.post("/api/settings/backup/passphrase", json={"passphrase": "  a good passphrase  "}).status_code == 200
    assert backup.pass_file().read_text() == "a good passphrase" and backup.has_passphrase()          # (what is kept is what is used)


# ---------------------------------------------------------------- a body with no Content-Length
def test_a_chunked_body_over_the_limit_is_a_413_before_it_is_read_whole(client, monkeypatch):
    """The 8 MB limit looked at `Content-Length` only: a body sent in chunks (no length) was read whole and parsed, before any sign-in (40 MB: +88 MB of the panel's memory; the box has no swap)."""
    from nikatel import main

    monkeypatch.setattr(main, "MAX_BODY", 10_000)
    anon = TestClient(client.app)
    sent = []

    def chunks(n):
        for _ in range(n):
            sent.append(1)
            yield b'{"user": "' + b"A" * 5990

    r = anon.post("/api/auth/login", content=chunks(40), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json() == {"ok": False, "error": "the request is too big", "code": "too_large"}, (r.status_code, r.text[:100])
    ok = anon.post("/api/auth/login", content=iter([b'{"user": "admin", ', b'"password": "wrong-wrong-1"}']), headers={"Content-Type": "application/json"})
    assert ok.status_code == 401          # (a small chunked body is still an ordinary request)


# ---------------------------------------------------------------- a link typed by the operator is not a way into the host
@pytest.mark.parametrize("url", ["http://127.0.0.1:9443/x", "http://localhost/x", "https://[::1]/x", "http://169.254.169.254/latest/meta-data/", "http://0.0.0.0/x", "http://[::ffff:127.0.0.1]/x", "http://2130706433/x"])
def test_a_subscription_link_to_the_machine_itself_is_refused(client, monkeypatch, url):
    from nikatel.services import configs

    def never(*a, **k):
        raise AssertionError("the panel downloaded from an internal address")

    monkeypatch.setattr(configs.httpx, "get", never)
    r = client.post("/api/subscriptions/preview", json={"name": "grp", "url": url})
    assert r.status_code in (400, 422) and "machine the panel runs on" in r.json()["error"], (url, r.status_code, r.text[:120])


def test_a_redirect_to_an_internal_address_is_not_followed(client, monkeypatch):
    from nikatel.services import configs

    calls = []

    class Redirect:
        status_code, content, is_redirect = 302, b"", True

        def __init__(self):
            self.headers = {"location": "http://127.0.0.1:9443/admin"}

    monkeypatch.setattr(configs.httpx, "get", lambda url, *a, **k: calls.append(url) or Redirect())
    monkeypatch.setattr("nikatel.netguard.socket.getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("203.0.113.7" if host == "sub.example.net" else "127.0.0.1", 0))])
    r = client.post("/api/subscriptions/preview", json={"name": "grp", "url": "https://sub.example.net/x"})
    assert r.status_code in (400, 422) and "machine the panel runs on" in r.json()["error"], r.text
    assert calls == ["https://sub.example.net/x"]          # (the first hop was made, the second never)


def test_a_private_address_and_a_name_that_does_not_resolve_pass_the_look():
    from nikatel import netguard

    netguard.check_public_url("http://10.0.0.5/sub")
    netguard.check_public_url("https://this-name-does-not-exist.invalid/sub")
    netguard.check_public_url("https://203.0.113.7/sub")

    with pytest.raises(ValueError):
        netguard.check_public_url("http:///nohost")


# ---------------------------------------------------------------- the mute list
def test_the_mute_list_has_bounds_and_keeps_every_name(client):
    assert client.post("/api/settings/alerts/mute?ref=" + "x" * 121).status_code == 422
    assert client.post("/api/settings/alerts/mute?ref=").status_code == 422
    errs = []

    def go(i):
        try:
            r = client.post(f"/api/settings/alerts/mute?ref=tun-{i}")
            assert r.status_code == 200, r.text
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert st.get("alerts")["muted"] == sorted(f"tun-{i}" for i in range(12))          # (a read-change-write with no lock lost names)
    assert client.post("/api/settings/alerts/mute?ref=tun-3&muted=false").json()["muted"].count("tun-3") == 0


def test_the_mute_list_is_capped(client, monkeypatch):
    from nikatel.api import alerts as api_alerts

    monkeypatch.setattr(api_alerts, "MAX_MUTES", 3)

    for i in range(3):
        assert client.post(f"/api/settings/alerts/mute?ref=m{i}").status_code == 200

    r = client.post("/api/settings/alerts/mute?ref=m9")
    assert r.status_code == 400 and "at most 3" in r.json()["error"]
    assert client.post("/api/settings/alerts/mute?ref=m1").status_code == 200          # (one that is there already is not "one more")


# ---------------------------------------------------------------- Update twice at one moment
def test_two_presses_of_update_at_one_moment_start_one_run(tmp_path, monkeypatch):
    """The look at the progress and the first write were two steps: two presses started two runs with ONE unit name (the seconds are the name), and the second `systemd-run` wrote "failed" over the run that was going."""
    monkeypatch.setattr(updater, "PROGRESS_FILE", tmp_path / "update-progress.json")
    monkeypatch.setattr(updater, "LOG_FILE", tmp_path / "update.log")
    src = tmp_path / "src"
    (src / "nikatel").mkdir(parents=True)
    (src / "nikatel" / "__init__.py").write_text(f'__version__ = "{__version__}"\n')
    monkeypatch.setattr(updater, "SRC", src)
    units = []

    def fake_run(cmd, **kw):
        if cmd[0] == "git":
            time.sleep(0.4)          # (the download takes a moment: the second press comes in during it)
        else:
            units.append(cmd)

        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    answers = []
    ts = [threading.Thread(target=lambda: answers.append(updater.start())) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(units) == 1 and sum(1 for a in answers if a.get("started")) == 1, answers
    assert sum(1 for a in answers if a.get("code") == "conflict") == 3
    assert json.loads(updater.PROGRESS_FILE.read_text())["step"] != "failed"


def test_a_failed_update_answers_with_a_code(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "PROGRESS_FILE", tmp_path / "update-progress.json")
    monkeypatch.setattr(updater, "LOG_FILE", tmp_path / "update.log")
    monkeypatch.setattr(updater.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fatal: no route\n"))
    r = updater.start()
    assert r["ok"] is False and r["code"] == "upstream_failed" and "no route" in r["error"]


# ---------------------------------------------------------------- one comparison cannot be left running by a dead thread
def test_a_comparison_with_an_id_the_database_cannot_hold_ends(client):
    from nikatel.services import tunnel_test as tt

    jid = tt.compare_start(2 ** 63, 2 ** 63, ["tcpmux"])

    for _ in range(50):
        st_ = tt.compare_status(jid)

        if st_.get("done"):
            break

        time.sleep(0.1)

    assert st_.get("done") is True, st_
    assert st_["results"] and st_["results"][0]["ok"] is False
