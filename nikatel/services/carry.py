"""The Doctor watches that every port of every tunnel really CARRIES a connection (1.69.209).
(The owner, 2026-11-04, after the UAE and US configs were down for an hour behind tunnels that were "up": "the Doctor must solve the problems of the connections by itself and not wait for me - it must keep an eye on all the configs".)

  what is looked at   every EVERY seconds, for each Iran server: the forwarded ports of its tunnels that are up are asked on the server itself (`ports_carry`: a connection to 127.0.0.1:<port> must stay open waiting for the
                      client's first bytes, as a proxy does; one that is closed at once, or not accepted, found nothing at its far end). A tunnel that is up, answers its ping and passes its own checks can still refuse every
                      connection - a BackPack Direct tunnel whose port lines dialled the Iran server itself did exactly that.
  when it is a fault  only when the SERVICE answers: the port is asked again on the foreign server where the tunnel delivers to (127.0.0.1:<target>). A service that closes a bare connection by itself is not the tunnel's fault.
  when it is sure     twice in a row, at least CONFIRM_FOR seconds apart. Then an event is told (once) and, in the modes fix and move, the Doctor repairs it by itself: the main units are restarted, the tunnel is rebuilt
                      (same type, same ports, its files written again), and in the mode move another Iran server is tried (the move asks the new tunnel first, `failover._not_carrying`). After each step the ports are asked again.
  where it is kept    the record of the tunnel, `carry` (the ports, the looks in a row, when it began, whether it is confirmed and told, how many repairs and when the last began).

The pure helpers (`mapping`, `parse`, `verdict`, `step`, `heal_due`) carry the decisions and are tested."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel, is_temp
from ..ssh import Node, SSHError, run_action
from . import settings as st
from .events import emit

log = get_logger("carry")
EVERY = 180                  # seconds between two looks (one light command per Iran server, about two seconds)
CONFIRM_AFTER = 2            # looks in a row ...
CONFIRM_FOR = 100            # ... over at least this many seconds, before a port is called "carries nothing" (a service that restarts is back in seconds)
HEAL_GAP = 600               # a repair of the same tunnel starts at most this often ...
HEAL_SLOW_GAP = 1800         # ... and after HEAL_FAST_RUNS repairs that did not help, this often
HEAL_FAST_RUNS = 3
UP_FOR = 120                 # a tunnel that has been up for less than this is not asked yet (its services are still starting)
RECHECK_WAIT = 20            # seconds after a repair step before the ports are asked again
HEAL = False                 # the repair by itself is OFF (2026-11-04: in its first hours the watch restarted, then rebuilt, ten healthy tunnels at once - see CHANGELOG 1.69.211): it only looks and tells until its verdict is proved
MASS_FLAGGED = 3             # this many tunnels flagged in ONE look are the test or the network, never the tunnels: nothing is kept, told or repaired
MASS_TELL_EVERY = 1800       # ... and that is told at most this often
_MASS_TOLD = [0.0]


# ------------------------------------------------------------------ pure helpers (tested)
def mapping(ports: list, record: dict | None) -> list[tuple[int, int]]:
    """Pure: (the port the Iran server listens on, the port it is delivered to on the foreign server) for every forwarded port of a tunnel. `record.port_mapping` ("11426:8680,1826:1820,8072") says where a port goes;
    a port without a number after the colon goes to the same one."""
    pm: dict[int, int] = {}

    for part in str((record or {}).get("port_mapping") or "").split(","):
        a, _, b = part.strip().partition(":")

        if a.isdigit() and b.isdigit():
            pm[int(a)] = int(b)

    out: list[tuple[int, int]] = []

    for p in ports or []:
        try:
            listen = int(p)
        except (TypeError, ValueError):
            continue

        out.append((listen, pm.get(listen, listen)))

    return out


def parse(out: str) -> dict[int, str]:
    """Pure: the answer of `ports_carry` -> {port: "CARRIES" | "CLOSED" | "NOCONNECT"}."""
    res: dict[int, str] = {}

    for line in str(out).splitlines():
        parts = line.split()

        if len(parts) == 2 and parts[0] in ("CARRIES", "CLOSED", "NOCONNECT") and parts[1].isdigit():
            res[int(parts[1])] = parts[0]

    return res


def verdict(maps: list[tuple[int, int]], iran: dict[int, str], foreign: dict[int, str]) -> tuple[list[int], list[int]]:
    """Pure: (the ports that do not carry although the service behind them answers - the tunnel's fault, the ports whose service does not answer either - not the tunnel's fault). `iran`: the answer on the Iran server for
    the listen ports, `foreign`: the answer on the foreign server for the targets. A port that was not asked, or whose target was not asked, is neither."""
    bad: list[int] = []
    service: list[int] = []

    for listen, target in maps:
        if iran.get(listen) in (None, "CARRIES"):
            continue

        if foreign.get(target) == "CARRIES":
            bad.append(listen)
        elif target in foreign:
            service.append(listen)

    return bad, service


def step(prev: dict, bad: list[int], now: int) -> dict:
    """Pure: the state kept in the record after one look. {} when nothing is wrong; else the ports, the looks in a row, when it began, `confirmed` (CONFIRM_AFTER looks over CONFIRM_FOR seconds) and what the repairs
    left (`told`, `runs`, `last_run`)."""
    if not bad:
        return {}

    fails = int(prev.get("fails", 0)) + 1
    first = int(prev.get("first") or now)
    out = {k: prev[k] for k in ("told", "runs", "last_run") if k in prev}
    out.update({"ports": sorted(set(bad)), "fails": fails, "first": first, "confirmed": fails >= CONFIRM_AFTER and now - first >= CONFIRM_FOR})
    return out


def heal_due(state: dict, now: int) -> bool:
    """Pure: may a repair of this tunnel start now - the fault is confirmed and the last repair began at least HEAL_GAP ago (HEAL_SLOW_GAP once HEAL_FAST_RUNS repairs did not help)."""
    if not state.get("confirmed"):
        return False

    gap = HEAL_GAP if int(state.get("runs") or 0) < HEAL_FAST_RUNS else HEAL_SLOW_GAP
    return now - int(state.get("last_run") or 0) >= gap


# ------------------------------------------------------------------ the look
def _row(t: Tunnel) -> dict | None:
    iran, foreign = t.iran, t.foreign

    if iran is None or foreign is None:
        return None

    return {"id": t.id, "name": t.name, "iran": (iran.id, iran.host, iran.ssh_user, iran.ssh_port), "foreign": (foreign.id, foreign.host, foreign.ssh_user, foreign.ssh_port),
            "maps": mapping(t.forwarded_ports, t.record), "carry": dict((t.record or {}).get("carry") or {})}


def _targets(now: int) -> list[dict]:
    """The tunnels that are asked: enabled, up for a while, with ports, both servers on."""
    rows: list[dict] = []

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            if t.state != "up" or is_temp(t.name) or not t.forwarded_ports or now - int(t.state_since or 0) < UP_FOR:
                continue

            if t.iran is None or t.foreign is None or t.iran.online is False or t.foreign.online is False:
                continue

            if (r := _row(t)) and r["maps"]:
                rows.append(r)

    return rows


def _look(server: tuple, ports: list[int]) -> dict[int, str] | None:
    """One look at ports of a server (read-only): {port: answer}; None when the server did not answer (that is not a fault of the tunnel)."""
    if not ports:
        return {}

    try:
        out = run_action(Node(server[1], server[2], server[3]), "ports_carry", {"CHECK": " ".join(str(p) for p in sorted(set(ports))[:200])}, timeout=45)
    except (SSHError, ValueError, RuntimeError, OSError) as e:
        log.info("the ports of %s could not be asked: %s", server[1], str(e)[:120])
        return None

    return parse(out)


def _assess(r: dict, iran_ans: dict[int, str] | None = None) -> tuple[list[int], list[int]] | None:
    """(bad ports, ports whose service does not answer either) of one tunnel; None when the Iran server did not answer."""
    if iran_ans is None:
        iran_ans = _look(r["iran"], [listen for listen, _ in r["maps"]])

    if iran_ans is None:
        return None

    suspects = [target for listen, target in r["maps"] if iran_ans.get(listen) in ("CLOSED", "NOCONNECT")]
    foreign_ans = _look(r["foreign"], suspects) if suspects else {}
    return verdict(r["maps"], iran_ans, foreign_ans or {}) if foreign_ans is not None else ([], [])


def _save(tunnel_id: int, state: dict) -> None:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            return

        rec = dict(t.record or {})

        if (rec.get("carry") or {}) == state:
            return

        if state:
            rec["carry"] = state
        else:
            rec.pop("carry", None)

        t.record = rec


def _tell(level: str, name: str, text: str) -> None:
    with session() as db:
        emit(db, level, "tunnels", text, name)


def run() -> None:
    """Job (every EVERY seconds): look at the ports of every tunnel that is up, keep the state, tell a confirmed fault once and - in the modes fix and move - start the repair."""
    from . import actions

    if get_settings().read_only or jobs.stopping() or actions.doctor_mode() == "off":
        return

    now = int(time.time())
    rows = _targets(now)
    by_iran: dict[int, list[dict]] = {}

    for r in rows:
        by_iran.setdefault(r["iran"][0], []).append(r)

    def look_server(group: list[dict]) -> dict[int, str] | None:
        return _look(group[0]["iran"], [listen for r in group for listen, _ in r["maps"]])

    with ThreadPoolExecutor(4) as ex:
        answers = dict(zip(by_iran, ex.map(look_server, by_iran.values()), strict=True))

    judged: list[tuple[dict, list[int]]] = []

    for iran_id, group in by_iran.items():
        if answers[iran_id] is None:
            continue

        for r in group:
            try:
                res = _assess(r, answers[iran_id])
            except Exception:
                log.exception("the ports of tunnel %s could not be judged", r["name"])
                continue

            if res is not None:
                judged.append((r, res[0]))

    flagged = [r["name"] for r, bad in judged if bad]

    if len(flagged) >= MASS_FLAGGED:
        _mass(flagged)
        judged = [(r, []) for r, _bad in judged]               # (every state goes: nothing is kept, told or repaired)

    for r, bad in judged:
        _apply(r, bad, now)


def _mass(names: list[str]) -> None:
    """Many tunnels flagged in one look: the test or the network, never the tunnels (2026-11-04: ten at once were restarted and rebuilt). One event, at most every MASS_TELL_EVERY seconds."""
    log.info("carry: %s tunnels flagged in one look (%s): not told, not repaired", len(names), ", ".join(names[:8]))

    if time.time() - _MASS_TOLD[0] < MASS_TELL_EVERY:
        return

    _MASS_TOLD[0] = time.time()
    _tell("info", "", f"{len(names)} tunnels were flagged in one look ({', '.join(names[:6])}{'...' if len(names) > 6 else ''}): that is the test or the network, not the tunnels - nothing is kept, told or repaired.")


def _apply(r: dict, bad: list[int], now: int) -> None:
    prev = r["carry"]
    state = step(prev, bad, now)
    name = r["name"]

    if state.get("confirmed") and not state.get("told"):
        state["told"] = True
        _tell("warn", name, f"Tunnel {name}: port {', '.join(str(p) for p in state['ports'])} accepts connections but carries nothing - a connection to it is closed at once, although the service behind it answers. "
                            + ("The Doctor repairs it by itself (restart, rebuild, another Iran server)." if HEAL else "Only reported: the repair by itself is off until this check is proved."))
    elif prev.get("told") and not state:
        _tell("ok", name, f"Tunnel {name}: its ports carry connections again.")

    if heal_due(state, now) and _may_heal(r):
        state["runs"] = int(state.get("runs") or 0) + 1
        state["last_run"] = now
        _save(r["id"], state)
        _start_heal(r)
        return

    _save(r["id"], state)


def _may_heal(r: dict) -> bool:
    """Only in the modes fix and move, never in a storm, while another repair of the tunnel runs, for a muted tunnel, or while a server of the pair is off."""
    from . import actions, autopilot, bandwidth
    from .tunnels import servers_off_of

    if not HEAL or not bandwidth.actions_mode_allows_fix() or autopilot.storm_now():
        return False

    if r["name"] in autopilot._BUSY or r["name"] in actions._RUNNING or r["name"] in set(st.get("alerts").get("muted") or []):
        return False

    return not servers_off_of(r["iran"][0], r["foreign"][0])


def _start_heal(r: dict) -> None:
    from . import autopilot

    try:
        autopilot._start(r["name"], heal, r["id"], action="autopilot", source="auto")
    except ValueError as e:
        log.info("the repair of the ports of tunnel %s not started: %s", r["name"], str(e)[:100])


# ------------------------------------------------------------------ the repair
def _restart(tunnel_id: int) -> str:
    """Restart the main units of the tunnel on both sides (not the GRE link under it): the foreign side first, the Iran side after. Returns one line for the story."""
    from .tunnels import plan_for

    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        plan = plan_for(t)
        sides = [(Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port), [u for u in plan.foreign_units if not u.startswith("nikatel-gre-")]),
                 (Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), [u for u in plan.iran_units if not u.startswith("nikatel-gre-")])]

    done = 0

    for node, units in sides:
        if units:
            run_action(node, "restart_units", {"UNITS": " ".join(units)}, timeout=90)
            done += len(units)

    return f"{done} unit(s) restarted" if done else "no unit to restart"


def _carries_now(tunnel_id: int) -> bool | None:
    """True when every port of the tunnel carries (asked now), False when one does not, None when the server did not answer."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        r = _row(t) if t is not None else None

    if r is None:
        return None

    res = _assess(r)
    return None if res is None else not res[0]


def heal(aid: int, tunnel_id: int) -> None:
    """The story of the repair: restart, rebuild, and - only in the mode move - another Iran server. After each step the ports are asked again; the first step after which they carry ends it."""
    from . import actions, autopilot, failover
    from .tunnels import servers_off_of

    snap = autopilot._snapshot(tunnel_id)
    name = snap["name"]
    say = lambda line: autopilot._say(aid, line)       # (one short line for the story)
    ports = ", ".join(str(p) for p in (((snap.get("record") or {}).get("carry") or {}).get("ports") or []))
    say(f"{name}: port {ports} accepts connections but carries nothing, although the service behind it answers on the foreign server. The tunnel is up, so a plain repair would find nothing: the Doctor restarts it, then rebuilds it, then tries another Iran server.")

    for kind in ("restart", "rebuild", "move"):
        if off := servers_off_of(snap["iran_id"], snap["foreign_id"]):
            say(f"Before the step “{kind}”: {'; '.join(off)}. It is looked at again when the server answers.")
            autopilot._say(aid, f"{name}: stopped.", done=True, ok=False)
            return

        if kind in ("rebuild", "move") and autopilot.storm_now():
            say("Mass disruption right now (several servers offline or many tunnels down): the tunnel is not rebuilt or moved. The repair looks again later.")
            break

        if kind == "restart":
            try:
                say("Restart: " + _restart(tunnel_id))
            except (SSHError, ValueError, RuntimeError) as e:
                say(f"Restart: failed ({str(e)[:120]}).")
        elif kind == "rebuild":
            ok, story = autopilot._rebuild_safe(aid, tunnel_id)
            say("Rebuild: " + (story[-1] if story else "no result"))

            if not ok:
                continue
        else:
            if actions.doctor_mode() != "move":
                say("Another Iran server is tried only in the Doctor's mode “move”: nothing is moved.")
                break

            if failover.quality_move(aid, tunnel_id, "quality", 0.0, None, say):
                autopilot._say(aid, f"{name}: a move to another Iran server was started - the new tunnel is asked about its ports before the DNS follows it.", done=True, ok=True)
                return

            continue

        time.sleep(RECHECK_WAIT)

        if _carries_now(tunnel_id):
            autopilot._say(aid, f"{name}: its ports carry connections again after the {kind}.", done=True, ok=True)
            return

    autopilot._say(aid, f"{name}: still carries nothing. A human should look at it.", done=True, ok=False)
