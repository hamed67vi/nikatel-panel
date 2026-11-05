"""The live list of the Iran test kit (1.69.233, services/fieldkit.py): the test user's links become Xray clients, the LAB configs and the panel's names come from the panel, a token of scope field may read the list, and a test user about to stop the kit is told."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from nikatel.services import field, fieldkit, panels, pg_cores, pg_users, tokens
from nikatel.services import settings as st
from nikatel.services.panels import PanelError

PW = "test-password-1"
UUID = "11111111-2222-3333-4444-555555555555"
GB = 1024 ** 3
NODES = [{"name": "TR1", "address": "203.0.113.10"}, {"name": "DE1", "address": "203.0.113.11"}, {"name": "ZZ", "address": "203.0.113.12"}]
CONF = {"direct": ["TCP-3"], "deep": ["TCP-1", "TCP-3", "Cloud-1"], "panel_names": ["sub-a.example", "sub-b.example"], "sub_names": ["sub-a.example"], "ips": [{"label": "box-1", "ip": "203.0.113.20"}], "kit_latest": "2.1"}
USER = {"username": "kit", "status": "active", "expire": "2030-01-01T00:00:00Z", "used": GB, "limit": 20 * GB, "sub": "https://sub-a.example/sub/SECRETTOKEN"}


@pytest.fixture(autouse=True)
def _fresh_state():
    """`tokens._LAST_USED` (a module-wide dict keyed by token id: a test database starts ids at 1 again) and the one-minute cache of the manifest must not carry over from one test to the next."""
    tokens._LAST_USED.clear()
    fieldkit._CACHE.update(at=0.0, data=None)
    yield
    tokens._LAST_USED.clear()
    fieldkit._CACHE.update(at=0.0, data=None)


def _links() -> str:
    return "\n".join([
        f"vless://{UUID}@tr1.example.net:8680?type=tcp&security=tls&sni=api.example.net&fp=firefox&alpn=http%2F1.1#%F0%9F%87%B9%F0%9F%87%B7%20TCP-3",
        f"vless://{UUID}@cdn.example.net:443?type=ws&security=tls&sni=cdn.example.net&path=%2Fws&host=cdn.example.net#Cloud-1",
        f"vless://{UUID}@ai.example.net:1830?type=tcp&security=tls&sni=ai.example.net#%F0%9F%A4%96%20AI-1",
        f"vless://{UUID}@x1.example.net:8443?type=xhttp&security=tls&sni=x1.example.net&path=%2F&mode=auto#TCP-1",
        f"vless://{UUID}@e1.example.net:11421?type=tcp&security=tls&sni=e1.example.net#Turkey-1",
        "ss://notvless@h.example.net:1#Shadow",
        f"vless://{UUID}@dup.example.net:443?type=tcp&security=tls#Cloud-1",          # the same name twice: the first one stays
        "vless://nouser",
    ])


def _inbounds(xhttp=True):
    k = pg_cores.reality_keys()
    rs = {"realitySettings": {"privateKey": k["private_key"], "serverNames": ["www.example.org,example.org"], "shortIds": ["", "ab12"]}}
    ib = {"VLESS TCP REALITY 1845": {"streamSettings": rs}}

    if xhttp:
        ib["VLESS XHTTP REALITY 2082"] = {"streamSettings": rs}

    return k, ib


def _plain(text: str) -> dict:
    return json.loads(text.replace("%%SOCKS%%", "1").replace("%%HTTP%%", "2"))


def test_the_links_of_the_test_user_are_read_by_name_and_the_rest_is_skipped():
    items = fieldkit.parse_links(_links())
    assert [i["label"] for i in items] == ["TCP-3", "Cloud-1", "AI-1", "TCP-1", "Turkey-1"]                 # the flag and the emoji in front go; a Shadowsocks line, a double name and a link without a user are skipped
    assert next(i for i in items if i["label"] == "Cloud-1")["host"] == "cdn.example.net" and items[0]["uuid"] == UUID and items[0]["port"] == 8680


def test_the_manifest_holds_every_config_as_a_ready_client_and_the_lab_ones_of_the_direct_nodes():
    k, ib = _inbounds()
    m = fieldkit.build(_links(), NODES, ib, CONF, USER, now=1_900_000_000)
    t = {x["label"]: x for x in m["targets"]}
    assert list(t) == ["TCP-3", "Cloud-1", "AI-1", "TCP-1", "Turkey-1", "LAB-DE1-reality1845", "LAB-DE1-xhttp2082", "LAB-TR1-reality1845", "LAB-TR1-xhttp2082"]       # a node that is no direct config's node (ZZ) gets none
    assert {l: t[l]["kind"] for l in t} == {"TCP-3": "direct", "Cloud-1": "cdn", "AI-1": "ai", "TCP-1": "cdn", "Turkey-1": "entry", **{l: "lab" for l in t if l.startswith("LAB")}}
    assert [l for l in t if t[l]["deep"]] == ["TCP-3", "Cloud-1", "TCP-1"] + [l for l in t if l.startswith("LAB")]
    assert len({x["file"] for x in m["targets"]}) == 9 and all(x["file"].endswith(".json") and x["file"].isascii() for x in m["targets"])

    c = _plain(t["TCP-3"]["config"])
    ss = c["outbounds"][0]["streamSettings"]
    assert c["inbounds"][0]["port"] == 1 and c["inbounds"][1]["port"] == 2 and "%%SOCKS%%" in t["TCP-3"]["config"]            # the tokens stand where the kit puts its ports
    assert ss["tlsSettings"] == {"serverName": "api.example.net", "fingerprint": "firefox", "alpn": ["http/1.1"]} and c["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"] == UUID
    assert _plain(t["Cloud-1"]["config"])["outbounds"][0]["streamSettings"]["wsSettings"] == {"path": "/ws", "headers": {"Host": "cdn.example.net"}}
    assert _plain(t["TCP-1"]["config"])["outbounds"][0]["streamSettings"]["xhttpSettings"]["mode"] == "auto"

    lab = _plain(t["LAB-TR1-xhttp2082"]["config"])["outbounds"][0]
    assert lab["settings"]["vnext"][0]["address"] == "203.0.113.10" and lab["settings"]["vnext"][0]["port"] == 2082 and lab["streamSettings"]["xhttpSettings"] == {"path": "/", "mode": "auto"}
    assert lab["streamSettings"]["realitySettings"] == {"serverName": "www.example.org", "fingerprint": "chrome", "publicKey": k["public_key"], "shortId": "ab12", "spiderX": ""}
    assert m["panel"] == {"names": ["sub-a.example", "sub-b.example"], "sub_names": ["sub-a.example"], "sub_path": "/sub/SECRETTOKEN", "ips": [{"label": "box-1", "ip": "203.0.113.20"}]} and m["kit_latest"] == "2.1"


def test_the_version_changes_with_anything_the_kit_tests_and_a_missing_inbound_or_link_is_handled():
    _k, ib = _inbounds()
    a = fieldkit.build(_links(), NODES, ib, CONF, USER, now=1.0)["version"]
    assert a == fieldkit.build(_links(), NODES, ib, CONF, USER, now=2.0)["version"] and len(a) == 8                      # the same list: the same version, whatever the time
    assert a != fieldkit.build(_links().replace("8680", "8681"), NODES, ib, CONF, USER)["version"]                       # a changed port
    assert a != fieldkit.build(_links(), NODES, ib, {**CONF, "panel_names": ["sub-a.example"]}, USER)["version"]          # a changed panel name
    assert a != fieldkit.build(_links().replace("#Turkey-1", "#Turkey-9"), NODES, ib, CONF, USER)["version"]             # a renamed config
    only = fieldkit.build(_links(), NODES, _inbounds(xhttp=False)[1], CONF, USER)
    assert [x["label"] for x in only["targets"] if x["kind"] == "lab"] == ["LAB-DE1-reality1845", "LAB-TR1-reality1845"]    # the core has no xHTTP-Reality inbound: no such LAB config
    assert [x["label"] for x in fieldkit.build(_links(), NODES, {}, CONF, USER)["targets"] if x["kind"] == "lab"] == []
    with pytest.raises(ValueError):
        fieldkit.build("ss://x@h:1#a\nnothing", NODES, ib, CONF, USER)


def test_a_test_user_about_to_stop_the_kit_is_told():
    now = 1_900_000_000
    fine = fieldkit.user_info({**USER, "expire": now + 20 * 86400}, now)
    assert fine["days_left"] == 20.0 and fine["used_share"] == 0.05 and fine["limit_gb"] == 20.0 and fieldkit.warnings(fine) == []
    keys = lambda u: [k for k, _ in fieldkit.warnings(fieldkit.user_info(u, now))]
    assert keys({**USER, "expire": now + 3 * 86400}) == ["expire"] and "expires in 3 day(s)" in fieldkit.warnings(fieldkit.user_info({**USER, "expire": now + 3 * 86400}, now))[0][1]
    assert keys({**USER, "expire": now - 86400}) == ["expire"]                                                         # already past: told too
    assert keys({**USER, "expire": now + 20 * 86400, "status": "expired"}) == ["status"]
    assert keys({**USER, "expire": now + 20 * 86400, "used": 18 * GB}) == ["volume"]
    assert fieldkit.user_info({"expire": "garbage"}, now)["days_left"] is None and fieldkit.user_info(None, now)["used_gb"] is None


def test_the_kit_token_may_read_the_manifest_and_nothing_else_to_read(client, monkeypatch):
    assert tokens.allowed("field", "GET", "/api/field/manifest") and tokens.allowed("field", "POST", "/api/field/ingest")
    assert not tokens.allowed("field", "GET", "/api/field") and not tokens.allowed("field", "POST", "/api/field/manifest") and not tokens.allowed("field", "GET", "/api/servers")
    tok = client.post("/api/tokens", json={"name": "iran-kit", "scope": "field", "password": PW}).json()["token"]
    h, m = {"Authorization": "Bearer " + tok}, TestClient(client.app)
    monkeypatch.setattr(fieldkit, "manifest", lambda: {"version": "abc12345", "targets": []})
    r = m.get("/api/field/manifest", headers=h)
    assert r.status_code == 200 and r.json() == {"ok": True, "version": "abc12345", "targets": []}
    assert m.get("/api/field/manifest").status_code == 401 and m.get("/api/field", headers=h).status_code == 403

    def no_user():
        raise ValueError("no test user is set for the kit")

    monkeypatch.setattr(fieldkit, "manifest", no_user)
    assert m.get("/api/field/manifest", headers=h).status_code == 400

    def panel_down():
        raise PanelError("the panel does not answer", 0)

    monkeypatch.setattr(fieldkit, "manifest", panel_down)
    assert m.get("/api/field/manifest", headers=h).status_code == 502


def test_the_manifest_is_read_from_the_panel_cached_for_a_minute_and_the_expiry_is_told_once_a_day(client, monkeypatch):
    _k, ib = _inbounds()
    calls = {"sub": 0}
    soon = {**USER, "expire": time.time() + 3 * 86400}

    def sub(c, name, fmt):
        calls["sub"] += 1
        return {"text": _links()}

    monkeypatch.setattr(fieldkit, "_pg_panel", lambda: (1, NODES))
    monkeypatch.setattr(panels, "cred_for", lambda pid: {})
    monkeypatch.setattr(pg_users, "user_view", lambda c, name: {"user": soon})
    monkeypatch.setattr(pg_users, "subscription", sub)
    monkeypatch.setattr(panels, "call", lambda c, method, path, **kw: {"config": {"inbounds": [{"tag": t, **v} for t, v in ib.items()]}})
    fieldkit._CACHE.update(at=0.0, data=None)

    with pytest.raises(ValueError):                                                              # no test user in the settings yet
        fieldkit.manifest()

    st.put("field", {"user": "kit", "panel_names": ["sub-a.example"], "direct": ["TCP-3"]})
    m = fieldkit.manifest()
    assert len(m["targets"]) == 9 and m["panel"]["sub_path"] == "/sub/SECRETTOKEN" and m["user"]["days_left"] == pytest.approx(3.0, abs=0.01)
    assert fieldkit.manifest() is m and calls["sub"] == 1                                         # a second kit within the minute costs the panel nothing
    fieldkit._CACHE.update(at=0.0, data=None)
    fieldkit.manifest()
    told = [e["text"] for e in client.get("/api/events?domain=configs").json()["events"] if "Iran kit" in e["text"]]
    assert len(told) == 1 and "expires in 3 day(s)" in told[0]                                    # told once today, though the list was made twice
    fieldkit._CACHE.update(at=0.0, data=None)


def test_a_run_says_which_list_it_used():
    p = {"kit": "2.1", "manifest": "live:ab12cd34", "configs": {"A": {"label": "A", "quick": {"google": {"ok": True}}}}}
    assert field.compact(p)["manifest"] == "live:ab12cd34" and field.compact({**p, "manifest": None})["manifest"] == ""
