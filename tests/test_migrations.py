"""Migrations: every ALTER TABLE a migration can run is run for real against a table that lacks the column, so a typo in the DDL fails here and not on the
live panel at start (1.25.4 shipped `DEFAULT '` once and the service could not start)."""
import re
from pathlib import Path

from sqlalchemy import text

from nikatel import db as dbmod
from nikatel.db import MIGRATIONS, session


def test_every_add_column_ddl_runs_on_a_table_without_the_column():
    src = (Path(dbmod.__file__)).read_text(encoding="utf-8")
    calls = re.findall(r'_add_column\(db, "([a-z_]+)", (?:c|"([a-z_]+)"), "([^"]+)"\)', src)
    assert calls, "no _add_column call found in db.py"
    loops = re.findall(r'_add_column\(db, "([a-z_]+)", c, "([^"]+)"\) for c in \(([^)]+)\)', src)

    with session() as s:
        for i, (table, col, ddl) in enumerate(calls):
            if not col:
                continue                                              # the looped form is covered below

            tmp = f"_mig_{i}"
            s.execute(text(f"CREATE TABLE {tmp} (id INTEGER PRIMARY KEY)"))
            s.execute(text(f"ALTER TABLE {tmp} ADD COLUMN {col} {ddl}"))
            s.execute(text(f"DROP TABLE {tmp}"))

        for i, (table, ddl, cols) in enumerate(loops):
            for col in re.findall(r'"([a-z_]+)"', cols):
                tmp = f"_migl_{i}_{col}"
                s.execute(text(f"CREATE TABLE {tmp} (id INTEGER PRIMARY KEY)"))
                s.execute(text(f"ALTER TABLE {tmp} ADD COLUMN {col} {ddl}"))
                s.execute(text(f"DROP TABLE {tmp}"))


def test_migrations_are_numbered_without_gaps_and_the_last_is_the_schema_version():
    numbers = [m[0] for m in MIGRATIONS]
    assert numbers == list(range(1, len(numbers) + 1))

    with session() as s:
        assert s.execute(text("SELECT MAX(version) FROM schema_version")).scalar() == numbers[-1]


def test_add_column_is_idempotent():
    """Running a migration twice (a restart in the middle) must not fail on a column that is already there."""
    with session() as s:
        dbmod._add_column(s, "jobs", "last_failure", "VARCHAR(300) DEFAULT ''")
        dbmod._add_column(s, "jobs", "last_failure", "VARCHAR(300) DEFAULT ''")


def test_the_connection_pool_is_big_enough_for_the_jobs_and_the_pages():
    """Review 2026-10-16: SQLAlchemy's default (5 + 10) let the 16th thread wait 30 s and fail while slow jobs held their connections."""
    from nikatel import db

    pool = db.engine().pool
    assert pool.size() == 20 and pool._max_overflow == 40
