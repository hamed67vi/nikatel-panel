"""The L2TP watch (1.69.257). The owner, 2026-11-13: "the Doctor must always check the health of the config, the server and its tunnel, and fix any problem."
Every EVERY seconds, while the product has an entry (settings `l2tp`) and is licensed, the watch reads what a connection needs and puts right what it can:
  the ENTRY (an Iran server): the IPsec and L2TP services and the tick timer run, the three UDP ports listen, the customers are sent to the tunnel's far end (exit.conf and the route table 250 carry the inner
    address the panel knows for the tunnel), and that far end answers through the tunnel; the accounts MIRROR to the direct server (its timer, its last word) when a direct server is chosen;
  the EXIT (a foreign server): its rules, its route for the entry's pool back to the entry's inner address, forwarding, its timer;
  the TUNNEL between them: up, and protected (the Doctor repairs and restarts a protected tunnel in place and never moves or replaces it - a move would change the inner addresses under the product);
  the DIRECT server, when chosen: its services and ports (an entry that is the way out at once).
Repairs, one reviewed action each (`l2tp_heal` on an entry, `l2tp_exit_heal` on the exit, `builder.set_protected` in the panel): the services restarted, the entry's tick run, the route set to the tunnel's inner
address, the exit's rules reconciled or its route added, the mirror run, the tunnel marked protected. Each kind of repair on one server at most once in COOLDOWN seconds and PER_DAY times in 24 hours; after that
the finding is told with a `bad` event every REMIND seconds until it is over. Every repair is an event; a finding that is over says so with an `info` event. What the watch sees and does is drawn at the top of
the Tunnel Doctor page (`/api/doctor` `l2tp`) and in the L2TP tab's strip. `parse_watch`, `judge`, `plan` and `flag_of` are pure and tested."""
from __future__ import annotations

import calendar
import json
import time

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel, is_protected
from ..ssh import SSHError, run_action, run_script
from . import l2tp, license
from . import settings as st
from .events import emit

log = get_logger("l2tpwatch")
EVERY = 120
COOLDOWN = 600            # seconds between two repairs of the same kind on the same server
PER_DAY = 6               # repairs of one kind on one server in 24 hours; then the finding needs a hand
REMIND = 10800            # seconds between two "needs a hand" events while a finding stays
MIRROR_STALE = 900        # the mirror's last word older than this = the direct server may be behind
PORTS = (500, 4500, 1701)
PARTS = ("entry", "exit", "tunnel", "direct", "mirror")


# ------------------------------------------------------------------ pure
def parse_watch(out: str) -> dict:
    """The output of the `l2tp_watch` read of an entry or of the direct server."""
    d: dict = {"installed": False, "units": {}, "udp": set(), "exit_ip": "", "route": "", "ping": "", "mirror_timer": "none", "mirror": {"raw": "", "at": 0, "ok": None}, "info": {}, "sessions": 0}

    for ln in out.splitlines():
        key, _, rest = ln.partition(" ")
        rest = rest.strip()

        if key == "L2TP":
            d["installed"] = rest == "ok"
        elif key == "UNIT":
            a = rest.split()

            if len(a) == 2:
                d["units"][a[0]] = a[1]
        elif key == "UDP":
            d["udp"] = {int(p) for p in rest.split() if p.isdigit()}
        elif key == "EXITIP":
            d["exit_ip"] = rest
        elif key == "ROUTE":
            d["route"] = rest
        elif key == "PING":
            d["ping"] = rest
        elif key == "MIRRORTIMER":
            d["mirror_timer"] = rest or "none"
        elif key == "MIRROR":
            d["mirror"] = _parse_mirror(rest)
        elif key == "INFO":
            try:
                v = json.loads(rest)
            except ValueError:
                v = None

            d["info"] = v if isinstance(v, dict) else {}
        elif key == "SESSIONS":
            d["sessions"] = int(rest) if rest.isdigit() else 0

    return d


def _parse_mirror(text: str) -> dict:
    """The mirror's last word: `2026-11-09T01:20:54Z 10.58.188.54 {"ok": true, ...}` or `... FAILED ...` -> {"raw", "at", "ok"} (ok None when there is no word)."""
    text = text.strip()

    if not text:
        return {"raw": "", "at": 0, "ok": None}

    stamp = text.split(" ", 1)[0]

    try:
        at = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        at = 0

    return {"raw": text[:200], "at": at, "ok": '"ok": true' in text and "FAILED" not in text}


def _finding(part: str, key: str, level: str, text: str, fix: dict | None = None) -> dict:
    return {"part": part, "key": key, "level": level, "text": text, "fix": fix}


def _heal(where: str, mode: str, what: str, **params) -> dict:
    return {"where": where, "action": "l2tp_exit_heal" if where == "exit" else "l2tp_heal", "params": {"MODE": mode, **params}, "what": what}


def judge(entry: dict | None, exit_: dict | None, tunnel: dict | None, direct: dict | None, now: int) -> list[dict]:
    """Pure. entry / exit_ / direct: the parsed reads, {"error": why} when the server could not be read, None when that server is not chosen; tunnel: {"id", "name", "state", "protected", "iran_ip", "foreign_ip"} or None.
    -> the findings, worst first: {"part", "key", "level" (bad / warn), "text", "fix" ({"where", "action", "params", "what"} or None when nothing here can mend it)}."""
    out: list[dict] = []
    pool = str(((entry or {}).get("info") or {}).get("pool") or "")

    if entry is None:
        return out

    if entry.get("error"):
        out.append(_finding("entry", "entry.reach", "bad", f"the entry could not be read: {entry['error']}"))
    elif not entry.get("installed"):
        out.append(_finding("entry", "entry.installed", "bad", "the L2TP manager is not installed on the entry (install-entry.sh)"))
    else:
        units = entry.get("units") or {}
        missing = sorted(set(PORTS) - set(entry.get("udp") or ()))

        if units.get("strongswan") != "active" or units.get("xl2tpd") != "active":
            out.append(_finding("entry", "entry.services", "bad", f"on the entry IPsec is {units.get('strongswan', '?')} and L2TP is {units.get('xl2tpd', '?')}: nobody can connect", _heal("entry", "restart", "restarted the IPsec and L2TP services on the entry")))
        elif missing:
            out.append(_finding("entry", "entry.ports", "bad", f"the entry does not listen on UDP {', '.join(map(str, missing))}: nobody can connect", _heal("entry", "restart", "restarted the IPsec and L2TP services on the entry")))
        elif units.get("tick") != "active":
            out.append(_finding("entry", "entry.tick", "warn", "the entry's tick timer (firewall, route, expiry every 30 s) is not running", _heal("entry", "restart", "started the entry's tick timer")))

        if tunnel and tunnel.get("foreign_ip"):
            far = str(tunnel["foreign_ip"])

            if entry.get("exit_ip") != far:
                out.append(_finding("entry", "entry.exitip", "bad", f"the entry sends its customers to {entry.get('exit_ip') or 'nowhere'}, but the far end of tunnel {tunnel['name']} is {far}: the tunnel was rebuilt under the product", _heal("entry", "route", f"pointed the entry's customers at {far}, the tunnel's far end", IP=far)))
            elif entry.get("route") != far:
                out.append(_finding("entry", "entry.route", "bad", f"the entry's route for its customers (table 250) does not lead to {far}", _heal("entry", "tick", "ran the entry's tick, which puts the route back")))
            elif entry.get("ping") == "no" and str(tunnel.get("state")) == "up":
                out.append(_finding("entry", "entry.ping", "bad", f"the exit's inner address {far} does not answer the entry through tunnel {tunnel['name']}, though the tunnel says up"))

        if direct is not None:
            m = entry.get("mirror") or {}

            if entry.get("mirror_timer") != "active":
                out.append(_finding("mirror", "mirror.timer", "warn", "the accounts mirror to the direct server is not running on the entry", _heal("entry", "mirror", "started the accounts mirror and ran it once")))
            elif m.get("ok") is False or (m.get("at") and now - int(m["at"]) > MIRROR_STALE):
                why = "its last run failed" if m.get("ok") is False else f"its last word is {(now - int(m['at'])) // 60} minutes old"
                out.append(_finding("mirror", "mirror.stale", "warn", f"the accounts mirror to the direct server: {why} - new accounts may not work on the direct address", _heal("entry", "mirror", "ran the accounts mirror")))

    if exit_ is not None:
        if exit_.get("error"):
            out.append(_finding("exit", "exit.reach", "bad", f"the exit could not be read: {exit_['error']}"))
        elif not exit_.get("installed"):
            out.append(_finding("exit", "exit.installed", "bad", "the L2TP exit is not installed on the exit server (install-exit.sh)"))
        else:
            near = str((tunnel or {}).get("iran_ip") or "")
            routes = [str(r) for r in (exit_.get("routes") or [])]
            mine = next((r for r in routes if pool and r.startswith(pool + " ")), "")

            if pool and near and mine and mine.split()[-1] != near:
                out.append(_finding("exit", "exit.route", "bad", f"the exit routes the customers' pool back to {mine.split()[-1]}, but the entry's inner address on tunnel {tunnel['name']} is {near}", _heal("exit", "add", f"routed the pool back to {near}, the entry's inner address", POOL=pool, IP=near)))
            elif pool and near and not mine:
                out.append(_finding("exit", "exit.route", "bad", f"the exit has no route for the customers' pool {pool}", _heal("exit", "add", f"routed the pool back to {near}", POOL=pool, IP=near)))
            elif not exit_.get("rules") or not exit_.get("forward") or exit_.get("timer") != "active":
                out.append(_finding("exit", "exit.rules", "bad", f"on the exit: {exit_.get('rules') or 0} rules, forwarding {'on' if exit_.get('forward') else 'OFF'}, timer {exit_.get('timer', '?')}", _heal("exit", "reconcile", "put the exit's rules, routes and timer back")))

    if tunnel is not None:
        if str(tunnel.get("state")) == "down":
            out.append(_finding("tunnel", "tunnel.down", "bad", f"tunnel {tunnel['name']} is down: nobody can use the product until the Doctor has it back (it repairs a protected tunnel in place)"))
        elif not tunnel.get("protected"):
            out.append(_finding("tunnel", "tunnel.protected", "warn", f"tunnel {tunnel['name']} is not protected: the Doctor could move or replace it and the product would lose its link", {"where": "panel", "action": "protect", "params": {}, "what": f"marked tunnel {tunnel['name']} protected"}))

    if direct is not None:
        if direct.get("error"):
            out.append(_finding("direct", "direct.reach", "bad", f"the direct server could not be read: {direct['error']}"))
        elif not direct.get("installed"):
            out.append(_finding("direct", "direct.installed", "bad", "the L2TP manager is not installed on the direct server"))
        else:
            units = direct.get("units") or {}
            missing = sorted(set(PORTS) - set(direct.get("udp") or ()))

            if units.get("strongswan") != "active" or units.get("xl2tpd") != "active":
                out.append(_finding("direct", "direct.services", "bad", f"on the direct server IPsec is {units.get('strongswan', '?')} and L2TP is {units.get('xl2tpd', '?')}", _heal("direct", "restart", "restarted the IPsec and L2TP services on the direct server")))
            elif missing:
                out.append(_finding("direct", "direct.ports", "bad", f"the direct server does not listen on UDP {', '.join(map(str, missing))}", _heal("direct", "restart", "restarted the IPsec and L2TP services on the direct server")))
            elif units.get("tick") != "active":
                out.append(_finding("direct", "direct.tick", "warn", "the direct server's tick timer is not running", _heal("direct", "restart", "started the direct server's tick timer")))

    out.sort(key=lambda f: (0 if f["level"] == "bad" else 1, PARTS.index(f["part"])))
    return out


def repair_key(f: dict) -> str:
    fx = f.get("fix") or {}
    return f"{fx.get('where', '')}:{fx.get('action', '')}:{fx.get('params', {}).get('MODE', fx.get('action', ''))}"


def plan(findings: list[dict], done: dict[str, list[int]], now: int, cooldown: int = COOLDOWN, per_day: int = PER_DAY) -> tuple[list[dict], list[dict]]:
    """Pure. -> (the findings whose repair runs now, the findings that wait or need a hand). A repair of the same kind on the same server runs at most once in `cooldown` seconds and `per_day` times in 24 hours;
    the second list carries `state`: "working" (its repair ran a moment ago, the next is allowed later), "hand" (the day's repairs are used, or nothing here can mend it)."""
    todo, held = [], []
    seen: set[str] = set()

    for f in findings:
        if not f.get("fix"):
            held.append({**f, "state": "hand"})
            continue

        key = repair_key(f)
        mine = [t for t in done.get(key, []) if now - int(t) < 86400]

        if key in seen or (mine and now - max(mine) < cooldown):          # (the same repair already runs this look, or ran a moment ago)
            held.append({**f, "state": "working"})
        elif len(mine) >= per_day:
            held.append({**f, "state": "hand"})
        else:
            seen.add(key)
            todo.append(f)

    return todo, held


def flag_of(f: dict, state: str, mine: list[int], since: int) -> dict:
    """Pure. One line of the Doctor page: the part, the state (working / hand), the sentence, since when, the repairs of the last 24 hours (`tries` of `max`), when the last one ran and when the next is allowed (0: none)."""
    last = max(mine) if mine else 0
    nxt = last + COOLDOWN if mine and len(mine) < PER_DAY and f.get("fix") else 0
    return {"part": f["part"], "key": f["key"], "state": state, "level": f["level"], "text": f["text"], "since": int(since), "tries": len(mine), "max": PER_DAY if f.get("fix") else 0, "last": int(last), "next": int(nxt)}


# ------------------------------------------------------------------ the view
def view(conf: dict | None = None) -> dict:
    """For the Doctor page and the L2TP tab: whether the watch is on and the product configured, the last look (at, ok, the state of every part) and the flags, worst first."""
    d = conf if conf is not None else st.get("l2tpwatch")
    c = l2tp.conf()
    seen = d.get("seen") or {}
    flags = sorted((d.get("flags") or {}).values(), key=lambda f: (0 if f.get("level") == "bad" else 1, PARTS.index(f["part"]) if f.get("part") in PARTS else 9))
    return {"on": bool(d.get("on", True)), "configured": bool(c.get("entry_id")), "direct": bool(c.get("direct_id")), "at": int(seen.get("at") or 0), "ok": seen.get("ok"), "parts": dict(seen.get("parts") or {}), "flags": flags}


# ------------------------------------------------------------------ the job
def _read(sid: int, what: str, script: str) -> dict:
    try:
        node = l2tp._node(sid, what)
        out = run_script(node, script, timeout=40)
    except (SSHError, RuntimeError, ValueError, OSError) as e:
        return {"error": str(e)[:160]}

    return parse_watch(out) if script == "l2tp_watch" else l2tp.parse_exit(out)


def _tunnel(tid: int) -> dict | None:
    if not tid:
        return None

    with session() as db:
        t = db.get(Tunnel, tid)

        if t is None:
            return None

        return {"id": t.id, "name": t.name, "state": t.state, "protected": is_protected(t), "iran_ip": t.iran_tunnel_ip or "", "foreign_ip": t.foreign_tunnel_ip or ""}


def _repair(f: dict, c: dict) -> str:
    """Run the repair of one finding; returns the server's last line (or the panel's word), raises on a failure."""
    fx = f["fix"]

    if fx["where"] == "panel":
        from . import builder

        builder.set_protected(int(c["tunnel_id"]), True)
        return "protected"

    sid = {"entry": c["entry_id"], "exit": c["exit_id"], "direct": c["direct_id"]}[fx["where"]]
    out = run_action(l2tp._node(int(sid), fx["where"]), fx["action"], fx["params"], timeout=60)
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else "")[:160]


def run() -> None:
    """Job (every two minutes): see the module text. Three short reads; nothing while the product has no entry, is not licensed, the watch is off or the panel is read-only."""
    if jobs.stopping():
        return

    conf = st.get("l2tpwatch")
    c = l2tp.conf()

    if not conf.get("on", True) or not c.get("entry_id") or get_settings().read_only or not license.has("l2tp"):
        return

    now = int(time.time())
    entry = _read(int(c["entry_id"]), "entry", "l2tp_watch")
    exit_ = _read(int(c["exit_id"]), "exit", "l2tp_exit_state") if c.get("exit_id") else None
    direct = _read(int(c["direct_id"]), "direct", "l2tp_watch") if c.get("direct_id") else None
    tunnel = _tunnel(int(c.get("tunnel_id") or 0))
    findings = judge(entry, exit_, tunnel, direct, now)

    done = {k: [int(t) for t in v if now - int(t) < 86400] for k, v in (conf.get("done") or {}).items()}
    told = dict(conf.get("told") or {})
    old = conf.get("flags") or {}
    flags: dict[str, dict] = {}
    todo, held = plan(findings, done, now)

    for f in todo:
        key = repair_key(f)

        try:
            word = _repair(f, c)
        except (SSHError, RuntimeError, ValueError, OSError) as e:
            log.warning("l2tp watch: the repair of %s failed: %s", f["key"], str(e)[:120])
            held.append({**f, "state": "working"})
            continue

        done[key] = done.get(key, []) + [now]

        with session() as db:
            emit(db, "warn", "l2tp", f"L2TP: {f['text']}. The watch {f['fix']['what']} ({word or 'done'}).", f["key"])

        held.append({**f, "state": "working"})

    for f in held:
        mine = [t for t in done.get(repair_key(f), []) if now - int(t) < 86400] if f.get("fix") else []
        flags[f["key"]] = flag_of(f, f["state"], mine, int((old.get(f["key"]) or {}).get("since") or now))

        if f["state"] == "hand" and f["level"] == "bad" and now - int(told.get(f["key"], 0)) >= REMIND:
            told[f["key"]] = now
            tail = " The watch cannot mend this by itself." if not f.get("fix") else f" The watch repaired it {len(mine)} times in the last 24 hours and stops here."

            with session() as db:
                emit(db, "bad", "l2tp", f"L2TP: {f['text']}.{tail} It needs a look by hand; it says so again every {REMIND // 3600} hours while it stays.", f["key"])

    for key, f in old.items():
        if key not in flags:
            told.pop(key, None)

            with session() as db:
                emit(db, "info", "l2tp", f"L2TP: over - {f.get('text', key)}.", key)

    parts = {p: "ok" for p in ("entry", "exit", "tunnel") if (p != "exit" or exit_ is not None) and (p != "tunnel" or tunnel is not None)}

    if direct is not None:
        parts.update(direct="ok", mirror="ok")

    for f in findings:
        if f["part"] in parts and (parts[f["part"]] == "ok" or f["level"] == "bad"):
            parts[f["part"]] = f["level"]

    seen = {"at": now, "ok": not any(f["level"] == "bad" for f in findings), "parts": parts, "sessions": int(entry.get("sessions") or 0) if not entry.get("error") else None}
    st.put("l2tpwatch", {"done": done, "told": told, "flags": flags, "seen": seen})
