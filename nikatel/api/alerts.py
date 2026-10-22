"""Telegram, alert rules, maintenance windows, daily report, backups."""
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Maintenance
from ..services import backup, report
from ..services import settings as st
from ..services.telegram import send_text, valid_chat, valid_token
from ..ssh import SSHError

router = APIRouter(tags=["alerts"], dependencies=[Depends(current_user)])


# ---- telegram
class TelegramIn(BaseModel):
    enabled: bool = False
    token: str = ""          # empty keeps the stored one
    chat_id: str = ""


@router.get("/settings/telegram")
def telegram_get():
    """The Telegram bot and chat the alerts go to (the token is masked)."""
    return {"ok": True, **st.public("telegram")}


@router.put("/settings/telegram")
def telegram_put(body: TelegramIn):
    """Save the Telegram bot token and chat id (the token's shape is checked)."""
    if body.token and not valid_token(body.token):
        raise HTTPException(400, "this does not look like a bot token (digits:letters, from @BotFather)")

    if body.chat_id and not valid_chat(body.chat_id):
        raise HTTPException(400, "the chat id is a number (negative for groups/channels) or @channelname")

    st.put("telegram", {"enabled": body.enabled, "token": body.token, "chat_id": body.chat_id})
    return {"ok": True, **st.public("telegram")}


@router.post("/settings/telegram/test")
def telegram_test():
    """Send a test message to the chat."""
    ok, why = send_text("✅ Nikatel panel: Telegram works.", require_enabled=False)

    if not ok:
        raise HTTPException(502, why)

    return {"ok": True}


# ---- alert rules
class RulesIn(BaseModel):
    down_seconds: int = Field(10, ge=0, le=3600)          # bad (as its checker confirmed it) for at least this long before DOWN
    up_seconds: int = Field(15, ge=0, le=3600)            # good for at least this long before UP
    remind_hours: int = Field(0, ge=0, le=168)            # 0 = never remind
    flap_max: int = Field(3, ge=0, le=50)                 # outages per hour before FLAPPING + quiet; 0 = off
    flap_quiet_hours: int = Field(6, ge=1, le=168)


@router.get("/settings/alerts")
def alerts_get():
    """The alert rules: seconds down before DOWN, seconds good before UP, reminders, the flapping guard, mutes."""
    return {"ok": True, **st.get("alerts")}


@router.put("/settings/alerts")
def alerts_put(body: RulesIn):
    """Save the alert rules."""
    return {"ok": True, **st.put("alerts", body.model_dump())}


_MUTE_LOCK = threading.Lock()
MAX_MUTES = 500


@router.post("/settings/alerts/mute")
def mute(ref: str = Query(min_length=1, max_length=120), muted: bool = True):
    """Mute or unmute one tunnel, server or config."""
    with _MUTE_LOCK:          # (the list is read, changed and written back: two clicks at one moment - or a script - must not lose each other's name)
        conf = st.get("alerts")
        m = set(conf.get("muted") or [])

        if muted and ref not in m and len(m) >= MAX_MUTES:
            raise HTTPException(400, f"at most {MAX_MUTES} things can be muted - unmute some first")

        (m.add if muted else m.discard)(ref)
        st.put("alerts", {"muted": sorted(m)})

    return {"ok": True, "muted": sorted(m)}


# ---- maintenance windows
_MAINT_LOCK = threading.Lock()


class MaintenanceIn(BaseModel):
    start: int = Field(ge=0, le=4102444800)          # (seconds since 1970 - up to the year 2100)
    end: int = Field(ge=0, le=4102444800)
    note: str = Field("", max_length=120)


@router.get("/maintenance")
def maintenance_list():
    """The maintenance windows (no alerts inside them), with whether each is active now."""
    now = int(time.time())

    with session() as db:
        rows = db.scalars(select(Maintenance).order_by(Maintenance.start.desc()))
        return {"ok": True, "windows": [{"id": m.id, "start": m.start, "end": m.end, "note": m.note, "active": m.start <= now <= m.end} for m in rows]}


@router.post("/maintenance", status_code=201)
def maintenance_add(body: MaintenanceIn):
    """Add a maintenance window."""
    if body.end <= body.start:
        raise HTTPException(400, "the window must end after it starts")

    if body.end - body.start > 30 * 86400:
        raise HTTPException(400, "a window longer than 30 days is a mute, not maintenance")

    with _MAINT_LOCK, session() as db:          # (held until the commit, so that two clicks at one moment are one window)
        same = db.scalar(select(Maintenance).where(Maintenance.start == body.start, Maintenance.end == body.end, Maintenance.note == body.note))

        if same is not None:          # the same window sent twice (a double click, a retry) is one window
            return {"ok": True, "id": same.id}

        m = Maintenance(start=body.start, end=body.end, note=body.note)
        db.add(m)
        db.flush()
        return {"ok": True, "id": m.id}


@router.delete("/maintenance/{mid}")
def maintenance_delete(mid: int):
    """Delete a maintenance window."""
    with session() as db:
        m = db.get(Maintenance, mid)

        if m is None:
            raise HTTPException(404, "no such window")

        db.delete(m)
        return {"ok": True}


# ---- report
class ReportIn(BaseModel):
    enabled: bool = True
    hour: int = Field(9, ge=0, le=23)
    tz_offset_minutes: int = Field(210, ge=-720, le=840)


@router.get("/settings/report")
def report_get():
    """The daily report: on or off, and the hour it is sent."""
    return {"ok": True, **st.get("report")}


@router.put("/settings/report")
def report_put(body: ReportIn):
    """Save the daily report settings."""
    return {"ok": True, **st.put("report", body.model_dump())}


@router.get("/report/preview")
def report_preview():
    """The daily report as it would be sent now."""
    return {"ok": True, "text": report.build()}


@router.post("/report/send")
def report_send():
    """Send the daily report now."""
    text = report.run(force=True)
    return {"ok": True, "text": text}


# ---- backup
class BackupIn(BaseModel):
    enabled: bool = True
    hour: int = Field(3, ge=0, le=23)
    keep_days: int = Field(14, ge=1, le=365)
    send_telegram: bool = True
    every_minutes: int = Field(0, ge=0, le=1440)          # 0 = once a day at `hour`; else every N minutes
    tg_every: str = Field("all", pattern=r"^(all|daily)$")   # with a short interval: every backup to Telegram, or once a day only


class PassIn(BaseModel):
    passphrase: str = Field(min_length=12, max_length=200)


def _backup_view() -> dict:
    """The backup settings with, for every part, its switch, its Telegram topic, its latest file and whether it can be made at all."""
    from ..services import shopbot, telegram

    conf, files, pc = st.get("backup"), backup.list_backups(), backup.parts_conf()
    tg = st.get("telegram")
    parts = {}

    for k, (label, what) in backup.PARTS.items():
        last = next((f for f in files if f["part"] == k), None)
        ready = bool(shopbot.conf().get("host")) if k == "bot" else True
        parts[k] = {**pc[k], "label": label, "what": what, "last": last, "ready": ready}

    return {**{k: v for k, v in conf.items() if k != "parts"}, "parts": parts, "has_passphrase": backup.has_passphrase(), "backups": files[:12],
            "telegram_ready": bool(tg.get("enabled") and telegram.valid_token(tg.get("token", "")) and telegram.valid_chat(tg.get("chat_id", "")))}


@router.get("/settings/backup")
def backup_get():
    """The backup settings and the parts (panel, bot) with their last files."""
    return {"ok": True, **_backup_view()}


@router.put("/settings/backup")
def backup_put(body: BackupIn):
    """Save the backup schedule, retention and Telegram delivery."""
    st.put("backup", body.model_dump())
    return {"ok": True, **_backup_view()}


class PartIn(BaseModel):
    enabled: bool


def _part(part: str) -> str:
    if part not in backup.PARTS:
        raise HTTPException(404, "no such backup part")

    return part


@router.post("/settings/backup/part/{part}")
def backup_part(part: str, body: PartIn):
    """Switch one part on or off."""
    _part(part)
    parts = st.get("backup").get("parts") or {}
    parts[part] = {**backup.parts_conf()[part], "enabled": body.enabled}
    st.put("backup", {"parts": parts})
    return {"ok": True, **_backup_view()}


@router.post("/backup/topic/{part}")
def backup_topic(part: str):
    """Make a topic (a separate page) for this part in the Telegram group; its backups go there."""
    from ..services import telegram

    _part(part)
    label = backup.PARTS[part][0]
    tid, why = telegram.create_topic(f"{label} backups")

    if not tid:
        raise HTTPException(400, why or "Telegram did not create the topic")

    parts = st.get("backup").get("parts") or {}
    parts[part] = {**backup.parts_conf()[part], "topic": tid, "topic_name": f"{label} backups"}
    st.put("backup", {"parts": parts})
    return {"ok": True, **_backup_view()}


@router.delete("/backup/topic/{part}")
def backup_topic_unlink(part: str):
    """Forget the topic of this part (the topic itself stays in Telegram); its backups go to the main page of the group again."""
    _part(part)
    parts = st.get("backup").get("parts") or {}
    parts[part] = {**backup.parts_conf()[part], "topic": 0, "topic_name": ""}
    st.put("backup", {"parts": parts})
    return {"ok": True, **_backup_view()}


@router.post("/settings/backup/passphrase")
def backup_pass(body: PassIn):
    """Set the passphrase the backups are encrypted with (once; never shown again)."""
    try:
        backup.set_passphrase(body.passphrase)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True}


@router.post("/backup/now")
def backup_now(send: bool = True, part: str = ""):
    """Make a backup now (and send it to Telegram when asked); `part` limits it to the panel or the bot."""
    r = backup.run(send=send, part=part)

    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "backup failed"))

    return r


class RestoreIn(BaseModel):
    file: str = Field(min_length=1, max_length=120)
    passphrase: str = Field(default="", max_length=200)


def _restore(blob: bytes, passphrase: str) -> dict:
    try:
        return backup.restore_blob(blob, passphrase)
    except (ValueError, RuntimeError, SSHError) as e:
        raise HTTPException(400, str(e))


@router.post("/backup/restore")
def backup_restore(body: RestoreIn):
    """Restore from a backup file kept on this server (the passphrase is only needed when the file was made with another one)."""
    p = backup.get_settings().data_dir.parent / "backups" / body.file

    if "/" in body.file or not body.file.startswith("nikatel-") or not p.is_file():
        raise HTTPException(404, "no such backup")

    return _restore(p.read_bytes(), body.passphrase)


@router.post("/backup/restore-upload")
async def backup_restore_upload(request: Request):
    """Restore from a backup file the operator chose on their computer (raw body; the passphrase, when it is another one, is the X-Backup-Passphrase header)."""
    from starlette.concurrency import run_in_threadpool

    chunks, size = [], 0

    async for part in request.stream():          # read piece by piece and stop at the limit: a body without a Content-Length (chunked) must not fill the memory either
        size += len(part)

        if size > backup.MAX_RESTORE:
            raise HTTPException(413, "the file is too big for a backup")

        chunks.append(part)

    blob = b"".join(chunks)

    if not blob:
        raise HTTPException(400, "no file received")

    return await run_in_threadpool(_restore, blob, request.headers.get("x-backup-passphrase", ""))


@router.get("/backup/download/{name}")
def backup_download(name: str):
    """Download one backup file kept on this server."""
    from fastapi.responses import FileResponse

    p = Path(backup.get_settings().data_dir.parent / "backups" / name)

    if "/" in name or not name.startswith("nikatel-") or not p.exists():
        raise HTTPException(404, "no such backup")

    return FileResponse(p, filename=name, media_type="application/octet-stream")
