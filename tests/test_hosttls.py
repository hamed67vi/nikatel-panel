"""TLS copies of plain WebSocket hosts: the pure parts, the plan, the copies with their backup, the removal, the API."""
import json

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Panel
from nikatel.services import hosttls, panels


def host(remark, address, host_, path=None, port=80, security="inbound_default", disabled=False, **kw):
    return {"remark": remark, "address": address, "port": port, "sni": "", "host": host_, "path": path, "security": security, "alpn": "", "fingerprint": "", "allowinsecure": False, "is_disabled": disabled, **kw}


def world():
    hosts = {"VLESS WS 2053": [host("🇹🇷 Turkey-1", "fa.example.test", "tr1.example.test"), host("🇩🇪 Germany", "fb.example.test", "de.example.test", path="/own")],
             "VLESS WS 443": [host("🇩🇪 CDN", "203.0.113.10", "nl.example.test", port=None, security="tls")],
             "VLESS TCP": [host("TCP-1", "tcp.example.test", "")],
             "VLESS WS 2096": [host("🇨🇦 IP only", "198.51.100.7", "")]}
    core = {"inbounds": [{"tag": "VLESS WS 2053", "port": 8072, "streamSettings": {"network": "ws", "security": "none", "wsSettings": {"path": "/api/v3"}}},
                         {"tag": "VLESS WS 443", "port": 443, "streamSettings": {"network": "ws", "security": "tls", "wsSettings": {"path": "/"}}},
                         {"tag": "VLESS TCP", "port": 8080, "streamSettings": {"network": "tcp", "security": "none"}},
                         {"tag": "VLESS WS 2096", "port": 8075, "streamSettings": {"network": "ws", "security": "none", "wsSettings": {"path": "/api/v3"}}}]}
    return hosts, core


@pytest.fixture
def marz(monkeypatch):
    hosts, core = world()
    state = {"hosts": hosts, "puts": [], "checked": []}

    def call(c, method, path, **kw):
        if path == "/api/hosts" and method == "GET":
            return json.loads(json.dumps(state["hosts"]))

        if path == "/api/hosts" and method == "PUT":
            state["puts"].append(kw["body"])
            state["hosts"] = json.loads(json.dumps(kw["body"]))
            return state["hosts"]

        if path == "/api/core/config":
            return core

        raise AssertionError(f"unexpected call {method} {path}")

    def check(address, port, sni, host_, path, timeout=8.0):
        state["checked"].append((address, port, sni, host_, path))
        ok = "de." not in sni
        return {"ok": ok, "stage": "ok" if ok else "cert", "detail": "TLS valid" if ok else "the certificate is not valid", "cert": "", "issuer": "", "days": 60, "ms": 5, "ws": 101 if ok else 0}

    monkeypatch.setattr(panels, "call", call)
    monkeypatch.setattr(hosttls, "check_one", check)
    return state


C = {"id": 1, "type": "marzban"}


def test_pure_helpers():
    assert hosttls.is_domain("cank.shoptinyis.ir") and not hosttls.is_domain("198.51.100.7") and not hosttls.is_domain("") and not hosttls.is_domain("localhost")
    h = host("R", "dr.example.test", "front.example.test")
    assert hosttls.sni_for(h) == "front.example.test"                                                    # the Host header is the name the CDN knows
    assert hosttls.sni_for(host("R", "dr.example.test", "")) == "dr.example.test" and hosttls.sni_for(host("R", "198.51.100.7", "")) == ""
    tw = hosttls.twin_of(h)
    assert (tw["remark"], tw["port"], tw["security"], tw["sni"], tw["host"], tw["alpn"], tw["fingerprint"], tw["allowinsecure"], tw["is_disabled"]) == ("R TLS", 443, "tls", "front.example.test", "front.example.test", "http/1.1", "chrome", False, False)
    assert h["security"] == "inbound_default" and h["port"] == 80 and h["remark"] == "R"                  # the original is not touched
    assert hosttls.twin_of({**h, "sni": ["x.example.test"], "host": ["front.example.test"]})["sni"] == ["front.example.test"]       # PasarGuard keeps its lists
    assert hosttls.same_twin({**tw, "tag": "t"}, {**hosttls.twin_of(h), "tag": "t"}) and not hosttls.same_twin({**tw, "tag": "t"}, {**tw, "tag": "u"})
    assert hosttls.ws_status(b"HTTP/1.1 101 Switching Protocols\r\n\r\n") == 101 and hosttls.ws_status(b"HTTP/1.0 502 Bad Gateway") == 502 and hosttls.ws_status(b"junk") == 0 and hosttls.ws_status(b"") == 0
    names, issuer, days = hosttls.cert_text({"subjectAltName": (("DNS", "a.example.test"), ("DNS", "b.example.test")), "issuer": ((("organizationName", "Test CA"),),), "notAfter": "Jan  1 00:00:00 2099 GMT"})
    assert names == "a.example.test, b.example.test" and issuer == "Test CA" and days > 1000
    assert hosttls.cert_text(None) == ("", "", None)


def test_which_hosts_can_have_a_copy():
    ws = {"network": "ws", "security": "none", "path": "/p"}
    assert hosttls.candidate(host("R", "a.example.test", "b.example.test"), ws) == ""
    assert "already uses TLS" in hosttls.candidate(host("R", "a", "b", security="tls"), ws)
    assert "template host" in hosttls.candidate(host("{STATUS_EMOJI} • {USERNAME} | {DATA_LEFT}", "a.example.test", "b.example.test"), ws)      # the account-details line of the subscription is not a config
    assert "tcp" in hosttls.candidate(host("R", "a", "b"), {"network": "tcp", "security": "none", "path": ""})
    assert "already has tls" in hosttls.candidate(host("R", "a", "b"), {"network": "ws", "security": "tls", "path": ""})
    assert "not in the core config" in hosttls.candidate(host("R", "a", "b"), None)
    assert hosttls.inbound_info({"inbounds": [{"tag": "x", "streamSettings": {"network": "WS", "wsSettings": {"path": "/q"}}}]})["x"] == {"network": "ws", "security": "none", "path": "/q", "host": "", "port": None}


def test_the_real_test_says_where_it_stopped():
    r = hosttls.check_one("nonexistent.invalid", 443, "x.example.test", "x.example.test", "/", timeout=2)
    assert r["ok"] is False and r["stage"] == "dns"
    r = hosttls.check_one("127.0.0.1", 1, "x.example.test", "x.example.test", "/", timeout=2)           # nothing listens on port 1
    assert r["ok"] is False and r["stage"] == "tcp" and "127.0.0.1:1" in r["detail"]


def test_plan_shows_what_would_be_made_and_why_not(marz):
    keys = ["VLESS WS 2053#0", "VLESS WS 2053#1", "VLESS WS 443#0", "VLESS TCP#0", "VLESS WS 2096#0", "nope#9"]
    items = {i["key"]: i for i in hosttls.plan(C, keys)}
    assert items["VLESS WS 2053#0"]["status"] == "new" and items["VLESS WS 2053#0"]["twin"]["port"] == 443 and items["VLESS WS 2053#0"]["twin"]["sni"] == "tr1.example.test"
    assert items["VLESS WS 2053#0"]["path"] == "/api/v3" and items["VLESS WS 2053#1"]["path"] == "/own"           # the host's own path, else the inbound's
    assert items["VLESS WS 2053#0"]["check"]["ok"] is True and items["VLESS WS 2053#1"]["check"]["ok"] is False
    assert "already uses TLS" in items["VLESS WS 443#0"]["reason"] and "tcp" in items["VLESS TCP#0"]["reason"] and "no domain" in items["VLESS WS 2096#0"]["reason"] and "not in the panel" in items["nope#9"]["reason"]
    assert marz["puts"] == [] and len(marz["checked"]) == 2                                             # the plan changes nothing; only the hosts that can have a copy are tested
    with pytest.raises(ValueError):
        hosttls.plan(C, ["VLESS WS 2053#0"], port=0)


def test_create_makes_copies_saves_a_backup_and_never_touches_the_originals(marz):
    before = json.loads(json.dumps(marz["hosts"]))
    out = hosttls.create(C, ["VLESS WS 2053#0", "VLESS WS 2053#1"])
    assert out["made"] == 2 and out["on"] == 1 and out["off"] == 1 and out["backup"].endswith(".json")
    new = marz["puts"][-1]
    assert new["VLESS WS 2053"][:2] == before["VLESS WS 2053"] and all(new[t] == before[t] for t in before if t != "VLESS WS 2053")    # every original, in its place
    tw = new["VLESS WS 2053"][2:]
    assert [t["remark"] for t in tw] == ["🇹🇷 Turkey-1 TLS", "🇩🇪 Germany TLS"] and [t["is_disabled"] for t in tw] == [False, True]      # the one that passed is on, the one that failed is off
    assert [t["port"] for t in tw] == [443, 443] and all(t["security"] == "tls" for t in tw)
    assert json.loads((get_settings().data_dir / "host-backups" / out["backup"]).read_text(encoding="utf-8")) == before             # the file holds the hosts as they were
    again = hosttls.create(C, ["VLESS WS 2053#0", "VLESS WS 2053#1"])
    assert again["made"] == 0 and {i["status"] for i in again["items"]} == {"exists"} and len(marz["puts"]) == 1                 # nothing is made twice
    skipped = hosttls.create(C, ["VLESS WS 2096#0", "VLESS WS 2053#0"], failed="skip")
    assert skipped["made"] == 0
    with pytest.raises(ValueError):
        hosttls.create(C, ["VLESS WS 2053#0"], failed="maybe")


def test_failed_copies_can_be_left_out(marz):
    out = hosttls.create(C, ["VLESS WS 2053#1"], failed="skip")
    assert out["made"] == 0 and out["items"][0]["status"] == "not_made" and "failed" in out["items"][0]["reason"] and marz["puts"] == []


def test_remove_takes_away_only_the_copies(marz):
    hosttls.create(C, ["VLESS WS 2053#0", "VLESS WS 2053#1"])
    before_copies = json.loads(json.dumps(marz["hosts"]))
    assert len(before_copies["VLESS WS 2053"]) == 4
    out = hosttls.remove(C)
    assert out["removed"] == 2 and out["backup"]
    assert marz["hosts"] == world()[0]                                                                  # exactly the first state again
    assert hosttls.remove(C) == {"removed": 0, "backup": ""}


@pytest.fixture
def panel_row():
    with session() as db:
        p = Panel(name="alpha", type="marzban", url="https://panel-a.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_the_api(client, marz, panel_row):
    body = {"keys": ["VLESS WS 2053#0", "VLESS WS 2053#1"]}
    j = client.post(f"/api/panels/{panel_row}/hosts-tls/plan", json=body).json()
    assert j["ok"] is True and [i["status"] for i in j["items"]] == ["new", "new"] and marz["puts"] == []
    j = client.post(f"/api/panels/{panel_row}/hosts-tls", json={**body, "failed": "disabled"}).json()
    assert j["made"] == 2 and j["on"] == 1 and j["off"] == 1
    assert client.post(f"/api/panels/{panel_row}/hosts-tls", json={**body, "failed": "x"}).status_code == 422
    assert client.post(f"/api/panels/{panel_row}/hosts-tls/plan", json={"keys": [], "port": 443}).status_code == 422
    assert client.post(f"/api/panels/{panel_row}/hosts-tls/remove").json()["removed"] == 2