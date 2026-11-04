"""The Doctor sweeps the tunnels that are there twice (1.69.212).
(The owner, 2026-11-04, with a picture of two tunnels that serve the same ports of the same foreign server from two Iran servers: "duplicate or extra tunnels must be looked at and must not stay for ever - a temporary or a test one is
fine, an extra one is not".)

  what is a duplicate   two or more enabled tunnels - not a test tunnel, not a hidden helper (a warm standby, a strand of a Trident) - that go to the SAME foreign server with the SAME forwarded ports. A move leaves one behind when its
                        old tunnel is still used at the end (the old one is kept, never cut: "remove it by hand when it is quiet"), a rebuild or a repair can make a second one, and nothing ever came back for it.
  what is extra         the duplicate that nobody uses: its users' traffic (the checks of the tunnel, one a minute) averaged under QUIET_MBPS over the last QUIET_FOR seconds, it is older than that, and no DNS record that points at its Iran
                        server is tied to it. The OTHER one - the busiest of the group, and it must be up - is the one that stays. When nothing proves which of two idle ones is used (no DNS account to ask, or both idle) nothing is removed:
                        it is told, once a day.
  what is done          in the modes fix and move, one extra every JANITOR_GAP, at most three a day, never in a storm: the tunnel goes to "Recently removed" (builder.delete_tunnel(trash=True): it can be restored for 30 days) and an event says
                        which tunnel it duplicated. Every look is written to the log; nothing is changed on a server before the check is made again.

The pure helpers (`duplicate_groups`, `pick_extras`, `rails`) carry the decisions and are tested."""
from __future__ import annotations

import time

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel, TunnelCheck, is_standby, is_temp
from . import settings as st
from .events import emit

log = get_logger("janitor")
EVERY = 600
QUIET_MBPS = 1.5            # what the users move through a tunnel (download + upload, average) under which nobody uses it ...
QUIET_FOR = 3 * 3600        # ... over this long (and the tunnel must be older than this)
MIN_COVER = 0.7             # the checks must cover this share of that time (a tunnel that was not measured is not quiet, it is unknown)
JANITOR_GAP = 1800          # between two removals
MAX_PER_DAY = 3
TELL_EVERY = 24 * 3600
POINT_GAP = 6 * 3600        # (1.69.219) the names of a copy are moved to the keeper at most once in this time
SETTLE = 20 * 60            # ... and the copy goes this long after, when it is quiet in the last minutes (not over the three hours of the window, which still hold the traffic of before)
SETTLED_MBPS = 0.3
_TOLD: dict[str, float] = {}


# ------------------------------------------------------------------ pure helpers (tested)
def duplicate_groups(rows: list[dict]) -> list[list[dict]]:
    """Pure: the groups of two or more tunnels that go to the same foreign server with the same forwarded ports. `rows`: {id, name, foreign_id, ports, ...} of the tunnels that may be judged (no test tunnel, no helper)."""
    groups: dict[tuple, list[dict]] = {}

    for r in rows:
        ports = tuple(sorted(int(p) for p in (r.get("ports") or [])))

        if ports:
            groups.setdefault((r["foreign_id"], ports), []).append(r)

    return [g for g in groups.values() if len(g) > 1]


def pick_extras(group: list[dict], use: dict[int, float | None], age: dict[int, int], referenced: dict[int, bool | None]) -> tuple[dict | None, list[dict], str]:
    """Pure: (the tunnel that stays, the extras that may go, why nothing goes when none does). `use`: the average traffic (Mb/s, download + upload) of each over the window, None = not measured enough; `age`: seconds since it was
    built; `referenced`: a DNS record that points at its Iran server is tied to it (True), is not (False), could not be asked (None). The keeper is the busiest that is up; an extra is quiet, old enough and proved unreferenced."""
    up = [t for t in group if t.get("state") == "up" and use.get(t["id"]) is not None]

    if not up:
        return None, [], "no tunnel of the group is up and measured"

    keeper = max(up, key=lambda t: (float(use[t["id"]]), -int(age.get(t["id"], 0))))
    extras, why = [], ""

    for t in group:
        if t["id"] == keeper["id"]:
            continue

        u = use.get(t["id"])

        if u is None:
            why = why or f"{t['name']} was not measured enough to say it is quiet"
        elif float(u) >= QUIET_MBPS:
            why = why or f"{t['name']} carries {float(u):.1f} Mb/s: it is used"
        elif int(age.get(t["id"], 0)) < QUIET_FOR:
            why = why or f"{t['name']} is younger than {QUIET_FOR // 3600} hours"
        elif referenced.get(t["id"]) is not False:
            why = why or (f"a DNS record is tied to {t['name']}" if referenced.get(t["id"]) else f"the DNS records could not be asked for {t['name']}")
        elif float(use[keeper["id"]]) < QUIET_MBPS and referenced.get(keeper["id"]) is not True:
            why = why or f"both {keeper['name']} and {t['name']} are idle and nothing proves which one is used"
        else:
            extras.append(t)

    return keeper, extras, why


def rails(done: list, now: int) -> str:
    """Pure: '' when one more removal may be made now, else why not (one every JANITOR_GAP seconds, MAX_PER_DAY a day)."""
    recent = [int(x) for x in done if now - int(x) < 86400]

    if len(recent) >= MAX_PER_DAY:
        return f"{MAX_PER_DAY} extra tunnels were removed in the last 24 hours"

    if recent and now - max(recent) < JANITOR_GAP:
        return f"the last removal was {(now - max(recent)) // 60} minutes ago"

    return ""


def consolidation_target(group: list[dict], keeper: dict | None, age: dict[int, int], referenced: dict[int, bool | None], pointed: dict, now: int) -> dict | None:
    """Pure: the copy whose DNS names are to be moved to the keeper (1.69.219) - the keeper is up, the copy is not the keeper, is older than QUIET_FOR, DNS names are tied to it (`referenced` True: the case that kept it,
    the owner, 2026-11-04: the Doctor must remove the duplicates by itself) and its names were not moved in the last POINT_GAP seconds. None when there is no such copy."""
    if keeper is None or keeper.get("state") != "up":
        return None

    for t in group:
        if t["id"] != keeper["id"] and referenced.get(t["id"]) is True and age.get(t["id"], 0) >= QUIET_FOR and now - int(pointed.get(str(t["id"]), 0)) >= POINT_GAP:
            return t

    return None


def pick_after_point(group: list[dict], keeper: dict | None, pointed: dict, recent: dict[int, float | None], referenced: dict[int, bool | None], now: int) -> list[dict]:
    """Pure: the copies whose names were moved to the keeper at least SETTLE seconds ago, that no DNS record is tied to any more and that carry under SETTLED_MBPS in the last minutes: they are extra now."""
    if keeper is None:
        return []

    out = []

    for t in group:
        at, r = int(pointed.get(str(t["id"]), 0)), recent.get(t["id"])

        if t["id"] != keeper["id"] and at and now - at >= SETTLE and r is not None and float(r) < SETTLED_MBPS and referenced.get(t["id"]) is False:
            out.append(t)

    return out


# ------------------------------------------------------------------ the look
def _rows() -> list[dict]:
    rows = []

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            if is_temp(t.name) or is_standby(t) or not t.forwarded_ports:
                continue

            rows.append({"id": t.id, "name": t.name, "foreign_id": t.foreign_server_id, "iran_id": t.iran_server_id, "state": t.state, "ports": [int(p) for p in t.forwarded_ports if str(p).isdigit()],
                         "created_at": int(t.created_at or 0)})

    return rows


def _use(tunnel_id: int, now: int) -> float | None:
    """The average traffic of the tunnel over the window from its own checks; None when they cover too little of it."""
    with session() as db:
        rows = db.execute(select(TunnelCheck.rx_mbps, TunnelCheck.tx_mbps).where(TunnelCheck.tunnel_id == tunnel_id, TunnelCheck.ts >= now - QUIET_FOR)).all()

    vals = [float(rx or 0) + float(tx or 0) for rx, tx in rows if rx is not None or tx is not None]
    expected = QUIET_FOR / max(1, int(get_settings().tunnel_check_interval))
    return sum(vals) / len(vals) if len(vals) >= MIN_COVER * expected else None


def _use_recent(tunnel_id: int, now: int, window: int = 900) -> float | None:
    """The average traffic of the tunnel over the last minutes (the checks must cover half of them); None when they cover too little."""
    with session() as db:
        rows = db.execute(select(TunnelCheck.rx_mbps, TunnelCheck.tx_mbps).where(TunnelCheck.tunnel_id == tunnel_id, TunnelCheck.ts >= now - window)).all()

    vals = [float(rx or 0) + float(tx or 0) for rx, tx in rows if rx is not None or tx is not None]
    expected = window / max(1, int(get_settings().tunnel_check_interval))
    return sum(vals) / len(vals) if len(vals) >= 0.5 * expected else None


def _referenced(row: dict) -> bool | None:
    """Is a DNS record that points at the Iran server of this tunnel tied to it (the configs and the panel hosts that dial the record's name on its ports)? None when the records could not be asked."""
    from . import dnsprov, failover

    if not dnsprov.accounts():
        return None

    try:
        with session() as db:
            from ..models import Server

            host = db.get(Server, row["iran_id"]).host

        recs, err = failover._records(host, failover._tunnels_of(row["iran_id"]))
    except Exception:
        log.exception("the records of tunnel %s could not be asked", row["name"])
        return None

    if err:
        return None

    return any(row["id"] in (r.get("tunnels") or []) for r in recs)


def _tell_once(key: str, text: str, level: str = "info") -> None:
    if time.time() - _TOLD.get(key, 0) < TELL_EVERY:
        return

    _TOLD[key] = time.time()

    with session() as db:
        emit(db, level, "tunnels", text, "")


def run() -> None:
    """Job (every EVERY seconds): find the duplicates, say which is extra, and - in the modes fix and move - remove one extra when every check agrees."""
    from . import actions, autopilot, bandwidth

    if get_settings().read_only or jobs.stopping() or actions.doctor_mode() == "off":
        return

    now = int(time.time())

    for group in duplicate_groups(_rows()):
        names = " + ".join(sorted(t["name"] for t in group))
        use = {t["id"]: _use(t["id"], now) for t in group}
        age = {t["id"]: now - int(t["created_at"]) for t in group}
        referenced = {t["id"]: _referenced(t) for t in group}
        keeper, extras, why = pick_extras(group, use, age, referenced)
        pointed = dict(st.get("janitor").get("pointed") or {})

        if not extras:          # (1.69.219: a copy whose DNS names moved to the keeper a while ago and that is quiet now goes, though the three hours of its window still hold the traffic of before)
            extras = pick_after_point(group, keeper, pointed, {t["id"]: _use_recent(t["id"], now) for t in group}, referenced, now)

        if not extras and _consolidate(group, keeper, age, referenced, pointed, now):
            return

        if not extras:
            _tell_once("dup:" + names, f"Two tunnels serve the same ports of the same foreign server: {names}. Nothing is removed: {why or 'both are used'}.")
            continue

        extra = extras[0]
        blocked = rails(st.get("janitor").get("done") or [], now) if bandwidth.actions_mode_allows_fix() else "the Doctor's mode is not fix or move"

        if not blocked and (autopilot.storm_now() or extra["name"] in autopilot._BUSY or keeper["name"] in autopilot._BUSY):
            blocked = "a repair is running or the system is in a storm"

        if blocked:
            _tell_once("extra:" + extra["name"], f"Tunnel {extra['name']} is an extra copy of {keeper['name']} (the same ports, quiet for {QUIET_FOR // 3600} hours, no DNS record is tied to it). Not removed now: {blocked}.")
            continue

        done = [x for x in (st.get("janitor").get("done") or []) if now - int(x) < 86400] + [now]
        st.put("janitor", {"done": done})

        try:
            autopilot._start(extra["name"], remove_extra, extra["id"], keeper["name"], float(use[extra["id"]] or 0.0), action="autopilot", source="auto")
        except ValueError as e:
            log.info("the removal of the extra tunnel %s not started: %s", extra["name"], str(e)[:100])

        return


def remove_extra(aid: int, tunnel_id: int, keeper: str, mbps: float) -> None:
    """The story of the removal: check again, take the tunnel off both servers into 'Recently removed'."""
    from . import autopilot, builder

    say = lambda line, **k: autopilot._say(aid, line, **k)       # (one short line for the story)
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            say("The tunnel is gone already.", done=True, ok=True)
            return

        name = t.name

    say(f"{name} is an extra copy of {keeper}: the same ports of the same foreign server, {mbps:.2f} Mb/s over the last {QUIET_FOR // 3600} hours, no DNS record is tied to it.")
    again = _use(tunnel_id, now)

    if again is None or again >= QUIET_MBPS:
        say(f"Looked again just now: {'not measured enough' if again is None else f'{again:.1f} Mb/s'} - it stays.", done=True, ok=False)
        return

    try:
        res = builder.delete_tunnel(tunnel_id, trash=True)
    except (ValueError, RuntimeError) as e:
        say(f"Not removed: {str(e)[:160]}", done=True, ok=False)
        return

    with session() as db:
        emit(db, "warn", "tunnels", f"Tunnel {name} was an extra copy of {keeper} (the same ports, quiet, no DNS record tied to it) and was removed; it can be restored from Recently removed.", name)

    helpers = _after_removal(name)
    say("Removed (kept in Recently removed for 30 days): " + "; ".join(res) + (f"; its standby / strands went with it: {', '.join(helpers)}" if helpers else ""), done=True, ok=True)


def _after_removal(name: str) -> list[str]:
    """What the removal of a tunnel by the page also does and the sweep did not (1.69.219: the standby of the removed copy stayed behind, up, with its units on both servers): the note of its warm standby is forgotten, an old
    rebuild note is dropped, and the standby and the strands that belonged to it are removed with it. Returns their names."""
    from . import builder, orphans, standby

    try:
        standby.forget(name)
        builder.pending_drop(name)
        return orphans.drop_helpers_of(name)
    except Exception:
        log.exception("the standby of %s could not be cleaned up", name)
        return []


def _consolidate(group: list[dict], keeper: dict | None, age: dict[int, int], referenced: dict[int, bool | None], pointed: dict, now: int) -> bool:
    """(1.69.219) A copy that cannot go because DNS names are tied to it: when those names serve ONLY that copy (`failover.replace_plan` - a name that also serves another tunnel of its Iran server is left alone) they are
    pointed at the Iran server of the keeper (the records stay, every change is in the Changes box with Restore), and once the copy has been quiet a while it goes like any extra copy. In the mode move only (it changes DNS).
    Returns True when it started the move."""
    from . import actions, autopilot, failover

    cand = consolidation_target(group, keeper, age, referenced, pointed, now)

    if cand is None:
        return False

    key = "point:" + cand["name"]

    if actions.doctor_mode() != "move":
        _tell_once(key, f"Tunnel {cand['name']} is a second copy of {keeper['name']} that DNS names are tied to. In the mode move the Doctor points those names at {keeper['name']} and then removes it.")
        return False

    done = st.get("janitor").get("done") or []
    blocked = rails(done, now) or ("a repair is running or the system is in a storm" if autopilot.storm_now() or cand["name"] in autopilot._BUSY or keeper["name"] in autopilot._BUSY else "")

    if blocked:
        _tell_once(key + ":b", f"Tunnel {cand['name']} is a second copy of {keeper['name']}. Its names are not moved now: {blocked}.")
        return False

    try:
        plan = failover.replace_plan(cand["id"], keeper["iran_id"])
    except (ValueError, RuntimeError) as e:
        log.info("the names of %s could not be planned: %s", cand["name"], str(e)[:100])
        return False

    if plan.get("dns_error") or plan.get("left_alone") or not plan.get("dns"):
        why = plan.get("dns_error") or ("a name also serves another tunnel of that Iran server: " + ", ".join(d["fqdn"] for d in plan["left_alone"]) if plan.get("left_alone") else "no name serves only this copy")
        _tell_once(key + ":s", f"Tunnel {cand['name']} is a second copy of {keeper['name']}. Its names are not moved: {why}.")
        return False

    names = [d["fqdn"] for d in plan["dns"]]
    st.put("janitor", {"done": [x for x in done if now - int(x) < 86400] + [now], "pointed": {**pointed, str(cand["id"]): now}})

    try:
        autopilot._start(cand["name"], consolidate_names, keeper["iran_id"], names, keeper["name"], action="autopilot", source="auto")
    except ValueError as e:
        log.info("the move of the names of %s not started: %s", cand["name"], str(e)[:100])
        return False

    return True


def consolidate_names(aid: int, iran_id: int, names: list[str], keeper: str) -> None:
    """The story of the move: the names that served only the second copy are pointed at the Iran server of the keeper. No record is deleted; every change is in the Changes box with Restore."""
    from . import autopilot, failover

    autopilot._say(aid, f"The names {', '.join(names)} serve only the second copy: they are pointed at the Iran server of {keeper}. The records stay.")
    failover.point_names(aid, names, iran_id)

    with session() as db:
        emit(db, "warn", "tunnels", f"DNS names {', '.join(names)} now point at the Iran server of {keeper}: they were tied to a second copy of it, which is removed once it is quiet. No record was deleted (Restore is in the Changes box).", keeper)

    autopilot._say(aid, "Done.", done=True, ok=True)
