"""The full check of the servers and nodes (2026-10-22): a renamed server keeps its alert mute and alert state, a deleted one takes its mute away, a role is not changed under a tunnel, an address is the same address in
capitals and with a dot at its end, a double click on Add server is a 409 and not a 500, Connect with a password keeps the read-only rule, and the page is not wider than the window."""
from pathlib import Path

from nikatel.db import session
from nikatel.models import AlertState, Tunnel
from nikatel.services import builder, nodes
from nikatel.services import settings as st

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def _pair(client, tag, a, b):
    ir = client.post("/api/servers", json={"name": f"ir-{tag}", "host": f"203.0.113.{a}", "role": "iran"}).json()
    fo = client.post("/api/servers", json={"name": f"fo-{tag}", "host": f"198.51.100.{b}", "role": "foreign"}).json()
    return ir, fo


def _tunnel(ir, fo, name):
    with session() as db:
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", iran_server_id=ir["id"], foreign_server_id=fo["id"], state="up", enabled=True, forwarded_ports=[8075])
        db.add(t)
        db.flush()
        return t.id


def _body(s, **kw):
    return {"name": s["name"], "host": s["host"], "role": s["role"], "ssh_user": "root", "ssh_port": 22, "provider": "", "enabled": True, **kw}


def test_a_renamed_server_keeps_its_mute_and_its_alert_state(client):
    _ir, fo = _pair(client, "rn", 31, 31)
    st.put("alerts", {"muted": [fo["name"], "other"]})

    with session() as db:
        db.add(AlertState(kind="server", ref=fo["name"], alerted=True, bad_since=100))
        db.add(AlertState(kind="server", ref="fo-rn2", alerted=False))          # (what an old server of the new name left behind)

    r = client.put(f"/api/servers/{fo['id']}", json=_body(fo, name="fo-rn2"))
    assert r.status_code == 200, r.text
    assert st.get("alerts")["muted"] == ["fo-rn2", "other"]

    with session() as db:
        rows = {a.ref: a for a in db.query(AlertState).filter(AlertState.kind == "server")}

    assert list(rows) == ["fo-rn2"] and rows["fo-rn2"].alerted is True and rows["fo-rn2"].bad_since == 100


def test_a_rename_with_a_muted_tunnel_and_a_muted_server_does_not_lock_the_database(client):
    ir, fo = _pair(client, "rl", 32, 32)
    old = builder.pretty_name(fo["name"], ir["name"], set())
    _tunnel(ir, fo, old)
    st.put("alerts", {"muted": [old, fo["name"]]})
    r = client.put(f"/api/servers/{fo['id']}", json=_body(fo, name="fo-rl2"))
    assert r.status_code == 200, r.text
    muted = st.get("alerts")["muted"]
    assert "fo-rl2" in muted and fo["name"] not in muted and old not in muted and len(muted) == 2


def test_a_deleted_server_takes_its_mute_with_it(client):
    _ir, fo = _pair(client, "dl", 33, 33)
    st.put("alerts", {"muted": [fo["name"], "other"]})
    assert client.delete(f"/api/servers/{fo['id']}").status_code == 200
    assert st.get("alerts")["muted"] == ["other"]
    again = client.post("/api/servers", json={"name": fo["name"], "host": "198.51.100.133", "role": "foreign"})
    assert again.status_code == 201 and fo["name"] not in st.get("alerts")["muted"]


def test_the_role_of_a_server_under_a_tunnel_is_not_changed(client):
    ir, fo = _pair(client, "rc", 34, 34)
    _tunnel(ir, fo, "rc-tun")
    r = client.put(f"/api/servers/{fo['id']}", json=_body(fo, role="iran"))
    assert r.status_code == 409 and "rc-tun" in r.json()["error"] and "delete the tunnel" in r.json()["error"]
    assert client.get(f"/api/servers/{fo['id']}").json()["role"] == "foreign"
    assert client.put(f"/api/servers/{fo['id']}", json=_body(fo, provider="x")).status_code == 200          # (an edit that keeps the role is fine)
    lone = client.post("/api/servers", json={"name": "lone", "host": "203.0.113.134", "role": "iran"}).json()
    assert client.put(f"/api/servers/{lone['id']}", json=_body(lone, role="foreign")).json()["role"] == "foreign"      # (no tunnel: the owner may correct the role)


def test_an_address_is_the_same_address_in_capitals_and_with_a_dot(client):
    assert client.post("/api/servers", json={"name": "c1", "host": "Example.COM", "role": "foreign"}).status_code == 201
    for host in ("example.com", "EXAMPLE.com.", "example.com."):
        r = client.post("/api/servers", json={"name": "c2", "host": host, "role": "foreign"})
        assert r.status_code == 409 and "c1" in r.json()["error"], host

    assert client.post("/api/servers", json={"name": "c3", "host": "example.org", "role": "foreign"}).status_code == 201
    c3 = client.get("/api/servers").json()[-1]
    assert client.put(f"/api/servers/{c3['id']}", json=_body(c3, host="EXAMPLE.com")).status_code == 409
    assert client.put(f"/api/servers/{c3['id']}", json=_body(c3, host="EXAMPLE.org")).status_code == 200          # (its own address in other letters: itself, not a second one)


def test_two_adds_of_one_name_at_once_are_a_409_not_a_500(client, monkeypatch):
    client.post("/api/servers", json={"name": "race", "host": "203.0.113.40", "role": "iran"})
    monkeypatch.setattr("nikatel.api.servers.server_conflict", lambda *a, **k: "")          # (both requests passed the check before either wrote)
    r = client.post("/api/servers", json={"name": "race", "host": "203.0.113.41", "role": "iran"})
    assert r.status_code == 409 and "moment ago" in r.json()["error"]


def test_connect_with_a_password_keeps_the_read_only_rule(client, monkeypatch):
    s = client.post("/api/servers", json={"name": "ro1", "host": "203.0.113.42", "role": "foreign"}).json()
    called = []
    monkeypatch.setattr(nodes, "_pubkey", lambda: "ssh-ed25519 AAAA test")
    monkeypatch.setattr(nodes.subprocess, "run", lambda *a, **k: called.append(a))
    r = client.post(f"/api/servers/{s['id']}/connect", json={"password": "secret-pass"})
    assert r.status_code == 502 and "read-only" in r.json()["error"] and not called
    assert (client.get(f"/api/servers/{s['id']}").json()["extra"] or {}).get("busy") is None          # (no "connecting" mark was left on the row)


def test_add_many_checks_the_port_and_the_length(client, monkeypatch):
    monkeypatch.setattr(nodes, "run_script", lambda *a, **k: "HOST=good")          # (the one good line is "reached")
    assert client.post("/api/servers/add-many", json={"role": "foreign", "lines": "x 203.0.113.50", "ssh_port": 70000}).status_code == 422
    out = client.post("/api/servers/add-many", json={"role": "foreign", "lines": "okm 203.0.113.51 root 99999\nlong " + "a" * 130 + ".example.com\ngood 203.0.113.52"}).json()["items"]
    assert [i["ok"] for i in out] == [False, False, True] and "port" in out[0]["error"] and "128" in out[1]["error"]
    assert [s["name"] for s in client.get("/api/servers").json()] == ["good"]


def test_a_typo_in_the_forged_address_is_told_as_a_typo(client):
    s = client.post("/api/servers", json={"name": "sp1", "host": "203.0.113.60", "role": "iran"}).json()
    client.post("/api/servers", json={"name": "sp2", "host": "203.0.113.61", "role": "foreign"})
    r = client.post(f"/api/servers/{s['id']}/spoof-test", json={"spoof_ip": "not-an-ip"})
    assert r.status_code == 400 and "IPv4 address" in r.json()["error"] and "no target" not in r.json()["error"]
    assert "ordinary address" in client.post(f"/api/servers/{s['id']}/spoof-test", json={"spoof_ip": "127.0.0.1"}).json()["error"]


def test_onboard_without_a_step_is_refused(client):
    s = client.post("/api/servers", json={"name": "ob1", "host": "203.0.113.62", "role": "iran"}).json()
    r = client.post(f"/api/servers/{s['id']}/onboard", json={"steps": []})
    assert r.status_code == 400 and "at least one step" in r.json()["error"]


def test_the_page_is_not_wider_than_the_window():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    rule = next(ln for ln in css.splitlines() if ".main:has(.lbox) {" in ln)
    assert "overflow: clip" in rule and "min-width: 0" in rule          # (a clipped box has no automatic minimum of 0: without this the grid column grew to the widest nowrap text, 1385 px, at windows of 1200-1536 px)
