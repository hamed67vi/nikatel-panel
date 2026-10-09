"""Telegram delivery. One function to send text, one to send a file. Never raises into a job:
failures are returned as a sentence and logged."""
from __future__ import annotations

import re

import httpx

from ..log import get_logger
from . import settings as st

log = get_logger("telegram")
_TOKEN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
_CHAT = re.compile(r"^(-?\d{5,20}|@[A-Za-z0-9_]{4,})$")


def valid_token(t: str) -> bool:
    return bool(_TOKEN.match(t or ""))


def valid_chat(c: str) -> bool:
    return bool(_CHAT.match(c or ""))


def _conf(require_enabled: bool) -> tuple[str, str] | None:
    c = st.get("telegram")

    if require_enabled and not c.get("enabled"):
        return None

    if not valid_token(c.get("token", "")) or not valid_chat(c.get("chat_id", "")):
        return None

    return c["token"], c["chat_id"]


def send_text(text: str, require_enabled: bool = True) -> tuple[bool, str]:
    conf = _conf(require_enabled)

    if conf is None:
        return False, "Telegram is off or not configured"

    token, chat = conf

    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                       json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True})
        d = r.json()
    except Exception as e:
        log.warning("send failed: %s", e)
        return False, f"Telegram did not answer: {str(e)[:80]}"

    if not d.get("ok"):
        why = str(d.get("description") or r.status_code)
        log.warning("send rejected: %s", why)
        return False, f"Telegram rejected the message: {why}"

    return True, "sent"


def send_file(name: str, data: bytes, caption: str = "") -> tuple[bool, str]:
    conf = _conf(True)

    if conf is None:
        return False, "Telegram is off or not configured"

    token, chat = conf

    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendDocument", timeout=120,
                       data={"chat_id": chat, "caption": caption[:900]}, files={"document": (name, data)})
        d = r.json()
    except Exception as e:
        log.warning("file send failed: %s", e)
        return False, f"Telegram did not answer: {str(e)[:80]}"

    if not d.get("ok"):
        return False, f"Telegram rejected the file: {d.get('description') or r.status_code}"

    return True, "sent"
