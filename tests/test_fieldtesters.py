"""The testers of the Iran test kit (1.69.238, services/fieldtesters.py): the people the kit is sent to in other cities. Each gets a zip of their own - the common files, the list of this minute, their name and city, a token
of their own - made by the panel; a token can be revoked, and what a tester sends is told apart by the name of that token. Nothing here touches a server."""
import io
import json
import shutil
import zipfile

import pytest
from fastapi.testclient import TestClient

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import FieldRun
from nikatel.services import fieldkit, fieldtesters, tokens
from nikatel.services import settings as st

PW = "test-password-1"
URL = "https://panel.example/api/field/ingest"
MANIFEST = {"version": "abc12345", "built_at": 1, "kit_latest": "2.5", "user": {},
            "targets": [{"label": "TCP-3", "file": "TCP-3.json", "host": "h.example", "port": 8680, "net": "tcp", "sec": "tls", "sni": "s.example", "kind": "direct", "deep": True, "config": '{"socks": %%SOCKS%%}'},
                        {"label": "Turkey-1", "file": "Turkey-1.json", "host": "t.example", "port": 11421, "net": "tcp", "sec": "tls", "sni": "s.example", "kind": "entry", "deep": False, "config": '{"socks": %%SOCKS%%}'}],
            "panel": {"names": ["sub-a.example"], "sub_names": ["sub-a.example"], "sub_path": "/sub/x", "ips": []}}


@pytest.fixture(autouse=True)
def _kit_folder(monkeypatch):
    """The common files of the kit are on the server, the panel knows where to send, and the live list is one that needs no panel; the folders are removed after."""
    tokens._LAST_USED.clear()
    d = get_settings().data_dir / "field-kit"
    (d / "xray").mkdir(parents=True, exist_ok=True)
    (d / "kit.ps1").write_text("# the kit")
    (d / "START.bat").write_text("@echo off")
    (d / "xray" / "xray.exe").write_bytes(b"MZ-not-really")
    st.put("field", {"upload_urls": [URL], "testers": []})
    monkeypatch.setattr(fieldkit, "manifest", lambda: MANIFEST)
    yield
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(get_settings().data_dir / "field-kits", ignore_errors=True)
    tokens._LAST_USED.clear()


def _zip(tid):
    p = fieldtesters.zip_file(tid)
    assert p is not None
    return zipfile.ZipFile(io.BytesIO(p.read_bytes()))


def test_the_words_of_a_tester_are_checked():
    assert fieldtesters.check("  Nima   Z ", "Tabriz", "MCI") == ("Nima Z", "Tabriz", "MCI")
    assert fieldtesters.check("Ali", "New York", "") == ("Ali", "New York", "")

    for bad in (("نیما", "Tabriz", ""), ("", "Tabriz", ""), ("Ali", "Tabriz1", ""), ("Ali", "T", ""), ("Ali", "Tabriz", "Hamrah"), ("a" * 31, "Tabriz", "")):
        with pytest.raises(ValueError):
            fieldtesters.check(*bad)

    assert fieldtesters.slug("Nima Z", "Tabriz") == "nima-z-tabriz" and fieldtesters.token_name("nima-tabriz") == "tester-nima-tabriz" and fieldtesters.sender_of("nima-tabriz") == "token:tester-nima-tabriz"


def test_a_tester_gets_a_zip_with_the_common_files_the_list_of_now_their_name_and_a_token_of_their_own():
    t = fieldtesters.add("Nima", "Tabriz", "MCI")
    z = _zip(t["id"])
    names = set(z.namelist())
    root = "nikatel-iran-test/"
    assert {root + n for n in ("kit.ps1", "START.bat", "xray/xray.exe", "targets.json", "panel.json", "upload.json", "who.json", "configs/TCP-3.json", "configs/Turkey-1.json")} <= names
    assert json.loads(z.read(root + "who.json")) == {"name": "Nima", "city": "Tabriz", "sim": "MCI"}
    targets = json.loads(z.read(root + "targets.json"))
    assert [x["label"] for x in targets] == ["TCP-3", "Turkey-1"] and targets[0]["file"] == "TCP-3.json" and "config" not in targets[0] and targets[0]["deep"] is True
    assert z.read(root + "configs/TCP-3.json").decode() == MANIFEST["targets"][0]["config"] and json.loads(z.read(root + "panel.json"))["sub_names"] == ["sub-a.example"]
    up = json.loads(z.read(root + "upload.json"))
    assert up["urls"] == [URL]
    row = tokens.verify(up["token"])
    assert row is not None and row["scope"] == "field" and row["name"] == "tester-nima-tabriz" and row["id"] == t["token_id"]          # (a token that may send a result and read the list, nothing else)
    assert not tokens.allowed("field", "GET", "/api/field/testers") and not tokens.allowed("field", "GET", "/api/field/testers/nima-tabriz/kit")
    assert (get_settings().data_dir / "field-kits" / "nima-tabriz.zip").stat().st_mode & 0o077 == 0          # (the zip holds the token: only the owner of the file may read it)


def test_what_is_missing_is_said_in_a_sentence_and_nothing_is_left_behind():
    shutil.rmtree(get_settings().data_dir / "field-kit" / "xray")
    assert "xray/xray.exe" in fieldtesters.template_problem()

    with pytest.raises(ValueError, match="xray/xray.exe"):
        fieldtesters.add("Ali", "Shiraz")

    assert fieldtesters.view() == [] and not tokens.rows()
    (get_settings().data_dir / "field-kit" / "xray").mkdir()
    (get_settings().data_dir / "field-kit" / "xray" / "xray.exe").write_bytes(b"MZ")
    st.put("field", {"upload_urls": []})
    assert "upload_urls" in fieldtesters.template_problem()
    st.put("field", {"upload_urls": [URL]})
    assert fieldtesters.template_problem() == ""


def test_the_same_person_twice_and_a_panel_that_does_not_answer(monkeypatch):
    fieldtesters.add("Nima", "Tabriz")

    with pytest.raises(ValueError, match="already"):
        fieldtesters.add("nima", "tabriz")

    def down():
        raise ValueError("the panel does not answer")

    monkeypatch.setattr(fieldkit, "manifest", down)

    with pytest.raises(ValueError):
        fieldtesters.add("Sara", "Shiraz")

    assert [x["id"] for x in fieldtesters.view()] == ["nima-tabriz"] and len(tokens.rows()) == 1          # (no token for the one that failed)


def test_a_new_kit_revokes_the_old_token_and_removing_a_tester_revokes_and_deletes_the_zip():
    t = fieldtesters.add("Nima", "Tabriz")
    old = json.loads(_zip(t["id"]).read("nikatel-iran-test/upload.json"))["token"]
    assert tokens.verify(old) is not None
    t2 = fieldtesters.rebuild(t["id"])
    new = json.loads(_zip(t["id"]).read("nikatel-iran-test/upload.json"))["token"]
    assert new != old and tokens.verify(old) is None and tokens.verify(new)["name"] == "tester-nima-tabriz" and t2["token_id"] != t["token_id"]
    fieldtesters.remove(t["id"])
    assert tokens.verify(new) is None and fieldtesters.zip_file(t["id"]) is None and fieldtesters.view() == []

    with pytest.raises(ValueError):
        fieldtesters.remove("nobody")

    with pytest.raises(ValueError):
        fieldtesters.rebuild("nobody")


def test_the_list_shows_what_each_tester_has_sent():
    t = fieldtesters.add("Nima", "Tabriz", "Irancell")
    [v] = fieldtesters.view()
    assert v["runs"] == 0 and v["last_ts"] == 0 and v["kit_ready"] and v["token_live"] and v["sim"] == "Irancell" and "nkt_" not in json.dumps(v)          # (no token value in the list)

    with session() as db:
        for ts, op in ((100, "Irancell"), (300, "MCI")):
            db.add(FieldRun(ts=ts, sender=fieldtesters.sender_of(t["id"]), operator=op, connection="mobile", city="Tabriz", ip="", mode="all", data={}, findings=[]))

        db.add(FieldRun(ts=999, sender="token:iran-kit", operator="TCI", connection="home", city="Tehran", ip="", mode="all", data={}, findings=[]))          # (another sender: not counted)

    [v] = fieldtesters.view()
    assert v["runs"] == 2 and v["last_ts"] == 300 and v["last_net"] == "MCI · mobile"


def test_the_routes_are_for_the_signed_in_owner_only(client):
    r = client.post("/api/field/testers", json={"name": "Nima", "city": "Tabriz", "sim": "MCI"})
    assert r.status_code == 201 and r.json() == {"ok": True, "id": "nima-tabriz"}
    assert client.post("/api/field/testers", json={"name": "Nima", "city": "Tabriz"}).status_code == 400
    assert client.post("/api/field/testers", json={"name": "نیما", "city": "Tabriz"}).status_code == 400
    lst = client.get("/api/field/testers").json()
    assert lst["ok"] and lst["problem"] == "" and [x["id"] for x in lst["testers"]] == ["nima-tabriz"]
    k = client.get("/api/field/testers/nima-tabriz/kit")
    assert k.status_code == 200 and k.headers["content-type"] == "application/zip" and "nikatel-iran-test-nima-tabriz.zip" in k.headers["content-disposition"]
    assert "nikatel-iran-test/who.json" in zipfile.ZipFile(io.BytesIO(k.content)).namelist() and client.get("/api/field/testers/nobody/kit").status_code == 404
    m = TestClient(client.app)
    kit_tok = json.loads(zipfile.ZipFile(io.BytesIO(k.content)).read("nikatel-iran-test/upload.json"))["token"]
    read_tok = client.post("/api/tokens", json={"name": "reader", "scope": "read", "password": PW}).json()["token"]

    for tok in (kit_tok, read_tok):          # (the kit of one tester must not download the kit of another, and a read token never gets the file)
        assert m.get("/api/field/testers", headers={"Authorization": "Bearer " + tok}).status_code == 403
        assert m.get("/api/field/testers/nima-tabriz/kit", headers={"Authorization": "Bearer " + tok}).status_code == 403

    assert m.get("/api/field/testers").status_code == 401
    assert client.post("/api/field/testers/nima-tabriz/rebuild").status_code == 200 and client.post("/api/field/testers/nobody/rebuild").status_code == 400
    assert tokens.verify(kit_tok) is None          # (the rebuild revoked it)
    assert client.delete("/api/field/testers/nima-tabriz").status_code == 200 and client.delete("/api/field/testers/nima-tabriz").status_code == 400
    assert client.get("/api/field/testers").json()["testers"] == []
