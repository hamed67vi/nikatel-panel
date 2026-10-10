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


@router.get("/autopilot/diagnose-config/{config_id}")
def diagnose_config(config_id: int):
    return {"ok": True, **_wrap(autopilot.diagnose_config, config_id)}


@router.post("/autopilot/smart-fix", status_code=202)
def smart_fix(body: FixIn):
    return {"ok": True, "id": _wrap(autopilot.smart_fix_start, body.tunnel_id, body.level)}


class LevelIn(BaseModel):
    level: str = Field("rebuild", pattern=r"^(restart|rebuild|switch)$")


@router.post("/autopilot/fix-all", status_code=202)
def fix_all(body: LevelIn):
    return {"ok": True, "id": _wrap(autopilot.fix_all_start, body.level)}


class ExplainIn(BaseModel):
    tunnel_id: int | None = None


@router.post("/autopilot/explain")
def explain(body: ExplainIn):
    return {"ok": True, **_wrap(autopilot.explain, body.tunnel_id)}


@router.get("/netwatch")
def netwatch_report():
    """Disturbances of the network itself: an Iran server with weak connectivity, a server most of whose tunnels lose packets."""
    from ..services import netwatch

    return {"ok": True, **netwatch.report()}


@router.get("/autopilot/outage")
def outage():
    """Is there a mass disruption (several servers offline / many tunnels down): the repair holds back from deleting or rebuilding."""
    return {"ok": True, **autopilot.outage_state(), "repairing": autopilot.repairing_names(), "working": autopilot.working()}


@router.get("/autopilot/situation")
def situation():
    return {"ok": True, **_wrap(autopilot.situation)}


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
