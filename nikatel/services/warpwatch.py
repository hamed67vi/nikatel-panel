"""The WARP watch (1.69.228; patient and loud since 1.69.239). An AI config (AI-1, AI-2 ...) leaves the world through a Cloudflare WARP account in the Xray of its PasarGuard node. When that Xray keeps a stuck WARP session the config
still connects and still opens Instagram and Cloudflare pages, but Google, Telegram, YouTube and X time out - and nothing else notices: on 2026-11-05 AI-2 stayed like this for 85 minutes (03:40 - 05:06) and ONE reconnect of its node
(4 s) cured it. So: an AI config the monitor shows down for DOWN_FOR seconds - both vantages agree, the monitor already weighed the witnesses - whose tunnel is not down and whose node is connected gets its node reconnected; every
reconnect is an event.
How often (1.69.239, after 2026-11-06: the cure held only minutes, the three tries of the day were gone by 08:46 and the watch went quiet for the rest of the day while the owner, whose Telegram alerts were dead, never heard): up to
PER_DAY reconnects of one node in 24 hours, each wait longer than the one before (COOLDOWNS: 15, 30, 60, then 120 minutes) - a cure that does not hold is tried less and less often, not never. When the day's tries are used and the config
is still down the watch says so with a `bad` event and repeats it every REMIND seconds until the config is back, and every config the watch is working on carries a FLAG (settings `warpwatch`.flags, drawn at the top of the Tunnel Doctor page):
"reconnecting" (tries used, next try), "needs a hand" (the day's tries are used), "not watched" (it cannot be placed on a node). A config that is up for BACK_FOR seconds drops its flag and an `info` event says it recovered.
The same goes for a config that keeps going up and down (1.69.229): on the afternoon of 2026-11-05 AI-2 passed only 6-8 % of its tests from 17:00 but an odd good test reset the seven minutes every time, so nothing happened until 17:53. Now
an AI config also counts as down when at most FLAP_UP_MAX of its samples of the last FLAP_WINDOW seconds are up (at least FLAP_MIN_ROWS samples, and not when the three newest are all up: it just came back).
The placement (config -> tunnel -> foreign server -> PasarGuard node) is the one `purpose` uses; nothing is guessed: a config that cannot be placed on exactly one node is not touched (and is flagged "not watched"). `decide` is pure and tested."""
from __future__ import annotations

import re
import time

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Config, ConfigSample, Panel, Server, Tunnel, is_temp
from . import dnscache, panels, userq
from . import settings as st
from .events import emit

log = get_logger("warpwatch")
EVERY = 60
DOWN_FOR = 420            # seconds an AI config is down (as the monitor shows it) before its node is touched
COOLDOWNS = (900, 1800, 3600, 7200)          # seconds one node waits after its 1st, 2nd, 3rd, 4th-and-later reconnect of the last 24 hours before it is reconnected again
PER_DAY = 6               # reconnects of one node in 24 hours
REMIND = 10800            # seconds between two "needs a look by hand" events while the config stays down (it was once a day; the owner's Telegram alerts were dead, the page and the events are all he has)
BACK_FOR = 600            # seconds an AI config must stay up before its flag goes and an event says it recovered
FLAP_WINDOW = 900         # seconds looked back at a config that keeps going up and down
FLAP_UP_MAX = 0.34        # ... it counts as down when at most this share of the samples of that window is up
FLAP_MIN_ROWS = 6         # ... judged on at least this many samples (a test every two minutes, a witness adds its own)
_AI = re.compile(r"^AI-?\d", re.IGNORECASE)


def is_ai(name: str) -> bool:
    return bool(_AI.match(str(name or "").strip()))


def cooldown_for(n: int) -> int:
    """Pure. Seconds a node waits after its n-th reconnect of the last 24 hours (n >= 1): 15 minutes, then 30, 60 and 120 - a cure that does not hold is tried less and less often."""
    return COOLDOWNS[min(max(int(n), 1), len(COOLDOWNS)) - 1]


def flapping(samples: list[tuple[int, bool]], now: int) -> bool:
    """Pure. samples: [(time, up)] of one config. True when the last FLAP_WINDOW seconds hold at least FLAP_MIN_ROWS of them, at most FLAP_UP_MAX of those are up and the three newest are not all up (a config that just came back is left alone)."""
    win = sorted((int(t), bool(u)) for t, u in samples if now - FLAP_WINDOW < int(t) <= now)

    if len(win) < FLAP_MIN_ROWS or sum(u for _, u in win) / len(win) > FLAP_UP_MAX:
        return False

    return not all(u for _, u in win[-3:])


def decide(down: list[dict], where: dict[str, dict], done: dict[str, list[int]], now: int) -> tuple[dict[str, list[str]], list[tuple[str, str, str]]]:
    """Pure. down: [{"name", "since"}] AI configs the monitor shows down; where: {config name: {"key", "node_status", "tunnel_state"}} (the placement); done: {node key: [times of its reconnects]}.
    -> ({node key: [config names]} to reconnect now, [(config name, why it is left alone, flag state)]). The flag state: "working" (the cooldown runs: a later try is allowed), "hand" (the day's tries are used up),
    "held" (it cannot be placed on a node: nobody watches it) or "" (its tunnel or its node is the matter: the Doctor and the panel deal with that, nothing to flag)."""
    todo: dict[str, list[str]] = {}
    held: list[tuple[str, str, str]] = []

    for d in down:
        name = d["name"]

        if now - int(d.get("since") or now) < DOWN_FOR:
            continue                                                   # not down for long: a lost test, a restart - nothing to say

        w = where.get(name)

        if not w:
            held.append((name, "it cannot be placed on exactly one node", "held"))
        elif w.get("node_status") != "connected":
            held.append((name, "its node is not connected (the panel reconnects a node by itself)", ""))
        elif w.get("tunnel_state") == "down":
            held.append((name, "its tunnel is down: the node is not the cause", ""))
        else:
            mine = [t for t in done.get(w["key"], []) if now - int(t) < 86400]

            if mine and now - max(mine) < cooldown_for(len(mine)):
                held.append((name, f"its node was reconnected {(now - max(mine)) // 60} minutes ago", "working"))
            elif len(mine) >= PER_DAY:
                held.append((name, f"its node was reconnected {len(mine)} times in the last 24 hours", "hand"))
            else:
                todo.setdefault(w["key"], []).append(name)

    return todo, held


def flag_of(state: str, why: str, w: dict | None, mine: list[int], since: int) -> dict:
    """Pure. The flag of one config for the Doctor page: state, the node's name, since when it is down, the reconnects of the last 24 hours (`tries` of `max`), when the last one was, when the next one is allowed (0: none will follow), and the sentence `why`."""
    last = max(mine) if mine else 0
    nxt = last + cooldown_for(len(mine)) if mine and len(mine) < PER_DAY else 0
    return {"state": state, "node": str((w or {}).get("node_name", "")), "since": int(since), "tries": len(mine), "max": PER_DAY, "last": int(last), "next": int(nxt), "why": why}


def flags_view(conf: dict | None = None) -> list[dict]:
    """The flags for the page and the API: [{"name", "state", "node", "since", "tries", "max", "last", "next", "why"}], by name; none while the watch is off."""
    d = conf if conf is not None else st.get("warpwatch")

    if not d.get("on", True):
        return []

    return [{"name": n, **f} for n, f in sorted((d.get("flags") or {}).items())]


def _where(cfgs: list[dict]) -> dict[str, dict]:
    """The placement of the configs: {config name: {"key", "panel_id", "node_id", "node_name", "node_status", "tunnel_state"}}; what cannot be placed for sure is left out."""
    records = list(dnscache.CACHE.get("rows") or [])

    if not records:
        return {}

    with session() as db:
        tunnels = [{"id": t.id, "name": t.name, "iran_id": t.iran_server_id, "ports": {int(p) for p in (t.forwarded_ports or []) if str(p).isdigit()}}
                   for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name)]
        info = {t.id: (t.state, t.foreign.host) for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))}
        servers = [{"id": s.id, "name": s.name, "host": s.host} for s in db.scalars(select(Server).where(Server.role == "iran"))]
        nodes = [(p.id, n) for p in db.scalars(select(Panel).where(Panel.enabled.is_(True), Panel.type == "pasarguard")) for n in (p.nodes or []) if isinstance(n, dict)]

    rows = [{"name": c["name"], "tunnel": c.get("tunnel") or "", "address": (c.get("meta") or {}).get("address") or "", "port": (c.get("meta") or {}).get("port") or 0,
             "sni": (c.get("meta") or {}).get("sni") or "", "host": (c.get("meta") or {}).get("host") or ""} for c in cfgs]
    out: dict[str, dict] = {}

    for name, w in userq.map_configs(rows, tunnels, servers, records).items():
        state, fhost = info.get(w["tunnel"], ("", ""))
        hits = [(pid, n) for pid, n in nodes if n.get("address") == fhost and n.get("id") is not None and not str(n.get("name", "")).upper().startswith("ECO")]          # (the economy node of the same host is another Xray)

        if len(hits) == 1:
            pid, n = hits[0]
            out[name] = {"key": f"{pid}:{n['id']}", "panel_id": pid, "node_id": n["id"], "node_name": str(n.get("name", "")), "node_status": str(n.get("status", "")).lower(), "tunnel_state": state}

    return out


def run() -> None:
    """Job (every minute): see the module text. Cheap while no AI config is down."""
    if jobs.stopping():
        return

    conf = st.get("warpwatch")

    if not conf.get("on", True) or get_settings().read_only:
        return

    now = int(time.time())

    down: list[dict] = []
    stable: set[str] = set()          # AI configs that have been up for BACK_FOR seconds
    names: set[str] = set()

    with session() as db:
        for c in db.scalars(select(Config).where(Config.enabled.is_(True))):
            if not is_ai(c.name):
                continue

            names.add(c.name)

            if c.status not in ("down", "up"):
                continue

            item = {"name": c.name, "since": c.status_since, "meta": c.meta or {}, "tunnel": c.tunnel}

            if c.status == "down" and now - int(c.status_since or now) >= DOWN_FOR:
                down.append(item)
            elif c.status == "up" and now - int(c.status_since or now) >= BACK_FOR:
                stable.add(c.name)
            elif flapping([(r.ts, r.up) for r in db.execute(select(ConfigSample.ts, ConfigSample.up).where(ConfigSample.config_id == c.id, ConfigSample.ts > now - FLAP_WINDOW))], now):
                down.append({**item, "since": now - FLAP_WINDOW, "flap": True})          # (up now and then, down most of the time: the seven minutes never run out)

    done = {k: list(v) for k, v in (conf.get("done") or {}).items()}
    told = dict(conf.get("told") or {})
    old = conf.get("flags") or {}
    flags = {k: dict(v) for k, v in old.items() if k in names}          # (a config that was deleted or switched off takes its flag with it)
    dirty = len(flags) != len(old)

    for name in [n for n in flags if n in stable]:          # it has stayed up: the flag goes, and an event says so
        f = flags.pop(name)
        told.pop(name, None)
        dirty = True

        extra = f" (node {f.get('node') or '?'} was reconnected {f['tries']} time(s) in the last 24 hours)" if f.get("tries") else ""

        with session() as db:
            emit(db, "info", "configs", f"{name} is back up and has stayed up for {BACK_FOR // 60}+ minutes{extra}.", name)

    if not down:
        if dirty:
            st.put("warpwatch", {"told": told, "flags": flags})

        return

    where = _where(down)
    todo, held = decide(down, where, done, now)
    states = {n: ("working", "") for names_ in todo.values() for n in names_}
    states.update({n: (s, why) for n, why, s in held if s})
    flapped = {d["name"] for d in down if d.get("flap")}

    for key, names_ in todo.items():
        w = where.get(names_[0])

        if not w:
            continue

        try:
            panels.node_reconnect(panels.cred_for(w["panel_id"]), w["node_id"])
        except (panels.PanelError, ValueError) as e:
            log.warning("warp watch: the reconnect of node %s failed: %s", w["node_name"], str(e)[:100])
            continue

        done[key] = [t for t in done.get(key, []) if now - int(t) < 86400] + [now]

        lead = f"has been going up and down for {FLAP_WINDOW // 60} minutes (most of its tests fail)" if all(n in flapped for n in names_) else f"has been down for {DOWN_FOR // 60}+ minutes"

        with session() as db:
            emit(db, "warn", "configs", f"{', '.join(names_)} {lead} while its tunnel and its node are up: the WARP session of the node's Xray is probably stuck. Node {w['node_name']} reconnected (a few seconds of its connections).", names_[0])

    for d in down:
        got = states.get(d["name"])

        if not got:
            continue                                  # (its tunnel or its node is the matter: the flag it had stays as it was)

        w = where.get(d["name"]) or {}
        mine = [t for t in done.get(w.get("key", ""), []) if now - int(t) < 86400]
        flags[d["name"]] = flag_of(got[0], got[1], w, mine, int((flags.get(d["name"]) or {}).get("since") or d.get("since") or now))

        if got[0] == "hand" and now - int(told.get(d["name"], 0)) >= REMIND:
            told[d["name"]] = now

            with session() as db:
                emit(db, "bad", "configs", f"Config {d['name']} is still down: {got[1]}. The WARP watch stops here - it needs a look by hand (the WARP account, the node's Xray, the tunnel). It says so again every {REMIND // 3600} hours while the config stays down.", d["name"])

    st.put("warpwatch", {"done": done, "told": told, "flags": flags})
