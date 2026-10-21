"""The WARM STANDBY of a tunnel: a second tunnel of another type, between the same two servers, built BEFORE the cut and kept idle, so that a cut tunnel is replaced in seconds instead of being rebuilt in
a minute and a half (the owner, 2026-10-17: "bring the delay down to the minimum"; docs/standby.md).

  what it is         a real tunnel, named `<name>-sb`, of the type the Doctor's knowledge ranks best for this pair and that does not share the carrier of the tunnel (another family: if the tunnel is Backhaul over GRE,
                     not a GRE kind), on SHIFTED ports: the users' port P is `20000 + P` on the Iran server (above 45535: `P - 20000`). It carries no user and nothing but its own checks. Its row is switched off
                     (`enabled` False, `record.standby_of` = the name of the tunnel): every place that lists, counts, alerts on, repairs, surveys or measures the enabled tunnels leaves it alone
                     (`models.is_standby`), and the standby job looks at it itself. It is never "a cut of the tunnel": its falls make no event, no alert, no repair - it is mended quietly.
  the handover       when the tunnel is DOWN for `after_seconds` (default 5) and the standby is up, ONE reviewed action on the Iran server (ssh.py `standby_on`) puts a NAT redirect in PREROUTING from the users'
                     port to the shifted port (tagged, idempotent, kept across a reboot by a small unit): about two seconds. The Doctor's ladder goes on with the tunnel in the background. The request the panel
                     makes ON the server is not redirected, and a tunnel whose ports are handed over is judged without the look from another Iran server (`tunnels.HANDED`), so it is still judged by what IT carries.
  the way back       when the tunnel has been UP for `RETURN_AFTER` (60 s, no repair running), `standby_off` takes the redirects off; connections that are open stay on the standby until they close (a harmless
                     tagged rule is left in the NAT chain on purpose: measured, deleting the LAST rule of the chain makes the kernel reset every connection it translated - it is taken off with the standby). Nothing is deleted.
                     Every handover is an entry of the Doctor's ledger (the Moves tab) and an event; "Back now" in the tunnel window or on the entry.
  the modes          off: nothing happens by itself (every button still works by hand); fix, move: the standby is built, mended and handed over by itself.
  the rails          one standby per tunnel; nothing while the panel is read-only; no build in a storm, while the tunnel is not up, while a repair works on it, on a busy server, for a pair every type of which failed
                     lately; a build that is running when the tunnel falls is stopped, so the servers are free for the repair; a failed build waits 2, 4, 8 ... 60 minutes; a handover is refused when the standby
                     is not up (it is looked at again first when its last look is older than FRESH).
  the carrier        a standby that ends up on the SAME carrier as its tunnel (the Doctor swapped the tunnel to another type, or brought it back, after the standby was built: a GRE filter would cut both) is built
                     again on another carrier - but only when a type of another carrier is known to connect on this pair, the tunnel has been up for RETURN_AFTER, the standby is older than CLASH_AFTER and
                     the last way back is older than CLASH_QUIET (removing the standby ends the connections that stayed on it).

The pure helpers (`shifted`, `shift_mapping`, `standby_name`, `carrier`, `choose_kind`, `better_carrier`, `decide`, `parse_list`, `retry_wait`) carry the decisions and are tested."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Server, Tunnel, is_standby, is_temp
from ..ssh import Node, SSHError, run_action, run_script
from . import (
    actions,
    autopilot,
    build_other,
    builder,
    doctor,
    knowledge,
    license,
    tunnel_test,
    tunnels,
)
from . import settings as st
from .events import emit

log = get_logger("standby")
SHIFT = 20000                 # the users' port P is SHIFT + P on the Iran server
SUFFIX = "-sb"
AFTER = 5                     # seconds a tunnel is DOWN before its users' ports are handed over (setting `after_seconds`)
RETURN_AFTER = 60             # the tunnel must have been UP this long (two checks) before the users go back
CHECK_EVERY = 15              # the standby is looked at this often
FRESH = 30                    # a look at the standby older than this is made again before a handover
UNHEALTHY_AFTER = 45          # a standby that is not up for this long gets its units restarted
REBUILD_AFTER = 300           # ... and is built again after this long
RESTART_EVERY = 300           # at most one restart of a standby this often
CLASH_AFTER = 300             # a standby on the carrier of its tunnel is built again only when it is at least this old (no loop of rebuilds while the Doctor swaps the tunnel)
CLASH_QUIET = 900             # ... and when the last way back is this old (the connections that stayed on the standby after it are mostly gone by then)
BETTER_EVERY = 60             # the knowledge is asked for a type to replace a clashing standby this often
MISSING_GRACE = 3 * 3600      # a marked tunnel whose row is gone this long is not coming back: the mark and its standby go
BUSY_CPU, BUSY_RAM = 85.0, 90.0      # a server this busy does not get a second tunnel
KIND_SKIP = 6 * 3600          # a type whose build failed is not chosen again for this long
TEST_PORT = tunnel_test.TEST_PORT
_LOCK = threading.Lock()
_BUILDS: dict[str, dict] = {}         # tunnel name -> {"aid", "name", "kind", "at"}: the standby builds that are running
_LOCKS: dict[str, threading.RLock] = {}      # one at a time per tunnel: a handover, the way back and the removal of its standby
_CHECKED: dict[int, float] = {}       # standby id -> when it was last looked at
_RESTARTED: dict[int, float] = {}     # standby id -> when its units were last restarted
_MISSING: dict[str, int] = {}         # tunnel name -> since when its row is missing
_TOLD: set[str] = set()               # standbys whose "ready" has been told
_NOTE: dict[str, str] = {}            # tunnel name -> why nothing is built now (shown in the tunnel window)
_BETTER: dict[tuple, tuple[int, str]] = {}      # (tunnel name, its type, its standby's type) -> (when asked, the type that should replace a standby on the carrier of its tunnel)
_STATE = {"init": False, "reconciled": 0.0}
WANT_FOR = 1200                       # a type asked for by the swap (`want`) is built for this long, whoever starts the build
_WANT: dict[str, tuple[str, int]] = {}      # tunnel name -> (the type its next standby must be, until when): the swap beside the tunnel (services/swap.py)


# ------------------------------------------------------------------ pure helpers (tested)
def shifted(port: int) -> int | None:
    """The shifted port of a users' port: SHIFT + P, or P - SHIFT above 45535 (None when neither is a usable port)."""
    p = int(port)

    if 1 <= p and p + SHIFT <= 65535:
        return p + SHIFT

    if p - SHIFT >= 1024 and p <= 65535:
        return p - SHIFT

    return None


def norm_ports(text: str) -> str:
    """A port mapping in the one form the builders keep it in ('443=2096, 80' -> '443:2096,80')."""
    return ",".join(p.spec() for p in builder.parse_ports(text))


def shift_mapping(text: str, taken: set[int]) -> tuple[str, dict[int, int]]:
    """('443=2096, 80', {...}) -> ('20443:2096,20080:80', {443: 20443, 80: 20080}): every users' port is shifted, the port on the foreign side stays. ValueError with a sentence when a port cannot be
    shifted or the shifted port is taken (`taken`: the ports other tunnels listen on, on the Iran server)."""
    maps = builder.parse_ports(text)

    if len(maps) > 200:
        raise ValueError("a standby follows at most 200 forwarded ports")

    used = set(taken) | {m.listen for m in maps} | {TEST_PORT}
    out, mapping = [], {}

    for m in maps:
        s = shifted(m.listen)

        if s is None:
            raise ValueError(f"port {m.listen} cannot be shifted to a free port, so it has no standby")

        if s in used:
            raise ValueError(f"port {s} (the shifted port of {m.listen}) is already used on the Iran server")

        used.add(s)
        mapping[m.listen] = s
        out.append(f"{s}:{m.target or m.listen}")

    return ",".join(out), mapping


def standby_name(base: str, taken: set[str]) -> str:
    """`<base>-sb` in at most 15 characters (the name is the GRE card of a Backhaul standby), unique among `taken`."""
    stem = str(base)[:12].rstrip("-") or "t"
    name, n = stem + SUFFIX, 2

    while name in taken:
        suf = f"{SUFFIX}{n}"
        name = stem[:15 - len(suf)].rstrip("-") + suf
        n += 1

    return name


def carrier(kind: str) -> str:
    """What carries a tunnel type: 'gre' (protocol 47), 'udp-link' (WireGuard / GRE inside UDP) or the engine ('bp', 'bpd', 'chisel', 'paqet'). A standby that shares the carrier of its tunnel falls with it."""
    if kind in knowledge.GRE_KINDS:
        return "gre"

    if kind in knowledge.UDP_LINK_KINDS:
        return "udp-link"

    return kind.split(":", 1)[0]


def choose_kind(kinds: list[str], primary_kind: str, pair: dict | None, now: int, allowed=lambda k: True, skip: frozenset | set = frozenset()) -> tuple[str, str]:
    """(the type of the standby, the sentence that says why) - ('', why not) when none can be. `kinds`: the Doctor's candidates for this pair, best first (`knowledge.plan_for`). Left out: the tunnel's own type, what
    failed on this pair lately, what the licence does not allow, what a standby build failed with lately (`skip`). Chosen: a type known to connect on this pair before an unknown one, and in each of the two
    groups one with ANOTHER carrier than the tunnel's before one with the same (a standby that falls with the tunnel is no standby)."""
    bad = set(knowledge.blocked(pair, now))
    pool = [k for k in kinds if k != primary_kind and k not in bad and k not in skip and allowed(k)]

    if not pool:
        return "", "no other tunnel type is left for a standby (every one failed on this pair lately, is not allowed by the licence or failed as a standby)"

    known = {r["kind"] for r in knowledge.ranked(pair, now, include_poor=True)}
    mine = carrier(primary_kind)
    best = min(enumerate(pool), key=lambda it: (carrier(it[1]) == mine, it[1] not in known, it[0]))[1]
    note = ("known to connect on this pair" if best in known else "not tested on this pair yet") + (", another carrier than the tunnel's" if carrier(best) != mine else ", the same carrier as the tunnel (nothing else was left)")
    return best, f"{best}: {note}"


def better_carrier(kinds: list[str], primary_kind: str, standby_kind: str, pair: dict | None, now: int, allowed=lambda k: True, skip: frozenset | set = frozenset()) -> str:
    """The type that should replace a standby which shares the carrier of its tunnel (the Doctor swapped the tunnel to another type after the standby was built, or brought it back): the first of `kinds` (the Doctor's
    candidates for this pair, best first) that has ANOTHER carrier and is known to connect on this pair. '' when the standby does not clash, or when no such type is known - a standby on the carrier of its tunnel
    still saves the tunnel from a crashed service, and one removed for a type nobody has tried would leave the tunnel with none if that build fails."""
    mine = carrier(primary_kind)

    if carrier(standby_kind) != mine:
        return ""

    bad = set(knowledge.blocked(pair, now))
    known = {r["kind"] for r in knowledge.ranked(pair, now)}

    for k in kinds:
        if k != primary_kind and k not in bad and k not in skip and allowed(k) and k in known and carrier(k) != mine:
            return k

    return ""


def retry_wait(fails: int) -> int:
    """Seconds a standby build waits after `fails` failures in a row: 2, 4, 8 ... at most 60 minutes."""
    return min(3600, 120 * 2 ** max(0, int(fails) - 1)) if fails else 0


def parse_list(out: str) -> dict[str, list[tuple[str, int, int]]]:
    """The answer of the `standby_list` script -> {standby name: [(protocol, users' port, shifted port), ...]}."""
    res: dict[str, list[tuple[str, int, int]]] = {}

    for line in str(out).splitlines():
        p = line.split()

        if len(p) == 5 and p[0] == "STANDBY" and p[3].isdigit() and p[4].isdigit():
            res.setdefault(p[1], []).append((p[2], int(p[3]), int(p[4])))

    return res


def nat_map(users: list[int], shifted_ports: list[int]) -> str:
    """'80:20080,443:20443' - the pairs the handover redirects."""
    if len(users) != len(shifted_ports) or not users:
        raise ValueError("the ports of the standby are not the shifted ports of the tunnel")

    return ",".join(f"{a}:{b}" for a, b in zip(users, shifted_ports))


def decide(p: dict | None, s: dict | None, *, now: int, auto: bool, storm: bool = False, repairing: bool = False, building: bool = False, cannot_build: str = "", wait_until: int = 0,
           after: int = AFTER, better: str = "") -> tuple[str, str]:
    """(what to do now, why): 'handover', 'back', 'build', 'restart', 'rebuild', 'drop' or 'wait'. Pure. `p`: the tunnel {"state", "state_since", "last_check_at", "iran_id", "foreign_id", "off" (a server of it is off)} or
    None (its row is missing: it is being rebuilt); `s`: its standby {"state", "state_since", "stale" (why it no longer fits the tunnel, "" = it does), "use" (the handover stands), "created_at", "parked_at" (the last way back)} or None; `auto`: the Doctor is
    in mode fix or move and the panel may write; `cannot_build`: the reason the rails give; `wait_until`: no build before this time (a failed build waits); `better`: the type of another carrier that should
    replace a standby on the carrier of its tunnel (`better_carrier`, "" = none)."""
    if p is None:
        return "wait", "the tunnel is being rebuilt or is gone: nothing is touched meanwhile"

    in_use = bool(s and s.get("use"))

    if not auto:
        return "wait", "the Doctor is off (or the panel is read-only): the standby does nothing by itself - the buttons still work"

    if in_use:
        if s["stale"]:
            return "back", f"the handover stands but the standby no longer fits the tunnel ({s['stale']}): the users go back first"

        if (s["use"] or {}).get("manual"):
            return "wait", "handed over by hand: the users' ports stay on the standby until Back now is pressed"

        if p["state"] == "up" and not p["off"] and not repairing and now - p["state_since"] >= RETURN_AFTER and now - p["last_check_at"] <= 120:
            return "back", f"the tunnel has been up for {now - p['state_since']} s: the users go back to it"

        if s["state"] != "up" and p["state"] != "up" and now - s["state_since"] >= 15:
            return "restart", "the users are on the standby and it is not up: its services are restarted at once"

        return "wait", "the users' ports are on the standby"

    if p["state"] == "down" and not p["off"]:
        if s is None:
            return "wait", "the tunnel is down and has no standby"

        if s["stale"]:
            return "wait", f"the tunnel is down but its standby no longer fits it ({s['stale']}): the users' ports stay where they are"

        if s["state"] != "up":
            return "wait", f"the tunnel is down but its standby is {s['state']}: the users' ports stay where they are"

        if now - p["state_since"] < after:
            return "wait", f"the tunnel is down; the handover comes after {after} s"

        return "handover", f"the tunnel has been down for {now - p['state_since']} s and its standby is up"

    if s is not None and s["stale"]:
        return "drop", f"the standby is removed (and built again): {s['stale']}"

    healthy = p["state"] == "up" and not p["off"] and not repairing and not storm

    if s is None:
        if building:
            return "wait", "the standby is being built"

        if not healthy:
            return "wait", "the standby is built when the tunnel is up and nothing repairs it or the servers"

        if cannot_build:
            return "wait", cannot_build

        if now < wait_until:
            return "wait", f"the last build failed: the next try is in {wait_until - now} s"

        return "build", "the tunnel has no standby"

    if s["state"] != "up":
        sick = now - s["state_since"]

        if not healthy:
            return "wait", f"the standby is {s['state']}; it is mended when the tunnel is up and nothing repairs it"

        if sick >= REBUILD_AFTER:
            return "rebuild", f"the standby has not been up for {sick} s: it is built again"

        if sick >= UNHEALTHY_AFTER:
            return "restart", f"the standby has not been up for {sick} s: its services are restarted"

        return "wait", f"the standby is {s['state']} ({sick} s): it is restarted after {UNHEALTHY_AFTER} s and built again after {REBUILD_AFTER} s"

    if (better and healthy and now - p["state_since"] >= RETURN_AFTER and now - p["last_check_at"] <= 120 and now - int(s.get("created_at") or 0) >= CLASH_AFTER
            and now - int(s.get("parked_at") or 0) >= CLASH_QUIET):
        return "rebuild", f"the standby has the carrier of its tunnel (a filter would cut both): it is built again as {better}"

    return "wait", "ready"


# ------------------------------------------------------------------ what is kept
def _marks() -> dict:
    return dict(st.get("standby").get("marked") or {})


def _mark(name: str, on: bool, manual: bool = False) -> None:
    with _LOCK:
        d = st.get("standby")
        marked = dict(d.get("marked") or {})
        failed = dict(d.get("failed") or {})

        optout = dict(d.get("optout") or {})

        if on:
            marked.setdefault(name, {"since": int(time.time())})
            optout.pop(name, None)
        else:
            marked.pop(name, None)
            failed.pop(name, None)

            if manual:
                optout[name] = int(time.time())               # the owner switched it off by hand: the automatic standby leaves this tunnel alone

        st.put("standby", {"marked": marked, "failed": failed, "optout": optout})


def _failed(name: str) -> dict:
    return dict((st.get("standby").get("failed") or {}).get(name) or {})


def _set_failed(name: str, data: dict | None) -> None:
    with _LOCK:
        failed = dict(st.get("standby").get("failed") or {})

        if data is None:
            failed.pop(name, None)
        else:
            failed[name] = data

        st.put("standby", {"failed": failed})


def _touch(server_id: int) -> None:
    with _LOCK:
        touched = list(st.get("standby").get("touched") or [])

        if server_id not in touched:
            st.put("standby", {"touched": touched + [server_id]})


def _lock(name: str) -> threading.RLock:
    with _LOCK:
        return _LOCKS.setdefault(name, threading.RLock())


def after_seconds() -> int:
    try:
        return max(0, min(300, int(st.get("standby").get("after_seconds", AFTER))))
    except (TypeError, ValueError):
        return AFTER


_RENAMES: list[tuple[str, str]] = []


def note_rename(old: str, new: str) -> None:
    """A tunnel was renamed (builder._rename_row, inside its session): the standby job moves the mark and the standby to the new name at its next turn, outside that session."""
    _RENAMES.append((old, new))
    wake()


def _apply_renames() -> None:
    while _RENAMES:
        old, new = _RENAMES.pop(0)
        renamed(old, new)


def renamed(old: str, new: str) -> None:
    """A tunnel was renamed: its mark, its failures and its standby follow the new name."""
    with _LOCK:
        d = st.get("standby")
        changes = {}

        for key in ("marked", "failed"):
            if old in (d.get(key) or {}):
                v = dict(d[key])
                v[new] = v.pop(old)
                changes[key] = v

        if changes:
            st.put("standby", changes)

    if old in _BUILDS:
        _BUILDS[new] = _BUILDS.pop(old)

    from . import lanes

    lanes.renamed(old, new)

    if old in tunnels.HANDED:
        tunnels.HANDED.discard(old)
        tunnels.HANDED.add(new)

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))):
            if (t.record or {}).get("standby_of") == old:
                t.record = {**(t.record or {}), "standby_of": new}


# ------------------------------------------------------------------ what the job reads of a tunnel and its standby
def _node(host: str, user: str, port: int) -> Node:
    return Node(host, user, port)


def _pview(t: Tunnel) -> dict:
    rec = t.record or {}
    ports = [int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()]
    return {"id": t.id, "name": t.name, "state": t.state, "state_since": int(t.state_since or 0), "last_check_at": int(t.last_check_at or 0), "iran_id": t.iran_server_id, "foreign_id": t.foreign_server_id,
            "off": bool(tunnels.servers_off(t)), "kind": autopilot.kind_of(t.type, t.transport, rec), "mapping": rec.get("port_mapping") or ",".join(str(x) for x in ports), "ports": ports,
            "iran": (t.iran.host, t.iran.ssh_user, t.iran.ssh_port), "label": f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"}


def _stale(t: Tunnel, p: dict | None) -> str:
    """Why a standby no longer fits its tunnel ('' = it does): another pair of servers, other ports."""
    if p is None:
        return ""

    if (t.iran_server_id, t.foreign_server_id) != (p["iran_id"], p["foreign_id"]):
        return "the tunnel runs between other servers now"

    try:
        want = norm_ports(shift_mapping(p["mapping"], set())[0])
        have = norm_ports((t.record or {}).get("port_mapping") or "")
    except ValueError:
        return "the ports of the tunnel cannot be shifted"

    return "" if want == have else "the forwarded ports of the tunnel changed"


def _sview(t: Tunnel, p: dict | None) -> dict:
    rec = t.record or {}
    return {"id": t.id, "name": t.name, "state": t.state, "state_since": int(t.state_since or 0), "last_check_at": int(t.last_check_at or 0), "iran_id": t.iran_server_id, "foreign_id": t.foreign_server_id,
            "kind": autopilot.kind_of(t.type, t.transport, rec), "ports": [int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()], "use": rec.get("use") or None, "stale": _stale(t, p),
            "udp": bool((rec.get("opts") or {}).get("accept_udp")), "last_error": t.last_error or "", "iran": (t.iran.host, t.iran.ssh_user, t.iran.ssh_port), "created_at": int(t.created_at or 0),
            "iran_off": t.iran.enabled is not False and t.iran.online is False, "parked": bool(rec.get("parked")), "parked_at": int(rec.get("parked_at") or 0), "lane": rec.get("lane") or None}


def standby_row(db, name: str) -> Tunnel | None:
    return next((t for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))) if (t.record or {}).get("standby_of") == name), None)


def _views(names: list[str]) -> dict[str, dict]:
    """{tunnel name: {"p": the tunnel, "s": its standby}} as plain data (two reads of the table, no session held): each is None when there is no such row."""
    with session() as db:
        rows = {t.name: t for t in db.scalars(select(Tunnel).where(Tunnel.name.in_(list(names))))} if names else {}
        sbs = {str((t.record or {}).get("standby_of")): t for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))) if is_standby(t)}
        out = {}

        for n in names:
            t = rows.get(n)
            p = _pview(t) if t is not None and not is_standby(t) and not is_temp(t.name) else None
            out[n] = {"p": p, "s": _sview(sbs[n], p) if n in sbs else None}

    return out


def _view(name: str) -> dict:
    return _views([name])[name]


def _set_use(standby_id: int, use: dict | None, parked: bool = False) -> None:
    """The handover of a standby (`record.use`) written or cleared. `parked`: a clear that left a rule of its tag on the server (see `_rules_off`): it is taken off when the standby is removed."""
    with session() as db:
        t = db.get(Tunnel, standby_id)

        if t is None:
            return

        rec = dict(t.record or {})

        if use is None:
            rec.pop("use", None)

            if parked:
                rec["parked"] = True
                rec["parked_at"] = int(time.time())
        else:
            rec["use"] = use
            rec.pop("parked", None)
            rec.pop("parked_at", None)

        t.record = rec


# ------------------------------------------------------------------ the ledger of the Doctor (the Moves tab)
def _ledger_open(name: str, p: dict, s: dict, why: str) -> str:
    """One entry per handover: written when the users' ports are handed over, closed (returned) when they go back."""
    now = int(time.time())
    e = {"id": f"standby-{name}-{now}", "at": now, "reason": "standby", "why": why, "tunnels": [], "asked": [name], "from_id": 0, "from": f"{name} ({p['kind']})", "from_ip": "", "to_id": None, "to": f"standby {s['kind']}",
         "to_ip": "", "dns": 0, "ok": True, "returned": False, "returned_at": None, "leftover": []}
    d = doctor._state()
    d["moves"].append(e)
    doctor._save(d)
    return e["id"]


def _ledger_close(move_id: str) -> None:
    d = doctor._state()

    for e in d["moves"]:
        if e.get("id") == move_id and not e.get("returned"):
            e["returned"], e["returned_at"] = True, int(time.time())
            doctor._save(d)
            return


# ------------------------------------------------------------------ the handover and the way back
def _rules_off(node: Node, sb_name: str, park: bool = False) -> str:
    """`park`: the way back (new connections stop being redirected, the open ones stay on the standby: a harmless rule of the tag is left in the NAT chain on purpose - an empty NAT chain makes the kernel reset every
    open connection it translated); else the standby is going away and every rule of its tag is taken off."""
    out = run_action(node, "standby_off", {"NAME": sb_name, "PARK": "1" if park else "0"}, timeout=40)
    m = re.search(rf"STANDBY_OFF {re.escape(sb_name)} (\d+)", out)

    if not m or int(m.group(1)) != 0:
        raise RuntimeError("the redirects were not taken off: " + out.strip()[-160:])

    return out


def handover(name: str, why: str = "", manual: bool = False) -> dict:
    """Hand the users' ports of tunnel `name` to its standby now: one action on the Iran server. ValueError when it cannot be done (the sentence says why), RuntimeError when the server did not do it.
    `manual` (the button): the handover stays until Back now is pressed - the job does not give it back by itself."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only: nothing is changed")

    with _lock(name):
        v = _view(name)
        p, s = v["p"], v["s"]

        if p is None or s is None:
            raise ValueError("this tunnel has no standby")

        if s["use"]:
            return {"ok": True, "already": True}

        if s["stale"]:
            raise ValueError("the standby does not fit the tunnel any more: " + s["stale"])

        if s["state"] != "up" or time.time() - s["last_check_at"] > FRESH:
            tunnels.check_tunnel(s["id"])
            s = _view(name)["s"] or s

        if s["state"] != "up":
            raise ValueError(f"the standby is {s['state']}: the users' ports stay where they are" + (f" ({s['last_error']})" if s["last_error"] else ""))

        pairs = nat_map(p["ports"], s["ports"])
        protos = "tcp udp" if s["udp"] else "tcp"
        iran = _node(*p["iran"])
        t0 = time.time()
        use = {"since": int(t0), "map": pairs, "protos": protos, "move": "", "iran_id": p["iran_id"], **({"manual": True} if manual else {})}
        _set_use(s["id"], use)                  # (written first: if the panel stops in the middle, the next start knows the redirects may stand)
        tunnels.HANDED.add(name)

        try:
            out = run_action(iran, "standby_on", {"NAME": s["name"], "MAP": pairs, "PROTOS": protos}, timeout=40)
            m = re.search(rf"STANDBY_ON {re.escape(s['name'])} (\d+)", out)

            if not m or int(m.group(1)) != len(pairs.split(",")) * len(protos.split()):
                raise RuntimeError("the redirects were not put: " + (out.split("STANDBY_FAIL", 1)[-1].strip() if "STANDBY_FAIL" in out else out.strip()[-160:]))
        except (SSHError, ValueError, RuntimeError) as e:
            _set_use(s["id"], None)
            tunnels.HANDED.discard(name)

            try:
                _rules_off(iran, s["name"])                  # (a half-put handover is taken off again)
            except (SSHError, ValueError, RuntimeError):
                pass

            raise RuntimeError(f"the handover failed: {e}")

        _set_use(s["id"], {**use, "move": _ledger_open(name, p, s, why or "the tunnel is cut")})

    _touch(p["iran_id"])
    secs = round(time.time() - t0, 1)
    cut = max(0, int(time.time()) - p["state_since"]) if p["state"] == "down" else 0

    with session() as db:
        emit(db, "warn", "tunnels", f"Standby: the users' ports of {p['label']} ({', '.join(str(x) for x in p['ports'][:6])}) were handed to its standby tunnel ({s['kind']}) in {secs} s" + (f", {cut} s after the cut." if cut else "."), name)

    log.info("standby: %s handed over to %s in %s s (%s)", name, s["name"], secs, why)
    return {"ok": True, "secs": secs, "standby": s["name"], "kind": s["kind"]}


def give_back(name: str, why: str = "") -> dict:
    """Take the redirects off: the users' ports are the tunnel's again. Open connections stay on the standby until they close. Nothing is deleted."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only: nothing is changed")

    with _lock(name):
        v = _view(name)
        p, s = v["p"], v["s"]

        if s is None or not s["use"]:
            tunnels.HANDED.discard(name)
            return {"ok": True, "already": True}

        use = dict(s["use"])

        if s["iran_off"]:
            log.info("standby: the Iran server of %s is off: its redirects cannot be taken off now (the reconcile does it when the server is back)", name)
        else:
            _rules_off(_node(*s["iran"]), s["name"], park=True)

        _set_use(s["id"], None, parked=not s["iran_off"])
        tunnels.HANDED.discard(name)
        _ledger_close(str(use.get("move") or ""))

    held = int(time.time()) - int(use.get("since") or time.time())

    with session() as db:
        emit(db, "ok", "tunnels", f"Standby: the users' ports of {p['label'] if p else name} are on the tunnel again after {held} s on its standby.", name)

    log.info("standby: %s taken back after %s s (%s)", name, held, why or "by hand")

    from . import lanes

    lanes.refresh(name)                    # (the way back parks a RETURN above a lane's rule: the lane is freed at once)
    return {"ok": True, "held": held}


# ------------------------------------------------------------------ the build, the removal, the restart
def _taken_ports(iran_id: int, own_standby: str = "") -> set[int]:
    """The ports other tunnels use on this Iran server (what the users' ports of a standby must not meet)."""
    out: set[int] = set()

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.iran_server_id == iran_id)):
            if t.name == own_standby:
                continue

            out |= {int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()}
            out.add(int(t.port or 0))
            out.add(int((t.record or {}).get("test_port") or 0))

    return out - {0}


def _busy_reason(*server_ids: int) -> str:
    with session() as db:
        for sid in server_ids:
            srv = db.get(Server, sid)

            if srv is None or not srv.enabled or srv.online is False:
                return f"server {srv.name if srv else sid} is off"

            f = srv.facts or {}

            if float(f.get("cpu") or 0) >= BUSY_CPU or float(f.get("ram") or 0) >= BUSY_RAM:
                return f"server {srv.name} is busy (CPU {f.get('cpu')} %, memory {f.get('ram')} %): a second tunnel would double the processes of the pair"

    return ""


def want(name: str, kind: str) -> None:
    """The next standby of tunnel `name` must be `kind` (the swap beside the tunnel asks for it; the standby job, which may start the build first, obeys too)."""
    with _LOCK:
        _WANT[name] = (kind, int(time.time()) + WANT_FOR)


def unwant(name: str) -> None:
    with _LOCK:
        _WANT.pop(name, None)


def _wanted(name: str) -> str:
    with _LOCK:
        kind, until = _WANT.get(name, ("", 0))

    return kind if kind and time.time() < until else ""


def borrow_mark(name: str) -> dict:
    """The swap needs the standby job to look after the tunnel while it runs: the tunnel is marked if it was not. Returns what `return_mark` needs."""
    d = st.get("standby")
    was = {"marked": name in (d.get("marked") or {}), "optout": name in (d.get("optout") or {})}

    if not was["marked"]:
        _mark(name, True)

    return was


def return_mark(name: str, was: dict) -> None:
    """The mark goes back as it was: a tunnel the owner had switched the standby off for loses the borrowed standby again (the removal ends the connections that stayed on it - the caller waited for them first)."""
    if was.get("marked") or not was.get("optout"):
        return

    _mark(name, False, manual=True)
    threading.Thread(target=_safe_cleanup, args=(name,), name=f"standby-off-{name}", daemon=True).start()


def build_plan(name: str, kind: str = "") -> dict:
    """What a standby of this tunnel would be: its name, type and ports - or ValueError with the reason there is none (nothing is built). `kind`: the type is asked for (the swap beside the tunnel; also `want`)."""
    v = _view(name)
    p = v["p"]

    if p is None:
        raise ValueError("tunnel not found")

    if not p["ports"]:
        raise ValueError("this tunnel forwards no port, so there is nothing to hand over")

    why = _busy_reason(p["iran_id"], p["foreign_id"])

    if why:
        raise ValueError(why)

    now = int(time.time())
    ports_text, mapping = shift_mapping(p["mapping"], _taken_ports(p["iran_id"], ""))
    asked = kind or _wanted(name)

    if asked:
        if asked == p["kind"]:
            raise ValueError("a standby of the tunnel's own type is no spare")

        if not license.tunnel_kind_allowed(asked):
            raise ValueError(f"{asked} is not allowed here (the licence)")

        kind, note = asked, f"{asked}: asked for by the swap of the tunnel's type (built beside the running type, then it takes the users)"
    else:
        pair = knowledge.pair(p["iran_id"], p["foreign_id"])
        kinds, _ = knowledge.plan_for(p["iran_id"], p["foreign_id"], p["kind"], False, False, now=now)
        skip = {k for k, at in (_failed(name).get("kinds") or {}).items() if now - int(at) < KIND_SKIP}
        kind, note = choose_kind(kinds, p["kind"], pair, now, allowed=license.tunnel_kind_allowed, skip=skip)

        if not kind:
            raise ValueError(note)

    with session() as db:
        taken = {t.name for t in db.scalars(select(Tunnel))}

    return {"name": standby_name(name, taken), "kind": kind, "note": note, "ports": ports_text, "mapping": mapping}


def _better_kind(p: dict, s: dict, name: str) -> str:
    """The type of another carrier that should replace the standby when it has the carrier of its tunnel ('' = leave it). Read only; asked only when the two carriers are the same."""
    if carrier(s["kind"]) != carrier(p["kind"]):
        return ""

    now = int(time.time())
    key = (name, p["kind"], s["kind"])
    hit = _BETTER.get(key)

    if hit is not None and now - hit[0] < BETTER_EVERY:
        return hit[1]

    pair = knowledge.pair(p["iran_id"], p["foreign_id"])
    kinds, _ = knowledge.plan_for(p["iran_id"], p["foreign_id"], p["kind"], False, False, now=now)
    skip = {k for k, at in (_failed(name).get("kinds") or {}).items() if now - int(at) < KIND_SKIP}
    res = better_carrier(kinds, p["kind"], s["kind"], pair, now, allowed=license.tunnel_kind_allowed, skip=skip)
    _BETTER[key] = (now, res)
    return res


def build(name: str, kind: str = "", by_swap: bool = False) -> int:
    """Start the build of the standby of tunnel `name`: a real tunnel of the chosen type on the shifted ports, as a build action (its id). ValueError with the reason when it must not be built now.
    `kind`: the type is asked for. `by_swap`: the call comes from the swap beside the tunnel, which is itself the action that works on the tunnel (its story is not "a repair")."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only: nothing is built")

    v = _view(name)
    p, s = v["p"], v["s"]

    if p is None:
        raise ValueError("tunnel not found")

    if s is not None:
        raise ValueError("this tunnel already has a standby")

    if name in _BUILDS:
        raise ValueError("its standby is being built")

    if p["state"] != "up":
        raise ValueError(f"the tunnel is {p['state']}: a standby is built when it is up")

    if name in autopilot.repairing_names() and not by_swap:
        raise ValueError("a repair works on this tunnel: the standby is built after it")

    if autopilot.storm_now():
        raise ValueError("several servers are off or many tunnels are down: nothing is built now")

    plan = build_plan(name, kind)
    k, tr = tunnel_test.kind_parts(plan["kind"])
    iran_id, foreign_id = p["iran_id"], p["foreign_id"]

    if k == "backhaul":
        opts = {} if tunnel_test.kind_link(plan["kind"]) == "gre" else {"link": tunnel_test.kind_link(plan["kind"])}          # (an automatic build never installs a package: the Doctor's rule)
        aid = builder.start_build(iran_id, foreign_id, plan["ports"], tr, forced_name=plan["name"], opts=opts, standby_of=name)
    else:
        aid = build_other.start_build(iran_id, foreign_id, plan["ports"], k, tr, {}, forced_name=plan["name"], measure="latency", standby_of=name)

    with _LOCK:
        _BUILDS[name] = {"aid": aid, "name": plan["name"], "kind": plan["kind"], "at": int(time.time())}

    _NOTE.pop(name, None)
    log.info("standby: building %s for %s (%s)", plan["name"], name, plan["note"])
    return aid


def _collect() -> None:
    """The builds that ended: a good one clears the failures, a bad one is counted (and its type is not chosen again for a while)."""
    for name, b in list(_BUILDS.items()):
        with session() as db:
            e = db.get(ActionLog, b["aid"])
            done, ok, last = (bool(e.finished_at), bool(e.ok), (list(e.story or []) or [""])[-1]) if e is not None else (True, False, "the build is gone")

        if not done:
            if time.time() - b["at"] > 1800:
                with _LOCK:
                    _BUILDS.pop(name, None)

            continue

        with _LOCK:
            _BUILDS.pop(name, None)

        if ok:
            _set_failed(name, None)
            continue

        f = _failed(name)
        kinds = dict(f.get("kinds") or {})
        kinds[b["kind"]] = int(time.time())
        n = int(f.get("n") or 0) + 1
        _set_failed(name, {"n": n, "at": int(time.time()), "why": str(last)[:200], "kinds": kinds})
        log.info("standby: the build of %s for %s failed (%s): next try in %s s", b["name"], name, str(last)[:120], retry_wait(n))

        if n in (1, 5):
            with session() as db:
                emit(db, "info", "tunnels", f"Standby of {name}: the {b['kind']} standby could not be built ({str(last)[:120]}); it is tried again with another type, the wait grows from {retry_wait(n) // 60} minutes.", name)


def remove_standby(name: str) -> list[str]:
    """Take the standby away: the redirects first (the users go back to the tunnel), then the tunnel itself from both servers. A standby whose redirects cannot be taken off (the Iran server does not answer) is KEPT -
    its boot unit would put them again on a port nobody listens on."""
    with _lock(name):
        s = _view(name)["s"]

        if s is None:
            return []

        from . import lanes

        lanes.release(name, s)              # (a lane sends new connections to the standby's ports: its rules go first - the standby is KEPT when the Iran server does not take them off)

        if s["use"]:
            give_back(name, "the standby is being removed")

        if (s["use"] or s["parked"]) and not s["iran_off"]:
            _rules_off(_node(*s["iran"]), s["name"])            # the rules that were left on purpose by the way back go with the standby

        with session() as db:
            t = db.get(Tunnel, s["id"])

            if t is None:
                return []

            off = any(srv is not None and srv.online is False for srv in (t.iran, t.foreign))

        res = builder.delete_tunnel(s["id"], skip_offline=off)
        _TOLD.discard(name)
        return res


def _restart(s: dict) -> None:
    """The units of a standby restarted (the Iran side first), then one look - without the events of a repair."""
    with session() as db:
        t = db.get(Tunnel, s["id"])

        if t is None:
            return

        plan = tunnels.plan_for(t)
        sides = [(_node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port), plan.iran_units), (_node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port), plan.foreign_units)]

    for node, units in sides:
        ordered = sorted(units, key=lambda u: not u.startswith("nikatel-gre-"))          # (the GRE card first - it carries the rest - then the services on it: a card that was stopped is not started by anything else)

        try:
            run_action(node, "restart_units", {"UNITS": " ".join(ordered)})
        except (SSHError, ValueError) as e:
            log.info("standby: restart of %s failed: %s", s["name"], str(e)[:100])

    time.sleep(6)
    tunnels.check_tunnel(s["id"])


# ------------------------------------------------------------------ the owner's switch
def switch(tunnel_id: int, on: bool) -> dict:
    """Mark a tunnel for a standby (it is built at once when the tunnel is up) or take the mark away (the standby goes; open handovers are given back first)."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only: nothing is built or removed")

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        if is_temp(t.name) or is_standby(t):
            raise ValueError("a temporary or a standby tunnel has no standby")

        name, ports = t.name, t.forwarded_ports or []

    if on:
        if not ports:
            raise ValueError("this tunnel forwards no port, so there is nothing to hand over")

        _mark(name, True)

        try:
            build(name)
        except ValueError as e:
            _NOTE[name] = str(e)             # (the standby job builds it when the reason is gone)

        wake()
    else:
        _mark(name, False, manual=True)
        threading.Thread(target=_safe_cleanup, args=(name,), name=f"standby-off-{name}", daemon=True).start()

    return status(name)


AUTO_EVERY = 120            # the automatic standby marks ONE more tunnel at most this often (and none while a standby is being built): twenty builds at once would load the servers of the owner


def auto_pick(candidates: list[dict], marked: set[str], optout: set[str], building: bool, auto: bool, last: float, now: float, every: int = AUTO_EVERY) -> str:
    """Pure: the name of the ONE tunnel that gets a standby now ('' when none): every real tunnel that is up and forwards a port has one (the owner, 2026-10-19: "always on, by itself - and do not show it"), except those
    the owner switched off by hand, one at a time, the oldest first, only in the modes fix and move, never while the panel is read-only or a standby is being built. `candidates`: {"name", "state", "ports", "created_at"}."""
    if not auto or building or now - last < every:
        return ""

    todo = [c for c in candidates if c["name"] not in marked and c["name"] not in optout and c["state"] == "up" and c["ports"]]
    return min(todo, key=lambda c: (c.get("created_at") or 0, c["name"]))["name"] if todo else ""


def _auto_mark() -> None:
    """The job: give the next tunnel its standby (see `auto_pick`)."""
    d = st.get("standby")

    with session() as db:
        cands = [{"name": t.name, "state": t.state, "ports": list(t.forwarded_ports or []), "created_at": t.created_at}
                 for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name) and not is_standby(t)]

    name = auto_pick(cands, set(d.get("marked") or {}), set(d.get("optout") or {}), bool(_BUILDS), actions.doctor_mode() != "off" and not get_settings().read_only, _STATE.get("auto_at", 0.0), time.time())

    if not name:
        return

    _STATE["auto_at"] = time.time()
    _mark(name, True)
    log.info("standby: %s gets its standby by itself", name)


def forget(name: str) -> None:
    """The owner removed the tunnel: its mark and its standby go with it (in the background; a tunnel that is rebuilt by a repair keeps both - this is only called for a removal the owner asked for)."""
    from . import lanes

    lanes.forget(name)

    if name in _marks() or _view(name)["s"] is not None:
        _mark(name, False)
        threading.Thread(target=_safe_cleanup, args=(name,), name=f"standby-forget-{name}", daemon=True).start()


def _safe_cleanup(name: str) -> None:
    try:
        if name in _BUILDS:
            builder.CANCEL.add(_BUILDS[name]["aid"])

        remove_standby(name)
        _NOTE.pop(name, None)
    except (ValueError, RuntimeError, SSHError) as e:          # (the Iran server does not answer, say: the job tries again at its next turn)
        log.warning("standby: %s could not be removed yet: %s", name, str(e)[:160])
    except Exception:          # the boundary of a thread
        log.exception("standby: the removal of the standby of %s crashed", name)


def wake() -> None:
    try:
        jobs.trigger("standby")
    except KeyError:
        pass                      # not registered (tests)


# ------------------------------------------------------------------ what the tunnel window shows
def _state_of(name: str, marked: bool, v: dict, now: int) -> tuple[str, str]:
    p, s = v["p"], v["s"]

    if not marked and s is None:
        return "off", ""

    if not marked:
        return "removing", "the standby is being removed"

    if s is not None and s["use"]:
        return "in_use", "the users' ports are on the standby" + (" (by hand: until Back now)" if s["use"].get("manual") else "")

    if name in _BUILDS:
        return "building", "the standby is being built"

    if s is not None and s["state"] == "up":
        return "ready", ""

    if s is not None:
        return "mending", s["last_error"] or f"the standby is {s['state']}"

    f = _failed(name)

    if f.get("n") and now < int(f.get("at") or 0) + retry_wait(int(f["n"])):
        return "failed", f"the last build failed ({f.get('why', '')}); the next try is in {int(f['at']) + retry_wait(int(f['n'])) - now} s"

    return "waiting", _NOTE.get(name) or ("the standby is built when the tunnel is up" if p is not None and p["state"] != "up" else "the standby is built in a moment")


def status(name: str) -> dict:
    return summaries([name])[name]


def summaries(names: list[str]) -> dict[str, dict]:
    """{tunnel name: what the tunnel window shows of its standby}: on, state (off · building · ready · in_use · mending · waiting · failed · removing), type, since, why, the shifted ports."""
    marks = _marks()
    now = int(time.time())
    out = {}
    views = _views(list(names))

    for name in names:
        v = views[name]
        s = v["s"]
        state, why = _state_of(name, name in marks, v, now)
        use = (s or {}).get("use") or {}
        out[name] = {"on": name in marks, "state": state, "why": why, "kind": (s or {}).get("kind") or (_BUILDS.get(name) or {}).get("kind", ""), "since": int(use.get("since") or 0) if state == "in_use" else int((s or {}).get("state_since") or 0),
                     "ports": ", ".join(f"{a} → {b}" for a, b in zip((v["p"] or {}).get("ports", []), (s or {}).get("ports", []))), "up": bool(s and s["state"] == "up")}

    return out


# ------------------------------------------------------------------ the job
def _init_once() -> None:
    """After a start of the panel: the tunnels whose ports stand on their standbys are known again."""
    if _STATE["init"]:
        return

    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))):
            if is_standby(t) and (t.record or {}).get("use"):
                tunnels.HANDED.add(str(t.record["standby_of"]))

    _STATE["init"] = True


def _cleanup_unmarked(name: str, v: dict) -> None:
    if name in _BUILDS:
        builder.CANCEL.add(_BUILDS[name]["aid"])

    if v["s"] is not None:
        remove_standby(name)


def _step(name: str) -> None:
    now = int(time.time())
    marked = name in _marks()
    v = _view(name)

    if not marked:
        _cleanup_unmarked(name, v)
        return

    p, s = v["p"], v["s"]

    if p is None:
        since = _MISSING.setdefault(name, now)

        if now - since > MISSING_GRACE:
            _MISSING.pop(name, None)
            _mark(name, False)
            _cleanup_unmarked(name, v)

        return

    _MISSING.pop(name, None)

    if s is not None and time.time() - _CHECKED.get(s["id"], 0) >= CHECK_EVERY and not s["stale"]:
        _CHECKED[s["id"]] = time.time()
        tunnels.check_tunnel(s["id"])
        v = _view(name)
        p, s = v["p"], v["s"]

        if p is None:
            return

    if s is not None and s["state"] == "up" and name not in _TOLD:
        _TOLD.add(name)

        if now - s["created_at"] < 900:                 # (told once, when it was just built - not again at every start of the panel)
            with session() as db:
                emit(db, "ok", "tunnels", f"Standby of {p['label']} is ready ({s['kind']} on the ports {', '.join(str(x) for x in s['ports'][:6])}).", name)

    b = _BUILDS.get(name)

    if b is not None and p["state"] == "down":
        builder.CANCEL.add(b["aid"])          # the tunnel is cut: the servers are the repair's, the standby build gives them up

    mode = actions.doctor_mode()
    auto = mode != "off" and not get_settings().read_only
    f = _failed(name)
    wait_until = int(f.get("at") or 0) + retry_wait(int(f.get("n") or 0)) if f.get("n") else 0
    needs_calm = p["state"] == "up" and (s is None or s["state"] != "up")           # (the look at the whole system is made only when a standby is to be built or mended)
    better = _better_kind(p, s, name) if auto and s is not None and not s["use"] and s["state"] == "up" and p["state"] == "up" else ""          # (a standby that took the carrier of its tunnel after a swap)
    act, why = decide(p, s, now=now, auto=auto, storm=autopilot.storm_now() if needs_calm or better else False, repairing=name in autopilot.repairing_names(), building=name in _BUILDS, wait_until=wait_until,
                      after=after_seconds(), better=better)

    if act == "handover":
        handover(name, why)
    elif act == "back":
        give_back(name, why)
    elif act == "build":
        try:
            build(name)
        except ValueError as e:
            _NOTE[name] = str(e)
    elif act == "drop":
        remove_standby(name)
    elif act == "rebuild":
        log.info("standby: %s is built again (%s)", name, why)
        remove_standby(name)
    elif act == "restart" and s is not None and time.time() - _RESTARTED.get(s["id"], 0) >= (15 if s["use"] else RESTART_EVERY):
        _RESTARTED[s["id"]] = time.time()
        threading.Thread(target=_restart, args=(s,), name=f"standby-restart-{s['name']}", daemon=True).start()


def _safe_step(name: str) -> None:
    try:
        _step(name)
    except (ValueError, RuntimeError, SSHError) as e:
        log.warning("standby: %s: %s", name, str(e)[:200])
    except Exception:
        log.exception("standby: %s crashed", name)


def reconcile() -> None:
    """Every two minutes: what stands on the Iran servers against what the panel believes. A redirect nobody asked for (the row is gone, the handover was given back while the server did not answer) is taken
    off; a handover whose redirects are gone (a flushed firewall) is put again."""
    d = st.get("standby")
    ids = [int(x) for x in d.get("touched") or []]

    if not ids:
        return

    with session() as db:
        servers = {s.id: _node(s.host, s.ssh_user, s.ssh_port) for s in db.scalars(select(Server).where(Server.id.in_(ids), Server.enabled.is_(True), Server.online.is_(True)))}
        wanted: dict[int, dict[str, dict]] = {}

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))):
            use = (t.record or {}).get("use")

            if is_standby(t) and use:
                wanted.setdefault(t.iran_server_id, {})[t.name] = {"use": use, "name": t.name}

    for sid, node in servers.items():
        try:
            have = parse_list(run_script(node, "standby_list", timeout=20))
        except (SSHError, ValueError) as e:
            log.info("standby: the redirects of a server could not be read: %s", str(e)[:100])
            continue

        mine = wanted.get(sid, {})

        for sb_name in have:
            if sb_name not in mine:
                log.warning("standby: redirects of %s stand on a server and nobody asked for them: taken off", sb_name)

                try:
                    _rules_off(node, sb_name)
                except (SSHError, ValueError, RuntimeError) as e:
                    log.warning("standby: could not take them off: %s", str(e)[:120])

        for sb_name, w in mine.items():
            if sb_name not in have:
                u = w["use"]
                log.warning("standby: the redirects of %s are gone from the server: put again", sb_name)

                try:
                    run_action(node, "standby_on", {"NAME": sb_name, "MAP": u["map"], "PROTOS": u["protos"]}, timeout=40)
                except (SSHError, ValueError) as e:
                    log.warning("standby: could not put them again: %s", str(e)[:120])


def run() -> None:
    """The job (every 5 s, and at once when a tunnel is cut): the marked tunnels, one after the other turn - the builds that ended, the look at every standby, the handover, the way back."""
    if jobs.stopping():
        return

    _init_once()
    _apply_renames()
    _collect()

    try:
        _auto_mark()
    except Exception:
        log.exception("standby: automatic mark")

    names = set(_marks())

    with session() as db:
        names |= {str((t.record or {}).get("standby_of")) for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))) if is_standby(t)}

    if names:
        with ThreadPoolExecutor(min(4, len(names))) as ex:
            list(ex.map(_safe_step, sorted(names)))

    if time.time() - _STATE["reconciled"] >= 120:
        _STATE["reconciled"] = time.time()

        try:
            reconcile()
        except Exception:
            log.exception("standby: reconcile")
