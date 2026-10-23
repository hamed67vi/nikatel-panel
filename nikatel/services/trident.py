"""TRIDENT: one tunnel carried by SEVERAL tunnels of different kinds between the same two servers, the users' NEW connections spread between them by live quality (the owner, 2026-10-23: "64 tunnel types can be built in
the panel - combine them into a strong multi-layer tunnel that gives the best connection in every condition - our own method"; docs/trident.md).

  what it is         a MODE of an existing tunnel (the owner marks it, like the standby). The tunnel is strand 0; its warm standby (services/standby.py) is strand 1; one more tunnel of a third kind, built beside them on
                     its own shifted ports (+30000), is strand 2. They are real tunnels, hidden from every list in the way a standby is (`record.strand_of`). The kinds are chosen to NOT SHARE A FATE: another path
                     (GRE / UDP / TCP / ICMP / ESP / IPIP), another side that dials, another disguise (`traits`, `pick_kind`) - one filter on the route must not cut two strands.
  how it shares      on the Iran server ONE chain of the NAT table (ssh.py `trident_set`): `-m statistic --mode random --probability p -j REDIRECT --to-ports <shifted>` for each strand, p the chance GIVEN that the rules above did not
                     take the connection (`chain_probs`); what no rule takes goes to the tunnel. The chain is replaced in ONE iptables-restore transaction (all or nothing; proved with 58 plan changes under load: not one connection
                     failed or went where it must not), the jumps to it never leave while the trident stands (an empty NAT chain resets the connections it translated). The NAT table sees only the first packet of a flow and conntrack
                     keeps what it decided: a connection that is open is NEVER touched. The share of a strand that falls is lost for its own connections only (a third, not all); the next ones go to the others.
  the weights        from what each strand shows NOW (`target_weights`): its capacity (the light test, `bandwidth.measure`) times a quality factor from its loss, its delay against the best one and how steady its kind has been
                     (`knowledge.stability`); a floor (a healthy strand keeps a few percent so it stays warm and measured) and a ceiling (no strand carries more than 70 %, 90 % when only two are left). A strand that
                     comes back, or is new, is let in by steps (`RAMP`: 10, 25, 50 %, then its own weight, one step per `RAMP_STEP`). A strand that is down gets nothing AT ONCE. Small drifts are ignored (`should_apply`).
  the safety net     while the Trident is not active (not yet applied, or its job stands still for a minute: `owns`) the standby hands the users' ports over exactly as before. The Trident never touches DNS, never
                     removes the tunnel and does nothing in the Doctor's mode *off*. Switching it off parks it: all new connections go to the tunnel, the open ones stay where they are until they end.

The pure helpers (`traits`, `pick_kind`, `quality`, `target_weights`, `advance`, `should_apply`, `chain_probs`, `plan_text`, `parse_list`) carry the decisions and are tested."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import ActionLog, Server, Tunnel, is_temp
from ..ssh import SSHError, run_action, run_script
from . import (
    actions,
    autopilot,
    bandwidth,
    build_other,
    builder,
    knowledge,
    lanes,
    license,
    standby,
    tunnel_test,
    tunnels,
    weather,
)
from . import settings as st
from .events import emit

log = get_logger("trident")

STRAND_SHIFT = 30000           # the users' port P is 30000 + P on the Iran server for the extra strand (the standby has 20000)
TICK = 5                       # the job runs this often
CHECK_EVERY = 10               # the extra strand is looked at this often (the standby has its own look every 15 s)
USABLE_AFTER = 30              # a strand that has been up this long gets a share (it settled)
RAMP = (0.10, 0.25, 0.50, 1.0)          # the most a strand may carry on each step of its way in: stage 0, 1, 2, 3 (3 = no limit but the ceiling)
RAMP_STEP = 120                # seconds a strand stays on one step while it is healthy
FLOOR = 0.05                   # the least a usable strand gets (it stays warm and measured)
CEIL_MANY = 0.70               # the most one strand carries when three are usable ...
CEIL_TWO = 0.90                # ... and when two are
LOSS_FREE = 1.0                # a loss under this (percent) costs a strand nothing
LOSS_SCALE = 3.0               # every 3 points over it halve the quality (1 / (1 + over / 3))
RTT_SLACK = 40.0               # a strand 40 ms slower than the best one has about half its quality factor from delay ... (best + 40) / (rtt + 40)
STAB_UNKNOWN = 0.85            # a kind whose stability is not proven yet counts this much (`knowledge.STEADY_UNKNOWN`)
CAP_DEFAULT = 100.0            # the capacity (Mb/s) of a strand nobody measured and no strand did: only the quality factors decide
ADJUST_SMALL = 0.08            # a drift of this many points (a share of 1) ...
ADJUST_EVERY = 600             # ... is applied at most this often
ADJUST_BIG = 0.25              # a drift of this many points is applied at once, but not more often than ...
ADJUST_BIG_EVERY = 60          # ... this
CAP_FRESH = 6 * 3600           # a measured capacity of a strand is used this long
CAP_RETRY = 1800               # a strand that could not be measured is left alone this long
BUILD_WAIT = 1800              # a build that has not finished by now is given up
BUILD_RETRY = 30               # a strand that cannot be planned or started now (the reason is in the window) is tried again after this
UNHEALTHY_AFTER = 45           # an extra strand that is not up this long gets its units restarted ...
REBUILD_AFTER = 300            # ... and is built again after this long
RESTART_EVERY = 300
ALIVE_FOR = 60                 # the Trident "owns" the users' ports only while its job answered in the last minute (else the standby's handover is free again)
RECONCILE_EVERY = 120
PARK_CLEAN_EVERY = 180
KIND_SKIP = 6 * 3600           # a kind whose build failed as a strand is not chosen again for this long
RENEW_AFTER = 2 * 3600         # an extra strand whose family has been under pressure or blocked on the route this long, while another family is open, is built again as a kind of that family ...
RENEW_EVERY = 6 * 3600         # ... at most this often per tunnel
MISSING_GRACE = 3 * 3600       # a marked tunnel whose row is gone this long is not coming back
MAX_PLAN = 1199                # the entries of one trident_set plan (ssh.py allows 1200)
_LOCK = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_BUILDS: dict[str, dict] = {}          # tunnel name -> {"aid", "name", "kind", "at"} the extra strand being built
_NOTE: dict[str, str] = {}             # tunnel name -> why nothing is happening (shown in the window)
_ALIVE: dict[str, float] = {}          # tunnel name -> when its step last finished without a crash
_CHECKED: dict[int, float] = {}
_RESTARTED: dict[int, float] = {}
_MEASURING: set[int] = set()
_MISSING: dict[str, int] = {}
_RETIRED: dict[str, tuple] = {}        # old tunnel name -> (id of the Iran server it stood on): rules left by a rename, taken off by the job
_RETRY_AT: dict[str, float] = {}       # tunnel name -> no new try to build its extra strand before this time
_FORCE: set[str] = set()               # tunnels whose plan is put at the next turn whatever the weights did (the owner's Rebalance, rules that are not what the panel put)
_CHECKING: set[int] = set()            # strands being looked at right now (the look runs in a thread: the conductor never waits for ssh)
_REMOVING: set[str] = set()            # tunnels whose extra strand is being removed right now
_STATE: dict = {"init": False, "reconciled": 0.0, "cleaned": 0.0}


# ------------------------------------------------------------------ pure helpers (tested)
_BP_L4 = {"tcpmux": "tcp", "tcp": "tcp", "stealth": "tcp", "ws": "tcp", "wsmux": "tcp", "wss": "tcp", "wssmux": "tcp", "pck": "tcp-raw", "kcp": "udp", "quic": "udp", "xdi": "icmp"}
_BPD_L4 = {"udp": "udp", "quic": "udp", "pck": "tcp-raw", "xdi": "icmp"}
_FAMILY = {"tcp": "tcp", "tcp-raw": "tcp", "udp": "udp", "icmp": "icmp"}
_LINK_FAMILY = {"wg": "udp", "fou": "udp", "gue": "udp", "ipipfou": "udp", "ipipgue": "udp", "ipsecnat": "udp", "ipip": "ipip", "ipsec": "esp"}


def traits(kind: str) -> dict:
    """What a filter on the route sees of a tunnel kind: `family` (what must pass between the two servers: gre · ipip · esp · udp · tcp · icmp - two kinds of one family fall to the same filter), `l4` (the packets: tcp ·
    tcp-raw · udp · icmp), `dial` (which server opens the connection: iran · foreign - a route that is filtered one way only cuts one of them) and `tls` (it looks like HTTPS). A guess from the name of the kind, made once
    and tested; a kind nobody knows counts as plain TCP dialled by the foreign server."""
    head, _, tail = str(kind).partition(":")

    if kind in knowledge.GRE_KINDS:
        return {"family": "gre", "l4": "tcp", "dial": "foreign", "tls": kind == "wssmux"}

    if head in ("wgchisel", "wgchiselfwd"):                     # WireGuard carried inside a Chisel TLS WebSocket: it is TCP on one port
        return {"family": "tcp", "l4": "tcp", "dial": "iran" if head == "wgchiselfwd" else "foreign", "tls": True}

    if head in _LINK_FAMILY:                                    # Backhaul over another link: what must pass is the link
        return {"family": _LINK_FAMILY[head], "l4": "tcp", "dial": "foreign", "tls": tail.endswith("wssmux")}

    if head == "bp":
        l4 = _BP_L4.get(tail, "tcp")
        return {"family": _FAMILY[l4], "l4": l4, "dial": "foreign", "tls": tail in ("wss", "wssmux")}

    if head == "bpd":
        l4 = _BPD_L4.get(tail, "udp")
        return {"family": _FAMILY[l4], "l4": l4, "dial": "iran", "tls": False}

    if head == "paqet":
        return {"family": "tcp", "l4": "tcp-raw", "dial": "iran", "tls": False}

    if head == "chisel":
        return {"family": "tcp", "l4": "tcp", "dial": "iran" if tail.startswith("fwd") else "foreign", "tls": "tls" in tail}

    return {"family": "tcp", "l4": "tcp", "dial": "foreign", "tls": False}


def clash(kind: str, chosen: list[str]) -> tuple[int, int, int]:
    """How much `kind` shares its fate with the kinds already chosen: (same family, same side that dials, same packets) - smaller is better, the family counts first."""
    t = traits(kind)
    others = [traits(c) for c in chosen]
    return (sum(t["family"] == o["family"] for o in others), sum(t["dial"] == o["dial"] for o in others), sum(t["l4"] == o["l4"] for o in others))


def pick_kind(kinds: list[str], chosen: list[str], pair: dict | None, now: int, allowed=lambda k: True, skip: frozenset | set = frozenset(), states: dict | None = None) -> tuple[str, str]:
    """(the kind of the next strand, the sentence that says why) - ('', why not) when none can be. `kinds`: the Doctor's candidates for this pair, best first (`knowledge.plan_for`); `chosen`: the kinds the tunnel
    and its other strands already have. Left out: those, what failed on this pair lately, what the licence does not allow and what a strand build failed with lately (`skip`). Chosen: a kind whose family is not
    BLOCKED on the route today (`states`: the weather, {family: open | pressure | blocked}; a blocked family only when nothing else is left), then the kind that shares the least fate with the chosen ones (another
    family first, then another side that dials, then other packets), a family under pressure after one that is open; among those a kind known to connect on this pair before one nobody tried; then the order of `kinds`."""
    bad = set(knowledge.blocked(pair, now))
    pool = [k for k in kinds if k not in chosen and k not in bad and k not in skip and allowed(k)]

    if not pool:
        return "", "no other tunnel type is left for a strand (every one is taken, failed on this pair lately, is not allowed by the licence or failed as a strand)"

    known = {r["kind"] for r in knowledge.ranked(pair, now, include_poor=True)}
    wx = states or {}
    best = min(enumerate(pool), key=lambda it: (wx.get(traits(it[1])["family"]) == "blocked", clash(it[1], chosen), wx.get(traits(it[1])["family"]) == "pressure", it[1] not in known, it[0]))[1]
    c = clash(best, chosen)
    state = wx.get(traits(best)["family"])
    note = ("known to connect on this pair" if best in known else "not tested on this pair yet") + (", another family than the others (a filter that cuts one does not cut this)" if c[0] == 0 else ", the same family as another strand (nothing better was left)")
    note += f"; its family is {'under pressure' if state == 'pressure' else 'blocked'} on this route today" if state in ("pressure", "blocked") else ""
    return best, f"{best}: {note}"


def quality(loss: float | None, rtt: float | None, best_rtt: float | None, stab: float | None) -> float:
    """0 - 1: how good a strand is NOW. Loss: free up to LOSS_FREE percent, then every LOSS_SCALE points halve it. Delay: (best + 40) / (this + 40) against the best strand. Stability of the kind: its proven
    stability (0 - 1), or STAB_UNKNOWN when it was tested too few times. A number that is not known costs nothing."""
    q = 1.0

    if loss is not None:
        q /= 1.0 + max(0.0, float(loss) - LOSS_FREE) / LOSS_SCALE

    if rtt is not None and best_rtt is not None:
        q *= (float(best_rtt) + RTT_SLACK) / (float(rtt) + RTT_SLACK)

    q *= STAB_UNKNOWN if stab is None else max(0.3, min(1.0, float(stab)))
    return max(0.01, min(1.0, q))


def target_weights(items: list[dict]) -> list[float]:
    """The share of each strand of the users' NEW connections, summing to 1 (all 0 when none is usable). `items`: one per strand in order (index 0 = the tunnel), each {"usable", "cap" (Mb/s or None),
    "rtt", "loss", "stab", "stage"}. A strand gets its capacity x quality as a share; then it is held inside [floor, ceiling] where the ceiling is the lower of the global ceiling and the step of its way in
    (`RAMP`). When the ceilings cannot add up to 1 (everybody is on the first steps) the strands that are fully in take the rest."""
    n = len(items)
    use = [i for i in range(n) if items[i].get("usable")]
    w = [0.0] * n

    if not use:
        return w

    if len(use) == 1:
        w[use[0]] = 1.0
        return w

    caps = sorted(float(items[i]["cap"]) for i in use if items[i].get("cap"))
    default = caps[len(caps) // 2] if caps else CAP_DEFAULT
    rtts = [float(items[i]["rtt"]) for i in use if items[i].get("rtt") is not None]
    best = min(rtts) if rtts else None
    raw = {i: float(items[i].get("cap") or default) * quality(items[i].get("loss"), items[i].get("rtt"), best, items[i].get("stab")) * float(items[i].get("wx") or 1.0) for i in use}      # wx: the weather of the strand's family on the route (services/weather.py) - 1 when nothing is known
    top = CEIL_TWO if len(use) == 2 else CEIL_MANY
    ceil = {i: min(top, RAMP[max(0, min(3, int(items[i].get("stage", 3))))]) for i in use}
    floor = {i: min(FLOOR, ceil[i]) for i in use}

    if sum(ceil.values()) < 1.0:                                        # everybody is on the first steps: the ones that are fully in may take more than the ceiling
        for i in use:
            if int(items[i].get("stage", 3)) >= 3:
                ceil[i] = 1.0

    # still under 1: the ceilings are lifted ONE AT A TIME, the strand that has been in the longest first (the highest step, then the earliest step time), until they add up - never all at once: on the real pair
    # (2026-10-23) a tunnel that had just come back took 56 % in the same second as two strands of one minute, because all three were on step 0 and every ceiling went
    for i in sorted(use, key=lambda j: (-int(items[j].get("stage", 0)), int(items[j].get("stage_at") or 0))):
        if sum(ceil.values()) >= 1.0:
            break

        ceil[i] = 1.0

    free, fixed, room = set(use), {}, 1.0

    for _ in range(len(use) + 2):
        total = sum(raw[i] for i in free)
        share = {i: (room * raw[i] / total if total > 0 else room / len(free)) for i in free}
        over = {i for i in free if share[i] > ceil[i] + 1e-12}
        under = {i for i in free if share[i] < floor[i] - 1e-12}

        if not over and not under:
            break

        for i in over:                                                  # (the ceilings first: a strand held down gives its share to the others, who may then hit their own ceiling)
            fixed[i] = ceil[i]

        if not over:
            for i in under:
                fixed[i] = floor[i]

        for i in list(fixed):
            free.discard(i)

        room = 1.0 - sum(fixed.values())

        if not free:
            break

    result = dict(fixed)

    for i in free:
        total = sum(raw[j] for j in free)
        result[i] = room * raw[i] / total if total > 0 else room / len(free)

    s = sum(result.values())

    for i in use:
        w[i] = result[i] / s if s > 0 else 1.0 / len(use)

    return w


def advance(stage: int, usable: bool, up_for: float, in_stage_for: float) -> int:
    """The step of a strand's way in after one more look: a strand that is not usable goes back to step 0; a usable one moves up one step when it has been healthy on its step for RAMP_STEP seconds."""
    if not usable:
        return 0

    if stage < 3 and in_stage_for >= RAMP_STEP and up_for >= USABLE_AFTER:
        return stage + 1

    return stage


def should_apply(prev: list[float] | None, new: list[float], now: float, applied_at: float, urgent: bool) -> bool:
    """Whether the rules on the server are rewritten now. At once: nothing stands yet, or something changed that cannot wait (a strand fell or came back, a step of the way in). A drift of ADJUST_BIG points
    after ADJUST_BIG_EVERY seconds, a drift of ADJUST_SMALL points after ADJUST_EVERY seconds; smaller drifts are left (a rule is rewritten in one transaction, but the shares should not dance)."""
    if prev is None or len(prev) != len(new) or urgent:
        return True

    drift = max(abs(a - b) for a, b in zip(prev, new))
    age = now - applied_at

    return (drift >= ADJUST_BIG and age >= ADJUST_BIG_EVERY) or (drift >= ADJUST_SMALL and age >= ADJUST_EVERY)


def chain_probs(weights: list[float]) -> list[float]:
    """The chance of each rule of the chain GIVEN that the rules above it did not take the connection. `weights`: the share of each strand in the order of the rules (the tunnel is not among them: what no
    rule takes goes to it). 0.3 and 0.3 -> 0.3 and 0.42857 (0.3 / 0.7); a rule that must take everything that reaches it is 1."""
    out, left = [], 1.0

    for x in weights:
        x = max(0.0, float(x))

        if x <= 0 or left <= 1e-9:
            out.append(0.0)
            continue

        p = min(1.0, x / left)
        out.append(1.0 if p >= 0.999999995 else p)
        left -= x

    return out


def prob_text(p: float) -> str:
    """'1' for a rule with no statistic match, else the kernel's text '0.xxxxxxxx'."""
    if p >= 0.999999995:
        return "1"

    return f"{max(p, 0.0):.8f}"


def plan_text(users_ports: list[int], strand_ports: list[list[int]], probs: list[float], protos: list[str]) -> str:
    """The `trident_set` plan: '<proto>:<users' port>:<shifted port>:<probability>' for every protocol, every users' port and every strand with a chance above 0, the rules of one port in the order of the strands.
    ValueError when a strand has not the ports of the tunnel or the plan would be too long."""
    out = []

    for proto in protos:
        for j, port in enumerate(users_ports):
            for ports, p in zip(strand_ports, probs):
                if p <= 0:
                    continue

                if len(ports) != len(users_ports):
                    raise ValueError("a strand does not forward the ports of the tunnel")

                t = prob_text(p)

                if t == "0.00000000":
                    continue

                out.append(f"{proto}:{int(port)}:{int(ports[j])}:{t}")

    if len(out) > MAX_PLAN:
        raise ValueError("the tunnel forwards too many ports to share")

    return ",".join(out)


def parse_list(out: str) -> dict[str, dict]:
    """The answer of the `trident_list` script -> {name: {"rules": [(proto, users' port, shifted port, probability)], "jumps": [(proto, users' port, chain, shadowed)]}}."""
    res: dict[str, dict] = {}

    for line in str(out).splitlines():
        p = line.split()

        if len(p) == 6 and p[0] == "TRULE" and p[3].isdigit() and p[4].isdigit():
            try:
                res.setdefault(p[1], {"rules": [], "jumps": []})["rules"].append((p[2], int(p[3]), int(p[4]), float(p[5])))
            except ValueError:
                continue
        elif len(p) == 6 and p[0] == "TJUMP" and p[3].isdigit() and p[5].isdigit():
            res.setdefault(p[1], {"rules": [], "jumps": []})["jumps"].append((p[2], int(p[3]), p[4], int(p[5])))

    return res


def same_plan(have: list[tuple], plan: str) -> bool:
    """Whether the rules read from the server are the plan (the same rules, in the same order, the same chances within a millionth)."""
    want = []

    for e in [x for x in plan.split(",") if x]:
        proto, port, to, prob = e.split(":")
        want.append((proto, int(port), int(to), 1.0 if prob == "1" else float(prob)))

    if len(want) != len(have):
        return False

    return all(a[:3] == b[:3] and abs(a[3] - b[3]) < 1e-6 for a, b in zip(sorted(want, key=lambda x: x[:3]), sorted(have, key=lambda x: x[:3])))


def strand_name(base: str, taken: set[str]) -> str:
    """The name of the extra strand: the standby's rule (`<base>-sb`, `-sb2` ... in at most 15 characters), so it is the first free one after the standby's."""
    return standby.standby_name(base, taken)


# ------------------------------------------------------------------ the mark, who owns the users' ports
def _doc() -> dict:
    return st.get("trident")


def marks() -> dict:
    return dict(_doc().get("tunnels") or {})


def _applied(name: str) -> dict:
    return dict((_doc().get("applied") or {}).get(name) or {})


def _set_applied(name: str, data: dict | None) -> None:
    with _LOCK:
        d = _doc()
        cur = dict(d.get("applied") or {})

        if data is None:
            cur.pop(name, None)
        else:
            cur[name] = data

        st.put("trident", {"applied": cur})


def _merge_applied(name: str, changes: dict, cap: tuple[str, dict] | None = None) -> dict:
    """Change some fields of the record of `name` in one read-modify-write under the lock (the job and the speed-test threads write it: neither may lose the other's fields). `cap`: (strand index, entry) to put in `caps`."""
    with _LOCK:
        cur = dict(_doc().get("applied") or {})
        rec = dict(cur.get(name) or {})
        rec.update(changes)

        if cap is not None:
            caps = dict(rec.get("caps") or {})
            caps[cap[0]] = cap[1]
            rec["caps"] = caps

        cur[name] = rec
        st.put("trident", {"applied": cur})
        return rec


def owns(name: str) -> bool:
    """Whether the Trident of tunnel `name` is the writer of the NAT chain of its users' ports right now: marked, rules put at least once, and its job answered in the last minute. While this is False the standby's
    handover and the lanes work as if there were no Trident (a Trident whose job stands still must not leave a cut tunnel without its safety net)."""
    return time.time() - _ALIVE.get(name, 0.0) < ALIVE_FOR and name in marks() and bool(_applied(name).get("at"))


def _lock(name: str) -> threading.RLock:
    with _LOCK:
        return _LOCKS.setdefault(name, threading.RLock())


def _touch(server_id: int) -> None:
    with _LOCK:
        d = _doc()
        t = [int(x) for x in d.get("touched") or []]

        if server_id not in t:
            st.put("trident", {"touched": t + [server_id]})


def _failed(name: str) -> dict:
    return dict((_doc().get("failed") or {}).get(name) or {})


def _set_failed(name: str, data: dict | None) -> None:
    with _LOCK:
        cur = dict(_doc().get("failed") or {})

        if data is None:
            cur.pop(name, None)
        else:
            cur[name] = data

        st.put("trident", {"failed": cur})


# ------------------------------------------------------------------ what the job reads
def _node(host: str, user: str, port: int):
    return standby._node(host, user, port)


def strand_row(db, name: str) -> Tunnel | None:
    return next((t for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))) if (t.record or {}).get("strand_of") == name), None)


def _stale(t: Tunnel, p: dict | None) -> str:
    """Why an extra strand no longer fits its tunnel ('' = it does): other servers, other ports."""
    if p is None:
        return ""

    if (t.iran_server_id, t.foreign_server_id) != (p["iran_id"], p["foreign_id"]):
        return "the tunnel runs between other servers now"

    try:
        want = standby.norm_ports(standby.shift_mapping(p["mapping"], set(), STRAND_SHIFT)[0])
        have = standby.norm_ports((t.record or {}).get("port_mapping") or "")
    except ValueError:
        return "the ports of the tunnel cannot be shifted"

    return "" if want == have else "the forwarded ports of the tunnel changed"


def _xview(t: Tunnel, p: dict | None) -> dict:
    """A tunnel or a strand as plain data for the conductor: state, delay, loss, kind, ports."""
    rec = t.record or {}
    return {"id": t.id, "name": t.name, "state": t.state, "state_since": int(t.state_since or 0), "last_check_at": int(t.last_check_at or 0), "kind": autopilot.kind_of(t.type, t.transport, rec),
            "ports": [int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()], "udp": bool((rec.get("opts") or {}).get("accept_udp")), "rtt": t.rtt_ms,
            "loss": (t.detail or {}).get("loss_pct"), "last_error": t.last_error or "", "iran_id": t.iran_server_id, "foreign_id": t.foreign_server_id, "created_at": int(t.created_at or 0),
            "stale": _stale(t, p) if rec.get("strand_of") else "", "iran": (t.iran.host, t.iran.ssh_user, t.iran.ssh_port), "iran_off": t.iran.enabled is not False and t.iran.online is False,
            "bw": dict(rec.get("bw") or {})}


def _read(name: str) -> dict:
    """{"p": the tunnel, "s1": its standby, "s2": the extra strand, "sb": the standby view of services/standby.py} as plain data (None where there is no such row)."""
    v = standby._view(name)
    p, sb = v["p"], v["s"]

    with session() as db:
        t0 = db.scalars(select(Tunnel).where(Tunnel.name == name)).first()
        px = _xview(t0, None) if t0 is not None and p is not None else None
        r1 = standby_row_by_id(db, sb["id"]) if sb is not None else None
        s1 = _xview(r1, p) if r1 is not None else None
        r2 = strand_row(db, name)
        s2 = _xview(r2, p) if r2 is not None else None

    if px is not None and p is not None:
        px.update({"mapping": p["mapping"], "ports": p["ports"], "off": p["off"]})

    if s1 is not None and sb is not None:
        s1.update({"stale": sb["stale"], "use": sb["use"], "parked": sb["parked"]})

    return {"p": px, "pv": p, "s1": s1, "s2": s2, "sb": sb}


def standby_row_by_id(db, tid: int) -> Tunnel | None:
    return db.get(Tunnel, tid)


def _taken_ports(iran_id: int) -> set[int]:
    return standby._taken_ports(iran_id, "")


# ------------------------------------------------------------------ the extra strand: plan, build, mend, remove
def build_plan(name: str, kind: str = "") -> dict:
    """What the extra strand would be: its name, kind and ports - or ValueError with the reason there is none (nothing is built)."""
    v = _read(name)
    p, s1 = v["p"], v["s1"]

    if p is None:
        raise ValueError("tunnel not found")

    if not p["ports"]:
        raise ValueError("this tunnel forwards no port, so there is nothing to share")

    why = standby._busy_reason(p["iran_id"], p["foreign_id"])

    if why:
        raise ValueError(why)

    now = int(time.time())
    ports_text, mapping = standby.shift_mapping(p["mapping"], _taken_ports(p["iran_id"]), STRAND_SHIFT)
    chosen = [p["kind"]] + ([s1["kind"]] if s1 else [])

    if kind:
        if kind in chosen or not license.tunnel_kind_allowed(kind):
            raise ValueError(f"{kind} cannot be the extra strand here")

        k, note = kind, f"{kind}: asked for"
    else:
        pair = knowledge.pair(p["iran_id"], p["foreign_id"])
        kinds, _ = knowledge.plan_for(p["iran_id"], p["foreign_id"], p["kind"], False, False, now=now)
        skip = {x for x, at in (_failed(name).get("kinds") or {}).items() if now - int(at) < KIND_SKIP}
        k, note = pick_kind(kinds, chosen, pair, now, allowed=license.tunnel_kind_allowed, skip=skip, states=weather.states_for(p["iran_id"], p["foreign_id"]))

        if not k:
            raise ValueError(note)

    with session() as db:
        taken = {t.name for t in db.scalars(select(Tunnel))}

    return {"name": strand_name(name, taken), "kind": k, "note": note, "ports": ports_text, "mapping": mapping}


def build_strand(name: str, kind: str = "") -> int:
    """Start the build of the extra strand of tunnel `name`: a real tunnel of the chosen kind on the shifted ports (+30000), hidden like a standby, as a build action (its id). ValueError with the reason when it
    must not be built now."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only: nothing is built")

    v = _read(name)
    p, s1 = v["p"], v["s1"]

    if p is None:
        raise ValueError("tunnel not found")

    if v["s2"] is not None:
        raise ValueError("this tunnel already has its extra strand")

    if name in _BUILDS:
        raise ValueError("its extra strand is being built")

    if s1 is None or s1["state"] != "up":
        raise ValueError("the extra strand is built after the standby is up")

    if p["state"] != "up":
        raise ValueError(f"the tunnel is {p['state']}: a strand is built when it is up")

    if name in autopilot.repairing_names():
        raise ValueError("a repair works on this tunnel: the strand is built after it")

    if autopilot.storm_now():
        raise ValueError("several servers are off or many tunnels are down: nothing is built now")

    plan = build_plan(name, kind)
    k, tr = tunnel_test.kind_parts(plan["kind"])
    iran_id, foreign_id = p["iran_id"], p["foreign_id"]

    if k == "backhaul":
        opts = {} if tunnel_test.kind_link(plan["kind"]) == "gre" else {"link": tunnel_test.kind_link(plan["kind"])}          # (an automatic build never installs a package: the Doctor's rule)
        aid = builder.start_build(iran_id, foreign_id, plan["ports"], tr, forced_name=plan["name"], opts=opts, standby_of=name, hidden_key="strand_of")
    else:
        aid = build_other.start_build(iran_id, foreign_id, plan["ports"], k, tr, {}, forced_name=plan["name"], measure="latency", standby_of=name, hidden_key="strand_of")

    with _LOCK:
        _BUILDS[name] = {"aid": aid, "name": plan["name"], "kind": plan["kind"], "at": int(time.time())}

    _NOTE.pop(name, None)
    log.info("trident: building the extra strand %s for %s (%s)", plan["name"], name, plan["note"])
    return aid


def _collect() -> None:
    """The strand builds that ended: a good one clears the failures, a bad one is counted (and its kind is not chosen again for a while)."""
    for name, b in list(_BUILDS.items()):
        with session() as db:
            e = db.get(ActionLog, b["aid"])
            done, ok, last = (bool(e.finished_at), bool(e.ok), (list(e.story or []) or [""])[-1]) if e is not None else (True, False, "the build is gone")

        if not done:
            if time.time() - b["at"] > BUILD_WAIT:
                with _LOCK:
                    _BUILDS.pop(name, None)

            continue

        with _LOCK:
            _BUILDS.pop(name, None)

        if ok:
            _set_failed(name, None)
            continue

        f = _failed(name)
        kinds = dict(f.get("kinds") or {})
        kinds[b["kind"]] = int(time.time())
        n = int(f.get("n") or 0) + 1
        _set_failed(name, {"n": n, "at": int(time.time()), "why": str(last)[:200], "kinds": kinds})
        log.info("trident: the build of %s for %s failed (%s): next try in %s s", b["name"], name, str(last)[:120], standby.retry_wait(n))


def remove_strand(name: str) -> list[str]:
    """Take the extra strand away: its share is already 0 (the caller parked or changed the plan), then the tunnel itself from both servers."""
    with _lock(name):
        with session() as db:
            t = strand_row(db, name)

            if t is None:
                return []

            tid = t.id
            off = any(srv is not None and srv.online is False for srv in (t.iran, t.foreign))

        return builder.delete_tunnel(tid, skip_offline=off)


def _restart_strand(s: dict) -> None:
    standby._restart(s)


# ------------------------------------------------------------------ the rules on the Iran server
def _act(node, action: str, params: dict) -> str:
    return run_action(node, action, params, timeout=40)


def _put(node, name: str, plan: str) -> int:
    out = _act(node, "trident_set", {"NAME": name, "PLAN": plan})
    m = re.search(rf"TRIDENT_SET {re.escape(name)} (\d+)", out)
    want = len([x for x in plan.split(",") if x])

    if not m or int(m.group(1)) != want:
        raise RuntimeError("the trident was not put: " + (out.split("TRIDENT_FAIL", 1)[-1].strip() if "TRIDENT_FAIL" in out else out.strip()[-160:]))

    return want


def _off(node, name: str, park: int) -> tuple[int, int, int]:
    """trident_off: (redirects left, rules of the tag left, connections on the shifted ports). RuntimeError when the answer is not understood."""
    out = _act(node, "trident_off", {"NAME": name, "PARK": str(park)})
    m = re.search(rf"TRIDENT_OFF {re.escape(name)} (\d+) (\d+) (\d+)", out)

    if not m:
        raise RuntimeError("the trident rules were not taken off: " + out.strip()[-160:])

    return int(m.group(1)), int(m.group(2)), int(m.group(3))


# ------------------------------------------------------------------ the conductor
def _stab_of(pair: dict | None, kind: str, now: int) -> float | None:
    e = ((pair or {}).get("kinds") or {}).get(kind)
    s = knowledge.stability(e, now)
    return None if s is None else s["pct"] / 100.0


def _cap_of(item: dict, applied: dict, now: int) -> float | None:
    """The capacity (Mb/s) of a strand: the tunnel's usual level of its own light test; a strand's own measurement kept in the trident's record."""
    if item["idx"] == 0:
        return lanes.cap_of(item.get("bw"), now)

    c = (applied.get("caps") or {}).get(str(item["idx"])) or {}

    if c.get("v") and c.get("kind") == item["kind"] and now - int(c.get("at") or 0) < CAP_FRESH:
        return float(c["v"])

    return None


def _measure_async(name: str, item: dict) -> None:
    """One light test of a strand (two connections, 3 s): its capacity, kept for CAP_FRESH. In a thread - the job never waits for a speed test."""
    sid = item["id"]

    with _LOCK:
        if sid in _MEASURING:
            return

        _MEASURING.add(sid)

    def work():
        idx = str(item["idx"])

        try:
            bw, _ = bandwidth.measure(sid)
            down = float(bw.get("down") or 0)

            if down > 0:
                _merge_applied(name, {}, (idx, {"v": round(down, 1), "at": int(time.time()), "kind": item["kind"]}))
            else:
                _merge_applied(name, {}, (idx, {"v": 0, "at": 0, "kind": item["kind"], "fail_at": int(time.time())}))
        except (ValueError, RuntimeError, SSHError) as e:
            _merge_applied(name, {}, (idx, {"v": 0, "at": 0, "kind": item["kind"], "fail_at": int(time.time()), "why": str(e)[:120]}))
        except Exception:
            log.exception("trident: measuring %s crashed", item.get("name"))
        finally:
            with _LOCK:
                _MEASURING.discard(sid)

    threading.Thread(target=work, name=f"trident-measure-{item['name']}", daemon=True).start()


def _items(name: str, v: dict, applied: dict, now: int) -> list[dict]:
    """The strands as the conductor reads them: index 0 the tunnel, 1 its standby, 2 the extra strand (an item with no row is None)."""
    ref = v["p"] or v.get("ref")
    pair = knowledge.pair(ref["iran_id"], ref["foreign_id"]) if ref else None
    stages = list(applied.get("stages") or [3, 0, 0])
    since = list(applied.get("stage_at") or [now, now, now])
    out = []

    was = list(applied.get("usable") or [])

    for idx, row in enumerate((v["p"], v["s1"], v["s2"])):
        if row is None:
            out.append(None)
            continue

        up_for = now - int(row["state_since"] or now) if row["state"] in ("up", "degraded") else 0
        before = bool(was[idx]) if idx < len(was) else False
        usable = row["state"] in ("up", "degraded") and not row.get("stale") and (up_for >= USABLE_AFTER or before)          # (a strand that had a share keeps it through a change of its own state up <-> degraded; one that was down has to settle first)

        if idx == 1 and (row.get("use") or {}):
            usable = False                     # a handover of the standby stands: the standby job gives it back first

        stage = int(stages[idx]) if idx < len(stages) else 0
        stage_at = int(since[idx]) if idx < len(since) else now

        if usable and not before:              # it enters now: the tunnel the first time the trident looks at it is fully in; a strand, or the tunnel after it was down, starts on the first step
            stage, stage_at = (3 if idx == 0 and not was else 0), now

        item = {"idx": idx, "id": row["id"], "name": row["name"], "kind": row["kind"], "state": row["state"], "up_for": up_for, "usable": usable, "rtt": row["rtt"], "loss": row["loss"],
                "ports": row["ports"], "udp": row["udp"], "bw": row.get("bw"), "stab": _stab_of(pair, row["kind"], now), "stage": stage, "stage_at": stage_at, "iran": row["iran"], "last_error": row["last_error"]}
        item["cap"] = _cap_of(item, applied, now)
        item["wx"], item["wx_word"] = weather.factor_for(row["iran_id"], row["foreign_id"], row["kind"], now)          # the weather of its family on the route: 1.0 and "" when nothing is known
        out.append(item)

    return out


def _protos(items: list[dict | None]) -> list[str]:
    """tcp always; udp too when every usable strand carries UDP (a UDP flow that is shared must find its answer on the strand that took it)."""
    live = [i for i in items if i is not None and i.get("usable")]
    return ["tcp"] + (["udp"] if live and all(i["udp"] for i in live) else [])


def _plan_for(p: dict, items: list[dict | None], weights: list[float]) -> str:
    """The plan for the weights: the strands 1 and 2 are the rules (in that order), the tunnel gets what they do not take."""
    rules = [(i, weights[k + 1]) for k, i in enumerate(items[1:])]
    probs = chain_probs([w if i is not None else 0.0 for i, w in rules])
    live = [(i, pr) for (i, _), pr in zip(rules, probs) if i is not None and pr > 0]

    if not live:
        return ""

    return plan_text(p["ports"], [i["ports"] for i, _ in live], [pr for _, pr in live], _protos(list(items)))


def _note(name: str, text: str) -> None:
    _NOTE[name] = text


def _apply(name: str, v: dict, items: list[dict], weights: list[float], now: int, why: str) -> dict | None:
    """Put the plan (or park the trident when no strand has a share) and keep what stands. RuntimeError when the server did not take it; what stood before stays then. None: there is nothing to put and nothing
    was ever put (the tunnel alone carries everything: the server is not touched)."""
    p = v["p"] or v["ref"]                  # (ref: the ports and the Iran server remembered while the tunnel's row is missing, see `_gone_share`)
    iran = _node(*p["iran"])
    plan = _plan_for(p, items, weights)
    prev = _applied(name)

    if not plan and not prev.get("at"):
        return None

    if plan:
        n = _put(iran, name, plan)
        state = "active"
    else:
        _off(iran, name, 1)
        n, state = 0, "idle"

    rec = _merge_applied(name, {"w": [round(x, 4) for x in weights], "usable": [bool(i and i["usable"]) for i in items], "stages": [(i or {}).get("stage", 0) for i in items],
                                "stage_at": [(i or {}).get("stage_at", now) for i in items], "plan": plan, "at": now, "iran_id": p["iran_id"], "ports": list(p["ports"]), "state": state, "rules": n,
                                "why": why[:160], "parked": not plan, "off": False})
    _touch(p["iran_id"])

    if plan:
        tunnels.HANDED.add(name)            # (a request from another Iran server to the users' port may be answered by a strand: the tunnel is judged by what IT carries)
    else:
        tunnels.HANDED.discard(name)

    return rec


def _words(items: list[dict], weights: list[float]) -> str:
    return ", ".join(f"{(i['kind'] if i else '-')} {round(100 * w)} %" for i, w in zip(items, weights) if i is not None)


def _step(name: str) -> None:
    now = int(time.time())
    mk = marks().get(name)

    if mk is None:
        return

    v = _read(name)
    p = v["p"]

    if p is None:
        since = _MISSING.setdefault(name, now)

        if now - since > MISSING_GRACE:
            _MISSING.pop(name, None)
            forget(name)
        elif actions.doctor_mode() != "off" and not get_settings().read_only:
            try:
                _gone_share(name, v, now)
            except (SSHError, ValueError, RuntimeError) as e:
                _note(name, f"the Trident could not move the share of the tunnel being rebuilt: {str(e)[:100]}")

        _ALIVE[name] = time.time()
        return

    _MISSING.pop(name, None)
    auto = actions.doctor_mode() != "off" and not get_settings().read_only

    if not auto:
        _note(name, "the Doctor is off (or the panel is read-only): the Trident does nothing by itself")
        _ALIVE[name] = time.time()
        return

    s1, s2 = v["s1"], v["s2"]
    iran_off = bool(p.get("off"))

    if s1 is None:
        if name not in standby._marks():
            standby._mark(name, True)            # (the standby is strand 1: it is built by the standby job)
            standby.wake()

        _note(name, "waiting for the standby (strand 1) to be built")

    if s1 is not None and s2 is None and name not in _BUILDS:
        f = _failed(name)
        wait_until = int(f.get("at") or 0) + standby.retry_wait(int(f.get("n") or 0)) if f.get("n") else 0

        if now >= wait_until and time.time() >= _RETRY_AT.get(name, 0.0) and s1["state"] == "up" and p["state"] == "up" and not iran_off:
            try:
                build_strand(name)
            except ValueError as e:
                _note(name, str(e))
                _RETRY_AT[name] = time.time() + BUILD_RETRY          # (the reason is looked at again in half a minute, not at every turn)

    if s2 is not None and not s2["stale"] and time.time() - _CHECKED.get(s2["id"], 0) >= CHECK_EVERY:
        _CHECKED[s2["id"]] = time.time()
        _check_async(s2["id"])                 # (the look takes seconds: its answer is read at the next turn)

    applied = _applied(name)

    if applied.get("state") == "active":
        tunnels.HANDED.add(name)                 # (a give-back of the standby takes it off the set; while a plan stands the tunnel is judged by what IT carries)

    items = _items(name, v, applied, now)

    for it in items:                           # the capacity of a strand nobody measured yet: one light test, in a thread, only while everything is calm
        if it is not None and it["idx"] > 0 and it["usable"] and it["cap"] is None and p["state"] == "up" and not autopilot.storm_now() and name not in autopilot.repairing_names():
            c = (applied.get("caps") or {}).get(str(it["idx"])) or {}

            if now - int(c.get("fail_at") or 0) >= CAP_RETRY:
                _measure_async(name, it)

    for it in items:                           # the way in: a step up after RAMP_STEP, back to 0 when the strand is not usable
        if it is None:
            continue

        new = advance(it["stage"], it["usable"], it["up_for"], now - it["stage_at"])

        if new != it["stage"]:
            it["stage"], it["stage_at"] = new, now
        elif not it["usable"]:
            it["stage_at"] = now

    weights = target_weights([({"usable": False} if it is None else it) for it in items])
    prev_w = applied.get("w")
    prev_usable = applied.get("usable")
    prev_stages = applied.get("stages")
    usable_now = [bool(i and i["usable"]) for i in items]
    stages_now = [(i or {}).get("stage", 0) for i in items]
    forced = name in _FORCE
    urgent = prev_usable != usable_now or prev_stages != stages_now or forced

    if prev_w is not None and len(prev_w) != len(weights):
        prev_w = None

    sh = _shadowed_note(v)

    if sh:
        _note(name, sh)
        _mend_strand(name, p, s2, now)
        _ALIVE[name] = time.time()
        return

    if should_apply(prev_w, weights, now, int(applied.get("at") or 0), urgent):
        try:
            rec = _apply(name, v, items, weights, now, _words(items, weights))
        except (SSHError, ValueError, RuntimeError) as e:
            _note(name, f"the Trident could not be put: {str(e)[:120]}")
            log.warning("trident: %s could not be put: %s", name, str(e)[:160])
            _ALIVE[name] = time.time()
            return

        _FORCE.discard(name)

        if rec is not None:
            _told(name, items, prev_usable, usable_now, weights)
            _NOTE.pop(name, None)
            log.info("trident: %s -> %s (%s)", name, rec["state"], rec["why"])
    elif not (_NOTE.get(name) or "").startswith(("the Trident could not", "waiting for", "the extra strand", "a handover")) and applied.get("at"):
        _NOTE.pop(name, None)

    _mend_strand(name, p, s2, now)          # (last: a strand that is to be removed has had its share taken away by the plan above)
    _renew_if_due(name, v, applied, now)
    _ALIVE[name] = time.time()


def renew_due(fam: str, states: dict[str, dict], used: set[str], renewed_at: int, now: int) -> str:
    """Pure: the open family an extra strand of family `fam` should be built again from ('' = leave it): its family has been under pressure or blocked on the route for RENEW_AFTER, another family that no other strand
    uses is open, and the last renewal is older than RENEW_EVERY. `states`: the weather of the route {family: {"state", "since"}}."""
    e = states.get(fam) or {}

    if e.get("state") not in ("pressure", "blocked") or now - int(e.get("since") or now) < RENEW_AFTER or now - int(renewed_at or 0) < RENEW_EVERY:
        return ""

    open_fams = sorted(f for f, x in states.items() if x.get("state") == "open" and f not in used and f != fam)
    return open_fams[0] if open_fams else ""


def _renew_if_due(name: str, v: dict, applied: dict, now: int) -> None:
    """The extra strand is built again as a kind of an open family when its own family has been bad on the route for hours (`renew_due`); the normal build path picks the kind (pick_kind reads the weather)."""
    p, s1, s2 = v["p"], v["s1"], v["s2"]

    if s2 is None or name in _REMOVING or s2["state"] not in ("up", "degraded") or p is None:
        return

    states = weather.weather_of(p["iran_id"], p["foreign_id"])
    fam = weather.family_of_kind(s2["kind"])
    used = {weather.family_of_kind(p["kind"])} | ({weather.family_of_kind(s1["kind"])} if s1 else set())
    to = renew_due(fam, states, used, int(applied.get("renewed_at") or 0), now)

    if not to:
        return

    since = int((states.get(fam) or {}).get("since") or now)
    _merge_applied(name, {"renewed_at": now})
    _remove_async(name, f"its family {fam} has been {states[fam]['state']} on this route for {max(1, (now - since) // 3600)} h while {to} is open: built again as another kind")


def _gone_share(name: str, v: dict, now: int) -> None:
    """The row of the tunnel is missing - the Doctor is rebuilding it (the old one is removed, the new one is not recorded yet; about a minute or two): the share the tunnel had goes to the strands at once, so
    that a rebuild of a tunnel that was still up cuts nothing. Needs what the last plan remembered (the users' ports) and a strand that is usable."""
    a = _applied(name)
    rows = [r for r in (v["s1"], v["s2"]) if r is not None]
    w = list(a.get("w") or [])

    if not a.get("at") or not a.get("ports") or not rows or not w or float(w[0]) <= 0 or a.get("state") != "active":
        return

    ref = {"ports": [int(x) for x in a["ports"]], "iran": rows[0]["iran"], "iran_id": rows[0]["iran_id"], "foreign_id": rows[0]["foreign_id"]}
    vv = {**v, "ref": ref}
    items = _items(name, vv, a, now)

    if not any(it is not None and it["usable"] for it in items):
        return

    for it in items:
        if it is not None and it["idx"] > 0 and not it["usable"]:
            it["stage"], it["stage_at"] = 0, now

    weights = target_weights([({"usable": False} if it is None else it) for it in items])

    if should_apply(a.get("w"), weights, now, int(a.get("at") or 0), True):
        rec = _apply(name, vv, items, weights, now, "the tunnel is being rebuilt: " + _words(items, weights))

        if rec is not None:
            log.info("trident: %s is being rebuilt: its share went to the strands (%s)", name, rec["why"])


def _check_async(sid: int) -> None:
    with _LOCK:
        if sid in _CHECKING:
            return

        _CHECKING.add(sid)

    def work():
        try:
            tunnels.check_tunnel(sid)
        except Exception:
            log.exception("trident: the look at a strand crashed")
        finally:
            with _LOCK:
                _CHECKING.discard(sid)

    threading.Thread(target=work, name=f"trident-check-{sid}", daemon=True).start()


def _shadowed_note(v: dict) -> str:
    sb = v.get("sb") or {}

    if (sb.get("use") or None):
        return "a handover of the standby stands: the Trident waits until the users' ports go back to the tunnel"

    return ""


def _told(name: str, items: list[dict | None], prev_usable, usable_now, weights) -> None:
    """One sentence in the events when a strand fell or came back."""
    if prev_usable is None:
        return

    try:
        with session() as db:
            for idx, (was, now_) in enumerate(zip(prev_usable, usable_now)):
                it = items[idx] if idx < len(items) else None

                if it is None or was == now_:
                    continue

                if was and not now_:
                    emit(db, "warn", "tunnels", f"Trident of {name}: {it['kind']} ({it['name']}) fell - its share went to the other strands.", name)
                elif now_ and not was:
                    emit(db, "ok", "tunnels", f"Trident of {name}: {it['kind']} ({it['name']}) is back - it is let in by steps ({round(100 * weights[idx])} % now).", name)
    except Exception:
        log.exception("trident: event")


def _remove_async(name: str, why: str) -> None:
    with _LOCK:
        if name in _REMOVING:
            return

        _REMOVING.add(name)

    def work():
        try:
            log.info("trident: the strand of %s is removed (%s)", name, why)
            remove_strand(name)
        except (ValueError, RuntimeError, SSHError) as e:
            log.warning("trident: the strand of %s could not be removed yet: %s", name, str(e)[:120])
        except Exception:
            log.exception("trident: removing the strand of %s crashed", name)
        finally:
            with _LOCK:
                _REMOVING.discard(name)

    threading.Thread(target=work, name=f"trident-remove-{name}", daemon=True).start()


def _mend_strand(name: str, p: dict, s2: dict | None, now: int) -> None:
    """The extra strand is not up: its units are restarted after UNHEALTHY_AFTER, it is built again after REBUILD_AFTER - quietly, only while the tunnel is up and nothing repairs it (as the standby)."""
    if s2 is None or name in _REMOVING:
        return

    if s2["stale"] and p["state"] == "up":
        _remove_async(name, s2["stale"])
        return

    if s2["state"] in ("up", "degraded"):
        return

    healthy = p["state"] == "up" and name not in autopilot.repairing_names() and not autopilot.storm_now()
    sick = now - int(s2["state_since"] or now)

    if not healthy:
        return

    if sick >= REBUILD_AFTER:
        _remove_async(name, f"it has not been up for {sick} s: it is built again")
    elif sick >= UNHEALTHY_AFTER and time.time() - _RESTARTED.get(s2["id"], 0) >= RESTART_EVERY:
        _RESTARTED[s2["id"]] = time.time()
        threading.Thread(target=_restart_strand, args=({"id": s2["id"], "name": s2["name"]},), name=f"trident-restart-{s2['name']}", daemon=True).start()


# ------------------------------------------------------------------ the owner's switch, the way out
def switch(tunnel_id: int, on: bool, manual: bool = True) -> dict:
    """Mark a tunnel for a Trident (its standby and its extra strand are built, the plan is put when they are up) or take the mark away (the trident is parked: every new connection goes to the tunnel)."""
    if get_settings().read_only:
        raise ValueError("the panel is read-only: nothing is built or changed")

    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        if is_temp(t.name) or (t.record or {}).get("standby_of") or (t.record or {}).get("strand_of"):
            raise ValueError("a temporary or a hidden tunnel has no Trident")

        name, ports = t.name, t.forwarded_ports or []

    if on:
        if not ports:
            raise ValueError("this tunnel forwards no port, so there is nothing to share")

        v = standby._view(name)
        sb = v["s"]

        if sb is not None and sb["use"]:
            raise ValueError("the users' ports are on the standby now (a handover): turn the Trident on after they went back")

        st_lane = lanes.status(name)

        if st_lane and st_lane.get("state") in ("sharing", "paused"):
            try:
                lanes.stop(name, "the Trident takes over")
            except (ValueError, RuntimeError, SSHError) as e:
                raise ValueError(f"the lane of this tunnel could not be stopped first: {str(e)[:100]}")

        with _LOCK:
            d = _doc()
            cur = dict(d.get("tunnels") or {})
            cur[name] = {"on": True, "since": int(time.time()), "manual": bool(manual)}
            st.put("trident", {"tunnels": cur})

        if name not in standby._marks():
            standby._mark(name, True)

        _NOTE[name] = "the Trident starts when the standby and the extra strand are up"
        wake()
        standby.wake()
    else:
        with _LOCK:
            cur = dict(_doc().get("tunnels") or {})
            cur.pop(name, None)
            st.put("trident", {"tunnels": cur})

        threading.Thread(target=_safe_release, args=(name,), name=f"trident-off-{name}", daemon=True).start()

    return status(name)


def release(name: str, hard: bool = False) -> None:
    """The trident ends: the chain is emptied (PARK=1: every new connection goes to the tunnel, the jumps stay so the open connections keep their translation); later the job takes the parked rules off when no
    connection is left on the shifted ports (PARK=2). `hard`: everything at once (the tunnel is removed)."""
    a = _applied(name)

    if not a.get("at"):
        tunnels.HANDED.discard(name)
        return

    with session() as db:
        t = db.scalars(select(Tunnel).where(Tunnel.name == name)).first()
        node = _node(t.iran.host, t.iran.ssh_user, t.iran.ssh_port) if t is not None else None

    if node is None:
        with session() as db:
            srv = db.get(Server, int(a.get("iran_id") or 0))
            node = _node(srv.host, srv.ssh_user, srv.ssh_port) if srv is not None else None

    if node is not None:
        _off(node, name, 0 if hard else 1)

    tunnels.HANDED.discard(name)

    if hard:
        _set_applied(name, None)
    else:
        _set_applied(name, {**a, "state": "parked", "parked_at": int(time.time()), "plan": "", "w": None, "parked": True, "off": True})


def _safe_release(name: str) -> None:
    try:
        release(name)
        remove_strand(name)
        _NOTE.pop(name, None)
    except (ValueError, RuntimeError, SSHError) as e:
        log.warning("trident: %s could not be switched off yet: %s", name, str(e)[:160])
    except Exception:
        log.exception("trident: switching %s off crashed", name)


def forget(name: str) -> None:
    """The owner removed the tunnel (or its row has been gone for hours): the mark, the extra strand and the rules go (in the background)."""
    with _LOCK:
        cur = dict(_doc().get("tunnels") or {})
        was = name in cur
        cur.pop(name, None)

        if was:
            st.put("trident", {"tunnels": cur})

    if was or _applied(name) or strand_exists(name):
        threading.Thread(target=_forget_work, args=(name,), name=f"trident-forget-{name}", daemon=True).start()


def strand_exists(name: str) -> bool:
    with session() as db:
        return strand_row(db, name) is not None


def _forget_work(name: str) -> None:
    try:
        if name in _BUILDS:
            builder.CANCEL.add(_BUILDS[name]["aid"])

        try:
            release(name, hard=True)
        except (ValueError, RuntimeError, SSHError) as e:
            log.warning("trident: the rules of %s could not be taken off now: %s", name, str(e)[:120])

        remove_strand(name)
        _set_failed(name, None)
        _NOTE.pop(name, None)
    except Exception:
        log.exception("trident: forgetting %s crashed", name)


def renamed(old: str, new: str) -> None:
    """A tunnel was renamed: its mark, record and failures follow the new name; the rules on the Iran server carry the OLD name, so they are parked and taken off by the job, and the new name starts its own chain."""
    with _LOCK:
        d = _doc()
        changes = {}

        for key in ("tunnels", "applied", "failed"):
            cur = dict(d.get(key) or {})

            if old in cur:
                cur[new] = cur.pop(old)
                changes[key] = cur

        if changes:
            st.put("trident", changes)

    if old in _BUILDS:
        _BUILDS[new] = _BUILDS.pop(old)

    a = (changes.get("applied") or {}).get(new)

    if a and a.get("at"):
        sid = int(a.get("iran_id") or 0)
        _RETIRED[old] = (sid,)
        _set_applied(new, {**a, "at": 0, "w": None, "plan": "", "state": "waiting"})

        def park_old():
            with session() as db:
                srv = db.get(Server, sid)
                node = _node(srv.host, srv.ssh_user, srv.ssh_port) if srv is not None and srv.enabled and srv.online is not False else None

            if node is not None:
                try:
                    _off(node, old, 1)               # (at once: the new name puts its own chain, and the old chain must not share the share again)
                except (SSHError, ValueError, RuntimeError) as e:
                    log.warning("trident: the old rules of %s could not be parked yet: %s", old, str(e)[:120])

        threading.Thread(target=park_old, name=f"trident-retire-{old}", daemon=True).start()

    # (tunnels.HANDED follows the new name in standby.renamed, which calls this)
    with session() as db:
        for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(False))):
            if (t.record or {}).get("strand_of") == old:
                t.record = {**(t.record or {}), "strand_of": new}


# ------------------------------------------------------------------ what the tunnel window shows
def _state_of(name: str, marked: bool, v: dict | None, now: int) -> tuple[str, str]:
    a = _applied(name)

    if not marked:
        if a.get("parked") and a.get("at"):
            return "winding_down", "switched off: all new connections go to the tunnel, the rest is taken off when the open ones end"

        return "off", ""

    if name in _BUILDS:
        return "building", f"the extra strand ({_BUILDS[name]['kind']}) is being built"

    if a.get("state") == "active":
        return "active", ""

    if a.get("state") == "idle":
        return "idle", _NOTE.get(name) or "no strand is usable: everything goes to the tunnel"

    f = _failed(name)

    if f.get("n") and now < int(f.get("at") or 0) + standby.retry_wait(int(f["n"])):
        return "failed", f"the last strand build failed ({f.get('why', '')}); the next try is in {int(f['at']) + standby.retry_wait(int(f['n'])) - now} s"

    return "waiting", _NOTE.get(name) or "the Trident starts when the standby and the extra strand are up"


def status(name: str) -> dict:
    """What the tunnel window shows: on, state (off · waiting · building · active · idle · failed · winding_down), why, and one row per strand {role, kind, name, state, weight, step, capacity, delay, loss}."""
    now = int(time.time())
    marked = name in marks()
    a = _applied(name)
    state, why = _state_of(name, marked, None, now)
    strands = []
    v: dict = {}

    if marked or a.get("at"):
        try:
            v = _read(name)
            items = _items(name, v, a, now) if v["p"] is not None else []
        except Exception:
            log.exception("trident: the strands of %s could not be read", name)
            items = []

        w = list(a.get("w") or [])

        for it in items:
            if it is None:
                continue

            strands.append({"idx": it["idx"], "role": ("tunnel", "standby", "strand")[it["idx"]], "kind": it["kind"], "name": it["name"], "state": it["state"], "usable": it["usable"],
                            "weight": round(100 * float(w[it["idx"]])) if it["idx"] < len(w) else None, "step": it["stage"], "capacity": it["cap"], "delay": it["rtt"], "loss": it["loss"], "weather": it.get("wx_word", "")})

        if state == "active" and not why and v.get("p"):
            why = weather.words(v["p"]["iran_id"], v["p"]["foreign_id"], now)          # (the only place the weather shows: a few words under the bar, e.g. "udp blocked on this route for 2 h")

    return {"on": marked, "state": state, "why": why, "since": int((marks().get(name) or {}).get("since") or 0), "strands": strands, "applied_at": int(a.get("at") or 0)}


def summaries(names: list[str]) -> dict[str, dict]:
    """{tunnel name: what the list and the window show of its Trident}: on, state, why - and, for a tunnel that has a Trident, the share of each strand (`weights`) and the strands themselves (`strands`)."""
    marks_ = marks()
    out = {}

    for n in names:
        state, why = _state_of(n, n in marks_, None, int(time.time()))
        out[n] = {"on": n in marks_, "state": state, "why": why}

        if n in marks_:
            a = _applied(n)

            if a.get("w"):
                out[n]["weights"] = [round(100 * float(x)) for x in a["w"]]

            full = status(n)
            out[n]["strands"] = full["strands"]
            out[n]["why"] = out[n]["why"] or full["why"]

    return out


def rebalance(name: str) -> dict:
    """Work the weights out again now and put them (the owner's button): ignores the wait between small drifts."""
    if name not in marks():
        raise ValueError("this tunnel has no Trident")

    _FORCE.add(name)                # (the next turn of the job puts the plan whatever the weights did)
    wake()
    return status(name)


def wake() -> None:
    try:
        jobs.trigger("trident")
    except KeyError:
        pass                      # not registered (tests)


# ------------------------------------------------------------------ the job
def _init_once() -> None:
    """After a start of the panel: the tunnels whose ports are shared by a trident are known again (they are judged by what they carry)."""
    if _STATE["init"]:
        return

    for n, a in ((_doc().get("applied") or {}).items()):
        if a.get("state") == "active" and n in marks():
            tunnels.HANDED.add(n)

    _STATE["init"] = True


def _safe_step(name: str) -> None:
    try:
        _step(name)
    except (SSHError, ValueError, RuntimeError) as e:
        _note(name, f"the Trident stopped: {str(e)[:120]}")
        log.warning("trident: %s: %s", name, str(e)[:160])
    except Exception:
        log.exception("trident: %s crashed", name)


def reconcile() -> None:
    """Every two minutes: what stands on the Iran servers against what the panel believes. A trident nobody asked for is parked; one whose rules are gone (a flushed firewall, a reboot) is put again at the next turn."""
    d = _doc()
    ids = [int(x) for x in d.get("touched") or []]

    if not ids:
        return

    with session() as db:
        servers ={s.id: _node(s.host, s.ssh_user, s.ssh_port) for s in db.scalars(select(Server).where(Server.id.in_(ids), Server.enabled.is_(True), Server.online.is_(True)))}

    mk = marks()
    ap = dict(d.get("applied") or {})

    for sid, node in servers.items():
        try:
            have = parse_list(run_script(node, "trident_list", timeout=20))
        except (SSHError, ValueError) as e:
            log.info("trident: the rules of a server could not be read: %s", str(e)[:100])
            continue

        for n, h in have.items():
            if n not in mk and h["rules"]:
                log.warning("trident: rules of %s stand on a server and nobody asked for them: parked", n)

                try:
                    _off(node, n, 1)
                except (SSHError, ValueError, RuntimeError) as e:
                    log.warning("trident: could not park them: %s", str(e)[:120])

        for n, a in ap.items():
            if n in mk and int(a.get("iran_id") or 0) == sid and a.get("plan") and not same_plan((have.get(n) or {}).get("rules", []), a["plan"]):
                log.warning("trident: the rules of %s are not what the panel put: they are put again", n)
                _FORCE.add(n)
                wake()


def _clean_parked() -> None:
    """The parked rules of a trident that was switched off (or renamed) go when no connection is left on the shifted ports (PARK=2)."""
    d = _doc()
    mk = marks()

    for n, a in list((d.get("applied") or {}).items()):
        if n in mk or not a.get("parked") or not a.get("off"):
            continue

        with session() as db:
            srv = db.get(Server, int(a.get("iran_id") or 0))
            node = _node(srv.host, srv.ssh_user, srv.ssh_port) if srv is not None and srv.enabled and srv.online is not False else None

        if node is None:
            continue

        try:
            left = _off(node, n, 2)
        except (SSHError, ValueError, RuntimeError) as e:
            log.info("trident: parked rules of %s: %s", n, str(e)[:100])
            continue

        if left[1] == 0:
            _set_applied(n, None)

    for old, (sid,) in list(_RETIRED.items()):
        with session() as db:
            srv = db.get(Server, sid)
            node = _node(srv.host, srv.ssh_user, srv.ssh_port) if srv is not None and srv.enabled and srv.online is not False else None

        if node is None:
            continue

        try:
            _off(node, old, 1)
            left = _off(node, old, 2)
        except (SSHError, ValueError, RuntimeError):
            continue

        if left[1] == 0:
            _RETIRED.pop(old, None)


def run() -> None:
    """The job (every 5 s): the marked tunnels, one turn each - the builds that ended, the look at the extra strand, the weights, the plan; every two minutes the rules on the servers are compared with the record."""
    if jobs.stopping():
        return

    _init_once()
    _collect()
    names = sorted(marks())

    if names:
        with ThreadPoolExecutor(min(4, len(names))) as ex:
            list(ex.map(_safe_step, names))

    if time.time() - _STATE["reconciled"] >= RECONCILE_EVERY:
        _STATE["reconciled"] = time.time()

        try:
            reconcile()
        except Exception:
            log.exception("trident: reconcile")

    if time.time() - _STATE["cleaned"] >= PARK_CLEAN_EVERY:
        _STATE["cleaned"] = time.time()

        try:
            _clean_parked()
        except Exception:
            log.exception("trident: cleaning the parked rules")
