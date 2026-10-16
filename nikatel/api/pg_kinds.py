"""PasarGuard: make a whole KIND of account in one step - the inbound, the group and the host together. Routes live under /api/panels/{panel_id}/pg/kinds."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import panels, pg_cores, pg_kinds
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])
P = "/panels/{panel_id}/pg/kinds"


def _wrap(fn, *a, **kw):
    """Run a service function; its refusals become HTTP errors: 409 for a core change that needs `confirm`, 400 for bad input, 502 for what the panel did or did not answer."""
    try:
        return fn(*a, **kw)
    except (pg_cores.NeedsConfirm, pg_cores.Conflict) as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except (PanelError, RuntimeError) as e:
        raise HTTPException(404 if getattr(e, "code", 0) == 404 else 502, str(e)) from None


def _cred(panel_id: int) -> dict:
    try:
        c = panels.cred_for(panel_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None

    if c.get("type") != "pasarguard":
        raise HTTPException(400, "kinds of account are for PasarGuard panels")

    return c


class KindIn(BaseModel):
    kind: str = Field(max_length=40)
    name: str = Field("", max_length=64)
    address: list[str] | str = Field(default="", max_length=2000)
    port: int | str | None = None
    host_port: int | str | None = None
    core_id: int | None = None
    group_mode: str = Field("new", max_length=10)
    group_id: int | None = None
    group_name: str = Field("", max_length=64)
    template: bool = False
    tpl_gb: float | str | None = None
    tpl_days: float | str | None = None
    link_name: str = Field("", max_length=256)
    path: str = Field("", max_length=1024)
    service: str = Field("", max_length=200)
    host_header: str = Field("", max_length=255)
    sni: str = Field("", max_length=255)
    tls_cert: str = Field("", max_length=500)
    tls_key: str = Field("", max_length=500)
    reality_dest: str = Field("", max_length=255)
    reality_names: str = Field("", max_length=1000)
    reality_short_ids: str = Field("", max_length=400)
    reality_spx: str = Field("", max_length=200)
    fingerprint: str = Field("", max_length=20)
    decryption: str = Field("", max_length=1500)
    encryption: str = Field("", max_length=1500)


class MakeIn(KindIn):
    confirm: bool = False
    restart: bool = False


class CustomIn(BaseModel):
    id: str = Field("", max_length=12)
    family: str = Field("", max_length=24)
    family_label: str = Field("", max_length=60)
    family_emoji: str = Field("", max_length=60)
    family_icon: str = Field("", max_length=20)
    short: str = Field(max_length=80)
    title: str = Field("", max_length=200)
    note: str = Field("", max_length=400)
    via: str = Field("builder", max_length=8)
    protocol: str = Field("", max_length=24)
    network: str = Field("", max_length=24)
    security: str = Field("", max_length=24)
    flow: str = Field("", max_length=24)
    header: str = Field("", max_length=8)
    xmode: str = Field("", max_length=16)
    ports: list[int | str] | str = Field(default="", max_length=100)
    text: str = Field("", max_length=30000)


@router.get(P)
def kinds_options(panel_id: int):
    """The kinds of account to choose from, with the cores, the groups, the node addresses and the certificate files and REALITY site this panel already uses; nothing is changed."""
    return {"ok": True, **_wrap(pg_kinds.options, _cred(panel_id))}


@router.post(P + "/plan")
def kinds_plan(panel_id: int, body: KindIn):
    """What making this kind would do step by step - the inbound, the group, the host - and whether each one passes the checks; nothing is written."""
    return {"ok": True, **_wrap(pg_kinds.plan, _cred(panel_id), body.model_dump())}


@router.post(P + "/make")
def kinds_make(panel_id: int, body: MakeIn):
    """Make the kind: the inbound (the core is backed up first), the group, the host, each read back; a failing step takes the earlier ones back; restart true (with confirm) restarts Xray on the nodes."""
    data = body.model_dump()
    confirm, restart = data.pop("confirm"), data.pop("restart")
    return {"ok": True, **_wrap(pg_kinds.make, _cred(panel_id), data, confirm, restart)}


@router.post(P + "/custom")
def kinds_custom_save(panel_id: int, body: CustomIn):
    """Add a protocol of your own to the table of kinds (id empty), or change one (its id): its drawer and emoji, short name, and the builder recipe or the JSON of an inbound. Answers the new table (kinds, families) and the id; nothing on the panel changes."""
    _cred(panel_id)
    return {"ok": True, **_wrap(pg_kinds.save_custom, body.model_dump())}


@router.delete(P + "/custom/{kind_id}")
def kinds_custom_delete(panel_id: int, kind_id: str):
    """Take a protocol of your own out of the table of kinds (accounts already made from it stay in the panel). Answers the new table; nothing on the panel changes."""
    _cred(panel_id)
    return {"ok": True, **_wrap(pg_kinds.delete_custom, kind_id)}