"""One step makes a whole KIND of account on a PasarGuard panel: the inbound (core), the group (access) and the host (hosts) together, checked first, read back, and taken back when a step
fails. A fake PasarGuard 5.4 panel keeps the rules that matter here (a group and a host can only name an inbound that a core has; a group name has 3 to 64 characters and is unique; a host is
made with POST /api/host/); no test reaches a real panel."""
import copy
import re
from urllib.parse import urlsplit

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Panel
from nikatel.services import panels, pg_cores, pg_kinds
from nikatel.services.panels import PanelError

C = {"id": 7, "type": "pasarguard"}
SECRET = "REALITY-PRIVATE-KEY-AAAA-BBBB-CCCC-DDDD-EEEE"


def base_config() -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "VLESS WS 80", "port": 80, "protocol": "vless", "settings": {"clients": [], "decryption": "none"}, "streamSettings": {"network": "ws", "security": "none", "wsSettings": {"path": "/ws"}}},
            {"tag": "TLS 8443", "port": 8443, "protocol": "trojan", "settings": {"clients": []},
             "streamSettings": {"network": "tcp", "security": "tls", "tlsSettings": {"certificates": [{"certificateFile": "/etc/own/cert.pem", "keyFile": "/etc/own/key.pem"}]}}},
            {"tag": "REALITY 2053", "port": 2053, "protocol": "vless", "settings": {"clients": [], "decryption": "none"},
             "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"privateKey": SECRET, "shortIds": ["ab"], "dest": "example.test:443", "serverNames": ["example.test"]}}},
        ],
        "outbounds": [{"tag": "DIRECT", "protocol": "freedom"}],
        "routing": {"rules": []},
    }


class Fake:
    def __init__(self):
        self.cores = {1: {"id": 1, "name": "Default Core Config", "type": "xray", "config": base_config(), "exclude_inbound_tags": [], "fallbacks_inbound_tags": [], "created_at": "2026-01-01T00:00:00Z"}}
        self.hosts = {1: {"id": 1, "remark": "h1", "inbound_tag": "VLESS WS 80", "address": ["a.example.test"], "port": None, "priority": 1, "security": "inbound_default", "is_disabled": False}}
        self.groups = {1: {"id": 1, "name": "Main", "inbound_tags": ["VLESS WS 80"], "total_users": 12, "is_disabled": False}}
        self.nodes = [{"id": 1, "name": "node-a", "status": "connected", "address": "203.0.113.21", "core_config_id": 1}]
        self.next_host = self.next_group = 2
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail: dict[tuple[str, str], PanelError] = {}
        self.restarts: list[int] = []

    def tags(self) -> set[str]:
        return {ib["tag"] for core in self.cores.values() for ib in core["config"]["inbounds"]}

    def __call__(self, c, method, path, **kw):
        u = urlsplit(path)
        body = kw.get("body")
        self.calls.append((method, u.path, copy.deepcopy(body)))

        if (method, u.path) in self.fail:
            raise self.fail[(method, u.path)]

        if u.path == "/api/cores":
            return {"count": len(self.cores), "cores": copy.deepcopy(list(self.cores.values()))}

        m = re.fullmatch(r"/api/core/(\d+)(/restart)?", u.path)

        if m:
            cid = int(m.group(1))

            if m.group(2):
                self.restarts.append(cid)
                return None

            if method == "GET":
                return copy.deepcopy(self.cores[cid])

            if method == "PUT":
                self.cores[cid].update(config=copy.deepcopy(body["config"]))
                return copy.deepcopy(self.cores[cid])

        if u.path == "/api/hosts":
            return copy.deepcopy(list(self.hosts.values()))

        if u.path == "/api/host/" and method == "POST":
            if body["inbound_tag"] not in self.tags():
                raise PanelError(f"HTTP 400: {body['inbound_tag']} not found", 400)

            hid, self.next_host = self.next_host, self.next_host + 1
            self.hosts[hid] = {"id": hid, **copy.deepcopy(body)}
            return copy.deepcopy(self.hosts[hid])

        m = re.fullmatch(r"/api/host/(\d+)", u.path)

        if m:
            hid = int(m.group(1))

            if method == "GET":
                return copy.deepcopy(self.hosts[hid])

            if method == "DELETE":
                del self.hosts[hid]
                return None

        if u.path == "/api/groups":
            return {"groups": copy.deepcopy(list(self.groups.values())), "total": len(self.groups)}

        if u.path == "/api/user_templates":
            return {"templates": []}

        if u.path == "/api/group" and method == "POST":
            bad = [t for t in body["inbound_tags"] if t not in self.tags()]

            if bad:
                raise PanelError(f"HTTP 400: inbound {bad[0]} not found", 400)

            gid, self.next_group = self.next_group, self.next_group + 1
            self.groups[gid] = {"id": gid, "total_users": 0, **copy.deepcopy(body)}
            return copy.deepcopy(self.groups[gid])

        m = re.fullmatch(r"/api/group/(\d+)", u.path)

        if m:
            gid = int(m.group(1))

            if method == "PUT":
                self.groups[gid].update(copy.deepcopy(body))
                return copy.deepcopy(self.groups[gid])

            if method == "DELETE":
                del self.groups[gid]
                return None

        if u.path == "/api/nodes":
            return {"nodes": copy.deepcopy(self.nodes), "total": len(self.nodes)}

        if u.path in ("/api/inbounds/details", "/api/inbounds"):
            return [{"tag": t, "protocol": "", "network": ""} for t in sorted(self.tags())]

        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self):
        return [x for x in self.calls if x[0] != "GET"]


@pytest.fixture(autouse=True)
def clean_backups():
    d = get_settings().data_dir / "pg-backups"

    if d.exists():
        for f in list(d.glob("cores-*")) + list(d.glob("panel7-*")):
            f.unlink()


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    monkeypatch.setattr(get_settings(), "read_only", False)
    return f


REALITY = {"kind": "vless-reality", "name": "Reality home", "address": "203.0.113.21", "port": 443, "reality_dest": "www.example.test:443"}
WS_TLS = {"kind": "vless-ws-tls", "name": "CDN socket", "address": "cdn.example.test", "port": 2083, "path": "/abc123"}


# ------------------------------------------------------------------ what the form chooses from
def test_the_options_say_what_the_panel_already_uses(fake):
    o = pg_kinds.options(C)
    assert [k["id"] for k in o["kinds"]] == list(pg_kinds.KINDS) and len(o["kinds"]) == 8
    assert o["cores"] == [{"id": 1, "name": "Default Core Config", "ports": [80, 2053, 8443]}]
    assert o["groups"] == [{"id": 1, "name": "Main", "tags": ["VLESS WS 80"]}] and o["addresses"] == ["203.0.113.21"]
    assert o["tls"] == {"cert": "/etc/own/cert.pem", "key": "/etc/own/key.pem"}                          # the files its own TLS inbound uses
    assert o["reality"] == {"dest": "example.test:443", "names": ["example.test"]}
    assert fake.writes() == []


def test_without_a_tls_inbound_the_default_certificate_is_a_guess(fake):
    fake.cores[1]["config"]["inbounds"].pop(1)
    assert pg_kinds.options(C)["tls"] == {"cert": pg_kinds.CERT, "key": pg_kinds.KEY, "guess": True}


# ------------------------------------------------------------------ the plan writes nothing
def test_a_plan_checks_the_three_steps_and_writes_nothing(fake):
    p = pg_kinds.plan(C, REALITY)
    assert p["valid"] is True and [s["key"] for s in p["steps"]] == ["core", "group", "host"] and all(s["ok"] for s in p["steps"])
    assert p["tag"] == "Reality home" and p["inbound"]["streamSettings"]["security"] == "reality" and p["inbound"]["port"] == 443
    assert "Core “Default Core Config”" in p["steps"][0]["title"] and "new group “Reality home”" in p["steps"][1]["title"] and "203.0.113.21:443" in p["steps"][2]["title"]
    assert fake.writes() == []


def test_a_plan_names_what_is_wrong_in_the_step_it_belongs_to(fake):
    p = pg_kinds.plan(C, {**REALITY, "reality_dest": ""})
    assert p["valid"] is False and p["steps"][0]["ok"] is False and "REALITY needs the site" in p["steps"][0]["errors"][0] and p["steps"][1]["ok"] and p["steps"][2]["ok"]
    p = pg_kinds.plan(C, {**REALITY, "name": "Main"})                                                  # a group of that name is there
    assert p["valid"] is False and p["steps"][1]["ok"] is False and "exists already" in p["steps"][1]["errors"][0]
    p = pg_kinds.plan(C, {**REALITY, "port": 8443})                                                    # a port the core uses already
    assert p["valid"] is False and p["steps"][0]["errors"]
    assert fake.writes() == []


def test_a_form_that_is_not_complete_is_refused_with_a_sentence(fake):
    for body, why in (({**REALITY, "kind": "nope"}, "choose the kind"), ({**REALITY, "name": "ab"}, "needs 3 to 64"), ({**REALITY, "address": ""}, "write the address"),
                      ({**REALITY, "port": 70000}, "port is a number"), ({**REALITY, "core_id": 9}, "not on the panel"), ({**REALITY, "group_mode": "existing"}, "choose the group"),
                      ({**REALITY, "group_mode": "x"}, "new, an existing")):
        with pytest.raises(ValueError, match=why):
            pg_kinds.plan(C, body)


def test_a_marzban_panel_has_no_kinds(fake):
    with pytest.raises(ValueError, match="not one"):
        pg_kinds.options({"id": 7, "type": "marzban"})


# ------------------------------------------------------------------ making one
def test_making_a_reality_kind_makes_the_inbound_the_group_and_the_host(fake):
    out = pg_kinds.make(C, REALITY)
    cfg = fake.cores[1]["config"]
    ib = next(i for i in cfg["inbounds"] if i["tag"] == "Reality home")
    assert ib["port"] == 443 and ib["protocol"] == "vless" and ib["streamSettings"]["security"] == "reality" and ib["streamSettings"]["realitySettings"]["dest"] == "www.example.test:443"
    assert out["core"]["public_key"] and out["core"]["backup"].startswith("cores-7-1-")                  # the private key stays on the panel, the public one is shown
    assert out["group"] == {"id": 2, "name": "Reality home", "created": True} and fake.groups[2]["inbound_tags"] == ["Reality home"]
    h = fake.hosts[out["host"]["id"]]
    assert h["inbound_tag"] == "Reality home" and h["address"] == ["203.0.113.21"] and h["fingerprint"] == "chrome" and h["remark"] == "Reality home ({USERNAME})" and "port" not in h
    assert [(c["what"], c["ok"]) for c in out["checks"]] == [("the inbound is in the core", True), ("the group holds the inbound", True), ("the host is on the inbound", True)]
    assert out["restarted"] is False and fake.restarts == []


def test_a_websocket_tls_kind_carries_the_domain_in_the_link(fake):
    out = pg_kinds.make(C, {**WS_TLS, "host_port": 443})
    ib = next(i for i in fake.cores[1]["config"]["inbounds"] if i["tag"] == "CDN socket")
    ss = ib["streamSettings"]
    assert ss["network"] == "ws" and ss["wsSettings"]["path"] == "/abc123" and ss["security"] == "tls"
    assert ss["tlsSettings"]["certificates"] == [{"certificateFile": pg_kinds.CERT, "keyFile": pg_kinds.KEY}] and ss["tlsSettings"]["alpn"] == ["http/1.1"]
    h = fake.hosts[out["host"]["id"]]
    assert h["address"] == ["cdn.example.test"] and h["sni"] == ["cdn.example.test"] and h["host"] == ["cdn.example.test"] and h["port"] == 443 and h["fingerprint"] == "chrome"


def test_the_certificate_files_of_the_form_are_used(fake):
    pg_kinds.make(C, {**WS_TLS, "tls_cert": "/etc/own/cert.pem", "tls_key": "/etc/own/key.pem"})
    ib = next(i for i in fake.cores[1]["config"]["inbounds"] if i["tag"] == "CDN socket")
    assert ib["streamSettings"]["tlsSettings"]["certificates"] == [{"certificateFile": "/etc/own/cert.pem", "keyFile": "/etc/own/key.pem"}]


def test_a_tls_kind_warns_about_the_certificate_and_an_ip_address(fake):
    p = pg_kinds.plan(C, {**WS_TLS, "address": "203.0.113.50"})
    w = p["steps"][2]["warnings"]
    assert len(w) == 2 and "must exist on every node" in w[0] and "address is an IP" in w[1]
    assert p["valid"] is True                                                                          # a warning, not a refusal
    assert pg_kinds.plan(C, {**WS_TLS, "address": "203.0.113.50", "sni": "cdn.example.test"})["steps"][2]["warnings"][1:] == []


def test_the_tag_is_kept_free_of_the_tags_that_exist(fake):
    out = pg_kinds.make(C, {**REALITY, "name": "TLS 8443"})                                            # a name that is an inbound tag already
    assert out["tag"] == "TLS 8443 2" and fake.hosts[out["host"]["id"]]["inbound_tag"] == "TLS 8443 2"


def test_the_tag_can_go_into_a_group_that_is_there(fake):
    out = pg_kinds.make(C, {**REALITY, "group_mode": "existing", "group_id": 1})
    assert out["group"] == {"id": 1, "name": "Main", "created": False} and fake.groups[1]["inbound_tags"] == ["VLESS WS 80", "Reality home"] and len(fake.groups) == 1


def test_with_no_group_only_the_inbound_and_the_host_are_made(fake):
    out = pg_kinds.make(C, {**REALITY, "group_mode": "none"})
    assert "group" not in out and len(fake.groups) == 1 and [c["what"] for c in out["checks"]] == ["the inbound is in the core", "the host is on the inbound"]


def test_a_restart_needs_the_ok_and_then_restarts_the_nodes(fake):
    with pytest.raises(ValueError, match="confirm: true"):
        pg_kinds.make(C, REALITY, restart=True)
    assert fake.writes() == []
    out = pg_kinds.make(C, REALITY, confirm=True, restart=True)
    assert out["restarted"] is True and fake.restarts == [1]


def test_a_failed_restart_does_not_undo_what_was_made(fake):
    fake.fail[("POST", "/api/core/1/restart")] = PanelError("no answer from the panel", 0)
    out = pg_kinds.make(C, REALITY, confirm=True, restart=True)
    assert out["restarted"] is False and "no answer" in out["restart_error"] and "Reality home" in fake.tags() and len(fake.hosts) == 2


def test_a_plan_that_is_not_valid_makes_nothing(fake):
    with pytest.raises(ValueError, match="core: .*REALITY needs the site"):
        pg_kinds.make(C, {**REALITY, "reality_dest": ""})
    assert fake.writes() == []


def test_a_read_only_server_makes_nothing(fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    with pytest.raises(ValueError, match="read-only"):
        pg_kinds.make(C, REALITY)
    assert fake.writes() == []


# ------------------------------------------------------------------ a failing step takes the earlier ones back
def test_a_host_that_fails_takes_the_group_and_the_inbound_back(fake):
    before = copy.deepcopy(fake.cores[1]["config"])
    fake.fail[("POST", "/api/host/")] = PanelError("HTTP 422: the host is wrong", 422)
    with pytest.raises(PanelError, match="host is wrong - taken back: group, inbound"):
        pg_kinds.make(C, REALITY)
    assert fake.cores[1]["config"] == before and list(fake.groups) == [1] and list(fake.hosts) == [1]


def test_a_host_that_fails_puts_the_tags_of_an_existing_group_back(fake):
    fake.fail[("POST", "/api/host/")] = PanelError("HTTP 422: the host is wrong", 422)
    with pytest.raises(PanelError, match="taken back: group, inbound"):
        pg_kinds.make(C, {**REALITY, "group_mode": "existing", "group_id": 1})
    assert fake.groups[1]["inbound_tags"] == ["VLESS WS 80"] and "Reality home" not in fake.tags()


def test_a_group_that_fails_takes_the_inbound_back(fake):
    fake.fail[("POST", "/api/group")] = PanelError("HTTP 500: boom", 500)
    with pytest.raises(PanelError, match="boom - taken back: inbound"):
        pg_kinds.make(C, REALITY)
    assert "Reality home" not in fake.tags() and len(fake.hosts) == 1


def test_a_step_that_cannot_be_taken_back_is_named(fake):
    fake.fail[("POST", "/api/host/")] = PanelError("HTTP 422: the host is wrong", 422)
    fake.fail[("DELETE", "/api/group/2")] = PanelError("HTTP 500: stuck", 500)
    with pytest.raises(PanelError, match="remove by hand: group"):
        pg_kinds.make(C, REALITY)


# ------------------------------------------------------------------ the API
@pytest.fixture
def pid():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_the_api_plans_and_makes_a_kind(client, fake, pid):
    base = f"/api/panels/{pid}/pg/kinds"
    assert client.get(base).json()["kinds"][0]["id"] == "vless-reality"
    j = client.post(base + "/plan", json=REALITY).json()
    assert j["valid"] is True and fake.writes() == []
    j = client.post(base + "/make", json=REALITY).json()
    assert j["ok"] is True and j["group"]["created"] is True and all(c["ok"] for c in j["checks"])
    bad = client.post(base + "/make", json={**REALITY, "name": "Other", "reality_dest": ""})
    assert bad.status_code == 400 and "REALITY needs the site" in bad.json()["error"]
    assert client.post(base + "/make", json={**REALITY, "name": "ab"}).status_code == 422


def test_the_api_is_for_pasarguard_panels_only(client, fake):
    with session() as db:
        p = Panel(name="mz", type="marzban", url="https://panel-m.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        mid = p.id

    assert client.get(f"/api/panels/{mid}/pg/kinds").status_code == 400
    assert client.get("/api/panels/9999/pg/kinds").status_code == 404


def test_every_route_is_described_in_one_sentence():
    from nikatel.api import pg_kinds as api_mod

    assert len(api_mod.router.routes) == 3

    for r in api_mod.router.routes:
        doc = (r.endpoint.__doc__ or "").strip()
        assert 20 < len(doc) <= 300 and "/pg/kinds" in r.path, r.path


def test_a_kind_of_every_protocol_builds_an_inbound_the_core_accepts(fake):
    for i, (kid, k) in enumerate(pg_kinds.KINDS.items()):
        body = {"kind": kid, "name": f"Kind number {i}", "address": "cdn.example.test", "port": 30000 + i, "reality_dest": "www.example.test:443", "path": "/p", "service": "svc"}
        p = pg_kinds.plan(C, body)
        assert p["valid"] is True, (kid, [s["errors"] for s in p["steps"]])
        ss = p["inbound"]["streamSettings"]
        assert (p["inbound"]["protocol"], ss["network"], ss["security"]) == (k["protocol"], k["network"], k["security"])
    assert pg_cores.check_config(fake.cores[1]["config"]) == []

def test_a_certificate_the_panel_cannot_read_is_said_in_plain_words(fake):
    fake.fail[("PUT", "/api/core/1")] = PanelError("HTTP 400: [Errno 2] No such file or directory: '/var/lib/pg-node/certs/ssl_cert.pem'", 400)
    with pytest.raises(PanelError, match="cannot read the certificate files /var/lib/pg-node/certs/ssl_cert.pem"):
        pg_kinds.make(C, WS_TLS)
    assert "CDN socket" not in fake.tags() and list(fake.groups) == [1] and list(fake.hosts) == [1]
