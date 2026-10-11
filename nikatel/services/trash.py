"""A removed tunnel can be put back. Before the panel removes a tunnel on request (Delete, or "replace" in Create tunnel) it keeps a copy of what is needed to build it again:
the pair of servers, the name, the ports, the type and its options. "Restore" builds it again from that copy (a new GRE key; the services start by themselves). Copies are kept
for 30 days and at most 30 of them; one whose name exists as a tunnel again is dropped by itself."""
from __future__ import annotations

import time

from sqlalchemy import select

from ..db import session
from ..models import Server, Tunnel
from . import settings as st

TTL = 30 * 86400
MAX = 30
OTHER = ("backpack", "paqet", "chisel")


def snapshot(t: Tunnel) -> dict:
    """Pure-ish: the copy of a tunnel (read from the row, while the session is open)."""
    rec = dict(t.record or {})
    typ, transport = (t.type or "").lower(), t.transport or "tcpmux"
    ports = rec.get("port_mapping") or ",".join(str(p) for p in (t.forwarded_ports or []))
    other = typ in OTHER
    return {"name": t.name, "title": t.name, "sub": f"{t.iran.name} ⇄ {t.foreign.name}", "ports": ports, "type": typ,
            "args": {"iran_id": t.iran_server_id, "foreign_id": t.foreign_server_id, "ports_text": ports, "other": other,
                     "transport": transport.replace("l3:", "") if other else transport, "kind": rec.get("kind") or ("backpack_direct" if transport.startswith("l3:") else typ),
                     "mtu": int(rec.get("mtu") or 0), "keepalive": int(rec.get("keepalive") or 75), "nodelay": bool(rec.get("nodelay", True)), "opts": dict(rec.get("opts") or {})}}


def add(snap: dict) -> None:
    now = int(time.time())
    items = [i for i in st.get("trash").get("items", []) if now - i.get("at", 0) < TTL and i.get("name") != snap["name"]]
    items.append({**snap, "id": f"{now}-{snap['name']}", "at": now})
    st.put("trash", {"items": items[-MAX:]})


def items() -> list[dict]:
    """The copies that can still be restored, newest first (a copy whose tunnel exists again is forgotten)."""
    now = int(time.time())

    with session() as db:
        existing = {t.name for t in db.scalars(select(Tunnel))}

    keep = [i for i in st.get("trash").get("items", []) if now - i.get("at", 0) < TTL and i.get("name") not in existing]

    if len(keep) != len(st.get("trash").get("items", [])):
        st.put("trash", {"items": keep})

    return sorted(keep, key=lambda i: -i.get("at", 0))


def forget(item_id: str) -> None:
    st.put("trash", {"items": [i for i in st.get("trash").get("items", []) if i.get("id") != item_id]})


def restore(item_id: str) -> int:
    """Build the tunnel again from its copy. Returns the build's action id. The copy stays until the tunnel exists."""
    from . import build_other, builder

    item = next((i for i in items() if i.get("id") == item_id), None)

    if item is None:
        raise ValueError("this copy is not there any more (restored already, forgotten, or older than 30 days)")

    a = item["args"]

    with session() as db:
        if db.scalar(select(Tunnel).where(Tunnel.name == item["name"])):
            raise ValueError(f"a tunnel named {item['name']} exists already")

        for sid in (a["iran_id"], a["foreign_id"]):
            s = db.get(Server, sid)

            if s is None or not s.enabled:
                raise ValueError("one of its servers is not in the panel any more or is disabled")

    if not a["ports_text"]:
        raise ValueError("the copy has no ports")

    if a["other"]:
        return build_other.start_build(a["iran_id"], a["foreign_id"], a["ports_text"], a["kind"], a["transport"], a["opts"], forced_name=item["name"])

    return builder.start_build(a["iran_id"], a["foreign_id"], a["ports_text"], a["transport"], a["mtu"], a["keepalive"], forced_name=item["name"], nodelay=a["nodelay"], opts=a["opts"])