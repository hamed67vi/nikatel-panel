"""The admin roles of a PasarGuard 5.4 panel: what a role looks like to the page and to the panel, the checks in front of the panel, make / change / delete with the backups,
restore, and the API. A fake panel stands in for PasarGuard (the rules it enforces are the real ones: the owner role is read only, built-in roles stay, a role with admins stays,
names are unique); no test reaches a real panel."""
import copy
import json
import re
from urllib.parse import urlsplit

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Panel
from nikatel.services import panels, pg_roles
from nikatel.services.panels import PanelError

C = {"id": 4, "type": "pasarguard", "username": "boss"}
GB = 1024 ** 3
DAY = 86400


def owner_role() -> dict:
    return {"id": 1, "name": "owner", "is_owner": True, "permissions": {}, "limits": {}, "features": {"can_use_reset_strategy": True, "can_use_next_plan": True}, "access": {},
            "hwid": {"enabled": True, "mode": "use_global"}, "disabled_when_limited": False, "disconnect_users_when_limited": True, "disconnect_users_when_disabled": True, "created_at": "2026-01-01T00:00:00Z"}


def operator_role() -> dict:
    return {"id": 3, "name": "operator", "is_owner": False, "created_at": "2026-01-01T00:00:00Z",
            "permissions": {"users": {"create": True, "read": {"scope": 1}, "update": {"scope": 2}}, "nodes": {"read": True}, "settings": {"read": True}},
            "limits": {"max_users": 50, "data_limit_max": 100 * GB, "expire_max": 90 * DAY}, "features": {"can_use_reset_strategy": False, "can_use_next_plan": True},
            "access": {"require_template": True, "allowed_template_ids": [2, 1], "allowed_group_ids": None},
            "hwid": {"enabled": True, "forced": False, "require_hwid_for_manual_sub": False, "fallback_limit": 2, "min_limit": None, "max_limit": 5, "mode": "override"},
            "disabled_when_limited": True, "disconnect_users_when_limited": True, "disconnect_users_when_disabled": False}


class Fake:
    """The role routes of a PasarGuard 5.4 panel plus the admins, groups and templates the page reads beside them."""

    def __init__(self):
        self.roles = {1: owner_role(), 3: operator_role()}
        self.next = 4
        self.admins = [{"id": 1, "username": "boss", "role": {"id": 1, "name": "owner", "is_owner": True}}, {"id": 2, "username": "ann", "role": {"id": 3, "name": "operator"}}]
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail: dict[tuple[str, str], PanelError] = {}

    def __call__(self, c, method, path, **kw):
        u = urlsplit(path)
        body = kw.get("body")
        self.calls.append((method, u.path, copy.deepcopy(body)))

        if (method, u.path) in self.fail:
            raise self.fail[(method, u.path)]

        if u.path == "/api/admin-roles" and method == "GET":
            return {"roles": copy.deepcopy(list(self.roles.values())), "total": len(self.roles)}

        if u.path == "/api/admin-role" and method == "POST":
            if any(r["name"] == body["name"] for r in self.roles.values()):
                raise PanelError("HTTP 409: Role with this name already exists", 409)

            rid, self.next = self.next, self.next + 1
            role = {**owner_role(), "id": rid, "is_owner": False, "permissions": {}, "limits": {}, **copy.deepcopy(body), "created_at": "2026-02-02T00:00:00Z"}
            self.roles[rid] = role
            return copy.deepcopy(role)

        m = re.fullmatch(r"/api/admin-role/(\d+)", u.path)

        if m:
            rid = int(m.group(1))

            if rid not in self.roles:
                raise PanelError("HTTP 404: Role not found", 404)

            if method == "GET":
                return copy.deepcopy(self.roles[rid])

            if method == "PUT":
                if self.roles[rid]["is_owner"]:
                    raise PanelError("HTTP 403: owner role cannot be modified", 403)

                if "name" in body and any(r["name"] == body["name"] and r["id"] != rid for r in self.roles.values()):
                    raise PanelError("HTTP 409: Role with this name already exists", 409)

                self.roles[rid].update(copy.deepcopy(body))
                return copy.deepcopy(self.roles[rid])

            if method == "DELETE":
                if rid <= 3:
                    raise PanelError("HTTP 403: Cannot delete built-in role", 403)

                del self.roles[rid]
                return None

        if u.path == "/api/admins":
            return {"admins": copy.deepcopy(self.admins), "total": len(self.admins), "active": len(self.admins), "disabled": 0, "limited": 0}

        if u.path == "/api/admin":
            return {"id": 1, "username": "boss", "role": {"id": 1, "name": "owner", "is_owner": True}}

        if u.path == "/api/groups/simple":
            return {"groups": [{"id": 1, "name": "Main"}, {"id": 2, "name": "Iran"}], "total": 2}

        if u.path == "/api/user_templates/simple":
            return {"templates": [{"id": 1, "name": "30 days"}, {"id": 2, "name": "90 days"}], "total": 2}

        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self):
        return [x for x in self.calls if x[0] != "GET"]


@pytest.fixture(autouse=True)
def clean_backups():
    d = get_settings().data_dir / "pg-backups"

    if d.exists():
        for f in d.glob("panel4-role-*"):
            f.unlink()


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    return f


# ------------------------------------------------------------------ pure
def test_a_role_is_shown_in_the_units_of_the_admins_tab():
    row = pg_roles.role_row(operator_role(), ["ann"])
    assert row["id"] == 3 and row["builtin"] is True and row["is_owner"] is False and row["admins"] == ["ann"]
    assert row["permissions"]["users"]["create"] == "yes" and row["permissions"]["users"]["read"] == "own" and row["permissions"]["users"]["update"] == "all" and row["permissions"]["groups"]["read"] == "no"
    assert row["granted"] == 5 and row["limits"]["max_users"] == 50 and row["limits"]["data_limit_max_gb"] == 100 and row["limits"]["expire_max_days"] == 90 and row["limits"]["data_limit_min_gb"] is None
    assert row["features"] == {"can_use_reset_strategy": False, "can_use_next_plan": True}
    assert row["access"] == {"require_template": True, "allowed_template_ids": [1, 2], "allowed_group_ids": None}
    assert row["hwid"]["mode"] == "override" and row["hwid"]["fallback_limit"] == 2 and row["hwid"]["max_limit"] == 5 and row["hwid"]["min_limit"] is None
    assert row["disabled_when_limited"] is True and row["disconnect_users_when_limited"] is True and row["disconnect_users_when_disabled"] is False


def test_a_role_row_never_crashes_on_a_shape_it_does_not_know():
    for junk in (None, {}, [], "x", {"id": "7", "permissions": "x", "limits": [], "access": 5, "hwid": None}):
        row = pg_roles.role_row(junk)
        assert row["name"] == "" and row["granted"] == 0 and row["features"] == {"can_use_reset_strategy": True, "can_use_next_plan": True} and row["admins"] == []
    assert pg_roles.role_row({"id": True})["builtin"] is False and pg_roles.role_row({"id": 9})["builtin"] is False


def test_the_page_speaks_gb_and_days_the_panel_bytes_and_seconds():
    body = pg_roles.convert({"name": " Seller ", "permissions": {"users": {"read": "own", "create": "yes"}, "nodes": {"read": "yes", "logs": "no"}},
                             "limits": {"max_users": 10, "data_limit_max_gb": 2.5, "expire_max_days": 30, "on_hold_timeout_max_days": ""}, "features": {"can_use_next_plan": False},
                             "access": {"require_template": True, "allowed_template_ids": [3, 3, 1], "allowed_group_ids": None}, "hwid": {"mode": "override", "fallback_limit": "3", "max_limit": 5},
                             "disabled_when_limited": 1})
    assert body["name"] == "Seller" and body["permissions"] == {"users": {"read": {"scope": 1}, "create": True}, "nodes": {"read": True}}
    assert body["limits"]["max_users"] == 10 and body["limits"]["data_limit_max"] == int(2.5 * GB) and body["limits"]["expire_max"] == 30 * DAY and body["limits"]["on_hold_timeout_max"] is None
    assert body["features"] == {"can_use_reset_strategy": True, "can_use_next_plan": False} and body["access"] == {"require_template": True, "allowed_template_ids": [1, 3], "allowed_group_ids": None}
    assert body["hwid"] == {"enabled": True, "forced": False, "require_hwid_for_manual_sub": False, "mode": "override", "fallback_limit": 3, "min_limit": None, "max_limit": 5} and body["disabled_when_limited"] is True
    assert pg_roles.convert({"name": "x"}) == {"name": "x"} and pg_roles.convert({}) == {}                                   # a section that is not given is not sent


def test_a_row_of_the_panel_goes_round_to_the_same_body():
    row = pg_roles.role_row(operator_role())
    again = pg_roles.convert(row)
    assert again["limits"]["data_limit_max"] == 100 * GB and again["limits"]["expire_max"] == 90 * DAY and again["permissions"]["users"] == {"create": True, "read": {"scope": 1}, "update": {"scope": 2}}
    assert pg_roles.convert(pg_roles.role_row({**operator_role(), "name": "operator"})) == again


def test_what_cannot_be_a_role_is_refused_in_plain_words():
    bad = [({"name": ""}, "name of 1 to 64"), ({"name": "x" * 65}, "name of 1 to 64"), ({"permissions": {"fly": {"x": "yes"}}}, "no resource fly"), ({"permissions": {"users": {"fly": "yes"}}}, "no action fly"),
           ({"permissions": {"nodes": {"read": "own"}}}, "no or yes"), ({"permissions": 5}, "object is expected"), ({"limits": {"max_users": -1}}, "between"), ({"limits": {"data_limit_max_gb": "lots"}}, "number"),
           ({"access": {"allowed_group_ids": [0]}}, "list of ids"), ({"access": {"allowed_template_ids": "x"}}, "list of ids"), ({"hwid": {"mode": "maybe"}}, "device rule"),
           ({"hwid": {"min_limit": 9, "max_limit": 2}}, "fewest devices"), ({"hwid": {"fallback_limit": 99999}}, "between")]

    for data, why in bad:
        with pytest.raises(ValueError, match=why):
            pg_roles.convert(data)


# ------------------------------------------------------------------ reading
def test_the_list_has_every_role_with_its_members_and_what_a_form_needs(fake):
    j = pg_roles.roles_list(C)
    assert [r["name"] for r in j["roles"]] == ["owner", "operator"] and j["total"] == 2 and j["roles"][1]["admins"] == ["ann"] and j["roles"][0]["admins"] == ["boss"]
    assert j["me"] == {"username": "boss", "role_id": 1, "is_owner": True} and [g["name"] for g in j["groups"]] == ["Main", "Iran"] and [t["name"] for t in j["templates"]] == ["30 days", "90 days"]
    assert any(r["key"] == "users" and {"key": "read", "kind": "scope"} in r["actions"] for r in j["permission_schema"]) and fake.writes() == []


def test_the_list_survives_admins_groups_and_templates_that_cannot_be_read(fake):
    for path in ("/api/admins", "/api/groups/simple", "/api/user_templates/simple"):
        fake.fail[("GET", path)] = PanelError("HTTP 403: not allowed", 403)
    j = pg_roles.roles_list(C)
    assert len(j["roles"]) == 2 and j["roles"][1]["admins"] == [] and j["groups"] == [] and j["templates"] == []


# ------------------------------------------------------------------ writing
def test_a_role_is_made_with_the_sections_given(fake):
    row = pg_roles.role_create(C, {"name": "Seller", "permissions": {"users": {"read": "own"}}, "limits": {"max_users": 5}})
    assert row["id"] == 4 and row["name"] == "Seller" and row["permissions"]["users"]["read"] == "own" and row["limits"]["max_users"] == 5 and row["is_owner"] is False
    post = fake.writes()[-1]
    assert post[:2] == ("POST", "/api/admin-role") and set(post[2]) == {"name", "permissions", "limits"} and post[2]["permissions"] == {"users": {"read": {"scope": 1}}}
    with pytest.raises(ValueError, match="needs a name"):
        pg_roles.role_create(C, {"limits": {}})
    with pytest.raises(PanelError, match="already exists"):
        pg_roles.role_create(C, {"name": "Seller"})


def test_a_change_sends_only_the_sections_that_differ_and_saves_the_role_first(fake):
    before = copy.deepcopy(fake.roles[3])
    data = pg_roles.role_row(before)
    data["limits"]["max_users"] = 80
    data["name"] = "operator"
    out = pg_roles.role_update(C, 3, data)
    assert out["changed"] == ["limits"] and out["role"]["limits"]["max_users"] == 80 and fake.roles[3]["limits"]["max_users"] == 80 and fake.roles[3]["permissions"] == before["permissions"]
    put = fake.writes()[-1]
    assert put[:2] == ("PUT", "/api/admin-role/3") and list(put[2]) == ["limits"] and put[2]["limits"]["data_limit_max"] == 100 * GB                      # the whole section, not one field
    saved = pg_roles.role_backups(C)
    assert [b["role_id"] for b in saved] == [3] and saved[0]["name"] == "operator" and saved[0]["why"] == "before a change"
    doc = json.loads((get_settings().data_dir / "pg-backups" / saved[0]["file"]).read_text())
    assert doc["role"]["limits"]["max_users"] == 50                                                                                                       # the file holds the role as it WAS
    with pytest.raises(ValueError, match="nothing to change"):
        pg_roles.role_update(C, 3, pg_roles.role_row(fake.roles[3]))


def test_the_owner_role_is_never_written(fake):
    with pytest.raises(ValueError, match="owner role cannot be changed"):
        pg_roles.role_update(C, 1, {"name": "boss"})
    with pytest.raises(ValueError, match="owner role cannot be deleted"):
        pg_roles.role_delete(C, 1, True)
    assert fake.writes() == []


def test_a_role_that_does_not_exist_says_so(fake):
    with pytest.raises(PanelError, match="not found"):
        pg_roles.role_update(C, 99, {"name": "x"})


def test_a_delete_names_the_admins_that_still_have_the_role_and_needs_the_ok(fake):
    r = pg_roles.role_create(C, {"name": "Temp"})
    fake.admins.append({"id": 3, "username": "bob", "role": {"id": r["id"], "name": "Temp"}})
    with pytest.raises(ValueError, match=r"1 admin has the role “Temp” \(bob\)"):
        pg_roles.role_delete(C, r["id"], True)
    fake.admins.pop()
    with pytest.raises(ValueError, match="confirm: true"):
        pg_roles.role_delete(C, r["id"])
    out = pg_roles.role_delete(C, r["id"], True)
    assert out["deleted"] is True and r["id"] not in fake.roles and out["backup"].startswith("panel4-role-")
    with pytest.raises(ValueError, match="built-in role"):
        pg_roles.role_delete(C, 3, True)
    assert 3 in fake.roles


def test_a_deleted_role_is_made_again_from_its_file(fake):
    r = pg_roles.role_create(C, {"name": "Temp", "permissions": {"nodes": {"read": "yes"}}, "limits": {"max_users": 7}})
    pg_roles.role_delete(C, r["id"], True)
    bk = pg_roles.role_backups(C)[0]
    assert bk["name"] == "Temp" and bk["why"] == "before a delete"
    out = pg_roles.role_restore(C, bk["file"])
    assert out["made_again"] is True and out["role"]["name"] == "Temp" and out["role"]["limits"]["max_users"] == 7 and out["role"]["permissions"]["nodes"]["read"] == "yes"


def test_a_changed_role_is_put_back_from_its_file(fake):
    pg_roles.role_update(C, 3, {**pg_roles.role_row(fake.roles[3]), "limits": {"max_users": 99}})
    bk = pg_roles.role_backups(C)[0]
    out = pg_roles.role_restore(C, bk["file"])
    assert out["made_again"] is False and fake.roles[3]["limits"]["max_users"] == 50 and out["backup"]                                                  # and the state before the restore is saved too
    assert len(pg_roles.role_backups(C)) == 2


def test_only_a_file_of_this_panel_and_this_kind_is_a_backup(fake):
    for bad in ("../../etc/passwd", "panel9-role-3-20260101-000000.json", "panel4-role-3-20260101-000000.txt", "x", "panel4-role-3-20260101-000000.json"):
        with pytest.raises(ValueError, match="unknown backup"):
            pg_roles.role_restore(C, bad)


def test_a_restore_refuses_a_file_of_the_owner_role(fake):
    d = get_settings().data_dir / "pg-backups"
    d.mkdir(parents=True, exist_ok=True)
    (d / "panel4-role-1-20260101-000000.json").write_text(json.dumps({"panel": 4, "role": owner_role()}))
    with pytest.raises(ValueError, match="cannot be put back"):
        pg_roles.role_restore(C, "panel4-role-1-20260101-000000.json")


# ------------------------------------------------------------------ the API
@pytest.fixture
def pid():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="boss", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_the_api_lists_makes_changes_and_deletes_roles(client, fake, pid):
    base = f"/api/panels/{pid}/pg/roles"
    j = client.get(base).json()
    assert j["ok"] is True and [r["name"] for r in j["roles"]] == ["owner", "operator"]
    made = client.post(base, json={"name": "Seller", "limits": {"max_users": 5}}).json()
    rid = made["role"]["id"]
    assert made["ok"] is True and made["role"]["limits"]["max_users"] == 5
    with session() as db:
        assert any("role “Seller” made" in e.text and e.text.startswith("PasarGuard alpha:") for e in db.query(Event).all())                                  # one sentence in the events: it was done from here
    assert client.post(base, json={"name": "Seller"}).status_code == 409                                                   # the panel's own 409
    ch = client.put(f"{base}/{rid}", json={"limits": {"max_users": 9}}).json()
    assert ch["ok"] is True and ch["changed"] == ["limits"] and ch["role"]["limits"]["max_users"] == 9
    assert client.put(f"{base}/{rid}", json={"limits": {"max_users": 9}}).status_code == 400                               # nothing to change
    assert client.put(f"{base}/1", json={"name": "x"}).status_code == 400 and client.put(f"{base}/99", json={"name": "x"}).status_code == 404
    assert client.delete(f"{base}/{rid}").status_code == 400
    assert client.delete(f"{base}/{rid}?confirm=true").json()["deleted"] is True
    bk = client.get(base + "/backups").json()["backups"]
    assert bk and client.post(f"{base}/restore/{bk[0]['file']}").json()["made_again"] is True
    assert client.post(base, json={"name": ""}).status_code == 422 and client.post(base, json={"permissions": {"fly": {}}, "name": "q"}).status_code == 400


def test_the_api_is_for_signed_in_owners_of_pasarguard_panels_only(client, fake, pid):
    from fastapi.testclient import TestClient

    stranger = TestClient(client.app)
    assert stranger.get(f"/api/panels/{pid}/pg/roles").status_code == 401 and stranger.delete(f"/api/panels/{pid}/pg/roles/5?confirm=true").status_code == 401
    with session() as db:
        mz = Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(mz)
        db.flush()
        mid = mz.id
    assert client.get(f"/api/panels/{mid}/pg/roles").status_code == 400 and client.get("/api/panels/9999/pg/roles").status_code == 404


def test_every_route_of_the_roles_has_a_short_description(client):
    spec = client.app.openapi()
    mine = {(m, p): op for p, ops in spec["paths"].items() if "/pg/roles" in p for m, op in ops.items()}
    assert len(mine) == 6
    assert all(0 < len((op.get("description") or "").split("\n")[0]) <= 300 for op in mine.values())
