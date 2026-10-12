from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Panel
from ..services import core, panels
from ..services.panels import PanelError

router = APIRouter(tags=["panels"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (PanelError, RuntimeError) as e:
        raise HTTPException(502, str(e))


def _cred(panel_id: int) -> dict:
    return _wrap(panels.cred_for, panel_id)


class PanelIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    type: str
    url: str
    username: str
    password: str
    verify_tls: bool = True


class PanelPatch(BaseModel):
    name: str | None = None
    url: str | None = None
    username: str | None = None
    password: str | None = None
    verify_tls: bool | None = None
    enabled: bool | None = None


@router.get("/panels")
def list_panels():
    """The Marzban / PasarGuard panels this panel manages: status, latency, version, users, nodes (as last checked)."""
    with session() as db:
        return {"ok": True, "panels": [panels.public(p) for p in db.scalars(select(Panel).order_by(Panel.name))]}


@router.post("/panels")
def add_panel(body: PanelIn):
    """Add a panel: the login is tried first; the password stays on this server."""
    return {"ok": True, "panel": _wrap(panels.add, body.name, body.type, body.url, body.username, body.password, body.verify_tls)}


@router.patch("/panels/{panel_id}")
def patch_panel(panel_id: int, body: PanelPatch):
    """Change a panel's name, address, login, TLS check or whether it is checked."""
    return {"ok": True, "panel": _wrap(panels.update, panel_id, body.model_dump(exclude_none=True))}


@router.delete("/panels/{panel_id}")
def delete_panel(panel_id: int):
    """Remove a panel from the list (nothing changes on the panel itself)."""
    panels.remove(panel_id)
    return {"ok": True}


@router.post("/panels/{panel_id}/test-config")
def test_config(panel_id: int):
    """A test user on the panel whose subscription is imported on the probe as the panel's group."""
    return {"ok": True, **_wrap(panels.test_config, panel_id)}


@router.post("/panels/{panel_id}/check")
def check_panel(panel_id: int):
    """Ask the panel now: status, latency, version, nodes (a few seconds)."""
    c = _cred(panel_id)
    res = panels.check(c)
    return {"ok": res["ok"], **res}


@router.post("/panels/{panel_id}/nodes/{node_id}/reconnect")
def node_reconnect(panel_id: int, node_id: int):
    """Ask the panel to reconnect one of its nodes."""
    _wrap(panels.node_reconnect, _cred(panel_id), node_id)
    return {"ok": True}


@router.get("/panels/{panel_id}/nodes/{node_id}/logs")
def node_logs(panel_id: int, node_id: int):
    """The last lines of one node's log as the panel keeps it."""
    return {"ok": True, "log": _wrap(panels.node_logs, _cred(panel_id), node_id)}


# ---- users
@router.get("/panels/{panel_id}/users")
def users(panel_id: int, search: str = "", offset: int = 0, limit: int = 40, status: str = "", sort: str = ""):
    """Users of the panel, paged, searched and filtered by status (read from the panel when asked)."""
    return {"ok": True, **_wrap(panels.users, _cred(panel_id), search, offset, limit, status, sort)}


@router.get("/panels/{panel_id}/users/{username}")
def user_get(panel_id: int, username: str):
    """One user of the panel in full."""
    return {"ok": True, "user": _wrap(panels.user_get, _cred(panel_id), username)}


class UserPatch(BaseModel):
    status: str | None = None
    expire_date: str | None = None
    limit_gb: float | None = None
    reset: str | None = None
    note: str | None = None
    inbounds: dict | None = None
    group_ids: list[int] | None = None


@router.put("/panels/{panel_id}/users/{username}")
def user_update(panel_id: int, username: str, body: UserPatch):
    """Change a user: expiry, data limit, status, note, proxies."""
    return {"ok": True, "user": _wrap(panels.user_update, _cred(panel_id), username, body.model_dump(exclude_none=True))}


class UserAction(BaseModel):
    action: str
    value: float | int | str | None = None
    usernames: list[str] = Field(default_factory=list)


@router.post("/panels/{panel_id}/users/{username}/action")
def user_action(panel_id: int, username: str, body: UserAction):
    """One action on a user: disable, enable, renew, add data, reset usage, delete."""
    _wrap(panels.user_action, _cred(panel_id), username, body.action, body.value)
    return {"ok": True}


@router.post("/panels/{panel_id}/users-bulk")
def users_bulk(panel_id: int, body: UserAction):
    """The same action on many users at once (the panel is written one user at a time)."""
    return {"ok": True, **_wrap(panels.users_bulk, _cred(panel_id), body.usernames, body.action, body.value)}


# ---- hosts
@router.get("/panels/{panel_id}/hosts")
def hosts(panel_id: int):
    """The hosts of the panel's subscription links (address, port, SNI, security) per inbound."""
    rows, _raw = _wrap(panels.hosts, _cred(panel_id))
    return {"ok": True, "hosts": rows}


@router.get("/panels/{panel_id}/hosts/{key}")
def host_get(panel_id: int, key: str):
    """One host in full."""
    return {"ok": True, "host": _wrap(panels.host_get, _cred(panel_id), key)}


@router.put("/panels/{panel_id}/hosts/{key}")
def host_save(panel_id: int, key: str, body: dict):
    """Change a host (the whole hosts document is read first and written back)."""
    return {"ok": True, "host": _wrap(panels.host_save, _cred(panel_id), key, body)}


class HostsToggle(BaseModel):
    keys: list[str]
    disable: bool = True


@router.post("/panels/{panel_id}/hosts-toggle")
def hosts_toggle(panel_id: int, body: HostsToggle):
    """Disable or enable several hosts at once."""
    return {"ok": True, "changed": _wrap(panels.host_toggle, _cred(panel_id), body.keys, body.disable)}


@router.post("/panels/{panel_id}/hosts/{key}/clone")
def host_clone(panel_id: int, key: str):
    """Copy a host (same inbound, same settings) to change afterwards."""
    _wrap(panels.host_clone, _cred(panel_id), key)
    return {"ok": True}


@router.delete("/panels/{panel_id}/hosts/{key}")
def host_delete(panel_id: int, key: str):
    """Delete a host; users lose that address in their links."""
    _wrap(panels.host_delete, _cred(panel_id), key)
    return {"ok": True}


# ---- core / outbounds
@router.get("/panels/{panel_id}/core")
def core_get(panel_id: int):
    """The Xray core config of the panel: outbounds, routing rules, balancers, and the backups kept here."""
    cur = _wrap(core.get, _cred(panel_id))
    return {"ok": True, "kind": cur["kind"], "name": cur["name"], "config": cur["config"], "summary": core.summary(cur["config"]), "backups": core.backups(panel_id)}


class CoreIn(BaseModel):
    config: dict
    note: str = ""


@router.put("/panels/{panel_id}/core")
def core_save(panel_id: int, body: CoreIn):
    """Write a whole core config (validated, the previous one backed up, read back, put back when the panel did not keep it)."""
    return {"ok": True, **_wrap(core.save, _cred(panel_id), body.config, body.note)}


class OutboundIn(BaseModel):
    uri: str
    tag: str
    make_default: bool = False


@router.post("/panels/{panel_id}/core/outbounds")
def core_add_outbound(panel_id: int, body: OutboundIn):
    """Add an outbound to the core from a share link (vless, vmess, trojan, ss), optionally as the default."""
    return {"ok": True, **_wrap(core.add_from_link, _cred(panel_id), body.uri, body.tag, body.make_default)}


@router.delete("/panels/{panel_id}/core/outbounds/{tag}")
def core_del_outbound(panel_id: int, tag: str):
    """Remove an outbound (the rules that use it go too)."""
    c = _cred(panel_id)
    cur = _wrap(core.get, c)
    return {"ok": True, **_wrap(core.save, c, core.without_outbound(cur["config"], tag), f"before removing outbound {tag}")}


@router.post("/panels/{panel_id}/core/outbounds/{tag}/default")
def core_default(panel_id: int, tag: str):
    """Make an outbound the default (first in the list)."""
    c = _cred(panel_id)
    cur = _wrap(core.get, c)
    new = _wrap(core.as_default, cur["config"], tag)
    return {"ok": True, **_wrap(core.save, c, new, f"before making {tag} the default")}


@router.post("/panels/{panel_id}/core/restore/{name}")
def core_restore(panel_id: int, name: str):
    """Put one of the kept core configs back."""
    return {"ok": True, **_wrap(core.restore, _cred(panel_id), name)}
