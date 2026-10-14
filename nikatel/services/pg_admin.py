"""PasarGuard: the admins, the API keys and the settings of a PasarGuard panel (roadmap #94). Written against the REST API of PasarGuard 5.x (read on a live 5.4.1
panel, and checked against the panel's own pydantic models) through services/panels.py (`panels.call` logs in by itself). Every change that is made leaves one
sentence in the events of Nikatel (`audit`: names and counts, never a secret).

ADMINS. An admin of this PasarGuard has a ROLE (owner, administrator, operator ...), not an `is_sudo` flag: a new admin needs a `role_id`, and the owner role can
never be given or taken from outside. The panel has no "what to do with the users" question when an admin is deleted: its users simply lose their owner, so
`admin_delete` offers to keep them, to switch them off first, or to delete them with the admin (typed confirmation). Two admins are protected here because
a mistake costs the most: the one Nikatel itself logs in with (deleting, disabling or re-passwording it would lock this panel out) and every owner.
The usage of an admin is asked with Tehran days: a start with the +03:30 offset makes the panel cut its day buckets at Tehran midnight (20:30 UTC of the day before).

API KEYS. A key acts as its admin (`X-Api-Key: pg_key_<uuid>`), with the rights of that admin (`inherit_permissions`) or only with the list stored on the key. The secret of a
key exists in ONE answer of the panel (the one to the create call, and to a revoke, which makes a new secret): it is handed on once and never kept, logged or backed up.

SETTINGS. `PUT /api/settings` REPLACES a whole section (the fields that are null are dropped), so a section is always written as read-modify-write: the live section
is read, the fields the operator changed are laid over it, and the whole section is sent. That is also how a secret (bot token, webhook secret, any key) can
"stay": the browser never gets it, it is only shown as set / not set, and the server sends back the value it has just read unless a new one was typed. Before each
write the settings are saved to a file WITHOUT secrets (data dir / pg-backups, the last 20 are kept) and `settings_restore` puts the non-secret values of a backup
back (the secrets that are set now stay as they are)."""
from __future__ import annotations

import copy
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

from sqlalchemy.exc import SQLAlchemyError

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Panel
from . import panels
from .events import emit
from .panels import PanelError

log = get_logger("pg_admin")
GB = 1024 ** 3
DAY = 86400
TEHRAN = timezone(timedelta(hours=3, minutes=30))
KEEP_BACKUPS = 20
PAGE = 100
MAX_PAGES = 10
MAX_RANGE_DAYS = 400
USERS_MODES = ("keep", "disable", "delete")
_USERNAME = re.compile(r"[A-Za-z0-9_.@\-]{3,34}")
_SPECIAL = frozenset("!@#$%^&*()-_=+[]{}|;:,.<>?/~`")
_NOTIFY = ("create", "modify", "delete", "status_change", "reset_data_usage", "data_reset_by_next", "subscription_revoked")
# (name in our API, name in the panel, unit in the panel): data limits are bytes (we speak GB), expire / on-hold limits are seconds (we speak days)
_OVERRIDES = (("max_users", "max_users", 1), ("data_limit_min_gb", "data_limit_min", GB), ("data_limit_max_gb", "data_limit_max", GB),
              ("expire_min_days", "expire_min", DAY), ("expire_max_days", "expire_max", DAY), ("min_hwid_per_user", "min_hwid_per_user", 1),
              ("max_hwid_per_user", "max_hwid_per_user", 1), ("on_hold_timeout_min_days", "on_hold_timeout_min", DAY), ("on_hold_timeout_max_days", "on_hold_timeout_max", DAY))
_TEXT_LIMITS = {"note": 500, "sub_domain": 256, "sub_template": 1024, "profile_title": 512, "support_url": 1024}


# ------------------------------------------------------------------ talking to the panel
def _clean(msg: str, secrets: tuple[str, ...] = ()) -> str:
    """The reason of a refusal as one readable line: PasarGuard answers a 422 with a list that panels.call has cut into a Python repr (it even repeats the value that
    was refused), so the `loc` and `msg` of each entry are taken out of it, and no secret we sent is ever left in the text."""
    text = re.sub(r"""'input': ('[^']*'?|"[^"]*"?)""", "'input': ***", str(msg))
    found = re.findall(r"'loc': \[([^\]]*)\], 'msg': '([^']*)", text)

    if found:
        parts = []

        for loc, m in found:
            names = [x.strip().strip("'") for x in loc.split(",")]
            where = ".".join(x for x in names if x and x not in ("body", "query", "path"))
            parts.append((where + ": " if where else "") + re.sub(r"^Value error, ", "", m))

        text = text.split(":", 1)[0] + ": " + "; ".join(parts)

    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "***")

    return text[:240]


def _call(c: dict, method: str, path: str, secrets: tuple[str, ...] = (), **kw):
    """panels.call with the refusal text cleaned (see _clean); the error carries the same HTTP code as the panel's."""
    try:
        return panels.call(c, method, path, **kw)
    except PanelError as e:
        raise PanelError(_clean(str(e), secrets), e.code) from None


class BadInput(ValueError):
    """A value of the wrong kind (text where a number is needed, a list that is not a list ...). Every ValueError of this module becomes an HTTP 400; this one says
    what kind of mistake it was."""


def audit(c: dict, text: str, level: str = "info") -> None:
    """One sentence in the events of Nikatel (the Activity of the dashboard) for a change that was made on the PasarGuard panel: the panel keeps its own log, this one
    says that it was done from here. Names and counts only, never a secret. It can not fail the change, which has already been made."""
    try:
        with session() as db:
            p = db.get(Panel, int(c.get("id") or 0))

            if p is not None:                                  # (a credential that is not a saved panel has no events)
                emit(db, level, "panels", f"PasarGuard {p.name}: {text}", p.name)
    except SQLAlchemyError as e:                                # boundary: an event that can not be written must not turn a done change into an error
        log.warning("could not write the event for a change on panel %s: %s", c.get("id"), e)


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _list(v) -> list:
    return v if isinstance(v, list) else []


# ------------------------------------------------------------------ backups (one folder for the whole PasarGuard area)
def _backup_dir():
    d = get_settings().data_dir / "pg-backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _found_backups(prefix: str) -> list[tuple[str, int, object]]:
    """(date-time, number, path) of the files of ONE kind, oldest first. The name must be exactly <prefix>-<date>-<time>[-n].json: a prefix like panel2-admin-ab also
    starts the files of the admin ab-cd, and the files other tabs keep in the same folder are never ours."""
    pat = re.compile(re.escape(prefix) + r"-(\d{8}-\d{6})(?:-(\d+))?\.json")
    found = [(m.group(1), int(m.group(2) or 1), p) for p in _backup_dir().iterdir() if (m := pat.fullmatch(p.name))]
    return sorted(found, key=lambda x: (x[0], x[1]))


def _backups_of(prefix: str) -> list:
    return [p for _stamp, _n, p in _found_backups(prefix)]


def _write_backup(prefix: str, doc: dict) -> str:
    """Write `doc` as <prefix>-<time>[-n].json and keep the last KEEP_BACKUPS of this kind; returns the file name. Several files in one second are numbered upward
    from the highest number that exists, never into a hole the rotation left (that file would count as the oldest and be removed at once)."""
    d = _backup_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    n = max([k for s, k, _p in _found_backups(prefix) if s == stamp], default=0) + 1

    while True:
        name = f"{prefix}-{stamp}.json" if n == 1 else f"{prefix}-{stamp}-{n}.json"

        try:
            with open(d / name, "x", encoding="utf-8") as fh:
                fh.write(json.dumps(doc, ensure_ascii=False))

            break
        except FileExistsError:
            n += 1

    for p in _backups_of(prefix)[:-KEEP_BACKUPS]:
        p.unlink()

    return name


# ------------------------------------------------------------------ admins: pure helpers
def clean_username(name) -> str:
    n = str(name or "").strip()

    if not _USERNAME.fullmatch(n):
        raise ValueError("a username has 3 to 34 letters, digits or _ . @ -")

    return n


def password_problems(password: str, username: str = "") -> list[str]:
    """Every rule of the panel's password check that the password breaks (the panel's own sentences, so the operator sees all of them at once): 12 characters at
    least, 72 bytes at most, 2 digits, 2 capitals, 2 small letters, one special character, no double quote, not the username inside."""
    pw = str(password or "")
    out = []

    if len(pw.encode("utf-8")) > 72:
        out.append("Password too long: maximum 72 bytes when UTF-8 encoded")

    if len(pw) < 12:
        out.append("Password must be at least 12 characters long")

    if len(re.findall(r"\d", pw)) < 2:
        out.append("Password must contain at least 2 digits")

    if len(re.findall(r"[A-Z]", pw)) < 2:
        out.append("Password must contain at least 2 uppercase letters")

    if len(re.findall(r"[a-z]", pw)) < 2:
        out.append("Password must contain at least 2 lowercase letters")

    if not any(ch in _SPECIAL for ch in pw):
        out.append("Password must contain at least one special character")

    if username and username.lower() in pw.lower():
        out.append("Password cannot contain the username")

    if '"' in pw:
        out.append('Password cannot contain the double quote (") character')

    return out


def tehran_range(start: str, end: str) -> dict:
    """The Tehran days from `start` to `end` (YYYY-MM-DD, both included). A Tehran day begins at 00:00 +03:30 = 20:30 UTC of the day before; the panel cuts its
    `day` buckets by the offset of the start it is given, so both ends carry +03:30 (and the same offset, which the panel insists on)."""
    try:
        d0, d1 = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except ValueError:
        raise ValueError("the dates must look like 2026-10-31") from None

    if d1 < d0:
        raise ValueError("the first day must not be after the last day")

    if (d1 - d0).days >= MAX_RANGE_DAYS:
        raise ValueError(f"at most {MAX_RANGE_DAYS} days at a time")

    first = datetime(d0.year, d0.month, d0.day, tzinfo=TEHRAN)
    last = datetime(d1.year, d1.month, d1.day, 23, 59, 59, tzinfo=TEHRAN)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    return {"start": first.isoformat(), "end": last.isoformat(), "from_utc": first.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to_utc": last.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "days": days}


def usage_days(raw, days: list[str]) -> list[dict]:
    """The bytes of every Tehran day of `days` from the panel's usage answer ({"stats": {node: [{period_start, total_traffic}]}}); a day the panel left out is 0."""
    totals = dict.fromkeys(days, 0)

    for series in _dict(_dict(raw).get("stats")).values():
        for p in _list(series):
            stamp = str(_dict(p).get("period_start") or "")

            try:
                day = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(TEHRAN).date().isoformat()
            except ValueError:
                continue

            if day in totals:
                totals[day] += int(_dict(p).get("total_traffic") or 0)

    return [{"date": d, "bytes": totals[d]} for d in days]


def role_of(a: dict) -> dict:
    """The role of an admin; `disconnects` says whether the role cuts the users off when their admin is switched off (the confirmation must say so)."""
    r = _dict(a.get("role"))

    if r:
        return {"id": r.get("id"), "name": str(r.get("name") or ""), "is_owner": bool(r.get("is_owner")), "disconnects": bool(r.get("disconnect_users_when_disabled"))}

    if a.get("is_sudo") is not None:                      # a PasarGuard from before the roles
        return {"id": None, "name": "sudo" if a.get("is_sudo") else "admin", "is_owner": False, "disconnects": False}

    return {"id": None, "name": "", "is_owner": False, "disconnects": False}


def _units(v, unit: int):
    if v is None:
        return None

    n = v / unit
    return int(n) if n == int(n) else round(n, 2)


def admin_row(a: dict, me: str = "") -> dict:
    """One admin as the tab shows it. The Discord webhook of an admin is a secret: only whether it is set leaves this module."""
    name = str(a.get("username") or "")
    status = str(a.get("status") or ("disabled" if a.get("is_disabled") else "limited" if a.get("is_limited") else "active"))
    role = role_of(a)
    ne = _dict(a.get("notification_enable"))
    po = _dict(a.get("permission_overrides"))
    limit = a.get("data_limit")
    return {"id": a.get("id"), "username": name, "status": status, "role": role, "is_owner": role["is_owner"], "me": bool(me) and name == me,
            "users": int(a.get("total_users") or 0), "used": int(a.get("used_traffic") or 0), "limit": limit if isinstance(limit, (int, float)) and limit > 0 else None,
            "lifetime": a.get("lifetime_used_traffic"), "telegram_id": a.get("telegram_id"), "has_discord": bool(a.get("discord_webhook")),
            "note": str(a.get("note") or ""), "sub_domain": str(a.get("sub_domain") or ""), "sub_template": str(a.get("sub_template") or ""),
            "profile_title": str(a.get("profile_title") or ""), "support_url": str(a.get("support_url") or ""),
            "notify": {k: bool(ne.get(k, True)) for k in _NOTIFY}, "overrides": {api: _units(po.get(key), unit) for api, key, unit in _OVERRIDES}}


def _number(v, label: str, lo: float, hi: float) -> float:
    if isinstance(v, bool):
        raise BadInput(f"{label}: must be a number")

    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: must be a number") from None

    if not lo <= n <= hi:
        raise ValueError(f"{label}: must be between {lo:g} and {hi:g}")

    return n


def _is_discord(s: str) -> bool:
    try:
        u = urlsplit(s)
    except ValueError:
        return False

    return u.scheme == "https" and u.hostname == "discord.com"


def fields_body(data: dict, cur: dict | None = None) -> dict:
    """The body for the optional fields of `data` that DIFFER from `cur` (the admin as `admin_row` gave it; None = a new admin). A field that is missing from `data`
    is never touched. Units: GB for data limits, days for expire and on-hold limits. The panel can not take a Telegram id away, so asking for that is refused."""
    cur = cur or {}
    body: dict = {}

    if data.get("data_limit_gb") is not None:
        gb = _number(data["data_limit_gb"], "Data limit", 0, 1_000_000)

        if round(gb, 2) != round((cur.get("limit") or 0) / GB, 2):          # the tab shows two decimals: an unchanged field must not rewrite the limit
            body["data_limit"] = int(gb * GB)               # 0 = unlimited: the panel turns anything <= 0 into "no limit"

    if "telegram_id" in data:
        tid = data["telegram_id"]

        if tid in (None, ""):
            if cur.get("telegram_id"):
                raise ValueError("the Telegram id can be changed but not removed (the panel keeps it when the field is empty)")
        else:
            n = int(_number(tid, "Telegram id", 1, 2 ** 53))

            if n != cur.get("telegram_id"):
                body["telegram_id"] = n

    hook = str(data.get("discord_webhook") or "").strip()

    if hook:
        if not _is_discord(hook):
            raise ValueError("Discord webhook: must be an https://discord.com/... address")

        body["discord_webhook"] = hook
    elif data.get("clear_discord") and cur.get("has_discord", True):
        body["discord_webhook"] = ""

    for key, limit in _TEXT_LIMITS.items():
        if data.get(key) is not None:
            s = str(data[key]).strip()

            if len(s) > limit:
                raise ValueError(f"{key}: at most {limit} characters")

            if s != cur.get(key, ""):
                body[key] = s

    if data.get("role_id") is not None and data["role_id"] != _dict(cur.get("role")).get("id"):
        body["role_id"] = int(data["role_id"])

    if isinstance(data.get("notify"), dict):
        new = {k: bool(data["notify"].get(k, _dict(cur.get("notify")).get(k, True))) for k in _NOTIFY}

        if new != {k: bool(_dict(cur.get("notify")).get(k, True)) for k in _NOTIFY}:
            body["notification_enable"] = new

    if isinstance(data.get("overrides"), dict):
        want = overrides_body(data["overrides"])

        if want != overrides_body(_dict(cur.get("overrides"))):
            body["permission_overrides"] = want

    return body


def overrides_body(ov: dict) -> dict:
    """The nine per-admin limits for the panel (a limit that is empty stays null = the role's own limit decides)."""
    out: dict = {}

    for api, key, unit in _OVERRIDES:
        v = ov.get(api)
        out[key] = None if v in (None, "") else int(_number(v, api, 0, 10 ** 9) * unit)

    return out


# ------------------------------------------------------------------ admins: reading
def _admins_of(r) -> list[dict]:
    lst = r.get("admins") if isinstance(r, dict) else r
    return [a for a in _list(lst) if isinstance(a, dict)]


def roles(c: dict) -> tuple[list[dict], bool]:
    """(the roles an admin can have, whether the panel has roles at all / lets us read them)."""
    try:
        r = _call(c, "GET", "/api/admin-roles/simple")
    except PanelError:
        return [], False

    lst = r.get("roles") if isinstance(r, dict) else r
    return [{"id": x.get("id"), "name": str(x.get("name") or ""), "is_owner": bool(x.get("is_owner"))} for x in _list(lst) if isinstance(x, dict) and x.get("id") is not None], True


def login_admin(c: dict) -> dict:
    """The admin the panel sees us as (`GET /api/admin`): with a password login that is the stored user, with an API key it is the admin that owns the key."""
    try:
        return _dict(_call(c, "GET", "/api/admin"))
    except PanelError:
        return {}


def _login_name(c: dict) -> str:
    return str(login_admin(c).get("username") or c.get("username") or "")


def _all_admins(c: dict) -> tuple[list[dict], dict]:
    """Every admin as the panel lists it, and the first answer (it has the counts). Paged by username: without an order the panel's pages skip and repeat rows."""
    rows: list[dict] = []
    head: dict = {}
    seen: set = set()

    for page in range(MAX_PAGES):
        r = _call(c, "GET", "/api/admins?" + urlencode({"offset": page * PAGE, "limit": PAGE, "sort": "username"}))
        got = [a for a in _admins_of(r) if a.get("id") not in seen]
        head = head or (r if isinstance(r, dict) else {})
        seen.update(a.get("id") for a in got)
        rows += got

        if len(got) < PAGE:
            break

    return rows, head


def _count(rows: list[dict], status: str) -> int:
    return sum(1 for x in rows if x["status"] == status)


def admins_list(c: dict) -> dict:
    """Every admin, the counts, the admin Nikatel is signed in as, and the roles."""
    raw, head = _all_admins(c)
    me = login_admin(c)
    mine = str(me.get("username") or c.get("username") or "")
    rows = [admin_row(a, mine) for a in raw]
    rl, supported = roles(c)
    total = head.get("total") if isinstance(head.get("total"), int) else len(rows)
    return {"admins": rows, "total": total, "active": head.get("active", _count(rows, "active")), "disabled": head.get("disabled", _count(rows, "disabled")),
            "limited": head.get("limited", _count(rows, "limited")), "me": {"username": mine, "role": role_of(me), "is_owner": role_of(me)["is_owner"]},
            "roles": rl, "roles_supported": supported, "truncated": total > len(rows)}


def _find(c: dict, admin_id: int) -> dict:
    """One admin as listed (the panel has no single-admin read), by its id."""
    r = _call(c, "GET", "/api/admins?" + urlencode({"ids": int(admin_id)}))

    for a in _admins_of(r):
        if a.get("id") == int(admin_id):
            return a

    for a in _all_admins(c)[0]:                        # a panel that ignores `ids` answers with the first page only
        if a.get("id") == int(admin_id):
            return a

    raise ValueError("this admin does not exist (any more)")


def _target(c: dict, admin_id: int) -> dict:
    """The admin to act on, as admin_row gives it, marked `me` when it is the one Nikatel is signed in as."""
    return admin_row(_find(c, admin_id), _login_name(c))


def _admin_call(c: dict, method: str, a: dict, tail: str = "", secrets: tuple[str, ...] = (), **kw):
    """The call on one admin by id; a panel from before the by-id routes ("Not Found" / "Method Not Allowed" for the route itself) is asked by username instead."""
    try:
        return _call(c, method, f"/api/admin/by-id/{int(a['id'])}{tail}", secrets, **kw)
    except PanelError as e:
        if e.code == 405 or str(e).endswith(": Not Found"):
            return _call(c, method, f"/api/admin/{quote(a['username'], safe='')}{tail}", secrets, **kw)

        raise


# ------------------------------------------------------------------ admins: writing
def admin_create(c: dict, data: dict) -> dict:
    """A new admin. The password is checked here against the panel's rules (all the broken ones at once), travels only in the request to the panel, and is not
    kept, logged or returned."""
    username = clean_username(data.get("username"))
    password = str(data.get("password") or "")
    bad = password_problems(password, username)

    if bad:
        raise ValueError("; ".join(bad))

    body: dict = {"username": username, "password": password}
    rid = data.get("role_id")
    rl, supported = roles(c)

    if rid is not None:
        role = next((r for r in rl if r["id"] == rid), None)

        if supported and role is None:
            raise ValueError("the panel has no such role")

        if role and role["is_owner"]:
            raise ValueError("the owner role can not be given from here (the panel only sets it up once)")

        body["role_id"] = int(rid)
    elif supported:
        raise ValueError("pick a role for the new admin")
    else:
        body["is_sudo"] = bool(data.get("is_sudo"))

    body.update(fields_body(data))
    made = _call(c, "POST", "/api/admin", (password, str(body.get("discord_webhook") or "")), body=body)              # the singular: POST /api/admins is not a route (405)
    row = admin_row(_dict(made))
    audit(c, f"admin {username} created (role {row['role']['name'] or 'none'})")
    return row


def _row_after(done, a: dict, **changed) -> dict:
    """The admin as the panel answered a write (else the old row with what we know changed), still marked `me` when it is."""
    return admin_row(_dict(done), a["username"] if a["me"] else "") if _dict(done) else {**a, **changed}


def admin_update(c: dict, admin_id: int, data: dict) -> dict:
    """Change the fields of `data` that differ from what the admin has now; the password has its own call."""
    a = _target(c, admin_id)
    new_role = data.get("role_id")

    if new_role is not None and new_role != a["role"]["id"]:
        if a["me"]:
            raise ValueError("this is the admin Nikatel is signed in as: its role is not changed from here")

        if a["is_owner"]:
            raise ValueError("the role of the owner can not be changed")

    body = fields_body(data, a)

    if not body:
        raise ValueError("nothing to change")

    done = _admin_call(c, "PUT", a, "", (str(body.get("discord_webhook") or ""),), body=body)
    audit(c, f"admin {a['username']} changed ({', '.join(sorted(body))})")
    return _row_after(done, a)


def admin_password(c: dict, admin_id: int, password: str) -> None:
    """Give an admin a new password. Never the one Nikatel is signed in as: the saved password of this panel would stop working and nothing could undo it from here."""
    a = _target(c, admin_id)

    if a["me"]:
        raise ValueError("this is the admin Nikatel is signed in as: change its password on the panel, then in Edit panel here")

    bad = password_problems(password, a["username"])

    if bad:
        raise ValueError("; ".join(bad))

    _admin_call(c, "PUT", a, "", (password,), body={"password": password})
    audit(c, f"password of admin {a['username']} changed")


def admin_status(c: dict, admin_id: int, status: str) -> dict:
    """Switch an admin on or off (an admin that is off can not sign in and, by its role, may lose its users' access)."""
    if status not in ("active", "disabled"):
        raise ValueError("the status must be active or disabled")

    a = _target(c, admin_id)

    if status == "disabled" and a["me"]:
        raise ValueError("this is the admin Nikatel is signed in as: switching it off would lock this panel out")

    if a["status"] == status:
        return a

    done = _admin_call(c, "PUT", a, "", body={"status": status})
    audit(c, f"admin {a['username']} switched {'off' if status == 'disabled' else 'on'}", "warn" if status == "disabled" else "info")
    return _row_after(done, a, status=status)


def admin_reset_usage(c: dict, admin_id: int) -> dict:
    """Set the used traffic of an admin back to zero (a limited admin becomes active again); the panel keeps the old figure in its lifetime total."""
    a = _target(c, admin_id)
    done = _admin_call(c, "POST", a, "/reset", body={})
    audit(c, f"usage of admin {a['username']} reset to zero")
    return _row_after(done, a, used=0)


def admin_users_action(c: dict, admin_id: int, action: str) -> dict:
    """Switch all the active users of an admin off, or all its disabled users on (the panel does not remember which were off on purpose)."""
    if action not in ("disable", "activate"):
        raise ValueError("the action must be disable or activate")

    a = _target(c, admin_id)
    _admin_call(c, "POST", a, f"/users/{action}", body={})
    audit(c, f"all the {'active' if action == 'disable' else 'disabled'} users of admin {a['username']} switched {'off' if action == 'disable' else 'on'} ({a['users']} users in all)", "warn")
    return {"admin": a["username"], "action": action, "users": a["users"]}


def admin_users_delete(c: dict, admin_id: int, confirm: str) -> dict:
    """Delete every user of an admin (they are gone from the panel and its nodes): the username of the admin must be typed as the confirmation."""
    a = _target(c, admin_id)

    if str(confirm or "") != a["username"]:
        raise ValueError("type the username of the admin to confirm deleting all its users")

    record = _snapshot(c, a, "before deleting all its users")
    r = _admin_call(c, "DELETE", a, "/users")
    found = re.search(r"\d+", str(_dict(r).get("detail", "")))
    n = int(found.group()) if found else a["users"]
    audit(c, f"all the users of admin {a['username']} deleted ({n} users)", "bad")
    return {"admin": a["username"], "deleted": n, "record": record}


def admin_delete(c: dict, admin_id: int, users: str = "keep", confirm: str = "") -> dict:
    """Delete an admin (and, in the panel, its API keys with it). `users`: keep (they stay on the panel without an owner), disable (switch them off first), delete
    (delete them too; the username must be typed in `confirm`). The owner and the admin Nikatel is signed in as are refused. A record of the admin is saved first."""
    if users not in USERS_MODES:
        raise ValueError("users must be keep, disable or delete")

    a = _target(c, admin_id)

    if a["is_owner"]:
        raise ValueError("an owner can not be deleted")

    if a["me"]:
        raise ValueError("this is the admin Nikatel is signed in as: deleting it would lock this panel out")

    if users == "delete" and str(confirm or "") != a["username"]:
        raise ValueError("type the username of the admin to confirm deleting all its users")

    record = _snapshot(c, a, "before deleting the admin (users: " + users + ")")
    out = {"deleted": a["username"], "users_mode": users, "users": a["users"], "users_deleted": 0, "record": record}

    if users == "disable" and a["users"]:
        _admin_call(c, "POST", a, "/users/disable", body={})
    elif users == "delete" and a["users"]:
        r = _admin_call(c, "DELETE", a, "/users")
        found = re.search(r"\d+", str(_dict(r).get("detail", "")))
        out["users_deleted"] = int(found.group()) if found else a["users"]

    _remove_admin(c, a)
    audit(c, f"admin {a['username']} deleted (its {a['users']} users: {users})", "bad" if users == "delete" else "warn")
    return out


def _remove_admin(c: dict, a: dict) -> None:
    """The bulk route first: it is the one that explicitly detaches the users and clears the usage log of the admin; older panels only have the single one."""
    try:
        _call(c, "POST", "/api/admins/bulk/delete", body={"ids": [int(a["id"])]})
        return
    except PanelError as e:
        if not (e.code == 405 or str(e).endswith(": Not Found")):
            raise

    _admin_call(c, "DELETE", a)


def _snapshot(c: dict, a: dict, why: str) -> str:
    """A record of the admin (no secret: its Discord webhook is only a flag) before something that can not be undone happens to it or to its users."""
    safe = re.sub(r"[^A-Za-z0-9_.@\-]", "_", a["username"])
    return _write_backup(f"panel{c.get('id', 0)}-admin-{safe}", {"kind": "admin", "panel_id": c.get("id", 0), "at": int(time.time()), "why": why, "admin": {k: v for k, v in a.items() if k != "me"}})


def admin_usage(c: dict, admin_id: int, start: str, end: str) -> dict:
    """The traffic of an admin per Tehran day between two dates (both included), from the panel's own day buckets."""
    rng = tehran_range(start, end)
    a = _target(c, admin_id)
    raw = _admin_call(c, "GET", a, "/usage?" + urlencode({"period": "day", "start": rng["start"], "end": rng["end"]}))
    days = usage_days(raw, rng["days"])
    peak = max(days, key=lambda d: d["bytes"])
    return {"admin": a["username"], "from": start, "to": end, "tz": "Asia/Tehran (UTC+03:30)", "from_utc": rng["from_utc"], "to_utc": rng["to_utc"], "days": days,
            "total": sum(d["bytes"] for d in days), "peak": peak if peak["bytes"] else None}


# ------------------------------------------------------------------ settings: the description of every section (the tab is drawn from it)
SECRET_KINDS = ("secret", "proxy")
_TG_TOKEN = r"\d{8,12}:[A-Za-z0-9_-]{35}"
_RESERVED_VARS = ("SERVER_IP", "SERVER_IPV6", "USERNAME", "DATA_USAGE", "DATA_LIMIT", "DATA_LEFT", "DAYS_LEFT", "EXPIRE_DATE", "JALALI_EXPIRE_DATE", "TIME_LEFT",
                  "STATUS_EMOJI", "USAGE_PERCENTAGE", "ADMIN_USERNAME", "PROFILE_TITLE", "PROTOCOL", "TRANSPORT", "URL", "FORMAT")
_PLATFORMS = ("android", "ios", "windows", "macos", "linux", "appletv", "androidtv")
_LANGS = ("fa", "en", "ru", "zh")
_PROXY = re.compile(r"(?:http|https|socks4|socks5)://(?:[^\s:@]+:[^\s:@]+@)?[A-Za-z0-9.\-]+:\d{1,5}")


def _f(key: str, kind: str, label: str, **kw) -> dict:
    return {"key": key, "kind": kind, "label": label, **kw}


def _b(key: str, label: str, **kw) -> dict:
    return _f(key, "bool", label, **kw)


def _i(key: str, label: str, lo: int | None = None, hi: int | None = None, **kw) -> dict:
    return _f(key, "int", label, min=lo, max=hi, **kw)


def _t(key: str, label: str, **kw) -> dict:
    return _f(key, "text", label, **kw)


def _s(key: str, label: str, **kw) -> dict:
    return _f(key, "secret", label, **kw)


def _x(key: str, label: str = "Proxy", **kw) -> dict:
    return _f(key, "proxy", label, help="http://host:3128 or socks5://user:pass@host:1080; the address is shown without its login", **kw)


def _o(key: str, label: str, options: list, **kw) -> dict:
    return _f(key, "enum", label, options=[[o, o] if isinstance(o, str) else o for o in options], **kw)


def _l(key: str, label: str, lo: int, hi: int, **kw) -> dict:
    return _f(key, "ints", label, min=lo, max=hi, **kw)


def _g(key: str, label: str, fields: list, **kw) -> dict:
    return _f(key, "group", label, fields=fields, **kw)


def _r(key: str, label: str, cols: list, **kw) -> dict:
    return _f(key, "rows", label, cols=cols, **kw)


def _j(key: str, label: str, shape: str, **kw) -> dict:
    return _f(key, "json", label, type=shape, **kw)


def _toggles(names: dict) -> list[dict]:
    return [_b(k, v) for k, v in names.items()]


_CRUD = {"create": "Created", "modify": "Changed", "delete": "Deleted"}
_CHANNEL = [_i("telegram_chat_id", "Chat id", -(2 ** 63), 2 ** 63, nullable=True), _i("telegram_topic_id", "Topic id", 0, 2 ** 31, nullable=True),
            _s("discord_webhook_url", "Discord webhook", discord=True)]
_ENTITIES = {"admin": "Admins", "admin_role": "Admin roles", "core": "Cores", "group": "Groups", "host": "Hosts", "node": "Nodes", "user": "Users", "user_template": "User templates", "api_key": "API keys"}
_EVENTS = {
    "admin": _toggles({**_CRUD, "reset_usage": "Usage reset", "login": "Login", "usage_limit_warning": "Usage limit warning"}) + [_l("usage_limit_warning_percentages", "Warn at (percent)", 1, 100)],
    "admin_role": _toggles(_CRUD), "core": _toggles(_CRUD), "group": _toggles(_CRUD), "host": _toggles({**_CRUD, "modify_hosts": "Hosts changed together"}),
    "node": _toggles({**_CRUD, "connect": "Connected", "recovered": "Recovered", "error": "Error", "limited": "Limited", "reset_usage": "Usage reset"}),
    "user": _toggles({**_CRUD, "status_change": "Status change", "reset_data_usage": "Data usage reset", "data_reset_by_next": "Reset by the next plan", "subscription_revoked": "Subscription revoked"}),
    "user_template": _toggles(_CRUD), "api_key": _toggles(_CRUD)}
_TARGETS = ("links", "links_base64", "xray", "wireguard", "sing_box", "clash", "clash_meta", "outline", "block")
_FORMATS = ("links", "links_base64", "xray", "wireguard", "sing_box", "clash", "clash_meta", "outline")

SECTIONS: list[dict] = [
    {"key": "telegram", "label": "Telegram", "help": "The Telegram bot of the panel.", "confirm": "Saving this section restarts the Telegram bot of the panel.", "fields": [
        _b("enable", "Bot on"), _o("method", "Run as", [["webhook", "webhook (needs a public address)"], ["long-polling", "long polling"]]),
        _s("token", "Bot token", pattern=_TG_TOKEN, pattern_msg="is not a Telegram bot token (like 123456789:AAA...)"),
        _t("webhook_url", "Webhook address", url=True, nullable=True, help="the https address Telegram calls (method webhook)"), _s("webhook_secret", "Webhook secret"), _x("proxy_url"),
        _b("mini_app_login", "Mini App login"), _t("mini_app_web_url", "Mini App address", url=True), _b("for_admins_only", "Admins only")]},
    {"key": "webhook", "label": "Webhook", "help": "Notifications sent to your own addresses.", "fields": [
        _b("enable", "Webhook on"), _r("webhooks", "Receivers", [_t("url", "Address", url=True, required=True), _s("secret", "Secret", required=True)], id_col="url", max_rows=20),
        _l("days_left", "Days left (warn at)", 1, 3650), _l("usage_percent", "Usage percent (warn at)", 1, 100), _i("timeout", "Timeout", 1, 86400, help="seconds"),
        _i("recurrent", "Recurrent", 1, 1000, help="times"), _x("proxy_url")]},
    {"key": "notification_settings", "label": "Notifications", "help": "Where the panel sends its own alerts (Telegram chat, Discord).", "fields": [
        _b("notify_telegram", "Telegram alerts"), _b("notify_discord", "Discord alerts"), _s("telegram_api_token", "Telegram bot token", pattern=_TG_TOKEN, pattern_msg="is not a Telegram bot token"),
        _i("telegram_chat_id", "Chat id", -(2 ** 63), 2 ** 63, nullable=True), _i("telegram_topic_id", "Topic id", 0, 2 ** 31, nullable=True), _s("discord_webhook_url", "Discord webhook", discord=True),
        _x("proxy_url"), _i("max_retries", "Retries", 2, 100),
        _g("channels", "Separate channels (an empty one uses the main settings)", [_g(k, v, copy.deepcopy(_CHANNEL)) for k, v in _ENTITIES.items()], layout="table")]},
    {"key": "notification_enable", "label": "Events", "help": "Which events produce an alert.", "fields": [
        *[_g(k, v, _EVENTS[k]) for k, v in _ENTITIES.items()], _b("days_left", "Days left warning"), _b("percentage_reached", "Usage percentage reached")]},
    {"key": "subscription", "label": "Subscription", "help": "What every user's subscription link answers.", "confirm": "This changes the subscription link of EVERY user of the panel.", "fields": [
        _t("url_prefix", "Public address", url=True, help="the https address users' links start with"), _i("update_interval", "Update interval", 1, 8760, help="hours"),
        _t("support_url", "Support address", url=True), _t("profile_title", "Profile title", max=100), _t("announce", "Announcement", max=128, help="shown only by v2RayTun and Happ"),
        _t("announce_url", "Announcement address", url=True), _j("response_headers", "Extra response headers", "object", help='JSON, like {"profile-web-page-url": "https://example.test"}'),
        _r("rules", "Rules by client (first match wins)", [_t("pattern", "Client matches (regular expression)", required=True), _o("target", "Answer with", list(_TARGETS), required=True), _j("response_headers", "Headers", "object")], max_rows=60),
        _g("manual_sub_request", "Formats a user may ask for", [_b(k, k) for k in _FORMATS]),
        _j("applications", "Applications", "array", help="JSON list for the download page: name, platform, download_links, ..."),
        _b("allow_browser_config", "Show the page in a browser"), _b("disable_sub_template", "Plain answer (no page template)"), _b("randomize_order", "Random order of configs")]},
    {"key": "hwid", "label": "HWID", "help": "Device limits for subscriptions.", "confirm": "This changes the device rules for EVERY user of the panel.", "fields": [
        _b("enabled", "HWID on"), _b("forced", "Required"), _b("require_hwid_for_manual_sub", "Required for manual subscriptions"),
        _i("fallback_limit", "Default devices", 0, 10000, nullable=True), _i("min_limit", "Fewest devices", 0, 10000, nullable=True), _i("max_limit", "Most devices", 0, 10000, nullable=True)]},
    {"key": "general", "label": "General", "help": "Defaults of new users and the variables of the subscription template.", "confirm": "This changes the defaults of the panel.", "fields": [
        _o("default_method", "Shadowsocks method", ["chacha20-ietf-poly1305", "aes-128-gcm", "aes-256-gcm", "xchacha20-poly1305"]),
        _r("custom_variables", "Variables for the template", [_t("key", "Name", max=64, normalize="varkey", required=True), _t("value", "Value", max=512)], max_rows=60)]},
]
BY_KEY = {s["key"]: s for s in SECTIONS}


# ------------------------------------------------------------------ settings: reading, checking and merging a section by its description
def _is_url(s: str) -> bool:
    try:
        u = urlsplit(s)
    except ValueError:
        return False

    return u.scheme in ("http", "https") and bool(u.netloc) and not re.search(r"\s", s)


def _norm_varkey(s: str) -> str:
    s = s.strip()

    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()

    return s.upper()


def _int_value(f: dict, v, label: str):
    if v is None or (isinstance(v, str) and not v.strip()):
        if f.get("nullable"):
            return None

        raise ValueError(f"{label}: a number is needed")

    if isinstance(v, bool):
        raise BadInput(f"{label}: must be a whole number")

    try:
        n = int(v.strip()) if isinstance(v, str) else int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: must be a whole number") from None

    if isinstance(v, float) and v != n:
        raise ValueError(f"{label}: must be a whole number")

    lo, hi = f.get("min"), f.get("max")

    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise ValueError(f"{label}: must be between {lo} and {hi}")

    return n


def _text_value(f: dict, v, label: str):
    if v is None:
        v = ""

    if not isinstance(v, str):
        raise BadInput(f"{label}: must be text")

    s = v.strip()

    if f.get("normalize") == "varkey":
        s = _norm_varkey(s)

    if len(s) > int(f.get("max", 300)):
        raise ValueError(f"{label}: at most {f.get('max', 300)} characters")

    if not s:
        if f.get("required"):
            raise ValueError(f"{label}: is needed")

        return None if f.get("nullable") else ""

    if f.get("url") and not _is_url(s):
        raise ValueError(f"{label}: must be an http:// or https:// address")

    if f.get("normalize") == "varkey" and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", s):
        raise ValueError(f"{label}: use capital letters, digits and _ (start with a letter)")

    return s


def _ints_value(f: dict, v, label: str) -> list[int]:
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\s]+", v.strip()) if x]

    if not isinstance(v, list) or len(v) > 50:
        raise ValueError(f"{label}: a list of whole numbers (at most 50)")

    out = {_int_value({"min": f.get("min"), "max": f.get("max")}, x, label) for x in v}
    return sorted(out)


def _json_value(f: dict, v, label: str):
    want = dict if f.get("type") == "object" else list

    if not isinstance(v, want):
        raise BadInput(f"{label}: must be a JSON {'object' if want is dict else 'list'}")

    if len(json.dumps(v)) > 20000:
        raise ValueError(f"{label}: too long")

    return v


def _coerce(f: dict, v, where: str = ""):
    """One non-secret value of the description `f`, checked and in the form the panel stores; a ValueError names the field."""
    kind, label = f["kind"], where + f["label"]

    if kind == "bool":
        if not isinstance(v, bool):
            raise BadInput(f"{label}: must be on or off")

        return v

    if kind == "int":
        return _int_value(f, v, label)

    if kind == "text":
        return _text_value(f, v, label)

    if kind == "enum":
        allowed = [o[0] for o in f["options"]]

        if v not in allowed:
            raise ValueError(f"{label}: pick one of " + ", ".join(allowed))

        return v

    if kind == "ints":
        return _ints_value(f, v, label)

    if kind == "json":
        return _json_value(f, v, label)

    raise ValueError(f"{label}: this kind of field can not be set")


def _check_secret(f: dict, v, where: str = "") -> str:
    """A typed secret ('' = nothing typed). The messages name the field and never repeat what was typed."""
    label = where + f["label"]

    if v is None:
        return ""

    if not isinstance(v, str):
        raise BadInput(f"{label}: must be text")

    s = v.strip()

    if not s:
        return ""

    if len(s) > int(f.get("max", 512)):
        raise ValueError(f"{label}: is too long")

    if re.search(r"\s", s):
        raise ValueError(f"{label}: must not contain spaces or line breaks")

    if f["kind"] == "proxy" and not _PROXY.fullmatch(s):
        raise ValueError(f"{label}: must look like socks5://user:pass@host:1080 or http://host:3128")

    if f.get("pattern") and not re.fullmatch(f["pattern"], s):
        raise ValueError(f"{label}: {f.get('pattern_msg', 'is not in the right format')}")

    if f.get("discord") and not _is_discord(s):
        raise ValueError(f"{label}: must be an https://discord.com/... address")

    return s


def _looks_masked(v) -> bool:
    return isinstance(v, str) and bool(re.fullmatch(r"[*•●#xX]{3,}", v.strip()))


def _empty_of(col: dict):
    """The value of a column nobody filled in."""
    kind = col["kind"]
    return {"bool": False, "int": None, "text": None if col.get("nullable") else "", "enum": col["options"][0][0] if col.get("options") else "", "ints": [], "json": {} if col.get("type") == "object" else []}.get(kind)


def _merge_rows(f: dict, current, incoming, where: str) -> list[dict]:
    """The list of rows after the operator's edit: a row that came from the panel (its `_id` is its identity column as read) keeps the secrets it had unless a new
    one was typed; a new row must have its secrets typed."""
    label = where + f["label"]

    if not isinstance(incoming, list):
        raise BadInput(f"{label}: a list is expected")

    if len(incoming) > int(f.get("max_rows", 50)):
        raise ValueError(f"{label}: at most {f.get('max_rows', 50)} rows")

    idc = f.get("id_col")
    old = {str(r.get(idc)): r for r in _list(current) if isinstance(r, dict)} if idc else {}
    out: list[dict] = []

    for n, row in enumerate(incoming, 1):
        if not isinstance(row, dict):
            raise BadInput(f"{label}: row {n} is not valid")

        base = old.get(str(row.get("_id"))) if idc and row.get("_id") is not None else None
        new = copy.deepcopy(base) if base else {}

        for col in f["cols"]:
            ck = col["key"]
            here = f"{label}, row {n}: "

            if col["kind"] in SECRET_KINDS:
                typed = _check_secret(col, row.get(ck), here)

                if typed:
                    new[ck] = typed
                elif not new.get(ck) and col.get("required"):
                    raise ValueError(f"{here}{col['label']} is needed")
            elif ck in row:
                new[ck] = _coerce(col, row[ck], here)
            elif ck not in new:                                   # a column the caller left out: what the row had, else the empty value of its kind
                if col.get("required"):
                    raise ValueError(f"{here}{col['label']} is needed")

                new[ck] = _empty_of(col)

        out.append(new)

    if idc:
        keys = [str(r.get(idc)) for r in out]

        if len(set(keys)) != len(keys):
            raise ValueError(f"{label}: the same {idc} twice")

    return out


def merge_fields(fields: list[dict], current, incoming, clear=frozenset(), path: str = "", where: str = "") -> dict:
    """`current` (the section as the panel has it, secrets in it) with the fields of `incoming` laid over it. A field missing from `incoming` stays; a secret changes
    only when a new one was typed (or its path is in `clear`); fields the description does not know pass through untouched."""
    out = copy.deepcopy(current) if isinstance(current, dict) else {}
    incoming = _dict(incoming)

    for f in fields:
        k, kind, p = f["key"], f["kind"], path + f["key"]

        if kind in SECRET_KINDS:
            if p in clear:
                out[k] = None
            else:
                typed = _check_secret(f, incoming.get(k), where)

                if typed:
                    out[k] = typed
        elif k in incoming:
            if kind == "group":
                out[k] = merge_fields(f["fields"], out.get(k), incoming[k], clear, p + ".", where)
            elif kind == "rows":
                out[k] = _merge_rows(f, out.get(k), incoming[k], where)
            else:
                out[k] = _coerce(f, incoming[k], where)

    return out


def proxy_hint(url) -> str:
    """The address of a proxy without its login."""
    return re.sub(r"//[^/@]*@", "//***@", str(url or ""))


def mask(fields: list[dict], data) -> dict:
    """The section for the browser: a secret becomes {set: bool} (a proxy also {hint}); rows get `_id`, the identity the server needs to keep a row's secret."""
    data = _dict(data)
    out: dict = {}

    for f in fields:
        k, kind, v = f["key"], f["kind"], data.get(f["key"])

        if kind == "secret":
            out[k] = {"set": bool(v)}
        elif kind == "proxy":
            out[k] = {"set": bool(v), "hint": proxy_hint(v)}
        elif kind == "group":
            out[k] = mask(f["fields"], v)
        elif kind == "rows":
            out[k] = [{**mask(f["cols"], r), **({"_id": r.get(f["id_col"])} if f.get("id_col") else {})} for r in _list(v) if isinstance(r, dict)]
        else:
            out[k] = copy.deepcopy(v)

    return out


def plain(fields: list[dict], data) -> dict:
    """Only what the description lists and what is not a secret (what a backup may hold): nothing else of the live section is copied."""
    data = _dict(data)
    out: dict = {}

    for f in fields:
        k, kind = f["key"], f["kind"]

        if kind in SECRET_KINDS or k not in data:
            continue

        if kind == "group":
            out[k] = plain(f["fields"], data[k])
        elif kind == "rows":
            out[k] = [plain(f["cols"], r) for r in _list(data[k]) if isinstance(r, dict)]
        else:
            out[k] = copy.deepcopy(data[k])

    return out


def _empty(v) -> bool:
    return v in (None, "", [])


def _same(a, b) -> bool:
    return (_empty(a) and _empty(b)) or a == b


def _rows_view(f: dict, rows, before=None) -> list[dict]:
    """The rows without their secrets; with `before` (the rows as they were) a row whose secret differs gets {"secret": "changed"|"new"}."""
    idc, old, out = f.get("id_col"), {}, []

    if before is not None and idc:
        old = {str(r.get(idc)): r for r in _list(before) if isinstance(r, dict)}

    for r in _list(rows):
        if not isinstance(r, dict):
            continue

        v = plain(f["cols"], r)

        if before is not None:
            prev = old.get(str(r.get(idc))) if idc else None

            for col in f["cols"]:
                if col["kind"] in SECRET_KINDS and r.get(col["key"]) and (prev is None or prev.get(col["key"]) != r.get(col["key"])):
                    v["secret"] = "new" if prev is None else "changed"

        out.append(v)

    return out


def diff(fields: list[dict], before, after, path: str = "", lab: str = "") -> list[dict]:
    """What a section edit changes, as lines the operator can read: [{path, label, before, after}] and, for a secret, {path, label, secret: set|replaced|removed}
    without any value."""
    before, after = _dict(before), _dict(after)
    out: list[dict] = []

    for f in fields:
        k, kind = f["key"], f["kind"]
        a, b = before.get(k), after.get(k)
        name = (lab + " / " if lab else "") + f["label"]

        if kind in SECRET_KINDS:
            if not _same(a, b):
                out.append({"path": path + k, "label": name, "secret": "removed" if not b else "set" if not a else "replaced"})
        elif kind == "group":
            out += diff(f["fields"], a, b, path + k + ".", name)
        elif kind == "rows":
            va, vb = _rows_view(f, a), _rows_view(f, b, a)

            if va != vb:
                out.append({"path": path + k, "label": name, "before": va, "after": vb})
        elif not _same(a, b):
            out.append({"path": path + k, "label": name, "before": a, "after": b})

    return out


def _masked_secrets(fields: list[dict], data, where: str = "") -> list[str]:
    """The secrets of a live section that the panel hands out hidden (stars): writing the section back would replace them with the stars."""
    data = _dict(data)
    out: list[str] = []

    for f in fields:
        v = data.get(f["key"])

        if f["kind"] in SECRET_KINDS and _looks_masked(v):
            out.append(where + f["label"])
        elif f["kind"] == "group":
            out += _masked_secrets(f["fields"], v, where)
        elif f["kind"] == "rows":
            for r in _list(v):
                out += _masked_secrets(f["cols"], r, where)

    return out


def _need(s: dict, *names: str) -> bool:
    return all(bool(s.get(n)) for n in names)


def _check_applications(apps: list) -> None:
    seen: set = set()

    for n, a in enumerate(apps, 1):
        if not isinstance(a, dict):
            raise BadInput(f"Applications, #{n}: must be an object")

        if not str(a.get("name") or "") or len(str(a["name"])) > 32:
            raise ValueError(f"Applications, #{n}: name is needed (at most 32 characters)")

        if a.get("platform") not in _PLATFORMS:
            raise ValueError(f"Applications, #{n}: platform must be one of " + ", ".join(_PLATFORMS))

        imp = str(a.get("import_url") or "")

        if len(imp) > 256 or (imp and "{url}" not in imp):
            raise ValueError(f"Applications, #{n}: import_url must contain {{url}} (at most 256 characters)")

        if len(str(a.get("icon_url") or "")) > 512:
            raise ValueError(f"Applications, #{n}: icon_url is too long")

        for lang in _dict(a.get("description")):
            if lang not in _LANGS:
                raise ValueError(f"Applications, #{n}: description languages are " + ", ".join(_LANGS))

        links = a.get("download_links")

        if not isinstance(links, list):
            raise BadInput(f"Applications, #{n}: download_links must be a list")

        for dl in links:
            if not isinstance(dl, dict) or not dl.get("name") or len(str(dl["name"])) > 64 or not dl.get("url") or dl.get("language") not in _LANGS:
                raise ValueError(f"Applications, #{n}: every download link needs name (at most 64), url and language ({', '.join(_LANGS)})")

        if a.get("recommended"):
            key = (a["platform"], bool(a.get("show_when_hwid_enabled")))

            if key in seen:
                raise ValueError(f"Applications: more than one recommended application for {a['platform']}")

            seen.add(key)


def _check_custom_variables(rows: list) -> None:
    seen: set = set()

    for r in rows:
        key = r["key"]

        if key in _RESERVED_VARS:
            raise ValueError(f"Variables: {key} is a built-in variable")

        if key in seen:
            raise ValueError(f"Variables: {key} twice")

        seen.add(key)

        try:
            str(r.get("value") or "").format(**dict.fromkeys(_RESERVED_VARS, ""))
        except (ValueError, IndexError):
            raise ValueError(f"Variables: the value of {key} has a broken {{...}} placeholder") from None
        except KeyError:
            pass                                                   # an unknown {NAME} is left as it is by the panel too


def check_section(section: str, s: dict) -> None:
    """The rules that look at several fields of the merged section (the secrets that are set count): the panel's own, so a refusal comes before anything is written."""
    if section == "telegram" and s.get("enable"):
        if s.get("method") == "long-polling":
            if not _need(s, "token"):
                raise ValueError("The bot can not be on without a token.")
        elif not _need(s, "token", "webhook_url", "webhook_secret"):
            raise ValueError("The bot can not be on without a token, a webhook address and a webhook secret (method webhook).")
    elif section == "webhook" and s.get("enable") and not s.get("webhooks"):
        raise ValueError("Webhook can not be on without at least one receiver.")
    elif section == "notification_settings":
        if s.get("notify_discord") and not _need(s, "discord_webhook_url"):
            raise ValueError("Discord alerts can not be on without a Discord webhook.")

        if s.get("notify_telegram") and not _need(s, "telegram_api_token"):
            raise ValueError("Telegram alerts can not be on without a bot token.")

        if s.get("notify_telegram") and s.get("telegram_chat_id") is None:
            raise ValueError("Telegram alerts can not be on without a chat id.")
    elif section == "subscription":
        for n, r in enumerate(s.get("rules") or [], 1):
            try:
                re.compile(str(r.get("pattern") or ""))
            except re.error:
                raise ValueError(f"Rules, row {n}: the pattern is not a valid regular expression") from None

        _check_applications(_list(s.get("applications")))
    elif section == "hwid":
        lo, hi = s.get("min_limit"), s.get("max_limit")

        if lo is not None and hi is not None and lo > hi:
            raise ValueError("Fewest devices is more than most devices.")
    elif section == "general":
        _check_custom_variables(_list(s.get("custom_variables")))


# ------------------------------------------------------------------ settings: the panel and the backups
SECTION_KEYS = tuple(s["key"] for s in SECTIONS)


def read_live(c: dict) -> dict:
    """All the sections as the panel has them now (secrets in them: this never leaves the server). The custom variables of `general` live in the subscription."""
    r = _call(c, "GET", "/api/settings")

    if not isinstance(r, dict):
        raise PanelError("the panel answered the settings with something unexpected")

    live = {k: _dict(r.get(k)) for k in SECTION_KEYS}
    gen = r.get("general") if isinstance(r.get("general"), dict) else None

    if gen is None:
        try:
            gen = _dict(_call(c, "GET", "/api/settings/general"))
        except PanelError:
            gen = {}

    gen = dict(gen)
    gen["custom_variables"] = _list(live["subscription"].get("custom_variables")) or _list(gen.get("custom_variables"))
    live["general"] = gen
    return live


def settings_get(c: dict) -> dict:
    """The description of the sections, the values for the browser (no secret in them), and the backups kept for this panel."""
    live = read_live(c)
    return {"sections": SECTIONS, "values": {s["key"]: mask(s["fields"], live[s["key"]]) for s in SECTIONS}, "backups": settings_backups(c.get("id", 0))}


def _body_for(section: str, new: dict) -> dict:
    """What PUT /api/settings gets: the whole section (the panel replaces a section as a whole). The custom variables of `general` are sent inside it; the panel files them under the subscription."""
    return {section: new}


def _secret_kept(sent, got) -> bool:
    """Did the panel keep a secret as it was sent? Set / not set must agree, and when both are set the values must agree unless the panel shows its answer hidden."""
    if bool(sent) != bool(got):
        return False

    return not (sent and got and not _looks_masked(got) and sent != got)


def _row_kept(f: dict, sent: dict, got: dict) -> bool:
    secrets = [c["key"] for c in f["cols"] if c["kind"] in SECRET_KINDS]
    return plain(f["cols"], sent) == plain(f["cols"], got) and all(_secret_kept(sent.get(k), got.get(k)) for k in secrets)


def _verify(fields: list[dict], expected, got) -> list[str]:
    """The labels of the fields the panel did NOT keep as sent (a secret is compared without ever being shown)."""
    expected, got = _dict(expected), _dict(got)
    out: list[str] = []

    for f in fields:
        k, kind = f["key"], f["kind"]
        a, b = expected.get(k), got.get(k)

        if kind in SECRET_KINDS:
            if not _secret_kept(a, b):
                out.append(f["label"])
        elif kind == "group":
            out += _verify(f["fields"], a, b)
        elif kind == "rows":
            ra, rb = _list(a), _list(b)

            if len(ra) != len(rb) or not all(_row_kept(f, _dict(x), _dict(y)) for x, y in zip(ra, rb)):
                out.append(f["label"])
        elif not _same(a, b):
            out.append(f["label"])

    return out


def _validate_merged(spec: dict, new: dict) -> None:
    """The merged section must be one the panel will take, and must not carry a secret the panel only showed hidden (writing that back would replace it with the stars)."""
    hidden = _masked_secrets(spec["fields"], new)

    if hidden:
        raise ValueError("the panel hides " + ", ".join(hidden) + " when it is read, so type a new value for it (or change it on the panel)")

    check_section(spec["key"], new)


def _section_of(answer, section: str) -> dict | None:
    """The section as the panel's answer to a write holds it (None when the answer does not hold it)."""
    got = answer.get(section) if isinstance(answer, dict) else None
    return got if isinstance(got, dict) else None


def _write_section(c: dict, spec: dict, live: dict, new: dict, changes: list[dict], how: str = "saved") -> dict:
    """The one way a section reaches the panel: the settings as they are now go to a backup file (no secret), the WHOLE merged section is sent, and what the panel
    answers is compared with what was sent. Returns {backup, warnings, values}; `values` is the section for the browser (no secret)."""
    section = spec["key"]
    secrets = tuple(sorted(_secret_values(spec["fields"], new)))
    backup = save_settings_backup(c, live, section)
    got = _section_of(_call(c, "PUT", "/api/settings", secrets, body=_body_for(section, new)), section)
    warnings = [f"the panel did not keep: {x}" for x in _verify(spec["fields"], new, got)] if got is not None else ["the panel did not say what it kept: open the tab again to see"]
    audit(c, f"settings {spec['label']} {how} ({len(changes)} change{'' if len(changes) == 1 else 's'}: {', '.join(x['label'] for x in changes[:6])}{', ...' if len(changes) > 6 else ''}); backup {backup}")
    return {"section": section, "written": True, "changes": changes, "backup": backup, "warnings": warnings, "values": mask(spec["fields"], got if got is not None else new)}


def normalize_section(section: str, s: dict) -> dict:
    """What the panel itself does to a section on the way in, done here first so that the changes shown and the read-back agree: a bot that does not run by webhook
    has no webhook address."""
    if section == "telegram" and s.get("method") != "webhook":
        s["webhook_url"] = None

    return s


def settings_update(c: dict, section: str, values: dict, clear: list[str] | None = None, dry_run: bool = False) -> dict:
    """Change ONE section: the live section is read, `values` laid over it (a secret only when typed, or removed when its path is in `clear`), the merged section checked,
    the previous settings saved to a file WITHOUT secrets, then the whole section is written and compared with what the panel says it kept. `dry_run` answers with
    the changes and writes nothing (no backup either)."""
    spec = BY_KEY.get(section)

    if spec is None:
        raise ValueError("unknown section; the sections are " + ", ".join(SECTION_KEYS))

    live = read_live(c)
    new = normalize_section(section, merge_fields(spec["fields"], live[section], values, frozenset(clear or ())))
    _validate_merged(spec, new)
    changes = diff(spec["fields"], live[section], new)

    if dry_run:
        return {"section": section, "written": False, "changes": changes, "backup": "", "warnings": []}

    if not changes:
        raise ValueError("nothing to change in this section")

    return _write_section(c, spec, live, new, changes)


def _secret_values(fields: list[dict], data) -> set[str]:
    """Every secret string of a section (so that no refusal text can repeat one)."""
    data = _dict(data)
    out: set[str] = set()

    for f in fields:
        v = data.get(f["key"])

        if f["kind"] in SECRET_KINDS and isinstance(v, str) and v:
            out.add(v)
        elif f["kind"] == "group":
            out |= _secret_values(f["fields"], v)
        elif f["kind"] == "rows":
            for r in _list(v):
                out |= _secret_values(f["cols"], r)

    return out


def save_settings_backup(c: dict, live: dict, section: str) -> str:
    """The settings as they are now, WITHOUT any secret and only the fields this module knows, as pg-backups/panel<id>-settings-<time>.json (the last 20 are kept)."""
    doc = {"kind": "settings", "panel_id": c.get("id", 0), "at": int(time.time()), "section": section, "settings": {s["key"]: plain(s["fields"], live.get(s["key"])) for s in SECTIONS}}
    return _write_backup(f"panel{int(c.get('id', 0))}-settings", doc)


def settings_backups(panel_id: int) -> list[dict]:
    """The backups of this panel's settings, newest first: [{name, at, section}]."""
    out = []

    for p in reversed(_backups_of(f"panel{int(panel_id)}-settings")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        out.append({"name": p.name, "at": doc.get("at"), "section": doc.get("section", "")})

    return out


def _restore_rows(f: dict, current, saved) -> tuple[list[dict], list[str]]:
    """The saved rows with the secrets of the rows that are still there now (matched by the identity column); a saved row whose secret is gone can not be put back."""
    idc = f.get("id_col")
    now = {str(r.get(idc)): r for r in _list(current) if isinstance(r, dict)} if idc else {}
    rows, skipped = [], []

    for r in _list(saved):
        if not isinstance(r, dict):
            continue

        base = copy.deepcopy(now.get(str(r.get(idc)))) if idc and str(r.get(idc)) in now else {}
        row = {**base, **r}
        lost = [col["label"] for col in f["cols"] if col["kind"] in SECRET_KINDS and col.get("required") and not row.get(col["key"])]

        if lost:
            skipped.append(f"{f['label']}: {r.get(idc, 'a row')} ({', '.join(lost)} is not kept in a backup)")
        else:
            rows.append(row)

    return rows, skipped


def restore_merge(fields: list[dict], current, saved) -> tuple[dict, list[str]]:
    """The live section with the non-secret values of the backup laid over it; every secret stays as it is now."""
    out = copy.deepcopy(current) if isinstance(current, dict) else {}
    saved, skipped = _dict(saved), []

    for f in fields:
        k, kind = f["key"], f["kind"]

        if kind in SECRET_KINDS or k not in saved:
            continue

        if kind == "group":
            out[k], more = restore_merge(f["fields"], out.get(k), saved[k])
            skipped += more
        elif kind == "rows":
            out[k], more = _restore_rows(f, out.get(k), saved[k])
            skipped += more
        else:
            out[k] = copy.deepcopy(saved[k])

    return out, skipped


def settings_restore(c: dict, name: str, section: str = "") -> dict:
    """Put the previous values of a section back from a backup: only its non-secret values are sent; the secrets that are set now are read from the panel again and
    kept. The state before the restore is saved too, so a restore can be undone the same way."""
    if not re.fullmatch(r"panel" + str(int(c.get("id", 0))) + r"-settings-\d{8}-\d{6}(?:-\d+)?\.json", str(name or "")):
        raise ValueError("this is not a backup of this panel's settings")

    p = _backup_dir() / name

    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("this backup does not exist (any more)") from None

    section = section or doc.get("section", "")
    spec = BY_KEY.get(section)

    if spec is None or section not in _dict(doc.get("settings")):
        raise ValueError("the backup has no such section")

    live = read_live(c)
    merged, skipped = restore_merge(spec["fields"], live[section], doc["settings"][section])
    merged = normalize_section(section, merged)

    try:
        _validate_merged(spec, merged)
    except ValueError as e:
        raise ValueError(f"{e} (a backup never holds secrets: set the secret that is missing first, then restore)") from None

    changes = diff(spec["fields"], live[section], merged)

    if not changes:
        raise ValueError("the section already has these values")

    return {**_write_section(c, spec, live, merged, changes, "restored from " + name), "restored_from": name, "skipped": skipped}


# ------------------------------------------------------------------ API keys
# A key acts as its admin (`X-Api-Key: pg_key_<uuid>`): with `inherit_permissions` it has the rights of that admin, otherwise only the list stored on the key (never owner
# level). The secret exists in ONE answer of the panel - the one to the create call (and to a revoke, which makes a new one) - and is handed on to the caller once: it is
# not logged, not kept, not written to any backup, and the list never has it (only the trimmed form pg_key_abc***xyz).
KEY_PAGE = 200
MAX_KEY_PAGES = 5
_CRUD5 = ("create", "read", "read_simple", "update", "delete")
# resource -> action -> "bool" (no / yes) or "scope" (no / own / all): the names a key's own list may hold (the panel refuses any other name)
PERMS: dict[str, dict[str, str]] = {
    "users": {"create": "bool", "read": "scope", "read_simple": "scope", "update": "scope", "delete": "scope", "reset_usage": "scope", "revoke_sub": "scope", "set_owner": "bool", "activate_next_plan": "scope"},
    "admins": dict.fromkeys(_CRUD5 + ("reset_usage",), "bool"), "nodes": dict.fromkeys(_CRUD5 + ("reconnect", "update_core", "logs", "stats"), "bool"),
    "groups": dict.fromkeys(_CRUD5, "bool"), "hosts": dict.fromkeys(("create", "read", "update"), "bool"), "templates": dict.fromkeys(_CRUD5, "bool"),
    "client_templates": dict.fromkeys(_CRUD5, "bool"), "cores": dict.fromkeys(_CRUD5, "bool"), "settings": dict.fromkeys(("read", "read_general", "update"), "bool"),
    "system": {"read": "bool"}, "hwids": dict.fromkeys(("read", "delete"), "bool"), "admin_roles": dict.fromkeys(_CRUD5, "bool"),
    "api_keys": {"create": "bool", "read": "scope", "read_simple": "scope", "update": "scope", "delete": "scope"}}
PERM_SCHEMA = [{"key": res, "actions": [{"key": act, "kind": kind} for act, kind in acts.items()]} for res, acts in PERMS.items()]


def _level(kind: str, v) -> str:
    """One permission value of the panel (True, {"scope": 0|1|2}, null) as no / yes / own / all."""
    scope = v.get("scope") if isinstance(v, dict) else None

    if kind == "bool":
        return "yes" if v is True or (isinstance(scope, int) and scope > 0) else "no"

    return "all" if v is True or scope == 2 else "own" if scope == 1 else "no"


def perms_view(raw) -> dict:
    """The permission list of a key as {resource: {action: no|yes|own|all}} for every name the panel knows (what is missing is 'no')."""
    raw = _dict(raw)
    return {res: {act: _level(kind, _dict(raw.get(res)).get(act)) for act, kind in acts.items()} for res, acts in PERMS.items()}


def perms_body(view) -> dict:
    """The permission list for the panel from the browser's {resource: {action: level}}; only what is granted is sent, and a name the panel does not know is refused."""
    if not isinstance(view, dict):
        raise BadInput("permissions: an object is expected")

    out: dict = {}

    for res, acts in view.items():
        kinds = PERMS.get(res)

        if kinds is None or not isinstance(acts, dict):
            raise ValueError(f"permissions: there is no resource {res}")

        for act, lvl in acts.items():
            kind = kinds.get(act)

            if kind is None:
                raise ValueError(f"permissions: {res} has no action {act}")

            if lvl in (None, "", "no", False):
                continue

            if kind == "bool" and lvl in ("yes", True):
                out.setdefault(res, {})[act] = True
            elif kind == "scope" and lvl in ("own", "all"):
                out.setdefault(res, {})[act] = {"scope": 1 if lvl == "own" else 2}
            else:
                raise ValueError(f"permissions: {res}.{act} must be " + ("no or yes" if kind == "bool" else "no, own or all"))

    return out


def expiry_of(day: str) -> str:
    """The end of a Tehran day (YYYY-MM-DD) as the panel wants an expiry: a timezone-aware time, which must be in the future."""
    try:
        d = date.fromisoformat(str(day))
    except ValueError:
        raise ValueError("the expiry must look like 2027-01-31") from None

    end = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=TEHRAN)

    if end <= datetime.now(timezone.utc):
        raise ValueError("the expiry must be in the future")

    if d.year > 2100:
        raise ValueError("the expiry is too far away")

    return end.isoformat()


def expiry_day(stamp) -> str:
    """The Tehran day (YYYY-MM-DD) of an expiry the panel gave, '' when there is none or it can not be read."""
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(TEHRAN).date().isoformat() if stamp else ""
    except ValueError:
        return ""


def own_trimmed(c: dict) -> str:
    """The trimmed form (pg_key_abc***xyz) of the API key Nikatel itself is connected with, '' when the panel is reached with a login or the credential does not say."""
    m = re.fullmatch(r"pg_key_([0-9a-fA-F\-]{36})", str(c.get("api_key") or ""))

    if m:
        return f"pg_key_{m.group(1)[:3]}***{m.group(1)[-3:]}"

    t = str(c.get("api_key_trimmed") or "")
    return t if re.fullmatch(r"pg_key_\w{3}\*\*\*\w{3}", t) else ""


def key_row(k: dict, names: dict | None = None, own: str = "") -> dict:
    """One key as the tab shows it: no secret in it (the panel has none to give), the state as one word, the permission list as no / yes / own / all."""
    stored = str(k.get("status") or "active")
    expired = bool(k.get("is_expired"))
    perms = perms_view(k.get("permissions"))
    trimmed = str(k.get("api_key_trimmed") or "")
    aid = k.get("admin_id")
    return {"id": k.get("id"), "name": str(k.get("name") or ""), "note": str(k.get("note") or ""), "admin_id": aid, "admin": str(_dict(names).get(aid, "")), "trimmed": trimmed,
            "status": "disabled" if stored == "disabled" else "expired" if expired else "active", "stored_status": stored, "expired": expired, "expire_date": k.get("expire_date"),
            "expire_day": expiry_day(k.get("expire_date")), "created_at": k.get("created_at"), "revoked_at": k.get("revoked_at"), "last_used": k.get("last_used_at") or k.get("last_used"),
            "inherit": k.get("inherit_permissions") is not False, "permissions": perms, "granted": sum(1 for acts in perms.values() for lvl in acts.values() if lvl != "no"),
            "connected": bool(own) and trimmed == own}


def _all_keys(c: dict) -> tuple[list[dict], int]:
    rows: list[dict] = []
    total = 0

    for page in range(MAX_KEY_PAGES):
        r = _dict(_call(c, "GET", "/api/api_keys?" + urlencode({"offset": page * KEY_PAGE, "limit": KEY_PAGE})))
        got = [k for k in _list(r.get("api_keys")) if isinstance(k, dict)]
        total = r.get("total") if isinstance(r.get("total"), int) else len(rows) + len(got)
        rows += got

        if len(got) < KEY_PAGE or len(rows) >= total:
            break

    return rows, total


def _admin_names(c: dict) -> dict:
    """{admin id: username} for the owners of the keys (empty when the login may not read the simple list)."""
    try:
        r = _dict(_call(c, "GET", "/api/admins/simple"))
    except PanelError:
        return {}

    return {a.get("id"): str(a.get("username") or "") for a in _list(r.get("admins")) if isinstance(a, dict)}


def keys_list(c: dict) -> dict:
    """Every API key (no secret), the admins a key can belong to, who Nikatel is signed in as, and the permission names the panel knows."""
    raw, total = _all_keys(c)
    names = _admin_names(c)
    own = own_trimmed(c)
    me = login_admin(c)
    return {"keys": [key_row(k, names, own) for k in raw], "total": total, "admins": [{"id": i, "username": n} for i, n in names.items()], "permission_schema": PERM_SCHEMA,
            "me": {"id": me.get("id"), "username": str(me.get("username") or c.get("username") or ""), "is_owner": role_of(me)["is_owner"]}, "connected_known": bool(own), "truncated": total > len(raw)}


def _key_name(v) -> str:
    n = str(v or "").strip()

    if not 1 <= len(n) <= 128:
        raise ValueError("a key needs a name of 1 to 128 characters")

    return n


def _key_note(v) -> str:
    n = str(v or "").strip()

    if len(n) > 512:
        raise ValueError("the note can have 512 characters at most")

    return n


def _one_key(c: dict, key_id: int) -> dict:
    """One key as the panel has it (raw), by its id."""
    return _dict(_call(c, "GET", f"/api/api_key/{int(key_id)}"))


def _guard_connected(c: dict, rows: list[dict], verb: str) -> None:
    """Never delete or renew the key Nikatel is connected with: this panel could not reach the PasarGuard panel any more (the key is told apart by its trimmed form)."""
    hit = [r["name"] for r in rows if r["connected"]]

    if hit:
        raise ValueError(f"Nikatel is connected to this panel with the key {', '.join(hit)}: {verb} it would cut this panel off")


def _snapshot_key(c: dict, row: dict, why: str) -> str:
    """The settings of a key (name, owner, expiry, permissions - never a secret) before it is renewed or deleted, so that an equal key can be made again."""
    keep = ("id", "name", "note", "admin_id", "admin", "trimmed", "status", "expire_date", "inherit", "permissions", "created_at", "revoked_at")
    return _write_backup(f"panel{int(c.get('id', 0))}-apikey", {"kind": "api_key", "panel_id": c.get("id", 0), "at": int(time.time()), "why": why, "key": {k: row.get(k) for k in keep}})


def key_create(c: dict, data: dict) -> dict:
    """Make a key. Returns {key, secret}: the secret is in this answer only, and the caller must show it once (the panel can not show it again)."""
    inherit = data.get("inherit_permissions", True) is not False
    body: dict = {"name": _key_name(data.get("name")), "inherit_permissions": inherit}
    note = _key_note(data.get("note"))

    if note:
        body["note"] = note

    if not inherit:
        body["permissions"] = perms_body(data.get("permissions"))

        if not body["permissions"]:
            raise ValueError("pick at least one permission, or let the key use the rights of its admin")

    if data.get("expire"):
        body["expire_date"] = expiry_of(data["expire"])

    if data.get("admin_id") is not None:
        body["admin_id"] = int(data["admin_id"])

    made = _dict(_call(c, "POST", "/api/api_key", body=body))
    secret = str(made.pop("api_key", "") or "")
    row = key_row(made, _admin_names(c), own_trimmed(c))
    audit(c, f"API key {row['name']} created for admin {row['admin'] or row['admin_id']} ({'the rights of its admin' if row['inherit'] else str(row['granted']) + ' permissions of its own'})")
    return {"key": row, "secret": secret}


def key_update(c: dict, key_id: int, data: dict) -> dict:
    """Change the name, note, expiry (`expire`: a day, or '' for none), status (active / disabled), owner or permissions of a key - only what differs from now is sent."""
    cur = _one_key(c, key_id)
    body: dict = {}

    if data.get("name") is not None and _key_name(data["name"]) != cur.get("name"):
        body["name"] = _key_name(data["name"])

    if data.get("note") is not None and _key_note(data["note"]) != str(cur.get("note") or ""):
        body["note"] = _key_note(data["note"])

    if "expire" in data:
        day = str(data["expire"] or "")

        if day != expiry_day(cur.get("expire_date")):                       # the tab shows the day: the same day keeps the exact time the key has
            body["expire_date"] = expiry_of(day) if day else None             # an explicit null removes the expiry

    if data.get("status") is not None:
        if data["status"] not in ("active", "disabled"):
            raise ValueError("the status must be active or disabled")

        if data["status"] != cur.get("status"):
            body["status"] = data["status"]

    if data.get("admin_id") is not None and int(data["admin_id"]) != cur.get("admin_id"):
        body["admin_id"] = int(data["admin_id"])

    was_inherit = cur.get("inherit_permissions") is not False
    inherit = data.get("inherit_permissions", was_inherit) is not False

    if inherit != was_inherit:
        body["inherit_permissions"] = inherit

    if not inherit and data.get("permissions") is not None:
        want = perms_body(data["permissions"])

        if not want:
            raise ValueError("pick at least one permission, or let the key use the rights of its admin")

        if was_inherit or want != perms_body(perms_view(cur.get("permissions"))):
            body["permissions"] = want

    if not inherit and was_inherit and "permissions" not in body:
        raise ValueError("pick the permissions of the key")

    if not body:
        raise ValueError("nothing to change")

    own = own_trimmed(c)

    if own and cur.get("api_key_trimmed") == own and (set(body) - {"name", "note"} and not _only_longer_expiry(body, cur)):
        raise ValueError("Nikatel is connected to this panel with this key: only its name, note and a later expiry are changed from here (anything else could cut this panel off)")

    row = key_row(_dict(_call(c, "PATCH", f"/api/api_key/{int(key_id)}", body=body)), _admin_names(c), own)
    audit(c, f"API key {row['name']} changed ({', '.join(sorted(body))})")
    return row


def _only_longer_expiry(body: dict, cur: dict) -> bool:
    """True when the only change besides name / note is an expiry that is later than the key has (or none): it can not cut the key off."""
    if set(body) - {"name", "note", "expire_date"}:
        return False

    new, old = body.get("expire_date"), cur.get("expire_date")

    if new is None:
        return True

    if old is None:
        return False                                         # a key that never expired would get an end

    try:
        return datetime.fromisoformat(new) >= datetime.fromisoformat(str(old).replace("Z", "+00:00"))
    except ValueError:
        return False


def key_revoke(c: dict, key_id: int) -> dict:
    """Renew a key: the panel makes a NEW secret and the old one stops working at once. Returns {key, secret} (the secret once). A disabled key is active again afterwards (the panel does that)."""
    cur = key_row(_one_key(c, key_id), _admin_names(c), own_trimmed(c))
    _guard_connected(c, [cur], "renewing")
    record = _snapshot_key(c, cur, "before the secret was renewed")
    made = _dict(_call(c, "POST", f"/api/api_key/{int(key_id)}/revoke", body={}))
    secret = str(made.pop("api_key", "") or "")
    audit(c, f"API key {cur['name']} renewed: its old secret no longer works", "warn")
    return {"key": key_row(made, _admin_names(c), own_trimmed(c)), "secret": secret, "record": record}


def key_delete(c: dict, key_id: int) -> dict:
    """Delete one key for good (a record of its settings, without the secret, is saved first)."""
    cur = key_row(_one_key(c, key_id), _admin_names(c), own_trimmed(c))
    _guard_connected(c, [cur], "deleting")
    record = _snapshot_key(c, cur, "before the key was deleted")
    _call(c, "DELETE", f"/api/api_key/{int(key_id)}")
    audit(c, f"API key {cur['name']} deleted", "warn")
    return {"deleted": cur["name"], "record": record}


def keys_delete(c: dict, ids: list[int]) -> dict:
    """Delete several keys in one call (the panel takes them all or none); the key Nikatel is connected with is refused."""
    want = sorted({int(i) for i in ids})

    if not 1 <= len(want) <= 100:
        raise ValueError("pick 1 to 100 keys")

    raw, _total = _all_keys(c)
    names, own = _admin_names(c), own_trimmed(c)
    rows = {k.get("id"): key_row(k, names, own) for k in raw}
    missing = [i for i in want if i not in rows]

    if missing:
        raise ValueError("there is no key " + ", ".join(str(i) for i in missing))

    _guard_connected(c, [rows[i] for i in want], "deleting")
    records = [_snapshot_key(c, rows[i], "before the key was deleted (several at once)") for i in want]
    r = _dict(_call(c, "POST", "/api/api_keys/bulk/delete", body={"ids": want}))
    names_gone = [str(n) for n in _list(r.get("api_keys"))] or [rows[i]["name"] for i in want]
    audit(c, f"{len(names_gone)} API keys deleted: {', '.join(names_gone)}", "warn")
    return {"deleted": names_gone, "count": r.get("count", len(want)), "records": len(records)}
