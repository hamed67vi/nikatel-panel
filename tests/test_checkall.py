"""Check all servers: one action on the panel server (a reload does not stop it, Stop does), progress through the API."""
import time

import pytest

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import checkall, nodes


def _add(client, name, host, role):
    r = client.post("/api/servers", json={"name": name, "host": host, "role": role})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _wait(client, limit=15):
    end = time.time() + limit

    while time.time() < end:
        st = client.get("/api/checkall").json()

        if not st["running"] and st.get("finished_at"):
            return st

        time.sleep(0.1)

    raise AssertionError("the check did not finish")


@pytest.fixture
def fakes(monkeypatch):
    seen = {"gre": [], "flt": []}

    def sample(sid):
        with session() as db:
            s = db.get(Server, sid)
            s.online = s.name != "fr-dead"

    monkeypatch.setattr(checkall, "sample_server", sample)
    monkeypatch.setattr(nodes, "gre_probe", lambda sid: seen["gre"].append(sid) or {"ok": True, "detail": "ok"})
    monkeypatch.setattr(nodes, "filter_check", lambda sid, wait=28: seen["flt"].append(sid) or {"level": "clean"})
    monkeypatch.setattr(checkall, "get_settings", lambda: type("S", (), {"read_only": False})())
    return seen


def test_it_checks_every_server_and_tells_the_result(client, fakes):
    _add(client, "ir-a", "203.0.113.41", "iran")
    fr = _add(client, "fr-a", "198.51.100.41", "foreign")
    _add(client, "fr-dead", "198.51.100.42", "foreign")
    assert client.get("/api/checkall").json()["running"] is False
    assert client.post("/api/checkall", json={}).json()["ok"] is True
    st = _wait(client)
    assert st["ok"] is False and "3 of 3 servers checked, 1 with a problem" in st["line"]          # the dead one is the one problem
    story = " ".join(client.get(f"/api/actions/{st['id']}").json()["story"])
    assert "fr-dead: not reachable" in story and "fr-a: online · GRE ok · not filtered" in story and "ir-a: online · GRE ok" in story
    assert fr in fakes["flt"] and len(fakes["flt"]) == 1 and len(fakes["gre"]) == 2             # filtering only for the foreign one that answers; GRE not for the dead one


def test_a_second_start_is_refused_while_it_runs_and_stop_ends_it(client, fakes, monkeypatch):
    for i in range(6):
        _add(client, f"fr-{i}", f"198.51.100.{50 + i}", "foreign")

    def slow(sid, wait=28):
        time.sleep(0.4)
        return {"level": "clean"}

    monkeypatch.setattr(nodes, "filter_check", slow)
    client.post("/api/checkall", json={})
    r = client.post("/api/checkall", json={})
    assert r.status_code == 400 and "already running" in r.text
    assert client.post("/api/checkall/stop").json()["stopping"] is True
    st = _wait(client)
    assert "Stopped by you" in st["line"]


def test_the_role_is_checked_and_nothing_runs_without_a_server(client, fakes):
    assert client.post("/api/checkall", json={"role": "mars"}).status_code == 422
    client.post("/api/checkall", json={"role": "iran"})
    st = _wait(client)
    assert "0 of 0 servers checked" in st["line"]
    assert client.post("/api/checkall/stop").json()["stopping"] is False
