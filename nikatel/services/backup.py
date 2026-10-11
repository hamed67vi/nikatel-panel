"""Backups, one file per PART: the panel (database, auth, secret key, known hosts) and the shop bot (its database dump). Each part is its own encrypted file and, when
Telegram is set up, goes to its own topic of the group (until everything is one piece).

Encryption is `openssl enc -aes-256-cbc -pbkdf2` with a passphrase the operator sets once (kept in
<data_dir>/backup.pass, 0600). The same passphrase restores on any machine with openssl - no
dependency on this code to get the data back. Restore is a CLI action because the service must be
stopped for it.
"""
from __future__ import annotations

import base64
import io
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
from .events import emit
from .telegram import send_file

log = get_logger("backup")
FILES = ("auth.json", "secret.key", "known_hosts", "backup.pass")
PARTS = {"panel": ("Panel", "database, settings, SSH keys of the panel"), "bot": ("Shop bot", "the bot database (users, orders, payments, plans)")}
MAX_BOT_DUMP = 30_000_000


def pass_file() -> Path:
    return get_settings().data_dir / "backup.pass"


def set_passphrase(p: str) -> None:
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


def part_of(name: str) -> str:
    """The part a backup file belongs to: nikatel-bot-... is the bot; every other file (also the older nikatel-<date>... ones) is the panel."""
    m = re.match(r"^nikatel-(panel|bot)-", name)
    return m.group(1) if m else "panel"


def parts_conf() -> dict:
    cur = st.get("backup").get("parts") or {}
    return {k: {"enabled": bool((cur.get(k) or {}).get("enabled", True)), "topic": int((cur.get(k) or {}).get("topic", 0) or 0), "topic_name": str((cur.get(k) or {}).get("topic_name", ""))} for k in PARTS}


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


def run(send: bool | None = None, part: str = "") -> dict:
    """Make the backup file of every enabled part (or of one `part`), keep them locally, send each to Telegram (to its own topic when it has one). Returns a summary."""
    s = get_settings()
    conf = st.get("backup")

    if not has_passphrase():
        with session() as db:
            emit(db, "warn", "backup", "No backup made: set a backup passphrase in Settings → Backup first.", "")

        return {"ok": False, "error": "no passphrase set"}

    if part and part not in PARTS:
        return {"ok": False, "error": "unknown part"}

    pc = parts_conf()
    chosen = [part] if part else [k for k in PARTS if pc[k]["enabled"]]
    out_dir = s.data_dir.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    keep = int(conf.get("keep_days", 14)) * 86400
    do_send = send if send is not None else conf.get("send_telegram")
    results: dict[str, dict] = {}
    makers = {"panel": make_archive, "bot": make_bot_archive}

    for k in chosen:
        label = PARTS[k][0]

        try:
            blob = encrypt(makers[k](), pass_file().read_text().strip())
        except (RuntimeError, ValueError, SSHError, OSError) as e:
            results[k] = {"ok": False, "error": str(e)[:160]}

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
            sent, why = send_file(path.name, blob, f"Nikatel {label} backup {stamp} ({len(blob) // 1024} KB)", thread_id=pc[k]["topic"])

        results[k] = {"ok": True, "file": path.name, "bytes": len(blob), "sent": sent, "telegram": why}

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
    """Job (every 5 minutes): once per local day at the configured hour - or, when `every_minutes` is set, every that many minutes (15, 30, 60 ...).
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
        r = run(send=send)
        _mark("backup_last_run", {"at": now})

        if r.get("ok") and daily_due:
            _mark("backup_last_day", {"day": day})

        return

    if hour < int(conf.get("hour", 3)) or last_day == day:
        return

    run()
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
