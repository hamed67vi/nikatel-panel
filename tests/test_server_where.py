"""Add server without a role: the panel says where the server is (Iran or foreign) from the geo services, and asks when they do not answer."""
from nikatel.services import nodes


def test_where_turns_a_country_into_a_role(client, monkeypatch):
    monkeypatch.setattr(nodes, "lookup", lambda host, want_provider=True: {"country_code": "IR", "country": "Iran", "city": "Tehran", "sources": 3})
    w = client.get("/api/servers/where?host=203.0.113.5").json()
    assert w["role"] == "iran" and w["country"] == "Iran"
    monkeypatch.setattr(nodes, "lookup", lambda host, want_provider=True: {"country_code": "FR", "country": "France", "city": "Paris", "sources": 2})
    assert client.get("/api/servers/where?host=203.0.113.6").json()["role"] == "foreign"
    monkeypatch.setattr(nodes, "lookup", lambda host, want_provider=True: {"country_code": "", "country": "", "city": "", "sources": 0})
    assert client.get("/api/servers/where?host=203.0.113.7").json()["role"] == ""
    assert client.get("/api/servers/where?host=bad host").status_code == 422


def test_a_server_added_without_a_role_gets_the_detected_one_or_a_clear_refusal(client, monkeypatch):
    monkeypatch.setattr(nodes, "lookup", lambda host, want_provider=True: {"country_code": "FR", "country": "France", "city": "", "sources": 2})
    r = client.post("/api/servers", json={"name": "fr9", "host": "203.0.113.6"})
    assert r.status_code == 201 and r.json()["role"] == "foreign" and r.json()["country"] == "FR"
    monkeypatch.setattr(nodes, "lookup", lambda host, want_provider=True: {"country_code": "", "country": "", "city": "", "sources": 0})
    r = client.post("/api/servers", json={"name": "x9", "host": "203.0.113.7"})
    assert r.status_code == 400 and "where" in r.json()["error"]
    assert client.post("/api/servers", json={"name": "x9", "host": "203.0.113.7", "role": "iran"}).status_code == 201     # given by hand: fine
    e = client.put("/api/servers/1", json={"name": "fr9", "host": "203.0.113.6"})                                      # an edit without a role keeps the role
    assert e.status_code == 200 and e.json()["role"] == "foreign"
