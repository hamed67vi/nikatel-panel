"""The update of the panel itself: where a run of scripts/deploy.sh stands, whether a newer version exists, and the start of a run.
(The owner, 2026-10-17: "the update is very slow - the percentage starts again when the page is refreshed - and while it is not finished the card says a new update exists".)

  progress   scripts/deploy.sh keeps ONE small file, `update-progress.json` ({run, step, at, from, to, reason}), and replaces it at every step. `progress()` turns it into {state, percent, ...}; between two steps the
             percentage creeps by the clock of the SERVER, so every page, every tab and every refresh shows the same number, and the restart of the panel in the middle of the run loses nothing.
  status     the answer of GET /api/system/update: what runs, what GitHub has (one `ls-remote`, kept for 30 s) and - first of all - whether a run goes: then it is "updating", never "up to date" and never "a new version
             is available" (the page asks only the progress, a file, while it runs).
  start      downloads the new version (git pull), writes the first step and starts deploy.sh in its own unit, so the panel can restart under it.

The pure helpers (`percent_of`, `progress`, `version_key`) carry the decisions and are tested."""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from .. import __version__
from ..log import get_logger

log = get_logger("updater")
STATE_DIR = Path("/var/lib/nikatel")
PROGRESS_FILE = STATE_DIR / "update-progress.json"
LOG_FILE = STATE_DIR / "update.log"
SRC = Path(__file__).resolve().parent.parent.parent
STALE_AFTER = 420            # seconds a step may stay the last one written: a restart waits up to 60 s and the health check 90 s, and every step writes its own time
REMOTE_KEEP = 30             # seconds the answer of GitHub is kept
STEPS = {                    # step: (percent where it starts, percent where the next one starts, seconds it usually takes) - the percentage creeps from the first towards the second and stops short of it
    "download": (2, 12, 3), "checks": (12, 30, 4), "install": (30, 55, 6), "restart": (55, 85, 8), "health": (85, 99, 6), "rollback": (90, 98, 20),
}
_remote: dict = {"at": 0.0, "head": "", "tag": "", "ok": False}


def percent_of(step: str, age: float) -> float:
    """Pure: how far a run is, `age` seconds into `step` - creeping from where the step starts towards where the next one starts, and never reaching it."""
    a, b, tau = STEPS.get(step, STEPS["download"])
    return round(min(b - 0.5, a + (b - a) * (1 - math.exp(-max(0.0, age) / tau))), 1)


def progress(rec: dict | None, now: float) -> dict:
    """Pure: where the run is, from what deploy.sh wrote - {state: idle | running | done | failed, percent, step, from, to, reason, at}. A run whose last step is older than STALE_AFTER is a run that stopped."""
    step = str((rec or {}).get("step") or "")

    if not rec or not step:
        return {"state": "idle", "percent": 0}

    at = float(rec.get("at") or 0)
    out = {"step": step, "run": rec.get("run"), "from": str(rec.get("from") or ""), "to": str(rec.get("to") or ""), "at": at, "reason": str(rec.get("reason") or "")}

    if step == "done":
        return {**out, "state": "done", "percent": 100}

    if step == "failed":
        return {**out, "state": "failed", "percent": 0, "reason": out["reason"] or "the update stopped"}

    if now - at > STALE_AFTER:
        return {**out, "state": "failed", "percent": 0, "reason": "the update stopped without saying why"}

    return {**out, "state": "running", "percent": percent_of(step, now - at)}


def version_key(v: str) -> tuple[int, ...]:
    """Pure: "1.62.1" as (1, 62, 1) to compare two versions; () for what is not a version."""
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return ()


def read_progress(now: float | None = None) -> dict:
    """The run as its file says it ("idle" when there is no file, or it cannot be read)."""
    try:
        rec = json.loads(PROGRESS_FILE.read_text())
    except (OSError, ValueError):
        rec = None

    return progress(rec if isinstance(rec, dict) else None, time.time() if now is None else now)


def write_progress(rec: dict) -> None:
    """Replace the file in one move (a reader never sees half of it); a disk that refuses is logged, the update goes on."""
    try:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROGRESS_FILE.with_name(PROGRESS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, PROGRESS_FILE)
    except OSError as e:
        log.warning("the progress of the update could not be written: %s", e)


def _git(*args: str, timeout: int = 40) -> str:
    try:
        return subprocess.run(["git", "-C", str(SRC), *args], check=False, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _repo_url() -> str:
    """The web address of the GitHub repository the panel was installed from ("" when it is not GitHub)."""
    m = re.match(r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", _git("remote", "get-url", "origin"))
    return f"https://github.com/{m.group(1)}" if m else ""


def remote() -> dict:
    """What GitHub has - {head, tag, ok}: the newest commit and the highest version tag from ONE `ls-remote` (kept 30 s). When GitHub cannot be reached the last answer stays and `ok` is false."""
    now = time.time()

    if now - _remote["at"] < REMOTE_KEEP:
        return dict(_remote)

    head, best = "", ()

    for ln in _git("ls-remote", "origin", "HEAD", "refs/tags/v*", timeout=10).splitlines():
        sha, _, ref = ln.partition("\t")
        m = re.fullmatch(r"refs/tags/v(\d+)\.(\d+)\.(\d+)", ref.strip())

        if ref.strip() == "HEAD":
            head = sha
        elif m:
            best = max(best, tuple(int(x) for x in m.groups()))

    _remote.update(at=now, ok=bool(head or best))

    if head or best:
        _remote.update(head=head, tag=".".join(str(x) for x in best))

    return dict(_remote)


def log_tail(lines: int = 12) -> str:
    """The last lines of the log of the update runs ("" when there is none)."""
    try:
        return "\n".join(LOG_FILE.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def status() -> dict:
    """The whole answer of GET /api/system/update: the version that runs, what GitHub has, and the run that goes. `up_to_date` is true only when GitHub answered, nothing newer is there and no run goes."""
    prog = read_progress()
    cur = _git("rev-parse", "--short", "HEAD")
    gh = remote()
    newest = str(gh["head"])[:7]
    behind = bool(newest and cur) and not newest.startswith(cur[:7])
    newer = bool(gh["tag"]) and version_key(str(gh["tag"])) > version_key(__version__)      # (the VERSION that runs, not the files on the disk: after a failed update the disk may already be ahead of the process)
    repo = _repo_url()
    return {"ok": True, "version": __version__, "installed": cur, "newest": newest, "checked": bool(gh["ok"]), "progress": prog, "last_log": log_tail(),
            "up_to_date": bool(gh["ok"]) and prog["state"] != "running" and not behind and not newer,
            "latest": gh["tag"], "newer": newer, "notes_url": f"{repo}/releases/tag/v{gh['tag']}" if repo and gh["tag"] and newer else ""}


def _failed(rec: dict, why: str) -> dict:
    write_progress({**rec, "step": "failed", "at": time.time(), "reason": why})
    return {"ok": False, "error": why}


def _tree_version() -> str:
    """The version the files on the disk carry ("" when they cannot be read): what the update goes to."""
    try:
        m = re.search(r'^__version__\s*=\s*"([^"]+)"', (SRC / "nikatel" / "__init__.py").read_text(), re.MULTILINE)
    except OSError:
        return ""

    return m.group(1) if m else ""


def start() -> dict:
    """Start an update: no second run, download the new version, write the first step, run deploy.sh in its own unit (the panel restarts under it). A download that fails answers at once, with git's own words."""
    if read_progress()["state"] == "running":
        return {"ok": False, "error": "An update is already running"}

    run = int(time.time())
    rec = {"run": run, "step": "download", "at": run, "from": __version__, "to": "", "reason": ""}
    write_progress(rec)

    try:
        pull = subprocess.run(["git", "-C", str(SRC), "pull", "-q", "--ff-only", "origin", "main"], check=False, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as e:
        return _failed(rec, "could not download the new version: " + str(e)[:160])

    if pull.returncode != 0:
        said = [x.strip() for x in (pull.stderr or pull.stdout).splitlines() if x.strip()]
        return _failed(rec, "could not download the new version: " + (said[-1][:200] if said else "git pull failed"))

    to = _tree_version()
    write_progress({**rec, "to": to})
    cmd = f"cd {shlex.quote(str(SRC))} && echo '== update started' && bash scripts/deploy.sh"

    try:
        subprocess.run(["systemd-run", "--no-block", "--unit", f"nikatel-update-{run}", f"--setenv=UPDATE_RUN={run}", f"--setenv=UPDATE_FROM={__version__}",
                        f"--property=StandardOutput=append:{LOG_FILE}", f"--property=StandardError=append:{LOG_FILE}", "bash", "-c", cmd], check=True, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return _failed({**rec, "to": to}, "could not start the update: " + str(e)[:160])

    return {"ok": True, "started": True, "to": to}
