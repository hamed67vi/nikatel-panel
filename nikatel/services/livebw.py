"""What the users use of every tunnel RIGHT NOW: the byte counters of the tunnel's network card on its Iran server, read every few seconds and turned into rates.
(The owner, 2026-10-17: "show the capacity, and beside it the bandwidth the users are using - the numbers must be real, reliable and live".)

  where it is read    the card of the tunnel on its Iran server - the GRE link of a Backhaul / GRE tunnel: the kernel's own counters (a BackPack Direct tunnel is NOT read from its L3 link bp<N>: that counts the headers of its
                      inner packets and the framing of its carrier, 20 % more than the users moved in a real test - its users' own connections are counted, see below)
                      (/sys/class/net/<card>/statistics), ONE light command per Iran server for all its cards (ssh.py `iface_counters`). A reverse proxy has no card (BackPack and Chisel over TCP):
                      its own TCP connections are counted instead - the byte counters the kernel keeps per socket (`ss -ti`: bytes_received / bytes_acked) of the connections on its control port (the
                      foreign server dialled in) or to it (forward). They include the framing of the tunnel (mux, WebSocket, TLS: a few percent) and what a short connection moved after the last
                      read is lost; a connection that is new counts what it has, one that is gone says nothing more. What has neither a card nor a TCP connection between its servers (UDP, QUIC, KCP,
                      raw sockets: Paqet, BackPack over UDP) is read from the USERS' own connections to its forwarded ports on the Iran server (the owner, 2026-10-17: "show the users' traffic in
                      every kind of tunnel"): the users connect over TCP whatever carries their data on, and the byte counters of those connections are exactly what the users move - what the Iran
                      server sends them is their download. A tunnel with no forwarded port has no counter: its cell says "-", never a made-up zero. For a card or a control connection, download is
                      what comes IN from the foreign server (what the users download), upload what goes out.
  how fast            every 5 s while somebody looks at the tunnels (the page asks GET /api/tunnels/live), every 30 s otherwise, so that the first number is there when the page opens.
  how it is counted   the rate over the last ~15 s (the newest read against the oldest one inside the window) on the SERVER's own uptime clock: steady, and still live. A counter that went back (the card
                      was made again) or a clock that did (a reboot) starts the window again; a rate no card can carry is dropped.
  what is left out    the panel's own tests of the link (`quiet`: the bandwidth test, the speed tests): their traffic is not the users'.

The pure helpers (`parse_counters`, `parse_socks`, `sock_step`, `push`, `rate`) carry the decisions and are tested."""
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
MAX_GAP = 45.0               # connections seen further apart than this (seconds of uptime) say nothing about the time between: the count starts again
PER_SERVER = 40              # cards (and connection specs) asked of one server in one command
MAX_USER_PORTS = 16          # ports of one tunnel whose users' connections are counted
CARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,14}")
SPEC = re.compile(r"s\d{1,5}|d\d{1,5}@\d{1,3}(\.\d{1,3}){3}|u\d{1,5}(,\d{1,5}){0,15}")
_LOCK = threading.Lock()
_READS: dict[tuple[int, str], deque] = {}          # (Iran server, card or "sock:<spec>") -> its last reads (uptime, rx_bytes, tx_bytes)
_SOCKS: dict[tuple[int, str], dict] = {}           # (Iran server, spec) -> {conns: the connections of the last read, acc: [sent, received] counted so far, up: the uptime of that read}
_LIVE: dict[int, dict] = {}                        # tunnel -> {down, up, span, at, via}: the rate of its card or connections and when it was read
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


def parse_socks(out: str) -> dict[str, dict[tuple[str, str], tuple[int, int]]]:
    """Pure: the connections in the answer of `iface_counters` ("SOCK <spec> <local> <remote> <bytes_acked> <bytes_received>"): {spec: {(local, remote): (bytes_acked, bytes_received)}}."""
    out_: dict[str, dict[tuple[str, str], tuple[int, int]]] = {}

    for ln in out.splitlines():
        p = ln.split()

        if len(p) == 6 and p[0] == "SOCK":
            try:
                out_.setdefault(p[1], {})[(p[2], p[3])] = (int(p[4]), int(p[5]))
            except ValueError:
                continue

    return out_


def sock_step(prev: dict | None, cur: dict) -> tuple[int, int]:
    """Pure: what the connections of one tunnel moved since the last read - (sent, received) bytes. A connection that was there grew by the difference; one that is new (it began since the last read) counts what it
    has; one whose counters went back is a new one; one that is gone says nothing more. `prev` None is the first sight: nothing is counted, there is no "since"."""
    if prev is None:
        return 0, 0

    tx = rx = 0

    for k, (a, r) in cur.items():
        pa, pr = prev.get(k, (0, 0))
        tx += a - pa if a >= pa else a
        rx += r - pr if r >= pr else r

    return tx, rx


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


def users_ports(t: Tunnel) -> list[int]:
    """The ports the USERS connect to on the Iran server of a tunnel - its forwarded ports. What the Iran server accepts there is the users' own connections, whatever carries them on to the foreign server
    (a mux, a WebSocket, KCP over raw sockets ...): their byte counters are what the users move."""
    ports = sorted({int(p) for p in (t.forwarded_ports or []) if str(p).isdigit() and 1 <= int(p) <= 65535})
    return ports[:MAX_USER_PORTS]


def sock_spec(t: Tunnel) -> str:
    """The TCP connections that tell what a tunnel with no card of its own carries, as the `iface_counters` script wants them. In this order:
    "s<port>" - the connections that came in on the control port of the Iran server (the foreign server dials in: BackPack and Chisel over TCP, reverse);
    "d<port>@<address>" - the ones the Iran server opened to the foreign server (a forward tunnel);
    "u<port>,<port>..." - the USERS' own connections to the forwarded ports of the Iran server: for what has no TCP connection between the servers (UDP, QUIC, KCP, raw sockets: Paqet, BackPack over UDP) - the
    users still connect over TCP to the Iran server, whatever carries their data on. "" when there is nothing to count (no forwarded port)."""
    rec = t.record or {}
    port = int(t.port or 0)
    udp = bool(rec.get("udp_carrier")) or str(t.transport or "").startswith("l3:")

    if t.type in ("backpack", "chisel") and 1 <= port <= 65535 and not udp:
        direction = rec.get("direction") or "reverse"

        if direction == "reverse":
            return f"s{port}"

        host = str(t.foreign.host) if direction == "forward" and t.foreign is not None else ""

        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host):
            return f"d{port}@{host}"

    ports = users_ports(t)
    return "u" + ",".join(str(p) for p in ports) if ports else ""


def targets() -> list[dict]:
    """The Iran servers to read, each with what to count of its tunnels: [{server_id, node, cards: {card: [tunnel ids]}, socks: {spec: [tunnel ids]}}] - the card of a tunnel that has one, else its TCP connections.
    A tunnel with neither, a test tunnel and a server that is off are left out."""
    out: dict[int, dict] = {}

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            if is_temp(t.name) or t.iran is None or not t.iran.enabled or t.iran.online is False:
                continue

            plan = plan_for(t)
            direct = t.type == "backpack" and str(t.transport or "").startswith("l3:")
            card = "" if direct and users_ports(t) else (plan.iface or plan.ping_iface)      # (the L3 link of a BackPack Direct tunnel counts the headers of its inner packets and the framing of its carrier - 20 % more than the users moved in a real test with the IP spoofing carrier: the users' own connections are exactly what they move)
            spec = "" if card else sock_spec(t)

            if card and not CARD.fullmatch(card):
                continue

            if not card and not (spec and SPEC.fullmatch(spec)):
                continue

            e = out.setdefault(t.iran_server_id, {"server_id": t.iran_server_id, "node": Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), "cards": {}, "socks": {}})
            (e["cards"].setdefault(card, []) if card else e["socks"].setdefault(spec, [])).append(t.id)

    for e in out.values():          # (one command asks for at most PER_SERVER of each: more would make the whole command refused)
        e["cards"] = dict(sorted(e["cards"].items())[:PER_SERVER])
        e["socks"] = dict(sorted(e["socks"].items())[:PER_SERVER])

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


def _sock_update(sid: int, spec: str, ids: list[int], cur: dict, up: float, at: float) -> None:
    """The connections of one tunnel as the last read saw them: what they moved since the read before goes into a running total (which only grows), and the total into the same window as a card's counters."""
    key = (sid, "sock:" + spec)
    reads = _READS.setdefault(key, deque())

    if any(_HOLD.get(i, 0.0) > at for i in ids):
        reads.clear()                          # (a test of the panel is on this tunnel: nothing of it is the users')
        _SOCKS.pop(key, None)
        return

    if not cur:                                # no connection with counters: the tunnel is not connected, or this kernel does not report them - there is no number to give
        _SOCKS.pop(key, None)
        reads.clear()
        return

    st = _SOCKS.get(key)

    if st is not None and not 0 < up - st["up"] <= MAX_GAP:
        st = None
        reads.clear()                          # (too long since the last look - or the server restarted: what is new now began somewhere in the gap)

    tx, rx = sock_step(st["conns"] if st else None, cur)
    acc = [(st["acc"][0] if st else 0) + tx, (st["acc"][1] if st else 0) + rx]
    _SOCKS[key] = {"conns": cur, "acc": acc, "up": up}
    users = spec.startswith("u")
    push(reads, up, *((acc[0], acc[1]) if users else (acc[1], acc[0])))      # (a control connection: what the Iran server RECEIVES from the foreign server is what the users download; a user's own connection: what it SENDS to the user is)
    r = rate(list(reads))

    if r is not None:
        for i in ids:
            _LIVE[i] = {**r, "at": at, "via": "users" if users else "connections"}


def _read(target: dict) -> None:
    """One read of one Iran server: its cards' counters and the connections of its tunnels go into their windows and the rates into `_LIVE`."""
    sid, cards, socks = target["server_id"], target["cards"], target.get("socks") or {}
    now = time.time()
    fails, until = _BACKOFF.get(sid, (0, 0.0))

    if now < until:
        return

    try:
        out = run_light(target["node"], "iface_counters", {"IFACES": " ".join(sorted(cards)), "SOCKS": " ".join(sorted(socks))}, timeout=TIMEOUT)
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
                _LIVE[i] = {**r, "at": at, "via": "link"}

    seen = parse_socks(out)

    for spec, ids in socks.items():
        _sock_update(sid, spec, ids, seen.get(spec, {}), up, at)


def _prune(todo: list[dict]) -> None:
    """What belongs to a tunnel or a server that is gone goes too."""
    keys = {(t["server_id"], c) for t in todo for c in t["cards"]} | {(t["server_id"], "sock:" + s) for t in todo for s in t.get("socks") or {}}
    ids = {i for t in todo for part in ("cards", "socks") for v in (t.get(part) or {}).values() for i in v}

    for d in (_READS, _SOCKS):
        for k in [k for k in d if k not in keys]:
            d.pop(k, None)

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
    """What the page gets: {tunnel id: {down, up, span, at, age, via}} for the tunnels read in the last two minutes (Mb/s; `age` in seconds; `via`: "link" = the card of the tunnel, "connections" = the TCP connections
    between its servers, "users" = the users' own connections to its ports)."""
    now = time.time()
    return {str(i): {**v, "age": round(now - v["at"], 1)} for i, v in list(_LIVE.items()) if now - v["at"] <= VIEW_FOR}
