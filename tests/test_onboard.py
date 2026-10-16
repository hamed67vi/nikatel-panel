"""A server that was just added is prepared in the background (services/onboard.py): its first reading, its GRE links and the tunnel cores - each step written on its row while it runs
(the owner, 2026-10-16: "from the very first moment show what is being done" and "the tunnel cores must install by themselves"). No test reaches a server: ssh, the downloads and the facts are stubbed."""
import threading
import time
import types

import pytest

from nikatel import ssh
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.main import cli
from nikatel.models import Server
from nikatel.services import engines, facts, nodes, onboard
from nikatel.ssh import Node, SSHError


@pytest.fixture
def sid(client):
    r = client.post("/api/servers", json={"name": "ob1", "host": "203.0.113.50", "role": "foreign"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture(autouse=True)
def quiet_state():
    engines._STATE.update(result=None, checked_at=0, checking=False, job=None)
    yield
    engines._STATE.update(result=None, checked_at=0, checking=False, job=None)


def _extra(sid: int) -> dict:
    with session() as db:
        return dict(db.get(Server, sid).extra or {})


def _answer(online: bool, error: str = ""):
    def sample(server_id):
        with session() as db:
            s = db.get(Server, server_id)
            s.online, s.last_error = online, error
            s.facts = {"cpu": 3.0} if online else {}

    return sample


# ------------------------------------------------------------------ the steps, in order, each one on the row
def test_the_steps_run_in_order_and_each_one_is_written_on_the_row(sid, monkeypatch):
    seen, real = [], nodes.set_busy

    def spy(server_id, what="", detail="", unless=""):
        seen.append((what, detail))
        real(server_id, what, detail, unless)

    def install(server_id, names=None, step=None):
        for i, title in enumerate(("Backhaul", "Paqet"), 1):
            step(title, i, 2)

        return {"backhaul": "installed", "paqet": "present"}

    monkeypatch.setattr(nodes, "set_busy", spy)
    monkeypatch.setattr(facts, "sample_server", _answer(True))
    monkeypatch.setattr(nodes, "gre_check", lambda server_id: {"ok": False, "links": 0})
    monkeypatch.setattr(engines, "install_on", install)
    out = onboard.run(sid)
    assert out["read"] == "ok" and out["gre"] == "ok" and out["cores"] == "ok" and out["cores_detail"] == {"backhaul": "installed", "paqet": "present"}
    assert seen == [("Reading the server", ""), ("Checking the GRE links", ""), ("Installing the tunnel cores", "Backhaul · 1 of 2"), ("Installing the tunnel cores", "Paqet · 2 of 2")]
    x = _extra(sid)
    assert x["busy"] is None and x["onboard"]["steps"] == {"read": "ok", "gre": "ok", "cores": "ok"} and x["onboard"]["cores"]["paqet"] == "present" and x["onboard"]["at"] >= x["onboard"]["started"]


def test_a_server_that_does_not_answer_ends_it_after_the_first_step(sid, monkeypatch):
    called = []
    monkeypatch.setattr(facts, "sample_server", _answer(False, "ssh: connect timed out"))
    monkeypatch.setattr(nodes, "gre_check", lambda server_id: called.append("gre"))
    monkeypatch.setattr(engines, "install_on", lambda *a, **k: called.append("cores"))
    out = onboard.run(sid)
    assert out == {"read": "failed: ssh: connect timed out"} and called == []                      # nothing is put on a server that does not answer
    assert _extra(sid)["busy"] is None and _extra(sid)["onboard"]["steps"]["read"].startswith("failed")


def test_a_core_that_fails_does_not_hide_the_others_and_the_event_says_which(client, sid, monkeypatch):
    monkeypatch.setattr(facts, "sample_server", _answer(True))
    monkeypatch.setattr(nodes, "gre_check", lambda server_id: {})
    monkeypatch.setattr(engines, "install_on", lambda server_id, names=None, step=None: {"backhaul": "installed", "paqet": "failed: no GitHub from here"})
    out = onboard.run(sid)
    assert out["cores"] == "failed: paqet no GitHub from here" and out["cores_detail"]["backhaul"] == "installed"
    ev = client.get("/api/events?domain=servers").json()["events"]
    assert any(e["level"] == "warn" and "Server ob1 prepared - cores: paqet no GitHub from here" in e["text"] for e in ev)


def test_a_gre_check_that_fails_is_told_and_the_cores_still_go(sid, monkeypatch):
    def broken(server_id):
        raise SSHError("gre scan failed")

    monkeypatch.setattr(facts, "sample_server", _answer(True))
    monkeypatch.setattr(nodes, "gre_check", broken)
    monkeypatch.setattr(engines, "install_on", lambda server_id, names=None, step=None: {"backhaul": "present"})
    out = onboard.run(sid)
    assert out["gre"].startswith("failed: gre scan failed") and out["cores"] == "ok"


def test_the_panel_in_read_only_mode_reads_and_checks_but_puts_nothing_on_the_server(sid, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    monkeypatch.setattr(facts, "sample_server", _answer(True))
    monkeypatch.setattr(nodes, "gre_check", lambda server_id: {})
    out = onboard.run(sid)
    assert out["read"] == "ok" and out["gre"] == "ok" and out["cores"] == "skipped: the panel is read-only"


def test_a_server_that_is_gone_is_not_an_error_and_a_step_can_be_left_out(sid, monkeypatch):
    monkeypatch.setattr(facts, "sample_server", _answer(True))
    assert onboard.run(9999) == {}                                                                # (deleted while the steps ran)
    monkeypatch.setattr(engines, "install_on", lambda server_id, names=None, step=None: {"xray": "installed"})
    assert onboard.run(sid, ("cores",)) == {"cores": "ok", "cores_detail": {"xray": "installed"}}  # only the cores: no reading, no GRE


# ------------------------------------------------------------------ the API answers at once, one run at a time
def test_the_api_starts_the_steps_in_the_background_and_answers_at_once(client, sid, monkeypatch):
    gate, calls = threading.Event(), []

    def slow(server_id, steps=onboard.STEPS):
        calls.append((server_id, tuple(steps)))
        gate.wait(5)

    monkeypatch.setattr(onboard, "run", slow)
    t0 = time.time()
    r = client.post(f"/api/servers/{sid}/onboard", json={"steps": ["cores"]})
    assert r.status_code == 202 and r.json() == {"ok": True, "started": True, "steps": ["cores"]} and time.time() - t0 < 2
    assert client.post(f"/api/servers/{sid}/onboard", json={}).json()["started"] is False         # one run at a time for a server
    gate.set()

    for _ in range(40):
        if not onboard._RUNNING:
            break

        time.sleep(0.05)

    assert calls == [(sid, ("cores",))] and not onboard._RUNNING
    assert client.post(f"/api/servers/{sid}/onboard", json={"steps": ["read", "nuke"]}).status_code == 400
    assert client.post("/api/servers/9999/onboard", json={}).status_code == 404


# ------------------------------------------------------------------ the cores
def test_install_on_reports_what_happened_to_each_core(sid, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    done, flags = [], []

    def fake(node, name, say=None, only_missing=False):
        done.append(name)
        flags.append(only_missing)

        if name == "paqet":
            raise RuntimeError("no GitHub from here")

        return name != "chisel"

    monkeypatch.setattr(engines, "ensure_on_node", fake)
    steps = []
    res = engines.install_on(sid, step=lambda t, i, n: steps.append((t, i, n)))
    assert res == {"backhaul": "installed", "backpack": "installed", "paqet": "failed: no GitHub from here", "chisel": "present", "xray": "installed"}
    assert done == list(engines.ENGINES) and steps[0] == ("Backhaul", 1, 5) and steps[-1] == ("Xray", 5, 5)
    assert all(flags)                                                                                  # only what is NOT there is put: a core that is there is left as it is
    assert engines.install_on(sid, ["paqet", "nope"]) == {"paqet": "failed: no GitHub from here"}      # an unknown name is left out
    monkeypatch.setattr(get_settings(), "read_only", True)

    with pytest.raises(ValueError, match="read-only"):
        engines.install_on(sid)

    monkeypatch.setattr(get_settings(), "read_only", False)

    with pytest.raises(ValueError, match="not found"):
        engines.install_on(9999)


def test_the_xray_gateway_directory_is_made_before_its_copy_and_the_others_need_none(monkeypatch, tmp_path):
    f = tmp_path / "core"
    f.write_bytes(b"x" * 1_500_000)
    calls = []

    def script(node, name, params=None, timeout=0):
        calls.append(("script", name, dict(params or {})))
        return "SHA=none"

    def action(node, name, params=None, timeout=0):
        calls.append(("action", name, dict(params or {})))
        return "SWAPPED units=" if name == "engine_swap" else "OK"

    monkeypatch.setattr(engines, "cache_file", lambda e: f)
    monkeypatch.setattr(engines, "run_script", script)
    monkeypatch.setattr(engines, "run_action", action)
    monkeypatch.setattr(engines.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stderr=""))
    node = Node("203.0.113.9", "root", 22)
    assert engines.ensure_on_node(node, "xray") is True
    assert [c[1] for c in calls] == ["engine_sha", "xgw_mkdir", "engine_swap"] and calls[0][2] == {"BIN": "/opt/nikatel-xgw/xray"}
    calls.clear()
    assert engines.ensure_on_node(node, "paqet") is True
    assert [c[1] for c in calls] == ["engine_sha", "engine_swap"] and calls[0][2] == {"BIN": "/usr/local/bin/paqet"}


def test_a_core_that_is_there_is_left_exactly_as_it_is_by_an_install(monkeypatch, tmp_path):
    """A server added to the panel often has tunnels of the old panel: a BackPack of another build must not be replaced behind their back (found on a real server, 2026-10-16)."""
    f = tmp_path / "core"
    f.write_bytes(b"x" * 1_500_000)
    calls, answer = [], {"v": "SHA=" + "a" * 64}

    def script(node, name, params=None, timeout=0):
        calls.append(("script", name))
        return answer["v"]

    monkeypatch.setattr(engines, "cache_file", lambda e: (calls.append(("cache", e)), f)[1])
    monkeypatch.setattr(engines, "run_script", script)
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: (calls.append(("action", name)), "SWAPPED units=")[1])
    monkeypatch.setattr(engines.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stderr=""))
    node = Node("203.0.113.9", "root", 22)
    assert engines.ensure_on_node(node, "backpack", only_missing=True) is False                           # another build is there: nothing is read from the cache, nothing is copied
    assert calls == [("script", "engine_sha")]
    calls.clear()
    answer["v"] = "SHA=none"
    assert engines.ensure_on_node(node, "backpack", only_missing=True) is True                            # not there: put
    assert ("cache", "backpack") in calls and ("action", "engine_swap") in calls
    calls.clear()
    answer["v"] = "SHA=" + "b" * 64
    assert engines.ensure_on_node(node, "backpack") is True                                               # the build of a tunnel still follows the cache, as before
    assert ("action", "engine_swap") in calls


def test_the_checksum_script_accepts_the_two_places_a_core_lives_and_nothing_else():
    import re

    rule = ssh.SCRIPTS["engine_sha"][1]["BIN"]
    assert re.fullmatch(rule, "/opt/nikatel-xgw/xray") and re.fullmatch(rule, "/usr/local/bin/nikatel-chisel")
    assert not re.fullmatch(rule, "/etc/passwd") and not re.fullmatch(rule, "/opt/nikatel-xgw/../shadow") and not re.fullmatch(rule, "/usr/local/bin/a b")
    assert all(re.fullmatch(rule, e["bin"]) for e in engines.ENGINES.values())                       # every core is somewhere the script accepts


def test_the_cache_is_filled_by_itself_and_what_could_not_be_downloaded_is_named(monkeypatch, tmp_path):
    cache = tmp_path / "engines"
    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data"))

    def fake(name):
        if name == "paqet":
            raise RuntimeError("no GitHub from here")

        cache.mkdir(parents=True, exist_ok=True)
        (cache / name).write_bytes(b"x" * 10)
        return cache / name

    monkeypatch.setattr(engines, "cache_file", fake)
    first = engines.prefetch()
    assert first["backhaul"] == "downloaded" and first["paqet"] == "failed: no GitHub from here" and set(first) == set(engines.ENGINES)
    assert engines.prefetch()["backhaul"] == "cached"                                                 # what is there is not downloaded again


def test_install_puts_a_core_on_the_servers_that_have_not_got_it_and_update_leaves_them_alone(client, monkeypatch, tmp_path):
    for n, h in (("a1", "203.0.113.61"), ("b1", "203.0.113.62"), ("c1", "203.0.113.63")):
        assert client.post("/api/servers", json={"name": n, "host": h, "role": "foreign"}).status_code == 201

    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data", read_only=False))
    monkeypatch.setattr(engines, "_download", lambda engine: ("v9.9.9", b"b" * 1_200_000))
    wrote = []
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: (wrote.append((node.host, name)), "SWAPPED units=")[1])
    engines._STATE["result"] = {"paqet": {"nodes": [{"name": "a1", "state": "not installed"}, {"name": "b1", "state": "current"}, {"name": "c1", "state": "outdated"}]}}
    only = engines.update("paqet")
    assert sorted(i["name"] for i in only["items"]) == ["c1"] and only["kind"] == "update"               # Update: the outdated ones only
    assert (tmp_path / "engines" / "paqet").read_bytes() == b"b" * 1_200_000                           # the panel's cache follows the update: a tunnel built later does not put the old build back
    put = []
    monkeypatch.setattr(engines, "ensure_on_node", lambda node, name, say=None, only_missing=False: put.append((node.host, name, only_missing)) or True)
    monkeypatch.setattr(engines, "cache_file", lambda e: tmp_path / "engines" / e)
    engines._STATE["job"] = None
    both = engines.update("paqet", only_missing=True)
    assert [i["name"] for i in both["items"]] == ["a1"] and both["items"][0]["state"] == "ok" and both["kind"] == "install"      # Install: the servers that have not got it - and only them
    assert put == [("203.0.113.61", "paqet", True)]


def test_the_install_button_asks_for_the_servers_that_have_not_got_the_core(client, monkeypatch):
    seen = []
    monkeypatch.setattr(engines, "update", lambda engine, only_missing=False: seen.append((engine, only_missing)) or {"engine": engine, "items": [], "done": True})
    assert client.post("/api/engines/paqet/update").json()["ok"] is True and client.post("/api/engines/paqet/update?missing=true").json()["ok"] is True
    assert seen == [("paqet", False), ("paqet", True)]


def test_the_check_counts_the_servers_that_have_not_got_a_core(client, monkeypatch):
    client.post("/api/servers", json={"name": "chk1", "host": "203.0.113.71", "role": "foreign"})
    monkeypatch.setattr(engines, "latest", lambda e: {"version": "v1.0.0", "url": "", "page": "", "digest": ""})
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=0: "ENGINE backhaul v1.0.0\nENGINE paqet NONE\nENGINE chisel NONE\nENGINE xray NONE\nENGINE backpack NONE\n")
    res = engines.check()["engines"]
    assert res["backhaul"]["missing"] == 0 and res["backhaul"]["current"] == 1 and res["paqet"]["missing"] == 1 and res["xray"]["nodes"][0]["state"] == "not installed"


def test_the_job_and_the_command_fill_the_cache_without_touching_a_server(monkeypatch, capsys):
    from nikatel import jobs, main

    monkeypatch.setattr(jobs, "_SPECS", {})
    monkeypatch.setattr(ssh, "set_jump_provider", lambda fn: None)                                   # (registering the jobs must not change how the other tests reach a server)
    main._register_jobs()
    assert "engine-cache" in jobs._SPECS and jobs._SPECS["engine-cache"].interval == 6 * 3600
    monkeypatch.setattr(engines, "prefetch", lambda: {"backhaul": "cached", "paqet": "failed: x"})
    assert cli(["engines"]) == 1
    out = capsys.readouterr().out
    assert "Backhaul: cached" in out and "Paqet: failed: x" in out
    monkeypatch.setattr(engines, "prefetch", lambda: {"backhaul": "downloaded"})
    assert cli(["engines"]) == 0
