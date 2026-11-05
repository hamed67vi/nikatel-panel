"""Failover of an Iran server: its tunnels move to another Iran server and the DNS records that pointed at it - in whichever
account they live (ArvanCloud, another ArvanCloud, Cloudflare) - are pointed at the new one.

The Doctor (services/doctor.py) decides WHEN (its mode "move": a server off for `server_minutes`, an exhausted server, the return
after a server came back); this module knows HOW:
  plan_for(server_id)      the dead-server plan  ·  route_plan(tunnel_id)  the plan for a route problem (only suggested, never run
                           by itself: a poor route or packet loss never moves a server)  ·  both: target, tunnels, DNS, problems
  _work(aid, plan)         the move: when the source server is ON the new tunnels are built and checked first, then the DNS, and
                           the old tunnels live on for the TTL + 5 minutes (make before break); when it is OFF the old tunnels are
                           dropped (their files are purged when the server is back) and the new ones built at once
  restore(log_id)          point ONE changed record back at the address it had before (the tunnels are left where they are)
  undo(plan_id)            the same for every record of one failover
  move_plan(server_id) · move_server(server_id, target_id, expect)
                           the "Move the tunnels…" item of an Iran server's menu (1.69.24): every tunnel of it to the best Iran server that has the ROOM (line quality, resources, tunnels
                           on it after the move) and FREE PORTS (its own tunnels there, and what listens - read over SSH), the DNS records of every account follow; the operator sees
                           the plan and confirms, and the plan is made again at the confirm - it runs only if it is still the same

The target is the Iran server that ranks best for every moved tunnel (ping to the foreign server, free resources, how many tunnels
it already carries, free ports). A record that serves a moved tunnel and a staying one would break the staying one, so such tunnels
are moved too (closure). The DNS is touched only after every tunnel moved. Every record re-pointed is written to `dns_log` (account,
old and new address, which server, when), so the panel can list the changes and put any of them back with one click."""
from __future__ import annotations

import fnmatch
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Config, Panel, Server, Tunnel, is_protected
from . import arvan, autopilot, dnsprov, telegram
from . import settings as st
from .events import emit

log = get_logger("failover")
ENABLED = True       # the kill switch of the whole DNS failover (tests flip it); what really decides is the Doctor's mode (services/doctor.py)
TTL_GRACE = 300      # seconds the old tunnel lives on after the DNS changed, on top of the record's TTL (clients that still hold the old address)
TTL_WAIT_MAX = 900
QUIET_LIMIT = 1800   # after the DNS grace, an old tunnel that still carries the users' traffic is waited for this long; if it is still busy it is KEPT (never cut)
ROUTE_CAUSES = ("gre_blocked", "route_cut", "lossy_route", "port_blocked")
REASONS = {"down": "the server does not answer",
           "resources": "the server's resources are exhausted",
           "return": "the server is back and healthy",
           "manual": "moved by you",
           "capacity": "its capacity was filling",
           "gre_blocked": "GRE is filtered on its route and no other tunnel type works",
           "route_cut": "the route to the foreign server is cut",
           "lossy_route": "the route loses too many packets and no other tunnel type does better",
           "port_blocked": "the tunnel port is blocked on its route and no other tunnel type works",
           "quality": "its speed dropped, no other tunnel type of its own server does better and another Iran server was proved much faster"}
QUALITY_MIN_SCORE = 70     # (1.69.186, the owner, 2026-11-02: "I gave the Doctor permission to change the IP too - anything it likes, only speed, stability and quality must be high") the Iran server a tunnel is moved to for quality ranks at least this
QUALITY_DELAY = 1.1        # ... answers the foreign server in at most this many times the tunnel's own delay ...
QUALITY_LOSS = 1.0         # ... and loses at most this % of the packets
# (1.69.208, the owner, 2026-11-04: "remove the 90-minute rule - when the server itself is the problem and nothing else cures it, change the server quickly; a config must not stay down": a dropped tunnel is looked at for a move at once)
QUALITY_GAP = 600          # one such move every ten minutes for the whole panel (it was an hour: the tunnels of an Iran server whose own line is bad waited in a queue, one an hour)


def conf() -> dict:
    return st.get("arvan")


def has_key() -> bool:
    """The DNS can be touched at all: the kill switch is on, the feature is licensed and at least one DNS account is saved."""
    from . import license

    return ENABLED and license.has("failover") and bool(dnsprov.accounts())


def enabled() -> bool:
    """The failover may move tunnels and re-point records by itself: the Doctor is in mode "move"."""
    from . import doctor

    return doctor.can_move()


def _sleep(seconds: float) -> None:      # tests replace it
    time.sleep(seconds)


def _apply_record(r: dict, old_ip: str, new_ip: str) -> dict:
    """Point one record (of any account) at `new_ip` where it pointed at `old_ip`; returns what the provider gave back (`before` for the restore)."""
    prov = dnsprov.provider(r["account"])
    ips = [new_ip if ip == old_ip else ip for ip in (r.get("ips") or [old_ip])]

    if new_ip not in ips:
        ips = [new_ip]

    return prov.update(r["domain"], str(r["id"]), r.get("name") or "@", ips, int(r.get("ttl") or 120), bool(r.get("cloud")))


def follow_ip_change(aid: int, old_ip: str, new_ip: str, server_name: str) -> list[dict]:
    """An Iran server got a new address: every A record that pointed at the old one follows (written to dns_log, so each can be put back). Only in the Doctor's mode "move"."""
    if not enabled():
        autopilot._say(aid, f"DNS: the Doctor is not in the mode that moves servers - the records that pointed at {old_ip} are NOT changed; point them by hand (Tunnels → CDN).")
        return []

    recs, err = _fetch_records(old_ip)

    if err:
        autopilot._say(aid, f"DNS: {err}")

    if not recs:
        autopilot._say(aid, f"DNS: no record pointed at {old_ip}.")
        return []

    done, s_ = [], _state()
    now = int(time.time())

    for r in recs:
        try:
            res = _apply_record(r, old_ip, new_ip)
            done.append(res)
            s_["dns_log"].append({"id": f"ip-{aid}:{r['id']}", "at": now, "plan_id": f"ip-{aid}", "server": server_name, "target": server_name, "account": r["account"], "domain": r["domain"], "record_id": str(r["id"]),
                                  "fqdn": r["fqdn"], "old": old_ip, "new": new_ip, "before": res.get("before"), "configs": [], "restored": False, "restored_at": None})
            autopilot._say(aid, f"DNS {r['fqdn']} ({r['account_name']}): {old_ip} → {new_ip}")
        except (RuntimeError, ValueError) as e:
            autopilot._say(aid, f"DNS {r['fqdn']}: not changed: {str(e)[:140]}")

    if done:
        _save(s_)
        _quiet(f"🌐 DNS: {len(done)} record(s) of {server_name} now point at its new address {new_ip}.")

    return done


# ------------------------------------------------------------------ the plan
MOVE_MIN_QUALITY = 50     # the "Move the tunnels" button (1.69.24): a server must reach the foreign server at least this well (ping and loss, 0-100 as in detect.auto_iran) ...
MOVE_MIN_RESOURCES = 40   # ... have at least this much CPU / RAM / load room (0-100) ...
MOVE_MAX_TUNNELS = 8      # ... and carry no more than this many tunnels once the moved ones are on it (the ranking spends 12.5 points of "unused" on each: 8 tunnels = none left)


def room_for(c: dict, n: int) -> str:
    """Pure: '' when this ranked Iran server (a row of autopilot.ranking) can take `n` more tunnels, else the sentence that says why not."""
    if c.get("quality", 100) < MOVE_MIN_QUALITY:
        return f"its line to the foreign server is poor (quality {c.get('quality')} of 100, needs {MOVE_MIN_QUALITY})"

    if c.get("resources", 100) < MOVE_MIN_RESOURCES:
        return f"its CPU, RAM and load leave too little room (resources {c.get('resources')} of 100, needs {MOVE_MIN_RESOURCES})"

    have = int(c.get("tunnels") or 0)

    if have + n > MOVE_MAX_TUNNELS:
        return f"it carries {have} tunnels already; {n} more would pass {MOVE_MAX_TUNNELS}"

    return ""


def rank_targets(rankings: dict[int, list[dict]]) -> list[dict]:
    """Pure: {tunnel_id: [candidate servers best first]} -> every server that can carry them ALL, the best total rank first (a tie goes to the one the first tunnel ranks higher)."""
    if not rankings:
        return []

    common = None

    for lst in rankings.values():
        ids = {c["id"] for c in lst}
        common = ids if common is None else common & ids

    if not common:
        return []

    first = next(iter(rankings.values()))

    def total(sid: int) -> tuple[int, int]:
        return sum(next(i for i, c in enumerate(lst) if c["id"] == sid) for lst in rankings.values()), next(i for i, c in enumerate(first) if c["id"] == sid)

    return [next(c for lst in rankings.values() for c in lst if c["id"] == sid) for sid in sorted(common, key=total)]


def pick_target(rankings: dict[int, list[dict]]) -> dict | None:
    """Pure: {tunnel_id: [candidate servers best first]} -> the one server that can carry them all with the best total rank."""
    ranked = rank_targets(rankings)
    return ranked[0] if ranked else None


# ------------------------------------------------------------------ the load spread (1.69.230: the owner, 2026-11-05: "when two or three servers - or half of them - are off at once, start every move together; with only two servers left, balance the load yourself")
RESERVE_COST = 12          # a tunnel that is on its way to an Iran server (a plan that runs now) takes this many points off that server's free resources in the ranking ...
RESERVE_QUALITY = 5        # ... and this many off its line quality (half a step of the ranking), so that the plans of one outage, all started in the same minute, do not pile onto the one server that ranks first
RESERVE_TTL = 1800         # a reservation nobody gave back (a plan that died in the middle) is forgotten after this long
PARALLEL_TUNNELS = 4       # tunnels of one dead server built at the same time
PLAN_THREADS = 6           # dead servers planned at the same time
_RESERVED: dict[str, tuple[int, int, float]] = {}      # plan key -> (Iran server the tunnels go to, how many, when): the tunnels that are on their way
_RESERVE_LOCK = threading.Lock()
_STATE_LOCK = threading.RLock()                         # the failover record (seen, tried, history, dns_log) is read, changed and saved by several plans at once


def held(book: dict | None = None) -> dict[int, int]:
    """The tunnels on their way to each Iran server: {server id: how many}."""
    now = time.time()
    out: dict[int, int] = {}

    for tid, n, at in list((_RESERVED if book is None else book).values()):
        if now - at < RESERVE_TTL:
            out[tid] = out.get(tid, 0) + n

    return out


def with_reserved(rows: list[dict], on_the_way: dict[int, int]) -> list[dict]:
    """Pure: the ranked Iran servers (rows of autopilot.ranking, best first) as they will look once the tunnels on their way have arrived - more tunnels, fewer free resources, a little less quality - ranked again."""
    from .detect import rank_key

    out = []

    for c in rows:
        n = int(on_the_way.get(c["id"], 0))

        if n <= 0 or c.get("score", 0) <= 0:
            out.append(c)
            continue

        res = max(0, int(c.get("resources", 0)) - RESERVE_COST * n)
        quality = max(0, int(c.get("quality", 0)) - RESERVE_QUALITY * n)
        have = int(c.get("tunnels") or 0) + n
        unused = max(0, round(100 - have * 12.5))
        out.append({**c, "tunnels": have, "resources": res, "quality": quality, "unused": unused, "score": max(1, round(quality * 0.4 + res * 0.3 + unused * 0.3)), "reserved": n})

    return sorted(out, key=rank_key)


def release(p: dict) -> None:
    """Give back what the plan reserved on its target (once; a plan that reserved nothing is left alone)."""
    key = p.pop("reserved", None)

    if key:
        with _RESERVE_LOCK:
            _RESERVED.pop(key, None)


def assign_target(p: dict, book: dict | None = None) -> dict:
    """Phase two of a dead server's plan, one plan at a time and at once: the target is the server that can carry every tunnel with the best rank AFTER the tunnels already on their way (the other dead servers' plans of this minute)
    are counted, and the plan reserves its own tunnels there. A plan that has its target already (or none to pick from) is left as it is."""
    rankings = p.pop("_rankings", None)

    if rankings is None:
        return p

    with _RESERVE_LOCK:
        way = held(book)
        target = pick_target({tid: with_reserved(lst, way) for tid, lst in rankings.items()}) if rankings else None
        moving = len(p["tunnels"])

        if target and moving:
            key = f"{p['server_id']}-{time.time_ns()}"
            (_RESERVED if book is None else book)[key] = (target["id"], moving, time.time())

            if book is None:
                p["reserved"] = key

    if target:
        with session() as db:
            row = db.get(Server, target["id"])
            p["target"] = {"id": target["id"], "name": target.get("name"), "ip": row.host if row is not None else None}
    elif moving:
        p["problems"].append("no other Iran server can carry all of these tunnels")

    return p


def closure(move: set[int], recs: list[dict]) -> set[int]:
    """Pure: the tunnels to move once the DNS is kept consistent. A record that serves a moved tunnel and a staying one would
    break the staying one, so it is moved as well - until nothing more is pulled in."""
    move = set(move)
    grew = True

    while grew:
        grew = False

        for r in recs:
            ts = set(r.get("tunnels") or [])

            if ts & move and not ts <= move:
                move |= ts
                grew = True

    return move


def _tunnels_of(server_id: int) -> list[dict]:
    with session() as db:
        return [{"id": t.id, "name": t.name, "state": t.state, "ports": {int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()}, "protected": is_protected(t)}
                for t in db.scalars(select(Tunnel).where(Tunnel.iran_server_id == server_id, Tunnel.enabled.is_(True)))]


def _fetch_records(ip: str) -> tuple[list[dict], str]:
    """The A records of EVERY DNS account (ArvanCloud, other ArvanCloud accounts, Cloudflare) that carry `ip`, each with the account it lives in
    (each record is later changed in its own account). Tests replace this."""
    recs, errors = [], []

    for a in dnsprov.accounts():
        prov = dnsprov.provider_of(a)

        try:
            for d in prov.domains():
                for r in prov.records(d):
                    if r.get("type") == "A" and ip in (r.get("ips") or []):
                        recs.append({"account": a.id, "account_name": a.name, "provider": a.provider, "domain": d, "id": str(r["id"]), "name": r.get("name") or "@", "fqdn": r["fqdn"], "ip": ip,
                                     "ips": list(r.get("ips") or []), "ttl": r.get("ttl"), "cloud": bool(r.get("cloud")), "port": r.get("port")})
        except (RuntimeError, ValueError) as e:
            errors.append(f"{a.name}: {str(e)[:120]}")

    return recs, "; ".join(errors)


_HOSTS_LAST: dict[int, dict[str, set[int]]] = {}      # the last reading of the hosts of each PasarGuard panel: a panel that is down for a minute must not turn a move into a guess


def _hosts_by_name(rows: list[dict]) -> dict[str, set[int]]:
    """Pure: {name: {port}} - the names the hosts of a PasarGuard panel dial and the port each name is dialled on. A host with no port of its own (or a range) says nothing about a tunnel and is skipped;
    a disabled host stays (it may be switched on again, and its record must not be left behind)."""
    from . import cfgmap

    out: dict[str, set[int]] = {}

    for h in rows:
        port = cfgmap.port_of(h.get("port"))

        if port is None:
            continue

        for a in cfgmap._strs(h.get("address")):
            out.setdefault(a.lower(), set()).add(port)

    return out


def _host_ports() -> tuple[dict[str, set[int]], str]:
    """{name: {port}} of the hosts of every PasarGuard panel (one read-only question each); the second value says which panel could not be read and had no earlier reading. Tests replace this."""
    from . import panels, pg_cores

    with session() as db:
        found = [(p.id, p.name) for p in db.scalars(select(Panel).where(Panel.type == "pasarguard", Panel.enabled.is_(True)))]

    out: dict[str, set[int]] = {}
    errors = []

    for pid, pname in found:
        try:
            got = _hosts_by_name(pg_cores._as_list(panels.call(panels.cred_for(pid), "GET", "/api/hosts"), "hosts"))
            _HOSTS_LAST[pid] = got
        except (ValueError, RuntimeError) as e:                                  # (PanelError is a RuntimeError)
            got = _HOSTS_LAST.get(pid)

            if got is None:
                errors.append(f"the hosts of the PasarGuard panel {pname} could not be read ({str(e)[:100]})")
                continue

        for n, ps in got.items():
            out.setdefault(n, set()).update(ps)

    return out, "; ".join(errors)


def _records(ip: str, tunnels: list[dict], hosts: bool = True) -> tuple[list[dict], str]:
    """The records that carry `ip`, each with the configs that use its name and the tunnels (of this server) they go through - by a probe config that names the record, by the origin port of a CDN record,
    and by a host of a PasarGuard panel that dials the name on the port of a tunnel (the owner, 2026-11-03: the US config died after its tunnel moved: its TLS host lives in the panel, not in the probe
    configs, and its record has no origin port, so the record was tied to no tunnel and stayed on the old server). `hosts=False` leaves the panels alone (a whole-server move takes every record anyway)."""
    if not dnsprov.accounts():
        return [], ""

    with session() as db:
        configs = [(c.name, str((c.meta or {}).get("address", "")).lower(), int((c.meta or {}).get("port") or 0)) for c in db.scalars(select(Config))]

    recs, err = _fetch_records(ip)
    dialled: dict[str, set[int]] = {}

    if hosts and recs:
        dialled, herr = _host_ports()
        err = "; ".join(x for x in (err, herr) if x)

    named = {x["fqdn"].lower() for x in recs if "*" not in x["fqdn"]}

    for r in recs:
        mine = [(n, p) for n, a, p in configs if a == r["fqdn"].lower()]
        r["configs"] = [n for n, _ in mine]
        by_config = {t["id"] for t in tunnels if any(p in t["ports"] for _, p in mine)}
        by_port = {t["id"] for t in tunnels if r.get("port") and int(r["port"]) in t["ports"]}       # a CDN record whose origin port is the port of a tunnel belongs to that tunnel (found by the real test: without this a record no probe config names was left on the old server)
        fq = r["fqdn"].lower()
        ports = set().union(*(ps for n, ps in dialled.items() if n == fq or ("*" in fq and n not in named and fnmatch.fnmatchcase(n, fq))))        # (a wildcard record serves the names under it that have no record of their own)
        by_host = {t["id"] for t in tunnels if ports & t["ports"]}
        r["tunnels"] = sorted(by_config | by_port | by_host)

    return recs, err


def _live_busy(server_id: int, ports: set[int]) -> list[int]:
    """Which of `ports` something already listens on (TCP) at this Iran server - read-only over SSH. Tests replace it."""
    from ..ssh import Node, run_script
    from . import build_other

    with session() as db:
        s = db.get(Server, server_id)
        node = Node(s.host, s.ssh_user, s.ssh_port)

    return sorted(ports & build_other.parse_simple_facts(run_script(node, "simple_facts", timeout=45))["tcp"])


def _ports_problem(server_id: int, ports: set[int]) -> str:
    """'' when every port of `ports` is free on this Iran server: no tunnel of the panel forwards it there AND nothing listens on it (looked at over SSH); else the sentence that says why not.
    A server that cannot be looked at counts as not usable: a move must not start on a guess."""
    if not ports:
        return ""

    with session() as db:
        used = {}

        for t in db.scalars(select(Tunnel).where(Tunnel.iran_server_id == server_id)):
            for x in t.forwarded_ports or []:
                if str(x).isdigit() and int(x) in ports:
                    used[int(x)] = t.name

    if used:
        return "port " + ", ".join(f"{p} (tunnel {n})" for p, n in sorted(used.items())) + " is used there"

    try:
        busy = _live_busy(server_id, ports)
    except Exception as e:      # noqa: BLE001 - SSH, a missing script, a bad answer: whatever it was, the port is not proved free
        return f"its listening ports could not be read ({str(e)[:80]})"

    return ("port " + ", ".join(str(p) for p in busy) + " already has a program listening on it") if busy else ""


def _make_plan(server_id: int, move: set[int] | None, reason: str, target_id: int | None = None, strict: bool = False, defer: bool = False, exclude: frozenset = frozenset(), dns: bool = True) -> dict:
    """move=None: every tunnel of the server (it is dead). Otherwise the given tunnels, widened so that the DNS stays consistent.
    `target_id`: the Iran server the operator (or the return) chose; else the one that ranks best for every moved tunnel - with the tunnels that are already on their way to a server counted against it (`with_reserved`).
    `defer` (the plans of one outage): the tunnels are ranked but the target is NOT picked - `assign_target` does it, one plan at a time, so that plans made at the same moment do not all choose the same server.
    `exclude`: Iran servers that are not candidates (a simulation of servers that are off); `dns=False`: the DNS accounts are not asked (a simulation).
    `strict` (the "Move the tunnels" button): only a server with room is a target - `room_for` (line quality, resources, tunnels on it after the move) - and every port of the moved tunnels is proved free on it
    (the panel's own tunnels there AND what listens on the server); the plan also lists the best three targets (`candidates`) and the servers left out with the reason (`skipped`)."""
    with session() as db:
        s = db.get(Server, server_id)

        if s is None or s.role != "iran":
            raise ValueError("choose an Iran server")

        info = {"server_id": s.id, "server": s.name, "ip": s.host}

    tunnels = _tunnels_of(server_id)
    recs, dns_error = _records(info["ip"], tunnels, hosts=move is not None) if dns else ([], "")       # (a whole-server move takes every record: the panels are not asked)
    all_ids = {t["id"] for t in tunnels}
    fixed = {t["id"] for t in tunnels if t.get("protected")}          # (1.69.221: a protected tunnel stays where it is, whatever the plan - and a DNS record it shares with a moved tunnel is left alone, as one that serves a tunnel that stays)
    asked = (all_ids if move is None else set(move) & all_ids) - fixed
    moved = all_ids - fixed if move is None else closure(asked, recs) - fixed
    whole = moved == all_ids
    kept = [t["name"] for t in tunnels if t["id"] in fixed and (move is None or t["id"] in move)]
    moving = [t for t in tunnels if t["id"] in moved]
    dns = [r for r in recs if whole or (r["tunnels"] and set(r["tunnels"]) <= moved)]
    left = [] if whole else [{"fqdn": r["fqdn"], "configs": r["configs"], "tunnels": r["tunnels"]} for r in recs if r not in dns]
    rankings, problems, skipped, candidates = {}, [], {}, []
    ports = set().union(*(t["ports"] for t in moving)) if moving else set()

    target = None

    if target_id:
        with session() as db:
            ts = db.get(Server, target_id)

            if ts is None or ts.role != "iran" or not ts.enabled or ts.online is False:
                raise ValueError("the target must be an Iran server that is on")

            if ts.id == server_id:
                raise ValueError("choose another Iran server")

            target = {"id": ts.id, "name": ts.name}

    if strict or not target_id:
        def rank_one(t: dict) -> tuple[list[dict] | None, str]:
            try:
                return [r for r in autopilot.ranking(t["id"]) if r.get("online", True) is not False and r.get("score", 0) > 0 and r["id"] not in exclude], ""
            except (ValueError, RuntimeError) as e:
                return None, f"{t['name']}: {str(e)[:120]}"

        with ThreadPoolExecutor(max(1, min(PARALLEL_TUNNELS, len(moving)))) as ex:          # (every tunnel is ranked by pinging its foreign server from each Iran server: a dead server of four tunnels waited four rounds of that, one after the other)
            for t, (lst, err) in zip(moving, ex.map(rank_one, moving)):
                if lst is None:
                    problems.append(err)
                else:
                    rankings[t["id"]] = lst

    if not strict:
        if not target_id and not defer:
            way = held()
            target = pick_target({tid: with_reserved(lst, way) for tid, lst in rankings.items()}) if rankings else None
    else:                                   # an explicit target (the one the operator confirmed) is judged by the same rules as an automatic one: the world may have changed since the plan was shown
        if len(rankings) < len(moving):
            rankings = {}                   # one tunnel could not be ranked: no server is proved to carry all of them

        way = held()
        rankings = {tid: with_reserved(lst, way) for tid, lst in rankings.items()}

        for lst in rankings.values():
            for c in lst:
                why = room_for(c, len(moving))

                if why:
                    skipped.setdefault(c["id"], {"name": c["name"], "why": why})

        ranked = rank_targets({tid: [c for c in lst if c["id"] not in skipped] for tid, lst in rankings.items()})

        if target:
            if not any(c["id"] == target["id"] for c in ranked):
                problems.append(f"{target['name']}: " + (skipped.get(target["id"]) or {}).get("why", "it is not one of the servers that can carry all of these tunnels"))

            ranked = [c for c in ranked if c["id"] == target["id"]]

        for c in ranked[:5]:
            bad = _ports_problem(c["id"], ports)

            if bad:
                skipped[c["id"]] = {"name": c["name"], "why": bad}
            elif len(candidates) < 3:
                candidates.append(c)

        target = candidates[0] if candidates else None
    if moving and not target and not defer:
        problems.append("no other Iran server can carry all of these tunnels" + (" (" + "; ".join(f"{v['name']}: {v['why']}" for v in skipped.values())[:300] + ")" if skipped else ""))

    if dns_error:
        problems.append(dns_error)

    new_ip = None

    with session() as db:
        hosts = {x.id: x.host for x in db.scalars(select(Server).where(Server.id.in_([c["id"] for c in candidates] + ([target["id"]] if target else []))))}

    if target:
        new_ip = hosts.get(target["id"])

    out = {**info, "reason": reason, "why": REASONS.get(reason, reason), "whole": whole,
           "tunnels": [{"id": t["id"], "name": t["name"]} for t in moving], "move": sorted(moved),
           "pulled": [t["name"] for t in moving if t["id"] not in asked],
           "target": ({"id": target["id"], "name": target.get("name"), "ip": new_ip} if target else None),
           "dns": [{k: v for k, v in r.items() if k != "tunnels"} for r in dns], "left_alone": left,
           "dns_error": dns_error, "problems": problems, "made_at": int(time.time())}

    if kept:
        out["protected"] = kept

    if defer:
        out["_rankings"] = rankings

    if strict:
        out["candidates"] = [{"id": c["id"], "name": c["name"], "ip": hosts.get(c["id"]), "reason": c.get("reason", ""), "quality": c.get("quality"), "resources": c.get("resources"), "tunnels": c.get("tunnels")} for c in candidates]
        out["skipped"] = list(skipped.values())

    return out


def plan_for(server_id: int) -> dict:
    """What a failover of a dead Iran server would do: every tunnel, every record that carries its address."""
    return _make_plan(server_id, None, "down")


def plan_dead(server_id: int) -> dict:
    """Phase one of the plan of a dead server in an outage (the Doctor makes it for every dead server at the same time): all of `plan_for` except the target, which `assign_target` picks next, one plan at a time."""
    return _make_plan(server_id, None, "down", defer=True)


def simulate(off_ids: list[int]) -> dict:
    """What the Doctor would do if these Iran servers were all off at the same moment (1.69.230; read-only: nothing is built, no DNS account is asked, nothing is saved). The servers are taken busiest first, as in a real outage, and each plan sees the
    tunnels the plans before it sent to a server (`assign_target`): the answer shows how the load would be spread, which dead server finds no room, and how many tunnels each server that is left would carry at the end."""
    off = {int(x) for x in off_ids}

    with session() as db:
        iran = {x.id: (x.name, x.online is not False) for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True)))}
        load: dict[int, int] = {}

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            load[t.iran_server_id] = load.get(t.iran_server_id, 0) + 1

    book: dict = {}
    rows, after = [], {sid: load.get(sid, 0) for sid, (_, on) in iran.items() if on and sid not in off}

    for sid in sorted(off & set(iran), key=lambda x: -load.get(x, 0)):
        if not load.get(sid):
            rows.append({"server": iran[sid][0], "tunnels": [], "target": None, "problems": []})
            continue

        try:
            p = assign_target(_make_plan(sid, None, "down", defer=True, exclude=frozenset(off), dns=False), book)
        except (ValueError, RuntimeError) as e:
            rows.append({"server": iran[sid][0], "tunnels": [f"{load[sid]} tunnel(s)"], "target": None, "problems": [str(e)[:160]]})
            continue

        tid = (p.get("target") or {}).get("id")

        if tid:
            after[tid] = after.get(tid, 0) + len(p["tunnels"])

        rows.append({"server": iran[sid][0], "tunnels": [t["name"] for t in p["tunnels"]], "target": (p.get("target") or {}).get("name"), "problems": p["problems"]})

    return {"off": sorted(iran[x][0] for x in off & set(iran)), "moves": rows, "load_after": {iran[sid][0]: n for sid, n in sorted(after.items())}, "stuck": [r["server"] for r in rows if r["tunnels"] and not r["target"]]}


def route_plan(tunnel_id: int, cause: str = "route_cut") -> dict:
    """What a failover for a route problem would do: this tunnel and the other broken tunnels of its Iran server, with the records
    that serve only them."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        sid = t.iran_server_id

    broken = {x["id"] for x in _tunnels_of(sid) if x["state"] != "up"} | {tunnel_id}
    return _make_plan(sid, broken, cause if cause in REASONS else "route_cut")


def move_blocked() -> str:
    """'' when the "Move the tunnels" button may run, else the sentence that says why not (the move changes DNS records, so it needs the failover license and a DNS account)."""
    from . import license

    if not ENABLED:
        return "the DNS failover is switched off"

    if not license.has("failover"):
        return "moving tunnels together with their DNS records needs the failover license (Settings → About → License)"

    if get_settings().read_only:
        return "the panel is read-only"

    if not dnsprov.accounts():
        return "no DNS account is saved (Tunnels → CDN → Add account): the DNS records could not follow the tunnels"

    return ""


def server_of(tunnel_id: int) -> int:
    """The Iran server a tunnel runs on."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        return t.iran_server_id


def move_plan(server_id: int, target_id: int | None = None, tunnels: list[int] | None = None) -> dict:
    """What the "Move…" button would do (nothing is changed): the tunnels - EVERY tunnel of the Iran server, or only `tunnels` (the one of the tunnel menu, plus the ones that share a DNS record with it) -
    go to the best Iran server that has the room (`room_for`) and free ports, and every DNS record that serves only the moved tunnels - in whichever account - follows. `blocked` says why the button
    cannot run (license, DNS account, read-only), `candidates` are the best targets."""
    p = _make_plan(server_id, set(tunnels) if tunnels else None, "manual", target_id=target_id, strict=True)
    p["blocked"] = move_blocked()
    return p


def move_server(server_id: int, target_id: int, expect: list[int] | None = None, tunnels: list[int] | None = None) -> int:
    """Run the move the operator confirmed: the plan is made again NOW (the world may have changed since the confirm), and it runs only when it is still the same - the same tunnels, the target still
    has room and free ports. The new tunnels are built and checked first, the DNS follows, the old ones go after the TTL (`_work`). Returns the repair story id."""
    why = move_blocked()

    if why:
        raise ValueError(why)

    p = move_plan(server_id, target_id, tunnels)

    if not p["tunnels"]:
        if p.get("protected"):
            raise ValueError(f"{', '.join(p['protected'])} {'is' if len(p['protected']) == 1 else 'are'} protected: never moved. Turn the protection off in the tunnel's menu first")

        raise ValueError(f"{p['server']} carries no tunnel: nothing to move" if not tunnels else f"this tunnel is not an enabled tunnel of {p['server']}: nothing to move")

    if not p["target"]:
        raise ValueError("no Iran server can take these tunnels now: " + "; ".join(p["problems"])[:300])

    if p["dns_error"]:
        raise ValueError(f"a DNS account did not answer or the hosts of a panel could not be read ({p['dns_error'][:200]}): the records could not be matched to the tunnels, so they could not follow them - fix the account (Tunnels → CDN) or the panel and try again")

    if expect is not None and set(expect) != set(p["move"]):
        raise ValueError("the tunnels of this server changed since you looked: look at the plan again")

    p["id"] = f"{server_id}-{int(time.time())}"
    return run_plan(p)


# ------------------------------------------------------------------ state: the pending plans, history, DNS changes, who has been offline since when
def _state() -> dict:
    s = st.get("failover")
    s.setdefault("seen", {})
    s.setdefault("pending", [])
    s.setdefault("history", [])
    s.setdefault("dns_log", [])
    s.setdefault("tried", {})
    return s


def _save(s: dict) -> None:
    st.put("failover", {"seen": s["seen"], "pending": s["pending"], "history": s["history"][-30:], "dns_log": s["dns_log"][-100:], "tried": s.get("tried") or {}})


def state() -> dict:
    from . import doctor

    s = _state()
    return {"mode": doctor.mode(), "can_move": doctor.can_move(), "confirm_minutes": int(st.get("repair").get("server_minutes", 10)), "pending": s["pending"], "history": list(reversed(s["history"]))[:15],
            "dns_log": list(reversed(s["dns_log"]))[:50]}


def _notice(text: str) -> None:
    try:
        telegram.send_text(text)
    except Exception:   # a Telegram problem must never stop a failover
        log.warning("failover notice not sent", exc_info=True)


def _quiet(text: str) -> None:
    """What was done with DNS records - by hand in the panel, put back, or moved after an outage - is kept in the DNS log and the Events page, and NOT sent to Telegram: only outages go there (the owner, 2026-10-23:
    sixteen "edit by hand" messages in a row after he moved the records of a domain to a new address)."""
    log.info("DNS change (not sent to Telegram): %s", text[:200])


def _describe(p: dict) -> str:
    names = ", ".join(t["name"] for t in p["tunnels"]) or "no tunnel"
    tgt = p.get("target")
    return (f"move {len(p['tunnels'])} tunnel(s) ({names}) to {tgt['name'] if tgt else '—'} and point {len(p['dns'])} DNS record(s)"
            f" ({', '.join(d['fqdn'] for d in p['dns']) or 'none'}) at {tgt['ip'] if tgt else '—'}")


def _notify_plan(p: dict, headline: str) -> None:
    _notice(f"🔶 {headline}\nPlan: {_describe(p)}." + (f"\nAlso moved to keep the DNS consistent: {', '.join(p['pulled'])}." if p.get("pulled") else "")
            + (f"\nProblems: {'; '.join(p['problems'])}" if p.get("problems") else ""))


def _busy(s: dict, sid: int, now: int) -> bool:
    """A failover of this server ran in the last 6 hours (or a plan still waits) - unless its tunnels have gone BACK to it since (1.69.230: a server that came back, got its tunnels again and went off a second time was not replaced for six hours)."""
    if any(p["server_id"] == sid and p.get("reason") != "replace" for p in s["pending"]):
        return True

    last = max((h["at"] for h in s["history"] if h["server_id"] == sid and h.get("reason") == "down" and now - h["at"] < 6 * 3600), default=0)
    back = max((h["at"] for h in s["history"] if h.get("reason") == "return" and (h.get("target") or {}).get("id") == sid), default=0)
    return bool(last) and back < last


RETRY_NO_TARGET = 120     # a dead server whose tunnels found no target is looked at again after this long (it was ten minutes: with half the servers off, the first plans leave a server with room later)
CUT_WAIT = 90             # an Iran server that is ON but cut from abroad (the pulse: "the panel's host cannot connect to it, but it is on") whose tunnels are half down or more is replaced after this many seconds ...
CUT_SHARE = 0.5           # ... (the owner, 2026-11-04: "when the routes of the Iran servers are closed the Doctor must replace one quickly - uptime matters to us"; a server that is off waits `server_minutes`)
CUT_WHY = "the server is cut from abroad (it is on, only Iran sees it) and its tunnels are down"
OFF_WAIT = 60             # an Iran server that is OFF (the pulse: two misses in a row, and nobody inside Iran sees it) is replaced after this many seconds (the owner, 2026-11-04: "when the Doctor sees a server is off it must replace it at once"); `server_minutes` now only counts when the panel cannot be sure ...
BLIND_MIN = 4             # ... that is, when this many servers - and BLIND_SHARE of all of them - are off at the same moment: that is the panel's own network, not their fault, and the old waiting time stands
BLIND_SHARE = 0.6


def is_cut(server) -> bool:
    """An Iran server that is on but cut from abroad: the pulse's note says the panel's host cannot connect to it although somebody inside Iran sees it. Its tunnels that the foreign servers dial into are down."""
    from . import pulse

    return getattr(server, "online", None) is True and str(getattr(server, "last_error", "") or "").startswith(pulse.IRAN_ONLY)


def panel_blind(total: int, off: int) -> bool:
    """Pure: is the panel itself the one that cannot see? `off` of the `total` servers are off at the same moment - when that is most of them (and at least BLIND_MIN) it is the panel's own network."""
    return total > 0 and off >= BLIND_MIN and off / total >= BLIND_SHARE


def off_wait(configured: int, blind: bool) -> int:
    """Pure: how long an Iran server that is off waits for its tunnels to be moved: OFF_WAIT at most (a smaller `server_minutes` of the owner still counts), the full `server_minutes` when the panel is blind."""
    return int(configured) if blind else min(int(configured), OFF_WAIT)


# ------------------------------------------------------------------ the job: dead servers (run by the Doctor in mode "move")
_CHECKING = threading.Lock()


FOLLOW_AFTER = 240      # the configs that ride a moved name are looked at this long after its record changed (the TTL is 2 minutes) ...
FOLLOW_AGAIN = 300      # ... and once more after this long when some were still down
_FOLLOW: list[dict] = []
_FOLLOW_LOCK = threading.Lock()


def _follow_later(p: dict, done_dns: list[dict]) -> None:
    """Remember the names a failover moved: `follow_up` looks at the configs behind them."""
    names = [str(d.get("fqdn") or "").lower() for d in done_dns if d.get("fqdn")]

    if names and p.get("target"):
        with _FOLLOW_LOCK:
            _FOLLOW.append({"due": time.time() + FOLLOW_AFTER, "server": p["server"], "target": p["target"]["name"], "names": names, "looks": 0})


def follow_up(now: float | None = None) -> None:
    """The result of a failover (1.69.230, the owner: "the Doctor must always keep its eye on the configs"): some minutes after the DNS records of a dead server moved, the configs whose address is one of those names are looked at -
    how many connect, which do not - and an event says so (Telegram too when some are down). Some still down: one more look later. It changes nothing; a config that stays down is the DNS heal's, the tunnel checks' and the repair's."""
    now = time.time() if now is None else now

    with _FOLLOW_LOCK:
        due = [f for f in _FOLLOW if f["due"] <= now]

        for f in due:
            _FOLLOW.remove(f)

    for f in due:
        with session() as db:
            rows = [(c.name, c.status) for c in db.scalars(select(Config)) if str((c.meta or {}).get("address", "")).lower() in f["names"]]

        if not rows:
            continue                                   # (no config rides these names: nothing to say)

        down = sorted(n for n, status in rows if status == "down")
        text = f"After the move of {f['server']} → {f['target']}: {len(rows) - len(down)} of {len(rows)} configs behind {', '.join(f['names'][:4])}{'…' if len(f['names']) > 4 else ''} connect" + (f"; still down: {', '.join(down[:8])}" if down else "") + "."

        with session() as db:
            emit(db, "warn" if down else "ok", "actions", f"Doctor: {text}", f["server"])

        if down:
            _notice("⚠ " + text + (" The Doctor looks again in 5 minutes." if f["looks"] < 1 else " Look at them: the tunnels, the node and the DNS name."))

            if f["looks"] < 1:
                with _FOLLOW_LOCK:
                    _FOLLOW.append({**f, "looks": f["looks"] + 1, "due": now + FOLLOW_AGAIN})


def tick() -> None:
    """The job of its own (every 10 seconds, 1.69.230): the dead servers are looked at without waiting for the Doctor's round of a minute - a server that is off is replaced one minute after the pulse says so, not one to two;
    and the result of a failover that ended is looked at (read-only, whatever the Doctor's mode)."""
    from . import doctor

    try:
        follow_up()
    except Exception:
        log.exception("failover: the look at the configs after a move")

    if doctor.mode() == "off" or not doctor.can_move():
        return

    check()


def check() -> None:
    """Every Iran server that has been off for OFF_WAIT seconds (`server_minutes` when the panel is blind: most servers off at once) gets its tunnels moved - ALL of them at the same moment (1.69.230, the owner, 2026-11-05: "two or three servers
    off together, or half of them: after a minute start every move at once, not one by one"): the plans are made at the same time, the targets are picked one plan after the other with the tunnels already on their way counted against a server
    (the load is spread, and with two servers left both carry their share), and every plan runs in a story of its own, its tunnels at the same time too."""
    if not enabled() or get_settings().read_only:
        return

    if not _CHECKING.acquire(blocking=False):
        return                                 # another round is planning right now

    try:
        _check()
    finally:
        _CHECKING.release()


def _check() -> None:
    now = int(time.time())
    configured = int(st.get("repair").get("server_minutes", 10)) * 60

    with session() as db:
        down = {x.id: x.name for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(False)))}
        everyone = [x.online for x in db.scalars(select(Server).where(Server.enabled.is_(True)))]
        blind = panel_blind(len(everyone), sum(1 for o in everyone if o is False))
        load, bad = {}, {}

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            load[t.iran_server_id] = load.get(t.iran_server_id, 0) + 1

            if t.state != "up":
                bad[t.iran_server_id] = bad.get(t.iran_server_id, 0) + 1

        cut = {x.id: x.name for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)))
               if is_cut(x) and load.get(x.id) and bad.get(x.id, 0) / load[x.id] >= CUT_SHARE}          # (on, cut from abroad AND its tunnels are down: a server only the panel cannot reach, whose tunnels carry, is left alone)
        down.update(cut)

    wait = off_wait(configured, blind)
    due: list[tuple[int, str, int]] = []       # the servers whose waiting time is over, the one with the most tunnels first

    with _STATE_LOCK:
        s = _state()
        stale = [sid for sid in s["seen"] if int(sid) not in down]

        for sid in stale:
            s["seen"].pop(sid)                 # it answered again: the clock starts over
            s.setdefault("tried", {}).pop(sid, None)

        changed = bool(stale)                  # a forgotten entry must be saved too, or the next outage would skip the waiting time

        for sid, name in sorted(down.items(), key=lambda kv: -load.get(kv[0], 0)):
            since = s["seen"].setdefault(str(sid), now)
            changed = changed or since == now

            if load.get(sid) and now - since < (CUT_WAIT if sid in cut else wait) + 120 and not _busy(s, sid, now):
                autopilot.mass_note(180)       # (a server that carries tunnels is off: from the first look the Doctor's own affairs wait - surveys, speed tests, sweeps, updates - and the configs come first)

            if now - since < (CUT_WAIT if sid in cut else wait) or _busy(s, sid, now) or not load.get(sid) or now - int(s.setdefault("tried", {}).get(str(sid), 0)) < RETRY_NO_TARGET:
                continue

            due.append((sid, name, since))

        if changed:
            _save(s)

    if not due:
        return

    def plan_one(item: tuple[int, str, int]) -> dict | None:
        try:
            return plan_dead(item[0])
        except (ValueError, RuntimeError):
            log.warning("failover plan for %s failed", item[1], exc_info=True)
            return None

    with ThreadPoolExecutor(max(1, min(PLAN_THREADS, len(due)))) as ex:          # (the slow part - every tunnel's foreign server is pinged from each Iran server - is done for all of them at the same time)
        plans = list(ex.map(plan_one, due))

    for (sid, name, since), p in zip(due, plans):
        if p is None:
            continue

        p = assign_target(p)                   # (instant: one plan after the other, so that the second one sees what the first one took)
        p["id"] = f"{sid}-{now}"
        p["down_since"] = since
        lead = f"is cut from abroad (on, but only Iran sees it) and its tunnels have been down for {(now - since) // 60} min" if sid in cut else f"has been off for {(now - since) // 60} min"

        if sid in cut:
            p["why"] = CUT_WHY

        with _STATE_LOCK:
            s = _state()
            s.setdefault("tried", {})[str(sid)] = now
            _save(s)

        if not p["target"]:
            _notice(f"⚠ Iran server {name} {lead}, but no healthy Iran server can carry its {len(p['tunnels'])} tunnel(s) right now" + (f" ({'; '.join(p['problems'])})" if p["problems"] else "")
                    + f". The Doctor tries again in {RETRY_NO_TARGET // 60} minutes.")

            with session() as db:
                emit(db, "bad", "actions", f"Doctor: {name} is off, no Iran server can take its tunnels yet; trying again in {RETRY_NO_TARGET // 60} minutes.", name)

            continue

        _notice(f"🔶 Iran server {name} {lead}: " + _describe(p) + "." + (f" Also moved to keep the DNS consistent: {', '.join(p['pulled'])}." if p.get("pulled") else ""))

        try:
            run_plan(p)                        # (every server in its own story, started now; the story writes the state itself when it is done)
        except Exception:                      # (one server that cannot start must not hold the others back)
            release(p)
            log.exception("failover of %s could not start", name)


# ------------------------------------------------------------------ doing it
def _temp_name(name: str, slug: str = "") -> str:
    """The name the new tunnel is built under while the old one still runs (same shape a tunnel name always has). A tunnel that was moved before keeps the server-side name it was
    built with (renaming changes only the panel), so it may already be called <name>-mv on the servers: the new one then takes -mw, or the two would share a GRE interface and a
    unit on the foreign server and the move would break the tunnel it replaces (found by the real test of Return now)."""
    base = name[:11] if len(name) > 11 else name
    return base + ("-mw" if str(slug or name).endswith("-mv") else "-mv")


def _leftover(snap: dict, server_id: int) -> dict:
    """What a tunnel left on an Iran server that was off when the tunnel was moved away: enough to purge it when the server is back."""
    rec = snap.get("record") or {}
    other = (snap.get("type") or "").lower() in ("backpack", "paqet", "chisel")
    return {"server_id": server_id, "name": snap["name"], "slug": str(rec.get("slug") or snap["name"]), "port": int(rec.get("port") or snap.get("port") or 0), "transport": (snap.get("transport") or "tcpmux"),
            "other": other, "type": snap.get("type") or "", "proto": "udp" if rec.get("udp_carrier") else "tcp"}


PICK_BEST = True     # the type of a moved tunnel is chosen by a test on the new pair (tests switch it off where they do not test that)


def _test_pair(aid: int, snap: dict, target: dict, old: str, first_ok: bool) -> list[dict]:
    """Test the tunnel types between the TARGET Iran server and the foreign server (`autopilot._recommend_now`: delay and loss, the Doctor's order for this pair first). [] when the test could not run."""
    from . import knowledge

    try:
        kinds, _notes = knowledge.plan_for(target["id"], snap["foreign_id"], old, False, True)
        kinds = kinds if old in kinds else [old] + kinds
        return autopilot._recommend_now(aid, target["id"], snap["foreign_id"], kinds, first_ok=first_ok)
    except Exception as e:      # noqa: BLE001 - a test that cannot run (another test too long, an SSH error) must not stop the move: the old type is built as before
        autopilot._say(aid, f"The test of the tunnel types on {target['name']} could not run ({str(e)[:120]}): the same type as now is built.")
        return []


def _best_kind(aid: int, snap: dict, target: dict, cache: dict, first_ok: bool = False, prefer: str = "") -> str:
    """The tunnel type to build on the new Iran server (the owner, 2026-10-19: "when it moves, the type stays the same as before unless the better ones do not answer - choose the best type here too"): the types are
    TESTED between the target and the foreign server (once for a pair, the tunnels of the pair share it); the best one that connects is built - but the type it has now stays when nothing beats it clearly
    (`autopilot.choose_better`: a clean line and BETTER_BY points more). When nothing connects the type it has now is built, as before."""
    old = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])

    if prefer:                                                   # (a move made for quality: the type that was SPEED-tested between the target and the foreign server and proved much faster is the one that is built)
        autopilot._say(aid, f"{snap['name']}: {prefer} is built - it gave the speed that proved {target['name']} better in the test before this move.")
        return prefer

    if not PICK_BEST:
        return old

    key = (target["id"], snap["foreign_id"])

    if key not in cache:
        cache[key] = _test_pair(aid, snap, target, old, first_ok)

    results = cache[key]
    cur = next((r for r in results if r.get("kind") == old and autopilot.connected(r)), None)
    best = autopilot.choose_better(cur.get("score") if cur else None, results) or (None if cur else autopilot.choose_better(None, results, cut=True))
    shown = ", ".join(f"{r['kind']} {r.get('score')}" for r in results if autopilot.connected(r)) or "none connected"
    autopilot._say(aid, f"{snap['name']}: tested {len(results)} tunnel type(s) between {target['name']} and {snap['foreign_name']}: {shown}.")

    if best is None:
        autopilot._say(aid, f"{snap['name']}: " + (f"nothing is clearly better than {old}: it stays {old}." if cur else f"no type connected in the test: {old} is built as before."))
        return old

    autopilot._say(aid, f"{snap['name']}: the best type here is {best['kind']} (score {best.get('score')})" + (f", not {old}." if best["kind"] != old else "."))
    return best["kind"]

def _apply_dns(aid: int, p: dict) -> tuple[list[dict], list[str], list[dict]]:
    done, errors, logged = [], [], []

    for d in p["dns"]:
        try:
            res = _apply_record(d, p["ip"], p["target"]["ip"])
            done.append({"fqdn": d["fqdn"], "account": d.get("account")})
            logged.append({"id": f"{p['id']}:{d['id']}", "at": int(time.time()), "plan_id": p["id"], "server": p["server"], "target": p["target"]["name"], "account": d.get("account") or "arvan", "domain": d["domain"],
                           "record_id": str(d["id"]), "fqdn": d["fqdn"], "old": p["ip"], "new": p["target"]["ip"], "before": res.get("before"), "configs": d.get("configs", []), "restored": False, "restored_at": None})
            autopilot._say(aid, f"DNS {d['fqdn']} ({d.get('account_name') or d.get('account') or 'ArvanCloud'}): {p['ip']} → {p['target']['ip']}")
        except (RuntimeError, ValueError) as e:
            errors.append(f"{d['fqdn']}: {str(e)[:140]}")
            autopilot._say(aid, f"DNS {d['fqdn']}: not changed: {str(e)[:160]}")

    return done, errors, logged


def _say_left(aid: int, p: dict) -> None:
    """The records that still carry the old server's address after a partial move, and why - said in the story where the operator reads it (the owner, 2026-11-03: a record tied to no tunnel stayed behind and a config died without a word)."""
    for d in p.get("left_alone") or []:
        why = "it also serves a tunnel that stays" if d.get("tunnels") else "nothing tells which tunnel it serves"
        autopilot._say(aid, f"DNS {d['fqdn']}: still points at {p['ip']} ({why}) - left alone; if a config uses it, point it by hand (Tunnels → CDN).")


def _not_carrying(aid: int, built: list) -> list[str]:
    """(1.69.208) The new halves of a move are asked, before any DNS record follows them, whether every port they forward really carries a connection (`ports_carry` on the Iran server: a connection that is closed at once found
    nothing at its far end). A tunnel that is up, answers its ping and passes its own checks can still refuse every connection - the Emirates and US configs, 2026-11-04, moved onto a BackPack Direct tunnel whose port lines
    dialled the Iran server itself. Returns "<tunnel>: <port>" for each port that does not; an answer that cannot be had (the server did not answer) is not a no."""
    from ..ssh import Node, SSHError, run_action

    bad: list[str] = []

    for _old, temp, snap in built:
        try:
            with session() as db:
                row = db.scalar(select(Tunnel).where(Tunnel.name == temp))

                if row is None or row.iran is None or not row.forwarded_ports:
                    continue

                node, ports = Node(row.iran.host, row.iran.ssh_user, row.iran.ssh_port), [int(x) for x in row.forwarded_ports]

            out = run_action(node, "ports_carry", {"CHECK": " ".join(str(x) for x in ports)}, timeout=60)
        except (SSHError, ValueError, RuntimeError, OSError) as e:
            autopilot._say(aid, f"{snap['name']}: the ports of the new tunnel could not be tried ({str(e)[:100]}); the move goes on.")
            continue

        shut = [ln.split()[1] for ln in out.splitlines() if ln.startswith(("CLOSED ", "NOCONNECT "))]

        if shut:
            autopilot._say(aid, f"{snap['name']}: the new tunnel is up but port {', '.join(shut)} carries nothing (a connection to it is closed at once): it is NOT used, the DNS is not changed and the old tunnel stays.")
            bad += [f"{snap['name']}: {x}" for x in shut]

    return bad


def _work(aid: int, p: dict, finish: bool = True) -> bool:
    """Inside story `aid`: move the tunnels, then - only when all moved - the DNS. When the source server is on, the new tunnels are
    built and checked FIRST, the DNS changed, the old tunnels kept for the TTL + 5 minutes, and only then removed (nobody is cut).
    When it is off, the old tunnels are dropped at once (their files are purged when it is back). Returns True when everything was done."""
    from . import builder, doctor

    with session() as db:
        src = db.get(Server, p["server_id"])
        src_on = src is not None and src.online is True and not is_cut(src)          # (a server that is cut from abroad cannot be reached to wait for its old tunnels: the failover way, like a dead one)

    autopilot._say(aid, f"{'Move' if src_on else 'Failover'} of {p['server']} ({p['why']}): {len(p['tunnels'])} tunnel(s) → {p['target']['name']}, {len(p['dns'])} DNS record(s) → {p['target']['ip']}"
                   + (" - the server is on: the new tunnels are built first, the old ones stay until the DNS has settled." if src_on and p["tunnels"] else ""))
    moved, moved_names, leftover = 0, [], []
    built: list[tuple[int, str, dict]] = []          # (old tunnel id, temp name, snapshot) while both run
    pairs: dict = {}                                  # (target, foreign) -> the test of the tunnel types of that pair

    def dead_one(t: dict) -> tuple[str, dict | None]:
        """One tunnel of a server that is off: what is left of it there, and its name when it was moved. The tunnels of one server go together (1.69.230: they were built one after the other)."""
        left = None

        try:
            snap = autopilot._snapshot(t["id"])

            with session() as db:
                row = db.get(Tunnel, t["id"])

                if row is not None and row.iran is not None and (row.iran.online is False or is_cut(row.iran)):
                    left = _leftover({**snap, "port": row.port}, p["server_id"])

            kind = _best_kind(aid, snap, p["target"], pairs, first_ok=True)      # (a dead server: the first type that connects, in the Doctor's order - no time for a full comparison)

            if autopilot._replace(aid, t["id"], p["target"]["id"], snap["foreign_id"], kind, f"Failover: {p['why']}.", skip_offline=True):
                return snap["name"], left
        except (ValueError, RuntimeError) as e:
            autopilot._say(aid, f"{t['name']}: not moved: {str(e)[:160]}")
        except Exception as e:      # (one tunnel must not stop the others that are built at the same time)
            log.exception("failover: %s", t["name"])
            autopilot._say(aid, f"{t['name']}: not moved: {str(e)[:160]}")

        return "", left

    if not src_on:
        with ThreadPoolExecutor(max(1, min(PARALLEL_TUNNELS, len(p["tunnels"])))) as ex:
            for name, left in ex.map(dead_one, p["tunnels"]):
                if left:
                    leftover.append(left)

                if name:
                    moved += 1
                    moved_names.append(name)

    for t in p["tunnels"] if src_on else []:
        try:
            snap = autopilot._snapshot(t["id"])
            kind = old_kind = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])
            temp = _temp_name(snap["name"], str((snap.get("record") or {}).get("slug") or ""))
            autopilot._say(aid, f"{snap['name']}: building the new {kind} tunnel on {p['target']['name']} as {temp} (the old one keeps running)…")
            kind = _best_kind(aid, snap, p["target"], pairs, prefer=str((p.get("prefer") or {}).get(t["id"]) or ""))
            ok, story = autopilot._wait(autopilot._build_kind({**snap, "name": temp}, p["target"]["id"], snap["foreign_id"], kind, snap["record"].get("opts") if kind == old_kind else None))

            if not ok:
                autopilot._say(aid, f"{snap['name']}: the new tunnel did not come up ({story[-1][:120] if story else '?'}); the old one stays.")
                break

            built.append((t["id"], temp, snap))
            moved += 1
        except (ValueError, RuntimeError) as e:
            autopilot._say(aid, f"{t['name']}: not moved: {str(e)[:160]}")

    release(p)                                         # (the tunnels are where they go now: the ranking counts them itself)

    if built and moved == len(p["tunnels"]) and _not_carrying(aid, built):
        moved = 0                                      # (the branch below takes the new halves away again and leaves the DNS alone)

    done_dns, errors, logged = [], [], []

    if moved < len(p["tunnels"]):
        autopilot._say(aid, f"{moved} of {len(p['tunnels'])} tunnels moved: the DNS is NOT changed (records must not point at a server that does not carry all of their tunnels).")

        for _, temp, _ in built:                      # make before break: nothing was broken, so the new halves go again
            with session() as db:
                row = db.scalar(select(Tunnel).where(Tunnel.name == temp))

            if row is not None:
                try:
                    builder.delete_tunnel(row.id)
                    autopilot._say(aid, f"{temp} removed again.")
                except (ValueError, RuntimeError) as e:
                    autopilot._say(aid, f"{temp}: not removed: {str(e)[:120]}")

        moved = 0
        built = []
    else:
        done_dns, errors, logged = _apply_dns(aid, p)
        _say_left(aid, p)
        _follow_later(p, done_dns)

        if built:
            if done_dns:
                ttl = max((int(d.get("ttl") or 0) if int(d.get("ttl") or 0) > 1 else 300) for d in p["dns"])
                grace = min(TTL_WAIT_MAX, ttl + TTL_GRACE)
                autopilot._say(aid, f"The old tunnels stay for {grace // 60} more minutes (the records' TTL + 5), so that nobody who still holds the old address is cut…")
                _sleep(grace)

            for old_id, temp, snap in built:
                if not autopilot._wait_quiet(aid, old_id, QUIET_LIMIT):         # the owner, 2026-10-19: "the old tunnel must not be cut until the new one is in place - the user must not feel a cut": it goes only when nobody uses it
                    autopilot._say(aid, f"{snap['name']}: users still use the old tunnel on {p['server']} {QUIET_LIMIT // 60} minutes after the DNS changed (clients that hold the old address, or a config that has the address itself), so it is NOT removed. "
                                   f"The new tunnel runs beside it as {temp}; remove the old one by hand when it is quiet (its menu → Remove tunnel).")
                    errors.append(f"{snap['name']}: the old tunnel is still in use, kept")
                    continue

                try:
                    res = builder.delete_tunnel(old_id, trash=True)
                    autopilot._say(aid, f"{snap['name']}: the old tunnel on {p['server']} removed: " + "; ".join(res))
                except (ValueError, RuntimeError) as e:
                    autopilot._say(aid, f"{snap['name']}: the old tunnel could not be removed ({str(e)[:120]}); it is still in the list.")

                with session() as db:
                    row = db.scalar(select(Tunnel).where(Tunnel.name == temp))

                if row is not None:
                    try:
                        builder.rename_tunnel(row.id, snap["name"])
                    except ValueError as e:
                        autopilot._say(aid, f"{temp}: keeps its name ({str(e)[:100]})")

                moved_names.append(snap["name"])

    ok = moved == len(p["tunnels"]) and not errors

    with _STATE_LOCK:                                  # (the plans of an outage end at about the same time: one reads, changes and saves the record at a time)
        s = _state()
        s["dns_log"].extend(logged)
        s["pending"] = [x for x in s["pending"] if x["id"] != p["id"]]
        s["history"].append({"id": p["id"], "at": int(time.time()), "server_id": p["server_id"], "server": p["server"], "ip": p["ip"], "reason": p.get("reason", "down"), "target": p["target"],
                             "moved": moved, "tunnels": len(p["tunnels"]), "dns": done_dns, "errors": errors, "ok": ok, "undone": False})
        _save(s)

    if p["tunnels"]:
        doctor.record_move(p, moved_names, leftover, ok)

    with session() as db:
        emit(db, "ok" if ok else "warn", "actions", f"Doctor: {p['server']} → {p['target']['name']} ({p['why']}): {moved}/{len(p['tunnels'])} tunnels moved, {len(done_dns)} DNS record(s) changed" + (f", {len(errors)} failed" if errors else "") + ".", p["server"])

    _notice(f"{'✅' if ok else '⚠'} Doctor: {p['server']} → {p['target']['name']} ({p['why']}): {moved}/{len(p['tunnels'])} tunnels moved, {len(done_dns)} DNS record(s) now point at {p['target']['ip']}." + (f" Errors: {'; '.join(errors)}" if errors else "") + " Undo: Tunnels → Tunnel Doctor → Moves.")

    if finish:
        autopilot._say(aid, "Done." if ok else "Done with problems: look at the lines above.", done=True, ok=ok)

    return ok


def run_plan(p: dict) -> int:
    """Start the story in the background: move the tunnels, then the DNS. Returns the repair story id."""
    if not p.get("target"):
        raise ValueError("there is no replacement server for this plan")

    autopilot.mass_begin()                             # (the Doctor's own affairs wait while any failover story runs, and a few minutes after: `autopilot.quiet_now`)

    try:
        return autopilot._start(f"failover {p['server']}", _run, p, action="move")
    except BaseException:
        autopilot.mass_end()
        raise


def _run(aid: int, p: dict) -> bool:
    try:
        return _work(aid, p)
    finally:
        release(p)                                     # (whatever happened, the tunnels on their way are not counted any more)
        autopilot.mass_end()


def quality_pick(ranking: list[dict], rtt: float | None) -> dict | None:
    """Pure: the Iran server a tunnel may be moved to by itself for quality - the first row of `autopilot.ranking` (best first) that is on, ranks at least QUALITY_MIN_SCORE, has the room for one more tunnel, did not fail its
    GRE check, has no cut tunnel of its own to this foreign server, answers it with at most QUALITY_LOSS % loss and in at most QUALITY_DELAY times the tunnel's own delay (`rtt`, None = not asked). None when there is none."""
    for c in ranking:
        if c.get("online", True) is False or int(c.get("score") or 0) < QUALITY_MIN_SCORE or c.get("gre_bad") or c.get("prev_cut") or room_for(c, 1):
            continue

        delay, loss = c.get("rtt_ms"), c.get("loss_pct")

        if delay is None or loss is None or float(loss) > QUALITY_LOSS or (rtt and float(delay) > QUALITY_DELAY * float(rtt)):
            continue

        return c

    return None


def quality_move(aid: int, tunnel_id: int, reason: str, now_mbps: float, rtt: float | None, say) -> bool:
    """(1.69.186) The Doctor moves a tunnel to another Iran server BY ITSELF, for speed and quality (the owner, 2026-11-02: "I gave the Doctor permission to change the IP as well - anything it likes, only speed, stability and quality must
    be high"). Only in the mode "move", and only when it is PROVED: the tunnel dropped (or is cut) and no type of its own server does better; another Iran server ranks well for the foreign server (`quality_pick`) and the plan moves
    this tunnel alone (no other tunnel is pulled along by a shared DNS record); and a SPEED test of the types between that server and the foreign server gives clearly more than the tunnel gives now, on a clean line (`bandwidth.drop_pick`).
    The move is the one that never cuts the users (the new tunnel is built first, the DNS follows, the old one goes when it is quiet) with its rails: three a day for a tunnel, two hours between, and one move for quality an hour for the
    whole panel. The type that gave the speed is the one that is built. True when a move was started."""
    from ..ssh import SSHError
    from . import bandwidth, doctor, knowledge, license

    if not doctor.can_move() or move_blocked() or autopilot.quiet_now():          # (1.69.230: no move for quality while the Doctor moves the tunnels of a dead server)
        return False

    snap = autopilot._snapshot(tunnel_id)

    if is_protected(snap["record"]):
        say(f"No move for quality: {snap['name']} is protected, it is never moved.")
        return False

    now = int(time.time())
    moves = doctor._state()["moves"]
    block = doctor.may_move(snap["name"], moves, now)
    last = max((int(m.get("at") or 0) for m in moves if m.get("reason") == "quality"), default=0)

    if not block and last and now - last < QUALITY_GAP:
        block = f"another tunnel was moved for quality {(now - last) // 60} minutes ago (one every {QUALITY_GAP // 60} minutes)"

    if block:
        say(f"No move for quality now: {block}.")
        return False

    try:
        cand = quality_pick(autopilot.ranking(tunnel_id), rtt)
    except (ValueError, RuntimeError) as e:
        say(f"No move for quality: the Iran servers could not be ranked ({str(e)[:100]}).")
        return False

    if cand is None:
        say("No move for quality: no other Iran server reaches the foreign server clearly better and has the room.")
        return False

    try:
        plan = _make_plan(snap["iran_id"], {tunnel_id}, reason if reason in REASONS else "quality", target_id=cand["id"])
    except (ValueError, RuntimeError) as e:
        say(f"No move for quality to {cand['name']}: {str(e)[:120]}.")
        return False

    if plan["pulled"] or plan["problems"] or not plan["target"]:
        why = ("it would pull " + ", ".join(plan["pulled"]) + " along (they share a DNS record with this tunnel)") if plan["pulled"] else "; ".join(plan["problems"]) or "no plan"
        say(f"No move for quality to {cand['name']}: {why}.")
        return False

    cur = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])
    cannot = knowledge.cannot_try(knowledge.pair(cand["id"], snap["foreign_id"]), now)
    order, _ = knowledge.plan_for(cand["id"], snap["foreign_id"], cur, False, True)
    kinds = [k for k in ([cur] + [x for x in order if x != cur]) if k not in cannot and license.tunnel_kind_allowed(k)][:bandwidth.DROP_TRIES]
    say(f"{snap['name']} runs at {float(now_mbps or 0):.0f} Mb/s and no type of {snap['iran_name']} does better: before moving it the Doctor speed-tests {', '.join(kinds)} between {cand['name']} ({cand.get('rtt_ms')} ms, {cand.get('loss_pct')} % loss) and {snap['foreign_name']}.")

    try:
        results = autopilot._recommend_now(aid, cand["id"], snap["foreign_id"], kinds, measure="full")
    except (ValueError, RuntimeError, SSHError) as e:
        say(f"The speed test could not run ({str(e)[:120]}): nothing is moved.")
        return False

    shown = ", ".join(f"{r['kind']} {float(r['down_mbps']):.0f} Mb/s" for r in results if r.get("down_mbps") is not None) or "none carried data"
    say(f"Tested between {cand['name']} and {snap['foreign_name']}: {shown}.")
    best = bandwidth.drop_pick(results, {}, float(now_mbps or 0), rtt)

    if best is None:
        say(f"No type gives clearly more than {float(now_mbps or 0):.0f} Mb/s there with a clean line: {snap['name']} stays on {snap['iran_name']}.")
        return False

    why = f"{snap['name']} gives {float(now_mbps or 0):.0f} Mb/s on {snap['iran_name']}; {best['kind']} gave {float(best['down_mbps']):.0f} Mb/s between {cand['name']} and {snap['foreign_name']} in a speed test just now, on a clean line"

    try:
        doctor.move_now(tunnel_id, cand["id"], "quality", why, prefer=best["kind"])
    except ValueError as e:
        say(f"The move was refused: {str(e)[:140]}.")
        return False

    say(f"The Doctor moves {snap['name']} from {snap['iran_name']} to {cand['name']} as {best['kind']}: {why}. The new tunnel is built first, the DNS follows, the old one goes when it is quiet (Tunnel Doctor → Moves can put the record back).")
    return True


def after_ladder(aid: int, tunnel_id: int, cause: str) -> bool:
    """Called by the smart repair when a route problem survived every step. In the mode "move" the Doctor now moves the tunnel by itself when another Iran server is PROVED much faster (`quality_move`: the owner, 2026-11-02);
    otherwise - or when nothing is proved - a poor route never moves a server by itself: the move is written down as a suggestion (Tunnel Doctor → one click), with the best Iran server for it. Returns True when a move was started."""
    from . import doctor

    if not ENABLED:
        autopilot._say(aid, "The move to another Iran server is switched off: the tunnel stays where it is.")
        return False

    with session() as db:
        t = db.get(Tunnel, tunnel_id)
        fixed = t is not None and is_protected(t)

    if fixed:          # (1.69.220: no move, and no suggestion of one either)
        autopilot._say(aid, "This tunnel is protected: it is never moved by the Doctor, and no move is suggested. Repairs in place (restarts) are all it gets.")
        return False

    try:
        p = route_plan(tunnel_id, cause)
    except (ValueError, RuntimeError) as e:
        autopilot._say(aid, f"No plan for a move: {str(e)[:160]}")
        return False

    if p.get("target") and doctor.mode() == "move" and quality_move(aid, tunnel_id, cause, 0.0, None, lambda line: autopilot._say(aid, line)):
        return True

    extra =(f" Also moved to keep the DNS consistent: {', '.join(p['pulled'])}." if p["pulled"] else "") + (f" Problems: {'; '.join(p['problems'])}." if p["problems"] else "")
    tgt = p.get("target") or {}
    doctor.suggest("route", tunnel_id, next((t["name"] for t in p["tunnels"] if t["id"] == tunnel_id), p.get("old_tunnel") or str(tunnel_id)), p["server_id"], p["server"], tgt.get("id"), tgt.get("name") or "",
                   f"{p['why']}; no tunnel type works on this route" + (f" - {tgt['name']} ranks best for it" if tgt else " - no other Iran server could carry it right now"))

    if tgt:
        autopilot._say(aid, f"Not moved by itself (only in the mode “move”, and only when another Iran server is proved much faster in a speed test). Suggested: {_describe(p)}.{extra} Tunnel Doctor → Suggestions does it with one click.")
    else:
        autopilot._say(aid, "No other Iran server can carry " + (", ".join(t["name"] for t in p["tunnels"]) or "this tunnel") + " right now." + extra)

    return False


def replace_plan(old_tunnel_id: int, new_iran_id: int) -> dict:
    """The DNS part of a manual replacement (Create tunnel on another Iran server, same foreign server and ports): the records that
    serve ONLY the replaced tunnel follow the new Iran server. A record that also serves a tunnel that stays is left alone and listed."""
    with session() as db:
        t = db.get(Tunnel, old_tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        old, new = db.get(Server, t.iran_server_id), db.get(Server, new_iran_id)

        if new is None or new.role != "iran":
            raise ValueError("choose an Iran server")

        info = {"server_id": old.id, "server": old.name, "ip": old.host, "old_tunnel": t.name}
        target = {"id": new.id, "name": new.name, "ip": new.host}

    recs, dns_error = _records(info["ip"], _tunnels_of(info["server_id"]))
    dns = [r for r in recs if r["tunnels"] == [old_tunnel_id]]
    left = [{"fqdn": r["fqdn"], "configs": r["configs"], "tunnels": r["tunnels"]} for r in recs if r not in dns and old_tunnel_id in r["tunnels"]]
    now = int(time.time())
    return {**info, "id": f"{info['server_id']}-{now}", "reason": "replace", "why": f"{info['old_tunnel']} was replaced by a tunnel on {target['name']}", "whole": False,
            "tunnels": [], "move": [], "pulled": [], "target": target, "dns": [{k: v for k, v in r.items() if k != "tunnels"} for r in dns], "left_alone": left,
            "dns_error": dns_error, "problems": [dns_error] if dns_error else [], "made_at": now}


def after_replace(aid: int, p: dict, immediate: bool = False) -> None:
    """After a manual replacement built fine: the DNS plan is applied now when the operator ticked it in the Create form (`immediate`)
    or the Doctor is in mode "move"; otherwise it is only told."""
    if not p["dns"]:
        autopilot._say(aid, f"DNS: no CDN record served only {p['old_tunnel']}" + (f" (left alone, they also serve tunnels that stay: {', '.join(d['fqdn'] for d in p['left_alone'])})" if p["left_alone"] else "")
                       + (f"; {p['dns_error']}" if p.get("dns_error") else "") + ".")
        return

    names = ", ".join(d["fqdn"] for d in p["dns"])

    if immediate or enabled():
        autopilot._say(aid, f"DNS ({'as ticked' if immediate else 'automatic'}): {names} → {p['target']['ip']}")
        _work(aid, p, finish=False)
    else:
        autopilot._say(aid, f"DNS: {names} still point at {p['ip']} (change them under Tunnels → CDN, or put the Doctor in the mode that moves servers).")


def _domain_of(fqdn: str, domains: list[str]) -> str:
    """Pure: the registered domain (from the account's list) that `fqdn` belongs to, longest match; '' when none."""
    f = fqdn.lower().strip(".")
    hits = [d for d in domains if f == d.lower() or f.endswith("." + d.lower())]
    return max(hits, key=len) if hits else ""


def point_names(aid: int, names: list[str], iran_id: int, tag: str = "create") -> list[dict]:
    """Point the A records of these names at the Iran server `iran_id` - as asked by the operator (a tick in Create tunnel, or the
    small form on the DNS changes box). The names may live in ANY of the DNS accounts (ArvanCloud, another ArvanCloud, Cloudflare): the account
    whose domain matches the name is used. Every change is written to `dns_log`, so it can be put back with Restore."""
    from . import dnsprov

    with session() as db:
        s = db.get(Server, iran_id)

        if s is None or s.role != "iran":
            raise ValueError("choose an Iran server")

        target = {"id": s.id, "name": s.name, "ip": s.host}
        hosts = {x.host: x.name for x in db.scalars(select(Server))}

    accs = dnsprov.accounts()

    if not accs:
        autopilot._say(aid, "DNS: no DNS account is saved (Tunnels → CDN), the names are not changed.")
        return []

    doms: dict[str, list[str]] = {}

    for a in accs:
        try:
            doms[a.id] = dnsprov.provider_of(a).domains()
        except (RuntimeError, ValueError) as e:
            autopilot._say(aid, f"DNS: {a.name} did not answer: {str(e)[:140]}")
            doms[a.id] = []

    done, fq_done, s_ = [], [], _state()
    now = int(time.time())

    for raw in names:
        fqdn = raw.lower().strip().strip(".")
        best = None

        for a in accs:      # the account whose domain matches the name best (the main ArvanCloud account wins a tie)
            d = _domain_of(fqdn, doms.get(a.id, []))

            if d and (best is None or len(d) > len(best[1])):
                best = (a, d)

        if best is None:
            autopilot._say(aid, f"DNS {fqdn}: not in any domain of your DNS accounts ({', '.join(sorted({d for v in doms.values() for d in v})[:8]) or 'none'})")
            continue

        acc, domain = best
        sub = "@" if fqdn == domain else fqdn[: -len(domain) - 1]

        if acc.id != "arvan":      # another ArvanCloud account or Cloudflare: through the provider, written to the log as a change by hand (Restore puts the old record back)
            prov = dnsprov.provider_of(acc)

            try:
                rows = [r for r in prov.records(domain) if r["type"] == "A" and r["name"] == sub]
            except (RuntimeError, ValueError) as e:
                autopilot._say(aid, f"DNS {fqdn}: {str(e)[:140]}")
                continue

            if not rows:
                autopilot._say(aid, f"DNS {fqdn}: no A record with this name in {domain} ({acc.name}); nothing changed")
                continue

            for r in rows:
                old_ips = list(r.get("ips") or [])

                if old_ips == [target["ip"]]:
                    autopilot._say(aid, f"DNS {fqdn}: already points at {target['ip']}")
                    continue

                try:
                    res = prov.update(domain, r["id"], r["name"], [target["ip"]], int(r.get("ttl") or 120), bool(r.get("cloud")))
                except (RuntimeError, ValueError) as e:
                    autopilot._say(aid, f"DNS {fqdn}: not changed: {str(e)[:140]}")
                    continue

                log_manual("edit", domain, fqdn, str(r["id"]), ", ".join(old_ips), target["ip"], res.get("before"), acc.id)
                done.append(res)
                fq_done.append(fqdn)
                autopilot._say(aid, f"DNS {fqdn} ({acc.name}): {', '.join(old_ips) or '-'} → {target['ip']} ({target['name']})")

            continue

        try:
            recs = [r for r in arvan.records(domain) if str(r.get("type", "")).lower() == "a" and str(r.get("name") or "@") == sub]
        except RuntimeError as e:
            autopilot._say(aid, f"DNS {fqdn}: {str(e)[:140]}")
            continue

        if not recs:
            autopilot._say(aid, f"DNS {fqdn}: no A record with this name in {domain}; nothing changed")
            continue

        for r in recs:
            for old in {str(i.get("ip")) for i in (r.get("value") or []) if isinstance(i, dict) and i.get("ip")}:
                if old == target["ip"]:
                    autopilot._say(aid, f"DNS {fqdn}: already points at {old}")
                    continue

                try:
                    res = arvan.apply(domain, r["id"], old, target["ip"])
                except RuntimeError as e:
                    autopilot._say(aid, f"DNS {fqdn}: not changed: {str(e)[:140]}")
                    continue

                done.append(res)
                fq_done.append(fqdn)
                s_["dns_log"].append({"id": f"{tag}-{aid}:{r['id']}:{old}", "at": now, "plan_id": f"{tag}-{aid}", "server": hosts.get(old, old), "target": target["name"], "domain": domain,
                                      "record_id": str(r["id"]), "fqdn": fqdn, "old": old, "new": target["ip"], "configs": [], "restored": False, "restored_at": None})
                autopilot._say(aid, f"DNS {fqdn}: {old} → {target['ip']} ({target['name']})")

    if done:
        fresh = _state()      # the by-hand entries written meanwhile are kept: only the entries of this run are added to what is stored now
        known = {e.get("id") for e in fresh["dns_log"]}
        fresh["dns_log"].extend(e for e in s_["dns_log"] if e.get("id") not in known)
        _save(fresh)

        with session() as db:
            emit(db, "ok", "actions", f"DNS: {len(done)} record(s) now point at {target['name']} ({target['ip']}): {', '.join(sorted(set(fq_done)))}.", target["name"])

        _quiet(f"🌐 DNS: {len(done)} record(s) now point at {target['name']} ({target['ip']}).")

    return done

def approve(plan_id: str) -> int:
    s = _state()
    p = next((x for x in s["pending"] if x["id"] == plan_id), None)

    if p is None:
        raise ValueError("this plan is no longer pending")

    reason = p.get("reason", "down")        # the world may have changed since the plan was made: plan again, same tunnels

    if reason == "replace":                 # DNS only, for a tunnel that is already gone: run it as it was made
        return run_plan(p)

    fresh = _make_plan(p["server_id"], None if reason == "down" else set(p.get("move") or [t["id"] for t in p["tunnels"]]), reason)
    fresh["id"], fresh["down_since"] = p["id"], p.get("down_since")
    return run_plan(fresh)


def dismiss(plan_id: str) -> None:
    s = _state()
    gone = [x for x in s["pending"] if x["id"] == plan_id]

    if not gone:
        raise ValueError("this plan is no longer pending")

    s["pending"] = [x for x in s["pending"] if x["id"] != plan_id]
    s["history"].append({"id": plan_id, "at": int(time.time()), "server_id": gone[0]["server_id"], "server": gone[0]["server"], "ip": gone[0]["ip"], "reason": gone[0].get("reason", "down"),
                         "target": gone[0].get("target"), "moved": 0, "tunnels": 0, "dns": [], "errors": [], "ok": None, "dismissed": True, "undone": False})
    _save(s)


def log_manual(op: str, domain: str, fqdn: str, record_id: str, old: str, new: str, before: dict | None = None, account: str = "arvan") -> dict:
    """A record made, changed or deleted BY HAND (the operator asked): kept in the DNS log with everything needed to put it back (op: create | edit | delete)."""
    s = _state()
    now = int(time.time())
    e = {"id": f"m-{now}-{len(s['dns_log'])}:{record_id}", "at": now, "plan_id": f"m-{now}-{len(s['dns_log'])}", "op": op, "server": {"create": "made by hand", "edit": "changed by hand", "delete": "deleted by hand"}[op], "target": "",
         "account": account, "domain": domain, "record_id": str(record_id), "fqdn": fqdn, "old": old, "new": new, "before": before, "configs": [], "restored": False, "restored_at": None}
    s["dns_log"].append(e)
    _save(s)
    _quiet(f"🌐 DNS {fqdn}: {op} by hand ({old or '-'} → {new or '-'}).")
    return e


def log_bridge(account: str, domain: str, fqdn: str, record_id: str, old: str, new: str, before: dict | None, server: str) -> dict:
    """The origin port of a record was switched to a forward of the outbound (services/outbound.py): kept with everything needed to put it back (op: bridge). No Telegram notice: the story says it."""
    s = _state()
    now = int(time.time())
    e = {"id": f"b-{now}-{len(s['dns_log'])}:{record_id}", "at": now, "plan_id": f"b-{now}", "op": "bridge", "server": server, "target": "outbound forward", "account": account, "domain": domain, "record_id": str(record_id), "fqdn": fqdn,
         "old": old, "new": new, "before": before, "configs": [], "restored": False, "restored_at": None}
    s["dns_log"].append(e)
    _save(s)
    return e


def restore(log_id: str) -> dict:
    """Put ONE DNS change back: a re-pointed record at the address it had (the tunnels stay where they are); a record made by hand is deleted again, a deleted one is made again,
    a changed one gets its old values back."""
    s = _state()
    e = next((x for x in s["dns_log"] if x["id"] == log_id), None)

    if e is None:
        raise ValueError("this DNS change is not in the list any more")

    if e.get("restored"):
        raise ValueError(f"{e['fqdn']} is already back as it was")

    op = e.get("op")
    prov = dnsprov.provider(e.get("account") or "arvan") if (op or e.get("before")) else None     # an entry from before 1.36 has neither: the plain ArvanCloud call below

    if op == "create":
        prov.delete(e["domain"], e["record_id"])
    elif op == "delete":
        old_id, new_id = e["record_id"], prov.create_from(e["domain"], e.get("before") or {})

        for x in s["dns_log"]:                      # the record has a new id now: the older changes of it that are still open follow it
            if x["domain"] == e["domain"] and x["record_id"] == old_id and not x.get("restored"):
                x["record_id"] = new_id
    elif op == "edit" or e.get("before"):
        prov.put_back(e["domain"], e["record_id"], e.get("before") or {})
    else:
        arvan.apply(e["domain"], e["record_id"], e["new"], e["old"])

    e["restored"], e["restored_at"] = True, int(time.time())

    if op:
        _save(s)

        with session() as db:
            emit(db, "warn", "actions", f"DNS {e['fqdn']}: " + ("the switch to the outbound forward" if op == "bridge" else f"the change by hand ({op})") + " was put back.", e["fqdn"])

        _quiet(f"↩ DNS {e['fqdn']}: " + ("the switch to the outbound forward" if op == "bridge" else f"the change by hand ({op})") + " was put back.")
        return dict(e)

    if not any(x["plan_id"] == e["plan_id"] and not x.get("restored") for x in s["dns_log"]):
        for h in s["history"]:
            if h["id"] == e["plan_id"]:
                h["undone"] = True              # every record of that failover is back

    _save(s)

    with session() as db:
        emit(db, "warn", "actions", f"DNS {e['fqdn']} points at {e['old']} ({e['server']}) again; the tunnels stay on {e['target']}.", e["server"])

    _quiet(f"↩ DNS {e['fqdn']} points at {e['old']} ({e['server']}) again. The tunnels stay on {e['target']}.")
    return dict(e)


def undo(plan_id: str) -> list[dict]:
    """Point every record of a finished failover back at the old address (the tunnels are left where they are)."""
    s = _state()
    todo = [x["id"] for x in s["dns_log"] if x["plan_id"] == plan_id and not x.get("restored")]

    if not todo:
        raise ValueError("nothing to undo for this entry")

    return [restore(i) for i in todo]
