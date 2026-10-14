"""PasarGuard: the cores (Xray configs) of a PasarGuard panel with backup and rollback. Routes live under /api/panels/{panel_id}/pg/cores..."""
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


@router.get("/panels/{panel_id}/pg/cores")
def cores_list(panel_id: int):
    """The cores of a PasarGuard panel: id, name, type, created and updated, how many inbounds, outbounds and routing rules each has, and how many backups are kept."""
    return {"ok": True, "cores": _wrap(pg_cores.list_cores, _cred(panel_id))}


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
