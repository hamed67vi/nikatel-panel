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

    from nikatel import auth, gate

    auth._FAILS.clear()
    auth._RESET.clear()
    gate._CACHE.update({"at": 0.0, "path": ""})
    yield
    db.reset_for_tests()


@pytest.fixture(autouse=True)
def _no_type_test_in_moves(monkeypatch):
    """A moved tunnel's type is chosen by a test on the new pair (failover._best_kind): that needs SSH and minutes - the tests that are not about it build the type the tunnel has."""
    from nikatel.services import failover

    monkeypatch.setattr(failover, "PICK_BEST", False)


@pytest.fixture(autouse=True)
def _no_type_test_after_a_drop(monkeypatch):
    """After a drop the Doctor tests other types between the pair's servers (bandwidth._swap_after_drop): that needs SSH and minutes - only the tests that are about it turn it on."""
    from nikatel.services import bandwidth

    monkeypatch.setattr(bandwidth, "DROP_SWAP", False)


@pytest.fixture(autouse=True)
def _no_review_after_a_repair(monkeypatch):
    """After a smart repair the Reviewer reads what happened in a thread of its own (services/reviewer.py): a thread that writes settings after the test that started the repair is over is not wanted - only tests/test_reviewer.py turns it on."""
    from nikatel.services import reviewer

    monkeypatch.setattr(reviewer, "AUTO", False)


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


# the Doctor's mode decides whether the failover moves anything (services/doctor.py); the tests set the mode they need
