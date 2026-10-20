import re
import time

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Tunnel, TunnelCheck, is_standby, is_temp
from ..services import analyst, builder, capacity, lanes, linkmodels, livebw, standby
from ..services.tunnels import check_tunnel, plan_for

router = APIRouter(tags=["tunnels"], dependencies=[Depends(current_user)])


class BuildIn(BaseModel):
    iran_id: int = Field(ge=0)                                  # 0 = the smart choice: the Iran server that ranks best for this foreign server
    foreign_id: int
    ports: str = Field(min_length=1, max_length=300)
    kind: str = Field("backhaul", pattern=r"^(auto|backhaul|backpack|backpack_direct|paqet|chisel)$")          # auto = "Best of the day": the panel picks the type that connects best between the two servers (knowledge.best_kind)
    transport: str = Field("tcpmux", pattern=r"^[a-z0-9:]{0,24}$")
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
    """Build a tunnel (any engine) as an action; the answer is its id. 0 as the Iran server = the best one is chosen. `kind: "auto"` (Best of the day) = the type that connects best between these two servers today is chosen;
    the answer says which (`kind`, `transport`, `why`)."""
    try:
        kind, why = body.kind, ""
        transport = body.transport or ("tcpmux" if kind == "backhaul" else "")     # an empty transport means the default of the engine
        opts = dict(body.opts)

        fallback: list[str] = []

        if kind == "auto":
            from ..services import knowledge, license, tunnel_test

            if body.iran_id == 0:
                raise ValueError("Best of the day is chosen for a pair of servers: pick the Iran server first (the smart choice of the Iran server cannot know the pair yet)")

            picks, why = knowledge.best_kinds(body.iran_id, body.foreign_id, allowed=license.tunnel_kind_allowed, limit=knowledge.AUTO_TRIES)

            if not picks:
                raise ValueError(why)

            (key, why), fallback = picks[0], [k for k, _ in picks[1:]]          # (a build that ends badly - a tunnel that carries nothing is refused - goes on to the next best type)

            kind, transport = tunnel_test.kind_parts(key)

            if kind == "backhaul" and tunnel_test.kind_link(key) != "gre":
                transport = f"{tunnel_test.kind_link(key)}:{transport}"

            opts = {}

        if kind == "backhaul" and ":" in transport:         # "wg:tcpmux" / "fou:tcpmux" / "ipsec:tcp" ...: Backhaul over another link (the kinds of the pair test)
            link, _, transport = transport.partition(":")

            if link not in linkmodels.ALL_LINKS or link == "gre":
                raise ValueError("the link of a Backhaul tunnel is GRE (the default) or one of: " + ", ".join(k for k in linkmodels.ALL_LINKS if k != "gre"))

            opts["link"] = link

            if link in builder.NEEDS_WG_TOOL:
                opts["install_wg"] = True                        # the owner asked for it: wireguard-tools may be installed (apt) on a server that lacks it

        if kind == "backhaul" and transport not in ("tcp", "tcpmux", "wsmux", "wssmux"):
            raise ValueError("transport must be tcp, tcpmux, wsmux or wssmux")

        if kind == "backhaul" and body.kind != "auto":
            opts["link_fallback"] = True                         # the link under it does not pass between the two servers: the other link models are tried, one by one, before the build gives up

        other_opts = {k: v for k, v in ({} if body.kind == "auto" else body.opts).items() if k in ("preset", "accept_udp", "sni_domain", "spoof_profile", "spoof_src_ip", "spoof_interface", "mtu", "kcp_mode", "conn", "keepalive", "heartbeat", "nodelay", "connection_pool", "mux_con", "channel_size", "rcvwnd", "sndwnd", "smuxbuf", "streambuf", "kcp_block") and isinstance(v, (str, int, bool)) and len(str(v)) <= 80}
        measure = "full" if body.opts.get("measure") != "latency" else "latency"

        names = [n.strip().lower() for n in body.dns_names if n.strip()]

        if any(len(n) > 253 or not re.fullmatch(r"[a-z0-9.*-]+", n) for n in names):
            raise ValueError("a DNS name may only have letters, digits, dots and dashes")

        if body.iran_id == 0 or body.replace_ids or body.replace_units or body.dns_follow or names or fallback:
            from ..services import create

            aid = create.start(body.iran_id, body.foreign_id, body.ports, kind, transport, opts if kind == "backhaul" else other_opts,
                               body.replace_ids, body.dns_follow, body.mtu, body.keepalive, body.nodelay, measure, dns_names=names, replace_units=body.replace_units, fallback_kinds=fallback)
        elif kind == "backhaul":
            aid = builder.start_build(body.iran_id, body.foreign_id, body.ports, transport, body.mtu, body.keepalive, nodelay=body.nodelay, opts=opts)
        else:
            from ..services import build_other

            aid = build_other.start_build(body.iran_id, body.foreign_id, body.ports, kind, transport, other_opts, measure=measure)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True, "id": aid, **({"kind": kind, "transport": transport, "why": why} if body.kind == "auto" else {})}          # (the answer of every other build is what it always was)


class PrecheckIn(BaseModel):
    iran_id: int = Field(0, ge=0)
    foreign_id: int
    ports: str = Field(min_length=1, max_length=300)
    live: bool = False                                           # also look at the Iran server itself (one SSH call)


class SmartTestIn(BaseModel):
    foreign_id: int
    ports: str = Field(min_length=1, max_length=300)
    kind: str = Field("tcpmux", pattern=r"^[a-z0-9:]{1,24}$")


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
         "transports": [{"key": k, "label": lbl, "note": n} for k, lbl, n in (("tcpmux", "TCP Mux", "many users, few connections (recommended)"), ("tcp", "TCP", "plain TCP"), ("wsmux", "WebSocket Mux", "clear-text WebSocket, multiplexed"), ("wssmux", "WebSocket TLS Mux", "TLS WebSocket, multiplexed"),
                                                                     ("wg:tcpmux", "TCP Mux · WireGuard", "over an encrypted WireGuard link (one UDP port) instead of GRE: passes where GRE is filtered"),
                                                                     ("wg:tcp", "TCP · WireGuard", "plain TCP over a WireGuard link"),
                                                                     ("fou:tcpmux", "TCP Mux · GRE over UDP (FOU)", "over GRE inside one UDP port: passes where protocol 47 is filtered, not encrypted"),
                                                                     ("gue:tcpmux", "TCP Mux · GRE over UDP (GUE)", "GRE inside one UDP port with the GUE header (another UDP shape than FOU), not encrypted"),
                                                                     ("ipip:tcpmux", "TCP Mux · IPIP", "IP-in-IP (protocol 4): the lightest link, one per pair of servers, not encrypted"),
                                                                     ("ipipfou:tcpmux", "TCP Mux · IPIP over UDP (FOU)", "IP-in-IP inside one UDP port, one per pair of servers, not encrypted"),
                                                                     ("ipipgue:tcpmux", "TCP Mux · IPIP over UDP (GUE)", "IP-in-IP inside one UDP port with the GUE header, one per pair of servers, not encrypted"),
                                                                     ("ipsec:tcpmux", "TCP Mux · IPsec (ESP)", "ESP (protocol 50) with AES-GCM, keys made by the panel"),
                                                                     ("ipsecnat:tcpmux", "TCP Mux · IPsec in UDP (NAT-T)", "ESP inside one UDP port with AES-GCM, keys made by the panel"),
                                                                     ("wgchisel:tcpmux", "TCP Mux · WireGuard in Chisel (foreign dials)", "WireGuard whose packets travel inside a Chisel TLS WebSocket on ONE TCP port: passes where GRE, ESP and UDP are all filtered; slower (a TCP stream carries the packets). Needs wireguard-tools: building it from here installs it"),
                                                                     ("wgchiselfwd:tcpmux", "TCP Mux · WireGuard in Chisel (Iran dials)", "the same, but the Iran server opens the TCP connection (use it when the foreign address is not filtered from Iran)"))]},
        {"key": "backpack", "title": "BackPack", "note": "the foreign server dials the Iran server; twelve transports, presets, UDP", "direction": "reverse",
         "transports": [{"key": k, "label": v[0], "note": v[1] + (" · root" if v[2] else ""), "udp": v[3]} for k, v in bo.BP_TRANSPORTS.items()]},
        {"key": "backpack_direct", "title": "BackPack Direct", "note": "an L3 link the Iran server dials: UDP / QUIC / PCK / SNI spoofing (a fake TLS hello) / IP spoofing (a forged source address) / xDi carriers", "direction": "direct",
         "transports": [{"key": k, "label": v[0], "note": v[1] + (" · root" if v[2] else ""), "udp": v[3]} for k, v in bo.BP_DIRECT.items()]},
        {"key": "paqet", "title": "Paqet", "note": "KCP over raw sockets: the foreign server listens, the Iran server opens the ports", "direction": "direct",
         "transports": [{"key": "kcp", "label": "KCP", "note": "modes normal / fast / fast2 / fast3"}]},
        {"key": "chisel", "title": "Chisel", "note": "one WebSocket port: on the Iran server (reverse, the foreign server dials) or on the foreign server (forward, the Iran server dials)", "direction": "reverse",
         "transports": [{"key": "ws", "label": "HTTP WebSocket", "note": "reverse, clear text"}, {"key": "wss", "label": "TLS WebSocket", "note": "reverse, self-signed certificate, key pinned"},
                        {"key": "fws", "label": "Forward HTTP WebSocket", "note": "the Iran server dials: the customers' downloads run at full speed"}, {"key": "fwss", "label": "Forward TLS WebSocket", "note": "the Iran server dials, TLS, key pinned"}]},
    ]}


@router.get("/tunnels/{tunnel_id}/live-read")
def live_read(tunnel_id: int, via: str = "card"):
    """ONE reading, now, of a network card's counters for the Live window of a tunnel (read-only; the page asks once a second while the window is open): `via` card = the tunnel's own card on its Iran server,
    iran_nic / foreign_nic = the main card of that server (like nload). The page turns two readings into Mb/s with the server's uptime clock."""
    from ..ssh import SSHError

    try:
        return {"ok": True, **livebw.sample(tunnel_id, via)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))

@router.get("/tunnels/live")
def tunnels_live():
    """What the users use of each tunnel right now (Mb/s down and up, from the counters of its link or, for a reverse proxy with no link, of its TCP connections; counted over about 15 s and read every 5 s while this is asked). Only a read of memory: the servers are read by a job."""
    livebw.ask()
    return {"ok": True, "at": time.time(), "live": livebw.view(), "use24": use_summary(int(time.time()))}


@router.get("/tunnels/brief")
def tunnels_brief():
    """ONE signature of the states of every server (on / off) and every tunnel (up, down ...): the open page asks for it every 2 s and redraws at once when it changes (the owner, 2026-10-20: "show it at once when a
    tunnel or a server is cut" - the page waited its 15 s or 30 s round). A few bytes, one read of two small tables; no names, no numbers that change by themselves."""
    import hashlib
    import json

    from ..models import Server

    with session() as db:
        servers = sorted((s.id, s.online is not False) for s in db.scalars(select(Server).where(Server.enabled.is_(True))))
        states = sorted((t.id, t.state) for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))))

    return {"ok": True, "sig": hashlib.md5(json.dumps([servers, states]).encode()).hexdigest()[:12], "servers": len(servers), "tunnels": len(states)}


_USE: dict = {"at": 0, "rows": {}}


def use_summary(now: int, window: int = 86400, keep: int = 60) -> dict:
    """What the users carried through every tunnel in the last 24 hours - the PEAK and the average of the Iran end's counters, read by the checks every minute - so that the page can show what the users get (the owner,
    2026-10-19: "show how much bandwidth the user gets, in place of these numbers": the little capacity test of the Doctor read 0.40 on a tunnel whose users carried 6 - 13). Kept `keep` seconds: the page asks every 4 s."""
    from sqlalchemy import func

    if now - int(_USE["at"]) < keep and _USE["rows"]:
        return _USE["rows"]

    with session() as db:
        rows = db.execute(select(TunnelCheck.tunnel_id, func.max(TunnelCheck.rx_mbps), func.avg(TunnelCheck.rx_mbps), func.count(TunnelCheck.rx_mbps))
                          .where(TunnelCheck.ts >= now - window, TunnelCheck.rx_mbps.is_not(None)).group_by(TunnelCheck.tunnel_id)).all()

    _USE["at"], _USE["rows"] = now, {int(tid): {"peak": round(float(mx), 1), "avg": round(float(av), 1)} for tid, mx, av, n in rows if n >= 5}
    return _USE["rows"]


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
        return {"ok": True, "id": tt.compare_start(body.iran_id, body.foreign_id, body.kinds, install_wg=True)}      # (the owner started it: a WireGuard kind may install wireguard-tools)
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
    """Remove a tunnel from both servers; it goes to the trash, from where it can be put back. Its warm standby (if it has one) is removed with it. A server that is off is not asked (its files are purged when it is back):
    waiting for it took 30 - 90 s per side and the tunnel of an off server could not be removed from the page (the owner, 2026-10-19)."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        name = t.name if t is not None and not is_standby(t) else ""

    try:
        res = builder.delete_tunnel(tunnel_id, trash=True, skip_offline=True)
    except ValueError as e:
        raise HTTPException(400 if "read-only" in str(e) else 404, str(e))

    if name:
        standby.forget(name)

    return {"ok": True, "results": res}


class StandbyIn(BaseModel):
    on: bool


@router.get("/tunnels/{tunnel_id}/standby")
def standby_get(tunnel_id: int):
    """The warm standby of a tunnel: whether it is on, its state (off · building · ready · in_use · mending · waiting · failed), its type, the shifted ports and why it waits."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None or is_standby(t):
            raise HTTPException(404, "tunnel not found")

        name = t.name

    return {"ok": True, **standby.status(name)}


@router.put("/tunnels/{tunnel_id}/standby")
def standby_set(tunnel_id: int, body: StandbyIn):
    """Switch the warm standby of a tunnel on (it is built at once when the tunnel is up: another tunnel type between the same two servers, on shifted ports) or off (it is removed)."""
    try:
        return {"ok": True, **standby.switch(tunnel_id, body.on)}
    except ValueError as e:
        raise HTTPException(400, str(e))


def _standby_name(tunnel_id: int) -> str:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None or is_standby(t):
            raise HTTPException(404, "tunnel not found")

        return t.name


@router.post("/tunnels/{tunnel_id}/standby/handover")
def standby_handover(tunnel_id: int):
    """Hand the users' ports of this tunnel to its standby NOW (by hand: they stay there until Back now). Refused when the standby is not up."""
    from ..ssh import SSHError

    name = _standby_name(tunnel_id)

    try:
        standby.handover(name, "handed over by you", manual=True)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, SSHError) as e:
        raise HTTPException(502, str(e))

    return {"ok": True, **standby.status(name)}


@router.post("/tunnels/{tunnel_id}/standby/back")
def standby_back(tunnel_id: int):
    """Back now: the users' ports are the tunnel's again (the redirects to the standby are taken off; open connections stay on the standby until they close)."""
    from ..ssh import SSHError

    name = _standby_name(tunnel_id)

    try:
        standby.give_back(name, "taken back by you")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, SSHError) as e:
        raise HTTPException(502, str(e))

    return {"ok": True, **standby.status(name)}


class LanesIn(BaseModel):
    on: bool


@router.get("/lanes/settings")
def lanes_settings():
    """The owner's switch for lanes (a tunnel that is nearly full shares its new connections with its warm standby): off by default."""
    return {"ok": True, "on": lanes.enabled()}


@router.put("/lanes/settings")
def lanes_settings_put(body: LanesIn):
    """Switch the automatic lanes on or off. Off: the lanes that were put by themselves are ended the careful way (a lane started by hand stays until Stop)."""
    return {"ok": True, **lanes.set_enabled(body.on)}


@router.get("/tunnels/{tunnel_id}/lane")
def lane_get(tunnel_id: int):
    """The lane of a tunnel: off · idle · waiting · sharing · paused, why, the share of the new connections that goes to its second tunnel, both capacities and the users' rate on both."""
    return {"ok": True, **lanes.status(_standby_name(tunnel_id))}


@router.post("/tunnels/{tunnel_id}/lane/start")
def lane_start(tunnel_id: int):
    """Share now (by hand): the new connections of this tunnel are split between it and its warm standby by the two capacities. Nobody is cut: connections that are open stay where they are. Stays until Stop."""
    from ..ssh import SSHError

    name = _standby_name(tunnel_id)

    try:
        lanes.start(name, manual=True)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, SSHError) as e:
        raise HTTPException(502, str(e))

    return {"ok": True, **lanes.status(name)}


@router.post("/tunnels/{tunnel_id}/lane/stop")
def lane_stop(tunnel_id: int):
    """Stop sharing: new connections go to the tunnel again; the ones that are open on the second tunnel finish there (the rules are removed when none is left)."""
    from ..ssh import SSHError

    name = _standby_name(tunnel_id)

    try:
        lanes.stop(name, "stopped by you")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, SSHError) as e:
        raise HTTPException(502, str(e))

    return {"ok": True, **lanes.status(name)}


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
    ux: dict = {}           # what the users get through it (services/userq.py): the grade, the configs with a problem, the Doctor's reading
    bw: dict = {}           # the bandwidth the Doctor keeps (services/bandwidth.py) without its history; GET /tunnels/{id}/bandwidth has the history
    record: dict = {}       # port mapping, direction, test door, engine options - never secrets
    cap: dict = {}          # the capacity that is filling (services/capacity.py): {state: warm | full, util, cap, since}; {} while it is fine or unknown
    standby: dict = {}      # the warm standby of the tunnel (services/standby.py): on, state, type, since, why, the shifted ports
    lane: dict = {}         # its lane (services/lanes.py): state (off · idle · waiting · sharing · paused), why, the share to the second tunnel, both capacities, the users' rate on both


def _out(t: Tunnel, sb: dict | None = None, lane: dict | None = None) -> TunnelOut:
    plan = plan_for(t)
    return TunnelOut(id=t.id, name=t.name, type=t.type, transport=t.transport, engine=t.engine,
                     iran_name=t.iran.name, iran_host=t.iran.host, foreign_name=t.foreign.name, foreign_host=t.foreign.host,
                     port=t.port, iran_tunnel_ip=t.iran_tunnel_ip, foreign_tunnel_ip=t.foreign_tunnel_ip,
                     forwarded_ports=t.forwarded_ports or [], enabled=t.enabled, created_at=t.created_at,
                     state=t.state, state_since=t.state_since, last_check_at=t.last_check_at, last_error=t.last_error,
                     rtt_ms=t.rtt_ms, rx_mbps=t.rx_mbps, tx_mbps=t.tx_mbps, detail=t.detail or {}, quality=(t.record or {}).get("quality") or {},
                     bw={k: v for k, v in ((t.record or {}).get("bw") or {}).items() if k != "hist"},
                     ux={k: v for k, v in ((t.record or {}).get("ux") or {}).items() if k != "pend"},
                     record={k: v for k, v in (t.record or {}).items() if k not in ("quality", "bw", "ux")},
                     units={"iran": plan.iran_units, "foreign": plan.foreign_units, "iface": plan.iface}, cap=capacity.view(t.name), standby=sb or {}, lane=lane or {})


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
        shown = [t for t in db.scalars(select(Tunnel).order_by(Tunnel.name)) if not is_temp(t.name) and not is_standby(t)]      # a temporary test tunnel and a warm standby are not part of the list
        sbs = standby.summaries([t.name for t in shown])
        lns = lanes.summaries([t.name for t in shown])
        rows = [_out(t, sbs.get(t.name), lns.get(t.name)) for t in shown]
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

        if t is None or is_standby(t):          # (a standby is shown inside the window of its tunnel only)
            raise HTTPException(404, "tunnel not found")

        return _out(t, standby.status(t.name), lanes.status(t.name))


@router.get("/tunnels/{tunnel_id}/stability")
def stability(tunnel_id: int):
    """How steady a tunnel is: the share of its checks that answered over 24 hours and 7 days, its cuts, the last one, how long it has been up, and the strip of the last day."""
    r = analyst.stability(tunnel_id)

    if r is None:
        raise HTTPException(404, "tunnel not found")

    return {"ok": True, **r}


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