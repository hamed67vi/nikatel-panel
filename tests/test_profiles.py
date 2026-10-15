"""Profiles (services/profiles.py, api/profiles.py) and the strip of cells that shows how a server looked (services/uptime.py): the Lab page is built on them. The owner asked (2026-10-14) for a profile
in place of the Iran / Foreign tabs, made first, and said what the add-profile section has: Iran server, Foreign server, PasarGuard node. Then the old lists went; a server that is made is on the page
at once, and the servers the panel already had are NOT moved there by themselves ("I did not say to move the cards, only to bring the information of the servers"): Add profile picks them."""
import time

import pytest
from sqlalchemy import select

from nikatel.db import init_db, session
from nikatel.models import Panel, Profile, Server, ServerSample
from nikatel.services import nodes, panels, uptime
from nikatel.services import profiles as svc


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


def _bare(name, host, role="iran") -> int:
    """A server with no profile: one that was taken off the page (or made by a script that never heard of profiles)."""
    with session() as db:
        s = Server(name=name, host=host, role=role)
        db.add(s)
        db.flush()
        return s.id


def _panel(kind="pasarguard", name="pg1", nodes_=None) -> int:
    with session() as db:
        p = Panel(name=name, type=kind, url="https://panel.example:8000", nodes=nodes_ if nodes_ is not None else [{"id": 7, "name": "n7", "address": "203.0.113.7", "status": "connected", "xray": "1.8"}, {"id": 8, "name": "n8", "address": "203.0.113.8", "status": "error"}])
        db.add(p)
        db.flush()
        return p.id


def _profiles(client):
    return client.get("/api/profiles").json()["profiles"]


def test_every_server_that_is_made_is_on_the_page_at_once_and_its_kind_follows_its_role(client, monkeypatch):
    a = _server(client, "ir1", "1.1.1.1", "iran")
    assert [(p["kind"], p["name"], p["server_id"]) for p in _profiles(client)] == [("iran", "ir1", a["id"])]

    r = client.put(f"/api/servers/{a['id']}", json={"name": "ir1", "host": "1.1.1.1", "role": "foreign"})                  # it was abroad after all
    assert r.status_code == 200 and [p["kind"] for p in _profiles(client)] == ["foreign"]

    def no_ssh(*a, **k):
        raise nodes.SSHError("no key in a test")

    monkeypatch.setattr(nodes, "run_script", no_ssh)
    r = client.post("/api/servers/add-many", json={"role": "iran", "lines": "ir5 3.3.3.3\nir6 4.4.4.4 root 2222"})          # several at once: each made server is on the page, even when its login fails
    assert r.status_code == 200 and [i["ok"] for i in r.json()["items"]] == [False, False]
    assert sorted((p["kind"], p["name"]) for p in _profiles(client)) == [("foreign", "ir1"), ("iran", "ir5"), ("iran", "ir6")]


def test_the_servers_the_panel_already_had_are_not_put_on_the_page_by_the_upgrade_they_are_picked_in_add_profile(client):
    ids = [_bare("ir1", "1.1.1.1", "iran"), _bare("fo1", "2.2.2.2", "foreign")]
    init_db()                                                                       # (what an upgrade runs on start)
    assert _profiles(client) == []                                                  # no card is moved by itself

    assert sorted(s["name"] for s in client.get("/api/servers").json()) == ["fo1", "ir1"]            # but they are all there, with their information, for the picker (and for the tunnels, the checks)
    assert client.post("/api/profiles", json={"kind": "iran", "server_ids": [ids[0]]}).status_code == 201
    assert [(p["kind"], p["name"]) for p in _profiles(client)] == [("iran", "ir1")]


def test_a_server_that_was_taken_off_the_page_is_put_back_by_kind_and_the_kind_must_fit(client):
    a, b = _bare("ir1", "1.1.1.1", "iran"), _bare("fo1", "2.2.2.2", "foreign")
    assert _profiles(client) == []

    r = client.post("/api/profiles", json={"kind": "iran", "server_ids": [a]})
    assert r.status_code == 201, r.text
    p = r.json()["added"][0]
    assert (p["kind"], p["name"], p["address"], p["label"], p["missing"]) == ("iran", "ir1", "1.1.1.1", "Iran server", False)

    assert client.post("/api/profiles", json={"kind": "foreign", "server_ids": [a]}).status_code == 400             # ir1 is an Iran server
    assert "Iran server" in client.post("/api/profiles", json={"kind": "foreign", "server_ids": [a]}).json()["error"]
    assert client.post("/api/profiles", json={"kind": "iran", "server_ids": [a]}).status_code == 409              # once is enough
    assert client.post("/api/profiles", json={"kind": "iran", "server_ids": [9999]}).status_code == 404
    assert client.post("/api/profiles", json={"kind": "iran", "server_ids": []}).status_code == 400
    assert client.post("/api/profiles", json={"kind": "marzban", "server_ids": [a]}).status_code == 400

    r = client.post("/api/profiles", json={"kind": "foreign", "server_ids": [b, a, 9999]})                          # several at once: what fits is added, the rest says why
    assert r.status_code == 201 and [x["name"] for x in r.json()["added"]] == ["fo1"] and len(r.json()["skipped"]) == 2

    assert [x["name"] for x in _profiles(client)] == ["ir1", "fo1"]


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
    node = client.post("/api/profiles", json={"kind": "node", "panel_id": pid, "node_ids": [7]}).json()["added"][0]["id"]
    by_name = {p["name"]: p["id"] for p in _profiles(client)}

    assert client.delete(f"/api/profiles/{by_name['ir2']}").json()["removed"] == "ir2"
    assert client.get(f"/api/servers/{other['id']}").status_code == 200 and client.delete(f"/api/profiles/{by_name['ir2']}").status_code == 404

    assert client.delete(f"/api/servers/{s['id']}").status_code == 200                                    # a server that is deleted takes its profile with it
    assert [p["kind"] for p in _profiles(client)] == ["node"] and _profiles(client)[0]["id"] == node

    assert client.delete(f"/api/panels/{pid}").status_code == 200                                         # so does a panel that is removed, for the profiles of its nodes
    assert _profiles(client) == []

    with session() as db:
        assert db.scalars(select(Profile)).all() == []


def test_a_node_that_left_the_panel_is_still_listed_as_missing(client):
    pid = _panel()
    client.post("/api/profiles", json={"kind": "node", "panel_id": pid, "node_ids": [7]})

    with session() as db:
        db.get(Panel, pid).nodes = [{"id": 8, "name": "n8", "address": "203.0.113.8", "status": "error"}]

    p = _profiles(client)[0]
    assert p["missing"] is True and p["name"] == "node 7" and p["panel"] == "pg1"


def test_the_strips_come_from_the_samples_for_the_servers_of_the_profiles_only(client):
    a, b = _server(client, "ir1", "1.1.1.1"), _bare("ir2", "3.3.3.3")
    now = int(time.time())

    with session() as db:
        for sid in (a["id"], b):
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


# ------------------------------------------------------------------ tags (the owner, 2026-10-14: a name and a tag - Any unless he chooses one - and three categories from the start)
def test_a_tag_is_free_text_empty_or_any_is_no_tag_and_the_three_categories_are_always_there(client):
    assert [svc.check_tag(v) for v in ("", None, " Any ", "ANY", "Iran", "  my  tag ")] == ["", "", "", "", "Iran", "my tag"]

    for bad in ("x" * 41, "a/b", "<b>"):
        with pytest.raises(ValueError):
            svc.check_tag(bad)

    assert client.get("/api/profiles").json()["tags"] == []                                                         # before anything is added: no category from the start

    a = _server(client, "ir1", "3.3.3.3", "iran", tag="Edge")
    assert client.get("/api/profiles").json()["tags"] == ["Edge"]                                                   # a tag he types is made by using it
    assert [p["tag"] for p in _profiles(client)] == ["Edge"]

    pid = _profiles(client)[0]["id"]
    assert client.put(f"/api/profiles/{pid}/tag", json={"tag": "iran"}).json()["tag"] == "iran"
    assert client.put(f"/api/profiles/{pid}/tag", json={"tag": "any"}).json()["tag"] == ""                   # Any = no tag
    assert client.put(f"/api/profiles/{pid}/tag", json={"tag": "a/b"}).status_code == 400
    assert client.put("/api/profiles/9999/tag", json={"tag": "x"}).status_code == 404
    assert client.get("/api/profiles").json()["tags"] == []                                                         # (Edge is not used any more: Any is no tag)
    assert a["id"]


def test_a_new_server_and_several_at_once_and_the_picked_ones_are_listed_under_the_tag_that_was_given(client, monkeypatch):
    assert client.post("/api/servers", json={"name": "ir1", "host": "3.3.3.3", "role": "iran", "tag": "x/y"}).status_code == 400          # a bad tag is refused before the server is made
    assert client.get("/api/servers").json() == []

    monkeypatch.setattr(nodes, "run_script", lambda *a, **k: (_ for _ in ()).throw(nodes.SSHError("no key in a test")))
    r = client.post("/api/servers/add-many", json={"role": "foreign", "lines": "fo5 4.4.4.4", "tag": "Hetzner"})
    assert r.status_code == 200 and [i["ok"] for i in r.json()["items"]] == [False]
    r = client.post("/api/servers/add-many", json={"role": "foreign", "lines": "fo6 5.5.5.5", "tag": "a/b"})
    assert r.status_code == 400

    b = _bare("fo7", "8.8.8.8", "foreign")
    r = client.post("/api/profiles", json={"kind": "foreign", "server_ids": [b], "tag": "Backup"})
    assert r.status_code == 201 and r.json()["added"][0]["tag"] == "Backup"
    assert sorted((p["name"], p["tag"]) for p in _profiles(client)) == [("fo5", "Hetzner"), ("fo7", "Backup")]


def test_the_nodes_of_a_pasarguard_panel_are_put_on_the_page_by_themselves_tagged_pasarguard(client, monkeypatch):
    nodes_ = [{"id": 7, "name": "n7", "address": "203.0.113.7", "status": "connected"}, {"id": 8, "name": "n8", "address": "203.0.113.8", "status": "error"}]
    monkeypatch.setattr(panels, "_credentials", lambda base, token, username, password, keep: ({**base, "url": "https://p.example:8000", "username": ""}, {"token": "pg_key_x"}))
    monkeypatch.setattr(panels, "check", lambda c: {"ok": True, "latency_ms": 5, "stats": {"version": "1"}, "nodes": nodes_, "error": ""})

    panels.add("pg1", "pasarguard", "https://p.example:8000", token="pg_key_x")
    assert sorted((p["kind"], p["name"], p["tag"]) for p in _profiles(client)) == [("node", "n7", "PasarGuard"), ("node", "n8", "PasarGuard")]

    panels.add("mz1", "marzban", "https://m.example:8000", token="t")                                        # a Marzban panel has no PasarGuard nodes
    assert len(_profiles(client)) == 2

    with session() as db:
        db.execute(Profile.__table__.delete())
        assert svc.auto_all(db) == 2 and svc.auto_all(db) == 0                                               # the upgrade step: once, and a second run makes nothing
        assert {p.tag for p in db.scalars(select(Profile))} == {"PasarGuard"}

def test_a_category_made_by_hand_stays_on_the_page_while_no_profile_carries_it(client):
    r = client.post("/api/profiles/tags", json={"tag": "  Gaming  "})
    assert r.status_code == 201 and r.json()["tag"] == "Gaming"
    assert client.post("/api/profiles/tags", json={"tag": "gaming"}).status_code == 201                         # the same name again changes nothing
    assert client.post("/api/profiles/tags", json={"tag": "iran"}).status_code == 201
    assert client.get("/api/profiles").json()["tags"] == ["Gaming", "iran"]                                         # in the order they were made; no profile carries them
    assert client.post("/api/profiles/tags", json={"tag": "any"}).status_code == 400                         # Any is every profile, not a category
    assert client.post("/api/profiles/tags", json={"tag": ""}).status_code == 400
    assert client.post("/api/profiles/tags", json={"tag": "a/b"}).status_code == 400

def test_a_category_can_be_renamed_and_deleted_and_its_servers_follow(client):
    s = _server(client, "ir1", "3.3.3.3", "iran", tag="Iran")
    _server(client, "fo1", "4.4.4.4", "foreign", tag="Marzban")
    assert client.put("/api/profiles/tags/Marzban", json={"tag": "Backup"}).json()["tag"] == "Backup"
    assert client.get("/api/profiles").json()["tags"] == ["Backup", "Iran"]                                          # (the tags the servers carry, in name order)
    assert sorted((p["name"], p["tag"]) for p in _profiles(client)) == [("fo1", "Backup"), ("ir1", "Iran")]       # the server follows
    assert client.put("/api/profiles/tags/Backup", json={"tag": "iran"}).status_code == 409                       # a name that exists
    assert client.put("/api/profiles/tags/Nothing", json={"tag": "x"}).status_code == 404
    assert client.put("/api/profiles/tags/Backup", json={"tag": "any"}).status_code == 400

    assert client.delete("/api/profiles/tags/Backup").json()["moved"] == 1
    assert client.get("/api/profiles").json()["tags"] == ["Iran"]                                                  # Backup is gone, Iran is still used
    assert sorted((p["name"], p["tag"]) for p in _profiles(client)) == [("fo1", ""), ("ir1", "Iran")]             # the server is under Any, still on the page
    assert client.delete("/api/profiles/tags/any").status_code == 400
    assert client.get(f"/api/servers/{s['id']}").status_code == 200


def test_a_node_added_in_pasarguard_later_is_found_by_the_check_and_put_on_the_page_and_one_taken_off_on_purpose_stays_off(client, monkeypatch):
    """The owner, 2026-10-15: "the panel was supposed to detect the node that was added in PasarGuard by itself" - the nodes were put on the page only when the panel was connected."""
    pid = _panel(nodes_=[{"id": 7, "name": "n7", "address": "203.0.113.7", "status": "connected"}])
    seen = [{"id": 7, "name": "n7", "address": "203.0.113.7", "status": "connected"}]
    monkeypatch.setattr(panels, "_cred", lambda p: {"id": p.id, "url": "https://p.example:8000", "type": p.type, "token": ""})
    monkeypatch.setattr(panels, "check", lambda c: {"ok": True, "latency_ms": 5, "stats": {"version": "1"}, "nodes": [dict(n) for n in seen], "error": ""})

    panels.check_all()
    assert [p["name"] for p in _profiles(client)] == ["n7"]                                                     # (the node that was there is on the page by the first check)

    seen.append({"id": 9, "name": "AS", "address": "203.0.113.9", "status": "connected"})                        # the owner adds a node in PasarGuard
    panels.check_all()
    assert sorted((p["name"], p["tag"]) for p in _profiles(client)) == [("AS", "PasarGuard"), ("n7", "PasarGuard")]
    panels.check_all()
    assert len(_profiles(client)) == 2                                                                           # (and not twice)

    gone = next(p["id"] for p in _profiles(client) if p["name"] == "AS")
    assert client.delete(f"/api/profiles/{gone}").status_code == 200                                              # taken off on purpose
    panels.check_all()
    assert [p["name"] for p in _profiles(client)] == ["n7"]                                                      # the check does not put it back

    r = client.post("/api/profiles", json={"kind": "node", "panel_id": pid, "node_ids": [9], "tag": "Mine"})
    assert r.status_code == 201 and r.json()["added"][0]["tag"] == "Mine"                                         # added by hand again: it follows the page again
    client.delete(f"/api/profiles/{r.json()['added'][0]['id']}")
    with session() as db:
        assert svc._skip(db) == {f"{pid}:9"}
        svc.drop_panel(db, pid)
        assert svc._skip(db) == set()                                                                              # a panel that is removed takes its notes with it

def test_a_node_deleted_in_pasarguard_goes_off_the_page_at_the_next_good_check(client, monkeypatch):
    """The owner, 2026-10-15: "I deleted the node in PasarGuard, it was removed there but not here"."""
    seen = [{"id": 7, "name": "n7", "address": "203.0.113.7", "status": "connected"}, {"id": 9, "name": "AS", "address": "203.0.113.9", "status": "connected"}]
    _panel(nodes_=[dict(n) for n in seen])
    answer = {"ok": True, "latency_ms": 5, "stats": {"version": "1"}, "error": ""}
    monkeypatch.setattr(panels, "_cred", lambda p: {"id": p.id, "url": "https://p.example:8000", "type": p.type, "token": ""})
    monkeypatch.setattr(panels, "check", lambda c: {**answer, "nodes": [dict(n) for n in seen]})
    panels.check_all()
    assert sorted(p["name"] for p in _profiles(client)) == ["AS", "n7"]

    answer["nodes_error"] = "HTTP 500"                                                                           # the node list could not be read: that says nothing about the nodes
    saved = list(seen)
    seen.clear()
    panels.check_all()
    assert len(_profiles(client)) == 2                                                                           # (nothing is removed on a bad read)
    answer.pop("nodes_error")
    seen.extend(saved)
    panels.check_all()
    assert len(_profiles(client)) == 2

    seen[:] = [n for n in saved if n["id"] != 9]                                                                 # the node is deleted in PasarGuard
    panels.check_all()
    assert [p["name"] for p in _profiles(client)] == ["n7"]
    panels.check_all()
    assert [p["name"] for p in _profiles(client)] == ["n7"]                                                      # and it does not come back

def test_the_profile_of_a_connected_pasarguard_panel_cannot_be_renamed_or_deleted_while_the_panel_is_there(client):
    """The owner, 2026-10-15: "while a PasarGuard panel is connected, its profile cannot be deleted or renamed"."""
    pid = _panel()
    r = client.post("/api/profiles", json={"kind": "node", "panel_id": pid, "node_ids": [7], "tag": "PasarGuard"})
    assert r.status_code == 201

    for name in ("PasarGuard", "pasarguard"):
        assert client.put(f"/api/profiles/tags/{name}", json={"tag": "Mine"}).status_code == 400
        assert client.delete(f"/api/profiles/tags/{name}").status_code == 400

    assert [p["tag"] for p in _profiles(client)] == ["PasarGuard"]
    other = client.post("/api/profiles/tags", json={"tag": "Hetzner"})
    assert other.status_code == 201 and client.put("/api/profiles/tags/Hetzner", json={"tag": "Backup"}).status_code == 200          # any other profile is free

    with session() as db:
        db.execute(Panel.__table__.delete())                                                                           # the panel is gone: the profile is a plain one again

    assert client.put("/api/profiles/tags/PasarGuard", json={"tag": "Mine"}).status_code == 200

def test_the_sync_button_of_a_pasarguard_profile_reads_the_panels_now_and_brings_every_node_in_even_one_taken_off(client, monkeypatch):
    """The owner, 2026-10-15: "in the PasarGuard profile an option to sync: a panel is there but its nodes are not shown"."""
    pid = _panel(nodes_=[])
    seen = [{"id": 7, "name": "n7", "address": "203.0.113.7", "status": "connected"}, {"id": 9, "name": "AS", "address": "203.0.113.9", "status": "connected"}]
    monkeypatch.setattr(panels, "_cred", lambda p: {"id": p.id, "url": "https://p.example:8000", "type": p.type, "token": ""})
    monkeypatch.setattr(panels, "check", lambda c: {"ok": True, "latency_ms": 5, "stats": {"version": "1"}, "nodes": [dict(n) for n in seen], "error": ""})
    r = client.post("/api/profiles/sync", json={"tag": "PasarGuard"})
    assert r.status_code == 200 and r.json()["added"] == 2 and r.json()["panels"] == 1 and r.json()["errors"] == []
    assert sorted((p["name"], p["tag"]) for p in _profiles(client)) == [("AS", "PasarGuard"), ("n7", "PasarGuard")]
    assert client.post("/api/profiles/sync", json={"tag": "PasarGuard"}).json()["added"] == 0                      # (nothing twice)

    gone = next(p["id"] for p in _profiles(client) if p["name"] == "AS")
    client.delete(f"/api/profiles/{gone}")                                                                         # taken off on purpose: the check leaves it, the Sync button brings it back
    panels.check_all()
    assert [p["name"] for p in _profiles(client)] == ["n7"]
    assert client.post("/api/profiles/sync", json={"tag": "Mine"}).json()["added"] == 1 and sorted(p["tag"] for p in _profiles(client)) == ["Mine", "PasarGuard"]

    monkeypatch.setattr(panels, "check", lambda c: {"ok": False, "latency_ms": None, "stats": {}, "nodes": [], "error": "no answer"})
    r = client.post("/api/profiles/sync", json={"tag": ""})
    assert r.json()["added"] == 0 and "no answer" in r.json()["errors"][0] and pid
    assert client.post("/api/profiles/sync", json={"tag": "a/b"}).status_code == 400

def test_the_two_pasarguard_profiles_become_one_called_pasarguard_at_the_upgrade():
    """The owner, 2026-10-15: "I have two PasarGuard profiles: delete the empty one, call this one PasarGuard"."""
    from nikatel.models import Setting

    with session() as db:
        db.add(Setting(key="lab", value={"tags": ["Marzban", "PasarGuard Nodes", "Iran", "PasarGuard"], "skip_nodes": []}))
        db.add(Profile(kind="node", panel_id=1, node_id=1, tag="PasarGuard Nodes"))
        db.add(Profile(kind="node", panel_id=1, node_id=2, tag="PasarGuard Nodes"))
        db.flush()
        assert svc.rename_nodes_tag(db) == 2
        db.flush()
        assert {p.tag for p in db.scalars(select(Profile))} == {"PasarGuard"}
        assert db.get(Setting, "lab").value["tags"] == ["Marzban", "PasarGuard", "Iran"] and db.get(Setting, "lab").value["skip_nodes"] == []          # (one name, listed once, in the place of the first)
        assert svc.rename_nodes_tag(db) == 0
