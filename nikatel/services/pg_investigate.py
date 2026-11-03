"""PasarGuard: INVESTIGATE one user - the answer to "my data ended too fast" / "my data goes by itself" (owner, 2026-11-03: type a username and see how much it used, when, through which
server, with which devices and subscription fetches, and a plain verdict).

It only READS, with what the Users window already has: the account, the usage by hour (by node) and by day, the devices, the last fetches of the subscription and the live addresses.
The judging is a pure function (`judge`) so its rules are tested without a panel. What no code here can change, and what the answer says so:
  * the panel keeps no history of the addresses a user connected from (the live list is "right now" only, and exists only while the core has statsUserOnline on);
  * the node of a tunnel sees the entrance server's address, not the customer's;
  * the address of a subscription fetch is the customer's only when the panel sees it (behind a reverse proxy it shows the proxy: 127.0.0.1).
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import statistics

from . import pg_usage, pg_users
from .panels import PanelError

GB = 1024 ** 3
TEHRAN = pg_usage.TEHRAN
DEFAULT_DEVICES = 3                      # the rule of the panel when the user has no limit of its own
FETCHES = 50                             # the fetches read
SHOWN_FETCHES = 15
NIGHT_HOURS = (1, 2, 3, 4, 5)            # Tehran 01:00-05:59
NIGHT_SHARE = 0.4                        # this much of the last two days at night is worth a warning ...
NIGHT_MIN = GB                           # ... when it is at least this much
BURST_HOURS = 3
BURST_SHARE = 0.5
BURST_MIN = 2 * GB
SPIKE_TIMES = 3                          # today at least 3 times the usual day ...
SPIKE_MIN = 3 * GB                       # ... and at least this much
NODE_SHARE = 0.6
NODE_MIN = 5 * GB
MANY = 3                                 # different addresses or apps that fetched the link
LOCAL_IPS = {"", "127.0.0.1", "::1", "localhost"}
LEVELS = {"ok": 0, "info": 1, "warn": 2, "bad": 3}
# Cloudflare's edge ranges (https://www.cloudflare.com/ips-v4 and ips-v6, read 2026-11-03): a request that came through Cloudflare without the customer's address (a worker, a health check, an app that
# is itself behind Cloudflare) is recorded with an edge address that changes from request to request - it is not the customer and must not count as "another address". The IPv4 ranges are written as
# (network address as a number, prefix length) because the repository is public and its secrets check refuses a public address written the usual way.
_CF4 = ((0xADF53000, 20), (0x6715F400, 22), (0x6716C800, 22), (0x671F0400, 22), (0x8D654000, 18), (0x6CA2C000, 18), (0xBE5DF000, 20), (0xBC726000, 20), (0xC5EAF000, 22), (0xC6298000, 17), (0xA29E0000, 15), (0x68100000, 13), (0x68180000, 14), (0xAC400000, 13), (0x83004800, 22))
_CF6 = ("2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32")
CF_NETS = [ipaddress.ip_network((ipaddress.IPv4Address(a), n)) for a, n in _CF4] + [ipaddress.ip_network(n) for n in _CF6]


# ------------------------------------------------------------------ pure pieces
def is_cf(ip) -> bool:
    """True when the address is one of Cloudflare's edge addresses."""
    try:
        a = ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return False

    return any(n.version == a.version and a in n for n in CF_NETS)


def _gb(n) -> float:
    return round((n or 0) / GB, 2)


def _f(level: str, code: str, title: str, detail: str, **data) -> dict:
    return {"level": level, "code": code, "title": title, "detail": detail, "data": data}


def hours_of(per_node: dict, names: dict, start: dt.datetime | None = None, end: dt.datetime | None = None) -> list[dict]:
    """The hour rows of the panel ({node id: [(hour start UTC, bytes, _)]}) as one list, oldest first, an hour nobody used as 0: [{at, local (Tehran), hour, bytes, node}] - `node` is the
    node that carried most of that hour."""
    acc: dict[dt.datetime, dict[str, int]] = {}

    for nid, rows in per_node.items():
        nm = "All nodes" if nid == -1 else names.get(nid) or f"node {nid}"

        for when, n, _raw in rows:
            acc.setdefault(when, {})
            acc[when][nm] = acc[when].get(nm, 0) + n

    if start is not None and end is not None:
        cur = start.replace(minute=0, second=0, microsecond=0)

        while cur <= end:
            acc.setdefault(cur, {})
            cur += dt.timedelta(hours=1)

    out = []

    for when in sorted(acc):
        by = acc[when]
        total = sum(by.values())
        loc = when + TEHRAN
        out.append({"at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "local": loc.strftime("%Y-%m-%d %H:%M"), "hour": loc.hour, "bytes": total, "node": max(by, key=by.get) if total else ""})

    return out


def busiest(hours: list[dict], width: int = BURST_HOURS) -> dict | None:
    """The `width` hours in a row that carried the most: {bytes, share, start, end, node}; None when nothing was used."""
    total = sum(h["bytes"] for h in hours)

    if not total:
        return None

    best, at = -1, 0

    for i in range(max(1, len(hours) - width + 1)):
        s = sum(h["bytes"] for h in hours[i:i + width])

        if s > best:
            best, at = s, i

    win = hours[at:at + width]
    by: dict[str, int] = {}

    for h in win:
        if h["node"]:
            by[h["node"]] = by.get(h["node"], 0) + h["bytes"]

    return {"bytes": best, "share": round(best / total, 3), "start": win[0]["local"], "end": win[-1]["local"], "node": max(by, key=by.get) if by else "", "first": at, "count": len(win)}


def night_share(hours: list[dict]) -> float:
    """The part of the usage that fell between 01:00 and 06:00 Tehran time."""
    total = sum(h["bytes"] for h in hours)
    return round(sum(h["bytes"] for h in hours if h["hour"] in NIGHT_HOURS) / total, 3) if total else 0.0


def app_of(agent: str) -> str:
    """The app in a user agent: 'v2rayNG/1.8' -> 'v2rayNG'."""
    return str(agent or "").strip().split("/")[0].split(" ")[0][:40] or "unknown"


def _ours() -> dict[str, str]:
    """{address: name} of the servers of this panel: behind a tunnel the node sees the Iran entrance server's address, not the customer's."""
    try:
        from sqlalchemy import select

        from ..db import session
        from ..models import Server

        with session() as db:
            return {s.host: s.name for s in db.scalars(select(Server))}
    except Exception:                                        # noqa: BLE001 - the report works without the names of our servers
        return {}


def judge(f: dict) -> list[dict]:
    """The findings about one user, worst first: {level ok/info/warn/bad, code, title, detail, data}. `f` = {user: {status, used, limit, hwid_limit, age_days}, hours, days [{t, bytes}], devices
    [{last}], fetches [{agent, ip}], by_node_30d [{name, bytes, share}], now}."""
    u, hours, days = f.get("user") or {}, f.get("hours") or [], f.get("days") or []
    devs, fetches, now = f.get("devices") or [], f.get("fetches") or [], f.get("now")
    out: list[dict] = []
    used, limit = u.get("used") or 0, u.get("limit") or 0

    # devices
    cap = u.get("hwid_limit") or DEFAULT_DEVICES

    if len(devs) > cap:
        out.append(_f("warn", "devices_over", f"{len(devs)} devices are registered (limit {cap})", "The link is used on more devices than allowed: it is probably shared, and everybody's traffic is taken from this one data limit.", count=len(devs), limit=cap))

    recent = [d for d in devs if now and (pg_usage._moment(d.get("last")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= now - dt.timedelta(days=2)]

    if len(recent) >= 2:
        out.append(_f("info", "devices_active", f"{len(recent)} devices were active in the last 2 days", "Each device uses data on its own: check that they are all the customer's.", count=len(recent)))

    # subscription fetches
    ips = {x.get("ip") for x in fetches if x.get("ip") not in LOCAL_IPS and not is_cf(x.get("ip"))}
    apps = {app_of(x.get("agent")) for x in fetches if x.get("agent")}

    if len(ips) >= MANY:
        out.append(_f("warn", "fetch_ips", f"The link was fetched from {len(ips)} different addresses", "One customer usually fetches from one or two networks (home, mobile). Several addresses suggest the link is in other hands.", count=len(ips)))

    if len(apps) >= MANY:
        out.append(_f("warn", "fetch_apps", f"{len(apps)} different apps fetched the link", "More than a couple of different apps fetching the same link is a sign it is shared: " + ", ".join(sorted(apps)[:6]) + ".", count=len(apps)))

    if fetches and not ips:
        out.append(_f("info", "fetch_hidden", "The address of the fetches is not the customer's", "The fetches are recorded with the proxy's own address (127.0.0.1) or one of Cloudflare's: the customer's address and country cannot be told from them."))

    # connected right now
    live = f.get("online") or []
    others = {x["ip"] for x in live if not x.get("ours") and not is_cf(x["ip"])}
    entry = sorted({x["ours"] for x in live if x.get("ours")})

    if len(others) >= MANY:
        out.append(_f("warn", "live_ips", f"Connected from {len(others)} different addresses right now", "One customer is rarely on three networks at the same moment: the link is probably shared.", count=len(others)))

    if entry:
        out.append(_f("info", "live_tunnel", "Connected right now through our server " + ", ".join(entry), "That address is our entrance server's, not the customer's: the customer's own address is not visible on a tunnel config.", servers=entry))

    # usage pattern
    total48 = sum(h["bytes"] for h in hours)
    night = night_share(hours)

    if total48 >= NIGHT_MIN and night >= NIGHT_SHARE:
        out.append(_f("warn", "night", f"{round(night * 100)} % of the last 2 days' usage was at night (01:00-06:00)", "Usage while the customer sleeps: an app updating or downloading in the background, a computer left on, or somebody else using the link.", share=night, gb=_gb(total48)))

    b = busiest(hours)

    if b and b["bytes"] >= BURST_MIN and b["share"] >= BURST_SHARE:
        out.append(_f("info", "burst", f"{_gb(b['bytes'])} GB in {b['count']} hours ({b['start'][5:]} to {b['end'][11:]}) through {b['node'] or 'one node'}", "One burst is the mark of a big download, a 4K video, a game or app update, or a speed test.", gb=_gb(b["bytes"]), start=b["start"], end=b["end"], node=b["node"], hours=b["count"]))

    # today against the usual day
    if len(days) >= 4:
        today, prev = days[-1]["bytes"], [d["bytes"] for d in days[:-1][-14:] if d["bytes"] > 0]

        if len(prev) >= 3:
            med = statistics.median(prev)

            if today >= SPIKE_MIN and med > 0 and today >= SPIKE_TIMES * med:
                out.append(_f("warn", "spike", f"Today {_gb(today)} GB is {round(today / med, 1)} times the usual day ({_gb(med)} GB)", "A sudden jump: look at the hours chart for when it happened.", gb=_gb(today), usual=_gb(med), times=round(today / med, 1)))

    # the pace of the account
    age = u.get("age_days") or 0

    if limit and used and age:
        pct = round(used * 100 / limit)
        avg = used / max(age, 1 / 24)

        if pct >= 80 or u.get("status") == "limited":
            out.append(_f("info", "burn", f"{pct} % used in {round(age, 1)} days ({_gb(avg)} GB a day on average)", "The data limit is nearly or fully used up: compare the daily average with what the customer expected.", pct=pct, days=round(age, 1), per_day=_gb(avg)))

        last3 = sum(d["bytes"] for d in days[-3:]) / 3 if days else 0

        if used < limit and last3 > 0:
            out.append(_f("info", "left", f"At the last 3 days' pace the rest ({_gb(limit - used)} GB) lasts {round((limit - used) / last3, 1)} days", "", left_gb=_gb(limit - used), days=round((limit - used) / last3, 1)))

    if u.get("status") == "limited":
        out.append(_f("info", "limited", "The data limit is reached (status limited)", "The panel stopped the user because the data ran out."))
    elif u.get("status") == "expired":
        out.append(_f("info", "expired", "The account is expired", "The panel stopped the user because the end date passed."))

    # which server carried it
    by30 = f.get("by_node_30d") or []

    if by30 and by30[0].get("bytes", 0) >= NODE_MIN and by30[0].get("share", 0) >= NODE_SHARE:
        out.append(_f("info", "node", f"{round(by30[0]['share'] * 100)} % of the last 30 days went through {by30[0]['name']}", "That is the location of the config the customer mostly uses.", node=by30[0]["name"], share=by30[0]["share"]))

    if not any(x["level"] in ("warn", "bad") for x in out):
        out.insert(0, _f("ok", "ok", "Nothing unusual found", "The usage matches the age of the account and its devices."))

    out.sort(key=lambda x: -LEVELS[x["level"]])
    return out


def verdict(findings: list[dict]) -> dict:
    top = findings[0]["level"] if findings else "ok"

    if top in ("warn", "bad"):
        titles = [x["title"] for x in findings if x["level"] == top]
        return {"level": top, "title": " · ".join(titles[:2]) + (f" · and {len(titles) - 2} more" if len(titles) > 2 else "")}

    return {"level": "ok", "title": "Nothing unusual found"}


def customer_text(user: dict, findings: list[dict]) -> str:
    """A short message the owner can paste to the customer (Persian: it is for the customers, not a panel label). It names the facts, never the addresses of other people."""
    used, limit = user.get("used") or 0, user.get("limit") or 0
    lines = [f"گزارش مصرف اشتراک {user.get('username', '')}"]
    lines.append(f"حجم مصرف‌شده: {_gb(used)} گیگ از {_gb(limit)} گیگ ({round(used * 100 / limit)}٪)" if limit else f"حجم مصرف‌شده: {_gb(used)} گیگ (اشتراک نامحدود)")
    by = {x["code"]: x["data"] for x in findings}

    if "burst" in by:
        d = by["burst"]
        lines.append(f"بیشترین مصرف در {d['hours']} ساعت پشت‌سرهم ({d['start'][11:]} تا {d['end'][11:]} به وقت ایران) حدود {d['gb']} گیگ بوده؛ معمولاً مربوط به دانلود حجیم، ویدیوی با کیفیت بالا یا آپدیت برنامه‌هاست.")

    if "night" in by:
        lines.append(f"حدود {round(by['night']['share'] * 100)}٪ مصرف دو روز اخیر بین ساعت ۱ تا ۶ بامداد بوده؛ آپدیت یا دانلود خودکار برنامه‌ها و سیستم روشن را بررسی کنید.")

    if "spike" in by:
        d = by["spike"]
        lines.append(f"مصرف امروز {d['gb']} گیگ است؛ حدود {d['times']} برابر یک روز معمولی ({d['usual']} گیگ).")

    if "devices_over" in by:
        d = by["devices_over"]
        lines.append(f"{d['count']} دستگاه با این اشتراک وصل شده‌اند (سقف {d['limit']}). مصرف همهٔ آن‌ها از همین حجم کم می‌شود.")

    if "fetch_ips" in by or "fetch_apps" in by:
        lines.append("لینک اشتراک از چند شبکه یا برنامهٔ مختلف دریافت شده است؛ اگر لینک را به کسی داده‌اید، پیشنهاد می‌شود لینک جدید بگیرید.")

    if "live_ips" in by:
        lines.append(f"همین الان از {by['live_ips']['count']} آی‌پی مختلف به این اشتراک وصل شده‌اند؛ اگر لینک را به کسی داده‌اید، پیشنهاد می‌شود لینک جدید بگیرید.")

    if "node" in by:
        lines.append(f"بیشتر ترافیک ({round(by['node']['share'] * 100)}٪) از سرور {by['node']['node']} رد شده است.")

    if "left" in by:
        lines.append(f"با روند ۳ روز اخیر، باقی‌ماندهٔ حجم ({by['left']['left_gb']} گیگ) حدود {by['left']['days']} روز کافی است.")

    if len(lines) == 2:
        lines.append("مورد غیرعادی‌ای در مصرف دیده نشد؛ مصرف با سن حساب و تعداد دستگاه‌ها هم‌خوانی دارد.")

    return "\n".join(lines)


# ------------------------------------------------------------------ the report
def investigate(c: dict, username: str, now: dt.datetime | None = None) -> dict:
    """Everything about one user for the Investigate window. Reads only; a part the panel cannot answer is left out and named in `notes`."""
    now = pg_usage._utc(now)
    uid = pg_users._id_of(c, username)
    raw = pg_users._dict(pg_users._call(c, "GET", f"/api/user/by-id/{uid}"))

    if raw.get("id") is None:
        raise ValueError("this user does not exist (any more)")

    u = pg_users.user_row(raw)
    notes: list[str] = []
    created = pg_usage._moment(u.get("created_at"))
    age_days = round(max(0.0, (now - created).total_seconds() / 86400), 2) if created else 0.0

    today = pg_usage.tehran_date(now)
    hours: list[dict] = []
    by48: list[dict] = []

    try:
        span = pg_usage.resolve_range("custom", (today - dt.timedelta(days=1)).isoformat(), today.isoformat(), now)
        usage = pg_usage._get(c, f"/api/user/by-id/{uid}/usage?" + pg_usage._query(span, "pasarguard"), 60)
        per = pg_usage.parse_pg_stats(usage)
        names = pg_usage.node_names(c)
        hours = hours_of(per, names, span.start, span.end)
        by48 = pg_usage.by_node_rows({nid: sum(n for _w, n, _r in rows) for nid, rows in per.items()}, names)
    except (PanelError, ValueError) as e:
        notes.append("The usage by hour could not be read: " + str(e)[:120])

    days: list[dict] = []
    by30: list[dict] = []

    try:
        d30 = pg_usage.user_usage(c, username, "last30", now=now)
        days = [{"t": p["t"], "bytes": p["bytes"]} for p in d30.get("series") or []]
        by30 = d30.get("by_node") or []
    except (PanelError, ValueError) as e:
        notes.append("The usage by day could not be read: " + str(e)[:120])

    devs = fetch = online = []

    try:
        devs = pg_users.devices(c, username)["devices"]
    except (PanelError, ValueError) as e:
        notes.append("The devices could not be read: " + str(e)[:120])

    try:
        fr = pg_users.sub_updates(c, username, 0, FETCHES)
        fetch, fetch_total = fr["updates"], fr["count"]
    except (PanelError, ValueError) as e:
        fetch, fetch_total = [], 0
        notes.append("The subscription fetches could not be read: " + str(e)[:120])

    try:
        online = pg_users.online_ips(c, username)["online"]
        ours = _ours()

        for row in online:
            row["ours"] = ours.get(row["ip"], "")
            row["cf"] = is_cf(row["ip"])
    except (PanelError, ValueError):
        online = []

    used, limit = u.get("used") or 0, u.get("limit") or 0
    user = {"username": u["username"], "status": u["status"], "admin": u["admin"], "created_at": u["created_at"], "online_at": u["online_at"], "used": used, "limit": limit,
            "pct": round(used * 100 / limit) if limit else None, "left": max(0, limit - used) if limit else None, "expire_day": u["expire_day"], "hwid_limit": u["hwid_limit"],
            "reset": u["reset"], "age_days": age_days, "per_day": round(used / max(age_days, 1 / 24) / GB, 2) if used else 0.0}
    findings = judge({"user": user, "hours": hours, "days": days, "devices": devs, "fetches": fetch, "by_node_30d": by30, "online": online, "now": now})
    for x in fetch:
        x["local"] = x.get("ip") in LOCAL_IPS
        x["cf"] = is_cf(x.get("ip"))

    ips = {x["ip"] for x in fetch if not x["local"] and not x["cf"]}
    b = busiest(hours)

    return {"user": user, "usage": {"hours": [{k: h[k] for k in ("local", "hour", "bytes", "node")} for h in hours], "days": days, "by_node_48h": by48[:8], "by_node_30d": by30[:8],
                                   "total_48h": sum(h["bytes"] for h in hours), "busiest": b, "night_share": night_share(hours)},
            "devices": {"limit": u["hwid_limit"] or DEFAULT_DEVICES, "own_limit": u["hwid_limit"], "count": len(devs), "rows": devs},
            "fetches": {"rows": fetch[:SHOWN_FETCHES], "read": len(fetch), "count": fetch_total, "addresses": len(ips), "apps": len({app_of(x.get("agent")) for x in fetch if x.get("agent")}), "hidden": bool(fetch) and not ips},
            "online": online, "findings": findings, "verdict": verdict(findings), "text": customer_text(user, findings), "notes": notes, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limits": ["The panel keeps no history of connection addresses: only the ones seen in the last minutes are listed.",
                       "On a tunnel config the node sees the entrance server's address, not the customer's. A direct config and a subscription fetch can show the real one."]}
