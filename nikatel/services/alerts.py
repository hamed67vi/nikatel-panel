"""Alerts with flapping control.

The checkers write the *state* (up / down …) on every run. This job turns states into
*notifications* with hysteresis: a thing must be bad for N checks in a row **and** for at least
`min_down_minutes` before one DOWN message goes out; an UP message goes out only after it has been
good for `up_stable_seconds`; reminders while it stays down are optional (`remind_hours`, 0 = never);
and a thing that keeps going down and up (`flap_max` outages inside an hour) gets one FLAPPING notice
and is then kept quiet for `flap_quiet_hours`. Muted refs and maintenance windows suppress messages
but the state tracking continues, so nothing is missed when the mute ends.

`decide()` is pure and tested; `run()` wires it to the database and Telegram.
"""
from __future__ import annotations

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


@dataclass
class Rules:
    fails: int
    up_stable_seconds: int
    remind_seconds: int             # 0 = never remind
    min_bad_seconds: int = 0        # bad for at least this long before DOWN (blips of a minute or two say nothing)
    flap_max: int = 0               # this many DOWNs inside flap_window -> one FLAPPING notice, then quiet; 0 = off
    flap_window: int = 3600
    flap_quiet: int = 6 * 3600


@dataclass
class Track:
    """What we remember about one thing between runs."""
    fails: int = 0
    alerted: bool = False
    bad_since: int = 0
    good_since: int = 0
    last_notice: int = 0
    flips: int = 0                  # DOWNs counted inside the current flapping window
    flap_since: int = 0             # when that window started
    quiet_until: int = 0            # while now < quiet_until nothing is said about this thing


def decide(track: Track, bad: bool, now: int, rules: Rules) -> tuple[Track, str | None]:
    """Returns the updated track and one of None | "down" | "up" | "remind" | "flapping"."""
    t = Track(**track.__dict__)
    quiet = now < t.quiet_until

    if bad:
        t.good_since = 0

        if t.fails == 0:
            t.bad_since = now

        t.fails += 1

        if not t.alerted and t.fails >= rules.fails and now - t.bad_since >= rules.min_bad_seconds:
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
        t.good_since = now

    if t.alerted and now - t.good_since >= rules.up_stable_seconds:
        t.alerted = False
        t.last_notice = now
        return t, None if quiet else "up"

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


def rules_from(conf: dict, fails_key: str) -> Rules:
    """The Rules of one kind of thing from the alerts settings."""
    return Rules(int(conf.get(fails_key, 3)), int(conf["up_stable_seconds"]), int(conf.get("remind_hours", 0)) * 3600,
                 int(conf.get("min_down_minutes", 0)) * 60, int(conf.get("flap_max", 0)), 3600, int(conf.get("flap_quiet_hours", 6)) * 3600)


def run() -> None:
    conf = st.get("alerts")
    now = int(time.time())
    muted = set(conf.get("muted") or [])
    rules_t = rules_from(conf, "tunnel_fails")
    rules_s = rules_from(conf, "server_fails")
    rules_c = rules_from(conf, "config_fails")
    down_msgs: list[str] = []
    up_msgs: list[str] = []
    remind_msgs: list[str] = []
    flap_msgs: list[str] = []
    quiet_for = _dur(rules_t.flap_quiet)

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

        def judge(kind: str, ref: str, bad: bool, rules: Rules) -> tuple[AlertState, str | None]:
            a = track_for(kind, ref)
            new, verdict = decide(Track(a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice, a.flips or 0, a.flap_since or 0, a.quiet_until or 0), bad, now, rules)
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

            a, verdict = judge("tunnel", t.name, t.state == "down", rules_t)

            if t.name not in muted and not quiet:
                say(verdict, a, f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})", t.last_error or "no data passes")

        for s in db.scalars(select(Server).where(Server.enabled.is_(True))):
            if s.online is None:
                continue

            a, verdict = judge("server", s.name, s.online is False, rules_s)

            if s.name not in muted and not quiet:
                say(verdict, a, f"server {s.name}", s.last_error or "does not answer", "unreachable")

        for c in db.scalars(select(Config).where(Config.enabled.is_(True))):
            if c.status == "unknown":
                continue

            a, verdict = judge("config", c.name, c.status == "down", rules_c)

            if c.name not in muted and not quiet:
                say(verdict, a, f"config {c.name}", c.last_error or "no answer through it")

        for p in db.scalars(select(Panel).where(Panel.enabled.is_(True))):
            if p.status == "unknown":
                continue

            a, verdict = judge("panel", p.name, p.status == "down", rules_s)

            if p.name not in muted and not quiet:
                say(verdict, a, f"panel {p.name}", p.last_error or "does not answer")

            for n in p.nodes or []:
                a, verdict = judge("node", f"{p.name}/{n.get('name', '')}", int(n.get("fails", 0)) >= 2, rules_s)

                if p.name not in muted and not quiet:
                    why = (n.get("status") or "not connected") + (f" ({n['message']})" if n.get("message") else "")
                    say(verdict, a, f"node {n.get('name', '')} of {p.name}", why, "disconnected")

        if down_msgs:
            _deliver(db, "bad", f"🔴 DOWN ({len(down_msgs)})\n" + "\n".join(down_msgs))

        if flap_msgs:
            _deliver(db, "warn", f"🟠 FLAPPING ({len(flap_msgs)})\n" + "\n".join(flap_msgs))

        if remind_msgs:
            _deliver(db, "warn", f"⚠ STILL DOWN ({len(remind_msgs)})\n" + "\n".join(remind_msgs))

        if up_msgs:
            _deliver(db, "ok", f"🟢 UP again ({len(up_msgs)})\n" + "\n".join(up_msgs))


def _deliver(db, level: str, text: str) -> None:
    ok, why = send_text(text)
    emit(db, level, "alerts", text.splitlines()[0] + (" · sent" if ok else f" · not sent: {why}"), "")
    log.info("alert %s: %s", "sent" if ok else "NOT sent (" + why + ")", text.splitlines()[0])
