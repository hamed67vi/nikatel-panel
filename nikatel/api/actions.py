from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..config import get_settings
from ..services import actions
from ..services import settings as st

router = APIRouter(tags=["actions"], dependencies=[Depends(current_user)])


class RepairIn(BaseModel):
    auto: bool = False
    after_seconds: int = Field(60, ge=30, le=3600)
    level: str = Field("restart", pattern=r"^(restart|rebuild|switch)$")


@router.post("/tunnels/{tunnel_id}/fix")
def start_fix(tunnel_id: int, dry_run: bool = False):
    """Start the plain repair of a tunnel (restart what is not working, one unit at a time, checked after each), as an action; `dry_run` only shows the plan."""
    try:
        aid = actions.fix(tunnel_id, source="manual", dry_run=dry_run)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {"ok": True, "id": aid}


@router.get("/actions/{aid}")
def action_status(aid: int):
    """One action: its story so far, whether it is done and whether it succeeded."""
    s = actions.status(aid)

    if s is None:
        raise HTTPException(404, "no such action")

    return {"ok": True, **s}


@router.get("/actions")
def action_history(ref: str | None = None, limit: int = 50, kinds: str = ""):
    """The history of actions (repairs, builds, tests, checks), newest first; `ref` filters by tunnel or server, `kinds` (comma list: fix, autopilot, build, ...) by what was done."""
    return {"ok": True, "actions": actions.history(ref, min(limit, 200), [k for k in kinds.split(",") if k]), "read_only": get_settings().read_only}


@router.get("/settings/repair")
def repair_get():
    """The auto-repair settings: on or off, how long a tunnel stays down first, how far the repair may go."""
    return {"ok": True, **st.get("repair"), "read_only": get_settings().read_only, "max_per_hour": actions.MAX_PER_HOUR}


@router.put("/settings/repair")
def repair_put(body: RepairIn):
    """Save the auto-repair settings (needs read-only off to turn it on)."""
    if body.auto and get_settings().read_only:
        raise HTTPException(400, "auto-repair needs NIKATEL_READ_ONLY=false on the panel server first")

    return {"ok": True, **st.put("repair", body.model_dump())}
