"""The sweep of duplicate tunnels (1.69.212; the owner, 2026-11-04: "duplicate or extra tunnels must be looked at and must not stay for ever - a temporary or a test one is fine, an extra one is not")."""
import time

from sqlalchemy import select
from test_failover import _servers_and_tunnels

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Tunnel, TunnelCheck
from nikatel.services import autopilot, builder, janitor
from nikatel.services import settings as st

HOUR = 3600


def row(i, name, foreign=1, ports=(80,), state="up"):
    return {"id": i, "name": name, "foreign_id": foreign, "ports": list(ports), "state": state}


# ------------------------------------------------------------------ the pure parts
def test_tunnels_to_the_same_foreign_server_with_the_same_ports_are_a_group():
    rows = [row(1, "a"), row(2, "b"), row(3, "c", ports=(81,)), row(4, "d", foreign=2), row(5, "e", ports=())]
    assert [[t["name"] for t in g] for g in janitor.duplicate_groups(rows)] == [["a", "b"]]        # (other ports, another foreign server, no ports: not duplicates)
    assert janitor.duplicate_groups([row(1, "a"), row(2, "b", ports=(80, 80))]) == []              # (a port written twice is not the same set)


G = [row(1, "keep"), row(2, "extra")]
OLD = {1: 4 * HOUR, 2: 4 * HOUR}


def test_the_busiest_stays_and_a_quiet_old_copy_that_no_dns_record_is_tied_to_is_extra():
    keeper, extras, why = janitor.pick_extras(G, {1: 43.0, 2: 0.2}, OLD, {1: True, 2: False})
    assert keeper["name"] == "keep" and [t["name"] for t in extras] == ["extra"] and why == ""


def test_nothing_goes_when_it_is_used_young_not_measured_tied_to_a_record_or_unproved():
    ok_ref = {1: True, 2: False}
    assert janitor.pick_extras(G, {1: 43.0, 2: 5.0}, OLD, ok_ref)[1] == []                                                 # used
    assert janitor.pick_extras(G, {1: 43.0, 2: 0.2}, {1: 4 * HOUR, 2: 600}, ok_ref)[1] == []                                # younger than the window
    assert janitor.pick_extras(G, {1: 43.0, 2: None}, OLD, ok_ref)[1] == []                                                 # not measured enough: unknown, not quiet
    assert janitor.pick_extras(G, {1: 43.0, 2: 0.2}, OLD, {1: True, 2: True})[1] == []                                      # a DNS record is tied to it
    assert janitor.pick_extras(G, {1: 43.0, 2: 0.2}, OLD, {1: True, 2: None})[1] == []                                      # the records could not be asked
    keeper, extras, _why = janitor.pick_extras([row(1, "keep", state="down"), row(2, "extra")], {1: 43.0, 2: 0.2}, OLD, ok_ref)
    assert keeper["name"] == "extra" and extras == []                                                                       # (the only one that is up stays, whatever the other carried)
    assert janitor.pick_extras([row(1, "keep", state="down"), row(2, "extra", state="down")], {1: 1.0, 2: 1.0}, OLD, ok_ref)[0] is None


def test_two_idle_copies_are_not_told_apart_unless_a_record_proves_the_one_that_stays():
    use = {1: 0.1, 2: 0.2}
    keeper, extras, why = janitor.pick_extras(G, use, OLD, {1: False, 2: None})
    assert keeper["id"] == 2 and extras == [] and "both" in why and "idle" in why
    keeper, extras, why = janitor.pick_extras(G, use, OLD, {1: False, 2: True})
    assert [t["id"] for t in extras] == [1]                                                                                 # (the record points at the other one)


def test_the_rails_one_removal_every_half_hour_and_three_a_day():
    now = 1_000_000
    assert janitor.rails([], now) == ""
    assert "minutes ago" in janitor.rails([now - 60], now) and janitor.rails([now - janitor.JANITOR_GAP - 1], now) == ""
    assert "3 extra tunnels" in janitor.rails([now - 4000, now - 9000, now - 20000], now)
    assert janitor.rails([now - 90000, now - 95000, now - 99000], now) == ""                                                # (older than a day: forgotten)


# ------------------------------------------------------------------ the job
def _world(monkeypatch, mode="fix", extra_referenced=False):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(janitor.jobs, "stopping", lambda: False)
    monkeypatch.setattr(janitor, "_TOLD", {})
    st.put("repair", {"mode": mode})
    st.put("janitor", {"done": []})
    ids = _servers_and_tunnels()
    now = int(time.time())

    with session() as db:
        old = now - 5 * HOUR
        t2 = db.get(Tunnel, ids["t2"])
        t2.created_at = old
        dup = Tunnel(name="t2-dup", type="backhaul", transport="tcpmux", iran_server_id=ids["ir2"], foreign_server_id=ids["fi1"], state="up", forwarded_ports=[8075], created_at=old)
        db.add(dup)
        db.flush()

        for tid, rx, tx in ((t2.id, 20.0, 3.0), (dup.id, 0.1, 0.0)):
            db.add_all([TunnelCheck(tunnel_id=tid, ts=now - i * 60, ok=True, rx_mbps=rx, tx_mbps=tx) for i in range(int(3.2 * 60))])

    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn.__name__, a)) or 1)
    monkeypatch.setattr(janitor, "_referenced", lambda r: True if r["name"] == "t2" else extra_referenced)
    return ids, started


def test_a_quiet_copy_nobody_is_tied_to_is_removed_by_the_job_one_at_a_time(monkeypatch):
    _ids, started = _world(monkeypatch)
    janitor.run()
    assert [(s[0], s[1]) for s in started] == [("t2-dup", "remove_extra")] and started[0][2][1] == "t2" and st.get("janitor")["done"]
    janitor.run()
    assert len(started) == 1                                                                                                # (the gap between two removals)


def test_nothing_is_removed_in_the_mode_off_in_a_storm_or_while_a_dns_record_is_tied_to_it(monkeypatch):
    _ids, started = _world(monkeypatch, mode="off")
    janitor.run()
    assert started == []
    st.put("repair", {"mode": "fix"})
    monkeypatch.setattr(autopilot, "storm_now", lambda: True)
    janitor.run()
    assert started == []
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(janitor, "_referenced", lambda r: True)                                                             # (both tied to records: both are used)
    janitor.run()
    assert started == []
    monkeypatch.setattr(janitor, "_referenced", lambda r: None)                                                             # (no DNS account to ask: nothing proves it)
    janitor.run()
    assert started == []


def test_the_removal_looks_again_and_puts_the_tunnel_in_recently_removed(monkeypatch):
    _world(monkeypatch)
    said, deleted = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append((line, done, ok)))
    monkeypatch.setattr(builder, "delete_tunnel", lambda tid, **kw: deleted.append((tid, kw)) or ["Iran: removed", "foreign: removed"])

    with session() as db:
        dup = db.scalar(select(Tunnel.id).where(Tunnel.name == "t2-dup"))

    janitor.remove_extra(5, dup, "t2", 0.1)
    assert deleted == [(dup, {"trash": True})] and said[-1][1:] == (True, True) and "Recently removed" in said[-1][0]
    deleted.clear()
    monkeypatch.setattr(janitor, "_use", lambda tid, now: 6.0)                                                              # (somebody uses it now)
    janitor.remove_extra(5, dup, "t2", 0.1)
    assert deleted == [] and said[-1][1:] == (True, False) and "it stays" in said[-1][0]


def test_the_job_is_registered_and_the_module_is_in_the_architecture():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert 'jobs.register("janitor", janitor.EVERY, janitor.run)' in (root / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert "janitor.py" in (root / "ARCHITECTURE.md").read_text(encoding="utf-8")
