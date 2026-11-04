from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from ..auth import current_user
from ..db import session
from ..services import commander
from ..services.events import emit

router = APIRouter(tags=["commander"], dependencies=[Depends(current_user)])

STATUS = {"off": 403, "ai_not_ready": 503, "busy": 409, "empty": 422, "no_run": 404, "no_proposal": 404, "answered": 409}


class OrderIn(BaseModel):
    text: str = Field(max_length=2000)


class SettingsIn(BaseModel):
    enabled: bool | None = None
    careful: bool | None = None


def _no(e: commander.Refusal) -> NoReturn:
    raise HTTPException(STATUS.get(e.code, 400), {"error": str(e), "code": e.code}) from e


def _on() -> None:
    if not commander.enabled():
        raise HTTPException(403, {"error": "the phone assistant is off: switch it on first", "code": "off"})


def _audit(text: str) -> None:
    with session() as db:
        emit(db, "info", "security", text, "")


@router.get("/commander/state")
def state(request: Request, user: str = Depends(current_user)):
    """The phone assistant at a glance: on or off, the AI in use, the limits and the lists of what it does alone and what needs a tap, the order that is working, the phones that hold a key."""
    return {"ok": True, "who": user, "this_phone": auth.phone_id(request), "phones": auth.phones(), **commander.state_view()}


@router.put("/commander/settings")
def settings(body: SettingsIn, user: str = Depends(current_user)):
    """Switch the phone assistant on or off, and "careful" (every change waits for a tap) on or off."""
    return {"ok": True, **commander.set_conf(body.enabled, body.careful, user)}


@router.post("/commander/logout")
def logout(request: Request, response: Response):
    """Take this phone's key away (the panel's own session, if any, is not touched)."""
    pid = auth.phone_id(request)

    if pid:
        auth.revoke_phone(pid)

    response.delete_cookie(auth.PHONE_COOKIE, path=auth.PHONE_PATH)
    return {"ok": True}


@router.delete("/commander/phones/{pid}")
def phone_forget(pid: str, user: str = Depends(current_user)):
    """Take one phone's key away: the key in that phone is dead from this moment."""
    if not auth.revoke_phone(pid):
        raise HTTPException(404, {"error": "no such phone", "code": "no_phone"})

    _audit(f"{user} took a phone's key away from the phone assistant.")
    return {"ok": True, "phones": auth.phones()}


@router.post("/commander/phones/revoke-all")
def phones_forget_all(user: str = Depends(current_user)):
    """Take every phone's key away (this phone's too)."""
    n = auth.revoke_all_phones()
    _audit(f"{user} took the keys of all {n} phone(s) away from the phone assistant.")
    return {"ok": True, "removed": n}


@router.get("/commander/runs")
def runs(limit: int = Query(30, ge=1, le=100)):
    """The orders given from the phone, newest first: who, the words, how each ended."""
    return {"ok": True, "runs": commander.runs(limit)}


@router.post("/commander/runs", status_code=202)
def order(body: OrderIn, request: Request, user: str = Depends(current_user)):
    """Give the assistant an order in your own words (Persian or English). It answers at once with the order's id; follow it with GET /commander/runs/{id}."""
    _on()

    try:
        return {"ok": True, "id": commander.start(body.text, user, auth.client_ip(request))}
    except commander.Refusal as e:
        _no(e)


@router.get("/commander/runs/{rid}")
def run_get(rid: int, after: int = Query(0, ge=0)):
    """One order with its steps (from step `after` on): what the AI said, every tool it used and what the tool answered, the Confirm cards, the ending."""
    r = commander.get_run(rid, after)

    if r is None:
        raise HTTPException(404, {"error": "no such order", "code": "no_run"})

    return {"ok": True, **r}


@router.post("/commander/runs/{rid}/stop")
def run_stop(rid: int, user: str = Depends(current_user)):
    """The Stop button: the order ends at its next look and a waiting Confirm card is cancelled (a repair the Doctor has already started goes on by itself to its end)."""
    try:
        commander.stop(rid, user)
    except commander.Refusal as e:
        _no(e)

    return {"ok": True}


@router.post("/commander/runs/{rid}/confirm/{pid}", status_code=202)
def run_confirm(rid: int, pid: str, user: str = Depends(current_user)):
    """Tap Confirm on a card: the change the card shows is made - once - and the assistant reports what came of it."""
    _on()

    try:
        commander.confirm(rid, pid, user)
    except commander.Refusal as e:
        _no(e)

    return {"ok": True}


@router.post("/commander/runs/{rid}/cancel/{pid}")
def run_cancel(rid: int, pid: str, user: str = Depends(current_user)):
    """Tap Cancel on a card: nothing is changed and the order ends."""
    try:
        commander.cancel(rid, pid, user)
    except commander.Refusal as e:
        _no(e)

    return {"ok": True}
