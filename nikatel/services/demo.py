"""The demo: a read-only copy of the panel filled with INVENTED servers, tunnels and configs, so anyone can look around and give an opinion.

Nothing here is real. Names are made up, addresses come from the documentation ranges (RFC 5737), there is no key and no way to reach a machine.
Switched on with NIKATEL_DEMO=true: the panel shows itself without a sign-in, every change (a POST, PUT, DELETE) is refused, and one small job keeps the numbers moving
so the pages look alive. `seed()` fills an empty database (run it once: `nikatel demo-seed`)."""
from __future__ import annotations

import math
import random
import time

from sqlalchemy import delete, func, select

from ..db import session
from ..models import (
    ActionLog,
    Config,
    ConfigSample,
    Event,
    Gateway,
    Job,
    Panel,
    Server,
    ServerSample,
    Tunnel,
    TunnelCheck,
)

# name, host, role, country, city, provider, cores, base cpu %, base ram %, base Mbit/s
SERVERS = [
    ("ir-north-1", "198.51.100.11", "iran", "IR", "Tehran", "Demo Telecom A", 4, 46, 58, 120),
    ("ir-north-2", "198.51.100.12", "iran", "IR", "Tehran", "Demo Telecom A", 4, 91, 71, 310),
    ("ir-east-1", "198.51.100.21", "iran", "IR", "Mashhad", "Demo Telecom B", 2, 34, 49, 70),
    ("ir-west-1", "198.51.100.31", "iran", "IR", "Tabriz", "Demo Telecom C", 4, 52, 62, 140),
    ("ir-south-1", "198.51.100.41", "iran", "IR", "Shiraz", "Demo Telecom B", 2, 28, 44, 45),
    ("ir-centre-1", "198.51.100.51", "iran", "IR", "Isfahan", "Demo Telecom D", 8, 38, 55, 210),
    ("de-edge-1", "203.0.113.11", "foreign", "DE", "Frankfurt", "Demo Cloud A", 2, 22, 41, 85),
    ("de-edge-2", "203.0.113.12", "foreign", "DE", "Frankfurt", "Demo Cloud A", 2, 12, 33, 30),
    ("nl-edge-1", "203.0.113.21", "foreign", "NL", "Amsterdam", "Demo Cloud B", 2, 31, 47, 95),
    ("fi-edge-1", "203.0.113.31", "foreign", "FI", "Helsinki", "Demo Cloud C", 1, 40, 60, 60),
    ("tr-edge-1", "203.0.113.41", "foreign", "TR", "Istanbul", "Demo Cloud D", 2, 47, 52, 150),
    ("uk-edge-1", "203.0.113.51", "foreign", "GB", "London", "Demo Cloud B", 1, 18, 38, 25),
    ("us-edge-1", "203.0.113.61", "foreign", "US", "New York", "Demo Cloud E", 4, 25, 36, 110),
    ("ae-edge-1", "203.0.113.71", "foreign", "AE", "Dubai", "Demo Cloud F", 2, 0, 0, 0),         # off: shows how a cut server looks
]

# name, kind, transport, iran, foreign, port, ports, state, rtt
TUNNELS = [
    ("de1-north1", "backhaul", "tcpmux", "ir-north-1", "de-edge-1", 8443, [443, 2053], "up", 44),
    ("de1-north2", "backhaul", "tcpmux", "ir-north-2", "de-edge-1", 8444, [443, 8443], "up", 48),
    ("de2-centre", "backhaul", "wsmux", "ir-centre-1", "de-edge-2", 8445, [443], "up", 52),
    ("nl1-west", "backhaul", "tcpmux", "ir-west-1", "nl-edge-1", 8446, [443, 2083], "up", 61),
    ("nl1-north2", "backpack", "", "ir-north-2", "nl-edge-1", 8447, [8080], "degraded", 118),
    ("fi1-east", "backhaul", "tcp", "ir-east-1", "fi-edge-1", 8448, [443], "up", 73),
    ("tr1-north1", "paqet", "", "ir-north-1", "tr-edge-1", 8449, [443, 8880], "up", 31),
    ("tr1-centre", "chisel", "", "ir-centre-1", "tr-edge-1", 8450, [2096], "up", 35),
    ("uk1-south", "backhaul", "tcpmux", "ir-south-1", "uk-edge-1", 8451, [443], "up", 83),
    ("us1-west", "backhaul", "wssmux", "ir-west-1", "us-edge-1", 8452, [443, 2087], "up", 142),
    ("us1-north2", "gre", "", "ir-north-2", "us-edge-1", 0, [8443], "up", 139),
    ("ae1-north1", "backhaul", "tcpmux", "ir-north-1", "ae-edge-1", 8453, [443], "down", None),
]

CONFIGS = [
    ("Fast-DE", "vless", "203.0.113.11", 443, "tcp", "reality", "up", 188, 41.0, 18.2, "de1-north1"),
    ("Fast-DE-2", "vless", "203.0.113.12", 443, "tcp", "reality", "up", 205, 38.4, 16.0, "de2-centre"),
    ("Calm-NL", "vless", "203.0.113.21", 443, "ws", "tls", "up", 232, 36.1, 15.1, "nl1-west"),
    ("Ruby-FI", "trojan", "203.0.113.31", 443, "tcp", "tls", "up", 310, 22.7, 9.4, "fi1-east"),
    ("Istanbul", "vless", "203.0.113.41", 8880, "tcp", "none", "up", 120, 62.3, 27.8, "tr1-north1"),
    ("London", "vmess", "203.0.113.51", 443, "ws", "tls", "up", 345, 14.2, 6.1, "uk1-south"),
    ("NY-Direct", "vless", "203.0.113.61", 443, "grpc", "reality", "up", 410, 19.9, 8.8, "us1-west"),
    ("Dubai", "vless", "203.0.113.71", 443, "tcp", "reality", "down", None, None, None, "ae1-north1"),
]

EVENTS = [
    ("ok", "tunnels", "Tunnel us1-north2 works again.", "us1-north2", 55),
    ("warn", "tunnels", "Tunnel nl1-north2 is degraded: 18 of 100 pings lost.", "nl1-north2", 38),
    ("bad", "servers", "Server ae-edge-1 does not answer: connection timed out.", "ae-edge-1", 190),
    ("bad", "tunnels", "Tunnel ae1-north1 is down: the foreign side does not answer.", "ae1-north1", 188),
    ("info", "system", "Smart repair restarted the tunnel units of de1-north2 (the link came back in 9 s).", "de1-north2", 700),
    ("ok", "servers", "Server ir-south-1 is back online.", "ir-south-1", 2400),
    ("warn", "servers", "Server ir-north-2: the CPU stays above 90 % - the Doctor turned on packet steering (RPS) on its network card.", "ir-north-2", 3100),
    ("info", "system", "Daily backup finished (encrypted, 412 KB).", "", 9000),
    ("ok", "tunnels", "Tunnel tr1-north1 works again.", "tr1-north1", 15000),
    ("info", "system", "The tunnel engines are up to date.", "", 30000),
]


def _wave(ts: float, base: float, amp: float, seed: int) -> float:
    """A day-shaped curve: quiet at night, busy in the evening, with a little noise that is the same for the same time."""
    hour = (ts % 86400) / 3600.0
    day = 0.5 + 0.5 * math.sin((hour - 14) / 24 * 2 * math.pi)         # peak around 20:00
    noise = random.Random(int(ts // 120) * 31 + seed).uniform(-1, 1)
    return max(0.0, base * (0.62 + 0.55 * day) + noise * amp)


def _facts(row: tuple, ts: float, seed: int, uptime: int) -> dict:
    name, _h, role, _c, _city, _p, cores, cpu0, ram0, mbps0 = row
    cpu = min(99.0, _wave(ts, cpu0, 4, seed)) if cpu0 else 0.0
    ram = min(96.0, ram0 + random.Random(int(ts // 600) + seed).uniform(-2, 2)) if ram0 else 0.0
    mbps = _wave(ts, mbps0, mbps0 * 0.08, seed + 1) if mbps0 else 0.0
    total_mb = {1: 1024, 2: 2048, 4: 4096, 8: 8192}.get(cores, 2048)
    return {
        "ts": int(ts), "cpu": round(cpu, 1), "ram": round(ram, 1), "ram_used_mb": int(total_mb * ram / 100), "ram_total_mb": total_mb, "swap": 0.0,
        "disk": 28.0 + seed % 17, "disk_used_gb": round(0.4 * (28 + seed % 17), 1), "disk_total_gb": 40.0,
        "load1": round(cores * cpu / 100 * 0.9, 2), "load5": round(cores * cpu / 100 * 0.85, 2), "load15": round(cores * cpu / 100 * 0.8, 2),
        "cores": cores, "uptime": uptime, "rx_mbps": round(mbps * 0.62, 2), "tx_mbps": round(mbps * 0.38, 2),
        "conn": int(mbps * 11 + 40), "hostname": name, "kernel": "5.15.0-demo",
        "top": f"{'backhaul' if role == 'iran' else 'xray'} {min(98, cpu * 0.8):.0f}%" if cpu else "", "abuse_rules": 0 if role == "iran" else 214,
    }


def seed() -> str:
    """Fill an EMPTY database. Does nothing when servers already exist (so a real database is never touched)."""
    now = int(time.time())

    with session() as db:
        if db.scalar(select(func.count()).select_from(Server)):
            return "the database is not empty - nothing done"

        by_name: dict[str, Server] = {}

        for i, row in enumerate(SERVERS):
            name, host, role, country, city, provider, _cores, cpu0, _r, _m = row
            off = cpu0 == 0
            s = Server(name=name, host=host, role=role, country=country, provider=provider, asn=f"AS{64500 + i}", enabled=True, online=not off,
                       last_seen_at=now - (190 * 60 if off else 5), last_error="connection timed out" if off else "",
                       facts={} if off else _facts(row, now, i * 7, 86400 * (9 + i)),
                       extra={"geo": {"city": city, "provider": provider, "domain": "example.test", "asn": f"AS{64500 + i}"}, "hostname": name}, created_at=now - 86400 * (40 + i))
            db.add(s)
            by_name[name] = s

        db.flush()

        for i, row in enumerate(SERVERS):
            if row[7] == 0:
                continue

            for k in range(48 * 6, -1, -1):              # 48 h, one sample every 10 minutes
                ts = now - k * 600
                f = _facts(row, ts, i * 7, 86400 * (9 + i))
                db.add(ServerSample(server_id=by_name[row[0]].id, ts=ts, cpu=f["cpu"], ram=f["ram"], disk=f["disk"], load1=f["load1"], rx_mbps=f["rx_mbps"], tx_mbps=f["tx_mbps"], conn=f["conn"]))

        for j, (name, kind, tr, iran, foreign, port, ports, state, rtt) in enumerate(TUNNELS):
            ok = state in ("up", "degraded")
            t = Tunnel(name=name, type=kind, transport=tr, engine=kind, iran_server_id=by_name[iran].id, foreign_server_id=by_name[foreign].id, port=port,
                       iran_tunnel_ip=f"10.77.{j}.1" if kind == "gre" else "", foreign_tunnel_ip=f"10.77.{j}.2" if kind == "gre" else "", forwarded_ports=ports,
                       record={"type": kind, "transport": tr, "network": f"10.77.{j}.0/30"} if kind == "gre" else {"type": kind, "transport": tr},
                       secrets={}, enabled=True, created_at=now - 86400 * (30 - j), state=state, state_since=now - (190 * 60 if state == "down" else 3600 * (5 + j)),
                       last_check_at=now - 20, last_error={"down": "the foreign side does not answer", "degraded": "18 of 100 pings lost"}.get(state, ""), rtt_ms=rtt,
                       rx_mbps=round(random.Random(j).uniform(3, 40), 2) if ok else None, tx_mbps=round(random.Random(j + 50).uniform(1, 15), 2) if ok else None,
                       detail={"iran_units": {f"{kind}-iran-{port or 'gre'}.service": "active" if ok else "failed"}, "foreign_units": {f"{kind}-foreign-{port or 'gre'}.service": "active" if ok else "inactive"},
                               "iface": "up" if ok else "down", "loss_pct": 18 if state == "degraded" else (100 if state == "down" else 0), "listen": {str(p): ok for p in ports},
                               "data_seen": ok, "data_fails": 0 if ok else 5, "data_code": "200" if ok else "000", "data_ms": rtt, "streak": 0})
            db.add(t)
            db.flush()

            for k in range(48, -1, -1):                   # a day of checks, one every 30 minutes
                bad = (state == "down" and k < 6) or (state == "degraded" and k < 4)
                db.add(TunnelCheck(tunnel_id=t.id, ts=now - k * 1800, ok=not bad, rtt_ms=None if bad and state == "down" else (rtt or 0) + random.Random(k + j).uniform(-4, 6),
                                   rx_mbps=round(random.Random(k).uniform(2, 30), 2), tx_mbps=round(random.Random(k + 9).uniform(1, 12), 2), error="" if not bad else "lost pings"))

        for i, (name, proto, addr, port, net, sec, status, ping, down, up, tunnel) in enumerate(CONFIGS):
            c = Config(name=name, slug=name.lower(), group="Demo subscription", port=10800 + i, enabled=True, tunnel=tunnel, cdn_port="",
                       meta={"proto": proto, "address": addr, "port": port, "network": net, "security": sec}, status=status, status_since=now - 3600 * (3 + i),
                       ping_ms=ping, down_mbps=down, up_mbps=up, speed_at="today", checked_at=now - 30, last_error="" if status == "up" else "the port does not open", created_at=now - 86400 * 20)
            db.add(c)
            db.flush()

            for k in range(72, -1, -1):
                db.add(ConfigSample(config_id=c.id, ts=now - k * 1200, up=not (status == "down" and k < 9), ping_ms=None if (status == "down" and k < 9) else (ping or 0) + random.Random(k + i).uniform(-12, 18)))

        db.add(Panel(name="Demo panel", type="marzban", url="https://panel.example.test", username="demo", secrets={}, enabled=True, status="up", status_since=now - 86400, checked_at=now - 10, latency_ms=64,
                     version="0.8.0", stats={"users_total": 1840, "users_active": 1215, "users_online": 143, "cpu": 21, "mem_pct": 48, "bw": 83},
                     nodes=[{"id": 1, "name": "de-edge-1", "address": "203.0.113.11", "status": "connected", "xray": "25.3.6", "message": "", "fails": 0},
                            {"id": 2, "name": "nl-edge-1", "address": "203.0.113.21", "status": "connected", "xray": "25.3.6", "message": "", "fails": 0},
                            {"id": 3, "name": "ae-edge-1", "address": "203.0.113.71", "status": "error", "xray": "", "message": "connection timed out", "fails": 7}], created_at=now - 86400 * 30))

        for level, domain, text, ref, ago in EVENTS:
            db.add(Event(ts=now - ago * 60, level=level, domain=domain, text=text, ref=ref))

        db.add(ActionLog(ts=now - 700 * 60, ref="de1-north2", action="fix", source="auto", dry_run=False, ok=True, finished_at=now - 700 * 60 + 14,
                         story=["Starting…", "de1-north2: the tunnel unit on the Iran side stopped answering.", "Restarted the units on both sides.", "The link answers again after 9 s.", "Done."]))
        db.add(ActionLog(ts=now - 3100 * 60, ref="cpu ir-north-2", action="balance", source="auto", dry_run=False, ok=True, finished_at=now - 3100 * 60 + 31,
                         story=["Starting…", "ir-north-2: 4 cores - one core processes 97 % of the network packets and is 88 % busy while the other 3 are 24 % busy.",
                                "Packet steering (RPS) turned on for the network card.", "After: busiest core 60 % (was 88 %), all cores on average 31 % (was 40 %).", "Done."]))
        db.add(ActionLog(ts=now - 190 * 60, ref="ae1-north1", action="fix", source="auto", dry_run=False, ok=False, finished_at=now - 190 * 60 + 40,
                         story=["Starting…", "ae1-north1: the foreign server does not answer on SSH or on the tunnel port.", "Nothing can be restarted from here: the machine itself is off.", "Left for the operator."]))
        db.add(Gateway(server_id=by_name["ir-north-1"].id, mode="auto", ports=[], borrowed=[], installed=False))

        for name, interval in (("servers", 120), ("pulse", 5), ("tunnels", 60), ("tunnels-fast", 8), ("repair", 20), ("cpu-balance", 600), ("configs", 120), ("panels", 30), ("backup", 300), ("alerts", 60), ("demo", 30)):
            db.add(Job(name=name, interval=interval, enabled=True, running=False, last_start=now - 5, last_ok=now - 5, last_duration_ms=120, runs=1000, failures=0))

    return "demo data created"


def tick() -> None:
    """Keeps the numbers moving (every 30 s): the live facts, one sample per ten minutes, the tunnel check time. Nothing leaves this machine."""
    now = int(time.time())
    rows = {r[0]: (i, r) for i, r in enumerate(SERVERS)}

    with session() as db:
        for s in db.scalars(select(Server)):
            if s.name not in rows:
                continue

            i, row = rows[s.name]

            if row[7] == 0:
                s.last_seen_at = now - 190 * 60
                continue

            f = _facts(row, now, i * 7, int(s.facts.get("uptime", 86400)) + 30 if s.facts else 86400)
            s.facts, s.last_seen_at, s.online = f, now, True
            last = db.scalar(select(func.max(ServerSample.ts)).where(ServerSample.server_id == s.id)) or 0

            if now - last >= 600:
                db.add(ServerSample(server_id=s.id, ts=now, cpu=f["cpu"], ram=f["ram"], disk=f["disk"], load1=f["load1"], rx_mbps=f["rx_mbps"], tx_mbps=f["tx_mbps"], conn=f["conn"]))

        for t in db.scalars(select(Tunnel)):
            t.last_check_at = now - 5

            if t.state != "down":
                t.rx_mbps = round(max(0.5, (t.rx_mbps or 10) + random.uniform(-2, 2)), 2)
                t.tx_mbps = round(max(0.2, (t.tx_mbps or 4) + random.uniform(-1, 1)), 2)

        for c in db.scalars(select(Config)):
            c.checked_at = now - 8

        for j in db.scalars(select(Job)):
            j.last_start = j.last_ok = now - 3

        db.execute(delete(ServerSample).where(ServerSample.ts < now - 3 * 86400))
