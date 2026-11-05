"""An Iran server that is on but cut from abroad (1.69.211; the owner, 2026-11-04: "if an Iran server became cut (اکسز), put a tag on it", "when the routes of the Iran servers are closed the Doctor must replace one quickly - uptime matters to us"):
the row says "Iran only", the Doctor moves its tunnels after ninety seconds when they are down, and a tunnel only moves to an Iran server of its own folder."""
import time

from sqlalchemy import select
from test_failover import _servers_and_tunnels
from test_ui_regression_walk import APP_JS, LAB_JS, _function, _line, _node, needs_node

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Profile, Server, Tunnel
from nikatel.services import autopilot, doctor, failover
from nikatel.services import settings as st
from nikatel.services.pulse import IRAN_ONLY

CUT = IRAN_ONLY + " (seen from Iran by iran-b, iran-c)"

TAG_HARNESS = r"""
globalThis.window = {};
const noLogin = () => false;
globalThis.state = {serversCache: [], tunnelsCache: []};
%(src)s
const base = {id: 1, name: "ir-a", enabled: true, online: true, facts: {}, extra: {}};
const names = s => labIssues(s).map(i => i.txt);
console.log(JSON.stringify({cut: names({...base, last_error: %(cut)s}), fine: names(base), off: names({...base, online: false, last_error: "no answer on the SSH port 22"}), ssh: names({...base, last_error: "SSH does not answer: timeout"})}));
"""


@needs_node
def test_a_server_that_is_on_but_cut_from_abroad_wears_the_tag_iran_only():
    import json

    src = "\n".join([_line(APP_JS, "const srvBusy = s =>"), _line(LAB_JS, "const labPeerRebooting = "), _function(LAB_JS, "function labIssues(s) {")])
    r = _node(TAG_HARNESS.replace("%(src)s", src).replace("%(cut)s", json.dumps(CUT)))
    assert r["cut"] == ["Iran only"] and r["fine"] == [] and r["off"] == ["Offline"] and r["ssh"] == ["SSH ✕"]
    assert 'add("bad", "Iran only"' in LAB_JS and "only Iran sees it" in LAB_JS


# ------------------------------------------------------------------ the quick move
def _cut_world(monkeypatch, tunnel_state="down", note=CUT, online=True):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    st.put("repair", {"mode": "move", "server_minutes": 5})

    with session() as db:
        s, f = Server(name="ir9", host="203.0.113.77", role="iran", online=online, last_error=note), Server(name="fx", host="203.0.113.78", role="foreign", online=True)
        db.add_all([s, f])
        db.flush()
        db.add_all([Tunnel(name="t9", type="backhaul", transport="tcpmux", iran_server_id=s.id, foreign_server_id=f.id, state=tunnel_state, forwarded_ports=[80]),
                    Tunnel(name="t10", type="backhaul", transport="tcpmux", iran_server_id=s.id, foreign_server_id=f.id, state=tunnel_state, forwarded_ports=[81])])
        sid = s.id

    ran = []
    monkeypatch.setattr(failover, "plan_dead", lambda server_id: {"server_id": server_id, "server": "ir9", "ip": "203.0.113.77", "reason": "down", "why": "the server does not answer", "tunnels": [{"id": 1, "name": "t9"}], "dns": [],
                                                                  "target": {"id": 2, "name": "other", "ip": "203.0.113.3"}, "problems": []})
    monkeypatch.setattr(failover, "run_plan", lambda p: ran.append(p) or 1)
    return sid, ran


def test_an_iran_server_cut_from_abroad_whose_tunnels_are_down_is_replaced_after_ninety_seconds(monkeypatch):
    sid, ran = _cut_world(monkeypatch)
    assert failover.CUT_WAIT == 90
    failover.check()
    assert str(sid) in st.get("failover")["seen"] and ran == []                                              # seen, ninety seconds have not passed
    st.put("failover", {"seen": {str(sid): int(time.time()) - 60}})
    failover.check()
    assert ran == []                                                                                          # a minute: not yet
    st.put("failover", {"seen": {str(sid): int(time.time()) - 100}})
    failover.check()
    assert len(ran) == 1 and ran[0]["server_id"] == sid and "cut from abroad" in ran[0]["why"]               # ninety seconds: replaced, the plan says why
    failover.check()
    assert len(ran) == 1                                                                                      # not twice


def test_an_iran_server_that_is_only_cut_from_the_panel_while_its_tunnels_are_up_is_not_moved(monkeypatch):
    sid, ran = _cut_world(monkeypatch, tunnel_state="up")
    st.put("failover", {"seen": {str(sid): 1}})
    failover.check()
    assert ran == [] and st.get("failover")["seen"] == {}                                                    # (its tunnels carry: the panel's own way in is what is cut)


def test_a_server_that_is_on_without_the_cut_note_or_not_on_by_the_pulse_is_not_taken_for_a_cut_one(monkeypatch):
    sid, ran = _cut_world(monkeypatch, note="SSH does not answer: timeout")
    st.put("failover", {"seen": {str(sid): 1}})
    failover.check()
    assert ran == []
    with session() as db:
        srv = db.get(Server, sid)
        assert failover.is_cut(srv) is False
        srv.last_error = CUT
        assert failover.is_cut(srv) is True
        srv.online = None
        assert failover.is_cut(srv) is False


def test_a_cut_server_is_not_counted_as_back_and_its_tunnels_go_the_dead_server_way(monkeypatch):
    sid, _ran = _cut_world(monkeypatch)
    s = doctor._state()
    doctor._track_back(s, int(time.time()))
    assert str(sid) not in s["back"]                                                                          # (the return clock starts when it is really back)

    with session() as db:
        db.get(Server, sid).last_error = ""

    doctor._track_back(s, int(time.time()))
    assert str(sid) in s["back"]


def test_a_move_from_a_cut_server_builds_nothing_beside_the_old_tunnels_and_remembers_what_it_left(monkeypatch):
    sid, _ran = _cut_world(monkeypatch)
    said, replaced = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "_replace", lambda aid, tid, iran_id, foreign_id, kind, why, skip_offline=False: replaced.append((tid, iran_id, skip_offline)) or True)
    monkeypatch.setattr(failover, "_best_kind", lambda *a, **k: "tcpmux")
    monkeypatch.setattr(failover, "_apply_dns", lambda aid, p: ([], [], []))
    recorded = []
    monkeypatch.setattr(doctor, "record_move", lambda p, moved, left, ok: recorded.append((moved, left, ok)))

    with session() as db:
        tid = db.scalar(select(Tunnel.id).where(Tunnel.name == "t9"))
        other = db.scalar(select(Server.id).where(Server.name == "fx"))

    p = {"id": "c-1", "server_id": sid, "server": "ir9", "ip": "203.0.113.77", "reason": "down", "why": failover.CUT_WHY, "tunnels": [{"id": tid, "name": "t9"}], "dns": [], "left_alone": [], "pulled": [],
         "target": {"id": other, "name": "other", "ip": "203.0.113.3"}, "problems": []}
    failover._work(7, p)
    assert replaced == [(tid, other, True)] and any(x.startswith("Failover of ir9") for x in said)           # (the failover way: no "new one beside the old one" on a server that cannot be reached)
    assert recorded and recorded[0][0] == ["t9"] and recorded[0][1][0]["name"] == "t9" and recorded[0][2] is True      # (what it left on the cut server is remembered, to be purged when it is back)


# ------------------------------------------------------------------ the folders
def test_a_tunnel_is_moved_only_to_an_iran_server_of_its_own_folder(monkeypatch):      # (the owner, 2026-11-04: "the Iran servers used for these must be separate from the ones used for the other tunnels")
    ids = _servers_and_tunnels()
    monkeypatch.setattr(autopilot.detect, "auto_iran", lambda foreign_id, ports: [{"id": ids["ir1"], "name": "ir1", "score": 90}, {"id": ids["ir2"], "name": "ir2", "score": 80}])
    assert [r["id"] for r in autopilot.ranking(ids["t2"])] == [ids["ir2"]]                                   # (no folders yet: every Iran server is one pool, as before)

    with session() as db:
        db.add_all([Profile(kind="iran", server_id=ids["ir1"], tag="Bot"), Profile(kind="iran", server_id=ids["ir2"], tag="Other")])

    assert autopilot.ranking(ids["t2"]) == []                                                                 # (the other server is in another folder)

    with session() as db:
        db.scalar(select(Profile).where(Profile.server_id == ids["ir2"])).tag = "Bot"

    assert [r["id"] for r in autopilot.ranking(ids["t2"])] == [ids["ir2"]]                                   # (the same folder: it may carry it)
    assert autopilot.pool_of(ids["ir1"]) == "Bot" and autopilot.pool_of(999999) == ""
