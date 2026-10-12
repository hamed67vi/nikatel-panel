"""The underuse watch: a server that carries little may carry little because something is wrong with its own network.

The analyst finds the servers that are idle or light. This module looks at each of them (read only: the TCP settings, the retransmits and the drops while the
server really downloads for a few seconds, the CPU steal, the line speed), says what is wrong in plain words, and - when the fault is the kernel's own
settings - fixes it: the same tuning the tunnel builder gives to the servers it builds on (BBR, fq, big buffers), measured before and after and put back when it
did not help. A fault that lies in the provider's network (packets lost on the path, a slow line, a stolen CPU) cannot be fixed from here: it is reported, once,
with what to do.

`parse` and `judge` are pure and covered by tests/test_underuse.py."""
from __future__ import annotations

import statistics
import time

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server
from ..ssh import Node, SSHError, run_action, run_script
from . import settings as st
from .events import emit

log = get_logger("underuse")

RECHECK_SECONDS = 12 * 3600       # a server is looked at again after this long
FIX_SECONDS = 24 * 3600           # and a fix is tried again only after this long
PER_TURN = 2                      # servers looked at per turn of the job (every 30 minutes)
BIG_BUFFER = 16 * 1024 * 1024
RETRANS_WARN, RETRANS_BAD = 2.0, 5.0        # % of the TCP segments sent again while the server downloads
STEAL_WARN = 5.0                            # % of the CPU time the provider's host took
DROP_WARN = 0.1                             # % of the packets dropped or in error on the card
SLOW_MBIT, SLOW_FRACTION = 30.0, 0.4        # a line under this, or under this share of the fleet's median, is slow


# ------------------------------------------------------------------ pure
def parse(out: str) -> dict:
    """The answer of the net_health script -> numbers. Pure. Raises ValueError when it is not an answer."""
    kv: dict[str, list[str]] = {}

    for line in str(out).splitlines():
        parts = line.split()

        if parts:
            kv[parts[0]] = parts[1:]

    if "ST1" not in kv or "ST2" not in kv:
        raise ValueError("the server did not answer the health probe")

    def num(key: str, i: int = 0, default: float | None = None) -> float | None:
        try:
            return float(kv.get(key, [])[i])
        except (ValueError, IndexError):
            return default

    def delta(a: str, b: str, i: int) -> float | None:
        x, y = num(a, i), num(b, i)
        return None if x is None or y is None else y - x

    total, steal = delta("ST1", "ST2", 0), delta("ST1", "ST2", 1)
    out_segs, retrans = delta("TCP1", "TCP2", 0), delta("TCP1", "TCP2", 1)
    pk = [delta("IF1", "IF2", i) or 0 for i in range(6)]                # rx, tx packets, rx, tx errors, rx, tx dropped
    packets, bad = pk[0] + pk[1], pk[2] + pk[3] + pk[4] + pk[5]
    down_bps = num("DOWN", 0, 0.0) or 0.0
    mem_avail, mem_total, swap = num("MEM", 0, 0) or 0, num("MEM", 1, 0) or 0, num("MEM", 2, 0) or 0
    ct_count, ct_max = num("CT", 0, -1), num("CT", 1, -1)

    return {"iface": (kv.get("IF") or [""])[0], "speed": int(num("SPEED", 0, -1) or -1), "mtu": int(num("MTU", 0, 0) or 0), "cc": (kv.get("CC") or [""])[0],
            "qdisc": [q for q in (kv.get("QDISC") or [])][:2], "rmem_max": int(num("RMEM", 0, 0) or 0), "wmem_max": int(num("RMEM", 1, 0) or 0),
            "tcp_rmem_max": int(num("TCPMEM", 0, 0) or 0), "tcp_wmem_max": int(num("TCPMEM", 1, 0) or 0), "mtu_probing": int(num("MTUPROBE", 0, 0) or 0),
            "ct_used": round(100.0 * ct_count / ct_max, 1) if (ct_max or 0) > 0 and (ct_count or 0) >= 0 else None, "tuned": (kv.get("TUNED") or ["no"])[0] == "yes",
            "mem_avail_pct": round(100.0 * mem_avail / mem_total, 1) if mem_total else None, "swap_mb": int(swap / 1024),
            "steal": round(100.0 * steal / total, 1) if total and total > 0 and steal is not None else None,
            "retrans": round(100.0 * retrans / out_segs, 2) if out_segs and out_segs >= 500 and retrans is not None else None,
            "drops": round(100.0 * bad / packets, 3) if packets >= 1000 else None,
            "down_mbit": round(down_bps * 8 / 1e6, 1), "down_ok": down_bps > 0}


def judge(p: dict, fleet_down: float | None = None) -> dict:
    """What is wrong, in plain words: {"findings": [{"id","text","fix"}], "verdict": healthy | tunable | provider}. Pure.
    `fix` is "tune" when the panel can put it right (the kernel settings), else None (a fault of the provider's network or of the plan)."""
    f: list[dict] = []
    cc = p.get("cc") or ""

    if cc and cc != "bbr":
        f.append({"id": "congestion", "fix": "tune", "text": f"TCP congestion control is {cc}, not BBR: on a long route that loses a few packets, {cc} slows down hard where BBR keeps the speed."})

    if p.get("rmem_max") and (p["rmem_max"] < BIG_BUFFER or p.get("wmem_max", 0) < BIG_BUFFER):
        f.append({"id": "buffers", "fix": "tune", "text": f"The socket buffers are small ({p['rmem_max'] // 1024} KB): one connection cannot fill a fast, far line."})

    if "fq" not in " ".join(p.get("qdisc") or []) and cc != "bbr":
        f.append({"id": "qdisc", "fix": "tune", "text": "The packet scheduler is not fq: TCP pacing is off."})

    if p.get("retrans") is not None and p["retrans"] >= RETRANS_WARN:
        f.append({"id": "retrans", "fix": None, "text": f"{p['retrans']} % of the TCP segments are sent again while the server downloads: the route from this server loses packets. BBR often lowers it (the tuning is tried first); what stays after that is the provider's network or this location."})

    if p.get("steal") is not None and p["steal"] >= STEAL_WARN:
        f.append({"id": "steal", "fix": None, "text": f"The provider's host takes {p['steal']} % of this server's CPU time (steal): the node is oversold. Ask the provider or move the server."})

    if 0 < (p.get("speed") or -1) <= 100:
        f.append({"id": "link", "fix": None, "text": f"The network card reports {p['speed']} Mbit/s: the plan's line is slow."})

    if p.get("drops") is not None and p["drops"] >= DROP_WARN:
        f.append({"id": "drops", "fix": None, "text": f"{p['drops']} % of the packets are dropped or in error on the card: a busy queue or a bad virtual card."})

    if p.get("ct_used") is not None and p["ct_used"] >= 80:
        f.append({"id": "conntrack", "fix": None, "text": f"The connection table is {p['ct_used']} % full: new connections will be dropped."})

    if (p.get("swap_mb") or 0) > 200 and (p.get("mem_avail_pct") or 100) < 10:
        f.append({"id": "memory", "fix": None, "text": "The server is out of memory and swaps: everything on it is slow."})

    if p.get("down_ok"):
        d = p["down_mbit"]

        if d < SLOW_MBIT or (fleet_down and d < SLOW_FRACTION * fleet_down):
            f.append({"id": "speed", "fix": None, "text": f"The download speed from this server is {d} Mbit/s" + (f" (the fleet's median is {fleet_down:.0f})" if fleet_down else "") + ": the line or the route is slow."})
    else:
        f.append({"id": "nospeed", "fix": None, "text": "The speed test could not run from this server (no answer from the test host)."})

    real = [x for x in f if x["id"] != "nospeed"]
    verdict = "healthy" if not real else "tunable" if all(x["fix"] == "tune" for x in real) else "provider"
    return {"findings": f, "verdict": verdict}


def better(before: dict, after: dict) -> tuple[bool, str]:
    """Did the tuning help? Kept when the speed did not fall by more than 10 % and the retransmits did not get clearly worse. Pure."""
    why = []
    d0, d1 = before.get("down_mbit") or 0, after.get("down_mbit") or 0

    if before.get("down_ok") and after.get("down_ok") and d1 < d0 * 0.9:
        why.append(f"the speed fell from {d0} to {d1} Mbit/s")

    r0, r1 = before.get("retrans"), after.get("retrans")

    if r0 is not None and r1 is not None and r1 > r0 + 1.0:
        why.append(f"the retransmits rose from {r0} % to {r1} %")

    return (not why), "; ".join(why)


def fleet_median(rows: list[dict], role: str) -> float | None:
    """The median download speed of the servers of one role that were measured, or None when there are fewer than three. Pure."""
    v = [r["down_mbit"] for r in rows if r.get("role") == role and r.get("down_ok") and r.get("down_mbit")]
    return statistics.median(v) if len(v) >= 3 else None


# ------------------------------------------------------------------ database
def _conf() -> dict:
    return st.get("underuse") or {}


def _node_of(sid: int) -> tuple[Node, str, str]:
    with session() as db:
        s = db.get(Server, sid)

        if s is None:
            raise ValueError("server not found")

        return Node(s.host, s.ssh_user, s.ssh_port), s.name, s.role


def _remember(sid: int, entry: dict) -> None:
    with session() as db:
        s = db.get(Server, sid)

        if s is not None:
            ex = dict(s.extra or {})
            ex["underuse"] = {**(ex.get("underuse") or {}), **entry}
            s.extra = ex


def _fleet_down() -> dict[str, float | None]:
    with session() as db:
        rows = [{"role": s.role, **((s.extra or {}).get("underuse") or {}).get("probe", {})} for s in db.scalars(select(Server))]

    return {"iran": fleet_median(rows, "iran"), "foreign": fleet_median(rows, "foreign")}


def probe(sid: int, remember: bool = True) -> dict:
    """Look at one server now (read only; ~10 s: the server downloads a test file while its counters are read)."""
    node, name, role = _node_of(sid)
    p = parse(run_script(node, "net_health", timeout=45))
    j = judge(p, _fleet_down().get(role))
    entry = {"at": int(time.time()), "probe": p, "findings": j["findings"], "verdict": j["verdict"]}

    if remember:
        _remember(sid, entry)

    return {"id": sid, "name": name, "role": role, **entry}


def tune(aid: int, sid: int, source: str = "manual") -> bool:
    """Fix what the kernel settings can fix on one server, with the story: before, the tuning, after - and back again when it did not help."""
    from . import autopilot

    node, name, _role = _node_of(sid)
    r0 = probe(sid)
    p0 = r0["probe"]
    autopilot._say(aid, f"{name}: " + ("; ".join(x["text"] for x in r0["findings"]) if r0["findings"] else "nothing wrong found") + f" (download {p0['down_mbit']} Mbit/s).")
    fixable = [x for x in r0["findings"] if x["fix"] == "tune"]

    if not fixable:
        advice = [x for x in r0["findings"] if x["id"] != "nospeed"]
        autopilot._say(aid, "Nothing the panel can fix here" + (": the cause lies in the provider's network or the plan (see above)." if advice else ": the server is healthy."), done=True, ok=not advice)
        return not advice

    out = run_action(node, "sysctl_tune")
    autopilot._say(aid, f"Kernel tuning applied ({out.strip().splitlines()[-1] if out.strip() else 'done'}): BBR, fq, bigger buffers.")
    time.sleep(5)
    r1 = probe(sid)
    p1 = r1["probe"]
    ok, why = better(p0, p1)
    autopilot._say(aid, f"After: download {p1['down_mbit']} Mbit/s (was {p0['down_mbit']}), retransmits {p1['retrans']} % (was {p0['retrans']} %).")

    if not ok:
        run_action(node, "sysctl_untune")
        _remember(sid, {"tune": "rolled back", "tune_at": int(time.time())})
        autopilot._say(aid, f"It did not help ({why}): the settings were put back as they were.", done=True, ok=False)
        return False

    left = [x for x in r1["findings"] if x["id"] != "nospeed"]
    _remember(sid, {"tune": "kept", "tune_at": int(time.time()), "before_mbit": p0["down_mbit"], "after_mbit": p1["down_mbit"]})

    with session() as db:
        emit(db, "ok", "actions", f"{name}: kernel tuning kept - download {p0['down_mbit']} -> {p1['down_mbit']} Mbit/s ({source}).", name)

    if left:
        autopilot._say(aid, "Still open, and not fixable from here: " + "; ".join(x["text"] for x in left), done=True, ok=False)
        return False

    autopilot._say(aid, "Done: the server is healthy now.", done=True, ok=True)
    return True


def tune_start(sid: int, source: str = "manual") -> int:
    from . import autopilot

    _, name, _role = _node_of(sid)

    def work(aid: int) -> None:
        try:
            tune(aid, sid, source)
        except (SSHError, ValueError) as e:
            autopilot._say(aid, f"Stopped: {str(e)[:160]}", done=True, ok=False)

    return autopilot._start(f"speed {name}", work, action="underuse", source=source)


def untune_start(sid: int) -> int:
    from . import autopilot

    node, name, _role = _node_of(sid)

    def work(aid: int) -> None:
        try:
            out = run_action(node, "sysctl_untune")
            _remember(sid, {"tune": "undone", "tune_at": int(time.time())})
            autopilot._say(aid, f"{name}: the kernel settings are back as they were ({out.strip().splitlines()[-1] if out.strip() else 'done'}).", done=True, ok=True)
        except (SSHError, ValueError) as e:
            autopilot._say(aid, f"Stopped: {str(e)[:160]}", done=True, ok=False)

    return autopilot._start(f"speed {name}", work, action="underuse", source="manual")


def candidates() -> list[tuple[int, str]]:
    """The servers worth a look now: idle, light or redundant at the peak (from the analyst), online, not looked at for RECHECK_SECONDS - the oldest look first."""
    from . import analyst

    now = time.time()
    rows = analyst.overview(7)["servers"]
    pick = [r for r in rows if r["verdict"] in ("idle", "redundant", "light") and r.get("online") is not False]

    with session() as db:
        last = {s.id: int(((s.extra or {}).get("underuse") or {}).get("at") or 0) for s in db.scalars(select(Server))}

    pick = [r for r in pick if now - last.get(r["id"], 0) >= RECHECK_SECONDS]
    pick.sort(key=lambda r: last.get(r["id"], 0))
    return [(r["id"], r["name"]) for r in pick]


def run() -> None:
    """The job: every 30 minutes a couple of the underused servers are looked at; a fault the kernel settings can fix is fixed (when `underuse.auto` is on and the panel is not read-only)."""
    from . import autopilot

    if autopilot.storm_now():
        return

    done_fix = 0

    for sid, name in candidates()[:PER_TURN]:
        try:
            r = probe(sid)
        except (SSHError, ValueError) as e:
            log.warning("underuse: %s not probed: %s", name, str(e)[:100])
            _remember(sid, {"at": int(time.time()), "error": str(e)[:120]})
            continue

        fixable = any(x["fix"] == "tune" for x in r["findings"])

        with session() as db:
            s = db.get(Server, sid)
            last_fix = int((((s.extra or {}).get("underuse") or {}).get("tune_at")) or 0) if s else 0

        if fixable and _conf().get("auto", True) and not get_settings().read_only and time.time() - last_fix >= FIX_SECONDS and not done_fix:
            done_fix += 1
            log.info("underuse: %s - tuning", name)
            tune_start(sid, "auto")
        elif r["verdict"] == "provider":
            _tell_once(sid, name, r)


def _tell_once(sid: int, name: str, r: dict) -> None:
    """A fault of the provider's network is written once per day to the events (so it is not lost), never repeated every 30 minutes."""
    with session() as db:
        s = db.get(Server, sid)
        told = int((((s.extra or {}).get("underuse") or {}).get("told_at")) or 0) if s else 0

        if s is None or time.time() - told < 24 * 3600:
            return

        emit(db, "warn", "servers", f"{name} is underused and its own network is the likely cause: " + "; ".join(x["text"] for x in r["findings"] if x["fix"] is None and x["id"] != "nospeed")[:260], name)

    _remember(sid, {"told_at": int(time.time())})
