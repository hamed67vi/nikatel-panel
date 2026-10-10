"""Data centres: providers out of the geo answers, grouping of our servers by provider, the API."""
from nikatel.services import dcs
from nikatel.services.nodes import domain_from_rdap, provider_from


def test_provider_from_each_geo_service():
    p = provider_from("ipwho", {"connection": {"asn": 24940, "org": "Example Hosting GmbH", "isp": "Example Hosting", "domain": "www.Example-Host.com"}})
    assert p == {"isp": "Example Hosting", "org": "Example Hosting GmbH", "domain": "example-host.com", "asn": "24940"}
    p = provider_from("ip-api", {"isp": "Example Hosting", "org": "Example Hosting GmbH", "as": "AS24940 Example Hosting GmbH"})
    assert p["asn"] == "24940" and p["isp"] == "Example Hosting" and p["domain"] == ""
    p = provider_from("ipinfo", {"org": "AS24940 Example Hosting GmbH"})
    assert p["org"] == "Example Hosting GmbH" and p["asn"] == "24940"
    assert provider_from("country.is", {"country": "DE"})["isp"] == ""


def test_domain_from_rdap_skips_the_registries():
    doc = {"entities": [{"vcardArray": ["vcard", [["fn", {}, "text", "RIPE"], ["email", {}, "text", "abuse@ripe.net"]]]},
                        {"entities": [{"vcardArray": ["vcard", [["email", {}, "text", "abuse@example-host.com"], ["email", {}, "text", "noc@example-host.com"]]]}]}]}
    assert domain_from_rdap(doc) == "example-host.com"
    assert domain_from_rdap({"entities": []}) == ""


def test_group_servers_by_provider_with_tunnels():
    servers = [{"name": "ir1", "role": "iran", "online": True, "country": "IR", "provider": "Iran Host", "domain": "iranhost.example"},
               {"name": "ir2", "role": "iran", "online": False, "country": "IR", "provider": "Iran Host", "domain": "iranhost.example"},
               {"name": "fi1", "role": "foreign", "online": True, "country": "FI", "provider": "Example Hosting", "domain": "example-host.com", "city": "Helsinki"},
               {"name": "x1", "role": "foreign", "online": None, "country": "", "provider": "", "domain": ""}]
    tunnels = [{"name": "t1", "state": "up", "iran": "ir1", "foreign": "fi1"}, {"name": "t2", "state": "down", "iran": "ir2", "foreign": "fi1"}]
    rows = dcs.group(servers, tunnels)
    assert [r["name"] for r in rows] == ["Iran Host", "Example Hosting", "unknown provider"]      # Iran first, then by size
    ir = rows[0]
    assert ir["total"] == 2 and ir["online"] == 1 and ir["role"] == "iran" and ir["tunnels_total"] == 2 and ir["tunnels_bad"] == 1
    fi = rows[1]
    assert fi["domain"] == "example-host.com" and fi["city"] == "Helsinki" and fi["tunnels_total"] == 2 and fi["tunnels_bad"] == 1
    assert rows[2]["total"] == 1 and rows[2]["domain"] == ""


def test_datacentres_api(client, monkeypatch):
    monkeypatch.setattr(dcs, "sites", lambda domains: {d: {"ok": True, "code": 200, "ms": 120, "at": 1} for d in domains})
    r = client.get("/api/datacentres")
    assert r.status_code == 200 and r.json()["ok"] is True and isinstance(r.json()["rows"], list)
