"""A tunnel that a move built as <name>-mv (or -mw) keeps that name for good when the tunnel it replaces is not removed in the same run of the move, or is removed by the janitor as "an extra copy" later
(the owner, 2026-11-18, a list with three of them: "why do these end in mv?"). Now the tidy round gives such a tunnel the name the panel would give it on its Iran server - once the old one is gone, and not while the move is young."""
import time

from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Config, Server, Tunnel
from nikatel.services import builder

OLD = 7200          # (two hours: long past the half hour the move owns a new tunnel)


def _world(foreign="fi1", iran=("ir1", "ir2")):
    with session() as db:
        servers = [Server(name=n, host=f"203.0.113.{10 + i}", role="iran", online=True) for i, n in enumerate(iran)]
        fi = Server(name=foreign, host="203.0.113.50", role="foreign", online=True)
        db.add_all([*servers, fi])
        db.flush()
        return {"iran": [s.id for s in servers], "fi": fi.id}


def _add(ids, name, on=1, age=OLD, ports=None, **rec):
    with session() as db:
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", iran_server_id=ids["iran"][on], foreign_server_id=ids["fi"], state="up", forwarded_ports=ports or [8075], record=rec, created_at=int(time.time()) - age)
        db.add(t)
        db.flush()
        return t.id


def _names():
    with session() as db:
        return sorted(db.scalars(select(Tunnel.name)))


# ------------------------------------------------------------------ the pure rule
def test_the_plain_name_rule():
    f = builder.plain_name_after_move
    iran = ["ir1", "ir2"]
    assert f("fi1-ir1-mv", "fi1", iran, "ir2", {"fi1-ir1-mv"}, OLD) == "fi1-ir2"          # (the old one is gone: the name of the server it runs on)
    assert f("fi1-ir1-mw", "fi1", iran, "ir2", {"fi1-ir1-mw"}, OLD) == "fi1-ir2"          # (-mw is the second kind of the same temporary name)
    assert f("fi1-ir1-mv", "fi1", iran, "ir2", {"fi1-ir1-mv", "fi1-ir1"}, OLD) is None    # (the old one stands beside it: its name is not free)
    assert f("fi1-ir1-mv", "fi1", iran, "ir2", {"fi1-ir1-mv"}, 60) is None                # (the move owns a new tunnel for half an hour)
    assert f("fi1-ir1-mv", "fi1", iran, "ir2", {"fi1-ir1-mv", "fi1-ir2"}, OLD) == "fi1-ir2-2"      # (the name of its server is taken: -2, like every built name)
    assert f("fi1-ir1-2-mv", "fi1", iran, "ir2", {"fi1-ir1-2-mv"}, OLD) == "fi1-ir2"      # (a -N name the panel made counts as a panel name)
    assert f("mine-mv", "fi1", iran, "ir2", {"mine-mv"}, OLD) is None                     # (a name that is not a panel name is not guessed)
    assert f("fi1-ir1", "fi1", iran, "ir2", {"fi1-ir1"}, OLD) is None                     # (no suffix, nothing to drop)


def test_a_cut_stem_is_found_again():
    """`failover._temp_name` keeps 11 characters of a long name before the suffix: the stem is a cut of the name the panel made."""
    ids = _world(foreign="foreignlong1", iran=("iranserver1", "iranserver2"))
    tid = _add(ids, "forei-irans-mv", on=1)
    assert builder.tidy_names() == ["forei-irans-mv → forei-iranserve"]

    with session() as db:
        assert db.get(Tunnel, tid).name == "forei-iranserve"


# ------------------------------------------------------------------ the tidy round
def test_a_move_name_left_alone_is_put_right_when_the_old_tunnel_is_gone():
    ids = _world()
    old = _add(ids, "fi1-ir1", on=0, ports=[8075])
    new = _add(ids, "fi1-ir1-mv", on=1, ports=[8076], slug="fi1-ir1-mw")

    assert builder.tidy_names() == []                          # (the old one stands: nothing is renamed, nothing is removed)
    assert _names() == ["fi1-ir1", "fi1-ir1-mv"]

    with session() as db:
        db.delete(db.get(Tunnel, old))                         # (the janitor, or the owner, removes it)

    assert builder.tidy_names() == ["fi1-ir1-mv → fi1-ir2"]
    assert _names() == ["fi1-ir2"]

    with session() as db:
        t = db.get(Tunnel, new)
        assert t.record["slug"] == "fi1-ir1-mw" and not t.record.get("custom_name")        # (what the servers know it as does not change; it is not a name chosen by hand)

    assert builder.tidy_names() == []                          # (and the round is quiet again)


def test_what_names_the_tunnel_follows_the_new_name():
    ids = _world()
    _add(ids, "fi1-ir1-mv", on=1)

    with session() as db:
        db.add(Config(name="cfg", enabled=True, tunnel="fi1-ir1-mv", meta={}))

    assert builder.tidy_names() == ["fi1-ir1-mv → fi1-ir2"]

    with session() as db:
        assert db.scalar(select(Config.tunnel).where(Config.name == "cfg")) == "fi1-ir2"


def test_a_young_one_a_hand_name_a_standby_a_protected_one_and_a_switched_off_one_are_left_alone():
    ids = _world()
    _add(ids, "fi1-ir1-mv", age=60, ports=[8076])                                # (the move still owns it)
    _add(ids, "fi1-ir1-2-mv", ports=[8077], custom_name=True)                    # (a name chosen by hand)
    _add(ids, "fi1-ir1-3-mv", ports=[8078], protected=True)                      # (something stands on it)
    _add(ids, "fi1-ir1-4-mv", ports=[8079], standby_of="x")                      # (a standby)
    t = _add(ids, "fi1-ir1-5-mv", ports=[8080])

    with session() as db:
        db.get(Tunnel, t).enabled = False                                        # (switched off: the standby job's)

    assert builder.tidy_names() == []
    assert len(_names()) == 5


def test_two_tunnels_of_one_pair_do_not_take_the_same_name_in_one_round():
    ids = _world()
    a = _add(ids, "fi1-ir1-mv", ports=[8076])
    b = _add(ids, "fi1-ir1-2-mv", ports=[8077])
    done = builder.tidy_names()
    assert len(done) == 2
    names = _names()
    assert names == ["fi1-ir2", "fi1-ir2-2"] and len(set(names)) == 2

    with session() as db:
        assert {db.get(Tunnel, a).name, db.get(Tunnel, b).name} == {"fi1-ir2", "fi1-ir2-2"}
