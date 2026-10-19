"""A record that was changed shows as changed at once (1.69.10; the owner, 2026-10-19: "I changed the DNS, it did not change, after a refresh page it had changed").
The page asks the providers right after the write, and the list of a provider (Cloudflare's, for seconds after a write) can still bring the OLD record: what the write answered is laid on top of the list for a while."""
import copy
import time

import pytest

from nikatel.services import cloudflare, dnscache, failover

TOKEN = "cf-token-0123456789"


class LaggingCloudflare:
    """Cloudflare as the owner met it: the answer to a write is right, but the LIST still shows the records as they were before it while `lag` is on."""
    def __init__(self):
        self.recs, self.seen, self.n, self.lag = {}, {}, 0, True

    def _write(self, before):
        self.seen = before if self.lag else copy.deepcopy(self.recs)           # lagging: the list shows the records as they were BEFORE the last write

    def req(self, token, method, path, body=None, params=None):
        if token != TOKEN:
            raise RuntimeError("Cloudflare refused the token (403)")

        if path == "/zones":
            return {"success": True, "result": [{"id": "z1", "name": "cf.test"}], "result_info": {"total_pages": 1}}

        parts = path.strip("/").split("/")
        rid = parts[3] if len(parts) > 3 else None

        if method == "GET" and rid is None:
            return {"success": True, "result": list((self.recs if not self.lag else self.seen).values()), "result_info": {"total_pages": 1}}

        before = copy.deepcopy(self.recs)

        if method == "POST":
            self.n += 1
            self.recs[str(self.n)] = {**copy.deepcopy(body), "id": str(self.n)}
            self._write(before)
            return {"success": True, "result": self.recs[str(self.n)]}

        if method == "PUT":
            self.recs[rid] = {**copy.deepcopy(body), "id": rid}
            self._write(before)
            return {"success": True, "result": self.recs[rid]}

        if method == "DELETE":
            self.recs.pop(rid, None)
            self._write(before)
            return {"success": True, "result": {"id": rid}}

        return {"success": True, "result": copy.deepcopy(self.recs[rid])}


@pytest.fixture
def cf(client, monkeypatch):
    from nikatel.config import get_settings

    c = LaggingCloudflare()
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(cloudflare, "req", c.req)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    dnscache.PINS.clear()
    dnscache.forget()
    return c


def listed(client, **q):
    return {r["fqdn"]: r for r in client.get("/api/dns/records" + ("?fresh=true" if q.get("fresh") else "")).json()["records"] if r["account"] != "arvan"}


def test_a_record_made_changed_and_deleted_shows_so_at_once_though_the_list_of_the_provider_still_lags(client, cf):
    cid = client.post("/api/dns/accounts", json={"provider": "cloudflare", "name": "CF", "key": TOKEN}).json()["account"]["id"]
    made = client.post("/api/dns/records", json={"account": cid, "domain": "cf.test", "name": "rtr", "ips": ["203.0.113.5"], "ttl": 1, "cloud": False})
    assert made.status_code == 201 and cf.seen == {}                                                          # (the provider's list does not have it yet)
    rid = made.json()["id"]
    assert listed(client, fresh=True)["rtr.cf.test"]["value"] == "203.0.113.5"                                  # made: it is there

    e = client.put(f"/api/dns/records/{cid}/cf.test/{rid}", json={"account": cid, "domain": "cf.test", "name": "rtr", "ips": ["203.0.113.99"], "ttl": 1, "cloud": True})
    assert e.status_code == 200 and cf.seen[rid]["content"] == "203.0.113.5"                                  # (the provider's list still says the OLD address)
    for fresh in (True, False):                                                                              # the read right after the write, and the ones of the next seconds (the page ticks every 15 s)
        row = listed(client, fresh=fresh)["rtr.cf.test"]
        assert row["value"] == "203.0.113.99" and row["cloud"] is True, fresh                                    # changed: it shows changed, not what the lagging list says

    assert client.delete(f"/api/dns/records/{cid}/cf.test/{rid}").status_code == 200
    assert "rtr.cf.test" not in listed(client, fresh=True) and "rtr.cf.test" not in listed(client)              # deleted: gone


def test_a_pin_goes_when_the_provider_agrees_or_when_its_time_is_over(client, cf):
    cid = client.post("/api/dns/accounts", json={"provider": "cloudflare", "name": "CF", "key": TOKEN}).json()["account"]["id"]
    rid = client.post("/api/dns/records", json={"account": cid, "domain": "cf.test", "name": "a", "ips": ["203.0.113.1"], "ttl": 1, "cloud": False}).json()["id"]
    assert len(dnscache.PINS) == 1
    cf.lag = False
    cf.seen = copy.deepcopy(cf.recs)                                                                         # the provider's list caught up
    assert listed(client, fresh=True)["a.cf.test"]["value"] == "203.0.113.1" and dnscache.PINS == {}          # it agrees: the pin is let go
    client.put(f"/api/dns/records/{cid}/cf.test/{rid}", json={"account": cid, "domain": "cf.test", "name": "a", "ips": ["203.0.113.2"], "ttl": 1, "cloud": False})
    assert listed(client, fresh=True)["a.cf.test"]["value"] == "203.0.113.2" and dnscache.PINS == {}          # (no lag any more: the list already says it)
    cf.lag = True
    client.put(f"/api/dns/records/{cid}/cf.test/{rid}", json={"account": cid, "domain": "cf.test", "name": "a", "ips": ["203.0.113.3"], "ttl": 1, "cloud": False})
    assert listed(client, fresh=True)["a.cf.test"]["value"] == "203.0.113.3"
    (key,) = dnscache.PINS
    dnscache.PINS[key]["until"] = time.time() - 1                                                            # its time is over: the provider is believed again, whatever it says
    assert listed(client, fresh=True)["a.cf.test"]["value"] == "203.0.113.2" and dnscache.PINS == {}


def test_apply_pins_changes_adds_and_removes_and_never_touches_other_rows():
    base = [{"account": "x", "domain": "d", "id": "1", "name": "a", "fqdn": "a.d", "type": "A", "value": "1.1.1.1", "ttl": 1, "cloud": False, "ips": ["1.1.1.1"]},
            {"account": "x", "domain": "d", "id": "2", "name": "b", "fqdn": "b.d", "type": "A", "value": "2.2.2.2", "ttl": 1, "cloud": False, "ips": ["2.2.2.2"]}]
    dnscache.PINS.clear()
    dnscache.PINS[("x", "d", "1")] = {"row": {**base[0], "value": "9.9.9.9", "ips": ["9.9.9.9"]}, "until": time.time() + 30}
    dnscache.PINS[("x", "d", "2")] = {"row": None, "until": time.time() + 30}
    dnscache.PINS[("x", "d", "3")] = {"row": {"account": "x", "domain": "d", "id": "3", "name": "c", "fqdn": "c.d", "type": "A", "value": "3.3.3.3", "ttl": 1, "cloud": False, "ips": ["3.3.3.3"]}, "until": time.time() + 30}
    out = dnscache.apply_pins(base)
    assert [(r["id"], r["value"]) for r in out] == [("1", "9.9.9.9"), ("3", "3.3.3.3")] and base[0]["value"] == "1.1.1.1"      # (the rows that were read are not changed in place)
    dnscache.PINS.clear()
    assert dnscache.apply_pins(base) == base

def test_a_write_does_not_make_the_next_look_read_every_domain_of_every_account_again(client, cf, monkeypatch):
    """The owner, 2026-10-20: "when I change a name or a DNS I must refresh the page". The write dropped the copy (`forget`), so the read right after it asked every domain of every account before it
    answered: 20-30 seconds with 26 domains, and the page said "Record changed" and kept the old record meanwhile. A write only marks the copy old (`stale`): the page gets the rows - with the write laid
    on them - at once, and the providers are read in the background."""
    cid = client.post("/api/dns/accounts", json={"provider": "cloudflare", "name": "CF", "key": TOKEN}).json()["account"]["id"]
    rid = client.post("/api/dns/records", json={"account": cid, "domain": "cf.test", "name": "a", "ips": ["203.0.113.1"], "ttl": 1, "cloud": False}).json()["id"]
    assert listed(client, fresh=True)["a.cf.test"]["value"] == "203.0.113.1"                                  # the copy is read once
    reads, asked, real = [], [], cloudflare.req

    def counting(token, method, path, body=None, params=None):
        if method == "GET" and path.endswith("/dns_records"):
            reads.append(path)

        return real(token, method, path, body, params)

    monkeypatch.setattr(cloudflare, "req", counting)
    monkeypatch.setattr(dnscache, "refresh_in_background", lambda: asked.append(1))
    assert client.put(f"/api/dns/records/{cid}/cf.test/{rid}", json={"account": cid, "domain": "cf.test", "name": "a", "ips": ["203.0.113.2"], "ttl": 1, "cloud": False}).status_code == 200
    reads.clear()
    assert listed(client)["a.cf.test"]["value"] == "203.0.113.2"                                              # the page's read right after the write: the new value ...
    assert reads == [] and asked == [1]                                                                      # ... with no read of any provider before the answer; the renewal is asked for
    assert client.delete(f"/api/dns/records/{cid}/cf.test/{rid}").status_code == 200
    assert "a.cf.test" not in listed(client) and reads == []                                                 # a delete the same


def test_stale_keeps_the_rows_and_never_makes_up_a_copy_that_was_not_read():
    old = dict(dnscache.CACHE)
    try:
        dnscache.CACHE.update(at=0.0, rows=[])
        dnscache.stale()
        assert dnscache.CACHE["at"] == 0.0                                                                  # nothing was read: the next look reads
        dnscache.CACHE.update(at=time.time(), rows=[{"id": "1"}])
        dnscache.stale()
        assert dnscache.CACHE["at"] == 1.0 and dnscache.CACHE["rows"] == [{"id": "1"}]                      # old, but its rows are kept
    finally:
        dnscache.CACHE.update(old)
