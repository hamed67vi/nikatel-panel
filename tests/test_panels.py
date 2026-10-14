import pytest

from nikatel.services import core, panels
from nikatel.services.panels import PanelError

SYSTEM = {"version": "0.8.4", "total_user": 120, "users_active": 100, "online_users": 7, "cpu_usage": 12.5, "mem_total": 1000, "mem_used": 250, "incoming_bandwidth": 5, "outgoing_bandwidth": 9}
NODES = [{"id": 1, "name": "fi1", "address": "2.2.2.2", "status": "connected", "xray_version": "1.8.0"}, {"id": 2, "name": "de1", "address": "3.3.3.3", "status": "error", "message": "timeout"}]
CORE = {"inbounds": [{"tag": "in1", "protocol": "vless"}], "outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}],
        "routing": {"rules": [{"ip": ["geoip:private"], "outboundTag": "block"}]}}


class Fake:
    """A Marzban-shaped panel in memory: the calls the services make, nothing else."""

    def __init__(self, hosts_as_list=False):
        self.core = dict(CORE)
        self.cores_list = None
        self.hosts = [{"id": 5, "inbound_tag": "in1", "remark": "h1", "address": ["1.1.1.1"], "port": 443, "is_disabled": False}] if hosts_as_list else {"in1": [{"remark": "h1", "address": "1.1.1.1", "port": None, "is_disabled": False}]}
        self.users = {"alice": {"username": "alice", "status": "active", "used_traffic": 10, "data_limit": 100, "expire": 1800000000, "subscription_url": "/sub/x"}}
        self.calls = []
        self.fail_put = False

    def __call__(self, c, method, path, **kw):
        self.calls.append((method, path))
        body = kw.get("body")

        if path == "/api/system":
            return SYSTEM
        if path == "/api/nodes":
            return NODES
        if path == "/api/cores":
            if self.cores_list is None:
                raise PanelError("HTTP 404", 404)
            return {"cores": self.cores_list}
        if path.startswith("/api/core/config"):
            if method == "PUT":
                if self.fail_put:
                    raise PanelError("HTTP 500: boom", 500)
                self.core = body
            return self.core
        if path == "/api/hosts":
            if method == "PUT":
                self.hosts = body
            return self.hosts
        if path.startswith("/api/host/") and method == "PUT":
            for h in self.hosts:
                if str(h["id"]) == path.rsplit("/", 1)[1]:
                    h.update(body)
            return body
        if path == "/api/inbounds":
            return {"vless": [{"tag": "in1", "port": 8443}]}
        if path.startswith("/api/users"):
            return {"users": list(self.users.values()), "total": len(self.users)}
        if path.startswith("/api/user/"):
            name = path.split("/")[3].split("?")[0]
            if name not in self.users:
                raise PanelError("HTTP 404: User not found", 404)
            if method == "PUT":
                self.users[name].update(body)
            if method == "DELETE":
                del self.users[name]
                return None
            if path.endswith("/reset"):
                self.users[name]["used_traffic"] = 0
            return self.users.get(name)
        raise PanelError("HTTP 404", 404)


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    monkeypatch.setattr(core, "call", f)
    return f


def test_pure_helpers():
    assert panels.base_url("https://p.example:8000/dashboard/") == "https://p.example:8000"
    with pytest.raises(ValueError):
        panels.base_url("p.example")
    s = panels.parse_system(SYSTEM)
    assert s["users_total"] == 120 and s["mem_pct"] == 25.0 and s["version"] == "0.8.4"
    n = panels.parse_nodes({"nodes": NODES})
    assert n[1]["status"] == "error" and n[1]["message"] == "timeout"
    assert panels.date_of(1800000000) == "2027-01-15" and panels.date_of("2027-02-01T00:00:00Z") == "2027-02-01" and panels.date_of(0) == ""
    assert isinstance(panels.expire_plus("2027-02-01T00:00:00Z", 30), str) and isinstance(panels.expire_plus(0, 30), int)


def test_user_body_rules():
    b = panels.user_body({"expire_date": "2027-01-31", "limit_gb": 2, "note": "x"}, 1800000000, "marzban")
    assert b["data_limit"] == 2 * 1024 ** 3 and isinstance(b["expire"], int) and b["note"] == "x"
    assert panels.user_body({"expire_date": ""}, None, "pasarguard")["expire"] is None
    assert panels.user_body({"expire_date": ""}, 0, "marzban")["expire"] == 0
    with pytest.raises(ValueError):
        panels.user_body({"expire_date": "31/01/2027"}, 0, "marzban")
    with pytest.raises(ValueError):
        panels.user_body({}, 0, "marzban")


def test_check_and_add_via_api(client, fake, monkeypatch):
    monkeypatch.setattr(panels, "_token", lambda c, fresh=False: "t")
    monkeypatch.setattr(panels, "_login", lambda c: "tok.tok.tok")                 # "sign in once": the login gives a token, the password is not kept
    r = client.post("/api/panels", json={"name": "shop", "type": "marzban", "url": "https://1.1.1.1:8000/dashboard", "username": "admin", "password": "pw"})
    assert r.status_code == 200, r.text
    p = r.json()["panel"]
    assert p["status"] == "up" and p["stats"]["users_total"] == 120 and len(p["nodes"]) == 2 and "password" not in str(p)
    pid = p["id"]

    # the job: the broken node gets a fail count, no reconnect in read-only mode
    panels.check_all()
    panels.check_all()
    lst = client.get("/api/panels").json()["panels"]
    bad = next(n for n in lst[0]["nodes"] if n["name"] == "de1")
    assert bad["fails"] == 2 and ("POST", "/api/node/2/reconnect") not in fake.calls

    # users
    r = client.get(f"/api/panels/{pid}/users?search=ali")
    assert r.json()["total"] == 1 and r.json()["users"][0]["expire_date"] == "2027-01-15"
    r = client.put(f"/api/panels/{pid}/users/alice", json={"limit_gb": 5, "note": "vip"})
    assert r.status_code == 200 and fake.users["alice"]["data_limit"] == 5 * 1024 ** 3
    r = client.post(f"/api/panels/{pid}/users/alice/action", json={"action": "renew", "value": 30})
    assert r.status_code == 200 and fake.users["alice"]["expire"] > 1800000000
    r = client.post(f"/api/panels/{pid}/users/nobody/action", json={"action": "disable"})
    assert r.status_code == 502
    r = client.put(f"/api/panels/{pid}/users/bad name!", json={"note": "x"})
    assert r.status_code == 400

    # hosts (Marzban dict shape -> keys tag#index, port from the inbound)
    r = client.get(f"/api/panels/{pid}/hosts").json()["hosts"]
    assert r[0]["key"] == "in1#0" and r[0]["port"] == 8443
    r = client.post(f"/api/panels/{pid}/hosts-toggle", json={"keys": ["in1#0"], "disable": True})
    assert r.json()["changed"] == 1 and fake.hosts["in1"][0]["is_disabled"] is True
    r = client.put(f"/api/panels/{pid}/hosts/in1%230", json={"remark": "renamed", "port": "", "allowinsecure": True})
    assert r.status_code == 200 and fake.hosts["in1"][0]["remark"] == "renamed" and fake.hosts["in1"][0]["allowinsecure"] is True
    r = client.put(f"/api/panels/{pid}/hosts/in1%230", json={"remark": "", "port": "99999"})
    assert r.status_code == 400

    # secrets stay out of the listing, and the password can be changed
    r = client.patch(f"/api/panels/{pid}", json={"password": "new", "verify_tls": False})
    assert r.status_code == 200 and r.json()["panel"]["verify_tls"] is False
    assert client.delete(f"/api/panels/{pid}").status_code == 200
    assert client.get("/api/panels").json()["panels"] == []


def test_hosts_list_shape():
    rows = panels.hosts_rows([{"id": 5, "inbound_tag": "in1", "remark": "h1", "address": ["a", "b"], "port": None}], {"in1": 443})
    assert rows[0]["key"] == "5" and rows[0]["address"] == "a, b" and rows[0]["port"] == 443


def test_core_validate_and_edits():
    core.validate(CORE)
    with pytest.raises(ValueError):
        core.validate({**CORE, "inbounds": []})
    with pytest.raises(ValueError):
        core.validate({**CORE, "outbounds": [{"tag": "x", "protocol": "vless"}]})       # no freedom
    with pytest.raises(ValueError):
        core.validate({**CORE, "routing": {"rules": [{"outboundTag": "ghost"}]}})
    ob = {"tag": "fi", "protocol": "vless", "settings": {"vnext": [{"address": "2.2.2.2", "port": 443}]}}
    new = core.with_outbound(CORE, ob, make_default=True)
    assert [o["tag"] for o in new["outbounds"]] == ["fi", "direct", "block"] and core.summary(new)["default"] == "fi"
    assert core.summary(new)["outbounds"][0]["target"] == "2.2.2.2:443"
    assert [o["tag"] for o in core.without_outbound(new, "block")["outbounds"]] == ["fi", "direct"]
    assert core.without_outbound(new, "block")["routing"]["rules"] == []
    assert core.as_default(new, "direct")["outbounds"][0]["tag"] == "direct"
    with pytest.raises(ValueError):
        core.as_default(new, "nope")


def test_core_save_backup_and_rollback(client, fake, monkeypatch):
    monkeypatch.setattr(panels, "_token", lambda c, fresh=False: "t")
    monkeypatch.setattr(panels, "_login", lambda c: "tok.tok.tok")
    pid = client.post("/api/panels", json={"name": "shop", "type": "marzban", "url": "https://1.1.1.1:8000", "username": "a", "password": "p"}).json()["panel"]["id"]

    # read-only panel refuses to write
    r = client.post(f"/api/panels/{pid}/core/outbounds", json={"uri": "vless://11111111-1111-1111-1111-111111111111@2.2.2.2:443?type=ws&security=tls&sni=x#fi", "tag": "fi"})
    assert r.status_code == 400 and "read-only" in r.json()["error"]

    real = core.get_settings()
    monkeypatch.setattr(core, "get_settings", lambda: type("S", (), {"read_only": False, "data_dir": real.data_dir})())
    r = client.post(f"/api/panels/{pid}/core/outbounds", json={"uri": "vless://11111111-1111-1111-1111-111111111111@2.2.2.2:443?type=ws&security=tls&sni=x#fi", "tag": "fi", "make_default": True})
    assert r.status_code == 200, r.text
    assert [o["tag"] for o in fake.core["outbounds"]] == ["fi", "direct", "block"]
    d = client.get(f"/api/panels/{pid}/core").json()
    assert d["summary"]["default"] == "fi" and len(d["backups"]) == 1 and d["backups"][0]["outbounds"] == ["direct", "block"]

    # a failing write puts the old config back
    fake.fail_put = True
    r = client.delete(f"/api/panels/{pid}/core/outbounds/fi")
    assert r.status_code == 502 and "old config" in r.json()["error"]
    fake.fail_put = False
    assert [o["tag"] for o in fake.core["outbounds"]] == ["fi", "direct", "block"]

    # restore the oldest backup (the failed delete made one too, newest first in the list)
    bl = client.get(f"/api/panels/{pid}/core").json()["backups"]
    assert len(bl) == 2 and bl[-1]["outbounds"] == ["direct", "block"]
    name = bl[-1]["file"]
    r = client.post(f"/api/panels/{pid}/core/restore/{name}")
    assert r.status_code == 200 and [o["tag"] for o in fake.core["outbounds"]] == ["direct", "block"]
    assert client.post(f"/api/panels/{pid}/core/restore/999-1.json").status_code == 400


def test_proxies_follow_the_inbounds_without_changing_the_users_ids():
    from nikatel.services.panels import proxies_for

    cur = {"vless": {"id": "11111111-1111-1111-1111-111111111111", "flow": ""}, "vmess": {"id": "22222222-2222-2222-2222-222222222222"}}
    out = proxies_for(cur, {"vless": ["VLESS TCP"], "vmess": [], "trojan": ["TROJAN WS"]})
    assert out["vless"]["id"].startswith("1111")           # kept with its id
    assert "vmess" not in out                                # no inbound left: removed
    assert out["trojan"] == {}                               # new protocol: Marzban makes the settings
    assert proxies_for(None, {"vless": ["a"]}) == {"vless": {}}

def test_a_panels_subscription_is_found_in_configs_under_any_name():
    from nikatel.services.panels import group_of_panel

    subs = [("Turkey pack", "https://pnl.example.com:8443/sub/abc123"), ("other", "https://other.example.org/sub/zzz")]
    assert group_of_panel("https://pnl.example.com:8443/", "marzban-main", subs, {"Turkey pack"}) == "Turkey pack"     # another name, same panel
    assert group_of_panel("https://nothing.example.net", "marzban-main", subs, {"Turkey pack", "marzban-main"}) == "marzban-main"   # a group named like the panel
    assert group_of_panel("https://nothing.example.net", "marzban-main", subs, {"Turkey pack"}) == ""                    # not there: the test user is made