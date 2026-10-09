import subprocess
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, ServerSample, Tunnel
from ..services.events import emit
from ..services.facts import sample_server

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

    model_config = {"from_attributes": True}


class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    host: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")
    role: str = Field(pattern=r"^(iran|foreign)$")
    ssh_user: str = Field("root", min_length=1, max_length=32, pattern=r"^[a-z_][a-z0-9_-]*$")
    ssh_port: int = Field(22, ge=1, le=65535)
    enabled: bool = True
    provider: str = Field("", max_length=128)


@router.get("/servers", response_model=list[ServerOut])
def list_servers(role: str | None = None):
    with session() as db:
        q = select(Server).order_by(Server.role, Server.name)

        if role:
            q = q.where(Server.role == role)

        return [ServerOut.model_validate(s) for s in db.scalars(q)]


@router.get("/servers/{server_id}", response_model=ServerOut)
def get_server(server_id: int):
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise HTTPException(404, "server not found")

        return ServerOut.model_validate(s)


@router.get("/servers/{server_id}/samples")
def samples(server_id: int, hours: int = 24):
    with session() as db:
        rows = db.scalars(select(ServerSample).where(ServerSample.server_id == server_id,
                                                     ServerSample.ts >= int(time.time()) - hours * 3600).order_by(ServerSample.ts))
        return {"ok": True, "columns": ["ts", "cpu", "ram", "disk", "load1", "rx_mbps", "tx_mbps", "conn"],
                "samples": [[r.ts, r.cpu, r.ram, r.disk, r.load1, r.rx_mbps, r.tx_mbps, r.conn] for r in rows]}


@router.post("/servers", response_model=ServerOut, status_code=201)
def add_server(body: ServerIn):
    with session() as db:
        if db.scalar(select(Server).where((Server.name == body.name) | (Server.host == body.host))):
            raise HTTPException(409, "a server with this name or host exists")

        s = Server(**body.model_dump())
        db.add(s)
        db.flush()
        emit(db, "info", "servers", f"Server {s.name} ({s.role}) added.", s.name)
        return ServerOut.model_validate(s)


@router.put("/servers/{server_id}", response_model=ServerOut)
def update_server(server_id: int, body: ServerIn):
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise HTTPException(404, "server not found")

        if db.scalar(select(Server).where(((Server.name == body.name) | (Server.host == body.host)) & (Server.id != server_id))):
            raise HTTPException(409, "another server has this name or host")

        was_enabled = s.enabled

        for k, v in body.model_dump().items():
            setattr(s, k, v)

        if was_enabled and not s.enabled:
            emit(db, "info", "servers", f"Server {s.name} disabled: it is no longer checked.", s.name)
        elif not was_enabled and s.enabled:
            emit(db, "info", "servers", f"Server {s.name} enabled again.", s.name)

        db.flush()
        return ServerOut.model_validate(s)


@router.delete("/servers/{server_id}")
def delete_server(server_id: int):
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
