from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .. import jobs
from ..auth import current_user
from ..db import session
from ..models import Job

router = APIRouter(tags=["jobs"], dependencies=[Depends(current_user)])


@router.get("/jobs")
def list_jobs():
    with session() as db:
        return {"ok": True, "jobs": [{"name": j.name, "interval": j.interval, "enabled": j.enabled, "running": j.running,
                                     "last_start": j.last_start, "last_ok": j.last_ok, "last_duration_ms": j.last_duration_ms,
                                     "last_error": j.last_error, "runs": j.runs, "failures": j.failures, "last_failure": j.last_failure, "last_failure_at": j.last_failure_at}
                                    for j in db.scalars(select(Job).order_by(Job.name))]}


@router.post("/jobs/{name}/run")
def run_now(name: str):
    try:
        jobs.trigger(name)
    except KeyError:
        raise HTTPException(404, "no such job")

    return {"ok": True}


@router.post("/jobs/{name}/enabled")
def set_enabled(name: str, enabled: bool):
    with session() as db:
        j = db.get(Job, name)

        if j is None:
            raise HTTPException(404, "no such job")

        j.enabled = enabled
        return {"ok": True, "enabled": enabled}
