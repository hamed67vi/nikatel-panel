"""The panel side of the Bale relay (services/bale.py, api/bale.py, the reviewed actions in ssh.py): install over SSH, config, status, notices, removal. SSH is a fake that checks every action
against the real rules of ssh.py, so a path or a value the real action would refuse fails here too."""
import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from nikatel import ssh
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Panel, Server
from nikatel.services import bale
from nikatel.services import settings as st

TOKEN = "1234567:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ"


class FakeSSH:
    def __init__(self):
        self.actions, self.files, self.status_out = [], {}, ""
        self.prepare = "RELAY_READY Python 3.11.2"
        self.unit = "active"
        self.fail_put = ""

    def run_action(self, node, name, params=None, timeout=90):
        ssh.render_action(name, params or {})                      # the real rules: a bad value or path raises here as it would on the server
        self.actions.append((name, dict(params or {}), node.host))

        if name == "relay_prepare":
            return self.prepare

        if name == "put_file":
            if self.fail_put and self.fail_put in params["PATH"]:
                raise ssh.SSHError("connection lost")

            self.files[params["PATH"]] = (base64.b64decode(params["B64"]), params["MODE"])
            return "WROTE " + params["PATH"]

        if name == "unit_enable_restart":
            return f"UNIT {params['UNIT']} {self.unit}"

        if name == "relay_remove":
            return "REMOVED"

        raise AssertionError(name)

    def run_script(self, node, name, params=None, timeout=40):
        ssh._script_text(name, params or {})
        assert name == "relay_status"
        return self.status_out

    def names(self):
        return [a[0] for a in self.actions]


@pytest.fixture
def fx(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    f = FakeSSH()
    monkeypatch.setattr(bale, "run_action", f.run_action)
    monkeypatch.setattr(bale, "run_script", f.run_script)
    monkeypatch.setattr(bale.time, "sleep", lambda s: None)

    with session() as db:
        ir = Server(name="ir1", host="203.0.113.7", role="iran", ssh_port=2222)
        fr1, fr2 = Server(name="fr1", host="198.51.100.7", role="foreign", ssh_port=22), Server(name="fr2", host="198.51.100.8", role="foreign", ssh_port=2200)
        off = Server(name="fr-off", host="198.51.100.9", role="foreign", enabled=False)
        db.add_all([ir, fr1, fr2, off])
        db.flush()
        f.iran = ir.id

    return f


def ready(fx, **kw):
    bale.save({"token": TOKEN, "hosts": ["Sub.Example.test", "panel.example.test:2096"], "admins": ["900"], **kw})


STATUS_OK = """UNIT active
ENABLED enabled
PY Python 3.11.2
DNS 198.51.100.3
STATUS
{"v": 1, "version": "1.0.0", "at": %d, "bot": {"id": 7, "username": "nika_bot"}, "bale": {"ok": true, "last_ok": 1, "err": "", "ips": ["198.51.100.3"]}, "users": {"total": 12, "muted": 1, "blocked": 2, "linked": 5}, "watch": {"up": true, "down_since": 0, "notified": false, "last_cut_at": 0, "targets": {}}, "broadcasts": [], "queue": 0}
END
"""


# ------------------------------------------------------------------ the reviewed actions
def test_the_new_paths_and_values_are_accepted_and_nothing_else_is():
    ok = lambda p: ssh.render_action("put_file", {"PATH": p, "B64": "QQ==", "MODE": "0600"})

    for p in ("/opt/nikatel-relay/config.json", "/opt/nikatel-relay/bale_relay.py", "/opt/nikatel-relay/outbox/1730000000-ab12cd.msg"):
        assert "WROTE" in ok(p)

    for p in ("/opt/nikatel-relay/data/relay.db", "/opt/nikatel-relayx/a", "/opt/nikatel-relay/outbox/a/b.msg", "/opt/nikatel-relay/a b", "/opt/other/config.json"):
        with pytest.raises(ValueError):
            ok(p)

    assert "RELAY_READY" in ssh.render_action("relay_prepare", {})
    assert "rm -rf /opt/nikatel-relay" in ssh.render_action("relay_remove", {"PURGE": "1"})

    for bad in ("2", "", "yes", "1; rm -rf /"):
        with pytest.raises(ValueError):
            ssh.render_action("relay_remove", {"PURGE": bad})

    assert "status.json" in ssh._script_text("relay_status", {})


def test_the_status_read_only_reads():
    s = ssh._script_text("relay_status", {})
    assert not any(w in s for w in (" rm ", "mv ", "tee ", "chmod", "systemctl restart", "systemctl start", "kill"))
    assert "relay_status" in ssh.SCRIPTS and "relay_status" not in ssh.ACTIONS and {"relay_prepare", "relay_remove"} <= set(ssh.ACTIONS)


# ------------------------------------------------------------------ the settings
def test_the_settings_are_checked_and_the_token_never_comes_back(fx):
    out = bale.save({"token": TOKEN, "admins": ["900", " 901 "], "hosts": ["A.example.test", "a.example.test", "b.example.test:2096", ""], "cut_after": 240, "restore_after": 60, "auto_notice": False})
    assert out["admins"] == [900, 901] and out["hosts"] == ["a.example.test", "b.example.test:2096"] and out["cut_after"] == 240 and out["auto_notice"] is False
    assert out["token"] == "" and out["has_token"] is True and TOKEN not in json.dumps(out)
    assert out["installed"] is False and out["pushed"] is False and out["defaults"]["cut"]

    bale.save({"hosts": ["only.example.test"]})                      # a save without a token keeps the saved one
    assert st.get("bale")["token"] == TOKEN and st.get("bale")["hosts"] == ["only.example.test"]


@pytest.mark.parametrize("changes, word", [
    ({"token": "not-a-token"}, "token"), ({"token": "123:short"}, "token"),
    ({"admins": ["abc"]}, "admin"), ({"admins": ["12"]}, "admin"),
    ({"hosts": ["bad host"]}, "host"), ({"hosts": ["https://a.example.test"]}, "host"), ({"hosts": ["a.example.test:99999999"]}, "host"),
    ({"cut_after": 5}, "cut_after"), ({"cut_after": 99999}, "cut_after"), ({"cut_after": "x"}, "cut_after"), ({"restore_after": 1}, "restore_after"),
    ({"texts": {"nope": "x"}}, "unknown"), ({"texts": {"cut": "x" * 1501}}, "1500"),
])
def test_a_bad_setting_is_refused_with_its_reason_and_nothing_is_saved(fx, changes, word):
    before = st.get("bale")

    with pytest.raises(ValueError, match=word):
        bale.save(changes)

    assert st.get("bale") == before


def test_a_text_equal_to_the_default_or_empty_is_not_kept_as_an_override(fx):
    d = bale.relay_module().DEFAULT_TEXTS
    bale.save({"texts": {"cut": "متن من", "restored": d["restored"], "welcome": "  "}})
    assert st.get("bale")["texts"] == {"cut": "متن من"}
    assert bale.config_doc()["texts"] == {"cut": "متن من"}                   # only what differs travels to the server
    bale.save({"texts": {}})
    assert st.get("bale")["texts"] == {}


def test_the_hosts_of_the_known_panels_are_the_first_guess(fx):
    with session() as db:
        db.add_all([Panel(name="p1", type="pasarguard", url="https://Panel.Example.test:8443/dashboard"), Panel(name="p2", type="marzban", url="https://panel.example.test:9000"),
                    Panel(name="p3", type="marzban", url="http://other.example.test", enabled=False), Panel(name="p4", type="pasarguard", url="https://sub.example.test")])

    assert bale.suggest_hosts() == ["panel.example.test", "sub.example.test"]
    assert bale.public()["suggest_hosts"] == ["panel.example.test", "sub.example.test"]


def test_the_server_watches_the_owners_foreign_servers_first_then_three_public_addresses(fx):
    doc = bale.config_doc()
    assert doc["watch"] == [["198.51.100.7", 22], ["198.51.100.8", 2200], ["1.1.1.1", 443], ["8.8.8.8", 443], ["9.9.9.9", 443]]          # a server that is switched off is not a witness


# ------------------------------------------------------------------ install
def test_install_puts_the_program_the_unit_and_the_config_and_starts_it(fx):
    ready(fx)
    fx.status_out = STATUS_OK % int(time.time())
    out = bale.install(fx.iran)

    assert out == {"server": "ir1", "unit": "active", "bot": "nika_bot", "bale_error": ""}
    assert fx.names() == ["relay_prepare", "put_file", "put_file", "put_file", "unit_enable_restart"]
    assert all(a[2] == "203.0.113.7" for a in fx.actions)

    prog, mode = fx.files["/opt/nikatel-relay/bale_relay.py"]
    assert prog == bale.SRC.read_bytes() and mode == "0644"
    unit, _ = fx.files["/etc/systemd/system/nikatel-relay.service"]
    assert b"ExecStart=/usr/bin/python3 /opt/nikatel-relay/bale_relay.py run" in unit and b"ReadWritePaths=/opt/nikatel-relay" in unit and b"NoNewPrivileges=yes" in unit and b"Restart=always" in unit

    cfg_raw, cmode = fx.files["/opt/nikatel-relay/config.json"]
    cfg = json.loads(cfg_raw)
    assert cmode == "0600"                                                # the bot token is in it
    assert cfg["token"] == TOKEN and cfg["admins"] == [900] and cfg["allowed_hosts"] == ["sub.example.test", "panel.example.test:2096"] and cfg["auto_notice"] is True
    assert cfg["watch"][0] == ["198.51.100.7", 22] and cfg["cut_after"] == 180 and cfg["restore_after"] == 120

    s = st.get("bale")
    assert s["server_id"] == fx.iran and s["installed_at"] > 0 and s["version"] == bale.relay_module().VERSION
    assert bale.public()["installed"] is True

    with session() as db:
        assert any(e.domain == "bale" and "ir1" in e.text for e in db.query(Event))


def test_the_relay_that_cannot_reach_bale_is_reported_not_hidden(fx):
    ready(fx)
    fx.status_out = STATUS_OK.replace('"err": ""', '"err": "[Errno 101] Network is unreachable"').replace('"bot": {"id": 7, "username": "nika_bot"}', '"bot": {}') % int(time.time())
    out = bale.install(fx.iran)
    assert out["bot"] == "" and "unreachable" in out["bale_error"] and out["unit"] == "active"


def test_install_is_refused_with_a_reason_before_anything_is_changed(fx, monkeypatch):
    with pytest.raises(ValueError, match="token"):
        bale.install(fx.iran)

    ready(fx)

    with session() as db:
        fid = db.query(Server).filter_by(name="fr1").one().id

    with pytest.raises(ValueError, match="Iran server"):
        bale.install(fid)

    with pytest.raises(ValueError, match="Iran server"):
        bale.install(99999)

    monkeypatch.setattr(get_settings(), "read_only", True)

    with pytest.raises(ValueError, match="read-only"):
        bale.install(fx.iran)

    assert fx.actions == [] and st.get("bale")["installed_at"] == 0


def test_a_server_without_python_or_a_unit_that_does_not_start_stops_the_install(fx):
    ready(fx)
    fx.prepare = "RELAY_FAIL this server has no python3"

    with pytest.raises(RuntimeError, match="no python3"):
        bale.install(fx.iran)

    assert fx.names() == ["relay_prepare"] and st.get("bale")["installed_at"] == 0

    fx.prepare, fx.unit, fx.actions = "RELAY_READY Python 3.11", "failed", []

    with pytest.raises(RuntimeError, match="did not start"):
        bale.install(fx.iran)

    assert st.get("bale")["installed_at"] == 0                    # not marked as installed


# ------------------------------------------------------------------ after the install
def installed(fx):
    ready(fx)
    fx.status_out = STATUS_OK % int(time.time())
    bale.install(fx.iran)
    fx.actions.clear()


def test_a_saved_change_is_written_to_the_server_without_a_restart(fx):
    installed(fx)
    out = bale.save({"texts": {"cut": "قطعی تازه"}, "cut_after": 300})
    assert out["pushed"] is True and out["push_error"] == "" and fx.names() == ["put_file"]
    cfg = json.loads(fx.files["/opt/nikatel-relay/config.json"][0])
    assert cfg["texts"] == {"cut": "قطعی تازه"} and cfg["cut_after"] == 300 and cfg["token"] == TOKEN


def test_a_save_that_cannot_reach_the_server_is_kept_and_says_so(fx):
    installed(fx)
    fx.fail_put = "config.json"
    out = bale.save({"cut_after": 400})
    assert out["pushed"] is False and "connection lost" in out["push_error"] and st.get("bale")["cut_after"] == 400


def test_the_status_is_read_from_the_server_and_dated(fx):
    assert bale.status() == {"installed": False}
    installed(fx)
    fx.status_out = STATUS_OK % (int(time.time()) - 30)
    d = bale.status()
    assert d["installed"] is True and d["server"] == "ir1" and d["unit"] == "active" and d["dns"] == "198.51.100.3"
    assert d["status"]["users"] == {"total": 12, "muted": 1, "blocked": 2, "linked": 5} and 25 <= d["age"] <= 60

    fx.status_out = "UNIT none\nENABLED no\nPY Python 3.11.2\nDNS\nSTATUS\n\nEND\n"                # the relay is gone or never wrote its file
    d = bale.status()
    assert d["unit"] == "none" and d["status"] == {} and d["age"] is None

    fx.status_out = "UNIT active\nSTATUS\n{broken\nEND\n"
    assert bale.status()["status"] == {}


def test_a_notice_is_put_in_the_outbox_of_the_relay(fx):
    installed(fx)
    out = bale.broadcast("  سرویس تا ساعتی دیگر وصل می‌شود  ")
    assert out["to"] == "all" and fx.names() == ["put_file"]
    (path, (raw, mode)), = [(p, v) for p, v in fx.files.items() if "/outbox/" in p]
    assert path == f"/opt/nikatel-relay/outbox/{out['id']}.msg" and mode == "0600"
    doc = json.loads(raw)
    assert doc["text"] == "سرویس تا ساعتی دیگر وصل می‌شود" and doc["kind"] == "manual" and "to" not in doc and doc["id"] == out["id"]

    with session() as db:
        assert any("queued for every customer" in e.text for e in db.query(Event))


def test_a_test_notice_goes_to_the_admins_only(fx):
    installed(fx)
    out = bale.broadcast("آزمایش", admins_only=True)
    doc = json.loads(next(v[0] for p, v in fx.files.items() if "/outbox/" in p))
    assert out["to"] == "admins" and doc["to"] == [900] and doc["kind"] == "test"

    st.put("bale", {"admins": []})

    with pytest.raises(ValueError, match="admins"):
        bale.broadcast("آزمایش", admins_only=True)


@pytest.mark.parametrize("text", ["", "   ", "x" * 4001])
def test_an_empty_or_huge_notice_is_refused(fx, text):
    installed(fx)

    with pytest.raises(ValueError, match="1 to 4000"):
        bale.broadcast(text)


def test_a_notice_needs_the_relay_to_be_installed_and_the_panel_to_be_writable(fx, monkeypatch):
    with pytest.raises(ValueError, match="not installed"):
        bale.broadcast("hi")

    installed(fx)
    monkeypatch.setattr(get_settings(), "read_only", True)

    with pytest.raises(ValueError, match="read-only"):
        bale.broadcast("hi")

    with pytest.raises(ValueError, match="read-only"):
        bale.remove()

    assert bale.status()["installed"] is True                                  # looking is allowed


def test_remove_stops_the_relay_and_keeps_the_customers_unless_asked(fx):
    installed(fx)
    out = bale.remove()
    assert out == {"server": "ir1", "purged": False} and fx.actions[-1][:2] == ("relay_remove", {"PURGE": "0"})
    assert st.get("bale")["installed_at"] == 0 and st.get("bale")["token"] == TOKEN and bale.public()["installed"] is False

    with pytest.raises(ValueError, match="not installed"):
        bale.remove()

    installed(fx)
    assert bale.remove(purge=True)["purged"] is True and fx.actions[-1][1] == {"PURGE": "1"}


# ------------------------------------------------------------------ the API
def test_the_api_hides_the_token_and_checks_what_it_is_given(client, fx):
    r = client.put("/api/bale", json={"token": TOKEN, "admins": ["900"], "hosts": ["sub.example.test"]})
    assert r.status_code == 200 and r.json()["has_token"] is True and TOKEN not in r.text
    assert client.get("/api/bale").json()["hosts"] == ["sub.example.test"] and TOKEN not in client.get("/api/bale").text

    for body in ({"token": "x"}, {"admins": ["x"]}, {"hosts": ["a b"]}, {"cut_after": 1}, {"texts": {"zzz": "x"}}):
        r = client.put("/api/bale", json=body)
        assert r.status_code == 400 and r.json()["error"], body

    assert client.put("/api/bale", json={"admins": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"]}).status_code == 422          # the model's own bound


def test_the_api_installs_sends_and_removes(client, fx):
    client.put("/api/bale", json={"token": TOKEN, "admins": ["900"], "hosts": ["sub.example.test"]})
    fx.status_out = STATUS_OK % int(time.time())
    r = client.post(f"/api/bale/install?server_id={fx.iran}")
    assert r.status_code == 200 and r.json()["bot"] == "nika_bot"

    s = client.get("/api/bale/status").json()
    assert s["installed"] is True and s["status"]["users"]["total"] == 12

    assert client.post("/api/bale/send", json={"text": "سلام"}).json()["to"] == "all"
    assert client.post("/api/bale/send", json={"text": "سلام", "admins_only": True}).json()["to"] == "admins"
    assert client.post("/api/bale/send", json={"text": ""}).status_code == 422
    assert client.post("/api/bale/remove", json={"purge": False}).json()["purged"] is False


def test_the_api_answers_the_failures_with_a_reason(client, fx, monkeypatch):
    assert client.post(f"/api/bale/install?server_id={fx.iran}").status_code == 400                    # no token yet
    assert client.post("/api/bale/send", json={"text": "x"}).status_code == 400                         # not installed
    assert client.get("/api/bale/status").json() == {"ok": True, "installed": False}

    client.put("/api/bale", json={"token": TOKEN})
    fx.prepare = "RELAY_FAIL this server has no python3"
    r = client.post(f"/api/bale/install?server_id={fx.iran}")
    assert r.status_code == 502 and "python3" in r.json()["error"]

    fx.fail_put = "bale_relay.py"
    fx.prepare = "RELAY_READY Python 3.11"
    assert client.post(f"/api/bale/install?server_id={fx.iran}").status_code == 502                    # the ssh link broke


def test_a_stranger_cannot_use_any_of_it(client):
    anon = TestClient(client.app)

    for m, p in (("get", "/api/bale"), ("get", "/api/bale/status"), ("put", "/api/bale"), ("post", "/api/bale/install?server_id=1"), ("post", "/api/bale/send"), ("post", "/api/bale/remove")):
        assert getattr(anon, m)(p).status_code == 401, p


def test_the_relay_source_ships_in_the_repository_and_runs_on_an_old_python():
    import ast

    src = bale.SRC.read_text(encoding="utf-8")
    assert bale.SRC.exists() and "from __future__ import annotations" in src
    tree = ast.parse(src, feature_version=(3, 8))                         # (no syntax newer than 3.8: the Iran servers are not new)
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= {"__future__", "http", "json", "os", "re", "signal", "socket", "sqlite3", "ssl", "sys", "threading", "time", "urllib"}          # the standard library only
def test_the_support_switch_is_saved_and_travels_to_the_server(fx):
    ready(fx)
    assert st.get("bale")["support"] is True and bale.config_doc()["support"] is True and bale.public()["support"] is True          # customers can write to the owner unless he says no
    bale.save({"support": False})
    assert bale.config_doc()["support"] is False and bale.public()["support"] is False
    bale.save({"support": True})
    assert bale.config_doc()["support"] is True


def test_every_text_of_the_relay_can_be_edited_in_the_box():
    """A text the bot sends but the box cannot edit would be a hole: the keys of the relay's defaults and the rows of the box are the same set."""
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "nikatel" / "web" / "bale.js").read_text(encoding="utf-8")
    rows = set(re.findall(r'\["(\w+)", "[^"]+", "[^"]+"\]', js.split("const BALE_TEXTS = ")[1].split("window.baleBoxHtml")[0]))
    assert rows == set(bale.relay_module().DEFAULT_TEXTS)
