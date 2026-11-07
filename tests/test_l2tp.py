"""The L2TP tab (1.69.241, services/l2tp.py): the accounts of the L2TP entry are read and changed over SSH through reviewed scripts. No test touches a server: the two reads and the one action are replaced by a fake entry."""
import base64
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import l2tp, settings
from nikatel.ssh import SSHError

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
NOW = 1_800_000_000

STATE_OUT = """L2TP ok
UNIT strongswan active
UNIT xl2tpd active
UNIT tick active
UDP 5
LOAD 0.42
INFO {"pool": "10.250.0.0/22", "accounts": 4, "online": 1, "version": 3, "features": ["speed", "quota"]}
LIST {"accounts": [{"user": "alice", "limit": 2, "expires": @LATER@, "active": true, "disabled": false, "online": 1, "rx": 1000, "tx": 5000000, "created": 1, "last_seen": 2, "note": "first", "down": 3.0, "up": 3.0, "quota": 50000000000, "used": 5001000, "over": false}, {"user": "l2_7", "limit": 1, "expires": @EARLIER@, "active": false, "disabled": false, "online": 0, "rx": 0, "tx": 0, "created": 1, "last_seen": null, "note": "order 7 user 9", "down": null, "up": null}, {"user": "bob", "limit": 1, "expires": null, "active": false, "disabled": true, "online": 0, "rx": 0, "tx": 0, "created": 1, "last_seen": null, "note": "", "down": 8.0, "up": 2.0}, {"user": "carl", "limit": 1, "expires": @LATER@, "active": false, "disabled": false, "online": 0, "rx": 600000000, "tx": 500000000, "created": 1, "last_seen": null, "note": "", "down": null, "up": null, "quota": 1000000000, "used": 1100000000, "over": true}]}
SESS {"sessions": [{"user": "alice", "pid": 11, "ifname": "ppp0", "start": 1799999000, "remote": "10.250.0.10"}]}
DONE
""".replace("@LATER@", str(NOW + 5 * 86400)).replace("@EARLIER@", str(NOW - 86400))

EXIT_OUT = """EXIT ok
ROUTES 10.250.0.0/22 192.0.2.17;
RULES 11
FORWARD 1
TIMER active
DONE
"""


def _servers(protected=True):
    with session() as db:
        a = Server(name="iran-a", host="192.0.2.10", role="iran")
        b = Server(name="foreign-a", host="192.0.2.20", role="foreign")
        db.add_all([a, b])
        db.flush()
        t = Tunnel(name="fa-ia", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, state="up", rtt_ms=39.2, record={"protected": True} if protected else {})
        db.add(t)
        db.flush()
        return a.id, b.id, t.id


class Fake:
    """A fake entry: the two reads answer with canned text, the action records what it was asked and answers like the manager."""

    def __init__(self, monkeypatch, state_out=STATE_OUT, exit_out=EXIT_OUT):
        self.calls = []
        self.answers = {}
        self.state_out, self.exit_out = state_out, exit_out
        monkeypatch.setattr(l2tp, "run_script", self.script)
        monkeypatch.setattr(l2tp, "run_action", self.action)

    def script(self, node, name, params=None, timeout=40):
        self.calls.append(("script", name, node.host))

        if name != "l2tp_state":
            return self.exit_out

        if isinstance(self.state_out, Exception):
            raise self.state_out

        return self.state_out

    def action(self, node, name, params=None, timeout=90):
        assert name == "l2tp_cmd"
        args = json.loads(base64.b64decode(params["B64"]).decode())
        self.calls.append(("action", params["CMD"], args))
        return self.answers.get(params["CMD"], json.dumps({"ok": True, "user": args[0] if args else ""}))

    def actions(self):
        return [c for c in self.calls if c[0] == "action"]


@pytest.fixture
def ids():
    return _servers()


@pytest.fixture
def ready(ids, monkeypatch):
    a, b, t = ids
    l2tp.set_conf(a, b, t, "Nikatel Shop", "")
    return Fake(monkeypatch)


# ------------------------------------------------------------------ the reviewed scripts
def test_the_state_read_never_carries_the_shared_key_and_the_action_takes_only_its_words():
    script = ssh.SCRIPTS["l2tp_state"][0]
    assert 'd.pop("psk",None)' in script and "$NK list" in script and "$NK sessions" in script

    for good in ("add", "renew", "set", "del", "show", "kick", "info"):
        assert ssh.render_action("l2tp_cmd", {"CMD": good, "B64": ""})

    for bad in ("hook", "reap", "list", "sessions", "rm", "add;id", "add ", "ADD", "", "add\nid", "$(id)"):
        with pytest.raises(ValueError):
            ssh.render_action("l2tp_cmd", {"CMD": bad, "B64": ""})

    for bad in ("a b", "x;id", "'", "$(id)", "a" * 8001):
        with pytest.raises(ValueError):
            ssh.render_action("l2tp_cmd", {"CMD": "add", "B64": bad})


def _run_action_script(tmp_path, cmd, args):
    """Run the python inside the action against a stub manager that prints its own argv: what reaches the manager is what was asked, one argument each, no shell."""
    stub = tmp_path / "nk-l2tp"
    stub.write_text("#!" + sys.executable + "\nimport json, sys\nprint(json.dumps({'ok': True, 'argv': sys.argv[1:]}))\n")
    stub.chmod(0o755)
    script = ssh.render_action("l2tp_cmd", {"CMD": cmd, "B64": base64.b64encode(json.dumps(args).encode()).decode()})
    code = script.split("<<'__NK_PY__'\n", 1)[1].rsplit("\n__NK_PY__", 1)[0].replace("/usr/local/sbin/nk-l2tp", str(stub))
    first = script.split("\n", 1)[0]
    assert first.startswith("python3 - ") and "'" + cmd + "'" in first
    r = subprocess.run([sys.executable, "-I", "-", cmd, base64.b64encode(json.dumps(args).encode()).decode()], input=code, capture_output=True, text=True, timeout=30, check=False)
    return r.stdout.strip()


def test_the_action_hands_every_argument_to_the_manager_as_it_is(tmp_path):
    nasty = ["l2_5", "--password", "a b;c$(id)'\"`", "--note", "order 1 user 2; rm -rf /", "--days", "7"]
    out = json.loads(_run_action_script(tmp_path, "add", nasty))
    assert out == {"ok": True, "argv": ["add"] + nasty}
    assert json.loads(_run_action_script(tmp_path, "info", []))["argv"] == ["info"]
    assert json.loads(_run_action_script(tmp_path, "set", ["x", "--note", ""]))["argv"] == ["set", "x", "--note", ""]


def test_the_action_refuses_what_is_not_a_short_list_of_strings(tmp_path):
    for args in ("text", {"a": 1}, [1, 2], ["x" * 201], ["a"] * 25):
        out = json.loads(_run_action_script(tmp_path, "add", args))
        assert out == {"error": "bad arguments"}, args


# ------------------------------------------------------------------ parsing
def test_the_state_read_is_parsed_and_an_account_is_shown_as_the_page_needs_it():
    p = l2tp.parse_state(STATE_OUT)
    assert p["installed"] and p["list_ok"] and p["units"] == {"strongswan": "active", "xl2tpd": "active", "tick": "active"} and p["udp"] == 5 and p["load"] == 0.42
    assert p["info"]["pool"] == "10.250.0.0/22" and "psk" not in p["info"] and len(p["accounts"]) == 4 and p["sessions"][0]["user"] == "alice"

    rows = {a["user"]: l2tp.shape_account(a, NOW, "l2_") for a in p["accounts"]}
    assert rows["alice"]["state"] == "active" and rows["alice"]["days_left"] == 5.0 and not rows["alice"]["bot"] and rows["alice"]["limit"] == 2 and rows["alice"]["tx"] == 5000000
    assert rows["l2_7"]["state"] == "expired" and rows["l2_7"]["bot"] and rows["l2_7"]["days_left"] == -1.0
    assert rows["bob"]["state"] == "disabled" and rows["bob"]["days_left"] is None and (rows["bob"]["down"], rows["bob"]["up"]) == (8.0, 2.0)
    assert rows["alice"]["quota"] == 50_000_000_000 and rows["alice"]["used"] == 5_001_000 and rows["alice"]["over"] is False
    assert rows["carl"]["state"] == "over" and rows["carl"]["over"] is True and rows["carl"]["used"] == 1_100_000_000 and rows["carl"]["quota"] == 1_000_000_000
    assert rows["bob"]["quota"] is None and rows["bob"]["used"] == 0                    # (an older manager sends no quota: the count is rx + tx, no quota)
    old = l2tp.shape_account({"user": "x", "rx": 3, "tx": 4, "expires": None}, NOW, "")
    assert old["quota"] is None and old["used"] == 7 and old["over"] is False and old["state"] == "active"

    assert l2tp.parse_state("L2TP none\n")["installed"] is False
    junk = l2tp.parse_state("L2TP ok\nLIST not json\nINFO {\nSESS []\nUNIT xl2tpd\nLOAD x\nUDP y\n")
    assert junk["installed"] and not junk["list_ok"] and junk["accounts"] == [] and junk["sessions"] == [] and junk["load"] is None and junk["udp"] == 0
    assert l2tp.shape_account({"user": "l2_x"}, NOW, "")["bot"] is False          # (no prefix = no account is the bot's)


def test_an_account_of_the_shop_bot_is_known_by_its_note_or_its_old_prefix():
    """The bot makes `nikatelshop_<order>` accounts with the note `order N user U` (the owner, 2026-11-07: the name carries the shop's name); the older ones are `l2_<n>`."""
    assert l2tp.made_by_bot("nikatelshop_123", "order 123 user 5445732982", "l2_") is True
    assert l2tp.made_by_bot("l2_7", "", "l2_") is True
    assert l2tp.made_by_bot("l2_7", "order 7 user 9", "") is True
    assert l2tp.made_by_bot("nikatelshop_ali", "for ali", "l2_") is False          # (made by hand in the panel: the name has the brand, the note is the owner's)
    assert l2tp.made_by_bot("nikatelshop_ali", "", "l2_") is False
    assert l2tp.made_by_bot("alice", "my order 5 user 1", "l2_") is False          # (the note must START with the bot's words)
    assert l2tp.made_by_bot("x", "order 5 user", "") is False
    a = l2tp.shape_account({"user": "nikatelshop_9", "note": "order 9 user 31"}, NOW, "l2_")
    assert a["bot"] is True and l2tp.shape_account({"user": "nikatelshop_ali", "note": ""}, NOW, "l2_")["bot"] is False


def test_the_exit_read_is_parsed():
    p = l2tp.parse_exit(EXIT_OUT)
    assert p == {"installed": True, "routes": ["10.250.0.0/22 192.0.2.17"], "rules": 11, "forward": True, "timer": "active"}
    assert l2tp.parse_exit("EXIT none\n")["installed"] is False


# ------------------------------------------------------------------ the settings
def test_the_default_name_is_nikatel_shop_and_the_servers_are_checked(ids):
    a, b, t = ids
    c = settings.get("l2tp")
    assert c["title"] == "Nikatel Shop" and c["entry_id"] == 0 and c["bot_prefix"] == "l2_"

    with pytest.raises(ValueError, match="Iran"):
        l2tp.set_conf(b, 0, 0, "x", "")                                  # a foreign server cannot be the entry

    with pytest.raises(ValueError, match="foreign"):
        l2tp.set_conf(a, a, 0, "x", "")                                  # an Iran server cannot be the exit

    with pytest.raises(ValueError, match="tunnel"):
        l2tp.set_conf(a, b, 999, "x", "")

    for bad in ("", "a\nb", "x" * 41):
        with pytest.raises(ValueError):
            l2tp.set_conf(a, b, t, bad, "")

    with pytest.raises(ValueError, match="address"):
        l2tp.set_conf(a, b, t, "Shop", "bad host;")

    assert l2tp.set_conf(a, b, t, " Nikatel Shop ", "vpn.example.org")["address"] == "vpn.example.org" and l2tp.conf()["title"] == "Nikatel Shop"


# ------------------------------------------------------------------ the page
def test_state_without_a_choice_is_not_configured_and_asks_no_server(monkeypatch):
    fake = Fake(monkeypatch)
    s = l2tp.state()
    assert s["configured"] is False and s["entry"] is None and s["accounts"] == [] and fake.calls == []


def test_state_reads_the_entry_the_exit_and_the_tunnel(ready):
    s = l2tp.state()
    assert s["entry"]["ok"] and s["entry"]["pool"] == "10.250.0.0/22" and s["entry"]["load"] == 0.42 and s["entry"]["features"] == ["speed", "quota"] and s["entry"]["quota"] is True
    assert s["exit"]["ok"] and s["exit"]["rules"] == 11
    assert s["tunnel"]["name"] == "fa-ia" and s["tunnel"]["state"] == "up" and s["tunnel"]["protected"] is True
    assert [a["user"] for a in s["accounts"]] == ["alice", "l2_7", "bob", "carl"] and s["online"] == 1 and s["sessions"][0]["ifname"] == "ppp0"
    assert "psk" not in json.dumps(s) and "password" not in json.dumps(s)          # (the list carries no secret)


def test_a_part_that_cannot_be_read_says_why_and_the_rest_still_shows(ids, monkeypatch):
    a, b, t = ids
    l2tp.set_conf(a, b, t, "Nikatel Shop", "")
    Fake(monkeypatch, state_out=SSHError("ssh connect failed"), exit_out="EXIT none\n")
    s = l2tp.state()
    assert s["entry"]["ok"] is False and "ssh connect failed" in s["entry"]["error"] and s["accounts"] == []
    assert s["exit"]["ok"] is False and "not installed" in s["exit"]["error"] and s["tunnel"]["name"] == "fa-ia"

    Fake(monkeypatch, state_out="L2TP none\n")
    assert "not installed" in l2tp.state()["entry"]["error"]

    settings.put("l2tp", {"exit_id": 9999})                              # a server removed from the panel
    s = l2tp.state()
    assert s["exit"]["ok"] is False and "gone" in s["exit"]["error"]


def test_an_entry_whose_manager_cannot_keep_a_quota_is_told(ids, monkeypatch):
    a, b, t = ids
    l2tp.set_conf(a, b, t, "Nikatel Shop", "")
    Fake(monkeypatch, state_out=STATE_OUT.replace('"version": 3, "features": ["speed", "quota"]', '"version": 2, "features": ["speed"]'))
    s = l2tp.state()
    assert s["entry"]["quota"] is False and s["entry"]["ok"] is True


def test_an_unprotected_tunnel_is_told(ids, monkeypatch):
    a, b, t = ids

    with session() as db:
        db.get(Tunnel, t).record = {}

    l2tp.set_conf(a, b, t, "Nikatel Shop", "")
    Fake(monkeypatch)
    assert l2tp.state()["tunnel"]["protected"] is False


# ------------------------------------------------------------------ changing
def test_a_new_account_is_asked_for_exactly(ready):
    r = l2tp.add("ali_01", 30, 2, 3, 3, "secret12", "for ali")
    assert r["ok"] is True
    assert ready.actions()[-1] == ("action", "add", ["ali_01", "--days", "30", "--limit", "2", "--speed", "3", "--password", "secret12", "--note", "for ali"])

    l2tp.add("bo_02", 7, 1, 8, 2)
    assert ready.actions()[-1][2] == ["bo_02", "--days", "7", "--limit", "1", "--down", "8", "--up", "2"]
    l2tp.add("cy_03", 7, 1)
    assert ready.actions()[-1][2] == ["cy_03", "--days", "7", "--limit", "1"]
    l2tp.add("di_04", 7, 1, 0, None)
    assert ready.actions()[-1][2][-2:] == ["--down", "0"]
    l2tp.add("ed_05", 1, 1, 2.5, 2.5)
    assert "--speed" in ready.actions()[-1][2] and "2.5" in ready.actions()[-1][2]


def test_the_traffic_the_end_date_and_the_reset_are_asked_for_exactly(ready):
    l2tp.add("gb_01", 30, 1, None, None, "", "", 50)
    assert ready.actions()[-1][2] == ["gb_01", "--days", "30", "--limit", "1", "--gb", "50"]
    l2tp.add("gb_02", 30, 1, 2, 2, "", "", 0.5)
    assert ready.actions()[-1][2] == ["gb_02", "--days", "30", "--limit", "1", "--speed", "2", "--gb", "0.5"]
    l2tp.add("gb_03", 30, 1, None, None, "", "", 0)
    assert ready.actions()[-1][2][-2:] == ["--gb", "0"]                # (0 is told to the manager: no limit)
    l2tp.add("gb_04", 30, 1, None, None, "", "", "")
    assert "--gb" not in ready.actions()[-1][2]                        # (empty = not given)

    end = int(l2tp.time.time()) + 10 * 86400
    l2tp.change("alice", password="newpass1", devices=3, down=5, up=1, gb=100, expires=end, reset_usage=True, disabled=False, note="x")
    assert ready.actions()[-1][2] == ["alice", "--password", "newpass1", "--limit", "3", "--down", "5", "--up", "1", "--gb", "100", "--expires", str(end), "--reset-usage", "--enable", "--note", "x"]
    l2tp.change("alice", reset_usage=True)
    assert ready.actions()[-1][2] == ["alice", "--reset-usage"]
    l2tp.change("alice", gb=0)
    assert ready.actions()[-1][2] == ["alice", "--gb", "0"]
    l2tp.renew("alice", 30, True)
    assert ready.actions()[-1] == ("action", "renew", ["alice", "--days", "30", "--reset-usage"])
    l2tp.renew("alice", 30)
    assert "--reset-usage" not in ready.actions()[-1][2]


def test_a_bad_traffic_or_end_date_never_reaches_the_entry(ready):
    before = len(ready.calls)
    now = int(l2tp.time.time())

    for gb in (-1, 0.005, 100001, "lots"):
        with pytest.raises(ValueError, match="traffic"):
            l2tp.add("gb_05", 30, 1, gb=gb)

        with pytest.raises(ValueError, match="traffic"):
            l2tp.change("alice", gb=gb)

    for end in (now - 400 * 86400, now + 4000 * 86400, "tomorrow"):
        with pytest.raises(ValueError, match="end date"):
            l2tp.change("alice", expires=end)

    assert len(ready.calls) == before


def test_a_bad_value_never_reaches_the_entry(ready):
    before = len(ready.calls)
    bad = [("Ali", 30, 1, None, None, "", ""), ("u1", 30, 1, None, None, "", ""), ("ali_01", 0, 1, None, None, "", ""), ("ali_01", 30, 11, None, None, "", ""), ("ali_01", 30, 1, -1, None, "", ""),
           ("ali_01", 30, 1, 0.05, None, "", ""), ("ali_01", 30, 1, 5000, None, "", ""), ("ali_01", 30, 1, "fast", None, "", ""), ("ali_01", 30, 1, None, None, "short", ""), ("ali_01", 30, 1, None, None, "has space", ""),
           ("ali_01", 30, 1, None, None, "", "a\nb"), ("ali_01", 30, 1, None, None, "", "x" * 81)]

    for args in bad:
        with pytest.raises(ValueError):
            l2tp.add(*args)

    assert len(ready.calls) == before


def test_renew_change_remove_and_kick(ready):
    l2tp.renew("alice", 30)
    assert ready.actions()[-1] == ("action", "renew", ["alice", "--days", "30"])
    l2tp.change("alice", password="newpass1", devices=3, down=5, up=1, disabled=True, note="x")
    assert ready.actions()[-1] == ("action", "set", ["alice", "--password", "newpass1", "--limit", "3", "--down", "5", "--up", "1", "--disable", "--note", "x"])
    l2tp.change("alice", disabled=False, note="")
    assert ready.actions()[-1][2] == ["alice", "--enable", "--note", ""]
    l2tp.change("alice", down=0, up=0)
    assert ready.actions()[-1][2] == ["alice", "--speed", "0"]
    l2tp.remove("alice")
    assert ready.actions()[-1] == ("action", "del", ["alice"])
    l2tp.kick("alice")
    assert ready.actions()[-1] == ("action", "kick", ["alice"])

    with pytest.raises(ValueError, match="nothing to change"):
        l2tp.change("alice")

    for fn in (lambda: l2tp.renew("alice", 0), lambda: l2tp.renew("Alice", 5), lambda: l2tp.remove("x"), lambda: l2tp.change("alice", devices=0), lambda: l2tp.change("alice", password="bad pw!")):
        with pytest.raises(ValueError):
            fn()


def test_the_managers_own_error_comes_back_in_its_words(ready):
    ready.answers["add"] = json.dumps({"error": "exists"})
    with pytest.raises(ValueError, match="exists"):
        l2tp.add("ali_01")

    ready.answers["del"] = "not json at all"
    with pytest.raises(ValueError, match="expected way"):
        l2tp.remove("ali_01")

    ready.answers["kick"] = ""
    with pytest.raises(ValueError, match="expected way"):
        l2tp.kick("ali_01")


def test_nothing_is_changed_before_an_entry_is_chosen(monkeypatch):
    fake = Fake(monkeypatch)

    with pytest.raises(ValueError, match="choose the entry"):
        l2tp.add("ali_01")

    assert fake.calls == []


# ------------------------------------------------------------------ the customer's message
def test_the_message_for_the_customer_has_the_connection_name_first(ready):
    ready.answers["show"] = json.dumps({"user": "alice", "password": "pw-alice-1", "limit": 2, "expires": NOW, "down": 3.0, "up": 3.0})
    ready.answers["info"] = json.dumps({"psk": "SHAREDKEY0123456789abcd", "pool": "10.250.0.0/22"})
    p = l2tp.profile("alice")
    t = p["text"]
    assert t.splitlines()[0] == "Nikatel Shop" and p["address"] == "192.0.2.10" and p["user"] == "alice"
    assert "pw-alice-1" in t and "SHAREDKEY0123456789abcd" in t and "192.0.2.10" in t and "alice" in t and "(Name): Nikatel Shop" in t
    assert "آیفون" in t and "اندروید" in t and "ویندوز" in t and "اعتبار تا: 2027-01-15" in t
    assert "3 / 3 Mbit/s" in t and "حجم: نامحدود" in t

    ready.answers["show"] = json.dumps({"user": "alice", "password": "pw-alice-1", "limit": 2, "expires": NOW, "down": None, "up": None, "quota": 50_000_000_000})
    assert "حجم: 50 GB" in l2tp.profile("alice")["text"]
    ready.answers["show"] = json.dumps({"user": "alice", "password": "pw-alice-1", "limit": 2, "expires": NOW, "down": None, "up": None, "quota": 1_500_000_000})
    assert "حجم: 1.5 GB" in l2tp.profile("alice")["text"]

    c = settings.get("l2tp")
    l2tp.set_conf(c["entry_id"], c["exit_id"], c["tunnel_id"], "My VPN", "vpn.example.org")
    ready.answers["show"] = json.dumps({"user": "bob", "password": "pw-bob-22", "limit": 1, "expires": None, "down": None, "up": None})
    t2 = l2tp.profile("bob")["text"]
    assert t2.splitlines()[0] == "My VPN" and "vpn.example.org" in t2 and "Mbit/s" not in t2 and "اعتبار تا: —" in t2


# ------------------------------------------------------------------ the routes
def test_the_routes_need_the_owner_to_be_signed_in():
    from fastapi.testclient import TestClient

    from nikatel import auth
    from nikatel.main import create_app

    auth.set_password("test-password-1")

    with TestClient(create_app(run_jobs=False)) as anon:
        for method, path in (("get", "/api/l2tp"), ("get", "/api/l2tp/config"), ("put", "/api/l2tp/config"), ("post", "/api/l2tp/accounts"), ("get", "/api/l2tp/accounts/alice/profile"), ("delete", "/api/l2tp/accounts/alice")):
            assert getattr(anon, method)(path).status_code == 401, path


def test_the_page_routes(client, ids, monkeypatch):
    a, b, t = ids
    fake = Fake(monkeypatch)

    r = client.get("/api/l2tp").json()
    assert r["ok"] and r["configured"] is False

    c = client.get("/api/l2tp/config").json()
    assert [s["name"] for s in c["choices"]["iran"]] == ["iran-a"] and [s["name"] for s in c["choices"]["foreign"]] == ["foreign-a"] and c["choices"]["tunnels"][0]["name"] == "fa-ia" and c["conf"]["title"] == "Nikatel Shop"
    assert client.put("/api/l2tp/config", json={"entry_id": b, "title": "x"}).status_code == 400
    assert client.put("/api/l2tp/config", json={"entry_id": a, "exit_id": b, "tunnel_id": t, "title": "Nikatel Shop"}).json()["conf"]["entry_id"] == a

    r = client.get("/api/l2tp").json()
    assert r["configured"] and [x["user"] for x in r["accounts"]] == ["alice", "l2_7", "bob", "carl"] and r["entry"]["ok"]

    n = len(fake.calls)
    client.get("/api/l2tp")
    assert len(fake.calls) == n                                          # (kept for 10 s: the entry is not asked again)
    assert client.get("/api/l2tp?fresh=true").status_code == 200 and len(fake.calls) > n

    fake.answers["add"] = json.dumps({"ok": True, "user": "ali_01", "password": "pw12345678", "limit": 1, "expires": NOW, "down": None, "up": None})
    r = client.post("/api/l2tp/accounts", json={"name": "ali_01", "days": 30, "devices": 1})
    assert r.status_code == 201 and r.json()["password"] == "pw12345678"
    assert fake.actions()[-1][2][:5] == ["ali_01", "--days", "30", "--limit", "1"]

    n = len(fake.calls)
    client.get("/api/l2tp")
    assert len(fake.calls) > n                                           # (a change drops the kept answer)

    assert client.post("/api/l2tp/accounts", json={"name": "gb_acct", "days": 30, "gb": 20}).status_code == 201 and fake.actions()[-1][2][-2:] == ["--gb", "20"]
    assert client.post("/api/l2tp/accounts", json={"name": "gb_acct2", "days": 30, "gb": -5}).status_code == 422
    assert client.post("/api/l2tp/accounts", json={"name": "gb_acct3", "days": 30, "gb": 0.001}).status_code == 400          # (below the smallest traffic limit: the service says so)
    assert client.post("/api/l2tp/accounts", json={"name": "Bad Name", "days": 30}).status_code == 400
    assert client.post("/api/l2tp/accounts", json={"name": "ok_name", "days": 0}).status_code == 422
    assert client.post("/api/l2tp/accounts/alice/renew", json={"days": 10}).status_code == 200 and fake.actions()[-1][1] == "renew"
    assert client.put("/api/l2tp/accounts/alice", json={"devices": 2, "disabled": False}).status_code == 200 and fake.actions()[-1][2] == ["alice", "--limit", "2", "--enable"]
    assert client.put("/api/l2tp/accounts/alice", json={"gb": 5, "reset_usage": True}).status_code == 200 and fake.actions()[-1][2] == ["alice", "--gb", "5", "--reset-usage"]
    assert client.post("/api/l2tp/accounts/alice/renew", json={"days": 30, "reset_usage": True}).status_code == 200 and fake.actions()[-1][2] == ["alice", "--days", "30", "--reset-usage"]
    assert client.put("/api/l2tp/accounts/alice", json={}).status_code == 400
    assert client.post("/api/l2tp/accounts/alice/kick").status_code == 200
    assert client.delete("/api/l2tp/accounts/alice").status_code == 200 and fake.actions()[-1][1] == "del"

    fake.answers["show"] = json.dumps({"user": "alice", "password": "pw-alice-1", "limit": 1, "expires": NOW, "down": None, "up": None})
    fake.answers["info"] = json.dumps({"psk": "KEYKEYKEYKEYKEYKEYKEYKEY"})
    p = client.get("/api/l2tp/accounts/alice/profile").json()
    assert p["ok"] and p["psk"] == "KEYKEYKEYKEYKEYKEYKEYKEY" and p["text"].startswith("Nikatel Shop")

    fake.answers["del"] = json.dumps({"error": "no such account"})
    assert client.delete("/api/l2tp/accounts/ghost").status_code == 400


def test_an_unreachable_entry_is_a_bad_gateway_and_a_read_only_panel_is_a_conflict(client, ids, monkeypatch):
    a, b, t = ids
    l2tp.set_conf(a, b, t, "Nikatel Shop", "")

    def boom(node, name, params=None, timeout=90):
        raise SSHError("ssh connect failed")

    monkeypatch.setattr(l2tp, "run_action", boom)
    assert client.post("/api/l2tp/accounts", json={"name": "ali_01", "days": 30}).status_code == 502

    monkeypatch.undo()                                                   # the real run_action: the test panel is read-only, it refuses before any connection
    r = client.post("/api/l2tp/accounts", json={"name": "ali_01", "days": 30})
    assert r.status_code == 409 and "read-only" in r.json()["error"]


# ------------------------------------------------------------------ the page's files
def test_the_tab_is_wired_into_the_page():
    index = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/static/l2tp.css">' in index and '<script src="/static/l2tp.js"></script>' in index
    assert index.index("/static/app.js") < index.index("/static/l2tp.js")                 # (it uses the helpers of app.js)
    assert '["l2tp", "🔐 L2TP"' in app.split("const SUB = {", 1)[1].split("\n};", 1)[0] and 'if (s === "l2tp")' in app and "PAGES.l2tp()" in app
    assert '"l2tp.js"' in (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")


def test_every_handler_the_page_writes_into_its_html_exists():
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    used = set()

    for attr in re.findall(r'\bon(?:click|input|change|submit)="([^"]*)"', js):
        used |= set(re.findall(r"\b(l2[A-Z]\w*)\(", attr))

    defined = set(re.findall(r"window\.(l2[A-Z]\w*) =", js)) | set(re.findall(r"function (l2[A-Z]\w*)\(", js)) | set(re.findall(r"const (l2[A-Z]\w*) =", js))
    assert used and used <= defined, used - defined
    assert "this.value = this.value.toLowerCase(); l2Sync()" in js and 'state.sub.tunnels === "l2tp"' in js and "if (!l2Here()) { clearInterval(l2.timer)" in js          # (the name is lower-cased as it is typed; the refresh stops when the tab is left)


def test_the_account_forms_are_windows_of_their_own_never_a_box_over_the_table():
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    css = (WEB / "l2tp.css").read_text(encoding="utf-8")
    assert 'showModal("New account"' in js and "showModal(`Edit · " in js and 'showModal("Servers"' in js and "showModal(`Connection · " in js
    assert "l2-pop" not in js and "l2-pop" not in css and "position: absolute" not in css          # (the owner, 2026-11-07: the box that made an account lay over the table - it is a window now)
    assert "#modal .modal.l2-modal" in css and "l2Sync" in js and "Create account" in js and "Save changes" in js
    assert 'const L2_BRAND = "nikatelshop_"' in js and 'value="${L2_BRAND}"' in js and "L2_BRAND + Math.random()" in js          # (the owner, 2026-11-07: every username starts with the shop's name: prefilled, and the Random name too)


def test_the_tab_opens_at_once_and_reads_the_entry_behind_it():
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    page = js.split("PAGES.l2tp = async () => {", 1)[1].split("\n};", 1)[0]
    assert "await" not in page          # (the owner, 2026-11-07: "it loads very late, the menu must open in a moment" - the page never waits for the SSH read before it draws)
    assert page.index("l2Draw();") < page.index("l2Pull(true)") and "l2Last()" in page
    assert "sessionStorage" in js and "if (mark && l2Here()) l2Draw();" in js and "if (l2Here()) l2Draw();" in js          # (the last answer is kept; a late answer never draws over another page)
    assert "Reading the entry…" in js and "Try again" in js and "this is the last answer" in js


def test_the_row_buttons_are_a_segmented_control_and_two_icon_buttons_and_no_native_dialog():
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    css = (WEB / "l2tp.css").read_text(encoding="utf-8")
    assert 'class="l2-seg"' in js and js.count('class="l2-ico') == 3 and ".l2-seg" in css and ".l2-ico" in css and ".l2-ico:disabled" in css          # (the owner, 2026-11-07: "make these buttons smart and professional, MacBook style"; the third is the delete of a plan in the shop window)
    assert "Nobody is connected now" in js and '" idle"' in js and ".l2-ico.idle" in css          # (1.69.245: the arrow button is muted while nobody is connected but still works - the picture could be a few seconds old)
    assert not re.search(r"(?<![\w.])(confirm|prompt|alert)\(", js)          # (the grey box of the browser is gone: the panel's own glass windows ask)
    assert "await gconfirm(" in js and "gprompt(" not in js and "glassChoice(" not in js          # (the renewal is a window of its own now: time and traffic together)
    assert "|Add|Disconnect)" in (WEB / "app.js").read_text(encoding="utf-8")          # (the glass box of a Disconnect names its button Disconnect, not OK)


def test_the_entry_and_the_exit_are_read_at_the_same_time(ids, monkeypatch):
    a, b, t = ids
    l2tp.set_conf(a, b, t, "Nikatel Shop", "")
    meet = threading.Barrier(2, timeout=5)

    def script(node, name, params=None, timeout=40):
        meet.wait()          # read one after the other, the first read would wait for a second one that never starts, and the barrier would break
        return STATE_OUT if name == "l2tp_state" else EXIT_OUT

    monkeypatch.setattr(l2tp, "run_script", script)
    s = l2tp.state()
    assert s["entry"]["ok"] is True and s["exit"]["ok"] is True and s["tunnel"]["name"] == "fa-ia" and len(s["accounts"]) == 4


def test_the_new_files_hold_no_secret_or_address_of_ours():
    for f in ("l2tp.js", "l2tp.css"):
        text = (WEB / f).read_text(encoding="utf-8")
        assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text), f


# ------------------------------------------------------------------ 1.69.245: the live light, the renewal window, the shop side (plans, sales, who sees L2TP)
def test_an_account_knows_since_when_a_device_is_connected(ready):
    s = l2tp.state()
    by = {a["user"]: a for a in s["accounts"]}
    assert by["alice"]["since"] == 1799999000 and by["bob"]["since"] is None


def test_the_light_read_asks_only_the_entry_and_keeps_the_exit_of_the_last_full_answer(ready, monkeypatch):
    fake = ready
    full = l2tp.state()
    fake.calls.clear()
    light = l2tp.state(base=full)
    assert [c[1] for c in fake.calls] == ["l2tp_state"]          # (not l2tp_exit_state)
    assert light["exit"] == full["exit"] and light["tunnel"]["name"] == "fa-ia" and len(light["accounts"]) == 4


def test_the_live_route_reads_the_entry_alone_between_the_full_reads(client, ids, monkeypatch):
    from nikatel.api import l2tp as api

    a, b, t = ids
    api._drop()
    fake = Fake(monkeypatch)
    client.put("/api/l2tp/config", json={"entry_id": a, "exit_id": b, "tunnel_id": t, "title": "Nikatel Shop"})
    monkeypatch.setattr(api, "LIVE_TTL", 0.0)
    assert client.get("/api/l2tp").status_code == 200
    fake.calls.clear()
    r = client.get("/api/l2tp?live=1").json()
    assert [c[1] for c in fake.calls] == ["l2tp_state"] and r["exit"]["ok"] and r["online"] == 1          # (one SSH read for the whole tab, every few seconds)
    api._STATE["full_at"] -= api.FULL_MAX + 5
    fake.calls.clear()
    assert client.get("/api/l2tp?live=1").status_code == 200 and sorted(c[1] for c in fake.calls) == ["l2tp_exit_state", "l2tp_state"]          # (the exit, the units and the tunnel are read in full now and then)
    api._drop()


def test_the_page_shows_who_is_connected_by_a_light_and_follows_the_entry():
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    css = (WEB / "l2tp.css").read_text(encoding="utf-8")
    assert "l2-dot ok l2-live" in js and "Connected now:" in js and ".l2-dot.l2-live" in css and "prefers-reduced-motion" in css          # (the owner, 2026-11-07: after Disconnect "the light must change, and show Connected again when the phone connects")
    assert "?live=1" in js and "l2.fast = Date.now() + 45000" in js and "visibilitychange" in js          # (the light read; close after a Disconnect; at once when the owner comes back to the window)
    assert 'const L2_STATE = {expired: ["bad", "Ended"], over: ["bad", "Used up"], disabled: ["warn", "Off"]}' in js and "l2-st" in css
    assert "l2Filters(d)" in js and 'l2Views = [["", "All"' in js and ".l2-filt" in css          # (All / Connected / Ending soon / Ended, each with its count)
    assert "typing >= 0" in js          # (the cursor stays in the search box while the figures move)


def test_the_renewal_is_a_window_of_its_own_with_time_and_traffic_together():
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    assert 'showModal(`Renew · ' in js and "l2RenewGo" in js and "Count the traffic from zero again" in js and "Add more traffic" in js
    assert "/renew`, {method: \"POST\", body: JSON.stringify({days, reset_usage: reset})}" in js and "gb: +((r.quota / 1e9) + add).toFixed(2)" in js          # (the top-up raises the limit of the account by that much)
    assert "l2GbAdd(10)" in js and "x.kicked ?" in js          # (+10 GB in Edit; the Disconnect says how many devices it cut, or that nobody was connected)


# ---- the shop side
class FakeBot:
    """The shop bot's database as the panel's two channels see it: `query` answers the SELECTs the module asks, `write` records the statement."""

    def __init__(self, monkeypatch, plans=None, accounts_on_plan=None, stage_value=None):
        from nikatel.services import shopbot

        self.writes, self.queries, self.states = [], [], []
        self.plans = plans if plans is not None else [
            {"id": "2", "title": "L2TP · 1 month", "days": "30", "devices": "2", "down": "10.0", "up": "10.0", "price": "150000", "descr": "line one\nline two", "active": "1", "sort": "1", "gb": "100.00"},
            {"id": "1", "title": "L2TP test", "days": "7", "devices": "1", "down": "NULL", "up": "NULL", "price": "0", "descr": "NULL", "active": "0", "sort": "0", "gb": "NULL"}]
        self.on_plan = accounts_on_plan if accounts_on_plan is not None else {2: 3}
        self.stage_value = stage_value
        monkeypatch.setattr(shopbot, "query", self.query)
        monkeypatch.setattr(shopbot, "write", self.write)
        monkeypatch.setattr(shopbot, "set_states", lambda ch: self.states.append(ch) or ch)

    def query(self, sql):
        self.queries.append(sql)

        if "FROM l2tp_plans p ORDER BY p.sort" in sql:
            return [{**p, "accounts": str(self.on_plan.get(int(p["id"]), 0))} for p in sorted(self.plans, key=lambda p: (int(p["sort"]), int(p["id"])))]

        if "WHERE plan_id =" in sql:
            return [{"n": str(self.on_plan.get(int(sql.rsplit("=", 1)[1]), 0))}]

        if "BOT_STATES" in sql:
            return [] if self.stage_value is None else [{"value": json.dumps({"miniL2tp": self.stage_value} if self.stage_value else {})}]

        if "COALESCE(SUM(CASE" in sql:
            return [{"n": "5", "total": "750000", "d1": "150000", "d7": "450000", "d30": "750000", "buys": "3", "renews": "2"}]

        if "FROM pays p LEFT JOIN users" in sql and "lp.title AS plan_title" in sql:
            return [{"id": "9", "user_id": "55", "name": "Sara", "username": "sara_x", "type": "BUY_L2TP", "price": "150000", "state": "paid_with_wallet", "request_date": "1799990000", "plan_id": "2", "description": "L2TP", "plan_title": "L2TP · 1 month"},
                    {"id": "8", "user_id": "66", "name": "", "username": "kian", "type": "RENEW_L2TP", "price": "150000", "state": "low_wallet", "request_date": "1799980000", "plan_id": "7", "description": "renew nikatelshop_4", "plan_title": "NULL"}]

        raise AssertionError("unexpected query: " + sql)

    def write(self, sql):
        self.writes.append(sql)
        return "DONE"


def test_the_plans_are_read_with_the_accounts_bought_on_them_and_the_stage_defaults_to_admins(monkeypatch):
    from nikatel.services import l2tp_shop

    bot = FakeBot(monkeypatch)
    ps = l2tp_shop.plans()
    assert [p["id"] for p in ps] == [1, 2]          # (the order number, then the id)
    two = ps[1]
    assert (two["title"], two["days"], two["devices"], two["gb"], two["down"], two["up"], two["price"], two["active"], two["accounts"]) == ("L2TP · 1 month", 30, 2, 100.0, 10.0, 10.0, 150000, True, 3) and two["descr"] == "line one\nline two"
    assert ps[0]["gb"] is None and ps[0]["down"] is None and ps[0]["active"] is False and ps[0]["accounts"] == 0 and ps[0]["descr"] == ""
    assert l2tp_shop.stage() == "admin"
    bot.stage_value = "on"
    assert l2tp_shop.stage() == "on"
    bot.stage_value = "nonsense"
    assert l2tp_shop.stage() == "admin"


def test_a_table_the_mini_app_has_not_made_yet_reads_as_nothing(monkeypatch):
    from nikatel.services import l2tp_shop, shopbot

    def gone(sql):
        raise RuntimeError("ERROR 1146 (42S02) at line 1: Table 'bot.l2tp_plans' doesn't exist")

    monkeypatch.setattr(shopbot, "query", gone)
    assert l2tp_shop.plans() == []


def test_a_plan_is_written_exactly_and_a_bad_text_never_reaches_the_bot(monkeypatch):
    from nikatel.services import l2tp_shop

    bot = FakeBot(monkeypatch)
    l2tp_shop.plan_save(None, "L2TP · 3 months", 90, 3, 300, 20, None, 400000, "a\nb", True, 5)
    assert bot.writes[-1] == 'INSERT INTO l2tp_plans (title, days, devices, gb, down, up, price, descr, active, sort) VALUES ("L2TP · 3 months", 90, 3, 300, 20, NULL, 400000, CONCAT_WS(CHAR(10), "a", "b"), 1, 5)'
    l2tp_shop.plan_save(2, "L2TP · 1 month", 30, 2, "", 0, 7.5, 150000, "", False, 0)
    assert bot.writes[-1] == 'UPDATE l2tp_plans SET title = "L2TP · 1 month", days = 30, devices = 2, gb = NULL, down = NULL, up = 7.5, price = 150000, descr = NULL, active = 0, sort = 0 WHERE id = 2'
    n = len(bot.writes)

    for kw in ({"title": 'Say "hi"'}, {"title": "a;b"}, {"title": "back\\slash"}, {"title": "it's"}, {"title": "x"}, {"title": "ok name", "descr": "no `tick`"}, {"title": "ok name", "days": 0}, {"title": "ok name", "days": 4000}, {"title": "ok name", "devices": 11},
               {"title": "ok name", "price": -1}, {"title": "ok name", "price": 10 ** 9}, {"title": "ok name", "gb": 0.001}, {"title": "ok name", "gb": 999999}, {"title": "ok name", "down": 5000}, {"title": "ok name", "price": "abc"}):
        args = {"title": "ok name", "days": 30, "devices": 1, "gb": None, "down": None, "up": None, "price": 0, "descr": ""}
        args.update(kw)

        with pytest.raises(ValueError):
            l2tp_shop.plan_save(None, **args)

    assert len(bot.writes) == n

    with pytest.raises(ValueError, match="no such plan"):
        l2tp_shop.plan_save(77, "ok name", 30, 1, None, None, None, 0, "")          # (the UPDATE of a plan that is not there changes nothing; the answer says so)


def test_a_refusal_of_the_bots_database_comes_back_in_words(monkeypatch):
    from nikatel.services import l2tp_shop

    bot = FakeBot(monkeypatch)
    bot.write = lambda sql: "ERROR 1054 (42S22) at line 1: Unknown column 'gb' in 'field list'"
    monkeypatch.setattr(l2tp_shop.shopbot, "write", bot.write)

    with pytest.raises(ValueError, match="refused"):
        l2tp_shop.plan_save(None, "ok name", 30, 1, None, None, None, 0, "")


def test_a_plan_with_accounts_is_never_deleted_and_its_switch_off_is_told_to_stop_the_renewal(monkeypatch):
    from nikatel.services import l2tp_shop

    bot = FakeBot(monkeypatch)

    with pytest.raises(ValueError, match="cannot be deleted") as e:
        l2tp_shop.plan_delete(2)

    assert "stops their renewal" in str(e.value)          # (the Mini App renews only on a plan that is on: switching off is no gentle way out)

    l2tp_shop.plan_delete(1)
    assert bot.writes[-1] == "DELETE FROM l2tp_plans WHERE id = 1"
    l2tp_shop.plan_active(2, False)
    assert bot.writes[-1] == "UPDATE l2tp_plans SET active = 0 WHERE id = 2"

    with pytest.raises(ValueError, match="no such plan"):
        l2tp_shop.plan_active(99, True)


def test_who_sees_l2tp_goes_through_the_bots_switches(monkeypatch):
    from nikatel.services import l2tp_shop

    bot = FakeBot(monkeypatch)
    assert l2tp_shop.set_stage("agents") == "agents" and bot.states == [{"miniL2tp": "agents"}]

    for bad in ("", "everyone", "ON", "admin;x"):
        with pytest.raises(ValueError):
            l2tp_shop.set_stage(bad)

    assert len(bot.states) == 1


def test_the_shop_window_reads_the_bot_four_ways_at_the_same_time(monkeypatch):
    from nikatel.services import l2tp_shop

    bot = FakeBot(monkeypatch)
    meet = threading.Barrier(4, timeout=5)
    inner = bot.query

    def query(sql):
        meet.wait()          # read one after the other (every read of the bot host is an SSH round trip of about two seconds), the first would wait for a second that never starts, and the barrier would break
        return inner(sql)

    monkeypatch.setattr(l2tp_shop.shopbot, "query", query)
    o = l2tp_shop.overview()
    assert o["stage"] == "admin" and len(o["plans"]) == 2 and o["sales"]["total"] == 750000 and len(o["sales"]["orders"]) == 2


def test_the_sales_count_only_paid_orders_in_the_money_and_show_every_order(monkeypatch):
    from nikatel.services import l2tp_shop

    bot = FakeBot(monkeypatch)
    s = l2tp_shop.sales(now=1_800_000_000)
    assert (s["today"], s["week"], s["month"], s["total"], s["count"], s["buys"], s["renews"]) == (150000, 450000, 750000, 750000, 5, 3, 2)
    sql = next(q for q in bot.queries if "COALESCE(SUM(CASE" in q)
    assert "p.state IN (" in sql and '"paid_with_wallet"' in sql and '"approved"' in sql and '"declined"' not in sql          # (a failed payment is no income)
    first, second = s["orders"]
    assert first["name"] == "Sara" and first["paid"] is True and first["plan"] == "L2TP · 1 month" and first["type"] == "BUY_L2TP"
    assert second["name"] == "kian" and second["paid"] is False and second["plan"] == "" and second["text"] == "renew nikatelshop_4"          # (a plan that is gone: its name is not made up)
    t0 = 1_800_000_000 + 12600
    t0 = t0 // 86400 * 86400 - 12600
    assert f"p.request_date >= {t0}" in sql          # (today starts at midnight in Tehran)


def test_the_shop_routes(client, monkeypatch):
    from nikatel.services import license

    bot = FakeBot(monkeypatch)
    monkeypatch.setattr(license, "has", lambda f: False)
    assert client.get("/api/l2tp/shop").status_code == 402 and client.put("/api/l2tp/shop/stage", json={"stage": "on"}).status_code == 402 and client.post("/api/l2tp/plans", json={"title": "ok name"}).status_code == 402          # (the same licence feature as the Shop bot page)
    assert bot.writes == [] and bot.states == []
    monkeypatch.setattr(license, "has", lambda f: True)

    r = client.get("/api/l2tp/shop").json()
    assert r["ok"] and r["stage"] == "admin" and [s["key"] for s in r["stages"]] == ["off", "admin", "agents", "on"] and len(r["plans"]) == 2 and r["sales"]["total"] == 750000

    assert client.put("/api/l2tp/shop/stage", json={"stage": "on"}).json()["stage"] == "on" and bot.states[-1] == {"miniL2tp": "on"}
    assert client.put("/api/l2tp/shop/stage", json={"stage": "all"}).status_code == 400

    body = {"title": "L2TP · 6 months", "days": 180, "devices": 2, "gb": 600, "price": 800000, "descr": "x"}
    assert client.post("/api/l2tp/plans", json=body).status_code == 201 and bot.writes[-1].startswith("INSERT INTO l2tp_plans")
    assert client.put("/api/l2tp/plans/2", json=body).status_code == 200 and bot.writes[-1].endswith("WHERE id = 2")
    assert client.put("/api/l2tp/plans/2/active", json={"on": False}).json()["ok"] and bot.writes[-1] == "UPDATE l2tp_plans SET active = 0 WHERE id = 2"
    assert client.delete("/api/l2tp/plans/2").status_code == 400          # (accounts were bought on it)
    assert client.delete("/api/l2tp/plans/1").status_code == 200
    assert client.post("/api/l2tp/plans", json={**body, "price": -5}).status_code == 422
    assert client.post("/api/l2tp/plans", json={**body, "title": 'a"b'}).status_code == 400

    def boom(sql):
        raise RuntimeError("ERROR 2002 can't connect")

    monkeypatch.setattr(license, "has", lambda f: True)
    from nikatel.services import shopbot

    monkeypatch.setattr(shopbot, "query", boom)
    assert client.get("/api/l2tp/shop").status_code == 502          # (the bot's database does not answer: the page says so, not a blank error)


def test_the_shop_window_is_wired_and_has_plans_sales_and_the_switch():
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    css = (WEB / "l2tp.css").read_text(encoding="utf-8")
    for need in ('showModal("Plans & sales"', "/api/l2tp/shop", "/api/l2tp/shop/stage", "/api/l2tp/plans", "Who sees L2TP in the Mini App", "l2PlanOpen(0)", "＋ New plan", "Switch off", "Only paid orders are counted", "Reading the shop…", "first a top-up of the wallet"):
        assert need in js, need

    assert "gconfirm(key === \"on\"" in js          # (opening L2TP to the customers asks first)
    for need in (".l2-plan", ".l2-opt", ".l2-tile", ".l2-ord", ".l2-ta"):
        assert need in css, need

def test_the_owner_is_told_what_a_plan_change_does_to_the_accounts_that_renew_on_it():
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    # (the Mini App session, 2026-11-07: a renewal uses the plan's CURRENT price, days and traffic and needs the plan to be ON)
    on = js.split("window.l2PlanOn = async (id, on) => {", 1)[1].split("\n};", 1)[0]
    assert "p.accounts" in on and "gconfirm(" in on and "cannot" in on and on.index("gconfirm(") < on.index("/active`")
    save = js.split("window.l2PlanSave = async () => {", 1)[1].split("\n};", 1)[0]
    assert "l2PlanChanges(was, body)" in save and "gconfirm(" in save and save.index("gconfirm(") < save.index("api(id ?") and "cannot be renewed" in save
    assert "const l2PlanChanges" in js and "A renewal in the Mini App always uses the plan" in js and "do not repeat that" in js
