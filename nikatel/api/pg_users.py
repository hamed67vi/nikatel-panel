"""PasarGuard: what the Users window and the Users tools need from a PasarGuard panel. One user: /api/panels/{panel_id}/pg/user/{username}; several users: .../pg/user-tools/."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import panels, pg_users
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])
U = "/panels/{panel_id}/pg/user/{username}"
T = "/panels/{panel_id}/pg/user-tools"


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
    """The credential of a PasarGuard panel; a Marzban panel has none of this."""
    try:
        c = panels.cred_for(panel_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    if c.get("type") != "pasarguard":
        raise HTTPException(400, "this is only for PasarGuard panels")

    return c


class UserIn(BaseModel):
    status: str | None = None
    limit_gb: float | None = None
    reset: str | None = None
    expire_day: str | None = None
    note: str | None = None
    on_hold_days: float | None = None
    on_hold_timeout_day: str | None = None
    group_ids: list[int] | None = None
    hwid_limit: int | None = None
    next_plan: dict | None = None


class ActionIn(BaseModel):
    what: str = Field(max_length=20)
    value: str | int | None = None
    confirm: bool = False


class BulkIn(BaseModel):
    what: str = Field(max_length=20)
    usernames: list[str] = Field(max_length=pg_users.MAX_BULK)
    value: str | int | None = None
    confirm: bool = False


class ShiftIn(BaseModel):
    what: str = Field(max_length=20)
    amount: float
    filters: dict = Field(default_factory=dict)
    dry_run: bool = True
    confirm: bool = False


class FromTemplateIn(BaseModel):
    template_id: int
    username: str = Field(max_length=128)
    note: str = Field("", max_length=pg_users.MAX_NOTE)


class BulkFromTemplateIn(BaseModel):
    template_id: int
    count: int
    strategy: str = Field("random", max_length=10)
    base: str = Field("", max_length=128)
    start_number: int | None = None
    note: str = Field("", max_length=pg_users.MAX_NOTE)


# ------------------------------------------------------------------ several users (declared first: a user may be called "expired")
@router.get(T + "/options")
def users_options(panel_id: int):
    """The groups, user templates and admins of the panel (id and name) that the forms of the Users tools choose from."""
    return {"ok": True, **_wrap(pg_users.options, _cred(panel_id))}


@router.post(T + "/bulk")
def users_bulk(panel_id: int, body: BulkIn):
    """Do one thing to the chosen users with the panel's own bulk route: enable, disable, delete, reset, revoke_sub (these three need confirm), set_owner or apply_template."""
    return {"ok": True, **_wrap(pg_users.bulk, _cred(panel_id), body.what, body.usernames, body.value, body.confirm)}


@router.post(T + "/shift")
def users_shift(panel_id: int, body: ShiftIn):
    """Add or take away days (end date) or GB (data limit) for every user a filter matches: the dry run only counts them, the real run needs confirm."""
    return {"ok": True, **_wrap(pg_users.shift, _cred(panel_id), body.what, body.amount, body.filters, body.dry_run, body.confirm)}


@router.get(T + "/expired")
def users_expired(panel_id: int, target: str = "expired", admin: str | None = None, before_day: str | None = None, after_day: str | None = None):
    """List the users that are expired, limited, on hold or disabled (by the target), for an admin and a range of dates."""
    return {"ok": True, **_wrap(pg_users.expired, _cred(panel_id), target, admin, before_day, after_day)}


@router.delete(T + "/expired")
def users_expired_delete(panel_id: int, target: str = "expired", typed: str = "", admin: str | None = None, before_day: str | None = None, after_day: str | None = None):
    """Delete for good the users the list of the same target shows; the target must be typed back."""
    return {"ok": True, **_wrap(pg_users.expired, _cred(panel_id), target, admin, before_day, after_day, True, typed)}


@router.post(T + "/from-template")
def user_from_template(panel_id: int, body: FromTemplateIn):
    """Make one user from a user template of the panel."""
    return {"ok": True, **_wrap(pg_users.from_template, _cred(panel_id), body.template_id, body.username, body.note)}


@router.post(T + "/bulk-from-template")
def users_from_template(panel_id: int, body: BulkFromTemplateIn):
    """Make up to 500 users at once from a user template, with random or numbered names; the subscription links come back."""
    return {"ok": True, **_wrap(pg_users.bulk_from_template, _cred(panel_id), body.template_id, body.count, body.strategy, body.base, body.start_number, body.note)}


# ------------------------------------------------------------------ one user
@router.get(U)
def user_view(panel_id: int, username: str):
    """One user of the panel in full (groups, on hold, devices limit, next plan, owner) with the groups, templates and admins its forms offer."""
    return {"ok": True, **_wrap(pg_users.user_view, _cred(panel_id), username)}


@router.put(U)
def user_modify(panel_id: int, username: str, body: UserIn):
    """Change a user: only the fields that differ are sent, and its next plan always goes along so the panel does not delete it."""
    return {"ok": True, **_wrap(pg_users.modify, _cred(panel_id), username, body.model_dump(exclude_unset=True))}


@router.post(U + "/action")
def user_action(panel_id: int, username: str, body: ActionIn):
    """One action on a user: reset, revoke_sub, active_next, set_owner, enable, disable or delete (delete needs confirm)."""
    return {"ok": True, **_wrap(pg_users.action, _cred(panel_id), username, body.what, body.value, body.confirm)}


@router.get(U + "/devices")
def user_devices(panel_id: int, username: str):
    """The devices a user has connected from, with their system, model and last use."""
    return {"ok": True, **_wrap(pg_users.devices, _cred(panel_id), username)}


@router.delete(U + "/devices")
def user_device_delete(panel_id: int, username: str, hwid: str):
    """Remove one device of a user (the user can then connect from a new one)."""
    return {"ok": True, **_wrap(pg_users.device_delete, _cred(panel_id), username, hwid)}


@router.post(U + "/devices/reset")
def user_devices_reset(panel_id: int, username: str):
    """Remove every device of a user."""
    return {"ok": True, **_wrap(pg_users.devices_reset, _cred(panel_id), username)}


@router.get(U + "/sub-updates")
def user_sub_updates(panel_id: int, username: str, offset: int = 0, limit: int = 10):
    """The last times a user's subscription link was fetched: when, by which app, from which address."""
    return {"ok": True, **_wrap(pg_users.sub_updates, _cred(panel_id), username, offset, limit)}


@router.get(U + "/online")
def user_online(panel_id: int, username: str):
    """The addresses a user is connected from right now, on each node."""
    return {"ok": True, **_wrap(pg_users.online_ips, _cred(panel_id), username)}
