"""Where is the packet loss: on the Iran side, on the foreign side, or only on the route between this pair?

One tunnel is looked at from five sides at once (20 pings each, in parallel):
  this Iran server  → this foreign server, 1-3 OTHER foreign servers, two international anchors, 1-2 OTHER Iran servers
  this foreign server → this Iran server, the international anchors
  1-3 OTHER Iran servers → this foreign server
`locate()` is pure and turns those numbers into a verdict the diagnosis, the smart repair and the AI all use:

  iran_side     this Iran server loses toward several foreign servers (or the international anchors, or other Iran servers) - its own network or upstream
  foreign_side  other Iran servers lose toward this foreign server too (or it loses toward the anchors) - the foreign server or its provider
  pair_route    only this pair loses: the peering between those two networks
  none          nothing lost
  unknown       too little answered (ICMP filtered)"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel
from ..ssh import Node, SSHError, run_script

log = get_logger("pathscan")
ANCHORS = ("1.1.1.1", "8.8.8.8")        # public anchors that answer ping from everywhere
BAD = 10.0                              # % loss that counts as lossy here (users feel it)


def parse_loss(out: str) -> dict[str, dict]:
    """Pure: 'LOSS host sent received avg' lines -> {host: {loss, avg_ms}} (loss None when nothing was sent)."""
    res = {}

    for line in str(out).splitlines():
        p = line.split()

        if len(p) >= 5 and p[0] == "LOSS" and p[2].isdigit() and p[3].isdigit():
            sent, got = int(p[2]), int(p[3])

            try:
                avg = float(p[4])
            except ValueError:
                avg = None

            res[p[1]] = {"loss": round(100.0 - got * 100.0 / sent, 1) if sent else None, "avg_ms": avg}

    return res


def _bad(x) -> bool:
    return x is not None and x >= BAD


def _majority_bad(values: list) -> bool | None:
    """True when most of the answered values are lossy, False when most are fine, None when nothing answered."""
    seen = [v for v in values if v is not None]

    if not seen:
        return None

    return sum(1 for v in seen if _bad(v)) * 2 > len(seen)


def locate(m: dict) -> dict:
    """Pure. m: pair_ab (Iran→this foreign), pair_ba (foreign→this Iran), iran_to_other_foreign [loss], other_iran_to_foreign [loss],
    iran_anchor [loss], foreign_anchor [loss], iran_domestic [loss] - every value a % or None (no answer)."""
    ab, ba = m.get("pair_ab"), m.get("pair_ba")
    pair_lossy = _bad(ab) or _bad(ba)
    iran_out = _majority_bad(m.get("iran_to_other_foreign", []))        # this Iran server toward other foreign servers
    foreign_in = _majority_bad(m.get("other_iran_to_foreign", []))      # other Iran servers toward this foreign server
    iran_anchor = _majority_bad(m.get("iran_anchor", []))
    foreign_anchor = _majority_bad(m.get("foreign_anchor", []))
    domestic = _majority_bad(m.get("iran_domestic", []))
    iran_bad = bool(iran_out or iran_anchor or domestic)
    foreign_bad = bool(foreign_in or foreign_anchor)

    if not pair_lossy and not iran_bad and not foreign_bad:
        known = any(v is not None for v in (ab, ba)) or iran_out is not None or foreign_in is not None
        return {"where": "none" if known else "unknown", "text": "no packet loss found between the servers or toward other servers" if known else "too few answers to say (ping may be filtered)"}

    if iran_bad and foreign_bad:
        return {"where": "both", "text": "loss on both ends: this Iran server loses toward other servers and the foreign server loses toward other Iran servers"}

    if iran_bad:
        why = "toward several foreign servers" if iran_out else "toward the internet" if iran_anchor else "even toward other Iran servers"
        return {"where": "iran_side", "text": f"the loss is on the IRAN side: this Iran server loses packets {why}, not only toward this foreign server (its own network or upstream)"}

    if foreign_bad:
        why = "other Iran servers lose packets toward this foreign server too" if foreign_in else "this foreign server loses packets toward the internet"
        return {"where": "foreign_side", "text": f"the loss is on the FOREIGN side: {why} (the foreign server or its provider)"}

    return {"where": "pair_route", "text": "the loss is only on the route between this pair: other servers reach each of them fine, so it is the peering between their networks"}


def _pick(servers: list[tuple[int, str, str]], n: int, skip: set[int]) -> list[tuple[int, str, str]]:
    return [s for s in servers if s[0] not in skip][:n]


def scan(tunnel_id: int) -> dict:
    """Measure and locate. Read only; about 8 seconds."""
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is None:
            raise ValueError("tunnel not found")

        i_id, f_id = t.iran_server_id, t.foreign_server_id
        online = [(s.id, s.name, s.host, s.role, Node(s.host, s.ssh_user, s.ssh_port)) for s in db.scalars(select(Server).where(Server.enabled.is_(True), Server.online.is_(True)))]

    iran = next((s for s in online if s[0] == i_id), None)
    foreign = next((s for s in online if s[0] == f_id), None)

    if iran is None or foreign is None:
        return {"where": "unknown", "text": "one of the two servers does not answer over SSH: the loss cannot be placed", "rows": [], "m": {}}

    others_f = [s for s in online if s[3] == "foreign" and s[0] != f_id][:3]
    others_i = [s for s in online if s[3] == "iran" and s[0] != i_id]
    dom = others_i[:2]
    far = others_i[:3]
    jobs = {"iran": (iran[4], [foreign[2]] + [s[2] for s in others_f] + list(ANCHORS) + [s[2] for s in dom]), "foreign": (foreign[4], [iran[2]] + list(ANCHORS))}

    for s in far:
        jobs[f"far{s[0]}"] = (s[4], [foreign[2]])

    def run(item):
        node, hosts = item

        try:
            return parse_loss(run_script(node, "loss_many", {"HOSTS": " ".join(dict.fromkeys(hosts))}, timeout=60))
        except (SSHError, ValueError) as e:
            log.warning("loss probe failed: %s", str(e)[:100])
            return {}

    with ThreadPoolExecutor(len(jobs)) as ex:
        out = dict(zip(jobs, ex.map(run, jobs.values())))

    def loss(key, host):
        return (out.get(key, {}).get(host) or {}).get("loss")

    m = {"pair_ab": loss("iran", foreign[2]), "pair_ba": loss("foreign", iran[2]),
         "iran_to_other_foreign": [loss("iran", s[2]) for s in others_f], "other_iran_to_foreign": [loss(f"far{s[0]}", foreign[2]) for s in far],
         "iran_anchor": [loss("iran", a) for a in ANCHORS], "foreign_anchor": [loss("foreign", a) for a in ANCHORS], "iran_domestic": [loss("iran", s[2]) for s in dom]}
    rows = [{"from": iran[1], "to": foreign[1], "loss": m["pair_ab"], "label": "this pair, Iran → foreign"}, {"from": foreign[1], "to": iran[1], "loss": m["pair_ba"], "label": "this pair, foreign → Iran"}]
    rows += [{"from": iran[1], "to": s[1], "loss": v, "label": "Iran server → other foreign server"} for s, v in zip(others_f, m["iran_to_other_foreign"])]
    rows += [{"from": s[1], "to": foreign[1], "loss": v, "label": "other Iran server → this foreign server"} for s, v in zip(far, m["other_iran_to_foreign"])]
    rows += [{"from": iran[1], "to": a, "loss": v, "label": "Iran server → internet"} for a, v in zip(ANCHORS, m["iran_anchor"])]
    rows += [{"from": foreign[1], "to": a, "loss": v, "label": "foreign server → internet"} for a, v in zip(ANCHORS, m["foreign_anchor"])]
    rows += [{"from": iran[1], "to": s[1], "loss": v, "label": "Iran server → other Iran server"} for s, v in zip(dom, m["iran_domestic"])]
    return {**locate(m), "rows": rows, "m": m}
