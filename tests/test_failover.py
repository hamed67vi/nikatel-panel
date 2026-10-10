"""ArvanCloud failover: the pure parts, the plan, the approval flow with a fake Arvan."""
import pytest

from nikatel.services import arvan, failover
from nikatel.services import settings as st

REC = {"id": "r1", "type": "A", "name": "ir", "value": [{"ip": "203.0.113.5", "port": None, "weight": 100, "country": ""}], "ttl": 120, "cloud": False, "upstream_https": "default", "ip_filter_mode": {"count": "single", "order": "none", "geo_filter": "none"}, "created_at": "x"}


def test_swap_ip_changes_only_the_address():
    body = arvan.swap_ip(REC, "203.0.113.5", "203.0.113.9")
    assert body["value"][0]["ip"] == "203.0.113.9" and body["ttl"] == 120 and body["name"] == "ir" and "created_at" not in body and "id" not in body
    assert REC["value"][0]["ip"] == "203.0.113.5"                         # the original is untouched
    assert arvan.swap_ip(REC, "203.0.113.77", "203.0.113.9") is None      # does not point there
    assert arvan.swap_ip({**REC, "type": "CNAME"}, "203.0.113.5", "203.0.113.9") is None
    with pytest.raises(ValueError):
        arvan.swap_ip(REC, "203.0.113.5", "not-an-ip")


def test_pick_target_best_common_server():
    a = {"id": 1, "name": "a"}
    b = {"id": 2, "name": "b"}
    c = {"id": 3, "name": "c"}
    assert failover.pick_target({10: [a, b], 11: [b, a]})["id"] in (1, 2)
    assert failover.pick_target({10: [a, b, c], 11: [b, c]})["id"] == 2
    assert failover.pick_target({10: [a], 11: [b]}) is None
    assert failover.pick_target({}) is None


class FakeArvan:
    def __init__(self):
        self.rec = {**REC, "value": [dict(REC["value"][0])]}
        self.calls = []

    def req(self, method, path, body=None, params=None):
        self.calls.append((method, path))

        if path == "/domains":
            return {"data": [{"domain": "example.test"}], "meta": {"last_page": 1}}

        if path == "/domains/example.test/dns-records":
            return {"data": [self.rec], "meta": {"last_page": 1}}

        if path.endswith("/dns-records/r1"):
            if method == "PUT":
                self.rec = {**self.rec, **body}
            return {"data": self.rec}

        raise AssertionError(path)


def test_find_apply_and_undo_with_fake_arvan(monkeypatch):
    fake = FakeArvan()
    monkeypatch.setattr(arvan, "_req", fake.req)
    found = arvan.find_by_ip("203.0.113.5")
    assert [(f["domain"], f["fqdn"], f["id"]) for f in found] == [("example.test", "ir.example.test", "r1")]
    assert arvan.find_by_ip("203.0.113.99") == []
    arvan.apply("example.test", "r1", "203.0.113.5", "203.0.113.9")
    assert fake.rec["value"][0]["ip"] == "203.0.113.9"
    with pytest.raises(RuntimeError):                                       # it no longer points at the old address: left alone
        arvan.apply("example.test", "r1", "203.0.113.5", "203.0.113.9")


def test_no_key_is_a_clear_error(client):
    with pytest.raises(RuntimeError, match="API key"):
        arvan.domains()


def test_settings_api_hides_the_key(client):
    r = client.put("/api/settings/arvan", json={"api_key": "secret-key-0123456789", "mode": "approve", "confirm_minutes": 5, "domains": ["Example.Test"]})
    assert r.status_code == 200
    j = r.json()
    assert j["api_key"] == "" and j["has_api_key"] is True and j["mode"] == "approve" and j["domains"] == ["example.test"]
    assert client.put("/api/settings/arvan", json={"mode": "banana"}).status_code == 400
    assert client.put("/api/settings/arvan", json={"mode": "off", "confirm_minutes": 1}).status_code == 400
    assert st.get("arvan")["api_key"] == "secret-key-0123456789"            # an empty key in a later save keeps it
    client.put("/api/settings/arvan", json={"mode": "off"})
    assert st.get("arvan")["api_key"] == "secret-key-0123456789"


def test_dismiss_and_state(client):
    st.put("failover", {"pending": [{"id": "1-1", "server_id": 1, "server": "ir1", "ip": "203.0.113.5", "tunnels": [], "dns": [], "target": None}]})
    assert client.get("/api/failover").json()["pending"][0]["id"] == "1-1"
    assert client.post("/api/failover/1-1/dismiss").status_code == 200
    j = client.get("/api/failover").json()
    assert j["pending"] == [] and j["history"][0]["dismissed"] is True
    assert client.post("/api/failover/1-1/dismiss").status_code == 400


def test_restore_puts_one_record_back_and_marks_the_failover_undone(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    fake = FakeArvan()
    fake.rec["value"][0]["ip"] = "203.0.113.9"                              # the failover already pointed it at the new server
    monkeypatch.setattr(arvan, "_req", fake.req)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    entry = {"id": "1-1:r1", "at": 1, "plan_id": "1-1", "server": "ir1", "target": "ir2", "domain": "example.test", "record_id": "r1", "fqdn": "ir.example.test",
             "old": "203.0.113.5", "new": "203.0.113.9", "configs": ["c1"], "restored": False, "restored_at": None}
    hist = {"id": "1-1", "at": 1, "server_id": 1, "server": "ir1", "ip": "203.0.113.5", "target": {"id": 2, "name": "ir2", "ip": "203.0.113.9"}, "moved": 1, "tunnels": 1, "dns": [], "errors": [], "ok": True, "undone": False}
    st.put("failover", {"dns_log": [entry], "history": [hist]})
    j = client.get("/api/failover").json()
    assert j["dns_log"][0]["fqdn"] == "ir.example.test" and j["dns_log"][0]["restored"] is False and j["history"][0]["undone"] is False
    r = client.post("/api/failover/dns/1-1%3Ar1/restore")
    assert r.status_code == 200, r.text
    assert r.json()["old"] == "203.0.113.5" and fake.rec["value"][0]["ip"] == "203.0.113.5"
    j = client.get("/api/failover").json()
    assert j["dns_log"][0]["restored"] is True and j["dns_log"][0]["restored_at"] and j["history"][0]["undone"] is True
    assert client.post("/api/failover/dns/1-1%3Ar1/restore").status_code == 400          # already back
    assert client.post("/api/failover/1-1/undo").status_code == 400                       # nothing left to undo
    assert client.post("/api/failover/dns/nope/restore").status_code == 400


def test_undo_restores_every_record_of_one_failover(monkeypatch):
    fake = FakeArvan()
    fake.rec["value"][0]["ip"] = "203.0.113.9"
    monkeypatch.setattr(arvan, "_req", fake.req)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    e = {"id": "1-1:r1", "at": 1, "plan_id": "1-1", "server": "ir1", "target": "ir2", "domain": "example.test", "record_id": "r1", "fqdn": "ir.example.test", "old": "203.0.113.5", "new": "203.0.113.9", "configs": [], "restored": False, "restored_at": None}
    st.put("failover", {"dns_log": [e], "history": []})
    back = failover.undo("1-1")
    assert len(back) == 1 and back[0]["restored"] is True and fake.rec["value"][0]["ip"] == "203.0.113.5"
    with pytest.raises(ValueError):
        failover.undo("1-1")
