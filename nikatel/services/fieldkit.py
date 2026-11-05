"""The LIVE LIST of the Iran test kit (1.69.233): what the kit tests today, made from the panel at the moment the kit starts, so the kit never goes stale.

The kit (what it sends back is read by services/field.py) used to carry its list of configs, panel names and addresses inside the zip: a config added, removed or changed on the panel was tested wrongly until a new zip was made. Now the kit
asks `GET /api/field/manifest` (a token of scope `field` may call it) at every start and uses what it gets; the list inside the zip stays only as the fallback for a network that cannot reach the panel. The list is made from
  - the subscription of ONE test user (settings key `field`, `user`): every vless link of it is a config of the kit, written as a ready Xray client (`%%SOCKS%%` and `%%HTTP%%` are the ports the kit gives it);
  - the LAB configs: the Reality and xHTTP-Reality inbounds of the core straight to the nodes the direct configs go to (`field.BASES`), with the test user's id - they tell "the path is throttled" from "the protocol is";
  - the panel's own subscription names and addresses (settings key `field`: panel_names, sub_names, ips), and the subscription path of the test user;
  - the newest kit number (`kit_latest`): an older kit says that a newer one exists (the script of the kit is never replaced by itself).
It also watches the test user: expiring in under 7 days, no longer active, or 80 % of its volume used is an event once a day (a user that stops makes every config of the kit fail). `parse_links`, `build` and `warnings` are pure and tested."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Panel
from . import panels, pg_cores, pg_users
from . import settings as st
from .events import emit
from .field import BASES

log = get_logger("fieldkit")
CACHE_SECONDS = 60               # the list is made once a minute at most, however many kits start
SOON_DAYS = 7                    # the test user expires in less than this: told
USED_SHARE = 0.8                 # ... or has used this share of its volume
GB = 1024 ** 3
CDN_PORTS = (443, 8443, 2083)
LABS = (("reality1845", "VLESS TCP REALITY 1845", 1845, "tcp", None), ("xhttp2082", "VLESS XHTTP REALITY 2082", 2082, "xhttp", {"path": "/", "mode": "auto"}))
_CACHE: dict = {"at": 0.0, "data": None}


# ------------------------------------------------------------------ the links of the test user -> Xray clients
def label_of(remark: str) -> str:
    """The name of a config from the remark of its link: what the customers see, without the flag or emoji in front."""
    return re.sub(r"^[^A-Za-z0-9]+", "", urllib.parse.unquote(remark).replace("\U0001F916", "").replace("\U0001F329", "")).strip()[:32]


def parse_links(text: str) -> list[dict]:
    """Pure. The vless links of a subscription (one per line, `#remark` = the config's name) -> [{label, link, uuid, host, port, q}]; a line that is not a vless link, has no name or no port is skipped, a name given twice keeps the first."""
    out, seen = [], set()

    for line in str(text or "").splitlines():
        line = line.strip()

        if not line.startswith("vless://") or "#" not in line:
            continue

        try:
            u = urllib.parse.urlsplit(line)
            port = u.port
        except ValueError:
            continue

        label = label_of(line.split("#", 1)[1])

        if not label or label in seen or not u.hostname or not port or not u.username:
            continue

        seen.add(label)
        out.append({"label": label, "link": line, "uuid": u.username, "host": u.hostname, "port": port, "q": {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}})

    return out


def outbound(it: dict) -> dict:
    """Pure. The Xray outbound of one parsed link: vless over tcp / ws / xhttp / grpc with tls, reality or nothing."""
    q, net = it["q"], it["q"].get("type", "tcp")
    ss: dict = {"network": net, "security": q.get("security", "none")}

    if ss["security"] == "tls":
        tl = {"serverName": q.get("sni", it["host"]), "fingerprint": q.get("fp", "chrome")}

        if q.get("alpn"):
            tl["alpn"] = q["alpn"].split(",")

        ss["tlsSettings"] = tl
    elif ss["security"] == "reality":
        ss["realitySettings"] = {"serverName": q.get("sni", it["host"]), "fingerprint": q.get("fp", "chrome"), "publicKey": q.get("pbk", ""), "shortId": q.get("sid", ""), "spiderX": q.get("spx", "")}

    if net == "ws":
        ss["wsSettings"] = {"path": q.get("path", "/"), "headers": {"Host": q.get("host", it["host"])}}
    elif net == "xhttp":
        ss["xhttpSettings"] = {"path": q.get("path", "/"), "host": q.get("host", it["host"]), "mode": q.get("mode", "auto")}

        if q.get("extra"):
            try:
                ss["xhttpSettings"]["extra"] = json.loads(q["extra"])
            except ValueError:
                pass
    elif net == "grpc":
        ss["grpcSettings"] = {"serviceName": q.get("serviceName", "")}

    user = {"id": it["uuid"], "encryption": q.get("encryption", "none")}

    if q.get("flow"):
        user["flow"] = q["flow"]

    return {"protocol": "vless", "settings": {"vnext": [{"address": it["host"], "port": it["port"], "users": [user]}]}, "streamSettings": ss}


def client_text(ob: dict) -> str:
    """Pure. A whole Xray client file around one outbound: a socks and an http inbound on 127.0.0.1 whose ports are the tokens %%SOCKS%% and %%HTTP%% (the kit puts its own in)."""
    conf = {"log": {"loglevel": "none"}, "inbounds": [{"tag": "socks", "listen": "127.0.0.1", "port": "%%SOCKS%%", "protocol": "socks", "settings": {"udp": False}},
                                                      {"tag": "http", "listen": "127.0.0.1", "port": "%%HTTP%%", "protocol": "http", "settings": {}}], "outbounds": [ob]}
    return json.dumps(conf, ensure_ascii=False, indent=1).replace('"%%SOCKS%%"', "%%SOCKS%%").replace('"%%HTTP%%"', "%%HTTP%%")


def kind_of(label: str, port: int, conf: dict) -> str:
    """Pure. direct (named in the settings: straight to a node), ai, cdn (a CDN port) or entry (through an Iran server) - what the report calls the config."""
    if label in (conf.get("direct") or []):
        return "direct"

    if label.upper().startswith("AI"):
        return "ai"

    return "cdn" if port in CDN_PORTS else "entry"


def _target(label: str, host: str, port: int, net: str, sec: str, sni: str, kind: str, deep: bool, ob: dict) -> dict:
    return {"label": label, "file": re.sub(r"[^A-Za-z0-9._-]+", "_", label) + ".json", "host": host, "port": port, "net": net, "sec": sec, "sni": sni, "kind": kind, "deep": deep, "config": client_text(ob)}


def lab_targets(nodes: list[dict], inbounds: dict, uuid: str) -> list[dict]:
    """Pure. The LAB configs: the Reality 1845 and xHTTP-Reality 2082 inbounds of the core straight to every node a direct config goes to (`field.BASES`), with the test user's id; an inbound the core lacks is left out."""
    addr = {n.get("name"): n.get("address") for n in nodes if isinstance(n, dict)}
    out = []

    for node in sorted(set(BASES.values())):
        ip = addr.get(node)

        if not ip:
            continue

        for lab, tag, port, net, extra in LABS:
            rs = (((inbounds or {}).get(tag) or {}).get("streamSettings") or {}).get("realitySettings") or {}

            try:
                pbk = pg_cores.public_of(rs["privateKey"])
            except (KeyError, ValueError):
                continue

            sni = ((rs.get("serverNames") or ["x"])[0] or "x").split(",")[0]
            ss = {"network": net, "security": "reality", "realitySettings": {"serverName": sni, "fingerprint": "chrome", "publicKey": pbk, "shortId": next((s for s in (rs.get("shortIds") or []) if s), ""), "spiderX": ""}}

            if extra:
                ss["xhttpSettings"] = dict(extra)

            ob = {"protocol": "vless", "settings": {"vnext": [{"address": ip, "port": port, "users": [{"id": uuid, "encryption": "none"}]}]}, "streamSettings": ss}
            out.append(_target(f"LAB-{node}-{lab}", ip, port, net, "reality", sni, "lab", True, ob))

    return out


# ------------------------------------------------------------------ the test user
def _epoch(exp) -> float | None:
    """A PasarGuard expiry (an ISO text with a Z, or seconds) as seconds since 1970; None when there is none or it cannot be read."""
    if isinstance(exp, (int, float)) and exp:
        return float(exp)

    if isinstance(exp, str) and exp:
        try:
            dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        except ValueError:
            return None

        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()

    return None


def user_info(u: dict | None, now: float) -> dict:
    """Pure. The test user in the words the kit and the warnings need: its state, the days left and the share of its volume used."""
    u = u or {}
    end = _epoch(u.get("expire"))
    days = None if end is None else (end - now) / 86400
    used, limit = u.get("used"), u.get("limit")
    share = (float(used) / float(limit)) if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit else None
    return {"name": str(u.get("username") or ""), "status": str(u.get("status") or ""), "days_left": None if days is None else round(days, 1), "used_gb": round(float(used) / GB, 2) if isinstance(used, (int, float)) else None,
            "limit_gb": round(float(limit) / GB, 2) if isinstance(limit, (int, float)) and limit else None, "used_share": None if share is None else round(share, 3)}


def warnings(info: dict) -> list[tuple[str, str]]:
    """Pure. [(key, sentence)] for a test user that is about to stop the kit: not active, expiring soon, or its volume nearly used."""
    out = []

    if info.get("status") and info["status"] != "active":
        out.append(("status", f"The test user of the Iran kit ({info.get('name', '')}) is {info['status']}: every config of the kit fails until it is active again."))

    d = info.get("days_left")

    if d is not None and d < SOON_DAYS:
        out.append(("expire", f"The test user of the Iran kit expires in {max(d, 0):.0f} day(s): extend it in PasarGuard (the same user: nothing in the kit changes) or every config of the kit stops."))

    s = info.get("used_share")

    if s is not None and s >= USED_SHARE:
        out.append(("volume", f"The test user of the Iran kit has used {s * 100:.0f} % of its volume: raise its limit in PasarGuard."))

    return out


# ------------------------------------------------------------------ the whole list
def build(text: str, nodes: list[dict], inbounds: dict, conf: dict, user: dict | None = None, now: float | None = None) -> dict:
    """Pure. The manifest: the test user's links as Xray clients, the LAB configs, the panel's names and addresses, the newest kit number, the test user's state, and a short `version` that changes when anything the kit tests changes."""
    now = float(now or time.time())
    items = parse_links(text)

    if not items:
        raise ValueError("the test user's subscription holds no vless link")

    deep = set(conf.get("deep") or [])
    targets = [_target(it["label"], it["host"], it["port"], it["q"].get("type", "tcp"), it["q"].get("security", "none"), it["q"].get("sni", it["host"]), kind_of(it["label"], it["port"], conf), it["label"] in deep, outbound(it)) for it in items]
    targets += lab_targets(nodes, inbounds, items[0]["uuid"])
    m = re.match(r"https?://[^/]+(/sub/[^/?#]+)", str((user or {}).get("sub") or ""))
    panel = {"names": list(conf.get("panel_names") or []), "sub_names": list(conf.get("sub_names") or []), "sub_path": m.group(1) if m else "", "ips": list(conf.get("ips") or [])}
    version = hashlib.sha1(json.dumps([[t["label"], t["config"]] for t in targets] + [panel], sort_keys=True).encode()).hexdigest()[:8]
    return {"version": version, "built_at": int(now), "kit_latest": str(conf.get("kit_latest") or ""), "targets": targets, "panel": panel, "user": user_info(user, now)}


def _pg_panel() -> tuple[int, list[dict]]:
    """The PasarGuard panel the test user lives on and its nodes (the table the panel keeps)."""
    with session() as db:
        p = db.scalar(select(Panel).where(Panel.enabled.is_(True), Panel.type == "pasarguard").order_by(Panel.id))

        if p is None:
            raise ValueError("no PasarGuard panel is connected")

        return p.id, [n for n in (p.nodes or []) if isinstance(n, dict)]


def _tell(info: dict, now: float) -> None:
    """The warnings about the test user, each once a day (settings key field, `told`)."""
    conf = st.get("field")
    told, day, sent = dict(conf.get("told") or {}), int(now // 86400), False

    with session() as db:
        for key, text in warnings(info):
            if told.get(key) != day:
                told[key] = day
                sent = True
                emit(db, "warn", "configs", text, "iran-kit")

    if sent:
        st.put("field", {"told": told})


def manifest() -> dict:
    """The live list of what the kit tests (see the module text): read from the panel, cached for a minute. ValueError when no test user is set or it has no links; PanelError when the panel does not answer."""
    conf = st.get("field")

    if not conf.get("user"):
        raise ValueError("no test user is set for the kit (settings key field, user)")

    now = time.time()

    if _CACHE["data"] is not None and now - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["data"]

    pid, nodes = _pg_panel()
    c = panels.cred_for(pid)
    user = pg_users.user_view(c, conf["user"])["user"]
    text = pg_users.subscription(c, conf["user"], "links")["text"]
    core = panels.call(c, "GET", "/api/core/1")
    inbounds = {i.get("tag"): i for i in (((core or {}).get("config") or core or {}).get("inbounds") or []) if isinstance(i, dict)}
    m = build(text, nodes, inbounds, conf, user, now)
    _tell(m["user"], now)
    _CACHE.update(at=now, data=m)
    return m
