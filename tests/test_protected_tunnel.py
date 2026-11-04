"""A PROTECTED tunnel (1.69.221). The owner, 2026-11-04, after the Doctor moved a tunnel to another Iran server and took the old one off - with the standby lane that a service of his stood on - while he was testing it:
"Yes, protect first". A tunnel something else stands on gets a mark in its record; the Doctor still repairs and restarts it, but

  never removes it   `builder.delete_tunnel` refuses (the janitor, the sweeps, a move, a rebuild and the page all go through it); the page answers 409; a server cannot be taken off with it
  never replaces it  `autopilot._replace`, `_switch_cut`, `_rebuild_safe` and `swap.beside` stop before anything is written, built or removed
  never moves it     `failover._make_plan` leaves it where it is (and the DNS record it shares is left alone), `doctor.move_now`, `quality_move` and `after_ladder` refuse
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel, is_protected
from nikatel.services import autopilot as ap
from nikatel.services import builder, doctor, failover, standby, swap
from nikatel.services import settings as st


@pytest.fixture(autouse=True)
def _writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)          # (the tests run read-only)
    monkeypatch.setattr(builder, "run_action", lambda *a, **k: "")


def world(tag="p", n_tunnels=1, protected=(), standby_of=""):
    """One Iran server, one foreign server and `n_tunnels` tunnels between them; the indexes in `protected` carry the mark."""
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + tag, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + tag, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        ids = []

        for i in range(n_tunnels):
            rec = {"link": "gre", "mtu": 1360, "keepalive": 75, "opts": {}, "network": f"10.174.{71 + i}.40/30"}

            if i in protected:
                rec["protected"] = True

            if standby_of:
                rec["standby_of"] = standby_of

            t = Tunnel(name=f"{tag}-t{i}", type="backhaul", transport="tcpmux", port=9902 + i, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8072 + i],
                       iran_tunnel_ip=f"10.174.{71 + i}.41", foreign_tunnel_ip=f"10.174.{71 + i}.42", record=rec, secrets={"token": "t0k", "gre_key": 77 + i}, created_at=1_790_000_000)
            db.add(t)
            db.flush()
            ids.append(t.id)

        return ids, ir.id, fr.id


def another_iran(tag="q"):
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        s = Server(name="ir-" + tag, host=f"203.0.113.{n}", role="iran", online=True)
        db.add(s)
        db.flush()
        return s.id


def exists(tid):
    with session() as db:
        return db.get(Tunnel, tid) is not None


def say_to(monkeypatch):
    said = []
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    return said


def no_build(monkeypatch):
    """Any build or removal after the guard is a failure of the test."""
    built = []
    monkeypatch.setattr(ap, "_try_build", lambda *a, **k: built.append(a) or (False, ["built"]))
    monkeypatch.setattr(ap, "_remember_rebuild", lambda *a, **k: built.append("note"))
    return built


# ------------------------------------------------------------------ the page
def test_the_menu_and_the_row_carry_the_mark():
    web = Path(__file__).resolve().parent.parent / "nikatel" / "web"
    lab, app, css = (web / "lab.js").read_text(encoding="utf-8"), (web / "app.js").read_text(encoding="utf-8"), (web / "app.css").read_text(encoding="utf-8")
    menu = lab.split("function tunMenuItems(id) {", 1)[1].split("\nfunction labNodeParts", 1)[0]
    assert 'labIt((t.record || {}).protected ? "Remove protection…" : "Protect from the Doctor…", "shield"' in menu and "protectTunnel(t.id, t.name, !(t.record || {}).protected)" in menu
    assert menu.index("Protect from the Doctor") < menu.index('labIt("Remove tunnel…"')          # (Remove stays the last item, in red)
    assert "window.protectTunnel = async (id, name, on) =>" in app and 'api(`/api/tunnels/${id}/protected`, {method: "PUT", body: JSON.stringify({on})})' in app
    assert '<span class="purpose lock"' in app and ">PROTECTED</span>" in app.split("const purposeTags = t =>", 1)[1].split("\n", 1)[0]
    assert ".purpose.lock {" in css


# ------------------------------------------------------------------ the mark
def test_the_mark_is_in_the_record_and_goes_off_again():
    (tid,), _, _ = world("m1")
    assert builder.set_protected(tid, True) is True

    with session() as db:
        t = db.get(Tunnel, tid)
        assert is_protected(t) and t.record["protected"] is True and t.record["network"]          # (the rest of the record stays)
        assert any("is protected" in e.text for e in db.scalars(select(Event).where(Event.ref == t.name)))

    assert builder.set_protected(tid, True) is True                         # (again: nothing changes)
    assert builder.set_protected(tid, False) is False

    with session() as db:
        t = db.get(Tunnel, tid)
        assert not is_protected(t) and "protected" not in t.record and t.record["network"]


def test_a_standby_follows_its_tunnel_and_has_no_mark_of_its_own():
    (tid,), _, _ = world("m2", standby_of="some-tunnel")

    with pytest.raises(ValueError, match="follows its tunnel"):
        builder.set_protected(tid, True)

    with pytest.raises(ValueError, match="not found"):
        builder.set_protected(987654, True)


def test_is_protected_reads_a_row_a_record_and_a_snapshot_record():
    assert is_protected({"protected": True}) and not is_protected({}) and not is_protected({"protected": False})
    (tid,), _, _ = world("m3", protected=(0,))
    assert is_protected(ap._snapshot(tid)["record"])


# ------------------------------------------------------------------ never removed
def test_a_protected_tunnel_is_not_removed_by_anything_until_the_mark_is_off():
    (tid,), _, _ = world("d1", protected=(0,))

    with pytest.raises(ValueError, match="is protected"):
        builder.delete_tunnel(tid, trash=True)

    assert exists(tid)

    with pytest.raises(ValueError, match="is protected"):          # (a rebuild removes first: refused, and its note of "rebuilding…" is dropped again)
        builder.rebuild_tunnel(tid)

    assert exists(tid) and not any(i.get("name") == "d1-t0" for i in st.get("pending_builds").get("items", []))
    builder.set_protected(tid, False)
    assert builder.delete_tunnel(tid, trash=True)
    assert not exists(tid)


def test_the_page_answers_409_and_the_toggle_works(client):
    (tid,), _, _ = world("d2")
    r = client.put(f"/api/tunnels/{tid}/protected", json={"on": True})
    assert r.status_code == 200 and r.json() == {"ok": True, "protected": True}
    assert client.get(f"/api/tunnels/{tid}").json()["record"]["protected"] is True          # (the list shows the mark and the menu reads it)
    d = client.delete(f"/api/tunnels/{tid}")
    assert d.status_code == 409 and "is protected" in d.json()["error"] and exists(tid)
    assert client.put(f"/api/tunnels/{tid}/protected", json={"on": False}).json()["protected"] is False
    assert client.delete(f"/api/tunnels/{tid}").status_code == 200 and not exists(tid)
    assert client.put("/api/tunnels/987654/protected", json={"on": True}).status_code == 404


def test_a_server_is_not_taken_off_with_its_tunnels_while_one_of_them_is_protected(client):
    (a, b), ir, _ = world("d3", n_tunnels=2, protected=(1,))
    r = client.delete(f"/api/servers/{ir}?tunnels=true")
    assert r.status_code == 409 and "d3-t1" in r.json()["error"] and "protected" in r.json()["error"]
    assert exists(a) and exists(b)                                          # (nothing was removed before the answer: the other tunnel is still there)


# ------------------------------------------------------------------ never replaced
def test_replace_stops_before_it_writes_builds_or_removes_anything(monkeypatch):
    (tid,), ir, fr = world("r1", protected=(0,))
    said, built = say_to(monkeypatch), no_build(monkeypatch)
    monkeypatch.setattr(builder, "delete_tunnel", lambda *a, **k: pytest.fail("a protected tunnel was removed"))
    assert ap._replace(1, tid, ir, fr, "backhaul:tcpmux", "A faster type.") is False
    assert built == [] and exists(tid) and any("is protected" in s for s in said)


def test_a_cut_protected_tunnel_is_not_switched_through_the_types(monkeypatch):
    (tid,), _, _ = world("r2", protected=(0,))
    said, built = say_to(monkeypatch), no_build(monkeypatch)
    assert ap._switch_cut(1, ap._snapshot(tid), ["backhaul:tcpmux", "backpack:wss", "chisel:fws"]) == ""
    assert built == [] and exists(tid) and any("no other type is tried" in s for s in said)


def test_the_rebuild_step_of_the_repair_leaves_a_protected_tunnel_alone(monkeypatch):
    (tid,), _, _ = world("r3", protected=(0,))
    said = say_to(monkeypatch)
    monkeypatch.setattr(builder, "rebuild_tunnel", lambda *a, **k: pytest.fail("a protected tunnel was rebuilt"))
    ok, story = ap._rebuild_safe(1, tid)
    assert ok is False and "protected" in story[-1] and any("protected" in s for s in said) and exists(tid)


def test_an_unprotected_tunnel_is_replaced_as_before(monkeypatch):
    (tid,), ir, fr = world("r4")
    say_to(monkeypatch)
    built = no_build(monkeypatch)
    monkeypatch.setattr(ap, "_try_build", lambda snap, i, f, kind, opts=None: built.append(kind) or (True, ["Tunnel is ready."]))
    assert ap._replace(1, tid, ir, fr, "backhaul:tcpmux", "A faster type.") is True
    assert built[-1] == "backhaul:tcpmux" and not exists(tid)               # (the old row is gone; the faked build made no new one)


def test_a_swap_beside_does_not_even_build_the_standby(monkeypatch):
    (tid,), _, _ = world("r5", protected=(0,))
    said = say_to(monkeypatch)
    monkeypatch.setattr(standby, "_view", lambda name: pytest.fail("the swap went on"))
    monkeypatch.setattr(standby, "build_plan", lambda *a, **k: pytest.fail("the swap went on"))
    swap.beside(1, ap._snapshot(tid), "backhaul:tcpmux", "dropped", 300.0)
    assert any("is protected" in s for s in said)
    said.clear()
    swap.beside(1, ap._snapshot(tid), "backhaul:tcpmux", "the users left the new type", 300.0, True)          # (the way back to the old type of the retype watch is a change of type too)
    assert any("is protected" in s for s in said)


def test_the_retype_button_and_the_suggestions_are_told_at_once():
    (tid,), _, _ = world("r6", protected=(0,))

    with pytest.raises(ValueError, match="is protected: its type is not changed"):
        doctor.retype_now(tid, "backhaul:tcpmux", "backhaul:wsmux", "faster there")


# ------------------------------------------------------------------ never moved
def test_a_failover_of_the_whole_server_leaves_the_protected_tunnel_and_its_shared_record(monkeypatch):
    (a, b), ir, _ = world("f1", n_tunnels=2, protected=(1,))
    target = another_iran("f1t")
    records = [{"fqdn": "only-a.example.test", "ips": ["203.0.113.5"], "ip": "203.0.113.5", "configs": [], "tunnels": [a]}, {"fqdn": "both.example.test", "ips": ["203.0.113.5"], "ip": "203.0.113.5", "configs": [], "tunnels": [a, b]}]
    monkeypatch.setattr(failover, "_records", lambda ip, tunnels, hosts=True: ([dict(r) for r in records], ""))
    p = failover._make_plan(ir, None, "down", target_id=target)
    assert p["move"] == [a] and [t["name"] for t in p["tunnels"]] == ["f1-t0"] and p["whole"] is False and p["protected"] == ["f1-t1"]
    assert [d["fqdn"] for d in p["dns"]] == ["only-a.example.test"]         # (the record that serves the protected tunnel too stays where it is)
    assert [d["fqdn"] for d in p["left_alone"]] == ["both.example.test"]


def test_a_plan_without_protected_tunnels_has_no_such_key(monkeypatch):
    (a,), ir, _ = world("f2")
    target = another_iran("f2t")
    monkeypatch.setattr(failover, "_records", lambda ip, tunnels, hosts=True: ([], ""))
    p = failover._make_plan(ir, None, "down", target_id=target)
    assert p["move"] == [a] and p["whole"] is True and "protected" not in p


def test_moving_only_a_protected_tunnel_says_why(monkeypatch):
    (_, b), ir, _ = world("f3", n_tunnels=2, protected=(1,))
    target = another_iran("f3t")
    monkeypatch.setattr(failover, "_records", lambda ip, tunnels, hosts=True: ([], ""))
    monkeypatch.setattr(failover, "move_blocked", lambda: "")

    with pytest.raises(ValueError, match="f3-t1 is protected: never moved"):
        failover.move_server(ir, target, tunnels=[b])


def test_the_move_button_and_the_suggestions_refuse_a_protected_tunnel():
    (tid,), _, _ = world("f4", protected=(0,))
    target = another_iran("f4t")

    with pytest.raises(ValueError, match="is protected: it is never moved"):
        doctor.move_now(tid, target, "manual")

    with pytest.raises(ValueError, match="is protected: it is never moved"):          # (the Doctor's own, for quality: the same door)
        doctor.move_now(tid, target, "quality", "faster there")


def test_the_quality_move_and_the_ladder_do_not_move_or_suggest(monkeypatch):
    (tid,), _, _ = world("f5", protected=(0,))
    said, lines = say_to(monkeypatch), []
    monkeypatch.setattr(doctor, "can_move", lambda: True)
    monkeypatch.setattr(failover, "move_blocked", lambda: "")
    monkeypatch.setattr(failover, "ENABLED", True)
    monkeypatch.setattr(doctor, "suggest", lambda *a, **k: pytest.fail("a move was suggested for a protected tunnel"))
    monkeypatch.setattr(failover, "route_plan", lambda *a, **k: pytest.fail("a plan was made for a protected tunnel"))
    assert failover.quality_move(1, tid, "quality", 100.0, None, lines.append) is False and "protected" in lines[-1]
    assert failover.after_ladder(1, tid, "route_cut") is False and any("protected" in s for s in said)
