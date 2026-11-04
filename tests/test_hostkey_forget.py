"""A server removed from the panel takes its pinned SSH key with it; a reinstalled server at the same address is told as that and not as a wrong password; the connect dialogs offer to trust the new identity (the owner, 2026-11-04)."""
import json
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from nikatel.api import servers as servers_api
from nikatel.config import get_settings
from nikatel.services import nodes

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"
NODE = shutil.which("node")
KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
KEY_B = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB"
CHANGED = ("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
           "Offending ED25519 key in /var/lib/x/known_hosts:14\nHost key for 203.0.113.50 has changed and you have requested strict checking.\nHost key verification failed.\n")
DENIED = "root@203.0.113.50: Permission denied (publickey,password).\n"


@pytest.fixture
def known_hosts(monkeypatch):
    from nikatel.services import cfgmeter

    monkeypatch.setattr(cfgmeter, "drop_server", lambda sid: None)          # (removing a server asks it over SSH to take the meter off; these addresses answer nothing)
    kh = get_settings().known_hosts
    kh.parent.mkdir(parents=True, exist_ok=True)
    kh.write_text(f"203.0.113.50 {KEY_A}\n[203.0.113.51]:2222 {KEY_A}\n198.51.100.7 {KEY_B}\n")
    return kh


def test_removing_a_server_drops_its_pinned_key_and_only_its_own(client, known_hosts):
    a = client.post("/api/servers", json={"name": "old1", "host": "203.0.113.50", "role": "foreign"}).json()["id"]
    b = client.post("/api/servers", json={"name": "old2", "host": "203.0.113.51", "role": "foreign", "ssh_port": 2222}).json()["id"]
    assert client.delete(f"/api/servers/{a}").status_code == 200
    left = known_hosts.read_text()
    assert "203.0.113.50" not in left and "[203.0.113.51]:2222" in left and "198.51.100.7" in left          # the one removed is gone, the others stay
    assert client.delete(f"/api/servers/{b}").status_code == 200
    left = known_hosts.read_text()
    assert "203.0.113.51" not in left and "198.51.100.7" in left                                            # a server on its own SSH port is forgotten by its bracketed name


def test_a_failing_key_cleanup_never_keeps_the_server_on_the_list(client, known_hosts, monkeypatch):
    sid = client.post("/api/servers", json={"name": "old3", "host": "203.0.113.50", "role": "foreign"}).json()["id"]

    def hang(host, port):
        raise subprocess.TimeoutExpired("ssh-keygen", 15)

    monkeypatch.setattr(servers_api, "drop_known_host", hang)
    assert client.delete(f"/api/servers/{sid}").status_code == 200 and client.get(f"/api/servers/{sid}").status_code == 404


def test_the_forget_route_still_answers_504_when_ssh_keygen_hangs(client, known_hosts, monkeypatch):
    sid = client.post("/api/servers", json={"name": "old4", "host": "203.0.113.50", "role": "foreign"}).json()["id"]
    monkeypatch.setattr(servers_api, "drop_known_host", lambda h, p: (_ for _ in ()).throw(subprocess.TimeoutExpired("ssh-keygen", 15)))
    assert client.post(f"/api/servers/{sid}/forget-hostkey").status_code == 504


@pytest.fixture
def conn(monkeypatch):
    real = nodes.get_settings()
    monkeypatch.setattr(nodes, "get_settings", lambda: types.SimpleNamespace(read_only=False, known_hosts=real.known_hosts, ssh_key=real.ssh_key))
    monkeypatch.setattr(nodes, "_pubkey", lambda: "ssh-ed25519 AAAA test")
    monkeypatch.setattr(nodes, "run_script", lambda *a, **k: "HOST=box-1")


def test_a_changed_host_key_is_told_as_that_not_as_a_wrong_password(client, conn, monkeypatch):
    sid = client.post("/api/servers", json={"name": "re1", "host": "203.0.113.50", "role": "foreign"}).json()["id"]
    monkeypatch.setattr(nodes.subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 255, stdout=CHANGED))
    r = client.post(f"/api/servers/{sid}/connect", json={"password": "secret-pass"})
    assert r.status_code == 502 and "host key of this server changed" in r.json()["error"] and "wrong user" not in r.json()["error"]
    err = client.get(f"/api/servers/{sid}").json()["last_error"]
    assert err.startswith("SSH login failed: the host key") and re.search(r"host key", err, re.IGNORECASE)          # the row's "Trust the new server" button keys on "host key"
    assert not re.search(r"password|user|denied", r.json()["error"], re.IGNORECASE)                               # the dialogs ask for the password again only on those words: not here


def test_a_plain_wrong_password_is_still_a_wrong_password(client, conn, monkeypatch):
    sid = client.post("/api/servers", json={"name": "re2", "host": "203.0.113.50", "role": "foreign"}).json()["id"]
    monkeypatch.setattr(nodes.subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 255, stdout=DENIED))
    r = client.post(f"/api/servers/{sid}/connect", json={"password": "wrong"})
    assert r.status_code == 502 and r.json()["error"] == "wrong user or password"


# ---------------------------------------------------------------- the browser side, run with node
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _connect_try_src() -> str:
    js = (WEB / "app.js").read_text(encoding="utf-8")
    i = js.index("const connectTry = async (id, name, pw) =>")
    return js[i:js.index("\n};", i) + 3]


def _run(first_error: str | None, yes: bool) -> dict:
    code = f"""
const calls = [], asked = []; let n = 0;
const api = async (u, o) => {{ calls.push(u.split("/").pop()); if (u.endsWith("/connect")) {{ n++; if (n === 1 && {json.dumps(first_error)}) throw new Error({json.dumps(first_error)}); return {{hostname: "box"}}; }} return {{ok: true}}; }};
const gconfirm = async t => {{ asked.push(t); return {json.dumps(yes)}; }};
{_connect_try_src()}
(async () => {{
  let got = null, err = null;
  try {{ got = await connectTry(7, "srv-x", "pw"); }} catch (e) {{ err = e.message; }}
  console.log(JSON.stringify({{calls, asked: asked.length, name: asked[0] || "", got, err}}));
}})();
"""
    out = subprocess.run([NODE, "-e", code], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


HOSTKEY = "the host key of this server changed (reinstalled?) - press Trust the new server in its window, or menu Access → Forget host key"


@needs_node
def test_trusting_the_new_identity_forgets_the_old_one_and_retries_with_the_same_password():
    r = _run(HOSTKEY, True)
    assert r["calls"] == ["connect", "forget-hostkey", "connect"] and r["got"] == {"hostname": "box"} and r["err"] is None and r["asked"] == 1 and "srv-x" in r["name"]


@needs_node
def test_not_trusting_it_stops_with_the_reason_and_changes_nothing():
    r = _run(HOSTKEY, False)
    assert r["calls"] == ["connect"] and r["got"] is None and "host key" in r["err"] and r["asked"] == 1


@needs_node
def test_any_other_error_goes_straight_to_the_caller_without_a_question():
    r = _run("wrong user or password", True)
    assert r["calls"] == ["connect"] and r["asked"] == 0 and r["err"] == "wrong user or password"
    assert _run(None, True)["calls"] == ["connect"]                                                             # a good password: one call


def test_all_three_connect_calls_use_it_and_the_prompt_tells_why_it_asks_again():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert js.count("connectTry(") == 3 and "api(`/api/servers/${id}/connect`" not in js.replace("go = () => api(`/api/servers/${id}/connect`", "")      # the row button, the add dialog and the second-chance dialog; only the helper itself posts to /connect
    assert "the SSH password of ${name} again?" in js[js.index("async function connectWithPrompt"):js.index("window.serverPassword")]
