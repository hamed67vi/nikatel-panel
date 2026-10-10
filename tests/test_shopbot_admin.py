"""The shop's administration from the panel: every statement it builds must pass the reviewed scripts' regexes,
text must travel as base64, and the bot's own rules (wallet on approve, one state change only) must hold."""
import base64
import json
import re

import pytest

from nikatel import ssh
from nikatel.services import shopbot
from nikatel.services import shopbot_admin as adm

READ_RE = ssh.SCRIPTS["bot_query"][1]["SQL"]
WRITE_RE = ssh.ACTIONS["bot_sql"][1]["SQL"]


class FakeDb:
    """Answers reads from a small script and records writes; both checked against the real regexes."""

    def __init__(self, answers=None):
        self.answers, self.writes, self.tg = answers or {}, [], []

    def query(self, sql):
        assert re.match(READ_RE, shopbot._q(sql)), sql
        for needle, rows in self.answers.items():
            if needle in sql:
                return rows() if callable(rows) else rows
        return []

    def write(self, sql):
        assert re.match(WRITE_RE, shopbot._q(sql)), sql
        self.writes.append(sql)
        return "DONE\n"

    def telegram(self, method, body):
        self.tg.append((method, body))
        return {"ok": True}


@pytest.fixture
def db(monkeypatch):
    f = FakeDb()
    monkeypatch.setattr(adm, "read", f.query)
    monkeypatch.setattr(adm, "write", f.write)
    monkeypatch.setattr(adm, "_tg", f.telegram)
    monkeypatch.setattr(adm, "_event", lambda *a, **k: None)
    return f


def b64_of(text):
    return base64.b64encode(text.encode()).decode()


def test_literals_never_contain_what_the_scripts_refuse():
    assert adm.lit(None) == "NULL" and adm.lit(5) == "5" and adm.lit(True) == "1"
    t = "سلام؛ it's `x` \\ \"q\"\nline"
    assert adm.lit(t) == 'FROM_BASE64("' + b64_of(t) + '")'
    assert re.match(WRITE_RE, shopbot._q(f"UPDATE t SET a = {adm.lit(t)} WHERE id = 1"))
    with pytest.raises(ValueError):
        adm.ident("users; DROP")


def test_approve_adds_the_price_to_the_wallet_once_and_tells_both_sides(db):
    pay = {"id": "7", "hash_id": "abc", "user_id": "555", "price": "250000", "state": "pending", "chat_id": "100", "message_id": "9", "admins_messages": json.dumps([{"chat_id": "101", "message_id": "10"}])}
    db.answers = {"FROM pays WHERE id = 7": [pay], "COALESCE(wallet, 0) AS w": [{"w": "1000"}]}
    r = adm.pay_approve(7, admin_id=42)
    assert r["state"] == "approved" and r["wallet"] == 251000
    assert any('SET state = "approved", approved_by = 42 WHERE id = 7' in w for w in db.writes)
    assert any('wallet = COALESCE(wallet, 0) + 250000 WHERE userid = "555"' in w for w in db.writes)
    methods = [m for m, _ in db.tg]
    assert methods.count("editMessageReplyMarkup") == 2 and "sendMessage" in methods         # the admin messages are settled, the user is told
    assert db.tg[-1][1]["chat_id"] == "555" and "250,000" in db.tg[-1][1]["text"]

    pay["state"] = "approved"
    with pytest.raises(ValueError):          # never twice
        adm.pay_approve(7)


def test_decline_tells_the_user_the_reason(db):
    db.answers = {"FROM pays WHERE id = 3": [{"id": "3", "hash_id": "h", "user_id": "9", "price": "5", "state": "pending", "chat_id": "", "message_id": "", "admins_messages": ""}]}
    adm.pay_decline(3, "the receipt is not readable")
    assert any('SET state = "declined" WHERE id = 3' in w for w in db.writes)
    assert db.tg[-1][0] == "sendMessage" and "the receipt is not readable" in db.tg[-1][1]["text"]


def test_plans_categories_and_servers_are_written_with_base64_text(db):
    db.answers = {"FROM server_plans ORDER BY id DESC": [{"id": "12"}], "FROM server_categories ORDER BY id DESC": [{"id": "3"}], "FROM server_info ORDER BY id DESC": [{"id": "4"}],
                  "COUNT(*) AS n FROM server_plans WHERE catid": [{"n": "0"}], "COUNT(*) AS n FROM orders_list WHERE server_id": [{"n": "0"}]}
    assert adm.plan_save(None, {"title": "پلن ۳۰ روزه", "days": 30, "volume": 50, "price": 120000, "catid": 1, "server_id": 2, "bogus": "x"}) == 12
    ins = db.writes[-1]
    assert ins.startswith("INSERT INTO server_plans (") and adm.lit("پلن ۳۰ روزه") in ins and "bogus" not in ins and "active" in ins
    adm.plan_save(12, {"price": "150000", "active": 0})
    assert "UPDATE server_plans SET price = 150000, active = 0 WHERE id = 12" in db.writes[-1]
    with pytest.raises(ValueError):
        adm.plan_save(12, {"bogus": 1})

    assert adm.category_save(None, "Europe", 0, 1) == 3
    assert adm.server_save(None, {"title": "DE panel", "ucount": 500}, {"type": "pasarguard", "panel_url": "https://p.example:8000", "username": "admin", "password": "s3cr\"et"}) == 4
    assert any("INSERT INTO server_config (id, type, panel_url, username, password" in w and adm.lit('s3cr"et') in w for w in db.writes)
    adm.server_save(4, {"ucount": 900}, {"password": ""})         # an empty password keeps the old one
    assert "UPDATE server_info SET ucount = 900 WHERE id = 4" in db.writes[-1] and not any("UPDATE server_config SET password" in w for w in db.writes)
    with pytest.raises(ValueError):
        adm.server_save(None, {"title": "x"}, {"type": "unknown"})
    adm.category_delete(3)
    adm.server_delete(4)
    assert db.writes[-1] == "DELETE FROM server_info WHERE id = 4"


def test_discounts_gifts_broadcasts(db):
    db.answers = {"FROM discounts ORDER BY id DESC": [{"id": "5"}], "FROM gift_codes ORDER BY id DESC": [{"id": "6"}], "FROM send_list ORDER BY id DESC": [{"id": "8"}]}
    assert adm.discount_save(None, {"hash_id": "OFF-20!", "type": "percent", "amount": 20, "days": 7, "audience_type": "buyers", "target_type": "all", "can_use": 2}) == 5
    assert 'INSERT INTO discounts (hash_id, type, amount' in db.writes[-1] and adm.lit("OFF-20") in db.writes[-1]
    with pytest.raises(ValueError):
        adm.discount_save(None, {"hash_id": "X", "type": "percent", "amount": 150})
    with pytest.raises(ValueError):
        adm.discount_save(None, {"hash_id": "X", "audience_type": "martians"})

    assert adm.gift_save("WELCOME", 50000, 10, 30, "spring") == 6
    assert "INSERT INTO gift_codes (code, amount, max_uses, used, used_by, expires, created, note)" in db.writes[-1] and ", 50000, 10, 0, \"[]\"," in db.writes[-1]
    with pytest.raises(ValueError):
        adm.gift_save("X", 0)

    assert adm.broadcast_create("Hi", "سلام به همه 👋\nخط دوم", "agents") == 8
    assert db.writes[-1].startswith('INSERT INTO send_list (type, title, text, target_group, state, created_at) VALUES ("text",') and ", 1, " in db.writes[-1]
    with pytest.raises(ValueError):
        adm.broadcast_create("t", "x", "everyone")
    adm.broadcast_cancel(8)
    assert "UPDATE send_list SET state = 4 WHERE id = 8" in db.writes[-1]


def test_tickets_reply_writes_and_sends(db):
    db.answers = {"FROM chats WHERE id = 2": [{"id": "2", "user_id": "77", "title": "cannot connect"}]}
    r = adm.ticket_reply(2, "try the other location")
    assert r["sent"] and 'VALUES (2, ' in db.writes[-1] and '"ADMIN"' in db.writes[-1]
    assert db.tg[-1][1]["chat_id"] == "77" and "try the other location" in db.tg[-1][1]["text"]
    adm.ticket_close(2)
    assert "UPDATE chats SET state = 2 WHERE id = 2" in db.writes[-1]


def test_settings_documents_go_as_one_base64_json(db):
    db.answers = {'type = "BOT_STATES"': [{"id": "1", "value": json.dumps({"sellState": "on", "weird": "x"})}], 'type = "PAYMENT_KEYS"': []}
    d = adm.states_structured()
    assert d["groups"][0]["title"] == "Sales" and {i["key"]: i["value"] for i in d["groups"][0]["items"]}["sellState"] == "on" and d["other"] == {"weird": "x"}
    adm.states_set({"sellState": "off", "supportID": "@help"})
    doc = json.loads(base64.b64decode(re.search(r'FROM_BASE64\("([^"]+)"\)', db.writes[-1]).group(1)))
    assert doc["sellState"] == "off" and doc["supportID"] == "@help" and doc["weird"] == "x" and db.writes[-1].startswith("UPDATE setting SET value")
    with pytest.raises(ValueError):
        adm.states_set({"bad key": "1"})
    adm.payment_keys_set({"bankAccount": "6037 9977 1234 5678", "holderName": "H. Vi", "zarinpal": ""})
    assert db.writes[-1].startswith('INSERT INTO setting (type, value) VALUES ("PAYMENT_KEYS"')
    with pytest.raises(ValueError):
        adm.payment_keys_set({"paypal": "x"})


def test_texts_are_read_from_php_and_overrides_written_as_json(monkeypatch):
    calls = []
    defaults = {"texts": {"banned": "❌ شما مسدود شدید.", "hello": "hi"}, "buttons": {"cancel": "لغو"}}

    def fake_script(node, name, params=None, timeout=40):
        assert name == "bot_texts"
        return "DEFAULTS " + json.dumps(defaults, ensure_ascii=False) + "\nTEXT_OVERRIDES {\"hello\": \"salam\"}\nBUTTON_OVERRIDES {}\nHOOKED 0\n"

    def fake_action(node, name, params=None, timeout=90):
        calls.append((name, dict(params or {})))
        return "WROTE\n"

    monkeypatch.setattr(adm, "run_script", fake_script)
    monkeypatch.setattr(adm, "run_action", fake_action)
    monkeypatch.setattr(adm, "_event", lambda *a, **k: None)
    monkeypatch.setattr(shopbot, "conf", lambda: {"host": "9.9.9.9", "dir": "/var/www/nikabot", "env": "/opt/nikabot/db.env", "ssh_user": "root", "ssh_port": 22})
    d = adm.texts()
    assert d["texts"]["hello"] == "hi" and d["text_overrides"] == {"hello": "salam"} and d["hooked"] is False
    r = adm.texts_set("text", {"banned": "⛔ مسدود", "hello": ""})
    assert r["overrides"] == {"banned": "⛔ مسدود"}                    # an empty value removes an override
    put = next(c for c in calls if c[0] == "bot_put_json")
    assert put[1]["PATH"] == "/var/www/nikabot/settings/text_overrides.json" and json.loads(base64.b64decode(put[1]["B64"])) == {"banned": "⛔ مسدود"}
    assert any(c[0] == "bot_texts_hook" for c in calls)                 # the bot was not reading the file yet: it is now
    with pytest.raises(ValueError):
        adm.texts_set("text", {"nope": "x"})


def test_admin_api_routes(client, monkeypatch, db):
    client.put("/api/settings/shopbot", json={"host": "9.9.9.9"})
    pay = {"id": "1", "hash_id": "h", "user_id": "5", "price": "1000", "state": "pending", "chat_id": "", "message_id": "", "admins_messages": ""}
    db.answers = {"SELECT p.id, p.hash_id": lambda: [{"id": "1", "user_id": "5", "name": "A", "price": "1000", "state": "pending"}], "COUNT(*) AS n FROM pays": [{"n": "1"}],
                  "FROM pays WHERE id = 1": [pay], "COALESCE(wallet, 0) AS w": [{"w": "0"}], "FROM needed_sofwares ORDER BY id DESC": [{"id": "2"}]}
    r = client.get("/api/shopbot/pays?state=pending").json()
    assert r["total"] == 1 and r["rows"][0]["name"] == "A"
    assert client.post("/api/shopbot/pays/1/approve").json()["wallet"] == 1000
    pay["state"] = "approved"
    assert client.post("/api/shopbot/pays/1/decline", json={"reason": "x"}).status_code == 400           # settled once, never again
    assert client.post("/api/shopbot/apps", json={"title": "v2rayNG", "link": "https://example.com/app"}).status_code == 201
    assert client.post("/api/shopbot/apps", json={"title": "x", "link": "ftp://nope"}).status_code == 400
    assert client.post("/api/shopbot/broadcasts", json={"text": "hi", "group": "nobody"}).status_code == 400
    monkeypatch.setattr(adm, "run_script", lambda node, name, params=None, timeout=40: "BACKUP manual-20261010-120000.sql.gz 2048\n")
    b = client.get("/api/shopbot/backups").json()["backups"]
    assert b[0]["name"] == "manual-20261010-120000.sql.gz" and b[0]["size"] == 2048 and b[0]["at"] > 0


def test_miniapp_status_compares_the_bot_server_with_the_repository(monkeypatch):
    local = {n: adm._fingerprint((adm.miniapp_dir() / n).read_bytes()) for n in adm.MINIAPP_FILES}
    assert len(local) == 4 and adm._version_in((adm.miniapp_dir() / "index.html").read_text(encoding="utf-8"))    # the repository ships the four files
    out = "\n".join(f"FILE {n} 1000 {h}" for n, h in local.items()) + "\nVERSION 2.0.0\nPHP_OK\nURL https://shop.example/bot/\n"
    st = adm.parse_miniapp_info(out, local)
    assert st["installed"] and st["up_to_date"] and st["php_ok"] and st["url"] == "https://shop.example/bot/app/" and st["version"] == "2.0.0"
    st = adm.parse_miniapp_info("FILE api.php 900 0000000000000000\nMISSING app.css\nPHP_BAD\nERRORS 3 miniapp buy: panel refused\n", local)
    assert st["installed"] and not st["up_to_date"] and st["php_ok"] is False and st["errors"] == 3 and "panel refused" in st["last_error"]
    assert [f["current"] for f in st["files"]] == [False, False]
    assert not adm.parse_miniapp_info("NOT_INSTALLED\n", local)["installed"]


def test_miniapp_deploy_sends_every_file_through_the_reviewed_action(monkeypatch, db):
    calls = []

    def fake_action(node, name, params=None, timeout=90):
        calls.append((name, dict(params or {})))
        return f"WROTE {params['PATH']} 1\n"

    monkeypatch.setattr(adm, "run_action", fake_action)
    monkeypatch.setattr(adm, "run_script", lambda node, name, params=None, timeout=40: "FILE api.php 1 x\nVERSION 2.0.0\nPHP_OK\n")
    monkeypatch.setattr(shopbot, "conf", lambda: {"host": "9.9.9.9", "dir": "/var/www/mybot", "env": "/opt/mybot/db.env", "ssh_user": "root", "ssh_port": 22})
    st = adm.miniapp_deploy()
    rules = ssh.ACTIONS["bot_miniapp_put"][1]
    assert [c[0] for c in calls] == ["bot_miniapp_put"] * 4
    for _, p in calls:
        assert re.match(rules["PATH"], p["PATH"]) and re.match(rules["B64"], p["B64"]), p["PATH"]
        name = p["PATH"].rsplit("/", 1)[1]
        assert base64.b64decode(p["B64"]) == (adm.miniapp_dir() / name).read_bytes()
    assert st["installed"] and st["version"] == "2.0.0"
