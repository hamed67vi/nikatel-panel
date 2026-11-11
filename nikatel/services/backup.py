"""Backups, one file per PART: the panel (database, auth, secret key, known hosts), the shop bot (its database dump) and the L2TP entry (its accounts, 1.69.248). Each part is its own encrypted file and, when
Telegram is set up, goes to its own topic of the group (until everything is one piece). A part may have a schedule of its own: L2TP is saved every hour (the owner, 2026-11-08: "a backup and restore system for the L2TP section,
and send its backup to a section of that group every hour"), the others follow the one daily schedule.

Encryption is `openssl enc -aes-256-cbc -pbkdf2` with a passphrase the operator sets once (kept in
<data_dir>/backup.pass, 0600). The same passphrase restores on any machine with openssl - no
dependency on this code to get the data back. Restore is a CLI action because the service must be
stopped for it.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..ssh import SSHError, run_action, run_script
from . import settings as st
from . import telegram as tgm
from .events import emit
from .telegram import send_file

log = get_logger("backup")
FILES = ("auth.json", "secret.key", "known_hosts", "backup.pass")
PARTS = {"panel": ("Panel", "database, settings, SSH keys of the panel"), "bot": ("Shop bot", "the bot database (users, orders, payments, plans)"),
         "l2tp": ("L2TP", "the accounts of the L2TP entry (names, passwords, devices, end dates, traffic, speeds)")}
OWN = {"l2tp": {"every_minutes": 60, "send": True}}      # a part with a schedule of its own (minutes) and whether each file goes to Telegram; the other parts follow the one daily schedule
TOPIC_ONLY = ("l2tp",)                                   # a part that is sent only to a topic of its own: an hourly file must never land on the main page of the group
TOPIC_RETRY = 6 * 3600                                   # (a topic that could not be made is tried again after this long by the schedule; a click tries at once)


def pass_file() -> Path:
    return get_settings().data_dir / "backup.pass"


def set_passphrase(p: str) -> None:
    if any(c in p for c in "\r\n\x00"):          # (openssl reads the passphrase from the first line of its input and the file after it: a second line was taken for the first bytes of the FILE - every backup made with it could not be opened)
        raise ValueError("the backup passphrase must be one line")

    p = p.strip()          # (the passphrase is read back stripped: twelve spaces were "set" and then no passphrase at all)

    if len(p) < 12:
        raise ValueError("the backup passphrase needs at least 12 characters")

    f = pass_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(p)
    f.chmod(0o600)


def has_passphrase() -> bool:
    return pass_file().exists() and bool(pass_file().read_text().strip())


def make_archive() -> bytes:
    """tar.gz of a consistent SQLite copy plus the small secret files (in memory)."""
    s = get_settings()
    buf = io.BytesIO()

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        src = sqlite3.connect(str(s.data_dir / "nikatel.db"))
        dst = sqlite3.connect(":memory:")
        src.backup(dst)
        src.close()
        dump = "\n".join(dst.iterdump()).encode()
        dst.close()
        info = tarfile.TarInfo("nikatel.sql")
        info.size, info.mtime, info.mode = len(dump), int(time.time()), 0o600
        tar.addfile(info, io.BytesIO(dump))

        for name in FILES:
            p = s.data_dir / name

            if p.exists() and name != "backup.pass":
                tar.add(str(p), arcname=name)

    return buf.getvalue()


def make_bot_archive() -> bytes:
    """tar.gz with the shop bot database dump (mysqldump, taken on the bot server by a reviewed action and fetched over SSH)."""
    from . import shopbot

    c = shopbot.conf()
    node = shopbot._node()                                    # raises "tell the panel where the bot runs" when it is not set
    out = run_action(node, "bot_backup", {"ENV": c["env"], "NAME": "auto"}, timeout=600)
    m = re.search(r"BACKUP (/opt/nikabot/backups/\S+\.sql\.gz) (\d+)", out)

    if not m:
        raise RuntimeError("the bot database dump failed: " + out.strip()[-120:])

    b64 = run_script(node, "bot_fetch_dump", {"FILE": m.group(1)}, timeout=300).strip()

    if not b64 or b64.startswith("TOO_BIG"):
        raise RuntimeError("the bot database dump is missing or bigger than 30 MB")

    gz = base64.b64decode(b64)

    try:
        run_action(node, "bot_prune_dumps", {}, timeout=30)    # the server keeps the last 7 dumps of this kind only
    except (SSHError, ValueError):
        pass

    buf = io.BytesIO()

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("bot.sql.gz")
        info.size, info.mtime, info.mode = len(gz), int(time.time()), 0o600
        tar.addfile(info, io.BytesIO(gz))

    return buf.getvalue()


def make_l2tp_archive() -> bytes:
    """tar.gz with the L2TP entry's accounts file as the manager keeps it (passwords included - the file is encrypted before it leaves this function's caller) and a small info file: when, how many, the pool and the shared key
    (kept for a rebuild of the entry by hand; a restore never changes the key)."""
    from .. import __version__
    from . import l2tp

    d = l2tp.backup_data()
    users = d["state"]["users"]
    now = int(time.time())
    parts = (("l2tp-accounts.json", json.dumps(d["state"], indent=1, sort_keys=True).encode()),
             ("l2tp-info.json", json.dumps({"taken_at": now, "accounts": len(users), "pool": d["pool"], "psk": d["psk"], "panel": __version__}, indent=1).encode()))
    buf = io.BytesIO()

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in parts:
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), now, 0o600
            tar.addfile(info, io.BytesIO(data))

    return buf.getvalue()


def part_of(name: str) -> str:
    """The part a backup file belongs to: nikatel-bot-... is the bot, nikatel-l2tp-... the L2TP entry; every other file (also the older nikatel-<date>... ones) is the panel."""
    m = re.match(r"^nikatel-(panel|bot|l2tp)-", name)
    return m.group(1) if m else "panel"


def parts_conf() -> dict:
    cur = st.get("backup").get("parts") or {}
    out = {}

    for k in PARTS:
        p, d = cur.get(k) or {}, OWN.get(k, {})
        out[k] = {"enabled": bool(p.get("enabled", True)), "topic": int(p.get("topic", 0) or 0), "topic_name": str(p.get("topic_name", "")),
                  "every_minutes": int(p.get("every_minutes", d.get("every_minutes", 0)) or 0), "send": bool(p.get("send", d.get("send", True)))}

    return out


def part_on(k: str) -> bool:
    """Does this install have this part at all: the L2TP part belongs to the license feature `l2tp` (no row in the Backups card, no hourly file, no manual run where it is not licensed)."""
    if k == "l2tp":
        from . import license

        return license.has("l2tp")

    return True


def part_ready(k: str) -> bool:
    """Is there anything to save for this part on this panel (L2TP: an entry server is chosen). The panel and the bot always count as ready here - the bot says why itself when it is not set up."""
    if k == "l2tp":
        from . import l2tp

        return part_on(k) and l2tp.backup_ready()

    return True


def part_state(k: str) -> dict:
    """How the part's own schedule did last: when, whether it worked, what went wrong, whether the file reached Telegram."""
    m = _marker(f"backup_part_{k}")
    return {"last_run": int(m.get("at", 0)), "ok": bool(m.get("ok", True)), "error": str(m.get("error", "")), "sent": bool(m.get("sent", False)), "telegram": str(m.get("telegram", ""))}


def make_topic(part: str) -> tuple[int, str]:
    """Make a topic for this part in the Telegram group and remember it: (thread id, "") or (0, why)."""
    label = PARTS[part][0]
    tid, why = tgm.create_topic(f"{label} backups")

    if not tid:
        return 0, why

    parts = st.get("backup").get("parts") or {}
    parts[part] = {**parts_conf()[part], "topic": tid, "topic_name": f"{label} backups"}
    st.put("backup", {"parts": parts})
    return tid, ""


def encrypt(data: bytes, passphrase: str) -> bytes:
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "stdin"], input=passphrase.encode() + b"\n" + data,
                       capture_output=True, check=False)

    if r.returncode != 0:
        raise RuntimeError("openssl failed: " + r.stderr.decode()[:120])

    return r.stdout


def decrypt(data: bytes, passphrase: str) -> bytes:
    r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", "stdin"], input=passphrase.encode() + b"\n" + data,
                       capture_output=True, check=False)

    # a wrong passphrase usually fails the padding check, but once in ~256 tries openssl returns
    # garbage with exit 0 - so the result must also look like the gzip archive we wrote
    if r.returncode != 0 or not r.stdout.startswith(b"\x1f\x8b"):
        raise RuntimeError("wrong passphrase or damaged file")

    return r.stdout


def run(send: bool | None = None, part: str = "", auto: str = "") -> dict:
    """Make the backup file of every enabled part (or of one `part`), keep them locally, send each to Telegram (to its own topic when it has one). Returns a summary.
    `auto` says who asks: "" = a person (every enabled part that has something to save), "global" = the one daily schedule (the parts that have a schedule of their own are left to it), "part" = such a part's own schedule
    (quiet - the schedule tells about a failure itself, once in a while - and a topic that could not be made is not asked for again for six hours)."""
    s = get_settings()
    conf = st.get("backup")

    if not has_passphrase():
        if auto != "part":
            with session() as db:
                emit(db, "warn", "backup", "No backup made: set a backup passphrase in Settings → Backup first.", "")

        return {"ok": False, "error": "no passphrase set"}

    if part and (part not in PARTS or not part_on(part)):
        return {"ok": False, "error": "unknown part"}

    pc = parts_conf()
    chosen = [part] if part else [k for k in PARTS if pc[k]["enabled"] and part_ready(k) and not (auto == "global" and pc[k]["every_minutes"])]
    out_dir = s.data_dir.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    keep = int(conf.get("keep_days", 14)) * 86400
    do_send = send if send is not None else conf.get("send_telegram")
    results: dict[str, dict] = {}
    makers = {"panel": make_archive, "bot": make_bot_archive, "l2tp": make_l2tp_archive}

    for k in chosen:
        label = PARTS[k][0]

        try:
            blob = encrypt(makers[k](), pass_file().read_text().strip())
        except (RuntimeError, ValueError, SSHError, OSError) as e:
            results[k] = {"ok": False, "error": str(e)[:160]}

            if auto != "part":
                with session() as db:
                    emit(db, "warn", "backup", f"{label} backup not made: {str(e)[:160]}", "")

            continue

        path = out_dir / f"nikatel-{k}-{stamp}.tar.gz.enc"
        path.write_bytes(blob)
        path.chmod(0o600)

        for old in out_dir.glob("nikatel-*.tar.gz.enc"):
            if part_of(old.name) == k and time.time() - old.stat().st_mtime > keep:
                old.unlink(missing_ok=True)

        sent, why = False, "not requested"

        if do_send:
            tid, blocked = pc[k]["topic"], ""

            if k in TOPIC_ONLY and not tid and tgm.ready():                      # (this part is sent only to a topic of its own: make one, in this group, when it has none)
                fail = _marker(f"backup_topic_{k}")

                if auto == "part" and time.time() - int(fail.get("at", 0)) < TOPIC_RETRY:
                    blocked = str(fail.get("why", "")) or "the topic could not be made"
                else:
                    tid, why_t = make_topic(k)
                    blocked = "" if tid else f"the Telegram topic could not be made: {why_t}"
                    _mark(f"backup_topic_{k}", {} if tid else {"at": int(time.time()), "why": blocked[:200]})

            if blocked:
                why = blocked
            else:
                sent, why = send_file(path.name, blob, f"Nikatel {label} backup {stamp} ({len(blob) // 1024} KB)", thread_id=tid)

                if not sent and k in TOPIC_ONLY and "thread not found" in why.lower():          # (the topic was deleted in Telegram: forget it, the next backup makes a new one)
                    parts = st.get("backup").get("parts") or {}
                    parts[k] = {**parts_conf()[k], "topic": 0, "topic_name": ""}
                    st.put("backup", {"parts": parts})
                    why += " (the topic is gone: a new one is made at the next backup)"

        results[k] = {"ok": True, "file": path.name, "bytes": len(blob), "sent": sent, "telegram": why}

        if auto != "part":
            with session() as db:
                emit(db, "info", "backup", f"{label} backup {path.name} made ({len(blob) // 1024} KB)" + (", sent to Telegram." if sent else f"; Telegram: {why}."), "")

        log.info("backup %s bytes=%s telegram=%s", path.name, len(blob), sent)

    made = [r for r in results.values() if r["ok"]]

    if not made:
        return {"ok": False, "error": "; ".join(f"{PARTS[k][0]}: {r['error']}" for k, r in results.items()) or "nothing to back up", "parts": results}

    first = made[0]
    return {"ok": True, "file": first["file"], "bytes": first["bytes"], "sent": all(r["sent"] for r in made), "telegram": first["telegram"], "parts": results}

def list_backups() -> list[dict]:
    out_dir = get_settings().data_dir.parent / "backups"

    if not out_dir.exists():
        return []

    return sorted(({"file": p.name, "part": part_of(p.name), "bytes": p.stat().st_size, "at": int(p.stat().st_mtime)} for p in out_dir.glob("nikatel-*.tar.gz.enc")),
                  key=lambda x: x["at"], reverse=True)


def _marker(key: str) -> dict:
    from ..models import Setting

    with session() as db:
        m = db.get(Setting, key)
        return dict(m.value) if m is not None and isinstance(m.value, dict) else {}


def _mark(key: str, value: dict) -> None:
    from ..models import Setting

    with session() as db:
        m = db.get(Setting, key)

        if m is None:
            db.add(Setting(key=key, value=value))
        else:
            m.value = value


def scheduled() -> None:
    """Job (every 5 minutes): the one daily schedule, then the parts that have a schedule of their own."""
    _scheduled_global()
    _scheduled_parts()


def _scheduled_parts() -> None:
    """The parts with a schedule of their own (L2TP: every hour, the owner, 2026-11-08): each is made when its time has come and goes to its own topic of the group. It does not depend on the daily schedule's switch - the part has its own.
    Without the backup passphrase nothing is made (the L2TP window says so). A try that failed is repeated after 10 minutes; a failure is told in the events once in six hours, not at every try."""
    if not has_passphrase():
        return

    now = int(time.time())

    for k, p in parts_conf().items():
        every = int(p["every_minutes"] or 0)

        if not every or not p["enabled"] or not part_ready(k):
            continue

        last = _marker(f"backup_part_{k}")

        if now - int(last.get("at", 0)) < (every * 60 - 30 if last.get("ok", True) else 600):          # 30 s of slack: the job ticks every 5 minutes, not to the second
            continue

        r = run(send=bool(p["send"]), part=k, auto="part")
        res = (r.get("parts") or {}).get(k) or {}
        ok = bool(r.get("ok"))
        mark = {"at": now, "ok": ok, "error": "" if ok else str(r.get("error", ""))[:160], "sent": bool(res.get("sent")), "telegram": str(res.get("telegram", ""))[:160], "warn_at": int(last.get("warn_at", 0))}

        if not ok and now - mark["warn_at"] > 6 * 3600:
            with session() as db:
                emit(db, "warn", "backup", f"{PARTS[k][0]} backup not made: {mark['error']}", "")

            mark["warn_at"] = now

        _mark(f"backup_part_{k}", mark)


def _scheduled_global() -> None:
    """The one daily schedule: once per local day at the configured hour - or, when `every_minutes` is set, every that many minutes (15, 30, 60 ...).
    With a short interval the files can still go to Telegram every time, or once a day only (`tg_every`), so the group is not flooded."""
    conf = st.get("backup")

    if not conf.get("enabled"):
        return

    from .report import local_day

    now = int(time.time())
    day, hour = local_day(now, int(st.get("report").get("tz_offset_minutes", 0)))
    every = int(conf.get("every_minutes") or 0)
    last_day = _marker("backup_last_day").get("day", "")

    if every > 0:
        if now - int(_marker("backup_last_run").get("at", 0)) < every * 60 - 30:        # 30 s of slack: the job ticks every 5 minutes, not to the second
            return

        daily_due = hour >= int(conf.get("hour", 3)) and last_day != day
        send = bool(conf.get("send_telegram")) and (conf.get("tg_every", "all") != "daily" or daily_due)
        r = run(send=send, auto="global")
        _mark("backup_last_run", {"at": now})

        if r.get("ok") and daily_due:
            _mark("backup_last_day", {"day": day})

        return

    if hour < int(conf.get("hour", 3)) or last_day == day:
        return

    run(auto="global")
    _mark("backup_last_day", {"day": day})


def restore(path: Path, passphrase: str, data_dir: Path | None = None) -> list[str]:
    """Unpack a backup into the data dir (service must be stopped). Returns the restored file names."""
    data_dir = data_dir or get_settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    raw = decrypt(path.read_bytes(), passphrase)
    restored = []

    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile() or "/" in m.name or m.name.startswith("."):
                continue

            data = tar.extractfile(m).read()

            if m.name == "bot.sql.gz":                       # the shop bot dump: not a panel file; put aside for `gunzip < bot.sql.gz | mysql <db>`
                (data_dir / "bot-restore").mkdir(exist_ok=True)
                (data_dir / "bot-restore" / m.name).write_bytes(data)
                restored.append("bot-restore/bot.sql.gz (load it with: gunzip < bot.sql.gz | mysql <database>)")
            elif m.name in ("l2tp-accounts.json", "l2tp-info.json"):          # the L2TP accounts: not a panel file either; put aside - the panel's L2TP page restores them onto the entry
                (data_dir / "l2tp-restore").mkdir(exist_ok=True)
                (data_dir / "l2tp-restore" / m.name).write_bytes(data)
                os.chmod(data_dir / "l2tp-restore" / m.name, 0o600)
                restored.append(f"l2tp-restore/{m.name} (restore it from the L2TP page of the panel)")
            elif m.name == "nikatel.sql":
                db_path = data_dir / "nikatel.db"

                for ext in ("", "-wal", "-shm"):
                    (data_dir / f"nikatel.db{ext}").unlink(missing_ok=True)

                conn = sqlite3.connect(str(db_path))
                conn.executescript(data.decode())
                conn.close()
                restored.append("nikatel.db")
            else:
                (data_dir / m.name).write_bytes(data)
                os.chmod(data_dir / m.name, 0o600)
                restored.append(m.name)

    return restored


# ---------------------------------------------------------------- restore from the panel
PENDING = "restore-pending"
MAX_RESTORE = 120_000_000


def read_archive(blob: bytes, passphrase: str = "") -> tuple[str, dict[str, bytes]]:
    """Decrypt and unpack a backup file in memory. Returns (part, files); refuses anything that is not a Nikatel backup of a known part."""
    if len(blob) > MAX_RESTORE:
        raise ValueError("the file is too big for a backup")

    pw = passphrase.strip() or (pass_file().read_text().strip() if has_passphrase() else "")

    if not pw:
        raise ValueError("no passphrase: type the passphrase this file was made with")

    raw = decrypt(blob, pw)
    files: dict[str, bytes] = {}

    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for m in tar.getmembers():
                if not m.isfile() or "/" in m.name or m.name.startswith(".") or m.size > MAX_RESTORE:
                    continue

                files[m.name] = tar.extractfile(m).read()
    except (tarfile.TarError, OSError) as e:
        raise ValueError("the file is damaged: " + str(e)[:80])

    if "nikatel.sql" in files:
        try:
            probe = sqlite3.connect(":memory:")
            probe.executescript(files["nikatel.sql"].decode())
            tables = probe.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            probe.close()
        except (sqlite3.Error, UnicodeDecodeError) as e:
            raise ValueError("the panel database inside is damaged: " + str(e)[:80])

        if tables < 3:
            raise ValueError("this is not a panel database")

        return "panel", files

    if "bot.sql.gz" in files:
        import gzip

        try:
            head = gzip.decompress(files["bot.sql.gz"])[:2_000_000]
        except (OSError, EOFError) as e:
            raise ValueError("the bot dump inside is damaged: " + str(e)[:80])

        if b"CREATE TABLE" not in head:
            raise ValueError("this is not a bot database dump")

        return "bot", files

    if "l2tp-accounts.json" in files:
        try:
            state = json.loads(files["l2tp-accounts.json"])
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError("the L2TP accounts inside are damaged: " + str(e)[:80])

        if not isinstance(state, dict) or not isinstance(state.get("users"), dict):
            raise ValueError("this is not an L2TP backup")

        return "l2tp", files

    raise ValueError("this is not a Nikatel backup")


def stage_panel(files: dict[str, bytes]) -> str:
    """Panel part: a safety copy of what runs now is kept first, then the new files wait in <data>/restore-pending and are put in place by the next start
    (the running service keeps its database open, so the swap happens before it opens it)."""
    import shutil

    s = get_settings()
    safety = ""

    if has_passphrase():
        out_dir = s.data_dir.parent / "backups"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"nikatel-panel-{time.strftime('%Y%m%d-%H%M%S')}-before-restore.tar.gz.enc"
        path.write_bytes(encrypt(make_archive(), pass_file().read_text().strip()))
        path.chmod(0o600)
        safety = path.name

    pend = s.data_dir / PENDING
    shutil.rmtree(pend, ignore_errors=True)
    pend.mkdir(parents=True)

    for name, data in files.items():
        if name == "nikatel.sql" or (name in FILES and name != "backup.pass"):
            (pend / name).write_bytes(data)
            (pend / name).chmod(0o600)

    return safety


def apply_pending_restore() -> str:
    """At start, before the database is opened: put the staged files in place. The new database is built beside the old one and swapped only when it loaded completely."""
    import shutil

    s = get_settings()
    pend = s.data_dir / PENDING

    if not pend.is_dir():
        return ""

    try:
        sql = pend / "nikatel.sql"

        if sql.exists():
            new = s.data_dir / "nikatel.db.restoring"
            new.unlink(missing_ok=True)
            conn = sqlite3.connect(str(new))
            conn.executescript(sql.read_text())
            conn.close()

            for ext in ("", "-wal", "-shm"):
                (s.data_dir / f"nikatel.db{ext}").unlink(missing_ok=True)

            new.replace(s.data_dir / "nikatel.db")

        for name in FILES:
            if name != "backup.pass" and (pend / name).exists():
                (s.data_dir / name).write_bytes((pend / name).read_bytes())
                os.chmod(s.data_dir / name, 0o600)
    except Exception:  # start-up boundary: a bad restore must not stop the panel from starting
        log.exception("restore failed, the panel starts with what it had")
        failed = s.data_dir / "restore-failed"
        shutil.rmtree(failed, ignore_errors=True)
        pend.rename(failed)
        return "failed"

    shutil.rmtree(pend, ignore_errors=True)
    log.info("restore applied from %s", PENDING)
    return "restored"


def restore_bot(gz: bytes) -> str:
    """Bot part: a fresh dump of the live bot database is kept on the bot server first, then the dump is loaded into it (tables are dropped and made again by the dump itself)."""
    from . import shopbot

    c = shopbot.conf()
    node = shopbot._node()
    safe = run_action(node, "bot_backup", {"ENV": c["env"], "NAME": "before-restore"}, timeout=600)
    m = re.search(r"BACKUP (/opt/nikabot/backups/\S+\.sql\.gz)", safe)

    if not m:
        raise RuntimeError("the safety dump of the live bot database failed, nothing was restored: " + safe.strip()[-120:])

    out = run_action(node, "bot_restore", {"ENV": c["env"], "B64": base64.b64encode(gz).decode()}, timeout=900)

    if "RESTORED" not in out:
        raise RuntimeError("the bot database was not restored: " + out.strip()[-160:] + f" (the safety dump is {m.group(1)} on the bot server)")

    return f"{out.strip().splitlines()[-1]} - the dump of the data before is {m.group(1)} on the bot server"


def restart_soon() -> None:
    """Restart the service in two seconds (outside this process, so the answer to the page goes out first)."""
    try:
        subprocess.run(["systemd-run", "--quiet", "--on-active=2", "systemctl", "restart", "nikatel"], check=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError("the files are staged but the restart failed: " + str(e)[:100] + " - restart the nikatel service by hand")


def restore_l2tp(files: dict[str, bytes], mode: str) -> dict:
    """L2TP part: a fresh encrypted copy of what the entry holds now is kept here first (when the passphrase is set and the entry answers - the entry keeps its own copy of the file in any case), then the accounts of the backup are put on the
    entry: `missing` = only the accounts it does not have (the default, it removes nothing), `all` = exactly as the backup. The shared key is never changed."""
    from . import l2tp

    users = json.loads(files["l2tp-accounts.json"])["users"]
    safety = ""

    if has_passphrase():
        try:
            out_dir = get_settings().data_dir.parent / "backups"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"nikatel-l2tp-{time.strftime('%Y%m%d-%H%M%S')}-before-restore.tar.gz.enc"
            path.write_bytes(encrypt(make_l2tp_archive(), pass_file().read_text().strip()))
            path.chmod(0o600)
            safety = path.name
        except (RuntimeError, ValueError, SSHError, OSError):
            safety = ""                                                    # (the entry cannot be read now: the restore will say so itself)

    d = l2tp.restore(users, mode or "missing")
    return {"ok": True, "part": "l2tp", "restarts": False, "added": d.get("added", 0), "updated": d.get("updated", 0), "removed": d.get("removed", 0), "skipped": d.get("skipped", 0),
            "message": l2tp.restore_text(d) + (f" A copy of it is also kept here as {safety}." if safety else "")}


def restore_blob(blob: bytes, passphrase: str = "", mode: str = "") -> dict:
    """The restore the Backups card asks for: any backup file (made here or uploaded). Panel part: staged, then the panel restarts. Bot part: loaded at once. L2TP part: put on the entry at once (`mode`: missing | all)."""
    if get_settings().read_only:
        raise ValueError("the panel is in read-only mode: a restore is not allowed here")

    if mode not in ("", "missing", "all"):
        raise ValueError("the way to restore is 'missing' or 'all'")

    part, files = read_archive(blob, passphrase)

    if part == "l2tp":
        return restore_l2tp(files, mode)

    if part == "bot":
        note = restore_bot(files["bot.sql.gz"])
        return {"ok": True, "part": "bot", "restarts": False, "message": "Shop bot database restored: " + note}

    safety = stage_panel(files)
    restart_soon()
    return {"ok": True, "part": "panel", "restarts": True, "safety": safety, "message": "Panel data restored. The panel restarts now; sign in again in a few seconds." + (f" Before this, what ran was saved as {safety}." if safety else "")}
