from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import checkall

router = APIRouter(tags=["servers"], dependencies=[Depends(current_user)])


class CheckAllIn(BaseModel):
    role: str = Field("", pattern=r"^(iran|foreign)?$")


@router.post("/checkall")
def check_all_start(body: CheckAllIn):
    """Check every enabled server (of one role) in one go: answers, GRE, filtering in Iran. Runs on the panel server: a page reload does not stop it."""
    try:
        return {"ok": True, "id": checkall.start(body.role)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/checkall")
def check_all_status():
    """Is a check of all servers running and how far is it (done / total, the last line)."""
    return {"ok": True, **checkall.status()}


@router.post("/checkall/stop")
def check_all_stop():
    """Stop the check: the servers being checked now finish, no new one starts."""
    return {"ok": True, "stopping": checkall.stop()}
