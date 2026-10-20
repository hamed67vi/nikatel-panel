"""Which counter belongs to which CONFIG (host) of a PasarGuard panel. Pure: the panel's hosts, cores, nodes, our servers, the DNS records of the DNS accounts and the DNS answers come in as plain data, the map comes out.

A PasarGuard HOST is one line of a subscription: an address (a server of ours, a name that points at one, or a name behind a CDN), an optional port override and the INBOUND (tag) it belongs to. The inbound lives in a
CORE config and listens on a port of the NODES that use that core; a node is one of our servers (`pg_nodes.match_servers`). From that:

  * a host whose address is a server of ours -> the counter of that server's port (the override, else the port of the inbound on that node). Source "port".
  * a host whose name is a record of a DNS account with the CDN cloud ON -> the traffic report of that domain at the CDN (source "cdn"); the origin port behind it is shared by every host of the inbound.
  * a host whose address is `{SERVER_IP}` (the node itself) -> the inbound's port on every node that carries it.
  * anything else (an address that is not ours, not in our DNS accounts) -> no source ("none"), with the reason.
Two hosts that end on the same server and port cannot be told apart by any counter: they are SHARED (the number is for all of them).
The map also says which ports each server should count (`servers`): every port of the inbounds the hosts use or that listen to the world, the ports of the hosts that point at a server of ours, the origin ports behind a CDN."""
from __future__ import annotations

import fnmatch
from collections.abc import Iterable

from . import pg_cores, pg_nodes

DEFAULT_CORE = 1                        # a node without a core_config_id runs the first core (the panel refuses to delete it)
RARE_NETWORKS = ("xhttp", "httpupgrade", "splithttp", "kcp", "quic", "hysteria", "hysteria2", "tuic")      # transports that older apps cannot open


def _s(v) -> str:
    return str(v if v is not None else "").strip()


def _strs(v) -> list[str]:
    if isinstance(v, str):
        v = [x for x in v.replace("\n", ",").split(",")]

    return [x for x in (_s(i) for i in (v if isinstance(v, (list, tuple)) else [])) if x]


def port_of(v) -> int | None:
    """A port as a whole number 1-65535; a range ('1000-2000'), a list or anything else is None (such an inbound cannot be metered by one counter)."""
    if isinstance(v, bool):
        return None

    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None

    return n if 1 <= n <= 65535 else None


def tunnel_ports(tunnels: Iterable[dict]) -> dict[int, dict[int, str]]:
    """{Iran server id: {user port: tunnel name}} from the forwarded ports of the tunnels (numbers or numeric text; anything else is skipped)."""
    out: dict[int, dict[int, str]] = {}

    for t in tunnels or []:
        sid = t.get("iran_server_id")

        for p in t.get("forwarded_ports") or []:
            n = port_of(p.get("port") if isinstance(p, dict) else p)

            if n and sid:
                out.setdefault(int(sid), {})[n] = _s(t.get("name"))

    return out


class _Index:
    """Our servers by IP address (an address of a server, or what a name of a server resolves to)."""

    def __init__(self, servers: list[dict], resolved: dict[str, list[str]]):
        self.by_ip: dict[str, dict] = {}
        res = {pg_nodes.norm(k): [pg_nodes.ip_of(x) for x in v if pg_nodes.ip_of(x)] for k, v in (resolved or {}).items()}

        for s in sorted(servers, key=lambda x: x["id"]):
            h = pg_nodes.norm(s.get("host"))

            for ip in ([pg_nodes.ip_of(h)] if pg_nodes.ip_of(h) else res.get(h, [])):
                self.by_ip.setdefault(ip, s)

        self.res = res

    def of_ips(self, ips: Iterable[str]) -> dict | None:
        for ip in ips:
            s = self.by_ip.get(pg_nodes.ip_of(ip))

            if s is not None:
                return s

        return None


def _dns_index(rows: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    exact: dict[str, list[dict]] = {}
    wild: list[dict] = []

    for r in rows or []:
        if str(r.get("type") or "").upper() not in ("A", "AAAA", "CNAME"):
            continue

        fq = pg_nodes.norm(r.get("fqdn"))

        if not fq:
            continue

        (wild if fq.startswith("*.") else exact.setdefault(fq, [])).append(r)

    return exact, wild


def classify(addr: str, idx: _Index, exact: dict, wild: list[dict]) -> dict:
    """What an address is: {"kind": "direct" | "cdn" | "node" | "unknown", "server": our server or None, ...}. `direct`: a server of ours (an IP, a name of a server, a name with an A record that is not behind a CDN or one that
    resolves to a server); `cdn`: a record of a DNS account with the cloud on; `node`: the template of the node's own address; `unknown`: the reason in `why`."""
    a = pg_nodes.norm(addr)

    if not a:
        return {"kind": "unknown", "server": None, "why": "the host has no address"}

    if "{" in a:
        return {"kind": "node", "server": None}

    ip = pg_nodes.ip_of(a)

    if ip:
        s = idx.by_ip.get(ip)
        return {"kind": "direct", "server": s, "via": "ip"} if s else {"kind": "unknown", "server": None, "why": f"{ip} is not one of our servers"}

    recs = list(exact.get(a, []))

    if not recs:
        recs = [r for r in wild if fnmatch.fnmatch(a, pg_nodes.norm(r.get("fqdn")))]

    cloud = next((r for r in recs if r.get("cloud")), None)

    if cloud is not None:
        origin = idx.of_ips(cloud.get("ips") or [])
        return {"kind": "cdn", "server": origin, "account": _s(cloud.get("account")), "domain": _s(cloud.get("domain")), "fqdn": a, "origin_ips": list(cloud.get("ips") or []),
                "origin_port": port_of(cloud.get("port")), "record_type": _s(cloud.get("type")).upper()}

    for r in recs:
        s = idx.of_ips(r.get("ips") or [])

        if s is not None:
            return {"kind": "direct", "server": s, "via": "dns-record"}

    s = idx.of_ips(idx.res.get(a, []))

    if s is not None:
        return {"kind": "direct", "server": s, "via": "dns"}

    why = f"{a} does not point at a server of ours" if (recs or idx.res.get(a)) else f"{a} is not in a DNS account of this panel and does not resolve to a server of ours"
    return {"kind": "unknown", "server": None, "why": why}


def build(hosts: list[dict], cores: list[dict], nodes: list[dict], servers: list[dict], dns_rows: list[dict] | None = None, resolved: dict[str, list[str]] | None = None,
          tunnels: list[dict] | None = None) -> dict:
    """The map. `hosts`: the panel's hosts as it gave them; `cores`: [{id, name, config}]; `nodes`: [{id, name, address, status, core_config_id}]; `servers`: [{id, name, host, role, enabled}].
    Returns {"nodes", "inbounds", "hosts", "servers", "cdn"} (see the module text); a host or inbound that cannot be placed is still there, with its reason."""
    idx = _Index(servers, resolved or {})
    exact, wild = _dns_index(dns_rows or [])
    tun = tunnel_ports(tunnels or [])
    by_sid = {s["id"]: s for s in servers}

    # ---- cores -> inbounds, nodes -> servers
    core_rows = {int(c["id"]): pg_cores.inbound_rows(c.get("config")) for c in cores if isinstance(c.get("id"), int)}
    info: dict[str, dict] = {}

    for rows in core_rows.values():
        for r in rows:
            info.setdefault(r["tag"], {"tag": r["tag"], "protocol": r["protocol"], "network": r["network"], "security": r["security"], "public": r["public"]})

    node_pairs, _free = pg_nodes.match_servers([{"address": n.get("address"), "name": n.get("name")} for n in nodes], servers, resolved or {})
    tag_ports: dict[tuple[int, str], set[int]] = {}               # (server id, tag) -> the ports of the inbound on the nodes of that server
    node_out = []

    for n, pr in zip(nodes, node_pairs):
        srv = pr["server"]
        core_id = n.get("core_config_id") if isinstance(n.get("core_config_id"), int) else DEFAULT_CORE
        tags = []

        for r in core_rows.get(core_id, []):
            p = port_of(r["port"])

            if p is None or not srv:
                continue

            tag_ports.setdefault((srv["id"], r["tag"]), set()).add(p)
            tags.append(r["tag"])

        node_out.append({"id": n.get("id"), "name": _s(n.get("name")), "address": _s(n.get("address")), "status": _s(n.get("status")), "server_id": srv["id"] if srv else None,
                         "server": srv["name"] if srv else "", "core_id": core_id, "tags": tags})

    carriers: dict[str, list[int]] = {}                           # tag -> the servers that carry it

    for (sid, tag) in sorted(tag_ports):
        carriers.setdefault(tag, []).append(sid)

    # ---- hosts
    plan: dict[int, dict[int, list[str]]] = {}                    # server id -> port -> why it is counted
    cdn: dict[str, dict] = {}
    used_tags: set[str] = set()
    out: list[dict] = []

    def want(sid: int, port: int | None, why: str) -> None:
        if port:
            plan.setdefault(sid, {}).setdefault(port, [])

            if why not in plan[sid][port]:
                plan[sid][port].append(why)

    for h in hosts:
        tag = _s(h.get("inbound_tag"))
        used_tags.add(tag)
        addrs = _strs(h.get("address"))
        over = port_of(h.get("port"))
        ib = info.get(tag, {})
        sec = _s(h.get("security")) or "inbound_default"
        row = {"id": h.get("id"), "remark": _s(h.get("remark")), "tag": tag, "protocol": ib.get("protocol", ""), "network": ib.get("network", ""),
               "security": ib.get("security", "") if sec == "inbound_default" else sec, "address": addrs[0] if addrs else "", "more_addresses": max(0, len(addrs) - 1),
               "port": over, "disabled": bool(h.get("is_disabled")), "kind": "unknown", "srcs": [], "origin_src": "", "cdn": None, "server_id": None, "server": "", "tunnel": "", "why": "",
               "rare": ib.get("network", "") in RARE_NETWORKS or ib.get("protocol", "") in RARE_NETWORKS}
        c = classify(row["address"], idx, exact, wild)
        row["kind"] = c["kind"]
        srv = c.get("server")

        if c["kind"] == "node":
            row["kind"] = "direct"
            row["why"] = "the address is the node itself: every node that carries the inbound"

            for sid in carriers.get(tag, []):
                for p in ([over] if over else sorted(tag_ports[(sid, tag)])):
                    row["srcs"].append(f"p:{sid}:{p}")
                    want(sid, p, f"host {row['id']}")

            if not row["srcs"]:
                row["kind"], row["why"] = "unknown", f"no node of ours carries the inbound {tag or '(none)'}"
        elif c["kind"] == "direct" and srv:
            row["server_id"], row["server"] = srv["id"], srv["name"]
            own = sorted(tag_ports.get((srv["id"], tag), set()))
            port = over or (own[0] if len(own) >= 1 else None)

            if port is None:
                row["kind"], row["why"] = "unknown", f"{srv['name']} carries no node with the inbound {tag or '(none)'} and the host has no port of its own"
            else:
                row["srcs"] = [f"p:{srv['id']}:{port}"]
                row["tunnel"] = tun.get(srv["id"], {}).get(port, "")
                want(srv["id"], port, f"host {row['id']}")

                if over and own and over not in own:
                    row["why"] = f"the host's port {over} is not the inbound's port {own[0]} (a redirect on the server is counted by the port the user dials)"
        elif c["kind"] == "cdn":
            ob = c.get("server")
            op = c.get("origin_port") or (min(tag_ports[(ob["id"], tag)]) if ob and tag_ports.get((ob["id"], tag)) else None)
            key = f"c:{c['account']}:{c['fqdn']}"
            row["cdn"] = {"account": c["account"], "domain": c["domain"], "fqdn": c["fqdn"], "origin_server_id": ob["id"] if ob else None, "origin_server": ob["name"] if ob else "", "origin_port": op,
                          "origin_ips": c.get("origin_ips", [])[:3]}
            row["srcs"] = [key]
            cdn.setdefault(key, {"src": key, "account": c["account"], "domain": c["domain"], "fqdn": c["fqdn"], "hosts": []})["hosts"].append(row["id"])

            if ob and op:
                row["origin_src"] = f"p:{ob['id']}:{op}"
                want(ob["id"], op, f"origin of host {row['id']}")
            else:
                row["why"] = "the CDN record has no origin server of ours" if not ob else "the origin port behind the CDN is not known"
        else:
            row["why"] = c.get("why", "")

        out.append(row)

    # ---- hosts that can only be counted together
    groups: dict[tuple, list[dict]] = {}

    for r in out:
        if r["kind"] == "direct" and r["srcs"]:
            groups.setdefault(tuple(r["srcs"]), []).append(r)
        elif r["kind"] == "cdn" and r["origin_src"]:
            groups.setdefault(("origin", r["origin_src"]), []).append(r)

    for g in groups.values():
        for r in g:
            r["shared_with"] = [x["id"] for x in g if x is not r]

    for r in out:
        r.setdefault("shared_with", [])

    # ---- ports of the inbounds themselves: the ones the hosts use or that listen to the world on a node of ours
    inbounds = []

    for tag in sorted(set(info) | used_tags):
        ib = info.get(tag, {"tag": tag, "protocol": "", "network": "", "security": "", "public": None})
        ports = []

        for sid in carriers.get(tag, []):
            for p in sorted(tag_ports[(sid, tag)]):
                if tag in used_tags or ib.get("public"):
                    want(sid, p, f"inbound {tag}")
                    ports.append({"server_id": sid, "server": by_sid[sid]["name"], "port": p, "src": f"p:{sid}:{p}"})

        inbounds.append({**ib, "ports": ports, "hosts": [r["id"] for r in out if r["tag"] == tag], "carried": bool(carriers.get(tag)), "used": tag in used_tags,
                         "rare": ib.get("network", "") in RARE_NETWORKS or ib.get("protocol", "") in RARE_NETWORKS})

    srv_plan = {sid: {"name": by_sid[sid]["name"], "role": by_sid[sid].get("role", ""), "ports": sorted(pp), "why": {str(p): w for p, w in sorted(pp.items())}} for sid, pp in sorted(plan.items()) if sid in by_sid}
    return {"nodes": node_out, "inbounds": inbounds, "hosts": out, "servers": srv_plan, "cdn": sorted(cdn.values(), key=lambda x: x["src"])}
