from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..services import settings as st
from ..services import shopbot
from ..ssh import SSHError

router = APIRouter(tags=["shopbot"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))


@router.get("/shopbot")
def info():
    return {"ok": True, **_wrap(shopbot.info)}


@router.get("/shopbot/telegram")
def telegram():
    return {"ok": True, **_wrap(shopbot.webhook_info)}


@router.get("/shopbot/stats")
def stats():
    return {"ok": True, **_wrap(shopbot.stats)}


class States(BaseModel):
    changes: dict


@router.put("/shopbot/states")
def states(body: States):
    return {"ok": True, "states": _wrap(shopbot.set_states, body.changes)}


@router.get("/shopbot/users")
def users(q: str = ""):
    return {"ok": True, "users": _wrap(shopbot.find_users, q)}


class UserAct(BaseModel):
    action: str
    userid: str
    value: float | int | str | None = None


@router.post("/shopbot/users/action")
def user_action(body: UserAct):
    return {"ok": True, "user": _wrap(shopbot.user_action, body.action, body.userid, body.value)}


@router.get("/shopbot/table/{name}")
def table(name: str, page: int = 0, q: str = ""):
    return {"ok": True, **_wrap(shopbot.table, name, page, q)}


class Query(BaseModel):
    sql: str


@router.post("/shopbot/query")
def query(body: Query):
    rows = _wrap(shopbot.query, body.sql)
    return {"ok": True, "rows": rows, "columns": list(rows[0].keys()) if rows else []}


@router.post("/shopbot/action/{act}")
def action(act: str):
    return {"ok": True, **_wrap(shopbot.action, act)}


@router.post("/shopbot/import-stage")
def import_stage():
    return {"ok": True, **_wrap(shopbot.import_stage)}


class BotSettings(BaseModel):
    host: str
    ssh_user: str = "root"
    ssh_port: int = 22
    dir: str = "/var/www/nikabot"
    env: str = "/opt/nikabot/db.env"
    public_url: str = ""


@router.get("/settings/shopbot")
def settings_get():
    return {"ok": True, **st.get("shopbot")}


@router.put("/settings/shopbot")
def settings_put(body: BotSettings):
    import re

    if body.host and not re.match(r"^[A-Za-z0-9.\-]{1,120}$", body.host):
        raise HTTPException(400, "the host looks like an IP or a hostname")

    if not re.match(r"^/var/www/[A-Za-z0-9._-]{1,40}$", body.dir) or not re.match(r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$", body.env):
        raise HTTPException(400, "the bot directory is /var/www/<name> and the env file /opt/<name>/db.env")

    if body.public_url and not re.match(r"^https://[A-Za-z0-9.\-]+(/[A-Za-z0-9._\-/]*)?$", body.public_url):
        raise HTTPException(400, "the public URL must start with https://")

    st.put("shopbot", body.model_dump())
    return settings_get()
