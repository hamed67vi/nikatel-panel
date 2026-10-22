"""Install after adding a server: the reviewed actions, the certificate check, the story, the API."""
import base64
import shutil
import subprocess
import time

import pytest

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import extras
from nikatel.services import settings as st
from nikatel.ssh import render_action

CERT = "-----BEGIN CERTIFICATE-----\nMIIBdemo\n\n-----END CERTIFICATE-----\n"


def test_actions_render_exactly_what_was_asked():
    bt = render_action("install_btblock", {})
    assert "Heclalava/blockpublictorrent-iptables/main/trackers" in bt and "ipset create" in bt and "ipset swap" in bt and "/etc/cron.daily/denypublic" in bt and "nikatel-btblock" in bt          # (1.56.10: the list is theirs, how it is applied is ours: one ipset, not 27 000 iptables calls)
    assert "bash bt.sh" not in bt and "ips-v4" in bt and "len(ns) > 3" in bt and "@reboot" in bt          # (their script is not run; shared and Cloudflare addresses are never blocked)
    b64 = base64.b64encode(extras.check_cert(CERT).encode()).decode()
    mz = render_action("install_marzban_node", {"B64": b64})
    assert "Gozargah/Marzban-scripts/raw/master/marzban-node.sh" in mz and "bash mz.sh install" in mz and b64 in mz and "MZNODE_OK" in mz
    with pytest.raises(ValueError):
        render_action("install_marzban_node", {"B64": "x; reboot"})                  # only base64 gets into the script


def test_certificate_check():
    assert extras.check_cert(CERT) == "-----BEGIN CERTIFICATE-----\nMIIBdemo\n-----END CERTIFICATE-----"      # blank lines dropped
    for bad in ("", "hello", "-----BEGIN CERTIFICATE-----\nno end", "-----BEGIN CERTIFICATE-----\n" + "A" * 7000 + "\n-----END CERTIFICATE-----"):
        with pytest.raises(ValueError):
            extras.check_cert(bad)


def _server():
    with session() as db:
        s = Server(name="ir7", host="203.0.113.77", role="iran")
        db.add(s)
        db.flush()
        return s.id


def _wait(client, aid):
    for _ in range(100):
        s = client.get(f"/api/actions/{aid}").json()

        if s["done"]:
            return s

        time.sleep(0.1)

    return s


def _fast(monkeypatch, status_answers, start_answer="STARTED\n"):
    """The server: starting answers `start_answer`, then every status question gets the next answer of the list (the last one repeats)."""
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(extras, "POLL", 0)
    monkeypatch.setattr(extras.time, "sleep", lambda s: None)
    calls, answers = [], list(status_answers)
    monkeypatch.setattr(extras, "run_action", lambda node, name, params=None, timeout=90: calls.append(name) or start_answer)
    monkeypatch.setattr(extras, "run_script", lambda node, name, params=None, timeout=90: answers.pop(0) if len(answers) > 1 else answers[0])
    return calls


def test_install_story_follows_each_detached_install(client, monkeypatch):
    sid = _server()
    calls = _fast(monkeypatch, ["STATE running\nsome log line\n", "STATE ok\nBT_OK drop_rules=41\n", "STATE ok\nMZNODE_OK marzban-node-marzban-node-1 Up 5 seconds\n"])
    r = client.post(f"/api/servers/{sid}/install", json={"items": ["btblock", "marzban_node"], "cert": CERT})
    assert r.status_code == 200, r.text
    s = _wait(client, r.json()["id"])
    text = "\n".join(s["story"])
    assert s["done"] and s["ok"] is True and calls == ["install_btblock", "install_marzban_node"], text
    assert "installed (drop rules=41)" in text and "address 203.0.113.77, port 62050" in text and "keep running on the server" in text
    assert st.get("pending_installs")["items"] == [] and client.get(f"/api/servers/{sid}").json()["extra"]["busy"] is None        # nothing is left pending


def test_a_failed_install_says_why(client, monkeypatch):
    sid = _server()
    _fast(monkeypatch, ["STATE failed\nMZNODE_FAILED\n"])
    r = client.post(f"/api/servers/{sid}/install", json={"items": ["marzban_node"], "cert": CERT})
    s = _wait(client, r.json()["id"])
    assert s["done"] and s["ok"] is False and "failed - MZNODE_FAILED" in "\n".join(s["story"])
    _fast(monkeypatch, [], start_answer="BAD_CERT\n")
    s = _wait(client, client.post(f"/api/servers/{sid}/install", json={"items": ["marzban_node"], "cert": CERT}).json()["id"])
    assert s["ok"] is False and "the certificate is not valid" in "\n".join(s["story"])


def test_an_install_that_vanished_is_a_failure_not_an_endless_wait(client, monkeypatch):
    sid = _server()
    _fast(monkeypatch, ["STATE none\n"])
    s = _wait(client, client.post(f"/api/servers/{sid}/install", json={"items": ["btblock"]}).json()["id"])
    assert s["ok"] is False and "left no result" in "\n".join(s["story"])


def test_a_restart_of_the_panel_does_not_lose_the_install(client, monkeypatch):
    from nikatel.db import session
    from nikatel.models import ActionLog

    sid = _server()
    calls = _fast(monkeypatch, ["STATE running\n", "STATE ok\nBT_OK drop_rules=3\n"])

    with session() as db:                                                       # the story of an install that was being followed when the panel stopped
        e = ActionLog(ref="install ir7", action="install", source="manual", story=["Starting…"])
        db.add(e)
        db.flush()
        aid = e.id

    st.put("pending_installs", {"items": [{"aid": aid, "server_id": sid, "items": ["btblock"], "cert": ""}]})
    extras.resume_pending()
    s = _wait(client, aid)
    text = "\n".join(s["story"])
    assert s["done"] and s["ok"] is True and "the panel was restarted; the install kept running" in text and "installed (drop rules=3)" in text
    assert calls == [] and st.get("pending_installs")["items"] == []             # it was only followed, never started a second time


def test_status_parser():
    assert extras.parse_status("STATE running\nline a\nline b\n") == ("running", "line b")
    assert extras.parse_status("STATE ok\nBT_OK drop_rules=5") == ("ok", "BT_OK drop_rules=5") and extras.parse_status("garbage") == ("none", "garbage")

def test_install_api_refuses_what_it_cannot_do(client):
    sid = _server()
    assert client.post(f"/api/servers/{sid}/install", json={"items": ["marzban_node"], "cert": "nope"}).status_code == 400   # no certificate
    assert client.post(f"/api/servers/{sid}/install", json={"items": ["rm-everything"]}).status_code == 400                    # unknown item
    assert client.post("/api/servers/99999/install", json={"items": ["btblock"]}).status_code == 400                           # no such server
    assert client.post(f"/api/servers/{sid}/install", json={"items": ["btblock"]}).status_code == 400                           # read-only in the tests


def test_default_marzban_certificate_is_kept_and_checked(client):
    assert client.get("/api/settings/nodes").json()["marzban_cert"] == ""
    assert client.put("/api/settings/nodes", json={"marzban_cert": "nope"}).status_code == 400
    r = client.put("/api/settings/nodes", json={"marzban_cert": CERT})
    assert r.status_code == 200 and r.json()["marzban_cert"].startswith("-----BEGIN CERTIFICATE-----") and "\n\n" not in r.json()["marzban_cert"]
    assert client.get("/api/settings/nodes").json()["marzban_cert"] == r.json()["marzban_cert"]
    assert client.put("/api/settings/nodes", json={"marzban_cert": ""}).json()["marzban_cert"] == ""


def test_a_wrong_password_is_remembered_as_the_servers_error(client, monkeypatch):
    import subprocess

    from nikatel.services import nodes

    sid = _server()
    monkeypatch.setattr(nodes.get_settings(), "read_only", False)          # (the key is put on a server only when the panel may change servers: 1.69.87)
    monkeypatch.setattr(nodes.shutil if hasattr(nodes, "shutil") else subprocess, "which", lambda x: "/usr/bin/" + x, raising=False)
    monkeypatch.setattr(nodes, "_pubkey", lambda: "ssh-ed25519 AAAA test")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 255, stdout="Permission denied, please try again.\n"))
    r = client.post(f"/api/servers/{sid}/connect", json={"password": "wrong"})
    assert r.status_code == 502 and "wrong user or password" in r.json()["error"]
    s = client.get(f"/api/servers/{sid}").json()
    assert s["last_error"] == "SSH login failed: wrong user or password" and s["online"] is None


def test_change_password_action_and_api(client, monkeypatch):
    from nikatel.config import get_settings
    from nikatel.services import nodes
    from nikatel.ssh import render_action

    b64 = base64.b64encode(b"root:S3cret pass:word").decode()
    script = render_action("set_password", {"USER": "root", "B64": b64})
    assert "chpasswd" in script and b64 in script and "BAD_USER" in script
    with pytest.raises(ValueError):
        render_action("set_password", {"USER": "root; reboot", "B64": b64})

    sid = _server()
    assert client.post(f"/api/servers/{sid}/password", json={"password": "short"}).status_code == 422
    seen = {}
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(nodes, "run_action", lambda node, name, params=None, timeout=90: seen.update(name=name, params=dict(params)) or "PASSWORD_SET\n")
    r = client.post(f"/api/servers/{sid}/password", json={"password": "N3w-strong-pass"})
    assert r.status_code == 200 and r.json()["user"] == "root" and seen["name"] == "set_password"
    assert base64.b64decode(seen["params"]["B64"]).decode() == "root:N3w-strong-pass"
    monkeypatch.setattr(nodes, "run_action", lambda node, name, params=None, timeout=90: "BAD_USER\n")
    assert client.post(f"/api/servers/{sid}/password", json={"password": "N3w-strong-pass"}).status_code == 502


def test_the_server_row_says_what_the_panel_is_doing(client, monkeypatch):
    from nikatel.services import nodes

    sid = _server()
    seen = []

    def spy(server_id, what="", detail=""):
        seen.append(what)

    monkeypatch.setattr(nodes, "set_busy", spy)
    monkeypatch.setattr(nodes, "_connect", lambda server_id, password: {"hostname": "h"})
    assert nodes.connect(sid, "pw") == {"hostname": "h"} and seen == ["connecting", ""]                  # set, then cleared
    monkeypatch.undo()
    nodes.set_busy(sid, "installing", "Marzban node")
    assert client.get(f"/api/servers/{sid}").json()["extra"]["busy"]["what"] == "installing"
    nodes.set_busy(sid, "")
    assert client.get(f"/api/servers/{sid}").json()["extra"]["busy"] is None


# ------------------------------------------------------------------ 1.69.9: what is on the server decides, not the folder
def test_a_node_that_runs_is_done_a_foreign_installer_is_respected_and_a_half_install_is_redone(client, monkeypatch):
    """The owner, 2026-10-19: the Marzban node did not install from the panel - the folder /opt/marzban-node was there (an installer started by hand had made it seconds before), nothing ran, and the panel said
    "already installed, nothing changed" and ended OK."""
    sid = _server()
    _fast(monkeypatch, [], start_answer="MZNODE_EXISTS\n")
    s = _wait(client, client.post(f"/api/servers/{sid}/install", json={"items": ["marzban_node"], "cert": CERT}).json()["id"])
    assert s["ok"] is True and "already installed and running on this server" in "\n".join(s["story"])
    _fast(monkeypatch, ["STATE none\n"], start_answer="MZNODE_BUSY\n")
    s = _wait(client, client.post(f"/api/servers/{sid}/install", json={"items": ["marzban_node"], "cert": CERT}).json()["id"])
    text = "\n".join(s["story"])
    assert s["ok"] is False and "another installer of the Marzban node is running" in text and "does not call it done" in text and "Done with problems" in text
    _fast(monkeypatch, ["STATE ok\nMZNODE_OK marzban-node Up 5 seconds\n"], start_answer="STARTED OVER_OLD\n")
    s = _wait(client, client.post(f"/api/servers/{sid}/install", json={"items": ["marzban_node"], "cert": CERT}).json()["id"])
    text = "\n".join(s["story"])
    assert s["ok"] is True and "the folder /opt/marzban-node is there but no node runs" in text and "installing it again over it" in text and "Marzban node: running" in text


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a shell")
@pytest.mark.parametrize("docker_says, foreign, folder, first, over", [
    ("marzban-node Up 3 minutes", False, True, "MZNODE_EXISTS", False),          # a node that runs is done - folder or not
    ("", True, True, "MZNODE_BUSY", False),                                       # nothing runs, an installer started by hand does: not started over, not done
    ("", False, True, "STARTED", True),                                           # a folder and nothing running (TR3): installed again over it
    ("", False, False, "STARTED", False),                                         # nothing there: a clean install
    ("marzban-node Exited (1) 2 minutes ago", False, True, "STARTED", True),      # a node that stopped is not a node that runs
])
def test_the_install_script_decides_by_what_runs_on_the_server(docker_says, foreign, folder, first, over, tmp_path):
    """The real script, run with stand-ins for docker / pgrep / nohup and its two folders in a temporary place: its first word and whether it will answer the installer's "override?" with y."""
    bin_dir, inst, node_dir = tmp_path / "bin", tmp_path / "inst", tmp_path / "opt-marzban-node"
    bin_dir.mkdir()

    if folder:
        node_dir.mkdir()

    stubs = {"docker": f"#!/bin/sh\n[ \"$1\" = ps ] && echo '{docker_says}'\nexit 0\n", "id": "#!/bin/sh\necho 0\n", "nohup": "#!/bin/sh\nexit 0\n",
             "pgrep": "#!/bin/sh\ncase \"$*\" in *'[m]arzban-node.sh'*) " + ("exit 0" if foreign else "exit 1") + ";; *) exit 1;; esac\n"}

    for name, text in stubs.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)

    script = render_action("install_marzban_node", {"B64": base64.b64encode(extras.check_cert(CERT).encode()).decode()}).replace("/var/lib/nikatel-install", str(inst)).replace("/opt/marzban-node", str(node_dir))
    r = subprocess.run(["sh", "-s"], input=script, env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, capture_output=True, text=True, timeout=30, check=False)
    assert r.stdout.split()[0] == first, r.stdout + r.stderr
    assert ("OVER_OLD" in r.stdout) == over

    if first == "STARTED":
        feed = (inst / "mznode.sh").read_text()
        assert "[ -d " + str(node_dir) + " ] && echo y; cat mznode.cert" in feed          # the answer to "override the previous installation?" comes before the certificate
