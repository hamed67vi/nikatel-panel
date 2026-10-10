from fastapi import APIRouter, Depends

from ..auth import current_user
from ..services import radar

router = APIRouter(tags=["radar"], dependencies=[Depends(current_user)])


@router.get("/radar")
def get_radar(refresh: bool = False):
    return {"ok": True, **radar.fetch(force=refresh)}