"""The pulse: a quick liveness check of every server, every 5 seconds - one TCP connection to its SSH port (2 s).

The full SSH sample runs every two minutes and is the one that reads cpu, ram and disk; it is far too slow to notice a server that was switched off or
that came back. Two misses in a row mark the server off (~12 s); a hit on a server that is marked off puts it back at once when the pulse itself marked
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
_miss: dict[int, int] = {}
_seen: dict[int, float] = {}          # server id -> when its SSH port last answered
_asked: dict[int, float] = {}
_ASKING: set[int] = set()


def tcp_open(host: str, port: int, timeout: float = TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def tcp_ok_recently(sid: int, seconds: float = 30.0) -> bool:
    """Did the SSH port of this server answer in the last `seconds`? (A server whose port answers is ON, whatever its SSH session does.)"""
    return time.time() - _seen.get(sid, 0.0) <= seconds


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


def run() -> None:
    with session() as db:
        rows = [(s.id, s.host, s.ssh_port) for s in db.scalars(select(Server).where(Server.enabled.is_(True)))]

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
                _miss[sid] = 0
                _seen[sid] = time.time()

                if s.online is False:
                    if (s.last_error or "").startswith(TAG):
                        s.online, s.last_error, s.last_seen_at = True, "", now
                        emit(db, "ok", "servers", f"Server {s.name} answers again.", s.name)
                        log.info("pulse: %s answers again", s.name)
                        changed = True
                        moved.append(sid)
                    elif sid not in _ASKING and time.time() - _asked.get(sid, 0) >= RETRY_SECONDS:
                        ask.append(sid)

                continue

            _miss[sid] = _miss.get(sid, 0) + 1

            if _miss[sid] >= MISSES and s.online is not False:
                s.online = False
                s.last_error = f"{TAG} {s.ssh_port}: {_miss[sid]} quick checks in a row got no connection"
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