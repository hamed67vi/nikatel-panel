"""One account over many panels (roadmap #81): the users of every Marzban / PasarGuard panel, asked through their REST API and put side by side.

An account is the same person on several panels. Two ways a user joins an account: the SAME USERNAME on two panels (found by itself, case-insensitive),
or a link the operator made by hand (different usernames, one person). Nothing is copied out of the panels: a search asks each panel at that moment,
and only the hand-made links are kept here (settings key "accounts")."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Panel
from . import panels
from . import settings as st
from .events import emit

log = get_logger("accounts")

PER_PANEL = 40          # users read from each panel for one search (the newest first when there is no search)
WORKERS = 6


# ------------------------------------------------------------------ pure
def key_of(username: str) -> str:
    return str(username or "").strip().lower()


def link_index(links: list[dict]) -> dict[tuple[int, str], dict]:
    """(panel id, lower-case username) -> the hand-made link it belongs to."""
    out = {}

    for ln in links:
        for m in ln.get("members") or []:
            out[(int(m["panel_id"]), key_of(m["username"]))] = ln

    return out


def group(rows: list[dict], links: list[dict]) -> list[dict]:
    """Pure: rows of users, each {panel_id, panel, username, ...}, into accounts. A hand-made link groups its members; every other user groups with the
    users of the same name on the other panels. An account lists at most one user per panel (the first one is kept: a panel never has two of one name)."""
    idx = link_index(links)
    accounts: dict[str, dict] = {}

    for r in rows:
        ln = idx.get((int(r["panel_id"]), key_of(r["username"])))
        k = "link:" + ln["id"] if ln else "name:" + key_of(r["username"])
        a = accounts.setdefault(k, {"key": k, "name": ln["name"] if ln else r["username"], "link": ln["id"] if ln else None, "members": []})

        if not any(m["panel_id"] == r["panel_id"] for m in a["members"]):
            a["members"].append(r)

    out = []

    for a in accounts.values():
        a["members"].sort(key=lambda m: m["panel"].lower())
        a["used"] = sum(int(m.get("used") or 0) for m in a["members"])
        a["limit"] = sum(int(m.get("limit") or 0) for m in a["members"]) if all(m.get("limit") for m in a["members"]) else 0       # 0 = at least one panel has no limit
        dates = [m["expire_date"] for m in a["members"] if m.get("expire_date")]
        a["expire_date"] = min(dates) if dates else ""             # the earliest end: the date by which something stops working
        a["unlimited"] = any(not m.get("expire_date") for m in a["members"])
        a["panels"] = len(a["members"])
        out.append(a)

    out.sort(key=lambda a: (-a["panels"], key_of(a["name"])))        # the accounts that exist on more than one panel first
    return out


def _check_members(members: list[dict]) -> list[dict]:
    seen, out = set(), []

    for m in members or []:
        pid, name = int(m.get("panel_id") or 0), str(m.get("username") or "").strip()

        if not pid or not name:
            raise ValueError("every member needs a panel and a username")

        if pid in seen:
            raise ValueError("an account has one user per panel: two members are on the same panel")

        seen.add(pid)
        out.append({"panel_id": pid, "username": name})

    if len(out) < 2:
        raise ValueError("a link joins at least two users on two different panels")

    return out


# ------------------------------------------------------------------ the hand-made links
def links() -> list[dict]:
    return list(st.get("accounts").get("links") or [])


def _save(lst: list[dict]) -> None:
    st.put("accounts", {"links": lst})


def make_link(members: list[dict], name: str = "") -> dict:
    """Join users with different names into one account. Each member must exist on its panel and belong to no other link."""
    members = _check_members(members)
    cur = links()
    idx = link_index(cur)

    with session() as db:
        by_id = {p.id: p for p in db.scalars(select(Panel))}

    for m in members:
        p = by_id.get(m["panel_id"])

        if p is None:
            raise ValueError("panel not found")

        if (m["panel_id"], key_of(m["username"])) in idx:
            raise ValueError(f"{m['username']} on {p.name} already belongs to a link: unlink it first")

        found = _find(panels.cred_for(p.id), m["username"])

        if found is None:
            raise ValueError(f"{m['username']} does not exist on {p.name}")

        m["username"] = found["username"]                         # the spelling the panel has

    ln = {"id": f"l{int(time.time() * 1000)}", "name": (str(name).strip()[:60] or members[0]["username"]), "members": members, "at": int(time.time())}
    _save(cur + [ln])
    return ln


def drop_link(link_id: str) -> None:
    cur = links()

    if not any(x["id"] == link_id for x in cur):
        raise ValueError("this link does not exist")

    _save([x for x in cur if x["id"] != link_id])


# ------------------------------------------------------------------ reading the panels
def _find(c: dict, username: str) -> dict | None:
    """One user of one panel by exact (case-insensitive) name, from the panel's search."""
    r = panels.users(c, search=username, offset=0, limit=10)
    return next((u for u in r["users"] if key_of(u["username"]) == key_of(username)), None)


def _row(p: dict, u: dict) -> dict:
    return {"panel_id": p["id"], "panel": p["name"], "type": p["type"], **{k: u.get(k) for k in ("username", "status", "used", "limit", "expire", "expire_date", "online_at", "created_at", "sub", "note")}}


def _ask(p: dict, search: str, limit: int) -> tuple[dict, list[dict], str]:
    try:
        r = panels.users(p["cred"], search=search, offset=0, limit=limit, sort="-created_at")
        return p, [_row(p, u) for u in r["users"]], str(r.get("total") if r.get("total") is not None else len(r["users"]))
    except (panels.PanelError, ValueError) as e:
        return p, [], "!" + str(e)[:120]


def _fetch(p: dict, username: str) -> dict | None:
    try:
        u = _find(p["cred"], username)
        return _row(p, u) if u else None
    except (panels.PanelError, ValueError):
        return None


def search(q: str = "", limit: int = PER_PANEL) -> dict:
    """The accounts that match `q` on any panel (no `q`: the newest users of every panel), with their users on the other panels filled in."""
    q = str(q or "").strip()[:64]
    limit = max(1, min(100, int(limit or PER_PANEL)))

    with session() as db:
        plist = [{"id": p.id, "name": p.name, "type": p.type, "up": p.status != "down", "cred": panels._cred(p)} for p in db.scalars(select(Panel).where(Panel.enabled.is_(True)).order_by(Panel.name))]

    live = [p for p in plist if p["up"]]
    rows, info = [], [{"id": p["id"], "name": p["name"], "type": p["type"], "ok": p["up"], "error": "" if p["up"] else "the panel is down", "total": None, "shown": 0} for p in plist]
    byid = {i["id"]: i for i in info}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for p, got, total in ex.map(lambda p: _ask(p, q, limit), live):
            i = byid[p["id"]]

            if total.startswith("!"):
                i["ok"], i["error"] = False, total[1:]
                continue

            i["total"], i["shown"] = int(total), len(got)
            rows += got

        # a hand-made link has members with other names: ask for the ones that did not come back (also for a link whose name matches the search)
        lks, have = links(), {(r["panel_id"], key_of(r["username"])) for r in rows}
        want = []

        for ln in lks:
            hit = any((m["panel_id"], key_of(m["username"])) in have for m in ln["members"]) or (q and q.lower() in ln["name"].lower())

            if hit:
                want += [(m, ln) for m in ln["members"] if (m["panel_id"], key_of(m["username"])) not in have]

        pmap = {p["id"]: p for p in live}
        jobs = [(pmap[m["panel_id"]], m["username"]) for m, _ in want if m["panel_id"] in pmap]

        for r in ex.map(lambda a: _fetch(*a), jobs):
            if r:
                rows.append(r)

    accounts = group(rows, lks)

    if q:
        accounts = [a for a in accounts if q.lower() in a["name"].lower() or any(q.lower() in m["username"].lower() for m in a["members"])]

    return {"accounts": accounts, "panels": info, "query": q, "per_panel": limit}


# ------------------------------------------------------------------ one action on every panel of an account (roadmap #82)
ACTIONS = ("enable", "disable", "reset", "revoke", "renew", "limit", "delete")


def clean_members(members: list[dict], minimum: int = 1) -> list[dict]:
    """Pure: the members of an account as (panel id, username), one per panel."""
    out, seen = [], set()

    for m in members or []:
        pid, name = int(m.get("panel_id") or 0), str(m.get("username") or "").strip()

        if not pid or not name:
            raise ValueError("every member needs a panel and a username")

        if pid in seen:
            raise ValueError("an account has one user per panel: two members are on the same panel")

        seen.add(pid)
        out.append({"panel_id": pid, "username": name})

    if len(out) < minimum:
        raise ValueError("choose at least one user")

    return out


def _do(m: dict, action: str, value) -> dict:
    try:
        c = panels.cred_for(m["panel_id"])
        panels.user_action(c, m["username"], action, value)
        return {**m, "ok": True, "error": ""}
    except (panels.PanelError, ValueError) as e:
        return {**m, "ok": False, "error": str(e)[:120]}


def act(members: list[dict], action: str, value=None) -> dict:
    """The same action (enable, disable, reset the usage, revoke the link, add days, set the limit, delete) on the user of every panel the account is on. Every panel is tried;
    the answer says which did it and which did not (a panel that is down does not stop the others)."""
    if action not in ACTIONS:
        raise ValueError("unknown action")

    members = clean_members(members)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        res = list(ex.map(lambda m: _do(m, action, value), members))

    names = {p["id"]: p["name"] for p in _panel_names()}

    for r in res:
        r["panel"] = names.get(r["panel_id"], str(r["panel_id"]))

    with session() as db:
        emit(db, "info" if all(r["ok"] for r in res) else "warn", "accounts", f"{action} on {len(res)} panel(s) for {res[0]['username']}: {sum(1 for r in res if r['ok'])} done.", res[0]["username"])

    return {"results": res, "done": sum(1 for r in res if r["ok"]), "failed": sum(1 for r in res if not r["ok"])}


def clean_targets(targets: list[dict]) -> list[dict]:
    """Pure: the panels a new account goes to - at least one, each once, with the optional inbounds / groups for that panel."""
    out, seen = [], set()

    for t in targets or []:
        pid = int(t.get("panel_id") or 0)

        if not pid:
            raise ValueError("every target needs a panel")

        if pid in seen:
            raise ValueError("a panel is listed twice")

        seen.add(pid)
        out.append({"panel_id": pid, "inbounds": t.get("inbounds") or None, "group_ids": t.get("group_ids") or None})

    if not out:
        raise ValueError("choose at least one panel")

    return out


def _make(t: dict, username: str, limit_gb, days, note: str) -> dict:
    try:
        c = panels.cred_for(t["panel_id"])
        u = panels.user_create(c, username, limit_gb, days, note, t["inbounds"], t["group_ids"])
        return {"panel_id": t["panel_id"], "ok": True, "error": "", "sub": u.get("sub", "")}
    except (panels.PanelError, ValueError) as e:
        return {"panel_id": t["panel_id"], "ok": False, "error": str(e)[:160], "sub": ""}


def create(username: str, targets: list[dict], limit_gb=0, days=0, note: str = "") -> dict:
    """The same new user on every chosen panel, with the same limit and end. Every panel is tried; a panel that refuses (the name is taken, no group chosen, down)
    does not stop the others, and the answer says which did it. The users share one name, so they are one account; `token` is the merged subscription link of the ones made."""
    targets = clean_targets(targets)
    name = str(username or "").strip()

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        res = list(ex.map(lambda t: _make(t, name, limit_gb, days, note), targets))

    names = {p["id"]: p["name"] for p in _panel_names()}

    for r in res:
        r["panel"] = names.get(r["panel_id"], str(r["panel_id"]))

    made = [r["panel_id"] for r in res if r["ok"]]

    with session() as db:
        emit(db, "info" if len(made) == len(res) else "warn", "accounts", f"new user {name} on {len(made)} of {len(res)} panel(s).", name)

    token = make_token([{"panel_id": pid, "username": name} for pid in made]) if made else ""
    return {"username": name, "results": res, "done": len(made), "failed": len(res) - len(made), "token": token, "path": "/sub/" + token if token else ""}


def _panel_names() -> list[dict]:
    with session() as db:
        return [{"id": p.id, "name": p.name} for p in db.scalars(select(Panel))]


# ------------------------------------------------------------------ one subscription link for an account (roadmap #83)
def _secret() -> str:
    s = st.get("accounts").get("sub_secret")

    if not s:
        s = secrets.token_urlsafe(32)
        st.put("accounts", {"sub_secret": s})

    return s


def reset_secret() -> None:
    """Every link made so far stops working (the old ones are signed with the old secret)."""
    st.put("accounts", {"sub_secret": secrets.token_urlsafe(32)})


def make_token(members: list[dict]) -> str:
    """A link token that carries its members and is signed: no table, nothing to look up, nothing to leak but what the link already gives."""
    ms = clean_members(members)

    if len(ms) > 12:
        raise ValueError("at most 12 panels in one link")

    body = base64.urlsafe_b64encode(json.dumps([[m["panel_id"], m["username"]] for m in ms], separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_token(token: str) -> list[dict] | None:
    """The members of a link token, or None when it is not one of ours (wrong signature, damaged, or made before the last reset)."""
    body, _, sig = str(token or "").partition(".")

    if not body or not sig or len(token) > 2000:
        return None

    good = hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()[:32]

    if not hmac.compare_digest(sig, good):
        return None

    try:
        raw = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return clean_members([{"panel_id": a, "username": b} for a, b in raw])
    except (ValueError, TypeError, KeyError):
        return None


def sub_links(text: str) -> list[str]:
    """Pure: the share links in a subscription body (base64 of one link per line, or the lines themselves)."""
    raw = str(text or "").strip()
    cand = raw

    if raw and "://" not in raw.split("\n")[0]:
        try:
            cand = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=False).decode("utf-8", "replace")
        except ValueError:
            cand = raw

    return [x.strip() for x in cand.replace("\r", "").split("\n") if re.match(r"^[a-z][a-z0-9+.\-]*://", x.strip(), re.IGNORECASE)]


def merge_links(parts: list[str]) -> str:
    """Pure: the links of several subscriptions in one list, each once, in the order of the panels - as the base64 text every client reads."""
    seen, out = set(), []

    for part in parts:
        for ln in sub_links(part):
            if ln not in seen:
                seen.add(ln)
                out.append(ln)

    return base64.b64encode("\n".join(out).encode()).decode()


def sum_userinfo(infos: list[str]) -> str:
    """Pure: the `subscription-userinfo` header of the whole account: upload and download added, the total added (0 = unlimited as soon as one panel has no limit), the earliest end."""
    up = down = total = 0
    expire, unlimited = 0, False

    for info in infos:
        kv = {k.strip(): v.strip() for k, v in (x.split("=", 1) for x in str(info or "").split(";") if "=" in x)}

        try:
            up += int(kv.get("upload", 0) or 0)
            down += int(kv.get("download", 0) or 0)
            t = int(kv.get("total", 0) or 0)
            e = int(kv.get("expire", 0) or 0)
        except ValueError:
            continue

        unlimited = unlimited or t == 0
        total += t
        expire = e if e and (not expire or e < expire) else expire

    return f"upload={up}; download={down}; total={0 if unlimited else total}; expire={expire}"


SUB_UA = "v2rayN/6.0"          # the panels answer this with the plain list of links (base64): the one format that can be merged
SUB_TTL = 60                    # a client that asks again within a minute gets the same answer without the panels being asked again
_SUB_CACHE: dict[str, tuple[float, tuple[str, str, int]]] = {}


def _fetch_sub(m: dict) -> tuple[str, str] | None:
    """One user's own subscription from its panel: (body, userinfo header), or None when the panel does not answer."""
    try:
        with session() as db:
            p = db.get(Panel, m["panel_id"])

            if p is None or not p.enabled:
                return None

            c = panels._cred(p)

        u = _find(c, m["username"])

        if u is None or not u.get("sub"):
            return None

        url = u["sub"] if u["sub"].startswith("http") else c["url"].rstrip("/") + u["sub"]
        r = httpx.get(url, headers={"User-Agent": SUB_UA}, timeout=15, verify=bool(c.get("verify_tls", True)), follow_redirects=True)
        return (r.text, r.headers.get("subscription-userinfo", "")) if r.status_code == 200 else None
    except (httpx.HTTPError, panels.PanelError, ValueError):
        return None


def build_sub(members: list[dict]) -> tuple[str, str, int]:
    """The merged subscription of an account: (base64 body, userinfo header, how many panels answered)."""
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        got = [g for g in ex.map(_fetch_sub, members) if g]

    return merge_links([g[0] for g in got]), sum_userinfo([g[1] for g in got]), len(got)

def serve_sub(token: str) -> tuple[str, str, int, int] | None:
    """What /sub/<token> answers: (body, userinfo, panels that answered, panels in the link), or None when the token is not ours. Cached for a minute."""
    members = read_token(token)

    if members is None:
        return None

    now = time.time()
    hit = _SUB_CACHE.get(token)

    if hit and now - hit[0] < SUB_TTL:
        return (*hit[1], len(members))

    res = build_sub(members)

    if res[2]:                          # an answer with no panel is not kept: the next ask tries again
        if len(_SUB_CACHE) > 500:
            _SUB_CACHE.clear()

        _SUB_CACHE[token] = (now, res)

    return (*res, len(members))
