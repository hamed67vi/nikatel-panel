"""A DNS record follows its tunnel when a HOST of a PasarGuard panel dials its name on the tunnel's port (the owner, 2026-11-03: the US TLS config died after its tunnel moved to another Iran server; the TLS
hosts live in the panel, not in the probe configs, and their records have no CDN origin port, so the record was tied to no tunnel and was left on the old server)."""
import pytest
from sqlalchemy import select
from test_failover import FakeProv

from nikatel.db import session
from nikatel.models import Panel, Server, Tunnel
from nikatel.services import autopilot, failover, panels


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    monkeypatch.setattr(failover, "ENABLED", True)
    monkeypatch.setattr(failover.dnsprov, "accounts", lambda: [object()])
    monkeypatch.setattr(failover.dnsprov, "provider", lambda acc: FakeProv(acc))
    failover._HOSTS_LAST.clear()
    FakeProv.calls = []


def _servers():
    """ir1 carries `us` (the ports 8079, 1829, 11429) and `ca` (1840, 8075); ir2 is the other Iran server; fi1 is abroad."""
    with session() as db:
        ir1, ir2, fi1 = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="ir2", host="203.0.113.9", role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir1, ir2, fi1])
        db.flush()
        us = Tunnel(name="us", type="backpack", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi1.id, state="up", forwarded_ports=[8079, 1829, 11429])
        ca = Tunnel(name="ca", type="backpack", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi1.id, state="up", forwarded_ports=[1840, 8075])
        db.add_all([us, ca])
        db.flush()
        return {"ir1": ir1.id, "ir2": ir2.id, "us": us.id, "ca": ca.id}


def _rec(acc, name, port=None, domain="example.test"):
    return {"account": acc, "account_name": acc, "provider": "x", "domain": domain, "id": "r-" + name, "name": name, "fqdn": f"{name}.{domain}", "ip": "203.0.113.5", "ips": ["203.0.113.5"], "ttl": 120, "cloud": False, "port": port}


def _records_of_the_old_server(monkeypatch):
    """The US world: the CDN record names its origin port; the TLS record (a Cloudflare record) names nothing; `other` is dialled on a port no tunnel forwards."""
    recs = [_rec("cf1", "tls-us"), _rec("arvan", "cdn-us", port=8079), _rec("cf1", "other")]
    monkeypatch.setattr(failover, "_fetch_records", lambda ip: ([dict(r) for r in recs], ""))
    monkeypatch.setattr(failover, "_host_ports", lambda: ({"tls-us.example.test": {11429}, "other.example.test": {9999}}, ""))


def test_the_names_and_ports_of_the_hosts_are_read_the_same_way_for_every_kind_of_row():
    rows = [{"address": ["TLS-US.example.test"], "port": 11429},
            {"address": "a.example.test, b.example.test", "port": "8075"},
            {"address": ["no-port.example.test"], "port": None},
            {"address": ["range.example.test"], "port": "1000-2000"},
            {"address": ["flag.example.test"], "port": True},
            {"address": ["tls-us.example.test"], "port": 1829, "is_disabled": True},
            {"address": [], "port": 80}]
    assert failover._hosts_by_name(rows) == {"tls-us.example.test": {11429, 1829}, "a.example.test": {8075}, "b.example.test": {8075}}


def test_a_record_follows_the_tunnel_whose_port_a_host_dials_on_its_name(monkeypatch):
    ids = _servers()
    _records_of_the_old_server(monkeypatch)
    recs, err = failover._records("203.0.113.5", failover._tunnels_of(ids["ir1"]))
    assert err == "" and {r["name"]: r["tunnels"] for r in recs} == {"tls-us": [ids["us"]], "cdn-us": [ids["us"]], "other": []}


def test_a_wildcard_record_serves_the_names_under_it_that_have_no_record_of_their_own(monkeypatch):
    ids = _servers()
    monkeypatch.setattr(failover, "_fetch_records", lambda ip: ([_rec("cf1", "*"), _rec("cf1", "plain")], ""))
    monkeypatch.setattr(failover, "_host_ports", lambda: ({"x.example.test": {8075}, "plain.example.test": {11429}}, ""))
    recs, _ = failover._records("203.0.113.5", failover._tunnels_of(ids["ir1"]))
    assert {r["name"]: r["tunnels"] for r in recs} == {"*": [ids["ca"]], "plain": [ids["us"]]}


def test_moving_the_us_tunnel_takes_both_of_its_records_and_says_which_one_it_left(monkeypatch):
    """The real case: the old plan took only the CDN record; the TLS record stayed on the old server and the config did not connect."""
    ids = _servers()
    _records_of_the_old_server(monkeypatch)
    p = failover._make_plan(ids["ir1"], {ids["us"]}, "manual", target_id=ids["ir2"])
    assert [t["name"] for t in p["tunnels"]] == ["us"] and p["target"]["ip"] == "203.0.113.9" and p["dns_error"] == ""
    assert sorted(d["fqdn"] for d in p["dns"]) == ["cdn-us.example.test", "tls-us.example.test"]
    assert [(d["fqdn"], d["tunnels"]) for d in p["left_alone"]] == [("other.example.test", [])]


def test_the_two_records_really_change_when_the_plan_runs(monkeypatch):
    ids = _servers()
    _records_of_the_old_server(monkeypatch)
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, text, **kw: said.append(text))
    p = failover._make_plan(ids["ir1"], {ids["us"]}, "manual", target_id=ids["ir2"])
    p["id"] = "1-1"                                                                                # (move_server gives the plan its id)
    done, errors, logged = failover._apply_dns(7, p)
    assert errors == [] and sorted(d["fqdn"] for d in done) == ["cdn-us.example.test", "tls-us.example.test"]
    assert sorted((c[1], c[3], c[4]) for c in FakeProv.calls) == [("arvan", "r-cdn-us", ["203.0.113.9"]), ("cf1", "r-tls-us", ["203.0.113.9"])] and len(logged) == 2


def test_a_record_that_stays_is_told_in_the_story_with_the_reason(monkeypatch):
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, text, **kw: said.append(text))
    failover._say_left(1, {"ip": "203.0.113.5", "left_alone": [{"fqdn": "a.example.test", "tunnels": [3]}, {"fqdn": "b.example.test", "tunnels": []}]})
    failover._say_left(1, {"ip": "203.0.113.5"})                                                  # (a plan kept from an older version has no such list)
    assert len(said) == 2 and "a.example.test" in said[0] and "also serves a tunnel that stays" in said[0] and "b.example.test" in said[1] and "nothing tells which tunnel" in said[1]


def test_a_whole_server_move_takes_every_record_without_asking_the_panels(monkeypatch):
    ids = _servers()
    _records_of_the_old_server(monkeypatch)

    def asked():
        raise AssertionError("the panels must not be asked for a whole-server move")

    monkeypatch.setattr(failover, "_host_ports", asked)
    p = failover._make_plan(ids["ir1"], None, "down", target_id=ids["ir2"])
    assert p["whole"] is True and len(p["dns"]) == 3 and p["left_alone"] == []


def test_hosts_that_cannot_be_read_stop_a_partial_move_with_a_plain_sentence(monkeypatch):
    ids = _servers()
    monkeypatch.setattr(failover, "_fetch_records", lambda ip: ([_rec("cf1", "tls-us")], ""))
    monkeypatch.setattr(failover, "_host_ports", lambda: ({}, "the hosts of the PasarGuard panel pg could not be read (timed out)"))
    monkeypatch.setattr(failover, "move_blocked", lambda: "")
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: [])
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [{"id": ids["ir2"], "name": "ir2", "score": 80, "quality": 90, "resources": 80, "tunnels": 0, "online": True}])
    p = failover.move_plan(ids["ir1"], ids["ir2"], [ids["us"]])
    assert "could not be read" in p["dns_error"] and p["dns_error"] in p["problems"]
    with pytest.raises(ValueError, match="could not be read"):
        failover.move_server(ids["ir1"], ids["ir2"], None, [ids["us"]])


def _pg_panels():
    with session() as db:
        db.add_all([Panel(name="pg", type="pasarguard", url="https://pg.example.test", enabled=True),
                    Panel(name="pg-off", type="pasarguard", url="https://pg-off.example.test", enabled=False),
                    Panel(name="mz", type="marzban", url="https://mz.example.test", enabled=True)])
        db.flush()
        return {p.name: p.id for p in db.scalars(select(Panel))}


def test_the_hosts_are_read_from_the_pasarguard_panels_that_are_on_and_the_last_reading_covers_a_short_outage(monkeypatch):
    ids = _pg_panels()
    asked, reply = [], [{"hosts": [{"address": ["tls-us.example.test"], "port": 11429}]}]
    monkeypatch.setattr(panels, "cred_for", lambda pid: {"id": pid})

    def call(c, method, path, **kw):
        asked.append((c["id"], method, path))

        if isinstance(reply[0], Exception):
            raise reply[0]

        return reply[0]

    monkeypatch.setattr(panels, "call", call)
    assert failover._host_ports() == ({"tls-us.example.test": {11429}}, "") and asked == [(ids["pg"], "GET", "/api/hosts")]        # (a Marzban panel and a switched-off panel are not asked)
    reply[0] = panels.PanelError("timed out")
    assert failover._host_ports() == ({"tls-us.example.test": {11429}}, "")                         # the panel is down for a minute: the earlier reading is used
    failover._HOSTS_LAST.clear()
    out, err = failover._host_ports()
    assert out == {} and "PasarGuard panel pg could not be read" in err and "timed out" in err
