"""A repair that takes a tunnel off to build another type never leaves the owner with nothing (the owner, 2026-10-21: "the UA tunnel was deleted by itself - find the cause and fix it").

The real case: the link to the foreign server was cut (100 % loss both ways), the smart repair took the tunnel off both servers, tried twenty BackPack / Chisel / Paqet types - every one stopped at the
same line, "the foreign server has no python3 (needed for the test door)", which says nothing about the route - and could not build the old type either (the route lost 3 of 10 packets): the tunnel was
gone from the list, with no copy and no row for the repair to retry. Now:

  before   a type that a server cannot run at all is left out BEFORE anything is removed; when nothing is left to build the tunnel is not touched
  during   the removal keeps a copy and (in place) writes down how to build it again, so a restart of the panel in the middle builds it again at the start
  after    nothing built, not even the old type: the tunnel is put back in the list as DOWN, with the reason, and the repair goes on with it
  memory   a type that failed only because of the server is "could not be tried", never "blocked for two weeks"
"""
import time

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import (
    analyst,
    build_other,
    builder,
    failover,
    knowledge,
    tunnel_test,
)
from nikatel.services import autopilot as ap
from nikatel.services import settings as st

NO_PYTHON = "Stopped: Foreign server UA1 has no python3 (needed for the test door). Everything created was removed again."
CANNOT_PING = "Stopped: the IPIP link is up but the inner addresses cannot ping each other (Iran→foreign 7/10, foreign→Iran 9/10). Everything created was removed again."


@pytest.fixture(autouse=True)
def _writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)          # (the tests run read-only)
    monkeypatch.setattr(builder, "run_action", lambda *a, **k: "")


def world(tag="x", tunnel="ua1-ir2"):
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + tag, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + tag, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=tunnel, type="backhaul", transport="tcpmux", port=9902, iran_server_id=ir.id, foreign_server_id=fr.id, state="down", forwarded_ports=[8072], iran_tunnel_ip="10.174.71.41",
                   foreign_tunnel_ip="10.174.71.42", record={"link": "ipip", "mtu": 1360, "keepalive": 75, "opts": {"link": "ipip"}, "network": "10.174.71.40/30"}, secrets={"token": "t0k", "gre_key": 77}, created_at=1_790_000_000)
        db.add(t)
        db.flush()
        return t.id, ir.id, fr.id


def row_of(tid):
    with session() as db:
        return db.get(Tunnel, tid)


def tunnel_by_name(name):
    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == name))
        return None if t is None else {c.name: getattr(t, c.name) for c in Tunnel.__table__.columns}


def events_about(name):
    with session() as db:
        return [e.text for e in db.scalars(select(Event).where(Event.ref == name))]


def fake_builds(monkeypatch, results):
    """The builds, faked: `results` maps a kind to the last line its build stops with ("" = it works); any other kind fails with that default. Returns the list of kinds built, in order."""
    built = []

    def try_build(snap, iran_id, foreign_id, kind, opts=None):
        built.append(kind)
        line = results.get(kind, results.get("*", CANNOT_PING))
        return (not line), ([line] if line else ["Tunnel is ready."])

    monkeypatch.setattr(ap, "_try_build", try_build)
    return built


def say_to(monkeypatch):
    said = []
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    return said


# ------------------------------------------------------------------ the two pure judgements
def test_a_server_that_cannot_run_the_engine_is_told_from_a_route_that_fails():
    assert tunnel_test.server_unfit(["x", NO_PYTHON]) is True
    assert tunnel_test.server_unfit(["Stopped: Iran server ir1: the SSH user is not root"]) is True
    assert tunnel_test.server_unfit(["Stopped: Foreign server f1: architecture aarch64 is not supported (x86_64 only)"]) is True
    assert tunnel_test.server_unfit([CANNOT_PING]) is False and tunnel_test.server_unfit([]) is False and tunnel_test.server_unfit(None) is False
    assert tunnel_test.server_unfit([NO_PYTHON] + ["later line"] * 7) is False          # only the end of the story counts

    ok = {"root": True, "python": True, "arch": "x86_64"}
    assert build_other.unfit_reason(ok) == "" and build_other.unfit_reason({**ok, "arch": "amd64"}) == ""
    assert "python3" in build_other.unfit_reason({**ok, "python": False}) and "not root" in build_other.unfit_reason({**ok, "root": False})
    assert "aarch64" in build_other.unfit_reason({**ok, "arch": "aarch64"})
    assert build_other.unfit_reason(build_other.parse_simple_facts("ID=0\nPY=0\nARCH=x86_64\n")).startswith("has no python3")
    assert build_other.unfit_reason(build_other.parse_simple_facts("ID=0\nPY=1\nARCH=x86_64\nLISTEN=22\n")) == ""


def test_the_look_at_the_servers_judges_only_what_it_saw(monkeypatch):
    tid, ir, fr = world("look1")
    snap = ap._snapshot(tid)
    seen = {}

    def run_script(node, name, params=None, timeout=90):
        return seen[node.host]

    monkeypatch.setattr(ap, "run_script", run_script)

    with session() as db:
        hosts = {db.get(Server, ir).name: db.get(Server, ir).host, db.get(Server, fr).name: db.get(Server, fr).host}

    seen[hosts["ir-look1"]], seen[hosts["fr-look1"]] = "ID=0\nPY=1\nARCH=x86_64\n", "ID=0\nPY=0\nARCH=x86_64\n"
    assert ap._unfit_servers(snap) == {"fr-look1": "has no python3 (needed for the test door)"}
    seen[hosts["fr-look1"]] = ""                                                              # (an answer with no facts in it: not judged, never "unfit")
    assert ap._unfit_servers(snap) == {}
    monkeypatch.setattr(ap, "run_script", lambda *a, **k: (_ for _ in ()).throw(ap.SSHError("no answer")))
    assert ap._unfit_servers(snap) == {}                                                      # (a server that does not answer is not judged either)


# ------------------------------------------------------------------ the row is put back
def test_the_row_of_a_tunnel_is_put_back_as_it_was_but_down():
    tid = world()[0]
    snap = ap._snapshot(tid)
    before = tunnel_by_name("ua1-ir2")
    builder.pending_put({"name": "ua1-ir2", "kind": "backhaul", "args": {}})

    with session() as db:
        db.delete(db.get(Tunnel, tid))

    assert builder.put_back_down(snap["row"], "the IPIP link is up but the inner addresses cannot ping each other") is True
    after = tunnel_by_name("ua1-ir2")
    assert after["id"] == tid                                                                  # the same id: the rest of the repair that holds it still finds it
    assert all(after[k] == before[k] for k in ("name", "type", "transport", "port", "iran_server_id", "foreign_server_id", "forwarded_ports", "record", "secrets", "iran_tunnel_ip", "foreign_tunnel_ip", "enabled", "created_at"))
    assert after["state"] == "down" and after["last_error"].startswith("Taken off by a repair that could not build it again: the IPIP link is up") and after["state_since"] > 0
    assert any("could not be built again" in e and "stays in the list as down" in e for e in events_about("ua1-ir2"))
    assert st.get("pending_builds").get("items", []) == []                                     # (the restart's way back is not needed any more: the row is there)
    assert builder.put_back_down(snap["row"], "again") is False                                # a tunnel of this name exists: nothing is put twice
    assert len(events_about("ua1-ir2")) == 1


def test_the_row_is_not_put_back_when_a_server_is_gone_or_the_id_is_taken():
    tid, ir, fr = world("gone1")
    snap = ap._snapshot(tid)

    with session() as db:
        db.delete(db.get(Tunnel, tid))
        db.add(Tunnel(name="someone-else", type="backhaul", iran_server_id=ir, foreign_server_id=fr, state="up"))
        db.flush()
        taken = db.scalar(select(Tunnel.id).where(Tunnel.name == "someone-else"))

    snap["row"]["id"] = taken                                                                   # (the id was given to another tunnel meanwhile: the row gets a new one)
    assert builder.put_back_down(snap["row"], "x") is True and tunnel_by_name("ua1-ir2")["id"] != taken

    with session() as db:
        db.delete(db.scalar(select(Tunnel).where(Tunnel.name == "ua1-ir2")))
        db.delete(db.scalar(select(Tunnel).where(Tunnel.name == "someone-else")))
        db.flush()
        db.delete(db.get(Server, fr))

    assert builder.put_back_down(snap["row"], "x") is False                                    # its foreign server is not in the panel any more
    assert builder.put_back_down({}, "x") is False


# ------------------------------------------------------------------ what a restart builds again
def test_the_way_to_build_it_again_is_the_one_a_rebuild_writes():
    rec = {"link": "ipip", "mtu": 1360, "keepalive": 60, "nodelay": False, "opts": {"link": "ipip"}}
    item = builder.pending_item("t1", 4, 9, "backhaul", "tcpmux", rec, "8072")
    assert item == {"name": "t1", "kind": "backhaul", "args": {"iran_id": 4, "foreign_id": 9, "ports_text": "8072", "transport": "tcpmux", "kind": "backhaul", "mtu": 1360, "keepalive": 60, "nodelay": False, "opts": {"link": "ipip"}}}
    other = builder.pending_item("t2", 4, 9, "backpack", "l3:pck", {"kind": "backpack_direct"}, "8072,8073")
    assert other["kind"] == "other" and other["args"]["transport"] == "pck" and other["args"]["kind"] == "backpack_direct" and other["args"]["keepalive"] == 75 and other["args"]["nodelay"] is True
    assert builder.pending_item("t3", 1, 2, "backhaul", "wssmux", {}, "1", opts={"avoid_net": "x"})["args"]["opts"] == {"avoid_net": "x"}


def test_a_repair_writes_down_how_to_build_the_tunnel_before_it_takes_it_off(monkeypatch):
    tid, ir, fr = world("rs1")
    said = say_to(monkeypatch)
    seen = []
    real_delete = builder.delete_tunnel
    monkeypatch.setattr(builder, "delete_tunnel", lambda t, **kw: seen.append((st.get("pending_builds").get("items", []), kw)) or real_delete(t, **kw))
    fake_builds(monkeypatch, {"wssmux": ""})
    assert ap._replace(1, tid, ir, fr, "wssmux", "", restore=True) is True
    items, kw = seen[0]
    assert [i["name"] for i in items] == ["ua1-ir2"] and items[0]["args"]["opts"] == {"link": "ipip"} and items[0]["args"]["iran_id"] == ir        # written BEFORE the removal
    assert kw["trash"] is True                                                                                                                     # ... with a copy kept for Restore
    assert any(i["name"] == "ua1-ir2" for i in st.get("trash").get("items", []))
    assert any("The new wssmux tunnel is built and works." in ln for ln in said)


def test_a_move_to_another_server_is_not_written_down_for_the_old_pair(monkeypatch):
    tid, ir, fr = world("mv1")
    other_ir = world("mv1-other", tunnel="other-tun")[1]
    snap = ap._snapshot(tid)
    ap._remember_rebuild(snap, other_ir, fr)
    assert st.get("pending_builds").get("items", []) == []                                     # (the old Iran server is what is broken)
    ap._remember_rebuild(snap, ir, fr)
    assert [i["name"] for i in st.get("pending_builds").get("items", [])] == ["ua1-ir2"]


def test_a_restart_in_the_middle_of_a_repair_builds_the_tunnel_again_at_the_start(monkeypatch):
    tid, ir, fr = world("rs2")
    ap._remember_rebuild(ap._snapshot(tid), ir, fr)
    builder.delete_tunnel(tid, trash=True)                                                     # (the repair took it off - and the panel restarted before anything was built)
    assert tunnel_by_name("ua1-ir2") is None
    built = []
    monkeypatch.setattr(builder, "start_build", lambda *a, **k: built.append((a, k)) or 5)
    assert builder.resume_pending() == 1
    args, kw = built[0]
    assert args[:4] == (ir, fr, "8072", "tcpmux") and kw["forced_name"] == "ua1-ir2" and kw["opts"] == {"link": "ipip"}
    assert any("interrupted by a restart of the panel" in e for e in events_about("ua1-ir2"))


# ------------------------------------------------------------------ nothing built: the tunnel stays in the list
def test_when_neither_the_new_type_nor_the_old_one_can_be_built_the_tunnel_is_put_back_down(monkeypatch):
    tid, ir, fr = world()
    said = say_to(monkeypatch)
    built = fake_builds(monkeypatch, {"*": CANNOT_PING})
    assert ap._replace(1, tid, ir, fr, "wssmux", "Best type today.", restore=True) is False
    assert built == ["wssmux", "ipip:tcpmux"]                                                  # the new type, then the old one (its own kind: the link is part of it)
    t = tunnel_by_name("ua1-ir2")
    assert t is not None and t["id"] == tid and t["state"] == "down" and "cannot ping each other" in t["last_error"]
    assert any("put back in the list as DOWN" in ln for ln in said) and not any("needs a human" in ln for ln in said)
    assert st.get("pending_builds").get("items", []) == []


def test_when_the_old_type_is_built_again_nothing_is_put_back(monkeypatch):
    tid, ir, fr = world()
    say_to(monkeypatch)
    built = fake_builds(monkeypatch, {"ipip:tcpmux": ""})
    assert ap._replace(1, tid, ir, fr, "wssmux", "", restore=True) is False and built == ["wssmux", "ipip:tcpmux"]
    assert tunnel_by_name("ua1-ir2") is None                                               # (the real build of the old type makes the row itself: a second one is not put beside it)


def test_a_build_the_code_refuses_to_start_is_a_failed_build_and_not_the_end_of_the_repair(monkeypatch):
    tid, ir, fr = world("ref1")
    said = say_to(monkeypatch)

    def refuse(snap, i, f, kind, opts=None):
        raise ValueError(f"port 9902 is already in use on the Iran server ({kind})")

    monkeypatch.setattr(ap, "_build_kind", refuse)
    ok, story = ap._try_build(ap._snapshot(tid), ir, fr, "wssmux")
    assert ok is False and story == ["Stopped: port 9902 is already in use on the Iran server (wssmux)"]
    assert ap._replace(1, tid, ir, fr, "wssmux", "", restore=True) is False                    # the new build is refused, so is the old one: the tunnel is still not lost
    assert tunnel_by_name("ua1-ir2") is not None
    assert any("put back in the list as DOWN" in ln for ln in said)


def test_a_rebuild_that_cannot_even_start_after_the_removal_puts_the_old_one_back(monkeypatch):
    tid = world("rb1")[0]
    said = say_to(monkeypatch)

    def rebuild(tunnel_id, fresh_link=False):
        builder.delete_tunnel(tunnel_id, trash=True)
        raise ValueError("port 9902 is already in use on the Iran server; choose another port")

    monkeypatch.setattr(builder, "rebuild_tunnel", rebuild)
    built = fake_builds(monkeypatch, {"*": CANNOT_PING})
    ok, story = ap._rebuild_safe(3, tid)
    assert ok is False and story == ["Stopped: port 9902 is already in use on the Iran server; choose another port"] and built == ["ipip:tcpmux"]
    assert tunnel_by_name("ua1-ir2")["state"] == "down" and any("put back in the list as DOWN" in ln for ln in said)


def test_a_rebuild_that_is_refused_before_anything_was_removed_ends_there(monkeypatch):
    tid = world("rb2")[0]
    say_to(monkeypatch)
    monkeypatch.setattr(builder, "rebuild_tunnel", lambda tunnel_id, fresh_link=False: (_ for _ in ()).throw(ValueError("the panel is read-only")))
    built = fake_builds(monkeypatch, {})

    with pytest.raises(ValueError, match="read-only"):
        ap._rebuild_safe(3, tid)

    assert built == [] and tunnel_by_name("ua1-ir2") is not None


# ------------------------------------------------------------------ the case of UA1
def switch_world(monkeypatch, d):
    said = say_to(monkeypatch)
    monkeypatch.setattr(ap, "diagnose", lambda tid: d)
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(analyst, "hints_for", lambda tunnel, cause, steps: (list(steps), []))
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    monkeypatch.setattr(ap, "_probe_links", lambda aid, snap, ks: [k for k in ks if tunnel_test.kind_link(k) == "gre"])          # (as in the real case: no link passes, so every kind that runs over one is left out)
    monkeypatch.setattr(ap, "_carrier_last_resort", lambda aid, snap, tried: [])
    monkeypatch.setattr(failover, "after_ladder", lambda *a, **k: False)
    monkeypatch.setattr(ap, "probe_other_iran", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_wait_quiet", lambda aid, tid: True)
    removed = []
    real_delete = builder.delete_tunnel
    monkeypatch.setattr(builder, "delete_tunnel", lambda t, **kw: removed.append(t) or real_delete(t, **kw))
    return said, removed


UA_CUT = {"cause": "route_cut", "summary": "the two servers cannot reach each other (loss 100.0% / 100.0%)", "kind": "ipip:tcpmux", "state": "down", "where": "foreign_side", "ext_blocked": False, "dial_i": False, "dial_f": False}


def test_the_tunnel_of_ua1_is_not_taken_off_when_no_type_can_run_on_that_server(monkeypatch):
    """The 2026-10-21 case from the top: every kind the repair could try needs python3 on the foreign server, which has none."""
    tid = world()[0]
    said, removed = switch_world(monkeypatch, UA_CUT)
    monkeypatch.setattr(ap, "_unfit_servers", lambda snap: {"UA1": "has no python3 (needed for the test door)"})
    built = fake_builds(monkeypatch, {"*": NO_PYTHON})
    assert ap.smart_fix(7, tid, "switch", "auto") is False
    assert removed == [] and built == []                                                       # nothing was taken off, nothing was built
    t = tunnel_by_name("ua1-ir2")
    assert t is not None and t["id"] == tid and t["secrets"] == {"token": "t0k", "gre_key": 77}
    assert any("kind(s) left out, nothing was removed for them: UA1 has no python3" in ln for ln in said)
    assert any("Nothing could be built on this route, so nothing was removed: the tunnel stays as it is." in ln for ln in said)
    assert any("The repair tries again in 1 minute" in ln for ln in said)                      # (and it can: the tunnel is in the list)


def test_the_types_a_server_cannot_run_are_left_out_and_the_others_are_still_tried(monkeypatch):
    tid = world("mix1")[0]
    said = switch_world(monkeypatch, {**UA_CUT, "dial_i": True, "dial_f": True})[0]
    monkeypatch.setattr(ap, "_unfit_servers", lambda snap: {"UA1": "has no python3 (needed for the test door)"})
    attempts = []

    def replace(aid, tunnel_id, iran, foreign, kind, why, skip_offline=False, snap=None, first=True, restore=True, out=None):
        attempts.append(kind)
        return kind == "wssmux"

    monkeypatch.setattr(ap, "_replace", replace)
    kinds = ["bp:wssmux", "chisel:tls", "wssmux", "bpd:pck", "paqet"]
    assert ap._switch_cut(1, ap._snapshot(tid), kinds) == "wssmux"
    assert attempts == ["wssmux"] and any(ln.startswith("4 kind(s) left out, nothing was removed for them") for ln in said)


def test_a_server_that_turns_out_unfit_in_the_middle_stops_the_other_types_of_the_engine_and_is_not_held_against_the_route(monkeypatch):
    tid, ir, fr = world("mid1")
    said = switch_world(monkeypatch, UA_CUT)[0]
    monkeypatch.setattr(ap, "_unfit_servers", lambda snap: {})                                  # (the look before saw nothing wrong - the first build finds out)
    built = fake_builds(monkeypatch, {"bp:wssmux": NO_PYTHON, "wssmux": CANNOT_PING, "*": CANNOT_PING})
    assert ap._switch_cut(1, ap._snapshot(tid), ["bp:wssmux", "chisel:tls", "bp:tcpmux", "wssmux"]) == ""
    assert built == ["bp:wssmux", "wssmux", "ipip:tcpmux"]                                      # chisel:tls and bp:tcpmux were not tried; the old type was, last
    assert any("2 more kind(s) left out: the server cannot run them" in ln for ln in said)
    p = knowledge.pair(ir, fr)
    assert knowledge.verdict(p["kinds"]["bp:wssmux"]) == "untested" and knowledge.verdict(p["kinds"]["wssmux"]) == "failed"          # not tested is not blocked
    assert "bp:wssmux" not in knowledge.blocked(p, int(time.time())) and "wssmux" in knowledge.blocked(p, int(time.time()))
    assert knowledge.untested(p)[0]["reason"] == "server"
    t = tunnel_by_name("ua1-ir2")
    assert t is not None and t["state"] == "down" and any("put back in the list as DOWN" in ln for ln in said)


def test_the_repair_of_a_cut_tunnel_that_ends_with_nothing_built_leaves_the_tunnel_to_be_retried(monkeypatch):
    tid = world("end1")[0]
    said, removed = switch_world(monkeypatch, {**UA_CUT, "dial_i": True, "dial_f": True})
    monkeypatch.setattr(ap, "_unfit_servers", lambda snap: {})
    built = fake_builds(monkeypatch, {"*": CANNOT_PING})
    assert ap.smart_fix(8, tid, "switch", "auto") is False
    assert removed == [tid] and built[-1] == "ipip:tcpmux" and len(built) > 2                   # taken off once, many types tried, the old type last
    t = tunnel_by_name("ua1-ir2")
    assert t is not None and t["state"] == "down" and t["id"] == tid
    assert not any("tunnel not found" in ln for ln in said)                                    # (it was "No plan for a move: tunnel not found": the row was gone)
