import time

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from .. import __version__
from ..auth import current_user
from ..config import get_settings
from ..db import session
from ..models import Job, Server, Tunnel

router = APIRouter(tags=["system"])
_STARTED = int(time.time())


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
