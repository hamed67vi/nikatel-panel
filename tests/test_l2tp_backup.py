"""The L2TP backup and restore (1.69.248; the owner, 2026-11-08: "a backup and restore system for the L2TP section, and its backup sent to a section of that group every hour").
Nothing here touches a server: the entry's answers are canned, and the two reviewed scripts (the read of the accounts and the restore) are run for real in a folder of their own against a small stand-in for the manager."""
import base64
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from nikatel import ssh
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server
from nikatel.services import backup, l2tp, telegram
from nikatel.services import settings as st

PW = "a-long-test-passphrase"
NOW = 1_800_000_000


def _acc(**kw):
    a = {"password": "pass1234", "limit": 2, "expires": NOW + 86400, "created": 1, "note": "order 7 user 9", "rx": 10, "tx": 20}
    a.update(kw)
    return a


USERS = {"alice": _acc(), "l2_7": _acc(password="seven777", limit=1, quota=5_000_000_000, speed={"down": 3.0, "up": 3.0})}


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _out(users=None, psk="nikatel-demo-key", pool="10.250.0.0/22") -> str:
    return f"L2TP ok\nACCOUNTS {_b64(json.dumps({'users': USERS if users is None else users}))}\nPSK {_b64(psk)}\nPOOL {_b64(pool)}\nDONE\n"


@pytest.fixture(autouse=True)
def _own_files():
    """The backup password and the backup files are files in the data folder, which the tests share: these tests start without any and leave none behind (test_alerts_backup counts the files and starts without a password, too)."""
    def clean():
        backup.pass_file().unlink(missing_ok=True)

        for f in (get_settings().data_dir.parent / "backups").glob("nikatel-*.tar.gz.enc"):
            f.unlink()

    clean()
    yield
    clean()


@pytest.fixture
def writable(monkeypatch):
    """The test panel is read-only on purpose; a restore is refused there, so the ones that are about the restore open it."""
    s = get_settings()
    monkeypatch.setattr(backup, "get_settings", lambda: SimpleNamespace(data_dir=s.data_dir, read_only=False))


@pytest.fixture
def entry(monkeypatch):
    """An L2TP entry is chosen (a server row is not needed: the read is replaced) and answers with USERS."""
    backup.set_passphrase(PW)
    st.put("l2tp", {"entry_id": 1})
    monkeypatch.setattr(l2tp, "backup_data", lambda: l2tp.parse_backup(_out()))


# ---------------------------------------------------------------- the read of the entry
def test_the_backup_read_is_parsed_and_every_trouble_is_a_sentence():
    d = l2tp.parse_backup(_out())
    assert d["state"]["users"].keys() == USERS.keys() and d["psk"] == "nikatel-demo-key" and d["pool"] == "10.250.0.0/22"
    assert l2tp.parse_backup(_out({}))["state"]["users"] == {}                             # an entry with no account yet is a backup of nothing, not an error

    with pytest.raises(RuntimeError, match="not installed"):
        l2tp.parse_backup("L2TP none\n")

    with pytest.raises(RuntimeError, match="boom"):
        l2tp.parse_backup("L2TP ok\nERROR boom\nDONE\n")

    with pytest.raises(RuntimeError, match="could not be read"):
        l2tp.parse_backup("L2TP ok\nACCOUNTS !!!not-base64!!!\nDONE\n")

    with pytest.raises(RuntimeError, match="could not be read"):
        l2tp.parse_backup(f"L2TP ok\nACCOUNTS {_b64('[1, 2]')}\nDONE\n")                    # not the manager's file


def _run_read_script(tmp_path, accounts: str | None, manager=True) -> str:
    """The reviewed read, run for real with its four paths pointed at a folder of ours."""
    mgr = tmp_path / "nk-l2tp"
    acc, psk, pool = tmp_path / "accounts.json", tmp_path / "psk", tmp_path / "pool"
    psk.write_text("nikatel-demo-key\n")
    pool.write_text("10.250.0.0/22\n")

    if manager:
        mgr.write_text("#!/bin/sh\n")
        mgr.chmod(0o755)

    if accounts is not None:
        acc.write_text(accounts)

    script = ssh.SCRIPTS["l2tp_backup"][0]
    script = script.replace("/usr/local/sbin/nk-l2tp", str(mgr)).replace("/var/lib/nk-l2tp/accounts.json", str(acc)).replace("/etc/nk-l2tp/psk", str(psk)).replace("/etc/nk-l2tp/pool", str(pool))
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=False).stdout


def test_the_reviewed_read_gives_the_accounts_the_key_and_the_pool(tmp_path):
    d = l2tp.parse_backup(_run_read_script(tmp_path, json.dumps({"users": USERS})))
    assert d["state"]["users"]["alice"]["password"] == "pass1234" and d["psk"] == "nikatel-demo-key" and d["pool"] == "10.250.0.0/22"


def test_the_reviewed_read_knows_a_missing_file_a_broken_file_and_a_missing_manager(tmp_path):
    fresh, broken, gone = tmp_path / "a", tmp_path / "b", tmp_path / "c"

    for p in (fresh, broken, gone):
        p.mkdir()

    assert l2tp.parse_backup(_run_read_script(fresh, None))["state"]["users"] == {}         # a manager that has no account yet

    with pytest.raises(RuntimeError, match="could not be read"):
        l2tp.parse_backup(_run_read_script(broken, "{not json"))

    with pytest.raises(RuntimeError, match="not installed"):
        l2tp.parse_backup(_run_read_script(gone, "{}", manager=False))


# ---------------------------------------------------------------- the file
def test_the_archive_holds_the_accounts_and_is_read_back_as_the_l2tp_part(client, entry):
    blob = backup.encrypt(backup.make_l2tp_archive(), PW)
    part, files = backup.read_archive(blob)
    assert part == "l2tp" and set(files) == {"l2tp-accounts.json", "l2tp-info.json"}
    assert json.loads(files["l2tp-accounts.json"])["users"].keys() == USERS.keys()
    info = json.loads(files["l2tp-info.json"])
    assert info["accounts"] == 2 and info["psk"] == "nikatel-demo-key" and info["taken_at"] > 0
    assert backup.part_of("nikatel-l2tp-20261108-120000.tar.gz.enc") == "l2tp" and backup.part_of("nikatel-20261108-0300.tar.gz.enc") == "panel"


def test_a_file_that_looks_like_an_l2tp_backup_but_is_not_is_refused(client):
    import io
    import tarfile

    backup.set_passphrase(PW)

    def pack(files):
        buf = io.BytesIO()

        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for n, data in files.items():
                i = tarfile.TarInfo(n)
                i.size = len(data)
                tar.addfile(i, io.BytesIO(data))

        return backup.encrypt(buf.getvalue(), PW)

    with pytest.raises(ValueError, match="damaged"):
        backup.read_archive(pack({"l2tp-accounts.json": b"{oops"}))

    with pytest.raises(ValueError, match="not an L2TP backup"):
        backup.read_archive(pack({"l2tp-accounts.json": b'{"users": []}'}))


# ---------------------------------------------------------------- the part, its topic and its own hourly schedule
def _telegram(monkeypatch, topic=77, send=None):
    sent = []
    topics = []
    monkeypatch.setattr(telegram, "ready", lambda: True)
    monkeypatch.setattr(telegram, "create_topic", lambda name: topics.append(name) or ((topic, "") if topic else (0, "the chat is not a forum")))
    monkeypatch.setattr(backup, "send_file", send or (lambda name, data, caption="", thread_id=0: sent.append((name, thread_id)) or (True, "sent")))
    return sent, topics


def test_the_hourly_run_makes_its_topic_sends_to_it_and_waits_an_hour(client, entry, monkeypatch):
    sent, topics = _telegram(monkeypatch)
    backup._scheduled_parts()
    assert topics == ["L2TP backups"] and len(sent) == 1 and sent[0][1] == 77 and sent[0][0].startswith("nikatel-l2tp-")
    pc = backup.parts_conf()["l2tp"]
    assert pc["topic"] == 77 and pc["topic_name"] == "L2TP backups" and pc["every_minutes"] == 60 and pc["send"] is True
    stt = backup.part_state("l2tp")
    assert stt["ok"] and stt["sent"] and stt["last_run"] > 0

    backup._scheduled_parts()
    assert len(sent) == 1                                                                 # not yet: an hour has not passed

    m = backup._marker("backup_part_l2tp")
    backup._mark("backup_part_l2tp", {**m, "at": int(time.time()) - 61 * 60})
    backup._scheduled_parts()
    assert len(sent) == 2 and sent[1][1] == 77 and len(topics) == 1                        # the topic is made once


def test_the_hourly_run_is_quiet_when_it_works_and_tells_a_failure_once_in_six_hours(client, entry, monkeypatch):
    sent, _ = _telegram(monkeypatch)
    events = []
    monkeypatch.setattr(backup, "emit", lambda db, level, source, text, extra="": events.append((level, text)))
    backup._scheduled_parts()
    assert events == []                                                                   # a good hourly file is not an event every hour

    tries = []

    def down():
        tries.append(1)
        raise RuntimeError("the entry is down")

    monkeypatch.setattr(l2tp, "backup_data", down)
    backup._mark("backup_part_l2tp", {"at": int(time.time()) - 61 * 60, "ok": True})
    backup._scheduled_parts()
    assert len(tries) == 1 and len(events) == 1 and events[0][0] == "warn" and "the entry is down" in events[0][1]
    assert backup.part_state("l2tp")["ok"] is False and "the entry is down" in backup.part_state("l2tp")["error"]

    backup._scheduled_parts()
    assert len(tries) == 1                                                                # a failed try waits ten minutes before the next one

    backup._mark("backup_part_l2tp", {**backup._marker("backup_part_l2tp"), "at": int(time.time()) - 11 * 60})
    backup._scheduled_parts()
    assert len(tries) == 2 and len(events) == 1                                           # tried again, but the warning is not repeated
    assert len(sent) == 1


def test_nothing_is_made_without_a_password_a_switch_or_an_entry(client, monkeypatch):
    sent, _ = _telegram(monkeypatch)
    made = []
    monkeypatch.setattr(l2tp, "backup_data", lambda: made.append(1) or l2tp.parse_backup(_out()))
    backup._scheduled_parts()
    assert made == []                                                                     # no password, no entry

    st.put("l2tp", {"entry_id": 1})
    backup._scheduled_parts()
    assert made == []                                                                     # an entry, but no password

    backup.set_passphrase(PW)
    parts = st.get("backup").get("parts") or {}
    parts["l2tp"] = {**backup.parts_conf()["l2tp"], "enabled": False}
    st.put("backup", {"parts": parts})
    backup._scheduled_parts()
    assert made == [] and sent == []                                                      # switched off

    parts["l2tp"]["enabled"] = True
    st.put("backup", {"parts": parts})
    backup._scheduled_parts()
    assert made == [1] and len(sent) == 1


def test_the_daily_run_leaves_the_l2tp_part_to_its_own_schedule_and_a_person_gets_all(client, entry, monkeypatch):
    _telegram(monkeypatch)
    assert set(backup.run(send=False, auto="global")["parts"]) == {"panel", "bot"}        # (the bot says why it was not made: not set up)
    assert set(backup.run(send=False)["parts"]) == {"panel", "bot", "l2tp"}

    st.put("l2tp", {"entry_id": 0})
    assert set(backup.run(send=False)["parts"]) == {"panel", "bot"}                        # a panel that does not use L2TP has nothing to save for it


def test_a_topic_that_cannot_be_made_blocks_the_send_and_the_schedule_does_not_ask_again(client, entry, monkeypatch):
    sent, topics = _telegram(monkeypatch, topic=0)
    r = backup.run(send=True, part="l2tp")
    assert r["ok"] and r["parts"]["l2tp"]["sent"] is False and "could not be made" in r["parts"]["l2tp"]["telegram"] and "not a forum" in r["parts"]["l2tp"]["telegram"]
    assert sent == [] and len(topics) == 1                                                # an hourly file never lands on the main page of the group

    backup._scheduled_parts()
    backup._mark("backup_part_l2tp", {**backup._marker("backup_part_l2tp"), "at": int(time.time()) - 61 * 60})
    backup._scheduled_parts()
    assert len(topics) == 1 and sent == []                                                 # the schedule waits six hours; a click (below) tries at once

    backup.run(send=True, part="l2tp")
    assert len(topics) == 2


def test_a_topic_deleted_in_telegram_is_forgotten_so_the_next_backup_makes_a_new_one(client, entry, monkeypatch):
    _telegram(monkeypatch, send=lambda name, data, caption="", thread_id=0: (False, "Telegram rejected the file: Bad Request: message thread not found"))
    r = backup.run(send=True, part="l2tp")
    assert r["parts"]["l2tp"]["sent"] is False and "new one is made" in r["parts"]["l2tp"]["telegram"]
    assert backup.parts_conf()["l2tp"]["topic"] == 0


# ---------------------------------------------------------------- the settings routes
def test_the_backup_view_and_the_part_switches(client, entry):
    v = client.get("/api/settings/backup").json()
    p = v["parts"]["l2tp"]
    assert p["every_minutes"] == 60 and p["send"] is True and p["ready"] is True and p["sched"]["last_run"] == 0 and v["parts"]["panel"]["sched"] is None

    r = client.post("/api/settings/backup/part/l2tp", json={"every_minutes": 180, "send": False})
    assert r.status_code == 200 and r.json()["parts"]["l2tp"]["every_minutes"] == 180 and r.json()["parts"]["l2tp"]["send"] is False
    assert client.post("/api/settings/backup/part/l2tp", json={"every_minutes": 5}).status_code == 400
    assert client.post("/api/settings/backup/part/l2tp", json={"every_minutes": 0}).json()["parts"]["l2tp"]["every_minutes"] == 0     # 0 = with the daily backup
    assert client.post("/api/settings/backup/part/panel", json={"every_minutes": 60}).status_code == 400
    assert client.post("/api/settings/backup/part/l2tp", json={"enabled": False}).json()["parts"]["l2tp"]["enabled"] is False

    st.put("l2tp", {"entry_id": 0})
    assert client.get("/api/settings/backup").json()["parts"]["l2tp"]["ready"] is False


def test_an_hourly_part_does_not_push_the_other_parts_out_of_the_list(client):
    out = get_settings().data_dir.parent / "backups"
    out.mkdir(parents=True, exist_ok=True)
    old = out / "nikatel-panel-20260101-030000.tar.gz.enc"
    old.write_bytes(b"x")
    os.utime(old, (1_700_000_000, 1_700_000_000))

    for i in range(30):
        f = out / f"nikatel-l2tp-2026110{i % 9 + 1}-{i:06d}.tar.gz.enc"
        f.write_bytes(b"y")
        os.utime(f, (1_790_000_000 + i, 1_790_000_000 + i))

    files = client.get("/api/settings/backup").json()["backups"]
    assert any(f["part"] == "panel" for f in files) and sum(f["part"] == "l2tp" for f in files) == 12


# ---------------------------------------------------------------- the restore
def test_a_restore_goes_to_the_entry_with_the_mode_and_keeps_a_copy_here(client, entry, writable, monkeypatch):
    calls = []
    monkeypatch.setattr(l2tp, "restore", lambda users, mode: calls.append((sorted(users), mode)) or {"added": 1, "updated": 0, "removed": 0, "skipped": 0, "total": 2, "safety": "accounts.json.before-restore-1"})
    blob = backup.encrypt(backup.make_l2tp_archive(), PW)

    r = backup.restore_blob(blob)
    assert r["part"] == "l2tp" and r["restarts"] is False and calls[-1] == (["alice", "l2_7"], "missing") and "1 account(s) put back" in r["message"]
    assert any("before-restore" in f["file"] and f["part"] == "l2tp" for f in backup.list_backups())          # what ran before is saved here too
    assert client.get("/api/settings/backup").json()["parts"]["l2tp"]["last"] is None                         # ... but that copy is not "the latest backup"
    backup.restore_blob(blob, "", "all")
    assert calls[-1][1] == "all"

    with pytest.raises(ValueError, match="missing"):
        backup.restore_blob(blob, "", "everything")


def test_the_restore_routes_take_the_mode(client, entry, writable, monkeypatch):
    calls = []
    monkeypatch.setattr(l2tp, "restore", lambda users, mode: calls.append(mode) or {"added": 0, "total": 2})
    name = backup.run(send=False, part="l2tp")["file"]
    assert client.post("/api/backup/restore", json={"file": name}).status_code == 200 and calls == ["missing"]
    assert client.post("/api/backup/restore", json={"file": name, "mode": "all"}).status_code == 200 and calls[-1] == "all"
    assert client.post("/api/backup/restore", json={"file": name, "mode": "nope"}).status_code == 422

    blob = (get_settings().data_dir.parent / "backups" / name).read_bytes()
    assert client.post("/api/backup/restore-upload", content=blob, headers={"x-backup-mode": "all"}).status_code == 200 and calls[-1] == "all"
    assert client.post("/api/backup/restore-upload", content=blob, headers={"x-backup-mode": "nope"}).status_code == 400


def test_the_restore_asks_the_entry_through_the_reviewed_action(monkeypatch):
    with session() as db:
        db.add(Server(name="iran-a", host="192.0.2.10", role="iran"))
        db.flush()
        sid = db.query(Server).one().id

    st.put("l2tp", {"entry_id": sid})
    seen = []

    def action(node, name, params=None, timeout=90):
        seen.append((name, dict(params)))
        return "noise\n" + json.dumps({"ok": True, "added": 2, "updated": 1, "removed": 0, "skipped": 1, "skipped_names": ["bad"], "total": 5, "safety": "accounts.json.before-restore-9"})

    monkeypatch.setattr(l2tp, "run_action", action)
    d = l2tp.restore(USERS, "all")
    assert seen[0][0] == "l2tp_restore" and seen[0][1]["MODE"] == "all" and json.loads(base64.b64decode(seen[0][1]["B64"]))["users"].keys() == USERS.keys()
    text = l2tp.restore_text(d)
    assert "2 account(s) put back" in text and "1 set as in the backup" in text and "1 skipped" in text and "5 accounts" in text and "before-restore-9" in text

    monkeypatch.setattr(l2tp, "run_action", lambda *a, **k: json.dumps({"error": "the restore stopped: disk full"}))

    with pytest.raises(RuntimeError, match="disk full"):
        l2tp.restore(USERS, "missing")

    with pytest.raises(ValueError):
        l2tp.restore(USERS, "some")

    with pytest.raises(ValueError):
        l2tp.restore([], "missing")


# ---------------------------------------------------------------- the reviewed restore, run for real against a stand-in for the manager
STUB = '''
import contextlib, json, os, re
DIR = "@DIR@"
STATE = DIR + "/accounts.json"
NAME = re.compile(r"^[a-z][a-z0-9_]{2,23}$")
PASS = re.compile(r"^[A-Za-z0-9._~@#%+=!-]{6,64}$")
@contextlib.contextmanager
def locked():
    yield
def load():
    try:
        st = json.load(open(STATE))
    except OSError:
        st = {}
    st.setdefault("users", {})
    return st
def save(st):
    json.dump(st, open(STATE, "w"))
def write_chap(st):
    open(DIR + "/chap", "w").write(",".join(sorted(st["users"])))
def _drop_inactive(st):
    open(DIR + "/dropped", "w").write("x")
'''


@pytest.fixture
def box(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    mgr = tmp_path / "nk-l2tp"
    mgr.write_text(STUB.replace("@DIR@", str(d)))

    def run(mode, users, entry_now=None):
        if entry_now is not None:
            (d / "accounts.json").write_text(json.dumps({"users": entry_now}))

        b64 = base64.b64encode(json.dumps({"users": users}).encode()).decode()
        script = ssh.ACTIONS["l2tp_restore"][0].replace("@MODE@", mode).replace("@B64@", b64).replace("/usr/local/sbin/nk-l2tp", str(mgr))
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=False)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout.strip().splitlines()[-1])

    run.dir = d
    run.now = lambda: json.loads((d / "accounts.json").read_text())["users"]
    return run


def test_mode_missing_puts_back_only_what_is_gone_and_skips_what_is_not_valid(box):
    entry_now = {"alice": _acc(password="newer-pass"), "zed": _acc()}
    d = box("missing", {"alice": _acc(password="older-pass"), "l2_7": _acc(), "Bad Name": _acc(), "evil": _acc(password="x"), "noted": _acc(note="a\nb")}, entry_now)
    assert (d["added"], d["updated"], d["removed"], d["skipped"]) == (1, 0, 0, 3) and sorted(d["skipped_names"]) == ["Bad Name", "evil", "noted"]
    now = box.now()
    assert now["alice"]["password"] == "newer-pass" and "zed" in now and "l2_7" in now and d["total"] == 3                # what exists is left alone
    assert (box.dir / d["safety"]).exists() and json.loads((box.dir / d["safety"]).read_text())["users"].keys() == entry_now.keys()
    assert (box.dir / "chap").read_text() == "alice,l2_7,zed" and not (box.dir / "dropped").exists()                        # the manager's own files are written through the manager


def test_mode_all_makes_the_entry_exactly_the_backup(box):
    d = box("all", {"alice": _acc(password="older-pass", limit=3), "l2_7": _acc()}, {"alice": _acc(password="newer-pass"), "zed": _acc()})
    assert (d["added"], d["updated"], d["removed"]) == (1, 1, 1)
    now = box.now()
    assert set(now) == {"alice", "l2_7"} and now["alice"]["password"] == "older-pass" and now["alice"]["limit"] == 3
    assert (box.dir / "dropped").exists()                                                  # the sessions of a removed account are ended


def test_an_empty_backup_never_empties_the_entry_and_a_broken_one_changes_nothing(box):
    keep = {"zed": _acc()}
    d = box("all", {}, keep)
    assert "no account" in d["error"] and box.now() == keep
    assert box("missing", {}, keep)["added"] == 0                                           # 'missing' with nothing to add is harmless
    assert "none of the accounts" in box("all", {"Bad Name": _acc()}, keep)["error"] and box.now() == keep

    script = ssh.ACTIONS["l2tp_restore"][0].replace("@MODE@", "all").replace("@B64@", base64.b64encode(b"[1, 2, 3]").decode()).replace("/usr/local/sbin/nk-l2tp", str(box.dir.parent / "nk-l2tp"))
    out = json.loads(subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=False).stdout.strip().splitlines()[-1])
    assert "could not be read" in out["error"] and box.now() == keep


def test_every_field_of_an_account_is_checked_and_kept(box):
    good = _acc(quota=7_000_000_000, speed={"down": 5, "up": None}, disabled=True, limit_hits=3, last_limit_hit=NOW, last_seen=NOW)
    d = box("missing", {"good": good, "limit0": _acc(limit=0), "limit11": _acc(limit=11), "fast": _acc(speed={"down": 5000}), "neg": _acc(rx=-1), "str": _acc(expires="tomorrow"), "boolq": _acc(quota=True)}, {})
    assert d["added"] == 1 and d["skipped"] == 6
    a = box.now()["good"]
    assert a["quota"] == 7_000_000_000 and a["speed"] == {"down": 5.0, "up": None} and a["disabled"] is True and a["limit_hits"] == 3 and a["last_seen"] == NOW and a["note"] == "order 7 user 9"


def test_the_entry_keeps_the_last_six_copies_only(box):
    for i in range(1, 9):
        (box.dir / f"accounts.json.before-restore-17000000{i:02d}").write_text("{}")

    box("missing", {"alice": _acc()}, {"zed": _acc()})
    kept = sorted(p.name for p in box.dir.iterdir() if p.name.startswith("accounts.json.before-restore-"))
    assert len(kept) == 6 and kept[0] == "accounts.json.before-restore-1700000004"            # (the new copy sorts after the old ones: the three oldest go)


def test_the_two_new_scripts_are_reviewed_ones_with_checked_parameters():
    assert "l2tp_backup" in ssh.SCRIPTS and "l2tp_backup" not in ssh.ACTIONS and "l2tp_restore" in ssh.ACTIONS
    _, rules = ssh.ACTIONS["l2tp_restore"]

    with pytest.raises(ValueError):
        ssh.render_action("l2tp_restore", {"MODE": "everything", "B64": "QUJD" * 8})

    with pytest.raises(ValueError):
        ssh.render_action("l2tp_restore", {"MODE": "all", "B64": "'; rm -rf /; '"})

    assert "QUJDQUJD" * 4 in ssh.render_action("l2tp_restore", {"MODE": "missing", "B64": "QUJD" * 8}) and set(rules) == {"MODE", "B64"}


def test_the_cli_restore_puts_an_l2tp_file_aside_not_among_the_panel_files(client, entry, tmp_path):
    f = tmp_path / "x.tar.gz.enc"
    f.write_bytes(backup.encrypt(backup.make_l2tp_archive(), PW))
    got = backup.restore(f, PW, data_dir=tmp_path / "out")
    assert any(g.startswith("l2tp-restore/l2tp-accounts.json") for g in got) and not (tmp_path / "out" / "nikatel.db").exists()
    assert Path(tmp_path / "out" / "l2tp-restore" / "l2tp-accounts.json").exists()
