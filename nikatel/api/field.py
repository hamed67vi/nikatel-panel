"""The field test: what the Iran test kit saw from a real network (services/field.py). The kit sends its result with an API token of scope `field` (that scope may call only the first route); the rest is read by the owner."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import current_user
from ..services import field, fieldkit
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
