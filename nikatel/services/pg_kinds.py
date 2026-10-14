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
import secrets
from urllib.parse import urlsplit

from . import panels, pg_access, pg_cores, pg_hosts
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
                 "ports": (80, 8080, 2082, 8880), "needs": ("path",)},
    "vless-grpc-tls": {"title": "VLESS · gRPC · TLS", "note": "gRPC over TLS; the certificate is on the node, the address is a domain.", "protocol": "vless", "network": "grpc", "security": "tls",
                       "ports": (443, 2053, 8443), "needs": ("tls", "service")},
    "trojan-ws-tls": {"title": "Trojan · WebSocket · TLS", "note": "Trojan over WebSocket and TLS, for a CDN.", "protocol": "trojan", "network": "ws", "security": "tls",
                      "ports": (443, 2096, 8443), "needs": ("tls", "path")},
    "trojan-tcp-tls": {"title": "Trojan · TCP · TLS", "note": "Trojan straight over TLS; the certificate is on the node, the address is a domain.", "protocol": "trojan", "network": "tcp", "security": "tls",
                       "ports": (443, 2053, 8443), "needs": ("tls",)},
    "vmess-ws-tls": {"title": "VMess · WebSocket · TLS", "note": "VMess over WebSocket and TLS, for a CDN.", "protocol": "vmess", "network": "ws", "security": "tls",
                     "ports": (443, 2083, 8443), "needs": ("tls", "path")},
    "shadowsocks": {"title": "Shadowsocks", "note": "Shadowsocks on a port of its own: no domain, no certificate.", "protocol": "shadowsocks", "network": "tcp", "security": "none",
                    "ports": (8388, 2375, 9443), "needs": ()},
}
ALPN = {"ws": "http/1.1", "grpc": "h2", "tcp": "h2,http/1.1"}     # what the TLS inbound of each transport offers
REALITY_SITES = ("play.google.com:443", "www.samsung.com:443", "amp-api-edge.apps.apple.com:443", "www.cloudflare.com:443", "www.speedtest.net:443")      # tried in this order when nothing says which site a REALITY inbound imitates (the first three are the ones of the owner's own links)
SPOOF_SNIS = ("pish-data-31.myket.ir", "pish-data-14.myket.ir", "pish-data-32.myket.ir", "pish-data-33.myket.ir", "pish-data-34.myket.ir", "pish-data-35.myket.ir", "pish-data-36.myket.ir", "pish-data-38.myket.ir")      # the names the links of the owner show to the network when the certificate is the panel's own (kinds "own certificate")
FINGERPRINTS = ("chrome", "firefox", "safari", "ios", "android", "edge", "random")


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


def options(c: dict) -> dict:
    """What the form chooses from: the kinds, the cores (with the ports they use), the groups, the addresses of the nodes, and the certificate files and REALITY site the panel's own inbounds already use."""
    pg_access.need_pasarguard(c)
    s = _survey(c)
    groups = [{"id": g["id"], "name": g["name"], "tags": g["inbound_tags"]} for g in (pg_access.group_row(r) for r in pg_access.as_rows(panels.call(c, "GET", "/api/groups"), "groups"))]
    kinds = [{"id": k, "title": v["title"], "note": v["note"], "ports": list(v["ports"]), "needs": list(v["needs"]), "protocol": v["protocol"], "network": v["network"], "security": v["security"], "flow": v.get("flow", ""), "own_cert": v.get("cert") == "self"} for k, v in KINDS.items()]
    return {"snis": list(SPOOF_SNIS), "fingerprints": list(FINGERPRINTS), "kinds": kinds, "cores": s["cores"], "groups": groups, "addresses": _node_addresses(c), "tls": s["tls"] or {"cert": CERT, "key": KEY, "guess": True}, "reality": s["reality"]}


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
    base, taken, n = kind["title"].replace(" · ", " "), _taken_names(c), 1
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
    kind = KINDS.get(str(b.get("kind") or ""))

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
         "reality_spx": text("reality_spx"), "fingerprint": text("fingerprint")}

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

    if kind["network"] in ("ws", "httpupgrade") and not f["path"]:
        f["path"] = auto["path"] = "/" + secrets.token_hex(3)
    elif kind["network"] == "grpc" and not f["service"]:
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

    if k["network"] == "ws" or k["network"] == "httpupgrade":
        spec["path"] = f["path"] or "/"

        if f["host_header"]:
            spec["host"] = f["host_header"]
    elif k["network"] == "grpc":
        spec["service"] = f["service"] or "grpc"

    if k["security"] == "tls" and cert is not None:
        spec.update({"tls_cert_pem": cert["cert"], "tls_key_pem": cert["key"], "alpn": ALPN.get(k["network"], "")})
    elif k["security"] == "tls":
        spec.update({"tls_cert": f["tls_cert"], "tls_key": f["tls_key"], "alpn": ALPN.get(k["network"], "")})
    elif k["security"] == "reality":
        spec.update({"reality_dest": f["reality_dest"], "reality_names": f["reality_names"], "reality_short_ids": f["reality_short_ids"], "reality_spx": f["reality_spx"]})

    if k.get("flow"):
        spec["flow"] = k["flow"]

    return {k2: v for k2, v in spec.items() if v not in ("", None)}


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

    if k["network"] == "ws" and not own and not f["host_header"] and not _is_ip(first):
        data["host"] = [first]                    # a WebSocket behind a CDN is found by its Host header: the domain of the link

    return data


def _warnings(f: dict) -> list[str]:
    """Things that are not wrong for the panel but that the owner should know before they are made (the panel cannot see the node's disk or the DNS)."""
    k, out = f["kind"], []

    if k["security"] == "tls" and not k.get("cert"):
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

    spec, host = _spec(f, tag, cert), _host_data(f, tag, cert["sha256"] if cert else None)
    steps: list[dict] = []
    core = next(x for x in _xray_cores(c) if int(x["id"]) == f["core_id"])
    r = pg_cores.inbound_dry_run(c, f["core_id"], "add", spec=spec)
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

    return {"valid": all(s["ok"] for s in steps), "steps": steps, "tag": tag, "needs_confirm": bool(r["warnings"]), "rev": r.get("rev"), "_f": f, "_spec": spec, "_host": host}


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
        r = pg_cores.inbound_apply(c, f["core_id"], "add", spec=p["_spec"], confirm=confirm)
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

        if "No such file" in str(e) and f["kind"]["security"] == "tls" and not f["kind"].get("cert"):
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
