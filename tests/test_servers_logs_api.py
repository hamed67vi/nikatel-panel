import json
import logging

from nikatel.config import get_settings
from nikatel.log import setup
from nikatel.services.importer import import_seed_dir


def test_server_add_edit_disable_delete(client):
    r = client.post("/api/servers", json={"name": "ir9", "host": "9.9.9.9", "role": "iran"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    assert client.post("/api/servers", json={"name": "other", "host": "9.9.9.9", "role": "iran"}).status_code == 409
    assert client.post("/api/servers", json={"name": "bad name!", "host": "1.1.1.1", "role": "iran"}).status_code == 422
    assert client.post("/api/servers", json={"name": "x", "host": "1.1.1.1; id", "role": "iran"}).status_code == 422

    r = client.put(f"/api/servers/{sid}", json={"name": "ir9", "host": "9.9.9.9", "role": "iran", "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False

    ev = client.get("/api/events?domain=servers").json()["events"]
    assert any("disabled" in e["text"] for e in ev) and any("added" in e["text"] for e in ev)

    assert client.delete(f"/api/servers/{sid}").status_code == 200
    assert client.get(f"/api/servers/{sid}").status_code == 404


def test_delete_refused_when_a_tunnel_uses_it(client, tmp_path):
    (tmp_path / "iran-servers.json").write_text(json.dumps([{"name": "ir1", "host": "1.1.1.1"}]))
    (tmp_path / "foreign-servers.json").write_text(json.dumps([{"name": "fi1", "host": "2.2.2.2"}]))
    (tmp_path / "tunnels.json").write_text(json.dumps({"tunnels": [{"name": "t1", "type": "backhaul", "iran_host": "1.1.1.1", "foreign_host": "2.2.2.2"}]}))
    import_seed_dir(tmp_path)
    sid = client.get("/api/servers?role=iran").json()[0]["id"]
    r = client.delete(f"/api/servers/{sid}")
    assert r.status_code == 409 and "t1" in r.json()["detail"]


def test_forget_hostkey_touches_only_our_file(client):
    kh = get_settings().known_hosts
    kh.parent.mkdir(parents=True, exist_ok=True)
    kh.write_text("9.9.9.9 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
                  "3.3.3.3 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB\n")
    sid = client.post("/api/servers", json={"name": "ir9", "host": "9.9.9.9", "role": "iran"}).json()["id"]
    assert client.post(f"/api/servers/{sid}/forget-hostkey").status_code == 200
    left = kh.read_text()
    assert "9.9.9.9" not in left and "3.3.3.3" in left


def test_logs_endpoint_filters_and_masks(client):
    s = get_settings()
    setup("INFO", s.log_dir)
    logging.getLogger("tunnels").info("check name=t1 state=up")
    logging.getLogger("auth").warning("login failed user=x password=not-a-real-password")

    for h in logging.getLogger().handlers:
        h.flush()

    r = client.get("/api/logs?lines=50")
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert lines and lines[0]["domain"] == "auth" and lines[0]["level"] == "WARNING"
    assert "not-a-real-password" not in r.text and "<hidden>" in r.text

    only = client.get("/api/logs?domain=tunnels").json()["lines"]
    assert only and all(x["domain"] == "tunnels" for x in only)
    assert client.get("/api/logs?q=no-such-text-anywhere").json()["lines"] == []


def test_settings_view_has_no_secrets(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["read_only"] is True and "tunnel_check_interval" in body
    assert "secret_key" not in body and "ssh_key" not in body
