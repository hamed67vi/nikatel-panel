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


def quality(tunnel_id: int) -> dict:
    """Latency / jitter / loss over the GRE link of an EXISTING tunnel (30 pings from the Iran side) and a
    score. The speed is not measured here: that needs the temporary tunnel of the pair test, or a dedicated
    test port. Stored in the tunnel record and shown in the list."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        has_door = bool((t.record or {}).get("test_port") and (t.secrets or {}).get("test_key"))

        if not has_door and (not t.foreign_tunnel_ip or t.type not in ("backhaul", "gre")):
            raise ValueError("this tunnel has no GRE link or test door to measure")

        node, name, peer = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), t.name, t.foreign_tunnel_ip

    if has_door:   # BackPack / Paqet / Chisel: through the real tunnel, speed included
        from . import build_other

        return build_other.quality(tunnel_id)

    out = run_script(node, "gre_pings", {"IFACE": name, "PEER": peer}, timeout=45)
    avg, jit, loss = jitter_from_pings(out)
    q = {"latency_ms": avg, "jitter_ms": jit, "loss_pct": loss, "score": score(avg, jit, loss, None), "at": int(time.time())}

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            rec["quality"] = q
            t.record = rec

    return {"name": name, **q}


def quality_all() -> list[dict]:
    with session() as db:
        ids = [t.id for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))]

    out = []

    for tid in ids:
        try:
            out.append(quality(tid))
        except (ValueError, SSHError) as e:
            out.append({"id": tid, "error": str(e)[:120]})

    return out


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
    RESULTS[aid] = {"latency_ms": avg, "jitter_ms": jitter, "loss_pct": loss, "down_mbps": mbps, "score": sc}
    verdict = "excellent" if sc >= 75 else "usable" if sc >= 50 else "poor"
    return sc >= 50, f"Score {sc}/100 — {verdict}. Everything temporary was removed."


# ------------------------------------------------------------------ compare several transports, one after the other
RESULTS: dict[int, dict] = {}
# key, label, note, group, ticked by default
KINDS = [
    ("tcpmux", "Backhaul · TCP Mux", "over GRE, many users over few connections (recommended)", "Backhaul over GRE", True),
    ("tcp", "Backhaul · TCP", "over GRE, plain TCP, one connection per user", "Backhaul over GRE", True),
    ("wsmux", "Backhaul · WebSocket Mux", "over GRE, clear-text WebSocket, multiplexed", "Backhaul over GRE", False),
    ("wssmux", "Backhaul · WebSocket TLS Mux", "over GRE, TLS WebSocket, multiplexed", "Backhaul over GRE", False),
    ("bp:tcpmux", "BackPack · TCP Mux", "many users over few connections", "BackPack", True),
    ("bp:tcp", "BackPack · TCP", "plain TCP", "BackPack", True),
    ("bp:stealth", "BackPack · TCP + Stealth", "looks like random bytes", "BackPack", True),
    ("bp:wss", "BackPack · WebSocket TLS", "TLS + decoy page", "BackPack", False),
    ("bp:wssmux", "BackPack · WebSocket TLS Mux", "TLS WebSocket, multiplexed", "BackPack", True),
    ("bp:ws", "BackPack · WebSocket", "clear-text WebSocket", "BackPack", False),
    ("bp:wsmux", "BackPack · WebSocket Mux", "clear-text, multiplexed", "BackPack", False),
    ("bp:kcp", "BackPack · UDP + KCP", "UDP must be open", "BackPack", False),
    ("bp:quic", "BackPack · UDP + QUIC", "UDP must be open", "BackPack", False),
    ("bp:pck", "BackPack · TCP + PCK", "raw TCP segments (root)", "BackPack", False),
    ("bp:xdi", "BackPack · xDi (ICMP)", "slow, last resort", "BackPack", False),
    ("bpd:udp", "BackPack Direct · UDP", "Iran dials, UDP", "BackPack Direct", True),
    ("bpd:quic", "BackPack Direct · QUIC", "Iran dials, QUIC", "BackPack Direct", False),
    ("bpd:pck", "BackPack Direct · PCK", "Iran dials, looks like TCP (root)", "BackPack Direct", True),
    ("bpd:xdi", "BackPack Direct · xDi (ICMP)", "Iran dials, inside ping (root)", "BackPack Direct", False),
    ("paqet", "Paqet · KCP raw sockets", "needs raw sockets", "Paqet", True),
    ("chisel", "Chisel · WebSocket", "HTTP WebSocket, one inbound port", "Chisel", True),
    ("chisel:tls", "Chisel · TLS WebSocket", "TLS WebSocket, one inbound port", "Chisel", False),
]
_COMPARE: dict[str, dict] = {}


def kind_parts(kind: str) -> tuple[str, str]:
    """A test kind -> (builder kind, transport). Backhaul transports keep their bare key."""
    if kind in ("tcpmux", "tcp", "wsmux", "wssmux"):
        return "backhaul", kind

    if kind.startswith("bp:"):
        return "backpack", kind[3:]

    if kind.startswith("bpd:"):
        return "backpack_direct", kind[4:]

    if kind == "paqet":
        return "paqet", "kcp"

    return "chisel", "wss" if kind == "chisel:tls" else "ws"


def start_other(iran_id: int, foreign_id: int, kind: str) -> int:
    """A temporary BackPack / Paqet / Chisel tunnel: built (which measures it), then removed. Returns the test's action id."""
    from . import build_other

    k, tr = kind_parts(kind)

    with session() as db:
        names = {t.name for t in db.scalars(select(Tunnel))}

    n = 1

    while f"nkt{n}" in names:
        n += 1

    name = f"nkt{n}"
    build_aid = build_other.start_build(iran_id, foreign_id, str(TEST_PORT), k, tr, {}, forced_name=name)

    with session() as db:
        entry = ActionLog(ref=name, action="tunnel_test", source="manual", story=[f"Temporary {kind} tunnel {name} is being built (build #{build_aid})…"])
        db.add(entry)
        db.flush()
        aid = entry.id

    threading.Thread(target=_other_worker, args=(aid, build_aid, name), name=f"tunnel-test-{name}", daemon=True).start()
    return aid


def _other_worker(aid: int, build_aid: int, name: str) -> None:
    ok: bool | None = False
    verdict = ""

    try:
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
            verdict = "Build failed: " + (story[-1] if story else "unknown reason")
        else:
            with session() as db:
                t = db.scalar(select(Tunnel).where(Tunnel.name == name))
                q = dict((t.record or {}).get("quality") or {}) if t else {}

            sc = int(q.get("score") or 0)
            RESULTS[aid] = {"latency_ms": q.get("latency_ms"), "jitter_ms": q.get("jitter_ms"), "loss_pct": q.get("loss_pct"), "down_mbps": q.get("down_mbps"), "up_mbps": q.get("up_mbps"), "score": sc}
            _say(aid, f"Built and measured: {q.get('latency_ms')} ms, jitter {q.get('jitter_ms')} ms, loss {q.get('loss_pct')}%, ↓{q.get('down_mbps')} ↑{q.get('up_mbps')} Mb/s")
            ok = sc >= 50
            verdict = f"Score {sc}/100 — {'excellent' if sc >= 75 else 'usable' if sc >= 50 else 'poor'}. Everything temporary was removed."
    except Exception as e:  # thread boundary
        log.exception("tunnel test #%s crashed", aid)
        verdict, ok = f"Stopped: {str(e)[:160]}", False
    finally:
        _cleanup(aid, name)
        _say(aid, verdict or "Finished.", done=True, ok=ok)


def compare_start(iran_id: int, foreign_id: int, kinds: list[str]) -> str:
    kinds = [k for k in kinds if k in {k_[0] for k_ in KINDS}] or ["tcpmux"]

    if any(not j["done"] for j in _COMPARE.values()):
        raise RuntimeError("another tunnel test is still running")

    jid = f"cmp{int(time.time())}"
    job = {"id": jid, "iran_id": iran_id, "foreign_id": foreign_id, "kinds": kinds, "results": [], "current": "", "current_aid": None, "done": False, "stop": False, "started": int(time.time())}
    _COMPARE[jid] = job

    for old in [k for k, v in _COMPARE.items() if v["done"] and time.time() - v["started"] > 7200]:
        _COMPARE.pop(old, None)

    threading.Thread(target=_compare_worker, args=(job,), name=f"tunnel-compare-{jid}", daemon=True).start()
    return jid


def _compare_worker(job: dict) -> None:
    try:
        for kind in job["kinds"]:
            if job["stop"]:
                break

            job["current"] = kind

            try:
                aid = start(job["iran_id"], job["foreign_id"], kind) if kind_parts(kind)[0] == "backhaul" else start_other(job["iran_id"], job["foreign_id"], kind)
            except (ValueError, RuntimeError) as e:
                job["results"].append({"kind": kind, "ok": False, "error": str(e)[:160]})
                continue

            job["current_aid"] = aid

            for _ in range(400):
                time.sleep(3)

                with session() as db:
                    e = db.get(ActionLog, aid)

                    if e is not None and e.finished_at:
                        ok, story = bool(e.ok), list(e.story or [])
                        break
            else:
                ok, story = False, ["the test took too long"]

            r = dict(RESULTS.get(aid) or {})
            r.update(kind=kind, ok=ok, aid=aid, verdict=story[-1] if story else "")
            job["results"].append(r)
    finally:
        job["current"], job["current_aid"], job["done"] = "", None, True


def compare_status(jid: str) -> dict:
    job = _COMPARE.get(jid)

    if job is None:
        raise ValueError("unknown test")

    story: list = []

    if job["current_aid"]:
        with session() as db:
            e = db.get(ActionLog, job["current_aid"])
            story = list(e.story or []) if e else []

    return {k: v for k, v in job.items() if k != "stop"} | {"story": story, "stopping": job["stop"] and not job["done"]}


def compare_stop(jid: str) -> None:
    job = _COMPARE.get(jid)

    if job is None:
        raise ValueError("unknown test")

    job["stop"] = True
