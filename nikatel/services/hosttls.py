"""TLS copies of the plain WebSocket hosts of a Marzban / PasarGuard panel (roadmap #93).

A user's link is built from a HOST of the panel: an address, a port, a security, an SNI. The plain WebSocket inbounds are reached through a CDN name on port 80.
When the CDN has HTTPS for the domain in the Host header, the same link can use TLS on port 443 with nothing changed on the nodes or the tunnels: only the host
changes. So the panel never edits a host in place: it makes a COPY of it (port 443, security tls, SNI = the Host header, alpn, fingerprint), tests it, and
the copy is on only when the test passes (the originals are untouched; switching them off later is the operator's call, and the copies can be removed again).

The test is a real one, made from the panel server: a TCP connect to the address, a TLS handshake with the SNI (the certificate must be valid for it), then a
WebSocket upgrade on the inbound's path - all the way through the CDN, the Iran server and the tunnel to the node. It says where it stopped.
It does NOT say what the Iranian filter does with the SNI: give a copy to a few users first."""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..config import get_settings
from . import panels

SUFFIX = " TLS"
WORKERS = 6
_DOMAIN = re.compile(r"^(?=.{4,253}$)([A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$")
_PLAIN_WS_NETS = ("ws", "websocket", "httpupgrade")


# ------------------------------------------------------------------ pure
def is_domain(s) -> bool:
    return bool(_DOMAIN.match(str(s or "").strip()))


def one(v) -> str:
    """A host field that may be a list (PasarGuard) or text (Marzban), as one text."""
    if isinstance(v, list):
        return str(v[0]) if v else ""

    return str(v or "").strip()


def inbound_info(core: dict) -> dict[str, dict]:
    """tag -> {network, security, path, host} from the core config of a panel."""
    out = {}

    for ib in (core or {}).get("inbounds", []) or []:
        ss = ib.get("streamSettings") or {}
        net = str(ss.get("network") or "").lower()
        ws = ss.get("wsSettings") or ss.get("httpupgradeSettings") or {}
        out[str(ib.get("tag"))] = {"network": net, "security": str(ss.get("security") or "none").lower(), "path": str(ws.get("path") or ""), "host": str(ws.get("host") or ""), "port": ib.get("port")}

    return out


def candidate(h: dict, info: dict | None) -> str:
    """'' when this host can have a TLS copy, else the sentence that says why not."""
    if h.get("security") == "tls":
        return "it already uses TLS"

    if re.search(r"\{[A-Z_]+\}", one(h.get("remark"))):
        return "it is a template host (the account details a client shows), not a config"

    if info is None:
        return "the inbound of this host is not in the core config"

    if info["network"] not in _PLAIN_WS_NETS:
        return f"its inbound is {info['network'] or 'not a WebSocket'}: only WebSocket inbounds are copied"

    if info["security"] not in ("none", ""):
        return f"its inbound already has {info['security']} on the node"

    return ""


def sni_for(h: dict) -> str:
    """The name the TLS copy asks the CDN for: the Host header of the link (the proxied name), else its own SNI, else the address when that is a name."""
    for v in (one(h.get("host")), one(h.get("sni")), one(h.get("address"))):
        if is_domain(v):
            return v

    return ""


def twin_of(h: dict, port: int = 443, alpn: str = "http/1.1", fingerprint: str = "chrome", suffix: str = SUFFIX) -> dict:
    """Pure: the TLS copy of a plain host (the original is not touched). The Host header stays what it was, so the CDN routes the same way."""
    cp = json.loads(json.dumps(h))
    sni = sni_for(h)
    cp.pop("id", None)
    cp["remark"] = (str(h.get("remark") or "host") + suffix)[:80]
    cp["port"] = int(port)
    cp["security"] = "tls"
    cp["sni"] = [sni] if isinstance(h.get("sni"), list) else sni

    if not one(h.get("host")):
        cp["host"] = [sni] if isinstance(h.get("host"), list) else sni

    cp["alpn"] = [a.strip() for a in alpn.split(",") if a.strip()] if isinstance(h.get("address"), list) else alpn         # (PasarGuard wants a list of names, Marzban one text)
    cp["fingerprint"] = fingerprint
    cp["allowinsecure"] = False
    cp["is_disabled"] = False
    return cp


def same_twin(a: dict, b: dict) -> bool:
    """A host that is already the TLS copy of another (same inbound, address, port, SNI and Host header)."""
    keys = ("address", "host", "sni")
    return (a.get("inbound_tag") or a.get("tag")) == (b.get("inbound_tag") or b.get("tag")) and int(a.get("port") or 0) == int(b.get("port") or 0) and a.get("security") == b.get("security") == "tls" and all(one(a.get(k)) == one(b.get(k)) for k in keys)


def ws_status(data: bytes) -> int:
    """Pure: the status code of an HTTP answer ('HTTP/1.1 101 Switching Protocols ...'), 0 when it is not one."""
    m = re.match(rb"HTTP/\d(?:\.\d)? (\d{3})", data or b"")
    return int(m.group(1)) if m else 0


def cert_text(cert: dict | None) -> tuple[str, str, int | None]:
    """Pure: (names the certificate is valid for, issuer, days left) from ssl's getpeercert()."""
    if not cert:
        return "", "", None

    names = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]

    if not names:
        names = [v for rdn in cert.get("subject", ()) for k, v in rdn if k == "commonName"]

    issuer = next((v for rdn in cert.get("issuer", ()) for k, v in rdn if k in ("organizationName", "commonName")), "")
    days = None

    try:
        end = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = int((end - datetime.now(timezone.utc)).total_seconds() // 86400)
    except (KeyError, ValueError):
        pass

    return ", ".join(names[:3]) + ("…" if len(names) > 3 else ""), issuer, days


# ------------------------------------------------------------------ the test (a real connection from the panel server)
def _upgrade(sock, host: str, path: str) -> int:
    key = base64.b64encode(os.urandom(16)).decode()
    req = f"GET {path or '/'} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    sock.sendall(req.encode())
    data = b""

    while b"\r\n\r\n" not in data and len(data) < 4096:
        chunk = sock.recv(1024)

        if not chunk:
            break

        data += chunk

    return ws_status(data)


def check_one(address: str, port: int, sni: str, host: str, path: str, timeout: float = 8.0) -> dict:
    """Connect to address:port, TLS with `sni` (the certificate must be valid for it), then a WebSocket upgrade with the Host header `host` on `path`.
    Returns {ok, stage (dns | tcp | tls | cert | ws | ok), detail, cert, issuer, days, ms}; `stage` is where it stopped."""
    t0 = time.time()
    out = {"ok": False, "stage": "dns", "detail": "", "cert": "", "issuer": "", "days": None, "ms": 0, "ws": 0}

    def done(stage: str, detail: str, ok: bool = False) -> dict:
        out.update(stage=stage, detail=detail, ok=ok, ms=int((time.time() - t0) * 1000))
        return out

    try:
        ip = socket.getaddrinfo(address, port, type=socket.SOCK_STREAM)[0][4][0]
    except OSError as e:
        return done("dns", f"{address} does not resolve ({e})"[:160])

    for verify in (True, False):
        try:
            raw = socket.create_connection((ip, port), timeout=timeout)
        except OSError as e:
            return done("tcp", f"{ip}:{port} does not answer ({e})"[:160])

        ctx = ssl.create_default_context()
        ctx.set_alpn_protocols(["http/1.1"])

        if not verify:
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE

        try:
            s = ctx.wrap_socket(raw, server_hostname=sni)
        except ssl.SSLCertVerificationError as e:
            raw.close()
            out["detail"] = f"the certificate is not valid for {sni}: {e.verify_message}"[:160]
            continue                                                       # look once more without the check, to say what the CDN does show
        except (ssl.SSLError, OSError) as e:
            raw.close()
            return done("tls", f"the TLS handshake failed ({e})"[:160])

        try:
            s.settimeout(timeout)
            out["cert"], out["issuer"], out["days"] = cert_text(s.getpeercert()) if verify else ("", "", None)
            out["ws"] = _upgrade(s, host or sni, path)
        except OSError as e:
            return done("ws", f"the WebSocket request failed ({e})"[:160])
        finally:
            s.close()

        if verify:
            if out["ws"] == 101:
                return done("ok", f"TLS valid for {sni}" + (f", {out['days']} days left" if out["days"] is not None else "") + ", WebSocket answered 101", True)

            return done("ws", f"TLS is fine but the WebSocket upgrade got HTTP {out['ws'] or 'no answer'}" + (" (the CDN could not reach the node)" if out["ws"] in (502, 503, 504, 522, 523, 524) else ""))

        return done("cert", out["detail"] + (f"; the WebSocket answered HTTP {out['ws']} without the check" if out["ws"] else ""))

    return done("cert", out["detail"] or "the certificate is not valid")


def check_many(items: list[dict]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        return list(ex.map(lambda i: check_one(i["address"], i["port"], i["sni"], i["host"], i["path"]), items))


# ------------------------------------------------------------------ the plan, the copies, the removal
def _read(c: dict) -> tuple[object, bool, list[dict]]:
    """(raw hosts, is it Marzban's dict, a flat list of {key, tag, h})."""
    raw = panels.call(c, "GET", "/api/hosts")
    flat = []

    if isinstance(raw, dict):
        for tag, lst in raw.items():
            for i, h in enumerate(lst if isinstance(lst, list) else []):
                flat.append({"key": f"{tag}#{i}", "tag": tag, "h": h})

        return raw, True, flat

    for h in raw or []:
        if isinstance(h, dict):
            flat.append({"key": str(h.get("id")), "tag": h.get("inbound_tag") or "", "h": h})

    return raw, False, flat


def _core(c: dict) -> dict:
    """The core config the inbounds are read from: Marzban has one (`/api/core/config`); PasarGuard has cores of its own (`/api/cores`, each with its config), whose inbounds are put together.
    (Without this every PasarGuard host was skipped as "the inbound is not in the core config": found on a live 5.4.1 panel.)"""
    try:
        if c.get("type") == "pasarguard":
            r = panels.call(c, "GET", "/api/cores")
            cores = r.get("cores") if isinstance(r, dict) else r
            return {"inbounds": [ib for k in (cores if isinstance(cores, list) else []) if isinstance(k, dict) and isinstance(k.get("config"), dict) for ib in (k["config"].get("inbounds") or []) if isinstance(ib, dict)]}

        return panels.call(c, "GET", "/api/core/config") or {}
    except (panels.PanelError, ValueError):
        return {}


def plan(c: dict, keys: list[str], port: int = 443, alpn: str = "http/1.1", fingerprint: str = "chrome", suffix: str = SUFFIX, test: bool = True) -> list[dict]:
    """For every ticked host: what its TLS copy would be, whether it can be made (and why not), whether a copy exists already, and the result of the real test."""
    if not 1 <= int(port) <= 65535:
        raise ValueError("the port must be 1-65535")

    _raw, _marz, flat = _read(c)
    info = inbound_info(_core(c))
    by_key = {f["key"]: f for f in flat}
    items, tests = [], []

    for k in list(dict.fromkeys(keys))[:200]:
        f = by_key.get(k)

        if f is None:
            items.append({"key": k, "status": "skip", "reason": "this host is not in the panel (any more)", "remark": ""})
            continue

        h, tag = f["h"], f["tag"]
        inf = info.get(tag)
        why = candidate(h, inf)
        it = {"key": k, "tag": tag, "remark": one(h.get("remark")), "address": one(h.get("address")), "port": h.get("port"), "status": "new", "reason": ""}

        if why:
            it.update(status="skip", reason=why)
        elif not sni_for(h):
            it.update(status="skip", reason="no domain for the SNI: the Host header and the address are not names")
        else:
            tw = twin_of(h, port, alpn, fingerprint, suffix)
            tw["inbound_tag"] = h.get("inbound_tag") or tag
            ex = next((g for g in flat if g["tag"] == tag and same_twin({**g["h"], "tag": tag}, {**tw, "tag": tag})), None)
            it["twin"] = {k2: (one(tw.get(k2)) if k2 in ("remark", "address", "sni", "host", "alpn", "fingerprint") else tw.get(k2)) for k2 in ("remark", "address", "port", "security", "sni", "host", "alpn", "fingerprint")}
            it["path"] = one(h.get("path")) or (inf or {}).get("path") or "/"

            if ex is not None:
                it.update(status="exists", reason="a TLS copy of it exists already", twin_key=ex["key"])
            elif test:
                tests.append(it)

        items.append(it)

    if tests:
        res = check_many([{"address": t["twin"]["address"], "port": t["twin"]["port"], "sni": t["twin"]["sni"], "host": t["twin"]["host"] or t["twin"]["sni"], "path": t["path"]} for t in tests])

        for t, r in zip(tests, res):
            t["check"] = r

    return items


def _backup(c: dict, raw) -> str:
    d = get_settings().data_dir / "host-backups"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"panel{c.get('id', 0)}-{int(time.time())}.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    for old in sorted(d.glob("panel*.json"), key=lambda x: x.stat().st_mtime)[:-20]:
        old.unlink()

    return p.name


def create(c: dict, keys: list[str], port: int = 443, alpn: str = "http/1.1", fingerprint: str = "chrome", suffix: str = SUFFIX, failed: str = "disabled") -> dict:
    """Make the copies: one that passed the real test is ON, one that did not is made OFF (failed = 'disabled') or not made at all (failed = 'skip'). The hosts of the panel are saved
    to a file first. The originals are never touched."""
    if failed not in ("disabled", "skip"):
        raise ValueError("failed must be disabled or skip")

    items = plan(c, keys, port, alpn, fingerprint, suffix, test=True)
    raw, marz, flat = _read(c)
    by_key = {f["key"]: f for f in flat}
    made = []

    for it in items:
        if it["status"] != "new":
            continue

        ok = bool((it.get("check") or {}).get("ok"))

        if not ok and failed == "skip":
            it.update(status="not_made", reason="the test failed: " + str((it.get("check") or {}).get("detail", ""))[:120])
            continue

        src = by_key[it["key"]]
        tw = twin_of(src["h"], port, alpn, fingerprint, suffix)
        tw["is_disabled"] = not ok
        made.append((src["tag"], tw))
        it.update(status="made_on" if ok else "made_off", reason="" if ok else "the test failed, so it is OFF: " + str((it.get("check") or {}).get("detail", ""))[:120])

    backup = ""

    if made:
        backup = _backup(c, raw)

        if marz:
            for tag, tw in made:
                raw[tag].append(tw)

            panels.call(c, "PUT", "/api/hosts", body=raw)
        else:
            for _tag, tw in made:
                tw["inbound_tag"] = tw.get("inbound_tag") or _tag
                panels.call(c, "POST", "/api/host/", body=panels.host_input(tw))        # (with the slash: without it the panel answers 307 and makes nothing)

    return {"items": items, "made": len(made), "on": sum(1 for i in items if i["status"] == "made_on"), "off": sum(1 for i in items if i["status"] == "made_off"), "backup": backup}


def remove(c: dict, suffix: str = SUFFIX) -> dict:
    """Remove the copies this tool made: the hosts whose remark ends with the suffix, whose security is tls and whose plain original (the same remark without the suffix, same inbound) is still there. The hosts are saved to a file first."""
    raw, marz, flat = _read(c)
    plain = {(f["tag"], str(one(f["h"].get("remark")))) for f in flat if f["h"].get("security") != "tls"}
    named = [f for f in flat if str(one(f["h"].get("remark"))).endswith(suffix) and f["h"].get("security") == "tls"]
    gone = [f for f in named if (f["tag"], str(one(f["h"].get("remark")))[: -len(suffix)]) in plain]          # a copy has its plain original beside it ("The original hosts stay"); a host the owner made by hand that happens to end with "TLS" has none and is never removed
    kept = len(named) - len(gone)

    if not gone:
        return {"removed": 0, "backup": "", **({"kept": kept} if kept else {})}

    backup = _backup(c, raw)

    if marz:
        ids = {id(f["h"]) for f in gone}

        for tag in list(raw):
            raw[tag] = [h for h in raw[tag] if id(h) not in ids]

        panels.call(c, "PUT", "/api/hosts", body=raw)
    else:
        for f in gone:
            panels.call(c, "DELETE", "/api/host/" + str(f["h"].get("id")))

    return {"removed": len(gone), "backup": backup, **({"kept": kept} if kept else {})}