"""PasarGuard tabs Admins and Settings (services/pg_admin.py, api/pg_admin.py): the pure helpers, the admins, the API keys and the settings against a fake panel, the
backups, and the routes. No network: the fake panel is a PasarGuard 5.x in memory that answers like the real one does (a settings section is replaced as a whole and
the fields that are null are dropped, a 422 is a list that panels.call has cut into a Python repr, ...). Every secret in it is a made-up value, and the tests look for
those values in everything that leaves the module."""
import copy
import json
import re
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Panel
from nikatel.services import panels
from nikatel.services import pg_admin as pa
from nikatel.services.panels import PanelError

GB = 1024 ** 3
TG_TOKEN = "123456789:" + "A" * 35
TG_HOOK_SECRET = "tg-webhook-secret-value"
PROXY = "socks5://proxyuser:proxypass@203.0.113.9:1080"
DISCORD = "https://discord.com/api/webhooks/111/discord-token-value"
HOOK_A, HOOK_B = "hook-a-secret-value", "hook-b-secret-value"
SECRETS = [TG_TOKEN, TG_HOOK_SECRET, PROXY, "proxypass", DISCORD, "discord-token-value", HOOK_A, HOOK_B]
GOOD_PW = "Abcd-1234-Efgh-5678"
C = {"id": 901, "url": "https://pg.example.test", "username": "moderator", "password": "x", "verify_tls": True, "type": "pasarguard"}


def no_secret(obj):
    """The made-up secrets appear nowhere in the JSON text of `obj`."""
    text = json.dumps(obj, default=str)
    assert not [s for s in SECRETS if s in text], [s for s in SECRETS if s in text]


def role(rid):
    return {"id": rid, "name": {1: "owner", 2: "administrator", 3: "operator"}[rid], "is_owner": rid == 1, "disconnect_users_when_disabled": rid == 3}


def adm(i, name, rid=3, users=0, used=0, limit=None, status="active", **kw):
    a = {"id": i, "username": name, "telegram_id": None, "discord_webhook": "", "sub_domain": "", "profile_title": "", "support_url": "", "custom_variables": [],
         "notification_enable": {k: True for k in pa._NOTIFY}, "total_users": users, "used_traffic": used, "data_limit": limit, "status": status, "sub_template": "",
         "lifetime_used_traffic": used, "note": "", "role": role(rid), "permission_overrides": {key: None for _a, key, _u in pa._OVERRIDES},
         "is_disabled": status == "disabled", "is_limited": status == "limited"}
    a.update(kw)
    return a


def pg_error(code, detail):
    """The error panels.call raises for a refusal: 'HTTP <code>: <detail cut to 120 characters>'."""
    return PanelError(f"HTTP {code}: {str(detail)[:120]}", code)


def pg_422(field, msg, value):
    return pg_error(422, [{"type": "value_error", "loc": ["body", field], "msg": "Value error, " + msg, "input": value, "ctx": {"error": {}}}])


def events():
    out = {}

    for f in pa.BY_KEY["notification_enable"]["fields"]:
        if f["kind"] == "group":
            out[f["key"]] = {x["key"]: ([] if x["kind"] == "ints" else True) for x in f["fields"]}
        else:
            out[f["key"]] = True

    return out


LIVE = {
    "telegram": {"enable": True, "token": TG_TOKEN, "webhook_url": "https://panel.example.test/tg", "webhook_secret": TG_HOOK_SECRET, "proxy_url": PROXY, "method": "webhook",
                 "mini_app_login": True, "mini_app_web_url": "", "for_admins_only": True},
    "webhook": {"enable": True, "webhooks": [{"url": "https://hook-a.example.test/x", "secret": HOOK_A}, {"url": "https://hook-b.example.test/y", "secret": HOOK_B}],
                "days_left": [3], "usage_percent": [80], "timeout": 180, "recurrent": 3, "proxy_url": None},
    "notification_settings": {"notify_telegram": False, "notify_discord": True, "telegram_api_token": None, "telegram_chat_id": None, "telegram_topic_id": None, "discord_webhook_url": DISCORD,
                              "channels": {k: {"telegram_chat_id": None, "telegram_topic_id": None, "discord_webhook_url": None} for k in pa._ENTITIES}, "proxy_url": None, "max_retries": 3},
    "notification_enable": events(),
    "subscription": {"url_prefix": "https://sub.example.test", "update_interval": 12, "support_url": "https://t.me/example", "profile_title": "Subscription", "announce": "", "announce_url": "",
                     "response_headers": {}, "rules": [{"pattern": "^([Cc]lash|[Ss]tash)", "target": "clash", "response_headers": {}}], "manual_sub_request": {k: True for k in pa._FORMATS},
                     "applications": [], "allow_browser_config": True, "disable_sub_template": False, "randomize_order": False, "custom_variables": [{"key": "BRAND", "value": "Example"}]},
    "hwid": {"enabled": True, "forced": False, "require_hwid_for_manual_sub": False, "fallback_limit": None, "min_limit": None, "max_limit": None},
    "general": {"default_method": "chacha20-ietf-poly1305", "custom_variables": None},
}


def fill(defaults, data):
    """What the panel's models do on the way out: every field of the model is there, the missing ones with their default (null for most)."""
    if isinstance(defaults, dict) and isinstance(data, dict):
        return {k: fill(defaults[k], data[k]) if k in data and k in defaults else data.get(k, copy.deepcopy(defaults.get(k))) for k in {**defaults, **data}}

    return copy.deepcopy(data)


def drop_none(v):
    """model_dump(exclude_none=True)."""
    if isinstance(v, dict):
        return {k: drop_none(x) for k, x in v.items() if x is not None}

    if isinstance(v, list):
        return [drop_none(x) for x in v]

    return v


def skeleton(v):
    """The same fields with nothing in them: what a field the panel dropped comes back as."""
    if isinstance(v, dict):
        return {k: skeleton(x) for k, x in v.items()}

    return [] if isinstance(v, list) else None


class Fake:
    """A PasarGuard 5.x panel in memory: admins with roles, API keys and settings. Every call is recorded in `calls` as (method, path, body)."""

    def __init__(self):
        self.calls = []
        self.admins = {1: adm(1, "moderator", 1, users=3), 2: adm(2, "alice", 3, users=5, used=5 * GB, limit=10 * GB), 3: adm(3, "bob", 2)}
        self.me = "moderator"
        self.roles_ok = True
        self.legacy = False                    # a panel from before the by-id routes and the bulk delete
        self.passwords = {}
        self.next_admin = 10
        self.usage = {}
        self.keys = {}
        self.secrets = {}
        self.next_key = 1
        self.settings = copy.deepcopy(LIVE)
        self.defaults = skeleton(LIVE)
        self.ignore = set()                    # "section.field": the panel keeps the old value of it
        self.fail = {}                         # (method, part of the path) -> the error to raise

    # ---- the entry point (what panels.call is replaced with)
    def __call__(self, c, method, path, **kw):
        body = kw.get("body")
        self.calls.append((method, path, copy.deepcopy(body)))

        for (m, part), err in self.fail.items():
            if m == method and part in path:
                raise err

        url = urlsplit(path)
        q = parse_qs(url.query)

        if self.legacy and ("/by-id/" in url.path or "/bulk/" in url.path):
            raise pg_error(404, "Not Found")

        for m, rx, fn in self.routes():
            hit = re.fullmatch(rx, url.path)

            if m == method and hit:
                return copy.deepcopy(fn(*hit.groups(), q=q, body=body))

        raise AssertionError(f"the fake panel has no route {method} {path}")

    def routes(self):
        return [
            ("GET", r"/api/admin", self.get_me), ("GET", r"/api/admins", self.list_admins), ("GET", r"/api/admins/simple", self.simple_admins),
            ("GET", r"/api/admin-roles/simple", self.get_roles), ("POST", r"/api/admin", self.create_admin), ("POST", r"/api/admins", self.not_allowed), ("POST", r"/api/admins/bulk/delete", self.bulk_delete),
            ("PUT", r"/api/admin/by-id/(\d+)", self.put_admin), ("PUT", r"/api/admin/([^/]+)", self.put_admin), ("DELETE", r"/api/admin/by-id/(\d+)", self.del_admin),
            ("DELETE", r"/api/admin/([^/]+)", self.del_admin), ("POST", r"/api/admin/by-id/(\d+)/reset", self.reset_admin), ("POST", r"/api/admin/([^/]+)/reset", self.reset_admin),
            ("POST", r"/api/admin/by-id/(\d+)/users/(disable|activate)", self.users_action), ("POST", r"/api/admin/([^/]+)/users/(disable|activate)", self.users_action),
            ("DELETE", r"/api/admin/by-id/(\d+)/users", self.users_delete), ("DELETE", r"/api/admin/([^/]+)/users", self.users_delete),
            ("GET", r"/api/admin/by-id/(\d+)/usage", self.get_usage), ("GET", r"/api/admin/([^/]+)/usage", self.get_usage),
            ("GET", r"/api/api_keys", self.list_keys), ("POST", r"/api/api_key", self.create_key), ("GET", r"/api/api_key/(\d+)", self.get_key),
            ("PATCH", r"/api/api_key/(\d+)", self.patch_key), ("DELETE", r"/api/api_key/(\d+)", self.delete_key), ("POST", r"/api/api_key/(\d+)/revoke", self.revoke_key),
            ("POST", r"/api/api_keys/bulk/delete", self.bulk_delete_keys),
            ("GET", r"/api/settings", self.get_settings), ("PUT", r"/api/settings", self.put_settings)]

    # ---- admins
    def one(self, ref):
        for a in self.admins.values():
            if str(a["id"]) == str(ref) or a["username"] == ref:
                return a

        raise pg_error(404, "Admin not found.")

    def get_me(self, q, body):
        return self.one(self.me)

    def list_admins(self, q, body):
        rows = sorted(self.admins.values(), key=lambda a: a["username"] if q.get("sort") else a["id"])

        if q.get("ids"):
            rows = [a for a in rows if str(a["id"]) in q["ids"]]

        off, lim = int(q.get("offset", [0])[0]), int(q.get("limit", [10 ** 6])[0])
        out = {"admins": rows[off:off + lim], "total": len(rows)}
        out.update({s: sum(1 for a in rows if a["status"] == s) for s in ("active", "disabled", "limited")})
        return out

    def simple_admins(self, q, body):
        rows = [{"id": a["id"], "username": a["username"]} for a in self.admins.values()]
        return {"admins": rows, "total": len(rows)}

    def get_roles(self, q, body):
        if not self.roles_ok:
            raise pg_error(403, "You're not allowed")

        return {"roles": [{"id": i, "name": role(i)["name"], "is_owner": i == 1} for i in (2, 1, 3)], "total": 3}

    def not_allowed(self, q, body):
        """The real panel has no POST /api/admins: the admin is created with POST /api/admin (found out on the live panel: 405)."""
        raise pg_error(405, "Method Not Allowed")

    def create_admin(self, q, body):
        if any(a["username"] == body["username"] for a in self.admins.values()):
            raise pg_error(409, "Admin already exists.")

        bad = pa.password_problems(body["password"], body["username"])

        if bad:
            raise pg_422("password", "; ".join(bad), body["password"])

        if body.get("role_id") == 1:
            raise pg_error(403, "Owner role cannot be assigned via this endpoint. Use the setup flow.")

        i, self.next_admin = self.next_admin, self.next_admin + 1
        a = adm(i, body["username"], body.get("role_id", 3), **{k: v for k, v in body.items() if k in ("telegram_id", "discord_webhook", "note", "sub_domain", "profile_title", "support_url", "data_limit")})
        self.admins[i] = a
        self.passwords[i] = body["password"]
        return a

    def put_admin(self, ref, q, body):
        a = self.one(ref)

        if a["role"]["is_owner"] and a["username"] != self.me:
            raise pg_error(403, "Owner cannot be modified via this endpoint. Use the setup flow.")

        if body.get("password"):
            bad = pa.password_problems(body["password"], a["username"])

            if bad:
                raise pg_422("password", "; ".join(bad), body["password"])

            self.passwords[a["id"]] = body["password"]

        for k, v in body.items():
            if k == "password":
                continue

            if k == "data_limit":
                a["data_limit"] = v if v > 0 else None
            elif k == "role_id":
                a["role"] = role(v)
            elif k == "status":
                a["status"], a["is_disabled"] = v, v == "disabled"
            else:
                a[k] = v

        return a

    def del_admin(self, ref, q, body):
        a = self.one(ref)
        del self.admins[a["id"]]

    def bulk_delete(self, q, body):
        gone = []

        for i in body["ids"]:
            a = self.one(i)

            if a["role"]["is_owner"]:
                raise pg_error(403, "Owner cannot be deleted via this endpoint. Use the setup flow.")

            gone.append(a)

        for a in gone:
            del self.admins[a["id"]]

        return {"admins": [a["username"] for a in gone], "count": len(gone)}

    def reset_admin(self, ref, q, body):
        a = self.one(ref)
        a["used_traffic"] = 0

        if a["status"] == "limited":
            a["status"], a["is_limited"] = "active", False

        return a

    def users_action(self, ref, action, q, body):
        self.one(ref)
        return {}

    def users_delete(self, ref, q, body):
        a = self.one(ref)
        n, a["total_users"] = a["total_users"], 0
        return {"detail": f"{n} users deleted"}

    def get_usage(self, ref, q, body):
        a = self.one(ref)
        start, end = q["start"][0], q["end"][0]

        if start[-6:] != end[-6:]:
            raise pg_error(400, "Start and end dates must have the same timezone")

        return {"period": "day", "start": start, "end": end, "stats": {"-1": self.usage.get(a["id"], [])}}

    # ---- API keys
    def key_dict(self, k):
        return {x: k[x] for x in k if x != "secret"}

    def list_keys(self, q, body):
        rows = sorted(self.keys.values(), key=lambda k: -k["id"])
        off, lim = int(q.get("offset", [0])[0]), int(q.get("limit", [50])[0])
        return {"api_keys": [self.key_dict(k) for k in rows[off:off + lim]], "total": len(rows)}

    def new_secret(self):
        raw = "pg_key_" + str(uuid.uuid4())
        return raw, f"pg_key_{raw[7:10]}***{raw[-3:]}"

    def create_key(self, q, body):
        if any(k["name"] == body["name"] and k["admin_id"] == body.get("admin_id", 1) for k in self.keys.values()):
            raise pg_error(409, "API key name already exists")

        raw, trimmed = self.new_secret()
        i, self.next_key = self.next_key, self.next_key + 1
        k = {"id": i, "name": body["name"], "note": body.get("note"), "admin_id": body.get("admin_id", 1), "permissions": {} if body["inherit_permissions"] else body.get("permissions", {}),
             "inherit_permissions": body["inherit_permissions"], "expire_date": body.get("expire_date"), "created_at": "2026-10-14T01:00:00Z", "api_key_trimmed": trimmed,
             "revoked_at": None, "status": "active", "is_expired": False}
        self.keys[i] = k
        self.secrets[i] = raw
        return {**k, "api_key": raw}

    def get_key(self, i, q, body):
        if int(i) not in self.keys:
            raise pg_error(404, "API key not found")

        return self.key_dict(self.keys[int(i)])

    def patch_key(self, i, q, body):
        k = self.keys.get(int(i))

        if k is None:
            raise pg_error(404, "API key not found")

        for f, v in body.items():
            k[f] = v

        if body.get("inherit_permissions") is True:
            k["permissions"] = {}

        return self.key_dict(k)

    def delete_key(self, i, q, body):
        self.get_key(i, q, body)
        del self.keys[int(i)]

    def revoke_key(self, i, q, body):
        k = self.keys[int(i)]
        raw, k["api_key_trimmed"] = self.new_secret()
        self.secrets[k["id"]] = raw
        k["revoked_at"], k["status"] = "2026-10-14T02:00:00Z", "active"
        return {**k, "api_key": raw}

    def bulk_delete_keys(self, q, body):
        names = []

        for i in body["ids"]:
            self.get_key(i, q, body)
            names.append(self.keys[i]["name"])

        for i in body["ids"]:
            del self.keys[i]

        return {"api_keys": names, "count": len(names)}

    # ---- settings
    def read(self, joined=False):
        out = fill(self.defaults, self.settings)

        if joined:                              # the answer to a write: the custom variables of the subscription are in `general` too
            out["general"]["custom_variables"] = copy.deepcopy(out["subscription"].get("custom_variables") or [])

        return out

    def get_settings(self, q, body):
        return self.read()

    def put_settings(self, q, body):
        old = copy.deepcopy(self.settings)

        for sec, val in body.items():
            new = drop_none(val)
            merged = fill(self.defaults[sec], new)

            if sec == "telegram" and merged["enable"]:
                need = ("token",) if merged["method"] == "long-polling" else ("token", "webhook_url", "webhook_secret")

                if not all(merged[k] for k in need):
                    raise pg_422("telegram", "Telegram bot cannot be enabled without " + ", ".join(need) + ".", merged)

            if sec == "telegram" and merged["method"] != "webhook":
                new.pop("webhook_url", None)                          # the panel's model: no webhook address unless the method is webhook

            if sec == "general" and val.get("custom_variables") is not None:
                self.settings["subscription"]["custom_variables"] = val["custom_variables"]
                new.pop("custom_variables", None)

            self.settings[sec] = new

        for path in self.ignore:
            sec, field = path.split(".")
            self.settings[sec][field] = old[sec].get(field)

        return self.read(joined=True)

    def puts(self):
        return self.sent("PUT", "/api/settings", exact=True)

    def sent(self, method, part="", exact=False):
        """The bodies of the calls of one method whose path holds `part` (or is it), oldest first."""
        return [b for m, p, b in self.calls if m == method and (p == part if exact else part in p)]


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    d = get_settings().data_dir / "pg-backups"

    if d.exists():
        for p in d.glob("panel*"):
            if re.match(r"panel\d+-(settings|admin|apikey)", p.name):
                p.unlink()

    return f


def backup_files():
    """The backup files of this tab (the folder is shared with the other PasarGuard tabs, whose files are not counted)."""
    d = get_settings().data_dir / "pg-backups"
    return sorted(p for p in d.glob("panel*") if re.match(r"panel\d+-(settings|admin|apikey)-", p.name)) if d.exists() else []


# ------------------------------------------------------------------ pure helpers
def test_password_rules_are_the_panels_rules_and_all_broken_ones_are_named():
    assert pa.password_problems(GOOD_PW, "someone") == []
    bad = pa.password_problems("abc", "abc")
    assert len(bad) == 5 and "at least 12 characters" in bad[0] and any("username" in b for b in bad)
    assert pa.password_problems("Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11-Aa11", "x")[0].startswith("Password too long")
    assert any("double quote" in b for b in pa.password_problems('Abcd-1234-Efg"-5678', "x"))
    assert any("special" in b for b in pa.password_problems("Abcd1234Efgh5678", "x"))
    assert any("2 digits" in b for b in pa.password_problems("Abcd-1xxx-Efgh-Xxxx", "x"))
    assert pa.password_problems(GOOD_PW, "ABCD-1234") != []                                      # the username inside, whatever its case
    assert pa.password_problems("") != [] and pa.password_problems(None) != []


def test_usernames():
    assert pa.clean_username("  nk-test.admin@1 ") == "nk-test.admin@1"

    for bad in ("ab", "x" * 35, "has space", "slash/name", "", None):
        with pytest.raises(ValueError):
            pa.clean_username(bad)


def test_tehran_days_begin_at_2030_utc_of_the_day_before():
    r = pa.tehran_range("2026-10-14", "2026-10-15")
    assert r["start"] == "2026-10-14T00:00:00+03:30" and r["end"] == "2026-10-15T23:59:59+03:30"
    assert r["from_utc"] == "2026-10-13T20:30:00Z" and r["to_utc"] == "2026-10-15T20:29:59Z" and r["days"] == ["2026-10-14", "2026-10-15"]
    assert r["start"][-6:] == r["end"][-6:] == "+03:30"                                         # the panel refuses two different offsets

    for a, b in (("2026-10-15", "2026-10-14"), ("x", "2026-10-14"), ("2026-10-14", "2026-10-1"), ("2025-01-01", "2026-10-14")):
        with pytest.raises(ValueError):
            pa.tehran_range(a, b)


def test_usage_days_put_every_bucket_on_its_tehran_day_and_fill_the_gaps():
    raw = {"stats": {"1": [{"period_start": "2026-10-13T20:30:00Z", "total_traffic": 5}, {"period_start": "2026-10-14T00:00:00+03:30", "total_traffic": 7}],
                     "2": [{"period_start": "2026-10-16T00:00:00+03:30", "total_traffic": 11}, {"period_start": "junk", "total_traffic": 1}, {"period_start": "2030-01-01T00:00:00+03:30", "total_traffic": 9}]}}
    days = pa.usage_days(raw, ["2026-10-14", "2026-10-15", "2026-10-16"])
    assert days == [{"date": "2026-10-14", "bytes": 12}, {"date": "2026-10-15", "bytes": 0}, {"date": "2026-10-16", "bytes": 11}]
    assert pa.usage_days(None, ["2026-10-14"]) == [{"date": "2026-10-14", "bytes": 0}]


def test_an_admin_row_has_no_discord_address_and_speaks_gb_and_days():
    a = adm(2, "alice", 3, users=5, used=5 * GB, limit=10 * GB, discord_webhook=DISCORD, telegram_id=77)
    a["permission_overrides"].update(data_limit_max=3 * GB, expire_min=2 * 86400, max_users=9)
    row = pa.admin_row(a, "alice")
    no_secret(row)
    assert row["has_discord"] is True and row["me"] is True and row["limit"] == 10 * GB and row["telegram_id"] == 77 and row["role"]["name"] == "operator"
    assert row["role"]["disconnects"] is True and pa.admin_row(adm(3, "bob", 2))["role"]["disconnects"] is False                   # the confirmation to switch an admin off needs to know
    assert row["overrides"]["data_limit_max_gb"] == 3 and row["overrides"]["expire_min_days"] == 2 and row["overrides"]["max_users"] == 9 and row["overrides"]["data_limit_min_gb"] is None
    assert pa.admin_row(adm(1, "o", 1))["is_owner"] is True and pa.admin_row({"username": "old", "is_sudo": True})["role"]["name"] == "sudo"
    assert pa.admin_row({"username": "x", "is_disabled": True})["status"] == "disabled" and pa.admin_row(adm(4, "z", limit=0))["limit"] is None


def test_fields_body_sends_only_what_differs():
    cur = pa.admin_row(adm(2, "alice", 3, limit=10 * GB, telegram_id=555, note="n"))
    assert pa.fields_body({}, cur) == {} and pa.fields_body({"data_limit_gb": 10, "telegram_id": 555, "note": "n"}, cur) == {}
    assert pa.fields_body({"data_limit_gb": 10.001}, cur) == {}                              # the tab shows two decimals: an unchanged field must not rewrite the limit
    assert pa.fields_body({"data_limit_gb": 20}, cur) == {"data_limit": 20 * GB} and pa.fields_body({"data_limit_gb": 0}, cur) == {"data_limit": 0}
    assert pa.fields_body({"telegram_id": 556}, cur) == {"telegram_id": 556} and pa.fields_body({"note": "new", "sub_domain": " d.example.test "}, cur) == {"note": "new", "sub_domain": "d.example.test"}

    with pytest.raises(ValueError, match="not removed"):
        pa.fields_body({"telegram_id": None}, cur)

    assert pa.fields_body({"telegram_id": None}, pa.admin_row(adm(3, "bob"))) == {}

    with pytest.raises(ValueError, match="at most 500"):
        pa.fields_body({"note": "x" * 501}, cur)

    assert pa.fields_body({"discord_webhook": DISCORD}, cur) == {"discord_webhook": DISCORD}

    with pytest.raises(ValueError, match="discord.com"):
        pa.fields_body({"discord_webhook": "https://example.test/hook"}, cur)

    assert pa.fields_body({"clear_discord": True}, cur) == {} and pa.fields_body({"clear_discord": True}, {**cur, "has_discord": True}) == {"discord_webhook": ""}
    assert pa.fields_body({"notify": {"create": True}}, cur) == {}
    assert pa.fields_body({"notify": {"delete": False}}, cur)["notification_enable"] == {**{k: True for k in pa._NOTIFY}, "delete": False}
    want = pa.fields_body({"overrides": {"max_users": 5, "data_limit_max_gb": 2, "expire_max_days": 30}}, cur)["permission_overrides"]
    assert want["max_users"] == 5 and want["data_limit_max"] == 2 * GB and want["expire_max"] == 30 * 86400 and want["data_limit_min"] is None and len(want) == 9
    assert pa.fields_body({"role_id": 3}, cur) == {} and pa.fields_body({"role_id": 2}, cur) == {"role_id": 2}


def test_a_refusal_is_read_out_of_the_422_list_and_no_secret_stays_in_it():
    short = pg_422("password", "bad", "Secretvalue-123")
    assert "Secretvalue-123" in str(short)                                                    # panels.call cuts the list into text that holds the value it refused
    assert pa._clean(str(short), ("Secretvalue-123",)) == "HTTP 422: password: bad"
    assert pa._clean(str(short)) == "HTTP 422: password: bad"                                 # even when the caller does not know the secret
    err = pg_422("password", "Password cannot contain the username", "Secretvalue-123")      # a longer sentence pushes the value out of the 120 characters
    text = pa._clean(str(err), ("Secretvalue-123",))
    assert text == "HTTP 422: password: Password cannot contain the username"
    assert "Secretvalue" not in pa._clean("HTTP 400: the value Secretvalue-123 is wrong", ("Secretvalue-123",))
    assert pa._clean("HTTP 422: [{'type': 'x', 'loc': ['body', 'a'], 'msg': 'bad', 'input': 'Secretvalue-123', 'ctx'", ("Secretvalue-123",)) == "HTTP 422: a: bad"
    assert pa._clean("no answer from the panel (ConnectError)") == "no answer from the panel (ConnectError)"
    assert pa._clean("HTTP 422: [{'type': 'x', 'input': 'Secretvalue-123") == "HTTP 422: [{'type': 'x', 'input': ***"


def test_permission_lists_round_trip_and_unknown_names_are_refused():
    view = pa.perms_view({"users": {"create": True, "read": {"scope": 1}, "update": {"scope": 2}, "delete": True}, "settings": {"read": True}, "api_keys": {"read": {"scope": 2}}})
    assert view["users"]["create"] == "yes" and view["users"]["read"] == "own" and view["users"]["update"] == "all" and view["users"]["delete"] == "all" and view["users"]["set_owner"] == "no"
    assert view["settings"]["read"] == "yes" and view["api_keys"]["read"] == "all" and view["nodes"]["logs"] == "no" and set(view) == set(pa.PERMS)
    body = pa.perms_body(view)
    assert body["users"] == {"create": True, "read": {"scope": 1}, "update": {"scope": 2}, "delete": {"scope": 2}} and body["settings"] == {"read": True} and "nodes" not in body
    assert pa.perms_view(body) == view and pa.perms_view(None)["users"]["read"] == "no" and pa.perms_body({}) == {}

    for bad in ({"nope": {"read": "yes"}}, {"users": {"nope": "yes"}}, {"users": {"read": "yes"}}, {"settings": {"read": "own"}}, {"users": {"read": 5}}, [], {"users": 5}):
        with pytest.raises(ValueError):
            pa.perms_body(bad)

    assert [r["key"] for r in pa.PERM_SCHEMA] == list(pa.PERMS)


def test_expiry_is_the_end_of_a_tehran_day_in_the_future():
    assert pa.expiry_of("2099-01-31") == "2099-01-31T23:59:59+03:30"
    assert pa.expiry_day("2027-10-22T01:13:50Z") == "2027-10-22" and pa.expiry_day("2027-10-22T22:00:00Z") == "2027-10-23"              # 22:00 UTC is already the next day in Tehran
    assert pa.expiry_day(None) == "" and pa.expiry_day("junk") == ""

    for bad in ("2020-01-01", "tomorrow", "2101-01-01", ""):
        with pytest.raises(ValueError):
            pa.expiry_of(bad)


def test_the_key_nikatel_is_connected_with_is_told_by_its_trimmed_form():
    raw = "pg_key_" + "abc12345-1111-4222-8333-444455556def"
    assert pa.own_trimmed({"api_key": raw}) == "pg_key_abc***def" and pa.own_trimmed({"api_key_trimmed": "pg_key_abc***def"}) == "pg_key_abc***def"
    assert pa.own_trimmed({}) == "" and pa.own_trimmed({"api_key": "junk"}) == "" and pa.own_trimmed({"api_key_trimmed": "x"}) == ""


# ------------------------------------------------------------------ the description of the settings
def test_every_section_is_described_well():
    kinds = {"bool", "int", "text", "secret", "proxy", "enum", "ints", "group", "rows", "json"}
    assert [s["key"] for s in pa.SECTIONS] == ["telegram", "webhook", "notification_settings", "notification_enable", "subscription", "hwid", "general"]

    def walk(fields, where):
        keys = [f["key"] for f in fields]
        assert len(keys) == len(set(keys)), where

        for f in fields:
            assert f["kind"] in kinds and f["label"], (where, f)

            if f["kind"] == "enum":
                assert f["options"] and all(len(o) == 2 for o in f["options"])
            elif f["kind"] == "group":
                walk(f["fields"], where + "." + f["key"])
            elif f["kind"] == "rows":
                walk(f["cols"], where + "." + f["key"])
                assert not f.get("id_col") or f["id_col"] in [x["key"] for x in f["cols"]]

    for s in pa.SECTIONS:
        walk(s["fields"], s["key"])
        assert s["label"] and s["help"]

    json.dumps(pa.SECTIONS)                                                                  # it goes to the browser as it is


def test_the_values_for_the_browser_hide_every_secret():
    for s in pa.SECTIONS:
        out = pa.mask(s["fields"], LIVE[s["key"]])
        no_secret(out)

    tg = pa.mask(pa.BY_KEY["telegram"]["fields"], LIVE["telegram"])
    assert tg["token"] == {"set": True} and tg["webhook_secret"] == {"set": True} and tg["proxy_url"] == {"set": True, "hint": "socks5://***@203.0.113.9:1080"}
    assert tg["enable"] is True and tg["webhook_url"] == "https://panel.example.test/tg"
    hooks = pa.mask(pa.BY_KEY["webhook"]["fields"], LIVE["webhook"])["webhooks"]
    assert hooks == [{"url": "https://hook-a.example.test/x", "secret": {"set": True}, "_id": "https://hook-a.example.test/x"}, {"url": "https://hook-b.example.test/y", "secret": {"set": True}, "_id": "https://hook-b.example.test/y"}]
    assert pa.mask(pa.BY_KEY["telegram"]["fields"], {})["token"] == {"set": False}


def test_a_secret_changes_only_when_one_is_typed():
    f = pa.BY_KEY["telegram"]["fields"]
    cur = {**LIVE["telegram"], "an_extra_field": 7}
    same = pa.merge_fields(f, cur, {"for_admins_only": False, "token": "", "webhook_secret": None})
    assert same["token"] == TG_TOKEN and same["webhook_secret"] == TG_HOOK_SECRET and same["for_admins_only"] is False and same["an_extra_field"] == 7        # unknown fields pass through
    new_token = "987654321:" + "B" * 35
    assert pa.merge_fields(f, cur, {"token": f"  {new_token} "})["token"] == new_token
    assert pa.merge_fields(f, cur, {}, clear={"token", "proxy_url"})["token"] is None and pa.merge_fields(f, cur, {}, clear={"proxy_url"})["proxy_url"] is None
    assert cur["token"] == TG_TOKEN                                                          # the live copy is never changed

    with pytest.raises(ValueError, match="Bot token") as e:
        pa.merge_fields(f, cur, {"token": "not-a-token-typed-by-mistake"})

    assert "typed-by-mistake" not in str(e.value)

    for bad in ({"proxy_url": "ftp://x"}, {"webhook_secret": "has space"}, {"token": 5}, {"webhook_url": "ftp://x.example.test"}, {"method": "pigeon"}, {"enable": "yes"}):
        with pytest.raises(ValueError):
            pa.merge_fields(f, cur, bad)

    assert pa.merge_fields(f, cur, {"proxy_url": "http://203.0.113.4:3128"})["proxy_url"] == "http://203.0.113.4:3128"


def test_rows_with_secrets_keep_them_by_identity():
    f = pa.BY_KEY["webhook"]["fields"]
    a, b = LIVE["webhook"]["webhooks"]
    rows = pa.merge_fields(f, LIVE["webhook"], {"webhooks": [{"url": b["url"], "_id": b["url"]}, {"url": "https://hook-c.example.test/z", "secret": "hook-c-secret"}, {"url": "https://renamed.example.test/a", "_id": a["url"]}]})["webhooks"]
    assert rows == [b, {"url": "https://hook-c.example.test/z", "secret": "hook-c-secret"}, {"url": "https://renamed.example.test/a", "secret": HOOK_A}]        # order of the edit, secrets kept, a new row has its own

    for bad, msg in (([{"url": "https://new.example.test/n"}], "Secret is needed"), ([{"url": "ftp://x"}], "address"), ([{"url": a["url"], "_id": a["url"]}, {"url": a["url"], "_id": a["url"]}], "twice"),
                     ("junk", "list"), ([5], "not valid"), ([{"url": f"https://h{i}.example.test", "secret": "s"} for i in range(21)], "at most 20")):
        with pytest.raises(ValueError, match=msg):
            pa.merge_fields(f, LIVE["webhook"], {"webhooks": bad})


def test_values_are_checked_by_their_kind():
    sub = pa.BY_KEY["subscription"]["fields"]
    cur = LIVE["subscription"]
    out = pa.merge_fields(sub, cur, {"update_interval": "6", "announce": " hi ", "support_url": "", "response_headers": {"a": "b"}, "manual_sub_request": {"clash": False}})
    assert out["update_interval"] == 6 and out["announce"] == "hi" and out["support_url"] == "" and out["manual_sub_request"]["clash"] is False and out["manual_sub_request"]["xray"] is True
    assert out["custom_variables"] == cur["custom_variables"] and out["rules"] == cur["rules"]

    for bad in ({"update_interval": 0}, {"update_interval": "x"}, {"update_interval": 1.5}, {"update_interval": True}, {"announce": "x" * 129}, {"announce": 5}, {"response_headers": []},
                {"rules": [{"pattern": "", "target": "clash"}]}, {"rules": [{"pattern": "a", "target": "pigeon"}]}):
        with pytest.raises(ValueError):
            pa.merge_fields(sub, cur, bad)

    hw = pa.BY_KEY["hwid"]["fields"]
    assert pa.merge_fields(hw, LIVE["hwid"], {"fallback_limit": "", "max_limit": 5})["max_limit"] == 5 and pa.merge_fields(hw, LIVE["hwid"], {"min_limit": None})["min_limit"] is None
    pct = pa.BY_KEY["notification_enable"]["fields"]
    assert pa.merge_fields(pct, LIVE["notification_enable"], {"admin": {"usage_limit_warning_percentages": "90, 70 70"}})["admin"]["usage_limit_warning_percentages"] == [70, 90]

    with pytest.raises(ValueError):
        pa.merge_fields(pct, LIVE["notification_enable"], {"admin": {"usage_limit_warning_percentages": "101"}})


def test_a_row_that_leaves_columns_out_gets_their_empty_value():
    sub = pa.BY_KEY["subscription"]["fields"]
    out = pa.merge_fields(sub, LIVE["subscription"], {"rules": [{"pattern": "^Block", "target": "block"}]})
    assert out["rules"] == [{"pattern": "^Block", "target": "block", "response_headers": {}}]                          # found out against the real PasarGuard models: the headers of a rule are optional

    with pytest.raises(ValueError, match="Answer with"):
        pa.merge_fields(sub, LIVE["subscription"], {"rules": [{"pattern": "^x"}]})

    gen = pa.BY_KEY["general"]["fields"]
    assert pa.merge_fields(gen, LIVE["general"], {"custom_variables": [{"key": "A"}]})["custom_variables"] == [{"key": "A", "value": ""}]
    assert pa._empty_of({"kind": "ints"}) == [] and pa._empty_of({"kind": "bool"}) is False and pa._empty_of({"kind": "text", "nullable": True}) is None and pa._empty_of({"kind": "json", "type": "array"}) == []


def test_what_goes_into_a_backup_is_only_known_and_never_secret():
    for s in pa.SECTIONS:
        no_secret(pa.plain(s["fields"], {**LIVE[s["key"]], "surprise": DISCORD}))

    assert "surprise" not in pa.plain(pa.BY_KEY["telegram"]["fields"], {**LIVE["telegram"], "surprise": 1})
    wh = pa.plain(pa.BY_KEY["webhook"]["fields"], LIVE["webhook"])
    assert wh["webhooks"] == [{"url": "https://hook-a.example.test/x"}, {"url": "https://hook-b.example.test/y"}] and "token" not in pa.plain(pa.BY_KEY["telegram"]["fields"], LIVE["telegram"])


def test_the_changes_are_listed_without_the_value_of_a_secret():
    f = pa.BY_KEY["telegram"]["fields"]
    new = pa.merge_fields(f, LIVE["telegram"], {"for_admins_only": False, "token": "987654321:" + "B" * 35}, clear={"proxy_url"})
    ch = pa.diff(f, LIVE["telegram"], new)
    assert {c["path"] for c in ch} == {"for_admins_only", "token", "proxy_url"}
    by = {c["path"]: c for c in ch}
    assert by["token"] == {"path": "token", "label": "Bot token", "secret": "replaced"} and by["proxy_url"]["secret"] == "removed" and by["for_admins_only"]["before"] is True and by["for_admins_only"]["after"] is False
    assert "B" * 35 not in json.dumps(ch) and pa.diff(f, LIVE["telegram"], LIVE["telegram"]) == []
    rows = pa.merge_fields(pa.BY_KEY["webhook"]["fields"], LIVE["webhook"], {"webhooks": [{"url": "https://hook-a.example.test/x", "_id": "https://hook-a.example.test/x", "secret": "brand-new-one"}]})
    ch = pa.diff(pa.BY_KEY["webhook"]["fields"], LIVE["webhook"], rows)
    assert ch[0]["after"] == [{"url": "https://hook-a.example.test/x", "secret": "changed"}] and "brand-new-one" not in json.dumps(ch)
    assert pa.diff(f, {"x": None}, {"x": ""}) == [] and pa.diff(f, {"method": ""}, {"method": None}) == []                  # null and empty are the same


def test_rules_that_look_at_several_fields_are_the_panels_rules():
    tg = dict(LIVE["telegram"])
    pa.check_section("telegram", tg)

    for broken, why in (({"token": None}, "token"), ({"webhook_url": None}, "webhook address"), ({"webhook_secret": None}, "webhook secret")):
        with pytest.raises(ValueError, match=why):
            pa.check_section("telegram", {**tg, **broken})

    pa.check_section("telegram", {**tg, "enable": False, "token": None})
    pa.check_section("telegram", {**tg, "method": "long-polling", "webhook_url": None, "webhook_secret": None})

    with pytest.raises(ValueError, match="token"):
        pa.check_section("telegram", {**tg, "method": "long-polling", "token": None})

    with pytest.raises(ValueError, match="receiver"):
        pa.check_section("webhook", {"enable": True, "webhooks": []})

    ns = LIVE["notification_settings"]

    with pytest.raises(ValueError, match="Discord"):
        pa.check_section("notification_settings", {**ns, "discord_webhook_url": None})

    with pytest.raises(ValueError, match="chat id"):
        pa.check_section("notification_settings", {**ns, "notify_telegram": True, "telegram_api_token": TG_TOKEN})

    with pytest.raises(ValueError, match="Fewest"):
        pa.check_section("hwid", {"min_limit": 5, "max_limit": 2})

    with pytest.raises(ValueError, match="regular expression"):
        pa.check_section("subscription", {"rules": [{"pattern": "(", "target": "clash"}], "applications": []})

    app = {"name": "App", "platform": "android", "download_links": [{"name": "Store", "url": "https://store.example.test", "language": "en"}], "import_url": "app://add?u={url}", "recommended": True}
    pa.check_section("subscription", {"rules": [], "applications": [app]})

    for broken in ({"platform": "amiga"}, {"name": ""}, {"import_url": "app://add"}, {"download_links": [{"name": "x"}]}, {"description": {"xx": "y"}}):
        with pytest.raises(ValueError, match="Applications"):
            pa.check_section("subscription", {"rules": [], "applications": [{**app, **broken}]})

    with pytest.raises(ValueError, match="Applications"):
        pa.check_section("subscription", {"rules": [], "applications": [app, {**app, "name": "Other"}]})                        # two recommended for one platform

    gen = pa.BY_KEY["general"]["fields"]
    out = pa.merge_fields(gen, LIVE["general"], {"custom_variables": [{"key": " {brand_name} ", "value": "Example {USERNAME}"}]})
    assert out["custom_variables"] == [{"key": "BRAND_NAME", "value": "Example {USERNAME}"}]
    pa.check_section("general", out)

    for rows, msg in (([{"key": "USERNAME", "value": ""}], "built-in"), ([{"key": "A", "value": ""}, {"key": "a", "value": ""}], "twice"), ([{"key": "A", "value": "{"}], "placeholder")):
        with pytest.raises(ValueError, match=msg):
            pa.check_section("general", pa.merge_fields(gen, LIVE["general"], {"custom_variables": rows}))

    with pytest.raises(ValueError, match="capital letters"):
        pa.merge_fields(gen, LIVE["general"], {"custom_variables": [{"key": "1bad", "value": ""}]})


# ------------------------------------------------------------------ the admins
def test_the_admin_list_has_the_rows_the_counts_who_we_are_and_the_roles(fake):
    r = pa.admins_list(C)
    assert [a["username"] for a in r["admins"]] == ["alice", "bob", "moderator"] and r["total"] == 3 and r["active"] == 3 and r["disabled"] == 0
    assert r["me"] == {"username": "moderator", "role": {"id": 1, "name": "owner", "is_owner": True, "disconnects": False}, "is_owner": True} and [a["me"] for a in r["admins"]] == [False, False, True]
    assert {x["name"] for x in r["roles"]} == {"owner", "administrator", "operator"} and r["roles_supported"] is True and r["truncated"] is False
    alice = r["admins"][0]
    assert alice["users"] == 5 and alice["used"] == 5 * GB and alice["limit"] == 10 * GB
    fake.roles_ok = False
    assert pa.admins_list(C)["roles"] == [] and pa.admins_list(C)["roles_supported"] is False


def test_a_long_list_of_admins_is_read_page_by_page_in_a_fixed_order(fake):
    for i in range(10, 260):
        fake.admins[i] = adm(i, f"user{i:04d}")

    r = pa.admins_list(C)
    names = [a["username"] for a in r["admins"]]
    assert len(names) == 253 and len(set(names)) == 253 and r["total"] == 253
    paged = [p for m, p in [(m, p) for m, p, b in fake.calls] if p.startswith("/api/admins?")]
    assert len(paged) == 3 and all("sort=username" in p for p in paged) and "offset=100" in paged[1] and "limit=100" in paged[0]


def test_create_an_admin_checks_the_password_first_and_keeps_it_nowhere(fake):
    out = pa.admin_create(C, {"username": "nk-test-admin", "password": GOOD_PW, "role_id": 3, "data_limit_gb": 50, "telegram_id": 4242, "discord_webhook": DISCORD, "note": "made by a test"})
    assert out["username"] == "nk-test-admin" and out["role"]["name"] == "operator" and out["limit"] == 50 * GB and out["telegram_id"] == 4242 and out["has_discord"] is True
    no_secret(out)
    assert GOOD_PW not in json.dumps(out)
    post = fake.sent("POST", "/api/admin", exact=True)[0]
    assert post["password"] == GOOD_PW and post["role_id"] == 3 and post["data_limit"] == 50 * GB and post["discord_webhook"] == DISCORD and "is_sudo" not in post
    assert all(GOOD_PW not in p.read_text() and DISCORD not in p.read_text() for p in backup_files())

    with pytest.raises(ValueError) as e:
        pa.admin_create(C, {"username": "nk-test-two", "password": "short", "role_id": 3})

    assert str(e.value).count(";") >= 3 and "at least 12" in str(e.value)

    for data, msg in (({"username": "ab", "password": GOOD_PW, "role_id": 3}, "username"), ({"username": "nk-test-x", "password": GOOD_PW}, "pick a role"),
                      ({"username": "nk-test-x", "password": GOOD_PW, "role_id": 1}, "owner role"), ({"username": "nk-test-x", "password": GOOD_PW, "role_id": 99}, "no such role"),
                      ({"username": "nk-test-x", "password": GOOD_PW, "role_id": 3, "discord_webhook": "https://example.test/x"}, "discord.com")):
        with pytest.raises(ValueError, match=msg):
            pa.admin_create(C, data)

    before = len(fake.admins)

    with pytest.raises(PanelError, match="already exists"):
        pa.admin_create(C, {"username": "alice", "password": GOOD_PW, "role_id": 3})

    assert len(fake.admins) == before


def test_what_the_panel_refuses_is_shown_without_the_password(fake, monkeypatch):
    fake.fail[("POST", "/api/admin")] = pg_422("password", "Password cannot contain the username", GOOD_PW)

    with pytest.raises(PanelError) as e:
        pa.admin_create(C, {"username": "nk-test-admin", "password": GOOD_PW, "role_id": 3})

    assert str(e.value) == "HTTP 422: password: Password cannot contain the username" and e.value.code == 422 and GOOD_PW not in str(e.value) and e.value.__cause__ is None


def test_a_panel_without_roles_gets_is_sudo(fake):
    fake.roles_ok = False
    pa.admin_create(C, {"username": "nk-test-old", "password": GOOD_PW, "is_sudo": True})
    post = fake.sent("POST", "/api/admin", exact=True)[0]
    assert post["is_sudo"] is True and "role_id" not in post


def test_update_an_admin_sends_the_difference_only(fake):
    out = pa.admin_update(C, 2, {"data_limit_gb": 20, "note": "hello", "telegram_id": 99, "role_id": 2, "discord_webhook": DISCORD})
    assert out["limit"] == 20 * GB and out["note"] == "hello" and out["telegram_id"] == 99 and out["role"]["name"] == "administrator" and out["has_discord"] is True
    no_secret(out)
    put = fake.sent("PUT")[0]
    assert put == {"data_limit": 20 * GB, "note": "hello", "telegram_id": 99, "role_id": 2, "discord_webhook": DISCORD}
    n = len(fake.calls)

    with pytest.raises(ValueError, match="nothing to change"):
        pa.admin_update(C, 2, {"data_limit_gb": 20, "note": "hello"})

    assert not [1 for m, p, b in fake.calls[n:] if m == "PUT"]

    with pytest.raises(ValueError, match="role"):
        pa.admin_update(C, 1, {"role_id": 2})                                                # the admin Nikatel is signed in as

    fake.me = "alice"

    with pytest.raises(ValueError, match="owner"):
        pa.admin_update(C, 1, {"role_id": 2})

    with pytest.raises(ValueError, match="does not exist"):
        pa.admin_update(C, 777, {"note": "x"})


def test_a_password_is_changed_only_for_another_admin_and_a_good_one(fake):
    pa.admin_password(C, 2, GOOD_PW)
    assert fake.passwords[2] == GOOD_PW
    assert fake.sent("PUT")[0] == {"password": GOOD_PW}

    with pytest.raises(ValueError, match="signed in as"):
        pa.admin_password(C, 1, GOOD_PW)

    with pytest.raises(ValueError, match="at least 12"):
        pa.admin_password(C, 3, "weak")

    with pytest.raises(ValueError, match="username"):
        pa.admin_password(C, 3, GOOD_PW.replace("Abcd", "bob") + "bobbob")


def test_switch_an_admin_off_and_on_but_never_the_one_we_sign_in_as(fake):
    assert pa.admin_status(C, 2, "disabled")["status"] == "disabled" and fake.admins[2]["status"] == "disabled"
    n = len(fake.calls)
    assert pa.admin_status(C, 2, "disabled")["status"] == "disabled" and not [1 for m, p, b in fake.calls[n:] if m == "PUT"]                  # already so: nothing is sent
    assert pa.admin_status(C, 2, "active")["status"] == "active"

    with pytest.raises(ValueError, match="lock this panel out"):
        pa.admin_status(C, 1, "disabled")

    with pytest.raises(ValueError, match="active or disabled"):
        pa.admin_status(C, 2, "limited")


def test_reset_usage(fake):
    assert pa.admin_reset_usage(C, 2)["used"] == 0 and fake.admins[2]["used_traffic"] == 0


def test_all_the_users_of_an_admin_switched_off_on_and_deleted(fake):
    assert pa.admin_users_action(C, 2, "disable") == {"admin": "alice", "action": "disable", "users": 5}
    assert pa.admin_users_action(C, 2, "activate")["action"] == "activate"

    with pytest.raises(ValueError, match="disable or activate"):
        pa.admin_users_action(C, 2, "wipe")

    with pytest.raises(ValueError, match="type the username"):
        pa.admin_users_delete(C, 2, "")

    assert fake.admins[2]["total_users"] == 5 and not [1 for m, p, b in fake.calls if m == "DELETE"]
    out = pa.admin_users_delete(C, 2, "alice")
    assert out["deleted"] == 5 and fake.admins[2]["total_users"] == 0 and out["record"].startswith("panel901-admin-alice-")
    doc = json.loads((get_settings().data_dir / "pg-backups" / out["record"]).read_text())
    assert doc["kind"] == "admin" and doc["admin"]["username"] == "alice" and doc["admin"]["users"] == 5 and "me" not in doc["admin"]


def test_delete_an_admin_with_a_record_and_a_choice_for_its_users(fake):
    fake.admins[5] = adm(5, "carol", 3, users=4, discord_webhook=DISCORD)
    fake.admins[6] = adm(6, "dave", 3, users=2)
    fake.admins[7] = adm(7, "erin", 3, users=0)

    with pytest.raises(ValueError, match="owner"):
        pa.admin_delete(C, 1)

    fake.admins[8] = adm(8, "frank", 1)

    with pytest.raises(ValueError, match="owner"):
        pa.admin_delete(C, 8)

    with pytest.raises(ValueError, match="users must be"):
        pa.admin_delete(C, 5, "burn")

    with pytest.raises(ValueError, match="type the username"):
        pa.admin_delete(C, 5, "delete", "nope")

    assert 5 in fake.admins
    out = pa.admin_delete(C, 5, "keep")
    assert out["deleted"] == "carol" and out["users"] == 4 and out["users_deleted"] == 0 and 5 not in fake.admins
    assert ("POST", "/api/admins/bulk/delete", {"ids": [5]}) in fake.calls                  # the route that detaches the users itself
    doc = json.loads((get_settings().data_dir / "pg-backups" / out["record"]).read_text())
    assert doc["admin"]["username"] == "carol" and doc["admin"]["has_discord"] is True and DISCORD not in json.dumps(doc)
    out = pa.admin_delete(C, 6, "disable")
    assert [p for m, p, b in fake.calls if p.endswith("/users/disable")] == ["/api/admin/by-id/6/users/disable"] and 6 not in fake.admins and out["users_mode"] == "disable"
    fake.admins[9] = adm(9, "gina", 3, users=3)
    out = pa.admin_delete(C, 9, "delete", "gina")
    assert out["users_deleted"] == 3 and 9 not in fake.admins
    n = len(fake.calls)
    pa.admin_delete(C, 7, "delete", "erin")                                               # nobody to delete: no call for the users
    assert not [p for m, p, b in fake.calls[n:] if p.endswith("/users")]
    assert len([p for p in backup_files() if "-admin-" in p.name]) == 4


def test_delete_never_touches_the_admin_we_are_signed_in_as(fake):
    fake.me = "alice"

    with pytest.raises(ValueError, match="lock this panel out"):
        pa.admin_delete(C, 2)

    assert 2 in fake.admins and not [1 for m, p, b in fake.calls if m in ("DELETE",) or p.endswith("bulk/delete")]


def test_a_panel_from_before_the_by_id_routes_is_asked_by_username(fake):
    fake.legacy = True
    fake.admins[5] = adm(5, "carol", 3, users=1)
    assert pa.admin_status(C, 5, "disabled")["status"] == "disabled"
    assert ("PUT", "/api/admin/carol", {"status": "disabled"}) in fake.calls
    pa.admin_delete(C, 5, "keep")
    assert 5 not in fake.admins and ("DELETE", "/api/admin/carol", None) in fake.calls
    fake.fail[("PUT", "/api/admin/by-id/2")] = pg_error(404, "Admin not found.")
    fake.legacy = False

    with pytest.raises(PanelError, match="Admin not found"):                              # a real 404 is not mistaken for a missing route
        pa.admin_update(C, 2, {"note": "x"})


def test_usage_of_an_admin_per_tehran_day(fake):
    fake.usage[2] = [{"period_start": "2026-10-13T00:00:00+03:30", "total_traffic": 3 * GB}, {"period_start": "2026-10-15T00:00:00+03:30", "total_traffic": GB}]
    r = pa.admin_usage(C, 2, "2026-10-13", "2026-10-15")
    assert r["days"] == [{"date": "2026-10-13", "bytes": 3 * GB}, {"date": "2026-10-14", "bytes": 0}, {"date": "2026-10-15", "bytes": GB}] and r["total"] == 4 * GB
    assert r["peak"] == {"date": "2026-10-13", "bytes": 3 * GB} and r["from_utc"] == "2026-10-12T20:30:00Z" and r["tz"].startswith("Asia/Tehran")
    path = next(p for m, p, b in fake.calls if "/usage" in p)
    q = parse_qs(urlsplit(path).query)
    assert q["period"] == ["day"] and q["start"] == ["2026-10-13T00:00:00+03:30"] and q["end"] == ["2026-10-15T23:59:59+03:30"]
    assert "%2B03%3A30" in path                                                           # a bare + would reach the panel as a space
    assert pa.admin_usage(C, 3, "2026-10-13", "2026-10-13")["peak"] is None

    with pytest.raises(ValueError):
        pa.admin_usage(C, 2, "2026-10-15", "2026-10-13")


# ------------------------------------------------------------------ the API keys
def test_the_key_list_never_has_a_secret_and_marks_the_state(fake):
    made = pa.key_create(C, {"name": "nk-test-key", "inherit_permissions": True})
    pa.key_create(C, {"name": "nk-test-off", "inherit_permissions": True})
    fake.keys[2]["status"] = "disabled"
    fake.keys[1]["expire_date"], fake.keys[1]["is_expired"] = "2020-01-01T00:00:00Z", True
    r = pa.keys_list(C)
    assert [k["name"] for k in r["keys"]] == ["nk-test-off", "nk-test-key"] and [k["status"] for k in r["keys"]] == ["disabled", "expired"]
    assert made["secret"].startswith("pg_key_") and made["secret"] not in json.dumps(r) and r["keys"][1]["trimmed"] == fake.keys[1]["api_key_trimmed"]
    assert r["keys"][0]["admin"] == "moderator" and r["connected_known"] is False and r["me"]["is_owner"] is True and {a["username"] for a in r["admins"]} == {"moderator", "alice", "bob"}
    assert r["keys"][0]["inherit"] is True and r["keys"][0]["granted"] == 0 and r["permission_schema"] == pa.PERM_SCHEMA
    connected = pa.keys_list({**C, "api_key_trimmed": fake.keys[1]["api_key_trimmed"]})
    assert [k["connected"] for k in connected["keys"]] == [False, True]


def test_a_new_key_shows_its_secret_once_and_it_is_kept_nowhere(fake):
    out = pa.key_create(C, {"name": "nk-test-key", "note": "for a test", "inherit_permissions": False, "permissions": {"users": {"read": "all", "create": "yes"}, "system": {"read": "yes"}},
                            "expire": "2099-12-31", "admin_id": 2})
    secret = out["secret"]
    assert secret == fake.secrets[1] and re.fullmatch(r"pg_key_[0-9a-f-]{36}", secret)
    assert "api_key" not in out["key"] and secret not in json.dumps(out["key"]) and out["key"]["granted"] == 3 and out["key"]["inherit"] is False and out["key"]["admin"] == "alice"
    post = fake.sent("POST", "/api/api_key", exact=True)[0]
    assert post == {"name": "nk-test-key", "inherit_permissions": False, "note": "for a test", "permissions": {"users": {"read": {"scope": 2}, "create": True}, "system": {"read": True}},
                    "expire_date": "2099-12-31T23:59:59+03:30", "admin_id": 2}
    assert secret not in json.dumps(pa.keys_list(C)) and all(secret not in p.read_text() for p in backup_files())
    pa.key_revoke(C, 1)
    pa.key_delete(C, 1)
    assert all(secret not in p.read_text() for p in backup_files())
    folders = [get_settings().data_dir, get_settings().log_dir]
    assert not [p for d in folders for p in d.rglob("*") if p.is_file() and p.stat().st_size < 10 ** 7 and secret.encode() in p.read_bytes()]          # nothing in the data or the log folder


def test_key_creation_refusals(fake):
    for data, msg in (({"name": ""}, "name"), ({"name": "x" * 129}, "name"), ({"name": "k", "note": "x" * 513}, "512"), ({"name": "k", "inherit_permissions": False, "permissions": {}}, "at least one"),
                      ({"name": "k", "inherit_permissions": False}, "permissions"), ({"name": "k", "inherit_permissions": False, "permissions": {"users": {"nope": "yes"}}}, "no action"),
                      ({"name": "k", "expire": "2020-01-01"}, "future"), ({"name": "k", "expire": "soon"}, "look like")):
        with pytest.raises(ValueError, match=msg):
            pa.key_create(C, data)

    assert not fake.keys
    pa.key_create(C, {"name": "k"})

    with pytest.raises(PanelError, match="already exists"):
        pa.key_create(C, {"name": "k"})


def test_update_a_key_sends_the_difference_only(fake):
    pa.key_create(C, {"name": "nk-test-key", "expire": "2099-12-31"})
    fake.keys[1]["expire_date"] = "2099-12-31T10:00:00Z"                                  # the panel's own time of day
    n = len(fake.calls)

    with pytest.raises(ValueError, match="nothing to change"):
        pa.key_update(C, 1, {"name": "nk-test-key", "note": "", "expire": "2099-12-31", "status": "active", "inherit_permissions": True})

    assert not [1 for m, p, b in fake.calls[n:] if m == "PATCH"]
    out = pa.key_update(C, 1, {"name": "renamed", "note": "n", "status": "disabled", "expire": ""})
    assert out["name"] == "renamed" and out["status"] == "disabled" and out["expire_date"] is None
    assert fake.sent("PATCH")[0] == {"name": "renamed", "note": "n", "status": "disabled", "expire_date": None}                    # an explicit null removes the expiry
    out = pa.key_update(C, 1, {"inherit_permissions": False, "permissions": {"nodes": {"read": "yes"}}})
    assert out["inherit"] is False and out["granted"] == 1
    assert [b for m, p, b in fake.calls if m == "PATCH"][-1] == {"inherit_permissions": False, "permissions": {"nodes": {"read": True}}}
    n = len(fake.calls)

    with pytest.raises(ValueError, match="nothing to change"):
        pa.key_update(C, 1, {"permissions": {"nodes": {"read": "yes"}}})

    with pytest.raises(ValueError, match="at least one"):
        pa.key_update(C, 1, {"permissions": {}})

    out = pa.key_update(C, 1, {"inherit_permissions": True})
    assert out["inherit"] is True and [b for m, p, b in fake.calls if m == "PATCH"][-1] == {"inherit_permissions": True}

    with pytest.raises(ValueError, match="pick the permissions"):
        pa.key_update(C, 1, {"inherit_permissions": False})

    with pytest.raises(ValueError, match="active or disabled"):
        pa.key_update(C, 1, {"status": "expired"})

    with pytest.raises(PanelError, match="not found"):
        pa.key_update(C, 99, {"name": "x"})


def test_the_key_nikatel_is_connected_with_keeps_its_access(fake):
    pa.key_create(C, {"name": "nikatel-key", "expire": "2099-12-31"})
    pa.key_create(C, {"name": "other-key"})
    me = {**C, "api_key_trimmed": fake.keys[1]["api_key_trimmed"]}
    assert pa.key_update(me, 1, {"name": "renamed", "note": "n"})["name"] == "renamed"
    assert pa.key_update(me, 1, {"expire": "2100-01-01"})["expire_day"] == "2100-01-01"                          # a later day can not cut it off
    assert pa.key_update(me, 1, {"expire": ""})["expire_date"] is None

    for ch in ({"status": "disabled"}, {"expire": "2098-01-01"}, {"inherit_permissions": False, "permissions": {"nodes": {"read": "yes"}}}, {"admin_id": 2}):
        with pytest.raises(ValueError, match="connected"):
            pa.key_update(me, 1, ch)

    with pytest.raises(ValueError, match="renewing"):
        pa.key_revoke(me, 1)

    with pytest.raises(ValueError, match="deleting"):
        pa.key_delete(me, 1)

    with pytest.raises(ValueError, match="key renamed"):
        pa.keys_delete(me, [1, 2])

    secret = fake.secrets[1]
    assert set(fake.keys) == {1, 2} and fake.secrets[1] == secret                          # nothing was renewed or deleted
    assert pa.key_delete(me, 2)["deleted"] == "other-key"


def test_renew_and_delete_keys_with_a_record_without_the_secret(fake):
    first = pa.key_create(C, {"name": "nk-test-key", "inherit_permissions": False, "permissions": {"users": {"read": "own"}}})["secret"]
    pa.key_update(C, 1, {"status": "disabled"})
    r = pa.key_revoke(C, 1)
    assert r["secret"] != first and r["secret"] == fake.secrets[1] and r["key"]["status"] == "active" and r["key"]["revoked_at"] and r["record"].startswith("panel901-apikey-")
    doc = json.loads((get_settings().data_dir / "pg-backups" / r["record"]).read_text())
    assert doc["kind"] == "api_key" and doc["key"]["name"] == "nk-test-key" and doc["key"]["status"] == "disabled" and doc["key"]["permissions"]["users"]["read"] == "own"
    assert first not in json.dumps(doc) and r["secret"] not in json.dumps(doc)
    d = pa.key_delete(C, 1)
    assert d["deleted"] == "nk-test-key" and not fake.keys and d["record"]

    with pytest.raises(PanelError, match="not found"):
        pa.key_delete(C, 1)

    with pytest.raises(PanelError, match="not found"):
        pa.key_revoke(C, 1)


def test_delete_several_keys_all_or_none(fake):
    for n in ("a", "b", "c"):
        pa.key_create(C, {"name": n})

    with pytest.raises(ValueError, match="no key 9"):
        pa.keys_delete(C, [1, 9])

    with pytest.raises(ValueError, match="1 to 100"):
        pa.keys_delete(C, [])

    assert len(fake.keys) == 3 and not [1 for m, p, b in fake.calls if p.endswith("bulk/delete")]
    r = pa.keys_delete(C, [1, 3, 3])
    assert r["deleted"] == ["a", "c"] and r["count"] == 2 and set(fake.keys) == {2} and r["records"] == 2


# ------------------------------------------------------------------ the settings
def test_the_settings_for_the_browser_hold_no_secret_at_all(fake):
    r = pa.settings_get(C)
    no_secret(r)
    assert [s["key"] for s in r["sections"]] == [s["key"] for s in pa.SECTIONS] and r["backups"] == []
    assert r["values"]["telegram"]["token"] == {"set": True} and r["values"]["notification_settings"]["discord_webhook_url"] == {"set": True}
    assert r["values"]["general"]["custom_variables"] == [{"key": "BRAND", "value": "Example"}] and r["values"]["general"]["default_method"] == "chacha20-ietf-poly1305"      # the variables are in the subscription
    assert r["values"]["subscription"]["rules"][0]["target"] == "clash" and r["values"]["webhook"]["webhooks"][0]["_id"] == "https://hook-a.example.test/x"
    assert [m for m, p, b in fake.calls] == ["GET"] and not fake.puts()


def test_a_section_is_written_whole_with_the_secrets_it_had(fake):
    out = pa.settings_update(C, "telegram", {"for_admins_only": False, "token": "", "webhook_secret": ""})
    no_secret(out)
    assert out["written"] is True and [c["path"] for c in out["changes"]] == ["for_admins_only"] and out["warnings"] == []
    body = fake.puts()[0]
    assert list(body) == ["telegram"] and body["telegram"]["token"] == TG_TOKEN and body["telegram"]["webhook_secret"] == TG_HOOK_SECRET and body["telegram"]["proxy_url"] == PROXY
    assert body["telegram"]["for_admins_only"] is False
    assert fake.settings["telegram"]["token"] == TG_TOKEN and fake.settings["telegram"]["for_admins_only"] is False                   # the secrets are still there on the panel
    assert out["values"]["token"] == {"set": True}


def test_a_typed_secret_replaces_and_an_explicit_clear_removes(fake):
    new = "987654321:" + "B" * 35
    out = pa.settings_update(C, "telegram", {"token": new})
    assert fake.settings["telegram"]["token"] == new and out["changes"] == [{"path": "token", "label": "Bot token", "secret": "replaced"}] and new not in json.dumps(out)
    out = pa.settings_update(C, "telegram", {"enable": False}, clear=["proxy_url", "webhook_secret"])
    assert fake.settings["telegram"].get("proxy_url") is None and fake.settings["telegram"].get("webhook_secret") is None and fake.settings["telegram"]["token"] == new
    assert {c["path"] for c in out["changes"]} == {"enable", "proxy_url", "webhook_secret"} and out["warnings"] == []


def test_a_bot_that_does_not_use_a_webhook_has_no_webhook_address(fake):
    out = pa.settings_update(C, "telegram", {"method": "long-polling"})
    assert {c["path"] for c in out["changes"]} == {"method", "webhook_url"} and out["warnings"] == []                      # the panel drops it, so the change says so and the read-back agrees
    assert fake.settings["telegram"].get("webhook_url") is None and fake.puts()[0]["telegram"]["webhook_url"] is None
    back = pa.settings_update(C, "telegram", {"method": "webhook", "webhook_url": "https://panel.example.test/tg"})
    assert back["warnings"] == [] and fake.settings["telegram"]["webhook_url"] == "https://panel.example.test/tg"
    assert pa.normalize_section("hwid", {"a": 1}) == {"a": 1}


def test_changes_leave_a_line_in_the_events_and_never_a_secret(fake, panel_row):
    from sqlalchemy import select

    from nikatel.models import Event

    c = panels.cred_for(panel_row[0])
    new_token = "987654321:" + "F" * 35
    made = pa.admin_create(c, {"username": "nk-test-admin", "password": GOOD_PW, "role_id": 3, "discord_webhook": DISCORD})
    pa.admin_update(c, made["id"], {"note": "n", "discord_webhook": DISCORD + "x"})
    pa.admin_status(c, made["id"], "disabled")
    pa.admin_password(c, made["id"], GOOD_PW + "x1")
    pa.admin_users_action(c, 2, "disable")
    pa.admin_delete(c, made["id"], "keep")
    key = pa.key_create(c, {"name": "nk-test-key"})
    pa.key_revoke(c, key["key"]["id"])
    pa.key_update(c, key["key"]["id"], {"note": "x"})
    pa.key_delete(c, key["key"]["id"])
    pa.settings_update(c, "telegram", {"token": new_token, "for_admins_only": False})
    pa.settings_restore(c, pa.settings_backups(c["id"])[0]["name"])

    with session() as db:
        rows = list(db.scalars(select(Event).where(Event.domain == "panels").order_by(Event.id)))

    texts = [e.text for e in rows]
    assert len(texts) == 12 and all(t.startswith("PasarGuard pg-test: ") for t in texts) and {e.ref for e in rows} == {"pg-test"}
    joined = " | ".join(texts)

    for part in ("admin nk-test-admin created (role operator)", "admin nk-test-admin changed (discord_webhook, note)", "admin nk-test-admin switched off", "password of admin nk-test-admin changed",
                 "all the active users of admin alice switched off (5 users in all)", "admin nk-test-admin deleted (its 0 users: keep)", "API key nk-test-key created for admin moderator",
                 "API key nk-test-key renewed", "API key nk-test-key changed (note)", "API key nk-test-key deleted", "settings Telegram saved (2 changes: Bot token, Admins only)", "settings Telegram restored from panel"):
        assert part in joined, part

    assert not [s for s in (GOOD_PW, DISCORD, new_token, key["secret"], "F" * 35) if s in joined]
    assert {e.level for e in rows} == {"info", "warn"}


def test_a_dry_run_lists_the_changes_and_changes_nothing(fake):
    before = copy.deepcopy(fake.settings)
    out = pa.settings_update(C, "hwid", {"forced": True, "max_limit": 3}, dry_run=True)
    assert out["written"] is False and {c["path"] for c in out["changes"]} == {"forced", "max_limit"} and out["backup"] == ""
    assert not fake.puts() and fake.settings == before and backup_files() == []
    assert pa.settings_update(C, "hwid", {}, dry_run=True)["changes"] == []

    with pytest.raises(ValueError, match="nothing to change"):
        pa.settings_update(C, "hwid", {"forced": False})

    assert not fake.puts() and backup_files() == []


def test_a_bad_section_is_refused_before_anything_is_saved_or_written(fake):
    for section, values, clear, msg in (("telegram", {"token": "x"}, [], "Bot token"), ("telegram", {}, ["token"], "token"), ("telegram", {"method": "pigeon"}, [], "pick one"),
                                        ("hwid", {"min_limit": 9, "max_limit": 2}, [], "Fewest"), ("webhook", {"webhooks": []}, [], "receiver"), ("nope", {}, [], "unknown section"),
                                        ("general", {"custom_variables": [{"key": "USERNAME", "value": ""}]}, [], "built-in")):
        with pytest.raises(ValueError, match=msg):
            pa.settings_update(C, section, values, clear)

    assert not fake.puts() and backup_files() == []


def test_the_webhook_receivers_keep_their_secrets(fake):
    a = LIVE["webhook"]["webhooks"][0]
    out = pa.settings_update(C, "webhook", {"webhooks": [{"url": a["url"], "_id": a["url"]}, {"url": "https://hook-c.example.test/z", "secret": "hook-c-secret"}], "timeout": 60})
    assert [r["url"] for r in fake.settings["webhook"]["webhooks"]] == [a["url"], "https://hook-c.example.test/z"] and fake.settings["webhook"]["webhooks"][0]["secret"] == HOOK_A
    assert "hook-c-secret" not in json.dumps(out) and fake.settings["webhook"]["timeout"] == 60 and out["warnings"] == []

    with pytest.raises(ValueError, match="Secret is needed"):
        pa.settings_update(C, "webhook", {"webhooks": [{"url": "https://hook-d.example.test/q"}]})


def test_the_variables_of_the_template_are_written_through_general(fake):
    out = pa.settings_update(C, "general", {"default_method": "aes-256-gcm", "custom_variables": [{"key": "brand", "value": "New"}, {"key": "SUPPORT", "value": "x"}]})
    assert fake.puts()[0] == {"general": {"default_method": "aes-256-gcm", "custom_variables": [{"key": "BRAND", "value": "New"}, {"key": "SUPPORT", "value": "x"}]}}
    assert fake.settings["subscription"]["custom_variables"][1] == {"key": "SUPPORT", "value": "x"} and out["warnings"] == []
    assert out["values"]["custom_variables"][0] == {"key": "BRAND", "value": "New"}


def test_editing_the_subscription_leaves_its_variables_alone(fake):
    pa.settings_update(C, "subscription", {"update_interval": 6})
    sent = fake.puts()[0]["subscription"]
    assert sent["custom_variables"] == [{"key": "BRAND", "value": "Example"}] and sent["update_interval"] == 6 and sent["rules"][0]["target"] == "clash"
    assert fake.settings["subscription"]["custom_variables"] == [{"key": "BRAND", "value": "Example"}]


def test_a_panel_that_does_not_keep_a_value_is_told_to_the_operator(fake):
    fake.ignore.add("hwid.max_limit")
    out = pa.settings_update(C, "hwid", {"max_limit": 4, "forced": True})
    assert out["warnings"] == ["the panel did not keep: Most devices"] and fake.settings["hwid"]["forced"] is True
    fake.ignore.add("telegram.token")
    out = pa.settings_update(C, "telegram", {"token": "987654321:" + "C" * 35})
    assert out["warnings"] == ["the panel did not keep: Bot token"]


def test_every_write_saves_the_previous_settings_without_secrets_and_keeps_the_last_20(fake):
    for i in range(23):
        pa.settings_update(C, "hwid", {"fallback_limit": i + 1})

    files = [p for p in backup_files() if "-settings-" in p.name]
    assert len(files) == 20
    listed = pa.settings_backups(901)
    assert len(listed) == 20 and listed[0]["section"] == "hwid" and listed[0]["at"] >= listed[-1]["at"]
    newest = json.loads((get_settings().data_dir / "pg-backups" / listed[0]["name"]).read_text())
    assert newest["kind"] == "settings" and newest["panel_id"] == 901 and newest["section"] == "hwid" and newest["settings"]["hwid"]["fallback_limit"] == 22        # the state BEFORE that write
    assert set(newest["settings"]) == {s["key"] for s in pa.SECTIONS}

    for p in files:
        no_secret(p.read_text())
        assert "token" not in json.dumps(json.loads(p.read_text())["settings"]["telegram"])

    other = get_settings().data_dir / "pg-backups" / "panel901-groups-20260101-000000.json"                              # a file of another tab is never ours to delete
    other.write_text("{}")

    try:
        for _ in range(3):
            pa.settings_update(C, "hwid", {"fallback_limit": 99 - _})

        assert other.exists()
    finally:
        other.unlink()

    assert pa.settings_backups(902) == []


def test_restore_puts_the_previous_values_back_and_keeps_the_secrets_of_now(fake):
    pa.settings_update(C, "telegram", {"for_admins_only": False, "mini_app_web_url": "https://mini.example.test"})
    first = pa.settings_backups(901)[0]["name"]
    fake.settings["telegram"]["token"] = "555555555:" + "Z" * 35                       # someone changed the token on the panel meanwhile
    out = pa.settings_restore(C, first)
    assert out["restored_from"] == first and out["section"] == "telegram" and {c["path"] for c in out["changes"]} == {"for_admins_only", "mini_app_web_url"} and out["skipped"] == []
    assert fake.settings["telegram"]["for_admins_only"] is True and fake.settings["telegram"]["mini_app_web_url"] == "" and fake.settings["telegram"]["token"] == "555555555:" + "Z" * 35
    sent = fake.puts()[-1]["telegram"]
    assert sent["token"] == "555555555:" + "Z" * 35 and sent["webhook_secret"] == TG_HOOK_SECRET                  # secrets of now, read again, re-sent
    assert len(pa.settings_backups(901)) == 2 and pa.settings_backups(901)[0]["name"] != first                      # the state before the restore was saved too

    with pytest.raises(ValueError, match="already has these values"):
        pa.settings_restore(C, first)

    pa.settings_restore(C, pa.settings_backups(901)[0]["name"])                                                       # undo the restore
    assert fake.settings["telegram"]["for_admins_only"] is False


def test_a_restore_that_would_leave_an_invalid_section_says_what_to_do(fake):
    pa.settings_update(C, "telegram", {"enable": False}, clear=["token"])                  # the backup of this holds the bot on, with its token
    name = pa.settings_backups(901)[0]["name"]

    with pytest.raises(ValueError, match="set the secret that is missing first") as e:
        pa.settings_restore(C, name)

    assert "token" in str(e.value) and len(fake.puts()) == 1                                 # nothing was sent


def test_restore_of_webhook_receivers_skips_a_row_whose_secret_is_gone(fake):
    a = LIVE["webhook"]["webhooks"][0]
    pa.settings_update(C, "webhook", {"webhooks": [{"url": a["url"], "_id": a["url"]}], "timeout": 30})                  # b is removed
    name = pa.settings_backups(901)[0]["name"]
    out = pa.settings_restore(C, name)
    assert [r["url"] for r in fake.settings["webhook"]["webhooks"]] == [a["url"]] and fake.settings["webhook"]["timeout"] == 180
    assert out["skipped"] and "hook-b.example.test" in out["skipped"][0] and "Secret" in out["skipped"][0] and HOOK_B not in json.dumps(out)


def test_restore_refuses_names_that_are_not_backups_of_this_panel(fake):
    pa.settings_update(C, "hwid", {"forced": True})
    ok = pa.settings_backups(901)[0]["name"]
    outside = get_settings().data_dir / "outside-901-settings-20260101-000000.json"
    outside.write_text("{}")

    for bad in ("../outside-901-settings-20260101-000000.json", "panel902-settings-20260101-000000.json", "panel901-admin-bob-20260101-000000.json", "", "x", ok + "/..", "panel901-settings-1.json"):
        with pytest.raises(ValueError, match="not a backup"):
            pa.settings_restore(C, bad)

    with pytest.raises(ValueError, match="does not exist"):
        pa.settings_restore(C, "panel901-settings-20200101-000000.json")

    with pytest.raises(ValueError, match="no such section"):
        pa.settings_restore(C, ok, "nope")

    outside.unlink()


def test_a_section_the_panel_hides_secrets_of_is_not_written_back(fake):
    fake.settings["telegram"]["token"] = "********"

    with pytest.raises(ValueError, match="Bot token"):
        pa.settings_update(C, "telegram", {"for_admins_only": False})

    assert not fake.puts()
    assert pa.settings_update(C, "telegram", {"token": "987654321:" + "D" * 35, "for_admins_only": False})["written"] is True                     # typing a new one is fine


def test_a_refusal_of_the_panel_is_shown_clearly_and_the_backup_exists(fake):
    fake.fail[("PUT", "/api/settings")] = pg_422("telegram", "Telegram bot cannot be enabled without token", TG_TOKEN)

    with pytest.raises(PanelError) as e:
        pa.settings_update(C, "telegram", {"for_admins_only": False})

    assert str(e.value) == "HTTP 422: telegram: Telegram bot cannot be enabled without token" and TG_TOKEN not in str(e.value)
    assert len(pa.settings_backups(901)) == 1                                              # saved before the write was tried


def test_a_panel_that_is_down(fake):
    fake.fail[("GET", "/api/settings")] = PanelError("no answer from the panel (ConnectError)")

    for fn in (lambda: pa.settings_get(C), lambda: pa.settings_update(C, "hwid", {"forced": True}), lambda: pa.settings_restore(C, "panel901-settings-20260101-000000.json")):
        with pytest.raises((PanelError, ValueError)):
            fn()

    fake.fail.clear()
    fake.fail[("GET", "/api/admins")] = PanelError("no answer from the panel (ConnectError)")

    with pytest.raises(PanelError, match="no answer"):
        pa.admins_list(C)


# ------------------------------------------------------------------ the routes
@pytest.fixture
def panel_row():
    with session() as db:
        p = Panel(name="pg-test", type="pasarguard", url="https://pg.example.test", username="moderator", secrets={"password": "x"}, status="up")
        m = Panel(name="mz-test", type="marzban", url="https://mz.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add_all([p, m])
        db.flush()
        return p.id, m.id


def test_the_admin_routes(client, fake, panel_row):
    pid, mid = panel_row
    base = f"/api/panels/{pid}/pg"
    j = client.get(base + "/admins").json()
    assert j["ok"] is True and [a["username"] for a in j["admins"]] == ["alice", "bob", "moderator"] and j["me"]["username"] == "moderator"
    r = client.post(base + "/admins", json={"username": "nk-test-admin", "password": GOOD_PW, "role_id": 3, "data_limit_gb": 5})
    assert r.status_code == 200 and r.json()["admin"]["limit"] == 5 * GB and GOOD_PW not in r.text
    aid = r.json()["admin"]["id"]
    assert client.put(base + f"/admins/{aid}", json={"note": "api"}).json()["admin"]["note"] == "api"
    assert client.put(base + f"/admins/{aid}", json={"overrides": {"max_users": 3}}).json()["admin"]["overrides"]["max_users"] == 3
    assert client.post(base + f"/admins/{aid}/status", json={"status": "disabled"}).json()["admin"]["status"] == "disabled"
    assert client.post(base + f"/admins/{aid}/password", json={"password": GOOD_PW + "x1"}).json() == {"ok": True}
    assert client.post(base + f"/admins/{aid}/reset-usage").json()["admin"]["used"] == 0
    assert client.post(base + f"/admins/{aid}/users/disable").json()["action"] == "disable" and client.post(base + f"/admins/{aid}/users/activate").json()["action"] == "activate"
    fake.usage[aid] = [{"period_start": "2026-10-14T00:00:00+03:30", "total_traffic": 9}]
    u = client.get(base + f"/admins/{aid}/usage", params={"from": "2026-10-14", "to": "2026-10-14"}).json()
    assert u["total"] == 9 and u["days"] == [{"date": "2026-10-14", "bytes": 9}]
    assert client.post(base + f"/admins/{aid}/users/delete", json={"confirm": "wrong"}).status_code == 400
    assert client.delete(base + f"/admins/{aid}", params={"users": "burn"}).status_code == 422
    d = client.delete(base + f"/admins/{aid}", params={"users": "keep"}).json()
    assert d["deleted"] == "nk-test-admin" and aid not in fake.admins
    # refusals
    assert client.post(base + "/admins", json={"username": "nk-test-x", "password": "weak", "role_id": 3}).status_code == 400
    assert client.post(base + "/admins", json={"username": "ab", "password": GOOD_PW, "role_id": 3}).status_code == 422
    assert client.post(base + "/admins", json={"username": "nk-test-x", "password": GOOD_PW, "role_id": 3, "data_limit_gb": -1}).status_code == 422
    assert client.post(base + "/admins/1/status", json={"status": "disabled"}).status_code == 400
    assert client.delete(base + "/admins/1").status_code == 400
    assert client.get(base + "/admins/2/usage", params={"from": "2026-10-15", "to": "2026-10-14"}).status_code == 400
    assert client.get(base + "/admins/2/usage").status_code == 422
    assert client.post(base + "/admins", json={"username": "alice", "password": GOOD_PW, "role_id": 3}).status_code == 502
    assert client.get(f"/api/panels/{mid}/pg/admins").status_code == 400                      # a Marzban panel has no such tab
    assert client.get("/api/panels/9999/pg/admins").status_code == 400
    fake.fail[("GET", "/api/admins")] = PanelError("no answer from the panel (ConnectError)")
    r = client.get(base + "/admins")
    assert r.status_code == 502 and "no answer" in r.json()["error"]


def test_the_key_routes(client, fake, panel_row):
    pid, _mid = panel_row
    base = f"/api/panels/{pid}/pg/admin/api-keys"
    r = client.post(base, json={"name": "nk-test-key", "inherit_permissions": False, "permissions": {"users": {"read": "all"}}, "expire": "2099-01-01"})
    assert r.status_code == 200 and r.json()["secret"].startswith("pg_key_") and r.json()["key"]["granted"] == 1
    secret = r.json()["secret"]
    j = client.get(base).json()
    assert j["total"] == 1 and secret not in json.dumps(j) and j["keys"][0]["expire_day"] == "2099-01-01"
    assert client.put(base + "/1", json={"status": "disabled", "note": "n"}).json()["key"]["status"] == "disabled"
    assert client.put(base + "/1", json={}).status_code == 400
    assert client.put(base + "/1", json={"status": "gone"}).status_code == 422
    new = client.post(base + "/1/revoke").json()
    assert new["secret"] != secret and new["key"]["status"] == "active"
    assert client.post(base, json={"name": "nk-test-key"}).status_code == 502
    assert client.post(base, json={"name": ""}).status_code == 422
    assert client.post(base, json={"name": "k", "inherit_permissions": False, "permissions": {}}).status_code == 400
    client.post(base, json={"name": "second"})
    assert client.post(base + "/bulk-delete", json={"ids": []}).status_code == 422
    assert client.post(base + "/bulk-delete", json={"ids": [1, 9]}).status_code == 400
    assert client.post(base + "/bulk-delete", json={"ids": [1]}).json()["count"] == 1
    assert client.delete(base + "/2").json()["deleted"] == "second" and client.delete(base + "/2").status_code == 502
    assert not fake.keys


def test_the_settings_routes(client, fake, panel_row):
    pid, _mid = panel_row
    base = f"/api/panels/{pid}/pg/settings"
    r = client.get(base)
    assert r.status_code == 200 and not [s for s in SECRETS if s in r.text] and len(r.json()["sections"]) == 7
    d = client.put(base + "/hwid", json={"values": {"forced": True}, "dry_run": True}).json()
    assert d["written"] is False and [c["path"] for c in d["changes"]] == ["forced"] and not fake.puts()
    w = client.put(base + "/hwid", json={"values": {"forced": True, "max_limit": 5}}).json()
    assert w["written"] is True and w["backup"].startswith(f"panel{pid}-settings-") and fake.settings["hwid"]["forced"] is True
    t = client.put(base + "/telegram", json={"values": {"token": "987654321:" + "E" * 35}, "clear": ["proxy_url"]})
    assert t.status_code == 200 and "E" * 35 not in t.text and fake.settings["telegram"].get("proxy_url") is None
    b = client.get(base + "/backups").json()["backups"]
    assert [x["section"] for x in b] == ["telegram", "hwid"]
    back = client.post(base + "/restore", json={"name": b[1]["name"]}).json()
    assert back["restored_from"] == b[1]["name"] and fake.settings["hwid"]["forced"] is False
    assert client.put(base + "/nope", json={"values": {}}).status_code == 400
    assert client.put(base + "/hwid", json={"values": {"forced": "yes"}}).status_code == 400
    assert client.put(base + "/hwid", json={"values": [], "dry_run": True}).status_code == 422
    assert client.post(base + "/restore", json={"name": "../x"}).status_code == 400
    assert client.post(base + "/restore", json={"name": ""}).status_code == 422
    fake.fail[("PUT", "/api/settings")] = pg_422("hwid", "bad", "v")
    assert client.put(base + "/hwid", json={"values": {"forced": True}}).status_code == 502


def test_every_route_of_this_tab_has_a_one_sentence_description(client):
    spec = client.app.openapi()
    mine = {(m, p): op for p, ops in spec["paths"].items() if "/pg/admin" in p or "/pg/settings" in p for m, op in ops.items()}
    assert len(mine) == 21

    for key, op in mine.items():
        desc = (op.get("description") or "").strip()
        assert desc and len(desc.split("\n")[0]) <= 300 and "\n" not in desc, key
