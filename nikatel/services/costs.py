"""Costs: what the servers, the CDN, the domains and the rest cost in a month, and what one GB costs (the owner, 2026-10-31: "I tell it the price of each server and each TB, and it tells me how much I spent this month" -
"what each 1 GB costs me").

Nothing here touches a server or writes to a panel. The prices are typed by the owner: a list of ITEMS in the `costs` settings document. The traffic of every node comes from the PasarGuard panels
(pg_usage, read only, whole Tehran days). One item is one of:

  monthly   a price per month (a server's rent); a part of a month is counted by days
  yearly    a price per year (a domain), a twelfth of it every month
  once      one payment (a fee, one invoice) spread over `months` months, from the month of `starts`
  per_tb    a price per TB of the traffic of ONE node (or of all nodes, `node` = "*"), after `included` free TB
  per_gb    the same per GB

Money is kept in the currency it was typed in and turned into toman with the rates the owner types (IRT is toman itself); an item whose currency has no rate yet is NOT counted and the page says so. A month is
a Jalali or a Gregorian month (the owner picks; Jalali by default): "so far" counts the days that have passed, "month" is the whole month (the traffic of the days that have passed carried on to the end).
A finished month is frozen the first time it is seen with its traffic, so a later change of a price does not rewrite the history (`recompute` writes it again)."""
from __future__ import annotations

import datetime as dt
import itertools
import json
import math
import re
import subprocess
import threading
import time
from typing import Any

import httpx

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Panel, Server, ServerSample
from . import panels, pg_usage, settings
from .panels import PanelError

log = get_logger("costs")

GROUPS = (("foreign", "Foreign servers"), ("iran", "Iran servers"), ("cdn", "CDN and traffic"), ("domain", "Domains"), ("software", "Software and services"), ("other", "Other"))
KINDS = ("monthly", "yearly", "once", "per_tb", "per_gb")
CURRENCIES = ("IRT", "USD", "EUR", "TRY")
CALENDARS = ("jalali", "gregorian")
DIRS = ("out", "both")                              # which traffic a price counts: what the node / server SENDS (the download), or both ways
TB = 1000.0                                         # GB in a TB for the prices by traffic (the providers' unit; the owner's own sums use it)
SAMPLE_DAYS = 8                                     # the panel keeps about a week of 2-minute samples of every server: the traffic of an Iran server is an estimate from them
SAMPLE_GAP = 600                                    # seconds: a longer hole between two samples is counted as this long (the server was not asked)
GIB = 1024 ** 3
INCOME_TTL = 120                                    # seconds a read of the bot's income is kept (the page redraws every ~15 s and the bot host rate-limits new SSH connections)
INCOME_RETRY = 60                                   # seconds after a failed read before the bot host is tried again (the last good read is shown meanwhile)
INCOME_STALE = 6 * 3600                             # a read this old is still shown, with a note, while the bot host cannot be reached; a finished range never changes
INCOME_TRIES = 2                                    # "Connection refused" (the firewall's limit of new connections) is tried again after INCOME_WAIT seconds (the page waits for it: a short wait)
INCOME_WAIT = 4
INCOME_KINDS = ("buy", "renew", "wallet", "other")
RATES_EVERY = 1800                                  # seconds between two reads of the site by the job
RATES_RETRY = 300                                   # ... after a read that failed
RATES_URL = "https://chand.online/api/market"       # the site the owner named for the day's rates (2026-10-31): {"prices": {"usd": {"priceToman": ...}, "eur": ..., "try": ...}, "updatedAt": ms}
MAX_ITEMS = 300
MAX_MONEY = 1e12
MAX_FROZEN = 60                       # months kept in the history
_KEY = re.compile(r"^(\d{4})-(\d{2})$")
_LOCK = threading.Lock()              # an item is added / changed / removed by reading the list and writing all of it back: two at the same moment must not lose each other


# ------------------------------------------------------------------ the typed prices
def _money(v, what: str) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None

    if not math.isfinite(n) or n < 0 or n > MAX_MONEY:
        raise ValueError(f"{what} must be a number from 0 to {MAX_MONEY:.0f}")

    return round(n, 6)


def _day(v, what: str) -> str:
    if not str(v or "").strip():
        return ""

    try:
        return pg_usage.parse_date(str(v)).isoformat()
    except ValueError as e:
        raise ValueError(f"{what}: {e}") from None


def clean_item(raw: dict) -> dict:
    """One item as the page sends it -> the item as it is kept (everything checked, nothing else let through)."""
    name = str(raw.get("name") or "").strip()

    if not 1 <= len(name) <= 60:
        raise ValueError("the name has 1 to 60 characters")

    group, kind, cur = str(raw.get("group") or "other"), str(raw.get("kind") or ""), str(raw.get("cur") or "IRT")

    if group not in dict(GROUPS):
        raise ValueError("the group must be one of: " + ", ".join(dict(GROUPS)))

    if kind not in KINDS:
        raise ValueError("the kind must be one of: " + ", ".join(KINDS))

    if cur not in CURRENCIES:
        raise ValueError("the currency must be one of: " + ", ".join(CURRENCIES))

    try:
        sid = int(raw.get("server_id") or 0)
        months = int(raw.get("months") or 1)
    except (TypeError, ValueError):
        raise ValueError("the server and the months must be whole numbers") from None

    if sid < 0 or not 1 <= months <= 120:
        raise ValueError("the months are 1 to 120")

    node = str(raw.get("node") or "").strip()

    if len(node) > 60:
        raise ValueError("the node name has at most 60 characters")

    starts, ends = _day(raw.get("starts"), "the start"), _day(raw.get("ends"), "the end")

    if starts and ends and ends < starts:
        raise ValueError("the end is before the start")

    if kind == "once" and not starts:
        raise ValueError("a single payment needs the day it was paid")

    note = str(raw.get("note") or "").strip()[:200]
    included = _money(raw.get("included") or 0, "the free traffic") if kind in ("per_tb", "per_gb") else 0.0
    tax = _money(raw.get("tax") or 0, "the tax")

    if tax > 100:
        raise ValueError("the tax is 0 to 100 percent")

    dirs = str(raw.get("dirs") or "out")

    if dirs not in DIRS:
        raise ValueError("the traffic that counts is: " + ", ".join(DIRS))

    quota = _money(raw.get("quota") or 0, "the traffic of the plan") if kind in ("monthly", "yearly") else 0.0
    return {"name": name, "group": group, "kind": kind, "amount": _money(raw.get("amount"), "the price"), "cur": cur, "tax": tax, "server_id": sid, "node": node, "included": included,
            "dirs": dirs, "quota": quota, "starts": starts, "ends": ends, "months": months if kind == "once" else 1, "note": note}


def get_state() -> dict:
    doc = settings.get("costs")
    return {"items": doc["items"], "rates": doc["rates"], "rates_at": doc["rates_at"], "calendar": doc["calendar"], "income": doc["income"], "rates_auto": bool(doc["rates_auto"]),
            "rates_info": doc["rates_info"], "rates_checked": int(doc["rates_checked"] or 0), "rates_error": doc["rates_error"], "rates_stale": bool(doc["rates_stale"])}


def fetch_rates(now: float | None = None) -> dict:
    """Today's dollar, euro and lira in toman from RATES_URL (read only), saved as the rates together with the day's change and the time of each price. A reply that is not a plausible number
    is refused and nothing is saved."""
    try:
        r = httpx.get(RATES_URL, timeout=20, headers={"Accept": "application/json"})
        r.raise_for_status()
        j = r.json()
        got = {c: float(j["prices"][c.lower()]["priceToman"]) for c in ("USD", "EUR", "TRY")}
        at = dt.datetime.fromtimestamp(float(j.get("updatedAt") or 0) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if j.get("updatedAt") else ""
        info = {}

        for c in got:
            p = j["prices"][c.lower()]
            info[c] = {"pct": round(float(p.get("changePct") or 0), 2), "at": dt.datetime.fromtimestamp(float(p.get("ts") or 0) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if p.get("ts") else ""}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError, OSError) as e:
        raise RuntimeError(f"the site did not give the rates: {str(e)[:100]}") from None

    for c, v in got.items():
        if not math.isfinite(v) or not 1 <= v <= 1e8:
            raise RuntimeError(f"the site gave a rate for {c} that cannot be right ({v})")

    save_settings(got)
    settings.put("costs", {"rates_at": at, "rates_info": info, "rates_checked": int(time.time() if now is None else now), "rates_error": "", "rates_stale": bool(j.get("stale"))})
    return get_state()


def run_rates(now: float | None = None) -> bool:
    """The job (every 10 minutes): today's rates from the site, unless the owner switched "automatic" off, and only when the last read is older than RATES_EVERY - a failed read is tried again after
    RATES_RETRY, and the rates that were there stay. Returns whether the site was asked."""
    doc = settings.get("costs")

    if not doc["rates_auto"]:
        return False

    t = time.time() if now is None else now

    if t - float(doc["rates_checked"] or 0) < (RATES_RETRY if doc["rates_error"] else RATES_EVERY):
        return False

    try:
        fetch_rates(t)
    except RuntimeError as e:
        log.info("costs: the rates were not read: %s", str(e)[:120])
        settings.put("costs", {"rates_error": str(e)[:160], "rates_checked": int(t)})

    return True


def save_settings(rates: dict | None = None, calendar: str | None = None, bot_server_id: int | None = None, rates_auto: bool | None = None) -> dict:
    with _LOCK:
        changes: dict[str, Any] = {}

        if rates_auto is not None:
            changes["rates_auto"] = bool(rates_auto)

            if rates_auto:
                changes["rates_checked"] = 0                       # switched on: the job reads the site at its next turn

        if bot_server_id is not None:
            if bot_server_id < 0:
                raise ValueError("the bot server is an inventory server (or 0 for none)")

            if bot_server_id:
                with session() as db:
                    if db.get(Server, bot_server_id) is None:
                        raise ValueError("that server is not in the inventory")

            changes["income"] = {**settings.get("costs")["income"], "bot_server_id": int(bot_server_id)}

        if rates is not None:
            cur = dict(settings.get("costs")["rates"])

            for k, v in rates.items():
                if k not in CURRENCIES or k == "IRT":
                    raise ValueError("a rate is for USD or EUR (toman needs none)")

                cur[k] = _money(v, f"the {k} rate")

            changes["rates"] = cur

        if calendar is not None:
            if calendar not in CALENDARS:
                raise ValueError("the calendar is jalali or gregorian")

            changes["calendar"] = calendar

        if changes:
            settings.put("costs", changes)

    return get_state()


def add_item(raw: dict) -> dict:
    item = clean_item(raw)

    with _LOCK:
        doc = settings.get("costs")

        if len(doc["items"]) >= MAX_ITEMS:
            raise ValueError(f"at most {MAX_ITEMS} items")

        item["id"] = int(doc["seq"]) + 1
        settings.put("costs", {"items": doc["items"] + [item], "seq": item["id"]})

    return item


def update_item(item_id: int, raw: dict) -> dict:
    item = clean_item(raw)

    with _LOCK:
        doc = settings.get("costs")

        if not any(i["id"] == item_id for i in doc["items"]):
            raise ValueError("no such item")

        item["id"] = item_id
        settings.put("costs", {"items": [item if i["id"] == item_id else i for i in doc["items"]]})

    return item


def delete_item(item_id: int) -> None:
    with _LOCK:
        doc = settings.get("costs")

        if not any(i["id"] == item_id for i in doc["items"]):
            raise ValueError("no such item")

        settings.put("costs", {"items": [i for i in doc["items"] if i["id"] != item_id]})


# ------------------------------------------------------------------ months (Jalali or Gregorian, "1405-08" or "2026-10")
def key_parts(key: str) -> tuple[int, int, str]:
    """(year, month, calendar) of a month key; the year tells the calendar, as parse_date does."""
    m = _KEY.match(str(key or ""))

    if not m:
        raise ValueError("a month looks like 1405-08 or 2026-10")

    y, mo = int(m.group(1)), int(m.group(2))

    if not 1 <= mo <= 12:
        raise ValueError("the month is 01 to 12")

    if 1300 <= y <= 1600:
        return y, mo, "jalali"

    if 1900 <= y <= 2200:
        return y, mo, "gregorian"

    raise ValueError("the year must be Jalali (1300-1600) or Gregorian (1900-2200)")


def key_of(d: dt.date, cal: str) -> str:
    if cal == "jalali":
        y, m, _d = pg_usage.date_to_jalali(d)
    else:
        y, m = d.year, d.month

    return f"{y:04d}-{m:02d}"


def month_bounds(key: str) -> tuple[dt.date, dt.date]:
    y, m, cal = key_parts(key)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)

    if cal == "jalali":
        first, nxt = pg_usage.jalali_to_date(y, m, 1), pg_usage.jalali_to_date(ny, nm, 1)
    else:
        first, nxt = dt.date(y, m, 1), dt.date(ny, nm, 1)

    return first, nxt - dt.timedelta(days=1)


def shift_key(key: str, n: int) -> str:
    y, m, _cal = key_parts(key)
    i = y * 12 + (m - 1) + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def key_label(key: str) -> str:
    y, m, cal = key_parts(key)
    return f"{pg_usage.JALALI_MONTHS[m - 1]} {y}" if cal == "jalali" else dt.date(y, m, 1).strftime("%B %Y")


def _months_between(a: str, b: str) -> int:
    ya, ma, _ = key_parts(a)
    yb, mb, _ = key_parts(b)
    return (yb * 12 + mb) - (ya * 12 + ma)


# ------------------------------------------------------------------ the arithmetic (pure)
def _overlap(a0: dt.date, a1: dt.date, b0: dt.date, b1: dt.date) -> int:
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0, (hi - lo).days + 1)


def _rate_of(cur: str, rates: dict) -> float | None:
    if cur == "IRT":
        return 1.0

    r = float(rates.get(cur) or 0)
    return r if r > 0 else None


def _gb(rec: dict, dirs: str) -> float:
    """The GB of a traffic record that a price counts: what was SENT, or both ways (a record without the split counts its whole `node_gb`)."""
    return float(rec["out_gb"]) if dirs == "out" and "out_gb" in rec else float(rec.get("node_gb") or 0)


def compute(items: list[dict], rates: dict, key: str, today: dt.date, traffic: dict[str, dict], traffic_ok: bool = True, servers: dict[int, str] | None = None,
            known_nodes: set[str] | None = None, coverage: float = 1.0) -> dict:
    """What `items` cost in the month `key` as seen on `today`. `traffic` is {name in lower case: {"name", "users_gb", "node_gb", "out_gb", "in_gb", "kind"}} for the days of the month that have passed:
    kind "node" (the default) = a node of a PasarGuard panel, what the customers used; kind "iran" = an Iran server, an estimate from the panel's own samples (users_gb 0: it is no customers' traffic).
    `coverage` (0-1) is the share of the days that have passed for which the panel HAS traffic (a new panel counts only from the day it started): the customers' GB and the price of a GB are scaled
    up from the days it has, so a month that began before the panel did does not show a price of a GB made of all the cost and some of the traffic.
    Returns toman everywhere; the page shows dollars with the rate. Pure: no clock, no panel, no database."""
    servers = servers or {}
    first, last = month_bounds(key)
    days = (last - first).days + 1
    elapsed = 0 if today < first else days if today >= last else (today - first).days + 1
    upto = min(last, today)
    factor = days / elapsed if 0 < elapsed < days else 1.0
    known = {n.lower() for n in (known_nodes or set())} | set(traffic)
    pg = {k: t for k, t in traffic.items() if t.get("kind", "node") == "node"}
    all_users = sum(t["users_gb"] for t in pg.values())
    all_node = sum(t["node_gb"] for t in pg.values())
    scale = 1 / coverage if 0 < coverage < 1 else 1.0
    all_users_est = all_users * scale
    estimated = False
    warnings: list[str] = []
    rows: list[dict] = []

    for it in items:
        rate = _rate_of(it["cur"], rates)

        if rate is not None:
            rate *= 1 + float(it.get("tax") or 0) / 100                         # a price plus its tax (VAT): everything below is the price WITH the tax

        sname = servers.get(it.get("server_id") or 0, "")
        node = (it.get("node") or "").strip() or (sname if sname.lower() in known else "")
        s0 = dt.date.fromisoformat(it["starts"]) if it.get("starts") else dt.date.min
        s1 = dt.date.fromisoformat(it["ends"]) if it.get("ends") else dt.date.max
        in_month, in_past = _overlap(s0, s1, first, last), _overlap(s0, s1, first, upto) if elapsed else 0
        so_far = total = 0.0
        gb = None
        state = "ok"

        if rate is None:
            state = "no_rate"
            warnings.append(f"“{it['name']}” is in {it['cur']} and there is no {it['cur']} rate yet: it is not counted.")
        elif not in_month:
            state = "idle"
        elif it["kind"] in ("monthly", "yearly"):
            base = it["amount"] * rate / (12 if it["kind"] == "yearly" else 1)
            so_far, total = base * in_past / days, base * in_month / days
        elif it["kind"] == "once":
            n = _months_between(key_of(s0, key_parts(key)[2]), key)

            if 0 <= n < it["months"]:
                share = it["amount"] * rate / it["months"]
                total = share
                so_far = share if elapsed and (n > 0 or s0 <= today) else 0.0
        else:                                                          # per_tb / per_gb
            rec = None

            if node == "*":
                rec = {"out_gb": sum(_gb(t, "out") for t in pg.values()), "node_gb": all_node}
            elif node:
                rec = traffic.get(node.lower())
            else:
                state = "needs_node"
                warnings.append(f"“{it['name']}” is priced by traffic but no node is chosen: it is not counted.")

            iran = bool(rec) and rec.get("kind") == "iran"

            if node and not traffic_ok and not iran:
                state = "no_traffic"
            elif node:
                got = _gb(rec, it["dirs"]) if rec else 0.0
                unit = TB if it["kind"] == "per_tb" else 1.0
                free = it["included"]
                gb = round(got, 2)
                estimated = estimated or iran
                so_far = it["amount"] * rate * max(0.0, got / unit - free)
                total = it["amount"] * rate * max(0.0, got * factor / unit - free)

        quota = None
        qrec = traffic.get(node.lower()) if node and node != "*" else None

        if it.get("quota") and state == "ok" and qrec and (traffic_ok or qrec.get("kind") == "iran"):          # a flat price with so much traffic in the plan: how much of it the node uses
            gb_month = _gb(qrec, it["dirs"]) * factor
            pct = gb_month / (it["quota"] * TB) * 100
            quota = {"tb": it["quota"], "gb_month": round(gb_month, 1), "pct": round(pct)}
            estimated = estimated or qrec.get("kind") == "iran"

            if pct >= 100:
                warnings.append(f"“{it['name']}” would pass the {it['quota']:g} TB of its plan this month ({gb_month / TB:.1f} TB at this pace).")
            elif pct >= 90:
                warnings.append(f"“{it['name']}” is close to the {it['quota']:g} TB of its plan ({gb_month / TB:.1f} TB at this pace).")

        rows.append({"id": it["id"], "name": it["name"], "group": it["group"], "kind": it["kind"], "cur": it["cur"], "amount": it["amount"], "tax": it.get("tax") or 0, "node": node, "server": sname,
                     "state": state, "gb": gb, "quota": quota, "so_far": round(so_far), "month": round(total)})

    if not traffic_ok:
        warnings.append("The traffic could not be read from the panel just now: the prices by traffic and the price of a GB are left out.")
    elif elapsed and not all_users:
        warnings.append("The panel recorded no traffic in these days (it counts only from the day it started).")

    if scale > 1 and traffic_ok and all_users:
        warnings.append(f"The panel has no traffic for the first {round(elapsed * (1 - coverage))} days of this month (it counts from the day it started): the GB of the month and the price of a GB are scaled from the other days.")

    if estimated:
        warnings.append("The traffic of the Iran servers is an estimate from the last days of the panel's own samples (about ±10 %).")

    total_so_far, total_month = sum(r["so_far"] for r in rows), sum(r["month"] for r in rows)
    by_group = {g: {"name": n, "so_far": sum(r["so_far"] for r in rows if r["group"] == g), "month": sum(r["month"] for r in rows if r["group"] == g)} for g, n in GROUPS}
    by_node: list[dict] = []
    by_server: list[dict] = []

    for lname, t in sorted(traffic.items(), key=lambda kv: -kv[1]["node_gb"]):
        mine = [r for r in rows if r["node"].lower() == lname]
        c_far, c_month = sum(r["so_far"] for r in mine), sum(r["month"] for r in mine)

        if t.get("kind") == "iran":
            by_server.append({"name": t["name"], "out_gb": round(t.get("out_gb", 0.0), 1), "in_gb": round(t.get("in_gb", 0.0), 1), "so_far": c_far, "month": c_month, "priced": bool(mine),
                              "per_gb": round(c_far / t["out_gb"], 2) if mine and t.get("out_gb") else None})
            continue

        by_node.append({"name": t["name"], "gb": round(t["users_gb"], 2), "node_gb": round(t["node_gb"], 2), "so_far": c_far, "month": c_month, "priced": bool(mine),
                        "per_gb": round(c_far / (t["users_gb"] * scale), 2) if mine and t["users_gb"] > 0 and traffic_ok else None})

    per_gb = round(total_so_far / all_users_est, 2) if traffic_ok and all_users > 0 else None
    per_gb_month = round(total_month / (all_users_est * factor), 2) if traffic_ok and all_users > 0 else None
    return {"month": key, "label": key_label(key), "calendar": key_parts(key)[2], "first": first.isoformat(), "last": last.isoformat(), "days": days, "elapsed": elapsed, "open": today <= last,
            "so_far": total_so_far, "total": total_month, "gb": round(all_users, 2), "gb_est": round(all_users_est, 2), "coverage": round(coverage, 3), "node_gb": round(all_node, 2),
            "gb_month": round(all_users_est * factor, 2), "per_gb": per_gb, "per_gb_month": per_gb_month,
            "usd_rate": float(rates.get("USD") or 0) or None, "by_group": by_group, "by_node": by_node, "by_server": by_server, "items": rows, "warnings": warnings, "traffic_ok": traffic_ok}


# ------------------------------------------------------------------ the traffic (read only)
def traffic_of(first: dt.date, last: dt.date, now: dt.datetime | None = None) -> tuple[dict[str, dict], bool, list[str], set[str]]:
    """The traffic of every node from the first to the last day (cut at today) -> (traffic by node, all panels answered, what went wrong, every node name the panels know)."""
    today = pg_usage.tehran_date(now or dt.datetime.now(dt.timezone.utc))
    out: dict[str, dict] = {}
    errors: list[str] = []
    names: set[str] = set()

    with session() as db:
        wanted = [(p.id, p.name, [str(n.get("name") or "") for n in (p.nodes or []) if isinstance(n, dict)]) for p in db.query(Panel).filter(Panel.type == "pasarguard", Panel.enabled.is_(True)).order_by(Panel.id)]

    for _id, _name, nodes in wanted:
        names |= {n for n in nodes if n}

    if first > today:
        return out, True, errors, names                                      # the month has not begun: nothing to ask

    out.update(_iran_samples(first, min(last, today), now))                  # (the Iran servers do not wait for a panel to answer)

    if not wanted:
        return out, False, ["no PasarGuard panel is connected"], names

    for pid, pname, _nodes in wanted:
        try:
            d = pg_usage.usage(panels.cred_for(pid), "custom", first.isoformat(), min(last, today).isoformat(), now)
        except (ValueError, RuntimeError, PanelError, OSError) as e:
            errors.append(f"{pname}: {str(e)[:120]}")
            continue

        for r in d.get("by_node") or []:
            rec = out.setdefault(str(r["name"]).lower(), {"name": str(r["name"]), "kind": "node", "users_gb": 0.0, "node_gb": 0.0, "out_gb": 0.0, "in_gb": 0.0})
            rec["users_gb"] += float(r.get("gb") or 0)

            if "up_gb" in r:                                                  # PasarGuard: the node's own up (from the users) and down (to the users)
                rec["node_gb"] += float(r["up_gb"] + r["down_gb"])
                rec["out_gb"] += float(r["down_gb"])
                rec["in_gb"] += float(r["up_gb"])
            else:
                rec["node_gb"] += float(r.get("gb") or 0)
                rec["out_gb"] += float(r.get("gb") or 0)

    return out, not errors, errors, names


def traffic_since(first: dt.date, upto: dt.date, now: dt.datetime | None = None) -> dt.date | None:
    """The first Tehran day of `first`..`upto` on which a PasarGuard panel recorded any traffic: a new panel counts only from the day it started, so a month that began earlier has days without traffic.
    Read from the day-by-day series of the usage question `traffic_of` has just asked (pg_usage keeps its answer). None for a range of one or two days (the series is by the hour then), when no panel
    answers, or when nothing was recorded."""
    if (upto - first).days + 1 <= 2:
        return None

    with session() as db:
        ids = [p.id for p in db.query(Panel).filter(Panel.type == "pasarguard", Panel.enabled.is_(True)).order_by(Panel.id)]

    best = None

    for pid in ids:
        try:
            d = pg_usage.usage(panels.cred_for(pid), "custom", first.isoformat(), upto.isoformat(), now)
        except (ValueError, RuntimeError, PanelError, OSError):
            continue

        if d.get("granularity") != "day":
            continue

        for point in d.get("series") or []:
            if point.get("bytes"):
                try:
                    day = dt.date.fromisoformat(str(point["t"])[:10])
                except ValueError:
                    break

                best = day if best is None else min(best, day)
                break

    return best


def _iran_samples(first: dt.date, upto: dt.date, now: dt.datetime | None) -> dict[str, dict]:
    """The traffic of every Iran server over the days `first`..`upto`: the panel keeps a week of 2-minute samples (rx / tx in Mbps) of each server; their average over the last SAMPLE_DAYS days
    (rates x time, a hole between samples counted as SAMPLE_GAP at most) is carried over those days. An estimate, good to about 10 %; a server with under an hour of samples is left out."""
    now_ts = int((now or dt.datetime.now(dt.timezone.utc)).timestamp())
    days = (upto - first).days + 1
    out: dict[str, dict] = {}

    with session() as db:
        for s in db.query(Server).filter(Server.role == "iran").all():
            rows = db.query(ServerSample.ts, ServerSample.rx_mbps, ServerSample.tx_mbps).filter(ServerSample.server_id == s.id, ServerSample.ts >= now_ts - SAMPLE_DAYS * 86400).order_by(ServerSample.ts).all()
            rx = tx = active = 0.0

            for (t0, _r0, _w0), (t1, r, w) in itertools.pairwise(rows):
                d = min(t1 - t0, SAMPLE_GAP)

                if d <= 0:
                    continue

                rx += (r or 0.0) * 125000.0 * d                               # 1 Mbps = 125,000 bytes a second
                tx += (w or 0.0) * 125000.0 * d
                active += d

            if active < 3600:
                continue

            k = 86400.0 / active * days / GIB
            out[s.name.lower()] = {"name": s.name, "kind": "iran", "users_gb": 0.0, "out_gb": tx * k, "in_gb": rx * k, "node_gb": (rx + tx) * k, "sample_days": round(active / 86400, 1)}

    return out


# ------------------------------------------------------------------ the income of the shop bot (read only)
_INC_CACHE: dict[tuple, tuple[float, dict]] = {}
_INC_FAIL: dict[int, tuple[float, str]] = {}          # by the bot's server: when it last could not be reached (every month asks the same host)
_INC_LOCK = threading.Lock()
_clock = time.monotonic                              # the tests move this clock


def _int(v) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def _income_ssh(host: str, port: int, user: str, a: int, b: int) -> str:
    """Runs `income A B` on the bot host with the panel's OWN key for it (data dir, `income_key`): on that host the key is restricted to one script that prints the sums of the approved
    payments between two unix times (counts and totals, no name, no id) - it cannot run anything else. A list of arguments, no shell: A and B are whole numbers."""
    s = get_settings()
    key = s.data_dir / "income_key"

    if not key.is_file():
        raise RuntimeError("the key the panel reads the bot with (income_key) is not in its data folder")

    cm = s.data_dir / "cm"
    cm.mkdir(parents=True, exist_ok=True)
    argv = ["ssh", "-i", str(key), "-p", str(int(port)), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={s.known_hosts}",
            "-o", f"ConnectTimeout={s.ssh_connect_timeout}", "-o", "LogLevel=ERROR",
            "-o", "ControlMaster=auto", "-o", f"ControlPath={cm / 'income-%C'}", "-o", "ControlPersist=600",          # one connection kept ten minutes: the next read opens no new TCP connection
            "--", f"{user}@{host}", f"income {int(a)} {int(b)}"]
    p = None

    for attempt in range(INCOME_TRIES):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"the bot host did not answer: {str(e)[:80]}") from None

        if p.returncode == 0 or p.stdout.strip() or "refused" not in p.stderr.lower() or attempt == INCOME_TRIES - 1:
            break

        time.sleep(INCOME_WAIT)                                                  # "Connection refused" is the firewall's limit of new connections: a moment later it lets one through

    if p.returncode != 0 and not p.stdout.strip():
        raise RuntimeError("the bot host refused: " + (p.stderr.strip()[-120:] or f"exit {p.returncode}"))

    return p.stdout


def bot_income(first: dt.date, upto: dt.date, now: dt.datetime | None = None) -> dict | None:
    """The approved payments of the shop bot from the start of Tehran day `first` to the end of `upto` (cut at now): {total, count, payers, by_kind: {buy, renew, wallet, other: {n, sum}}}. None when no bot
    server is chosen. Kept for INCOME_TTL seconds and asked one at a time: the bot host's firewall refuses a burst of new SSH connections."""
    sid = int((settings.get("costs").get("income") or {}).get("bot_server_id") or 0)

    if not sid:
        return None

    with session() as db:
        srv = db.get(Server, sid)
        target = (srv.host, srv.ssh_port, srv.ssh_user) if srv is not None else None

    if target is None:
        raise ValueError("the server chosen for the bot is not in the inventory any more")

    now_dt = now or dt.datetime.now(dt.timezone.utc)
    a = int(pg_usage.tehran_day_start(first).timestamp())
    b = min(int(pg_usage.tehran_day_start(upto + dt.timedelta(days=1)).timestamp()), int(now_dt.timestamp()))

    if b <= a:
        return {"total": 0, "count": 0, "payers": 0, "by_kind": {k: {"n": 0, "sum": 0} for k in INCOME_KINDS}, "from": a, "to": a}

    key = (sid, a, int(pg_usage.tehran_day_start(upto + dt.timedelta(days=1)).timestamp()))          # the range, not the moment: its end is cut at now and moves
    finished = b >= key[2]                                                                              # the whole last day is over: the answer can never change

    with _INC_LOCK:
        hit = _INC_CACHE.get(key)
        age = _clock() - hit[0] if hit else None

        if hit and (finished or age < INCOME_TTL):
            return hit[1]

        if _INC_FAIL.get(sid) and _clock() - _INC_FAIL[sid][0] < INCOME_RETRY:                           # the host failed a moment ago (for this month or another): not at once again
            return _stale(hit, age, _INC_FAIL[sid][1])

        try:
            res = _parse_income(_income_ssh(*target, a, b), a, b)
        except RuntimeError as e:
            _INC_FAIL[sid] = (_clock(), str(e))
            return _stale(hit, age, str(e))

        _INC_FAIL.pop(sid, None)

        for old in [k for k, v in _INC_CACHE.items() if _clock() - v[0] >= INCOME_STALE]:
            _INC_CACHE.pop(old)

        _INC_CACHE[key] = (_clock(), res)
        return res


def _stale(hit: tuple[float, dict] | None, age: float | None, error: str) -> dict:
    """The last good read, marked old, when the bot host cannot be reached now; with none (or one older than INCOME_STALE) the failure is raised."""
    if hit is None or age is None or age > INCOME_STALE:
        raise RuntimeError(error)

    return {**hit[1], "stale": True, "age": int(age), "error": error[:120]}


def _parse_income(out: str, a: int, b: int) -> dict:
    try:
        d = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError("the answer of the bot host is not JSON") from None

    if not isinstance(d, dict) or not d.get("ok"):
        raise RuntimeError("the bot host said: " + str(d.get("error") if isinstance(d, dict) else d)[:100])

    try:
        return {"total": _int(d["total"]), "count": _int(d["count"]), "payers": _int(d["payers"]), "from": a, "to": b, "by_kind": {k: {"n": _int(d[k]["n"]), "sum": _int(d[k]["sum"])} for k in INCOME_KINDS}}
    except (KeyError, TypeError):
        raise RuntimeError("the answer of the bot host is not what was asked for") from None


# ------------------------------------------------------------------ the page's answers
def _servers() -> dict[int, str]:
    with session() as db:
        return {s.id: s.name for s in db.query(Server).all()}


def _one(doc: dict, key: str, now: dt.datetime | None) -> dict:
    first, last = month_bounds(key)
    today = pg_usage.tehran_date(now or dt.datetime.now(dt.timezone.utc))
    traffic, ok, errors, names = traffic_of(first, last, now)
    coverage = 1.0

    if ok:
        upto = min(last, today)
        since = traffic_since(first, upto, now)

        if since is not None and since > first:                              # the panel began counting after the month began
            coverage = max(((upto - since).days + 1) / ((upto - first).days + 1), 0.05)

    out = compute(doc["items"], doc["rates"], key, today, traffic, ok, _servers(), names, coverage)
    out["errors"] = errors

    if (doc.get("income") or {}).get("bot_server_id"):
        try:
            raw = bot_income(first, min(last, today), now)
        except (RuntimeError, ValueError, OSError) as e:
            out["warnings"].append(f"The shop bot's income could not be read: {str(e)[:140]}")
        else:
            if raw is not None:
                if raw.get("stale"):
                    out["warnings"].append(f"The shop bot's income is from {max(1, raw['age'] // 60)} min ago: the bot host could not be reached now ({raw['error']}).")

                f = out["days"] / out["elapsed"] if 0 < out["elapsed"] < out["days"] else 1.0
                month = round(raw["total"] * f)
                out["income"] = {"bot": raw, "so_far": raw["total"], "month": month, "profit_so_far": raw["total"] - out["so_far"], "profit_month": month - out["total"],
                                 "profit_per_gb": round((raw["total"] - out["so_far"]) / out["gb_est"], 2) if out["gb_est"] > 0 and out["traffic_ok"] else None}

    return out


def _freeze(key: str, s: dict, now: dt.datetime | None = None) -> None:
    doc = settings.get("costs")
    frozen = dict(doc["frozen"])
    frozen[key] = {**s, "frozen_at": int((now or dt.datetime.now(dt.timezone.utc)).timestamp())}

    for old in sorted(frozen)[:-MAX_FROZEN]:
        frozen.pop(old)

    settings.put("costs", {"frozen": frozen})


def _final(entry: dict | None, last: dt.date) -> bool:
    """A saved month counts only when it was saved after its last day was over (1.69.169-173 saved it ON that day, with the evening's traffic and income missing)."""
    at = (entry or {}).get("frozen_at")
    return bool(at) and pg_usage.tehran_date(dt.datetime.fromtimestamp(int(at), dt.timezone.utc)) > last


GREG_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _span(a: tuple[int, int, int], b: tuple[int, int, int], names: tuple[str, ...]) -> str:
    """(year, month, day) to (year, month, day) as "23 Oct – 21 Nov 2026": what is said twice is said once."""
    (ay, am, ad), (by, bm, bd) = a, b
    left = f"{ad}" if (ay, am) == (by, bm) else f"{ad} {names[am - 1]}" if ay == by else f"{ad} {names[am - 1]} {ay}"
    return f"{left} – {bd} {names[bm - 1]} {by}"


def day_info(today: dt.date, first: dt.date, last: dt.date) -> dict:
    """What the bar says about today and about the days of the month, in both calendars: today_j "9 Aban 1405", today_g "31 Oct 2026", span_j / span_g (the first to the last day of the month)."""
    jy, jm, jd = pg_usage.date_to_jalali(today)
    return {"today": today.isoformat(), "today_j": f"{jd} {pg_usage.JALALI_MONTHS[jm - 1]} {jy}", "today_g": f"{today.day} {GREG_MONTHS[today.month - 1]} {today.year}", "weekday": WEEKDAYS[today.weekday()],
            "span_j": _span(pg_usage.date_to_jalali(first), pg_usage.date_to_jalali(last), pg_usage.JALALI_MONTHS),
            "span_g": _span((first.year, first.month, first.day), (last.year, last.month, last.day), GREG_MONTHS)}


def summary(month: str = "", recompute: bool = False, now: dt.datetime | None = None) -> dict:
    """The month asked for (the current one by default). A finished month comes from the history when it was frozen; the first time it is seen with its traffic it is frozen."""
    doc = settings.get("costs")
    today = pg_usage.tehran_date(now or dt.datetime.now(dt.timezone.utc))
    key = month or key_of(today, doc["calendar"])
    first, last = month_bounds(key)

    current = key_of(today, doc["calendar"])
    info = day_info(today, first, last)

    if last < today and not recompute and _final(doc["frozen"].get(key), last):
        return {**doc["frozen"][key], "frozen": True, "current": current, **info}

    s = _one(doc, key, now)
    s["frozen"], s["current"] = False, current
    s.update(info)

    if not s["open"] and s["traffic_ok"] and doc["items"] and s["total"] and s["gb"]:          # (a month with no traffic at all is not frozen: the panel may have been empty just then)
        _freeze(key, s, now)
        s["frozen"] = True
    elif s["open"] and doc["items"]:
        prev = shift_key(key, -1)

        prev_last = month_bounds(prev)[1]

        if prev_last < today and not _final(doc["frozen"].get(prev), prev_last):   # the month that just ended is put in the history the first time the current one is looked at
            try:
                p = _one(doc, prev, now)

                if p["traffic_ok"] and p["total"] and p["gb"]:
                    _freeze(prev, p, now)
            except (ValueError, RuntimeError, OSError) as e:
                log.info("costs: the last month was not frozen: %s", str(e)[:100])

    return s


def history() -> list[dict]:
    doc = settings.get("costs")
    return [{"month": k, "label": v.get("label") or key_label(k), "total": v["total"], "gb": v["gb"], "per_gb": v["per_gb"]} for k, v in sorted(doc["frozen"].items(), reverse=True)]


def lists() -> dict:
    """What the form needs: the servers of the inventory and the nodes the panels know."""
    with session() as db:
        servers = [{"id": s.id, "name": s.name, "role": s.role} for s in db.query(Server).order_by(Server.role, Server.name).all()]
        nodes = sorted({str(n.get("name") or "") for p in db.query(Panel).filter(Panel.type == "pasarguard").all() for n in (p.nodes or []) if isinstance(n, dict) and n.get("name")}, key=str.lower)

    return {"servers": servers, "nodes": nodes, "groups": [{"key": k, "name": n} for k, n in GROUPS], "kinds": list(KINDS), "currencies": list(CURRENCIES)}
