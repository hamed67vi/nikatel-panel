"""The duplicate sweep also settles a copy that DNS names are tied to (1.69.219; the owner, 2026-11-04: "the Doctor was to remove the duplicate tunnels by itself"): the names that serve ONLY the copy are pointed at the
Iran server of the keeper (the records stay - the way back to Arvan), and when the copy is quiet it goes. Before this, two FR tunnels stayed for hours: one served the current configs, the other two old CDN names."""
import time

from test_failover import _servers_and_tunnels
from test_janitor import HOUR, OLD, G

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Tunnel, TunnelCheck
from nikatel.services import autopilot, failover, janitor
from nikatel.services import settings as st


# ------------------------------------------------------------------ the pure parts
def test_the_copy_that_names_are_tied_to_is_the_one_to_consolidate_when_the_keeper_is_up():
    keeper, copy = G[0], G[1]
    ref = {1: True, 2: True}
    assert janitor.consolidation_target(G, keeper, OLD, ref, {}, 10_000_000) == copy
    assert janitor.consolidation_target(G, {**keeper, "state": "down"}, OLD, ref, {}, 10_000_000) is None                 # (a keeper that is not up carries nobody)
    assert janitor.consolidation_target(G, None, OLD, ref, {}, 10_000_000) is None
    assert janitor.consolidation_target(G, keeper, {1: 4 * HOUR, 2: 600}, ref, {}, 10_000_000) is None                      # (a young copy: maybe a move in progress)
    assert janitor.consolidation_target(G, keeper, OLD, {1: True, 2: False}, {}, 10_000_000) is None                        # (nothing tied: the plain sweep handles it)
    assert janitor.consolidation_target(G, keeper, OLD, {1: True, 2: None}, {}, 10_000_000) is None                         # (the records could not be asked)
    now = 10_000_000
    assert janitor.consolidation_target(G, keeper, OLD, ref, {"2": now - 600}, now) is None                                 # (its names moved ten minutes ago)
    assert janitor.consolidation_target(G, keeper, OLD, ref, {"2": now - janitor.POINT_GAP - 1}, now) == copy


def test_a_copy_whose_names_moved_is_extra_when_it_is_quiet_in_the_last_minutes_whatever_its_three_hours_hold():
    keeper, copy = G[0], G[1]
    now = 10_000_000
    at = {"2": now - janitor.SETTLE - 5}
    assert janitor.pick_after_point(G, keeper, at, {1: 9.0, 2: 0.05}, {1: True, 2: False}, now) == [copy]
    assert janitor.pick_after_point(G, keeper, {"2": now - 60}, {1: 9.0, 2: 0.05}, {1: True, 2: False}, now) == []          # (too soon)
    assert janitor.pick_after_point(G, keeper, at, {1: 9.0, 2: 1.2}, {1: True, 2: False}, now) == []                        # (still carries something)
    assert janitor.pick_after_point(G, keeper, at, {1: 9.0, 2: None}, {1: True, 2: False}, now) == []                       # (not measured)
    assert janitor.pick_after_point(G, keeper, at, {1: 9.0, 2: 0.05}, {1: True, 2: True}, now) == []                        # (a name is still tied to it)
    assert janitor.pick_after_point(G, keeper, {}, {1: 9.0, 2: 0.05}, {1: True, 2: False}, now) == []                       # (its names never moved: that is not this rule)
    assert janitor.pick_after_point(G, None, at, {1: 9.0, 2: 0.05}, {1: True, 2: False}, now) == []


# ------------------------------------------------------------------ the job
def _world(monkeypatch, mode="move", shared=False, no_names=False):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(janitor.jobs, "stopping", lambda: False)
    monkeypatch.setattr(janitor, "_TOLD", {})
    st.put("repair", {"mode": mode})
    st.put("janitor", {"done": [], "pointed": {}})
    ids = _servers_and_tunnels()
    now = int(time.time())

    with session() as db:
        old = now - 5 * HOUR
        t2 = db.get(Tunnel, ids["t2"])
        t2.created_at = old
        dup = Tunnel(name="t2-dup", type="backhaul", transport="tcpmux", iran_server_id=ids["ir2"], foreign_server_id=ids["fi1"], state="up", forwarded_ports=[8075], created_at=old)
        db.add(dup)
        db.flush()

        for tid, rx, tx in ((t2.id, 20.0, 3.0), (dup.id, 2.0, 0.5)):                       # (the copy still carries 2.5 Mb/s: over the 1.5 that makes it "used")
            db.add_all([TunnelCheck(tunnel_id=tid, ts=now - i * 60, ok=True, rx_mbps=rx, tx_mbps=tx) for i in range(int(3.2 * 60))])

        dup_id = dup.id

    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn.__name__, a)) or 1)
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(janitor, "_referenced", lambda r: True)                              # (names are tied to both: the plain sweep stays out)
    plan = {"dns": [] if no_names else [{"fqdn": "frnika.example.test"}, {"fqdn": "bgt.example.test"}], "left_alone": [{"fqdn": "tr1ss.example.test"}] if shared else [], "dns_error": ""}
    asked = []
    monkeypatch.setattr(failover, "replace_plan", lambda tid, iran_id: asked.append((tid, iran_id)) or plan)
    return ids, dup_id, started, asked


def test_the_names_that_serve_only_the_copy_are_pointed_at_the_keeper_in_the_mode_move(monkeypatch):
    ids, dup_id, started, asked = _world(monkeypatch)

    with session() as db:
        keeper_iran = db.get(Tunnel, ids["t2"]).iran_server_id

    janitor.run()
    assert [(s[0], s[1]) for s in started] == [("t2-dup", "consolidate_names")] and started[0][2] == (keeper_iran, ["frnika.example.test", "bgt.example.test"], "t2")
    assert asked == [(dup_id, keeper_iran)] and list(st.get("janitor")["pointed"]) == [str(dup_id)] and st.get("janitor")["done"]
    janitor.run()
    assert len(started) == 1                                                                 # (the gap between two actions, and its names moved a moment ago)


def test_nothing_is_pointed_in_the_mode_fix(monkeypatch):
    _ids, _dup, started, asked = _world(monkeypatch, mode="fix")
    janitor.run()
    assert started == [] and asked == []                                                     # (DNS changes only in the mode move)


def test_a_name_that_also_serves_another_tunnel_is_left_alone(monkeypatch):
    _ids, _dup, started, _asked = _world(monkeypatch, shared=True)
    janitor.run()
    assert started == []


def test_nothing_is_pointed_when_no_name_serves_only_the_copy(monkeypatch):
    _ids, _dup, started, _asked = _world(monkeypatch, no_names=True)
    janitor.run()
    assert started == []


def test_the_copy_goes_by_the_plain_sweep_once_its_names_have_moved_and_it_is_quiet(monkeypatch):
    _ids, dup_id, started, _asked = _world(monkeypatch)
    now = int(time.time())
    st.put("janitor", {"done": [], "pointed": {str(dup_id): now - janitor.SETTLE - 30}})
    monkeypatch.setattr(janitor, "_referenced", lambda r: r["name"] == "t2")      # (the names moved: nothing is tied to the copy now)
    monkeypatch.setattr(janitor, "_use_recent", lambda tid, now, window=900: 0.05)
    janitor.run()
    assert [(s[0], s[1]) for s in started] == [("t2-dup", "remove_extra")]


def test_the_sweep_removes_the_standby_of_the_copy_with_it(monkeypatch):
    from nikatel.services import builder, orphans, standby

    _ids, dup_id, _started, _asked = _world(monkeypatch)
    said, calls = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append((line, done, ok)))
    monkeypatch.setattr(builder, "delete_tunnel", lambda tid, **kw: calls.append(("delete", tid)) or ["Iran: removed", "foreign: removed"])
    monkeypatch.setattr(builder, "pending_drop", lambda name: calls.append(("pending_drop", name)))
    monkeypatch.setattr(standby, "forget", lambda name: calls.append(("forget", name)))
    monkeypatch.setattr(orphans, "drop_helpers_of", lambda name: calls.append(("helpers", name)) or ["t2-dup-sb"])
    monkeypatch.setattr(janitor, "_use", lambda tid, now: 0.1)
    janitor.remove_extra(9, dup_id, "t2", 0.1)
    assert calls == [("delete", dup_id), ("forget", "t2-dup"), ("pending_drop", "t2-dup"), ("helpers", "t2-dup")] and "standby / strands went with it: t2-dup-sb" in said[-1][0]
