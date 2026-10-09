"""Server facts: run the `facts` script, parse it, store a sample. Pure parsing is separate so it
can be tested without a server."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete, select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, ServerSample
from ..ssh import Node, SSHError, run_script
from .events import emit

log = get_logger("servers")

KEEP_SAMPLES_SECONDS = 7 * 86400


def parse_facts(out: str) -> dict:
    """Turn the script output into numbers. Raises ValueError on an incomplete answer."""
    kv: dict[str, str] = {}

    for line in str(out).splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            kv[k.strip()] = v.strip()

    def nums(key: str, n: int) -> list[float]:
        parts = kv.get(key, "").split()

        if len(parts) < n:
            raise ValueError(f"no {key} in the answer")

        return [float(x) for x in parts[:n]]

    t1, i1, t2, i2 = nums("CPU", 4)
    dt = t2 - t1
    cpu = max(0.0, min(100.0, (dt - (i2 - i1)) * 100.0 / dt)) if dt > 0 else 0.0
    rx1, tx1, rx2, tx2 = nums("NET", 4)
    mt, ma, st, sf = nums("MEM", 4)
    dsk_total, dsk_used = nums("DISK", 2)
    l1, l5, l15, cores = nums("LOAD", 4)
    up = nums("UP", 1)[0]
    conn = int(float(kv.get("CONN", "0").split()[0] or 0)) if kv.get("CONN", "").strip() else 0
    host = kv.get("HOST", "").split()
    ram_used = max(0.0, mt - ma)
    top = kv.get("TOP", "").split()
    abuse = kv.get("ABUSE", "").strip()

    return {
        "ts": int(time.time()),
        "cpu": round(cpu, 1),
        "ram": round(ram_used * 100.0 / mt, 1) if mt else 0.0,
        "ram_used_mb": int(ram_used / 1024),
        "ram_total_mb": int(mt / 1024),
        "swap": round((st - sf) * 100.0 / st, 1) if st else 0.0,
        "disk": round(dsk_used * 100.0 / dsk_total, 1) if dsk_total else 0.0,
        "disk_used_gb": round(dsk_used / 1048576.0, 1),
        "disk_total_gb": round(dsk_total / 1048576.0, 1),
        "load1": l1, "load5": l5, "load15": l15,
        "cores": int(cores) or 1,
        "uptime": int(up),
        "rx_mbps": round(max(0.0, rx2 - rx1) * 8 / 1e6, 2),
        "tx_mbps": round(max(0.0, tx2 - tx1) * 8 / 1e6, 2),
        "conn": conn,
        "hostname": host[0] if host else "",
        "kernel": host[1] if len(host) > 1 else "",
        "top": f"{top[0]} {float(top[-1]):.0f}%" if len(top) >= 2 else "",      # busiest process
        "abuse_rules": int(abuse) if abuse.isdigit() else 0,                   # Abuse-Defender DROP rules (0 = not installed)
    }


def sample_server(server_id: int) -> None:
    """One server: ssh, parse, store. Errors become the server's last_error, never an exception."""
    with session() as db:
        srv = db.get(Server, server_id)

        if srv is None or not srv.enabled:
            return

        node = Node(srv.host, srv.ssh_user, srv.ssh_port)
        was_online = srv.online

        try:
            facts = parse_facts(run_script(node, "facts", timeout=35))
        except (SSHError, ValueError) as e:
            srv.online = False
            srv.last_error = str(e)[:300]
            log.warning("sample failed name=%s host=%s err=%s", srv.name, srv.host, e)

            if was_online:
                emit(db, "bad", "servers", f"Server {srv.name} does not answer: {e}", srv.name)

            return

        srv.online = True
        srv.last_seen_at = facts["ts"]
        srv.last_error = ""
        srv.facts = facts
        db.add(ServerSample(server_id=srv.id, ts=facts["ts"], cpu=facts["cpu"], ram=facts["ram"], disk=facts["disk"],
                            load1=facts["load1"], rx_mbps=facts["rx_mbps"], tx_mbps=facts["tx_mbps"], conn=facts["conn"]))

        if was_online is False:
            emit(db, "ok", "servers", f"Server {srv.name} answers again.", srv.name)

        log.info("sample ok name=%s cpu=%s ram=%s disk=%s", srv.name, facts["cpu"], facts["ram"], facts["disk"])


def sample_all() -> None:
    with session() as db:
        ids = list(db.scalars(select(Server.id).where(Server.enabled.is_(True))))

    with ThreadPoolExecutor(get_settings().ssh_workers) as ex:
        list(ex.map(sample_server, ids))

    with session() as db:
        db.execute(delete(ServerSample).where(ServerSample.ts < int(time.time()) - KEEP_SAMPLES_SECONDS))
