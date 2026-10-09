from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..config import get_settings
from ..services import actions
from ..services import settings as st

router = APIRouter(tags=["actions"], dependencies=[Depends(current_user)])


class RepairIn(BaseModel):
    auto: bool = False
    after_seconds: int = Field(180, ge=60, le=3600)


@router.post("/tunnels/{tunnel_id}/fix")
def start_fix(tunnel_id: int, dry_run: bool = False):
    try:
        aid = actions.fix(tunnel_id, source="manual", dry_run=dry_run)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {"ok": True, "id": aid}


@router.get("/actions/{aid}")
def action_status(aid: int):
    s = actions.status(aid)

    if s is None:
        raise HTTPException(404, "no such action")

    return {"ok": True, **s}


@router.get("/actions")
def action_history(ref: str | None = None, limit: int = 50):
    return {"ok": True, "actions": actions.history(ref, min(limit, 200)), "read_only": get_settings().read_only}


@router.get("/settings/repair")
def repair_get():
    return {"ok": True, **st.get("repair"), "read_only": get_settings().read_only, "max_per_hour": actions.MAX_PER_HOUR}


@router.put("/settings/repair")
def repair_put(body: RepairIn):
    if body.auto and get_settings().read_only:
        raise HTTPException(400, "auto-repair needs NIKATEL_READ_ONLY=false on the panel server first")

    return {"ok": True, **st.put("repair", body.model_dump())}
