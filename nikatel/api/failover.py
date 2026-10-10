from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..config import get_settings
from ..services import arvan, failover
from ..services import settings as st

router = APIRouter(tags=["failover"], dependencies=[Depends(current_user)])


class ArvanIn(BaseModel):
    api_key: str = ""
    mode: str = "off"
    confirm_minutes: int = 5
    domains: list[str] = []


@router.get("/settings/arvan")
def arvan_get():
    return {"ok": True, **st.public("arvan")}


@router.put("/settings/arvan")
def arvan_put(body: ArvanIn):
    if body.mode not in ("off", "approve", "auto"):
        raise HTTPException(400, "mode must be off, approve or auto")

    if not 2 <= body.confirm_minutes <= 120:
        raise HTTPException(400, "minutes: between 2 and 120")

    doms = [d.strip().lower() for d in body.domains if d.strip()][:20]
    st.put("arvan", {"api_key": body.api_key.strip(), "mode": body.mode, "confirm_minutes": body.confirm_minutes, "domains": doms})

    if body.api_key.strip():                 # a new key: find out at once whether it works (read only)
        st.put("arvan", {"status": {}})

    if st.get("arvan").get("api_key") and (body.api_key.strip() or not st.get("arvan").get("status")):
        try:
            arvan.test()
        except RuntimeError as e:
            st.put("arvan", {"status": {"ok": False, "error": str(e)}})

    return arvan_get()


@router.post("/settings/arvan/test")
def arvan_test():
    """Read-only: lists the domains and counts the records the key can see."""
    try:
        return arvan.test()
    except RuntimeError as e:
        st.put("arvan", {"status": {"ok": False, "error": str(e)}})
        raise HTTPException(502, str(e))


@router.get("/failover")
def fo_state():
    return {"ok": True, **failover.state()}


@router.get("/failover/plan/{server_id}")
def fo_plan(server_id: int):
    """What a failover of this Iran server would do right now (nothing is changed)."""
    try:
        return {"ok": True, **failover.plan_for(server_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


def _writable():
    if get_settings().read_only:
        raise HTTPException(403, "the panel is read-only")


@router.post("/failover/{plan_id}/approve")
def fo_approve(plan_id: str):
    _writable()

    try:
        return {"ok": True, "action_id": failover.approve(plan_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/failover/{plan_id}/dismiss")
def fo_dismiss(plan_id: str):
    try:
        failover.dismiss(plan_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True}


@router.post("/failover/{plan_id}/undo")
def fo_undo(plan_id: str):
    _writable()

    try:
        return {"ok": True, "changed": failover.undo(plan_id)}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.post("/failover/dns/{log_id}/restore")
def fo_restore(log_id: str):
    """One re-pointed DNS record back to the address it had before the failover."""
    _writable()

    try:
        return {"ok": True, **failover.restore(log_id)}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
