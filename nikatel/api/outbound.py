from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import outbound
from ..ssh import SSHError

router = APIRouter(tags=["outbound"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, SSHError) as e:
        raise HTTPException(502, str(e))


class ConfigsIn(BaseModel):
    text: str = Field(min_length=1, max_length=60000)


class ConfigPatch(BaseModel):
    enabled: bool | None = None
    note: str | None = Field(None, max_length=60)


class ModeIn(BaseModel):
    mode: str = Field(pattern=r"^(spread|fast|auto)$")


class GatewaysIn(BaseModel):
    server_ids: list[int] = Field(max_length=40)


class RouteIn(BaseModel):
    target_host: str = Field(min_length=1, max_length=120)
    target_port: int = Field(ge=1, le=65535)
    name: str = Field("", max_length=60)


class RoutesIn(BaseModel):
    routes: list[RouteIn] = Field(min_length=1, max_length=600)


class AutoRoutesIn(BaseModel):
    addresses: list[str] = Field(min_length=1, max_length=100)
    ports: list[int] = Field(min_length=1, max_length=100)


class RouteDrop(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=600)


class RoutePatch(BaseModel):
    enabled: bool


class UsersIn(BaseModel):
    mode: str = Field(pattern=r"^(manual|auto)$")
    after_minutes: int | None = Field(None, ge=1, le=120)
    back_minutes: int | None = Field(None, ge=5, le=1440)


class KeysIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=300)


@router.get("/outbound")
def get_state():
    """The outbound: how the Iran servers reach the foreign nodes - the pool of exit configs (never their links), the mode, the Iran servers that carry it with what the last check found, and the forwards."""
    return {"ok": True, **outbound.state()}


@router.post("/outbound/configs")
def add_configs(body: ConfigsIn):
    """Add exit configs to the pool: paste vless / vmess / trojan / ss links, one per line. Answers which were added and which skipped (and why)."""
    return {"ok": True, **_wrap(outbound.add_configs, body.text)}


@router.patch("/outbound/configs/{config_id}")
def patch_config(config_id: str, body: ConfigPatch):
    """Switch an exit on or off, or change its note."""
    _wrap(outbound.set_config, config_id, body.enabled, body.note)
    return {"ok": True}


@router.delete("/outbound/configs/{config_id}")
def delete_config(config_id: str):
    """Remove an exit from the pool."""
    _wrap(outbound.drop_config, config_id)
    return {"ok": True}


@router.put("/outbound/mode")
def put_mode(body: ModeIn):
    """How the exits are used: spread (the load is shared by every alive one), fast (the fastest alive one) or auto (direct while it works, the pool when it does not)."""
    _wrap(outbound.set_mode, body.mode)
    return {"ok": True}


@router.put("/outbound/gateways")
def put_gateways(body: GatewaysIn):
    """The Iran servers that carry the outbound (each gets the whole pool and every forward when it is applied)."""
    _wrap(outbound.set_gateways, body.server_ids)
    return {"ok": True}


@router.put("/outbound/hubs")
def put_hubs(body: GatewaysIn):
    """The foreign servers that run the relay behind the exits (each gets its own port and password; applied with the rest)."""
    _wrap(outbound.set_hubs, body.server_ids)
    return {"ok": True}


@router.post("/outbound/hubs/{server_id}/remove")
def remove_hub(server_id: int):
    """Take the relay off a foreign server and forget it."""
    _wrap(outbound.remove_hub, server_id)
    return {"ok": True}


@router.put("/outbound/users")
def put_users(body: UsersIn):
    """How the users are connected to the pool: manual (you choose the records and press Connect) or auto (the panel points the records of a tunnel that stays down at the pool, and puts them back when it has been healthy)."""
    _wrap(outbound.set_users, body.mode, body.after_minutes, body.back_minutes)
    return {"ok": True}


@router.get("/outbound/suggest")
def suggest():
    """What the Marzban / PasarGuard panels offer to forward to: their nodes (address) and inbounds (port), and how many forwards each node already has. Each panel is asked at that moment."""
    return {"ok": True, **_wrap(outbound.suggest)}


@router.post("/outbound/routes")
def add_routes(body: RoutesIn):
    """Add forwards one by one (a node address and a port; the listen port on the Iran servers is given once and kept)."""
    return {"ok": True, **_wrap(outbound.add_routes, [r.model_dump() for r in body.routes])}


@router.post("/outbound/routes/auto")
def auto_routes(body: AutoRoutesIn):
    """Forwards for every chosen node address on every chosen port."""
    return {"ok": True, **_wrap(outbound.auto_routes, body.addresses, body.ports)}


@router.post("/outbound/routes/tunnels")
def routes_from_tunnels():
    """A forward on each Iran server for every port its TCP tunnels serve (its own listen port; it leads, through the pool, to the node and port the tunnel leads to). Run again, it follows the tunnels."""
    return {"ok": True, **_wrap(outbound.from_tunnels)}


@router.get("/outbound/connect")
def connect_plan():
    """What connecting the users would change: the ArvanCloud records (proxied, one Iran server address) whose origin port is the port of a tunnel that has a forward, each with the port it would get and whether it is already on the forward. Every account is asked at that moment."""
    return {"ok": True, **_wrap(outbound.connect_plan)}


@router.post("/outbound/connect", status_code=202)
def connect(body: KeysIn):
    """Point the chosen records (keys of the plan) at the forwards, as an action. The tunnels stay untouched; every change is in the DNS log and can be put back."""
    return {"ok": True, "id": _wrap(outbound.connect, body.keys)}


@router.post("/outbound/connect/undo", status_code=202)
def disconnect(body: KeysIn):
    """Put the chosen records back on the tunnels' ports, as an action."""
    return {"ok": True, "id": _wrap(outbound.disconnect, body.keys)}


@router.post("/outbound/routes/drop")
def drop_routes(body: RouteDrop):
    """Remove forwards (the users' links through them stop working once the change is applied)."""
    return {"ok": True, "removed": _wrap(outbound.drop_routes, body.ids)}


@router.patch("/outbound/routes/{route_id}")
def patch_route(route_id: str, body: RoutePatch):
    """Switch one forward on or off."""
    _wrap(outbound.set_route, route_id, body.enabled)
    return {"ok": True}


@router.post("/outbound/apply", status_code=202)
def apply():
    """Put the pool and the forwards on every chosen Iran server (installs Xray where it is missing), as an action; its story says what happened on each server."""
    return {"ok": True, "id": _wrap(outbound.apply_all)}


@router.post("/outbound/check")
def check_now():
    """Look at the pool from every chosen Iran server now (what the job does every few minutes): each exit, and the gateway as users see it."""
    _wrap(outbound.check)
    return {"ok": True, **outbound.state()}


@router.post("/outbound/servers/{server_id}/remove")
def remove_server(server_id: int):
    """Take the outbound off one Iran server (Xray is stopped and removed there; the pool and the forwards stay in the panel)."""
    _wrap(outbound.remove_from, server_id)
    return {"ok": True}
