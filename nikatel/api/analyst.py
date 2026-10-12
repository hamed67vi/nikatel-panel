from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..services import analyst, underuse
from ..services import settings as st
from ..ssh import SSHError

router = APIRouter(tags=["analyst"], dependencies=[Depends(current_user)])


@router.get("/analyst")
def analyst_overview(days: int = 7):
    """What the history says: tunnel profiles, the ones at risk, outages that come together, what fixes what, the servers' load and redundancy."""
    return {"ok": True, **analyst.overview(days), "underuse_auto": bool((st.get("underuse") or {}).get("auto", True))}


class AutoIn(BaseModel):
    auto: bool


@router.get("/analyst/underuse")
def underuse_get():
    return {"ok": True, "auto": bool((st.get("underuse") or {}).get("auto", True))}


@router.put("/analyst/underuse")
def underuse_put(body: AutoIn):
    st.put("underuse", {"auto": body.auto})
    return underuse_get()


@router.post("/analyst/server/{server_id}/check")
def underuse_check(server_id: int):
    """Look at one server now (read only; about ten seconds: it downloads a test file while its counters are read)."""
    try:
        r = underuse.probe(server_id)
    except (ValueError, SSHError) as e:
        raise HTTPException(400, str(e))

    analyst._CACHE.clear()
    return {"ok": True, **r}


@router.post("/analyst/server/{server_id}/tune", status_code=202)
def underuse_tune(server_id: int):
    """Fix what the kernel settings can fix (BBR, fq, buffers): measured before and after, put back when it did not help."""
    try:
        return {"ok": True, "action_id": underuse.tune_start(server_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/analyst/server/{server_id}/untune", status_code=202)
def underuse_untune(server_id: int):
    try:
        return {"ok": True, "action_id": underuse.untune_start(server_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))