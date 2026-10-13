from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..config import get_settings
from ..services import doctor, failover

router = APIRouter(tags=["doctor"], dependencies=[Depends(current_user)])


def _writable() -> None:
    if get_settings().read_only:
        raise HTTPException(403, "the panel is read-only")


@router.get("/doctor")
def doctor_state():
    """The Tunnel Doctor: its mode, the ledger of moves (with what can be returned), the suggestions, which servers are back since when, the rails."""
    return {"ok": True, **doctor.state()}


@router.get("/doctor/plan/{server_id}")
def doctor_plan(server_id: int):
    """What the Doctor would do if this Iran server went off right now: the tunnels, the target, the DNS records of every account (nothing is changed)."""
    try:
        return {"ok": True, **doctor.plan_preview(server_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/doctor/moves/{move_id}/return", status_code=202)
def doctor_return(move_id: str):
    """Bring the tunnels of this move back to the server they left, now (the DNS follows), as an action."""
    _writable()

    try:
        return {"ok": True, "id": doctor.return_now(move_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/doctor/moves/{move_id}/undo-dns")
def doctor_undo_dns(move_id: str):
    """Point every DNS record of this move back at the old address (the tunnels stay where they are)."""
    _writable()

    try:
        return {"ok": True, "changed": failover.undo(move_id)}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.post("/doctor/suggestions/{sid}/apply", status_code=202)
def doctor_apply(sid: str):
    """Do what the Doctor suggested: move the tunnel to the Iran server it named, as an action."""
    _writable()

    try:
        return {"ok": True, "id": doctor.apply_suggestion(sid)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/doctor/suggestions/{sid}/dismiss")
def doctor_dismiss(sid: str):
    """Drop a suggestion."""
    doctor.dismiss_suggestion(sid)
    return {"ok": True}
