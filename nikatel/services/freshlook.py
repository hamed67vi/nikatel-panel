"""What a PROTECTED tunnel that loses packets is told, and what is looked at for it (1.69.271).

The owner, 2026-11-14, about the L2TP lifeline (a protected tunnel): its GRE link lost 15-65 % of its packets every evening and the smart repair ran about twenty times a day - each time it tested other tunnel
types beside it for eight minutes, found that a fresh link was clearly better, and was refused at the swap, because a protected tunnel is never replaced. Nothing was ever healed; each run only loaded the pair.
(A new GRE key was tried by hand on 2026-11-14 and did NOT cure it: three different keys on the real link all lost 15-40 %, while a fresh link with another inner network lost 0-3 % in the same minutes,
and so did other inner addresses on the very same link. The loss belongs to the inner pair of THIS link, somewhere on the road between the two servers.)

Now a protected tunnel is not offered what protection forbids (no candidate tests, no swap, no move), is looked at at most every six hours, and the story says the one thing that decides what the owner can do:
does a FRESH link of the same kind between the same two servers lose less right now (the panel's own link probe: one temporary GRE link, ten pings each way, removed again)? If it does, the loss belongs to THIS link
(its inner network), not to the route: a new link would cure it, and that is the owner's call (protection off, then Relink) - the tunnel itself is never touched. If the fresh link loses too, the route is poor and
nothing on this tunnel would help.
"""
from __future__ import annotations

import time

from ..db import session
from ..log import get_logger
from ..models import Tunnel
from . import linkprobe
from .events import emit

log = get_logger("freshlook")

GAP = 6 * 3600          # a protected tunnel is looked at this seldom (the repair used to run every half hour and only load the pair)
OWN_BAD = 15.0          # % loss on the tunnel's own link from which the look is worth making (the level users feel)
FRESH_OK = 10.0         # % loss of the fresh link (ten pings: one lost) that still counts as a clean link


def gap_left(rec: dict | None, now: int) -> int:
    """Pure: seconds until the next look is allowed (0 = now)."""
    at = int((((rec or {}).get("fresh_look")) or {}).get("at") or 0)
    return max(0, at + GAP - int(now))


def verdict(own: float | None, fresh: float | None) -> str:
    """Pure. "link": a fresh link is clean and this one is not (the loss belongs to this link); "route": the fresh link loses too; "clean": this link does not lose much now; "unknown": the look could not be made."""
    if fresh is None:
        return "unknown"

    if own is not None and own < OWN_BAD:
        return "clean"

    return "link" if fresh <= FRESH_OK else "route"


def sentence(name: str, own: float | None, fresh: float | None, v: str, why: str = "") -> str:
    """Pure: the plain sentence of the story (and of the event) for a verdict."""
    mine = "an unknown share of its packets" if own is None else f"{own:g} % of its packets"

    if v == "link":
        return (f"A fresh GRE link between the same two servers loses only {fresh:g} % right now, {name} loses {mine}: the loss belongs to THIS link (its inner network), not to the route between the servers. "
                f"A new link would cure it - but {name} is protected, so nothing was changed. Relink it (protection off first) when it suits you.")

    if v == "route":
        return f"A fresh GRE link between the same two servers loses {fresh:g} % as well: the route is poor right now, a new link would not help. Nothing to do on {name}."

    if v == "clean":
        return f"{name} loses only {own:g} % of its packets right now: nothing to look at."

    return f"The fresh-link look could not be made ({why or 'no answer'}): nothing can be said about the route. {name} is protected, so nothing was changed."


def _own_loss(tunnel_id: int) -> float | None:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        v = (t.detail or {}).get("loss_pct") if t is not None else None

    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def look(snap: dict, now: int | None = None) -> dict:
    """The look for one protected tunnel that loses packets: {"verdict", "text", "skipped"}. At most once every GAP seconds (the last one is told again instead). Only a temporary link is made (the link probe,
    which removes it); the tunnel is never touched. The verdict is kept in the tunnel's record (`fresh_look`) and a "link" verdict is also an event."""
    now = int(now if now is not None else time.time())
    name = snap["name"]
    last = (snap.get("record") or {}).get("fresh_look") or {}

    if left := gap_left(snap.get("record"), now):
        return {"verdict": last.get("verdict", "unknown"), "skipped": True,
                "text": f"{name} was looked at {(now - int(last.get('at') or now)) // 60} min ago ({last.get('verdict', 'unknown')}: its link {last.get('own')} %, a fresh one {last.get('fresh')} %): not again for {left // 60} min."}

    own = _own_loss(snap["id"])
    fresh: float | None = None
    why = ""

    try:
        r = (linkprobe.probe(snap["iran_id"], snap["foreign_id"], ["gre"], record=False) or {}).get("gre") or {}

        if r.get("ok") is not None:
            fresh = float(r.get("loss_pct") if r.get("loss_pct") is not None else 100.0)
        else:
            why = str(r.get("error") or "the probe could not tell")[:120]
    except (ValueError, RuntimeError) as e:          # (read-only, a server off, a probe that could not start)
        why = str(e)[:120]
    except Exception as e:                           # the look is advice: it never stops the repair
        log.warning("the fresh-link look of %s failed", name, exc_info=True)
        why = str(e)[:120]

    v = verdict(own, fresh)
    text = sentence(name, own, fresh, v, why)

    with session() as db:
        t = db.get(Tunnel, snap["id"])

        if t is not None:
            t.record = {**(t.record or {}), "fresh_look": {"at": now, "own": own, "fresh": fresh, "verdict": v}}

        if v == "link":
            emit(db, "warn", "tunnels", text, name)

    return {"verdict": v, "skipped": False, "text": text}
