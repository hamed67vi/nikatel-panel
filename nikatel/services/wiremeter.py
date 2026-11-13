"""THE WIRE METER: what every tunnel costs on the wire against what its users move, measured on the real traffic and kept for 24 hours (the owner, 2026-11-13: "check all the tunnels: is every one 1:1, is there
extra overhead?" - the panel had no such number: the meter per port is off on every server, and the speed tests say what a TYPE costs in a test, not what this tunnel costs in use).

  the two sides   USERS     the byte counters of the users' own TCP connections to the forwarded ports of the Iran server (`ss -ti`: bytes_acked + bytes_received, both directions) - what the users moved.
                  TUNNEL    a GRE-type link (Backhaul): the kernel's counters of its card plus the header the link adds PER PACKET (GRE 24, GUE 32 ...: the packet counters say how many); a reverse proxy
                            (BackPack, Chisel over TCP): the byte counters of its own TCP connections between the two servers (`livebw.sock_spec`: the framing of a mux, a WebSocket or TLS is in them).
                            A tunnel that only has a UDP / KCP / raw-socket carrier has no counter on its tunnel side: it is left out (the model of the type stays the only number).
  how             every 10 minutes, one Iran server at a time per worker: three reads 8 s apart (`wire_counters`, one light command each); a connection that is new since the last read counts what it has
                  (`livebw.sock_step`), one that is gone says nothing more, a counter that went back or a clock that did not grow drops that step. 3 servers at once.
  what is kept    a ring of [time, users bytes, wire bytes] per tunnel for 24 hours (the settings document `wiremeter`) and the summary in `tunnel.record["wire"]`: ratio = all the wire bytes over all
                  the users' bytes of the ring - never an average of ratios (a quiet read would count as much as a busy one) - and None until the users moved 50 MB (`MIN_USERS`).
  what it is not  an exact number: the connections that opened and closed between two reads are lost (on the users' side mostly: the ratio is then a little HIGH, never low), the inner headers and the
                  acknowledgements of the users' own TCP are in a link's card (3 - 8 % in every tunnel, whatever the type), and what the users send over UDP is not counted at all. It TELLS: no decision of the Doctor reads it.

The pure helpers (`parse`, `totals`, `add_sample`, `summary`) carry the decisions and are tested."""
from __future__ import annotations

import itertools
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel, is_standby, is_temp
from ..ssh import Node, SSHError, run_light
from . import livebw, overhead
from . import settings as st
from .tunnels import plan_for

log = get_logger("wiremeter")
EVERY = 600                  # seconds between two rounds (the job is registered in main.py)
READS = 3                    # reads of one server in a round ...
GAP = 8.0                    # ... this many seconds apart
TIMEOUT = 12                 # one read of one server
WORKERS = 3                  # servers read at once
KEEP = 24 * 3600             # the ring keeps this long
MAX_RING = 160               # and at most this many samples (a round every 10 minutes: 144 a day)
MIN_USERS = 50_000_000       # a ratio is given when the users moved at least this many bytes in the ring: under it, one quiet read says nothing
HEADER = {"gre": 24, "gue": 32, "fou": 28, "ipip": 20, "ipipfou": 28, "ipipgue": 36}      # the bytes a link adds to every packet (outer IP 20, GRE 4, UDP 8, GUE 4 ...); a link that is not here is not measured


def side_of(t: Tunnel) -> tuple | None:
    """The tunnel's own side: ("card", card, header bytes per packet) for a GRE-type link, ("sock", spec) for a reverse proxy over TCP, None when nothing counts it (a UDP / KCP / raw carrier, a BackPack Direct tunnel
    whose L3 link counts the headers of its inner packets, a link whose header is not known)."""
    if t.type == "backpack" and str(t.transport or "").startswith("l3:"):
        return None

    card = plan_for(t).iface
    hdr = HEADER.get((t.record or {}).get("link") or "gre")

    if card and hdr and t.type in ("backhaul", "gre") and livebw.CARD.fullmatch(card):
        return ("card", card, hdr)

    spec = livebw.sock_spec(t)

    if spec[:1] in ("s", "d") and livebw.SPEC.fullmatch(spec):
        return ("sock", spec)

    return None


def plan() -> list[dict]:
    """[{server_id, node, tunnels: [{id, users, side, kind}]}] - the tunnels that run, on their Iran server, with the connections of their users and the side of the tunnel that counts them."""
    from . import autopilot

    out: dict[int, dict] = {}

    with session() as db:
        for t in db.scalars(select(Tunnel)):
            if not t.enabled or is_standby(t) or is_temp(t.name) or t.iran is None or not t.iran.enabled or t.iran.online is False:
                continue

            ports = livebw.users_ports(t)
            side = side_of(t)

            if not ports or side is None:
                continue

            e = out.setdefault(t.iran_server_id, {"server_id": t.iran_server_id, "node": Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), "tunnels": []})
            e["tunnels"].append({"id": t.id, "users": "u" + ",".join(str(p) for p in ports), "side": side, "kind": autopilot.kind_of(t.type, t.transport or "", t.record or {}), "name": t.name})

    return list(out.values())


def parse(out: str) -> dict | None:
    """Pure: one read - {up, cards: {card: (rx, tx)}, pk: {card: (rx_packets, tx_packets)}, socks: {spec: {(local, remote): (acked, received)}}}; None when the answer has no clock."""
    up, cards = livebw.parse_counters(out)

    if up is None:
        return None

    pk: dict[str, tuple[int, int]] = {}

    for ln in out.splitlines():
        p = ln.split()

        if len(p) == 4 and p[0] == "PK":
            try:
                pk[p[1]] = (int(p[2]), int(p[3]))
            except ValueError:
                continue

    return {"up": up, "cards": cards, "pk": pk, "socks": livebw.parse_socks(out)}


def _step(a: dict, b: dict, spec: str) -> int:
    """What the connections of one spec moved between two reads, both directions (a read that does not list the spec had no such connection: a new one counts what it has)."""
    tx, rx = livebw.sock_step(a["socks"].get(spec, {}), b["socks"].get(spec, {}))
    return tx + rx


def _wire_step(a: dict, b: dict, side: tuple) -> int | None:
    """What the tunnel's own side moved between two reads, on the wire; None when this step cannot be counted (the card is not in a read, or a counter went back)."""
    if side[0] == "sock":
        return _step(a, b, side[1])

    card, hdr = side[1], side[2]
    ra, rb, pa, pb = a["cards"].get(card), b["cards"].get(card), a["pk"].get(card), b["pk"].get(card)

    if not (ra and rb and pa and pb) or min(rb[0] - ra[0], rb[1] - ra[1], pb[0] - pa[0], pb[1] - pa[1]) < 0:
        return None

    return (rb[0] - ra[0]) + (rb[1] - ra[1]) + hdr * ((pb[0] - pa[0]) + (pb[1] - pa[1]))


def totals(tunnels: list[dict], reads: list[dict]) -> dict[int, dict]:
    """Pure: {tunnel id: {users, wire, span}} - the bytes the users moved and the bytes the tunnel moved on the wire over all the steps between the reads that can be counted. A step whose clock did not
    grow (the server restarted) or whose tunnel side cannot be read is dropped for that tunnel, users and wire alike: the two sides always cover the same time."""
    out: dict[int, dict] = {}

    for t in tunnels:
        users = wire = 0
        span = 0.0

        for a, b in itertools.pairwise(reads):
            if b["up"] <= a["up"]:
                continue

            w = _wire_step(a, b, t["side"])

            if w is None:
                continue

            users += _step(a, b, t["users"])
            wire += w
            span += b["up"] - a["up"]

        if span > 0:
            out[t["id"]] = {"users": users, "wire": wire, "span": round(span, 1)}

    return out


def add_sample(ring: list | None, now: float, users: int, wire: int) -> list:
    """Pure: the ring with the new sample [time, users bytes, wire bytes] added, what is older than KEEP gone, at most MAX_RING kept (a sample that moved nothing is not kept)."""
    out = [r for r in (ring or []) if isinstance(r, list) and len(r) == 3 and now - r[0] <= KEEP]

    if users > 0 or wire > 0:
        out.append([int(now), int(users), int(wire)])

    return out[-MAX_RING:]


def summary(ring: list | None, now: float, kind: str, hdr: int | None = None) -> dict:
    """Pure: what `tunnel.record["wire"]` says - the ratio of all the wire bytes to all the users' bytes of the last 24 hours (None until the users moved MIN_USERS), the volumes, and the cost the design of
    the type gives (`overhead.model_cost`) to read it against."""
    rows = [r for r in (ring or []) if now - r[0] <= KEEP]
    users, wire = sum(r[1] for r in rows), sum(r[2] for r in rows)
    ratio = round(wire / users, 2) if users >= MIN_USERS else None
    return {"at": int(now), "ratio": ratio, "users_gb": round(users / 1e9, 2), "wire_gb": round(wire / 1e9, 2), "samples": len(rows), "kind": kind, "model": overhead.model_cost(kind), "hdr": hdr}


def measure(entry: dict, reads: int = READS, gap: float = GAP) -> dict[int, dict]:
    """One Iran server: `reads` reads `gap` seconds apart, then the totals of its tunnels. A read that fails ends the round for this server (what was read until then is still counted)."""
    tunnels = entry["tunnels"]
    cards = sorted({t["side"][1] for t in tunnels if t["side"][0] == "card"})
    socks = sorted({t["users"] for t in tunnels} | {t["side"][1] for t in tunnels if t["side"][0] == "sock"})[: livebw.PER_SERVER]
    got: list[dict] = []

    for i in range(reads):
        if i:
            time.sleep(gap)

        try:
            one = parse(run_light(entry["node"], "wire_counters", {"IFACES": " ".join(cards[: livebw.PER_SERVER]), "SOCKS": " ".join(socks)}, timeout=TIMEOUT))
        except (SSHError, ValueError) as e:
            log.debug("wire meter: server %s not read (%s)", entry["server_id"], str(e)[:100])
            break

        if one is None:
            break

        got.append(one)

    return totals(tunnels, got)


def _safe_measure(entry: dict) -> dict[int, dict]:
    try:
        return measure(entry)
    except Exception:
        log.exception("wire meter: server %s failed", entry.get("server_id"))
        return {}


def store(todo: list[dict], results: dict[int, dict], now: float | None = None) -> None:
    """Add the round to the rings (the settings document `wiremeter`) and write each tunnel's summary into its record."""
    now = now or time.time()
    kinds = {t["id"]: (t["kind"], t["side"][2] if t["side"][0] == "card" else None) for e in todo for t in e["tunnels"]}

    with session() as db:
        alive = {str(i) for i in db.scalars(select(Tunnel.id))}

    rings = {k: v for k, v in (st.get("wiremeter").get("rings") or {}).items() if k in alive}

    for tid, r in results.items():
        rings[str(tid)] = add_sample(rings.get(str(tid)), now, r["users"], r["wire"])

    st.put("wiremeter", {"rings": rings})

    with session() as db:
        for tid, (kind, hdr) in kinds.items():
            ring = rings.get(str(tid))
            t = db.get(Tunnel, tid) if ring is not None else None

            if t is not None:
                rec = dict(t.record or {})
                rec["wire"] = summary(ring, now, kind, hdr)
                t.record = rec


def run() -> None:
    """Job (every EVERY seconds): read the Iran servers that carry measurable tunnels, a few at a time, and keep what they say. Nothing is changed on any server."""
    if get_settings().read_only or jobs.stopping():
        return

    from . import autopilot

    if autopilot.quiet_now():
        return                              # (a failover is on: the servers have enough to do)

    todo = plan()

    if not todo:
        return

    results: dict[int, dict] = {}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for part in ex.map(_safe_measure, todo):
            results.update(part)

    if results and not jobs.stopping():
        store(todo, results)
