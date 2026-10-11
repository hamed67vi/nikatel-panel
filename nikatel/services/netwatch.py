"""Disturbances of the network itself - an Iran server whose own connectivity is weak, a foreign server that many tunnels lose packets to, a route.

Two sources, combined in `report()`:
  * ACTIVE: every 5 minutes each online Iran server pings the internet (two anchors), two other Iran servers and two foreign servers (20 pings each,
    one SSH call per server). `net_level()` turns the three numbers into ok / weak / bad / down; a change is written to the events
    after two probes in a row agree (one lost ping must not raise an alarm), and kept in the server's data for the list.
  * PASSIVE: the tunnels already measured every minute. When most tunnels of one Iran server (or of one foreign server) lose packets at the same time, the
    trouble is that server's network, not the tunnels; a single lossy tunnel is just that pair's route.

The result is only shown and reported; the smart repair uses it to decide that rebuilding a tunnel would not help."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel, is_temp
from ..ssh import Node, SSHError, run_script
from .events import emit
from .pathscan import ANCHORS, parse_loss

log = get_logger("netwatch")
LOSS_TUNNEL = 10.0      # % loss on a tunnel that counts as "lossy" for the passive rule
SHARE = 0.6             # this share of a server's tunnels lossy together = the server's network
MIN_TUNNELS = 2         # ... with at least this many tunnels (one tunnel alone is a route)
LABEL = {"ok": "fine", "weak": "weak", "bad": "disturbed", "down": "cut off"}


TARGET_BAD = 25.0      # % loss toward one target that counts as bad


def _share_bad(values: list) -> bool:
    """Pure: most of the targets that answered are lossy. One bad target among two is that target's own trouble (it may be down, or filter ping), not the server's."""
    seen = [v for v in values if v is not None]
    return bool(seen) and (sum(1 for v in seen if v >= TARGET_BAD) / len(seen) > 0.5 if len(seen) >= 2 else seen[0] >= 60)


def net_level(anchor: float | None, domestic: list, foreign: list) -> str:
    """Pure: the state of an Iran server's connectivity: mean loss (%) toward the two internet anchors, and the losses toward each other Iran server
    and each foreign server (bad only when MOST of those targets lose packets)."""
    seen = [x for x in [anchor] + list(domestic) + list(foreign) if x is not None]

    if not seen:
        return "unknown"

    if anchor is not None and anchor >= 80:
        return "down"

    if (anchor or 0) >= 20 or _share_bad(domestic) or _share_bad(foreign):
        return "bad"

    return "weak" if max(seen) >= 8 else "ok"


def _mean(values: list) -> float | None:
    v = [x for x in values if x is not None]
    return round(sum(v) / len(v), 1) if v else None


def passive(servers: list[dict], tunnels: list[dict]) -> list[dict]:
    """Pure: disturbances read off the tunnels. servers [{id,name,role}], tunnels [{name,iran_id,foreign_id,loss,state}]."""
    out = []
    by = {"iran": {}, "foreign": {}}

    for t in tunnels:
        by["iran"].setdefault(t["iran_id"], []).append(t)
        by["foreign"].setdefault(t["foreign_id"], []).append(t)

    for s in servers:
        mine = by.get(s["role"], {}).get(s["id"], [])
        bad = [t for t in mine if t["state"] == "down" or (t["loss"] or 0) >= LOSS_TUNNEL]

        if len(mine) >= MIN_TUNNELS and len(bad) / len(mine) >= SHARE and len(bad) >= MIN_TUNNELS:
            who = "Iran server" if s["role"] == "iran" else "foreign server"
            out.append({"kind": s["role"] + "_server", "server": s["name"], "text": f"{who} {s['name']}: {len(bad)} of its {len(mine)} tunnels lose packets or are down at the same time - the trouble is its network, not the tunnels",
                        "tunnels": [t["name"] for t in bad]})

    return out


def probe_one(item) -> dict:
    sid, name, node, targets = item

    try:
        res = parse_loss(run_script(node, "loss_many", {"HOSTS": " ".join(dict.fromkeys(h for g in targets.values() for h in g))}, timeout=60))
    except (SSHError, ValueError) as e:
        return {"id": sid, "name": name, "error": str(e)[:100]}

    def mean(group):
        return _mean([(res.get(h) or {}).get("loss") for h in targets.get(group, [])])

    def each(group):
        return [(res.get(h) or {}).get("loss") for h in targets.get(group, [])]

    anchor, domestic, foreign = mean("anchor"), mean("domestic"), mean("foreign")
    return {"id": sid, "name": name, "anchor": anchor, "domestic": domestic, "foreign": foreign, "level": net_level(anchor, each("domestic"), each("foreign")),
            "foreign_raw": {h: (res.get(h) or {}).get("loss") for h in targets.get("foreign", [])}}


REACH_WINDOW = 900       # seconds of samples a foreign server's "route from Iran" verdict is made of
REACH_KEEP = 8


def reach_level(samples: list[dict], now: int) -> dict:
    """Pure: the route from Iran toward ONE foreign server, out of the pings several Iran servers sent it lately ([{iran, loss, at}]). "bad" when at least two
    Iran servers looked in the window and most of them lose TARGET_BAD % or more - one lossy Iran server is that server's own trouble, not the foreign one's."""
    fresh = [s for s in samples if s.get("loss") is not None and now - int(s.get("at", 0)) <= REACH_WINDOW]
    bad = [s for s in fresh if s["loss"] >= TARGET_BAD]

    if not fresh:
        return {"level": "unknown", "bad": 0, "total": 0}

    level = "bad" if len(fresh) >= 2 and len(bad) / len(fresh) > 0.5 else "weak" if max(s["loss"] for s in fresh) >= 8 else "ok"
    return {"level": level, "bad": len(bad), "total": len(fresh), "worst": max(s["loss"] for s in fresh), "from": sorted({s["iran"] for s in bad})}


def probe_all() -> None:
    """Job hook: look at every online Iran server's connectivity, keep the result, tell the events about a confirmed change."""
    with session() as db:
        online = [(s.id, s.name, s.host, s.role, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.enabled.is_(True), Server.online.is_(True)))]

    iran = [s for s in online if s[3] == "iran"]
    foreign = [s for s in online if s[3] == "foreign"]

    if not iran:
        return

    jobs = []

    for n, s in enumerate(iran):
        others = [o for o in iran if o[0] != s[0]]
        dom = [others[(n + k) % len(others)][2] for k in range(min(2, len(others)))] if others else []
        far = [foreign[(n * 4 + k) % len(foreign)][2] for k in range(min(4, len(foreign)))] if foreign else []      # four foreign servers per Iran server, rotating: every foreign server is seen from a few Iran servers each round
        jobs.append((s[0], s[1], s[4], {"anchor": list(ANCHORS), "domestic": dom, "foreign": far}))

    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(probe_one, jobs))

    now = int(time.time())

    for r in results:
        if "error" in r:
            continue

        with session() as db:
            s = db.get(Server, r["id"])

            if s is None:
                continue

            extra = dict(s.extra or {})
            prev = dict(extra.get("net") or {})
            shown = prev.get("level", "ok")
            streak = prev.get("streak", {"level": shown, "n": 0})
            streak = {"level": r["level"], "n": streak["n"] + 1} if streak.get("level") == r["level"] else {"level": r["level"], "n": 1}
            confirmed = r["level"] if streak["n"] >= 2 or r["level"] == "down" else shown      # two probes agree (a cut is told at once)
            extra["net"] = {"at": now, "anchor": r["anchor"], "domestic": r["domestic"], "foreign": r["foreign"], "level": confirmed, "now": r["level"], "streak": streak}
            s.extra = extra

            if confirmed != shown and (confirmed in ("bad", "down") or shown in ("bad", "down")):
                nums = f"internet {r['anchor']}%, other Iran servers {r['domestic']}%, foreign servers {r['foreign']}% lost"

                if confirmed in ("bad", "down"):
                    emit(db, "bad" if confirmed == "down" else "warn", "servers", f"Iran server {s.name}: its network is {LABEL[confirmed]} ({nums}).", s.name)
                else:
                    emit(db, "ok", "servers", f"Iran server {s.name}: its network is {LABEL[confirmed]} again.", s.name)

    # the other way round: how every foreign server is reached FROM Iran (the pings the Iran servers just sent it), so a foreign server with a bad route is named as the problem
    by_host: dict[str, list[dict]] = {}

    for r in results:
        for host, loss in (r.get("foreign_raw") or {}).items():
            by_host.setdefault(host, []).append({"iran": r["name"], "loss": loss, "at": now})

    for sid, name, host, _role, _node in foreign:
        with session() as db:
            s = db.get(Server, sid)

            if s is None:
                continue

            extra = dict(s.extra or {})
            prev = dict(extra.get("reach_ir") or {})
            samples = ([x for x in prev.get("samples", []) if now - int(x.get("at", 0)) <= REACH_WINDOW] + by_host.get(host, []))[-REACH_KEEP:]
            verdict = reach_level(samples, now)
            extra["reach_ir"] = {"at": now, "samples": samples, **verdict}
            s.extra = extra
            was, is_ = prev.get("level", "ok"), verdict["level"]

            if is_ == "bad" and was != "bad":
                emit(db, "warn", "servers", f"Foreign server {name}: the route from Iran is bad - {verdict['bad']} of {verdict['total']} Iran servers lose {TARGET_BAD:g} % or more of their packets toward it (worst {verdict['worst']} %). The tunnels on it suffer; the fault is this server or its provider.", name)
            elif was == "bad" and is_ in ("ok", "weak"):
                emit(db, "ok", "servers", f"Foreign server {name}: the route from Iran is fine again.", name)


def report() -> dict:
    """What is disturbed right now: the Iran servers whose own probes are bad, plus what the tunnels show together."""
    with session() as db:
        servers = [{"id": s.id, "name": s.name, "role": s.role, "net": dict((s.extra or {}).get("net") or {})} for s in db.scalars(select(Server).where(Server.enabled.is_(True)))]
        tunnels = [{"name": t.name, "iran_id": t.iran_server_id, "foreign_id": t.foreign_server_id, "state": t.state, "loss": (t.detail or {}).get("loss_pct")}
                   for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name)]

    items = []

    for s in servers:
        n = s["net"]

        if s["role"] == "iran" and n.get("level") in ("bad", "down"):
            items.append({"kind": "iran_server", "server": s["name"], "level": n["level"], "text": f"Iran server {s['name']}: its own connectivity is {LABEL[n['level']]} - internet {n.get('anchor')}%, other Iran servers {n.get('domestic')}%, foreign servers {n.get('foreign')}% lost", "tunnels": []})

    seen = {i["server"] for i in items}
    items += [i for i in passive(servers, tunnels) if i["server"] not in seen]
    return {"items": items, "iran": [{"name": s["name"], "level": (s["net"] or {}).get("level", "unknown"), "anchor": (s["net"] or {}).get("anchor"), "domestic": (s["net"] or {}).get("domestic"), "foreign": (s["net"] or {}).get("foreign"), "at": (s["net"] or {}).get("at")}
                        for s in servers if s["role"] == "iran"]}
