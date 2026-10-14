"""PasarGuard: the admin roles of a PasarGuard panel. Routes live under /api/panels/{panel_id}/pg/roles."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import panels, pg_roles
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])
P = "/panels/{panel_id}/pg/roles"


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PanelError as e:
        raise HTTPException(404 if e.code == 404 else 409 if e.code == 409 else 502, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


def _cred(panel_id: int) -> dict:
    """The credential of a PasarGuard panel; a Marzban panel has no roles."""
    try:
        c = panels.cred_for(panel_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    if c.get("type") != "pasarguard":
        raise HTTPException(400, "this is only for PasarGuard panels")

    return c


class RoleIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=pg_roles.MAX_NAME)
    permissions: dict | None = None
    limits: dict | None = None
    features: dict | None = None
    access: dict | None = None
    hwid: dict | None = None
    disabled_when_limited: bool | None = None
    disconnect_users_when_limited: bool | None = None
    disconnect_users_when_disabled: bool | None = None


@router.get(P)
def roles_list(panel_id: int):
    """Every admin role of the panel with its permissions (no / yes / own / all), limits (GB and days), features, access, device rules and the admins that have it; also the permission names, groups and templates."""
    return {"ok": True, **_wrap(pg_roles.roles_list, _cred(panel_id))}


@router.post(P)
def role_create(panel_id: int, body: RoleIn):
    """Make a role: a name (up to 64 characters, unique) and the sections you give; the panel fills the others with its defaults."""
    return {"ok": True, "role": _wrap(pg_roles.role_create, _cred(panel_id), body.model_dump(exclude_unset=True))}


@router.get(P + "/backups")
def roles_backups(panel_id: int):
    """The files kept here before a change or a delete of a role of this panel, newest first (names, numbers and permissions: no secret)."""
    return {"ok": True, "backups": _wrap(pg_roles.role_backups, _cred(panel_id))}


@router.post(P + "/restore/{name}")
def role_restore(panel_id: int, name: str):
    """Put a role back from one of the kept files: written over the role that has its id now, or made again (new id) when it was deleted."""
    return {"ok": True, **_wrap(pg_roles.role_restore, _cred(panel_id), name)}


@router.put(P + "/{role_id}")
def role_update(panel_id: int, role_id: int, body: RoleIn):
    """Change a role: only the sections that differ from what it has now are sent, after the role is saved to a file; the owner role cannot be changed."""
    return {"ok": True, **_wrap(pg_roles.role_update, _cred(panel_id), role_id, body.model_dump(exclude_unset=True))}


@router.delete(P + "/{role_id}")
def role_delete(panel_id: int, role_id: int, confirm: bool = False):
    """Delete a role (needs confirm=true): refused for the owner role, the built-in roles and a role that admins still have (they are named); the role is saved to a file first."""
    return {"ok": True, **_wrap(pg_roles.role_delete, _cred(panel_id), role_id, confirm)}
