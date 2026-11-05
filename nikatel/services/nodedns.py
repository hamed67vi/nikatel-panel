"""The Doctor's look at the name lookups of the foreign servers (1.69.221).

(The owner, 2026-11-05: "the Emirates config is down but the Doctor knows nothing - and its tunnel shows connected". The foreign server UA listed 8.8.8.8 first in /etc/resolv.conf and that resolver does not answer from its network, so every name lookup of every
 connection through the server waited 5 s: the probe's test of the config died with "SSL connection timeout", the users waited 5 s for every new site, and the tunnel - which carries bytes, not names - was fine and said so. Every other foreign server answered in under 25 ms.)

  look   one light command per foreign server that ends a tunnel (`dns_look`): the best of three lookups of a test name and the first resolver of /etc/resolv.conf. A healthy server answers in 1-25 ms.
  slow   SLOW_MS or more on CONFIRM looks in a row, at least MIN_GAP seconds apart (one slow look is a blip).
  heal   in the Doctor's modes fix and move, the reviewed action `dns_heal`: the usual public resolvers are tried FROM the server, the ones that answer go first (the old ones stay behind them), `options timeout:2 attempts:2`, in the host's file and in the
         node container's copy (host network: nothing is restarted, nobody is cut); the lookup is measured again and everything is put back when it did not get better. Once a day per server at most, MAX_PER_DAY in the fleet; never on a link
         (systemd-resolved) and never when fewer than two resolvers answer: then the owner is told, in words, what is wrong.
  say    an event for a heal and for a fault the Doctor cannot heal; the look at suffering users (services/userq.py) names a slow lookup as the cause (`last_of`).
The pure parts (`parse_look`, `judge`, `parse_heal`, `rails`) carry the decisions and are tested."""
from __future__ import annotations

import concurrent.futures as cf
import threading
import time

from sqlalchemy import select

from .. import jobs
from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server, Tunnel, is_temp
from ..ssh import Node, SSHError, run_action, run_script
from . import settings as st
from .events import emit

log = get_logger("nodedns")
EVERY = 600
SLOW_MS = 1500.0        # a healthy server answers in 1-25 ms; one dead resolver in front of the list costs 2 s (the heal sets that timeout) or 5 s (the system's default)
CONFIRM = 2             # slow looks in a row ...
MIN_GAP = 240           # ... at least this many seconds apart
HEAL_EVERY = 24 * 3600  # per server
MAX_PER_DAY = 4         # in the whole fleet
_LOCK = threading.Lock()
_STATE: dict[int, dict] = {}          # server id -> {"ms", "at", "n", "last_slow_at"}
_TOLD: dict[str, float] = {}


# ------------------------------------------------------------------ pure (tested)
def parse_look(out: str) -> dict:
    """The answer of `dns_look` -> {"ms": best lookup in ms or None (curl missing), "link": /etc/resolv.conf is a link, "first": its first resolver}. Pure. Raises ValueError when it is not an answer."""
    kv: dict[str, str] = {}

    for line in str(out).splitlines():
        p = line.split()

        if p and p[0] in ("DNS_MS", "DNS_LINK", "DNS_FIRST"):
            kv[p[0]] = p[1] if len(p) > 1 else ""

    if "DNS_MS" not in kv:
        raise ValueError("the server did not answer the lookup probe")

    try:
        ms: float | None = float(kv["DNS_MS"])
    except ValueError:
        ms = None

    return {"ms": ms if ms is not None and ms >= 0 else None, "link": kv.get("DNS_LINK") == "1", "first": kv.get("DNS_FIRST", "")}


def judge(state: dict | None, look: dict, now: int) -> tuple[dict, str]:
    """Pure: (the new memory of the server, verdict) for one look. Verdicts: ok, unknown (the server cannot measure), wait (slow, not confirmed yet), slow (confirmed: CONFIRM slow looks MIN_GAP apart)."""
    old = dict(state or {})
    ms = look.get("ms")

    if ms is None:
        return {**old, "at": now}, "unknown"

    if ms < SLOW_MS:
        return {"ms": ms, "at": now, "n": 0, "last_slow_at": 0}, "ok"

    n, last = int(old.get("n", 0)), int(old.get("last_slow_at", 0))

    if n == 0 or now - last >= MIN_GAP:
        n, last = n + 1, now

    return {"ms": ms, "at": now, "n": n, "last_slow_at": last}, ("slow" if n >= CONFIRM else "wait")


def parse_heal(out: str) -> dict:
    """The answer of `dns_heal` -> {"result": ok | skip | fail, "before", "after" (ms), "first" (the resolver now first), "why"}. Pure."""
    for line in reversed(str(out).strip().splitlines()):
        p = line.split(None, 1)

        if not p:
            continue

        if p[0] == "DNS_OK":
            f = (p[1] if len(p) > 1 else "").split()
            nums = [int(x) for x in f[:2] if x.isdigit()]
            return {"result": "ok", "before": nums[0] if nums else None, "after": nums[1] if len(nums) > 1 else None, "first": f[2] if len(f) > 2 else "", "why": ""}

        if p[0] in ("DNS_SKIP", "DNS_FAIL"):
            return {"result": "skip" if p[0] == "DNS_SKIP" else "fail", "before": None, "after": None, "first": "", "why": (p[1] if len(p) > 1 else "").strip()[:160]}

    return {"result": "fail", "before": None, "after": None, "first": "", "why": "no answer from the server"}


def rails(done: list[int], healed: dict, sid: int, now: int) -> str:
    """Pure: "" when a heal may run on this server now, else why not (once a day per server, MAX_PER_DAY in the fleet)."""
    if now - int(healed.get(str(sid), 0)) < HEAL_EVERY:
        return "this server was healed less than a day ago"

    if len([x for x in done if now - int(x) < 86400]) >= MAX_PER_DAY:
        return f"{MAX_PER_DAY} heals were done in the last day"

    return ""


def last_of(server_id: int | None) -> float | None:
    """The best lookup time (ms) of the last look at this foreign server, or None when it was never looked at (userq names a slow lookup as the cause of suffering users)."""
    s = _STATE.get(int(server_id)) if server_id is not None else None
    return s.get("ms") if s else None


# ------------------------------------------------------------------ the job
def _servers() -> list[dict]:
    """The foreign servers a tunnel of the panel ends on (a host that carries no tunnel - the panel's own, the bot's - is not this job's business)."""
    with session() as db:
        ends = {t.foreign_server_id for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True))) if not is_temp(t.name)}
        return [{"id": s.id, "name": s.name, "host": s.host, "user": s.ssh_user, "port": s.ssh_port} for s in db.scalars(select(Server).where(Server.enabled.is_(True), Server.online.is_(True), Server.role == "foreign")) if s.id in ends]


def _node(s: dict) -> Node:
    return Node(s["host"], s["user"], s["port"])


def _tell(key: str, level: str, text: str, ref: str) -> None:
    if time.time() - _TOLD.get(key, 0) < 24 * 3600:
        return

    _TOLD[key] = time.time()

    with session() as db:
        emit(db, level, "servers", text, ref)


def run() -> None:
    """Job (every ten minutes): every foreign server that ends a tunnel is asked how fast it answers a name lookup; a slow one, confirmed twice, gets its resolvers put in order (see the top of this file)."""
    from . import actions, autopilot

    if get_settings().read_only or jobs.stopping() or actions.doctor_mode() == "off" or autopilot.quiet_now():
        return

    if not _LOCK.acquire(blocking=False):
        return

    try:
        now = int(time.time())
        servers = _servers()

        def look(s: dict):
            try:
                return s, parse_look(run_script(_node(s), "dns_look", {}, timeout=60))
            except (SSHError, ValueError, RuntimeError, OSError) as e:
                log.info("the name lookup of %s could not be measured: %s", s["name"], str(e)[:100])
                return s, None

        with cf.ThreadPoolExecutor(4) as ex:
            looks = list(ex.map(look, servers))

        doc = st.get("nodedns")
        healed, done = dict(doc.get("healed") or {}), [int(x) for x in (doc.get("done") or [])]

        for s, lk in looks:
            if lk is None:
                continue

            _STATE[s["id"]], verdict = judge(_STATE.get(s["id"]), lk, now)

            if verdict != "slow":
                continue

            ms = lk["ms"] or 0
            why = rails(done, healed, s["id"], now)

            if lk["link"]:
                _tell(f"link:{s['id']}", "warn", f"Server {s['name']} answers every name lookup in {ms / 1000:.1f} s ({lk['first'] or 'its first resolver'} does not answer) and its /etc/resolv.conf is managed by systemd-resolved: the Doctor does not touch it. "
                      "Put a resolver that answers first in its network settings.", s["name"])
                continue

            if actions.doctor_mode() not in ("fix", "move") or autopilot.storm_now() or why:
                _tell(f"slow:{s['id']}", "warn", f"Server {s['name']} answers every name lookup in {ms / 1000:.1f} s: {'the Doctor will put its resolvers in order when its mode allows it' if not why else why}. Everything through it waits that long for every new site.", s["name"])
                continue

            try:
                res = parse_heal(run_action(_node(s), "dns_heal", {}, timeout=150))
            except (SSHError, ValueError, RuntimeError, OSError) as e:
                res = {"result": "fail", "why": str(e)[:100], "before": None, "after": None, "first": ""}

            healed[str(s["id"])] = now

            if res["result"] == "ok":
                done.append(now)
                _STATE[s["id"]] = {"ms": float(res["after"] or 0), "at": now, "n": 0, "last_slow_at": 0}

                with session() as db:
                    emit(db, "warn", "servers", f"Server {s['name']} answered every name lookup in {(res['before'] or ms) / 1000:.1f} s (its first resolver did not answer): the resolvers that answer are first now, a lookup takes {res['after']} ms. "
                         f"The old /etc/resolv.conf is saved as /etc/resolv.conf.nikatel-bak on the server.", s["name"])
            else:
                _tell(f"fail:{s['id']}", "warn", f"Server {s['name']} answers every name lookup in {ms / 1000:.1f} s and the Doctor could not fix it by itself ({res['why'] or 'unknown'}). Put a resolver that answers first in its /etc/resolv.conf.", s["name"])

        st.put("nodedns", {"healed": healed, "done": done[-100:]})
    finally:
        _LOCK.release()
