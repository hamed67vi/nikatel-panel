"""Disturbances of the network itself - an Iran server whose own connectivity is weak, a foreign server that many tunnels lose packets to, a route.

Two sources, combined in `report()`:
  * ACTIVE: every 5 minutes each online Iran server pings the internet (two anchors), two other Iran servers and two foreign servers (20 pings each,
    one SSH call per server). `net_level()` turns the three numbers into ok / weak / bad / down; a change is written to the events
    after two probes in a row agree (one lost ping must not raise an alarm), and kept in the server's data for the list.
  * PASSIVE: the tunnels already measured every minute. When most tunnels of one Iran server (or of one foreign server) lose packets at the same time, the
    trouble is that server's network, not the tunnels; a single lossy tunnel is just that pair's route.

The result is shown and reported, and (1.69.292) it is ACTED on: an Iran server that is ON but whose own network is cut off - it reaches neither the internet nor the foreign servers - counts as dead for the tunnels that
stand on it (`is_netcut`): nothing is restarted or rebuilt for them, and after one minute the failover moves them to another Iran server. The round of five minutes is too slow for that, so the first tunnel of an Iran server that
is down gets a QUICK look at its server (`quick_look`, one per QUICK_GAP seconds per server) before anything is done to it."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel, is_temp
from ..ssh import Node, SSHError, run_script
from . import folders
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


NET_FRESH = 240          # (1.69.292) seconds a look at an Iran server's own network stays good enough to act on
FOREIGN_LOST = 50.0      # ... the foreign servers it pinged lost at least this % together: with a dead internet that is a cut (a server whose internet ping is only filtered still reaches them)
QUICK_GAP = 45           # one quick look per Iran server in this many seconds
QUICK_DOWN_FOR = 10      # a tunnel that has been down this long makes its Iran server worth a quick look
QUICK_THREADS = 6
_QUICK: dict[int, float] = {}
_QUICK_LOCK = threading.Lock()


def is_netcut(net: dict | None, now: float) -> bool:
    """Pure: does the last look at an Iran server's own network (`extra["net"]`) say it is CUT OFF - its newest look is a "down" (the internet anchors lost 80 % or more), the foreign servers it pinged lost at least
    FOREIGN_LOST % - and is the look young enough (NET_FRESH) to act on? An old look, a look without foreign servers (nothing to tell a cut from a filtered ping) and anything but "down" are not a cut."""
    if not net or (net.get("now") or net.get("level")) != "down":
        return False

    if now - int(net.get("at") or 0) > NET_FRESH:
        return False

    far = net.get("foreign")
    return far is not None and float(far) >= FOREIGN_LOST


def _targets(sid: int, iran: list[tuple[int, str]], foreign: list[tuple[int, str]], may) -> dict:
    """Pure: what a quick look pings from one Iran server: the two internet anchors, up to two other Iran servers and up to three foreign servers, each only when the Servers folders allow the probe (`may(sid, target_id)`)."""
    return {"anchor": list(ANCHORS), "domestic": [h for i, h in iran if i != sid and may(sid, i)][:2], "foreign": [h for i, h in foreign if may(sid, i)][:3]}


def quick_look(sid: int, now: float | None = None) -> bool:
    """Look NOW at the network of one Iran server (the same pings as the round of five minutes), keep the result in its data and tell the events when it changes. Once per QUICK_GAP seconds per server; a server that is not on,
    or that cannot be asked, is not looked at (False) - the pulse and the vantage already say whether it is on."""
    now = time.time() if now is None else now

    with _QUICK_LOCK:
        if now - _QUICK.get(sid, 0) < QUICK_GAP:
            return False

        _QUICK[sid] = now

    with session() as db:
        s = db.get(Server, sid)

        if s is None or s.role != "iran" or not s.enabled or s.online is not True:
            return False

        name, node = s.name, Node(s.host, s.ssh_user, s.ssh_port)
        walls = folders.load()
        iran = [(x.id, x.host) for x in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)).order_by(Server.id))]
        foreign = [(x.id, x.host) for x in db.scalars(select(Server).where(Server.role == "foreign", Server.enabled.is_(True), Server.online.is_(True)).order_by(Server.id))]

    r = probe_one((sid, name, node, _targets(sid, iran, foreign, walls.may_probe)))

    if "error" in r:
        return False

    _store_result(r, int(now))
    return True


def quick_looks(sids) -> None:
    """Quick looks at several Iran servers at the same time; nothing here may stop the job that asked (a look that fails is only a log line)."""
    ids = sorted({int(x) for x in sids})

    if not ids:
        return

    def one(sid: int) -> None:
        try:
            quick_look(sid)
        except Exception:
            log.exception("netwatch: the quick look at server %s", sid)

    with ThreadPoolExecutor(max(1, min(QUICK_THREADS, len(ids)))) as ex:
        list(ex.map(one, ids))


def look_suspects(now: float | None = None) -> None:
    """Job hook (the failover tick, every 10 seconds): every Iran server that has an enabled tunnel down for QUICK_DOWN_FOR seconds gets a quick look - so the verdict is there in seconds, not at the next round."""
    now = time.time() if now is None else now

    with session() as db:
        ids = {t.iran_server_id for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True), Tunnel.state == "down")) if now - int(t.state_since or 0) >= QUICK_DOWN_FOR}

    quick_looks(ids)


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


def _store_result(r: dict, now: int) -> None:
    """Keep one look at an Iran server's network in its data (two looks in a row agree before a change is shown; a cut is told at once) and tell the events about a confirmed change."""
    with session() as db:
        s = db.get(Server, r["id"])

        if s is None:
            return

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

            if confirmed == "down":          # (1.69.292: the server is ON - the words say so, and say what the Doctor does about it)
                emit(db, "bad", "servers", f"Iran server {s.name} is on, but its network is cut off ({nums}): its tunnels cannot work through it. The Doctor moves them to another Iran server when it stays like this for a minute.", s.name)
            elif confirmed == "bad":
                emit(db, "warn", "servers", f"Iran server {s.name}: its network is {LABEL[confirmed]} ({nums}).", s.name)
            else:
                emit(db, "ok", "servers", f"Iran server {s.name}: its network is {LABEL[confirmed]} again.", s.name)


def probe_all() -> None:
    """Job hook: look at every online Iran server's connectivity, keep the result, tell the events about a confirmed change."""
    with session() as db:
        online = [(s.id, s.name, s.host, s.role, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.enabled.is_(True), Server.online.is_(True)))]

    iran = [s for s in online if s[3] == "iran"]
    foreign = [s for s in online if s[3] == "foreign"]

    if not iran:
        return

    jobs = []

    walls = folders.load()          # (1.69.286: a server of a private Servers folder pings the servers of its own folder only; the fleet's servers may look at it)

    for n, s in enumerate(iran):
        others = [o for o in iran if o[0] != s[0] and walls.may_probe(s[0], o[0])]
        dom = [others[(n + k) % len(others)][2] for k in range(min(2, len(others)))] if others else []
        seen = [o for o in foreign if walls.may_probe(s[0], o[0])]
        far = [seen[(n * 4 + k) % len(seen)][2] for k in range(min(4, len(seen)))] if seen else []      # four foreign servers per Iran server, rotating: every foreign server is seen from a few Iran servers each round
        jobs.append((s[0], s[1], s[4], {"anchor": list(ANCHORS), "domestic": dom, "foreign": far}))

    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(probe_one, jobs))

    now = int(time.time())

    for r in results:
        if "error" in r:
            continue

        _store_result(r, now)

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
