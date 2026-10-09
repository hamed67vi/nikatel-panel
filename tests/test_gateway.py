import base64
import json

import pytest

from nikatel.services.gateway import (
    TEST_BASE,
    build_config,
    describe,
    outbound_from_uri,
    validate_state,
)

VLESS = "vless://b8b1ed60-e9b9-4a7f-a0d9-ab681eb382c9@gw.example.net:80?security=none&type=ws&path=%2Fapi%2Fv3&host=h.example.net#X"
VMESS = "vmess://" + base64.b64encode(json.dumps({"v": "2", "ps": "x", "add": "a.example.net", "port": "443", "id": "b8b1ed60-e9b9-4a7f-a0d9-ab681eb382c9",
                                                   "aid": "0", "scy": "auto", "net": "grpc", "type": "none", "host": "", "path": "svc", "tls": "tls", "sni": "a.example.net"}).encode()).decode()
TROJAN = "trojan://pass%40w@t.example.net:443?security=tls&sni=t.example.net&type=tcp#T"
SS = "ss://" + base64.b64encode(b"aes-256-gcm:secret").decode() + "@1.1.1.1:8388#SS"
REALITY = "vless://u@r.example.net:443?encryption=none&security=reality&sni=x.example.net&fp=chrome&pbk=KEY&sid=ab&type=tcp&flow=xtls-rprx-vision#R"


def test_outbounds_from_every_protocol():
    v = outbound_from_uri(VLESS, "t")
    assert v["protocol"] == "vless" and v["streamSettings"]["network"] == "ws" and v["streamSettings"]["wsSettings"]["path"] == "/api/v3" and v["streamSettings"]["wsSettings"]["headers"]["Host"] == "h.example.net"
    m = outbound_from_uri(VMESS, "t")
    assert m["protocol"] == "vmess" and m["streamSettings"]["security"] == "tls" and m["streamSettings"]["grpcSettings"]["serviceName"] == "svc"
    t = outbound_from_uri(TROJAN, "t")
    assert t["protocol"] == "trojan" and t["settings"]["servers"][0]["password"] == "pass@w"
    s = outbound_from_uri(SS, "t")
    assert s["protocol"] == "shadowsocks" and s["settings"]["servers"][0]["method"] == "aes-256-gcm"
    r = outbound_from_uri(REALITY, "t")
    assert r["streamSettings"]["realitySettings"]["publicKey"] == "KEY" and r["settings"]["vnext"][0]["users"][0]["flow"] == "xtls-rprx-vision"

    for bad in ("hello", "http://x", "vless://@host:80", "vmess://notbase64!!"):
        with pytest.raises(ValueError):
            outbound_from_uri(bad, "t")

    assert describe(VLESS).startswith("vless · gw.example.net:80") and describe("nope") == "unreadable link"


def test_validate_state_rules():
    st = validate_state({"ports": [{"port": 28080, "target_host": "2.2.2.2", "target_port": 443}], "borrowed": [{"uri": VLESS, "note": "x"}], "mode": "weird"})
    assert st["mode"] == "auto" and st["ports"][0]["port"] == 28080 and st["borrowed"][0]["id"] and st["borrowed"][0]["uri"] == VLESS

    for bad in ({"ports": [{"port": 80, "target_host": "2.2.2.2", "target_port": 443}]},
                {"ports": [{"port": TEST_BASE, "target_host": "2.2.2.2", "target_port": 443}]},
                {"ports": [{"port": 28080, "target_host": "2.2.2.2; id", "target_port": 443}]},
                {"borrowed": [{"uri": "garbage"}]}):
        with pytest.raises(ValueError):
            validate_state(bad)

    # an empty uri keeps the stored one
    kept = validate_state({"borrowed": [{"id": "abc123", "uri": ""}]}, [{"id": "abc123", "uri": VLESS}])
    assert kept["borrowed"][0]["uri"] == VLESS


def test_build_config_auto_and_direct():
    st = validate_state({"ports": [{"port": 28080, "target_host": "2.2.2.2", "target_port": 443}], "borrowed": [{"uri": VLESS}], "mode": "auto"})
    cfg = build_config(st)
    tags = [o["tag"] for o in cfg["outbounds"]]
    assert tags[:2] == ["direct", "block"] and tags[2].startswith("b-")
    assert cfg["routing"]["balancers"][0]["selector"] == ["direct", "b-"] and cfg["routing"]["balancers"][0]["fallbackTag"] == "direct"
    assert "observatory" in cfg
    doors = {i["port"]: i["tag"] for i in cfg["inbounds"] if i["protocol"] == "socks"}
    assert doors[TEST_BASE - 1] == "gw-t-direct" and doors[TEST_BASE] == "gw-test" and TEST_BASE + 1 in doors
    user_in = next(i for i in cfg["inbounds"] if i["protocol"] == "dokodemo-door")
    assert user_in["port"] == 28080 and user_in["settings"]["address"] == "2.2.2.2" and user_in["settings"]["network"] == "tcp"
    assert any(r.get("balancerTag") == "gw-bal" and "gw-in-28080" in r["inboundTag"] for r in cfg["routing"]["rules"])

    st["mode"] = "direct"
    cfg2 = build_config(st)
    assert "balancers" not in cfg2["routing"] and any(r.get("outboundTag") == "direct" and "gw-in-28080" in r["inboundTag"] for r in cfg2["routing"]["rules"])


def test_gateway_api_read_only(client):
    sid = client.post("/api/servers", json={"name": "ir1", "host": "1.1.1.1", "role": "iran"}).json()["id"]
    r = client.get(f"/api/gateway/{sid}")
    assert r.status_code == 200 and r.json()["installed"] is False and r.json()["read_only"] is True
    r = client.put(f"/api/gateway/{sid}", json={"mode": "borrowed", "ports": [{"port": 28080, "target_host": "2.2.2.2", "target_port": 443}], "borrowed": [{"uri": VLESS, "note": "test"}]})
    assert r.status_code == 200 and r.json()["borrowed"][0]["info"].startswith("vless") and "b8b1ed60" not in r.text
    assert client.post(f"/api/gateway/{sid}/install").status_code == 400
    assert client.post(f"/api/gateway/{sid}/apply").status_code == 400
    fid = client.post("/api/servers", json={"name": "fi1", "host": "2.2.2.2", "role": "foreign"}).json()["id"]
    assert client.get(f"/api/gateway/{fid}").status_code == 400
