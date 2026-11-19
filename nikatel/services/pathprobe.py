"""The layered look (1.69.290, the owner, 2026-11-19, after the Austria config answered 24-85 % of its tests for two days while its server, its DNS and its Iran network were fine and only its tunnel reset new connections):
the question "where is the problem" is answered by a LIGHT look at every layer between a user and the internet, not by one speed test.

  what is looked at     the user ports of the configs that ride a tunnel, nothing else - a few kilobytes per port, never a speed test: N fresh connections, the TCP connect time and the TLS handshake with the config's own SNI,
                        nothing is sent after it. Three layers: the NODE (from the panel's host, abroad: the foreign server's inbound port, no tunnel, no Iran network), the TUNNEL (on its Iran server itself, 127.0.0.1: the
                        tunnel and the node behind it, no network in between) and the PATH (from other Iran servers to the Iran server's own address - only for a tunnel whose users suffer while the first two are fine).
  in parallel           one ssh call per Iran server (all its ports at once, the ports in threads on the server) and one thread per foreign node, `THREADS` at a time: a round of the whole fleet takes about the time of the slowest.
  the verdict           the first layer that fails: `node` (the foreign node), else `tunnel` (the tunnel instance resets or stalls new connections), else `path` (the Iran network to the Iran server), else `ok`; a layer that was not
                        measured never accuses anybody. A verdict is kept in the record of the tunnel (`layers`) and changes only after CONFIRM looks in a row that agree (one noisy look is nothing).
  what is done          `tunnel`, confirmed, in the modes fix and move: the tunnel is restarted on both ends (`actions.restart_now`, the same action as the button; a few seconds, the users reconnect) - once in RESTART_GAP per tunnel,
                        never a protected one, never while the Doctor's own affairs wait. When that does not cure it (1.69.291: two looks after the restart the verdict is still `tunnel`) the next rung is another type built BESIDE the tunnel
                        without a cut (`escalate`: `doctor._run_retype`, the best other type that works on the pair and is not known to be unsteady) - once in 24 hours per tunnel, the rails of the restart on top.
  the tag               a foreign server whose node does not answer from outside for TAG_AFTER looks in a row gets a small tag (`Server.extra["health"]`, shown next to its name on the Servers page) and an event; it goes when the node
                        answers again. Nothing is tagged when the panel's own host is the blind one (most nodes fail at once: its network, not theirs).

The pure helpers (`parse_probe`, `layer_state`, `merge`, `verdict`, `settle`, `node_target`) carry the decisions and are tested."""
from __future__ import annotations

import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel, is_protected, is_standby, is_temp
from ..ssh import Node, SSHError, run_script
from . import settings as st
from .events import emit

log = get_logger("pathprobe")
EVERY = 1800                 # a look at the whole fleet this often
COUNT = 8                    # fresh connections per port per look
OK_SHARE = 0.75              # a layer passes when at least this share of its handshakes succeed
CONFIRM = 2                  # a verdict (and a tag) changes after this many looks in a row that agree
TAG_AFTER = 2
RESTART_GAP = 6 * 3600       # a tunnel is restarted by the look at most this often
ESCALATE_AFTER = 3300        # (1.69.291) a restart that was made this long ago (two looks) and a tunnel that STILL resets new connections: another type is built beside it
ESCALATE_GAP = 24 * 3600     # ... and it is done for the same tunnel at most this often
FLEET_SHARE = 1 / 3          # more than this share of the looked-at tunnels with the verdict `tunnel` at once: not the type of one tunnel
THREADS = 8
TIMEOUT = 4                  # seconds one connect or one handshake may take
BLIND_SHARE = 0.8            # more than this share of the nodes failing from the panel's host: the panel's host is blind, the node layer is not judged
MAX_PORTS = 60               # the most user ports looked at in one round
HANDSHAKE_BYTES = 5000       # what one handshake moves, about (counted in the day's volume of the Doctor's tests)
_LOCK = threading.Lock()
_RUNNING = [False]


# ------------------------------------------------------------------ pure helpers (tested)
def parse_probe(out: str) -> dict[int, dict]:
    """Pure: the lines `PORT=11436 N=8 CONN=8 TLS=6 P50=112 P90=2585 ERR=ConnectionResetError` of the script `tls_probe` -> {port: {"n", "conn", "tls", "p50", "p90", "err"}}. A line that does not parse is left out."""
    res: dict[int, dict] = {}

    for ln in str(out or "").splitlines():
        if not ln.startswith("PORT="):
            continue

        kv = dict(x.split("=", 1) for x in ln.split() if "=" in x)

        try:
            res[int(kv["PORT"])] = {"n": int(kv["N"]), "conn": int(kv["CONN"]), "tls": int(kv["TLS"]), "p50": int(kv.get("P50") or 0), "p90": int(kv.get("P90") or 0), "err": kv.get("ERR", "")}
        except (KeyError, ValueError):
            continue

    return res


def layer_state(r: dict | None) -> str:
    """Pure: 'ok' | 'bad' | 'none' for one layer's result: it passes when OK_SHARE of its handshakes succeeded; no result = not measured."""
    if not r or not r.get("n"):
        return "none"

    return "ok" if int(r.get("tls") or 0) >= OK_SHARE * int(r["n"]) else "bad"


def merge(rows: list[dict | None]) -> dict | None:
    """Pure: the results of the ports of one tunnel as one (the sums, the slowest p50 and p90, the first error); None when nothing was measured."""
    rows = [r for r in rows if r and r.get("n")]

    if not rows:
        return None

    return {"n": sum(int(r["n"]) for r in rows), "conn": sum(int(r.get("conn") or 0) for r in rows), "tls": sum(int(r.get("tls") or 0) for r in rows),
            "p50": max(int(r.get("p50") or 0) for r in rows), "p90": max(int(r.get("p90") or 0) for r in rows), "err": next((r["err"] for r in rows if r.get("err")), "")}


def best(rows: list[dict | None]) -> dict | None:
    """Pure: of the results of several vantages the one with the most handshakes through (a layer fails only when EVERY vantage fails: one vantage can have a bad network of its own)."""
    rows = [r for r in rows if r and r.get("n")]
    return max(rows, key=lambda r: int(r.get("tls") or 0) / int(r["n"])) if rows else None


def _share(r: dict) -> str:
    return f"{int(r.get('tls') or 0)} of {int(r['n'])}"


def _err(r: dict | None) -> str:
    return f" ({r['err']})" if r and r.get("err") else ""


def verdict(node: dict | None, tunnel: dict | None, path: dict | None) -> tuple[str, str]:
    """Pure: (code, one sentence) - the FIRST layer that fails. node: the foreign node does not answer from outside; tunnel: the node answers but the tunnel resets or stalls new connections; tunnel_or_node: the tunnel layer fails and
    the node layer was not measured (nothing is accused); path: node and tunnel pass, the Iran network to the Iran server does not; ok; unknown: nothing was measured."""
    ns, ts, ps = layer_state(node), layer_state(tunnel), layer_state(path)

    if node and ns == "bad":
        return "node", f"the foreign node answers only {_share(node)} TLS handshakes from outside the tunnel{_err(node)}"

    if tunnel and ts == "bad":
        if ns == "ok":
            return "tunnel", f"the node answers, but through the tunnel only {_share(tunnel)} TLS handshakes succeed on its Iran server itself{_err(tunnel)}: the tunnel resets or stalls new connections"

        return "tunnel_or_node", f"only {_share(tunnel)} TLS handshakes succeed through the tunnel on its Iran server itself{_err(tunnel)}; the node was not measured from outside"

    if path and ps == "bad":
        return "path", f"the tunnel and the node are fine, but from other Iran servers only {_share(path)} TLS handshakes reach the Iran server"

    if "ok" not in (ns, ts, ps):
        return "unknown", ""

    return "ok", ""


def settle(old: dict | None, code: str, now: int) -> tuple[dict, bool]:
    """Pure: the kept state {"code", "since", "pend": {"code", "n"}} after one more look that says `code` - it changes only when CONFIRM looks in a row agree; the first look of all is taken at once when it finds nothing wrong
    (a tunnel's first look that finds a fault is `unknown` until a second look says the same: nothing is accused, nothing is restarted on one look). Returns (state, changed)."""
    old = dict(old or {})
    pend = dict(old.get("pend") or {})
    pend = {"code": code, "n": int(pend.get("n", 0)) + 1} if pend.get("code") == code else {"code": code, "n": 1}
    cur = old.get("code")

    if cur is None:
        if code in ("ok", "unknown"):
            return {"code": code, "since": now, "pend": pend}, True

        return {"code": "unknown", "since": now, "pend": pend}, False

    if code == cur:
        return {"code": cur, "since": int(old.get("since") or now), "pend": pend}, False

    if pend["n"] >= CONFIRM:
        return {"code": code, "since": now, "pend": pend}, True

    return {"code": cur, "since": int(old.get("since") or now), "pend": pend}, False


def node_target(record: dict | None, listen: int) -> int:
    """Pure: the port of the foreign node that the user port `listen` of a tunnel is forwarded to (`record["port_mapping"]`: "8079:8075,1829:1820,11429:8680"; a port without a target is the same port there)."""
    for part in str((record or {}).get("port_mapping") or "").split(","):
        left, _, right = part.strip().partition(":")

        if left.isdigit() and int(left) == int(listen):
            return int(right) if right.isdigit() else int(listen)

    return int(listen)


# ------------------------------------------------------------------ the layers
def handshakes(host: str, port: int, sni: str, n: int = COUNT) -> dict:
    """One layer's measurement from THIS machine: n fresh connections to host:port, the TCP connect and the TLS handshake with `sni` (nothing is sent after it). It stops early once the share can no longer be reached."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn, tls, times, err, fails = 0, 0, [], "", 0

    for i in range(n):
        s = None

        try:
            s = socket.create_connection((host, int(port)), timeout=TIMEOUT)
            conn += 1
            t0 = time.time()
            w = ctx.wrap_socket(s, server_hostname=sni)
            times.append((time.time() - t0) * 1000)
            tls += 1
            w.close()
            s = None
        except (OSError, ssl.SSLError) as e:
            fails += 1
            err = err or type(e).__name__
        finally:
            if s is not None:
                s.close()

        if fails > n - int(OK_SHARE * n + 0.999):          # (it cannot pass any more)
            break

        if i < n - 1:
            time.sleep(0.2)

    times.sort()
    return {"n": n, "conn": conn, "tls": tls, "p50": int(times[len(times) // 2]) if times else 0, "p90": int(times[min(len(times) - 1, int(len(times) * 0.9))]) if times else 0, "err": err}


def _remote(node: Node, host: str, ports: list[int], sni: str) -> dict[int, dict]:
    """The same measurement made BY an Iran server (`ssh.tls_probe`: one call, all its ports in threads there)."""
    try:
        out = run_script(node, "tls_probe", {"HOST": host, "PORTS": " ".join(str(p) for p in ports), "SNI": sni, "COUNT": str(COUNT)}, timeout=75)
    except (SSHError, ValueError, OSError) as e:
        log.info("the layered look on %s failed: %s", node.host, str(e)[:100])
        return {}

    return parse_probe(out)


def plan() -> list[dict]:
    """The user ports to look at: one item per (tunnel, port of a config that rides it) - {tunnel_id, name, iran_id, iran_host, ..., listen, target, sni, grade}. Only configs whose port the tunnel forwards (a CDN name's port is the CDN's)."""
    from . import userq

    items: dict[tuple, dict] = {}

    with session() as db:
        cfgs, placed = userq._placement(db)
        tunnels = {t.id: t for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name) and not is_standby(t)}

        for c in cfgs:
            p = placed.get(c.name)
            t = tunnels.get(p["tunnel"]) if p else None
            meta = c.meta or {}

            if t is None or t.iran is None or t.foreign is None:
                continue

            listen = int(meta.get("port") or 0)

            if not listen or listen not in [int(x) for x in (t.forwarded_ports or []) if str(x).isdigit()]:
                continue

            sni = str(meta.get("sni") or meta.get("host") or meta.get("address") or "").lower()

            if not sni or len(sni) > 99:
                continue

            key = (t.id, listen, sni)
            items.setdefault(key, {"tunnel_id": t.id, "name": t.name, "iran_id": t.iran_server_id, "iran_host": t.iran.host, "iran_user": t.iran.ssh_user, "iran_port": t.iran.ssh_port, "foreign_id": t.foreign_server_id,
                                   "foreign_host": t.foreign.host, "listen": listen, "target": node_target(t.record, listen), "sni": sni, "protected": is_protected(t),
                                   "grade": str(((t.record or {}).get("ux") or {}).get("grade") or ""), "was": str(((t.record or {}).get("layers") or {}).get("state", {}).get("code") or "")})

    return sorted(items.values(), key=lambda x: (x["tunnel_id"], x["listen"]))[:MAX_PORTS]


def look(items: list[dict]) -> dict:
    """Make the measurements of every layer for the planned ports, in parallel. Returns {"tunnels": {tunnel id: {"node", "tunnel", "path"}}, "servers": {foreign id: {"node": merged}}, "blind": bool, "handshakes": how many}."""
    node_res: dict[tuple, dict] = {}
    tun_res: dict[tuple, dict] = {}
    path_res: dict[tuple, list[dict]] = {}
    count = [0]

    with ThreadPoolExecutor(THREADS) as ex:
        node_f = {k: ex.submit(handshakes, k[0], k[1], k[2]) for k in {(i["foreign_host"], i["target"], i["sni"]) for i in items}}
        groups: dict[tuple, list[dict]] = {}

        for i in items:
            groups.setdefault((i["iran_id"], i["sni"]), []).append(i)

        tun_f = {g: ex.submit(_remote, Node(v[0]["iran_host"], v[0]["iran_user"], v[0]["iran_port"]), "127.0.0.1", sorted({x["listen"] for x in v}), g[1]) for g, v in groups.items()}

        for k, f in node_f.items():
            node_res[k] = f.result()

        for g, f in tun_f.items():
            for port, r in f.result().items():
                tun_res[(g[0], g[1], port)] = r

        # the path: only for a tunnel whose users suffer (or suffered) while the tunnel and the node pass
        need = [i for i in items if (i["grade"] in ("poor", "bad") or i["was"] == "path") and layer_state(tun_res.get((i["iran_id"], i["sni"], i["listen"]))) == "ok"
                and layer_state(node_res.get((i["foreign_host"], i["target"], i["sni"]))) != "bad"]
        servers_up = _vantages()
        pf = {}

        for i in need:
            vs = [v for v in servers_up if v["id"] != i["iran_id"]][:2]

            for v in vs:
                pf[(i["tunnel_id"], i["listen"], v["id"])] = ex.submit(_remote, Node(v["host"], v["user"], v["port"]), i["iran_host"], [i["listen"]], i["sni"])

        for (tid, listen, _vid), f in pf.items():
            path_res.setdefault((tid, listen), []).append(f.result().get(listen) or {})

    for r in list(node_res.values()) + list(tun_res.values()) + [x for v in path_res.values() for x in v]:
        count[0] += int(r.get("n") or 0)

    nodes_bad = [k for k, r in node_res.items() if layer_state(r) == "bad"]
    blind = bool(node_res) and len(node_res) >= 3 and len(nodes_bad) > BLIND_SHARE * len(node_res)            # (most nodes fail from here at once: the panel's host is the blind one)
    out: dict = {"tunnels": {}, "servers": {}, "blind": blind, "handshakes": count[0]}
    by_tunnel: dict[int, dict] = {}

    for i in items:
        d = by_tunnel.setdefault(i["tunnel_id"], {"node": [], "tunnel": [], "path": [], "name": i["name"], "protected": i["protected"], "foreign_id": i["foreign_id"]})
        d["node"].append(node_res.get((i["foreign_host"], i["target"], i["sni"])))
        d["tunnel"].append(tun_res.get((i["iran_id"], i["sni"], i["listen"])))
        d["path"].append(best(path_res.get((i["tunnel_id"], i["listen"]), [])))

    for tid, d in by_tunnel.items():
        out["tunnels"][tid] = {"name": d["name"], "protected": d["protected"], "foreign_id": d["foreign_id"], "node": None if blind else merge(d["node"]), "tunnel": merge(d["tunnel"]), "path": merge(d["path"])}

        if not blind:
            fs = out["servers"].setdefault(d["foreign_id"], [])
            fs.extend(d["node"])

    out["servers"] = {sid: merge(rows) for sid, rows in out["servers"].items()}
    return out


def _vantages() -> list[dict]:
    with session() as db:
        return [{"id": s.id, "host": s.host, "user": s.ssh_user, "port": s.ssh_port} for s in db.scalars(select(Server).where(Server.role == "iran", Server.enabled.is_(True), Server.online.is_(True)).order_by(Server.id))]


# ------------------------------------------------------------------ what is kept and told
def _store(tid: int, r: dict, now: int) -> tuple[str, str] | None:
    """Keep the layers and the settled verdict in the record of the tunnel; one event when the verdict changes. Returns (old code, new code) when it changed."""
    code, text = verdict(r.get("node"), r.get("tunnel"), r.get("path"))

    with session() as db:
        t = db.get(Tunnel, tid)

        if t is None:
            return None

        rec = dict(t.record or {})
        old = dict(rec.get("layers") or {})
        state, changed = settle(old.get("state"), code, now)
        rec["layers"] = {"at": now, "node": r.get("node"), "tunnel": r.get("tunnel"), "path": r.get("path"), "state": state, "text": text if state["code"] == code else old.get("text", "")}
        t.record = rec
        was = (old.get("state") or {}).get("code")

        if changed and state["code"] in ("node", "tunnel", "tunnel_or_node", "path"):
            emit(db, "warn", "tunnels", f"Tunnel {t.name}: the layered look - {text}.", t.name)
        elif changed and was in ("node", "tunnel", "tunnel_or_node", "path") and state["code"] == "ok":
            emit(db, "ok", "tunnels", f"Tunnel {t.name}: the layered look finds every layer fine again.", t.name)

        return (was or "", state["code"]) if changed else None


def _tag(server_id: int, r: dict | None, blind: bool, now: int) -> None:
    """The small tag of a foreign server whose node does not answer from outside (TAG_AFTER looks in a row); it goes when the node answers again. Nothing changes while the panel's own host is blind."""
    if blind or r is None or layer_state(r) == "none":
        return

    bad = layer_state(r) == "bad"

    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            return

        extra = dict(s.extra or {})
        before = dict(extra)
        tagged = (extra.get("health") or {}).get("code") == "node"

        if bad:
            extra.pop("health_ok", None)
            extra["health_bad"] = int(extra.get("health_bad") or 0) + 1

            if extra["health_bad"] >= TAG_AFTER and not tagged:
                text = f"the node answers only {_share(r)} TLS handshakes from outside{_err(r)}"
                extra["health"] = {"code": "node", "short": "node problem", "text": text, "since": now, "at": now}
                emit(db, "warn", "servers", f"Server {s.name} is tagged: {text}.", s.name)
            elif tagged:
                extra["health"] = {**extra["health"], "at": now}
        else:
            extra.pop("health_bad", None)

            if tagged:
                extra["health_ok"] = int(extra.get("health_ok") or 0) + 1

                if extra["health_ok"] >= TAG_AFTER:
                    extra.pop("health", None)
                    extra.pop("health_ok", None)
                    emit(db, "ok", "servers", f"Server {s.name}: its node answers again, the tag is removed.", s.name)

        if extra != before:
            s.extra = extra


def _fix_allowed(name: str, now: int) -> str:
    """'' when the look may restart this tunnel now, else why not."""
    from . import autopilot, bandwidth, swap

    if get_settings().read_only or not bandwidth.actions_mode_allows_fix():
        return "the Doctor is not in the mode fix or move"

    if autopilot.quiet_now():
        return "the Doctor's own affairs wait now"

    if swap.at_once():
        return "a swap beside a tunnel is going on"

    last = int(((st.get("pathprobe") or {}).get("restarted") or {}).get(name) or 0)

    if last and now - last < RESTART_GAP:
        return f"it was restarted by the look {(now - last) // 60} minutes ago"

    return ""


def restart_story(aid: int, tunnel_id: int, why: str) -> None:
    """The story of the restart: the reason, the action of the button (`actions.restart_now`), what came of it."""
    from . import actions, autopilot

    autopilot._say(aid, why)

    try:
        res = actions.restart_now(tunnel_id, source="auto")
    except (ValueError, RuntimeError) as e:
        autopilot._say(aid, f"Not restarted: {str(e)[:160]}", done=True, ok=False)
        return

    autopilot._say(aid, f"{res['name']}: " + "; ".join(res.get("steps") or []) + f" - the tunnel is {res.get('state')}" + (f" ({res['why']})" if res.get("why") else "") + ". The next look (in half an hour) measures the layers again.", done=True, ok=res.get("state") == "up")


def _mark(key: str, name: str, now: int) -> None:
    """Keep when the look did `key` ("restarted" | "escalated") for a tunnel; marks older than a week go."""
    d = dict(st.get("pathprobe") or {})
    r = {k: v for k, v in (d.get(key) or {}).items() if now - int(v) < 7 * 86400}
    r[name] = now
    st.put("pathprobe", {key: r})


def _mark_restart(name: str, now: int) -> None:
    _mark("restarted", name, now)


def fix(result: dict, now: int) -> list[str]:
    """The rung of the look: a tunnel whose settled verdict is `tunnel` (the node answers, the tunnel resets new connections) is restarted - one tunnel per look, under the rails of `_fix_allowed`. Returns the names it started a restart for."""
    from . import autopilot

    started: list[str] = []

    with session() as db:
        rows = [(t.id, t.name, dict((t.record or {}).get("layers") or {})) for t in db.scalars(select(Tunnel).where(Tunnel.id.in_(list(result["tunnels"]) or [0])))]

    for tid, name, lay in rows:
        state = lay.get("state") or {}
        r = result["tunnels"].get(tid) or {}

        if state.get("code") != "tunnel" or r.get("protected") or verdict(r.get("node"), r.get("tunnel"), r.get("path"))[0] != "tunnel":
            continue

        if _fix_allowed(name, now):
            continue

        _mark_restart(name, now)

        try:
            autopilot._start(name, restart_story, tid, f"The layered look: {lay.get('text') or 'the tunnel resets new connections'}. It has said so for {max(1, (now - int(state.get('since') or now)) // 60)} minutes: the tunnel is restarted on both ends.",
                             action="autopilot", source="auto")
        except ValueError as e:
            log.info("the restart of %s was not started: %s", name, str(e)[:100])
            continue

        started.append(name)
        break

    return started


def escalate_wanted(restarted_at: int, state: dict | None, code: str, now: int, tried_at: int = 0) -> str:
    """Pure: '' when the look may build another type beside a tunnel now, else why not. The tunnel was restarted by the look `restarted_at` (0 = never), the settled state is `state`, this look says `code`:
    only a tunnel that was restarted, whose verdict was `tunnel` before and after, ESCALATE_AFTER seconds later, and has not been escalated in the last ESCALATE_GAP."""
    if code != "tunnel" or (state or {}).get("code") != "tunnel":
        return "the tunnel does not reset new connections now"

    if not restarted_at:
        return "the look has not restarted it yet"

    if now - restarted_at < ESCALATE_AFTER:
        return f"it was restarted only {(now - restarted_at) // 60} minutes ago: the next look says whether that cured it"

    if tried_at and now - tried_at < ESCALATE_GAP:
        return f"another type was tried for it {(now - tried_at) // 3600} hours ago"

    return ""


def escalate(result: dict, now: int) -> list[str]:
    """The second rung of the look: a tunnel that was restarted by the look and still resets new connections two looks later gets another type built beside it, without a cut (`doctor._run_retype`, the same swap as every automatic change
    of type; the speed gain is not asked for - a speed test does not see a tunnel that stalls new connections, the new tunnel is judged by what it carries). One tunnel per look. The rails: the modes fix and move, nothing while the Doctor's own
    affairs wait, `swap.MAX_AT_ONCE`, the day's volume of tests (`urgent`), once in 24 hours per tunnel (`doctor.may_retype` and ESCALATE_GAP), a protected tunnel never, and not while more than a third of the looked-at tunnels say `tunnel` at once
    (then it is the Iran side or the panel's host, not the type of one tunnel). Returns the names it started a change for."""
    from . import autopilot, bandwidth, doctor, swap, testbudget, userq

    rows_res = result.get("tunnels") or {}
    bad = [tid for tid, r in rows_res.items() if verdict(r.get("node"), r.get("tunnel"), r.get("path"))[0] == "tunnel"]

    if not bad or len(rows_res) >= 3 and len(bad) > FLEET_SHARE * len(rows_res):
        return []

    if get_settings().read_only or not bandwidth.actions_mode_allows_fix() or autopilot.quiet_now() or swap.at_once() >= swap.MAX_AT_ONCE or not testbudget.allow("urgent"):
        return []

    doc = st.get("pathprobe") or {}
    restarted, escalated = dict(doc.get("restarted") or {}), dict(doc.get("escalated") or {})

    with session() as db:
        rows = [(t.id, t.name, dict((t.record or {}).get("layers") or {}), is_protected(t)) for t in db.scalars(select(Tunnel).where(Tunnel.id.in_(bad)))]

    for tid, name, lay, protected in rows:
        r = rows_res.get(tid) or {}
        code = verdict(r.get("node"), r.get("tunnel"), r.get("path"))[0]

        if protected or escalate_wanted(int(restarted.get(name) or 0), lay.get("state"), code, now, int(escalated.get(name) or 0)):
            continue

        if doctor.may_retype(name, doctor._state().get("retype_done") or {}, now):
            continue

        try:
            snap = autopilot._snapshot(tid)
            cur = autopilot.kind_of(snap["type"], snap["transport"], snap["record"])
        except ValueError:
            continue

        cand, _pct = userq._steadier_kind(snap, cur, now, force=True)

        if cand is None:
            continue

        why = (f"The layered look: {lay.get('text') or 'the tunnel resets new connections'}. The tunnel was restarted {(now - int(restarted.get(name) or now)) // 60} minutes ago and it still does, so {cand['kind']} (it works on this pair, score {cand.get('score')}) "
               f"is built beside it and the users' new connections are handed over without a cut. The new tunnel is judged by what it carries, not by a speed gain.")

        try:
            autopilot._start(name, doctor._run_retype, tid, cand["kind"], cur, why, True, False, 0.0, action="autopilot", source="auto")
        except ValueError as e:
            log.info("the change of type of %s was not started: %s", name, str(e)[:100])
            continue

        _mark("escalated", name, now)
        userq._UX_TRIED[name] = now
        return [name]

    return []


# ------------------------------------------------------------------ the job
def run() -> list[str]:
    """Job (every EVERY seconds): plan, look at every layer in parallel, keep the verdicts, tag the foreign servers whose node fails, restart a tunnel that resets. Nothing when the Doctor is off or the panel is read-only.
    Returns the names of the tunnels a restart or a change of type was started for."""
    from . import actions, testbudget

    if get_settings().read_only or jobs.stopping() or actions.doctor_mode() == "off":
        return []

    with _LOCK:
        if _RUNNING[0]:
            return []

        _RUNNING[0] = True

    try:
        now = int(time.time())
        items = plan()

        if not items:
            return []

        result = look(items)
        testbudget.spend(int(result.get("handshakes", 0)) * HANDSHAKE_BYTES, "pathprobe", now)

        for tid, r in result["tunnels"].items():
            try:
                _store(tid, r, now)
            except Exception:
                log.exception("the verdict of tunnel %s could not be kept", tid)

        for sid, r in result["servers"].items():
            try:
                _tag(sid, r, result["blind"], now)
            except Exception:
                log.exception("the tag of server %s could not be kept", sid)

        started = fix(result, now)

        if not started:
            started = escalate(result, now)

        return started
    finally:
        with _LOCK:
            _RUNNING[0] = False
