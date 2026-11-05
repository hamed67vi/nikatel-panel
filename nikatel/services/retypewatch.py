"""After the Doctor changes the TYPE of a tunnel it watches the users (1.69.220).
(The owner, 2026-11-05, Persian: "the Doctor must test now which tunnel type is the best and the steadiest, always examine the network - not only when there is a problem -, always replace a tunnel when a better one
exists, and check that the customers' use of the tunnel did not fall a lot afterwards: if it did, move the tunnel to a better type or put the one before back".)

A better type on paper (a pair test, a speed number) can still be worse for the users: they leave a tunnel whose type their apps dislike, or whose route is poor at the hours that count. The users' use is the honest
judge, so every change of type - the Doctor's own (`swap.beside`) or the one the owner asked for - leaves a note here, and three looks follow it (30 minutes, 2 hours, 6 hours):

  how the use is judged   the average traffic of the tunnel over the last twenty minutes, against what it carried in the three hours BEFORE the change, divided by what the rest of the fleet did over the same two
                          stretches (the evening is quieter than the afternoon for everybody: only a fall that the others did not have counts). A tunnel that carried under MIN_BEFORE_MBPS before cannot show a fall.
  what a fall does        under DROP_RATIO of the expected use: the old type is put back by the same no-cut change (`swap.beside` with `revert`: the old type is built beside, the users go over, the new one is removed),
                          the new type is entered in the Doctor's memory of the pair as one that did not work (it is not chosen again for that pair for two weeks) and an event says so. A change that is put back is not watched again.
  what holds              three looks that find the use held close the note with one event.

The pure parts (`snapshot` numbers, `relative_use`, `verdict`, `due_look`) carry the decisions and are tested."""
from __future__ import annotations

import time

from sqlalchemy import func, select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel, TunnelCheck, is_temp
from . import settings as st
from .events import emit

log = get_logger("retypewatch")
EVERY = 300
LOOKS = (30 * 60, 2 * 3600, 6 * 3600)      # when the use is looked at after the change
BEFORE_WINDOW = 3 * 3600                    # what the tunnel carried before the change is the average of this long
AFTER_WINDOW = 20 * 60                      # ... and what it carries at a look is the average of the last twenty minutes
MIN_BEFORE_MBPS = 3.0                       # a tunnel that carried less than this before cannot show a fall
DROP_RATIO = 0.55                           # the use fell to under this share of what the fleet's own movement says it should be
MIN_COVER = 0.5                             # the checks of the tunnel must cover this share of a window
KEEP_AFTER = 8 * 3600                       # a note is forgotten this long after its last look


# ------------------------------------------------------------------ pure helpers (tested)
def relative_use(before: float | None, after: float | None, fleet_before: float | None, fleet_after: float | None) -> float | None:
    """Pure: the use of the tunnel after the change as a share of what it should be - (after / before) / (fleet after / fleet before). None when it cannot be told: no reading, a tunnel that carried under
    MIN_BEFORE_MBPS before, a fleet that carried nothing."""
    if before is None or after is None or fleet_before is None or fleet_after is None:
        return None

    if before < MIN_BEFORE_MBPS or fleet_before <= 0 or fleet_after <= 0:
        return None

    return (after / before) / (fleet_after / fleet_before)


def verdict(rel: float | None) -> tuple[str, str]:
    """Pure: ("unknown" | "ok" | "drop", the sentence) from `relative_use`."""
    if rel is None:
        return "unknown", "the use before or after could not be told (too little carried, or no reading)"

    if rel < DROP_RATIO:
        return "drop", f"its users use {rel * 100:.0f} % of what they should (the rest of the fleet moved the same way): they left it"

    return "ok", f"its users use {rel * 100:.0f} % of what they should: they stayed"


def due_look(entry: dict, now: int) -> int | None:
    """Pure: the index of the look that is due for this note (0, 1 or 2), None when none is."""
    done = int(entry.get("looks") or 0)

    if entry.get("closed") or done >= len(LOOKS):
        return None

    return done if now - int(entry.get("at") or 0) >= LOOKS[done] else None


# ------------------------------------------------------------------ the numbers
def _avg(db, tunnel_id: int, start: int, end: int) -> float | None:
    """The average traffic (download + upload, Mb/s) of one tunnel from its own checks; None when they cover too little of the stretch."""
    n, total = db.execute(select(func.count(), func.avg(func.coalesce(TunnelCheck.rx_mbps, 0) + func.coalesce(TunnelCheck.tx_mbps, 0))).where(
        TunnelCheck.tunnel_id == tunnel_id, TunnelCheck.ts >= start, TunnelCheck.ts <= end, (TunnelCheck.rx_mbps.is_not(None)) | (TunnelCheck.tx_mbps.is_not(None)))).one()
    expected = (end - start) / max(1, int(get_settings().tunnel_check_interval))
    return float(total) if n and n >= MIN_COVER * expected and total is not None else None


def _fleet(db, skip_id: int, start: int, end: int) -> float | None:
    """What the rest of the tunnels carried over the stretch, together (Mb/s); None when nothing was read."""
    ids = [t.id for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name) and t.id != skip_id]

    if not ids:
        return None

    rows = db.execute(select(TunnelCheck.tunnel_id, func.avg(func.coalesce(TunnelCheck.rx_mbps, 0) + func.coalesce(TunnelCheck.tx_mbps, 0))).where(
        TunnelCheck.tunnel_id.in_(ids), TunnelCheck.ts >= start, TunnelCheck.ts <= end).group_by(TunnelCheck.tunnel_id)).all()
    return float(sum(float(a or 0) for _, a in rows)) if rows else None


def snapshot(tunnel_id: int, now: int | None = None) -> dict:
    """The use of the tunnel and of the rest of the fleet over the last BEFORE_WINDOW - taken BEFORE the change (the old tunnel and its checks are gone afterwards)."""
    now = now or int(time.time())

    try:
        with session() as db:
            return {"before": _avg(db, tunnel_id, now - BEFORE_WINDOW, now), "fleet_before": _fleet(db, tunnel_id, now - BEFORE_WINDOW, now), "at": now}
    except Exception:
        log.exception("the use of tunnel %s before its change could not be read", tunnel_id)
        return {"before": None, "fleet_before": None, "at": now}


def _doc() -> dict:
    return dict(st.get("retypewatch").get("watch") or {})


def begin(name: str, was: str, to: str, pre: dict | None, now: int | None = None) -> None:
    """A change of type went well: the note that the looks follow. `pre`: `snapshot` taken before it."""
    now = now or int(time.time())
    watch = {k: v for k, v in _doc().items() if now - int(v.get("at") or 0) < KEEP_AFTER + LOOKS[-1]}
    watch[name] = {"was": was, "to": to, "at": now, "before": (pre or {}).get("before"), "fleet_before": (pre or {}).get("fleet_before"), "looks": 0}
    st.put("retypewatch", {"watch": watch})

    with session() as db:
        emit(db, "info", "tunnels", f"Tunnel {name} was changed from {was} to {to}. The Doctor looks at what its users use after 30 minutes, 2 hours and 6 hours, and puts {was} back when they leave it.", name)


def _id_of(name: str) -> int | None:
    with session() as db:
        return db.scalar(select(Tunnel.id).where(Tunnel.name == name, Tunnel.enabled.is_(True)))


def _revert(name: str, tunnel_id: int, e: dict, text: str) -> None:
    """The users left: the old type goes back (the no-cut change, without the gain it asks of a change forward), and the new one is entered in the memory of the pair as one that did not work."""
    from . import autopilot, doctor, knowledge, swap

    with session() as db:
        emit(db, "warn", "tunnels", f"Tunnel {name}: after the change to {e['to']} {text}. The Doctor puts {e['was']} back.", name)
        t = db.get(Tunnel, tunnel_id)
        ends = (t.iran_server_id, t.foreign_server_id) if t is not None else None

    if ends:
        try:
            knowledge.record(ends[0], ends[1], {"kind": e["to"], "ok": False, "score": None, "error": "the users left the tunnel after it was changed to this type"}, "full")
        except Exception:
            log.exception("the memory of the pair of %s could not be told that %s lost its users", name, e["to"])

    swap._TRIED.pop(name, None)          # (a change back is a repair: the three-hour gap of a change forward does not apply)

    try:
        autopilot._start(name, doctor._run_retype, tunnel_id, e["was"], e["to"], f"The users left {e['to']}: {text}.", True, True, action="autopilot", source="auto")
    except ValueError as ex:
        log.info("the change back of %s not started: %s", name, str(ex)[:100])

        with session() as db:
            emit(db, "bad", "tunnels", f"Tunnel {name}: the change back to {e['was']} could not be started ({str(ex)[:120]}); look at the tunnel.", name)


# ------------------------------------------------------------------ the job
def run() -> None:
    """Job (every five minutes): the looks that are due, one note at a time."""
    from . import actions, autopilot

    if get_settings().read_only or jobs.stopping() or actions.doctor_mode() == "off" or autopilot.quiet_now():
        return

    now = int(time.time())
    watch = _doc()
    changed = False

    for name, e in list(watch.items()):
        if now - int(e.get("at") or 0) > KEEP_AFTER + LOOKS[-1]:
            watch.pop(name)
            changed = True
            continue

        i = due_look(e, now)

        if i is None:
            continue

        tid = _id_of(name)

        if tid is None:
            watch.pop(name)                                              # (the tunnel is gone)
            changed = True
            continue

        with session() as db:
            after, fleet_after = _avg(db, tid, now - AFTER_WINDOW, now), _fleet(db, tid, now - AFTER_WINDOW, now)

        kind, text = verdict(relative_use(e.get("before"), after, e.get("fleet_before"), fleet_after))
        e["looks"] = i + 1
        e["last"] = {"at": now, "verdict": kind}
        changed = True

        if kind == "drop":
            e["closed"] = "reverted"
            _revert(name, tid, e, text)
        elif i + 1 == len(LOOKS):
            e["closed"] = "kept"

            with session() as db:
                emit(db, "ok", "tunnels", f"Tunnel {name} on {e['to']}: six hours after the change {text if kind == 'ok' else 'its use could not be compared'}. The change stays.", name)

    if changed:
        st.put("retypewatch", {"watch": watch})
