"""The first-run checklist of the Dashboard (the owner, 2026-10-16: "a new user who installs the panel should be asked for the API keys at the start"): what this panel still needs from its owner
before it can do its job, each item with what is already there. Reads only - the one thing written is the "hide it" flag."""
from __future__ import annotations

from sqlalchemy import func, select

from .. import auth
from ..db import session
from ..models import Panel, Server
from . import backup, dnsprov
from . import settings as st

# key, title, what it asks for (the order is the order on the Dashboard: the API keys first)
ITEMS = (
    ("panel", "Connect your panel", "Marzban or PasarGuard: its address and an API key"),
    ("telegram", "Telegram alerts", "a bot token and the chat id"),
    ("ai", "AI assistant", "an Anthropic API key - the Tunnel Doctor asks it"),
    ("dns", "DNS account", "an ArvanCloud or Cloudflare key - CDN records and the failover"),
    ("servers", "Add your servers", "at least one Iran and one foreign server"),
    ("probe", "Probe", "tests the configs from inside Iran"),
    ("backup", "Encrypted backups", "a passphrase the backups are encrypted with"),
    ("security", "Two-step sign-in", "a code from your phone at every sign-in"),
)


def _facts() -> dict[str, tuple[bool, str]]:
    """key -> (done, a short line about what is there)."""
    with session() as db:
        n_panels = db.scalar(select(func.count()).select_from(Panel)) or 0
        n_iran = db.scalar(select(func.count()).select_from(Server).where(Server.role == "iran")) or 0
        n_foreign = db.scalar(select(func.count()).select_from(Server).where(Server.role == "foreign")) or 0

    tg, ai, probe, arvan = st.get("telegram"), st.get("ai"), st.get("probe"), st.get("arvan")
    n_dns = len(dnsprov.accounts()) + (1 if arvan.get("api_key") else 0)
    return {
        "panel": (n_panels > 0, f"{n_panels} panel{'' if n_panels == 1 else 's'}" if n_panels else "none yet"),
        "telegram": (bool(tg.get("token") and tg.get("chat_id")), ("sending alerts" if tg.get("enabled") else "saved, switched off") if tg.get("token") and tg.get("chat_id") else "not set"),
        "ai": (bool(ai.get("api_key")), "key saved" if ai.get("api_key") else "no key"),
        "dns": (n_dns > 0, f"{n_dns} account{'' if n_dns == 1 else 's'}" if n_dns else "none yet"),
        "servers": (n_iran > 0 and n_foreign > 0, f"{n_iran} Iran · {n_foreign} foreign"),
        "probe": (bool(probe.get("api_url")), "set" if probe.get("api_url") else "not set"),
        "backup": (backup.has_passphrase(), "encrypted" if backup.has_passphrase() else "no passphrase"),
        "security": (auth.totp_enabled(), "on" if auth.totp_enabled() else "off"),
    }


def state() -> dict:
    """The checklist: every item with its state, how many are done, and whether the owner hid it."""
    facts = _facts()
    items = [{"key": k, "title": t, "hint": h, "done": facts[k][0], "detail": facts[k][1]} for k, t, h in ITEMS]
    done = sum(1 for i in items if i["done"])
    return {"ok": True, "items": items, "done": done, "total": len(items), "dismissed": bool(st.get("setup").get("dismissed"))}


def dismiss(on: bool = True) -> dict:
    st.put("setup", {"dismissed": bool(on)})
    return state()