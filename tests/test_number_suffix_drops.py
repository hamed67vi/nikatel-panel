"""A tunnel built beside its old twin gets -2 on its name (`pretty_name` finds the plain name taken) and kept it for good when the twin was removed later
(the owner, 2026-11-23, a list with three of them: "what are these 2 at the end of some names? fix it"). Now the tidy round takes the number off once the plain name is free - and not while the tunnel is young."""
import time

from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Config, Server, Tunnel
from nikatel.services import builder

OLD = 7200          # (two hours: long past the half hour a move owns a new tunnel)


def _world(foreign="fi1", iran=("ir1", "ir2")):
    with session() as db:
        servers = [Server(name=n, host=f"203.0.113.{10 + i}", role="iran", online=True) for i, n in enumerate(iran)]
        fi = Server(name=foreign, host="203.0.113.50", role="foreign", online=True)
        db.add_all([*servers, fi])
        db.flush()
        return {"iran": [s.id for s in servers], "fi": fi.id}


def _add(ids, name, on=0, age=OLD, ports=None, **rec):
    with session() as db:
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", iran_server_id=ids["iran"][on], foreign_server_id=ids["fi"], state="up", forwarded_ports=ports or [8075], record=rec, created_at=int(time.time()) - age)
        db.add(t)
        db.flush()
        return t.id


def _names():
    with session() as db:
        return sorted(db.scalars(select(Tunnel.name)))


# ------------------------------------------------------------------ the pure rule
def test_the_plain_name_without_a_number_rule():
    f = builder.plain_name_without_number
    assert f("fi1-ir1-2", "fi1", "ir1", {"fi1-ir1-2"}, OLD) == "fi1-ir1"                       # (the twin is gone: the plain name is free)
    assert f("fi1-ir1-2", "fi1", "ir1", {"fi1-ir1-2", "fi1-ir1"}, OLD) is None                 # (the twin stands: its name is not free)
    assert f("fi1-ir1-2", "fi1", "ir1", {"fi1-ir1-2"}, 60) is None                             # (young: a build or a move may still be using the pair)
    assert f("fi1-ir1-3", "fi1", "ir1", {"fi1-ir1-3", "fi1-ir1"}, OLD) == "fi1-ir1-2"          # (the plain one is held, -2 is free: the lowest free name)
    assert f("fi1-ir1-3", "fi1", "ir1", {"fi1-ir1-3"}, OLD) == "fi1-ir1"
    assert f("fi1-ir1-2", "fi1", "ir2", {"fi1-ir1-2"}, OLD) is None                            # (it carries the name of ANOTHER Iran server: `moved_name` is the rule for that)
    assert f("mine-2", "fi1", "ir1", {"mine-2"}, OLD) is None                                  # (a name that is not a panel name is not guessed)
    assert f("fi1-ir1", "fi1", "ir1", {"fi1-ir1"}, OLD) is None                                # (no number, nothing to drop)
    assert f("fi1-ir1-mv", "fi1", "ir1", {"fi1-ir1-mv"}, OLD) is None                          # (the move suffix is `plain_name_after_move`)


def test_a_long_name_is_cut_before_the_number():
    """`pretty_name` cuts a 15-character name before the number: the numbered name is a cut of the plain one."""
    plain = builder.pretty_name("foreignlong1", "iranserver1", set())
    numbered = builder.pretty_name("foreignlong1", "iranserver1", {plain})
    assert numbered != plain and numbered.endswith("-2") and len(numbered) <= 15
    assert builder.plain_name_without_number(numbered, "foreignlong1", "iranserver1", {numbered}, OLD) == plain


# ------------------------------------------------------------------ the tidy round
def test_the_number_goes_when_the_twin_is_gone():
    ids = _world()
    old = _add(ids, "fi1-ir1", ports=[8075])
    new = _add(ids, "fi1-ir1-2", ports=[8076], slug="fi1-ir1-2")

    assert builder.tidy_names() == []                          # (the twin stands: nothing is renamed)
    assert _names() == ["fi1-ir1", "fi1-ir1-2"]

    with session() as db:
        db.delete(db.get(Tunnel, old))                         # (the swap removes the old one when the users have left it)

    assert builder.tidy_names() == ["fi1-ir1-2 → fi1-ir1"]
    assert _names() == ["fi1-ir1"]

    with session() as db:
        t = db.get(Tunnel, new)
        assert t.record["slug"] == "fi1-ir1-2" and not t.record.get("custom_name")          # (what the servers know it as does not change; it is not a name chosen by hand)

    assert builder.tidy_names() == []                          # (and the round is quiet again)


def test_what_names_the_tunnel_follows_the_new_name():
    ids = _world()
    _add(ids, "fi1-ir1-2")

    with session() as db:
        db.add(Config(name="cfg", enabled=True, tunnel="fi1-ir1-2", meta={}))

    assert builder.tidy_names() == ["fi1-ir1-2 → fi1-ir1"]

    with session() as db:
        assert db.scalar(select(Config.tunnel).where(Config.name == "cfg")) == "fi1-ir1"


def test_a_young_one_a_hand_name_a_standby_a_protected_one_and_a_switched_off_one_are_left_alone():
    ids = _world()
    _add(ids, "fi1-ir1-2", age=60, ports=[8076])                                  # (young)
    _add(ids, "fi1-ir2-2", on=1, ports=[8077], custom_name=True)                  # (a name chosen by hand)
    _add(ids, "fi1-ir1-3", ports=[8078], protected=True)                          # (something stands on it)
    _add(ids, "fi1-ir1-4", ports=[8079], standby_of="x")                          # (a standby)
    t = _add(ids, "fi1-ir1-5", ports=[8080])

    with session() as db:
        db.get(Tunnel, t).enabled = False                                          # (switched off: the standby job's)

    assert builder.tidy_names() == []
    assert len(_names()) == 5


def test_a_tunnel_whose_plain_name_is_held_by_another_keeps_its_number():
    ids = _world()
    _add(ids, "fi1-ir1", ports=[8075])
    _add(ids, "fi1-ir1-2", ports=[8076])
    assert builder.tidy_names() == []
    assert _names() == ["fi1-ir1", "fi1-ir1-2"]


def test_two_tunnels_of_one_pair_do_not_take_the_same_name_in_one_round():
    ids = _world()
    a = _add(ids, "fi1-ir1-2", ports=[8076])
    b = _add(ids, "fi1-ir1-3", ports=[8077])
    done = builder.tidy_names()
    assert len(done) == 2
    names = _names()
    assert names == ["fi1-ir1", "fi1-ir1-2"] and len(set(names)) == 2

    with session() as db:
        assert {db.get(Tunnel, a).name, db.get(Tunnel, b).name} == {"fi1-ir1", "fi1-ir1-2"}
