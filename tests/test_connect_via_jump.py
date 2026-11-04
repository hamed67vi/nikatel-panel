"""Connect SSH with a password: an Iran server cut from abroad is reached through another Iran server, so a wrong password is told as such; the row's button asks again."""
import json
import subprocess
import time
import types
from pathlib import Path

import pytest

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Server
from nikatel.services import nodes
from nikatel.services.pulse import IRAN_ONLY
from nikatel.ssh import Node, SSHError

TIMED_OUT = "ssh: connect to host 203.0.113.9 port 22: Connection timed out\n"
DENIED = "root@203.0.113.9: Permission denied (publickey,password).\n"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ssh._VIA.clear()
    ssh.set_jump_provider(None)
    real = nodes.get_settings()
    fake = types.SimpleNamespace(read_only=False, known_hosts=real.known_hosts, ssh_key=real.ssh_key)
    monkeypatch.setattr(nodes, "get_settings", lambda: fake)
    monkeypatch.setattr(nodes, "_pubkey", lambda: "ssh-ed25519 AAAA test")
    monkeypatch.setattr(nodes, "run_script", lambda *a, **k: "HOST=box-9")
    yield
    ssh._VIA.clear()
    ssh.set_jump_provider(None)


def _fake_run(answers):
    """sshpass as a fake: `answers` maps 'direct' / 'jump' to (exit code, output); every call is written down."""
    calls = []

    def run(cmd, *a, **k):
        via = any(str(x).startswith("ProxyCommand=") for x in cmd)
        calls.append("jump" if via else "direct")
        code, out = answers["jump" if via else "direct"]

        if out == "HANG":
            raise subprocess.TimeoutExpired(cmd, 30)

        return subprocess.CompletedProcess(cmd, code, stdout=out)

    return run, calls


def _probe_via_jump_only(monkeypatch):
    """The jump priming (ssh.ensure_via) sees the jump answer and the direct path not."""
    def once(node, script, timeout, j=None):
        if j is None:
            raise SSHError("timeout after 20s")

        return "nikatel-via-ok"

    monkeypatch.setattr(ssh, "_exec_once", once)
    ssh.set_jump_provider(lambda n: [Node("203.0.113.7")])


def _server(client, role="iran"):
    return client.post("/api/servers", json={"name": "cut1", "host": "203.0.113.9", "role": role}).json()


def test_a_cut_iran_server_is_reached_through_a_jump_and_the_key_lands(client, monkeypatch):
    s = _server(client)
    run, calls = _fake_run({"direct": (255, TIMED_OUT), "jump": (0, "KEY_INSTALLED\n")})
    monkeypatch.setattr(nodes.subprocess, "run", run)
    _probe_via_jump_only(monkeypatch)
    r = client.post(f"/api/servers/{s['id']}/connect", json={"password": "secret-pass"})
    assert r.status_code == 200 and r.json()["hostname"] == "box-9"
    assert calls == ["direct", "jump"]                                                   # direct once (it timed out), then through the jump
    assert ssh.via_of("203.0.113.9") == "203.0.113.7"                                     # the jump is remembered for the jobs that follow
    assert (client.get(f"/api/servers/{s['id']}").json()["extra"] or {}).get("hostname") == "box-9"
    evs = client.get("/api/events").json()
    evs = evs.get("events", evs.get("items", [])) if isinstance(evs, dict) else evs
    assert any("through 203.0.113.7" in json.dumps(e) for e in evs)                       # the event says the server is reached from Iran


def test_a_wrong_password_through_the_jump_is_told_as_a_wrong_password_not_a_time_out(client, monkeypatch):
    s = _server(client)
    run, calls = _fake_run({"direct": (255, TIMED_OUT), "jump": (5, DENIED)})
    monkeypatch.setattr(nodes.subprocess, "run", run)
    _probe_via_jump_only(monkeypatch)
    r = client.post(f"/api/servers/{s['id']}/connect", json={"password": "wrong"})
    assert r.status_code == 502 and r.json()["error"] == "wrong user or password" and calls == ["direct", "jump"]
    assert client.get(f"/api/servers/{s['id']}").json()["last_error"].startswith("SSH login failed: wrong user")


def test_a_server_the_pulse_calls_cut_skips_the_direct_try(client, monkeypatch):
    s = _server(client)

    with session() as db:
        db.get(Server, s["id"]).last_error = IRAN_ONLY + " (seen from Iran by iran-a)"

    run, calls = _fake_run({"direct": (255, TIMED_OUT), "jump": (0, "KEY_INSTALLED\n")})
    monkeypatch.setattr(nodes.subprocess, "run", run)
    _probe_via_jump_only(monkeypatch)
    assert client.post(f"/api/servers/{s['id']}/connect", json={"password": "secret-pass"}).status_code == 200
    assert calls == ["jump"]                                                              # no 12-second direct wait first


def test_a_jump_already_known_is_used_at_once(client, monkeypatch):
    s = _server(client)
    ssh._VIA["203.0.113.9"] = (Node("203.0.113.7"), time.time() + 300)
    run, calls = _fake_run({"direct": (255, TIMED_OUT), "jump": (0, "KEY_INSTALLED\n")})
    monkeypatch.setattr(nodes.subprocess, "run", run)
    assert client.post(f"/api/servers/{s['id']}/connect", json={"password": "secret-pass"}).status_code == 200 and calls == ["jump"]


def test_no_jump_answers_the_message_says_so_and_still_reads_as_no_answer(client, monkeypatch):
    s = _server(client)
    run, calls = _fake_run({"direct": (255, TIMED_OUT), "jump": (0, "KEY_INSTALLED\n")})
    monkeypatch.setattr(nodes.subprocess, "run", run)
    monkeypatch.setattr(ssh, "_exec_once", lambda *a, **k: (_ for _ in ()).throw(SSHError("timeout after 20s")))
    ssh.set_jump_provider(lambda n: [Node("203.0.113.7")])
    r = client.post(f"/api/servers/{s['id']}/connect", json={"password": "secret-pass"})
    assert r.status_code == 502 and calls == ["direct"]
    assert "timed out" in r.json()["error"] and "no other Iran server" in r.json()["error"]      # the web's "does not answer" dialog still matches


def test_a_hung_direct_login_goes_through_the_jump_too(client, monkeypatch):
    s = _server(client)
    run, calls = _fake_run({"direct": (0, "HANG"), "jump": (0, "KEY_INSTALLED\n")})
    monkeypatch.setattr(nodes.subprocess, "run", run)
    _probe_via_jump_only(monkeypatch)
    assert client.post(f"/api/servers/{s['id']}/connect", json={"password": "secret-pass"}).status_code == 200 and calls == ["direct", "jump"]


def test_a_foreign_server_and_a_plain_wrong_password_are_as_before(client, monkeypatch):
    s = _server(client, role="foreign")
    run, calls = _fake_run({"direct": (255, TIMED_OUT), "jump": (0, "KEY_INSTALLED\n")})
    monkeypatch.setattr(nodes.subprocess, "run", run)
    probed = []
    monkeypatch.setattr(ssh, "_exec_once", lambda *a, **k: probed.append(1))
    ssh.set_jump_provider(lambda n: [Node("203.0.113.7")])
    r = client.post(f"/api/servers/{s['id']}/connect", json={"password": "secret-pass"})
    assert r.status_code == 502 and "timed out" in r.json()["error"] and calls == ["direct"] and not probed      # a foreign server takes no detour through Iran
    s2 = client.post("/api/servers", json={"name": "ir2", "host": "203.0.113.10", "role": "iran"}).json()
    run, calls = _fake_run({"direct": (5, DENIED), "jump": (0, "KEY_INSTALLED\n")})
    monkeypatch.setattr(nodes.subprocess, "run", run)
    r = client.post(f"/api/servers/{s2['id']}/connect", json={"password": "wrong"})
    assert r.status_code == 502 and r.json()["error"] == "wrong user or password" and calls == ["direct"] and not probed      # answered directly: no jump


def test_the_rows_connect_button_asks_the_password_again_on_a_wrong_one():
    js = Path(__file__).resolve().parents[1].joinpath("nikatel", "web", "app.js").read_text(encoding="utf-8")
    fn = js[js.index("window.srvConnect = async (id, name) =>"):]
    fn = fn[:fn.index("\n};") + 3]
    assert "for (let i = 0; i < 3; i++)" in fn and "tries" in fn and "/password|user|denied/i" in fn           # three tries on a wrong password
    assert "{password: true" in fn                                                                           # the password is typed hidden
