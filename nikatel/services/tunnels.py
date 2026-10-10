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

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import delete, select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel, TunnelCheck
from ..ssh import Node, SSHError, run_script
from .events import emit

log = get_logger("tunnels")

KEEP_CHECKS_SECONDS = 14 * 86400
GRACE_SECONDS = 180          # a tunnel younger than this is "starting", not "down"
DEGRADED_LOSS = 15.0         # % loss over the GRE link (20 pings) that counts as degraded; the Iran-abroad
                             # paths lose 3-10 % of packets on a normal day, that is not an event
CONFIRM_CHECKS = 2           # up <-> degraded flips need this many consecutive checks that agree


@dataclass
class Plan:
    iran_units: list[str]
    foreign_units: list[str]
    iface: str = ""
    iran_peer: str = ""       # the foreign end's overlay IP, pinged from Iran
    ports: list[int] = field(default_factory=list)


def plan_for(t: Tunnel) -> Plan:
    name, typ, tr, port = t.name, t.type.lower(), (t.transport or "tcpmux").lower(), int(t.port or 0)
    ports = [int(p) for p in (t.forwarded_ports or []) if str(p).isdigit()]

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
            s.data_port, s.data_code = int(p[1]), p[2]
        elif p[0] == "NOW" and len(p) >= 2:
            s.now = int(p[1])

    return s


DATA_DOWN_AFTER = 4          # this many failed data probes in a row (4 minutes): the tunnel is down, not just degraded


def judge(plan: Plan, iran: Side, foreign: Side, data_seen: bool = False, data_fails: int = 0) -> tuple[str, str]:
    """(state, reason). Pure, so the rules are testable. `data_seen`: this tunnel has answered an HTTP request before (so silence now
    is a fault, not a port that speaks another protocol); `data_fails`: consecutive probes without an answer, this one included."""
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

    if plan.iface:
        if iran.iface_state not in ("UP", "UNKNOWN"):
            return "down", f"the GRE interface {plan.iface} is {iran.iface_state or 'missing'} on the Iran server"

        if plan.iran_peer:
            if iran.loss_pct is None or iran.loss_pct >= 100:
                return "down", "no answer from the foreign end over the GRE link"

            if iran.loss_pct >= DEGRADED_LOSS:
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

    with ThreadPoolExecutor(2) as ex:
        fi = ex.submit(_query, iran_node, plan, True)
        ff = ex.submit(_query, foreign_node, plan, False)
        iran, foreign = fi.result(), ff.result()

    if (iran.error or foreign.error) and jobs.stopping():
        log.info("check name=%s skipped: the panel is restarting", plan.iface or tunnel_id)
        return

    data_seen = bool(prev.get("data_seen")) or iran.data_code not in ("", "000")
    data_fails = int(prev.get("data_fails", 0)) + 1 if iran.data_code == "000" else 0
    state, reason = judge(plan, iran, foreign, data_seen, data_fails)
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

        judged = state
        state, streak = settle(old, judged, prev.get("streak"))

        if state != judged:
            reason = ""          # not confirmed yet: the tunnel keeps its state and its reason

        t.last_check_at = now
        t.last_error = reason[:300] if state != "up" else ""
        t.rtt_ms = iran.rtt_ms
        t.rx_mbps, t.tx_mbps = rx, tx
        t.detail = {"iran_units": iran.units, "foreign_units": foreign.units, "iface": iran.iface_state,
                    "loss_pct": iran.loss_pct, "listen": {str(k): v for k, v in iran.listen.items()},
                    "rx_bytes": iran.rx_bytes, "tx_bytes": iran.tx_bytes, "now": iran.now,
                    "iran_error": iran.error, "foreign_error": foreign.error, "streak": streak,
                    "data_seen": data_seen, "data_fails": data_fails, "data_code": iran.data_code}
        db.add(TunnelCheck(tunnel_id=t.id, ts=now, ok=judged == "up", rtt_ms=iran.rtt_ms, rx_mbps=rx, tx_mbps=tx, error=reason[:300]))

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

    with ThreadPoolExecutor(max(1, get_settings().ssh_workers // 2)) as ex:
        list(ex.map(check_tunnel, ids))

    with session() as db:
        db.execute(delete(TunnelCheck).where(TunnelCheck.ts < int(time.time()) - KEEP_CHECKS_SECONDS))
