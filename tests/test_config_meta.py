import base64
import json

from nikatel.services import configs, probe


def test_meta_from_links():
    m = configs.meta_from_uri("vless://id@sub.example.net:8443?type=ws&security=tls&path=%2Fx#Name")
    assert m == {"proto": "vless", "address": "sub.example.net", "port": 8443, "network": "ws", "security": "tls"}
    assert configs.meta_from_uri("vless://id@203.0.113.9:443?type=grpc&security=reality&sni=a.b#n")["security"] == "reality"
    assert configs.meta_from_uri("vless://id@203.0.113.9:443#n")["network"] == "tcp" and configs.meta_from_uri("vless://id@203.0.113.9:443#n")["security"] == "none"
    assert configs.meta_from_uri("trojan://pw@host.example:443?type=tcp#n")["security"] == "tls"
    raw = base64.b64encode(json.dumps({"add": "h.example", "port": "2096", "net": "ws", "tls": "tls", "ps": "x"}).encode()).decode()
    assert configs.meta_from_uri("vmess://" + raw) == {"proto": "vmess", "address": "h.example", "port": 2096, "network": "ws", "security": "tls"}
    ss = base64.urlsafe_b64encode(b"aes-256-gcm:secret").decode().rstrip("=")
    m = configs.meta_from_uri(f"ss://{ss}@198.51.100.7:8388#n")
    assert m["proto"] == "ss" and m["address"] == "198.51.100.7" and m["port"] == 8388 and "secret" not in str(m)
    assert configs.meta_from_uri("garbage")["port"] == 0


def test_meta_is_filled_from_the_probe(client, monkeypatch):
    monkeypatch.setattr(probe, "add_config", lambda uri, push_url="": {"ok": True})
    assert client.post("/api/configs", json={"uri": "vless://u@h.example:443?type=ws&security=tls#A", "group": "g"}).status_code == 201
    c = client.get("/api/configs").json()["configs"][0]
    assert c["meta"]["address"] == "h.example" and c["meta"]["network"] == "ws" and c["meta"]["security"] == "tls"
    # a config that appeared on the probe by itself gets its facts from the probe's copy of the link
    from nikatel.db import session
    from nikatel.models import Config

    with session() as db:
        db.add(Config(name="Other", status="unknown"))

    monkeypatch.setattr(probe, "config_uri", lambda name: "trojan://p@t.example:2087?type=tcp#Other")
    assert configs.fill_meta() == 1
    other = next(x for x in client.get("/api/configs").json()["configs"] if x["name"] == "Other")
    assert other["meta"]["proto"] == "trojan" and other["meta"]["port"] == 2087
