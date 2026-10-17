"""What the Doctor KNOWS about every pair of servers: which tunnel types work between an Iran server and a foreign server, and how well - kept, so that when something breaks it already
knows what to build instead of testing a fixed list in the middle of the outage.
(The owner, 2026-10-16: "the Doctor must always be up to date: any tunnel added, on any disruption it knows by itself what tunnel to build in any situation between servers; it runs its tests
in all transports; the first priority is uptime and a high speed for every user".)

  what is kept         per pair (an Iran server and a foreign server), per tunnel kind (the 24 kinds of the tunnel test): the last test - did it work, how well, why not, when - and the last
                       FULL measurement (score, latency, jitter, loss, the download with eight connections and with one, the upload). Settings key `knowledge`.
  where it comes from  every pair test the panel runs (the Tunnel test page, the Doctor's own tests while it repairs, the Create flow), the quality test of every live tunnel with a test door,
                       and the NIGHT SURVEY: one pair at a time, every kind that is due, on the pairs that carry a tunnel.
  how it is used       on a disruption the Doctor tries the kinds known to work between THIS pair first, best first (what a customer downloads counts most), leaves out those that failed here
                       lately and stops at the first that works (`plan_for`); a pair nobody tested yet gets the standard order (`PRIOR`: what the real tests said).
  the night survey     `run` (job, every ten minutes): when it is on, the local time is inside the night window, no repair or test runs, no server is busy and the tunnels of the pair carry
                       little, it starts the survey of the pair with the least knowledge; it stops when the window closes or a repair needs the place for its own tests (`yield_slot`).

The pure helpers (`merge`, `verdict`, `ranked`, `blocked`, `poor`, `due_kinds`, `candidates`, `in_window`) carry the decisions and are tested."""
from __future__ import annotations

import threading
import time

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, ServerSample, Tunnel, is_temp
from . import settings as st
from .events import emit

log = get_logger("knowledge")
FULL_FRESH = 30 * 86400         # a full measurement older than this is only a hint (the numbers of the day, not of the month)
REFRESH = 7 * 86400             # a kind that worked is tested again after this
FAIL_FRESH = 14 * 86400         # a kind that did not work is left alone this long (and the Doctor does not try it in the middle of an outage)
QUIET_MBPS = 150.0              # the tunnels of the two servers carry less than this together (the night survey only asks a few pings of a temporary tunnel, no speed test: a survey hardly touches the users)
BUSY_CPU = 60.0                 # a server this busy is not surveyed
LIMIT = 5                       # the kinds the Doctor tries live at most
SURVEYS = ("survey", "survey-manual", "survey-cold")      # survey-cold: the first survey of a pair nobody has tested, by day too (see COLD_QUIET_MBPS)
COLD_QUIET_MBPS = 30.0          # a pair that has never been surveyed is surveyed outside the night window as well, when the tunnels of its servers carry less than this: without what the Doctor knows about a pair, a cut on it
                                # is repaired by trying the tunnel types one by one - the longest part of a repair (the owner, 2026-10-17: "bring the delay down to the minimum")
GRE_KINDS = ("tcpmux", "tcp", "wsmux", "wssmux")          # the Backhaul kinds: they need GRE (protocol 47) to pass between the two servers
# What the real tests said (docs/tunnels.md, 2026-10-16): the side that DIALS decides what a customer downloads, so the kinds where the Iran server dials come first; the kinds where the foreign
# server dials (reverse - they are the way when the Iran server cannot dial out) follow; the odd ones are last. It is the order for a pair nobody tested yet, and the tie-break.
PRIOR = ["tcpmux", "bpd:pck", "chisel:fwd", "paqet", "bpd:udp", "chisel:fwdtls", "bp:wssmux", "bp:tcpmux", "chisel:tls", "bp:stealth", "bp:wss", "bp:tcp",
         "wssmux", "tcp", "wsmux", "bp:pck", "chisel", "bp:ws", "bp:wsmux", "bp:kcp", "bpd:quic", "bp:quic", "bpd:xdi", "bp:xdi"]
# What the night survey tests on every pair that carries a tunnel (about twelve minutes a pair): the kinds the Doctor reaches for when a route is cut, the robust ones first. The other kinds are tested when
# the owner asks (Doctor page, Knowledge) or by a repair that needs them.
READY = ["bp:wssmux", "chisel:tls", "bp:tcpmux", "bpd:pck", "chisel:fwd", "paqet", "bp:wss", "chisel:fwdtls", "bp:tcp", "bpd:udp"]
NUMS = ("score", "latency_ms", "jitter_ms", "loss_pct", "down_mbps", "up_mbps", "down1_mbps", "up1_mbps")
INCIDENTS = 200             # the cuts that are kept (the newest)
PLAYBOOK_DAYS = 90          # what healed a pair this long ago is still the first thing tried
_LOCK = threading.Lock()


# ------------------------------------------------------------------ pure helpers (tested)
def pair_key(iran_id: int, foreign_id: int) -> str:
    return f"{int(iran_id)}:{int(foreign_id)}"


def merge(entry: dict | None, r: dict, measure: str, now: int) -> dict:
    """The entry of one kind after one test. `r` is a row of a pair test (kind, ok, score, latency_ms ..., error / verdict); `measure`: "full" (speed included) or "latency"."""
    e = dict(entry or {})
    ok, measured = bool(r.get("ok")), r.get("score") is not None or bool(r.get("works"))          # "works": it was built and carried data (a real build checks that) though nobody scored it
    e["last"] = {"ok": ok, "measured": measured, "at": now, "measure": measure, "score": r.get("score"), "error": "" if ok or measured else str(r.get("error") or r.get("verdict") or "")[:140]}

    if measured:
        e["full" if measure == "full" else "lat"] = {**{k: r.get(k) for k in NUMS}, "at": now}

    return e


def verdict(e: dict | None) -> str:
    """"good" (the last test passed), "poor" (it carried data but scored under the line), "failed" (it did not work), "" (never tested)."""
    last = (e or {}).get("last")

    if not last:
        return ""

    if last.get("ok"):
        return "good"

    return "poor" if last.get("measured") else "failed"


def ranked(pair: dict | None, now: int, include_poor: bool = False) -> list[dict]:
    """The kinds that work on the pair, best first: those with a fresh FULL measurement before those known by latency only, each by score (a tie by the standard order).
    `include_poor`: the kinds that carried data but scored under the line count too (for a tunnel that is CUT, any tunnel that connects is better than none)."""
    out = []

    for kind, e in ((pair or {}).get("kinds") or {}).items():
        if verdict(e) not in (("good", "poor") if include_poor else ("good",)):
            continue

        full = e.get("full") or {}
        fresh = full.get("score") is not None and now - int(full.get("at") or 0) < FULL_FRESH
        src = full if fresh else (e.get("lat") or full)
        out.append({"kind": kind, "score": src.get("score"), "measure": "full" if fresh else "latency", "at": int(src.get("at") or 0), "down_mbps": src.get("down_mbps"),
                    "latency_ms": src.get("latency_ms"), "loss_pct": src.get("loss_pct")})

    return sorted(out, key=lambda r: (r["measure"] != "full", -(r["score"] or 0), PRIOR.index(r["kind"]) if r["kind"] in PRIOR else 99))


def blocked(pair: dict | None, now: int) -> list[str]:
    """The kinds that did not work on the pair lately (a test that failed less than FAIL_FRESH ago)."""
    return sorted(k for k, e in ((pair or {}).get("kinds") or {}).items() if verdict(e) == "failed" and now - int(e["last"].get("at") or 0) < FAIL_FRESH)


def poor(pair: dict | None, now: int) -> list[str]:
    """The kinds that carried data on the pair but scored under the line, tested less than FULL_FRESH ago."""
    return sorted(k for k, e in ((pair or {}).get("kinds") or {}).items() if verdict(e) == "poor" and now - int(e["last"].get("at") or 0) < FULL_FRESH)


def due_kinds(pair: dict | None, kinds: list[str], now: int, refresh: int = REFRESH) -> list[str]:
    """The kinds (in the order given) whose knowledge is missing or old: never tested; tested more than `refresh` ago; one that failed, more than FAIL_FRESH ago."""
    have = (pair or {}).get("kinds") or {}
    out = []

    for k in kinds:
        last = (have.get(k) or {}).get("last")

        if not last or now - int(last.get("at") or 0) >= (FAIL_FRESH if verdict(have[k]) == "failed" else refresh):
            out.append(k)

    return out


REVERSE_KINDS = frozenset({"bp:tcpmux", "bp:tcp", "bp:stealth", "bp:wss", "bp:wssmux", "bp:ws", "bp:wsmux", "bp:kcp", "bp:quic", "bp:pck", "bp:xdi", "chisel", "chisel:tls"})      # the FOREIGN server dials the Iran server
FORWARD_KINDS = frozenset({"bpd:udp", "bpd:quic", "bpd:pck", "bpd:xdi", "paqet", "chisel:fwd", "chisel:fwdtls"})                                                                   # the IRAN server dials the foreign server
# For a tunnel that is CUT the first thing is to connect, so the order is by robustness against filtering, not by speed: TLS WebSocket kinds look like HTTPS, the foreign server dialling in works when the
# foreign address is filtered from Iran (only the connections that START in Iran are cut), plain kinds next, UDP / raw / ICMP kinds last.
CUT_REVERSE = ["bp:wssmux", "chisel:tls", "bp:tcpmux", "bp:wss", "bp:tcp", "chisel", "bp:stealth", "bp:wsmux", "bp:ws", "bp:kcp", "bp:pck", "bp:quic", "bp:xdi"]
CUT_FORWARD = ["bpd:pck", "chisel:fwd", "paqet", "chisel:fwdtls", "bpd:udp", "bpd:quic", "bpd:xdi"]
CUT_GRE = ["tcpmux", "wssmux", "tcp", "wsmux"]


def cut_order(dial_i: bool | None, dial_f: bool | None, no_gre: bool) -> list[str]:
    """The order in which a CUT tunnel's replacements are tried (see CUT_REVERSE). `dial_i` / `dial_f`: can the Iran / the foreign server open a TCP connection to the other one (None = not known).
    When only the foreign server can dial (the foreign address is filtered from Iran) the reverse kinds come first, when the Iran server can dial the forward kinds (faster) come first."""
    gre = [] if no_gre else CUT_GRE

    if dial_i is False and dial_f is not False:
        return gre + CUT_REVERSE + CUT_FORWARD

    if dial_i:
        return gre + CUT_FORWARD + CUT_REVERSE

    return gre + CUT_REVERSE + CUT_FORWARD


def unreachable(dial_i: bool | None, dial_f: bool | None) -> frozenset:
    """The kinds that cannot connect because of who cannot dial (known only when the test said False)."""
    return (FORWARD_KINDS if dial_i is False else frozenset()) | (REVERSE_KINDS if dial_f is False else frozenset())


def candidates(pair: dict | None, current: str, no_gre: bool, lossy: bool, now: int, limit: int = LIMIT, base: list[str] | None = None, unreachable_kinds: frozenset = frozenset(),
               prefer: list[str] | None = None) -> list[str]:
    """The kinds the Doctor tries live when it must switch a tunnel, in the order it tries them: what is known to connect on this pair (best first), then what nobody tried yet (the order of `base`);
    what the facts of the moment say cannot connect (a server that cannot dial) goes to the end; what failed here lately only when nothing else is left. For a tunnel that is CUT (`lossy` False) the
    kinds that carried data but scored poorly count as known to connect. The tunnel's own kind is left out unless it is only lossy (a fresh build of it may be better)."""
    allowed = [k for k in (base or PRIOR) if not (no_gre and k in GRE_KINDS) and (lossy or k != current)]
    good = [r["kind"] for r in ranked(pair, now, include_poor=not lossy) if r["kind"] in allowed]
    skip = set(blocked(pair, now)) | (set(poor(pair, now)) if lossy else set())
    unknown = [k for k in allowed if k not in good and k not in skip]
    unknown = [k for k in (prefer or []) if k in unknown] + [k for k in unknown if k not in (prefer or [])]            # (what the other pairs of the same server connect with comes before what nobody tried)
    ordered = good + unknown
    ordered = [k for k in ordered if k not in unreachable_kinds] + [k for k in ordered if k in unreachable_kinds]

    return ordered[:limit] or [k for k in allowed if k in skip][:limit]


def recent_failure(pair: dict | None, kinds: tuple | list, now: int, within: int = 1800) -> bool:
    """Did any of these kinds fail on the pair less than `within` seconds ago (a new GRE link was tested a minute ago and did not pass: it is not tested again at once)?"""
    have = (pair or {}).get("kinds") or {}
    return any(verdict(have.get(k)) == "failed" and now - int((have[k].get("last") or {}).get("at") or 0) < within for k in kinds)


def in_window(now: int, tz_minutes: int, from_h: int, to_h: int) -> bool:
    """Is the local hour inside the night window [from_h, to_h)? The window may wrap past midnight; equal hours mean any time."""
    if from_h == to_h:
        return True

    h = ((now + tz_minutes * 60) // 3600) % 24
    return from_h <= h < to_h if from_h < to_h else (h >= from_h or h < to_h)


# ------------------------------------------------------------------ what is kept
def _doc() -> dict:
    d = st.get("knowledge")
    d["pairs"] = dict(d.get("pairs") or {})
    return d


def pair(iran_id: int, foreign_id: int) -> dict:
    return dict(_doc()["pairs"].get(pair_key(iran_id, foreign_id)) or {})


def record(iran_id: int, foreign_id: int, r: dict, measure: str, now: int | None = None) -> None:
    """Keep one result of a pair test (a row of a compare job)."""
    if r.get("kind") not in PRIOR:
        return

    now = now or int(time.time())

    with _LOCK:
        d = _doc()
        k = pair_key(iran_id, foreign_id)
        p = dict(d["pairs"].get(k) or {})
        kinds = dict(p.get("kinds") or {})
        kinds[r["kind"]] = merge(kinds.get(r["kind"]), r, measure, now)
        d["pairs"][k] = {"at": now, "kinds": kinds}
        st.put("knowledge", {"pairs": d["pairs"]})


def note_quality(tunnel_id: int, q: dict) -> None:
    """The quality test of a live tunnel with a test door measured it with eight connections: that is a FULL measurement of its kind on its pair, for nothing."""
    from . import autopilot

    if q.get("score") is None or q.get("error"):
        return

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None or is_temp(t.name):
            return

        kind, iran_id, foreign_id = autopilot.kind_of(t.type, t.transport, t.record or {}), t.iran_server_id, t.foreign_server_id

    record(iran_id, foreign_id, {"kind": kind, "ok": int(q["score"]) >= 50, **{k: q.get(k) for k in NUMS}}, "full")


def forget_server(server_id: int) -> None:
    """A server that was removed takes its pairs with it."""
    with _LOCK:
        d = _doc()
        keep = {k: v for k, v in d["pairs"].items() if str(server_id) not in k.split(":")}

        if len(keep) != len(d["pairs"]):
            st.put("knowledge", {"pairs": keep})


# ------------------------------------------------------------------ what the Doctor remembers of every cut
def remember(iran_id: int, foreign_id: int, cause: str, tunnel: str, steps: list[dict], healed: bool, secs: int, now: int | None = None) -> None:
    """One cut, kept (the owner, 2026-10-16: "when a tunnel is cut it must find out why, and keep it in its memory so that the next time it knows what to do"): the cause the Doctor found, the steps it
    tried in order (each with the tunnel type it built), what healed it and how long that took."""
    now = now or int(time.time())
    done = [{"step": s.get("step"), "ok": bool(s.get("ok")), **({"kind": s["kind"]} if s.get("kind") else {})} for s in steps][:12]
    fix = next((s for s in reversed(done) if s["ok"]), None) if healed else None
    entry = {"at": now, "tunnel": tunnel, "pair": pair_key(iran_id, foreign_id), "cause": cause, "steps": done, "healed": bool(healed), "secs": int(secs), "fix": (fix or {}).get("step", ""), "kind": (fix or {}).get("kind", "")}

    with _LOCK:
        d = _doc()
        st.put("knowledge", {"incidents": (list(d.get("incidents") or []) + [entry])[-INCIDENTS:]})


def playbook(iran_id: int, foreign_id: int, cause: str, now: int | None = None) -> dict:
    """What healed this pair the last time this cause came up: {"kind", "step", "at", "n" (how many times that kind healed it), "secs"}; {} when nothing did (within PLAYBOOK_DAYS)."""
    now = now or int(time.time())
    key = pair_key(iran_id, foreign_id)
    rows = [e for e in reversed(list(_doc().get("incidents") or [])) if e.get("pair") == key and e.get("cause") == cause and e.get("healed") and e.get("kind") and now - int(e.get("at") or 0) <= PLAYBOOK_DAYS * 86400]

    if not rows:
        return {}

    return {"kind": rows[0]["kind"], "step": rows[0].get("fix", ""), "at": int(rows[0]["at"]), "n": sum(1 for e in rows if e["kind"] == rows[0]["kind"]), "secs": int(rows[0].get("secs") or 0)}


def cut_before(iran_id: int, foreign_id: int, causes: tuple | list, now: int | None = None, days: int = 14) -> bool:
    """Was this pair cut for one of these causes in the last `days` days? (A pair whose route was filtered a week ago needs no restart tried first: the Doctor goes straight to the type that connected.)"""
    now = now or int(time.time())
    key = pair_key(iran_id, foreign_id)
    return any(e.get("pair") == key and e.get("cause") in causes and now - int(e.get("at") or 0) <= days * 86400 for e in (_doc().get("incidents") or []))


def repeats(iran_id: int, foreign_id: int, now: int | None = None, days: int = 7) -> int:
    """How many times this pair was cut in the last `days` days (a tunnel that is cut again and again is the one that gets a standby)."""
    now = now or int(time.time())
    key = pair_key(iran_id, foreign_id)
    return sum(1 for e in (_doc().get("incidents") or []) if e.get("pair") == key and now - int(e.get("at") or 0) <= days * 86400)


def sibling_kinds(iran_id: int, foreign_id: int, now: int | None = None) -> list[str]:
    """The kinds that connect on the OTHER pairs of the same foreign server, most pairs first: an address that is filtered from Iran is filtered for every Iran server, so what works for its neighbours is
    the best guess for a pair nobody tested."""
    count: dict[str, int] = {}

    for key, p in _doc()["pairs"].items():
        i, f = (int(x) for x in key.split(":"))

        if f != foreign_id or i == iran_id:
            continue

        for kind, e in (p.get("kinds") or {}).items():
            if verdict(e) in ("good", "poor"):
                count[kind] = count.get(kind, 0) + 1

    return sorted(count, key=lambda k: (-count[k], PRIOR.index(k) if k in PRIOR else 99))


def configure(on: bool | None = None, from_hour: int | None = None, to_hour: int | None = None) -> dict:
    changes: dict = {}

    if on is not None:
        changes["on"] = bool(on)

    for key, v in (("from_hour", from_hour), ("to_hour", to_hour)):
        if v is not None:
            if not 0 <= int(v) <= 23:
                raise ValueError("an hour is from 0 to 23")

            changes[key] = int(v)

    if changes:
        st.put("knowledge", changes)

    d = st.get("knowledge")
    return {"on": bool(d["on"]), "from_hour": int(d["from_hour"]), "to_hour": int(d["to_hour"])}


# ------------------------------------------------------------------ how the Doctor uses it
def plan_for(iran_id: int, foreign_id: int, current: str, no_gre: bool, lossy: bool, now: int | None = None, dial_i: bool | None = None, dial_f: bool | None = None, cause: str = "") -> tuple[list[str], list[str]]:
    """(the kinds to try, the lines for the story of the repair): what healed this pair the last time this cause came up (the playbook), what is known about THIS pair, what connects on the other pairs of the
    same foreign server and - for a tunnel that is cut - who can still dial whom, decide the order. A tunnel that is cut tries EVERY kind in its order until one connects (the first that works stays);
    one that still carries data tries a few and is swapped only for something clearly better."""
    now = now or int(time.time())
    p = pair(iran_id, foreign_id)
    base = None if lossy else cut_order(dial_i, dial_f, no_gre)
    cannot = unreachable(dial_i, dial_f) if not lossy else frozenset()
    hints = [] if lossy else sibling_kinds(iran_id, foreign_id, now)
    kinds = candidates(p, current, no_gre, lossy, now, limit=LIMIT if lossy else len(PRIOR), base=base, unreachable_kinds=cannot, prefer=hints)
    pb = {} if (lossy or not cause) else playbook(iran_id, foreign_id, cause, now)

    if pb and pb["kind"] in kinds and pb["kind"] not in cannot and pb["kind"] not in blocked(p, now):
        kinds = [pb["kind"]] + [k for k in kinds if k != pb["kind"]]
    else:
        pb = {}

    good = [r for r in ranked(p, now, include_poor=not lossy) if r["kind"] in kinds]
    bad = blocked(p, now)
    lines = []

    if pb:
        lines.append(f"The last time this happened on this pair ({cause.replace('_', ' ')}), {pb['kind']} healed it" + (f" ({pb['n']} times)" if pb["n"] > 1 else "") + " - it goes first.")

    if not lossy and dial_i is not None:
        lines.append("Who can still open connections: the Iran server " + ("CAN" if dial_i else "CANNOT") + " reach the foreign server" + ("" if dial_f is None else ", the foreign server " + ("CAN" if dial_f else "CANNOT") + " reach the Iran server")
                     + (" - the kinds where the foreign server dials in (reverse) come first." if dial_i is False and dial_f is not False else " - the fast kinds where the Iran server dials come first." if dial_i else "."))

    if good:
        lines.append("Known to connect between these two servers (what the Doctor tested before): " + ", ".join(f"{r['kind']} {r['score'] if r['score'] is not None else 'works'}" for r in good[:4]) + " - tried first, the best first.")
    else:
        lines.append("Nothing is known yet about the tunnel types between these two servers: " + ("the order of a cut tunnel is used (robust kinds first)." if not lossy else "the standard order is used.") + " The Doctor tests the pairs before the next failure (Doctor page, Knowledge).")

    if hints:
        lines.append("The other tunnels that end on this foreign server connect with: " + ", ".join(hints[:4]) + " - tried after what is known for this pair.")

    if bad:
        lines.append("Not tried, they failed on this pair lately: " + ", ".join(bad[:6]) + ".")

    return kinds, lines


# ------------------------------------------------------------------ the survey
def _running() -> dict | None:
    from . import tunnel_test

    for job in list(tunnel_test._COMPARE.values()):
        if not job["done"] and job.get("source") in SURVEYS:
            return {"id": job["id"], "iran_id": job["iran_id"], "foreign_id": job["foreign_id"], "kind": job["current"], "done": len(job["results"]), "total": len(job["kinds"]), "source": job["source"]}

    return None


def survey_start(iran_id: int, foreign_id: int, source: str = "survey-manual", kinds: list[str] | None = None) -> str:
    """Test every tunnel kind (or only `kinds`) between an Iran server and a foreign server, one after the other, and keep what they gave. ValueError when the pair is not fit for it,
    RuntimeError when another tunnel test runs (only one at a time)."""
    from . import tunnel_test

    with session() as db:
        i, f = db.get(Server, iran_id), db.get(Server, foreign_id)

        if i is None or i.role != "iran" or f is None or f.role != "foreign":
            raise ValueError("pick an Iran server and a foreign server")

        if not (i.enabled and f.enabled) or i.online is False or f.online is False:
            raise ValueError("both servers must be on")

        names = (i.name, f.name)

    order = [k for k in PRIOR if kinds is None or k in kinds]
    measure = "full" if source == "survey-manual" else "latency"      # the night survey asks only whether a type connects and what its delay and loss are (a few pings through a temporary tunnel): no speed test on servers that carry users
    jid = tunnel_test.compare_start(iran_id, foreign_id, order, measure=measure, source=source)

    with session() as db:
        how = "by hand: with a speed test" if source == "survey-manual" else "the first survey of this pair, on quiet servers: delay and loss only" if source == "survey-cold" else "the night survey: delay and loss only"
        emit(db, "info", "tunnels", f"Survey of {names[0]} ⇄ {names[1]} started: {len(order)} tunnel types, one after the other ({how}).", names[0])

    return jid


def yield_slot() -> bool:
    """A repair needs the one place there is for tunnel tests: a survey that runs gives it up (the kind it is testing is cancelled and removed). True when a survey was running."""
    from . import tunnel_test

    r = _running()

    if r is None:
        return False

    tunnel_test.compare_stop(r["id"])
    return True


def stop() -> None:
    if not yield_slot():
        raise ValueError("no survey is running")


def _server_state(db, server_id: int, now: int) -> tuple[bool, float | None]:
    s = db.get(Server, server_id)
    smp = db.scalar(select(ServerSample).where(ServerSample.server_id == server_id, ServerSample.ts >= now - 360).order_by(ServerSample.ts.desc()).limit(1))
    return bool(s is not None and s.enabled and s.online is not False), (smp.cpu if smp is not None else None)


def _live_pairs(db) -> dict[tuple[int, int], int]:
    out: dict[tuple[int, int], int] = {}

    for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
        if not is_temp(t.name):
            out[(t.iran_server_id, t.foreign_server_id)] = out.get((t.iran_server_id, t.foreign_server_id), 0) + 1

    return out


def pending_pairs(now: int, cold_only: bool = False) -> list[tuple[int, int, list[str]]]:
    """The pairs that carry a tunnel and have kinds due for a test: (Iran id, foreign id, the due kinds in the standard order), the pair with the least knowledge first.
    `cold_only`: only the pairs that have never been surveyed (nothing is known about them at all)."""
    from . import license

    allowed = [k for k in READY if license.tunnel_kind_allowed(k)]            # (the kinds a repair reaches for; the owner can survey all of them by hand)
    d = _doc()

    with session() as db:
        live = _live_pairs(db)
        out = []

        for i, f in live:
            if not (_server_state(db, i, now)[0] and _server_state(db, f, now)[0]):
                continue

            p = d["pairs"].get(pair_key(i, f)) or {}

            if cold_only and p.get("kinds"):
                continue

            due = due_kinds(p, allowed, now)

            if due:
                out.append((len(p.get("kinds") or {}), int(p.get("at") or 0), i, f, due))

    return [(i, f, due) for _, _, i, f, due in sorted(out)]


def not_now(iran_id: int, foreign_id: int, now: int, quiet: float = QUIET_MBPS) -> str:
    """Why the pair must not be surveyed right now ('' when it may): a server is busy, or its tunnels carry users (`quiet`: Mb/s of users under which the survey goes on)."""
    with session() as db:
        for sid in (iran_id, foreign_id):
            _on, cpu = _server_state(db, sid, now)

            if cpu is not None and cpu >= BUSY_CPU:
                return f"a server is busy (CPU {cpu:.0f} %)"

        carried = sum((t.rx_mbps or 0) + (t.tx_mbps or 0) for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), (Tunnel.iran_server_id == iran_id) | (Tunnel.foreign_server_id == foreign_id)))
                      if not is_temp(t.name))

    return f"its servers carry {carried:.0f} Mb/s for users (the survey waits under {quiet:.0f})" if carried >= quiet else ""


def _tell_finished() -> None:
    """One event for every survey that ended."""
    from . import tunnel_test

    for job in list(tunnel_test._COMPARE.values()):
        if not job["done"] or job.get("source") not in SURVEYS or job.get("told"):
            continue

        job["told"] = True

        with session() as db:
            i, f = db.get(Server, job["iran_id"]), db.get(Server, job["foreign_id"])
            names = (i.name if i else "?", f.name if f else "?")
            rows = [r for r in job["results"] if r.get("ok")]
            best = sorted(rows, key=lambda r: -(r.get("score") or 0))[:3]
            text = (f"Survey of {names[0]} ⇄ {names[1]} " + ("stopped" if job.get("stop") else "finished") + f": {len(job['results'])} of {len(job['kinds'])} tunnel types tested, {len(rows)} work"
                    + (" - best: " + ", ".join(f"{r['kind']} {r.get('score')}" for r in best) if best else "") + ".")
            emit(db, "ok" if rows else "warn", "tunnels", text, names[0])


def run() -> None:
    """Job (every ten minutes): tell about a survey that ended, stop one that must not go on, start the next one when everything allows it."""
    if jobs.stopping() or get_settings().read_only:
        return

    from . import autopilot, tunnel_test

    now = int(time.time())
    d = st.get("knowledge")
    tz = int(st.get("report").get("tz_offset_minutes", 210))
    window = in_window(now, tz, int(d["from_hour"]), int(d["to_hour"]))
    _tell_finished()
    running = _running()
    busy = bool(autopilot.repairing_names()) or autopilot.storm_now()

    if running:
        if busy or (running["source"] == "survey" and not (d["on"] and window)) or (running["source"] == "survey-cold" and not d["on"]):
            tunnel_test.compare_stop(running["id"])            # a repair needs the place, or the night is over

        return

    if not d["on"] or busy:
        return

    cold = not window                                          # outside the night window only the pairs nobody has tested yet are surveyed, and only on very quiet servers
    source = "survey-cold" if cold else "survey"

    for iran_id, foreign_id, kinds in pending_pairs(now, cold_only=cold):
        why = not_now(iran_id, foreign_id, now, COLD_QUIET_MBPS if cold else QUIET_MBPS)

        if why:
            log.info("survey of %s:%s waits: %s", iran_id, foreign_id, why)
            continue

        try:
            survey_start(iran_id, foreign_id, source, kinds)
        except (ValueError, RuntimeError) as e:
            log.info("survey of %s:%s not started: %s", iran_id, foreign_id, str(e)[:120])

        return


# ------------------------------------------------------------------ for the page
def state(now: int | None = None) -> dict:
    now = now or int(time.time())
    d = _doc()
    tz = int(st.get("report").get("tz_offset_minutes", 210))

    with session() as db:
        names = {s.id: s.name for s in db.scalars(select(Server))}
        live = _live_pairs(db)

    rows = []

    for k in sorted(set(d["pairs"]) | {pair_key(i, f) for i, f in live}):
        i, f = (int(x) for x in k.split(":"))

        if i not in names or f not in names:
            continue

        p = d["pairs"].get(k) or {}
        kinds = p.get("kinds") or {}
        rows.append({"iran_id": i, "foreign_id": f, "iran": names[i], "foreign": names[f], "tunnels": live.get((i, f), 0), "at": int(p.get("at") or 0), "tested": len(kinds),
                     "due": len(due_kinds(p, READY, now)), "best": ranked(p, now), "poor": poor(p, now), "cuts": repeats(i, f, now),
                     "blocked": [{"kind": x, "error": kinds[x]["last"].get("error") or "", "at": int(kinds[x]["last"].get("at") or 0)} for x in blocked(p, now)]})

    rows.sort(key=lambda r: (r["iran"].lower(), r["foreign"].lower()))
    return {"on": bool(d["on"]), "from_hour": int(d["from_hour"]), "to_hour": int(d["to_hour"]), "tz_minutes": tz, "window_open": in_window(now, tz, int(d["from_hour"]), int(d["to_hour"])),
            "running": _running(), "kinds": len(PRIOR), "ready": list(READY), "pairs": rows,
            "incidents": [{**e, "pair_name": " ⇄ ".join(names.get(int(x), "?") for x in str(e.get("pair", "0:0")).split(":"))} for e in list(reversed(d.get("incidents") or []))[:30]]}
