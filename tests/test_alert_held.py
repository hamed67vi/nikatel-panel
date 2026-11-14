"""Alerts that Telegram refuses are KEPT and sent in one message when it works again, and the page says out loud that they do not arrive (1.69.269).
The owner, 2026-11-14: the bot token had been refused ("Unauthorized") since 2026-10-30 and every DOWN message of those days was dropped - "yes, build" the suggestion that the alerts must never go silent."""
import time
from pathlib import Path

import pytest

from nikatel.db import session
from nikatel.models import Event
from nikatel.services import alerts, telegram
from nikatel.services import settings as st

NOW = 1_800_000_000
REFUSED = "Telegram rejected the message: Unauthorized"


def msg(text, at, level="bad"):
    return {"level": level, "text": text, "at": at, "tries": 0}


@pytest.fixture
def held(monkeypatch):
    sent = []
    alerts._OUTBOX.clear()
    alerts._NEXT_TRY["at"] = 0.0
    alerts._HELD_AT["at"] = 0.0
    alerts._VIEW["at"] = 0.0
    st.put("alerts_held", {"items": [], "lost": 0, "why": ""})
    yield sent
    alerts._OUTBOX.clear()
    st.put("alerts_held", {"items": [], "lost": 0, "why": ""})
    st.put("telegram", {"enabled": False, "token": "", "chat_id": ""})


def events(text):
    with session() as db:
        return [e.text for e in db.query(Event).all() if text in e.text]


# ------------------------------------------------------------------ what is a refusal of the whole connection
def test_a_refused_bot_or_chat_is_fatal_a_timeout_or_a_slow_down_is_not():
    for why in (REFUSED, "Telegram rejected the message: Forbidden: bot was blocked by the user", "Telegram rejected the message: Bad Request: chat not found", "Unauthorized"):
        assert telegram.is_fatal(why), why

    for why in ("Telegram did not answer: timed out", "Telegram rejected the message: Too Many Requests: retry after 5", "Telegram is off or not configured", ""):
        assert not telegram.is_fatal(why), why


# ------------------------------------------------------------------ the messages are held, not dropped
def test_messages_telegram_refuses_are_held_and_the_bot_is_asked_only_once(monkeypatch, held):
    calls = []
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: calls.append(text) or (False, REFUSED))
    alerts._OUTBOX.extend([msg("🔴 DOWN (1)\n• tunnel a-tun — port 8075 accepts connections", NOW), msg("🟢 UP again (1)\n• tunnel a-tun — was down 2m", NOW + 120, "ok"), msg("🔴 DOWN (1)\n• server s-1 unreachable", NOW + 300)])
    alerts.flush(force=True)
    assert len(calls) == 1                                                                         # (the first refusal is enough: the other two are not sent to a bot that refuses)
    assert alerts._OUTBOX == []
    items = st.get("alerts_held")["items"]
    assert [x["text"].splitlines()[0] for x in items] == ["🔴 DOWN (1)", "🟢 UP again (1)", "🔴 DOWN (1)"] and items[0]["at"] == NOW
    assert len(events("held: Telegram refuses the bot")) >= 3                                      # (the events say "held", no longer "not sent")


def test_a_timeout_is_still_retried_and_a_wrong_setup_is_still_not_held(monkeypatch, held):
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: (False, "Telegram did not answer: timed out"))
    alerts._OUTBOX.append(msg("🔴 DOWN (1)\n• tunnel b-tun", time.time()))
    alerts.flush(force=True)
    assert len(alerts._OUTBOX) == 1 and st.get("alerts_held")["items"] == []                       # (kept in the outbox for the next look, as before)
    alerts._OUTBOX.clear()
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: (False, "Telegram is off or not configured"))
    alerts._OUTBOX.append(msg("🔴 DOWN (1)\n• tunnel c-tun", time.time()))
    alerts.flush(force=True)
    assert alerts._OUTBOX == [] and st.get("alerts_held")["items"] == []                           # (Telegram is switched off on purpose: nothing piles up for ever)


def test_the_list_keeps_the_newest_sixty_and_counts_what_it_dropped(monkeypatch, held):
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: (False, REFUSED))

    for i in range(65):
        alerts._OUTBOX.append(msg(f"🔴 DOWN (1)\n• tunnel t{i}", NOW + i))

    alerts.flush(force=True)
    d = st.get("alerts_held")
    assert len(d["items"]) == alerts.HELD_MAX == 60 and d["items"][-1]["text"].endswith("t64") and d["lost"] == 5


# ------------------------------------------------------------------ the digest
def test_the_digest_lists_the_held_alerts_oldest_first_with_their_times():
    items = [{"at": NOW, "level": "bad", "text": "🔴 DOWN (1)\n• tunnel a-tun — port 8075 accepts connections"}, {"at": NOW + 600, "level": "ok", "text": "🟢 UP again (1)\n• tunnel a-tun — was down 10m"}]
    text = alerts.held_digest(items, 0, NOW + 3600, 210)
    assert "2 alert(s)" in text and "11:30" in text and "11:40" in text                            # (the times are Tehran's: 08:00 UTC = 11:30)
    assert text.index("DOWN (1)") < text.index("UP again (1)") and "a-tun" in text and "dropped" not in text
    assert "(+7 older" in alerts.held_digest(items, 7, NOW + 3600, 210)
    assert len(alerts.held_digest([{"at": NOW + i, "level": "bad", "text": "🔴 DOWN\n" + "x" * 500} for i in range(60)], 0, NOW + 3600)) <= 3900


def test_the_held_alerts_go_out_in_one_message_when_telegram_works_again(monkeypatch, held):
    st.put("alerts_held", {"items": [{"at": int(time.time()) - 600, "level": "bad", "text": "🔴 DOWN (1)\n• tunnel d-tun"}, {"at": int(time.time()) - 60, "level": "ok", "text": "🟢 UP again (1)\n• tunnel d-tun"}], "lost": 0, "why": REFUSED})
    calls = []
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: calls.append(text) or (False, REFUSED))
    alerts.release_held()
    assert len(calls) == 1 and len(st.get("alerts_held")["items"]) == 2                            # (still refused: kept)
    alerts.release_held()
    assert len(calls) == 1                                                                         # (and not asked again before five minutes)
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: calls.append(text) or (True, "sent"))
    alerts._HELD_AT["at"] = 0.0
    alerts.release_held()
    assert len(calls) == 2 and "d-tun" in calls[1] and st.get("alerts_held")["items"] == []        # (ONE message, and the list is empty)
    assert events("held alert(s) were sent in one message")


def test_the_alerts_held_for_more_than_three_days_are_dropped(monkeypatch, held):
    now = int(time.time())
    st.put("alerts_held", {"items": [{"at": now - 4 * 86400, "level": "bad", "text": "🔴 DOWN\n• ancient"}, {"at": now - 60, "level": "bad", "text": "🔴 DOWN\n• fresh"}], "lost": 0, "why": REFUSED})
    calls = []
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: calls.append(text) or (True, "sent"))
    alerts.release_held()
    assert len(calls) == 1 and "fresh" in calls[0] and "ancient" not in calls[0]


def test_a_message_that_goes_through_releases_the_held_ones_first_in_the_same_look(monkeypatch, held):
    now = int(time.time())
    st.put("alerts_held", {"items": [{"at": now - 900, "level": "bad", "text": "🔴 DOWN (1)\n• tunnel e-tun"}], "lost": 0, "why": REFUSED})
    sent = []
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: sent.append(text) or (True, "sent"))
    alerts._OUTBOX.append(msg("🔴 DOWN (1)\n• tunnel f-tun", now))
    alerts.flush(force=True)
    assert len(sent) == 2 and "f-tun" in sent[0] and "e-tun" in sent[1] and st.get("alerts_held")["items"] == []      # (Telegram works again: the new one, then the digest of what it missed)


# ------------------------------------------------------------------ the page
def test_the_banner_says_when_alerts_do_not_arrive(monkeypatch, held):
    monkeypatch.setattr(telegram, "state", lambda: {"ok": False, "why": REFUSED, "at": NOW, "since": NOW - 86400, "fails": 9})
    st.put("telegram", {"enabled": True, "token": "", "chat_id": ""})
    v = alerts.delivery_view(NOW)
    assert v["bad"] is True and "NOT reaching you" in v["text"] and "Unauthorized" in v["text"] and v["held"] == 0
    st.put("alerts_held", {"items": [{"at": NOW, "level": "bad", "text": "x"}] * 3, "lost": 0, "why": ""})
    alerts._VIEW["at"] = 0.0
    assert "3 alert(s) wait" in alerts.delivery_view(NOW)["text"]
    monkeypatch.setattr(telegram, "state", lambda: {"ok": True, "why": "", "at": NOW, "since": NOW, "fails": 0})
    alerts._VIEW["at"] = 0.0
    v = alerts.delivery_view(NOW)
    assert v["bad"] is True and "waiting for Telegram" in v["text"]                                # (it works again but the held ones have not gone out yet)
    st.put("alerts_held", {"items": [], "lost": 0, "why": ""})
    alerts._VIEW["at"] = 0.0
    assert alerts.delivery_view(NOW)["bad"] is False                                               # (all is well: no banner)
    monkeypatch.setattr(telegram, "state", lambda: {"ok": False, "why": REFUSED, "at": NOW, "since": NOW, "fails": 1})
    st.put("telegram", {"enabled": False})
    alerts._VIEW["at"] = 0.0
    assert alerts.delivery_view(NOW)["bad"] is False                                               # (switched off on purpose: no banner)


def test_the_view_is_kept_for_ten_seconds(monkeypatch, held):
    n = []
    monkeypatch.setattr(telegram, "state", lambda: n.append(1) or {"ok": True, "why": "", "at": 0, "since": 0, "fails": 0})
    st.put("telegram", {"enabled": True})
    alerts.delivery_view(NOW)
    alerts.delivery_view(NOW + 5)
    alerts.delivery_view(NOW + 11)
    assert len(n) == 2                                                                             # (the page asks every two seconds: the state is read twice, not five times)


def test_the_brief_the_page_asks_every_two_seconds_carries_it_and_the_page_paints_it():
    from nikatel.api import tunnels as tapi

    alerts._VIEW["at"] = 0.0
    assert "alerts" in tapi.tunnels_brief() and "bad" in tapi.tunnels_brief()["alerts"]
    root = Path(__file__).resolve().parent.parent / "nikatel" / "web"
    app, html, css = (root / "app.js").read_text(encoding="utf-8"), (root / "index.html").read_text(encoding="utf-8"), (root / "app.css").read_text(encoding="utf-8")
    assert "paintAlertBanner(r.alerts)" in app and "const paintAlertBanner" in app
    assert 'id="alert-banner"' in html and ".alert-banner" in css
