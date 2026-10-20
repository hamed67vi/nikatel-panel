"""The CONFIG METER: how many bytes (and how many open connections) each PORT of a server carries, kept as hours for about two months. (The owner, 2026-10-21: "which configs are little used or falling" - a PasarGuard
panel records usage per user and per node, never per host or inbound, so the panel measures it itself.)

What it puts on a server: ONE nftables table `inet nikatel_meter` that only COUNTS. Its base chains (input, output, forward) have policy accept and no verdict other than `jump` into a chain per port that holds two
`counter` statements - no drop, no reject, no NAT, no mark. Nothing is restarted, no xray core is touched, no user is cut, no file is left on the disk and there is no boot unit: after a reboot the panel loads the
table again at its next turn. "Remove meters" deletes the table and that is all there is to undo.

Which packets are counted: the port is the port the USER dialled (`ct original proto-dst`: still right after a REDIRECT or a DNAT), counted ONCE where a packet enters the machine or is made on it -
  * input   packets of a connection the user began (`ct direction original`)     -> rx, bytes the users send (their upload)
  * output  replies the machine makes to such a connection (`ct direction reply`)   -> tx, bytes the users get (their download)
  * forward (only connections that were DNAT-ed to another machine, `ct status dnat`) both directions - an Iran server in front of a tunnel counts the port it forwards.
A connection the machine OPENS itself to some remote port (a node reaching a website on 443) is never counted as that port: its first packet is `original` in output, not in input, and its replies are `reply` in
input, not in output. The bytes are those of the IP packets (headers included, about 3 % over what the users moved). Both TCP and UDP.

The counters are cumulative and a reboot or a new table zeroes them, so only DIFFERENCES between two reads are stored (`step`): a counter that went back, a new boot id, a port new in the table all mean "count what
is there"; a gap between two reads is spread over the hours it covers (`spread`). The stored unit is the hour (`CfgBucket`); the page sums hours into 24 h / 7 d / 30 d and the 30 days before."""
from __future__ import annotations

import base64
import threading
import time
from collections.abc import Iterable

from sqlalchemy import select, text

from ..db import session
from ..log import get_logger
from ..models import CfgMeter, Server
from ..ssh import Node, run_action, run_script

log = get_logger("cfgmeter")
TABLE = "nikatel_meter"
MAX_PORTS = 300                         # ports of one server in the table
KEEP_DAYS = 65                          # hours older than this are deleted (a 30 days window and the 30 days before it need 60)
HOUR = 3600
DAY = 86400
TEHRAN = 12600                          # UTC+3:30, no daylight saving: a Tehran day begins at 20:30 UTC of the day before
MAX_SPREAD = 48 * HOUR                  # a gap between two reads is spread over at most this long
READ_TIMEOUT = 30
_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(server_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(server_id, threading.Lock())


# ------------------------------------------------------------------ pure: the ruleset
def clean_ports(ports) -> list[int]:
    """The ports as a sorted list of whole numbers 1-65535, no repeats; anything else is left out."""
    out: set[int] = set()

    for p in ports or []:
        try:
            n = int(p)
        except (TypeError, ValueError):
            continue

        if 1 <= n <= 65535 and not isinstance(p, bool):
            out.add(n)

    return sorted(out)


def ruleset(ports: Iterable[int]) -> str:
    """The nft file that makes the table (add, delete, new: one transaction, so it can be loaded again and again). No port = a table with nothing in it."""
    ps = clean_ports(ports)[:MAX_PORTS]
    lines = [f"add table inet {TABLE}", f"delete table inet {TABLE}", f"table inet {TABLE} {{"]

    for p in ps:
        lines += [f"  chain p{p} {{", '    ct direction original counter comment "rx"', '    ct direction reply counter comment "tx"', "  }"]

    if ps:
        vm = ", ".join(f"{p} : jump p{p}" for p in ps)
        key = "meta l4proto { tcp, udp } ct original proto-dst"
        lines += ["  chain c_in {", "    type filter hook input priority -5; policy accept;", f"    ct direction original {key} vmap {{ {vm} }}", "  }",
                  "  chain c_out {", "    type filter hook output priority -5; policy accept;", f"    ct direction reply {key} vmap {{ {vm} }}", "  }",
                  "  chain c_fwd {", "    type filter hook forward priority -5; policy accept;", f"    ct status dnat {key} vmap {{ {vm} }}", "  }"]

    lines.append("}")
    return "\n".join(lines) + "\n"


def b64(text_: str) -> str:
    return base64.b64encode(text_.encode()).decode()


# ------------------------------------------------------------------ pure: what the server answered
def parse_answer(out: str) -> dict:
    """The answer of `meter_read`, `meter_apply` or `meter_remove` as one dict: boot id, uptime, whether the table is there, the counters `c` ({port: [rx, tx]}; for apply / remove the counters of the OLD table are `old`),
    the ports with a listener, the open connections by port ("S": sockets, "T": conntrack), and the verdict of an apply / remove (`ok`, `why`). Lines it does not understand are skipped."""
    r: dict = {"boot": "", "up": None, "table": False, "c": {}, "old": {}, "listen": [], "s": {}, "t": {}, "ct": None, "done": False, "ok": None, "why": ""}
    in_old = False

    for ln in str(out or "").splitlines():
        p = ln.split()

        if not p:
            continue

        try:
            if p[0] == "BOOT" and len(p) >= 2:
                r["boot"] = p[1][:64]
            elif p[0] == "UP" and len(p) >= 2:
                r["up"] = float(p[1])
            elif p[0] == "TABLE" and len(p) >= 2:
                r["table"] = p[1] == "ok"
            elif p[0] == "CT" and len(p) >= 2:
                r["ct"] = p[1] == "yes"
            elif p[0] == "OLD":
                in_old = True
            elif p[0] == "ENDOLD":
                in_old = False
            elif p[0] == "C" and len(p) == 4 and p[2] in ("rx", "tx"):
                (r["old"] if in_old else r["c"]).setdefault(int(p[1]), [0, 0])[0 if p[2] == "rx" else 1] = int(p[3])
            elif p[0] == "L" and len(p) == 2:
                r["listen"].append(int(p[1]))
            elif p[0] in ("S", "T") and len(p) == 3:
                r["s" if p[0] == "S" else "t"][int(p[1])] = int(p[2])
            elif p[0] == "DONE":
                r["done"] = True
            elif p[0] in ("METER_ON", "METER_OFF"):
                r["ok"], r["why"] = True, " ".join(p[1:])[:200]
            elif p[0] == "METER_FAIL":
                r["ok"], r["why"] = False, " ".join(p[1:])[:200] or "the server refused"
        except ValueError:
            continue

    return r


def conns_of(read: dict, ports: Iterable[int]) -> dict[int, int]:
    """Open connections per port: the sockets of the listener when the machine listens on the port, else what conntrack says (a port that is forwarded to another machine has no listener here). A forwarded
    port on a machine that can say nothing about conntrack (`ct` is False) is LEFT OUT: no number, not a made-up zero."""
    lis = set(read.get("listen") or [])
    s, t = read.get("s") or {}, read.get("t") or {}
    out: dict[int, int] = {}

    for p in ports:
        if p in lis:
            out[p] = s.get(p, 0)
        elif read.get("ct") is not False:
            out[p] = t.get(p, s.get(p, 0))

    return out


# ------------------------------------------------------------------ pure: counters become differences, differences become hours
def step(last: dict | None, counters: dict[int, list[int]], boot: str, now: int) -> tuple[dict, dict[int, tuple[int, int]], int]:
    """(the new state, {port: (rx, tx) added since the last read}, the moment those bytes began to be counted).
    `last` is the state of the read before ({boot, ts, c}); None = the first read, which only sets the baseline (the table counts from the moment it was loaded, and the caller sets that baseline itself
    with c = {}: then everything counted so far is new). A counter that went back (the table was loaded again), a new boot id (the machine restarted) and a port that was not in the last read all mean
    "count what is there now"."""
    cur = {int(p): [int(v[0]), int(v[1])] for p, v in counters.items()}
    new = {"boot": boot, "ts": int(now), "c": cur}

    if not isinstance(last, dict) or not isinstance(last.get("c"), dict):
        return new, {}, int(now)

    rebooted = bool(boot) and bool(last.get("boot")) and boot != last["boot"]
    prev = {int(k): v for k, v in last["c"].items()}
    out: dict[int, tuple[int, int]] = {}

    for p, (rx, tx) in cur.items():
        a, b = prev.get(p, (0, 0)) if not rebooted else (0, 0)
        d = (rx - a if rx >= a else rx, tx - b if tx >= b else tx)

        if d[0] or d[1]:
            out[p] = d

    since = int(now) if rebooted else max(int(last.get("ts") or now), int(now) - MAX_SPREAD)
    return new, out, since


def spread(amount: int, start: int, end: int) -> list[tuple[int, int]]:
    """`amount` bytes counted between two reads put into the hours between them, in proportion to the seconds each hour has of the gap (the sum is exact). One read, or both in one hour: that hour."""
    if amount <= 0:
        return []

    end = max(int(end), int(start))
    h0, h1 = int(start) // HOUR * HOUR, end // HOUR * HOUR

    if h0 == h1 or end == int(start):
        return [(h1, int(amount))]

    parts = [(h, min(end, h + HOUR) - max(int(start), h)) for h in range(h0, h1 + HOUR, HOUR)]
    parts = [(h, w) for h, w in parts if w > 0]
    total = sum(w for _h, w in parts)
    out, left = [], int(amount)

    for i, (h, w) in enumerate(parts):
        a = left if i == len(parts) - 1 else int(amount) * w // total
        out.append((h, a))
        left -= a

    return out


def add(acc: dict, src: str, hour: int, rx: int = 0, tx: int = 0, conns: int | None = None) -> None:
    """Put bytes (and a connection sample) into the accumulator {(src, hour): [rx, tx, conn_max, conn_sum, n]} that `flush` writes."""
    e = acc.setdefault((src, int(hour)), [0, 0, 0, 0, 0])
    e[0] += max(0, int(rx))
    e[1] += max(0, int(tx))

    if conns is not None:
        e[2] = max(e[2], int(conns))
        e[3] += int(conns)
        e[4] += 1


_UPSERT = text("INSERT INTO cfg_buckets (src, hour, rx, tx, conn_max, conn_sum, n) VALUES (:src, :hour, :rx, :tx, :cm, :cs, :n) "
               "ON CONFLICT(src, hour) DO UPDATE SET rx = rx + excluded.rx, tx = tx + excluded.tx, conn_max = MAX(conn_max, excluded.conn_max), conn_sum = conn_sum + excluded.conn_sum, n = n + excluded.n")


def flush(acc: dict) -> int:
    """Write the accumulator: hours that exist are added to, never rewritten. Returns how many hours were written."""
    if not acc:
        return 0

    with session() as db:
        db.execute(_UPSERT, [{"src": s, "hour": h, "rx": v[0], "tx": v[1], "cm": v[2], "cs": v[3], "n": v[4]} for (s, h), v in acc.items()])

    return len(acc)


def src_of(server_id: int, port: int) -> str:
    return f"p:{int(server_id)}:{int(port)}"


def prune(now: int | None = None) -> int:
    """Hours older than KEEP_DAYS go."""
    cut = (int(now or time.time()) // HOUR) * HOUR - KEEP_DAYS * DAY

    with session() as db:
        return db.execute(text("DELETE FROM cfg_buckets WHERE hour < :c"), {"c": cut}).rowcount or 0


# ------------------------------------------------------------------ reading the hours back
WINDOWS = (("24h", 1), ("7d", 7), ("30d", 30))


def tehran_day(ts: int) -> int:
    """The number of the Tehran day a UTC moment falls on (days since the epoch, Tehran calendar)."""
    return (int(ts) + TEHRAN) // DAY


def stats(srcs: Iterable[str], now: int | None = None) -> dict[str, dict]:
    """For each source with any hour: bytes (rx + tx) of the last 24 hours / 7 days / 30 days and of the same-length window BEFORE each (`prev`), rx and tx of the three windows, the most connections seen at once
    in each window (`peak`, and how many connection samples it rests on: `samples`, 0 = the number was never read), the first and last hour on record (`first`, `last`) and the last 30 Tehran days as a list
    (`days`, oldest first, the last one is today so far). A window ends with the current hour."""
    keys = sorted({s for s in srcs if s})
    out: dict[str, dict] = {}

    if not keys:
        return out

    now = int(now or time.time())
    end = now // HOUR * HOUR + HOUR
    a = {k: end - d * DAY for k, d in (("24", 1), ("48", 2), ("7", 7), ("14", 14), ("30", 30), ("60", 60))}
    marks = ",".join(f":s{i}" for i in range(len(keys)))
    par = {f"s{i}": k for i, k in enumerate(keys)} | {f"a{k}": v for k, v in a.items()}

    def tot(lo, hi=None):
        return f"SUM(CASE WHEN hour >= :a{lo}" + (f" AND hour < :a{hi}" if hi else "") + " THEN rx + tx END)"

    q = (f"SELECT src, MIN(hour), MAX(hour), {tot('24')}, {tot('48', '24')}, {tot('7')}, {tot('14', '7')}, {tot('30')}, {tot('60', '30')}, "
         "SUM(CASE WHEN hour >= :a24 THEN rx END), SUM(CASE WHEN hour >= :a24 THEN tx END), SUM(CASE WHEN hour >= :a7 THEN rx END), SUM(CASE WHEN hour >= :a7 THEN tx END), "
         "SUM(CASE WHEN hour >= :a30 THEN rx END), SUM(CASE WHEN hour >= :a30 THEN tx END), "
         "MAX(CASE WHEN hour >= :a24 THEN conn_max END), MAX(CASE WHEN hour >= :a7 THEN conn_max END), MAX(CASE WHEN hour >= :a30 THEN conn_max END), "
         "SUM(CASE WHEN hour >= :a24 THEN n END), SUM(CASE WHEN hour >= :a7 THEN n END), SUM(CASE WHEN hour >= :a30 THEN n END) "
         f"FROM cfg_buckets WHERE src IN ({marks}) GROUP BY src")
    today = tehran_day(now)
    first_day = today - 29

    with session() as db:
        for r in db.execute(text(q), par):
            n = lambda v: int(v or 0)
            out[r[0]] = {"first": int(r[1]), "last": int(r[2]),
                         "bytes": {"24h": n(r[3]), "7d": n(r[5]), "30d": n(r[7])}, "prev": {"24h": n(r[4]), "7d": n(r[6]), "30d": n(r[8])},
                         "down": {"24h": n(r[10]), "7d": n(r[12]), "30d": n(r[14])}, "up": {"24h": n(r[9]), "7d": n(r[11]), "30d": n(r[13])},
                         "peak": {"24h": n(r[15]), "7d": n(r[16]), "30d": n(r[17])}, "samples": {"24h": n(r[18]), "7d": n(r[19]), "30d": n(r[20])}, "days": [0] * 30}

        rows = db.execute(text(f"SELECT src, (hour + {TEHRAN}) / {DAY} AS d, SUM(rx + tx) FROM cfg_buckets WHERE hour >= :lo AND src IN ({marks}) GROUP BY src, d"), par | {"lo": first_day * DAY - TEHRAN})

        for s, d, b in rows:
            i = int(d) - first_day

            if s in out and 0 <= i < 30:
                out[s]["days"][i] = int(b or 0)

    return out


def coverage_days(first: int | None, now: int | None = None) -> float:
    """How many days the record of a source reaches back (0 when there is none)."""
    if not first:
        return 0.0

    return max(0.0, ((int(now or time.time()) // HOUR * HOUR + HOUR) - int(first)) / DAY)


# ------------------------------------------------------------------ the servers
def _server(server_id: int) -> dict:
    with session() as db:
        s = db.get(Server, int(server_id))

        if s is None:
            raise ValueError("server not found")

        return {"id": s.id, "name": s.name, "host": s.host, "user": s.ssh_user, "port": s.ssh_port, "role": s.role, "enabled": bool(s.enabled)}


def _node(s: dict) -> Node:
    return Node(s["host"], s["user"], s["port"])


def get(server_id: int) -> dict:
    """The meter record of a server (an empty one when it was never switched on)."""
    with session() as db:
        m = db.get(CfgMeter, int(server_id))

        if m is None:
            return {"server_id": int(server_id), "enabled": False, "ports": [], "since": 0, "applied_at": 0, "last_ok": 0, "last_error": "", "state": {}}

        return {"server_id": m.server_id, "enabled": bool(m.enabled), "ports": list(m.ports or []), "since": m.since or 0, "applied_at": m.applied_at or 0,
                "last_ok": m.last_ok or 0, "last_error": m.last_error or "", "state": dict(m.state or {})}


def all_meters() -> dict[int, dict]:
    with session() as db:
        return {m.server_id: {"server_id": m.server_id, "enabled": bool(m.enabled), "ports": list(m.ports or []), "since": m.since or 0, "applied_at": m.applied_at or 0,
                              "last_ok": m.last_ok or 0, "last_error": m.last_error or ""} for m in db.scalars(select(CfgMeter))}


def _save(server_id: int, **fields) -> None:
    with session() as db:
        m = db.get(CfgMeter, int(server_id))

        if m is None:
            m = CfgMeter(server_id=int(server_id))
            db.add(m)

        for k, v in fields.items():
            setattr(m, k, v)


def _ingest(server_id: int, counters: dict[int, list[int]], boot: str, conns: dict[int, int] | None, now: int) -> dict:
    """Add what the counters say to the hours (and the connection samples to the hour of `now`), and keep the read as the state of the next step."""
    m = get(server_id)
    state, deltas, since = step(m["state"], counters, boot, now)
    acc: dict = {}

    for p, (rx, tx) in deltas.items():
        rx_parts, tx_parts = dict(spread(rx, since, now)), dict(spread(tx, since, now))

        for h in sorted(set(rx_parts) | set(tx_parts)):
            add(acc, src_of(server_id, p), h, rx_parts.get(h, 0), tx_parts.get(h, 0))

    for p, n in (conns or {}).items():
        add(acc, src_of(server_id, p), now // HOUR * HOUR, 0, 0, n)

    flush(acc)
    _save(server_id, state={"boot": boot, "ts": int(now), "c": {str(p): v for p, v in state["c"].items()}})
    return {"deltas": deltas, "hours": len(acc)}


def apply(server_id: int, ports: Iterable[int], now: int | None = None) -> dict:
    """Put the table on the server for these ports (or change the ports it counts). What the old table had counted is added to the hours first. Switches the meter ON in the record. RuntimeError with the
    reason when the server refuses or does not answer: then nothing is recorded as on."""
    ps = clean_ports(ports)[:MAX_PORTS]

    if not ps:
        raise ValueError("there is no port to count on this server")

    s = _server(server_id)
    now = int(now or time.time())

    with _lock(server_id):
        out = parse_answer(run_action(_node(s), "meter_apply", {"B64": b64(ruleset(ps))}, timeout=45))

        if out["ok"] is not True:
            _save(server_id, last_error=(out["why"] or "no answer from the meter script")[:300])
            raise RuntimeError(out["why"] or "the server did not confirm that the meter is on")

        if out["old"]:
            _ingest(server_id, out["old"], out["boot"], None, now)

        m = get(server_id)
        _save(server_id, enabled=True, ports=ps, since=m["since"] or now, applied_at=now, last_error="", state={"boot": out["boot"], "ts": now, "c": {}})

    log.info("meter on %s: %d port%s", s["name"], len(ps), "" if len(ps) == 1 else "s")
    return {"server": s["name"], "ports": ps}


def remove(server_id: int, now: int | None = None) -> dict:
    """Delete the table (what it had counted is added to the hours first) and switch the meter OFF in the record; the hours already stored stay. RuntimeError when the server does not confirm."""
    s = _server(server_id)
    now = int(now or time.time())

    with _lock(server_id):
        out = parse_answer(run_action(_node(s), "meter_remove", {}, timeout=40))

        if out["ok"] is not True:
            _save(server_id, last_error=(out["why"] or "no answer from the meter script")[:300])
            raise RuntimeError(out["why"] or "the server did not confirm that the table is gone")

        if out["old"]:
            _ingest(server_id, out["old"], out["boot"], None, now)

        _save(server_id, enabled=False, ports=[], last_error="", state={})

    log.info("meter off %s", s["name"])
    return {"server": s["name"]}


def tick(server_id: int, want: Iterable[int] | None = None, now: int | None = None) -> dict:
    """One turn for a server whose meter is on: read it; when its table is gone (a reboot) or the ports it should count changed, load it again; else add the difference to the hours.
    Returns {"ok", "action": read | applied | skipped, "error"}. Never raises for a server that does not answer: the record keeps the reason."""
    m = get(server_id)

    if not m["enabled"]:
        return {"ok": True, "action": "skipped", "error": ""}

    now = int(now or time.time())
    stored = clean_ports(m["ports"])
    ports = clean_ports(want) if want is not None else stored

    try:
        s = _server(server_id)

        with _lock(server_id):
            rd = parse_answer(run_script(_node(s), "meter_read", {"PORTS": " ".join(str(p) for p in stored)}, timeout=READ_TIMEOUT))

        if not rd["done"] and not rd["boot"]:
            raise RuntimeError("the meter script gave no answer")

        if not rd["table"]:
            apply(server_id, ports or stored, now)
            return {"ok": True, "action": "applied", "error": ""}

        with _lock(server_id):
            _ingest(server_id, rd["c"], rd["boot"], conns_of(rd, stored), now)
            _save(server_id, last_ok=now, last_error="")

        if ports and ports != stored:
            apply(server_id, ports, now)
            return {"ok": True, "action": "applied", "error": ""}

        return {"ok": True, "action": "read", "error": ""}
    except Exception as e:                                           # noqa: BLE001 - the boundary of one server's turn: another server's turn goes on
        msg = str(e)[:300] or e.__class__.__name__
        _save(server_id, last_error=msg)
        log.info("meter turn of server %s: %s", server_id, msg[:120])
        return {"ok": False, "action": "read", "error": msg}
