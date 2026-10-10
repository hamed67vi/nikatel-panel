"""Configs: mirror of the probe's list with live state, history and events; add / edit / delete through
the probe; subscriptions whose nodes become a group of configs."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import urllib.parse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from ..db import session
from ..log import get_logger
from ..models import Config, ConfigSample, Server, Subscription
from ..ssh import Node, SSHError, run_script
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


# ------------------------------------------------------------------ what a link says (type, address, port, transport, TLS)
def meta_from_uri(uri: str) -> dict:
    """{'proto','address','port','network','security'} from a vless / vmess / trojan / ss link. No secret is kept."""
    from urllib.parse import parse_qs, urlsplit

    uri = str(uri).strip()
    proto = uri.split("://", 1)[0].lower() if "://" in uri else ""
    m = {"proto": proto, "address": "", "port": 0, "network": "tcp", "security": "none"}

    try:
        if proto == "vmess":
            raw = uri[8:].split("#", 1)[0]
            j = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
            m.update(address=str(j.get("add", "")), port=int(j.get("port") or 0), network=str(j.get("net") or "tcp"), security="tls" if str(j.get("tls", "")).lower() == "tls" else "none")
        elif proto in ("vless", "trojan"):
            u = urlsplit(uri)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            m.update(address=u.hostname or "", port=u.port or 0, network=q.get("type", "tcp"), security=q.get("security") or ("tls" if proto == "trojan" else "none"))
        elif proto == "ss":
            rest = uri[5:].split("#", 1)[0].split("?", 1)[0]

            if "@" not in rest:
                rest = base64.urlsafe_b64decode(rest + "=" * (-len(rest) % 4)).decode()

            hostport = rest.rsplit("@", 1)[1]
            host, _, port = hostport.rpartition(":")
            m.update(address=host.strip("[]"), port=int(port) if port.isdigit() else 0)
    except (ValueError, IndexError, KeyError, UnicodeError):
        pass

    return m


def assign_groups() -> int:
    """A config without a group whose name starts with a subscription's name ("<group> <node>") belongs to that group."""
    with session() as db:
        groups = sorted({s.name for s in db.scalars(select(Subscription))} | {c.group for c in db.scalars(select(Config)) if c.group}, key=len, reverse=True)
        n = 0

        for c in db.scalars(select(Config).where(Config.group == "")):
            g = next((x for x in groups if x and c.name.startswith(x + " ")), None)

            if g:
                c.group = g
                n += 1

    return n


def fill_meta(limit: int = 20) -> int:
    """Read the link of configs that have no meta yet (from the probe) and keep the readable facts. A few per run."""
    with session() as db:
        names = [c.name for c in db.scalars(select(Config).where(Config.enabled.is_(True)))
                 if not (c.meta or {}).get("proto")][:limit]

    done = 0

    for name in names:
        try:
            meta = meta_from_uri(probe.config_uri(name))
        except (RuntimeError, ValueError):
            continue

        if not meta["proto"]:
            continue

        with session() as db:
            c = db.scalar(select(Config).where(Config.name == name))

            if c is not None:
                c.meta = meta
                done += 1

    return done


# ------------------------------------------------------------------ sync job
def sync() -> None:
    down_tcp: list[str] = []

    try:
        down_tcp = _sync()
    finally:
        try:
            assign_groups()
            fill_meta()
        except Exception:
            log.exception("meta fill failed")

    try:
        refresh_reach(down_tcp)
    except Exception:
        log.exception("reach check failed")


REACH_TTL = 300     # seconds a reachability answer is kept


def _direct_tcp(meta: dict | None) -> bool:
    """A config that goes straight to a foreign server over raw TCP (also with Reality). For these "the port opens from Iran" is what tells
    filtered from not filtered: the probe's full proxy test from a data centre fails on paths that work for people."""
    m = meta or {}
    return m.get("network") == "tcp" and bool(m.get("address")) and bool(m.get("port"))


def refresh_reach(names: list[str]) -> None:
    """For direct TCP configs the probe calls down: can the first two Iran servers open the port? Kept in meta["reach"] for REACH_TTL seconds."""
    if not names:
        return

    now = int(time.time())

    with session() as db:
        todo: dict[str, list[int]] = {}

        for c in db.scalars(select(Config).where(Config.name.in_(names))):
            m = c.meta or {}

            if _direct_tcp(m) and now - int((m.get("reach") or {}).get("at", 0)) >= REACH_TTL:
                todo.setdefault(f"{m['address']}:{m['port']}", []).append(c.id)

        vantage = [Node(s.host, s.ssh_user, s.ssh_port) for s in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True)).order_by(Server.id).limit(2))]

    if not todo or not vantage:
        return

    answers: dict[str, tuple[bool, int | None]] = {}      # host:port -> (opens, fastest connect time in ms)
    targets = list(todo)

    for i in range(0, len(targets), 40):
        for node in vantage:
            try:
                out = run_script(node, "tcp_reach", {"TARGETS": " ".join(targets[i:i + 40])}, timeout=40)
            except (SSHError, ValueError):
                continue

            for ln in out.splitlines():
                p = ln.split()

                if len(p) >= 3 and p[0] == "REACH":
                    ok, ms = p[2] == "1", (int(p[3]) if len(p) > 3 and p[3].isdigit() else 0) or None
                    was_ok, was_ms = answers.get(p[1], (False, None))
                    answers[p[1]] = (was_ok or ok, min(x for x in (was_ms, ms if ok else None) if x) if (was_ms or (ok and ms)) else None)

    with session() as db:
        for hp, ids in todo.items():
            if hp not in answers:
                continue

            for cid in ids:
                c = db.get(Config, cid)

                if c is not None:
                    c.meta = {**(c.meta or {}), "reach": {"ok": answers[hp][0], "ms": answers[hp][1], "at": now}}


def _sync() -> list[str]:
    try:
        with _PLOCK:
            pending = set(_PENDING)

        rows = [r for r in parse_health(probe.health()) if r["name"] not in pending]
    except RuntimeError as e:
        log.warning("sync skipped: %s", e)

        with session() as db:
            emit_once_probe_down(db, str(e))

        return []

    now = int(time.time())
    down_tcp: list[str] = []

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
            m = c.meta or {}

            if _direct_tcp(m):          # direct TCP: down only when the port really does not open from Iran
                reach = m.get("reach") or {}
                fresh = now - int(reach.get("at", 0)) < REACH_TTL * 6

                if r["status"] == "down":
                    down_tcp.append(c.name)

                    if reach.get("ok") and fresh:
                        r = {**r, "status": "up", "ping_ms": reach.get("ms"), "error": "the port opens from Iran (the delay is the connect time); the probe's full proxy test does not pass from a data centre"}
                    elif reach.get("ok") is False and fresh:
                        r = {**r, "error": "the port does not open from Iran: the address is filtered or its server is off"}
                elif r["status"] == "up" and reach:
                    c.meta = {k: v for k, v in m.items() if k != "reach"}

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
    return down_tcp


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
        c = Config(name=wanted, group=group[:64], status="unknown", status_since=int(time.time()), meta=meta_from_uri(uri))
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

        if uri:
            c.meta = meta_from_uri(uri)

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


_PENDING: set[str] = set()      # names being deleted on the probe right now: the sync must not bring them back meanwhile
_PLOCK = threading.Lock()


def _spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def remove_fast(names: list[str]) -> dict:
    """Delete configs at once for the user: the rows (and their samples) go from the panel in this call, the probe is told in the
    background with ONE bulk call (the probe needs seconds per config to restart its services). If the probe refuses, a warning
    event says so and the config comes back with the next sync."""
    names = [n for n in dict.fromkeys(names) if n]

    if not names:
        return {"deleted": 0}

    with session() as db:
        rows = list(db.scalars(select(Config).where(Config.name.in_(names))))

        for c in rows:
            db.delete(c)

        emit(db, "warn", "configs", f"Config {rows[0].name} deleted." if len(rows) == 1 else f"{len(rows)} configs deleted.", rows[0].name if len(rows) == 1 else "")

    with _PLOCK:
        _PENDING.update(names)

    _spawn(_probe_delete, names)
    return {"deleted": len(rows)}


def _probe_delete(names: list[str]) -> None:
    try:
        failed = {n: why for n, why in probe.delete_many(names).items() if why is not None}
    except RuntimeError as e:
        failed = {n: str(e)[:100] for n in names}
    finally:
        with _PLOCK:
            _PENDING.difference_update(names)

    if failed:
        first = next(iter(failed))

        with session() as db:
            emit(db, "bad", "configs", f"{len(failed)} configs could not be deleted on the probe (for example {first}: {failed[first]}); they come back with the next sync.", first)

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
        rows_now = list(db.scalars(select(Config)))
        existing = {c.name for c in rows_now}
        ungrouped = {c.name for c in rows_now if not c.group}      # on the probe already, but in no group yet (the sync job registered them first)
        sub = db.scalar(select(Subscription).where(Subscription.name == name)) or Subscription(name=name, url=url)
        sub.url = url
        db.add(sub)

    todo, regroup = [], []

    for n in nodes:
        if picks is not None and n["name"] not in picks:
            continue

        full = clean_name(f"{prefix} {n['name']}" if prefix else n["name"])

        if full in existing:
            skipped.append(full)

            if full in ungrouped:
                regroup.append(full)

            continue

        todo.append((full, n["uri"]))

    if todo:   # one call for all of them: the probe restarts its services once, not once per config
        try:
            res = probe.add_many([{"uri": with_name(uri, full), "name": full} for full, uri in todo])
        except RuntimeError as e:
            res = {full: str(e)[:60] for full, _ in todo}

        for full, uri in todo:
            why = res.get(full, "no answer for this one")

            if why:
                failed.append(f"{full}: {str(why)[:60]}")
                continue

            regroup.append(full)            # the sync job may have registered it a moment ago without a group: it joins this one

            try:
                with session() as db:
                    if db.scalar(select(Config).where(Config.name == full)) is None:
                        db.add(Config(name=full, group=name, status="unknown", status_since=int(time.time()), meta=meta_from_uri(uri)))
            except IntegrityError:          # registered by the sync job in the same instant: the regroup below puts it in the group
                pass

            added.append(full)
            existing.add(full)
    if regroup:
        with session() as db:
            for c in db.scalars(select(Config).where(Config.name.in_(regroup), Config.group == "")):
                c.group = name

    with session() as db:
        sub = db.scalar(select(Subscription).where(Subscription.name == name))
        sub.nodes = sorted(set(list(sub.nodes or []) + added + skipped))
        sub.last_import = int(time.time())
        emit(db, "info", "configs", f"Subscription {name}: {len(added)} configs added, {len(skipped)} already there, {len(failed)} failed.", name)

    return {"added": added, "skipped": skipped, "failed": failed}
