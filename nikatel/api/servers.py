import subprocess
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, ServerSample, Tunnel
from ..services.events import emit
from ..services.facts import sample_server
from ..services.nodes import server_conflict

log = get_logger("servers")
router = APIRouter(tags=["servers"], dependencies=[Depends(current_user)])


class ServerOut(BaseModel):
    id: int
    name: str
    host: str
    role: str
    ssh_user: str
    ssh_port: int
    enabled: bool
    provider: str
    asn: str
    country: str
    online: bool | None
    last_seen_at: int
    last_error: str
    facts: dict
    extra: dict = {}
    created_at: int = 0          # when the server was added: the list marks the ones added in the last hour as "new"

    model_config = {"from_attributes": True}


class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    host: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")
    role: str = Field("", pattern=r"^(iran|foreign)?$")        # empty = found from the address (Iran or foreign) when the server is added; an edit keeps the role
    ssh_user: str = Field("root", min_length=1, max_length=32, pattern=r"^[a-z_][a-z0-9_-]*$")
    ssh_port: int = Field(22, ge=1, le=65535)
    enabled: bool = True
    provider: str = Field("", max_length=128)


@router.get("/servers", response_model=list[ServerOut])
def list_servers(role: str | None = None):
    """Every server with its live facts (cpu, ram, disk, traffic) and extra (geo, network, underuse); `role` filters."""
    with session() as db:
        q = select(Server).order_by(Server.role, Server.name)

        if role:
            q = q.where(Server.role == role)

        return [ServerOut.model_validate(s) for s in db.scalars(q)]


@router.get("/servers/panel-nodes")
def server_panel_nodes():
    """For every server the nodes of the panels (Marzban and PasarGuard) that run on it, and `outside`: the nodes that run on no server of ours (with `hint`); as the health job saw them, no call to a panel."""
    from ..services import pg_nodes

    on, out = pg_nodes.placement()
    return {"ok": True, "servers": {str(k): v for k, v in on.items()}, "outside": out}


@router.get("/servers/where")
def server_where(host: str = Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")):
    """Where a server is before it is added: its country by the geo services, and the role that follows (iran / foreign); empty when no service answers."""
    from ..services import nodes

    return {"ok": True, **nodes.where(host)}


@router.get("/servers/{server_id}", response_model=ServerOut)
def get_server(server_id: int):
    """One server in full."""
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise HTTPException(404, "server not found")

        return ServerOut.model_validate(s)


@router.get("/servers/{server_id}/samples")
def samples(server_id: int, hours: int = 24):
    """The samples of a server over the last hours (cpu, ram, disk, load, traffic, connections)."""
    with session() as db:
        rows = db.scalars(select(ServerSample).where(ServerSample.server_id == server_id,
                                                     ServerSample.ts >= int(time.time()) - hours * 3600).order_by(ServerSample.ts))
        return {"ok": True, "columns": ["ts", "cpu", "ram", "disk", "load1", "rx_mbps", "tx_mbps", "conn"],
                "samples": [[r.ts, r.cpu, r.ram, r.disk, r.load1, r.rx_mbps, r.tx_mbps, r.conn] for r in rows]}


@router.post("/servers", response_model=ServerOut, status_code=201)
def add_server(body: ServerIn):
    """Add a server (name, address, role, SSH user and port); it is sampled at the next round."""
    with session() as db:
        why = server_conflict(db, body.name, body.host)

        if why:
            raise HTTPException(409, why[0].upper() + why[1:])

        data = body.model_dump()

        if not data["role"]:
            from ..services import nodes

            w = nodes.where(body.host)

            if not w["role"]:
                raise HTTPException(400, "could not tell where this server is (no geo service answered): choose Iran or foreign and add it again")

            data["role"] = w["role"]

            if w["country_code"]:
                data["country"] = w["country_code"]

        s = Server(**data)
        db.add(s)
        db.flush()
        emit(db, "info", "servers", f"Server {s.name} ({s.role}) added.", s.name)
        return ServerOut.model_validate(s)


@router.put("/servers/{server_id}", response_model=ServerOut)
def update_server(server_id: int, body: ServerIn):
    """Change a server; a new address rebuilds its tunnels and moves its DNS records."""
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise HTTPException(404, "server not found")

        why = server_conflict(db, body.name, body.host, exclude_id=server_id)

        if why:
            raise HTTPException(409, why[0].upper() + why[1:])

        was_enabled, old_name, old_host = s.enabled, s.name, s.host

        data = body.model_dump()

        if not data["role"]:
            data["role"] = s.role                    # an edit without a role keeps the one the server has

        for k, v in data.items():
            setattr(s, k, v)

        if s.name != old_name:                       # the tunnels that carry the name the panel made for them follow the new server name
            from ..services import builder

            db.flush()

            for line in builder.follow_server_rename(db, s, old_name):
                emit(db, "info", "servers", f"Tunnel name follows the server: {line}.", s.name)

        if was_enabled and not s.enabled:
            emit(db, "info", "servers", f"Server {s.name} disabled: it is no longer checked.", s.name)
        elif not was_enabled and s.enabled:
            emit(db, "info", "servers", f"Server {s.name} enabled again.", s.name)

        db.flush()
        out = ServerOut.model_validate(s)

    if body.host != old_host:                        # a new public address: the tunnels (their GRE links name the address) follow, and the DNS of an Iran server
        from ..services import builder

        builder.follow_server_host(server_id, old_host, body.host)

    return out


@router.delete("/servers/{server_id}")
def delete_server(server_id: int):
    """Delete a server from the inventory (nothing changes on the server; refused while tunnels use it)."""
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise HTTPException(404, "server not found")

        used = db.scalar(select(Tunnel).where((Tunnel.iran_server_id == server_id) | (Tunnel.foreign_server_id == server_id)))

        if used:
            raise HTTPException(409, f"the tunnel {used.name} uses this server; disable the server instead of deleting it")

        emit(db, "warn", "servers", f"Server {s.name} removed from the panel (nothing was changed on the server).", s.name)
        db.delete(s)
        return {"ok": True}


@router.post("/servers/{server_id}/check", response_model=ServerOut)
def check_now(server_id: int):
    """Sample a server now (over SSH, a few seconds) and return it."""
    sample_server(server_id)
    return get_server(server_id)


@router.post("/servers/{server_id}/forget-hostkey")
def forget_hostkey(server_id: int):
    """After a server is reinstalled its SSH identity changes; this drops the pinned key so the next
    connection learns the new one. It touches only the panel's own known_hosts file."""
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise HTTPException(404, "server not found")

        host, name, port = s.host, s.name, s.ssh_port

    kh = get_settings().known_hosts

    if kh.exists():
        for target in {host, f"[{host}]:{port}"}:
            subprocess.run(["ssh-keygen", "-R", target, "-f", str(kh)], capture_output=True, check=False)

    with session() as db:
        emit(db, "info", "servers", f"Forgot the SSH host key of {name}; it is learnt again on the next check.", name)

    log.info("host key forgotten name=%s", name)
    return {"ok": True}
