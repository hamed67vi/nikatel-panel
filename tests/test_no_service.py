"""A tunnel that is UP but leads nowhere (the owner, 2026-10-25: a new tunnel to port 8079 showed "connected" while nothing listened on 8079 on the foreign server - its users got 502): the panel asks the foreign server
whether anything listens on the port the tunnel delivers to, and says "connected, no service" - the state of the tunnel stays what it is, the Doctor does not "repair" it."""
import os
import shutil
import stat
import subprocess
import time

import pytest
from sqlalchemy import select

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import tunnels as tn
from nikatel.services import userq


# ------------------------------------------------------------------ pure
def tunnel(mapping, **rec):
    return Tunnel(name="t", type="backpack", record={"port_mapping": mapping, **rec}, forwarded_ports=[8079])


def test_the_foreign_ports_come_from_the_port_map_the_panel_wrote():
    assert tn.foreign_targets(tunnel("8079:8075")) == [8075]                 # Iran 8079 is delivered to the foreign port 8075
    assert tn.foreign_targets(tunnel("8079:8075,443,443:2096,8075")) == [8075, 443, 2096]
    assert tn.foreign_targets(tunnel("8079=8075")) == [8075]


def test_where_the_panel_does_not_know_it_does_not_judge():
    assert tn.foreign_targets(tunnel("")) == []                              # a tunnel found on the servers: no map the panel can trust
    assert tn.foreign_targets(tunnel("8079:8075", standby_of="us-x")) == []   # a standby follows its tunnel
    assert tn.foreign_targets(tunnel("8079:8075", strand_of="us-x")) == []
    assert tn.foreign_targets(tunnel("80-90")) == [] and tn.foreign_targets(tunnel("abc")) == [] and tn.foreign_targets(tunnel("8079:99999")) == []
    assert tn.foreign_targets(Tunnel(name="t", type="backpack", record=None, forwarded_ports=[1])) == []


def test_the_answer_of_the_foreign_server_is_read():
    s = tn.parse_side("UNIT a.service active\nTLISTEN 8075 1\nTLISTEN 8079 0\nTLISTEN x 1\nNOW 5\n")
    assert s.target_listen == {8075: True, 8079: False} and s.listen == {}


def test_a_missing_port_is_called_no_service_only_after_looks_in_a_row_over_a_minute_and_a_half():
    now = 10_000
    a = tn.no_service({}, [8075], {8075: False}, now)
    assert a["ports"] == [8075] and a["all"] is True and a["fails"] == 1 and a["first"] == now and a["confirmed"] is False

    b = tn.no_service(a, [8075], {8075: False}, now + 8)                     # the fast lane looks every ~8 s: three looks in 16 s are not "confirmed"
    c = tn.no_service(b, [8075], {8075: False}, now + 16)
    assert c["fails"] == 3 and c["confirmed"] is False and c["first"] == now

    d = tn.no_service(c, [8075], {8075: False}, now + 95)
    assert d["confirmed"] is True and d["first"] == now


def test_one_look_that_finds_a_listener_clears_it():
    seen = tn.no_service({"ports": [8075], "all": True, "fails": 5, "first": 1, "confirmed": True, "told": True}, [8075], {8075: True}, 500)
    assert seen == {}


def test_when_nothing_was_asked_what_was_known_stays():
    prev = {"ports": [8075], "all": True, "fails": 2, "first": 1, "confirmed": False}
    assert tn.no_service(prev, [8075], {}, 500) == prev                       # a quick look, a server that did not answer, a tool that is not there
    assert tn.no_service(prev, [], {8075: True}, 500) == prev
    assert tn.no_service({}, [8075], {}, 500) == {}


def test_some_of_the_ports_missing_is_said_so():
    r = tn.no_service({}, [8075, 443], {8075: True, 443: False}, 100)
    assert r["ports"] == [443] and r["all"] is False


def test_the_users_look_names_the_missing_port_before_blaming_what_is_beyond():
    fine = {"state": "up", "n_bad": 1, "known": 1, "bw_state": "ok", "bw_text": "300 Mb/s", "iran": {"name": "ir1", "cpu": 10}, "foreign": {"name": "fr1", "cpu": 10}, "loss": 0.0, "rtt": 80.0, "rtt_ref": 80.0}
    assert userq.explain_users(fine)[0] == "beyond"
    code, text = userq.explain_users({**fine, "no_service": [8075]})
    assert code == "no_service" and "8075" in text and "Ports" in text
    assert userq.explain_users({**fine, "no_service": [8075], "state": "down"})[0] == "tunnel_down"      # a tunnel that is down says that first


# ------------------------------------------------------------------ the script
def _run_side(tmp_path, ss_output, targets):
    """The real text of the script, run by bash with a fake `ss` (and a quiet `systemctl`) first on the PATH."""
    if shutil.which("bash") is None:
        pytest.skip("no bash")

    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)

    for name, body in (("ss", f"#!/bin/sh\ncat <<'EOF'\n{ss_output}EOF\n"), ("systemctl", "#!/bin/sh\necho active\n")):
        p = bindir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    text = ssh._script_text("tunnel_side", {"UNITS": "x.service", "IFACE": "", "PEER": "", "PORTS": "", "TARGETS": targets, "QUICK": ""})
    out = subprocess.run(["bash", "-c", text], check=False, capture_output=True, text=True, timeout=30, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"})
    return tn.parse_side(out.stdout), out.stdout


SS = """tcp   LISTEN 0 4096 127.0.0.1:8075 0.0.0.0:*
tcp   LISTEN 0 128  [::]:22 [::]:*
udp   UNCONN 0 0 *:5353 *:*
tcp   LISTEN 0 511  0.0.0.0:18079 0.0.0.0:*
"""


def test_the_script_says_which_target_ports_have_a_listener(tmp_path):
    side, out = _run_side(tmp_path, SS, "8075 8079 5353")
    assert side.target_listen == {8075: True, 8079: False, 5353: True}, out            # TCP, UDP; 18079 is not 8079
    assert side.listen == {} and side.units == {"x.service": "active"}


def test_the_script_says_nothing_when_ss_says_nothing_or_nothing_is_asked(tmp_path):
    side, out = _run_side(tmp_path, "", "8075")
    assert side.target_listen == {}, out                                                # a tool that is not there never reads as "no service"
    side, out = _run_side(tmp_path, SS, "")
    assert side.target_listen == {} and "TLISTEN" not in out


def test_the_script_takes_only_port_numbers():
    for bad in ("8075;id", "$(id)", "80 75x", "8075\nreboot"):
        with pytest.raises(ValueError):
            ssh._script_text("tunnel_side", {"UNITS": "x.service", "TARGETS": bad})


# ------------------------------------------------------------------ the check
def world(name, mapping="8079:8075", **rec):
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backpack", transport="tcpmux", port=9916, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", state_since=int(time.time()) - 600,
                   forwarded_ports=[8079], record={"port_mapping": mapping, **rec} if mapping else dict(rec), created_at=int(time.time()) - 3600)
        db.add(t)
        db.flush()
        return t.id


def fake_world(monkeypatch, listen):
    """Every unit is active and the users' port answers 404; the foreign server says what `listen` (a dict, mutable: the test changes it between checks) says, or nothing when it is None."""
    asked = []

    def query(node, plan, is_iran, quick=False):
        text = "".join(f"UNIT {u} active\n" for u in (plan.iran_units if is_iran else plan.foreign_units))

        if is_iran:
            text += "DATA 8079 404 0.05\n"
        elif listen is not None:
            asked.append(list(plan.targets))
            text += "".join(f"TLISTEN {p} {1 if listen.get(p) else 0}\n" for p in plan.targets)

        return tn.parse_side(text + "NOW 1000\n")

    monkeypatch.setattr(tn, "_query", query)
    monkeypatch.setattr(tn.jobs, "stopping", lambda: False)
    monkeypatch.setattr(tn.jobs, "trigger", lambda name: None)
    return asked


def detail_of(tid):
    with session() as db:
        t = db.get(Tunnel, tid)
        return dict(t.detail), t.state


def events_of(name):
    with session() as db:
        return [(e.level, e.text) for e in db.scalars(select(Event).where(Event.ref == name).order_by(Event.id))]


def seed(tid, ns):
    with session() as db:
        t = db.get(Tunnel, tid)
        t.detail = {**(t.detail or {}), "no_service": ns}


def test_the_foreign_server_is_asked_for_the_ports_the_tunnel_delivers_to(monkeypatch):
    tid = world("ns1")
    asked = fake_world(monkeypatch, {8075: True})
    tn._check_tunnel(tid)
    d, state = detail_of(tid)
    assert asked == [[8075]] and state == "up" and d["no_service"] is None and events_of("ns1") == []


def test_the_first_look_that_finds_nothing_listening_only_counts(monkeypatch):
    tid = world("ns2")
    fake_world(monkeypatch, {})
    tn._check_tunnel(tid)
    d, state = detail_of(tid)
    assert state == "up" and d["no_service"]["fails"] == 1 and d["no_service"]["confirmed"] is False and events_of("ns2") == []      # one look is not a verdict


def test_a_confirmed_no_service_is_told_once_and_the_tunnel_stays_up(monkeypatch):
    tid = world("ns3")
    fake_world(monkeypatch, {})
    seed(tid, {"ports": [8075], "all": True, "fails": 2, "first": int(time.time()) - 200, "confirmed": False})
    tn._check_tunnel(tid)
    d, state = detail_of(tid)
    assert state == "up" and d["no_service"]["confirmed"] is True and d["no_service"]["told"] is True      # the tunnel itself is fine: no repair, no cut
    ev = events_of("ns3")
    assert len(ev) == 1 and ev[0][0] == "warn" and "connected, but it has no service" in ev[0][1] and "8075" in ev[0][1] and "fr-ns3" in ev[0][1] and "Ports" in ev[0][1]

    tn._check_tunnel(tid)
    tn._check_tunnel(tid)
    assert len(events_of("ns3")) == 1                                                                   # not again at every look


def test_when_the_service_comes_back_it_is_said_and_cleared(monkeypatch):
    tid = world("ns4")
    listen: dict = {}
    fake_world(monkeypatch, listen)
    seed(tid, {"ports": [8075], "all": True, "fails": 6, "first": int(time.time()) - 400, "confirmed": True, "told": True})
    listen[8075] = True
    tn._check_tunnel(tid)
    d, _ = detail_of(tid)
    ev = events_of("ns4")
    assert d["no_service"] is None and len(ev) == 1 and ev[0][0] == "ok" and "8075" in ev[0][1] and "back" in ev[0][1]


def test_a_foreign_server_that_does_not_answer_the_question_changes_nothing(monkeypatch):
    tid = world("ns5")
    fake_world(monkeypatch, None)                                                                       # `ss` said nothing: no TLISTEN line
    ns = {"ports": [8075], "all": True, "fails": 4, "first": int(time.time()) - 300, "confirmed": True, "told": True}
    seed(tid, ns)
    tn._check_tunnel(tid)
    d, _ = detail_of(tid)
    assert d["no_service"] == ns and events_of("ns5") == []                                             # neither "cleared" nor told again


def test_a_tunnel_without_a_port_map_is_not_judged(monkeypatch):
    tid = world("ns6", mapping="")
    asked = fake_world(monkeypatch, {})
    tn._check_tunnel(tid)
    d, state = detail_of(tid)
    assert asked == [[]] and d["no_service"] is None and state == "up"


def test_a_standby_is_never_judged_for_it(monkeypatch):
    tid = world("ns7-sb", standby_of="ns7")
    seed(tid, {"ports": [8075], "all": True, "fails": 9, "first": 1, "confirmed": True})
    fake_world(monkeypatch, {})
    tn._check_tunnel(tid)
    d, _ = detail_of(tid)
    assert d["no_service"] is None and events_of("ns7-sb") == []
