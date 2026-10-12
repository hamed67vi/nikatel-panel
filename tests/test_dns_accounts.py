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
