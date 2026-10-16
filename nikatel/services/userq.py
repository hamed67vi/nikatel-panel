"""What the USERS really get through a tunnel - the question the Tunnel Doctor exists for (the owner, 2026-10-16: "I want the Doctor to truly tell whether the user receives quality internet or not").

A tunnel can be up, clean and fast and still give its users nothing (the foreign server's own internet, its Xray, the CDN in front of the Iran server). The only look at the whole path is the probe: a server inside
Iran that keeps one xray per config and tests it all the time (status, ping, now and then a speed test). This module reads what the panel already keeps of those tests and says, per tunnel:

  which configs ride it   the operator's label, the address of the Iran server, or - for a CDN name - the A record of that name in the DNS accounts (the Iran server it points at and the port it sends to);
  how each of them does   against ITSELF: the share of its tests answered in the last 2 hours, its ping against its own week (a config through a CDN pings 400 - 1000 ms and that is normal for it), its speed
                          against its own speed tests; a config that is down is told as down;
  the grade of the tunnel good (nothing) · fair (a few configs have a problem) · poor (half of them or more) · bad (every one) · unknown (no config, or too little data) - changed only when two looks in a row agree.

When the grade falls to poor or bad the Doctor looks by itself: if the tunnel is fine (bandwidth, loss, delay, servers) the problem is BEYOND it, and that is said; if the tunnel is not fine, the tunnel's own explanation is
(see services/bandwidth.py). Kept in the record of the tunnel, `ux`; the pure helpers (`map_configs`, `config_stats`, `config_issue`, `tunnel_ux`, `settle_grade`, `explain_users`) carry the decisions and are tested."""
from __future__ import annotations

import statistics
import threading
import time

from sqlalchemy import select

from .. import jobs
from ..db import session
from ..log import get_logger
from ..models import Config, ConfigSample, Server, Tunnel, is_temp
from . import dnscache
from .events import emit

log = get_logger("userq")
RECENT = 2 * 3600            # the look at "now": the tests of the last two hours
REF_WINDOW = 7 * 86400       # what a config usually does: its last week (without the last two hours)
REF_REFRESH = 3600           # the usual ping of a config is worked out again this often (it reads thousands of samples)
MIN_RECENT = 10              # fewer tests than this in the window: nothing is judged (a config just added)
MIN_REF = 100                # fewer samples than this in the week: its usual ping is not known
UNSTABLE_UNDER = 0.85        # answered less than this share of its tests: unstable
DOWN_AFTER = 600             # a config that has been down this long is down (the probe's own settle already asks two reads)
PING_RATIO = 2.0             # the ping is at least this many times its usual ...
PING_EXTRA_MS = 200          # ... and at least this much more: slow
SPEED_RATIO = 0.5            # the last speed test is under this share of the usual speed of the config: slow
SPEED_FRESH = 12 * 3600      # a speed test older than this says nothing about now
MIN_SPEEDS = 4               # speed tests needed before the usual speed is known
DNS_FRESH = 600              # the copy of the DNS records is renewed (in the background) when older than this
CONFIRM = 2                  # the grade of a tunnel changes after this many looks in a row that agree (the job runs every five minutes)
_LOCK = threading.Lock()
_RUNNING = [False]


# ------------------------------------------------------------------ pure helpers (tested)
def map_configs(configs: list[dict], tunnels: list[dict], servers: list[dict], records: list[dict]) -> dict[str, dict]:
    """Which tunnel each config rides: {config name: {"tunnel": id, "how": "label" | "address" | "dns"}}; a config that cannot be placed for sure is left out (nothing is guessed).
    configs: [{"name", "tunnel" (the label), "address", "port"}]; tunnels: [{"id", "name", "iran_id", "ports": set}]; servers (Iran): [{"id", "name", "host"}]; records: the A records of the DNS accounts [{"fqdn", "ips", "port"}]."""
    by_name = {t["name"]: t for t in tunnels}
    by_host = {s["host"]: s["id"] for s in servers}
    by_sname = {str(s["name"]).lower(): s["id"] for s in servers}
    by_fqdn: dict[str, list[dict]] = {}

    for r in records:
        if str(r.get("type") or "A").upper() == "A" and r.get("fqdn"):
            by_fqdn.setdefault(str(r["fqdn"]).lower().strip("."), []).append(r)

    out: dict[str, dict] = {}

    for c in configs:
        label = str(c.get("tunnel") or "").strip()

        if label and label in by_name:
            out[c["name"]] = {"tunnel": by_name[label]["id"], "how": "label"}
            continue

        addr, port = str(c.get("address") or "").lower().strip("."), int(c.get("port") or 0)

        if not addr:
            continue

        sids, origin, how = set(), set(), "address"

        if addr in by_host:
            sids.add(by_host[addr])
        elif addr in by_sname:
            sids.add(by_sname[addr])
        else:
            for r in by_fqdn.get(addr, []):
                for ip in r.get("ips") or []:
                    if ip in by_host:
                        sids.add(by_host[ip])
                        how = "dns"

                if r.get("port"):
                    origin.add(int(r["port"]))                  # a CDN record sends to its own origin port: THAT is the port the tunnel forwards (the port of the config is the CDN's)

        if not sids:
            continue

        ports = origin or ({port} if port else set())
        hits = [t for t in tunnels if t["iran_id"] in sids and t["ports"] & ports]

        if len(hits) == 1:
            out[c["name"]] = {"tunnel": hits[0]["id"], "how": how}

    return out


def config_stats(samples: list[tuple], now: int, ref: float | None = None) -> dict:
    """Pure: what the tests of one config say. samples: [(ts, up, ping_ms)] of the last two hours (any order); `ref`: its usual ping (None when not known).
    -> {"n", "up_share" (None under MIN_RECENT tests), "ping" (the middle of the last six answered pings), "ref", "ratio"}."""
    recent = sorted((s for s in samples if now - int(s[0]) <= RECENT), key=lambda s: s[0])
    up_share = sum(1 for s in recent if s[1]) / len(recent) if len(recent) >= MIN_RECENT else None
    pings = [float(s[2]) for s in recent if s[1] and s[2] is not None][-6:]
    ping = statistics.median(pings) if len(pings) >= 3 else None
    return {"n": len(recent), "up_share": up_share, "ping": ping, "ref": ref, "ratio": round(ping / ref, 2) if ping and ref else None}


def config_issue(status: str, status_for: int, stats: dict, speed: dict | None = None) -> tuple[str, str]:
    """Pure: (issue, one sentence) of one config - good · down · unstable · slow_ping · slow_speed · unknown. `speed`: {"down", "ref", "age"} (the last speed test, the usual one, seconds since)."""
    if status == "down" and status_for >= DOWN_AFTER:
        return "down", f"does not connect (for {max(1, status_for // 60)} minutes)"

    if status not in ("up", "down"):
        return "unknown", ""

    share = stats.get("up_share")

    if share is not None and share < UNSTABLE_UNDER:
        return "unstable", f"answered only {share * 100:.0f} % of its tests in the last 2 hours"

    ping, ref = stats.get("ping"), stats.get("ref")

    if ping and ref and ping >= PING_RATIO * ref and ping - ref >= PING_EXTRA_MS:
        return "slow_ping", f"ping {ping:.0f} ms (usually {ref:.0f})"

    if speed and speed.get("down") is not None and speed.get("ref") and speed["ref"] >= 5 and speed.get("age", 1e9) <= SPEED_FRESH and speed["down"] < SPEED_RATIO * speed["ref"]:
        return "slow_speed", f"speed {speed['down']:.0f} Mb/s (usually {speed['ref']:.0f})"

    if share is None and ping is None:
        return "unknown", ""

    return "good", ""


def tunnel_ux(items: list[tuple[str, str, str]]) -> dict:
    """Pure: the grade of a tunnel from its configs. items: [(config name, issue, sentence)]. good · fair (under half have a problem) · poor (half or more) · bad (every one of them is down or unstable) · unknown."""
    known = [i for i in items if i[1] != "unknown"]
    bad = [i for i in known if i[1] != "good"]

    if not known:
        grade = "unknown"
    elif len(bad) == len(known):
        grade = "bad" if all(i[1] in ("down", "unstable") for i in bad) else "poor"        # every config down or unstable: nobody gets through; every one only slow: poor
    elif len(bad) * 2 >= len(known):
        grade = "poor"
    elif bad:
        grade = "fair"
    else:
        grade = "good"

    return {"grade": grade, "n": len(items), "known": len(known), "bad": [{"config": b[0], "issue": b[1], "text": b[2]} for b in bad]}


def settle_grade(old: str, new: str, pend: dict | None) -> tuple[str, dict]:
    """Pure: the grade of a tunnel changes only when CONFIRM looks in a row say the same new grade (a tunnel with no grade yet takes its first at once)."""
    pend = dict(pend or {})

    if pend.get("grade") == new:
        pend["n"] = int(pend.get("n", 0)) + 1
    else:
        pend = {"grade": new, "n": 1}

    if new == old or old in ("", None):
        return new, pend

    return (new, pend) if pend["n"] >= CONFIRM else (old, pend)


def summary(ux: dict) -> str:
    """Pure: one sentence of a tunnel's users, for the Doctor's page and the events."""
    bad = ux.get("bad") or []
    head = f"{len(bad)} of the {ux.get('known', len(bad))} configs through it " + ("have a problem" if len(bad) != 1 else "has a problem")
    return head + ": " + "; ".join(f"{b['config']} {b['text']}" for b in bad[:3]) + (f"; and {len(bad) - 3} more" if len(bad) > 3 else "") + "."


def explain_users(f: dict) -> tuple[str, str]:
    """Pure: (a short code, ONE sentence) - where the problem of a tunnel whose users suffer most likely is. `f`: the grade, the number of configs, the tunnel's own health (state, bandwidth state,
    loss, delay against its usual) and the load of the servers. The tunnel first: when it is fine, the problem is beyond it."""
    n_bad, n = f.get("n_bad", 0), f.get("known", 0)
    what = f"{n_bad} of the {n} configs through it" if n != 1 else "the config through it"

    if f.get("state") not in (None, "up"):
        return "tunnel_down", f"The tunnel itself is {f['state']}: that is what its users feel."

    if f.get("bw_state") == "drop":
        return "tunnel_slow", f"The bandwidth of the tunnel dropped ({f.get('bw_text', '')}): that is what its users feel - see the look at the bandwidth."

    for side in ("iran", "foreign"):
        s = f.get(side) or {}

        if (s.get("cpu") or 0) >= 85:
            return f"{side}_busy", f"The {side} server {s.get('name', '')} is busy (CPU {s['cpu']:.0f} %): everything on it is slow, the users of this tunnel too."

    if (f.get("loss") or 0) >= 5:
        return "tunnel_loss", f"The line of the tunnel loses {f['loss']:.0f} % of its packets: its users feel it."

    rtt, ref = f.get("rtt"), f.get("rtt_ref")

    if rtt is not None and ref and rtt >= 1.5 * ref:
        return "tunnel_delay", f"The delay of the tunnel rose from {ref:.0f} to {rtt:.0f} ms: its users feel it."

    return "beyond", (f"The tunnel is healthy (it carries {f.get('bw_text') or 'its usual speed'}, its line loses nothing, its delay is as usual) but {what} {'has' if n == 1 else 'have'} a problem: "
                      "the problem is BEYOND the tunnel - the foreign server's own internet or its Xray, or the CDN in front of the Iran server.")


# ------------------------------------------------------------------ reading the panel
def _ref_ping(db, c: Config, now: int) -> float | None:
    """The usual ping of a config: the middle of its answered pings of the last week without the last two hours. Worked out again once an hour and kept in its meta."""
    q = dict((c.meta or {}).get("q") or {})

    if now - int(q.get("at", 0)) < REF_REFRESH:
        return q.get("ping_ref")

    vals = [r[0] for r in db.execute(select(ConfigSample.ping_ms).where(ConfigSample.config_id == c.id, ConfigSample.ts >= now - REF_WINDOW, ConfigSample.ts < now - RECENT, ConfigSample.up.is_(True),
                                                                          ConfigSample.ping_ms.is_not(None)))]
    ref = statistics.median(vals) if len(vals) >= MIN_REF else None
    c.meta = {**(c.meta or {}), "q": {"ping_ref": ref, "at": now}}
    return ref


def _speed_of(c: Config, now: int) -> dict | None:
    hist = [h for h in ((c.meta or {}).get("speed_hist") or []) if h and h[1] is not None]

    if not c.speed_at or c.down_mbps is None or len(hist) < MIN_SPEEDS:
        return None

    vals = sorted(float(h[1]) for h in hist)
    ref = vals[round(0.75 * (len(vals) - 1))]
    try:
        import datetime

        age = now - int(datetime.datetime.fromisoformat(str(c.speed_at)).timestamp())
    except ValueError:
        return None

    return {"down": float(c.down_mbps), "ref": ref, "age": age}


def _placement(db) -> tuple[list[Config], dict[str, dict]]:
    """The enabled configs and the tunnel each one rides (see `map_configs`)."""
    tunnels = [{"id": t.id, "name": t.name, "iran_id": t.iran_server_id, "ports": {int(p) for p in (t.forwarded_ports or []) if str(p).isdigit()}}
               for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name)]
    servers = [{"id": s.id, "name": s.name, "host": s.host} for s in db.scalars(select(Server).where(Server.role == "iran"))]
    cfgs = list(db.scalars(select(Config).where(Config.enabled.is_(True))))
    by_cfg = [{"name": c.name, "tunnel": c.tunnel, "address": (c.meta or {}).get("address", ""), "port": (c.meta or {}).get("port", 0)} for c in cfgs]
    return cfgs, map_configs(by_cfg, tunnels, servers, list(dnscache.CACHE.get("rows") or []))


def read_all(now: int | None = None) -> dict[int, dict]:
    """{tunnel id: ux} for every enabled tunnel that has at least one config riding it (a pure read)."""
    now = now or int(time.time())

    with session() as db:
        cfgs, placed = _placement(db)
        out: dict[int, list] = {}

        for c in cfgs:
            p = placed.get(c.name)

            if p is None:
                continue

            samples = [(r[0], bool(r[1]), r[2]) for r in db.execute(select(ConfigSample.ts, ConfigSample.up, ConfigSample.ping_ms).where(ConfigSample.config_id == c.id, ConfigSample.ts >= now - RECENT))]
            stats = config_stats(samples, now, _ref_ping(db, c, now))
            issue, text = config_issue(c.status, now - int(c.status_since or now), stats, _speed_of(c, now))
            out.setdefault(p["tunnel"], []).append((c.name, issue, text))

    return {tid: tunnel_ux(items) for tid, items in out.items()}


def linked_state() -> dict:
    """The LINKS right now: how many configs ride a known tunnel and the names of those that do not (the Doctor cannot say what their users get through a tunnel until its configs are tied to it)."""
    with session() as db:
        cfgs, placed = _placement(db)

    return {"total": len(cfgs), "linked": len(placed), "unlinked": sorted(c.name for c in cfgs if c.name not in placed)[:300]}


# ------------------------------------------------------------------ the job
def run() -> None:
    """Job (every five minutes): the grade of every tunnel that has configs; one event when it changes; a look by the Doctor when it falls to poor or bad."""
    if jobs.stopping():
        return

    with _LOCK:
        if _RUNNING[0]:
            return

        _RUNNING[0] = True

    try:
        if time.time() - float(dnscache.CACHE.get("at") or 0) > DNS_FRESH:
            dnscache.refresh_in_background()                 # (the records of the DNS accounts: the answer is there at the next look)

        now = int(time.time())
        looked = []

        for tid, ux in read_all(now).items():
            change = _store(tid, ux, now)

            if change and change[1] in ("poor", "bad") and change[0] in ("good", "fair", "unknown", ""):
                looked.append(tid)

        for tid in looked:
            _investigate_later(tid)
    finally:
        with _LOCK:
            _RUNNING[0] = False


def _store(tunnel_id: int, ux: dict, now: int) -> tuple[str, str] | None:
    """Keep the grade in the record of the tunnel (with the hysteresis) and write the event of a change; returns (old grade, new grade) when it changed."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            return None

        rec = dict(t.record or {})
        old = dict(rec.get("ux") or {})
        grade, pend = settle_grade(old.get("grade", ""), ux["grade"], old.get("pend"))
        new = {**ux, "grade": grade, "pend": pend, "at": now, "since": old.get("since", now) if grade == old.get("grade") else now}

        if grade != ux["grade"]:                       # not confirmed yet: the tunnel keeps its grade and its sentence
            new["bad"], new["n"], new["known"] = old.get("bad", []), old.get("n", ux["n"]), old.get("known", ux["known"])

        rec["ux"] = new
        t.record = rec
        label = f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"

        if grade == old.get("grade", ""):
            return None

        if grade in ("poor", "bad"):
            emit(db, "bad" if grade == "bad" else "warn", "tunnels", f"Tunnel {label}: the users get a {grade} connection - {summary(new)}", t.name)
        elif old.get("grade") in ("poor", "bad") and grade in ("good", "fair"):
            emit(db, "ok", "tunnels", f"Tunnel {label}: the users get a good connection again.", t.name)

        return old.get("grade", ""), grade


# ------------------------------------------------------------------ the look at suffering users
def _facts(tunnel_id: int) -> dict:
    from . import bandwidth

    f = bandwidth._facts(tunnel_id)                    # the tunnel's own health: servers' load, loss, delay, what the line carries

    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        ux = dict((t.record or {}).get("ux") or {})
        bw = dict((t.record or {}).get("bw") or {})
        state = t.state

    return {**f, "state": state, "n_bad": len(ux.get("bad") or []), "known": ux.get("known", 0), "bw_state": bw.get("state"),
            "bw_text": f"{bw['down']:.0f} Mb/s" if bw.get("down") is not None else "", "ux": ux}


def investigate(aid: int, tunnel_id: int) -> None:
    """The story of the look (the Doctor's Live window): the users, the tunnel, where the problem is."""
    from . import autopilot

    f = _facts(tunnel_id)
    say = lambda line: autopilot._say(aid, line)       # (one short line for the story)
    say(f"{f['name']}: " + summary(f["ux"]))

    for b in (f["ux"].get("bad") or [])[:6]:
        say(f"✕ {b['config']}: {b['text']}")

    say(("✓ " if f["state"] == "up" else "✕ ") + f"The tunnel is {f['state']}")

    if f.get("now") is not None:
        say(("✕ " if f["bw_state"] == "drop" else "✓ ") + f"Its bandwidth is {f['now']:.0f} Mb/s" + (f", usually {f['ref']:.0f}" if f.get("ref") else ""))

    for side in ("iran", "foreign"):
        s = f[side]
        say(("✕ " if (s["cpu"] or 0) >= 85 else "✓ ") + f"The {side} server {s['name']}: " + (f"CPU {s['cpu']:.0f} %" if s["cpu"] is not None else "no reading of its load just now"))

    say(("✕ " if (f["loss"] or 0) >= 5 else "✓ ") + (f"The line loses {f['loss']:.0f} % of its packets" if f["loss"] is not None else "The loss of the line cannot be measured here (the provider drops pings); the data passes"))
    code, sentence = explain_users(f)

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            ux = dict(rec.get("ux") or {})
            ux["why"] = {"code": code, "text": sentence, "at": int(time.time())}
            rec["ux"] = ux
            t.record = rec
            emit(db, "warn", "tunnels", f"Tunnel {t.name}: its users suffer - {sentence}", t.name)

    say("Where the problem is: " + sentence)
    autopilot._say(aid, "The Doctor keeps looking every five minutes and says when the users are fine again.", done=True, ok=True)


def _investigate_later(tunnel_id: int) -> None:
    from . import autopilot

    try:
        with session() as db:
            t = db.get(Tunnel, tunnel_id)
            name = t.name if t is not None else ""

        if name:
            autopilot._start(name, investigate, tunnel_id, action="speed", source="auto", allow_read_only=True)
    except ValueError as e:
        log.info("look at the users of tunnel %s not started: %s", tunnel_id, str(e)[:100])
