"""The bandwidth of a tunnel, kept fresh: what it can carry TODAY, measured often and lightly, so that a drop is seen in minutes, understood and told in plain words.
(The owner, 2026-10-16: "show the bandwidth of the tunnel very simply - and when it drops, the Doctor checks it by itself and we understand".)

  how it is measured   a GRE tunnel (Backhaul): TWO connections pull 60 MB each at most (3 s) over the link from a small sink the panel keeps on the foreign server, bound to the link's inner address (nothing public);
                       a tunnel with a test door (BackPack, Paqet, Chisel): the quality job of every half hour already measures it through the door - its number is taken from there.
  where it is kept     the record of the tunnel, `bw`: the last number, the history (60 samples), the usual level (`ref`), a verdict (learning / ok / low / drop) and, while it lasts, the drop.
  what is a drop       a sample under 60 % of the usual level (the 75th percentile of the last 7 days), confirmed by a second sample three minutes later; it ends when a sample is back over 80 %.
  what the Doctor does  it looks, by itself, at what could explain it (the servers' load, the other tunnels of the same servers, the line's loss and delay, the users on the tunnel) and writes the
                       one sentence that says where the problem is (`explain`) - in the story of the look, in an event and on the Doctor's page. Nothing is repaired by it yet: it tells.

The pure helpers (`reference`, `verdict`, `add_sample`, `explain`) carry the decisions and are tested."""
from __future__ import annotations

import secrets
import threading
import time

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ServerSample, Tunnel, TunnelCheck, is_temp
from ..ssh import Node, SSHError, run_action, run_script
from .events import emit
from .tunnels import link_of

log = get_logger("bandwidth")
INTERVAL = 90 * 60           # between two measurements of one tunnel (a little different for each, so they do not all fall in one minute)
CONFIRM_AFTER = 180          # a sample that looks like a drop is measured again after this
WATCH_AFTER = 20 * 60        # while a drop lasts the tunnel is measured this often (to see it end)
RETRY_AFTER = 15 * 60        # a tunnel that could not be measured is left alone this long
REF_WINDOW = 7 * 86400       # the usual level is what the last week gave
REF_MIN = 4                  # samples needed before anything is judged
MIN_REF_MBPS = 20.0          # a tunnel whose usual level is under this is too slow to tell a drop from noise
DROP_RATIO = 0.6             # under this share of the usual level: a drop
LOW_RATIO = 0.8              # a drop ends over this share
KEEP = 60                    # samples kept
LINK_PORT = 39777            # the sink of a GRE tunnel listens on its inner address (never a public one) on this port
BYTES = 60_000_000           # each of the two connections pulls this much at most ...
SECS = 3                     # ... and for 3 s at most (real tests, 2026-10-16, over a GRE link: one connection scattered by 16 % from run to run, two by 9 %, four by 7 % - and one connection of 4 s stalled once)
STREAMS = 2
BUSY_CPU = 85.0              # a server this busy is not measured (the test would be slowed by it, and would slow the users more)
_LOCK = threading.Lock()
_RUNNING: set[int] = set()


# ------------------------------------------------------------------ pure helpers (tested)
def reference(hist: list, now: int) -> float | None:
    """The usual level of a tunnel: the 75th percentile of what the last week measured (what it gives on a normal day, not its best minute and not its worst). None until REF_MIN samples exist."""
    vals = sorted(float(x[1]) for x in hist if x and x[1] is not None and now - int(x[0]) <= REF_WINDOW)

    if len(vals) < REF_MIN:
        return None

    return vals[round(0.75 * (len(vals) - 1))]


def verdict(down: float | None, ref: float | None) -> str:
    """none (nothing measured) · learning (too few samples) · ok · low (under 80 %) · drop (under 60 %)."""
    if down is None:
        return "none"

    if ref is None:
        return "learning"

    if ref < MIN_REF_MBPS:
        return "ok"

    ratio = down / ref
    return "drop" if ratio < DROP_RATIO else "low" if ratio < LOW_RATIO else "ok"


def add_sample(bw: dict | None, now: int, down: float, up: float | None, how: str, ping: float | None = None) -> tuple[dict, str]:
    """Pure: (the new `bw` of the tunnel, what changed: "" | "drop" | "recovered") after one sample. The sample is judged against the level BEFORE it, so a drop never pulls its own reference down."""
    bw = dict(bw or {})
    hist = [list(x) for x in (bw.get("hist") or []) if now - int(x[0]) <= REF_WINDOW]
    ref_before = reference(hist, now)
    v = verdict(down, ref_before)
    drop, suspect, change = bw.get("drop"), bool(bw.get("suspect")), ""
    next_at = now + INTERVAL

    if v == "drop":
        if suspect or drop:
            if not drop:
                change = "drop"
                drop = {"since": now, "from": round(ref_before or 0.0, 1), "now": round(down, 1)}
            else:
                drop = {**drop, "now": round(down, 1)}

            suspect, next_at = False, now + WATCH_AFTER
        else:
            suspect, next_at = True, now + CONFIRM_AFTER           # the first sample under the line: measured again soon before anybody is told
    else:
        suspect = False

        if drop and v == "ok":
            drop, change = None, "recovered"
        elif drop:
            drop = {**drop, "now": round(down, 1)}
            next_at = now + WATCH_AFTER

    hist.append([now, round(down, 1), None if up is None else round(up, 1)])
    hist = hist[-KEEP:]
    state = "drop" if drop else v if v in ("low", "learning") else "ok"
    return {"down": round(down, 1), "up": None if up is None else round(up, 1), "at": now, "how": how, "ping": ping, "ref": reference(hist, now), "state": state, "drop": drop, "suspect": suspect,
            "next_at": next_at, "hist": hist}, change


def explain(f: dict) -> tuple[str, str]:
    """Pure: (a short code, ONE sentence) - where the problem of a tunnel whose bandwidth dropped most likely is. `f` holds what the Doctor found: now / ref (Mb/s), the CPU of the two servers, how many tunnels
    of the same Iran / foreign server dropped too, the loss and delay of the line (against its usual delay), the traffic the tunnel carries now. The first rule that fits wins, the cheapest explanation first."""
    now, ref = float(f.get("now") or 0), float(f.get("ref") or 0)

    for side, label in (("iran", "Iran"), ("foreign", "foreign")):
        s = f.get(side) or {}

        if (s.get("cpu") or 0) >= BUSY_CPU:
            return f"{side}_busy", f"The {label} server {s.get('name', '')} is busy (CPU {s['cpu']:.0f} %): everything on it is slow, not only this tunnel."

    same = f.get("same_iran") or {}

    if same.get("total", 0) >= 2 and same.get("dropped", 0) >= max(2, 0.6 * same["total"]):
        return "iran_wide", f"{same['dropped']} of the {same['total']} tunnels of the Iran server {(f.get('iran') or {}).get('name', '')} dropped together: its own line or its provider is the cause, not this tunnel."

    same = f.get("same_foreign") or {}

    if same.get("total", 0) >= 2 and same.get("dropped", 0) >= max(2, 0.6 * same["total"]):
        return "foreign_wide", f"{same['dropped']} of the {same['total']} tunnels that end on {(f.get('foreign') or {}).get('name', '')} dropped together: that foreign server or its provider is the cause."

    live = f.get("live")

    if live is not None and now > 0 and live >= 0.7 * now:
        return "full", f"The tunnel carries {live:.0f} Mb/s right now of the {now:.0f} it can give: it is full of users, nothing is broken."

    loss = f.get("loss")

    if loss is not None and loss >= 5:
        return "loss", f"The line loses {loss:.0f} % of its packets: the route got worse."

    rtt, rtt_ref = f.get("rtt"), f.get("rtt_ref")

    if rtt is not None and rtt_ref and rtt >= 1.5 * rtt_ref:
        return "latency", f"The delay rose from {rtt_ref:.0f} to {rtt:.0f} ms: the route changed or is congested."

    return "this_only", (f"Only this tunnel is slower ({now:.0f} Mb/s, usually {ref:.0f}): the servers are idle, the line loses nothing and its delay is as usual. "
                         "A restart of the tunnel, or another tunnel type for this pair, may bring the speed back.")


# ------------------------------------------------------------------ storing a sample
def _store(tunnel_id: int, down: float, up: float | None, how: str, ping: float | None) -> tuple[dict, str] | None:
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            return None

        rec = dict(t.record or {})
        bw, change = add_sample(rec.get("bw"), now, down, up, how, ping)

        if bw["next_at"] == now + INTERVAL:               # the usual rhythm: a few minutes different for every tunnel, so that they do not all fall in one minute
            bw["next_at"] += (tunnel_id * 97) % 600

        rec["bw"] = bw
        t.record = rec
        name, label = t.name, f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"

        if change == "drop":
            emit(db, "warn", "tunnels", f"Tunnel {label}: the bandwidth dropped to {bw['down']} Mb/s (usually {bw['drop']['from']}). The Doctor is looking at why.", name)
        elif change == "recovered":
            emit(db, "ok", "tunnels", f"Tunnel {label}: the bandwidth is back, {bw['down']} Mb/s.", name)

    return bw, change


def note_quality(tunnel_id: int, q: dict) -> None:
    """The quality job measured a tunnel with a test door (8 connections, 4 s): its download is a sample of the bandwidth too, so a BackPack / Paqet / Chisel tunnel needs no second test."""
    if q.get("down_mbps") is None:
        return

    res = _store(tunnel_id, float(q["down_mbps"]), None if q.get("up_mbps") is None else float(q["up_mbps"]), "door", q.get("latency_ms"))

    if res and res[1] == "drop":
        _investigate_later(tunnel_id)


# ------------------------------------------------------------------ measuring a GRE tunnel
def _target(t: Tunnel) -> dict | None:
    """How a tunnel is measured by this module: {"how": "link", "peer": inner address of the foreign end}; None when it has no GRE link (the doors are measured by the quality job)."""
    iface, peer = link_of(t)

    if t.type in ("backhaul", "gre") and iface and peer and (t.foreign_tunnel_ip or "") == peer:
        return {"how": "link", "peer": peer}

    return None


def _ensure_sink(tunnel_id: int, foreign: Node, peer: str, key: str, force: bool = False) -> None:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        have = (t.record or {}).get("bw_sink") if t is not None else None

    if have and have.get("ip") == peer and not force:
        return

    out = run_action(foreign, "sink_install", {"NAME": f"bw{tunnel_id}", "PORT": str(LINK_PORT), "KEY": key, "BIND": peer}, timeout=60)

    if "SINK_OK" not in out:
        raise RuntimeError("the bandwidth sink did not start on the foreign server: " + out.strip()[-120:])

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            t.record = {**(t.record or {}), "bw_sink": {"name": f"bw{tunnel_id}", "ip": peer, "port": LINK_PORT}}


def _server_busy(server_id: int, now: int) -> bool:
    with session() as db:
        s = db.scalar(select(ServerSample).where(ServerSample.server_id == server_id, ServerSample.ts >= now - 360).order_by(ServerSample.ts.desc()).limit(1))
        return bool(s is not None and (s.cpu or 0) >= BUSY_CPU)


def measure(tunnel_id: int) -> tuple[dict, str]:
    """One light measurement of a tunnel (read: ValueError when it cannot be measured now). Returns (the new `bw`, what changed)."""
    from . import build_other

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        has_door = bool((t.record or {}).get("test_port") and (t.secrets or {}).get("test_key"))
        target = _target(t)
        state = t.state
        iran, foreign = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port)
        iran_id, foreign_id = t.iran_server_id, t.foreign_server_id
        key = str((t.secrets or {}).get("bw_key") or "")

    if has_door:                                          # BackPack / Paqet / Chisel: the quality job measures through the door; this is the same, on demand
        from . import tunnel_test

        tunnel_test.quality(tunnel_id)                    # (it calls note_quality)

        with session() as db:
            bw = dict((db.get(Tunnel, tunnel_id).record or {}).get("bw") or {})

        return bw, ""

    if target is None:
        raise ValueError("this tunnel has no GRE link or test door to measure")

    if state != "up":
        raise ValueError("the tunnel is not up")

    now = int(time.time())

    if _server_busy(iran_id, now) or _server_busy(foreign_id, now):
        raise ValueError("a server of this tunnel is busy right now")

    if get_settings().read_only:
        raise ValueError("the panel is read-only: the bandwidth sink cannot be put on the foreign server")

    if not key:
        key = secrets.token_hex(8)

        with session() as db:
            t = db.get(Tunnel, tunnel_id)
            t.secrets = {**(t.secrets or {}), "bw_key": key}

    _ensure_sink(tunnel_id, foreign, target["peer"], key)
    params = {"PORT": str(LINK_PORT), "SECS": str(SECS), "STREAMS": str(STREAMS), "PINGS": "5", "KEY": key, "TO": "2.0", "HOST": target["peer"], "BYTES": str(BYTES), "DIR": "d"}

    try:
        d = build_other.parse_probe(run_script(iran, "probe_run", params, timeout=45))

        if d.get("error") or d.get("down_mbps") is None:
            raise RuntimeError(str(d.get("error") or "no answer from the sink"))
    except (RuntimeError, SSHError):                      # the sink may have died with a reboot of the foreign server: put it back once and ask again
        _ensure_sink(tunnel_id, foreign, target["peer"], key, force=True)
        d = build_other.parse_probe(run_script(iran, "probe_run", params, timeout=45))

        if d.get("error") or d.get("down_mbps") is None:
            raise RuntimeError("the sink does not answer through the link: " + str(d.get("error") or "no data")[:100])

    res = _store(tunnel_id, float(d["down_mbps"]), None, "link", d.get("latency_ms"))

    if res is None:
        raise ValueError("tunnel not found")

    return res


def remove_sink(tunnel_id: int, foreign: Node, rec: dict) -> None:
    """The sink of a tunnel that is removed goes with it (best effort: the file is purged when the server is back)."""
    if not (rec or {}).get("bw_sink"):
        return

    try:
        run_action(foreign, "bw_sink_remove", {"NAME": f"bw{tunnel_id}"}, timeout=40)
    except (SSHError, ValueError) as e:
        log.info("bandwidth sink of tunnel %s not removed: %s", tunnel_id, str(e)[:100])


# ------------------------------------------------------------------ the job
def _due(now: int) -> list[int]:
    """The tunnels to measure, the one that waited longest first: up, with a GRE link, not measured for INTERVAL (or asked for sooner by `next_at`)."""
    out = []

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), Tunnel.state == "up")):
            if is_temp(t.name) or _target(t) is None or t.iran.online is False or t.foreign.online is False:
                continue

            bw = (t.record or {}).get("bw") or {}
            nxt = int(bw.get("next_at") or 0) or (int(bw.get("at") or 0) + INTERVAL + (t.id * 97) % 600)

            if now >= nxt:
                out.append((int(bw.get("at") or 0), t.id))

    return [i for _, i in sorted(out)]


def _failed(tunnel_id: int, why: str) -> None:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            bw = dict(rec.get("bw") or {})
            bw["next_at"], bw["error"] = int(time.time()) + RETRY_AFTER, why[:120]
            rec["bw"] = bw
            t.record = rec


def run() -> None:
    """Job (every minute): ONE tunnel is measured per turn - the one that waited longest - so the servers feel a few seconds of one connection, never a burst."""
    if get_settings().read_only or jobs.stopping():
        return

    with _LOCK:
        if _RUNNING:
            return

    ids = _due(int(time.time()))

    if not ids:
        return

    tid = ids[0]

    with _LOCK:
        _RUNNING.add(tid)

    try:
        bw, change = measure(tid)
        log.info("bandwidth of tunnel %s: %s Mb/s (usual %s) %s", tid, bw.get("down"), bw.get("ref"), bw.get("state"))

        if change == "drop":
            _investigate_later(tid)
    except (ValueError, RuntimeError, SSHError) as e:
        log.info("bandwidth of tunnel %s not measured: %s", tid, str(e)[:140])
        _failed(tid, str(e))
    finally:
        with _LOCK:
            _RUNNING.discard(tid)


# ------------------------------------------------------------------ the look at a drop
def _facts(tunnel_id: int) -> dict:
    """What the Doctor can read about a drop, all of it already on the panel (no server is asked)."""
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        bw = dict((t.record or {}).get("bw") or {})
        d = dict(t.detail or {})
        loss = d.get("loss_pct")
        live = t.rx_mbps                                   # what comes to the Iran server through the link now: the direction the bandwidth is measured in
        rtts = sorted(c.rtt_ms for c in db.scalars(select(TunnelCheck).where(TunnelCheck.tunnel_id == t.id, TunnelCheck.ts >= now - 7 * 86400, TunnelCheck.ok.is_(True))) if c.rtt_ms is not None)
        sides = {}

        for side, srv in (("iran", t.iran), ("foreign", t.foreign)):
            s = db.scalar(select(ServerSample).where(ServerSample.server_id == srv.id, ServerSample.ts >= now - 600).order_by(ServerSample.ts.desc()).limit(1))
            sides[side] = {"name": srv.name, "cpu": float(s.cpu or 0) if s is not None else None}

        def share(col, sid: int) -> dict:
            rows = [x for x in db.scalars(select(Tunnel).where(col == sid, Tunnel.enabled.is_(True))) if not is_temp(x.name)]
            return {"total": len(rows), "dropped": sum(1 for x in rows if ((x.record or {}).get("bw") or {}).get("state") == "drop")}

        return {"name": t.name, "now": bw.get("down"), "ref": (bw.get("drop") or {}).get("from") or bw.get("ref"), "loss": loss, "live": live, "iran": sides["iran"], "foreign": sides["foreign"],
                "rtt": t.rtt_ms, "rtt_ref": rtts[len(rtts) // 2] if len(rtts) >= 20 else None, "same_iran": share(Tunnel.iran_server_id, t.iran_server_id), "same_foreign": share(Tunnel.foreign_server_id, t.foreign_server_id)}


def investigate(aid: int, tunnel_id: int, finish: bool = True) -> None:
    """The story of the look (the Doctor's Live window shows it): what dropped, what was checked, where the problem is."""
    from . import autopilot

    f = _facts(tunnel_id)
    say = lambda line: autopilot._say(aid, line)       # (one short line for the story)
    say(f"{f['name']}: the bandwidth dropped to {f['now']} Mb/s; it usually gives {f['ref']}.")

    for side in ("iran", "foreign"):
        s = f[side]
        say(("✕ " if (s["cpu"] or 0) >= BUSY_CPU else "✓ ") + f"The {side} server {s['name']}: " + (f"CPU {s['cpu']:.0f} %" if s["cpu"] is not None else "no reading of its load just now"))

    for key, label in (("same_iran", f["iran"]["name"]), ("same_foreign", f["foreign"]["name"])):
        s = f[key]
        say(("✕ " if s["total"] >= 2 and s["dropped"] >= 2 else "✓ ") + f"Tunnels of {label}: {s['dropped']} of {s['total']} are dropping" + (" (this one counts)" if s["dropped"] else ""))

    say(("✕ " if (f["loss"] or 0) >= 5 else "✓ ") + ("The line loses " + f"{f['loss']:.0f} % of its packets" if f["loss"] is not None else "The loss of the line cannot be measured here (the provider drops pings); the data passes"))

    if f["rtt"] is not None:
        say(("✕ " if f["rtt_ref"] and f["rtt"] >= 1.5 * f["rtt_ref"] else "✓ ") + f"The delay is {f['rtt']:.0f} ms" + (f" (usually {f['rtt_ref']:.0f})" if f["rtt_ref"] else ""))

    if f["live"] is not None:
        say(f"Users are carrying {f['live']:.0f} Mb/s through it right now.")

    code, sentence = explain(f)

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            bw = dict(rec.get("bw") or {})
            bw["why"] = {"code": code, "text": sentence, "at": int(time.time())}
            rec["bw"] = bw
            t.record = rec
            emit(db, "warn", "tunnels", f"Tunnel {t.name}: the bandwidth dropped - {sentence}", t.name)

    say("Where the problem is: " + sentence)

    if finish:
        autopilot._say(aid, "The Doctor keeps measuring this tunnel every 20 minutes and says when the bandwidth is back.", done=True, ok=True)


def measure_start(tunnel_id: int) -> int:
    """The Measure button: one measurement as an action (follow /api/actions/{id}); a drop it finds is looked at in the same story."""
    from . import autopilot

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        name = t.name

    def work(aid: int) -> None:
        autopilot._say(aid, "Measuring the bandwidth: two light connections, 120 MB at most (a tunnel with a test door: its quality test)…")
        bw, change = measure(tunnel_id)
        ref = bw.get("ref")
        autopilot._say(aid, f"Bandwidth: {bw.get('down')} Mb/s" + (f", usually {ref:.0f}" if ref else ", the usual level is still being learnt") + f" - {bw.get('state')}.")

        if change == "drop" or bw.get("state") == "drop":
            investigate(aid, tunnel_id)
        else:
            autopilot._say(aid, "Done.", done=True, ok=True)

    return autopilot._start(name, work, action="speed", allow_read_only=True)


def _investigate_later(tunnel_id: int) -> None:
    """Start the look at a confirmed drop as a story of the Doctor (not twice at once, never for a tunnel whose repair is running)."""
    from . import autopilot

    try:
        with session() as db:
            t = db.get(Tunnel, tunnel_id)
            name = t.name if t is not None else ""

        if name:
            autopilot._start(name, investigate, tunnel_id, action="speed", source="auto", allow_read_only=True)
    except ValueError as e:
        log.info("look at the drop of tunnel %s not started: %s", tunnel_id, str(e)[:100])
