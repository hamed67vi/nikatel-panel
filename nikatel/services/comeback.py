"""Back to the better tunnel: a disturbance that was only temporary does not leave the tunnel on its emergency type for good.

When the Doctor swaps a CUT tunnel for the first type that connects (autopilot `_switch_cut`), the type it had is kept as the one WANTED (`note`). The emergency type connects, but it is usually a slower one
(the robust kinds come first when a route is cut), and the cut may be a few minutes of a filter, a route event or a provider's bad hour. So the Doctor looks again, with growing waits (5, 10, 20, 30, 60 min,
2 h, then every 4 h, for three days): the wanted type is TESTED beside the running tunnel (a temporary tunnel, delay and loss only - the users are not touched), and when it connects clean and measures clearly
better than the running one (`autopilot.BETTER_BY` points) the tunnel is swapped back at a quiet moment. If it is only as good, the running type stays and the looking stops; if it does not connect (or loses
packets) the disturbance is still there and the next look comes later. Nothing is done:
  * while the Doctor is off or the panel is read-only, while a repair works on the tunnel, in a storm of cuts, or before the tunnel has been up for `UP_FOR` seconds;
  * to a tunnel somebody changed in the meantime (another type than the Doctor left it as: a manual swap, a later repair) - that entry is dropped, the owner's choice is never undone;
  * more than `MAX_RETURNS` times in 24 hours for one tunnel (a route that flaps does not make the tunnel swing between two types).
The tests are the same temporary-tunnel tests every repair uses, so they teach the Doctor's memory of the pair (`knowledge.record`) on the way.

Pure helpers (`wait_after`, `mark`, `due`, `outcome`, `may_return`) carry the decisions and are tested; `run` is the job (every 60 s), `look` the action it starts."""
from __future__ import annotations

import threading
import time

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Tunnel, is_standby, is_temp
from . import actions, autopilot, knowledge, tunnel_test
from . import settings as st
from .events import emit

log = get_logger("comeback")
BACKOFF = (300, 600, 1200, 1800, 3600, 7200, 14400)      # seconds before the n-th look (the first after the swap, then after each look that found the wanted type not there); the last value repeats
GIVE_UP = 3 * 86400                                       # the Doctor stops looking this long after the swap
UP_FOR = 180                                              # the tunnel must have been up this long: a repair that has just ended is left alone
MAX_RETURNS = 3                                           # returns of one tunnel inside RETURNS_WINDOW
RETURNS_WINDOW = 86400
BUSY_RETRY = 120                                          # the test place was taken or the tunnel was busy: look again in this many seconds (not counted as a look)
_LOCK = threading.Lock()
_RUNNING: set[str] = set()                                # the tunnels with a look in progress


# ------------------------------------------------------------------ pure helpers (tested)
def wait_after(tries: int) -> int:
    """Seconds before the next look, after `tries` looks (0 = right after the swap)."""
    return BACKOFF[min(max(int(tries), 0), len(BACKOFF) - 1)]


def mark(wants: dict, name: str, want: str, now_kind: str, cause: str, pair: str, now: int) -> dict:
    """The wants after a cut tunnel was swapped from `want` to `now_kind`. A tunnel already waiting for its type keeps the ORIGINAL wish (a second cut while it runs on the emergency type does not change what
    it should go back to) but starts the waits again; one that is swapped back to the wanted type needs nothing more."""
    out = {k: dict(v) for k, v in wants.items()}
    old = out.get(name)
    wish = old["kind"] if old else want

    if not wish or wish == now_kind:
        out.pop(name, None)
        return out

    out[name] = {"kind": wish, "now": now_kind, "cause": cause, "pair": pair, "left": int(old["left"]) if old else now, "tries": 0, "next": now + wait_after(0)}
    return out


def due(entry: dict, now: int) -> bool:
    return now >= int(entry.get("next") or 0)


def expired(entry: dict, now: int) -> bool:
    return now - int(entry.get("left") or now) > GIVE_UP


def may_return(returns: list[int], now: int) -> bool:
    """Fewer than MAX_RETURNS swaps back of this tunnel in the last 24 hours."""
    return sum(1 for t in returns if now - int(t) < RETURNS_WINDOW) < MAX_RETURNS


def outcome(current_score: float | None, results: list[dict]) -> tuple[str, dict | None]:
    """What the test of the wanted type says. ("swap", row): it connects, loses almost nothing and beats the running tunnel by BETTER_BY points. ("same", None): it connects clean but is not clearly better - the
    running type is as good, no need to look any more. ("not yet", None): it does not connect, or it loses packets - the disturbance is probably still there."""
    clean = [r for r in results if autopilot.connected(r) and (r.get("loss_pct") or 0) < autopilot.LOSSY_CLEAN]

    if not clean:
        return "not yet", None

    best = autopilot.choose_better(current_score, results)
    return ("swap", best) if best else ("same", None)


# ------------------------------------------------------------------ what is kept (settings "comeback")
def wants() -> dict:
    return {k: dict(v) for k, v in (st.get("comeback").get("wants") or {}).items()}


def _save(wants_now: dict | None = None, returns: dict | None = None) -> None:
    changes: dict = {}

    if wants_now is not None:
        changes["wants"] = wants_now

    if returns is not None:
        changes["returns"] = returns

    st.put("comeback", changes)


def note(name: str, iran_id: int, foreign_id: int, want: str, now_kind: str, cause: str, now: int | None = None) -> None:
    """A cut tunnel was swapped from `want` to `now_kind`: keep the wish (see `mark`)."""
    now = now or int(time.time())

    with _LOCK:
        _save(mark(wants(), name, want, now_kind, cause, knowledge.pair_key(iran_id, foreign_id), now))


def drop(name: str) -> None:
    with _LOCK:
        w = wants()

        if w.pop(name, None) is not None:
            _save(w)


def _later(name: str, seconds: int, tried: bool) -> None:
    """The next look of this tunnel is `seconds` away; `tried`: the look counts (the waits grow)."""
    with _LOCK:
        w = wants()

        if name in w:
            w[name]["next"] = int(time.time()) + seconds
            w[name]["tries"] = int(w[name].get("tries") or 0) + (1 if tried else 0)
            _save(w)


def _returned(name: str, now: int) -> None:
    with _LOCK:
        w, r = wants(), {k: [int(t) for t in v if now - int(t) < RETURNS_WINDOW] for k, v in (st.get("comeback").get("returns") or {}).items()}
        w.pop(name, None)
        r.setdefault(name, []).append(now)
        _save(w, {k: v for k, v in r.items() if v})


# ------------------------------------------------------------------ the job
def _tunnel_row(name: str) -> dict | None:
    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == name))

        if t is None or not t.enabled or is_temp(t.name) or is_standby(t):
            return None

        return {"id": t.id, "state": t.state, "since": int(t.state_since or 0)}


def run(now: int | None = None) -> None:
    """Every minute: the first tunnel whose look is due and which is quiet and healthy gets a look (one at a time: the test place is one)."""
    now = int(time.time() if now is None else now)
    todo = wants()

    if not todo or actions.doctor_mode() == "off" or get_settings().read_only:
        return

    busy = set(autopilot.repairing_names())
    returns = st.get("comeback").get("returns") or {}

    if _RUNNING:
        return          # one look at a time (the test place is one)

    for name, e in sorted(todo.items(), key=lambda kv: kv[1].get("next") or 0):
        row = _tunnel_row(name)

        if row is None or expired(e, now):
            drop(name)          # the tunnel is gone (removed, renamed, switched off) or three days passed
            continue

        if not due(e, now) or name in busy:
            continue

        if row["state"] != "up" or now - row["since"] < UP_FOR or autopilot.storm_now():
            continue

        if not may_return([int(t) for t in returns.get(name) or []], now):
            _later(name, 6 * 3600, False)          # (it has gone back and forth enough today)
            continue

        _spawn(name, row["id"])
        return


def _spawn(name: str, tunnel_id: int) -> int:
    """The look as an action (its story is in the Doctor's log) in its own thread. It does not take the tunnel's repair place while it only tests: a cut during the test is repaired at once."""
    with _LOCK:
        if name in _RUNNING:
            raise ValueError("a look is already running")

        _RUNNING.add(name)

    try:
        with session() as db:
            entry = ActionLog(ref=name, action="comeback", source="auto", story=["Starting…"])
            db.add(entry)
            db.flush()
            aid = entry.id
    except Exception:
        _RUNNING.discard(name)
        raise

    def work() -> None:
        try:
            look(aid, tunnel_id, name)
        except (RuntimeError, ValueError) as ex:
            log.warning("comeback #%s: stopped: %s", aid, str(ex)[:200])
            autopilot._say(aid, f"Stopped: {str(ex)[:200]}", done=True, ok=False)
        except Exception as ex:
            log.exception("comeback #%s crashed", aid)
            autopilot._say(aid, f"Stopped: {str(ex)[:200]}", done=True, ok=False)
        finally:
            _RUNNING.discard(name)

    threading.Thread(target=work, name=f"comeback-{name}", daemon=True).start()
    return aid


def _score_of(tunnel_id: int) -> float | None:
    """The running tunnel's score by delay and loss alone (the way the candidates are scored, so the two numbers can be compared)."""
    q = tunnel_test.quality(tunnel_id)
    return tunnel_test.score(q.get("latency_ms"), q.get("jitter_ms"), q.get("loss_pct"), None) if q.get("latency_ms") is not None else q.get("score")


def look(aid: int, tunnel_id: int, name: str) -> None:
    """One look at the wanted type of a tunnel that runs on its emergency type: test it beside the tunnel, swap back if it is clearly better."""
    from . import license

    def say(line: str, done: bool | None = None, ok: bool | None = None) -> None:
        autopilot._say(aid, line, done=done, ok=ok)

    e = wants().get(name)

    if e is None:
        say("Nothing is waiting to go back.", done=True, ok=True)
        return

    snap = autopilot._snapshot(tunnel_id)
    cur = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])

    if cur == e["kind"]:
        drop(name)
        say(f"{name} runs as {cur} again: nothing to go back to.", done=True, ok=True)
        return

    if cur != e["now"]:
        drop(name)
        say(f"{name} was changed to {cur} after the Doctor left it on {e['now']} (a swap by hand or a later repair): it is not touched, the wish to go back to {e['kind']} is forgotten.", done=True, ok=True)
        return

    if not license.tunnel_kind_allowed(e["kind"]):
        drop(name)
        say(f"{e['kind']} is not allowed by the license: not looking for it.", done=True, ok=True)
        return

    say(f"{name} runs as {cur} since {e['cause'].replace('_', ' ')} cut it as {e['kind']}. Is {e['kind']} back? (look {int(e.get('tries') or 0) + 1}; it is tested beside the tunnel, the users are not touched)")

    if not autopilot._probe_links(aid, snap, [e["kind"]]):
        _later(name, wait_after(int(e.get("tries") or 0) + 1), True)
        say(f"The link of {e['kind']} still does not pass between the two servers: the disturbance is not over. Next look in {wait_after(int(e.get('tries') or 0) + 1) // 60} min.", done=True, ok=True)
        return

    try:
        current = _score_of(tunnel_id)
    except (ValueError, RuntimeError, OSError) as ex:
        current = None
        say(f"The running tunnel could not be measured ({str(ex)[:80]}): the wanted type must connect and lose almost nothing.")

    try:
        results = autopilot._recommend_now(aid, snap["iran_id"], snap["foreign_id"], [e["kind"]], patience=120)
    except RuntimeError as ex:                  # the one test place is taken: not a look, try again soon
        if "still running" not in str(ex):
            raise

        _later(name, BUSY_RETRY, False)
        say(f"The test place is busy ({str(ex)[:80]}): looking again in {BUSY_RETRY // 60} min.", done=True, ok=True)
        return

    row = next((r for r in results if r.get("kind") == e["kind"]), None)
    detail = (f"score {row.get('score')}, delay {row.get('latency_ms')} ms, loss {row.get('loss_pct')} %" + (f", {str(row['error'])[:80]}" if row.get("error") else "")) if row else "no answer"
    say(f"The test of {e['kind']}: {detail}; the running tunnel scores {current}.")
    verdict, best = outcome(current, results)

    if verdict == "not yet":
        wait = wait_after(int(e.get("tries") or 0) + 1)
        _later(name, wait, True)
        say(f"{e['kind']} still does not connect clean: the disturbance is not over. Next look in {wait // 60} min.", done=True, ok=True)
        return

    if verdict == "same":
        drop(name)
        say(f"{e['kind']} connects but is not clearly better than {cur} (the running tunnel scores {current}, a swap needs {autopilot.BETTER_BY} points more): {cur} stays and the looking stops.", done=True, ok=True)
        return

    say(f"{e['kind']} is back: score {best.get('score')} against {current} for {cur}, loss {best.get('loss_pct')} %.")

    with autopilot._LOCK:
        if name in autopilot._BUSY and autopilot._BUSY[name] != aid:
            taken = True
        else:
            taken = False
            autopilot._BUSY[name] = aid          # the tunnel is mine while it is swapped: no repair starts on it when it is down for the moment of the swap

    if taken:
        _later(name, BUSY_RETRY, False)
        say("A repair works on the tunnel: looking again soon.", done=True, ok=True)
        return

    try:
        if not autopilot._wait_quiet(aid, tunnel_id, limit=900):
            _later(name, 600, False)
            say("The tunnel is busy: the swap back waits for a quiet moment (next look in 10 min).", done=True, ok=True)
            return

        ok = autopilot._replace(aid, tunnel_id, snap["iran_id"], snap["foreign_id"], e["kind"], f"The disturbance is over: back to {e['kind']}.", snap=snap)
    finally:
        with autopilot._LOCK:
            if autopilot._BUSY.get(name) == aid:
                autopilot._BUSY.pop(name, None)

    if ok:
        _returned(name, int(time.time()))

        with session() as db:
            emit(db, "ok", "tunnels", f"Tunnel {name} is back on {e['kind']}: the disturbance that moved it to {cur} is over ({e['kind']} scores {best.get('score')}, {cur} {current}).", name)

        say(f"{name} is back on {e['kind']}.", done=True, ok=True)
    else:
        _later(name, wait_after(int(e.get("tries") or 0) + 1), True)
        say(f"The build of {e['kind']} failed; {cur} was put back. Next look later.", done=True, ok=False)
