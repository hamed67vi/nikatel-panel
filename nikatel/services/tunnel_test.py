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
from ..models import ActionLog, Server, Tunnel
from ..ssh import Node, SSHError, run_action, run_script
from . import builder
from .tunnels import link_of

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

        iface, peer = link_of(t)

        if not has_door and (not peer or t.type not in ("backhaul", "gre")):
            raise ValueError("this tunnel has no GRE link or test door to measure")

        node, name = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), t.name

    if has_door:   # BackPack / Paqet / Chisel: through the real tunnel, speed included
        from . import build_other

        return build_other.quality(tunnel_id)

    out = run_script(node, "gre_pings", {"IFACE": iface, "PEER": peer}, timeout=45)
    avg, jit, loss = jitter_from_pings(out)

    if not iface and (loss is None or loss >= 100):   # no GRE link of its own: the ping goes to the foreign server's public address, and that may simply ignore ping
        raise ValueError("the foreign server does not answer ping from the Iran server (ICMP is filtered); this tunnel has no GRE link to measure, its data path is checked by the health check instead")
    q = {"latency_ms": avg, "jitter_ms": jit, "loss_pct": loss, "score": score(avg, jit, loss, None), "at": int(time.time())}

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            rec["quality"] = q
            t.record = rec

    return {"name": name, **q}


def quality_all_start(source: str = "manual") -> int:
    """The quality of every tunnel (10 s of pings each, one after the other) as an action. Read only."""
    from . import autopilot

    def work(aid: int) -> None:
        out = quality_all()

        for r in out:
            _say(aid, f"{r.get(chr(110) + chr(97) + chr(109) + chr(101), r.get(chr(105) + chr(100)))}: " + (("error: " + r["error"]) if r.get("error") else f"score {r.get(chr(115) + chr(99) + chr(111) + chr(114) + chr(101))}/100"))

        good = sum(1 for r in out if not r.get("error"))
        _say(aid, f"Measured {good} of {len(out)} tunnel" + ("s" if len(out) != 1 else "") + ".", done=True, ok=good == len(out))

    return autopilot._start("quality of all tunnels", work, action="quality", source=source, allow_read_only=True)


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


LOSS_HIGH = 10.0           # % packet loss users feel (video stalls, slow pages)
LOSS_SOME = 3.0


def loss_level(loss: float | None) -> str:
    """Pure: how bad a packet loss is for the users. Shown only - nothing is repaired because of it."""
    if loss is None:
        return ""

    return "high" if loss >= LOSS_HIGH else "some" if loss >= LOSS_SOME else ""


def full_check() -> dict:
    """One button for everything: every tunnel is checked for real (are both sides up, does data pass), then its packet loss and
    latency are measured; the answer lists up / degraded / down and flags a loss that users will feel. Display only."""
    from concurrent.futures import ThreadPoolExecutor

    from . import actions

    states = {r["id"]: r for r in actions.health_all()}

    with session() as db:
        info = {t.id: (t.iran.name, t.foreign.name, dict(t.detail or {})) for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))}

    def one(tid: int) -> dict:
        try:
            return quality(tid)
        except (ValueError, SSHError) as e:
            return {"error": str(e)[:120]}

    with ThreadPoolExecutor(6) as ex:
        measured = dict(zip(info, ex.map(one, list(info))))

    rows = []

    for tid, (iran, foreign, det) in info.items():
        q, st_ = measured.get(tid, {}), states.get(tid, {})
        loss = q.get("loss_pct") if q.get("loss_pct") is not None else det.get("loss_pct")
        rows.append({"id": tid, "name": st_.get("name", str(tid)), "iran": iran, "foreign": foreign, "state": st_.get("state", "unknown"), "why": st_.get("why", ""),
                     "latency_ms": q.get("latency_ms"), "jitter_ms": q.get("jitter_ms"), "loss_pct": loss, "loss": loss_level(loss), "measured": "error" not in q})

    order = {"down": 0, "unknown": 1, "degraded": 2, "up": 3}
    rows.sort(key=lambda r: (order.get(r["state"], 1), -(r["loss_pct"] or 0), r["name"]))
    return {"rows": rows, **{k: sum(1 for r in rows if r["state"] == k) for k in order}, "high_loss": sum(1 for r in rows if r["loss"] == "high"), "some_loss": sum(1 for r in rows if r["loss"] == "some")}


def purge_stale() -> None:
    """Job hook (every 10 minutes): a temporary test tunnel older than 40 minutes belongs to a test that is gone (the panel restarted in the middle of it):
    it is removed from both servers, so that nothing hidden is left behind."""
    from ..models import is_temp

    if any(not j["done"] for j in _COMPARE.values()):
        return

    with session() as db:
        old = [t.id for t in db.scalars(select(Tunnel)) if is_temp(t.name) and time.time() - (t.created_at or 0) > 2400]

    for tid in old:
        try:
            builder.delete_tunnel(tid)
            log.info("removed a stale temporary test tunnel (#%s)", tid)
        except (ValueError, SSHError) as e:
            log.warning("stale temporary tunnel #%s not removed: %s", tid, str(e)[:100])


def purge_leftovers(iran_id: int, foreign_id: int) -> None:
    """Before a test: whatever an earlier test that was cut short (a restart of the panel) left on the two servers is removed, so the test port is free again."""
    with session() as db:
        nodes = [Node(s.host, s.ssh_user, s.ssh_port) for s in (db.get(Server, iran_id), db.get(Server, foreign_id)) if s is not None]

    for node in nodes:
        try:
            out = run_action(node, "purge_test_leftovers", {"TESTPORT": str(TEST_PORT)}, timeout=60)

            if "PURGED 0" not in out:
                log.info("test leftovers removed on %s: %s", node.host, out.strip()[-40:])
        except (SSHError, ValueError) as e:
            log.warning("could not purge test leftovers on %s: %s", node.host, e)


def start(iran_id: int, foreign_id: int, transport: str = "tcpmux") -> int:
    """Validate and start; returns the action id of the test (the build has its own id in the story)."""
    purge_leftovers(iran_id, foreign_id)

    with session() as db:
        names = {t.name for t in db.scalars(select(Tunnel))}

    n = 1

    while f"test-{n}" in names:
        n += 1

    name = f"test-{n}"
    build_aid = builder.start_build(iran_id, foreign_id, str(TEST_PORT), transport, forced_name=name)

    with session() as db:
        entry = ActionLog(ref=name, action="tunnel_test", source="manual", story=[f"Temporary {transport} tunnel {name} is being built (build #{build_aid})…"])
        db.add(entry)
        db.flush()
        aid = entry.id

    BUILD_OF[aid] = build_aid

    threading.Thread(target=_worker, args=(aid, build_aid, name, iran_id, foreign_id), name=f"tunnel-test-{name}", daemon=True).start()
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


def _worker(aid: int, build_aid: int, name: str, iran_id: int = 0, foreign_id: int = 0) -> None:
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
        _cleanup(aid, name, iran_id, foreign_id)
        _say(aid, verdict or "Finished.", done=True, ok=ok)


def _cleanup(aid: int, name: str, iran_id: int = 0, foreign_id: int = 0) -> None:
    """Remove the temporary tunnel: the one with the test's name and - should the builder have given it another name - any tunnel
    of the pair that forwards only the test port."""
    with session() as db:
        ids = [t.id for t in db.scalars(select(Tunnel).where(Tunnel.name == name))]

        if iran_id and foreign_id:
            ids += [t.id for t in db.scalars(select(Tunnel).where(Tunnel.iran_server_id == iran_id, Tunnel.foreign_server_id == foreign_id))
                    if t.id not in ids and [int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()] == [TEST_PORT]]

    for tid in ids:
        try:
            res = builder.delete_tunnel(tid)
            _say(aid, "Removed the temporary tunnel: " + "; ".join(res))
        except (ValueError, SSHError) as e:
            _say(aid, f"Could not remove the temporary tunnel cleanly: {e}")


def _run(aid: int, build_aid: int, name: str) -> tuple[bool, str]:
    # wait for the build
    seen = 0

    for _ in range(200):
        time.sleep(3)

        with session() as db:
            b = db.get(ActionLog, build_aid)
            lines, fin, bok = (list(b.story or []), bool(b.finished_at), b.ok) if b is not None else ([], False, None)

        for ln in lines[seen:]:               # the steps of the build, as they happen: the operator sees what the test is doing
            _say(aid, ln)

        seen = len(lines)

        if fin:
            ok, story = bok, lines
            break
    else:
        raise RuntimeError("the build took too long")

    if not ok:
        return False, "Build failed: " + (story[-1] if story else "unknown reason")

    if build_aid in builder.CANCEL:
        return False, "Stopped by you."

    _say(aid, "Built; measuring…")

    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == name))
        iran, foreign = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port)
        peer = t.foreign_tunnel_ip

    out = run_script(iran, "gre_pings", {"IFACE": name, "PEER": peer}, timeout=45)
    avg, jitter, loss = jitter_from_pings(out)
    _say(aid, f"GRE: latency {avg} ms, jitter {jitter} ms, loss {loss}% (30 pings)")

    mbps = None

    if build_aid in builder.CANCEL:
        return False, "Stopped by you."

    try:
        run_action(foreign, "speed_sink", {"PORT": str(TEST_PORT)})
        time.sleep(1)
        pulled = run_script(iran, "speed_pull", {"PORT": str(TEST_PORT)}, timeout=40).strip()
        m = re.search(r"SPEED=([\d.]+)", pulled)
        _say(aid, "Pull: " + " ".join(pulled.split())[:220])              # bytes, seconds, who was on the socket: a number that is too good to be true can be checked

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
BUILD_OF: dict[int, int] = {}   # test action id -> its build id (what Stop cancels)
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
    ("chisel:fwd", "Chisel · forward WebSocket", "the Iran server dials: a customer's download is fast", "Chisel", True),
    ("chisel:fwdtls", "Chisel · forward TLS WebSocket", "the Iran server dials, TLS", "Chisel", False),
]
KIND_KEYS = {k[0] for k in KINDS}
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

    return "chisel", {"chisel:tls": "wss", "chisel:fwd": "fws", "chisel:fwdtls": "fwss"}.get(kind, "ws")


def start_other(iran_id: int, foreign_id: int, kind: str, measure: str = "full") -> int:
    """A temporary BackPack / Paqet / Chisel tunnel: built (which measures it), then removed. Returns the test's action id."""
    purge_leftovers(iran_id, foreign_id)
    from . import build_other

    k, tr = kind_parts(kind)

    with session() as db:
        names = {t.name for t in db.scalars(select(Tunnel))}

    n = 1

    while f"test-{n}" in names:
        n += 1

    name = f"test-{n}"
    build_aid = build_other.start_build(iran_id, foreign_id, str(TEST_PORT), k, tr, {}, forced_name=name, measure=measure)

    with session() as db:
        entry = ActionLog(ref=name, action="tunnel_test", source="manual", story=[f"Temporary {kind} tunnel {name} is being built (build #{build_aid})…"])
        db.add(entry)
        db.flush()
        aid = entry.id

    BUILD_OF[aid] = build_aid

    threading.Thread(target=_other_worker, args=(aid, build_aid, name, iran_id, foreign_id), name=f"tunnel-test-{name}", daemon=True).start()
    return aid


def _other_worker(aid: int, build_aid: int, name: str, iran_id: int = 0, foreign_id: int = 0) -> None:
    ok: bool | None = False
    verdict = ""

    try:
        seen = 0

        for _ in range(200):
            time.sleep(3)

            with session() as db:
                b = db.get(ActionLog, build_aid)
                lines, fin, bok = (list(b.story or []), bool(b.finished_at), b.ok) if b is not None else ([], False, None)

            for ln in lines[seen:]:
                _say(aid, ln)

            seen = len(lines)

            if fin:
                ok, story = bok, lines
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
            RESULTS[aid] = {"latency_ms": q.get("latency_ms"), "jitter_ms": q.get("jitter_ms"), "loss_pct": q.get("loss_pct"), "down_mbps": q.get("down_mbps"), "up_mbps": q.get("up_mbps"), "down1_mbps": q.get("down1_mbps"), "up1_mbps": q.get("up1_mbps"), "score": sc}
            _say(aid, f"Built and measured: {q.get('latency_ms')} ms, jitter {q.get('jitter_ms')} ms, loss {q.get('loss_pct')}%, ↓{q.get('down_mbps')} ↑{q.get('up_mbps')} Mb/s" + (f", one connection ↓{q['down1_mbps']} ↑{q.get('up1_mbps')}" if q.get("down1_mbps") is not None else ""))
            ok = sc >= 50
            verdict = f"Score {sc}/100 — {'excellent' if sc >= 75 else 'usable' if sc >= 50 else 'poor'}. Everything temporary was removed."
    except Exception as e:  # thread boundary
        log.exception("tunnel test #%s crashed", aid)
        verdict, ok = f"Stopped: {str(e)[:160]}", False
    finally:
        _cleanup(aid, name, iran_id, foreign_id)
        _say(aid, verdict or "Finished.", done=True, ok=ok)


def explain_failure(kind: str, error: str) -> str:
    """Pure: a failed kind in plain words. What the real tests showed on Iranian routes: clear-text WebSocket and QUIC are filtered while their TLS / plain-UDP cousins pass."""
    e = str(error or "")

    if not e:
        return e

    if kind in ("bp:ws", "bp:wsmux") and "no data" in e:
        return e + " — clear-text WebSocket is usually filtered on Iranian routes; use the TLS variant (wss / wssmux)."

    if "quic" in kind:
        return e + " — QUIC is blocked on most Iranian routes; use UDP (BackPack Direct · UDP) or KCP instead."

    return e


def compare_start(iran_id: int, foreign_id: int, kinds: list[str], measure: str = "full") -> str:
    from . import license

    kinds = [k for k in kinds if k in {k_[0] for k_ in KINDS}] or ["tcpmux"]
    allowed = [k for k in kinds if license.tunnel_kind_allowed(k)]

    if not allowed:
        license.require("tunnels")

    kinds = allowed

    if any(not j["done"] for j in _COMPARE.values()):
        raise RuntimeError("another tunnel test is still running")

    jid = f"cmp{int(time.time())}"
    job = {"id": jid, "iran_id": iran_id, "foreign_id": foreign_id, "measure": measure, "kinds": kinds, "results": [], "current": "", "current_aid": None, "done": False, "stop": False, "started": int(time.time())}
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
                aid = start(job["iran_id"], job["foreign_id"], kind) if kind_parts(kind)[0] == "backhaul" else start_other(job["iran_id"], job["foreign_id"], kind, job.get("measure", "full"))
            except (ValueError, RuntimeError) as e:
                job["results"].append({"kind": kind, "ok": False, "error": str(e)[:160]})
                continue

            job["current_aid"] = aid

            for _ in range(400):
                time.sleep(3)

                if job["stop"]:
                    builder.CANCEL.add(BUILD_OF.get(aid, -1))

                with session() as db:
                    e = db.get(ActionLog, aid)

                    if e is not None and e.finished_at:
                        ok, story = bool(e.ok), list(e.story or [])
                        break
            else:
                ok, story = False, ["the test took too long"]

            builder.CANCEL.discard(BUILD_OF.pop(aid, -1))

            if job["stop"] and aid not in RESULTS:           # cancelled half way: no row for it
                continue

            r = dict(RESULTS.get(aid) or {})
            r.update(kind=kind, ok=ok, aid=aid, verdict=story[-1] if story else "", error="" if ok or r.get("score") is not None else explain_failure(kind, (story[-1] if story else "no result")[:160]))   # a failed build says why
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
    aid = job.get("current_aid")

    if aid in BUILD_OF:
        builder.CANCEL.add(BUILD_OF[aid])      # the running build stops at its next step and removes what it made
