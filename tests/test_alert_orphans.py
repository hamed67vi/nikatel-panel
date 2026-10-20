"""The alert state of a thing that is gone (the audit of 2026-10-21; the owner: "the alert state of deleted servers and tunnels is never cleared - fix it"): the state (`alert_states`) of a tunnel, server, config, panel or
node that no longer exists is dropped after a week - not at once, because a tunnel that a repair rebuilds is deleted and made again under the same name on purpose and its "UP again" message needs its state."""
import pytest
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import AlertState, Config, Panel, Server, Tunnel
from nikatel.services import alerts
from nikatel.services import settings as st

DAY = 86400
T0 = 1_800_000_000


def _state(kind, ref, alerted=True, fails=3):
    with session() as db:
        db.add(AlertState(kind=kind, ref=ref, fails=fails, alerted=alerted, bad_since=T0 - 50, good_since=0, last_notice=0, flips=0, flap_since=0, quiet_until=0))


def _pair():
    with session() as db:
        a = Server(name="ir-x", host="203.0.113.1", role="iran")
        b = Server(name="fr-x", host="203.0.113.2", role="foreign")
        db.add_all([a, b])
        db.flush()
        return a.id, b.id


def _refs():
    with session() as db:
        return {(a.kind, a.ref) for a in db.scalars(select(AlertState))}


@pytest.fixture(autouse=True)
def _fresh(client):
    alerts._PRUNED["at"] = 0
    yield
    alerts._PRUNED["at"] = 0


def test_the_state_of_a_gone_tunnel_is_kept_for_a_week_and_then_dropped():
    _state("tunnel", "ghost-1")
    assert alerts.prune_orphans(T0) == 0 and ("tunnel", "ghost-1") in _refs()                          # the first sweep that sees it missing starts the clock
    assert st.get("alert_orphans")["since"] == {"tunnel|ghost-1": T0}
    assert alerts.prune_orphans(T0 + 7 * DAY - 1) == 0 and ("tunnel", "ghost-1") in _refs()            # a repair that rebuilds a tunnel has all this time to bring it back
    assert alerts.prune_orphans(T0 + 7 * DAY) == 1 and ("tunnel", "ghost-1") not in _refs()
    assert st.get("alert_orphans")["since"] == {}                                                       # (and the clock of a dropped state is gone with it)


def test_a_thing_that_is_made_again_before_the_week_keeps_its_state_and_the_clock_starts_over_next_time():
    ia, fa = _pair()
    _state("tunnel", "rebuilt-1", alerted=True)
    alerts.prune_orphans(T0)
    assert "tunnel|rebuilt-1" in st.get("alert_orphans")["since"]

    with session() as db:
        db.add(Tunnel(name="rebuilt-1", type="gre", iran_server_id=ia, foreign_server_id=fa, enabled=True))

    assert alerts.prune_orphans(T0 + 3 * DAY) == 0
    assert st.get("alert_orphans")["since"] == {}                                                       # back: forgotten
    assert ("tunnel", "rebuilt-1") in _refs()

    with session() as db:
        db.delete(db.scalar(select(Tunnel).where(Tunnel.name == "rebuilt-1")))

    assert alerts.prune_orphans(T0 + 8 * DAY) == 0                                                      # missing again: a NEW clock (not the old one, which was forgotten)
    assert alerts.prune_orphans(T0 + 15 * DAY) == 1


def test_a_thing_that_exists_is_never_dropped_even_when_it_is_switched_off():
    ia, fa = _pair()

    with session() as db:
        db.add(Tunnel(name="t-off", type="gre", iran_server_id=ia, foreign_server_id=fa, enabled=False))
        db.add(Server(name="s-off", host="203.0.113.9", role="foreign", enabled=False))
        db.add(Config(name="c-off", enabled=False))
        db.add(Panel(name="p-off", type="pasarguard", url="https://p-off.example.test", enabled=False))

    for kind, ref in [("tunnel", "t-off"), ("server", "s-off"), ("config", "c-off"), ("panel", "p-off"), ("node", "p-off/node-1")]:
        _state(kind, ref)

    alerts.prune_orphans(T0)
    assert alerts.prune_orphans(T0 + 30 * DAY) == 0
    assert len(_refs()) == 5 and st.get("alert_orphans")["since"] == {}


def test_the_nodes_of_a_panel_go_with_the_panel_only():
    with session() as db:
        db.add(Panel(name="p-live", type="pasarguard", url="https://p-live.example.test", enabled=True))

    _state("node", "p-live/node-gone")            # the panel does not list this node any more: a node comes and goes with the panel's reading, it stays
    _state("node", "p-dead/node-1")               # its panel is gone
    alerts.prune_orphans(T0)
    assert alerts.prune_orphans(T0 + 8 * DAY) == 1
    assert _refs() == {("node", "p-live/node-gone")}


def test_a_kind_the_sweep_does_not_know_is_left_alone():
    _state("mystery", "x")
    alerts.prune_orphans(T0)
    assert alerts.prune_orphans(T0 + 90 * DAY) == 0 and ("mystery", "x") in _refs()


def test_the_job_sweeps_at_most_every_ten_minutes_and_a_failure_of_the_sweep_does_not_stop_it(monkeypatch):
    calls = []
    monkeypatch.setattr(alerts, "prune_orphans", lambda now=None: calls.append(now) or 0)
    alerts.run(T0)
    alerts.run(T0 + 5)
    alerts.run(T0 + alerts.PRUNE_EVERY - 1)
    assert calls == [T0]
    alerts.run(T0 + alerts.PRUNE_EVERY)
    assert calls == [T0, T0 + alerts.PRUNE_EVERY]

    def boom(now=None):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(alerts, "prune_orphans", boom)
    alerts.run(T0 + 2 * alerts.PRUNE_EVERY)            # the looks of the alerts go on