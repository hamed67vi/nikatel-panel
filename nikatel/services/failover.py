"""Failover of an Iran server: when one stays dead, its tunnels move to another Iran server and the ArvanCloud DNS
records that pointed at it are pointed at the new one.

  plan(server_id)   what would be done: the best replacement server, the tunnels to move, the DNS records to change
  check()           the job (every minute): a server offline for `confirm_minutes` -> a plan; mode "approve" waits for
                    a click in the panel (and says so on Telegram), mode "auto" runs it
  approve(id)       run the pending plan: move every tunnel (same name, ports and type), then - only when all moved - the DNS
  restore(log_id)   point ONE changed record back at the address it had before (the tunnels are left where they are)
  undo(plan_id)     the same for every record of one failover

Every record the failover re-points is written to `dns_log` (old and new address, which server, when), so the panel can
list the changes and put any of them back with one click.

The DNS is touched only after every tunnel moved: a record pointing at a server that does not carry its tunnels would
break the configs. Every step is told in the repair story and written as an event."""
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


def plan_for(server_id: int, with_dns: bool = True) -> dict:
    with session() as db:
        s = db.get(Server, server_id)

        if s is None or s.role != "iran":
            raise ValueError("choose an Iran server")

        info = {"server_id": s.id, "server": s.name, "ip": s.host}
        tunnels = [(t.id, t.name) for t in db.scalars(select(Tunnel).where(Tunnel.iran_server_id == s.id, Tunnel.enabled.is_(True)))]
        configs = [(c.name, str((c.meta or {}).get("address", ""))) for c in db.scalars(select(Config))]

    rankings = {}
    problems = []

    for tid, name in tunnels:
        try:
            rankings[tid] = [r for r in autopilot.ranking(tid) if r.get("online", True) is not False]
        except (ValueError, RuntimeError) as e:
            problems.append(f"{name}: {str(e)[:120]}")

    target = pick_target(rankings) if rankings else None

    if tunnels and not target:
        problems.append("no other Iran server can carry all of its tunnels")

    dns, dns_error = [], ""

    if with_dns and conf().get("api_key"):
        try:
            only = conf().get("domains") or None
            dns = arvan.find_by_ip(info["ip"], only)

            for d in dns:
                d["configs"] = [n for n, addr in configs if addr and addr.lower() == d["fqdn"].lower()]
        except RuntimeError as e:
            dns_error = str(e)
            problems.append(dns_error)

    new_ip = None

    if target:
        with session() as db:
            ts = db.get(Server, target["id"])
            new_ip = ts.host if ts else None

    return {**info, "tunnels": [{"id": tid, "name": n} for tid, n in tunnels], "target": ({"id": target["id"], "name": target.get("name"), "ip": new_ip} if target else None),
            "dns": dns, "dns_error": dns_error, "problems": problems, "made_at": int(time.time())}


# ------------------------------------------------------------------ state: the pending plan, history, who has been offline since when
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


# ------------------------------------------------------------------ the job
def check() -> None:
    c = conf()
    mode = c.get("mode", "off")

    if mode not in ("approve", "auto") or get_settings().read_only:
        return

    now = int(time.time())
    s = _state()

    with session() as db:
        down = {x.id: x.name for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(False)))}

    for sid in list(s["seen"]):
        if int(sid) not in down:
            s["seen"].pop(sid)                 # it answered again: the clock starts over

    changed = False

    for sid, name in down.items():
        since = s["seen"].setdefault(str(sid), now)
        changed = changed or since == now

        if now - since < int(c.get("confirm_minutes", 5)) * 60:
            continue

        if any(p["server_id"] == sid for p in s["pending"]) or any(h["server_id"] == sid and now - h["at"] < 6 * 3600 for h in s["history"]):
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
            run_plan(p)
            continue

        s["pending"].append(p)
        changed = True
        _notice(f"🔶 Iran server {name} has been down for {(now - since) // 60} min.\nPlan: move {len(p['tunnels'])} tunnel(s) to {p['target']['name'] if p['target'] else '—'}"
                f" and point {len(p['dns'])} DNS record(s) at {p['target']['ip'] if p['target'] else '—'}.\nOpen the panel → Tunnels → Smart repair to approve or dismiss.")

    if changed:
        _save(s)


# ------------------------------------------------------------------ doing it
def run_plan(p: dict) -> int:
    """Start the story: move the tunnels, then the DNS. Returns the repair story id."""
    if not p.get("target"):
        raise ValueError("there is no replacement server for this plan")

    def work(aid: int) -> None:
        autopilot._say(aid, f"Failover of {p['server']}: {len(p['tunnels'])} tunnel(s) → {p['target']['name']}, {len(p['dns'])} DNS record(s) → {p['target']['ip']}")
        moved = 0

        for t in p["tunnels"]:
            try:
                snap = autopilot._snapshot(t["id"])
                kind = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])

                if autopilot._replace(aid, t["id"], p["target"]["id"], snap["foreign_id"], kind, f"Failover: {p['server']} is down."):
                    moved += 1
            except (ValueError, RuntimeError) as e:
                autopilot._say(aid, f"{t['name']}: not moved: {str(e)[:160]}")

        done_dns, errors, logged = [], [], []

        if moved < len(p["tunnels"]):
            autopilot._say(aid, f"{moved} of {len(p['tunnels'])} tunnels moved: the DNS is NOT changed (records must not point at a server that does not carry all of its tunnels).")
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
        s["pending"] = [x for x in s["pending"] if x["server_id"] != p["server_id"]]
        s["history"].append({"id": p["id"], "at": int(time.time()), "server_id": p["server_id"], "server": p["server"], "ip": p["ip"], "target": p["target"], "moved": moved, "tunnels": len(p["tunnels"]), "dns": done_dns, "errors": errors, "ok": ok, "undone": False})
        _save(s)

        with session() as db:
            emit(db, "ok" if ok else "warn", "actions", f"Failover of {p['server']}: {moved}/{len(p['tunnels'])} tunnels moved to {p['target']['name']}, {len(done_dns)} DNS record(s) changed" + (f", {len(errors)} failed" if errors else "") + ".", p["server"])

        _notice(f"{'✅' if ok else '⚠'} Failover of {p['server']}: {moved}/{len(p['tunnels'])} tunnels moved to {p['target']['name']}, {len(done_dns)} DNS record(s) now point at {p['target']['ip']}." + (f" Errors: {'; '.join(errors)}" if errors else ""))
        autopilot._say(aid, "Done." if ok else "Done with problems: look at the lines above.", done=True, ok=ok)

    return autopilot._start(f"failover {p['server']}", work)


def approve(plan_id: str) -> int:
    s = _state()
    p = next((x for x in s["pending"] if x["id"] == plan_id), None)

    if p is None:
        raise ValueError("this plan is no longer pending")

    fresh = plan_for(p["server_id"])        # the world may have changed since the plan was made
    fresh["id"], fresh["down_since"] = p["id"], p.get("down_since")
    return run_plan(fresh)


def dismiss(plan_id: str) -> None:
    s = _state()
    gone = [x for x in s["pending"] if x["id"] == plan_id]

    if not gone:
        raise ValueError("this plan is no longer pending")

    s["pending"] = [x for x in s["pending"] if x["id"] != plan_id]
    s["history"].append({"id": plan_id, "at": int(time.time()), "server_id": gone[0]["server_id"], "server": gone[0]["server"], "ip": gone[0]["ip"], "target": gone[0].get("target"), "moved": 0, "tunnels": 0, "dns": [], "errors": [], "ok": None, "dismissed": True, "undone": False})
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
