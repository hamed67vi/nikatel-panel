"""The Users window and tools of a PasarGuard 5.4 panel: what a user looks like to the page, the body of a change (only what differs, the next plan always along, the end date removed with
0), the checks in front of the panel, one user's actions, devices, subscription fetches, the bulk routes by name (ids looked up), the shift of end dates and data limits (a count, a
confirm), the expired / limited / on-hold / disabled clean-up, and users made from a template. A fake panel stands in for PasarGuard and keeps ITS rules (a PUT with no next plan deletes
it, an on-hold user needs a duration and no end date, a dry run only counts); no test reaches a real panel."""
import copy
import re
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from nikatel.db import session
from nikatel.models import Event, Panel
from nikatel.services import panels, pg_users
from nikatel.services.panels import PanelError

C = {"id": 4, "type": "pasarguard", "username": "boss"}
GB = 1024 ** 3
DAY = 86400
PLAN = {"user_template_id": 2, "data_limit": 7 * GB + 12345, "expire": 30 * DAY + 7, "add_remaining_traffic": True}


def user(uid: int, name: str, **kw) -> dict:
    u = {"id": uid, "username": name, "status": "active", "used_traffic": 5 * GB, "lifetime_used_traffic": 9 * GB, "data_limit": 10 * GB, "data_limit_reset_strategy": "no_reset",
         "expire": "2027-01-31T23:59:59Z", "note": None, "on_hold_expire_duration": None, "on_hold_timeout": None, "group_ids": [1], "auto_delete_in_days": None, "hwid_limit": None,
         "next_plan": None, "created_at": "2026-01-01T00:00:00Z", "edit_at": None, "online_at": None, "subscription_url": f"https://p.example.test/sub/{name}", "admin": {"id": 1, "username": "boss"}}
    u.update(kw)
    return u


class Fake:
    """The user routes of a PasarGuard 5.4 panel, with the rules the real one enforces."""

    def __init__(self):
        self.users = {1: user(1, "alice", next_plan=copy.deepcopy(PLAN), note="vip"), 2: user(2, "bob", status="expired"), 3: user(3, "carol", status="disabled", expire=None, data_limit=None),
                      4: user(4, "dave", status="on_hold", expire=None, on_hold_expire_duration=3 * DAY)}
        self.admins = [{"id": 1, "username": "boss"}, {"id": 2, "username": "ann"}]
        self.hwids = {1: [{"id": 1, "hwid": "abcdef0123456789abcdef", "device_os": "iOS", "os_version": "17", "device_model": "iPhone", "created_at": "2026-01-02T00:00:00Z", "last_used_at": "2026-02-02T00:00:00Z"},
                          {"id": 2, "hwid": "a/b?c", "device_os": None, "os_version": None, "device_model": None, "created_at": "2026-01-03T00:00:00Z", "last_used_at": "2026-02-03T00:00:00Z"}]}
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.fail: dict[tuple[str, str], PanelError] = {}
        self.cleanup = {"expired": ["bob", "zed"], "limited": ["lee"], "on_hold": [], "disabled": ["carol"]}

    def __call__(self, c, method, path, **kw):
        u = urlsplit(path)
        q = {k: v for k, v in parse_qs(u.query).items()}
        body = kw.get("body")
        self.calls.append((method, u.path, copy.deepcopy(body), q))

        if (method, u.path) in self.fail:
            raise self.fail[(method, u.path)]

        if u.path == "/api/users/simple":
            names = q.get("usernames", [])
            return {"users": [{"id": x["id"], "username": x["username"]} for x in self.users.values() if not names or x["username"] in names], "total": len(self.users)}

        m = re.fullmatch(r"/api/user/by-id/(\d+)(/\w+)?", u.path)

        if m:
            return self.user_route(method, int(m.group(1)), m.group(2) or "", body, q)

        m = re.fullmatch(r"/api/user/(\d+)/hwids(?:/(.+))?", unquote(u.path) if "/hwids/" in u.path and "%" in u.path else u.path)

        if m:
            uid = int(m.group(1))

            if method == "GET":
                return {"hwids": copy.deepcopy(self.hwids.get(uid, [])), "count": len(self.hwids.get(uid, []))}

            if method == "DELETE":
                self.hwids[uid] = [h for h in self.hwids.get(uid, []) if h["hwid"] != m.group(2)]
                return {}

        if u.path.endswith("/hwids/reset") and method == "POST":
            self.hwids[int(u.path.split("/")[3])] = []
            return {}

        if u.path.startswith("/api/users/bulk/"):
            return self.bulk_route(u.path.rsplit("/", 1)[1], method, body)

        if u.path == "/api/users/expired":
            names = list(self.cleanup[q.get("target", ["expired"])[0]])

            if method == "GET":
                return names

            return {"users": names, "count": len(names)}

        if u.path == "/api/user/from_template" and method == "POST":
            if body["user_template_id"] != 2:
                raise PanelError("HTTP 404: User template not found", 404)

            uid = max(self.users) + 1
            self.users[uid] = user(uid, body["username"], note=body.get("note"))
            return copy.deepcopy(self.users[uid])

        if u.path == "/api/admins/simple":
            names = q.get("usernames", [])
            return {"admins": [a for a in self.admins if not names or a["username"] in names], "total": len(self.admins)}

        if u.path == "/api/groups/simple":
            return {"groups": [{"id": 1, "name": "Main"}, {"id": 2, "name": "Iran"}], "total": 2}

        if u.path == "/api/user_templates/simple":
            return {"templates": [{"id": 2, "name": "30 days"}], "total": 1}

        if u.path == "/api/nodes/simple":
            return {"nodes": [{"id": 7, "name": "frankfurt"}], "total": 1}

        if re.fullmatch(r"/api/node/online_stats/\d+/ip", u.path):
            return {"nodes": {"7": {"192.0.2.4": 2, "192.0.2.8": 1}, "9": {"192.0.2.99": 1}}}

        raise AssertionError(f"unexpected call {method} {path}")

    def user_route(self, method, uid, sub, body, q):
        if uid not in self.users:
            raise PanelError("HTTP 404: User not found", 404)

        u = self.users[uid]

        if sub == "" and method == "GET":
            return copy.deepcopy(u)

        if sub == "" and method == "DELETE":
            del self.users[uid]
            return {}

        if sub == "" and method == "PUT":
            return self.put(u, body)

        if sub == "/reset":
            u["used_traffic"] = 0
        elif sub == "/revoke_sub":
            u["subscription_url"] += "-new"
        elif sub == "/active_next":
            if not u["next_plan"]:
                raise PanelError("HTTP 400: User doesn't have next plan", 400)

            u["next_plan"] = None
        elif sub == "/disabled":
            u["status"] = "disabled" if body["disabled"] else "active"
        elif sub == "/set_owner":
            u["admin"] = next(a for a in self.admins if a["username"] == q["admin_username"][0])
        elif sub == "/sub_update":
            return {"updates": [{"created_at": "2026-03-03T00:00:00Z", "user_agent": "Hiddify/2.0 " + "x" * 300, "ip": "192.0.2.4", "hwid": "h" * 60}], "count": 1}
        else:
            raise AssertionError(f"unexpected user route {method} {sub}")

        return copy.deepcopy(u)

    @staticmethod
    def put(u, body):
        """The rules of crud modify_user and of the validators in front of it."""
        if body.get("status") == "on_hold":
            if not body.get("on_hold_expire_duration"):
                raise PanelError("HTTP 422: User cannot be on hold without a valid on_hold_expire_duration.", 422)

            if body.get("expire"):
                raise PanelError("HTTP 422: User cannot be on hold with specified expire.", 422)

        if body.get("status"):
            u["status"] = body["status"]

        if body.get("status") == "on_hold" or body.get("expire") == 0:
            u["expire"] = None
        elif body.get("expire") is not None:
            u["expire"] = body["expire"]

        for k in ("data_limit_reset_strategy", "group_ids", "hwid_limit", "on_hold_expire_duration", "on_hold_timeout"):
            if body.get(k) is not None:
                u[k] = body[k]

        if body.get("data_limit") is not None:
            u["data_limit"] = body["data_limit"] or None

        if body.get("note") is not None:
            u["note"] = body["note"] or None

        u["next_plan"] = copy.deepcopy(body["next_plan"]) if body.get("next_plan") is not None else None                    # (no plan in the body = none wanted: the panel deletes it)
        return copy.deepcopy(u)

    def bulk_route(self, what, method, body):
        if what in ("enable", "disable", "reset", "revoke_sub", "delete", "set_owner", "apply_template"):
            names = [self.users[i]["username"] for i in body["ids"]]

            if what == "delete":
                for i in body["ids"]:
                    del self.users[i]

                return {"users": names, "count": len(names)}

            return {"users": names, "count": len(names)}

        if what in ("expire", "data_limit"):
            hit = [x for x in self.users.values() if (not body.get("status") or x["status"] in body["status"]) and (not body.get("users") or x["id"] in body["users"])
                   and (not body.get("admins") or x["admin"]["id"] in body["admins"]) and (not body.get("group_ids") or set(body["group_ids"]) & set(x["group_ids"]))]

            if body["dry_run"]:
                return {"dry_run": True, "affected_users": len(hit)}

            return {"detail": f"operation has been successfuly done on {len(hit)} users"}

        if what == "from_template":
            return {"created": body["count"], "subscription_urls": [f"https://p.example.test/sub/{i}" for i in range(body["count"])]}

        raise AssertionError(f"unexpected bulk route {what}")

    def writes(self):
        return [x for x in self.calls if x[0] != "GET"]


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    return f


# ------------------------------------------------------------------ pure
def test_usernames_are_checked_like_the_panel_checks_them():
    assert pg_users.clean_username(" alice.b-2@x ") == "alice.b-2@x"

    for bad in ("", "ab", "a" * 129, "has space", "a--b", "a_.b", "ünï", None):
        with pytest.raises(ValueError, match="3 to 128"):
            pg_users.clean_username(bad)

    assert pg_users.check_usernames(["bob", "alice", "bob"]) == ["bob", "alice"]

    for bad in ([], None, "bob", ["bob", "x"]):
        with pytest.raises(ValueError):
            pg_users.check_usernames(bad)

    with pytest.raises(ValueError, match="at most 300"):
        pg_users.check_usernames([f"user{i}" for i in range(301)])


def test_days_are_ends_of_days_in_utc():
    assert pg_users.day_end("2027-01-31") == "2027-01-31T23:59:59Z"
    assert pg_users.day_of("2027-01-31T23:59:59Z") == "2027-01-31" and pg_users.day_of(1800000000) == "2027-01-15" and pg_users.day_of(None) == "" and pg_users.day_of(0) == ""

    for bad in ("2027-02-30", "27-1-1", "", None, "2027/01/01"):
        with pytest.raises(ValueError, match="date"):
            pg_users.day_end(bad)


def test_a_user_is_shown_in_gb_days_and_names():
    row = pg_users.user_row(user(1, "alice", next_plan=copy.deepcopy(PLAN), hwid_limit=3, group_ids=[2, 1], on_hold_expire_duration=36 * 3600))
    assert row["limit_gb"] == 10 and row["expire_day"] == "2027-01-31" and row["group_ids"] == [1, 2] and row["hwid_limit"] == 3 and row["admin"] == "boss" and row["admin_id"] == 1
    assert row["on_hold_days"] == 1.5 and row["status"] == "active" and row["sub"].endswith("/alice")
    assert row["next_plan"]["template_id"] == 2 and row["next_plan"]["expire_days"] == 30 and row["next_plan"]["add_remaining"] is True
    assert row["plan_raw"] == PLAN                                                                                          # exactly as the panel gave it (it goes back in every PUT)
    flat = pg_users.user_row(user(2, "bob", data_limit=0, expire=None))
    assert flat["limit_gb"] is None and flat["expire_day"] == "" and flat["next_plan"] is None and flat["plan_raw"] is None and pg_users.user_row(None)["username"] == ""


def test_a_change_carries_only_what_differs_and_always_the_next_plan():
    cur = pg_users.user_row(user(1, "alice", next_plan=copy.deepcopy(PLAN), note="vip"))
    assert pg_users.modify_body({"note": "gold"}, cur) == {"note": "gold", "next_plan": PLAN}
    assert pg_users.modify_body({"note": "vip", "limit_gb": 10, "expire_day": "2027-01-31", "reset": "no_reset", "group_ids": [1], "status": "active"}, cur) == {"next_plan": PLAN}
    body = pg_users.modify_body({"limit_gb": 12.5, "reset": "month", "expire_day": "2028-02-29", "group_ids": [3, 1, 3], "hwid_limit": 2, "status": "disabled"}, cur)
    assert body == {"status": "disabled", "data_limit": int(12.5 * GB), "data_limit_reset_strategy": "month", "expire": "2028-02-29T23:59:59Z", "group_ids": [1, 3], "hwid_limit": 2, "next_plan": PLAN}
    assert pg_users.modify_body({"limit_gb": None}, cur)["data_limit"] == 0 and pg_users.modify_body({"limit_gb": ""}, cur)["data_limit"] == 0          # unlimited is 0
    assert pg_users.modify_body({"expire_day": ""}, cur)["expire"] == 0                                                                                   # 0 removes the end date (null would leave it)
    assert pg_users.modify_body({"note": "x"}, pg_users.user_row(user(2, "bob"))) == {"note": "x"}                                                        # no plan, none sent


def test_the_next_plan_can_be_changed_or_taken_away():
    cur = pg_users.user_row(user(1, "alice", next_plan=copy.deepcopy(PLAN)))
    new = pg_users.modify_body({"next_plan": {"template_id": 5, "data_gb": 20, "expire_days": 90, "add_remaining": True}}, cur)["next_plan"]
    assert new == {"user_template_id": 5, "data_limit": None, "expire": None, "add_remaining_traffic": True}                        # from a template: its data and days count, the plan's own are dropped
    assert "next_plan" not in pg_users.modify_body({"next_plan": None, "note": "x"}, cur) and "next_plan" not in pg_users.modify_body({"next_plan": {}, "note": "x"}, cur)   # none wanted: not sent
    assert "next_plan" not in pg_users.modify_body({"next_plan": {"template_id": None, "data_gb": "", "expire_days": ""}, "note": "x"}, cur)
    assert pg_users.modify_body({"next_plan": {"data_gb": 3}}, pg_users.user_row(user(2, "bob")))["next_plan"] == {"user_template_id": None, "data_limit": 3 * GB, "expire": None, "add_remaining_traffic": False}
    assert pg_users.modify_body({"next_plan": {"data_gb": 0, "expire_days": 30}}, pg_users.user_row(user(2, "bob")))["next_plan"] == {"user_template_id": None, "data_limit": 0, "expire": 30 * DAY, "add_remaining_traffic": False}

    for bad, why in (({"template_id": "x"}, "is an id"), ({"template_id": 0, "data_gb": -1}, "between"), ({"data_gb": "lots"}, "number"), ({"data_gb": 1, "expire_days": 99999}, "between"),
                     ({"expire_days": 5}, "needs its data")):
        with pytest.raises(ValueError, match=why):
            pg_users.modify_body({"next_plan": bad}, cur)


def test_on_hold_needs_a_duration_and_has_no_end_date():
    cur = pg_users.user_row(user(1, "alice"))
    body = pg_users.modify_body({"status": "on_hold", "on_hold_days": 2.5}, cur)
    assert body["status"] == "on_hold" and body["on_hold_expire_duration"] == int(2.5 * DAY) and "expire" not in body
    assert pg_users.modify_body({"status": "on_hold", "on_hold_days": 2, "expire_day": ""}, cur).get("expire") is None                                   # (the panel clears the end date itself)

    with pytest.raises(ValueError, match="needs the number of days"):
        pg_users.modify_body({"status": "on_hold"}, cur)

    with pytest.raises(ValueError, match="needs the number of days"):
        pg_users.modify_body({"status": "on_hold", "on_hold_days": 0}, cur)

    with pytest.raises(ValueError, match="no end date"):
        pg_users.modify_body({"status": "on_hold", "on_hold_days": 2, "expire_day": "2027-01-01"}, cur)

    held = pg_users.user_row(user(4, "dave", status="on_hold", expire=None, on_hold_expire_duration=3 * DAY))
    assert pg_users.modify_body({"note": "x"}, held) == {"note": "x"} and pg_users.modify_body({"on_hold_days": 4}, held) == {"on_hold_expire_duration": 4 * DAY}
    left = pg_users.user_row(user(5, "eve", on_hold_expire_duration=3 * DAY))
    assert pg_users.modify_body({"status": "on_hold"}, left)["on_hold_expire_duration"] == 3 * DAY                                                       # the duration the panel kept is used
    assert pg_users.modify_body({"on_hold_timeout_day": "2027-05-05"}, cur)["on_hold_timeout"] == "2027-05-05T23:59:59Z" and pg_users.modify_body({"on_hold_timeout_day": ""}, pg_users.user_row(user(6, "f", on_hold_timeout="2027-05-05T00:00:00Z")))["on_hold_timeout"] == 0


def test_what_cannot_be_set_is_refused_in_plain_words():
    cur = pg_users.user_row(user(1, "alice", hwid_limit=2))
    bad = [({"status": "limited"}, "status is one of"), ({"status": "expired"}, "status is one of"), ({"limit_gb": -1}, "between"), ({"limit_gb": "lots"}, "number"), ({"reset": "hourly"}, "reset period"),
           ({"expire_day": "tomorrow"}, "date"), ({"note": "x" * 501}, "at most 500"), ({"group_ids": [0]}, "group ids"), ({"group_ids": "1"}, "group ids"), ({"group_ids": [True]}, "group ids"),
           ({"hwid_limit": ""}, "not taken away"), ({"hwid_limit": -1}, "between"), ({"on_hold_days": 99999}, "between"), ({"on_hold_timeout_day": "soon"}, "date")]

    for data, why in bad:
        with pytest.raises(ValueError, match=why):
            pg_users.modify_body(data, cur)

    assert pg_users.modify_body({"hwid_limit": None}, pg_users.user_row(user(2, "bob"))) == {}                                                           # nothing there, nothing to take away


# ------------------------------------------------------------------ reading
def test_a_user_in_full_comes_with_what_its_forms_offer(fake):
    v = pg_users.user_view(C, "alice")
    assert v["user"]["username"] == "alice" and v["user"]["note"] == "vip" and v["options"]["groups"] == [{"id": 1, "name": "Main"}, {"id": 2, "name": "Iran"}]
    assert v["options"]["templates"] == [{"id": 2, "name": "30 days"}] and [a["name"] for a in v["options"]["admins"]] == ["boss", "ann"]
    assert [(m, p) for m, p, _, _ in fake.calls if "by-id" in p] == [("GET", "/api/user/by-id/1")]                                                         # the id route, not the by-username one
    assert all("all" in q for m, p, b, q in fake.calls if p.endswith("/simple") and "users" not in p)                                                     # every one of the lists, not a page of them

    with pytest.raises(ValueError, match="no user called nobody"):
        pg_users.user_view(C, "nobody")


def test_the_devices_a_user_used(fake):
    d = pg_users.devices(C, "alice")
    assert d["count"] == 2 and d["devices"][0]["short"] == "abcdef01…cdef" and d["devices"][0]["os"] == "iOS" and d["devices"][1]["os"] == "" and d["devices"][1]["short"] == "a/b?c"
    assert pg_users.devices(C, "bob") == {"user_id": 2, "devices": [], "count": 0}


def test_a_device_id_with_odd_characters_is_written_into_the_path_safely(fake):
    pg_users.device_delete(C, "alice", "a/b?c")
    assert fake.writes()[-1][1] == "/api/user/1/hwids/a%2Fb%3Fc" or fake.writes()[-1][1] == "/api/user/1/hwids/a/b?c"
    assert [h["hwid"] for h in fake.hwids[1]] == ["abcdef0123456789abcdef"]
    pg_users.devices_reset(C, "alice")
    assert fake.hwids[1] == []

    for bad in ("", "x" * 201, None):
        with pytest.raises(ValueError, match="choose the device"):
            pg_users.device_delete(C, "alice", bad)


def test_the_last_fetches_of_the_subscription(fake):
    s = pg_users.sub_updates(C, "alice", -5, 999)
    assert s["count"] == 1 and s["updates"][0]["ip"] == "192.0.2.4" and len(s["updates"][0]["agent"]) == 200 and len(s["updates"][0]["hwid"]) == 40
    assert fake.calls[-1][1] == "/api/user/by-id/1/sub_update" and fake.calls[-1][3] == {"offset": ["0"], "limit": ["100"]}


def test_where_a_user_is_connected_from_now(fake):
    o = pg_users.online_ips(C, "alice")
    assert o["count"] == 3 and {"node": "frankfurt", "ip": "192.0.2.4", "connections": 2} in o["online"] and {"node": "9", "ip": "192.0.2.99", "connections": 1} in o["online"]


# ------------------------------------------------------------------ one user
def test_a_change_keeps_the_next_plan_in_the_panel(fake):
    out = pg_users.modify(C, "alice", {"note": "gold", "reset": "week"})
    assert out["changed"] == ["data_limit_reset_strategy", "note"] and out["user"]["note"] == "gold" and out["user"]["reset"] == "week"
    assert fake.users[1]["next_plan"] == PLAN                                                                                  # the panel kept it because the PUT carried it
    put = next(b for m, p, b, _ in fake.calls if m == "PUT")
    assert put["next_plan"] == PLAN and set(put) == {"note", "data_limit_reset_strategy", "next_plan"}


def test_a_change_with_nothing_new_is_refused_before_the_panel_is_asked(fake):
    with pytest.raises(ValueError, match="nothing to change"):
        pg_users.modify(C, "alice", {"note": "vip", "limit_gb": 10})

    assert fake.writes() == []


def test_the_end_date_is_removed_with_zero_and_the_status_can_be_set(fake):
    out = pg_users.modify(C, "alice", {"expire_day": "", "status": "disabled"})
    assert out["user"]["expire_day"] == "" and out["user"]["status"] == "disabled" and fake.users[1]["expire"] is None
    out = pg_users.modify(C, "alice", {"status": "on_hold", "on_hold_days": 5})
    assert out["user"]["status"] == "on_hold" and out["user"]["on_hold_days"] == 5 and fake.users[1]["expire"] is None


def test_the_panels_refusal_reaches_the_page_in_plain_words(fake):
    fake.fail[("PUT", "/api/user/by-id/1")] = PanelError("HTTP 422: some rule of the panel", 422)

    with pytest.raises(PanelError, match="some rule"):
        pg_users.modify(C, "alice", {"note": "x"})


def test_the_actions_on_one_user(fake):
    assert pg_users.action(C, "alice", "reset") == {"user": "alice", "action": "reset"} and fake.users[1]["used_traffic"] == 0
    pg_users.action(C, "alice", "revoke_sub")
    assert fake.users[1]["subscription_url"].endswith("-new")
    pg_users.action(C, "alice", "disable")
    assert fake.users[1]["status"] == "disabled"
    pg_users.action(C, "alice", "enable")
    assert fake.users[1]["status"] == "active"
    pg_users.action(C, "alice", "set_owner", "ann")
    assert fake.users[1]["admin"]["username"] == "ann" and fake.calls[-1][3] == {"admin_username": ["ann"]}
    pg_users.action(C, "alice", "active_next")
    assert fake.users[1]["next_plan"] is None

    with pytest.raises(PanelError, match="next plan"):
        pg_users.action(C, "alice", "active_next")

    for what, value, why in (("set_owner", "", "choose the admin"), ("fly", None, "action is one of"), ("delete", None, "confirm")):
        with pytest.raises(ValueError, match=why):
            pg_users.action(C, "alice", what, value)

    pg_users.action(C, "alice", "delete", confirm=True)
    assert 1 not in fake.users


# ------------------------------------------------------------------ several users
def test_the_bulk_routes_take_ids_looked_up_from_the_names(fake):
    out = pg_users.bulk(C, "disable", ["carol", "alice", "carol"])
    assert out == {"action": "disable", "count": 2, "users": ["carol", "alice"]}
    m, p, b, _ = fake.writes()[-1]
    assert (m, p, b) == ("POST", "/api/users/bulk/disable", {"ids": [3, 1]})                                                  # in the order of the names
    pg_users.bulk(C, "set_owner", ["bob"], "ann")
    assert fake.writes()[-1][:3] == ("PUT", "/api/users/bulk/set_owner", {"ids": [2], "admin_username": "ann"})
    pg_users.bulk(C, "apply_template", ["bob", "carol"], 2)
    assert fake.writes()[-1][:3] == ("POST", "/api/users/bulk/apply_template", {"user_template_id": 2, "ids": [2, 3]})


def test_the_bulk_actions_that_cannot_be_undone_need_a_confirm(fake):
    for what in ("delete", "revoke_sub", "reset"):
        with pytest.raises(ValueError, match="confirm"):
            pg_users.bulk(C, what, ["bob"])

    assert fake.writes() == []
    assert pg_users.bulk(C, "delete", ["bob", "carol"], confirm=True)["count"] == 2 and set(fake.users) == {1, 4}


def test_a_bulk_names_the_user_the_panel_does_not_know(fake):
    with pytest.raises(ValueError, match="no user called ghost"):
        pg_users.bulk(C, "enable", ["alice", "ghost"])

    for what, value, why in (("set_owner", None, "choose the admin"), ("apply_template", None, "choose the template"), ("apply_template", True, "choose the template"), ("fly", None, "action is one of")):
        with pytest.raises(ValueError, match=why):
            pg_users.bulk(C, what, ["alice"], value)

    assert fake.writes() == []


def test_a_shift_of_end_dates_is_counted_first_and_done_with_a_confirm(fake):
    dry = pg_users.shift(C, "expire", 30, {"status": ["active"], "admins": ["boss"], "expire_before_day": "2027-12-31"})
    assert dry == {"what": "expire", "dry_run": True, "count": 1}
    _m, p, b, _ = fake.writes()[-1]
    assert p == "/api/users/bulk/expire" and b == {"dry_run": True, "amount": 30 * DAY, "status": ["active"], "admins": [1], "expire_before": "2027-12-31T23:59:59Z"}   # admins by id

    with pytest.raises(ValueError, match="confirm"):
        pg_users.shift(C, "expire", 30, {"status": ["active"]}, dry_run=False)

    assert fake.writes()[-1][2]["dry_run"] is True                                                                            # nothing real was sent
    done = pg_users.shift(C, "expire", -7, {"status": ["active", "expired"]}, dry_run=False, confirm=True)
    assert done == {"what": "expire", "dry_run": False, "count": 2} and fake.writes()[-1][2]["amount"] == -7 * DAY and fake.writes()[-1][2]["dry_run"] is False


def test_a_shift_of_data_limits_by_users_and_groups(fake):
    out = pg_users.shift(C, "data_limit", 2.5, {"usernames": ["alice", "bob"], "group_ids": [1], "expire_after_day": "2026-01-01"})
    assert out["count"] == 2
    b = fake.writes()[-1][2]
    assert b["amount"] == int(2.5 * GB) and b["users"] == [1, 2] and b["group_ids"] == [1] and b["expire_after"] == "2026-01-01T00:00:00Z"


def test_a_shift_needs_a_filter_an_amount_and_known_names(fake):
    bad = [("expire", 5, {}, "choose whom"), ("expire", 5, {"note": "x"}, "choose whom"), ("expire", 0, {"status": ["active"]}, "not zero"), ("expire", "x", {"status": ["active"]}, "number"),
           ("expire", 99999, {"status": ["active"]}, "between"), ("fly", 5, {"status": ["active"]}, "shift the end date"), ("expire", 5, {"status": ["fast"]}, "status is one of"),
           ("expire", 5, {"group_ids": [0]}, "group ids"), ("expire", 5, {"admins": ["ghost"]}, "no admin called ghost"), ("expire", 5, {"usernames": ["ghost"]}, "no user called ghost"),
           ("expire", 5, {"expire_before_day": "2027-02-30"}, "date")]

    for what, amount, filters, why in bad:
        with pytest.raises(ValueError, match=why):
            pg_users.shift(C, what, amount, filters)

    assert fake.writes() == []


def test_the_cleanup_lists_first_and_deletes_only_with_the_state_typed(fake):
    assert pg_users.expired(C, "expired") == {"deleted": False, "count": 2, "users": ["bob", "zed"], "truncated": False}
    assert fake.calls[-1][3] == {"target": ["expired"]}
    pg_users.expired(C, "disabled", "ann", "2027-01-01", "2026-01-01")
    assert fake.calls[-1][3] == {"target": ["disabled"], "admin_username": ["ann"], "expired_before": ["2027-01-01T23:59:59Z"], "expired_after": ["2026-01-01T00:00:00Z"]}

    for typed in ("", "limited", None):
        with pytest.raises(ValueError, match="type the state"):
            pg_users.expired(C, "expired", delete=True, typed=typed)

    assert fake.writes() == []
    gone = pg_users.expired(C, "expired", delete=True, typed="expired")
    assert gone == {"deleted": True, "count": 2, "users": ["bob", "zed"]} and fake.writes()[-1][:2] == ("DELETE", "/api/users/expired")

    with pytest.raises(ValueError, match="state is one of"):
        pg_users.expired(C, "all")


def test_one_user_from_a_template(fake):
    out = pg_users.from_template(C, 2, " newbie ", "from the shop")
    assert out["user"]["username"] == "newbie" and out["user"]["note"] == "from the shop" and fake.writes()[-1][2] == {"user_template_id": 2, "username": "newbie", "note": "from the shop"}

    for args, why in (((0, "newbie"), "choose the template"), ((True, "newbie"), "choose the template"), ((2, "x"), "3 to 128")):
        with pytest.raises(ValueError, match=why):
            pg_users.from_template(C, *args)

    with pytest.raises(PanelError, match="not found"):
        pg_users.from_template(C, 9, "newbie2")


def test_many_users_from_a_template_with_random_or_numbered_names(fake):
    out = pg_users.bulk_from_template(C, 2, 3)
    assert out["created"] == 3 and len(out["links"]) == 3
    assert fake.writes()[-1][2] == {"user_template_id": 2, "count": 3, "strategy": "random", "note": None}                       # random: no name base, no start number (the panel refuses them)
    pg_users.bulk_from_template(C, 2, 500, "sequence", "shop-", 10, "batch 1")
    assert fake.writes()[-1][2] == {"user_template_id": 2, "count": 500, "strategy": "sequence", "note": "batch 1", "username": "shop-", "start_number": 10}
    pg_users.bulk_from_template(C, 2, 2, "sequence", "x")
    assert fake.writes()[-1][2]["start_number"] == 1

    bad = [((2, 0), "between"), ((2, 501), "between"), ((2, "x"), "number"), ((0, 5), "choose the template"), ((2, 5, "fancy"), "random or sequence"), ((2, 5, "sequence", ""), "base of the names"),
           ((2, 5, "sequence", "a--b"), "base of the names"), ((2, 5, "sequence", "ok", -1), "between")]

    for args, why in bad:
        with pytest.raises(ValueError, match=why):
            pg_users.bulk_from_template(C, *args)


# ------------------------------------------------------------------ the API
@pytest.fixture
def pid():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="boss", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_the_api_reads_changes_and_acts_on_a_user(client, fake, pid):
    base = f"/api/panels/{pid}/pg/user/alice"
    j = client.get(base).json()
    assert j["ok"] is True and j["user"]["username"] == "alice" and j["options"]["groups"]
    ch = client.put(base, json={"note": "gold", "hwid_limit": 3}).json()
    assert ch["ok"] is True and ch["changed"] == ["hwid_limit", "note"] and ch["user"]["hwid_limit"] == 3 and fake.users[1]["next_plan"] == PLAN
    assert client.put(base, json={"note": "gold"}).status_code == 400 and client.put(base, json={"status": "limited"}).status_code == 400
    assert client.put(base, json={"limit_gb": 12, "next_plan": None}).json()["user"]["next_plan"] is None                       # a plan can be taken away on purpose
    assert client.post(base + "/action", json={"what": "reset"}).json() == {"ok": True, "user": "alice", "action": "reset"}
    assert client.post(base + "/action", json={"what": "delete"}).status_code == 400 and client.post(base + "/action", json={"what": "set_owner", "value": "ann"}).status_code == 200
    assert client.get(base + "/devices").json()["count"] == 2 and client.get(base + "/sub-updates").json()["count"] == 1 and client.get(base + "/online").json()["count"] == 3
    assert client.delete(base + "/devices", params={"hwid": "a/b?c"}).json()["removed"] is True and client.post(base + "/devices/reset").json()["reset"] is True
    assert client.get(f"/api/panels/{pid}/pg/user/ghost").status_code == 400 and client.delete(base + "/devices").status_code == 422
    with session() as db:
        texts = [e.text for e in db.query(Event).all()]
        assert any(t.startswith("PasarGuard alpha: user alice changed") for t in texts) and any("user alice: set owner → ann" in t for t in texts)               # done from here, said once


def test_the_api_does_several_users_at_once(client, fake, pid):
    t = f"/api/panels/{pid}/pg/user-tools"
    o = client.get(t + "/options").json()
    assert o["ok"] is True and [g["name"] for g in o["groups"]] == ["Main", "Iran"] and o["templates"] == [{"id": 2, "name": "30 days"}] and [a["name"] for a in o["admins"]] == ["boss", "ann"]
    assert client.post(t + "/bulk", json={"what": "disable", "usernames": ["alice", "bob"]}).json()["count"] == 2
    assert client.post(t + "/bulk", json={"what": "delete", "usernames": ["bob"]}).status_code == 400 and client.post(t + "/bulk", json={"what": "delete", "usernames": []}).status_code == 400
    assert client.post(t + "/bulk", json={"what": "delete", "usernames": [f"user{i}" for i in range(301)]}).status_code == 422
    assert client.post(t + "/shift", json={"what": "expire", "amount": 10, "filters": {"status": ["active"]}}).json() == {"ok": True, "what": "expire", "dry_run": True, "count": 1}
    assert client.post(t + "/shift", json={"what": "expire", "amount": 10, "filters": {"status": ["active"]}, "dry_run": False}).status_code == 400
    assert client.get(t + "/expired").json()["users"] == ["bob", "zed"] and client.delete(t + "/expired").status_code == 400
    assert client.delete(t + "/expired", params={"typed": "expired"}).json()["deleted"] is True
    assert client.post(t + "/from-template", json={"template_id": 2, "username": "newbie"}).json()["user"]["username"] == "newbie"
    assert client.post(t + "/bulk-from-template", json={"template_id": 2, "count": 4}).json()["created"] == 4 and client.post(t + "/bulk-from-template", json={"template_id": 2, "count": 4, "strategy": "x"}).status_code == 400
    assert client.post(t + "/from-template", json={"template_id": 9, "username": "newbie2"}).status_code == 404                  # the panel's own 404


def test_a_user_called_expired_is_still_a_user(client, fake, pid):
    fake.users[9] = user(9, "expired")
    assert client.get(f"/api/panels/{pid}/pg/user/expired").json()["user"]["id"] == 9 and client.get(f"/api/panels/{pid}/pg/user-tools/expired").json()["users"] == ["bob", "zed"]


def test_the_api_is_for_signed_in_owners_of_pasarguard_panels_only(client, fake, pid):
    from fastapi.testclient import TestClient

    stranger = TestClient(client.app)
    assert stranger.get(f"/api/panels/{pid}/pg/user/alice").status_code == 401 and stranger.post(f"/api/panels/{pid}/pg/user-tools/bulk", json={"what": "enable", "usernames": ["bob"]}).status_code == 401
    with session() as db:
        mz = Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(mz)
        db.flush()
        mid = mz.id
    assert client.get(f"/api/panels/{mid}/pg/user/alice").status_code == 400 and client.get("/api/panels/9999/pg/user/alice").status_code == 404


def test_every_route_of_the_users_has_a_short_description(client):
    spec = client.app.openapi()
    mine = {(m, p): op for p, ops in spec["paths"].items() if "/pg/user/" in p or "/pg/user-tools/" in p for m, op in ops.items()}
    assert len(mine) == 15
    assert all(0 < len((op.get("description") or "").split("\n")[0]) <= 300 for op in mine.values())
