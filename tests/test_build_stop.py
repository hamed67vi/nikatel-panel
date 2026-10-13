"""Stop on a build that runs: the build is told to stop (it rolls back what it made); a build that is not running cannot be stopped."""
import time

from nikatel.db import session
from nikatel.models import ActionLog
from nikatel.services import builder


def _action(action: str, finished: bool) -> int:
    with session() as db:
        e = ActionLog(ref="bs-test", action=action, source="manual", story=["Starting…"], finished_at=int(time.time()) if finished else 0)
        db.add(e)
        db.flush()
        return e.id


def test_a_running_build_can_be_stopped_and_the_build_sees_it(client):
    aid = _action("build", False)
    assert client.post(f"/api/tunnels/builds/{aid}/stop").json()["ok"] is True
    assert aid in builder.CANCEL
    builder.CANCEL.discard(aid)


def test_a_finished_build_an_unknown_one_and_another_kind_of_action_cannot_be_stopped(client):
    done = _action("build", True)
    other = _action("fix", False)
    assert client.post(f"/api/tunnels/builds/{done}/stop").status_code == 404
    assert client.post(f"/api/tunnels/builds/{other}/stop").status_code == 404
    assert client.post("/api/tunnels/builds/999999/stop").status_code == 404
    assert done not in builder.CANCEL and other not in builder.CANCEL
