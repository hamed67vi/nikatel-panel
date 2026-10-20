"""LANES: the warm standby of a tunnel put to work BESIDE it, so that a tunnel whose capacity is nearly full gets more capacity without cutting a single customer (the owner, 2026-10-20: "when the capacity of a tunnel
is nearly full - capacity 76, users use 69 - the Doctor must add capacity without cutting any customer: load balancing between tunnels, or anything else that raises capacity"; docs/lanes.md).

  what it is         the standby (services/standby.py) is a real tunnel of another type between the same two servers on shifted ports. A lane makes the Iran server send a RANDOM SHARE of the NEW connections to it:
                     one NAT rule per users' port (`-m statistic --mode random --probability <share> -j REDIRECT --to-ports <shifted>`, ssh.py `lane_on`); the others go on to the tunnel. The NAT table only sees the
                     first packet of a flow and conntrack keeps what it decided: a connection that is open is never touched - nobody is cut; the relief comes as connections end and new ones are spread.
  the share          to the standby: cap_s / (cap_p + cap_s), clamped to 10 - 90 %. cap_p = the usual level of the tunnel's light test (bandwidth `ref`, else its last test), cap_s = one light test of the standby
                     (`bandwidth.measure`, asked when the lane starts and again after 6 hours). A standby that would add under max(10 Mb/s, 25 % of cap_p) is no lane. The rule is REPLACED in place when the share moves 8 points.
  when it starts     the owner switched lanes on (settings `lanes.on`, OFF by default), the Doctor is in mode fix / move, the panel is not read-only, both tunnels have been up for a minute, no repair, no storm, calm servers,
                     and the Doctor's own detector (services/capacity.py: the users use 85 % of the capacity in 60 % of the last ten minutes, step 1 of the owner's wish) has called the tunnel FULL for four minutes in a row -
                     after its story (the swap without a cut, the move: steps 2 and 3) has had its turn: a lane waits while `capacity <tunnel>` works. The owner's "Share now" starts one by hand (no load condition, it stays until Stop).
  when it stops      the TOTAL load (tunnel + lane) under 50 % of cap_p for 20 minutes, the lane at least 30 minutes old (85 / 50: no flapping); the owner's Stop; switching lanes (or the Doctor) off; the standby going away.
  failures           the standby not up (or no longer fitting): the lane is PARKED - every new connection goes to the tunnel, the wish stays and the lane is put again when both tunnels have been up for a minute. The
                     tunnel not up: the standby's own handover (a full redirect ABOVE the lane rule) sends everything to the standby. A lane rule shadowed by the parked RETURN of the standby's way back is freed
                     (`lane_on` takes that RETURN off); rules lost in a reboot or a firewall flush are put again; rules nobody asked for are parked. No boot unit: after a reboot everything goes to the tunnel.
  taking it off      PARK=1 (the careful way: a tagged RETURN first, then the redirect - deleting the LAST rule of a NAT chain resets what the kernel translated), later PARK=2 which removes the parked rules only when no
                     connection to the shifted ports is left; PARK=0 when the standby itself is removed. DNS is never touched.

The pure helpers (`share_pct`, `prob_text`, `cap_of`, `is_full`, `is_idle`, `decide`, `parse_list`) carry the decisions and are tested."""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel, is_standby
from ..ssh import SSHError, run_action, run_script
from . import actions, autopilot, bandwidth, capacity, livebw, standby
from . import settings as st
from .events import emit

log = get_logger("lanes")
FULL_FOR = 240                # the Doctor's detector (capacity.py) must have called the tunnel FULL this long: its own story (which starts after 3 minutes of FULL) is on its way before a lane is
LOW = 0.50                   # the total load under this share of the tunnel's capacity ...
IDLE_WINDOW = 1200            # ... for this long (in 95 % of the samples) ends a lane ...
IDLE_SPAN = 1140
MIN_AGE = 1800                # ... that is at least this old
QUIET_AFTER = 3600            # after a lane ended by itself no automatic one starts for this long
UP_FOR = 60                   # both tunnels must have been up this long before a lane is put or resumed
CAP_MIN_P = 20.0              # a tunnel whose capacity is under this is "very slow" (bandwidth.SLOW_MBPS): its capacity is not a number to share by
CAP_MIN_S = 10.0              # the standby must add at least this much ...
CAP_MIN_RATIO = 0.25          # ... and this share of the tunnel's capacity
SHARE_MIN, SHARE_MAX = 10, 90
ADJUST_POINTS = 8             # the rule is rewritten when the share moved this many points ...
ADJUST_EVERY = 600            # ... and not more often than this
CAP_FRESH = 6 * 3600          # a measurement of the standby is used this long
CAP_RETRY = 1800              # a standby that could not be measured is left alone this long
BW_FRESH = 7 * 86400          # the tunnel's own test must be this fresh to count as its capacity
LIVE_FRESH = 75.0             # a live number older than this (seconds) is not a sample (the servers are read every 30 s when nobody looks at the page, every 5 s when somebody does)
RING_KEEP = 1500              # seconds of samples kept (in memory)
LIST_EVERY = 20               # the lane rules of a server that has a lane are read this often ...
IDLE_LIST_EVERY = 300         # ... and of one that had one once, this often
BACKOFF = 60                  # a tunnel whose lane action failed is left alone this long
IDLE_MIN = 30                 # samples needed in the window of a stop (they come every 30 s when nobody looks at the page)
PARKED_MIN = 180              # a parked lane is cleaned (PARK=2) after this, then again every PARKED_MIN
_LOCK = threading.Lock()
_RING: dict[str, deque] = {}               # tunnel name -> (ts, tunnel Mb/s, lane Mb/s) while a lane works (for the stop)
_FULL: dict[str, float] = {}               # tunnel name -> since when the Doctor's detector calls it FULL (memory only)
_NOTE: dict[str, str] = {}                 # tunnel name -> why no lane is on now (the tunnel window shows it)
_STATE = {"listed": {}, "cleaned": {}, "backoff": {}}


# ------------------------------------------------------------------ pure helpers (tested)
def share_pct(cap_p: float | None, cap_s: float | None) -> int | None:
    """The share of the NEW connections that goes to the standby, in percent: cap_s / (cap_p + cap_s), kept between 10 and 90. None when a capacity is unknown or the standby would add too little."""
    if not cap_p or not cap_s or cap_p <= 0 or cap_s <= 0:
        return None

    if cap_s < max(CAP_MIN_S, CAP_MIN_RATIO * cap_p):
        return None

    return max(SHARE_MIN, min(SHARE_MAX, round(100 * cap_s / (cap_p + cap_s))))


def prob_text(pct: int) -> str:
    """'0.40000000' - what `-m statistic --probability` takes."""
    return f"{max(1, min(99, int(pct))) / 100:.8f}"


def cap_of(bw: dict | None, now: int) -> float | None:
    """What a tunnel can carry, from its light test (bandwidth.py): the usual level of the last week, else the last sample, never below the sample (a tunnel that tests better than usual). None: no fresh sample, or under CAP_MIN_P."""
    bw = bw or {}

    if not bw.get("down") or now - int(bw.get("at") or 0) > BW_FRESH:
        return None

    c = max(float(bw["down"]), float(bw.get("ref") or 0))
    return c if c >= CAP_MIN_P else None


def _window(samples, now: float, secs: float) -> list:
    return [x for x in samples if now - x[0] <= secs]


def is_full(state: str, since: float | None, now: float) -> bool:
    """The Doctor's detector (capacity.py `state_of`) calls the tunnel FULL, and has done so for FULL_FOR seconds (`since`: when it first did, in memory)."""
    return state == "full" and since is not None and now - since >= FULL_FOR


def is_idle(samples, now: float, cap_p: float | None) -> bool:
    """The lane is not needed: for IDLE_WINDOW seconds (95 % of the samples, covering IDLE_SPAN) the TOTAL load of the tunnel and the lane was under LOW of the tunnel's capacity."""
    w = _window(samples, now, IDLE_WINDOW)

    if not cap_p or len(w) < IDLE_MIN or w[-1][0] - w[0][0] < IDLE_SPAN:
        return False

    return sum(1 for _, p, s in w if p + s < LOW * cap_p) >= 0.95 * len(w)


def parse_list(out: str) -> tuple[dict[str, list[dict]], dict[str, list[tuple[str, int]]]]:
    """The answer of `lane_list` -> ({standby name: [{proto, port, to, prob, shadow}]}, {standby name: [(proto, port)]} - the parked RETURNs)."""
    lanes: dict[str, list[dict]] = {}
    parked: dict[str, list[tuple[str, int]]] = {}

    for line in str(out).splitlines():
        p = line.split()

        if len(p) == 7 and p[0] == "LANE" and p[3].isdigit() and p[4].isdigit() and p[6].isdigit():
            try:
                lanes.setdefault(p[1], []).append({"proto": p[2], "port": int(p[3]), "to": int(p[4]), "prob": float(p[5]), "shadow": int(p[6])})
            except ValueError:
                continue
        elif len(p) == 4 and p[0] == "PARKED" and p[3].isdigit():
            parked.setdefault(p[1], []).append((p[2], int(p[3])))

    return lanes, parked


def decide(p: dict | None, s: dict | None, w: dict, ap: dict | None, load: dict, *, now: int, enabled: bool, auto: bool, storm: bool = False, repairing: bool = False, calm: str = "") -> tuple[str, str]:
    """(what to do now, why): 'wait', 'measure', 'start', 'adjust', 'resume', 'park', 'stop' or 'clean'. Pure. `p`: the tunnel {"state", "state_since", "last_check_at", "off"} or None; `s`: its standby {"state", "state_since", "stale",
    "use" (a handover stands)} or None; `w`: the wish {"want", "manual", "stopped_at"}; `ap`: what is on the server {"state": on | parked, "pct", "since", "changed", "parked_at"} or None; `load`: {"cap_p", "cap_s", "pct", "hot", "idle"};
    `enabled`: the owner switched lanes on; `auto`: the Doctor is in mode fix or move and the panel may write; `calm`: why the servers are too busy for a second tunnel ('' = calm)."""
    want, manual = bool(w.get("want")), bool(w.get("manual"))
    state = (ap or {}).get("state", "")
    automatic = enabled and auto

    if p is None or s is None:
        return "wait", "the tunnel is being rebuilt or has no standby: nothing is touched meanwhile" if (ap or want) else "a lane needs a ready standby tunnel"

    s_up = s["state"] == "up" and not s["stale"]
    p_up = p["state"] == "up" and not p["off"]
    both = (p_up and s_up and now - p["state_since"] >= UP_FOR and now - s["state_since"] >= UP_FOR and now - p["last_check_at"] <= 120
            and not repairing and not storm and not s.get("use"))

    if state == "on":
        if not s_up:
            return "park", "the second tunnel is not up: every new connection goes to the tunnel until it is"

        if not manual and not automatic:
            return "stop", "automatic lanes are switched off"

        if s.get("use") or not p_up:
            return "wait", "the tunnel is cut: its standby carries everything (the lane waits)"

        if not manual and load.get("idle") and now - int(ap.get("since") or now) >= MIN_AGE:
            return "stop", "the load stayed under half of the tunnel's capacity for 20 minutes: the lane is not needed"

        pct = load.get("pct")

        if pct is not None and abs(pct - int(ap.get("pct") or 0)) >= ADJUST_POINTS and now - int(ap.get("changed") or ap.get("since") or 0) >= ADJUST_EVERY:
            return "adjust", f"the capacities moved: the share goes from {ap.get('pct')} % to {pct} %"

        return "wait", "sharing"

    if state == "parked":
        if not want:
            return ("clean", "the parked rules are taken off when no connection is left on them") if now - int(ap.get("parked_at") or 0) >= PARKED_MIN else ("wait", "the lane was ended: its parked rules go soon")

        if not manual and not automatic:
            return "stop", "automatic lanes are switched off"

    if want and state != "on":
        if not manual and not automatic:
            return "stop", "automatic lanes are switched off"

        if not both:
            return "wait", "the lane waits until both tunnels have been up for a minute"

        if not manual and load.get("idle"):
            return "stop", "the load is low: the lane is not needed any more"

        if calm:
            return "wait", calm

        return "resume", "both tunnels are up: the lane is put again"

    if not automatic:
        return "wait", "automatic lanes are off" if not enabled else "the Doctor is off (or the panel is read-only): the buttons still work"

    if not both:
        return "wait", "a lane is considered when both tunnels are up and nothing repairs them"

    if calm:
        return "wait", calm

    if now - int(w.get("stopped_at") or 0) < QUIET_AFTER:
        return "wait", "a lane ended a short while ago: the next one waits"

    if not load.get("cap_p"):
        return "wait", "the capacity of the tunnel is not measured yet"

    if not load.get("hot"):
        return "wait", "the tunnel has room (a lane starts when the Doctor has called its capacity FULL for four minutes)"

    if load.get("cap_s") is None:
        return "measure", "the tunnel is nearly full: the standby's capacity is measured first"

    if load.get("pct") is None:
        return "wait", "the standby would add too little capacity to be a lane"

    return "start", "the users' load stayed at 85 % of the tunnel's capacity or more for most of 10 minutes"


# ------------------------------------------------------------------ what is kept
def enabled() -> bool:
    return bool(st.get("lanes").get("on"))


def set_enabled(on: bool) -> dict:
    st.put("lanes", {"on": bool(on)})
    wake()
    return {"on": bool(on)}


def _wishes() -> dict:
    return dict(st.get("lanes").get("tunnels") or {})


def _wish(name: str) -> dict:
    return dict(_wishes().get(name) or {})


def _set_wish(name: str, changes: dict | None) -> None:
    """Merge `changes` into the wish of a tunnel (None: forget it)."""
    with _LOCK:
        d = st.get("lanes")
        t = dict(d.get("tunnels") or {})

        if changes is None:
            t.pop(name, None)
        else:
            t[name] = {**(t.get(name) or {}), **changes}

        st.put("lanes", {"tunnels": t})


def _touch(server_id: int) -> None:
    with _LOCK:
        touched = list(st.get("lanes").get("touched") or [])

        if server_id not in touched:
            st.put("lanes", {"touched": touched + [server_id]})


def renamed(old: str, new: str) -> None:
    """A tunnel was renamed: its wish follows."""
    w = _wishes()

    if old in w:
        _set_wish(new, w[old])
        _set_wish(old, None)

    if old in _RING:
        _RING[new] = _RING.pop(old)


def forget(name: str) -> None:
    """The owner removed the tunnel: its wish goes (the rules go with its standby, see `release`)."""
    _set_wish(name, None)
    _RING.pop(name, None)
    _NOTE.pop(name, None)


def wake() -> None:
    try:
        jobs.trigger("lanes")
    except KeyError:
        pass                      # not registered (tests)


def _set_lane(standby_id: int, lane: dict | None) -> None:
    """What is on the server for this standby (`record.lane`), written BEFORE the action (if the panel stops in the middle, the next start knows the rules may stand)."""
    with session() as db:
        t = db.get(Tunnel, standby_id)

        if t is None:
            return

        rec = dict(t.record or {})

        if lane is None:
            rec.pop("lane", None)
        else:
            rec["lane"] = lane

        t.record = rec


# ------------------------------------------------------------------ the actions on the Iran server
def _act(iran, action: str, params: dict) -> str:
    return run_action(iran, action, params, timeout=40)


def _off(iran, sb_name: str, park: int) -> tuple[int, int, int]:
    """lane_off: (redirects left, rules of the tag left, connections on the shifted ports). RuntimeError when the answer is not understood."""
    out = _act(iran, "lane_off", {"NAME": sb_name, "PARK": str(park)})
    m = re.search(rf"LANE_OFF {re.escape(sb_name)} (\d+) (\d+) (\d+)", out)

    if not m:
        raise RuntimeError("the lane rules were not taken off: " + out.strip()[-160:])

    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _put(iran, sb_name: str, pct: int, pairs: str, protos: str) -> None:
    out = _act(iran, "lane_on", {"NAME": sb_name, "PROB": prob_text(pct), "MAP": pairs, "PROTOS": protos})
    m = re.search(rf"LANE_ON {re.escape(sb_name)} (\d+)", out)
    want = len(pairs.split(",")) * len(protos.split())

    if not m or int(m.group(1)) != want:
        raise RuntimeError("the lane rules were not put: " + (out.split("LANE_FAIL", 1)[-1].strip() if "LANE_FAIL" in out else out.strip()[-160:]))


def _redirects_off(iran, sb_name: str) -> None:
    """Take every lane rule of this standby off at once (the standby is going away): RuntimeError when the server does not say it is clean."""
    left = _off(iran, sb_name, 0)

    if left[1]:
        raise RuntimeError(f"{left[1]} lane rule(s) are still on the server")


def release(name: str, s: dict) -> None:
    """The standby is being removed: its lane rules go first (they would send new connections to a port nobody listens on). RuntimeError when the Iran server does not take them off - the standby is then KEPT. The wish stays."""
    if not s.get("lane") or s.get("iran_off"):
        return

    _redirects_off(standby._node(*s["iran"]), s["name"])
    _set_lane(s["id"], None)
    _NOTE[name] = "the second tunnel is being replaced: the lane is put again when it is ready"


# ------------------------------------------------------------------ capacities
def _bw_of(tunnel_id: int) -> dict:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        return dict((t.record or {}).get("bw") or {}) if t is not None else {}


def _cap_s(name: str, s: dict, measure: bool, force: bool = False) -> float | None:
    """The capacity of the standby: the number kept when it is fresh, else one light test (when `measure`; `force`: the owner pressed the button - a failed test a short while ago is no reason to refuse). ValueError with the reason
    when it cannot be measured now."""
    now = int(time.time())
    w = _wish(name)

    if w.get("cap_s") and w.get("cap_s_kind") == s["kind"] and now - int(w.get("cap_s_at") or 0) < CAP_FRESH:
        return float(w["cap_s"])

    if not measure:
        return None

    if now - int(w.get("cap_fail_at") or 0) < CAP_RETRY and not force:
        raise ValueError("the second tunnel could not be measured a short while ago: " + str(w.get("cap_fail_why") or "")[:100])

    try:
        bw, _ = bandwidth.measure(s["id"])
        down = float(bw.get("down") or 0)
    except (ValueError, RuntimeError, SSHError) as e:
        _set_wish(name, {"cap_fail_at": now, "cap_fail_why": str(e)[:160]})
        raise ValueError(f"the second tunnel could not be measured: {str(e)[:140]}")

    if down <= 0:
        _set_wish(name, {"cap_fail_at": now, "cap_fail_why": "it carried nothing in the test"})
        raise ValueError("the second tunnel carried nothing in its test")

    _set_wish(name, {"cap_s": round(down, 1), "cap_s_at": now, "cap_s_kind": s["kind"], "cap_fail_at": 0})
    return down


# ------------------------------------------------------------------ start, adjust, park, stop
def _apply(name: str, p: dict, s: dict, pct: int, manual: bool, why: str) -> dict:
    """Put (or rewrite) the lane rules on the Iran server. The record is written first; a failure puts back what was there."""
    now = int(time.time())
    pairs = standby.nat_map(p["ports"], s["ports"])
    protos = "tcp udp" if s["udp"] else "tcp"
    iran = standby._node(*p["iran"])
    prev = s.get("lane") or None
    on = bool(prev and prev.get("state") == "on")
    lane = {"state": "on", "pct": int(pct), "map": pairs, "protos": protos, "since": int(prev["since"]) if on else now, "changed": now, "iran_id": p["iran_id"], "manual": bool(manual)}
    _set_lane(s["id"], lane)

    try:
        _put(iran, s["name"], pct, pairs, protos)
    except (SSHError, ValueError, RuntimeError) as e:
        if on:
            _set_lane(s["id"], prev)
        else:
            try:
                _redirects_off(iran, s["name"])
            except (SSHError, ValueError, RuntimeError):
                pass

            _set_lane(s["id"], prev if prev else None)

        raise RuntimeError(f"the lane could not be put: {e}")

    _touch(p["iran_id"])
    return lane


def start(name: str, manual: bool = False, why: str = "") -> dict:
    """Start a lane for tunnel `name` now (the owner's button, or the job): the standby's capacity is measured when it is not known, the share is worked out, the rules are put. ValueError says why it cannot be;
    RuntimeError when the server did not take the rules."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only: nothing is changed")

    def check(v):
        p, s = v["p"], v["s"]

        if p is None or s is None:
            raise ValueError("this tunnel has no standby: a lane needs a ready second tunnel")

        if s["stale"]:
            raise ValueError("the standby does not fit the tunnel any more: " + s["stale"])

        if s["state"] != "up":
            raise ValueError(f"the standby is {s['state']}: a lane needs it up")

        if p["state"] != "up":
            raise ValueError(f"the tunnel is {p['state']}: a lane is put when it is up")

        if s["use"]:
            raise ValueError("the users' ports are on the standby now (a handover): a lane waits for the way back")

        if not p["ports"]:
            raise ValueError("this tunnel forwards no port")

        return p, s

    p, s = check(standby._view(name))
    now = int(time.time())
    cap_p = cap_of(_bw_of(p["id"]), now)
    cap_s = _cap_s(name, s, measure=True, force=manual)             # (the test of the standby runs OUTSIDE the lock: a handover must never wait for it)
    pct = share_pct(cap_p, cap_s) if cap_p else None

    if pct is None:
        raise ValueError("the tunnel's capacity is not measured yet" if not cap_p else f"the second tunnel ({cap_s:.0f} Mb/s) would add too little to the tunnel ({cap_p:.0f} Mb/s): no lane")

    with standby._lock(name):
        p, s = check(standby._view(name))                # (looked at again: a minute may have passed)
        lane = _apply(name, p, s, pct, manual, why)
        _set_wish(name, {"want": True, "manual": bool(manual), "since": lane["since"], "pct": pct, "cap_p": round(cap_p, 1), "why": why or ("by hand" if manual else "the tunnel is nearly full")})

    _NOTE.pop(name, None)

    with session() as db:
        emit(db, "info", "tunnels", f"Lane: {name} shares its new connections with its second tunnel ({s['kind']}): {100 - pct} % + {pct} % (capacity {cap_p:.0f} + {cap_s:.0f} Mb/s). Open connections are not touched.", name)

    log.info("lanes: %s shares %s %% to %s (%s)", name, pct, s["name"], why or "by hand")
    return {"ok": True, "pct": pct, "cap_p": cap_p, "cap_s": cap_s}


def adjust(name: str, pct: int) -> None:
    with standby._lock(name):
        v = standby._view(name)
        p, s = v["p"], v["s"]

        if p is None or s is None or (s.get("lane") or {}).get("state") != "on":
            return

        _apply(name, p, s, pct, bool(s["lane"].get("manual")), "")

    log.info("lanes: %s share changed to %s %%", name, pct)


def park(name: str, why: str = "") -> bool:
    """Take the lane off the careful way (new connections to the tunnel, open ones stay); the wish stays, so it is put again. Returns whether rules were taken off."""
    with standby._lock(name):
        s = standby._view(name)["s"]

        if s is None or not s.get("lane") or s["lane"].get("state") == "parked":
            return False

        if not s["iran_off"]:
            _off(standby._node(*s["iran"]), s["name"], 1)

        _set_lane(s["id"], {**s["lane"], "state": "parked", "parked_at": int(time.time())})

    log.info("lanes: %s parked (%s)", name, why)
    return True


def stop(name: str, why: str = "") -> dict:
    """End the lane: parked now (the careful way), the wish is gone; the parked rules are removed later when no connection is left on them."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only: nothing is changed")

    _set_wish(name, {"want": False, "manual": False, "stopped_at": int(time.time())})
    parked = park(name, why or "stopped")

    if parked:
        with session() as db:
            emit(db, "info", "tunnels", f"Lane of {name} ended. New connections go to the tunnel; the ones open on the second tunnel finish there.", name)

        log.info("lanes: %s ended (%s)", name, why or "by hand")

    _NOTE.pop(name, None)
    return {"ok": True}


def clean(name: str) -> None:
    """The parked rules of a lane are removed - only when no connection to the shifted ports is left (the script says; otherwise they stay and it is asked again later)."""
    with standby._lock(name):
        s = standby._view(name)["s"]

        if s is None or not s.get("lane") or s["lane"].get("state") != "parked" or s["iran_off"]:
            return

        _, rules, conns = _off(standby._node(*s["iran"]), s["name"], 2)

        if rules == 0:
            _set_lane(s["id"], None)
            log.info("lanes: the parked rules of %s are gone", name)
        else:
            _set_lane(s["id"], {**s["lane"], "parked_at": int(time.time())})
            log.info("lanes: %s still has %s connection(s) on the second tunnel: its parked rules stay", name, conns)


def refresh(name: str) -> None:
    """After the standby's way back (it parks a RETURN above the lane rule): put the lane rules again at once, so they are not dead until the next look of the job."""
    try:
        with standby._lock(name):
            v = standby._view(name)
            p, s = v["p"], v["s"]

            if p is None or s is None or (s.get("lane") or {}).get("state") != "on" or s["state"] != "up":
                return

            lane = s["lane"]
            _put(standby._node(*p["iran"]), s["name"], int(lane["pct"]), lane["map"], lane["protos"])
    except (SSHError, ValueError, RuntimeError) as e:
        log.info("lanes: %s could not be refreshed after the way back: %s", name, str(e)[:120])


# ------------------------------------------------------------------ the numbers of the users
def _live_at(tunnel_id: int | None, view: dict) -> tuple[float, float] | None:
    """(the users' rate down in Mb/s, when it was read) for a tunnel, from livebw.view(); None when there is no fresh number."""
    r = view.get(str(tunnel_id)) if tunnel_id is not None else None

    if not r or float(r["age"] if r.get("age") is not None else 999) > LIVE_FRESH or r.get("down") is None:
        return None

    return float(r["down"]), float(r["at"])


def _live(tunnel_id: int | None, view: dict) -> float | None:
    r = _live_at(tunnel_id, view)
    return r[0] if r else None


def _sample(name: str, p: dict, s: dict | None, ap: dict | None, view: dict) -> None:
    """One look at the users' rate of the tunnel and of the lane (memory only; the window is RING_KEEP seconds)."""
    rp = _live_at(p["id"], view)
    rs = _live_at(s["id"], view) if s is not None else None
    on = bool(ap and ap.get("state") == "on")

    if rp is None or (on and rs is None):
        return

    ring = _RING.setdefault(name, deque())
    at = rp[1]

    if ring and at <= ring[-1][0]:
        return                          # (the same read as the last one: the servers are read every 30 s, this job looks every 10)

    ring.append((at, rp[0], rs[0] if rs else 0.0))

    while ring and at - ring[0][0] > RING_KEEP:
        ring.popleft()


def _track_full(name: str, now: float) -> bool:
    """Is the tunnel FULL by the Doctor's detector (services/capacity.py, step 1 of the owner's wish) for FULL_FOR seconds? (`_FULL` keeps since when; the detector's state lives in memory too and is empty for a minute after a start.)"""
    state = capacity.state_of(name)

    if state == "full":
        _FULL.setdefault(name, now)
    else:
        _FULL.pop(name, None)

    return is_full(state, _FULL.get(name), now)


def _load(name: str, p: dict, s: dict | None, w: dict, hot: bool) -> dict:
    now = time.time()
    ring = list(_RING.get(name) or [])
    cap_p = cap_of(_bw_of(p["id"]), int(now))
    cap_s = float(w["cap_s"]) if s is not None and w.get("cap_s") and w.get("cap_s_kind") == s["kind"] and now - int(w.get("cap_s_at") or 0) < CAP_FRESH else None
    return {"cap_p": cap_p, "cap_s": cap_s, "pct": share_pct(cap_p, cap_s) if cap_p and cap_s else None, "hot": hot, "idle": is_idle(ring, now, cap_p)}


def _story_busy(name: str) -> bool:
    """The Doctor's story for a full tunnel (capacity.py: the swap without a cut, the move) is working on it: a lane waits for its turn."""
    return f"capacity {name}" in (getattr(autopilot, "_BUSY", None) or {})


def _calm(p: dict) -> str:
    return standby._busy_reason(p["iran_id"], p["foreign_id"])


# ------------------------------------------------------------------ the job
def _step(name: str, view: dict) -> None:
    now = int(time.time())
    v = standby._view(name)
    p, s = v["p"], v["s"]
    w = _wish(name)
    ap = (s or {}).get("lane") or None
    auto = actions.doctor_mode() != "off" and not get_settings().read_only

    if p is None:
        return

    if s is None and not w.get("want") and name not in standby._marks():
        _set_wish(name, None)
        return

    if s is not None and ap and ap.get("state") == "on":
        _sample(name, p, s, ap, view)                 # (the users' rate on both lanes, for the stop; nothing is kept while no lane works)
    else:
        _RING.pop(name, None)

    hot = _track_full(name, time.time())

    if time.time() < _STATE["backoff"].get(name, 0):
        return

    for _ in range(2):                  # (the second round: the capacity of the standby was just measured - the decision goes on at once)
        w = _wish(name)
        load = _load(name, p, s, w, hot)
        calm = _calm(p) if (s is not None and not ap and not w.get("want") and load["hot"]) or (w.get("want") and not ap) else ""
        act, why = decide(p, s, w, ap, load, now=now, enabled=enabled(), auto=auto, storm=autopilot.storm_now() if (load["hot"] or w.get("want")) and not ap else False,
                          repairing=name in autopilot.repairing_names() or _story_busy(name), calm=calm)
        _NOTE[name] = why

        if act != "measure":
            break

        try:
            _cap_s(name, s, measure=True)
        except ValueError as e:
            _NOTE[name] = str(e)
            return

    if act == "start":
        try:
            start(name, manual=False, why=why)
        except (ValueError, RuntimeError, SSHError) as e:
            _NOTE[name] = str(e)
    elif act == "resume":
        pct = load["pct"] or int(w.get("pct") or 0)

        if not pct:
            _NOTE[name] = "the lane cannot be put again: the capacities are not known"
        else:
            with standby._lock(name):
                _apply(name, p, s, pct, bool(w.get("manual")), "")

            log.info("lanes: %s lane put again (%s)", name, why)
    elif act == "adjust":
        adjust(name, int(load["pct"]))
    elif act == "park":
        park(name, why)
    elif act == "stop":
        stop(name, why)
    elif act == "clean" and time.time() - _STATE["cleaned"].get(name, 0) >= PARKED_MIN:
        _STATE["cleaned"][name] = time.time()
        clean(name)


def _safe_step(args) -> None:
    name, view = args

    try:
        _step(name, view)
    except (ValueError, RuntimeError, SSHError) as e:
        _STATE["backoff"][name] = time.time() + BACKOFF
        _NOTE[name] = str(e)[:200]
        log.warning("lanes: %s: %s", name, str(e)[:200])
    except Exception:
        _STATE["backoff"][name] = time.time() + BACKOFF
        log.exception("lanes: %s crashed", name)


def reconcile() -> None:
    """The lane rules that stand on the Iran servers against what the panel believes: a lane that should be on and whose rules are gone (a reboot, a flushed firewall) is put again; a rule that is shadowed is freed;
    rules nobody asked for (the lane was parked or ended, the row is gone) are taken off the careful way."""
    with session() as db:
        servers = {sv.id: sv for sv in db.scalars(select(Server).where(Server.enabled.is_(True), Server.online.is_(True)))}
        rows: dict[int, dict[str, dict]] = {}

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))):
            lane = (t.record or {}).get("lane")

            if is_standby(t) and lane:
                rows.setdefault(t.iran_server_id, {})[t.name] = {"lane": lane, "standby_of": str((t.record or {}).get("standby_of")), "state": t.state, "id": t.id}

        sids = set(rows) | {int(x) for x in st.get("lanes").get("touched") or []}
        nodes = {i: standby._node(servers[i].host, servers[i].ssh_user, servers[i].ssh_port) for i in sids if i in servers}
        standbys = {t.name for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))) if is_standby(t)}

    now = time.time()

    for sid, node in nodes.items():
        if now - _STATE["listed"].get(sid, 0) < (LIST_EVERY if rows.get(sid) else IDLE_LIST_EVERY):
            continue

        _STATE["listed"][sid] = now

        try:
            lanes, parked = parse_list(run_script(node, "lane_list", timeout=20))
        except (SSHError, ValueError) as e:
            log.info("lanes: the rules of a server could not be read: %s", str(e)[:100])
            continue

        mine = rows.get(sid, {})

        for sb_name, rules in lanes.items():
            r = mine.get(sb_name)

            if r is None or r["lane"].get("state") != "on":
                log.warning("lanes: lane rules of %s stand on a server and nobody asked for them: taken off", sb_name)

                try:
                    _off(node, sb_name, 1 if sb_name in standbys else 0)
                except (SSHError, ValueError, RuntimeError) as e:
                    log.warning("lanes: could not take them off: %s", str(e)[:120])
            elif any(x["shadow"] for x in rules):
                lane = r["lane"]

                try:
                    _put(node, sb_name, int(lane["pct"]), lane["map"], lane["protos"])
                    log.info("lanes: the lane of %s was shadowed by a RETURN of the standby's way back: freed", sb_name)
                except (SSHError, ValueError, RuntimeError) as e:
                    log.warning("lanes: could not free the lane: %s", str(e)[:120])

        for sb_name, r in mine.items():
            lane = r["lane"]

            if lane.get("state") == "on" and sb_name not in lanes and r["state"] == "up":
                log.warning("lanes: the lane rules of %s are gone from the server: put again", sb_name)

                try:
                    _put(node, sb_name, int(lane["pct"]), lane["map"], lane["protos"])
                except (SSHError, ValueError, RuntimeError) as e:
                    log.warning("lanes: could not put them again: %s", str(e)[:120])

        for sb_name in parked:
            if sb_name not in standbys:
                try:
                    _off(node, sb_name, 0)                  # (the standby is gone: nothing is translated by these rules any more)
                except (SSHError, ValueError, RuntimeError) as e:
                    log.warning("lanes: could not take the parked rules of a removed standby off: %s", str(e)[:120])


def run() -> None:
    """The job (every 10 s): for every tunnel with a ready standby (or a wish or a lane): the look at the users' rate, the decision, the action; every 20 s the lane rules on the Iran servers are read."""
    if jobs.stopping():
        return

    on = enabled()
    names = set(_wishes())

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))):
            if is_standby(t) and (t.record or {}).get("lane"):
                names.add(str((t.record or {}).get("standby_of")))

    if on:
        names |= set(standby._marks())

    if not names and not st.get("lanes").get("touched"):
        return

    view = livebw.view()

    if names:
        with ThreadPoolExecutor(min(4, len(names))) as ex:
            list(ex.map(_safe_step, [(n, view) for n in sorted(names)]))

    try:
        reconcile()
    except Exception:
        log.exception("lanes: reconcile")


# ------------------------------------------------------------------ what the tunnel window shows
def summaries(names: list[str]) -> dict[str, dict]:
    """{tunnel name: what the tunnel window shows of its lane}: state (off · idle · waiting · sharing · paused), why, the share to the second tunnel, both capacities, the users' rate on both, since, by hand."""
    out = {}
    on = enabled()
    views = standby._views(list(names))
    live = livebw.view()
    now = int(time.time())

    for name in names:
        v = views[name]
        p, s = v["p"], v["s"]
        w = _wish(name)
        ap = (s or {}).get("lane") or None
        state = (ap or {}).get("state", "")

        if state == "on":
            st_ = "sharing"
        elif state == "parked" or w.get("want"):
            st_ = "paused"
        elif not on:
            st_ = "off"
        elif s is None or s["state"] != "up":
            st_ = "waiting"
        else:
            st_ = "idle"

        why = _NOTE.get(name, "") if st_ != "off" else ""
        cap_p = cap_of(_bw_of(p["id"]), now) if p is not None and st_ != "off" else None
        pct = int((ap or {}).get("pct") or w.get("pct") or 0)
        out[name] = {"on": on, "state": st_, "why": why, "pct": pct if st_ in ("sharing", "paused") else 0, "kind": (s or {}).get("kind", ""), "manual": bool(w.get("manual")),
                     "since": int((ap or {}).get("since") or 0) if st_ == "sharing" else 0, "cap_p": round(cap_p, 1) if cap_p else None, "cap_s": w.get("cap_s") if st_ in ("sharing", "paused") else None,
                     "use_p": _live(p["id"], live) if p is not None and st_ != "off" else None, "use_s": _live(s["id"], live) if s is not None and st_ == "sharing" else None}

    return out


def status(name: str) -> dict:
    return summaries([name])[name]
