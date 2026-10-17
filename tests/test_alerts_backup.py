import json
import time

import pytest

from nikatel.config import get_settings
from nikatel.services import backup, report
from nikatel.services import settings as st
from nikatel.services.alerts import Rules, Track, decide, in_maintenance
from nikatel.services.importer import import_seed_dir
from nikatel.services.telegram import valid_chat, valid_token

R = Rules(down_seconds=10, up_seconds=15, remind_seconds=3600)


def test_decide_says_down_once_after_down_seconds_and_counts_from_the_first_bad_look():
    t = Track()
    t, v1 = decide(t, True, 1000, R)
    t, v2 = decide(t, True, 1005, R)
    t, v3 = decide(t, True, 1010, R)               # 10 s bad: now
    t, v4 = decide(t, True, 1015, R)
    assert (v1, v2, v3, v4) == (None, None, "down", None) and t.alerted and t.bad_since == 1000


def test_decide_a_blip_shorter_than_down_seconds_says_nothing():
    t = Track()
    for i in range(8):   # bad 5 s, good 5 s, bad 5 s, good ...
        t, v = decide(t, i % 2 == 0, 1000 + i * 5, R)
        assert v is None and not t.alerted


def test_decide_the_clock_starts_where_the_checker_changed_the_state():
    """A tunnel the checker called down 7 s before our look is told 3 s later, not 10."""
    t, v = decide(Track(), True, 1007, R, since=1000)
    assert v is None and t.bad_since == 1000
    t, v = decide(t, True, 1010, R, since=1000)
    assert v == "down"
    # the same for the way back: it has been up since 2000, our first look is at 2012
    t, v = decide(t, False, 2012, R, since=2000)
    assert v is None and t.good_since == 2000
    t, v = decide(t, False, 2015, R, since=2000)
    assert v == "up" and t.alerted is False
    # a `since` in the future (a clock that jumped) is not believed
    t, v = decide(Track(), True, 3000, R, since=3600)
    assert t.bad_since == 3000


def test_decide_up_only_after_it_held_and_reminders():
    t = Track(fails=1, alerted=True, bad_since=0, last_notice=0)
    t, v = decide(t, True, 3700, R)
    assert v == "remind"
    t, v = decide(t, False, 4000, R)
    assert v is None and t.alerted is True
    t, v = decide(t, True, 4005, R)                 # it fell again before it held: no UP, and no second DOWN (it is still "down" for the reader)
    assert v is None and t.alerted is True and t.good_since == 0
    t, v = decide(t, False, 4010, R)
    t, v = decide(t, False, 4020, R)
    assert v is None
    t, v = decide(t, False, 4025, R)
    assert v == "up" and t.alerted is False and t.fails == 0


def test_decide_never_reminds_when_off_and_down_seconds_zero_is_at_once():
    rules = Rules(down_seconds=0, up_seconds=10, remind_seconds=0)
    t, v1 = decide(Track(), True, 1000, rules)          # 0 s: the very first bad look
    assert v1 == "down" and t.bad_since == 1000
    t, v = decide(t, True, 90000, rules)           # down for a day: no reminder, reminders are off
    assert v is None
    t, v = decide(t, False, 90100, rules)
    t, v = decide(t, False, 90110, rules)
    assert v == "up"


def test_decide_does_not_write_the_track_again_while_nothing_changes():
    """The look is every 5 s: a thing that stays down must not change its row on every look."""
    t, _ = decide(Track(), True, 1000, R)
    t2, _ = decide(t, True, 1005, R)
    assert t2 == t
    t, _ = decide(t, True, 1010, R)
    t2, _ = decide(t, True, 1015, R)
    assert t2 == t


def test_decide_flapping_guard_says_it_once_then_keeps_quiet():
    rules = Rules(down_seconds=0, up_seconds=0, remind_seconds=0, flap_max=3, flap_window=3600, flap_quiet=6 * 3600)
    t, out = Track(), []
    for i in range(10):                             # down / up every 5 minutes for 50 minutes
        t, v = decide(t, i % 2 == 0, 1000 + i * 300, rules)
        out.append(v)
    assert out[:4] == ["down", "up", "down", "up"]
    assert out[4] == "flapping" and t.quiet_until == 1000 + 4 * 300 + 6 * 3600
    assert all(v is None for v in out[5:])         # quiet: no more DOWN / UP about it
    t, v = decide(t, True, t.quiet_until + 10, rules)
    assert v == "down" and t.flips == 1            # the quiet period is over and the window started again


def test_maintenance_window():
    assert in_maintenance(100, [(50, 150)]) and not in_maintenance(200, [(50, 150)])


def test_telegram_validators():
    assert valid_token("123456789:" + "A" * 35) and not valid_token("hello") and not valid_token("")
    assert valid_chat("-1001234567890") and valid_chat("@mychannel") and not valid_chat("hi there")


def test_settings_keep_secret_when_blank_and_public_masks():
    st.put("telegram", {"token": "123456789:" + "B" * 35, "chat_id": "42424242", "enabled": True})
    st.put("telegram", {"token": "", "chat_id": "42424242"})
    d = st.get("telegram")
    assert d["token"].startswith("123456789:") and d["enabled"] is True
    p = st.public("telegram")
    assert p["token"] == "" and p["has_token"] is True

    with pytest.raises(ValueError):
        st.put("telegram", {"bogus": 1})


def test_settings_api_masks_token(client):
    r = client.put("/api/settings/telegram", json={"enabled": False, "token": "123456789:" + "C" * 35, "chat_id": "@chan"})
    assert r.status_code == 200 and r.json()["has_token"] is True and "CCCC" not in r.text
    assert client.put("/api/settings/telegram", json={"token": "bad"}).status_code == 400
    assert client.get("/api/settings/telegram").json()["token"] == ""


def test_alert_rules_mute_and_maintenance_api(client):
    d = client.get("/api/settings/alerts").json()
    assert d["down_seconds"] == 10 and d["up_seconds"] == 15 and d["remind_hours"] == 0         # the defaults: a fault is told after 10 s, a repair after 15 s
    r = client.put("/api/settings/alerts", json={"down_seconds": 20, "up_seconds": 30, "remind_hours": 2})
    assert r.status_code == 200 and r.json()["down_seconds"] == 20 and r.json()["up_seconds"] == 30 and r.json()["flap_max"] == 3
    assert not any(k in r.json() for k in ("tunnel_fails", "server_fails", "config_fails", "min_down_minutes", "up_stable_seconds"))
    assert client.put("/api/settings/alerts", json={"down_seconds": -1}).status_code == 422 and client.put("/api/settings/alerts", json={"up_seconds": 99999}).status_code == 422
    assert client.put("/api/settings/alerts", json={"remind_hours": 0, "flap_max": 0}).status_code == 200      # reminders and the guard can be switched off
    assert client.put("/api/settings/alerts", json={"remind_hours": -1}).status_code == 422
    assert client.post("/api/settings/alerts/mute?ref=t1").json()["muted"] == ["t1"]
    assert client.post("/api/settings/alerts/mute?ref=t1&muted=false").json()["muted"] == []
    now = int(time.time())
    assert client.post("/api/maintenance", json={"start": now, "end": now - 1}).status_code == 400
    mid = client.post("/api/maintenance", json={"start": now - 60, "end": now + 3600, "note": "upgrade"}).json()["id"]
    w = client.get("/api/maintenance").json()["windows"]
    assert w[0]["active"] is True and w[0]["note"] == "upgrade"
    assert client.delete(f"/api/maintenance/{mid}").status_code == 200


def test_report_builds_without_telegram(client, tmp_path):
    (tmp_path / "iran-servers.json").write_text(json.dumps([{"name": "ir1", "host": "1.1.1.1"}]))
    (tmp_path / "foreign-servers.json").write_text(json.dumps([{"name": "fi1", "host": "2.2.2.2"}]))
    (tmp_path / "tunnels.json").write_text(json.dumps({"tunnels": [{"name": "t1", "type": "backhaul", "iran_host": "1.1.1.1", "foreign_host": "2.2.2.2"}]}))
    import_seed_dir(tmp_path)
    text = report.build()
    assert "Tunnels: 0/1 up" in text and "Servers: 2/2" in text
    r = client.get("/api/report/preview")
    assert r.status_code == 200 and "Daily report" in r.json()["text"]


def test_backup_roundtrip_and_restore(client, tmp_path):
    s = get_settings()
    assert client.post("/api/backup/now").status_code == 400   # no passphrase yet
    assert client.post("/api/settings/backup/passphrase", json={"passphrase": "short"}).status_code == 422
    assert client.post("/api/settings/backup/passphrase", json={"passphrase": "a-long-test-passphrase"}).status_code == 200

    r = client.post("/api/backup/now?send=false")
    assert r.status_code == 200, r.text
    name = r.json()["file"]
    assert client.get("/api/settings/backup").json()["backups"][0]["file"] == name
    assert client.get(f"/api/backup/download/{name}").status_code == 200
    assert client.get("/api/backup/download/../etc/passwd").status_code in (404, 422)

    blob = (s.data_dir.parent / "backups" / name).read_bytes()
    with pytest.raises(RuntimeError):
        backup.decrypt(blob, "wrong-passphrase-here")

    target = tmp_path / "restored"
    files = backup.restore(s.data_dir.parent / "backups" / name, "a-long-test-passphrase", data_dir=target)
    assert "nikatel.db" in files and "auth.json" in files and (target / "nikatel.db").stat().st_size > 0


def test_alerts_run_with_new_config_state(client):
    """A config that is down on its very first alert evaluation must not crash the job (fails was None)."""
    from nikatel.db import session
    from nikatel.models import Config
    from nikatel.services import alerts

    with session() as db:
        db.add(Config(name="x", status="down", status_since=1, last_error="timeout"))

    alerts.run()
    alerts.run()


def test_backup_parts_each_go_to_their_own_topic(client, monkeypatch):
    from nikatel.services import telegram

    assert client.post("/api/settings/backup/passphrase", json={"passphrase": "a-long-test-passphrase"}).status_code == 200
    sent = []
    monkeypatch.setattr(backup, "send_file", lambda name, data, caption="", thread_id=0: sent.append((name.split("-")[1], thread_id)) or (True, "sent"))
    r = client.post("/api/backup/now").json()                                   # the bot is not set up: only the panel is made, the bot says why
    assert r["ok"] and r["parts"]["panel"]["ok"] is True and r["parts"]["bot"]["ok"] is False and sent == [("panel", 0)]
    monkeypatch.setattr(backup, "make_bot_archive", lambda: b"\x1f\x8bbot-dump")
    monkeypatch.setattr(telegram, "create_topic", lambda name: (11 if name.startswith("Panel") else 22, ""))
    v = client.post("/api/backup/topic/panel").json()
    assert v["parts"]["panel"]["topic"] == 11 and v["parts"]["panel"]["topic_name"] == "Panel backups"
    assert client.post("/api/backup/topic/bot").json()["parts"]["bot"]["topic"] == 22
    sent.clear()
    r = client.post("/api/backup/now").json()
    assert sorted(sent) == [("bot", 22), ("panel", 11)] and r["parts"]["bot"]["ok"] is True
    files = client.get("/api/settings/backup").json()
    assert {b["part"] for b in files["backups"]} == {"panel", "bot"} and files["parts"]["bot"]["last"]["file"].startswith("nikatel-bot-")
    sent.clear()
    assert client.post("/api/backup/now?part=bot").json()["parts"].keys() == {"bot"} and sent == [("bot", 22)]
    assert client.delete("/api/backup/topic/panel").json()["parts"]["panel"]["topic"] == 0
    assert client.post("/api/settings/backup/part/bot", json={"enabled": False}).json()["parts"]["bot"]["enabled"] is False
    sent.clear()
    client.post("/api/backup/now")
    assert sent == [("panel", 0)]                                              # the switched-off part is left out
    assert client.post("/api/backup/topic/nope").status_code == 404 and client.post("/api/backup/now?part=nope").status_code == 400


def test_telegram_topic_creation_reports_why(monkeypatch):
    from nikatel.services import telegram

    st.put("telegram", {"token": "123456789:" + "D" * 35, "chat_id": "-1001234567890", "enabled": True})

    class R:
        status_code = 400

        def json(self):
            return {"ok": False, "description": "Bad Request: the chat is not a forum"}

    monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k: R())
    tid, why = telegram.create_topic("Panel backups")
    assert tid == 0 and "not a forum" in why and "Topics" in why

def test_backup_runs_every_n_minutes_and_sends_to_telegram_once_a_day(client, monkeypatch):
    import time as _t

    from nikatel.services import backup as bk

    st.put("backup", {"enabled": True, "every_minutes": 15, "hour": 3, "send_telegram": True, "tg_every": "daily"})
    calls = []
    monkeypatch.setattr(bk, "run", lambda send=None, part="": calls.append(send) or {"ok": True})
    monkeypatch.setattr(bk, "local_day", lambda now, tz: ("2026-10-11", 9), raising=False)
    monkeypatch.setattr("nikatel.services.report.local_day", lambda now, tz: ("2026-10-11", 9))
    bk.scheduled()
    assert calls == [True]                                   # the first run of the day after 03:00 goes to Telegram
    bk.scheduled()
    assert calls == [True]                                   # 15 minutes have not passed: nothing
    bk._mark("backup_last_run", {"at": int(_t.time()) - 16 * 60})
    bk.scheduled()
    assert calls == [True, False]                            # the next ones in the day are kept locally only
    st.put("backup", {"tg_every": "all"})
    bk._mark("backup_last_run", {"at": int(_t.time()) - 16 * 60})
    bk.scheduled()
    assert calls[-1] is True
    assert client.put("/api/settings/backup", json={"every_minutes": 15, "tg_every": "daily"}).json()["every_minutes"] == 15
    assert client.put("/api/settings/backup", json={"tg_every": "sometimes"}).status_code == 422

def test_restore_from_the_panel_stages_then_swaps_at_start(client, tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace

    d = tmp_path / "data"
    d.mkdir()
    (d / "nikatel.db").write_bytes(b"old")
    monkeypatch.setattr(backup, "get_settings", lambda: SimpleNamespace(data_dir=d, read_only=False))
    sql = "BEGIN TRANSACTION; CREATE TABLE a(x); CREATE TABLE b(x); CREATE TABLE c(x); INSERT INTO a VALUES(1); COMMIT;"
    backup.stage_panel({"nikatel.sql": sql.encode(), "secret.key": b"k", "backup.pass": b"nope", "evil": b"x"})
    pend = d / "restore-pending"
    assert (pend / "nikatel.sql").exists() and (pend / "secret.key").exists()
    assert not (pend / "backup.pass").exists() and not (pend / "evil").exists()      # only the known files are staged, never the passphrase
    assert backup.apply_pending_restore() == "restored"
    assert sqlite3.connect(str(d / "nikatel.db")).execute("SELECT x FROM a").fetchone() == (1,)
    assert (d / "secret.key").read_bytes() == b"k" and not pend.exists()
    assert backup.apply_pending_restore() == ""                                      # nothing staged: nothing happens

    backup.stage_panel({"nikatel.sql": b"THIS IS NOT SQL"})                          # a bad file: the panel keeps what it had and says so
    assert backup.apply_pending_restore() == "failed"
    assert sqlite3.connect(str(d / "nikatel.db")).execute("SELECT x FROM a").fetchone() == (1,)
    assert (d / "restore-failed").is_dir()


def test_restore_reads_only_real_backups(client, tmp_path, monkeypatch):
    import io
    import tarfile
    from types import SimpleNamespace

    pw = "a-long-test-passphrase"
    s = get_settings()
    backup.set_passphrase(pw)
    blob = backup.encrypt(backup.make_archive(), pw)
    part, files = backup.read_archive(blob)
    assert part == "panel" and "nikatel.sql" in files
    assert backup.read_archive(blob, pw)[0] == "panel"                                # the passphrase of the file may be given by hand

    with pytest.raises(RuntimeError):
        backup.read_archive(blob, "another-long-passphrase")

    buf = io.BytesIO()

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("notes.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))

    with pytest.raises(ValueError):
        backup.read_archive(backup.encrypt(buf.getvalue(), pw))

    monkeypatch.setattr(backup, "get_settings", lambda: SimpleNamespace(data_dir=s.data_dir, read_only=True))

    with pytest.raises(ValueError):
        backup.restore_blob(blob)                                                    # the read-only panel never restores