import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Tunnel, TunnelCheck
from ..services.tunnels import check_tunnel, plan_for

router = APIRouter(tags=["tunnels"], dependencies=[Depends(current_user)])


class TunnelOut(BaseModel):
    id: int
    name: str
    type: str
    transport: str
    engine: str
    iran_name: str
    iran_host: str
    foreign_name: str
    foreign_host: str
    port: int
    iran_tunnel_ip: str
    foreign_tunnel_ip: str
    forwarded_ports: list
    enabled: bool
    created_at: int
    state: str
    state_since: int
    last_check_at: int
    last_error: str
    rtt_ms: float | None
    rx_mbps: float | None
    tx_mbps: float | None
    detail: dict
    units: dict


def _out(t: Tunnel) -> TunnelOut:
    plan = plan_for(t)
    return TunnelOut(id=t.id, name=t.name, type=t.type, transport=t.transport, engine=t.engine,
                     iran_name=t.iran.name, iran_host=t.iran.host, foreign_name=t.foreign.name, foreign_host=t.foreign.host,
                     port=t.port, iran_tunnel_ip=t.iran_tunnel_ip, foreign_tunnel_ip=t.foreign_tunnel_ip,
                     forwarded_ports=t.forwarded_ports or [], enabled=t.enabled, created_at=t.created_at,
                     state=t.state, state_since=t.state_since, last_check_at=t.last_check_at, last_error=t.last_error,
                     rtt_ms=t.rtt_ms, rx_mbps=t.rx_mbps, tx_mbps=t.tx_mbps, detail=t.detail or {},
                     units={"iran": plan.iran_units, "foreign": plan.foreign_units, "iface": plan.iface})


@router.get("/tunnels", response_model=list[TunnelOut])
def list_tunnels():
    with session() as db:
        return [_out(t) for t in db.scalars(select(Tunnel).order_by(Tunnel.name))]


@router.get("/tunnels/{tunnel_id}", response_model=TunnelOut)
def get_tunnel(tunnel_id: int):
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise HTTPException(404, "tunnel not found")

        return _out(t)


@router.get("/tunnels/{tunnel_id}/checks")
def checks(tunnel_id: int, hours: int = 24):
    with session() as db:
        rows = db.scalars(select(TunnelCheck).where(TunnelCheck.tunnel_id == tunnel_id,
                                                    TunnelCheck.ts >= int(time.time()) - hours * 3600).order_by(TunnelCheck.ts))
        return {"ok": True, "columns": ["ts", "ok", "rtt_ms", "rx_mbps", "tx_mbps", "error"],
                "checks": [[r.ts, r.ok, r.rtt_ms, r.rx_mbps, r.tx_mbps, r.error] for r in rows]}


@router.post("/tunnels/{tunnel_id}/check", response_model=TunnelOut)
def check_now(tunnel_id: int):
    check_tunnel(tunnel_id)
    return get_tunnel(tunnel_id)
