import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Config, ConfigSample, Subscription
from ..services import configs, probe
from ..services import settings as st

router = APIRouter(tags=["configs"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.get("/configs")
def list_configs():
    with session() as db:
        rows = list(db.scalars(select(Config).order_by(Config.group, Config.name)))
        return {"ok": True, "probe": probe.active(), "configs": [{
            "id": c.id, "name": c.name, "slug": c.slug, "group": c.group, "port": c.port, "enabled": c.enabled, "tunnel": c.tunnel, "cdn_port": c.cdn_port, "meta": c.meta or {},
            "status": c.status, "status_since": c.status_since, "ping_ms": c.ping_ms, "down_mbps": c.down_mbps, "up_mbps": c.up_mbps, "speed_at": c.speed_at,
            "checked_at": c.checked_at, "last_error": c.last_error} for c in rows]}


@router.get("/configs/{config_id}/samples")
def samples(config_id: int, hours: int = 24):
    with session() as db:
        rows = db.scalars(select(ConfigSample).where(ConfigSample.config_id == config_id, ConfigSample.ts >= int(time.time()) - hours * 3600).order_by(ConfigSample.ts))
        return {"ok": True, "columns": ["ts", "up", "ping_ms"], "samples": [[r.ts, r.up, r.ping_ms] for r in rows]}


@router.get("/configs/{config_id}/uri")
def get_uri(config_id: int):
    with session() as db:
        c = db.get(Config, config_id)

        if c is None:
            raise HTTPException(404, "config not found")

        name = c.name

    return {"ok": True, "uri": _wrap(probe.config_uri, name)}


class AddIn(BaseModel):
    uri: str = Field(min_length=10, max_length=4000)
    name: str = Field("", max_length=64)
    group: str = Field("", max_length=64)


@router.post("/configs", status_code=201)
def add(body: AddIn):
    return {"ok": True, "name": _wrap(configs.add, body.uri, body.name, body.group)}


class UpdateIn(BaseModel):
    uri: str = Field("", max_length=4000)
    new_name: str = Field("", max_length=64)
    group: str | None = Field(None, max_length=64)
    tunnel: str | None = Field(None, max_length=80)
    cdn_port: str | None = Field(None, max_length=8, pattern=r"^\d{0,5}$")


@router.put("/configs/{config_id}")
def update(config_id: int, body: UpdateIn):
    with session() as db:
        c = db.get(Config, config_id)

        if c is None:
            raise HTTPException(404, "config not found")

        name = c.name

    _wrap(configs.update, name, body.uri, body.new_name, body.group, body.tunnel, body.cdn_port)
    return {"ok": True}


@router.delete("/configs/{config_id}")
def remove(config_id: int):
    with session() as db:
        c = db.get(Config, config_id)

        if c is None:
            raise HTTPException(404, "config not found")

        name = c.name

    _wrap(configs.remove, name)
    return {"ok": True}


@router.post("/configs/sync")
def sync_now():
    configs.sync()
    return {"ok": True, "probe": probe.active()}


@router.get("/configs/{config_id}/speed")
def speed(config_id: int):
    with session() as db:
        c = db.get(Config, config_id)

        if c is None:
            raise HTTPException(404, "config not found")

        name = c.name

    return {"ok": True, **_wrap(probe.speed_state, name)}


# ---- subscriptions
class SubIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    url: str = Field("", max_length=2000)     # empty = the link saved with this group (Sync)
    picks: list[str] | None = None


def _sub_url(body: SubIn) -> str:
    if body.url.strip():
        return body.url.strip()

    with session() as db:
        s = db.scalar(select(Subscription).where(Subscription.name == body.name))

    if s is None or not s.url:
        raise HTTPException(400, "no saved link for this group — paste the subscription link")

    return s.url


@router.get("/subscriptions")
def subs():
    with session() as db:
        return {"ok": True, "subscriptions": [{"id": s.id, "name": s.name, "nodes": s.nodes, "last_import": s.last_import, "url_host": s.url.split("/")[2] if "//" in s.url else ""}
                                              for s in db.scalars(select(Subscription).order_by(Subscription.name))]}


@router.post("/subscriptions/preview")
def sub_preview(body: SubIn):
    nodes = _wrap(configs.fetch_subscription, _sub_url(body))

    with session() as db:
        existing = {c.name for c in db.scalars(select(Config))}

    return {"ok": True, "nodes": [{"name": n["name"], "proto": n["proto"], "exists": n["name"] in existing} for n in nodes]}


@router.post("/subscriptions/import")
def sub_import(body: SubIn):
    return {"ok": True, **_wrap(configs.import_subscription, body.name, _sub_url(body), body.picks)}


@router.delete("/subscriptions/{sid}")
def sub_delete(sid: int):
    with session() as db:
        s = db.get(Subscription, sid)

        if s is None:
            raise HTTPException(404, "no such subscription")

        db.delete(s)   # the configs stay; only the group record goes

    return {"ok": True}


# ---- probe settings
class ProbeIn(BaseModel):
    api_url: str = ""
    api_token: str = ""      # empty keeps the stored one
    standby: list[dict] = []
    server_id: int = 0


@router.get("/settings/probe")
def probe_get():
    d = st.public("probe")
    d["standby"] = [{"name": x.get("name", ""), "api_url": x.get("api_url", ""), "has_token": bool(x.get("api_token"))} for x in d.get("standby") or []]
    return {"ok": True, **d, "state": probe.active()}


@router.put("/settings/probe")
def probe_put(body: ProbeIn):
    if body.api_url and not probe.valid_url(body.api_url):
        raise HTTPException(400, "the probe address looks like http://host:port")

    cur = st.get("probe")
    standby = []

    for i, x in enumerate(body.standby):
        url, tok = str(x.get("api_url", "")).strip(), str(x.get("api_token", "")).strip()

        if not probe.valid_url(url):
            raise HTTPException(400, f"standby {i + 1}: the address looks like http://host:port")

        old = next((o for o in (cur.get("standby") or []) if o.get("api_url") == url), None)
        standby.append({"name": str(x.get("name", "") or f"standby{i + 1}")[:32], "api_url": url, "api_token": tok or (old or {}).get("api_token", "")})

    st.put("probe", {"api_url": body.api_url.rstrip("/"), "api_token": body.api_token, "standby": standby, "server_id": body.server_id})
    return probe_get()


@router.post("/settings/probe/install")
def probe_install(server_id: int, as_standby: bool = False):
    from ..services import probe_install as pi

    return {"ok": True, **_wrap(pi.install, server_id, as_standby)}


@router.post("/configs/{config_id}/speedtest")
def speedtest(config_id: int):
    """Run a speed test for one config on the probe server (over SSH; needs the probe's server in the inventory)."""
    from ..models import Server
    from ..ssh import Node, run_action

    with session() as db:
        c = db.get(Config, config_id)

        if c is None:
            raise HTTPException(404, "config not found")

        name = c.name
        srv = db.get(Server, int(st.get("probe").get("server_id") or 0))

        if srv is None:
            raise HTTPException(400, "tell the panel which server runs the probe (Settings → Probe → server)")

        node = Node(srv.host, srv.ssh_user, srv.ssh_port)

    try:
        out = run_action(node, "probe_speed", {"NAME": name}, timeout=200)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, str(e))

    configs.sync()
    return {"ok": True, "output": out[-400:]}


@router.post("/settings/probe/test")
def probe_test():
    d = _wrap(probe.request, "GET", "/api/config/list", None, 30)
    return {"ok": True, "configs": len(d.get("configs") or []), "probe": probe.active()}
