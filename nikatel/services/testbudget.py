"""THE VOLUME OF THE DOCTOR'S OWN TESTS: a daily budget (the owner, 2026-11-05: "does the tunnel type matter for the traffic volume of the Iran servers, and is there a way to save volume?" - the Doctor's speed tests are
traffic too: every test moves up to 120 MB through a tunnel, a tunnel is measured every 90 minutes, and the pair surveys test a score of types).

What it counts: the bytes the streams of every speed test moved (`probe_run` says them: `w_moved`; build_other.parse_probe adds them here), per Tehran day, kept for KEEP_DAYS days. What it does with the count:
  routine  (the 90-minute measurements, the verify surveys, the night survey) go on while the day has used under ROUTINE_SHARE of the budget; from STRETCH's shares on the 90 minutes become 2 and 4 times as long;
  urgent   (a tunnel that dropped or is very slow, a pair nobody has surveyed) go on until the budget is used;
  first    (a tunnel nobody has measured yet: its row shows a dash until its first test - 1.69.265) goes on until the day has used FIRST_SHARE of the budget: ONE light test, never held for hours by a day the surveys and the moves filled;
           (1.69.276) and over FIRST_SHARE too, a few times: `first_allowed` gives a tunnel FIRST_TRIES tries a day and all the tunnels together FIRST_TOTAL - a day at 184 % left a tunnel rebuilt that afternoon with a dash until the next Tehran day;
  cut      (a tunnel that is cut: the repair and its tests) are never held back;
  by hand  (the owner's own "Test now") are never held back.
The budget is `daily_gb` in the settings document `testbudget` (default 30 GB, a day is a Tehran day).
(1.69.282) Every count carries the NAME of what asked for the test (`by`, the bytes; `tests`, how many; the last days keep their `days_by`): "where does the volume go" is read, not worked out.
(1.69.280) The routine speed tests are asked once in `speed_every()` - `speed_days` in the same document, 10 days by default (the owner, 2026-11-15: "تست سرعت هر 10 روز یک بار انجام بشه"): one speed test per kind of a live pair
(the verify survey) and per tunnel (the bandwidth job). The pings, the latency tests and the tests that something asks for (a new tunnel, a drop, a poor score, a repair, the owner) are not held by it."""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time

from sqlalchemy.exc import SQLAlchemyError

from ..log import get_logger
from . import settings as st

log = get_logger("testbudget")
ROUTINE_SHARE = 0.8              # the routine tests stop at this share of the day's budget ...
STRETCH = ((0.5, 2), (0.8, 4))     # ... and from these shares the routine interval is this many times as long
FIRST_SHARE = 1.5                # the first measurement of a new tunnel goes on until the day has used this share (1.69.265: it waited for the next Tehran day at 125 %)
FIRST_TRIES = 3                  # (1.69.276) over FIRST_SHARE a tunnel with no number still gets this many first tests a day ...
FIRST_TOTAL = 60                 # ... and all the tunnels together this many (a first test moves 120 MB at the most: about 7 GB) - a test that fails again and again, or a loop that builds new tunnels again and again, has an end
KEEP_DAYS = 14
TEHRAN = 12600                   # UTC+3:30
_LOCK = threading.Lock()


def speed_every() -> int:
    """Seconds between two SPEED tests of the same kind of a pair / the same tunnel (1.69.280): `speed_days` of the settings document `testbudget`, 10 days by default; never less than a day, never more than 60."""
    try:
        days = float(st.get("testbudget").get("speed_days") or 10.0)
    except (TypeError, ValueError):
        days = 10.0

    return int(min(max(days, 1.0), 60.0) * 86400)


_WHAT: contextvars.ContextVar[str] = contextvars.ContextVar("testbudget_what", default="probe")


@contextlib.contextmanager
def purpose(what: str):
    """(1.69.282) The name the speed tests run inside the block are counted under (`spend`): the caller says what it is for, the test itself - `build_other.parse_probe` - does not know. A thread has its own: a job that
    measures a tunnel and a pair test that runs beside it never mix their names."""
    token = _WHAT.set(str(what) or "probe")

    try:
        yield
    finally:
        _WHAT.reset(token)


def purpose_now() -> str:
    """The name the tests of this thread are counted under now ("probe" when nobody named it)."""
    return _WHAT.get()


def _day(now: int) -> int:
    return (int(now) + TEHRAN) // 86400


def limit_bytes() -> int:
    try:
        gb = float(st.get("testbudget").get("daily_gb") or 0)
    except (TypeError, ValueError):
        gb = 0.0

    return int(max(gb, 1.0) * 1e9)


def spend(nbytes, what: str = "probe", now: int | None = None) -> None:
    """Count what a test moved, under the name of what asked for it (1.69.282: `bandwidth` the job that measures a tunnel, `pair-survey` the Doctor's speed test of a pair, `pair-test` and `new-tunnel` the tests the owner asked for
    or a repair needed, `tunnel-test`, `tunnel-speed`; the tests are counted too, per name). Never raises: a speed test is not stopped by its own bookkeeping."""
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
            days, days_by = dict(d.get("days") or {}), dict(d.get("days_by") or {})
            used, by, tests = int(d.get("bytes") or 0), dict(d.get("by") or {}), dict(d.get("tests") or {})

            if int(d.get("day") or 0) != day:
                if d.get("day"):
                    days[str(d["day"])] = used
                    days_by[str(d["day"])] = by          # (1.69.282) what the day moved, per kind of test: the answer to "what is the volume made of" is kept for the last days too

                used, by, tests = 0, {}, {}
                days = {k: v for k, v in days.items() if int(k) > day - KEEP_DAYS}
                days_by = {k: v for k, v in days_by.items() if int(k) > day - KEEP_DAYS}

            by[what] = int(by.get(what, 0)) + n
            tests[what] = int(tests.get(what, 0)) + 1
            st.put("testbudget", {"day": day, "bytes": used + n, "by": by, "tests": tests, "days": days, "days_by": days_by})
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


def _firsts(now: int) -> dict[str, int]:
    """The tries of the first tests made over FIRST_SHARE today, per tunnel id (the count belongs to its Tehran day)."""
    d = st.get("testbudget")

    if int(d.get("firsts_day") or 0) != _day(now):
        return {}

    return {str(k): int(v) for k, v in dict(d.get("firsts") or {}).items()}


def first_allowed(tunnel_id, now: int | None = None) -> bool:
    """May the FIRST measurement of a tunnel with no number go on? Yes until the day has used FIRST_SHARE of the budget (`allow("first")`); over it, yes for the first FIRST_TRIES tries of this tunnel and the first
    FIRST_TOTAL tries of all the tunnels of the day (1.69.276: the surveys and the moves had filled a day to 184 % and a tunnel rebuilt that afternoon kept its dash until the next Tehran day)."""
    now = int(now or time.time())

    if allow("first", now):
        return True

    f = _firsts(now)
    return f.get(str(tunnel_id), 0) < FIRST_TRIES and sum(f.values()) < FIRST_TOTAL


def note_first(tunnel_id, now: int | None = None) -> None:
    """Count one try of a first measurement made over FIRST_SHARE. Never raises: a speed test is not stopped by its own bookkeeping."""
    now = int(now or time.time())

    try:
        key = str(int(tunnel_id))

        with _LOCK:
            f = _firsts(now)
            f[key] = f.get(key, 0) + 1
            st.put("testbudget", {"firsts": f, "firsts_day": _day(now)})
    except (SQLAlchemyError, TypeError, ValueError):
        log.exception("the try of a first test could not be counted")


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
    days_by = dict(d.get("days_by") or {})
    days = [{"day": time.strftime("%Y-%m-%d", time.gmtime(int(k) * 86400)), "gb": round(int(v) / 1e9, 1),
             **({"by": {w: round(int(b) / 1e9, 2) for w, b in dict(days_by[k]).items()}} if isinstance(days_by.get(k), dict) else {})} for k, v in sorted(dict(d.get("days") or {}).items(), key=lambda kv: int(kv[0]))[-7:]]
    today_by = int(d.get("day") or 0) == today
    return {"daily_gb": round(limit_bytes() / 1e9, 1), "used_gb": round(cur / 1e9, 2), "share": round(cur / limit_bytes(), 3), "stretch": stretch(now), "routine": allow("routine", now), "firsts": sum(_firsts(now).values()),
            "by": {k: round(int(v) / 1e9, 2) for k, v in dict(d.get("by") or {}).items()} if cur else {},
            "tests": {k: int(v) for k, v in dict(d.get("tests") or {}).items()} if today_by else {}, "days": days}
