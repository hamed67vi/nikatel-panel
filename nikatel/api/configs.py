import time

from fastapi import APIRouter, Depends, HTTPException, Query
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
    """Every client config on the probe with its state, ping, speed and group, in the order of its subscription."""
    with session() as db:
        order = {s.name: {n: i for i, n in enumerate(s.nodes or [])} for s in db.scalars(select(Subscription))}      # a group lists its configs in the order of its subscription
        rows = sorted(db.scalars(select(Config)), key=lambda c: (c.group or "", order.get(c.group or "", {}).get(c.name, 10**6), c.name.lower()))
        return {"ok": True, "probe": probe.active(), "configs": [{
            "id": c.id, "name": c.name, "slug": c.slug, "group": c.group, "port": c.port, "enabled": c.enabled, "tunnel": c.tunnel, "cdn_port": c.cdn_port, "meta": c.meta or {},
            "status": c.status, "status_since": c.status_since, "ping_ms": c.ping_ms, "down_mbps": c.down_mbps, "up_mbps": c.up_mbps, "speed_at": c.speed_at,
            "checked_at": c.checked_at, "last_error": c.last_error} for c in rows]}


@router.get("/configs/{config_id}/samples")
def samples(config_id: int, hours: int = Query(24, ge=1, le=720)):
    """The up / down history and the pings of one config over the last hours."""
    with session() as db:
        rows = db.scalars(select(ConfigSample).where(ConfigSample.config_id == config_id, ConfigSample.ts >= int(time.time()) - hours * 3600).order_by(ConfigSample.ts))
        return {"ok": True, "columns": ["ts", "up", "ping_ms"], "samples": [[r.ts, r.up, r.ping_ms] for r in rows]}


@router.get("/configs/{config_id}/uri")
def get_uri(config_id: int):
    """The share link of one config (as kept on the probe)."""
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
    """Add a config from a share link; it is written to the probe."""
    return {"ok": True, "name": _wrap(configs.add, body.uri, body.name, body.group)}


class UpdateIn(BaseModel):
    uri: str = Field("", max_length=4000)
    new_name: str = Field("", max_length=64)
    group: str | None = Field(None, max_length=64)
    tunnel: str | None = Field(None, max_length=80)
    cdn_port: str | None = Field(None, max_length=8, pattern=r"^\d{0,5}$")


@router.put("/configs/{config_id}")
def update(config_id: int, body: UpdateIn):
    """Change a config's name, link, group, tunnel label or CDN port."""
    with session() as db:
        c = db.get(Config, config_id)

        if c is None:
            raise HTTPException(404, "config not found")

        name = c.name

    _wrap(configs.update, name, body.uri, body.new_name, body.group, body.tunnel, body.cdn_port)
    return {"ok": True}


class DeleteMany(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)


@router.post("/configs/delete-many")
def remove_many(body: DeleteMany):
    """Delete several configs at once (the rows go now, the probe is told in the background)."""
    with session() as db:
        names = [c.name for c in db.scalars(select(Config).where(Config.id.in_(body.ids)))]

    return {"ok": True, **configs.remove_fast(names)}

@router.delete("/configs/{config_id}")
def remove(config_id: int):
    """Delete one config."""
    with session() as db:
        c = db.get(Config, config_id)

        if c is None:
            raise HTTPException(404, "config not found")

        name = c.name

    configs.remove_fast([name])
    return {"ok": True}


@router.post("/configs/sync")
def sync_now():
    """Read the probe now instead of at the next round."""
    configs.sync()
    return {"ok": True, "probe": probe.active()}


@router.get("/configs/{config_id}/speed")
def speed(config_id: int):
    """The last speed test of one config."""
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
    """The subscriptions whose nodes were imported as groups of configs."""
    with session() as db:
        return {"ok": True, "subscriptions": [{"id": s.id, "name": s.name, "nodes": s.nodes, "excluded": s.excluded or [], "last_import": s.last_import, "url_host": s.url.split("/")[2] if "//" in s.url else ""}
                                              for s in db.scalars(select(Subscription).order_by(Subscription.name))]}


@router.post("/subscriptions/preview")
def sub_preview(body: SubIn):
    """Read a subscription link and list its nodes without importing."""
    nodes = _wrap(configs.fetch_subscription, _sub_url(body))

    with session() as db:
        existing = {c.name for c in db.scalars(select(Config))}

    return {"ok": True, "nodes": [{"name": n["name"], "proto": n["proto"], "exists": n["name"] in existing} for n in nodes]}


class ExcludedIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    excluded: list[str] = Field(default_factory=list, max_length=2000)


@router.put("/subscriptions/excluded")
def sub_excluded(body: ExcludedIn):
    """Which nodes of a subscription are never imported (unticked)."""
    with session() as db:
        s = db.scalar(select(Subscription).where(Subscription.name == body.name))

        if s is None:
            raise HTTPException(404, "no such subscription")

        s.excluded = sorted(set(body.excluded))

    return {"ok": True, "excluded": sorted(set(body.excluded))}


@router.post("/subscriptions/sync")
def sub_sync(body: SubIn):
    """Make the group equal to its subscription again: add the new nodes, remove the gone ones."""
    return {"ok": True, **_wrap(configs.sync_subscription, body.name)}


@router.post("/subscriptions/import")
def sub_import(body: SubIn):
    """Import the chosen nodes of a subscription as the configs of one group."""
    return {"ok": True, **_wrap(configs.import_subscription, body.name, _sub_url(body), body.picks)}


@router.delete("/subscriptions/{sid}")
def sub_delete(sid: int):
    """Forget a subscription record (its configs stay)."""
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
    """The probe (primary and standby): addresses, whether a token is kept, the server it runs on."""
    d = st.public("probe")
    d["standby"] = [{"name": x.get("name", ""), "api_url": x.get("api_url", ""), "has_token": bool(x.get("api_token"))} for x in d.get("standby") or []]
    return {"ok": True, **d, "state": probe.active()}


@router.put("/settings/probe")
def probe_put(body: ProbeIn):
    """Save the probe addresses and tokens (an empty token keeps the stored one)."""
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
    """Install a probe on an Iran server of the inventory, as an action (primary or standby)."""
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
    """Ask the active probe for its config list (read only)."""
    d = _wrap(probe.request, "GET", "/api/config/list", None, 30)
    return {"ok": True, "configs": len(d.get("configs") or []), "probe": probe.active()}
