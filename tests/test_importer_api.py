import json
from pathlib import Path

from nikatel.db import session
from nikatel.models import Tunnel
from nikatel.services.importer import import_seed_dir


def _seed(tmp_path: Path) -> Path:
    (tmp_path / "iran-servers.json").write_text(json.dumps([{"name": "ir1", "host": "1.1.1.1", "ssh_port": 22, "provider": "X", "asn": "AS1"}]))
    (tmp_path / "foreign-servers.json").write_text(json.dumps({"servers": [{"name": "FI", "host": "2.2.2.2"}, {"name": "NL", "host": "3.3.3.3"}]}))
    (tmp_path / "tunnels.json").write_text(json.dumps({"tunnels": [
        {"name": "fi-ir1", "type": "backhaul", "transport": "tcpmux", "port": 9443, "iran_host": "1.1.1.1", "foreign_host": "2.2.2.2",
         "iran_tunnel_ip": "10.2.0.1", "foreign_tunnel_ip": "10.2.0.2", "forwarded_ports": [80], "token": "SECRET", "test_key": "K", "quality": {"x": 1}, "mtu": 1400},
        {"name": "orphan", "type": "backhaul", "iran_host": "9.9.9.9", "foreign_host": "2.2.2.2"}]}))
    return tmp_path


def test_import_is_idempotent_and_hides_secrets(tmp_path):
    seed = _seed(tmp_path)
    r1 = import_seed_dir(seed)
    assert r1["iran"] == (1, 0) and r1["foreign"] == (2, 0)
    assert r1["tunnels"][0] == 1 and r1["tunnels"][2] == ["orphan"]

    r2 = import_seed_dir(seed)
    assert r2["iran"] == (0, 1) and r2["tunnels"][:2] == (0, 1)

    with session() as db:
        t = db.query(Tunnel).filter_by(name="fi-ir1").one()
        assert t.secrets == {"token": "SECRET", "test_key": "K"}
        assert "token" not in t.record and "quality" not in t.record and t.record["mtu"] == 1400
        assert t.iran.name == "ir1" and t.foreign.name == "FI"


def test_api_lists_without_secrets(client, tmp_path):
    import_seed_dir(_seed(tmp_path))
    r = client.get("/api/tunnels")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["name"] == "fi-ir1" and body[0]["iran_name"] == "ir1"
    assert "SECRET" not in r.text and "secrets" not in body[0]
    assert body[0]["units"]["iran"][0] == "backhaul-iran-tcpmux-9443.service"

    r = client.get("/api/servers?role=foreign")
    assert [s["name"] for s in r.json()] == ["FI", "NL"]

    r = client.get("/api/overview")
    assert r.json()["tunnels"]["total"] == 1 and r.json()["read_only"] is True


def test_auth_required(tmp_path):
    from fastapi.testclient import TestClient

    from nikatel.main import create_app

    with TestClient(create_app(run_jobs=False)) as c:
        assert c.get("/api/servers").status_code == 401
        assert c.get("/api/health").status_code == 200
        assert c.post("/api/auth/login", json={"user": "admin", "password": "nope"}).status_code == 401
