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


def decide(track: Track, bad: bool, now: int, rules: Rules, since: int = 0, hold: bool = False) -> tuple[Track, str | None]:
    """Returns the updated track and one of None | "down" | "up" | "remind" | "flapping".
    `since`: when the checker itself changed the state (0 = unknown): the clock of a DOWN / UP starts there, so the message does not wait for our next look.
    `hold`: a mass event is going on (see MASS): a DOWN or an UP that is due is NOT given yet and nothing is changed - the next look asks again, and when the hold ends they all come in the same look (one message)."""
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
            if hold:
                return t, None

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
        if hold:
            return t, None

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


# ------------------------------------------------------------------ one cause, one message
# (the owner, 2026-10-17: fourteen configs at once - "DOWN (14)" - every one with the same error toward the same address: "why so many errors for one small disturbance?") Things that fail TOGETHER and
# with the same error have one cause, which is not fourteen faults: the test the probe makes (its own path, its test address), the panel's own network, a provider event. So:
#   * while many things of a kind are bad at the same time (MASS: at least this many, at least this share of all of them) the DOWN and UP messages of that kind WAIT - for a few seconds for tunnels and servers, for
#     a minute and a half for configs (the probe tests them one after the other, so they fall one after the other); a disturbance that is over by then is not told at all;
#   * what is left is told as ONE line: how many, the error they share, a few names and the likely cause.
MASS = {"config": (4, 0.5, 90), "server": (3, 0.4, 30), "tunnel": (4, 0.3, 20)}           # kind -> (at least this many bad, at least this share of all, wait this many seconds for the rest)
GROUP_MIN = 4                                                                             # this many of a kind with no MASS rule (panels, nodes) in ONE message are told as one line
NAMES_SHOWN = 6
HINT = {"config": "The same test fails for all of them at the same moment: the probe's own path to its test address is the likely cause, not {n} separate faults.",
        "server": "Several servers stopped answering together: the panel's own network or a provider event is the likely cause.",
        "tunnel": "Several tunnels fell together: a route or provider event is the likely cause; the Doctor is already working on them."}


def mass_state(kind: str, bad_since: list[int], total: int, now: int) -> tuple[bool, bool]:
    """Pure: (is a mass event going on, are the messages of this kind held back). `bad_since`: when each thing that is bad now became bad; `total`: how many things of this kind there are."""
    if kind not in MASS:
        return False, False

    lo, share, wait = MASS[kind]
    mass = len(bad_since) >= lo and len(bad_since) >= share * max(1, total)
    return mass, mass and now - min(bad_since) < wait


def group_min(kind: str) -> int:
    """How many of a kind in one message are told as one line: the number that makes a mass event of that kind."""
    return MASS[kind][0] if kind in MASS else GROUP_MIN


def shared_reason(items: list[tuple[str, str, str]]) -> tuple[str, int]:
    """Pure: the error most of the things share, and how many: ("", 0) when no error covers at least 70 % of them. `items`: (name, label, why)."""
    counts: dict[str, int] = {}

    for _, _, why in items:
        counts[why] = counts.get(why, 0) + 1

    why, n = max(counts.items(), key=lambda kv: kv[1]) if counts else ("", 0)
    return (why, n) if n >= 0.7 * len(items) and n >= 2 else ("", 0)


def down_lines(by_kind: dict[str, list[tuple[str, str, str]]]) -> list[str]:
    """Pure: the lines of a DOWN message. Four or more things of one kind in one message are ONE line (see MASS); fewer are one line each. `by_kind`: kind -> [(name, label, why)]."""
    out: list[str] = []

    for kind, items in by_kind.items():
        if len(items) < group_min(kind):
            out += [f"• {label} — {why}" for _, label, why in items]
            continue

        why, n = shared_reason(items)
        shown = ", ".join(name for name, _, _ in items[:NAMES_SHOWN]) + (f", … (+{len(items) - NAMES_SHOWN})" if len(items) > NAMES_SHOWN else "")
        head = f"• {len(items)} {kind}s fail together" + (f", {n} of them with the same error: {why}" if n < len(items) and why else f", all with the same error: {why}" if why else "")
        out.append(head + f"\n  {shown}" + (f"\n  {HINT[kind].format(n=len(items))}" if kind in HINT else ""))

    return out


def up_lines(by_kind: dict[str, list[tuple[str, str, str]]], words: dict[str, str]) -> list[str]:
    """Pure: the lines of an UP message, grouped like the DOWN ones. `by_kind`: kind -> [(name, label, how long it was down)]; `words`: kind -> "down" / "unreachable" / "disconnected"."""
    out: list[str] = []

    for kind, items in by_kind.items():
        if len(items) < group_min(kind):
            out += [f"• {label} — was {words.get(kind, 'down')} {how}" for _, label, how in items]
            continue

        shown = ", ".join(name for name, _, _ in items[:NAMES_SHOWN]) + (f", … (+{len(items) - NAMES_SHOWN})" if len(items) > NAMES_SHOWN else "")
        out.append(f"• {len(items)} {kind}s work again\n  {shown}")

    return out

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
WORDS = {"tunnel": "down", "server": "unreachable", "config": "down", "panel": "down", "node": "disconnected"}


ORPHAN_SECONDS = 7 * 86400          # the state of a thing that is gone is kept this long after the sweep first saw it missing
PRUNE_EVERY = 600                   # the sweep looks every ten minutes (run() looks every 5 seconds)
_PRUNED = {"at": 0}


def prune_orphans(now: int | None = None) -> int:
    """The alert state (`alert_states`) of a tunnel, server, config, panel or node that no longer exists is dropped - after ORPHAN_SECONDS, not at once: a tunnel that a repair rebuilds is deleted and made again under the same
    name ON PURPOSE and its "UP again" message needs the state to survive, and a name used again much later must not inherit `alerted` / `fails` of a thing that is long gone (the audit of 2026-10-21; the owner: "fix it").
    The clock starts at the first sweep that saw the thing missing (kept in the settings document `alert_orphans`); a thing that is back is forgotten. Returns how many states were dropped."""
    now = int(time.time() if now is None else now)

    with session() as db:
        known = {"tunnel": set(db.scalars(select(Tunnel.name))), "server": set(db.scalars(select(Server.name))),
                 "config": set(db.scalars(select(Config.name))), "panel": set(db.scalars(select(Panel.name)))}

        def alive(a: AlertState) -> bool:
            if a.kind == "node":
                return a.ref.split("/", 1)[0] in known["panel"]          # (the nodes of a panel come and go with its reading: only a panel that is gone takes its nodes)

            return a.kind not in known or a.ref in known[a.kind]          # (a kind this sweep does not know is never dropped)

        missing = {f"{a.kind}|{a.ref}": a for a in db.scalars(select(AlertState)) if not alive(a)}
        seen = {k: int(v) for k, v in (st.get("alert_orphans").get("since") or {}).items() if isinstance(v, (int, float))}
        since = {k: seen.get(k, now) for k in missing}
        dropped = 0

        for k, at in since.items():
            if now - at >= ORPHAN_SECONDS:
                db.delete(missing[k])
                dropped += 1

        since = {k: v for k, v in since.items() if now - v < ORPHAN_SECONDS}

    if since != seen:
        st.put("alert_orphans", {"since": since})

    if dropped:
        log.info("alert states of %d things that are gone for good were dropped", dropped)

    return dropped


def run(now: int | None = None) -> None:
    flush()           # whatever Telegram did not take at the last look goes first

    conf = st.get("alerts")
    now = int(time.time() if now is None else now)

    if now - _PRUNED["at"] >= PRUNE_EVERY:
        _PRUNED["at"] = now

        try:
            prune_orphans(now)
        except Exception:
            log.exception("the sweep of the alert states of things that are gone failed")

    muted = set(conf.get("muted") or [])
    rules = rules_from(conf)
    downs: dict[str, list[tuple[str, str, str]]] = {}          # kind -> [(name, label, why)]
    ups: dict[str, list[tuple[str, str, str]]] = {}            # kind -> [(name, label, how long it was down)]
    remind_msgs: list[str] = []
    flap_msgs: list[str] = []
    quiet_for = _dur(rules.flap_quiet)

    with session() as db:
        windows = [(m.start, m.end) for m in db.scalars(select(Maintenance))]
        quiet = in_maintenance(now, windows)
        tracks = {(a.kind, a.ref): a for a in db.scalars(select(AlertState))}
        tunnels = [t for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if t.state not in ("unknown", "starting")]      # (not a verdict about the tunnel)
        servers = [s for s in db.scalars(select(Server).where(Server.enabled.is_(True))) if s.online is not None]
        configs = [c for c in db.scalars(select(Config).where(Config.enabled.is_(True))) if c.status != "unknown"]
        # ONE message for a server that goes off (the owner, 2026-10-23, after he switched a test server off: "only that the server went off - not the tunnel, not the node, not the rest"): what fails only BECAUSE the
        # server is off - its tunnels, the configs that point at its address, the nodes of a panel that point at it - is not judged while it is off. When it is back and one of them is still bad, it is told then, with its own reason.
        off_ids = {s.id for s in servers if s.online is False}
        off_hosts = {str(s.host or "").strip().lower() for s in servers if s.online is False} - {""}
        tunnels = [t for t in tunnels if t.iran_server_id not in off_ids and t.foreign_server_id not in off_ids]
        configs = [c for c in configs if str((c.meta or {}).get("address") or "").strip().lower() not in off_hosts]

        def since_of(kind: str, ref: str) -> int:
            a = tracks.get((kind, ref))
            return int(a.bad_since) if a is not None and a.fails and a.bad_since else now          # (a track that says "bad" at the last look: when it began; else it began at this look)

        def recovering(kind: str, things: list[tuple[str, bool, int]]) -> tuple[int, int]:
            """(how many of this kind were told as down, how many of those are good now but not for `up_seconds` yet): the UPs of a group wait for the last of them, a few seconds, so they come as ONE message."""
            told = ripening = 0

            for ref, bad, since in things:
                a = tracks.get((kind, ref))

                if a is None or not a.alerted:
                    continue

                told += 1

                if not bad and now - (a.good_since or (since if 0 < since <= now else now)) < rules.up_seconds:
                    ripening += 1

            return told, ripening

        # is a mass event going on - many things of one kind bad at the same moment? then their messages wait (see MASS)
        bad_since = {"tunnel": [int(t.state_since or now) for t in tunnels if t.state == "down"],
                     "server": [since_of("server", s.name) for s in servers if s.online is False],
                     "config": [int(c.status_since or now) for c in configs if c.status == "down"]}
        totals = {"tunnel": len(tunnels), "server": len(servers), "config": len(configs)}
        told_ripening = {"tunnel": recovering("tunnel", [(t.name, t.state == "down", int(t.state_since or 0)) for t in tunnels]),
                         "server": recovering("server", [(s.name, s.online is False, 0) for s in servers]),
                         "config": recovering("config", [(c.name, c.status == "down", int(c.status_since or 0)) for c in configs])}
        mass: dict[str, tuple[bool, bool, bool]] = {}          # kind -> (mass going on, DOWNs held, UPs held)

        for kind, since_list in bad_since.items():
            in_mass, hold_down = mass_state(kind, since_list, totals[kind], now)
            told, ripening = told_ripening[kind]
            mass[kind] = (in_mass, hold_down, in_mass or (told >= group_min(kind) and ripening > 0))          # (a group that was told together comes back together: the UPs wait until the last good one has been good for `up_seconds`)

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
            _, hold_down, hold_up = mass.get(kind, (False, False, False))
            new, verdict = decide(Track(a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice, a.flips or 0, a.flap_since or 0, a.quiet_until or 0), bad, now, rules, since, hold=hold_down if bad else hold_up)
            a.fails, a.alerted, a.bad_since, a.good_since, a.last_notice = new.fails, new.alerted, new.bad_since, new.good_since, new.last_notice
            a.flips, a.flap_since, a.quiet_until = new.flips, new.flap_since, new.quiet_until
            return a, verdict

        def say(verdict: str | None, a: AlertState, kind: str, name: str, label: str, why: str = "") -> None:
            if not verdict:
                return

            if verdict == "down":
                downs.setdefault(kind, []).append((name, label, why))
            elif verdict == "remind":
                remind_msgs.append(f"• {label} — still {WORDS[kind]} for {_dur(now - a.bad_since)}")
            elif verdict == "flapping":
                flap_msgs.append(f"• {label} — {a.flips} outages in the last hour; quiet about it for {quiet_for}")
            else:
                ups.setdefault(kind, []).append((name, label, _dur(a.good_since - a.bad_since)))

        for t in tunnels:
            a, verdict = judge("tunnel", t.name, t.state == "down", int(t.state_since or 0))

            if t.name not in muted and not quiet:
                say(verdict, a, "tunnel", t.name, f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})", t.last_error or "no data passes")

        for s in servers:
            a, verdict = judge("server", s.name, s.online is False)

            if s.name not in muted and not quiet:
                say(verdict, a, "server", s.name, f"server {s.name}", s.last_error or "does not answer")

        for c in configs:
            a, verdict = judge("config", c.name, c.status == "down", int(c.status_since or 0))

            if c.name not in muted and not quiet:
                say(verdict, a, "config", c.name, f"config {c.name}", c.last_error or "no answer through it")

        for p in db.scalars(select(Panel).where(Panel.enabled.is_(True))):
            if p.status == "unknown":
                continue

            a, verdict = judge("panel", p.name, p.status == "down", int(p.status_since or 0))

            if p.name not in muted and not quiet:
                say(verdict, a, "panel", p.name, f"panel {p.name}", p.last_error or "does not answer")

            for n in p.nodes or []:
                if str(n.get("address") or "").strip().lower() in off_hosts:
                    continue          # (the node points at a server that is off: the server's own message says it)

                a, verdict = judge("node", f"{p.name}/{n.get('name', '')}", int(n.get("fails", 0)) >= 2)

                if p.name not in muted and not quiet:
                    why = (n.get("status") or "not connected") + (f" ({n['message']})" if n.get("message") else "")
                    say(verdict, a, "node", f"{p.name}/{n.get('name', '')}", f"node {n.get('name', '')} of {p.name}", why)

    # the states are saved; the messages go out after the database is let go (a slow Telegram must not hold a connection)
    if downs:
        _deliver("bad", f"🔴 DOWN ({sum(len(v) for v in downs.values())})\n" + "\n".join(down_lines(downs)))

    if flap_msgs:
        _deliver("warn", f"🟠 FLAPPING ({len(flap_msgs)})\n" + "\n".join(flap_msgs))

    if remind_msgs:
        _deliver("warn", f"⚠ STILL DOWN ({len(remind_msgs)})\n" + "\n".join(remind_msgs))

    if ups:
        _deliver("ok", f"🟢 UP again ({sum(len(v) for v in ups.values())})\n" + "\n".join(up_lines(ups, WORDS)))