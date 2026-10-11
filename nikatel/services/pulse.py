"""The pulse: a quick liveness check of every server, every 10 seconds - one TCP connection to its SSH port (3 s).

The full SSH sample runs every two minutes and is the one that reads cpu, ram and disk; it is far too slow to notice a
server that was switched off (the tunnel check saw the GRE link die a minute before the server was called off).
Two misses in a row mark the server off at once; one hit puts it back. The pulse only puts back a server it marked off
itself: a server whose SSH login is broken stays as the full sample judged it."""
from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Server
from .events import emit

log = get_logger("servers")

TAG = "no answer on the SSH port"
MISSES = 2
TIMEOUT = 3.0
_miss: dict[int, int] = {}


def tcp_open(host: str, port: int, timeout: float = TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run() -> None:
    with session() as db:
        rows = [(s.id, s.host, s.ssh_port) for s in db.scalars(select(Server).where(Server.enabled.is_(True)))]

    if not rows:
        return

    with ThreadPoolExecutor(min(32, max(4, len(rows)))) as ex:
        res = list(ex.map(lambda r: (r[0], tcp_open(r[1], r[2])), rows))

    now = int(time.time())

    with session() as db:
        for sid, ok in res:
            s = db.get(Server, sid)

            if s is None:
                continue

            if ok:
                _miss[sid] = 0

                if s.online is False and (s.last_error or "").startswith(TAG):
                    s.online, s.last_error, s.last_seen_at = True, "", now
                    emit(db, "ok", "servers", f"Server {s.name} answers again.", s.name)
                    log.info("pulse: %s answers again", s.name)

                continue

            _miss[sid] = _miss.get(sid, 0) + 1

            if _miss[sid] >= MISSES and s.online is not False:
                s.online = False
                s.last_error = f"{TAG} {s.ssh_port}: {_miss[sid]} quick checks in a row got no connection"
                emit(db, "bad", "servers", f"Server {s.name} does not answer: {s.last_error}", s.name)
                log.warning("pulse: %s does not answer on port %s", s.name, s.ssh_port)
