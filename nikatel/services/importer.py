"""Import the inventory of the old panel (its JSON files) into the database. Idempotent: running
it again updates records by name and never deletes anything."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel
from .events import emit

log = get_logger("import")

SECRET_FIELDS = ("token", "test_key", "gre_key")
DROP_FIELDS = ("quality", "status", "last_health_check", "last_connection", "last_restart", "latency_ms",
               "measured_at", "speed_mbps", "up_mbps", "score", "gre_test", "data_test")


def _servers(data) -> list[dict]:
    return data if isinstance(data, list) else list(data.get("servers", []))


def import_servers(path: Path, role: str) -> tuple[int, int]:
    added = updated = 0

    with session() as db:
        for rec in _servers(json.loads(path.read_text(encoding="utf-8"))):
            host = str(rec.get("host", "")).strip()
            name = str(rec.get("name", "")).strip() or host

            if not host:
                continue

            srv = db.scalar(select(Server).where(Server.host == host))

            if srv is None:
                srv = Server(name=name, host=host, role=role)
                db.add(srv)
                added += 1
            else:
                updated += 1

            srv.ssh_user = str(rec.get("ssh_user") or "root")
            srv.ssh_port = int(rec.get("ssh_port") or 22)
            srv.provider = str(rec.get("provider") or "")[:128]
            srv.asn = str(rec.get("asn") or "")[:32]
            srv.country = str(rec.get("country_code") or "")[:8]

        emit(db, "info", "system", f"Imported {added + updated} {role} servers ({added} new).")

    return added, updated


def import_tunnels(path: Path) -> tuple[int, int, list[str]]:
    added = updated = 0
    skipped: list[str] = []

    with session() as db:
        hosts = {s.host: s for s in db.scalars(select(Server))}

        for rec in json.loads(path.read_text(encoding="utf-8")).get("tunnels", []):
            name = str(rec.get("name", "")).strip()
            iran, foreign = hosts.get(str(rec.get("iran_host", ""))), hosts.get(str(rec.get("foreign_host", "")))

            if not name or iran is None or foreign is None:
                skipped.append(name or "?")
                continue

            t = db.scalar(select(Tunnel).where(Tunnel.name == name))

            if t is None:
                t = Tunnel(name=name, type=str(rec.get("type") or "backhaul"), iran_server_id=iran.id, foreign_server_id=foreign.id)
                db.add(t)
                added += 1
            else:
                updated += 1

            t.type = str(rec.get("type") or "backhaul")
            t.transport = str(rec.get("transport") or "")
            t.engine = str(rec.get("engine") or "")
            t.iran_server_id, t.foreign_server_id = iran.id, foreign.id
            t.port = int(rec.get("port") or 0)
            t.iran_tunnel_ip = str(rec.get("iran_tunnel_ip") or "")
            t.foreign_tunnel_ip = str(rec.get("foreign_tunnel_ip") or "")
            t.forwarded_ports = [int(p) for p in (rec.get("forwarded_ports") or []) if str(p).isdigit()]
            t.secrets = {k: rec[k] for k in SECRET_FIELDS if rec.get(k)}
            t.record = {k: v for k, v in rec.items() if k not in SECRET_FIELDS and k not in DROP_FIELDS}

            if rec.get("created_at"):
                t.created_at = int(rec["created_at"])

        emit(db, "info", "system", f"Imported {added + updated} tunnels ({added} new, {len(skipped)} skipped).")

    log.info("tunnels imported added=%s updated=%s skipped=%s", added, updated, skipped)
    return added, updated, skipped


def import_seed_dir(seed: Path) -> dict:
    out = {}

    if (seed / "iran-servers.json").exists():
        out["iran"] = import_servers(seed / "iran-servers.json", "iran")

    if (seed / "foreign-servers.json").exists():
        out["foreign"] = import_servers(seed / "foreign-servers.json", "foreign")

    if (seed / "tunnels.json").exists():
        out["tunnels"] = import_tunnels(seed / "tunnels.json")

    return out
