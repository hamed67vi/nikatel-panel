"""What Telegram answered the last time is kept and shown (1.69.252). The owner, 2026-11-09: "no backup is sent to the bot" - Telegram had refused the panel's bot for ten days while the Settings box said "● sending".
No test talks to Telegram: httpx.post is replaced."""
from pathlib import Path

import httpx as real_httpx
import pytest

from nikatel.services import settings as st
from nikatel.services import telegram

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
TOKEN = "123456789:" + "E" * 35
CHAT = "-1001234567890"


class Ans:
    def __init__(self, ok, description="", code=200):
        self.ok, self.description, self.status_code = ok, description, code

    def json(self):
        return {"ok": self.ok, **({"description": self.description} if self.description else {}), "result": {"message_thread_id": 7}}


@pytest.fixture(autouse=True)
def _nothing_remembered():
    telegram.forget()
    yield
    telegram.forget()


def _answer(monkeypatch, *a, **k):
    monkeypatch.setattr(telegram.httpx, "post", lambda *x, **y: Ans(*a, **k))


def test_a_refused_bot_is_remembered_with_since_when_and_how_often(monkeypatch):
    st.put("telegram", {"token": TOKEN, "chat_id": CHAT, "enabled": True})
    assert telegram.state() == {"ok": None, "why": "", "at": 0, "since": 0, "fails": 0}          # nothing was sent yet: not "sending", not "rejected"

    _answer(monkeypatch, False, "Unauthorized", 401)
    assert telegram.send_text("x") == (False, "Telegram rejected the message: Unauthorized")
    s1 = telegram.state()
    assert s1["ok"] is False and s1["why"] == "Unauthorized" and s1["fails"] == 1 and s1["since"] > 0 and s1["at"] >= s1["since"]

    ok, why = telegram.send_file("a.enc", b"data")
    assert not ok and "Unauthorized" in why
    assert telegram.create_topic("L2TP backups")[0] == 0
    s2 = telegram.state()
    assert s2["fails"] == 3 and s2["since"] == s1["since"]          # (the same answer three times: it began when it began)

    telegram._STATE.clear()          # the panel restarts: the answer was kept beside the database, the box does not turn green again
    assert telegram.state()["ok"] is False and telegram.state()["why"] == "Unauthorized" and telegram.state()["since"] == s1["since"]

    _answer(monkeypatch, True)
    assert telegram.send_text("x") == (True, "sent")
    s3 = telegram.state()
    assert s3["ok"] is True and s3["why"] == "" and s3["fails"] == 0 and s3["at"] > 0
    telegram._STATE.clear()
    assert telegram.state()["ok"] is True


def test_one_message_that_fails_or_a_network_error_does_not_make_the_connection_red(monkeypatch):
    st.put("telegram", {"token": TOKEN, "chat_id": CHAT, "enabled": True})
    _answer(monkeypatch, True)
    telegram.send_text("x")

    for desc in ("Bad Request: message thread not found", "Too Many Requests: retry after 5", "Request Entity Too Large"):
        _answer(monkeypatch, False, desc, 400)
        assert telegram.send_file("a.enc", b"x")[0] is False and telegram.state()["ok"] is True, desc

    def boom(*a, **k):
        raise real_httpx.ConnectError("no route")

    monkeypatch.setattr(telegram.httpx, "post", boom)
    assert telegram.send_text("x")[0] is False and telegram.state()["ok"] is True          # (Telegram did not answer at all: nothing is known about the bot)

    _answer(monkeypatch, False, "Bad Request: the chat is not a forum", 400)
    assert telegram.create_topic("x")[0] == 0 and telegram.state()["ok"] is True          # (about the topic, not about the bot)

    for desc in ("Forbidden: bot was kicked from the supergroup chat", "Bad Request: chat not found", "Forbidden: bot is not a member of the supergroup chat"):
        telegram.forget()
        _answer(monkeypatch, False, desc, 403)
        telegram.send_text("x")
        assert telegram.state()["ok"] is False and telegram.state()["why"] == desc, desc


def test_the_routes_say_it_and_another_token_or_chat_starts_clean(client, monkeypatch):
    assert client.put("/api/settings/telegram", json={"enabled": True, "token": TOKEN, "chat_id": CHAT}).json()["state"]["ok"] is None
    _answer(monkeypatch, False, "Unauthorized", 401)
    assert client.post("/api/settings/telegram/test").status_code == 502

    d = client.get("/api/settings/telegram").json()
    assert d["state"]["ok"] is False and d["state"]["why"] == "Unauthorized" and d["state"]["fails"] == 1 and d["token"] == "" and d["has_token"] is True
    assert client.get("/api/settings/backup").json()["telegram_state"]["ok"] is False          # (the backup pages read it from here)

    r = client.put("/api/settings/telegram", json={"enabled": True, "token": "", "chat_id": CHAT}).json()          # the same bot and chat saved again: the answer stands
    assert r["state"]["ok"] is False

    r = client.put("/api/settings/telegram", json={"enabled": True, "token": TOKEN.replace("E", "F"), "chat_id": CHAT}).json()          # another token: nothing is known about it yet
    assert r["state"] == {"ok": None, "why": "", "at": 0, "since": 0, "fails": 0}

    _answer(monkeypatch, True)
    assert client.post("/api/settings/telegram/test").status_code == 200
    assert client.get("/api/settings/telegram").json()["state"]["ok"] is True

    r = client.put("/api/settings/telegram", json={"enabled": True, "token": "", "chat_id": "-1009999999999"}).json()          # another chat: the same
    assert r["state"]["ok"] is None


def test_the_pages_say_a_refusal_in_red():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "const tgs = tg.state || {}, tgBad = tgOn && tgs.ok === false;" in app and "✕ rejected</span>" in app and "saved · not tried yet" in app and "tgs.ok === true ?" in app          # ("● sending" only when Telegram really took the last message)
    assert "<b>Nothing is delivered</b>" in app and "const tgWhy = w =>" in app and "const tgFix = w =>" in app and "@BotFather" in app and 'body: `${tgNote}<div class="ab-grid">' in app
    assert "✕ Telegram rejects the bot" in app and "html body #page .ab-row.tg-bad {" in css          # (the Backups card says it too: the files stay on the server)
    assert "const l2TgDown = (b, p) =>" in js and "b.telegram_state.ok === false" in js and "sc.ok === false || unsent ? false" in js and "Telegram refuses the panel's bot" in js          # (the L2TP backup cell and its window)
