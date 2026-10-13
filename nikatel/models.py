"""The tables. One class per thing the panel manages. JSON columns hold rarely-queried detail."""
from __future__ import annotations

import time

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def is_temp(name: str) -> bool:
    """A temporary test tunnel (built by a tunnel test or by the smart repair to measure a candidate, removed again): never listed, never checked, never repaired."""
    return str(name).startswith("test-")


def now() -> int:
    return int(time.time())


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    host: Mapped[str] = mapped_column(String(128), unique=True)
    role: Mapped[str] = mapped_column(String(16))          # iran | foreign
    ssh_user: Mapped[str] = mapped_column(String(32), default="root")
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    provider: Mapped[str] = mapped_column(String(128), default="")
    asn: Mapped[str] = mapped_column(String(32), default="")
    country: Mapped[str] = mapped_column(String(8), default="")
    created_at: Mapped[int] = mapped_column(Integer, default=now)

    # live state (written by the sampler)
    online: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_seen_at: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    facts: Mapped[dict] = mapped_column(JSON, default=dict)   # latest sample (cpu, ram, disk, load, rx, tx ...)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)   # abuse defender, Iran filter check, geo, hostname/system

    samples: Mapped[list[ServerSample]] = relationship(back_populates="server", cascade="all, delete-orphan")


class ServerSample(Base):
    __tablename__ = "server_samples"
    __table_args__ = (Index("ix_server_samples_server_ts", "server_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    ts: Mapped[int] = mapped_column(Integer)
    cpu: Mapped[float] = mapped_column(Float)
    ram: Mapped[float] = mapped_column(Float)
    disk: Mapped[float] = mapped_column(Float)
    load1: Mapped[float] = mapped_column(Float)
    rx_mbps: Mapped[float] = mapped_column(Float)
    tx_mbps: Mapped[float] = mapped_column(Float)
    conn: Mapped[int] = mapped_column(Integer)

    server: Mapped[Server] = relationship(back_populates="samples")


class Tunnel(Base):
    __tablename__ = "tunnels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(16))             # backhaul | backpack | paqet | chisel | gre
    transport: Mapped[str] = mapped_column(String(16), default="")
    engine: Mapped[str] = mapped_column(String(32), default="")
    iran_server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"))
    foreign_server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"))
    port: Mapped[int] = mapped_column(Integer, default=0)
    iran_tunnel_ip: Mapped[str] = mapped_column(String(64), default="")
    foreign_tunnel_ip: Mapped[str] = mapped_column(String(64), default="")
    forwarded_ports: Mapped[list] = mapped_column(JSON, default=list)
    record: Mapped[dict] = mapped_column(JSON, default=dict)      # the rest of the imported record (no secrets)
    secrets: Mapped[dict] = mapped_column(JSON, default=dict)     # token, test_key, gre_key - never leaves the API
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now)

    # live state (written by the checker)
    state: Mapped[str] = mapped_column(String(16), default="unknown")   # unknown | up | degraded | down
    state_since: Mapped[int] = mapped_column(Integer, default=0)
    last_check_at: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)          # unit states, iface, ports of the last check

    iran: Mapped[Server] = relationship(foreign_keys=[iran_server_id])
    foreign: Mapped[Server] = relationship(foreign_keys=[foreign_server_id])
    checks: Mapped[list[TunnelCheck]] = relationship(back_populates="tunnel", cascade="all, delete-orphan")


class TunnelCheck(Base):
    __tablename__ = "tunnel_checks"
    __table_args__ = (Index("ix_tunnel_checks_tunnel_ts", "tunnel_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tunnel_id: Mapped[int] = mapped_column(ForeignKey("tunnels.id", ondelete="CASCADE"))
    ts: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)
    rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str] = mapped_column(String(300), default="")

    tunnel: Mapped[Tunnel] = relationship(back_populates="checks")


class Event(Base):
    """What the operator reads on the dashboard: one plain sentence per thing that happened."""
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_ts", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[int] = mapped_column(Integer, default=now)
    level: Mapped[str] = mapped_column(String(8))         # ok | info | warn | bad
    domain: Mapped[str] = mapped_column(String(16))       # servers | tunnels | jobs | auth | system
    text: Mapped[str] = mapped_column(Text)
    ref: Mapped[str] = mapped_column(String(64), default="")   # the server / tunnel name it is about


class Setting(Base):
    """Editable panel settings and small markers: one JSON document per key (see services/settings.py)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class AlertState(Base):
    """Flapping control per thing: how many bad checks in a row, whether we already told, since when."""
    __tablename__ = "alert_states"
    __table_args__ = (Index("ix_alert_states_kind_ref", "kind", "ref", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))        # tunnel | server
    ref: Mapped[str] = mapped_column(String(64))
    fails: Mapped[int] = mapped_column(Integer, default=0)
    alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    bad_since: Mapped[int] = mapped_column(Integer, default=0)
    good_since: Mapped[int] = mapped_column(Integer, default=0)
    last_notice: Mapped[int] = mapped_column(Integer, default=0)
    flips: Mapped[int] = mapped_column(Integer, default=0)          # DOWNs inside the current flapping window
    flap_since: Mapped[int] = mapped_column(Integer, default=0)
    quiet_until: Mapped[int] = mapped_column(Integer, default=0)    # flapping guard: nothing is said until then


class Maintenance(Base):
    """A window in which alerts are not sent (state tracking continues)."""
    __tablename__ = "maintenance"

    id: Mapped[int] = mapped_column(primary_key=True)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(120), default="")


class ActionLog(Base):
    """Every repair the panel performed or planned: who/what/where, the exact steps, the outcome."""
    __tablename__ = "action_log"
    __table_args__ = (Index("ix_action_log_ref_ts", "ref", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[int] = mapped_column(Integer, default=now)
    ref: Mapped[str] = mapped_column(String(64))             # tunnel name
    action: Mapped[str] = mapped_column(String(32))          # fix | restart_units | restart_gre
    source: Mapped[str] = mapped_column(String(16))          # manual | auto
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)   # None while running
    story: Mapped[list] = mapped_column(JSON, default=list)  # the sentences shown to the operator
    finished_at: Mapped[int] = mapped_column(Integer, default=0)


class RepairCase(Base):
    """One run of the smart repair, kept as DATA (not only as a story): what was wrong, which steps were tried in which order, which one healed it.
    The analyst learns from these rows (services/analyst.py)."""
    __tablename__ = "repair_cases"
    __table_args__ = (Index("ix_repair_cases_tunnel_ts", "tunnel", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[int] = mapped_column(Integer, default=now)
    tunnel: Mapped[str] = mapped_column(String(64))
    iran: Mapped[str] = mapped_column(String(64), default="")
    foreign: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(24), default="")           # tcpmux, bp:tcpmux, paqet ...
    cause: Mapped[str] = mapped_column(String(24), default="")          # units_down, route_cut, gre_blocked, lossy_route, port_blocked, unknown
    steps: Mapped[list] = mapped_column(JSON, default=list)             # [{"step": "restart", "ok": false, "secs": 31}, ...] in the order they were tried
    healed: Mapped[bool] = mapped_column(Boolean, default=False)
    secs: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="")         # manual | auto


class Gateway(Base):
    """The Xray gateway of one Iran server: ports -> foreign nodes, borrowed configs, mode."""
    __tablename__ = "gateways"

    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="auto")          # auto | borrowed | direct
    ports: Mapped[list] = mapped_column(JSON, default=list)                 # [{port, target_host, target_port, udp, enabled, note}]
    borrowed: Mapped[list] = mapped_column(JSON, default=list)              # [{id, uri, note, enabled}]  (uri is a secret)
    installed: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_at: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(64), default="")

    server: Mapped[Server] = relationship()


class Config(Base):
    """A client config the probe keeps alive and tests (one xray per config on the probe)."""
    __tablename__ = "configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    slug: Mapped[str] = mapped_column(String(64), default="")
    group: Mapped[str] = mapped_column(String(64), default="")          # subscription / group name
    port: Mapped[int] = mapped_column(Integer, default=0)               # SOCKS port on the probe
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tunnel: Mapped[str] = mapped_column(String(80), default="")         # free label: which tunnel it rides
    cdn_port: Mapped[str] = mapped_column(String(8), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)              # read from the link: proto, address, port, network, security (no secrets)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # up | down | unknown
    status_since: Mapped[int] = mapped_column(Integer, default=0)
    ping_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    down_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    up_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_at: Mapped[str] = mapped_column(String(40), default="")
    checked_at: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class ConfigSample(Base):
    __tablename__ = "config_samples"
    __table_args__ = (Index("ix_config_samples_config_ts", "config_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("configs.id", ondelete="CASCADE"))
    ts: Mapped[int] = mapped_column(Integer)
    up: Mapped[bool] = mapped_column(Boolean)
    ping_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class Subscription(Base):
    """A subscription link whose nodes were imported as configs (the group)."""
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(Text)                              # secret-ish: never listed in clear
    nodes: Mapped[list] = mapped_column(JSON, default=list)             # imported node names, in the order of the subscription
    excluded: Mapped[list] = mapped_column(JSON, default=list)          # node names the operator unticked: a sync never adds them (and removes them when present)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    last_import: Mapped[int] = mapped_column(Integer, default=0)


class Panel(Base):
    """A Marzban or PasarGuard panel the operator manages: login, last check, nodes."""
    __tablename__ = "panels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    type: Mapped[str] = mapped_column(String(16))                        # marzban | pasarguard
    url: Mapped[str] = mapped_column(String(200))
    username: Mapped[str] = mapped_column(String(64), default="")
    secrets: Mapped[dict] = mapped_column(JSON, default=dict)            # {"password": ...} - never listed
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")   # up | down | unknown
    status_since: Mapped[int] = mapped_column(Integer, default=0)
    fails: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[str] = mapped_column(String(40), default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)              # users_total / active / online, cpu, mem_pct, bw
    nodes: Mapped[list] = mapped_column(JSON, default=list)              # [{id, name, address, status, xray, message, fails}]
    last_error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class OutboundConfig(Base):
    """An exit of the outbound pool (services/outbound.py): a vless / vmess / trojan / ss link that still has internet. The link is a secret: never listed."""
    __tablename__ = "outbound_configs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    uri: Mapped[str] = mapped_column(String(3000))
    note: Mapped[str] = mapped_column(String(60), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class OutboundHub(Base):
    """A foreign server that runs the relay of the outbound (services/outbound.py): the Iran gateway wraps the traffic in Shadowsocks, sends it through an exit, and the relay unwraps it
    and dials the node. Without it an exit that sniffs the Host / SNI of the inner traffic dials that NAME instead of the node. The password is a secret: never listed."""
    __tablename__ = "outbound_hubs"

    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    port: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(32), default="chacha20-ietf-poly1305")
    password: Mapped[str] = mapped_column(String(64))
    installed: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_at: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(64), default="")

    server: Mapped[Server] = relationship()


class ApiToken(Base):
    """The sign-in of a machine (docs/api.md section 3): only the hash of the token is kept; the token itself is shown once when it is made."""
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(8))                        # read | act | admin
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(12), default="")          # the first characters, so the operator can tell tokens apart
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    last_used_at: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[int] = mapped_column(Integer, default=0)


class Job(Base):
    """Status of every background job, so nothing fails silently."""
    __tablename__ = "jobs"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    interval: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    last_start: Mapped[int] = mapped_column(Integer, default=0)
    last_ok: Mapped[int] = mapped_column(Integer, default=0)
    last_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    runs: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_failure: Mapped[str] = mapped_column(String(300), default="")      # the last failure is kept even after later runs succeed (last_error is cleared by a success)
    last_failure_at: Mapped[int] = mapped_column(Integer, default=0)
