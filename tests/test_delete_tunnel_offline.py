"""Deleting a tunnel whose server is off (the owner, 2026-10-19: "even my tunnel is not deleted"; the log: `DELETE /api/tunnels/51 -> 200 in 32679ms` twice, `database is locked` in a job meanwhile):

  the lock     a BackPack / Paqet / Chisel tunnel was removed from its servers INSIDE the database session that had just written the action's row: SQLite lets one writer in, so for the whole SSH wait
               (30 - 90 s per side when a server does not answer) every job and every page that wrote failed with `database is locked` - the servers are now asked with no session open
  the wait     the page's delete asked an off server over SSH and waited for it: it now skips a server that is off (its files are purged when it is back)"""
import sqlite3
from pathlib import Path

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import build_other, builder


def make_tunnel(name: str, foreign_online: bool) -> int:
    with session() as db:
        n = len(db.query(Server).all()) + 40
        ir = Server(name=f"ir-{name}", host=f"203.0.113.{n}", role="iran", online=True)
        fr = Server(name=f"fr-{name}", host=f"198.51.100.{n}", role="foreign", online=foreign_online)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backpack", transport="wss", port=9903, iran_server_id=ir.id, foreign_server_id=fr.id, state="down", forwarded_ports=[8072])
        db.add(t)
        db.flush()
        return t.id


def fake_servers(monkeypatch):
    """The servers answer; each question also tries to take the write lock of the database from another connection - which fails while a session of the panel holds it."""
    calls, locked = [], []

    def run_action(node, name, params=None, timeout=90):
        calls.append((node.host, name))
        path = Path(get_settings().db_url.replace("sqlite:///", ""))
        c = sqlite3.connect(str(path), timeout=0.3)

        try:
            c.execute("BEGIN IMMEDIATE")
            c.rollback()
        except sqlite3.OperationalError:
            locked.append(node.host)
        finally:
            c.close()

        return ""

    monkeypatch.setattr(build_other, "run_action", run_action)
    return calls, locked


def test_a_backpack_tunnel_is_removed_from_its_servers_with_no_database_lock_held(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)          # (the tests run read-only)
    tid = make_tunnel("bpdel1", foreign_online=True)
    calls, locked = fake_servers(monkeypatch)
    res = builder.delete_tunnel(tid, trash=True)
    assert [c[1] for c in calls] == ["other_remove", "other_remove"] and res == ["Iran: removed", "foreign: removed"]
    assert locked == []                                                                                                  # (the servers were asked while another writer could still get in)

    with session() as db:
        assert db.get(Tunnel, tid) is None


def test_the_page_removes_the_tunnel_of_a_server_that_is_off_without_waiting_for_it(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    tid = make_tunnel("bpdel2", foreign_online=False)
    calls, locked = fake_servers(monkeypatch)
    r = client.delete(f"/api/tunnels/{tid}")
    assert r.status_code == 200
    assert len(calls) == 1 and calls[0][0].startswith("203.0.113.")                                                      # only the Iran server (it answers) was asked
    assert r.json()["results"][0] == "Iran: removed" and r.json()["results"][1].startswith("foreign: skipped (the server is off")
    assert locked == []

    with session() as db:
        assert db.get(Tunnel, tid) is None
