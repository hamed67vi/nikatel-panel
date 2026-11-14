"""Telegram delivery. One function to send text, one to send a file. Never raises into a job:
failures are returned as a sentence and logged."""
from __future__ import annotations

import json
import os
import re
import threading
import time

import httpx

from ..config import get_settings
from ..log import get_logger
from . import settings as st

log = get_logger("telegram")
_TOKEN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
_CHAT = re.compile(r"^(-?\d{5,20}|@[A-Za-z0-9_]{4,})$")

# What Telegram answered the last time (1.69.252). The owner, 2026-11-09: "no backup is sent to the bot" - Telegram had refused the panel's bot for ten days (Unauthorized: every alert, report and backup), while the Settings box said
# "● sending": it only looked at whether a token was SAVED. The last definite answer is kept now - delivered, or refused for a reason that is about the bot or the chat itself - with since when and how many times in a row, in a small
# file beside the database (written only when the answer CHANGES), so the pages can say it in red. A network error and a refusal that is about one message (a topic that is gone, too many requests) do not change it.
_FATAL = ("unauthorized", "forbidden", "chat not found", "not a member", "kicked", "deactivated", "have no rights", "not enough rights to send")
_STATE: dict = {}
_STATE_LOCK = threading.Lock()


def _state_path():
    return get_settings().data_dir / "telegram.state.json"


def _num(v) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else 0


def _loaded() -> dict:          # (call with the lock held)
    if not _STATE:
        try:
            d = json.loads(_state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}

        if not isinstance(d, dict):
            d = {}

        ok = d.get("ok")
        _STATE.update(ok=ok if isinstance(ok, bool) else None, why=str(d.get("why") or "")[:200], at=_num(d.get("at")), since=_num(d.get("since")), fails=_num(d.get("fails")))

    return _STATE


def state() -> dict:
    """The last definite answer of Telegram: {"ok": True (delivered) / False (refused) / None (nothing was sent since the token was saved), "why", "at" (the last try), "since" (when this answer began), "fails" (refusals in a row)}."""
    with _STATE_LOCK:
        return dict(_loaded())


def forget() -> None:
    """The token or the chat was changed: what the old ones were answered says nothing about the new ones."""
    with _STATE_LOCK:
        _STATE.clear()
        _STATE.update(ok=None, why="", at=0, since=0, fails=0)

        try:
            _state_path().unlink()
        except OSError:
            pass


def _note(ok: bool, why: str = "") -> None:
    """Keep a definite answer. A refusal is kept only when it is about the bot or the chat (`_FATAL`): one message that could not be delivered does not make the whole connection red."""
    why = str(why or "")[:200]

    if not ok and not any(w in why.lower() for w in _FATAL):
        return

    now = int(time.time())

    with _STATE_LOCK:
        cur = _loaded()
        changed = cur.get("ok") is not ok or (not ok and cur.get("why") != why)

        if changed:
            cur.update(ok=ok, why="" if ok else why, since=now, fails=0)

        cur["at"] = now
        cur["fails"] = 0 if ok else int(cur.get("fails") or 0) + 1

        if changed:          # (the file is written when the answer changes, not at every message)
            try:
                p = _state_path()
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(cur), encoding="utf-8")
                os.chmod(tmp, 0o600)
                os.replace(tmp, p)
            except OSError as e:
                log.warning("the Telegram state could not be kept: %s", e)


def is_fatal(why: str) -> bool:
    """Pure: does this answer say that Telegram refuses the BOT or the CHAT itself (a wrong token, a blocked bot, a chat that is gone) - not one message (a timeout, "too many requests")? The alerts that meet it are kept."""
    return any(w in str(why or "").lower() for w in _FATAL)


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


def _thread() -> int:
    """The topic of the group that alerts, reports and the test message go to (1.69.269; 0 = the main page of the group). Backups have topics of their own (backup.py)."""
    return _num(st.get("telegram").get("thread_id"))


def ready() -> bool:
    """Is Telegram switched on with a usable token and chat (so a file can be sent)?"""
    return _conf(True) is not None


def send_text(text: str, require_enabled: bool = True) -> tuple[bool, str]:
    conf = _conf(require_enabled)

    if conf is None:
        return False, "Telegram is off or not configured"

    token, chat = conf

    body = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}
    topic = _thread()

    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={**body, **({"message_thread_id": topic} if topic else {})})
        d = r.json()

        if topic and not d.get("ok") and "thread not found" in str(d.get("description") or "").lower():          # (the topic was deleted in Telegram: the message is not lost - it goes to the main page of the group)
            log.warning("the Telegram topic %s is gone: sending to the main page", topic)
            r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json=body)
            d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("send failed: %s", e)
        return False, f"Telegram did not answer: {str(e)[:80]}"

    if not d.get("ok"):
        why = str(d.get("description") or r.status_code)
        log.warning("send rejected: %s", why)
        _note(False, why)
        return False, f"Telegram rejected the message: {why}"

    _note(True)
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
        _note(False, str(d.get("description") or r.status_code))
        return False, f"Telegram rejected the file: {d.get('description') or r.status_code}"

    _note(True)
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

        if "unauthorized" in why.lower():          # (the token itself is refused: that is the state of the whole connection, not of this topic)
            _note(False, why)

        hint = " - switch Topics on in the group settings and make the bot an admin with the right to manage topics" if "not a forum" in why.lower() or "rights" in why.lower() or "admin" in why.lower() else ""
        return 0, f"Telegram refused: {why}{hint}"

    return int((d.get("result") or {}).get("message_thread_id") or 0), ""