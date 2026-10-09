from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..services import engines

router = APIRouter(tags=["engines"], dependencies=[Depends(current_user)])


@router.get("/engines")
def last():
    return {"ok": True, **engines.last()}


@router.post("/engines/check")
def check():
    return {"ok": True, **engines.check()}


@router.post("/engines/{engine}/update")
def update(engine: str):
    try:
        job = engines.update(engine)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    return {"ok": not job.get("error"), **job}
