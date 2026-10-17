"""One cause, one message (1.68.1; the owner, 2026-10-17: fourteen configs at once, "DOWN (14)", every one with the same curl error toward the same address - "why so many errors for one small disturbance?").
Things of one kind that fail TOGETHER have one cause: their messages wait (a disturbance that is over by then is not told at all) and what is left is ONE line with the count, the error they share, a few names and the likely cause."""
import pytest
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Config, Event, Server
from nikatel.services import alerts
from nikatel.services import settings as st

ERR = "curl: (35) OpenSSL SSL_connect: SSL_ERROR_SYSCALL in connection to www.gstatic.com:443"
NAMES = ["Turkey-1", "Germany-1", "Finland", "Emirates", "France", "Poland", "Canada", "UnitedKingdom", "Sweden", "Czech", "Italy", "CDN", "Nikatel-bot Turkey-2", "Nikatel-bot Germany-2"]


@pytest.fixture
def telegram(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_text", lambda text, require_enabled=True: sent.append(text) or (True, "sent"))
    alerts._OUTBOX.clear()
    alerts._NEXT_TRY["at"] = 0.0
    yield sent
    alerts._OUTBOX.clear()


def add_configs(status="up", since=0, error="", names=NAMES):
    with session() as db:
        for i, n in enumerate(names):
            db.add(Config(name=n, status=status, status_since=since + i, last_error=error))


def set_configs(status, since, error="", names=NAMES, stagger=1):
    with session() as db:
        for i, c in enumerate(db.scalars(select(Config).where(Config.name.in_(names)))):
            c.status, c.status_since, c.last_error = status, since + (i * stagger), error


# ------------------------------------------------------------------ pure
def test_a_mass_event_is_many_bad_at_once_and_its_messages_wait_a_little():
    assert alerts.mass_state("config", [1000] * 14, 14, 1050) == (True, True)                                         # fourteen of fourteen, 50 s: wait
    assert alerts.mass_state("config", [1000] * 14, 14, 1090) == (True, False)                                        # 90 s: it is not a blip, tell it (once)
    assert alerts.mass_state("config", [1000] * 3, 14, 1010) == (False, False)                                        # three of fourteen: three faults
    assert alerts.mass_state("config", [1000] * 5, 14, 1010) == (False, False)                                        # five of fourteen: under half
    assert alerts.mass_state("config", [1000] * 7, 14, 1010) == (True, True)
    assert alerts.mass_state("server", [1000] * 3, 6, 1010) == (True, True) and alerts.mass_state("server", [1000] * 3, 6, 1031) == (True, False)
    assert alerts.mass_state("tunnel", [1000] * 6, 20, 1010) == (True, True) and alerts.mass_state("tunnel", [1000] * 3, 20, 1010) == (False, False)
    assert alerts.mass_state("panel", [1000] * 9, 9, 1010) == (False, False)                                          # (panels and nodes are few: no mass rule)


def test_the_error_that_most_of_them_share_and_the_lines_of_the_message():
    items = [(n, f"config {n}", ERR) for n in NAMES]
    assert alerts.shared_reason(items) == (ERR, 14)
    assert alerts.shared_reason(items[:10] + [("x", "config x", "other")] * 4) == (ERR, 10)                          # (10 of 14 = 71 %: shared)
    assert alerts.shared_reason(items[:9] + [("x", "config x", "other")] * 5) == ("", 0)                              # (9 of 14 = 64 %: no common error)
    assert alerts.shared_reason([("a", "a", "e1"), ("b", "b", "e2"), ("c", "c", "e3"), ("d", "d", "e4")]) == ("", 0)
    lines = alerts.down_lines({"config": items})
    assert len(lines) == 1 and lines[0].startswith(f"• 14 configs fail together, all with the same error: {ERR}")
    assert "Turkey-1, Germany-1, Finland, Emirates, France, Poland, … (+8)" in lines[0] and "not 14 separate faults" in lines[0]
    few = alerts.down_lines({"config": items[:3], "server": [("ir1", "server ir1", "does not answer")]})
    assert few == [f"• config Turkey-1 — {ERR}", f"• config Germany-1 — {ERR}", f"• config Finland — {ERR}", "• server ir1 — does not answer"]            # under four of a kind: one line each, as before
    mixed = alerts.down_lines({"config": items[:10] + [("z1", "config z1", "e1"), ("z2", "config z2", "e2"), ("z3", "config z3", "e3"), ("z4", "config z4", "e4")]})
    assert "10 of them with the same error" in mixed[0]
    assert alerts.up_lines({"config": [(n, f"config {n}", "2 min") for n in NAMES]}, alerts.WORDS)[0].startswith("• 14 configs work again\n  Turkey-1, Germany-1")
    assert alerts.up_lines({"server": [("ir1", "server ir1", "40 s")]}, alerts.WORDS) == ["• server ir1 — was unreachable 40 s"]


# ------------------------------------------------------------------ the look
def test_fourteen_configs_that_fall_one_after_the_other_with_one_error_are_ONE_message_after_the_wait(telegram):
    sent = telegram
    add_configs("up", 0)
    alerts.run(now=900)
    set_configs("down", 1000, ERR, stagger=2)                                                                       # the probe tests them one after the other: they fall over 26 s
    alerts.run(now=1010)
    alerts.run(now=1040)
    alerts.run(now=1089)
    assert sent == []                                                                                              # a few seconds, a minute: nothing yet - it may be a blip
    alerts.run(now=1091)
    assert len(sent) == 1 and sent[0].startswith("🔴 DOWN (14)\n• 14 configs fail together, all with the same error:") and "gstatic.com:443" in sent[0] and sent[0].count("\n") == 3
    alerts.run(now=1100)
    alerts.run(now=2000)
    assert len(sent) == 1                                                                                          # once


def test_the_same_disturbance_that_is_over_within_the_wait_is_not_told_at_all(telegram):
    sent = telegram
    add_configs("up", 0)
    alerts.run(now=900)
    set_configs("down", 3000, ERR)
    alerts.run(now=3010)
    alerts.run(now=3050)
    set_configs("up", 3060)
    alerts.run(now=3061)
    alerts.run(now=3100)
    alerts.run(now=3200)
    assert sent == []                                                                                              # (no DOWN, so no UP: the owner is not asked to read a blip)


def test_the_way_back_is_one_message_too_and_waits_until_the_mass_is_over(telegram):
    sent = telegram
    add_configs("up", 0)
    alerts.run(now=900)
    set_configs("down", 1000, ERR)
    alerts.run(now=1100)
    assert len(sent) == 1
    set_configs("up", 2000, names=NAMES[:6], stagger=3)                                                            # six come back first: eight are still down, a mass still
    alerts.run(now=2030)
    alerts.run(now=2100)
    assert len(sent) == 1                                                                                          # (no UP for six of them while the event goes on)
    set_configs("up", 2200, names=NAMES[6:], stagger=3)
    alerts.run(now=2215)
    assert len(sent) == 1
    alerts.run(now=2260)
    assert len(sent) == 2 and sent[1].startswith("🟢 UP again (14)\n• 14 configs work again\n  Turkey-1, Germany-1")


def test_two_configs_that_fail_are_told_one_by_one_as_before_and_fast(telegram):
    sent = telegram
    add_configs("up", 0, names=NAMES[:8])
    alerts.run(now=900)
    set_configs("down", 1000, ERR, names=NAMES[:2])
    alerts.run(now=1005)
    assert sent == []                                                                                              # (10 s rule)
    alerts.run(now=1012)
    assert len(sent) == 1 and sent[0] == f"🔴 DOWN (2)\n• config Turkey-1 — {ERR}\n• config Germany-1 — {ERR}"


def test_three_servers_that_stop_answering_together_are_one_line(telegram):
    sent = telegram

    with session() as db:
        for i in range(4):
            db.add(Server(name=f"ir{i}", host=f"203.0.113.{i + 1}", role="iran", online=True))

    alerts.run(now=900)

    with session() as db:
        for s in db.scalars(select(Server).where(Server.name.in_(["ir0", "ir1", "ir2"]))):
            s.online, s.last_error = False, "no answer on the SSH port 22"

    alerts.run(now=1000)
    alerts.run(now=1015)
    assert sent == []                                                                                              # 15 s: it may be the panel's own network
    alerts.run(now=1031)
    alerts.run(now=1050)
    assert len(sent) == 1 and sent[0].startswith("🔴 DOWN (3)\n") and "3 servers fail together, all with the same error: no answer on the SSH port 22" in sent[0] and "panel's own network" in sent[0]


def test_the_events_page_tells_fourteen_configs_that_fell_in_one_read_as_one_event(monkeypatch):
    from nikatel.services import configs, probe

    clock = {"at": 1791575800}

    def health(status, error=None):
        def read():
            clock["at"] += 30                                                                                       # (every read is a NEW test of the probe)
            return {"ok": True, "configs": [{"name": n, "slug": n.lower(), "port": 11000 + i, "status": status, "ping": 100 if status == "up" else 0, "checked_at": clock["at"], "error": error, "speed": {}} for i, n in enumerate(NAMES)]}

        return read

    def texts():
        with session() as db:
            return [e.text for e in db.scalars(select(Event).where(Event.domain == "configs"))]

    monkeypatch.setattr(probe, "health", health("up"))
    configs.sync()
    monkeypatch.setattr(probe, "health", health("down", ERR))
    configs.sync()
    configs.sync()                                                                                                  # the second test that agrees: all fourteen go down in this one read
    fell = [t for t in texts() if "not working" in t]
    assert len(fell) == 1 and fell[0].startswith("14 configs are not working at the same moment, all with the same error:") and "not 14 faults" in fell[0] and "(+6)" in fell[0]
    monkeypatch.setattr(probe, "health", health("up"))
    configs.sync()
    configs.sync()
    back = [t for t in texts() if "work again" in t]
    assert len(back) == 1 and back[0].startswith("14 configs work again: Turkey-1, Germany-1")
    assert not any(t.startswith("Config ") for t in texts() if "appeared" not in t)                                  # (no per-config line besides the "appeared on the probe" ones)


def test_the_events_page_says_it_once_too(telegram):
    add_configs("up", 0)
    alerts.run(now=900)
    set_configs("down", 1000, ERR)
    alerts.run(now=1100)

    with session() as db:
        texts = [e.text for e in db.scalars(select(Event).where(Event.domain == "alerts"))]

    assert len(texts) == 1 and texts[0].startswith("🔴 DOWN (14)") and "sent" in texts[0]
    assert st.get("alerts")["down_seconds"] == 10
