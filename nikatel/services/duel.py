"""THE DUEL: a tunnel and its warm standby tested one after the other, at the same hour, until the better one is PROVEN - then the Doctor changes the type without a cut (1.69.268).

(The owner, 2026-11-14, after the Doctor kept a tunnel on the slowest type it knew for ten days: "what would you improve in the Doctor's speed test?" - "yes, build" the suggestions 4 and 6: a test of the tunnel and
its standby back to back, and a confirm round that changes the type by itself.)

Why. The Doctor needs three full tests, a week of them, of a rival type before it calls it steady (`knowledge.STEADY_MIN_TESTS`), and the tests it gets are of different hours and of different kinds (a pair test
and the quality test of a running tunnel read different things). A line that gives 38 Mb/s at one hour and 121 at the next cannot be judged that way. But EVERY tunnel already has a warm standby of ANOTHER type, up
beside it and carrying nobody: a free laboratory. Testing the tunnel and its standby within seconds of each other, round after round, is the fairest comparison there is - same servers, same route, same minute.

  a round     the tunnel and its standby are measured through their own test doors (`bandwidth.measure`, two light connections, 120 MB at most each), one after the other, the order alternating from round to round.
              Both numbers are kept in the Doctor's memory of the pair (the tests do that by themselves) and in the round.
  a case      ROUNDS rounds, ROUND_GAP apart. After the FIRST round a standby that is not clearly faster (FIRST_LOOK times) in ANY round ends the case: nothing more is tested for it for RECHECK. After the last round `rate`
              says "upgrade" only when the median of the standby is the gain the upgrade rule asks of a better type (`knowledge.UPGRADE_RATIO` times and `UPGRADE_MIN_GAIN` Mb/s more), it was clearly faster in EVERY
              round, lost at most `knowledge.UPGRADE_MAX_LOSS` %, answers about as fast (`knowledge._ping_ok`), costs no more on the wire than the licence and the overhead rule allow.
  what then   the same no-cut change the Doctor has always made (`doctor._run_retype` with auto: the new type is already built beside the old one, it is measured through itself again, the users' NEW connections go
              over, it is watched for a minute and a half, the old tunnel is waited for until its connections have ended, and only then built again; the old type goes back when the new one carries worse), with every
              rail of it: once in 24 hours per tunnel, one change an hour for the whole panel (`bandwidth.UPGRADE_FLEET_GAP`), the modes of the Doctor, a protected tunnel never, and the users are watched for six hours
              afterwards (`retypewatch`: they leave a type they dislike and the old type goes back). New here: at most MAX_PER_DAY changes started by a duel in a day.
  what it costs   one round is two light tests: at most 240 MB. A first round for every tunnel with a standby of another type once in RECHECK (3 days) and two more rounds only where the standby looked clearly faster;
              nothing runs while the day's volume of the Doctor's tests is used to its routine share (`testbudget`), while the servers carry users (`knowledge.not_now`), while a failover is on, or while the
              tunnel or its standby is not in its usual state. One round per turn of the job, one tunnel at a time.

The settings document `duel`: `on` (the owner's switch) and the `cases`. The pure parts (`rate`, `next_step`, `settled`) carry the decisions and are tested."""
from __future__ import annotations

import statistics
import threading
import time

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel, is_protected, is_standby, is_temp
from ..ssh import SSHError
from . import settings as st
from .events import emit

log = get_logger("duel")
EVERY = 300                    # seconds between two turns of the job (the job is registered in main.py): one round at most per turn
ROUNDS = 3                     # rounds of a case
ROUND_GAP = 40 * 60            # between two rounds of one case: the same hour, but not the same minute
FIRST_LOOK = 1.2               # the standby must be at least this many times as fast as the tunnel in EVERY round
READY_FOR = 3 * 3600           # the standby has been up this long (a standby that was just built or mended proves nothing about its steadiness)
CASE_STALE = 4 * 3600          # the rounds of a case that waited longer than this are void: a new case starts
RECHECK = 3 * 24 * 3600        # a case that ended with "no" is not looked at again for this long (the same two types)
VALID_FOR = 6 * 3600           # an "upgrade" verdict waits this long for the rails (once in 24 hours, one change an hour for the panel) to let the change start
MAX_PER_DAY = 2                # changes of type started by duels in 24 hours, for the whole panel
KEEP_CASES = 80
_LOCK = threading.Lock()
_RUNNING = False


# ------------------------------------------------------------------ pure decisions
def _med(values: list[float]) -> float:
    return float(statistics.median(values))


def rate(rounds: list[dict]) -> tuple[str, str]:
    """Pure: ("more" | "no" | "upgrade", the sentence) from the rounds of a case, each {"p": the tunnel's download (Mb/s), "s": its standby's, "p_ms", "s_ms": their delay, "s_loss": the standby's loss %}.
    "no" after the first round when the standby is not FIRST_LOOK times as fast; "more" until ROUNDS rounds are in; then "upgrade" only when the medians give the gain the upgrade rule asks of a better type
    (UPGRADE_RATIO times and UPGRADE_MIN_GAIN Mb/s more), the standby was FIRST_LOOK times as fast in EVERY round, lost at most UPGRADE_MAX_LOSS % and answers about as fast as the tunnel."""
    from . import knowledge

    if not rounds:
        return "more", ""

    p, s = [float(r.get("p") or 0.0) for r in rounds], [float(r.get("s") or 0.0) for r in rounds]
    first = f"{s[0]:.0f} against {p[0]:.0f} Mb/s"

    for i, (sv, pv) in enumerate(zip(s, p), 1):
        if sv < FIRST_LOOK * max(pv, 1.0):
            return "no", f"in round {i} of the duel the standby gave {sv:.0f} against {pv:.0f} Mb/s: not clearly faster"

    if len(rounds) < ROUNDS:
        return "more", f"the first round gave {first}: more rounds to confirm"

    rounds, p, s = rounds[-ROUNDS:], p[-ROUNDS:], s[-ROUNDS:]
    mp, ms = _med(p), _med(s)
    need = max(mp * knowledge.UPGRADE_RATIO, mp + knowledge.UPGRADE_MIN_GAIN)
    said = f"{ROUNDS} rounds: the standby gave {ms:.0f} Mb/s (median), the tunnel {mp:.0f}"

    if ms < need:
        return "no", f"{said}: under the {need:.0f} Mb/s that make a change worth it"

    loss = max(float(r.get("s_loss") or 0.0) for r in rounds)

    if loss > knowledge.UPGRADE_MAX_LOSS:
        return "no", f"{said}: but the standby lost {loss:g} % of the packets"

    lat_s = [float(r["s_ms"]) for r in rounds if r.get("s_ms") is not None]
    lat_p = [float(r["p_ms"]) for r in rounds if r.get("p_ms") is not None]

    if lat_s and lat_p and not knowledge._ping_ok({"latency_ms": _med(lat_s)}, {"latency_ms": _med(lat_p)}):
        return "no", f"{said}: but the standby answers in {_med(lat_s):.0f} ms against {_med(lat_p):.0f}"

    return "upgrade", f"{said}, clearly faster in every round, {loss:g} % lost"


def next_step(case: dict | None, p_kind: str, s_kind: str, now: int) -> str:
    """Pure: what the job does for one tunnel now - "reset" (the case belongs to other types or its rounds are void: start again), "wait" (nothing to do), "round" (a round is due), "start" (an "upgrade"
    verdict is waiting for the change to start). `case`: {"p_kind", "s_kind", "rounds": [...], "state": "run" | "no" | "upgrade" | "started", "at": when the state began}."""
    if not case or (case.get("p_kind"), case.get("s_kind")) != (p_kind, s_kind):
        return "reset"

    state, at, rounds = case.get("state") or "run", int(case.get("at") or 0), case.get("rounds") or []

    if state == "no":
        return "reset" if now - at >= RECHECK else "wait"

    if state == "started":
        return "reset" if now - at >= RECHECK else "wait"

    if state == "upgrade":
        return "start" if now - at < VALID_FOR else "reset"

    last = int(rounds[-1].get("at") or 0) if rounds else 0

    if rounds and now - last > CASE_STALE:
        return "reset"

    return "round" if not rounds or now - last >= ROUND_GAP else "wait"


def settled(case: dict, rnd: dict, now: int) -> dict:
    """Pure: the case after one more round: the round is added and the verdict of `rate` decides the state ("run" while more rounds are due)."""
    out = dict(case)
    out["rounds"] = (list(case.get("rounds") or []) + [rnd])[-ROUNDS:]
    verdict, text = rate(out["rounds"])
    out["state"] = {"more": "run", "no": "no", "upgrade": "upgrade"}[verdict]
    out["text"] = text

    if out["state"] != "run":
        out["at"] = now

    return out


def started_today(cases: dict, now: int) -> int:
    """Pure: the changes of type that duels started in the last 24 hours."""
    return sum(1 for c in cases.values() if c.get("state") == "started" and now - int(c.get("at") or 0) < 24 * 3600)


# ------------------------------------------------------------------ the job
def _doc() -> dict:
    d = st.get("duel")
    d.setdefault("on", True)
    d.setdefault("cases", {})
    return d


def _candidates(now: int) -> list[dict]:
    """The tunnels that have a standby of another type, up and idle for READY_FOR: [{"name", "id", "sid", "iran_id", "foreign_id", "p_kind", "s_kind"}]. Everything else the job needs is asked of the one chosen."""
    from . import autopilot, knowledge, license, standby, trident

    out = []

    with session() as db:
        names = [t.name for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), Tunnel.state == "up")) if not is_standby(t) and not is_temp(t.name) and not is_protected(t)]

    views = standby._views(names)

    for name in names:
        p, s = views[name]["p"], views[name]["s"]

        if p is None or s is None or p["off"] or s["state"] != "up" or s["use"] or s["stale"] or s["parked"] or s["kind"] == p["kind"] or not license.tunnel_kind_allowed(s["kind"]):
            continue

        if now - int(s["state_since"] or 0) < READY_FOR or now - int(p["state_since"] or 0) < READY_FOR or name in autopilot._BUSY or name in standby._BUILDS:
            continue

        if (s.get("lane") or {}).get("on") or trident.owns(name):          # (a standby that carries users in a lane, or a tunnel whose ports a Trident shares, is not a free laboratory: the test would be unfair and the change would be refused)
            continue

        if s["kind"] in knowledge.blocked(knowledge.pair(p["iran_id"], p["foreign_id"]), now):          # (a type that did not work on this pair lately - or that the users left - is not given a duel)
            continue

        out.append({"name": name, "id": p["id"], "sid": s["id"], "iran_id": p["iran_id"], "foreign_id": p["foreign_id"], "p_kind": p["kind"], "s_kind": s["kind"]})

    return out


def _measure_round(c: dict, parity: int) -> dict | None:
    """One round: the tunnel and its standby measured one after the other (the order alternates). None when one of them could not be measured now."""
    from . import bandwidth

    order = [("p", c["id"]), ("s", c["sid"])] if parity % 2 == 0 else [("s", c["sid"]), ("p", c["id"])]
    got: dict[str, dict] = {}

    for side, tid in order:
        try:
            bw, change = bandwidth.measure(tid)
        except (ValueError, RuntimeError, SSHError, OSError) as e:
            log.info("duel of %s: the %s could not be measured: %s", c["name"], "tunnel" if side == "p" else "standby", str(e)[:120])
            return None

        if change in ("drop", "slow") and side == "p":
            bandwidth._investigate_later(tid)

        with session() as db:
            t = db.get(Tunnel, tid)
            q = dict((t.record or {}).get("quality") or {}) if t is not None else {}

        got[side] = {"down": bw.get("down"), "ms": bw.get("ping") if bw.get("ping") is not None else q.get("latency_ms"), "loss": q.get("loss_pct")}

    if got["p"]["down"] is None or got["s"]["down"] is None:
        return None

    return {"at": int(time.time()), "p": float(got["p"]["down"]), "s": float(got["s"]["down"]), "p_ms": got["p"]["ms"], "s_ms": got["s"]["ms"], "p_loss": got["p"]["loss"], "s_loss": got["s"]["loss"], "first": order[0][0]}


def _start_change(c: dict, case: dict, now: int) -> str:
    """Start the no-cut change of type of an "upgrade" case with every rail of the Doctor's own change. Returns "" when it started, else the sentence that says why not (the case waits: the rails may open)."""
    from . import actions, autopilot, bandwidth, doctor, knowledge, license, overhead

    if actions.doctor_mode() == "off" or get_settings().read_only:
        return "the Doctor is off or the panel is read-only"

    s = doctor._state()

    why = doctor.may_retype(c["name"], s.get("retype_done") or {}, now)

    if why:
        return why

    if any(now - int(v) < bandwidth.UPGRADE_FLEET_GAP for v in (s.get("retype_done") or {}).values()):
        return "another tunnel was changed less than an hour ago (one change an hour for the whole panel)"

    if started_today(_doc()["cases"], now) >= MAX_PER_DAY:
        return f"{MAX_PER_DAY} changes were started by duels in the last 24 hours"

    if any(x.get("tunnel") == c["name"] for x in s["suggestions"]):
        return "a move of this tunnel is suggested: it is more urgent"

    p = knowledge.pair(c["iran_id"], c["foreign_id"])
    ent = (p or {}).get("kinds") or {}
    c_cur, _ = overhead.cost(c["p_kind"], (ent.get(c["p_kind"]) or {}).get("wire"), knowledge.wire_raw(c["p_kind"]))
    c_new, _ = overhead.cost(c["s_kind"], (ent.get(c["s_kind"]) or {}).get("wire"), knowledge.wire_raw(c["s_kind"]))

    if c_new >= overhead.HEAVY and c_new > c_cur:
        return f"{c['s_kind']} costs more on the wire ({c_new:.2f}x) than the rule allows"

    if not license.tunnel_kind_allowed(c["s_kind"]):
        return "the licence does not allow the type"

    rounds = case.get("rounds") or []
    why = (f"A duel of {c['name']} against its standby: {case.get('text', '')}. Nothing is wrong with {c['p_kind']}: {c['s_kind']} was clearly faster in every one of {len(rounds)} rounds, "
           f"tested one after the other over {max(1, (int(rounds[-1]['at']) - int(rounds[0]['at'])) // 60)} minutes. The standby is already built: the change needs no new build and cuts nobody.")

    try:
        autopilot._start(c["name"], doctor._run_retype, c["id"], c["s_kind"], c["p_kind"], why, True, action="autopilot", source="auto")
    except ValueError as e:
        return str(e)[:140]

    return ""


def _save(d: dict, name: str, case: dict) -> None:
    cases = dict(d["cases"])
    cases[name] = case
    cases = dict(sorted(cases.items(), key=lambda kv: -int(kv[1].get("at") or 0))[:KEEP_CASES])
    st.put("duel", {"cases": cases})
    d["cases"] = cases


def _note(c: dict, case: dict) -> None:
    """The summary the tunnel's window shows (record["duel"]) - the standby's type, the medians, what it decided."""
    rounds = case.get("rounds") or []

    if not rounds:
        return

    with session() as db:
        t = db.get(Tunnel, c["id"])

        if t is not None:
            rec = dict(t.record or {})
            rec["duel"] = {"s_kind": c["s_kind"], "n": len(rounds), "p": round(_med([float(r["p"]) for r in rounds]), 1), "s": round(_med([float(r["s"]) for r in rounds]), 1), "state": case.get("state"),
                           "text": str(case.get("text") or "")[:200], "at": int(rounds[-1]["at"])}
            t.record = rec


def run(now: int | None = None) -> None:
    """Job (EVERY seconds): ONE round of ONE tunnel at most per turn. `now`: for the tests."""
    global _RUNNING

    if get_settings().read_only or jobs.stopping():
        return

    from . import actions, autopilot, bandwidth, knowledge, testbudget

    d = _doc()

    if not d.get("on", True) or actions.doctor_mode() == "off" or autopilot.quiet_now():
        return

    with _LOCK:
        if _RUNNING:
            return

        _RUNNING = True

    try:
        now = now or int(time.time())
        todo = []

        for c in _candidates(now):
            case = d["cases"].get(c["name"])
            step = next_step(case, c["p_kind"], c["s_kind"], now)

            if step == "reset":
                case = {"p_kind": c["p_kind"], "s_kind": c["s_kind"], "rounds": [], "state": "run", "at": now}
                step = "round"

            if step in ("round", "start"):
                todo.append((0 if step == "start" else 1 if case.get("rounds") else 2, int((case.get("rounds") or [{}])[-1].get("at") or 0), c, case, step))

        if not todo:
            return

        todo.sort(key=lambda x: (x[0], x[1]))                                                   # (a verdict that waits first, then a case under way, then a new one; the longest waiting first)
        _, _, c, case, step = todo[0]

        if step == "start":
            why = _start_change(c, case, now)

            if not why:
                case = {**case, "state": "started", "at": now}
                _save(d, c["name"], case)
                log.info("duel: the change of %s to %s started", c["name"], c["s_kind"])
            else:
                log.info("duel: the change of %s waits: %s", c["name"], why)

            return

        if not testbudget.allow("routine", now) or knowledge.not_now(c["iran_id"], c["foreign_id"], now, knowledge.VERIFY_QUIET_MBPS) or bandwidth._server_busy_for(c["id"], now):
            return

        rnd = _measure_round(c, len(case.get("rounds") or []))

        if rnd is None:
            return

        was = case.get("state")
        case = settled(case, rnd, now)
        _save(d, c["name"], case)
        _note(c, case)
        log.info("duel of %s (%s) against its standby (%s): round %s: %.0f against %.0f Mb/s -> %s", c["name"], c["p_kind"], c["s_kind"], len(case["rounds"]), rnd["s"], rnd["p"], case["state"])

        if case["state"] != was and case["state"] in ("no", "upgrade"):
            with session() as db:
                emit(db, "ok" if case["state"] == "upgrade" else "info", "tunnels",
                     f"Duel of {c['name']} ({c['p_kind']}) against its standby ({c['s_kind']}): {case['text']}." + (" The type is changed without a cut when the Doctor's rails allow it." if case["state"] == "upgrade" else ""), c["name"])

            if case["state"] == "upgrade":
                why = _start_change(c, case, now)

                if not why:
                    _save(d, c["name"], {**case, "state": "started", "at": now})
                else:
                    log.info("duel: the change of %s waits: %s", c["name"], why)
    finally:
        with _LOCK:
            _RUNNING = False
