"""The daily report: one Telegram message at the configured local hour."""
from __future__ import annotations

import time

from sqlalchemy import func, select

from ..db import session
from ..log import get_logger
from ..models import Server, Setting, Tunnel, TunnelCheck
from . import settings as st
from .events import emit
from .telegram import send_text

log = get_logger("report")


def local_day(now: int, tz_offset_minutes: int) -> tuple[str, int]:
    """(YYYY-MM-DD, hour) in the configured local time."""
    t = time.gmtime(now + tz_offset_minutes * 60)
    return time.strftime("%Y-%m-%d", t), t.tm_hour


def build(now: int | None = None) -> str:
    now = now or int(time.time())
    since = now - 86400

    with session() as db:
        tunnels = list(db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))))
        servers = list(db.scalars(select(Server).where(Server.enabled.is_(True))))
        rows = db.execute(select(TunnelCheck.tunnel_id, func.count(), func.sum(TunnelCheck.ok)).where(TunnelCheck.ts >= since).group_by(TunnelCheck.tunnel_id)).all()

    avail = {tid: (int(ok or 0), int(n)) for tid, n, ok in rows}
    up = [t for t in tunnels if t.state == "up"]
    down = [t for t in tunnels if t.state == "down"]
    deg = [t for t in tunnels if t.state == "degraded"]
    off = [s for s in servers if s.online is False]
    lines = [f"📊 Daily report\nTunnels: {len(up)}/{len(tunnels)} up" + (f", {len(down)} down" if down else "") + (f", {len(deg)} degraded" if deg else ""),
             f"Servers: {len(servers) - len(off)}/{len(servers)} online"]

    worst = sorted(((t, avail.get(t.id, (0, 0))) for t in tunnels if avail.get(t.id, (0, 0))[1]), key=lambda x: x[1][0] / x[1][1])[:5]
    bad = [(t, ok, n) for t, (ok, n) in worst if ok < n]

    if bad:
        lines.append("\nLeast available (24 h):")
        lines += [f"• {t.name}: {ok * 100 // n}% ({n - ok} failed checks)" for t, ok, n in bad]

    if down:
        lines.append("\nDown now: " + ", ".join(t.name for t in down))

    if off:
        lines.append("Offline servers: " + ", ".join(s.name for s in off))

    hot = [s for s in servers if (s.facts or {}).get("cpu", 0) >= 85 or (s.facts or {}).get("ram", 0) >= 90 or (s.facts or {}).get("disk", 0) >= 90]

    if hot:
        lines.append("Under pressure: " + ", ".join(f"{s.name} (cpu {s.facts.get('cpu')}%, ram {s.facts.get('ram')}%, disk {s.facts.get('disk')}%)" for s in hot[:6]))

    return "\n".join(lines)


def run(force: bool = False) -> str | None:
    conf = st.get("report")

    if not conf.get("enabled") and not force:
        return None

    now = int(time.time())
    day, hour = local_day(now, int(conf.get("tz_offset_minutes", 0)))

    with session() as db:
        marker = db.get(Setting, "report_last_day")
        last = marker.value.get("day") if marker is not None and isinstance(marker.value, dict) else ""

    if not force and (hour < int(conf.get("hour", 9)) or last == day):
        return None

    text = build(now)
    ok, why = send_text(text, require_enabled=not force)

    with session() as db:
        marker = db.get(Setting, "report_last_day")

        if marker is None:
            db.add(Setting(key="report_last_day", value={"day": day}))
        else:
            marker.value = {"day": day}

        emit(db, "info", "report", "Daily report " + ("sent." if ok else f"not sent: {why}"), "")

    return text
