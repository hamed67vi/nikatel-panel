"""Profiles (services/profiles.py, api/profiles.py) and the strip of cells that shows how a server looked (services/uptime.py): the Lab page is built on them. The owner asked (2026-10-14) for a profile
in place of the Iran / Foreign tabs, made first, and said what the add-profile section has: Iran server, Foreign server, PasarGuard node."""
import time

import pytest
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Panel, Profile, Server, ServerSample
from nikatel.services import profiles as svc
from nikatel.services import uptime


# ------------------------------------------------------------------ the kinds (pure)
def test_what_is_added_is_one_of_three_kinds_and_nothing_else():
    assert [svc.check_kind(k) for k in ("iran", " Foreign ", "NODE")] == ["iran", "foreign", "node"]
    assert list(svc.LABEL.values()) == ["Iran server", "Foreign server", "PasarGuard node"]

    for bad in ("", "marzban", "server", None):
        with pytest.raises(ValueError):
            svc.check_kind(bad)


# ------------------------------------------------------------------ the strip of cells (pure)
def test_a_cell_is_up_when_it_holds_half_of_the_samples_it_should_part_when_it_holds_some_and_down_when_none():
    width, start = 1800, 1_000_000                      # 30 minutes = 15 samples of two minutes
    counts = {0: 15, 1: 8, 2: 7, 3: 1}                  # 15 and 8 are "up" (8 >= half of 15), 7 and 1 are "now and then"; the rest is silent
    assert uptime.build_strip(counts, start, width, 6) == "221100"
    assert uptime.up_percent("221100") == round(100 * (2 + 1) / 6)


def test_cells_before_the_server_was_added_have_no_verdict_and_do_not_count():
    width, start = 1800, 1_000_000
    s = uptime.build_strip({3: 15, 4: 15, 5: 15}, start, width, 6, since=start + 3 * width)
    assert s == "---222" and uptime.up_percent(s) == 100
    assert uptime.up_percent("------") is None


def test_a_cell_the_server_was_added_in_expects_only_the_samples_after_that_moment():
    width, start = 1800, 1_000_000
    s = uptime.build_strip({0: 1}, start, width, 6, since=start + width - 120)           # added two minutes before the end of the first cell: one sample is all it could hold
    assert s[0] == "2"


# ------------------------------------------------------------------ the API
def _server(client, name="ir1", host="1.1.1.1", role="iran", **extra):
    r = client.post("/api/servers", json={"name": name, "host": host, "role": role, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _panel(kind="pasarguard", name="pg1", nodes=None) -> int:
    with session() as db:
        p = Panel(name=name, type=kind, url="https://panel.example:8000", nodes=nodes if nodes is not None else [{"id": 7, "name": "n7", "address": "203.0.113.7", "status": "connected", "xray": "1.8"}, {"id": 8, "name": "n8", "address": "203.0.113.8", "status": "error"}])
        db.add(p)
        db.flush()
        return p.id


def test_an_iran_and_a_foreign_server_are_added_by_kind_and_the_kind_must_fit_the_server(client):
    a, b = _server(client, "ir1", "1.1.1.1", "iran"), _server(client, "fo1", "2.2.2.2", "foreign")
    assert client.get("/api/profiles").json()["profiles"] == []

    r = client.post("/api/profiles", json={"kind": "iran", "server_ids": [a["id"]]})
    assert r.status_code == 201, r.text
    p = r.json()["added"][0]
    assert (p["kind"], p["name"], p["address"], p["label"], p["missing"]) == ("iran", "ir1", "1.1.1.1", "Iran server", False)

    assert client.post("/api/profiles", json={"kind": "foreign", "server_ids": [a["id"]]}).status_code == 400             # ir1 is an Iran server
    assert "Iran server" in client.post("/api/profiles", json={"kind": "foreign", "server_ids": [a["id"]]}).json()["error"]
    assert client.post("/api/profiles", json={"kind": "iran", "server_ids": [a["id"]]}).status_code == 409              # once is enough
    assert client.post("/api/profiles", json={"kind": "iran", "server_ids": [9999]}).status_code == 404
    assert client.post("/api/profiles", json={"kind": "iran", "server_ids": []}).status_code == 400
    assert client.post("/api/profiles", json={"kind": "marzban", "server_ids": [a["id"]]}).status_code == 400

    r = client.post("/api/profiles", json={"kind": "foreign", "server_ids": [b["id"], a["id"], 9999]})                  # several at once: what fits is added, the rest says why
    assert r.status_code == 201 and [x["name"] for x in r.json()["added"]] == ["fo1"] and len(r.json()["skipped"]) == 2

    assert [x["name"] for x in client.get("/api/profiles").json()["profiles"]] == ["ir1", "fo1"]


def test_a_node_is_added_from_a_pasarguard_panel_that_knows_it(client):
    pid = _panel()
    r = client.post("/api/profiles", json={"kind": "node", "panel_id": pid, "node_ids": [7, 8]})
    assert r.status_code == 201, r.text
    assert [(x["name"], x["address"], x["panel"], x["status"]) for x in r.json()["added"]] == [("n7", "203.0.113.7", "pg1", "connected"), ("n8", "203.0.113.8", "pg1", "error")]

    assert client.post("/api/profiles", json={"kind": "node", "panel_id": pid, "node_ids": [7]}).status_code == 409
    assert client.post("/api/profiles", json={"kind": "node", "panel_id": pid, "node_ids": [99]}).status_code == 404            # the panel has no node 99
    assert client.post("/api/profiles", json={"kind": "node", "panel_id": 9999, "node_ids": [7]}).status_code == 404
    assert client.post("/api/profiles", json={"kind": "node", "node_ids": [7]}).status_code == 400                           # no panel said

    mz = _panel("marzban", "mz1", [{"id": 1, "name": "m", "address": "x", "status": "connected"}])
    r = client.post("/api/profiles", json={"kind": "node", "panel_id": mz, "node_ids": [1]})
    assert r.status_code == 400 and "PasarGuard" in r.json()["error"]


def test_a_profile_is_taken_off_without_touching_what_it_points_at_and_follows_the_death_of_its_target(client):
    s, other = _server(client, "ir1", "1.1.1.1"), _server(client, "ir2", "3.3.3.3")
    pid = _panel()
    ids = {k: client.post("/api/profiles", json=body).json()["added"][0]["id"] for k, body in (("s", {"kind": "iran", "server_ids": [s["id"]]}), ("o", {"kind": "iran", "server_ids": [other["id"]]}),
                                                                                                ("n", {"kind": "node", "panel_id": pid, "node_ids": [7]}))}

    assert client.delete(f"/api/profiles/{ids['o']}").json()["removed"] == "ir2"
    assert client.get(f"/api/servers/{other['id']}").status_code == 200 and client.delete(f"/api/profiles/{ids['o']}").status_code == 404

    assert client.delete(f"/api/servers/{s['id']}").status_code == 200                                    # a server that is deleted takes its profile with it
    assert [p["kind"] for p in client.get("/api/profiles").json()["profiles"]] == ["node"]

    assert client.delete(f"/api/panels/{pid}").status_code == 200                                         # so does a panel that is removed, for the profiles of its nodes
    assert client.get("/api/profiles").json()["profiles"] == []

    with session() as db:
        assert db.scalars(select(Profile)).all() == []


def test_a_node_that_left_the_panel_is_still_listed_as_missing(client):
    pid = _panel()
    client.post("/api/profiles", json={"kind": "node", "panel_id": pid, "node_ids": [7]})

    with session() as db:
        db.get(Panel, pid).nodes = [{"id": 8, "name": "n8", "address": "203.0.113.8", "status": "error"}]

    p = client.get("/api/profiles").json()["profiles"][0]
    assert p["missing"] is True and p["name"] == "node 7" and p["panel"] == "pg1"


def test_the_strips_come_from_the_samples_for_the_servers_of_the_profiles_only(client):
    a, b = _server(client, "ir1", "1.1.1.1"), _server(client, "ir2", "3.3.3.3")
    client.post("/api/profiles", json={"kind": "iran", "server_ids": [a["id"]]})
    now = int(time.time())

    with session() as db:
        for sid in (a["id"], b["id"]):
            db.get(Server, sid).created_at = now - 3 * 86400

        for k in range(0, 12 * 3600, 120):                                  # answered every two minutes for the last twelve hours, silent before
            db.add(ServerSample(server_id=a["id"], ts=now - k, cpu=1, ram=1, disk=1, load1=0, rx_mbps=0, tx_mbps=0, conn=0))

    r = client.get("/api/profiles/strips?hours=24&buckets=24").json()
    assert list(r["servers"]) == [str(a["id"])]                                                             # ir2 has no profile: it is not asked about
    st = r["servers"][str(a["id"])]
    assert len(st["strip"]) == 24 and st["strip"].endswith("2" * 12) and st["strip"].startswith("0" * 11)
    assert 45 <= st["up"] <= 55

    one = client.get(f"/api/servers/{a['id']}/uptime?hours=24&buckets=24").json()
    assert one["strip"] == st["strip"] and one["up"] == st["up"]
    assert client.get("/api/servers/9999/uptime").status_code == 404
    assert client.get(f"/api/servers/{a['id']}/uptime?hours=9999").status_code == 422
