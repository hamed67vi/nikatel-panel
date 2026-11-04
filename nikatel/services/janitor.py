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

    say("Removed (kept in Recently removed for 30 days): " + "; ".join(res), done=True, ok=True)
