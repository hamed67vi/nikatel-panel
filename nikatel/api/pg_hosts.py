"""PasarGuard: the hosts of a PasarGuard panel in full (every field, the advanced settings, make / change / clone / delete, bulk, backups). Routes: /api/panels/{panel_id}/pg/hosts."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import panels, pg_hosts
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])
P = "/panels/{panel_id}/pg/hosts"
Names = list[str] | str | None
Adv = dict | str | None


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
    """The credential of a PasarGuard panel; a Marzban panel keeps its hosts in another shape."""
    try:
        c = panels.cred_for(panel_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    if c.get("type") != "pasarguard":
        raise HTTPException(400, "this is only for PasarGuard panels")

    return c


class HostIn(BaseModel):
    remark: str | None = Field(None, max_length=256)
    address: Names = None
    port: int | str | None = None
    inbound_tag: str | None = Field(None, max_length=128)
    sni: Names = None
    host: Names = None
    path: str | None = Field(None, max_length=1024)
    security: str | None = Field(None, max_length=20)
    alpn: Names = None
    fingerprint: str | None = Field(None, max_length=30)
    allowinsecure: bool | str | None = None
    is_disabled: bool | None = None
    random_user_agent: bool | None = None
    use_sni_as_host: bool | None = None
    vless_route: str | None = Field(None, max_length=128)
    priority: int | None = None
    status: list[str] | None = None
    pinned_peer_cert_sha256: str | None = Field(None, max_length=256)
    verify_peer_cert_by_name: Names = None
    cipher_suites: str | None = Field(None, max_length=1024)
    http_headers: Adv = None
    transport_settings: Adv = None
    mux_settings: Adv = None
    fragment_settings: Adv = None
    noise_settings: Adv = None
    ech: Adv = None
    wireguard_overrides: Adv = None
    subscription_templates: Adv = None
    final_mask_settings: Adv = None


class BulkIn(BaseModel):
    what: str = Field(max_length=10)
    ids: list[int] = Field(max_length=pg_hosts.MAX_BULK)
    confirm: bool = False


@router.get(P + "/new")
def host_new(panel_id: int):
    """The empty form of a new host with the choices it offers: the inbounds, the xray subscription templates, the next free priority and the values the panel allows."""
    return {"ok": True, **_wrap(pg_hosts.new_view, _cred(panel_id))}


@router.get(P + "/backups")
def hosts_backups(panel_id: int):
    """The files kept here before a change or a delete of a host of this panel, newest first (names and settings: no password)."""
    return {"ok": True, "backups": _wrap(pg_hosts.host_backups, _cred(panel_id))}


@router.post(P + "/restore/{name}")
def host_restore(panel_id: int, name: str):
    """Put a host back from one of the kept files: written over the host that has its id now, or made again (a new id) when it was deleted."""
    return {"ok": True, **_wrap(pg_hosts.host_restore, _cred(panel_id), name)}


@router.post(P + "/bulk")
def hosts_bulk(panel_id: int, body: BulkIn):
    """Enable, disable or delete several hosts with the panel's own bulk routes; a delete needs confirm and saves every host to a file first."""
    return {"ok": True, **_wrap(pg_hosts.bulk, _cred(panel_id), body.what, body.ids, body.confirm)}


@router.post(P)
def host_create(panel_id: int, body: HostIn):
    """Make a host from the fields given (a remark is needed); the priority is the next free one unless it is given."""
    return {"ok": True, **_wrap(pg_hosts.host_create, _cred(panel_id), body.model_dump(exclude_unset=True))}


@router.get(P + "/{host_id}")
def host_view(panel_id: int, host_id: int):
    """One host in full: its fields, the advanced settings as text, and the choices its form offers."""
    return {"ok": True, **_wrap(pg_hosts.host_view, _cred(panel_id), host_id)}


@router.put(P + "/{host_id}")
def host_update(panel_id: int, host_id: int, body: HostIn):
    """Change a host: only the fields that differ are changed, after the host is saved to a file; an advanced setting with a key the panel does not know is refused."""
    return {"ok": True, **_wrap(pg_hosts.host_update, _cred(panel_id), host_id, body.model_dump(exclude_unset=True))}


@router.post(P + "/{host_id}/clone")
def host_clone(panel_id: int, host_id: int):
    """Copy a host: the copy is called “… copy”, gets the next free priority and is switched off until it is looked at."""
    return {"ok": True, **_wrap(pg_hosts.host_clone, _cred(panel_id), host_id)}


@router.delete(P + "/{host_id}")
def host_delete(panel_id: int, host_id: int, confirm: bool = False):
    """Delete a host (needs confirm=true); it is saved to a file first, so Backups can make it again."""
    return {"ok": True, **_wrap(pg_hosts.host_delete, _cred(panel_id), host_id, confirm)}
