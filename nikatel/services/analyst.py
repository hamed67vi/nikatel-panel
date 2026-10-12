"""The analyst: what the history says, and what the smart repair should do about it.

Three things come out of the data the panel already keeps (the checks of every tunnel, the samples of every server, the repairs it ran):

1. A PROFILE of every tunnel: availability, outages and how long they last, the normal latency and whether it is rising, the hours of the day it is weak,
   and a verdict (stable / watch / chronic). Tunnels whose outages happen together are grouped: that is one broken server or network, not several broken tunnels.
2. What FIXES WHAT: from the repair cases (RepairCase) and the plain restarts in the action log - how often each step healed which cause, how fast.
3. HINTS for the smart repair: a step that has failed again and again on this tunnel (or for this cause everywhere) is skipped, and the story says why.
   Nothing here acts by itself; the smart repair asks `hints_for` and decides, and it never ends up with no step at all.

Plus a view of the SERVERS: load in the peak hours, what depends on each, which are idle and which are redundant (same city, the rest can take their load).

Everything marked "pure" works on plain lists and numbers and is covered by tests/test_analyst.py."""
from __future__ import annotations

import re
import statistics
import time
from collections import Counter, defaultdict

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import (
    ActionLog,
    RepairCase,
    Server,
    ServerSample,
    Tunnel,
    TunnelCheck,
    is_temp,
)

log = get_logger("analyst")

OUTAGE_AFTER = 2               # this many failed checks in a row make an outage (one lost check is noise)
GAP_SECONDS = 600              # two checks further apart than this are not "in a row" (the panel was off)
CHRONIC_PER_DAY = 2.0          # outages per day from which a tunnel is chronic
WATCH_PER_DAY = 0.5
CHRONIC_AVAIL = 97.0           # availability below this is chronic whatever the count
WATCH_AVAIL = 99.5
RTT_UP_FACTOR = 1.5            # the recent latency is this many times the normal ...
RTT_UP_MS = 30.0               # ... and at least this many ms above it
SKIP_AFTER_TUNNEL = 3          # a step that failed this many times in a row on this tunnel (and never healed it in the window) is skipped
SKIP_AFTER_CAUSE = 8           # ... or this many times for this cause on all tunnels, with under 10 % success
NEVER_SKIP = {("units_down", "restart")}      # a dead service is always restarted first


# ------------------------------------------------------------------ pure: tunnel profile
def find_outages(checks: list[tuple[int, bool]], now: int | None = None) -> list[dict]:
    """[(ts, ok)] -> outages: runs of at least OUTAGE_AFTER failed checks. `ongoing` is a run that has no good check after it yet."""
    out: list[dict] = []
    run: list[int] = []
    last_ts = None

    def close(end: int | None) -> None:
        if len(run) >= OUTAGE_AFTER:
            e = end if end is not None else (now or run[-1])
            out.append({"start": run[0], "end": None if end is None else e, "secs": max(0, e - run[0]), "ongoing": end is None})

    for ts, ok in sorted(checks):
        if last_ts is not None and ts - last_ts > GAP_SECONDS:
            run.clear()                      # a hole in the data: the run before it is not continued

        last_ts = ts

        if ok:
            close(ts)
            run.clear()
        else:
            run.append(ts)

    close(None)
    return out


def _norm_error(text: str) -> str:
    """The reason of a failed check without the numbers that change from one check to the next, so the same reason counts as one."""
    t = re.sub(r"\d+(\.\d+)?", "#", str(text or "").strip().lower())
    return t[:70]


def tunnel_profile(checks: list[tuple[int, bool, float | None, str]], now: int, days: float) -> dict:
    """The profile of one tunnel from its checks [(ts, ok, rtt_ms, error)] over `days`. Pure."""
    n = len(checks)

    if n == 0:
        return {"checks": 0, "availability": None, "outages": 0, "outages_per_day": 0.0, "mttr_secs": None, "longest_secs": 0, "rtt_base": None, "rtt_p95": None,
                "rtt_now": None, "rtt_ratio": None, "weak_hours": [], "causes": [], "verdict": "no data", "rising": False, "ongoing": False, "down_secs": 0}

    ok_n = sum(1 for c in checks if c[1])
    avail = round(ok_n * 100.0 / n, 2)
    outs = find_outages([(c[0], c[1]) for c in checks], now)
    closed = [o["secs"] for o in outs if not o["ongoing"]]
    per_day = round(len(outs) / max(days, 0.25), 2)
    rtts = sorted(c[2] for c in checks if c[1] and c[2] is not None)
    base = round(statistics.median(rtts), 1) if rtts else None
    p95 = round(rtts[min(len(rtts) - 1, int(len(rtts) * 0.95))], 1) if rtts else None
    recent = [c[2] for c in checks if c[1] and c[2] is not None and now - c[0] <= 1800]
    rnow = round(statistics.median(recent), 1) if len(recent) >= 3 else None
    ratio = round(rnow / base, 2) if (rnow is not None and base) else None

    # the hours of the day (server clock) this tunnel fails more than twice as often as on average
    by_hour: dict[int, list[int]] = defaultdict(lambda: [0, 0])

    for ts, ok, _r, _e in checks:
        h = int(time.strftime("%H", time.localtime(ts)))
        by_hour[h][0] += 1
        by_hour[h][1] += 0 if ok else 1

    overall_fail = (n - ok_n) / n
    weak = sorted(h for h, (cnt, bad) in by_hour.items() if cnt >= 30 and bad / cnt >= max(0.05, 2 * overall_fail) and bad >= 3)

    # rising: the last 24 h fail at least twice as often as the days before, and at least 1 %
    day_ago = now - 86400
    last = [c for c in checks if c[0] >= day_ago]
    before = [c for c in checks if c[0] < day_ago]
    fl = (sum(1 for c in last if not c[1]) / len(last)) if len(last) >= 60 else None
    fb = (sum(1 for c in before if not c[1]) / len(before)) if len(before) >= 200 else None
    rising = bool(fl is not None and fb is not None and fl >= 0.01 and fl >= 2 * fb)

    causes = Counter(_norm_error(c[3]) for c in checks if not c[1] and c[3]).most_common(3)

    if per_day >= CHRONIC_PER_DAY or avail < CHRONIC_AVAIL:
        verdict = "chronic"
    elif per_day >= WATCH_PER_DAY or avail < WATCH_AVAIL or rising:
        verdict = "watch"
    else:
        verdict = "stable"

    return {"checks": n, "availability": avail, "outages": len(outs), "outages_per_day": per_day, "mttr_secs": int(sum(closed) / len(closed)) if closed else None,
            "longest_secs": max((o["secs"] for o in outs), default=0), "down_secs": sum(o["secs"] for o in outs), "rtt_base": base, "rtt_p95": p95, "rtt_now": rnow, "rtt_ratio": ratio,
            "weak_hours": weak, "causes": [{"reason": r, "n": k} for r, k in causes], "verdict": verdict, "rising": rising, "ongoing": any(o["ongoing"] for o in outs)}


def risk_of(profile: dict, state: str, hour_now: int) -> list[str]:
    """Why a tunnel that is UP right now is worth watching (empty = nothing). Pure."""
    if state != "up" or not profile.get("checks"):
        return []

    why: list[str] = []
    base, rnow = profile.get("rtt_base"), profile.get("rtt_now")

    if base and rnow and rnow >= base * RTT_UP_FACTOR and rnow >= base + RTT_UP_MS:
        why.append(f"latency {rnow:.0f} ms against a normal {base:.0f} ms")

    if profile.get("rising"):
        why.append("it fails more often than in the days before")

    if hour_now in (profile.get("weak_hours") or []) and profile.get("verdict") in ("watch", "chronic"):
        why.append("this is one of its weak hours")

    if profile.get("verdict") == "chronic":
        why.append(f"chronic: {profile['outages']} outages, {profile['availability']} % available")

    return why


def correlate(outages: dict[str, list[dict]], ends: dict[str, tuple[str, str]], slack: int = 120) -> list[dict]:
    """Outages that happen TOGETHER on tunnels that share a server point at that server (or its network), not at the tunnels. Pure.
    outages: tunnel -> [{"start","end"}], ends: tunnel -> (iran name, foreign name)."""
    by_server: dict[str, list[str]] = defaultdict(list)

    for t, (i, f) in ends.items():
        by_server[i].append(t)
        by_server[f].append(t)

    found = []

    for server, names in by_server.items():
        if len(names) < 2:
            continue

        total = shared = 0

        for t in names:
            for o in outages.get(t, []):
                total += 1
                s0, e0 = o["start"], o.get("end") or o["start"] + 600

                if any(not (p["start"] > e0 + slack or (p.get("end") or p["start"] + 600) < s0 - slack) for u in names if u != t for p in outages.get(u, [])):
                    shared += 1

        if total >= 3 and shared / total >= 0.5:
            found.append({"server": server, "tunnels": sorted(names), "outages": total, "together": shared,
                          "text": f"{shared} of {total} outages on the tunnels of {server} happened together with another tunnel of the same server: look at the server or its network, not at the tunnels."})

    return sorted(found, key=lambda x: -x["together"])


# ------------------------------------------------------------------ pure: what fixes what, hints for the smart repair
def step_stats(rows: list[dict]) -> dict:
    """rows [{tunnel, cause, step, ok, secs, ts}] -> {"by_step": {...}, "by_cause": {cause: {step: {...}}}}. Pure."""
    def add(d: dict, ok: bool, secs: int | None) -> None:
        d["attempts"] = d.get("attempts", 0) + 1
        d["healed"] = d.get("healed", 0) + (1 if ok else 0)

        if ok and secs:
            d["_secs"] = d.get("_secs", 0) + secs

    by_step: dict[str, dict] = {}
    by_cause: dict[str, dict[str, dict]] = {}

    for r in rows:
        add(by_step.setdefault(r["step"], {}), r["ok"], r.get("secs"))
        add(by_cause.setdefault(r.get("cause") or "unknown", {}).setdefault(r["step"], {}), r["ok"], r.get("secs"))

    def fin(d: dict) -> dict:
        a = d.get("attempts", 0)
        return {"attempts": a, "healed": d.get("healed", 0), "rate": round(d.get("healed", 0) * 100.0 / a) if a else None, "avg_secs": int(d["_secs"] / d["healed"]) if d.get("healed") and d.get("_secs") else None}

    return {"by_step": {k: fin(v) for k, v in by_step.items()}, "by_cause": {c: {s: fin(v) for s, v in d.items()} for c, d in by_cause.items()}}


def choose_steps(steps: list[str], cause: str, tunnel_rows: list[dict], all_rows: list[dict], now: int, window: int = 14 * 86400) -> tuple[list[str], list[str]]:
    """The steps the smart repair should really try, and the sentences that say what history changed. Pure.
    Never returns an empty list: if everything would be skipped, the original order stays."""
    keep, notes = [], []

    for step in steps:
        if (cause, step) in NEVER_SKIP or step in ("move", "switch"):          # the escalations are the way out: they are never skipped by history
            keep.append(step)
            continue

        mine = sorted((r for r in tunnel_rows if r["step"] == step and now - r["ts"] <= window), key=lambda r: r["ts"])
        tail = []

        for r in reversed(mine):
            if r["ok"]:
                break

            tail.append(r)

        if len(tail) >= SKIP_AFTER_TUNNEL:
            notes.append(f"History: “{step}” failed {len(tail)} times in a row on this tunnel and has not healed it in {window // 86400} days - it is skipped.")
            continue

        same = [r for r in all_rows if r["step"] == step and (r.get("cause") or "unknown") == cause and now - r["ts"] <= 30 * 86400]

        if len(same) >= SKIP_AFTER_CAUSE and sum(1 for r in same if r["ok"]) * 10 < len(same):
            notes.append(f"History: “{step}” healed {sum(1 for r in same if r['ok'])} of {len(same)} tunnels with this cause - it is skipped.")
            continue

        keep.append(step)

    return (keep or list(steps)), (notes if keep else [])


# ------------------------------------------------------------------ pure: servers
def server_verdicts(rows: list[dict]) -> list[dict]:
    """rows [{name, role, city, cores, peak_cpu_p95, peak_mbps, tunnels, tunnels_mbps}] -> the same rows with `verdict` and `note`. Pure.
    idle: nothing runs there worth a server; redundant: another server in the same city can take its load; busy: little room left."""
    out = []

    for r in rows:
        if r.get("peak_cpu_p95") is None:
            out.append({**r, "used": 0.0, "verdict": "no data", "note": "no samples yet"})
            continue

        used = (r.get("peak_cpu_p95") or 0) * (r.get("cores") or 1) / 100.0        # cores in use at the peak
        r = {**r, "used": round(used, 2)}

        if (r.get("peak_cpu_p95") or 0) < 15 and (r.get("peak_mbps") or 0) < 20 and (r.get("tunnels_mbps") or 0) < 5:
            r["verdict"], r["note"] = "idle", "almost nothing runs here even in the peak hours"
        elif (r.get("peak_cpu_p95") or 0) >= 70:
            r["verdict"], r["note"] = "busy", "little room left at the peak"
        elif (r.get("peak_cpu_p95") or 0) < 30:
            r["verdict"], r["note"] = "light", "plenty of room"
        else:
            r["verdict"], r["note"] = "ok", ""

        out.append(r)

    by_city: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for r in out:
        if r.get("city") and r["verdict"] != "no data":
            by_city[(r["role"], r["city"])].append(r)

    for (_role, city), group in by_city.items():
        if len(group) < 2:
            continue

        group.sort(key=lambda r: r["used"])
        lightest, rest = group[0], group[1:]
        spare = sum(max(0.0, 0.6 * (x.get("cores") or 1) - x["used"]) for x in rest)         # each other server may be used up to 60 % of its cores at the peak

        if lightest["used"] <= spare and lightest["verdict"] in ("light", "idle"):          # only a light server is called redundant; the room is judged by CPU (the line speed is not known)
            lightest["verdict"] = "redundant" if lightest["verdict"] != "idle" else "idle"
            lightest["note"] = f"{', '.join(x['name'] for x in rest)} in {city} can take its load ({lightest['used']:.1f} cores at the peak, {spare:.1f} cores free there)"

    return out


# ------------------------------------------------------------------ database
_CACHE: dict = {}


def _kind_of(t: Tunnel) -> str:
    from . import autopilot

    return autopilot.kind_of(t.type, t.transport, t.record or {})


def record_case(tunnel: str, iran: str, foreign: str, kind: str, cause: str, steps: list[dict], healed: bool, secs: int, source: str) -> None:
    """Called by the smart repair when a run ends. Never raises: the analyst must not be able to break a repair."""
    try:
        with session() as db:
            db.add(RepairCase(tunnel=tunnel, iran=iran, foreign=foreign, kind=kind, cause=cause, steps=steps, healed=healed, secs=secs, source=source))
            old = list(db.scalars(select(RepairCase.id).order_by(RepairCase.id.desc()).offset(2000)))

            for cid in old:
                db.delete(db.get(RepairCase, cid))
    except Exception:
        log.exception("could not keep the repair case of %s", tunnel)


def step_rows(days: int = 30, tunnel: str | None = None) -> list[dict]:
    """Every step ever tried, as rows: the repair cases, plus the plain restarts of the action log (one "restart" step each)."""
    since = int(time.time()) - days * 86400
    rows: list[dict] = []

    with session() as db:
        q = select(RepairCase).where(RepairCase.ts >= since)

        if tunnel:
            q = q.where(RepairCase.tunnel == tunnel)

        for c in db.scalars(q):
            for s in c.steps or []:
                rows.append({"tunnel": c.tunnel, "cause": c.cause, "step": s.get("step", ""), "ok": bool(s.get("ok")), "secs": s.get("secs"), "ts": c.ts, "kind": c.kind})

        q2 = select(ActionLog).where(ActionLog.action == "fix", ActionLog.dry_run.is_(False), ActionLog.ts >= since, ActionLog.ok.is_not(None))

        if tunnel:
            q2 = q2.where(ActionLog.ref == tunnel)

        for a in db.scalars(q2):
            rows.append({"tunnel": a.ref, "cause": "units_down", "step": "restart", "ok": bool(a.ok), "secs": (a.finished_at - a.ts) if a.finished_at else None, "ts": a.ts, "kind": ""})

    return rows


def hints_for(tunnel: str, cause: str, steps: list[str]) -> tuple[list[str], list[str]]:
    """What the smart repair asks before it climbs: (steps to try, sentences for the story). Never raises, never empties the list."""
    try:
        rows = step_rows(30)
        mine = [r for r in rows if r["tunnel"] == tunnel]
        return choose_steps(steps, cause, mine, rows, int(time.time()))
    except Exception:
        log.exception("the analyst could not advise (the repair goes on without it)")
        return list(steps), []


def _peak_hours(samples: dict[int, list[ServerSample]], servers: dict[int, Server]) -> set[int]:
    traffic: dict[int, float] = defaultdict(float)

    for sid, rows in samples.items():
        if servers[sid].role != "iran":
            continue

        for r in rows:
            traffic[int(time.strftime("%H", time.localtime(r.ts)))] += r.rx_mbps + r.tx_mbps

    if not traffic:
        return set()

    best = max(range(24), key=lambda h: sum(traffic.get((h + k) % 24, 0.0) for k in range(4)))
    return {(best + k) % 24 for k in range(4)}


def overview(days: int = 7) -> dict:
    """The whole analysis (cached for two minutes: it reads a few hundred thousand rows)."""
    days = max(1, min(14, int(days)))
    c = _CACHE.get(days)

    if c and time.time() - c["at"] < 120:
        return c["data"]

    now = int(time.time())
    since = now - days * 86400

    with session() as db:
        servers = {s.id: s for s in db.scalars(select(Server))}
        tunnels = [t for t in db.scalars(select(Tunnel)) if not is_temp(t.name) and t.enabled]
        names = {t.id: t.name for t in tunnels}
        checks: dict[int, list] = defaultdict(list)

        for tid, ts, ok, rtt, err in db.execute(select(TunnelCheck.tunnel_id, TunnelCheck.ts, TunnelCheck.ok, TunnelCheck.rtt_ms, TunnelCheck.error).where(TunnelCheck.ts >= since)):
            if tid in names:
                checks[tid].append((ts, bool(ok), rtt, err or ""))

        samples: dict[int, list] = defaultdict(list)

        for r in db.scalars(select(ServerSample).where(ServerSample.ts >= since)):
            samples[r.server_id].append(r)

        trows = [{"id": t.id, "name": t.name, "iran": t.iran.name, "foreign": t.foreign.name, "kind": _kind_of(t), "state": t.state, "mbps": (t.rx_mbps or 0) + (t.tx_mbps or 0)} for t in tunnels]
        srows = {sid: {"name": s.name, "role": s.role, "online": s.online, "city": (((s.extra or {}).get("geo") or {}).get("city") or "") or "", "cores": (s.facts or {}).get("cores") or 1,
                       "provider": (((s.extra or {}).get("geo") or {}).get("provider") or s.provider or ""), "id": sid} for sid, s in servers.items()}

    hour_now = int(time.strftime("%H", time.localtime(now)))
    profiles, risks, all_outs = [], [], {}

    for t in trows:
        ck = sorted(checks.get(t["id"], []))
        p = tunnel_profile(ck, now, days)
        all_outs[t["name"]] = find_outages([(c0[0], c0[1]) for c0 in ck], now)
        profiles.append({**t, **p})
        why = risk_of(p, t["state"], hour_now)

        if why:
            risks.append({"tunnel": t["name"], "iran": t["iran"], "foreign": t["foreign"], "why": why, "level": "high" if len(why) >= 2 else "watch"})

    order = {"chronic": 0, "watch": 1, "stable": 2, "no data": 3}
    profiles.sort(key=lambda p: (order.get(p["verdict"], 3), -(p["outages_per_day"] or 0), p["name"]))
    common = correlate(all_outs, {t["name"]: (t["iran"], t["foreign"]) for t in trows})

    peak = _peak_hours({sid: rows for sid, rows in samples.items()}, servers)
    on_server: dict[str, list] = defaultdict(list)

    for t in trows:
        on_server[t["iran"]].append(t)
        on_server[t["foreign"]].append(t)

    sv = []

    for sid, s in srows.items():
        rows = [r for r in samples.get(sid, []) if int(time.strftime("%H", time.localtime(r.ts))) in peak] or samples.get(sid, [])
        cpu = sorted(r.cpu for r in rows)
        sv.append({**s, "peak_cpu_p95": round(cpu[min(len(cpu) - 1, int(len(cpu) * 0.95))], 1) if cpu else None, "peak_cpu_avg": round(sum(cpu) / len(cpu), 1) if cpu else None,
                   "peak_mbps": round(sum(r.rx_mbps + r.tx_mbps for r in rows) / len(rows), 1) if rows else 0.0, "peak_mbps_max": round(max((r.rx_mbps + r.tx_mbps for r in rows), default=0.0), 1),
                   "peak_ram": round(sum(r.ram for r in rows) / len(rows), 1) if rows else None, "samples": len(rows),
                   "tunnels": len(on_server.get(s["name"], [])), "tunnels_mbps": round(sum(t["mbps"] for t in on_server.get(s["name"], [])), 1)})

    sv = server_verdicts(sv)
    sv.sort(key=lambda x: ({"redundant": 0, "idle": 1, "busy": 2, "ok": 3, "light": 4}.get(x["verdict"], 5), x["role"], -(x["peak_cpu_p95"] or 0)))

    rows = step_rows(30)
    data = {"at": now, "days": days, "peak_hours": sorted(peak), "tunnels": profiles, "risks": risks, "common": common, "servers": sv, "repairs": step_stats(rows),
            "summary": {"tunnels": len(profiles), "chronic": sum(1 for p in profiles if p["verdict"] == "chronic"), "watch": sum(1 for p in profiles if p["verdict"] == "watch"), "at_risk": len(risks),
                        "idle": sum(1 for x in sv if x["verdict"] == "idle"), "redundant": sum(1 for x in sv if x["verdict"] == "redundant"),
                        "availability": round(sum(p["availability"] for p in profiles if p["availability"] is not None) / max(1, sum(1 for p in profiles if p["availability"] is not None)), 2)}}
    _CACHE[days] = {"at": time.time(), "data": data}
    return data
