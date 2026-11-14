"""The alerts, the daily report and the test message go to ONE topic of the Telegram group (1.69.269). The owner, 2026-11-14: the panel's test line had landed in the statistics topic - "I want it in Monitor".
No test talks to Telegram: httpx.post is replaced."""
from pathlib import Path

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
        return {"ok": self.ok, **({"description": self.description} if self.description else {}), "result": {"message_id": 1}}


@pytest.fixture(autouse=True)
def _nothing_remembered():
    telegram.forget()
    yield
    telegram.forget()


def _record(monkeypatch, *answers):
    """Every request is kept; the answers come in turn (the last one repeats)."""
    sent, left = [], list(answers)

    def post(url, **k):
        sent.append(k.get("json"))
        return left.pop(0) if len(left) > 1 else left[0]

    monkeypatch.setattr(telegram.httpx, "post", post)
    return sent


def test_no_topic_means_no_thread_id_in_the_request(monkeypatch):
    st.put("telegram", {"token": TOKEN, "chat_id": CHAT, "enabled": True})
    sent = _record(monkeypatch, Ans(True))
    assert telegram.send_text("hello") == (True, "sent")
    assert len(sent) == 1 and sent[0]["chat_id"] == CHAT and sent[0]["text"] == "hello" and "message_thread_id" not in sent[0]


def test_a_topic_goes_into_the_request_as_a_number(monkeypatch):
    st.put("telegram", {"token": TOKEN, "chat_id": CHAT, "enabled": True, "thread_id": 13678})
    sent = _record(monkeypatch, Ans(True))
    assert telegram.send_text("hello") == (True, "sent")
    assert len(sent) == 1 and sent[0]["message_thread_id"] == 13678 and isinstance(sent[0]["message_thread_id"], int) and sent[0]["chat_id"] == CHAT


def test_a_deleted_topic_does_not_lose_the_message(monkeypatch):
    st.put("telegram", {"token": TOKEN, "chat_id": CHAT, "enabled": True, "thread_id": 13678})
    sent = _record(monkeypatch, Ans(False, "Bad Request: message thread not found", 400), Ans(True))
    assert telegram.send_text("alert") == (True, "sent")
    assert len(sent) == 2 and sent[0]["message_thread_id"] == 13678 and "message_thread_id" not in sent[1] and sent[1]["text"] == "alert"
    assert telegram.state()["ok"] is True


def test_another_refusal_is_one_request_and_is_remembered(monkeypatch):
    st.put("telegram", {"token": TOKEN, "chat_id": CHAT, "enabled": True, "thread_id": 13678})
    sent = _record(monkeypatch, Ans(False, "Unauthorized", 401))
    assert telegram.send_text("x") == (False, "Telegram rejected the message: Unauthorized")
    assert len(sent) == 1 and telegram.state()["ok"] is False and telegram.state()["why"] == "Unauthorized"


def test_the_default_is_the_main_page():
    assert st.DEFAULTS["telegram"]["thread_id"] == 0


def test_the_route_stores_keeps_and_clears_the_topic(client, monkeypatch):
    r = client.put("/api/settings/telegram", json={"enabled": True, "token": TOKEN, "chat_id": CHAT, "thread_id": 13678}).json()
    assert r["thread_id"] == 13678 and client.get("/api/settings/telegram").json()["thread_id"] == 13678

    _record(monkeypatch, Ans(True))
    r = client.put("/api/settings/telegram", json={"enabled": True, "token": "", "chat_id": CHAT}).json()          # a screen that does not know the field: the topic stays
    assert r["thread_id"] == 13678

    sent = _record(monkeypatch, Ans(True))
    assert client.post("/api/settings/telegram/test").status_code == 200
    assert sent[-1]["message_thread_id"] == 13678          # the test message goes where the alerts go

    r = client.put("/api/settings/telegram", json={"enabled": True, "token": "", "chat_id": CHAT, "thread_id": 0}).json()          # 0 = the main page again
    assert r["thread_id"] == 0
    sent = _record(monkeypatch, Ans(True))
    assert client.post("/api/settings/telegram/test").status_code == 200 and "message_thread_id" not in sent[-1]

    assert client.put("/api/settings/telegram", json={"enabled": True, "token": "", "chat_id": CHAT, "thread_id": -5}).status_code == 422


def test_another_topic_starts_clean(client, monkeypatch):
    assert client.put("/api/settings/telegram", json={"enabled": True, "token": TOKEN, "chat_id": CHAT, "thread_id": 5}).json()["state"]["ok"] is None
    _record(monkeypatch, Ans(False, "Unauthorized", 401))
    assert client.post("/api/settings/telegram/test").status_code == 502
    assert client.put("/api/settings/telegram", json={"enabled": True, "token": "", "chat_id": CHAT, "thread_id": 5}).json()["state"]["ok"] is False          # (the same topic saved again: the answer stands)
    assert client.put("/api/settings/telegram", json={"enabled": True, "token": "", "chat_id": CHAT, "thread_id": 6}).json()["state"]["ok"] is None          # (another topic: nothing is known about it)


def test_the_settings_form_has_the_field_and_sends_it_as_a_number():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'abF("Topic id"' in app and 'name="thread_id"' in app and 'formJson(e, ["thread_id"], ["enabled"])' in app
    assert 'abF("Chat id"' in app and 'abF("Bot token"' in app
