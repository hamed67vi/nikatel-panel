"""PasarGuard: ONE step makes a new KIND of account (the owner, 2026-10-15: "to make an account we only choose its kind and the panel builds it").

A link in a customer's subscription stands on three things that a PasarGuard panel keeps in three places: an INBOUND in a core (the Xray listener), a GROUP (which inbounds a user gets)
and a HOST (the address and port that are written into the link). Making one meant doing all three by hand, in this order, and a missing step only shows as a link that is not there.
`plan` checks the three together and writes nothing; `make` does them in order - the inbound (the core is backed up first), the group (a new one, or the tag put into one that is there), the
host - reads each one back, and when a step fails it takes the earlier ones back, so the panel is left as it was. Everything is done through pg_cores, pg_access and pg_hosts: every check,
every backup and every refusal of those still applies."""
from __future__ import annotations

import ipaddress

from . import panels, pg_access, pg_cores, pg_hosts
from .panels import PanelError

CERT = "/var/lib/pg-node/certs/ssl_cert.pem"            # where a PasarGuard node keeps its certificate, when no TLS inbound of the panel says otherwise
KEY = "/var/lib/pg-node/certs/ssl_key.pem"

# the kinds: what the owner picks from. "needs" says which fields of the form the kind uses (tls: the certificate files, reality: the site it imitates, path / service: the transport's own name)
KINDS: dict[str, dict] = {
    "vless-reality": {"title": "VLESS · REALITY", "note": "Direct to the node, no domain and no certificate: the node imitates a real site.", "protocol": "vless", "network": "tcp", "security": "reality",
                      "ports": (443, 8443, 2053), "needs": ("reality",)},
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


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v.strip("[]"))
    except ValueError:
        return False

    return True


def _xray_cores(c: dict) -> list[dict]:
    raw = panels.call(c, "GET", "/api/cores")
    items = raw.get("cores") if isinstance(raw, dict) else raw
    return [x for x in (items if isinstance(items, list) else []) if isinstance(x, dict) and x.get("id") is not None and str(x.get("type") or "xray").lower() == "xray"]


def options(c: dict) -> dict:
    """What the form chooses from: the kinds, the cores (with the ports they use), the groups, the addresses of the nodes, and the certificate files and REALITY site the panel's own inbounds already use."""
    pg_access.need_pasarguard(c)
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

    try:
        addresses = list(dict.fromkeys(n["address"] for n in panels.parse_nodes(panels.call(c, "GET", "/api/nodes")) if n["address"]))
    except (PanelError, ValueError):
        addresses = []

    groups = [{"id": g["id"], "name": g["name"], "tags": g["inbound_tags"]} for g in (pg_access.group_row(r) for r in pg_access.as_rows(panels.call(c, "GET", "/api/groups"), "groups"))]
    kinds = [{"id": k, "title": v["title"], "note": v["note"], "ports": list(v["ports"]), "needs": list(v["needs"]), "protocol": v["protocol"], "network": v["network"], "security": v["security"]} for k, v in KINDS.items()]
    return {"kinds": kinds, "cores": cores, "groups": groups, "addresses": addresses, "tls": tls or {"cert": CERT, "key": KEY, "guess": True}, "reality": reality}


def _inputs(c: dict, b: dict) -> dict:
    """The form, checked and complete (a ValueError says what is wrong in one sentence): the kind, the name, the address, the port, the core, the group choice and the pieces of the inbound and of the host."""
    kind = KINDS.get(str(b.get("kind") or ""))

    if kind is None:
        raise ValueError("choose the kind of account first")

    name = pg_access.name_ok(b.get("name"), "account type", 3)
    address = pg_hosts._list_of_names(b.get("address"), "address")

    if not address:
        raise ValueError("write the address the links point at (a domain, or the IP of the node)")

    port = pg_cores._int_port(b.get("port") if b.get("port") not in (None, "") else kind["ports"][0])
    hp = b.get("host_port")
    host_port = None if hp in (None, "", 0) else pg_cores._int_port(hp)
    cores = _xray_cores(c)
    core_id = int(b.get("core_id") or 0) or (int(cores[0]["id"]) if cores else 0)

    if not any(int(x["id"]) == core_id for x in cores):
        raise ValueError("that core is not on the panel (any more)")

    mode = str(b.get("group_mode") or "new")

    if mode not in ("new", "existing", "none"):
        raise ValueError("the group is new, an existing one, or none")

    if mode == "existing" and not b.get("group_id"):
        raise ValueError("choose the group that gets the new inbound")

    link = str(b.get("link_name") or "").strip() or f"{name} ({{USERNAME}})"

    def text(k: str) -> str:
        return str(b.get(k) or "").strip()

    f = {"kind_id": str(b["kind"]), "kind": kind, "name": name, "address": address, "port": port, "host_port": host_port, "core_id": core_id, "group_mode": mode, "group_id": int(b.get("group_id") or 0),
         "group_name": text("group_name") or name, "link": link, "path": text("path"), "service": text("service"), "host_header": text("host_header"), "sni": text("sni"),
         "tls_cert": text("tls_cert"), "tls_key": text("tls_key"), "reality_dest": text("reality_dest"), "reality_names": text("reality_names"), "reality_short_ids": text("reality_short_ids")}

    if "tls" in kind["needs"] and not (f["tls_cert"] and f["tls_key"]):
        f["tls_cert"], f["tls_key"] = f["tls_cert"] or CERT, f["tls_key"] or KEY

    return f


def _spec(f: dict, tag: str) -> dict:
    """The flat form of the inbound builder (pg_cores.build_inbound) for this kind."""
    k = f["kind"]
    spec: dict = {"protocol": k["protocol"], "network": k["network"], "security": k["security"], "port": f["port"], "tag": tag}

    if k["network"] == "ws" or k["network"] == "httpupgrade":
        spec["path"] = f["path"] or "/"

        if f["host_header"]:
            spec["host"] = f["host_header"]
    elif k["network"] == "grpc":
        spec["service"] = f["service"] or "grpc"

    if k["security"] == "tls":
        spec.update({"tls_cert": f["tls_cert"], "tls_key": f["tls_key"], "alpn": ALPN.get(k["network"], "")})
    elif k["security"] == "reality":
        spec.update({"reality_dest": f["reality_dest"], "reality_names": f["reality_names"], "reality_short_ids": f["reality_short_ids"]})

    return {k2: v for k2, v in spec.items() if v not in ("", None)}


def _host_data(f: dict, tag: str) -> dict:
    """The host of the new inbound: the remark, the address, the port, the inbound's tag, and what the kind needs in the link (the SNI and Host header of a domain behind TLS, a fingerprint)."""
    k = f["kind"]
    first = f["address"][0]
    data: dict = {"remark": f["link"], "address": f["address"], "inbound_tag": tag, "security": "inbound_default"}

    if f["host_port"]:
        data["port"] = f["host_port"]

    if k["security"] in ("tls", "reality"):
        data["fingerprint"] = "chrome"

    if k["security"] == "tls":
        sni = f["sni"] or (first if not _is_ip(first) else "")

        if sni:
            data["sni"] = [sni]

    if k["network"] == "ws" and not f["host_header"] and not _is_ip(first):
        data["host"] = [first]                    # a WebSocket behind a CDN is found by its Host header: the domain of the link

    return data


def _warnings(f: dict) -> list[str]:
    """Things that are not wrong for the panel but that the owner should know before they are made (the panel cannot see the node's disk or the DNS)."""
    k, out = f["kind"], []

    if k["security"] == "tls":
        out.append(f"The certificate {f['tls_cert']} and its key must exist on every node of the core, or Xray cannot start there after a restart.")

        if _is_ip(f["address"][0]) and not f["sni"]:
            out.append("The address is an IP and no SNI is given: the certificate has to be for that IP, or give an SNI (a domain).")

    return out


def _plan_of(c: dict, f: dict) -> dict:
    taken = pg_cores._all_tags(c)
    tag = pg_cores._free_tag(f["name"], taken)
    spec, host = _spec(f, tag), _host_data(f, tag)
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
    return {"valid": all(s["ok"] for s in steps), "steps": steps, "tag": tag, "needs_confirm": bool(r["warnings"]), "rev": r.get("rev"), "_f": f, "_spec": spec, "_host": host}


def plan(c: dict, body: dict) -> dict:
    """What making this kind would do, step by step, and whether every step passes; nothing is written: {valid, steps: [{key, title, ok, errors, warnings}], tag, needs_confirm, inbound}."""
    pg_access.need_pasarguard(c)
    p = _plan_of(c, _inputs(c, body))
    core = p["steps"][0]
    return {"valid": p["valid"], "steps": [{k: v for k, v in s.items() if k not in ("diff", "inbound")} for s in p["steps"]], "tag": p["tag"], "needs_confirm": p["needs_confirm"],
            "inbound": core.get("inbound"), "diff": core.get("diff")}


def _undo(c: dict, f: dict, tag: str, done: dict) -> str:
    """Take back what `make` did, last step first; a step that cannot be taken back is named, so it can be removed by hand."""
    back, stuck = [], []

    for step, fn in (("host", lambda: pg_hosts.host_delete(c, done["host"], confirm=True)),
                     ("group", lambda: pg_access.group_delete(c, done["group"], force=True) if done.get("group_new") else pg_access.group_update(c, done["group"], {"inbound_tags": done["group_before"]})),
                     ("inbound", lambda: pg_cores.inbound_apply(c, f["core_id"], "delete", tag=tag, confirm=True))):
        if step == "host" and "host" not in done or step == "group" and "group" not in done or step == "inbound" and "core" not in done:
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

    p = _plan_of(c, _inputs(c, body))

    if not p["valid"]:
        bad = next(s for s in p["steps"] if not s["ok"])
        raise ValueError(f"{bad['key']}: {bad['errors'][0]}")

    if p["needs_confirm"] and not confirm:
        raise pg_cores.NeedsConfirm([{"text": w} for w in p["steps"][0]["warnings"]])

    f, tag, done = p["_f"], p["tag"], {}
    out: dict = {"tag": tag}

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
        out["checks"] = _verify(c, f, tag, out)
    except (pg_cores.NeedsConfirm, pg_cores.Conflict):
        raise
    except (ValueError, PanelError, RuntimeError) as e:
        note = _undo(c, f, tag, done)
        msg = f"{e} - {note}" if note else str(e)

        if "No such file" in str(e) and f["kind"]["security"] == "tls":
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
    return checks
