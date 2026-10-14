"""PasarGuard: the admins, the API keys and the settings of a PasarGuard panel. Routes live under /api/panels/{panel_id}/pg/admins, /pg/admin/api-keys and /pg/settings."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import panels, pg_admin
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (PanelError, RuntimeError) as e:
        raise HTTPException(502, str(e))


def _cred(panel_id: int) -> dict:
    """The credential of a PasarGuard panel; a Marzban panel has none of these routes."""
    c = _wrap(panels.cred_for, panel_id)

    if c.get("type") != "pasarguard":
        raise HTTPException(400, "this is only for PasarGuard panels")

    return c


# ------------------------------------------------------------------ admins
class NotifyIn(BaseModel):
    create: bool = True
    modify: bool = True
    delete: bool = True
    status_change: bool = True
    reset_data_usage: bool = True
    data_reset_by_next: bool = True
    subscription_revoked: bool = True


class OverridesIn(BaseModel):
    max_users: int | None = Field(None, ge=0, le=10 ** 9)
    data_limit_min_gb: float | None = Field(None, ge=0, le=1_000_000)
    data_limit_max_gb: float | None = Field(None, ge=0, le=1_000_000)
    expire_min_days: float | None = Field(None, ge=0, le=36500)
    expire_max_days: float | None = Field(None, ge=0, le=36500)
    min_hwid_per_user: int | None = Field(None, ge=0, le=10 ** 6)
    max_hwid_per_user: int | None = Field(None, ge=0, le=10 ** 6)
    on_hold_timeout_min_days: float | None = Field(None, ge=0, le=36500)
    on_hold_timeout_max_days: float | None = Field(None, ge=0, le=36500)


class AdminFields(BaseModel):
    role_id: int | None = Field(None, ge=1)
    data_limit_gb: float | None = Field(None, ge=0, le=1_000_000)
    telegram_id: int | None = Field(None, ge=1, le=2 ** 53)
    discord_webhook: str | None = Field(None, max_length=1024)
    note: str | None = Field(None, max_length=500)
    sub_domain: str | None = Field(None, max_length=256)
    sub_template: str | None = Field(None, max_length=1024)
    profile_title: str | None = Field(None, max_length=512)
    support_url: str | None = Field(None, max_length=1024)
    notify: NotifyIn | None = None
    overrides: OverridesIn | None = None


class AdminIn(AdminFields):
    username: str = Field(min_length=3, max_length=34)
    password: str = Field(min_length=1, max_length=200)
    is_sudo: bool | None = None


class AdminPatch(AdminFields):
    clear_discord: bool = False


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class StatusIn(BaseModel):
    status: str = Field(pattern=r"^(active|disabled)$")


class ConfirmIn(BaseModel):
    confirm: str = Field("", max_length=64)


@router.get("/panels/{panel_id}/pg/admins")
def admins_list(panel_id: int):
    """Every admin of the PasarGuard panel with its role, status, users, usage and limit, the counts, the roles to pick from and who Nikatel is signed in as."""
    return {"ok": True, **_wrap(pg_admin.admins_list, _cred(panel_id))}


@router.post("/panels/{panel_id}/pg/admins")
def admin_create(panel_id: int, body: AdminIn):
    """Make a new admin (a role, a password that follows the panel's rules, optional limits and contacts); the password is sent to the panel and kept nowhere."""
    return {"ok": True, "admin": _wrap(pg_admin.admin_create, _cred(panel_id), body.model_dump(exclude_unset=True))}


@router.put("/panels/{panel_id}/pg/admins/{admin_id}")
def admin_update(panel_id: int, admin_id: int, body: AdminPatch):
    """Change the role, limit, contacts, notices or limit overrides of an admin; only the fields that differ from what it has now are sent."""
    return {"ok": True, "admin": _wrap(pg_admin.admin_update, _cred(panel_id), admin_id, body.model_dump(exclude_unset=True))}


@router.post("/panels/{panel_id}/pg/admins/{admin_id}/password")
def admin_password(panel_id: int, admin_id: int, body: PasswordIn):
    """Give an admin a new password (checked against the panel's rules first); the admin Nikatel is signed in as is refused."""
    _wrap(pg_admin.admin_password, _cred(panel_id), admin_id, body.password)
    return {"ok": True}


@router.post("/panels/{panel_id}/pg/admins/{admin_id}/status")
def admin_status(panel_id: int, admin_id: int, body: StatusIn):
    """Switch an admin on (active) or off (disabled); the admin Nikatel is signed in as can not be switched off."""
    return {"ok": True, "admin": _wrap(pg_admin.admin_status, _cred(panel_id), admin_id, body.status)}


@router.post("/panels/{panel_id}/pg/admins/{admin_id}/reset-usage")
def admin_reset_usage(panel_id: int, admin_id: int):
    """Set the used traffic of an admin back to zero (a limited admin becomes active again)."""
    return {"ok": True, "admin": _wrap(pg_admin.admin_reset_usage, _cred(panel_id), admin_id)}


@router.post("/panels/{panel_id}/pg/admins/{admin_id}/users/disable")
def admin_users_disable(panel_id: int, admin_id: int):
    """Switch off every active user of an admin."""
    return {"ok": True, **_wrap(pg_admin.admin_users_action, _cred(panel_id), admin_id, "disable")}


@router.post("/panels/{panel_id}/pg/admins/{admin_id}/users/activate")
def admin_users_activate(panel_id: int, admin_id: int):
    """Switch on every disabled user of an admin, including the ones that were switched off on purpose."""
    return {"ok": True, **_wrap(pg_admin.admin_users_action, _cred(panel_id), admin_id, "activate")}


@router.post("/panels/{panel_id}/pg/admins/{admin_id}/users/delete")
def admin_users_delete(panel_id: int, admin_id: int, body: ConfirmIn):
    """Delete every user of an admin for good; the username of the admin must be given in `confirm`."""
    return {"ok": True, **_wrap(pg_admin.admin_users_delete, _cred(panel_id), admin_id, body.confirm)}


@router.delete("/panels/{panel_id}/pg/admins/{admin_id}")
def admin_delete(panel_id: int, admin_id: int, users: str = Query("keep", pattern=r"^(keep|disable|delete)$"), confirm: str = Query("", max_length=64)):
    """Delete an admin and its API keys; its users stay without an owner (`users=keep`), are switched off first (`disable`) or are deleted too (`delete`, with the username in `confirm`)."""
    return {"ok": True, **_wrap(pg_admin.admin_delete, _cred(panel_id), admin_id, users, confirm)}


@router.get("/panels/{panel_id}/pg/admins/{admin_id}/usage")
def admin_usage(panel_id: int, admin_id: int, start: str = Query(alias="from", min_length=10, max_length=10), end: str = Query(alias="to", min_length=10, max_length=10)):
    """The traffic of an admin per Tehran day (a day begins at 20:30 UTC of the day before) between two dates YYYY-MM-DD, both included."""
    return {"ok": True, **_wrap(pg_admin.admin_usage, _cred(panel_id), admin_id, start, end)}


# ------------------------------------------------------------------ API keys
class KeyFields(BaseModel):
    note: str | None = Field(None, max_length=512)
    inherit_permissions: bool = True
    permissions: dict | None = Field(None, max_length=20)
    expire: str | None = Field(None, max_length=10)
    admin_id: int | None = Field(None, ge=1)


class KeyIn(KeyFields):
    name: str = Field(min_length=1, max_length=128)


class KeyPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    note: str | None = Field(None, max_length=512)
    status: str | None = Field(None, pattern=r"^(active|disabled)$")
    inherit_permissions: bool | None = None
    permissions: dict | None = Field(None, max_length=20)
    expire: str | None = Field(None, max_length=10)
    admin_id: int | None = Field(None, ge=1)


class KeysDeleteIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)


@router.get("/panels/{panel_id}/pg/admin/api-keys")
def keys_list(panel_id: int):
    """The API keys of the PasarGuard panel (never their secrets: only the trimmed form), the admins they can belong to and the permission names a key can hold."""
    return {"ok": True, **_wrap(pg_admin.keys_list, _cred(panel_id))}


@router.post("/panels/{panel_id}/pg/admin/api-keys")
def key_create(panel_id: int, body: KeyIn):
    """Make an API key for an admin; the answer holds its secret, which the panel can not show again, so it is shown once and kept nowhere."""
    return {"ok": True, **_wrap(pg_admin.key_create, _cred(panel_id), body.model_dump(exclude_unset=True))}


@router.put("/panels/{panel_id}/pg/admin/api-keys/{key_id}")
def key_update(panel_id: int, key_id: int, body: KeyPatch):
    """Change the name, note, expiry day, status, owner or permissions of a key; only what differs from now is sent, and the key Nikatel is connected with keeps its access."""
    return {"ok": True, "key": _wrap(pg_admin.key_update, _cred(panel_id), key_id, body.model_dump(exclude_unset=True))}


@router.post("/panels/{panel_id}/pg/admin/api-keys/{key_id}/revoke")
def key_revoke(panel_id: int, key_id: int):
    """Renew a key: the old secret stops working at once and the answer holds the new one, shown once; the key Nikatel is connected with is refused."""
    return {"ok": True, **_wrap(pg_admin.key_revoke, _cred(panel_id), key_id)}


@router.delete("/panels/{panel_id}/pg/admin/api-keys/{key_id}")
def key_delete(panel_id: int, key_id: int):
    """Delete one API key for good (its settings, without the secret, are saved first); the key Nikatel is connected with is refused."""
    return {"ok": True, **_wrap(pg_admin.key_delete, _cred(panel_id), key_id)}


@router.post("/panels/{panel_id}/pg/admin/api-keys/bulk-delete")
def keys_delete(panel_id: int, body: KeysDeleteIn):
    """Delete several API keys in one call (all or none); the key Nikatel is connected with is refused."""
    return {"ok": True, **_wrap(pg_admin.keys_delete, _cred(panel_id), body.ids)}


# ------------------------------------------------------------------ settings
class SettingsIn(BaseModel):
    values: dict = Field(default_factory=dict, max_length=60)
    clear: list[str] = Field(default_factory=list, max_length=60)
    dry_run: bool = False


class RestoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    section: str = Field("", max_length=40)


@router.get("/panels/{panel_id}/pg/settings")
def settings_get(panel_id: int):
    """The settings of the PasarGuard panel by section with a description of every field, the values (a secret only as set / not set) and the backups kept."""
    return {"ok": True, **_wrap(pg_admin.settings_get, _cred(panel_id))}


@router.put("/panels/{panel_id}/pg/settings/{section}")
def settings_update(panel_id: int, section: str, body: SettingsIn):
    """Change ONE section of the settings: a secret changes only when a new value is given, the previous settings are saved without secrets first, `dry_run` lists the changes and writes nothing."""
    return {"ok": True, **_wrap(pg_admin.settings_update, _cred(panel_id), section, body.values, body.clear, body.dry_run)}


@router.get("/panels/{panel_id}/pg/settings/backups")
def settings_backups(panel_id: int):
    """The backups of this panel's settings that Nikatel keeps (never with a secret), newest first, with the section that was being changed."""
    _cred(panel_id)
    return {"ok": True, "backups": pg_admin.settings_backups(panel_id)}


@router.post("/panels/{panel_id}/pg/settings/restore")
def settings_restore(panel_id: int, body: RestoreIn):
    """Put the previous values of a section back from a backup: only its non-secret values are sent, the secrets set now stay, and the state before is saved too."""
    return {"ok": True, **_wrap(pg_admin.settings_restore, _cred(panel_id), body.name, body.section)}
