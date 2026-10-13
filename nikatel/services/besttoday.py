"""The best tunnels of the day: every tunnel is looked at all the time (a health check each minute, a measurement of latency, jitter and loss every half hour)
and ranked by how it behaved in the last hours - how often it answered, how fast, how steady, how often it broke.

Nothing is built or changed: the ranking reads the checks the panel already writes (`tunnel_checks`) and the last quality measurement of each tunnel.
`refresh()` is the job that keeps the measurements fresh (read-only: pings over the GRE links)."""
from __future__ import annotations

import itertools
import statistics
import time

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Tunnel, TunnelCheck, is_temp
from ..ssh import SSHError

log = get_logger("besttoday")
MIN_SAMPLES = 20             # fewer checks than this: too little to say anything


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def score_of(avail: float, rtt: float | None, jitter: float | None, loss: float | None, outages: int) -> int:
    """0-100, pure: staying up counts most, then the latency, the packet loss, the steadiness, and every break takes a little."""
    a = _clamp((avail - 85.0) / 15.0)                                 # 85 % up = nothing, 100 % = full
    lat = _clamp(1.0 - ((rtt if rtt is not None else 150.0) - 40.0) / 260.0)          # 40 ms = full, 300 ms = nothing
    jit = _clamp(1.0 - (jitter if jitter is not None else 20.0) / 60.0)
    lo = _clamp(1.0 - (loss if loss is not None else 0.0) / 10.0)
    raw = 100.0 * (0.45 * a + 0.2 * lat + 0.2 * lo + 0.15 * jit) - min(25.0, 4.0 * outages)
    return round(max(0.0, min(100.0, raw)))


def outages_in(flags: list[bool]) -> int:
    """Pure: how many times the tunnel went from answering to not answering."""
    return sum(1 for a, b in itertools.pairwise(flags) if a and not b)


def summarise(checks: list[tuple[int, bool, float | None]]) -> dict:
    """Pure: [(ts, ok, rtt)] oldest first -> availability %, median latency, jitter (standard deviation of the latency), number of breaks."""
    flags = [c[1] for c in checks]
    rtts = [c[2] for c in checks if c[1] and c[2] is not None]
    avail = 100.0 * sum(flags) / len(flags) if flags else 0.0
    return {"samples": len(flags), "avail": round(avail, 2), "rtt_ms": round(statistics.median(rtts), 1) if rtts else None,
            "jitter_ms": round(statistics.pstdev(rtts), 1) if len(rtts) > 1 else None, "outages": outages_in(flags)}


def rank(hours: int = 24) -> dict:
    """The enabled tunnels, the best first. `best_for` marks the best one to each foreign server (when several tunnels lead there)."""
    now = int(time.time())
    since = now - hours * 3600

    with session() as db:
        tunnels = [t for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name)]
        info = {t.id: {"id": t.id, "name": t.name, "iran": t.iran.name, "foreign": t.foreign.name, "type": t.type, "transport": t.transport or "", "state": t.state,
                       "loss_pct": ((t.record or {}).get("quality") or {}).get("loss_pct"), "measured_at": ((t.record or {}).get("quality") or {}).get("at") or 0} for t in tunnels}
        rows_by: dict[int, list[tuple[int, bool, float | None]]] = {tid: [] for tid in info}

        for tid, ts, ok, rtt in db.execute(select(TunnelCheck.tunnel_id, TunnelCheck.ts, TunnelCheck.ok, TunnelCheck.rtt_ms).where(TunnelCheck.ts >= since).order_by(TunnelCheck.ts)):
            if tid in rows_by:
                rows_by[tid].append((ts, bool(ok), rtt))

    out = []

    for tid, base in info.items():
        s = summarise(rows_by[tid])
        enough = s["samples"] >= MIN_SAMPLES
        out.append({**base, **s, "score": score_of(s["avail"], s["rtt_ms"], s["jitter_ms"], base["loss_pct"], s["outages"]) if enough else None})

    out.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["name"]))
    best: dict[str, int] = {}

    for r in out:
        if r["score"] is not None and r["foreign"] not in best:
            best[r["foreign"]] = r["id"]

    multi = {f for f in best if sum(1 for r in out if r["foreign"] == f) > 1}

    for i, r in enumerate(out, 1):
        r["place"] = i if r["score"] is not None else None
        r["best_for"] = best.get(r["foreign"]) == r["id"] and r["foreign"] in multi

    return {"hours": hours, "at": now, "rows": out}


def refresh() -> None:
    """The job: the latency, jitter and loss of every tunnel are measured again (read-only pings over the GRE links), one tunnel after the other."""
    from . import tunnel_test

    try:
        res = tunnel_test.quality_all()
    except (SSHError, ValueError) as e:
        log.warning("quality refresh failed: %s", str(e)[:120])
        return

    log.info("quality refreshed: %d of %d tunnels measured", sum(1 for r in res if not r.get("error")), len(res))
