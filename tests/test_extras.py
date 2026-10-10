"""Install after adding a server: the reviewed actions, the certificate check, the story, the API."""
import base64
import time

import pytest

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import extras
from nikatel.ssh import render_action

CERT = "-----BEGIN CERTIFICATE-----\nMIIBdemo\n\n-----END CERTIFICATE-----\n"


def test_actions_render_exactly_what_was_asked():
    bt = render_action("install_btblock", {})
    assert "Heclalava/blockpublictorrent-iptables/raw/main/bt.sh" in bt and "bash bt.sh" in bt and "/etc/cron.daily/denypublic" in bt
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


def test_install_story_reports_each_item(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    sid = _server()
    calls = []

    def fake(node, name, params=None, timeout=90):
        calls.append(name)
        return {"install_btblock": "BT_OK drop_rules=41\n", "install_marzban_node": "MZNODE_OK marzban-node-marzban-node-1 Up 5 seconds\n"}[name]

    monkeypatch.setattr(extras, "run_action", fake)
    r = client.post(f"/api/servers/{sid}/install", json={"items": ["btblock", "marzban_node"], "cert": CERT})
    assert r.status_code == 200, r.text

    for _ in range(60):
        s = client.get(f"/api/actions/{r.json()['id']}").json()
        if s["done"]:
            break
        time.sleep(0.1)

    text = "\n".join(s["story"])
    assert s["done"] and s["ok"] is True and calls == ["install_btblock", "install_marzban_node"]
    assert "installed (drop rules=41)" in text and "address 203.0.113.77, port 62050" in text


def test_a_failed_node_install_says_why(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    sid = _server()
    monkeypatch.setattr(extras, "run_action", lambda node, name, params=None, timeout=90: "BAD_CERT\n")
    r = client.post(f"/api/servers/{sid}/install", json={"items": ["marzban_node"], "cert": CERT})

    for _ in range(60):
        s = client.get(f"/api/actions/{r.json()['id']}").json()
        if s["done"]:
            break
        time.sleep(0.1)

    assert s["done"] and s["ok"] is False and "the certificate is not valid" in "\n".join(s["story"])


def test_install_api_refuses_what_it_cannot_do(client):
    sid = _server()
    assert client.post(f"/api/servers/{sid}/install", json={"items": ["marzban_node"], "cert": "nope"}).status_code == 400   # no certificate
    assert client.post(f"/api/servers/{sid}/install", json={"items": ["rm-everything"]}).status_code == 400                    # unknown item
    assert client.post("/api/servers/99999/install", json={"items": ["btblock"]}).status_code == 400                           # no such server
    assert client.post(f"/api/servers/{sid}/install", json={"items": ["btblock"]}).status_code == 400                           # read-only in the tests
