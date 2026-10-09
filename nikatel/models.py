"""The tables. One class per thing the panel manages. JSON columns hold rarely-queried detail."""
from __future__ import annotations

import time

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


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
    ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)   # None while running
    story: Mapped[list] = mapped_column(JSON, default=list)  # the sentences shown to the operator
    finished_at: Mapped[int] = mapped_column(Integer, default=0)


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
