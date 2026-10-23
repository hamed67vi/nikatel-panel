"""The Bale relay: a channel to the customers that still works while the international internet is cut (services/bale.py)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import bale
from ..ssh import SSHError

router = APIRouter(tags=["bale"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except (SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e)) from None


class BaleIn(BaseModel):
    token: str | None = None                       # empty / missing keeps the saved one
    admins: list[str] | None = Field(None, max_length=10)
    hosts: list[str] | None = Field(None, max_length=20)
    auto_notice: bool | None = None
    cut_after: int | None = None
    restore_after: int | None = None
    texts: dict[str, str] | None = None


class SendIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    admins_only: bool = False


class RemoveIn(BaseModel):
    purge: bool = False


@router.get("/bale")
def bale_get():
    """The relay's settings (the token is masked), the default texts and the hosts the panel guesses the subscription links live on."""
    return {"ok": True, **bale.public()}


@router.put("/bale")
def bale_put(body: BaleIn):
    """Save the settings; when the relay is installed its config is written again at once."""
    return {"ok": True, **_wrap(bale.save, body.model_dump(exclude_none=True))}


@router.get("/bale/status")
def bale_status():
    """What the relay says about itself: its unit, Bale, how many customers started it, the outside look, the last notices (read over SSH)."""
    return {"ok": True, **_wrap(bale.status)}


@router.post("/bale/install")
def bale_install(server_id: int):
    """Install (or update) the relay on an Iran server of the inventory."""
    return {"ok": True, **_wrap(bale.install, server_id)}


@router.post("/bale/send")
def bale_send(body: SendIn):
    """Queue a notice for every customer who started the bot (or for the admins only, as a test)."""
    return {"ok": True, **_wrap(bale.broadcast, body.text, body.admins_only)}


@router.post("/bale/remove")
def bale_remove(body: RemoveIn):
    """Stop and remove the relay; its list of customers stays on the server unless `purge`."""
    return {"ok": True, **_wrap(bale.remove, body.purge)}
