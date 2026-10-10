"""Failover of an Iran server: its tunnels move to another Iran server and the ArvanCloud DNS records that pointed at it are
pointed at the new one.

Two triggers:
  * the server is DEAD (no SSH answer for `confirm_minutes`): `check()` (the job, every minute) plans the move of ALL its tunnels
    and of every record that carries its address;
  * the server answers but its ROUTE is useless (GRE filtered, route cut, lossy, port blocked) and the smart repair found no other
    tunnel type that works: `after_ladder()` plans the move of the broken tunnels, plus the records that serve only them. A record
    that also serves a tunnel that stays would break it, so such tunnels are moved too (the plan says so).

  plan_for(server_id)      the dead-server plan  ·  route_plan(tunnel_id)  the route plan  ·  both: target, tunnels, DNS, problems
  approve / dismiss        mode "approve" waits for a click on Smart repair → Failover (a Telegram notice says so); mode "auto"
                           runs clean plans by itself
  restore(log_id)          point ONE changed record back at the address it had before (the tunnels are left where they are)
  undo(plan_id)            the same for every record of one failover

The target is the Iran server that ranks best for every moved tunnel (ping to the foreign server, free resources, how many tunnels
it already carries, free ports). The DNS is touched only after every tunnel moved. Every record the failover re-points is written to
`dns_log` (old and new address, which server, when), so the panel can list the changes and put any of them back with one click."""
from __future__ import annotations

import time

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Config, Server, Tunnel
from . import arvan, autopilot, telegram
from . import settings as st
from .events import emit

log = get_logger("failover")
ROUTE_CAUSES = ("gre_blocked", "route_cut", "lossy_route", "port_blocked")
REASONS = {"down": "the server does not answer",
           "gre_blocked": "GRE is filtered on its route and no other tunnel type works",
           "route_cut": "the route to the foreign server is cut",
           "lossy_route": "the route loses too many packets and no other tunnel type does better",
           "port_blocked": "the tunnel port is blocked on its route and no other tunnel type works"}


def conf() -> dict:
    return st.get("arvan")


# ------------------------------------------------------------------ the plan
def pick_target(rankings: dict[int, list[dict]]) -> dict | None:
    """Pure: {tunnel_id: [candidate servers best first]} -> the one server that can carry them all with the best total rank."""
    if not rankings:
        return None

    common = None

    for lst in rankings.values():
        ids = {c["id"] for c in lst}
        common = ids if common is None else common & ids

    if not common:
        return None

    def total(sid: int) -> int:
        return sum(next(i for i, c in enumerate(lst) if c["id"] == sid) for lst in rankings.values())

    best = min(common, key=total)
    return next(c for lst in rankings.values() for c in lst if c["id"] == best)


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


def _records(ip: str, tunnels: list[dict]) -> tuple[list[dict], str]:
    """The CDN A records that carry `ip`, each with the configs that use its name and the tunnels (of this server) those configs go through."""
    if not conf().get("api_key"):
        return [], ""

    with session() as db:
        configs = [(c.name, str((c.meta or {}).get("address", "")).lower(), int((c.meta or {}).get("port") or 0)) for c in db.scalars(select(Config))]

    try:
        recs = arvan.find_by_ip(ip, conf().get("domains") or None)
    except RuntimeError as e:
        return [], str(e)

    for r in recs:
        mine = [(n, p) for n, a, p in configs if a == r["fqdn"].lower()]
        r["configs"] = [n for n, _ in mine]
        r["tunnels"] = sorted({t["id"] for t in tunnels if any(p in t["ports"] for _, p in mine)})

    return recs, ""


def _make_plan(server_id: int, move: set[int] | None, reason: str) -> dict:
    """move=None: every tunnel of the server (it is dead). Otherwise the given broken tunnels, widened so that the DNS stays consistent."""
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
    rankings, problems = {}, []

    for t in moving:
        try:
            rankings[t["id"]] = [r for r in autopilot.ranking(t["id"]) if r.get("online", True) is not False]
        except (ValueError, RuntimeError) as e:
            problems.append(f"{t['name']}: {str(e)[:120]}")

    target = pick_target(rankings) if rankings else None

    if moving and not target:
        problems.append("no other Iran server can carry all of these tunnels")

    if dns_error:
        problems.append(dns_error)

    new_ip = None

    if target:
        with session() as db:
            ts = db.get(Server, target["id"])
            new_ip = ts.host if ts else None

    return {**info, "reason": reason, "why": REASONS.get(reason, reason), "whole": whole,
            "tunnels": [{"id": t["id"], "name": t["name"]} for t in moving], "move": sorted(moved),
            "pulled": [t["name"] for t in moving if t["id"] not in asked],
            "target": ({"id": target["id"], "name": target.get("name"), "ip": new_ip} if target else None),
            "dns": [{k: v for k, v in r.items() if k != "tunnels"} for r in dns], "left_alone": left,
            "dns_error": dns_error, "problems": problems, "made_at": int(time.time())}


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


# ------------------------------------------------------------------ state: the pending plans, history, DNS changes, who has been offline since when
def _state() -> dict:
    s = st.get("failover")
    s.setdefault("seen", {})
    s.setdefault("pending", [])
    s.setdefault("history", [])
    s.setdefault("dns_log", [])
    return s


def _save(s: dict) -> None:
    st.put("failover", {"seen": s["seen"], "pending": s["pending"], "history": s["history"][-30:], "dns_log": s["dns_log"][-100:]})


def state() -> dict:
    s = _state()
    return {"mode": conf().get("mode", "off"), "confirm_minutes": conf().get("confirm_minutes", 5), "pending": s["pending"], "history": list(reversed(s["history"]))[:15],
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
            + (f"\nProblems: {'; '.join(p['problems'])}" if p.get("problems") else "") + "\nOpen the panel → Tunnels → Smart repair → Failover to approve or dismiss.")


def _busy(s: dict, sid: int, now: int) -> bool:
    """A plan for this server is waiting, or one was handled in the last 6 hours."""
    return any(p["server_id"] == sid and p.get("reason") != "replace" for p in s["pending"]) or any(h["server_id"] == sid and h.get("reason") != "replace" and now - h["at"] < 6 * 3600 for h in s["history"])


# ------------------------------------------------------------------ the job: dead servers
def check() -> None:
    c = conf()
    mode = c.get("mode", "off")

    if mode not in ("approve", "auto") or get_settings().read_only:
        return

    now = int(time.time())
    s = _state()

    with session() as db:
        down = {x.id: x.name for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(False)))}

    stale = [sid for sid in s["seen"] if int(sid) not in down]

    for sid in stale:
        s["seen"].pop(sid)                     # it answered again: the clock starts over

    changed = bool(stale)                      # a forgotten entry must be saved too, or the next outage would skip the waiting time

    for sid, name in down.items():
        since = s["seen"].setdefault(str(sid), now)
        changed = changed or since == now

        if now - since < int(c.get("confirm_minutes", 5)) * 60 or _busy(s, sid, now):
            continue

        try:
            p = plan_for(sid)
        except (ValueError, RuntimeError):
            log.warning("failover plan for %s failed", name, exc_info=True)
            continue

        if not p["tunnels"] and not p["dns"]:
            continue

        p["id"] = f"{sid}-{now}"
        p["down_since"] = since

        if mode == "auto" and p["target"] and not p["problems"]:
            _save(s)
            run_plan(p)                        # one server per tick; the story writes the state itself when it is done
            return

        s["pending"].append(p)
        changed = True
        _notify_plan(p, f"Iran server {name} has been down for {(now - since) // 60} min.")

    if changed:
        _save(s)


# ------------------------------------------------------------------ doing it
def _work(aid: int, p: dict, finish: bool = True) -> bool:
    """Inside story `aid`: move the tunnels, then - only when all moved - the DNS. Returns True when everything was done."""
    autopilot._say(aid, f"Failover of {p['server']} ({p['why']}): {len(p['tunnels'])} tunnel(s) → {p['target']['name']}, {len(p['dns'])} DNS record(s) → {p['target']['ip']}")
    moved = 0

    for t in p["tunnels"]:
        try:
            snap = autopilot._snapshot(t["id"])
            kind = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])

            if autopilot._replace(aid, t["id"], p["target"]["id"], snap["foreign_id"], kind, f"Failover: {p['why']}."):
                moved += 1
        except (ValueError, RuntimeError) as e:
            autopilot._say(aid, f"{t['name']}: not moved: {str(e)[:160]}")

    done_dns, errors, logged = [], [], []

    if moved < len(p["tunnels"]):
        autopilot._say(aid, f"{moved} of {len(p['tunnels'])} tunnels moved: the DNS is NOT changed (records must not point at a server that does not carry all of their tunnels).")
    else:
        for d in p["dns"]:
            try:
                done_dns.append(arvan.apply(d["domain"], d["id"], p["ip"], p["target"]["ip"]))
                logged.append({"id": f"{p['id']}:{d['id']}", "at": int(time.time()), "plan_id": p["id"], "server": p["server"], "target": p["target"]["name"], "domain": d["domain"],
                               "record_id": str(d["id"]), "fqdn": d["fqdn"], "old": p["ip"], "new": p["target"]["ip"], "configs": d.get("configs", []), "restored": False, "restored_at": None})
                autopilot._say(aid, f"DNS {d['fqdn']}: {p['ip']} → {p['target']['ip']}")
            except RuntimeError as e:
                errors.append(str(e)[:160])
                autopilot._say(aid, f"DNS {d['fqdn']}: not changed: {str(e)[:160]}")

    ok = moved == len(p["tunnels"]) and not errors
    s = _state()
    s["dns_log"].extend(logged)
    s["pending"] = [x for x in s["pending"] if x["id"] != p["id"]]
    s["history"].append({"id": p["id"], "at": int(time.time()), "server_id": p["server_id"], "server": p["server"], "ip": p["ip"], "reason": p.get("reason", "down"), "target": p["target"],
                         "moved": moved, "tunnels": len(p["tunnels"]), "dns": done_dns, "errors": errors, "ok": ok, "undone": False})
    _save(s)

    with session() as db:
        emit(db, "ok" if ok else "warn", "actions", f"Failover of {p['server']}: {moved}/{len(p['tunnels'])} tunnels moved to {p['target']['name']}, {len(done_dns)} DNS record(s) changed" + (f", {len(errors)} failed" if errors else "") + ".", p["server"])

    _notice(f"{'✅' if ok else '⚠'} Failover of {p['server']}: {moved}/{len(p['tunnels'])} tunnels moved to {p['target']['name']}, {len(done_dns)} DNS record(s) now point at {p['target']['ip']}." + (f" Errors: {'; '.join(errors)}" if errors else ""))

    if finish:
        autopilot._say(aid, "Done." if ok else "Done with problems: look at the lines above.", done=True, ok=ok)

    return ok


def run_plan(p: dict) -> int:
    """Start the story in the background: move the tunnels, then the DNS. Returns the repair story id."""
    if not p.get("target"):
        raise ValueError("there is no replacement server for this plan")

    return autopilot._start(f"failover {p['server']}", _work, p)


def after_ladder(aid: int, tunnel_id: int, cause: str) -> bool:
    """Called by the smart repair when a route problem survived every step: the move to another Iran server, with the DNS.
    Mode "approve": the plan waits on Smart repair → Failover (a Telegram notice says so). Mode "auto": done now, inside this story.
    Returns True when the tunnel was moved."""
    mode = conf().get("mode", "off")

    if mode not in ("approve", "auto"):
        autopilot._say(aid, "Failover is off (Smart repair → Failover · CDN → settings): the tunnel is not moved to another Iran server.")
        return False

    try:
        p = route_plan(tunnel_id, cause)
    except (ValueError, RuntimeError) as e:
        autopilot._say(aid, f"No failover plan: {str(e)[:160]}")
        return False

    s = _state()

    if any(x["server_id"] == p["server_id"] and x.get("reason") != "replace" for x in s["pending"]):
        autopilot._say(aid, f"A failover plan for {p['server']} is already waiting for approval on Smart repair → Failover.")
        return False

    p["id"] = f"{p['server_id']}-{int(time.time())}"
    extra = (f". Also moved to keep the DNS consistent: {', '.join(p['pulled'])}" if p["pulled"] else "") + (f". Problems: {'; '.join(p['problems'])}" if p["problems"] else "")

    if not p["target"]:
        autopilot._say(aid, "No other Iran server can carry " + (", ".join(t["name"] for t in p["tunnels"]) or "this tunnel") + extra)
        return False

    if mode == "auto" and not p["problems"]:
        autopilot._say(aid, "Failover (automatic): " + _describe(p) + extra)
        return _work(aid, p, finish=False)

    s["pending"].append(p)
    _save(s)
    autopilot._say(aid, "Failover plan prepared, waiting for your approval on Smart repair → Failover · CDN: " + _describe(p) + extra)
    _notify_plan(p, f"{p['server']}: {p['why']}.")
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


def after_replace(aid: int, p: dict) -> None:
    """After a manual replacement built fine: the DNS plan is applied now (mode auto), kept for approval (approve) or only told (off)."""
    mode = conf().get("mode", "off")

    if not p["dns"]:
        autopilot._say(aid, f"DNS: no CDN record served only {p['old_tunnel']}" + (f" (left alone, they also serve tunnels that stay: {', '.join(d['fqdn'] for d in p['left_alone'])})" if p["left_alone"] else "")
                       + (f"; {p['dns_error']}" if p.get("dns_error") else "") + ".")
        return

    names = ", ".join(d["fqdn"] for d in p["dns"])

    if mode == "auto":
        autopilot._say(aid, f"DNS (automatic): {names} → {p['target']['ip']}")
        _work(aid, p, finish=False)
    elif mode == "approve":
        s = _state()
        s["pending"] = [x for x in s["pending"] if x["id"] != p["id"]]
        s["pending"].append(p)
        _save(s)
        autopilot._say(aid, f"DNS: {names} still point at {p['ip']}; the plan to point them at {p['target']['ip']} waits for your approval on Smart repair → Failover · CDN.")
        _notify_plan(p, f"{p['old_tunnel']} was replaced by a tunnel on {p['target']['name']}.")
    else:
        autopilot._say(aid, f"DNS: {names} still point at {p['ip']} (the failover is off: change them yourself, or switch it on under Smart repair → Failover · CDN).")


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


def restore(log_id: str) -> dict:
    """Point one re-pointed record back at the address it had before the failover. The tunnels stay where they are."""
    s = _state()
    e = next((x for x in s["dns_log"] if x["id"] == log_id), None)

    if e is None:
        raise ValueError("this DNS change is not in the list any more")

    if e.get("restored"):
        raise ValueError(f"{e['fqdn']} already points at {e['old']} again")

    arvan.apply(e["domain"], e["record_id"], e["new"], e["old"])
    e["restored"], e["restored_at"] = True, int(time.time())

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
