import re
import time

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Tunnel, TunnelCheck, is_temp
from ..services import builder
from ..services.tunnels import check_tunnel, plan_for

router = APIRouter(tags=["tunnels"], dependencies=[Depends(current_user)])


class BuildIn(BaseModel):
    iran_id: int = Field(ge=0)                                  # 0 = the smart choice: the Iran server that ranks best for this foreign server
    foreign_id: int
    ports: str = Field(min_length=1, max_length=300)
    kind: str = Field("backhaul", pattern=r"^(backhaul|backpack|backpack_direct|paqet|chisel)$")
    transport: str = Field("tcpmux", pattern=r"^[a-z0-9:]{0,16}$")
    mtu: int = Field(0, ge=0, le=1476)
    keepalive: int = Field(75, ge=10, le=600)
    nodelay: bool = True
    opts: dict = Field(default_factory=dict)
    replace_units: list[str] = Field(default_factory=list, max_length=5)   # dead Iran-side backhaul units that hold a wanted port ("backhaul-iran-tcpmux-9921.service"): removed first
    replace_ids: list[int] = Field(default_factory=list, max_length=5)   # tunnels (same foreign server and port) removed before the build
    dns_follow: bool = False                                     # the CDN records that served only a replaced tunnel follow the new Iran server
    dns_names: list[str] = Field(default_factory=list, max_length=10)    # DNS names (A records in the ArvanCloud account) pointed at the new Iran server after the build


@router.post("/tunnels", status_code=202)
def build(body: BuildIn):
    """Build a tunnel (any engine) as an action; the answer is its id. 0 as the Iran server = the best one is chosen."""
    try:
        transport = body.transport or ("tcpmux" if body.kind == "backhaul" else "")     # an empty transport means the default of the engine

        if body.kind == "backhaul" and transport not in ("tcp", "tcpmux", "wsmux", "wssmux"):
            raise ValueError("transport must be tcp, tcpmux, wsmux or wssmux")

        other_opts = {k: v for k, v in body.opts.items() if k in ("preset", "accept_udp", "sni_domain", "mtu", "kcp_mode", "conn", "keepalive", "heartbeat", "nodelay", "connection_pool", "mux_con", "channel_size", "rcvwnd", "sndwnd", "smuxbuf", "streambuf", "kcp_block") and isinstance(v, (str, int, bool)) and len(str(v)) <= 80}
        measure = "full" if body.opts.get("measure") != "latency" else "latency"

        names = [n.strip().lower() for n in body.dns_names if n.strip()]

        if any(len(n) > 253 or not re.fullmatch(r"[a-z0-9.*-]+", n) for n in names):
            raise ValueError("a DNS name may only have letters, digits, dots and dashes")

        if body.iran_id == 0 or body.replace_ids or body.replace_units or body.dns_follow or names:
            from ..services import create

            aid = create.start(body.iran_id, body.foreign_id, body.ports, body.kind, transport, body.opts if body.kind == "backhaul" else other_opts,
                               body.replace_ids, body.dns_follow, body.mtu, body.keepalive, body.nodelay, measure, dns_names=names, replace_units=body.replace_units)
        elif body.kind == "backhaul":
            aid = builder.start_build(body.iran_id, body.foreign_id, body.ports, transport, body.mtu, body.keepalive, nodelay=body.nodelay, opts=body.opts)
        else:
            from ..services import build_other

            aid = build_other.start_build(body.iran_id, body.foreign_id, body.ports, body.kind, transport, other_opts, measure=measure)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True, "id": aid}


class PrecheckIn(BaseModel):
    iran_id: int = Field(0, ge=0)
    foreign_id: int
    ports: str = Field(min_length=1, max_length=300)
    live: bool = False                                           # also look at the Iran server itself (one SSH call)


class SmartTestIn(BaseModel):
    foreign_id: int
    ports: str = Field(min_length=1, max_length=300)
    kind: str = Field("tcpmux", pattern=r"^[a-z0-9:]{1,16}$")


@router.post("/tunnels/smart-test", status_code=202)
def smart_test(body: SmartTestIn):
    """Test the Iran servers one after the other (best first) with the chosen tunnel type; the first that scores well wins."""
    from ..services import create

    try:
        return {"ok": True, "id": create.smart_test(body.foreign_id, body.ports, body.kind)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/tunnels/precheck")
def precheck(body: PrecheckIn):
    """Before a build: ports already used on the Iran server, and tunnels that already carry these ports to the foreign server."""
    from ..services import create

    try:
        return {"ok": True, **create.precheck(body.iran_id, body.foreign_id, body.ports, body.live)}
    except ValueError as e:
        raise HTTPException(400, str(e))


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
        {"key": "chisel", "title": "Chisel", "note": "one WebSocket port: on the Iran server (reverse, the foreign server dials) or on the foreign server (forward, the Iran server dials)", "direction": "reverse",
         "transports": [{"key": "ws", "label": "HTTP WebSocket", "note": "reverse, clear text"}, {"key": "wss", "label": "TLS WebSocket", "note": "reverse, self-signed certificate, key pinned"},
                        {"key": "fws", "label": "Forward HTTP WebSocket", "note": "the Iran server dials: the customers' downloads run at full speed"}, {"key": "fwss", "label": "Forward TLS WebSocket", "note": "the Iran server dials, TLS, key pinned"}]},
    ]}


class TestIn(BaseModel):
    iran_id: int
    foreign_id: int
    transport: str = Field("tcpmux", pattern=r"^(tcp|tcpmux|wsmux|wssmux)$")


class PortsIn(BaseModel):
    ports: str = Field(min_length=1, max_length=300)


@router.put("/tunnels/{tunnel_id}/ports")
def set_ports(tunnel_id: int, body: PortsIn):
    """Change the forwarded ports of a tunnel on both servers (the units are rewritten and restarted)."""
    from ..ssh import SSHError

    try:
        return {"ok": True, **builder.set_ports(tunnel_id, body.ports)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


@router.put("/tunnels/{tunnel_id}/name")
def rename(tunnel_id: int, body: RenameIn):
    """Rename a tunnel (what the operator sees); on the servers it keeps the name it was built with."""
    try:
        return {"ok": True, "name": builder.rename_tunnel(tunnel_id, body.name)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class AutoIn(BaseModel):
    foreign_id: int
    ports: str = ""


@router.post("/tunnels/auto-iran")
def auto_iran(body: AutoIn):
    """Rank the Iran servers for a foreign server and say which one to build on."""
    from ..services import detect as dt

    try:
        return {"ok": True, "ranking": dt.auto_iran(body.foreign_id, body.ports)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/tunnels/{tunnel_id}/quality")
def quality(tunnel_id: int):
    """Measure one tunnel: 30 pings over the link for latency, jitter, loss and a score (about 10 s)."""
    from ..services import tunnel_test as tt
    from ..ssh import SSHError

    try:
        return {"ok": True, **tt.quality(tunnel_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except SSHError as e:
        raise HTTPException(502, str(e))


class SpeedIn(BaseModel):
    mode: str = "customer"


@router.get("/tunnels/{tunnel_id}/speed")
def speed_options(tunnel_id: int):
    """What can be measured on this tunnel (the speed a customer gets through a config, or the tunnel alone through its test door), and the last result set beside what the tunnel can carry."""
    from ..services import tunnel_speed

    try:
        return tunnel_speed.options(tunnel_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/tunnels/{tunnel_id}/speed", status_code=202)
def speed_start(tunnel_id: int, body: SpeedIn):
    """Measure the speed a customer in Iran gets, as an action (follow /api/actions/{action_id}; the result is kept, see GET /tunnels/{id}/speed).
    mode customer: the probe downloads and uploads through a config of this tunnel, one connection like a phone; mode door: one and four connections through the test door of the tunnel."""
    from ..services import tunnel_speed

    try:
        return {"ok": True, "action_id": tunnel_speed.start(tunnel_id, body.mode)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/tunnels/{tunnel_id}/bandwidth")
def bandwidth_get(tunnel_id: int):
    """The bandwidth of a tunnel, kept by the Doctor: the last number (Mb/s down), the usual level, the verdict (learning / ok / low / drop), the drop with its explanation and the history (60 samples)."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise HTTPException(404, "tunnel not found")

        return {"ok": True, "name": t.name, **((t.record or {}).get("bw") or {})}


@router.post("/tunnels/{tunnel_id}/bandwidth", status_code=202)
def bandwidth_measure(tunnel_id: int):
    """Measure the bandwidth of this tunnel now, as an action (follow /api/actions/{action_id}): a GRE tunnel with two light connections (120 MB at most) over its link, a tunnel with a test door with its quality test. A drop is looked at by the Doctor in the same story."""
    from ..services import bandwidth

    try:
        return {"ok": True, "action_id": bandwidth.measure_start(tunnel_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/tunnels/retune", status_code=202)
def retune():
    """Apply the newer Backhaul defaults to the tunnels that exist - one at a time, at quiet moments, each checked and rolled back if worse."""
    from ..services import retune as rt

    try:
        return {"ok": True, "id": rt.start()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/tunnels/full-check")
def full_check():
    """Check every tunnel (up / down, data path) and measure its packet loss; display only."""
    from ..services import tunnel_test as tt

    return {"ok": True, **tt.full_check()}


@router.post("/tunnels/quality-all", status_code=202)
def quality_all():
    """Measure the quality of every tunnel (10 s of pings each), as an action: follow /api/actions/{action_id}. Read only."""
    from ..services import tunnel_test as tt

    return {"ok": True, "action_id": tt.quality_all_start()}


class CompareIn(BaseModel):
    iran_id: int
    foreign_id: int
    kinds: list[str] = Field(default_factory=lambda: ["tcpmux"])


@router.get("/tunnel-test/kinds")
def test_kinds():
    """Every tunnel kind the pair test can build, grouped by engine."""
    from ..services import tunnel_test as tt

    return {"ok": True, "kinds": [{"key": k, "label": lbl, "note": note, "group": grp, "default": dflt} for k, lbl, note, grp, dflt in tt.KINDS]}


@router.post("/tunnel-test/compare", status_code=202)
def compare_start(body: CompareIn):
    """Build the chosen kinds between two servers one after the other, measure each and compare; follow /tunnel-test/compare/{jid}."""
    from ..services import tunnel_test as tt

    try:
        return {"ok": True, "id": tt.compare_start(body.iran_id, body.foreign_id, body.kinds)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.get("/tunnel-test/compare/{jid}")
def compare_status(jid: str):
    """How far a comparison is, and the results so far."""
    from ..services import tunnel_test as tt

    try:
        return {"ok": True, **tt.compare_status(jid)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/tunnels/builds/{action_id}/stop")
def build_stop(action_id: int):
    """Stop a build that is running: the next step of the build ends it and everything it made so far is rolled back."""
    from ..services import actions, builder

    s = actions.status(action_id)

    if s is None or s.get("done") or s.get("action") not in ("build", "create"):
        raise HTTPException(404, "no such build is running")

    builder.CANCEL.add(action_id)
    return {"ok": True}


@router.post("/tunnel-test/compare/{jid}/stop")
def compare_stop(jid: str):
    """Stop a comparison; the temporary tunnel is removed."""
    from ..services import tunnel_test as tt

    try:
        tt.compare_stop(jid)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {"ok": True}


@router.post("/tunnel-test", status_code=202)
def tunnel_test(body: TestIn):
    """Build one temporary tunnel between two servers, measure it and remove it, as an action."""
    from ..services import tunnel_test as tt

    try:
        return {"ok": True, "id": tt.start(body.iran_id, body.foreign_id, body.transport)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/tunnels/{tunnel_id}")
def delete(tunnel_id: int):
    """Remove a tunnel from both servers; it goes to the trash, from where it can be put back."""
    try:
        return {"ok": True, "results": builder.delete_tunnel(tunnel_id, trash=True)}
    except ValueError as e:
        raise HTTPException(400 if "read-only" in str(e) else 404, str(e))


@router.post("/tunnels/{tunnel_id}/rebuild", status_code=202)
def rebuild(tunnel_id: int):
    """Rebuild a tunnel as an action (the old one comes back when the new build fails)."""
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
    bw: dict = {}           # the bandwidth the Doctor keeps (services/bandwidth.py) without its history; GET /tunnels/{id}/bandwidth has the history
    record: dict = {}       # port mapping, direction, test door, engine options - never secrets


def _out(t: Tunnel) -> TunnelOut:
    plan = plan_for(t)
    return TunnelOut(id=t.id, name=t.name, type=t.type, transport=t.transport, engine=t.engine,
                     iran_name=t.iran.name, iran_host=t.iran.host, foreign_name=t.foreign.name, foreign_host=t.foreign.host,
                     port=t.port, iran_tunnel_ip=t.iran_tunnel_ip, foreign_tunnel_ip=t.foreign_tunnel_ip,
                     forwarded_ports=t.forwarded_ports or [], enabled=t.enabled, created_at=t.created_at,
                     state=t.state, state_since=t.state_since, last_check_at=t.last_check_at, last_error=t.last_error,
                     rtt_ms=t.rtt_ms, rx_mbps=t.rx_mbps, tx_mbps=t.tx_mbps, detail=t.detail or {}, quality=(t.record or {}).get("quality") or {},
                     bw={k: v for k, v in ((t.record or {}).get("bw") or {}).items() if k != "hist"},
                     record={k: v for k, v in (t.record or {}).items() if k not in ("quality", "bw")},
                     units={"iran": plan.iran_units, "foreign": plan.foreign_units, "iface": plan.iface})


def rebuilding_rows(items: list[dict], servers: dict[int, tuple[str, str]], known: set[str], now: int) -> list[TunnelOut]:
    """Pure: a placeholder row for every rebuild in flight (the old tunnel is already removed, the new one not yet recorded), so the list never loses the tunnel
    for the minute of a rebuild. A negative id marks it: the UI shows "rebuilding…" and no buttons."""
    rows = []

    for n, it in enumerate(items):
        a = it.get("args") or {}
        name = str(it.get("name") or "")

        if not name or name in known or is_temp(name):
            continue

        iran, foreign = servers.get(int(a.get("iran_id") or 0), ("?", "")), servers.get(int(a.get("foreign_id") or 0), ("?", ""))
        ports = [int(x.split("=")[0].split(":")[0]) for x in str(a.get("ports_text") or "").replace(",", " ").split() if x.split("=")[0].split(":")[0].isdigit()]
        rows.append(TunnelOut(id=-(n + 1), name=name, type=str(a.get("kind") or "backhaul"), transport=str(a.get("transport") or ""), engine=str(a.get("kind") or "backhaul"),
                              iran_name=iran[0], iran_host=iran[1], foreign_name=foreign[0], foreign_host=foreign[1], port=0, iran_tunnel_ip="", foreign_tunnel_ip="",
                              forwarded_ports=ports, enabled=True, created_at=now, state="starting", state_since=now, last_check_at=0, last_error="rebuilding… (about a minute)",
                              rtt_ms=None, rx_mbps=None, tx_mbps=None, detail={}, units={}))

    return rows


@router.get("/tunnels", response_model=list[TunnelOut])
def list_tunnels():
    """Every tunnel with its live state, units, quality and the servers at both ends."""
    from ..models import Server
    from ..services import settings as st

    with session() as db:
        rows = [_out(t) for t in db.scalars(select(Tunnel).order_by(Tunnel.name)) if not is_temp(t.name)]      # a temporary test tunnel is not part of the list
        pending = st.get("pending_builds").get("items", [])

        if pending:
            servers = {s.id: (s.name, s.host) for s in db.scalars(select(Server))}
            rows += rebuilding_rows(pending, servers, {r.name for r in rows}, int(time.time()))

        return sorted(rows, key=lambda r: r.name)


@router.get("/tunnels/{tunnel_id}", response_model=TunnelOut)
def get_tunnel(tunnel_id: int):
    """One tunnel in full."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise HTTPException(404, "tunnel not found")

        return _out(t)


@router.get("/tunnels/{tunnel_id}/checks")
def checks(tunnel_id: int, hours: int = Query(24, ge=1, le=720)):
    """The checks of a tunnel over the last hours: ok, latency, throughput, reason."""
    with session() as db:
        rows = db.scalars(select(TunnelCheck).where(TunnelCheck.tunnel_id == tunnel_id,
                                                    TunnelCheck.ts >= int(time.time()) - hours * 3600).order_by(TunnelCheck.ts))
        return {"ok": True, "columns": ["ts", "ok", "rtt_ms", "rx_mbps", "tx_mbps", "error"],
                "checks": [[r.ts, r.ok, r.rtt_ms, r.rx_mbps, r.tx_mbps, r.error] for r in rows]}


@router.post("/tunnels/health-all", status_code=202)
def health_all():
    """Check every tunnel now, as an action: follow /api/actions/{action_id}; the last line of the story is the count of states. Read only."""
    from ..services import actions

    return {"ok": True, "action_id": actions.health_all_start()}


@router.post("/tunnels/restart-all", status_code=202)
def restart_all():
    """Restart every tunnel on both servers, four at a time, as an action (needs read-only off; 409 while one is already running)."""
    from ..services import actions

    try:
        return {"ok": True, "action_id": actions.restart_all_start()}
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/tunnels/{tunnel_id}/restart")
def restart_one(tunnel_id: int):
    """Restart the tunnel's units on both servers now and check it again (a few seconds)."""
    from ..services import actions

    try:
        return {"ok": True, **actions.restart_now(tunnel_id)}
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/tunnels/{tunnel_id}/check", response_model=TunnelOut)
def check_now(tunnel_id: int):
    """Check one tunnel now (both servers over SSH, about five seconds) and return it."""
    check_tunnel(tunnel_id)
    return get_tunnel(tunnel_id)

# ------------------------------------------------------------------ removed tunnels (restore)
@router.get("/tunnel-trash")
def trash_items():
    """Removed tunnels kept for a while with everything needed to put them back."""
    from ..services import trash

    return {"ok": True, "items": [{k: v for k, v in i.items() if k != "args"} for i in trash.items()]}


@router.post("/tunnel-trash/{item_id}/restore", status_code=202)
def trash_restore(item_id: str = Path(pattern=r"^[A-Za-z0-9._-]{1,90}$")):
    """Build a removed tunnel again from the trash, as an action."""
    from ..services import trash

    try:
        return {"ok": True, "id": trash.restore(item_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/tunnel-trash/{item_id}")
def trash_forget(item_id: str = Path(pattern=r"^[A-Za-z0-9._-]{1,90}$")):
    """Forget a removed tunnel for good."""
    from ..services import trash

    trash.forget(item_id)
    return {"ok": True}