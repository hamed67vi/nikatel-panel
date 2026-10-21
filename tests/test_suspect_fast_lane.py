"""A tunnel that is "up" but failed its newest check is confirmed after ~8 s, not at the next turn of the slow cycle (the owner, 2026-10-21: the tunnel to the US was cut and the panel did not call it cut for about
two minutes: the slow cycle found the first failed check, then waited a whole minute for the second one that CONFIRM_CHECKS asks for)."""
import itertools
import time

from nikatel.db import session
from nikatel.models import Server, Tunnel, TunnelCheck
from nikatel.services import tunnels

HOSTS = itertools.count(100)


def _tunnel(tag, state="up", last_check_ago=30, enabled=True):
    with session() as db:
        n = next(HOSTS)
        ir, fo = Server(name=f"ir-{tag}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fo-{tag}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fo])
        db.flush()
        t = Tunnel(name=f"t-{tag}", type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fo.id, state=state, enabled=enabled, forwarded_ports=[8075],
                   last_check_at=int(time.time()) - last_check_ago)
        db.add(t)
        db.flush()
        return t.id


def _checks(tid, oks, ago=30):
    now = int(time.time())

    with session() as db:
        for i, ok in enumerate(oks):          # oks: oldest first
            db.add(TunnelCheck(tunnel_id=tid, ts=now - ago - (len(oks) - 1 - i) * 5, ok=ok, error="" if ok else "no data"))


def test_an_up_tunnel_whose_newest_check_failed_is_checked_again_in_the_fast_lane(monkeypatch):
    bad = _tunnel("s1")
    _checks(bad, [True, True, False])
    good = _tunnel("s2")
    _checks(good, [False, True])                                          # (it failed before, but the newest check passed)
    recent = _tunnel("s3", last_check_ago=2)
    _checks(recent, [False], ago=2)                                       # (looked at 2 s ago: not again yet)
    old = _tunnel("s4")
    _checks(old, [False], ago=400)                                        # (a failure of long ago: the slow cycle owns it)
    off = _tunnel("s5", enabled=False)
    _checks(off, [False])
    never = _tunnel("s6")                                                 # (no checks at all)
    assert tunnels.suspected(int(time.time())) == [bad]
    seen = []
    monkeypatch.setattr(tunnels, "check_tunnel", lambda tid, fresh_ext=False, quick=False: seen.append((tid, fresh_ext)))
    tunnels.check_unhealthy()
    assert (bad, True) in seen and all(i not in (good, recent, old, off, never) for i, _ in seen)


def test_a_tunnel_that_is_down_is_not_looked_at_twice(monkeypatch):
    down = _tunnel("s7", state="down")
    _checks(down, [False])
    seen = []
    monkeypatch.setattr(tunnels, "check_tunnel", lambda tid, fresh_ext=False, quick=False: seen.append(tid))
    tunnels.check_unhealthy()
    assert seen.count(down) == 1 and down not in tunnels.suspected(int(time.time()))
