"""Alerts with flapping control.

The checkers write the *state* (up / down …) and they confirm it themselves (a tunnel is `down` after its own confirming checks, a server after two quick
misses of the pulse, a config after two different tests of the probe, a panel after two reads). This job, which looks every 5 seconds, turns states into
*notifications*: a thing that stays bad for `down_seconds` gets one DOWN message, a thing that stays good for `up_seconds` gets one UP message (the clock
starts at the moment the checker changed the state, not at our next look); reminders while it stays down are optional (`remind_hours`, 0 = never); and a
thing that keeps going down and up (`flap_max` outages inside an hour) gets one FLAPPING notice and is then kept quiet for `flap_quiet_hours`. Muted refs
and maintenance windows suppress messages but the state tracking continues, so nothing is missed when the mute ends.

A message that Telegram does not take (a timeout, "too many requests") is kept and sent again every few seconds, in order, for half an hour: a DOWN or an
UP is never lost because one request failed.

`decide()` is pure and tested; `run()` wires it to the database and Telegram.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import AlertState, Config, Maintenance, Panel, Server, Tunnel
from . import settings as st
from .events import emit
from .telegram import send_text

log = get_logger("alerts")

DOWN_SECONDS = 10       # defaults (the checkers have confirmed the fault already: this is only the time a blip must not outlast)
UP_SECONDS = 15


@dataclass
class Rules:
    down_seconds: int = DOWN_SECONDS      # bad for at least this long before DOWN
    up_seconds: int = UP_SECONDS          # good for at least this long before UP
    remind_seconds: int = 0               # 0 = never remind
    flap_max: int = 0                     # this many DOWNs inside flap_window -> one FLAPPING notice, then quiet; 0 = off
    flap_window: int = 3600
    flap_quiet: int = 6 * 3600


@dataclass
class Track:
    """What we remember about one thing between runs."""
    fails: int = 0                  # 0 = good at the last look, 1 = bad at the last look (kept as a number: the column is older than the time rules)
    alerted: bool = False
    bad_since: int = 0
    good_since: int = 0
    last_notice: int = 0
    flips: int = 0                  # DOWNs counted inside the current flapping window
    flap_since: int = 0             # when that window started
    quiet_until: int = 0            # while now < quiet_until nothing is said about this thing


def decide(track: Track, bad: bool, now: int, rules: Rules, since: int = 0) -> tuple[Track, str | None]:
    """Returns the updated track and one of None | "down" | "up" | "remind" | "flapping".
    `since`: when the checker itself changed the state (0 = unknown): the clock of a DOWN / UP starts there, so the message does not wait for our next look."""
    t = Track(**track.__dict__)
    quiet = now < t.quiet_until
    began = since if 0 < since <= now else now

    if bad:
        t.good_since = 0

        if t.fails == 0:
            if not t.alerted:                # (a thing already told as down that is good for a moment and bad again is still the same outage: its start stays)
                t.bad_since = began

            t.fails = 1

        if not t.alerted and now - t.bad_since >= rules.down_seconds:
            t.alerted = True
            t.last_notice = now

            if rules.flap_max > 0:
                if now - t.flap_since > rules.flap_window:
                    t.flap_since, t.flips = now, 0

                t.flips += 1

                if quiet:
                    return t, None

                if t.flips >= rules.flap_max:
                    t.quiet_until = now + rules.flap_quiet
                    return t, "flapping"

            return t, None if quiet else "down"

        if t.alerted and rules.remind_seconds > 0 and now - t.last_notice >= rules.remind_seconds and not quiet:
            t.last_notice = now
            return t, "remind"

        return t, None

    # good
    t.fails = 0

    if t.good_since == 0:
        t.good_since = began

    if t.alerted and now - t.good_since >= rules.up_seconds:
        t.alerted = False
        t.last_notice = now
        return t, None if quiet else "up"

    return t, None


def _dur(seconds: int) -> str:
    seconds = max(0, int(seconds))

    if seconds < 60:
        return f"{seconds} s"

    if seconds < 600:
        return f"{seconds // 60} min" + (f" {seconds % 60} s" if seconds % 60 else "")

    if seconds < 3600:
        return f"{seconds // 60} min"

    if seconds < 86400:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"

    return f"{seconds // 86400} d {seconds % 86400 // 3600} h"


def in_maintenance(now: int, windows: list[tuple[int, int]]) -> bool:
    return any(a <= now <= b for a, b in windows)


def rules_from(conf: dict) -> Rules:
    """The Rules from the alerts settings."""
    return Rules(int(conf.get("down_seconds", DOWN_SECONDS)), int(conf.get("up_seconds", UP_SECONDS)), int(conf.get("remind_hours", 0)) * 3600,
                 int(conf.get("flap_max", 0)), 3600, int(conf.get("flap_quiet_hours", 6)) * 3600)


# ------------------------------------------------------------------ delivery
OUTBOX_MAX_AGE = 1800          # a message older than this is dropped (a DOWN of half an hour ago is no news; the next DOWN / UP of the thing says where it stands)
OUTBOX_MAX = 40
RETRY_EVERY = 10
_OUTBOX: list[dict] = []       # messages Telegram did not take yet, oldest first
_Q_LOCK = threading.Lock()     # the list (held for an instant)
_SEND_LOCK = threading.Lock()  # one sender at a time (held while Telegram is asked: up to 20 s)
_NEXT_TRY = {"at": 0.0}


def retryable(why: str) -> bool:
    """A failed send worth another try: the request did not get through, or Telegram asked to slow down. Not configured / a wrong chat id will not get better."""
    return why.startswith("Telegram did not answer") or any(w in why for w in ("Too Many Requests", "retry after", "Bad Gateway", "Internal Server Error", "Gateway Time-out"))


def _deliver(level: str, text: str) -> None:
    """Queue the message and send what is queued (in order)."""
    with _Q_LOCK:
        _OUTBOX.append({"level": level, "text": text, "at": time.time(), "tries": 0})
        del _OUTBOX[:-OUTBOX_MAX]

    flush(force=True)


def flush(force: bool = False) -> None:
    """Send the queued messages, oldest first; stops at the first one Telegram does not take (it stays, the next look tries again)."""
    if not _OUTBOX or (not force and time.time() < _NEXT_TRY["at"]):
        return

    if not _SEND_LOCK.acquire(blocking=False):
        return                                   # another look is sending right now

    try:
        while True:
            with _Q_LOCK:
                m = _OUTBOX[0] if _OUTBOX else None

            if m is None:
                return

            if time.time() - m["at"] > OUTBOX_MAX_AGE:
                _forget(m)
                _say("warn", f"{m['text'].splitlines()[0]} · dropped: Telegram did not take it for {_dur(OUTBOX_MAX_AGE)}")
                continue

            ok, why = send_text(m["text"])
            m["tries"] += 1
            head = m["text"].splitlines()[0]

            if ok:
                _forget(m)
                _say(m["level"], head + " · sent" + (f" (after {m['tries']} tries, {_dur(time.time() - m['at'])} late)" if m["tries"] > 1 else ""))
                log.info("alert sent: %s", head)
                continue

            if retryable(why):
                _NEXT_TRY["at"] = time.time() + RETRY_EVERY
                log.warning("alert not sent yet (%s); tried %s time(s), will try again", why, m["tries"])
                return

            _forget(m)
            _say(m["level"], head + f" · not sent: {why}")
            log.info("alert NOT sent (%s): %s", why, head)
    finally:
        _SEND_LOCK.release()


def _forget(m: dict) -> None:
    with _Q_LOCK:
        if m in _OUTBOX:
            _OUTBOX.remove(m)


def _say(level: str, text: str) -> None:
    with session() as db:
        emit(db, level, "alerts", text, "")


# ------------------------------------------------------------------ the look
def run(now: int | None = None) -> None:
    flush()           # whatever Telegram did not take at the last look goes first

    conf = st.get("alerts")
    now = int(time.time() if now is None else now)
    muted = set(conf.get("muted") or [])
    rules = rules_from(conf)
    down_msgs: list[str] = []
    up_msgs: list[str] = []
    remind_msgs: list[str] = []
    flap_msgs: list[str] = []
    quiet_for = _dur(rules.flap_quiet)

    with session() as db:
        windows = [(m.start, m.end) for m in db.scalars(select(Maintenance))]
        quiet = in_maintenance(now, windows)
        tracks = {(a.kind, a.ref): a for a in db.scalars(select(AlertState))}

        def track_for(kind: str, ref: str) -> AlertState:
            a = tracks.get((kind, ref))

            if a is None:
                # column defaults only apply at flush: set them now, decide() reads them right away
                a = AlertState(kind=kind, ref=ref, fails=0, alerted=False, bad_since=0, good_since=0, last_notice=0, flips=0, flap_since=0, quiet_until=0)
                db.add(a)
                tracks[(kind, ref)] = a

            return a

        def judge(kind: str, ref: str, bad: bool, since: int = 0) -> tuple[AlertState, str | None]:
            a = track_for(kind, ref)
            new, verdict = decide(Track(a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice, a.flips or 0, a.flap_since or 0, a.quiet_until or 0), bad, now, rules, since)
            a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice = new.fails, new.alerted, new.bad_since, new.good_since, new.last_notice
            a.flips, a.flap_since, a.quiet_until = new.flips, new.flap_since, new.quiet_until
            return a, verdict

        def say(verdict: str | None, a: AlertState, label: str, why: str, down_word: str = "down") -> None:
            if not verdict:
                return

            if verdict == "down":
                down_msgs.append(f"• {label} — {why}")
            elif verdict == "remind":
                remind_msgs.append(f"• {label} — still {down_word} for {_dur(now - a.bad_since)}")
            elif verdict == "flapping":
                flap_msgs.append(f"• {label} — {a.flips} outages in the last hour; quiet about it for {quiet_for}")
            else:
                up_msgs.append(f"• {label} — was {down_word} {_dur(a.good_since - a.bad_since)}")

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            if t.state in ("unknown", "starting"):
                continue   # not a verdict about the tunnel

            a, verdict = judge("tunnel", t.name, t.state == "down", int(t.state_since or 0))

            if t.name not in muted and not quiet:
                say(verdict, a, f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})", t.last_error or "no data passes")

        for s in db.scalars(select(Server).where(Server.enabled.is_(True))):
            if s.online is None:
                continue

            a, verdict = judge("server", s.name, s.online is False)

            if s.name not in muted and not quiet:
                say(verdict, a, f"server {s.name}", s.last_error or "does not answer", "unreachable")

        for c in db.scalars(select(Config).where(Config.enabled.is_(True))):
            if c.status == "unknown":
                continue

            a, verdict = judge("config", c.name, c.status == "down", int(c.status_since or 0))

            if c.name not in muted and not quiet:
                say(verdict, a, f"config {c.name}", c.last_error or "no answer through it")

        for p in db.scalars(select(Panel).where(Panel.enabled.is_(True))):
            if p.status == "unknown":
                continue

            a, verdict = judge("panel", p.name, p.status == "down", int(p.status_since or 0))

            if p.name not in muted and not quiet:
                say(verdict, a, f"panel {p.name}", p.last_error or "does not answer")

            for n in p.nodes or []:
                a, verdict = judge("node", f"{p.name}/{n.get('name', '')}", int(n.get("fails", 0)) >= 2)

                if p.name not in muted and not quiet:
                    why = (n.get("status") or "not connected") + (f" ({n['message']})" if n.get("message") else "")
                    say(verdict, a, f"node {n.get('name', '')} of {p.name}", why, "disconnected")

    # the states are saved; the messages go out after the database is let go (a slow Telegram must not hold a connection)
    if down_msgs:
        _deliver("bad", f"🔴 DOWN ({len(down_msgs)})\n" + "\n".join(down_msgs))

    if flap_msgs:
        _deliver("warn", f"🟠 FLAPPING ({len(flap_msgs)})\n" + "\n".join(flap_msgs))

    if remind_msgs:
        _deliver("warn", f"⚠ STILL DOWN ({len(remind_msgs)})\n" + "\n".join(remind_msgs))

    if up_msgs:
        _deliver("ok", f"🟢 UP again ({len(up_msgs)})\n" + "\n".join(up_msgs))
