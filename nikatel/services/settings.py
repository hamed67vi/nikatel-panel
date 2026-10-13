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
    "alerts": {"tunnel_fails": 3, "server_fails": 2, "config_fails": 3, "min_down_minutes": 4, "up_stable_seconds": 600, "remind_hours": 0, "flap_max": 3, "flap_quiet_hours": 6, "muted": []},
    "report": {"enabled": True, "hour": 9, "tz_offset_minutes": 210},   # 09:00 Tehran (UTC+3:30)
    "backup": {"enabled": True, "hour": 3, "keep_days": 14, "send_telegram": True, "every_minutes": 0, "tg_every": "all", "parts": {"panel": {"enabled": True, "topic": 0, "topic_name": ""}, "bot": {"enabled": True, "topic": 0, "topic_name": ""}}},
    # the smart repair is on by default (restart, rebuild, a new GRE link, another tunnel type - by itself); it can be switched off on Smart repair to work by hand
    "repair": {"auto": True, "after_seconds": 60, "level": "switch", "mode": "", "server_minutes": 10},   # mode: off | fix | move (the Doctor; "" = not chosen yet: derived from the old `auto` switch, so an install that had it off stays off); server_minutes: an Iran server off this long is replaced (mode move)
    "accounts": {"links": []},                                                                          # hand-made links between users with different names on different panels (services/accounts.py)
    "doctor": {"moves": [], "suggestions": [], "back": {}, "tried": {}, "res_at": {}},                    # the Doctor's ledger of moves, its suggestions, who came back when
    # the probe(s): primary + standby list [{name, api_url, api_token}]
    "probe": {"api_url": "", "api_token": "", "standby": [], "server_id": 0},
    # the shop bot: where it runs (reached over SSH with the panel's key) and its public webhook URL
    "shopbot": {"host": "", "ssh_user": "root", "ssh_port": 22, "dir": "/var/www/nikabot", "env": "/opt/nikabot/db.env", "public_url": ""},
    # the AI assistant (Anthropic): advises inside Fix; `actions` lets its suggested steps be added to the plan
    "ai": {"enabled": False, "api_key": "", "model": "claude-sonnet-5-5", "actions": True, "status": {}},   # status: the result of the last key test
    # ArvanCloud DNS: the failover of an Iran server points the records that used its address at the replacement (mode: off | approve | auto)
    "arvan": {"api_key": "", "mode": "off", "confirm_minutes": 5, "domains": [], "status": {}},
    # CPU balance: the Tunnel Doctor turns RPS on for a busy multi-core server whose network work sits on one core (auto), what was found per server
    # DNS accounts added on the DNS page (ArvanCloud accounts, Cloudflare); the main ArvanCloud key stays in "arvan" (the failover uses it). Keys never come back through the API.
    "dns": {"accounts": []},
    "balance": {"auto": True, "servers": {}},
    "underuse": {"auto": True},       # the Doctor fixes what the kernel settings can fix on a server that carries little (services/underuse.py)
    "failover": {"seen": {}, "pending": [], "history": [], "dns_log": [], "tried": {}},   # dns_log: every record the failover re-pointed, so each one can be put back; tried: dead servers looked at without a target
    # rebuilds that were started but not finished: a restart of the panel in the middle must not leave a removed tunnel unbuilt
    "pending_builds": {"items": []},
    # tunnels removed on request: a copy of each so that it can be restored (30 days)
    "trash": {"items": []},
    # installs on a server (torrent block, Marzban node) that the panel is following: picked up again when the panel starts
    "pending_installs": {"items": []},
    # the secret entry path: when set, the panel answers 404 to everything until the browser has opened /<entry_path>
    "security": {"entry_path": ""},
    # Add server → Marzban node: the certificate of the Marzban panel that is filled in by default (a public certificate, kept in the panel database only)
    "nodes": {"marzban_cert": ""},
    # the license token (see services/license.py); empty = the free tier, unless NIKATEL_LICENSE=owner
    "license": {"token": ""},
}

SECRET_FIELDS = {"telegram": ("token",), "probe": ("api_token",), "ai": ("api_key",), "arvan": ("api_key",), "license": ("token",)}


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
        if key in ("ai", "arvan"):
            d[f"{f}_tail"] = str(d.get(f) or "")[-4:] if len(str(d.get(f) or "")) >= 12 else ""   # the last four characters tell which key it is

        d[f] = ""

    return d
