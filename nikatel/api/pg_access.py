"""PasarGuard: groups and user templates of a PasarGuard panel. Routes live under /api/panels/{panel_id}/pg/groups, /pg/templates and /pg/access."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import panels, pg_access
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])

Tag = Annotated[str, Field(min_length=1, max_length=200)]
Method = Literal["", "aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "xchacha20-poly1305"]
# the fields of a template where null means something: unlimited / none / clear (every other null is dropped, it would only mean 'keep')
_NULL_MEANS = ("data_limit_gb", "expire_days", "on_hold_days", "hwid_limit", "username_prefix", "username_suffix")


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (PanelError, RuntimeError) as e:
        raise HTTPException(502, str(e))


def _cred(panel_id: int) -> dict:
    return _wrap(panels.cred_for, panel_id)


# ---- groups
class GroupIn(BaseModel):
    name: str = Field(min_length=3, max_length=64)
    inbound_tags: list[Tag] = Field(min_length=1, max_length=300)
    is_disabled: bool = False


class GroupPatch(BaseModel):
    name: str | None = Field(None, min_length=3, max_length=64)
    inbound_tags: list[Tag] | None = Field(None, min_length=1, max_length=300)
    is_disabled: bool | None = None


class BulkIn(BaseModel):
    action: Literal["enable", "disable", "delete"]
    ids: list[int] = Field(min_length=1, max_length=200)
    force: bool = False


class MembersIn(BaseModel):
    action: Literal["add", "remove"]
    group_ids: list[int] = Field(min_length=1, max_length=50)
    has_group_ids: list[int] = Field(default_factory=list, max_length=50)
    has_no_group: bool = False
    user_ids: list[int] = Field(default_factory=list, max_length=5000)
    admin_ids: list[int] = Field(default_factory=list, max_length=100)
    all_users: bool = False
    dry_run: bool = True


@router.get("/panels/{panel_id}/pg/groups")
def groups_list(panel_id: int):
    """The groups of a PasarGuard panel (name, inbound tags, on / off, how many users hold each and which templates use it) and the inbounds a group can hold."""
    return {"ok": True, **_wrap(pg_access.groups, _cred(panel_id))}


@router.get("/panels/{panel_id}/pg/groups/{group_id}")
def group_get(panel_id: int, group_id: int):
    """One group of the panel in full."""
    return {"ok": True, "group": _wrap(pg_access.group_get, _cred(panel_id), group_id)}


@router.post("/panels/{panel_id}/pg/groups")
def group_create(panel_id: int, body: GroupIn):
    """Create a group from a name (3-64 characters) and the inbound tags it holds; a name that is already taken is refused."""
    return {"ok": True, "group": _wrap(pg_access.group_create, _cred(panel_id), body.name, body.inbound_tags, body.is_disabled)}


@router.put("/panels/{panel_id}/pg/groups/{group_id}")
def group_update(panel_id: int, group_id: int, body: GroupPatch):
    """Change a group's name, inbound tags or switch; what is not sent stays, and the panel always gets all three; the groups and templates are saved to a file first."""
    return {"ok": True, **_wrap(pg_access.group_update, _cred(panel_id), group_id, body.model_dump(exclude_none=True))}


@router.delete("/panels/{panel_id}/pg/groups/{group_id}")
def group_delete(panel_id: int, group_id: int, force: bool = False):
    """Delete a group; refused while users or templates still hold it unless force=true (they lose it); the groups and templates are saved to a file first."""
    return {"ok": True, **_wrap(pg_access.group_delete, _cred(panel_id), group_id, force)}


@router.post("/panels/{panel_id}/pg/groups/bulk")
def groups_bulk(panel_id: int, body: BulkIn):
    """Enable, disable or delete several groups at once; deleting groups that are in use needs force=true; the groups and templates are saved to a file first."""
    return {"ok": True, **_wrap(pg_access.group_bulk, _cred(panel_id), body.action, body.ids, body.force)}


@router.post("/panels/{panel_id}/pg/groups/members")
def groups_members(panel_id: int, body: MembersIn):
    """Add groups to users or take them away (all users, the users who hold a group or none, some users, an admin's users); dry_run=true (the default) only counts who would change."""
    return {"ok": True, **_wrap(pg_access.group_members, _cred(panel_id), body.action, body.group_ids, body.has_group_ids, body.has_no_group, body.user_ids, body.admin_ids, body.all_users, body.dry_run)}


# ---- user templates
class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    group_ids: list[int] = Field(min_length=1, max_length=200)
    data_limit_gb: float | None = Field(None, ge=0, le=1_000_000)
    expire_days: float | None = Field(None, ge=0, le=24855)
    username_prefix: str | None = Field(None, max_length=20)
    username_suffix: str | None = Field(None, max_length=20)
    status: Literal["active", "on_hold"] = "active"
    data_limit_reset_strategy: Literal["no_reset", "day", "week", "month", "year"] = "no_reset"
    reset_usages: bool = False
    on_hold_days: float | None = Field(None, ge=0, le=24855)
    hwid_limit: int | None = Field(None, ge=0, le=1_000_000)
    method: Method = ""
    is_disabled: bool = False


class TemplatePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    group_ids: list[int] | None = Field(None, min_length=1, max_length=200)
    data_limit_gb: float | None = Field(None, ge=0, le=1_000_000)
    expire_days: float | None = Field(None, ge=0, le=24855)
    username_prefix: str | None = Field(None, max_length=20)
    username_suffix: str | None = Field(None, max_length=20)
    status: Literal["active", "on_hold"] | None = None
    data_limit_reset_strategy: Literal["no_reset", "day", "week", "month", "year"] | None = None
    reset_usages: bool | None = None
    on_hold_days: float | None = Field(None, ge=0, le=24855)
    hwid_limit: int | None = Field(None, ge=0, le=1_000_000)
    method: Method | None = None
    is_disabled: bool | None = None


class TemplateUserIn(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    note: str = Field("", max_length=500)


@router.get("/panels/{panel_id}/pg/templates")
def templates_list(panel_id: int):
    """The user templates of a PasarGuard panel (limits in GB and days as well as bytes and seconds) and the groups a template can use."""
    return {"ok": True, **_wrap(pg_access.templates, _cred(panel_id))}


@router.get("/panels/{panel_id}/pg/templates/{template_id}")
def template_get(panel_id: int, template_id: int):
    """One user template of the panel in full."""
    return {"ok": True, "template": _wrap(pg_access.template_get, _cred(panel_id), template_id)}


@router.post("/panels/{panel_id}/pg/templates")
def template_create(panel_id: int, body: TemplateIn):
    """Create a user template: name, groups, data limit in GB (empty = unlimited), duration in days (empty = none), username prefix and suffix, status, reset period, shadowsocks method."""
    return {"ok": True, **_wrap(pg_access.template_create, _cred(panel_id), body.model_dump())}


@router.put("/panels/{panel_id}/pg/templates/{template_id}")
def template_update(panel_id: int, template_id: int, body: TemplatePatch):
    """Change some fields of a user template; what is not sent stays, the panel always gets the whole template; the groups and templates are saved to a file first."""
    ch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None or k in _NULL_MEANS}
    return {"ok": True, **_wrap(pg_access.template_update, _cred(panel_id), template_id, ch)}


@router.delete("/panels/{panel_id}/pg/templates/{template_id}")
def template_delete(panel_id: int, template_id: int):
    """Delete a user template (users made from it stay); the groups and templates are saved to a file first."""
    return {"ok": True, **_wrap(pg_access.template_delete, _cred(panel_id), template_id)}


@router.post("/panels/{panel_id}/pg/templates/bulk")
def templates_bulk(panel_id: int, body: BulkIn):
    """Enable, disable or delete several user templates at once; the groups and templates are saved to a file first."""
    return {"ok": True, **_wrap(pg_access.template_bulk, _cred(panel_id), body.action, body.ids)}


@router.post("/panels/{panel_id}/pg/templates/{template_id}/user")
def template_user(panel_id: int, template_id: int, body: TemplateUserIn):
    """Create a user from a template; the template's prefix and suffix go around the username; a switched-off template is refused."""
    return {"ok": True, **_wrap(pg_access.template_user, _cred(panel_id), template_id, body.username, body.note)}


# ---- the way back
class RestoreIn(BaseModel):
    everything: bool = False
    groups: list[int] | None = Field(None, max_length=200)
    templates: list[int] | None = Field(None, max_length=200)
    dry_run: bool = True


@router.get("/panels/{panel_id}/pg/access/backups")
def access_backups(panel_id: int):
    """The files in which the groups and templates of this panel were saved before a change, newest first (the last 20)."""
    return {"ok": True, "backups": _wrap(pg_access.backups, _cred(panel_id))}


@router.post("/panels/{panel_id}/pg/access/backups/{name}/restore")
def access_restore(panel_id: int, name: str, body: RestoreIn):
    """Put a saved file back (all of it, or the listed groups and templates): gone ones are made again with new ids, changed fields are put back, nothing is deleted; dry_run=true (the default) only lists what it would do."""
    return {"ok": True, **_wrap(pg_access.restore, _cred(panel_id), name, body.groups, body.templates, body.everything, body.dry_run)}
