"""How a server looked over the last hours, as a strip of cells (the Lab page, 1.49.0). The panel samples every server over SSH every two minutes and keeps the samples for a week, so a cell
that holds no sample at all is a time the panel did not hear from the server. Only the table of samples is read; nothing is asked of a server."""
from __future__ import annotations

import time
from collections.abc import Iterable

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from ..models import Server

STEP = 120                    # seconds between two samples of a server (config.server_sample_interval)
GOOD = 0.5                    # a cell that holds at least this share of the samples it should hold is "up"
UP, PART, DOWN, NONE = "2", "1", "0", "-"
MAX_HOURS = 168               # the samples are kept for 7 days (facts.KEEP_SAMPLES_SECONDS)


def build_strip(counts: dict[int, int], start: int, width: int, buckets: int, since: int = 0, step: int = STEP) -> str:
    """Pure: one character per cell, oldest first: 2 = answered, 1 = answered now and then, 0 = silent, - = the server did not exist yet (`since` = when it was added)."""
    out = []

    for i in range(buckets):
        b0, b1 = start + i * width, start + (i + 1) * width

        if b1 <= since:
            out.append(NONE)
            continue

        expected = (b1 - max(b0, since)) / step
        n = counts.get(i, 0)
        out.append(UP if n >= max(1.0, GOOD * expected) else PART if n > 0 else DOWN)

    return "".join(out)


def up_percent(strip: str) -> int | None:
    """Pure: the share of the time the server was heard (a cell of 'now and then' counts half); None when the strip holds no cell with a verdict."""
    up, part, down = strip.count(UP), strip.count(PART), strip.count(DOWN)
    total = up + part + down
    return round(100 * (up + part / 2) / total) if total else None


def strips(db: Session, server_ids: Iterable[int], hours: int = 24, buckets: int = 48, now: int | None = None) -> dict[int, dict]:
    """{server id: {strip, up}} for the servers over the last `hours`, in `buckets` cells: ONE grouped question to the table of samples, whatever the number of servers."""
    ids = sorted({int(i) for i in server_ids})
    hours, buckets = max(1, min(MAX_HOURS, int(hours))), max(6, min(168, int(buckets)))

    if not ids:
        return {}

    end = int(now if now is not None else time.time())
    width = max(60, hours * 3600 // buckets)
    start = end - buckets * width
    q = text("SELECT server_id, CAST((ts - :s) / :w AS INTEGER) AS b, COUNT(*) AS n FROM server_samples WHERE server_id IN :ids AND ts >= :s AND ts <= :e GROUP BY server_id, b").bindparams(bindparam("ids", expanding=True))
    counts: dict[int, dict[int, int]] = {i: {} for i in ids}

    for sid, b, n in db.execute(q, {"s": start, "w": width, "e": end, "ids": ids}):
        counts[sid][min(buckets - 1, max(0, b))] = counts[sid].get(min(buckets - 1, max(0, b)), 0) + n

    since = {sid: created for sid, created in db.execute(select(Server.id, Server.created_at).where(Server.id.in_(ids)))}
    out = {}

    for sid in ids:
        s = build_strip(counts[sid], start, width, buckets, since.get(sid, 0))
        out[sid] = {"strip": s, "up": up_percent(s)}

    return out


def cpu_series(db: Session, server_ids: Iterable[int], hours: int = 3, buckets: int = 24, now: int | None = None) -> dict[int, list[float | None]]:
    """{server id: the CPU percent averaged in `buckets` cells over the last `hours`, oldest first, None where the server was not heard}: ONE grouped question to the table of samples."""
    ids = sorted({int(i) for i in server_ids})
    hours, buckets = max(1, min(MAX_HOURS, int(hours))), max(6, min(96, int(buckets)))

    if not ids:
        return {}

    end = int(now if now is not None else time.time())
    width = max(60, hours * 3600 // buckets)
    start = end - buckets * width
    q = text("SELECT server_id, CAST((ts - :s) / :w AS INTEGER) AS b, AVG(cpu) AS c FROM server_samples WHERE server_id IN :ids AND ts >= :s AND ts <= :e GROUP BY server_id, b").bindparams(bindparam("ids", expanding=True))
    out: dict[int, list[float | None]] = {i: [None] * buckets for i in ids}

    for sid, b, c in db.execute(q, {"s": start, "w": width, "e": end, "ids": ids}):
        out[sid][min(buckets - 1, max(0, b))] = round(float(c), 1)

    return out
