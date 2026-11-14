"""THE CHRONIC LOOK: a tunnel that keeps being cut is moved to a steadier Iran server, and a server that keeps costing its tunnels says so (1.69.269).

(The owner, 2026-11-14, after a tunnel to the UK was cut twice in 50 minutes on an Iran server the panel's host cannot even reach - "yes, build" for the suggestion of a chronic score: "after a few cuts the Doctor stops
patching and moves the tunnel to a steadier server".)

Why. The Doctor mends a cut tunnel again and again - restart, rebuild, another type - and every mend is a cut for the users. A tunnel that was rebuilt thirty times is not a tunnel with bad luck: it stands on a server (or a
route) that does not hold. The pieces for the cure exist - the ranking of the Iran servers for the foreign server, the plan that moves ONE tunnel without pulling others along, the make-before-break move with its DNS - but
they only ran for a SPEED drop. Nothing counted the cuts.

  what counts      the runs of failed checks of a tunnel (`bandwidth._cuts_lately`: the checks are kept 14 days) or the cuts the Doctor remembers for its pair of servers (`knowledge.repeats`: they survive a rebuild,
                   which gives the tunnel a new row and an empty history) - the larger of the two: CUTS_WEEK cuts in the last 7 days AND CUTS_DAY of them in the last 24 hours. A tunnel that was cut eight times
                   last week and has been quiet since is mended, not chronic.
  what it does     (mode "move") the tunnel is moved to another Iran server by the same make-before-break move the Doctor makes for quality (`failover.quality_pick`: the server is on, ranks well, has room, answers the foreign
                   server with little loss and about the tunnel's own delay, has no cut tunnel of its own to it) - and never to a server that carries a chronic tunnel itself. Its rails: not a protected tunnel, only a
                   tunnel that is up or unstable now (a cut one belongs to the repair and the failover), not while a failover is on, `doctor.may_move` (a day's limit and a cooldown per tunnel), one chronic move a day for
                   the whole panel and one per tunnel in a week. The move plan must take this tunnel alone (a DNS record that other tunnels share is never moved).
  what it says     one event per chronic tunnel a day: how many cuts, on which server, what the Doctor did or why it did not. A server with two chronic tunnels (or many cuts across its tunnels) gets its own event: the
                   tunnels of a server that keeps failing are better off elsewhere, and the server may be worth retiring.
  what it is not   a verdict about speed (that is the quality move) and no change of a tunnel's type (that is the duel and the upgrade check).

The settings document `chronic`: `told`, `moved` (when, per tunnel), `last_move`. The pure parts (`is_chronic`, `pick`, `sentence`) carry the decisions and are tested."""
from __future__ import annotations

import time

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel, is_protected, is_standby, is_temp
from . import settings as st
from .events import emit

log = get_logger("chronic")
EVERY = 1800                   # seconds between two looks (the job is registered in main.py)
CUTS_WEEK = 8                  # cuts in 7 days ...
CUTS_DAY = 3                   # ... and this many of them in the last 24 hours: it is not over
MOVE_GAP = 24 * 3600           # one chronic move a day for the whole panel
PER_TUNNEL = 7 * 24 * 3600     # and one per tunnel in a week
TELL_EVERY = 24 * 3600         # one event per chronic tunnel a day
SERVER_TELL_EVERY = 48 * 3600
SERVER_CHRONIC = 2             # chronic tunnels on one Iran server that make the server worth a word
KEEP = 14 * 24 * 3600


# ------------------------------------------------------------------ pure decisions
def is_chronic(week: int, day: int) -> bool:
    """Pure: a tunnel cut CUTS_WEEK times in a week, CUTS_DAY of them in the last 24 hours (a cut tunnel the Doctor mended once a week is not chronic)."""
    return int(week) >= CUTS_WEEK and int(day) >= CUTS_DAY


def pick(ranking: list[dict], rtt: float | None, bad_servers: set[int]) -> dict | None:
    """Pure: the Iran server a chronic tunnel is moved to - the first of the ranking that `failover.quality_pick` accepts and that carries no chronic tunnel itself (`bad_servers`: the ids of the Iran servers that do)."""
    from . import failover

    return failover.quality_pick([c for c in ranking if int(c.get("id") or 0) not in bad_servers], rtt)


def sentence(r: dict, outcome: str) -> str:
    """Pure: the event of one chronic tunnel. `r`: name, iran_name, foreign_name, week, day, server_week, server_tunnels."""
    more = f" Its Iran server {r['iran_name']} had {r['server_week']} cuts across its {r['server_tunnels']} tunnels in the last 7 days." if r.get("server_tunnels", 0) > 1 else ""
    return f"Tunnel {r['name']} ({r['iran_name']} ⇄ {r['foreign_name']}) is chronic: cut {r['week']} times in 7 days, {r['day']} of them in the last 24 hours.{more} {outcome}"


# ------------------------------------------------------------------ the job
def _doc() -> dict:
    d = st.get("chronic")
    d.setdefault("told", {})
    d.setdefault("moved", {})
    d.setdefault("last_move", 0)
    return d


def _gather(now: int) -> list[dict]:
    """Every tunnel of the list with its cuts of the last 7 days and of the last 24 hours."""
    from . import bandwidth, knowledge

    with session() as db:
        rows = [{"id": t.id, "name": t.name, "state": t.state, "iran_id": t.iran_server_id, "foreign_id": t.foreign_server_id, "iran_name": t.iran.name, "foreign_name": t.foreign.name, "rtt": t.rtt_ms, "protected": is_protected(t)}
                for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_standby(t) and not is_temp(t.name)]

    for r in rows:
        r["week"] = max(bandwidth._cuts_lately(r["id"], now, 168), knowledge.repeats(r["iran_id"], r["foreign_id"], now, 7))
        r["day"] = max(bandwidth._cuts_lately(r["id"], now, 24), knowledge.repeats(r["iran_id"], r["foreign_id"], now, 1))

    return rows


def _try_move(r: dict, bad_servers: set[int], doc: dict, now: int) -> str:
    """Move one chronic tunnel to a steadier Iran server. "" when the move started, else the sentence that says why not."""
    from . import autopilot, doctor, failover

    if not doctor.can_move():
        return "The Doctor is not in the mode that moves tunnels (Fix + move), so it only tells you."

    why = failover.move_blocked()

    if why:
        return f"No move: {why}"

    if r["protected"]:
        return "It is protected: it is never moved."

    if r["state"] not in ("up", "degraded"):
        return "It is cut now: the repair and the failover look after it first."

    if r["name"] in autopilot._BUSY:
        return "A repair or a change of it is running now."

    if now - int(doc.get("last_move") or 0) < MOVE_GAP:
        return "Another tunnel was moved for this less than 24 hours ago (one a day for the whole panel)."

    if now - int(doc["moved"].get(r["name"]) or 0) < PER_TUNNEL:
        return "It was moved for this less than a week ago."

    why = doctor.may_move(r["name"], doctor._state()["moves"], now)

    if why:
        return f"No move now: {why}."

    try:
        cand = pick(autopilot.ranking(r["id"]), r["rtt"], bad_servers)
    except (ValueError, RuntimeError) as e:
        return f"The Iran servers could not be ranked ({str(e)[:100]})."

    if cand is None:
        return "No other Iran server is steadier for its foreign server (on, ranked well, with room, little loss, and no chronic tunnel of its own): it stays."

    try:
        plan = failover._make_plan(r["iran_id"], {r["id"]}, "quality", target_id=cand["id"])
    except (ValueError, RuntimeError) as e:
        return f"No move to {cand['name']}: {str(e)[:120]}."

    if plan["pulled"] or plan["problems"] or not plan["target"]:
        return f"No move to {cand['name']}: " + (("it would pull " + ", ".join(plan["pulled"]) + " along (they share a DNS record with it)") if plan["pulled"] else "; ".join(plan["problems"]) or "no plan") + "."

    why = (f"{r['name']} was cut {r['week']} times in 7 days ({r['day']} in the last 24 hours) on {r['iran_name']}; {cand['name']} reaches {r['foreign_name']} in {cand.get('rtt_ms')} ms with "
           f"{cand.get('loss_pct')} % loss and carries no chronic tunnel")

    try:
        doctor.move_now(r["id"], cand["id"], "quality", why)
    except ValueError as e:
        return f"The move was refused: {str(e)[:140]}."

    return ""


def run(now: int | None = None) -> None:
    """Job (every EVERY seconds): the chronic tunnels are counted, told and - in the mode move - one of them is moved."""
    if get_settings().read_only or jobs.stopping():
        return

    from . import actions, autopilot

    if actions.doctor_mode() == "off" or autopilot.quiet_now():
        return

    now = now or int(time.time())
    rows = _gather(now)
    bad = sorted((r for r in rows if is_chronic(r["week"], r["day"])), key=lambda r: (-r["day"], -r["week"]))

    if not bad:
        return

    doc = _doc()
    told, moved = {k: v for k, v in doc["told"].items() if now - int(v) < KEEP}, {k: v for k, v in doc["moved"].items() if now - int(v) < KEEP}
    per_server: dict[int, list[int]] = {}

    for r in rows:
        e = per_server.setdefault(r["iran_id"], [0, 0])
        e[0] += 1
        e[1] += r["week"]

    bad_servers = {r["iran_id"] for r in bad}
    started = False

    for r in bad:
        r["server_tunnels"], r["server_week"] = per_server[r["iran_id"]]
        outcome = ""

        if not started:
            outcome = _try_move(r, bad_servers, {"moved": moved, "last_move": doc["last_move"]}, now)

            if not outcome:
                started = True
                moved[r["name"]] = now
                outcome = "The Doctor moves it to a steadier Iran server (the new tunnel is built first, the DNS follows, the old one goes when it is quiet): see Tunnel Doctor → Moves."

        if now - int(told.get(r["name"]) or 0) >= TELL_EVERY:
            told[r["name"]] = now

            with session() as db:
                emit(db, "warn", "tunnels", sentence(r, outcome or "It was not moved this time."), r["name"])

    for sid, (n_tunnels, n_cuts) in per_server.items():
        chronic_here = [r for r in bad if r["iran_id"] == sid]
        key = f"server:{sid}"

        if len(chronic_here) >= SERVER_CHRONIC and now - int(told.get(key) or 0) >= SERVER_TELL_EVERY:
            told[key] = now
            name = chronic_here[0]["iran_name"]

            with session() as db:
                emit(db, "warn", "servers", f"Iran server {name}: {len(chronic_here)} of its {n_tunnels} tunnels are chronic ({n_cuts} cuts in 7 days across them). The tunnels of a server that keeps failing do better elsewhere; "
                                            "if it stays like this the server is worth retiring.", name)

    st.put("chronic", {"told": told, "moved": moved, "last_move": now if started else int(doc.get("last_move") or 0)})
