"""CPU balance: a server whose network work sits on ONE core.

On a virtual server the network card often has one receive queue and one interrupt, so every packet - and for a tunnel server that is
all of the traffic - is processed on one core (`ksoftirqd`) while the others idle, and the server is "full" at 40 % of its power.
RPS (receive packet steering) hands the packet processing to all cores. It is a switch in /sys, takes effect at once, never drops a
connection and is undone with the same switch.

  probe(node)          read-only: cores, receive queues, RPS masks, per-core load and NET_RX over two seconds (script cpu_spread)
  judge(facts)         pure: is the network work stuck on one core? -> (skewed, one sentence)
  scan_server(id)      probe + judge, kept in settings `balance`
  apply(aid, id, on)   the Tunnel Doctor's step: turn RPS on (a boot unit keeps it), wait, probe again, keep it when it helped and put it
                       back when the server got worse; every step is in the action's story
  run()                the job (every 10 minutes): busy servers are probed; with `balance.auto` the Doctor applies RPS by itself - it never touches a tunnel,
                       and a running tunnel is not interrupted"""
from __future__ import annotations

import re
import time

from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server
from ..ssh import Node, SSHError, run_action, run_script
from . import settings as st
from .events import emit

log = get_logger("balance")
MIN_BUSY = 55.0          # a server below this CPU is not looked at (nothing to gain)
SKEW_SHARE = 0.8         # this share of the packet work on one core = stuck on one core
MIN_PACKETS = 400        # over the two seconds of the probe: less than this is no traffic to judge by


def parse(out: str) -> dict:
    """Pure: the lines of the cpu_spread script -> facts."""
    f: dict = {"iface": "", "cores": 0, "queues": {}, "unit": "no", "s1": {}, "s2": {}, "n1": [], "n2": []}

    for line in str(out).splitlines():
        p = line.split()

        if not p:
            continue

        if p[0] == "IF" and len(p) > 1:
            f["iface"] = p[1]
        elif p[0] == "CORES" and len(p) > 1 and p[1].isdigit():
            f["cores"] = int(p[1])
        elif p[0] == "RXQ" and len(p) >= 2:
            f["queues"][p[1]] = p[2] if len(p) > 2 else "0"
        elif p[0] in ("S1", "S2") and len(p) >= 4:
            try:
                f["s" + p[0][1]][p[1]] = (int(p[2]), int(p[3]))
            except ValueError:
                pass
        elif p[0] in ("N1", "N2"):
            f["n" + p[0][1]] = [int(x) for x in p[1:] if x.isdigit()]
        elif p[0] == "UNIT" and len(p) > 1:
            f["unit"] = p[1]

    cpus = sorted(set(f["s1"]) & set(f["s2"]), key=lambda c: int(c[3:] or 0))
    busy, net = [], []

    for i, c in enumerate(cpus):
        dt = f["s2"][c][0] - f["s1"][c][0]
        di = f["s2"][c][1] - f["s1"][c][1]
        busy.append(round(max(0.0, min(100.0, (dt - di) * 100.0 / dt)), 1) if dt > 0 else 0.0)
        net.append(max(0, f["n2"][i] - f["n1"][i]) if i < len(f["n1"]) and i < len(f["n2"]) else 0)

    f["busy"], f["net"] = busy, net
    f["rps_on"] = any(re.sub(r"[0,]", "", m) != "" for m in f["queues"].values())
    return f


def judge(f: dict) -> tuple[bool, str]:
    """Pure: (skewed, sentence). Skewed = several cores, real traffic, almost all of it processed on one busy core while the others idle."""
    n, busy, net = int(f.get("cores") or 0), f.get("busy") or [], f.get("net") or []

    if n < 2 or len(busy) < 2:
        return False, "one core: nothing to spread"

    total = sum(net)

    if total < MIN_PACKETS:
        return False, "not enough traffic to judge by"

    top = max(range(len(net)), key=lambda i: net[i])
    share = net[top] / total
    others = [b for i, b in enumerate(busy) if i != top]
    rest = sum(others) / len(others) if others else 0.0

    if f.get("rps_on"):
        return False, f"RPS is on: the busiest core carries {share:.0%} of the packets, the others {rest:.0f} % busy"

    if share >= SKEW_SHARE and busy[top] >= 60 and rest <= busy[top] - 25:
        return True, f"core {top} processes {share:.0%} of the network packets and is {busy[top]:.0f} % busy while the other {len(others)} are {rest:.0f} % busy on average"

    return False, f"the network work is spread well enough (busiest core {share:.0%} of the packets)"


def probe(node: Node) -> dict:
    return parse(run_script(node, "cpu_spread", timeout=30))


def _state() -> dict:
    s = st.get("balance")
    s.setdefault("servers", {})
    return s


def _remember(sid: int, entry: dict) -> None:
    s = _state()
    s["servers"][str(sid)] = {**s["servers"].get(str(sid), {}), **entry, "at": int(time.time())}
    st.put("balance", {"servers": s["servers"]})


def _node_of(sid: int) -> tuple[Node, str]:
    with session() as db:
        srv = db.get(Server, sid)

        if srv is None:
            raise ValueError("server not found")

        return Node(srv.host, srv.ssh_user, srv.ssh_port), srv.name


def scan_server(sid: int) -> dict:
    """Probe one server and keep what was found."""
    node, name = _node_of(sid)
    f = probe(node)
    skewed, why = judge(f)
    entry = {"name": name, "cores": f["cores"], "busy": f["busy"], "net": f["net"], "iface": f["iface"], "rps_on": f["rps_on"], "unit": f["unit"], "skewed": skewed, "why": why}
    _remember(sid, entry)
    return {"id": sid, **entry}


def apply(aid: int, sid: int, on: bool = True, source: str = "manual") -> bool:
    """RPS on (or off = undone) on one server, with its story: before, the switch, after; kept only when it helped (on)."""
    from . import autopilot

    node, name = _node_of(sid)
    f0 = probe(node)

    if not f0["iface"] or not f0["queues"]:
        autopilot._say(aid, f"{name}: the network card has no receive queue that can be steered (nothing to do).", done=True, ok=False)
        return False

    skew0, why0 = judge(f0)
    autopilot._say(aid, f"{name}: {f0['cores']} cores · card {f0['iface']} · RPS {'on' if f0['rps_on'] else 'off'} · {why0}")

    if on and f0["rps_on"]:
        autopilot._say(aid, "RPS is already on: nothing changed.", done=True, ok=True)
        return True

    if not on and not f0["rps_on"] and f0["unit"] in ("no", "disabled", ""):
        autopilot._say(aid, "RPS is already off: nothing changed.", done=True, ok=True)
        return True

    out = run_action(node, "rps_set", {"IFACE": f0["iface"], "MODE": "on" if on else "off"})
    autopilot._say(aid, f"{name}: RPS {'turned on for' if on else 'turned off for'} {f0['iface']} ({out.strip().splitlines()[-1] if out.strip() else 'done'}).")
    time.sleep(25)                                  # the traffic settles; then the same probe again
    f1 = probe(node)
    skew1, why1 = judge(f1)
    b0, b1 = max(f0["busy"] or [0]), max(f1["busy"] or [0])
    avg0, avg1 = sum(f0["busy"]) / max(1, len(f0["busy"])), sum(f1["busy"]) / max(1, len(f1["busy"]))
    autopilot._say(aid, f"After: busiest core {b1:.0f} % (was {b0:.0f} %), all cores on average {avg1:.0f} % (was {avg0:.0f} %). {why1}")

    if on and (b1 > b0 + 15 and avg1 > avg0 + 10):    # clearly worse: put it back
        run_action(node, "rps_set", {"IFACE": f0["iface"], "MODE": "off"})
        autopilot._say(aid, "The server got worse with RPS: it was turned off again.", done=True, ok=False)
        _remember(sid, {"rps_on": False, "skewed": skew0, "why": why0, "applied": "rolled back"})
        return False

    _remember(sid, {"name": name, "cores": f1["cores"], "busy": f1["busy"], "net": f1["net"], "rps_on": f1["rps_on"], "unit": f1["unit"], "skewed": skew1, "why": why1, "applied": "on" if on else "off", "before_busy": b0, "after_busy": b1})

    with session() as db:
        emit(db, "ok", "actions", f"{name}: RPS {'on' if on else 'off'} - the busiest core {b0:.0f} % -> {b1:.0f} % ({source}).", name)

    autopilot._say(aid, "Done.", done=True, ok=True)
    return True


def apply_start(sid: int, on: bool = True, source: str = "manual") -> int:
    from . import autopilot

    _, name = _node_of(sid)
    return autopilot._start(f"cpu {name}", lambda aid: _guard(aid, apply, aid, sid, on, source), action="balance", source=source)


def _guard(aid: int, fn, *args) -> None:
    from . import autopilot

    try:
        fn(*args)
    except (SSHError, ValueError) as e:
        autopilot._say(aid, f"Stopped: {str(e)[:160]}", done=True, ok=False)


def run() -> None:
    """The job: every busy multi-core server is probed; a skewed one gets RPS from the Doctor when `balance.auto` is on (never in read-only mode)."""
    s = get_settings()

    with session() as db:
        rows = [(x.id, x.name, (x.facts or {})) for x in db.scalars(select(Server).where(Server.enabled.is_(True), Server.online.is_(True)))]

    busy = [(sid, name) for sid, name, f in rows if int(f.get("cores") or 1) >= 2 and float(f.get("cpu") or 0) >= MIN_BUSY]

    for sid, name in busy[:6]:
        try:
            r = scan_server(sid)
        except (SSHError, ValueError) as e:
            log.warning("cpu balance: %s not probed: %s", name, str(e)[:100])
            continue

        if r["skewed"] and not r["rps_on"] and _state().get("auto", True) and not s.read_only:
            last = _state()["servers"].get(str(sid), {}).get("tried_at", 0)

            if time.time() - last < 6 * 3600:
                continue

            _remember(sid, {"tried_at": int(time.time())})
            log.info("cpu balance: %s - %s", name, r["why"])

            try:
                apply_start(sid, True, "auto")
            except ValueError:
                pass
