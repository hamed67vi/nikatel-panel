import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Tunnel, TunnelCheck
from ..services import builder
from ..services.tunnels import check_tunnel, plan_for

router = APIRouter(tags=["tunnels"], dependencies=[Depends(current_user)])


class BuildIn(BaseModel):
    iran_id: int
    foreign_id: int
    ports: str = Field(min_length=1, max_length=300)
    kind: str = Field("backhaul", pattern=r"^(backhaul|backpack|backpack_direct|paqet|chisel)$")
    transport: str = Field("tcpmux", pattern=r"^[a-z0-9:]{0,16}$")
    mtu: int = Field(0, ge=0, le=1476)
    keepalive: int = Field(75, ge=10, le=600)
    opts: dict = Field(default_factory=dict)


@router.post("/tunnels", status_code=202)
def build(body: BuildIn):
    try:
        if body.kind == "backhaul":
            if body.transport not in ("tcp", "tcpmux", "wsmux", "wssmux"):
                raise ValueError("transport must be tcp, tcpmux, wsmux or wssmux")

            aid = builder.start_build(body.iran_id, body.foreign_id, body.ports, body.transport, body.mtu, body.keepalive)
        else:
            from ..services import build_other

            opts = {k: v for k, v in body.opts.items() if k in ("preset", "accept_udp", "sni_domain", "mtu", "kcp_mode", "conn", "keepalive", "heartbeat") and isinstance(v, (str, int, bool)) and len(str(v)) <= 80}
            aid = build_other.start_build(body.iran_id, body.foreign_id, body.ports, body.kind, body.transport, opts, measure="full" if body.opts.get("measure") != "latency" else "latency")
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True, "id": aid}


@router.get("/tunnels/kinds")
def tunnel_kinds():
    """What Create tunnel can build: the engines, their transports / carriers and the options each takes."""
    from ..services import build_other as bo

    return {"ok": True, "kinds": [
        {"key": "backhaul", "title": "Backhaul over GRE", "note": "GRE link with local IPs, backhaul on top - like the existing tunnels", "direction": "reverse",
         "transports": [{"key": k, "label": lbl, "note": n} for k, lbl, n in (("tcpmux", "TCP Mux", "many users, few connections (recommended)"), ("tcp", "TCP", "plain TCP"), ("wsmux", "WebSocket Mux", "clear-text WebSocket, multiplexed"), ("wssmux", "WebSocket TLS Mux", "TLS WebSocket, multiplexed"))]},
        {"key": "backpack", "title": "BackPack", "note": "the foreign server dials the Iran server; twelve transports, presets, UDP", "direction": "reverse",
         "transports": [{"key": k, "label": v[0], "note": v[1] + (" · root" if v[2] else ""), "udp": v[3]} for k, v in bo.BP_TRANSPORTS.items()]},
        {"key": "backpack_direct", "title": "BackPack Direct", "note": "an L3 link the Iran server dials: UDP / QUIC / PCK / SNI / xDi carriers", "direction": "direct",
         "transports": [{"key": k, "label": v[0], "note": v[1] + (" · root" if v[2] else ""), "udp": v[3]} for k, v in bo.BP_DIRECT.items()]},
        {"key": "paqet", "title": "Paqet", "note": "KCP over raw sockets: the foreign server listens, the Iran server opens the ports", "direction": "direct",
         "transports": [{"key": "kcp", "label": "KCP", "note": "modes normal / fast / fast2 / fast3"}]},
        {"key": "chisel", "title": "Chisel", "note": "reverse tunnel over one WebSocket port on the Iran server", "direction": "reverse",
         "transports": [{"key": "ws", "label": "HTTP WebSocket", "note": "clear text"}, {"key": "wss", "label": "TLS WebSocket", "note": "self-signed certificate, key pinned"}]},
    ]}


class TestIn(BaseModel):
    iran_id: int
    foreign_id: int
    transport: str = Field("tcpmux", pattern=r"^(tcp|tcpmux|wsmux|wssmux)$")


class PortsIn(BaseModel):
    ports: str = Field(min_length=1, max_length=300)


@router.put("/tunnels/{tunnel_id}/ports")
def set_ports(tunnel_id: int, body: PortsIn):
    from ..ssh import SSHError

    try:
        return {"ok": True, **builder.set_ports(tunnel_id, body.ports)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))


@router.get("/tunnels/detect")
def detect():
    from ..services import detect as dt

    return {"ok": True, **dt.scan()}


@router.post("/tunnels/adopt")
def adopt(body: dict):
    from ..services import detect as dt

    try:
        return {"ok": True, "name": dt.adopt(body)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class AutoIn(BaseModel):
    foreign_id: int
    ports: str = ""


@router.post("/tunnels/auto-iran")
def auto_iran(body: AutoIn):
    from ..services import detect as dt

    try:
        return {"ok": True, "ranking": dt.auto_iran(body.foreign_id, body.ports)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/tunnels/{tunnel_id}/quality")
def quality(tunnel_id: int):
    from ..services import tunnel_test as tt
    from ..ssh import SSHError

    try:
        return {"ok": True, **tt.quality(tunnel_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except SSHError as e:
        raise HTTPException(502, str(e))


@router.post("/tunnels/quality-all")
def quality_all():
    from ..services import tunnel_test as tt

    return {"ok": True, "results": tt.quality_all()}


class CompareIn(BaseModel):
    iran_id: int
    foreign_id: int
    kinds: list[str] = Field(default_factory=lambda: ["tcpmux"])


@router.get("/tunnel-test/kinds")
def test_kinds():
    from ..services import tunnel_test as tt

    return {"ok": True, "kinds": [{"key": k, "label": lbl, "note": note, "group": grp, "default": dflt} for k, lbl, note, grp, dflt in tt.KINDS]}


@router.post("/tunnel-test/compare", status_code=202)
def compare_start(body: CompareIn):
    from ..services import tunnel_test as tt

    try:
        return {"ok": True, "id": tt.compare_start(body.iran_id, body.foreign_id, body.kinds)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.get("/tunnel-test/compare/{jid}")
def compare_status(jid: str):
    from ..services import tunnel_test as tt

    try:
        return {"ok": True, **tt.compare_status(jid)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/tunnel-test/compare/{jid}/stop")
def compare_stop(jid: str):
    from ..services import tunnel_test as tt

    try:
        tt.compare_stop(jid)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {"ok": True}


@router.post("/tunnel-test", status_code=202)
def tunnel_test(body: TestIn):
    from ..services import tunnel_test as tt

    try:
        return {"ok": True, "id": tt.start(body.iran_id, body.foreign_id, body.transport)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/tunnels/{tunnel_id}")
def delete(tunnel_id: int):
    try:
        return {"ok": True, "results": builder.delete_tunnel(tunnel_id)}
    except ValueError as e:
        raise HTTPException(400 if "read-only" in str(e) else 404, str(e))


@router.post("/tunnels/{tunnel_id}/rebuild", status_code=202)
def rebuild(tunnel_id: int):
    try:
        return {"ok": True, "id": builder.rebuild_tunnel(tunnel_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


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
    quality: dict = {}
    record: dict = {}       # port mapping, direction, test door, engine options - never secrets


def _out(t: Tunnel) -> TunnelOut:
    plan = plan_for(t)
    return TunnelOut(id=t.id, name=t.name, type=t.type, transport=t.transport, engine=t.engine,
                     iran_name=t.iran.name, iran_host=t.iran.host, foreign_name=t.foreign.name, foreign_host=t.foreign.host,
                     port=t.port, iran_tunnel_ip=t.iran_tunnel_ip, foreign_tunnel_ip=t.foreign_tunnel_ip,
                     forwarded_ports=t.forwarded_ports or [], enabled=t.enabled, created_at=t.created_at,
                     state=t.state, state_since=t.state_since, last_check_at=t.last_check_at, last_error=t.last_error,
                     rtt_ms=t.rtt_ms, rx_mbps=t.rx_mbps, tx_mbps=t.tx_mbps, detail=t.detail or {}, quality=(t.record or {}).get("quality") or {},
                     record={k: v for k, v in (t.record or {}).items() if k != "quality"},
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
