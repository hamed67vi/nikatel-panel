"""The usage of the CONFIGS (hosts and inbounds) of a PasarGuard panel: which are little used, which are falling. (The owner, 2026-10-21: "is a section in the panel that shows the usage of the configs possible?" - "only for
PasarGuard" - "add the apps' statistics too". He wants to know which configs are little used or falling: not nodes, configs.)

PasarGuard records usage per USER and per NODE only, so the panel measures it itself (read only for the panel):
  * `cfgmap.build` puts every host of the panel on a counter: a port of one of our servers (services/cfgmeter.py: an nft table that only counts, switched on per server by the owner), or a domain behind a CDN
    (services/cfgcdn.py: the CDN's own report of that domain, read once an hour from the DNS accounts), or nothing, with the reason;
  * this module reads the hours back (`cfgmeter.stats`), adds them into 24 h / 7 d / 30 d with the same-length window before each, and judges.
The judgement (constants below, shown on the page): a host is LITTLE USED when it is measured on its own counter (not shared), enabled, has at least two days on record, and a day of its last week carries less than a tenth
of what an average measured host carries; it is FALLING when the last seven complete days carry at least 30 % less than the seven before (needs 16 days on record; with 8 days the last three are compared with the three
before), and the earlier days carried something worth speaking of. No history before the collection began: until then the row says how many days it still needs, it does not guess.
Limits that stay: two hosts on one port cannot be separated; the bytes are those of IP packets (about 3 % over what the users moved); behind a CDN the number is the CDN's."""
from __future__ import annotations

import threading
import time

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Panel, Server, Tunnel, is_standby, is_temp
from . import cfgcdn, cfgmap, cfgmeter, dnscache, dnsprov, panels, pg_cores, pg_nodes

log = get_logger("cfgstats")
PLAN_TTL = 120                          # seconds the map of a panel is kept (3 questions to the panel)
LITTLE_FACTOR = 0.10                    # a host is "little used" under this share of the average measured host
MIN_MEASURED = 3                        # ... and only when at least this many hosts are measured (an average of two says nothing)
LITTLE_MIN_DAYS = 2                     # ... with at least this many days on record
FALL_DROP = 0.30                        # "falling": this much less than the same number of days before
FALL_MIN_DAY_BYTES = 150 * 1024 ** 2    # ... and the days before carried at least this much per day
TREND_NEED = {"24h": 2.0, "7d": 14.0, "30d": 60.0}       # days on record a trend of each window needs (the window and the one before it)
FLAT = 0.15                             # a change under this is "flat"
DAY = cfgmeter.DAY
_LOCK = threading.Lock()
_PLANS: dict[int, tuple[float, dict]] = {}
_IDLE = [0.0]                           # when the job last looked while no meter was on


# ------------------------------------------------------------------ the map of a panel
def _servers() -> list[dict]:
    with session() as db:
        return [{"id": s.id, "name": s.name, "host": s.host, "role": s.role, "enabled": bool(s.enabled), "online": s.online} for s in db.scalars(select(Server).order_by(Server.id))]


def _tunnels() -> list[dict]:
    with session() as db:
        return [{"name": t.name, "iran_server_id": t.iran_server_id, "forwarded_ports": list(t.forwarded_ports or [])} for t in db.scalars(select(Tunnel)) if not is_temp(t.name) and not is_standby(t)]


def _names(hosts: list[dict], nodes: list[dict], servers: list[dict]) -> list[str]:
    out: set[str] = set()

    for a in [x for h in hosts for x in cfgmap._strs(h.get("address"))] + [n.get("address") for n in nodes] + [s.get("host") for s in servers]:
        n = pg_nodes.norm(a)

        if n and "{" not in n and not pg_nodes.ip_of(n):
            out.add(n)

    return sorted(out)[:150]


def _dns_rows(block: bool) -> list[dict]:
    try:
        if dnsprov.accounts():
            age = time.time() - float(dnscache.CACHE.get("at") or 0)

            if not dnscache.CACHE.get("rows") and block:
                dnscache.read_all()
            elif age > 900:
                dnscache.refresh_in_background()
    except Exception as e:                                       # noqa: BLE001 - without DNS rows a CDN host is only "unknown"
        log.info("cfgstats: the DNS accounts could not be read: %s", str(e)[:100])

    return list(dnscache.CACHE.get("rows") or [])


def read_plan(c: dict, block: bool = False) -> dict:
    """Ask the panel for its hosts, cores and nodes (read only) and build the map (cfgmap.build)."""
    hosts = pg_cores._as_list(panels.call(c, "GET", "/api/hosts"), "hosts")
    cores = []

    for x in pg_cores._as_list(panels.call(c, "GET", "/api/cores"), "cores"):
        try:
            cores.append(pg_cores._norm_core(x))
        except (KeyError, TypeError, ValueError):
            continue

    nodes = [{"id": n.get("id"), "name": n.get("name"), "address": n.get("address"), "status": n.get("status"), "core_config_id": n.get("core_config_id")} for n in pg_nodes._nodes_raw(c)]
    servers = _servers()
    names = _names(hosts, nodes, servers)
    resolved = pg_nodes.resolve_names(names, timeout=8.0) if names else {}
    return cfgmap.build(hosts, cores, nodes, servers, _dns_rows(block), resolved, _tunnels())


def plan_for(c: dict, force: bool = False, block: bool = False) -> dict:
    """The map of a panel, kept PLAN_TTL seconds (several open tabs share one reading)."""
    pid = int(c["id"])

    with _LOCK:
        hit = _PLANS.get(pid)

        if hit and not force and time.time() - hit[0] < PLAN_TTL:
            return hit[1]

    plan = read_plan(c, block)

    with _LOCK:
        _PLANS[pid] = (time.time(), plan)

    return plan


def forget() -> None:
    with _LOCK:
        _PLANS.clear()
        _IDLE[0] = 0.0


# ------------------------------------------------------------------ pure: numbers into judgements
def combine(items: list[dict]) -> dict | None:
    """Several counters that make one row (a host on every node that carries its inbound): bytes and peaks add up, the record starts at the earliest."""
    if not items:
        return None

    if len(items) == 1:
        return items[0]

    def add(key: str) -> dict:
        return {w: sum(i[key][w] for i in items) for w in ("24h", "7d", "30d")}

    return {"first": min(i["first"] for i in items), "last": max(i["last"] for i in items), "bytes": add("bytes"), "prev": add("prev"), "down": add("down"), "up": add("up"),
            "peak": add("peak"), "samples": add("samples"), "days": [sum(i["days"][d] for i in items) for d in range(30)]}


def pct_change(cur: int, prev: int) -> float | None:
    return None if prev <= 0 else (cur - prev) / prev


def arrow_of(p: float | None) -> str:
    """up / down / flat, "" when there is nothing to compare."""
    if p is None:
        return ""

    return "up" if p >= FLAT else "down" if p <= -FLAT else "flat"


def trends(m: dict, cov: float) -> dict:
    """For each window: the change against the window before it, or why there is none yet ("needs": the days still missing)."""
    out = {}

    for w, need in TREND_NEED.items():
        if cov >= need:
            p = pct_change(m["bytes"][w], m["prev"][w])
            out[w] = {"pct": None if p is None else round(p * 100, 1), "arrow": arrow_of(p), "needs": 0.0}
        else:
            out[w] = {"pct": None, "arrow": "", "needs": round(need - cov, 1)}

    return out


def falling(days: list[int], cov: float) -> dict | None:
    """The fall of the last complete days against the same number before them: {"window": 7 | 3, "cur", "prev", "pct", "falling"}; None when there is not enough record. `days` is the 30 Tehran days
    (the last one is today so far and is left out)."""
    w = 7 if cov >= 16 else 3 if cov >= 8 else 0

    if not w:
        return None

    cur, prev = sum(days[29 - w:29]), sum(days[29 - 2 * w:29 - w])
    pct = pct_change(cur, prev)
    return {"window": w, "cur": cur, "prev": prev, "pct": None if pct is None else round(pct * 100, 1), "falling": prev >= w * FALL_MIN_DAY_BYTES and pct is not None and pct <= -FALL_DROP}


def little_threshold(rows: list[dict]) -> float | None:
    """Bytes per day under which a measured host is "little used" (a tenth of the average one), None when too few hosts are measured. `rows` need `daily`."""
    vals = [r["daily"] for r in rows]

    if len(vals) < MIN_MEASURED:
        return None

    return LITTLE_FACTOR * (sum(vals) / len(vals))


# ------------------------------------------------------------------ the page's answer
def _view(m: dict | None, now: int) -> dict:
    """The numbers of a counter as the page wants them."""
    if m is None:
        return {"has": False}

    cov = cfgmeter.coverage_days(m["first"], now)
    n = {w: m["samples"][w] for w in m["samples"]}
    return {"has": True, "cov": round(cov, 1), "first": m["first"], "last": m["last"], "bytes": m["bytes"], "down": m["down"], "up": m["up"], "prev": m["prev"],
            "peak": {w: (m["peak"][w] if n[w] else None) for w in n}, "days": m["days"], "trend": trends(m, cov), "fall": falling(m["days"], cov)}


def snapshot(panel_id: int, c: dict | None = None, force: bool = False, now: int | None = None) -> dict:
    """Everything the Configs section shows for a PasarGuard panel: the hosts and inbounds with their numbers, the servers and their meters, the CDN accounts."""
    c = c or panels.cred_for(panel_id)

    if c.get("type") != "pasarguard":
        raise ValueError("the config statistics are a part of PasarGuard panels")

    now = int(now or time.time())
    plan = plan_for(c, force)
    meters = cfgmeter.all_meters()
    cdn_state = cfgcdn.state()
    srcs = {s for h in plan["hosts"] for s in h["srcs"]} | {h["origin_src"] for h in plan["hosts"] if h["origin_src"]} | {p["src"] for ib in plan["inbounds"] for p in ib["ports"]}
    st = cfgmeter.stats(srcs, now)
    sv = {s["id"]: s for s in _servers()}

    def meter_of(sid: int | None, port: int | None) -> str:
        m = meters.get(sid or 0)

        if not m or not m["enabled"]:
            return "off"

        return "on" if port in (m["ports"] or []) else "pending"

    rows = []

    for h in plan["hosts"]:
        r = {k: h[k] for k in ("id", "remark", "tag", "protocol", "network", "security", "address", "more_addresses", "port", "disabled", "rare", "server_id", "server", "tunnel", "why", "shared_with")}
        m = combine([st[s] for s in h["srcs"] if s in st]) if h["kind"] != "unknown" else None
        source, text, state, meter, short = "none", h["why"], "none", "n/a", "—"

        if h["kind"] == "direct" and h["srcs"]:
            ms = [meter_of(int(s.split(":")[1]), int(s.split(":")[2])) for s in h["srcs"]]
            meter = "on" if all(x == "on" for x in ms) else "off" if all(x == "off" for x in ms) else "pending"
            port = int(h["srcs"][0].split(":")[2])
            source = "port"
            text = f"port {port} on {h['server'] or 'a server of ours'}" + (f" (tunnel {h['tunnel']})" if h["tunnel"] else "") if len(h["srcs"]) == 1 else f"port of the inbound on {len(h['srcs'])} nodes"
            short = f"{h['server']} :{port}" if len(h["srcs"]) == 1 else f"{len(h['srcs'])} nodes"
            state = "ok" if m else ("wait" if meter in ("on", "pending") else "off")
        elif h["kind"] == "cdn":
            cd = h["cdn"]
            acc = cdn_state.get(cd["account"], {})
            origin = st.get(h["origin_src"]) if h["origin_src"] else None
            source, short = "cdn", "CDN"
            text = f"the CDN report of {cd['fqdn']}"

            if m is not None:
                state = "ok"
                r["shared_with"] = []                                   # its own number from the CDN: not shared with the hosts that end on the same origin port
            elif origin is not None:
                m, source, state = origin, "origin", "ok"
                meter = meter_of(cd["origin_server_id"], cd["origin_port"])
                short = f"origin :{cd['origin_port']}"
                text = f"origin port {cd['origin_port']} on {cd['origin_server']}: every host behind the CDN of this inbound shares it" + (f" ({acc['text']})" if acc.get("filter") is False and acc.get("text") else "")
            elif h["origin_src"]:
                meter = meter_of(cd["origin_server_id"], cd["origin_port"])
                source, state, short = "origin", "wait" if meter in ("on", "pending") else "off", f"origin :{cd['origin_port']}"
                text = f"the CDN report of {cd['fqdn']} has not been read yet; the origin port {cd['origin_port']} on {cd['origin_server']} is not counted yet"
            else:
                state = "wait" if cfgcdn.enabled() else "off"
                text = h["why"] or f"the CDN report of {cd['fqdn']} has not been read yet"

        r.update({"source": source, "source_text": text, "short": short, "state": state, "meter": meter, "cdn": h["cdn"], "view": _view(m, now)})
        rows.append(r)

    # ---- who is little used / falling (only a host that has its own counter, is enabled and is not shared)
    judged = [r for r in rows if r["state"] == "ok" and r["source"] in ("port", "cdn") and not r["shared_with"] and not r["disabled"] and r["view"]["cov"] >= LITTLE_MIN_DAYS]

    for r in judged:
        v = r["view"]
        r["daily"] = v["bytes"]["7d"] / max(1.0, min(7.0, v["cov"]))

    thr = little_threshold(judged)
    is_judged = {id(r) for r in judged}

    for r in rows:
        badges = []
        v = r["view"]

        if id(r) in is_judged and thr is not None and r["daily"] < thr:
            badges.append("little")

        if id(r) in is_judged and v["fall"] and v["fall"]["falling"]:
            badges.append("falling")

        if (r["state"] == "ok" and r["source"] == "origin") or r["shared_with"]:
            badges.append("shared")

        if r["disabled"]:
            badges.append("disabled")

        if r["state"] in ("off", "wait", "none"):
            badges.append("nodata")

        r["badges"] = badges
        r.pop("daily", None)

    inbounds = []

    for ib in plan["inbounds"]:
        if not ib["ports"] and not ib["used"]:
            continue

        m = combine([st[p["src"]] for p in ib["ports"] if p["src"] in st])
        meter = "n/a" if not ib["ports"] else ("on" if all(meter_of(p["server_id"], p["port"]) == "on" for p in ib["ports"]) else "off")
        inbounds.append({"tag": ib["tag"], "protocol": ib["protocol"], "network": ib["network"], "security": ib["security"], "rare": ib["rare"], "hosts": len(ib["hosts"]),
                         "carried": ib["carried"], "ports": [{"server": p["server"], "port": p["port"]} for p in ib["ports"]], "meter": meter, "view": _view(m, now)})

    totals_src = sorted({p["src"] for ib in plan["inbounds"] for p in ib["ports"]})
    tot = combine([st[s] for s in totals_src if s in st])
    firsts = [m["first"] for m in st.values()]
    servers = []

    for sid, sp in plan["servers"].items():
        m = meters.get(sid) or {"enabled": False, "ports": [], "last_ok": 0, "last_error": "", "since": 0}
        s = sv.get(sid, {})
        servers.append({"id": sid, "name": sp["name"], "role": sp["role"], "enabled": bool(m["enabled"]), "plan_ports": len(sp["ports"]), "ports": len(m["ports"] or []), "since": m["since"], "last_ok": m["last_ok"],
                        "last_error": m["last_error"], "stale": bool(m["enabled"] and m["last_ok"] and now - m["last_ok"] > 1200), "reachable": s.get("online") is not False and s.get("enabled", True),
                        "differs": bool(m["enabled"] and sorted(m["ports"] or []) != sp["ports"])})

    accounts = {a.id: a for a in _accounts()}
    cdn_groups: dict[str, dict] = {}

    for g in plan["cdn"]:
        a = accounts.get(g["account"])
        e = cdn_groups.setdefault(g["account"], {"id": g["account"], "name": a.name if a else g["account"], "provider": a.provider if a else "", "domains": set(), "hosts": 0, "sample": g["fqdn"],
                                                 "state": cdn_state.get(g["account"], {})})
        e["domains"].add(g["domain"])
        e["hosts"] += len(g["hosts"])

    cdn = [{**e, "domains": sorted(e["domains"])} for e in cdn_groups.values()]
    measured = [r for r in rows if r["state"] == "ok"]
    summary = {"hosts": len(rows), "measured": len(measured), "little": sum("little" in r["badges"] for r in rows), "falling": sum("falling" in r["badges"] for r in rows),
               "nodata": sum("nodata" in r["badges"] for r in rows), "shared": sum("shared" in r["badges"] for r in rows), "disabled": sum(r["disabled"] for r in rows)}
    return {"panel_id": panel_id, "now": now, "hosts": rows, "inbounds": inbounds, "servers": servers, "cdn": cdn, "cdn_on": cfgcdn.enabled(), "summary": summary,
            "total": _view(tot, now) if tot else {"has": False}, "since": min(firsts) if firsts else 0, "cov": round(cfgmeter.coverage_days(min(firsts), now), 1) if firsts else 0.0,
            "rules": {"little_factor": LITTLE_FACTOR, "little_threshold_bytes_per_day": None if thr is None else int(thr), "min_measured": MIN_MEASURED, "fall_drop": FALL_DROP,
                      "fall_min_day_bytes": FALL_MIN_DAY_BYTES, "trend_need_days": TREND_NEED},
            "read_only": _read_only()}


def _accounts():
    try:
        return dnsprov.accounts()
    except Exception:                                            # noqa: BLE001 - the page works without the account names
        return []


def _read_only() -> bool:
    from ..config import get_settings

    return bool(get_settings().read_only)


# ------------------------------------------------------------------ switching the meters
def meter_on(panel_id: int, server_id: int) -> dict:
    """Put the counting table on a server for the ports the map of this panel wants counted there."""
    c = panels.cred_for(panel_id)
    plan = plan_for(c, force=True)
    sp = plan["servers"].get(int(server_id))

    if not sp or not sp["ports"]:
        raise ValueError("no host or inbound of this panel needs a counter on that server")

    out = cfgmeter.apply(int(server_id), sp["ports"])
    forget()
    return out


def meter_off(server_id: int) -> dict:
    out = cfgmeter.remove(int(server_id))
    forget()
    return out


def meters_all(panel_id: int, on: bool) -> dict:
    """Every server of the map at once (or every server whose meter is on). One that fails does not stop the others: {server name: "ok" | the reason}."""
    c = panels.cred_for(panel_id)
    plan = plan_for(c, force=True)
    res: dict[str, str] = {}
    ids = list(plan["servers"]) if on else [sid for sid, m in cfgmeter.all_meters().items() if m["enabled"]]

    for sid in ids:
        name = plan["servers"].get(sid, {}).get("name") or str(sid)

        try:
            if on:
                cfgmeter.apply(sid, plan["servers"][sid]["ports"])
            else:
                cfgmeter.remove(sid)

            res[name] = "ok"
        except (RuntimeError, ValueError) as e:
            res[name] = str(e)[:200]

    forget()
    return res


# ------------------------------------------------------------------ the job
def run() -> None:
    """Every five minutes: for each PasarGuard panel read the map, give every server whose meter is on its turn (read, or load again), and read the CDN reports when they are due. Does nothing while no meter is
    on and no CDN domain is used."""
    now = int(time.time())

    with session() as db:
        pids = [p.id for p in db.scalars(select(Panel).where(Panel.type == "pasarguard", Panel.enabled.is_(True)))]

    if not pids:
        return

    meters = {sid: m for sid, m in cfgmeter.all_meters().items() if m["enabled"]}

    if not meters:                                               # no meter: only the CDN reports are due, once an hour - the panel is not asked every five minutes for nothing
        if not cfgcdn.enabled() or now - _IDLE[0] < cfgcdn.EVERY:
            return

        _IDLE[0] = now

    acc: dict = {}

    for pid in pids:
        try:
            c = panels.cred_for(pid)
            plan = plan_for(c, force=True, block=True)
        except (panels.PanelError, ValueError, RuntimeError) as e:
            log.info("cfgstats: panel %s: %s", pid, str(e)[:120])
            continue

        for sid in list(meters):
            if sid in plan["servers"]:
                cfgmeter.tick(sid, plan["servers"][sid]["ports"], now)
                meters.pop(sid, None)

        cfgcdn.collect(plan["cdn"], lambda src, hour, rx, tx: cfgmeter.add(acc, src, hour, rx, tx), now)

    for sid in meters:                                           # a meter on a server no panel needs any more: read it as it stands
        cfgmeter.tick(sid, None, now)

    cfgmeter.flush(acc)

    if now % 86400 < 300:
        cfgmeter.prune(now)
