"""The Telegram shop bot (PHP + MariaDB) on its own server, managed over SSH through reviewed scripts:
status and sales numbers, bot switches (BOT_STATES), users of the bot, tables, a read-only SQL box,
backups, and the one-time import of the real database from the staging copy."""
from __future__ import annotations

import json
import re
import time

from ..db import session
from ..log import get_logger
from ..ssh import Node, run_action, run_script
from . import settings as st
from .events import emit

log = get_logger("shopbot")

TABLES = ("users", "orders_list", "pays", "server_plans", "server_config", "server_categories", "discounts", "setting", "send_list", "add_link", "increase_order", "gift_codes", "wheel_log", "cashback_log")
USER_ACTIONS = ("agent_on", "agent_off", "discount", "wallet", "wallet_set", "ban", "unban")
_KEY = re.compile(r"^[A-Za-z_]\w{0,40}$")


def conf() -> dict:
    return st.get("shopbot")


def _node() -> Node:
    c = conf()

    if not c.get("host"):
        raise ValueError("tell the panel where the bot runs (Settings → Shop bot)")

    return Node(c["host"], c.get("ssh_user") or "root", int(c.get("ssh_port") or 22))


def _q(v) -> str:
    """A SQL string literal: the only characters the scripts' regexes refuse are ; ' ` and backslash,
    so they are dropped here and the statement cannot be broken out of."""
    return "'" + re.sub(r"[;'`\\]", "", str(v)) + "'"


def _rows(out: str) -> list[dict]:
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("mysql: [Warning]")]

    if not lines:
        return []

    if lines[0].startswith("ERROR"):
        raise RuntimeError(lines[0][:200])

    head = lines[0].split("\t")
    return [dict(zip(head, ln.split("\t"))) for ln in lines[1:]]


def query(sql: str) -> list[dict]:
    """Read-only SELECT / SHOW against the bot's database."""
    s = str(sql or "").strip().rstrip(";")

    if not re.match(r"^(SELECT|SHOW|DESCRIBE)\b", s, re.IGNORECASE) or ";" in s:
        raise ValueError("only one SELECT / SHOW statement is allowed here (string literals in double quotes)")

    if re.match(r"^SELECT", s, re.IGNORECASE) and not re.search(r"\bLIMIT\b", s, re.IGNORECASE):
        s += " LIMIT 200"

    return _rows(run_script(_node(), "bot_query", {"ENV": conf()["env"], "SQL": _q(s)}, timeout=40))


def write(sql: str) -> str:
    return run_action(_node(), "bot_sql", {"ENV": conf()["env"], "SQL": _q(sql)}, timeout=40)


# ------------------------------------------------------------------ parsing (pure)
def parse_info(out: str) -> dict:
    d: dict = {"installed": False, "units": {}, "cron": 0, "counts": {}, "stage_users": None, "states": {}, "panels": [], "sales": {}, "backups": [], "logs": {"errors": [], "php": []}}
    mode = None

    for ln in out.splitlines():
        if ln == "LOG_ERRORS":
            mode = "errors"
            continue
        if ln == "LOG_PHP":
            mode = "php"
            continue
        if ln == "LOG_END":
            mode = None
            continue
        if mode:
            d["logs"][mode].append(ln[:240])
            continue

        p = ln.split(" ", 1)
        key, rest = p[0], (p[1] if len(p) > 1 else "")

        if key == "INSTALLED":
            d["installed"] = rest.strip() == "1"
        elif key == "UNIT":
            u = rest.split()
            if len(u) == 2 and u[1] != "unknown":
                d["units"][u[0]] = u[1]
        elif key == "CRON":
            d["cron"] = int(rest or 0) if rest.strip().isdigit() else 0
        elif key == "COUNTS":
            n = rest.split()
            if len(n) == 6 and all(x.isdigit() for x in n):
                d["counts"] = dict(zip(("users", "orders", "pending_pays", "paid", "plans", "panels"), map(int, n)))
        elif key == "STAGE":
            d["stage_users"] = int(rest) if rest.strip().isdigit() else None
        elif key == "STATES":
            try:
                d["states"] = json.loads(rest) if rest.strip() else {}
            except ValueError:
                d["states"] = {}
        elif key == "PANELS":
            d["panels"] = [dict(zip(("id", "type", "host"), x.split("|"))) for x in rest.split() if x.count("|") == 2]
        elif key in ("TODAY", "WEEK", "MONTH"):
            n = rest.split()
            if len(n) == 2:
                d["sales"][key.lower()] = {"orders": int(float(n[0])), "income": int(float(n[1]))}
        elif key == "NEWUSERS":
            n = rest.split()
            if len(n) == 2:
                d["sales"]["new_users_today"], d["sales"]["new_users_week"] = int(n[0]), int(n[1])
        elif key == "BACKUPS":
            d["backups"] = rest.split()
        elif key in ("TICKETS", "QUEUE"):
            d[key.lower()] = int(rest) if rest.strip().isdigit() else 0
        elif key == "HOOKED":
            d["texts_hooked"] = rest.strip() not in ("", "0")

    return d


# ------------------------------------------------------------------ status
def info() -> dict:
    c = conf()
    out = run_script(_node(), "bot_info", {"DIR": c["dir"], "ENV": c["env"]}, timeout=60)
    d = parse_info(out)
    d["public_url"] = c.get("public_url", "")
    d["host"] = c["host"]
    return d


def telegram(method: str, body: dict | None = None) -> dict:
    """getMe / getWebhookInfo are reads (a script); everything else changes the bot (an action)."""
    if method in ("getMe", "getWebhookInfo"):
        out = run_script(_node(), "bot_tg_get", {"DIR": conf()["dir"], "METHOD": method}, timeout=40)
    else:
        out = run_action(_node(), "bot_tg", {"DIR": conf()["dir"], "METHOD": method, "BODY": "'" + json.dumps(body or {}, ensure_ascii=False).replace("'", "").replace("\\", "") + "'"}, timeout=40)

    try:
        return json.loads(out.strip() or "{}")
    except ValueError:
        return {"ok": False, "description": out.strip()[:200]}


def webhook_info() -> dict:
    me = telegram("getMe").get("result") or {}
    wh = telegram("getWebhookInfo").get("result") or {}
    return {"bot": {"username": me.get("username", ""), "name": me.get("first_name", "")},
            "webhook": {"url": wh.get("url", ""), "pending": wh.get("pending_update_count", 0), "last_error": wh.get("last_error_message", ""), "last_error_at": wh.get("last_error_date", 0)}}


# ------------------------------------------------------------------ switches
def set_states(changes: dict) -> dict:
    cur = {}
    rows = query("SELECT value FROM setting WHERE type = \"BOT_STATES\"")

    if rows and rows[0].get("value"):
        try:
            cur = json.loads(rows[0]["value"])
        except ValueError:
            cur = {}

    for k, v in (changes or {}).items():
        if not _KEY.match(str(k)):
            raise ValueError("bad key")

        cur[k] = re.sub(r"[\"'`\\\\;]", "", str(v))[:300]      # the value travels inside a quoted SQL literal: no quote may break it

    val = json.dumps(cur, ensure_ascii=False)

    if rows:
        write(f"UPDATE setting SET value = {_q(val)} WHERE type = \"BOT_STATES\"")
    else:
        write(f"INSERT INTO setting (type, value) VALUES (\"BOT_STATES\", {_q(val)})")

    with session() as db:
        emit(db, "info", "shopbot", "Bot switches changed: " + ", ".join(f"{k}={str(v)[:20]}" for k, v in changes.items())[:200], "")

    return cur


# ------------------------------------------------------------------ users of the bot
def find_users(q: str) -> list[dict]:
    q = str(q or "").strip()

    if not q:
        return []

    e = re.sub(r"[;'\"`\\%]", "", q)
    return query(f"SELECT u.id, u.userid, u.name, u.username, u.wallet, u.is_agent, u.discount_percent, u.step, u.phone, u.date, (SELECT COUNT(*) FROM orders_list o WHERE o.userid = u.userid) AS orders FROM users u WHERE u.userid = \"{e}\" OR u.username LIKE \"%{e}%\" OR u.name LIKE \"%{e}%\" OR u.phone LIKE \"%{e}%\" ORDER BY u.id DESC LIMIT 30")


def user_action(action: str, userid: str, value=None) -> dict:
    uid = str(userid or "").strip()

    if not re.fullmatch(r"-?\d{1,20}", uid):
        raise ValueError("bad user id")

    if action not in USER_ACTIONS:
        raise ValueError("unknown action")

    if not query(f"SELECT 1 AS x FROM users WHERE userid = \"{uid}\""):
        raise ValueError("user not found")

    if action == "agent_on":
        write(f"UPDATE users SET is_agent = 1, agent_date = {int(time.time())} WHERE userid = \"{uid}\"")
    elif action == "agent_off":
        write(f"UPDATE users SET is_agent = 0 WHERE userid = \"{uid}\"")
    elif action == "discount":
        v = int(float(value or 0))

        if not 0 <= v <= 100:
            raise ValueError("discount is a percent between 0 and 100")

        write(f"UPDATE users SET discount_percent = {v} WHERE userid = \"{uid}\"")
    elif action == "wallet":
        write(f"UPDATE users SET wallet = GREATEST(0, COALESCE(wallet, 0) + {int(float(value or 0))}) WHERE userid = \"{uid}\"")
    elif action == "wallet_set":
        write(f"UPDATE users SET wallet = {max(0, int(float(value or 0)))} WHERE userid = \"{uid}\"")
    elif action == "ban":
        write(f"UPDATE users SET step = \"banned\" WHERE userid = \"{uid}\"")
    elif action == "unban":
        write(f"UPDATE users SET step = \"\" WHERE userid = \"{uid}\"")

    with session() as db:
        emit(db, "info", "shopbot", f"Bot user {uid}: {action}" + (f" {value}" if value is not None else ""), uid)

    return query(f"SELECT userid, name, username, wallet, is_agent, discount_percent, step FROM users WHERE userid = \"{uid}\"")[0]


def table(name: str, page: int = 0, q: str = "") -> dict:
    if name not in TABLES:
        raise ValueError("unknown table")

    page = max(0, int(page or 0))
    where = ""

    if q:
        cols = [r.get("Field") for r in query(f"SHOW COLUMNS FROM {name}")][:12]
        e = re.sub(r"[;'\"`\\%]", "", str(q))
        where = " WHERE " + " OR ".join(f'{c} LIKE "%{e}%"' for c in cols if c)

    total = int(query(f"SELECT COUNT(*) AS n FROM {name}{where}")[0]["n"])
    rows = query(f"SELECT * FROM {name}{where} ORDER BY id DESC LIMIT 40 OFFSET {page * 40}")

    for r in rows:
        for k, v in r.items():
            if v is not None and len(v) > 160:
                r[k] = v[:160] + "…"

    return {"rows": rows, "total": total, "page": page, "columns": list(rows[0].keys()) if rows else []}


def stats() -> dict:
    out: dict = {}
    out["top_buyers"] = query("SELECT u.userid, u.name, u.username, COUNT(o.id) AS orders, COALESCE(SUM(o.amount), 0) AS spent FROM orders_list o JOIN users u ON u.userid = o.userid GROUP BY o.userid ORDER BY spent DESC LIMIT 10")
    out["top_wallets"] = query("SELECT userid, name, username, wallet FROM users ORDER BY wallet DESC LIMIT 10")
    out["daily"] = query("SELECT FROM_UNIXTIME(date, \"%Y-%m-%d\") AS day, COUNT(*) AS orders, COALESCE(SUM(amount), 0) AS income FROM orders_list WHERE date >= UNIX_TIMESTAMP() - 30 * 86400 GROUP BY day ORDER BY day")
    out["pending_pays"] = query("SELECT p.id, p.user_id, u.name, p.price, p.type, p.request_date FROM pays p LEFT JOIN users u ON u.userid = p.user_id WHERE p.state = \"pending\" ORDER BY p.id DESC LIMIT 20")
    return out


# ------------------------------------------------------------------ actions
def backup(name: str = "manual") -> str:
    out = run_action(_node(), "bot_backup", {"ENV": conf()["env"], "NAME": name}, timeout=600)
    m = re.search(r"BACKUP (\S+) (\d+)", out)

    if not m:
        raise RuntimeError("the dump did not finish: " + out.strip()[-200:])

    with session() as db:
        emit(db, "info", "shopbot", f"Bot database dumped to {m.group(1)} ({int(m.group(2)) // 1024} KB).", "")

    return m.group(1)


def import_stage() -> dict:
    """The real data (staging database on the bot server) over the live database, after a dump of the live one."""
    bk = backup("before-import")
    out = run_action(_node(), "bot_import_stage", {"ENV": conf()["env"]}, timeout=900)

    if "IMPORTED" not in out:
        raise RuntimeError("import failed after backup " + bk + ": " + out.strip()[-300:])

    counts = {ln.split()[1]: int(ln.split()[2]) for ln in out.splitlines() if ln.startswith("TABLE ") and len(ln.split()) == 3 and ln.split()[2].isdigit()}

    with session() as db:
        emit(db, "ok", "shopbot", f"Real bot database imported from staging: {counts.get('users', '?')} users, {counts.get('orders_list', '?')} orders (live copy saved as {bk}).", "")

    return {"backup": bk, "tables": counts}


def action(act: str) -> dict:
    c = conf()

    if act == "webhook":
        if not c.get("public_url"):
            raise ValueError("set the bot's public URL in Settings → Shop bot first")

        r = telegram("setWebhook", {"url": c["public_url"]})
        return {"done": bool(r.get("ok")), "message": r.get("description", "")}

    if act == "restart":
        out = run_action(_node(), "bot_restart_php", timeout=60)
        return {"done": "RESTARTED" in out, "message": out.strip()[-120:] or "no PHP-FPM unit found"}

    if act == "clearlogs":
        run_action(_node(), "bot_clear_logs", {"DIR": c["dir"]}, timeout=30)
        return {"done": True, "message": "logs cleared"}

    if act == "backup":
        return {"done": True, "message": backup("manual")}

    raise ValueError("unknown action")
