"""Profiles (the owner's idea, 2026-10-14: "instead of choosing the Iran and the Foreign tab, a profile - made first, from the very start - and the add-profile section has: Iran server, Foreign server,
PasarGuard node"). A profile is one thing the owner added to the Lab page by saying what it is: an Iran server, a foreign server or a PasarGuard node. A server profile points at a server of ours, a node
profile at a node of a PasarGuard panel (the panel is reached with its API key or a sign-in, services/panels.py), so nothing is copied and a profile changes nothing on a server or on a panel: it only says
"this one is on the Lab page". The list of profiles is one list, whatever the kinds; the page filters it (Any, Iran, Foreign, PasarGuard). web/lab.js is built on it.
Since 1.49.1 the old Foreign / Iran lists are gone and NOTHING puts the servers the panel already has on the page by itself (the owner: "I did not say to move the cards, only to bring the information of
the servers"): the page lists only what was added with Add profile. A server that is MADE there - one new server, several at once, the API, the importer - gets its profile at once (`ensure_server`); the
servers the panel already had are picked in the "Already in the panel" tab of Add profile (their name, address and provider are listed; "Select all" takes the lot). A profile that is taken off the page
only unlists the server - the panel still watches it, its tunnels go on - and the same tab brings it back."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Panel, Profile, Server
from .events import emit

KINDS = ("iran", "foreign", "node")
LABEL = {"iran": "Iran server", "foreign": "Foreign server", "node": "PasarGuard node"}
MAX_PROFILES = 400
DEFAULT_TAGS = ("Marzban", "PasarGuard", "Iran")      # the categories the owner asked for (2026-10-14): they are on the page from the start; any other tag he types is made by using it
NODE_TAG = "PasarGuard"      # the tag of the profiles that are made by themselves for the nodes of a connected PasarGuard panel


def _int(v, what: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None


def check_kind(v) -> str:
    """Pure: what is being added - iran, foreign or node."""
    k = str(v or "").strip().lower()

    if k not in KINDS:
        raise ValueError("choose what you are adding: an Iran server, a foreign server or a PasarGuard node")

    return k


def check_tag(v) -> str:
    """Pure: the tag of a profile - free text of up to 40 letters, digits, spaces and - _ . ; empty or "any" is no tag (Any)."""
    t = re.sub(r"\s+", " ", str(v or "").strip())

    if t.lower() == "any":
        return ""

    if len(t) > 40 or (t and not re.match(r"^[\w .\-]+$", t)):
        raise ValueError("a tag is up to 40 letters, digits, spaces and - _ .")

    return t


def all_tags(db: Session) -> list[str]:
    """The categories of the page: the three that are always there, then every other tag a profile carries (a tag is written once, whatever its case)."""
    out = list(DEFAULT_TAGS)
    seen = {t.lower() for t in out}

    for t in db.scalars(select(Profile.tag).distinct().order_by(Profile.tag)):
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)

    return out


def node_in(panel: Panel, node_id: int) -> dict | None:
    """Pure: the node of the panel (as the health job last read it) with this id, or None."""
    return next((n for n in (panel.nodes or []) if isinstance(n, dict) and n.get("id") == node_id), None)


def row(p: Profile, servers: dict[int, Server], panels: dict[int, Panel]) -> dict:
    """Pure: one profile for the list - what it is, and what it points at (its name and address; `missing` when that is gone)."""
    out = {"id": p.id, "kind": p.kind, "tag": p.tag or "", "label": LABEL.get(p.kind, p.kind), "server_id": p.server_id, "panel_id": p.panel_id, "node_id": p.node_id, "created_at": p.created_at}

    if p.kind == "node":
        pn = panels.get(p.panel_id or 0)
        n = node_in(pn, p.node_id) if pn is not None and p.node_id is not None else None
        out.update(name=(n or {}).get("name") or f"node {p.node_id}", address=(n or {}).get("address", ""), panel=pn.name if pn is not None else "", status=(n or {}).get("status", ""), missing=n is None)
    else:
        s = servers.get(p.server_id or 0)
        out.update(name=s.name if s is not None else f"server {p.server_id}", address=s.host if s is not None else "", panel="", status="", missing=s is None)

    return out


def list_all(db: Session) -> list[dict]:
    """Every profile, oldest first, with the name and address of what it points at."""
    servers = {s.id: s for s in db.scalars(select(Server))}
    panels = {p.id: p for p in db.scalars(select(Panel))}
    return [row(p, servers, panels) for p in db.scalars(select(Profile).order_by(Profile.id))]


def get_row(db: Session, pid: int) -> dict:
    r = next((r for r in list_all(db) if r["id"] == int(pid)), None)

    if r is None:
        raise LookupError("profile not found")

    return r


def _count(db: Session) -> int:
    return len(db.scalars(select(Profile.id)).all())


def create(db: Session, kind: str, server_id=None, panel_id=None, node_id=None, tag: str = "") -> Profile:
    """One profile. iran / foreign: an existing server of that kind (a server is on the page once). node: a node of a PasarGuard panel that is connected (a node is on the page once)."""
    kind = check_kind(kind)
    tag = check_tag(tag)

    if _count(db) >= MAX_PROFILES:
        raise ValueError(f"at most {MAX_PROFILES} profiles")

    if kind == "node":
        pid, nid = _int(panel_id, "the panel"), _int(node_id, "the node")
        panel = db.get(Panel, pid)

        if panel is None:
            raise LookupError("panel not found")

        if panel.type != "pasarguard":
            raise ValueError("only the nodes of a PasarGuard panel can be added")

        n = node_in(panel, nid)

        if n is None:
            raise LookupError("this panel has no node with that number (the panel is read every 30 seconds; a node that was just made shows then)")

        if db.scalar(select(Profile.id).where(Profile.kind == "node", Profile.panel_id == pid, Profile.node_id == nid)) is not None:
            raise FileExistsError(f"the node {n.get('name') or nid} is on the page already")

        p = Profile(kind="node", panel_id=pid, node_id=nid, tag=tag)
        what = f"PasarGuard node {n.get('name') or nid} ({panel.name})"
    else:
        sid = _int(server_id, "the server")
        s = db.get(Server, sid)

        if s is None:
            raise LookupError("server not found")

        if s.role != kind:
            raise ValueError(f"{s.name} is {'an Iran' if s.role == 'iran' else 'a foreign'} server: add it as {'an Iran' if s.role == 'iran' else 'a foreign'} server")

        if db.scalar(select(Profile.id).where(Profile.kind == kind, Profile.server_id == sid)) is not None:
            raise FileExistsError(f"{s.name} is on the page already")

        p = Profile(kind=kind, server_id=sid, tag=tag)
        what = f"{LABEL[kind]} {s.name}"

    db.add(p)
    db.flush()
    emit(db, "info", "servers", f"{what} added as a profile.", what)
    return p


def set_tag(db: Session, pid: int, tag) -> str:
    """Give a profile another tag (the category it is listed under). Returns the tag ("" = Any)."""
    p = db.get(Profile, int(pid))

    if p is None:
        raise LookupError("profile not found")

    p.tag = check_tag(tag)
    return p.tag


def auto_nodes(db: Session, panel: Panel) -> int:
    """A PasarGuard panel was connected (the owner: "a profile is added by itself on the Lab page, tagged PasarGuard"): every node it has, that is not on the page yet, gets a profile with the tag PasarGuard. Returns how many."""
    if panel.type != "pasarguard":
        return 0

    made = 0

    for n in panel.nodes or []:
        if isinstance(n, dict) and isinstance(n.get("id"), int) and db.scalar(select(Profile.id).where(Profile.kind == "node", Profile.panel_id == panel.id, Profile.node_id == n["id"])) is None:
            db.add(Profile(kind="node", panel_id=panel.id, node_id=n["id"], tag=NODE_TAG))
            made += 1

    return made


def auto_all(db: Session) -> int:
    """The one-time step of the upgrade to 1.50.0: the nodes of the PasarGuard panels that are connected already."""
    return sum(auto_nodes(db, p) for p in db.scalars(select(Panel)))


def ensure_server(db: Session, server: Server, tag: str = "") -> bool:
    """A server that is MADE - by the Add profile flow, the API, the importer, several at once - is on the Lab page at once: it gets the profile of its kind. The servers the panel already had are not touched
    (they are picked in Add profile). True when the profile was made now, False when the server had one already."""
    if db.scalar(select(Profile.id).where(Profile.server_id == server.id)) is not None:
        return False

    db.add(Profile(kind=server.role if server.role in ("iran", "foreign") else "foreign", server_id=server.id, tag=check_tag(tag)))
    return True


def follow_role(db: Session, server: Server) -> None:
    """A server that was said to be in Iran (or abroad) after all: its profile is of that kind now."""
    if server.role not in ("iran", "foreign"):
        return

    for p in db.scalars(select(Profile).where(Profile.server_id == server.id)):
        p.kind = server.role


def delete(db: Session, pid: int) -> str:
    """Take a profile off the page. What it pointed at is not touched. Returns its name."""
    p = db.get(Profile, int(pid))

    if p is None:
        raise LookupError("profile not found")

    name = row(p, {s.id: s for s in db.scalars(select(Server))}, {x.id: x for x in db.scalars(select(Panel))})["name"]
    emit(db, "info", "servers", f"Profile {name} taken off the Lab page (nothing was changed on it).", name)
    db.delete(p)
    return name


def drop_server(db: Session, server_id: int) -> int:
    """A server was deleted: its profile goes with it. Returns how many."""
    gone = list(db.scalars(select(Profile).where(Profile.server_id == server_id)))

    for p in gone:
        db.delete(p)

    return len(gone)


def drop_panel(db: Session, panel_id: int) -> int:
    """A panel was removed from the list: the profiles of its nodes go with it. Returns how many."""
    gone = list(db.scalars(select(Profile).where(Profile.panel_id == panel_id)))

    for p in gone:
        db.delete(p)

    return len(gone)
