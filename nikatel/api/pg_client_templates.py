"""PasarGuard: the client templates (what the subscription answers are built from) of a PasarGuard panel. Routes live under /api/panels/{panel_id}/pg/client-templates."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import panels
from ..services import pg_client_templates as ct
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])
P = "/panels/{panel_id}/pg/client-templates"


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PanelError as e:
        raise HTTPException(404 if e.code == 404 else 409 if e.code == 409 else 400 if e.code in (400, 422) else 502, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


def _cred(panel_id: int) -> dict:
    """The credential of a PasarGuard panel; a Marzban panel has no client templates."""
    try:
        c = panels.cred_for(panel_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    if c.get("type") != "pasarguard":
        raise HTTPException(400, "this is only for PasarGuard panels")

    return c


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=ct.MAX_NAME)
    template_type: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=ct.MAX_CONTENT)
    is_default: bool = False


class UpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=ct.MAX_NAME)
    content: str | None = Field(None, min_length=1, max_length=ct.MAX_CONTENT)
    is_default: bool | None = None


@router.get(P)
def templates_list(panel_id: int):
    """Every client template of the panel (without its content: its size and lines), how many there are per type, and the five types with their names."""
    return {"ok": True, **_wrap(ct.templates_list, _cred(panel_id))}


@router.post(P)
def template_create(panel_id: int, body: CreateIn):
    """Make a client template: a name (up to 64 characters, unique per type), a type (Clash, Xray JSON, sing-box JSON, User-Agent list, gRPC User-Agent list) and its content; the panel checks the content."""
    return {"ok": True, "template": _wrap(ct.template_create, _cred(panel_id), body.name, body.template_type, body.content, body.is_default)}


class BulkDeleteIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=ct.MAX_BULK)
    confirm: bool = False


@router.post(P + "/bulk-delete")
def templates_delete(panel_id: int, body: BulkDeleteIn):
    """Delete several client templates in one call (all or none): never a system template, every type keeps one, each is saved to a file first; the body must say confirm true."""
    return {"ok": True, **_wrap(ct.templates_delete, _cred(panel_id), body.ids, body.confirm)}


@router.get(P + "/backups")
def templates_backups(panel_id: int):
    """The files kept here before a change or a delete of a client template of this panel, newest first."""
    return {"ok": True, "backups": _wrap(ct.template_backups, _cred(panel_id))}


@router.post(P + "/restore/{name}")
def template_restore(panel_id: int, name: str):
    """Put a client template back from one of the kept files: its name and content are written over the template that has its id now, or the template is made again (new id) when it was deleted."""
    return {"ok": True, **_wrap(ct.template_restore, _cred(panel_id), name)}


@router.get(P + "/{template_id}")
def template_get(panel_id: int, template_id: int):
    """One client template with its whole content."""
    return {"ok": True, "template": _wrap(ct.template_get, _cred(panel_id), template_id)}


@router.put(P + "/{template_id}")
def template_update(panel_id: int, template_id: int, body: UpdateIn):
    """Change the name or the content of a client template, or make it the default of its type (switching the default off is refused: make another one the default); only what differs is sent, after a file is saved."""
    return {"ok": True, **_wrap(ct.template_update, _cred(panel_id), template_id, body.name, body.content, body.is_default)}


@router.delete(P + "/{template_id}")
def template_delete(panel_id: int, template_id: int, confirm: bool = False):
    """Delete a client template (needs confirm=true): never a system template and never the last one of its type; the template is saved to a file first."""
    return {"ok": True, **_wrap(ct.template_delete, _cred(panel_id), template_id, confirm)}
