#!/usr/bin/env python3
"""migrate_check.py <live data dir> - the two things only the panel's own machine can answer about the checked-out code, asked before the service is restarted:
1. the whole panel IMPORTS with the libraries installed here (a missing library or a typo in any module fails here, with the old service still running - the tests no longer run on the panel's server);
2. its migrations run against a COPY of the live database (a typo in a DDL, a column that already exists under another type) instead of failing at the start of the new service.
Exit 0 = the new code starts and opens the live data; anything else = do not restart."""
import os
import shutil
import sqlite3
import sys
import tempfile

live = os.path.join(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/nikatel/data", "nikatel.db")
tmp = tempfile.mkdtemp(prefix="nikatel-migrate-")

try:
    os.environ["NIKATEL_DATA_DIR"] = tmp          # (before the first import: nothing of the new code may touch the live data or the live log)
    os.environ["NIKATEL_LOG_DIR"] = tmp
    os.environ["NIKATEL_BOOTSTRAP_DIR"] = tmp

    import nikatel.main  # noqa: F401  - the import is the check

    print("the whole panel imports")

    if not os.path.exists(live):
        print("no live database yet: nothing to migrate")
        sys.exit(0)

    src = sqlite3.connect(live)
    dst = sqlite3.connect(os.path.join(tmp, "nikatel.db"))
    src.backup(dst)                      # a consistent copy, WAL included
    dst.close()
    src.close()

    from nikatel.db import init_db

    print("migrations run on a copy of the live database: schema version", init_db())
finally:
    shutil.rmtree(tmp, ignore_errors=True)
