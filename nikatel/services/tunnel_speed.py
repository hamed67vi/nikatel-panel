"""The speed a CUSTOMER gets through a tunnel (the owner, 2026-10-16: "I want to see what speed a user in Iran gets - what the customer holds - the closest possible to it").
Two ways, the closest first:
  customer  the probe (a server inside Iran) downloads 25 MB and uploads 10 MB from the internet THROUGH a config that rides this tunnel, ONE connection like a phone does:
            the result is the whole path of the customer (Iran server -> tunnel -> foreign server -> the internet) with the real protocol of the config on it;
  door      no probe or no config: one and then four connections through the test door of the tunnel (BackPack, Paqet, Chisel), with data that cannot be compressed:
            the tunnel alone, from the Iran server to the foreign one.
The answer is kept in the record of the tunnel (record.speed) and set beside what the tunnel can carry (record.quality), so the verdict says where the speed is lost."""
from __future__ import annotations

import time
from urllib.parse import quote

from sqlalchemy import select

from ..db import session
from ..log import get_logger
from ..models import Config, Tunnel
from ..ssh import SSHError
from . import livebw, probe

log = get_logger("tunnel-speed")
MAX_CONFIGS = 3                  # the configs of one tunnel that are asked, the best looking first (each takes up to a minute on the probe)
BUSY_TRIES = 4                   # the probe runs ONE speed test at a time: a busy answer is waited out this many times
FEELS = ((50, "smooth", "Smooth: 4K video, big downloads and calls run without waiting."),
         (20, "good", "Good: HD video, browsing and calls are fine; a big download takes a while."),
         (8, "fair", "Fair: browsing and HD video work; heavy use makes it wait."),
         (2, "slow", "Slow: pages open, video stalls."),
         (0, "bad", "Very slow: almost nothing gets through."))


def feel(down: float | None) -> tuple[str, str]:
    """(grade, what it feels like) of the download speed of ONE connection."""
    d = down or 0.0

    for floor, key, text in FEELS:
        if d >= floor:
            return key, text

    return FEELS[-1][1], FEELS[-1][2]


def riders(t: Tunnel, configs: list[Config]) -> list[Config]:
    """The configs that ride this tunnel: the ones labelled with its name, or whose address is the Iran server and whose port is one the tunnel forwards. The ones that are up come first."""
    hosts = {str(t.iran.host), str(t.iran.name)}
    ports = {str(p) for p in (t.forwarded_ports or [])}
    out = [c for c in configs if c.enabled and (c.tunnel == t.name or (str((c.meta or {}).get("address", "")) in hosts and str((c.meta or {}).get("port", "")) in ports))]
    return sorted(out, key=lambda c: (c.status != "up", c.ping_ms if c.ping_ms is not None else 1e9, c.name))


def _tunnel(db, tunnel_id: int) -> Tunnel:
    t = db.get(Tunnel, tunnel_id)

    if t is None:
        raise ValueError("tunnel not found")

    return t


def options(tunnel_id: int) -> dict:
    """What can be measured on this tunnel, and what was measured last (for the window)."""
    with session() as db:
        t = _tunnel(db, tunnel_id)
        rec, sec = t.record or {}, t.secrets or {}
        rows = riders(t, list(db.scalars(select(Config))))
        cfgs = [{"id": c.id, "name": c.name, "status": c.status, "ping_ms": c.ping_ms, "down_mbps": c.down_mbps, "up_mbps": c.up_mbps, "speed_at": c.speed_at} for c in rows]
        door = bool(rec.get("test_port") and sec.get("test_key"))
        name, kind, last, cap = t.name, t.type, rec.get("speed"), rec.get("quality")

    has_probe = bool(probe.candidates())
    why = "" if has_probe and cfgs else "no probe is set (Settings → Api & Key)" if not has_probe else "no config is labelled with this tunnel (Configs → the tunnel column)"
    return {"ok": True, "name": name, "type": kind, "customer": {"ok": has_probe and bool(cfgs), "why": why, "configs": cfgs},
            "door": {"ok": door, "why": "" if door else "this tunnel has no test door (BackPack, Paqet and Chisel have one)"}, "last": last, "capacity": cap}


REVERSE_SLOW = 30      # Mb/s of ONE connection under which a tunnel that the foreign server dials is told about the ones the Iran server dials


def verdict(mode: str, down: float | None, up: float | None, ping: float | None, cap: dict | None, runs: int = 1, reverse_tcp: bool = False) -> tuple[str, str]:
    """(grade, the text under the numbers): what it feels like, and where the speed is lost when the tunnel can carry much more than one customer gets.
    `reverse_tcp`: a TCP tunnel that the foreign server dials (real tests, 2026-10-16: the route slows the data that flows away from the side that opened the connection - 20 - 130 Mb/s for all the connections together)."""
    grade, text = feel(down)
    parts = [text]
    cd = (cap or {}).get("down_mbps")

    if reverse_tcp and down is not None and down < REVERSE_SLOW:
        parts.append("The foreign server dials this tunnel, and some routes slow the data that flows away from the side that opened the connection (on the route this was measured on, 20 - 130 Mb/s for all connections together). A tunnel the Iran server dials - Chisel forward, Paqet, BackPack Direct - can give customers much more: try them with Test this pair.")

    if down is not None and cd and cd > 0:
        if down < 0.25 * cd and mode == "customer" and grade != "smooth":
            parts.append(f"The tunnel can carry about {round(cd)} Mb/s together, and ONE customer connection gets {round(down)}: the rest is lost beyond the tunnel (the foreign server's own internet, the protocol of the config, the CDN) or one connection cannot fill a long path - several connections together do better.")
        elif down >= 0.6 * cd:
            parts.append(f"One connection gets most of what the tunnel can carry ({round(cd)} Mb/s): the tunnel is the limit - another kind of tunnel may be faster.")

    if ping is not None and ping > 600:
        parts.append(f"The ping is high ({round(ping)} ms): pages and games feel slow whatever the speed.")

    if up is not None and down and up < 0.1 * down:
        parts.append("The upload is much smaller than the download: sending photos and video calls will feel slower than browsing.")

    return grade, " ".join(parts)


def _keep(tunnel_id: int, res: dict) -> None:
    with session() as db:
        t = db.get(Tunnel, tunnel_id)

        if t is not None:
            rec = dict(t.record or {})
            rec["speed"] = res
            t.record = rec


def _num(v) -> float | None:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def _ask_probe(name: str, say) -> dict:
    """One speed test of one config on the probe; a probe that is busy with another is waited for."""
    for attempt in range(BUSY_TRIES):
        d = probe.request("POST", "/api/speed?name=" + quote(name, safe=""), timeout=200)

        if d.get("ok"):
            return d.get("speed") or {}

        if "running" in str(d.get("error", "")) and attempt + 1 < BUSY_TRIES:
            say("The probe is busy with another speed test: waiting 20 s…")
            time.sleep(20)
            continue

        raise RuntimeError(str(d.get("error") or "the probe gave no answer")[:120])

    raise RuntimeError("the probe stayed busy")


def run_customer(aid: int, tunnel_id: int, say) -> dict:
    """Through the configs of the tunnel on the probe: the speed of one connection, the way a customer's phone gets it."""
    opts = options(tunnel_id)

    if not opts["customer"]["ok"]:
        raise ValueError(opts["customer"]["why"])

    cfgs = opts["customer"]["configs"][:MAX_CONFIGS]
    say(f"{len(opts['customer']['configs'])} config{'s' if len(opts['customer']['configs']) != 1 else ''} ride this tunnel; the probe measures {len(cfgs)} of them, one connection each way (about a minute for each).")
    runs = []

    with livebw.quiet(tunnel_id, 100.0 * len(cfgs) + 30):        # (the probe's downloads are the panel's own test: they are not the users' use of the tunnel)
        for c in cfgs:
            say(f"«{c['name']}»: the probe downloads 25 MB and uploads 10 MB through it…")

            try:
                sp = _ask_probe(c["name"], say)
            except (RuntimeError, ValueError) as e:
                say(f"«{c['name']}»: no answer ({str(e)[:100]})")
                runs.append({"name": c["name"], "ok": False, "error": str(e)[:100]})
                continue

            r = {"name": c["name"], "ok": bool(sp.get("ok", True)), "ping_ms": _num(sp.get("ping")), "down_mbps": _num(sp.get("download")), "up_mbps": _num(sp.get("upload"))}
            runs.append(r)
            say(f"«{c['name']}»: ↓ {r['down_mbps']} Mb/s · ↑ {r['up_mbps']} Mb/s · ping {r['ping_ms']} ms")

    good = [r for r in runs if r.get("ok") and r.get("down_mbps") is not None]

    if not good:
        raise RuntimeError("no config answered: the tunnel may be down, or the configs do not work (see Configs)")

    best = max(good, key=lambda r: r["down_mbps"])

    try:
        from . import configs as cfg_service

        cfg_service.sync()          # the numbers show in the list of configs too
    except (RuntimeError, ValueError, OSError) as e:
        log.info("sync after the speed test: %s", str(e)[:80])

    return _finish(tunnel_id, "customer", best["down_mbps"], best.get("up_mbps"), best.get("ping_ms"), runs, say, best["name"])


def run_door(aid: int, tunnel_id: int, say) -> dict:
    """Through the test door of the tunnel: one connection, then four (random data: a tunnel that compresses must not look faster than it is)."""
    from ..ssh import run_action
    from . import build_other

    with session() as db:
        t = _tunnel(db, tunnel_id)
        rec, sec = t.record or {}, t.secrets or {}

        if not (rec.get("test_port") and sec.get("test_key")):
            raise ValueError("this tunnel has no test door (BackPack, Paqet and Chisel have one)")

        iran, foreign, port, key = build_other._node(t.iran), build_other._node(t.foreign), int(rec["test_port"]), str(sec["test_key"])
        name, bind = build_other.sname(t), "0.0.0.0" if str(t.transport or "").startswith("l3:") else ""

    try:
        run_action(foreign, "sink_install", {"NAME": name, "PORT": str(port), "KEY": key, "BIND": bind}, timeout=60)          # (the sink is kept at the current version)
    except (SSHError, ValueError) as e:
        log.info("sink refresh: %s", str(e)[:80])

    say("One connection through the test door of the tunnel: 6 s down, 6 s up…")

    with livebw.quiet(tunnel_id, 90):                             # (the two tests move data over the link: not the users')
        one = build_other._probe(iran, port, key, secs=6, streams=1, pings=20, to=2.5)

        if one.get("error") or one.get("latency_ms") is None:
            raise RuntimeError("no data passes through the tunnel right now" + (f" ({one['error']})" if one.get("error") else ""))

        say(f"One connection: ↓ {one.get('down_mbps')} Mb/s · ↑ {one.get('up_mbps')} Mb/s · ping {one.get('latency_ms')} ms")
        say("Four connections together: 6 s down, 6 s up…")
        four = build_other._probe(iran, port, key, secs=6, streams=4, pings=2, to=2.5)

    say(f"Four connections: ↓ {four.get('down_mbps')} Mb/s · ↑ {four.get('up_mbps')} Mb/s")
    runs = [{"name": "1 connection", "ok": True, "ping_ms": _num(one.get("latency_ms")), "down_mbps": _num(one.get("down_mbps")), "up_mbps": _num(one.get("up_mbps"))},
            {"name": "4 connections", "ok": True, "ping_ms": None, "down_mbps": _num(four.get("down_mbps")), "up_mbps": _num(four.get("up_mbps"))}]
    return _finish(tunnel_id, "door", runs[0]["down_mbps"], runs[0]["up_mbps"], runs[0]["ping_ms"], runs, say, "")


def _finish(tunnel_id: int, mode: str, down, up, ping, runs: list[dict], say, via: str) -> dict:
    with session() as db:
        t = _tunnel(db, tunnel_id)
        rec = t.record or {}
        cap = rec.get("quality")
        reverse_tcp = t.type in ("backpack", "chisel") and rec.get("direction") == "reverse" and not rec.get("udp_carrier")

    cap = {"down_mbps": cap.get("down_mbps"), "up_mbps": cap.get("up_mbps"), "at": cap.get("at")} if cap and cap.get("down_mbps") else None
    grade, text = verdict(mode, down, up, ping, cap, len(runs), reverse_tcp)
    res = {"mode": mode, "at": int(time.time()), "via": via, "ping_ms": ping, "down_mbps": down, "up_mbps": up, "runs": runs, "capacity": cap, "grade": grade, "verdict": text}
    _keep(tunnel_id, res)
    say(f"Result: ↓ {down} Mb/s · ↑ {up} Mb/s" + (f" · ping {ping} ms" if ping is not None else "") + f" - {text.split(':')[0]}.")
    return res


def start(tunnel_id: int, mode: str) -> int:
    """The speed test as an action (follow /api/actions/{id}); read only apart from the test sink of the door."""
    from . import autopilot

    if mode not in ("customer", "door"):
        raise ValueError("mode is customer or door")

    with session() as db:
        name = _tunnel(db, tunnel_id).name

    opts = options(tunnel_id)

    if not opts[mode]["ok"]:
        raise ValueError(opts[mode]["why"])

    def work(aid: int) -> None:
        say = lambda line: autopilot._say(aid, line)      # (one short line for the story)
        (run_customer if mode == "customer" else run_door)(aid, tunnel_id, say)
        autopilot._say(aid, "Done.", done=True, ok=True)

    return autopilot._start(name, work, action="speed", allow_read_only=True)
