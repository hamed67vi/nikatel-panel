import re
import time
from collections import deque

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from .. import __version__, jobs
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
    """The dashboard's numbers: servers online and offline, tunnels by state, jobs stale or failing."""
    s = get_settings()
    now = int(time.time())

    with session() as db:
        servers = list(db.scalars(select(Server)))
        tunnels = list(db.scalars(select(Tunnel)))
        jobs_ = list(db.scalars(select(Job)))

    stale = [j.name for j in jobs_ if j.enabled and j.last_ok and now - j.last_ok > j.interval * 3]
    failing = [j.name for j in jobs_ if j.last_error]
    revived = jobs.ensure_alive()                                   # a job whose thread died is started again right here (and said below)

    if revived:
        failing = sorted(set(failing) | set(revived))

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


def _git(*args: str) -> str:
    import subprocess
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent.parent

    try:
        return subprocess.run(["git", "-C", str(src), *args], check=False, capture_output=True, text=True, timeout=40).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


@router.get("/system/update", dependencies=[Depends(current_user)])
def update_status():
    """Installed commit vs. the newest on GitHub, and the log of the last update run."""
    from pathlib import Path

    cur = _git("rev-parse", "--short", "HEAD")
    remote = _git("ls-remote", "origin", "HEAD").split()
    newest = remote[0][:7] if remote else ""
    log_path = Path("/var/lib/nikatel/update.log")
    tail = ""

    if log_path.exists():
        tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-12:])

    return {"ok": True, "version": __version__, "installed": cur, "newest": newest, "up_to_date": bool(newest) and cur != "" and newest.startswith(cur[:7]), "last_log": tail}


@router.post("/system/update", dependencies=[Depends(current_user)])
def update_now():
    """Pull the newest code and run deploy.sh (tests, restart, health check, rollback) in a transient unit,
    so the panel can restart under it. Watch /system/update for the log."""
    import subprocess
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent.parent
    cmd = f"cd {src} && git pull -q --ff-only origin main && bash scripts/deploy.sh"

    try:
        subprocess.run(["systemd-run", "--no-block", "--unit", f"nikatel-update-{int(time.time())}", "--property=StandardOutput=append:/var/lib/nikatel/update.log",
                        "--property=StandardError=append:/var/lib/nikatel/update.log", "bash", "-c", cmd], check=True, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": "could not start the update: " + str(e)[:160]}

    return {"ok": True, "started": True}


@router.get("/logs", dependencies=[Depends(current_user)])
def logs(lines: int = Query(300, ge=1, le=2000), level: str = "", domain: str = "", q: str = "", origin: str = Query("", pattern="^(panel|server|outside|unknown)?$")):
    """The tail of the panel's own log, newest first, filtered (level, domain, text, and `origin`: panel / server / outside). Each warning or error carries its origin. Lines are already secret-masked."""
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

            if (want_level and lvl != want_level) or (domain and dom != domain) or (needle and not all(w in raw.lower() for w in needle.split())):
                continue

            out.append({"ts": ts, "level": lvl, "domain": dom, "text": text, "rid": rid or ""})

    from ..services.diagnosis import classify, is_plain_answer

    rows = list(reversed(out))

    for r in rows:
        r["origin"] = classify(r["text"])["origin"] if r["level"] in ("WARNING", "ERROR") and not is_plain_answer(r["text"]) else ""

    if origin:
        rows = [r for r in rows if r["origin"] == origin]

    return {"ok": True, "lines": rows}


@router.get("/logs/summary", dependencies=[Depends(current_user)])
def logs_summary(hours: int = Query(24, ge=1, le=168)):
    """What went wrong in the last hours, grouped: how many findings are the panel's own, a service on your server, or outside the panel, with the commonest sentences and what each means."""
    from datetime import datetime, timedelta, timezone

    from ..services.diagnosis import group_findings

    path = get_settings().log_dir / "nikatel.log"

    if not path.exists():
        return {"ok": True, "hours": hours, "by_origin": {}, "groups": [], "total": 0}

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    keep: deque = deque(maxlen=30000)

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            m = _LOG_LINE.match(raw.rstrip("\n"))

            if m and m.group(2) in ("WARNING", "ERROR") and m.group(1) >= since:
                keep.append({"ts": m.group(1), "level": m.group(2), "domain": m.group(3), "text": m.group(4)})

    return {"ok": True, "hours": hours, **group_findings(list(keep))}
