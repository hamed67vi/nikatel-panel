"""THE VOLUME OF THE DOCTOR'S OWN TESTS: a daily budget (the owner, 2026-11-05: "does the tunnel type matter for the traffic volume of the Iran servers, and is there a way to save volume?" - the Doctor's speed tests are
traffic too: every test moves up to 120 MB through a tunnel, a tunnel is measured every 90 minutes, and the pair surveys test a score of types).

What it counts: the bytes the streams of every speed test moved (`probe_run` says them: `w_moved`; build_other.parse_probe adds them here), per Tehran day, kept for KEEP_DAYS days. What it does with the count:
  routine  (the 90-minute measurements, the verify surveys, the night survey) go on while the day has used under ROUTINE_SHARE of the budget; from STRETCH's shares on the 90 minutes become 2 and 4 times as long;
  urgent   (a tunnel that dropped or is very slow, a pair nobody has surveyed) go on until the budget is used;
  first    (a tunnel nobody has measured yet: its row shows a dash until its first test - 1.69.265) goes on until the day has used FIRST_SHARE of the budget: ONE light test, never held for hours by a day the surveys and the moves filled;
  cut      (a tunnel that is cut: the repair and its tests) are never held back;
  by hand  (the owner's own "Test now") are never held back.
The budget is `daily_gb` in the settings document `testbudget` (default 30 GB, a day is a Tehran day)."""
from __future__ import annotations

import threading
import time

from sqlalchemy.exc import SQLAlchemyError

from ..log import get_logger
from . import settings as st

log = get_logger("testbudget")
ROUTINE_SHARE = 0.8              # the routine tests stop at this share of the day's budget ...
STRETCH = ((0.5, 2), (0.8, 4))     # ... and from these shares the routine interval is this many times as long
FIRST_SHARE = 1.5                # the first measurement of a new tunnel goes on until the day has used this share (1.69.265: it waited for the next Tehran day at 125 %)
KEEP_DAYS = 14
TEHRAN = 12600                   # UTC+3:30
_LOCK = threading.Lock()


def _day(now: int) -> int:
    return (int(now) + TEHRAN) // 86400


def limit_bytes() -> int:
    try:
        gb = float(st.get("testbudget").get("daily_gb") or 0)
    except (TypeError, ValueError):
        gb = 0.0

    return int(max(gb, 1.0) * 1e9)


def spend(nbytes, what: str = "probe", now: int | None = None) -> None:
    """Count what a test moved. Never raises: a speed test is not stopped by its own bookkeeping."""
    try:
        n = int(nbytes or 0)
    except (TypeError, ValueError):
        return

    if n <= 0:
        return

    now = int(now or time.time())
    day = _day(now)

    try:
        with _LOCK:
            d = st.get("testbudget")
            days = dict(d.get("days") or {})
            used, by = int(d.get("bytes") or 0), dict(d.get("by") or {})

            if int(d.get("day") or 0) != day:
                if d.get("day"):
                    days[str(d["day"])] = used

                used, by = 0, {}
                days = {k: v for k, v in days.items() if int(k) > day - KEEP_DAYS}

            by[what] = int(by.get(what, 0)) + n
            st.put("testbudget", {"day": day, "bytes": used + n, "by": by, "days": days})
    except SQLAlchemyError:
        log.exception("the volume of a test could not be counted")


def used(now: int | None = None) -> int:
    """Bytes the Doctor's tests have moved today."""
    d = st.get("testbudget")
    return int(d.get("bytes") or 0) if int(d.get("day") or 0) == _day(now or time.time()) else 0


def share(now: int | None = None) -> float:
    return used(now) / limit_bytes()


def allow(level: str = "routine", now: int | None = None) -> bool:
    """May a test of this level go on now? "cut" always; "first" until FIRST_SHARE of the budget is used; "urgent" until the budget is used; anything else (routine) until ROUTINE_SHARE of it is."""
    if level == "cut":
        return True

    return share(now) < {"first": FIRST_SHARE, "urgent": 1.0}.get(level, ROUTINE_SHARE)


def stretch(now: int | None = None) -> int:
    """How many times as long as usual the routine interval is today (1, 2 or 4)."""
    s, out = share(now), 1

    for at, mult in STRETCH:
        if s >= at:
            out = mult

    return out


def state(now: int | None = None) -> dict:
    """For the page: the budget, what today has used (per kind of test) and the last days."""
    now = int(now or time.time())
    d = st.get("testbudget")
    today = _day(now)
    cur = int(d.get("bytes") or 0) if int(d.get("day") or 0) == today else 0
    days = [{"day": time.strftime("%Y-%m-%d", time.gmtime(int(k) * 86400)), "gb": round(int(v) / 1e9, 1)} for k, v in sorted(dict(d.get("days") or {}).items(), key=lambda kv: int(kv[0]))[-7:]]
    return {"daily_gb": round(limit_bytes() / 1e9, 1), "used_gb": round(cur / 1e9, 2), "share": round(cur / limit_bytes(), 3), "stretch": stretch(now), "routine": allow("routine", now),
            "by": {k: round(int(v) / 1e9, 2) for k, v in dict(d.get("by") or {}).items()} if cur else {}, "days": days}
