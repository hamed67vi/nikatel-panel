"""The shop bot's administration from the panel: payments, orders, users, plans, categories, the panels it sells from,
discounts, gift codes, broadcasts, tickets, texts, payment settings, apps, top-up prices, backups.

Everything goes through the two reviewed scripts of `shopbot`: `query` (one SELECT) and `write` (one UPDATE / INSERT /
DELETE). Text travels as FROM_BASE64("...") so Persian, newlines and quotes never touch the SQL; messages to Telegram
go through the bot's own token on the bot server (the panel never holds it)."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path

from ..db import session
from ..log import get_logger
from ..ssh import SSHError, run_action, run_script
from . import shopbot
from .events import emit

log = get_logger("shopbot")
PAGE = 40
GROUPS = {"all": "every user", "has": "users with an active subscription", "none": "users without an active subscription", "agents": "agents"}
AUDIENCES = ("all", "agents", "non_agents", "buyers", "non_buyers", "has_active_order", "no_active_order", "user")
TICKET_OPEN, TICKET_CLOSED = 1, 2
BC_DRAFT, BC_QUEUED, BC_RUNNING, BC_DONE, BC_CANCELLED = 0, 1, 2, 3, 4
PAYMENT_KEYS = ("bankAccount", "holderName", "zarinpal", "nextpay", "nowpayment", "tronwallet")
# the switches of BOT_STATES, grouped the way the operator thinks about them: (key, label, kind, hint)
STATE_GROUPS = [
    ("Sales", [("botState", "Whole bot", "onoff", "off = the bot answers nobody but the admins"), ("sellState", "Selling", "onoff", "off = browsing only, no purchase"),
               ("renewAccountState", "Renewing", "onoff", ""), ("testAccount", "Free test accounts", "onoff", ""), ("plandelkhahState", "Custom plans (any days / GB)", "onoff", ""),
               ("increaseVolumeState", "Buy extra volume", "onoff", ""), ("increaseTimeState", "Buy extra days", "onoff", ""), ("switchLocationState", "Switch location", "onoff", ""),
               ("changeProtocolState", "Change protocol", "onoff", ""), ("agencyState", "Agents (resellers)", "onoff", ""), ("agencyPlanDiscount", "Agent discount %", "number", "percent off every plan for agents")]),
    ("Wallet and payment", [("walletState", "Wallet", "onoff", ""), ("walletAmounts", "Top-up amounts", "text", "comma separated, Toman"), ("cartToCartState", "Card to card", "onoff", ""),
                            ("cartToCartAutoAcceptState", "Auto-accept card to card", "onoff", "accept receipts without an admin"), ("cartToCartAutoAcceptType", "Auto-accept type", "text", ""),
                            ("cartToCartAutoAcceptTime", "Auto-accept after (min)", "number", ""), ("aiReceiptState", "AI receipt check", "onoff", "the panel's AI reads the receipt picture"),
                            ("zarinpal", "ZarinPal gateway", "onoff", ""), ("nextpay", "NextPay gateway", "onoff", ""), ("nowPaymentWallet", "NowPayments (crypto)", "onoff", ""),
                            ("nowPaymentOther", "NowPayments for purchases", "onoff", ""), ("tronWallet", "TRON wallet payments", "onoff", ""), ("weSwapState", "WeSwap", "onoff", ""),
                            ("USDRate", "USD rate (Toman)", "number", ""), ("TRXRate", "TRX rate (Toman)", "number", "")]),
    ("Prices", [("gbPrice", "Price per GB (custom plans)", "number", "Toman"), ("dayPrice", "Price per day (custom plans)", "number", "Toman"), ("cashbackPercent", "Cashback %", "number", "0 = off")]),
    ("Support and channels", [("supportID", "Support ID", "text", "@username shown to users"), ("rewardChannel", "Report channel", "text", "@channel or chat id for sale reports"),
                              ("lockChannel", "Required channel", "text", "users must join it first"), ("reportState", "Reports to channel", "onoff", ""), ("rewaredTime", "Report every (min)", "number", "")]),
    ("Users", [("requirePhone", "Ask for a phone number", "onoff", ""), ("requireIranPhone", "Iranian numbers only", "onoff", ""), ("firstUseState", "Welcome flow for new users", "onoff", ""),
               ("rateLimit", "Rate limit (messages / min)", "number", ""), ("searchState", "Search in subscriptions", "onoff", ""), ("compactMenu", "Compact main menu", "onoff", ""), ("miniAppState", "Mini App button", "onoff", "")]),
    ("Links and configs", [("subLinkState", "Subscription links", "onoff", ""), ("configLinkState", "Config links", "onoff", ""), ("qrSubState", "QR for subscription", "onoff", ""), ("qrConfigState", "QR for configs", "onoff", ""),
                           ("updateConfigLinkState", "Refresh config link button", "onoff", ""), ("updateConnectionState", "Update connection button", "onoff", ""), ("renewConfigLinkState", "New link on renew", "onoff", ""),
                           ("sharedExistence", "Shared accounts", "onoff", ""), ("individualExistence", "Individual accounts", "onoff", ""), ("remark", "Account name prefix", "text", "")]),
    ("Extras", [("wheelState", "Lucky wheel", "onoff", ""), ("wheelPrizes", "Wheel prizes", "text", "comma separated, Toman"), ("wheelHours", "Hours between spins", "number", ""), ("giftState", "Gift codes", "onoff", ""),
                ("lotteryState", "Nightly lottery", "onoff", ""), ("lotteryPrize", "Lottery prize", "number", "Toman"), ("onHoldState", "On-hold accounts", "onoff", "the clock starts at first use"),
                ("onHoldDays", "On-hold days", "number", ""), ("renewAutoState", "Auto renew from wallet", "onoff", ""), ("cleanupDays", "Delete expired after (days)", "number", "")]),
]


# ------------------------------------------------------------------ SQL helpers
def lit(v) -> str:
    """A SQL value: numbers as they are, NULL, text as FROM_BASE64 (nothing the scripts' regexes refuse can appear)."""
    if v is None:
        return "NULL"

    if isinstance(v, bool):
        return "1" if v else "0"

    if isinstance(v, (int, float)):
        return str(v)

    return 'FROM_BASE64("' + base64.b64encode(str(v).encode()).decode() + '")'


def num(v, default: int = 0) -> int:
    try:
        return int(float(str(v).strip() or default))
    except ValueError:
        raise ValueError(f"{v!r} is not a number")


def ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", str(name)):
        raise ValueError("bad field name")

    return name


def read(sql: str) -> list[dict]:
    return shopbot.query(sql)


def write(sql: str) -> None:
    out = shopbot.write(sql)

    if "ERROR" in out.split("DONE")[0]:
        raise RuntimeError(out.strip().split("\n")[0][:200])


def last_id(table: str) -> int:
    rows = read(f"SELECT id FROM {ident(table)} ORDER BY id DESC LIMIT 1")
    return int(rows[0]["id"]) if rows else 0


def set_clause(fields: dict) -> str:
    return ", ".join(f"{ident(k)} = {lit(v)}" for k, v in fields.items())


def _tg(method: str, body: dict) -> dict:
    """Telegram through the bot's token on its own server; the body is base64 so any text may travel."""
    c = shopbot.conf()
    out = run_action(shopbot._node(), "bot_tg_json", {"DIR": c["dir"], "METHOD": method, "B64": base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()}, timeout=40)

    try:
        return json.loads(out.strip() or "{}")
    except ValueError:
        return {"ok": False, "description": out.strip()[:200]}


def _event(level: str, text: str, ref: str = "") -> None:
    with session() as db:
        emit(db, level, "shopbot", text, ref)


# ------------------------------------------------------------------ payments (card to card and the others the admin confirms)
def pays(state: str = "pending", page: int = 0, q: str = "", limit: int = 0) -> dict:
    # the bot's states: pending = asked, no receipt yet; have_sent = receipt sent, waits for an admin; approved / declined; expired_*
    where = {"confirm": 'p.state = "have_sent"', "pending": 'p.state = "pending"', "approved": 'p.state = "approved"', "declined": 'p.state = "declined"', "all": "1 = 1"}.get(state, 'p.state = "have_sent"')
    e = re.sub(r"[;'\"`\\%]", "", str(q or ""))

    if e:
        where += f' AND (p.user_id = "{e}" OR u.name LIKE "%{e}%" OR u.username LIKE "%{e}%" OR p.hash_id LIKE "%{e}%")'

    per = max(5, min(100, int(limit))) if limit else PAGE          # the page holds what fits the window: the client says how many rows
    total = int(read(f"SELECT COUNT(*) AS n FROM pays p LEFT JOIN users u ON u.userid = p.user_id WHERE {where}")[0]["n"])
    rows = read(f"SELECT p.id, p.hash_id, p.user_id, u.name, u.username, p.price, p.type, p.state, p.request_date, p.plan_id, p.volume, p.day, p.approved_by, "
                f"LEFT(p.description, 200) AS description FROM pays p LEFT JOIN users u ON u.userid = p.user_id WHERE {where} ORDER BY p.id DESC LIMIT {per} OFFSET {max(0, page) * per}")
    cnt = {r["state"]: int(r["c"]) for r in read("SELECT p.state, COUNT(*) AS c FROM pays p GROUP BY p.state")}      # the number on each filter tab
    counts = {"confirm": cnt.get("have_sent", 0), "pending": cnt.get("pending", 0), "approved": cnt.get("approved", 0), "declined": cnt.get("declined", 0), "all": sum(cnt.values())}
    return {"rows": rows, "total": total, "page": max(0, page), "per": per, "counts": counts}


def _pay(pay_id: int) -> dict:
    rows = read(f"SELECT * FROM pays WHERE id = {int(pay_id)}")

    if not rows:
        raise ValueError("payment not found")

    return rows[0]


def _admin_keys_done(p: dict, label: str) -> None:
    """Tell the admins' Telegram messages that this receipt is settled (same look as the bot's own buttons)."""
    keys = {"inline_keyboard": [[{"text": label, "callback_data": "wizwizch"}], [{"text": "🆔 " + str(p.get("hash_id", "")), "callback_data": "noop_" + str(p.get("hash_id", ""))}]]}
    targets = []

    if p.get("chat_id") and p.get("message_id"):
        targets.append((p["chat_id"], p["message_id"]))

    try:
        for m in json.loads(p.get("admins_messages") or "[]"):
            if isinstance(m, dict) and m.get("chat_id") and m.get("message_id"):
                targets.append((m["chat_id"], m["message_id"]))
    except ValueError:
        pass

    for chat, mid in targets[:6]:
        _tg("editMessageReplyMarkup", {"chat_id": chat, "message_id": mid, "reply_markup": keys})


def pay_approve(pay_id: int, admin_id: int = 0) -> dict:
    """What the bot does when an admin presses ✅: state approved, the amount added to the user's wallet, both sides told."""
    p = _pay(pay_id)

    if p.get("state") in ("approved", "declined"):
        raise ValueError(f"this payment was already {p['state']}")

    price, uid = num(p.get("price")), str(p.get("user_id", "")).strip()

    if not re.fullmatch(r"-?\d{1,20}", uid):
        raise ValueError("the payment has no user")

    before = read(f'SELECT COALESCE(wallet, 0) AS w FROM users WHERE userid = "{uid}"')
    old = int(before[0]["w"]) if before else 0
    write(f'UPDATE pays SET state = "approved", approved_by = {int(admin_id)} WHERE id = {int(pay_id)}')
    write(f'UPDATE users SET wallet = COALESCE(wallet, 0) + {price} WHERE userid = "{uid}"')
    _admin_keys_done(p, "✅ تایید شد (پنل)")
    _tg("sendMessage", {"chat_id": uid, "parse_mode": "HTML", "text": f"✅ افزایش حساب شما با موفقیت تایید شد.\n💰 مبلغ <b>{price:,} تومان</b> به کیف پول شما اضافه شد.\n\n"
                                                                     "برای خرید یا تمدید از منوی ربات استفاده کنید و در مرحله پرداخت «پرداخت با موجودی» را بزنید."})
    _event("ok", f"Bot payment #{pay_id} approved from the panel: {price:,} Toman to the wallet of {uid} ({old:,} → {old + price:,}).", uid)
    return {"id": pay_id, "state": "approved", "wallet": old + price}


def pay_decline(pay_id: int, reason: str = "") -> dict:
    p = _pay(pay_id)

    if p.get("state") in ("approved", "declined"):
        raise ValueError(f"this payment was already {p['state']}")

    uid = str(p.get("user_id", "")).strip()
    write(f'UPDATE pays SET state = "declined" WHERE id = {int(pay_id)}')
    _admin_keys_done(p, "❌ رد شد (پنل)")

    if re.fullmatch(r"-?\d{1,20}", uid):
        why = (reason or "").strip()
        _tg("sendMessage", {"chat_id": uid, "parse_mode": "HTML", "text": "❌ درخواست افزایش موجودی شما تایید نشد." + (f"\n\n📝 دلیل: {why}" if why else "") + "\n\nدر صورت اشتباه با پشتیبانی در تماس باشید."})

    _event("warn", f"Bot payment #{pay_id} declined from the panel" + (f": {reason[:80]}" if reason else "."), uid)
    return {"id": pay_id, "state": "declined"}


# ------------------------------------------------------------------ orders (subscriptions sold)
def orders(q: str = "", page: int = 0, only_active: bool = False) -> dict:
    where = "o.status = 1" if only_active else "1 = 1"
    e = re.sub(r"[;'\"`\\%]", "", str(q or ""))

    if e:
        where += f' AND (o.userid = "{e}" OR o.remark LIKE "%{e}%" OR u.name LIKE "%{e}%" OR u.username LIKE "%{e}%")'

    total = int(read(f"SELECT COUNT(*) AS n FROM orders_list o LEFT JOIN users u ON u.userid = o.userid WHERE {where}")[0]["n"])
    rows = read(f"SELECT o.id, o.userid, u.name, u.username, o.remark, o.amount, o.date, o.expire_date, o.status, o.server_id, s.title AS server, o.protocol, o.autoRenew, o.agent_bought "
                f"FROM orders_list o LEFT JOIN users u ON u.userid = o.userid LEFT JOIN server_info s ON s.id = o.server_id WHERE {where} ORDER BY o.id DESC LIMIT {PAGE} OFFSET {max(0, page) * PAGE}")
    return {"rows": rows, "total": total, "page": max(0, page)}


def order_status(order_id: int, active: bool) -> None:
    write(f"UPDATE orders_list SET status = {1 if active else 0} WHERE id = {int(order_id)}")
    _event("info", f"Bot order #{order_id} {'enabled' if active else 'disabled'} from the panel.", "")


# ------------------------------------------------------------------ one user in full
def user_detail(userid: str) -> dict:
    uid = str(userid or "").strip()

    if not re.fullmatch(r"-?\d{1,20}", uid):
        raise ValueError("bad user id")

    u = read(f'SELECT id, userid, name, username, phone, wallet, is_agent, agent_date, discount_percent, step, date, first_start, freetrial, isAdmin, refcode, refered_by FROM users WHERE userid = "{uid}"')

    if not u:
        raise ValueError("user not found")

    return {"user": u[0],
            "orders": read(f'SELECT o.id, o.remark, o.amount, o.date, o.expire_date, o.status, s.title AS server FROM orders_list o LEFT JOIN server_info s ON s.id = o.server_id WHERE o.userid = "{uid}" ORDER BY o.id DESC LIMIT 30'),
            "pays": read(f'SELECT id, price, type, state, request_date FROM pays WHERE user_id = "{uid}" ORDER BY id DESC LIMIT 30'),
            "wheel": read(f'SELECT prize, at FROM wheel_log WHERE userid = "{uid}" ORDER BY id DESC LIMIT 10'),
            "cashback": read(f'SELECT order_amount, cashback, at FROM cashback_log WHERE userid = "{uid}" ORDER BY id DESC LIMIT 10'),
            "referred": read(f'SELECT COUNT(*) AS n FROM users WHERE refered_by = "{uid}"')[0]["n"]}


def user_message(userid: str, text: str) -> dict:
    uid = str(userid or "").strip()

    if not re.fullmatch(r"-?\d{1,20}", uid) or not (text or "").strip():
        raise ValueError("a user id and a text are needed")

    r = _tg("sendMessage", {"chat_id": uid, "text": text.strip()[:3500]})

    if not r.get("ok"):
        raise RuntimeError("Telegram: " + str(r.get("description", "not sent"))[:160])

    _event("info", f"Message sent to bot user {uid} from the panel.", uid)
    return {"sent": True}


# ------------------------------------------------------------------ plans
PLAN_FIELDS = {"title": str, "catid": int, "server_id": int, "days": float, "volume": float, "price": int, "acount": int, "limitip": int, "active": int, "descr": str, "type": str, "protocol": str, "inbound_id": int, "step": int, "custom_sni": str, "fileid": str, "pic": str, "custom_port": int}


def plans() -> list[dict]:
    return read("SELECT p.id, p.title, p.catid, c.title AS category, p.server_id, s.title AS server, p.days, p.volume, p.price, p.acount, p.limitip, p.active, p.descr, p.type, p.protocol, p.inbound_id, p.step, p.date, p.custom_sni, "
                "(SELECT COUNT(*) FROM orders_list o WHERE o.fileid = p.id) AS sold FROM server_plans p LEFT JOIN server_categories c ON c.id = p.catid LEFT JOIN server_info s ON s.id = p.server_id ORDER BY p.catid, p.price, p.id")


def _clean(fields: dict, allowed: dict) -> dict:
    out = {}

    for k, v in (fields or {}).items():
        if k not in allowed:
            continue

        kind = allowed[k]

        if kind is int:
            out[k] = num(v)
        elif kind is float:
            out[k] = float(str(v).strip() or 0)
        else:
            out[k] = str(v if v is not None else "")[:2000]

    return out


def plan_save(plan_id: int | None, fields: dict) -> int:
    f = _clean(fields, PLAN_FIELDS)

    if plan_id is None:
        if not f.get("title"):
            raise ValueError("the plan needs a title")

        f.setdefault("active", 1)
        f.setdefault("step", 5)
        f.setdefault("type", "normal")
        f.setdefault("limitip", 1)
        f.setdefault("protocol", "vless")
        f.setdefault("inbound_id", 0)
        f.setdefault("catid", 0)

        for k in ("fileid", "pic"):   # NOT NULL columns without a default in the bot's schema
            f.setdefault(k, "")

        f.setdefault("custom_port", 0)
        cols = ", ".join(ident(k) for k in f) + ", date"
        vals = ", ".join(lit(v) for v in f.values()) + f", {int(time.time())}"
        write(f"INSERT INTO server_plans ({cols}) VALUES ({vals})")
        pid = last_id("server_plans")
        _event("info", f"Bot plan '{f.get('title')}' added from the panel (#{pid}).", "")
        return pid

    if not f:
        raise ValueError("nothing to change")

    write(f"UPDATE server_plans SET {set_clause(f)} WHERE id = {int(plan_id)}")
    _event("info", f"Bot plan #{plan_id} changed: " + ", ".join(f"{k}={str(v)[:20]}" for k, v in f.items())[:200], "")
    return int(plan_id)


def plan_delete(plan_id: int) -> None:
    write(f"DELETE FROM server_plans WHERE id = {int(plan_id)}")
    _event("warn", f"Bot plan #{plan_id} deleted from the panel.", "")


# ------------------------------------------------------------------ categories
def categories() -> list[dict]:
    return read("SELECT c.id, c.title, c.parent, c.active, c.step, (SELECT COUNT(*) FROM server_plans p WHERE p.catid = c.id) AS plans FROM server_categories c ORDER BY c.parent, c.id")


def category_save(cat_id: int | None, title: str, parent: int = 0, active: int = 1) -> int:
    title = str(title or "").strip()

    if not title:
        raise ValueError("the category needs a title")

    if cat_id is None:
        write(f"INSERT INTO server_categories (title, parent, active, step) VALUES ({lit(title)}, {int(parent)}, {1 if active else 0}, 4)")
        return last_id("server_categories")

    write(f"UPDATE server_categories SET title = {lit(title)}, parent = {int(parent)}, active = {1 if active else 0} WHERE id = {int(cat_id)}")
    return int(cat_id)


def category_delete(cat_id: int) -> None:
    n = int(read(f"SELECT COUNT(*) AS n FROM server_plans WHERE catid = {int(cat_id)}")[0]["n"])

    if n:
        raise ValueError(f"{n} plan(s) still use this category - move or delete them first")

    write(f"DELETE FROM server_categories WHERE id = {int(cat_id)}")


# ------------------------------------------------------------------ the panels the bot sells from (server_info + server_config)
def servers() -> list[dict]:
    return read("SELECT i.id, i.title, i.remark, i.flag, i.ucount, i.active, i.state, c.type, c.panel_url, c.username, c.sni, c.ip, "
                "(SELECT COUNT(*) FROM orders_list o WHERE o.server_id = i.id AND o.status = 1) AS active_orders, (SELECT COUNT(*) FROM server_plans p WHERE p.server_id = i.id) AS plans "
                "FROM server_info i LEFT JOIN server_config c ON c.id = i.id ORDER BY i.id")


def server_save(server_id: int | None, info: dict, config: dict) -> int:
    """info: title, remark, flag, ucount, active, state. config: type, panel_url, username, password (empty keeps), sni, ip."""
    i = _clean(info, {"title": str, "remark": str, "flag": str, "ucount": int, "active": int, "state": int})
    c = _clean(config, {"type": str, "panel_url": str, "username": str, "password": str, "sni": str, "ip": str})

    if c.get("panel_url") and not re.match(r"^https?://[A-Za-z0-9.\-:\[\]]+(/.*)?$", c["panel_url"]):
        raise ValueError("the panel address starts with http:// or https://")

    if c.get("type") and c["type"] not in ("marzban", "pasarguard", "marzneshin", "sanaei", "alireza"):
        raise ValueError("type is marzban, pasarguard, marzneshin, sanaei or alireza")

    if not c.get("password"):
        c.pop("password", None)

    if server_id is None:
        if not i.get("title"):
            raise ValueError("the server needs a title")

        i.setdefault("ucount", 1000)
        i.setdefault("active", 1)
        i.setdefault("state", 1)
        i.setdefault("remark", "")
        i.setdefault("flag", "")
        write(f"INSERT INTO server_info ({', '.join(ident(k) for k in i)}) VALUES ({', '.join(lit(v) for v in i.values())})")
        sid = last_id("server_info")
        c.setdefault("type", "pasarguard")
        c.setdefault("panel_url", "")
        c.setdefault("username", "")
        c.setdefault("password", "")
        c.setdefault("sni", "")
        c.setdefault("ip", "")
        write(f"INSERT INTO server_config (id, {', '.join(ident(k) for k in c)}, header_type, security) VALUES ({sid}, {', '.join(lit(v) for v in c.values())}, \"none\", \"none\")")
        _event("info", f"Bot server '{i['title']}' added from the panel (#{sid}, {c['type']}).", "")
        return sid

    if i:
        write(f"UPDATE server_info SET {set_clause(i)} WHERE id = {int(server_id)}")

    if c:
        if not read(f"SELECT id FROM server_config WHERE id = {int(server_id)}"):
            write(f"INSERT INTO server_config (id, header_type, security) VALUES ({int(server_id)}, \"none\", \"none\")")

        write(f"UPDATE server_config SET {set_clause(c)} WHERE id = {int(server_id)}")

    _event("info", f"Bot server #{server_id} changed from the panel.", "")
    return int(server_id)


def server_delete(server_id: int) -> None:
    n = int(read(f"SELECT COUNT(*) AS n FROM orders_list WHERE server_id = {int(server_id)} AND status = 1")[0]["n"])

    if n:
        raise ValueError(f"{n} active subscription(s) live on this server - it cannot be removed")

    write(f"DELETE FROM server_plans WHERE server_id = {int(server_id)}")
    write(f"DELETE FROM server_config WHERE id = {int(server_id)}")
    write(f"DELETE FROM server_info WHERE id = {int(server_id)}")
    _event("warn", f"Bot server #{server_id} removed from the panel (with its plans).", "")


def panels_known() -> list[dict]:
    """The Marzban / PasarGuard panels this panel manages: a server of the bot can be made from one with one click."""
    from sqlalchemy import select

    from ..models import Panel

    with session() as db:
        return [{"id": p.id, "name": p.name, "type": p.type, "url": p.url, "username": p.username} for p in db.scalars(select(Panel).order_by(Panel.name))]


def server_from_panel(panel_id: int, title: str = "", ucount: int = 1000) -> int:
    from ..models import Panel

    with session() as db:
        p = db.get(Panel, int(panel_id))

        if p is None:
            raise ValueError("panel not found")

        info = {"title": title or p.name, "ucount": ucount, "active": 1, "state": 1}
        conf = {"type": "pasarguard" if p.type == "pasarguard" else "marzban", "panel_url": p.url.rstrip("/"), "username": p.username, "password": p.password}

    return server_save(None, info, conf)


# ------------------------------------------------------------------ discounts
def discounts() -> list[dict]:
    return read("SELECT id, hash_id, type, amount, expire_date, expire_count, can_use, audience_type, audience_user_id, target_type, target_id, min_price, max_discount, is_active, "
                "(LENGTH(COALESCE(used_by, \"\")) - LENGTH(REPLACE(COALESCE(used_by, \"\"), \",\", \"\"))) AS uses FROM discounts ORDER BY id DESC LIMIT 200")


def discount_save(disc_id: int | None, f: dict) -> int:
    code = re.sub(r"[^A-Za-z0-9_-]", "", str(f.get("hash_id", "")))[:40]
    kind = "percent" if str(f.get("type", "percent")) == "percent" else "amount"
    amount, days = num(f.get("amount", 0)), num(f.get("days", 0))
    audience = str(f.get("audience_type", "all"))
    target = str(f.get("target_type", "all"))

    if audience not in AUDIENCES or target not in ("all", "plan", "category", "server"):
        raise ValueError("bad audience or target")

    if kind == "percent" and not 1 <= amount <= 100:
        raise ValueError("a percent discount is between 1 and 100")

    vals = {"hash_id": code, "type": kind, "amount": amount, "expire_date": int(time.time()) + days * 86400 if days else 0, "expire_count": num(f.get("expire_count", 0)), "can_use": num(f.get("can_use", 1)),
            "audience_type": audience, "audience_user_id": num(f.get("audience_user_id", 0)), "target_type": target, "target_id": num(f.get("target_id", 0)), "min_price": num(f.get("min_price", 0)),
            "max_discount": num(f.get("max_discount", 0)), "is_active": 1 if str(f.get("is_active", 1)) not in ("0", "false", "False") else 0}

    if disc_id is None:
        if not code:
            raise ValueError("the code needs letters or digits")

        write(f"INSERT INTO discounts ({', '.join(vals)}) VALUES ({', '.join(lit(v) for v in vals.values())})")
        did = last_id("discounts")
        _event("info", f"Bot discount code {code} added from the panel.", "")
        return did

    write(f"UPDATE discounts SET {set_clause(vals)} WHERE id = {int(disc_id)}")
    return int(disc_id)


def discount_delete(disc_id: int) -> None:
    write(f"DELETE FROM discounts WHERE id = {int(disc_id)}")


# ------------------------------------------------------------------ gift codes (wallet credit)
def gifts() -> list[dict]:
    return read("SELECT id, code, amount, max_uses, used, expires, created, note FROM gift_codes ORDER BY id DESC LIMIT 200")


def gift_save(code: str, amount: int, max_uses: int = 1, days: int = 0, note: str = "") -> int:
    code = re.sub(r"[^A-Za-z0-9_-]", "", str(code or ""))[:40] or base64.b32encode(str(time.time()).encode()).decode()[:10]
    amount, max_uses = num(amount), max(1, num(max_uses, 1))

    if amount <= 0:
        raise ValueError("the amount must be above zero")

    write(f"INSERT INTO gift_codes (code, amount, max_uses, used, used_by, expires, created, note) VALUES ({lit(code)}, {amount}, {max_uses}, 0, \"[]\", {int(time.time()) + num(days) * 86400 if num(days) else 0}, {int(time.time())}, {lit(note or '')})")
    _event("info", f"Bot gift code {code} ({amount:,} Toman × {max_uses}) added from the panel.", "")
    return last_id("gift_codes")


def gift_delete(gift_id: int) -> None:
    write(f"DELETE FROM gift_codes WHERE id = {int(gift_id)}")


# ------------------------------------------------------------------ broadcasts (the bot's cron sends them, one user at a time)
def broadcasts() -> list[dict]:
    return read("SELECT id, title, type, target_group, state, sent, total_cnt, deleted_cnt, created_at, updated_at, LEFT(text, 160) AS text FROM send_list ORDER BY id DESC LIMIT 60")


def broadcast_create(title: str, text: str, group: str = "all") -> int:
    title, text = str(title or "").strip()[:200], str(text or "").strip()

    if not text:
        raise ValueError("the message needs a text")

    if group not in GROUPS:
        raise ValueError("the audience is all, has, none or agents")

    write(f"INSERT INTO send_list (type, title, text, target_group, state, created_at) VALUES (\"text\", {lit(title or text[:40])}, {lit(text)}, {lit(group)}, {BC_QUEUED}, {int(time.time())})")
    bid = last_id("send_list")
    _event("warn", f"Broadcast #{bid} queued from the panel for {GROUPS[group]}: {text[:60]!r}", "")
    return bid


def broadcast_cancel(bid: int) -> None:
    write(f"UPDATE send_list SET state = {BC_CANCELLED} WHERE id = {int(bid)} AND state IN ({BC_DRAFT}, {BC_QUEUED}, {BC_RUNNING})")
    _event("info", f"Broadcast #{bid} cancelled from the panel.", "")


# ------------------------------------------------------------------ tickets
def tickets(state: str = "open", page: int = 0) -> dict:
    where = f"c.state = {TICKET_OPEN}" if state == "open" else f"c.state = {TICKET_CLOSED}" if state == "closed" else "1 = 1"
    total = int(read(f"SELECT COUNT(*) AS n FROM chats c WHERE {where}")[0]["n"])
    rows = read(f"SELECT c.id, c.user_id, u.name, u.username, c.title, c.category, c.state, c.rate, c.create_date, (SELECT COUNT(*) FROM chats_info m WHERE m.chat_id = c.id) AS messages, "
                f"(SELECT MAX(m.sent_date) FROM chats_info m WHERE m.chat_id = c.id) AS last_at FROM chats c LEFT JOIN users u ON u.userid = c.user_id WHERE {where} ORDER BY c.id DESC LIMIT {PAGE} OFFSET {max(0, page) * PAGE}")
    return {"rows": rows, "total": total, "page": max(0, page)}


def ticket_messages(chat_id: int) -> list[dict]:
    return read(f"SELECT id, msg_type, text, sent_date FROM chats_info WHERE chat_id = {int(chat_id)} ORDER BY id ASC LIMIT 200")


def ticket_reply(chat_id: int, text: str) -> dict:
    text = str(text or "").strip()

    if not text:
        raise ValueError("the reply needs a text")

    rows = read(f"SELECT id, user_id, title FROM chats WHERE id = {int(chat_id)}")

    if not rows:
        raise ValueError("ticket not found")

    uid = str(rows[0]["user_id"])
    write(f"INSERT INTO chats_info (chat_id, sent_date, msg_type, text) VALUES ({int(chat_id)}, {int(time.time())}, \"ADMIN\", {lit(text)})")
    r = _tg("sendMessage", {"chat_id": uid, "parse_mode": "HTML", "text": f"💬 پاسخ پشتیبانی به تیکت «{rows[0].get('title', '')}»:\n\n{text}"})
    _event("info", f"Ticket #{chat_id} answered from the panel.", uid)
    return {"sent": bool(r.get("ok")), "telegram": r.get("description", "")}


def ticket_close(chat_id: int) -> None:
    write(f"UPDATE chats SET state = {TICKET_CLOSED} WHERE id = {int(chat_id)}")
    rows = read(f"SELECT user_id FROM chats WHERE id = {int(chat_id)}")

    if rows:
        _tg("sendMessage", {"chat_id": str(rows[0]["user_id"]), "text": "✅ تیکت شما بسته شد. اگر مشکل ادامه داشت، تیکت جدیدی ثبت کنید."})

    _event("info", f"Ticket #{chat_id} closed from the panel.", "")


# ------------------------------------------------------------------ texts and buttons of the bot
def texts() -> dict:
    """The bot's own texts (values.php) with the panel's overrides on top; the overrides live in settings/*_overrides.json on the bot server."""
    c = shopbot.conf()
    out = run_script(shopbot._node(), "bot_texts", {"DIR": c["dir"]}, timeout=60)
    d: dict = {"texts": {}, "buttons": {}, "text_overrides": {}, "button_overrides": {}, "hooked": False}

    for ln in out.splitlines():
        key, _, rest = ln.partition(" ")

        try:
            if key == "DEFAULTS":
                j = json.loads(rest or "{}")
                d["texts"], d["buttons"] = {k: str(v) for k, v in (j.get("texts") or {}).items() if isinstance(v, (str, int, float))}, {k: str(v) for k, v in (j.get("buttons") or {}).items() if isinstance(v, (str, int, float))}
            elif key == "TEXT_OVERRIDES":
                d["text_overrides"] = {k: str(v) for k, v in json.loads(rest or "{}").items()}
            elif key == "BUTTON_OVERRIDES":
                d["button_overrides"] = {k: str(v) for k, v in json.loads(rest or "{}").items()}
            elif key == "HOOKED":
                d["hooked"] = rest.strip() == "1"
        except ValueError:
            continue

    return d


def texts_set(kind: str, changes: dict) -> dict:
    """Merge `changes` into the overrides file (an empty value removes the override) and make sure the bot reads the file."""
    if kind not in ("text", "button"):
        raise ValueError("kind is text or button")

    cur = texts()
    ov = dict(cur["text_overrides" if kind == "text" else "button_overrides"])
    known = cur["texts"] if kind == "text" else cur["buttons"]

    for k, v in (changes or {}).items():
        if k not in known:
            raise ValueError(f"unknown key {k!r}")

        if str(v) == "" or str(v) == known[k]:
            ov.pop(k, None)
        else:
            ov[k] = str(v)[:4000]

    c = shopbot.conf()
    run_action(shopbot._node(), "bot_put_json", {"PATH": f"{c['dir']}/settings/{kind}_overrides.json", "B64": base64.b64encode(json.dumps(ov, ensure_ascii=False, indent=1).encode()).decode()}, timeout=40)

    if kind == "text" and not cur["hooked"]:
        run_action(shopbot._node(), "bot_texts_hook", {"DIR": c["dir"]}, timeout=40)

    _event("info", f"Bot {kind}s changed from the panel: " + ", ".join(list(changes)[:6]), "")
    return {"overrides": ov}


# ------------------------------------------------------------------ settings documents (BOT_STATES structured, PAYMENT_KEYS)
def setting_get(kind: str) -> dict:
    rows = read(f'SELECT value FROM setting WHERE type = "{ident(kind)}" LIMIT 1')

    if not rows or not rows[0].get("value") or rows[0]["value"] == "NULL":
        return {}

    try:
        v = json.loads(rows[0]["value"])
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def setting_put(kind: str, doc: dict) -> dict:
    """The whole JSON document of one setting type (BOT_STATES, PAYMENT_KEYS ...) as base64: nothing can break it."""
    ident(kind)
    exists = read(f'SELECT id FROM setting WHERE type = "{kind}" LIMIT 1')
    b = lit(json.dumps(doc, ensure_ascii=False))

    if exists:
        write(f'UPDATE setting SET value = {b} WHERE type = "{kind}"')
    else:
        write(f'INSERT INTO setting (type, value) VALUES ("{kind}", {b})')

    return doc


def states_structured() -> dict:
    cur = setting_get("BOT_STATES")
    known = {k for _, items in STATE_GROUPS for k, *_ in items}
    return {"groups": [{"title": title, "items": [{"key": k, "label": label, "kind": kind, "hint": hint, "value": cur.get(k, "")} for k, label, kind, hint in items]} for title, items in STATE_GROUPS],
            "other": {k: v for k, v in cur.items() if k not in known}}


def states_set(changes: dict) -> dict:
    cur = setting_get("BOT_STATES")

    for k, v in (changes or {}).items():
        if not shopbot._KEY.match(str(k)):
            raise ValueError("bad key")

        cur[k] = str(v).strip()[:300]

    setting_put("BOT_STATES", cur)
    _event("info", "Bot settings changed: " + ", ".join(f"{k}={str(v)[:20]}" for k, v in changes.items())[:200], "")
    return cur


def payment_keys_get() -> dict:
    cur = setting_get("PAYMENT_KEYS")
    return {k: cur.get(k, "") for k in PAYMENT_KEYS}


def payment_keys_set(changes: dict) -> dict:
    cur = setting_get("PAYMENT_KEYS")

    for k, v in (changes or {}).items():
        if k not in PAYMENT_KEYS:
            raise ValueError(f"unknown key {k!r}")

        if str(v).strip():
            cur[k] = str(v).strip()[:300]
        else:
            cur.pop(k, None)

    setting_put("PAYMENT_KEYS", cur)
    _event("info", "Bot payment settings changed: " + ", ".join(changes), "")
    return {k: cur.get(k, "") for k in PAYMENT_KEYS}


# ------------------------------------------------------------------ apps list, top-up prices, backups, command menu
def apps() -> list[dict]:
    return read("SELECT id, title, link, status, date FROM needed_sofwares ORDER BY id")


def app_save(app_id: int | None, title: str, link: str, status: int = 1) -> int:
    title, link = str(title or "").strip()[:100], str(link or "").strip()[:250]

    if not title or not re.match(r"^https?://", link):
        raise ValueError("a title and a link starting with http(s):// are needed")

    if app_id is None:
        write(f"INSERT INTO needed_sofwares (title, link, status, date) VALUES ({lit(title)}, {lit(link)}, {1 if status else 0}, {int(time.time())})")
        return last_id("needed_sofwares")

    write(f"UPDATE needed_sofwares SET title = {lit(title)}, link = {lit(link)}, status = {1 if status else 0} WHERE id = {int(app_id)}")
    return int(app_id)


def app_delete(app_id: int) -> None:
    write(f"DELETE FROM needed_sofwares WHERE id = {int(app_id)}")


def topups() -> dict:
    return {"volume": read("SELECT id, price, volume FROM increase_plan ORDER BY volume"), "days": read("SELECT id, price, volume AS days FROM increase_day ORDER BY volume")}


def topup_save(kind: str, price: int, amount: float) -> int:
    table = {"volume": "increase_plan", "days": "increase_day"}.get(kind)

    if not table or num(price) <= 0 or float(amount) <= 0:
        raise ValueError("kind is volume or days; price and amount are above zero")

    write(f"INSERT INTO {table} (price, volume) VALUES ({num(price)}, {float(amount)})")
    return last_id(table)


def topup_delete(kind: str, row_id: int) -> None:
    table = {"volume": "increase_plan", "days": "increase_day"}.get(kind)

    if not table:
        raise ValueError("kind is volume or days")

    write(f"DELETE FROM {table} WHERE id = {int(row_id)}")


def backups() -> list[dict]:
    out = run_script(shopbot._node(), "bot_backups", timeout=30)
    rows = []

    for ln in out.splitlines():
        p = ln.split()

        if len(p) == 3 and p[0] == "BACKUP" and p[2].isdigit():
            name, size = p[1], int(p[2])
            m = re.search(r"(\d{8})-(\d{6})", name)
            rows.append({"name": name, "size": size, "at": time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")) if m else 0})

    return sorted(rows, key=lambda r: r["name"], reverse=True)


# ------------------------------------------------------------------ the Mini App (bot/miniapp in this repository) on the bot server
MINIAPP_FILES = ("api.php", "index.html", "app.js", "app.css")


def miniapp_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "bot" / "miniapp"


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _version_in(html: str) -> str:
    m = re.search(r"app\.js\?v=([0-9.]+)", html)
    return m.group(1) if m else ""


def parse_miniapp_info(out: str, local: dict[str, str]) -> dict:
    """Pure: the bot_miniapp_info output + the fingerprints of the repository's files -> status."""
    st: dict = {"installed": False, "files": [], "version": "", "php_ok": None, "url": "", "errors": 0, "last_error": "", "up_to_date": False}

    for ln in out.splitlines():
        p = ln.split(maxsplit=3)

        if not p:
            continue

        if p[0] == "FILE" and len(p) >= 4:
            st["installed"] = True
            st["files"].append({"name": p[1], "size": int(p[2]) if p[2].isdigit() else 0, "hash": p[3], "current": local.get(p[1]) == p[3]})
        elif p[0] == "MISSING" and len(p) >= 2:
            st["files"].append({"name": p[1], "size": 0, "hash": "", "current": False})
        elif p[0] == "VERSION" and len(p) >= 2:
            st["version"] = p[1]
        elif p[0] in ("PHP_OK", "PHP_BAD"):
            st["php_ok"] = p[0] == "PHP_OK"
        elif p[0] == "URL" and len(p) >= 2:
            st["url"] = p[1].rstrip("/") + "/app/"
        elif p[0] == "ERRORS" and len(p) >= 2:
            st["errors"] = int(p[1]) if p[1].isdigit() else 0
            st["last_error"] = ln.split(maxsplit=2)[2] if len(ln.split(maxsplit=2)) > 2 else ""

    st["up_to_date"] = st["installed"] and len(st["files"]) == len(MINIAPP_FILES) and all(f["current"] for f in st["files"])
    return st


def _local_files() -> dict[str, bytes]:
    d = miniapp_dir()
    return {name: (d / name).read_bytes() for name in MINIAPP_FILES if (d / name).is_file()}


def miniapp_status() -> dict:
    c = shopbot.conf()
    out = run_script(shopbot._node(), "bot_miniapp_info", {"DIR": c["dir"]}, timeout=40)
    local = _local_files()
    st = parse_miniapp_info(out, {k: _fingerprint(v) for k, v in local.items()})
    st["local_version"] = _version_in(local.get("index.html", b"").decode("utf-8", "replace"))
    try:
        st["mini_app_switch"] = setting_get("BOT_STATES").get("miniAppState", "on") == "on"
    except (SSHError, RuntimeError, ValueError):   # the switch is a detail; the status must still come back
        st["mini_app_switch"] = None

    return st


def miniapp_deploy() -> dict:
    """Copies the repository's Mini App into <bot dir>/app/ over the reviewed action, one file at a time (php -l before replacing)."""
    c = shopbot.conf()
    local = _local_files()

    if len(local) != len(MINIAPP_FILES):
        raise RuntimeError("the Mini App files are missing from this installation of the panel (bot/miniapp)")

    written = []

    for name in MINIAPP_FILES:
        out = run_action(shopbot._node(), "bot_miniapp_put", {"PATH": f"{c['dir']}/app/{name}", "B64": base64.b64encode(local[name]).decode()}, timeout=90)

        if "WROTE" not in out:
            raise RuntimeError(f"{name}: " + (out.strip().splitlines() or ["not written"])[-1][:160])

        written.append(name)

    _event("info", "Mini App installed / updated on the bot server (" + ", ".join(written) + ").", "")
    return miniapp_status()


COMMANDS = [{"command": "start", "description": "منوی اصلی"}, {"command": "buy", "description": "خرید اشتراک"}, {"command": "mysubs", "description": "اشتراک‌های من"},
            {"command": "wallet", "description": "کیف پول"}, {"command": "support", "description": "پشتیبانی"}, {"command": "help", "description": "راهنما"}]


def set_commands() -> dict:
    r = _tg("setMyCommands", {"commands": COMMANDS})

    if not r.get("ok"):
        raise RuntimeError("Telegram: " + str(r.get("description", "refused"))[:160])

    _event("info", "The bot's command menu was set from the panel.", "")
    return {"done": True, "commands": [c["command"] for c in COMMANDS]}
