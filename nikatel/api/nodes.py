from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import nodes
from ..ssh import SSHError

router = APIRouter(tags=["nodes"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))


@router.get("/nodes/presets")
def presets():
    return {"ok": True, "presets": [{"key": k, "label": v[0], "command": v[1]} for k, v in nodes.PRESETS.items()]}


class Cmd(BaseModel):
    command: str = Field(min_length=1, max_length=4000)


@router.post("/servers/{server_id}/console")
def console(server_id: int, body: Cmd):
    return {"ok": True, **_wrap(nodes.console, server_id, body.command)}


class Pw(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.post("/servers/{server_id}/connect")
def connect(server_id: int, body: Pw):
    return {"ok": True, **_wrap(nodes.connect, server_id, body.password)}


class Many(BaseModel):
    role: str
    lines: str = Field(max_length=20000)
    password: str = ""
    ssh_user: str = "root"
    ssh_port: int = 22


@router.post("/servers/add-many")
def add_many(body: Many):
    return {"ok": True, "items": _wrap(nodes.add_many, body.role, body.lines, body.password, body.ssh_user, body.ssh_port)}


@router.get("/servers/{server_id}/launcher")
def launcher(server_id: int, app: str = "ssh"):
    name, body = _wrap(nodes.launcher, server_id, app)
    return Response(body, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/nodes/user-key")
def user_key(part: str = "private"):
    """The operator's own key pair: `private` once for the PC (~/.ssh/nikatel_user), `public` to read."""
    pub = nodes.user_key_ensure()

    if part == "public":
        return {"ok": True, "public": pub}

    data = nodes.user_key_path().read_bytes()
    return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="nikatel_user"'})


class Act(BaseModel):
    action: str


@router.post("/servers/{server_id}/abuse")
def abuse(server_id: int, body: Act):
    return {"ok": True, **_wrap(nodes.abuse, server_id, body.action)}


@router.post("/servers/{server_id}/reboot")
def reboot(server_id: int):
    _wrap(nodes.reboot, server_id)
    return {"ok": True}


@router.post("/servers/geo-refresh")
def geo_refresh(only_missing: bool = False):
    return {"ok": True, "changes": _wrap(nodes.geo_refresh, only_missing)}


@router.post("/servers/gre-check-all")
def gre_check_all(role: str = ""):
    return {"ok": True, **_wrap(nodes.gre_check_all, role)}


@router.post("/servers/{server_id}/gre-check")
def gre_check(server_id: int):
    return {"ok": True, **_wrap(nodes.gre_check, server_id)}


@router.post("/servers/{server_id}/filter-check")
def filter_check(server_id: int):
    return {"ok": True, **_wrap(nodes.filter_check, server_id)}
