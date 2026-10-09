
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Server, ServerSample
from ..services.facts import sample_server

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

    model_config = {"from_attributes": True}


class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    host: str = Field(min_length=1, max_length=128)
    role: str = Field(pattern=r"^(iran|foreign)$")
    ssh_user: str = "root"
    ssh_port: int = Field(22, ge=1, le=65535)
    enabled: bool = True


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
    import time

    with session() as db:
        rows = db.scalars(select(ServerSample).where(ServerSample.server_id == server_id,
                                                     ServerSample.ts >= int(time.time()) - hours * 3600).order_by(ServerSample.ts))
        return {"ok": True, "samples": [[r.ts, r.cpu, r.ram, r.disk, r.load1, r.rx_mbps, r.tx_mbps, r.conn] for r in rows],
                "columns": ["ts", "cpu", "ram", "disk", "load1", "rx_mbps", "tx_mbps", "conn"]}


@router.post("/servers", response_model=ServerOut, status_code=201)
def add_server(body: ServerIn):
    with session() as db:
        if db.scalar(select(Server).where((Server.name == body.name) | (Server.host == body.host))):
            raise HTTPException(409, "a server with this name or host exists")

        s = Server(**body.model_dump())
        db.add(s)
        db.flush()
        return ServerOut.model_validate(s)


@router.put("/servers/{server_id}", response_model=ServerOut)
def update_server(server_id: int, body: ServerIn):
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise HTTPException(404, "server not found")

        for k, v in body.model_dump().items():
            setattr(s, k, v)

        db.flush()
        return ServerOut.model_validate(s)


@router.post("/servers/{server_id}/check", response_model=ServerOut)
def check_now(server_id: int):
    sample_server(server_id)
    return get_server(server_id)
