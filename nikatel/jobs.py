"""Background jobs. Every job is registered with a name and an interval, runs in its own thread,
never dies, and writes its status to the `jobs` table so the UI can show what is going on."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

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

    with session() as db:
        job = db.get(Job, name) or Job(name=name, interval=spec.interval)
        db.add(job)
        job.running = True
        job.last_start = int(t0)
        job.runs += 1

    try:
        spec.fn()
        err = ""
    except Exception as e:  # the boundary: a job never kills its loop
        err = str(e)[:300]
        log.exception("job %s failed: %s", name, e)
    finally:
        request_id.reset(token)

    with session() as db:
        job = db.get(Job, name)

        if job is not None:
            job.running = False
            job.last_duration_ms = int((time.time() - t0) * 1000)

            if err:
                job.failures += 1
                job.last_error = err
            else:
                job.last_ok = int(time.time())
                job.last_error = ""


def _loop(spec: _Spec) -> None:
    assert spec.wake is not None
    time.sleep(3)   # let the server come up first

    while not _STOP.is_set():
        with session() as db:
            job = db.get(Job, spec.name)
            enabled = job.enabled if job is not None else True

        if enabled:
            run_once(spec.name)

        spec.wake.wait(spec.interval)
        spec.wake.clear()


def start_all() -> None:
    with session() as db:
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


def stop_all() -> None:
    _STOP.set()

    for spec in _SPECS.values():
        if spec.wake is not None:
            spec.wake.set()
