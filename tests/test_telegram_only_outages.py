"""Only outages go to Telegram (the owner, 2026-10-23, a picture of the chat: sixteen "DNS ...: edit by hand (a -> b)" messages after he moved the records of a domain). What was done with DNS records - by hand, put back,
moved after an outage - and the switch of records to the outbound pool and back are in the DNS log and the Events page, not in Telegram."""
import re
from pathlib import Path

from nikatel.services import failover, outbound, telegram

SRC = Path(__file__).resolve().parents[1] / "nikatel" / "services"


def test_a_dns_change_is_logged_and_not_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_text", lambda text, require_enabled=True: sent.append(text) or (True, "sent"))
    failover._quiet("🌐 DNS app.example.com: edit by hand (203.0.113.1 → 203.0.113.2).")
    outbound._quiet("🌉 Tunnel(s) t1 down for 10 min: 2 record(s) now go through the outbound pool.")
    assert sent == []


def test_no_dns_or_pool_switch_message_goes_through_the_telegram_senders_any_more():
    f = (SRC / "failover.py").read_text(encoding="utf-8")
    o = (SRC / "outbound.py").read_text(encoding="utf-8")
    assert not re.search(r'_notice\(f"(🌐|↩) DNS', f) and f.count('_quiet(f"🌐 DNS') == 3 and f.count('_quiet(f"↩ DNS') == 2
    assert '_tell(f"↩ Tunnel(s)' not in o and '_tell(f"🌉 Tunnel(s)' not in o and o.count('_quiet(f"') == 2


def test_the_outages_still_go_out():
    f = (SRC / "failover.py").read_text(encoding="utf-8")
    o = (SRC / "outbound.py").read_text(encoding="utf-8")
    assert '_notice(f"⚠ Iran server {name} has been off for' in f and '_notice(f"🔶 Iran server {name} has been off for' in f          # (the Iran server that is off and the move the Doctor made)
    assert '_tell(("✅ " if active else "🔴 ") + f"Outbound relay on {name} {say}")' in o and 'telegram.send_text({"ok": "✅ ", "degraded": "⚠ ", "down": "🔴 "}[new]' in o
