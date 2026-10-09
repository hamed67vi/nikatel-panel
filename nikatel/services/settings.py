"""Editable panel settings (as opposed to deployment settings in config.py): one JSON document per
key in the `settings` table. Telegram credentials live here too - the data dir is 0700 and the API
never returns them in clear."""
from __future__ import annotations

import copy
from typing import Any

from ..db import session
from ..models import Setting

DEFAULTS: dict[str, dict[str, Any]] = {
    "telegram": {"enabled": False, "token": "", "chat_id": ""},
    "alerts": {"tunnel_fails": 3, "server_fails": 2, "up_stable_seconds": 600, "remind_hours": 6, "muted": []},
    "report": {"enabled": True, "hour": 9, "tz_offset_minutes": 210},   # 09:00 Tehran (UTC+3:30)
    "backup": {"enabled": True, "hour": 3, "keep_days": 14, "send_telegram": True},
}

SECRET_FIELDS = {"telegram": ("token",)}


def get(key: str) -> dict:
    with session() as db:
        row = db.get(Setting, key)
        merged = copy.deepcopy(DEFAULTS.get(key, {}))

        if row is not None and isinstance(row.value, dict):
            merged.update(row.value)

        return merged


def put(key: str, changes: dict) -> dict:
    """Merge `changes` into the stored document. An empty string for a secret field keeps the old value."""
    if key not in DEFAULTS:
        raise ValueError(f"unknown settings key {key!r}")

    with session() as db:
        row = db.get(Setting, key)
        current = copy.deepcopy(DEFAULTS[key])

        if row is not None and isinstance(row.value, dict):
            current.update(row.value)

        for k, v in changes.items():
            if k not in DEFAULTS[key]:
                raise ValueError(f"unknown field {k!r} for {key}")

            if k in SECRET_FIELDS.get(key, ()) and v == "":
                continue

            current[k] = v

        if row is None:
            row = Setting(key=key, value=current)
            db.add(row)
        else:
            row.value = current

        return copy.deepcopy(current)


def public(key: str) -> dict:
    """The document with secrets replaced by a flag."""
    d = get(key)

    for f in SECRET_FIELDS.get(key, ()):
        d[f"has_{f}"] = bool(d.get(f))
        d[f] = ""

    return d
