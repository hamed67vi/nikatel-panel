"""(1.69.292) The Doctor looks at the Iran server FIRST (the owner, 2026-11-21: "if the Iran server is not on again within a minute, move the tunnels to another Iran server - do not test tunnels against a dead server; and even
when its whole international line is closed the Doctor must tell correctly whether the server is on"): an Iran server that is ON but whose own network is cut off counts as dead for the tunnels that stand on it."""
import time

from sqlalchemy import select
from test_iran_only import _cut_world

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import actions, autopilot, failover, netwatch, tunnels
from nikatel.services import settings as st

NOW = 1_800_000_000


def net(at=None, foreign=100.0, level="down", newest="down", anchor=100.0):
    """What the round of five minutes (or a quick look) keeps in `extra["net"]` of an Iran server."""
    return {"at": int(time.time()) if at is None else at, "anchor": anchor, "domestic": 0.0, "foreign": foreign, "level": level, "now": newest, "streak": {"level": level, "n": 1}}


def _put_net(sid, value):
    with session() as db:
        srv = db.get(Server, sid)
        srv.extra = {"net": value} if value is not None else {}


# ------------------------------------------------------------------ the verdict
def test_a_look_is_a_cut_only_when_it_is_down_young_and_the_foreign_servers_were_lost_too():
    assert netwatch.is_netcut(net(at=NOW), NOW) is True
    assert netwatch.is_netcut(net(at=NOW - 240), NOW) is True and netwatch.is_netcut(net(at=NOW - 241), NOW) is False          # (four minutes, then it is an old look)
    assert netwatch.is_netcut(net(at=NOW, foreign=None), NOW) is False                                                         # (nothing to tell a cut from a filtered ping)
    assert netwatch.is_netcut(net(at=NOW, foreign=49.9), NOW) is False and netwatch.is_netcut(net(at=NOW, foreign=50.0), NOW) is True
    assert netwatch.is_netcut(net(at=NOW, level="down", newest="ok"), NOW) is False                                           # (its newest look says it is back)
    assert netwatch.is_netcut(net(at=NOW, level="bad", newest="bad"), NOW) is False and netwatch.is_netcut(net(at=NOW, level="ok", newest="down"), NOW) is True
    assert netwatch.is_netcut({}, NOW) is False and netwatch.is_netcut(None, NOW) is False


def test_a_quick_look_pings_the_anchors_two_iran_servers_and_three_foreign_servers_the_folders_allow():
    iran, foreign = [(1, "i1"), (2, "i2"), (3, "i3"), (4, "i4")], [(10, "f1"), (11, "f2"), (12, "f3"), (13, "f4")]
    t = netwatch._targets(1, iran, foreign, lambda sid, other: other not in (3, 11))
    assert t["domestic"] == ["i2", "i4"] and t["foreign"] == ["f1", "f3", "f4"] and t["anchor"] == list(netwatch.ANCHORS)
    assert netwatch._targets(2, iran, foreign, lambda sid, other: True)["domestic"] == ["i1", "i3"]                            # (never itself)


# ------------------------------------------------------------------ the quick look
def _look_world():
    with session() as db:
        a, b, f = Server(name="ir1", host="203.0.113.11", role="iran", online=True), Server(name="ir2", host="203.0.113.12", role="iran", online=True), Server(name="fx", host="203.0.113.13", role="foreign", online=True)
        db.add_all([a, b, f])
        db.flush()
        return a.id


def _result(sid, **kw):
    r = {"id": sid, "name": "ir1", "anchor": 100.0, "domestic": 0.0, "foreign": 100.0, "level": "down", "foreign_raw": {}}
    r.update(kw)
    return r


def test_a_quick_look_keeps_the_result_tells_the_events_that_the_server_is_on_and_waits_between_looks(monkeypatch):
    sid = _look_world()
    seen = []
    monkeypatch.setattr(netwatch, "probe_one", lambda item: seen.append(item) or _result(sid))
    assert netwatch.quick_look(sid, now=1000.0) is True
    assert len(seen) == 1 and seen[0][0] == sid and seen[0][3]["domestic"] == ["203.0.113.12"] and seen[0][3]["foreign"] == ["203.0.113.13"]

    with session() as db:
        extra = db.get(Server, sid).extra
        assert extra["net"]["level"] == "down" and extra["net"]["foreign"] == 100.0
        text = [e.text for e in db.scalars(select(Event).where(Event.ref == "ir1"))]
        assert any("is on, but its network is cut off" in x and "moves them to another Iran server" in x for x in text)

    assert netwatch.quick_look(sid, now=1010.0) is False and len(seen) == 1                                                    # (one look per QUICK_GAP seconds)
    assert netwatch.quick_look(sid, now=1000.0 + netwatch.QUICK_GAP) is True and len(seen) == 2


def test_a_server_that_is_not_on_or_cannot_be_asked_is_not_looked_at_and_nothing_is_kept(monkeypatch):
    sid = _look_world()
    monkeypatch.setattr(netwatch, "probe_one", lambda item: {"id": item[0], "name": item[1], "error": "ssh: timeout"})
    assert netwatch.quick_look(sid, now=2000.0) is False
    _put_net(sid, None)

    with session() as db:
        assert (db.get(Server, sid).extra or {}).get("net") is None

    with session() as db:
        db.get(Server, sid).online = False

    asked = []
    monkeypatch.setattr(netwatch, "probe_one", lambda item: asked.append(item) or _result(sid))
    assert netwatch.quick_look(sid, now=5000.0) is False and asked == []                                                       # (the pulse says whether it is on)


def test_the_suspects_are_the_servers_with_a_tunnel_that_has_been_down_for_a_few_seconds(monkeypatch):
    sid = _look_world()
    looked = []
    monkeypatch.setattr(netwatch, "quick_look", lambda s, now=None: looked.append(s))
    now = int(time.time())

    with session() as db:
        f = db.scalar(select(Server).where(Server.name == "fx"))
        db.add_all([Tunnel(name="t1", type="backhaul", transport="tcpmux", iran_server_id=sid, foreign_server_id=f.id, state="down", state_since=now - 3),
                    Tunnel(name="t2", type="backhaul", transport="tcpmux", iran_server_id=sid, foreign_server_id=f.id, state="up", state_since=now - 500)])

    netwatch.look_suspects(now)
    assert looked == []                                                                                                       # (down for 3 seconds: a blink)

    with session() as db:
        db.scalar(select(Tunnel).where(Tunnel.name == "t1")).state_since = now - 30

    netwatch.look_suspects(now)
    assert looked == [sid]


# ------------------------------------------------------------------ the guard every repair step asks
def test_an_iran_server_cut_off_in_itself_is_a_server_that_is_off_for_the_repair_guard():
    sid = _look_world()
    assert tunnels.servers_off_of(sid, 0) == []
    _put_net(sid, net())
    off = tunnels.servers_off_of(sid, 0)
    assert len(off) == 1 and "Iran server ir1 is on, but its own network is cut off" in off[0]
    assert "nothing is done to this tunnel until it is back" in autopilot.off_note(off)
    _put_net(sid, net(at=int(time.time()) - 300))                                                                              # (an old look: no guard)
    assert tunnels.servers_off_of(sid, 0) == []
    _put_net(sid, net())

    with session() as db:
        db.get(Server, sid).online = False

    assert "is off or does not answer" in tunnels.servers_off_of(sid, 0)[0]                                                    # (off stays what it was)


def test_the_guard_sees_it_for_a_tunnel_row_too():
    sid = _look_world()
    _put_net(sid, net())

    with session() as db:
        f = db.scalar(select(Server).where(Server.name == "fx"))
        db.add(Tunnel(name="t1", type="backhaul", transport="tcpmux", iran_server_id=sid, foreign_server_id=f.id, state="down"))

    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == "t1"))
        assert tunnels.servers_off(t) and "own network is cut off" in tunnels.servers_off(t)[0]


# ------------------------------------------------------------------ the move after one minute
def test_an_iran_server_cut_off_in_itself_whose_tunnel_is_down_is_replaced_after_a_minute(monkeypatch):
    sid, ran = _cut_world(monkeypatch, note="")
    _put_net(sid, net())
    failover.check()
    assert str(sid) in st.get("failover")["seen"] and ran == []
    st.put("failover", {"seen": {str(sid): int(time.time()) - 40}})
    failover.check()
    assert ran == []                                                                                                          # forty seconds: not yet
    st.put("failover", {"seen": {str(sid): int(time.time()) - 65}})
    failover.check()
    assert len(ran) == 1 and ran[0]["server_id"] == sid and ran[0]["why"] == failover.NETCUT_WHY
    failover.check()
    assert len(ran) == 1                                                                                                      # not twice


def test_a_cut_look_that_is_old_or_without_foreign_servers_or_with_the_tunnels_up_moves_nothing(monkeypatch):
    sid, ran = _cut_world(monkeypatch, note="")

    for value in (net(at=int(time.time()) - 400), net(foreign=None), net(level="ok", newest="ok", anchor=0.0, foreign=0.0)):
        _put_net(sid, value)
        st.put("failover", {"seen": {str(sid): 1}})
        failover.check()
        assert ran == []

    _put_net(sid, net())

    with session() as db:
        for t in db.scalars(select(Tunnel)):
            t.state = "up"

    st.put("failover", {"seen": {str(sid): 1}})
    failover.check()
    assert ran == []                                                                                                          # (its tunnels carry: nothing to move)


def test_when_most_iran_servers_are_cut_off_together_it_is_the_line_of_the_country_and_nothing_moves(monkeypatch):
    sid, ran = _cut_world(monkeypatch, note="")

    with session() as db:
        f = db.scalar(select(Server).where(Server.name == "fx"))
        others = [Server(name=f"ir{n}", host=f"203.0.113.{90 + n}", role="iran", online=True, extra={"net": net()}) for n in range(1, 3)]
        db.add_all(others)
        db.flush()
        db.add_all([Tunnel(name=f"x{o.id}", type="backhaul", transport="tcpmux", iran_server_id=o.id, foreign_server_id=f.id, state="down", forwarded_ports=[90 + i]) for i, o in enumerate(others)])

    _put_net(sid, net())
    st.put("failover", {"seen": {str(sid): 1}})
    failover.check()
    assert ran == []                                                                                                          # (three of three: no one to move to)


def test_a_server_cut_off_in_itself_is_not_counted_as_back_and_not_a_source_that_can_wait_for_old_tunnels(monkeypatch):
    from nikatel.services import doctor

    sid, _ran = _cut_world(monkeypatch, note="")
    _put_net(sid, net())
    s = doctor._state()
    doctor._track_back(s, int(time.time()))
    assert str(sid) not in s["back"]

    with session() as db:
        assert failover.is_netcut(db.get(Server, sid)) is True and failover.is_cut(db.get(Server, sid)) is False


# ------------------------------------------------------------------ the first step for a tunnel that is down
def _repair_world(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    sid = _look_world()

    with session() as db:
        f = db.scalar(select(Server).where(Server.name == "fx"))
        db.add(Tunnel(name="t1", type="backhaul", transport="tcpmux", iran_server_id=sid, foreign_server_id=f.id, state="down", state_since=int(time.time()) - 120, forwarded_ports=[80]))

    fixed, looked = [], []
    monkeypatch.setattr(actions, "fix", lambda tid, source="manual": fixed.append(tid))
    return sid, fixed, looked


def test_the_first_step_for_a_tunnel_that_is_down_is_a_look_at_its_iran_server_and_a_cut_one_gets_no_restart(monkeypatch):
    sid, fixed, looked = _repair_world(monkeypatch)
    monkeypatch.setattr(netwatch, "quick_looks", lambda ids: looked.append(set(ids)) or _put_net(sid, net()))
    actions.auto_repair()
    assert looked == [{sid}] and fixed == []                                                                                   # (it was looked at first; the look said cut: nothing is restarted)


def test_a_server_that_reaches_the_world_gets_its_plain_restart_as_before(monkeypatch):
    sid, fixed, looked = _repair_world(monkeypatch)
    monkeypatch.setattr(netwatch, "quick_looks", lambda ids: looked.append(set(ids)) or _put_net(sid, net(level="ok", newest="ok", anchor=0.0, foreign=0.0)))
    actions.auto_repair()
    assert looked == [{sid}] and len(fixed) == 1
