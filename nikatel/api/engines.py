from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import engines, settings

router = APIRouter(tags=["engines"], dependencies=[Depends(current_user)])


@router.get("/engines")
def last():
    """The last engine check: the version of each tunnel core on every server against the newest release."""
    return {"ok": True, **engines.last()}


@router.post("/engines/check", status_code=202)
def check():
    """Start the version check of every server in the background; follow it with GET /engines (`checking`, then `checked_at`)."""
    return {"ok": True, "started": engines.check_start(), **engines.last()}


class AutoIn(BaseModel):
    enabled: bool
    hour: int | None = Field(None, ge=0, le=23)


@router.post("/engines/auto")
def auto(body: AutoIn):
    """The switch of the Doctor that keeps the tunnel cores up to date by itself (once a night at `hour`, panel clock = Tehran by default): one server at a time, the quietest first, the old build put back when a
    new one does not hold. Off until it is switched on."""
    changes = {"auto": body.enabled}

    if body.hour is not None:
        changes["hour"] = body.hour

    settings.put("engines", changes)
    return {"ok": True, **engines.last()}


@router.post("/engines/{engine}/update")
def update(engine: str, missing: bool = False):
    """Update one tunnel core on every outdated server (verified download, units restarted); `missing=true` instead puts it on every server that has not got it (nothing is restarted); refused while another update runs."""
    try:
        job = engines.update(engine, only_missing=missing)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    return {"ok": not job.get("error"), **job}

@router.post("/engines/wireguard/install", status_code=202)
def install_wireguard(server_id: int | None = None):
    """The owner's button: install wireguard-tools with apt on every server that lacks it (`server_id`: on that one only), four at a time, in the background; follow it with GET /engines (`wg_job`, then `wireguard`).
    Nothing but this button (and a build the owner starts that asks for WireGuard) ever puts a package on a server."""
    try:
        started = engines.install_wireguard_start([server_id] if server_id else None)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True, "started": started, **engines.last()}