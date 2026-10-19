from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..config import get_settings
from ..services import doctor, failover, knowledge, linkprobe
from ..ssh import SSHError

router = APIRouter(tags=["doctor"], dependencies=[Depends(current_user)])


def _writable() -> None:
    if get_settings().read_only:
        raise HTTPException(403, "the panel is read-only")


@router.get("/doctor")
def doctor_state():
    """The Tunnel Doctor: its mode, the ledger of moves (with what can be returned), the suggestions, which servers are back since when, the rails."""
    return {"ok": True, **doctor.state()}


@router.get("/doctor/plan/{server_id}")
def doctor_plan(server_id: int):
    """What the Doctor would do if this Iran server went off right now: the tunnels, the target, the DNS records of every account (nothing is changed)."""
    try:
        return {"ok": True, **doctor.plan_preview(server_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/doctor/moves/{move_id}/return", status_code=202)
def doctor_return(move_id: str):
    """Bring the tunnels of this move back to the server they left, now (the DNS follows), as an action."""
    _writable()

    try:
        return {"ok": True, "id": doctor.return_now(move_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/doctor/moves/{move_id}/undo-dns")
def doctor_undo_dns(move_id: str):
    """Point every DNS record of this move back at the old address (the tunnels stay where they are)."""
    _writable()

    try:
        return {"ok": True, "changed": failover.undo(move_id)}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.post("/doctor/suggestions/{sid}/apply", status_code=202)
def doctor_apply(sid: str):
    """Do what the Doctor suggested: move the tunnel to the Iran server it named, or (a very slow tunnel) build it again as the type it named, as an action."""
    _writable()

    try:
        return {"ok": True, "id": doctor.apply_suggestion(sid)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/doctor/suggestions/{sid}/dismiss")
def doctor_dismiss(sid: str):
    """Drop a suggestion."""
    doctor.dismiss_suggestion(sid)
    return {"ok": True}

class KnowledgeIn(BaseModel):
    on: bool | None = None
    from_hour: int | None = Field(None, ge=0, le=23)
    to_hour: int | None = Field(None, ge=0, le=23)


class SurveyIn(BaseModel):
    iran_id: int
    foreign_id: int


@router.get("/doctor/knowledge")
def doctor_knowledge():
    """What the Doctor knows about every pair of servers: the tunnel types that work between them (best first), those that did not, how old the knowledge is, the night survey (on, its hours) and the survey that runs now."""
    return {"ok": True, **knowledge.state()}


@router.put("/doctor/knowledge")
def doctor_knowledge_set(body: KnowledgeIn):
    """Switch the night survey on or off and set the local hours it may run in (equal hours = any time)."""
    try:
        return {"ok": True, **knowledge.configure(body.on, body.from_hour, body.to_hour)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/doctor/knowledge/survey", status_code=202)
def doctor_survey(body: SurveyIn):
    """Test every tunnel type between an Iran server and a foreign server now, one after the other (about two minutes each; temporary tunnels, removed again) and keep what they gave."""
    _writable()

    try:
        return {"ok": True, "id": knowledge.survey_start(body.iran_id, body.foreign_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/doctor/knowledge/links")
def doctor_links(body: SurveyIn):
    """Ask which of the nine links (GRE, GRE / IPIP inside UDP, IPIP, WireGuard, IPsec ...) pass between an Iran server and a foreign server RIGHT NOW: a temporary link of each, all at once, a few seconds,
    removed again. The answers are kept in the Doctor's memory of the pair (they are in GET /doctor/knowledge, per pair, under `links`)."""
    _writable()

    try:
        return {"ok": True, "links": linkprobe.probe(body.iran_id, body.foreign_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, SSHError) as e:
        raise HTTPException(502, str(e)[:200])


@router.post("/doctor/knowledge/stop")
def doctor_survey_stop():
    """Stop the survey that runs; the tunnel it is testing is removed."""
    try:
        knowledge.stop()
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {"ok": True}
