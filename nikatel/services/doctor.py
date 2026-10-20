"""The Tunnel Doctor's three modes, and the moves of tunnels between Iran servers.

  off   - watches and reports; every button still works by hand
  fix   - the repair ladder (restart, rebuild, a new GRE link, another tunnel type); a move to another Iran server is only SUGGESTED, and so is a change of TYPE for a tunnel that is very slow while
          the Doctor's memory of the pair knows a type that gave clearly more (1.69.14; one click, never by itself)
  move  - the ladder, plus the move of tunnels to another Iran server, by itself and without asking:
          * an Iran server that is OFF (no answer) for `server_minutes` (default 10): ALL of its tunnels go to the best healthy Iran
            server and the DNS records that carried its address follow - in whichever account they live (ArvanCloud, another
            ArvanCloud, Cloudflare). A provider-wide outage (several Iran servers off at once) is handled server by server, as long
            as one healthy Iran server is left; the storm guard of the repair never holds a move back.
          * an Iran server whose resources are exhausted (CPU or RAM >= 90 % for 15 minutes): its busiest tunnel goes to a server
            with room - one per hour per server.
          * NEVER for packet loss or a poor route: those climb the ladder, and when no tunnel type works a move is suggested.
          * back: a server that came back and stayed healthy for 3 hours gets back the tunnels that left it because it was off.

Rails: one tunnel moves at most MAX_PER_DAY times a day with COOLDOWN between two moves; when the source server is on, the new
tunnel is built and checked FIRST, then the DNS, and the old tunnel lives on for the TTL + 5 minutes before it is removed
(make before break); nothing moves while the panel is read-only. Every move is in the ledger (the Moves tab) with Return.

The pure helpers (`is_saturated`, `may_move`, `pick_busiest`, `ready_to_return`) carry the decisions and are tested."""
from __future__ import annotations

import time

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, ServerSample, Tunnel
from . import actions, autopilot, detect, failover, license, netwatch
from . import settings as st
from .events import emit

log = get_logger("doctor")
MODES = ("off", "fix", "move")
MAX_PER_DAY = 3             # moves of one tunnel in 24 hours (out and back count)
COOLDOWN = 2 * 3600         # between two moves of the same tunnel
BACK_AFTER = 3 * 3600       # a server that came back must stay healthy this long before its tunnels return
SATURATED = 90.0            # % CPU or RAM ...
SATURATED_FOR = 900         # ... over this many seconds of samples (one every 2 minutes)
SATURATED_MIN_SAMPLES = 5
RESOURCE_GAP = 3600         # one resource move per server per hour
TARGET_MIN_RESOURCES = 60   # a target for a resource move must have this much room
TARGET_MIN_QUALITY = 50
_SLOW = [0.0]


# ------------------------------------------------------------------ the mode
def mode() -> str:
    return actions.doctor_mode()


def can_move() -> bool:
    """Mode "move", the failover licensed, the panel writable."""
    return mode() == "move" and failover.ENABLED and license.has("failover") and not get_settings().read_only


# ------------------------------------------------------------------ state: the ledger of moves, the suggestions, who came back when
def _state() -> dict:
    s = st.get("doctor")
    s.setdefault("moves", [])
    s.setdefault("suggestions", [])
    s.setdefault("back", {})
    s.setdefault("tried", {})
    s.setdefault("res_at", {})
    s.setdefault("retype_done", {})
    return s


def _save(s: dict) -> None:
    st.put("doctor", {"moves": s["moves"][-60:], "suggestions": s["suggestions"][-20:], "back": s["back"], "tried": s["tried"], "res_at": s["res_at"], "retype_done": s["retype_done"]})


QUIET_WAIT = 300            # an automatic change of type waits this long for a quiet moment, then goes on
RETYPE_GAP = 24 * 3600     # the Doctor changes the TYPE of a very slow tunnel by itself at most once in this time (1.69.17)


def may_retype(name: str, retyped: dict, now: int, gap: int = RETYPE_GAP) -> str:
    """Pure: '' when the Doctor may change the type of this very slow tunnel by itself now, else the sentence that says why not. Only in the modes fix and move."""
    if mode() == "off":
        return "the Doctor is off"

    last = int((retyped or {}).get(name) or 0)

    if last and now - last < gap:
        return f"{name} was changed by the Doctor {(now - last) // 3600} hours ago (once in {gap // 3600} hours)"

    return ""


def mark_retyped(name: str, now: int) -> None:
    s = _state()
    s["retype_done"] = {k: v for k, v in s["retype_done"].items() if now - int(v) < RETYPE_GAP * 7}
    s["retype_done"][name] = now
    _save(s)


HEALTHY_FOR = 600           # a suggestion to move a tunnel is withdrawn once the tunnel has been up, without loss, for this long: the problem it was written for is over


def suggestion_valid(e: dict, tunnel: dict | None, saturated: set[int], now: int) -> str:
    """Pure: '' when the suggestion still stands, else the sentence that says why it is withdrawn. `tunnel`: {"state", "state_since", "loss"} of the tunnel it is about, None when that tunnel is gone;
    `saturated`: the ids of the Iran servers that are exhausted now. (The owner, 2026-10-16: three suggestions of two days earlier stood on the Doctor's page for tunnels that had been up and clean for a day.)"""
    if tunnel is None:
        return "the tunnel is not in the list any more"

    if e.get("kind") == "resources":
        return "" if int(e.get("from_id") or 0) in saturated else f"{e.get('from') or 'the server'} is no longer exhausted"

    if e.get("kind") == "capacity":                              # (stands while the capacity of the tunnel is filling)
        return "" if tunnel.get("cap_state") in ("warm", "full") else f"{e.get('tunnel')} no longer fills its capacity"

    if e.get("kind") == "type" and e.get("upgrade"):             # (a better type known for a tunnel that WORKS: it stands while the tunnel runs the same type, a week - then the look asks again)
        if tunnel.get("kind") and e.get("now_kind") and tunnel["kind"] != e["now_kind"]:
            return f"{e.get('tunnel')} runs as {tunnel['kind']} now, not as {e['now_kind']}"

        return "" if now - int(e.get("at") or now) < 7 * 86400 else "the suggestion is a week old: the Doctor looks again"

    if e.get("kind") == "type":                                  # (a very slow tunnel is UP and clean all the time: the rule below would withdraw it at once; this one stands until the speed is back or the type was changed)
        if tunnel.get("kind") and e.get("now_kind") and tunnel["kind"] != e["now_kind"]:
            return f"{e.get('tunnel')} runs as {tunnel['kind']} now, not as {e['now_kind']}"

        return "" if tunnel.get("bw_state") in ("slow", "drop") else f"{e.get('tunnel')} is no longer very slow"

    healthy = tunnel.get("state") == "up" and (tunnel.get("loss") or 0) < autopilot.ACT_FROM and now - int(tunnel.get("state_since") or now) >= HEALTHY_FOR

    return f"{e.get('tunnel')} has been up without loss for {(now - int(tunnel.get('state_since') or now)) // 60} minutes: the problem is over" if healthy else ""


def refresh_suggestions(now: int | None = None) -> list[str]:
    """Withdraw the suggestions whose problem is over (see `suggestion_valid`); returns the sentences. Called by the job and whenever the Doctor's state is read, so the page never shows an old verdict."""
    from . import capacity

    s = _state()

    if not s["suggestions"]:
        return []

    now = now or int(time.time())

    with session() as db:
        rows = {t.name: {"state": t.state, "state_since": t.state_since, "loss": (t.detail or {}).get("loss_pct"), "kind": autopilot.kind_of(t.type, t.transport, t.record or {}), "bw_state": ((t.record or {}).get("bw") or {}).get("state"), "cap_state": capacity.state_of(t.name)}
                for t in db.scalars(select(Tunnel))}

    sat = {x["id"] for x in saturated_servers(now)}
    kept, told = [], []

    for e in s["suggestions"]:
        why = suggestion_valid(e, rows.get(e.get("tunnel")), sat, now)

        if why:
            told.append(f"{e.get('tunnel')}: {why}")
        else:
            kept.append(e)

    if told:
        s["suggestions"] = kept
        _save(s)

        with session() as db:
            for line in told:
                emit(db, "info", "actions", f"Doctor: a suggestion was withdrawn - {line}.", line.split(":")[0])

    return told


def state() -> dict:
    from . import userq

    refresh_suggestions()
    s = _state()
    conf = st.get("repair")
    return {"mode": mode(), "can_move": can_move(), "licensed": license.has("failover"), "server_minutes": int(conf.get("server_minutes", 10)), "after_seconds": actions.DOWN_GRACE,
            "moves": list(reversed(s["moves"]))[:40], "suggestions": list(reversed(s["suggestions"])), "back": {k: v for k, v in s["back"].items()},
            "users": userq.linked_state(), "rails": {"max_per_day": MAX_PER_DAY, "cooldown_hours": COOLDOWN // 3600, "back_after_hours": BACK_AFTER // 3600, "saturated": SATURATED, "saturated_minutes": SATURATED_FOR // 60}}


# ------------------------------------------------------------------ pure decisions
def is_saturated(samples: list[tuple[float, float]], min_n: int = SATURATED_MIN_SAMPLES, limit: float = SATURATED) -> str:
    """Pure: the sentence that says why a server counts as exhausted ('' when it does not). `samples` are (cpu, ram) over the window, newest or oldest first."""
    if len(samples) < min_n:
        return ""

    if all(c >= limit for c, _ in samples):
        return f"CPU at or above {limit:g} % for {SATURATED_FOR // 60} minutes"

    if all(r >= limit for _, r in samples):
        return f"RAM at or above {limit:g} % for {SATURATED_FOR // 60} minutes"

    return ""


def may_move(name: str, moves: list[dict], now: int, max_per_day: int = MAX_PER_DAY, cooldown: int = COOLDOWN) -> str:
    """Pure: '' when the tunnel may move now, else the sentence that says why not (too many moves today, the cooldown)."""
    mine = [m for m in moves if name in (m.get("tunnels") or []) and m.get("ok") is not False]
    today = [m for m in mine if now - int(m.get("at", 0)) < 86400]

    if len(today) >= max_per_day:
        return f"{name} already moved {len(today)} times in the last 24 hours (the limit is {max_per_day})"

    last = max((int(m.get("at", 0)) for m in mine), default=0)

    if last and now - last < cooldown:
        return f"{name} moved {(now - last) // 60} minutes ago; the next move waits {cooldown // 3600} hours"

    return ""


def pick_busiest(tunnels: list[dict]) -> dict | None:
    """Pure: the tunnel that carries the most traffic among those that are up (the one whose move relieves the server most)."""
    up = [t for t in tunnels if t.get("state") == "up"]
    return max(up, key=lambda t: (t.get("rx") or 0) + (t.get("tx") or 0)) if up else None


def ready_to_return(entry: dict, back_since: int | None, now: int, net_level: str, back_after: int = BACK_AFTER) -> str:
    """Pure: '' when the tunnels of this ledger entry may go back to the server they left, else why not."""
    if entry.get("returned") or entry.get("reason") != "down" or entry.get("ok") is False:
        return "not a move that returns"

    if not back_since:
        return "the server is still off"

    if now - int(back_since) < back_after:
        return f"the server has been back for {(now - int(back_since)) // 60} minutes; it must stay healthy for {back_after // 3600} hours"

    if net_level in ("bad", "down"):
        return "the server's own network is still disturbed"

    return ""


# ------------------------------------------------------------------ the ledger and the suggestions
def record_move(p: dict, moved: list[str], leftover: list[dict], ok: bool) -> dict:
    """Written by failover._work when a plan ran: one ledger entry per plan. A return marks the entry it undoes."""
    s = _state()
    now = int(time.time())
    e = {"id": p["id"], "at": now, "reason": p.get("reason", "down"), "why": p.get("why", ""), "tunnels": moved, "asked": [t["name"] for t in p.get("tunnels") or []],
         "from_id": p["server_id"], "from": p["server"], "from_ip": p["ip"], "to_id": (p.get("target") or {}).get("id"), "to": (p.get("target") or {}).get("name"), "to_ip": (p.get("target") or {}).get("ip"),
         "dns": len(p.get("dns") or []), "ok": ok, "returned": False, "returned_at": None, "leftover": leftover}

    if e["reason"] == "return":
        for old in s["moves"]:
            if not old.get("returned") and old.get("reason") == "down" and old.get("to_id") == p["server_id"] and set(moved) & set(old.get("tunnels") or []):
                old["tunnels"] = [n for n in old["tunnels"] if n not in moved]

                if not old["tunnels"]:
                    old["returned"], old["returned_at"] = True, now

    s["moves"].append(e)
    s["suggestions"] = [x for x in s["suggestions"] if x.get("tunnel") not in moved]
    _save(s)
    return e


def suggest(kind: str, tunnel_id: int, tunnel: str, from_id: int, from_name: str, to_id: int | None, to_name: str, why: str, extra: dict | None = None) -> dict:
    """A move the Doctor would make in mode "move": written down for the operator (one per tunnel, the newest wins). kind "type" (1.69.14): not a move - a change of the tunnel's type (`from`: the running type,
    `to`: the type the Doctor's memory has seen give clearly more on this pair; `extra`: now_kind, to_kind, now_mbps, memory_mbps)."""
    s = _state()
    s["suggestions"] = [x for x in s["suggestions"] if x.get("tunnel") != tunnel]
    e = {"id": f"s-{int(time.time())}-{tunnel_id}", "at": int(time.time()), "kind": kind, "tunnel_id": tunnel_id, "tunnel": tunnel, "from_id": from_id, "from": from_name, "to_id": to_id, "to": to_name, "why": why, **(extra or {})}
    s["suggestions"].append(e)
    _save(s)
    return e


def dismiss_suggestion(sid: str) -> None:
    s = _state()
    s["suggestions"] = [x for x in s["suggestions"] if x.get("id") != sid]
    _save(s)


# ------------------------------------------------------------------ moving one tunnel (a suggestion applied, a resource move, a return, the Move button)
def _run_move(aid: int, tunnel_id: int, target_id: int, reason: str, why: str) -> None:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("the tunnel is not in the list any more")

        src_id, name = t.iran_server_id, t.name

    if reason == "return":
        _purge_leftovers(aid, target_id, name)

    p = failover._make_plan(src_id, {tunnel_id}, reason, target_id=target_id)
    p["id"] = f"{reason}-{src_id}-{int(time.time())}"

    if why:
        p["why"] = why

    if not p["target"]:
        autopilot._say(aid, "No target: " + ("; ".join(p["problems"]) or "the Iran server cannot carry this tunnel"), done=True, ok=False)
        return

    if p["pulled"]:
        autopilot._say(aid, "Also moved, so that the DNS stays consistent: " + ", ".join(p["pulled"]))

    failover._work(aid, p)


def move_now(tunnel_id: int, iran_id: int, reason: str = "manual", why: str = "", rails: bool = True) -> int:
    """Start the move of one tunnel to this Iran server (the DNS records that serve only it follow). Returns the story id. `rails`: the limits of the automatic moves (how many a day,
    the cooldown) apply to every reason but "manual"; the Return button passes rails=False, because it is the operator asking (found by the real test: it was refused for two hours)."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        if t.iran_server_id == iran_id:
            raise ValueError("choose another Iran server")

        tgt = db.get(Server, iran_id)

        if tgt is None or tgt.role != "iran" or not tgt.enabled or tgt.online is False:
            raise ValueError("the target must be an Iran server that is on")

        name = t.name

    if reason != "manual" and rails:
        block = may_move(name, _state()["moves"], int(time.time()))

        if block:
            raise ValueError(block)

    return autopilot._start(f"move {name}", _run_move, tunnel_id, iran_id, reason, why, action="move", source="manual" if reason == "manual" else "auto")


def apply_suggestion(sid: str) -> int:
    s = _state()
    e = next((x for x in s["suggestions"] if x.get("id") == sid), None)

    if e is None:
        raise ValueError("this suggestion is gone")

    if e.get("kind") == "type":
        return retype_now(int(e["tunnel_id"]), str(e.get("to_kind") or ""), str(e.get("now_kind") or ""), e.get("why", ""))

    if not e.get("to_id"):
        raise ValueError("no Iran server was found for it: choose one with “Move to another Iran server”")

    return move_now(int(e["tunnel_id"]), int(e["to_id"]), "manual", e.get("why", ""))


# ------------------------------------------------------------------ a change of the tunnel's type (a "type" suggestion applied)
def _run_retype(aid: int, tunnel_id: int, kind: str, was: str, why: str, auto: bool = False) -> None:
    """The tunnel is built again as `kind` (same name, same ports), the old type is put back when the new one does not build; then it is measured again, and what it gave is told next to what it gave before."""
    from ..ssh import SSHError
    from . import bandwidth, knowledge

    snap = autopilot._snapshot(tunnel_id)
    cur = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])
    say = lambda line, **kw: autopilot._say(aid, line, **kw)

    if not kind or not license.tunnel_kind_allowed(kind):
        raise ValueError(f"{kind or 'this type'} is not allowed here (the licence)")

    if cur == kind:
        say(f"{snap['name']} runs as {kind} already: nothing to change.", done=True, ok=True)
        return

    if was and cur != was:
        raise ValueError(f"{snap['name']} runs as {cur} now, not as {was}: the suggestion is out of date")

    before = float(((snap["record"].get("bw") or {}).get("down")) or 0)
    say(f"{snap['name']}: changing the type {cur} → {kind}. {why}")

    if not autopilot._probe_links(aid, snap, [kind]):
        say(f"The link of {kind} does not pass between {snap['iran_name']} and {snap['foreign_name']}: nothing is built, {snap['name']} is not touched.", done=True, ok=False)
        return

    if auto:
        if not autopilot._wait_quiet(aid, tunnel_id, limit=QUIET_WAIT):      # (1.69.21: it gave up after 15 minutes and never came back - the users of the owner's tunnel carry 5 - 10 Mb/s all day, "quiet" never came, and nothing was changed)
            say(f"The users are still active after {QUIET_WAIT // 60} minutes: the change goes on anyway - the tunnel is very slow, the users are cut for a minute or two.")

        mark_retyped(snap["name"], int(time.time()))                          # (counted when it really starts: a change that did not start is not "once in 24 hours")

    if not autopilot._replace(aid, tunnel_id, snap["iran_id"], snap["foreign_id"], kind, "The Doctor changed it by itself: the tunnel was very slow." if auto else "The Doctor suggested it and you asked for it.", snap=snap):
        say(f"The build of {kind} failed; {cur} was put back.", done=True, ok=False)
        return

    after, down = "", None

    try:
        bw, _ = bandwidth.measure(tunnel_id)
        down = float(bw.get("down") or 0)
        after = f" It gives {bw.get('down')} Mb/s in the Doctor's test now (before: {before:g})."
    except (ValueError, RuntimeError, SSHError, OSError) as ex:
        after = f" It could not be measured just now ({str(ex)[:80]}): the Doctor measures it in a few minutes."

    if down is not None:                                              # what it gave is kept in the Doctor's memory of the pair, so the next choice stands on today's number
        try:
            knowledge.record(snap["iran_id"], snap["foreign_id"], {"kind": kind, "ok": True, "works": True, "down_mbps": down}, "full")
        except Exception:
            log.exception("the speed of %s could not be kept", kind)

    if auto and down is not None and before and down < 0.8 * before:      # clearly WORSE than before: the old type goes back (one more cut, but the users are not left on something slower)
        say(f"{kind} gave {down:g} Mb/s, less than {cur} ({before:g}): the old type is put back.")

        with session() as db:
            new_id = db.scalar(select(Tunnel.id).where(Tunnel.name == snap["name"]))

        if new_id is not None and autopilot._replace(aid, new_id, snap["iran_id"], snap["foreign_id"], cur, "It was slower than the old type."):
            with session() as db:
                emit(db, "warn", "tunnels", f"Tunnel {snap['name']}: the Doctor tried {kind} ({down:g} Mb/s) and went back to {cur} ({before:g} Mb/s).", snap["name"])

            say(f"{snap['name']} runs as {cur} again.", done=True, ok=True)
            return

    who = "by the Doctor itself (the tunnel was very slow)" if auto else "by your click on the Doctor's suggestion"

    with session() as db:
        emit(db, "ok", "tunnels", f"Tunnel {snap['name']} is now {kind} (it was {cur}): changed {who}.{after}", snap["name"])

    s = _state()
    s["suggestions"] = [x for x in s["suggestions"] if x.get("tunnel") != snap["name"]]
    _save(s)
    say(f"{snap['name']} runs as {kind} now.{after}", done=True, ok=True)


def retype_now(tunnel_id: int, kind: str, was: str = "", why: str = "") -> int:
    """Start the change of a tunnel's type as an action (the story is followed by its id). `was`: the type the suggestion was written for - if the tunnel runs another type by now, nothing is changed."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        name = t.name

    return autopilot._start(name, _run_retype, tunnel_id, kind, was, why, source="manual")


def return_now(move_id: str) -> int:
    """The Return button of the ledger: the tunnels of this move go back to the server they left (now, whatever the clock says)."""
    s = _state()
    e = next((x for x in s["moves"] if x.get("id") == move_id), None)

    if e is None or e.get("returned"):
        raise ValueError("nothing to return for this entry")

    names = list(e.get("tunnels") or [])

    with session() as db:
        rows = [(t.id, t.name) for t in db.scalars(select(Tunnel).where(Tunnel.name.in_(names), Tunnel.iran_server_id == e.get("to_id")))]

    if not rows:
        raise ValueError("these tunnels are no longer on the server they were moved to")

    tid = rows[0][0]
    return move_now(tid, int(e["from_id"]), "return", f"back to {e['from']} as asked", rails=False)


def _purge_leftovers(aid: int, server_id: int, name: str) -> None:
    """The files of a tunnel that was moved away while its Iran server was off are still on that server: removed before the tunnel is built there again."""
    from ..ssh import Node, SSHError, run_action

    s = _state()
    changed = False

    for e in s["moves"]:
        for lo in list(e.get("leftover") or []):
            if lo.get("server_id") != server_id or lo.get("name") != name:
                continue

            with session() as db:
                srv = db.get(Server, server_id)

                if srv is None:
                    continue

                node = Node(srv.host, srv.ssh_user, srv.ssh_port)

            try:
                if lo.get("other"):
                    run_action(node, "other_remove", {"NAME": lo["slug"], "KIND": lo.get("type", ""), "PORT": str(lo.get("port", 0)), "PROTO": lo.get("proto", "tcp")}, timeout=90)
                else:
                    run_action(node, "remove_tunnel_files", {"NAME": lo["slug"], "PORT": str(lo.get("port", 0)), "TRANSPORT": lo.get("transport", "tcpmux")}, timeout=90)

                autopilot._say(aid, f"{srv.name}: the leftover units of {name} were removed.")
            except (SSHError, ValueError) as ex:
                autopilot._say(aid, f"{srv.name}: the leftover units of {name} could not be removed ({str(ex)[:100]}); the build will say if a port is held.")

            e["leftover"] = [x for x in e["leftover"] if x is not lo]
            changed = True

    if changed:
        _save(s)


# ------------------------------------------------------------------ the job
def _track_back(s: dict, now: int) -> bool:
    """Who is on since when: the clock of a server that came back starts when it is first seen on, and is forgotten when it goes off."""
    with session() as db:
        on = {str(x.id) for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)))}

    changed = False

    for sid in list(s["back"]):
        if sid not in on:
            s["back"].pop(sid)
            changed = True

    for sid in on:
        if sid not in s["back"]:
            s["back"][sid] = now
            changed = True

    return changed


def saturated_servers(now: int | None = None) -> list[dict]:
    now = now or int(time.time())
    out = []

    with session() as db:
        for srv in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True))):
            rows = [(float(x.cpu or 0), float(x.ram or 0)) for x in db.scalars(select(ServerSample).where(ServerSample.server_id == srv.id, ServerSample.ts >= now - SATURATED_FOR))]
            why = is_saturated(rows)

            if why:
                out.append({"id": srv.id, "name": srv.name, "why": why})

    return out


def _tunnels_on(server_id: int) -> list[dict]:
    with session() as db:
        return [{"id": t.id, "name": t.name, "state": t.state, "rx": t.rx_mbps, "tx": t.tx_mbps, "foreign_id": t.foreign_server_id, "ports": (t.record or {}).get("port_mapping") or ",".join(str(p) for p in (t.forwarded_ports or []))}
                for t in db.scalars(select(Tunnel).where(Tunnel.iran_server_id == server_id, Tunnel.enabled.is_(True)))]


def _mark_res(server_id: int, now: int) -> None:
    s = _state()                      # read again: suggest() or the story of the move may have written in between
    s["res_at"][str(server_id)] = now
    _save(s)


def resources() -> None:
    """Every 5 minutes: an exhausted Iran server gives its busiest tunnel to a server with room (mode move), or the move is suggested (mode fix)."""
    m = mode()

    if m == "off" or get_settings().read_only:
        return

    now = int(time.time())
    sat = saturated_servers(now)

    if not sat:
        return

    s = _state()
    sat_ids = {x["id"] for x in sat}

    for srv in sat:
        if now - int(s["res_at"].get(str(srv["id"]), 0)) < RESOURCE_GAP:
            continue

        busiest = pick_busiest(_tunnels_on(srv["id"]))

        if busiest is None:
            continue

        block = may_move(busiest["name"], s["moves"], now)

        if block:
            log.info("resource move of %s skipped: %s", busiest["name"], block)
            continue

        try:
            ranking = detect.auto_iran(busiest["foreign_id"], busiest["ports"])
        except (ValueError, RuntimeError) as e:
            log.warning("resource move: no ranking for %s: %s", busiest["name"], e)
            continue

        target = next((r for r in ranking if r.get("score", 0) > 0 and r["id"] != srv["id"] and r["id"] not in sat_ids and r.get("resources", 0) >= TARGET_MIN_RESOURCES and r.get("quality", 0) >= TARGET_MIN_QUALITY), None)
        why = f"{srv['name']}: {srv['why']}; {busiest['name']} is its busiest tunnel"

        if target is None:
            suggest("resources", busiest["id"], busiest["name"], srv["id"], srv["name"], None, "", why + " - no other Iran server has room and a good route right now")
            _mark_res(srv["id"], now)
            continue

        if m == "fix" or not can_move():
            suggest("resources", busiest["id"], busiest["name"], srv["id"], srv["name"], target["id"], target["name"], why + f"; {target['name']} has room ({target.get('reason', '')})")
            _mark_res(srv["id"], now)
            continue

        try:
            move_now(busiest["id"], target["id"], "resources", why + f" → {target['name']} ({target.get('reason', '')})")
            _mark_res(srv["id"], now)

            with session() as db:
                emit(db, "warn", "actions", f"Doctor: {srv['name']} is exhausted ({srv['why']}); {busiest['name']} moves to {target['name']}.", srv["name"])

            return                                          # one move at a time
        except ValueError as e:
            log.info("resource move of %s refused: %s", busiest["name"], e)


def returns() -> None:
    """Every 5 minutes: the tunnels that left a server because it was off go back once it has been healthy for BACK_AFTER."""
    if not can_move():
        return

    now = int(time.time())
    s = _state()
    levels = {x["name"]: x.get("level", "unknown") for x in netwatch.report().get("iran", [])}

    for e in s["moves"]:
        with session() as db:
            origin = db.get(Server, int(e.get("from_id") or 0))

        if origin is None or not origin.enabled or origin.online is not True:
            continue

        block = ready_to_return(e, s["back"].get(str(origin.id)), now, levels.get(origin.name, "unknown"))

        if block:
            continue

        with session() as db:
            rows = [(t.id, t.name) for t in db.scalars(select(Tunnel).where(Tunnel.name.in_(e.get("tunnels") or []), Tunnel.iran_server_id == e.get("to_id")))]

        if not rows:
            e["returned"], e["returned_at"] = True, now        # nothing of it is where it was put: nothing to bring back
            _save(s)
            continue

        tid, name = rows[0]
        block = may_move(name, s["moves"], now)

        if block:
            continue

        try:
            move_now(tid, origin.id, "return", f"{origin.name} has been back and healthy for {(now - int(s['back'][str(origin.id)])) // 3600} hours")
        except ValueError as ex:
            log.info("return of %s refused: %s", name, ex)

        return                                              # one at a time


def run() -> None:
    """The job (every minute): the suggestions whose problem is over are withdrawn, the clock of the servers that came back, the dead servers (mode move), and - every five minutes - the exhausted servers and the returns."""
    try:
        refresh_suggestions()
    except Exception:       # (a job that must go on: the rest below does not depend on it)
        log.exception("doctor: suggestions")

    try:
        from . import capacity

        capacity.check()                  # (the capacity that is filling is told in every mode; the Doctor acts on it in fix and move)
    except Exception:
        log.exception("doctor: capacity")

    if mode() == "off":
        return

    s = _state()

    if _track_back(s, int(time.time())):
        _save(s)

    if can_move():
        failover.check()

    if time.time() - _SLOW[0] < 290:
        return

    _SLOW[0] = time.time()

    try:
        resources()
    except Exception:
        log.exception("doctor: resources")

    try:
        returns()
    except Exception:
        log.exception("doctor: returns")


def plan_preview(server_id: int) -> dict:
    """What a failover of this Iran server would do right now (nothing is changed)."""
    return failover.plan_for(server_id)
