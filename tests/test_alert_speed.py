"""The alerts in seconds (1.67.0): a fault is told after `down_seconds`, a repair after `up_seconds`, the clock starts where the checker changed the state, a message Telegram
does not take is sent again in order, and the rules saved by older versions become seconds."""
import pytest
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Config, Event, Server, Setting, Tunnel
from nikatel.services import alerts
from nikatel.services import settings as st


@pytest.fixture
def telegram(monkeypatch):
    """Telegram in memory: `sent` is every text it was asked to send, `answers` the next answers (an empty list = everything is taken)."""
    sent, answers = [], []

    def fake(text, require_enabled=True):
        sent.append(text)
        return answers.pop(0) if answers else (True, "sent")

    monkeypatch.setattr(alerts, "send_text", fake)
    alerts._OUTBOX.clear()
    alerts._NEXT_TRY["at"] = 0.0
    yield sent, answers
    alerts._OUTBOX.clear()


def events():
    with session() as db:
        return [e.text for e in db.scalars(select(Event).where(Event.domain == "alerts").order_by(Event.id))]


def add_server(name="ir5", host="203.0.113.5", **kw):
    with session() as db:
        s = Server(name=name, host=host, role="iran", **kw)
        db.add(s)
        db.flush()
        return s.id


def set_server(sid, **kw):
    with session() as db:
        s = db.get(Server, sid)

        for k, v in kw.items():
            setattr(s, k, v)


def test_a_server_that_goes_off_and_comes_back_is_told_both_ways_after_the_seconds_of_the_rules(telegram):
    sent, _ = telegram
    sid = add_server(online=True)
    alerts.run(now=1000)
    set_server(sid, online=False, last_error="no answer on the SSH port 22: 2 quick checks in a row got no connection")
    alerts.run(now=1001)                                # the first look that sees it off
    alerts.run(now=1005)
    assert sent == []                                   # 4 s: a blip says nothing
    alerts.run(now=1011)                                # 10 s
    assert len(sent) == 1 and sent[0].startswith("🔴 DOWN (1)") and "server ir5" in sent[0] and "no answer on the SSH port" in sent[0]
    alerts.run(now=1016)
    assert len(sent) == 1                               # once

    set_server(sid, online=True, last_error="")
    alerts.run(now=1100)
    alerts.run(now=1110)
    assert len(sent) == 1                               # it has held for 10 s only
    alerts.run(now=1115)                                # 15 s
    assert len(sent) == 2 and sent[1].startswith("🟢 UP again (1)") and "was unreachable 1 min 39 s" in sent[1]
    alerts.run(now=1200)
    assert len(sent) == 2


def test_a_tunnel_is_told_from_the_moment_its_checker_called_it_down(telegram):
    sent, _ = telegram
    a, b = add_server("ir1", "203.0.113.1"), add_server("fo1", "203.0.113.2")

    with session() as db:
        db.add(Tunnel(name="t1", type="gre", iran_server_id=a, foreign_server_id=b, state="down", state_since=1000, last_error="the unit is not running"))

    alerts.run(now=1007)                                # we look 7 s after the checker wrote `down`
    assert sent == []
    alerts.run(now=1010)                                # 10 s after the checker, not 10 s after our first look
    assert len(sent) == 1 and "t1 (ir1 ⇄ fo1)" in sent[0] and "the unit is not running" in sent[0]

    with session() as db:
        t = db.scalar(select(Tunnel))
        t.state, t.state_since = "up", 1100

    alerts.run(now=1108)
    assert len(sent) == 1
    alerts.run(now=1115)
    assert len(sent) == 2 and "was down 1 min 40 s" in sent[1]


def test_a_config_is_told_the_same_way_and_the_old_wait_of_minutes_is_gone(telegram):
    sent, _ = telegram

    with session() as db:
        db.add(Config(name="Fastly", status="down", status_since=5000, last_error="curl: (28) SSL connection timeout"))

    alerts.run(now=5003)
    assert sent == []
    alerts.run(now=5010)
    assert len(sent) == 1 and "config Fastly" in sent[0] and "SSL connection timeout" in sent[0]


def test_the_owner_can_make_it_slower_or_instant(telegram):
    sent, _ = telegram
    sid = add_server(online=False, last_error="off")
    st.put("alerts", {"down_seconds": 0, "up_seconds": 0})
    alerts.run(now=100)
    assert len(sent) == 1                               # 0 s: the very first look
    set_server(sid, online=True)
    alerts.run(now=105)
    assert len(sent) == 2 and sent[1].startswith("🟢 UP")
    set_server(sid, online=False)
    st.put("alerts", {"down_seconds": 120})
    alerts.run(now=110)
    alerts.run(now=200)
    assert len(sent) == 2
    alerts.run(now=230)
    assert len(sent) == 3 and sent[2].startswith("🔴 DOWN")


def test_a_message_telegram_does_not_take_is_kept_and_sent_again_once(telegram):
    sent, answers = telegram
    add_server(online=False, last_error="off")
    st.put("alerts", {"down_seconds": 0, "up_seconds": 0})
    answers.append((False, "Telegram did not answer: timed out"))
    alerts.run(now=1000)
    assert len(sent) == 1 and len(alerts._OUTBOX) == 1                  # not taken: kept
    alerts.run(now=1002)
    assert len(sent) == 1                                               # (too soon to try again: the next try is 10 s after the last one)
    alerts._NEXT_TRY["at"] = 0.0                                        # ... 10 s pass
    alerts.run(now=1010)
    assert len(sent) == 2 and sent[0] == sent[1] and alerts._OUTBOX == []
    alerts.run(now=1020)
    assert len(sent) == 2                                               # sent once in the end: no second DOWN
    assert any("sent (after 2 tries" in t for t in events())


def test_the_messages_go_out_in_the_order_they_were_made_even_when_the_first_one_fails(telegram):
    sent, answers = telegram
    answers.append((False, "Telegram rejected the message: Too Many Requests: retry after 3"))
    alerts._deliver("bad", "🔴 DOWN (1)\n• a")
    alerts._deliver("ok", "🟢 UP again (1)\n• a")
    assert sent == ["🔴 DOWN (1)\n• a", "🔴 DOWN (1)\n• a", "🟢 UP again (1)\n• a"] and alerts._OUTBOX == []


def test_a_message_that_can_never_be_sent_is_dropped_with_the_reason_and_an_old_one_is_dropped_too(telegram):
    sent, answers = telegram
    answers.append((False, "Telegram is off or not configured"))
    alerts._deliver("bad", "🔴 DOWN (1)\n• a")
    assert len(sent) == 1 and alerts._OUTBOX == [] and any("not sent: Telegram is off or not configured" in t for t in events())
    alerts._OUTBOX.append({"level": "bad", "text": "🔴 DOWN (1)\n• old", "at": 1.0, "tries": 3})          # half an hour old
    alerts.flush(force=True)
    assert len(sent) == 1 and alerts._OUTBOX == [] and any("dropped" in t for t in events())
    assert alerts.retryable("Telegram did not answer: boom") and alerts.retryable("Telegram rejected the message: Too Many Requests: retry after 5")
    assert not alerts.retryable("Telegram is off or not configured") and not alerts.retryable("Telegram rejected the message: Bad Request: chat not found")


def test_a_muted_thing_and_a_maintenance_window_stay_quiet(telegram):
    sent, _ = telegram
    add_server(online=False, last_error="off")
    st.put("alerts", {"down_seconds": 0, "muted": ["ir5"]})
    alerts.run(now=100)
    assert sent == []
    st.put("alerts", {"muted": []})

    with session() as db:
        from nikatel.models import Maintenance

        db.add(Maintenance(start=0, end=10**10, note="upgrade"))

    alerts.run(now=200)
    assert sent == []


def test_the_durations_are_told_in_seconds_under_a_minute():
    assert alerts._dur(7) == "7 s" and alerts._dur(99) == "1 min 39 s" and alerts._dur(120) == "2 min" and alerts._dur(1800) == "30 min" and alerts._dur(6 * 3600) == "6 h 0 min"


def test_the_rules_saved_before_1_67_become_seconds_only_when_the_owner_had_changed_them(client):
    def save(v):
        with session() as db:
            row = db.get(Setting, "alerts")

            if row is None:
                db.add(Setting(key="alerts", value=v))
            else:
                row.value = v

    save({"tunnel_fails": 3, "server_fails": 2, "config_fails": 3, "min_down_minutes": 4, "up_stable_seconds": 600, "remind_hours": 0, "flap_max": 3, "flap_quiet_hours": 6, "muted": ["t1"]})
    d = st.get("alerts")
    assert d["down_seconds"] == 10 and d["up_seconds"] == 15 and d["muted"] == ["t1"]       # the old DEFAULTS were never his choice: the new ones apply
    assert not any(k in d for k in ("tunnel_fails", "server_fails", "config_fails", "min_down_minutes", "up_stable_seconds"))

    save({"tunnel_fails": 5, "server_fails": 2, "config_fails": 3, "min_down_minutes": 10, "up_stable_seconds": 120, "remind_hours": 2, "flap_max": 3, "flap_quiet_hours": 6, "muted": []})
    d = st.get("alerts")
    assert d["down_seconds"] == 600 and d["up_seconds"] == 120 and d["remind_hours"] == 2   # what he had set, in seconds
    assert client.get("/api/settings/alerts").json()["down_seconds"] == 600

    st.put("alerts", {"down_seconds": 30})
    with session() as db:
        stored = db.get(Setting, "alerts").value

    assert stored["down_seconds"] == 30 and stored["up_seconds"] == 120 and "min_down_minutes" not in stored          # the next save writes the new shape


def test_the_alerts_job_looks_every_five_seconds():
    """The look is the delay: it was a minute."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert 'jobs.register("alerts", 5, alerts.run)' in src and 'jobs.register("configs-fast"' in src and 'jobs.register("panels", 15' in src
