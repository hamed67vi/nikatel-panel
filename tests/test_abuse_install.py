"""The Abuse-Defender install on a server without cron (the owner, 2026-10-19: "when I install Abuse-Defender it gives an error": `bash: line 45: crontab: command not found`).
A minimal server has no `crontab`; the script put the rules in and then died on the line that writes the nightly refresh. It now installs cron first, and when there is none still, a systemd timer does the refresh."""
import shutil
import subprocess

import pytest

from nikatel.ssh import render_action

PARAMS = {"URL": "https://raw.githubusercontent.com/example/lists/main/abuse.txt", "WL": "1.1.1.1"}


def test_the_install_gets_cron_before_it_needs_it_and_has_a_timer_when_there_is_none():
    s = render_action("abuse_install", PARAMS)
    assert s.index("command -v crontab") < s.index("curl -fsS --max-time 25")                                             # cron is looked for (and installed) before the list is fetched and any rule is put in
    assert "apt-get install -y cron" in s and "systemctl enable --now cron" in s
    assert 'echo "0 0 * * * /root/abuse-defender-update.sh"' in s and s.count("| crontab -") == 2                      # (the cron line stays: with cron, nothing changes; the first `| crontab -` is the cleanup of the old lines)
    assert "abuse-defender-update.timer" in s and "OnCalendar=*-*-* 00:00:00" in s and "ExecStart=/root/abuse-defender-update.sh" in s
    assert "systemctl disable --now abuse-defender-update.timer" in s                                                       # (a fresh install removes an earlier timer, like the earlier cron lines)


def test_the_remove_takes_the_timer_away_too():
    s = render_action("abuse_remove", {})
    assert "systemctl disable --now abuse-defender-update.timer" in s and "rm -f /etc/systemd/system/abuse-defender-update.service" in s


@pytest.mark.parametrize("action,params", [("abuse_install", PARAMS), ("abuse_remove", {})])
def test_the_scripts_are_valid_shell(action, params, tmp_path):
    bash = shutil.which("bash")

    if bash is None:
        pytest.skip("bash is not installed here")

    f = tmp_path / "s.sh"
    f.write_text(render_action(action, params), encoding="utf-8")
    r = subprocess.run([bash, "-n", str(f)], capture_output=True, text=True, timeout=30, check=False)
    assert r.returncode == 0, r.stderr[-400:]


def _wait(client, aid):
    import time

    end = time.time() + 20

    while time.time() < end:
        s = client.get(f"/api/actions/{aid}").json()

        if s.get("done"):
            return s

        time.sleep(0.1)

    raise AssertionError("the action did not finish")


def _servers(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    client.post("/api/servers", json={"name": "ir-ab", "host": "1.1.1.1", "role": "iran"})
    client.post("/api/servers", json={"name": "fi-ab", "host": "2.2.2.2", "role": "foreign"})
    return {s["name"]: s["id"] for s in client.get("/api/servers").json()}


def test_the_install_is_an_action_whose_log_the_page_follows_in_a_box(client, monkeypatch):
    """The owner, 2026-10-19: "when a node or Abuse-Defender or anything is installed, put its log in the box" (the result was a toast)."""
    from nikatel.services import nodes

    ids = _servers(client, monkeypatch)
    out = "STEP 1/5 packages checked: iptables, curl, cron\nSTEP 2/5 the list was downloaded (4321 lines)\nSTEP 5/5 the list is refreshed every night by cron\nABUSE_OK=4321\n"
    monkeypatch.setattr(nodes, "run_action", lambda node, name, params=None, timeout=90: out)
    r = client.post(f"/api/servers/{ids['fi-ab']}/abuse", json={"action": "install", "stream": True})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["id"]
    s = _wait(client, r.json()["id"])
    story = "\n".join(s["story"])
    assert s["ok"] is True and "installing on fi-ab" in story and "2/5 the list was downloaded (4321 lines)" in story and "5/5 the list is refreshed every night by cron" in story
    assert s["story"][-1] == "Done: Abuse-Defender is on, 4321 ranges are blocked."
    assert client.get(f"/api/servers/{ids['fi-ab']}").json()["extra"]["abuse"]["installed"] is True
    assert nodes.abuse_steps(out) == ["1/5 packages checked: iptables, curl, cron", "2/5 the list was downloaded (4321 lines)", "5/5 the list is refreshed every night by cron"]


def test_what_the_install_failed_with_is_in_the_log_not_in_a_toast(client, monkeypatch):
    from nikatel.services import nodes
    from nikatel.ssh import SSHError

    ids = _servers(client, monkeypatch)

    def boom(node, name, params=None, timeout=90):
        raise SSHError("bash: line 45: crontab: command not found · id c7286b")

    monkeypatch.setattr(nodes, "run_action", boom)
    r = client.post(f"/api/servers/{ids['fi-ab']}/abuse", json={"action": "install", "stream": True})
    s = _wait(client, r.json()["id"])
    assert s["ok"] is False and s["story"][-1] == "Failed: bash: line 45: crontab: command not found · id c7286b"
    assert "abuse" not in client.get(f"/api/servers/{ids['fi-ab']}").json()["extra"]                                          # (nothing is written down as installed)
    assert client.post(f"/api/servers/{ids['ir-ab']}/abuse", json={"action": "install", "stream": True}).status_code == 400        # never on an Iran server, said at once
    assert client.post(f"/api/servers/{ids['fi-ab']}/abuse", json={"action": "update", "stream": True}).status_code == 400
