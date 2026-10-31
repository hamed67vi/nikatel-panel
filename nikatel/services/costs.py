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
import math
import re
import threading
from typing import Any

import httpx

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
    return {"items": doc["items"], "rates": doc["rates"], "rates_at": doc["rates_at"], "calendar": doc["calendar"]}


def fetch_rates() -> dict:
    """Today's dollar, euro and lira in toman from RATES_URL (read only), saved as the rates. A reply that is not a plausible number is refused and nothing is saved."""
    try:
        r = httpx.get(RATES_URL, timeout=20, headers={"Accept": "application/json"})
        r.raise_for_status()
        j = r.json()
        got = {c: float(j["prices"][c.lower()]["priceToman"]) for c in ("USD", "EUR", "TRY")}
        at = dt.datetime.fromtimestamp(float(j.get("updatedAt") or 0) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if j.get("updatedAt") else ""
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError, OSError) as e:
        raise RuntimeError(f"the site did not give the rates: {str(e)[:100]}") from None

    for c, v in got.items():
        if not math.isfinite(v) or not 1 <= v <= 1e8:
            raise RuntimeError(f"the site gave a rate for {c} that cannot be right ({v})")

    save_settings(got)
    settings.put("costs", {"rates_at": at})
    return get_state()


def save_settings(rates: dict | None = None, calendar: str | None = None) -> dict:
    with _LOCK:
        changes: dict[str, Any] = {}

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
            known_nodes: set[str] | None = None) -> dict:
    """What `items` cost in the month `key` as seen on `today`. `traffic` is {name in lower case: {"name", "users_gb", "node_gb", "out_gb", "in_gb", "kind"}} for the days of the month that have passed:
    kind "node" (the default) = a node of a PasarGuard panel, what the customers used; kind "iran" = an Iran server, an estimate from the panel's own samples (users_gb 0: it is no customers' traffic).
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
                        "per_gb": round(c_far / t["users_gb"], 2) if mine and t["users_gb"] > 0 and traffic_ok else None})

    per_gb = round(total_so_far / all_users, 2) if traffic_ok and all_users > 0 else None
    per_gb_month = round(total_month / (all_users * factor), 2) if traffic_ok and all_users > 0 else None
    return {"month": key, "label": key_label(key), "calendar": key_parts(key)[2], "first": first.isoformat(), "last": last.isoformat(), "days": days, "elapsed": elapsed, "open": elapsed < days,
            "so_far": total_so_far, "total": total_month, "gb": round(all_users, 2), "node_gb": round(all_node, 2), "gb_month": round(all_users * factor, 2), "per_gb": per_gb, "per_gb_month": per_gb_month,
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


# ------------------------------------------------------------------ the page's answers
def _servers() -> dict[int, str]:
    with session() as db:
        return {s.id: s.name for s in db.query(Server).all()}


def _one(doc: dict, key: str, now: dt.datetime | None) -> dict:
    first, last = month_bounds(key)
    today = pg_usage.tehran_date(now or dt.datetime.now(dt.timezone.utc))
    traffic, ok, errors, names = traffic_of(first, last, now)
    out = compute(doc["items"], doc["rates"], key, today, traffic, ok, _servers(), names)
    out["errors"] = errors
    return out


def _freeze(key: str, s: dict) -> None:
    doc = settings.get("costs")
    frozen = dict(doc["frozen"])
    frozen[key] = {**s, "frozen_at": int(dt.datetime.now(dt.timezone.utc).timestamp())}

    for old in sorted(frozen)[:-MAX_FROZEN]:
        frozen.pop(old)

    settings.put("costs", {"frozen": frozen})


def summary(month: str = "", recompute: bool = False, now: dt.datetime | None = None) -> dict:
    """The month asked for (the current one by default). A finished month comes from the history when it was frozen; the first time it is seen with its traffic it is frozen."""
    doc = settings.get("costs")
    today = pg_usage.tehran_date(now or dt.datetime.now(dt.timezone.utc))
    key = month or key_of(today, doc["calendar"])
    _first, last = month_bounds(key)

    current = key_of(today, doc["calendar"])

    if last < today and key in doc["frozen"] and not recompute:
        return {**doc["frozen"][key], "frozen": True, "current": current}

    s = _one(doc, key, now)
    s["frozen"], s["current"] = False, current

    if not s["open"] and s["traffic_ok"] and doc["items"] and s["total"] and s["gb"]:          # (a month with no traffic at all is not frozen: the panel may have been empty just then)
        _freeze(key, s)
        s["frozen"] = True
    elif s["open"] and doc["items"]:
        prev = shift_key(key, -1)

        if prev not in doc["frozen"]:                                              # the month that just ended is put in the history the first time the current one is looked at
            try:
                p = _one(doc, prev, now)

                if p["traffic_ok"] and p["total"] and p["gb"]:
                    _freeze(prev, p)
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
