from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import current_user
from ..config import get_settings
from ..services import actions
from ..services import settings as st

router = APIRouter(tags=["actions"], dependencies=[Depends(current_user)])


class RepairIn(BaseModel):
    mode: str | None = Field(None, pattern=r"^(off|fix|move)$")      # the Doctor: off | fix (the ladder) | move (the ladder + another Iran server when one is off or exhausted)
    server_minutes: int = Field(10, ge=2, le=60)                     # an Iran server off this long is replaced (mode move)
    auto: bool | None = None                                         # old clients: True = fix, False = off (ignored when `mode` is given)
    after_seconds: int = Field(0, ge=0, le=3600)                     # no longer used (the Doctor starts at the first moment a tunnel is down); accepted so that an old client keeps working
    level: str = Field("switch", pattern=r"^(restart|rebuild|switch)$")


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
def action_history(ref: str | None = Query(None, max_length=120), limit: int = Query(50, ge=1, le=200), kinds: str = Query("", max_length=200)):
    """The history of actions (repairs, builds, tests, checks), newest first; `ref` filters by tunnel or server, `kinds` (comma list: fix, autopilot, build, ...) by what was done."""
    return {"ok": True, "actions": actions.history(ref, limit, [k for k in kinds.split(",") if k]), "read_only": get_settings().read_only}       # (`limit=-1` was SQLite's "no limit": every action of the table, each read again one by one)


@router.get("/settings/repair")
def repair_get():
    """The Tunnel Doctor's settings: its mode (off, fix, move), how long a tunnel stays down before a repair, how long an Iran server stays off before its tunnels move."""
    from ..services import doctor

    return {"ok": True, **st.get("repair"), "after_seconds": actions.DOWN_GRACE, "mode": doctor.mode(), "can_move": doctor.can_move(), "licensed_move": doctor.license.has("failover"), "read_only": get_settings().read_only, "max_per_hour": actions.MAX_PER_HOUR}


@router.put("/settings/repair")
def repair_put(body: RepairIn):
    """Save the Doctor's settings (a mode other than off needs read-only off; the mode "move" needs the failover license)."""
    from ..services import doctor

    mode = body.mode or ("fix" if body.auto else "off") if body.mode or body.auto is not None else doctor.mode()

    if mode != "off" and get_settings().read_only:
        raise HTTPException(400, "the Doctor needs NIKATEL_READ_ONLY=false on the panel server first")

    if mode == "move" and not doctor.license.has("failover"):
        raise HTTPException(400, "the mode that moves servers needs the failover license (Settings → About → License)")

    return {"ok": True, **st.put("repair", {"mode": mode, "auto": mode != "off", "level": "switch", "after_seconds": actions.DOWN_GRACE, "server_minutes": body.server_minutes}), "can_move": doctor.can_move()}
