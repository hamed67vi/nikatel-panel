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

    windows = []

    for key in ("CPU", "CPU2", "CPU3"):
        try:
            t1, i1, t2, i2 = nums(key, 4)
        except (ValueError, KeyError):
            continue

        if t2 - t1 > 0:
            windows.append(max(0.0, min(100.0, ((t2 - t1) - (i2 - i1)) * 100.0 / (t2 - t1))))

    windows.sort()
    cpu = windows[len(windows) // 2] if windows else 0.0      # the MEDIAN of the one-second windows: a burst that happens to fall into one second does not read as a full server
    rx1, tx1, rx2, tx2 = nums("NET", 4)
    netdt = max(1.0, float(kv.get("NETDT", "1").split()[0] or 1)) if kv.get("NETDT") else 1.0
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
        "swap_used_mb": int(max(0.0, st - sf) / 1024),
        "swap_total_mb": int(st / 1024),
        "disk": round(dsk_used * 100.0 / dsk_total, 1) if dsk_total else 0.0,
        "disk_used_gb": round(dsk_used / 1048576.0, 1),
        "disk_total_gb": round(dsk_total / 1048576.0, 1),
        "load1": l1, "load5": l5, "load15": l15,
        "cores": int(cores) or 1,
        "uptime": int(up),
        "rx_mbps": round(max(0.0, rx2 - rx1) * 8 / 1e6 / netdt, 2),
        "tx_mbps": round(max(0.0, tx2 - tx1) * 8 / 1e6 / netdt, 2),
        "rx_bytes": int(rx2), "tx_bytes": int(tx2),      # everything the card of the default route has carried since the server started (the "Traffic used" column of the Lab)
        "conn": conn,
        "hostname": host[0] if host else "",
        "kernel": host[1] if len(host) > 1 else "",
        "top": f"{top[0]} {float(top[-1]):.0f}%" if len(top) >= 2 else "",      # busiest process
        "abuse_rules": int(abuse) if abuse.isdigit() else 0,                   # Abuse-Defender DROP rules (0 = not installed)
    }


def _wake_tunnels() -> None:
    """A server went off or came back: the tunnels that are not up are looked at now (the fast lane), not at the next turn of the slow cycle."""
    from .. import jobs

    try:
        jobs.trigger("tunnels-fast")
    except KeyError:
        pass


REBOOT_WAIT = 180          # seconds: a server that still answers with its old uptime this long after "Reboot" did not restart (the command failed): the mark goes


def _rebooting(srv) -> bool:
    return (((srv.extra or {}).get("busy") or {}).get("what")) == "Rebooting"


def _reboot_done(srv, facts: dict) -> None:
    """The "Rebooting" mark of a row goes when the server answers and its uptime is shorter than the time since the restart was asked (it has started again), or when it still answers with its old uptime
    long after (the command did not restart it). A server that answers in the first seconds, before it goes down, keeps the mark."""
    b = ((srv.extra or {}).get("busy") or {})

    if b.get("what") != "Rebooting":
        return

    since = time.time() - (b.get("at") or 0)

    if since > REBOOT_WAIT or float(facts.get("uptime") or 0) < since + 30:
        ex = dict(srv.extra or {})
        ex.pop("busy", None)
        srv.extra = ex


def sample_server(server_id: int) -> None:
    """One server: ssh, parse, store. Errors become the server's last_error, never an exception.

    The database is NOT held while the server is asked (up to 35 s): the connection goes back to the pool at once, so a slow server never holds one of the few connections the other jobs and the pages need (review 2026-10-16)."""
    with session() as db:
        srv = db.get(Server, server_id)

        if srv is None or not srv.enabled:
            return

        node, name, host = Node(srv.host, srv.ssh_user, srv.ssh_port), srv.name, srv.host

    facts, failure = None, None

    try:
        facts = parse_facts(run_script(node, "facts", timeout=35))
    except (SSHError, ValueError) as e:
        failure = e

    with session() as db:
        srv = db.get(Server, server_id)

        if srv is None or not srv.enabled:          # (removed or switched off while it was being asked)
            return

        was_online = srv.online          # what it is NOW: pulse may have changed it meanwhile, and an event is written only for a real change

        if failure is not None:
            e = failure
            log.warning("sample failed name=%s host=%s err=%s", name, host, e)
            from . import pulse

            if pulse.tcp_ok_recently(srv.id) and "key is not accepted" not in str(e) and "host key" not in str(e):
                # the SSH port answers: the server is ON. Only the SSH session fails (a stalled path, a busy sshd) - it is not "offline"; the reason stays visible
                srv.last_error = ("SSH does not answer, the server is up (its SSH port answers): " + str(e))[:300]

                if was_online is not True:
                    srv.online = True
                    emit(db, "warn", "servers", f"Server {srv.name} is up (its SSH port answers); only its SSH session fails: {e}", srv.name)
                    _wake_tunnels()

                return

            srv.online = False
            srv.last_error = str(e)[:300]

            if was_online and not _rebooting(srv):          # (a restart asked from the panel is no alarm: its row says "Rebooting")
                emit(db, "bad", "servers", f"Server {srv.name} does not answer: {e}", srv.name)
                _wake_tunnels()

            return

        srv.online = True
        srv.last_seen_at = facts["ts"]
        srv.last_error = ""
        srv.facts = facts
        _reboot_done(srv, facts)
        db.add(ServerSample(server_id=srv.id, ts=facts["ts"], cpu=facts["cpu"], ram=facts["ram"], disk=facts["disk"],
                            load1=facts["load1"], rx_mbps=facts["rx_mbps"], tx_mbps=facts["tx_mbps"], conn=facts["conn"]))

        if was_online is False:
            emit(db, "ok", "servers", f"Server {srv.name} answers again.", srv.name)
            _wake_tunnels()

        log.info("sample ok name=%s cpu=%s ram=%s disk=%s", srv.name, facts["cpu"], facts["ram"], facts["disk"])


def sample_all() -> None:
    with session() as db:
        ids = list(db.scalars(select(Server.id).where(Server.enabled.is_(True))))

    with ThreadPoolExecutor(get_settings().ssh_workers) as ex:
        list(ex.map(sample_server, ids))

    with session() as db:
        db.execute(delete(ServerSample).where(ServerSample.ts < int(time.time()) - KEEP_SAMPLES_SECONDS))
