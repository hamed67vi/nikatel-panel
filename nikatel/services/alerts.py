"""Alerts with flapping control.

The checkers write the *state* (up / down …) on every run. This job turns states into
*notifications* with hysteresis: a thing must be bad for N checks in a row before one DOWN
message goes out; an UP message goes out only after it has been good for `up_stable_seconds`;
while it stays down a reminder goes out every `remind_hours`. Muted refs and maintenance windows
suppress messages but the state tracking continues, so nothing is missed when the mute ends.

`decide()` is pure and tested; `run()` wires it to the database and Telegram.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import AlertState, Config, Maintenance, Server, Tunnel
from . import settings as st
from .events import emit
from .telegram import send_text

log = get_logger("alerts")


@dataclass
class Rules:
    fails: int
    up_stable_seconds: int
    remind_seconds: int


@dataclass
class Track:
    """What we remember about one thing between runs."""
    fails: int = 0
    alerted: bool = False
    bad_since: int = 0
    good_since: int = 0
    last_notice: int = 0


def decide(track: Track, bad: bool, now: int, rules: Rules) -> tuple[Track, str | None]:
    """Returns the updated track and one of None | "down" | "up" | "remind"."""
    t = Track(**track.__dict__)

    if bad:
        t.good_since = 0

        if t.fails == 0:
            t.bad_since = now

        t.fails += 1

        if not t.alerted and t.fails >= rules.fails:
            t.alerted = True
            t.last_notice = now
            return t, "down"

        if t.alerted and now - t.last_notice >= rules.remind_seconds:
            t.last_notice = now
            return t, "remind"

        return t, None

    # good
    t.fails = 0

    if t.good_since == 0:
        t.good_since = now

    if t.alerted and now - t.good_since >= rules.up_stable_seconds:
        t.alerted = False
        t.last_notice = now
        return t, "up"

    return t, None


def _dur(seconds: int) -> str:
    seconds = max(0, int(seconds))

    if seconds < 3600:
        return f"{seconds // 60} min"

    if seconds < 86400:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"

    return f"{seconds // 86400} d {seconds % 86400 // 3600} h"


def in_maintenance(now: int, windows: list[tuple[int, int]]) -> bool:
    return any(a <= now <= b for a, b in windows)


def run() -> None:
    conf = st.get("alerts")
    now = int(time.time())
    muted = set(conf.get("muted") or [])
    rules_t = Rules(int(conf["tunnel_fails"]), int(conf["up_stable_seconds"]), int(conf["remind_hours"]) * 3600)
    rules_s = Rules(int(conf["server_fails"]), int(conf["up_stable_seconds"]), int(conf["remind_hours"]) * 3600)
    down_msgs: list[str] = []
    up_msgs: list[str] = []
    remind_msgs: list[str] = []

    with session() as db:
        windows = [(m.start, m.end) for m in db.scalars(select(Maintenance))]
        quiet = in_maintenance(now, windows)
        tracks = {(a.kind, a.ref): a for a in db.scalars(select(AlertState))}

        def track_for(kind: str, ref: str) -> AlertState:
            a = tracks.get((kind, ref))

            if a is None:
                a = AlertState(kind=kind, ref=ref)
                db.add(a)
                tracks[(kind, ref)] = a

            return a

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            if t.state in ("unknown", "starting"):
                continue   # not a verdict about the tunnel

            a = track_for("tunnel", t.name)
            new, verdict = decide(Track(a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice), t.state == "down", now, rules_t)
            a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice = new.fails, new.alerted, new.bad_since, new.good_since, new.last_notice
            label = f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"

            if verdict and t.name not in muted and not quiet:
                if verdict == "down":
                    down_msgs.append(f"• {label} — {t.last_error or 'no data passes'}")
                elif verdict == "remind":
                    remind_msgs.append(f"• {label} — still down for {_dur(now - a.bad_since)}")
                else:
                    up_msgs.append(f"• {label} — was down {_dur(a.good_since - a.bad_since)}")

        for s in db.scalars(select(Server).where(Server.enabled.is_(True))):
            if s.online is None:
                continue

            a = track_for("server", s.name)
            new, verdict = decide(Track(a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice), s.online is False, now, rules_s)
            a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice = new.fails, new.alerted, new.bad_since, new.good_since, new.last_notice

            if verdict and s.name not in muted and not quiet:
                if verdict == "down":
                    down_msgs.append(f"• server {s.name} — {s.last_error or 'does not answer'}")
                elif verdict == "remind":
                    remind_msgs.append(f"• server {s.name} — still unreachable for {_dur(now - a.bad_since)}")
                else:
                    up_msgs.append(f"• server {s.name} — was unreachable {_dur(a.good_since - a.bad_since)}")

        for c in db.scalars(select(Config).where(Config.enabled.is_(True))):
            if c.status == "unknown":
                continue

            a = track_for("config", c.name)
            new, verdict = decide(Track(a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice), c.status == "down", now, rules_t)
            a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice = new.fails, new.alerted, new.bad_since, new.good_since, new.last_notice

            if verdict and c.name not in muted and not quiet:
                if verdict == "down":
                    down_msgs.append(f"• config {c.name} — {c.last_error or 'no answer through it'}")
                elif verdict == "remind":
                    remind_msgs.append(f"• config {c.name} — still down for {_dur(now - a.bad_since)}")
                else:
                    up_msgs.append(f"• config {c.name} — was down {_dur(a.good_since - a.bad_since)}")

        if down_msgs:
            _deliver(db, "bad", f"🔴 DOWN ({len(down_msgs)})\n" + "\n".join(down_msgs))

        if remind_msgs:
            _deliver(db, "warn", f"⚠ STILL DOWN ({len(remind_msgs)})\n" + "\n".join(remind_msgs))

        if up_msgs:
            _deliver(db, "ok", f"🟢 UP again ({len(up_msgs)})\n" + "\n".join(up_msgs))


def _deliver(db, level: str, text: str) -> None:
    ok, why = send_text(text)
    emit(db, level, "alerts", text.splitlines()[0] + (" · sent" if ok else f" · not sent: {why}"), "")
    log.info("alert %s: %s", "sent" if ok else "NOT sent (" + why + ")", text.splitlines()[0])
