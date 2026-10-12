from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..config import get_settings
from ..services import balance
from ..services import settings as st
from ..ssh import SSHError

router = APIRouter(tags=["balance"], dependencies=[Depends(current_user)])


class AutoIn(BaseModel):
    auto: bool


class ApplyIn(BaseModel):
    on: bool = True


@router.get("/balance")
def balance_get():
    """The CPU-on-the-cores card: automatic or not, and what was found on each server (cores, RPS, verdict)."""
    s = st.get("balance")
    return {"ok": True, "auto": bool(s.get("auto", True)), "read_only": get_settings().read_only, "servers": s.get("servers", {})}


@router.put("/balance")
def balance_put(body: AutoIn):
    """Switch the Doctor's automatic RPS on or off."""
    st.put("balance", {"auto": body.auto})
    return balance_get()


@router.post("/balance/{server_id}/scan")
def balance_scan(server_id: int):
    """Probe one server now (read only)."""
    try:
        return {"ok": True, **balance.scan_server(server_id)}
    except (ValueError, SSHError) as e:
        raise HTTPException(400, str(e))


@router.post("/balance/{server_id}/apply", status_code=202)
def balance_apply(server_id: int, body: ApplyIn):
    """RPS on (or off = undone) for one server, as an action with its story."""
    try:
        return {"ok": True, "action_id": balance.apply_start(server_id, body.on)}
    except ValueError as e:
        raise HTTPException(400, str(e))