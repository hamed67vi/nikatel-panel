"""The field test: what the Iran test kit saw from a real network (services/field.py). The kit sends its result with an API token of scope `field` (that scope may call only the first route); the rest is read by the owner."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import field, fieldkit, fieldtesters
from ..services.panels import PanelError

router = APIRouter(tags=["field"], dependencies=[Depends(current_user)])


@router.post("/field/ingest")
def ingest(body: dict, user: str = Depends(current_user)):
    """The Iran test kit sends the result of one run (JSON); the panel keeps a compact copy, judges it and answers with what it found (a token of scope `field` may call only this)."""
    if len(json.dumps(body, separators=(",", ":"))) > field.MAX_BYTES:
        raise HTTPException(413, "the result is too large")

    try:
        return {"ok": True, **field.ingest(body, user)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except field.TooMany as e:
        raise HTTPException(429, str(e))


@router.get("/field")
def overview():
    """The newest field test of every network (operator, connection, city) with what the Doctor found in it, and the matrix of every config against every network."""
    return {"ok": True, **field.summary()}


@router.get("/field/runs")
def list_runs(limit: int = Query(30, ge=1, le=200)):
    """The runs the kit sent, newest first (who sent, the network, how many findings)."""
    return {"ok": True, "runs": field.runs(limit)}


@router.get("/field/runs/{run_id}")
def one_run(run_id: int):
    """One run in full: the compact copy of everything the kit measured and what was found in it."""
    r = field.run_by_id(run_id)

    if r is None:
        raise HTTPException(404, "no such run")

    return {"ok": True, **r}

@router.get("/field/manifest")
def kit_manifest():
    """The live list of what the Iran test kit tests - its configs as Xray clients, the panel's names and addresses, the newest kit number and the state of its test user - made from the panel at this moment, so the kit never goes stale (a token of scope `field` may call this too)."""
    try:
        return {"ok": True, **fieldkit.manifest()}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PanelError as e:
        raise HTTPException(502, str(e))


class TesterIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    city: str = Field(min_length=1, max_length=40)
    sim: str = Field("", max_length=12)


@router.get("/field/testers")
def testers_list():
    """The people the Iran test kit is sent to (name, city, SIM, what each has sent) and, when the panel cannot make a kit yet, the sentence that says what is missing. Only the owner's sign-in or an `admin` token."""
    return {"ok": True, "testers": fieldtesters.view(), "problem": fieldtesters.template_problem()}


@router.post("/field/testers", status_code=201)
def testers_add(body: TesterIn):
    """Add a person (English name and city, and their SIM or Home - empty = the kit asks): the panel makes their kit (a zip with a token of their own) at once."""
    try:
        t = fieldtesters.add(body.name, body.city, body.sim)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PanelError as e:
        raise HTTPException(502, str(e))

    return {"ok": True, "id": t["id"]}


@router.get("/field/testers/{tid}/kit")
def tester_kit(tid: str):
    """The kit of one tester as a zip (it holds their token: never to be shared)."""
    p = fieldtesters.zip_file(tid)

    if p is None:
        raise HTTPException(404, "no kit for this tester")

    return FileResponse(p, media_type="application/zip", filename=f"nikatel-iran-test-{tid}.zip")


@router.post("/field/testers/{tid}/rebuild")
def tester_rebuild(tid: str):
    """A new kit for a tester with the list of today and a new token: the old kit stops working."""
    try:
        fieldtesters.rebuild(tid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PanelError as e:
        raise HTTPException(502, str(e))

    return {"ok": True}


@router.delete("/field/testers/{tid}")
def tester_remove(tid: str):
    """Remove a tester: their token is revoked and their kit deleted (what they sent stays)."""
    try:
        fieldtesters.remove(tid)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True}
