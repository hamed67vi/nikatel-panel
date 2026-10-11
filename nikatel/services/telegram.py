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
    except (httpx.HTTPError, ValueError) as e:
        log.warning("send failed: %s", e)
        return False, f"Telegram did not answer: {str(e)[:80]}"

    if not d.get("ok"):
        why = str(d.get("description") or r.status_code)
        log.warning("send rejected: %s", why)
        return False, f"Telegram rejected the message: {why}"

    return True, "sent"


def send_file(name: str, data: bytes, caption: str = "", thread_id: int = 0) -> tuple[bool, str]:
    conf = _conf(True)

    if conf is None:
        return False, "Telegram is off or not configured"

    token, chat = conf

    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendDocument", timeout=120,
                       data={"chat_id": chat, "caption": caption[:900], **({"message_thread_id": int(thread_id)} if thread_id else {})}, files={"document": (name, data)})
        d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("file send failed: %s", e)
        return False, f"Telegram did not answer: {str(e)[:80]}"

    if not d.get("ok"):
        return False, f"Telegram rejected the file: {d.get('description') or r.status_code}"

    return True, "sent"

def create_topic(name: str) -> tuple[int, str]:
    """A new topic (a separate page) in the group: (thread id, "") or (0, why). The group must have Topics switched on and the bot must be an admin that may manage topics."""
    conf = _conf(False)

    if conf is None:
        return 0, "Telegram is not configured (token and chat id)"

    token, chat = conf

    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/createForumTopic", timeout=20, json={"chat_id": chat, "name": name[:100]})
        d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return 0, f"Telegram did not answer: {str(e)[:80]}"

    if not d.get("ok"):
        why = str(d.get("description") or r.status_code)
        hint = " - switch Topics on in the group settings and make the bot an admin with the right to manage topics" if "not a forum" in why.lower() or "rights" in why.lower() or "admin" in why.lower() else ""
        return 0, f"Telegram refused: {why}{hint}"

    return int((d.get("result") or {}).get("message_thread_id") or 0), ""