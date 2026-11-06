"""The TESTERS of the Iran test kit (1.69.238): the people the owner sends the kit to, in different cities.

The internet is not the same in every part of Iran: one city filters an IP that another does not, one SIM stalls where another is fast. So the kit goes to several people, each with a kit of their own: the panel
makes the zip (the same folder everybody gets) with that person's name and city in it (`who.json`), the list of what to test as of this minute (`targets.json`, `configs/`, `panel.json`: the same data as the live
list of the kit, so a new zip is never stale) and a token of its own (scope `field`: it may send a result and read the list, nothing else; revoking it stops that kit). What the testers send is read by
services/field.py like every other run: the Field test tab shows the newest run of every network (operator, city, SIM or home line) and the line of what the networks have in common.

Where the pieces live (settings.data_dir): `field-kit/` holds the files that are the same for everybody (kit.ps1, resend.ps1, the .bat files, the README and xray/xray.exe: put there by hand, they are not part
of this repository); `field-kits/<id>.zip` is the zip of one tester (it holds that tester's token: mode 0600). The list of testers is in the settings key `field` (`testers`); the address the kit sends to is
`upload_urls` there. Nothing here touches a server or a config: it makes a file.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

from sqlalchemy import func, select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import FieldRun
from . import fieldkit, tokens
from . import settings as st

log = get_logger("fieldtesters")
SIMS = ("", "Irancell", "MCI", "Rightel", "Home")          # "" = the kit asks the person at the start; Home = a home line, not a SIM's hotspot
MAX_TESTERS = 30
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,29}")          # (ASCII: the kit is a PowerShell 5.1 script and prints the name in its greeting)
CITY_RE = re.compile(r"[A-Za-z][A-Za-z ]{1,23}")
ROOT = "nikatel-iran-test/"
KIT_DIR, OUT_DIR = "field-kit", "field-kits"


def _dir(name: str) -> Path:
    return get_settings().data_dir / name


def slug(name: str, city: str) -> str:
    """Pure. The id of a tester: name and city in lower case letters, digits and dashes (30 at most)."""
    return re.sub(r"[^a-z0-9]+", "-", f"{name} {city}".lower()).strip("-")[:30].strip("-")


def token_name(tid: str) -> str:
    return f"tester-{tid}"


def sender_of(tid: str) -> str:
    """How the runs of this tester are signed in the table of runs (services/field.ingest: `token:<name of the token>`)."""
    return "token:" + token_name(tid)


def _rows() -> list[dict]:
    return [dict(t) for t in (st.get("field").get("testers") or []) if isinstance(t, dict) and t.get("id")]


def _save(rows: list[dict]) -> None:
    st.put("field", {"testers": rows})


def check(name: str, city: str, sim: str) -> tuple[str, str, str]:
    """Pure. The three words of a tester made safe: name, city, SIM; ValueError with a sentence."""
    name, city, sim = " ".join(str(name or "").split()), " ".join(str(city or "").split()), str(sim or "").strip()

    if not NAME_RE.fullmatch(name):
        raise ValueError("the name: English letters, digits, space, - or _ (30 at most)")

    if not CITY_RE.fullmatch(city):
        raise ValueError("the city: English letters (for example Shiraz), 2 to 24")

    if sim not in SIMS:
        raise ValueError("the SIM must be one of: " + ", ".join(s or "(ask)" for s in SIMS))

    return name, city, sim


def template_problem() -> str:
    """"" when the files every kit is made of are on this server; else the sentence that says what is missing."""
    d = _dir(KIT_DIR)
    need = ("kit.ps1", "START.bat", "xray/xray.exe")
    gone = [f for f in need if not (d / f).is_file()]

    if gone:
        return f"the kit files are not on this server: {', '.join(gone)} (put the kit folder in {d})"

    if not (st.get("field").get("upload_urls") or []):
        return "the address the kit sends to is not set (settings key field, upload_urls)"

    return ""


def _targets_json(m: dict) -> str:
    keys = ("label", "file", "host", "port", "net", "sec", "sni", "kind", "deep")
    return json.dumps([{k: t.get(k) for k in keys} for t in m["targets"]], indent=1)


def build_zip(t: dict, token: str, m: dict, urls: list[str]) -> bytes:
    """The zip of one tester: the common files + the list of this minute + that person's who.json and upload.json. Pure apart from reading the common files."""
    kit = _dir(KIT_DIR)
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(kit.rglob("*")):
            if p.is_file() and p.name not in ("who.json", "upload.json", "targets.json", "panel.json"):
                z.write(p, ROOT + p.relative_to(kit).as_posix())

        z.writestr(ROOT + "targets.json", _targets_json(m))

        for tg in m["targets"]:
            z.writestr(ROOT + "configs/" + re.sub(r"[^A-Za-z0-9._-]", "_", str(tg["file"])), str(tg["config"]))

        z.writestr(ROOT + "panel.json", json.dumps(m["panel"], indent=1))
        z.writestr(ROOT + "upload.json", json.dumps({"urls": list(urls), "token": token}))
        z.writestr(ROOT + "who.json", json.dumps({"name": t["name"], "city": t["city"], "sim": t.get("sim", "")}))

    return buf.getvalue()


def _write(tid: str, data: bytes) -> None:
    d = _dir(OUT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{tid}.zip"
    p.write_bytes(data)
    os.chmod(p, 0o600)


def zip_file(tid: str) -> Path | None:
    p = _dir(OUT_DIR) / f"{tid}.zip"
    return p if p.is_file() and any(t["id"] == tid for t in _rows()) else None


def _make(t: dict) -> dict:
    """A fresh token and a fresh zip for `t`. The token keeps its name (the runs of a tester are signed with it), so the old one is revoked first: the old kit stops working. Returns the updated record."""
    problem = template_problem()

    if problem:
        raise ValueError(problem)

    m = fieldkit.manifest()          # (first: a panel that does not answer leaves nothing changed)

    if t.get("token_id"):
        try:
            tokens.revoke(int(t["token_id"]))
        except ValueError:
            pass

    token, row = tokens.create(token_name(t["id"]), "field", by="admin")

    try:
        _write(t["id"], build_zip(t, token, m, list(st.get("field").get("upload_urls") or [])))
    except Exception:
        tokens.revoke(row["id"])
        raise

    return {**t, "token_id": row["id"], "built_at": int(time.time()), "list": str(m.get("version") or "")}


def add(name: str, city: str, sim: str = "") -> dict:
    name, city, sim = check(name, city, sim)
    rows = _rows()
    tid = slug(name, city)

    if not tid:
        raise ValueError("the name has no letters or digits")

    if any(t["id"] == tid for t in rows):
        raise ValueError(f"{name} in {city} is already a tester")

    if len(rows) >= MAX_TESTERS:
        raise ValueError(f"{MAX_TESTERS} testers at most")

    t = _make({"id": tid, "name": name, "city": city, "sim": sim, "created": int(time.time())})
    _save(rows + [t])
    log.info("field tester added %s", tid)
    return t


def rebuild(tid: str) -> dict:
    """A new zip with the list of today and a new token (the old kit stops working: use it when a kit was lost or a person should stop)."""
    rows = _rows()
    t = next((x for x in rows if x["id"] == tid), None)

    if t is None:
        raise ValueError("no such tester")

    t = _make(t)
    _save([t if x["id"] == tid else x for x in rows])
    return t


def remove(tid: str) -> None:
    rows = _rows()
    t = next((x for x in rows if x["id"] == tid), None)

    if t is None:
        raise ValueError("no such tester")

    if t.get("token_id"):
        try:
            tokens.revoke(int(t["token_id"]))
        except ValueError:
            pass

    p = _dir(OUT_DIR) / f"{tid}.zip"

    if p.is_file():
        p.unlink()

    _save([x for x in rows if x["id"] != tid])          # (their runs stay: they are what the networks saw)


def view() -> list[dict]:
    """The testers with what each has sent: runs, the newest run and its network, whether the kit is ready and the token alive. No secret."""
    rows, live = _rows(), {r["id"] for r in tokens.rows()}
    out = []

    with session() as db:
        for t in rows:
            who = sender_of(t["id"])
            n = int(db.scalar(select(func.count(FieldRun.id)).where(FieldRun.sender == who)) or 0)
            last = db.scalars(select(FieldRun).where(FieldRun.sender == who).order_by(FieldRun.ts.desc()).limit(1)).first()
            out.append({"id": t["id"], "name": t["name"], "city": t["city"], "sim": t.get("sim", ""), "created": t.get("created", 0), "built_at": t.get("built_at", 0), "list": t.get("list", ""),
                        "runs": n, "last_ts": last.ts if last else 0, "last_net": f"{last.operator} · {last.connection}" if last else "",
                        "kit_ready": (_dir(OUT_DIR) / f"{t['id']}.zip").is_file(), "token_live": int(t.get("token_id") or 0) in live})

    return out
