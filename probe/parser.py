#!/usr/bin/env python3
"""Turns a share link into an Xray outbound.

Links: vless://  vmess://  trojan://  ss://
Transports: tcp (raw, with or without an http header), ws, grpc, httpupgrade, xhttp (splithttp), h2, kcp
Security: none, tls (sni, alpn, fingerprint), reality (public key, short id, spider x), vless flow
"""

import base64
import json
import urllib.parse


def _b64decode(value):
    value = str(value).strip()
    value += "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value).decode("utf-8", "replace")


_NETWORK_ALIASES = {
    "raw": "tcp",
    "splithttp": "xhttp",
    "h2": "http",
    "mkcp": "kcp",
}


def _csv(value):
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def _stream(network, security, g):
    """g(name, default) reads one parameter of the link."""
    network = _NETWORK_ALIASES.get(str(network or "tcp").lower(), str(network or "tcp").lower())
    security = str(security or "none").lower()

    stream = {"network": network, "security": security}

    path = urllib.parse.unquote(str(g("path", "") or ""))
    host = str(g("host", "") or "")

    if network == "tcp":
        if str(g("headerType", "none")).lower() == "http":
            stream["tcpSettings"] = {
                "header": {
                    "type": "http",
                    "request": {
                        "path": _csv(path) or ["/"],
                        "headers": {"Host": _csv(host)} if host else {}
                    }
                }
            }

    elif network == "ws":
        ws = {"path": path or "/"}

        if host:
            ws["headers"] = {"Host": host}

        stream["wsSettings"] = ws

    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": urllib.parse.unquote(str(g("serviceName", g("path", "")) or "")),
            "multiMode": str(g("mode", "")) == "multi"
        }

        if g("authority", ""):
            stream["grpcSettings"]["authority"] = g("authority", "")

    elif network == "httpupgrade":
        hu = {"path": path or "/"}

        if host:
            hu["host"] = host

        stream["httpupgradeSettings"] = hu

    elif network == "xhttp":
        xh = {"path": path or "/", "mode": str(g("mode", "auto") or "auto")}

        if host:
            xh["host"] = host

        extra = g("extra", "")

        if extra:
            try:
                xh["extra"] = json.loads(urllib.parse.unquote(str(extra)))
            except Exception:
                pass

        stream["xhttpSettings"] = xh

    elif network == "http":
        stream["httpSettings"] = {"path": path or "/", "host": _csv(host)}

    elif network == "kcp":
        stream["kcpSettings"] = {
            "header": {"type": str(g("headerType", "none") or "none")},
            "seed": str(g("seed", "") or "")
        }

    if security == "tls":
        tls = {
            "serverName": str(g("sni", "") or host or g("peer", "") or ""),
            "allowInsecure": str(g("allowInsecure", g("insecure", "0"))).lower() in ("1", "true")
        }

        alpn = _csv(urllib.parse.unquote(str(g("alpn", "") or "")))

        if alpn:
            tls["alpn"] = alpn

        if g("fp", ""):
            tls["fingerprint"] = g("fp", "")

        stream["tlsSettings"] = tls

    elif security == "reality":
        stream["realitySettings"] = {
            "serverName": str(g("sni", "") or ""),
            "fingerprint": str(g("fp", "") or "chrome"),
            "publicKey": str(g("pbk", "") or ""),
            "shortId": str(g("sid", "") or ""),
            "spiderX": urllib.parse.unquote(str(g("spx", "") or ""))
        }

    return stream


def _query_getter(u):
    q = urllib.parse.parse_qs(u.query, keep_blank_values=True)

    def get(name, default=""):
        v = q.get(name)
        return v[0] if v else default

    return get


def parse_vless(uri):
    u = urllib.parse.urlparse(uri)
    get = _query_getter(u)

    user = {"id": urllib.parse.unquote(u.username or ""), "encryption": get("encryption", "none") or "none"}

    if get("flow", ""):
        user["flow"] = get("flow", "")

    return {
        "protocol": "vless",
        "settings": {"vnext": [{"address": u.hostname, "port": u.port, "users": [user]}]},
        "streamSettings": _stream(get("type", "tcp"), get("security", "none"), get)
    }


def parse_vmess(uri):
    data = json.loads(_b64decode(uri[len("vmess://"):]))

    net = data.get("net", "tcp")
    tls = str(data.get("tls", "") or "").lower()

    def get(name, default=""):
        # vmess json uses a few different key names
        key = {"type": "net", "headerType": "type", "serviceName": "path"}.get(name, name)

        if name == "security":
            return "tls" if tls == "tls" else "none"

        v = data.get(key, default)
        return default if v is None else v

    return {
        "protocol": "vmess",
        "settings": {"vnext": [{
            "address": data["add"],
            "port": int(data["port"]),
            "users": [{"id": data["id"], "alterId": int(data.get("aid", 0) or 0), "security": data.get("scy", "auto") or "auto"}]
        }]},
        "streamSettings": _stream(net, "tls" if tls == "tls" else "none", get)
    }


def parse_trojan(uri):
    u = urllib.parse.urlparse(uri)
    get = _query_getter(u)

    return {
        "protocol": "trojan",
        "settings": {"servers": [{"address": u.hostname, "port": u.port, "password": urllib.parse.unquote(u.username or "")}]},
        "streamSettings": _stream(get("type", "tcp"), get("security", "tls"), get)
    }


def parse_ss(uri):
    body = uri[len("ss://"):].split("#", 1)[0]
    query = ""

    if "?" in body:
        body, query = body.split("?", 1)

    if "@" in body:
        userinfo, hostport = body.rsplit("@", 1)

        try:
            userinfo = _b64decode(userinfo)
        except Exception:
            userinfo = urllib.parse.unquote(userinfo)
    else:
        decoded = _b64decode(body)
        userinfo, hostport = decoded.rsplit("@", 1)

    method, _, password = userinfo.partition(":")
    host, _, port = hostport.rpartition(":")

    return {
        "protocol": "shadowsocks",
        "settings": {"servers": [{"address": host.strip("[]"), "port": int(port), "method": method, "password": password}]},
        "streamSettings": {"network": "tcp", "security": "none"}
    }


def parse_uri(uri):
    uri = uri.strip()

    if uri.startswith("vless://"):
        return parse_vless(uri)

    if uri.startswith("vmess://"):
        return parse_vmess(uri)

    if uri.startswith("trojan://"):
        return parse_trojan(uri)

    if uri.startswith("ss://"):
        return parse_ss(uri)

    raise ValueError("Supported links: vless, vmess, trojan, ss")


def make_xray_config(uri, port):
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": int(port),
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": True}
        }],
        "outbounds": [parse_uri(uri)]
    }
