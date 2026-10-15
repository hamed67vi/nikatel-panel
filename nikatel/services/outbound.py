"""The Outbound section: how the Iran servers reach the foreign nodes when Iran is cut off from the world.

    users -> Iran server (a forward port) -> one of the POOL's configs (shared out, a dead one skipped by itself) -> foreign node IP:port

The pool is a list of ordinary vless / vmess / trojan / ss links that still have internet (any provider, as many as the operator has).
The panel runs a plain Xray (services/gateway.py) on each chosen Iran server: every route is a port on that server that forwards to a node's
address and port, and the exit of every connection is one of the pool's configs (mode spread: the next alive one in turn; fast: the fastest;
auto: direct while it works, the pool when it does not). Which exits are alive is measured from the Iran servers themselves, every few minutes.
The exit links are secrets: they live in their own table and are never listed (only what they point at).

The operator chooses Iran servers, foreign servers and ports; for every such pair and port the panel makes a forward (a "tunnel through the configs") on the Iran server:
its IP and listen port are what the operator then puts behind the CDN by hand. A forward can also be made FROM AN EXISTING TUNNEL (same node and port); "connecting the
records" is then one change per ArvanCloud record: its origin port becomes the forward's port (the tunnel stays untouched, the change is kept in the DNS log and can be put
back). While AUTO_USERS is off, nothing points a record at the pool by itself: the operator previews and presses."""
from __future__ import annotations

import base64
import json
import random
import re
import secrets
import subprocess
import time
import uuid

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import (
    Gateway,
    OutboundConfig,
    OutboundHub,
    Panel,
    Server,
    ServerSample,
    Tunnel,
)
from ..ssh import Node, SSHError, run_action, run_script
from ..ssh import target as ssh_target
from . import (
    autopilot,
    builder,
    dnscache,
    dnsprov,
    failover,
    gateway,
    license,
    panels,
    telegram,
)
from . import settings as st
from .events import emit

log = get_logger("outbound")

MODES = ("spread", "fast", "auto")
USERS_MODES = ("manual", "auto")
AUTO_USERS = False             # the automatic switch of CDN records is OFF until its tests are finished (the owner's decision, 2026-10-14): the code stays, nothing runs, no mode can be set
MAX_PORTS = 20                 # ports the operator can name for the foreign servers
RELAY_DIR = "/opt/nikatel-xrelay"
RELAY_PORTS = (44000, 44999)
MAX_CONFIGS = 30
MAX_ROUTES = 600
PORT_BASE = 30000


# ------------------------------------------------------------------ pure
def norm(uri: str) -> str:
    """The link without its #name: the same exit pasted twice is one exit."""
    return str(uri or "").strip().split("#")[0]


def split_links(text: str) -> list[str]:
    """Pure: the links in a pasted text, one per line (blank lines and a '#' comment line are ignored)."""
    out = []

    for line in str(text or "").replace("\r", "").split("\n"):
        line = line.strip()

        if line and not line.startswith("#"):
            out.append(line)

    return out


def next_port(taken: set[int], start: int = PORT_BASE) -> int:
    """Pure: the first listen port at or above `start` that is not taken (the forwards keep their port for good, so a user's link never moves)."""
    p = start

    while p in taken or gateway.TEST_BASE - 2 <= p <= gateway.TEST_BASE + 60:
        p += 1

    if p > 65535:
        raise ValueError("no free forward port is left")

    return p


def alloc_port(s: dict, taken: set[int]) -> int:
    """The next listen port: above every port ever given (a forward that was removed never hands its port to another target, so a CDN record that still points at it never leads somewhere else
    by surprise); only when the top is reached the gaps are used."""
    mark = int(s.get("port_mark") or 0)

    try:
        p = next_port(taken, max(PORT_BASE, mark + 1))
    except ValueError:
        p = next_port(taken)

    s["port_mark"] = max(mark, p)
    return p


def ports_of(routes: list[dict], server_id: int = 0) -> list[dict]:
    """Pure: the forwards of one Iran server (the ones for every server and the ones made for it) in the shape the gateway wants."""
    routes = [r for r in routes if not server_id or r.get("server_id", 0) in (0, server_id)]
    return [{"port": r["listen"], "target_host": r["target_host"], "target_port": r["target_port"], "udp": False, "enabled": r.get("enabled", True) is not False,
             "note": str(r.get("name") or "")[:60]} for r in routes]


def summarize(results: list[dict], ids: list[str]) -> dict:
    """Pure: from a gateway test (rows with key / ok / ms / ip / country) what the pool looks like from that Iran server."""
    by = {r["key"]: r for r in results}
    exits = {i: {"ok": bool(by[i]["ok"]), "ms": by[i]["ms"], "ip": by[i].get("ip", ""), "country": by[i].get("country", "")} for i in ids if i in by}
    gw = by.get("gateway") or {}
    return {"alive": sum(1 for e in exits.values() if e["ok"]), "total": len(ids), "exits": exits, "gateway_ok": bool(gw.get("ok")), "ms": gw.get("ms")}


def verdict(sm: dict | None) -> str:
    """Pure: 'ok' (every exit answers), 'degraded' (some do), 'down' (the gateway as users see it does not), 'unknown' (not measured yet)."""
    if not sm:
        return "unknown"

    if not sm["gateway_ok"] or sm["alive"] == 0:
        return "down"

    return "ok" if sm["alive"] == sm["total"] else "degraded"


# ------------------------------------------------------------------ the state
def _state() -> dict:
    s = st.get("outbound")
    s.setdefault("routes", [])
    s.setdefault("gateways", [])
    s.setdefault("foreign", [])
    s.setdefault("ports", [])
    s.setdefault("port_mark", 0)
    s.setdefault("health", {})
    s.setdefault("users", {"mode": "manual", "after_minutes": 5, "back_minutes": 30, "auto": {}})
    s["users"].setdefault("auto", {})
    return s


def _save(s: dict) -> None:
    st.put("outbound", {"mode": s["mode"], "gateways": s["gateways"], "foreign": s["foreign"], "ports": s["ports"], "port_mark": s["port_mark"], "routes": s["routes"][:MAX_ROUTES], "health": s["health"], "users": s["users"]})


def _need_rw() -> None:
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to change the outbound")


def state() -> dict:
    """Everything the page shows. The exit links are never in it."""
    s = _state()

    with session() as db:
        rows = list(db.scalars(select(OutboundConfig).order_by(OutboundConfig.created_at, OutboundConfig.id)))
        gws = {g.server_id: g for g in db.scalars(select(Gateway))}
        iran = list(db.scalars(select(Server).where(Server.role == "iran").order_by(Server.name)))
        foreign = [{"id": x.id, "name": x.name, "host": x.host, "online": x.online, "chosen": x.id in s["foreign"]} for x in db.scalars(select(Server).where(Server.role == "foreign", Server.enabled.is_(True)).order_by(Server.name))]

    ids = [c.id for c in rows if c.enabled]
    servers = []

    for sv in iran:
        g = gws.get(sv.id)
        h = s["health"].get(str(sv.id)) or {}
        sm = h.get("summary")
        servers.append({"id": sv.id, "name": sv.name, "host": sv.host, "online": sv.online, "enabled": sv.enabled, "chosen": sv.id in s["gateways"], "installed": bool(g and g.installed),
                        "applied_at": g.applied_at if g else 0, "checked_at": h.get("at", 0), "summary": sm, "verdict": verdict(sm) if sv.id in s["gateways"] else "off"})

    configs = []

    for c in rows:
        per = {sid: (s["health"].get(sid, {}).get("summary") or {}).get("exits", {}).get(c.id) for sid in s["health"] if sid.isdigit() and int(sid) in s["gateways"]}
        seen = [x for x in per.values() if x]
        configs.append({"id": c.id, "note": c.note, "enabled": c.enabled, "info": gateway.describe(c.uri), "added": c.created_at // 1000,
                        "alive": sum(1 for x in seen if x["ok"]), "measured": len(seen), "ms": min((x["ms"] for x in seen if x["ok"] and x["ms"] is not None), default=None),
                        "exit": next(({"ip": x["ip"], "country": x["country"]} for x in seen if x["ok"]), None)})

    u = s["users"]
    return {"mode": s["mode"], "modes": list(MODES), "configs": configs, "servers": servers, "routes": s["routes"], "enabled_configs": len(ids), "read_only": get_settings().read_only, "hubs": hubs_state(s), "foreign": foreign, "ports": s["ports"], "paths": sum(1 for r in s["routes"] if r.get("source") == "path"), "default_ports": default_ports(), "tunnel_ports": sum(1 for tp in tunnel_ports() if tp["server_id"] in s["gateways"]), "suggested_relay": suggest_relay(),
            "users": {"mode": u["mode"], "after_minutes": u["after_minutes"], "back_minutes": u["back_minutes"], "licensed": license.has("failover"), "auto_available": AUTO_USERS, "auto": sorted(u["auto"])},
            "binary": (get_settings().data_dir.parent / "engines" / "xray").exists(), "limits": {"configs": MAX_CONFIGS, "routes": MAX_ROUTES}}


# ------------------------------------------------------------------ the pool
def add_configs(text: str) -> dict:
    """Add the links of a pasted text. Each is read like Xray reads it; a link that cannot be read, or is already in the pool, is skipped with the reason."""
    _need_rw()
    links = split_links(text)

    if not links:
        raise ValueError("paste at least one link (vless://, vmess://, trojan:// or ss://), one per line")

    added, skipped = [], []
    stamp = int(time.time() * 1000)             # milliseconds: the pool keeps the order in which links were pasted

    with session() as db:
        have = {norm(c.uri): c for c in db.scalars(select(OutboundConfig))}

        for ln in links:
            try:
                gateway.outbound_from_uri(ln, "x")
            except ValueError as e:
                skipped.append({"link": ln[:40] + ("…" if len(ln) > 40 else ""), "why": str(e)})
                continue

            if norm(ln) in have:
                skipped.append({"link": ln[:40] + ("…" if len(ln) > 40 else ""), "why": "already in the pool"})
                continue

            if len(have) >= MAX_CONFIGS:
                skipped.append({"link": ln[:40] + "…", "why": f"the pool holds at most {MAX_CONFIGS} configs"})
                continue

            note = re.sub(r"[^\w .\-]", "", urlnote(ln))[:60]
            c = OutboundConfig(id=uuid.uuid4().hex[:10], uri=ln, note=note or "", created_at=stamp + len(added))
            db.add(c)
            have[norm(ln)] = c
            added.append({"id": c.id, "info": gateway.describe(ln)})

        if added:
            emit(db, "info", "outbound", f"{len(added)} config(s) added to the outbound pool.", "outbound")

    return {"added": added, "skipped": skipped}


def urlnote(link: str) -> str:
    """Pure: the name after the # of a link, as far as it is plain text (an emoji-only name gives '')."""
    import urllib.parse

    return urllib.parse.unquote(link.split("#", 1)[1]) if "#" in link else ""


def set_config(config_id: str, enabled: bool | None = None, note: str | None = None) -> None:
    _need_rw()

    with session() as db:
        c = db.get(OutboundConfig, config_id)

        if c is None:
            raise ValueError("this config is not in the pool")

        if enabled is not None:
            c.enabled = bool(enabled)

        if note is not None:
            c.note = re.sub(r"[^\w .\-]", "", str(note))[:60]


def drop_config(config_id: str) -> None:
    _need_rw()

    with session() as db:
        c = db.get(OutboundConfig, config_id)

        if c is None:
            raise ValueError("this config is not in the pool")

        db.delete(c)
        emit(db, "info", "outbound", "A config was removed from the outbound pool.", "outbound")


def set_mode(mode: str) -> None:
    _need_rw()

    if mode not in MODES:
        raise ValueError("the mode is spread, fast or auto")

    s = _state()
    s["mode"] = mode
    _save(s)


def set_gateways(server_ids: list[int]) -> None:
    """The Iran servers that carry the outbound (each gets the whole pool and every forward)."""
    _need_rw()

    with session() as db:
        ok = {x.id for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True)))}

    bad = [i for i in server_ids if i not in ok]

    if bad:
        raise ValueError("choose enabled Iran servers")

    s = _state()
    s["gateways"] = sorted({int(i) for i in server_ids})
    _save(s)


def parse_ports(text) -> list[int]:
    """Pure: ports from "8075, 80 443" or a list; each 1-65535, once, in the order given (at most MAX_PORTS)."""
    raw = text if isinstance(text, list) else re.split(r"[\s,;]+", str(text or ""))
    out: list[int] = []

    for x in raw:
        if str(x).strip() == "":
            continue

        if not str(x).strip().isdigit() or not 1 <= int(x) <= 65535:
            raise ValueError(f"'{str(x).strip()[:12]}' is not a port (1-65535)")

        if int(x) not in out:
            out.append(int(x))

    if len(out) > MAX_PORTS:
        raise ValueError(f"at most {MAX_PORTS} ports")

    return out


def wanted_paths(iran: list[dict], foreign: list[dict], ports: list[int]) -> list[dict]:
    """Pure: the forward of every chosen Iran server to every chosen foreign server on every chosen port."""
    return [{"server_id": i["id"], "target_host": f["host"], "target_port": p, "name": f"{i['name']} → {f['name']} :{p}", "foreign_id": f["id"]} for i in iran for f in foreign for p in ports]


def default_ports() -> list[int]:
    """The ports the tunnels of the chosen Iran servers serve (what the users connect to today): the suggestion for the ports box."""
    s = _state()
    return sorted({tp["iran_port"] for tp in tunnel_ports() if tp["server_id"] in s["gateways"]})[:MAX_PORTS]


def set_targets(foreign_ids: list[int], ports) -> None:
    """The foreign servers and the ports the chosen Iran servers get a way to (Start makes the forwards)."""
    _need_rw()
    ports = parse_ports(ports)

    with session() as db:
        ok = {x.id for x in db.scalars(select(Server).where(Server.role == "foreign", Server.enabled.is_(True)))}

    if any(int(i) not in ok for i in foreign_ids):
        raise ValueError("choose enabled foreign servers")

    s = _state()
    s["foreign"] = sorted({int(i) for i in foreign_ids})
    s["ports"] = ports
    _save(s)


def make_paths() -> dict:
    """Make exactly the forwards the choice says - every chosen Iran server x every chosen foreign server x every chosen port - and drop the ones made this way that are not
    chosen any more. A forward keeps its listen port for good (what the operator put behind the CDN does not move)."""
    _need_rw()
    s = _state()

    with session() as db:
        iran = [{"id": x.id, "name": x.name} for x in db.scalars(select(Server).where(Server.id.in_(s["gateways"] or [0])).order_by(Server.name))]
        foreign = [{"id": x.id, "name": x.name, "host": x.host} for x in db.scalars(select(Server).where(Server.id.in_(s["foreign"] or [0])).order_by(Server.name))]

    want = wanted_paths(iran, foreign, s["ports"])

    if len(want) > MAX_ROUTES:
        raise ValueError(f"{len(want)} forwards are too many (at most {MAX_ROUTES}): choose fewer servers or ports")

    keys = {(w["server_id"], w["target_host"].lower(), w["target_port"]) for w in want}
    kept = [r for r in s["routes"] if r.get("source") != "path" or (r.get("server_id", 0), r["target_host"].lower(), r["target_port"]) in keys]
    removed = len(s["routes"]) - len(kept)
    s["routes"] = kept
    _save(s)
    made = add_routes(want, "path") if want else {"added": 0, "skipped": 0}
    return {"added": made["added"], "kept": made["skipped"], "removed": removed}


# ------------------------------------------------------------------ the forwards
def add_routes(items: list[dict], source: str = "manual") -> dict:
    """Add forwards: each {target_host, target_port, name}. The listen port is given once and kept; the same target twice is one forward."""
    _need_rw()
    s = _state()
    taken = {r["listen"] for r in s["routes"]}
    have = {(r.get("server_id", 0), r["target_host"].lower(), r["target_port"]) for r in s["routes"]}
    added, skipped = 0, 0

    for it in items:
        host, port = str(it.get("target_host", "")).strip(), int(it.get("target_port", 0) or 0)

        if not re.match(r"^[A-Za-z0-9.\-]{1,120}$", host) or not 1 <= port <= 65535:
            raise ValueError("a forward needs a node address (an IP or a host name) and a port 1-65535")

        sid = int(it.get("server_id") or 0)

        if (sid, host.lower(), port) in have:
            skipped += 1
            continue

        if len(s["routes"]) >= MAX_ROUTES:
            raise ValueError(f"at most {MAX_ROUTES} forwards")

        listen = alloc_port(s, taken)
        taken.add(listen)
        have.add((sid, host.lower(), port))
        s["routes"].append({"id": uuid.uuid4().hex[:8], "name": str(it.get("name") or f"{host}:{port}")[:60], "target_host": host, "target_port": port, "listen": listen, "enabled": True, "source": source,
                            "server_id": sid, **({"tunnel": it["tunnel"], "iran_port": int(it["iran_port"])} if it.get("tunnel") else {})})
        added += 1

    _save(s)
    return {"added": added, "skipped": skipped}


def set_route(route_id: str, enabled: bool) -> None:
    _need_rw()
    s = _state()
    r = next((x for x in s["routes"] if x["id"] == route_id), None)

    if r is None:
        raise ValueError("this forward does not exist")

    r["enabled"] = bool(enabled)
    _save(s)


def drop_routes(route_ids: list[str]) -> int:
    _need_rw()
    s = _state()
    n = len(s["routes"])
    s["routes"] = [r for r in s["routes"] if r["id"] not in set(route_ids)]
    _save(s)
    return n - len(s["routes"])


def suggest() -> dict:
    """What the panels offer to forward to: each node (name, address, status) and each inbound (tag, protocol, port), and which of them already have a forward."""
    s = _state()
    have = {(r["target_host"].lower(), r["target_port"]) for r in s["routes"] if not r.get("server_id")}

    with session() as db:
        plist = [(p.id, p.name, p.type, panels._cred(p), p.nodes or []) for p in db.scalars(select(Panel).where(Panel.enabled.is_(True)).order_by(Panel.name))]

    out = []

    for pid, name, typ, cred, nodes in plist:
        try:
            opts = panels.options(cred)
        except (panels.PanelError, ValueError) as e:
            out.append({"panel_id": pid, "panel": name, "type": typ, "error": str(e)[:100], "nodes": [], "inbounds": []})
            continue

        ports = sorted({int(i["port"]) for i in opts.get("inbounds", []) if str(i.get("port") or "").isdigit()})
        out.append({"panel_id": pid, "panel": name, "type": typ, "error": "", "ports": ports,
                    "inbounds": [{"tag": i.get("tag"), "protocol": i.get("protocol", ""), "port": i.get("port")} for i in opts.get("inbounds", [])],
                    "nodes": [{"name": n.get("name"), "address": n.get("address"), "status": n.get("status"), "routed": sum(1 for p in ports if (str(n.get("address", "")).lower(), p) in have)} for n in nodes if n.get("address")]})

    return {"panels": out}


def auto_routes(addresses: list[str], ports: list[int]) -> dict:
    """The forwards for every chosen node address on every chosen port (what a node's users connect to)."""
    items = [{"target_host": a, "target_port": p, "name": f"{a}:{p}"} for a in addresses for p in ports]

    if len(items) > MAX_ROUTES:
        raise ValueError(f"{len(items)} forwards are too many (at most {MAX_ROUTES}): choose fewer nodes or ports")

    return add_routes(items, "auto")


# ------------------------------------------------------------------ the tunnels' forwards, and the users
TCP_TYPES = ("backhaul", "backpack", "chisel")


def tunnel_ports() -> list[dict]:
    """The ports every enabled TCP tunnel serves: the Iran server, the port on it, and the foreign server and port it leads to."""
    out = []

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            rec = t.record or {}

            if t.type not in TCP_TYPES or rec.get("direction") == "direct" or (t.transport or "").startswith("l3:"):
                continue                                            # a UDP / raw tunnel cannot go through a TCP forward

            try:
                maps = builder.parse_ports(rec.get("port_mapping") or ",".join(str(p) for p in (t.forwarded_ports or [])))
            except ValueError:
                continue

            for m in maps:
                out.append({"tunnel": t.name, "server_id": t.iran_server_id, "iran_port": m.listen, "target_host": t.foreign.host, "target_port": m.target or m.listen})

    return out


def from_tunnels(server_ids: set[int] | None = None) -> dict:
    """A forward on each Iran server for every port its tunnels serve: its own listen port there, leading (through the pool) to the node and port the tunnel leads to.
    `server_ids`: only the tunnels of these Iran servers (the chosen ones). Run again later, it follows the tunnels (a changed target is updated, the listen port stays)."""
    _need_rw()
    s = _state()
    taken = {r["listen"] for r in s["routes"]}
    have = {(r.get("server_id", 0), r.get("iran_port")): r for r in s["routes"] if r.get("tunnel")}
    added = updated = 0

    for tp in tunnel_ports():
        if server_ids is not None and tp["server_id"] not in server_ids:
            continue

        r = have.get((tp["server_id"], tp["iran_port"]))

        if r is not None:
            if (r["target_host"], r["target_port"], r["tunnel"]) != (tp["target_host"], tp["target_port"], tp["tunnel"]):
                r["target_host"], r["target_port"], r["tunnel"] = tp["target_host"], tp["target_port"], tp["tunnel"]
                r["name"] = f"{tp['tunnel']} :{tp['iran_port']}"
                updated += 1

            continue

        if len(s["routes"]) >= MAX_ROUTES:
            raise ValueError(f"at most {MAX_ROUTES} forwards")

        listen = alloc_port(s, taken)
        taken.add(listen)
        s["routes"].append({"id": uuid.uuid4().hex[:8], "name": f"{tp['tunnel']} :{tp['iran_port']}", "target_host": tp["target_host"], "target_port": tp["target_port"], "listen": listen, "enabled": True,
                            "source": "tunnel", "server_id": tp["server_id"], "tunnel": tp["tunnel"], "iran_port": tp["iran_port"]})
        added += 1

    _save(s)
    return {"added": added, "updated": updated}


def classify(port, iran_port: int, listen: int) -> str:
    """Pure: where a record's origin port stands against one tunnel forward: 'normal' (the tunnel's own port), 'bridged' (the forward's port) or '' (neither)."""
    return "normal" if port == iran_port else "bridged" if port == listen else ""


RECORDS_MAX_AGE = 600           # the page reads the records the DNS page already has: asking ArvanCloud again for every domain takes many seconds; a copy older than this is shown at once and renewed in the background


def _arvan_rows(fresh: bool) -> tuple[list[dict], list[str]]:
    """The records of every ArvanCloud account: from the DNS page's copy when it is recent (and `fresh` is not asked), else read now (which also renews that copy)."""
    accs = {a.id: a for a in dnsprov.accounts() if a.provider == "arvan"}

    if not fresh and dnscache.CACHE["at"]:
        if time.time() - dnscache.CACHE["at"] > RECORDS_MAX_AGE:
            dnscache.refresh_in_background()                     # what is on the screen is the copy it has; the next look has the new one

        bad = [f"{i['name']}: {i['error']}" for i in dnscache.CACHE["accounts"] if i["id"] in accs and not i["ok"]]
        return [r for r in dnscache.CACHE["rows"] if r.get("account") in accs], bad

    dnscache.read_all()
    bad = [f"{i['name']}: {i['error']}" for i in dnscache.CACHE["accounts"] if i["id"] in accs and not i["ok"]]
    return [r for r in dnscache.CACHE["rows"] if r.get("account") in accs], bad


def _forget_records() -> None:
    """A record was changed: the DNS page's copy is out of date."""
    dnscache.forget()


def connect_plan(fresh: bool = False) -> dict:
    """What 'connect the records' would change: the ArvanCloud records (proxied, one Iran server address) whose origin port is the port of a tunnel that has a forward -
    each with the port it would get. A record already on a forward's port is 'bridged' and can be put back. `fresh`: ask ArvanCloud now instead of using the copy the DNS page has."""
    s = _state()
    routes = [r for r in s["routes"] if r.get("tunnel") and r.get("enabled", True)]
    by_iran = {(r["server_id"], r["iran_port"]): r for r in routes}
    by_listen = {(r["server_id"], r["listen"]): r for r in routes}

    with session() as db:
        iran = {x.host: x for x in db.scalars(select(Server).where(Server.role == "iran"))}
        gws = {g.server_id: g for g in db.scalars(select(Gateway))}

    rows = []
    names = {a.id: a.name for a in dnsprov.accounts()}
    got, errors = _arvan_rows(fresh) if routes else ([], [])             # no forward from a tunnel yet: there is nothing to look for, ArvanCloud is not asked

    for r in got:
        if r.get("type") != "A" or not r.get("cloud") or r.get("port") is None or len(r.get("ips") or []) != 1:
            continue

        sv = iran.get(r["ips"][0])

        if sv is None:
            continue

        port = int(r["port"])
        route = by_iran.get((sv.id, port)) or by_listen.get((sv.id, port))

        if route is None:
            continue

        g = gws.get(sv.id)
        rows.append({"key": f"{r['account']}|{r['domain']}|{r['id']}", "account": r["account"], "account_name": names.get(r["account"], ""), "domain": r["domain"], "id": r["id"], "name": r["name"], "fqdn": r["fqdn"], "ip": r["ips"][0],
                     "server_id": sv.id, "server": sv.name, "state": classify(port, route["iran_port"], route["listen"]), "port": port, "iran_port": route["iran_port"], "listen": route["listen"], "tunnel": route["tunnel"],
                     "target": f"{route['target_host']}:{route['target_port']}", "ready": bool(g and g.installed and g.applied_at)})

    rows.sort(key=lambda x: (x["server"], x["fqdn"]))

    return {"records": rows, "errors": errors, "forwards": len(routes), "bridged": sum(1 for x in rows if x["state"] == "bridged"), "cached_at": int(dnscache.CACHE["at"] or time.time())}


def _set_port(row: dict, port: int, before_log: bool) -> dict:
    """Change the origin port of one record (everything else of the record stays as it was read). Returns what the provider answered."""
    prov = dnsprov.provider(row["account"])
    cur = prov.get(row["domain"], row["id"])

    if len(cur["ips"]) != 1 or cur["ips"][0] != row["ip"]:
        raise ValueError(f"{row['fqdn']} changed since the plan was made (its address is not {row['ip']} any more)")

    res = prov.update(row["domain"], row["id"], cur["name"], cur["ips"], int(cur["ttl"] or 120), bool(cur["cloud"]), port=port)
    _forget_records()

    if before_log:
        failover.log_bridge(row["account"], row["domain"], row["fqdn"], row["id"], f"{row['ip']}:{row['port']}", f"{row['ip']}:{port}", res.get("before"), row["server"])

    return res


def _connect_story(aid: int, keys: list[str]) -> None:
    plan = {x["key"]: x for x in connect_plan(True)["records"]}
    done = bad = 0
    status: dict[int, dict] = {}

    for k in keys:
        row = plan.get(k)

        if row is None:
            autopilot._say(aid, f"{k}: not in the plan any more - skipped.")
            bad += 1
            continue

        if row["state"] != "normal":
            autopilot._say(aid, f"{row['fqdn']}: already on the forward's port - nothing to do.")
            continue

        if row["server_id"] not in status:
            try:
                status[row["server_id"]] = gateway.status(row["server_id"])
            except Exception as e:  # noqa: BLE001  an unreachable server is a reason, not a crash
                status[row["server_id"]] = {"error": str(e)[:100]}

        sv = status[row["server_id"]]

        if sv.get("error") or sv.get("active") != "active" or not sv.get("listen", {}).get(str(row["listen"])):
            autopilot._say(aid, f"{row['fqdn']}: NOT changed - the forward :{row['listen']} is not listening on {row['server']} ({sv.get('error') or 'press Apply first'}).")
            bad += 1
            continue

        try:
            _set_port(row, row["listen"], True)
            autopilot._say(aid, f"{row['fqdn']}: origin {row['ip']}:{row['port']} → {row['ip']}:{row['listen']} (the tunnel {row['tunnel']} stays untouched; users now go through the pool to {row['target']}).")
            done += 1
        except (ValueError, RuntimeError) as e:
            autopilot._say(aid, f"{row['fqdn']}: NOT changed - {str(e)[:140]}")
            bad += 1

    with session() as db:
        emit(db, "warn" if bad else "ok", "outbound", f"Users connected through the outbound: {done} record(s) changed, {bad} not.", "outbound")

    autopilot._say(aid, f"Done: {done} record(s) now go through the outbound" + (f", {bad} could not be changed." if bad else "."), done=True, ok=not bad)


def connect(keys: list[str]) -> int:
    """Start the story that points the chosen records at the forwards (a record already there is left alone). Returns the action id."""
    _need_rw()

    if not keys:
        raise ValueError("choose at least one record")

    return autopilot._start("outbound connect", _connect_story, list(keys)[:300], action="outbound", source="manual")


def _disconnect_story(aid: int, keys: list[str]) -> None:
    plan = {x["key"]: x for x in connect_plan(True)["records"]}
    s = failover._state()
    done = bad = 0

    for k in keys:
        row = plan.get(k)

        if row is None or row["state"] != "bridged":
            autopilot._say(aid, f"{k}: not on a forward - nothing to put back.")
            continue

        entry = next((e for e in reversed(s["dns_log"]) if e.get("op") == "bridge" and not e.get("restored") and e.get("record_id") == row["id"] and e.get("domain") == row["domain"]), None)

        try:
            if entry is not None:
                failover.restore(entry["id"])
            else:
                _set_port(row, row["iran_port"], False)

            autopilot._say(aid, f"{row['fqdn']}: back on {row['ip']}:{row['iran_port']} (the tunnel).")
            done += 1
        except (ValueError, RuntimeError) as e:
            autopilot._say(aid, f"{row['fqdn']}: NOT put back - {str(e)[:140]}")
            bad += 1

    _forget_records()
    autopilot._say(aid, f"Done: {done} record(s) back on their tunnels" + (f", {bad} could not be changed." if bad else "."), done=True, ok=not bad)


def disconnect(keys: list[str]) -> int:
    """Start the story that puts the chosen records back on the tunnels' ports. Returns the action id."""
    _need_rw()

    if not keys:
        raise ValueError("choose at least one record")

    return autopilot._start("outbound disconnect", _disconnect_story, list(keys)[:300], action="outbound", source="manual")
# ------------------------------------------------------------------ the relay (a foreign server behind the exits)
def pick_relay(cands: list[dict]) -> int | None:
    """Pure: the foreign server that suits best as the relay: online, not busy (CPU / RAM under 85 %), then the most room and the fewest tunnels. None when no server qualifies."""
    ok = [c for c in cands if c.get("online") is not False and max(c.get("cpu") or 0, c.get("ram") or 0) < 85]

    if not ok:
        return None

    def room(c: dict) -> int:
        return 100 - max(c.get("cpu") if c.get("cpu") is not None else 50, c.get("ram") if c.get("ram") is not None else 50) - 2 * int(c.get("tunnels") or 0)

    return max(ok, key=lambda c: (room(c), -c["id"]))["id"]


def suggest_relay() -> dict | None:
    """The foreign server the panel would choose as the relay (the quietest one), or None."""
    with session() as db:
        fo = list(db.scalars(select(Server).where(Server.role == "foreign", Server.enabled.is_(True))))
        load = {}

        for sv in fo:
            smp = db.execute(select(ServerSample.cpu, ServerSample.ram).where(ServerSample.server_id == sv.id).order_by(ServerSample.ts.desc()).limit(1)).first()       # LIMIT 1 and two columns: a hundred thousand samples are not read to find the newest
            load[sv.id] = (smp.cpu, smp.ram) if smp is not None else (None, None)

        count = {}

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            count[t.foreign_server_id] = count.get(t.foreign_server_id, 0) + 1

        cands = [{"id": sv.id, "name": sv.name, "online": sv.online, "cpu": load[sv.id][0], "ram": load[sv.id][1], "tunnels": count.get(sv.id, 0)} for sv in fo]

    pid = pick_relay(cands)
    return next(({"id": c["id"], "name": c["name"]} for c in cands if c["id"] == pid), None)


def relay_config(port: int, method: str, password: str) -> dict:
    """Pure: the Xray config of the relay: a Shadowsocks inbound (NO sniffing: it must dial exactly what the gateway asked), a direct exit, and a block of the server's own and private addresses."""
    return {"log": {"loglevel": "warning", "error": RELAY_DIR + "/error.log"},
            "inbounds": [{"tag": "relay-in", "listen": "0.0.0.0", "port": port, "protocol": "shadowsocks", "settings": {"method": method, "password": password, "network": "tcp"}}],
            "outbounds": [{"tag": "direct", "protocol": "freedom", "settings": {}}, {"tag": "block", "protocol": "blackhole", "settings": {}}],
            "routing": {"domainStrategy": "AsIs", "rules": [{"type": "field", "ip": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"], "outboundTag": "block"}]}}


def hubs_state(s: dict | None = None) -> list[dict]:
    """The relays for the page (never the password)."""
    s = s or _state()

    with session() as db:
        rows = list(db.scalars(select(OutboundHub)))
        names = {x.id: (x.name, x.host) for x in db.scalars(select(Server))}

    out = []

    for h in rows:
        chk = (s["health"].get("hubs") or {}).get(str(h.server_id)) or {}
        out.append({"server_id": h.server_id, "name": names.get(h.server_id, ("?", ""))[0], "host": names.get(h.server_id, ("", ""))[1], "port": h.port, "installed": h.installed, "applied_at": h.applied_at,
                    "active": chk.get("active"), "checked_at": chk.get("at", 0)})

    return out


def set_hubs(server_ids: list[int]) -> None:
    """Choose the foreign servers that run the relay (a new one gets its own port and password; one that is installed must be removed first - it holds files on its server)."""
    _need_rw()
    want = {int(i) for i in server_ids}

    with session() as db:
        foreign = {x.id for x in db.scalars(select(Server).where(Server.role == "foreign", Server.enabled.is_(True)))}

        if want - foreign:
            raise ValueError("choose enabled foreign servers")

        rows = {h.server_id: h for h in db.scalars(select(OutboundHub))}

        for sid in set(rows) - want:
            if rows[sid].installed:
                raise ValueError("this relay is installed on its server: remove it from there first")

            db.delete(rows[sid])

        taken = {h.port for h in rows.values()}

        for sid in want - set(rows):
            port = next(p for p in random.sample(range(RELAY_PORTS[0], RELAY_PORTS[1] + 1), 200) if p not in taken)
            taken.add(port)
            db.add(OutboundHub(server_id=sid, port=port, password=secrets.token_urlsafe(24)))


def relays() -> list[dict]:
    """The relays the gateways may use: installed and applied ones, in the shape the gateway wants."""
    with session() as db:
        return [{"id": f"h{h.server_id}", "host": h.server.host, "port": h.port, "method": h.method, "password": h.password} for h in db.scalars(select(OutboundHub).where(OutboundHub.installed.is_(True), OutboundHub.applied_at > 0))]


def apply_hub(aid: int, server_id: int) -> bool:
    """Install Xray on the relay's server where it is missing and put the relay's config there."""
    with session() as db:
        h = db.get(OutboundHub, server_id)

        if h is None:
            raise ValueError("this server is not a relay")

        node, name, port, method, pw, installed = Node(h.server.host, h.server.ssh_user, h.server.ssh_port), h.server.name, h.port, h.method, h.password, h.installed

    if not installed:
        binary = get_settings().data_dir.parent / "engines" / "xray"

        if not binary.exists() or binary.stat().st_size < 5_000_000:
            raise RuntimeError("the panel has no Xray binary cached; run scripts/fetch-xray.sh on the panel server")

        autopilot._say(aid, f"relay {name}: installing Xray (about 35 MB over SSH)…")
        s = get_settings()
        opts = ["-i", str(s.ssh_key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={s.known_hosts}", "-o", "ConnectTimeout=15"]
        run_action(node, "xrelay_mkdir")
        r = subprocess.run(["scp", *opts, "-P", str(node.port), "--", str(binary), f"{ssh_target(node)}:{RELAY_DIR}/xray.new"], capture_output=True, text=True, timeout=400, check=False)

        if r.returncode != 0:
            raise RuntimeError("could not copy Xray to the relay: " + r.stderr.strip()[:140])

        ver = (re.search(r"VER (.+)", run_action(node, "xrelay_install_bin", timeout=60)) or [None, ""])[1].strip()

        with session() as db:
            x = db.get(OutboundHub, server_id)
            x.installed, x.version = True, ver[:64]

    out = run_action(node, "xrelay_apply", {"B64": base64.b64encode(json.dumps(relay_config(port, method, pw)).encode()).decode(), "PORT": str(port)}, timeout=120)

    if out.strip().startswith("REFUSED"):
        raise RuntimeError("Xray refused the relay config: " + out.strip()[8:240])

    if "ACTIVE" not in out:
        raise RuntimeError("the relay did not start; the previous config was put back. " + out.strip()[:240])

    with session() as db:
        db.get(OutboundHub, server_id).applied_at = int(time.time())
        emit(db, "ok", "outbound", f"Relay on {name} applied (port {port}).", name)

    autopilot._say(aid, f"relay {name}: running on port {port}.")
    return True


def remove_hub(server_id: int) -> None:
    """Take the relay off its server and forget it."""
    _need_rw()

    with session() as db:
        h = db.get(OutboundHub, server_id)

        if h is None:
            raise ValueError("this server is not a relay")

        node, installed = Node(h.server.host, h.server.ssh_user, h.server.ssh_port), h.installed

    if installed:
        run_action(node, "xrelay_remove", timeout=60)

    with session() as db:
        h = db.get(OutboundHub, server_id)

        if h is not None:
            db.delete(h)

    s = _state()
    (s["health"].get("hubs") or {}).pop(str(server_id), None)
    _save(s)


# ------------------------------------------------------------------ how the users are connected: by hand, or by the panel itself
def set_users(mode: str, after_minutes: int | None = None, back_minutes: int | None = None) -> None:
    """manual: you choose the records and press Connect. auto: a tunnel that stays down for `after_minutes` has its records pointed at the pool by the panel itself, and they go back
    after `back_minutes` of the tunnel being up (what the panel connected by itself only)."""
    _need_rw()

    if mode not in USERS_MODES:
        raise ValueError("the mode is manual or auto")

    if mode == "auto" and not AUTO_USERS:
        raise ValueError("the automatic switch is off for now: it is still being tested. Connect the records by hand.")

    if mode == "auto" and not license.has("failover"):
        raise ValueError("the automatic switch needs the failover license (Settings → About → License)")

    s = _state()
    s["users"]["mode"] = mode

    if after_minutes is not None:
        s["users"]["after_minutes"] = max(1, min(120, int(after_minutes)))

    if back_minutes is not None:
        s["users"]["back_minutes"] = max(5, min(1440, int(back_minutes)))

    _save(s)


def due(tunnels: dict[str, dict], auto: dict, after: int, back: int, now: int, cooldown: int = 900) -> tuple[list[str], list[str]]:
    """Pure: which tunnels to connect and which to put back. `tunnels`: name -> {state, since}; `auto`: tunnel -> when the panel connected it. A tunnel that has been down for `after` minutes
    (and was not touched for `cooldown` seconds) is connected; one the panel connected that has been up for `back` minutes comes back."""
    go, home = [], []

    for name, t in tunnels.items():
        age = now - int(t.get("since") or now)

        if name in auto:
            if t.get("state") == "up" and age >= back * 60 and now - int(auto[name]) >= cooldown:
                home.append(name)
        elif t.get("state") == "down" and age >= after * 60:
            go.append(name)

    return sorted(go), sorted(home)


def auto_users() -> None:
    """The job (every minute): in mode auto, point the records of a tunnel that stays down at the pool, and put them back when it has been healthy. One change per tick; Telegram says so."""
    s = _state()
    u = s["users"]

    if not AUTO_USERS or u["mode"] != "auto" or get_settings().read_only or not license.has("failover"):
        return

    now = int(time.time())

    with session() as db:
        tunnels = {t.name: {"state": t.state, "since": t.state_since} for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))}

    mine = {r["tunnel"]: r for r in s["routes"] if r.get("tunnel") and r.get("enabled", True)}
    go, home = due({k: v for k, v in tunnels.items() if k in mine}, u["auto"], u["after_minutes"], u["back_minutes"], now)

    for name in list(u["auto"]):                       # a tunnel that is gone from the list is forgotten
        if name not in tunnels:
            u["auto"].pop(name)

    if not go and not home:
        _save(s)
        return

    rows = connect_plan(True)["records"]

    if home:
        keys = [r["key"] for r in rows if r["tunnel"] in home and r["state"] == "bridged"]

        for name in home:
            u["auto"].pop(name, None)

        _save(s)

        if keys:
            _tell(f"↩ Tunnel(s) {', '.join(home)} healthy for {u['back_minutes']} min: {len(keys)} record(s) go back to the tunnel.")
            disconnect(keys)

        return

    healthy = {sid for sid, h in s["health"].items() if sid.isdigit() and verdict((h or {}).get("summary")) in ("ok", "degraded")}
    keys = [r["key"] for r in rows if r["tunnel"] in go and r["state"] == "normal" and r["ready"] and str(r["server_id"]) in healthy]

    if not keys:
        return

    for name in go:
        if any(r["tunnel"] == name and r["key"] in keys for r in rows):
            u["auto"][name] = now

    _save(s)
    _tell(f"🌉 Tunnel(s) {', '.join(go)} down for {u['after_minutes']} min: {len(keys)} record(s) now go through the outbound pool.")
    connect(keys)


def _tell(text: str) -> None:
    try:
        telegram.send_text(text)
    except Exception:  # a Telegram problem must never stop the switch
        log.warning("outbound notice not sent", exc_info=True)

# ------------------------------------------------------------------ putting it on the Iran servers
def desired(server_id: int = 0) -> dict:
    """The gateway state an Iran server should have: the mode, the forwards (the ones for every server and the ones made from its tunnels), the enabled configs of the pool."""
    s = _state()

    with session() as db:
        configs = [{"id": c.id, "uri": c.uri, "note": c.note or gateway.describe(c.uri), "enabled": True} for c in db.scalars(select(OutboundConfig).where(OutboundConfig.enabled.is_(True)).order_by(OutboundConfig.created_at))]

    return {"mode": s["mode"], "ports": ports_of([r for r in s["routes"] if r.get("enabled", True)], server_id), "borrowed": configs}


def apply_one(aid: int, server_id: int) -> bool:
    with session() as db:
        sv = db.get(Server, server_id)
        gw = db.get(Gateway, server_id)
        name, installed = (sv.name if sv else str(server_id)), bool(gw and gw.installed)

    d = desired(server_id)

    if not d["borrowed"] and d["mode"] != "auto":
        autopilot._say(aid, f"{name}: the pool has no enabled config - nothing to carry the traffic. Add a config first.")
        return False

    if not installed:
        autopilot._say(aid, f"{name}: installing Xray (about 35 MB over SSH)…")
        autopilot._say(aid, f"{name}: installed {gateway.install(server_id)['version'][:24]}")

    gateway.set_state(server_id, d)
    hubs = relays()
    res = gateway.apply(server_id, hubs)
    autopilot._say(aid, f"{name}: running with {len(d['borrowed'])} exit(s), {len(res['ports'])} forward(s), mode {d['mode']}" + (f", through {len(hubs)} relay(s)." if hubs else ". NO relay: an exit that reads the Host of the traffic will break it - add a relay."))
    return True


def _apply_story(aid: int) -> None:
    s = _state()

    if not s["gateways"]:
        autopilot._say(aid, "No Iran server is chosen for the outbound.", done=True, ok=False)
        return

    ok = 0

    if s["foreign"] and s["ports"]:
        made = make_paths()
        autopilot._say(aid, f"Tunnels through your configs (Iran x foreign x port): {made['added']} new, {made['kept']} already there" + (f", {made['removed']} no longer chosen and removed." if made["removed"] else "."))
    else:
        made = from_tunnels(set(s["gateways"]))
        autopilot._say(aid, f"No foreign server and port chosen: forwards for the tunnels of the chosen Iran server(s) instead - {made['added']} new, {made['updated']} updated.")

    with session() as db:
        hubs = [h.server_id for h in db.scalars(select(OutboundHub))]

    if not hubs:
        pick = suggest_relay()

        if pick is None:
            autopilot._say(aid, "No foreign server qualifies as a relay right now (all are offline or busy): the traffic goes without one.")
        else:
            set_hubs([pick["id"]])
            hubs = [pick["id"]]
            autopilot._say(aid, f"Relay: {pick['name']} chosen (the quietest foreign server). You can change it on the page.")

    for hid in hubs:
        try:
            apply_hub(aid, hid)
        except Exception as e:  # noqa: BLE001  a relay that fails is said, the gateways still go on without it
            autopilot._say(aid, f"relay {hid}: failed: {str(e)[:160]}")

    for sid in s["gateways"]:
        try:
            ok += 1 if apply_one(aid, sid) else 0
        except Exception as e:  # noqa: BLE001  one server must not stop the others
            autopilot._say(aid, f"Server {sid}: failed: {str(e)[:160]}")

    check()
    autopilot._say(aid, f"Done: {ok} of {len(s['gateways'])} Iran server(s) carry the outbound now.", done=True, ok=ok == len(s["gateways"]))


def apply_all() -> int:
    """Start the story that puts the pool and the forwards on every chosen Iran server (installs Xray where it is missing). Returns the action id."""
    _need_rw()
    return autopilot._start("outbound apply", _apply_story, action="outbound", source="manual")


def remove_from(server_id: int) -> None:
    _need_rw()
    gateway.remove(server_id)
    s = _state()
    s["health"].pop(str(server_id), None)
    _save(s)


# ------------------------------------------------------------------ the job: what the pool looks like from each Iran server
def check() -> None:
    """Every few minutes: from each chosen, installed Iran server test every exit and the gateway as users see it; keep the numbers; say so when the picture changes."""
    s = _state()

    if not s["gateways"]:
        return

    with session() as db:
        ids = [c.id for c in db.scalars(select(OutboundConfig).where(OutboundConfig.enabled.is_(True)).order_by(OutboundConfig.created_at))]
        gws = {g.server_id: g for g in db.scalars(select(Gateway))}
        names = {x.id: x.name for x in db.scalars(select(Server))}

    now = int(time.time())
    changed = False

    for sid in s["gateways"]:
        g = gws.get(sid)

        if g is None or not g.installed:
            continue

        try:
            sm = summarize(gateway.test(sid), ids)
        except Exception as e:  # noqa: BLE001  an unreachable server is a result, not a crash
            log.warning("outbound check of %s failed: %s", names.get(sid), e)
            sm = {"alive": 0, "total": len(ids), "exits": {}, "gateway_ok": False, "ms": None, "error": str(e)[:120]}

        old = verdict((s["health"].get(str(sid)) or {}).get("summary"))
        new = verdict(sm)
        s["health"][str(sid)] = {"at": now, "summary": sm}
        changed = True

        if new != old and (old != "unknown" or new == "down"):          # a first look says something only when it is bad
            _say_change(names.get(sid, str(sid)), old, new, sm)

    _check_hubs(s, now)

    if changed or s["health"].get("hubs") is not None:
        _save(s)


def _check_hubs(s: dict, now: int) -> None:
    """The relays: is the service running and listening? A relay that stops is said once."""
    with session() as db:
        rows = [(h.server_id, h.server.name, Node(h.server.host, h.server.ssh_user, h.server.ssh_port), h.port) for h in db.scalars(select(OutboundHub).where(OutboundHub.installed.is_(True)))]

    seen = s["health"].setdefault("hubs", {})

    for sid, name, node, port in rows:
        try:
            out = run_script(node, "xrelay_status", {"PORT": str(port)}, timeout=40)
            active = "ACTIVE active" in out and f"LISTEN {port} 0" not in out
        except (SSHError, ValueError):
            active = False

        old = (seen.get(str(sid)) or {}).get("active")
        seen[str(sid)] = {"at": now, "active": active}

        if old is not None and old != active:
            say = "is running again." if active else "is NOT running: the traffic of the outbound has no way through it."

            with session() as db:
                emit(db, "ok" if active else "bad", "outbound", f"Relay on {name} {say}", name)

            _tell(("✅ " if active else "🔴 ") + f"Outbound relay on {name} {say}")


def _say_change(name: str, old: str, new: str, sm: dict) -> None:
    say = {"ok": f"every exit answers again ({sm['alive']}/{sm['total']}).", "degraded": f"{sm['alive']} of {sm['total']} exits answer; the load goes through those.",
           "down": "no exit answers - the users that go through it have no way out."}[new]

    with session() as db:
        emit(db, {"ok": "ok", "degraded": "warn", "down": "bad"}[new], "outbound", f"Outbound on {name}: {say}", name)

    try:
        telegram.send_text({"ok": "✅ ", "degraded": "⚠ ", "down": "🔴 "}[new] + f"Outbound on {name}: {say}")
    except Exception:  # a Telegram problem must never stop the check
        log.warning("outbound notice not sent", exc_info=True)
