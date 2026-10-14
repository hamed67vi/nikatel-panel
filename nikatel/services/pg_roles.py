"""PasarGuard: the admin ROLES of a PasarGuard 5.4 panel - list, make, change, delete, and the backups that make a change or a delete undoable (the owner asked for every
section of the panel, 2026-10-14). Written against the REST API read on a live 5.4.1 panel and its own pydantic models (app/models/admin_role.py, app/operation/admin_role.py).

A ROLE decides what an admin may do: `permissions` (the same list of resources and actions an API key can hold - see pg_admin.PERMS), `limits` (users, data, expiry, devices,
on-hold), `features`, `access` (templates and groups the admin may use) and the device (HWID) rules, plus three flags about what happens to the users of a limited or disabled admin.
What the panel enforces, and so what this module checks before it asks:
  * the owner role (is_owner) can never be changed, and the built-in roles (ids 1 to 3) can never be deleted (403);
  * a role with admins in it cannot be deleted (409): the names are listed instead of a bare refusal;
  * a name is unique (409) and at most 64 characters; the permission models refuse a name they do not know (`extra="forbid"`), so a typo never silently grants nothing.
A change sends only the sections that differ from the role as it is now (a section is replaced as a whole on the panel). The role is saved to a file in the data folder first (the
files hold no secret: names and numbers), `role_restore` puts a role back - or makes it again when it was deleted."""
from __future__ import annotations

import json
import re
import time

from .panels import PanelError
from .pg_admin import (
    _OVERRIDES,
    PERM_SCHEMA,
    _all_admins,
    _backup_dir,
    _call,
    _dict,
    _list,
    _number,
    _units,
    _write_backup,
    audit,
    login_admin,
    overrides_body,
    perms_body,
    perms_view,
)

MAX_NAME = 64
BUILTIN_MAX = 3                                  # roles 1 (owner), 2 and 3 are built in
HWID_MODES = ("disabled", "use_global", "override")
KEEP_LISTED = 40
_FEATURES = ("can_use_reset_strategy", "can_use_next_plan")
_FLAGS = (("disabled_when_limited", False), ("disconnect_users_when_limited", True), ("disconnect_users_when_disabled", True))
_HWID_INTS = ("fallback_limit", "min_limit", "max_limit")
_BK = re.compile(r"panel(\d+)-role-(\d+)-(\d{8}-\d{6})(?:-(\d+))?\.json")


# ------------------------------------------------------------------ pure: a role as the page sees it, and as the panel wants it
def _ids(v) -> list[int] | None:
    if v is None:
        return None

    return sorted({int(x) for x in _list(v) if isinstance(x, int) and not isinstance(x, bool)})


def role_row(r, admins: list[str] | None = None) -> dict:
    """One role for the page: the permission list as no / yes / own / all, the limits in GB and days (as the admins tab speaks them), the other sections as they are."""
    r = _dict(r)
    perms = perms_view(r.get("permissions"))
    lim, ft, acc, hw = (_dict(r.get(k)) for k in ("limits", "features", "access", "hwid"))
    rid = r.get("id")
    return {"id": rid, "name": str(r.get("name") or ""), "is_owner": bool(r.get("is_owner")), "builtin": isinstance(rid, int) and not isinstance(rid, bool) and rid <= BUILTIN_MAX,
            "created_at": r.get("created_at"), "permissions": perms, "granted": sum(1 for acts in perms.values() for lvl in acts.values() if lvl != "no"),
            "limits": {api: _units(lim.get(key), unit) for api, key, unit in _OVERRIDES}, "features": {k: ft.get(k) is not False for k in _FEATURES},
            "access": {"require_template": bool(acc.get("require_template")), "allowed_template_ids": _ids(acc.get("allowed_template_ids")), "allowed_group_ids": _ids(acc.get("allowed_group_ids"))},
            "hwid": {"enabled": hw.get("enabled") is not False, "forced": bool(hw.get("forced")), "require_hwid_for_manual_sub": bool(hw.get("require_hwid_for_manual_sub")),
                     **{k: hw.get(k) for k in _HWID_INTS}, "mode": str(hw.get("mode") or "use_global")},
            **{k: r.get(k) if isinstance(r.get(k), bool) else d for k, d in _FLAGS}, "admins": list(admins or [])}


def _access(v) -> dict:
    v = _dict(v)
    out: dict = {"require_template": bool(v.get("require_template"))}

    for k in ("allowed_template_ids", "allowed_group_ids"):
        x = v.get(k)

        if x is None:
            out[k] = None
            continue

        if not isinstance(x, list) or any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in x):
            raise ValueError(f"{k.replace('_', ' ')}: a list of ids, or nothing for no restriction")

        out[k] = sorted(set(x))

    return out


def _hwid(v) -> dict:
    v = _dict(v)
    mode = str(v.get("mode") or "use_global")

    if mode not in HWID_MODES:
        raise ValueError(f"the device rule is one of {', '.join(HWID_MODES)}")

    out: dict = {"enabled": bool(v.get("enabled", True)), "forced": bool(v.get("forced")), "require_hwid_for_manual_sub": bool(v.get("require_hwid_for_manual_sub")), "mode": mode}

    for k in _HWID_INTS:
        x = v.get(k)
        out[k] = None if x in (None, "") else int(_number(x, k.replace("_", " "), 0, 10000))

    if out["min_limit"] is not None and out["max_limit"] is not None and out["min_limit"] > out["max_limit"]:
        raise ValueError("the fewest devices cannot be more than the most")

    return out


def convert(data: dict) -> dict:
    """The sections of `data` that are present, checked and in the shape the panel takes. A section that is not in `data` is not in the answer (it stays as it is)."""
    data = _dict(data)
    out: dict = {}

    if "name" in data:
        n = str(data["name"] or "").strip()

        if not n or len(n) > MAX_NAME:
            raise ValueError(f"a role needs a name of 1 to {MAX_NAME} characters")

        out["name"] = n

    if "permissions" in data:
        out["permissions"] = perms_body(data["permissions"] or {})

    if "limits" in data:
        out["limits"] = overrides_body(_dict(data["limits"]))

    if "features" in data:
        out["features"] = {k: bool(_dict(data["features"]).get(k, True)) for k in _FEATURES}

    if "access" in data:
        out["access"] = _access(data["access"])

    if "hwid" in data:
        out["hwid"] = _hwid(data["hwid"])

    for k, _d in _FLAGS:
        if k in data:
            out[k] = bool(data[k])

    return out


# ------------------------------------------------------------------ reading
def _get(c: dict, role_id: int) -> dict:
    r = _dict(_call(c, "GET", f"/api/admin-role/{int(role_id)}"))

    if r.get("id") is None:
        raise ValueError("this role does not exist (any more)")

    return r


def _simple(c: dict, path: str, key: str) -> list[dict]:
    try:
        r = _dict(_call(c, "GET", path))
    except PanelError:
        return []

    return [{"id": x.get("id"), "name": str(x.get("name") or "")} for x in _list(r.get(key)) if isinstance(x, dict) and x.get("id") is not None]


def _members(c: dict) -> dict:
    """{role id: [usernames]} from the admins of the panel (empty when they cannot be read)."""
    out: dict = {}

    try:
        for a in _all_admins(c)[0]:
            rid = _dict(a.get("role")).get("id")

            if rid is not None:
                out.setdefault(rid, []).append(str(a.get("username") or ""))
    except PanelError:
        pass

    return out


def roles_list(c: dict) -> dict:
    """Every role with its permissions, limits and members, the permission names the panel knows, the groups and templates a role can give access to, and who Nikatel is signed in as."""
    r = _dict(_call(c, "GET", "/api/admin-roles"))
    raw = [x for x in _list(r.get("roles")) if isinstance(x, dict)]
    members = _members(c)
    me = login_admin(c)
    rows = [role_row(x, members.get(x.get("id"))) for x in raw]
    return {"roles": rows, "total": r.get("total") if isinstance(r.get("total"), int) else len(rows), "permission_schema": PERM_SCHEMA, "groups": _simple(c, "/api/groups/simple", "groups"),
            "templates": _simple(c, "/api/user_templates/simple", "templates"), "me": {"username": str(me.get("username") or c.get("username") or ""), "role_id": _dict(me.get("role")).get("id"),
                                                                                       "is_owner": bool(_dict(me.get("role")).get("is_owner"))}}


# ------------------------------------------------------------------ backups
def _backup(c: dict, raw: dict, why: str) -> str:
    return _write_backup(f"panel{int(c['id'])}-role-{int(raw['id'])}", {"panel": int(c["id"]), "role": raw, "why": why, "at": int(time.time())})


def role_backups(c: dict) -> list[dict]:
    """The files kept before a change or a delete of a role of this panel, newest first: {file, role_id, name, why, at}."""
    found = []

    for p in _backup_dir().iterdir():
        m = _BK.fullmatch(p.name)

        if not m or int(m.group(1)) != int(c["id"]):
            continue

        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            found.append((m.group(3), int(m.group(4) or 1), {"file": p.name, "role_id": int(m.group(2)), "name": str(_dict(doc.get("role")).get("name") or ""), "why": str(doc.get("why") or ""), "at": doc.get("at")}))
        except (OSError, ValueError, AttributeError):
            continue

    return [x[2] for x in sorted(found, key=lambda x: (x[0], x[1]), reverse=True)[:KEEP_LISTED]]


def role_restore(c: dict, name: str) -> dict:
    """Put a role back from a file kept here: the role as it was, written over the one that has its id now, or made again (a new id) when it was deleted. The owner role is never written."""
    m = _BK.fullmatch(str(name))

    if not m or int(m.group(1)) != int(c["id"]):
        raise ValueError("unknown backup")

    try:
        doc = json.loads((_backup_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("unknown backup") from None

    raw = _dict(doc.get("role"))

    if raw.get("is_owner") or raw.get("id") is None:
        raise ValueError("this backup cannot be put back")

    body = convert(role_row(raw))

    try:
        cur = _get(c, raw["id"])
    except (PanelError, ValueError):
        cur = None

    if cur is None:
        made = _dict(_call(c, "POST", "/api/admin-role", body=body))
        audit(c, f"role “{body['name']}” made again from a backup")
        return {"role": role_row(made), "made_again": True}

    if cur.get("is_owner"):
        raise ValueError("the owner role cannot be changed")

    bk = _backup(c, cur, "before a restore")
    made = _dict(_call(c, "PUT", f"/api/admin-role/{int(raw['id'])}", body=body))
    audit(c, f"role “{body['name']}” put back from a backup")
    return {"role": role_row(made), "made_again": False, "backup": bk}


# ------------------------------------------------------------------ writing
def role_create(c: dict, data: dict) -> dict:
    """Make a role. Only the sections given are sent; the panel fills the rest with its defaults (nothing granted, no limits, the device rules of the panel)."""
    sec = convert(data)

    if "name" not in sec:
        raise ValueError("a role needs a name")

    made = _dict(_call(c, "POST", "/api/admin-role", body=sec))
    audit(c, f"role “{sec['name']}” made")
    return role_row(made)


def role_update(c: dict, role_id: int, data: dict) -> dict:
    """Change a role: only the sections that differ from what it has now are sent, after the role is saved to a file. The owner role is refused here (the panel would refuse it too)."""
    cur = _get(c, role_id)

    if cur.get("is_owner"):
        raise ValueError("the owner role cannot be changed")

    want, have = convert(data), convert(role_row(cur))
    diff = {k: v for k, v in want.items() if v != have.get(k)}

    if not diff:
        raise ValueError("nothing to change: the role already has these values")

    bk = _backup(c, cur, "before a change")
    made = _dict(_call(c, "PUT", f"/api/admin-role/{int(role_id)}", body=diff))
    audit(c, f"role “{made.get('name') or cur.get('name')}” changed ({', '.join(sorted(diff))})")
    return {"role": role_row(made), "backup": bk, "changed": sorted(diff)}


def role_delete(c: dict, role_id: int, confirm: bool = False) -> dict:
    """Delete a role: refused for the owner role, a built-in role and a role that admins still have (they are named); otherwise it needs `confirm`, and the role is saved to a file first."""
    cur = _get(c, role_id)
    name = str(cur.get("name") or "")

    if cur.get("is_owner"):
        raise ValueError("the owner role cannot be deleted")

    if int(role_id) <= BUILTIN_MAX:
        raise ValueError(f"“{name}” is a built-in role: the panel does not delete it")

    users = _members(c).get(cur.get("id"), [])

    if users:
        raise ValueError(f"{len(users)} admin{'s have' if len(users) != 1 else ' has'} the role “{name}” ({', '.join(users[:6])}{', …' if len(users) > 6 else ''}): give them another role first")

    if not confirm:
        raise ValueError("deleting a role cannot be undone on the panel: send confirm: true (the role is saved here first)")

    bk = _backup(c, cur, "before a delete")
    _call(c, "DELETE", f"/api/admin-role/{int(role_id)}")
    audit(c, f"role “{name}” deleted (saved as {bk})", "warn")
    return {"deleted": True, "backup": bk}
