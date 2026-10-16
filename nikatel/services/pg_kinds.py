"""PasarGuard: ONE step makes a new KIND of account (the owner, 2026-10-15: "to make an account we only choose its kind and the panel builds it").

A link in a customer's subscription stands on three things that a PasarGuard panel keeps in three places: an INBOUND in a core (the Xray listener), a GROUP (which inbounds a user gets)
and a HOST (the address and port that are written into the link). Making one meant doing all three by hand, in this order, and a missing step only shows as a link that is not there.
`plan` checks the three together and writes nothing; `make` does them in order - the inbound (the core is backed up first), the group (a new one, or the tag put into one that is there), the
host, and when asked a user TEMPLATE that gives the group (the recipe a new user is made from: after that the kind is one choice) - reads each one back, and when a step fails it takes the
earlier ones back, so the panel is left as it was. Everything is done through pg_cores, pg_access and pg_hosts: every check, every backup and every refusal of those still applies.
The owner only has to give the PROTOCOL (the kind): what he leaves empty is chosen here - the name, the address (the first node of the panel), the port (the first usual one that is free in the core), the
WebSocket path or gRPC service (random), the certificate files (the ones the TLS inbounds of the panel use), the site a REALITY inbound imitates (the panel's own, or the first usual site the panel finds usable)
- and `plan` says what was chosen (`auto`, and `notes` for how)."""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import re
import secrets
import threading
from urllib.parse import urlsplit

from . import panels, pg_access, pg_cores, pg_hosts, settings
from .panels import PanelError

CERT = "/var/lib/pg-node/certs/ssl_cert.pem"            # where a PasarGuard node keeps its certificate, when no TLS inbound of the panel says otherwise
KEY = "/var/lib/pg-node/certs/ssl_key.pem"

# the kinds: what the owner picks from. "needs" says which fields of the form the kind uses (tls: the certificate files, reality: the site it imitates, path / service: the transport's own name)
KINDS: dict[str, dict] = {
    "vless-reality": {"title": "VLESS · REALITY", "note": "Direct to the node, no domain and no certificate: the node imitates a real site.", "protocol": "vless", "network": "tcp", "security": "reality",
                      "ports": (443, 8443, 2053), "needs": ("reality", "spx")},
    "vless-reality-vision": {"title": "VLESS · REALITY · Vision", "note": "REALITY with the xtls-rprx-vision flow (what most clients prefer): no domain, no certificate.", "protocol": "vless", "network": "tcp", "security": "reality",
                             "flow": "xtls-rprx-vision", "ports": (443, 8443, 2053), "needs": ("reality", "spx")},
    "trojan-tcp-pin": {"title": "Trojan · TCP · TLS · own certificate", "note": "TLS with a certificate the panel makes itself and pins in the link: no domain and no files; the network sees the name you choose.", "protocol": "trojan",
                       "network": "tcp", "security": "tls", "cert": "self", "ports": (32001, 32002, 32003, 32004, 32005), "needs": ("pin",)},
    "vless-ws-pin": {"title": "VLESS · WebSocket · TLS · own certificate", "note": "WebSocket over TLS with a certificate the panel makes itself and pins in the link: no domain and no files.", "protocol": "vless", "network": "ws",
                     "security": "tls", "cert": "self", "ports": (32001, 32002, 32003, 32004, 32005), "needs": ("pin", "path")},
    "vless-ws-tls": {"title": "VLESS · WebSocket · TLS", "note": "Behind a CDN or a tunnel; the certificate is on the node, the address is a domain.", "protocol": "vless", "network": "ws", "security": "tls",
                     "ports": (443, 2083, 2087, 8443), "needs": ("tls", "path")},
    "vless-ws": {"title": "VLESS · WebSocket", "note": "Plain WebSocket for a CDN or a tunnel that does the TLS itself.", "protocol": "vless", "network": "ws", "security": "none",
                 "ports": (80, 8080, 2082, 8880), "needs": ("path",), "first_port": True},      # (first_port: the Add form starts at the first port even when the core holds it - the owner, 2026-10-16: "the default port of ws is 80"; a call with no port still gets a free one)
    "vless-grpc-tls": {"title": "VLESS · gRPC · TLS", "note": "gRPC over TLS; the certificate is on the node, the address is a domain.", "protocol": "vless", "network": "grpc", "security": "tls",
                       "ports": (443, 2053, 8443), "needs": ("tls", "service")},
    "trojan-ws-tls": {"title": "Trojan · WebSocket · TLS", "note": "Trojan over WebSocket and TLS, for a CDN.", "protocol": "trojan", "network": "ws", "security": "tls",
                      "ports": (443, 2096, 8443), "needs": ("tls", "path")},
    "trojan-tcp-tls": {"title": "Trojan · TCP · TLS", "note": "Trojan straight over TLS; the certificate is on the node, the address is a domain.", "protocol": "trojan", "network": "tcp", "security": "tls",
                       "ports": (443, 2053, 8443), "needs": ("tls",)},
    "vmess-ws-tls": {"title": "VMess · WebSocket · TLS", "note": "VMess over WebSocket and TLS, for a CDN.", "protocol": "vmess", "network": "ws", "security": "tls",
                     "ports": (443, 2083, 8443), "needs": ("tls", "path")},
    # the kinds of the owner's own links (2026-10-15): TCP with an HTTP header, xHTTP (plain, TLS, REALITY), httpupgrade over TLS, VLESS Encryption (ML-KEM)
    "vless-tcp-http": {"title": "VLESS · TCP · HTTP header", "note": "Plain TCP that looks like an HTTP request (headerType=http), with a path.", "protocol": "vless", "network": "tcp", "security": "none", "header": "http",
                       "ports": (1616, 2086, 80), "needs": ("path",)},
    "vless-xhttp": {"title": "VLESS · xHTTP", "note": "xHTTP with no TLS of its own: for a CDN that does the TLS (a Host header and a path).", "protocol": "vless", "network": "xhttp", "security": "none", "tuned": True,
                    "ports": (50335, 8080, 2082), "needs": ("path",)},
    "vless-xhttp-tls": {"title": "VLESS · xHTTP · TLS", "note": "xHTTP over TLS (stream-up), for a CDN or a domain.", "protocol": "vless", "network": "xhttp", "security": "tls", "mode": "stream-up", "tuned": True,
                        "ports": (8443, 443, 2053), "needs": ("tls", "path")},
    "vless-xhttp-reality": {"title": "VLESS · xHTTP · REALITY", "note": "xHTTP with REALITY: no domain, no certificate.", "protocol": "vless", "network": "xhttp", "security": "reality", "tuned": True,
                            "ports": (4433, 2052, 2085), "needs": ("reality", "spx", "path")},
    "vless-httpupgrade-tls": {"title": "VLESS · HTTPUpgrade · TLS", "note": "HTTPUpgrade over TLS, for a CDN or a domain.", "protocol": "vless", "network": "httpupgrade", "security": "tls",
                              "ports": (2096, 443, 2053), "needs": ("tls", "path")},
    "vless-mlkem": {"title": "VLESS · Encryption (ML-KEM)", "note": "VLESS with post-quantum encryption (mlkem768x25519plus): paste the two strings that `xray vlessenc` prints - the panel cannot make them.", "protocol": "vless",
                    "network": "tcp", "security": "none", "ports": (80, 8088, 2095), "needs": ("mlkem",)},
    "shadowsocks": {"title": "Shadowsocks", "note": "Shadowsocks on a port of its own: no domain, no certificate.", "protocol": "shadowsocks", "network": "tcp", "security": "none",
                    "ports": (8388, 2375, 9443), "needs": ()},
}
ALPN = {"ws": "http/1.1", "grpc": "h2", "tcp": "h2,http/1.1"}     # what the TLS inbound of each transport offers
REALITY_SITES = ("play.google.com:443", "www.samsung.com:443", "amp-api-edge.apps.apple.com:443", "www.cloudflare.com:443", "www.speedtest.net:443")      # tried in this order when nothing says which site a REALITY inbound imitates (the first three are the ones of the owner's own links)
SPOOF_SNIS = ("pish-data-31.myket.ir", "pish-data-14.myket.ir", "pish-data-32.myket.ir", "pish-data-33.myket.ir", "pish-data-34.myket.ir", "pish-data-35.myket.ir", "pish-data-36.myket.ir", "pish-data-38.myket.ir")      # the names the links of the owner show to the network when the certificate is the panel's own (kinds "own certificate")
FINGERPRINTS = ("chrome", "firefox", "safari", "ios", "android", "edge", "random")

# ---- the table of the Add form: the DRAWERS (one per protocol, each with its own emoji) and the short name on every button (the owner, 2026-10-16: "write the names short, e.g. ws+tls" and "every
# protocol its own emoji"). The title stays the long name (the tooltip of the button, the name of the account made from it).
FAMILIES: tuple[tuple[str, str, str], ...] = (("vless", "VLESS", "⚡"), ("trojan", "Trojan", "🐴"), ("vmess", "VMess", "💬"), ("shadowsocks", "Shadowsocks", "👻"))
SHORT = {"vless-reality": "reality", "vless-reality-vision": "reality+vision", "trojan-tcp-pin": "tcp+tls pin", "vless-ws-pin": "ws+tls pin", "vless-ws-tls": "ws+tls", "vless-ws": "ws", "vless-grpc-tls": "grpc+tls",
         "trojan-ws-tls": "ws+tls", "trojan-tcp-tls": "tcp+tls", "vmess-ws-tls": "ws+tls", "vless-tcp-http": "tcp+http", "vless-xhttp": "xhttp", "vless-xhttp-tls": "xhttp+tls", "vless-xhttp-reality": "xhttp+reality",
         "vless-httpupgrade-tls": "httpupgrade+tls", "vless-mlkem": "ML-KEM", "shadowsocks": "tcp+udp"}
BUILTIN_FAMILY = {f[0] for f in FAMILIES}
CUSTOM_KEY = "pg_kinds"                    # the settings document: {"custom": [the kinds the owner added], "families": [the drawers he made: {id, label, emoji}]}
MAX_CUSTOM = 40
_CUSTOM_LOCK = threading.Lock()
SECURITY_CHOICES = ("none", "tls", "tls-pin", "reality")          # what the form of a protocol of your own offers (tls-pin: TLS with a certificate the panel makes itself and pins in the link)
PORTS_BY = {"tls": (443, 8443, 2053), "reality": (443, 8443, 2053), "pin": (32001, 32002, 32003, 32004, 32005), "none": (80, 8080, 2082), "shadowsocks": (8388, 2375, 9443)}      # the usual ports of a protocol of your own, when its form names none


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v.strip("[]"))
    except ValueError:
        return False

    return True


def _self_cert(sni: str) -> dict:
    """A certificate the panel makes for the name `sni` (a P-256 key, ten years): {cert, key, sha256}. `sha256` is the hash of the certificate that the link carries as pcs, so a client trusts exactly this one."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sni[:64])])
    san = x509.IPAddress(ipaddress.ip_address(sni.strip("[]"))) if _is_ip(sni) else x509.DNSName(sni)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=3650)).add_extension(x509.SubjectAlternativeName([san]), critical=False).sign(key, hashes.SHA256()))
    return {"cert": cert.public_bytes(serialization.Encoding.PEM).decode(), "sha256": hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest(),
            "key": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()}


def _xray_cores(c: dict) -> list[dict]:
    raw = panels.call(c, "GET", "/api/cores")
    items = raw.get("cores") if isinstance(raw, dict) else raw
    return [x for x in (items if isinstance(items, list) else []) if isinstance(x, dict) and x.get("id") is not None and str(x.get("type") or "xray").lower() == "xray"]


def _survey(c: dict) -> dict:
    """What the panel already has that a new kind can copy: its xray cores with the ports they listen on, the certificate files of its TLS inbounds and the site its REALITY inbounds imitate."""
    cores, tls, reality = [], None, None

    for x in _xray_cores(c):
        cfg = pg_cores._obj(x.get("config"))
        cores.append({"id": int(x["id"]), "name": str(x.get("name") or ""), "ports": sorted({r["port"] for r in pg_cores.inbound_rows(cfg) if isinstance(r["port"], int)})})

        for ib in pg_cores._dicts(cfg.get("inbounds")):
            ss = pg_cores._obj(ib.get("streamSettings"))
            certs = pg_cores._dicts(pg_cores._obj(ss.get("tlsSettings")).get("certificates"))
            rs = pg_cores._obj(ss.get("realitySettings"))

            if tls is None and certs and certs[0].get("certificateFile") and certs[0].get("keyFile"):
                tls = {"cert": str(certs[0]["certificateFile"]), "key": str(certs[0]["keyFile"])}

            if reality is None and rs.get("dest"):
                reality = {"dest": str(rs["dest"]), "names": [str(n) for n in (rs.get("serverNames") or []) if n]}

    return {"cores": cores, "tls": tls, "reality": reality}


def _node_addresses(c: dict) -> list[str]:
    try:
        return list(dict.fromkeys(n["address"] for n in panels.parse_nodes(panels.call(c, "GET", "/api/nodes")) if n["address"]))
    except (PanelError, ValueError):
        return []


# ---- the protocols the owner adds to the table (the owner, 2026-10-16: "let the user add other protocols to the table too"): a saved recipe in the settings, looked up together with KINDS everywhere a kind is
def _store() -> dict:
    d = settings.get(CUSTOM_KEY)
    return {"custom": [x for x in d.get("custom") or [] if isinstance(x, dict) and x.get("id")], "families": [x for x in d.get("families") or [] if isinstance(x, dict) and x.get("id")]}


def _kind_of(x: dict) -> dict:
    """The kind (the shape of an entry of KINDS) of a saved protocol of the owner's own: what its form needs follows from its transport and security."""
    out: dict = {"title": x["title"], "short": x["short"], "note": x.get("note") or "", "family": x["family"], "custom": True, "protocol": x["protocol"], "network": x["network"], "security": x["security"], "ports": tuple(x["ports"])}

    if x.get("via") == "json":
        return {**out, "needs": (), "json": x["text"]}

    needs = [("pin" if x.get("cert") == "self" else "tls")] if x["security"] == "tls" else ["reality", "spx"] if x["security"] == "reality" else []

    if x["network"] in ("ws", "httpupgrade", "xhttp") or x.get("header"):
        needs.append("path")
    elif x["network"] == "grpc":
        needs.append("service")

    out["needs"] = tuple(needs)

    for k in ("flow", "header", "cert"):
        if x.get(k):
            out[k] = x[k]

    if x["network"] == "xhttp":
        out.update(mode=x.get("xmode") or "auto", tuned=True)

    return out


def all_kinds() -> dict[str, dict]:
    """Every kind the Add form knows, the built-in ones first and then the owner's own, each with its short name and its drawer."""
    out = {k: {**v, "short": SHORT.get(k) or v["title"], "family": v["protocol"]} for k, v in KINDS.items()}

    for x in _store()["custom"]:
        try:
            out[x["id"]] = _kind_of(x)
        except (KeyError, TypeError, ValueError):
            continue                                              # a record that is not whole (a hand-edited database) is left out, not a reason for the form to fail

    return out


def families() -> list[dict]:
    """The drawers of the table: the four protocols, then the ones the owner made (only while they hold a protocol), each with its emoji."""
    st = _store()
    used = {x.get("family") for x in st["custom"]}
    own = [{"id": f["id"], "label": str(f.get("label") or f["id"]), "emoji": str(f.get("emoji") or "🧩"), "custom": True} for f in st["families"] if f["id"] in used and f["id"] not in BUILTIN_FAMILY]
    return [{"id": i, "label": label, "emoji": emoji, "custom": False} for i, label, emoji in FAMILIES] + own


def catalog() -> dict:
    """What the table shows: {kinds, families}. A kind has its long name (title) and short name, its drawer (family), its usual ports and the fields it uses (needs); one of the owner's own also carries `saved`, the record that the edit form is filled from."""
    saved = {x["id"]: x for x in _store()["custom"]}
    kinds = [{"id": k, "title": v["title"], "short": v["short"], "note": v["note"], "ports": list(v["ports"]), "needs": list(v["needs"]), "protocol": v["protocol"], "network": v["network"], "security": v["security"], "flow": v.get("flow", ""),
              "own_cert": v.get("cert") == "self", "first_port": bool(v.get("first_port")), "family": v["family"], "custom": bool(v.get("custom")), **({"saved": saved[k]} if k in saved else {})} for k, v in all_kinds().items()]
    return {"kinds": kinds, "families": families()}


def _words(v, what: str, lo: int, hi: int) -> str:
    """A name: its words with the spaces made single, `lo` to `hi` characters, no control character."""
    s = " ".join(str(v or "").split())

    if not lo <= len(s) <= hi or any(ord(ch) < 32 for ch in s):
        raise ValueError(f"{what}: {lo} to {hi} characters")

    return s


def _ports(v, default: tuple[int, ...]) -> list[int]:
    """The usual ports of a protocol of the owner's own: a list or a text (commas or spaces), up to eight, each 1 to 65535; none given = the usual ones of its security."""
    raw = re.split(r"[,\s]+", v) if isinstance(v, str) else list(v or [])
    out: list[int] = []

    for p in raw:
        if str(p).strip():
            n = pg_cores._int_port(p)

            if n not in out:
                out.append(n)

    if len(out) > 8:
        raise ValueError("at most 8 usual ports")

    return out or list(default)


def _family_of(b: dict, st: dict) -> str:
    """The drawer a new protocol goes into: one of the four, one the owner made before, or a NEW one (a name and an emoji; a name that is there already means that drawer)."""
    fid = str(b.get("family") or "").strip().lower()

    if fid in BUILTIN_FAMILY or any(f["id"] == fid for f in st["families"]):
        return fid

    if fid:
        raise ValueError("that group is not in the table (any more)")

    label = _words(b.get("family_label"), "the name of the new group", 2, 24)
    fid = re.sub(r"[^a-z0-9]+", "", label.lower())[:20]

    if len(fid) < 2:
        fid = "g" + secrets.token_hex(3)

    if fid in BUILTIN_FAMILY or any(f["id"] == fid for f in st["families"]):
        return fid

    emoji = "".join(str(b.get("family_emoji") or "").split())[:12] or "🧩"

    if any(ord(ch) < 32 for ch in emoji):
        raise ValueError("the emoji of the group has a control character")

    st["families"].append({"id": fid, "label": label, "emoji": emoji})
    return fid


def _from_form(b: dict) -> dict:
    """The recipe of the builder - protocol, transport, security and what goes with them (flow, HTTP header, xHTTP mode, usual ports) - checked by the rules of the inbound builder itself."""
    proto, net, sec = (str(b.get(k) or d).strip().lower() for k, d in (("protocol", ""), ("network", "tcp"), ("security", "none")))
    pin = sec == "tls-pin"
    sec = "tls" if pin else sec

    if proto not in pg_cores.PROTOCOLS:
        raise ValueError(f"the protocol is one of {', '.join(pg_cores.PROTOCOLS)} (anything else: write the JSON)")

    if net not in pg_cores.NETWORKS:
        raise ValueError(f"the transport is one of {', '.join(pg_cores.NETWORKS)}")

    if sec not in pg_cores.SECURITIES:
        raise ValueError(f"the security is one of {', '.join(SECURITY_CHOICES)}")

    if sec == "reality" and proto == "shadowsocks":
        raise ValueError("REALITY needs a vless, vmess or trojan inbound")

    flow = str(b.get("flow") or "").strip()

    if flow and (proto != "vless" or flow not in pg_cores.FLOWS):
        raise ValueError(f"the flow is one of {', '.join(pg_cores.FLOWS)} and only a vless inbound has one")

    if flow and (net != "tcp" or sec == "none"):
        raise ValueError(f"the flow {flow} needs a TCP inbound with TLS or REALITY")

    header = str(b.get("header") or "").strip().lower()
    header = "" if header == "none" else header

    if header and (header != "http" or net != "tcp"):
        raise ValueError("the HTTP header is for a TCP inbound (header: none or http)")

    xmode = str(b.get("xmode") or "").strip().lower() or "auto" if net == "xhttp" else ""

    if xmode and xmode not in pg_cores.XHTTP_MODES:
        raise ValueError(f"the xHTTP mode is one of {', '.join(pg_cores.XHTTP_MODES)}")

    usual = PORTS_BY["shadowsocks"] if proto == "shadowsocks" else PORTS_BY["pin"] if pin else PORTS_BY[sec]
    return {"protocol": proto, "network": net, "security": sec, "cert": "self" if pin else "", "flow": flow, "header": header, "xmode": xmode, "ports": _ports(b.get("ports"), usual)}


def _from_json(b: dict) -> dict:
    """The recipe written as the JSON of ONE inbound (for what the builder does not make: hysteria, wireguard, ...). Its tag and port are put in when an account is made. A secret in it is refused: the text is kept in the clear and every account would share it."""
    text = str(b.get("text") or "").strip()

    if not text or len(text) > 20000:
        raise ValueError("paste the JSON of ONE inbound (up to 20000 characters)")

    ib = pg_cores.parse_text(text)

    if not isinstance(ib, dict):
        raise ValueError("the inbound is a JSON object { … }")  # noqa: TRY004 - the caller shows the ValueError text to the operator

    proto = str(ib.get("protocol") or "").strip().lower()

    if not re.fullmatch(r"[a-z0-9_\-]{2,40}", proto):
        raise ValueError("the inbound needs a protocol, like vless or hysteria")

    if pg_cores.mask_secrets(ib)[1]:
        raise ValueError("the JSON holds a secret (a private key, a password, a client id): leave it out - the builder makes REALITY keys itself, and the panel puts the users in")

    ss = pg_cores._obj(ib.get("streamSettings"))
    net, sec = (str(ss.get(k) or d).strip().lower()[:20] for k, d in (("network", "tcp"), ("security", "none")))
    port = ib.get("port")
    ports = [port] if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535 else []
    return {"protocol": proto, "network": net, "security": sec, "ports": _ports(ports, PORTS_BY["tls" if sec == "tls" else "none"]), "text": json.dumps(ib, ensure_ascii=False, separators=(",", ":"))}


def _put(st: dict) -> None:
    used = {x["family"] for x in st["custom"]}
    settings.put(CUSTOM_KEY, {"custom": st["custom"], "families": [f for f in st["families"] if f["id"] in used]})


def save_custom(body: dict) -> dict:
    """Add a protocol to the table, or change one the owner added (body.id): its drawer (one of the four, one of his, or a new one with an emoji), its short name, and its recipe - the builder (protocol, transport, security, ...) or the JSON of an inbound. Returns the new catalog and the id."""
    with _CUSTOM_LOCK:
        st = _store()
        kid = str(body.get("id") or "").strip()

        if kid and not any(x["id"] == kid for x in st["custom"]):
            raise ValueError("that protocol is not in the table (any more)")

        if not kid and len(st["custom"]) >= MAX_CUSTOM:
            raise ValueError(f"at most {MAX_CUSTOM} protocols of your own")

        fam = _family_of(body, st)
        label = next((f[1] for f in FAMILIES if f[0] == fam), None) or next(f["label"] for f in st["families"] if f["id"] == fam)
        short = _words(body.get("short"), "the name", 1, 24)
        via = str(body.get("via") or "builder")

        if via not in ("builder", "json"):
            raise ValueError("a protocol is made with the builder or with JSON")

        recipe = _from_form(body) if via == "builder" else _from_json(body)

        if fam in BUILTIN_FAMILY and recipe["protocol"] != fam:
            raise ValueError(f"the group {label} holds {label} protocols only: choose another group, or a new one")

        given = " ".join(str(body.get("title") or "").split())
        title = _words(given, "the full name", 3, 80) if given else f"{label} · {short}"
        rec = {"id": kid or "c-" + secrets.token_hex(3), "family": fam, "short": short, "title": title, "note": " ".join(str(body.get("note") or "").split())[:200], "via": via, **recipe}

        if short.lower() in {v["short"].lower() for k, v in all_kinds().items() if v["family"] == fam and k != rec["id"]}:
            raise ValueError(f"“{short}” is in that group already: choose another name")

        st["custom"] = [rec if x["id"] == rec["id"] else x for x in st["custom"]] if kid else [*st["custom"], rec]
        _put(st)
        return {**catalog(), "id": rec["id"]}


def delete_custom(kid: str) -> dict:
    """Take a protocol of the owner's own out of the table (the accounts already made from it stay in the panel); a drawer he made goes with its last protocol. Returns the new catalog."""
    with _CUSTOM_LOCK:
        st = _store()

        if not any(x["id"] == kid for x in st["custom"]):
            raise ValueError("that protocol is not in the table (any more)")

        st["custom"] = [x for x in st["custom"] if x["id"] != kid]
        _put(st)
        return catalog()


def options(c: dict) -> dict:
    """What the form chooses from: the kinds and their drawers, the cores (with the ports they use), the groups, the addresses of the nodes, and the certificate files and REALITY site the panel's own inbounds already use."""
    pg_access.need_pasarguard(c)
    s = _survey(c)
    groups = [{"id": g["id"], "name": g["name"], "tags": g["inbound_tags"]} for g in (pg_access.group_row(r) for r in pg_access.as_rows(panels.call(c, "GET", "/api/groups"), "groups"))]
    return {"snis": list(SPOOF_SNIS), "fingerprints": list(FINGERPRINTS), **catalog(), "cores": s["cores"], "groups": groups, "addresses": _node_addresses(c), "tls": s["tls"] or {"cert": CERT, "key": KEY, "guess": True}, "reality": s["reality"]}


# ---- everything the owner does not say is chosen here (the owner, 2026-10-15: "I only give the protocol, you do the rest")
def _taken_names(c: dict) -> set[str]:
    """The names a new kind must not repeat: an inbound tag, a group and a template of that name would confuse the owner (compared without capitals)."""
    names = {t.lower() for t in pg_cores._all_tags(c)}
    names |= {str(g.get("name") or "").lower() for g in pg_access.as_rows(panels.call(c, "GET", "/api/groups"), "groups")}

    try:
        names |= {str(t.get("name") or "").lower() for t in pg_access.as_rows(panels.call(c, "GET", "/api/user_templates"), "templates")}
    except PanelError:
        pass                                                    # a limited admin may not read the templates: the other two lists are enough

    return names


def _auto_name(c: dict, kind: dict) -> str:
    base, taken, n = " ".join(kind["title"].replace(" · ", " ").replace("<=>", " ").replace(",", " ").split())[:56], _taken_names(c), 1      # (no comma and no <=> in a tag; the group name holds 64 characters)
    name = base

    while name.lower() in taken:
        n += 1
        name = f"{base} {n}"

    return name


def _auto_address(c: dict) -> str:
    """The address the links point at: the first node of the panel; else the host name of the panel itself when it is not a private or local address."""
    nodes = _node_addresses(c)

    if nodes:
        return nodes[0]

    host = urlsplit(str(c.get("url") or "")).hostname or ""

    if host and host != "localhost" and not (_is_ip(host) and (ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback)):
        return host

    raise ValueError("the panel does not say where its nodes are: write the address the links point at (a domain, or the IP of the node)")


def _auto_port(kind: dict, used: list[int]) -> int:
    """The first usual port of the kind that no inbound of the core listens on; when all of them are taken, a free one between 20000 and 39999."""
    free = next((p for p in kind["ports"] if p not in used), None)

    while free is None:
        p = 20000 + secrets.randbelow(20000)
        free = p if p not in used else None

    return free


def _auto_reality(c: dict, hint: dict | None) -> tuple[str, str]:
    """(the site a REALITY inbound imitates, how it was chosen): the one the panel's own REALITY inbound imitates; else the first of REALITY_SITES the panel finds usable (TLS 1.3, h2, x25519)."""
    if hint:
        return hint["dest"], "the site of the REALITY inbound the panel already has"

    for site in REALITY_SITES:
        try:
            r = pg_cores.reality_scan(c, site, 8)
        except (PanelError, ValueError):
            return REALITY_SITES[0], f"{REALITY_SITES[0]} (the panel could not be asked whether it is usable)"

        if r.get("feasible"):
            return site, f"{site} (the panel found it usable: TLS 1.3, h2, x25519)"

    return REALITY_SITES[0], f"{REALITY_SITES[0]} (none of the usual sites was found usable from the panel: look at it with Scan…)"


def _inputs(c: dict, b: dict) -> dict:
    """The form, checked and complete (a ValueError says what is wrong in one sentence). What is left empty is chosen here and reported in `auto` (the values) and `notes` (how): the name, the address, the
    port, the WebSocket path or gRPC service, the certificate files, the REALITY site."""
    kind = all_kinds().get(str(b.get("kind") or ""))

    if kind is None:
        raise ValueError("choose the kind of account first")

    def text(k: str) -> str:
        return str(b.get(k) or "").strip()

    survey, auto, notes = _survey(c), {}, []
    cores = survey["cores"]
    core_id = int(b.get("core_id") or 0) or (cores[0]["id"] if cores else 0)
    core = next((x for x in cores if x["id"] == core_id), None)

    if core is None:
        raise ValueError("that core is not on the panel (any more)")

    name = pg_access.name_ok(b["name"], "account type", 3) if text("name") else _auto_name(c, kind)
    address = pg_hosts._list_of_names(b.get("address"), "address")

    if not address:
        address = [_auto_address(c)]
        auto["address"] = address[0]
        notes.append(f"address {address[0]}: the first node of the panel")

    port = pg_cores._int_port(b.get("port")) if b.get("port") not in (None, "") else _auto_port(kind, core["ports"])
    hp = b.get("host_port")
    host_port = None if hp in (None, "", 0) else pg_cores._int_port(hp)
    mode = str(b.get("group_mode") or "new")

    if mode not in ("new", "existing", "none"):
        raise ValueError("the group is new, an existing one, or none")

    if mode == "existing" and not b.get("group_id"):
        raise ValueError("choose the group that gets the new inbound")

    template = bool(b.get("template"))

    if template and mode == "none":
        raise ValueError("a user template gives a group: choose a group, or leave the template out")

    f = {"template": template, "tpl_gb": b.get("tpl_gb") if b.get("tpl_gb") not in (None, "") else 50, "tpl_days": b.get("tpl_days") if b.get("tpl_days") not in (None, "") else 30,
         "kind_id": str(b["kind"]), "kind": kind, "name": name, "address": address, "port": port, "host_port": host_port, "core_id": core_id, "group_mode": mode, "group_id": int(b.get("group_id") or 0),
         "group_name": text("group_name") or name, "link": text("link_name") or f"{name} ({{USERNAME}})", "path": text("path"), "service": text("service"), "host_header": text("host_header"), "sni": text("sni"),
         "tls_cert": text("tls_cert"), "tls_key": text("tls_key"), "reality_dest": text("reality_dest"), "reality_names": text("reality_names"), "reality_short_ids": text("reality_short_ids"),
         "reality_spx": text("reality_spx"), "fingerprint": text("fingerprint"), "decryption": text("decryption"), "encryption": text("encryption")}

    if "mlkem" in kind["needs"] and not (f["decryption"] and f["encryption"]):
        raise ValueError("paste the two strings of `xray vlessenc` (decryption for the inbound, encryption for the links): the panel cannot make ML-KEM keys")

    if f["fingerprint"] and f["fingerprint"] not in FINGERPRINTS and f["fingerprint"] not in pg_hosts.FINGERPRINTS:
        raise ValueError("the fingerprint is one of " + ", ".join(FINGERPRINTS))

    if kind.get("cert") == "self":
        if not f["sni"]:
            f["sni"] = auto["sni"] = SPOOF_SNIS[0]
            notes.append(f"SNI {f['sni']}: the first name of your own links - it is what the network sees")

        notes.append("certificate: made here and written into the config of the core (nothing to put on the nodes); the link pins it by its hash")

    if not text("name"):
        auto["name"] = name

    if b.get("port") in (None, ""):
        auto["port"] = port

    if "path" in kind["needs"] and not f["path"]:
        f["path"] = auto["path"] = "/" + secrets.token_hex(3)
    elif "service" in kind["needs"] and not f["service"]:
        f["service"] = auto["service"] = "g" + secrets.token_hex(3)

    if "tls" in kind["needs"] and not (f["tls_cert"] and f["tls_key"]):
        t = survey["tls"] or {"cert": CERT, "key": KEY}
        f["tls_cert"], f["tls_key"] = f["tls_cert"] or t["cert"], f["tls_key"] or t["key"]
        auto["tls_cert"], auto["tls_key"] = f["tls_cert"], f["tls_key"]
        notes.append("certificate files: " + ("the ones the TLS inbounds of the panel use" if survey["tls"] else "a guess (no TLS inbound of the panel to copy them from): they must exist on every node"))

    if "reality" in kind["needs"] and not f["reality_dest"]:
        f["reality_dest"], how = _auto_reality(c, survey["reality"])
        auto["reality_dest"] = f["reality_dest"]
        notes.append("REALITY site: " + how)

    f["auto"], f["notes"] = auto, notes
    return f

def _spec(f: dict, tag: str, cert: dict | None = None) -> dict:
    """The flat form of the inbound builder (pg_cores.build_inbound) for this kind; `cert` is the certificate of an "own certificate" kind."""
    k = f["kind"]
    spec: dict = {"protocol": k["protocol"], "network": k["network"], "security": k["security"], "port": f["port"], "tag": tag}

    if k["network"] in ("ws", "httpupgrade", "xhttp") or k.get("header"):
        spec["path"] = f["path"] or "/"

        if f["host_header"]:
            spec["host"] = f["host_header"]
    elif k["network"] == "grpc":
        spec["service"] = f["service"] or "grpc"

    if k.get("header"):
        spec["header"] = k["header"]

    if k["network"] == "xhttp":
        spec["mode"] = k.get("mode", "auto")

        if k.get("tuned"):
            spec["xhttp_tuned"] = True

    if "mlkem" in k["needs"]:
        spec.update({"decryption": f["decryption"], "encryption": f["encryption"]})

    if k["security"] == "tls" and cert is not None:
        spec.update({"tls_cert_pem": cert["cert"], "tls_key_pem": cert["key"], "alpn": ALPN.get(k["network"], "")})
    elif k["security"] == "tls":
        spec.update({"tls_cert": f["tls_cert"], "tls_key": f["tls_key"], "alpn": ALPN.get(k["network"], "")})
    elif k["security"] == "reality":
        spec.update({"reality_dest": f["reality_dest"], "reality_names": f["reality_names"], "reality_short_ids": f["reality_short_ids"], "reality_spx": f["reality_spx"]})

    if k.get("flow"):
        spec["flow"] = k["flow"]

    return {k2: v for k2, v in spec.items() if v not in ("", None)}


def _json_text(f: dict, tag: str) -> str:
    """The inbound of a protocol written as JSON, with the tag and the port of this account put in."""
    ib = json.loads(f["kind"]["json"])
    ib["tag"], ib["port"] = tag, f["port"]
    return json.dumps(ib, ensure_ascii=False)


def _host_data(f: dict, tag: str, pin: str | None = None) -> dict:
    """The host of the new inbound: the remark, the address, the port, the inbound's tag, and what the kind needs in the link (the SNI and Host header of a domain behind TLS, a fingerprint)."""
    k = f["kind"]
    first = f["address"][0]
    data: dict = {"remark": f["link"], "address": f["address"], "inbound_tag": tag, "security": "inbound_default"}

    if f["host_port"]:
        data["port"] = f["host_port"]

    own = pin is not None

    if k["security"] == "reality" or (k["security"] == "tls" and not own):
        data["fingerprint"] = f["fingerprint"] or "chrome"
    elif f["fingerprint"]:
        data["fingerprint"] = f["fingerprint"]

    if own:
        data["sni"] = [f["sni"]]                                  # the name the network sees; the client trusts the certificate by its hash, not by this name
        data["pinned_peer_cert_sha256"] = pin
    elif k["security"] == "tls":
        sni = f["sni"] or (first if not _is_ip(first) else "")

        if sni:
            data["sni"] = [sni]

    if k["network"] in ("ws", "xhttp", "httpupgrade") and not own and not f["host_header"] and not _is_ip(first):
        data["host"] = [first]                    # a WebSocket behind a CDN is found by its Host header: the domain of the link

    return data


def _warnings(f: dict) -> list[str]:
    """Things that are not wrong for the panel but that the owner should know before they are made (the panel cannot see the node's disk or the DNS)."""
    k, out = f["kind"], []

    if "tls" in k["needs"]:
        out.append(f"The certificate {f['tls_cert']} and its key must exist on every node of the core, or Xray cannot start there after a restart.")

        if _is_ip(f["address"][0]) and not f["sni"]:
            out.append("The address is an IP and no SNI is given: the certificate has to be for that IP, or give an SNI (a domain).")

    return out


def _plan_of(c: dict, f: dict, real: bool = False) -> dict:
    """The three (four) steps checked. `real` makes the certificate of an "own certificate" kind (the plan only shows a stand-in: it writes nothing and must not make keys)."""
    taken = pg_cores._all_tags(c)
    tag = pg_cores._free_tag(f["name"], taken)
    cert = None

    if f["kind"].get("cert") == "self":
        cert = _self_cert(f["sni"]) if real else {"cert": "PREVIEW-ONLY-CERTIFICATE", "key": "PREVIEW-ONLY-KEY", "sha256": "0" * 64}

    text = _json_text(f, tag) if f["kind"].get("json") else None                  # a protocol of the owner's own written as JSON: its tag and port are put in here
    spec, host = None if text else _spec(f, tag, cert), _host_data(f, tag, cert["sha256"] if cert else None)
    steps: list[dict] = []
    core = next(x for x in _xray_cores(c) if int(x["id"]) == f["core_id"])
    r = pg_cores.inbound_dry_run(c, f["core_id"], "add", spec=spec, text=text)
    k = f["kind"]
    clash = [row["tag"] for row in pg_cores.inbound_rows(pg_cores._obj(core.get("config"))) if row["port"] == f["port"] and row["public"]]
    errors = list(r["errors"]) + ([f"port {f['port']} is used by the inbound “{clash[0]}” of this core: Xray cannot listen twice on one port (choose another port)"] if clash else [])
    steps.append({"key": "core", "title": f"Core “{core.get('name')}”: a new inbound “{tag}” ({k['protocol']} · {k['network']} · {k['security']}, port {f['port']})", "ok": bool(r["valid"]) and not clash,
                  "errors": errors, "warnings": [w["text"] for w in r["warnings"]], "diff": r.get("diff"), "inbound": r.get("inbound")})
    gs = pg_access.as_rows(panels.call(c, "GET", "/api/groups"), "groups")
    gerr, gtitle = [], "No group: the inbound will reach nobody until a group holds it"

    try:
        if f["group_mode"] == "new":
            name = pg_access.name_ok(f["group_name"], "group", 3)
            pg_access._clash(gs, name, "group")
            gtitle = f"Access: a new group “{name}” that holds the inbound"
        elif f["group_mode"] == "existing":
            g = pg_access.group_row(pg_access._find(gs, f["group_id"], "group"))
            gtitle = f"Access: the inbound is added to the group “{g['name']}” ({g['users'] or 0} users get it at once)"
    except ValueError as e:
        gerr.append(str(e))

    steps.append({"key": "group", "title": gtitle, "ok": not gerr, "errors": gerr, "warnings": []})
    herr = []

    try:
        pg_hosts.apply(host, None, 1)
    except ValueError as e:
        herr.append(str(e))

    where = f"{', '.join(f['address'])}:{f['host_port'] or f['port']}"
    steps.append({"key": "host", "title": f"Hosts: the link “{f['link']}” points at {where}", "ok": not herr, "errors": herr, "warnings": _warnings(f)})

    if f["template"]:
        terr = []

        try:
            pg_access.template_body(None, pg_access.to_panel_units({"name": f["name"], "group_ids": [f["group_id"] or 1], "data_limit_gb": f["tpl_gb"], "expire_days": f["tpl_days"]}, None))
            pg_access._clash(pg_access.as_rows(panels.call(c, "GET", "/api/user_templates"), "templates"), f["name"], "template")
        except ValueError as e:
            terr.append(str(e))

        gb, days = f["tpl_gb"], f["tpl_days"]
        steps.append({"key": "template", "title": f"Access: a user template “{f['name']}” ({'unlimited data' if not _num(gb) else f'{_num(gb):g} GB'}, {'no end' if not _num(days) else f'{_num(days):g} days'}) that gives the group: a new user is one choice", "ok": not terr,
                      "errors": terr, "warnings": []})

    return {"valid": all(s["ok"] for s in steps), "steps": steps, "tag": tag, "needs_confirm": bool(r["warnings"]), "rev": r.get("rev"), "_f": f, "_spec": spec, "_text": text, "_host": host}


def plan(c: dict, body: dict) -> dict:
    """What making this kind would do, step by step, and whether every step passes; nothing is written: {valid, steps: [{key, title, ok, errors, warnings}], tag, needs_confirm, inbound}."""
    pg_access.need_pasarguard(c)
    p = _plan_of(c, _inputs(c, body))
    core = p["steps"][0]
    return {"valid": p["valid"], "steps": [{k: v for k, v in s.items() if k not in ("diff", "inbound")} for s in p["steps"]], "tag": p["tag"], "needs_confirm": p["needs_confirm"],
            "inbound": core.get("inbound"), "diff": core.get("diff"), "auto": p["_f"]["auto"], "notes": p["_f"]["notes"]}


def _undo(c: dict, f: dict, tag: str, done: dict) -> str:
    """Take back what `make` did, last step first; a step that cannot be taken back is named, so it can be removed by hand."""
    back, stuck = [], []

    for step, fn in (("template", lambda: pg_access.template_delete(c, done["template"])),
                     ("host", lambda: pg_hosts.host_delete(c, done["host"], confirm=True)),
                     ("group", lambda: pg_access.group_delete(c, done["group"], force=True) if done.get("group_new") else pg_access.group_update(c, done["group"], {"inbound_tags": done["group_before"]})),
                     ("inbound", lambda: pg_cores.inbound_apply(c, f["core_id"], "delete", tag=tag, confirm=True))):
        if step in ("template", "host", "group") and step not in done or step == "inbound" and "core" not in done:
            continue

        try:
            fn()
            back.append(step)
        except (ValueError, PanelError, RuntimeError) as e:
            stuck.append(f"{step} ({str(e)[:120]})")

    return ("taken back: " + ", ".join(back) if back else "") + (("; " if back else "") + "could not be taken back, remove by hand: " + ", ".join(stuck) if stuck else "")


def make(c: dict, body: dict, confirm: bool = False, restart: bool = False) -> dict:
    """Make the kind: the inbound, then the group, then the host - each read back; a failing step takes the earlier ones back. `confirm` answers the warnings of the core, and a restart of the nodes needs it too."""
    pg_access.need_pasarguard(c)
    pg_cores._writable()

    if restart and not confirm:
        raise ValueError("restarting the nodes cuts their customers for a moment: send confirm: true")

    p = _plan_of(c, _inputs(c, body), real=True)

    if not p["valid"]:
        bad = next(s for s in p["steps"] if not s["ok"])
        raise ValueError(f"{bad['key']}: {bad['errors'][0]}")

    if p["needs_confirm"] and not confirm:
        raise pg_cores.NeedsConfirm([{"text": w} for w in p["steps"][0]["warnings"]])

    f, tag, done = p["_f"], p["tag"], {}
    out: dict = {"tag": tag, "auto": f["auto"], "notes": f["notes"]}

    if p["_host"].get("pinned_peer_cert_sha256"):
        out["pcs"] = p["_host"]["pinned_peer_cert_sha256"]

    try:
        r = pg_cores.inbound_apply(c, f["core_id"], "add", spec=p["_spec"], text=p["_text"], confirm=confirm)
        done["core"] = r["backup"]
        out["core"] = {"id": f["core_id"], "backup": r["backup"], "public_key": r.get("public_key")}

        if f["group_mode"] == "new":
            g = pg_access.group_create(c, f["group_name"], [tag])
            done.update(group=g["id"], group_new=True)
            out["group"] = {"id": g["id"], "name": g["name"], "created": True}
        elif f["group_mode"] == "existing":
            before = pg_access.group_row(pg_access._find(pg_access.as_rows(panels.call(c, "GET", "/api/groups"), "groups"), f["group_id"], "group"))["inbound_tags"]
            u = pg_access.group_update(c, f["group_id"], {"inbound_tags": [*before, tag]})
            done.update(group=f["group_id"], group_new=False, group_before=before)
            out["group"] = {"id": f["group_id"], "name": u["group"]["name"], "created": False}

        h = pg_hosts.host_create(c, p["_host"])["host"]
        done["host"] = h["id"]
        out["host"] = {"id": h["id"], "remark": h["remark"], "address": h["address"], "port": h["port"]}

        if f["template"]:
            tp = pg_access.template_create(c, {"name": f["name"], "group_ids": [out["group"]["id"]], "data_limit_gb": f["tpl_gb"], "expire_days": f["tpl_days"]})["template"]
            done["template"] = tp["id"]
            out["template"] = {"id": tp["id"], "name": tp["name"], "data_limit_gb": tp["data_limit_gb"], "expire_days": tp["expire_days"]}

        out["checks"] = _verify(c, f, tag, out)
    except (pg_cores.NeedsConfirm, pg_cores.Conflict):
        raise
    except (ValueError, PanelError, RuntimeError) as e:
        note = _undo(c, f, tag, done)
        msg = f"{e} - {note}" if note else str(e)

        if "No such file" in str(e) and "tls" in f["kind"]["needs"]:
            msg = f"the panel cannot read the certificate files {f['tls_cert']} and {f['tls_key']}: they must exist at that path where the panel runs and on its nodes - write the real paths in the form ({str(e)[:140]})"

        raise (PanelError(msg, e.code) if isinstance(e, PanelError) else ValueError(msg)) from None

    out["restarted"], out["restart_error"] = False, ""

    if restart:
        try:
            pg_cores.restart(c, f["core_id"], confirm=True)
            out["restarted"] = True
        except (ValueError, PanelError) as e:
            out["restart_error"] = str(e)[:200]                    # everything IS saved; only the restart failed

    return out


def _verify(c: dict, f: dict, tag: str, out: dict) -> list[dict]:
    """Read back what was made, from the panel, and say for each piece whether it is there: the inbound in the core, the tag in the group, the host on the inbound."""
    checks = []
    core = pg_cores._get_core(c, f["core_id"])
    checks.append({"what": "the inbound is in the core", "ok": any(r["tag"] == tag for r in pg_cores.inbound_rows(core["config"]))})

    if "group" in out:
        g = pg_access.group_row(pg_access._find(pg_access.as_rows(panels.call(c, "GET", "/api/groups"), "groups"), out["group"]["id"], "group"))
        checks.append({"what": "the group holds the inbound", "ok": tag in g["inbound_tags"]})

    h = pg_hosts.host_row(pg_hosts._get_raw(c, out["host"]["id"]))
    checks.append({"what": "the host is on the inbound", "ok": h["inbound_tag"] == tag and h["address"] == f["address"]})

    if out.get("pcs"):
        checks.append({"what": "the link pins the certificate", "ok": h["pinned_peer_cert_sha256"] == out["pcs"]})

    if "template" in out:
        tp = pg_access.template_row(pg_access._find(pg_access.as_rows(panels.call(c, "GET", "/api/user_templates"), "templates"), out["template"]["id"], "template"))
        checks.append({"what": "the template gives the group", "ok": out["group"]["id"] in tp["group_ids"]})

    return checks
