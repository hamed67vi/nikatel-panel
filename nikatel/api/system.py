import re
import time
from collections import deque

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from .. import __version__
from ..auth import current_user
from ..config import get_settings
from ..db import session
from ..models import Job, Server, Tunnel

router = APIRouter(tags=["system"])
_STARTED = int(time.time())
_LOG_LINE = re.compile(r"^(\S+) (\w+)\s+(\S+)\s+(.*?)(?: rid=(\S+))?$")


@router.get("/health")
def health():
    """Unauthenticated liveness check for deploy scripts and uptime monitors."""
    with session() as db:
        db.execute(select(func.count(Server.id)))

    return {"ok": True, "version": __version__, "uptime": int(time.time()) - _STARTED}


@router.get("/overview", dependencies=[Depends(current_user)])
def overview():
    s = get_settings()
    now = int(time.time())

    with session() as db:
        servers = list(db.scalars(select(Server)))
        tunnels = list(db.scalars(select(Tunnel)))
        jobs_ = list(db.scalars(select(Job)))

    stale = [j.name for j in jobs_ if j.enabled and j.last_ok and now - j.last_ok > j.interval * 3]
    failing = [j.name for j in jobs_ if j.last_error]

    return {
        "ok": True,
        "version": __version__,
        "read_only": s.read_only,
        "servers": {"total": len(servers), "online": sum(1 for x in servers if x.online), "offline": sum(1 for x in servers if x.online is False),
                    "iran": sum(1 for x in servers if x.role == "iran"), "foreign": sum(1 for x in servers if x.role == "foreign")},
        "tunnels": {"total": len(tunnels), "up": sum(1 for t in tunnels if t.state == "up"), "down": sum(1 for t in tunnels if t.state == "down"),
                    "degraded": sum(1 for t in tunnels if t.state == "degraded"), "unknown": sum(1 for t in tunnels if t.state in ("unknown", "starting"))},
        "jobs": {"total": len(jobs_), "stale": stale, "failing": failing},
    }


@router.get("/settings", dependencies=[Depends(current_user)])
def settings_view():
    """The effective settings - values only, never the secret key or key paths."""
    s = get_settings()
    return {"ok": True, "version": __version__, "read_only": s.read_only, "log_level": s.log_level,
            "server_sample_interval": s.server_sample_interval, "tunnel_check_interval": s.tunnel_check_interval,
            "session_hours": s.session_hours, "ssh_workers": s.ssh_workers, "listen_port": s.listen_port,
            "env_file": "/etc/nikatel/nikatel.env"}


@router.get("/logs", dependencies=[Depends(current_user)])
def logs(lines: int = Query(300, ge=1, le=2000), level: str = "", domain: str = "", q: str = ""):
    """The tail of the panel's own log, newest first, filtered. Lines are already secret-masked."""
    path = get_settings().log_dir / "nikatel.log"

    if not path.exists():
        return {"ok": True, "lines": [], "note": "no log file yet"}

    want_level = level.upper()
    needle = q.lower()
    out: deque = deque(maxlen=lines)

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.rstrip("\n")
            m = _LOG_LINE.match(raw)

            if not m:
                if out:   # continuation line of a traceback belongs to the previous entry
                    out[-1]["text"] += "\n" + raw
                continue

            ts, lvl, dom, text, rid = m.groups()

            if (want_level and lvl != want_level) or (domain and dom != domain) or (needle and needle not in raw.lower()):
                continue

            out.append({"ts": ts, "level": lvl, "domain": dom, "text": text, "rid": rid or ""})

    return {"ok": True, "lines": list(reversed(out))}
