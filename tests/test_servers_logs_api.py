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
    assert r.status_code == 409 and "t1" in r.json()["error"]


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


def test_logs_say_where_a_problem_is_and_summarise_the_day(client):
    s = get_settings()
    setup("INFO", s.log_dir)
    before = client.get("/api/logs/summary?hours=24").json()["by_origin"]      # the log file is shared with the tests that ran before
    logging.getLogger("ssh").warning("ssh 203.0.113.5 exit 255: ssh: connect to host 203.0.113.5 port 22: Connection timed out")
    logging.getLogger("ssh").warning("ssh 198.51.100.9 exit 255: ssh: connect to host 198.51.100.9 port 22: Connection timed out")
    logging.getLogger("jobs").error("job alerts failed: database is locked")
    logging.getLogger("tunnels").info("check name=t1 state=up")

    for h in logging.getLogger().handlers:
        h.flush()

    rows = client.get("/api/logs?lines=50").json()["lines"]
    assert {r["origin"] for r in rows if r["level"] == "INFO"} == {""}

    outside = client.get("/api/logs?origin=outside&lines=2000").json()["lines"]
    assert len(outside) >= 2 and all(r["origin"] == "outside" for r in outside) and "203.0.113.5" in outside[1]["text"] + outside[0]["text"]
    panel = client.get("/api/logs?origin=panel&lines=2000").json()["lines"]
    assert panel and panel[0]["text"].startswith("job alerts failed") and all(r["origin"] == "panel" for r in panel)
    assert client.get("/api/logs?origin=nonsense").status_code == 422

    sm = client.get("/api/logs/summary?hours=24").json()
    assert sm["by_origin"]["outside"] - before["outside"] == 2 and sm["by_origin"]["panel"] - before["panel"] == 1      # the 422 just above is a plain answer, not a finding
    assert sm["total"] == sum(sm["by_origin"].values()) and sm["groups"][0]["origin"] == "panel" and all(g["hint"] for g in sm["groups"])
    assert client.get("/api/logs/summary?hours=0").status_code == 422


def test_settings_view_has_no_secrets(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["read_only"] is True and "tunnel_check_interval" in body
    assert "secret_key" not in body and "ssh_key" not in body


def test_gre_check_all_counts_every_server_and_survives_a_dead_one(client, monkeypatch):
    from nikatel.services import nodes
    from nikatel.ssh import SSHError

    for n, role in (("a1", "iran"), ("a2", "iran"), ("a3", "iran"), ("f1", "foreign")):
        assert client.post("/api/servers", json={"name": n, "host": f"9.9.9.{ord(n[0]) + ord(n[1])}", "role": role}).status_code == 201

    def fake(sid):
        name = {s["id"]: s["name"] for s in client.get("/api/servers").json()}.get(sid, "")

        if name == "a1":
            return {"ok": True, "links": 2, "bad": [], "detail": "2 GRE link(s) answer"}

        if name == "a2":
            return {"ok": False, "links": 1, "bad": ["gre-x"], "detail": "GRE not OK: gre-x"}

        raise SSHError("ssh timeout")

    monkeypatch.setattr(nodes, "gre_check", fake)
    r = client.post("/api/servers/gre-check-all?role=iran")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total"] == 3 and j["ok"] == 1 and j["no_gre"] == 0
    assert any("a2" in b for b in j["bad"]) and any("a3" in b and "check failed" in b for b in j["bad"])
    assert client.post("/api/servers/gre-check-all?role=foreign").json()["total"] == 1

def test_events_say_how_many_there_are_in_all(client):
    from nikatel.db import session
    from nikatel.services.events import emit

    with session() as db:
        for i in range(5):
            emit(db, "info", "servers", f"sample {i}", "x")

    r = client.get("/api/events?limit=2&domain=servers").json()
    assert len(r["events"]) == 2 and r["total"] >= 5
