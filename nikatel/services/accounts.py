"""One account over many panels (roadmap #81): the users of every Marzban / PasarGuard panel, asked through their REST API and put side by side.

An account is the same person on several panels. Two ways a user joins an account: the SAME USERNAME on two panels (found by itself, case-insensitive),
or a link the operator made by hand (different usernames, one person). Nothing is copied out of the panels: a search asks each panel at that moment,
and only the hand-made links are kept here (settings key "accounts")."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Panel
from . import panels
from . import settings as st

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
