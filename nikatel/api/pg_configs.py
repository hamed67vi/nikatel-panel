"""PasarGuard: the usage of the CONFIGS (hosts and inbounds) of a panel, measured by this panel (services/cfgstats.py). Routes: /api/panels/{panel_id}/pg/configs. Reading changes nothing; switching a meter on or off
puts (or takes) ONE counting-only nftables table on a server of ours."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import cfgcdn, cfgstats, panels
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])
P = "/panels/{panel_id}/pg/configs"


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PanelError as e:
        raise HTTPException(502, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


class MeterIn(BaseModel):
    server_id: int | None = Field(None, description="the server; empty = every server of the map (on) or every server whose meter is on (off)")
    on: bool


class CdnIn(BaseModel):
    on: bool


class CdnTestIn(BaseModel):
    account: str = Field(max_length=40)
    fqdn: str = Field(max_length=253)


@router.get(P)
def configs(panel_id: int, refresh: bool = False):
    """The usage of every host and inbound of the panel (24 hours, 7 and 30 days, trend, peak connections, little used, falling), the servers and their meters, and the CDN accounts."""
    return {"ok": True, **_wrap(cfgstats.snapshot, panel_id, None, refresh)}


@router.post(P + "/meter")
def meter(panel_id: int, body: MeterIn):
    """Switch the counting table on (or off) on one server, or on all of them: it only counts bytes, stops no packet and changes nothing else, and Remove deletes it."""
    if body.server_id is None:
        return {"ok": True, "results": _wrap(cfgstats.meters_all, panel_id, body.on)}

    if body.on:
        return {"ok": True, **_wrap(cfgstats.meter_on, panel_id, body.server_id)}

    return {"ok": True, **_wrap(cfgstats.meter_off, body.server_id)}


@router.post(P + "/cdn")
def cdn(panel_id: int, body: CdnIn):
    """Read the traffic reports of the CDN domains of the DNS accounts once an hour (read only), or stop reading them."""
    _wrap(panels.cred_for, panel_id)
    cfgcdn.set_enabled(body.on)
    cfgstats.forget()
    return {"ok": True, "cdn_on": cfgcdn.enabled()}


@router.post(P + "/cdn-test")
def cdn_test(panel_id: int, body: CdnTestIn):
    """Try the traffic report of one CDN name now (read only) and say what the CDN answered and whether per-host numbers exist."""
    _wrap(panels.cred_for, panel_id)
    cfgstats.forget()
    return {"ok": True, **_wrap(cfgcdn.probe, body.account, body.fqdn)}
