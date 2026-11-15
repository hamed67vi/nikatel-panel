"""THE FREE QUALITY SIGNALS: what the users' own traffic says about a tunnel, at no cost in bytes (1.69.281; the owner, 2026-11-15, «بله، بساز» - after the speed tests were set to one in ten days, and a cheap way to see the quality
of the tunnels between them was proposed). A speed test moves up to 120 MB through an Iran server, which bills it; the traffic of the users and the TCP sessions of the tunnels themselves are there anyway.

  1. the tunnel's own TCP     (ssh.py `tcp_health`, one light command per Iran server every 5 minutes) - the sockets of the tunnel's control connection (Backhaul over its GRE link, BackPack, Chisel ...) carry every user's data, and the kernel
                              counts what each of them sent and what it had to send AGAIN (`bytes_sent`, `bytes_retrans` of `ss -tin`). A clean line resends about 0.1 % (measured 2026-11-15 on four live tunnels); a line that loses
                              packets resends several percent. The share of the last hour, from the change of the counters between two reads, is the signal. Nothing is sent.
  2. the users against themselves  the speed the users get through the tunnel (the checks' counters, `TunnelCheck.rx_mbps` + `tx_mbps`) over the last 15 minutes against the SAME HOUR of the last days: a tunnel whose users
                              carry under half of what they usually carry at this hour, when that usual is a real traffic, is looked at. (A fall of the users' traffic is no proof: fewer people may be online - it is a reason to ask.)

What a signal DOES: it asks the bandwidth job for a speed test of that tunnel NOW (`bw.next_at`), at most once in ASK_GAP (12 hours) per tunnel and only while the day's volume of the Doctor's tests allows routine tests, and writes why
(the record `fl`, an event). The speed test is the confirmation; it also decides (a drop, slow, fine) by the rules it always had. Nothing is changed by a signal alone, no server is touched, nothing is restarted.

The pure helpers (`parse`, `step`, `share`, `same_hour`, `users_fell`) carry the decisions and are tested."""
from __future__ import annotations

import re
import statistics
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import select

from .. import jobs
from ..db import session
from ..log import get_logger
from ..models import Tunnel, TunnelCheck, is_temp
from ..ssh import Node, SSHError, run_light
from . import testbudget
from .events import emit

log = get_logger("freesignals")
EVERY = 300                    # the job: every 5 minutes
TIMEOUT = 15                   # one read of one server (ss lists the sockets of the whole server before it filters them)
WINDOW = 12                    # reads kept per tunnel: an hour
MIN_BYTES = 3_000_000          # the window must have SENT this much through the tunnel before a resend share is judged (an idle tunnel says nothing)
RETRANS_BAD = 0.02             # 2 % of the bytes had to be sent again (a clean line: 0.1 %)
CONFIRMS = 2                   # evaluations in a row that must say so (10 minutes): one bad read is a blip
FELL = 0.5                     # the users carry under this share of their usual traffic at this hour ...
MIN_USUAL_MBPS = 10.0          # ... and that usual traffic is at least this (a tunnel with a few Mb/s of users has no "usual" to fall from)
MIN_NOW, MIN_USUAL = 8, 40     # readings needed: in the last 15 minutes / of the same hours of the last days
USUAL_DAYS = 7
HOUR_SPAN = 1                  # the same hour of the day, one hour either side
LOOK_A = 900                   # the users' traffic is looked at at most this often per tunnel
ASK_GAP = 12 * 3600            # a tunnel is asked for a speed test by a signal at most this often
PER_PEER = 300                 # sockets kept of one far end (a plain Backhaul has one socket per user connection)
TEHRAN = 12600
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_PREV: dict[int, dict] = {}            # tunnel -> its sockets at the last read {(local port, peer port): (bytes sent, bytes resent, rtt ms)}
_WIN: dict[int, deque] = {}            # tunnel -> the last WINDOW reads (time, bytes sent since the read before, bytes resent)
_BAD: dict[int, int] = {}              # tunnel -> evaluations in a row that said "the tunnel's TCP resends too much"
_FELL: dict[int, int] = {}             # tunnel -> evaluations in a row that said "the users fell"
_LAST_A: dict[int, float] = {}         # tunnel -> when the users' traffic was last looked at
VIEW: dict[int, dict] = {}             # what the last look saw, per tunnel (for a page; never stored)


@dataclass
class Watch:
    id: int
    name: str
    iran_id: int
    peer: str
    ports: set = field(default_factory=set)


# ------------------------------------------------------------------ pure
def parse(out: str) -> dict[str, dict[tuple[int, int], tuple[int, int, float | None]]]:
    """Pure: the answer of the `tcp_health` script -> {far end: {(local port, peer port): (bytes sent, bytes resent, rtt ms | None)}}. A line of any other shape is ignored."""
    peers: dict[str, dict] = {}

    for line in str(out).splitlines():
        p = line.split()

        if len(p) == 7 and p[0] == "S":
            try:
                peers.setdefault(p[1], {})[(int(p[2]), int(p[3]))] = (int(p[4]), int(p[5]), None if p[6] == "-" else float(p[6]))
            except ValueError:
                continue

    return peers


def step(prev: dict | None, cur: dict, ports: set) -> tuple[int, int, list[float]]:
    """Pure: what the tunnel's sockets sent and resent since the last read - (bytes sent, bytes resent, the rtts now), of the sockets that belong to the tunnel (its control port at either end). The FIRST read has nothing
    to compare with: no bytes (the lifetime counters of a socket that was opened long ago are not "since the last read"). A socket that is new since the last read counts what it has; a counter that went back
    (a port used again by a new connection) counts as a new socket."""
    rtts = [v[2] for k, v in cur.items() if (k[0] in ports or k[1] in ports) and v[2] is not None]

    if prev is None:
        return 0, 0, rtts

    dsent = dretr = 0

    for key, (sent, retr, _rtt) in cur.items():
        if key[0] not in ports and key[1] not in ports:
            continue

        old = prev.get(key)

        if old is not None and sent >= old[0] and retr >= old[1]:
            dsent, dretr = dsent + sent - old[0], dretr + retr - old[1]
        else:
            dsent, dretr = dsent + sent, dretr + retr

    return dsent, dretr, rtts


def share(window) -> float | None:
    """Pure: the share of the bytes the tunnel sent in the window that it had to send again; None while the window holds less than MIN_BYTES."""
    sent, retr = sum(x[1] for x in window), sum(x[2] for x in window)
    return retr / sent if sent >= MIN_BYTES else None


def _hour(ts: float) -> int:
    return int((ts + TEHRAN) // 3600) % 24


def same_hour(rows, now: float) -> tuple[list[float], list[float], int]:
    """Pure: from rows (time, users' rx Mb/s, tx Mb/s) -> (the readings of the last 15 minutes, the readings of the SAME HOURS (this hour and one either side) of the last days, how many different days those are from). A reading is
    rx + tx; a row with neither is left out. The last two hours are not part of "usual" - they are what is being judged."""
    cur, usual, days = [], [], set()
    h = _hour(now)

    for ts, rx, tx in rows:
        if rx is None and tx is None:
            continue

        v = float(rx or 0.0) + float(tx or 0.0)

        if now - 900 <= ts <= now:
            cur.append(v)
        elif now - USUAL_DAYS * 86400 <= ts < now - 7200 and min((_hour(ts) - h) % 24, (h - _hour(ts)) % 24) <= HOUR_SPAN:
            usual.append(v)
            days.add(int((ts + TEHRAN) // 86400))

    return cur, usual, len(days)


def users_fell(cur: list[float], usual: list[float], days: int) -> bool:
    """Pure: do the users carry under FELL of what they usually carry at this hour? Needs a real usual (MIN_USUAL_MBPS, MIN_USUAL readings from two different days) and enough readings now."""
    if len(cur) < MIN_NOW or len(usual) < MIN_USUAL or days < 2:
        return False

    u = statistics.median(usual)
    return u >= MIN_USUAL_MBPS and statistics.median(cur) < FELL * u


# ------------------------------------------------------------------ the look
def _load() -> tuple[list[Watch], dict[int, Node]]:
    with session() as db:
        out: list[Watch] = []
        nodes: dict[int, Node] = {}

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), Tunnel.state.in_(("up", "degraded")))):
            if is_temp(t.name) or t.iran is None or t.foreign is None or not t.port or t.iran.online is False or t.foreign.online is False:
                continue

            peer = t.foreign_tunnel_ip or t.foreign.host

            if not _IPV4.match(peer or ""):
                continue

            out.append(Watch(t.id, t.name, t.iran_server_id, peer, {int(t.port)}))
            nodes.setdefault(t.iran_server_id, Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port))

    return out, nodes


def _read_one(node: Node, peers: list[str]) -> dict | None:
    try:
        return parse(run_light(node, "tcp_health", {"PEERS": " ".join(peers), "KEEP": str(PER_PEER)}, timeout=TIMEOUT))
    except (SSHError, ValueError) as e:
        log.debug("free signals: a server could not be read: %s", str(e)[:100])
        return None


def ask(tunnel_id: int, why: str, now: float | None = None) -> bool:
    """A signal asks for a speed test of the tunnel NOW (`bw.next_at`) - at most once in ASK_GAP, only while the day's volume of the Doctor's tests allows routine ones - and says why (the record `fl`, an event).
    True when it asked. A tunnel never measured is due at once anyway: only the reason is written."""
    now = int(now or time.time())

    if not testbudget.allow("routine", now):
        return False

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None or is_temp(t.name) or t.state not in ("up", "degraded"):
            return False

        rec = dict(t.record or {})
        fl = dict(rec.get("fl") or {})

        if now - int(fl.get("asked") or 0) < ASK_GAP:
            return False

        if rec.get("bw"):
            bw = dict(rec["bw"])
            bw["next_at"] = min(int(bw.get("next_at") or now), now)
            rec["bw"] = bw

        fl.update({"asked": now, "why": why[:200]})
        rec["fl"] = fl
        t.record = rec
        emit(db, "info", "tunnels", f"{t.name}: {why} - a speed test is asked to confirm it.", t.name)

    log.info("free signals: %s: %s - a speed test is asked", tunnel_id, why)
    return True


def evaluate_tcp(w: Watch, cur: dict, now: float) -> None:
    """The tunnel's own TCP of one read: the window, the share, and - after CONFIRMS bad evaluations in a row - the ask."""
    if len(cur) >= PER_PEER:          # the script keeps PER_PEER sockets of a far end: when it is full, WHICH sockets it kept can differ between two reads, and their change cannot be told from new sockets - no judgement (a plain Backhaul has one socket per user connection)
        VIEW.setdefault(w.id, {})["capped"] = True
        _PREV.pop(w.id, None)
        return

    prev = _PREV.get(w.id)
    dsent, dretr, rtts = step(prev, cur, w.ports)
    _PREV[w.id] = cur

    if prev is None:
        return

    win = _WIN.setdefault(w.id, deque(maxlen=WINDOW))
    win.append((now, dsent, dretr))
    sh = share(win)
    VIEW.setdefault(w.id, {}).update({"retrans_pct": None if sh is None else round(100 * sh, 2), "rtt_ms": round(statistics.median(rtts), 1) if rtts else None, "sockets": sum(1 for k in cur if k[0] in w.ports or k[1] in w.ports), "at": int(now)})
    _BAD[w.id] = _BAD.get(w.id, 0) + 1 if sh is not None and sh >= RETRANS_BAD else 0

    if _BAD[w.id] >= CONFIRMS and ask(w.id, f"the tunnel's own TCP sent {100 * sh:.1f} % of its bytes again in the last hour (a clean line: about 0.1 %)", now):
        _BAD[w.id] = 0


def evaluate_users(w: Watch, now: float) -> None:
    """The users of the tunnel against the same hours of the last days (at most once in LOOK_A per tunnel)."""
    if now - _LAST_A.get(w.id, 0.0) < LOOK_A:
        return

    _LAST_A[w.id] = now

    with session() as db:
        rows = db.execute(select(TunnelCheck.ts, TunnelCheck.rx_mbps, TunnelCheck.tx_mbps).where(TunnelCheck.tunnel_id == w.id, TunnelCheck.ts >= int(now) - USUAL_DAYS * 86400)).all()

    cur, usual, days = same_hour(rows, now)
    fell = users_fell(cur, usual, days)
    VIEW.setdefault(w.id, {}).update({"users_now": round(statistics.median(cur), 1) if cur else None, "users_usual": round(statistics.median(usual), 1) if usual else None})
    _FELL[w.id] = _FELL.get(w.id, 0) + 1 if fell else 0

    if _FELL[w.id] >= CONFIRMS and ask(w.id, f"its users carry {statistics.median(cur):.0f} Mb/s, under half of the {statistics.median(usual):.0f} Mb/s they usually carry at this hour", now):
        _FELL[w.id] = 0


def run() -> None:
    """The job (every 5 minutes): one light read per Iran server, then the two signals of every tunnel. Reads only; at most it asks the bandwidth job for a speed test."""
    if jobs.stopping():
        return

    watches, nodes = _load()

    if not watches:
        return

    ask_of: dict[int, list[str]] = {}

    for w in watches:
        ask_of.setdefault(w.iran_id, [])

        if w.peer not in ask_of[w.iran_id]:
            ask_of[w.iran_id].append(w.peer)

    sids = list(ask_of)

    with ThreadPoolExecutor(min(6, len(sids))) as ex:
        reads = dict(zip(sids, ex.map(lambda s: _read_one(nodes[s], ask_of[s]), sids)))

    now = time.time()

    for w in watches:
        r = reads.get(w.iran_id)

        try:
            if r is not None:
                evaluate_tcp(w, r.get(w.peer, {}), now)

            evaluate_users(w, now)
        except Exception:  # the boundary of one tunnel: an odd record or a locked database must not stop the look at the others
            log.exception("free signals: %s could not be looked at", w.name)
