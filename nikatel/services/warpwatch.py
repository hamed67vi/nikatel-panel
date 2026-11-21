"""The WARP watch (1.69.228; no daily cap and a move to another WARP account since 1.69.292). An AI config (AI-1, AI-2 ...) leaves the world through a Cloudflare WARP account in the Xray of its PasarGuard node. When that Xray keeps a stuck WARP session the config
still connects and still opens Instagram and Cloudflare pages, but Google, Telegram, YouTube and X time out - and nothing else notices: on 2026-11-05 AI-2 stayed like this for 85 minutes (03:40 - 05:06) and ONE reconnect of its node
(4 s) cured it. So: an AI config the monitor shows down for DOWN_FOR seconds - both vantages agree, the monitor already weighed the witnesses - whose tunnel is not down and whose node is connected gets its node reconnected; every
reconnect is an event.
How often: as long as the config is down, with COOLDOWN seconds between two reconnects of one node. There is NO cap per day any more (the owner, 2026-11-19, after AI-2 had been down for three hours with the six tries of the day used up: "what is the
daily cap for? remove it"). A cure that holds only for hours is repeated each time it stops holding: the config is down for minutes, not for the rest of the night.
A cure that does not hold at all is not repeated for ever: when one node has been reconnected SWITCH_AFTER times in the last SWITCH_WINDOW seconds and the config is down again, the next step is not another reconnect but a MOVE to another WARP account
(`_move`): the routing rule of the config's inbound in the shared core of the node is pointed at another WireGuard outbound of the same core (the one the fewest rules use), the core is saved by `pg_cores.save` (backup first, read back, put back by itself when the
panel did not keep it) and ONLY that node is reconnected, which applies it there (the other nodes run the new core after their own next restart - the way the pin of AI-2 was made by hand on 2026-11-06); a node that does not come back within SWITCH_WAIT seconds gets
the old core back. A config is moved at most once in SWITCH_GAP seconds (an account that is no better is not swapped back and forth every hour), and a move starts the reconnect count of that node again. When a move is not possible (the core has no other WARP
account, no single rule for the inbound, a read-only panel ...) the watch says so with a `bad` event and carries on reconnecting.
A config the watch keeps working on carries a FLAG (settings `warpwatch`.flags, drawn at the top of the Tunnel Doctor page): "reconnecting" (the tries of the last 24 hours, the next try, a move that was made), "needs a hand" (kept for flags of older versions), "not
watched" (it cannot be placed on a node). One `warn` event a day says when the cure does not hold (CHRONIC reconnects of one node in 24 hours). A config that is up for BACK_FOR seconds drops its flag and an `info` event says it recovered.
The same goes for a config that keeps going up and down (1.69.229): on the afternoon of 2026-11-05 AI-2 passed only 6-8 % of its tests from 17:00 but an odd good test reset the seven minutes every time, so nothing happened until 17:53. Now
an AI config also counts as down when at most FLAP_UP_MAX of its samples of the last FLAP_WINDOW seconds are up (at least FLAP_MIN_ROWS samples, and not when the three newest are all up: it just came back).
The placement (config -> tunnel -> foreign server -> PasarGuard node) is the one `purpose` uses; nothing is guessed: a config that cannot be placed on exactly one node is not touched (and is flagged "not watched"). `decide` is pure and tested."""
from __future__ import annotations

import copy
import re
import time
from collections import Counter

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Config, ConfigSample, Panel, Server, Tunnel, is_temp
from . import dnscache, panels, pg_cores, userq
from . import settings as st
from .events import emit

log = get_logger("warpwatch")
EVERY = 60
DOWN_FOR = 420            # seconds an AI config is down (as the monitor shows it) before its node is touched
COOLDOWN = 900            # seconds one node waits after a reconnect (or a move) before it is touched again; NO cap per day
SWITCH_AFTER = 3          # reconnects of one node within SWITCH_WINDOW that did not hold: the next step is a move to another WARP account
SWITCH_WINDOW = 21600     # six hours
SWITCH_GAP = 21600        # at most one move per config in this many seconds (a failed attempt counts too)
SWITCH_WAIT = 90          # seconds the node gets to come back after a move before the old core is put back
CHRONIC = 4               # reconnects of one node in 24 hours after which the owner is told once a day that the cure does not hold
CHRONIC_EVERY = 86400
BACK_FOR = 600            # seconds an AI config must stay up before its flag goes and an event says it recovered
FLAP_WINDOW = 900         # seconds looked back at a config that keeps going up and down
FLAP_UP_MAX = 0.34        # ... it counts as down when at most this share of the samples of that window is up
FLAP_MIN_ROWS = 6         # ... judged on at least this many samples (a test every two minutes, a witness adds its own)
_AI = re.compile(r"^AI-?\d", re.IGNORECASE)


class MoveError(Exception):
    """A move to another WARP account that did not happen; `touched` tells whether the node was reconnected on the way (then the caller must not reconnect it again at once)."""

    def __init__(self, why: str, touched: bool = False):
        super().__init__(why)
        self.touched = touched


def is_ai(name: str) -> bool:
    return bool(_AI.match(str(name or "").strip()))


def flapping(samples: list[tuple[int, bool]], now: int) -> bool:
    """Pure. samples: [(time, up)] of one config. True when the last FLAP_WINDOW seconds hold at least FLAP_MIN_ROWS of them, at most FLAP_UP_MAX of those are up and the three newest are not all up (a config that just came back is left alone)."""
    win = sorted((int(t), bool(u)) for t, u in samples if now - FLAP_WINDOW < int(t) <= now)

    if len(win) < FLAP_MIN_ROWS or sum(u for _, u in win) / len(win) > FLAP_UP_MAX:
        return False

    return not all(u for _, u in win[-3:])


def decide(down: list[dict], where: dict[str, dict], done: dict[str, list[int]], now: int, moved: dict[str, dict] | None = None) -> tuple[dict[str, list[str]], list[tuple[str, str, str]], dict[str, str]]:
    """Pure. down: [{"name", "since"}] AI configs the monitor shows down; where: {config name: {"key", "node_status", "tunnel_state"}} (the placement); done: {node key: [times of its reconnects]};
    moved: {config name: {"at": the time of its last move or attempt}}.
    -> ({node key: [config names]} to reconnect now, [(config name, why it is left alone, flag state)], {config name: node key} to MOVE to another WARP account now).
    The flag state: "working" (the cooldown runs: a later try is allowed), "held" (it cannot be placed on a node: nobody watches it) or "" (its tunnel or its node is the matter: the Doctor and the panel deal with that, nothing to flag)."""
    todo: dict[str, list[str]] = {}
    held: list[tuple[str, str, str]] = []
    moves: dict[str, str] = {}

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
            mine = [int(t) for t in done.get(w["key"], []) if now - int(t) < 86400]
            entry = (moved or {}).get(name, {})
            last_move = int(entry.get("at") or 0)                         # the last move TRY (a failed one counts for the gap)
            moved_at = last_move if entry.get("to") else 0                # the last move that was made: its account gets time to show whether it holds

            if mine and now - max(mine) < COOLDOWN and max(mine) >= moved_at:
                held.append((name, f"its node was reconnected {(now - max(mine)) // 60} minutes ago", "working"))
            elif moved_at and now - moved_at < COOLDOWN:
                held.append((name, f"it was moved to another WARP account {(now - moved_at) // 60} minutes ago: the new account gets time to show whether it holds", "working"))
            elif sum(1 for t in mine if now - t < SWITCH_WINDOW) >= SWITCH_AFTER and now - last_move >= SWITCH_GAP:
                moves[name] = w["key"]                                 # the reconnects do not hold: another WARP account
            else:
                todo.setdefault(w["key"], []).append(name)

    return todo, held, moves


def flag_of(state: str, why: str, w: dict | None, mine: list[int], since: int) -> dict:
    """Pure. The flag of one config for the Doctor page: state, the node's name, since when it is down, the reconnects of the last 24 hours (`tries`; `max` is 0: there is no cap), when the last one was, when the next one is allowed (0: none is planned), and the sentence `why`."""
    last = max(mine) if mine else 0
    nxt = last + COOLDOWN if mine else 0
    return {"state": state, "node": str((w or {}).get("node_name", "")), "since": int(since), "tries": len(mine), "max": 0, "last": int(last), "next": int(nxt), "why": why}


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


def _node_rows(c: dict) -> list[dict]:
    raw = panels.call(c, "GET", "/api/nodes")
    items = raw.get("nodes") if isinstance(raw, dict) else raw
    return [n for n in (items if isinstance(items, list) else []) if isinstance(n, dict)]


def _core_id_of(c: dict, node_id: int) -> int:
    """The core a node runs (`core_config_id` of the panel's node list); the first core when the panel does not say."""
    for n in _node_rows(c):
        if n.get("id") == node_id and n.get("core_config_id") is not None:
            return int(n["core_config_id"])

    return pg_cores.DEFAULT_CORE_ID


def _node_back(c: dict, node_id: int) -> bool:
    """True when the node shows connected within SWITCH_WAIT seconds (looked at every five seconds)."""
    end = time.time() + SWITCH_WAIT

    while time.time() < end:
        time.sleep(5)

        try:
            if any(n.get("id") == node_id and str(n.get("status", "")).lower() in ("connected", "healthy") for n in _node_rows(c)):
                return True
        except (panels.PanelError, ValueError):
            pass

    return False


def _plan_move(cfg: dict, port: int) -> tuple[dict, str, str]:
    """Pure. (the new config, the old account's tag, the new one's) for the inbound on `port`: its routing rule is pointed at the other WireGuard outbound that the fewest rules use.
    A MoveError (nothing touched) when it is not clear: no single inbound on that port, no single rule that sends it to a WARP outbound, no other WireGuard outbound."""
    new = copy.deepcopy(cfg)
    tags = [i.get("tag") for i in new.get("inbounds") or [] if isinstance(i, dict) and str(i.get("port")) == str(port)]

    if len(tags) != 1:
        raise MoveError(f"the core has {len(tags)} inbounds on port {port}: it cannot tell which one carries the config")

    rules = (new.get("routing") or {}).get("rules") or []
    mine = [r for r in rules if isinstance(r, dict) and tags[0] in (r.get("inboundTag") or [])]

    if len(mine) != 1 or not str(mine[0].get("outboundTag", "")).lower().startswith("warp"):
        raise MoveError("its inbound has no single routing rule that sends it to a WARP account")

    old = str(mine[0]["outboundTag"])
    others = [str(o.get("tag")) for o in new.get("outbounds") or [] if isinstance(o, dict) and o.get("protocol") == "wireguard" and o.get("tag") and o.get("tag") != old]

    if not others:
        raise MoveError("the core has no other WARP account to move it to")

    use = Counter(str(r.get("outboundTag")) for r in rules if isinstance(r, dict) and r.get("outboundTag") in others)
    pick = min(others, key=lambda t: (use.get(t, 0), t))
    mine[0]["outboundTag"] = pick
    return new, old, pick


def _move(w: dict, name: str, meta: dict) -> dict:
    """Move the config's inbound to another WARP account (see the module text). -> {"from", "to", "backup", "core"}. Raises MoveError."""
    try:
        port = int(meta.get("port") or 0)
        c = panels.cred_for(w["panel_id"])
        core_id = _core_id_of(c, w["node_id"])
        cur = pg_cores._get_core(c, core_id)
        new, old, pick = _plan_move(cur["config"], port)
        res = pg_cores.save(c, core_id, config=new, confirm=True, restart_after=False, base_rev=pg_cores.rev_of(cur), note=f"WARP watch: {name} moved from {old} to {pick}")
    except MoveError:
        raise
    except Exception as e:          # noqa: BLE001 - whatever goes wrong BEFORE the core is saved is "not moved", never a job that dies and tries again every minute
        raise MoveError(str(e)[:160] or type(e).__name__) from None

    backup = str(res.get("backup") or "")

    try:
        panels.node_reconnect(c, w["node_id"])
    except (panels.PanelError, ValueError) as e:
        raise MoveError(f"the core was saved but the node could not be reconnected ({str(e)[:80]}); the old core is on the panel as the backup {backup}", True) from None

    if not _node_back(c, w["node_id"]):
        try:
            pg_cores.restore(c, core_id, backup, confirm=True, restart_after=False)
            panels.node_reconnect(c, w["node_id"])
        except (panels.PanelError, ValueError, RuntimeError) as e:
            raise MoveError(f"the node did not come back with the new account and the old core could not be put back ({str(e)[:80]}): restore the backup {backup} by hand", True) from None

        raise MoveError(f"the node did not come back within {SWITCH_WAIT} s with the new account; the old core was put back", True)

    return {"from": old, "to": pick, "backup": backup, "core": core_id}


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
    moved = {k: dict(v) for k, v in (conf.get("moved") or {}).items()}
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
    todo, held, moves = decide(down, where, done, now, moved)
    states = {n: ("working", "") for names_ in todo.values() for n in names_}
    states.update({n: (s, "" if s == "working" and why.startswith("its node was reconnected") else why) for n, why, s in held if s})          # (the flag already says when the last reconnect was)
    flapped = {d["name"] for d in down if d.get("flap")}
    touched: set[str] = set()          # node keys that were reconnected by a move: not once more for the same minute

    for name, key in moves.items():
        w = where.get(name) or {}
        item = next((d for d in down if d["name"] == name), {})
        tries = len([t for t in done.get(key, []) if now - int(t) < SWITCH_WINDOW])

        try:
            got = _move(w, name, item.get("meta") or {})
        except MoveError as e:
            moved[name] = {"at": now, "failed": str(e)[:200]}          # (a failed try counts for the gap: no new try every minute)

            with session() as db:
                emit(db, "bad", "configs", f"The WARP watch tried to move {name} to another WARP account (its node was reconnected {tries} times in the last {SWITCH_WINDOW // 3600} hours and the config keeps going down) but could not: {e}. It carries on reconnecting the node.", name)

            if e.touched:
                done[key] = [t for t in done.get(key, []) if now - int(t) < 86400] + [now]
                touched.add(key)
                states[name] = ("working", "the move to another WARP account failed")
            else:
                todo.setdefault(key, []).append(name)

            continue

        done[key] = []          # a new account starts with a clean sheet
        moved[name] = {"at": now, "from": got["from"], "to": got["to"], "backup": got["backup"]}
        touched.add(key)
        states[name] = ("working", f"moved from {got['from']} to {got['to']}")

        with session() as db:
            emit(db, "warn", "configs", f"{name} keeps going down although its node {w.get('node_name') or '?'} was reconnected {tries} times in the last {SWITCH_WINDOW // 3600} hours: the WARP watch moved the config from {got['from']} to {got['to']} "
                                        f"(core backup {got['backup']}) and reconnected only that node, which runs it now; the other nodes take it at their next restart.", name)

    for key in touched:
        todo.pop(key, None)

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

        if len(mine) >= CHRONIC and now - int(told.get(d["name"] + ":chronic", 0)) >= CHRONIC_EVERY:
            told[d["name"] + ":chronic"] = now

            with session() as db:
                emit(db, "warn", "configs", f"The cure of {d['name']} does not hold: its node {w.get('node_name') or '?'} was reconnected {len(mine)} times in the last 24 hours and the config keeps going down. The WARP session of that node's Xray keeps breaking - "
                                            "the watch carries on (a reconnect every quarter of an hour while it is down, a move to another WARP account when the reconnects do not hold), but the cause wants a real fix: look at the WARP account and the node's Xray.", d["name"])

    st.put("warpwatch", {"done": done, "told": told, "flags": flags, "moved": moved})
