"""Tunnel health, read-only.

A tunnel is UP when: the service units on both servers are active, the GRE interface (if the
type has one) is up on the Iran side, and the Iran side can ping the foreign end over it.
DEGRADED: everything runs but the ping is lossy or a forwarded port does not listen.
DOWN: a unit is not active, the interface is missing, or the peer does not answer.

Unit names follow the conventions of the servers (see docs/tunnels.md):
    backhaul : backhaul-iran-<transport>-<port>  /  backhaul-kharej-<transport>-<port>, plus nikatel-gre-<name> on both
    backpack : backpack-<name>  /  backpack-<name>-kharej
    paqet    : paqet-<name> on both
    chisel   : chisel-<name>  /  chisel-<name>-kharej
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import delete, select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel, TunnelCheck
from ..ssh import Node, SSHError, run_script
from .events import emit

log = get_logger("tunnels")

KEEP_CHECKS_SECONDS = 14 * 86400
GRACE_SECONDS = 180          # a tunnel younger than this is "starting", not "down"
DEGRADED_LOSS = 15.0         # % loss over the GRE link (20 pings) that counts as degraded; the Iran-abroad
                             # paths lose 3-10 % of packets on a normal day, that is not an event
CONFIRM_CHECKS = 2           # up <-> degraded flips need this many consecutive checks that agree
FLAP_WINDOW = 10             # the last checks looked at for flapping
FLAP_BAD = 3                 # this many failed checks in the window: the link flaps - the tunnel is degraded even when this check passed


@dataclass
class Plan:
    iran_units: list[str]
    foreign_units: list[str]
    iface: str = ""
    iran_peer: str = ""       # the foreign end's overlay IP, pinged from Iran
    ports: list[int] = field(default_factory=list)


def link_of(t: Tunnel) -> tuple[str, str]:
    """(GRE interface, address to ping from the Iran side) of a tunnel. A tunnel found on the servers keeps the interface
    it already had (none when its foreign end has no such link; then the foreign server's own address is pinged, for the latency only)."""
    rec = t.record or {}

    if rec.get("legacy_gre"):
        iface = str(rec.get("legacy_iface") or "")
        peer = t.foreign_tunnel_ip if iface else (t.foreign.host if t.foreign else "")
        return iface, peer if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", peer or "") else ""

    return sname(t), t.foreign_tunnel_ip or ""


def sname(t: Tunnel) -> str:
    """The name the tunnel carries ON THE SERVERS (GRE interface, unit and file names). It is the name it was built with: renaming a tunnel in the panel changes what the operator sees, never what is on the servers."""
    return str((t.record or {}).get("slug") or t.name)


def servers_off(t: Tunnel) -> list[str]:
    """Plain sentences for the servers of this tunnel that are off (or do not answer). While one is, nothing is done to the tunnel by itself: there is nothing to repair until the server is back."""
    return [f"{role} server {s.name} is off or does not answer" for role, s in (("Iran", t.iran), ("Foreign", t.foreign)) if s is not None and s.enabled and s.online is False]


def plan_for(t: Tunnel) -> Plan:
    name, typ, tr, port = sname(t), t.type.lower(), (t.transport or "tcpmux").lower(), int(t.port or 0)
    ports = [int(p) for p in (t.forwarded_ports or []) if str(p).isdigit()]

    if typ == "backhaul" and (t.record or {}).get("legacy_gre"):   # found on the servers: its GRE link is not run by a nikatel-gre unit, so only backhaul, the data path and (when it has one) the link are judged
        iface, peer = link_of(t)
        return Plan([f"backhaul-iran-{tr}-{port}.service"], [f"backhaul-kharej-{tr}-{port}.service"], iface=iface, iran_peer=peer, ports=ports)

    if typ == "backhaul":
        return Plan([f"backhaul-iran-{tr}-{port}.service", f"nikatel-gre-{name}.service"],
                    [f"backhaul-kharej-{tr}-{port}.service", f"nikatel-gre-{name}.service"],
                    iface=name, iran_peer=t.foreign_tunnel_ip or "", ports=ports)

    if typ == "gre":
        return Plan([f"nikatel-gre-{name}.service"], [f"nikatel-gre-{name}.service"], iface=name,
                    iran_peer=t.foreign_tunnel_ip or "", ports=ports)

    if typ == "paqet":
        return Plan([f"paqet-{name}.service"], [f"paqet-{name}.service"], ports=ports)

    if typ == "chisel":
        return Plan([f"chisel-{name}.service"], [f"chisel-{name}-kharej.service"], ports=ports)

    return Plan([f"backpack-{name}.service"], [f"backpack-{name}-kharej.service"], ports=ports)


@dataclass
class Side:
    units: dict[str, str] = field(default_factory=dict)
    iface_state: str = ""
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    rtt_ms: float | None = None
    loss_pct: float | None = None
    listen: dict[int, bool] = field(default_factory=dict)
    data_port: int = 0           # the forwarded port that was asked for one HTTP answer
    data_code: str = ""          # its answer: an HTTP code, "000" = nothing came back, "" = not asked
    data_ms: float | None = None  # how long that answer took (the latency of the users' path when the ping over the link is blocked)
    now: int = 0
    error: str = ""


def parse_side(out: str) -> Side:
    s = Side()

    for line in str(out).splitlines():
        p = line.split()

        if not p:
            continue

        if p[0] == "UNIT" and len(p) >= 3:
            s.units[p[1]] = p[2]
        elif p[0] == "IFACE" and len(p) >= 2:
            s.iface_state = p[2] if len(p) >= 3 and p[2].isalpha() else ""
            nums = [x for x in p[2:] if x.isdigit()]

            if len(nums) >= 2:
                s.rx_bytes, s.tx_bytes = int(nums[0]), int(nums[1])
        elif p[0] == "PEER" and len(p) >= 2:
            # "PEER <ip> [<avg rtt>] [<loss %>]" - the rtt is missing when every ping was lost
            rest = p[2:]

            if len(rest) >= 2:
                s.rtt_ms = float(rest[0])
                s.loss_pct = float(rest[1])
            elif len(rest) == 1 and rest[0].isdigit():
                s.loss_pct = float(rest[0])
            else:
                s.loss_pct = 100.0
        elif p[0] == "LISTEN" and len(p) >= 3:
            s.listen[int(p[1])] = p[2] != "0"
        elif p[0] == "DATA" and len(p) >= 3:
            s.data_port, s.data_code = int(p[1]), ("000" if set(p[2]) == {"0"} else p[2])      # curl prints 000 itself when it fails: "000000" and "0" mean the same

            if len(p) >= 4 and s.data_code != "000":
                try:
                    s.data_ms = round(float(p[3]) * 1000, 1)
                except ValueError:
                    s.data_ms = None
        elif p[0] == "NOW" and len(p) >= 2:
            s.now = int(p[1])

    return s


DATA_DOWN_AFTER = 4          # this many failed data probes in a row (4 minutes): the tunnel is down, not just degraded


def judge(plan: Plan, iran: Side, foreign: Side, data_seen: bool = False, data_fails: int = 0,
          ext: tuple | None = None, ext_seen: bool = False, ext_fails: int = 0) -> tuple[str, str]:
    """(state, reason). Pure, so the rules are testable. `data_seen`: this tunnel has answered an HTTP request before (so silence now
    is a fault, not a port that speaks another protocol); `data_fails`: consecutive probes without an answer, this one included.
    `ext`: (http code, ms, vantage name) of the same request made from ANOTHER Iran server - the path the users take; `ext_seen` /
    `ext_fails` like data_seen / data_fails for it."""
    if iran.error and foreign.error:
        return "unknown", f"both servers do not answer ({iran.error})"

    if iran.error:
        return "unknown", f"the Iran server does not answer ({iran.error})"

    if foreign.error:
        return "unknown", f"the foreign server does not answer ({foreign.error})"

    for label, side, units in (("Iran", iran, plan.iran_units), ("foreign", foreign, plan.foreign_units)):
        for u in units:
            st = side.units.get(u, "unknown")

            if st != "active":
                return "down", f"{label}: {u} is {st}"

    if plan.iface and iran.iface_state not in ("UP", "UNKNOWN"):
        return "down", f"the GRE interface {plan.iface} is {iran.iface_state or 'missing'} on the Iran server"

    # the users' path decides: an HTTP answer through the forwarded port - from another Iran server, or at least on the server itself -
    # proves the tunnel carries data, whatever a ping over the link says (some routes drop ICMP inside GRE while TCP passes)
    local_ok = iran.data_code not in ("", "000")
    ext_code = ext[0] if ext else None
    ext_ok = ext_code is not None and ext_code != "000"
    ext_silent = ext_seen and ext_code == "000"
    linked = bool(plan.iface and plan.iran_peer)
    lossy = linked and iran.loss_pct is not None and DEGRADED_LOSS <= iran.loss_pct < 100
    silent = linked and (iran.loss_pct is None or iran.loss_pct >= 100)

    if ext_ok or (local_ok and not ext_silent):
        if lossy:
            return "degraded", f"{iran.loss_pct:.0f}% loss on the GRE link"

        return "up", ""

    if ext_silent and local_ok:
        why = f"port {iran.data_port} answers on the server but not from {ext[2] if ext and len(ext) > 2 and ext[2] else 'another Iran server'}: blocked in front of the server"
        return ("down" if ext_fails >= DATA_DOWN_AFTER else "degraded"), why

    if silent:
        return "down", "no answer from the foreign end over the GRE link"

    if lossy:
        return "degraded", f"{iran.loss_pct:.0f}% loss on the GRE link"

    closed = [p for p, ok in iran.listen.items() if not ok]

    if closed:
        return "degraded", "port " + ", ".join(str(p) for p in closed) + " is not listening on the Iran server"

    if data_seen and iran.data_code == "000":   # everything is running and the port listens, yet nothing comes back through the tunnel
        why = f"port {iran.data_port} accepts connections but no data comes back through the tunnel"
        return ("down" if data_fails >= DATA_DOWN_AFTER else "degraded"), why

    return "up", ""


def settle(old_state: str, judged: str, streak: dict | None) -> tuple[str, dict]:
    """Flapping control: between up and degraded, and into unknown (an ssh hiccup), the judged state
    must repeat CONFIRM_CHECKS times before it replaces the stored one. down applies at once (a
    stopped unit is not noise), and so does the first state of a new tunnel. Pure, so the rule is testable."""
    streak = dict(streak or {})

    if streak.get("state") == judged:
        streak["n"] = int(streak.get("n", 0)) + 1
    else:
        streak = {"state": judged, "n": 1}

    if judged == old_state:
        return old_state, streak

    soft = {"up", "degraded", "unknown"}

    if old_state in soft and judged in soft and judged != "down" and streak["n"] < CONFIRM_CHECKS and old_state != "unknown":
        return old_state, streak

    return judged, streak


def flap(raw: str, recent_ok: list[bool]) -> tuple[str, str]:
    """Pure: a check that passed on a link that failed FLAP_BAD of the last FLAP_WINDOW checks is 'degraded', not 'up'. Users of a
    link that answers only now and then see cuts, and the row must not turn green between two failures."""
    window = (list(recent_ok) + [raw == "up"])[-FLAP_WINDOW:]
    fails = sum(1 for ok in window if not ok)

    if raw == "up" and fails >= FLAP_BAD and not all(window[-3:]):      # three clean checks in a row: whatever happened before is over, the users see no cuts
        switches = sum(1 for i in range(1, len(window)) if window[i] != window[i - 1])

        if switches >= 3:        # on and off again and again
            return "degraded", f"the link flaps: {fails} of the last {len(window)} checks failed, users see cuts"

    # a stretch of loss that is over (this check is clean, the link is not switching on and off) is history, not a fault: the tunnel is up again

    return raw, ""


EXT_FRESH = 240              # seconds an answer from the other Iran server is used for; older ones are ignored
_EXT: dict = {"at": 0, "res": {}, "from": [], "one": {}}      # one: single answers asked right after a repair, (host, port) -> (ts, code, ms, vantage)


def ext_for(key: tuple, now: float, cycle_at: float, cycle: dict, one: dict) -> tuple | None:
    """Pure: the external answer a check uses for one tunnel - the single probe made right after a repair when it is newer than the
    cycle's answer (which may be from before the repair and would call a healed tunnel still down), else the cycle's; None when stale."""
    a = cycle.get(key) if now - cycle_at < EXT_FRESH else None
    b = one.get(key)

    if b and now - b[0] < EXT_FRESH and (a is None or b[0] >= cycle_at):
        return tuple(b[1:])

    return a


def external_probe_one(iran_host: str, port: int, owner_id: int) -> tuple | None:
    """Right after a repair: the same request as external_probe_all for ONE tunnel, from the first other Iran server that answers; the
    answer stands in for the cycle's (older) one in the next checks of this tunnel. None when no vantage could be asked."""
    for sid, name, node in _vantages():
        if sid == owner_id:
            continue

        try:
            out = run_script(node, "reach_many", {"TARGETS": f"{iran_host}:{int(port)}"}, timeout=20)
        except (SSHError, ValueError) as e:
            log.warning("external probe of %s:%s from %s failed: %s", iran_host, port, name, str(e)[:120])
            continue

        got = parse_reach(out).get((iran_host, int(port)))

        if got is None:
            continue

        res = (got[0], got[1], name)
        _EXT.setdefault("one", {})[(iran_host, int(port))] = (int(time.time()), *res)
        return res

    return None


def parse_reach(out: str) -> dict[tuple[str, int], tuple[str, float | None]]:
    """Pure: 'REACH host port code seconds' lines -> {(host, port): (code, ms)}; ms only when something answered."""
    res: dict[tuple[str, int], tuple[str, float | None]] = {}

    for line in str(out).splitlines():
        p = line.split()

        if len(p) >= 4 and p[0] == "REACH" and p[2].isdigit():
            code = "000" if set(p[3]) <= {"0"} else p[3]
            ms = None

            if len(p) >= 5 and code != "000":
                try:
                    ms = round(float(p[4]) * 1000, 1)
                except ValueError:
                    ms = None

            res[(p[1], int(p[2]))] = (code, ms)

    return res


def _vantages() -> list[tuple[int, str, Node]]:
    """Up to two online Iran servers to look from: the probe server first (it sits in Iran like the users), then any other."""
    from . import settings as st

    pid = int(st.get("probe").get("server_id") or 0)

    with session() as db:
        rows = [(s.id, s.name, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)))]

    rows.sort(key=lambda r: 0 if r[0] == pid else 1)
    return rows[:2]


def external_probe_all() -> None:
    """Once per cycle: from one or two other Iran servers, one HTTP request to every tunnel's first forwarded port on its Iran server -
    the path the users take. The answers are kept in _EXT for the checks of this cycle; a server never looks at itself."""
    with session() as db:
        targets: dict[tuple[str, int], set[int]] = {}

        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))):
            ports = [int(p) for p in (t.forwarded_ports or []) if str(p).isdigit()]

            if ports:
                targets.setdefault((t.iran.host, ports[0]), set()).add(t.iran_server_id)

    if not targets:
        return

    res: dict = {}
    seen_from = []

    for sid, name, node in _vantages():
        mine = [f"{h}:{p}" for (h, p), owners in targets.items() if sid not in owners]

        if not mine:
            continue

        try:
            out = run_script(node, "reach_many", {"TARGETS": " ".join(mine)}, timeout=45)
        except (SSHError, ValueError) as e:
            log.warning("external probe from %s failed: %s", name, str(e)[:120])
            continue

        seen_from.append(name)

        for key, (code, ms) in parse_reach(out).items():
            cur = res.get(key)

            if cur is None or (cur[0] == "000" and code != "000"):      # a vantage that got an answer wins over one that did not
                res[key] = (code, ms, name)

    _EXT["at"], _EXT["res"], _EXT["from"] = int(time.time()), res, seen_from


def _query(node: Node, plan: Plan, is_iran: bool) -> Side:
    params = {"UNITS": " ".join(plan.iran_units if is_iran else plan.foreign_units),
              "IFACE": plan.iface if is_iran else "",
              "PEER": plan.iran_peer if is_iran else "",
              "PORTS": " ".join(str(p) for p in plan.ports) if is_iran else ""}

    for attempt in (1, 2):
        try:
            return parse_side(run_script(node, "tunnel_side", params, timeout=35))
        except SSHError as e:
            if e.code == 255 and attempt == 1:       # the connection dropped mid-run: one more try before "unknown"
                time.sleep(3)
                continue

            s = Side()
            s.error = str(e)[:160]
            return s
        except ValueError as e:
            s = Side()
            s.error = str(e)[:160]
            return s

    return Side()


def check_tunnel(tunnel_id: int) -> None:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None or not t.enabled:
            return

        plan = plan_for(t)
        iran_node = Node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port)
        foreign_node = Node(t.foreign.host, t.foreign.ssh_user, t.foreign.ssh_port)
        prev = dict(t.detail or {})
        iran_host = t.iran.host

    with ThreadPoolExecutor(2) as ex:
        fi = ex.submit(_query, iran_node, plan, True)
        ff = ex.submit(_query, foreign_node, plan, False)
        iran, foreign = fi.result(), ff.result()

    if (iran.error or foreign.error) and jobs.stopping():
        log.info("check name=%s skipped: the panel is restarting", plan.iface or tunnel_id)
        return

    data_seen = bool(prev.get("data_seen")) or iran.data_code not in ("", "000")
    data_fails = int(prev.get("data_fails", 0)) + 1 if iran.data_code == "000" else 0
    # the same request from another Iran server (this cycle's external probe), when there is a fresh one for this port
    ext = ext_for((iran_host, iran.data_port or (plan.ports[0] if plan.ports else 0)), time.time(), _EXT["at"], _EXT["res"], _EXT.get("one") or {})
    ext_seen = bool(prev.get("ext_seen")) or (ext is not None and ext[0] != "000")
    ext_fails = (int(prev.get("ext_fails", 0)) + 1 if ext[0] == "000" else 0) if ext is not None else int(prev.get("ext_fails", 0))
    state, reason = judge(plan, iran, foreign, data_seen, data_fails, ext, ext_seen, ext_fails)
    now = int(time.time())
    rx = tx = None

    # throughput from the interface counters between two checks
    if iran.rx_bytes is not None and prev.get("rx_bytes") is not None and iran.now and prev.get("now"):
        dt = iran.now - int(prev["now"])

        if dt > 5 and iran.rx_bytes >= prev["rx_bytes"] and iran.tx_bytes >= prev["tx_bytes"]:
            rx = round((iran.rx_bytes - prev["rx_bytes"]) * 8 / dt / 1e6, 2)
            tx = round((iran.tx_bytes - prev["tx_bytes"]) * 8 / dt / 1e6, 2)

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            return

        old = t.state

        if state in ("down", "unknown") and now - t.created_at < GRACE_SECONDS and old == "unknown":
            state = "starting"

        raw = state                  # what this one check found; the stored rows keep this raw signal, never the flap verdict
        recent = [bool(c.ok) for c in db.scalars(select(TunnelCheck).where(TunnelCheck.tunnel_id == t.id, TunnelCheck.ts >= t.created_at + GRACE_SECONDS)
                                                 .order_by(TunnelCheck.id.desc()).limit(FLAP_WINDOW - 1))][::-1]
        state, flap_why = flap(raw, recent)

        if flap_why:
            reason = flap_why

        judged = state
        state, streak = settle(old, judged, prev.get("streak"))

        if state != judged:
            reason = ""          # not confirmed yet: the tunnel keeps its state and its reason

        t.last_check_at = now
        t.last_error = reason[:300] if state != "up" else ""
        # latency: the ping over the link; when that is blocked, how long the users' request took (from the other Iran server, else on the server)
        t.rtt_ms = iran.rtt_ms if iran.rtt_ms is not None else (ext[1] if ext is not None and ext[1] is not None else iran.data_ms)
        t.rx_mbps, t.tx_mbps = rx, tx
        t.detail = {"iran_units": iran.units, "foreign_units": foreign.units, "iface": iran.iface_state,
                    "loss_pct": iran.loss_pct, "listen": {str(k): v for k, v in iran.listen.items()},
                    "rx_bytes": iran.rx_bytes, "tx_bytes": iran.tx_bytes, "now": iran.now,
                    "iran_error": iran.error, "foreign_error": foreign.error, "streak": streak,
                    "data_seen": data_seen, "data_fails": data_fails, "data_code": iran.data_code, "data_ms": iran.data_ms,
                    "ext": ({"code": ext[0], "ms": ext[1], "from": ext[2]} if ext is not None else None), "ext_seen": ext_seen, "ext_fails": ext_fails}
        db.add(TunnelCheck(tunnel_id=t.id, ts=now, ok=raw == "up", rtt_ms=iran.rtt_ms, rx_mbps=rx, tx_mbps=tx, error=reason[:300]))

        if state != old:
            t.state = state
            t.state_since = now
            label = f"{t.name} ({t.iran.name} ⇄ {t.foreign.name})"

            if state == "up" and old in ("down", "degraded"):
                emit(db, "ok", "tunnels", f"Tunnel {label} works again.", t.name)
            elif state == "down":
                emit(db, "bad", "tunnels", f"Tunnel {label} is down: {reason}", t.name)
            elif state == "degraded":
                emit(db, "warn", "tunnels", f"Tunnel {label} is degraded: {reason}", t.name)
            elif state == "unknown":
                emit(db, "warn", "tunnels", f"Tunnel {label} cannot be checked: {reason}", t.name)

        log.info("check name=%s state=%s judged=%s loss=%s rtt=%s rx=%s tx=%s %s", t.name, state, judged, iran.loss_pct, iran.rtt_ms, rx, tx, reason)


def check_all() -> None:
    with session() as db:
        ids = list(db.scalars(select(Tunnel.id).where(Tunnel.enabled.is_(True))))

    try:
        external_probe_all()       # the users' path first: one look from another Iran server at every forwarded port
    except Exception:
        log.exception("external probe failed")

    with ThreadPoolExecutor(max(1, get_settings().ssh_workers // 2)) as ex:
        list(ex.map(check_tunnel, ids))

    with session() as db:
        db.execute(delete(TunnelCheck).where(TunnelCheck.ts < int(time.time()) - KEEP_CHECKS_SECONDS))
