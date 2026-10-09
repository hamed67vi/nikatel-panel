"""Configs: mirror of the probe's list with live state, history and events; add / edit / delete through
the probe; subscriptions whose nodes become a group of configs."""
from __future__ import annotations

import base64
import re
import time
import urllib.parse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from ..db import session
from ..log import get_logger
from ..models import Config, ConfigSample, Subscription
from . import probe
from .events import emit

log = get_logger("configs")
KEEP_SAMPLES_SECONDS = 7 * 86400
_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://")


# ------------------------------------------------------------------ pure helpers
def parse_health(answer: dict) -> list[dict]:
    """The probe's /api/configs -> one flat dict per config."""
    out = []

    for c in answer.get("configs") or []:
        sp = c.get("speed") or {}
        out.append({"name": str(c.get("name", "")), "slug": str(c.get("slug", "")), "port": int(c.get("port") or 0), "enabled": c.get("enabled", True) is not False,
                    "status": "up" if c.get("status") == "up" else ("down" if c.get("status") else "unknown"), "ping_ms": c.get("ping"),
                    "error": str(c.get("error") or "")[:300], "checked_at": int(c.get("checked_at") or 0),
                    "down_mbps": sp.get("download") if sp.get("ok") else None, "up_mbps": sp.get("upload") if sp.get("ok") else None, "speed_at": str(sp.get("timestamp") or "")})

    return out


def link_name(uri: str) -> str:
    """The remark (#fragment or vmess 'ps') of a link, or ''."""
    uri = uri.strip()

    if uri.startswith("vmess://"):
        try:
            import json

            s = uri[8:].replace("-", "+").replace("_", "/")
            return str(json.loads(base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")).get("ps") or "")
        except (ValueError, UnicodeDecodeError):
            return ""

    return urllib.parse.unquote(uri.split("#", 1)[1]) if "#" in uri else ""


def with_name(uri: str, name: str) -> str:
    """The same link with its remark replaced (so the probe names the config as we want)."""
    uri = uri.strip()

    if uri.startswith("vmess://"):
        try:
            import json

            s = uri[8:].replace("-", "+").replace("_", "/")
            j = json.loads(base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace"))
            j["ps"] = name
            return "vmess://" + base64.b64encode(json.dumps(j, ensure_ascii=False).encode()).decode()
        except (ValueError, UnicodeDecodeError):
            return uri

    return uri.split("#", 1)[0] + "#" + urllib.parse.quote(name)


def clean_name(s: str) -> str:
    """What the probe does with names: letters, digits, space . - _ ; at most 32 chars."""
    s = re.sub(r"[^\w .\-]+", " ", str(s or ""))
    return re.sub(r"\s+", " ", s).strip(" ._-")[:32].strip()


def decode_subscription(body: bytes) -> list[str]:
    """A subscription body (base64 or plain) -> list of config links."""
    text = body.decode("utf-8", "replace").strip()

    if not any(text.startswith(s) for s in _SCHEMES):
        try:
            compact = re.sub(r"\s+", "", text).replace("-", "+").replace("_", "/")
            text = base64.b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8", "replace")
        except ValueError:
            pass

    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith(_SCHEMES)]


# ------------------------------------------------------------------ sync job
def sync() -> None:
    try:
        rows = parse_health(probe.health())
    except RuntimeError as e:
        log.warning("sync skipped: %s", e)

        with session() as db:
            emit_once_probe_down(db, str(e))

        return

    now = int(time.time())

    with session() as db:
        by_name = {c.name: c for c in db.scalars(select(Config))}
        seen = set()

        for r in rows:
            c = by_name.get(r["name"])

            if c is None:
                c = Config(name=r["name"], created_at=now)
                db.add(c)
                db.flush()
                emit(db, "info", "configs", f"Config {r['name']} appeared on the probe.", r["name"])

            seen.add(r["name"])
            old = c.status
            c.slug, c.port, c.enabled = r["slug"], r["port"], r["enabled"]
            c.ping_ms, c.last_error, c.checked_at = r["ping_ms"], r["error"], r["checked_at"] or now

            if r["down_mbps"] is not None:
                c.down_mbps, c.up_mbps, c.speed_at = r["down_mbps"], r["up_mbps"], r["speed_at"]

            if r["status"] != old:
                c.status, c.status_since = r["status"], now

                if r["status"] == "down":
                    emit(db, "bad", "configs", f"Config {c.name} is not working: {r['error'] or 'the probe gets no answer through it'}", c.name)
                elif r["status"] == "up" and old == "down":
                    emit(db, "ok", "configs", f"Config {c.name} works again.", c.name)

            db.add(ConfigSample(config_id=c.id, ts=now, up=r["status"] == "up", ping_ms=r["ping_ms"]))

        for name, c in by_name.items():
            if name not in seen:
                emit(db, "warn", "configs", f"Config {name} is gone from the probe; removed from the list.", name)
                db.delete(c)

        db.flush()
        db.execute(delete(ConfigSample).where(ConfigSample.ts < now - KEEP_SAMPLES_SECONDS))

    log.info("sync ok configs=%s up=%s", len(rows), sum(1 for r in rows if r["status"] == "up"))


_PROBE_DOWN = {"at": 0}


def emit_once_probe_down(db, why: str) -> None:
    now = int(time.time())

    if now - _PROBE_DOWN["at"] > 1800:
        _PROBE_DOWN["at"] = now
        emit(db, "bad", "probe", f"The probe does not answer: {why}", "")


# ------------------------------------------------------------------ changes through the probe
def add(uri: str, name: str = "", group: str = "") -> str:
    uri = uri.strip()

    if not uri.startswith(_SCHEMES):
        raise ValueError("paste a vless://, vmess://, trojan:// or ss:// link")

    wanted = clean_name(name or link_name(uri))

    if not wanted:
        raise ValueError("give the config a name")

    with session() as db:
        if db.scalar(select(Config).where(Config.name == wanted)):
            raise ValueError(f"a config named {wanted} exists")

    probe.add_config(with_name(uri, wanted))

    with session() as db:
        c = Config(name=wanted, group=group[:64], status="unknown", status_since=int(time.time()))
        db.add(c)
        emit(db, "info", "configs", f"Config {wanted} added" + (f" to group {group}" if group else "") + ".", wanted)

    return wanted


def update(name: str, uri: str = "", new_name: str = "", group: str | None = None, tunnel: str | None = None, cdn_port: str | None = None) -> None:
    with session() as db:
        c = db.scalar(select(Config).where(Config.name == name))

        if c is None:
            raise ValueError("config not found")

    new_clean = clean_name(new_name) if new_name else ""

    if uri or (new_clean and new_clean != name):
        if uri and not uri.strip().startswith(_SCHEMES):
            raise ValueError("this is not a config link")

        probe.update_config(name, with_name(uri, new_clean or name) if uri else "", new_clean)

    with session() as db:
        c = db.scalar(select(Config).where(Config.name == name))

        if new_clean and new_clean != name:
            c.name = new_clean

        if group is not None:
            c.group = group[:64]

        if tunnel is not None:
            c.tunnel = tunnel[:80]

        if cdn_port is not None:
            c.cdn_port = cdn_port[:8]

        emit(db, "info", "configs", f"Config {name} changed" + (f" (now {new_clean})" if new_clean and new_clean != name else "") + ".", c.name)


def remove(name: str) -> None:
    probe.delete_config(name)

    with session() as db:
        c = db.scalar(select(Config).where(Config.name == name))

        if c is not None:
            db.delete(c)

        emit(db, "warn", "configs", f"Config {name} deleted from the probe.", name)


# ------------------------------------------------------------------ subscriptions
def fetch_subscription(url: str) -> list[dict]:
    """Download and decode; returns [{name, uri, proto}] - nothing is added yet."""
    if not re.match(r"^https?://", url or ""):
        raise ValueError("the subscription link must start with http:// or https://")

    try:
        r = httpx.get(url, timeout=40, follow_redirects=True, headers={"User-Agent": "v2rayN/6.0"})
    except httpx.HTTPError as e:
        raise RuntimeError(f"could not download the subscription: {str(e)[:80]}")

    if r.status_code >= 400:
        raise RuntimeError(f"the subscription answered HTTP {r.status_code}")

    links = decode_subscription(r.content)

    if not links:
        raise RuntimeError("no vless / vmess / trojan / ss links in this subscription")

    return [{"name": clean_name(link_name(u)) or f"node{i + 1}", "uri": u, "proto": u.split("://", 1)[0]} for i, u in enumerate(links)]


def import_subscription(name: str, url: str, picks: list[str] | None = None, prefix: str = "") -> dict:
    """Add the chosen nodes (or all) as configs of group `name`. Existing names are skipped.
    With `prefix` every config is named `<prefix> <node>` so a panel's test nodes never clash with hand-added ones."""
    name = clean_name(name)

    if not name:
        raise ValueError("give the group a name")

    nodes = fetch_subscription(url)
    added, skipped, failed = [], [], []

    with session() as db:
        existing = {c.name for c in db.scalars(select(Config))}
        sub = db.scalar(select(Subscription).where(Subscription.name == name)) or Subscription(name=name, url=url)
        sub.url = url
        db.add(sub)

    for n in nodes:
        if picks is not None and n["name"] not in picks:
            continue

        full = clean_name(f"{prefix} {n['name']}" if prefix else n["name"])

        if full in existing:
            skipped.append(full)
            continue

        try:
            probe.add_config(with_name(n["uri"], full))
        except RuntimeError as e:
            failed.append(f"{full}: {str(e)[:60]}")
            continue

        try:
            with session() as db:
                db.add(Config(name=full, group=name, status="unknown", status_since=int(time.time())))
        except IntegrityError:          # the sync job registered it a moment ago: it is there, that is all we need
            pass

        added.append(full)
        existing.add(full)

    with session() as db:
        sub = db.scalar(select(Subscription).where(Subscription.name == name))
        sub.nodes = sorted(set(list(sub.nodes or []) + added + skipped))
        sub.last_import = int(time.time())
        emit(db, "info", "configs", f"Subscription {name}: {len(added)} configs added, {len(skipped)} already there, {len(failed)} failed.", name)

    return {"added": added, "skipped": skipped, "failed": failed}
