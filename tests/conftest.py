"""Every test gets its own data dir; no test touches a real server (ssh is stubbed)."""
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="nikatel-test-")
os.environ["NIKATEL_DATA_DIR"] = _TMP + "/data"
os.environ["NIKATEL_LOG_DIR"] = _TMP + "/logs"
os.environ["NIKATEL_SSH_KEY"] = _TMP + "/no-key"
os.environ["NIKATEL_READ_ONLY"] = "true"
os.environ["NIKATEL_BOOTSTRAP_DIR"] = _TMP + "/etc"      # never the real /etc/nikatel of the machine the tests run on
os.environ["NIKATEL_LICENSE"] = "owner"                  # every feature on; test_license switches to the free tier on purpose

from nikatel import db
from nikatel.config import get_settings


@pytest.fixture(autouse=True)
def fresh_db():
    get_settings.cache_clear()
    db.reset_for_tests()
    s = get_settings()

    for f in ("nikatel.db", "nikatel.db-wal", "nikatel.db-shm", "auth.json"):
        try:
            (s.data_dir / f).unlink()
        except FileNotFoundError:
            pass

    db.init_db()

    from nikatel import auth
    from nikatel.services import gate

    auth._FAILS.clear()
    auth._RESET.clear()
    gate._CACHE.update({"at": 0.0, "path": ""})
    yield
    db.reset_for_tests()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from nikatel import auth
    from nikatel.main import create_app

    auth.set_password("test-password-1")
    app = create_app(run_jobs=False)

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"user": "admin", "password": "test-password-1"})
        assert r.status_code == 200, r.text
        yield c


# the automatic failover is paused in the product (failover.AUTOMATIC = False); its logic is still tested, so the suite runs with it on - tests of the pause switch it off themselves
from nikatel.services import failover as _failover

_failover.AUTOMATIC = True
