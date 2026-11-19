"""A tunnel whose type has no traffic counters (chisel, BackPack wss / wssmux / stealth / tcpmux: 12 of 26 tunnels of the owner's panel) used to lose every duplicate group to the OLD tunnel (the owner, 2026-11-19, «بله، بساز»): the sweep's
keeper is the busiest tunnel that is up AND measured, so the only measurable one - the old tunnel a move had kept because users still held it - was the keeper, the sweep pointed the DNS name the move had changed BACK at it (2026-11-18 04:34,
the Doctor's own move undone), and the new tunnel was neither keeper nor extra ("not measured enough") for good, still called -mv. Now a tunnel that cannot be measured but that the DNS points at is the keeper, no name is ever pointed away from
a tunnel whose use is not known, and the old tunnel goes when it is quiet."""
import time

from test_failover import _servers_and_tunnels
from test_janitor import HOUR, OLD, G, row

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Tunnel, TunnelCheck
from nikatel.services import autopilot, failover, janitor
from nikatel.services import settings as st

NEW_OLD = [row(1, "new"), row(2, "old")]            # (`new`: no counters; `old`: the one a move kept)


# ------------------------------------------------------------------ the pure parts
def test_a_tunnel_without_counters_that_the_dns_points_at_is_the_keeper_and_the_old_one_goes_when_quiet():
    keeper, extras, why = janitor.pick_extras(NEW_OLD, {1: None, 2: 24.0}, OLD, {1: True, 2: False})
    assert keeper["name"] == "new" and extras == [] and "24.0 Mb/s" in why and "used" in why                  # (the old one still carries users: nothing goes yet)
    keeper, extras, why = janitor.pick_extras(NEW_OLD, {1: None, 2: 0.4}, OLD, {1: True, 2: False})
    assert keeper["name"] == "new" and [t["name"] for t in extras] == ["old"] and why == ""                   # (quiet, old enough, no name tied to it: extra)
    assert janitor.pick_extras(NEW_OLD, {1: None, 2: 0.4}, {1: 4 * HOUR, 2: 600}, {1: True, 2: False})[1] == []   # (a young old tunnel stays)
    assert janitor.pick_extras(NEW_OLD, {1: None, 2: 0.4}, OLD, {1: True, 2: True})[1] == []                  # (a name is tied to it)


def test_the_old_rule_stays_for_everything_else():
    assert janitor.pick_extras(G, {1: 43.0, 2: 0.2}, OLD, {1: True, 2: False})[0]["name"] == "keep"
    keeper, extras, why = janitor.pick_extras(NEW_OLD, {1: None, 2: 5.0}, OLD, {1: False, 2: True})           # (the DNS points at the measured one: it stays, the blind one is not judged)
    assert keeper["name"] == "old" and extras == [] and "not measured" in why
    assert janitor.pick_extras(NEW_OLD, {1: None, 2: 5.0}, OLD, {1: True, 2: True})[0]["name"] == "old"         # (both tied: the measured one, as before)
    assert janitor.pick_extras([row(1, "new", state="down"), row(2, "old")], {1: None, 2: 5.0}, OLD, {1: True, 2: False})[0]["name"] == "old"      # (a blind tunnel that is down is no keeper)
    assert janitor.pick_extras(NEW_OLD, {1: None, 2: None}, OLD, {1: False, 2: False}) == (None, [], "no tunnel of the group is up and measured")
    keeper, extras, why = janitor.pick_extras(NEW_OLD, {1: None, 2: None}, {1: 5 * HOUR, 2: 10 * HOUR}, {1: True, 2: True})
    assert keeper["name"] == "new" and extras == [] and "not measured" in why                                   # (two blind ones the DNS points at: the younger, nothing goes)


def test_no_name_is_pointed_away_from_a_tunnel_whose_use_is_not_known():
    ref, now = {1: True, 2: True}, 10_000_000
    assert janitor.consolidation_target(G, G[0], OLD, ref, {}, now) == G[1]                                   # (no use given: as before)
    assert janitor.consolidation_target(G, G[0], OLD, ref, {}, now, {1: 9.0, 2: 2.0}) == G[1]                 # (all measured: as before)
    assert janitor.consolidation_target(G, G[0], OLD, ref, {}, now, {1: 9.0, 2: None}) is None                # (the copy cannot be measured)
    assert janitor.consolidation_target(G, G[0], OLD, ref, {}, now, {1: None, 2: 2.0}) is None                # (the keeper cannot be measured)


# ------------------------------------------------------------------ the job: the move of 2026-11-18
def _world(monkeypatch, old_use=(20.0, 3.0)):
    """`t2` is the OLD tunnel (measured, no name tied to it), `t2-dup` the NEW one (no counters, the DNS name points at it)."""
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(janitor.jobs, "stopping", lambda: False)
    monkeypatch.setattr(janitor, "_TOLD", {})
    st.put("repair", {"mode": "move"})
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
        db.add_all([TunnelCheck(tunnel_id=t2.id, ts=now - i * 60, ok=True, rx_mbps=old_use[0], tx_mbps=old_use[1]) for i in range(int(3.2 * 60))])
        db.add_all([TunnelCheck(tunnel_id=dup.id, ts=now - i * 60, ok=True, rx_mbps=None, tx_mbps=None) for i in range(int(3.2 * 60))])

    started, asked = [], []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn.__name__, a)) or 1)
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(janitor, "_referenced", lambda r: r["name"] == "t2-dup")
    monkeypatch.setattr(failover, "replace_plan", lambda tid, iran_id: asked.append((tid, iran_id)) or {"dns": [{"fqdn": "x.example.test"}], "left_alone": [], "dns_error": ""})
    return ids, started, asked


def test_the_job_does_not_point_the_names_back_at_the_old_tunnel_nor_remove_it_while_it_is_used(monkeypatch):
    _ids, started, asked = _world(monkeypatch)
    janitor.run()
    janitor.run()
    assert started == [] and asked == [] and st.get("janitor").get("pointed") == {}                          # (before: the names went back to the old tunnel at the first look)


def test_the_job_removes_the_old_tunnel_when_it_is_quiet_and_the_dns_points_at_the_new_one(monkeypatch):
    _ids, started, asked = _world(monkeypatch, old_use=(0.1, 0.0))
    janitor.run()
    assert [(s[0], s[1]) for s in started] == [("t2", "remove_extra")] and started[0][2][1] == "t2-dup" and asked == []
