from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import settings as st
from ..services import shopbot
from ..services import shopbot_admin as adm
from ..ssh import SSHError


def _licensed():
    from ..services import license

    try:
        license.require("shopbot")
    except ValueError as e:
        raise HTTPException(402, str(e))


router = APIRouter(tags=["shopbot"], dependencies=[Depends(current_user), Depends(_licensed)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))


_INFO: dict = {"at": 0.0, "data": None}


@router.get("/shopbot")
def info(fresh: bool = False):
    """The overview of the bot (one SSH call and several database queries on the bot server): kept for a minute, so opening the page again and again costs nothing."""
    import time as _t

    if fresh or _INFO["data"] is None or _t.time() - _INFO["at"] > 60:
        _INFO["data"] = _wrap(shopbot.info)
        _INFO["at"] = _t.time()

    return {"ok": True, **_INFO["data"]}


@router.get("/shopbot/telegram")
def telegram():
    """The bot's webhook as Telegram sees it (read on the bot server): address, pending updates, last error."""
    return {"ok": True, **_wrap(shopbot.webhook_info)}


@router.get("/shopbot/stats")
def stats():
    """Counts and sums of the bot: users, orders, payments, sales by day - read from its database."""
    return {"ok": True, **_wrap(shopbot.stats)}


class States(BaseModel):
    changes: dict


@router.put("/shopbot/states")
def states(body: States):
    """Switch the bot's feature flags (BOT_STATES) on or off; each change is written to the bot's settings document."""
    return {"ok": True, "states": _wrap(shopbot.set_states, body.changes)}


class CronIn(BaseModel):
    jobs: list[dict]


@router.get("/shopbot/cron")
def cron_get():
    """The scheduled jobs of the bot on its server (the cron file) and whether they are paused."""
    return {"ok": True, **_wrap(shopbot.cron_jobs)}


@router.put("/shopbot/cron")
def cron_put(body: CronIn):
    """Write the bot's cron jobs (pause, resume, change the schedule); the previous file is kept."""
    return {"ok": True, **_wrap(shopbot.cron_set, body.jobs)}

@router.get("/shopbot/partners")
def partners(q: str = "", page: int = 0, sort: str = "recent", limit: int = 0):
    """The resellers (partners) of the bot with their users, wallet and sales, searched and paged."""
    return {"ok": True, **_wrap(shopbot.partners, q, page, sort, limit)}


@router.get("/shopbot/userlist")
def userlist(q: str = "", kind: str = "all", page: int = 0, limit: int = 0):
    """The users of the bot, paged, filtered by kind (all, active, blocked, with balance) and searched by any word."""
    return {"ok": True, **_wrap(shopbot.users_page, q, kind, page, limit)}


@router.get("/shopbot/users")
def users(q: str = ""):
    """Find users of the bot by id, name, username or phone (the quick search of the user windows)."""
    return {"ok": True, "users": _wrap(shopbot.find_users, q)}


class UserAct(BaseModel):
    action: str
    userid: str
    value: float | int | str | None = None


@router.post("/shopbot/users/action")
def user_action(body: UserAct):
    """One admin action on a user of the bot: block, unblock, add to the wallet, set a note - exactly what the bot's admin menu does."""
    return {"ok": True, "user": _wrap(shopbot.user_action, body.action, body.userid, body.value)}


@router.get("/shopbot/table/{name}")
def table(name: str, page: int = 0, q: str = "", limit: int = 0):
    """Rows of one table of the bot's database, paged and searched (read only; the table names are a fixed list)."""
    return {"ok": True, **_wrap(shopbot.table, name, page, q, limit)}


class Query(BaseModel):
    sql: str


@router.post("/shopbot/query")
def query(body: Query):
    """Run one read-only SQL statement (SELECT or SHOW) on the bot's database and return the rows."""
    rows = _wrap(shopbot.query, body.sql)
    return {"ok": True, "rows": rows, "columns": list(rows[0].keys()) if rows else []}


@router.post("/shopbot/action/{act}")
def action(act: str):
    """A maintenance action on the bot server: restart PHP, dump the database, clear the logs, set the webhook."""
    return {"ok": True, **_wrap(shopbot.action, act)}


@router.post("/shopbot/import-stage")
def import_stage():
    """Copy every table of the staging database over the live one (the live one is dumped first; the broadcast queues are left out)."""
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
    """Where the bot runs: host, SSH user and port, the bot directory, the env file, the public URL."""
    return {"ok": True, **st.get("shopbot")}


@router.put("/settings/shopbot")
def settings_put(body: BotSettings):
    """Save where the bot runs; the host and the paths are checked for shape before they are kept."""
    import re

    if body.host and not re.match(r"^[A-Za-z0-9.\-]{1,120}$", body.host):
        raise HTTPException(400, "the host looks like an IP or a hostname")

    if not re.match(r"^/var/www/[A-Za-z0-9._-]{1,40}$", body.dir) or not re.match(r"^/opt/[A-Za-z0-9._-]{1,40}/db\.env$", body.env):
        raise HTTPException(400, "the bot directory is /var/www/<name> and the env file /opt/<name>/db.env")

    if body.public_url and not re.match(r"^https://[A-Za-z0-9.\-]+(/[A-Za-z0-9._\-/]*)?$", body.public_url):
        raise HTTPException(400, "the public URL must start with https://")

    st.put("shopbot", body.model_dump())
    return settings_get()


# ------------------------------------------------------------------ the shop itself: payments, orders, users
@router.get("/shopbot/pays")
def pays(state: str = "pending", page: int = 0, q: str = "", limit: int = 0):
    """Payments of the bot by state (pending, sent, approved, declined), paged and searched."""
    return {"ok": True, **_wrap(adm.pays, state, page, q, limit)}


class Reason(BaseModel):
    reason: str = Field("", max_length=500)


@router.post("/shopbot/pays/{pay_id}/approve")
def pay_approve(pay_id: int):
    """Approve a payment the way the bot does: the wallet is credited, the user and the admins are told."""
    return {"ok": True, **_wrap(adm.pay_approve, pay_id)}


@router.post("/shopbot/pays/{pay_id}/decline")
def pay_decline(pay_id: int, body: Reason):
    """Decline a payment with a reason; the user is told."""
    return {"ok": True, **_wrap(adm.pay_decline, pay_id, body.reason)}


@router.get("/shopbot/orders")
def orders(q: str = "", page: int = 0, active: bool = False, limit: int = 0):
    """Orders of the bot (the services sold), paged and searched; `active` lists only the ones still running."""
    return {"ok": True, **_wrap(adm.orders, q, page, active, limit)}


class Active(BaseModel):
    active: bool


@router.put("/shopbot/orders/{order_id}/status")
def order_status(order_id: int, body: Active):
    """Mark an order active or inactive."""
    _wrap(adm.order_status, order_id, body.active)
    return {"ok": True}


@router.get("/shopbot/users/{userid}")
def user_detail(userid: str):
    """One user of the bot in full: profile, wallet, orders, payments, tickets, referrals."""
    return {"ok": True, **_wrap(adm.user_detail, userid)}


class Text(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/shopbot/users/{userid}/message")
def user_message(userid: str, body: Text):
    """Send a Telegram message to this user through the bot (the token stays on the bot server)."""
    return {"ok": True, **_wrap(adm.user_message, userid, body.text)}


# ------------------------------------------------------------------ catalogue: plans, categories, servers
class Fields(BaseModel):
    fields: dict


@router.get("/shopbot/plans")
def plans():
    """The plans the bot sells, with the categories and the servers they belong to."""
    return {"ok": True, "plans": _wrap(adm.plans), "categories": _wrap(adm.categories), "servers": _wrap(adm.servers)}


@router.get("/shopbot/plan-inbounds")
def plan_inbounds(server_id: int):
    """The inbounds of the panel behind a bot server, to pick for a plan."""
    return {"ok": True, **_wrap(adm.plan_inbounds, server_id)}


@router.get("/shopbot/plans/{plan_id}/accounts")
def plan_accounts(plan_id: int):
    """How many accounts were bought from this plan (for 'apply to the accounts already bought')."""
    return {"ok": True, "count": _wrap(adm.plan_accounts, plan_id)}


@router.post("/shopbot/plans/{plan_id}/apply", status_code=202)
def plan_apply(plan_id: int):
    """Apply the plan's inbounds to the accounts already bought from it, as a job on the bot server; follow /shopbot/jobs/{jid}."""
    return {"ok": True, **_wrap(adm.plan_apply_start, plan_id)}


@router.get("/shopbot/jobs/{jid}")
def plan_job(jid: str):
    """How far a bot-side job is (the apply of a plan): done, progress, errors."""
    return {"ok": True, **_wrap(adm.job, jid)}


@router.post("/shopbot/plans", status_code=201)
def plan_add(body: Fields):
    """Add a plan (the fields of the bot's own form: title, category, server, time, volume, price, stock)."""
    return {"ok": True, "id": _wrap(adm.plan_save, None, body.fields)}


@router.put("/shopbot/plans/{plan_id}")
def plan_edit(plan_id: int, body: Fields):
    """Change a plan's fields."""
    return {"ok": True, "id": _wrap(adm.plan_save, plan_id, body.fields)}


@router.delete("/shopbot/plans/{plan_id}")
def plan_delete(plan_id: int):
    """Delete a plan (the accounts bought from it stay)."""
    _wrap(adm.plan_delete, plan_id)
    return {"ok": True}


class CategoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=50)
    parent: int = 0
    active: bool = True


@router.get("/shopbot/categories")
def categories():
    """The categories of the bot's menu, with their parent and whether they are shown."""
    return {"ok": True, "categories": _wrap(adm.categories)}


@router.post("/shopbot/categories", status_code=201)
def category_add(body: CategoryIn):
    """Add a category to the bot's menu."""
    return {"ok": True, "id": _wrap(adm.category_save, None, body.title, body.parent, int(body.active))}


@router.put("/shopbot/categories/{cat_id}")
def category_edit(cat_id: int, body: CategoryIn):
    """Rename, move or hide a category."""
    return {"ok": True, "id": _wrap(adm.category_save, cat_id, body.title, body.parent, int(body.active))}


@router.delete("/shopbot/categories/{cat_id}")
def category_delete(cat_id: int):
    """Delete a category (its plans must be moved first)."""
    _wrap(adm.category_delete, cat_id)
    return {"ok": True}


class ServerIn(BaseModel):
    info: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)


@router.get("/shopbot/servers")
def servers():
    """The servers (panels) the bot sells from, and the panels this panel knows, to add from."""
    return {"ok": True, "servers": _wrap(adm.servers), "panels": _wrap(adm.panels_known)}


@router.post("/shopbot/servers", status_code=201)
def server_add(body: ServerIn):
    """Add a server the bot sells from (its panel login and config as the bot's own form)."""
    return {"ok": True, "id": _wrap(adm.server_save, None, body.info, body.config)}


@router.put("/shopbot/servers/{server_id}")
def server_edit(server_id: int, body: ServerIn):
    """Change a bot server's info or config."""
    return {"ok": True, "id": _wrap(adm.server_save, server_id, body.info, body.config)}


@router.delete("/shopbot/servers/{server_id}")
def server_delete(server_id: int):
    """Delete a bot server (the plans on it must be moved first)."""
    _wrap(adm.server_delete, server_id)
    return {"ok": True}


class FromPanel(BaseModel):
    panel_id: int
    title: str = Field("", max_length=200)
    ucount: int = 1000


@router.post("/shopbot/servers/from-panel", status_code=201)
def server_from_panel(body: FromPanel):
    """Add a bot server from a panel this panel already manages (the login is copied, nothing retyped)."""
    return {"ok": True, "id": _wrap(adm.server_from_panel, body.panel_id, body.title, body.ucount)}


# ------------------------------------------------------------------ discounts, gift codes, broadcasts
@router.get("/shopbot/discounts")
def discounts():
    """The discount codes of the bot."""
    return {"ok": True, "discounts": _wrap(adm.discounts)}


@router.post("/shopbot/discounts", status_code=201)
def discount_add(body: Fields):
    """Add a discount code (the fields of the bot's own form)."""
    return {"ok": True, "id": _wrap(adm.discount_save, None, body.fields)}


@router.put("/shopbot/discounts/{disc_id}")
def discount_edit(disc_id: int, body: Fields):
    """Change a discount code."""
    return {"ok": True, "id": _wrap(adm.discount_save, disc_id, body.fields)}


@router.put("/shopbot/discounts/{disc_id}/active")
def discount_active(disc_id: int, body: Active):
    """Switch a discount code on or off."""
    _wrap(adm.discount_active, disc_id, body.active)
    return {"ok": True}


@router.delete("/shopbot/discounts/{disc_id}")
def discount_delete(disc_id: int):
    """Delete a discount code."""
    _wrap(adm.discount_delete, disc_id)
    return {"ok": True}


class GiftIn(BaseModel):
    code: str = Field("", max_length=40)
    amount: int
    max_uses: int = 1
    days: int = 0
    expires: int = 0
    note: str = Field("", max_length=190)


@router.get("/shopbot/gifts")
def gifts():
    """The gift codes (wallet credit) of the bot, with their uses."""
    return {"ok": True, "gifts": _wrap(adm.gifts)}


@router.post("/shopbot/gifts", status_code=201)
def gift_add(body: GiftIn):
    """Make a gift code: amount, how many uses, days, note, expiry."""
    return {"ok": True, "id": _wrap(adm.gift_save, body.code, body.amount, body.max_uses, body.days, body.note, body.expires)}


@router.delete("/shopbot/gifts/{gift_id}")
def gift_delete(gift_id: int):
    """Delete a gift code."""
    _wrap(adm.gift_delete, gift_id)
    return {"ok": True}


class BroadcastIn(BaseModel):
    title: str = Field("", max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    group: str = "all"


@router.get("/shopbot/broadcasts")
def broadcasts():
    """The broadcasts of the bot (queued, running, done) and the groups they can go to."""
    return {"ok": True, "broadcasts": _wrap(adm.broadcasts), "groups": adm.GROUPS}


@router.post("/shopbot/broadcasts", status_code=201)
def broadcast_add(body: BroadcastIn):
    """Queue a broadcast to a group of users; the bot's own sender delivers it."""
    return {"ok": True, "id": _wrap(adm.broadcast_create, body.title, body.text, body.group)}


@router.post("/shopbot/broadcasts/{bid}/cancel")
def broadcast_cancel(bid: int):
    """Cancel a queued or running broadcast."""
    _wrap(adm.broadcast_cancel, bid)
    return {"ok": True}


# ------------------------------------------------------------------ tickets
@router.get("/shopbot/tickets")
def tickets(state: str = "open", page: int = 0, limit: int = 0):
    """Support tickets of the bot by state, paged."""
    return {"ok": True, **_wrap(adm.tickets, state, page, limit)}


@router.get("/shopbot/tickets/{chat_id}")
def ticket_messages(chat_id: int):
    """The messages of one ticket (the conversation with the user)."""
    return {"ok": True, "messages": _wrap(adm.ticket_messages, chat_id)}


@router.post("/shopbot/tickets/{chat_id}/reply")
def ticket_reply(chat_id: int, body: Text):
    """Reply to a ticket through the bot; the user gets it in Telegram."""
    return {"ok": True, **_wrap(adm.ticket_reply, chat_id, body.text)}


@router.post("/shopbot/tickets/{chat_id}/close")
def ticket_close(chat_id: int):
    """Close a ticket."""
    _wrap(adm.ticket_close, chat_id)
    return {"ok": True}


# ------------------------------------------------------------------ texts, settings documents, apps, top-ups, backups, tools
@router.get("/shopbot/texts")
def texts():
    """Every text and button of the bot, with the overrides this panel wrote."""
    return {"ok": True, **_wrap(adm.texts)}


class TextChanges(BaseModel):
    kind: str = Field(pattern=r"^(text|button)$")
    changes: dict


@router.put("/shopbot/texts")
def texts_set(body: TextChanges):
    """Override texts or buttons of the bot (written as JSON next to the bot; one include line in its config, checked with php -l)."""
    return {"ok": True, **_wrap(adm.texts_set, body.kind, body.changes)}


@router.get("/shopbot/settings")
def bot_settings():
    """The bot's settings documents (feature switches, grouped) and its payment keys (masked)."""
    return {"ok": True, **_wrap(adm.states_structured), "payment_keys": _wrap(adm.payment_keys_get)}


@router.put("/shopbot/settings")
def bot_settings_set(body: States):
    """Change the bot's feature switches."""
    return {"ok": True, "states": _wrap(adm.states_set, body.changes)}


@router.put("/shopbot/payment-keys")
def payment_keys_set(body: States):
    """Change the bot's payment keys (card number, gateway keys); the previous document is kept."""
    return {"ok": True, "payment_keys": _wrap(adm.payment_keys_set, body.changes)}


class AppIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    link: str = Field(min_length=8, max_length=250)
    status: bool = True


@router.get("/shopbot/apps")
def apps():
    """The client apps the bot offers for download."""
    return {"ok": True, "apps": _wrap(adm.apps)}


@router.post("/shopbot/apps", status_code=201)
def app_add(body: AppIn):
    """Add an app (title, link, shown or not)."""
    return {"ok": True, "id": _wrap(adm.app_save, None, body.title, body.link, int(body.status))}


@router.put("/shopbot/apps/{app_id}")
def app_edit(app_id: int, body: AppIn):
    """Change an app."""
    return {"ok": True, "id": _wrap(adm.app_save, app_id, body.title, body.link, int(body.status))}


@router.delete("/shopbot/apps/{app_id}")
def app_delete(app_id: int):
    """Delete an app."""
    _wrap(adm.app_delete, app_id)
    return {"ok": True}


class TopupIn(BaseModel):
    kind: str = Field(pattern=r"^(volume|days)$")
    price: int
    amount: float


@router.get("/shopbot/topups")
def topups():
    """The top-up prices of the bot (wallet credit, and traffic / days packages)."""
    return {"ok": True, **_wrap(adm.topups)}


@router.post("/shopbot/topups", status_code=201)
def topup_add(body: TopupIn):
    """Add a top-up price."""
    return {"ok": True, "id": _wrap(adm.topup_save, body.kind, body.price, body.amount)}


@router.delete("/shopbot/topups/{kind}/{row_id}")
def topup_delete(kind: str, row_id: int):
    """Delete a top-up price."""
    _wrap(adm.topup_delete, kind, row_id)
    return {"ok": True}


@router.get("/shopbot/backups")
def backups():
    """The database dumps kept on the bot server (made by Dump and before every import)."""
    return {"ok": True, "backups": _wrap(adm.backups)}


@router.post("/shopbot/commands")
def set_commands():
    """Set the bot's command menu in Telegram (the / commands)."""
    return {"ok": True, **_wrap(adm.set_commands)}


@router.get("/shopbot/miniapp")
def miniapp():
    """Whether the Mini App is installed next to the bot, and which version."""
    return {"ok": True, **_wrap(adm.miniapp_status)}


@router.post("/shopbot/miniapp/deploy")
def miniapp_deploy():
    """Install or update the Mini App next to the bot from this repository (php -l before the old file is replaced; the previous copy is kept)."""
    return {"ok": True, **_wrap(adm.miniapp_deploy)}
