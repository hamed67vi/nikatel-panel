"""The Bale relay: a customer channel that still works when the international internet is cut.

Telegram and every server abroad are out of reach in a cut; Bale (a messenger inside Iran) and an Iran server are not. The relay (relay/bale_relay.py in this repository, Python standard library only) is a small Bale bot
that runs on ONE Iran server of the inventory: customers start it and link their subscription, and it tells everybody who started it when the server loses the outside world (and when it is back), by itself.
This module installs it over SSH like the probe (the reviewed actions `relay_prepare`, `put_file`, `unit_enable_restart`, `relay_remove`), writes its config, reads its status and queues a broadcast.
The panel is only the operator's console: once the config is on the server the relay needs nothing from the panel."""
from __future__ import annotations

import base64
import importlib.util
import json
import re
import secrets
import time
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Panel, Server
from ..ssh import Node, SSHError, run_action, run_script
from . import settings as st
from .events import emit
from .panels import origin

log = get_logger("bale")
SRC = Path(__file__).resolve().parent.parent.parent / "relay" / "bale_relay.py"
HOME = "/opt/nikatel-relay"
UNIT = "nikatel-relay.service"
UNIT_TEXT = f"""[Unit]
Description=Nikatel Bale relay - customer notices while the international internet is cut
After=network-online.target
Wants=network-online.target

[Service]
Environment=NIKATEL_RELAY_HOME={HOME}
ExecStart=/usr/bin/python3 {HOME}/bale_relay.py run
Restart=always
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths={HOME}

[Install]
WantedBy=multi-user.target
"""
ANCHORS = [["1.1.1.1", 443], ["8.8.8.8", 443], ["9.9.9.9", 443]]          # when none of the owner's foreign servers and none of these answers, the outside world is gone
_TOKEN = re.compile(r"^\d{4,12}:[A-Za-z0-9_:.\-]{20,80}$")
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?(:\d{1,5})?$")
_MOD = None


def relay_module():
    """The relay's source as a module (its texts and rules are the panel's defaults too); it has no side effect on import."""
    global _MOD

    if _MOD is None:
        spec = importlib.util.spec_from_file_location("nikatel_bale_relay", SRC)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod

    return _MOD


def valid_token(t: str) -> bool:
    return bool(_TOKEN.match(t or ""))


def clean_hosts(items) -> list[str]:
    out: list[str] = []

    for x in items or []:
        h = str(x).strip().lower()

        if h and h not in out:
            if not _HOST.match(h):
                raise ValueError(f"{h[:40]!r} is not a host name (like sub.example.com, or sub.example.com:2096)")

            out.append(h)

    return out[:20]


def suggest_hosts() -> list[str]:
    """The hosts of the Marzban / PasarGuard panels the panel already knows: the first guess for where the subscription links live."""
    with session() as db:
        urls = [p.url for p in db.scalars(select(Panel).where(Panel.enabled.is_(True)))]

    out: list[str] = []

    for u in urls:
        h = origin(u)[1]

        if h and h not in out:
            out.append(h)

    return out


def watch_targets() -> list[list]:
    with session() as db:
        rows = list(db.scalars(select(Server).where(Server.role == "foreign", Server.enabled.is_(True)).order_by(Server.id)))

    return [[s.host, int(s.ssh_port or 22)] for s in rows[:3]] + ANCHORS


def config_doc() -> dict:
    c = st.get("bale")
    defaults = relay_module().DEFAULT_TEXTS
    return {"token": c["token"], "admins": [int(x) for x in c["admins"]], "allowed_hosts": list(c["hosts"]), "watch": watch_targets(), "auto_notice": bool(c["auto_notice"]), "support": bool(c["support"]),
            "cut_after": int(c["cut_after"]), "restore_after": int(c["restore_after"]), "cooldown": 21600,
            "texts": {k: v for k, v in (c.get("texts") or {}).items() if k in defaults and str(v).strip()}}


def public() -> dict:
    d = st.public("bale")
    d["defaults"] = dict(relay_module().DEFAULT_TEXTS)
    d["suggest_hosts"] = suggest_hosts()
    d["installed"] = bool(d.get("installed_at") and d.get("server_id"))
    return d


def save(changes: dict) -> dict:
    """Keep the settings (checked). When the relay is installed its config.json is written again - it reads the file by itself, nothing is restarted."""
    c = {}

    if changes.get("token"):
        if not valid_token(changes["token"]):
            raise ValueError("this does not look like a Bale bot token (digits:letters, from @botfather in Bale)")

        c["token"] = changes["token"].strip()

    if "admins" in changes:
        ids = [str(x).strip() for x in changes["admins"] if str(x).strip()]

        if any(not re.fullmatch(r"\d{3,15}", i) for i in ids):
            raise ValueError("an admin id is a number (send /id to the bot to see yours)")

        c["admins"] = [int(i) for i in ids][:10]

    if "hosts" in changes:
        c["hosts"] = clean_hosts(changes["hosts"])

    for k in ("auto_notice", "support"):
        if k in changes:
            c[k] = bool(changes[k])

    for k, lo, hi in (("cut_after", 30, 3600), ("restore_after", 10, 3600)):
        if k in changes:
            try:
                v = int(changes[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k} is a number of seconds") from None

            if not lo <= v <= hi:
                raise ValueError(f"{k}: between {lo} and {hi} seconds")

            c[k] = v

    if "texts" in changes:
        defaults = relay_module().DEFAULT_TEXTS
        t = {}

        for k, v in (changes["texts"] or {}).items():
            if k not in defaults:
                raise ValueError(f"unknown text {k!r}")

            v = str(v).strip()

            if len(v) > 1500:
                raise ValueError(f"the text {k!r} is longer than 1500 characters")

            if v and v != defaults[k]:
                t[k] = v

        c["texts"] = t

    out = st.put("bale", c) if c else st.get("bale")
    pushed, why = False, ""

    if out.get("installed_at") and out.get("server_id") and not get_settings().read_only:
        try:
            push_config()
            pushed = True
        except (SSHError, RuntimeError, ValueError) as e:
            why = str(e)[:200]

    return {**public(), "pushed": pushed, "push_error": why}


def _node(server_id: int) -> tuple[Node, str]:
    with session() as db:
        srv = db.get(Server, server_id)

        if srv is None or srv.role != "iran":
            raise ValueError("choose an Iran server")

        return Node(srv.host, srv.ssh_user, srv.ssh_port), srv.name


def _put(node: Node, path: str, data: bytes, mode: str) -> None:
    run_action(node, "put_file", {"PATH": path, "B64": base64.b64encode(data).decode(), "MODE": mode})


def _installed_node() -> tuple[Node, str]:
    c = st.get("bale")

    if not c.get("installed_at") or not c.get("server_id"):
        raise ValueError("the relay is not installed yet")

    return _node(int(c["server_id"]))


def _need_writable() -> None:
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to install or change the relay")


def push_config_to(node: Node) -> None:
    _put(node, f"{HOME}/config.json", json.dumps(config_doc(), ensure_ascii=False, indent=1).encode(), "0600")


def push_config() -> None:
    push_config_to(_installed_node()[0])


def install(server_id: int) -> dict:
    _need_writable()
    c = st.get("bale")

    if not valid_token(c.get("token", "")):
        raise ValueError("save the Bale bot token first (make a bot with @botfather in Bale)")

    if not SRC.exists():
        raise RuntimeError("the relay source is missing from the panel's checkout (relay/bale_relay.py)")

    node, name = _node(server_id)
    out = run_action(node, "relay_prepare", timeout=60)

    if "RELAY_READY" not in out:
        raise RuntimeError(next((ln[len("RELAY_FAIL "):] for ln in out.splitlines() if ln.startswith("RELAY_FAIL ")), "the server could not be prepared")[:200])

    _put(node, f"{HOME}/bale_relay.py", SRC.read_bytes(), "0644")
    _put(node, f"/etc/systemd/system/{UNIT}", UNIT_TEXT.encode(), "0644")
    push_config_to(node)
    out = run_action(node, "unit_enable_restart", {"UNIT": UNIT}, timeout=60)

    if f"UNIT {UNIT} active" not in out:
        raise RuntimeError(f"the relay did not start on {name}: " + out.strip()[-160:])

    st.put("bale", {"server_id": server_id, "installed_at": int(time.time()), "version": relay_module().VERSION})
    seen = {}

    for _ in range(10):                       # the first look at Bale is written within a few seconds
        time.sleep(2)

        try:
            seen = status().get("status") or {}
        except (SSHError, ValueError):
            seen = {}

        if (seen.get("bot") or {}).get("username") or (seen.get("bale") or {}).get("err"):
            break

    with session() as db:
        emit(db, "ok", "bale", f"Bale relay installed on {name}.", name)

    log.info("bale relay installed on %s", name)
    bot = (seen.get("bot") or {}).get("username") or ""
    err = (seen.get("bale") or {}).get("err") or ""
    return {"server": name, "unit": "active", "bot": bot, "bale_error": err}


def parse_status(out: str) -> dict:
    d: dict = {"status": {}}
    body, grab = [], False

    for ln in out.splitlines():
        if ln.startswith("STATUS"):
            grab = True
        elif ln.startswith("END"):
            grab = False
        elif grab:
            body.append(ln)
        else:
            k, _, v = ln.partition(" ")
            d[k.lower()] = v.strip()

    try:
        s = json.loads("\n".join(body) or "{}")
        d["status"] = s if isinstance(s, dict) else {}
    except ValueError:
        d["status"] = {}

    return d


def status() -> dict:
    """What the relay says about itself (read over SSH; works while the panel is read-only)."""
    c = st.get("bale")

    if not c.get("installed_at") or not c.get("server_id"):
        return {"installed": False}

    node, name = _node(int(c["server_id"]))
    d = parse_status(run_script(node, "relay_status", timeout=25))
    s = d["status"]
    d.update(installed=True, server=name, age=max(0, int(time.time()) - int(s.get("at") or 0)) if s.get("at") else None)
    return d


def broadcast(text: str, admins_only: bool = False) -> dict:
    """Queue a notice: the relay sends it to everybody who started the bot (or to the admins only, as a test)."""
    _need_writable()
    text = (text or "").strip()

    if not text or len(text) > 4000:
        raise ValueError("the notice is 1 to 4000 characters")

    node, name = _installed_node()
    c = st.get("bale")
    to = [int(x) for x in c["admins"]] if admins_only else None

    if admins_only and not to:
        raise ValueError("add your Bale id to the admins first (send /id to the bot)")

    mid = f"{int(time.time())}-{secrets.token_hex(3)}"
    doc = {"id": mid, "text": text, "kind": "test" if admins_only else "manual", "at": int(time.time())}

    if to is not None:
        doc["to"] = to

    _put(node, f"{HOME}/outbox/{mid}.msg", json.dumps(doc, ensure_ascii=False).encode(), "0600")

    with session() as db:
        emit(db, "warn" if not admins_only else "info", "bale", f"Notice {mid} queued for {'the admins' if admins_only else 'every customer on Bale'}: {text[:60]!r}", name)

    return {"id": mid, "to": "admins" if admins_only else "all"}


def remove(purge: bool = False) -> dict:
    _need_writable()
    node, name = _installed_node()
    out = run_action(node, "relay_remove", {"PURGE": "1" if purge else "0"}, timeout=60)

    if "REMOVED" not in out:
        raise RuntimeError("the relay could not be removed: " + out.strip()[-160:])

    st.put("bale", {"installed_at": 0, "server_id": 0, "version": ""})

    with session() as db:
        emit(db, "warn", "bale", f"Bale relay removed from {name}{' with its data' if purge else ' (the list of customers was kept on the server)'}.", name)

    return {"server": name, "purged": purge}
