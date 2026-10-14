"""The Core tab, second part: a new core, deleting one (and making it again from its backup), the name and the two tag lists, the inbound builder, and one inbound at a
time (add, replace, delete) with the same safety as a save. A fake PasarGuard 5.4 panel stands in for the real one; no test reaches a real panel."""
import copy
import json
import re
from urllib.parse import urlsplit

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Panel
from nikatel.services import panels, pg_cores
from nikatel.services.panels import PanelError

SECRET = "REALITY-PRIVATE-KEY-AAAA-BBBB-CCCC-DDDD-EEEE"


def base_config() -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "VLESS WS 80", "port": 80, "protocol": "vless", "settings": {"clients": [], "decryption": "none"}, "streamSettings": {"network": "ws", "security": "none", "wsSettings": {"path": "/ws"}}},
            {"tag": "VLESS REALITY 443", "port": 443, "protocol": "vless", "settings": {"clients": [], "decryption": "none"},
             "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"privateKey": SECRET, "shortIds": ["ab"], "dest": "example.test:443", "serverNames": ["example.test"]}}},
        ],
        "outbounds": [{"tag": "DIRECT", "protocol": "freedom"}, {"tag": "BLOCK", "protocol": "blackhole"}],
        "routing": {"rules": [{"type": "field", "ip": ["geoip:private"], "outboundTag": "BLOCK"}]},
    }


class Fake:
    """The core routes of a PasarGuard 5.4 panel: list, get, put, create, delete (the first core cannot go), restart, the reality scan - and the hosts, groups and nodes the warnings read."""

    def __init__(self):
        self.cores = {1: {"id": 1, "name": "Default Core Config", "type": "xray", "config": base_config(), "exclude_inbound_tags": [], "fallbacks_inbound_tags": [], "created_at": "2026-01-01T00:00:00Z"}}
        self.next = 2
        self.hosts = [{"id": 1, "remark": "h1", "inbound_tag": "VLESS WS 80", "port": None, "is_disabled": False}]
        self.groups = [{"id": 1, "name": "Main", "inbound_tags": ["VLESS WS 80", "VLESS REALITY 443"], "total_users": 12, "is_disabled": False}]
        self.nodes = [{"id": 1, "name": "node-a", "status": "connected", "address": "203.0.113.21", "core_config_id": 1}]
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail: dict[tuple[str, str], PanelError] = {}
        self.restarts: list[int] = []

    def __call__(self, c, method, path, **kw):
        u = urlsplit(path)
        body = kw.get("body")
        self.calls.append((method, u.path, copy.deepcopy(body)))

        if (method, u.path) in self.fail:
            raise self.fail[(method, u.path)]

        if u.path == "/api/cores" and method == "GET":
            return {"count": len(self.cores), "cores": copy.deepcopy(list(self.cores.values()))}

        if u.path == "/api/core" and method == "POST":
            if not body.get("config"):
                raise PanelError("HTTP 422: config dictionary cannot be empty", 422)

            if body["type"] == "xray" and not body["config"].get("inbounds"):
                raise PanelError("HTTP 400: config doesn't have inbounds", 400)

            cid, self.next = self.next, self.next + 1
            self.cores[cid] = {"id": cid, "name": body["name"], "type": body["type"], "config": copy.deepcopy(body["config"]), "exclude_inbound_tags": body["exclude_inbound_tags"],
                               "fallbacks_inbound_tags": body["fallbacks_inbound_tags"], "created_at": "2026-02-02T00:00:00Z"}
            return copy.deepcopy(self.cores[cid])

        if u.path == "/api/cores/bulk/delete" and method == "POST":
            if 1 in body["ids"]:
                raise PanelError("HTTP 403: Cannot delete default core config", 403)

            gone = [self.cores.pop(i)["name"] for i in body["ids"] if i in self.cores]
            return {"cores": gone, "count": len(gone)}

        if u.path == "/api/core/reality-scan" and method == "POST":
            return {"target": body["target"], "host": "example.test", "port": 443, "feasible": True, "tls13": True, "h2": True, "cert_valid": True, "server_names": ["example.test"], "latency_ms": 31}

        m = re.fullmatch(r"/api/core/(\d+)(/restart)?", u.path)

        if m:
            cid = int(m.group(1))

            if cid not in self.cores:
                raise PanelError("HTTP 404: Core config not found", 404)

            if m.group(2):
                self.restarts.append(cid)
                return None

            if method == "GET":
                return copy.deepcopy(self.cores[cid])

            if method == "PUT":
                self.cores[cid].update(config=copy.deepcopy(body["config"]), name=body.get("name", self.cores[cid]["name"]), type=body.get("type", self.cores[cid]["type"]),
                                       exclude_inbound_tags=body.get("exclude_inbound_tags", []), fallbacks_inbound_tags=body.get("fallbacks_inbound_tags", []))
                return copy.deepcopy(self.cores[cid])

            if method == "DELETE":
                if cid == 1:
                    raise PanelError("HTTP 403: Cannot delete default core config", 403)

                del self.cores[cid]
                return None

        if u.path == "/api/hosts":
            return copy.deepcopy(self.hosts)

        if u.path == "/api/groups":
            return {"groups": copy.deepcopy(self.groups), "total": len(self.groups)}

        if u.path == "/api/nodes":
            return {"nodes": copy.deepcopy(self.nodes), "total": len(self.nodes)}

        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self):
        return [x for x in self.calls if x[0] != "GET"]


C = {"id": 7, "type": "pasarguard"}


@pytest.fixture(autouse=True)
def clean_backups():
    d = get_settings().data_dir / "pg-backups"

    if d.exists():
        for f in d.glob("cores-*"):
            f.unlink()


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    monkeypatch.setattr(get_settings(), "read_only", False)
    return f


# ------------------------------------------------------------------ small pure things
def test_the_tag_lists_are_clean_sorted_lists():
    assert pg_cores.tag_list(None) == [] and pg_cores.tag_list("") == [] and pg_cores.tag_list(" b, a\n c ,a ,,") == ["a", "b", "c"] and pg_cores.tag_list(["x", " x", "y"]) == ["x", "y"]
    with pytest.raises(ValueError, match="a list of inbound tags"):
        pg_cores.tag_list(5)
    with pytest.raises(ValueError, match="too long"):
        pg_cores.tag_list([f"tag{i}" + "x" * 100 for i in range(30)])


def test_a_core_name_is_one_to_256_plain_characters():
    assert pg_cores.check_core_name("  Iran core ") == "Iran core"
    for bad in ("", "   ", None, "x" * 257, "a\x00b"):
        with pytest.raises(ValueError):
            pg_cores.check_core_name(bad)


def test_the_starting_config_gets_a_tag_no_core_uses():
    cfg = pg_cores.default_config("xray", {"Shadowsocks TCP", "Shadowsocks TCP 2"})
    assert cfg["inbounds"][0]["tag"] == "Shadowsocks TCP 3" and pg_cores.check_config(cfg) == []
    assert pg_cores.default_config("xray")["inbounds"][0]["tag"] == "Shadowsocks TCP" and pg_cores.default_config("wg") == {} and pg_cores.default_config("xray") is not pg_cores._DEFAULT_XRAY


def test_reality_keys_are_a_real_x25519_pair():
    k = pg_cores.reality_keys()
    assert len(k["private_key"]) == 43 and len(k["public_key"]) == 43 and k["private_key"] != pg_cores.reality_keys()["private_key"]
    assert pg_cores.public_of(k["private_key"]) == k["public_key"]
    for bad in ("", "short", "!" * 43, "A" * 50):
        with pytest.raises(ValueError, match="not a REALITY private key"):
            pg_cores.public_of(bad)


# ------------------------------------------------------------------ the builder
def test_the_builder_makes_the_inbound_a_panel_expects():
    ib, pub = pg_cores.build_inbound({"protocol": "vless", "network": "tcp", "security": "reality", "port": 443, "reality_dest": "www.example.test:443", "reality_names": "www.example.test, example.test"})
    assert ib["tag"] == "VLESS TCP REALITY" and ib["port"] == 443 and ib["protocol"] == "vless" and ib["listen"] == "0.0.0.0" and ib["settings"] == {"clients": [], "decryption": "none"}
    rs = ib["streamSettings"]["realitySettings"]
    assert rs["dest"] == "www.example.test:443" and rs["serverNames"] == ["www.example.test", "example.test"] and rs["xver"] == 0 and re.fullmatch(r"[0-9a-f]{16}", rs["shortIds"][0])
    assert pg_cores.public_of(rs["privateKey"]) == pub and ib["sniffing"] == {"enabled": True, "destOverride": ["http", "tls", "quic"]}
    assert pg_cores.check_config({"inbounds": [ib], "outbounds": [{"tag": "o"}]}) == []

    ws, pub = pg_cores.build_inbound({"protocol": "vmess", "network": "ws", "security": "tls", "port": 2087, "path": "/p", "host": "cdn.example.test", "tls_cert": "/c/a.pem", "tls_key": "/c/a.key", "alpn": "h2,http/1.1", "tag": "VMESS WS"})
    assert pub is None and ws["tag"] == "VMESS WS" and ws["settings"] == {"clients": []}
    assert ws["streamSettings"]["wsSettings"] == {"path": "/p", "headers": {"Host": "cdn.example.test"}}
    assert ws["streamSettings"]["tlsSettings"] == {"certificates": [{"certificateFile": "/c/a.pem", "keyFile": "/c/a.key"}], "minVersion": "1.2", "alpn": ["h2", "http/1.1"]}

    g, _ = pg_cores.build_inbound({"protocol": "trojan", "network": "grpc", "port": 8443, "service": "svc", "sniffing": False})
    assert g["streamSettings"]["grpcSettings"] == {"serviceName": "svc", "multiMode": True} and "sniffing" not in g and g["tag"] == "TROJAN GRPC"
    x, _ = pg_cores.build_inbound({"protocol": "vless", "network": "xhttp", "port": 8080, "path": "/x", "mode": "stream-up"})
    assert x["streamSettings"]["xhttpSettings"] == {"path": "/x", "host": "", "mode": "stream-up"}
    h, _ = pg_cores.build_inbound({"protocol": "vless", "network": "httpupgrade", "port": 8081})
    assert h["streamSettings"]["httpupgradeSettings"] == {"path": "/", "host": ""}
    ss, _ = pg_cores.build_inbound({"protocol": "shadowsocks", "port": 1080})
    assert ss["settings"] == {"clients": [], "network": "tcp,udp"} and ss["tag"] == "SHADOWSOCKS TCP"


def test_the_builder_refuses_what_cannot_work_in_plain_words():
    ok = {"protocol": "vless", "network": "tcp", "security": "none", "port": 443}
    bad = [({"protocol": "hysteria"}, "protocol is one of"), ({"network": "quic"}, "network is one of"), ({"security": "xtls"}, "security is one of"), ({"port": 0}, "port is a number"), ({"port": "x"}, "port is a number"),
           ({"port": 70000}, "port is a number"), ({"tag": "a,b"}, "comma"), ({"security": "tls"}, "path of the certificate"), ({"security": "reality"}, "site it imitates"),
           ({"security": "reality", "reality_dest": "https://x.test"}, "site it imitates"), ({"security": "reality", "reality_dest": "x.test:443", "reality_short_ids": "abc"}, "short id"),
           ({"security": "reality", "reality_dest": "x.test:443", "reality_private_key": "nope"}, "not a REALITY private key"), ({"protocol": "shadowsocks", "security": "reality", "reality_dest": "x.test"}, "vless, vmess or trojan"),
           ({"network": "xhttp", "mode": "fast"}, "xhttp mode")]

    for patch, why in bad:
        with pytest.raises(ValueError, match=why):
            pg_cores.build_inbound({**ok, **patch})

    ib, _ = pg_cores.build_inbound({**ok, "security": "reality", "reality_dest": "x.test", "reality_private_key": pg_cores.reality_keys()["private_key"]})
    assert ib["streamSettings"]["realitySettings"]["dest"] == "x.test:443" and ib["streamSettings"]["realitySettings"]["serverNames"] == ["x.test"]      # a bare name means port 443 and is its own server name
    assert pg_cores.build_inbound({**ok}, {"VLESS TCP"})[0]["tag"] == "VLESS TCP 2"                                                                     # a free tag is made when none is typed


def test_a_preview_never_makes_a_real_key():
    ib, pub = pg_cores.build_inbound({"security": "reality", "reality_dest": "x.test:443", "port": 443}, preview=True)
    assert pub is None and ib["streamSettings"]["realitySettings"]["privateKey"] == "PREVIEW-ONLY-KEY-NOT-STORED"


def test_one_inbound_changes_at_a_time_and_the_config_is_not_touched():
    cfg = base_config()
    new = pg_cores.inbound_change(cfg, "add", None, {"tag": "NEW", "port": 1, "protocol": "vless"})
    assert [i["tag"] for i in new["inbounds"]] == ["VLESS WS 80", "VLESS REALITY 443", "NEW"] and len(cfg["inbounds"]) == 2
    new = pg_cores.inbound_change(cfg, "set", "VLESS WS 80", {"tag": "RENAMED", "port": 81})
    assert [i["tag"] for i in new["inbounds"]] == ["RENAMED", "VLESS REALITY 443"]
    assert [i["tag"] for i in pg_cores.inbound_change(cfg, "delete", "VLESS WS 80")["inbounds"]] == ["VLESS REALITY 443"]
    for args, why in ((("add", None, {"tag": "VLESS WS 80"}), "exists already"), (("add", None, {"port": 1}), "needs a tag"), (("add", None, "x"), "needs a tag"), (("set", "none", {"tag": "x"}), "no inbound is called"),
                      (("set", "VLESS WS 80", "x"), "must be a JSON object"), (("delete", "none"), "no inbound is called"), (("fly", "x"), "op is add")):
        with pytest.raises(ValueError, match=why):
            pg_cores.inbound_change(cfg, *args)
    with pytest.raises(ValueError, match="no inbounds list"):
        pg_cores.inbound_change({"inbounds": "x"}, "add", None, {"tag": "a"})


# ------------------------------------------------------------------ a new core
def test_a_new_core_starts_from_the_panels_own_config_and_runs_on_no_node(fake):
    out = pg_cores.create(C, " Iran core ")
    assert out["created"] is True and out["core"]["name"] == "Iran core" and out["core"]["type"] == "xray" and out["core"]["id"] == 2 and out["inbounds"] == 1
    post = fake.writes()[-1]
    assert post[0] == "POST" and post[1] == "/api/core" and post[2]["name"] == "Iran core" and post[2]["exclude_inbound_tags"] == [] and post[2]["config"]["inbounds"][0]["tag"] == "Shadowsocks TCP"
    assert not any(x[1].endswith("/restart") for x in fake.calls) and fake.restarts == []                                    # nothing restarts for a new core
    with session() as db:
        assert any("Iran core" in e.text and "made on panel #7" in e.text for e in db.query(Event).all())


def test_a_new_core_can_come_from_text_a_config_or_a_copy_of_another_core(fake):
    cfg = pg_cores.default_config("xray")
    cfg["inbounds"][0]["tag"] = "Mine"
    assert pg_cores.create(C, "a", "xray", text=json.dumps(cfg), exclude=["Mine", "Mine"], fallbacks="x, y")["core"]["id"] == 2
    assert fake.cores[2]["exclude_inbound_tags"] == ["Mine"] and fake.cores[2]["fallbacks_inbound_tags"] == ["x", "y"]
    assert pg_cores.create(C, "b", "singbox", config={"anything": 1})["core"]["type"] == "singbox"
    cp = pg_cores.create(C, "c", copy_from=1)
    assert fake.cores[cp["core"]["id"]]["config"] == fake.cores[1]["config"] and SECRET in json.dumps(fake.cores[cp["core"]["id"]]["config"])     # a copy keeps the secrets of the original


def test_a_new_core_is_refused_before_it_reaches_the_panel(fake):
    for kw, why in (({"name": ""}, "needs a name"), ({"name": "x", "core_type": "bsd"}, "type of a core"), ({"name": "x", "text": "{nope"}, "not valid JSON"), ({"name": "x", "config": {}}, "inbounds"),
                    ({"name": "x", "text": json.dumps({"inbounds": [{"tag": "<secret:0123456789>"}], "outbounds": [{"tag": "o"}], "k": "<secret:0123456789>"})}, "placeholder"),
                    ({"name": "x", "config": {"inbounds": [{"tag": "a"}, {"tag": "a"}], "outbounds": [{"tag": "o"}]}}, "unique"), ({"name": "x", "exclude": 5}, "list of inbound tags"), ({"name": "x", "copy_from": 99}, "not found")):
        with pytest.raises((ValueError, PanelError), match=why):
            pg_cores.create(C, **kw)
    assert fake.writes() == []


def test_a_new_core_needs_the_panel_to_be_writable(fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    with pytest.raises(ValueError, match="read-only"):
        pg_cores.create(C, "x")
    assert fake.calls == []


def test_the_words_of_the_panel_are_passed_on_without_the_secrets_of_the_config(fake):
    fake.fail[("POST", "/api/core")] = PanelError(f"HTTP 400: bad config near {SECRET}", 400)
    cfg = base_config()
    with pytest.raises(PanelError) as e:
        pg_cores.create(C, "x", config=cfg)
    assert "refused the core" in str(e.value) and SECRET not in str(e.value) and "<hidden>" in str(e.value) and e.value.code == 400


def test_an_answer_without_an_id_is_not_taken_for_a_core(fake, monkeypatch):
    def odd(c, method, path, **kw):
        return {"name": "x"} if (method, path) == ("POST", "/api/core") else fake(c, method, path, **kw)

    monkeypatch.setattr(panels, "call", odd)
    with pytest.raises(PanelError, match="without the id"):
        pg_cores.create(C, "x")


# ------------------------------------------------------------------ deleting a core
def make_second(fake):
    cfg = {"inbounds": [{"tag": "Only Here", "port": 9000, "protocol": "vless", "settings": {"clients": []}, "streamSettings": {"network": "tcp", "security": "none"}}], "outbounds": [{"tag": "DIRECT", "protocol": "freedom"}]}
    fake.cores[2] = {"id": 2, "name": "Second", "type": "xray", "config": cfg, "exclude_inbound_tags": [], "fallbacks_inbound_tags": [], "created_at": "2026-02-02T00:00:00Z"}
    fake.next = max(fake.next, 3)
    return cfg


def test_the_first_core_is_never_deleted(fake):
    with pytest.raises(ValueError, match="default one"):
        pg_cores.delete(C, 1, True)
    assert fake.writes() == []


def test_a_delete_names_what_it_takes_with_it_and_needs_the_ok(fake):
    make_second(fake)
    fake.hosts.append({"id": 2, "remark": "h2", "inbound_tag": "Only Here", "port": None, "is_disabled": False})
    fake.groups.append({"id": 2, "name": "Solo", "inbound_tags": ["Only Here"], "total_users": 4})
    fake.nodes.append({"id": 2, "name": "node-b", "status": "connected", "core_config_id": 2})
    with pytest.raises(pg_cores.NeedsConfirm) as e:
        pg_cores.delete(C, 2)
    texts = " | ".join(w["text"] for w in e.value.warnings)
    assert e.value.warnings[0]["code"] == "nodes_use_core" and "node-b" in texts and "“Only Here”" in texts and "1 host" in texts and "Solo" in texts and 2 in fake.cores and fake.writes() == []


def test_the_check_says_what_a_delete_would_take_and_changes_nothing(fake):
    make_second(fake)
    fake.nodes.append({"id": 2, "name": "node-b", "status": "connected", "core_config_id": 2})
    ck = pg_cores.delete_check(C, 2)
    assert ck["allowed"] is True and ck["name"] == "Second" and ck["inbounds"] == 1 and [n["name"] for n in ck["nodes"]] == ["node-b"] and ck["warnings"][0]["code"] == "nodes_use_core"
    first = pg_cores.delete_check(C, 1)
    assert first["allowed"] is False and "default one" in first["reason"]
    assert fake.writes() == []


def test_a_core_nobody_uses_still_needs_the_ok(fake):
    make_second(fake)
    fake.hosts.clear()
    fake.groups.clear()
    fake.nodes.clear()
    with pytest.raises(ValueError, match="confirm: true"):
        pg_cores.delete(C, 2)
    assert 2 in fake.cores


def test_a_confirmed_delete_keeps_a_backup_and_the_core_can_be_made_again(fake):
    make_second(fake)
    out = pg_cores.delete(C, 2, True)
    assert out["deleted"] is True and 2 not in fake.cores and out["backup"].startswith("cores-7-2-")
    gone = pg_cores.deleted_backups(C)
    assert [g["core"] for g in gone] == [2] and gone[0]["name"] == "Second" and gone[0]["inbounds"] == 1 and gone[0]["file"] == out["backup"]
    back = pg_cores.recreate(C, 2, out["backup"])
    assert back["core"]["id"] == 3 and fake.cores[3]["name"] == "Second" and fake.cores[3]["config"]["inbounds"][0]["tag"] == "Only Here"
    assert pg_cores.deleted_backups(C) == [] or all(g["core"] != 3 for g in pg_cores.deleted_backups(C))
    with pytest.raises(ValueError, match="unknown backup"):
        pg_cores.recreate(C, 5, out["backup"])                                                                         # a backup of another core is not this core's


def test_when_the_panel_refuses_the_delete_the_backup_is_not_kept(fake):
    make_second(fake)
    fake.fail[("DELETE", "/api/core/2")] = PanelError("HTTP 409: the core is in use", 409)
    with pytest.raises(PanelError, match="did not delete the core"):
        pg_cores.delete(C, 2, True)
    assert 2 in fake.cores and list((get_settings().data_dir / "pg-backups").glob("cores-7-2-*")) == []


def test_several_cores_go_in_one_call_with_a_backup_of_each(fake):
    make_second(fake)
    fake.cores[3] = {**copy.deepcopy(fake.cores[2]), "id": 3, "name": "Third"}
    fake.hosts.clear()
    fake.groups.clear()
    fake.nodes.clear()
    with pytest.raises(ValueError, match="confirm: true"):
        pg_cores.delete_many(C, [2, 3])
    assert fake.writes() == [] and set(fake.cores) == {1, 2, 3}
    out = pg_cores.delete_many(C, [3, 2, 3], True)                                                                       # a double is one
    assert out["deleted"] == ["Second", "Third"] and out["count"] == 2 and len(out["backups"]) == 2 and set(fake.cores) == {1}
    assert ("POST", "/api/cores/bulk/delete", {"ids": [2, 3]}) in fake.calls
    assert sorted(g["core"] for g in pg_cores.deleted_backups(C)) == [2, 3]                                                # each can be made again


def test_a_bulk_delete_of_cores_names_what_it_takes_never_takes_the_first_and_keeps_no_backup_when_refused(fake):
    make_second(fake)
    fake.nodes.append({"id": 2, "name": "node-b", "status": "connected", "core_config_id": 2})
    with pytest.raises(pg_cores.NeedsConfirm) as e:
        pg_cores.delete_many(C, [2])
    assert e.value.warnings[0]["code"] == "nodes_use_core" and e.value.warnings[0]["core"] == "Second" and 2 in fake.cores and fake.writes() == []

    with pytest.raises(ValueError, match="default one"):
        pg_cores.delete_many(C, [1, 2], True)

    fake.fail[("POST", "/api/cores/bulk/delete")] = PanelError("HTTP 409: a core is in use", 409)
    with pytest.raises(PanelError, match="did not delete the cores"):
        pg_cores.delete_many(C, [2], True)

    assert 2 in fake.cores and list((get_settings().data_dir / "pg-backups").glob("cores-7-2-*")) == []                 # nothing went: the backups are not kept

    for ids, why in (([], "1 to 20"), (list(range(2, 30)), "1 to 20")):
        with pytest.raises(ValueError, match=why):
            pg_cores.delete_many(C, ids, True)

    with pytest.raises(PanelError, match="not found"):
        pg_cores.delete_many(C, [9], True)


def test_every_deleted_core_is_listed_once_with_its_newest_backup(fake):
    make_second(fake)
    fake.cores[3] = {**copy.deepcopy(fake.cores[2]), "id": 3, "name": "Third"}
    pg_cores.backup_write(7, pg_cores._get_core(C, 2), "an older backup")                                            # a core with two backups is still one row
    pg_cores.delete(C, 2, True)
    pg_cores.delete(C, 3, True)
    rows = pg_cores.deleted_backups(C)
    assert sorted(g["name"] for g in rows) == ["Second", "Third"] and [g["note"] for g in rows if g["core"] == 2] == ["before deleting the core"]


# ------------------------------------------------------------------ the name and the tag lists
def test_the_name_and_the_tag_lists_change_with_a_backup_and_nothing_else(fake):
    before = copy.deepcopy(fake.cores[1]["config"])
    out = pg_cores.update_meta(C, 1, name=" Main ", exclude=["VLESS WS 80"], fallbacks="a, b")
    assert out["saved"] is True and out["core"]["name"] == "Main" and fake.cores[1]["exclude_inbound_tags"] == ["VLESS WS 80"] and fake.cores[1]["fallbacks_inbound_tags"] == ["a", "b"]
    assert fake.cores[1]["config"] == before and out["backup"].startswith("cores-7-1-")
    put = [x for x in fake.writes() if x[0] == "PUT"][-1]
    assert put[2]["config"] == before and put[2]["name"] == "Main" and put[2]["type"] == "xray"
    with pytest.raises(ValueError, match="nothing to change"):
        pg_cores.update_meta(C, 1, name="Main", exclude=["VLESS WS 80"], fallbacks=["a", "b"])
    with pytest.raises(pg_cores.Conflict):
        pg_cores.update_meta(C, 1, name="Other", base_rev="0" * 16)
    with pytest.raises(ValueError, match="needs a name"):
        pg_cores.update_meta(C, 1, name="")


def test_a_refused_rename_leaves_no_backup_behind(fake):
    fake.fail[("PUT", "/api/core/1")] = PanelError("HTTP 400: nope", 400)
    with pytest.raises(PanelError, match="refused the change"):
        pg_cores.update_meta(C, 1, name="Other")
    assert list((get_settings().data_dir / "pg-backups").glob("cores-7-1-*")) == []


# ------------------------------------------------------------------ one inbound at a time, with the safety of a save
SPEC = {"protocol": "vless", "network": "ws", "security": "none", "port": 2095, "path": "/new", "tag": "NEW WS"}


def test_a_dry_run_of_an_added_inbound_shows_the_change_and_writes_nothing(fake):
    r = pg_cores.inbound_dry_run(C, 1, "add", spec=SPEC)
    assert r["valid"] is True and r["changed"] is True and r["inbound"]["tag"] == "NEW WS" and r["inbounds"] == 3 and r["diff"]["added"] > 5
    assert any(w["code"] == "plain_exposed" for w in r["warnings"]) and r["needs_confirm"] is True                       # a plain vless on a public port is said out loud
    assert fake.writes() == []
    dup = pg_cores.inbound_dry_run(C, 1, "add", spec={**SPEC, "tag": "VLESS WS 80"})
    assert dup["valid"] is False and "exists already" in dup["errors"][0] and dup["inbound"] is None
    assert pg_cores.inbound_dry_run(C, 1, "add", spec={**SPEC, "port": "x"})["errors"] == ["the port is a number from 1 to 65535"]
    assert "send the inbound" in pg_cores.inbound_dry_run(C, 1, "add")["errors"][0]


def test_an_added_inbound_needs_the_ok_when_it_has_warnings_and_is_backed_up(fake):
    with pytest.raises(pg_cores.NeedsConfirm):
        pg_cores.inbound_apply(C, 1, "add", spec=SPEC)
    out = pg_cores.inbound_apply(C, 1, "add", spec=SPEC, confirm=True)
    assert out["saved"] is True and out["inbounds"] == 3 and out["backup"] and out["inbound"]["tag"] == "NEW WS" and out["public_key"] is None
    assert [i["tag"] for i in fake.cores[1]["config"]["inbounds"]] == ["VLESS WS 80", "VLESS REALITY 443", "NEW WS"]
    assert fake.restarts == []


def test_a_reality_inbound_from_the_form_gets_a_new_key_whose_public_half_comes_back(fake):
    spec = {"protocol": "vless", "network": "tcp", "security": "reality", "port": 8443, "reality_dest": "www.example.test:443", "tag": "R2"}
    pre = pg_cores.inbound_dry_run(C, 1, "add", spec=spec)
    assert pre["valid"] is True and "PREVIEW-ONLY" not in json.dumps(pre["inbound"])                                      # the preview shows a placeholder, never a key
    out = pg_cores.inbound_apply(C, 1, "add", spec=spec, confirm=True)
    stored = fake.cores[1]["config"]["inbounds"][-1]["streamSettings"]["realitySettings"]
    assert out["public_key"] and pg_cores.public_of(stored["privateKey"]) == out["public_key"] and stored["privateKey"] != "PREVIEW-ONLY-KEY-NOT-STORED"
    assert stored["privateKey"] not in json.dumps(out)                                                                  # the private key never leaves
    given = pg_cores.reality_keys()
    out = pg_cores.inbound_apply(C, 1, "add", spec={**spec, "tag": "R3", "reality_private_key": given["private_key"]}, confirm=True)
    assert out["public_key"] == given["public_key"]


def test_an_inbound_is_replaced_from_its_json_and_its_secrets_come_back(fake):
    masked = pg_cores.mask_secrets(fake.cores[1]["config"])[0]["inbounds"][1]                                              # what the page shows
    masked["port"] = 8443
    text = json.dumps(masked)
    assert "<secret:" in text and SECRET not in text
    r = pg_cores.inbound_dry_run(C, 1, "set", "VLESS REALITY 443", text=text)
    assert r["valid"] is True and any(w["code"] == "port_changed" for w in r["warnings"]) and SECRET not in json.dumps(r)
    out = pg_cores.inbound_apply(C, 1, "set", "VLESS REALITY 443", text=text, confirm=True)
    assert out["saved"] is True and fake.cores[1]["config"]["inbounds"][1]["port"] == 8443 and fake.cores[1]["config"]["inbounds"][1]["streamSettings"]["realitySettings"]["privateKey"] == SECRET


def test_a_rename_that_hosts_and_groups_still_use_is_a_danger(fake):
    ib = copy.deepcopy(fake.cores[1]["config"]["inbounds"][0])
    ib["tag"] = "VLESS WS 80 NEW"
    r = pg_cores.inbound_dry_run(C, 1, "set", "VLESS WS 80", text=json.dumps(ib))
    w = [x for x in r["warnings"] if x["code"] == "tag_removed"]
    assert w and w[0]["level"] == "danger" and "1 host" in w[0]["text"] and "Main" in w[0]["text"] and "renamed to “VLESS WS 80 NEW”" in w[0]["text"]


def test_deleting_an_inbound_is_checked_like_any_save(fake):
    r = pg_cores.inbound_dry_run(C, 1, "delete", "VLESS WS 80")
    assert r["valid"] is True and r["inbounds"] == 1 and any(w["code"] == "tag_removed" for w in r["warnings"]) and r["inbound"] is None
    with pytest.raises(pg_cores.NeedsConfirm):
        pg_cores.inbound_apply(C, 1, "delete", "VLESS WS 80")
    out = pg_cores.inbound_apply(C, 1, "delete", "VLESS WS 80", confirm=True)
    assert [i["tag"] for i in fake.cores[1]["config"]["inbounds"]] == ["VLESS REALITY 443"] and out["backup"]
    last = pg_cores.inbound_dry_run(C, 1, "delete", "VLESS REALITY 443")
    assert last["valid"] is False and "no inbounds" in " ".join(last["errors"])                                         # the panel refuses a config without inbounds: said before it is tried
    assert pg_cores.inbound_dry_run(C, 1, "delete", "nope")["errors"] == ["no inbound is called “nope”"]


def test_the_core_changing_under_the_page_is_refused(fake):
    r = pg_cores.inbound_dry_run(C, 1, "add", spec={**SPEC, "security": "none"})
    rev = r["rev"]
    fake.cores[1]["config"]["log"]["loglevel"] = "debug"                                                                # somebody else saves
    with pytest.raises(pg_cores.Conflict):
        pg_cores.inbound_apply(C, 1, "add", spec=SPEC, confirm=True, base_rev=rev)


def test_a_restart_after_an_inbound_change_needs_the_ok(fake):
    with pytest.raises(ValueError, match="confirm: true"):
        pg_cores.inbound_apply(C, 1, "add", spec={**SPEC, "tag": "X", "security": "reality", "reality_dest": "a.test:443"}, restart_after=True)
    out = pg_cores.inbound_apply(C, 1, "add", spec={**SPEC, "tag": "X", "security": "reality", "reality_dest": "a.test:443"}, confirm=True, restart_after=True)
    assert out["restarted"] is True and fake.restarts == [1]


def test_the_reality_scan_is_a_read_that_checks_the_target_first(fake):
    r = pg_cores.reality_scan(C, "example.test:443", 5)
    assert r["feasible"] is True and fake.calls[-1][2] == {"target": "example.test:443", "timeout": 5.0}
    assert pg_cores.reality_scan(C, "example.test")["tls13"] is True and "timeout" not in fake.calls[-1][2]
    for bad in ("", "https://example.test", "a b", "x;y", "<script>"):
        with pytest.raises(ValueError, match="target is a name"):
            pg_cores.reality_scan(C, bad)


# ------------------------------------------------------------------ the API
@pytest.fixture
def pid():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def url(pid, tail=""):
    return f"/api/panels/{pid}/pg/cores{tail}"


def test_the_api_makes_deletes_and_remakes_a_core(client, fake, pid):
    t = client.get(url(pid, "/template")).json()
    assert t["ok"] is True and t["type"] == "xray" and "wg" in t["types"] and t["config"]["inbounds"][0]["tag"] == "Shadowsocks TCP" and json.loads(t["text"]) == t["config"]
    j = client.post(url(pid), json={"name": "Second", "text": t["text"]}).json()
    assert j["ok"] is True and j["core"]["id"] == 2
    r = client.delete(url(pid, "/2"))
    assert r.status_code == 400 and "confirm: true" in r.json()["error"]                                                  # a delete always asks
    out = client.delete(url(pid, "/2?confirm=true")).json()
    assert out["deleted"] is True
    gone = client.get(url(pid, "/deleted")).json()["cores"]
    assert [g["core"] for g in gone] == [2]
    again = client.post(url(pid, f"/deleted/2/{gone[0]['file']}/recreate")).json()
    assert again["core"]["id"] == 3 and client.delete(url(pid, "/1?confirm=true")).status_code == 400


def test_the_api_deletes_several_cores(client, fake, pid):
    make_second(fake)
    fake.cores[3] = {**copy.deepcopy(fake.cores[2]), "id": 3, "name": "Third"}
    fake.hosts.clear()
    fake.groups.clear()
    fake.nodes.clear()
    r = client.post(url(pid, "/bulk-delete"), json={"ids": [2, 3]})
    assert r.status_code == 400 and "confirm: true" in r.json()["error"] and set(fake.cores) == {1, 2, 3}                 # it always asks
    assert client.post(url(pid, "/bulk-delete"), json={"ids": [1, 2], "confirm": True}).status_code == 400                # never the first
    assert client.post(url(pid, "/bulk-delete"), json={"ids": []}).status_code == 422
    fake.nodes.append({"id": 2, "name": "node-b", "status": "connected", "core_config_id": 2})
    assert client.post(url(pid, "/bulk-delete"), json={"ids": [2]}).status_code == 409                                    # what it takes with it is told first
    fake.nodes.clear()
    ok = client.post(url(pid, "/bulk-delete"), json={"ids": [2, 3], "confirm": True}).json()
    assert ok["ok"] is True and ok["deleted"] == ["Second", "Third"] and set(fake.cores) == {1}


def test_the_api_renames_and_scans(client, fake, pid):
    j = client.patch(url(pid, "/1/meta"), json={"name": "Main", "exclude_inbound_tags": ["a"]}).json()
    assert j["ok"] is True and j["core"]["name"] == "Main" and j["core"]["exclude_inbound_tags"] == ["a"]
    assert client.patch(url(pid, "/1/meta"), json={"name": "Main"}).status_code == 400
    assert client.patch(url(pid, "/1/meta"), json={"name": "Z", "base_rev": "0" * 16}).status_code == 409
    s = client.post(url(pid, "/reality-scan"), json={"target": "example.test:443"}).json()
    assert s["ok"] is True and s["feasible"] is True and client.post(url(pid, "/reality-scan"), json={"target": "https://x"}).status_code == 400


def test_the_api_changes_one_inbound(client, fake, pid):
    spec = {"protocol": "vless", "network": "tcp", "security": "reality", "port": 8443, "reality_dest": "www.example.test:443", "tag": "R2"}
    d = client.post(url(pid, "/1/inbounds/dry-run"), json={"op": "add", "spec": spec}).json()
    assert d["ok"] is True and d["valid"] is True and d["inbound"]["tag"] == "R2" and client.get(url(pid, "/1")).json()["rev"] == d["rev"]
    a = client.post(url(pid, "/1/inbounds/apply"), json={"op": "add", "spec": spec, "base_rev": d["rev"]}).json()
    assert a["ok"] is True and a["public_key"] and a["inbounds"] == 3 and len(fake.cores[1]["config"]["inbounds"]) == 3
    r = client.post(url(pid, "/1/inbounds/apply"), json={"op": "delete", "tag": "VLESS WS 80"})
    assert r.status_code == 409 and "warning" in r.json()["error"]                                                      # hosts and groups use it
    assert client.post(url(pid, "/1/inbounds/apply"), json={"op": "delete", "tag": "VLESS WS 80", "confirm": True}).json()["ok"] is True
    assert client.post(url(pid, "/1/inbounds/apply"), json={"op": "fly"}).status_code == 422
    assert client.post(url(pid, "/1/inbounds/apply"), json={"op": "add"}).status_code == 400


def test_the_api_is_for_signed_in_owners_of_pasarguard_panels_only(client, fake, pid):
    from fastapi.testclient import TestClient

    stranger = TestClient(client.app)
    assert stranger.post(url(pid), json={"name": "x"}).status_code == 401 and stranger.delete(url(pid, "/2?confirm=true")).status_code == 401
    with session() as db:
        mz = Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(mz)
        db.flush()
        mid = mz.id
    assert client.post(url(mid), json={"name": "x"}).status_code == 400 and client.get(url(mid, "/deleted")).status_code == 400
