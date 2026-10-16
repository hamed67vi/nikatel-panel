import re
import time
from collections import deque

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import __version__, jobs
from ..auth import current_user, session_user
from ..config import get_settings
from ..db import session
from ..log import get_logger, mask
from ..models import Job, Panel, Server, Tunnel

router = APIRouter(tags=["system"])
_STARTED = int(time.time())
_LOG_LINE = re.compile(r"^(\S+) (\w+)\s+(\S+)\s+(.*?)(?: rid=(\S+))?$")
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}\b")
clog = get_logger("client")
_CLIENT_HITS: deque = deque()          # when the browser last told about a script error (a loop of errors must not fill the log)


@router.get("/health")
def health(request: Request):
    """Liveness check for deploy scripts and uptime monitors: `{"ok": true}` for everyone; the version and the uptime only to a signed-in session or token (they tell a stranger what to try)."""
    with session() as db:
        db.execute(select(func.count(Server.id)))

    if session_user(request):
        return {"ok": True, "version": __version__, "uptime": int(time.time()) - _STARTED}

    return {"ok": True}


@router.get("/health/detail", dependencies=[Depends(current_user)])
def health_detail():
    """What is wrong with the panel itself, in plain words: the database, the jobs that are late or failing, the SSH key, the free disk, the last backup, the errors of the last hour.
    `/api/health` only says that the panel answers; this one says what to look at when it does not run well."""
    import os
    import shutil

    from ..services import backup

    s = get_settings()
    now = int(time.time())
    problems: list[dict] = []
    checks: dict[str, dict] = {}

    def note(name: str, ok: bool, text: str, hint: str = "") -> None:
        checks[name] = {"ok": ok, "text": text}

        if not ok:
            problems.append({"what": name, "text": text, "hint": hint})

    try:
        with session() as db:
            n = db.scalar(select(func.count(Server.id))) or 0
            jobs_ = list(db.scalars(select(Job)))

        size = os.path.getsize(s.data_dir / "nikatel.db") // (1024 * 1024) if (s.data_dir / "nikatel.db").exists() else 0
        note("database", True, f"answers, {n} servers, {size} MB")
    except Exception as e:  # noqa: BLE001  a broken database must be reported, not raised
        jobs_ = []
        note("database", False, "does not answer: " + str(e)[:100], "Restore the last backup (docs/operations.md) or look at the log of the panel.")

    late = [j.name for j in jobs_ if j.enabled and j.last_ok and now - j.last_ok > j.interval * 3]
    failing = [j.name for j in jobs_ if j.last_error]
    note("jobs", not late and not failing, f"{len(jobs_)} background jobs" + (f", late: {', '.join(late)}" if late else "") + (f", failing: {', '.join(failing)}" if failing else ""),
         "A late or failing job shows its last error in Settings → About; a restart of the panel starts every job again.")
    key = s.ssh_key
    note("ssh_key", key.exists() and os.access(key, os.R_OK), "the panel's SSH key is there" if key.exists() else "the panel's SSH key is missing", "Without it the panel cannot log in to any server: Settings shows how to make a new one.")

    try:
        du = shutil.disk_usage(s.data_dir if s.data_dir.exists() else "/")
        free = round(100.0 * du.free / du.total, 1)
        note("disk", free >= 10, f"{free} % free ({du.free // (1024 ** 3)} GB)", "Less than 10 % is free where the database lives: clear old backups and logs.")
    except OSError as e:
        note("disk", False, "cannot be read: " + str(e)[:80])

    last = int(backup._marker("backup_last_run").get("at", 0))      # when the daily backup last ran (0 = never)

    note("backup", last == 0 or now - last < 3 * 86400, "no backup has run yet" if not last else f"last backup {round((now - last) / 3600)} h ago", "The last backup is older than three days: Settings → Backups.")
    errors = 0
    path = s.log_dir / "nikatel.log"

    if path.exists():
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))

        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                m = _LOG_LINE.match(raw.rstrip("\n"))

                if m and m.group(2) == "ERROR" and m.group(1) >= since:
                    errors += 1

    note("errors", errors < 20, f"{errors} errors in the log in the last hour", "Many errors: Logs → What went wrong shows where they come from (the panel itself, a server, or outside).")
    return {"ok": not problems, "version": __version__, "uptime": now - _STARTED, "problems": problems, "checks": checks}


class ClientError(BaseModel):
    message: str = Field(max_length=400)
    source: str = Field("", max_length=200)
    line: int = Field(0, ge=0, le=10_000_000)
    stack: str = Field("", max_length=1500)
    page: str = Field("", max_length=60)


@router.post("/client-error", dependencies=[Depends(current_user)])
def client_error(body: ClientError):
    """The page tells the panel about a script error (window.onerror / unhandled rejection): ONE warning line in the log with the request id, so what the owner saw can be found. At most 20 a minute."""
    now = time.time()

    while _CLIENT_HITS and now - _CLIENT_HITS[0] > 60:
        _CLIENT_HITS.popleft()

    if len(_CLIENT_HITS) >= 20:
        return {"ok": True, "dropped": True}

    _CLIENT_HITS.append(now)
    clog.warning("browser error on %s: %s (%s:%s) %s", body.page or "?", body.message, body.source.rsplit("/", 1)[-1][:60], body.line, body.stack.replace("\n", " | ")[:700])
    return {"ok": True}


def _recent_problems(limit: int = 25) -> list[dict]:
    """The newest warnings and errors of the log (the last 400 KB of it), secret-masked, with their request ids and the addresses cut to their first two numbers."""
    path = get_settings().log_dir / "nikatel.log"

    if not path.exists():
        return []

    size = path.stat().st_size

    with path.open("rb") as f:
        f.seek(max(0, size - 400_000))
        raw = f.read().decode("utf-8", errors="replace")

    out: list[dict] = []

    for ln in raw.splitlines():
        m = _LOG_LINE.match(ln)

        if m and m.group(2) in ("WARNING", "ERROR"):
            out.append({"ts": m.group(1), "level": m.group(2), "domain": m.group(3), "text": _IPV4.sub(r"\1.\2.x.x", mask(m.group(4)))[:300], "rid": m.group(5) or ""})

    return out[-limit:][::-1]


@router.get("/system/diagnostics", dependencies=[Depends(current_user)])
def diagnostics():
    """Everything needed to describe a problem, in one answer to copy: versions, the health checks, every job, the counts, the newest warnings and errors. No key, no password, no address of a server (the log lines have theirs cut)."""
    import platform
    import sys

    s = get_settings()
    now = int(time.time())
    health = health_detail()

    with session() as db:
        servers = list(db.scalars(select(Server)))
        tunnels = list(db.scalars(select(Tunnel)))
        job_rows = list(db.scalars(select(Job)))
        n_panels = db.scalar(select(func.count(Panel.id))) or 0

    return {"ok": True, "generated": now, "version": __version__, "python": sys.version.split()[0], "platform": platform.platform(), "uptime_s": now - _STARTED, "read_only": s.read_only,
            "settings": {"log_level": s.log_level, "session_hours": s.session_hours, "tunnel_check_interval": s.tunnel_check_interval, "server_sample_interval": s.server_sample_interval, "ssh_workers": s.ssh_workers},
            "health": {"ok": health["ok"], "problems": health["problems"], "checks": health["checks"]},
            "counts": {"servers": len(servers), "servers_online": sum(1 for x in servers if x.online), "tunnels": len(tunnels), "tunnels_up": sum(1 for x in tunnels if x.state == "up"), "panels": n_panels},
            "jobs": [{"name": j.name, "interval": j.interval, "enabled": j.enabled, "running": j.running, "runs": j.runs, "failures": j.failures, "last_ok_ago_s": (now - j.last_ok) if j.last_ok else None,
                      "last_duration_ms": j.last_duration_ms, "last_error": mask(j.last_error or "")[:200]} for j in sorted(job_rows, key=lambda x: x.name)],
            "dead_job_threads": jobs.dead(), "recent_problems": _recent_problems()}


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


def _repo_url() -> str:
    """The web address of the GitHub repository the panel was installed from ("" when it is not GitHub)."""
    m = re.match(r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", _git("remote", "get-url", "origin"))
    return f"https://github.com/{m.group(1)}" if m else ""


def _newest_tag() -> str:
    """The highest version tag (v1.2.3) of the remote, as 1.2.3 ("" when it cannot be read)."""
    best: tuple[int, ...] = ()

    for ln in _git("ls-remote", "--tags", "--refs", "origin", "v*").splitlines():
        m = re.fullmatch(r".*\trefs/tags/v(\d+)\.(\d+)\.(\d+)", ln.strip())

        if m:
            best = max(best, tuple(int(x) for x in m.groups()))

    return ".".join(str(x) for x in best)


def update_progress(text: str, quiet_s: float = 0.0) -> dict:
    """Pure: where the last update run is, from its log - {state: idle | running | done | failed, percent}. The steps of scripts/deploy.sh are the markers (a released version skips the tests; when they run they take 10 to 35 %, from the percent pytest prints); `quiet_s` = seconds since the log last grew."""
    i = max(text.rfind("== update started"), text.rfind("== no secrets in the repo"))

    if i < 0:
        return {"state": "idle", "percent": 0}

    run = text[i:]

    if "DEPLOY OK" in run:
        return {"state": "done", "percent": 100}

    if "HEALTH CHECK FAILED" in run or "STILL DOWN" in run or "rolled back" in run or ("== no secrets" not in run and ("fatal:" in run or "error:" in run)):
        return {"state": "failed", "percent": 90}

    if quiet_s > 240:          # nothing was written for four minutes and it did not finish: it stopped (a failed test or step ends the script)
        return {"state": "failed", "percent": 0}

    if "== install" in run:
        pct = 60
    elif "== migrations" in run:
        pct = 35
    elif "== tests skipped" in run:
        pct = 15
    elif "== tests" in run:
        found = re.findall(r"\[\s*(\d+)%\]", run.split("== tests", 1)[1])
        pct = 10 + round(0.25 * min(100, int(found[-1]))) if found else 10
    elif "== no secrets" in run:
        pct = 8
    else:
        pct = 3
    return {"state": "running", "percent": pct}

@router.get("/system/update", dependencies=[Depends(current_user)])
def update_status():
    """Installed commit vs. the newest on GitHub, the newest version number with the link of its release notes, and the log of the last update run."""
    from pathlib import Path

    cur = _git("rev-parse", "--short", "HEAD")
    remote = _git("ls-remote", "origin", "HEAD").split()
    newest = remote[0][:7] if remote else ""
    latest = _newest_tag()
    repo = _repo_url()

    def key(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return ()

    log_path = Path("/var/lib/nikatel/update.log")
    tail = ""

    if log_path.exists():
        tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-12:])

    quiet = time.time() - log_path.stat().st_mtime if log_path.exists() else 0.0
    progress = update_progress(log_path.read_text(errors="replace")[-60000:] if log_path.exists() else "", quiet)

    return {"ok": True, "version": __version__, "installed": cur, "newest": newest, "up_to_date": bool(newest) and cur != "" and newest.startswith(cur[:7]), "last_log": tail, "progress": progress,
            "latest": latest, "newer": bool(latest) and key(latest) > key(__version__), "notes_url": f"{repo}/releases/tag/v{latest}" if repo and latest else ""}


@router.post("/system/update", dependencies=[Depends(current_user)])
def update_now():
    """Pull the newest code and run deploy.sh (tests, restart, health check, rollback) in a transient unit,
    so the panel can restart under it. Watch /system/update for the log."""
    import subprocess
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent.parent
    cmd = f"cd {src} && echo '== update started' && git pull -q --ff-only origin main && bash scripts/deploy.sh"

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
