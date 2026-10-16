"""One step makes a whole KIND of account on a PasarGuard panel: the inbound (core), the group (access) and the host (hosts) together, checked first, read back, and taken back when a step
fails. A fake PasarGuard 5.4 panel keeps the rules that matter here (a group and a host can only name an inbound that a core has; a group name has 3 to 64 characters and is unique; a host is
made with POST /api/host/); no test reaches a real panel."""
import copy
import hashlib
import json
import re
from urllib.parse import urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

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
        self.templates = {}
        self.scans: list[str] = []
        self.unusable: set[str] = set()
        self.next_host = self.next_group = self.next_template = 2
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

        if u.path == "/api/core/reality-scan":
            self.scans.append(body["target"])
            return {"target": body["target"], "feasible": body["target"] not in self.unusable, "tls13": True, "h2": True}

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
            return {"templates": copy.deepcopy(list(self.templates.values()))}

        if u.path == "/api/user_template" and method == "POST":
            tid, self.next_template = self.next_template, self.next_template + 1
            self.templates[tid] = {"id": tid, **copy.deepcopy(body)}
            return copy.deepcopy(self.templates[tid])

        m = re.fullmatch(r"/api/user_template/(\d+)", u.path)

        if m and method == "DELETE":
            self.templates.pop(int(m.group(1)))
            return None

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


ML = {"decryption": "mlkem768x25519plus.native.600s.PRIVATEPART", "encryption": "mlkem768x25519plus.native.0rtt.PUBLICPART"}
REALITY = {"kind": "vless-reality", "name": "Reality home", "address": "203.0.113.21", "port": 443, "reality_dest": "www.example.test:443"}
WS_TLS = {"kind": "vless-ws-tls", "name": "CDN socket", "address": "cdn.example.test", "port": 2083, "path": "/abc123"}


# ------------------------------------------------------------------ what the form chooses from
def test_the_options_say_what_the_panel_already_uses(fake):
    o = pg_kinds.options(C)
    assert [k["id"] for k in o["kinds"]] == list(pg_kinds.KINDS) and len(o["kinds"]) == 17
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
    p = pg_kinds.plan(C, {**REALITY, "reality_dest": "not a site"})
    assert p["valid"] is False and p["steps"][0]["ok"] is False and "REALITY needs the site" in p["steps"][0]["errors"][0] and p["steps"][1]["ok"] and p["steps"][2]["ok"]
    p = pg_kinds.plan(C, {**REALITY, "name": "Main"})                                                  # a group of that name is there
    assert p["valid"] is False and p["steps"][1]["ok"] is False and "exists already" in p["steps"][1]["errors"][0]
    p = pg_kinds.plan(C, {**REALITY, "port": 8443})                                                    # a port the core uses already
    assert p["valid"] is False and p["steps"][0]["errors"]
    assert fake.writes() == []


def test_a_form_that_is_not_complete_is_refused_with_a_sentence(fake):
    for body, why in (({**REALITY, "kind": "nope"}, "choose the kind"), ({**REALITY, "name": "ab"}, "needs 3 to 64"),
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
    assert ss["tlsSettings"]["certificates"] == [{"certificateFile": "/etc/own/cert.pem", "keyFile": "/etc/own/key.pem"}] and ss["tlsSettings"]["alpn"] == ["http/1.1"]
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
        pg_kinds.make(C, {**REALITY, "reality_dest": "not a site"})
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
    bad = client.post(base + "/make", json={**REALITY, "name": "Other", "port": 8444, "reality_dest": "not a site"})
    assert bad.status_code == 400 and "REALITY needs the site" in bad.json()["error"]
    assert client.post(base + "/make", json={**REALITY, "name": "ab"}).status_code == 400 and client.post(base + "/make", json={"name": "abc"}).status_code == 422          # (a kind is the one thing that is needed)


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
        body = {"kind": kid, "name": f"Kind number {i}", "address": "cdn.example.test", "port": 30000 + i, "reality_dest": "www.example.test:443", "path": "/p", "service": "svc", **(ML if kid == "vless-mlkem" else {})}
        p = pg_kinds.plan(C, body)
        assert p["valid"] is True, (kid, [s["errors"] for s in p["steps"]])
        ss = p["inbound"]["streamSettings"]
        assert (p["inbound"]["protocol"], ss["network"], ss["security"]) == (k["protocol"], k["network"], k["security"])
    assert pg_cores.check_config(fake.cores[1]["config"]) == []


def test_a_certificate_the_panel_cannot_read_is_said_in_plain_words(fake):
    fake.fail[("PUT", "/api/core/1")] = PanelError("HTTP 400: [Errno 2] No such file or directory: '/var/lib/pg-node/certs/ssl_cert.pem'", 400)
    with pytest.raises(PanelError, match="cannot read the certificate files /etc/own/cert.pem and /etc/own/key.pem"):
        pg_kinds.make(C, WS_TLS)
    assert "CDN socket" not in fake.tags() and list(fake.groups) == [1] and list(fake.hosts) == [1]


# ------------------------------------------------------------------ the user template: after it, a user of this kind is one choice
def test_a_template_that_gives_the_group_can_be_made_with_the_kind(fake):
    out = pg_kinds.make(C, {**REALITY, "template": True, "tpl_gb": 20, "tpl_days": 7})
    t = fake.templates[out["template"]["id"]]
    assert t["name"] == "Reality home" and t["group_ids"] == [out["group"]["id"]] and t["data_limit"] == 20 * 1024 ** 3 and t["expire_duration"] == 7 * 86400
    assert out["template"] == {"id": 2, "name": "Reality home", "data_limit_gb": 20.0, "expire_days": 7.0}
    assert ("the template gives the group", True) in [(c["what"], c["ok"]) for c in out["checks"]]
    p = pg_kinds.plan(C, {**REALITY, "name": "Other kind", "template": True})
    assert [s["key"] for s in p["steps"]] == ["core", "group", "host", "template"] and "50 GB, 30 days" in p["steps"][3]["title"]      # the defaults of a new kind


def test_a_template_with_an_existing_group_gives_that_group(fake):
    out = pg_kinds.make(C, {**REALITY, "group_mode": "existing", "group_id": 1, "template": True, "tpl_gb": 0, "tpl_days": 0})
    assert fake.templates[out["template"]["id"]]["group_ids"] == [1] and fake.templates[out["template"]["id"]]["data_limit"] == 0


def test_a_template_needs_a_group_and_a_free_name(fake):
    with pytest.raises(ValueError, match="gives a group"):
        pg_kinds.plan(C, {**REALITY, "group_mode": "none", "template": True})
    fake.templates[1] = {"id": 1, "name": "reality home", "group_ids": [1], "data_limit": 0, "expire_duration": 0}
    p = pg_kinds.plan(C, {**REALITY, "template": True})
    assert p["valid"] is False and p["steps"][3]["ok"] is False and "exists already" in p["steps"][3]["errors"][0] and p["steps"][0]["ok"]
    assert pg_kinds.plan(C, {**REALITY, "name": "Third kind", "template": True, "tpl_gb": "abc"})["steps"][3]["ok"] is False


def test_a_template_that_fails_takes_everything_back(fake):
    before = copy.deepcopy(fake.cores[1]["config"])
    fake.fail[("POST", "/api/user_template")] = PanelError("HTTP 500: template boom", 500)
    with pytest.raises(PanelError, match="template boom - taken back: host, group, inbound"):
        pg_kinds.make(C, {**REALITY, "template": True})
    assert fake.cores[1]["config"] == before and list(fake.groups) == [1] and list(fake.hosts) == [1] and fake.templates == {}


# ------------------------------------------------------------------ the owner gives the protocol, the rest is chosen
def test_the_kind_alone_is_enough_for_every_kind(fake):
    for kid in pg_kinds.KINDS:
        p = pg_kinds.plan(C, {"kind": kid, **(ML if kid == "vless-mlkem" else {})})
        assert p["valid"] is True, (kid, [s["errors"] for s in p["steps"]])
        assert p["auto"]["name"] and p["auto"]["address"] == "203.0.113.21" and p["auto"]["port"]
    assert fake.writes() == []


def test_a_kind_made_with_nothing_but_its_name_chooses_the_rest_and_the_next_one_gets_the_next_name_and_port(fake):
    one = pg_kinds.make(C, {"kind": "vless-reality", "template": True})
    assert one["tag"] == "VLESS REALITY" and one["auto"]["port"] == 443 and one["auto"]["address"] == "203.0.113.21" and one["auto"]["reality_dest"] == "example.test:443"      # the site of the REALITY inbound the panel has
    assert one["group"]["name"] == "VLESS REALITY" and one["template"]["name"] == "VLESS REALITY" and all(c["ok"] for c in one["checks"])
    two = pg_kinds.make(C, {"kind": "vless-reality"})
    assert two["tag"] == "VLESS REALITY 2" and 20000 <= two["auto"]["port"] < 40000       # the three usual ports (443, 8443, 2053) are taken now
    assert fake.hosts[two["host"]["id"]]["address"] == ["203.0.113.21"]


def test_the_reality_site_is_the_first_one_the_panel_finds_usable(fake):
    fake.cores[1]["config"]["inbounds"].pop(2)                               # no REALITY inbound to copy the site from
    fake.unusable = {"play.google.com:443"}
    p = pg_kinds.plan(C, {"kind": "vless-reality"})
    assert p["auto"]["reality_dest"] == "www.samsung.com:443" and fake.scans == ["play.google.com:443", "www.samsung.com:443"] and "usable" in p["notes"][-1]
    fake.unusable = set(pg_kinds.REALITY_SITES)
    p = pg_kinds.plan(C, {"kind": "vless-reality"})
    assert p["auto"]["reality_dest"] == pg_kinds.REALITY_SITES[0] and "none of the usual sites" in p["notes"][-1]


def test_a_panel_that_cannot_scan_still_gets_a_site(fake):
    fake.cores[1]["config"]["inbounds"].pop(2)
    fake.fail[("POST", "/api/core/reality-scan")] = PanelError("HTTP 404: no such route", 404)
    p = pg_kinds.plan(C, {"kind": "vless-reality"})
    assert p["valid"] is True and p["auto"]["reality_dest"] == pg_kinds.REALITY_SITES[0] and "could not be asked" in p["notes"][-1]


def test_without_a_node_or_a_public_panel_address_the_address_is_asked_for(fake):
    fake.nodes = []
    with pytest.raises(ValueError, match="does not say where its nodes are"):
        pg_kinds.plan(C, {"kind": "vless-reality"})
    assert pg_kinds.plan({**C, "url": "https://panel.example.test:8000"}, {"kind": "vless-reality"})["auto"]["address"] == "panel.example.test"      # the host name of a public panel
    with pytest.raises(ValueError, match="does not say where"):
        pg_kinds.plan({**C, "url": "http://127.0.0.1:18700"}, {"kind": "vless-reality"})


def test_a_kind_whose_usual_ports_are_all_taken_gets_a_free_one(fake):
    for i, port in enumerate(pg_kinds.KINDS["shadowsocks"]["ports"]):
        fake.cores[1]["config"]["inbounds"].append({"tag": f"taken {i}", "port": port, "protocol": "shadowsocks", "settings": {"network": "tcp,udp"}, "streamSettings": {"network": "tcp", "security": "none"}})
    port = pg_kinds.plan(C, {"kind": "shadowsocks"})["auto"]["port"]
    assert 20000 <= port < 40000 and port not in pg_kinds.KINDS["shadowsocks"]["ports"]


def test_the_default_certificate_files_are_a_guess_that_the_plan_says(fake):
    fake.cores[1]["config"]["inbounds"].pop(1)                               # no TLS inbound to copy the files from
    p = pg_kinds.plan(C, {"kind": "vless-ws-tls", "address": "cdn.example.test"})
    assert p["auto"]["tls_cert"] == pg_kinds.CERT and any("a guess" in n for n in p["notes"]) and p["auto"]["path"].startswith("/")


# ------------------------------------------------------------------ the kinds of the owner's own links
def test_reality_with_the_vision_flow_carries_the_flow_the_spiderx_and_the_fingerprint(fake):
    out = pg_kinds.make(C, {"kind": "vless-reality-vision", "reality_spx": "/api", "fingerprint": "firefox", "reality_dest": "play.google.com:443"})
    ib = next(i for i in fake.cores[1]["config"]["inbounds"] if i["tag"] == out["tag"])
    assert ib["settings"]["flow"] == "xtls-rprx-vision" and ib["streamSettings"]["realitySettings"]["spiderX"] == "/api" and ib["streamSettings"]["realitySettings"]["dest"] == "play.google.com:443"
    assert fake.hosts[out["host"]["id"]]["fingerprint"] == "firefox"
    plain = pg_kinds.make(C, {"kind": "vless-reality", "reality_dest": "play.google.com:443", "port": 8453})
    assert "flow" not in next(i for i in fake.cores[1]["config"]["inbounds"] if i["tag"] == plain["tag"])["settings"]


@pytest.mark.parametrize("kind", ["trojan-tcp-pin", "vless-ws-pin"])
def test_a_kind_with_the_panels_own_certificate_writes_it_into_the_core_and_pins_it_in_the_link(fake, kind):
    p = pg_kinds.plan(C, {"kind": kind})
    assert p["valid"] is True and p["auto"]["sni"] == pg_kinds.SPOOF_SNIS[0] and any("pins it by its hash" in n for n in p["notes"]) and p["auto"]["port"] == 32001
    assert "PREVIEW-ONLY" in json.dumps(p["inbound"]) or "<secret:" in json.dumps(p["inbound"])                 # the plan makes no key: a stand-in, with the key hidden
    out = pg_kinds.make(C, {"kind": kind})
    ib = next(i for i in fake.cores[1]["config"]["inbounds"] if i["tag"] == out["tag"])
    one = ib["streamSettings"]["tlsSettings"]["certificates"][0]
    assert "certificateFile" not in one and one["certificate"][0] == "-----BEGIN CERTIFICATE-----" and one["key"][0] == "-----BEGIN PRIVATE KEY-----"
    cert = x509.load_pem_x509_certificate("\n".join(one["certificate"]).encode())
    der = cert.public_bytes(serialization.Encoding.DER)
    h = fake.hosts[out["host"]["id"]]
    assert h["pinned_peer_cert_sha256"] == hashlib.sha256(der).hexdigest() == out["pcs"] and h["sni"] == [pg_kinds.SPOOF_SNIS[0]] and "host" not in h
    assert cert.subject.rfc4514_string() == "CN=" + pg_kinds.SPOOF_SNIS[0]
    assert ("the link pins the certificate", True) in [(c["what"], c["ok"]) for c in out["checks"]]


def test_the_own_certificate_sni_can_be_chosen_and_the_core_listing_hides_the_key(fake):
    out = pg_kinds.make(C, {"kind": "trojan-tcp-pin", "sni": "pish-data-14.myket.ir", "fingerprint": "ios"})
    h = fake.hosts[out["host"]["id"]]
    assert h["sni"] == ["pish-data-14.myket.ir"] and h["fingerprint"] == "ios"
    masked = json.dumps(pg_cores.mask_secrets(fake.cores[1]["config"])[0])
    assert "BEGIN PRIVATE KEY" not in masked and "<secret:" in masked                                        # (the editor of the core never shows the private key)


def test_the_flow_and_the_spiderx_are_checked_by_the_builder():
    base = {"protocol": "vless", "network": "tcp", "security": "reality", "port": 443, "reality_dest": "example.test:443"}
    ib, _ = pg_cores.build_inbound({**base, "flow": "xtls-rprx-vision", "reality_spx": "/api"}, preview=True)
    assert ib["settings"]["flow"] == "xtls-rprx-vision" and ib["streamSettings"]["realitySettings"]["spiderX"] == "/api"
    for bad, why in (({"flow": "x"}, "the flow is one of"), ({"flow": "xtls-rprx-vision", "protocol": "trojan"}, "only a vless"), ({"flow": "xtls-rprx-vision", "network": "ws", "security": "tls", "tls_cert": "a", "tls_key": "b"}, "needs a TCP inbound"),
                     ({"reality_spx": "api"}, "starts with a slash")):
        with pytest.raises(ValueError, match=why):
            pg_cores.build_inbound({**base, **bad}, preview=True)


# ------------------------------------------------------------------ the kinds of the owner's own links (xHTTP, TCP with an HTTP header, HTTPUpgrade, ML-KEM)
def _ib(fake, out):
    return next(i for i in fake.cores[1]["config"]["inbounds"] if i["tag"] == out["tag"])


def test_tcp_with_the_http_header_has_the_header_and_a_path_the_xhttp_kinds_carry_the_tuning(fake):
    ss = _ib(fake, pg_kinds.make(C, {"kind": "vless-tcp-http", "path": "/api/v2b", "host_header": "example.test"}, confirm=True))["streamSettings"]
    assert ss["network"] == "tcp" and ss["tcpSettings"]["header"] == {"type": "http", "request": {"path": ["/api/v2b"], "headers": {"Host": ["example.test"]}}}
    for kid, sec in (("vless-xhttp", "none"), ("vless-xhttp-tls", "tls"), ("vless-xhttp-reality", "reality")):
        ib = _ib(fake, pg_kinds.make(C, {"kind": kid, "reality_dest": "play.google.com:443"}, confirm=True))
        x = ib["streamSettings"]["xhttpSettings"]
        assert ib["streamSettings"]["security"] == sec and x["scMaxEachPostBytes"] == "1000000" and x["scMinPostsIntervalMs"] == 30 and x["xPaddingBytes"] == "100-1000" and x["path"].startswith("/")
        assert x["mode"] == ("stream-up" if kid == "vless-xhttp-tls" else "auto")


def test_httpupgrade_over_tls_and_the_host_header_of_a_domain(fake):
    out = pg_kinds.make(C, {"kind": "vless-httpupgrade-tls", "address": "cdn.example.test", "path": "/HellSevice1404"})
    assert _ib(fake, out)["streamSettings"]["httpupgradeSettings"]["path"] == "/HellSevice1404"
    h = fake.hosts[out["host"]["id"]]
    assert h["host"] == ["cdn.example.test"] and h["sni"] == ["cdn.example.test"]


def test_vless_encryption_needs_the_two_strings_and_writes_both_into_the_inbound(fake):
    with pytest.raises(ValueError, match="xray vlessenc"):
        pg_kinds.plan(C, {"kind": "vless-mlkem"})
    out = pg_kinds.make(C, {"kind": "vless-mlkem", **ML}, confirm=True)
    s = _ib(fake, out)["settings"]
    assert s["decryption"] == ML["decryption"] and s["encryption"] == ML["encryption"]
    with pytest.raises(ValueError, match="mlkem768x25519plus"):
        pg_cores.build_inbound({"protocol": "vless", "network": "tcp", "security": "none", "port": 80, "decryption": "x", "encryption": "y"}, preview=True)
    with pytest.raises(ValueError, match="mlkem768x25519plus"):
        pg_cores.build_inbound({"protocol": "trojan", "network": "tcp", "security": "none", "port": 80, **ML}, preview=True)


def test_the_form_starts_the_plain_websocket_at_port_80_but_the_api_still_picks_a_free_one(fake):
    """The owner, 2026-10-16: the default port of the plain WebSocket in the form is 80, not 8080 (the form tells when the core holds it); a call with no port still gets a free one."""
    o = pg_kinds.options(C)
    ws = next(k for k in o["kinds"] if k["id"] == "vless-ws")
    assert ws["first_port"] is True and ws["ports"][0] == 80 and not any(k["first_port"] for k in o["kinds"] if k["id"] != "vless-ws")
    assert pg_kinds._auto_port(pg_kinds.KINDS["vless-ws"], [80, 2053]) == 8080
    p = pg_kinds.plan(C, {"kind": "vless-ws", "name": "Plain ws", "address": "203.0.113.21", "path": "/abc123", "port": 80})
    assert p["valid"] is False and "port 80 is used" in " ".join(e for s in p["steps"] for e in s["errors"])
