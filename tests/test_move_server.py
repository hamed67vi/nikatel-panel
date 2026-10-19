"""The "Move the tunnels…" item in the menu of an Iran server (1.69.24): the plan with an automatic target (line quality, resources, tunnels on it, free ports - the panel's own and what listens), the refusals
(no target, no license, no DNS account, read-only, the world changed since the confirm) and the real move with the DNS records of every account changing."""
from pathlib import Path

import pytest
from sqlalchemy import select
from test_doctor import _finished
from test_failover import FakeProv, _dns_world, _servers_and_tunnels

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot, builder, failover
from nikatel.services import settings as st

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    monkeypatch.setattr(failover, "ENABLED", True)
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: [])         # never a real SSH call; a test that wants a listening program replaces it


def _row(sid, name, quality=90, resources=80, tunnels=0, score=80):
    """A row of autopilot.ranking (detect.auto_iran)."""
    return {"id": sid, "name": name, "score": score, "quality": quality, "resources": resources, "tunnels": tunnels, "reason": f"12 ms, 0% loss, {tunnels} tunnels"}


def _more_servers():
    with session() as db:
        a, b = Server(name="ir3", host="203.0.113.13", role="iran", online=True), Server(name="ir4", host="203.0.113.14", role="iran", online=True)
        db.add_all([a, b])
        db.flush()
        return {"ir3": a.id, "ir4": b.id}


def _ranking(monkeypatch, rows):
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [dict(r) for r in rows])


def test_room_and_the_order_of_the_targets_are_pure_and_the_same_every_time():
    assert failover.room_for(_row(1, "a"), 3) == ""
    assert "line" in failover.room_for(_row(1, "a", quality=30), 3)
    assert "CPU" in failover.room_for(_row(1, "a", resources=20), 3)
    assert "pass 8" in failover.room_for(_row(1, "a", tunnels=6), 3) and failover.room_for(_row(1, "a", tunnels=5), 3) == ""      # 5 + 3 = 8 is still allowed
    a, b, c = _row(1, "a"), _row(2, "b"), _row(3, "c")
    assert [x["id"] for x in failover.rank_targets({10: [a, b, c], 11: [b, c, a]})] == [2, 1, 3]       # totals: b 1, a 2, c 3
    assert [x["id"] for x in failover.rank_targets({10: [a, b], 11: [b, a]})] == [1, 2]                  # a tie goes to the server the first tunnel ranks higher, never to chance
    assert failover.rank_targets({10: [a], 11: [b]}) == [] and failover.rank_targets({}) == []


def test_the_plan_picks_the_best_server_with_room_and_free_ports_and_says_why_the_others_were_left_out(monkeypatch):
    ids = _servers_and_tunnels()
    more = _more_servers()
    _dns_world(monkeypatch)
    monkeypatch.setattr(get_settings(), "read_only", False)
    # ir2: the line to the foreign server is poor; ir3: good; ir4: the best on paper, but a program listens on port 443 there
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2", quality=30), _row(more["ir3"], "ir3", quality=85, resources=70, tunnels=1), _row(more["ir4"], "ir4", quality=95, resources=90)])
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: [443] if sid == more["ir4"] else [])
    p = failover.move_plan(ids["ir1"])
    assert p["blocked"] == "" and p["reason"] == "manual" and p["whole"] is True and sorted(t["name"] for t in p["tunnels"]) == ["t1", "t2", "t3"]
    assert p["target"] == {"id": more["ir3"], "name": "ir3", "ip": "203.0.113.13"}
    assert [c["name"] for c in p["candidates"]] == ["ir3"] and p["candidates"][0]["ip"] == "203.0.113.13" and p["candidates"][0]["tunnels"] == 1
    why = {x["name"]: x["why"] for x in p["skipped"]}
    assert "line" in why["ir2"] and "443" in why["ir4"] and "listening" in why["ir4"]
    assert [(d["fqdn"], d["account"]) for d in p["dns"]] == [("a.example.test", "arvan"), ("b.example.test", "arvan"), ("c.example.test", "cf1")]      # every record, each in its own account
    assert p["problems"] == []
    assert FakeProv.calls == []                                                                           # a plan changes nothing


def test_the_target_must_have_room_for_all_the_moved_tunnels_together(monkeypatch):
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2", tunnels=6)])                                          # 3 tunnels move: 6 + 3 would be 9
    p = failover.move_plan(ids["ir1"])
    assert p["target"] is None and p["candidates"] == [] and "no other Iran server can carry all of these tunnels" in " ".join(p["problems"]) and "carries 6 tunnels" in p["skipped"][0]["why"]


def test_a_port_the_panel_already_forwards_on_the_target_counts_as_busy_without_asking_the_server(monkeypatch):
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)

    with session() as db:
        fi1 = db.get(Server, ids["fi1"])
        db.add(Tunnel(name="other", type="backhaul", transport="tcpmux", iran_server_id=ids["ir2"], foreign_server_id=fi1.id, state="up", forwarded_ports=[443]))

    asked = []
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: asked.append(sid) or [])
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2", tunnels=1)])
    p = failover.move_plan(ids["ir1"])
    assert p["target"] is None and "443 (tunnel other)" in p["skipped"][0]["why"] and asked == []


def test_a_server_whose_listening_ports_cannot_be_read_is_not_a_target(monkeypatch):
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)

    def boom(sid, ports):
        raise RuntimeError("ssh: connection timed out")

    monkeypatch.setattr(failover, "_live_busy", boom)
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2")])
    p = failover.move_plan(ids["ir1"])
    assert p["target"] is None and "could not be read" in p["skipped"][0]["why"]                         # a move never starts on a guess


def test_a_tunnel_that_cannot_be_ranked_leaves_no_target(monkeypatch):
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)

    def ranking(tid):
        if tid == ids["t2"]:
            raise RuntimeError("the foreign server does not answer")

        return [_row(ids["ir2"], "ir2")]

    monkeypatch.setattr(autopilot, "ranking", ranking)
    p = failover.move_plan(ids["ir1"])
    assert p["target"] is None and any("t2" in x for x in p["problems"])


def test_an_explicit_target_is_checked_for_free_ports_too_and_never_the_server_itself(monkeypatch):
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2")])
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: [80])
    p = failover.move_plan(ids["ir1"], ids["ir2"])
    assert p["target"] is None and "already has a program listening" in " ".join(p["problems"])
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: [])
    assert failover.move_plan(ids["ir1"], ids["ir2"])["target"]["id"] == ids["ir2"]

    with pytest.raises(ValueError, match="another Iran server"):
        failover.move_plan(ids["ir1"], ids["ir1"])

    _ranking(monkeypatch, [_row(ids["ir2"], "ir2", resources=10)])                                        # the server the operator confirmed got busy since the plan was shown
    p = failover.move_plan(ids["ir1"], ids["ir2"])
    assert p["target"] is None and "CPU" in " ".join(p["problems"])


def test_it_is_refused_without_the_license_without_a_dns_account_and_while_the_panel_is_read_only(client, monkeypatch):
    ids = _servers_and_tunnels()
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2")])
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    body = {"target_id": ids["ir2"], "tunnels": [ids["t1"], ids["t2"], ids["t3"]]}
    r = client.get(f"/api/failover/move-plan/{ids['ir1']}")
    assert r.status_code == 200 and "read-only" in r.json()["blocked"]                                    # the tests run read-only: the plan is shown, nothing can run
    assert client.post(f"/api/failover/move-server/{ids['ir1']}", json=body).status_code == 403
    monkeypatch.setattr(get_settings(), "read_only", False)
    r = client.get(f"/api/failover/move-plan/{ids['ir1']}")
    assert "DNS account" in r.json()["blocked"]                                                           # no account saved yet
    r = client.post(f"/api/failover/move-server/{ids['ir1']}", json=body)
    assert r.status_code == 403 and "DNS account" in r.text
    _dns_world(monkeypatch)                                                                               # an account is saved now
    assert client.get(f"/api/failover/move-plan/{ids['ir1']}").json()["blocked"] == ""
    monkeypatch.setattr(get_settings(), "license", "")                                                    # the free tier: no failover feature
    r = client.get(f"/api/failover/move-plan/{ids['ir1']}")
    assert "license" in r.json()["blocked"]
    assert client.post(f"/api/failover/move-server/{ids['ir1']}", json=body).status_code == 403
    monkeypatch.setattr(get_settings(), "license", "owner")
    monkeypatch.setattr(failover, "ENABLED", False)
    assert "switched off" in client.get(f"/api/failover/move-plan/{ids['ir1']}").json()["blocked"]
    assert client.post(f"/api/failover/move-server/{ids['ir1']}", json=body).status_code == 403


def test_a_move_with_no_target_or_no_tunnel_or_a_changed_world_is_refused_with_the_reason(client, monkeypatch):
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    monkeypatch.setattr(get_settings(), "read_only", False)
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: started.append(label) or 1)
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2", quality=10)])                                          # nobody is good enough
    r = client.post(f"/api/failover/move-server/{ids['ir1']}", json={"target_id": ids["ir2"]})
    assert r.status_code == 400 and "no Iran server can take these tunnels" in r.text and started == []
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2")])
    r = client.post(f"/api/failover/move-server/{ids['ir1']}", json={"target_id": ids["ir2"], "tunnels": [ids["t1"]]})      # the plan the operator saw had one tunnel; now it has three
    assert r.status_code == 400 and "changed since you looked" in r.text and started == []
    r = client.post(f"/api/failover/move-server/{ids['ir2']}", json={"target_id": ids["ir1"]})                              # ir2 carries nothing
    assert r.status_code == 400 and "no tunnel" in r.text and started == []
    monkeypatch.setattr(failover, "_fetch_records", lambda ip: ([], "Cloudflare: HTTP 403"))
    r = client.post(f"/api/failover/move-server/{ids['ir1']}", json={"target_id": ids["ir2"]})
    assert r.status_code == 400 and "did not answer" in r.text and started == []                           # a DNS account that cannot be read: its records could not follow


def test_the_confirmed_move_builds_the_tunnels_changes_the_dns_of_every_account_and_keeps_it_for_undo(client, monkeypatch):
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    monkeypatch.setattr(get_settings(), "read_only", False)
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2")])
    events, said = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: fn(1, *a) or 1)                    # the story runs here, not in a thread

    def build(snap, iran_id, foreign_id, kind, opts=None):
        events.append(("build", snap["name"], iran_id))

        with session() as db:
            db.add(Tunnel(name=snap["name"], type="backhaul", transport="tcpmux", iran_server_id=iran_id, foreign_server_id=foreign_id, state="up", forwarded_ports=[1000 + len(events)]))

        return 0

    def delete(tid, keep_record=False, trash=False, skip_offline=False):
        with session() as db:
            t = db.get(Tunnel, tid)
            events.append(("delete", t.name, trash))
            db.delete(t)

        return ["Iran: removed", "foreign: removed"]

    monkeypatch.setattr(autopilot, "_build_kind", build)
    monkeypatch.setattr(autopilot, "_wait", _finished(True))
    monkeypatch.setattr(builder, "delete_tunnel", delete)
    monkeypatch.setattr(failover, "_sleep", lambda secs: events.append(("sleep", secs)))
    plan = client.get(f"/api/failover/move-plan/{ids['ir1']}").json()
    assert plan["blocked"] == "" and plan["target"]["id"] == ids["ir2"]
    r = client.post(f"/api/failover/move-server/{ids['ir1']}", json={"target_id": plan["target"]["id"], "tunnels": plan["move"]})
    assert r.status_code == 202 and r.json()["action_id"] == 1
    assert [e for e in events if e[0] == "build"] == [("build", "t1-mv", ids["ir2"]), ("build", "t2-mv", ids["ir2"]), ("build", "t3-mv", ids["ir2"])]       # new first ...
    assert ("sleep", 600) in events and events.index(("sleep", 600)) < events.index(next(e for e in events if e[0] == "delete"))                           # ... then the DNS, the records' TTL (300 for the proxied one) + 5 minutes, then the old ones
    assert sorted(e[1] for e in events if e[0] == "delete") == ["t1", "t2", "t3"] and all(e[2] is True for e in events if e[0] == "delete")        # (to the trash)
    assert FakeProv.calls == [("update", "arvan", "example.test", "r-a", ["203.0.113.9"], 120, True), ("update", "arvan", "example.test", "r-b", ["203.0.113.9"], 120, True),
                              ("update", "cf1", "example.test", "r-c", ["203.0.113.9"], 1, True)]                                                      # the three records, two accounts, all at the new address

    with session() as db:
        assert sorted((t.name, t.iran_server_id) for t in db.scalars(select(Tunnel))) ==[("t1", ids["ir2"]), ("t2", ids["ir2"]), ("t3", ids["ir2"])]          # the new ones carry the old names

    log = st.get("failover")["dns_log"]
    assert sorted(e["fqdn"] for e in log) == ["a.example.test", "b.example.test", "c.example.test"] and all(e["old"] == "203.0.113.5" and e["new"] == "203.0.113.9" and not e["restored"] for e in log)
    h = st.get("failover")["history"][-1]
    assert h["reason"] == "manual" and h["ok"] is True and h["moved"] == 3 and h["server"] == "ir1" and h["target"]["name"] == "ir2"
    assert client.post(f"/api/failover/{h['id']}/undo").status_code == 200 and FakeProv.calls[-1][0] == "back"                                         # every record can be put back


def test_a_tunnel_move_takes_this_tunnel_and_the_records_that_serve_only_it(client, monkeypatch):
    """The owner, 2026-10-19: "remove it from the server, add it to the tunnel": the item of a tunnel's menu moves THAT tunnel (and the ones that share a DNS record with it)."""
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    monkeypatch.setattr(get_settings(), "read_only", False)
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2")])
    p = client.get(f"/api/failover/tunnel-move-plan/{ids['t2']}").json()
    assert p["blocked"] == "" and [t["name"] for t in p["tunnels"]] == ["t2"] and p["whole"] is False and p["pulled"] == [] and p["target"]["id"] == ids["ir2"]
    assert [d["fqdn"] for d in p["dns"]] == ["b.example.test"]                                           # a and c serve other tunnels (or none): left alone
    p = client.get(f"/api/failover/tunnel-move-plan/{ids['t1']}").json()
    assert sorted(t["name"] for t in p["tunnels"]) == ["t1", "t3"] and p["pulled"] == ["t3"] and [d["fqdn"] for d in p["dns"]] == ["a.example.test"]      # t3 shares the record a with t1: it goes too
    assert client.get("/api/failover/tunnel-move-plan/9999").status_code == 400
    assert client.post("/api/failover/move-tunnel/9999", json={"target_id": ids["ir2"]}).status_code == 400
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: started.append(label) or 1)
    r = client.post(f"/api/failover/move-tunnel/{ids['t2']}", json={"target_id": ids["ir2"], "tunnels": [ids["t2"], ids["t3"]]})                              # the plan the operator saw had two tunnels, now it has one
    assert r.status_code == 400 and "changed since you looked" in r.text and started == []


def test_the_confirmed_tunnel_move_builds_only_that_tunnel_and_changes_only_its_record(client, monkeypatch):
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    monkeypatch.setattr(get_settings(), "read_only", False)
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2")])
    events = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: None)
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: fn(1, *a) or 1)

    def build(snap, iran_id, foreign_id, kind, opts=None):
        events.append(("build", snap["name"], iran_id))

        with session() as db:
            db.add(Tunnel(name=snap["name"], type="backhaul", transport="tcpmux", iran_server_id=iran_id, foreign_server_id=foreign_id, state="up", forwarded_ports=[8075]))

        return 0

    def delete(tid, keep_record=False, trash=False, skip_offline=False):
        with session() as db:
            t = db.get(Tunnel, tid)
            events.append(("delete", t.name, trash))
            db.delete(t)

        return ["Iran: removed", "foreign: removed"]

    monkeypatch.setattr(autopilot, "_build_kind", build)
    monkeypatch.setattr(autopilot, "_wait", _finished(True))
    monkeypatch.setattr(builder, "delete_tunnel", delete)
    monkeypatch.setattr(failover, "_sleep", lambda secs: events.append(("sleep", secs)))
    r = client.post(f"/api/failover/move-tunnel/{ids['t2']}", json={"target_id": ids["ir2"], "tunnels": [ids["t2"]]})
    assert r.status_code == 202
    assert events == [("build", "t2-mv", ids["ir2"]), ("sleep", 420), ("delete", "t2", True)]
    assert FakeProv.calls == [("update", "arvan", "example.test", "r-b", ["203.0.113.9"], 120, True)]       # one record, the one that serves only t2

    with session() as db:
        assert sorted((t.name, t.iran_server_id) for t in db.scalars(select(Tunnel))) == [("t1", ids["ir1"]), ("t2", ids["ir2"]), ("t3", ids["ir1"])]     # t1 and t3 stay where they are


def _tunnel_move_fakes(monkeypatch, events, said, quiet):
    """The builder, the waits and the DNS providers faked for a move of t2 from ir1 to ir2; `quiet`: what the wait for the old tunnel to be quiet answers (a list of answers, one per call)."""
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: fn(1, *a) or 1)

    def build(snap, iran_id, foreign_id, kind, opts=None):
        events.append(("build", snap["name"], iran_id))

        with session() as db:
            db.add(Tunnel(name=snap["name"], type="backhaul", transport="tcpmux", iran_server_id=iran_id, foreign_server_id=foreign_id, state="up", forwarded_ports=[8075]))

        return 0

    def delete(tid, keep_record=False, trash=False, skip_offline=False):
        with session() as db:
            t = db.get(Tunnel, tid)
            events.append(("delete", t.name, trash))
            db.delete(t)

        return ["Iran: removed", "foreign: removed"]

    monkeypatch.setattr(autopilot, "_build_kind", build)
    monkeypatch.setattr(autopilot, "_wait", _finished(True))
    monkeypatch.setattr(autopilot, "_wait_quiet", lambda aid, tid, limit=1800: events.append(("quiet", tid, limit)) or quiet.pop(0))
    monkeypatch.setattr(builder, "delete_tunnel", delete)
    monkeypatch.setattr(failover, "_sleep", lambda secs: events.append(("sleep", secs)))


def test_the_old_tunnel_is_removed_only_when_nobody_uses_it_any_more(client, monkeypatch):
    """The owner, 2026-10-19: "the old tunnel must not be cut until the new one is in place - the user must not feel a cut"."""
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    monkeypatch.setattr(get_settings(), "read_only", False)
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2")])
    events, said = [], []
    _tunnel_move_fakes(monkeypatch, events, said, [True])
    r = client.post(f"/api/failover/move-tunnel/{ids['t2']}", json={"target_id": ids["ir2"], "tunnels": [ids["t2"]]})
    assert r.status_code == 202
    assert events == [("build", "t2-mv", ids["ir2"]), ("sleep", 420), ("quiet", ids["t2"], failover.QUIET_LIMIT), ("delete", "t2", True)]      # build, the DNS, the TTL wait, the wait for quiet, and only then the old one goes
    assert st.get("failover")["history"][-1]["ok"] is True


def test_an_old_tunnel_that_is_still_in_use_is_kept_beside_the_new_one_and_the_owner_is_told(client, monkeypatch):
    ids = _servers_and_tunnels()
    _dns_world(monkeypatch)
    monkeypatch.setattr(get_settings(), "read_only", False)
    _ranking(monkeypatch, [_row(ids["ir2"], "ir2")])
    events, said = [], []
    _tunnel_move_fakes(monkeypatch, events, said, [False])                                                 # clients that hold the old address keep it busy
    r = client.post(f"/api/failover/move-tunnel/{ids['t2']}", json={"target_id": ids["ir2"], "tunnels": [ids["t2"]]})
    assert r.status_code == 202
    assert ("quiet", ids["t2"], failover.QUIET_LIMIT) in events and not any(e[0] == "delete" for e in events)            # nothing is cut
    assert FakeProv.calls == [("update", "arvan", "example.test", "r-b", ["203.0.113.9"], 120, True)]                      # the DNS did follow
    assert any("NOT removed" in x and "t2-mv" in x for x in said)

    with session() as db:
        assert sorted((t.name, t.iran_server_id) for t in db.scalars(select(Tunnel))) == [("t1", ids["ir1"]), ("t2", ids["ir1"]), ("t2-mv", ids["ir2"]), ("t3", ids["ir1"])]       # both run

    h = st.get("failover")["history"][-1]
    assert h["ok"] is False and "still in use" in h["errors"][0]


def test_the_menu_item_the_plan_box_and_the_words_are_in_the_page():
    lab, app, fa, css = (WEB / "lab.js").read_text(encoding="utf-8"), (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "i18n.fa.ui2.js").read_text(encoding="utf-8"), (WEB / "lab.css").read_text(encoding="utf-8")
    assert lab.count('labIt("Move to a better server…", "gre", () => tunMoveAway(t.id, t.name)') == 1 and "srvMoveAway" not in lab and "Move the tunnels…" not in lab      # in the menu of a TUNNEL, no longer in the menu of a server
    assert "window.tunMoveAway = async (id, name) =>" in lab and "/api/failover/tunnel-move-plan/${id}" in lab and "/api/failover/move-tunnel/${id}" in lab and "tunnels: p.move" in lab
    assert "function glassChoice(text, choices, o)" in app and "o && o.html" in app and ".ask-text.mv-plan" in css
    assert '"Move to a better server":' in fa and '"Move {} to another Iran server?":' in fa and "Nothing is cut: the new tunnels are built and tested first" in fa
