"""What a tunnel is for (services/purpose.py, 1.69.216; the owner, 2026-11-04: "the tunnels that are for eco or AI - write it on them"): the hosts of the economy (coefficient) and AI configs are placed on tunnels like the
configs of the probe, and the Tunnels page says ECO / AI next to the name."""
from pathlib import Path

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import purpose

SERVERS = [{"id": 1, "name": "ir-alpha", "host": "203.0.113.10"}, {"id": 2, "name": "ir-beta", "host": "203.0.113.20"}]
TUNNELS = [{"id": 10, "name": "nl1-ir-alpha", "iran_id": 1, "ports": {8072, 1828, 11428}}, {"id": 11, "name": "nl2-ir-beta", "iran_id": 2, "ports": {11443}},
           {"id": 12, "name": "us-ir-alpha", "iran_id": 1, "ports": {1818, 1819, 1830}}]
RECORDS = [{"fqdn": "econl.example.com", "type": "A", "ips": ["203.0.113.20"], "port": None}, {"fqdn": "ai-us.example.com", "type": "A", "ips": ["203.0.113.10"], "port": None},
           {"fqdn": "nlss.example.com", "type": "A", "ips": ["203.0.113.10"], "port": None}]


def host(remark, address, port, disabled=False):
    return {"remark": remark, "address": address, "port": port, "disabled": disabled}


def test_a_host_is_eco_ai_or_nothing():
    assert purpose.purpose_of("🇳🇱 Netherlands ضریب x2", "econl.rakvul.ir") == "eco"
    assert purpose.purpose_of("🇹🇷 Turkey ضریب x2", "203.0.113.99") == "eco"               # the coefficient in the remark is enough
    assert purpose.purpose_of("anything", "ECOde.rakvul.ir") == "eco"                         # the address, whatever its case
    assert purpose.purpose_of("🇺🇸 AI-1 🤖", "ai-us.majalekhodrostore.ir") == "ai"
    assert purpose.purpose_of("🇩🇪 AI-2 🤖", "203.0.113.99") == "ai"
    assert purpose.purpose_of("🇳🇱 Netherlands", "nlss.rakvul.ir") == ""
    assert purpose.purpose_of("Maintain", "x.example.com") == ""                              # "ai" inside a word is no AI config
    assert purpose.purpose_of("🇩🇪 AI-2", ["ai-de.example.com", "other"]) == "ai"             # a list of addresses: the first


def test_every_tunnel_is_told_the_purposes_of_the_hosts_that_end_on_it():
    hosts = [host("🇳🇱 Netherlands ضریب x2", "econl.example.com", 11443), host("🇳🇱 Netherlands", "nlss.example.com", 11428), host("🇺🇸 AI-1 🤖", "ai-us.example.com", 1830),
             host("🇺🇸 AI-1 🤖", "ai-us.example.com", 1819)]
    got = purpose.tunnel_purposes(hosts, TUNNELS, SERVERS, RECORDS)
    assert got == {11: ["eco"], 12: ["ai"]}                  # the plain Netherlands config labels nothing: nl1-ir-alpha carries it and is neither


def test_nothing_is_guessed():
    hosts = [host("ضریب", "econl.example.com", 9999),         # no tunnel of that Iran server forwards the port
             host("ضریب", "unknown.example.com", 11443),     # a name no record points at an Iran server
             host("ضریب", "econl.example.com", 11443, disabled=True),
             host("ضریب", "econl.example.com", None),
             host("ضریب", "", 11443)]
    assert purpose.tunnel_purposes(hosts, TUNNELS, SERVERS, RECORDS) == {}


def test_a_tunnel_that_carries_both_says_both():
    both = [{"id": 10, "name": "t", "iran_id": 1, "ports": {1830, 11443}}]
    hosts = [host("AI-1", "ai-us.example.com", 1830), host("ضریب", "ai-us.example.com", 11443)]
    assert purpose.tunnel_purposes(hosts, both, SERVERS, RECORDS) == {10: ["ai", "eco"]}


def _tunnels():
    with session() as db:
        a, f = Server(name="ir-s", host="203.0.113.1", role="iran", online=True), Server(name="fx-s", host="203.0.113.2", role="foreign", online=True)
        db.add_all([a, f])
        db.flush()
        t1 = Tunnel(name="t-one", type="backhaul", transport="tcpmux", iran_server_id=a.id, foreign_server_id=f.id, state="up", forwarded_ports=[80], record={"keep": 1, "purpose": ["ai"]})
        t2 = Tunnel(name="t-two", type="backhaul", transport="tcpmux", iran_server_id=a.id, foreign_server_id=f.id, state="up", forwarded_ports=[81], record={"keep": 2})
        db.add_all([t1, t2])
        db.flush()
        return t1.id, t2.id


def test_the_labels_are_written_changed_and_taken_away_and_the_rest_of_the_record_stays():
    one, two = _tunnels()
    assert purpose.store({two: ["eco"]}) == 2                  # t-two gets eco, t-one loses its old ai
    with session() as db:
        assert db.get(Tunnel, one).record == {"keep": 1} and db.get(Tunnel, two).record == {"keep": 2, "purpose": ["eco"]}

    assert purpose.store({two: ["eco"]}) == 0                  # nothing changed: nothing written


def test_the_tunnels_page_writes_it_next_to_the_name():
    app = (Path(purpose.__file__).parent.parent / "web" / "app.js").read_text(encoding="utf-8")
    assert "const purposeTags = t =>" in app and "${esc(t.name)}${purposeTags(t)}${wrench(t)}" in app
    assert 'eco: ["ECO"' in app and 'ai: ["AI"' in app
