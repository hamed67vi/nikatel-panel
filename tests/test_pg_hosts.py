"""The hosts of a PasarGuard 5.4 panel in full: a host as the page sees it and as the panel wants it (the READ and the WRITE names of the xray mux), the checks in front of the panel
(the advanced settings are checked key by key because the panel drops an unknown key without a word), make / change / clone / delete / bulk, the backups and the restore, the API.
A fake panel stands in for PasarGuard and keeps ITS rules found on a live 5.4.1 panel (a host is made with POST /api/host/ - the path without the slash only redirects -, address is a list,
priority is required, an inbound tag must exist, security is one of three, unknown keys of a setting are dropped, the mux is read with other names than it is written with); no test
reaches a real panel."""
import copy
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Panel
from nikatel.services import panels, pg_hosts
from nikatel.services.panels import PanelError

C = {"id": 4, "type": "pasarguard", "username": "boss"}


def out_host(hid: int, remark: str, **kw) -> dict:
    """A host as the panel READS it (every setting an object full of nulls, the mux with the read names)."""
    h = {"id": hid, "remark": remark, "address": ["a.example.test"], "inbound_tag": "VLESS WS", "port": 8443, "sni": ["s.example.test"], "host": ["h.example.test"], "path": "/ws", "security": "tls",
         "alpn": ["h2", "http/1.1"], "fingerprint": "chrome", "allowinsecure": False, "is_disabled": False, "http_headers": {"X-Test": "1"},
         "transport_settings": {"xhttp_settings": None, "grpc_settings": None, "kcp_settings": None, "tcp_settings": None, "websocket_settings": {"heartbeatPeriod": 30}},
         "mux_settings": {"sing_box": None, "clash": None, "xray": {"enabled": True, "concurrency": 8, "xudpConcurrency": 16, "xudpProxyUDP443": "allow"}},
         "fragment_settings": {"xray": {"packets": "tlshello", "length": "100-200", "interval": "10-20", "maxSplit": None}, "sing_box": None},
         "noise_settings": {"xray": [{"type": "rand", "packet": "10-20", "delay": "10-16", "apply_to": "ip", "rand": None}]}, "random_user_agent": True, "use_sni_as_host": False, "vless_route": "",
         "priority": hid, "status": ["active", "on_hold"], "ech": {"xray": {"config_list": "", "query_strategy": "none"}, "mihomo": None, "sing_box": None}, "pinned_peer_cert_sha256": None,
         "verify_peer_cert_by_name": ["v.example.test"], "wireguard_overrides": None, "subscription_templates": None, "final_mask_settings": None, "cipher_suites": None}
    h.update(kw)
    return h


class Fake:
    def __init__(self):
        self.hosts = {1: out_host(1, "WS one"), 2: out_host(2, "Plain", security="none", sni=None, alpn=None, mux_settings=None, noise_settings=None, fragment_settings=None, ech=None, status=None, http_headers=None,
                                                         transport_settings=None, verify_peer_cert_by_name=None, host=None)}
        self.tags = {"VLESS WS": ("vless", "ws"), "TROJAN TCP": ("trojan", "tcp")}
        self.next = 3
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail: dict[tuple[str, str], PanelError] = {}

    def __call__(self, c, method, path, **kw):
        u = urlsplit(path)
        q = parse_qs(u.query)
        body = kw.get("body")
        self.calls.append((method, path, copy.deepcopy(body)))

        if (method, u.path) in self.fail:
            raise self.fail[(method, u.path)]

        if u.path == "/api/inbounds/details":
            return [{"tag": t, "protocol": p, "network": n} for t, (p, n) in self.tags.items()]

        if u.path == "/api/inbounds":
            return list(self.tags)

        if u.path == "/api/client_templates/simple":
            return {"templates": [{"id": 2, "name": "Default Xray Subscription", "template_type": "xray_subscription", "is_default": True}, {"id": 1, "name": "Clash", "template_type": "clash_subscription", "is_default": True}], "total": 2}

        if u.path == "/api/hosts" and method == "GET":
            ids = [int(x) for x in q.get("ids", [])]
            return [copy.deepcopy(h) for h in self.hosts.values() if not ids or h["id"] in ids]

        if u.path == "/api/host":
            raise PanelError("HTTP 307: the panel redirected POST /api/host to /api/host/ instead of doing it", 307)

        if u.path == "/api/host/" and method == "POST":
            hid, self.next = self.next, self.next + 1
            self.hosts[hid] = self.store(hid, body)
            return copy.deepcopy(self.hosts[hid])

        m = re.fullmatch(r"/api/host/(\d+)", u.path)

        if m:
            hid = int(m.group(1))

            if hid not in self.hosts:
                raise PanelError("HTTP 404: Host not found", 404)

            if method == "GET":
                return copy.deepcopy(self.hosts[hid])

            if method == "PUT":
                self.hosts[hid] = self.store(hid, body)
                return copy.deepcopy(self.hosts[hid])

            if method == "DELETE":
                del self.hosts[hid]
                return None

        m = re.fullmatch(r"/api/hosts/bulk/(enable|disable|delete)", u.path)

        if m:
            ids = body["ids"]

            for i in ids:
                if m.group(1) == "delete":
                    self.hosts.pop(i, None)
                else:
                    self.hosts[i]["is_disabled"] = m.group(1) == "disable"

            return {"hosts": [str(i) for i in ids], "count": len(ids)}

        raise AssertionError(f"unexpected call {method} {path}")

    def store(self, hid, body):
        """CreateHost in, BaseHost out: the rules of the real panel."""
        if not isinstance(body.get("address", []), list):
            raise PanelError("HTTP 422: {'address': 'Input should be a valid set'}", 422)

        if "priority" not in body:
            raise PanelError("HTTP 422: {'priority': 'Field required'}", 422)

        if body.get("security", "inbound_default") not in ("inbound_default", "none", "tls"):
            raise PanelError("HTTP 422: {'security': \"Input should be 'inbound_default', 'none' or 'tls'\"}", 422)

        if body.get("alpn") is not None and not isinstance(body["alpn"], list):
            raise PanelError("HTTP 422: {'alpn': 'Input should be a valid list'}", 422)

        if body.get("inbound_tag") and body["inbound_tag"] not in self.tags:
            raise PanelError(f"HTTP 400: {body['inbound_tag']} not found", 400)

        h = out_host(hid, "")
        h.update({k: copy.deepcopy(v) for k, v in body.items() if k in h and k not in ("id", "transport_settings", "mux_settings", "fragment_settings", "noise_settings", "ech")})
        t = body.get("transport_settings") or {}
        h["transport_settings"] = {k: copy.deepcopy(t.get(k)) for k in ("xhttp_settings", "grpc_settings", "kcp_settings", "tcp_settings", "websocket_settings")}       # an unknown key is dropped
        x = ((body.get("mux_settings") or {}).get("xray")) or {}
        h["mux_settings"] = {"sing_box": None, "clash": None, "xray": {"enabled": bool(x.get("enabled")), "concurrency": x.get("concurrency"), "xudpConcurrency": x.get("xudp_concurrency"),
                                                                           "xudpProxyUDP443": x.get("xudp_proxy_udp_443", "reject")}}                                        # the WRITE names are read back as the READ names
        h["fragment_settings"] = copy.deepcopy(body.get("fragment_settings")) or {"xray": None, "sing_box": None}
        h["noise_settings"] = copy.deepcopy(body.get("noise_settings")) or {"xray": None}
        h["ech"] = copy.deepcopy(body.get("ech")) or {"xray": None, "mihomo": None, "sing_box": None}
        h["address"] = list(dict.fromkeys(body.get("address", [])))
        return h

    def writes(self):
        return [x for x in self.calls if x[0] != "GET"]


@pytest.fixture(autouse=True)
def clean_backups():
    d = get_settings().data_dir / "pg-backups"

    if d.exists():
        for f in d.glob("panel4-host-*"):
            f.unlink()


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    return f


# ------------------------------------------------------------------ pure
def test_a_host_is_shown_with_the_names_the_panel_writes_and_without_the_empty_leaves():
    row = pg_hosts.host_row(out_host(1, "WS one"))
    assert row["id"] == 1 and row["address"] == ["a.example.test"] and row["alpn"] == ["h2", "http/1.1"] and row["status"] == ["active", "on_hold"] and row["allowinsecure"] is False and row["priority"] == 1
    assert json.loads(row["json"]["mux_settings"]) == {"xray": {"enabled": True, "concurrency": 8, "xudp_concurrency": 16, "xudp_proxy_udp_443": "allow"}}              # the write names, no null
    assert json.loads(row["json"]["transport_settings"]) == {"websocket_settings": {"heartbeatPeriod": 30}} and json.loads(row["json"]["http_headers"]) == {"X-Test": "1"}
    assert json.loads(row["json"]["fragment_settings"]) == {"xray": {"packets": "tlshello", "length": "100-200", "interval": "10-20"}}
    assert row["json"]["wireguard_overrides"] == "" and row["json"]["final_mask_settings"] == "" and row["json"]["subscription_templates"] == ""                              # nothing set: empty text
    assert json.loads(row["json"]["ech"]) == {"xray": {"query_strategy": "none"}}
    plain = pg_hosts.host_row(out_host(2, "Plain", security="none", sni=None, status=None, alpn=None, allowinsecure=None, transport_settings={"websocket_settings": None}))
    assert plain["sni"] == [] and plain["status"] is None and plain["alpn"] == [] and plain["allowinsecure"] is None and plain["json"]["transport_settings"] == ""


def test_a_row_never_crashes_on_a_shape_it_does_not_know():
    for junk in (None, {}, [], "x", {"address": "a,b\nc", "port": "80", "status": "active", "mux_settings": "x", "priority": "3"}):
        row = pg_hosts.host_row(junk)
        assert row["remark"] == "" and row["security"] == "inbound_default" and isinstance(row["json"], dict) and set(row["json"]) == set(pg_hosts.JSON_KEYS)
    r = pg_hosts.host_row({"address": "a,b\nc"})
    assert r["address"] == ["a", "b", "c"] and r["port"] is None and r["priority"] == 0


def test_a_new_host_needs_a_remark_and_gets_the_panels_defaults():
    body, changed = pg_hosts.apply({"remark": " Fast ", "address": "a.example.test, b.example.test\na.example.test", "port": "443", "inbound_tag": "VLESS WS"}, None, 7)
    assert body["remark"] == "Fast" and body["address"] == ["a.example.test", "b.example.test"] and body["port"] == 443 and body["inbound_tag"] == "VLESS WS" and body["priority"] == 7
    assert body["security"] == "inbound_default" and body["fingerprint"] == "" and body["is_disabled"] is False and "id" not in body and set(changed) == {"remark", "address", "port", "inbound_tag"}

    for data in ({}, {"remark": ""}, {"remark": "  "}, {"address": "x"}):
        with pytest.raises(ValueError, match="remark"):
            pg_hosts.apply(data, None)


def test_every_field_is_checked_in_plain_words():
    bad = [({"remark": "x" * 257}, "1 to 256"), ({"address": ["has space"]}, "not a name"), ({"sni": ["a b"]}, "not a name"), ({"address": [f"h{i}.example.test" for i in range(101)]}, "at most 100"),
           ({"port": 0.5}, "between"), ({"port": 70000}, "between"), ({"port": "x"}, "number"), ({"inbound_tag": "x" * 129}, "at most 128"), ({"path": "x" * 1025}, "at most 1024"),
           ({"security": "reality"}, "security is one of"), ({"fingerprint": "ie6"}, "fingerprint"), ({"alpn": ["spdy"]}, "alpn is a list of"), ({"allowinsecure": "maybe"}, "yes, no or the default"),
           ({"priority": -1}, "between"), ({"priority": "high"}, "number"), ({"status": ["gone"]}, "user statuses"), ({"cipher_suites": "x" * 1025}, "at most 1024"),
           ({"vless_route": "x" * 129}, "at most 128"), ({"pinned_peer_cert_sha256": "x" * 257}, "at most 256")]

    for data, why in bad:
        with pytest.raises(ValueError, match=why):
            pg_hosts.apply({"remark": "r", **data}, None)


def test_the_advanced_settings_are_checked_key_by_key_because_the_panel_drops_an_unknown_key_silently():
    ok, _ = pg_hosts.apply({"remark": "r", "transport_settings": '{"websocket_settings": {"heartbeatPeriod": 10}}', "mux_settings": {"xray": {"enabled": True, "xudpConcurrency": 5}}, "http_headers": {"Any": "thing"}}, None)
    assert ok["transport_settings"] == {"websocket_settings": {"heartbeatPeriod": 10}} and ok["http_headers"] == {"Any": "thing"}
    assert ok["mux_settings"]["xray"] == {"enabled": True, "xudp_concurrency": 5}                                                                                          # a read name pasted is turned into the write name
    empty, _ = pg_hosts.apply({"remark": "r", "transport_settings": "", "ech": None, "noise_settings": {}, "final_mask_settings": "  "}, None)
    assert empty["transport_settings"] is None and empty["ech"] is None and empty["noise_settings"] is None and empty["final_mask_settings"] is None

    bad = [({"transport_settings": {"fly": 1}}, "does not know fly"), ({"mux_settings": {"xray": {}, "wrong": 1}}, "does not know wrong"), ({"ech": '{"xray": 1,'}, "not valid JSON"),
           ({"fragment_settings": "[1, 2]"}, "JSON object is expected"), ({"final_mask_settings": 5}, "JSON object is expected"), ({"wireguard_overrides": {"dns": ["1.1.1.1"], "bogus": 1}}, "does not know bogus"),
           ({"subscription_templates": {"clash": 1}}, "does not know clash"), ({"noise_settings": "x" * 70000}, "too long")]

    for data, why in bad:
        with pytest.raises(ValueError, match=why):
            pg_hosts.apply({"remark": "r", **data}, None)


def test_a_change_names_only_the_fields_that_differ():
    cur = out_host(1, "WS one")
    body, changed = pg_hosts.apply({"remark": "WS one", "address": ["a.example.test"], "port": 8443, "security": "tls", "alpn": ["h2", "http/1.1"], "priority": 1, "is_disabled": False,
                                    "mux_settings": json.loads(pg_hosts.host_row(cur)["json"]["mux_settings"]), "status": ["on_hold", "active"]}, cur)
    assert changed == [] and body["remark"] == "WS one"                                                                                                           # the same values: nothing
    body, changed = pg_hosts.apply({"remark": "Renamed", "sni": ["new.example.test"], "is_disabled": True, "priority": 9, "fragment_settings": {"xray": {"packets": "1-3", "length": "10", "interval": "5"}}}, cur)
    assert sorted(changed) == ["fragment_settings", "is_disabled", "priority", "remark", "sni"] and body["sni"] == ["new.example.test"] and body["host"] == ["h.example.test"]
    assert body["mux_settings"]["xray"] == {"enabled": True, "concurrency": 8, "xudp_concurrency": 16, "xudp_proxy_udp_443": "allow"}                              # the rest goes back with the WRITE names
    assert "id" not in body


def test_an_empty_object_on_the_panel_and_an_empty_box_on_the_page_are_the_same_thing():
    """Found on a real panel: hosts with `http_headers: {}` came back as changed on every save, because the page shows an unset setting as an empty box."""
    cur = out_host(2, "Plain", http_headers={}, transport_settings={"websocket_settings": None, "grpc_settings": None}, ech=None, noise_settings={"xray": None})
    row = pg_hosts.host_row(cur)
    assert row["json"]["http_headers"] == "" and row["json"]["transport_settings"] == "" and row["json"]["noise_settings"] == ""
    _body, changed = pg_hosts.apply({"http_headers": row["json"]["http_headers"], "transport_settings": row["json"]["transport_settings"], "ech": "", "noise_settings": ""}, cur)
    assert changed == []


def test_a_list_that_is_null_on_the_panel_stays_null_when_nothing_is_typed():
    cur = out_host(2, "Plain", sni=None, alpn=None, host=None, verify_peer_cert_by_name=None)
    body, changed = pg_hosts.apply({"sni": "", "alpn": [], "host": [], "verify_peer_cert_by_name": ""}, cur)
    assert changed == [] and body["sni"] is None and body["alpn"] is None
    body, changed = pg_hosts.apply({"sni": ["x.example.test"], "alpn": ["h3"]}, cur)
    assert sorted(changed) == ["alpn", "sni"] and body["alpn"] == ["h3"]
    body, changed = pg_hosts.apply({"sni": []}, out_host(1, "WS one"))                                                                                         # a list with names in it can be emptied
    assert changed == ["sni"] and body["sni"] == []


# ------------------------------------------------------------------ reading
def test_the_options_of_the_form(fake):
    o = pg_hosts.options(C)
    assert o["inbounds"][0] == {"tag": "VLESS WS", "protocol": "vless", "network": "ws"} and o["templates"] == [{"id": 2, "name": "Default Xray Subscription"}] and o["next_priority"] == 3
    assert "tls" in o["securities"] and "h3" in o["alpns"] and "chrome" in o["fingerprints"] and "on_hold" in o["statuses"] and o["json_keys"]["transport_settings"][0] == "grpc_settings"
    fake.fail[("GET", "/api/inbounds/details")] = PanelError("HTTP 404: not found", 404)
    assert pg_hosts.options(C)["inbounds"] == [{"tag": "VLESS WS", "protocol": "", "network": ""}, {"tag": "TROJAN TCP", "protocol": "", "network": ""}]              # an older panel: its plain list of tags
    fake.fail[("GET", "/api/client_templates/simple")] = PanelError("HTTP 404: not found", 404)
    assert pg_hosts.options(C)["templates"] == []


def test_one_host_in_full_and_the_form_of_a_new_one(fake):
    v = pg_hosts.host_view(C, 1)
    assert v["host"]["remark"] == "WS one" and v["options"]["inbounds"]
    n = pg_hosts.new_view(C)
    assert n["host"]["remark"] == "" and n["host"]["priority"] == 3 and n["host"]["address"] == [] and n["host"]["security"] == "inbound_default"

    with pytest.raises(PanelError, match="not found"):
        pg_hosts.host_view(C, 99)


# ------------------------------------------------------------------ writing
def test_a_host_is_made_with_the_slash_and_the_next_priority(fake):
    out = pg_hosts.host_create(C, {"remark": "New", "address": ["n.example.test"], "inbound_tag": "TROJAN TCP", "security": "tls", "alpn": ["h2"], "mux_settings": {"xray": {"enabled": True, "concurrency": 4}}})
    assert out["host"]["id"] == 3 and out["host"]["priority"] == 3 and out["host"]["alpn"] == ["h2"] and out["host"]["json"]["mux_settings"]
    method, path, body = fake.writes()[-1]
    assert (method, path) == ("POST", "/api/host/") and body["priority"] == 3 and body["address"] == ["n.example.test"]
    assert pg_hosts.host_create(C, {"remark": "Second", "priority": 20})["host"]["priority"] == 20

    for data, why in (({"address": ["x.example.test"]}, "remark"), ({"remark": "r", "inbound_tag": "ghost"}, "ghost not found")):
        with pytest.raises((ValueError, PanelError), match=why):
            pg_hosts.host_create(C, data)


def test_a_change_goes_back_whole_with_the_write_names_and_keeps_the_xudp_settings(fake):
    """The bug that was in the old editor: the read-back goes into the PUT and the panel resets xudpConcurrency and xudpProxyUDP443."""
    out = pg_hosts.host_update(C, 1, {"remark": "Renamed", "path": "/new"})
    assert out["changed"] == ["path", "remark"] and out["host"]["remark"] == "Renamed" and out["backup"].startswith("panel4-host-1-")
    assert fake.hosts[1]["mux_settings"]["xray"] == {"enabled": True, "concurrency": 8, "xudpConcurrency": 16, "xudpProxyUDP443": "allow"}                              # still there
    method, path, body = fake.writes()[-1]
    assert (method, path) == ("PUT", "/api/host/1") and body["mux_settings"]["xray"]["xudp_concurrency"] == 16 and "id" not in body and body["remark"] == "Renamed"


def test_a_change_with_nothing_new_is_refused_before_the_panel_is_asked(fake):
    with pytest.raises(ValueError, match="nothing to change"):
        pg_hosts.host_update(C, 1, {"remark": "WS one", "port": 8443})

    assert fake.writes() == [] and pg_hosts.host_backups(C) == []


def test_the_advanced_settings_can_be_set_changed_and_cleared(fake):
    pg_hosts.host_update(C, 2, {"final_mask_settings": {"tcp": [{"type": "fragment", "settings": {"packets": "1-3"}}]}, "subscription_templates": {"xray": 2}})
    assert fake.hosts[2]["final_mask_settings"]["tcp"][0]["type"] == "fragment" and fake.hosts[2]["subscription_templates"] == {"xray": 2}
    out = pg_hosts.host_update(C, 1, {"mux_settings": "", "http_headers": None})
    assert sorted(out["changed"]) == ["http_headers", "mux_settings"] and json.loads(out["host"]["json"]["mux_settings"])["xray"]["enabled"] is False and out["host"]["json"]["http_headers"] == ""

    with pytest.raises(ValueError, match="does not know fly"):
        pg_hosts.host_update(C, 1, {"transport_settings": {"fly": 1}})                                                                                           # and the websocket setting is not lost

    assert fake.hosts[1]["transport_settings"]["websocket_settings"] == {"heartbeatPeriod": 30}


def test_the_panels_refusal_reaches_the_page(fake):
    fake.fail[("PUT", "/api/host/1")] = PanelError("HTTP 400: VLESS WS not found", 400)

    with pytest.raises(PanelError, match="not found"):
        pg_hosts.host_update(C, 1, {"remark": "x"})


def test_a_clone_is_switched_off_and_gets_the_next_priority(fake):
    out = pg_hosts.host_clone(C, 1)
    assert out["host"]["remark"] == "WS one copy" and out["host"]["is_disabled"] is True and out["host"]["priority"] == 3 and out["host"]["id"] == 3
    method, path, body = fake.writes()[-1]
    assert (method, path) == ("POST", "/api/host/") and "id" not in body and body["mux_settings"]["xray"]["xudp_concurrency"] == 16
    assert fake.hosts[1]["is_disabled"] is False                                                                                                                 # the original is not touched


def test_a_delete_needs_a_confirm_and_is_saved_first(fake):
    with pytest.raises(ValueError, match="confirm"):
        pg_hosts.host_delete(C, 1)

    assert 1 in fake.hosts
    out = pg_hosts.host_delete(C, 1, True)
    assert out["deleted"] is True and 1 not in fake.hosts and out["backup"].startswith("panel4-host-1-")
    bk = pg_hosts.host_backups(C)
    assert len(bk) == 1 and bk[0]["host_id"] == 1 and bk[0]["remark"] == "WS one" and bk[0]["why"] == "before a delete"


def test_a_deleted_host_is_made_again_and_a_changed_one_put_back(fake):
    pg_hosts.host_delete(C, 1, True)
    name = pg_hosts.host_backups(C)[0]["file"]
    out = pg_hosts.host_restore(C, name)
    assert out["made_again"] is True and out["host"]["remark"] == "WS one" and out["host"]["id"] == 3 and fake.hosts[3]["mux_settings"]["xray"]["xudpConcurrency"] == 16
    assert fake.writes()[-1][:2] == ("POST", "/api/host/")
    pg_hosts.host_update(C, 3, {"remark": "Changed"})
    older = next(b for b in pg_hosts.host_backups(C) if b["why"] == "before a delete")["file"]
    out = pg_hosts.host_restore(C, older)                                                                                                                         # the id 1 is not there: made again
    assert out["made_again"] is True
    changed = next(b for b in pg_hosts.host_backups(C) if b["why"] == "before a change")
    assert changed["host_id"] == 3
    out = pg_hosts.host_restore(C, changed["file"])
    assert out["made_again"] is False and fake.hosts[3]["remark"] == "WS one" and out["backup"]


def test_only_a_file_of_this_panel_and_this_kind_is_a_backup(fake):
    for bad in ("../../etc/passwd", "panel9-host-1-20260101-000000.json", "panel4-host-1-20260101-000000.txt", "x", "panel4-host-1-20260101-000000.json", "panel4-role-1-20260101-000000.json"):
        with pytest.raises(ValueError, match="unknown backup"):
            pg_hosts.host_restore(C, bad)


def test_several_hosts_at_once(fake):
    out = pg_hosts.bulk(C, "disable", [1, 2, 2])
    assert out["count"] == 2 and fake.hosts[1]["is_disabled"] is True and fake.writes()[-1][2] == {"ids": [1, 2]}
    assert pg_hosts.bulk(C, "enable", [1])["count"] == 1 and fake.hosts[1]["is_disabled"] is False

    with pytest.raises(ValueError, match="confirm"):
        pg_hosts.bulk(C, "delete", [1, 2])

    assert len(fake.hosts) == 2
    out = pg_hosts.bulk(C, "delete", [1, 2], confirm=True)
    assert out["count"] == 2 and fake.hosts == {} and sorted(b["host_id"] for b in pg_hosts.host_backups(C)) == [1, 2]

    for what, ids, why in (("fly", [1], "action is one of"), ("enable", [], "at least one"), ("enable", "1", "at least one"), ("enable", [0], "at least one"), ("enable", [True], "at least one"),
                           ("enable", list(range(1, 202)), "at most 200")):
        with pytest.raises(ValueError, match=why):
            pg_hosts.bulk(C, what, ids)


# ------------------------------------------------------------------ the API
@pytest.fixture
def pid():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="boss", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def test_the_api_makes_changes_clones_and_deletes_hosts(client, fake, pid):
    base = f"/api/panels/{pid}/pg/hosts"
    j = client.get(base + "/new").json()
    assert j["ok"] is True and j["host"]["priority"] == 3 and j["options"]["inbounds"]
    made = client.post(base, json={"remark": "New", "address": ["n.example.test"], "inbound_tag": "VLESS WS", "alpn": ["h2"], "transport_settings": '{"websocket_settings": {"heartbeatPeriod": 5}}'}).json()
    hid = made["host"]["id"]
    assert made["ok"] is True and made["host"]["json"]["transport_settings"] and fake.writes()[-1][1] == "/api/host/"
    assert client.post(base, json={"address": ["x"]}).status_code == 400 and client.post(base, json={"remark": "r", "security": "reality"}).status_code == 400
    assert client.post(base, json={"remark": "r", "inbound_tag": "ghost"}).status_code == 502                                                                    # the panel's own 400 is shown
    v = client.get(f"{base}/{hid}").json()
    assert v["host"]["remark"] == "New" and v["options"]["templates"]
    ch = client.put(f"{base}/{hid}", json={"remark": "Renamed", "status": ["active"], "allowinsecure": "true"}).json()
    assert ch["ok"] is True and ch["changed"] == ["allowinsecure", "remark", "status"] and ch["host"]["allowinsecure"] is True
    assert client.put(f"{base}/{hid}", json={"remark": "Renamed"}).status_code == 400 and client.put(f"{base}/99", json={"remark": "x"}).status_code == 404
    assert client.put(f"{base}/{hid}", json={"ech": {"nope": 1}}).status_code == 400 and client.put(f"{base}/{hid}", json={"remark": "x" * 300}).status_code == 422
    cl = client.post(f"{base}/{hid}/clone").json()
    assert cl["host"]["is_disabled"] is True and cl["host"]["remark"] == "Renamed copy"
    assert client.delete(f"{base}/{hid}").status_code == 400 and client.delete(f"{base}/{hid}?confirm=true").json()["deleted"] is True
    bk = client.get(base + "/backups").json()["backups"]
    assert bk and client.post(f"{base}/restore/{bk[0]['file']}").json()["made_again"] is True
    assert client.post(base + "/bulk", json={"what": "disable", "ids": [1, 2]}).json()["count"] == 2
    assert client.post(base + "/bulk", json={"what": "delete", "ids": [1]}).status_code == 400 and client.post(base + "/bulk", json={"what": "enable", "ids": []}).status_code == 400
    with session() as db:
        texts = [e.text for e in db.query(Event).all()]
        assert any(t.startswith("PasarGuard alpha: host “New” made") for t in texts) and any("host “Renamed” changed (allowinsecure, remark, status)" in t for t in texts)


def test_the_api_is_for_signed_in_owners_of_pasarguard_panels_only(client, fake, pid):
    from fastapi.testclient import TestClient

    stranger = TestClient(client.app)
    assert stranger.get(f"/api/panels/{pid}/pg/hosts/1").status_code == 401 and stranger.delete(f"/api/panels/{pid}/pg/hosts/1?confirm=true").status_code == 401
    with session() as db:
        mz = Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(mz)
        db.flush()
        mid = mz.id
    assert client.get(f"/api/panels/{mid}/pg/hosts/1").status_code == 400 and client.get("/api/panels/9999/pg/hosts/1").status_code == 404


def test_every_route_of_the_hosts_has_a_short_description(client):
    spec = client.app.openapi()
    mine = {(m, p): op for p, ops in spec["paths"].items() if "/pg/hosts" in p for m, op in ops.items()}
    assert len(mine) == 9
    assert all(0 < len((op.get("description") or "").split("\n")[0]) <= 300 for op in mine.values())
