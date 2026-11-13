"""The L2TP tab of Network (services/l2tp.py): the accounts of the L2TP entry, who is online, the state of the entry, the exit and the tunnel between them, and the text a customer is sent. Only the owner's sign-in or an `admin` token."""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import l2tp, l2tp_shop, l2tpwatch
from ..ssh import SSHError


def _l2tp_licensed():          # (the owner, 2026-11-11: "the L2TP parts are disabled for the others": the whole tab is a licensed feature; the owner's own panel has every feature)
    from ..services import license

    try:
        license.require("l2tp")
    except ValueError as e:
        raise HTTPException(402, str(e))


router = APIRouter(tags=["l2tp"], dependencies=[Depends(current_user), Depends(_l2tp_licensed)])

_STATE: dict = {"at": 0.0, "full_at": 0.0, "data": None}          # (the page asks every few seconds; the entry is read over SSH, so an answer is kept for 10 s - 3 s for the light read of an open tab - and dropped at once by every change)
STATE_TTL = 10.0
LIVE_TTL = 2.0
FULL_MAX = 120.0          # (the exit, the units and the tunnel are read again at least this often, even while the open tab only asks for the light read)


def _shop_licensed():          # (the shop side reads and writes the shop bot's database: it is behind the same licence feature as the Shop bot page)
    from ..services import license

    try:
        license.require("shopbot")
    except ValueError as e:
        raise HTTPException(402, str(e))


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except SSHError as e:
        raise HTTPException(409 if "read-only" in str(e) else 502, str(e))
    except RuntimeError as e:          # (the shop bot's database said no, or the bot host did not answer)
        raise HTTPException(502, str(e)[:200])


def _drop():          # (the servers themselves were changed: nothing that was read is kept)
    _STATE["at"] = 0.0
    _STATE["full_at"] = 0.0
    _STATE["data"] = None
    l2tp.forget_exit()


def _changed():          # (an ACCOUNT was changed - 1.69.250: the next read asks the entry again at once, and only the entry; the exit and the tunnel did not change with it, and waiting for them is what kept a deleted row on the screen)
    _STATE["at"] = 0.0


@router.get("/l2tp")
def overview(fresh: bool = False, live: bool = False):
    """The entry (services, load, pool), its accounts without passwords, the sessions, the exit and the tunnel; each part says why when it cannot be read.
    `live` is the light read an open tab makes every few seconds: only the entry is read again (who is connected now), the rest comes from the last full answer. The read after a change of an account is that light read too."""
    now = time.time()

    if fresh or _STATE["data"] is None or now - _STATE["full_at"] > (FULL_MAX if live else STATE_TTL):
        _STATE["data"] = l2tp.state()
        _STATE["at"] = _STATE["full_at"] = time.time()
    elif now - _STATE["at"] > (LIVE_TTL if live else STATE_TTL):
        _STATE["data"] = l2tp.state(base=_STATE["data"])
        _STATE["at"] = time.time()

    return {"ok": True, **_STATE["data"], "watch": l2tpwatch.view()}          # (1.69.257: what the L2TP watch saw and did, for the strip's Doctor cell - a settings read, never an SSH read)


@router.get("/l2tp/config")
def get_config():
    """Which servers carry the product, the connection name the customer sees, and the servers and tunnels to choose from."""
    return {"ok": True, "conf": l2tp.conf(), "choices": l2tp.choices()}


class ConfIn(BaseModel):
    entry_id: int = Field(0, ge=0)
    exit_id: int = Field(0, ge=0)
    tunnel_id: int = Field(0, ge=0)
    direct_id: int = Field(0, ge=0)          # (1.69.257) the optional second entry with a direct address: a foreign server that is an entry and the way out at once
    title: str = Field("Nikatel Shop", min_length=1, max_length=40)
    address: str = Field("", max_length=253)


@router.put("/l2tp/config")
def put_config(body: ConfIn):
    """Choose the entry (an Iran server), the exit (a foreign server), the tunnel between them, the optional direct server and the connection name the customer sees."""
    c = _wrap(l2tp.set_conf, body.entry_id, body.exit_id, body.tunnel_id, body.title, body.address, body.direct_id)
    _drop()
    return {"ok": True, "conf": c}


class NewIn(BaseModel):
    name: str = Field(min_length=3, max_length=24)
    days: int = Field(30, ge=1, le=l2tp.MAX_DAYS)
    devices: int = Field(1, ge=1, le=10)
    down: float | None = Field(None, ge=0, le=l2tp.MAX_MBIT)          # Mbit/s, 0 or empty = no limit
    up: float | None = Field(None, ge=0, le=l2tp.MAX_MBIT)
    gb: float | None = Field(None, ge=0, le=l2tp.MAX_GB)              # the traffic in GB (download + upload), 0 or empty = no limit
    password: str = Field("", max_length=64)                          # empty = the manager makes one
    note: str = Field("", max_length=80)


@router.post("/l2tp/accounts", status_code=201)
def add_account(body: NewIn):
    """A new account. The answer carries its password once; it can be read again from the account's details."""
    r = _wrap(l2tp.add, body.name, body.days, body.devices, body.down, body.up, body.password, body.note, body.gb)
    _changed()
    return {"ok": True, **r}


class RenewIn(BaseModel):
    days: int = Field(30, ge=1, le=l2tp.MAX_DAYS)
    reset_usage: bool = False                                          # a new period: the traffic is counted from zero again


@router.post("/l2tp/accounts/{name}/renew")
def renew_account(name: str, body: RenewIn):
    """Add days to an account, from today or from its end date when that is later; an account that was switched off comes back, and the traffic can start again from zero."""
    r = _wrap(l2tp.renew, name, body.days, body.reset_usage)
    _changed()
    return {"ok": True, **r}


class ChangeIn(BaseModel):
    password: str = Field("", max_length=64)
    devices: int | None = Field(None, ge=1, le=10)
    down: float | None = Field(None, ge=0, le=l2tp.MAX_MBIT)
    up: float | None = Field(None, ge=0, le=l2tp.MAX_MBIT)
    disabled: bool | None = None
    note: str | None = Field(None, max_length=80)
    gb: float | None = Field(None, ge=0, le=l2tp.MAX_GB)              # the traffic in GB, 0 = no limit
    reset_usage: bool = False                                          # count the traffic from zero again
    expires: int | None = None                                         # the end date, unix seconds


@router.put("/l2tp/accounts/{name}")
def change_account(name: str, body: ChangeIn):
    """Change what is given and nothing else: the password, the devices, the speed (0 = no limit), the traffic in GB (0 = no limit), the end date, on or off, the note; a speed change reaches the devices that are online at once."""
    r = _wrap(l2tp.change, name, body.password, body.devices, body.down, body.up, body.disabled, body.note, body.gb, body.reset_usage, body.expires)
    _changed()
    return {"ok": True, **r}


@router.delete("/l2tp/accounts/{name}")
def delete_account(name: str):
    """Delete an account; its sessions are cut."""
    r = _wrap(l2tp.remove, name)
    _changed()
    return {"ok": True, **r}


@router.post("/l2tp/accounts/{name}/kick")
def kick_account(name: str):
    """Disconnect the account's devices now (it can connect again)."""
    r = _wrap(l2tp.kick, name)
    _changed()
    return {"ok": True, **r}


@router.get("/l2tp/accounts/{name}/profile")
def account_profile(name: str):
    """What a customer needs to connect, and the message to send: the connection name, the address, the user, the password and the shared key."""
    return {"ok": True, **_wrap(l2tp.profile, name)}


# ------------------------------------------------------------------ the shop side (1.69.245): what the Mini App sells and what it sold
@router.get("/l2tp/shop", dependencies=[Depends(_shop_licensed)])
def shop():
    """Who sees L2TP in the Mini App, the plans it sells (the switched-off ones too) and the money it made: income today, in 7 days, in 30 days, and the last orders."""
    return {"ok": True, **_wrap(l2tp_shop.overview)}


class StageIn(BaseModel):
    stage: str = Field(max_length=12)


@router.put("/l2tp/shop/stage", dependencies=[Depends(_shop_licensed)])
def shop_stage(body: StageIn):
    """Who sees L2TP in the Mini App: off, admins only, admins and partners, or everybody. It takes effect for the customers at once."""
    return {"ok": True, "stage": _wrap(l2tp_shop.set_stage, body.stage)}


class PlanIn(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    days: int = Field(30, ge=1, le=l2tp.MAX_DAYS)
    devices: int = Field(1, ge=1, le=10)
    gb: float | None = Field(None, ge=0, le=l2tp.MAX_GB)              # the traffic, 0 or empty = no limit
    down: float | None = Field(None, ge=0, le=l2tp.MAX_MBIT)
    up: float | None = Field(None, ge=0, le=l2tp.MAX_MBIT)
    price: int = Field(0, ge=0, le=l2tp_shop.MAX_PRICE)               # toman; 0 = free (a test plan)
    descr: str = Field("", max_length=500)
    active: bool = True
    sort: int = Field(0, ge=-1000, le=1000)


@router.post("/l2tp/plans", status_code=201, dependencies=[Depends(_shop_licensed)])
def add_plan(body: PlanIn):
    """A new plan for the Mini App. It is on sale at once when it is switched on and the Mini App's stage lets the buyer see L2TP."""
    return {"ok": True, "plans": _wrap(l2tp_shop.plan_save, None, body.title, body.days, body.devices, body.gb, body.down, body.up, body.price, body.descr, body.active, body.sort)}


@router.put("/l2tp/plans/{pid}", dependencies=[Depends(_shop_licensed)])
def change_plan(pid: int, body: PlanIn):
    """Change a plan. The Mini App renews an account at its plan's CURRENT price, days and traffic: the change reaches the next purchase and every renewal of the accounts bought on this plan."""
    return {"ok": True, "plans": _wrap(l2tp_shop.plan_save, pid, body.title, body.days, body.devices, body.gb, body.down, body.up, body.price, body.descr, body.active, body.sort)}


class PlanOnIn(BaseModel):
    on: bool


@router.put("/l2tp/plans/{pid}/active", dependencies=[Depends(_shop_licensed)])
def plan_active(pid: int, body: PlanOnIn):
    """Switch a plan on or off (an off plan cannot be bought, and the accounts bought on it cannot be renewed in the Mini App)."""
    return {"ok": True, "plans": _wrap(l2tp_shop.plan_active, pid, body.on)}


@router.put("/l2tp/plans/{pid}/soldout", dependencies=[Depends(_shop_licensed)])
def plan_soldout(pid: int, body: PlanOnIn):
    """Mark a plan sold out or available again. A sold-out plan stays in the Mini App's list as «ناموجود»: nobody can buy it, but the accounts bought on it can still be renewed."""
    return {"ok": True, "plans": _wrap(l2tp_shop.plan_soldout, pid, body.on)}


@router.delete("/l2tp/plans/{pid}", dependencies=[Depends(_shop_licensed)])
def delete_plan(pid: int):
    """Delete a plan nobody bought; a plan with accounts cannot be deleted."""
    return {"ok": True, "plans": _wrap(l2tp_shop.plan_delete, pid)}
