"""PasarGuard / Marzban: usage and live statistics of a panel (roadmap #94), READ ONLY.

Nothing here writes to a panel. The two panel types answer usage in different shapes (PasarGuard: `stats` per node and per hour; Marzban: one total per node and
no series), so the answers are normalised into one shape: {range, total, by_node, series}. Days are Tehran days (UTC+3:30, no daylight saving since 2022: a Tehran
day starts at 20:30 UTC of the day before), and the Jalali calendar is computed here in pure Python (the same algorithm lives in pg_usage.js).

What was verified against the real panels (read only): PasarGuard `GET /api/system`, `/api/users/usage` and `/api/admin/by-id/<id>/usage` and `/api/user/by-id/<id>/usage`
(`period`, `start`, `end`, `group_by_node`; the ids are looked up: the by-username routes are deprecated), `/api/users/counts/<metric>`, `/api/users/sub_update/chart`, `/api/node/usage`, `/api/nodes/simple`, `/api/admins/simple`, `/api/users?limit&sort&admin`; Marzban `GET /api/system`,
`/api/users/usage?start&end&admin=`, `/api/admin/usage/<name>`, `/api/user/<name>/usage`, `/api/admins`, `/api/users?limit&sort&admin`. The usage of ALL users of the big
Marzban panel is not asked at all (it times out on thousands of users): Marzban always needs an admin name."""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import quote, urlencode

from ..log import get_logger
from . import panels, pg_users
from .panels import PanelError

log = get_logger("pg_usage")

GB = 1024 ** 3
TEHRAN = dt.timedelta(hours=3, minutes=30)           # Iran has had no daylight saving since 2022
UTC = dt.timezone.utc
MAX_DAYS = 93                                         # one question never covers more than about three months
MIN_DAY = dt.date(2022, 9, 22)                        # Iran dropped daylight saving then: before it a Tehran day did not always start at 20:30 UTC
MAX_TOP = 20                                          # the top users list never asks the panel for more rows than this
OVERVIEW_TTL = 15                                     # seconds: several open tabs share one question to the panel
RANGE_TTL = 30                                        # a range that includes "now"
PAST_TTL = 300                                        # a range that ended more than two hours ago does not change
NAMES_TTL = 300                                       # node names, admin names
MARZBAN_TTL = 120                                     # a usage question can take many seconds on a big Marzban: it is not repeated at the drop of a hat (a finished range: 10 minutes)
MARZBAN_PAST_TTL = 600
STALE = 300                                           # seconds past its TTL a counter, a range, the top users are still handed out at once (old) while a new one is asked in the background: nobody waits for the panel (the owner, 2026-10-16: "these boxes load late")
PRESETS = ("today", "last7", "last30", "jmonth", "last_jmonth", "custom")
JALALI_MONTHS = ("Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar", "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand")
TOP_SORTS = {"pasarguard": ("used_traffic", "online_at", "data_limit", "created_at"), "marzban": ("used_traffic", "data_limit", "created_at")}
TYPES = tuple(TOP_SORTS)
_NAME = re.compile(r"^[A-Za-z0-9_.@\-]{1,64}$")
_DATE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")


# ------------------------------------------------------------------ Jalali calendar (pure; pg_usage.js has the same algorithm)
_CUM_DAYS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """The Jalali (Solar Hijri) date of a Gregorian date, by the 33-year cycle arithmetic: exact for the years 1178-1633, which is far more than a panel needs."""
    if not (1 <= gm <= 12 and 1 <= gd <= 31 and gy >= 1):
        raise ValueError("not a Gregorian date")

    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + _CUM_DAYS[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31

    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    """The Gregorian date of a Jalali date (the inverse of gregorian_to_jalali; it does not check that the day exists in that month)."""
    jy += 1595
    month_days = (jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd + month_days
    gy = 400 * (days // 146097)
    days %= 146097

    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524

        if days >= 365:
            days += 1

    gy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365

    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    lengths = (0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    gm = 0

    while gm < 13 and gd > lengths[gm]:
        gd -= lengths[gm]
        gm += 1

    return gy, gm, gd


def date_to_jalali(d: dt.date) -> tuple[int, int, int]:
    return gregorian_to_jalali(d.year, d.month, d.day)


def jalali_to_date(jy: int, jm: int, jd: int) -> dt.date:
    """A real Jalali day as a date; 1405/12/30 (the year has no day 30) or 1405/07/31 is refused."""
    if not (1 <= jm <= 12 and 1 <= jd <= 31):
        raise ValueError("not a Jalali date")

    gy, gm, gd = jalali_to_gregorian(jy, jm, jd)

    try:
        d = dt.date(gy, gm, gd)
    except ValueError:
        raise ValueError("not a Jalali date") from None

    if date_to_jalali(d) != (jy, jm, jd):
        raise ValueError("not a Jalali date")

    return d


def jalali_text(d: dt.date) -> str:
    jy, jm, jd = date_to_jalali(d)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


# ------------------------------------------------------------------ Tehran days and ranges (pure)
def _utc(now: dt.datetime | None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(UTC).replace(microsecond=0)

    return (now if now.tzinfo else now.replace(tzinfo=UTC)).astimezone(UTC).replace(microsecond=0)


def tehran_day_start(d: dt.date) -> dt.datetime:
    """The UTC moment a Tehran day begins: 00:00 in Tehran is 20:30 UTC of the day before."""
    return dt.datetime(d.year, d.month, d.day, tzinfo=UTC) - TEHRAN


def tehran_date(moment: dt.datetime) -> dt.date:
    """The Tehran calendar day a UTC moment falls on."""
    return (_utc(moment) + TEHRAN).date()


def iso_z(moment: dt.datetime) -> str:
    return _utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(text: str) -> dt.date:
    """'2026-10-14' (Gregorian) or '1405/07/22' (Jalali); the year decides which. Persian digits work too."""
    m = _DATE.match(str(text or "").strip())

    if not m:
        raise ValueError("a date looks like 2026-10-14 or 1405/07/22")

    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))

    if 1300 <= y <= 1600:
        return jalali_to_date(y, mo, d)

    if 1900 <= y <= 2200:
        try:
            return dt.date(y, mo, d)
        except ValueError:
            raise ValueError("that day does not exist") from None

    raise ValueError("the year must be a Jalali year (1300-1600) or a Gregorian year (1900-2200)")


@dataclass(frozen=True)
class Span:
    """A range of whole Tehran days: `start` and `end` are the UTC moments sent to the panel (the end is cut at 'now')."""

    preset: str
    label: str
    first: dt.date
    last: dt.date
    start: dt.datetime
    end: dt.datetime

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    def to_dict(self) -> dict:
        return {"preset": self.preset, "label": self.label, "from": self.first.isoformat(), "to": self.last.isoformat(),
                "from_j": jalali_text(self.first), "to_j": jalali_text(self.last), "start": iso_z(self.start), "end": iso_z(self.end), "days": self.days}


def resolve_range(preset: str, frm: str = "", to: str = "", now: dt.datetime | None = None) -> Span:
    """The range a preset (or a custom from/to) stands for, in Tehran days: today, last 7 days, last 30 days (both end today), this Jalali month so far, the whole
    last Jalali month, or custom. A custom end in the future is cut at today; a start in the future, an end before the start, and more than MAX_DAYS days are refused."""
    now = _utc(now)
    today = tehran_date(now)
    one = dt.timedelta(days=1)

    if preset == "today":
        first, last, label = today, today, "Today"
    elif preset == "last7":
        first, last, label = today - 6 * one, today, "Last 7 days"
    elif preset == "last30":
        first, last, label = today - 29 * one, today, "Last 30 days"
    elif preset == "jmonth":
        jy, jm, _jd = date_to_jalali(today)
        first, last, label = jalali_to_date(jy, jm, 1), today, f"{JALALI_MONTHS[jm - 1]} {jy}"
    elif preset == "last_jmonth":
        jy, jm, _jd = date_to_jalali(today)
        py, pm = (jy - 1, 12) if jm == 1 else (jy, jm - 1)
        first, last, label = jalali_to_date(py, pm, 1), jalali_to_date(jy, jm, 1) - one, f"{JALALI_MONTHS[pm - 1]} {py}"
    elif preset == "custom":
        if not str(frm or "").strip() or not str(to or "").strip():
            raise ValueError("a custom range needs both dates")

        first, last, label = parse_date(frm), parse_date(to), "Custom"
    else:
        raise ValueError("the range must be one of: " + ", ".join(PRESETS))

    if first < MIN_DAY:
        raise ValueError(f"days before {MIN_DAY.isoformat()} are not offered: Tehran time had daylight saving then")

    if first > today:
        raise ValueError("the range starts in the future")

    last = min(last, today)

    if last < first:
        raise ValueError("the end date is before the start date")

    if (last - first).days + 1 > MAX_DAYS:
        raise ValueError(f"a range can cover at most {MAX_DAYS} days")

    return Span(preset, label, first, last, tehran_day_start(first), min(tehran_day_start(last + one), now))


def presets(now: dt.datetime | None = None) -> dict:
    """Every ready-made range with its Gregorian and Jalali dates, so the page does not have to repeat the rules."""
    now = _utc(now)
    today = tehran_date(now)
    return {"now": iso_z(now), "tehran_today": today.isoformat(), "tehran_today_j": jalali_text(today),
            "presets": [resolve_range(p, now=now).to_dict() for p in PRESETS if p != "custom"], "max_days": MAX_DAYS}


# ------------------------------------------------------------------ normalising the answers (pure; tolerant of missing keys)
def _num(v) -> int:
    """A traffic figure as a non-negative whole number; anything that is not a number is 0."""
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError, OverflowError):
        return 0


def gb_of(n) -> float:
    return round(_num(n) / GB, 2)


def _first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]

    return None


def parse_overview(sysd) -> dict:
    """The counters of /api/system as one shape. PasarGuard says `active_users`, Marzban `users_active`; a key the panel does not send is None."""
    s = sysd if isinstance(sysd, dict) else {}
    mt, mu = _num(s.get("mem_total")), _num(s.get("mem_used"))
    dtot, du = _num(s.get("disk_total")), _num(s.get("disk_used"))
    cpu = s.get("cpu_usage")
    return {"total": _first(s, "total_user", "total_users"), "online": _first(s, "online_users"), "active": _first(s, "active_users", "users_active"),
            "on_hold": _first(s, "on_hold_users", "users_on_hold"), "disabled": _first(s, "disabled_users", "users_disabled"),
            "expired": _first(s, "expired_users", "users_expired"), "limited": _first(s, "limited_users", "users_limited"),
            "cpu_pct": round(float(cpu), 1) if isinstance(cpu, (int, float)) else None, "cpu_cores": _first(s, "cpu_cores"),
            "mem_pct": round(mu * 100 / mt, 1) if mt and "mem_used" in s else None, "mem_used": mu if mt else None, "mem_total": mt or None,
            "disk_pct": round(du * 100 / dtot, 1) if dtot and "disk_used" in s else None, "disk_used": du if dtot else None, "disk_total": dtot or None,
            "traffic_in": _first(s, "incoming_bandwidth"), "traffic_out": _first(s, "outgoing_bandwidth"),
            "speed_in": _first(s, "incoming_bandwidth_speed"), "speed_out": _first(s, "outgoing_bandwidth_speed"),
            "uptime_s": _first(s, "uptime_seconds"), "version": str(_first(s, "version") or "")[:30]}


def _moment(v) -> dt.datetime | None:
    if not isinstance(v, str):
        return None

    try:
        d = dt.datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None

    return d if d.tzinfo else d.replace(tzinfo=UTC)


def parse_pg_stats(raw) -> dict[int, list[tuple[dt.datetime, int, int]]]:
    """PasarGuard `stats` -> {node id: [(period start, bytes, bytes), ...]}. The first number is `total_traffic` (users usage); the second is `uplink + downlink`
    (node traffic). A key that is not a number, a row without a date and anything that is not a list are skipped; the key -1 means "all nodes together"."""
    stats = raw.get("stats") if isinstance(raw, dict) else None

    if isinstance(stats, list):
        stats = {"-1": stats}

    out: dict[int, list[tuple[dt.datetime, int, int]]] = {}

    if not isinstance(stats, dict):
        return out

    for key, rows in stats.items():
        try:
            nid = int(key)
        except (TypeError, ValueError):
            continue

        for r in rows if isinstance(rows, list) else []:
            when = _moment(r.get("period_start")) if isinstance(r, dict) else None

            if when is not None:
                out.setdefault(nid, []).append((when, _num(r.get("total_traffic")), _num(r.get("uplink")) + _num(r.get("downlink"))))

    return out


def parse_marzban_usages(raw) -> list[dict]:
    """Marzban `usages` -> [{id, name, bytes}] for the nodes that carried traffic (the master has node_id null). Returns [] for anything unexpected."""
    items = raw.get("usages") if isinstance(raw, dict) else raw
    out = []

    for u in items if isinstance(items, list) else []:
        if not isinstance(u, dict):
            continue

        n = _num(u.get("used_traffic"))

        if n > 0:
            out.append({"id": u.get("node_id"), "name": str(u.get("node_name") or "node")[:60], "bytes": n})

    return out


def node_names_of(raw) -> dict[int, str]:
    """{node id: name} from /api/nodes/simple, /api/nodes, or a plain list."""
    items = raw.get("nodes") if isinstance(raw, dict) else raw
    out: dict[int, str] = {}

    for n in items if isinstance(items, list) else []:
        if isinstance(n, dict) and isinstance(n.get("id"), int):
            out[n["id"]] = str(n.get("name") or "")[:60]

    return out


def by_node_rows(totals: dict, names: dict[int, str], node_traffic: dict | None = None) -> list[dict]:
    """The by-node table: biggest first, with each node's share of the total. `totals` is {node id: bytes}; `node_traffic` {node id: (up, down)} adds the raw traffic of the node."""
    whole = sum(totals.values())
    rows = []

    for nid, n in totals.items():
        name = "All nodes" if nid == -1 else names.get(nid) or f"node {nid}"
        row = {"id": nid, "name": name, "bytes": n, "gb": gb_of(n), "share": round(n / whole, 4) if whole else 0.0}

        if node_traffic is not None:
            up, down = node_traffic.get(nid, (0, 0))
            row.update({"up_gb": gb_of(up), "down_gb": gb_of(down)})

        rows.append(row)

    rows.sort(key=lambda r: (-r["bytes"], r["name"].lower()))
    return rows


def build_series(per_node: dict[int, list[tuple[dt.datetime, int, int]]], span: Span) -> tuple[str, list[dict]]:
    """The bars of the chart, from PasarGuard's hourly rows: Tehran days (one point per day, zeros where the panel sent nothing), or Tehran hours when the range is
    one or two days. A row is put in the Tehran day its hour STARTS in (the hour 20:00-21:00 UTC is 23:30-00:30 in Tehran and belongs to the day it begins in), so a
    day boundary is off by at most half an hour of traffic. The sum of the points is exactly the total."""
    hourly = span.days <= 2
    sums: dict[Any, int] = {}

    for rows in per_node.values():
        for when, n, _raw in rows:
            local = when + TEHRAN
            key = local.replace(tzinfo=None, second=0, microsecond=0) if hourly else local.date()
            sums[key] = sums.get(key, 0) + n

    keys: set = set(sums)

    if hourly:
        cur = span.start if (span.start.minute, span.start.second) == (0, 0) else span.start.replace(minute=0, second=0) + dt.timedelta(hours=1)

        while cur <= span.end:
            keys.add((cur + TEHRAN).replace(tzinfo=None))
            cur += dt.timedelta(hours=1)
    else:
        day = span.first

        while day <= span.last:
            keys.add(day)
            day += dt.timedelta(days=1)

    series = []

    for key in sorted(keys):
        n = sums.get(key, 0)
        day = key.date() if isinstance(key, dt.datetime) else key
        item = {"t": key.strftime("%Y-%m-%dT%H:%M") if isinstance(key, dt.datetime) else key.isoformat(),
                "label": key.strftime("%H:%M") if isinstance(key, dt.datetime) else key.strftime("%m-%d"), "jalali": jalali_text(day), "bytes": n, "gb": gb_of(n)}
        series.append(item)

    return ("hour" if hourly else "day"), series


# ------------------------------------------------------------------ a small cache
T = TypeVar("T")
_CACHE: dict[tuple, tuple[float, Any]] = {}
_LOCKS: dict[tuple, threading.Lock] = {}
_GUARD = threading.Lock()
_HEAVY: dict[int, threading.Lock] = {}
_MAX_CACHE = 200
_clock = time.monotonic          # the tests move this clock


def _refresh_behind(key: tuple, lock: threading.Lock, fn: Callable[[], Any]) -> None:
    """The new answer for a value that was handed out old: asked from the panel in the background (the caller holds `lock`), kept when it comes; an error leaves the old value for its turn."""
    try:
        value = fn()

        with _GUARD:
            _CACHE[key] = (_clock(), value)
    except Exception as e:                                # noqa: BLE001 - a background refresh must never take the thread down; the next question asks again
        log.info("refresh of %s failed: %s", key[0], str(e)[:100])
    finally:
        lock.release()


def _cached(key: tuple, ttl: float, fn: Callable[[], T], stale: float = 0) -> tuple[T, float | None]:
    """(value, age in seconds), the age is None when the value was just asked from the panel. One asker at a time per key, so several open tabs wait for the first answer instead of each asking the panel; an error is not kept.
    With `stale` a value older than `ttl` but younger than `ttl + stale` is handed out AT ONCE, old, and a new one is asked in the background (the owner, 2026-10-16: "these boxes load late"): nobody waits for the panel except the very first time."""
    with _GUARD:
        hit = _CACHE.get(key)
        lock = _LOCKS.setdefault(key, threading.Lock())

    if hit is not None and _clock() - hit[0] < ttl:
        return hit[1], _clock() - hit[0]

    if hit is not None and stale and _clock() - hit[0] < ttl + stale:
        if lock.acquire(blocking=False):                  # nobody is asking yet: ask in the background (the lock is let go there)
            threading.Thread(target=_refresh_behind, args=(key, lock, fn), daemon=True, name="usage-refresh").start()

        return hit[1], _clock() - hit[0]

    with lock:
        hit = _CACHE.get(key)

        if hit is not None and _clock() - hit[0] < ttl:
            return hit[1], _clock() - hit[0]

        value = fn()

        with _GUARD:
            _CACHE[key] = (_clock(), value)

            if len(_CACHE) > _MAX_CACHE:
                for old in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: _MAX_CACHE // 4]:
                    _CACHE.pop(old, None)
                    _LOCKS.pop(old, None)

            if len(_LOCKS) > 2 * _MAX_CACHE:                    # the locks of questions that failed never got a cache entry: do not keep them for ever
                for k in [k for k, lk in _LOCKS.items() if k not in _CACHE and not lk.locked()]:
                    _LOCKS.pop(k, None)

        return value, None


def _meta(age: float | None) -> dict:
    """What the answer says about its freshness: {cached, age_s}."""
    return {"cached": age is not None, "age_s": round(age or 0.0, 1)}


def clear_cache() -> None:
    with _GUARD:
        _CACHE.clear()
        _LOCKS.clear()
        _HEAVY.clear()


def _heavy_lock(c: dict) -> threading.Lock:
    """One lock per panel for the slow Marzban usage questions: two of them at the same time could slow the panel down for its customers, so the second waits."""
    with _GUARD:
        return _HEAVY.setdefault(c["id"], threading.Lock())


# ------------------------------------------------------------------ talking to the panel (GET only)
def _check_type(c: dict) -> str:
    typ = c.get("type", "")

    if typ not in TYPES:
        raise ValueError("this tab works for PasarGuard and Marzban panels")

    return typ


def _name(value: str, what: str) -> str:
    n = str(value or "").strip()

    if not _NAME.match(n):
        raise ValueError(f"{what} has 1 to 64 letters, digits or _ . @ -")

    return n


def _get(c: dict, path: str, timeout: float = 30):
    return panels.call(c, "GET", path, timeout=timeout)


def node_names(c: dict) -> dict[int, str]:
    """Node names of a PasarGuard panel for 5 minutes; the names only dress up the table, so a failure gives an empty dict (and is asked again next time)."""
    key = ("nodes", c["id"])

    def ask() -> dict[int, str]:
        for path in ("/api/nodes/simple", "/api/nodes"):
            try:
                return node_names_of(_get(c, path, 20))
            except PanelError as e:
                log.info("node names from %s: %s", path, str(e)[:80])

        return {}

    names, _age = _cached(key, NAMES_TTL, ask)

    if not names:
        with _GUARD:
            _CACHE.pop(key, None)

    return names


def admins(c: dict) -> dict:
    """The admin names of the panel (for the lookup box), kept 5 minutes."""
    typ = _check_type(c)

    def ask() -> list[str]:
        raw = _get(c, "/api/admins/simple" if typ == "pasarguard" else "/api/admins", 30 if typ == "pasarguard" else 60)
        lst = raw.get("admins") if isinstance(raw, dict) else raw
        return sorted({str(a.get("username")) for a in lst or [] if isinstance(a, dict) and a.get("username")}, key=str.lower)

    names, _age = _cached(("admins", c["id"]), NAMES_TTL, ask)
    return {"admins": names}


def overview(c: dict) -> dict:
    """The counters of the panel (users by status, online, cpu, memory, traffic). Asked at most once in 15 seconds per panel, however many tabs are open."""
    typ = _check_type(c)
    value, age = _cached(("overview", c["id"]), OVERVIEW_TTL, lambda: parse_overview(_get(c, "/api/system", 30)), STALE)
    return {**value, "panel_type": typ, **_meta(age)}


def online(c: dict) -> dict:
    """Users online right now (from the cached counters, no extra question)."""
    ov = overview(c)
    return {"online": ov["online"], "total": ov["total"], "cached": ov["cached"], "age_s": ov["age_s"]}


def _query(span: Span, typ: str, extra: dict | None = None) -> str:
    """The query string: PasarGuard wants Z-suffixed UTC moments and hourly buckets by node; Marzban wants naive UTC moments (it uses utcnow())."""
    if typ == "pasarguard":
        q = {"period": "hour", "group_by_node": "true", "start": iso_z(span.start), "end": iso_z(span.end)}
    else:
        q = {"start": iso_z(span.start).rstrip("Z"), "end": iso_z(span.end).rstrip("Z")}

    q.update(extra or {})
    return urlencode(q)


def _node_traffic(c: dict, span: Span) -> dict[int, tuple[int, int]] | None:
    """PasarGuard only: the raw uplink/downlink of every node in the range (the traffic of the node itself, which is more than the users' usage). None when the panel does not answer."""
    try:
        raw = _get(c, "/api/node/usage?" + _query(span, "pasarguard"), 30)
    except PanelError as e:
        log.info("node traffic: %s", str(e)[:80])
        return None

    out: dict[int, tuple[int, int]] = {}
    stats = raw.get("stats") if isinstance(raw, dict) else None

    for key, rows in (stats if isinstance(stats, dict) else {}).items():
        try:
            nid = int(key)
        except (TypeError, ValueError):
            continue

        up = sum(_num(r.get("uplink")) for r in rows if isinstance(r, dict)) if isinstance(rows, list) else 0
        down = sum(_num(r.get("downlink")) for r in rows if isinstance(r, dict)) if isinstance(rows, list) else 0
        out[nid] = (up, down)

    return out


def _result(typ: str, scope: str, name: str, span: Span, by_node: list[dict], series: list[dict], granularity: str, notes: list[str]) -> dict:
    total = sum(r["bytes"] for r in by_node)
    return {"panel_type": typ, "scope": scope, "name": name, "range": span.to_dict(), "start": iso_z(span.start), "end": iso_z(span.end),
            "total": {"bytes": total, "gb": gb_of(total)}, "avg_per_day_gb": round(total / GB / span.days, 2), "granularity": granularity,
            "by_node": by_node, "series": series, "notes": notes}


def _fetch_usage(c: dict, typ: str, scope: str, name: str, span: Span) -> dict:
    notes: list[str] = []

    if typ == "pasarguard":
        if scope == "user":
            base = f"/api/user/by-id/{pg_users._id_of(c, name)}/usage"                        # the by-username routes are deprecated (removed in 6.0): the id is looked up
        elif scope == "admin":
            base = f"/api/admin/by-id/{pg_users._admin_ids(c, [name])[0]}/usage"
        else:
            base = "/api/users/usage"

        try:
            raw = _get(c, base + "?" + _query(span, typ), 60)
        except PanelError as e:
            if e.code == 404 and scope != "all":
                raise ValueError(f"the panel has no {scope} named {name}") from None

            raise

        per_node = parse_pg_stats(raw)
        names = node_names(c)
        traffic = _node_traffic(c, span) if scope == "all" else None
        totals = {nid: sum(n for _w, n, _r in rows) for nid, rows in per_node.items()}
        granularity, series = build_series(per_node, span)

        if traffic is not None:
            totals = {nid: totals.get(nid, 0) for nid in set(totals) | set(traffic)}

        if not totals:
            notes.append("The panel has no usage rows in this range (it keeps usage only for a limited time).")

        return _result(typ, scope, name, span, by_node_rows(totals, names, traffic), series, granularity, notes)

    path = "/api/users/usage?" + _query(span, typ, {"admin": name}) if scope == "admin" else f"/api/user/{quote(name, safe='')}/usage?" + _query(span, typ)

    try:
        with _heavy_lock(c):
            raw = _get(c, path, 120)
    except PanelError as e:
        if e.code == 404:
            raise ValueError(f"the panel has no {scope} named {name}") from None

        raise

    rows = parse_marzban_usages(raw)
    totals = {(r["id"] if isinstance(r["id"], int) else 0): r["bytes"] for r in rows}
    names = {(r["id"] if isinstance(r["id"], int) else 0): r["name"] for r in rows}
    notes.append("Marzban gives one total per node for the range, without a day-by-day series.")
    return _result(typ, scope, name, span, by_node_rows(totals, names), [], "none", notes)


def _usage(c: dict, scope: str, name: str, preset: str, frm: str, to: str, now: dt.datetime | None) -> dict:
    typ = _check_type(c)

    if typ == "marzban" and scope == "all":
        raise ValueError("Marzban needs an admin name: the usage of all users is too heavy for a panel with thousands of users")

    now = _utc(now)
    span = resolve_range(preset, frm, to, now)
    past = span.end <= now - dt.timedelta(hours=2)
    ttl = (PAST_TTL if past else RANGE_TTL) if typ == "pasarguard" else (MARZBAN_PAST_TTL if past else MARZBAN_TTL)
    # the key is the range of DAYS, not the moment "now" that cuts its end: a question a few seconds later is the same question
    value, age = _cached(("usage", c["id"], scope, name, span.first.isoformat(), span.last.isoformat()), ttl, lambda: _fetch_usage(c, typ, scope, name, span), STALE)
    return {**value, **_meta(age)}


def usage(c: dict, preset: str = "last7", frm: str = "", to: str = "", now: dt.datetime | None = None) -> dict:
    """The usage of all users of the panel in a range: total, by node, and the day-by-day (or hour-by-hour) series. PasarGuard only; Marzban uses the lookups below."""
    return _usage(c, "all", "", preset, frm, to, now)


def admin_usage(c: dict, admin: str, preset: str = "last7", frm: str = "", to: str = "", now: dt.datetime | None = None) -> dict:
    """The usage of the users of one admin in a range. On Marzban the name must be a real admin (checked against the admin list) and the lifetime total is added."""
    typ = _check_type(c)
    name = _name(admin, "an admin name")

    if typ == "marzban":
        try:
            known = admins(c)["admins"]
        except PanelError as e:
            log.info("admin list for the check: %s", str(e)[:80])          # the check is a courtesy: without the list, the usage question decides
            known = []

        if known and name not in known:
            raise ValueError(f"the panel has no admin named {name}")

    out = _usage(c, "admin", name, preset, frm, to, now)

    if typ == "marzban":
        out = {**out, "lifetime_gb": _admin_lifetime_gb(c, name)}

    return out


def _admin_lifetime_gb(c: dict, name: str) -> float | None:
    """Marzban: everything the admin's users ever used (`/api/admin/usage/<name>` is one number); None when the panel does not say."""
    try:
        life, _age = _cached(("admin-life", c["id"], name), RANGE_TTL, lambda: _get(c, f"/api/admin/usage/{quote(name, safe='')}", 60))
    except PanelError as e:
        log.info("admin lifetime usage: %s", str(e)[:80])
        return None

    return gb_of(life) if isinstance(life, (int, float)) else None


def user_usage(c: dict, username: str, preset: str = "last7", frm: str = "", to: str = "", now: dt.datetime | None = None) -> dict:
    """The usage of one user in a range, by node."""
    return _usage(c, "user", _name(username, "a username"), preset, frm, to, now)


COUNT_METRICS = ("online", "expired", "limited")


def parse_counts(raw) -> tuple[int, list[dict]]:
    """(the count the panel gives for the whole range, [{t, count}] by period). The panel's `stats` is {key: [{period_start, count}]}; when it lists more than one key the points of
    the same moment are not added (the same user could be counted on two nodes): the bigger one is kept."""
    if not isinstance(raw, dict):
        return 0, []

    per: dict[str, int] = {}

    for rows in (raw.get("stats") if isinstance(raw.get("stats"), dict) else {}).values():
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and isinstance(r.get("period_start"), str):
                per[r["period_start"]] = max(per.get(r["period_start"], 0), _num(r.get("count")))

    return _num(raw.get("count_during_period")), [{"t": k, "count": v} for k, v in sorted(per.items())]


def counts(c: dict, metric: str, preset: str = "last7", frm: str = "", to: str = "", admin: str = "", now: dt.datetime | None = None) -> dict:
    """How many users were ONLINE, how many became EXPIRED and how many LIMITED over time (`GET /api/users/counts/{metric}`), per hour for a range of two days or less and per day
    otherwise; the users of one admin when `admin` is given. PasarGuard only."""
    if _check_type(c) != "pasarguard":
        raise ValueError("the user counts over time are a part of PasarGuard panels")

    if metric not in COUNT_METRICS:
        raise ValueError("the metric is one of: " + ", ".join(COUNT_METRICS))

    now = _utc(now)
    span = resolve_range(preset, frm, to, now)
    period = "hour" if span.days <= 2 else "day"
    who = _name(admin, "an admin name") if admin else ""
    q = [("period", period), ("start", iso_z(span.start)), ("end", iso_z(span.end))] + ([("admin", who)] if who else [])
    past = span.end <= now - dt.timedelta(hours=2)

    def ask() -> dict:
        total, series = parse_counts(_get(c, f"/api/users/counts/{metric}?" + urlencode(q), 60))
        return {"metric": metric, "period": period, "range": span.to_dict(), "admin": who, "count": total, "series": series, "max": max((p["count"] for p in series), default=0)}

    value, age = _cached(("counts", c["id"], metric, who, period, span.first.isoformat(), span.last.isoformat()), PAST_TTL if past else RANGE_TTL, ask, STALE)
    return {**value, **_meta(age)}


def sub_apps(c: dict, preset: str = "last7", frm: str = "", to: str = "", user: str = "", admin: str = "", now: dt.datetime | None = None) -> dict:
    """Which apps fetch the subscription: the share of every client (its User-Agent name) over a range, for everybody, one user or the users of one admin
    (`GET /api/users/sub_update/chart`). PasarGuard only."""
    if _check_type(c) != "pasarguard":
        raise ValueError("the subscription apps are a part of PasarGuard panels")

    now = _utc(now)
    span = resolve_range(preset, frm, to, now)
    q = [("period", "day"), ("start", iso_z(span.start)), ("end", iso_z(span.end))]
    uname, aname = (_name(user, "a username") if user else ""), (_name(admin, "an admin name") if admin else "")

    if uname and aname:
        raise ValueError("ask for one user or for one admin, not both")

    if uname:
        q.append(("user_id", str(pg_users._id_of(c, uname))))
    elif aname:
        q.append(("admin_id", str(pg_users._admin_ids(c, [aname])[0])))

    past = span.end <= now - dt.timedelta(hours=2)

    def ask() -> dict:
        raw = _get(c, "/api/users/sub_update/chart?" + urlencode(q), 60)
        raw = raw if isinstance(raw, dict) else {}
        segs = [{"name": str(s.get("name") or "")[:80], "count": _num(s.get("count")), "percent": round(float(s.get("percentage") or 0), 1)} for s in (raw.get("segments") if isinstance(raw.get("segments"), list) else []) if isinstance(s, dict)]
        return {"range": span.to_dict(), "user": uname, "admin": aname, "total": _num(raw.get("total")), "apps": sorted(segs, key=lambda s: -s["count"])[:30]}

    value, age = _cached(("subapps", c["id"], uname, aname, span.first.isoformat(), span.last.isoformat()), PAST_TTL if past else RANGE_TTL, ask, STALE)
    return {**value, **_meta(age)}


def top_row(u: dict) -> dict:
    """One user for the top list: the figures only (never the proxy settings, ids or subscription link that the panel sends along)."""
    adm = u.get("admin")
    admin = adm.get("username") if isinstance(adm, dict) else adm
    used, limit = _num(u.get("used_traffic")), _num(u.get("data_limit"))
    return {"username": str(u.get("username") or "")[:64], "status": str(u.get("status") or "")[:20], "used_bytes": used, "used_gb": gb_of(used),
            "limit_gb": gb_of(limit) if limit else None, "percent": round(used * 100 / limit) if limit else None, "expire": panels.date_of(u.get("expire")),
            "online_at": str(u.get("online_at") or "")[:32], "created_at": str(u.get("created_at") or "")[:32], "admin": str(admin or "")[:64]}


def top_users(c: dict, sort: str = "used_traffic", limit: int = MAX_TOP, admin: str = "") -> dict:
    """The users at the top of a sort that the panel itself does (`sort=-used_traffic&limit=20`): never more than 20 rows are read, never the whole user list. The figure is the
    panel's own counter (used since its last reset), not the usage in the chosen range: the panel keeps no per-user usage for a range in one question."""
    typ = _check_type(c)

    if sort not in TOP_SORTS[typ]:
        raise ValueError("this panel can sort by: " + ", ".join(TOP_SORTS[typ]))

    try:
        n = max(1, min(MAX_TOP, int(limit)))
    except (TypeError, ValueError):
        raise ValueError("the limit must be a number from 1 to 20") from None

    who = _name(admin, "an admin name") if admin else ""
    q = {"limit": n, "sort": "-" + sort}

    if who:
        q["admin"] = who

    def ask() -> dict:
        raw = _get(c, "/api/users?" + urlencode(q), 60 if typ == "marzban" else 30)
        rows = raw.get("users") if isinstance(raw, dict) else raw
        total = raw.get("total") if isinstance(raw, dict) else None
        users = [top_row(u) for u in rows if isinstance(u, dict)] if isinstance(rows, list) else []
        return {"users": users[:MAX_TOP], "total": total if isinstance(total, int) else len(users)}

    value, age = _cached(("top", c["id"], sort, n, who), RANGE_TTL, ask, STALE)
    return {**value, "sort": sort, "limit": n, "admin": who, "sorts": list(TOP_SORTS[typ]), **_meta(age)}
