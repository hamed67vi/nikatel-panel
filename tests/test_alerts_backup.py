import json
import time

import pytest

from nikatel.config import get_settings
from nikatel.services import backup, report
from nikatel.services import settings as st
from nikatel.services.alerts import Rules, Track, decide, in_maintenance
from nikatel.services.importer import import_seed_dir
from nikatel.services.telegram import valid_chat, valid_token

R = Rules(fails=3, up_stable_seconds=600, remind_seconds=3600)


def test_decide_needs_n_failures_then_one_down():
    t = Track()
    t, v1 = decide(t, True, 1000, R)
    t, v2 = decide(t, True, 1060, R)
    t, v3 = decide(t, True, 1120, R)
    t, v4 = decide(t, True, 1180, R)
    assert (v1, v2, v3, v4) == (None, None, "down", None) and t.alerted and t.bad_since == 1000


def test_decide_flap_does_not_alert():
    t = Track()
    for i in range(6):   # bad, good, bad, good …
        t, v = decide(t, i % 2 == 0, 1000 + i * 60, R)
        assert v is None


def test_decide_up_only_after_stable_and_reminders():
    t = Track(fails=3, alerted=True, bad_since=0, last_notice=0)
    t, v = decide(t, True, 3700, R)
    assert v == "remind"
    t, v = decide(t, False, 4000, R)
    assert v is None and not t.alerted is False
    t, v = decide(t, False, 4300, R)
    assert v is None
    t, v = decide(t, False, 4700, R)
    assert v == "up" and t.alerted is False and t.fails == 0


def test_decide_waits_for_the_minimum_down_time_and_never_reminds_when_off():
    rules = Rules(fails=1, up_stable_seconds=60, remind_seconds=0, min_bad_seconds=240)
    t = Track()
    t, v1 = decide(t, True, 1000, rules)           # one failed check, but only 0 s down
    t, v2 = decide(t, True, 1120, rules)           # 2 min
    t, v3 = decide(t, True, 1250, rules)           # 4 min 10 s -> now it counts
    assert (v1, v2, v3) == (None, None, "down") and t.bad_since == 1000
    t, v = decide(t, True, 90000, rules)           # down for a day: no reminder, reminders are off
    assert v is None
    t, v = decide(t, False, 90100, rules)
    t, v = decide(t, False, 90200, rules)
    assert v == "up"
    # a blip: bad for 2 minutes, then good again -> not a word
    t, v = decide(Track(), True, 2000, rules); t, v2 = decide(t, True, 2100, rules); t, v3 = decide(t, False, 2200, rules); t, v4 = decide(t, False, 2300, rules)
    assert (v, v2, v3, v4) == (None, None, None, None) and not t.alerted


def test_decide_flapping_guard_says_it_once_then_keeps_quiet():
    rules = Rules(fails=1, up_stable_seconds=0, remind_seconds=0, min_bad_seconds=0, flap_max=3, flap_window=3600, flap_quiet=6 * 3600)
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
    r = client.put("/api/settings/alerts", json={"tunnel_fails": 4, "server_fails": 2, "up_stable_seconds": 300, "remind_hours": 2})
    assert r.status_code == 200 and r.json()["tunnel_fails"] == 4 and r.json()["flap_max"] == 3 and r.json()["min_down_minutes"] == 4
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
