"""Tunnel test: before you commit to a pair of servers, build a temporary tunnel between them, measure
it (latency, jitter, loss over GRE; real download speed through a forwarded port) and remove it again.
Nothing stays on the servers; the result is kept in action_log."""
from __future__ import annotations

import re
import threading
import time

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import ActionLog, Tunnel
from ..ssh import Node, SSHError, run_action, run_script
from . import builder

log = get_logger("tunnel-test")
TEST_PORT = 39400          # forwarded port used by the speed test (listen on Iran, sink on foreign)


def jitter_from_pings(out: str) -> tuple[float | None, float | None, float | None]:
    """(avg ms, jitter ms, loss %) from `ping` output with per-packet times."""
    times = [float(x) for x in re.findall(r"time=([\d.]+)", str(out))]
    m = re.search(r"(\d+) packets transmitted, (\d+) received", str(out))
    loss = round(100.0 - int(m.group(2)) * 100.0 / int(m.group(1)), 1) if m and int(m.group(1)) else None

    if not times:
        return None, None, loss

    avg = sum(times) / len(times)
    jitter = sum(abs(t - avg) for t in times) / len(times)
    return round(avg, 1), round(jitter, 1), loss


def score(avg: float | None, jitter: float | None, loss: float | None, mbps: float | None) -> int:
    """0-100: speed counts most, then latency, loss and jitter."""
    if avg is None:
        return 0

    sp = min(100.0, (mbps or 0) / 8.0)                 # 800 Mbps = full marks
    lat = max(0.0, 100.0 - max(0.0, avg - 40) / 2.0)
    jit = max(0.0, 100.0 - (jitter or 0) * 5)
    lo = max(0.0, 100.0 - (loss or 0) * 10)
    return round(sp * 0.40 + lat * 0.25 + jit * 0.10 + lo * 0.25)


def start(iran_id: int, foreign_id: int, transport: str = "tcpmux") -> int:
    """Validate and start; returns the action id of the test (the build has its own id in the story)."""
    with session() as db:
        names = {t.name for t in db.scalars(select(Tunnel))}

    n = 1

    while f"nkt{n}" in names:
        n += 1

    name = f"nkt{n}"
    build_aid = builder.start_build(iran_id, foreign_id, str(TEST_PORT), transport, forced_name=name)

    with session() as db:
        entry = ActionLog(ref=name, action="tunnel_test", source="manual", story=[f"Temporary {transport} tunnel {name} is being built (build #{build_aid})…"])
        db.add(entry)
        db.flush()
        aid = entry.id

    threading.Thread(target=_worker, args=(aid, build_aid, name), name=f"tunnel-test-{name}", daemon=True).start()
    return aid


def _say(aid: int, line: str, done: bool | None = None, ok: bool | None = None) -> None:
    with session() as db:
        e = db.get(ActionLog, aid)

        if e is None:
            return

        e.story = list(e.story or []) + [line]

        if done:
            e.ok = ok
            e.finished_at = int(time.time())

    log.info("tunnel test #%s: %s", aid, line)


def _worker(aid: int, build_aid: int, name: str) -> None:
    """Measure, then remove, then mark the test as done - so 'done' always means 'nothing is left behind'."""
    ok: bool | None = False
    verdict = ""

    try:
        ok, verdict = _run(aid, build_aid, name)
    except Exception as e:  # thread boundary
        log.exception("tunnel test #%s crashed", aid)
        verdict = f"Stopped: {str(e)[:160]}"
        ok = False
    finally:
        _cleanup(aid, name)
        _say(aid, verdict or "Finished.", done=True, ok=ok)


def _cleanup(aid: int, name: str) -> None:
    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == name))
        tid = t.id if t else None

    if tid is not None:
        try:
            res = builder.delete_tunnel(tid)
            _say(aid, "Removed the temporary tunnel: " + "; ".join(res))
        except (ValueError, SSHError) as e:
            _say(aid, f"Could not remove the temporary tunnel cleanly: {e}")


def _run(aid: int, build_aid: int, name: str) -> tuple[bool, str]:
    # wait for the build
    for _ in range(200):
        time.sleep(3)

        with session() as db:
            b = db.get(ActionLog, build_aid)

            if b is not None and b.finished_at:
                ok, story = b.ok, list(b.story or [])
                break
    else:
        raise RuntimeError("the build took too long")

    if not ok:
        return False, "Build failed: " + (story[-1] if story else "unknown reason")

    _say(aid, "Built. " + next((s for s in story if s.startswith("Path MTU")), ""))

    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == name))
        iran, foreign = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port)
        peer = t.foreign_tunnel_ip

    out = run_script(iran, "gre_pings", {"IFACE": name, "PEER": peer}, timeout=45)
    avg, jitter, loss = jitter_from_pings(out)
    _say(aid, f"GRE: latency {avg} ms, jitter {jitter} ms, loss {loss}% (30 pings)")

    mbps = None

    try:
        run_action(foreign, "speed_sink", {"PORT": str(TEST_PORT)})
        time.sleep(1)
        pulled = run_script(iran, "speed_pull", {"PORT": str(TEST_PORT)}, timeout=40).strip()
        m = re.search(r"SPEED=([\d.]+)", pulled)

        if m and float(m.group(1)) > 0:
            mbps = round(float(m.group(1)) * 8 / 1e6, 1)
    except (SSHError, ValueError) as e:
        _say(aid, f"Speed test skipped: {e}")

    _say(aid, f"Download through the tunnel (foreign → Iran): {mbps if mbps is not None else 'not measured'} Mbps")
    sc = score(avg, jitter, loss, mbps)
    verdict = "excellent" if sc >= 75 else "usable" if sc >= 50 else "poor"
    return sc >= 50, f"Score {sc}/100 — {verdict}. Everything temporary was removed."
