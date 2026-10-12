"""Upgrade in place: a database made by the PREVIOUS release opens with the current code, every migration runs, the new tables and columns are there.
This is the test that catches a migration that works on a fresh database but not on a real one (1.25.4 shipped one). It needs the git history with
its tags (CI checks out with fetch-depth 0; the panel server is a git checkout); elsewhere it is skipped."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nikatel import __version__
from nikatel.db import MIGRATIONS

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=False, capture_output=True, text=True)


def test_a_database_of_the_previous_release_upgrades_in_place(tmp_path):
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")

    tags = _git("tag", "--sort=-v:refname")
    prev = next((t for t in tags.stdout.split() if t.lstrip("v") != __version__), None) if tags.returncode == 0 else None

    if not prev:
        pytest.skip("no earlier tag in this checkout")

    old = tmp_path / "old"
    old.mkdir()
    r = subprocess.run(f"git -C '{ROOT}' archive {prev} nikatel | tar -x -C '{old}'", shell=True, check=False, capture_output=True, text=True)

    if r.returncode != 0 or not (old / "nikatel" / "db.py").exists():
        pytest.skip(f"could not unpack {prev}: {r.stderr[:200]}")

    data = tmp_path / "data"
    data.mkdir()
    env = {**os.environ, "NIKATEL_DATA_DIR": str(data), "NIKATEL_LOG_DIR": str(tmp_path / "logs"), "NIKATEL_BOOTSTRAP_DIR": str(tmp_path / "etc"), "NIKATEL_LICENSE": "owner"}

    # 1. the previous release makes its database
    env["PYTHONPATH"] = str(old)
    r1 = subprocess.run([sys.executable, "-c", "from nikatel.db import init_db; print(init_db())"], env=env, check=False, capture_output=True, text=True, cwd=str(old))
    assert r1.returncode == 0, f"{prev} could not make its database: {r1.stderr[-600:]}"
    old_version = int(r1.stdout.strip().splitlines()[-1])

    # 2. the current code opens it: the migrations above the old version run, the schema is complete
    env["PYTHONPATH"] = str(ROOT)
    check = ("import os, sqlite3\n"
             "from nikatel.db import init_db, MIGRATIONS\n"
             "v = init_db()\n"
             "assert v == MIGRATIONS[-1][0], (v, MIGRATIONS[-1][0])\n"
             "c = sqlite3.connect(os.environ['NIKATEL_DATA_DIR'] + '/nikatel.db')\n"
             "tabs = {r[0] for r in c.execute(\"select name from sqlite_master where type='table'\")}\n"
             "for t in ('api_tokens', 'repair_cases', 'jobs', 'servers', 'tunnels'):\n"
             "    assert t in tabs, (t, sorted(tabs))\n"
             "cols = {r[1] for r in c.execute('pragma table_info(jobs)')}\n"
             "assert {'last_failure', 'last_failure_at'} <= cols, cols\n"
             "print('upgraded to', v)\n")
    r2 = subprocess.run([sys.executable, "-c", check], env=env, check=False, capture_output=True, text=True, cwd=str(ROOT))
    assert r2.returncode == 0, f"upgrade from {prev} (schema {old_version}) failed: {r2.stderr[-900:]}"
    assert old_version <= MIGRATIONS[-1][0]
