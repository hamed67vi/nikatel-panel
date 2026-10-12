#!/usr/bin/env python3
"""migrate_check.py <live data dir> - run the migrations of the checked-out code against a COPY of the live database, before the service is restarted.
A migration that cannot run (a typo in its DDL, a column that already exists under another type) fails here, with the old service still running,
instead of at the start of the new one. Exit 0 = the new code opens the live data; anything else = do not restart."""
import os
import shutil
import sqlite3
import sys
import tempfile

live = os.path.join(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/nikatel/data", "nikatel.db")

if not os.path.exists(live):
    print("no live database yet: nothing to check")
    sys.exit(0)

tmp = tempfile.mkdtemp(prefix="nikatel-migrate-")

try:
    src = sqlite3.connect(live)
    dst = sqlite3.connect(os.path.join(tmp, "nikatel.db"))
    src.backup(dst)                      # a consistent copy, WAL included
    dst.close()
    src.close()
    os.environ["NIKATEL_DATA_DIR"] = tmp
    os.environ["NIKATEL_LOG_DIR"] = tmp
    os.environ["NIKATEL_BOOTSTRAP_DIR"] = tmp

    from nikatel.db import init_db

    print("migrations run on a copy of the live database: schema version", init_db())
finally:
    shutil.rmtree(tmp, ignore_errors=True)
