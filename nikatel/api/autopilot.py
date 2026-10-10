from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import autopilot
from ..ssh import SSHError

router = APIRouter(tags=["autopilot"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))


@router.get("/autopilot/diagnose/{tunnel_id}")
def diagnose(tunnel_id: int):
    return {"ok": True, **_wrap(autopilot.diagnose, tunnel_id)}


class FixIn(BaseModel):
    tunnel_id: int
    level: str = Field("switch", pattern=r"^(restart|rebuild|switch)$")


@router.post("/autopilot/smart-fix", status_code=202)
def smart_fix(body: FixIn):
    return {"ok": True, "id": _wrap(autopilot.smart_fix_start, body.tunnel_id, body.level)}


class RecommendIn(BaseModel):
    iran_id: int
    foreign_id: int
    kinds: list[str] = Field(default_factory=list)


@router.post("/autopilot/recommend", status_code=202)
def recommend(body: RecommendIn):
    return {"ok": True, "id": _wrap(autopilot.recommend_start, body.iran_id, body.foreign_id, body.kinds)}


class ReplaceIn(BaseModel):
    tunnel_id: int
    kind: str = Field(max_length=16)


@router.post("/autopilot/replace", status_code=202)
def replace(body: ReplaceIn):
    return {"ok": True, "id": _wrap(autopilot.replace_start, body.tunnel_id, body.kind)}


class MoveIn(BaseModel):
    tunnel_id: int
    iran_id: int


@router.post("/autopilot/move", status_code=202)
def move(body: MoveIn):
    return {"ok": True, "id": _wrap(autopilot.move_start, body.tunnel_id, body.iran_id)}


@router.get("/autopilot/ranking/{tunnel_id}")
def ranking(tunnel_id: int):
    return {"ok": True, "ranking": _wrap(autopilot.ranking, tunnel_id)}
