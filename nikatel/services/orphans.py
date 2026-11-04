"""The Doctor's sweep of what nothing owns (1.69.219).
(The owner, 2026-11-04, seeing the same tunnel twice again: "the Doctor was to remove the duplicate tunnels by itself - I want the extra records and the extra ports gone from the panel and from the servers. The DNS records
 of Arvan stay: at some moment we may have to switch to them, we must always be ready".)

A tunnel that is removed, moved or rebuilt leaves things behind: the warm standby and the strands of a Trident that belonged to it, a backhaul server nobody dials, the link of a GRE tunnel with its check timer, a test
door (`nikatel-sink-…`). By hand they were found at the end of a long day: 49 idle units on 12 servers and two standby rows of tunnels that were gone. This job finds them by itself.

  what is owned    a unit is owned when a tunnel of the panel - visible or a hidden helper, a standby or a strand - names it (`tunnels.plan_for`), or a link / sink carries the name of such a tunnel's link. Everything else of the
                   engines (backpack, backhaul, paqet, chisel, the GRE link and its timer, the sinks) is not owned. The web UI and the monitor of BackPack are the engine's own and never touched.
  what is idle     no peer or user is connected to it (the sockets of its process), and its link carries not one byte in six seconds (even a keepalive means its other end is alive).
  when it goes     only when two looks, at least CONFIRM_AFTER seconds apart, found it unowned and idle (a tunnel that is being built is owned a moment later), a test one (`test` in its name) only after a day; in the modes fix
                   and move, never in a storm, at most MAX_PER_SERVER on a server in one look and MAX_PER_DAY in a day; a server that shows more than TOO_MANY of them is told, not touched (then the list of what the panel owns
                   is wrong, not the server). Before a server is touched a copy of the configs of the engines is taken (`orphan_backup`, the last five are kept in /var/lib/nikatel-orphans).
  the helper rows  a hidden tunnel (standby or strand) whose parent tunnel is gone, seen so for ROW_GRACE seconds with no traffic, is removed like any tunnel (into Recently removed).
  never            a DNS record. The records are the way back to Arvan; this job does not read them and cannot write them.

The removal uses the reviewed actions the panel already has (`remove_backhaul_unit`, `remove_tunnel_files`, `other_remove`, `bw_sink_remove`). The pure parts (`removal_of`, `owned`, `parse_look`, `idle_orphans`, `confirm`, `rails`)
carry the decisions and are tested."""
from __future__ import annotations

import concurrent.futures as cf
import functools
import re
import threading
import time

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel
from ..ssh import SSHError
from . import settings as st
from .events import emit

log = get_logger("orphans")
EVERY = 1800
CONFIRM_AFTER = 3000          # a unit is removed only when two looks this far apart found it unowned and idle
ROW_GRACE = 900               # a hidden helper whose parent is gone: this long, with no traffic, before it is removed
TEST_AGE = 24 * 3600          # a unit with "test" in its name is somebody's test: it must be unowned this long
MAX_PER_SERVER = 6
MAX_PER_DAY = 30
TOO_MANY = 25
KEEP = ("backpack-monitor.service", "backpack-webui.service")
_LOCK = threading.Lock()
_TNAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,14}$")
_BH_IRAN = re.compile(r"^backhaul-iran-(?:tcp|tcpmux|wsmux|wssmux)-\d{1,5}\.service$")
_BH_KHAREJ = re.compile(r"^backhaul-kharej-(tcp|tcpmux|wsmux|wssmux)-(\d{1,5})\.service$")
_ENGINE = re.compile(r"^(backpack|paqet|chisel)-([a-z0-9][a-z0-9-]*?)(-kharej)?\.service$")
_GRE = re.compile(r"^nikatel-(?:gre-check|gre)-([a-z0-9][a-z0-9-]*)\.(?:service|timer)$")
_SINK = re.compile(r"^nikatel-sink-([a-z0-9][a-z0-9-]*)\.service$")


# ------------------------------------------------------------------ pure helpers (tested)
def removal_of(unit: str) -> tuple[str, dict] | None:
    """Pure: the reviewed action that takes a leftover unit off a server, or None when it cannot be done safely (a name longer than the panel's own names, a unit of no known shape)."""
    if _BH_IRAN.match(unit):
        return "remove_backhaul_unit", {"UNIT": unit}

    m = _BH_KHAREJ.match(unit)

    if m:
        return "remove_tunnel_files", {"NAME": "orphan" + m.group(2), "PORT": m.group(2), "TRANSPORT": m.group(1)}

    m = _ENGINE.match(unit)

    if m and _TNAME.match(m.group(2)):
        return "other_remove", {"NAME": m.group(2), "KIND": m.group(1), "PORT": "0", "PROTO": "tcp"}

    m = _GRE.match(unit)

    if m and _TNAME.match(m.group(1)):
        return "remove_tunnel_files", {"NAME": m.group(1), "PORT": "0", "TRANSPORT": "tcpmux"}

    m = _SINK.match(unit)

    if m and re.fullmatch(r"bw\d{1,7}", m.group(1)):
        return "bw_sink_remove", {"NAME": m.group(1)}

    if m and _TNAME.match(m.group(1)):
        return "other_remove", {"NAME": m.group(1), "KIND": "backpack", "PORT": "0", "PROTO": "tcp"}

    return None


def owned(unit: str, expected: set[str], ifaces: set[str], slugs: set[str], ids: set[int]) -> bool:
    """Pure: does a tunnel of the panel own this unit on this server? `expected`: the units the tunnels name there; `ifaces`: their link names; `slugs`: the server-side names of their engines (a test door may carry a
    shortened one); `ids`: the ids of every tunnel row (the bandwidth sink of tunnel 604 is `nikatel-sink-bw604`)."""
    if unit in expected or unit in KEEP:
        return True

    m = _GRE.match(unit)

    if m:
        return m.group(1) in ifaces

    m = _SINK.match(unit)

    if m:
        key = m.group(1)

        if re.fullmatch(r"bw\d{1,7}", key):
            return int(key[2:]) in ids

        return any(key == s or s.startswith(key) or key.startswith(s) for s in slugs | ifaces)

    return False


def parse_look(out: str) -> tuple[dict[str, dict], dict[str, int]]:
    """Pure: the answer of `orphan_look` -> ({unit: {"active", "estab" (None for a timer or a link unit)}}, {link: bytes in six seconds, -1 = no such link})."""
    units, links = {}, {}

    for ln in out.splitlines():
        p = ln.split()

        try:
            if len(p) >= 4 and p[0] == "U":
                units[p[1]] = {"active": p[2], "estab": None if p[3] == "-" else int(p[3])}
            elif len(p) >= 3 and p[0] == "L":
                links[p[1]] = int(p[2])
        except ValueError:
            continue

    return units, links


def idle_orphans(units: dict[str, dict], links: dict[str, int], is_owned) -> list[str]:
    """Pure: the units that nothing owns and nothing uses: no peer or user connected to its process, and - for the link of a GRE tunnel - not a byte of traffic in six seconds."""
    out = []

    for u, v in units.items():
        if is_owned(u) or v.get("estab"):
            continue

        m = _GRE.match(u)
        moved = links.get(m.group(1), 0) if m else 0

        if moved > 0 or moved < -1:          # (a byte went through it, or its counters jumped back - the link was made again just now: either way something is working with it; -1 is "no such link")
            continue

        out.append(u)

    return sorted(out)


def is_test(unit: str) -> bool:
    return "test" in unit


def confirm(seen: dict, sid: int, found: list[str], now: int) -> tuple[list[str], dict]:
    """Pure: (the units that were seen unowned and idle by an earlier look long enough ago, the new memory of the server). A unit that is not found now is forgotten; a test one needs TEST_AGE."""
    prefix = f"{sid}:"
    mem = {k: v for k, v in seen.items() if not k.startswith(prefix)}
    ready = []

    for u in found:
        first = int(seen.get(prefix + u, now))
        mem[prefix + u] = first
        need = TEST_AGE if is_test(u) else CONFIRM_AFTER

        if now - first >= need:
            ready.append(u)

    return ready, mem


def rails(done: list, now: int) -> str:
    """Pure: '' when one more removal may be made, else why not (MAX_PER_DAY in 24 hours)."""
    recent = [x for x in done if now - int(x) < 86400]
    return f"{MAX_PER_DAY} leftovers were removed in the last 24 hours" if len(recent) >= MAX_PER_DAY else ""


def actions_for(units: list[str]) -> list[tuple[str, dict]]:
    """Pure: the removals of these units, one for each distinct (action, parameters) - a GRE link, its check and its timer are one removal."""
    seen, out = set(), []

    for u in units:
        r = removal_of(u)

        if r is None:
            continue

        key = (r[0], tuple(sorted(r[1].items())))

        if key not in seen:
            seen.add(key)
            out.append(r)

    return out


# ------------------------------------------------------------------ what the panel owns
def _world() -> dict | None:
    """{"expected": {server id: units}, "ifaces": {...}, "slugs": {...}, "ids": set, "names": set, "rows": hidden rows} from every tunnel row, visible or hidden; None when a plan cannot be made (then nothing is judged)."""
    from . import tunnels as T

    expected: dict[int, set[str]] = {}
    ifaces: dict[int, set[str]] = {}
    slugs: dict[int, set[str]] = {}
    ids, names, rows = set(), set(), []

    with session() as db:
        for t in db.scalars(select(Tunnel)):
            try:
                p = T.plan_for(t)
            except Exception:
                log.exception("the units of tunnel %s could not be worked out: no leftover is judged this time", t.name)
                return None

            ids.add(t.id)
            names.add(t.name)
            rec = t.record or {}

            if not t.enabled and (rec.get("standby_of") or rec.get("strand_of")):
                rows.append({"id": t.id, "name": t.name, "parent": str(rec.get("standby_of") or rec.get("strand_of")), "rx": t.rx_mbps, "tx": t.tx_mbps})

            for sid, units in ((t.iran_server_id, p.iran_units or []), (t.foreign_server_id, p.foreign_units or [])):
                for u in units:
                    expected.setdefault(sid, set()).add(u)
                    m = re.match(r"^(?:backpack|paqet|chisel)-(.+?)(?:-kharej)?\.service$", u)

                    if m:
                        slugs.setdefault(sid, set()).add(m.group(1))

            if p.iface:
                for sid in (t.iran_server_id, t.foreign_server_id):
                    ifaces.setdefault(sid, set()).add(p.iface)

    return {"expected": expected, "ifaces": ifaces, "slugs": slugs, "ids": ids, "names": names, "rows": rows}


def _servers() -> list[dict]:
    with session() as db:
        return [{"id": s.id, "name": s.name, "host": s.host, "user": s.ssh_user, "port": s.ssh_port} for s in db.scalars(select(Server).where(Server.enabled.is_(True), Server.online.is_(True)))
                if s.role in ("iran", "foreign")]


def _node(s: dict):
    from ..ssh import Node

    return Node(s["host"], s["user"], s["port"])


def _look(s: dict) -> str:
    from ..ssh import run_action

    return run_action(_node(s), "orphan_look", {}, timeout=60)


def _backup(s: dict) -> str:
    from ..ssh import run_action

    return run_action(_node(s), "orphan_backup", {}, timeout=90).strip().splitlines()[-1][:120]


def _act(s: dict, action: str, params: dict) -> str:
    from ..ssh import run_action

    return run_action(_node(s), action, params, timeout=90).strip().splitlines()[-1][:100]


def _doc() -> dict:
    d = st.get("orphans")
    return {"seen": dict(d.get("seen") or {}), "done": [int(x) for x in (d.get("done") or [])]}


# ------------------------------------------------------------------ helper rows of the panel
def drop_helpers_of(name: str) -> list[str]:
    """The warm standby and the strands that belonged to a tunnel that was just removed: removed with it (into Recently removed). Returns their names."""
    from . import builder

    with session() as db:
        rows = [(t.id, t.name) for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))) if (t.record or {}).get("standby_of") == name or (t.record or {}).get("strand_of") == name]

    out = []

    for tid, hname in rows:
        try:
            builder.delete_tunnel(tid, trash=True, skip_offline=True)
            out.append(hname)
        except (ValueError, RuntimeError) as e:
            log.warning("the helper %s of %s could not be removed: %s", hname, name, str(e)[:100])

    return out


def _orphan_rows(world: dict, seen: dict, now: int) -> tuple[list[dict], dict]:
    """(hidden helpers whose parent is gone, seen so long enough with no traffic; the new memory)."""
    mem = {k: v for k, v in seen.items() if not k.startswith("row:")}
    ready = []

    for r in world["rows"]:
        if r["parent"] in world["names"]:
            continue

        key = f"row:{r['id']}"
        first = int(seen.get(key, now))
        mem[key] = first

        if now - first >= ROW_GRACE and float(r.get("rx") or 0) + float(r.get("tx") or 0) < 0.1:
            ready.append(r)

    return ready, mem


# ------------------------------------------------------------------ the job
def run() -> None:
    """Job (every half hour): the helper rows and the units nothing owns are looked for, confirmed by a second look, and removed (see the top of this file)."""
    from . import actions, autopilot, builder

    if get_settings().read_only or jobs.stopping() or actions.doctor_mode() == "off" or autopilot.storm_now():
        return

    if not _LOCK.acquire(blocking=False):
        return

    try:
        now = int(time.time())
        doc = _doc()
        seen, done = doc["seen"], [x for x in doc["done"] if now - x < 86400]
        world = _world()

        if world is None:
            return

        ready_rows, seen = _orphan_rows(world, seen, now)

        for r in ready_rows:
            if rails(done, now):
                break

            try:
                builder.delete_tunnel(r["id"], trash=True, skip_offline=True)
            except (ValueError, RuntimeError) as e:
                log.warning("the helper row %s could not be removed: %s", r["name"], str(e)[:100])
                continue

            done.append(now)

            with session() as db:
                emit(db, "warn", "tunnels", f"Tunnel {r['name']} was the standby or a strand of {r['parent']}, which is gone: it was removed too (it can be restored from Recently removed).", r["name"])

        servers = _servers()

        def look(s):
            try:
                return s, _look(s)
            except (SSHError, ValueError, RuntimeError, OSError) as e:
                log.info("the leftovers of %s could not be looked for: %s", s["name"], str(e)[:100])
                return s, None

        with cf.ThreadPoolExecutor(6) as ex:
            looks = list(ex.map(look, servers))

        for s, out in looks:
            if out is None:
                continue

            units, links = parse_look(out)
            sid = s["id"]
            exp, ifs, slg = world["expected"].get(sid, set()), world["ifaces"].get(sid, set()), world["slugs"].get(sid, set())
            found = idle_orphans(units, links, functools.partial(owned, expected=exp, ifaces=ifs, slugs=slg, ids=world["ids"]))

            if len(found) > TOO_MANY:
                _tell(f"toomany:{sid}", f"Server {s['name']} shows {len(found)} units that no tunnel of the panel owns: more than the sweep trusts itself with. Nothing is removed; look at the list of tunnels of this server.")
                ready, seen = [], {k: v for k, v in seen.items() if not k.startswith(f"{sid}:")}
            else:
                ready, seen = confirm(seen, sid, found, now)

            todo = actions_for(ready)[:MAX_PER_SERVER]

            if not todo or rails(done, now):
                continue

            try:
                saved = _backup(s)
            except (SSHError, ValueError, RuntimeError, OSError) as e:
                log.warning("no backup on %s (%s): nothing is removed there", s["name"], str(e)[:100])
                continue

            gone = []

            for action, params in todo:
                try:
                    _act(s, action, params)
                    gone.append(params.get("UNIT") or params.get("NAME"))
                    done.append(now)
                except (SSHError, ValueError, RuntimeError, OSError) as e:
                    log.warning("%s on %s failed: %s", action, s["name"], str(e)[:100])

            if gone:
                with session() as db:
                    emit(db, "warn", "servers", f"Server {s['name']}: {len(gone)} leftover(s) of removed tunnels were taken off ({', '.join(gone[:6])}{'…' if len(gone) > 6 else ''}); nothing owned them and nothing used them. A copy of the configs is in {saved}.", s["name"])

                ready_set = set(ready)
                seen = {k: v for k, v in seen.items() if not (k.startswith(f"{sid}:") and k.split(":", 1)[1] in ready_set)}

        st.put("orphans", {"seen": seen, "done": done[-200:]})
    finally:
        _LOCK.release()


_TOLD: dict[str, float] = {}


def _tell(key: str, text: str) -> None:
    if time.time() - _TOLD.get(key, 0) < 24 * 3600:
        return

    _TOLD[key] = time.time()

    with session() as db:
        emit(db, "warn", "servers", text, "")
