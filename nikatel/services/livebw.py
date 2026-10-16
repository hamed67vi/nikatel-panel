"""What the users use of every tunnel RIGHT NOW: the byte counters of the tunnel's network card on its Iran server, read every few seconds and turned into rates.
(The owner, 2026-10-17: "show the capacity, and beside it the bandwidth the users are using - the numbers must be real, reliable and live".)

  where it is read    the card of the tunnel on its Iran server - the GRE link of a Backhaul / GRE tunnel, the L3 link (bp<N>) of a BackPack Direct tunnel: the kernel's own counters
                      (/sys/class/net/<card>/statistics), ONE light command per Iran server for all its cards (ssh.py `iface_counters`). A tunnel with no card (a reverse proxy without a link) has no
                      counter: its cell says "-", never a made-up zero. Download is what comes IN over the link (foreign -> Iran: what the users download), upload what goes out.
  how fast            every 5 s while somebody looks at the tunnels (the page asks GET /api/tunnels/live), every 30 s otherwise, so that the first number is there when the page opens.
  how it is counted   the rate over the last ~15 s (the newest read against the oldest one inside the window) on the SERVER's own uptime clock: steady, and still live. A counter that went back (the card
                      was made again) or a clock that did (a reboot) starts the window again; a rate no card can carry is dropped.
  what is left out    the panel's own tests of the link (`quiet`: the bandwidth test, the speed tests): their traffic is not the users'.

The pure helpers (`parse_counters`, `push`, `rate`) carry the decisions and are tested."""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from sqlalchemy import select

from .. import jobs
from ..db import session
from ..log import get_logger
from ..models import Tunnel, is_temp
from ..ssh import Node, SSHError, run_light
from .tunnels import plan_for

log = get_logger("livebw")
FAST = 5.0                   # seconds between two reads of a server while the tunnels are looked at ...
SLOW = 30.0                  # ... and while they are not
ACTIVE_FOR = 45.0            # a request for the numbers keeps the fast pace for this long
WINDOW = 15.0                # the rate is counted over about this many seconds: steady, and still live
MIN_SPAN = 4.0               # a rate needs at least this long between its two reads
KEEP = 90.0                  # the reads of a card are kept this long (seconds of the server's uptime)
VIEW_FOR = 120.0             # a number older than this is not given to the page at all (it falls back to the check of the tunnel)
MAX_MBPS = 400_000.0         # a rate above this is a wrong counter, not a rate
TIMEOUT = 8                  # one read of one server
CARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,14}")
_LOCK = threading.Lock()
_READS: dict[tuple[int, str], deque] = {}          # (Iran server, card) -> its last reads (uptime, rx_bytes, tx_bytes)
_LIVE: dict[int, dict] = {}                        # tunnel -> {down, up, span, at}: the rate of its card and when it was read
_ASKED = [0.0]                                     # when the page last asked for the numbers
_LAST = [0.0]                                      # when the servers were last read
_HOLD: dict[int, float] = {}                       # tunnel -> until when a test of the panel is on its link
_BACKOFF: dict[int, tuple[int, float]] = {}        # server -> (failures in a row, not before): a server that does not answer is not asked every 5 s


def parse_counters(out: str) -> tuple[float | None, dict[str, tuple[int, int]]]:
    """Pure: the uptime and the (rx, tx) bytes of every card in the answer of the `iface_counters` script ("IF <card> <rx> <tx>" and "UP <seconds>")."""
    up: float | None = None
    cards: dict[str, tuple[int, int]] = {}

    for ln in out.splitlines():
        p = ln.split()

        try:
            if len(p) == 2 and p[0] == "UP":
                up = float(p[1])
            elif len(p) == 4 and p[0] == "IF":
                cards[p[1]] = (int(p[2]), int(p[3]))
        except ValueError:
            continue

    return up, cards


def push(reads: deque, up: float, rx: int, tx: int) -> None:
    """Add a read to a card's window; a counter that went back (the card was made again) or a clock that did (the server restarted) starts the window again."""
    if reads and (rx < reads[-1][1] or tx < reads[-1][2] or up <= reads[-1][0]):
        reads.clear()

    reads.append((up, rx, tx))

    while reads and up - reads[0][0] > KEEP:
        reads.popleft()


def rate(reads: list[tuple[float, int, int]], window: float = WINDOW) -> dict | None:
    """Pure: {down, up, span} in Mb/s from the reads of a card (uptime, rx_bytes, tx_bytes), oldest first: the newest against the oldest read inside `window` - or, when only older ones are far enough away
    (a read every 30 s), the nearest of them. None: no pair `MIN_SPAN` apart, a counter that went back, or more than a card can carry."""
    if len(reads) < 2:
        return None

    t1, rx1, tx1 = reads[-1]
    far = [r for r in reads[:-1] if t1 - r[0] >= MIN_SPAN]

    if not far:
        return None

    inside = [r for r in far if t1 - r[0] <= window]
    t0, rx0, tx0 = inside[0] if inside else far[-1]

    if rx1 < rx0 or tx1 < tx0:
        return None

    dt = t1 - t0
    down, up = (rx1 - rx0) * 8 / dt / 1e6, (tx1 - tx0) * 8 / dt / 1e6

    if max(down, up) > MAX_MBPS:
        return None

    return {"down": round(down, 2), "up": round(up, 2), "span": round(dt, 1)}


def targets() -> list[dict]:
    """The Iran servers to read, each with the cards of its tunnels: [{server_id, node, cards: {card: [tunnel ids]}}]. A tunnel with no card, a test tunnel and a server that is off are left out."""
    out: dict[int, dict] = {}

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            if is_temp(t.name) or t.iran is None or not t.iran.enabled or t.iran.online is False:
                continue

            plan = plan_for(t)
            card = plan.iface or plan.ping_iface

            if not card or not CARD.fullmatch(card):
                continue

            e = out.setdefault(t.iran_server_id, {"server_id": t.iran_server_id, "node": Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), "cards": {}})
            e["cards"].setdefault(card, []).append(t.id)

    return list(out.values())


def hold(tunnel_id: int, seconds: float) -> None:
    """A test of the panel is on the link of this tunnel for `seconds`: what it moves is not the users', so the numbers wait (and start again afterwards)."""
    with _LOCK:
        _HOLD[tunnel_id] = time.time() + seconds


@contextmanager
def quiet(tunnel_id: int, seconds: float = 60.0):
    """While the panel tests the link of a tunnel (bandwidth test, speed tests) the live numbers of that tunnel wait; a few seconds after the test they start again."""
    hold(tunnel_id, seconds)

    try:
        yield
    finally:
        hold(tunnel_id, 6.0)


def _read(target: dict) -> None:
    """One read of one Iran server: its cards' counters go into their windows and the rates into `_LIVE`."""
    sid, cards = target["server_id"], target["cards"]
    now = time.time()
    fails, until = _BACKOFF.get(sid, (0, 0.0))

    if now < until:
        return

    try:
        out = run_light(target["node"], "iface_counters", {"IFACES": " ".join(sorted(cards))}, timeout=TIMEOUT)
    except (SSHError, ValueError) as e:
        n = fails + 1
        _BACKOFF[sid] = (n, now + min(60.0, FAST * 2 ** min(n, 4)))
        log.debug("live numbers: server %s not read (%s)", sid, str(e)[:100])
        return

    _BACKOFF.pop(sid, None)
    up, found = parse_counters(out)

    if up is None:
        return

    at = time.time()

    for card, (rx, tx) in found.items():
        ids = cards.get(card, [])
        reads = _READS.setdefault((sid, card), deque())

        if any(_HOLD.get(i, 0.0) > at for i in ids):
            reads.clear()                      # (a test of the panel is on this link: nothing of it is the users')
            continue

        push(reads, up, rx, tx)
        r = rate(list(reads))

        if r is not None:
            for i in ids:
                _LIVE[i] = {**r, "at": at}


def _prune(todo: list[dict]) -> None:
    """What belongs to a tunnel or a server that is gone goes too."""
    cards = {(t["server_id"], c) for t in todo for c in t["cards"]}
    ids = {i for t in todo for v in t["cards"].values() for i in v}

    for k in [k for k in _READS if k not in cards]:
        _READS.pop(k, None)

    for i in [i for i in _LIVE if i not in ids]:
        _LIVE.pop(i, None)

    for k in [k for k in _BACKOFF if k not in {t["server_id"] for t in todo}]:
        _BACKOFF.pop(k, None)


def run() -> None:
    """Job (every 5 s): every Iran server that carries a tunnel with a card is read - at the fast pace while the page asks for the numbers, once in 30 s otherwise (so the first number is ready when it opens)."""
    if jobs.stopping():
        return

    now = time.time()

    if now - _ASKED[0] > ACTIVE_FOR and now - _LAST[0] < SLOW:
        return

    _LAST[0] = now
    todo = targets()
    _prune(todo)

    if todo:
        with ThreadPoolExecutor(min(8, len(todo))) as ex:
            list(ex.map(_read, todo))


def ask() -> None:
    """The page looks at the tunnels: the servers are read at the fast pace for the next ACTIVE_FOR seconds."""
    _ASKED[0] = time.time()


def view() -> dict[str, dict]:
    """What the page gets: {tunnel id: {down, up, span, at, age}} for the tunnels read in the last two minutes (Mb/s; `age` in seconds)."""
    now = time.time()
    return {str(i): {**v, "age": round(now - v["at"], 1)} for i, v in list(_LIVE.items()) if now - v["at"] <= VIEW_FOR}
