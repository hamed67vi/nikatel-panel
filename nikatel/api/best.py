from fastapi import APIRouter, Depends, Query

from ..auth import current_user
from ..services import besttoday

router = APIRouter(tags=["tunnels"], dependencies=[Depends(current_user)])


@router.get("/besttoday")
def best_today(hours: int = Query(24, ge=1, le=168)):
    """The tunnels ranked by how they behaved in the last hours (availability, latency, jitter, loss, breaks), the best first; `best_for` marks the best one to each foreign server."""
    return {"ok": True, **besttoday.rank(hours)}
