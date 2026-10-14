"""PasarGuard: the HOSTS of a PasarGuard 5.4 panel - every field of a host (the owner asked for every section of the panel, 2026-10-14): where it points, how it is secured, which users
get it, and the advanced settings (transport, mux, fragment, noise, ECH, final mask, WireGuard overrides, the subscription template, extra headers); make, change, clone, delete,
enable / disable many, and the backups that make a change or a delete undoable. Written against the REST API read on a live 5.4.1 panel (app/models/host.py).

A HOST is one line of a user's subscription: an address and a port the client connects to, with the SNI, Host header, path, ALPN and fingerprint it presents. What the panel does that
matters here, and so what this module does:
  * a host is READ with some names (the xray mux says xudpConcurrency / xudpProxyUDP443) and WRITTEN with others (xudp_concurrency / xudp_proxy_udp_443): a host put back as it was read
    silently resets both. Everything here shows and sends the WRITE names (`panels.host_input`);
  * the panel DROPS a key of the advanced settings that it does not know, without a word: `{"transport_settings": {"fly": 1}}` came back as an empty transport. The keys are checked
    here and a wrong one is refused, so a typo does not turn a setting into nothing;
  * a host is made with POST /api/host/ - WITH the slash: without it the panel only answers 307 and makes nothing;
  * `inbound_tag` must be a tag of a configured core (the panel answers 400 "<tag> not found"); `address`, `sni`, `host` and `verify_peer_cert_by_name` are lists of names; `priority` is
    required; `security` is inbound_default, none or tls; `alpn` is a list of http/1.1, h2, h3;
  * the bulk routes take host ids (`/api/hosts/bulk/{enable|disable|delete}`).
A change sends the whole host (the panel replaces it), with only the fields that differ changed, after the host is saved to a file in the data folder; `host_restore` puts a host back,
or makes it again when it was deleted. The files hold names and settings, no password."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlencode

from .panels import PanelError, host_input
from .pg_admin import _backup_dir, _call, _dict, _list, _number, _write_backup, audit

MAX_BULK = 200
KEEP_LISTED = 40
MAX_JSON = 65536
SECURITIES = ("inbound_default", "none", "tls")
ALPNS = ("http/1.1", "h2", "h3")
FINGERPRINTS = ("", "chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq", "random", "randomized", "randomizednoalpn", "unsafe")
STATUSES = ("active", "disabled", "limited", "expired", "on_hold")
LISTS = ("address", "sni", "host", "verify_peer_cert_by_name")
BOOLS = ("is_disabled", "random_user_agent", "use_sni_as_host")
# the advanced settings and the keys the panel knows in each (an unknown key is dropped by the panel without an error); http_headers is free
JSON_KEYS: dict[str, set[str] | None] = {
    "http_headers": None,
    "transport_settings": {"xhttp_settings", "grpc_settings", "kcp_settings", "tcp_settings", "websocket_settings"},
    "mux_settings": {"xray", "sing_box", "clash"},
    "fragment_settings": {"xray", "sing_box"},
    "noise_settings": {"xray"},
    "ech": {"xray", "mihomo", "sing_box"},
    "wireguard_overrides": {"allowed_ips", "mtu", "reserved", "keepalive_seconds", "dns"},
    "subscription_templates": {"xray"},
    "final_mask_settings": {"tcp", "udp", "quicParams"},
}
_BK = re.compile(r"panel(\d+)-host-(\d+)-(\d{8}-\d{6})(?:-(\d+))?\.json")
_NAME = re.compile(r"^\S{1,255}$")


# ------------------------------------------------------------------ pure: a host as the page sees it, and as the panel wants it
def _prune(v):
    """The value without the empty leaves: the panel answers an unset setting as an object full of nulls."""
    if isinstance(v, dict):
        out = {k: _prune(x) for k, x in v.items()}
        return {k: x for k, x in out.items() if x not in (None, "", [], {})}

    if isinstance(v, list):
        return [_prune(x) for x in v]

    return v


def _strs(v) -> list[str]:
    if v is None:
        return []

    if isinstance(v, str):
        v = re.split(r"[,\n]", v)

    return [str(x).strip() for x in _list(v) if str(x).strip()]


def host_row(h) -> dict:
    """One host for the page: the plain fields as they are, the advanced settings as text (pretty JSON without the empty leaves, '' when nothing is set), all with the names the panel writes."""
    w = host_input(_dict(h))
    adv = {k: _prune(w.get(k)) for k in JSON_KEYS}
    ai = w.get("allowinsecure")
    return {"id": w.get("id"), "remark": str(w.get("remark") or ""), "address": _strs(w.get("address")), "inbound_tag": str(w.get("inbound_tag") or ""), "port": w.get("port") if isinstance(w.get("port"), int) else None,
            "sni": _strs(w.get("sni")), "host": _strs(w.get("host")), "path": str(w.get("path") or ""), "security": str(w.get("security") or "inbound_default"), "alpn": _strs(w.get("alpn")),
            "fingerprint": str(w.get("fingerprint") or ""), "allowinsecure": ai if isinstance(ai, bool) else None, "is_disabled": bool(w.get("is_disabled")), "random_user_agent": bool(w.get("random_user_agent")),
            "use_sni_as_host": bool(w.get("use_sni_as_host")), "vless_route": str(w.get("vless_route") or ""), "priority": w.get("priority") if isinstance(w.get("priority"), int) else 0,
            "status": [s for s in _list(w.get("status")) if s in STATUSES] or None, "pinned_peer_cert_sha256": str(w.get("pinned_peer_cert_sha256") or ""),
            "verify_peer_cert_by_name": _strs(w.get("verify_peer_cert_by_name")), "cipher_suites": str(w.get("cipher_suites") or ""),
            "json": {k: (json.dumps(v, indent=2, ensure_ascii=False) if v else "") for k, v in adv.items()}}


def _list_of_names(v, label: str) -> list[str]:
    out: list[str] = []

    for n in _strs(v):
        if not _NAME.match(n):
            raise ValueError(f"{label}: “{n[:40]}” is not a name (no spaces, at most 255 characters)")

        if n not in out:
            out.append(n)

    if len(out) > 100:
        raise ValueError(f"{label}: at most 100 entries")

    return out


def _json_field(k: str, v):
    """The advanced setting `k` from text or an object: None for nothing (the panel's default), else an object whose keys the panel knows."""
    if isinstance(v, str):
        if not v.strip():
            return None

        if len(v) > MAX_JSON:
            raise ValueError(f"{k}: the text is too long")

        try:
            v = json.loads(v)
        except ValueError as e:
            raise ValueError(f"{k}: this is not valid JSON ({str(e)[:80]})") from None

    if v is None or v == {}:
        return None

    if not isinstance(v, dict):
        raise ValueError(f"{k}: a JSON object is expected, like {{ … }}")  # noqa: TRY004 - the caller shows the ValueError text to the operator

    known = JSON_KEYS[k]

    if known is not None:
        bad = sorted(set(v) - known)

        if bad:
            raise ValueError(f"{k}: the panel does not know {', '.join(bad)} here and would drop it without a word; the keys are {', '.join(sorted(known))}")

    if len(json.dumps(v)) > MAX_JSON:
        raise ValueError(f"{k}: too long")

    return host_input({"mux_settings": v})["mux_settings"] if k == "mux_settings" else v


def apply(data: dict, cur: dict | None, next_priority: int = 1) -> tuple[dict, list[str]]:
    """(the body to send, the names of the fields that differ from `cur`). `cur` is the host as the panel gave it (None = a new host); a field that is not in `data` is kept as it is.
    The body is the whole host in the names the panel writes: the panel replaces a host as a whole."""
    data = _dict(data)
    body = host_input(cur) if cur is not None else {"remark": "", "address": [], "security": "inbound_default", "fingerprint": "", "is_disabled": False, "random_user_agent": False,
                                                       "use_sni_as_host": False, "priority": next_priority}
    body.pop("id", None)
    was = host_row(cur) if cur is not None else host_row({})
    changed: list[str] = []

    def put(k, v, same):
        if cur is None or not same:
            body[k] = v
            changed.append(k)

    if "remark" in data:
        r = str(data["remark"] or "").strip()

        if not 1 <= len(r) <= 256:
            raise ValueError("a host needs a remark (its name) of 1 to 256 characters")

        put("remark", r, r == was["remark"])

    for k in LISTS:
        if k in data:
            v = _list_of_names(data[k], k)

            if not v and (cur or {}).get(k) is None:
                continue                                                                            # nothing there, nothing typed: it stays as it is (null)

            put(k, v, v == was[k])

    if "port" in data:
        p = data["port"]
        n = None if p in (None, "", 0) else int(_number(p, "the port", 1, 65535))
        put("port", n, n == was["port"])

    if "inbound_tag" in data:
        t = str(data["inbound_tag"] or "").strip()

        if len(t) > 128:
            raise ValueError("the inbound tag is at most 128 characters")

        put("inbound_tag", t or None, t == was["inbound_tag"])

    for k, limit in (("path", 1024), ("vless_route", 128), ("cipher_suites", 1024), ("pinned_peer_cert_sha256", 256)):
        if k in data:
            t = str(data[k] or "").strip()

            if len(t) > limit:
                raise ValueError(f"{k}: at most {limit} characters")

            put(k, t if (t or k in ("path",)) else None, t == was[k])

    if "security" in data:
        if data["security"] not in SECURITIES:
            raise ValueError(f"security is one of: {', '.join(SECURITIES)}")

        put("security", data["security"], data["security"] == was["security"])

    if "fingerprint" in data:
        if (data["fingerprint"] or "") not in FINGERPRINTS:
            raise ValueError("this fingerprint is not one the panel knows")

        put("fingerprint", data["fingerprint"] or "", (data["fingerprint"] or "") == was["fingerprint"])

    if "alpn" in data:
        v = [a for a in dict.fromkeys(_strs(data["alpn"]))]

        if any(a not in ALPNS for a in v):
            raise ValueError(f"alpn is a list of: {', '.join(ALPNS)}")

        if v or (cur or {}).get("alpn") is not None:
            put("alpn", v, v == was["alpn"])

    if "allowinsecure" in data:
        a = data["allowinsecure"]
        a = None if a in (None, "") else (a if isinstance(a, bool) else {"true": True, "false": False}.get(str(a).lower(), "?"))

        if a == "?":
            raise ValueError("allowinsecure is yes, no or the default")

        put("allowinsecure", a, a == was["allowinsecure"])

    for k in BOOLS:
        if k in data:
            put(k, bool(data[k]), bool(data[k]) == was[k])

    if "priority" in data:
        n = int(_number(data["priority"], "the priority", 0, 1_000_000))
        put("priority", n, n == was["priority"])

    if "status" in data:
        s = [x for x in dict.fromkeys(_strs(data["status"]))]

        if any(x not in STATUSES for x in s):
            raise ValueError(f"the user statuses are: {', '.join(STATUSES)}")

        put("status", s or None, sorted(s) == sorted(was["status"] or []))                         # (the order of the statuses means nothing)

    for k in JSON_KEYS:
        if k in data:
            v = _json_field(k, data[k])
            put(k, v, (_prune(v) or None) == (_prune(host_input(cur or {}).get(k)) or None))              # (the panel answers an unset header list as {} and the page shows "": the same thing)

    if cur is None and not str(body.get("remark") or "").strip():
        raise ValueError("a host needs a remark (its name)")

    return body, changed


# ------------------------------------------------------------------ reading
def _get_raw(c: dict, hid: int) -> dict:
    h = _dict(_call(c, "GET", f"/api/host/{int(hid)}"))

    if h.get("id") is None:
        raise ValueError("this host does not exist (any more)")

    return h


def _all_raw(c: dict) -> list[dict]:
    return [h for h in _list(_call(c, "GET", "/api/hosts")) if isinstance(h, dict)]


def options(c: dict) -> dict:
    """What the form of a host chooses from: the inbounds (tag, protocol, network), the xray subscription templates, the next free priority and the values the panel allows."""
    try:
        ib = [{"tag": str(x.get("tag") or ""), "protocol": str(x.get("protocol") or ""), "network": str(x.get("network") or "")} for x in _list(_call(c, "GET", "/api/inbounds/details")) if isinstance(x, dict)]
    except PanelError:
        ib = [{"tag": str(t), "protocol": "", "network": ""} for t in _list(_call(c, "GET", "/api/inbounds")) if isinstance(t, str)]

    try:
        tpl = [{"id": t.get("id"), "name": str(t.get("name") or "")} for t in _list(_dict(_call(c, "GET", "/api/client_templates/simple?all=true")).get("templates"))
               if isinstance(t, dict) and t.get("template_type") == "xray_subscription"]
    except PanelError:
        tpl = []

    top = max([h.get("priority") for h in _all_raw(c) if isinstance(h.get("priority"), int)], default=0)
    return {"inbounds": ib, "templates": tpl, "next_priority": top + 1, "securities": list(SECURITIES), "alpns": list(ALPNS), "fingerprints": list(FINGERPRINTS), "statuses": list(STATUSES),
            "json_keys": {k: sorted(v) if v else [] for k, v in JSON_KEYS.items()}}


def host_view(c: dict, hid: int) -> dict:
    return {"host": host_row(_get_raw(c, hid)), "options": options(c)}


def new_view(c: dict) -> dict:
    """The empty form of a new host (the next priority already in it) with its options."""
    o = options(c)
    return {"host": {**host_row({}), "priority": o["next_priority"], "address": [], "remark": ""}, "options": o}


# ------------------------------------------------------------------ backups
def _backup(c: dict, raw: dict, why: str) -> str:
    return _write_backup(f"panel{int(c['id'])}-host-{int(raw['id'])}", {"panel": int(c["id"]), "host": raw, "why": why, "at": int(time.time())})


def host_backups(c: dict) -> list[dict]:
    """The files kept before a change or a delete of a host of this panel, newest first: {file, host_id, remark, why, at}."""
    found = []

    for p in _backup_dir().iterdir():
        m = _BK.fullmatch(p.name)

        if not m or int(m.group(1)) != int(c["id"]):
            continue

        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            found.append((m.group(3), int(m.group(4) or 1), {"file": p.name, "host_id": int(m.group(2)), "remark": str(_dict(doc.get("host")).get("remark") or ""), "why": str(doc.get("why") or ""), "at": doc.get("at")}))
        except (OSError, ValueError, AttributeError):
            continue

    return [x[2] for x in sorted(found, key=lambda x: (x[0], x[1]), reverse=True)[:KEEP_LISTED]]


def host_restore(c: dict, name: str) -> dict:
    """Put a host back from a file kept here: written over the host that has its id now (that one is saved first), or made again (a new id) when it was deleted."""
    m = _BK.fullmatch(str(name))

    if not m or int(m.group(1)) != int(c["id"]):
        raise ValueError("unknown backup")

    try:
        doc = json.loads((_backup_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("unknown backup") from None

    raw = _dict(doc.get("host"))

    if raw.get("id") is None:
        raise ValueError("this backup cannot be put back")

    body = host_input(raw)
    body.pop("id", None)

    try:
        cur = _get_raw(c, raw["id"])
    except (PanelError, ValueError):
        cur = None

    if cur is None:
        made = _dict(_call(c, "POST", "/api/host/", body=body))
        audit(c, f"host “{body.get('remark')}” made again from a backup")
        return {"host": host_row(made), "made_again": True}

    bk = _backup(c, cur, "before a restore")
    made = _dict(_call(c, "PUT", f"/api/host/{int(raw['id'])}", body=body))
    audit(c, f"host “{body.get('remark')}” put back from a backup")
    return {"host": host_row(made), "made_again": False, "backup": bk}


# ------------------------------------------------------------------ writing
def host_create(c: dict, data: dict) -> dict:
    """Make a host: only the fields given are set; the rest are the panel's defaults (security the inbound's own, no ALPN, no advanced settings). The priority is the next free one unless given."""
    top = max([h.get("priority") for h in _all_raw(c) if isinstance(h.get("priority"), int)], default=0) + 1
    body, _changed = apply(data, None, top)
    made = _dict(_call(c, "POST", "/api/host/", body=body))
    audit(c, f"host “{body['remark']}” made")
    return {"host": host_row(made)}


def host_update(c: dict, hid: int, data: dict) -> dict:
    """Change a host: the whole host is sent with only the fields that differ changed, after it is saved to a file."""
    cur = _get_raw(c, hid)
    body, changed = apply(data, cur)

    if not changed:
        raise ValueError("nothing to change: the host already has these values")

    bk = _backup(c, cur, "before a change")
    made = _dict(_call(c, "PUT", f"/api/host/{int(hid)}", body=body))
    audit(c, f"host “{made.get('remark') or cur.get('remark')}” changed ({', '.join(sorted(changed))})")
    return {"host": host_row(made), "backup": bk, "changed": sorted(changed)}


def host_clone(c: dict, hid: int) -> dict:
    """A copy of a host (the remark ends with “copy”, the next free priority), switched off so that it does not reach users before it is looked at."""
    cur = _get_raw(c, hid)
    body = host_input(cur)
    body.pop("id", None)
    body["remark"] = (str(body.get("remark") or "host") + " copy")[:256]
    body["priority"] = max([h.get("priority") for h in _all_raw(c) if isinstance(h.get("priority"), int)], default=0) + 1
    body["is_disabled"] = True
    made = _dict(_call(c, "POST", "/api/host/", body=body))
    audit(c, f"host “{cur.get('remark')}” cloned (the copy is off)")
    return {"host": host_row(made)}


def host_delete(c: dict, hid: int, confirm: bool = False) -> dict:
    """Delete a host (needs confirm); it is saved to a file first, so Backups can make it again."""
    cur = _get_raw(c, hid)

    if not confirm:
        raise ValueError("deleting a host cannot be undone on the panel: send confirm: true (the host is saved here first)")

    bk = _backup(c, cur, "before a delete")
    _call(c, "DELETE", f"/api/host/{int(hid)}")
    audit(c, f"host “{cur.get('remark')}” deleted (saved as {bk})", "warn")
    return {"deleted": True, "backup": bk}


def bulk(c: dict, what: str, ids, confirm: bool = False) -> dict:
    """Enable, disable or delete (needs confirm) several hosts with the panel's own bulk routes; a delete saves every host to a file first."""
    if what not in ("enable", "disable", "delete"):
        raise ValueError("the action is one of: enable, disable, delete")

    if not isinstance(ids, list) or not ids or any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in ids):
        raise ValueError("choose at least one host")

    ids = list(dict.fromkeys(ids))

    if len(ids) > MAX_BULK:
        raise ValueError(f"at most {MAX_BULK} hosts at a time")

    if what == "delete":
        if not confirm:
            raise ValueError("deleting hosts cannot be undone on the panel: send confirm: true (every host is saved here first)")

        for h in _list(_call(c, "GET", "/api/hosts?" + urlencode([("ids", i) for i in ids] + [("limit", len(ids))]))):
            if isinstance(h, dict) and h.get("id") in ids:
                _backup(c, h, "before a delete")

    r = _dict(_call(c, "POST", f"/api/hosts/bulk/{what}", body={"ids": ids}, timeout=60))
    n = r.get("count") if isinstance(r.get("count"), int) else len(ids)
    audit(c, f"{what} done for {n} host{'s' if n != 1 else ''}", "warn" if what == "delete" else "info")
    return {"action": what, "count": n, "hosts": [str(x) for x in _list(r.get("hosts"))][:100]}
