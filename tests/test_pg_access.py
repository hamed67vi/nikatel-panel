"""PasarGuard groups and user templates: the pure helpers, the services against a fake panel that keeps the panel's own defaults and refusals, the backups and the restore, the API."""
import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Panel
from nikatel.services import panels
from nikatel.services import pg_access as pa

C = {"id": 2, "type": "pasarguard"}
GB = 1024 ** 3


def err(code, msg):
    return panels.PanelError(f"HTTP {code}: {msg}", code)


class Fake:
    """A PasarGuard 5.4 in memory: the same routes, the same refusals and - the point - the same DEFAULTS: a group PUT without is_disabled switches it ON, a template PUT without
    data_limit_reset_strategy resets it. Users 1-30 hold the group 'main', users 31-40 hold nothing."""

    def __init__(self):
        self.inbounds = ["VLESS WS 80", "TROJAN TCP TLS 2080", "VLESS TCP 1616", "Shadowsocks TCP"]
        self.groups = {1: {"id": 1, "name": "main", "inbound_tags": ["VLESS WS 80", "TROJAN TCP TLS 2080"], "is_disabled": False}}
        self.members = {1: set(range(1, 31))}
        self.templates = {}
        self.universe = set(range(1, 41))
        self.users = {}
        self.log = []
        self.refuse = None                          # (code, text): the next write is refused like that
        self.details = True                         # /api/inbounds/details exists (v5.4); False = an older panel
        self.templates_readable = True
        self.next_g, self.next_t = 2, 1

    def writes(self):
        return [x for x in self.log if x[0] != "GET"]

    def add_template(self, **kw):
        t = {"id": self.next_t, "name": "t", "data_limit": 0, "hwid_limit": None, "expire_duration": 0, "username_prefix": None, "username_suffix": None, "group_ids": [1],
             "extra_settings": None, "status": "active", "reset_usages": False, "on_hold_timeout": None, "data_limit_reset_strategy": "no_reset", "is_disabled": False, **kw}
        self.templates[t["id"]] = t
        self.next_t += 1
        return t

    def group_out(self, g):
        return {**copy.deepcopy(g), "total_users": len(self.members.get(g["id"], ()))}

    def scope(self, b):
        users = set(b.get("users") or [])
        other = None

        if b.get("has_group_ids"):
            other = set().union(*(self.members.get(i, set()) for i in b["has_group_ids"]))

        if b.get("has_no_group"):
            nog = self.universe - set().union(*self.members.values())
            other = nog if other is None else other & nog

        if users and other is not None:
            return users | other

        return users or (other if other is not None else set(self.universe))

    def call(self, c, method, path, **kw):
        body = copy.deepcopy(kw.get("body"))
        self.log.append((method, path, body))

        if method != "GET" and self.refuse:
            code, msg = self.refuse
            self.refuse = None
            raise err(code, msg)

        if method == "GET":
            return self.get(path)

        if path == "/api/group" and method == "POST":
            if not 3 <= len(body["name"]) <= 64:
                raise err(422, "name: String should have at least 3 characters")

            for t in body["inbound_tags"]:
                if t not in self.inbounds:
                    raise err(400, f"{t} not found")

            g = {"id": self.next_g, "name": body["name"], "inbound_tags": body["inbound_tags"], "is_disabled": body.get("is_disabled", False)}
            self.groups[g["id"]] = g
            self.next_g += 1
            return self.group_out(g)

        m = re.fullmatch(r"/api/group/(\d+)", path)

        if m:
            gid = int(m.group(1))

            if gid not in self.groups:
                raise err(404, "Group not found")

            if method == "PUT":
                g = self.groups[gid]

                for t in body.get("inbound_tags") or []:
                    if t not in self.inbounds:
                        raise err(400, f"{t} not found")

                if body.get("inbound_tags") is not None:
                    g["inbound_tags"] = body["inbound_tags"]

                g["name"] = body["name"]
                g["is_disabled"] = body.get("is_disabled", False)             # the trap: missing means False
                return self.group_out(g)

            if method == "DELETE":
                del self.groups[gid]
                self.members.pop(gid, None)

                for t in self.templates.values():
                    t["group_ids"] = [i for i in t["group_ids"] if i != gid]

                return None

        m = re.fullmatch(r"/api/groups/bulk/(enable|disable|delete)", path)

        if m and method == "POST":
            for i in body["ids"]:
                if i not in self.groups:
                    raise err(404, "Group not found")

            names = [self.groups[i]["name"] for i in body["ids"]]

            for i in body["ids"]:
                if m.group(1) == "delete":
                    del self.groups[i]
                    self.members.pop(i, None)
                else:
                    self.groups[i]["is_disabled"] = m.group(1) == "disable"

            return {"groups": names, "count": len(names)}

        m = re.fullmatch(r"/api/groups/bulk/(add|remove)", path)

        if m and method == "POST":
            who = self.scope(body)

            if body["dry_run"]:
                return {"dry_run": True, "affected_users": len(who)}

            for gid in body["group_ids"]:
                cur = self.members.setdefault(gid, set())
                self.members[gid] = cur | who if m.group(1) == "add" else cur - who

            return {"detail": f"operation has been successfuly done on {len(who)} users"}

        if path == "/api/user_template" and method == "POST":
            if any(t["name"] == body["name"] for t in self.templates.values()):
                raise err(409, "Template by this name already exists")

            for gid in body["group_ids"]:
                if gid not in self.groups:
                    raise err(404, "Group not found")

            return copy.deepcopy(self.add_template(**{k: v for k, v in body.items() if v is not None or k in ("hwid_limit",)}))

        m = re.fullmatch(r"/api/user_template/(\d+)", path)

        if m:
            tid = int(m.group(1))

            if tid not in self.templates:
                raise err(404, "User Template not found")

            t = self.templates[tid]

            if method == "PUT":
                for k in ("name", "data_limit", "expire_duration", "username_prefix", "username_suffix", "extra_settings", "status", "reset_usages", "on_hold_timeout", "is_disabled"):
                    if body.get(k) is not None:
                        t[k] = body[k]

                if "hwid_limit" in body:
                    t["hwid_limit"] = body["hwid_limit"]

                if body.get("group_ids"):
                    t["group_ids"] = body["group_ids"]

                t["data_limit_reset_strategy"] = body.get("data_limit_reset_strategy") or "no_reset"          # the trap: missing means no_reset
                return copy.deepcopy(t)

            if method == "DELETE":
                del self.templates[tid]
                return None

        m = re.fullmatch(r"/api/user_templates/bulk/(enable|disable|delete)", path)

        if m and method == "POST":
            names = [self.templates[i]["name"] for i in body["ids"] if i in self.templates]

            for i in body["ids"]:
                if m.group(1) == "delete":
                    self.templates.pop(i, None)
                else:
                    self.templates[i]["is_disabled"] = m.group(1) == "disable"

            return {"templates": names, "count": len(names)}

        if path == "/api/user/from_template" and method == "POST":
            t = self.templates.get(body["user_template_id"])

            if t is None:
                raise err(404, "User Template not found")

            if t["is_disabled"]:
                raise err(403, "this template is disabled")

            name = (t["username_prefix"] or "") + body["username"] + (t["username_suffix"] or "")

            if name in self.users:
                raise err(409, "User already exists")

            self.users[name] = body
            return {"username": name, "status": "active", "used_traffic": 0, "data_limit": t["data_limit"], "expire": None, "subscription_url": f"https://sub.example.test/sub/{name}", "note": body.get("note") or ""}

        raise AssertionError(f"unexpected call {method} {path}")

    def get(self, path):
        if path == "/api/groups":
            rows = [self.group_out(g) for g in self.groups.values()]
            return {"groups": rows, "total": len(rows)}

        if path == "/api/inbounds/details":
            if not self.details:
                raise err(404, "Not Found")

            return [{"tag": t, "protocol": t.split()[0].lower(), "network": "ws" if " WS " in t else "tcp"} for t in self.inbounds]

        if path == "/api/inbounds":
            return list(self.inbounds)

        if path == "/api/user_templates":
            if not self.templates_readable:
                raise err(403, "You are not allowed to read templates")

            return [copy.deepcopy(t) for t in self.templates.values()]

        m = re.fullmatch(r"/api/group/(\d+)", path)

        if m:
            if int(m.group(1)) not in self.groups:
                raise err(404, "Group not found")

            return self.group_out(self.groups[int(m.group(1))])

        m = re.fullmatch(r"/api/user_template/(\d+)", path)

        if m:
            if int(m.group(1)) not in self.templates:
                raise err(404, "User Template not found")

            return copy.deepcopy(self.templates[int(m.group(1))])

        raise AssertionError(f"unexpected read {path}")


@pytest.fixture
def pg(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f.call)
    return f


@pytest.fixture(autouse=True)
def no_old_backups():
    """The data folder is shared by the tests of a run: every test starts with no backup files."""
    d = get_settings().data_dir / "pg-backups"

    if d.is_dir():
        for p in d.glob("*.json"):
            p.unlink()


def backup_doc(name):
    return json.loads((get_settings().data_dir / "pg-backups" / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ pure
def test_units_are_converted_and_an_untouched_value_keeps_its_exact_bytes():
    assert pa.gb_to_bytes(1.5) == 1610612736 and pa.gb_to_bytes("") == 0 and pa.gb_to_bytes(None) == 0 and pa.gb_to_bytes(0) == 0 and pa.gb_to_bytes("2") == 2 * GB
    assert pa.gb_to_bytes(0.1) == round(0.1 * GB) and pa.gb_to_bytes(0.1, keep=107374181) == 107374181          # shown as 0.1 GB, typed again as 0.1: the bytes do not move
    assert pa.gb_to_bytes(0.2, keep=107374181) == round(0.2 * GB)                                              # a real change is a change
    assert pa.days_to_seconds(30) == 2592000 and pa.days_to_seconds(0.5) == 43200 and pa.days_to_seconds("") == 0 and pa.days_to_seconds(30, keep=2592001) == 2592001

    for bad in (-1, "abc", float("nan"), 2_000_000):
        with pytest.raises(ValueError):
            pa.gb_to_bytes(bad)

    for bad in (-1, "x", 30000):
        with pytest.raises(ValueError):
            pa.days_to_seconds(bad)

    cur = pa.template_row({"data_limit": 107374181, "expire_duration": 2592001, "on_hold_timeout": 86400})
    ch = pa.to_panel_units({"data_limit_gb": 0.1, "expire_days": 30, "on_hold_days": 2, "name": "x", "data_limit_reset_strategy": "month"}, cur)
    assert ch == {"data_limit": 107374181, "expire_duration": 2592001, "on_hold_timeout": 172800, "name": "x", "data_limit_reset_strategy": "month"}
    assert pa.to_panel_units({"data_limit": 5, "expire_duration": 6}, None) == {"data_limit": 5, "expire_duration": 6}           # the panel's own units pass through


def test_input_checks():
    assert pa.name_ok("  main  ", "group", 3) == "main"

    for bad in ("ab", "", None, "x" * 65, "bad\nname"):
        with pytest.raises(ValueError):
            pa.name_ok(bad, "group", 3)

    assert pa.tags_ok(["a", " b ", "a", ""]) == ["a", "b"]

    for bad in ([], [""], "a", None, ["x" * 201], [f"t{i}" for i in range(301)]):
        with pytest.raises(ValueError):
            pa.tags_ok(bad)

    assert pa.ids_ok([3, "1", 3], "group") == [3, 1] and pa.ids_ok(None, "group", required=False) == []

    for bad in ([], [0], [-1], ["x"], "5", [1] * 2 + list(range(2, 600))):
        with pytest.raises(ValueError):
            pa.ids_ok(bad, "group")

    assert pa.affix_ok(" vip_ ", "prefix") == "vip_" and pa.affix_ok(None, "prefix") == "" and pa.affix_ok("a.b-c@d", "suffix") == "a.b-c@d"

    for bad in ("a" * 21, "a b", "a--b", "a_.b", "é"):
        with pytest.raises(ValueError):
            pa.affix_ok(bad, "prefix")

    assert pa.username_ok(" user_1 ") == "user_1"

    for bad in ("ab", "a" * 129, "a b", "a..b", ""):
        with pytest.raises(ValueError):
            pa.username_ok(bad)

    with pytest.raises(ValueError):
        pa.need_pasarguard({"type": "marzban"})


def test_rows_are_tolerant_of_a_missing_or_odd_shape():
    assert pa.group_row({}) == {"id": None, "name": "", "inbound_tags": [], "is_disabled": False, "users": None}
    assert pa.group_row({"id": "7", "name": "g", "inbound_tags": ["a", 5], "is_disabled": 1, "total_users": "12"}) == {"id": 7, "name": "g", "inbound_tags": ["a", "5"], "is_disabled": True, "users": 12}
    assert pa.group_row({"inbound_tags": "oops"})["inbound_tags"] == []
    t = pa.template_row({})
    assert (t["id"], t["name"], t["group_ids"], t["data_limit_gb"], t["expire_days"], t["status"], t["data_limit_reset_strategy"], t["hwid_limit"], t["method"], t["is_disabled"]) == (None, "", [], 0.0, 0.0, "active", "no_reset", None, "", False)
    t = pa.template_row({"id": 3, "name": "n", "group_ids": [2, "1", None, 2], "data_limit": 3 * GB, "expire_duration": 86400 * 7, "extra_settings": {"method": "aes-256-gcm"}, "status": "on_hold", "on_hold_timeout": 86400, "hwid_limit": 2})
    assert (t["group_ids"], t["data_limit_gb"], t["expire_days"], t["method"], t["on_hold_days"], t["hwid_limit"]) == ([1, 2], 3.0, 7.0, "aes-256-gcm", 1.0, 2)
    assert pa.template_row({"extra_settings": "junk", "group_ids": "junk"})["group_ids"] == []
    assert pa.as_rows({"groups": [{"a": 1}, 5, None]}, "groups") == [{"a": 1}] and pa.as_rows([{"a": 1}], "groups") == [{"a": 1}] and pa.as_rows("x", "groups") == [] and pa.as_rows(None, "groups") == []
    assert pa._inbounds([{"tag": "b", "protocol": "vless", "network": "ws"}, "a", {"tag": "b"}, 7]) == [{"tag": "a", "protocol": "", "network": ""}, {"tag": "b", "protocol": "vless", "network": "ws"}]
    assert pa._inbounds({"vless": [{"tag": "x", "port": 1}], "trojan": "junk"}) == [{"tag": "x", "protocol": "vless", "network": ""}] and pa._inbounds("junk") == []


def test_template_body_is_always_complete():
    new = pa.template_body(None, {"name": "plan", "group_ids": [2, 1], "data_limit": 5 * GB, "expire_duration": 30 * 86400, "username_prefix": "vip", "data_limit_reset_strategy": "month", "method": "aes-128-gcm"})
    assert new == {"name": "plan", "group_ids": [1, 2], "data_limit": 5 * GB, "expire_duration": 2592000, "username_prefix": "vip", "username_suffix": None, "status": "active",
                   "data_limit_reset_strategy": "month", "reset_usages": False, "on_hold_timeout": None, "hwid_limit": None, "is_disabled": False, "extra_settings": {"method": "aes-128-gcm"}}
    cur = pa.template_row({"id": 1, "name": "plan", "group_ids": [1], "data_limit": GB, "expire_duration": 86400, "username_prefix": "vip", "data_limit_reset_strategy": "week", "hwid_limit": 3, "extra_settings": {"method": "aes-256-gcm"}})
    body = pa.template_body(cur, {"name": "plan2"})
    assert body["name"] == "plan2" and body["data_limit_reset_strategy"] == "week" and body["hwid_limit"] == 3 and body["username_prefix"] == "vip" and body["extra_settings"] == {"method": "aes-256-gcm"}
    assert body["username_suffix"] == "" and body["on_hold_timeout"] == 0                                              # an existing template: '' and 0 clear, null would only mean 'keep'
    assert pa.template_body(cur, {"hwid_limit": None})["hwid_limit"] is None and pa.template_body(cur, {"username_prefix": ""})["username_prefix"] == ""
    assert pa.template_body(cur, {"method": ""})["extra_settings"] == {"method": pa.DEFAULT_METHOD}                        # 'default' on a template that has a method

    for ch in ({"name": ""}, {"name": "x" * 65}, {"group_ids": []}, {"data_limit": -1}, {"expire_duration": -5}, {"status": "disabled"}, {"status": "on_hold", "expire_duration": 0},
               {"data_limit_reset_strategy": "hourly"}, {"method": "rot13"}, {"hwid_limit": "x"}, {"username_prefix": "a--b"}, {"on_hold_timeout": -1}):
        with pytest.raises(ValueError):
            pa.template_body(cur, ch)

    assert pa.template_body(None, {"name": "h", "group_ids": [1], "status": "on_hold", "expire_duration": 86400, "on_hold_timeout": 3 * 86400})["on_hold_timeout"] == 259200


# ------------------------------------------------------------------ groups
def test_the_groups_with_their_use_and_the_inbounds_to_choose_from(pg):
    pg.add_template(name="plan", group_ids=[1])
    out = pa.groups(C)
    assert out["groups"] == [{"id": 1, "name": "main", "inbound_tags": ["VLESS WS 80", "TROJAN TCP TLS 2080"], "is_disabled": False, "users": 30, "templates": ["plan"]}] and out["warn"] == ""
    assert [i["tag"] for i in out["inbounds"]] == ["Shadowsocks TCP", "TROJAN TCP TLS 2080", "VLESS TCP 1616", "VLESS WS 80"]
    assert {"tag": "VLESS WS 80", "protocol": "vless", "network": "ws"} in out["inbounds"]
    assert pg.writes() == []
    pg.details = False                                                                         # an older panel: only the plain list of tags
    assert pa.groups(C)["inbounds"][0] == {"tag": "Shadowsocks TCP", "protocol": "", "network": ""}
    pg.templates_readable = False                                                              # an admin who may not read templates still gets the groups
    out = pa.groups(C)
    assert out["groups"][0]["templates"] == [] and "templates could not be read" in out["warn"]
    assert pa.group_get(C, 1)["users"] == 30

    with pytest.raises(panels.PanelError, match="404"):
        pa.group_get(C, 99)

    with pytest.raises(ValueError, match="PasarGuard"):
        pa.groups({"id": 1, "type": "marzban"})


def test_create_a_group(pg):
    g = pa.group_create(C, "  nk-extra ", ["VLESS TCP 1616", "VLESS TCP 1616", "Shadowsocks TCP"], True)
    assert g == {"id": 2, "name": "nk-extra", "inbound_tags": ["VLESS TCP 1616", "Shadowsocks TCP"], "is_disabled": True, "users": 0}
    assert pg.writes() == [("POST", "/api/group", {"name": "nk-extra", "inbound_tags": ["VLESS TCP 1616", "Shadowsocks TCP"], "is_disabled": True})]

    for name, tags, why in (("ab", ["VLESS WS 80"], "needs 3"), ("MAIN", ["VLESS WS 80"], "exists already"), ("fine", [], "at least one"), ("fine", ["No Such Tag"], "No Such Tag not found")):
        with pytest.raises((ValueError, panels.PanelError), match=why):
            pa.group_create(C, name, tags)

    assert len(pg.groups) == 2                                                                  # none of the refused ones got in


def test_a_group_edit_sends_all_three_fields_so_the_panel_cannot_fall_back_to_its_defaults(pg):
    pg.groups[1]["is_disabled"] = True
    pg.call(C, "PUT", "/api/group/1", body={"name": "main", "inbound_tags": None})                  # what a half body does at the panel: it switches the group ON
    assert pg.groups[1]["is_disabled"] is False
    pg.groups[1]["is_disabled"] = True
    pg.log.clear()
    out = pa.group_update(C, 1, {"name": "renamed"})
    assert out["changed"] is True and out["group"]["name"] == "renamed" and pg.groups[1]["is_disabled"] is True                  # still off: the body said so
    assert pg.writes() == [("PUT", "/api/group/1", {"name": "renamed", "inbound_tags": ["VLESS WS 80", "TROJAN TCP TLS 2080"], "is_disabled": True})]
    assert out["before"]["name"] == "main" and out["added"] == [] and out["removed"] == []
    doc = backup_doc(out["backup"])
    assert doc["kind"] == "pg-access" and doc["panel"] == 2 and doc["groups"][0]["name"] == "main" and "renamed" not in out["backup"]       # the file holds the state BEFORE


def test_group_update_reports_the_inbounds_added_and_removed_and_does_nothing_when_nothing_changes(pg):
    out = pa.group_update(C, 1, {"inbound_tags": ["TROJAN TCP TLS 2080", "Shadowsocks TCP"], "is_disabled": False})
    assert out["added"] == ["Shadowsocks TCP"] and out["removed"] == ["VLESS WS 80"] and pg.groups[1]["inbound_tags"] == ["TROJAN TCP TLS 2080", "Shadowsocks TCP"]
    pg.log.clear()
    same = pa.group_update(C, 1, {"name": "main", "inbound_tags": ["Shadowsocks TCP", "TROJAN TCP TLS 2080"]})            # the same set in another order
    assert same["changed"] is False and same["backup"] == "" and pg.writes() == []

    for ch, why in (({"name": "x"}, "needs 3"), ({"inbound_tags": []}, "at least one"), ({"inbound_tags": ["Nope"]}, "Nope not found")):
        with pytest.raises((ValueError, panels.PanelError), match=why):
            pa.group_update(C, 1, ch)

    with pytest.raises(ValueError, match="not on the panel"):
        pa.group_update(C, 42, {"name": "abc"})


def test_a_name_clash_is_refused_but_an_old_duplicate_does_not_block_other_changes(pg):
    pa.group_create(C, "second", ["VLESS WS 80"])

    with pytest.raises(ValueError, match="exists already"):
        pa.group_update(C, 2, {"name": "Main"})

    pg.groups[2]["name"] = "main"                                                              # a duplicate that was there before
    assert pa.group_update(C, 2, {"is_disabled": True})["changed"] is True


def test_deleting_a_group_that_is_in_use_needs_a_yes(pg):
    pg.add_template(name="plan", group_ids=[1])

    with pytest.raises(ValueError, match="used by 30 users and 1 templates"):
        pa.group_delete(C, 1)

    assert pg.writes() == [] and 1 in pg.groups
    out = pa.group_delete(C, 1, force=True)
    assert out["deleted"]["name"] == "main" and out["users"] == 30 and out["templates"] == ["plan"] and 1 not in pg.groups and pg.templates[1]["group_ids"] == []
    doc = backup_doc(out["backup"])
    assert doc["groups"][0]["name"] == "main" and doc["templates"][0]["group_ids"] == [1]               # the file still has the group and the template as they were
    empty = pa.group_create(C, "nobody", ["VLESS WS 80"])
    assert pa.group_delete(C, empty["id"])["users"] == 0                                              # nothing uses it: no yes needed

    with pytest.raises(ValueError, match="not on the panel"):
        pa.group_delete(C, 99)


def test_bulk_group_actions(pg):
    a, b = pa.group_create(C, "aaa", ["VLESS WS 80"])["id"], pa.group_create(C, "bbb", ["VLESS WS 80"])["id"]
    pg.log.clear()
    out = pa.group_bulk(C, "disable", [a, b])
    assert out["count"] == 2 and out["groups"] == ["aaa", "bbb"] and out["backup"] and pg.groups[a]["is_disabled"] and pg.groups[b]["is_disabled"]
    assert pg.writes() == [("POST", "/api/groups/bulk/disable", {"ids": [a, b]})]
    assert pa.group_bulk(C, "enable", [a])["count"] == 1 and pg.groups[a]["is_disabled"] is False

    with pytest.raises(ValueError, match="used by 30 users"):
        pa.group_bulk(C, "delete", [1, a])

    assert 1 in pg.groups and a in pg.groups
    assert pa.group_bulk(C, "delete", [a, b])["count"] == 2 and a not in pg.groups                      # nobody uses them: no force needed
    assert pa.group_bulk(C, "delete", [1], force=True)["count"] == 1

    for args, why in ((("explode", [1]), "enable, disable or delete"), (("enable", []), "pick at least one"), (("enable", [77]), "not on the panel")):
        with pytest.raises(ValueError, match=why):
            pa.group_bulk(C, *args)


def test_members_need_an_explicit_choice_of_who_and_count_first(pg):
    out = pa.group_members(C, "add", [1], has_no_group=True)
    assert out == {"dry_run": True, "action": "add", "users": 10, "detail": ""} and pg.members[1] == set(range(1, 31))                  # the default only counts
    assert pg.writes() == [("POST", "/api/groups/bulk/add", {"group_ids": [1], "has_group_ids": [], "has_no_group": True, "admins": [], "users": [], "dry_run": True})]
    g2 = pa.group_create(C, "second", ["VLESS WS 80"])["id"]
    done = pa.group_members(C, "add", [g2], has_group_ids=[1], dry_run=False)
    assert done["users"] == 30 and done["dry_run"] is False and pg.members[g2] == set(range(1, 31))
    assert pa.group_members(C, "remove", [1], user_ids=[1, 2], dry_run=False)["users"] == 2 and 1 not in pg.members[1]
    assert pa.group_members(C, "add", [g2], all_users=True)["users"] == 40

    for kw, why in (({}, "choose who is changed"), ({"all_users": True, "has_no_group": True}, "cannot be mixed"), ({"has_no_group": True, "has_group_ids": [1]}, "nobody is both"),
                    ({"user_ids": [1], "has_group_ids": [1]}, "OR"), ({"has_group_ids": [0]}, "not a whole number")):
        with pytest.raises(ValueError, match=why):
            pa.group_members(C, "add", [1], **kw)

    with pytest.raises(ValueError, match="add or remove"):
        pa.group_members(C, "swap", [1], all_users=True)

    with pytest.raises(ValueError, match="pick at least one group"):
        pa.group_members(C, "add", [], all_users=True)

    before = len(pg.writes())

    with pytest.raises(ValueError):
        pa.group_members(C, "add", [1])                                                          # an empty choice never reaches the panel (it would mean every user)

    assert len(pg.writes()) == before


# ------------------------------------------------------------------ templates
def test_create_a_template_with_the_panels_units(pg):
    out = pa.template_create(C, {"name": "plan 30d", "group_ids": [1], "data_limit_gb": 1.5, "expire_days": 30, "username_prefix": "nk", "username_suffix": "x", "data_limit_reset_strategy": "month", "method": "aes-256-gcm"})
    assert pg.writes() == [("POST", "/api/user_template", {"name": "plan 30d", "group_ids": [1], "data_limit": 1610612736, "expire_duration": 2592000, "username_prefix": "nk", "username_suffix": "x", "status": "active",
                                                           "data_limit_reset_strategy": "month", "reset_usages": False, "on_hold_timeout": None, "hwid_limit": None, "is_disabled": False, "extra_settings": {"method": "aes-256-gcm"}})]
    t = out["template"]
    assert (t["id"], t["data_limit_gb"], t["expire_days"], t["method"], t["data_limit_reset_strategy"]) == (1, 1.5, 30.0, "aes-256-gcm", "month")

    for ch, why in (({"name": "p2", "group_ids": [9]}, "group 9 is not on the panel"), ({"name": "PLAN 30D", "group_ids": [1]}, "exists already"), ({"name": "p3", "group_ids": [1], "status": "on_hold"}, "ON HOLD needs a duration"),
                    ({"name": "p4", "group_ids": [1], "username_prefix": "a--b"}, "never two"), ({"name": "p5", "group_ids": []}, "pick at least one group"), ({"group_ids": [1]}, "template name needs")):
        with pytest.raises(ValueError, match=why):
            pa.template_create(C, ch)

    assert len(pg.templates) == 1
    pg.refuse = (409, "Template by this name already exists")

    with pytest.raises(panels.PanelError, match="409: Template by this name"):
        pa.template_create(C, {"name": "other", "group_ids": [1]})                              # the panel's own reason comes through


def test_a_template_edit_changes_only_what_was_asked(pg):
    pg.add_template(name="plan", data_limit=107374181, expire_duration=2592000, username_prefix="nk", data_limit_reset_strategy="week", hwid_limit=2, extra_settings={"method": "aes-256-gcm"}, group_ids=[1])
    pg.log.clear()
    out = pa.template_update(C, 1, {"name": "plan b", "expire_days": 60})
    put = pg.writes()[0]
    assert put[0:2] == ("PUT", "/api/user_template/1")
    assert put[2] == {"name": "plan b", "group_ids": [1], "data_limit": 107374181, "expire_duration": 5184000, "username_prefix": "nk", "username_suffix": "", "status": "active",
                      "data_limit_reset_strategy": "week", "reset_usages": False, "on_hold_timeout": 0, "hwid_limit": 2, "is_disabled": False, "extra_settings": {"method": "aes-256-gcm"}}
    assert pg.templates[1]["data_limit_reset_strategy"] == "week"                               # the panel did not reset it, because it was sent
    assert out["changed"] and out["before"]["name"] == "plan" and out["template"]["expire_days"] == 60.0 and backup_doc(out["backup"])["templates"][0]["name"] == "plan"
    pg.log.clear()
    assert pa.template_update(C, 1, {"data_limit_gb": 0.1})["changed"] is False and pg.writes() == []                    # 0.1 GB is what it holds already: not even a PUT
    out = pa.template_update(C, 1, {"username_prefix": None, "hwid_limit": None, "group_ids": [1], "data_limit_reset_strategy": "no_reset", "is_disabled": True})
    t = pg.templates[1]
    assert (t["username_prefix"], t["hwid_limit"], t["data_limit_reset_strategy"], t["is_disabled"], t["data_limit"]) == ("", None, "no_reset", True, 107374181)
    assert out["changed"] is True

    for ch, why in (({"group_ids": [9]}, "group 9 is not on the panel"), ({"status": "on_hold", "expire_days": 0}, "ON HOLD needs"), ({"name": ""}, "needs 1 to 64")):
        with pytest.raises(ValueError, match=why):
            pa.template_update(C, 1, ch)

    with pytest.raises(ValueError, match="not on the panel"):
        pa.template_update(C, 5, {"name": "x"})


def test_a_key_of_extra_settings_this_module_does_not_know_is_kept(pg):
    """PasarGuard 5.4 has only `method` in extra_settings; an older panel also had `flow`. An edit and a restore must not drop what they do not know."""
    pg.add_template(name="old", group_ids=[1], extra_settings={"method": "aes-256-gcm", "flow": "xtls-rprx-vision"})
    pa.template_update(C, 1, {"name": "older", "method": "aes-128-gcm"})
    assert pg.templates[1]["extra_settings"] == {"flow": "xtls-rprx-vision", "method": "aes-128-gcm"}
    out = pa.template_delete(C, 1)
    done = pa.restore(C, out["backup"], groups=[], templates=[1], dry_run=False)
    assert done["created"] == 1 and pg.templates[done["items"][0]["new_id"]]["extra_settings"] == {"flow": "xtls-rprx-vision", "method": "aes-128-gcm"}


def test_a_template_name_that_clashes_is_refused(pg):
    pg.add_template(name="one")
    pg.add_template(name="two")

    with pytest.raises(ValueError, match="exists already"):
        pa.template_update(C, 2, {"name": "ONE"})

    assert pa.template_update(C, 2, {"name": "three"})["changed"] is True


def test_delete_and_bulk_templates(pg):
    pg.add_template(name="one")
    pg.add_template(name="two")
    pg.add_template(name="three")
    out = pa.template_delete(C, 1)
    assert out["deleted"]["name"] == "one" and 1 not in pg.templates and backup_doc(out["backup"])["templates"][0]["name"] == "one"
    assert pa.template_bulk(C, "disable", [2, 3])["count"] == 2 and pg.templates[2]["is_disabled"] and pg.templates[3]["is_disabled"]
    assert pa.template_bulk(C, "enable", [2])["templates"] == ["two"] and pa.template_bulk(C, "delete", [2, 3])["count"] == 2 and pg.templates == {}

    for args, why in ((("enable", [9]), "not on the panel"), (("zap", [9]), "enable, disable or delete"), (("enable", []), "pick at least one")):
        with pytest.raises(ValueError, match=why):
            pa.template_bulk(C, *args)

    with pytest.raises(ValueError, match="not on the panel"):
        pa.template_delete(C, 1)


def test_a_user_from_a_template(pg):
    pg.add_template(name="plan", username_prefix="nk-", username_suffix="-x", data_limit=GB)
    out = pa.template_user(C, 1, " alice ", " for a friend ")
    assert pg.writes() == [("POST", "/api/user/from_template", {"username": "alice", "user_template_id": 1, "note": "for a friend"})]            # the panel puts the prefix and suffix around it
    assert out["username"] == "nk-alice-x" and out["user"]["username"] == "nk-alice-x" and out["user"]["sub"] == "https://sub.example.test/sub/nk-alice-x" and out["user"]["limit"] == GB
    pg.log.clear()

    with pytest.raises(panels.PanelError, match="409: User already exists"):
        pa.template_user(C, 1, "alice")

    pg.templates[1]["is_disabled"] = True
    pg.log.clear()

    with pytest.raises(ValueError, match="switched off"):
        pa.template_user(C, 1, "bob")

    assert pg.writes() == []                                                                    # refused before the panel is asked to make anything
    pg.templates[1]["is_disabled"] = False

    for name, why in (("ab", "3 to 128"), ("a b", "letters, digits"), ("a__b", "two of"), ("x" * 125, "3 to 128")):
        with pytest.raises(ValueError, match=why):
            pa.template_user(C, 1, name)

    with pytest.raises(ValueError, match="500"):
        pa.template_user(C, 1, "carol", "n" * 501)

    with pytest.raises(panels.PanelError, match="404"):
        pa.template_user(C, 7, "dave")

    assert list(pg.users) == ["nk-alice-x"]


# ------------------------------------------------------------------ backups and the way back
def test_backups_are_listed_newest_first_and_only_the_last_twenty_are_kept(pg):
    for i in range(23):
        pa.group_update(C, 1, {"name": f"main {i:02d}"})

    files = sorted(p.name for p in (get_settings().data_dir / "pg-backups").glob("panel2-access-*.json"))
    assert len(files) == 20
    assert re.fullmatch(r"panel2-access-\d{8}-\d{6}(-\d+)?\.json", files[0])
    listed = pa.backups(C)
    assert len(listed) == 20 and listed[0]["groups"] == 1 and listed[0]["templates"] == 0 and "main 21" in listed[0]["reason"] and listed[0]["at"] > 0
    assert pa.backups({"id": 5, "type": "pasarguard"}) == []                                    # another panel's id has none


def test_backups_of_the_same_second_keep_their_order_and_names_even_from_threads():
    seq = [pa.write_backup(C, f"r{i}", [], []) for i in range(25)]
    assert len(set(seq)) == 25 and [b["reason"] for b in pa.backups(C)] == [f"r{i}" for i in range(24, 4, -1)]            # the newest 20, newest first, though most share one second
    with ThreadPoolExecutor(max_workers=8) as ex:
        names = list(ex.map(lambda i: pa.write_backup(C, f"t{i}", [], []), range(30)))

    assert len(set(names)) == 30 and len(pa.backups(C)) == 20                                                         # no name was used twice: nothing was overwritten


def test_a_group_that_was_deleted_comes_back_and_the_templates_follow_it(pg):
    extra = pa.group_create(C, "extra", ["VLESS TCP 1616"])["id"]
    pg.add_template(name="plan", group_ids=[1, extra], data_limit=GB, expire_duration=86400, data_limit_reset_strategy="month")
    out = pa.group_delete(C, extra, force=True)
    assert pg.templates[1]["group_ids"] == [1]
    pg.log.clear()
    plan = pa.restore(C, out["backup"], groups=[extra], templates=[], dry_run=True)
    assert plan["dry_run"] is True and plan["plan"] == {"create": 1, "update": 0, "same": 0, "skip": 0} and plan["items"][0]["action"] == "create" and pg.writes() == []                  # a plan changes nothing
    done = pa.restore(C, out["backup"], groups=[extra], templates=[1], dry_run=False)
    new = done["items"][0]["new_id"]
    assert done["created"] == 1 and done["updated"] == 1 and done["failed"] == 0 and new and new != extra and pg.groups[new]["name"] == "extra" and pg.groups[new]["inbound_tags"] == ["VLESS TCP 1616"]
    assert pg.templates[1]["group_ids"] == sorted([1, new]) and pg.templates[1]["data_limit_reset_strategy"] == "month"          # the template points at the NEW id and kept its other fields
    assert done["backup"] and backup_doc(done["backup"])["groups"][0]["id"] == 1                                              # a backup of the state before the restore
    assert [w[0] for w in pg.writes()] == ["POST", "PUT"]                                                                      # nothing was deleted
    assert pa.restore(C, out["backup"], groups=[extra], templates=[1], dry_run=False)["items"][0]["action"] == "skip"           # the group is there again (same name): left alone


def test_a_restore_puts_changed_fields_back_but_only_for_what_was_asked(pg):
    pa.group_create(C, "second", ["VLESS WS 80"])
    pg.add_template(name="plan", group_ids=[1], data_limit=GB, username_prefix="nk")
    snap = pa.group_update(C, 1, {"name": "main renamed", "is_disabled": True, "inbound_tags": ["Shadowsocks TCP"]})["backup"]           # the file holds 'main' as it was
    pg.groups[2]["name"] = "second changed"
    pg.templates[1]["name"] = "plan changed"
    pg.log.clear()
    one = pa.restore(C, snap, groups=[1], templates=[], dry_run=False)
    assert pg.groups[1] == {"id": 1, "name": "main", "inbound_tags": ["VLESS WS 80", "TROJAN TCP TLS 2080"], "is_disabled": False}
    assert pg.groups[2]["name"] == "second changed" and pg.templates[1]["name"] == "plan changed"        # the others were not asked for: untouched
    assert [i["kind"] + ":" + i["action"] for i in one["items"]] == ["group:update"] and [w[:2] for w in pg.writes()] == [("PUT", "/api/group/1")]
    pa.group_create(C, "third", ["VLESS WS 80"])                                                          # made after the file: the file does not know it, and a restore never deletes
    pg.log.clear()
    both = pa.restore(C, snap, everything=True, dry_run=False)
    assert pg.groups[2]["name"] == "second" and pg.groups[3]["name"] == "third"
    assert pg.templates[1]["name"] == "plan" and pg.templates[1]["username_prefix"] == "nk"
    assert {(i["kind"], i["action"]) for i in both["items"]} == {("group", "same"), ("group", "update"), ("template", "update")}
    pg.log.clear()
    assert pa.restore(C, snap, everything=True, dry_run=False)["plan"] == {"create": 0, "update": 0, "same": 3, "skip": 0} and pg.writes() == []        # nothing left to do, nothing written


def test_a_restore_skips_what_cannot_be_made_and_goes_on_after_a_failure(pg):
    only = pa.group_create(C, "only", ["VLESS TCP 1616"])["id"]
    pg.add_template(name="needs only", group_ids=[only])
    pg.add_template(name="two groups", group_ids=[1, only])
    snap = pa.group_delete(C, only, force=True)["backup"]
    pa.template_delete(C, 1)
    pg.templates[2]["data_limit"] = 5                                                           # 'two groups' changed since the file was written
    pg.log.clear()
    plan = pa.restore(C, snap, groups=[], templates=[1, 2], dry_run=True)
    acts = {i["name"]: (i["action"], i["reason"]) for i in plan["items"]}
    assert acts["needs only"][0] == "skip" and "none of its groups" in acts["needs only"][1]                  # its only group is gone and not being restored
    assert acts["two groups"][0] == "update" and "1 of its groups are gone" in acts["two groups"][1] and pg.writes() == []
    done = pa.restore(C, snap, groups=[only], templates=[1, 2], dry_run=False)
    assert {i["name"]: i["action"] for i in done["items"]} == {"only": "create", "needs only": "create", "two groups": "update"}
    new = done["items"][0]["new_id"]
    assert pg.templates[2]["group_ids"] == sorted([1, new]) and pg.templates[2]["data_limit"] == 0 and [t["group_ids"] for t in pg.templates.values() if t["name"] == "needs only"] == [[new]]
    pg.groups = {k: v for k, v in pg.groups.items() if v["name"] != "only"}
    pg.log.clear()
    pg.refuse = (400, "VLESS TCP 1616 not found")
    res = pa.restore(C, snap, groups=[only], templates=[], dry_run=False)
    assert res["failed"] == 1 and res["items"][0]["ok"] is False and "not found" in res["items"][0]["reason"] and res["created"] == 0
    assert not any(w[0] == "DELETE" for w in pg.writes())                                                     # a restore never deletes


def test_a_restore_refuses_files_that_are_not_ours(pg):
    mine = pa.group_update(C, 1, {"name": "again"})["backup"]
    d = get_settings().data_dir / "pg-backups"
    (d / "panel9-access-20260101-000000.json").write_text(json.dumps({"kind": "pg-access", "groups": [], "templates": []}), encoding="utf-8")
    (d / "panel2-access-20260101-000001.json").write_text("not json", encoding="utf-8")
    (d / "panel2-access-20260101-000002.json").write_text(json.dumps({"kind": "other"}), encoding="utf-8")

    for name, why in (("../../etc/passwd", "not a backup of this panel"), ("panel9-access-20260101-000000.json", "not a backup of this panel"), ("panel2-access-20260101-999999.json", "not there"),
                      ("panel2-access-20260101-000001.json", "cannot be read"), ("panel2-access-20260101-000002.json", "not a backup of groups"), ("", "not a backup")):
        with pytest.raises(ValueError, match=why):
            pa.restore(C, name, everything=True)

    with pytest.raises(ValueError, match="say what to restore"):
        pa.restore(C, mine)

    assert [x["name"] for x in pa.backups(C)] == [mine]                                         # the broken files are not listed


def test_a_backup_that_cannot_be_written_stops_the_change(pg, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pa, "_backup_dir", boom)

    with pytest.raises(RuntimeError, match="nothing was changed"):
        pa.group_delete(C, 1, force=True)

    assert pg.writes() == [] and 1 in pg.groups


# ------------------------------------------------------------------ the API
@pytest.fixture
def panel_row():
    with session() as db:
        p = Panel(name="pg-test", type="pasarguard", url="https://panel-a.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


@pytest.fixture
def marzban_row():
    with session() as db:
        p = Panel(name="mz-test", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_the_group_routes(client, pg, panel_row):
    base = f"/api/panels/{panel_row}/pg/groups"
    j = client.get(base).json()
    assert j["ok"] is True and j["groups"][0]["users"] == 30 and j["inbounds"] and j["total"] == 1
    assert client.get(base + "/1").json()["group"]["name"] == "main"
    j = client.post(base, json={"name": "api-group", "inbound_tags": ["VLESS WS 80"]}).json()
    gid = j["group"]["id"]
    assert j["ok"] is True and pg.groups[gid]["is_disabled"] is False
    j = client.put(f"{base}/{gid}", json={"is_disabled": True}).json()
    assert j["changed"] is True and pg.groups[gid]["is_disabled"] is True and pg.groups[gid]["name"] == "api-group"
    assert client.put(f"{base}/{gid}", json={"is_disabled": True}).json()["changed"] is False
    j = client.post(base + "/bulk", json={"action": "enable", "ids": [gid]}).json()
    assert j["count"] == 1 and pg.groups[gid]["is_disabled"] is False
    j = client.post(base + "/members", json={"action": "add", "group_ids": [gid], "has_no_group": True}).json()
    assert j["dry_run"] is True and j["users"] == 10 and pg.members.get(gid, set()) == set()
    j = client.post(base + "/members", json={"action": "add", "group_ids": [gid], "has_no_group": True, "dry_run": False}).json()
    assert j["dry_run"] is False and pg.members[gid] == set(range(31, 41))
    r = client.delete(f"{base}/{gid}")
    assert r.status_code == 400 and "used by 10 users" in r.json()["error"] and gid in pg.groups                      # in use: refused with the reason
    assert client.delete(f"{base}/{gid}?force=true").json()["users"] == 10 and gid not in pg.groups


def test_the_group_routes_refuse_bad_input_and_report_the_panels_reason(client, pg, panel_row):
    base = f"/api/panels/{panel_row}/pg/groups"
    assert client.post(base, json={"name": "ab", "inbound_tags": ["VLESS WS 80"]}).status_code == 422
    assert client.post(base, json={"name": "fine", "inbound_tags": []}).status_code == 422
    assert client.post(base, json={"name": "fine"}).status_code == 422
    assert client.put(base + "/1", json={"name": "x"}).status_code == 422
    assert client.put(base + "/1", json={"inbound_tags": []}).status_code == 422
    assert client.post(base + "/bulk", json={"action": "explode", "ids": [1]}).status_code == 422
    assert client.post(base + "/bulk", json={"action": "enable", "ids": []}).status_code == 422
    assert client.post(base + "/members", json={"action": "add", "group_ids": [1]}).status_code == 400                    # no 'who': the panel would take it as everybody
    r = client.post(base, json={"name": "main", "inbound_tags": ["VLESS WS 80"]})
    assert r.status_code == 400 and "exists already" in r.json()["error"]
    r = client.post(base, json={"name": "newone", "inbound_tags": ["No Such Tag"]})
    assert r.status_code == 502 and "No Such Tag not found" in r.json()["error"]                                          # the panel's own reason
    r = client.get(base + "/77")
    assert r.status_code == 502 and "404" in r.json()["error"] and "Group not found" in r.json()["error"]
    assert client.get("/api/panels/9999/pg/groups").status_code == 400


def test_the_template_routes(client, pg, panel_row):
    base = f"/api/panels/{panel_row}/pg/templates"
    j = client.get(base).json()
    assert j["templates"] == [] and j["groups"] == [{"id": 1, "name": "main", "is_disabled": False}] and "month" in j["resets"] and "aes-256-gcm" in j["methods"]
    j = client.post(base, json={"name": "api plan", "group_ids": [1], "data_limit_gb": 2, "expire_days": 7, "username_prefix": "ap"}).json()
    tid = j["template"]["id"]
    assert j["template"]["data_limit"] == 2 * GB and j["template"]["expire_duration"] == 604800
    assert client.get(f"{base}/{tid}").json()["template"]["username_prefix"] == "ap"
    j = client.put(f"{base}/{tid}", json={"data_limit_gb": 3, "username_prefix": None}).json()
    assert j["changed"] is True and pg.templates[tid]["data_limit"] == 3 * GB and pg.templates[tid]["username_prefix"] == "" and pg.templates[tid]["expire_duration"] == 604800
    j = client.put(f"{base}/{tid}", json={"status": None, "name": None}).json()                                      # a null that would only mean 'keep' is dropped
    assert j["changed"] is False
    j = client.post(f"{base}/bulk", json={"action": "disable", "ids": [tid]}).json()
    assert j["count"] == 1 and pg.templates[tid]["is_disabled"] is True
    r = client.post(f"{base}/{tid}/user", json={"username": "newbie"})
    assert r.status_code == 400 and "switched off" in r.json()["error"]
    client.post(f"{base}/bulk", json={"action": "enable", "ids": [tid]})
    j = client.post(f"{base}/{tid}/user", json={"username": "newbie", "note": "api"}).json()
    assert j["ok"] is True and j["username"] == "newbie" and j["user"]["sub"].endswith("/newbie") and "newbie" in pg.users
    assert client.delete(f"{base}/{tid}").json()["deleted"]["name"] == "api plan" and pg.templates == {}


def test_the_template_routes_refuse_bad_input(client, pg, panel_row):
    base = f"/api/panels/{panel_row}/pg/templates"
    good = {"name": "p", "group_ids": [1]}

    for bad in ({**good, "name": ""}, {**good, "group_ids": []}, {**good, "data_limit_gb": -1}, {**good, "expire_days": 30000}, {**good, "status": "disabled"}, {**good, "data_limit_reset_strategy": "hourly"},
                {**good, "method": "rot13"}, {**good, "username_prefix": "x" * 21}, {**good, "hwid_limit": -1}, {"name": "p"}):
        assert client.post(base, json=bad).status_code == 422, bad

    r = client.post(base, json={**good, "group_ids": [9]})
    assert r.status_code == 400 and "group 9" in r.json()["error"]
    r = client.post(base, json={**good, "status": "on_hold"})
    assert r.status_code == 400 and "ON HOLD" in r.json()["error"]
    assert client.put(base + "/5", json={"name": "x"}).status_code == 400
    assert client.post(base + "/5/user", json={"username": "ab"}).status_code == 422
    r = client.post(base + "/5/user", json={"username": "abcd"})
    assert r.status_code == 502 and "404" in r.json()["error"]
    assert client.delete(base + "/5").status_code == 400
    assert client.post(base + "/bulk", json={"action": "delete", "ids": [5]}).status_code == 400


def test_the_backup_routes(client, pg, panel_row):
    base = f"/api/panels/{panel_row}/pg"
    assert client.get(base + "/access/backups").json()["backups"] == []
    name = client.put(base + "/groups/1", json={"name": "renamed"}).json()["backup"]
    pg.groups[1]["name"] = "later"
    listed = client.get(base + "/access/backups").json()["backups"]
    assert [b["name"] for b in listed] == [name] and listed[0]["groups"] == 1
    j = client.post(f"{base}/access/backups/{name}/restore", json={"groups": [1]}).json()                                  # dry_run is the default
    assert j["dry_run"] is True and j["items"][0]["action"] == "update" and pg.groups[1]["name"] == "later"
    j = client.post(f"{base}/access/backups/{name}/restore", json={"groups": [1], "dry_run": False}).json()
    assert j["updated"] == 1 and pg.groups[1]["name"] == "main"
    assert client.post(f"{base}/access/backups/{name}/restore", json={}).status_code == 400
    assert client.post(f"{base}/access/backups/panel2-access-20260101-000000.json/restore", json={"everything": True}).status_code == 400
    assert client.post(f"{base}/access/backups/{name}/restore", json={"groups": [0]}).status_code == 400


def test_a_marzban_panel_has_no_groups_and_a_panel_that_is_down_is_reported(client, pg, marzban_row, panel_row, monkeypatch):
    for path in ("groups", "templates", "access/backups/x.json/restore"):
        r = client.post(f"/api/panels/{marzban_row}/pg/{path}", json={"name": "abc", "group_ids": [1], "inbound_tags": ["a"], "everything": True}) if path.endswith("restore") else client.get(f"/api/panels/{marzban_row}/pg/{path}")
        assert r.status_code == 400 and "PasarGuard" in r.json()["error"], path

    assert pg.log == []                                                                          # not one call reached a Marzban panel

    def down(*a, **k):
        raise panels.PanelError("no answer from the panel (ConnectError)")

    monkeypatch.setattr(panels, "call", down)

    for method, path, body in (("get", "groups", None), ("get", "templates", None), ("post", "groups", {"name": "abc", "inbound_tags": ["a"]}), ("delete", "groups/1", None), ("post", "templates", {"name": "p", "group_ids": [1]})):
        r = getattr(client, method)(f"/api/panels/{panel_row}/pg/{path}", **({"json": body} if body else {}))
        assert r.status_code == 502 and "no answer from the panel" in r.json()["error"], path
