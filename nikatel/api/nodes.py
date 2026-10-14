from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import dcs, nodes
from ..ssh import SSHError

router = APIRouter(tags=["nodes"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))


@router.get("/datacentres")
def datacentres():
    """The providers our servers are hosted at: servers and tunnels there, and whether the provider's website answers."""
    return {"ok": True, **dcs.fetch()}


@router.get("/nodes/presets")
def presets():
    """The console presets (named read-only commands) a server can be asked to run."""
    return {"ok": True, "presets": [{"key": k, "label": v[0], "command": v[1]} for k, v in nodes.PRESETS.items()]}


class Cmd(BaseModel):
    command: str = Field(min_length=1, max_length=4000)


@router.post("/servers/{server_id}/console")
def console(server_id: int, body: Cmd):
    """Run one console preset on a server and return its output (read only)."""
    return {"ok": True, **_wrap(nodes.console, server_id, body.command)}


class Pw(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class NodesIn(BaseModel):
    marzban_cert: str = Field("", max_length=8000)


@router.get("/settings/nodes")
def nodes_get():
    """The settings of Add server: the Marzban node certificate filled in by default."""
    from ..services import settings as st

    return {"ok": True, **st.get("nodes")}


@router.put("/settings/nodes")
def nodes_put(body: NodesIn):
    """The certificate that Add server → Marzban node fills in by default (empty = none)."""
    from ..services import extras
    from ..services import settings as st

    try:
        cert = extras.check_cert(body.marzban_cert) if body.marzban_cert.strip() else ""
    except ValueError as e:
        raise HTTPException(400, str(e))

    st.put("nodes", {"marzban_cert": cert})
    return {"ok": True, **st.get("nodes")}


class InstallIn(BaseModel):
    items: list[str] = Field(min_length=1, max_length=4)
    cert: str = Field("", max_length=8000)      # the Marzban panel's certificate, or - for a PasarGuard node - the API key the PasarGuard panel gives


@router.get("/servers/{server_id}/pasarguard-cert")
def pasarguard_cert(server_id: int):
    """The public certificate of the PasarGuard node installed on this server (to set it in the PasarGuard panel)."""
    from ..services import extras

    try:
        return {"ok": True, **extras.pasarguard_cert(server_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/servers/{server_id}/install")
def install_extras(server_id: int, body: InstallIn):
    """Install on a server: the torrent block, a Marzban node, a PasarGuard node (with its API key) or the update of the system packages."""
    from ..services import extras

    try:
        return {"ok": True, "id": extras.start(server_id, body.items, body.cert)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class NewPw(BaseModel):
    password: str = Field(min_length=8, max_length=128)


@router.post("/servers/{server_id}/password")
def change_password(server_id: int, body: NewPw):
    """Change the SSH login password of this server (Tools → Change SSH password)."""
    return {"ok": True, **_wrap(nodes.set_password, server_id, body.password)}


@router.post("/servers/{server_id}/connect")
def connect(server_id: int, body: Pw):
    """Install the panel's SSH key on a server with its password (the password is used once and never kept)."""
    return {"ok": True, **_wrap(nodes.connect, server_id, body.password)}


class Many(BaseModel):
    role: str
    lines: str = Field(max_length=20000)
    password: str = ""
    ssh_user: str = "root"
    ssh_port: int = 22
    tag: str = Field("", max_length=40)


@router.post("/servers/add-many")
def add_many(body: Many):
    """Add several servers from pasted lines (name, address, password), installing the key on each; they are listed on the Lab page under `tag`."""
    from ..services import profiles

    try:
        profiles.check_tag(body.tag)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    return {"ok": True, "items": _wrap(nodes.add_many, body.role, body.lines, body.password, body.ssh_user, body.ssh_port, body.tag)}


@router.get("/servers/{server_id}/launcher")
def launcher(server_id: int, app: str = "ssh"):
    """A launcher file for ssh, PuTTY or MobaXterm that opens this server with the operator's own key."""
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
    """Install, update or remove the Abuse-Defender rules (torrent and abuse traffic blocked) on a server."""
    return {"ok": True, **_wrap(nodes.abuse, server_id, body.action)}


@router.post("/servers/{server_id}/reboot")
def reboot(server_id: int):
    """Reboot a server (its tunnels come back with it)."""
    _wrap(nodes.reboot, server_id)
    return {"ok": True}


@router.post("/servers/geo-refresh")
def geo_refresh(only_missing: bool = False):
    """Look the country and the hosting provider of the servers up again (all, or only the ones without)."""
    return {"ok": True, "changes": _wrap(nodes.geo_refresh, only_missing)}


@router.post("/servers/gre-check-all")
def gre_check_all(role: str = ""):
    """Check the GRE links of every server (or of one role) now."""
    return {"ok": True, **_wrap(nodes.gre_check_all, role)}


@router.post("/servers/{server_id}/gre-check")
def gre_check(server_id: int):
    """Check the GRE links of one server now."""
    return {"ok": True, **_wrap(nodes.gre_check, server_id)}


@router.post("/servers/{server_id}/gre-probe")
def gre_probe(server_id: int):
    """Does GRE work to this server: an Iran server builds a temporary GRE link to it, pings the inner address and removes the link (up to a minute)."""
    return {"ok": True, **_wrap(nodes.gre_probe, server_id)}


@router.post("/servers/{server_id}/filter-check")
def filter_check(server_id: int):
    """Is this server's address filtered inside Iran right now (asked from an Iran server)."""
    return {"ok": True, **_wrap(nodes.filter_check, server_id)}
