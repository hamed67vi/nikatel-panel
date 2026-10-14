"""The client templates of a PasarGuard 5.4 panel: the checks in front of the panel, make / change / default / delete with the files that make them undoable, restore, and the API.
A fake panel stands in for PasarGuard and enforces its real rules (a name is unique per type, the default of a type cannot be switched off, a system template and the last of a type
stay); no test reaches a real panel."""
import copy
import json
import re
from urllib.parse import urlsplit

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Panel
from nikatel.services import panels
from nikatel.services import pg_client_templates as ct
from nikatel.services.panels import PanelError

C = {"id": 6, "type": "pasarguard"}


def seed() -> dict:
    out = {}

    for i, t in enumerate(ct.TYPES, 1):
        out[i] = {"id": i, "name": f"default {t}", "template_type": t, "content": f"CONTENT OF {t}\nsecond line", "is_default": True, "is_system": True}

    return out


class Fake:
    def __init__(self):
        self.t = seed()
        self.next = 10
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail: dict[tuple[str, str], PanelError] = {}

    def __call__(self, c, method, path, **kw):
        u = urlsplit(path)
        body = kw.get("body")
        self.calls.append((method, u.path, copy.deepcopy(body)))

        if (method, u.path) in self.fail:
            raise self.fail[(method, u.path)]

        if u.path == "/api/client_templates" and method == "GET":
            return {"count": len(self.t), "templates": copy.deepcopy(list(self.t.values()))}

        if u.path == "/api/client_template" and method == "POST":
            if any(x["name"] == body["name"] and x["template_type"] == body["template_type"] for x in self.t.values()):
                raise PanelError("HTTP 409: Template with this name already exists for this type", 409)

            if body["template_type"] == "user_agent" and '"list"' not in body["content"]:
                raise PanelError("HTTP 400: Invalid template content: User-Agent template content must contain a 'list' field", 400)

            tid, self.next = self.next, self.next + 1
            self.t[tid] = {"id": tid, "name": body["name"], "template_type": body["template_type"], "content": body["content"], "is_default": body["is_default"], "is_system": False}
            return copy.deepcopy(self.t[tid])

        m = re.fullmatch(r"/api/client_template/(\d+)", u.path)

        if m:
            tid = int(m.group(1))

            if tid not in self.t:
                raise PanelError("HTTP 404: Client template not found", 404)

            if method == "GET":
                return copy.deepcopy(self.t[tid])

            if method == "PUT":
                if body.get("is_default") is False and self.t[tid]["is_default"]:
                    raise PanelError("HTTP 400: Cannot unset default template directly", 400)

                if body.get("is_default"):
                    for x in self.t.values():
                        if x["template_type"] == self.t[tid]["template_type"]:
                            x["is_default"] = x["id"] == tid

                self.t[tid].update({k: v for k, v in body.items() if v is not None and k != "is_default"})
                return copy.deepcopy(self.t[tid])

            if method == "DELETE":
                if self.t[tid]["is_system"]:
                    raise PanelError("HTTP 403: Cannot delete system template", 403)

                del self.t[tid]
                return None

        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self):
        return [x for x in self.calls if x[0] != "GET"]


@pytest.fixture(autouse=True)
def clean_backups():
    d = get_settings().data_dir / "pg-backups"

    if d.exists():
        for f in d.glob("panel6-ctpl-*"):
            f.unlink()


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    return f


UA = json.dumps({"list": ["Mozilla/5.0 test"]})


# ------------------------------------------------------------------ pure
def test_what_a_template_needs_is_checked_before_the_panel_is_asked():
    assert ct.clean_name("  My clash  ") == "My clash" and ct.clean_type("clash_subscription") == "clash_subscription" and ct.clean_content("x") == "x"
    for bad in ("", "   ", None, "n" * 65):
        with pytest.raises(ValueError, match="name of 1 to 64"):
            ct.clean_name(bad)
    for bad in ("", "  \n ", None, 5):
        with pytest.raises(ValueError, match="cannot be empty"):
            ct.clean_content(bad)
    with pytest.raises(ValueError, match="too long"):
        ct.clean_content("x" * (ct.MAX_CONTENT + 1))
    for bad in ("", "clash", None, "xray"):
        with pytest.raises(ValueError, match="type of a template is one of"):
            ct.clean_type(bad)


def test_a_row_leaves_the_content_out_unless_asked():
    t = {"id": 2, "name": "x", "template_type": "xray_subscription", "content": "a\nb\nc", "is_default": True, "is_system": False}
    r = ct.row(t)
    assert r == {"id": 2, "name": "x", "type": "xray_subscription", "label": "Xray (v2ray) JSON", "default": True, "system": False, "size": 5, "lines": 3} and "content" not in r
    assert ct.row(t, True)["content"] == "a\nb\nc"
    assert ct.row(None)["name"] == "" and ct.row({"content": ""})["lines"] == 0                                              # a strange shape gives a row, never a crash


# ------------------------------------------------------------------ reading
def test_the_list_counts_every_type_and_never_carries_a_content(fake):
    j = ct.templates_list(C)
    assert j["total"] == 5 and j["per_type"] == dict.fromkeys(ct.TYPES, 1) and [t["key"] for t in j["types"]] == list(ct.TYPES) and all("content" not in t for t in j["templates"])
    assert ct.template_get(C, 1)["content"].startswith("CONTENT OF clash_subscription")
    with pytest.raises(PanelError, match="not found"):
        ct.template_get(C, 99)
    assert fake.writes() == []


# ------------------------------------------------------------------ writing
def test_a_template_is_made_and_the_panels_own_words_come_back(fake):
    t = ct.template_create(C, " Seller UA ", "user_agent", UA)
    assert t["id"] == 10 and t["name"] == "Seller UA" and t["type"] == "user_agent" and t["default"] is False and t["system"] is False
    post = fake.writes()[-1]
    assert post[:2] == ("POST", "/api/client_template") and post[2] == {"name": "Seller UA", "template_type": "user_agent", "content": UA, "is_default": False}
    with pytest.raises(PanelError, match="already exists"):
        ct.template_create(C, "Seller UA", "user_agent", UA)
    with pytest.raises(PanelError, match="'list' field"):
        ct.template_create(C, "Bad", "user_agent", "{}")                                                                     # the panel checks the content: its sentence is the answer
    with pytest.raises(ValueError, match="cannot be empty"):
        ct.template_create(C, "x", "user_agent", "")
    assert len(fake.writes()) == 3                                                                                           # nothing reached the panel for the empty one


def test_a_change_sends_only_what_differs_and_saves_the_template_first(fake):
    t = ct.template_create(C, "Mine", "user_agent", UA)
    out = ct.template_update(C, t["id"], name="Mine 2", content=json.dumps({"list": ["a", "b"]}))
    assert out["changed"] == ["content", "name"] and out["template"]["name"] == "Mine 2"
    put = fake.writes()[-1]
    assert put[:2] == ("PUT", f"/api/client_template/{t['id']}") and set(put[2]) == {"name", "content"}
    saved = ct.template_backups(C)
    assert saved[0]["name"] == "Mine" and saved[0]["why"] == "before a change" and saved[0]["type"] == "user_agent"                  # the file holds it as it WAS
    with pytest.raises(ValueError, match="nothing to change"):
        ct.template_update(C, t["id"], name="Mine 2")
    with pytest.raises(PanelError, match="not found"):
        ct.template_update(C, 99, name="x")


def test_the_default_of_a_type_moves_and_cannot_be_switched_off(fake):
    a = ct.template_create(C, "Second", "xray_subscription", "{}")
    out = ct.template_update(C, a["id"], is_default=True)
    assert out["changed"] == ["is_default"] and fake.t[a["id"]]["is_default"] is True and fake.t[2]["is_default"] is False
    with pytest.raises(ValueError, match="make another template of this type the default"):
        ct.template_update(C, a["id"], is_default=False)


def test_a_delete_keeps_the_system_templates_and_the_last_template_of_a_type(fake):
    with pytest.raises(ValueError, match="system template"):
        ct.template_delete(C, 1, True)
    fake.t[3]["is_system"] = False                                                                                             # a custom template that is the only one of its type
    with pytest.raises(ValueError, match="last sing-box JSON template"):
        ct.template_delete(C, 3, True)
    assert 3 in fake.t and fake.writes() == []


def test_a_delete_needs_the_ok_and_can_be_undone(fake):
    t = ct.template_create(C, "Temp", "user_agent", UA)
    with pytest.raises(ValueError, match="confirm: true"):
        ct.template_delete(C, t["id"])
    out = ct.template_delete(C, t["id"], True)
    assert out["deleted"] is True and out["was_default"] is False and t["id"] not in fake.t and out["backup"].startswith("panel6-ctpl-")
    bk = ct.template_backups(C)[0]
    assert bk["name"] == "Temp" and bk["why"] == "before a delete"
    again = ct.template_restore(C, bk["file"])
    assert again["made_again"] is True and again["template"]["name"] == "Temp" and again["template"]["type"] == "user_agent" and again["template"]["default"] is False
    assert fake.writes()[-1][2]["content"] == UA


def test_a_changed_template_is_put_back_from_its_file(fake):
    t = ct.template_create(C, "Mine", "user_agent", UA)
    ct.template_update(C, t["id"], content=json.dumps({"list": ["x"]}))
    bk = ct.template_backups(C)[0]
    out = ct.template_restore(C, bk["file"])
    assert out["made_again"] is False and fake.t[t["id"]]["content"] == UA and out["backup"] and len(ct.template_backups(C)) == 2


def test_only_a_file_of_this_panel_and_this_kind_is_a_backup(fake):
    for bad in ("../x", "panel9-ctpl-1-20260101-000000.json", "panel6-ctpl-1-20260101-000000.txt", "panel6-ctpl-1-20260101-000000.json", "x"):
        with pytest.raises(ValueError, match="unknown backup"):
            ct.template_restore(C, bad)
    d = get_settings().data_dir / "pg-backups"
    d.mkdir(parents=True, exist_ok=True)
    (d / "panel6-ctpl-1-20260101-000000.json").write_text(json.dumps({"template": {"id": 1, "template_type": "bogus", "name": "x", "content": "y"}}))
    with pytest.raises(ValueError, match="cannot be put back"):
        ct.template_restore(C, "panel6-ctpl-1-20260101-000000.json")


# ------------------------------------------------------------------ the API
@pytest.fixture
def pid():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="boss", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_the_api_lists_makes_changes_and_deletes(client, fake, pid):
    base = f"/api/panels/{pid}/pg/client-templates"
    j = client.get(base).json()
    assert j["ok"] is True and j["total"] == 5
    made = client.post(base, json={"name": "Mine", "template_type": "user_agent", "content": UA}).json()
    tid = made["template"]["id"]
    assert made["ok"] is True and client.get(f"{base}/{tid}").json()["template"]["content"] == UA
    with session() as db:
        assert any("client template “Mine”" in e.text and e.text.startswith("PasarGuard alpha:") for e in db.query(Event).all())
    assert client.post(base, json={"name": "Mine", "template_type": "user_agent", "content": UA}).status_code == 409
    assert client.post(base, json={"name": "Bad", "template_type": "user_agent", "content": "{}"}).status_code == 400            # the panel's 400 stays a 400
    assert client.post(base, json={"name": "x", "template_type": "nope", "content": "y"}).status_code == 400
    assert client.post(base, json={"name": "", "template_type": "user_agent", "content": "y"}).status_code == 422
    ch = client.put(f"{base}/{tid}", json={"content": json.dumps({"list": ["z"]})}).json()
    assert ch["changed"] == ["content"] and client.put(f"{base}/{tid}", json={"name": "Mine"}).status_code == 400
    assert client.put(f"{base}/99", json={"name": "q"}).status_code == 404
    assert client.delete(f"{base}/{tid}").status_code == 400 and client.delete(f"{base}/1?confirm=true").status_code == 400
    assert client.delete(f"{base}/{tid}?confirm=true").json()["deleted"] is True
    bk = client.get(base + "/backups").json()["backups"]
    assert bk and client.post(f"{base}/restore/{bk[0]['file']}").json()["made_again"] is True


def test_the_api_is_for_signed_in_owners_of_pasarguard_panels_only(client, fake, pid):
    from fastapi.testclient import TestClient

    stranger = TestClient(client.app)
    assert stranger.get(f"/api/panels/{pid}/pg/client-templates").status_code == 401
    with session() as db:
        mz = Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(mz)
        db.flush()
        mid = mz.id
    assert client.get(f"/api/panels/{mid}/pg/client-templates").status_code == 400 and client.get("/api/panels/9999/pg/client-templates").status_code == 404


def test_every_route_has_a_short_description(client):
    spec = client.app.openapi()
    mine = {(m, p): op for p, ops in spec["paths"].items() if "/pg/client-templates" in p for m, op in ops.items()}
    assert len(mine) == 7
    assert all(0 < len((op.get("description") or "").split("\n")[0]) <= 300 for op in mine.values())
