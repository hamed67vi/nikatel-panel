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

import time

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Config, Server, Tunnel
from . import arvan, autopilot, dnsprov, telegram
from . import settings as st
from .events import emit

log = get_logger("failover")
ENABLED = True       # the kill switch of the whole DNS failover (tests flip it); what really decides is the Doctor's mode (services/doctor.py)
TTL_GRACE = 300      # seconds the old tunnel lives on after the DNS changed, on top of the record's TTL (clients that still hold the old address)
TTL_WAIT_MAX = 900
ROUTE_CAUSES = ("gre_blocked", "route_cut", "lossy_route", "port_blocked")
REASONS = {"down": "the server does not answer",
           "resources": "the server's resources are exhausted",
           "return": "the server is back and healthy",
           "manual": "moved by you",
           "gre_blocked": "GRE is filtered on its route and no other tunnel type works",
           "route_cut": "the route to the foreign server is cut",
           "lossy_route": "the route loses too many packets and no other tunnel type does better",
           "port_blocked": "the tunnel port is blocked on its route and no other tunnel type works"}


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
        _notice(f"🌐 DNS: {len(done)} record(s) of {server_name} now point at its new address {new_ip}.")

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
        return [{"id": t.id, "name": t.name, "state": t.state, "ports": {int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()}}
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


def _records(ip: str, tunnels: list[dict]) -> tuple[list[dict], str]:
    """The records that carry `ip`, each with the configs that use its name and the tunnels (of this server) those configs go through."""
    if not dnsprov.accounts():
        return [], ""

    with session() as db:
        configs = [(c.name, str((c.meta or {}).get("address", "")).lower(), int((c.meta or {}).get("port") or 0)) for c in db.scalars(select(Config))]

    recs, err = _fetch_records(ip)

    for r in recs:
        mine = [(n, p) for n, a, p in configs if a == r["fqdn"].lower()]
        r["configs"] = [n for n, _ in mine]
        by_config = {t["id"] for t in tunnels if any(p in t["ports"] for _, p in mine)}
        by_port = {t["id"] for t in tunnels if r.get("port") and int(r["port"]) in t["ports"]}       # a CDN record whose origin port is the port of a tunnel belongs to that tunnel (found by the real test: without this a record no probe config names was left on the old server)
        r["tunnels"] = sorted(by_config | by_port)

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


def _make_plan(server_id: int, move: set[int] | None, reason: str, target_id: int | None = None, strict: bool = False) -> dict:
    """move=None: every tunnel of the server (it is dead). Otherwise the given tunnels, widened so that the DNS stays consistent.
    `target_id`: the Iran server the operator (or the return) chose; else the one that ranks best for every moved tunnel.
    `strict` (the "Move the tunnels" button): only a server with room is a target - `room_for` (line quality, resources, tunnels on it after the move) - and every port of the moved tunnels is proved free on it
    (the panel's own tunnels there AND what listens on the server); the plan also lists the best three targets (`candidates`) and the servers left out with the reason (`skipped`)."""
    with session() as db:
        s = db.get(Server, server_id)

        if s is None or s.role != "iran":
            raise ValueError("choose an Iran server")

        info = {"server_id": s.id, "server": s.name, "ip": s.host}

    tunnels = _tunnels_of(server_id)
    recs, dns_error = _records(info["ip"], tunnels)
    all_ids = {t["id"] for t in tunnels}
    asked = all_ids if move is None else set(move) & all_ids
    moved = all_ids if move is None else closure(asked, recs)
    whole = moved == all_ids
    moving = [t for t in tunnels if t["id"] in moved]
    dns = [r for r in recs if whole or (r["tunnels"] and set(r["tunnels"]) <= moved)]
    left = [] if whole else [{"fqdn": r["fqdn"], "configs": r["configs"]} for r in recs if r not in dns]
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
        for t in moving:
            try:
                rankings[t["id"]] = [r for r in autopilot.ranking(t["id"]) if r.get("online", True) is not False and r.get("score", 0) > 0]
            except (ValueError, RuntimeError) as e:
                problems.append(f"{t['name']}: {str(e)[:120]}")

    if not strict:
        if not target_id:
            target = pick_target(rankings) if rankings else None
    else:                                   # an explicit target (the one the operator confirmed) is judged by the same rules as an automatic one: the world may have changed since the plan was shown
        if len(rankings) < len(moving):
            rankings = {}                   # one tunnel could not be ranked: no server is proved to carry all of them

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
    if moving and not target:
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

    if strict:
        out["candidates"] = [{"id": c["id"], "name": c["name"], "ip": hosts.get(c["id"]), "reason": c.get("reason", ""), "quality": c.get("quality"), "resources": c.get("resources"), "tunnels": c.get("tunnels")} for c in candidates]
        out["skipped"] = list(skipped.values())

    return out


def plan_for(server_id: int) -> dict:
    """What a failover of a dead Iran server would do: every tunnel, every record that carries its address."""
    return _make_plan(server_id, None, "down")


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
        raise ValueError(f"{p['server']} carries no tunnel: nothing to move" if not tunnels else f"this tunnel is not an enabled tunnel of {p['server']}: nothing to move")

    if not p["target"]:
        raise ValueError("no Iran server can take these tunnels now: " + "; ".join(p["problems"])[:300])

    if p["dns_error"]:
        raise ValueError(f"a DNS account did not answer ({p['dns_error'][:200]}): its records could not follow the tunnels - fix the account (Tunnels → CDN) and try again")

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


def _describe(p: dict) -> str:
    names = ", ".join(t["name"] for t in p["tunnels"]) or "no tunnel"
    tgt = p.get("target")
    return (f"move {len(p['tunnels'])} tunnel(s) ({names}) to {tgt['name'] if tgt else '—'} and point {len(p['dns'])} DNS record(s)"
            f" ({', '.join(d['fqdn'] for d in p['dns']) or 'none'}) at {tgt['ip'] if tgt else '—'}")


def _notify_plan(p: dict, headline: str) -> None:
    _notice(f"🔶 {headline}\nPlan: {_describe(p)}." + (f"\nAlso moved to keep the DNS consistent: {', '.join(p['pulled'])}." if p.get("pulled") else "")
            + (f"\nProblems: {'; '.join(p['problems'])}" if p.get("problems") else ""))


def _busy(s: dict, sid: int, now: int) -> bool:
    """A failover of this server ran in the last 6 hours (or a plan still waits)."""
    return any(p["server_id"] == sid and p.get("reason") != "replace" for p in s["pending"]) or any(h["server_id"] == sid and h.get("reason") == "down" and now - h["at"] < 6 * 3600 for h in s["history"])


RETRY_NO_TARGET = 600     # a dead server whose tunnels found no target is looked at again after this long


# ------------------------------------------------------------------ the job: dead servers (run by the Doctor in mode "move")
def check() -> None:
    """Every Iran server that has been off for `server_minutes` gets its tunnels moved - one server per tick, the one with the most tunnels first.
    Several servers off at once (a provider-wide outage) are handled one after the other, as long as a healthy Iran server is left to carry them."""
    if not enabled() or get_settings().read_only:
        return

    now = int(time.time())
    s = _state()
    wait = int(st.get("repair").get("server_minutes", 10)) * 60

    with session() as db:
        down = {x.id: x.name for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(False)))}
        load = {}

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            load[t.iran_server_id] = load.get(t.iran_server_id, 0) + 1

    stale = [sid for sid in s["seen"] if int(sid) not in down]

    for sid in stale:
        s["seen"].pop(sid)                     # it answered again: the clock starts over
        s.setdefault("tried", {}).pop(sid, None)

    changed = bool(stale)                      # a forgotten entry must be saved too, or the next outage would skip the waiting time

    for sid, name in sorted(down.items(), key=lambda kv: -load.get(kv[0], 0)):
        since = s["seen"].setdefault(str(sid), now)
        changed = changed or since == now

        if now - since < wait or _busy(s, sid, now) or not load.get(sid) or now - int(s.setdefault("tried", {}).get(str(sid), 0)) < RETRY_NO_TARGET:
            continue

        try:
            p = plan_for(sid)
        except (ValueError, RuntimeError):
            log.warning("failover plan for %s failed", name, exc_info=True)
            continue

        p["id"] = f"{sid}-{now}"
        p["down_since"] = since
        s["tried"][str(sid)] = now
        changed = True

        if not p["target"]:
            _notice(f"⚠ Iran server {name} has been off for {(now - since) // 60} min, but no healthy Iran server can carry its {len(p['tunnels'])} tunnel(s) right now" + (f" ({'; '.join(p['problems'])})" if p["problems"] else "") + ". The Doctor tries again in 10 minutes.")

            with session() as db:
                emit(db, "bad", "actions", f"Doctor: {name} is off, no Iran server can take its tunnels yet; trying again in 10 minutes.", name)

            continue

        _save(s)
        _notice(f"🔶 Iran server {name} has been off for {(now - since) // 60} min: " + _describe(p) + "." + (f" Also moved to keep the DNS consistent: {', '.join(p['pulled'])}." if p.get("pulled") else ""))
        run_plan(p)                            # one server per tick; the story writes the state itself when it is done
        return

    if changed:
        _save(s)


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


def _work(aid: int, p: dict, finish: bool = True) -> bool:
    """Inside story `aid`: move the tunnels, then - only when all moved - the DNS. When the source server is on, the new tunnels are
    built and checked FIRST, the DNS changed, the old tunnels kept for the TTL + 5 minutes, and only then removed (nobody is cut).
    When it is off, the old tunnels are dropped at once (their files are purged when it is back). Returns True when everything was done."""
    from . import builder, doctor

    with session() as db:
        src = db.get(Server, p["server_id"])
        src_on = src is not None and src.online is True

    autopilot._say(aid, f"{'Move' if src_on else 'Failover'} of {p['server']} ({p['why']}): {len(p['tunnels'])} tunnel(s) → {p['target']['name']}, {len(p['dns'])} DNS record(s) → {p['target']['ip']}"
                   + (" - the server is on: the new tunnels are built first, the old ones stay until the DNS has settled." if src_on and p["tunnels"] else ""))
    moved, moved_names, leftover = 0, [], []
    built: list[tuple[int, str, dict]] = []          # (old tunnel id, temp name, snapshot) while both run

    for t in p["tunnels"]:
        try:
            snap = autopilot._snapshot(t["id"])
            kind = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])

            if src_on:
                temp = _temp_name(snap["name"], str((snap.get("record") or {}).get("slug") or ""))
                autopilot._say(aid, f"{snap['name']}: building the new {kind} tunnel on {p['target']['name']} as {temp} (the old one keeps running)…")
                ok, story = autopilot._wait(autopilot._build_kind({**snap, "name": temp}, p["target"]["id"], snap["foreign_id"], kind, snap["record"].get("opts")))

                if not ok:
                    autopilot._say(aid, f"{snap['name']}: the new tunnel did not come up ({story[-1][:120] if story else '?'}); the old one stays.")
                    break

                built.append((t["id"], temp, snap))
                moved += 1
            else:
                with session() as db:
                    row = db.get(Tunnel, t["id"])

                    if row is not None and row.iran is not None and row.iran.online is False:
                        leftover.append(_leftover({**snap, "port": row.port}, p["server_id"]))

                if autopilot._replace(aid, t["id"], p["target"]["id"], snap["foreign_id"], kind, f"Failover: {p['why']}.", skip_offline=True):
                    moved += 1
                    moved_names.append(snap["name"])
        except (ValueError, RuntimeError) as e:
            autopilot._say(aid, f"{t['name']}: not moved: {str(e)[:160]}")

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

        if built:
            if done_dns:
                ttl = max((int(d.get("ttl") or 0) if int(d.get("ttl") or 0) > 1 else 300) for d in p["dns"])
                grace = min(TTL_WAIT_MAX, ttl + TTL_GRACE)
                autopilot._say(aid, f"The old tunnels stay for {grace // 60} more minutes (the records' TTL + 5), so that nobody who still holds the old address is cut…")
                _sleep(grace)

            for old_id, temp, snap in built:
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

    return autopilot._start(f"failover {p['server']}", _work, p)


def after_ladder(aid: int, tunnel_id: int, cause: str) -> bool:
    """Called by the smart repair when a route problem survived every step. A poor route or packet loss NEVER moves a server by itself
    (the operator's rule): the move is written down as a suggestion (Tunnel Doctor → one click), with the best Iran server for it. Returns False."""
    from . import doctor

    if not ENABLED:
        autopilot._say(aid, "The move to another Iran server is switched off: the tunnel stays where it is.")
        return False

    try:
        p = route_plan(tunnel_id, cause)
    except (ValueError, RuntimeError) as e:
        autopilot._say(aid, f"No plan for a move: {str(e)[:160]}")
        return False

    extra = (f" Also moved to keep the DNS consistent: {', '.join(p['pulled'])}." if p["pulled"] else "") + (f" Problems: {'; '.join(p['problems'])}." if p["problems"] else "")
    tgt = p.get("target") or {}
    doctor.suggest("route", tunnel_id, next((t["name"] for t in p["tunnels"] if t["id"] == tunnel_id), p.get("old_tunnel") or str(tunnel_id)), p["server_id"], p["server"], tgt.get("id"), tgt.get("name") or "",
                   f"{p['why']}; no tunnel type works on this route" + (f" - {tgt['name']} ranks best for it" if tgt else " - no other Iran server could carry it right now"))

    if tgt:
        autopilot._say(aid, f"A route problem never moves the server by itself. Suggested: {_describe(p)}.{extra} Tunnel Doctor → Suggestions does it with one click.")
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
    left = [{"fqdn": r["fqdn"], "configs": r["configs"]} for r in recs if r not in dns and old_tunnel_id in r["tunnels"]]
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

        _notice(f"🌐 DNS: {len(done)} record(s) now point at {target['name']} ({target['ip']}).")

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
    _notice(f"🌐 DNS {fqdn}: {op} by hand ({old or '-'} → {new or '-'}).")
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

        _notice(f"↩ DNS {e['fqdn']}: " + ("the switch to the outbound forward" if op == "bridge" else f"the change by hand ({op})") + " was put back.")
        return dict(e)

    if not any(x["plan_id"] == e["plan_id"] and not x.get("restored") for x in s["dns_log"]):
        for h in s["history"]:
            if h["id"] == e["plan_id"]:
                h["undone"] = True              # every record of that failover is back

    _save(s)

    with session() as db:
        emit(db, "warn", "actions", f"DNS {e['fqdn']} points at {e['old']} ({e['server']}) again; the tunnels stay on {e['target']}.", e["server"])

    _notice(f"↩ DNS {e['fqdn']} points at {e['old']} ({e['server']}) again. The tunnels stay on {e['target']}.")
    return dict(e)


def undo(plan_id: str) -> list[dict]:
    """Point every record of a finished failover back at the old address (the tunnels are left where they are)."""
    s = _state()
    todo = [x["id"] for x in s["dns_log"] if x["plan_id"] == plan_id and not x.get("restored")]

    if not todo:
        raise ValueError("nothing to undo for this entry")

    return [restore(i) for i in todo]
