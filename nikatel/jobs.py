"""Background jobs. Every job is registered with a name and an interval, runs in its own thread,
never dies, and writes its status to the `jobs` table so the UI can show what is going on."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select

from .db import session
from .log import get_logger, request_id
from .models import Job

log = get_logger("jobs")


@dataclass
class _Spec:
    name: str
    interval: int
    fn: Callable[[], None]
    thread: threading.Thread | None = None
    wake: threading.Event | None = None


_SPECS: dict[str, _Spec] = {}
_STOP = threading.Event()


def register(name: str, interval: int, fn: Callable[[], None]) -> None:
    _SPECS[name] = _Spec(name, interval, fn)


def run_once(name: str) -> None:
    """Run a job now (also used by the API's "run now" button and by tests)."""
    spec = _SPECS[name]
    token = request_id.set(f"job:{name}")
    t0 = time.time()

    try:
        with session() as db:
            job = db.get(Job, name) or Job(name=name, interval=spec.interval)
            db.add(job)
            job.running = True
            job.last_start = int(t0)
            job.runs = int(job.runs or 0) + 1          # (a row that is made right here has no count yet: `nikatel run-job NAME` runs a job that has no row)
    except Exception:
        log.exception("job %s: could not mark the start", name)

    try:
        spec.fn()
        err = ""
    except Exception as e:  # the boundary: a job never kills its loop
        err = str(e)[:300]
        log.exception("job %s failed", name)
    finally:
        request_id.reset(token)

    for attempt in range(3):                 # the result row: three tries over a locked database, then give up on the row (never on the job)
        try:
            with session() as db:
                job = db.get(Job, name)

                if job is not None:
                    job.running = False
                    job.last_duration_ms = int((time.time() - t0) * 1000)

                    if err:
                        job.failures = int(job.failures or 0) + 1
                        job.last_error = err
                        job.last_failure, job.last_failure_at = err, int(time.time())
                    else:
                        job.last_ok = int(time.time())
                        job.last_error = ""

            break
        except Exception:
            if attempt == 2:
                log.exception("job %s: could not write its result", name)
            else:
                time.sleep(2)


def _loop(spec: _Spec) -> None:
    """The thread of one job. NOTHING may end it: a "database is locked" while the job row was being updated once killed five job threads at the same moment
    and the panel kept running with no checks for hours. Whatever goes wrong is logged and the loop waits and goes on."""
    assert spec.wake is not None
    time.sleep(3 + (sum(map(ord, spec.name)) * 7) % 23)   # let the server come up first; every job has its own start (a fixed rate from one common start would make jobs fire in the same second for ever)

    while not _STOP.is_set():
        t_start = time.time()

        try:
            with session() as db:
                job = db.get(Job, spec.name)
                enabled = job.enabled if job is not None else True

            if enabled:
                run_once(spec.name)
        except Exception:
            log.exception("job %s: its loop hit an error (the job goes on next time)", spec.name)

        spec.wake.wait(max(1.0, spec.interval - (time.time() - t_start)))      # every `interval` from the START of the turn; a turn longer than the interval goes on at once
        spec.wake.clear()


def dead() -> list[str]:
    """The jobs whose thread is not running any more (it should never happen; the dashboard says so when it does)."""
    return [s.name for s in _SPECS.values() if s.thread is not None and not s.thread.is_alive()]


def ensure_alive() -> list[str]:
    """Start the thread again for every job whose thread died. Returns the names that were revived."""
    revived = []

    for spec in _SPECS.values():
        if spec.thread is not None and not spec.thread.is_alive() and not _STOP.is_set():
            spec.wake = threading.Event()
            spec.thread = threading.Thread(target=_loop, args=(spec,), name=f"job-{spec.name}", daemon=True)
            spec.thread.start()
            revived.append(spec.name)
            log.warning("job %s: its thread had died - started again", spec.name)

    return revived


def start_all() -> None:
    with session() as db:
        for gone in [j for j in db.scalars(select(Job)) if j.name not in _SPECS]:       # a job that was merged or switched off must not stay in the list as stale
            db.delete(gone)

        for spec in _SPECS.values():
            job = db.get(Job, spec.name)

            if job is None:
                db.add(Job(name=spec.name, interval=spec.interval))
            else:
                job.interval = spec.interval
                job.running = False

    for spec in _SPECS.values():
        spec.wake = threading.Event()
        spec.thread = threading.Thread(target=_loop, args=(spec,), name=f"job-{spec.name}", daemon=True)
        spec.thread.start()

    log.info("jobs started: %s", ", ".join(_SPECS))


def trigger(name: str) -> None:
    """Make a job run on its next tick, now."""
    spec = _SPECS.get(name)

    if spec is None:
        raise KeyError(name)

    if spec.wake is not None:
        spec.wake.set()


def stopping() -> bool:
    """True once shutdown began: a check that fails now failed because of the restart, not the server."""
    return _STOP.is_set()


def stop_all(wait_seconds: float = 40) -> None:
    """Stop the loops and give running jobs time to finish, so a restart does not leave half-done
    checks (the systemd unit uses KillMode=mixed so the ssh children live until the process exits)."""
    _STOP.set()

    for spec in _SPECS.values():
        if spec.wake is not None:
            spec.wake.set()

    deadline = time.time() + wait_seconds

    for spec in _SPECS.values():
        if spec.thread is not None and spec.thread.is_alive():
            spec.thread.join(max(0.0, deadline - time.time()))

    still = [s.name for s in _SPECS.values() if s.thread is not None and s.thread.is_alive()]

    if still:
        log.warning("jobs still running at shutdown: %s", ", ".join(still))
