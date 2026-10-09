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
]


def engine() -> Engine:
    global _engine, _Session

    if _engine is None:
        s = get_settings()
        s.data_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(s.db_url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)

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
