"""Profiles (the owner's idea, 2026-10-14: "instead of choosing the Iran and the Foreign tab, a profile - made first, from the very start - and the add-profile section has: Iran server, Foreign server,
PasarGuard node"). A profile is one thing the owner added to the Lab page by saying what it is: an Iran server, a foreign server or a PasarGuard node. A server profile points at a server of ours, a node
profile at a node of a PasarGuard panel (the panel is reached with its API key or a sign-in, services/panels.py), so nothing is copied and a profile changes nothing on a server or on a panel: it only says
"this one is on the Lab page". The list of profiles is one list, whatever the kinds; the page filters it (Any, Iran, Foreign, PasarGuard). web/lab.js is built on it.
Since 1.49.1 the old Foreign / Iran lists are gone and NOTHING puts the servers the panel already has on the page by itself (the owner: "I did not say to move the cards, only to bring the information of
the servers"): the page lists only what was added with Add profile. A server that is MADE there - one new server, several at once, the API, the importer - gets its profile at once (`ensure_server`); the
servers the panel already had are picked in the "Already in the panel" tab of Add profile (their name, address and provider are listed; "Select all" takes the lot). A profile that is taken off the page
only unlists the server - the panel still watches it, its tunnels go on - and the same tab brings it back."""
from __future__ import annotations

import ipaddress
import re
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Panel, Profile, Server, Setting
from . import settings
from .events import emit

KINDS = ("iran", "foreign", "node")
LABEL = {"iran": "Iran server", "foreign": "Foreign server", "node": "PasarGuard node"}
MAX_PROFILES = 400
DEFAULT_TAGS: tuple[str, ...] = ()      # no category from the start (the owner, 2026-10-15: "delete all, only Add profile stays: the user makes a profile first"); a tag is made by adding a profile with it, or with the + beside the tabs
NODE_TAG = "PasarGuard Nodes"      # (the owner: "PasarGuard Nodes") the tag of the profiles that are made by themselves for the nodes of a connected PasarGuard panel


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


def _base() -> list[str]:
    """The categories the owner has: his own list when he changed it (made, renamed or deleted one), else none (a new panel has no category until one is made)."""
    mine = settings.get("lab").get("tags")
    return [t for t in mine if isinstance(t, str)] if isinstance(mine, list) else list(DEFAULT_TAGS)


def all_tags(db: Session) -> list[str]:
    """The categories of the page: his set, then every other tag a profile carries (a tag is written once, whatever its case)."""
    out = _base()
    seen = {t.lower() for t in out}

    for t in db.scalars(select(Profile.tag).distinct().order_by(Profile.tag)):
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)

    return out

def node_in(panel: Panel, node_id: int) -> dict | None:
    """Pure: the node of the panel (as the health job last read it) with this id, or None."""
    return next((n for n in (panel.nodes or []) if isinstance(n, dict) and n.get("id") == node_id), None)


def _skip(db: Session) -> set[str]:
    """The nodes ("panel:node") the owner took off the page on purpose: the health check does not put them back."""
    row_ = db.get(Setting, "lab")
    v = row_.value.get("skip_nodes") if row_ is not None and isinstance(row_.value, dict) else None
    return {x for x in v if isinstance(x, str)} if isinstance(v, list) else set()


def _set_skip(db: Session, keys: set[str]) -> None:
    row_ = db.get(Setting, "lab")
    doc = dict(row_.value) if row_ is not None and isinstance(row_.value, dict) else {}
    doc["skip_nodes"] = sorted(keys)[:400]

    if row_ is None:
        db.add(Setting(key="lab", value=doc))
    else:
        row_.value = doc

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


_COUNTRY: dict[str, tuple[str, float]] = {}      # address -> (country code, when): the flag of a node that runs on no server of ours (the owner: a flag on every row, whatever the profile)
_COUNTRY_BUSY: set[str] = set()
COUNTRY_TTL, COUNTRY_RETRY = 7 * 86400, 600


def _fill_country(host: str) -> None:
    from . import nodes

    code = ""

    try:
        code = str(nodes.where(host).get("country_code") or "")
    except Exception:  # noqa: BLE001 - the geo services are outside: no answer is an answer
        code = ""
    finally:
        _COUNTRY[host] = (code, time.time() - (0 if code else COUNTRY_TTL - COUNTRY_RETRY))          # (no answer is asked again after ten minutes)
        _COUNTRY_BUSY.discard(host)


def country_of(host: str) -> str:
    """The country code of an address, from the cache; a miss starts ONE lookup in the background (the geo services are outside the box) and answers "" until it is done. Private and documentation addresses are not asked."""
    host = (host or "").strip()

    if not host:
        return ""

    try:
        if not ipaddress.ip_address(host.strip("[]")).is_global:
            return ""
    except ValueError:
        pass          # a name: the geo services resolve it

    hit = _COUNTRY.get(host)

    if hit and time.time() - hit[1] < COUNTRY_TTL:
        return hit[0]

    if host not in _COUNTRY_BUSY:
        _COUNTRY_BUSY.add(host)
        threading.Thread(target=_fill_country, args=(host,), daemon=True).start()

    return hit[0] if hit else ""

def list_all(db: Session) -> list[dict]:
    """Every profile, oldest first, with the name and address of what it points at."""
    servers = {s.id: s for s in db.scalars(select(Server))}
    panels = {p.id: p for p in db.scalars(select(Panel))}
    rows = [row(p, servers, panels) for p in db.scalars(select(Profile).order_by(Profile.id))]

    for r in rows:
        if r["kind"] == "node" and r.get("address"):
            r["country"] = country_of(r["address"])          # (the flag of a node: its server's, or - when it runs on no server of ours - the country of its address)

    return rows


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
        skipped = _skip(db)

        if f"{pid}:{nid}" in skipped:          # (added by hand again: the check may follow it again)
            _set_skip(db, skipped - {f"{pid}:{nid}"})
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


def add_tag(tag) -> str:
    """A category made by hand (the "+" beside the tabs of the Lab): it stays on the page while no profile carries it. Returns the tag as it is written."""
    t = check_tag(tag)

    if not t:
        raise ValueError("give the profile a name")

    cur = _base()

    if t.lower() not in {x.lower() for x in cur}:
        settings.put("lab", {"tags": [*cur, t][:60]})

    return t


def _locked(db: Session, tag: str) -> bool:
    """A PasarGuard profile (PasarGuard, PasarGuard Nodes ...) stays as it is while a PasarGuard panel is connected: no rename, no delete (the owner, 2026-10-15)."""
    return tag.lower().startswith("pasarguard") and db.scalar(select(Panel.id).where(Panel.type == "pasarguard").limit(1)) is not None


LOCKED_MSG = "A PasarGuard panel is connected: a PasarGuard profile stays as it is (it cannot be renamed or deleted)"


def rename_tag(db: Session, old, new) -> str:
    """Rename a category: the profiles that carry it follow. Returns the new name."""
    o, n = check_tag(old), check_tag(new)

    if _locked(db, o):
        raise ValueError(LOCKED_MSG)

    if not o or not n:
        raise ValueError("give the profile a name")

    cur = _base()
    known = {x.lower() for x in cur} | {t.lower() for t in db.scalars(select(Profile.tag).distinct()) if t}

    if o.lower() not in known:
        raise LookupError("no such profile")

    if n.lower() != o.lower() and n.lower() in known:
        raise FileExistsError(f"{n} exists already")

    settings.put("lab", {"tags": [n if x.lower() == o.lower() else x for x in cur] if o.lower() in {x.lower() for x in cur} else [*cur, n]})

    for p in db.scalars(select(Profile)):
        if (p.tag or "").lower() == o.lower():
            p.tag = n

    return n


def delete_tag(db: Session, tag) -> int:
    """Delete a category: the profiles that carried it go to Any (nothing else changes). Returns how many."""
    t = check_tag(tag)

    if not t:
        raise ValueError("Any cannot be deleted")

    if _locked(db, t):
        raise ValueError(LOCKED_MSG)

    settings.put("lab", {"tags": [x for x in _base() if x.lower() != t.lower()]})
    n = 0

    for p in db.scalars(select(Profile)):
        if (p.tag or "").lower() == t.lower():
            p.tag = ""
            n += 1

    return n

def set_tag(db: Session, pid: int, tag) -> str:
    """Give a profile another tag (the category it is listed under). Returns the tag ("" = Any)."""
    p = db.get(Profile, int(pid))

    if p is None:
        raise LookupError("profile not found")

    p.tag = check_tag(tag)
    return p.tag


def auto_nodes(db: Session, panel: Panel, tag: str = NODE_TAG, force: bool = False) -> int:
    """The nodes of a PasarGuard panel are put on the Lab page by themselves (the owner: "the panel must detect a node that was added in PasarGuard"): when the panel is connected and on every
    check of it (30 s), a node that has no profile yet - and that he did not take off the page on purpose - gets one, tagged PasarGuard Nodes. Returns how many."""
    if panel.type != "pasarguard":
        return 0

    made, skipped = 0, set() if force else _skip(db)          # (force = the Sync button: the nodes he took off the page are brought back too)

    if force:
        _set_skip(db, {k for k in _skip(db) if not k.startswith(f"{panel.id}:")})

    for n in panel.nodes or []:
        if not isinstance(n, dict) or not isinstance(n.get("id"), int) or f"{panel.id}:{n['id']}" in skipped:
            continue

        if db.scalar(select(Profile.id).where(Profile.kind == "node", Profile.panel_id == panel.id, Profile.node_id == n["id"])) is None:
            db.add(Profile(kind="node", panel_id=panel.id, node_id=n["id"], tag=tag))
            emit(db, "info", "servers", f"Node {n.get('name') or n['id']} of the panel {panel.name} was found and put on the Lab page ({tag or 'Any'}).", str(n.get("name") or n["id"]))
            made += 1

    return made

def drop_gone_nodes(db: Session, panel: Panel, names: dict[str, str] | None = None) -> int:
    """A node that was taken out of the PasarGuard panel itself goes off the Lab page too (the owner: "I deleted the node in PasarGuard and it stayed here"): the profiles of this panel whose node the panel
    no longer has are removed (nothing is changed anywhere). `names` = the names the nodes had at the last check, for the event. Call it only after a good read of the node list. Returns how many."""
    if panel.type != "pasarguard":
        return 0

    alive = {n["id"] for n in panel.nodes or [] if isinstance(n, dict) and isinstance(n.get("id"), int)}
    gone = [p for p in db.scalars(select(Profile).where(Profile.kind == "node", Profile.panel_id == panel.id)) if p.node_id not in alive]

    for p in gone:
        name = (names or {}).get(str(p.node_id)) or f"node {p.node_id}"
        emit(db, "info", "servers", f"Node {name} is not in the panel {panel.name} any more: taken off the Lab page.", name)
        db.delete(p)

    if gone:
        _set_skip(db, _skip(db) - {f"{panel.id}:{p.node_id}" for p in gone})

    return len(gone)


def rename_old_tag(db: Session) -> int:
    """The step of the upgrade to 1.52.0: the category that was called PasarGuard is called PasarGuard Nodes (the owner). Returns how many profiles followed."""
    n = 0

    for p in db.scalars(select(Profile)):
        if (p.tag or "").lower() == "pasarguard":
            p.tag = NODE_TAG
            n += 1

    mine = settings.get("lab").get("tags")

    if isinstance(mine, list) and any(isinstance(x, str) and x.lower() == "pasarguard" for x in mine):
        settings.put("lab", {"tags": [NODE_TAG if isinstance(x, str) and x.lower() == "pasarguard" else x for x in mine]})

    return n


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

    if p.kind == "node" and p.panel_id is not None and p.node_id is not None:
        _set_skip(db, _skip(db) | {f"{p.panel_id}:{p.node_id}"})          # (on purpose: the check of the panel does not put it back)

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

    _set_skip(db, {k for k in _skip(db) if not k.startswith(f"{panel_id}:")})
    return len(gone)
