"""What the USER gets, as one number 0-100 (the owner, 2026-10-21: "for the tests of the tunnels the first and most important measure must be that the user gets the best speed and ping - and it must count when the
best tunnel of the day is chosen in building a tunnel").

Two things count most, and together they are 75 % of the mark:
  speed  45 %  what ONE user downloads: the one-connection download counts 60 % and the eight connections together 40 % (a phone downloads over ONE connection), the upload 15 % of the speed and never more than the
               download; 300 Mb/s is full marks - a user does not feel more than that, so above it the PING decides between two tunnels
  ping   30 %  30 ms and less is full marks, 180 ms and more is none (a straight line between)
and then the quality of the path: packets lost 15 % (10 % lost = none), jitter 10 % (20 ms = none).
One function, used by the tests of every kind of tunnel (`build_other.quality_score`, `tunnel_test.score`) and by the choice of the best tunnel of a pair (`knowledge.ranked`), so that a number written down last week and
a number measured today are the same kind of number."""
from __future__ import annotations

SPEED_FULL = 300.0           # Mb/s of what one user gets: full marks
ONE_CONNECTION = 0.6         # the share of the one-connection download in "the speed a user gets"
PING_BEST = 30.0
PING_NONE = 180.0
W_SPEED, W_PING, W_LOSS, W_JITTER = 0.45, 0.30, 0.15, 0.10


def speed_points(q: dict) -> float:
    d8, d1, up = q.get("down_mbps") or 0.0, q.get("down1_mbps"), q.get("up_mbps") or 0.0
    down = float(d8) if d1 is None else ONE_CONNECTION * float(d1) + (1.0 - ONE_CONNECTION) * float(d8)
    return min(100.0, (down * 0.85 + min(float(up), down) * 0.15) / (SPEED_FULL / 100.0))


def ping_points(ms: float | None) -> float:
    return 0.0 if ms is None else max(0.0, min(100.0, 100.0 - (float(ms) - PING_BEST) / (PING_NONE - PING_BEST) * 100.0))


def user_score(q: dict | None) -> int:
    """0-100 of a measurement {latency_ms, jitter_ms, loss_pct, down_mbps, down1_mbps, up_mbps}; 0 for no measurement or one that says "error"."""
    if not q or q.get("error") or q.get("latency_ms") is None:
        return 0

    jit = max(0.0, 100.0 - (q.get("jitter_ms") or 0) * 5)
    loss = max(0.0, 100.0 - (q.get("loss_pct") or 0) * 10)
    return round(speed_points(q) * W_SPEED + ping_points(q["latency_ms"]) * W_PING + loss * W_LOSS + jit * W_JITTER)


def has_speed(q: dict | None) -> bool:
    """Does the measurement say how fast a user downloads (a number of the speed, not only pings)?"""
    return bool(q) and (q.get("down_mbps") is not None or q.get("down1_mbps") is not None)
