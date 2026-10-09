"""Tunnels that already exist on the servers but not in the panel: scan every server's backhaul
tomls and GRE links, pair the Iran side with the foreign side, and adopt a pair as a Tunnel record.
Also: the automatic choice of the Iran server for a new tunnel."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel
from ..ssh import Node, SSHError, run_script
from .events import emit
from .nodes import parse_ping

log = get_logger("detect")


def parse_scan(out: str) -> dict:
    """{'bh': [{file, role, addr, host, port, transport, ports}], 'gre': [{iface, ip, remote, unit}]}"""
    d: dict = {"bh": [], "gre": []}

    for ln in str(out).splitlines():
        p = ln.split()

        if ln.startswith("BH ") and len(p) >= 6:
            host, _, port = p[3].rpartition(":")
            d["bh"].append({"file": p[1], "role": p[2], "addr": p[3], "host": host, "port": int(port) if port.isdigit() else 0, "transport": p[4],
                            "ports": [x for x in p[5].split(",") if x and x != "none"]})
        elif ln.startswith("GRE ") and len(p) >= 4:
            d["gre"].append({"iface": p[1], "ip": p[2].split("/")[0], "remote": p[3] if len(p) > 3 else "", "unit": p[4] if len(p) > 4 else "none"})

    return d


def pair(scans: dict[int, dict], servers: dict[int, Server]) -> list[dict]:
    """Iran [server] tomls matched with foreign [client] tomls by transport + control port + the Iran GRE ip."""
    out = []
    foreign_clients = [(sid, bh) for sid, sc in scans.items() if servers[sid].role == "foreign" for bh in sc["bh"] if bh["role"] == "client"]

    for sid, sc in scans.items():
        if servers[sid].role != "iran":
            continue

        for bh in sc["bh"]:
            if bh["role"] != "server":
                continue

            gre = next((g for g in sc["gre"] if g["ip"] == bh["host"]), None)
            match = next(((fid, fb) for fid, fb in foreign_clients if fb["port"] == bh["port"] and fb["transport"] == bh["transport"] and fb["host"] == bh["host"]), None)
            fgre = None

            if match:
                fgre = next((g for g in scans[match[0]]["gre"] if gre and g["iface"] == gre["iface"]), None)

            out.append({"iran_id": sid, "iran_name": servers[sid].name, "foreign_id": match[0] if match else None, "foreign_name": servers[match[0]].name if match else "",
                        "name": gre["iface"] if gre else bh["file"], "transport": bh["transport"], "port": bh["port"], "ports": bh["ports"],
                        "iran_tunnel_ip": gre["ip"] if gre else "", "foreign_tunnel_ip": fgre["ip"] if fgre else (gre["remote"] if gre else ""),
                        "paired": bool(match), "gre_unit": gre["unit"] if gre else "none"})

    return out


def scan() -> dict:
    with session() as db:
        servers = {s.id: s for s in db.scalars(select(Server).where(Server.enabled.is_(True)))}
        known = {t.name for t in db.scalars(select(Tunnel))}
        known_ports = {(t.iran_server_id, int(t.port or 0)) for t in db.scalars(select(Tunnel))}
        nodes = {sid: Node(s.host, s.ssh_user, s.ssh_port) for sid, s in servers.items()}

    def one(item):
        sid, node = item

        try:
            return sid, parse_scan(run_script(node, "scan_tunnels", timeout=30)), ""
        except (SSHError, ValueError) as e:
            return sid, {"bh": [], "gre": []}, str(e)[:100]

    scans, errors = {}, []

    with ThreadPoolExecutor(10) as ex:
        for sid, sc, err in ex.map(one, nodes.items()):
            scans[sid] = sc

            if err:
                errors.append({"server": servers[sid].name, "error": err})

    pairs = pair(scans, servers)

    for p in pairs:
        p["in_panel"] = p["name"] in known or (p["iran_id"], p["port"]) in known_ports

    return {"pairs": pairs, "errors": errors, "scanned": len(scans)}


def adopt(p: dict) -> str:
    """Create the Tunnel record for a detected pair (nothing changes on the servers)."""
    name = re.sub(r"[^A-Za-z0-9._-]", "-", str(p.get("name", "")))[:64]

    if not name or not p.get("iran_id") or not p.get("foreign_id"):
        raise ValueError("a paired tunnel with a name and both servers is needed")

    with session() as db:
        if db.scalar(select(Tunnel).where(Tunnel.name == name)):
            raise ValueError(f"the tunnel {name} is already in the panel")

        iran, foreign = db.get(Server, int(p["iran_id"])), db.get(Server, int(p["foreign_id"]))

        if iran is None or foreign is None:
            raise ValueError("server not found")

        ports = [int(x.split("=")[0]) for x in p.get("ports", []) if str(x).split("=")[0].isdigit()]
        t = Tunnel(name=name, type="backhaul", transport=str(p.get("transport") or "tcpmux"), engine="backhaul", iran_server_id=iran.id, foreign_server_id=foreign.id,
                   port=int(p.get("port") or 0), iran_tunnel_ip=str(p.get("iran_tunnel_ip") or ""), foreign_tunnel_ip=str(p.get("foreign_tunnel_ip") or ""),
                   forwarded_ports=ports, record={"port_mapping": ",".join(p.get("ports", [])), "adopted_at": int(time.time())})
        db.add(t)
        emit(db, "info", "tunnels", f"Tunnel {name} ({iran.name} ⇄ {foreign.name}) found on the servers and added to the panel.", name)

    return name


# ------------------------------------------------------------------ automatic choice of the Iran server
def auto_iran(foreign_id: int, ports_text: str = "") -> list[dict]:
    """Rank the Iran servers for a new tunnel to this foreign server: ping quality from each one (loss,
    rtt), free resources, how many tunnels it already carries, and whether the wanted ports are free."""
    from .builder import parse_ports

    wanted = {p.listen for p in parse_ports(ports_text)} if ports_text.strip() else set()

    with session() as db:
        f = db.get(Server, foreign_id)

        if f is None or f.role != "foreign":
            raise ValueError("choose a foreign server")

        fhost = f.host
        iran = [(s.id, s.name, Node(s.host, s.ssh_user, s.ssh_port), dict(s.facts or {})) for s in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)))]
        load = {}

        for t in db.scalars(select(Tunnel)):
            load[t.iran_server_id] = load.get(t.iran_server_id, 0) + 1
            load.setdefault(("ports", t.iran_server_id), set()).update(int(x) for x in (t.forwarded_ports or []) if str(x).isdigit())

    def one(item):
        sid, name, node, facts = item
        row = {"id": sid, "name": name, "tunnels": load.get(sid, 0), "cpu": facts.get("cpu"), "ram": facts.get("ram"), "load1": facts.get("load1"), "cores": facts.get("cores") or 1}
        busy = wanted & load.get(("ports", sid), set())

        if busy:
            row.update(score=0, reason="port " + ", ".join(map(str, sorted(busy))) + " already used here")
            return row

        try:
            tx, rx, avg = parse_ping(run_script(node, "ping_host", {"HOST": fhost}, timeout=30))
        except (SSHError, ValueError) as e:
            row.update(score=0, reason="cannot ping: " + str(e)[:60])
            return row

        if rx == 0:
            row.update(score=0, reason="cannot reach the foreign server", rtt_ms=None, loss_pct=100)
            return row

        loss = round((tx - rx) * 100.0 / max(tx, 1))
        q = max(0.0, 100 - loss * 10) * 0.6 + max(0.0, 100 - max(0.0, (avg or 0) - 40) / 2) * 0.4
        r = max(0.0, 100 - (facts.get("cpu") or 0)) * 0.5 + max(0.0, 100 - (facts.get("ram") or 0)) * 0.3 + max(0.0, 100 - min(100, (facts.get("load1") or 0) / (facts.get("cores") or 1) * 50)) * 0.2
        u = max(0.0, 100 - load.get(sid, 0) * 12.5)
        row.update(rtt_ms=avg, loss_pct=loss, quality=round(q), resources=round(r), unused=round(u), score=round(q * 0.4 + r * 0.3 + u * 0.3),
                   reason=f"{avg} ms, {loss}% loss, {load.get(sid, 0)} tunnels, cpu {facts.get('cpu')}%")
        return row

    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, iran))

    return sorted(rows, key=lambda r: -r["score"])
