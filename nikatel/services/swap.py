"""MAKE BEFORE BREAK: the automatic change of a tunnel's type without a cut (the owner, 2026-10-21: "make the tunnels steady fast, WITHOUT the customer noticing").

Until 1.69.77 the Doctor changed the type of a dropped or very slow tunnel by REMOVING it and building it again (`autopilot._replace`): the users were cut for a minute or two. Now the new type is built BESIDE the old
one, as the warm standby of the tunnel (`standby.py`), and the old tunnel is removed only when the new one has shown that it carries:

  1. the standby of the new type is built on the shifted ports (an older standby of another type is a spare that carries nobody: it is built again) - the tunnel keeps carrying its users meanwhile;
  2. the new tunnel is measured by a speed test through ITSELF (`bandwidth.measure`): it must give clearly more than the dropped tunnel gives now (`confirm_ok`);
  3. the users' NEW connections are handed to it (`standby.handover`, a NAT redirect, about two seconds; the open ones stay on the old tunnel) and it is watched for a minute and a half: up, little loss,
     data flowing (`carry_verdict`). If it is worse, the new connections go back (`standby.give_back`) and the old tunnel was never touched;
  4. the old tunnel is waited for until its open connections have ended (`autopilot._wait_quiet`, however bad its line is - nobody is cut), and only then removed and built again as the new type, while the users
     stand on the standby (when that build fails the old type is built again, as `autopilot._replace` does - nobody is cut either);
  5. the new connections go back to the rebuilt tunnel (the way back of the standby); the connections that are left on the standby end there.

At every step the users stand on a tunnel that works. Whatever ends the story - a refusal, a crash - the new connections are given back (`finally`). When the swap cannot even start (no port to hand over, a busy
server, the licence, the tunnel not up) the Doctor does NOT cut the users by itself: the change is a SUGGESTION on the Doctor page (one click = the old way, with its cut).

The rails of every automatic change stay: once in 24 hours per tunnel (`doctor.mark_retyped`, counted when the users are first touched), only the modes fix and move, and only a type whose steadiness is proven
(the callers' rule: `knowledge.STEADY_MIN_TESTS` tests, `STEADY_OK`)."""
from __future__ import annotations

import time

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel
from ..ssh import SSHError
from .events import emit

log = get_logger("swap")
BUILD_WAIT = 900                # the standby of the new type must be up within this long (seconds)
POLL = 10                       # seconds between two looks while it is built
SETTLE = 90                     # the new tunnel is watched this long after the users' new connections came to it ...
SETTLE_STEP = 15                # ... one look this often
CARRY_MBPS = 0.05               # the users' data through the new tunnel (both ways together) counts as "carries" from this
BAD_LOSS = 5.0                  # % of the packets the new tunnel may lose while it carries the users
CONFIRM_GAIN = 1.5              # the speed test through the new tunnel must give this many times what the dropped tunnel gives now ...
DRAIN_LIMIT = 1800              # ... the old tunnel is waited for this long (seconds) until its open connections have ended
RETRY_GAP = 3 * 3600            # a swap that did not finish is not started again for the same tunnel before this
_TRIED: dict[str, int] = {}     # tunnel name -> when a swap of it was last started


# ------------------------------------------------------------------ pure decisions
def confirm_ok(down: float | None, before: float | None) -> str:
    """'' when the speed test through the new tunnel backs the change, else the sentence that says why not. `before`: what the dropped tunnel gives now (Mb/s). The new one must give CONFIRM_GAIN times that and at
    least `knowledge.FASTER_MIN_MBPS`."""
    from . import knowledge

    if down is None:
        return "the speed test through the new tunnel gave no number"

    need = max(float(knowledge.FASTER_MIN_MBPS), CONFIRM_GAIN * float(before or 0))
    return "" if float(down) >= need else f"it gave {float(down):.0f} Mb/s in the speed test through itself, less than the {need:.0f} Mb/s that make a change worth it"


def carry_verdict(samples: list[dict]) -> tuple[str, str]:
    """('carries' | 'idle' | 'bad', the sentence) from the looks at the new tunnel after the users' new connections came to it; `samples`: [{"state", "rx", "tx", "loss"}] in time order. 'bad': the last look found it
    not up, or it lost more than BAD_LOSS % of the packets (the mean of the last three looks that knew). 'carries': data flowed through it. 'idle': nothing flowed (nobody used it, or its counters are not
    readable) - the speed test through the tunnel itself is then what backs it."""
    if not samples:
        return "idle", "no look was made"

    last = samples[-1]

    if last.get("state") != "up":
        return "bad", f"it is {last.get('state') or 'not up'}"

    losses = [float(s["loss"]) for s in samples if s.get("loss") is not None][-3:]

    if losses and sum(losses) / len(losses) > BAD_LOSS:
        return "bad", f"it loses {sum(losses) / len(losses):.0f} % of the packets"

    flow = max((float(s.get("rx") or 0) + float(s.get("tx") or 0)) for s in samples)

    if flow >= CARRY_MBPS:
        return "carries", f"the users' data flows through it ({flow:.1f} Mb/s)"

    return "idle", "no user data flowed through it in this time"


# ------------------------------------------------------------------ the pieces
def _sample(sb_id: int) -> dict:
    """One fresh look at the standby (its row after a check)."""
    from . import tunnels

    try:
        tunnels.check_tunnel(sb_id)
    except (ValueError, RuntimeError, SSHError, OSError) as e:
        log.info("swap: the look at the new tunnel failed: %s", str(e)[:100])

    with session() as db:
        t = db.get(Tunnel, sb_id)

        if t is None:
            return {"state": "gone"}

        return {"state": t.state, "rx": t.rx_mbps, "tx": t.tx_mbps, "loss": (t.detail or {}).get("loss_pct")}


def _settle(sb_id: int) -> tuple[str, str]:
    samples: list[dict] = []

    for _ in range(max(1, SETTLE // max(1, SETTLE_STEP))):
        time.sleep(SETTLE_STEP)
        samples.append(_sample(sb_id))
        verdict = carry_verdict(samples)

        if verdict[0] == "bad" or (verdict[0] == "carries" and len(samples) >= 2):
            return verdict

    return carry_verdict(samples)


def _await_ready(name: str, kind: str, limit: float = BUILD_WAIT) -> tuple[dict | None, str]:
    """(the standby once it is `kind` and up, '') or (None, why not). Waits for the build that the swap (or the standby job, which obeys `standby.want`) started."""
    from . import standby, tunnels

    end = time.time() + limit

    while True:
        building = name in standby._BUILDS
        s = standby._view(name)["s"]

        if s is not None and s["kind"] == kind and not s["stale"]:
            if s["state"] != "up" and not building:
                try:
                    tunnels.check_tunnel(s["id"])
                except (ValueError, RuntimeError, SSHError, OSError):
                    pass

                s = standby._view(name)["s"] or s

            if s["state"] == "up":
                return s, ""

        elif not building and s is None:
            return None, str(standby._failed(name).get("why") or "the build did not finish")

        if time.time() > end:
            return None, f"it was not up after {int(limit) // 60} minutes"

        time.sleep(POLL)


def _wait_standby_quiet(name: str) -> None:
    """The standby the swap borrowed (the owner had switched the standby off for this tunnel) goes away only when the connections that stayed on it have ended."""
    from . import autopilot, standby

    end = time.time() + DRAIN_LIMIT

    while time.time() < end:
        s = standby._view(name)["s"]

        if s is None:
            return

        x = _sample(s["id"])

        if autopilot.quiet_ok(x.get("rx"), x.get("tx"), None):
            return

        time.sleep(POLL)


def _suggest(aid: int, snap: dict, cur: str, kind: str, why: str, reason: str, before: float) -> None:
    """The swap cannot be made beside the tunnel: it is NOT done and NOT suggested (the owner, 2026-10-22: "no more suggestions - when it sees a steady tunnel with better quality it does it by itself"). A change the old way would
    cut the users, so the Doctor leaves the tunnel as it is and looks again at its next round. (The name is old: nothing is suggested now.)"""
    from . import autopilot

    autopilot._say(aid, f"{snap['name']} is NOT changed to {kind} by itself: {reason}. A change would cut the users, so the Doctor leaves it as it is and looks again later; it suggests nothing.", done=True, ok=True)

# ------------------------------------------------------------------ the story
def beside(aid: int, snap: dict, kind: str, why: str, before: float) -> None:
    """Change the type of the tunnel in `snap` to `kind` without a cut (see the module's text). `why`: the sentence of the change; `before`: what the tunnel gives now (Mb/s)."""
    from . import autopilot, standby

    say = lambda line, **kw: autopilot._say(aid, line, **kw)
    name = snap["name"]
    cur = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])
    now = int(time.time())

    if get_settings().read_only:
        say("The panel is read-only: nothing is changed.", done=True, ok=False)
        return

    if now - _TRIED.get(name, 0) < RETRY_GAP:
        say(f"A swap of {name} was tried {(now - _TRIED[name]) // 60} minutes ago and did not finish: it is not tried again before {RETRY_GAP // 3600} hours have passed.", done=True, ok=False)
        return

    v = standby._view(name)
    p, s = v["p"], v["s"]

    if p is None or p["state"] != "up":
        _suggest(aid, snap, cur, kind, why, f"{name} is not up, and a tunnel that is not up cannot hand its users to a new one", before)
        return

    if s is not None and s["use"]:
        _suggest(aid, snap, cur, kind, why, f"the users' ports of {name} stand on its standby now", before)
        return

    try:
        standby.build_plan(name, kind)                     # (only a look: the ports, the licence, the load of the two servers - nothing is built or removed yet)
    except ValueError as e:
        _suggest(aid, snap, cur, kind, why, str(e), before)
        return

    if not autopilot._probe_links(aid, snap, [kind]):
        say(f"The link of {kind} does not pass between {snap['iran_name']} and {snap['foreign_name']}: nothing is built, {name} is not touched.", done=True, ok=False)
        return

    _TRIED[name] = now
    say(f"{name}: changing the type {cur} → {kind} WITHOUT a cut - {kind} is built beside {cur}, the users go over to it, and {cur} is removed last. {why}")
    was = standby.borrow_mark(name)

    try:
        _work(aid, snap, cur, kind, why, before, say)
    finally:
        standby.unwant(name)

        if not was["marked"] and was["optout"]:
            _wait_standby_quiet(name)
            standby.return_mark(name, was)


def _work(aid: int, snap: dict, cur: str, kind: str, why: str, before: float, say) -> None:
    from . import autopilot, bandwidth, doctor, failover, knowledge, standby

    name = snap["name"]
    s = standby._view(name)["s"]

    if s is not None and (s["kind"] != kind or s["stale"]):
        say(f"The standby of {name} is {s['kind']}: it is a spare that carries nobody, so it is built again as {kind}; {name} keeps carrying its users.")

        try:
            standby.remove_standby(name)
        except (ValueError, RuntimeError, SSHError) as e:
            say(f"The old standby could not be removed ({str(e)[:140]}): nothing was changed.", done=True, ok=False)
            return

        s = None

    if s is None:
        standby.want(name, kind)

        try:
            standby.build(name, kind, by_swap=True)
        except ValueError as e:
            if "being built" not in str(e):
                say(f"The new tunnel cannot be built now ({str(e)[:140]}): nothing was changed.", done=True, ok=False)
                return

        say(f"Building {kind} beside {cur} (a minute or two); {name} carries its users meanwhile.")

    s, reason = _await_ready(name, kind)

    if s is None:
        say(f"{kind} could not be built beside {cur} ({reason[:160]}): nothing was changed, {name} was never touched.", done=True, ok=False)
        return

    try:
        bw, _ = bandwidth.measure(s["id"])
        down = None if bw.get("down") is None else float(bw["down"])
    except (ValueError, RuntimeError, SSHError, OSError) as e:
        say(f"The new tunnel could not be measured ({str(e)[:120]}): the users stay on {cur}.", done=True, ok=False)
        return

    bad = confirm_ok(down, before)

    if bad:
        say(f"{kind} is up beside {cur}, but {bad}: the users stay on {cur}.", done=True, ok=False)
        return

    say(f"{kind} gives {down:g} Mb/s in the speed test through itself (the dropped {cur} gives {before:g}). The users' new connections go over to it now.")
    doctor.mark_retyped(name, int(time.time()))

    try:
        standby.handover(name, why, manual=True)
    except (ValueError, RuntimeError, SSHError) as e:
        say(f"The new connections could not be handed over ({str(e)[:140]}): nothing was changed.", done=True, ok=False)
        return

    state = {"back": False}

    def back(label: str) -> bool:
        if state["back"]:
            return True

        try:
            standby.give_back(name, label)
            state["back"] = True
        except (ValueError, RuntimeError, SSHError) as e:
            say(f"The way back failed ({str(e)[:140]}): press Back now in the window of the tunnel.")

        return state["back"]

    ok = False

    try:
        verdict, note = _settle(s["id"])

        if verdict == "bad":
            say(f"{kind} does not carry the users well ({note}): the new connections go back to {cur}, which was never touched.", done=True, ok=False)
            back("swap beside: the new tunnel is worse")

            with session() as db:
                emit(db, "warn", "tunnels", f"Tunnel {name}: the Doctor built {kind} beside {cur}, but it did not carry well ({note}); the users stayed on {cur}.", name)

            return

        say(f"{kind} carries the users' new connections ({note}). Waiting up to {DRAIN_LIMIT // 60} minutes for the connections that are open on {cur} to end - it is not touched before.")

        if not autopilot._wait_quiet(aid, snap["id"], DRAIN_LIMIT, loss_counts=False):
            say(f"{name} still carries open connections after {DRAIN_LIMIT // 60} minutes: it is NOT touched (that would cut them). The new connections go back to it; {kind} stays as its standby.", done=True, ok=False)
            back("swap beside: the open connections did not end")
            return

        say(f"{name} is quiet: it is built again as {kind} while the users stand on {kind} beside it.")
        ok = autopilot._replace(aid, snap["id"], snap["iran_id"], snap["foreign_id"], kind, "Swap beside the tunnel: the old type dropped.", snap=snap)
    finally:
        if not state["back"]:
            back("swap beside: the tunnel is built again" if ok else "swap beside: ended")

    if not ok:
        if state["back"]:
            say(f"The build of {kind} failed; {cur} was put back. Nobody was cut.", done=True, ok=False)

        return

    say("The new connections are on the tunnel again; the connections that are left on the standby end there.")
    after = ""

    try:
        with session() as db:
            new_id = db.scalar(select(Tunnel.id).where(Tunnel.name == name))

        bw, _ = bandwidth.measure(new_id)
        got = float(bw.get("down") or 0)
        after = f" It gives {got:g} Mb/s in the Doctor's test now (before: {before:g})."

        try:
            knowledge.record(snap["iran_id"], snap["foreign_id"], {"kind": kind, "ok": True, "works": True, "down_mbps": got}, "full")
        except Exception:
            log.exception("the speed of %s could not be kept", kind)
    except (ValueError, RuntimeError, SSHError, OSError) as e:
        after = f" It could not be measured just now ({str(e)[:80]}): the Doctor measures it in a few minutes."

    with session() as db:
        emit(db, "ok", "tunnels", f"Tunnel {name} is now {kind} (it was {cur}): the Doctor changed it without a cut.{after}", name)

    try:
        failover._notice(f"✅ {name}: {cur} → {kind} without a cut.{after}")
    except Exception:
        log.exception("the notice of the swap could not be sent")

    d = doctor._state()
    d["suggestions"] = [x for x in d["suggestions"] if x.get("tunnel") != name]          # (what was suggested for this tunnel is done)
    doctor._save(d)
    say(f"{name} runs as {kind} now.{after}", done=True, ok=True)

