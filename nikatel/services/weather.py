"""THE WEATHER OF A ROUTE: what each FAMILY of carriers (gre · udp · tcp · icmp · esp · ipip) does between an Iran server and a foreign server RIGHT NOW - open, under pressure or blocked, since when - fused from what the
panel already sees, kept for the Trident (the owner, 2026-10-24: "I want the tunnel to be strong against the day's data. No radar to look at - its data must help the Trident tunnel. Take help from any source needed").
There is no page: the weather shows only as a few words in the Trident's bar and in the reasons of the Doctor.

  the sources (every minute; read-only on the servers but for the probes)
    the fleet   every real tunnel and every hidden strand of the panel is a sensor. On one ROUTE (the same two servers) a tunnel of a family that is DOWN with a cut-like cause while a tunnel of another family on the same
                route is up = that family is BLOCKED on the route. On one IRAN SERVER (whatever the foreign server) the same, weaker: the kind_wide rule of the Doctor (>= 2 tunnels and >= 60 % of the family down while
                another family is fine) = blocked on every route of that server that has no evidence of its own; one tunnel down = pressure. A server that is off is no evidence of anything.
    the probes  the link probe (services/linkprobe.py: gre, fou = udp, ipip, ipsec = esp, asked in seconds, nothing stays on the servers) and the reach probe (ssh.py `reach_probe`: five pings and three TCP
                handshakes from the Iran server to the foreign server: icmp and tcp) - asked every PROBE_EVERY for every pair that carries a Trident, one pair at a time, only while both servers are calm and no repair runs.
    the builds  a kind that failed on the pair because its carrier is filtered (knowledge.blocked, the error says filtered / cannot pass / no data passes): pressure on its family, blocked when two kinds of the family failed.
    open        a tunnel of the family up and carrying on the route, a probe that passed - and OPEN evidence newer than every failure wins (what carries now beats what failed an hour ago).
  the pattern   per route and family, the minutes blocked and the minutes seen in each hour of the day (Iran time) over the last 7 days: "usually blocked at this hour" = blocked in half of the minutes seen, an hour seen at least.
  the consumers trident.target_weights (a strand of a family under pressure counts half, blocked a tenth, usually-blocked-within-30-minutes 0.7 - before anything falls), trident.pick_kind (blocked families last),
                the renewal of a strand whose family has been blocked for hours, knowledge.best_kinds and standby.choose_kind (blocked families last). Without data every consumer does what it did before.

The pure helpers (`family_of_link`, `fuse`, `factor`, `usual_blocked`, `pattern_add`, `fleet_evidence`, `probe_due`) carry the decisions and are tested."""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel, is_temp
from ..ssh import Node, SSHError, run_script
from . import autopilot, knowledge, linkprobe, tunnels
from . import settings as st

log = get_logger("weather")
FAMILIES = ("gre", "udp", "tcp", "icmp", "esp", "ipip")
LINK_FAMILY = {"gre": "gre", "fou": "udp", "gue": "udp", "ipipfou": "udp", "ipipgue": "udp", "ipsecnat": "udp", "wg": "udp", "ipip": "ipip", "ipsec": "esp", "wgchisel": "tcp", "wgchiselfwd": "tcp"}
PROBE_LINKS = ["gre", "fou", "ipip", "ipsec"]          # one link per family the link probe can ask (udp = GRE in a UDP port; the carried links need Chisel and a minute - not asked)
FRESH = 3600                   # evidence older than this says nothing about now ...
BLOCK_FRESH = 2 * 3600         # ... but a block seen this recently still counts (a filter does not lift in an hour)
PROBE_EVERY = 600              # a pair with a Trident is probed this often ...
PROBE_CPU = 60.0               # ... only while both servers are under this CPU
PROBE_MIN_GAP = 60             # ... and one pair at a time, this long apart
USUAL_SHARE = 0.5              # "usually blocked at this hour": blocked in this share of the minutes seen in that hour ...
USUAL_SEEN = 60                # ... with at least this many minutes seen
AHEAD = 1800                   # the proactive look: 30 minutes ahead
PATTERN_DAYS = 7
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))         # the hours of the pattern are Iran's: the users and the filters live there, the servers' clocks are UTC
FACTOR = {"open": 1.0, "pressure": 0.5, "blocked": 0.1, "usual": 0.7}
CUT_RE = re.compile(r"filtered|cannot pass|no data passes|does not pass|no answer|silent|inactive|missing|not come up|cut", re.IGNORECASE)
_LOCK = threading.Lock()
_PROBING: set[str] = set()
_STATE = {"last_probe": 0.0}


# ------------------------------------------------------------------ pure helpers (tested)
def route_key(iran_id: int, foreign_id: int) -> str:
    return f"{int(iran_id)}:{int(foreign_id)}"


def family_of_kind(kind: str) -> str:
    from . import trident

    return trident.traits(kind)["family"]


def family_of_link(link: str) -> str:
    return LINK_FAMILY.get(link, "udp")


def fuse(evidence: list[dict], prev: dict | None, now: int) -> dict | None:
    """The state of one family on one route from its evidence [{"at", "verdict": open | pressure | blocked, "why"}]: OPEN evidence newer than every failure wins; else a block seen within BLOCK_FRESH; else pressure within
    FRESH; else nothing is known (None). `since` is kept from `prev` while the state stays the same."""
    ev = [e for e in evidence if e.get("verdict") in ("open", "pressure", "blocked") and now - int(e.get("at") or 0) <= BLOCK_FRESH]

    if not ev:
        return None

    opens = [e for e in ev if e["verdict"] == "open" and now - int(e["at"]) <= FRESH]
    fails = [e for e in ev if e["verdict"] != "open"]
    newest_open = max((int(e["at"]) for e in opens), default=-1)
    newest_fail = max((int(e["at"]) for e in fails), default=-1)

    if opens and newest_open >= newest_fail:
        state, why = "open", next(e["why"] for e in opens if int(e["at"]) == newest_open)
    elif any(e["verdict"] == "blocked" for e in fails):
        b = max((e for e in fails if e["verdict"] == "blocked"), key=lambda e: int(e["at"]))
        state, why = "blocked", b["why"]
    elif any(e["verdict"] == "pressure" and now - int(e["at"]) <= FRESH for e in fails):
        p = max((e for e in fails if e["verdict"] == "pressure"), key=lambda e: int(e["at"]))
        state, why = "pressure", p["why"]
    elif opens:
        state, why = "open", next(e["why"] for e in opens if int(e["at"]) == newest_open)
    else:
        return None

    since = int(prev["since"]) if prev and prev.get("state") == state and prev.get("since") else now
    return {"state": state, "since": since, "at": now, "why": str(why)[:160], "last_ok": newest_open if newest_open >= 0 else int((prev or {}).get("last_ok") or 0),
            "last_fail": newest_fail if newest_fail >= 0 else int((prev or {}).get("last_fail") or 0)}


def usual_blocked(hours: dict | None, hour: int) -> bool:
    """Pure: is this family usually blocked at this hour of the day - blocked in USUAL_SHARE of the minutes seen in that hour over the kept days, USUAL_SEEN minutes seen at least. `hours` = {date: {"b": [24], "n": [24]}}."""
    b = sum(int((d.get("b") or [0] * 24)[hour]) for d in (hours or {}).values())
    n = sum(int((d.get("n") or [0] * 24)[hour]) for d in (hours or {}).values())
    return n >= USUAL_SEEN and b >= USUAL_SHARE * n


def factor(entry: dict | None, hours: dict | None, now: int) -> tuple[float, str]:
    """Pure: (the weight factor of a strand of this family on this route, the word) - open 1.0, pressure 0.5, blocked 0.1; open but usually blocked within AHEAD seconds: 0.7 ("usual"); nothing known: 1.0 ("")."""
    state = (entry or {}).get("state") or ""

    if state in ("pressure", "blocked"):
        return FACTOR[state], state

    ahead = datetime.fromtimestamp(now + AHEAD, IRAN_TZ).hour

    if usual_blocked(hours, ahead) or usual_blocked(hours, datetime.fromtimestamp(now, IRAN_TZ).hour):
        return FACTOR["usual"], "usual"

    return 1.0, state


def pattern_add(hours: dict | None, now: int, blocked: bool, minutes: int = 1) -> dict:
    """Pure: the pattern of one family on one route after one more look of `minutes`: the minutes seen (and blocked) of this hour of today (Iran time); days older than PATTERN_DAYS are dropped."""
    out = {k: {"b": list(v.get("b") or [0] * 24), "n": list(v.get("n") or [0] * 24)} for k, v in (hours or {}).items()}
    local = datetime.fromtimestamp(now, IRAN_TZ)
    day = local.strftime("%Y-%m-%d")
    today = out.setdefault(day, {"b": [0] * 24, "n": [0] * 24})
    today["n"][local.hour] += minutes

    if blocked:
        today["b"][local.hour] += minutes

    keep = sorted(out)[-PATTERN_DAYS:]
    return {k: out[k] for k in keep}


def fleet_evidence(rows: list[dict], now: int) -> dict[str, dict[str, list[dict]]]:
    """Pure: {route: {family: [evidence]}} from the tunnels of the panel. `rows`: [{"name", "iran_id", "foreign_id", "family", "state", "since", "off", "error", "hidden"}] - every real tunnel and every hidden strand,
    not the temporary ones. On a route: a tunnel of a family down with a cut-like cause while another family is up there = blocked (its own tunnel up = open). On an Iran server: >= 2 and >= 60 % of the tunnels of a
    family down while another family is fine = blocked on the server's other routes (as the Doctor's kind_wide rule), one down = pressure there."""
    out: dict[str, dict[str, list[dict]]] = {}
    by_route: dict[str, list[dict]] = {}
    by_iran: dict[int, list[dict]] = {}

    for r in rows:
        if r.get("off"):
            continue                        # a server that is off says nothing about the route

        by_route.setdefault(route_key(r["iran_id"], r["foreign_id"]), []).append(r)
        by_iran.setdefault(int(r["iran_id"]), []).append(r)

    def down(r: dict) -> bool:
        return r.get("state") == "down" and bool(CUT_RE.search(str(r.get("error") or "")))

    def up(r: dict) -> bool:
        return r.get("state") in ("up", "degraded")

    for key, rs in by_route.items():
        fams = {r["family"] for r in rs}
        ups = {r["family"] for r in rs if up(r)}

        for fam in fams:
            mine = [r for r in rs if r["family"] == fam]
            lst = out.setdefault(key, {}).setdefault(fam, [])

            for r in mine:
                if up(r):
                    lst.append({"at": now, "verdict": "open", "why": f"{r['name']} ({fam}) carries on this route"})          # (a tunnel that is up carries NOW: its evidence is of this minute, not of the hour it came up)

            dead = [r for r in mine if down(r)]

            if dead and (ups - {fam}):
                other = next(iter(sorted(ups - {fam})))
                d = max(dead, key=lambda r: int(r.get("since") or 0))
                lst.append({"at": int(d.get("since") or now), "verdict": "blocked", "why": f"{d['name']} ({fam}) is down ({str(d.get('error') or '')[:60]}) while {other} carries on this route"})

    for rs in by_iran.values():
        fams = {r["family"] for r in rs}
        ups = {r["family"] for r in rs if up(r)}

        for fam in fams:
            mine = [r for r in rs if r["family"] == fam]
            dead = [r for r in mine if down(r)]

            if not dead or not (ups - {fam}):
                continue

            strong = len(dead) >= 2 and len(dead) >= 0.6 * len(mine)
            verdict = "blocked" if strong else "pressure"
            d = max(dead, key=lambda r: int(r.get("since") or 0))
            why = f"{len(dead)} of the {len(mine)} {fam} tunnels of the Iran server are down while another family carries ({d['name']}: {str(d.get('error') or '')[:50]})"

            for key in sorted({route_key(r["iran_id"], r["foreign_id"]) for r in rs}):          # (once per route of the server)
                lst = out.setdefault(key, {}).setdefault(fam, [])

                if not any(e["verdict"] == "blocked" for e in lst):          # (the route's own evidence stands above the server's)
                    lst.append({"at": int(d.get("since") or now), "verdict": verdict, "why": why})

    return out


def link_evidence(pair: dict | None, reach: dict | None, now: int) -> dict[str, list[dict]]:
    """Pure: {family: [evidence]} from the link probe's answers kept on the pair (knowledge: pair["links"]) and the reach probe (icmp, tcp)."""
    out: dict[str, list[dict]] = {}

    for link, e in ((pair or {}).get("links") or {}).items():
        if e.get("ok") is None:
            continue

        fam = family_of_link(link)
        out.setdefault(fam, []).append({"at": int(e.get("at") or 0), "verdict": "open" if e.get("ok") else "blocked", "why": f"the {link} link {'passed' if e.get('ok') else 'did not pass'} in the probe"})

    for fam, e in (reach or {}).items():
        if e.get("ok") is None:
            continue

        out.setdefault(fam, []).append({"at": int(e.get("at") or 0), "verdict": "open" if e.get("ok") else "blocked", "why": f"{fam}: {e.get('why', '')}"})

    return out


def build_evidence(pair: dict | None, now: int) -> dict[str, list[dict]]:
    """Pure: {family: [evidence]} from the kinds that failed on the pair because their carrier does not pass (knowledge.blocked with a filtered-carrier error): pressure, blocked when two kinds of a family failed."""
    out: dict[str, list[dict]] = {}
    hits: dict[str, list[tuple[int, str]]] = {}

    for kind in knowledge.blocked(pair, now):
        e = (((pair or {}).get("kinds") or {}).get(kind) or {}).get("last") or {}
        err = str(e.get("error") or "")

        if not re.search(r"filtered|cannot pass|no data passes|does not pass|not come up", err, re.IGNORECASE):
            continue

        hits.setdefault(family_of_kind(kind), []).append((int(e.get("at") or 0), kind))

    for fam, lst in hits.items():
        at = max(a for a, _ in lst)
        kinds = ", ".join(sorted(k for _, k in lst))
        out.setdefault(fam, []).append({"at": at, "verdict": "blocked" if len(lst) >= 2 else "pressure", "why": f"{kinds} failed to build on this pair (the carrier does not pass)"})

    return out


def probe_due(routes: list[dict], probed: dict, now: float, last_probe: float, busy: set | frozenset = frozenset()) -> str:
    """Pure: the ONE route to probe now ('' = none): a pair with a Trident, not probed for PROBE_EVERY, both servers calm, no repair on its tunnel, not being probed; the oldest probe first; one every PROBE_MIN_GAP.
    `routes`: [{"key", "name", "cpu_i", "cpu_f", "repairing"}]."""
    if now - last_probe < PROBE_MIN_GAP or busy:
        return ""

    due = [r for r in routes if now - float(probed.get(r["key"]) or 0) >= PROBE_EVERY and float(r.get("cpu_i") or 0) < PROBE_CPU and float(r.get("cpu_f") or 0) < PROBE_CPU and not r.get("repairing")]
    return min(due, key=lambda r: float(probed.get(r["key"]) or 0))["key"] if due else ""


def parse_reach(out: str) -> dict:
    """Pure: the answer of the reach probe -> {"icmp": {"ok", "why"}, "tcp": {"ok", "why"}} (an answer that is not understood gives {})."""
    m = re.search(r"REACH icmp (\d+)/(\d+) tcp (\d+)/(\d+)", str(out))

    if not m:
        return {}

    pi, ni, pt, nt = (int(x) for x in m.groups())
    return {"icmp": {"ok": pi >= max(1, ni // 2), "why": f"{pi} of {ni} pings came back"}, "tcp": {"ok": pt >= 1, "why": f"{pt} of {nt} TCP handshakes to the foreign server succeeded"}}


# ------------------------------------------------------------------ what is kept
def _doc() -> dict:
    return st.get("trident")


def weather_of(iran_id: int, foreign_id: int) -> dict[str, dict]:
    """{family: entry} of a route as kept now (empty when nothing is known)."""
    return dict((_doc().get("weather") or {}).get(route_key(iran_id, foreign_id)) or {})


def pattern_of(iran_id: int, foreign_id: int, family: str) -> dict:
    return dict(((_doc().get("pattern") or {}).get(route_key(iran_id, foreign_id)) or {}).get(family) or {})


def factor_for(iran_id: int, foreign_id: int, kind: str, now: int | None = None) -> tuple[float, str]:
    """(the weight factor, the word) of a kind on a route - 1.0 and '' when nothing is known."""
    now = now or int(time.time())
    fam = family_of_kind(kind)
    return factor(weather_of(iran_id, foreign_id).get(fam), pattern_of(iran_id, foreign_id, fam), now)


def blocked_families(iran_id: int, foreign_id: int, now: int | None = None) -> set[str]:
    """The families blocked on the route now (pressure is not blocked)."""
    return {fam for fam, e in weather_of(iran_id, foreign_id).items() if e.get("state") == "blocked"}


def states_for(iran_id: int, foreign_id: int) -> dict[str, str]:
    """{family: state} of a route (only the families with evidence)."""
    return {fam: str(e.get("state") or "") for fam, e in weather_of(iran_id, foreign_id).items()}


def words(iran_id: int, foreign_id: int, now: int | None = None) -> str:
    """One short sentence for the Trident's bar: the families that are not open on the route ('' when all is well or nothing is known)."""
    now = now or int(time.time())
    bad = [(fam, e) for fam, e in sorted(weather_of(iran_id, foreign_id).items()) if e.get("state") in ("pressure", "blocked")]

    if not bad:
        return ""

    def ago(t: int) -> str:
        m = max(0, now - int(t or now)) // 60
        return f"{m} min" if m < 120 else f"{m // 60} h"

    return "; ".join(f"{fam} {e['state']} on this route for {ago(e.get('since'))}" for fam, e in bad)


# ------------------------------------------------------------------ the job
def _rows() -> list[dict]:
    out = []

    with session() as db:
        for t in db.scalars(select(Tunnel)):
            if is_temp(t.name):
                continue

            rec = t.record or {}
            kind = autopilot.kind_of(t.type, t.transport, rec)
            out.append({"name": t.name, "iran_id": t.iran_server_id, "foreign_id": t.foreign_server_id, "family": family_of_kind(kind), "kind": kind, "state": t.state, "since": int(t.state_since or 0),
                        "off": bool(tunnels.servers_off(t)), "error": t.last_error or "", "hidden": not t.enabled})

    return out


def observe(now: int | None = None) -> dict:
    """One look: the weather of every route that has a tunnel, from the fleet, the probes and the builds; the pattern of every family seen. Returns the weather document."""
    now = now or int(time.time())
    rows = _rows()
    d = _doc()
    prev = dict(d.get("weather") or {})
    pattern = dict(d.get("pattern") or {})
    reach = dict(d.get("reach") or {})
    fleet = fleet_evidence(rows, now)
    routes = sorted({route_key(r["iran_id"], r["foreign_id"]) for r in rows})
    weather: dict[str, dict] = {}

    for key in routes:
        i, f = (int(x) for x in key.split(":"))
        pair = knowledge.pair(i, f)
        ev: dict[str, list[dict]] = {}

        for src in (fleet.get(key) or {}, link_evidence(pair, reach.get(key), now), build_evidence(pair, now)):
            for fam, lst in src.items():
                ev.setdefault(fam, []).extend(lst)

        entry: dict[str, dict] = {}

        for fam in FAMILIES:
            fused = fuse(ev.get(fam) or [], (prev.get(key) or {}).get(fam), now)

            if fused is not None:
                entry[fam] = fused

            pattern.setdefault(key, {})[fam] = pattern_add((pattern.get(key) or {}).get(fam), now, bool(fused and fused["state"] == "blocked"))

        if entry:
            weather[key] = entry

    for key in list(pattern):                                           # (a route that lost its last tunnel keeps its pattern for a week, then goes)
        if key not in routes and all(not v for v in pattern[key].values()):
            pattern.pop(key, None)

    st.put("trident", {"weather": weather, "pattern": pattern})
    return weather


def _trident_routes() -> list[dict]:
    """The routes that carry a Trident: {"key", "name", "cpu_i", "cpu_f", "repairing"}."""
    from . import trident

    marks = set(trident.marks())

    if not marks:
        return []

    out = []
    repairing = set(autopilot.repairing_names())

    with session() as db:
        cpu = {s.id: float((s.facts or {}).get("cpu") or 0) for s in db.scalars(select(Server))}
        online = {s.id: bool(s.enabled) and s.online is not False for s in db.scalars(select(Server))}

        for t in db.scalars(select(Tunnel).where(Tunnel.name.in_(list(marks)))):
            if online.get(t.iran_server_id) and online.get(t.foreign_server_id):
                out.append({"key": route_key(t.iran_server_id, t.foreign_server_id), "name": t.name, "cpu_i": cpu.get(t.iran_server_id, 0.0), "cpu_f": cpu.get(t.foreign_server_id, 0.0), "repairing": t.name in repairing})

    return out


def _probe(key: str) -> None:
    """One pair: the link probe (four links, one per family) and the reach probe, in a thread; the answers go to the knowledge of the pair and to `reach`."""
    i, f = (int(x) for x in key.split(":"))

    try:
        linkprobe.probe(i, f, links=PROBE_LINKS)
    except (ValueError, RuntimeError, SSHError) as e:
        log.info("weather: the link probe of %s could not run: %s", key, str(e)[:120])
    except Exception:
        log.exception("weather: the link probe of %s crashed", key)

    try:
        with session() as db:
            si, sf = db.get(Server, i), db.get(Server, f)
            node = Node(si.host, si.ssh_user, si.ssh_port) if si is not None and sf is not None else None
            host, port = (sf.host, int(sf.ssh_port or 22)) if sf is not None else ("", 22)

        if node is not None:
            r = parse_reach(run_script(node, "reach_probe", {"HOST": host, "PORT": str(port)}, timeout=40))
            now = int(time.time())

            with _LOCK:
                reach = dict(_doc().get("reach") or {})
                reach[key] = {fam: {**v, "at": now} for fam, v in r.items()}
                st.put("trident", {"reach": reach})
    except (ValueError, RuntimeError, SSHError) as e:
        log.info("weather: the reach probe of %s could not run: %s", key, str(e)[:120])
    except Exception:
        log.exception("weather: the reach probe of %s crashed", key)
    finally:
        with _LOCK:
            _PROBING.discard(key)


def _probe_if_due() -> None:
    if get_settings().read_only:
        return

    routes = _trident_routes()

    if not routes:
        return

    probed = dict(_doc().get("probed") or {})

    with _LOCK:
        key = probe_due(routes, probed, time.time(), _STATE["last_probe"], set(_PROBING))

        if not key:
            return

        _PROBING.add(key)
        _STATE["last_probe"] = time.time()
        probed[key] = int(time.time())
        st.put("trident", {"probed": probed})

    log.info("weather: probing the route %s", key)
    threading.Thread(target=_probe, args=(key,), name=f"weather-probe-{key}", daemon=True).start()


def run() -> None:
    """The job (every 60 s): one look at the fleet, the probes and the builds; then a probe of one Trident pair when one is due."""
    if jobs.stopping():
        return

    try:
        observe()
    except Exception:
        log.exception("weather: observe")

    try:
        _probe_if_due()
    except Exception:
        log.exception("weather: probe")
