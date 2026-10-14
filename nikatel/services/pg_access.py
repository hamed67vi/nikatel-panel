"""PasarGuard: the groups and the user templates of a PasarGuard panel (roadmap #94).

A GROUP decides which inbounds a user gets: a user holds groups, a group holds inbound tags. A USER TEMPLATE is the recipe for new users: groups, data limit,
duration, username prefix / suffix, status. Both are read and written through the REST API of the panel (`/api/group`, `/api/user_template`; the bodies follow
PasarGuard v5.4.1, the answers were read from the live panel) with `panels.call`, which logs in by itself.

Four things are done on purpose, because the panel does not do them:
* an edit always sends the COMPLETE body: PasarGuard fills a field that is missing with its own default (a PUT of a group without `is_disabled` switches it ON again,
  a PUT of a template without `data_limit_reset_strategy` resets it to `no_reset`), so a half body would change what nobody asked to change;
* a group that users or templates still use is not deleted unless the caller says so (the panel deletes it at once and the users simply lose it);
* before a change that can lose something the groups and templates are written to <data>/pg-backups/ (the last 20 of a panel are kept), and `restore` puts them
  back: it only creates what is gone and puts changed fields back, it never deletes;
* the UI talks GB and days, the panel bytes and seconds: the conversion is here, and a value that was not touched keeps its exact bytes."""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path

from ..config import get_settings
from . import panels

GB = 1024 ** 3
DAY = 86400
MAX_GB = 1_000_000                       # a petabyte: far beyond any plan, it stops a typo like 1e12
MAX_SECONDS = 2_147_483_647              # the panel's own ceiling for a duration
KEEP_BACKUPS = 20
_LOCK = threading.Lock()
RESETS = ("no_reset", "day", "week", "month", "year")
STATUSES = ("active", "on_hold")
METHODS = ("aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "xchacha20-poly1305")
DEFAULT_METHOD = "chacha20-ietf-poly1305"
BULK_ACTIONS = ("enable", "disable", "delete")
_NAME_CHARS = re.compile(r"^[A-Za-z0-9._@-]+$")
_SPECIAL_RUN = re.compile(r"[-_@.]{2,}")
_BACKUP_NAME = re.compile(r"^panel(\d+)-access-(\d{8}-\d{6})(?:-(\d+))?\.json$")
_USERS_DONE = re.compile(r"(\d+)\s+users?")
_TEMPLATE_FIELDS = ("name", "data_limit", "expire_duration", "username_prefix", "username_suffix", "status", "data_limit_reset_strategy", "reset_usages",
                    "on_hold_timeout", "hwid_limit", "is_disabled", "method", "extra_settings")


# ------------------------------------------------------------------ pure: input checks
def need_pasarguard(c: dict) -> None:
    """Groups and templates are a PasarGuard thing; a Marzban panel has neither (its answer would be a confusing 404)."""
    if str(c.get("type") or "") != "pasarguard":
        raise ValueError("groups and templates belong to PasarGuard panels, and this panel is not one")


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def as_rows(raw, key: str) -> list[dict]:
    """The rows of an answer that is {key: [...], total: n} or a plain list; anything else is an empty list (a version may differ)."""
    if isinstance(raw, dict):
        raw = raw.get(key)

    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def name_ok(name, what: str, low: int = 1, high: int = 64) -> str:
    n = str(name or "").strip()

    if not low <= len(n) <= high:
        raise ValueError(f"the {what} name needs {low} to {high} characters")

    if any(ord(ch) < 32 for ch in n):
        raise ValueError(f"the {what} name has a control character in it")

    return n


def tags_ok(tags) -> list[str]:
    """The inbound tags of a group: texts, no empty ones, no duplicates, at least one."""
    if not isinstance(tags, (list, tuple)):
        raise ValueError("the inbound tags must be a list")  # noqa: TRY004 - the caller shows ValueError text to the operator

    out: list[str] = []

    for t in tags:
        s = str(t or "").strip()

        if len(s) > 200:
            raise ValueError("an inbound tag is too long")

        if s and s not in out:
            out.append(s)

    if not out:
        raise ValueError("pick at least one inbound")

    if len(out) > 300:
        raise ValueError("a group cannot hold more than 300 inbounds")

    return out


def ids_ok(ids, what: str, required: bool = True, high: int = 500) -> list[int]:
    """Positive whole numbers, no duplicates, in the order given."""
    if ids is None:
        ids = []

    if not isinstance(ids, (list, tuple, set)):
        raise ValueError(f"the {what} ids must be a list")  # noqa: TRY004 - the caller shows ValueError text to the operator

    out: list[int] = []

    for i in ids:
        n = _int(i)

        if n is None or n < 1:
            raise ValueError(f"{what} id {i!r} is not a whole number above zero")

        if n not in out:
            out.append(n)

    if required and not out:
        raise ValueError(f"pick at least one {what}")

    if len(out) > high:
        raise ValueError(f"at most {high} {what}s at once")

    return out


def affix_ok(v, what: str) -> str:
    """A username prefix or suffix, with the panel's own rules (20 characters, letters digits - _ @ . and never two of the four in a row)."""
    s = str(v or "").strip()

    if not s:
        return ""

    if len(s) > 20:
        raise ValueError(f"the username {what} can have 20 characters at most")

    if not _NAME_CHARS.match(s) or _SPECIAL_RUN.search(s):
        raise ValueError(f"the username {what} can have letters, digits and - _ @ . only, and never two of the last four in a row")

    return s


def username_ok(u) -> str:
    s = str(u or "").strip()

    if not 3 <= len(s) <= 128:
        raise ValueError("a username has 3 to 128 characters")

    if not _NAME_CHARS.match(s):
        raise ValueError("a username can have letters, digits and - _ @ . only")

    if _SPECIAL_RUN.search(s):
        raise ValueError("a username cannot have two of - _ @ . in a row")

    return s


def gb_to_bytes(gb, keep: int | None = None) -> int:
    """GB -> bytes; empty or 0 is 'unlimited' (0). `keep` = the bytes the template has now: when the GB value typed is the one shown for them, they are kept exactly
    (0.1 GB read back is 0.1, and writing it again must not move the bytes by one)."""
    if gb in (None, ""):
        return 0

    try:
        v = float(gb)
    except (TypeError, ValueError):
        raise ValueError("the data limit must be a number of GB") from None

    if math.isnan(v) or v < 0 or v > MAX_GB:
        raise ValueError(f"the data limit must be between 0 and {MAX_GB} GB (0 = unlimited)")

    if keep and round(int(keep) / GB, 3) == round(v, 3):
        return int(keep)

    return round(v * GB)


def days_to_seconds(days, keep: int | None = None) -> int:
    """Days -> seconds; empty or 0 is 'none' (0). `keep` works as in gb_to_bytes."""
    if days in (None, ""):
        return 0

    try:
        v = float(days)
    except (TypeError, ValueError):
        raise ValueError("a duration must be a number of days") from None

    if math.isnan(v) or v < 0 or v * DAY > MAX_SECONDS:
        raise ValueError(f"a duration must be between 0 and {MAX_SECONDS // DAY} days (0 = none)")

    if keep and round(int(keep) / DAY, 3) == round(v, 3):
        return int(keep)

    return round(v * DAY)


# ------------------------------------------------------------------ pure: rows
def group_row(g: dict) -> dict:
    """A group as the UI uses it. `users` is None when the panel does not say how many users hold it."""
    tags = g.get("inbound_tags")
    return {"id": _int(g.get("id")), "name": str(g.get("name") or ""), "inbound_tags": [str(t) for t in tags] if isinstance(tags, list) else [],
            "is_disabled": bool(g.get("is_disabled")), "users": _int(g.get("total_users"))}


def template_row(t: dict) -> dict:
    """A user template with both units: bytes / seconds as the panel keeps them, GB / days as the UI shows them. Missing fields get their defaults."""
    extra = t.get("extra_settings") if isinstance(t.get("extra_settings"), dict) else {}
    gids = t.get("group_ids")
    dl, ex, hold = _int(t.get("data_limit"), 0) or 0, _int(t.get("expire_duration"), 0) or 0, _int(t.get("on_hold_timeout"), 0) or 0
    return {"id": _int(t.get("id")), "name": str(t.get("name") or ""),
            "group_ids": sorted({i for i in (_int(x) for x in gids) if i is not None}) if isinstance(gids, list) else [],
            "data_limit": dl, "data_limit_gb": round(dl / GB, 3), "expire_duration": ex, "expire_days": round(ex / DAY, 3),
            "username_prefix": str(t.get("username_prefix") or ""), "username_suffix": str(t.get("username_suffix") or ""),
            "status": str(t.get("status") or "active"), "data_limit_reset_strategy": str(t.get("data_limit_reset_strategy") or "no_reset"),
            "reset_usages": bool(t.get("reset_usages")), "on_hold_timeout": hold, "on_hold_days": round(hold / DAY, 3),
            "hwid_limit": _int(t.get("hwid_limit")), "is_disabled": bool(t.get("is_disabled")), "method": str(extra.get("method") or ""), "extra_settings": extra}


def to_panel_units(ch: dict, cur: dict | None) -> dict:
    """The change as the panel wants it: data_limit_gb -> data_limit (bytes), expire_days -> expire_duration and on_hold_days -> on_hold_timeout (seconds).
    Fields that already speak the panel's units pass through. `cur` (a template_row) lets an untouched value keep its exact bytes."""
    out = dict(ch)
    cur = cur or {}

    if "data_limit_gb" in out:
        out["data_limit"] = gb_to_bytes(out.pop("data_limit_gb"), cur.get("data_limit"))

    if "expire_days" in out:
        out["expire_duration"] = days_to_seconds(out.pop("expire_days"), cur.get("expire_duration"))

    if "on_hold_days" in out:
        out["on_hold_timeout"] = days_to_seconds(out.pop("on_hold_days"), cur.get("on_hold_timeout"))

    return out


def template_body(cur: dict | None, ch: dict) -> dict:
    """The COMPLETE body of POST / PUT /api/user_template: what the template has now (`cur`, a template_row; None = a new one), changed by `ch` (panel units), checked.
    Always whole, because the panel fills a missing field with its own default. A few fields cannot be sent as null to clear them (null means 'keep' on a PUT), so an
    empty prefix / suffix goes as '' and no on-hold timeout as 0 when the template exists."""
    base = cur or {}
    editing = cur is not None

    def val(key, default=None):
        return ch[key] if key in ch else base.get(key, default)

    name = name_ok(val("name"), "template", 1)
    gids = ids_ok(val("group_ids", []), "group")
    data_limit = _int(val("data_limit", 0), None)
    expire = _int(val("expire_duration", 0), None)
    hold = _int(val("on_hold_timeout", 0) or 0, None)

    if data_limit is None or not 0 <= data_limit <= MAX_GB * GB:
        raise ValueError("the data limit must be a number of bytes, 0 or more")

    if expire is None or not 0 <= expire <= MAX_SECONDS:
        raise ValueError("the duration must be a number of seconds, 0 or more")

    if hold is None or not 0 <= hold <= MAX_SECONDS:
        raise ValueError("the on-hold timeout must be a number of seconds, 0 or more")

    status = str(val("status", "active") or "active")
    reset = str(val("data_limit_reset_strategy", "no_reset") or "no_reset")
    method = str(val("method", "") or "")
    hwid = val("hwid_limit", None)

    if status not in STATUSES:
        raise ValueError("the status must be active or on_hold")

    if status == "on_hold" and not expire:
        raise ValueError("a template that starts users ON HOLD needs a duration (the days that count from the first connection)")

    if reset not in RESETS:
        raise ValueError("the data limit reset must be one of " + ", ".join(RESETS))

    if method and method not in METHODS:
        raise ValueError("the shadowsocks method must be one of " + ", ".join(METHODS))

    if not method and base.get("method"):
        method = DEFAULT_METHOD                        # 'default' on a template that has a method: null would keep the old one

    if hwid is not None:
        hwid = _int(hwid)

        if hwid is None or hwid < 0 or hwid > 1_000_000:
            raise ValueError("the device limit must be a whole number, 0 or more")

    src = val("extra_settings")
    extra = {k: v for k, v in (src if isinstance(src, dict) else {}).items() if k != "method"}       # a key this module does not know (an older panel's `flow`) is sent back as the panel has it
    empty = "" if editing else None

    if method:
        extra["method"] = method

    return {"name": name, "group_ids": sorted(gids), "data_limit": data_limit, "expire_duration": expire,
            "username_prefix": affix_ok(val("username_prefix", ""), "prefix") or empty, "username_suffix": affix_ok(val("username_suffix", ""), "suffix") or empty,
            "status": status, "data_limit_reset_strategy": reset, "reset_usages": bool(val("reset_usages", False)),
            "on_hold_timeout": hold or (0 if editing else None), "hwid_limit": hwid, "is_disabled": bool(val("is_disabled", False)),
            "extra_settings": extra or None}


# ------------------------------------------------------------------ reading
def _state(c: dict) -> tuple[list[dict], list[dict]]:
    """The raw groups and templates of the panel: what a change reads first (and what a backup keeps)."""
    return (as_rows(panels.call(c, "GET", "/api/groups"), "groups"), as_rows(panels.call(c, "GET", "/api/user_templates"), "templates"))


def _inbounds(raw) -> list[dict]:
    """[{tag, protocol, network}] from /api/inbounds/details (list of objects), /api/inbounds (list of tags) or a Marzban-style {protocol: [{tag}]}."""
    items: list[tuple[str, str, str]] = []

    if isinstance(raw, dict):
        for proto, lst in raw.items():
            items += [(str(i.get("tag") or ""), str(proto), "") for i in lst if isinstance(i, dict)] if isinstance(lst, list) else []
    elif isinstance(raw, list):
        for i in raw:
            if isinstance(i, dict):
                items.append((str(i.get("tag") or ""), str(i.get("protocol") or ""), str(i.get("network") or "")))
            elif isinstance(i, str):
                items.append((i, "", ""))

    seen: set[str] = set()
    out = []

    for tag, proto, net in items:
        if tag and tag not in seen:
            seen.add(tag)
            out.append({"tag": tag, "protocol": proto, "network": net})

    return sorted(out, key=lambda r: r["tag"].lower())


def inbound_list(c: dict) -> list[dict]:
    """The inbound tags a group can hold, with protocol and network when the panel says (v5.4 does at /api/inbounds/details, older ones only list the tags)."""
    try:
        out = _inbounds(panels.call(c, "GET", "/api/inbounds/details"))
    except panels.PanelError:
        out = []

    return out or _inbounds(panels.call(c, "GET", "/api/inbounds"))


def groups(c: dict) -> dict:
    """Every group with how many users hold it and which templates use it, and the inbounds to choose from."""
    need_pasarguard(c)
    rows = [group_row(g) for g in as_rows(panels.call(c, "GET", "/api/groups"), "groups")]
    warn = ""

    try:
        tpls = [template_row(t) for t in as_rows(panels.call(c, "GET", "/api/user_templates"), "templates")]
    except panels.PanelError as e:                     # a limited admin may not read templates: the groups are still worth showing
        tpls, warn = [], f"the templates could not be read ({e}), so it is not shown which of them use a group"

    for g in rows:
        g["templates"] = [t["name"] for t in tpls if g["id"] in t["group_ids"]][:50]

    return {"groups": rows, "total": len(rows), "inbounds": inbound_list(c), "warn": warn}


def group_get(c: dict, gid: int) -> dict:
    need_pasarguard(c)
    return group_row(panels.call(c, "GET", f"/api/group/{int(gid)}") or {})


def templates(c: dict) -> dict:
    """Every user template, and the groups to choose from (with their ids and names)."""
    need_pasarguard(c)
    gs, ts = _state(c)
    return {"templates": [template_row(t) for t in ts], "total": len(ts), "groups": [{"id": g["id"], "name": g["name"], "is_disabled": g["is_disabled"]} for g in map(group_row, gs)],
            "resets": list(RESETS), "statuses": list(STATUSES), "methods": list(METHODS)}


def template_get(c: dict, tid: int) -> dict:
    need_pasarguard(c)
    return template_row(panels.call(c, "GET", f"/api/user_template/{int(tid)}") or {})


# ------------------------------------------------------------------ backups
def _backup_dir() -> Path:
    d = get_settings().data_dir / "pg-backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _files(pid: int) -> list[Path]:
    """The backup files of this panel, oldest first. The order comes from the NAME (the second, then the counter used when several were written within it): the file
    system's own times are too coarse to tell two files of the same moment apart."""
    found = []

    for p in _backup_dir().glob(f"panel{pid}-access-*.json"):
        m = _BACKUP_NAME.match(p.name)

        if m and int(m.group(1)) == pid:
            found.append((m.group(2), int(m.group(3) or 1), p))

    return [p for _s, _n, p in sorted(found, key=lambda x: (x[0], x[1]))]


def write_backup(c: dict, reason: str, gs: list[dict], ts: list[dict]) -> str:
    """Write the groups and templates as they are NOW to <data>/pg-backups/panel<id>-access-<time>.json and keep the last 20 of this panel. Returns the file name.
    The change that asked for it must not go on when the file could not be written."""
    pid = int(c.get("id") or 0)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    doc = {"kind": "pg-access", "panel": pid, "at": int(time.time()), "reason": str(reason)[:160], "groups": gs, "templates": ts}

    try:
        with _LOCK:                                           # two requests in the same second must not pick the same name
            d = _backup_dir()
            used = [int(m.group(3) or 1) for p in _files(pid) for m in [_BACKUP_NAME.match(p.name)] if m and m.group(2) == stamp]
            n = max(used) + 1 if used else 1                  # the counter goes on after the highest one of this second, even when the older files are gone (they sort by it)
            name = f"panel{pid}-access-{stamp}.json" if n == 1 else f"panel{pid}-access-{stamp}-{n}.json"
            (d / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

            for old in _files(pid)[:-KEEP_BACKUPS]:
                old.unlink()
    except OSError as e:
        raise RuntimeError(f"the backup could not be written ({type(e).__name__}), so nothing was changed") from e

    return name


def backups(c: dict) -> list[dict]:
    """The backups of this panel, newest first: {name, at, reason, groups, templates} (the two counts)."""
    out = []

    for p in reversed(_files(int(c.get("id") or 0))):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        if isinstance(doc, dict) and doc.get("kind") == "pg-access":
            out.append({"name": p.name, "at": _int(doc.get("at"), 0), "reason": str(doc.get("reason") or ""),
                        "groups": len(doc.get("groups") or []), "templates": len(doc.get("templates") or [])})

    return out[:KEEP_BACKUPS]


def _read_backup(c: dict, name: str) -> dict:
    m = _BACKUP_NAME.match(str(name or ""))

    if not m or int(m.group(1)) != int(c.get("id") or 0):
        raise ValueError("this is not a backup of this panel")

    p = _backup_dir() / str(name)

    if not p.is_file():
        raise ValueError("this backup file is not there (any more)")

    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("this backup file cannot be read") from None

    if not isinstance(doc, dict) or doc.get("kind") != "pg-access":
        raise ValueError("this file is not a backup of groups and templates")

    return doc


# ------------------------------------------------------------------ groups: write
def _find(rows: list[dict], rid: int, what: str) -> dict:
    row = next((r for r in rows if _int(r.get("id")) == int(rid)), None)

    if row is None:
        raise ValueError(f"this {what} is not on the panel (any more)")

    return row


def _clash(rows: list[dict], name: str, what: str, own_id: int | None = None) -> None:
    for r in rows:
        if str(r.get("name") or "").lower() == name.lower() and _int(r.get("id")) != own_id:
            raise ValueError(f"a {what} named {r.get('name')} exists already (id {r.get('id')})")


def _used_by(g: dict, ts: list[dict]) -> list[str]:
    return [str(t.get("name") or "") for t in ts if g["id"] in template_row(t)["group_ids"]]


def group_create(c: dict, name: str, inbound_tags, is_disabled: bool = False) -> dict:
    need_pasarguard(c)
    name = name_ok(name, "group", 3)
    tags = tags_ok(inbound_tags)
    _clash(as_rows(panels.call(c, "GET", "/api/groups"), "groups"), name, "group")
    return group_row(panels.call(c, "POST", "/api/group", body={"name": name, "inbound_tags": tags, "is_disabled": bool(is_disabled)}) or {})


def group_update(c: dict, gid: int, ch: dict) -> dict:
    """Change the name, the inbound tags and / or the switch of a group. The panel is always sent all three (see the module text). Returns {group, before, added,
    removed, changed, backup}; nothing is written when nothing changes."""
    need_pasarguard(c)
    gs, ts = _state(c)
    before = group_row(_find(gs, gid, "group"))
    name = name_ok(ch["name"] if ch.get("name") is not None else before["name"], "group", 3)
    tags = tags_ok(ch["inbound_tags"]) if ch.get("inbound_tags") is not None else before["inbound_tags"]
    disabled = bool(ch["is_disabled"]) if ch.get("is_disabled") is not None else before["is_disabled"]
    out = {"before": before, "added": [t for t in tags if t not in before["inbound_tags"]], "removed": [t for t in before["inbound_tags"] if t not in tags]}

    if (name, sorted(tags), disabled) == (before["name"], sorted(before["inbound_tags"]), before["is_disabled"]):
        return {**out, "group": before, "changed": False, "backup": ""}

    if name != before["name"]:                            # a pre-existing twin name must not block an unrelated change
        _clash(gs, name, "group", before["id"])

    backup = write_backup(c, f"before changing group {before['name']}", gs, ts)
    raw = panels.call(c, "PUT", f"/api/group/{before['id']}", body={"name": name, "inbound_tags": tags, "is_disabled": disabled})
    return {**out, "group": group_row(raw or {}), "changed": True, "backup": backup}


def group_delete(c: dict, gid: int, force: bool = False) -> dict:
    """Delete a group. While users or templates hold it this is refused unless `force`: the panel would delete it and they would lose it (the users their inbounds,
    the templates a group)."""
    need_pasarguard(c)
    gs, ts = _state(c)
    g = group_row(_find(gs, gid, "group"))
    tpls = _used_by(g, ts)

    if (g["users"] or tpls) and not force:
        raise ValueError(f"group {g['name']} is used by {g['users'] or 0} users and {len(tpls)} templates: deleting it takes it away from them. Move them first or confirm the delete")

    backup = write_backup(c, f"before deleting group {g['name']}", gs, ts)
    panels.call(c, "DELETE", f"/api/group/{g['id']}")
    return {"deleted": g, "users": g["users"], "templates": tpls, "backup": backup}


def group_bulk(c: dict, action: str, ids, force: bool = False) -> dict:
    """Enable, disable or delete several groups with the panel's bulk calls. A delete of groups that are in use needs `force` as for one group."""
    need_pasarguard(c)

    if action not in BULK_ACTIONS:
        raise ValueError("the action must be enable, disable or delete")

    ids = ids_ok(ids, "group", high=200)
    gs, ts = _state(c)
    rows = [group_row(_find(gs, i, "group")) for i in ids]

    if action == "delete":
        users, tpls = sum(r["users"] or 0 for r in rows), {n for r in rows for n in _used_by(r, ts)}

        if (users or tpls) and not force:
            raise ValueError(f"these groups are used by {users} users and {len(tpls)} templates: deleting them takes the groups away from them. Confirm the delete")

    backup = write_backup(c, f"before {action} of groups {', '.join(r['name'] for r in rows)[:100]}", gs, ts)
    raw = panels.call(c, "POST", f"/api/groups/bulk/{action}", body={"ids": ids})
    names = [str(n) for n in raw.get("groups", [])] if isinstance(raw, dict) and isinstance(raw.get("groups"), list) else []
    return {"action": action, "count": _int(raw.get("count"), len(names)) if isinstance(raw, dict) else len(names), "groups": names, "backup": backup}


def group_members(c: dict, action: str, group_ids, has_group_ids=(), has_no_group: bool = False, user_ids=(), admin_ids=(), all_users: bool = False, dry_run: bool = True) -> dict:
    """Add the groups to users, or take them away (the panel's bulk call). WHO is chosen explicitly: the panel takes an empty choice as 'every user', so an empty choice is
    refused here and 'all users' must be asked for. `dry_run` (the default) only counts. Returns {dry_run, users, detail}; there is no backup of who held what."""
    need_pasarguard(c)

    if action not in ("add", "remove"):
        raise ValueError("the action must be add or remove")

    gids = ids_ok(group_ids, "group", high=50)
    has = ids_ok(has_group_ids, "group", required=False, high=50)
    users = ids_ok(user_ids, "user", required=False, high=5000)
    admins = ids_ok(admin_ids, "admin", required=False, high=100)

    if all_users and (has or has_no_group or users or admins):
        raise ValueError("'all users' cannot be mixed with another choice")

    if not all_users and not (has or has_no_group or users or admins):
        raise ValueError("choose who is changed: users who hold a group, users with no group, some users, the users of an admin, or all users")

    if has_no_group and has:
        raise ValueError("'users with no group' and 'users who hold a group' cannot be mixed: nobody is both")

    if users and (has or has_no_group or admins):
        raise ValueError("a list of users cannot be mixed with another choice (the panel would join them with OR)")

    raw = panels.call(c, "POST", f"/api/groups/bulk/{action}", body={"group_ids": gids, "has_group_ids": has, "has_no_group": bool(has_no_group), "admins": admins, "users": users, "dry_run": bool(dry_run)})
    raw = raw if isinstance(raw, dict) else {}
    detail = str(raw.get("detail") or "")
    n = _int(raw.get("affected_users"))

    if n is None:
        m = _USERS_DONE.search(detail)
        n = int(m.group(1)) if m else None

    return {"dry_run": bool(dry_run), "action": action, "users": n, "detail": detail[:200]}


# ------------------------------------------------------------------ templates: write
def _check_groups_exist(ids: list[int], gs: list[dict]) -> None:
    have = {_int(g.get("id")) for g in gs}

    for i in ids:
        if i not in have:
            raise ValueError(f"group {i} is not on the panel")


def template_create(c: dict, ch: dict) -> dict:
    """A new template from `ch` (GB / days or the panel's own bytes / seconds; see template_body). Returns {template}."""
    need_pasarguard(c)
    gs, ts = _state(c)
    body = template_body(None, to_panel_units(ch, None))
    _check_groups_exist(body["group_ids"], gs)
    _clash(ts, body["name"], "template")
    return {"template": template_row(panels.call(c, "POST", "/api/user_template", body=body) or {})}


def template_update(c: dict, tid: int, ch: dict) -> dict:
    """Change some fields of a template; the rest stays as it is (the whole body goes to the panel). Returns {template, before, changed, backup}."""
    need_pasarguard(c)
    gs, ts = _state(c)
    before = template_row(_find(ts, tid, "template"))
    body = template_body(before, to_panel_units(ch, before))

    if body == template_body(before, {}):
        return {"template": before, "before": before, "changed": False, "backup": ""}

    if body["group_ids"] != before["group_ids"]:
        _check_groups_exist(body["group_ids"], gs)

    if body["name"] != before["name"]:
        _clash(ts, body["name"], "template", before["id"])

    backup = write_backup(c, f"before changing template {before['name']}", gs, ts)
    raw = panels.call(c, "PUT", f"/api/user_template/{before['id']}", body=body)
    return {"template": template_row(raw or {}), "before": before, "changed": True, "backup": backup}


def template_delete(c: dict, tid: int) -> dict:
    """Delete a template. Users made from it stay; a user that has it queued as the next plan loses that plan."""
    need_pasarguard(c)
    gs, ts = _state(c)
    t = template_row(_find(ts, tid, "template"))
    backup = write_backup(c, f"before deleting template {t['name']}", gs, ts)
    panels.call(c, "DELETE", f"/api/user_template/{t['id']}")
    return {"deleted": t, "backup": backup}


def template_bulk(c: dict, action: str, ids) -> dict:
    need_pasarguard(c)

    if action not in BULK_ACTIONS:
        raise ValueError("the action must be enable, disable or delete")

    ids = ids_ok(ids, "template", high=200)
    gs, ts = _state(c)
    rows = [template_row(_find(ts, i, "template")) for i in ids]
    backup = write_backup(c, f"before {action} of templates {', '.join(r['name'] for r in rows)[:100]}", gs, ts)
    raw = panels.call(c, "POST", f"/api/user_templates/bulk/{action}", body={"ids": ids})
    names = [str(n) for n in raw.get("templates", [])] if isinstance(raw, dict) and isinstance(raw.get("templates"), list) else []
    return {"action": action, "count": _int(raw.get("count"), len(names)) if isinstance(raw, dict) else len(names), "templates": names, "backup": backup}


def template_user(c: dict, tid: int, username: str, note: str = "") -> dict:
    """Make a user from a template (POST /api/user/from_template). The panel puts the template's prefix and suffix around `username`; both the typed name and the
    final one must be valid usernames, which is checked here first. Returns {user, username} with the final username."""
    need_pasarguard(c)
    t = template_get(c, tid)

    if t["is_disabled"]:
        raise ValueError(f"template {t['name']} is switched off: switch it on first")

    base = username_ok(username)
    final = username_ok(t["username_prefix"] + base + t["username_suffix"])
    note = str(note or "").strip()

    if len(note) > 500:
        raise ValueError("the note can have 500 characters at most")

    raw = panels.call(c, "POST", "/api/user/from_template", body={"username": base, "user_template_id": t["id"], "note": note or None})
    return {"user": panels.user_row(raw if isinstance(raw, dict) else {}), "username": final}


# ------------------------------------------------------------------ restore
def restore(c: dict, name: str, groups=None, templates=None, everything: bool = False, dry_run: bool = True) -> dict:
    """Put a backup back. `groups` / `templates` = the ids (of the backup) to restore, or `everything`. A group or template that is gone is created again (it gets a NEW
    id; the templates are pointed at it), one that changed gets its name / inbounds / switch (fields) back, one with the same name already there is left alone, and
    NOTHING IS EVER DELETED. The users a deleted group held are not put back (the panel dropped them). `dry_run` (the default) only says what it would do.
    Returns {dry_run, items: [{kind, id, name, action, reason, ok, new_id}], plan: {create, update, same, skip}, created, updated, failed, backup}."""
    need_pasarguard(c)
    snap = _read_backup(c, name)

    if not everything and groups is None and templates is None:
        raise ValueError("say what to restore: everything, or the ids of groups and templates")

    only_g = None if everything else set(ids_ok(groups, "group", required=False))
    only_t = None if everything else set(ids_ok(templates, "template", required=False))
    gs, ts = _state(c)
    cur_g, cur_t = [group_row(g) for g in gs], [template_row(t) for t in ts]
    g_id, g_name = {g["id"]: g for g in cur_g}, {g["name"].lower(): g for g in cur_g}
    t_id, t_name = {t["id"]: t for t in cur_t}, {t["name"].lower(): t for t in cur_t}
    gmap: dict[int, int] = {}                    # id in the backup -> id on the panel (a group that is created gets its entry when it is)
    ops: list[dict] = []

    for raw in as_rows(snap.get("groups"), "groups"):
        b = group_row(raw)

        if b["id"] is None:
            continue

        mine = only_g is None or b["id"] in only_g
        now = g_id.get(b["id"])
        twin = g_name.get(b["name"].lower())
        item = {"kind": "group", "id": b["id"], "name": b["name"], "reason": "", "ok": None, "new_id": None}

        if now is not None:
            gmap[b["id"]] = now["id"]
            same = (now["name"], sorted(now["inbound_tags"]), now["is_disabled"]) == (b["name"], sorted(b["inbound_tags"]), b["is_disabled"])

            if mine:
                ops.append({**item, "action": "same" if same else "update", "snap": b, "cur": now})
        elif twin is not None:
            gmap[b["id"]] = twin["id"]

            if mine:
                ops.append({**item, "action": "skip", "reason": f"a group with this name exists already (id {twin['id']})"})
        elif mine:
            ops.append({**item, "action": "create", "snap": b})

    creating = {o["id"] for o in ops if o["kind"] == "group" and o["action"] == "create"}

    for raw in as_rows(snap.get("templates"), "templates"):
        b = template_row(raw)

        if b["id"] is None or not (only_t is None or b["id"] in only_t):
            continue

        now = t_id.get(b["id"])
        twin = t_name.get(b["name"].lower())
        item = {"kind": "template", "id": b["id"], "name": b["name"], "reason": "", "ok": None, "new_id": None}
        alive = [g for g in b["group_ids"] if g in gmap or g in creating]

        if now is None and twin is not None:
            ops.append({**item, "action": "skip", "reason": f"a template with this name exists already (id {twin['id']})"})
        elif not alive:
            ops.append({**item, "action": "skip", "reason": "none of its groups exists any more, so it would have no group"})
        elif now is None:
            ops.append({**item, "action": "create", "snap": b, "gids": alive})
        else:
            ch = {k: b[k] for k in _TEMPLATE_FIELDS}
            same = not creating.intersection(alive) and now["group_ids"] == sorted(gmap[g] for g in alive if g in gmap) and _same_template(now, ch)
            ops.append({**item, "action": "same" if same else "update", "snap": b, "cur": now, "gids": alive,
                        "reason": f"{len(b['group_ids']) - len(alive)} of its groups are gone and stay out" if len(alive) < len(b["group_ids"]) else ""})

    plan = {a: sum(1 for o in ops if o["action"] == a) for a in ("create", "update", "same", "skip")}
    pre = ""

    if not dry_run and (plan["create"] or plan["update"]):
        pre = write_backup(c, f"before restoring {name}", gs, ts)

    for o in sorted(ops, key=lambda o: o["kind"] != "group"):             # the groups first: the templates point at them
        if dry_run or o["action"] not in ("create", "update"):
            continue

        try:
            _apply_op(c, o, gmap)
            o["ok"] = True
        except (panels.PanelError, ValueError) as e:                       # one item failing must not stop the rest: it is reported
            o.update(ok=False, action="failed", reason=str(e)[:160])

    items = [{k: v for k, v in o.items() if k in ("kind", "id", "name", "action", "reason", "ok", "new_id")} for o in ops]
    return {"dry_run": bool(dry_run), "items": items, "plan": plan, "created": sum(1 for o in ops if o["action"] == "create" and o["ok"]),
            "updated": sum(1 for o in ops if o["action"] == "update" and o["ok"]), "failed": sum(1 for o in ops if o["action"] == "failed"), "backup": pre}


def _same_template(now: dict, snap_fields: dict) -> bool:
    """Is the template on the panel what the backup holds (the fields the restore writes)?"""
    try:
        return template_body(now, {}) == template_body(now, {**snap_fields, "group_ids": now["group_ids"]})
    except ValueError:
        return False


def _apply_op(c: dict, o: dict, gmap: dict[int, int]) -> None:
    """One create or update of a restore. Raises PanelError / ValueError; the caller reports it."""
    b = o["snap"]

    if o["kind"] == "group":
        body = {"name": name_ok(b["name"], "group", 3), "inbound_tags": tags_ok(b["inbound_tags"]), "is_disabled": b["is_disabled"]}

        if o["action"] == "create":
            new = group_row(panels.call(c, "POST", "/api/group", body=body) or {})
            gmap[b["id"]] = o["new_id"] = new["id"]
        else:
            panels.call(c, "PUT", f"/api/group/{o['cur']['id']}", body=body)

        return

    ch = {k: b[k] for k in _TEMPLATE_FIELDS}
    ch["group_ids"] = [gmap[g] for g in o["gids"] if g in gmap]

    if o["action"] == "create":
        new = template_row(panels.call(c, "POST", "/api/user_template", body=template_body(None, ch)) or {})
        o["new_id"] = new["id"]
    else:
        panels.call(c, "PUT", f"/api/user_template/{o['cur']['id']}", body=template_body(o["cur"], ch))
