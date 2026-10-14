"""The Install view of a server: a Marzban or PasarGuard node, the Abuse-Defender, the update of the system packages."""
import pytest

from nikatel import ssh
from nikatel.services import extras


def _add(client, name, host, role="foreign"):
    r = client.post("/api/servers", json={"name": name, "host": host, "role": role})
    assert r.status_code == 201, r.text
    return r.json()["id"]


NODE_KEY = "123e4567-e89b-12d3-a456-426614174000"


def test_the_pasarguard_install_is_the_documented_command_and_answers_with_flags_not_with_stdin():
    script = ssh.render_action("install_pasarguard_node", {"KEY": NODE_KEY})
    assert "PasarGuard/scripts/raw/main/pg-node.sh" in script and '@ install' in script and "shred" in script
    assert NODE_KEY in script                                       # the key is written to a 0600 file the installer reads it from ...
    # ... and every other answer is a flag: the questions of the installer change from one release to the next, so a stdin answer lands on the wrong one
    for flag in ("-y", "--api-key", "--self-signed", "--use-grpc", "--service-port 62050", "--install-service", "--api-port 62051"):
        assert f" {flag}" in script, flag
    assert "printf '\\n\\n\\n\\n'" not in script and "< /dev/null" in script
    for bad in ("short", "has space in it", "x'; reboot; '", "a" * 300, "abcd1234efgh", "123e4567-e89b-12d3-a456-42661417400"):     # only a UUID is accepted (the installer refuses anything else)
        with pytest.raises(ValueError):
            ssh.render_action("install_pasarguard_node", {"KEY": bad})


def test_the_update_of_the_system_is_apt_without_questions_and_never_reboots():
    script = ssh.render_action("install_os_update", {})
    assert "apt-get update" in script and "upgrade" in script and "force-confold" in script and "reboot-required" in script
    assert "reboot\n" not in script and "shutdown" not in script


def test_the_status_script_knows_the_new_installs():
    for name in ("pgnode", "osupdate", "mznode", "btblock"):
        assert ssh.SCRIPTS["install_status"][1]["NAME"] and "STATE" in ssh.render_action.__globals__["SCRIPTS"]["install_status"][0]
        import re
        assert re.match(ssh.SCRIPTS["install_status"][1]["NAME"], name)

    import re
    assert not re.match(ssh.SCRIPTS["install_status"][1]["NAME"], "other")


def test_the_install_request_is_checked_before_anything_starts(client):
    sid = _add(client, "fr-i", "198.51.100.31")
    r = client.post(f"/api/servers/{sid}/install", json={"items": ["pasarguard_node"], "cert": ""})
    assert r.status_code == 400 and "API key" in r.text
    r = client.post(f"/api/servers/{sid}/install", json={"items": ["pasarguard_node", "os_update"], "cert": NODE_KEY})
    assert r.status_code == 400 and "on its own" in r.text
    r = client.post(f"/api/servers/{sid}/install", json={"items": ["marzban_node"], "cert": "not a certificate"})
    assert r.status_code == 400
    assert set(extras.ITEMS) == {"btblock", "marzban_node", "pasarguard_node", "os_update"} and all(extras.LABEL[i] and extras.KEY[i] and extras.ACTION[i] for i in extras.ITEMS)


def test_the_certificate_of_the_node_is_shown_only_when_there_is_one(client, monkeypatch):
    sid = _add(client, "fr-i", "198.51.100.31")
    monkeypatch.setattr(extras, "run_script", lambda *a, **k: "NO_CERT")
    assert client.get(f"/api/servers/{sid}/pasarguard-cert").status_code == 400
    monkeypatch.setattr(extras, "run_script", lambda *a, **k: "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    r = client.get(f"/api/servers/{sid}/pasarguard-cert").json()
    assert r["ok"] and r["cert"].startswith("-----BEGIN CERTIFICATE-----")


def test_the_console_has_the_plain_questions(client):
    keys = {p["key"] for p in client.get("/api/nodes/presets").json()["presets"]}
    assert {"health", "failed", "logs", "top", "disk", "net"} <= keys
