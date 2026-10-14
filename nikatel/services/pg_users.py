"""PasarGuard: what the Users list and the window of a user lacked for a 5.4 panel (the owner asked for every section of the panel, 2026-10-14) - the whole user (groups, on hold,
devices limit, next plan, owner), the devices (HWID) a user has used, the last fetches of its subscription, one-call actions on several users at once, shifting the end date or the data
limit of many users, the users that are expired / limited / on hold / disabled (list and delete), and users made from a template (one or many). Written against the REST API read on a
live 5.4.1 panel and its sources (app/models/user.py, app/db/crud/user.py, app/operation/user.py).

What the panel does that matters, and so what is done here:
  * a PUT that does not carry `next_plan` DELETES the user's next plan (crud modify_user: "elif db_user.next_plan is not None: delete"): `modify` always resends the plan the user has;
  * `expire: 0` removes the end date and makes an expired user active; `expire: null` means "leave it"; `data_limit: 0` is unlimited; `on_hold_timeout: 0` clears it;
  * a user cannot be ON HOLD without `on_hold_expire_duration` > 0, and an on-hold user cannot have an `expire`;
  * `hwid_limit` can be set to a number, not taken away again (null means "leave it");
  * the by-username routes are deprecated (removed in 6.0): the by-id routes are used here, so this module needs the id of a user, and finds it with GET /api/users?usernames=;
  * the bulk routes take user ids; `POST /api/users/bulk/{data_limit|expire}` take filters (group ids, admin ids, user ids, statuses, expire range) and an `amount` to ADD (negative:
    take away); their dry run answers with a COUNT of the users it would touch (no names), and the real run with a sentence that holds the count;
  * DELETE /api/users/expired removes users by state (target expired | limited | on_hold | disabled) for an admin and a time range, with a dry run.
The devices carry the device id (HWID): it leaves this module shortened. Everything that deletes or cuts needs `confirm`; a delete of many names them first."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from . import panels
from .panels import PanelError
from .pg_admin import _call, _dict, _list, _number, audit

GB = 1024 ** 3
DAY = 86400
STATUSES = ("active", "disabled", "on_hold")
RESETS = ("no_reset", "day", "week", "month", "year")
TARGETS = ("expired", "limited", "on_hold", "disabled")
BULK = ("enable", "disable", "delete", "reset", "revoke_sub")
MAX_BULK = 300
MAX_FROM_TEMPLATE = 500
MAX_NOTE = 500
_USERNAME = re.compile(r"^[A-Za-z0-9\-_@.]{3,128}$")
_BASE = re.compile(r"^[A-Za-z0-9\-_@.]{1,128}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------ pure
def clean_username(name) -> str:
    n = str(name or "").strip()

    if not _USERNAME.match(n) or re.search(r"[-_@.]{2,}", n):
        raise ValueError("a username has 3 to 128 letters, digits or - _ @ . (no two of those signs in a row)")

    return n


def check_usernames(names) -> list[str]:
    """A list of 1 to 300 usernames, no doubles (the order is kept)."""
    if not isinstance(names, list) or not names:
        raise ValueError("choose at least one user")

    out: list[str] = []

    for n in names:
        n = clean_username(n)

        if n not in out:
            out.append(n)

    if len(out) > MAX_BULK:
        raise ValueError(f"at most {MAX_BULK} users at a time")

    return out


def day_end(day: str) -> str:
    """The end of a calendar day (YYYY-MM-DD) as the panel wants an end date: UTC 23:59:59, written with the Z."""
    if not _DAY.match(str(day or "")):
        raise ValueError("the date must look like 2027-01-31")

    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise ValueError("that date does not exist") from None

    return f"{day}T23:59:59Z"


def day_of(stamp) -> str:
    """The calendar day (YYYY-MM-DD) of an end date the panel gave (an ISO text or a Unix time), '' when there is none."""
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and stamp > 0:
        return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")

    return str(stamp)[:10] if isinstance(stamp, str) and len(stamp) >= 10 else ""


def _gb(v):
    return None if v in (None, 0) else (int(v / GB) if v % GB == 0 else round(v / GB, 2))


def user_row(u) -> dict:
    """The user as the window shows it: GB and days where the panel has bytes and seconds."""
    u = _dict(u)
    plan = _dict(u.get("next_plan"))
    return {"id": u.get("id"), "username": str(u.get("username") or ""), "status": str(u.get("status") or ""), "used": u.get("used_traffic"), "lifetime": u.get("lifetime_used_traffic"),
            "limit": u.get("data_limit"), "limit_gb": _gb(u.get("data_limit")), "reset": str(u.get("data_limit_reset_strategy") or "no_reset"), "expire": u.get("expire"), "expire_day": day_of(u.get("expire")),
            "note": str(u.get("note") or ""), "on_hold_days": (round(u["on_hold_expire_duration"] / DAY, 2) if isinstance(u.get("on_hold_expire_duration"), (int, float)) and u["on_hold_expire_duration"] else None),
            "on_hold_timeout_day": day_of(u.get("on_hold_timeout")), "group_ids": sorted(x for x in _list(u.get("group_ids")) if isinstance(x, int)), "hwid_limit": u.get("hwid_limit"),
            "auto_delete_days": u.get("auto_delete_in_days"), "created_at": u.get("created_at"), "edit_at": u.get("edit_at"), "online_at": u.get("online_at"), "sub": str(u.get("subscription_url") or ""),
            "admin": str(_dict(u.get("admin")).get("username") or ""), "admin_id": _dict(u.get("admin")).get("id"),
            "next_plan": {"template_id": plan.get("user_template_id"), "data_gb": _gb(plan.get("data_limit")), "expire_days": round(plan["expire"] / DAY, 2) if isinstance(plan.get("expire"), (int, float)) and plan["expire"] else None,
                          "add_remaining": bool(plan.get("add_remaining_traffic"))} if plan else None,
            "plan_raw": {k: plan.get(k) for k in ("user_template_id", "data_limit", "expire")} | {"add_remaining_traffic": bool(plan.get("add_remaining_traffic"))} if plan else None}


def modify_body(data: dict, cur: dict) -> dict:
    """The PUT body for the fields of `data` that differ from `cur` (the user as `user_row` gave it). A field that is not in `data` is never touched; the next plan of the user is
    always sent along (the panel deletes it otherwise). Fields: status, limit_gb, reset, expire_day, note, on_hold_days, on_hold_timeout_day, group_ids, hwid_limit, next_plan."""
    data = _dict(data)
    body: dict = {}
    status = data.get("status", cur.get("status"))

    if "status" in data and data["status"] != cur.get("status"):
        if data["status"] not in STATUSES:                                                       # (limited / expired are worked out by the panel, never set)
            raise ValueError(f"the status is one of: {', '.join(STATUSES)}")

        body["status"] = data["status"]

    if "limit_gb" in data:
        gb = _number(data["limit_gb"] if data["limit_gb"] not in (None, "") else 0, "the data limit", 0, 1_000_000)

        if round(gb, 2) != round(cur.get("limit_gb") or 0, 2):
            body["data_limit"] = int(gb * GB)

    if "reset" in data:
        if data["reset"] not in RESETS:
            raise ValueError(f"the reset period is one of: {', '.join(RESETS)}")

        if data["reset"] != cur.get("reset"):
            body["data_limit_reset_strategy"] = data["reset"]

    if "expire_day" in data:
        day = str(data["expire_day"] or "").strip()

        if status == "on_hold":
            if day:
                raise ValueError("a user on hold has no end date: it starts counting at its first connection")
        elif day != (cur.get("expire_day") or ""):
            body["expire"] = day_end(day) if day else 0                                     # 0 removes the end date (null would leave it)

    if "note" in data:
        note = str(data["note"] or "")

        if len(note) > MAX_NOTE:
            raise ValueError(f"the note is at most {MAX_NOTE} characters")

        if note != cur.get("note", ""):
            body["note"] = note

    days = cur.get("on_hold_days") or 0                                                      # the days after the first connection the user stays valid (the panel keeps it after the user left "on hold")

    if "on_hold_days" in data:
        v = data["on_hold_days"]
        days = 0.0 if v in (None, "") else _number(v, "on hold for", 0, 24855)

    if status == "on_hold":
        if days <= 0:
            raise ValueError("a user on hold needs the number of days it stays valid after its first connection")

        if "on_hold_days" in data and round(days, 2) != round(cur.get("on_hold_days") or 0, 2) or cur.get("status") != "on_hold":
            body["on_hold_expire_duration"] = int(days * DAY)
    elif "on_hold_days" in data and days > 0 and round(days, 2) != round(cur.get("on_hold_days") or 0, 2):
        body["on_hold_expire_duration"] = int(days * DAY)                                   # (a duration of 0 is read by the panel as "leave it": it is never sent)

    if "on_hold_timeout_day" in data:
        day = str(data["on_hold_timeout_day"] or "").strip()

        if day != (cur.get("on_hold_timeout_day") or ""):
            body["on_hold_timeout"] = day_end(day) if day else 0

    if "group_ids" in data:
        ids = data["group_ids"]

        if not isinstance(ids, list) or any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in ids):
            raise ValueError("the groups are a list of group ids")

        if sorted(set(ids)) != cur.get("group_ids"):
            body["group_ids"] = sorted(set(ids))

    if "hwid_limit" in data:
        v = data["hwid_limit"]

        if v not in (None, ""):
            n = int(_number(v, "the device limit", 0, 10 ** 6))

            if n != cur.get("hwid_limit"):
                body["hwid_limit"] = n
        elif cur.get("hwid_limit") is not None:
            raise ValueError("the device limit can be changed but not taken away (the panel keeps it when the field is empty): set a number, or 0")

    plan = _plan_body(data["next_plan"]) if "next_plan" in data else _plan_of(cur)

    if plan is not None:
        body["next_plan"] = plan

    return body


def _plan_of(cur: dict) -> dict | None:
    """The next plan the user has now, exactly as the panel gave it (not the rounded GB and days of the window): it goes back in every PUT."""
    p = cur.get("plan_raw")
    return dict(p) if isinstance(p, dict) else None


def _plan_body(p) -> dict | None:
    """The next plan the page asks for: {template_id, data_gb, expire_days, add_remaining}; an empty one (nothing set) is the way to ask for no plan (None: not sent, the panel then removes it)."""
    p = _dict(p)
    tid = p.get("template_id")
    gb = p.get("data_gb")
    days = p.get("expire_days")

    if tid in (None, "", 0) and gb in (None, "") and days in (None, ""):
        return None

    if tid not in (None, "", 0) and (isinstance(tid, bool) or not isinstance(tid, int) or tid < 1):
        raise ValueError("the template of the next plan is an id")

    if tid:                                                                                  # a plan from a template: the panel takes the data and the duration from the template and ignores these two
        return {"user_template_id": tid, "data_limit": None, "expire": None, "add_remaining_traffic": bool(p.get("add_remaining"))}

    if gb in (None, ""):                                                                     # (the panel adds this number when the plan starts: a plan with no template and no data would fail then)
        raise ValueError("a next plan with no template needs its data in GB (0 = unlimited)")

    return {"user_template_id": None, "data_limit": int(_number(gb, "the data of the next plan", 0, 1_000_000) * GB),
            "expire": int(_number(days, "the days of the next plan", 0, 36500) * DAY) if days not in (None, "") else None, "add_remaining_traffic": bool(p.get("add_remaining"))}


# ------------------------------------------------------------------ reading
def _id_of(c: dict, username: str) -> int:
    """The id of a user by its name (the by-id routes are the ones the panel keeps)."""
    name = clean_username(username)
    r = _dict(_call(c, "GET", "/api/users/simple?" + urlencode({"usernames": name, "limit": 1})))

    for u in _list(r.get("users")):
        if isinstance(u, dict) and u.get("username") == name and isinstance(u.get("id"), int):
            return u["id"]

    raise ValueError(f"no user called {name} on this panel")


def ids_of(c: dict, usernames: list[str]) -> dict[str, int]:
    """{username: id} for every name; a name the panel does not know is an error that names it."""
    names = check_usernames(usernames)
    found: dict[str, int] = {}

    for i in range(0, len(names), 100):
        part = names[i:i + 100]
        r = _dict(_call(c, "GET", "/api/users/simple?" + urlencode([("usernames", n) for n in part] + [("limit", len(part))])))

        for u in _list(r.get("users")):
            if isinstance(u, dict) and isinstance(u.get("id"), int) and u.get("username") in part:
                found[u["username"]] = u["id"]

    missing = [n for n in names if n not in found]

    if missing:
        raise ValueError(f"no user called {', '.join(missing[:5])}{' …' if len(missing) > 5 else ''} on this panel")

    return {n: found[n] for n in names}                                                      # (in the order the names were given)


def _simple(c: dict, path: str, key: str, label: str) -> list[dict]:
    try:
        r = _dict(_call(c, "GET", path + "?all=true"))
    except PanelError:
        return []

    return [{"id": x.get("id"), "name": str(x.get(label) or "")} for x in _list(r.get(key)) if isinstance(x, dict) and x.get("id") is not None]


def options(c: dict) -> dict:
    """What the forms of the Users window and tools choose from: the groups, the user templates and the admins of the panel (id and name)."""
    return {"groups": _simple(c, "/api/groups/simple", "groups", "name"), "templates": _simple(c, "/api/user_templates/simple", "templates", "name"),
            "admins": _simple(c, "/api/admins/simple", "admins", "username")}


def user_view(c: dict, username: str) -> dict:
    """One user in full for the window, with what its forms need (see `options`)."""
    uid = _id_of(c, username)
    u = _dict(_call(c, "GET", f"/api/user/by-id/{uid}"))

    if u.get("id") is None:
        raise ValueError("this user does not exist (any more)")

    return {"user": user_row(u), "options": options(c)}


def devices(c: dict, username: str) -> dict:
    """The devices a user has used: {id, hwid (shortened), os, version, model, first, last}."""
    uid = _id_of(c, username)
    r = _dict(_call(c, "GET", f"/api/user/{uid}/hwids"))
    rows = [{"id": x.get("id"), "hwid": str(x.get("hwid") or ""), "short": (str(x.get("hwid") or "")[:8] + "…" + str(x.get("hwid") or "")[-4:]) if len(str(x.get("hwid") or "")) > 14 else str(x.get("hwid") or ""),
             "os": str(x.get("device_os") or ""), "version": str(x.get("os_version") or ""), "model": str(x.get("device_model") or ""), "first": x.get("created_at"), "last": x.get("last_used_at")}
            for x in _list(r.get("hwids")) if isinstance(x, dict)]
    return {"user_id": uid, "devices": rows, "count": len(rows)}


def sub_updates(c: dict, username: str, offset: int = 0, limit: int = 10) -> dict:
    """The last fetches of a user's subscription: {at, agent, ip, hwid}."""
    q = {"offset": max(0, int(offset)), "limit": min(100, max(1, int(limit)))}
    r = _dict(_call(c, "GET", f"/api/user/by-id/{_id_of(c, username)}/sub_update?{urlencode(q)}"))
    return {"updates": [{"at": x.get("created_at"), "agent": str(x.get("user_agent") or "")[:200], "ip": str(x.get("ip") or ""), "hwid": str(x.get("hwid") or "")[:40]} for x in _list(r.get("updates")) if isinstance(x, dict)],
            "count": r.get("count") if isinstance(r.get("count"), int) else 0}


def online_ips(c: dict, username: str) -> dict:
    """Where a user is connected from right now, on every node: [{node, ip, connections}] (the node by its name when the panel gives it)."""
    uid = _id_of(c, username)
    r = _dict(_call(c, "GET", f"/api/node/online_stats/{uid}/ip"))

    try:
        names = {x.get("id"): str(x.get("name") or "") for x in _list(_dict(_call(c, "GET", "/api/nodes/simple?all=true")).get("nodes")) if isinstance(x, dict)}
    except PanelError:
        names = {}

    rows: list[dict] = []

    for node, v in _dict(r.get("nodes")).items():
        ips = _dict(v).get("ips") if isinstance(_dict(v).get("ips"), dict) else v

        for ip, n in (ips.items() if isinstance(ips, dict) else []):
            nid = int(node) if str(node).isdigit() else node
            rows.append({"node": names.get(nid) or str(node), "ip": str(ip), "connections": n if isinstance(n, int) and not isinstance(n, bool) else 1})

    return {"online": rows, "count": len(rows)}


# ------------------------------------------------------------------ writing one user
def modify(c: dict, username: str, data: dict) -> dict:
    """Change a user. Only the fields that differ from what it has now are sent (and its next plan, always, so the panel does not delete it); the by-id route is used."""
    uid = _id_of(c, username)
    cur = user_row(_call(c, "GET", f"/api/user/by-id/{uid}"))
    body = modify_body(data, cur)
    sent = sorted(k for k in body if k != "next_plan" or "next_plan" in _dict(data))

    if not sent:
        raise ValueError("nothing to change: the user already has these values")

    made = _call(c, "PUT", f"/api/user/by-id/{uid}", body=body)
    audit(c, f"user {cur['username']} changed ({', '.join(sent)})")
    return {"user": user_row(made), "changed": sent}


def action(c: dict, username: str, what: str, value=None, confirm: bool = False) -> dict:
    """One action on one user: reset (usage to zero), revoke_sub (a new subscription link: the old one stops), active_next (start the next plan now), set_owner (value: the admin's username),
    disable / enable, delete (needs confirm)."""
    uid = _id_of(c, username)
    name = clean_username(username)

    if what == "reset":
        _call(c, "POST", f"/api/user/by-id/{uid}/reset", body={})
    elif what == "revoke_sub":
        _call(c, "POST", f"/api/user/by-id/{uid}/revoke_sub", body={})
    elif what == "active_next":
        _call(c, "POST", f"/api/user/by-id/{uid}/active_next", body={})
    elif what == "set_owner":
        who = str(value or "").strip()

        if not who:
            raise ValueError("choose the admin that becomes the owner")

        _call(c, "PUT", f"/api/user/by-id/{uid}/set_owner?{urlencode({'admin_username': who})}")
    elif what in ("disable", "enable"):
        _call(c, "PUT", f"/api/user/by-id/{uid}/disabled", body={"disabled": what == "disable"})
    elif what == "delete":
        if not confirm:
            raise ValueError("deleting a user cannot be undone: send confirm: true")

        _call(c, "DELETE", f"/api/user/by-id/{uid}")
    else:
        raise ValueError("the action is one of: reset, revoke_sub, active_next, set_owner, enable, disable, delete")

    audit(c, f"user {name}: {what.replace('_', ' ')}" + (f" → {value}" if what == "set_owner" else ""), "warn" if what in ("delete", "revoke_sub") else "info")
    return {"user": name, "action": what}


def device_delete(c: dict, username: str, hwid: str) -> dict:
    """Remove one device (by the device id the panel gave: it is a text, written into the path)."""
    uid = _id_of(c, username)
    h = str(hwid or "")

    if not 1 <= len(h) <= 200:
        raise ValueError("choose the device")

    _call(c, "DELETE", f"/api/user/{uid}/hwids/{quote(h, safe='')}")
    audit(c, f"user {clean_username(username)}: a device was removed")
    return {"removed": True}


def devices_reset(c: dict, username: str) -> dict:
    uid = _id_of(c, username)
    _call(c, "POST", f"/api/user/{uid}/hwids/reset", body={})
    audit(c, f"user {clean_username(username)}: all devices were removed")
    return {"reset": True}


# ------------------------------------------------------------------ several users
def bulk(c: dict, what: str, usernames, value=None, confirm: bool = False) -> dict:
    """The panel's own bulk routes for the chosen users (by name; their ids are looked up): enable, disable, delete (confirm), reset, revoke_sub (confirm), set_owner (value: the admin),
    apply_template (value: the template id)."""
    names = check_usernames(usernames)
    ids = list(ids_of(c, names).values())

    if what in ("delete", "revoke_sub", "reset") and not confirm:
        raise ValueError({"delete": "deleting users cannot be undone: send confirm: true", "revoke_sub": "this makes new subscription links: the old ones stop working: send confirm: true",
                          "reset": "this sets the used traffic of every chosen user to zero: send confirm: true"}[what])

    if what in BULK:
        r = _call(c, "POST", f"/api/users/bulk/{what}", body={"ids": ids}, timeout=120)
    elif what == "set_owner":
        who = str(value or "").strip()

        if not who:
            raise ValueError("choose the admin that becomes the owner")

        r = _call(c, "PUT", "/api/users/bulk/set_owner", body={"ids": ids, "admin_username": who}, timeout=120)
    elif what == "apply_template":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("choose the template")

        r = _call(c, "POST", "/api/users/bulk/apply_template", body={"user_template_id": value, "ids": ids}, timeout=120)
    else:
        raise ValueError("the action is one of: enable, disable, delete, reset, revoke_sub, set_owner, apply_template")

    count = r.get("count") if isinstance(r, dict) and isinstance(r.get("count"), int) else len(ids)
    audit(c, f"{what.replace('_', ' ')} done for {count} user{'s' if count != 1 else ''} ({', '.join(names[:5])}{', …' if len(names) > 5 else ''})", "warn" if what in ("delete", "revoke_sub") else "info")
    return {"action": what, "count": count, "users": names}


STATUS_FILTERS = ("active", "disabled", "limited", "expired", "on_hold")


def _admin_ids(c: dict, names: list) -> list[int]:
    """The panel's ids for admin usernames (the bulk routes filter by admin id)."""
    names = [clean_username(n) for n in names]
    r = _dict(_call(c, "GET", "/api/admins/simple?" + urlencode([("usernames", n) for n in names] + [("all", "true")])))
    found = {a.get("username"): a.get("id") for a in _list(r.get("admins")) if isinstance(a, dict) and isinstance(a.get("id"), int)}
    missing = [n for n in names if n not in found]

    if missing:
        raise ValueError(f"no admin called {', '.join(missing[:5])} on this panel")

    return [found[n] for n in names]


def filter_body(c: dict, filters: dict | None, dry_run: bool) -> dict:
    """The part of a bulk body that says WHOM: `dry_run` and the filters the panel's bulk routes share - statuses, groups (ids), admins and users (names, looked up here), and a range of end
    dates (YYYY-MM-DD). At least one filter: the panel would change every user otherwise."""
    f = _dict(filters)
    body: dict = {"dry_run": bool(dry_run)}

    if f.get("status"):
        st = f["status"] if isinstance(f["status"], list) else [f["status"]]

        if any(s not in STATUS_FILTERS for s in st):
            raise ValueError(f"the status is one of: {', '.join(STATUS_FILTERS)}")

        body["status"] = st

    if f.get("group_ids"):
        if not isinstance(f["group_ids"], list) or any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in f["group_ids"]):
            raise ValueError("the groups are a list of group ids")

        body["group_ids"] = f["group_ids"]

    if f.get("admins"):
        body["admins"] = _admin_ids(c, f["admins"] if isinstance(f["admins"], list) else [f["admins"]])

    if f.get("usernames"):
        body["users"] = list(ids_of(c, f["usernames"]).values())

    if f.get("expire_after_day"):
        day_end(f["expire_after_day"])
        body["expire_after"] = f"{f['expire_after_day']}T00:00:00Z"

    if f.get("expire_before_day"):
        body["expire_before"] = day_end(f["expire_before_day"])

    if len(body) <= 1:
        raise ValueError("choose whom to change: a status, a group, an admin, an end date range, or ticked users")

    return body


def shift_body(c: dict, what: str, amount, filters: dict | None, dry_run: bool) -> dict:
    """The body of the bulk change of the end date (`expire`, days) or the data limit (`data_limit`, GB): `amount` added to (negative: taken from) every user the filters match."""
    if what not in ("expire", "data_limit"):
        raise ValueError("shift the end date (expire) or the data limit (data_limit)")

    hi = 36500 if what == "expire" else 1_000_000
    n = _number(amount, "the amount", -hi, hi)

    if n == 0:
        raise ValueError("the amount is not zero")

    body = filter_body(c, filters, dry_run)
    body["amount"] = int(n * (DAY if what == "expire" else GB))
    return body


def shift(c: dict, what: str, amount, filters: dict | None = None, dry_run: bool = True, confirm: bool = False) -> dict:
    """Add (or take away) days to the END date or GB to the DATA LIMIT of every user the filters match. The dry run (the default) only counts whom it would touch; the real run needs `confirm`."""
    body = shift_body(c, what, amount, filters, dry_run)

    if not dry_run and not confirm:
        raise ValueError("this changes every user that matches: look at the count of the dry run first, then send confirm: true")

    r = _dict(_call(c, "POST", f"/api/users/bulk/{what}", body=body, timeout=180))
    m = re.search(r"\b(\d+)\s+users?\b", str(r.get("detail") or ""))
    count = r["affected_users"] if isinstance(r.get("affected_users"), int) else int(m.group(1)) if m else None

    if not dry_run:
        audit(c, f"{'end date' if what == 'expire' else 'data limit'} shifted by {float(amount):g} {'days' if what == 'expire' else 'GB'} for {count if count is not None else 'the matching'} users", "warn")

    return {"what": what, "dry_run": bool(dry_run), "count": count}


SS_METHODS = ("aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "xchacha20-poly1305")


def proxy_method(c: dict, method: str, filters: dict | None = None, dry_run: bool = True, confirm: bool = False) -> dict:
    """Change the SHADOWSOCKS cipher (method) of every user the filters match (`POST /api/users/bulk/proxy_settings`): the user's own password stays, only the method changes, and a client
    has to take the subscription again. The dry run (the default) only counts whom it would touch; the real run needs `confirm`."""
    if method not in SS_METHODS:
        raise ValueError(f"the method is one of: {', '.join(SS_METHODS)}")

    body = filter_body(c, filters, dry_run)
    body["method"] = method

    if not dry_run and not confirm:
        raise ValueError("this changes every user that matches: look at the count of the dry run first, then send confirm: true")

    r = _dict(_call(c, "POST", "/api/users/bulk/proxy_settings", body=body, timeout=180))
    m = re.search(r"\b(\d+)\s+users?\b", str(r.get("detail") or ""))
    count = r["affected_users"] if isinstance(r.get("affected_users"), int) else int(m.group(1)) if m else None

    if not dry_run:
        audit(c, f"shadowsocks method set to {method} for {count if count is not None else 'the matching'} users", "warn")

    return {"method": method, "dry_run": bool(dry_run), "count": count}


FORMATS = ("links", "links_base64", "xray", "sing_box", "clash", "clash_meta", "outline", "wireguard")      # (the panel lists `block` too and answers 406 for it)
MAX_SUB_TEXT = 400_000


def subscription(c: dict, username: str, fmt: str) -> dict:
    """The subscription of ONE user in ONE client format, as the panel renders it (`GET /api/user/{id}/subscription/{format}`): links, Xray JSON, sing-box, Clash, Clash Meta, Outline are text
    (cut at 400 KB); WireGuard is a zip, so only its size and file are reported (download it from the link of the user). Holds the user's own secrets, like its link: it is not logged."""
    if fmt not in FORMATS:
        raise ValueError(f"the format is one of: {', '.join(FORMATS)}")

    name = clean_username(username)
    uid = _id_of(c, name)

    try:
        ctype, raw = panels.call_raw(c, "GET", f"/api/user/{uid}/subscription/{fmt}", timeout=60)
    except PanelError as e:
        if e.code == 406:
            raise ValueError("the panel does not render this format") from None

        raise

    if fmt == "wireguard" or "zip" in ctype:
        return {"format": fmt, "type": ctype, "size": len(raw), "binary": True, "text": "", "truncated": False, "empty": len(raw) <= 22}      # an empty zip is 22 bytes

    text = raw.decode("utf-8", "replace")
    return {"format": fmt, "type": ctype.split(";")[0].strip(), "size": len(raw), "binary": False, "text": text[:MAX_SUB_TEXT], "truncated": len(text) > MAX_SUB_TEXT, "empty": not text.strip()}


def expired(c: dict, target: str = "expired", admin: str | None = None, before_day: str | None = None, after_day: str | None = None, delete: bool = False, typed: str = "") -> dict:
    """The users in one state (expired, limited, on_hold, disabled), for an admin and a time range - the list, or (`delete`, with the target typed back) their removal for good."""
    if target not in TARGETS:
        raise ValueError(f"the state is one of: {', '.join(TARGETS)}")

    q: dict = {"target": target}

    if admin:
        q["admin_username"] = clean_username(admin)

    if before_day:
        q["expired_before"] = day_end(before_day)

    if after_day:
        day_end(after_day)
        q["expired_after"] = f"{after_day}T00:00:00Z"

    if delete:
        if str(typed or "").strip() != target:
            raise ValueError(f"type the state ({target}) to confirm: this deletes those users for good")

        r = _dict(_call(c, "DELETE", "/api/users/expired?" + urlencode(q), timeout=180))
        names = [str(x) for x in _list(r.get("users"))]
        audit(c, f"{len(names)} {target} users deleted", "warn")
        return {"deleted": True, "count": r.get("count") if isinstance(r.get("count"), int) else len(names), "users": names[:200]}

    r = _call(c, "GET", "/api/users/expired?" + urlencode(q))
    names = [str(x) for x in (r if isinstance(r, list) else _list(_dict(r).get("users")))]
    return {"deleted": False, "count": len(names), "users": names[:200], "truncated": len(names) > 200}


def _template_id(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValueError("choose the template")

    return v


def from_template(c: dict, template_id: int, username: str, note: str = "") -> dict:
    """Make one user from a user template (the template decides the groups, the limit, the duration and the name's prefix and suffix)."""
    body = {"user_template_id": _template_id(template_id), "username": clean_username(username), "note": str(note or "")[:MAX_NOTE] or None}
    made = _dict(_call(c, "POST", "/api/user/from_template", body=body))
    audit(c, f"user {body['username']} made from template #{body['user_template_id']}")
    return {"user": user_row(made)}


def bulk_from_template(c: dict, template_id: int, count: int, strategy: str = "random", base: str = "", start_number: int | None = None, note: str = "") -> dict:
    """Make many users from one template (at most 500 at a time). Names: `random` (the panel makes them, the template's prefix and suffix around them) or `sequence` (`base` and a number
    counted up from `start_number`, 1 when empty). Returns how many were made and their subscription links."""
    n = int(_number(count, "how many users", 1, MAX_FROM_TEMPLATE))

    if strategy not in ("random", "sequence"):
        raise ValueError("the names are random or sequence")

    body: dict = {"user_template_id": _template_id(template_id), "count": n, "strategy": strategy, "note": str(note or "")[:MAX_NOTE] or None}

    if strategy == "sequence":
        b = str(base or "").strip()

        if not _BASE.match(b) or re.search(r"[-_@.]{2,}", b):
            raise ValueError("the base of the names has letters, digits or - _ @ . (no two of those signs in a row)")

        body["username"] = b
        body["start_number"] = int(_number(start_number if start_number not in (None, "") else 1, "the first number", 0, 10 ** 9))

    r = _dict(_call(c, "POST", "/api/users/bulk/from_template", body=body, timeout=300))
    made = r.get("created") if isinstance(r.get("created"), int) else n
    audit(c, f"{made} users made from template #{body['user_template_id']}")
    return {"created": made, "links": [str(x) for x in _list(r.get("subscription_urls"))[:500]]}
