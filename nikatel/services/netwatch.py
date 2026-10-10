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


def net_level(anchor: float | None, domestic: float | None, foreign: float | None) -> str:
    """Pure: the state of an Iran server's connectivity from its mean loss (%) toward the internet, other Iran servers and foreign servers."""
    known = [x for x in (anchor, domestic, foreign) if x is not None]

    if not known:
        return "unknown"

    if anchor is not None and anchor >= 80:
        return "down"

    if (anchor or 0) >= 20 or (foreign or 0) >= 25 or (domestic or 0) >= 25:
        return "bad"

    return "weak" if max(known) >= 8 else "ok"


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

    anchor, domestic, foreign = mean("anchor"), mean("domestic"), mean("foreign")
    return {"id": sid, "name": name, "anchor": anchor, "domestic": domestic, "foreign": foreign, "level": net_level(anchor, domestic, foreign)}


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
        far = [foreign[(n + k) % len(foreign)][2] for k in range(min(2, len(foreign)))] if foreign else []
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
