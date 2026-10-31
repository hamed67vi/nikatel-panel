"""Editable panel settings (as opposed to deployment settings in config.py): one JSON document per
key in the `settings` table. Telegram credentials live here too - the data dir is 0700 and the API
never returns them in clear."""
from __future__ import annotations

import copy
import threading
from typing import Any

from ..db import session
from ..models import Setting

DEFAULTS: dict[str, dict[str, Any]] = {
    "telegram": {"enabled": False, "token": "", "chat_id": ""},
    # down_seconds / up_seconds: how long a thing must stay bad / good (as its checker confirmed it) before one DOWN / UP message (services/alerts.py); the older rules (failed checks in a row, minutes, stable seconds) became these two - see _alerts_today
    "alerts": {"down_seconds": 10, "up_seconds": 15, "remind_hours": 0, "flap_max": 3, "flap_quiet_hours": 6, "muted": []},
    "report": {"enabled": True, "hour": 9, "tz_offset_minutes": 210},   # 09:00 Tehran (UTC+3:30)
    "backup": {"enabled": True, "hour": 3, "keep_days": 14, "send_telegram": True, "every_minutes": 0, "tg_every": "all", "parts": {"panel": {"enabled": True, "topic": 0, "topic_name": ""}, "bot": {"enabled": True, "topic": 0, "topic_name": ""}}},
    # the smart repair is on by default (restart, rebuild, a new GRE link, another tunnel type - by itself); it can be switched off on Smart repair to work by hand
    "repair": {"auto": True, "after_seconds": 0, "level": "switch", "mode": "", "server_minutes": 10},       # after_seconds: no longer used - the Doctor starts at the first moment a tunnel is down (kept so that an old client and an old saved value do not break)   # mode: off | fix | move (the Doctor; "" = not chosen yet: derived from the old `auto` switch, so an install that had it off stays off); server_minutes: an Iran server off this long is replaced (mode move)
    "outbound": {"mode": "spread", "gateways": [], "foreign": [], "ports": [], "port_mark": 0, "routes": [], "health": {}, "users": {"mode": "manual", "after_minutes": 5, "back_minutes": 30, "auto": {}}},                         # the Outbound section (services/outbound.py): how the exits are used, the Iran servers that carry it, the forwards, what the last checks found. The exit links themselves are in the table outbound_configs
    "accounts": {"links": [], "sub_secret": ""},                                                                         # hand-made links between users with different names on different panels (services/accounts.py)
    "capacity": {"tunnels": {}, "acted": {}},                    # the tunnels whose capacity is filling and when the Doctor last looked for more (services/capacity.py)
    "doctor": {"moves": [], "suggestions": [], "back": {}, "tried": {}, "res_at": {}, "retyped": {}, "retype_done": {}},                    # the Doctor's ledger of moves, its suggestions, who came back when
    # the tunnel cores (services/engines.py): `auto` = the Doctor keeps them up to date by itself (off until the owner switches it on), `hour` = the local hour it starts (panel clock), `bad` = {core: version} that did
    # not hold on a server and was put back (never tried again by itself), `last` = what the last automatic run did
    "engines": {"auto": False, "hour": 5, "bad": {}, "last": {}},
    # the probe(s): primary + standby list [{name, api_url, api_token}]
    "probe": {"api_url": "", "api_token": "", "standby": [], "server_id": 0},
    # the shop bot: where it runs (reached over SSH with the panel's key) and its public webhook URL
    "shopbot": {"host": "", "ssh_user": "root", "ssh_port": 22, "dir": "/var/www/nikabot", "env": "/opt/nikabot/db.env", "public_url": ""},
    # the Bale relay (services/bale.py): a customer channel for the days the international internet is cut. server_id / installed_at: where it runs (an Iran server) and since when; hosts: the hosts a customer's subscription link may be on;
    # texts: the owner's own wording of the relay's messages (only those that differ from the default)
    "bale": {"server_id": 0, "installed_at": 0, "version": "", "token": "", "admins": [], "hosts": [], "auto_notice": True, "support": True, "cut_after": 180, "restore_after": 120, "texts": {}},
    # the AI assistant (Anthropic): advises inside Fix; `actions` lets its suggested steps be added to the plan
    "ai": {"enabled": False, "api_key": "", "model": "claude-sonnet-5-5", "actions": True, "status": {},   # api_key / model / status: the Anthropic record (status = the result of the last key test)
           "provider": "anthropic", "providers": {}},                                                                       # provider: the AI every call uses; providers: {id: {api_key, model, base_url, status}} of the other AIs (services/ai.py)
    # the Costs page (services/costs.py): the prices the owner typed (items), the rates of the dollar, the euro and the lira in toman (and when the site gave them), the calendar of a month, the finished months kept as they were (frozen), the last item number (seq), the income: the inventory server of the shop bot (bot_server_id)
    "costs": {"items": [], "rates": {"USD": 0, "EUR": 0, "TRY": 0}, "rates_at": "", "calendar": "jalali", "frozen": {}, "seq": 0, "income": {"bot_server_id": 0}},
    # ArvanCloud DNS: the failover of an Iran server points the records that used its address at the replacement (mode: off | approve | auto)
    "arvan": {"api_key": "", "mode": "off", "confirm_minutes": 5, "domains": [], "status": {}},
    # CPU balance: the Tunnel Doctor turns RPS on for a busy multi-core server whose network work sits on one core (auto), what was found per server
    # DNS accounts added on the DNS page (ArvanCloud accounts, Cloudflare); the main ArvanCloud key stays in "arvan" (the failover uses it). Keys never come back through the API.
    "dns": {"accounts": []},
    # the usage of the CONFIGS (hosts) of a PasarGuard panel (services/cfgstats.py): read the CDN reports of the DNS accounts (read only), and what the last read of each said
    "cfgstats": {"cdn": True, "cdn_state": {}},
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
    # the Lab page (services/profiles.py): the categories (tags) the owner made by hand, kept even while no profile carries them
    "lab": {"tags": None, "skip_nodes": []},      # tags: None = none from the start; a list = his own set (made, renamed, deleted by hand). skip_nodes: "panel:node" of the nodes he took off the page on purpose (the check does not put them back)
    # the first-run checklist of the Dashboard (services/setup.py): hidden by the owner or not
    "setup": {"dismissed": False},
    # the license token (see services/license.py); empty = the free tier, unless NIKATEL_LICENSE=owner
    "license": {"token": ""},
    # the Add tab of a PasarGuard panel (services/pg_kinds.py): the protocols the owner added to the table, and the drawers he made for them
    "pg_kinds": {"custom": [], "families": []},
    # the warm standby of a tunnel (services/standby.py): the tunnels the owner marked (by name: a rebuild or a repair gives a tunnel a new row but keeps its name), what went wrong with the build of a standby, and
    # how long a tunnel is down before the users' ports are handed to its standby
    "standby": {"marked": {}, "failed": {}, "after_seconds": 5, "touched": [], "optout": {}},         # touched: the Iran servers a handover was ever put on (read now and then for rules nobody asked for)
    # the lanes (services/lanes.py): the warm standby put to work beside its tunnel when the tunnel is nearly full. on: the owner's switch (OFF by default); tunnels: the wish of each tunnel by NAME (want, manual,
    # the share, both capacities, when the last one ended); touched: the Iran servers a lane was ever put on (their rules are read now and then)
    "lanes": {"on": False, "tunnels": {}, "touched": []},
    # the TRIDENT (services/trident.py): a tunnel shared between itself and other tunnels of other kinds between the same two servers (its strands), the users' new connections spread by live quality. tunnels: the owner's mark by
    # tunnel NAME ({"on", "since", "manual"}); applied: what stands on the Iran server by tunnel name (weights, ramp stages, the plan, when); failed: strand builds that failed; touched: the Iran servers a trident was ever put on
    "trident": {"tunnels": {}, "applied": {}, "failed": {}, "touched": [], "weather": {}, "pattern": {}, "probed": {}, "reach": {}},      # weather / pattern / probed / reach: services/weather.py - what each family of carriers does on each route now, the hourly pattern, when a pair was last probed, the reach probe's answers
    # what the Doctor knows about every pair of servers (services/knowledge.py): the night survey (on, the local hours it may run in) and the tests kept per pair and tunnel kind
    "knowledge": {"on": True, "from_hour": 2, "to_hour": 6, "pairs": {}, "incidents": []},      # the night survey (temporary tunnels, tested one pair at a time, only at night, only on quiet servers) and what the Doctor remembers of every cut
    # the way back to the better tunnel type (services/comeback.py): a cut tunnel swapped to an emergency type is tested again for its old type, with growing waits, and swapped back when it is clearly better
    "alert_orphans": {"since": {}},          # the alert state of a thing that is gone ("kind|ref") -> when the sweep first saw it missing (alerts.prune_orphans)
    "comeback": {"wants": {}, "returns": {}},          # wants: tunnel name -> {kind it should go back to, kind it runs as, cause, when it left, looks so far, next look}; returns: tunnel name -> when it was swapped back (the last 24 hours)
    # the Reviewer (services/reviewer.py), the teacher of the Doctor: ai = the AI may write the reviews (when a key is saved); reviews = what it found about the repairs that went badly (newest last); lessons = what it proposes to
    # teach the Doctor (proposed -> approved / dismissed by the owner); seq = the last lesson number
    "reviewer": {"ai": True, "reviews": [], "lessons": [], "seq": 0},
}

SECRET_FIELDS = {"telegram": ("token",), "bale": ("token",), "probe": ("api_token",), "ai": ("api_key",), "arvan": ("api_key",), "license": ("token",)}


_ALERT_OLD = ("tunnel_fails", "server_fails", "config_fails", "min_down_minutes", "up_stable_seconds")


def _alerts_today(v: dict) -> dict:
    """The alert rules saved before 1.67.0 counted failed checks in a row and minutes; the checkers confirm a fault themselves now, so only two times are left. The old numbers
    go; what the owner had CHANGED (the minutes down, the seconds good) is kept as seconds, and the defaults of the version he never touched do not hold the alerts back."""
    if not any(k in v for k in _ALERT_OLD):
        return v

    out = {k: x for k, x in v.items() if k not in _ALERT_OLD}
    minutes, stable = int(v.get("min_down_minutes", 4)), int(v.get("up_stable_seconds", 600))

    if (minutes, stable) != (4, 600):
        out.setdefault("down_seconds", minutes * 60)
        out.setdefault("up_seconds", stable)

    return out


def _stored(key: str, value: dict) -> dict:
    return _alerts_today(value) if key == "alerts" else value


def get(key: str) -> dict:
    with session() as db:
        row = db.get(Setting, key)
        merged = copy.deepcopy(DEFAULTS.get(key, {}))

        if row is not None and isinstance(row.value, dict):
            merged.update(_stored(key, row.value))

        return merged


_PUT_LOCK = threading.Lock()          # put() reads the document, changes some fields and writes ALL of it back: two puts at the same moment (a job and a page) must not lose each other's fields


def put(key: str, changes: dict) -> dict:
    """Merge `changes` into the stored document. An empty string for a secret field keeps the old value."""
    if key not in DEFAULTS:
        raise ValueError(f"unknown settings key {key!r}")

    with _PUT_LOCK, session() as db:
        row = db.get(Setting, key)
        current = copy.deepcopy(DEFAULTS[key])

        if row is not None and isinstance(row.value, dict):
            current.update(_stored(key, row.value))

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

    if key == "ai":                             # the keys of the other AIs sit one level down: they leave as a flag and four characters too
        d["providers"] = {pid: {**v, "api_key": "", "has_api_key": bool(v.get("api_key")), "api_key_tail": str(v.get("api_key") or "")[-4:] if len(str(v.get("api_key") or "")) >= 12 else ""}
                          for pid, v in (d.get("providers") or {}).items() if isinstance(v, dict)}

    return d
