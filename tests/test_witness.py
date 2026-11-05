"""The second vantage of the Configs monitoring (services/witness.py, 1.69.227): a config the main probe calls down but a witness gets through is shown up; both fail -> down;
the witness is never a failover; its config list follows the main probe's."""
import time

import httpx
import pytest

from nikatel.db import session
from nikatel.models import Config
from nikatel.services import configs, probe, witness
from nikatel.services import settings as st

_T = {"at": 1791576000}


def fresh(health):
    def read():
        _T["at"] += 30
        return {**health, "configs": [dict(c, checked_at=_T["at"]) for c in health["configs"]]}

    return read


def _row(name, status, checked_at, ping=300, by="w1"):
    return {"name": name, "slug": name.lower(), "port": 11000, "enabled": True, "status": status, "ping_ms": ping, "error": "", "checked_at": checked_at, "down_mbps": None, "up_mbps": None, "speed_at": "", "by": by}


@pytest.fixture(autouse=True)
def _clean():
    witness._STATE.update(read_at=0.0, fail_at=0.0, rows={})
    witness._SYNCED.clear()
    yield
    witness._STATE.update(read_at=0.0, fail_at=0.0, rows={})
    witness._SYNCED.clear()


def test_rescue_is_up_only_when_a_fresh_witness_test_passed():
    now = 1_800_000_000
    down = _row("TCP-1", "down", now)
    up = _row("TCP-1", "up", now - 30, ping=410, by="w1")
    r = witness.rescue(down, up, now)
    assert r["status"] == "up" and r["ping_ms"] == 410 and "works from w1" in r["error"] and "main probe" in r["error"]
    assert witness.rescue(down, _row("TCP-1", "down", now), now) is None                       # both fail: it stays down
    assert witness.rescue(down, _row("TCP-1", "up", now - witness.FRESH - 1), now) is None     # an old test says nothing about now
    assert witness.rescue(down, None, now) is None                                              # no witness answer
    assert witness.rescue(_row("TCP-1", "up", now), up, now) is None                            # nothing to rescue


def test_a_config_the_main_probe_calls_down_is_up_while_a_witness_gets_through(client, monkeypatch):
    base = {"name": "TCP-1", "slug": "tcp-1", "port": 11002, "status": "down", "ping": 0, "error": "curl: (28) SSL connection timeout", "speed": {}}
    monkeypatch.setattr(probe, "health", fresh({"ok": True, "configs": [base]}))
    answers = {"TCP-1": _row("TCP-1", "up", int(time.time()), ping=480, by="far-iran")}
    monkeypatch.setattr(witness, "read", lambda max_age=witness.READ_EVERY: answers)

    for _ in range(3):
        configs.sync()

    r = client.get("/api/configs").json()["configs"][0]
    assert r["status"] == "up" and r["ping_ms"] == 480 and "works from far-iran" in r["last_error"]
    assert r["meta"]["witness"]["by"] == "far-iran"
    assert not any("not working" in e["text"] for e in client.get("/api/events?domain=configs").json()["events"])      # no false alarm, no flap

    answers["TCP-1"] = _row("TCP-1", "down", int(time.time()), by="far-iran")           # now the witness fails too: it is really down
    configs.sync()
    configs.sync()
    r = client.get("/api/configs").json()["configs"][0]
    assert r["status"] == "down" and "timeout" in r["last_error"] and "witness" not in r["meta"]
    assert any("not working" in e["text"] for e in client.get("/api/events?domain=configs").json()["events"])

    answers.clear()                                                                          # a witness that does not answer changes nothing
    configs.sync()
    assert client.get("/api/configs").json()["configs"][0]["status"] == "down"


def test_the_witnesses_are_not_asked_while_everything_is_up(client, monkeypatch):
    up = {"ok": True, "configs": [{"name": "A", "slug": "a", "port": 11000, "status": "up", "ping": 100, "error": None, "speed": {}}]}
    monkeypatch.setattr(probe, "health", fresh(up))
    asked = []
    monkeypatch.setattr(witness, "read", lambda max_age=0: asked.append(1) or {})
    configs.sync()
    assert asked == []


def test_read_takes_the_best_answer_caches_and_backs_off(monkeypatch):
    st.put("probe", {"witness": [{"name": "w1", "api_url": "http://192.0.2.1:18080", "api_token": "a" * 40}, {"name": "w2", "api_url": "http://192.0.2.2:18080", "api_token": "b" * 40}]})
    calls = []

    def fake(p, method, path, body=None, timeout=30):
        calls.append(p["name"])
        status = "up" if p["name"] == "w2" else "down"
        return {"ok": True, "configs": [{"name": "TCP-1", "slug": "t", "port": 1, "status": status, "ping": 50, "error": "", "checked_at": 5, "speed": {}}]}

    monkeypatch.setattr(probe, "_call", fake)
    rows = witness.read()
    assert rows["TCP-1"]["status"] == "up" and rows["TCP-1"]["by"] == "w2"          # one witness that gets through is enough
    n = len(calls)
    witness.read()
    assert len(calls) == n                                                              # kept for READ_EVERY seconds

    witness._STATE["read_at"] = 0.0

    def dead(p, method, path, body=None, timeout=30):
        calls.append("dead")
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(probe, "_call", dead)
    assert witness.read() == {}                                                         # nobody answers: nothing, no exception
    n = len(calls)
    witness._STATE["read_at"] = 0.0
    assert witness.read() == {} and len(calls) == n                                     # and not asked again for BACKOFF seconds


def test_a_witness_is_never_a_failover_and_never_shown_with_its_token(client):
    client.put("/api/settings/probe", json={"api_url": "http://10.0.0.9:18080", "api_token": "T" * 40})
    st.put("probe", {"witness": [{"name": "w1", "api_url": "http://192.0.2.1:18080", "api_token": "W" * 40}]})
    assert [c["name"] for c in probe.candidates()] == ["primary"]
    assert [w["name"] for w in probe.witnesses()] == ["w1"]
    r = client.get("/api/settings/probe")
    assert r.json()["witness"] == [{"name": "w1", "api_url": "http://192.0.2.1:18080", "has_token": True}] and "WWWW" not in r.text
    assert client.put("/api/settings/probe", json={"api_url": "http://10.0.0.9:18080"}).status_code == 200
    assert [w["name"] for w in probe.witnesses()] == ["w1"]                              # saving the probe settings keeps the witness
    assert client.delete("/api/settings/probe/witness/nobody").status_code == 404
    assert client.delete("/api/settings/probe/witness/w1").json()["witness"] == [] and probe.witnesses() == []


def test_mirror_keeps_the_witness_list_equal_to_the_main_probe(client, monkeypatch):
    st.put("probe", {"witness": [{"name": "w1", "api_url": "http://192.0.2.1:18080", "api_token": "a" * 40}]})

    with session() as db:
        for n in ("A", "B"):
            db.add(Config(name=n, status="up", enabled=True, meta={}))

    links = {"A": "vless://u@a.example:443?type=tcp#A", "B": "vless://u@b.example:443?type=tcp#B"}
    monkeypatch.setattr(probe, "config_uri", lambda name: links[name])
    theirs = {"B": "vless://u@b.example:443?type=tcp#B", "C": "vless://u@c.example:443?type=tcp#C"}      # the witness holds B (the same) and C (nobody has it any more)
    bulk = []

    def fake(p, method, path, body=None, timeout=30):
        if path == "/api/config/list":
            return {"ok": True, "configs": [{"name": n} for n in theirs]}

        if path.startswith("/api/config/uri"):
            return {"ok": True, "uri": theirs[path.split("name=")[1].replace("%20", " ")]}

        assert path == "/api/config/bulk"
        bulk.append(body)

        for it in body["add"]:
            theirs[it["name"]] = it["uri"]

        for it in body["update"]:
            theirs[it["name"]] = it["uri"]

        for n in body["delete"]:
            theirs.pop(n, None)

        return {"ok": True, "results": {"add": [{"name": i["name"], "ok": True} for i in body["add"]], "update": [{"name": i["name"], "ok": True} for i in body["update"]], "delete": []}}

    monkeypatch.setattr(probe, "_call", fake)
    assert witness.mirror() == {"w1": {"add": 1, "update": 0, "delete": 1}}
    assert [i["name"] for i in bulk[0]["add"]] == ["A"] and bulk[0]["delete"] == ["C"] and bulk[0]["update"] == []
    assert witness.mirror() == {} and len(bulk) == 1                                      # equal now: no call at all

    links["B"] = "vless://u@b2.example:443?type=tcp#B"                                    # the main probe's link of B changed
    assert witness.mirror() == {"w1": {"add": 0, "update": 1, "delete": 0}}
    assert bulk[1]["update"] == [{"name": "B", "uri": "vless://u@b2.example:443?type=tcp#B"}]
    assert witness.mirror() == {}


def test_mirror_does_nothing_without_a_witness_and_survives_a_dead_one(client, monkeypatch):
    assert witness.mirror() == {}
    st.put("probe", {"witness": [{"name": "w1", "api_url": "http://192.0.2.1:18080", "api_token": "a" * 40}]})

    with session() as db:
        db.add(Config(name="A", status="up", enabled=True, meta={}))

    monkeypatch.setattr(probe, "config_uri", lambda name: "vless://u@a.example:443?type=tcp#A")

    def dead(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(probe, "_call", dead)
    assert witness.mirror() == {}


def test_install_as_witness_registers_it_beside_the_probe_not_as_a_standby(client, monkeypatch, tmp_path):
    from nikatel.services import probe_install

    eng = tmp_path / "engines"
    eng.mkdir()
    (eng / "xray").write_bytes(b"\0" * 5_000_001)

    class S:
        read_only = False

        class data_dir:
            parent = tmp_path

        ssh_key = "k"
        known_hosts = "kh"

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(probe_install, "get_settings", lambda: S())
    monkeypatch.setattr(probe_install, "_put", lambda *a, **k: None)
    monkeypatch.setattr(probe_install, "run_action", lambda node, name, params=None, timeout=25: "UNIT nikatel-speed-api active\n")
    monkeypatch.setattr(probe_install.subprocess, "run", lambda *a, **k: Done())
    main = client.post("/api/servers", json={"name": "iran-a", "host": "192.0.2.10", "role": "iran"}).json()
    other = client.post("/api/servers", json={"name": "iran-b", "host": "192.0.2.11", "role": "iran"}).json()
    main_id, other_id = main["id"], other["id"]
    st.put("probe", {"api_url": "http://192.0.2.10:18080", "api_token": "T" * 40, "server_id": main_id})

    r = client.post(f"/api/settings/probe/install?server_id={main_id}&as_witness=true")                 # the main probe's own server is no second vantage
    assert r.status_code == 400 and "SECOND" in r.json()["error"]

    r = client.post(f"/api/settings/probe/install?server_id={other_id}&as_witness=true")
    assert r.status_code == 200 and r.json()["role"] == "witness"
    assert [w["name"] for w in probe.witnesses()] == ["iran-b"] and st.get("probe")["standby"] == []
    assert [c["name"] for c in probe.candidates()] == ["primary"]
