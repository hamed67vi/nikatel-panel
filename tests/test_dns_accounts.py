"""DNS accounts: ArvanCloud (several) and Cloudflare under one list; records made, changed and deleted by hand, each kept with what puts it back."""
import copy

import pytest

from nikatel.services import arvan, cloudflare, dnsprov, failover
from nikatel.services import settings as st


def test_a_record_body_is_checked_and_keeps_the_rest_of_the_record():
    b = arvan.a_body("Sub", ["203.0.113.5", "203.0.113.6"], 300, True)
    assert b["type"] == "a" and b["name"] == "sub" and b["ttl"] == 300 and b["cloud"] is True
    assert [i["ip"] for i in b["value"]] == ["203.0.113.5", "203.0.113.6"] and b["ip_filter_mode"]["count"] == "multi"
    base = {"type": "a", "name": "old", "ttl": 120, "cloud": False, "upstream_https": "https", "value": [{"ip": "203.0.113.5", "port": 8443, "weight": 3, "country": "ir"}], "id": "x"}
    c = arvan.a_body("old", ["203.0.113.5"], 120, False, base=base)
    assert c["upstream_https"] == "https" and c["value"][0]["port"] == 8443 and "id" not in c      # what the form does not ask is kept as read

    for bad in (("a b", ["203.0.113.5"], 120), ("ok", ["not-an-ip"], 120), ("ok", [], 120), ("ok", ["203.0.113.5", "203.0.113.5"], 120), ("ok", ["203.0.113.5"], 7)):
        with pytest.raises(ValueError):
            arvan.a_body(*bad)


class FakeArvan:
    def __init__(self):
        self.recs = {"9": {"id": "9", "type": "mx", "name": "@", "value": {"host": "mail.example.test", "priority": 10}, "ttl": 120}}
        self.n = 0

    def req(self, method, path, body=None, params=None):
        if path == "/domains":
            return {"data": [{"domain": "example.test"}], "meta": {"last_page": 1}}

        parts = path.strip("/").split("/")
        rid = parts[3] if len(parts) > 3 else None

        if method == "GET" and rid is None:
            return {"data": list(self.recs.values()), "meta": {"last_page": 1}}

        if method == "POST":
            self.n += 1
            self.recs[str(self.n)] = {**copy.deepcopy(body), "id": str(self.n)}
            return {"data": {"id": str(self.n)}}

        if method == "PUT":
            self.recs[rid] = {**copy.deepcopy(body), "id": rid}
            return {}

        if method == "DELETE":
            self.recs.pop(rid, None)
            return {}

        if rid not in self.recs:
            raise RuntimeError("ArvanCloud answered 404: not found")

        return {"data": copy.deepcopy(self.recs[rid])}


class FakeCloudflare:
    def __init__(self):
        self.recs = {}
        self.n = 0

    def req(self, token, method, path, body=None, params=None):
        if token != "cf-token-0123456789":
            raise RuntimeError("Cloudflare refused the token (403)")

        if path == "/zones":
            return {"success": True, "result": [{"id": "z1", "name": "cf.test"}], "result_info": {"total_pages": 1}}

        parts = path.strip("/").split("/")
        rid = parts[3] if len(parts) > 3 else None

        if method == "GET" and rid is None:
            return {"success": True, "result": list(self.recs.values()), "result_info": {"total_pages": 1}}

        if method == "POST":
            self.n += 1
            self.recs[str(self.n)] = {**copy.deepcopy(body), "id": str(self.n)}
            return {"success": True, "result": self.recs[str(self.n)]}

        if method == "PUT":
            self.recs[rid] = {**copy.deepcopy(body), "id": rid}
            return {"success": True, "result": self.recs[rid]}

        if method == "DELETE":
            self.recs.pop(rid, None)
            return {"success": True, "result": {"id": rid}}

        return {"success": True, "result": copy.deepcopy(self.recs[rid])}


@pytest.fixture
def fakes(client, monkeypatch):
    from nikatel.api import dns as api_dns
    from nikatel.config import get_settings

    a, c = FakeArvan(), FakeCloudflare()
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(arvan, "_req", a.req)
    monkeypatch.setattr(cloudflare, "req", c.req)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    api_dns._CACHE["at"] = 0.0
    st.put("arvan", {"api_key": "arvan-key-0123456789"})
    return a, c


def test_accounts_are_added_after_the_key_is_tried_and_the_key_never_comes_back(client, fakes):
    assert client.post("/api/dns/accounts", json={"provider": "cloudflare", "name": "My CF", "key": "wrong-token-123456"}).status_code == 502
    assert len(client.get("/api/dns/accounts").json()["accounts"]) == 1                 # nothing saved for a key that does not work
    r = client.post("/api/dns/accounts", json={"provider": "cloudflare", "name": "My CF", "key": "cf-token-0123456789"})
    assert r.status_code == 201 and r.json()["account"]["provider"] == "cloudflare"
    lst = client.get("/api/dns/accounts").json()["accounts"]
    assert [x["provider"] for x in lst] == ["arvan", "cloudflare"] and all("key" not in x for x in lst) and lst[1]["key_tail"] == "6789"
    assert client.delete("/api/dns/accounts/" + lst[1]["id"]).status_code == 200
    assert len(client.get("/api/dns/accounts").json()["accounts"]) == 1


def test_every_type_is_listed_and_a_records_are_made_changed_deleted_and_restored(client, fakes):
    arv, cf = fakes
    cid = client.post("/api/dns/accounts", json={"provider": "cloudflare", "name": "CF", "key": "cf-token-0123456789"}).json()["account"]["id"]

    # Arvan: an MX record is listed (read only) beside the A record that is made
    r = client.post("/api/dns/records", json={"account": "arvan", "domain": "example.test", "name": "nk-test", "ips": ["203.0.113.9"], "ttl": 120, "cloud": False})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    rows = client.get("/api/dns/records?fresh=true").json()["records"]
    kinds = {(x["type"], x["editable"]) for x in rows}
    assert ("MX", False) in kinds and ("A", True) in kinds and any(x["value"] == "mail.example.test (priority 10)" for x in rows)
    assert client.post("/api/dns/records", json={"account": "arvan", "domain": "nope.test", "name": "x", "ips": ["203.0.113.9"]}).status_code == 400
    assert client.post("/api/dns/records", json={"account": "arvan", "domain": "example.test", "name": "x", "ips": ["1.1.1"]}).status_code == 400
    e = client.put(f"/api/dns/records/arvan/example.test/{rid}", json={"account": "arvan", "domain": "example.test", "name": "nk-test", "ips": ["203.0.113.10"], "ttl": 300, "cloud": False})
    assert e.status_code == 200 and arv.recs[rid]["value"][0]["ip"] == "203.0.113.10" and arv.recs[rid]["ttl"] == 300
    assert client.delete(f"/api/dns/records/arvan/example.test/{rid}").status_code == 200 and rid not in arv.recs

    # Cloudflare: one address per record
    assert client.post("/api/dns/records", json={"account": cid, "domain": "cf.test", "name": "a", "ips": ["203.0.113.1", "203.0.113.2"], "ttl": 1}).status_code == 400
    made = client.post("/api/dns/records", json={"account": cid, "domain": "cf.test", "name": "a", "ips": ["203.0.113.1"], "ttl": 1, "cloud": True})
    assert made.status_code == 201 and cf.recs[made.json()["id"]]["name"] == "a.cf.test" and cf.recs[made.json()["id"]]["proxied"] is True
    cr = client.get("/api/dns/records?fresh=true").json()["records"]
    assert any(x["account"] == cid and x["fqdn"] == "a.cf.test" and x["cloud"] is True and x["ttl"] == 1 for x in cr)

    # the log: newest first, each can be put back through its own account
    log = client.get("/api/failover").json()["dns_log"]
    assert [(x["op"], x["account"]) for x in log] == [("create", cid), ("delete", "arvan"), ("edit", "arvan"), ("create", "arvan")]
    path = lambda x: "/api/failover/dns/" + x["id"].replace(":", "%3A") + "/restore"
    assert client.post(path(log[1])).status_code == 200 and any(v.get("value") and v["value"][0]["ip"] == "203.0.113.10" for v in arv.recs.values() if v.get("type") == "a")
    assert client.post(path(log[2])).status_code == 200 and any(v.get("value") and v["value"][0]["ip"] == "203.0.113.9" for v in arv.recs.values() if v.get("type") == "a")
    assert client.post(path(log[0])).status_code == 200 and not any(v.get("name") == "a.cf.test" for v in cf.recs.values())      # the Cloudflare record is deleted again
    assert client.post(path(log[2])).status_code == 400                                       # not twice


def test_the_main_arvan_key_can_be_removed(client, fakes):
    assert client.delete("/api/dns/accounts/arvan").status_code == 200
    assert dnsprov.accounts() == []


def test_the_record_list_is_served_from_the_cache_and_renewed_in_the_background(client, fakes, monkeypatch):
    import time as _t

    from nikatel.api import dns as api_dns

    api_dns._CACHE.update(at=0.0, rows=[], accounts=[], refreshing=False)
    first = client.get("/api/dns/records").json()
    assert first["refreshing"] is False and first["cached_at"] > 0 and any(r["type"] == "MX" for r in first["records"])
    api_dns._CACHE["at"] = _t.time() - 600                               # stale: the page gets the old rows at once, a thread renews them
    seen = []
    monkeypatch.setattr(api_dns, "_read_all", lambda: (seen.append(1), api_dns._CACHE.update(at=_t.time())))
    stale = client.get("/api/dns/records").json()
    assert any(r["type"] == "MX" for r in stale["records"])
    _t.sleep(0.3)
    assert seen == [1] and client.get("/api/dns/records").json()["refreshing"] is False
    assert client.get("/api/dns/records?fresh=true").json()["ok"] and seen == [1, 1]           # fresh waits for a real read


def test_a_cloudflare_key_that_is_not_a_token_gets_a_plain_answer(monkeypatch):
    import httpx

    from nikatel.services import cloudflare

    class Reply:
        status_code = 400
        text = '{"success":false,"errors":[{"message":"Invalid request headers"}]}'

        def json(self):
            return {"success": False, "errors": [{"message": "Invalid request headers"}]}

    monkeypatch.setattr(httpx, "request", lambda *a, **k: Reply())

    try:
        cloudflare.req("0123456789abcdef0123456789abcdef01234", "GET", "/zones")
    except RuntimeError as e:
        assert "API Tokens" in str(e) and "37 characters" in str(e)
    else:
        raise AssertionError("a wrong key should be refused in plain words")

    try:
        cloudflare.req("tok\u0646en", "GET", "/zones")
    except RuntimeError as e:
        assert "copy it again" in str(e)
    else:
        raise AssertionError("a non-ASCII token should be refused before it is sent")

def test_other_record_types_are_built_and_refused_in_plain_words():
    from nikatel.services import arvan, dnsprov

    aaaa = arvan.other_body("AAAA", "v6", ["2001:db8::1"], "", 120, True)
    assert aaaa["type"] == "aaaa" and aaaa["value"][0]["ip"] == "2001:db8::1" and aaaa["cloud"] is True
    cname = arvan.other_body("cname", "www", [], "target.example.com.", 300, False)
    assert cname["value"] == {"host": "target.example.com", "host_header": "source"}
    txt = arvan.other_body("txt", "@", [], "v=spf1 -all", 120, False)
    assert txt["value"] == {"text": "v=spf1 -all"} and "cloud" not in txt

    for bad in (("aaaa", "x", ["203.0.113.1"], ""), ("cname", "x", [], "not a host"), ("txt", "x", [], ""), ("mx", "x", [], "")):
        try:
            arvan.other_body(bad[0], bad[1], bad[2], bad[3])
        except ValueError:
            continue

        raise AssertionError(f"{bad} should be refused")

    cf = dnsprov.CloudflareProvider._other_body("example.com", "CNAME", "www", [], "target.example.net", 1, True)
    assert cf == {"type": "CNAME", "name": "www.example.com", "content": "target.example.net", "ttl": 1, "proxied": True}
    cf6 = dnsprov.CloudflareProvider._other_body("example.com", "AAAA", "@", ["2001:db8::2"], "", 300, False)
    assert cf6["name"] == "example.com" and cf6["content"] == "2001:db8::2"
    cft = dnsprov.CloudflareProvider._other_body("example.com", "TXT", "t", [], "hello", 300, False)
    assert "proxied" not in cft


def test_the_account_id_is_recognised_when_it_is_pasted_instead_of_the_token():
    from nikatel.services import cloudflare

    try:
        cloudflare.req("8ea4cc21fbaea5e9b0563323b22278eb", "GET", "/zones")
    except RuntimeError as e:
        assert "Account ID" in str(e) and "cfat_" in str(e)
    else:
        raise AssertionError("an Account ID should be named as such")
