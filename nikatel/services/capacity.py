"""Capacity: a tunnel whose users use nearly all that it can carry (the owner, 2026-10-20, with a picture of a tunnel that could carry 76 Mb/s and carried 69: "when the Doctor sees the capacity filling, it must
bring a tunnel that gives more bandwidth, without cutting the customers; load balancing or any other idea that raises the capacity").

  saturation(rx, cap, peak24)         pure: "unknown" | "ok" | "warm" | "full" - what share of its capacity the users used over the last ten minutes
  better_kinds(pair, now, cur, ...)   pure: the types the Doctor's memory of the pair knows to carry clearly more, in a fresh FULL test, with little loss (the types that SUCCEEDED in the tests)
  lighter(candidates, loads, here)    pure: the Iran servers whose tunnels use a smaller share of what they can carry than the one that is filling
  check()                             the job (every minute, from the Doctor's run): the state of every tunnel is kept (`state_of`, `view`); a tunnel that stays FULL for CONFIRM minutes is told (an event and
                                      Telegram) and - in the modes fix and move, once in 24 hours, never in a storm or while a repair works on it - one story starts for it:
  the story                           1. a better type that its standby already is (ready, up) -> the SWAP WITHOUT A CUT: the users' NEW connections are handed to the standby (the open ones stay on the tunnel), the
                                         tunnel is built again as that type once its open connections have ended, then the new connections go back to it (the way back of the standby); the standby carries what is left
                                         until it ends. Nothing is cut: at every step the users stand on a tunnel that works.
                                      2. a better type is known but no standby of it is ready -> a suggestion on the Doctor page (one click; that change cuts the users for a minute or two, so it is never done by itself);
                                      3. an Iran server whose tunnels use less of what they can carry, with room and a good route -> the tunnel MOVES there (make before break, the DNS follows: the Doctor's move, only in
                                         the mode "move" and with the failover licence), else the move is suggested;
                                      4. nothing found: it is told, and the load balancing (a second tunnel beside this one) is the next step.

The capacity is the Doctor's own test of the tunnel (`record.bw.down`); a tunnel whose users carried MORE than that in the last 24 hours (the test reads low) is never called full."""
from __future__ import annotations

import statistics
import time

from sqlalchemy import func, select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel, TunnelCheck, is_standby, is_temp
from . import settings as st
from .events import emit

log = get_logger("capacity")
FULL_AT = 0.85              # a minute counts as full when the users use this share of the capacity ...
WARM_AT = 0.70              # ... "warm" when the typical minute does
WINDOW = 600                # the last ten minutes are judged
MIN_SAMPLES = 6             # readings needed (one a minute)
FULL_SHARE = 0.6            # FULL = this share of the readings is at FULL_AT or more
CONFIRM = 3                 # minutes of FULL in a row before the Doctor acts
ACT_GAP = 24 * 3600         # one story per tunnel in this time
BW_FRESH = 3 * 3600         # the capacity is believed this long after its test
TRUST = 1.15                # the capacity is believed when the users' 24 h peak is not above it by more than this (else the test reads low: the users carried more)
LANE_RATIO = 1.5            # a better type carried this many times what the running type carried in the pair tests ...
LANE_GAIN = 20.0            # ... and at least this many Mb/s more ...
LANE_MAX_LOSS = 2.0         # ... lost at most this % of its packets ...
DRAIN_LIMIT = 1800          # the swap waits this long for the open connections of the old tunnel to end
LOAD_MAX = 0.5              # a lighter Iran server: its tunnels use at most this share of what they can carry
PEAK_EVERY = 600            # the 24 h peaks are read again this often
_VIEW: dict = {"at": 0, "tunnels": {}}
_PEAKS: dict = {"at": 0, "rows": {}}


# ------------------------------------------------------------------ pure decisions
def saturation(rx: list[float | None], cap: float | None, peak24: float | None = None) -> dict:
    """Pure: {"state", "util", "share", "n"}. `rx`: the readings (Mb/s, one a minute) of the last WINDOW seconds, `cap`: the capacity (Mb/s), `peak24`: the highest reading of the last 24 hours. "unknown": no capacity, too few
    readings, or the users carried more than the capacity in the last day (the test reads low, the capacity is not what limits them)."""
    vals = [float(v) for v in rx if v is not None]
    out = {"state": "unknown", "util": None, "share": None, "n": len(vals)}

    if cap is None or cap <= 0 or len(vals) < MIN_SAMPLES or (peak24 is not None and peak24 > cap * TRUST):
        return out

    util = statistics.median(vals) / cap
    share = sum(1 for v in vals if v >= FULL_AT * cap) / len(vals)
    out.update(util=round(util, 3), share=round(share, 2), state="full" if share >= FULL_SHARE else "warm" if util >= WARM_AT else "ok")
    return out


def better_kinds(pair: dict | None, now: int, current: str, allowed=lambda k: True) -> list[dict]:
    """Pure: the types the Doctor's memory of the pair knows to carry CLEARLY more than the running type: both numbers are FULL pair tests of the same kind of measurement and fresh; the other type carried at least
    LANE_RATIO times as much and LANE_GAIN Mb/s more, lost at most LANE_MAX_LOSS % and scored at least as well; the licence allows it. The fastest first. Empty when the running type has no fresh full test on the pair
    (nothing to compare with: nothing is guessed). Only a type that SUCCEEDED in a test AND whose steadiness is PROVEN (`knowledge.stability`: at least STEADY_MIN_TESTS tests, STEADY_OK or more) is ever chosen."""
    from . import knowledge

    full = (((pair or {}).get("kinds") or {}).get(current) or {}).get("full") or {}

    if full.get("down_mbps") is None or now - int(full.get("at") or 0) > knowledge.FASTER_FRESH:
        return []

    base = float(full["down_mbps"])
    need = max(base * LANE_RATIO, base + LANE_GAIN)
    rows = [r for r in knowledge.ranked(pair, now) if r["kind"] != current and r.get("measure") == "full" and r.get("down_mbps") is not None and float(r["down_mbps"]) >= need
            and now - int(r.get("at") or 0) <= knowledge.FASTER_FRESH and float(r.get("loss_pct") or 0) <= LANE_MAX_LOSS and (r.get("score") or 0) >= (full.get("score") or 0) and allowed(r["kind"])
            and r.get("steady") is not None and r["steady"] >= knowledge.STEADY_OK]          # (STEADY: it passed its tests - the owner, 2026-10-21: "if there is no stability, do not go to that type by itself")
    return sorted(rows, key=lambda r: (-float(r["down_mbps"]), -(r["score"] or 0)))


def loads(items: list[dict]) -> dict[int, float]:
    """Pure: {Iran server id: the share of what its tunnels can carry that their users use} - from rows {"iran_id", "use", "cap"} (Mb/s); a tunnel with no capacity is left out."""
    use: dict[int, float] = {}
    cap: dict[int, float] = {}

    for x in items:
        if x.get("cap") and x["cap"] > 0:
            use[x["iran_id"]] = use.get(x["iran_id"], 0.0) + float(x.get("use") or 0)
            cap[x["iran_id"]] = cap.get(x["iran_id"], 0.0) + float(x["cap"])

    return {sid: round(use[sid] / cap[sid], 3) for sid in cap}


def lighter(candidates: list[dict], by_server: dict[int, float], here: float | None, limit: float = LOAD_MAX) -> list[dict]:
    """Pure: the candidate Iran servers (best first, as the move's plan gives them) whose tunnels use at most `limit` of what they can carry and less than the server that is filling (`here`); a server with
    no tunnel that has a capacity counts as empty."""
    return [c for c in candidates if by_server.get(c["id"], 0.0) <= limit and (here is None or by_server.get(c["id"], 0.0) < here)]


# ------------------------------------------------------------------ state
def _state() -> dict:
    s = st.get("capacity")
    s.setdefault("tunnels", {})
    s.setdefault("acted", {})
    return s


def view(name: str) -> dict:
    """What the page shows of a tunnel (kept in memory by `check`): {} while it is fine or unknown, else {"state": "warm"|"full", "util": ..., "cap": ..., "since": ...}."""
    e = (_VIEW.get("tunnels") or {}).get(name) or {}
    return {k: e[k] for k in ("state", "util", "cap", "since") if k in e} if e.get("state") in ("warm", "full") else {}


def state_of(name: str) -> str:
    return str(((_VIEW.get("tunnels") or {}).get(name) or {}).get("state") or "")


def _peaks(now: int) -> dict[int, float]:
    """The highest reading of the last 24 hours of every tunnel (read again every PEAK_EVERY seconds)."""
    if now - int(_PEAKS["at"]) < PEAK_EVERY and _PEAKS["rows"]:
        return _PEAKS["rows"]

    with session() as db:
        rows = db.execute(select(TunnelCheck.tunnel_id, func.max(TunnelCheck.rx_mbps)).where(TunnelCheck.ts >= now - 86400, TunnelCheck.rx_mbps.is_not(None)).group_by(TunnelCheck.tunnel_id)).all()

    _PEAKS["at"], _PEAKS["rows"] = now, {int(a): float(b) for a, b in rows if b is not None}
    return _PEAKS["rows"]


def snapshot(now: int) -> list[dict]:
    """Every enabled tunnel that is up and has a capacity measured lately: its servers, its type, the capacity and the readings of the last WINDOW seconds (Mb/s)."""
    from . import autopilot

    with session() as db:
        rows = []

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            if is_standby(t) or is_temp(t.name) or t.state != "up":
                continue

            bw = (t.record or {}).get("bw") or {}

            if bw.get("down") is None or now - int(bw.get("at") or 0) > BW_FRESH or bw.get("state") in ("drop", "slow"):
                continue

            rows.append({"id": t.id, "name": t.name, "iran_id": t.iran_server_id, "foreign_id": t.foreign_server_id, "iran": t.iran.name, "cap": float(bw["down"]),
                         "kind": autopilot.kind_of(t.type, t.transport, t.record or {})})

        ids = {r["id"] for r in rows}
        series: dict[int, list[float]] = {}

        for tid, rx in db.execute(select(TunnelCheck.tunnel_id, TunnelCheck.rx_mbps).where(TunnelCheck.ts >= now - WINDOW, TunnelCheck.rx_mbps.is_not(None)).order_by(TunnelCheck.ts)):
            if tid in ids:
                series.setdefault(int(tid), []).append(float(rx))

    for r in rows:
        r["series"] = series.get(r["id"], [])
        r["use"] = statistics.median(r["series"]) if r["series"] else 0.0

    return rows


# ------------------------------------------------------------------ the job
def _notice(text: str) -> None:
    from . import failover

    failover._notice(text)


def check(now: int | None = None) -> list[str]:
    """Every minute: the state of every tunnel; a tunnel that has been FULL for CONFIRM minutes in a row is told once, and - when the Doctor may act - ONE story starts for the first of them that was not looked at in
    the last ACT_GAP. Returns the sentences told."""
    from . import actions, autopilot, doctor

    now = now or int(time.time())
    snap = snapshot(now)
    peaks = _peaks(now)
    s = _state()
    cur: dict[str, dict] = {}
    told: list[str] = []
    due: list[dict] = []

    for t in snap:
        sat = saturation(t["series"], t["cap"], peaks.get(t["id"]))
        old = s["tunnels"].get(t["name"]) or {}
        n = int(old.get("n", 0)) + 1 if sat["state"] == "full" else 0
        cur[t["name"]] = {"state": sat["state"], "util": sat["util"], "n": n, "cap": round(t["cap"], 1), "since": int(old.get("since", now)) if old.get("state") == sat["state"] else now, "at": now}

        if n == CONFIRM:
            line = f"{t['name']}: its users use {t['use']:.0f} of the {t['cap']:.0f} Mb/s it can carry ({round((sat['util'] or 0) * 100)} %) - the capacity is filling"
            told.append(line)

        if n >= CONFIRM and now - int(s["acted"].get(t["name"], 0)) >= ACT_GAP:
            due.append(t)

    s["tunnels"] = cur
    _VIEW["at"], _VIEW["tunnels"] = now, cur
    acted = None
    m = actions.doctor_mode()

    if due and m != "off" and not get_settings().read_only and not autopilot.storm_now():
        for t in due:
            if t["name"] in autopilot._BUSY or doctor.may_retype(t["name"], doctor._state().get("retype_done") or {}, now):
                continue

            acted = t
            break

    if acted is not None:
        s["acted"][acted["name"]] = now
        s["acted"] = {k: v for k, v in s["acted"].items() if now - int(v) < 7 * ACT_GAP}

    st.put("capacity", {"tunnels": s["tunnels"], "acted": s["acted"]})

    for line in told:
        with session() as db:
            emit(db, "warn", "actions", f"Capacity: {line}.", line.split(":")[0])

        _notice(f"⚠ Capacity: {line}." + (" The Doctor looks for a way to raise it." if m != "off" else " The Doctor is off: it only tells you."))

    if acted is not None:
        try:
            autopilot._start(f"capacity {acted['name']}", _story, acted["id"], action="move", source="auto")
        except ValueError as e:
            log.info("capacity story of %s not started: %s", acted["name"], e)

    return told


# ------------------------------------------------------------------ the story
def _say(aid: int, line: str, **kw) -> None:
    from . import autopilot

    autopilot._say(aid, line, **kw)


def _lane(info: dict, now: int) -> tuple[dict | None, list[dict]]:
    """(the better type that the tunnel's standby already is, every better type the memory knows) - the standby must be ready: up, not stale, not carrying anybody."""
    from . import knowledge, license, standby

    rows = better_kinds(knowledge.pair(info["iran_id"], info["foreign_id"]), now, info["kind"], license.tunnel_kind_allowed)
    sv = standby._view(info["name"])["s"]
    lane = next((r for r in rows if sv and r["kind"] == sv["kind"] and sv["state"] == "up" and not sv["stale"] and not sv["use"]), None)
    return lane, rows


def _story(aid: int, tunnel_id: int) -> None:
    """The look at a tunnel whose capacity is filling (see the module's text)."""
    from . import actions

    now = int(time.time())
    info = next((x for x in snapshot(now) if x["id"] == tunnel_id), None)

    if info is None:
        _say(aid, "The tunnel is not up with a measured capacity any more: nothing to do.", done=True, ok=True)
        return

    m = actions.doctor_mode()
    sat = saturation(info["series"], info["cap"], _peaks(now).get(tunnel_id))
    _say(aid, f"{info['name']}: its users use {info['use']:.0f} of the {info['cap']:.0f} Mb/s it can carry ({round((sat['util'] or 0) * 100)} %). The Doctor looks for more capacity (mode {m}).")
    lane, rows = _lane(info, now)

    if lane is not None:
        _lane_swap(aid, tunnel_id, info, lane)
        return

    if rows:
        r = rows[0]
        _say(aid, f"A better steady type is known ({r['kind']}), but the standby of this tunnel is not that type or not ready: the Doctor does not cut the users of a nearly full tunnel for it, and it suggests nothing (the owner, 2026-10-22: no more suggestions) - it looks for a lighter Iran server.")
    _try_move(aid, tunnel_id, info, snap_all=snapshot(now), mode=m)


def _try_move(aid: int, tunnel_id: int, info: dict, snap_all: list[dict], mode: str) -> None:
    """Step 3: an Iran server whose tunnels use less of what they can carry (and with room and a good route, the move's own rules) takes the tunnel - by the Doctor's move (mode move, licence) or as a suggestion."""
    from . import doctor, failover

    by_server = loads(snap_all)
    here = by_server.get(info["iran_id"])

    try:
        plan = failover.move_plan(info["iran_id"], None, [tunnel_id])
    except (ValueError, RuntimeError) as e:
        _say(aid, f"A move to another Iran server could not be planned ({str(e)[:140]}).", done=True, ok=False)
        return

    good = lighter(plan.get("candidates") or [], by_server, here)

    if not good:
        _say(aid, "No other Iran server is lighter and has room for this tunnel right now" + (f" ({'; '.join(plan.get('problems') or [])[:200]})" if plan.get("problems") else "")
             + ". What is left is more capacity on the same pair - the load balancing (a second tunnel beside this one) - or another Iran server.", done=True, ok=False)
        return

    target = good[0]
    why = (f"{info['name']} fills its {info['cap']:.0f} Mb/s on {info['iran']} (its tunnels use {round((here or 0) * 100)} % of what they can carry); "
           f"{target['name']}'s use {round(by_server.get(target['id'], 0.0) * 100)} % ({target.get('reason', '')})")

    if mode == "move" and doctor.can_move() and not failover.move_blocked():
        try:
            doctor.move_now(tunnel_id, target["id"], "capacity", why)
            _say(aid, f"The Doctor moves {info['name']} to {target['name']} (the new tunnel is built first, the DNS follows, nobody is cut): {why}.", done=True, ok=True)
            return
        except ValueError as e:
            _say(aid, f"The move is not done now: {str(e)[:160]}.")

    doctor.suggest("capacity", tunnel_id, info["name"], info["iran_id"], info["iran"], target["id"], target["name"], why)
    _say(aid, f"Suggested on the Doctor page: move {info['name']} to {target['name']}. {why}.", done=True, ok=True)


def _lane_swap(aid: int, tunnel_id: int, info: dict, lane: dict) -> None:
    """Step 2, the swap WITHOUT A CUT (see the module's text): hand the new connections to the standby, wait for the old ones to end, build the tunnel again as the better type, take the new connections back."""
    from ..ssh import SSHError
    from . import autopilot, bandwidth, doctor, knowledge, standby

    name, kind = info["name"], lane["kind"]
    snap = autopilot._snapshot(tunnel_id)
    why = f"capacity: {name} fills its {info['cap']:.0f} Mb/s; {kind} carried {float(lane['down_mbps']):.0f} Mb/s in the pair tests (the type it is: {info['kind']})"
    _say(aid, f"Swap without a cut: {info['kind']} → {kind}. {why}")
    doctor.mark_retyped(name, int(time.time()))

    try:
        standby.handover(name, why, manual=True)
    except (ValueError, RuntimeError, SSHError) as e:
        _say(aid, f"The standby could not take the new connections ({str(e)[:140]}): nothing was changed.", done=True, ok=False)
        return

    _say(aid, f"The users' NEW connections go through the standby ({kind}) now; the connections that are open stay on {name} until they close. Waiting for them (up to {DRAIN_LIMIT // 60} minutes).")

    if not autopilot._wait_quiet(aid, tunnel_id, DRAIN_LIMIT):
        _say(aid, f"{name} still carries open connections after {DRAIN_LIMIT // 60} minutes: it is NOT touched (that would cut them). The new connections go back to it; the Doctor tries again tomorrow.", done=True, ok=False)

        try:
            standby.give_back(name, "capacity swap: the open connections did not end")
        except (ValueError, RuntimeError, SSHError) as e:
            _say(aid, f"The way back failed ({str(e)[:140]}): press Back now in the window of the tunnel.")

        return

    _say(aid, f"{name} is quiet: it is built again as {kind} while the users stand on the standby.")
    ok = autopilot._replace(aid, tunnel_id, snap["iran_id"], snap["foreign_id"], kind, "Capacity swap: the old type was full.", snap=snap)

    try:
        standby.give_back(name, "capacity swap: the tunnel is built again")
        _say(aid, "The new connections are on the tunnel again; the standby keeps the connections that are left until they end.")
    except (ValueError, RuntimeError, SSHError) as e:
        _say(aid, f"The way back failed ({str(e)[:140]}): press Back now in the window of the tunnel.")

    if not ok:
        _say(aid, f"The build of {kind} failed; {info['kind']} was put back. Nobody was cut.", done=True, ok=False)
        return

    after = ""

    try:
        with session() as db:
            new_id = db.scalar(select(Tunnel.id).where(Tunnel.name == name))

        bw, _ = bandwidth.measure(new_id)
        down = float(bw.get("down") or 0)
        after = f" It gives {down:g} Mb/s in the Doctor's test now (before: {info['cap']:g})."

        try:
            knowledge.record(snap["iran_id"], snap["foreign_id"], {"kind": kind, "ok": True, "works": True, "down_mbps": down}, "full")
        except Exception:
            log.exception("the speed of %s could not be kept", kind)
    except (ValueError, RuntimeError, SSHError, OSError) as e:
        after = f" It could not be measured just now ({str(e)[:80]}): the Doctor measures it in a few minutes."

    with session() as db:
        emit(db, "ok", "tunnels", f"Tunnel {name} is now {kind} (it was {info['kind']}): the Doctor swapped it without a cut, its capacity was filling.{after}", name)

    _notice(f"✅ {name}: swapped without a cut, {info['kind']} → {kind}, because its capacity was filling.{after}")
    _say(aid, f"{name} runs as {kind} now.{after}", done=True, ok=True)
