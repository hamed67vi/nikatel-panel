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
