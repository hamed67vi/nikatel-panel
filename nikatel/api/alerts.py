"""Telegram, alert rules, maintenance windows, daily report, backups."""
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Maintenance
from ..services import backup, report
from ..services import settings as st
from ..services.telegram import send_text, valid_chat, valid_token

router = APIRouter(tags=["alerts"], dependencies=[Depends(current_user)])


# ---- telegram
class TelegramIn(BaseModel):
    enabled: bool = False
    token: str = ""          # empty keeps the stored one
    chat_id: str = ""


@router.get("/settings/telegram")
def telegram_get():
    return {"ok": True, **st.public("telegram")}


@router.put("/settings/telegram")
def telegram_put(body: TelegramIn):
    if body.token and not valid_token(body.token):
        raise HTTPException(400, "this does not look like a bot token (digits:letters, from @BotFather)")

    if body.chat_id and not valid_chat(body.chat_id):
        raise HTTPException(400, "the chat id is a number (negative for groups/channels) or @channelname")

    st.put("telegram", {"enabled": body.enabled, "token": body.token, "chat_id": body.chat_id})
    return {"ok": True, **st.public("telegram")}


@router.post("/settings/telegram/test")
def telegram_test():
    ok, why = send_text("✅ Nikatel panel: Telegram works.", require_enabled=False)

    if not ok:
        raise HTTPException(502, why)

    return {"ok": True}


# ---- alert rules
class RulesIn(BaseModel):
    tunnel_fails: int = Field(3, ge=1, le=20)
    server_fails: int = Field(2, ge=1, le=20)
    up_stable_seconds: int = Field(600, ge=0, le=86400)
    remind_hours: int = Field(6, ge=1, le=168)


@router.get("/settings/alerts")
def alerts_get():
    return {"ok": True, **st.get("alerts")}


@router.put("/settings/alerts")
def alerts_put(body: RulesIn):
    return {"ok": True, **st.put("alerts", body.model_dump())}


@router.post("/settings/alerts/mute")
def mute(ref: str, muted: bool = True):
    conf = st.get("alerts")
    m = set(conf.get("muted") or [])
    (m.add if muted else m.discard)(ref)
    st.put("alerts", {"muted": sorted(m)})
    return {"ok": True, "muted": sorted(m)}


# ---- maintenance windows
class MaintenanceIn(BaseModel):
    start: int
    end: int
    note: str = Field("", max_length=120)


@router.get("/maintenance")
def maintenance_list():
    now = int(time.time())

    with session() as db:
        rows = db.scalars(select(Maintenance).order_by(Maintenance.start.desc()))
        return {"ok": True, "windows": [{"id": m.id, "start": m.start, "end": m.end, "note": m.note, "active": m.start <= now <= m.end} for m in rows]}


@router.post("/maintenance", status_code=201)
def maintenance_add(body: MaintenanceIn):
    if body.end <= body.start:
        raise HTTPException(400, "the window must end after it starts")

    if body.end - body.start > 30 * 86400:
        raise HTTPException(400, "a window longer than 30 days is a mute, not maintenance")

    with session() as db:
        m = Maintenance(start=body.start, end=body.end, note=body.note)
        db.add(m)
        db.flush()
        return {"ok": True, "id": m.id}


@router.delete("/maintenance/{mid}")
def maintenance_delete(mid: int):
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
    return {"ok": True, **st.get("report")}


@router.put("/settings/report")
def report_put(body: ReportIn):
    return {"ok": True, **st.put("report", body.model_dump())}


@router.get("/report/preview")
def report_preview():
    return {"ok": True, "text": report.build()}


@router.post("/report/send")
def report_send():
    text = report.run(force=True)
    return {"ok": True, "text": text}


# ---- backup
class BackupIn(BaseModel):
    enabled: bool = True
    hour: int = Field(3, ge=0, le=23)
    keep_days: int = Field(14, ge=1, le=365)
    send_telegram: bool = True


class PassIn(BaseModel):
    passphrase: str = Field(min_length=12, max_length=200)


@router.get("/settings/backup")
def backup_get():
    return {"ok": True, **st.get("backup"), "has_passphrase": backup.has_passphrase(), "backups": backup.list_backups()}


@router.put("/settings/backup")
def backup_put(body: BackupIn):
    return {"ok": True, **st.put("backup", body.model_dump())}


@router.post("/settings/backup/passphrase")
def backup_pass(body: PassIn):
    try:
        backup.set_passphrase(body.passphrase)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True}


@router.post("/backup/now")
def backup_now(send: bool = True):
    r = backup.run(send=send)

    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "backup failed"))

    return r


@router.get("/backup/download/{name}")
def backup_download(name: str):
    from fastapi.responses import FileResponse

    p = Path(backup.get_settings().data_dir.parent / "backups" / name)

    if "/" in name or not name.startswith("nikatel-") or not p.exists():
        raise HTTPException(404, "no such backup")

    return FileResponse(p, filename=name, media_type="application/octet-stream")
