"""The Doctor and the DNS name of a config that does not connect (1.69.196).

The owner (2026-11-03): a Turkey TLS config was down for a long while although its tunnel was up. Cause: the tunnel had been moved to another Iran server and the record of the config's name still
pointed at the old one, which does not forward the port. The Doctor judged the tunnel (up, listening) and never looked at the name the users dial. He asked that the Doctor, in the mode he had put it
in, checks the health of the configs and changes the DNS when it sees the need.

The look (every five minutes, from the quality job, only for a tunnel that has a config DOWN for ten minutes and is itself up and listening on that port): the name of the config must point at an Iran
server that forwards the port through a tunnel that is up. When it points at none of them it is pointed at the Iran server of THIS tunnel. (1.69.243: a config whose name points at an Iran server that no longer has
its tunnel rides NO tunnel, so no tunnel has it among its down configs; `misdirected` finds those - down 15 minutes, the port forwarded by exactly one tunnel in the panel, that one up - and hands them to the same look.) Only in the Doctor's mode "move" (the mode that may change
the DNS by itself); in "fix" the Doctor says it once an hour and leaves the record.

What it never touches: a name behind a CDN, a name with no record of its own (a wildcard serves it), an address that is a number, a name that another host of a panel dials on a port this tunnel does not
carry. Rails: one repair of one name in six hours, six repairs in 24 hours for the whole panel, the copy of the DNS records must be fresh. Every change is in the Changes box (dns_log) with Restore.

The pure part (`stale_names`) carries the decisions and is tested."""
from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Config, Server, Tunnel, is_temp
from . import autopilot, dnscache, doctor, failover
from .events import emit

log = get_logger("dnsheal")
UP_FOR = 600              # the tunnel itself must have been up this long (a move in progress points the names by itself a little after the new tunnel is built)
FRESH = 900               # the copy of the DNS records must be this fresh (the quality job renews it in the background)
GAP = 6 * 3600            # one repair of one name in this time: a name that was changed and is still down has another problem, the Doctor must not flap it
PER_DAY = 6               # repairs of names for the whole panel in 24 hours
BLOCKED_GAP = 3600        # a tunnel with something to repair that the mode does not allow is looked at (and said) again after this long
ORPHAN_DOWN = 900         # (1.69.243) a config that rides NO tunnel is judged only after it has been down this long
_LOOKED: dict[int, float] = {}


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def _listens(t: dict, port: int) -> bool:
    return bool((t.get("listen") or {}).get(str(port)))


def stale_names(down: list[dict], tunnel: dict, tunnels: list[dict], hosts: dict[int, str], records: list[dict], dialled: Callable[[], dict[str, set[int]] | None]) -> tuple[list[dict], list[str]]:
    """Pure: the names of the configs that are DOWN whose record points at no Iran server that forwards their port, and the sentences for those that are left alone.
    down: [{"name", "address", "port"}]; tunnel / tunnels: {"id", "name", "iran_id", "host", "state", "ports": set, "listen": {"port": bool}}; hosts: {Iran server id: address};
    records: the rows of the DNS copy; dialled: asked at most once, only when a name is a candidate - {name: {ports}} of the hosts of the panels, None when they cannot be read.
    Returns (fixes, notes): fixes = [{"fqdn", "port", "config", "old": [ips], "new": ip, "server_id"}]."""
    fixes, notes, seen, panel = [], [], set(), []

    if tunnel.get("state") != "up" or not tunnel.get("host"):
        return [], [f"{tunnel.get('name')} is not up: its configs are not judged by their DNS"]

    for c in down:
        addr = str(c.get("address") or "").lower().strip(".")
        port = int(c.get("port") or 0)

        if not addr or not port or addr in seen or port not in tunnel["ports"]:
            continue                                        # (a config that rides this tunnel by its label alone, or one name twice)

        seen.add(addr)

        if _is_ip(addr):
            notes.append(f"{c['name']}: its address is a number, not a name - there is no DNS record to look at")
            continue

        if not _listens(tunnel, port):
            notes.append(f"{c['name']}: {tunnel['name']} does not listen on {port} on its Iran side - the DNS is not what stops it")
            continue

        recs = [r for r in records if str(r.get("type") or "A").upper() == "A" and str(r.get("fqdn") or "").lower().strip(".") == addr]

        if not recs:
            notes.append(f"{addr}: no record carries exactly this name (a wildcard may serve it) - left alone")
            continue

        if any(r.get("cloud") or r.get("port") for r in recs):
            notes.append(f"{addr}: the record is behind a CDN - left alone")
            continue

        serving = {hosts[t["iran_id"]] for t in tunnels if t.get("state") == "up" and port in t["ports"] and _listens(t, port) and t["iran_id"] in hosts}
        ips = {ip for r in recs for ip in (r.get("ips") or [])}

        if ips & serving:
            continue                                        # (it points at an Iran server that carries the port: the name is right, the fault is elsewhere)

        if not panel:
            panel.append(dialled())

        if panel[0] is None:
            notes.append(f"{addr}: the hosts of the panels cannot be read now, so it is not known what else dials this name - left alone")
            continue

        other = sorted(p for p in panel[0].get(addr, set()) if p not in tunnel["ports"] or not _listens(tunnel, p))

        if other:
            notes.append(f"{addr}: another host of a panel dials it on port {other[0]}, which {tunnel['name']} does not carry - left alone")
            continue

        fixes.append({"fqdn": addr, "port": port, "config": c["name"], "old": sorted(ips), "new": tunnel["host"], "server_id": tunnel["iran_id"]})

    return fixes, notes


def misdirected(configs: list[dict], tunnels: list[dict], servers: list[dict], records: list[dict]) -> dict[int, list[str]]:
    """Pure (1.69.243): {tunnel id: [config names]} for the configs that ride NO tunnel although their name points at an Iran server - the name was right once and the tunnel moved away. The owner, 2026-11-07: Netherlands and AI-1 were
    down for five hours; a failover rebuilt their tunnels on another Iran server and left the names on the old one. `userq.map_configs` finds no tunnel for such a config, so the look above - which starts from a tunnel that has a down
    config - never saw them.
    configs: [{"name", "address", "port", "down_for": seconds}] (the enabled configs that are down and ride no tunnel); tunnels: [{"id", "iran_id", "ports": set, "state"}] (enabled, not temporary); servers (Iran): [{"id", "host"}];
    records: the rows of the DNS copy. A config counts when it has been down for ORPHAN_DOWN seconds, its name has a record that points at an Iran server (not a number, not a name nobody knows), no tunnel of that server forwards its
    port, and EXACTLY ONE tunnel of the whole panel forwards it at all and that one is up (a port that several tunnels forward proves nothing). What is done with it is `stale_names`' business: CDN names, wildcards, other hosts that dial
    the name, and the rails."""
    by_host = {s["host"]: s["id"] for s in servers}
    by_fqdn: dict[str, list[dict]] = {}

    for r in records:
        if str(r.get("type") or "A").upper() == "A" and r.get("fqdn"):
            by_fqdn.setdefault(str(r["fqdn"]).lower().strip("."), []).append(r)

    out: dict[int, list[str]] = {}

    for c in configs:
        addr, port = str(c.get("address") or "").lower().strip("."), int(c.get("port") or 0)

        if not addr or not port or _is_ip(addr) or int(c.get("down_for") or 0) < ORPHAN_DOWN:
            continue

        sids = {by_host[ip] for r in by_fqdn.get(addr, []) for ip in (r.get("ips") or []) if ip in by_host}

        if not sids or any(t["iran_id"] in sids and port in t["ports"] for t in tunnels):
            continue                                        # (it points at no Iran server we know, or at one that forwards the port: not this look's business)

        holders = [t for t in tunnels if port in t["ports"]]

        if len(holders) == 1 and holders[0].get("state") == "up":
            out.setdefault(holders[0]["id"], []).append(c["name"])

    return out


def _orphans_now(now: int | None = None) -> dict[int, list[str]]:
    """{tunnel id: [config names]} read from the panel now - see `misdirected`."""
    from . import userq

    now = int(time.time()) if now is None else now

    with session() as db:
        cfgs, placed = userq._placement(db)          # (the same placement the grades use)
        down = [{"name": c.name, "address": (c.meta or {}).get("address", ""), "port": (c.meta or {}).get("port", 0), "down_for": (now - int(c.status_since)) if c.status_since else 0}
                for c in cfgs if c.status == "down" and c.name not in placed]

        if not down:
            return {}

        tunnels = [{"id": t.id, "iran_id": t.iran_server_id, "state": t.state, "ports": {int(p) for p in (t.forwarded_ports or []) if str(p).isdigit()}}
                   for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name)]
        servers = [{"id": s.id, "host": s.host} for s in db.scalars(select(Server).where(Server.role == "iran"))]

    return misdirected(down, tunnels, servers, list(dnscache.CACHE.get("rows") or []))


def _dialled() -> dict[str, set[int]] | None:
    out, err = failover._host_ports()
    return None if (err and not out) else out


def _world(tunnel_id: int, down_names: list[str], now: int) -> dict | None:
    """What the look needs, read once: the tunnel, every tunnel, the Iran servers, the down configs by name. None when the tunnel is not worth a look (gone, not up for UP_FOR, a temporary one)."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None or not t.enabled or is_temp(t.name) or t.state != "up" or now - int(t.state_since or now) < UP_FOR:
            return None

        hosts = {s.id: s.host for s in db.scalars(select(Server).where(Server.role == "iran"))}
        rows = [{"id": x.id, "name": x.name, "iran_id": x.iran_server_id, "host": hosts.get(x.iran_server_id, ""), "state": x.state, "ports": {int(p) for p in (x.forwarded_ports or []) if str(p).isdigit()},
                 "listen": dict((x.detail or {}).get("listen") or {})} for x in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(x.name)]
        down = [{"name": c.name, "address": (c.meta or {}).get("address", ""), "port": (c.meta or {}).get("port", 0)} for c in db.scalars(select(Config).where(Config.enabled.is_(True), Config.name.in_(down_names)))]

    me = next((x for x in rows if x["id"] == tunnel_id), None)
    return {"tunnel": me, "tunnels": rows, "hosts": hosts, "down": down} if me else None


def _blocked() -> str:
    """'' when the Doctor may change a record by itself now, else the sentence that says why not."""
    if doctor.mode() != "move":
        return f"the Doctor is in the mode '{doctor.mode()}'; only the mode 'move' changes the DNS by itself"

    return "" if doctor.can_move() else "the failover is not licensed here or the panel is read-only"


def _over(s: dict, now: int, fqdns: list[str]) -> str:
    """'' when these names may be repaired now, else why not (the gap of one name, the number in 24 hours)."""
    done = s["dns_heal"]

    for f in fqdns:
        if now - int(done.get(f) or 0) < GAP:
            return f"{f} was repaired by the Doctor {(now - int(done[f])) // 60} minutes ago (once in {GAP // 3600} hours)"

    if sum(1 for v in done.values() if now - int(v) < 86400) + len(fqdns) > PER_DAY:
        return f"{PER_DAY} names were repaired in the last 24 hours already"

    return ""


def look(ux_by_tunnel: dict[int, dict]) -> None:
    """Called by the quality job with the grade of every tunnel that has configs: the look at the DNS of each tunnel that has a config DOWN (see the top of this file). Since 1.69.243 also the configs that ride no tunnel at all
    because their name points at the wrong Iran server (`misdirected`): they are looked at through the one tunnel that forwards their port."""
    wanted: dict[int, list[str]] = {}

    for tid, ux in ux_by_tunnel.items():
        down = [b["config"] for b in (ux.get("bad") or []) if b.get("issue") == "down"]

        if down:
            wanted[tid] = down

    try:
        for tid, names in _orphans_now().items():
            wanted.setdefault(tid, []).extend(n for n in names if n not in wanted.get(tid, []))
    except Exception:
        log.exception("dns look at the configs that ride no tunnel failed")

    for tid, down in wanted.items():
        try:
            _look_at(tid, down)
        except Exception:
            log.exception("dns look at tunnel %s failed", tid)


def _look_at(tunnel_id: int, down_names: list[str]) -> None:
    now = int(time.time())

    if now - float(dnscache.CACHE.get("at") or 0) > FRESH or now - _LOOKED.get(tunnel_id, 0) < BLOCKED_GAP:
        return

    w = _world(tunnel_id, down_names, now)

    if w is None:
        return

    fixes, _ = stale_names(w["down"], w["tunnel"], w["tunnels"], w["hosts"], list(dnscache.CACHE.get("rows") or []), _dialled)

    if not fixes:
        return

    name, s = w["tunnel"]["name"], doctor._state()
    s["dns_heal"] = {k: v for k, v in s["dns_heal"].items() if now - int(v) < 7 * 86400}
    names = [f["fqdn"] for f in fixes]
    why = _blocked() or _over(s, now, names)

    if why:
        _LOOKED[tunnel_id] = now
        log.info("dns look at %s: %s - %s", name, ", ".join(names), why)

        with session() as db:
            emit(db, "warn", "tunnels", f"Tunnel {name}: {', '.join(names)} points at a server that does not carry the port ({', '.join(fixes[0]['old'])}), the Doctor did not change it - {why}.", name)

        return

    try:
        autopilot._start(name, _fix, tunnel_id, fixes, action="fix", source="auto")
    except ValueError as e:
        log.info("dns repair of %s not started: %s", name, str(e)[:100])
        return

    s["dns_heal"].update({f: now for f in names})
    doctor._save(s)


def _reach(ip: str, port: int) -> str:
    t0 = time.time()

    try:
        socket.create_connection((ip, port), 6).close()
        return f"âœ“ {ip}:{port} accepts a connection ({(time.time() - t0) * 1000:.0f} ms)"
    except OSError:
        return f"{ip}:{port} did not answer from the panel (an Iran server is often closed to the abroad; the tunnel itself says it listens)"


def _fix(aid: int, tunnel_id: int, fixes: list[dict]) -> None:
    """The story of the repair: say what is wrong, point each name at the Iran server of the tunnel, check the port."""
    say = lambda line, **kw: autopilot._say(aid, line, **kw)      # (one short line for the story)
    say(f"{len(fixes)} config name(s) of this tunnel point at a server that does not carry their port, while the tunnel is up and listens:")

    for f in fixes:
        say(f"âœ• {f['config']}: {f['fqdn']} â†’ {', '.join(f['old']) or '-'}, the Iran side of the tunnel is {f['new']}:{f['port']}")

    by_server: dict[int, list[str]] = {}

    for f in fixes:
        by_server.setdefault(f["server_id"], []).append(f["fqdn"])

    changed = 0

    for sid, names in by_server.items():
        changed += len(failover.point_names(aid, names, sid, tag="heal"))

    say(_reach(fixes[0]["new"], fixes[0]["port"]))
    say(f"{changed} record(s) changed; each is in the Changes box with Restore. The Doctor keeps looking every five minutes and says when the configs connect again.", done=True, ok=bool(changed))
