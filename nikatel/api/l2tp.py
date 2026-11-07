"""The L2TP tab of Network (services/l2tp.py): the accounts of the L2TP entry, who is online, the state of the entry, the exit and the tunnel between them, and the text a customer is sent. Only the owner's sign-in or an `admin` token."""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import l2tp
from ..ssh import SSHError

router = APIRouter(tags=["l2tp"], dependencies=[Depends(current_user)])

_STATE: dict = {"at": 0.0, "data": None}          # (the page asks every few seconds; the entry is read over SSH, so an answer is kept for 10 s and dropped at once by every change)
STATE_TTL = 10.0


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except SSHError as e:
        raise HTTPException(409 if "read-only" in str(e) else 502, str(e))


def _drop():
    _STATE["at"] = 0.0
    _STATE["data"] = None


@router.get("/l2tp")
def overview(fresh: bool = False):
    """The entry (services, load, pool), its accounts without passwords, the sessions, the exit and the tunnel; each part says why when it cannot be read."""
    if fresh or _STATE["data"] is None or time.time() - _STATE["at"] > STATE_TTL:
        _STATE["data"] = l2tp.state()
        _STATE["at"] = time.time()

    return {"ok": True, **_STATE["data"]}


@router.get("/l2tp/config")
def get_config():
    """Which servers carry the product, the connection name the customer sees, and the servers and tunnels to choose from."""
    return {"ok": True, "conf": l2tp.conf(), "choices": l2tp.choices()}


class ConfIn(BaseModel):
    entry_id: int = Field(0, ge=0)
    exit_id: int = Field(0, ge=0)
    tunnel_id: int = Field(0, ge=0)
    title: str = Field("Nikatel Shop", min_length=1, max_length=40)
    address: str = Field("", max_length=253)


@router.put("/l2tp/config")
def put_config(body: ConfIn):
    """Choose the entry (an Iran server), the exit (a foreign server), the tunnel between them and the connection name the customer sees."""
    c = _wrap(l2tp.set_conf, body.entry_id, body.exit_id, body.tunnel_id, body.title, body.address)
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
    _drop()
    return {"ok": True, **r}


class RenewIn(BaseModel):
    days: int = Field(30, ge=1, le=l2tp.MAX_DAYS)
    reset_usage: bool = False                                          # a new period: the traffic is counted from zero again


@router.post("/l2tp/accounts/{name}/renew")
def renew_account(name: str, body: RenewIn):
    """Add days to an account, from today or from its end date when that is later; an account that was switched off comes back, and the traffic can start again from zero."""
    r = _wrap(l2tp.renew, name, body.days, body.reset_usage)
    _drop()
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
    _drop()
    return {"ok": True, **r}


@router.delete("/l2tp/accounts/{name}")
def delete_account(name: str):
    """Delete an account; its sessions are cut."""
    r = _wrap(l2tp.remove, name)
    _drop()
    return {"ok": True, **r}


@router.post("/l2tp/accounts/{name}/kick")
def kick_account(name: str):
    """Disconnect the account's devices now (it can connect again)."""
    r = _wrap(l2tp.kick, name)
    _drop()
    return {"ok": True, **r}


@router.get("/l2tp/accounts/{name}/profile")
def account_profile(name: str):
    """What a customer needs to connect, and the message to send: the connection name, the address, the user, the password and the shared key."""
    return {"ok": True, **_wrap(l2tp.profile, name)}
