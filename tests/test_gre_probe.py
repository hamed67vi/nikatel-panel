"""The GRE test: does GRE work TO a server (an Iran server builds a temporary link, pings the inner address, removes the link) - not 'does it have a GRE link'."""
import pytest

from nikatel import ssh
from nikatel.services import nodes
from nikatel.ssh import SSHError


def _add(client, name, host, role):
    r = client.post("/api/servers", json={"name": name, "host": host, "role": role})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def calls(monkeypatch):
    log = []

    def fake_action(node, name, params=None, timeout=90):
        log.append((name, node.host, dict(params or {})))
        return "ok"

    monkeypatch.setattr(nodes, "run_action", fake_action)
    return log


def test_it_passes_when_the_pings_come_back_and_the_link_is_removed_on_both(client, monkeypatch, calls):
    ir = _add(client, "ir-a", "203.0.113.11", "iran")
    fr = _add(client, "fr-a", "198.51.100.21", "foreign")
    monkeypatch.setattr(nodes, "run_script", lambda node, name, params=None, timeout=40: "10 packets transmitted, 10 received, 0% packet loss")
    r = client.post(f"/api/servers/{fr}/gre-probe").json()
    assert r["ok"] is True and r["via"] == "ir-a" and r["links"] == 1 and r["mode"] == "probe"
    ups = [c for c in calls if c[0] == "gre_probe_up"]
    downs = [c for c in calls if c[0] == "gre_probe_down"]
    assert {c[1] for c in ups} == {"203.0.113.11", "198.51.100.21"} and {c[1] for c in downs} == {"203.0.113.11", "198.51.100.21"}
    assert ups[0][2]["LOCAL"] == "203.0.113.11" and ups[0][2]["REMOTE"] == "198.51.100.21" and ups[0][2]["ADDR"].startswith("10.253.")
    assert ups[0][2]["NAME"] == downs[0][2]["NAME"] and ups[0][2]["KEY"] == ups[1][2]["KEY"]            # both ends of ONE link
    assert client.get(f"/api/servers/{fr}").json()["extra"]["gre"]["ok"] is True
    assert ir


def test_it_fails_with_a_second_look_from_another_iran_server(client, monkeypatch, calls):
    _add(client, "ir-a", "203.0.113.11", "iran")
    _add(client, "ir-b", "203.0.113.12", "iran")
    fr = _add(client, "fr-a", "198.51.100.21", "foreign")
    monkeypatch.setattr(nodes, "run_script", lambda node, name, params=None, timeout=40: "10 packets transmitted, 0 received, 100% packet loss")
    r = client.post(f"/api/servers/{fr}/gre-probe").json()
    assert r["ok"] is False and r["links"] == 2 and r["bad"] == ["ir-a", "ir-b"] and "protocol 47" in r["detail"]
    assert len([c for c in calls if c[0] == "gre_probe_down"]) == 4                        # nothing is left on any of the four ends
    extra = client.get(f"/api/servers/{fr}").json()["extra"]["gre"]
    assert extra["ok"] is False and extra["links"] == 2                                       # the list shows its GRE mark from this


def test_the_link_is_removed_even_when_the_target_cannot_be_reached(client, monkeypatch):
    _add(client, "ir-a", "203.0.113.11", "iran")
    fr = _add(client, "fr-a", "198.51.100.21", "foreign")
    seen = []

    def action(node, name, params=None, timeout=90):
        seen.append((name, node.host))

        if name == "gre_probe_up" and node.host == "198.51.100.21":
            raise SSHError("ssh connection lost")

        return "ok"

    monkeypatch.setattr(nodes, "run_action", action)
    monkeypatch.setattr(nodes, "run_script", lambda *a, **k: "")
    r = client.post(f"/api/servers/{fr}/gre-probe").json()
    assert r["ok"] is False and "could not run" in r["detail"]
    assert ("gre_probe_down", "203.0.113.11") in seen and ("gre_probe_down", "198.51.100.21") in seen


def test_an_iran_server_is_tested_from_another_iran_server_and_needs_one(client, monkeypatch, calls):
    a = _add(client, "ir-a", "203.0.113.11", "iran")
    r = client.post(f"/api/servers/{a}/gre-probe")
    assert r.status_code >= 400 and "no other Iran server" in r.text
    _add(client, "ir-b", "203.0.113.12", "iran")
    monkeypatch.setattr(nodes, "run_script", lambda *a, **k: "10 packets transmitted, 9 received")
    assert client.post(f"/api/servers/{a}/gre-probe").json()["via"] == "ir-b"


def test_the_two_actions_are_validated_and_touch_only_their_own_interface():
    script = ssh.render_action("gre_probe_up", {"NAME": "nkgp7", "LOCAL": "203.0.113.11", "REMOTE": "198.51.100.21", "KEY": "123456", "ADDR": "10.253.4.1/30"})
    assert "ip tunnel add" in script and "nikatel-gre-probe-" in script and "nkgp7" in script
    for bad in ({"NAME": "eth0"}, {"ADDR": "192.168.1.1/30"}, {"LOCAL": "x; reboot"}, {"KEY": "1;2"}):
        params = {"NAME": "nkgp7", "LOCAL": "203.0.113.11", "REMOTE": "198.51.100.21", "KEY": "123456", "ADDR": "10.253.4.1/30", **bad}

        with pytest.raises(ValueError):
            ssh.render_action("gre_probe_up", params)

    with pytest.raises(ValueError):
        ssh.render_action("gre_probe_down", {"NAME": "wg0"})
