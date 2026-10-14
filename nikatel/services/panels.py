"""Marzban / PasarGuard panels: the connection (an API key or token), health + nodes every 2 minutes, users and hosts on demand.

Both panels share the Marzban API shape (`/api/admin/token`, `/api/system`, `/api/nodes`, `/api/users`,
`/api/hosts`); where PasarGuard differs (hosts as a list with ids, expire as a date string, cores as a
list) the functions look at the shape of the answer, not at the declared type.

How a panel is connected (docs/panels.md): the panel is given a credential, not a password. PasarGuard has API keys (`pg_key_...`,
header X-Api-Key: they have their own end date and can be revoked one by one). Marzban and PasarGuard both accept the admin's access
token (a JWT that a sign-in produces, header Authorization: Bearer; it ends when the panel's JWT lifetime says). "Sign in once" gets
a token with the admin's username and password and forgets the password; a password is kept only when the owner asks for it (to renew
a token that ends)."""
from __future__ import annotations

import base64
import calendar
import datetime
import json
import re
import time
import uuid
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
TOKEN_SHORT_DAYS = 30        # a token that ends sooner is no way to stay connected without a login that renews it
KEY_META_EVERY = 12 * 3600   # how often the panel is asked about its API key (end date, still active)
_JWT = re.compile(r"^[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]*$")
_ALERTED: dict[int, str] = {}      # panel id -> the level of the warning about the key / token the owner was last given (forgotten when it is fine)


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


# ------------------------------------------------------------------ the API key / token (pure)
def is_api_key(s: str) -> bool:
    """A PasarGuard API key: 'pg_key_' and a version-4 UUID (the panel turns away anything else before it looks the key up)."""
    s = str(s or "")

    if not s.startswith("pg_key_"):
        return False

    try:
        return uuid.UUID(s[7:]).version == 4
    except ValueError:
        return False


def auth_headers(secret: str) -> dict:
    """An API key travels in X-Api-Key (PasarGuard); an access token is a Bearer token (both panels)."""
    return {"X-Api-Key": secret} if is_api_key(secret) else {"Authorization": "Bearer " + secret}


def _epoch(s) -> int | None:
    """An ISO date from a panel ('2027-10-22T01:13:50Z') -> unix seconds; None when empty or unreadable."""
    if not s:
        return None

    try:
        d = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None

    return int((d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)).timestamp())


def token_claims(token: str) -> dict | None:
    """What an access token says about itself (admin, rights, issued, ends), or None when it is not a JWT.

    The signature cannot be checked here (the secret stays on the panel): the claims only tell WHEN the token ends, which is what the
    owner needs to see. A token without `exp` never ends."""
    t = str(token or "").strip()

    if not _JWT.match(t):
        return None

    body = t.split(".")[1]

    try:
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:         # bad base64, bad UTF-8 and bad JSON are all ValueErrors
        return None

    return claims if isinstance(claims, dict) else None


def _secs(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def token_info(token: str, now: float | None = None) -> dict:
    """{"known", "admin", "access", "exp", "left_s", "life_s"}: exp / left_s / life_s are None when the token never ends (or is unreadable)."""
    now = time.time() if now is None else now
    cl = token_claims(token)
    exp, iat = _secs((cl or {}).get("exp")), _secs((cl or {}).get("iat"))
    return {"known": cl is not None, "admin": str((cl or {}).get("sub") or "")[:64], "access": str((cl or {}).get("access") or "")[:20],
            "exp": exp, "left_s": int(exp - now) if exp else None, "life_s": exp - iat if exp and iat and exp > iat else None}


def clean_token(token: str, now: float | None = None) -> str:
    """What was pasted (maybe in quotes, or after "Bearer ", "ApiKey " or "X-Api-Key:") -> the bare API key or access token; ValueError says why it cannot work."""
    t = str(token or "").strip().strip("\"'").strip()

    for lead in ("bearer ", "apikey ", "x-api-key:"):
        if t.lower().startswith(lead):
            t = t[len(lead):].strip()
            break

    if is_api_key(t):
        return t

    if token_claims(t) is None:
        raise ValueError("this is neither an API key (pg_key_…) nor an access token (three parts joined by dots, xxxx.yyyy.zzzz)")

    left = token_info(t, now)["left_s"]

    if left is not None and left <= 60:
        raise ValueError("this token has already ended — get a fresh one")

    return t


def span_text(seconds: float) -> str:
    """'35 min' / '23 h' / '5 days' for a length of time (the sign is ignored)."""
    s = abs(int(seconds))

    if s < 3600:
        return f"{max(1, s // 60)} min"

    if s < 86400:
        return f"{s // 3600} h"

    d = s // 86400
    return "1 day" if d == 1 else f"{d} days"


def auth_of(token: str, username: str, has_login: bool, meta: dict | None = None, now: float | None = None) -> dict:
    """How a panel is connected, for the page: what is kept, who the admin is, when it ends and whether that needs attention.

    kind is api_key | token | login | none, level is ok | warn | bad. `meta` is what the panel said about its API key (name, end, status):
    a key has no claims to read. A saved login renews a connection that ends, so only a key or token WITHOUT a login can run out."""
    now = time.time() if now is None else now
    meta = meta if isinstance(meta, dict) else {}
    key = is_api_key(token)
    info = token_info(token, now)
    kind = "api_key" if key else "token" if token else "login" if has_login else "none"

    if key:
        exp = _secs(meta.get("exp"))
        left, known = (int(exp - now) if exp else None), bool(meta.get("found"))
    else:
        exp, left, known = info["exp"], info["left_s"], info["known"]

    what = "API key" if key else "token"
    status = str(meta.get("status") or "active") if key else "active"

    if kind == "none":
        level, note = "bad", "no API key, no token and no saved login"
    elif status != "active":
        level, note = ("ok", f"the API key is {status}: the saved login is used") if has_login else ("bad", f"the API key is {status}")
    elif has_login:
        level, note = "ok", f"the saved login renews the {what}" if token else "signs in with the saved login"
    elif not known:
        level, note = "ok", f"the end of this {what} is not known"
    elif left is None:
        level, note = "ok", f"the {what} never ends"
    elif left <= 0:
        level, note = "bad", f"the {what} ended {span_text(left)} ago"
    elif left < 86400:
        level, note = "bad", f"the {what} ends in {span_text(left)}"
    elif left < 7 * 86400:
        level, note = "warn", f"the {what} ends in {span_text(left)}"
    else:
        level, note = "ok", f"the {what} is valid for {span_text(left)}"

    return {"kind": kind, "token": bool(token), "login": has_login, "admin": (str(meta.get("admin") or "") if key else info["admin"]) or username,
            "access": str(meta.get("role") or "") if key else info["access"], "exp": exp, "left_s": left, "life_s": info["life_s"],
            "short": kind == "token" and left is not None and left < TOKEN_SHORT_DAYS * 86400, "level": level, "note": note,
            "key_name": str(meta.get("name") or "")[:128] if key else "", "key_status": status if key else ""}


# ------------------------------------------------------------------ HTTP
class PanelError(RuntimeError):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


def _cred(p: Panel) -> dict:
    sec = p.secrets or {}
    tok = str(sec.get("token") or "")
    return {"id": p.id, "url": p.url, "username": p.username, "password": sec.get("password", ""), "token": tok,
            "key_meta": sec.get("key_meta") or {}, "verify_tls": p.verify_tls, "type": p.type,
            "api_key_trimmed": f"{tok[:10]}***{tok[-3:]}" if is_api_key(tok) else ""}      # the form the panel lists its keys in: the API keys tab marks (and protects) the key this panel is connected with


def _http(c: dict, method: str, path: str, token: str | None = None, form: dict | None = None, body=None, timeout: float = 20):
    headers = {"Accept": "application/json", "User-Agent": "nikatel-panel"}

    if token:
        headers.update(auth_headers(token))

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


def _login(c: dict) -> str:
    """One sign-in with the admin's username and password. The answer is an access token (the only credential Marzban has; PasarGuard also has API keys)."""
    r = _http(c, "POST", "/api/admin/token", form={"username": c.get("username", ""), "password": c.get("password", "")})
    t = (r or {}).get("access_token") if isinstance(r, dict) else None

    if not t:
        raise PanelError("login failed: no token returned")

    return str(t)


def _token(c: dict, fresh: bool = False) -> str:
    """The credential for a request: a token a saved login made (cached), else the API key / token the owner gave (while it lasts), else a sign-in with the saved login.

    `fresh` is the retry after the panel answered 401: nothing stored is tried again, only the saved login can still help."""
    now = time.time()
    cached = _TOKENS.get(c["id"])

    if cached and not fresh and cached[1] > now:
        return cached[0]

    own = str(c.get("token") or "")

    if own and not fresh:
        left = token_info(own, now)["left_s"]         # None for an API key: its end is the panel's business, a 401 tells

        if left is None or left > 60:
            return own

    if c.get("username") and c.get("password"):
        t = _login(c)

        if c["id"]:
            _TOKENS[c["id"]] = (t, now + 45 * 60)

        return t

    if own:
        raise PanelError(f"the panel does not accept the {'API key' if is_api_key(own) else 'token'} (it ended or was revoked) — paste a new one in Edit", 401)

    raise PanelError("no API key, no token and no saved login for this panel — paste an API key or token in Edit", 401)


def key_info(c: dict) -> dict:
    """What the panel says about the API key it is connected with: {"found", "name", "status", "exp", "inherit", "admin", "role", "asked"}.

    The key is found in the panel's list of keys by its trimmed form ("pg_key_a9d***dc4"); the admin it acts as comes from /api/admin.
    A key that may not read these (no 'api_keys: read' right) just stays unknown: the answer is {"asked": now} and nothing is guessed."""
    key = str(c.get("token") or "")
    meta: dict = {"asked": int(time.time())}

    if not is_api_key(key):
        return meta

    try:
        me = call(c, "GET", "/api/admin")
        role = (me or {}).get("role") if isinstance(me, dict) else None
        meta["admin"] = str((me or {}).get("username") or "")[:64] if isinstance(me, dict) else ""
        meta["role"] = str((role or {}).get("name") or ("owner" if (role or {}).get("is_owner") else ""))[:40] if isinstance(role, dict) else ""
    except (PanelError, ValueError):
        pass

    try:
        r = call(c, "GET", "/api/api_keys?limit=200")
    except (PanelError, ValueError):
        return meta

    for k in (r.get("api_keys") if isinstance(r, dict) else None) or []:
        t = str(k.get("api_key_trimmed") or "") if isinstance(k, dict) else ""

        if "***" in t and t.startswith(key[:10]) and t.endswith(key[-3:]):
            meta.update(found=True, id=k.get("id"), name=str(k.get("name") or "")[:128], status=str(k.get("status") or "active"),
                        exp=_epoch(k.get("expire_date")), inherit=bool(k.get("inherit_permissions", True)))
            break

    return meta


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
    if c.get("type") == "pasarguard":
        # PasarGuard answers with an endless server-sent-events stream: `call` would wait for a body that never ends. services/pg_nodes.py reads it for a few seconds.
        from . import pg_nodes

        return "\n".join(pg_nodes.node_logs(c, node_id, 120)["lines"])[-6000:]

    r = call(c, "GET", f"/api/node/{int(node_id)}/logs?lines=120")

    if isinstance(r, dict):
        r = r.get("logs") or r.get("log") or r.get("lines") or json.dumps(r)

    if isinstance(r, list):
        r = "\n".join(str(x) for x in r)

    return str(r or "")[-6000:]


def _auth(p: Panel) -> dict:
    sec = p.secrets or {}
    return auth_of(str(sec.get("token") or ""), p.username, bool(p.username and sec.get("password")), sec.get("key_meta"))


def _token_alert(db, p: Panel, label: str) -> None:
    """Tells the owner, once per level, that an API key or token kept WITHOUT a login is about to end (or ended): after that the panel stops answering."""
    a = _auth(p)
    lvl = a["level"] if a["kind"] in ("api_key", "token") and not a["login"] else "ok"

    if lvl == "ok":
        _ALERTED.pop(p.id, None)
    elif _ALERTED.get(p.id) != lvl:
        _ALERTED[p.id] = lvl
        emit(db, lvl, "panels", f"Panel {label}: {a['note']} — paste a new {'API key' if a['kind'] == 'api_key' else 'token'} in Panels → Edit.", p.name)


def refresh_key_meta(panel_id: int, c: dict) -> None:
    """Asks the panel about its API key (when does it end, is it still active) so the page can warn before the connection dies."""
    meta = key_info(c)

    with session() as db:
        p = db.get(Panel, panel_id)

        if p is not None and (p.secrets or {}).get("token") == c["token"]:          # not replaced while the panel was asked
            p.secrets = {**(p.secrets or {}), "key_meta": meta}


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
            _token_alert(db, p, label)

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

        if res["ok"] and is_api_key(c.get("token", "")) and now - int((c.get("key_meta") or {}).get("asked") or 0) > KEY_META_EVERY:
            refresh_key_meta(pid, c)

        log.info("panel %s ok=%s nodes=%s latency=%s %s", c["url"].split("//")[-1].split("/")[0].split(":")[0][:3] + "…", res["ok"], len(res["nodes"]), res["latency_ms"], res["error"])


# ------------------------------------------------------------------ CRUD
def public(p: Panel) -> dict:
    return {"id": p.id, "name": p.name, "type": p.type, "url": p.url, "username": p.username, "verify_tls": p.verify_tls, "enabled": p.enabled,
            "status": p.status, "status_since": p.status_since, "fails": p.fails, "checked_at": p.checked_at, "latency_ms": p.latency_ms,
            "version": p.version, "stats": p.stats or {}, "nodes": p.nodes or [], "last_error": p.last_error, "auth": _auth(p)}


def _credentials(c: dict, token: str, username: str, password: str, keep_login: bool) -> tuple[dict, dict]:
    """What the owner gave -> (the cred that is tried, the secrets that would be kept). ValueError when it is neither a key / token nor a login.

    An API key or token is used as it is (a key is also asked what it is: name, end, the admin it acts as). A username and password are
    used ONCE to get a token; the password is kept only when keep_login is true (the tried cred never holds it otherwise, so the check
    really tests the key / token)."""
    c = dict(c)

    if str(token or "").strip():
        t = clean_token(token)
        sec: dict = {"token": t}

        if is_api_key(t):
            if c.get("type") == "marzban":
                raise ValueError("Marzban has no API keys (PasarGuard has): paste an access token, or sign in once")

            c.update(token=t, password="")
            meta = key_info(c)
            c.update(key_meta=meta, username=meta.get("admin") or c.get("username", ""))
            sec["key_meta"] = meta
        else:
            c.update(token=t, password="", username=token_info(t)["admin"] or c.get("username", ""))

        return c, sec

    username, password = str(username or "").strip()[:64], str(password or "")

    if not username or not password:
        raise ValueError("paste the panel's API key or an access token, or sign in once with the admin's username and password")

    c.update(username=username, password=password, token="")

    try:
        t = _login(c)
    except PanelError as e:
        raise ValueError("the panel did not accept the sign-in: " + str(e)[:140])

    c.update(token=t, password=password if keep_login else "")
    return c, ({"token": t, "password": password} if keep_login else {"token": t})


def add(name: str, typ: str, url: str, *, token: str = "", username: str = "", password: str = "", verify_tls: bool = True, keep_login: bool = False) -> dict:
    """Adds a panel with an API key or access token. Or "sign in once": the username and password get a token and only the token is kept."""
    name = str(name or "").strip()[:40]
    typ = str(typ or "").strip().lower()

    if typ not in TYPES:
        raise ValueError("pick Marzban or PasarGuard")

    if not name or not re.match(r"^[\w .\-]{1,40}$", name):
        raise ValueError("give the panel a short name (letters, digits, - _ . and spaces)")

    base = {"id": 0, "url": base_url(url), "username": "", "password": "", "token": "", "verify_tls": bool(verify_tls), "type": typ}
    c, sec = _credentials(base, token, username, password, keep_login)
    res = check(c)

    if not res["ok"]:
        raise ValueError("the panel did not accept it or did not answer: " + (res["error"] or "unknown error"))

    with session() as db:
        if db.scalar(select(Panel).where(Panel.name == name)):
            raise ValueError("a panel with this name already exists")

        p = Panel(name=name, type=typ, url=c["url"], username=c["username"], secrets=sec, verify_tls=c["verify_tls"],
                  status="up", status_since=int(time.time()), checked_at=int(time.time()), latency_ms=res["latency_ms"],
                  version=res["stats"].get("version", ""), stats=res["stats"], nodes=[dict(n, fails=0) for n in res["nodes"]])
        db.add(p)
        db.flush()
        emit(db, "info", "panels", f"Panel {name} ({typ}) added with its {'API key' if is_api_key(sec['token']) else 'token'}" + (" and the login." if "password" in sec else "."), name)
        return public(p)


def update(panel_id: int, changes: dict) -> dict:
    """Name, address, TLS check, enabled; and the credentials: a new `token`, or a `password` (a login that is kept unless keep_login is false).

    New credentials are tried on the panel first; nothing is replaced when the panel does not accept them."""
    token, password = str(changes.get("token") or "").strip(), str(changes.get("password") or "")
    sec, admin = None, ""

    if token or password:
        c = cred_for(panel_id)
        keep = changes.get("keep_login")

        if changes.get("url"):
            c["url"] = base_url(changes["url"])

        if "verify_tls" in changes:
            c["verify_tls"] = bool(changes["verify_tls"])

        c, sec = _credentials(c, token, changes.get("username") or c.get("username", ""), password, True if keep is None else bool(keep))
        res = check(c)

        if not res["ok"]:
            raise ValueError("the panel did not accept it: " + (res["error"] or "unknown error"))

        admin = c["username"]

    with session() as db:
        p = db.get(Panel, panel_id)

        if p is None:
            raise ValueError("panel not found")

        if changes.get("url"):
            p.url = base_url(changes["url"])

        if changes.get("username"):
            p.username = str(changes["username"]).strip()[:64]

        if sec is not None:
            old = p.secrets or {}

            if token and old.get("password"):
                sec = {**sec, "password": old["password"]}      # a new token does not wipe the saved login: that is "forget the login"
            elif admin:
                p.username = admin

            p.secrets = sec

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
        _ALERTED.pop(p.id, None)
        return public(p)


def forget_login(panel_id: int, force: bool = False) -> dict:
    """Keeps only a token: signs in one last time with the saved login, keeps the token it gives and wipes the password.

    Refused when that token ends within TOKEN_SHORT_DAYS (the panel would stop answering) unless `force`."""
    c = cred_for(panel_id)

    if not (c["username"] and c["password"]):
        raise ValueError("no login is saved for this panel")

    tok = str(c.get("token") or "")
    left = token_info(tok)["left_s"] if tok else 0

    if not tok or (left is not None and left < TOKEN_SHORT_DAYS * 86400):
        try:
            tok = _login(c)          # the kept token is missing or short: the panel gives a fresh one
        except PanelError as e:
            raise ValueError("the saved login did not work: " + str(e)[:140])

    info = token_info(tok)

    if info["left_s"] is not None and info["left_s"] < TOKEN_SHORT_DAYS * 86400 and not force:
        fix = ("Make an API key on the panel (API Keys page) and paste it in Edit first" if c.get("type") == "pasarguard"
               else "Raise JWT_ACCESS_TOKEN_EXPIRE_MINUTES on the panel (0 = never) and paste a fresh token first")
        raise ValueError(f"The token this panel gives lasts only {span_text(info['left_s'])}: without the login the panel would stop answering after that. "
                         f"{fix} — or forget the login anyway and paste a new token yourself when it ends.")

    res = check(dict(c, token=tok, password=""))

    if not res["ok"]:
        raise ValueError("the token did not work: " + (res["error"] or "unknown error"))

    with session() as db:
        p = db.get(Panel, panel_id)

        if p is None:
            raise ValueError("panel not found")

        meta = c.get("key_meta") or {}
        p.secrets = {"token": tok, **({"key_meta": meta} if meta and tok == c.get("token") else {})}       # what the panel said about the key stays
        p.username = info["admin"] or (meta.get("admin") if tok == c.get("token") else "") or p.username
        emit(db, "info", "panels", f"Panel {p.name}: the saved login was forgotten, it is connected with its {'API key' if is_api_key(tok) else 'token'} only.", p.name)
        _TOKENS.pop(p.id, None)
        _ALERTED.pop(p.id, None)
        return public(p)


def remove(panel_id: int) -> None:
    with session() as db:
        p = db.get(Panel, panel_id)

        if p is not None:
            emit(db, "info", "panels", f"Panel {p.name} removed from the list.", p.name)
            db.delete(p)

    _TOKENS.pop(panel_id, None)
    _ALERTED.pop(panel_id, None)


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

            sec = {k: str(x[k]) for k in ("token", "password") if x.get(k)}

            if x.get("api_key") and "token" not in sec:
                sec["token"] = str(x["api_key"])         # panels.json may call the PasarGuard key api_key

            db.add(Panel(name=str(x.get("name") or x.get("id") or "panel")[:40], type=x["type"], url=str(x["url"]).rstrip("/"),
                         username=str(x.get("username", "")), secrets=sec, verify_tls=bool(x.get("verify_tls", True))))

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
