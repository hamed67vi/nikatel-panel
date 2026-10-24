"""A rebuild note (settings `pending_builds`) that outlives its tunnel drew a ghost "rebuilding…" row (the owner, 2026-10-24: a deleted tunnel stayed in the list as NEW / starting,
Foreign node "?", and had no button to remove it)."""

from __future__ import annotations


def _note(name: str, iran_id: int, foreign_id: int) -> dict:
    return {"name": name, "kind": "other", "args": {"iran_id": iran_id, "foreign_id": foreign_id, "ports_text": "8075", "transport": "wssmux", "kind": "backpack", "mtu": 0, "keepalive": 75, "nodelay": True, "opts": {}}}


def _pair(db, tag: str = "1"):
    from nikatel.models import Server

    a, b = Server(name=f"i{tag}", host=f"10.0.{tag}.1", role="iran"), Server(name=f"f{tag}", host=f"10.0.{tag}.2", role="foreign")
    db.add_all([a, b])
    db.flush()

    return a.id, b.id


def test_a_note_whose_server_is_gone_draws_no_row():
    from nikatel.api.tunnels import dead_note, rebuilding_rows

    servers = {2: ("ir1", "203.0.113.1"), 3: ("de1", "203.0.113.2")}
    ghost, live = _note("us-r1", 2, 25), _note("de1-r1", 2, 3)

    assert dead_note(ghost, servers) and not dead_note(live, servers)
    assert dead_note(_note("x", 0, 3), servers)          # (a note with no server at all)
    assert [r.name for r in rebuilding_rows([ghost, live], servers, set(), 0)] == ["de1-r1"]          # (a rebuild in flight still shows)


def test_the_list_shows_a_rebuild_in_flight_but_not_a_dead_note(client):
    from nikatel.db import session
    from nikatel.services import settings as st

    with session() as db:
        ir, fr = _pair(db)

    st.put("pending_builds", {"items": [_note("f1-i1", ir, fr), _note("us-gone", ir, 999)]})

    assert [(r["name"], r["state"], r["id"] < 0) for r in client.get("/api/tunnels").json()] == [("f1-i1", "starting", True)]


def test_removing_a_tunnel_by_hand_drops_its_old_rebuild_note(client, monkeypatch):
    """The tunnel f1-i1 had a note nobody dropped (a build that ended while the database was locked). Removed by hand, it must not come back as a ghost row - nor be built again by the next start."""
    from nikatel.db import session
    from nikatel.models import Tunnel
    from nikatel.services import builder
    from nikatel.services import settings as st

    with session() as db:
        ir, fr = _pair(db)
        t = Tunnel(name="f1-i1", type="backpack", transport="wssmux", iran_server_id=ir, foreign_server_id=fr, port=8075, forwarded_ports=[8075])
        db.add(t)
        db.flush()
        tid = t.id

    st.put("pending_builds", {"items": [_note("f1-i1", ir, fr), _note("other", ir, fr)]})
    assert [r["name"] for r in client.get("/api/tunnels").json()] == ["f1-i1", "other"]          # (the real tunnel, and the rebuild in flight)

    def removed(tunnel_id, **kw):          # (what delete_tunnel does to the row; the servers are not asked)
        with session() as db:
            db.delete(db.get(Tunnel, tunnel_id))

        return ["Iran: removed", "foreign: removed"]

    monkeypatch.setattr(builder, "delete_tunnel", removed)
    assert client.delete(f"/api/tunnels/{tid}").status_code == 200
    assert [i["name"] for i in st.get("pending_builds")["items"]] == ["other"]          # (only its own note went)
    assert [r["name"] for r in client.get("/api/tunnels").json()] == ["other"]          # (the other rebuild is still in flight)


def test_a_refused_removal_keeps_the_note(client):
    """A removal that is refused (the tests run read-only) removed nothing: the tunnel is still there and so is its note."""
    from nikatel.db import session
    from nikatel.models import Tunnel
    from nikatel.services import settings as st

    with session() as db:
        ir, fr = _pair(db)
        t = Tunnel(name="f1-i1", type="backpack", transport="wssmux", iran_server_id=ir, foreign_server_id=fr, port=8075, forwarded_ports=[8075])
        db.add(t)
        db.flush()
        tid = t.id

    st.put("pending_builds", {"items": [_note("f1-i1", ir, fr)]})
    r = client.delete(f"/api/tunnels/{tid}")

    assert r.status_code == 400 and "read-only" in r.json()["error"]
    assert [i["name"] for i in st.get("pending_builds")["items"]] == ["f1-i1"]
    assert [x["name"] for x in client.get("/api/tunnels").json()] == ["f1-i1"]


def test_the_start_drops_a_note_whose_server_is_gone_without_a_warning(client, monkeypatch):
    """`resume_pending` used to try the build and report 'could not be resumed' for it, a red Event about a tunnel nobody waits for."""
    from sqlalchemy import select

    from nikatel.db import session
    from nikatel.models import Event
    from nikatel.services import builder
    from nikatel.services import settings as st

    with session() as db:
        ir, fr = _pair(db)

    calls = []
    monkeypatch.setattr(builder, "start_build", lambda *a, **k: calls.append(k) or 1)
    monkeypatch.setattr("nikatel.services.build_other.start_build", lambda *a, **k: calls.append(k) or 1)
    st.put("pending_builds", {"items": [_note("us-gone", ir, 999), _note("f1-i1", ir, fr)]})

    assert builder.resume_pending() == 1          # (the one whose servers exist is built again)
    assert [c["forced_name"] for c in calls] == ["f1-i1"]
    assert [i["name"] for i in st.get("pending_builds")["items"]] == ["f1-i1"]

    with session() as db:
        assert not [e for e in db.scalars(select(Event)) if "us-gone" in (e.text or "")]
