"""Backups: the data dir (database, auth, secret key, known hosts) as one encrypted file.

Encryption is `openssl enc -aes-256-cbc -pbkdf2` with a passphrase the operator sets once (kept in
<data_dir>/backup.pass, 0600). The same passphrase restores on any machine with openssl - no
dependency on this code to get the data back. Restore is a CLI action because the service must be
stopped for it.
"""
from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path

from ..config import get_settings
from ..db import session
from ..log import get_logger
from . import settings as st
from .events import emit
from .telegram import send_file

log = get_logger("backup")
FILES = ("auth.json", "secret.key", "known_hosts", "backup.pass")


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


def encrypt(data: bytes, passphrase: str) -> bytes:
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "stdin"], input=passphrase.encode() + b"\n" + data,
                       capture_output=True, check=False)

    if r.returncode != 0:
        raise RuntimeError("openssl failed: " + r.stderr.decode()[:120])

    return r.stdout


def decrypt(data: bytes, passphrase: str) -> bytes:
    r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", "stdin"], input=passphrase.encode() + b"\n" + data,
                       capture_output=True, check=False)

    if r.returncode != 0:
        raise RuntimeError("wrong passphrase or damaged file")

    return r.stdout


def run(send: bool | None = None) -> dict:
    """Make a backup file, keep it locally, optionally send it to Telegram. Returns a summary."""
    s = get_settings()
    conf = st.get("backup")

    if not has_passphrase():
        with session() as db:
            emit(db, "warn", "backup", "No backup made: set a backup passphrase in Settings → Backup first.", "")

        return {"ok": False, "error": "no passphrase set"}

    out_dir = s.data_dir.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    blob = encrypt(make_archive(), pass_file().read_text().strip())
    path = out_dir / f"nikatel-{stamp}.tar.gz.enc"
    path.write_bytes(blob)
    path.chmod(0o600)

    keep = int(conf.get("keep_days", 14)) * 86400

    for old in out_dir.glob("nikatel-*.tar.gz.enc"):
        if time.time() - old.stat().st_mtime > keep:
            old.unlink(missing_ok=True)

    sent, why = False, "not requested"

    if send if send is not None else conf.get("send_telegram"):
        sent, why = send_file(path.name, blob, f"Nikatel panel backup {stamp} ({len(blob) // 1024} KB)")

    with session() as db:
        emit(db, "info", "backup", f"Backup {path.name} made ({len(blob) // 1024} KB)" + (", sent to Telegram." if sent else f"; Telegram: {why}."), "")

    log.info("backup %s bytes=%s telegram=%s", path.name, len(blob), sent)
    return {"ok": True, "file": path.name, "bytes": len(blob), "sent": sent, "telegram": why}


def list_backups() -> list[dict]:
    out_dir = get_settings().data_dir.parent / "backups"

    if not out_dir.exists():
        return []

    return sorted(({"file": p.name, "bytes": p.stat().st_size, "at": int(p.stat().st_mtime)} for p in out_dir.glob("nikatel-*.tar.gz.enc")),
                  key=lambda x: x["at"], reverse=True)


def scheduled() -> None:
    """Job: once per local day at the configured hour."""
    conf = st.get("backup")

    if not conf.get("enabled"):
        return

    from ..models import Setting
    from .report import local_day

    now = int(time.time())
    day, hour = local_day(now, int(st.get("report").get("tz_offset_minutes", 0)))

    with session() as db:
        marker = db.get(Setting, "backup_last_day")
        last = marker.value.get("day") if marker is not None and isinstance(marker.value, dict) else ""

    if hour < int(conf.get("hour", 3)) or last == day:
        return

    run()

    with session() as db:
        marker = db.get(Setting, "backup_last_day")

        if marker is None:
            db.add(Setting(key="backup_last_day", value={"day": day}))
        else:
            marker.value = {"day": day}


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

            if m.name == "nikatel.sql":
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
