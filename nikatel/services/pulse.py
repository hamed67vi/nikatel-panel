"""The pulse: a quick liveness check of every server, every 3 seconds - one TCP connection to its SSH port (2 s).

The full SSH sample runs every two minutes and is the one that reads cpu, ram and disk; it is far too slow to notice a server that was switched off or
that came back. Two misses in a row mark the server off (~8 s); a hit on a server that is marked off puts it back at once when the pulse itself marked
it, and otherwise (the full sample marked it, with an SSH error) asks the server over SSH right now - once per 15 s per server - so the answer comes in
seconds, not at the next full sample. A server whose SSH login is really broken stays off (the SSH answer says so). Every change of state wakes the fast
lane of the tunnels, so the tunnels of the server are looked at in the same moment."""
from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .. import jobs
from ..db import session
from ..log import get_logger
from ..models import Server
from .events import emit

log = get_logger("servers")

TAG = "no answer on the SSH port"
MISSES = 2
TIMEOUT = 2.0
RETRY_SECONDS = 15
REFUSED = "refused"          # what tcp_open answers when the machine itself replied with a refusal: it is ON (the owner, 2026-10-29: BOT answered ping, 80 and 443 and showed Offline - its firewall refuses new connections to port 22 that come too fast, and the pulse connects every 3 s)
REFUSAL = "SSH port refuses the panel's connections - the server is on (its firewall may limit new connections from the panel's address)"
SLOW_SECONDS = 20          # a server that refuses is looked at every 20 s, not every 3 s: a limit of a few connections per half minute is not kept tripped by the pulse itself
_slow_until: dict[int, float] = {}
_refusing: dict[int, float] = {}          # server id -> when it last answered with a refusal
VIA_GRACE = 150          # a server the panel reaches over SSH only THROUGH another Iran server (its address is cut from the panel's host, not from Iran) is on while its last full look is younger than this (the full look is every two minutes)
VIA_MEMORY = 900          # ...and it counts as such for this long after the panel was last seen going that way
_via_seen: dict[int, float] = {}          # server id -> when the panel last reached it through a jump server
PROOF_GRACE = 200          # an Iran server the panel's host cannot connect to is ON while someone inside Iran saw it answer this recently (services/vantage.py asks every minute)
IRAN_ONLY = "the panel's host cannot connect to it, but it is on"          # the start of the note in its row (the end says who saw it)
_missed_at: dict[int, float] = {}          # server id -> when the panel's own connection to it last failed
_proof: dict[int, tuple[float, str]] = {}          # server id -> (when, who) an Iran server or check-host.net last saw it answer
_miss: dict[int, int] = {}
_seen: dict[int, float] = {}          # server id -> when its SSH port last answered
_asked: dict[int, float] = {}
_ASKING: set[int] = set()


def tcp_open(host: str, port: int, timeout: float = TIMEOUT):
    """True: connected. REFUSED: the machine answered with a refusal (nothing listens, or its firewall rejects the connection) - a switched-off machine never does that. False: no answer."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except ConnectionRefusedError:
        return REFUSED
    except OSError:
        return False


def tcp_ok_recently(sid: int, seconds: float = 30.0) -> bool:
    """Did the SSH port of this server answer in the last `seconds`? (A server whose port answers is ON, whatever its SSH session does.)"""
    return time.time() - _seen.get(sid, 0.0) <= seconds


def carried(sid: int, last_seen: float | None) -> bool:
    """True: the panel's own connection to this server fails (the pulse missed) but the server is reached through another Iran server and was looked at over SSH lately - it is ON. The owner, 2026-10-31: the tunnels of two
    Iran servers "show down but their configs are connected": the panel's host (abroad) cannot reach them, the full look went through a jump server every two minutes and put them on, and the pulse took them off again
    three seconds later - a new "answers again" / "does not answer" pair every two minutes, a tunnel that flapped down and up, and the Doctor repairing a tunnel that carried the users."""
    now = time.time()
    return now - _via_seen.get(sid, 0.0) < VIA_MEMORY and now - float(last_seen or 0) <= VIA_GRACE


def prove(sid: int, how: str) -> bool:
    """An Iran server (or check-host.net) saw this server answer from inside Iran. True when it was not proven a moment ago (the first proof is the news)."""
    new = not proven(sid)
    _proof[sid] = (time.time(), how)
    return new


def proven(sid: int) -> bool:
    p = _proof.get(sid)
    return bool(p) and time.time() - p[0] <= PROOF_GRACE


def proof_how(sid: int) -> str:
    return (_proof.get(sid) or (0.0, ""))[1]


def missing(seconds: float = 30.0) -> list[int]:
    """The servers whose connection from the panel's host failed in the last `seconds` (services/vantage.py asks about them from inside Iran)."""
    now = time.time()
    return [sid for sid, t in _missed_at.items() if now - t <= seconds]


def _wake_vantage() -> None:
    try:
        jobs.trigger("vantage")
    except KeyError:
        return          # not registered (tests)


def refusing(sid: int, seconds: float = 60.0) -> bool:
    """Did this server answer the pulse with a refusal lately? (It is on; its SSH port turns the panel's connections away.)"""
    return time.time() - _refusing.get(sid, 0.0) <= seconds


def _wake_tunnels(server_ids: list[int] | None = None) -> None:
    try:
        jobs.trigger("tunnels-fast")
    except KeyError:
        return                         # not registered (tests)

    if server_ids:          # (the fast lane looks only at tunnels that are NOT up: a tunnel of a server that just went off was still "up" until the slow cycle, up to a minute - the owner, 2026-10-20: "it detected very late")
        threading.Thread(target=_look_at, args=(list(server_ids),), name="pulse-look", daemon=True).start()


def _look_at(server_ids: list[int]) -> None:
    from . import tunnels

    try:
        tunnels.check_of_servers(server_ids)
    except Exception:
        log.exception("pulse: the tunnels of the servers %s could not be looked at", server_ids)


def _ask_over_ssh(sid: int) -> None:
    """The port answers but the server is marked off by a full sample: one full sample of this server now (it puts the server back, or says why not)."""
    from . import facts

    try:
        facts.sample_server(sid)
    except Exception:
        log.exception("pulse: the sample of server %s failed", sid)
    finally:
        _ASKING.discard(sid)


def _rebooting(srv) -> bool:
    from . import facts

    return facts._rebooting(srv)


def run() -> None:
    with session() as db:
        rows = [(s.id, s.host, s.ssh_port) for s in db.scalars(select(Server).where(Server.enabled.is_(True)))]

    if not rows:
        return

    from .. import ssh as _ssh

    t_now = time.time()

    for r in rows:
        if _ssh.via_of(r[1]):
            _via_seen[r[0]] = t_now

    rows = [r for r in rows if _slow_until.get(r[0], 0.0) <= t_now]          # (a server that refuses is looked at only every SLOW_SECONDS)

    if not rows:
        return

    with ThreadPoolExecutor(min(32, max(4, len(rows)))) as ex:
        res = list(ex.map(lambda r: (r[0], tcp_open(r[1], r[2])), rows))

    now = int(time.time())
    changed = False
    moved: list[int] = []          # the servers that went off or came back in this round
    ask: list[int] = []

    with session() as db:
        for sid, ok in res:
            s = db.get(Server, sid)

            if s is None:
                continue

            if ok:
                refused = ok == REFUSED
                _miss[sid] = 0
                _seen[sid] = time.time()

                _missed_at.pop(sid, None)
                _proof.pop(sid, None)

                if refused:
                    _refusing[sid] = time.time()
                    _slow_until[sid] = time.time() + SLOW_SECONDS
                else:
                    _refusing.pop(sid, None)
                    _slow_until.pop(sid, None)

                    if s.online is not False and (s.last_error or "").startswith((REFUSAL, IRAN_ONLY)):
                        s.last_error = ""          # (it takes connections again)

                if s.online is False:
                    if (s.last_error or "").startswith(TAG):
                        s.online, s.last_error, s.last_seen_at = True, REFUSAL if refused else "", now
                        emit(db, "warn" if refused else "ok", "servers", f"Server {s.name} is on: {REFUSAL}." if refused else f"Server {s.name} answers again.", s.name)
                        log.info("pulse: %s %s", s.name, "refuses SSH but is on" if refused else "answers again")
                        changed = True
                        moved.append(sid)
                    elif sid not in _ASKING and time.time() - _asked.get(sid, 0) >= RETRY_SECONDS:
                        ask.append(sid)
                elif refused and not s.last_error:
                    s.last_error = REFUSAL          # (it is on, and its SSH port turns the connections away: the row says why)

                continue

            first = sid not in _missed_at or time.time() - _missed_at[sid] > 30
            _missed_at[sid] = time.time()

            if proven(sid) or carried(sid, s.last_seen_at):          # (the panel's own way in is cut, but somebody inside Iran sees it or the way through another Iran server works: it is ON - and not asked again every 3 s)
                _miss[sid] = 0
                _slow_until[sid] = time.time() + SLOW_SECONDS
                note = f"{IRAN_ONLY} ({proof_how(sid) if proven(sid) else 'its SSH answers through another Iran server'})"

                if s.online is False:
                    s.online, s.last_error = True, note[:300]
                    emit(db, "warn", "servers", f"Server {s.name} is on: {note}.", s.name)
                    log.info("pulse: %s is cut from the panel's host but is on", s.name)
                    changed = True
                    moved.append(sid)
                elif not s.last_error or s.last_error.startswith(IRAN_ONLY):
                    s.last_error = note[:300]

                continue

            if first and s.role == "iran":          # (the first miss of an Iran server: ask from inside Iran now, not at the next minute)
                _wake_vantage()

            _miss[sid] = _miss.get(sid, 0) + 1
            _slow_until.pop(sid, None)
            _refusing.pop(sid, None)

            if _miss[sid] >= MISSES and s.online is not False:
                s.online = False
                s.last_error = f"{TAG} {s.ssh_port}: {_miss[sid]} quick checks in a row got no connection"

                if _rebooting(s):          # (a restart asked from the panel is no alarm - the full sample (facts.sample_server) keeps the same rule: the row says Rebooting, no red event, the tunnels are not woken)
                    log.info("pulse: %s is rebooting (asked from the panel)", s.name)
                    continue

                emit(db, "bad", "servers", f"Server {s.name} does not answer: {s.last_error}", s.name)
                log.warning("pulse: %s does not answer on port %s", s.name, s.ssh_port)
                changed = True
                moved.append(sid)

    for sid in ask:
        _asked[sid] = time.time()
        _ASKING.add(sid)
        threading.Thread(target=_ask_over_ssh, args=(sid,), name=f"pulse-ask-{sid}", daemon=True).start()

    if changed:
        _wake_tunnels(moved)