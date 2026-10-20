"""Database: SQLite through SQLAlchemy 2.0, WAL mode, numbered migrations.

Schema changes are migrations in MIGRATIONS (append only). `init_db()` creates the tables of the
current models and then applies every migration above the stored version, so an old database
upgrades in place. Keep migrations small and idempotent.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings
from .log import get_logger

log = get_logger("db")


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None

# (version, description, sql statements or callable)
MIGRATIONS: list[tuple[int, str, list[str] | Callable[[Session], None]]] = [
    (1, "initial schema", []),
    (2, "settings, alert_states, maintenance tables (created by create_all)", []),
    (3, "action_log table (created by create_all)", []),
    (4, "gateways table (created by create_all)", []),
    (5, "configs, config_samples, subscriptions tables (created by create_all)", []),
    (6, "panels table (created by create_all)", []),
    (7, "servers.extra column", lambda db: _add_column(db, "servers", "extra", "JSON DEFAULT '{}'")),
    (8, "configs.meta column", lambda db: _add_column(db, "configs", "meta", "JSON DEFAULT '{}'")),
    (9, "subscriptions.excluded column", lambda db: _add_column(db, "subscriptions", "excluded", "JSON DEFAULT '[]'")),
    (10, "alert_states flapping guard columns", lambda db: [_add_column(db, "alert_states", c, "INTEGER DEFAULT 0") for c in ("flips", "flap_since", "quiet_until")]),
    (11, "repair_cases table (created by create_all)", []),
    (12, "jobs.last_failure / last_failure_at columns", lambda db: [_add_column(db, "jobs", "last_failure", "VARCHAR(300) DEFAULT ''"), _add_column(db, "jobs", "last_failure_at", "INTEGER DEFAULT 0")]),
    (13, "api_tokens table (created by create_all)", []),
    (14, "outbound_configs table (created by create_all)", []),
    (15, "outbound_hubs table (created by create_all)", []),
    (16, "profiles table (created by create_all)", []),
    (17, "profiles.tag column + a profile for every node of a connected PasarGuard panel", lambda db: [_add_column(db, "profiles", "tag", "VARCHAR(40)"), _auto_nodes(db)]),
    (18, "the category PasarGuard is called PasarGuard Nodes", lambda db: _rename_pg_tag(db)),
    (19, "the category PasarGuard Nodes is called PasarGuard again (one PasarGuard profile)", lambda db: _rename_nodes_tag(db)),
    (20, "cfg_meters, cfg_buckets tables (created by create_all)", []),
]


def _auto_nodes(db: Session) -> None:
    from .services import profiles

    n = profiles.auto_all(db)
    log.info("profiles: %s node%s of the connected PasarGuard panels put on the Lab page", n, "" if n == 1 else "s")


def _rename_pg_tag(db: Session) -> None:
    from .services import profiles

    profiles.rename_old_tag(db)


def _rename_nodes_tag(db: Session) -> None:
    from .services import profiles

    n = profiles.rename_nodes_tag(db)
    log.info("profiles: %s profile%s moved from PasarGuard Nodes to PasarGuard", n, "" if n == 1 else "s")


def _add_column(db: Session, table: str, column: str, ddl: str) -> None:
    """ALTER TABLE ... ADD COLUMN unless create_all already made it (fresh databases)."""
    have = {row[1] for row in db.execute(text(f"PRAGMA table_info({table})"))}

    if column not in have:
        db.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def engine() -> Engine:
    global _engine, _Session

    if _engine is None:
        s = get_settings()
        s.data_dir.mkdir(parents=True, exist_ok=True)
        # the pool: ~20 job threads, the page requests (a pool of 40) and the pools inside the jobs all ask for a connection; SQLAlchemy's default (5 + 10 overflow) made the 16th wait 30 s and then fail
        _engine = create_engine(s.db_url, connect_args={"check_same_thread": False, "timeout": 30}, future=True, pool_size=20, max_overflow=40, pool_timeout=30)

        @event.listens_for(_engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)

    return _engine


@contextmanager
def session() -> Iterator[Session]:
    """`with session() as db:` - commits on success, rolls back on error."""
    engine()
    assert _Session is not None
    db = _Session()

    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> int:
    """Create tables, apply migrations. Returns the schema version."""
    from . import models  # noqa: F401  (registers the tables)

    e = engine()
    Base.metadata.create_all(e)

    with e.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"))
        row = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
        current = int(row or 0)

    for version, desc, steps in MIGRATIONS:
        if version <= current:
            continue

        log.info("migration %s: %s", version, desc)

        with session() as db:
            if callable(steps):
                steps(db)
            else:
                for stmt in steps:
                    db.execute(text(stmt))

            db.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": version})

        current = version

    return current


def reset_for_tests() -> None:
    """Forget the engine so a test can point data_dir somewhere else."""
    global _engine, _Session

    if _engine is not None:
        _engine.dispose()

    _engine = None
    _Session = None
