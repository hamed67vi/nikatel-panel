"""Re-tune the tunnels that already run: apply the newer defaults of the Backhaul settings to every existing tunnel, gently.

The new defaults (more parallel connections, a bigger stream buffer) are for many users over lossy long-RTT paths. For a tunnel that exists they are applied like this,
one tunnel at a time:
  1. only a tunnel that is up, has little loss, whose two servers answer and that this panel built (its two config files are read from the servers, never guessed);
  2. at a quiet moment (little traffic), so customers are not cut - a tunnel that stays busy is skipped and tried at the next run;
  3. its health is measured, ONLY the two lines are changed in the files read from the servers, both services restarted (the Iran side first), the health measured again;
  4. when the tunnel does not come back up, or its loss or latency is clearly worse, the original files are put back and restarted.
After two roll-backs in a row the run stops: something else is going on. Everything is told in one story."""
from __future__ import annotations

import base64
import re
import time

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Tunnel, is_temp
from ..ssh import Node, SSHError, run_script
from . import actions, autopilot, builder, tunnel_test
from .tunnels import check_tunnel

log = get_logger("retune")
POOL = 24                   # connection_pool of a multiplexed tunnel (was 16)
STREAMBUF = 2097152         # mux_streambuffer (was 524288; the real test showed the window caps a stream at window / RTT)
WORSE_LOSS = 5.0            # % more loss than before = worse
WORSE_MS = 25.0             # ms more latency than before = worse
GAP = 90                    # seconds between two tunnels


def _set_line(text: str, key: str, value: int, add: bool) -> tuple[str, bool]:
    """Pure: key = value in a toml text; True when something changed. A missing line is added only when `add`."""
    pat = re.compile(rf"^{key}\s*=\s*(\S+)\s*$", re.MULTILINE)
    m = pat.search(text)

    if m:
        return (text, False) if int(m.group(1)) >= value else (pat.sub(f"{key} = {value}", text, count=1), True)

    return (text.rstrip("\n") + f"\n{key} = {value}\n", True) if add else (text, False)


def retune_texts(iran_text: str, foreign_text: str, pool: int = POOL, streambuf: int = STREAMBUF) -> tuple[str, str, bool]:
    """Pure: the two Backhaul config texts with the new values. Only a multiplexed tunnel (its server file has mux_con) is touched; a value that is already
    at least as big is left alone. Returns (iran, foreign, changed)."""
    if "mux_con" not in iran_text or "[client]" not in foreign_text or "[server]" not in iran_text:
        return iran_text, foreign_text, False

    f, c1 = _set_line(foreign_text, "connection_pool", pool, add=True)
    f, c2 = _set_line(f, "mux_streambuffer", streambuf, add=False)
    i, c3 = _set_line(iran_text, "mux_streambuffer", streambuf, add=False)
    return i, f, (c1 or c2 or c3)


def _paths(t: Tunnel) -> tuple[str, str, str, str]:
    tr, port = (t.transport or "tcpmux"), int(t.port or 0)
    return f"/etc/backhaul/iran-{tr}-{port}.toml", f"/etc/backhaul/kharej-{tr}-{port}.toml", f"backhaul-iran-{tr}-{port}.service", f"backhaul-kharej-{tr}-{port}.service"


def _read(node: Node, path: str) -> str:
    out = run_script(node, "read_cfg", {"PATH": path}, timeout=30)
    return base64.b64decode("".join(out.split())).decode() if out.strip() else ""


def candidates() -> list[int]:
    with session() as db:
        rows = [t for t in db.scalars(select(Tunnel).order_by(Tunnel.name))
                if t.enabled and not is_temp(t.name) and t.state == "up" and t.type == "backhaul" and (t.record or {}).get("built_by") == "nikatel-panel"
                and not (t.record or {}).get("legacy_gre") and ((t.detail or {}).get("loss_pct") or 0) < autopilot.ACT_FROM and t.iran.online is not False and t.foreign.online is not False]
        rows.sort(key=lambda t: (t.rx_mbps or 0) + (t.tx_mbps or 0))         # the idlest first: they are the ones that can be changed without cutting anybody
        return [t.id for t in rows]


def _measure(tid: int) -> dict:
    try:
        return tunnel_test.quality(tid)
    except (ValueError, SSHError) as e:
        return {"error": str(e)[:100]}


def _state(tid: int) -> str:
    check_tunnel(tid)

    with session() as db:
        t = db.get(Tunnel, tid)
        return t.state if t is not None else "gone"


def tune_one(aid: int, tid: int) -> str:
    """'done' | 'same' (nothing to change) | 'skipped' | 'rolled back'."""
    with session() as db:
        t = db.get(Tunnel, tid)
        name = t.name
        iran = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port)
        foreign = Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port)
        ip, fp, _iu, _fu = _paths(t)

    try:
        old_i, old_f = _read(iran, ip), _read(foreign, fp)
    except (SSHError, ValueError) as e:
        autopilot._say(aid, f"{name}: skipped - its config could not be read ({str(e)[:80]})")
        return "skipped"

    new_i, new_f, changed = retune_texts(old_i, old_f)

    if not changed:
        autopilot._say(aid, f"{name}: nothing to change (already tuned, or not a multiplexed tunnel)")
        return "same"

    base = _measure(tid)

    if not autopilot._wait_quiet(aid, tid, limit=900):
        autopilot._say(aid, f"{name}: skipped - it stays busy; tried again at the next run")
        return "skipped"

    autopilot._say(aid, f"{name}: applying (connections {POOL}, stream buffer {STREAMBUF // 1024} KB)…")

    try:
        builder._put(iran, ip, new_i, "0600")
        builder._put(foreign, fp, new_f, "0600")
        actions.restart_now(tid, "retune")
    except (SSHError, ValueError) as e:
        autopilot._say(aid, f"{name}: restart problem - {str(e)[:80]}")

    time.sleep(20)
    state = _state(tid)
    after = _measure(tid) if state == "up" else {}
    both = not base.get("error") and not after.get("error")        # compared only when both measurements worked; an "up" tunnel that could not be measured is kept
    worse = state != "up" or (both and ((after.get("loss_pct") or 0) > (base.get("loss_pct") or 0) + WORSE_LOSS or (after.get("latency_ms") or 0) > (base.get("latency_ms") or 0) + WORSE_MS))

    if not worse:
        with session() as db:
            t = db.get(Tunnel, tid)

            if t is not None:
                rec = dict(t.record or {})
                rec["opts"] = {**(rec.get("opts") or {}), "connection_pool": POOL, "mux_streambuffer": STREAMBUF}
                t.record = rec

        autopilot._say(aid, f"{name}: kept - ping {base.get('latency_ms')}→{after.get('latency_ms')} ms, loss {base.get('loss_pct')}→{after.get('loss_pct')} %")
        return "done"

    autopilot._say(aid, f"{name}: worse or not up ({state}; ping {base.get('latency_ms')}→{after.get('latency_ms')} ms, loss {base.get('loss_pct')}→{after.get('loss_pct')} %): putting the original config back")

    try:
        builder._put(iran, ip, old_i, "0600")
        builder._put(foreign, fp, old_f, "0600")
        actions.restart_now(tid, "retune rollback")
        back = _state(tid)
        autopilot._say(aid, f"{name}: original config back, tunnel is {back}")
    except (SSHError, ValueError) as e:
        autopilot._say(aid, f"{name}: the roll-back needs a human: {str(e)[:100]}")

    return "rolled back"


def run(aid: int, limit: int = 0) -> bool:
    ids = candidates()[: limit or None]
    autopilot._say(aid, f"Re-tuning {len(ids)} tunnel(s), one at a time, at quiet moments; each is checked and rolled back if it gets worse.")
    results, back_in_a_row = {}, 0

    for n, tid in enumerate(ids):
        if autopilot.storm_now():
            autopilot._say(aid, "Stopped: a mass disruption is going on - nothing more is changed now.")
            break

        with session() as db:
            nm = db.get(Tunnel, tid).name if db.get(Tunnel, tid) is not None else str(tid)

        try:
            r = tune_one(aid, tid)
        except Exception as e:
            log.exception("retune of %s failed", nm)
            autopilot._say(aid, f"{nm}: error - {str(e)[:100]}")
            r = "skipped"

        results[nm] = r
        back_in_a_row = back_in_a_row + 1 if r == "rolled back" else 0

        if back_in_a_row >= 2:
            autopilot._say(aid, "Stopped: two roll-backs in a row - something else is going on, a human should look.")
            break

        if r in ("done", "rolled back") and n < len(ids) - 1:
            time.sleep(GAP)

    done = sum(1 for r in results.values() if r == "done")
    autopilot._say(aid, f"Finished: {done} tuned, {sum(1 for r in results.values() if r == 'rolled back')} rolled back, {sum(1 for r in results.values() if r == 'skipped')} skipped, "
                        f"{sum(1 for r in results.values() if r == 'same')} already fine.", done=True, ok=not any(r == "rolled back" for r in results.values()))
    return done > 0


def start(limit: int = 0) -> int:
    return autopilot._start("retune all tunnels", run, limit, action="retune")
