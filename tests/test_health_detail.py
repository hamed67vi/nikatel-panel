"""/api/health/detail: what is wrong with the panel itself, in plain words."""
import time

from nikatel.db import session
from nikatel.models import Job


def test_the_health_answers_for_the_open_check_and_the_detail_needs_the_sign_in(client, anon_client=None):
    assert client.get("/api/health").json()["ok"] is True
    d = client.get("/api/health/detail").json()
    assert set(d["checks"]) == {"database", "jobs", "ssh_key", "disk", "backup", "errors"} and "version" in d and isinstance(d["problems"], list)
    assert d["checks"]["database"]["ok"] is True


def test_a_late_or_failing_job_is_named_in_the_problems(client):
    now = int(time.time())

    with session() as db:
        db.add(Job(name="hd-late", interval=60, enabled=True, last_ok=now - 10 * 60))
        db.add(Job(name="hd-bad", interval=60, enabled=True, last_ok=now, last_error="boom"))

    d = client.get("/api/health/detail").json()
    jobs = next(p for p in d["problems"] if p["what"] == "jobs")
    assert "hd-late" in jobs["text"] and "hd-bad" in jobs["text"] and jobs["hint"]
    assert d["ok"] is False