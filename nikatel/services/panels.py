"""Marzban / PasarGuard panels: login, health + nodes every 2 minutes, users and hosts on demand.

Both panels share the Marzban API shape (`/api/admin/token`, `/api/system`, `/api/nodes`, `/api/users`,
`/api/hosts`); where PasarGuard differs (hosts as a list with ids, expire as a date string, cores as a
list) the functions look at the shape of the answer, not at the declared type."""
from __future__ import annotations

import calendar
import datetime
import json
import re
import time
from pathlib import Path

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Config, Panel, Subscription
from .events import emit

log = get_logger("panels")

TYPES = ("marzban", "pasarguard")
_TOKENS: dict[int, tuple[str, float]] = {}
_USERNAME = re.compile(r"^[A-Za-z0-9_.@\-]{1,64}$")
_SORTS = ("created_at", "-created_at", "username", "-username", "used_traffic", "-used_traffic", "expire", "-expire")
_RESETS = ("no_reset", "day", "week", "month", "year")
_STATUSES = ("active", "disabled", "limited", "expired", "on_hold")
NODE_OK = ("connected", "disabled", "healthy", "connecting")


# ------------------------------------------------------------------ pure helpers
def base_url(url: str) -> str:
    u = str(url or "").strip()

    if not re.match(r"^https?://", u, re.IGNORECASE):
        raise ValueError("the address must start with http:// or https://")

    u = u.split("#")[0].split("?")[0].rstrip("/")

    for tail in ("/dashboard", "/api"):
        if u.lower().endswith(tail):
            u = u[: -len(tail)]

    if not re.match(r"^https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~\-/]*)?$", u):
        raise ValueError("the address is not valid")

    return u


def _pick(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]

    return default


def parse_system(sysd: dict) -> dict:
    sysd = sysd if isinstance(sysd, dict) else {}
    mt, mu = _pick(sysd, "mem_total", "memory_total"), _pick(sysd, "mem_used", "memory_used")
    return {"version": str(_pick(sysd, "version", "panel_version", default="") or ""),
            "users_total": _pick(sysd, "total_user", "total_users", "users_total"),
            "users_active": _pick(sysd, "users_active", "active_users"),
            "users_online": _pick(sysd, "online_users", "users_online"),
            "cpu": _pick(sysd, "cpu_usage", "cpu_percent"),
            "mem_pct": round(mu * 100.0 / mt, 1) if mt and mu is not None else None,
            "bw_in": _pick(sysd, "incoming_bandwidth"), "bw_out": _pick(sysd, "outgoing_bandwidth")}


def parse_nodes(nd) -> list[dict]:
    if isinstance(nd, dict):
        nd = nd.get("nodes") or nd.get("items") or []

    out = []

    for n in nd if isinstance(nd, list) else []:
        if not isinstance(n, dict):
            continue

        out.append({"id": n.get("id"), "name": str(n.get("name", ""))[:60], "address": str(n.get("address", ""))[:80],
                    "status": str(n.get("status", "")).lower(), "xray": str(n.get("xray_version") or n.get("core_version") or "")[:20],
                    "message": str(n.get("message") or "")[:120]})

    return out


def date_of(v) -> str:
    """'YYYY-MM-DD' of a panel expire value (unix seconds or a date string), '' when unlimited."""
    if isinstance(v, (int, float)) and v > 0:
        return time.strftime("%Y-%m-%d", time.gmtime(v))

    if isinstance(v, str) and len(v) >= 10:
        return v[:10]

    return ""


def expire_plus(old, add_days: int):
    """Keeps the type the panel uses for 'expire' (unix seconds or an ISO string)."""
    now = time.time()

    if isinstance(old, (int, float)) and old:
        base = max(now, float(old))
    elif isinstance(old, str) and old:
        try:
            base = max(now, datetime.datetime.fromisoformat(old.replace("Z", "+00:00")).timestamp())
        except ValueError:
            base = now
    else:
        base = now

    new = base + int(add_days) * 86400

    if isinstance(old, str):
        return datetime.datetime.fromtimestamp(new, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return int(new)


def user_row(u: dict) -> dict:
    return {"username": str(u.get("username", "")), "status": str(u.get("status", "")), "used": u.get("used_traffic"),
            "limit": u.get("data_limit"), "expire": u.get("expire"), "expire_date": date_of(u.get("expire")),
            "online_at": u.get("online_at"), "created_at": u.get("created_at"),
            "sub": str(u.get("subscription_url") or ""), "note": str(u.get("note") or "")[:120]}


def hosts_rows(raw, ports: dict) -> list[dict]:
    """Marzban: {inbound_tag: [host, ...]} -> key 'tag#index'. PasarGuard: [host with id] -> key = id."""
    hosts = []

    if isinstance(raw, dict):
        for tag, lst in raw.items():
            for i, h in enumerate(lst if isinstance(lst, list) else []):
                hosts.append({"key": f"{tag}#{i}", "tag": tag, "remark": str(h.get("remark", ""))[:60], "address": str(h.get("address", ""))[:80],
                              "port": h.get("port") or ports.get(tag), "disabled": bool(h.get("is_disabled"))})
    elif isinstance(raw, list):
        for h in raw:
            if not isinstance(h, dict):
                continue

            tag = h.get("inbound_tag") or h.get("tag") or ""
            addr = h.get("address")
            hosts.append({"key": str(h.get("id")), "tag": tag, "remark": str(h.get("remark", ""))[:60],
                          "address": (", ".join(map(str, addr)) if isinstance(addr, list) else str(addr or ""))[:80],
                          "port": h.get("port") or ports.get(tag), "disabled": bool(h.get("is_disabled"))})

    return hosts


# ------------------------------------------------------------------ HTTP
class PanelError(RuntimeError):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


def _cred(p: Panel) -> dict:
    return {"id": p.id, "url": p.url, "username": p.username, "password": (p.secrets or {}).get("password", ""), "verify_tls": p.verify_tls, "type": p.type}


def _http(c: dict, method: str, path: str, token: str | None = None, form: dict | None = None, body=None, timeout: float = 20):
    headers = {"Accept": "application/json", "User-Agent": "nikatel-panel"}

    if token:
        headers["Authorization"] = "Bearer " + token

    try:
        r = httpx.request(method, c["url"] + path, headers=headers, data=form, json=body if form is None else None,
                          timeout=timeout, verify=bool(c.get("verify_tls", True)))
    except httpx.HTTPError as e:
        raise PanelError(f"no answer from the panel ({type(e).__name__})")

    if r.status_code >= 400:
        detail = ""

        try:
            j = r.json()
            detail = str(j.get("detail") if isinstance(j, dict) else j)[:120]
        except ValueError:
            pass

        raise PanelError(f"HTTP {r.status_code}" + (f": {detail}" if detail else ""), r.status_code)

    if not r.content:
        return None

    try:
        return r.json()
    except ValueError:
        raise PanelError("the panel answered with something that is not JSON")


def _token(c: dict, fresh: bool = False) -> str:
    tok = _TOKENS.get(c["id"])

    if tok and not fresh and tok[1] > time.time():
        return tok[0]

    r = _http(c, "POST", "/api/admin/token", form={"username": c["username"], "password": c["password"]})
    t = (r or {}).get("access_token") if isinstance(r, dict) else None

    if not t:
        raise PanelError("login failed: no token returned")

    if c["id"]:
        _TOKENS[c["id"]] = (t, time.time() + 45 * 60)

    return t


def call(c: dict, method: str, path: str, **kw):
    try:
        return _http(c, method, path, token=_token(c), **kw)
    except PanelError as e:
        if e.code == 401:
            return _http(c, method, path, token=_token(c, fresh=True), **kw)

        raise


def cred_for(panel_id: int) -> dict:
    with session() as db:
        p = db.get(Panel, panel_id)

        if p is None:
            raise ValueError("panel not found")

        return _cred(p)


# ------------------------------------------------------------------ health
def check(c: dict) -> dict:
    t0 = time.time()
    res = {"ok": False, "error": "", "nodes": [], "stats": {}, "latency_ms": None}

    try:
        sysd = call(c, "GET", "/api/system")
        res["latency_ms"] = int((time.time() - t0) * 1000)
        res["stats"] = parse_system(sysd)
        res["ok"] = True
    except (PanelError, ValueError) as e:
        res["error"] = str(e)[:160]
        return res

    try:
        res["nodes"] = parse_nodes(call(c, "GET", "/api/nodes"))
    except (PanelError, ValueError) as e:
        res["nodes_error"] = str(e)[:120]

    return res


def node_reconnect(c: dict, node_id: int) -> None:
    call(c, "POST", f"/api/node/{int(node_id)}/reconnect", body={})


def node_logs(c: dict, node_id: int) -> str:
    r = call(c, "GET", f"/api/node/{int(node_id)}/logs?lines=120")

    if isinstance(r, dict):
        r = r.get("logs") or r.get("log") or r.get("lines") or json.dumps(r)

    if isinstance(r, list):
        r = "\n".join(str(x) for x in r)

    return str(r or "")[-6000:]


def check_all() -> None:
    """The job: every enabled panel, nodes that stay disconnected get a reconnect request after two checks."""
    with session() as db:
        items = [(p.id, _cred(p)) for p in db.scalars(select(Panel).where(Panel.enabled.is_(True)))]

    rw = not get_settings().read_only

    for pid, c in items:
        res = check(c)
        now = int(time.time())

        with session() as db:
            p = db.get(Panel, pid)

            if p is None:
                continue

            old_nodes = {str(n.get("id")): n for n in (p.nodes or [])}
            label = f"{p.name} ({p.type})"
            p.checked_at = now
            p.latency_ms = res["latency_ms"]

            if res["ok"]:
                p.fails = 0
                p.last_error = res.get("nodes_error", "")
                p.stats = res["stats"]
                p.version = res["stats"].get("version") or p.version
                nodes = []

                for n in res["nodes"]:
                    prev = old_nodes.get(str(n.get("id")), {})
                    fails = 0 if n["status"] in NODE_OK else int(prev.get("fails", 0)) + 1
                    n["fails"] = fails

                    if fails == 0 and int(prev.get("fails", 0)) >= 2:
                        emit(db, "ok", "panels", f"Node {n['name']} of {label} is connected again.", p.name)
                    elif fails >= 2 and n.get("id") is not None:
                        how = "reconnect not requested (panel is read-only)"

                        if rw:
                            try:
                                node_reconnect(c, n["id"])
                                how = "reconnect requested"
                            except (PanelError, ValueError) as e:
                                how = "reconnect failed: " + str(e)[:80]

                        if fails in (2, 10, 60):
                            emit(db, "bad", "panels", f"Node {n['name']} of {label} is {n['status'] or 'not connected'}" + (f" ({n['message']})" if n["message"] else "") + f" — {how}.", p.name)

                    nodes.append(n)

                p.nodes = nodes

                if p.status != "up":
                    if p.status == "down":
                        emit(db, "ok", "panels", f"Panel {label} answers again.", p.name)

                    p.status, p.status_since = "up", now
            else:
                p.fails += 1
                p.last_error = res["error"]

                if p.fails >= 2 and p.status != "down":
                    emit(db, "bad", "panels", f"Panel {label} is not answering: {res['error']}", p.name)
                    p.status, p.status_since = "down", now

        log.info("panel %s ok=%s nodes=%s latency=%s %s", c["url"].split("//")[-1].split("/")[0].split(":")[0][:3] + "…", res["ok"], len(res["nodes"]), res["latency_ms"], res["error"])


# ------------------------------------------------------------------ CRUD
def public(p: Panel) -> dict:
    return {"id": p.id, "name": p.name, "type": p.type, "url": p.url, "username": p.username, "verify_tls": p.verify_tls, "enabled": p.enabled,
            "status": p.status, "status_since": p.status_since, "fails": p.fails, "checked_at": p.checked_at, "latency_ms": p.latency_ms,
            "version": p.version, "stats": p.stats or {}, "nodes": p.nodes or [], "last_error": p.last_error}


def add(name: str, typ: str, url: str, username: str, password: str, verify_tls: bool = True) -> dict:
    name = str(name or "").strip()[:40]
    typ = str(typ or "").strip().lower()

    if typ not in TYPES:
        raise ValueError("pick Marzban or PasarGuard")

    if not name or not re.match(r"^[\w .\-]{1,40}$", name):
        raise ValueError("give the panel a short name (letters, digits, - _ . and spaces)")

    if not username or not password:
        raise ValueError("the admin username and password are needed")

    c = {"id": 0, "url": base_url(url), "username": str(username).strip(), "password": str(password), "verify_tls": bool(verify_tls), "type": typ}
    res = check(c)

    if not res["ok"]:
        raise ValueError("the panel did not accept the login or did not answer: " + (res["error"] or "unknown error"))

    with session() as db:
        if db.scalar(select(Panel).where(Panel.name == name)):
            raise ValueError("a panel with this name already exists")

        p = Panel(name=name, type=typ, url=c["url"], username=c["username"], secrets={"password": password}, verify_tls=c["verify_tls"],
                  status="up", status_since=int(time.time()), checked_at=int(time.time()), latency_ms=res["latency_ms"],
                  version=res["stats"].get("version", ""), stats=res["stats"], nodes=[dict(n, fails=0) for n in res["nodes"]])
        db.add(p)
        db.flush()
        emit(db, "info", "panels", f"Panel {name} ({typ}) added.", name)
        return public(p)


def update(panel_id: int, changes: dict) -> dict:
    with session() as db:
        p = db.get(Panel, panel_id)

        if p is None:
            raise ValueError("panel not found")

        if changes.get("url"):
            p.url = base_url(changes["url"])

        if changes.get("username"):
            p.username = str(changes["username"]).strip()[:64]

        if changes.get("password"):
            p.secrets = {"password": str(changes["password"])}

        if "verify_tls" in changes:
            p.verify_tls = bool(changes["verify_tls"])

        if "enabled" in changes:
            p.enabled = bool(changes["enabled"])

        if changes.get("name"):
            n = str(changes["name"]).strip()[:40]

            if not re.match(r"^[\w .\-]{1,40}$", n):
                raise ValueError("bad name")

            p.name = n

        _TOKENS.pop(p.id, None)
        return public(p)


def remove(panel_id: int) -> None:
    with session() as db:
        p = db.get(Panel, panel_id)

        if p is not None:
            emit(db, "info", "panels", f"Panel {p.name} removed from the list.", p.name)
            db.delete(p)

    _TOKENS.pop(panel_id, None)


def bootstrap() -> None:
    """First start: take the panels from /etc/nikatel/panels.json when the table is empty."""
    with session() as db:
        if db.scalar(select(Panel.id)):
            return

    f = Path(get_settings().bootstrap_dir) / "panels.json"

    if not f.exists():
        return

    try:
        items = json.loads(f.read_text())
    except (OSError, ValueError) as e:
        log.warning("could not read %s: %s", f, e)
        return

    with session() as db:
        for x in items if isinstance(items, list) else []:
            if not isinstance(x, dict) or x.get("type") not in TYPES or not x.get("url"):
                continue

            db.add(Panel(name=str(x.get("name") or x.get("id") or "panel")[:40], type=x["type"], url=str(x["url"]).rstrip("/"),
                         username=str(x.get("username", "")), secrets={"password": str(x.get("password", ""))}, verify_tls=bool(x.get("verify_tls", True))))

        log.info("panels taken from %s", f)


# ------------------------------------------------------------------ users
def users(c: dict, search: str = "", offset: int = 0, limit: int = 40, status: str = "", sort: str = "") -> dict:
    q = {"offset": max(0, int(offset)), "limit": min(100, max(1, int(limit)))}

    if search:
        q["search"] = str(search)[:64]

    if status in _STATUSES:
        q["status"] = status

    path = "/api/users?" + httpx.QueryParams(q).__str__()

    if sort in _SORTS:
        try:
            r = call(c, "GET", path + "&sort=" + sort)
        except PanelError:
            r = call(c, "GET", path)
    else:
        r = call(c, "GET", path)

    lst = r.get("users") if isinstance(r, dict) else r
    total = r.get("total") if isinstance(r, dict) else None
    rows = [user_row(u) for u in lst if isinstance(u, dict)] if isinstance(lst, list) else []
    return {"users": rows, "total": total if total is not None else len(rows)}


def _upath(username: str) -> str:
    if not _USERNAME.match(str(username or "")):
        raise ValueError("bad username")

    return "/api/user/" + httpx.QueryParams({"u": username})["u"]


def user_get(c: dict, username: str) -> dict:
    u = call(c, "GET", _upath(username)) or {}
    out = user_row(u)
    out.update({"lifetime": u.get("lifetime_used_traffic"), "reset": u.get("data_limit_reset_strategy") or "no_reset", "note": u.get("note") or "",
                "inbounds": u.get("inbounds") if isinstance(u.get("inbounds"), dict) else None,
                "group_ids": u.get("group_ids") if isinstance(u.get("group_ids"), list) else None, "options": {"inbounds": [], "groups": []}})

    try:
        ib = call(c, "GET", "/api/inbounds")

        if isinstance(ib, dict):
            for proto, lst in ib.items():
                for i in lst if isinstance(lst, list) else []:
                    out["options"]["inbounds"].append({"protocol": proto, "tag": i.get("tag"), "port": i.get("port")})
        elif isinstance(ib, list):
            for i in ib:
                out["options"]["inbounds"].append({"protocol": i.get("protocol", ""), "tag": i.get("tag"), "port": i.get("port")})
    except (PanelError, ValueError, AttributeError):
        pass

    if out["group_ids"] is not None:
        try:
            g = call(c, "GET", "/api/groups")
            g = g.get("groups") if isinstance(g, dict) else g
            out["options"]["groups"] = [{"id": x.get("id"), "name": x.get("name")} for x in g or [] if isinstance(x, dict)]
        except (PanelError, ValueError):
            pass

    return out


def options(c: dict) -> dict:
    """The inbounds (Marzban, and PasarGuard's tags) and the groups (PasarGuard) a panel offers: what a plan can hand to its accounts."""
    out: dict = {"inbounds": [], "groups": []}

    try:
        ib = call(c, "GET", "/api/inbounds")

        if isinstance(ib, dict):
            for proto, lst in ib.items():
                for i in lst if isinstance(lst, list) else []:
                    out["inbounds"].append({"protocol": proto, "tag": i.get("tag"), "port": i.get("port")})
        elif isinstance(ib, list):
            for i in ib:
                out["inbounds"].append({"protocol": i.get("protocol", ""), "tag": i.get("tag"), "port": i.get("port")})
    except (PanelError, ValueError, AttributeError):
        pass

    if c.get("type") == "pasarguard":
        try:
            g = call(c, "GET", "/api/groups")
            g = g.get("groups") if isinstance(g, dict) else g
            out["groups"] = [{"id": x.get("id"), "name": x.get("name")} for x in g or [] if isinstance(x, dict)]
        except (PanelError, ValueError):
            pass

    return out


def user_body(ch: dict, cur_expire, typ: str) -> dict:
    """The PUT body for the fields in `ch`: status, expire_date ('' = unlimited), limit_gb (0 = unlimited), reset, note, inbounds, group_ids."""
    body: dict = {}

    if "status" in ch:
        if ch["status"] not in ("active", "disabled", "on_hold"):
            raise ValueError("bad status")

        body["status"] = ch["status"]

    if "expire_date" in ch:
        d = str(ch["expire_date"] or "").strip()

        if not d:
            body["expire"] = None if typ == "pasarguard" else 0
        else:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                raise ValueError("the date must look like 2027-01-31")

            ts = calendar.timegm(time.strptime(d, "%Y-%m-%d")) + 86399
            body["expire"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if isinstance(cur_expire, str) else int(ts)

    if "limit_gb" in ch:
        gb = float(ch["limit_gb"])

        if not 0 <= gb <= 100000:
            raise ValueError("the limit must be between 0 and 100000 GB")

        body["data_limit"] = int(gb * 1024 ** 3)

    if "reset" in ch:
        if ch["reset"] not in _RESETS:
            raise ValueError("bad reset period")

        body["data_limit_reset_strategy"] = ch["reset"]

    if "note" in ch:
        body["note"] = str(ch["note"] or "")[:500]

    if isinstance(ch.get("inbounds"), dict):
        body["inbounds"] = {str(k)[:30]: [str(t)[:80] for t in v][:100] for k, v in ch["inbounds"].items() if isinstance(v, list)}

    if isinstance(ch.get("group_ids"), list):
        body["group_ids"] = [int(x) for x in ch["group_ids"]][:200]

    if not body:
        raise ValueError("nothing to change")

    return body


def proxies_for(cur_proxies: dict, inbounds: dict) -> dict:
    """Pure: the `proxies` Marzban needs beside new `inbounds`. A protocol that keeps inbounds keeps its CURRENT settings (its UUID / password must not change: the
    users' links would break); a protocol that gets inbounds for the first time gets empty settings (Marzban makes its id); a protocol with no inbound left is
    dropped (Marzban removes it). Marzban deletes every protocol that is missing from `proxies`, so all the kept ones must be listed."""
    cur_proxies = cur_proxies if isinstance(cur_proxies, dict) else {}
    return {p: (cur_proxies.get(p) if isinstance(cur_proxies.get(p), dict) else {}) for p, tags in inbounds.items() if tags}


def user_update(c: dict, username: str, ch: dict) -> dict:
    cur = call(c, "GET", _upath(username)) or {}
    body = user_body(ch, cur.get("expire"), c.get("type", ""))

    if "inbounds" in body and c.get("type", "") != "pasarguard":
        body["proxies"] = proxies_for(cur.get("proxies") or {}, body["inbounds"])

    call(c, "PUT", _upath(username), body=body)
    return user_get(c, username)


def user_action(c: dict, username: str, action: str, value=None) -> None:
    path = _upath(username)

    if action in ("enable", "disable"):
        call(c, "PUT", path, body={"status": "active" if action == "enable" else "disabled"})
    elif action == "reset":
        call(c, "POST", path + "/reset", body={})
    elif action == "renew":
        days = int(value)

        if not 1 <= days <= 3650:
            raise ValueError("days must be between 1 and 3650")

        cur = call(c, "GET", path) or {}
        call(c, "PUT", path, body={"expire": expire_plus(cur.get("expire"), days)})
    elif action == "revoke":
        call(c, "POST", path + "/revoke_sub", body={})
    elif action == "delete":
        call(c, "DELETE", path)
    elif action == "limit":
        gb = float(value)

        if not 0 <= gb <= 100000:
            raise ValueError("the limit must be between 0 and 100000 GB (0 = unlimited)")

        call(c, "PUT", path, body={"data_limit": int(gb * 1024 ** 3)})
    else:
        raise ValueError("unknown action")


_PROTOS = ("vless", "vmess", "trojan", "shadowsocks")


def create_body(typ: str, username: str, limit_gb, days, note: str, avail_inbounds: list[dict], avail_groups: list[dict], inbounds=None, group_ids=None, now: float | None = None) -> dict:
    """Pure: the POST body of a new user. Marzban: the protocols and inbounds (all of them unless `inbounds` {protocol: [tags]} says which); PasarGuard: the groups
    (the only one the panel has, else `group_ids` must say which). `days` 0 = no end; `limit_gb` 0 = unlimited."""
    name = str(username or "").strip()

    if not 3 <= len(name) <= 32 or not _USERNAME.match(name):
        raise ValueError("a username has 3 to 32 letters, digits or _ . @ -")

    gb = float(limit_gb or 0)

    if not 0 <= gb <= 100000:
        raise ValueError("the limit must be between 0 and 100000 GB (0 = unlimited)")

    days = int(days or 0)

    if not 0 <= days <= 3650:
        raise ValueError("days must be between 0 (no end) and 3650")

    end = int(now or time.time()) + days * 86400 if days else 0
    body: dict = {"username": name, "status": "active", "data_limit": int(gb * 1024 ** 3), "note": str(note or "")[:500]}

    if typ == "pasarguard":
        known = [g["id"] for g in avail_groups if g.get("id") is not None]
        ids = [int(x) for x in (group_ids or [])]

        if not ids:
            if len(known) != 1:
                raise ValueError("this panel has " + ("several groups: say which ones the user gets" if known else "no group"))

            ids = known

        for i in ids:
            if i not in known:
                raise ValueError(f"the panel has no group {i}")

        body["group_ids"] = ids
        body["expire"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end)) if end else None
        return body

    have: dict[str, list[str]] = {}

    for i in avail_inbounds:
        if i.get("protocol") in _PROTOS and i.get("tag"):
            have.setdefault(i["protocol"], []).append(i["tag"])

    pick = have

    if isinstance(inbounds, dict) and inbounds:
        pick = {}

        for proto, tags in inbounds.items():
            if proto not in have:
                raise ValueError(f"the panel has no {proto} inbound")

            for tg in tags:
                if tg not in have[proto]:
                    raise ValueError(f"the panel has no inbound {tg}")

            pick[proto] = [str(x) for x in tags]

    if not pick:
        raise ValueError("the panel has no inbound")

    body["proxies"] = {p: {} for p in pick}
    body["inbounds"] = pick
    body["expire"] = end
    return body


def user_create(c: dict, username: str, limit_gb=0, days=0, note: str = "", inbounds=None, group_ids=None) -> dict:
    """Make a new user on the panel; a name that is already taken there is refused (it may be another person's account)."""
    opts = options(c)
    body = create_body(c.get("type", ""), username, limit_gb, days, note, opts["inbounds"], opts["groups"], inbounds, group_ids)

    try:
        call(c, "GET", _upath(body["username"]))
        raise ValueError("this username already exists on the panel")
    except PanelError as e:
        if e.code != 404:
            raise

    return user_row(call(c, "POST", "/api/user", body=body) or {})


def users_bulk(c: dict, usernames: list, action: str, value=None) -> dict:
    done, failed = 0, []

    for n in [str(x) for x in (usernames or [])][:300]:
        try:
            user_action(c, n, action, value)
            done += 1
        except (PanelError, ValueError) as e:
            failed.append({"username": n, "error": str(e)[:80]})

    return {"done": done, "failed": failed[:20], "failed_count": len(failed)}


# ------------------------------------------------------------------ the panel's own test config on the probe
TEST_USER_PREFIX = "nikatel-test-"


def test_user(c: dict, panel_name: str, gb: int = 5, days: int = 30) -> dict:
    """A small test user on the panel (created once, renewed when expired); returns {username, sub}."""
    uname = (TEST_USER_PREFIX + re.sub(r"[^a-z0-9]+", "-", panel_name.lower()).strip("-"))[:32]
    expire = int(time.time()) + days * 86400
    u = None

    try:
        u = call(c, "GET", _upath(uname))
    except PanelError as e:
        if e.code != 404:
            raise

    if u is None:
        body: dict = {"username": uname, "status": "active", "data_limit": gb * 1024 ** 3, "data_limit_reset_strategy": "month", "note": "test config made by the Nikatel panel; it is kept alive and measured on the probe"}

        if c.get("type") == "pasarguard":
            g = call(c, "GET", "/api/groups")
            g = g.get("groups") if isinstance(g, dict) else g
            body["group_ids"] = [x["id"] for x in (g or []) if isinstance(x, dict) and x.get("id") is not None][:1]
            body["expire"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expire))
        else:
            ib = call(c, "GET", "/api/inbounds")
            body["proxies"] = {proto: {} for proto in (ib or {}) if proto in ("vless", "vmess", "trojan", "shadowsocks")} or {"vless": {}}
            body["inbounds"] = {proto: [i.get("tag") for i in lst if isinstance(i, dict) and i.get("tag")] for proto, lst in (ib or {}).items() if proto in body["proxies"]}
            body["expire"] = expire

        u = call(c, "POST", "/api/user", body=body) or {}
    else:
        exp = u.get("expire")
        expired = (isinstance(exp, (int, float)) and 0 < exp < time.time()) or (isinstance(exp, str) and exp[:10] < time.strftime("%Y-%m-%d"))

        if u.get("status") != "active" or expired:
            call(c, "PUT", _upath(uname), body={"status": "active", "expire": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expire)) if isinstance(exp, str) else expire})
            u = call(c, "GET", _upath(uname)) or u

    sub = str(u.get("subscription_url") or "")

    if sub.startswith("/"):
        sub = c["url"] + sub

    if not sub:
        raise PanelError("the panel gave no subscription link for the test user")

    return {"username": uname, "sub": sub}


def group_of_panel(base_url: str, name: str, subs: list[tuple[str, str]], groups: set[str]) -> str:
    """The Configs group that already holds this panel's subscription, whatever it is called: a subscription whose link starts with the panel's address, else a group named like the panel."""
    base = base_url.rstrip("/").lower()

    for gname, url in subs:
        if base and str(url).strip().lower().startswith(base):
            return gname

    return name if name in groups else ""


def test_config(panel_id: int) -> dict:
    """Look in Configs for this panel's subscription (it may be there under another name); when it is not, make the panel's test user and import its subscription on the probe as group <panel name>."""
    from . import configs

    with session() as db:
        p = db.get(Panel, panel_id)

        if p is None:
            raise ValueError("panel not found")

        c, name, had_test = _cred(p), p.name, bool((p.stats or {}).get("test_user"))
        subs = [(s.name, s.url) for s in db.scalars(select(Subscription))]
        groups = {g for g in db.scalars(select(Config.group)) if g}

    found = "" if had_test else group_of_panel(c["url"], name, subs, groups)

    if found:
        with session() as db:
            n = len(list(db.scalars(select(Config).where(Config.group == found))))
            emit(db, "info", "panels", f"Panel {name}: its subscription is already in Configs as group {found} ({n} configs); nothing added.", name)

        return {"username": "", "added": [], "skipped": [], "existing": found, "existing_count": n}

    tu = test_user(c, name)
    res = configs.import_subscription(name, tu["sub"], prefix=name)

    with session() as db:
        p = db.get(Panel, panel_id)

        if p is not None:
            st = dict(p.stats or {})
            st["test_user"] = tu["username"]
            st["test_config_at"] = int(time.time())
            p.stats = st

        emit(db, "info", "panels", f"Test config of panel {name}: {len(res.get('added', []))} added, {len(res.get('skipped', []))} already there.", name)

    return {"username": tu["username"], **res}


# ------------------------------------------------------------------ hosts
_HOST_TEXT = ("remark", "address", "sni", "host", "path", "alpn", "fingerprint")
_HOST_BOOL = ("allowinsecure", "mux_enable", "random_user_agent", "use_sni_as_host", "is_disabled")
_HOST_SECURITY = ("inbound_default", "none", "tls")


def _inbound_ports(c: dict) -> dict:
    ports = {}

    try:
        ib = call(c, "GET", "/api/inbounds")
        items = [i for lst in ib.values() for i in lst] if isinstance(ib, dict) else (ib or [])

        for i in items:
            if isinstance(i, dict) and i.get("tag"):
                ports[i["tag"]] = i.get("port")
    except (PanelError, ValueError, AttributeError):
        pass

    return ports


def hosts(c: dict) -> tuple[list[dict], object]:
    raw = call(c, "GET", "/api/hosts")
    return hosts_rows(raw, _inbound_ports(c)), raw


def _host_find(c: dict, key: str):
    raw = call(c, "GET", "/api/hosts")

    if isinstance(raw, dict):
        tag, idx = str(key).rsplit("#", 1)

        try:
            return raw, raw[tag][int(idx)], True
        except (KeyError, IndexError, ValueError):
            raise ValueError("host not found")

    for h in raw or []:
        if str(h.get("id")) == str(key):
            return raw, h, False

    raise ValueError("host not found")


def _host_put(c: dict, raw, h, marz: bool, key: str) -> None:
    if marz:
        call(c, "PUT", "/api/hosts", body=raw)
    else:
        call(c, "PUT", "/api/host/" + httpx.QueryParams({"k": str(key)})["k"], body=h)


def host_get(c: dict, key: str) -> dict:
    _raw, h, _m = _host_find(c, key)
    out = {"key": key, "tag": h.get("inbound_tag") or ""}

    for k in _HOST_TEXT + ("port", "security") + _HOST_BOOL:
        v = h.get(k)
        out[k] = ", ".join(str(x) for x in v) if isinstance(v, list) else v

    return out


def host_apply(h: dict, ch: dict) -> None:
    for k in _HOST_TEXT:
        if k in ch:
            v = str(ch[k] or "").strip()[:300]
            h[k] = [x.strip() for x in v.split(",") if x.strip()] if isinstance(h.get(k), list) else v

    if "port" in ch:
        v = str(ch["port"] if ch["port"] is not None else "").strip()

        if v and not (v.isdigit() and 1 <= int(v) <= 65535):
            raise ValueError("the port must be 1-65535 (empty = the port of the inbound)")

        h["port"] = int(v) if v else None

    if "security" in ch:
        if ch["security"] not in _HOST_SECURITY:
            raise ValueError("security must be inbound_default, none or tls")

        h["security"] = ch["security"]

    for k in _HOST_BOOL:
        if k in ch:
            h[k] = bool(ch[k])

    if not str(h.get("remark", "")).strip():
        raise ValueError("a host needs a remark (its name)")


def host_save(c: dict, key: str, ch: dict) -> dict:
    raw, h, marz = _host_find(c, key)
    host_apply(h, ch)
    _host_put(c, raw, h, marz, key)
    return host_get(c, key)


def host_toggle(c: dict, keys: list[str], disable: bool) -> int:
    n = 0

    for k in keys[:100]:
        raw, h, marz = _host_find(c, k)
        h["is_disabled"] = bool(disable)
        _host_put(c, raw, h, marz, k)
        n += 1

    return n


def host_clone(c: dict, key: str) -> None:
    raw, h, marz = _host_find(c, key)
    cp = json.loads(json.dumps(h))
    cp["remark"] = (str(cp.get("remark", "host")) + " copy")[:80]
    cp.pop("id", None)

    if marz:
        raw[str(key).rsplit("#", 1)[0]].append(cp)
        call(c, "PUT", "/api/hosts", body=raw)
    else:
        call(c, "POST", "/api/host", body=cp)


def host_delete(c: dict, key: str) -> None:
    raw, _h, marz = _host_find(c, key)

    if marz:
        tag, idx = str(key).rsplit("#", 1)
        del raw[tag][int(idx)]
        call(c, "PUT", "/api/hosts", body=raw)
    else:
        call(c, "DELETE", "/api/host/" + httpx.QueryParams({"k": str(key)})["k"])
