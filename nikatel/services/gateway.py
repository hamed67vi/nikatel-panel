"""Iran gateway: a plain Xray on an Iran server.

    users -> Iran server (port P, dokodemo-door) -> [direct | a borrowed config that still has internet] -> foreign node IP:port

Borrowed configs are ordinary vless/vmess/trojan/ss links from any provider; the users keep their own
configs, the borrowed one only carries encrypted traffic. Mode `auto` lets Xray's balancer pick the
fastest alive exit (direct included) and fall back by itself when Iran is cut from the world.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import time
import urllib.parse
import uuid

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Gateway, Server
from ..ssh import Node, SSHError, run_action, run_script
from .events import emit

log = get_logger("gateway")
XGW_DIR = "/opt/nikatel-xgw"
TEST_BASE = 21080          # local SOCKS doors: 21079 direct, 21080 the gateway route, 21081+ one per borrowed config


# ------------------------------------------------------------------ link -> outbound (pure)
def _b64d(s: str) -> str:
    s = s.replace("-", "+").replace("_", "/").strip()
    return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")


def _stream(net: str, sec: str, g) -> dict:
    net = "http" if net == "h2" else (net or "tcp")
    ss: dict = {"network": net, "security": sec if sec in ("tls", "reality") else "none"}

    if ss["security"] == "tls":
        t = {"serverName": g("sni") or g("host") or g("address"), "allowInsecure": g("allowInsecure") in ("1", "true") or g("insecure") in ("1", "true")}

        if g("fp"):
            t["fingerprint"] = g("fp")

        if g("alpn"):
            t["alpn"] = [x.strip() for x in g("alpn").split(",") if x.strip()]

        ss["tlsSettings"] = t

    if ss["security"] == "reality":
        ss["realitySettings"] = {"serverName": g("sni") or g("address"), "fingerprint": g("fp") or "chrome", "publicKey": g("pbk"), "shortId": g("sid"), "spiderX": g("spx")}

    path, host = g("path") or "/", g("host")

    if net == "ws":
        ss["wsSettings"] = {"path": path, "headers": {"Host": host} if host else {}}
    elif net == "grpc":
        ss["grpcSettings"] = {"serviceName": g("serviceName"), "multiMode": g("mode") == "multi"}
    elif net == "httpupgrade":
        ss["httpupgradeSettings"] = {"path": path, "host": host}
    elif net == "xhttp":
        ss["xhttpSettings"] = {"path": path, "host": host, **({"mode": g("mode")} if g("mode") else {})}
    elif net == "http":
        ss["httpSettings"] = {"path": path, "host": [host] if host else []}
    elif net == "kcp":
        ss["kcpSettings"] = {"header": {"type": g("headerType") or "none"}, **({"seed": g("seed")} if g("seed") else {})}
    elif net == "tcp" and g("headerType") == "http":
        ss["tcpSettings"] = {"header": {"type": "http", "request": {"path": [path], "headers": {"Host": [host]} if host else {}}}}

    return ss


def outbound_from_uri(uri: str, tag: str) -> dict:
    """vless / vmess / trojan / ss link -> Xray outbound. ValueError with a sentence otherwise."""
    uri = str(uri or "").strip()
    m = re.match(r"^([a-z0-9]+)://", uri, re.IGNORECASE)

    if not m:
        raise ValueError("this is not a config link")

    proto = m.group(1).lower()

    try:
        if proto == "vmess":
            j = json.loads(_b64d(uri[8:]))
            vals = {"address": j.get("add", ""), "sni": j.get("sni", ""), "host": j.get("host", ""), "path": j.get("path", ""), "fp": j.get("fp", ""), "alpn": j.get("alpn", ""),
                    "serviceName": j.get("path", "") if j.get("net") == "grpc" else "", "headerType": j.get("type", "")}
            g = lambda k: str(vals.get(k, "") or "")
            return {"tag": tag, "protocol": "vmess", "settings": {"vnext": [{"address": j["add"], "port": int(j["port"]),
                    "users": [{"id": j["id"], "alterId": int(j.get("aid", 0) or 0), "security": j.get("scy") or "auto", "level": 0}]}]},
                    "streamSettings": _stream(j.get("net") or "tcp", "tls" if j.get("tls") == "tls" else "none", g)}

        if proto == "ss":
            body = uri[5:].split("#")[0].split("?")[0]

            if "@" not in body:
                body = _b64d(body)

            cred, server = body.rsplit("@", 1)

            if ":" not in cred:
                cred = _b64d(cred)

            method, password = cred.split(":", 1)
            sm = re.match(r"^\[?(.*?)\]?:(\d+)$", server.strip("/"))
            return {"tag": tag, "protocol": "shadowsocks", "settings": {"servers": [{"address": sm.group(1), "port": int(sm.group(2)), "method": method,
                    "password": urllib.parse.unquote(password), "level": 0}]}}

        if proto in ("vless", "trojan"):
            u = urllib.parse.urlparse(uri)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            q["address"] = u.hostname or ""
            g = lambda k: str(q.get(k, "") or "")
            ident = urllib.parse.unquote(u.username or "")

            if not q["address"] or not ident or not u.port:
                raise ValueError("the link has no address, port or id")

            ss = _stream(g("type") or "tcp", g("security") or ("tls" if proto == "trojan" else "none"), g)

            if proto == "vless":
                user = {"id": ident, "encryption": g("encryption") or "none", "level": 0, **({"flow": g("flow")} if g("flow") else {})}
                return {"tag": tag, "protocol": "vless", "settings": {"vnext": [{"address": q["address"], "port": u.port, "users": [user]}]}, "streamSettings": ss}

            srv = {"address": q["address"], "port": u.port, "password": ident, "level": 0, **({"flow": g("flow")} if g("flow") else {})}
            return {"tag": tag, "protocol": "trojan", "settings": {"servers": [srv]}, "streamSettings": ss}
    except ValueError:
        raise
    except (KeyError, TypeError, IndexError, AttributeError) as e:   # a malformed link: missing fields, bad json shape
        raise ValueError("cannot read this link (" + str(e)[:60] + ")")

    raise ValueError("only vless, vmess, trojan and ss links are supported")


def describe(uri: str) -> str:
    try:
        o = outbound_from_uri(uri, "x")
        s = o.get("settings", {})
        srv = (s.get("vnext") or s.get("servers") or [{}])[0]
        return f"{o['protocol']} · {srv.get('address', '')}:{srv.get('port', '')} · {(o.get('streamSettings') or {}).get('network', 'tcp')}"
    except ValueError:
        return "unreadable link"


# ------------------------------------------------------------------ state validation + config (pure)
def validate_state(raw: dict, old_borrowed: list | None = None) -> dict:
    old = {x["id"]: x for x in (old_borrowed or [])}
    ports, seen = [], set()

    for p in raw.get("ports") or []:
        port, tp, th = int(p.get("port", 0)), int(p.get("target_port", 0)), str(p.get("target_host", "")).strip()

        if not (1024 <= port <= 65535) or not (1 <= tp <= 65535):
            raise ValueError("ports must be 1024-65535 (listen) and 1-65535 (target)")

        if port in seen or TEST_BASE - 2 <= port <= TEST_BASE + 60:
            raise ValueError(f"port {port} is used twice or reserved for the gateway tests")

        if not re.match(r"^[A-Za-z0-9.\-]{1,120}$", th):
            raise ValueError("the target must be an IP address or a host name")

        seen.add(port)
        ports.append({"port": port, "target_host": th, "target_port": tp, "udp": bool(p.get("udp")), "enabled": p.get("enabled", True) is not False, "note": str(p.get("note", ""))[:60]})

    borrowed, ids = [], set()

    for b in raw.get("borrowed") or []:
        bid = re.sub(r"[^a-z0-9]", "", str(b.get("id") or ""))[:12] or uuid.uuid4().hex[:8]

        if bid in ids:
            bid = uuid.uuid4().hex[:8]

        ids.add(bid)
        uri = str(b.get("uri", "")).strip() or str((old.get(str(b.get("id") or "")) or {}).get("uri", ""))
        outbound_from_uri(uri, "b-" + bid)
        borrowed.append({"id": bid, "uri": uri, "note": str(b.get("note", ""))[:60], "enabled": b.get("enabled", True) is not False})

    mode = str(raw.get("mode", "auto"))
    return {"ports": ports, "borrowed": borrowed, "mode": mode if mode in ("auto", "borrowed", "direct") else "auto"}


def build_config(st: dict) -> dict:
    on = [b for b in st["borrowed"] if b.get("enabled", True)]
    outbounds = [{"tag": "direct", "protocol": "freedom", "settings": {}}, {"tag": "block", "protocol": "blackhole", "settings": {}}]
    outbounds += [outbound_from_uri(b["uri"], "b-" + b["id"]) for b in on]
    inbounds, rules, tags = [], [], []

    for p in st["ports"]:
        if p.get("enabled", True):
            tags.append(f"gw-in-{p['port']}")
            inbounds.append({"tag": tags[-1], "listen": "0.0.0.0", "port": p["port"], "protocol": "dokodemo-door",
                             "settings": {"address": p["target_host"], "port": p["target_port"], "network": "tcp,udp" if p.get("udp") else "tcp"}})

    def door(tag, port, out):
        inbounds.append({"tag": tag, "listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"udp": False}})
        rules.append({"type": "field", "inboundTag": [tag], "outboundTag": out})

    door("gw-t-direct", TEST_BASE - 1, "direct")

    for i, b in enumerate(on):
        door("gw-t-" + b["id"], TEST_BASE + 1 + i, "b-" + b["id"])

    inbounds.append({"tag": "gw-test", "listen": "127.0.0.1", "port": TEST_BASE, "protocol": "socks", "settings": {"udp": False}})
    cfg = {"log": {"loglevel": "warning", "error": XGW_DIR + "/error.log"}, "inbounds": inbounds, "outbounds": outbounds, "routing": {"domainStrategy": "AsIs", "rules": rules}}
    mode = st.get("mode", "auto")

    if on and mode in ("auto", "borrowed"):
        sel = ["b-"] if mode == "borrowed" else ["direct", "b-"]
        bal = {"tag": "gw-bal", "selector": sel, "strategy": {"type": "leastPing"}, **({"fallbackTag": "direct"} if mode == "auto" else {})}
        cfg["routing"]["balancers"] = [bal]
        cfg["observatory"] = {"subjectSelector": sel, "probeUrl": "https://www.gstatic.com/generate_204", "probeInterval": "30s", "enableConcurrency": True}
        rules.append({"type": "field", "inboundTag": tags + ["gw-test"], "balancerTag": "gw-bal"})
    else:
        rules.append({"type": "field", "inboundTag": tags + ["gw-test"], "outboundTag": "direct"})

    return cfg


def public_state(gw: Gateway) -> dict:
    return {"server_id": gw.server_id, "mode": gw.mode, "ports": list(gw.ports or []), "installed": gw.installed, "applied_at": gw.applied_at, "version": gw.version,
            "borrowed": [{"id": b["id"], "note": b.get("note", ""), "enabled": b.get("enabled", True), "info": describe(b["uri"])} for b in (gw.borrowed or [])]}


# ------------------------------------------------------------------ server side
def _gw(db, server_id: int) -> Gateway:
    srv = db.get(Server, server_id)

    if srv is None or srv.role != "iran":
        raise ValueError("choose an Iran server")

    gw = db.get(Gateway, server_id)

    if gw is None:
        gw = Gateway(server_id=server_id)
        db.add(gw)
        db.flush()

    return gw


def get_state(server_id: int) -> dict:
    with session() as db:
        return public_state(_gw(db, server_id))


def set_state(server_id: int, raw: dict) -> dict:
    with session() as db:
        gw = _gw(db, server_id)
        new = validate_state(raw, gw.borrowed)
        gw.mode, gw.ports, gw.borrowed = new["mode"], new["ports"], new["borrowed"]
        return public_state(gw)


def _need_rw():
    if get_settings().read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to manage gateways")


def install(server_id: int) -> dict:
    """Copy the cached Xray binary to the server (it may have no internet) and make it the live one."""
    _need_rw()
    s = get_settings()
    binary = s.data_dir.parent / "engines" / "xray"

    if not binary.exists() or binary.stat().st_size < 5_000_000:
        raise RuntimeError("the panel has no Xray binary cached; run scripts/fetch-xray.sh on the panel server")

    with session() as db:
        gw = _gw(db, server_id)
        node = Node(gw.server.host, gw.server.ssh_user, gw.server.ssh_port)
        name = gw.server.name

    run_action(node, "put_file", {"PATH": "/etc/systemd/system/nikatel-xgw-dir.service", "B64": base64.b64encode(b"[Unit]\nDescription=placeholder\n").decode(), "MODE": "0644"})
    opts = ["-i", str(s.ssh_key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={s.known_hosts}", "-o", "ConnectTimeout=15"]
    # /opt/nikatel-xgw must exist before scp: put_file creates directories only under the allowed roots, so use a tiny unit-free trick: scp into /tmp then install
    r = subprocess.run(["scp", *opts, "-P", str(node.port), str(binary), f"{node.user}@{node.host}:/opt/nikatel-xgw/xray.new"], capture_output=True, text=True, timeout=400, check=False)

    if r.returncode != 0 and "No such file" in r.stderr:
        run_action(node, "xgw_mkdir")
        r = subprocess.run(["scp", *opts, "-P", str(node.port), str(binary), f"{node.user}@{node.host}:/opt/nikatel-xgw/xray.new"], capture_output=True, text=True, timeout=400, check=False)

    if r.returncode != 0:
        raise RuntimeError("could not copy Xray to the server: " + r.stderr.strip()[:140])

    out = run_action(node, "xgw_install_bin", timeout=60)
    ver = (re.search(r"VER (.+)", out) or [None, ""])[1].strip()

    with session() as db:
        gw = _gw(db, server_id)
        gw.installed, gw.version = True, ver[:64]
        emit(db, "info", "gateway", f"Xray gateway installed on {name}: {ver}", name)

    return {"version": ver}


def apply(server_id: int) -> dict:
    _need_rw()

    with session() as db:
        gw = _gw(db, server_id)

        if not gw.installed:
            raise ValueError("install the gateway on this server first")

        node = Node(gw.server.host, gw.server.ssh_user, gw.server.ssh_port)
        name = gw.server.name
        st = {"mode": gw.mode, "ports": list(gw.ports or []), "borrowed": list(gw.borrowed or [])}

    cfg = build_config(st)
    ports = [str(p["port"]) for p in st["ports"] if p.get("enabled", True)]
    out = run_action(node, "xgw_apply", {"B64": base64.b64encode(json.dumps(cfg).encode()).decode(), "PORTS": " ".join(ports)}, timeout=120)

    if out.strip().startswith("REFUSED"):
        raise RuntimeError("Xray refused the config: " + out.strip()[8:300])

    if "ACTIVE" not in out:
        raise RuntimeError("the gateway did not start; the previous config was put back. " + out.strip()[:300])

    with session() as db:
        gw = _gw(db, server_id)
        gw.applied_at = int(time.time())
        emit(db, "ok", "gateway", f"Gateway on {name} applied: {len(ports)} port(s), mode {st['mode']}, {len([b for b in st['borrowed'] if b.get('enabled', True)])} borrowed config(s).", name)

    return {"active": True, "ports": ports}


def status(server_id: int) -> dict:
    with session() as db:
        gw = _gw(db, server_id)
        node = Node(gw.server.host, gw.server.ssh_user, gw.server.ssh_port)
        ports = " ".join(str(p["port"]) for p in (gw.ports or []) if p.get("enabled", True))
        res = {"installed": gw.installed, "applied_at": gw.applied_at, "active": "", "version": "", "listen": {}, "log": []}

    try:
        out = run_script(node, "xgw_status", {"PORTS": ports}, timeout=40)
    except (SSHError, ValueError) as e:
        res["error"] = str(e)
        return res

    log_on = False

    for line in out.splitlines():
        if line.startswith("ACTIVE"):
            res["active"] = line[6:].strip()
        elif line.startswith("VER"):
            res["version"] = line[3:].strip()
        elif line.startswith("LISTEN"):
            x = line.split()
            res["listen"][x[1]] = len(x) > 2 and x[2] not in ("0", "")
        elif line.startswith("LOG"):
            log_on = True
        elif log_on:
            res["log"].append(line)

    return res


def test(server_id: int) -> list[dict]:
    with session() as db:
        gw = _gw(db, server_id)
        node = Node(gw.server.host, gw.server.ssh_user, gw.server.ssh_port)
        on = [b for b in (gw.borrowed or []) if b.get("enabled", True)]

    doors = [("direct", TEST_BASE - 1)] + [(b["id"], TEST_BASE + 1 + i) for i, b in enumerate(on)] + [("gateway", TEST_BASE)]
    out = run_script(node, "xgw_test", {"PORTS": " ".join(str(p) for _, p in doors)}, timeout=120)
    names = {b["id"]: (b.get("note") or b["id"]) for b in on}
    by_port = {p: k for k, p in doors}
    res = []

    for line in out.splitlines():
        m = re.match(r"^T (\d+) (\d{3})?\s*([\d.]*)\s*\|\s*(.*)$", line)

        if not m:
            continue

        port, code, secs, rest = m.groups()
        key = by_port.get(int(port), port)
        res.append({"key": key, "name": names.get(key, key), "ok": code == "204", "ms": int(float(secs) * 1000) if secs else None,
                    "ip": (re.search(r"ip=(\S+)", rest) or [None, ""])[1], "country": (re.search(r"loc=(\S+)", rest) or [None, ""])[1]})

    return res


def remove(server_id: int) -> None:
    _need_rw()

    with session() as db:
        gw = _gw(db, server_id)
        node = Node(gw.server.host, gw.server.ssh_user, gw.server.ssh_port)
        name = gw.server.name

    run_action(node, "xgw_remove", timeout=60)

    with session() as db:
        gw = _gw(db, server_id)
        gw.installed = False
        emit(db, "warn", "gateway", f"Xray gateway removed from {name}.", name)
