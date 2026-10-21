"""The bandwidth of a tunnel, kept fresh: what it can carry TODAY, measured often and lightly, so that a drop is seen in minutes, understood and told in plain words.
(The owner, 2026-10-16: "show the bandwidth of the tunnel very simply - and when it drops, the Doctor checks it by itself and we understand".)

  how it is measured   a GRE tunnel (Backhaul): TWO connections pull 60 MB each at most (3 s) over the link from a small sink the panel keeps on the foreign server, bound to the link's inner address (nothing public);
                       a tunnel with a test door (BackPack, Paqet, Chisel): the quality job of every half hour already measures it through the door - its number is taken from there.
  where it is kept     the record of the tunnel, `bw`: the last number, the history (60 samples), the usual level (`ref`), a verdict (learning / ok / low / drop) and, while it lasts, the drop.
  what is a drop       a sample under 60 % of the usual level (the 75th percentile of the last 7 days), confirmed by a second sample three minutes later; it ends when a sample is back over 80 %.
  what is "slow"       a tunnel that has no usual level to drop from (new, rebuilt, or one that never gave 20 Mb/s) whose test gives under 20 Mb/s twice in a row (the second a minute later): it is looked at like a drop,
                       and what the Doctor's memory of the pair knows - a type that gave clearly more here - is written on the Doctor page and, in the modes fix and move, done (1.69.17). It ends when a test gives 32 Mb/s.
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
from ..models import ServerSample, Tunnel, TunnelCheck, is_standby, is_temp
from ..ssh import Node, SSHError, run_action, run_script
from . import livebw
from .events import emit
from .tunnels import link_of

log = get_logger("bandwidth")
INTERVAL = 90 * 60           # between two measurements of one tunnel (a little different for each, so they do not all fall in one minute)
CONFIRM_AFTER = 180          # a sample that looks like a drop is measured again after this
SLOW_CONFIRM_AFTER = 60      # the second test of a very slow one comes this soon (a drop keeps its 3 minutes): the owner, 2026-10-19: "the process must not take long"
POOR_SCORE = 40             # a tunnel whose quality test scored under this is measured every POOR_AFTER, not every INTERVAL
POOR_AFTER = 10 * 60
WATCH_AFTER = 20 * 60       # while a drop lasts the tunnel is measured this often (to see it end)
RETRY_AFTER = 15 * 60        # a tunnel that could not be measured is left alone this long
RETRY_FIRST = 90             # ... but a tunnel that has NO capacity yet (a new one) is tried again soon: its row shows a dash until the first test (the owner, 2026-10-21)
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
METHOD = 2                   # the way a sample is taken (streams, seconds): another way is another scale, so samples of another method never mix with these (the samples of 1.60.0 / 1.60.1 were one connection)
BUSY_CPU = 85.0              # a server this busy is not measured (the test would be slowed by it, and would slow the users more)
STALL_MBPS = 0.5             # a test that moved less than this moved NOTHING: the tunnel answers its checks but carries no data under load (never noise, never "still learning")
ALIVE_MBPS = 5.0             # a drop whose usual level is not known yet is over when a test moves at least this much
SLOW_MBPS = 20.0             # a tunnel whose usual level is not known (or is under MIN_REF_MBPS) is VERY SLOW when two tests in a row give less than this (the owner, 2026-10-19: a tunnel that gave 0.6 and 0.8 Mb/s showed red and the Doctor did nothing: it never "dropped", it was slow from the first test; the other tunnels of that panel measure 137 - 1400)
SLOW_OVER = 32.0             # a very slow tunnel is back when a test gives at least this much (1.6 x SLOW_MBPS: a gap, so that a tunnel at 20 does not flicker). 1.69.20: the line is 20 Mb/s (the owner: "if the bandwidth is under 20, the Doctor acts")
STALL_FRESH = 2400          # a confirmed stall counts for the state of the tunnel this long after its last test (the job measures a dropped tunnel every twenty minutes)
SINK_V = 2                   # the version of the sink on the foreign server: 2 echoes UDP too (the loss of a link that drops ICMP); a sink of an older version is put again at the next measurement
UDP_PACKETS = 30             # the UDP packets that measure the loss (20 ms apart, about two seconds in all)
LOSS_FRESH = 3 * 3600        # the loss measured with UDP shows for this long (the job measures a tunnel about every 90 minutes)
ENGINE_GAP = 6 * 3600           # the engine of a slow tunnel is restarted at most this often
ENGINE_FIXED = 0.8           # ... and it is fixed when the test after it gives this share of the usual level
FIRST_BATCH = 4              # tunnels that were never measured: this many in one turn of the job (the first round after an update would take twenty minutes at one a minute)
_LOCK = threading.Lock()
_RUNNING: set[int] = set()         # the tunnel the job is measuring (one at a time)
_MEASURING: set[int] = set()       # every tunnel being measured now (the job or a click: never two at once on the same tunnel)


# ------------------------------------------------------------------ pure helpers (tested)
def reference(hist: list, now: int) -> float | None:
    """The usual level of a tunnel: the 75th percentile of what the last week measured (what it gives on a normal day, not its best minute and not its worst). None until REF_MIN samples exist."""
    vals = sorted(float(x[1]) for x in hist if x and x[1] is not None and now - int(x[0]) <= REF_WINDOW)

    if len(vals) < REF_MIN:
        return None

    return vals[round(0.75 * (len(vals) - 1))]


def verdict(down: float | None, ref: float | None, slow_line: float = SLOW_MBPS) -> str:
    """none (nothing measured) · learning (too few samples) · ok · low (under 80 %) · drop (under 60 %) · slow (under SLOW_MBPS and no usual level that could tell a drop: slow from the start, not dropped)."""
    if down is None:
        return "none"

    if down < STALL_MBPS:
        return "drop"                          # nothing moved: it needs no usual level to be a fault

    if ref is None:
        return "slow" if down < slow_line else "learning"

    if ref < MIN_REF_MBPS:
        return "slow" if down < slow_line else "ok"

    ratio = down / ref
    return "drop" if ratio < DROP_RATIO else "low" if ratio < LOW_RATIO else "ok"


def add_sample(bw: dict | None, now: int, down: float, up: float | None, how: str, ping: float | None = None, loss: float | None = None, slow_line: float = SLOW_MBPS, live: float | None = None) -> tuple[dict, str]:
    """Pure: (the new `bw` of the tunnel, what changed: "" | "drop" | "slow" | "recovered") after one sample. The sample is judged against the level BEFORE it, so a drop never pulls its own reference down.
    "slow": the tunnel is VERY slow (two tests in a row under SLOW_MBPS, no usual level that could call it a drop) - it was never fast, so nothing dropped; it ends when a test gives SLOW_OVER."""
    bw = dict(bw or {})

    if bw and (bw.get("m") != METHOD or (bw.get("how") and bw["how"] != how)):
        bw = {}                                                    # another way of measuring (or another kind of tunnel under the same name) is another scale: the history starts again

    hist = [list(x) for x in (bw.get("hist") or []) if now - int(x[0]) <= REF_WINDOW]
    ref_before = reference(hist, now)
    v = verdict(down, ref_before, slow_line)

    if v == "slow" and live is not None and live >= SLOW_MBPS:
        v = "learning"                                            # the users carry the line or more through it: the tunnel is not slow, the little test reads low (1.69.21)

    drop, slow, suspect, change = bw.get("drop"), bw.get("slow"), bool(bw.get("suspect")), ""
    next_at = now + INTERVAL

    if v == "drop":
        if suspect or drop:
            if not drop:
                change = "drop"
                drop = {"since": now, "from": round(ref_before or 0.0, 1), "now": round(down, 1)}
                slow = None                                        # (a drop is the worse word: it takes the place of "slow")
            else:
                drop = {**drop, "now": round(down, 1)}

            suspect, next_at = False, now + WATCH_AFTER
        else:
            suspect, next_at = True, now + CONFIRM_AFTER           # the first sample under the line: measured again soon before anybody is told
    elif v == "slow" and not drop:
        if suspect or slow:
            if not slow:
                change = "slow"
                slow = {"since": now, "now": round(down, 1)}
            else:
                slow = {**slow, "now": round(down, 1)}

            suspect, next_at = False, now + WATCH_AFTER
        else:
            suspect, next_at = True, now + SLOW_CONFIRM_AFTER      # the first very slow sample: measured again within a minute, nobody is told yet
    else:
        suspect = False

        if drop and (v == "ok" or (v == "learning" and down >= ALIVE_MBPS)):
            drop, change = None, "recovered"
        elif drop:
            drop = {**drop, "now": round(down, 1)}
            next_at = now + WATCH_AFTER

        if slow and not drop:
            if down >= max(SLOW_OVER, 1.6 * slow_line):
                slow, change = None, "recovered"
            else:
                slow = {**slow, "now": round(down, 1)}
                next_at = now + WATCH_AFTER

    hist.append([now, round(down, 1), None if up is None else round(up, 1)])
    hist = hist[-KEEP:]
    state = "drop" if drop else "slow" if slow else v if v in ("low", "learning") else "learning" if v == "slow" else "ok"
    old_loss = (bw.get("loss") or {})
    keep = {"pct": old_loss["pct"], "at": old_loss["at"]} if loss is None and old_loss.get("pct") is not None and now - int(old_loss.get("at") or 0) <= LOSS_FRESH else None      # (a test without UDP keeps the last number for a while)

    return {"m": METHOD, "down": round(down, 1), "up": None if up is None else round(up, 1), "at": now, "how": how, "ping": ping, "ref": reference(hist, now), "state": state, "drop": drop, "slow": slow, "suspect": suspect,
            "next_at": next_at, "hist": hist, "loss": {"pct": float(loss), "at": now} if loss is not None else keep}, change


def explain(f: dict) -> tuple[str, str]:
    """Pure: (a short code, ONE sentence) - where the problem of a tunnel whose bandwidth dropped most likely is. `f` holds what the Doctor found: now / ref (Mb/s), the CPU of the two servers, how many tunnels
    of the same Iran / foreign server dropped too, the loss and delay of the line (against its usual delay), the traffic the tunnel carries now. The first rule that fits wins, the cheapest explanation first."""
    now, ref = float(f.get("now") or 0), float(f.get("ref") or 0)

    for side, label in (("iran", "Iran"), ("foreign", "foreign")):
        s = f.get(side) or {}

        if (s.get("cpu") or 0) >= BUSY_CPU:
            return f"{side}_busy", f"The {label} server {s.get('name', '')} is busy (CPU {s['cpu']:.0f} %): everything on it is slow, not only this tunnel."

    fam = f.get("same_kind") or {}

    if fam.get("total", 0) >= 2 and fam.get("dropped", 0) >= max(2, 0.6 * fam["total"]) and fam.get("others_dropped", 0) <= 0.3 * fam.get("others_total", 0):
        return "kind_wide", (f"{fam['dropped']} of the {fam['total']} tunnels that run on {fam['family']} dropped together while the tunnels of the other types are fine: this tunnel type is disturbed on the routes now "
                             "(its carrier may be throttled) - other servers do not matter, another type does.")

    same = f.get("same_iran") or {}

    if same.get("total", 0) >= 2 and same.get("dropped", 0) >= max(2, 0.6 * same["total"]):
        return "iran_wide", f"{same['dropped']} of the {same['total']} tunnels of the Iran server {(f.get('iran') or {}).get('name', '')} dropped together: its own line or its provider is the cause, not this tunnel."

    same = f.get("same_foreign") or {}

    if same.get("total", 0) >= 2 and same.get("dropped", 0) >= max(2, 0.6 * same["total"]):
        return "foreign_wide", f"{same['dropped']} of the {same['total']} tunnels that end on {(f.get('foreign') or {}).get('name', '')} dropped together: that foreign server or its provider is the cause."

    if now < STALL_MBPS:
        return "stalled", ("The test moved no data at all although the checks of the tunnel pass: the data path is stalled - the services run and nothing flows under load. "
                           "A restart of the tunnel, or another tunnel type for this pair, is the way.")

    live = f.get("live")

    if live is not None and now > 0 and live >= 0.8 * now:
        return "full", f"The tunnel carries {live:.0f} Mb/s right now of the {now:.0f} it can give: it is full of users, nothing is broken."

    loss = f.get("loss")

    if loss is not None and loss >= 5:
        return "loss", f"The line loses {loss:.0f} % of its packets: the route got worse."

    rtt, rtt_ref = f.get("rtt"), f.get("rtt_ref")

    if rtt is not None and rtt_ref and rtt >= 1.5 * rtt_ref:
        return "latency", f"The delay rose from {rtt_ref:.0f} to {rtt:.0f} ms: the route changed or is congested."

    return "this_only", (f"Only this tunnel is slower ({now:.0f} Mb/s, usually {ref:.0f}): the servers are idle, the line loses nothing and its delay is as usual. "
                         "A restart of the tunnel, or another tunnel type for this pair, may bring the speed back.")


def explain_slow(f: dict) -> tuple[str, str]:
    """Pure: (a short code, ONE sentence) - where the problem of a tunnel that is VERY SLOW (never dropped: it was slow from its first test) most likely is. `f` as in `explain`. The "full" rule of `explain` does not
    apply: a slow tunnel can carry more than its test reads (the test is two connections for three seconds), that is not "full of users"."""
    now = float(f.get("now") or 0)

    for side, label in (("iran", "Iran"), ("foreign", "foreign")):
        s = f.get(side) or {}

        if (s.get("cpu") or 0) >= BUSY_CPU:
            return f"{side}_busy", f"The {label} server {s.get('name', '')} is busy (CPU {s['cpu']:.0f} %): everything on it is slow, not only this tunnel."

    for key, side, label in (("same_iran", "iran", "the Iran server"), ("same_foreign", "foreign", "the foreign server")):
        same = f.get(key) or {}

        if same.get("total", 0) >= 2 and same.get("dropped", 0) >= max(2, 0.6 * same["total"]):
            return f"{side}_wide", f"{same['dropped']} of the {same['total']} tunnels of {label} {(f.get(side) or {}).get('name', '')} are slow or dropped together: its own line or its provider is the cause, not this tunnel."

    loss = f.get("loss")

    if loss is not None and loss >= 5:
        return "loss", f"The line loses {loss:.0f} % of its packets: the route is bad, and a tunnel over it is slow."

    rtt, rtt_ref = f.get("rtt"), f.get("rtt_ref")

    if rtt is not None and rtt_ref and rtt >= 1.5 * rtt_ref:
        return "latency", f"The delay rose from {rtt_ref:.0f} to {rtt:.0f} ms: the route changed or is congested."

    live = f.get("live")

    return "slow_type", (f"Only this tunnel is that slow ({now:.1f} Mb/s in the test" + (f", the users carry {live:.0f} Mb/s through it now" if live is not None else "") + "): the servers are idle, the line loses nothing and its delay is as usual, "
                         "so the way this tunnel is built (its type, or the link under it) is the likely cause - another type may carry more.")


def stalled(bw: dict | None, now: int) -> str:
    """The sentence that says why the tunnel is not healthy when its last tests moved NO data (a CONFIRMED stall: two tests three minutes apart), "" otherwise. The state of the tunnel follows it:
    a tunnel that answers every check and carries nothing is not "up" (the owner, 2026-10-16: "it shows connected but has no bandwidth")."""
    bw = bw or {}

    if bw.get("drop") and bw.get("down") is not None and float(bw["down"]) < STALL_MBPS and now - int(bw.get("at") or 0) <= STALL_FRESH:
        return f"no data moves through the tunnel under load: the last tests carried {bw['down']:g} Mb/s"

    return ""


# ------------------------------------------------------------------ storing a sample
def _live_peak(tunnel_id: int, now: int) -> float | None:
    """The most the users carried through the tunnel in the last half hour (the checks' counters of the Iran end): what the little test of the Doctor must not contradict. None: fewer than five readings."""
    with session() as db:
        rx = [c.rx_mbps for c in db.scalars(select(TunnelCheck).where(TunnelCheck.tunnel_id == tunnel_id, TunnelCheck.ts >= now - 1800)) if c.rx_mbps is not None]

    return max(rx) if len(rx) >= 5 else None


def _store(tunnel_id: int, down: float, up: float | None, how: str, ping: float | None, loss: float | None = None) -> tuple[dict, str] | None:
    now = int(time.time())
    live = _live_peak(tunnel_id, now) if down < SLOW_MBPS else None

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            return None

        rec = dict(t.record or {})
        bw, change = add_sample(rec.get("bw"), now, down, up, how, ping, loss, live=live)

        if bw["next_at"] == now + INTERVAL:               # the usual rhythm: a few minutes different for every tunnel, so that they do not all fall in one minute
            bw["next_at"] += (tunnel_id * 97) % 600

        rec["bw"] = bw
        t.record = rec
        name, label = t.name, f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"

        if is_standby(t):
            change = ""                      # (a warm standby is measured for its lane: nobody is told about a tunnel that is not in the list)

        if change == "drop":
            emit(db, "warn", "tunnels", f"Tunnel {label}: the bandwidth dropped to {bw['down']} Mb/s (usually {bw['drop']['from']}). The Doctor is looking at why.", name)
        elif change == "slow":
            emit(db, "warn", "tunnels", f"Tunnel {label}: the bandwidth is very low - {bw['down']} Mb/s in two tests in a row (it did not drop: it was never higher). The Doctor is looking at why.", name)
        elif change == "recovered":
            emit(db, "ok", "tunnels", f"Tunnel {label}: the bandwidth is back, {bw['down']} Mb/s.", name)

    return bw, change


def note_quality(tunnel_id: int, q: dict) -> None:
    """The quality job measured a tunnel with a test door (8 connections, 4 s): its download is a sample of the bandwidth too, so a BackPack / Paqet / Chisel tunnel needs no second test."""
    if q.get("down_mbps") is None:
        return

    res = _store(tunnel_id, float(q["down_mbps"]), None if q.get("up_mbps") is None else float(q["up_mbps"]), "door", q.get("latency_ms"))

    if res and res[1] in ("drop", "slow"):
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

    if have and have.get("ip") == peer and int(have.get("v") or 1) >= SINK_V and not force:
        return

    out = run_action(foreign, "sink_install", {"NAME": f"bw{tunnel_id}", "PORT": str(LINK_PORT), "KEY": key, "BIND": peer}, timeout=60)

    if "SINK_OK" not in out:
        raise RuntimeError("the bandwidth sink did not start on the foreign server: " + out.strip()[-120:])

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            t.record = {**(t.record or {}), "bw_sink": {"name": f"bw{tunnel_id}", "ip": peer, "port": LINK_PORT, "v": SINK_V}}


def _server_busy(server_id: int, now: int) -> bool:
    with session() as db:
        s = db.scalar(select(ServerSample).where(ServerSample.server_id == server_id, ServerSample.ts >= now - 360).order_by(ServerSample.ts.desc()).limit(1))
        return bool(s is not None and (s.cpu or 0) >= BUSY_CPU)


def measure(tunnel_id: int) -> tuple[dict, str]:
    """One light measurement of a tunnel (ValueError when it cannot be measured now). Returns (the new `bw`, what changed)."""
    with _LOCK:
        if tunnel_id in _MEASURING:
            raise ValueError("this tunnel is being measured right now")

        _MEASURING.add(tunnel_id)

    try:
        return _measure(tunnel_id)
    finally:
        with _LOCK:
            _MEASURING.discard(tunnel_id)


def _measure(tunnel_id: int) -> tuple[dict, str]:
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

    if state not in ("up", "degraded"):
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
    params = {"PORT": str(LINK_PORT), "SECS": str(SECS), "STREAMS": str(STREAMS), "PINGS": "5", "KEY": key, "TO": "2.0", "HOST": target["peer"], "BYTES": str(BYTES), "DIR": "d", "UDPN": str(UDP_PACKETS)}

    with livebw.quiet(tunnel_id):                         # (what this test moves over the link is not the users': the live numbers wait for it)
        try:
            d = build_other.parse_probe(run_script(iran, "probe_run", params, timeout=45))

            if d.get("error") or d.get("down_mbps") is None:
                raise RuntimeError(str(d.get("error") or "no answer from the sink"))
        except (RuntimeError, SSHError):                  # the sink may have died with a reboot of the foreign server: put it back once and ask again
            _ensure_sink(tunnel_id, foreign, target["peer"], key, force=True)
            d = build_other.parse_probe(run_script(iran, "probe_run", params, timeout=45))

            if d.get("error") or d.get("down_mbps") is None:
                raise RuntimeError("the sink does not answer through the link: " + str(d.get("error") or "no data")[:100])

    res = _store(tunnel_id, float(d["down_mbps"]), None, "link", d.get("latency_ms"), d.get("udp_loss_pct"))

    if res is None:
        raise ValueError("tunnel not found")

    return res


def recheck(tunnel_id: int) -> None:
    """After a repair of a tunnel whose last tests moved no data: measure it again at once, so that its state does not wait for the next turn of the job - a repair is judged by what the users feel."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        stalled_now = t is not None and bool(stalled((t.record or {}).get("bw"), int(time.time())))

    if not stalled_now:
        return

    try:
        measure(tunnel_id)
    except (ValueError, RuntimeError, SSHError) as e:
        log.info("bandwidth of tunnel %s not measured after the repair: %s", tunnel_id, str(e)[:120])


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
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), Tunnel.state.in_(("up", "degraded")))):         # (a tunnel that is only unstable carries its users: its bandwidth is the thing to see)
            has_door = bool((t.record or {}).get("test_port") and (t.secrets or {}).get("test_key"))       # (a tunnel with a test door is measured by its quality test: the job only repeats it to confirm and to watch a drop)

            if is_temp(t.name) or (_target(t) is None and not has_door) or t.iran.online is False or t.foreign.online is False:
                continue

            bw = (t.record or {}).get("bw") or {}
            nxt = int(bw.get("next_at") or 0) or (int(bw.get("at") or 0) + INTERVAL + (t.id * 97) % 600)
            at = int(bw.get("at") or 0)

            if at:
                q = (t.record or {}).get("quality") or {}

                if bw.get("state") == "learning" and float(bw.get("down") if bw.get("down") is not None else 99) < SLOW_MBPS:
                    nxt = min(nxt, at + SLOW_CONFIRM_AFTER)               # a very slow test that nobody has judged (the first of a pair, or one of a schedule from before 1.69.15) is not left for 90 minutes
                elif q.get("score") is not None and float(q["score"]) < POOR_SCORE and bw.get("state") not in ("drop", "slow"):
                    nxt = min(nxt, at + POOR_AFTER)                       # a tunnel the quality test calls poor is measured again soon: "when a tunnel has a problem, do not wait for the next sample" (the owner, 2026-10-19)

            if now >= nxt:
                out.append((int(bw.get("at") or 0), t.id))

    return [i for _, i in sorted(out)]


def _no_sample(tunnel_id: int) -> bool:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        return t is not None and not ((t.record or {}).get("bw") or {}).get("down") and not ((t.record or {}).get("bw") or {}).get("at")


def _server_busy_for(tunnel_id: int, now: int) -> bool:
    """Is a server of this tunnel too busy to be measured (the measurement would be slowed by it and would slow the users more)?"""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        ids = (t.iran_server_id, t.foreign_server_id) if t is not None else ()

    return any(_server_busy(i, now) for i in ids)


def _failed(tunnel_id: int, why: str) -> None:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            bw = dict(rec.get("bw") or {})
            bw["next_at"], bw["error"] = int(time.time()) + (RETRY_AFTER if bw.get("down") is not None or "has no python" in why else RETRY_FIRST), why[:120]          # (a server with no python is not tried again every 90 s)
            rec["bw"] = bw
            t.record = rec


def run() -> None:
    """Job (every minute): ONE tunnel is measured per turn - the one that waited longest - so the servers feel a few seconds of one connection, never a burst."""
    if get_settings().read_only or jobs.stopping():
        return

    with _LOCK:
        if _RUNNING:
            return

    try:
        _zero_watch()
    except Exception:
        log.exception("the watch of the tunnels that nobody uses failed")

    now = int(time.time())
    ids = _due(now)

    if not ids:
        return

    fresh = [i for i in ids if _no_sample(i)][:FIRST_BATCH]         # a tunnel that was never measured is shown with a dash: the first round goes faster (a few light measurements in a turn), the rest one by one

    for tid in fresh or ids[:1]:
        if jobs.stopping() or _server_busy_for(tid, now):
            continue

        with _LOCK:
            _RUNNING.add(tid)

        try:
            bw, change = measure(tid)
            log.info("bandwidth of tunnel %s: %s Mb/s (usual %s) %s", tid, bw.get("down"), bw.get("ref"), bw.get("state"))

            if change in ("drop", "slow"):
                _investigate_later(tid)
            elif bw.get("state") == "slow":
                _retype_later(tid)                                  # a tunnel that is ALREADY very slow (judged before an update, or a change that could not start): tried again at every watch, until it is changed
            elif bw.get("state") in ("ok", "learning", "low"):
                try:
                    _upgrade_check(tid)                             # (a tunnel that works: is there a better type known for its pair?)
                except Exception:
                    log.exception("the look for a better type of tunnel %s failed", tid)
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
        rx = sorted(c.rx_mbps for c in db.scalars(select(TunnelCheck).where(TunnelCheck.tunnel_id == t.id, TunnelCheck.ts >= now - 900).order_by(TunnelCheck.ts.desc()).limit(10)) if c.rx_mbps is not None)
        live = rx[len(rx) // 2] if len(rx) >= 5 else None       # what comes to the Iran server through the link: the MIDDLE of the last ten minutes (one minute that holds a test of the panel itself, a few seconds of 500 Mb/s, must not make the tunnel look full of users)
        rtts = sorted(c.rtt_ms for c in db.scalars(select(TunnelCheck).where(TunnelCheck.tunnel_id == t.id, TunnelCheck.ts >= now - 7 * 86400, TunnelCheck.ok.is_(True))) if c.rtt_ms is not None)
        sides = {}

        for side, srv in (("iran", t.iran), ("foreign", t.foreign)):
            s = db.scalar(select(ServerSample).where(ServerSample.server_id == srv.id, ServerSample.ts >= now - 600).order_by(ServerSample.ts.desc()).limit(1))
            sides[side] = {"name": srv.name, "cpu": float(s.cpu or 0) if s is not None else None}

        def family_of(x: Tunnel) -> str:
            from . import autopilot

            return autopilot.kind_of(x.type, x.transport, x.record).rsplit(":", 1)[-1]          # (the carrier: wssmux, tcpmux, wss ... - "bp:wssmux" and "wssmux" are one family)

        mine = family_of(t)
        fam = [x for x in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(x.name) and not is_standby(x) and family_of(x) == mine]
        same_kind = {"family": mine, "total": len(fam), "dropped": sum(1 for x in fam if ((x.record or {}).get("bw") or {}).get("state") in ("drop", "slow"))}
        others = [x for x in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(x.name) and not is_standby(x) and family_of(x) != mine]
        same_kind["others_total"], same_kind["others_dropped"] = len(others), sum(1 for x in others if ((x.record or {}).get("bw") or {}).get("state") in ("drop", "slow"))

        def share(col, sid: int) -> dict:
            rows = [x for x in db.scalars(select(Tunnel).where(col == sid, Tunnel.enabled.is_(True))) if not is_temp(x.name)]
            return {"total": len(rows), "dropped": sum(1 for x in rows if ((x.record or {}).get("bw") or {}).get("state") in ("drop", "slow"))}

        return {"name": t.name, "state": bw.get("state"), "now": bw.get("down"), "ref": (bw.get("drop") or {}).get("from") or bw.get("ref"), "loss": loss, "live": live, "iran": sides["iran"], "foreign": sides["foreign"],
                "rtt": t.rtt_ms, "rtt_ref": rtts[len(rtts) // 2] if len(rtts) >= 20 else None, "same_iran": share(Tunnel.iran_server_id, t.iran_server_id), "same_foreign": share(Tunnel.foreign_server_id, t.foreign_server_id), "same_kind": same_kind}


def _engine_fix(tunnel_id: int, say) -> None:
    """The servers are idle, the line is clean, its delay is as usual and ONLY this tunnel is slower: the fault is in the tunnel itself or its engine (a stale multiplexed session, a stuck buffer) - the Doctor
    fixes the tunnel itself (the owner, 2026-10-16): it restarts the tunnel service on both servers (about thirty seconds; the users reconnect at once) and measures again. Not while the Doctor is off or the
    panel is read-only, not twice in six hours; what came of it is kept in the Doctor's memory."""
    from . import actions, knowledge

    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            return

        bw, name, iran_id, foreign_id = dict((t.record or {}).get("bw") or {}), t.name, t.iran_server_id, t.foreign_server_id

    if actions.doctor_mode() == "off" or get_settings().read_only:
        say("The Doctor is off (or the panel is read-only): it is told, not repaired.")
        return

    if now - int(bw.get("engine_fix_at") or 0) < ENGINE_GAP:
        say(f"The engine of this tunnel was restarted less than {ENGINE_GAP // 3600} hours ago: not again now.")
        return

    with session() as db:                                           # (stamped before it starts: a crash in the middle must not repeat it)
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            rec["bw"] = {**(rec.get("bw") or {}), "engine_fix_at": now}
            t.record = rec

    say("Only this tunnel is slower: the Doctor restarts its engine (the tunnel service on both servers) - about thirty seconds, the users reconnect at once - and measures it again.")

    try:
        res = actions.restart_now(tunnel_id, source="auto")
        say(f"Restart: {res['state']}" + (f" ({res['why']})" if res.get("why") else "") + ".")
    except ValueError as e:
        say(f"The engine could not be restarted: {str(e)[:120]}")
        return

    fixed = False

    try:
        bw2, _ = measure(tunnel_id)
        ref = (bw2.get("drop") or {}).get("from") or bw2.get("ref")
        down = float(bw2.get("down") or 0)
        fixed = down >= ENGINE_FIXED * float(ref) if ref else down >= ALIVE_MBPS
        say(f"Measured again: {down:.0f} Mb/s" + (f" (usually {float(ref):.0f})" if ref else "") + ".")
    except (ValueError, RuntimeError, SSHError) as e:
        say(f"It could not be measured again: {str(e)[:120]}")

    say("The restart brought the speed back." if fixed else "The restart did not bring the speed back: the Doctor keeps measuring, and it offers another tunnel type only when a better one is known for this pair (Doctor page, Knowledge).")

    try:
        knowledge.remember(iran_id, foreign_id, "slow", name, [{"step": "restart", "ok": fixed}], fixed, int(time.time()) - now)
    except Exception:
        log.exception("the slow tunnel %s could not be kept in the Doctor's memory", name)

    return fixed


def _say_checks(say, f: dict) -> None:
    """The checks of a look at a drop or at a very slow tunnel, one line each: the two servers, the other tunnels of the same servers, the loss, the delay, the users."""
    for side in ("iran", "foreign"):
        s = f[side]
        say(("✕ " if (s["cpu"] or 0) >= BUSY_CPU else "✓ ") + f"The {side} server {s['name']}: " + (f"CPU {s['cpu']:.0f} %" if s["cpu"] is not None else "no reading of its load just now"))

    for key, label in (("same_iran", f["iran"]["name"]), ("same_foreign", f["foreign"]["name"])):
        s = f[key]
        say(("✕ " if s["total"] >= 2 and s["dropped"] >= 2 else "✓ ") + (f"Tunnels of {label}: this is its only tunnel" if s["total"] <= 1 else f"Tunnels of {label}: {s['dropped']} of {s['total']} are dropping" + (" (this one counts)" if s["dropped"] else "")))

    say(("✕ " if (f["loss"] or 0) >= 5 else "✓ ") + ("The line loses " + f"{f['loss']:.0f} % of its packets" if f["loss"] is not None else "The loss of the line cannot be measured here (the provider drops pings); the data passes"))

    if f["rtt"] is not None:
        say(("✕ " if f["rtt_ref"] and f["rtt"] >= 1.5 * f["rtt_ref"] else "✓ ") + f"The delay is {f['rtt']:.0f} ms" + (f" (usually {f['rtt_ref']:.0f})" if f["rtt_ref"] else ""))

    if f["live"] is not None:
        say(f"Users are carrying {f['live']:.0f} Mb/s through it right now.")


def _keep_why(tunnel_id: int, code: str, sentence: str, what: str) -> None:
    """The sentence that says where the problem is: kept in the record of the tunnel (the page shows it) and told as an event."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            bw = dict(rec.get("bw") or {})
            bw["why"] = {"code": code, "text": sentence, "at": int(time.time())}
            rec["bw"] = bw
            t.record = rec
            emit(db, "warn", "tunnels", f"Tunnel {t.name}: the bandwidth {what} - {sentence}", t.name)


def investigate(aid: int, tunnel_id: int, finish: bool = True) -> None:
    """The story of the look (the Doctor's Live window shows it): what dropped, what was checked, where the problem is. A tunnel that is VERY SLOW (it never dropped) has its own story, `_investigate_slow`."""
    from . import autopilot

    f = _facts(tunnel_id)
    say = lambda line: autopilot._say(aid, line)       # (one short line for the story)

    if f.get("state") == "slow":
        _investigate_slow(aid, tunnel_id, f, say, finish)
        return

    say(f"{f['name']}: the bandwidth dropped to {f['now']} Mb/s; it usually gives {f['ref']}.")
    _say_checks(say, f)
    code, sentence = explain(f)
    _keep_why(tunnel_id, code, sentence, "dropped")
    say("Where the problem is: " + sentence)

    fixed = None

    if code == "this_only":
        fixed = _engine_fix(tunnel_id, say)

    if code in DROP_SWAP_CODES and fixed is not True:
        _swap_after_drop(aid, tunnel_id, say)

    if finish:
        autopilot._say(aid, "The Doctor keeps measuring this tunnel every 20 minutes and says when the bandwidth is back.", done=True, ok=True)


DROP_SWAP = True                          # (the tests of the other files turn it off: a swap tests types on the real servers)
DROP_SWAP_CODES = ("kind_wide", "this_only", "stalled", "loss", "latency")          # a busy server, a whole server dropping or "full of users" is not cured by another type of this tunnel
DROP_TRIES = 3                            # types tested (speed included) for a tunnel that dropped
DROP_GAIN = 2.0                           # a type must give at least this many times what the dropped tunnel gives now ...
DROP_LOSS = 1.0                           # ... lose at most this % of the packets in its test (steady) ...
DROP_PING_SLACK, DROP_PING_MS = 1.3, 15.0 # ... and answer about as fast as the running type (the ping is what the user feels first)


def drop_pick(results: list[dict], steady: dict, now_mbps: float, rtt: float | None) -> dict | None:
    """Pure: the tested type to change a dropped tunnel to, or None. A candidate must have CONNECTED in its own test, lost at most DROP_LOSS %, given at least DROP_GAIN times what the dropped tunnel gives now (and
    knowledge.FASTER_MIN_MBPS), and not answer much slower than the running type; a type the memory knows to be UNSTEADY is never picked. `steady`: {kind: stability %} from the Doctor's memory - a type whose
    stability is PROVEN (known, STEADY_OK or more) goes before one that is not proven yet, then the faster. The caller changes the tunnel by itself only to a proven one (the owner, 2026-10-21: "if there is no stability, do not go to that type by itself")."""
    from . import knowledge

    need = max(knowledge.FASTER_MIN_MBPS, DROP_GAIN * float(now_mbps or 0))
    ok = [r for r in results if (steady.get(r["kind"]) is None or steady[r["kind"]] >= knowledge.STEADY_OK) and r.get("score") is not None and float(r.get("loss_pct") or 0) <= DROP_LOSS and r.get("down_mbps") is not None and float(r["down_mbps"]) >= need
          and (rtt is None or r.get("latency_ms") is None or float(r["latency_ms"]) <= DROP_PING_SLACK * float(rtt) + DROP_PING_MS)]
    return min(ok, key=lambda r: (steady.get(r["kind"]) is None, -float(r["down_mbps"]))) if ok else None


def _swap_after_drop(aid: int, tunnel_id: int, say) -> None:
    """A tunnel dropped and the look found no outside cause (the owner, 2026-10-21: "when we have a drop the Doctor must test the other types - the problem may not exist on them - and it can change the type; I said
    the tunnel must be steady"). The Doctor TESTS the other types the memory of the pair knows to be steady (or has not tried enough yet), with a real speed test, one after the other, and changes the tunnel to
    the one that gives clearly more and was clean in its test (`drop_pick`) - with the rails of every automatic change: once in 24 hours per tunnel, at a quiet moment, the old type put back when the new one does
    not build or gives clearly less. Nothing when the Doctor is off; when no type is clearly better the tunnel stays as it is and keeps being measured."""
    from . import actions, autopilot, doctor, knowledge, license

    if not DROP_SWAP:
        return

    if actions.doctor_mode() == "off" or get_settings().read_only:
        say("The Doctor is off (or the panel is read-only): it does not test other types.")
        return

    f = _facts(tunnel_id)

    if f.get("state") not in ("drop", "slow"):
        return                                                   # (the restart brought it back)

    fam = f.get("same_kind") or {}
    bad_family = fam.get("family") if explain(f)[0] == "kind_wide" else None          # (the carrier is disturbed on many tunnels: no type that runs on it is tried)

    snap = autopilot._snapshot(tunnel_id)
    cur = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])
    now = int(time.time())
    block = doctor.may_retype(snap["name"], doctor._state().get("retype_done") or {}, now)

    if block:
        say(f"The Doctor does not change the type now: {block}.")
        return

    p = knowledge.pair(snap["iran_id"], snap["foreign_id"])
    rows = [r for r in knowledge.ranked(p, now) if r["kind"] != cur and license.tunnel_kind_allowed(r["kind"]) and (r.get("steady") is None or r["steady"] >= knowledge.STEADY_OK)
            and r["kind"].rsplit(":", 1)[-1] != bad_family]
    kinds = [r["kind"] for r in rows]

    if not kinds:                                                # (nothing known about the pair: the Doctor's own order for it)
        plan, _ = knowledge.plan_for(snap["iran_id"], snap["foreign_id"], cur, False, True)
        kinds = [k for k in plan if k != cur and license.tunnel_kind_allowed(k) and k not in knowledge.blocked(p, now) and k.rsplit(":", 1)[-1] != bad_family]

    kinds = kinds[:DROP_TRIES]

    if not kinds:
        say(f"No other tunnel type can be tried between {snap['iran_name']} and {snap['foreign_name']} (none is steady in the Doctor's memory, or the licence allows none).")
        return

    say(f"{snap['name']} ({cur}) dropped and nothing outside it explains it - the problem may not be on the other types. The Doctor tests {', '.join(kinds)} between {snap['iran_name']} and {snap['foreign_name']} now, speed included.")

    try:
        results = autopilot._recommend_now(aid, snap["iran_id"], snap["foreign_id"], kinds, measure="full")
    except (ValueError, RuntimeError, SSHError) as e:
        say(f"The test could not run ({str(e)[:120]}): the tunnel stays {cur}; the Doctor keeps measuring.")
        return

    shown = ", ".join(f"{r['kind']} {float(r['down_mbps']):.0f} Mb/s" for r in results if r.get("down_mbps") is not None) or "none carried data"
    say(f"Tested: {shown}.")
    steady = {r["kind"]: r.get("steady") for r in knowledge.ranked(knowledge.pair(snap["iran_id"], snap["foreign_id"]), int(time.time()), include_poor=True)}          # (the tests just made are in the memory now: the stability counts them)
    best = drop_pick(results, steady, float(f.get("now") or 0), f.get("rtt"))

    if best is None:
        say(f"No tested type gives clearly more than {float(f.get('now') or 0):.0f} Mb/s with a clean line and a steady record: {snap['name']} stays {cur}, and the Doctor keeps measuring it.")
        return

    st_ = steady.get(best["kind"])
    note = f"stability in the Doctor's memory {st_} %" if st_ is not None else "not enough earlier tests to call it steady"
    why = (f"{cur} dropped to {float(f.get('now') or 0):.0f} Mb/s (usually {float(f.get('ref') or 0):.0f}); {best['kind']} gave {float(best['down_mbps']):.0f} Mb/s in the speed test just now, lost {float(best.get('loss_pct') or 0):g} % "
           f"and answered in {float(best.get('latency_ms') or 0):.0f} ms; {note}.")

    if st_ is None:                                              # (a type that was not proven steady is never taken by itself: the owner decides with one click)
        doctor.suggest("type", tunnel_id, snap["name"], snap["iran_id"], cur, None, best["kind"], why, extra={"now_kind": cur, "to_kind": best["kind"], "now_mbps": f.get("now"), "memory_mbps": best["down_mbps"]})
        say(f"Suggested on the Doctor page: {cur} → {best['kind']}. {why} It is not changed by itself: its steadiness is not proven yet (it needs {knowledge.STEADY_MIN_TESTS} tests; each test of the pair counts).")
        return

    say(f"The Doctor changes the type by itself ({actions.doctor_mode()} mode): {cur} → {best['kind']}.")
    doctor._run_retype(aid, tunnel_id, best["kind"], cur, why, auto=True)

def _investigate_slow(aid: int, tunnel_id: int, f: dict, say, finish: bool) -> None:
    """A tunnel that was never fast (two tests in a row under SLOW_MBPS, no usual level to drop from): the same checks, the sentence, and - from what the Doctor KNOWS of the pair - a type that gave clearly more (a suggestion)."""
    from . import autopilot

    say(f"{f['name']}: the bandwidth is very low - {f['now']} Mb/s in the last two tests. It did not drop: this tunnel never gave more, so there is no usual level to compare with.")
    _say_checks(say, f)
    code, sentence = explain_slow(f)
    _keep_why(tunnel_id, code, sentence, "is very low")
    say("Where the problem is: " + sentence)

    if code in ("slow_type", "latency", "loss"):                        # (a busy server or a slow line of the whole Iran / foreign server is not cured by another type of this tunnel)
        _suggest_faster(aid, tunnel_id, f, say)

    if finish:
        autopilot._say(aid, "The Doctor keeps measuring this tunnel every 20 minutes and says when the bandwidth is back.", done=True, ok=True)


def _suggest_faster(aid: int, tunnel_id: int, f: dict, say) -> None:
    """From the Doctor's memory of the pair: a type that gave clearly more here becomes a SUGGESTION on the Doctor page (1.69.14). Since 1.69.17 the Doctor DOES it by itself in the modes fix and move (the owner,
    2026-10-19: "when the Doctor sees it is slow, let it do the other types it tested before - what is it waiting for?"), under rails: once in 24 hours per tunnel, at a quiet moment, the link probed first, the old
    type put back when the new one does not build or is clearly worse. Nothing when the Doctor is off."""
    from . import actions, autopilot, doctor, knowledge, license

    if actions.doctor_mode() == "off":
        say("The Doctor is off: it tells, it does not suggest a change.")
        return

    snap = autopilot._snapshot(tunnel_id)
    cur = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])
    now = int(time.time())
    live = float(f.get("live") or 0)
    rows = knowledge.faster_kinds(knowledge.pair(snap["iran_id"], snap["foreign_id"]), now, cur, max(float(f.get("now") or 0), live), license.tunnel_kind_allowed)

    if not rows:
        say(f"The Doctor's memory of {snap['iran_name']} ⇄ {snap['foreign_name']} knows no other type that gave clearly more than {cur} here. To find one: Doctor → Knowledge → test this pair.")
        return

    rows = [r for r in rows if r.get("steady") is None or r["steady"] >= knowledge.STEADY_OK]          # (a type that fell or failed in some of its tests is not offered)

    if not rows:
        say(f"The Doctor's memory knows a faster type for {snap['iran_name']} ⇄ {snap['foreign_name']}, but it was not steady in its tests: nothing is offered.")
        return

    r = next((x for x in rows if x.get("steady") is not None), rows[0])          # (a type with a proven steadiness first)
    age = max(0, (now - int(r.get("at") or now)) // 3600)
    why = (f"{cur} gives {f['now']} Mb/s in the test" + (f" (the users carry {live:.0f} Mb/s through it now)" if live else "") + f"; the Doctor's memory has {r['kind']} at {float(r['down_mbps']):.0f} Mb/s on this pair, measured {age} h ago.")
    doctor.suggest("type", tunnel_id, snap["name"], snap["iran_id"], cur, None, r["kind"], why, extra={"now_kind": cur, "to_kind": r["kind"], "now_mbps": f.get("now"), "memory_mbps": r["down_mbps"]})
    say(f"Suggested on the Doctor page: change the type from {cur} to {r['kind']} - one click, the old type is put back if the new one does not build. {why}")
    block = doctor.may_retype(snap["name"], doctor._state().get("retype_done") or {}, now)

    if block:
        say(f"Not done by itself now: {block}.")
        return

    if get_settings().read_only:
        return

    if r.get("steady") is None:                                  # (not proven steady: the owner decides with one click, the Doctor does not change a running tunnel to it)
        say(f"Not done by itself: the steadiness of {r['kind']} is not proven yet ({knowledge.steady_note(r)}).")
        return

    say(f"The Doctor does it by itself ({actions.doctor_mode()} mode): {cur} → {r['kind']}.")
    doctor._run_retype(aid, tunnel_id, r["kind"], cur, why, auto=True)


def _cuts_lately(tunnel_id: int, now: int, hours: int = 24) -> int:
    """How many times the tunnel was cut in the last `hours` (runs of failed checks, `analyst.find_outages`): a type that keeps dropping is unsteady, whatever its test said."""
    from . import analyst

    with session() as db:
        rows = [(int(r[0]), bool(r[1])) for r in db.execute(select(TunnelCheck.ts, TunnelCheck.ok).where(TunnelCheck.tunnel_id == tunnel_id, TunnelCheck.ts >= now - hours * 3600))]

    return len(analyst.find_outages(rows, now))


UPGRADE_EVERY = 6 * 3600         # a tunnel is looked at for a better type this often (the look reads the Doctor's memory only: no server is asked)
_UPGRADE_AT: dict[int, int] = {}


def _upgrade_check(tunnel_id: int) -> None:
    """After a measurement of a tunnel that is fine (the owner, 2026-10-20: "after it sets the types it must test whether a better and more stable one exists"): when the Doctor's memory of the pair knows a type that
    carried clearly more in the same pair test, and was stable in it, the Doctor PUTS IT ON ITS PAGE as a suggestion (one click: Change type, the old type is put back when the new one does not build). It does not
    change a running tunnel by itself. The memory is kept fresh by the night survey and by "Test this pair"."""
    from . import actions, autopilot, doctor, knowledge, license

    now = int(time.time())

    if now - _UPGRADE_AT.get(tunnel_id, 0) < UPGRADE_EVERY or actions.doctor_mode() == "off":
        return

    _UPGRADE_AT[tunnel_id] = now
    snap = autopilot._snapshot(tunnel_id)
    cur = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])
    p = knowledge.pair(snap["iran_id"], snap["foreign_id"])
    cuts = _cuts_lately(tunnel_id, now)
    steadier = cuts >= knowledge.STEADY_CUTS
    rows = knowledge.steadier_kinds(p, now, cur, license.tunnel_kind_allowed) if steadier else []
    rows = rows or knowledge.upgrade_kinds(p, now, cur, license.tunnel_kind_allowed)

    if not rows or any(x.get("tunnel") == snap["name"] and x.get("kind") != "type" for x in doctor._state()["suggestions"]):          # (a move suggestion of the tunnel is more urgent)
        return

    r = rows[0]

    if steadier and r.get("steady") is not None and r["steady"] >= knowledge.STEADY_OK:
        why = (f"{snap['name']} was cut {cuts} times in the last day. In the tests of {snap['iran_name']} ⇄ {snap['foreign_name']} {r['kind']} was steady: {r['passed']} of {r['tests']} tests passed, stability "
               f"{r['steady']} %, {float(r['down_mbps']):.0f} Mb/s, {float(r.get('latency_ms') or 0):.0f} ms. Many users need a type that does not drop.")
        doctor.suggest("type", tunnel_id, snap["name"], snap["iran_id"], cur, None, r["kind"], why, extra={"now_kind": cur, "to_kind": r["kind"], "memory_mbps": r["down_mbps"], "upgrade": True, "steadier": True})
        log.info("a steadier type for tunnel %s: %s -> %s (%s cuts in a day)", tunnel_id, cur, r["kind"], cuts)
        return

    base = float((((p or {}).get("kinds") or {}).get(cur) or {}).get("full", {}).get("down_mbps") or 0)
    age = max(0, (now - int(r.get("at") or now)) // 3600)
    why = (f"In the pair test of {snap['iran_name']} ⇄ {snap['foreign_name']} {cur} carried {base:.0f} Mb/s; {r['kind']} carried {float(r['down_mbps']):.0f} Mb/s, "
           f"lost {float(r.get('loss_pct') or 0):g} % and scored {r['score']} ({age} h ago; {knowledge.steady_note(r)}). Nothing is wrong with {cur}: this is a better type known for this pair.")
    doctor.suggest("type", tunnel_id, snap["name"], snap["iran_id"], cur, None, r["kind"], why, extra={"now_kind": cur, "to_kind": r["kind"], "now_mbps": base, "memory_mbps": r["down_mbps"], "upgrade": True})
    log.info("a better type for tunnel %s: %s -> %s", tunnel_id, cur, r["kind"])


# ------------------------------------------------------------------ a tunnel that nobody uses
ZERO_MBPS = 0.05          # what the users use under this: nobody uses the tunnel
ZERO_AFTER = 5 * 60       # ... for this long (the live reader reads every 30 s even when nobody looks at the page)
ZERO_GAP = 3 * 3600       # a silent tunnel is looked into again at most this often (a test carries up to 120 MB: an idle tunnel must not be tested all day)
LOSS_BAD = 5.0            # packets lost (%): a problem
PING_HIGH_RATIO, PING_HIGH_EXTRA_MS = 2.0, 60.0          # the ping is a problem when it is this many times its usual AND this much more
_ZERO: dict[int, dict] = {}


def zero_step(entry: dict | None, now: int, use: float | None, up: bool) -> tuple[dict | None, bool]:
    """Pure: one turn of the watch for one tunnel - (its new state, look now?). The state is None while the tunnel is down, has no reading, or its users use something (the silence starts again); otherwise
    {"since": when the silence began, "looked": when the Doctor last looked into it}. Look when the silence has lasted ZERO_AFTER and the last look is older than ZERO_GAP."""
    if not up or use is None or use >= ZERO_MBPS:
        return None, False

    e = dict(entry or {})
    e.setdefault("since", now)
    look = now - int(e["since"]) >= ZERO_AFTER and ("looked" not in e or now - int(e["looked"]) >= ZERO_GAP)

    if look:
        e["looked"] = now

    return e, look


def silent_problems(f: dict) -> list[str]:
    """Pure: what is WRONG with a tunnel nobody uses, from what the full look found - `f`: state, last_error, data_fails, ext_code, loss, ping, usual_ping, bw_state, carries. An empty list: nothing is wrong, the
    users are simply quiet (or their trouble is beyond the tunnel)."""
    out = []

    if f.get("state") not in (None, "up"):
        out.append(f"the tunnel is {f['state']}" + (f" ({str(f['last_error'])[:80]})" if f.get("last_error") else ""))

    if f.get("data_fails"):
        out.append("the test through its port failed")

    if str(f.get("ext_code") or "") in ("000", "0"):
        out.append("a test from another Iran server through its port got no answer")

    if f.get("loss") is not None and float(f["loss"]) >= LOSS_BAD:
        out.append(f"{float(f['loss']):g} % of the packets are lost")

    if f.get("bw_state") in ("drop", "slow") or (f.get("carries") is not None and float(f["carries"]) < STALL_MBPS):
        out.append("it carries almost nothing under load" if (f.get("carries") is not None and float(f["carries"]) < STALL_MBPS) else "what it can carry dropped")

    p, u = f.get("ping"), f.get("usual_ping")

    if p is not None and u is not None and float(p) >= float(u) * PING_HIGH_RATIO and float(p) >= float(u) + PING_HIGH_EXTRA_MS:
        out.append(f"the ping is {float(p):.0f} ms, usually {float(u):.0f} ms")

    return out


def _silent_set(tunnel_id: int, rec: dict | None) -> None:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            r = dict(t.record or {})

            if rec:
                r["silent"] = rec
            else:
                r.pop("silent", None)

            t.record = r


def _usual_ping(tunnel_id: int) -> float | None:
    """The usual ping of a tunnel: the middle of its good checks of the last 7 days (None under 20 of them)."""
    now = int(time.time())

    with session() as db:
        rtts = sorted(c.rtt_ms for c in db.scalars(select(TunnelCheck).where(TunnelCheck.tunnel_id == tunnel_id, TunnelCheck.ts >= now - 7 * 86400, TunnelCheck.ok.is_(True))) if c.rtt_ms is not None)

    return rtts[len(rtts) // 2] if len(rtts) >= 20 else None


def _zero_look(tunnel_id: int, name: str, silent_for: int) -> None:
    """The Doctor looks FULLY into a tunnel that is up but that nobody uses (the owner, 2026-10-21: "a tunnel nobody uses may be at an hour when the users are truly not there - check and test it completely, and if it
    really has a problem, fix it"; a config was cut and its tunnel said connected): a fresh look at the tunnel with the test of another Iran server through its port, the pings (latency, jitter, loss) and, where the
    tunnel has a test door, the speed a user gets, what it can carry now, and the ping against its usual. Something wrong: the Doctor fixes it now (restart, rebuild, another type - the best for the USER first, see
    `userscore`) - nobody is on it to be cut. Nothing wrong: it says so, with the numbers, and that the users are quiet or their trouble is beyond the tunnel."""
    from . import autopilot, tunnel_test, userscore
    from .tunnels import check_tunnel

    with _LOCK:
        if tunnel_id in _RUNNING:
            return

        _RUNNING.add(tunnel_id)

    try:
        check_tunnel(tunnel_id, fresh_ext=True)

        with session() as db:
            t = db.get(Tunnel, tunnel_id)
            state, err, rtt, d = (t.state, t.last_error, t.rtt_ms, dict(t.detail or {})) if t is not None else ("", "", None, {})

        try:
            q = tunnel_test.quality(tunnel_id)
        except (ValueError, RuntimeError, SSHError) as e:
            log.info("the pings of the silent tunnel %s: %s", tunnel_id, str(e)[:120])
            q = {}

        bw, change = measure(tunnel_id)
        ping = q.get("latency_ms") if q.get("latency_ms") is not None else rtt
        loss = q.get("loss_pct") if q.get("loss_pct") is not None else d.get("loss_pct")
        carries = bw.get("down")
        f = {"state": state, "last_error": err, "data_fails": d.get("data_fails"), "ext_code": (d.get("ext") or {}).get("code"), "loss": loss, "ping": ping, "usual_ping": _usual_ping(tunnel_id),
             "bw_state": bw.get("state") if change != "slow" else "slow", "carries": carries}
        problems = silent_problems(f)
        user = userscore.user_score({"latency_ms": ping, "jitter_ms": q.get("jitter_ms"), "loss_pct": loss, "down_mbps": q.get("down_mbps") if q.get("down_mbps") is not None else carries, "down1_mbps": q.get("down1_mbps")})
        mins = max(1, int(silent_for) // 60)
        said = f"ping {_mbps_text(ping)} ms, {_mbps_text(loss) if loss is not None else '?'} % lost, it carries {_mbps_text(carries)} Mb/s, what a user gets {user}/100"
        fixing = False

        if problems:
            if actions_mode_allows_fix():
                try:
                    autopilot.smart_fix_start(tunnel_id, "switch", source="doctor")
                    fixing = True
                except (ValueError, RuntimeError) as e:
                    log.info("the fix of the silent tunnel %s did not start: %s", tunnel_id, str(e)[:120])

            note = f"Nobody has used it for {mins} minutes (it may be a quiet hour). The Doctor checked it fully ({said}) and found: " + "; ".join(problems) + (". It is fixing it now." if fixing else ".")
        else:
            note = (f"Nobody has used it for {mins} minutes (it may be a quiet hour). The Doctor checked it fully ({said}): nothing is wrong with the tunnel. If the users say it does not work, the problem is "
                    "beyond it (their configs, the CDN in front of the Iran server, the foreign server's internet) - look at the configs that use this tunnel.")

        now = int(time.time())
        _silent_set(tunnel_id, {"since": now - int(silent_for), "looked_at": now, "carries": carries, "ping": ping, "loss": loss, "user": user, "bad": bool(problems), "problems": problems, "fixing": fixing, "note": note})

        with session() as db:
            emit(db, "warn" if problems else "info", "tunnels", f"Tunnel {name}: {note}", name)
    except (ValueError, RuntimeError, SSHError) as e:
        log.info("the look into the silent tunnel %s failed: %s", tunnel_id, str(e)[:140])
    finally:
        with _LOCK:
            _RUNNING.discard(tunnel_id)


def actions_mode_allows_fix() -> bool:
    """May the Doctor fix by itself? In the modes fix and move (not off, not a read-only panel)."""
    from . import actions

    return actions.doctor_mode() in ("fix", "move") and not get_settings().read_only


def _mbps_text(v) -> str:
    return "?" if v is None else f"{float(v):.0f}" if float(v) >= 10 else f"{float(v):.1f}"


def _zero_watch() -> None:
    """Every minute: the tunnels that are up are read in the live reader's numbers; one that nobody uses for ZERO_AFTER is looked into (see `_zero_look`). Nothing when the Doctor is off."""
    from ..models import is_standby
    from . import actions

    if actions.doctor_mode() == "off":
        return

    now = int(time.time())
    view = livebw.view()

    with session() as db:
        rows = [(t.id, t.name, bool((t.record or {}).get("silent"))) for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), Tunnel.state == "up")) if not is_standby(t) and not is_temp(t.name)]

    seen = set()

    for tid, name, marked in rows:
        seen.add(tid)
        r = view.get(str(tid))
        use = max(float(r.get("down") or 0), float(r.get("up") or 0)) if r and float(r.get("age") or 999) <= 120 else None
        entry, look = zero_step(_ZERO.get(tid), now, use, True)

        if entry is None:
            _ZERO.pop(tid, None)

            if marked and use is not None:
                _silent_set(tid, None)          # (the users are back: the mark goes)
        else:
            _ZERO[tid] = entry

        if look:
            _zero_look(tid, name, now - int(entry["since"]))

    for gone in set(_ZERO) - seen:
        _ZERO.pop(gone, None)


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

        if change in ("drop", "slow"):
            investigate(aid, tunnel_id)
        elif bw.get("state") == "drop":
            autopilot._say(aid, f"Still dropped: it usually gives {(bw.get('drop') or {}).get('from')} Mb/s. The reason found when it dropped: {(bw.get('why') or {}).get('text', 'not written yet')}", done=True, ok=True)
        elif bw.get("state") == "slow":
            autopilot._say(aid, f"Still very slow: {bw.get('down')} Mb/s. The reason found: {(bw.get('why') or {}).get('text', 'not written yet')}", done=True, ok=True)
        else:
            autopilot._say(aid, "Done.", done=True, ok=True)

    return autopilot._start(name, work, action="speed", allow_read_only=True)


def _retype_later(tunnel_id: int) -> None:
    """A very slow tunnel whose type suggestion stands (the Doctor page) and that the Doctor has not changed in the last 24 hours: the change is done now, as its own story (modes fix and move). Nothing when no suggestion
    stands (nothing faster is known), when the Doctor is off, or the panel is read-only."""
    from . import actions, autopilot, doctor

    if get_settings().read_only or actions.doctor_mode() == "off":
        return

    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        name = t.name if t is not None else ""

    if not name:
        return

    sug = next((x for x in doctor.state()["suggestions"] if x.get("tunnel_id") == tunnel_id and x.get("kind") == "type" and x.get("to_kind")), None)

    if sug is None or doctor.may_retype(name, doctor._state()["retype_done"], int(time.time())):
        return

    try:
        autopilot._start(name, doctor._run_retype, tunnel_id, sug["to_kind"], sug.get("now_kind") or "", sug.get("why") or "", True, action="autopilot", source="auto")
    except ValueError as e:
        log.info("the change of type of tunnel %s not started: %s", tunnel_id, str(e)[:100])


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
