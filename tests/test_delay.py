"""The Doctor's delay, brought down (the owner, 2026-10-17: "bring the delay down to the minimum - in every step"):

  the sentinel          a tunnel that is up and loses a unit, its GRE card or its GRE peer is checked within seconds (services/sentinel.py)
  the wake-ups          the repair job runs at once when a tunnel is cut and when a repair ends, and every 5 s besides
  the restart look      a healed tunnel is seen sooner, and a silent route stops the wait
  the retries           1, 2, 5, then every 10 minutes after the END of the last smart repair
  the fast lane         a degraded tunnel that moves no data is looked at every ~8 s too
  the builds            the two servers are asked at once
  the survey            a pair nobody has tested is surveyed by day, on very quiet servers"""
import time

import pytest
from sqlalchemy import select

from nikatel import jobs, ssh
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import actions, bandwidth, builder, knowledge, sentinel
from nikatel.services import autopilot as ap
from nikatel.services import tunnels as tn


def world(name, state="up", **kw):
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9300 + n, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, state_since=int(time.time()) - 300,
                   forwarded_ports=[8300 + n], foreign_tunnel_ip=f"10.8.{n}.2", **kw)
        db.add(t)
        db.flush()
        return t.id, ir.id, fr.id


# ------------------------------------------------------------------ the sentinel: what it reads
ANSWER = """UNIT backhaul-iran-tcpmux-9301.service active
UNIT nikatel-gre-t1.service active
IFACE t1 UP
PEER 10.8.1.2 0
NOW 1000
"""


def test_the_answer_of_the_sentinel_script_is_read_unit_by_unit_card_by_card_and_peer_by_peer():
    r = sentinel.parse(ANSWER + "unknown\nUNIT gone.service inactive\nIFACE nocard \nPEER 10.8.2.2 100\nPEER 10.8.3.2 x\n")
    assert r["units"] == {"backhaul-iran-tcpmux-9301.service": "active", "nikatel-gre-t1.service": "active", "gone.service": "inactive"}      # (the stray "unknown" line of a failed systemctl is not a unit)
    assert r["ifaces"] == {"t1": "UP", "nocard": ""}                                                                                         # an empty state: no such card
    assert r["peers"] == {"10.8.1.2": 0.0, "10.8.2.2": 100.0}                                                                                # (a loss that is not a number says nothing)
    assert sentinel.parse("") == {"units": {}, "ifaces": {}, "peers": {}}


def watch(**kw):
    base = {"id": 1, "name": "t1", "iran_id": 1, "foreign_id": 2, "units_i": ["backhaul-iran-tcpmux-9301.service", "nikatel-gre-t1.service"], "units_f": ["backhaul-kharej-tcpmux-9301.service", "nikatel-gre-t1.service"],
            "iface": "t1", "peer": "10.8.1.2"}
    base.update(kw)
    return sentinel.Watch(**base)


def test_a_tunnel_that_is_up_shows_a_sign_when_a_unit_the_card_or_the_peer_fails():
    iran = sentinel.parse(ANSWER)
    foreign = sentinel.parse("UNIT backhaul-kharej-tcpmux-9301.service active\nUNIT nikatel-gre-t1.service active\n")
    assert sentinel.suspects(watch(), iran, foreign) == []                                                                                    # everything answers: nothing to look at
    assert sentinel.suspects(watch(), None, None) == []                                                                                       # a server that could not be read says nothing
    dead = sentinel.parse(ANSWER.replace("backhaul-iran-tcpmux-9301.service active", "backhaul-iran-tcpmux-9301.service failed"))
    assert sentinel.suspects(watch(), dead, foreign) == ["Iran: backhaul-iran-tcpmux-9301.service is failed"]
    gone = sentinel.parse("UNIT backhaul-kharej-tcpmux-9301.service inactive\nUNIT nikatel-gre-t1.service active\n")
    assert sentinel.suspects(watch(), iran, gone) == ["foreign: backhaul-kharej-tcpmux-9301.service is inactive"]                             # either end
    down = sentinel.parse(ANSWER.replace("IFACE t1 UP", "IFACE t1 DOWN"))
    assert sentinel.suspects(watch(), down, foreign) == ["the GRE interface t1 is DOWN on the Iran server"]
    missing = sentinel.parse(ANSWER.replace("IFACE t1 UP", "IFACE t1 "))
    assert sentinel.suspects(watch(), missing, foreign) == ["the GRE interface t1 is missing on the Iran server"]
    assert sentinel.suspects(watch(), sentinel.parse(ANSWER.replace("IFACE t1 UP", "IFACE t1 UNKNOWN")), foreign) == []                       # a GRE card shows UNKNOWN when it is fine
    silent = sentinel.parse(ANSWER.replace("PEER 10.8.1.2 0", "PEER 10.8.1.2 100"))
    assert sentinel.suspects(watch(), silent, foreign) == ["no answer from the foreign end over the GRE link"]
    assert sentinel.suspects(watch(ping_blocked=True), silent, foreign) == []                                                                 # a provider that drops ICMP: the pings say nothing
    assert sentinel.suspects(watch(), sentinel.parse(ANSWER.replace("PEER 10.8.1.2 0", "PEER 10.8.1.2 33")), foreign) == []                   # loss is the check's business, silence is the sentinel's
    assert sentinel.suspects(watch(iface="", peer=""), sentinel.parse(ANSWER.replace("IFACE t1 UP", "IFACE t1 DOWN")), foreign) == []        # a tunnel with no GRE card has no card to lose


def test_every_server_is_asked_once_for_all_its_tunnels():
    a = watch(id=1, name="a", units_i=["u1.service", "nikatel-gre-a.service"], units_f=["f1.service", "nikatel-gre-a.service"], iface="a", peer="10.8.1.2")
    b = watch(id=2, name="b", iran_id=1, foreign_id=3, units_i=["u2.service", "nikatel-gre-b.service"], units_f=["f2.service"], iface="b", peer="10.8.2.2")
    c = watch(id=3, name="c", iran_id=1, foreign_id=2, units_i=["u3.service"], units_f=["f3.service"], iface="", peer="")                     # no GRE card (BackPack, Chisel, Paqet)
    d = watch(id=4, name="d", iran_id=1, foreign_id=2, units_i=["u4.service"], units_f=["f4.service"], iface="d", peer="10.8.4.2", ping_blocked=True)
    ask = sentinel.plan_reads([a, b, c, d])
    assert ask[1]["units"] == ["u1.service", "nikatel-gre-a.service", "u2.service", "nikatel-gre-b.service", "u3.service", "u4.service"]       # the Iran units of every tunnel on server 1, once each
    assert ask[1]["ifaces"] == ["a", "b", "d"] and ask[1]["peers"] == ["10.8.1.2", "10.8.2.2"]                                                # (a peer whose pings are blocked is not pinged)
    assert ask[2]["units"] == ["f1.service", "nikatel-gre-a.service", "f3.service", "f4.service"] and ask[2]["ifaces"] == [] and ask[2]["peers"] == []     # a foreign server: units only
    assert ask[3]["units"] == ["f2.service"]
    many = [watch(id=n, name=f"m{n}", units_i=[f"i{n}.service"], units_f=[f"f{n}.service"], iface="", peer="") for n in range(60)]
    assert len(sentinel.plan_reads(many)[1]["units"]) == sentinel.PER_SERVER                                                                  # one command never asks for more than a server can answer fast


def test_the_sentinel_script_takes_only_what_its_rules_allow():
    text = ssh._script_text("sentinel", {"UNITS": "backhaul-iran-tcpmux-9301.service nikatel-gre-t1.service", "IFACES": "t1 t2", "PEERS": "10.8.1.2 10.8.2.2"})
    assert 'systemctl is-active "$u"' in text and "backhaul-iran-tcpmux-9301.service nikatel-gre-t1.service" in text and "10.8.1.2 10.8.2.2" in text
    assert "rm " not in text and "systemctl restart" not in text and "kill" not in text            # it only looks
    assert ssh._script_text("sentinel", {"UNITS": "a.service", "IFACES": "", "PEERS": ""})
    for bad in ({"UNITS": "a.service; reboot", "IFACES": "", "PEERS": ""}, {"UNITS": "a.service", "IFACES": "t1;reboot", "PEERS": ""}, {"UNITS": "a.service", "IFACES": "", "PEERS": "203.0.113.4;reboot"}):
        with pytest.raises(ValueError):
            ssh._script_text("sentinel", bad)


# ------------------------------------------------------------------ the sentinel: what it does
def sentinel_world(monkeypatch, answers):
    """Two servers, one up tunnel; `answers`: what the servers say. Returns (tunnel id, the ids that were checked)."""
    tid, ir, fr = world("sn1")
    checked = []
    monkeypatch.setattr(sentinel, "_LAST", {})
    monkeypatch.setattr(sentinel.jobs, "stopping", lambda: False)
    monkeypatch.setattr(sentinel, "run_light", lambda node, name, params=None, timeout=8: answers(node, params))
    QUICK.clear()

    def fake_check(tunnel_id, fresh_ext=False, quick=False):
        checked.append((tunnel_id, fresh_ext))
        QUICK.append(quick)

    monkeypatch.setattr(sentinel.tunnels, "check_tunnel", fake_check)
    monkeypatch.setattr(ap, "repairing_names", list)
    return tid, ir, fr, checked


QUICK: list[bool] = []         # the `quick` flag of every check the sentinel asked for


def test_the_sentinel_checks_at_once_a_tunnel_that_lost_a_unit_and_leaves_a_healthy_one_alone(monkeypatch):
    def answers(node, params):
        units = params["UNITS"].split()
        text = "".join(f"UNIT {u} " + ("failed" if u.startswith("backhaul-iran") else "active") + "\n" for u in units)
        text += "".join(f"IFACE {i} UP\n" for i in params["IFACES"].split()) + "".join(f"PEER {p} 0\n" for p in params["PEERS"].split())
        return text

    tid, _, _, checked = sentinel_world(monkeypatch, answers)
    sentinel.run()
    assert checked == [(tid, True)] and QUICK == [True]                                                              # a fresh look (from another Iran server too), not the minute cycle's; a stopped unit: the quick read first
    checked.clear()
    sentinel.run()
    assert checked == []                                                                                             # the same sign again within the cooldown: not looked at again
    sentinel._LAST.clear()
    monkeypatch.setattr(ap, "repairing_names", lambda: ["sn1"])
    sentinel.run()
    assert checked == []                                                                                             # a repair is working on it: left alone


def test_a_silent_peer_alone_gets_the_full_look_and_a_stopped_unit_or_a_card_the_quick_read(monkeypatch):
    assert sentinel.is_hard(["Iran: backhaul-iran-tcpmux-9301.service is failed"]) and sentinel.is_hard(["the GRE interface t1 is DOWN on the Iran server"])
    assert sentinel.is_hard(["foreign: x.service is inactive", "no answer from the foreign end over the GRE link"])
    assert not sentinel.is_hard(["no answer from the foreign end over the GRE link"]) and not sentinel.is_hard([])

    def silent(node, params):
        return "".join(f"UNIT {u} active\n" for u in params["UNITS"].split()) + "".join(f"IFACE {i} UP\n" for i in params["IFACES"].split()) + "".join(f"PEER {p} 100\n" for p in params["PEERS"].split())

    tid, _, _, checked = sentinel_world(monkeypatch, silent)
    sentinel.run()
    assert checked == [(tid, True)] and QUICK == [False]                                                             # an ICMP filter is not a cut: the full look decides


def quick_world(monkeypatch, name, iran_units_state):
    """A tunnel whose servers answer from `iran_units_state` ("active" / "inactive") on the Iran side; records every _query (quick or not) and every single look from another server."""
    tid, _, _ = world(name)
    calls, looks = [], []

    def query(node, plan, is_iran, quick=False):
        calls.append(("iran" if is_iran else "foreign", quick))
        units = plan.iran_units if is_iran else plan.foreign_units
        text = "".join(f"UNIT {u} " + (iran_units_state if is_iran else "active") + "\n" for u in units)

        if is_iran and plan.iface:
            text += f"IFACE {plan.iface} UP\n"

        if not quick and is_iran:
            text += "PEER 10.8.1.2 1.5 0\nDATA 8310 200 0.01\n"

        return tn.parse_side(text + "NOW 1000\n")

    monkeypatch.setattr(tn, "_query", query)
    monkeypatch.setattr(tn, "external_probe_one", lambda *a, **k: looks.append(a))
    monkeypatch.setattr(tn.jobs, "trigger", lambda name: None)
    return tid, calls, looks


def test_a_stopped_unit_is_called_down_after_the_quick_read_without_the_pings_the_request_and_the_look_from_another_server(monkeypatch):
    tid, calls, looks = quick_world(monkeypatch, "qk1", "inactive")
    tn._check_tunnel(tid, fresh_ext=True, quick=True)

    with session() as db:
        t = db.get(Tunnel, tid)
        assert t.state == "down" and "is inactive" in t.last_error

    assert calls == [("iran", True), ("foreign", True)] and looks == []                                              # two quick reads, nothing else: ~2 s, not ~15


def test_when_the_quick_read_finds_nothing_stopped_the_full_check_follows_and_the_look_from_another_server_is_made_with_it(monkeypatch):
    tid, calls, looks = quick_world(monkeypatch, "qk2", "active")
    tn._check_tunnel(tid, fresh_ext=True, quick=True)

    with session() as db:
        assert db.get(Tunnel, tid).state == "up"

    assert sorted(calls) == [("foreign", False), ("foreign", True), ("iran", False), ("iran", True)] and len(looks) == 1       # the quick pair, then the full pair + one look from another server (at the same time)
    calls.clear()
    looks.clear()
    tn._check_tunnel(tid, fresh_ext=False)                                                                           # an ordinary check: no quick read, no extra look
    assert sorted(calls) == [("foreign", False), ("iran", False)] and looks == []


def test_a_server_that_does_not_answer_the_quick_read_is_not_asked_a_second_time(monkeypatch):
    tid, _, _ = world("qk3")
    calls = []

    def query(node, plan, is_iran, quick=False):
        calls.append((is_iran, quick))
        s = tn.Side()
        s.error = "ssh connection lost" if is_iran else ""
        s.units = {u: "active" for u in plan.foreign_units} if not is_iran else {}
        return s

    monkeypatch.setattr(tn, "_query", query)
    monkeypatch.setattr(tn, "external_probe_one", lambda *a, **k: None)
    monkeypatch.setattr(tn.jobs, "trigger", lambda name: None)
    monkeypatch.setattr(tn.jobs, "stopping", lambda: False)                                                          # (an earlier test that shut the app down leaves the flag set)
    tn._check_tunnel(tid, fresh_ext=True, quick=True)

    with session() as db:
        assert db.get(Tunnel, tid).detail["iran_error"] == "ssh connection lost"          # (an up tunnel needs two such checks to become "unknown": the rule of `settle`)

    assert sorted(calls) == [(False, True), (True, True)]                                                           # one quick read each; the full look would only wait for the same time-out again


def test_hard_fault_is_what_judge_calls_down_before_any_ping():
    plan = tn.Plan(["backhaul-iran-tcpmux-9301.service", "nikatel-gre-t1.service"], ["backhaul-kharej-tcpmux-9301.service"], ports=[8301], iface="t1", iran_peer="10.8.1.2")
    ok = tn.parse_side("UNIT backhaul-iran-tcpmux-9301.service active\nUNIT nikatel-gre-t1.service active\nIFACE t1 UP\n")
    far = tn.parse_side("UNIT backhaul-kharej-tcpmux-9301.service active\n")
    assert tn.hard_fault(plan, ok, far) == ""
    assert tn.hard_fault(plan, ok, tn.parse_side("UNIT backhaul-kharej-tcpmux-9301.service inactive\n")) == "foreign: backhaul-kharej-tcpmux-9301.service is inactive"
    assert tn.hard_fault(plan, tn.parse_side("UNIT backhaul-iran-tcpmux-9301.service active\nUNIT nikatel-gre-t1.service active\nIFACE t1 DOWN\n"), far) == "the GRE interface t1 is DOWN on the Iran server"
    assert tn.judge(plan, ok, tn.parse_side("UNIT backhaul-kharej-tcpmux-9301.service failed\n")) == ("down", "foreign: backhaul-kharej-tcpmux-9301.service is failed")


def test_the_tunnel_side_script_asks_the_users_path_while_it_pings_and_does_not_wait_for_twenty_pings_of_a_silent_peer():
    params = {"UNITS": "a.service", "IFACE": "t1", "PEER": "10.8.1.2", "PORTS": "8301"}
    text = ssh._script_text("tunnel_side", params)
    assert "ping -c 3" in text and "ping -c 20" in text and '*" 0 received"*' in text                                  # three pings first; twenty only when something answered
    assert text.index("curl -s -m 4") < text.index("ping -c 3") and ") & fi" in text and "\nwait\n" in text          # the request through the port runs in the background beside the pings
    quick = ssh._script_text("tunnel_side", {**params, "QUICK": "1"})
    assert '[ "1" = 1 ]' in quick and "exit 0" in quick
    assert '[ "" = 1 ]' in ssh._script_text("tunnel_side", params)                                                    # (no QUICK given: the full look)

    with pytest.raises(ValueError):
        ssh._script_text("tunnel_side", {**params, "QUICK": "2"})


def test_the_sentinel_says_nothing_about_a_healthy_tunnel_a_down_one_or_a_server_it_cannot_ask(monkeypatch):
    def healthy(node, params):
        return "".join(f"UNIT {u} active\n" for u in params["UNITS"].split()) + "".join(f"IFACE {i} UP\n" for i in params["IFACES"].split()) + "".join(f"PEER {p} 0\n" for p in params["PEERS"].split())

    tid, _, _, checked = sentinel_world(monkeypatch, healthy)
    sentinel.run()
    assert checked == []
    monkeypatch.setattr(sentinel, "run_light", lambda node, name, params=None, timeout=8: (_ for _ in ()).throw(ssh.SSHError("ssh connection lost")))
    sentinel.run()
    assert checked == []                                                                                             # nothing could be read: no sign
    monkeypatch.setattr(sentinel, "run_light", lambda node, name, params=None, timeout=8: "UNIT x.service failed\n")
    monkeypatch.setattr(sentinel, "_LAST", {})

    with session() as db:
        db.get(Tunnel, tid).state = "down"

    sentinel.run()
    assert checked == []                                                                                             # a tunnel that is down is the fast lane's, not the sentinel's


# ------------------------------------------------------------------ the wake-ups
def test_the_repair_job_runs_at_once_when_a_tunnel_is_cut_and_when_a_repair_ends(monkeypatch):
    woken = []
    monkeypatch.setattr(jobs, "trigger", lambda name: woken.append(name))
    ap.wake_repair()
    assert woken == ["repair"]
    monkeypatch.setattr(ap.get_settings(), "read_only", False)
    aid = ap._start("wake-test", lambda aid: None, action="observe", allow_read_only=True)
    end = time.time() + 5

    while time.time() < end and len(woken) < 2:
        time.sleep(0.05)

    assert woken == ["repair", "repair"] and aid                                                                   # the end of a repair wakes the job

    tid, _, _ = world("wk2")
    woken.clear()

    def down(node, plan, is_iran):
        units = plan.iran_units if is_iran else plan.foreign_units
        text = "".join(f"UNIT {u} " + ("inactive" if is_iran else "active") + "\n" for u in units)
        return tn.parse_side(text + "NOW 1000\n")

    monkeypatch.setattr(tn, "_query", down)
    tn._check_tunnel(tid)

    with session() as db:
        assert db.get(Tunnel, tid).state == "down"

    assert woken == ["repair"]                                                                                     # a tunnel turned down: the job looks at it now
    woken.clear()
    tn._check_tunnel(tid)
    assert woken == []                                                                                             # (no change of state, no wake-up)


def test_the_repair_job_ticks_every_five_seconds_and_the_sentinel_every_ten():
    import inspect

    from nikatel import main

    src = inspect.getsource(main._register_jobs)
    assert 'jobs.register("repair", 5, autopilot.repair_cycle)' in src and 'jobs.register("sentinel", 10, sentinel.run)' in src


# ------------------------------------------------------------------ the restart and its look
def test_a_healed_tunnel_is_seen_sooner_and_a_silent_route_stops_the_wait(monkeypatch):
    tid, _, _ = world("rc1", state="down")
    slept = []
    monkeypatch.setattr(actions.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(actions, "external_probe_one", lambda *a, **k: None)
    monkeypatch.setattr(bandwidth, "recheck", lambda tunnel_id: None)
    plan = actions.Plan([], [], ports=[])
    seq = iter(["up", "up"])

    def check(tunnel_id, fresh_ext=False):
        with session() as db:
            db.get(Tunnel, tunnel_id).state = next(seq)

    monkeypatch.setattr(actions, "check_tunnel", check)
    assert actions._recheck(tid, plan, "203.0.113.20", 1)[0] == "up"
    assert slept == [actions.RECHECK_FIRST, actions.RECHECK_EVERY] and actions.RECHECK_FIRST == 4 and actions.RECHECK_EVERY == 5      # seen after 9 s of waiting (it was 18)

    silent = "no answer from the foreign end over the GRE link"
    looks = []

    def check_silent(tunnel_id, fresh_ext=False):
        looks.append(1)

        with session() as db:
            t = db.get(Tunnel, tunnel_id)
            t.state, t.last_error = "down", silent

    monkeypatch.setattr(actions, "check_tunnel", check_silent)
    state, why = actions._recheck(tid, plan, "203.0.113.20", 1)
    assert state == "down" and why == silent and len(looks) == actions.RECHECK_GIVE_UP + 1                          # a silent route after three looks: the smart repair goes on, the rest of the time is not waited out
    looks.clear()

    def check_stopped(tunnel_id, fresh_ext=False):
        looks.append(1)

        with session() as db:
            t = db.get(Tunnel, tunnel_id)
            t.state, t.last_error = "down", "Iran: backhaul-iran-tcpmux-9906.service is inactive"

    monkeypatch.setattr(actions, "check_tunnel", check_stopped)
    assert actions._recheck(tid, plan, "203.0.113.20", 1, tries=4)[0] == "down" and len(looks) == 5               # a stopped service may still be starting: the whole time is waited


# ------------------------------------------------------------------ the retries
def test_a_cut_tunnel_waits_one_two_five_then_ten_minutes_after_the_end_of_its_last_try():
    assert [ap.retry_after("down", n) for n in (0, 1, 2, 3, 4, 9)] == [60, 60, 120, 300, 600, 600]
    assert ap.retry_after("degraded", 1) == 1800 and ap.retry_after("degraded", 7) == 1800                          # one that only flaps: every half hour
    assert ap.MAX_AUTO_REPAIRS == 4 and ap.SOAK == 25


# ------------------------------------------------------------------ no waiting time
def test_the_doctor_starts_at_the_first_moment_a_tunnel_is_down_whatever_an_old_setting_says(monkeypatch):
    from nikatel.models import ActionLog
    from nikatel.services import settings as st

    tid, _, _ = world("gr1", state="down")
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tid)
        t.state_since, t.last_error = now - 1, "no answer from the foreign end over the GRE link"          # down for ONE second (a silent link: the case that used to wait the whole grace)

    st.put("repair", {"after_seconds": 600, "mode": "fix"})                                                    # (an old saved value: it is ignored)
    started, smart = [], []
    monkeypatch.setattr(actions, "fix", lambda tunnel_id, source="manual", dry_run=False: started.append(tunnel_id) or 1)
    monkeypatch.setattr(actions.get_settings(), "read_only", False)
    monkeypatch.setattr(ap, "outage_state", lambda: {"storm": False})
    monkeypatch.setattr(ap, "smart_fix_start", lambda tunnel_id, level="switch", source="manual": smart.append(tunnel_id) or 1)
    actions.auto_repair()
    assert actions.DOWN_GRACE == 0 and started == [tid]                                                        # the plain restart begins at once
    ap.auto_escalate()
    assert smart == []                                                                                         # (it waits for the restart's own outcome, as before)

    with session() as db:
        db.add(ActionLog(ref="gr1", action="fix", source="auto", ok=False, finished_at=now, story=["Every step was tried; the tunnel is still not up."], ts=now - 1))

    ap.auto_escalate()
    assert smart == [tid]                                                                                      # the restart did not heal it: the smart repair starts the same second, with no waiting time


def test_the_waiting_time_is_gone_from_the_page_the_settings_and_the_state(client):
    app = (__import__("pathlib").Path(__file__).parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")
    assert 'cfg("after_seconds"' not in app and "no waiting time" in app and 'apNum("after_seconds"' not in app          # no field: nothing to set
    assert client.get("/api/settings/repair").json()["after_seconds"] == 0
    assert ap.OBSERVE_EVERY == 30 and ap.DEGRADED_AFTER == 120


# ------------------------------------------------------------------ the fast lane
def test_the_fast_lane_looks_at_a_degraded_tunnel_that_moves_no_data_but_not_at_one_that_only_loses_packets(monkeypatch):
    now = int(time.time())
    nodata, _, _ = world("fl1", state="degraded")
    lossy, _, _ = world("fl2", state="degraded")
    shut, _, _ = world("fl3", state="degraded")
    down, _, _ = world("fl4", state="down")
    fresh, _, _ = world("fl5", state="degraded")

    with session() as db:
        for tid, why, at in ((nodata, "port 8075 accepts connections but no data comes back through the tunnel", now - 30), (lossy, "20% loss on the GRE link", now - 30),
                             (shut, "port 8075 is not listening on the Iran server", now - 30), (down, "Iran: x.service is inactive", now - 30),
                             (fresh, "port 8075 accepts connections but no data comes back through the tunnel", now - 2)):
            t = db.get(Tunnel, tid)
            t.last_error, t.last_check_at = why, at

    seen = []
    monkeypatch.setattr(tn, "check_tunnel", lambda tunnel_id, fresh_ext=False: seen.append(tunnel_id))
    tn.check_unhealthy()
    assert {nodata, shut, down} <= set(seen) and not {lossy, fresh} & set(seen)                                   # (the one that was looked at two seconds ago waits; the lossy one stays in the minute cycle)


# ------------------------------------------------------------------ the builds
def test_the_two_servers_of_a_build_are_asked_at_the_same_time_and_an_error_is_raised_after_both_ended():
    import threading

    started = threading.Barrier(2, timeout=5)                    # both calls must be in flight together, or the barrier breaks
    assert builder._both(lambda: (started.wait(), "iran")[1], lambda: (started.wait(), "foreign")[1]) == ("iran", "foreign")
    ended = []

    def slow_fail():
        time.sleep(0.2)
        ended.append("slow")
        raise RuntimeError("the Iran side failed")

    def other():
        time.sleep(0.4)
        ended.append("other")
        return 1

    with pytest.raises(RuntimeError, match="the Iran side failed"):
        builder._both(slow_fail, other)

    assert ended == ["slow", "other"]                             # nothing is left running behind the exception
    assert builder.STABLE_WAIT == 5


# ------------------------------------------------------------------ the survey
def test_a_pair_nobody_has_tested_is_surveyed_by_day_on_very_quiet_servers_and_a_known_pair_waits_for_the_night(monkeypatch):
    from nikatel.config import get_settings
    from nikatel.services import settings as st

    ir1, fr1 = world("sv1")[1:]
    ir2, fr2 = world("sv2")[1:]
    knowledge.record(ir1, fr1, {"kind": "tcpmux", "ok": True, "score": 60}, "full")
    started = []
    monkeypatch.setattr(knowledge, "survey_start", lambda iran_id, foreign_id, source="survey-manual", kinds=None: started.append((iran_id, foreign_id, source)) or "cmp1")
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(ap, "repairing_names", list)
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(knowledge, "_running", lambda: None)
    monkeypatch.setattr(knowledge.jobs, "stopping", lambda: False)                                               # (an earlier test may have stopped the jobs for good)
    monkeypatch.setattr(knowledge, "in_window", lambda *a, **k: False)                                           # an afternoon
    st.put("knowledge", {"on": True})
    assert [p[:2] for p in knowledge.pending_pairs(int(time.time()), cold_only=True)] == [(ir2, fr2)]            # only the pair that was never surveyed
    knowledge.run()
    assert started == [(ir2, fr2, "survey-cold")]
    assert "survey-cold" in knowledge.SURVEYS and knowledge.COLD_QUIET_MBPS == 30.0
    monkeypatch.setattr(knowledge, "not_now", lambda i, f, now, quiet=knowledge.QUIET_MBPS: "its servers carry 200 Mb/s for users" if quiet < 100 else "")
    started.clear()
    knowledge.run()
    assert started == []                                                                                         # by day the bar is 30 Mb/s: a pair that carries users waits for the night
    st.put("knowledge", {"on": False})
    knowledge.run()
    assert started == []                                                                                         # switched off: nothing, day or night
