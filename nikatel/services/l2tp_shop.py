"""The shop side of the L2TP product (1.69.245): what the Mini App SELLS and what it sold. The plans (a name, days, devices, traffic, speed, a price), the switch that says who sees L2TP in the Mini App, and the sales
(a purchase or a renewal paid from the wallet or by card) live in the shop bot's own database, in the tables the Mini App makes (`l2tp_plans`, `l2tp_accounts`, and `pays` for the money). This module reads and changes them
through the panel's two reviewed channels to the bot host (`shopbot.query` = one SELECT, `shopbot.write` = an UPDATE / INSERT / DELETE of a short list), never through a shell of its own.
The accounts themselves (their passwords, their end dates) stay with the manager on the entry; nothing here touches the entry."""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from ..log import get_logger
from . import shopbot

log = get_logger("l2tp_shop")

STAGES = [("off", "Off", "Nobody sees L2TP in the Mini App."),
          ("admin", "Admins only", "Only the bot's admins see it: the testing stage."),
          ("agents", "Admins and partners", "The bot's admins and its partners (همکار) see it."),
          ("on", "Everybody", "Every customer of the Mini App sees it.")]
STAGE_KEYS = [s[0] for s in STAGES]
STAGE_DEFAULT = "admin"                                                          # (what the Mini App does when the switch was never set)
PAID = ("paid_with_wallet", "approved", "auto_cart_approved", "paid")            # (the states of the bot's `pays` that mean the money came)
TYPES = ("BUY_L2TP", "RENEW_L2TP")
TEHRAN = 12600
DAY = 86400
BAD_CHARS = re.compile(r"[\"'`\\;\x00-\x09\x0b-\x1f\x7f]")                       # (a text travels inside a quoted SQL literal: none of these may be in it - a line break is made with CHAR(10), below)
MAX_PRICE = 100_000_000                                                          # (toman)


def _query(sql: str) -> list[dict]:
    """One SELECT on the bot's database; a table the Mini App has not made yet reads as nothing."""
    try:
        return shopbot.query(sql)
    except RuntimeError as e:
        if "doesn't exist" in str(e) or "1146" in str(e):
            return []

        raise


def _text(v, what: str, lo: int, hi: int, lines: bool = False) -> str:
    s = str(v or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    if not lines and "\n" in s:
        raise ValueError(f"{what} is one line")

    if BAD_CHARS.search(s):
        raise ValueError(f"{what} may not hold quotes, a backslash, a semicolon or a back-quote")

    if not lo <= len(s) <= hi:
        raise ValueError(f"{what} must be {lo}-{hi} characters" if lo else f"{what} may be at most {hi} characters")

    return s


def _lit(s: str) -> str:
    """A text as a SQL expression: "a line", or CONCAT_WS(CHAR(10), "line one", "line two") for a text with line breaks (the SQL goes through a channel that refuses a raw back-slash and a quote)."""
    parts = s.split("\n")
    return '"' + parts[0] + '"' if len(parts) == 1 else "CONCAT_WS(CHAR(10), " + ", ".join('"' + p + '"' for p in parts) + ")"


def _int(v, what: str, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} is a whole number")

    if not lo <= n <= hi:
        raise ValueError(f"{what} is {lo} to {hi}")

    return n


def _dec(v, what: str, hi: float, step_min: float = 0.1, places: int = 1):
    """None or 0 = no limit (stored as NULL); else a number up to `hi`, rounded to the places the column keeps (a speed: one, the traffic: two)."""
    if v is None or v == "":
        return None

    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} is a number (empty = no limit)")

    if x == 0:
        return None

    if x < step_min or x > hi:
        raise ValueError(f"{what} is {step_min} to {int(hi)} (empty = no limit)")

    return round(x, places)


def _sqlnum(x) -> str:
    if x is None:
        return "NULL"

    f = float(x)
    return str(int(f)) if f.is_integer() else repr(f)


def _done(out: str) -> None:
    """The write channel answers with the mysql output and DONE: an ERROR line is a refusal."""
    bad = next((ln for ln in str(out or "").splitlines() if ln.startswith("ERROR")), None)

    if bad:
        raise ValueError("the bot's database refused it: " + bad[:160])


# ------------------------------------------------------------------ reading
def stage() -> str:
    rows = _query('SELECT value FROM setting WHERE type = "BOT_STATES"')

    try:
        cur = json.loads(rows[0]["value"]) if rows and rows[0].get("value") else {}
    except ValueError:
        cur = {}

    v = str(cur.get("miniL2tp") or STAGE_DEFAULT)
    return v if v in STAGE_KEYS else STAGE_DEFAULT


def _num(v):
    try:
        return None if v in (None, "", "NULL") else float(v)
    except (TypeError, ValueError):
        return None


def shape_plan(r: dict) -> dict:
    pid = int(r.get("id") or 0)
    gb = _num(r.get("gb"))
    descr = str(r.get("descr") or "")          # (mysql prints an empty description as the word NULL)
    return {"id": pid, "title": str(r.get("title") or ""), "days": int(r.get("days") or 0), "devices": int(r.get("devices") or 1), "gb": gb if gb else None,
            "down": _num(r.get("down")) or None, "up": _num(r.get("up")) or None, "price": int(r.get("price") or 0), "descr": "" if descr == "NULL" else descr,
            "active": str(r.get("active")) == "1", "sort": int(r.get("sort") or 0), "accounts": int(r.get("accounts") or 0)}


def plans() -> list[dict]:
    """Every plan, the switched-off ones too, with the number of live accounts that were bought on it (ONE query: every read of the bot host is an SSH round trip of about two seconds)."""
    rows = _query("SELECT p.id, p.title, p.days, p.devices, p.down, p.up, p.price, p.descr, p.active, p.sort, p.gb, "
                  "(SELECT COUNT(*) FROM l2tp_accounts a WHERE a.plan_id = p.id AND a.status = 1) AS accounts FROM l2tp_plans p ORDER BY p.sort, p.id")
    return [shape_plan(r) for r in rows]


def _paid_sql() -> str:
    return "p.state IN (" + ", ".join(f'"{s}"' for s in PAID) + ")"


def _types() -> str:
    return ", ".join(f'"{t}"' for t in TYPES)


def sales_money(now: float | None = None) -> dict:
    """The money of the product: income today / in 7 days / in 30 days / in all, and how many purchases and renewals were paid."""
    now = now or time.time()
    t0 = int((now + TEHRAN) // DAY * DAY - TEHRAN)                                # the start of today in Tehran
    s = (_query(f"SELECT COUNT(*) AS n, COALESCE(SUM(p.price), 0) AS total, COALESCE(SUM(CASE WHEN p.request_date >= {t0} THEN p.price END), 0) AS d1, "
                f"COALESCE(SUM(CASE WHEN p.request_date >= {t0 - 6 * DAY} THEN p.price END), 0) AS d7, COALESCE(SUM(CASE WHEN p.request_date >= {t0 - 29 * DAY} THEN p.price END), 0) AS d30, "
                f'COALESCE(SUM(p.type = "BUY_L2TP"), 0) AS buys, COALESCE(SUM(p.type = "RENEW_L2TP"), 0) AS renews FROM pays p WHERE p.type IN ({_types()}) AND {_paid_sql()}') or [{}])[0]

    def money(k: str) -> int:
        try:
            return int(float(s.get(k) or 0))
        except (TypeError, ValueError):
            return 0

    return {"today": money("d1"), "week": money("d7"), "month": money("d30"), "total": money("total"), "count": money("n"), "buys": money("buys"), "renews": money("renews")}


def sales_orders() -> list[dict]:
    """The last 40 orders, in any state (a failed payment is seen too), with the buyer's name and the plan's name."""
    rows = _query("SELECT p.id, p.user_id, u.name, u.username, p.type, p.price, p.state, p.request_date, p.plan_id, p.description, lp.title AS plan_title FROM pays p "
                  f"LEFT JOIN users u ON u.userid = p.user_id LEFT JOIN l2tp_plans lp ON lp.id = p.plan_id WHERE p.type IN ({_types()}) ORDER BY p.id DESC LIMIT 40")
    out = []

    for r in rows:
        title = str(r.get("plan_title") or "")
        out.append({"id": int(r.get("id") or 0), "user_id": str(r.get("user_id") or ""), "name": str(r.get("name") or "") or str(r.get("username") or ""), "type": str(r.get("type") or ""), "price": int(r.get("price") or 0),
                    "state": str(r.get("state") or ""), "at": int(r.get("request_date") or 0), "plan": "" if title == "NULL" else title, "paid": str(r.get("state") or "") in PAID, "text": str(r.get("description") or "")[:120]})

    return out


def sales(now: float | None = None) -> dict:
    """The money and the last orders (two reads at the same time)."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        fm, fo = pool.submit(sales_money, now), pool.submit(sales_orders)
        return {**fm.result(), "orders": fo.result()}


def overview() -> dict:
    """The Shop window: the switch, the plans, the sales - four reads of the bot host made AT THE SAME TIME (one after the other they took ten seconds)."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        fs, fp, fm, fo = pool.submit(stage), pool.submit(plans), pool.submit(sales_money), pool.submit(sales_orders)
        return {"stage": fs.result(), "stages": [{"key": k, "label": lb, "note": nt} for k, lb, nt in STAGES], "plans": fp.result(), "sales": {**fm.result(), "orders": fo.result()}}

# ------------------------------------------------------------------ changing
def set_stage(value: str) -> str:
    v = str(value or "")

    if v not in STAGE_KEYS:
        raise ValueError("the stage is one of: " + ", ".join(STAGE_KEYS))

    shopbot.set_states({"miniL2tp": v})
    log.info("l2tp shop: the Mini App stage is now %s", v)
    return v


def _plan_fields(title, days, devices, gb, down, up, price, descr, active, sort) -> dict:
    return {"title": _text(title, "the plan name", 2, 80), "days": _int(days, "the days", 1, 3650), "devices": _int(devices, "the devices", 1, 10), "gb": _dec(gb, "the traffic", 100000.0, 0.01, 2),
            "down": _dec(down, "the download speed", 1000.0), "up": _dec(up, "the upload speed", 1000.0), "price": _int(price, "the price", 0, MAX_PRICE), "descr": _text(descr, "the description", 0, 500, lines=True),
            "active": 1 if active else 0, "sort": _int(sort or 0, "the order number", -1000, 1000)}


def plan_save(pid: int | None, title, days, devices, gb, down, up, price, descr, active=True, sort=0) -> list[dict]:
    """A new plan (pid empty) or the changes of one. The Mini App renews an account at its plan's CURRENT price, days and traffic (and only while the plan is on), so an edit reaches the next purchase and every renewal of the accounts bought on this plan; an account that is running keeps its own end date and limit until it is renewed."""
    f = _plan_fields(title, days, devices, gb, down, up, price, descr, active, sort)
    vals = f'title = {_lit(f["title"])}, days = {f["days"]}, devices = {f["devices"]}, gb = {_sqlnum(f["gb"])}, down = {_sqlnum(f["down"])}, up = {_sqlnum(f["up"])}, price = {f["price"]}, descr = {_lit(f["descr"]) if f["descr"] else "NULL"}, active = {f["active"]}, sort = {f["sort"]}'

    if pid:
        pid = _int(pid, "the plan", 1, 2_000_000_000)
        _done(shopbot.write(f"UPDATE l2tp_plans SET {vals} WHERE id = {pid}"))
        log.info("l2tp shop: plan %s changed (%s, %s toman)", pid, f["title"], f["price"])
    else:
        _done(shopbot.write(f'INSERT INTO l2tp_plans (title, days, devices, gb, down, up, price, descr, active, sort) VALUES ({_lit(f["title"])}, {f["days"]}, {f["devices"]}, {_sqlnum(f["gb"])}, {_sqlnum(f["down"])}, {_sqlnum(f["up"])}, {f["price"]}, {_lit(f["descr"]) if f["descr"] else "NULL"}, {f["active"]}, {f["sort"]})'))
        log.info("l2tp shop: a plan was added (%s, %s toman)", f["title"], f["price"])

    now = plans()

    if pid and not any(p["id"] == pid for p in now):
        raise ValueError("no such plan")

    return now


def plan_active(pid: int, on: bool) -> list[dict]:
    """Switch a plan on or off. An OFF plan cannot be bought and the accounts bought on it cannot be renewed in the Mini App."""
    pid = _int(pid, "the plan", 1, 2_000_000_000)
    _done(shopbot.write(f"UPDATE l2tp_plans SET active = {1 if on else 0} WHERE id = {pid}"))
    now = plans()

    if not any(p["id"] == pid for p in now):
        raise ValueError("no such plan")

    return now


def plan_delete(pid: int) -> list[dict]:
    """Remove a plan nobody bought. A plan that has accounts cannot be deleted: they renew on it and the sales list names it. Switching it off is possible, but it also stops the renewal of those accounts (the Mini App renews only on a plan that is on)."""
    pid = _int(pid, "the plan", 1, 2_000_000_000)
    used = _query(f"SELECT COUNT(*) AS n FROM l2tp_accounts WHERE plan_id = {pid}")

    if used and int(used[0].get("n") or 0) > 0:
        raise ValueError(f"{used[0]['n']} account(s) were bought on this plan, so it cannot be deleted (switching it off stops their renewal too)")

    _done(shopbot.write(f"DELETE FROM l2tp_plans WHERE id = {pid}"))
    log.info("l2tp shop: plan %s deleted", pid)
    return plans()
