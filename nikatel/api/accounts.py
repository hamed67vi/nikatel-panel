from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import accounts
from ..services.panels import PanelError

router = APIRouter(tags=["accounts"], dependencies=[Depends(current_user)])


class MemberIn(BaseModel):
    panel_id: int
    username: str = Field(min_length=1, max_length=64)


class LinkIn(BaseModel):
    members: list[MemberIn] = Field(min_length=2, max_length=20)
    name: str = Field("", max_length=60)


@router.get("/accounts")
def search_accounts(search: str = "", limit: int = accounts.PER_PANEL):
    """The accounts that match a search on any Marzban / PasarGuard panel: one account lists the same person on every panel (same username, or a hand-made link). Each panel is asked at that moment."""
    try:
        return {"ok": True, **accounts.search(search, limit)}
    except (PanelError, ValueError) as e:
        raise HTTPException(502, str(e))


@router.get("/accounts/links")
def list_links():
    """The links made by hand between users with different names."""
    return {"ok": True, "links": accounts.links()}


@router.post("/accounts/links")
def make_link(body: LinkIn):
    """Join users with different names on different panels into one account (each must exist on its panel and be in no other link)."""
    try:
        return {"ok": True, "link": accounts.make_link([m.model_dump() for m in body.members], body.name)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PanelError as e:
        raise HTTPException(502, str(e))


@router.delete("/accounts/links/{link_id}")
def drop_link(link_id: str):
    """Undo a hand-made link (nothing changes on the panels)."""
    try:
        accounts.drop_link(link_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {"ok": True}

class ActIn(BaseModel):
    members: list[MemberIn] = Field(min_length=1, max_length=20)
    action: str
    value: float | int | str | None = None
    confirm: bool = False


@router.post("/accounts/action")
def act(body: ActIn):
    """The same action (enable, disable, reset, revoke, renew, limit, delete) on the user of every panel the account is on. Every panel is tried; the answer lists which did it and which did not. `delete` needs `confirm: true`."""
    if body.action == "delete" and not body.confirm:
        raise HTTPException(400, "deleting a user on every panel needs confirm: true")

    try:
        return {"ok": True, **accounts.act([m.model_dump() for m in body.members], body.action, body.value)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class TargetIn(BaseModel):
    panel_id: int
    inbounds: dict | None = None          # Marzban: {protocol: [tags]}; empty = every inbound
    group_ids: list[int] | None = None    # PasarGuard: which groups; empty = the only one the panel has


class CreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    panels: list[TargetIn] = Field(min_length=1, max_length=20)
    limit_gb: float = Field(0, ge=0, le=100000)
    days: int = Field(0, ge=0, le=3650)
    note: str = Field("", max_length=500)


@router.post("/accounts/create")
def create(body: CreateIn):
    """Make one new user - the same name, limit and end - on every chosen panel. Every panel is tried and the answer lists which did it and which refused (a name already taken there, no group chosen, down); `token` / `path` is the merged subscription link of the ones made."""
    try:
        return {"ok": True, **accounts.create(body.username, [t.model_dump() for t in body.panels], body.limit_gb, body.days, body.note)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class TokenIn(BaseModel):
    members: list[MemberIn] = Field(min_length=1, max_length=12)


@router.post("/accounts/sub-link")
def sub_link(body: TokenIn):
    """The token of one merged subscription link for the given users (one per panel). The public address is /sub/<token>; it is signed, so a changed token is refused."""
    try:
        t = accounts.make_token([m.model_dump() for m in body.members])
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True, "token": t, "path": "/sub/" + t}


@router.post("/accounts/sub-link/reset")
def sub_reset():
    """Make every merged link made so far stop working (the signing secret is replaced)."""
    accounts.reset_secret()
    return {"ok": True}
