"""PasarGuard: WATCH one user for a few hours (phase 3 of the Investigate window; the owner, 2026-11-03: a customer says his data goes by itself - watch him and see).

The panel keeps no history of the addresses a user connects from and its usage is by hour at best, so for a user under suspicion this job keeps its own: every `EVERY` seconds it reads the used
traffic of the user (the difference to the last read is the usage of that time) and the addresses he is connected from right now on each node, and keeps both in the panel's own database for up to
`MAX_HOURS` hours (`user_watches`, `user_watch_samples`; a finished watch is kept `KEEP_DAYS` days). It only reads from the PasarGuard panel; the one thing it writes is its own rows.
The window shows the usage in 10-minute bars, every address with when it was first and last seen, and a few plain findings (`summarize`, a pure function). What it cannot show: behind a tunnel the node
sees the entrance server's address, not the customer's (the address is then marked as ours), and an address that came and went between two reads is missed.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select

from ..db import session
from ..log import get_logger
from ..models import UserWatch, UserWatchSample, now
from . import panels, pg_users
from .panels import PanelError

log = get_logger("pg_watch")

EVERY = 120                    # seconds between two reads of a watched user
MIN_HOURS, MAX_HOURS = 0.25, 72
MAX_ACTIVE = 10                # users watched at the same time (each read is 4 light calls to the panel)
KEEP_DAYS = 14
BUCKET = 600                   # the bars of the chart: 10 minutes
TEHRAN = dt.timedelta(hours=3, minutes=30)
GB = 1024 ** 3
NIGHT_HOURS = (1, 2, 3, 4, 5)
MANY = 3                       # different addresses at the same moment


# ------------------------------------------------------------------ pure: what the samples say
def _f(level: str, code: str, title: str, detail: str) -> dict:
    return {"level": level, "code": code, "title": title, "detail": detail}


def summarize(samples: list[dict], every: int = EVERY, ours: dict | None = None) -> dict:
    """The watch as the window shows it. `samples` = [{ts, used, delta, online: [{node, ip, seen}]}] oldest first. Gives the bars (10 minutes each), the addresses with first / last seen and the time
    they were seen for (reads x the interval), the figures, and the findings."""
    ours = ours or {}
    buckets: dict[int, dict] = {}
    addrs: dict[str, dict] = {}
    worst = (0, 0)                                                         # (most different addresses of the user's own at one read, when)
    total = 0
    night = 0

    for s in samples:
        b = buckets.setdefault(s["ts"] // BUCKET * BUCKET, {"bytes": 0, "ips": set()})
        d = int(s.get("delta") or 0)
        b["bytes"] += d
        total += d

        if (dt.datetime.fromtimestamp(s["ts"], dt.timezone.utc) + TEHRAN).hour in NIGHT_HOURS:
            night += d

        mine = set()

        for o in s.get("online") or []:
            a = addrs.setdefault(o["ip"], {"ip": o["ip"], "first": s["ts"], "last": s["ts"], "reads": 0, "nodes": set(), "ours": ours.get(o["ip"], "")})
            a["last"], a["reads"] = s["ts"], a["reads"] + 1
            a["nodes"].add(o.get("node") or "")
            b["ips"].add(o["ip"])

            if not a["ours"]:
                mine.add(o["ip"])

        if len(mine) > worst[0]:
            worst = (len(mine), s["ts"])

    bars = [{"t": k, "bytes": v["bytes"], "ips": len(v["ips"])} for k, v in sorted(buckets.items())]
    table = sorted(({"ip": a["ip"], "first": a["first"], "last": a["last"], "minutes": round(a["reads"] * every / 60), "nodes": sorted(n for n in a["nodes"] if n), "ours": a["ours"]} for a in addrs.values()), key=lambda x: (-x["minutes"], x["ip"]))
    span = (samples[-1]["ts"] - samples[0]["ts"]) if len(samples) > 1 else 0
    peak = max(bars, key=lambda x: x["bytes"]) if bars else None
    own = [a for a in table if not a["ours"]]
    out = []

    if worst[0] >= MANY:
        out.append(_f("warn", "simultaneous", f"{worst[0]} different addresses were connected at the same moment", "One customer is rarely on three networks at once: the link is probably shared."))

    if total >= GB and night / total >= 0.4:
        out.append(_f("warn", "night", f"{round(night * 100 / total)} % of the usage was at night (01:00-06:00 Tehran)", "Usage while the customer sleeps: an app updating or downloading in the background, a computer left on, or somebody else using the link."))

    if peak and total >= 2 * GB and peak["bytes"] / total >= 0.5:
        out.append(_f("info", "burst", f"{round(peak['bytes'] / GB, 2)} GB of the {round(total / GB, 2)} GB went in one 10-minute stretch", "A single burst is the mark of a big download, a video, a game or app update, or a speed test."))

    if len(own) >= 4:
        out.append(_f("info", "addresses", f"Seen from {len(own)} different addresses", "On a mobile network the address changes often; look at the 'at the same moment' finding to tell sharing from moving."))

    if table and not own:
        out.append(_f("info", "ours_only", "Only our own entrance servers were seen", "The user is on a tunnel config: the nodes see our server's address, not the customer's."))

    if span >= 6 * 3600 and total < 100 * 1024 ** 2:
        out.append(_f("ok", "quiet", f"Very little was used in {round(span / 3600)} hours ({round(total / 1024 ** 2)} MB)", "Nothing is consuming the data by itself in this period."))

    if samples and not table:
        out.append(_f("info", "no_addresses", "No address was listed in any read", "The user was not connected during the reads, or the panel could not tell."))

    return {"bars": bars, "addresses": table, "total": total, "samples": len(samples), "span": span, "peak": peak, "findings": out, "night_share": round(night / total, 3) if total else 0.0, "simultaneous": worst[0]}


# ------------------------------------------------------------------ the job
def _read(c: dict, uid: int, username: str) -> tuple[int, list[dict]]:
    """(the used traffic, the live addresses) of one user, read now."""
    raw = pg_users._dict(pg_users._call(c, "GET", f"/api/user/by-id/{uid}"))
    rows = pg_users.online_ips(c, username)["online"]
    return int(raw.get("used_traffic") or 0), [{"node": r["node"], "ip": r["ip"], "seen": r.get("last_seen")} for r in rows]


def sample(watch_id: int, ts: int | None = None) -> bool:
    """One read of one watch, stored. A panel that does not answer is noted on the watch (and tried again at the next turn). True = a sample was stored."""
    ts = ts or now()

    with session() as db:
        w = db.get(UserWatch, watch_id)

        if w is None or not w.active:
            return False

        panel_id, uid, username, last_used = w.panel_id, w.user_id, w.username, w.last_used

    try:
        c = panels.cred_for(panel_id)
        used, online = _read(c, uid, username)
    except (PanelError, ValueError, OSError) as e:
        with session() as db:
            w = db.get(UserWatch, watch_id)
            w.last_at, w.last_error = ts, str(e)[:200]

        log.info("watch %s: %s", username, str(e)[:100])
        return False

    with session() as db:
        w = db.get(UserWatch, watch_id)
        delta = max(0, used - last_used) if last_used is not None else 0              # (a reset of the usage must not count as a negative or a huge read)
        db.add(UserWatchSample(watch_id=watch_id, ts=ts, used=used, delta=delta, online=online))
        w.last_at, w.last_used, w.last_error = ts, used, ""

    return True


def run(ts: int | None = None) -> None:
    """The job (every minute): reads every watch that is due, switches off the ones that ended, and throws away what is older than KEEP_DAYS."""
    ts = ts or now()
    due: list[int] = []

    with session() as db:
        for w in db.scalars(select(UserWatch).where(UserWatch.active.is_(True))):
            if ts >= w.ends_at:
                w.active = False
            elif ts - (w.last_at or 0) >= w.every - 5:
                due.append(w.id)

        old = [i for (i,) in db.execute(select(UserWatch.id).where(UserWatch.active.is_(False), UserWatch.ends_at < ts - KEEP_DAYS * 86400))]

        if old:
            db.execute(delete(UserWatchSample).where(UserWatchSample.watch_id.in_(old)))
            db.execute(delete(UserWatch).where(UserWatch.id.in_(old)))

    for wid in due:
        sample(wid, ts)


# ------------------------------------------------------------------ what the window calls
def _latest(db, panel_id: int, username: str):
    return db.scalar(select(UserWatch).where(UserWatch.panel_id == panel_id, UserWatch.username == username).order_by(UserWatch.started_at.desc(), UserWatch.id.desc()).limit(1))


def status(panel_id: int, username: str, ts: int | None = None) -> dict:
    """The newest watch of a user (running or finished) with what it saw; `watch` is None when the user was never watched (or the watch is older than KEEP_DAYS)."""
    ts = ts or now()

    with session() as db:
        w = _latest(db, panel_id, username)

        if w is None:
            return {"active": False, "watch": None, "summary": None, "max_hours": MAX_HOURS}

        info = {"id": w.id, "started_at": w.started_at, "ends_at": w.ends_at, "every": w.every, "last_at": w.last_at, "last_error": w.last_error, "active": bool(w.active and ts < w.ends_at)}
        rows = db.execute(select(UserWatchSample.ts, UserWatchSample.used, UserWatchSample.delta, UserWatchSample.online).where(UserWatchSample.watch_id == w.id).order_by(UserWatchSample.ts)).all()

    samples = [{"ts": r[0], "used": r[1], "delta": r[2], "online": r[3] or []} for r in rows]
    from .pg_investigate import _ours

    return {"active": info["active"], "watch": info, "summary": summarize(samples, info["every"], _ours()), "max_hours": MAX_HOURS}


def start(panel_id: int, username: str, hours: float, ts: int | None = None) -> dict:
    """Watch a user for `hours` hours from now (a watch that already runs is only made longer) and read him once at once."""
    ts = ts or now()

    try:
        hours = float(hours)
    except (TypeError, ValueError):
        raise ValueError("hours must be a number") from None

    if not MIN_HOURS <= hours <= MAX_HOURS:
        raise ValueError(f"a watch lasts from {MIN_HOURS} to {MAX_HOURS} hours")

    c = panels.cred_for(panel_id)
    uid = pg_users._id_of(c, username)                                         # (an unknown user is refused here)

    with session() as db:
        w = db.scalar(select(UserWatch).where(UserWatch.panel_id == panel_id, UserWatch.username == username, UserWatch.active.is_(True), UserWatch.ends_at > ts))

        if w is None:
            if (db.scalar(select(func.count()).select_from(UserWatch).where(UserWatch.active.is_(True), UserWatch.ends_at > ts)) or 0) >= MAX_ACTIVE:
                raise ValueError(f"{MAX_ACTIVE} users are watched already: stop one first")

            w = UserWatch(panel_id=panel_id, username=username, user_id=uid, started_at=ts, ends_at=int(ts + hours * 3600), every=EVERY, active=True, last_at=0, last_used=None, last_error="")
            db.add(w)
        else:
            w.ends_at = max(w.ends_at, int(ts + hours * 3600))

        db.flush()
        wid = w.id

    sample(wid, ts)
    return status(panel_id, username, ts)


def stop(panel_id: int, username: str, ts: int | None = None) -> dict:
    """End the running watch of a user now (what it saw stays for KEEP_DAYS days)."""
    ts = ts or now()

    with session() as db:
        for w in db.scalars(select(UserWatch).where(UserWatch.panel_id == panel_id, UserWatch.username == username, UserWatch.active.is_(True))):
            w.active, w.ends_at = False, min(w.ends_at, ts)

    return status(panel_id, username, ts)
