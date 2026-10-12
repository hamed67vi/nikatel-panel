from fastapi import APIRouter, Depends

from ..auth import current_user
from ..services import analyst

router = APIRouter(tags=["analyst"], dependencies=[Depends(current_user)])


@router.get("/analyst")
def analyst_overview(days: int = 7):
    """What the history says: tunnel profiles, the ones at risk, outages that come together, what fixes what, the servers' load and redundancy."""
    return {"ok": True, **analyst.overview(days)}
