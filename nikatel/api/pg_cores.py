"""PasarGuard: the cores (Xray configs) of a PasarGuard panel with backup and rollback. Routes live under /api/panels/{panel_id}/pg/cores..."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from ..auth import current_user
from ..services import panels, pg_cores
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    """Run a service function; its refusals become HTTP errors: 409 for a change that needs `confirm` or met a newer core, 400 for bad input,
    404 when the panel says the core is not there, 502 for anything else the panel did or did not answer."""
    try:
        return fn(*a, **kw)
    except (pg_cores.NeedsConfirm, pg_cores.Conflict) as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except PanelError as e:
        raise HTTPException(404 if e.code == 404 else 502, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from None


def _cred(panel_id: int) -> dict:
    try:
        c = panels.cred_for(panel_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None

    if c.get("type") != "pasarguard":
        raise HTTPException(400, "the cores tab is for PasarGuard panels")

    return c


class EditIn(BaseModel):
    text: str | None = Field(None, max_length=pg_cores.MAX_TEXT)
    config: dict | None = None

    @model_validator(mode="after")
    def _one_of_them(self):
        if (self.text is None) == (self.config is None):
            raise ValueError("send exactly one of text (the editor's text) and config (an object)")

        return self


class SaveIn(EditIn):
    confirm: bool = False
    restart: bool = False
    base_rev: str | None = Field(None, max_length=64)
    note: str = Field("", max_length=120)


class RestoreIn(BaseModel):
    confirm: bool = False
    restart: bool = False


class RestartIn(BaseModel):
    confirm: bool = False


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    type: Literal["xray", "wg", "mtproto", "singbox"] = "xray"
    text: str | None = Field(None, max_length=pg_cores.MAX_TEXT)
    config: dict | None = None
    copy_from: int | None = Field(None, ge=1)
    exclude_inbound_tags: list[str] | None = Field(None, max_length=500)
    fallbacks_inbound_tags: list[str] | None = Field(None, max_length=500)


class MetaIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=256)
    exclude_inbound_tags: list[str] | None = Field(None, max_length=500)
    fallbacks_inbound_tags: list[str] | None = Field(None, max_length=500)
    base_rev: str | None = Field(None, max_length=64)


class ScanIn(BaseModel):
    target: str = Field(min_length=1, max_length=253)
    timeout: float | None = Field(None, ge=1, le=30)


class InboundIn(BaseModel):
    op: Literal["add", "set", "delete"]
    tag: str | None = Field(None, max_length=256)
    spec: dict | None = None
    text: str | None = Field(None, max_length=200_000)


class InboundApplyIn(InboundIn):
    confirm: bool = False
    restart: bool = False
    base_rev: str | None = Field(None, max_length=64)


@router.get("/panels/{panel_id}/pg/cores")
def cores_list(panel_id: int):
    """The cores of a PasarGuard panel: id, name, type, created and updated, how many inbounds, outbounds and routing rules each has, and how many backups are kept."""
    return {"ok": True, "cores": _wrap(pg_cores.list_cores, _cred(panel_id))}


@router.get("/panels/{panel_id}/pg/cores/template")
def core_template(panel_id: int, type: Literal["xray", "wg", "mtproto", "singbox"] = "xray"):
    """The config a new core of this type starts with (for Xray the one of the panel, its inbound with a tag no core uses yet), also as editor text, and the types the panel knows."""
    c = _cred(panel_id)
    cfg = pg_cores.default_config(type, pg_cores._all_tags(c))
    return {"ok": True, "type": type, "types": list(pg_cores.CORE_TYPES), "config": cfg, "text": pg_cores.config_text(cfg)}


@router.get("/panels/{panel_id}/pg/cores/deleted")
def cores_deleted(panel_id: int):
    """The cores of this panel that are gone from it but have a backup here (deleted from this page, or from the panel itself after a change made here): the newest backup of each."""
    return {"ok": True, "cores": _wrap(pg_cores.deleted_backups, _cred(panel_id))}


@router.post("/panels/{panel_id}/pg/cores/deleted/{core_id}/{name}/recreate")
def core_recreate(panel_id: int, core_id: int, name: str):
    """Make a NEW core (new id) from the backup of a core that is gone: its name, type, config and tag lists. The nodes must be pointed at it again."""
    return {"ok": True, **_wrap(pg_cores.recreate, _cred(panel_id), core_id, name)}


@router.post("/panels/{panel_id}/pg/cores/reality-scan")
def core_reality_scan(panel_id: int, body: ScanIn):
    """Ask the panel whether a site can be a REALITY target: TLS 1.3, h2, x25519, the names of its certificate and the delay. Nothing is changed anywhere."""
    return {"ok": True, **_wrap(pg_cores.reality_scan, _cred(panel_id), body.target, body.timeout)}


@router.post("/panels/{panel_id}/pg/cores")
def core_create(panel_id: int, body: CreateIn):
    """Make a new core: a name, a type (xray, wg, mtproto, singbox) and a config (text, object, a copy of another core, or the starting config); nothing restarts, no node runs it until it is chosen for one."""
    return {"ok": True, **_wrap(pg_cores.create, _cred(panel_id), body.name, body.type, body.text, body.config, body.exclude_inbound_tags, body.fallbacks_inbound_tags, body.copy_from)}


@router.get("/panels/{panel_id}/pg/cores/{core_id}")
def core_get(panel_id: int, core_id: int):
    """One core: its config with every secret replaced by a placeholder (as an object and as text), the inbounds with the hosts and groups that use each, the outbounds, a revision to send back with a save, and its backups."""
    return {"ok": True, **_wrap(pg_cores.core_view, _cred(panel_id), core_id)}


@router.post("/panels/{panel_id}/pg/cores/{core_id}/dry-run")
def core_dry_run(panel_id: int, core_id: int, body: EditIn):
    """Check an edited config without saving it: structure errors, warnings, and a line diff against the config on the panel. Nothing is changed anywhere."""
    return {"ok": True, **_wrap(pg_cores.dry_run, _cred(panel_id), core_id, body.text, body.config)}


@router.put("/panels/{panel_id}/pg/cores/{core_id}")
def core_save(panel_id: int, core_id: int, body: SaveIn):
    """Save an edited config: the current one is backed up first; warnings need confirm true, and so does restart true (it cuts customers for a moment); a base_rev older than the core is refused."""
    return {"ok": True, **_wrap(pg_cores.save, _cred(panel_id), core_id, body.text, body.config, body.confirm, body.restart, body.base_rev, body.note)}


@router.post("/panels/{panel_id}/pg/cores/{core_id}/restart")
def core_restart(panel_id: int, core_id: int, body: RestartIn):
    """Restart Xray on every node of the core and say how the nodes look afterwards; customers of those nodes are cut for a moment, so the body must say confirm true."""
    return {"ok": True, **_wrap(pg_cores.restart, _cred(panel_id), core_id, body.confirm)}


@router.get("/panels/{panel_id}/pg/cores/{core_id}/delete-check")
def core_delete_check(panel_id: int, core_id: int):
    """What deleting a core would take with it - the nodes that run it, the hosts and groups that use its inbounds - and whether the panel allows it at all; nothing is changed."""
    return {"ok": True, **_wrap(pg_cores.delete_check, _cred(panel_id), core_id)}


@router.delete("/panels/{panel_id}/pg/cores/{core_id}")
def core_delete(panel_id: int, core_id: int, confirm: bool = False):
    """Delete a core (never the first one). The refusal lists what it takes with it (the nodes that run it, hosts and groups that use its inbounds); confirm=true goes on, and the whole core is backed up here first."""
    return {"ok": True, **_wrap(pg_cores.delete, _cred(panel_id), core_id, confirm)}


@router.patch("/panels/{panel_id}/pg/cores/{core_id}/meta")
def core_meta(panel_id: int, core_id: int, body: MetaIn):
    """Rename a core and/or change its two tag lists (excluded and fallback inbound tags); the config stays as it is, the core is backed up first and read back."""
    return {"ok": True, **_wrap(pg_cores.update_meta, _cred(panel_id), core_id, body.name, body.exclude_inbound_tags, body.fallbacks_inbound_tags, body.base_rev)}


@router.post("/panels/{panel_id}/pg/cores/{core_id}/inbounds/dry-run")
def core_inbound_dry_run(panel_id: int, core_id: int, body: InboundIn):
    """What adding (spec: the form, or text: its JSON), replacing (op set, text) or deleting (op delete) ONE inbound would do: the checks, the warnings about the hosts and groups that use it and the diff; nothing is written."""
    return {"ok": True, **_wrap(pg_cores.inbound_dry_run, _cred(panel_id), core_id, body.op, body.tag, body.spec, body.text)}


@router.post("/panels/{panel_id}/pg/cores/{core_id}/inbounds/apply")
def core_inbound_apply(panel_id: int, core_id: int, body: InboundApplyIn):
    """Add, replace or delete ONE inbound with everything a save does: checks, warnings that need confirm, a backup first, a read back, and (restart true, with confirm) a restart of the nodes. A REALITY inbound made from the form returns its public key."""
    return {"ok": True, **_wrap(pg_cores.inbound_apply, _cred(panel_id), core_id, body.op, body.tag, body.spec, body.text, body.confirm, body.restart, body.base_rev)}


@router.get("/panels/{panel_id}/pg/cores/{core_id}/backups")
def core_backups(panel_id: int, core_id: int):
    """The backups kept for a core (the last 20 of this panel and core), newest first; read from this server's disk, not from the panel."""
    _cred(panel_id)
    return {"ok": True, "backups": pg_cores.backups(panel_id, core_id)}


@router.get("/panels/{panel_id}/pg/cores/{core_id}/backups/{name}")
def core_backup_get(panel_id: int, core_id: int, name: str):
    """One backup: its config with secrets as placeholders, the diff from the config on the panel to it, and the errors and warnings that restoring it would meet."""
    return {"ok": True, **_wrap(pg_cores.backup_view, _cred(panel_id), core_id, name)}


@router.post("/panels/{panel_id}/pg/cores/{core_id}/backups/{name}/restore")
def core_backup_restore(panel_id: int, core_id: int, name: str, body: RestoreIn | None = None):
    """Put a backup back: the current config is backed up first, then the backup is PUT, with the same checks and the same confirm rule as a save."""
    b = body or RestoreIn()
    return {"ok": True, **_wrap(pg_cores.restore, _cred(panel_id), core_id, name, b.confirm, b.restart)}
