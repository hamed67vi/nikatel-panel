"""The lanes (the owner, 2026-10-20: "when the capacity of a tunnel is nearly full - capacity 76, users use 69 - the Doctor must add capacity without cutting any customer: load balancing between tunnels, or anything else that
raises capacity"; services/lanes.py, docs/lanes.md): the warm standby put to work BESIDE its tunnel, the NEW connections split between the two by a NAT rule on the Iran server.

  the numbers     the share by the two capacities (10 - 90 %), the probability text, the capacity of a tunnel, "nearly full" and "not needed" over their windows
  the decisions   `decide` - pure: start, measure, adjust, park, resume, stop, clean - in every state, in the Doctor's modes
  the rules       the three reviewed actions run for real against a fake iptables: one rule per port, replaced in place, the standby's parked RETURN taken off, the careful end, the clean removal only when nothing is left on it
  the service     start / adjust / park / stop / clean / refresh, the job, the reconcile, the removal of the standby, the numbers of the lane
  the page        the bar in the tunnel window, the switch on the Doctor page, the API
The same rules on a REAL kernel (two network namespaces, the panel's own rendered actions): lane-netns-proof.sh in the shared notes folder, recorded in docs/lanes.md."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from nikatel import ssh
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import (
    actions,
    autopilot,
    bandwidth,
    builder,
    capacity,
    lanes,
    livebw,
    standby,
)
from nikatel.services import settings as st

NOW = int(time.time())


def clock() -> int:
    return int(time.time())


def writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)


@pytest.fixture(autouse=True)
def _clean_memory(monkeypatch):
    """What the module keeps in memory must not leak from one test to the next."""
    lanes._RING.clear()
    lanes._FULL.clear()
    capacity._VIEW["tunnels"] = {}
    lanes._NOTE.clear()
    lanes._STATE.update({"listed": {}, "cleaned": {}, "backoff": {}})
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(autopilot, "repairing_names", lambda: set())
    yield


def world(name, state="up", since=300, bw=76.0, **kw):
    """Two servers and a Backhaul tunnel (capacity `bw` Mb/s by its light test) forwarding one port."""
    rec = {"bw": {"down": bw, "ref": bw, "at": clock()}} if bw else {}

    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        port = 8300 + n
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9300 + n, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, state_since=clock() - since, last_check_at=clock() - 2,
                   forwarded_ports=[port], foreign_tunnel_ip=f"10.8.{n}.2", record=rec, **kw)
        db.add(t)
        db.flush()
        return {"id": t.id, "name": name, "iran": ir.id, "foreign": fr.id, "port": port}


def add_standby(w, state="up", since=300, use=None, lane=None, ports=None):
    name = w["name"] + "-sb"
    shifted = ports if ports is not None else [standby.shifted(w["port"])]
    rec = {"standby_of": w["name"], "port_mapping": f"{shifted[0]}:{w['port']}", "kind": "chisel", "built_by": "nikatel-panel", **({"use": use} if use else {}), **({"lane": lane} if lane else {})}

    with session() as db:
        t = Tunnel(name=name, type="chisel", transport="wss", enabled=False, port=9650, iran_server_id=w["iran"], foreign_server_id=w["foreign"], state=state, state_since=clock() - since,
                   last_check_at=clock() - 2, forwarded_ports=shifted, record=rec)
        db.add(t)
        db.flush()
        return t.id


def lane_rec(w, state="on", pct=43, since=3600, **kw):
    return {"state": state, "pct": pct, "map": f"{w['port']}:{standby.shifted(w['port'])}", "protos": "tcp", "since": clock() - since, "changed": clock() - since, "iran_id": w["iran"], "manual": False, **kw}


def lane_of(sid):
    with session() as db:
        return (db.get(Tunnel, sid).record or {}).get("lane")


def fake_actions(monkeypatch, fail=False, off="LANE_OFF {n} 0 0 0"):
    """The reviewed actions of the lane, answered like the real ones (nothing leaves the test)."""
    calls = []

    def run_action(node, name, params=None, timeout=90):
        calls.append((name, dict(params or {})))

        if name == "lane_on":
            if fail:
                return "LANE_FAIL the kernel did not take the rule for port 80\n"

            return f"LANE_ON {params['NAME']} {len(params['MAP'].split(',')) * len(params['PROTOS'].split())}\n"

        if name == "standby_off":
            return f"STANDBY_OFF {params['NAME']} 0\n"

        return off.format(n=params["NAME"]) + "\n"

    monkeypatch.setattr(lanes, "run_action", run_action)
    monkeypatch.setattr(standby, "run_action", run_action)
    return calls


def p_view(state="up", since=300, **kw):
    return {"state": state, "state_since": NOW - since, "last_check_at": NOW - 2, "off": False, **kw}


def s_view(state="up", since=300, **kw):
    return {"state": state, "state_since": NOW - since, "stale": "", "use": None, **kw}


LOAD_HOT = {"cap_p": 76.0, "cap_s": 57.0, "pct": 43, "hot": True, "idle": False}
LOAD_COLD = {"cap_p": 76.0, "cap_s": 57.0, "pct": 43, "hot": False, "idle": False}
AP_ON = {"state": "on", "pct": 43, "since": NOW - 3600, "changed": NOW - 3600}


# ------------------------------------------------------------------ the numbers
def test_the_share_follows_the_two_capacities_and_stays_between_ten_and_ninety_percent():
    assert lanes.share_pct(76, 76) == 50 and lanes.share_pct(76, 57) == 43 and lanes.share_pct(60, 120) == 67
    assert lanes.share_pct(20, 400) == 90 and lanes.share_pct(100, 25) == 20 and lanes.share_pct(1000, 100) is None   # the clamp (a standby under a quarter of the tunnel is no lane, so 20 % is the least a lane gives)
    assert lanes.share_pct(None, 50) is None and lanes.share_pct(50, None) is None and lanes.share_pct(0, 50) is None and lanes.share_pct(50, -1) is None
    assert lanes.share_pct(76, 9) is None and lanes.share_pct(100, 24) is None and lanes.share_pct(100, 25) == 20      # a second tunnel that would add under 10 Mb/s or a quarter of the first is no lane
    assert lanes.prob_text(43) == "0.43000000" and lanes.prob_text(5) == "0.05000000" and lanes.prob_text(0) == "0.01000000" and lanes.prob_text(150) == "0.99000000"


def test_the_capacity_of_a_tunnel_is_its_usual_level_never_below_its_last_test_and_must_be_fresh():
    now = clock()
    assert lanes.cap_of({"down": 70.0, "ref": 76.0, "at": now}, now) == 76.0 and lanes.cap_of({"down": 90.0, "ref": 76.0, "at": now}, now) == 90.0
    assert lanes.cap_of({"down": 70.0, "ref": None, "at": now}, now) == 70.0                                        # (no usual level yet)
    assert lanes.cap_of({"down": 70.0, "ref": 76.0, "at": now - 8 * 86400}, now) is None                            # a week-old test says nothing about today
    assert lanes.cap_of({"down": 15.0, "ref": 15.0, "at": now}, now) is None                                        # "very slow" tunnels have another story
    assert lanes.cap_of({}, now) is None and lanes.cap_of(None, now) is None


def ring(values, step=30.0, end=None, lane=0.0):
    """Samples (time, tunnel Mb/s, lane Mb/s) every `step` seconds, the last at `end`."""
    end = end or time.time()
    return [(end - step * (len(values) - 1 - i), v, lane) for i, v in enumerate(values)]


def test_a_tunnel_is_full_for_a_lane_when_the_doctors_detector_has_said_so_for_four_minutes():
    now = time.time()
    assert lanes.is_full("full", now - 241, now) and not lanes.is_full("full", now - 200, now)                       # four minutes: the Doctor's own story (it starts after three) is on its way first
    assert not lanes.is_full("warm", now - 900, now) and not lanes.is_full("ok", now - 900, now) and not lanes.is_full("", now - 900, now) and not lanes.is_full("full", None, now)

def test_the_lane_is_not_needed_when_the_total_stayed_under_half_of_the_capacity_for_twenty_minutes():
    calm = ring([20.0] * 41, lane=10.0)                                                                                # 20 minutes, 30 of 76
    assert lanes.is_idle(calm, time.time(), 76.0)
    assert not lanes.is_idle(ring([20.0] * 20, lane=10.0), time.time(), 76.0)                                           # only 10 minutes
    assert not lanes.is_idle(ring([20.0] * 20 + [60.0] * 5 + [20.0] * 16), time.time(), 76.0)                           # a burst in it
    assert not lanes.is_idle(ring([20.0] * 41, lane=25.0), time.time(), 76.0)                                          # 45 of 76 (59 %) is over half
    assert not lanes.is_idle([], time.time(), 76.0) and not lanes.is_idle(calm, time.time(), None)


def test_the_rules_on_a_server_are_read_by_standby_and_port():
    out = ("LANE de-sb tcp 8080 28080 0.39999999991 0\nLANE de-sb udp 8080 28080 0.39999999991 1\nLANE other-sb tcp 443 20443 0.70000000019 0\nPARKED old-sb tcp 80\nPARKED old-sb udp 80\nNOW 1700000000\nLANE bad tcp x 1 0.5 0\n")
    lanes_, parked = lanes.parse_list(out)
    assert {k: [(r["proto"], r["port"], r["to"], r["shadow"]) for r in v] for k, v in lanes_.items()} == {"de-sb": [("tcp", 8080, 28080, 0), ("udp", 8080, 28080, 1)], "other-sb": [("tcp", 443, 20443, 0)]}
    assert abs(lanes_["de-sb"][0]["prob"] - 0.4) < 1e-6 and parked == {"old-sb": [("tcp", 80), ("udp", 80)]}


# ------------------------------------------------------------------ the decisions
def test_a_lane_starts_only_when_everything_is_calm_and_the_tunnel_is_nearly_full():
    d = lambda p=None, s=None, w=None, ap=None, load=LOAD_HOT, **kw: lanes.decide(p or p_view(), s or s_view(), w or {}, ap, load, now=NOW, enabled=kw.pop("enabled", True), auto=kw.pop("auto", True), **kw)
    assert d() == ("start", "the users' load stayed at 85 % of the tunnel's capacity or more for most of 10 minutes")
    assert d(load={**LOAD_HOT, "cap_s": None, "pct": None})[0] == "measure"                                              # the standby's capacity is measured first
    assert d(load={**LOAD_HOT, "pct": None})[0] == "wait" and "too little" in d(load={**LOAD_HOT, "pct": None})[1]
    assert d(load=LOAD_COLD)[0] == "wait" and "room" in d(load=LOAD_COLD)[1]
    assert d(load={**LOAD_HOT, "cap_p": None})[0] == "wait" and "not measured" in d(load={**LOAD_HOT, "cap_p": None})[1]
    assert d(enabled=False)[0] == "wait" and "off" in d(enabled=False)[1]                                                  # the owner has not switched lanes on
    assert d(auto=False)[0] == "wait" and "Doctor is off" in d(auto=False)[1]
    assert d(p=p_view("down"))[0] == "wait" and d(p=p_view("up", 30))[0] == "wait" and d(s=s_view("down"))[0] == "wait" and d(s=s_view("up", 20))[0] == "wait"      # both up for a minute
    assert d(s=s_view(use={"since": NOW}))[0] == "wait" and d(s=s_view(stale="the ports changed"))[0] == "wait"
    assert d(repairing=True)[0] == "wait" and d(storm=True)[0] == "wait" and d(calm="server x is busy")[1] == "server x is busy"
    assert d(w={"stopped_at": NOW - 600})[0] == "wait" and "short while ago" in d(w={"stopped_at": NOW - 600})[1]            # a lane that ended by itself is not put again at once
    assert d(w={"stopped_at": NOW - 4000})[0] == "start"
    assert lanes.decide(None, s_view(), {}, None, LOAD_HOT, now=NOW, enabled=True, auto=True)[0] == "wait"
    assert "ready standby" in lanes.decide(p_view(), None, {}, None, LOAD_HOT, now=NOW, enabled=True, auto=True)[1]


def test_a_lane_that_works_is_left_alone_parked_when_the_second_tunnel_falls_and_stopped_when_it_is_not_needed():
    d = lambda p=None, s=None, w=None, ap=AP_ON, load=LOAD_HOT, **kw: lanes.decide(p or p_view(), s or s_view(), w or {"want": True}, ap, load, now=NOW, enabled=kw.pop("enabled", True), auto=kw.pop("auto", True), **kw)
    assert d() == ("wait", "sharing")
    assert d(s=s_view("down"))[0] == "park" and d(s=s_view(stale="other ports"))[0] == "park"                             # the second tunnel is not up: everything goes to the tunnel
    assert d(p=p_view("down"))[0] == "wait" and d(s=s_view(use={"since": NOW}))[0] == "wait"                              # the tunnel is cut: its standby's own handover carries everything, above the lane
    assert d(load={**LOAD_HOT, "idle": True})[0] == "stop"                                                                # not needed (the lane is an hour old)
    assert d(ap={**AP_ON, "since": NOW - 600}, load={**LOAD_HOT, "idle": True})[0] == "wait"                              # ... but not within its first half hour
    assert d(load={**LOAD_HOT, "idle": True}, w={"want": True, "manual": True})[0] == "wait"                              # a lane started by hand stays until Stop
    assert d(enabled=False)[0] == "stop" and d(auto=False)[0] == "stop"                                                   # lanes switched off, the Doctor off: the automatic lane ends
    assert d(enabled=False, w={"want": True, "manual": True})[0] == "wait"
    assert d(load={**LOAD_HOT, "pct": 60})[0] == "adjust" and d(load={**LOAD_HOT, "pct": 48})[0] == "wait"               # the share moved 17 points / 5 points
    assert d(load={**LOAD_HOT, "pct": 60}, ap={**AP_ON, "changed": NOW - 100})[0] == "wait"                               # ... not rewritten more than every ten minutes


def test_a_parked_lane_is_put_again_when_both_tunnels_are_up_and_cleaned_when_nobody_wants_it():
    parked = {"state": "parked", "pct": 43, "since": NOW - 3600, "parked_at": NOW - 600}
    d = lambda p=None, s=None, w=None, ap=parked, load=LOAD_HOT, **kw: lanes.decide(p or p_view(), s or s_view(), w if w is not None else {"want": True}, ap, load, now=NOW, enabled=kw.pop("enabled", True), auto=kw.pop("auto", True), **kw)
    assert d()[0] == "resume" and d(s=s_view("up", 20))[0] == "wait" and d(s=s_view("down"))[0] == "wait"                    # it waits until both have been up for a minute
    assert d(load={**LOAD_COLD, "idle": True})[0] == "stop" and d(enabled=False)[0] == "stop"
    assert d(w={})[0] == "clean" and d(w={}, ap={**parked, "parked_at": NOW - 60})[0] == "wait"                              # nobody wants it: the parked rules are taken off after a few minutes
    assert d(w={"want": True}, ap=None)[0] == "resume"                                                                      # the standby was rebuilt: the wish is put on the new one
    assert d(w={"want": True}, ap=None, calm="server x is busy")[0] == "wait"


# ------------------------------------------------------------------ the reviewed actions, run for real against a fake iptables
FAKE_IPTABLES = '''#!/usr/bin/env python3
import json, os, sys

path = os.environ["FAKE_IPT_STATE"]
rules = json.load(open(path)) if os.path.exists(path) else []
args = [a for a in sys.argv[1:] if a != "-w"]
table = "filter"

if args[:1] == ["-t"]:
    table, args = args[1], args[2:]

op = args[0]

if op == "-L":
    sys.exit(0)

chain = args[1]


def norm(spec):
    out, i = [], 0

    while i < len(spec):
        if spec[i] == "-p":
            out += ["-p", spec[i + 1], "-m", spec[i + 1]]
            i += 4 if spec[i + 2:i + 4] == ["-m", spec[i + 1]] else 2
        elif spec[i] == "--comment":
            out += ["--comment", '"' + spec[i + 1] + '"']
            i += 2
        else:
            out.append(spec[i])
            i += 1

    return " ".join(out)


if op == "-S":
    for t, c, spec in rules:
        if t == table and c == chain:
            print("-A " + chain + " " + spec)

    sys.exit(0)

if op == "-R":
    n = int(args[2])
    rec = [table, chain, norm(args[3:])]
    where = [i for i, (t, c, _) in enumerate(rules) if t == table and c == chain]
    rules[where[n - 1]] = rec
    json.dump(rules, open(path, "w"))
    sys.exit(0)

spec = args[3:] if op == "-I" and args[2].isdigit() else args[2:]
rec = [table, chain, norm(spec)]

if op == "-C":
    sys.exit(0 if rec in rules else 1)

if op == "-A":
    rules.append(rec)
elif op == "-I":
    rules.insert(0, rec)
elif op == "-D":
    if rec not in rules:
        sys.exit(1)

    rules.remove(rec)

json.dump(rules, open(path, "w"))
'''
FAKE_SS = ('#!/bin/sh\n'
           'case "$*" in *":20080 "*|*":21080 "*) ;; *) exit 0 ;; esac\n'          # (only the connections to these two shifted ports are "open")
           'n=$(cat "$FAKE_SS_FILE" 2>/dev/null || echo 0); i=0; while [ "$i" -lt "$n" ]; do echo "ESTAB 0 0 10.0.0.1:20080 10.0.0.9:5555"; i=$((i+1)); done\n')


class Box:
    """A scratch 'Iran server': a fake iptables on the PATH with its rules in a file, and a fake `ss` that reports as many open connections as the test says."""

    def __init__(self, tmp: Path):
        self.tmp, self.state, self.ss = tmp, tmp / "rules.json", tmp / "ss.n"
        (tmp / "bin").mkdir()
        (tmp / "bin" / "iptables").write_text(FAKE_IPTABLES)
        (tmp / "bin" / "ss").write_text(FAKE_SS)
        (tmp / "bin" / "systemctl").write_text("#!/bin/sh\nexit 0\n")

        for f in ("iptables", "ss", "systemctl"):
            (tmp / "bin" / f).chmod(0o755)

    def run(self, text: str) -> str:
        r = subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=60, check=False,
                           env={"PATH": f"{self.tmp / 'bin'}:/usr/bin:/bin", "FAKE_IPT_STATE": str(self.state), "FAKE_SS_FILE": str(self.ss)})
        assert r.returncode == 0, r.stderr
        return r.stdout

    def action(self, name, **params):
        return self.run(ssh.render_action(name, params))

    def rules(self):
        return [tuple(r) for r in json.loads(self.state.read_text())] if self.state.exists() else []

    def mine(self, tag):
        return [r for r in self.rules() if f'"nikatel-{tag}"' in r[2]]


needs_shell = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("python3") is None or shutil.which("awk") is None, reason="needs bash, python3 and awk")


@needs_shell
def test_the_lane_puts_one_random_redirect_per_port_replaces_it_in_place_and_frees_it_from_a_parked_return(tmp_path):
    box = Box(tmp_path)
    box.state.write_text(json.dumps([["nat", "PREROUTING", "-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998"], ["filter", "INPUT", "-j DROP"]]))              # somebody else's rules
    assert box.action("lane_on", NAME="de-sb", PROB="0.40000000", MAP="80:20080,443:20443", PROTOS="tcp").strip() == "LANE_ON de-sb 2"
    nat = [r for r in box.mine("lane-de-sb") if r[0] == "nat"]
    assert len(nat) == 2 and all("-m statistic --mode random --probability 0.40000000" in r[2] and r[2].endswith("-j REDIRECT --to-ports " + r[2].rsplit(" ", 1)[1]) for r in nat)
    assert any("--dport 80 " in r[2] and r[2].endswith("--to-ports 20080") for r in nat) and any("--dport 443 " in r[2] and r[2].endswith("--to-ports 20443") for r in nat)
    inp = [r for r in box.mine("lane-de-sb") if r[1] == "INPUT"]
    assert len(inp) == 2 and all(r[0] == "filter" and r[2].endswith("-j ACCEPT") for r in inp)                                                  # what was redirected must be let in
    assert not list((tmp_path).glob("sbin")) and not list((tmp_path).glob("systemd"))                                                           # no boot unit: after a reboot everything goes to the tunnel
    before = box.rules()
    assert box.action("lane_on", NAME="de-sb", PROB="0.40000000", MAP="80:20080,443:20443", PROTOS="tcp").strip() == "LANE_ON de-sb 2" and box.rules() == before        # idempotent

    # a new share REPLACES the rule where it stands
    assert box.action("lane_on", NAME="de-sb", PROB="0.70000000", MAP="80:20080,443:20443", PROTOS="tcp").strip() == "LANE_ON de-sb 2"
    nat = [r for r in box.mine("lane-de-sb") if r[0] == "nat"]
    assert len(nat) == 2 and all("--probability 0.70000000" in r[2] for r in nat) and len(box.rules()) == len(before)
    assert ("nat", "PREROUTING", "-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998") in box.rules() and ("filter", "INPUT", "-j DROP") in box.rules()

    # what the panel reads is what it put; and a RETURN parked by the standby's way back ABOVE the lane rule shadows it
    lanes_, parked = lanes.parse_list(box.run(ssh._script_text("lane_list", {})))
    assert {r["port"]: (r["to"], r["shadow"]) for r in lanes_["de-sb"]} == {80: (20080, 0), 443: (20443, 0)} and parked == {}
    rules = box.rules()
    box.state.write_text(json.dumps([["nat", "PREROUTING", '-p tcp -m tcp --dport 80 -m comment --comment "nikatel-standby-de-sb" -j RETURN']] + [list(r) for r in rules]))
    lanes_, _ = lanes.parse_list(box.run(ssh._script_text("lane_list", {})))
    assert {r["port"]: r["shadow"] for r in lanes_["de-sb"]} == {80: 1, 443: 0}                                                                # the lane rule for port 80 is dead until that RETURN goes
    assert box.action("lane_on", NAME="de-sb", PROB="0.70000000", MAP="80:20080,443:20443", PROTOS="tcp").strip() == "LANE_ON de-sb 2"
    assert not any("nikatel-standby-" in r[2] for r in box.rules()) and len(box.mine("lane-de-sb")) == 4                                       # freed: the standby's RETURN is gone, the lane rule keeps the chain from being empty
    assert box.action("lane_on", NAME="de-sb3", PROB="0.30000000", MAP="80:20080", PROTOS="tcp udp").strip() == "LANE_ON de-sb3 2"              # a protocol list: one rule for each


@needs_shell
def test_the_lane_ends_the_careful_way_and_the_parked_rules_go_only_when_no_connection_is_left_on_them(tmp_path):
    box = Box(tmp_path)
    box.state.write_text(json.dumps([["nat", "PREROUTING", "-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998"]]))
    box.action("lane_on", NAME="de-sb", PROB="0.40000000", MAP="80:20080,443:20443", PROTOS="tcp")
    box.action("lane_on", NAME="de-sb2", PROB="0.40000000", MAP="80:21080", PROTOS="tcp")                                                       # another lane whose name starts with the first one's

    # PARK=1: nothing redirects any more, a RETURN of the tag is left in the chain on purpose, the accepts keep only the established connections
    box.ss.write_text("3")
    assert box.action("lane_off", NAME="de-sb", PARK="1").strip() == "LANE_OFF de-sb 0 4 3"
    nat = [r for r in box.mine("lane-de-sb") if r[0] == "nat"]
    inp = [r for r in box.mine("lane-de-sb") if r[1] == "INPUT"]
    assert len(nat) == 2 and all(r[2].endswith("-j RETURN") for r in nat) and not any("REDIRECT" in r[2] for r in box.mine("lane-de-sb"))
    assert len(inp) == 2 and all("--ctstate ESTABLISHED,RELATED" in r[2] and r[2].endswith("-j ACCEPT") for r in inp)
    assert len(box.mine("lane-de-sb2")) == 2 and all("REDIRECT" in r[2] or r[2].endswith("-j ACCEPT") for r in box.mine("lane-de-sb2"))        # the other lane is untouched
    assert box.action("lane_off", NAME="de-sb", PARK="1").strip() == "LANE_OFF de-sb 0 4 3" and len(box.mine("lane-de-sb")) == 4               # again: nothing is added twice
    lanes_, parked = lanes.parse_list(box.run(ssh._script_text("lane_list", {})))
    assert list(lanes_) == ["de-sb2"] and sorted(parked["de-sb"]) == [("tcp", 80), ("tcp", 443)]                                               # a parked rule is not a lane

    # PARK=2: while a connection to the shifted ports is open the rules stay (and it says so) ...
    assert box.action("lane_off", NAME="de-sb", PARK="2").strip() == "LANE_OFF de-sb 0 4 3" and len(box.mine("lane-de-sb")) == 4
    # ... when none is left they all go
    box.ss.write_text("0")
    assert box.action("lane_off", NAME="de-sb", PARK="2").strip() == "LANE_OFF de-sb 0 0 0" and box.mine("lane-de-sb") == []
    assert len(box.mine("lane-de-sb2")) == 2
    assert ("nat", "PREROUTING", "-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998") in box.rules()                                       # somebody else's rule

    # PARK=0 (the standby is going away): every rule of the tag, parked or not, whatever is open
    box.ss.write_text("5")
    assert box.action("lane_off", NAME="de-sb2", PARK="0").strip() == "LANE_OFF de-sb2 0 0 5"
    assert [r for r in box.rules() if "nikatel-lane-" in r[2]] == [] and box.action("lane_off", NAME="de-sb2", PARK="0").strip() == "LANE_OFF de-sb2 0 0 0"


@needs_shell
def test_a_server_without_iptables_says_so_and_changes_nothing(tmp_path):
    box = Box(tmp_path)
    (tmp_path / "bin" / "iptables").unlink()
    r = subprocess.run([shutil.which("bash"), "-c", ssh.render_action("lane_on", {"NAME": "de-sb", "PROB": "0.40000000", "MAP": "80:20080", "PROTOS": "tcp"})], capture_output=True, text=True, timeout=30,
                       check=False, env={"PATH": f"{tmp_path / 'empty'}", "FAKE_IPT_STATE": str(box.state)})
    assert "LANE_FAIL this server has no iptables" in r.stdout and not box.state.exists()


def test_the_actions_take_only_a_name_a_share_ports_and_a_protocol():
    on = ssh.render_action("lane_on", {"NAME": "de-ir1-sb", "PROB": "0.43000000", "MAP": "80:20080,443:20443", "PROTOS": "tcp udp"})
    assert "-m statistic --mode random --probability" in on and "REDIRECT" in on and "PREROUTING" in on and "nikatel-lane-" in on and "0.43000000" in on
    assert "OUTPUT" not in on and " -F" not in on and "flush" not in on.lower() and "systemctl" not in on                                                                    # not the request on the server itself; nothing flushed; no boot unit
    off = ssh.render_action("lane_off", {"NAME": "de-ir1-sb", "PARK": "1"})
    assert "-j RETURN" in off and "sed 's/^-A /-D /'" in off and " -F" not in off and "reboot" not in off and "ESTABLISHED,RELATED" in off and "ss -Htn state established" in off
    assert ssh.render_action("lane_off", {"NAME": "de-ir1-sb", "PARK": "0"}) and ssh.render_action("lane_off", {"NAME": "de-ir1-sb", "PARK": "2"})
    assert "iptables -t nat -S PREROUTING" in ssh._script_text("lane_list", {}) and "-A " not in ssh._script_text("lane_list", {}).replace("^-A PREROUTING", "")          # reading only

    for bad in ({"NAME": "x; reboot", "PROB": "0.4", "MAP": "80:20080", "PROTOS": "tcp"}, {"NAME": "de-sb", "PROB": "0.4", "MAP": "80:20080", "PROTOS": "tcp"}, {"NAME": "de-sb", "PROB": "1.0", "MAP": "80:20080", "PROTOS": "tcp"},
                {"NAME": "de-sb", "PROB": "0.40000000;id", "MAP": "80:20080", "PROTOS": "tcp"}, {"NAME": "de-sb", "PROB": "0.40000000", "MAP": "80", "PROTOS": "tcp"}, {"NAME": "de-sb", "PROB": "0.40000000", "MAP": "80:20080", "PROTOS": "sctp"},
                {"NAME": "Upper", "PROB": "0.40000000", "MAP": "80:20080", "PROTOS": "tcp"}):
        with pytest.raises(ValueError):
            ssh.render_action("lane_on", bad)

    for bad in ({"NAME": "a b", "PARK": "1"}, {"NAME": "de-sb", "PARK": "3"}, {"NAME": "de-sb", "PARK": ""}, {"NAME": "de-sb"}, {"NAME": "de-sb", "PARK": "1; id"}):
        with pytest.raises(ValueError):
            ssh.render_action("lane_off", bad)

    with pytest.raises(ValueError):
        ssh._script_text("lane_list", {"NAME": "x"})


# ------------------------------------------------------------------ the service: start, adjust, park, stop, clean
def measured(monkeypatch, down=57.0):
    seen = []
    monkeypatch.setattr(lanes.bandwidth, "measure", lambda tid: seen.append(tid) or ({"down": down}, ""))
    return seen


def test_a_lane_is_started_with_the_share_of_the_two_capacities_and_told_in_one_event(monkeypatch):
    writable(monkeypatch)
    w = world("ln1")
    sid = add_standby(w)
    calls = fake_actions(monkeypatch)
    seen = measured(monkeypatch)
    r = lanes.start("ln1", manual=True)

    assert r["pct"] == 43 and r["cap_p"] == 76.0 and r["cap_s"] == 57.0 and seen == [sid]                                  # the standby was measured (not the tunnel)
    assert calls == [("lane_on", {"NAME": "ln1-sb", "PROB": "0.43000000", "MAP": f"{w['port']}:{standby.shifted(w['port'])}", "PROTOS": "tcp"})]
    lane = lane_of(sid)
    assert lane["state"] == "on" and lane["pct"] == 43 and lane["manual"] is True and lane["map"] == f"{w['port']}:{standby.shifted(w['port'])}"
    wish = lanes._wish("ln1")
    assert wish["want"] is True and wish["manual"] is True and wish["cap_s"] == 57.0 and wish["cap_p"] == 76.0
    assert w["iran"] in st.get("lanes")["touched"]

    with session() as db:
        ev = db.scalars(select(Event).where(Event.ref == "ln1")).all()
        assert len(ev) == 1 and ev[0].level == "info" and "57 % + 43 % (capacity 76 + 57 Mb/s)" in ev[0].text and "Open connections are not touched" in ev[0].text

    st_ = lanes.status("ln1")
    assert st_["state"] == "sharing" and st_["pct"] == 43 and st_["kind"] == "chisel:tls" and st_["cap_p"] == 76.0 and st_["cap_s"] == 57.0 and st_["manual"] is True

    seen.clear()
    r = lanes.start("ln1", manual=True)                                                                                      # again: the measurement is kept for six hours
    assert seen == [] and r["pct"] == 43


def test_a_lane_is_refused_when_it_would_do_harm_and_says_why(monkeypatch):
    w = world("ln2")
    add_standby(w)
    fake_actions(monkeypatch)
    measured(monkeypatch)

    with pytest.raises(ValueError, match="read-only"):
        lanes.start("ln2")

    writable(monkeypatch)

    with pytest.raises(ValueError, match="no standby"):
        lanes.start("nothing")

    sick = world("ln3")
    add_standby(sick, state="down")

    with pytest.raises(ValueError, match="the standby is down"):
        lanes.start("ln3")

    cut = world("ln4", state="down")
    add_standby(cut)

    with pytest.raises(ValueError, match="the tunnel is down"):
        lanes.start("ln4")

    handed = world("ln5")
    add_standby(handed, use={"since": clock(), "map": "1:2", "protos": "tcp", "move": ""})

    with pytest.raises(ValueError, match="handover"):
        lanes.start("ln5")

    unknown = world("ln6", bw=0)
    add_standby(unknown)

    with pytest.raises(ValueError, match="capacity is not measured"):
        lanes.start("ln6")

    weak = world("ln7")
    add_standby(weak)
    measured(monkeypatch, down=9.0)

    with pytest.raises(ValueError, match="too little"):
        lanes.start("ln7")

    broken = world("ln8")
    add_standby(broken)
    monkeypatch.setattr(lanes.bandwidth, "measure", lambda tid: (_ for _ in ()).throw(ValueError("a server of this tunnel is busy right now")))

    with pytest.raises(ValueError, match="could not be measured: a server of this tunnel is busy"):
        lanes.start("ln8")

    with pytest.raises(ValueError, match="could not be measured a short while ago"):
        lanes.start("ln8")                                                                                                   # (it is not hammered)

    assert lane_of(standby._view("ln8")["s"]["id"]) is None


def test_a_lane_that_the_server_did_not_put_leaves_nothing_behind(monkeypatch):
    writable(monkeypatch)
    w = world("ln9")
    sid = add_standby(w)
    calls = fake_actions(monkeypatch, fail=True)
    measured(monkeypatch)

    with pytest.raises(RuntimeError, match="the lane could not be put: the lane rules were not put: the kernel did not take the rule"):
        lanes.start("ln9", manual=True)

    assert [c[0] for c in calls] == ["lane_on", "lane_off"] and calls[1][1]["PARK"] == "0"                                    # a half-put lane is taken off again
    assert lane_of(sid) is None and lanes._wish("ln9").get("want") is None

    with session() as db:
        assert db.scalars(select(Event).where(Event.ref == "ln9")).all() == []


def test_the_share_is_rewritten_in_place_and_the_lane_keeps_its_age(monkeypatch):
    writable(monkeypatch)
    w = world("ad1")
    sid = add_standby(w, lane=lane_rec(w, since=5000))
    calls = fake_actions(monkeypatch)
    lanes.adjust("ad1", 60)
    assert calls == [("lane_on", {"NAME": "ad1-sb", "PROB": "0.60000000", "MAP": f"{w['port']}:{standby.shifted(w['port'])}", "PROTOS": "tcp"})]
    lane = lane_of(sid)
    assert lane["pct"] == 60 and lane["state"] == "on" and clock() - lane["since"] >= 4999 and clock() - lane["changed"] <= 5

    failing = fake_actions(monkeypatch, fail=True)

    with pytest.raises(RuntimeError):
        lanes.adjust("ad1", 20)

    assert lane_of(sid)["pct"] == 60 and [c[0] for c in failing] == ["lane_on"]                                                # the old share stays when the new one was not taken


def test_a_lane_is_parked_the_careful_way_and_cleaned_later_when_nothing_is_left_on_it(monkeypatch):
    writable(monkeypatch)
    w = world("pk1")
    sid = add_standby(w, lane=lane_rec(w))
    lanes._set_wish("pk1", {"want": True, "pct": 43})
    calls = fake_actions(monkeypatch, off="LANE_OFF {n} 0 2 4")
    assert lanes.park("pk1", "the second tunnel fell") is True
    assert calls == [("lane_off", {"NAME": "pk1-sb", "PARK": "1"})]
    assert lane_of(sid)["state"] == "parked" and lane_of(sid)["parked_at"] and lanes._wish("pk1")["want"] is True              # the wish stays: it is put again
    assert lanes.park("pk1") is False and len(calls) == 1                                                                       # (again: nothing happens twice)
    assert lanes.status("pk1")["state"] == "paused"

    lanes.clean("pk1")                                                                                                           # four connections are still on the second tunnel: the parked rules stay
    assert calls[-1] == ("lane_off", {"NAME": "pk1-sb", "PARK": "2"}) and lane_of(sid)["state"] == "parked"

    fake_actions(monkeypatch, off="LANE_OFF {n} 0 0 0")
    lanes.clean("pk1")
    assert lane_of(sid) is None                                                                                                  # nothing is left: the record goes with the rules


def test_stopping_a_lane_parks_it_forgets_the_wish_tells_once_and_keeps_the_next_automatic_one_away_for_an_hour(monkeypatch):
    writable(monkeypatch)
    w = world("sp1")
    sid = add_standby(w, lane=lane_rec(w))
    lanes._set_wish("sp1", {"want": True, "manual": True, "pct": 43})
    calls = fake_actions(monkeypatch)
    lanes.stop("sp1", "stopped by you")
    assert calls == [("lane_off", {"NAME": "sp1-sb", "PARK": "1"})] and lane_of(sid)["state"] == "parked"
    wish = lanes._wish("sp1")
    assert wish["want"] is False and wish["manual"] is False and clock() - wish["stopped_at"] <= 5

    with session() as db:
        ev = db.scalars(select(Event).where(Event.ref == "sp1")).all()
        assert len(ev) == 1 and ev[0].text.startswith("Lane of sp1 ended.")

    lanes.stop("sp1")                                                                                                            # again: nothing to end, no second event, no second SSH
    assert len(calls) == 1

    with session() as db:
        assert len(db.scalars(select(Event).where(Event.ref == "sp1")).all()) == 1


def test_after_the_way_back_of_the_standby_the_lane_rules_are_put_again_at_once(monkeypatch):
    writable(monkeypatch)
    w = world("rf1")
    add_standby(w, lane=lane_rec(w, pct=40))
    calls = fake_actions(monkeypatch)
    lanes.refresh("rf1")
    assert calls == [("lane_on", {"NAME": "rf1-sb", "PROB": "0.40000000", "MAP": f"{w['port']}:{standby.shifted(w['port'])}", "PROTOS": "tcp"})]
    calls.clear()
    w2 = world("rf2")
    add_standby(w2)
    lanes.refresh("rf2")
    lanes.refresh("nothing")
    assert calls == []                                                                                                           # no lane: no SSH


# ------------------------------------------------------------------ the job
def full_for(name, seconds=300, state="full"):
    """The Doctor's detector (capacity.py) has called the tunnel `state` for `seconds`."""
    capacity._VIEW["tunnels"][name] = {"state": state, "util": 0.9, "n": 5, "cap": 76.0, "since": clock() - seconds}

    if state == "full":
        lanes._FULL[name] = time.time() - seconds
    else:
        lanes._FULL.pop(name, None)


def idle_ring(name, value=20.0, lane=10.0, n=45):
    lanes._RING[name] = __import__("collections").deque(ring([value] * n, lane=lane))


def test_the_job_starts_a_lane_when_the_tunnel_has_been_nearly_full_and_only_then(monkeypatch):
    writable(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    w = world("jb1")
    sid = add_standby(w)
    calls = fake_actions(monkeypatch)
    measured(monkeypatch)
    lanes.set_enabled(True)

    full_for("jb1", 30, state="warm")                                                                                            # the detector says "warm": room
    lanes._step("jb1", {})
    assert calls == [] and lanes._NOTE["jb1"].startswith("the tunnel has room")

    full_for("jb1", 100)                                                                                                         # FULL, but only for 100 s: the Doctor's own story (3 minutes) goes first
    lanes._step("jb1", {})
    assert calls == []

    full_for("jb1", 300)                                                                                                         # FULL for five minutes
    monkeypatch.setattr(autopilot, "_BUSY", {"capacity jb1": 5})                                                                 # ... while the Doctor's story for it works (the swap without a cut, the move): a lane waits
    lanes._step("jb1", {})
    assert calls == []
    monkeypatch.setattr(autopilot, "_BUSY", {})
    lanes._step("jb1", {})
    assert [c[0] for c in calls] == ["lane_on"] and lane_of(sid)["state"] == "on" and lane_of(sid)["manual"] is False and lanes._wish("jb1")["want"] is True      # (the standby was measured first, in the same turn)
    calls.clear()
    full_for("jb1", 900, state="ok")                                                                                             # the tunnel is calm now because the lane works: that is no reason to end it
    lanes._step("jb1", {})
    assert calls == []                                                                                                           # it works: left alone


def test_the_job_does_nothing_while_lanes_are_off_the_doctor_is_off_or_the_numbers_are_missing(monkeypatch):
    writable(monkeypatch)
    w = world("jb2")
    add_standby(w)
    calls = fake_actions(monkeypatch)
    measured(monkeypatch)
    full_for("jb2")

    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    lanes._step("jb2", {})
    assert calls == []                                                                                                           # the owner has not switched lanes on (the default)

    lanes.set_enabled(True)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    lanes._step("jb2", {})
    assert calls == []                                                                                                           # the Doctor is off

    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    capacity._VIEW["tunnels"] = {}
    lanes._FULL.clear()
    lanes._step("jb2", {})
    assert calls == []                                                                                                           # the detector has not looked yet (a minute after a start of the panel): nothing is guessed
    assert st.get("lanes")["on"] is True and st.DEFAULTS["lanes"]["on"] is False


def test_the_job_measures_the_second_tunnel_first_and_parks_the_lane_when_it_falls_and_puts_it_again(monkeypatch):
    writable(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    lanes.set_enabled(True)
    w = world("jb3")
    sid = add_standby(w, lane=lane_rec(w))
    lanes._set_wish("jb3", {"want": True, "pct": 43, "cap_s": 57.0, "cap_s_at": clock(), "cap_s_kind": "chisel:tls"})
    calls = fake_actions(monkeypatch)
    measured(monkeypatch)
    idle_ring("jb3", 40.0, 30.0)

    with session() as db:
        db.get(Tunnel, sid).state = "down"                                                                                     # the second tunnel falls
        db.get(Tunnel, sid).state_since = clock() - 5

    lanes._step("jb3", {})
    assert calls == [("lane_off", {"NAME": "jb3-sb", "PARK": "1"})] and lane_of(sid)["state"] == "parked" and lanes._wish("jb3")["want"] is True

    calls.clear()
    lanes._step("jb3", {})
    assert calls == []                                                                                                           # still down: it waits

    with session() as db:
        db.get(Tunnel, sid).state = "up"
        db.get(Tunnel, sid).state_since = clock() - 20

    lanes._step("jb3", {})
    assert calls == []                                                                                                           # up for 20 s: not yet (a minute)

    with session() as db:
        db.get(Tunnel, sid).state_since = clock() - 90

    lanes._step("jb3", {})
    assert [c[0] for c in calls] == ["lane_on"] and lane_of(sid)["state"] == "on"                                              # put again, with the share of the two capacities


def test_the_job_ends_an_automatic_lane_when_the_load_stays_low_or_lanes_are_switched_off_but_not_a_lane_made_by_hand(monkeypatch):
    writable(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    lanes.set_enabled(True)
    auto = world("jb4")
    sid = add_standby(auto, lane=lane_rec(auto, since=4000))
    lanes._set_wish("jb4", {"want": True, "pct": 43})
    hand = world("jb5")
    sid5 = add_standby(hand, lane=lane_rec(hand, since=4000, manual=True))
    lanes._set_wish("jb5", {"want": True, "manual": True, "pct": 43})
    calls = fake_actions(monkeypatch)
    lanes._RING["jb4"] = __import__("collections").deque(ring([20.0] * 45, lane=10.0))
    lanes._RING["jb5"] = __import__("collections").deque(ring([20.0] * 45, lane=10.0))
    lanes._step("jb4", {})
    lanes._step("jb5", {})
    assert calls == [("lane_off", {"NAME": "jb4-sb", "PARK": "1"})] and lane_of(sid)["state"] == "parked" and lane_of(sid5)["state"] == "on"          # half an hour at 30 of 76: not needed; the one made by hand stays
    assert lanes._wish("jb4")["want"] is False and clock() - lanes._wish("jb4")["stopped_at"] <= 5

    calls.clear()
    lanes.set_enabled(False)                                                                                                     # the owner switches lanes off
    lanes._step("jb5", {})
    assert calls == []                                                                                                           # ... a lane he made by hand stays until Stop


def test_the_job_cleans_the_parked_rules_of_an_ended_lane(monkeypatch):
    writable(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    w = world("jb6")
    sid = add_standby(w, lane=lane_rec(w, state="parked", parked_at=clock() - 1000))
    calls = fake_actions(monkeypatch, off="LANE_OFF {n} 0 0 0")
    lanes._step("jb6", {})
    assert calls == [("lane_off", {"NAME": "jb6-sb", "PARK": "2"})] and lane_of(sid) is None


def test_a_failing_server_makes_the_job_wait_a_minute_instead_of_asking_every_ten_seconds(monkeypatch):
    writable(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    lanes.set_enabled(True)
    w = world("jb7")
    add_standby(w, lane=lane_rec(w, state="parked", pct=43))
    lanes._set_wish("jb7", {"want": True, "pct": 43})
    asked = []

    def boom(node, name, params=None, timeout=90):
        asked.append(name)
        raise ssh.SSHError("the Iran server does not answer")

    monkeypatch.setattr(lanes, "run_action", boom)
    lanes._safe_step(("jb7", {}))
    lanes._safe_step(("jb7", {}))
    assert asked == ["lane_on", "lane_off"] and "does not answer" in lanes._NOTE["jb7"]                                       # (the failed put is taken off again; the second turn did not ask at all)


def test_the_samples_of_the_users_come_from_the_live_numbers_one_per_read():
    w = world("sm1")
    sid = add_standby(w, lane=lane_rec(w))
    p, s = standby._view("sm1")["p"], standby._view("sm1")["s"]
    now = time.time()
    view = {str(w["id"]): {"down": 41.0, "up": 2.0, "at": now - 5, "age": 5.0}, str(sid): {"down": 12.0, "up": 1.0, "at": now - 5, "age": 5.0}}
    lanes._sample("sm1", p, s, s["lane"], view)
    lanes._sample("sm1", p, s, s["lane"], view)                                                                                   # (the same read: not counted twice)
    assert [(round(x[1]), round(x[2])) for x in lanes._RING["sm1"]] == [(41, 12)]
    lanes._sample("sm2", p, s, s["lane"], {str(w["id"]): {"down": 41.0, "at": now, "age": 1.0}})                                 # the lane is on and its own rate is not known: no sample (a zero would end it)
    assert "sm2" not in lanes._RING
    lanes._sample("sm3", p, s, None, {str(w["id"]): {"down": 41.0, "at": now - 200, "age": 200.0}})                              # too old
    assert "sm3" not in lanes._RING


# ------------------------------------------------------------------ the standby and its lane
def test_removing_a_standby_takes_its_lane_rules_off_first_and_keeps_a_standby_it_cannot_clean(monkeypatch):
    writable(monkeypatch)
    removed = []
    monkeypatch.setattr(builder, "delete_tunnel", lambda tunnel_id, keep_record=False, trash=False, skip_offline=False: removed.append((tunnel_id, skip_offline)) or ["removed"])
    w = world("rm1")
    sid = add_standby(w, lane=lane_rec(w))
    calls = fake_actions(monkeypatch)
    standby.remove_standby("rm1")
    assert calls == [("lane_off", {"NAME": "rm1-sb", "PARK": "0"})] and removed == [(sid, False)]                              # the lane goes first: it would send new connections to a port nobody listens on

    removed.clear()
    stuck = world("rm2")
    add_standby(stuck, lane=lane_rec(stuck))

    def refuse(node, name, params=None, timeout=90):
        raise ssh.SSHError("the Iran server does not answer")

    monkeypatch.setattr(lanes, "run_action", refuse)

    with pytest.raises(ssh.SSHError):
        standby.remove_standby("rm2")

    assert removed == []                                                                                                         # the rules cannot be taken off: the standby is KEPT

    off = world("rm3")
    add_standby(off, lane=lane_rec(off))

    with session() as db:
        db.get(Server, off["iran"]).online = False

    monkeypatch.setattr(lanes, "run_action", lambda *a, **k: pytest.fail("the Iran server is off: nothing to ask"))
    standby.remove_standby("rm3")
    assert len(removed) == 1 and removed[0][1] is True


def test_the_wish_follows_a_rename_and_goes_with_a_tunnel_the_owner_removes(monkeypatch):
    lanes._set_wish("old1", {"want": True, "pct": 40})
    lanes._RING["old1"] = __import__("collections").deque(ring([1.0] * 3))
    lanes.renamed("old1", "new1")
    assert lanes._wish("old1") == {} and lanes._wish("new1")["pct"] == 40 and "new1" in lanes._RING
    lanes.forget("new1")
    assert lanes._wish("new1") == {} and "new1" not in lanes._RING


def test_the_standbys_way_back_frees_the_lane(monkeypatch):
    writable(monkeypatch)
    w = world("gb1", state="up")
    add_standby(w, use={"since": clock() - 100, "map": f"{w['port']}:{standby.shifted(w['port'])}", "protos": "tcp", "move": ""}, lane=lane_rec(w))
    calls = fake_actions(monkeypatch)
    monkeypatch.setattr(standby.tunnels, "check_tunnel", lambda *a, **k: None)
    standby.give_back("gb1", "up for a minute")
    assert [c[0] for c in calls] == ["standby_off", "lane_on"]                                                                    # the RETURN it parks above the lane rule is taken off at once


# ------------------------------------------------------------------ the reconcile
def test_the_lane_rules_on_a_server_are_compared_with_what_the_panel_believes(monkeypatch):
    writable(monkeypatch)
    w = world("rc1")
    sid = add_standby(w, lane=lane_rec(w, pct=40))
    lanes._touch(w["iran"])
    calls = fake_actions(monkeypatch)
    to = standby.shifted(w["port"])
    answers = {"out": "LANE ghost-sb tcp 80 20080 0.39999999991 0\nPARKED gone-sb tcp 81\nNOW 1\n"}
    monkeypatch.setattr(lanes, "run_script", lambda node, name, params=None, timeout=40: answers["out"])
    lanes.reconcile()
    assert ("lane_off", {"NAME": "ghost-sb", "PARK": "0"}) in calls and ("lane_off", {"NAME": "gone-sb", "PARK": "0"}) in calls        # rules of a standby that does not exist: off (nothing is translated by them any more)
    assert ("lane_on", {"NAME": "rc1-sb", "PROB": "0.40000000", "MAP": f"{w['port']}:{to}", "PROTOS": "tcp"}) in calls                  # the lane is believed on but the server has none (a reboot): put again

    calls.clear()
    lanes._STATE["listed"].clear()
    answers["out"] = f"LANE rc1-sb tcp {w['port']} {to} 0.39999999991 0\nNOW 1\n"
    lanes.reconcile()
    assert calls == []                                                                                                           # what stands is what is believed

    lanes._STATE["listed"].clear()
    answers["out"] = f"LANE rc1-sb tcp {w['port']} {to} 0.39999999991 1\nNOW 1\n"
    lanes.reconcile()
    assert calls == [("lane_on", {"NAME": "rc1-sb", "PROB": "0.40000000", "MAP": f"{w['port']}:{to}", "PROTOS": "tcp"})]            # shadowed by the way back's RETURN: freed

    calls.clear()
    lanes._STATE["listed"].clear()
    lanes._set_lane(sid, {**lane_rec(w), "state": "parked"})
    answers["out"] = f"LANE rc1-sb tcp {w['port']} {to} 0.39999999991 0\nNOW 1\n"
    lanes.reconcile()
    assert calls == [("lane_off", {"NAME": "rc1-sb", "PARK": "1"})]                                                              # rules that stand while the lane is parked: parked the careful way

    calls.clear()
    lanes._STATE["listed"].clear()
    lanes._set_lane(sid, {**lane_rec(w), "state": "on"})

    with session() as db:
        db.get(Tunnel, sid).state = "down"                                                                                       # the second tunnel is down: the rules are not put again on a port nobody listens on

    answers["out"] = "NOW 1\n"
    lanes.reconcile()
    assert calls == []


# ------------------------------------------------------------------ the live numbers, the measurement, the lists
def test_the_live_numbers_read_a_standby_only_while_a_lane_is_on(monkeypatch):
    w = world("lv1")
    sid = add_standby(w)

    def ids():
        found = set()

        for e in livebw.targets():
            for v in list(e["cards"].values()) + list(e["socks"].values()):
                found |= set(v)

        return found

    assert w["id"] in ids() and sid not in ids()                                                                                 # an idle standby is not read (it is not in any list)
    lanes._set_lane(sid, lane_rec(w))
    assert sid in ids()


def test_the_test_of_a_standby_makes_no_event_about_a_tunnel_nobody_sees():
    w = world("bwx", bw=0)
    sid = add_standby(w)

    for _ in range(3):
        bandwidth._store(sid, 0.1, None, "link", None)                                                                           # a test that moved nothing, three times: a drop for a tunnel in the list ...
        bandwidth._store(w["id"], 0.1, None, "link", None)

    with session() as db:
        refs = [e.ref for e in db.scalars(select(Event))]

    assert refs.count("bwx") >= 1 and "bwx-sb" not in refs                                                                       # ... and not a word about the standby


def test_a_standby_is_in_no_list_and_the_lane_of_a_tunnel_is_in_its_row(client, monkeypatch):
    writable(monkeypatch)
    w = world("ap1")
    sid = add_standby(w)
    rows = client.get("/api/tunnels").json()
    assert [t["name"] for t in rows] == ["ap1"] and rows[0]["lane"]["state"] == "off" and rows[0]["lane"]["on"] is False        # off by default
    assert client.get("/api/lanes/settings").json() == {"ok": True, "on": False}
    assert client.put("/api/lanes/settings", json={"on": True}).json()["on"] is True and client.get("/api/lanes/settings").json()["on"] is True
    row = client.get("/api/tunnels").json()[0]
    assert row["lane"]["state"] == "idle" and row["lane"]["on"] is True                                                         # a ready standby and nothing to do
    assert client.get(f"/api/tunnels/{w['id']}").json()["lane"]["state"] == "idle" and client.get(f"/api/tunnels/{w['id']}/lane").json()["state"] == "idle"
    assert client.get(f"/api/tunnels/{sid}/lane").status_code == 404 and client.get("/api/tunnels/9999/lane").status_code == 404      # a standby has no lane of its own

    calls = fake_actions(monkeypatch)
    measured(monkeypatch)
    r = client.post(f"/api/tunnels/{w['id']}/lane/start")
    assert r.status_code == 200 and r.json()["state"] == "sharing" and r.json()["pct"] == 43 and calls[0][0] == "lane_on"
    assert client.get("/api/tunnels").json()[0]["lane"]["state"] == "sharing"
    r = client.post(f"/api/tunnels/{w['id']}/lane/stop")
    assert r.status_code == 200 and r.json()["state"] == "paused" and calls[-1] == ("lane_off", {"NAME": "ap1-sb", "PARK": "1"})

    monkeypatch.setattr(get_settings(), "read_only", True)
    assert client.post(f"/api/tunnels/{w['id']}/lane/start").status_code == 400 and client.post(f"/api/tunnels/{w['id']}/lane/stop").status_code == 400
    monkeypatch.setattr(get_settings(), "read_only", False)
    sick = world("ap2", state="down")
    add_standby(sick)
    r = client.post(f"/api/tunnels/{sick['id']}/lane/start")
    assert r.status_code == 400 and "the tunnel is down" in r.json()["error"]

    monkeypatch.setattr(lanes, "run_action", lambda *a, **k: "LANE_FAIL no iptables")
    r = client.post(f"/api/tunnels/{w['id']}/lane/start")
    assert r.status_code == 502                                                                                                   # the server did not take the rules


def test_the_states_of_a_lane_in_the_tunnel_window(monkeypatch):
    on = world("sy1")
    world("sy2")
    add_standby(on)
    assert lanes.status("sy1")["state"] == "off"                                                                                 # lanes are off, nothing is shown
    lanes.set_enabled(True)
    assert lanes.status("sy1")["state"] == "idle" and lanes.status("sy2")["state"] == "waiting"                                   # sy2 has no standby
    sid = standby._view("sy1")["s"]["id"]
    lanes._set_lane(sid, lane_rec(on, pct=60))
    s = lanes.status("sy1")
    assert s["state"] == "sharing" and s["pct"] == 60 and s["kind"] == "chisel:tls" and s["since"]
    lanes._set_lane(sid, {**lane_rec(on), "state": "parked"})
    lanes._NOTE["sy1"] = "the second tunnel is not up: every new connection goes to the tunnel until it is"
    s = lanes.status("sy1")
    assert s["state"] == "paused" and "not up" in s["why"]


def test_the_tunnel_window_the_doctor_page_and_the_job_know_the_lanes():
    import inspect

    from nikatel import main

    web = Path(__file__).resolve().parent.parent / "nikatel" / "web"
    app = (web / "app.js").read_text(encoding="utf-8")
    fa = (web / "i18n.fa.doctor.js").read_text(encoding="utf-8")
    assert "function tunLane(t)" in app and "tunBwBox(t) + tunLane(t) + tunGrid(t)" in app                                     # a bar under the standby's
    assert all(f"window.{f} = async" in app for f in ("tunLaneStart", "tunLaneStop", "apLanes")) and "/lane/start" in app and "/lane/stop" in app and "/api/lanes/settings" in app
    assert 'class="dr-lanes"' in app and "Add capacity by itself" in app and "${modes}${lanesRow}" in app                           # the owner's switch on the Doctor page
    assert "Lane · Sharing {} % + {} %" in fa and "Add capacity by itself" in fa and "Share now" in fa
    assert 'jobs.register("lanes", 10, lanes.run)' in inspect.getsource(main._register_jobs)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_bar_of_the_lane_is_drawn_only_when_it_has_something_to_say():
    web = Path(__file__).resolve().parent.parent / "nikatel" / "web"
    js = (web / "app.js").read_text(encoding="utf-8")
    fn = "function tunLane(t) {" + js.split("function tunLane(t) {", 1)[1].split("\nfunction tunBanner", 1)[0]
    code = ("const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');\n" + fn + "\n"
            "const bar = l => tunLane({id: 7, lane: l});\n"
            "const out = {off: bar({state: 'off'}), waiting: bar({state: 'waiting'}), idleLow: bar({state: 'idle', cap_p: 76, use_p: 20}), idleNear: bar({state: 'idle', cap_p: 76, use_p: 69}),\n"
            "  sharing: bar({state: 'sharing', pct: 43, kind: 'chisel:tls', cap_p: 76, cap_s: 57, use_p: 41.2, use_s: 18.7, since: 1700000000}), sharingNoLive: bar({state: 'sharing', pct: 43, kind: 'bp:wssmux', cap_p: 76, cap_s: 57}),\n"
            "  paused: bar({state: 'paused', why: 'the second tunnel is not up'}), none: tunLane({id: 7})};\n"
            "console.log(JSON.stringify(out));")
    out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert out["off"] == "" and out["waiting"] == "" and out["idleLow"] == "" and out["none"] == ""                              # nothing to say: nothing drawn (like an idle standby)
    assert "Lane · Nearly full" in out["idleNear"] and "tunLaneStart(7)" in out["idleNear"] and "69 of 76 Mb/s are used (91 % of the capacity)" in out["idleNear"]
    assert "Lane · Sharing 57 % + 43 %" in out["sharing"] and "57 % to this tunnel · 43 % to chisel:tls · by capacity 76 + 57 Mb/s · users now 41 + 19 Mb/s" in out["sharing"] and "tunLaneStop(7)" in out["sharing"]
    assert "users now" not in out["sharingNoLive"] and "43 % to bp:wssmux" in out["sharingNoLive"]
    assert "Lane · Paused" in out["paused"] and "the second tunnel is not up" in out["paused"] and "tunLaneStop(7)" in out["paused"]


def test_the_job_has_nothing_to_do_without_tunnels_and_a_look_at_each_tunnel_otherwise(monkeypatch):
    from nikatel import jobs

    monkeypatch.setattr(jobs, "stopping", lambda: False)
    seen = []
    monkeypatch.setattr(lanes, "_step", lambda name, view: seen.append(name))
    monkeypatch.setattr(lanes, "reconcile", lambda: None)
    world("jn1")
    lanes.run()
    assert seen == []                                                                                                            # lanes off, no wish, no lane: not even a look
    lanes.set_enabled(True)
    standby._mark("jn1", True)
    lanes.run()
    assert seen == ["jn1"]
    lanes._set_wish("jn2", {"want": True})
    seen.clear()
    lanes.run()
    assert seen == ["jn1", "jn2"]
