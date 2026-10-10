import re

import pytest

from nikatel import ssh
from nikatel.services import shopbot
from nikatel.services.shopbot import parse_info

INFO = """INSTALLED 1
UNIT php8.3-fpm active
UNIT mariadb active
UNIT caddy active
CRON 4
COUNTS 15583 2962 3 120000 58 9
STAGE 15583
STATES {"cashbackPercent":"5","wheelState":"on"}
PANELS 1|marzban|p.example 2|pasarguard|q.example
TODAY 12 3400000
WEEK 80 21000000
MONTH 300 90000000
NEWUSERS 4 31
BACKUPS before-import-1.sql.gz manual-2.sql.gz
TICKETS 3
QUEUE 1
HOOKED 0
LOG_ERRORS
[09-Oct] something failed
LOG_PHP
LOG_END
"""


def test_parse_info():
    d = parse_info(INFO)
    assert d["installed"] and d["units"]["mariadb"] == "active" and d["cron"] == 4
    assert d["counts"]["users"] == 15583 and d["counts"]["panels"] == 9 and d["stage_users"] == 15583
    assert d["states"]["wheelState"] == "on" and d["panels"][1]["type"] == "pasarguard"
    assert d["sales"]["week"] == {"orders": 80, "income": 21000000} and d["sales"]["new_users_week"] == 31
    assert d["backups"] == ["before-import-1.sql.gz", "manual-2.sql.gz"] and d["logs"]["errors"] == ["[09-Oct] something failed"]
    assert d["tickets"] == 3 and d["queue"] == 1 and d["texts_hooked"] is False


def test_sql_scripts_accept_only_safe_statements():
    sel = ssh.SCRIPTS["bot_query"][1]["SQL"]
    assert re.match(sel, "'SELECT * FROM users LIMIT 10'")
    assert not re.match(sel, "'SELECT 1; DROP TABLE users'")
    assert not re.match(sel, "'UPDATE users SET wallet = 0'")
    assert not re.match(sel, "'SELECT \\'x\\''")
    upd = ssh.ACTIONS["bot_sql"][1]["SQL"]
    assert re.match(upd, "'UPDATE users SET wallet = 5 WHERE userid = '")  # the quotes inside are stripped by _q
    assert not re.match(upd, "'DROP TABLE users'")
    assert not re.match(upd, "'UPDATE x SET a = 1; DELETE FROM y'")
    assert shopbot._q("it's; `x` \\") == "'its x '"


def test_info_users_and_import_through_api(client, monkeypatch):
    calls = []

    def fake_script(node, name, params=None, timeout=40):
        calls.append(("script", name, dict(params or {})))

        if name == "bot_info":
            return INFO
        if name == "bot_query":
            sql = params["SQL"]
            if "FROM users u WHERE" in sql:
                return "id\tuserid\tname\twallet\n1\t555\tAli\t1000\n"
            if "SELECT 1 AS x" in sql:
                return "x\n1\n"
            if "discount_percent FROM users" in sql:
                return "discount_percent\n{\"normal\": 5, \"plans\": {\"3\": 20}}\n"
            if "SELECT userid, name" in sql:
                return "userid\tname\twallet\n555\tAli\t6000\n"
            if "BOT_STATES" in sql:
                return "value\n{\"a\":\"1\"}\n"
            return ""
        if name == "bot_tg_get":
            return '{"ok":true,"result":{"username":"shop_bot","url":"https://x/bot.php","pending_update_count":0}}'
        return ""

    def fake_action(node, name, params=None, timeout=90):
        calls.append(("action", name, dict(params or {})))

        if name == "bot_backup":
            return "BACKUP /opt/nikabot/backups/before-import-1.sql.gz 2048\n"
        if name == "bot_import_stage":
            return "TABLE users 15583\nTABLE orders_list 2962\nIMPORTED\n"
        return "DONE\n"

    monkeypatch.setattr(shopbot, "run_script", fake_script)
    monkeypatch.setattr(shopbot, "run_action", fake_action)

    # nothing works before the bot server is configured
    assert client.get("/api/shopbot").status_code == 400
    r = client.put("/api/settings/shopbot", json={"host": "9.9.9.9", "public_url": "https://bot.example/x/bot.php"})
    assert r.status_code == 200 and r.json()["host"] == "9.9.9.9"
    assert client.put("/api/settings/shopbot", json={"host": "9.9.9.9", "dir": "/etc/passwd"}).status_code == 400

    d = client.get("/api/shopbot").json()
    assert d["counts"]["users"] == 15583 and d["public_url"] == "https://bot.example/x/bot.php"
    tg = client.get("/api/shopbot/telegram").json()
    assert tg["bot"]["username"] == "shop_bot" and tg["webhook"]["url"] == "https://x/bot.php"

    r = client.get("/api/shopbot/users?q=Ali").json()
    assert r["users"][0]["userid"] == "555"
    r = client.post("/api/shopbot/users/action", json={"action": "wallet", "userid": "555", "value": 5000})
    assert r.status_code == 200 and r.json()["user"]["wallet"] == "6000"
    assert any(c[1] == "bot_sql" and "wallet = GREATEST" in c[2]["SQL"] for c in calls)
    r = client.post("/api/shopbot/users/action", json={"action": "discount", "userid": "555", "value": 15})
    assert r.status_code == 200
    sql = next(c[2]["SQL"] for c in calls if c[1] == "bot_sql" and "discount_percent = FROM_BASE64" in c[2]["SQL"])
    import base64 as _b
    doc = __import__("json").loads(_b.b64decode(sql.split('FROM_BASE64("')[1].split('")')[0]))
    assert doc == {"normal": 15, "plans": {"3": 20}}              # the other keys the bot keeps are untouched
    assert client.post("/api/shopbot/users/action", json={"action": "drop", "userid": "555"}).status_code == 400
    assert client.post("/api/shopbot/users/action", json={"action": "ban", "userid": "x;y"}).status_code == 400

    r = client.put("/api/shopbot/states", json={"changes": {"wheelState": "off"}})
    assert r.status_code == 200 and r.json()["states"] == {"a": "1", "wheelState": "off"}
    assert client.put("/api/shopbot/states", json={"changes": {"bad key!": "1"}}).status_code == 400

    r = client.post("/api/shopbot/query", json={"sql": "DELETE FROM users"})
    assert r.status_code == 400

    r = client.post("/api/shopbot/import-stage")
    assert r.status_code == 200 and r.json()["tables"]["users"] == 15583 and r.json()["backup"].endswith(".sql.gz")
    names = [c[1] for c in calls if c[0] == "action"]
    assert names.index("bot_backup") < names.index("bot_import_stage")          # dump first, then import


def test_import_refuses_without_import_marker(client, monkeypatch):
    monkeypatch.setattr(shopbot, "run_action", lambda node, name, params=None, timeout=90: "BACKUP /b.sql.gz 1\n" if name == "bot_backup" else "ERROR 1049 unknown database\n")
    client.put("/api/settings/shopbot", json={"host": "9.9.9.9"})
    r = client.post("/api/shopbot/import-stage")
    assert r.status_code == 502 and "import failed after backup" in r.json()["detail"]


def test_unknown_table_and_settings_validation(client):
    client.put("/api/settings/shopbot", json={"host": "9.9.9.9"})
    with pytest.raises(ValueError):
        shopbot.table("information_schema.tables")
    assert client.put("/api/settings/shopbot", json={"host": "9.9.9.9", "public_url": "http://plain"}).status_code == 400
