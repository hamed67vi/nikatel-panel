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
    """Why a tunnel is down or lossy: both servers, the route, GRE, the control port, the data path (read only, about 20 s)."""
    return {"ok": True, **_wrap(autopilot.diagnose, tunnel_id)}


class FixIn(BaseModel):
    tunnel_id: int
    level: str = Field("switch", pattern=r"^(restart|rebuild|switch)$")


@router.get("/autopilot/diagnose-config/{config_id}")
def diagnose_config(config_id: int):
    """Why a config fails the probe: the address, the port from Iran, the SNI (read only)."""
    return {"ok": True, **_wrap(autopilot.diagnose_config, config_id)}


@router.post("/autopilot/smart-fix", status_code=202)
def smart_fix(body: FixIn):
    """Diagnose a tunnel and climb the repair ladder up to the level (restart, rebuild, new link, another type, move), as an action."""
    return {"ok": True, "id": _wrap(autopilot.smart_fix_start, body.tunnel_id, body.level)}


class LevelIn(BaseModel):
    level: str = Field("rebuild", pattern=r"^(restart|rebuild|switch)$")


@router.post("/autopilot/fix-all", status_code=202)
def fix_all(body: LevelIn):
    """The smart repair on every tunnel that is down or lossy, one after the other, as an action."""
    return {"ok": True, "id": _wrap(autopilot.fix_all_start, body.level)}


class ExplainIn(BaseModel):
    tunnel_id: int | None = None


@router.post("/autopilot/explain")
def explain(body: ExplainIn):
    """The AI reads what the panel knows about a tunnel (or everything) and says what to do first."""
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
    """The whole situation in one dict: servers, tunnels, configs, disturbances, repairs running."""
    return {"ok": True, **_wrap(autopilot.situation)}


class RecommendIn(BaseModel):
    iran_id: int
    foreign_id: int
    kinds: list[str] = Field(default_factory=list)


@router.post("/autopilot/recommend", status_code=202)
def recommend(body: RecommendIn):
    """Test the chosen tunnel kinds between two servers beside what runs and rank them, as an action."""
    return {"ok": True, "id": _wrap(autopilot.recommend_start, body.iran_id, body.foreign_id, body.kinds)}


class ReplaceIn(BaseModel):
    tunnel_id: int
    kind: str = Field(max_length=16)


@router.post("/autopilot/replace", status_code=202)
def replace(body: ReplaceIn):
    """Replace a tunnel with one of another kind (same name and ports), as an action; the old one comes back when the new fails."""
    return {"ok": True, "id": _wrap(autopilot.replace_start, body.tunnel_id, body.kind)}


class MoveIn(BaseModel):
    tunnel_id: int
    iran_id: int


@router.post("/autopilot/move", status_code=202)
def move(body: MoveIn):
    """Move a tunnel to another Iran server, as an action: the new tunnel is built first, the DNS records that serve only it follow (every account), the old tunnel goes after the TTL."""
    from ..services import doctor

    return {"ok": True, "id": _wrap(doctor.move_now, body.tunnel_id, body.iran_id)}


@router.get("/autopilot/ranking/{tunnel_id}")
def ranking(tunnel_id: int):
    """The Iran servers ranked for this tunnel's foreign server (where a move would go)."""
    return {"ok": True, "ranking": _wrap(autopilot.ranking, tunnel_id)}
