"""The warm standby of a tunnel (the owner, 2026-10-17: "bring the delay down to the minimum - do items 1 to 9"; item 9, services/standby.py, docs/standby.md):

  the ports       the users' port P is 20000 + P on the Iran server (P - 20000 above 45535); the mapping, the name (a GRE card is 15 characters), the type (another carrier than the tunnel's)
  the decisions   `decide` - pure: build, handover, back, restart, rebuild, drop - in the Doctor's modes
  the handover    one reviewed action on the Iran server (a tagged NAT redirect), run here for real against a fake iptables: idempotent, the way back, nothing else touched
  the rails       never a cut of the tunnel: a standby that falls makes no event, no alert, no repair; a tunnel whose ports are on the standby is judged without the look from another Iran server
  the page        the switch, the state, Back now, the Moves tab"""
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from nikatel import jobs, ssh
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, Event, Server, Tunnel, TunnelCheck, is_standby
from nikatel.services import (
    actions,
    alerts,
    builder,
    doctor,
    knowledge,
    report,
    sentinel,
    standby,
)
from nikatel.services import autopilot as ap
from nikatel.services import build_other as bo
from nikatel.services import tunnels as tn

NOW = int(time.time())          # (for the pure rules: every time in them is relative to this one)


def clock() -> int:
    """The time NOW, for what lives in the database and is read by the code with the real clock (the module is imported long before its last test runs)."""
    return int(time.time())


def writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)


def world(name, state="up", since=300, **kw):
    """Two servers and a Backhaul tunnel between them (forwarding one port). Returns the ids and the port."""
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        port = 8300 + n
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9300 + n, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, state_since=clock() - since, last_check_at=clock() - 2,
                   forwarded_ports=[port], foreign_tunnel_ip=f"10.8.{n}.2", **kw)
        db.add(t)
        db.flush()
        return {"id": t.id, "name": name, "iran": ir.id, "foreign": fr.id, "port": port}


def add_standby(w, state="up", since=300, use=None, name=None, ports=None, mapping=None, iran=None, **kw):
    """The standby row of world `w`: a Chisel TLS tunnel on the shifted port, switched off in the list."""
    name = name or w["name"] + "-sb"
    shifted = ports if ports is not None else [standby.shifted(w["port"])]
    rec = {"standby_of": w["name"], "port_mapping": mapping if mapping is not None else f"{shifted[0]}:{w['port']}", "kind": "chisel", "built_by": "nikatel-panel", **({"use": use} if use else {})}

    with session() as db:
        t = Tunnel(name=name, type="chisel", transport="wss", enabled=False, port=9650, iran_server_id=iran or w["iran"], foreign_server_id=w["foreign"], state=state, state_since=clock() - since, last_check_at=clock() - 2,
                   forwarded_ports=shifted, record=rec, **kw)
        db.add(t)
        db.flush()
        return t.id


def P(state="up", since=300, **kw):
    return {"state": state, "state_since": NOW - since, "last_check_at": NOW - 2, "iran_id": 1, "foreign_id": 2, "off": False, **kw}


def S(state="up", since=300, **kw):
    return {"state": state, "state_since": NOW - since, "stale": "", "use": None, **kw}


# ------------------------------------------------------------------ the ports and the names
def test_the_users_port_is_shifted_by_20000_and_below_that_above_45535():
    assert standby.shifted(80) == 20080 and standby.shifted(443) == 20443 and standby.shifted(8072) == 28072
    assert standby.shifted(45535) == 65535 and standby.shifted(45536) == 25536 and standby.shifted(65535) == 45535
    assert standby.shifted(0) is None and standby.shifted(-5) is None
    assert standby.shifted(standby.shifted(8072) - 20000) == 28072                                              # (shifting is only ever done once: the shifted port is a port of the standby)


def test_the_mapping_keeps_the_port_on_the_foreign_side_and_refuses_what_cannot_be_shifted():
    text, mapping = standby.shift_mapping("443=2096, 80", set())
    assert text == "20443:2096,20080:80" and mapping == {443: 20443, 80: 20080}
    assert standby.norm_ports(text) == text                                                                       # (the form the builders keep: what the standby row says is what a rebuild compares with)
    assert standby.shift_mapping("10000-10002", set())[0] == "30000:10000,30001:10001,30002:10002"

    for bad, taken, word in (("80, 20080", set(), "20080"), ("80", {20080}, "already used"), ("19400", set(), "already used"), ("1-201", set(), "at most 200")):
        with pytest.raises(ValueError, match=word):
            standby.shift_mapping(bad, taken)

    with pytest.raises(ValueError):
        standby.shift_mapping("", set())                                                                           # (no port: the builder's own sentence)


def test_the_name_of_a_standby_fits_a_gre_card_and_is_unique():
    assert standby.standby_name("de-ir1", set()) == "de-ir1-sb" and standby.standby_name("de-ir1", {"de-ir1-sb"}) == "de-ir1-sb2"
    long = standby.standby_name("abcdefghijklmno", set())
    assert long == "abcdefghijkl-sb" and len(long) == 15 and standby.standby_name("abcdefghijklmno", {long}) == "abcdefghijk-sb2"
    assert standby.standby_name("abcdefghijk-lmn", set()) == "abcdefghijk-sb"                                       # (no double dash where the cut falls on one)

    for n in (long, standby.standby_name("abcdefghijklmno", {long}), "de-ir1-sb", standby.standby_name("x", set())):
        assert builder.NAME_RE.match(n), n


def test_the_standby_has_another_carrier_than_the_tunnel_and_what_is_known_to_connect_comes_first():
    assert [standby.carrier(k) for k in ("tcpmux", "wssmux", "wg:tcpmux", "fou:tcpmux", "bp:wssmux", "bpd:pck", "chisel", "chisel:tls", "chisel:fwd", "paqet")] == ["gre", "gre", "udp-link", "udp-link", "bp", "bpd", "chisel", "chisel", "chisel", "paqet"]
    good = lambda kind, score=70: knowledge.merge(None, {"kind": kind, "ok": True, "score": score, "latency_ms": 120.0, "loss_pct": 0.0}, "latency", NOW)
    failed = lambda kind: knowledge.merge(None, {"kind": kind, "ok": False, "error": "no data passes"}, "latency", NOW)
    kinds = ["wssmux", "bp:wssmux", "chisel:tls", "paqet"]

    assert standby.choose_kind(kinds, "tcpmux", {}, NOW)[0] == "bp:wssmux"                                           # nothing known: the first kind of ANOTHER carrier (the GRE kinds fall with the GRE link)
    pair = {"kinds": {"chisel:tls": good("chisel:tls"), "wssmux": good("wssmux", 90)}}
    kind, why = standby.choose_kind(kinds, "tcpmux", pair, NOW)
    assert kind == "chisel:tls" and "known to connect" in why and "another carrier" in why                          # known and different beats unknown and different, and known with the same carrier
    kind, why = standby.choose_kind(["wssmux"], "tcpmux", {"kinds": {"wssmux": good("wssmux")}}, NOW)
    assert kind == "wssmux" and "the same carrier" in why                                                            # only the same carrier is left: better than none
    assert standby.choose_kind(kinds, "bp:wssmux", {}, NOW)[0] == "wssmux"                                           # a BackPack tunnel: the first kind that is not BackPack
    assert standby.choose_kind(kinds, "tcpmux", {"kinds": {"bp:wssmux": failed("bp:wssmux")}}, NOW)[0] == "chisel:tls"            # what failed on this pair lately is not chosen
    assert standby.choose_kind(kinds, "tcpmux", {}, NOW, skip={"bp:wssmux", "chisel:tls"})[0] == "paqet"                          # nor what a standby build failed with lately
    assert standby.choose_kind(kinds, "tcpmux", {}, NOW, allowed=lambda k: k == "wssmux")[0] == "wssmux"                          # the licence decides what may be built
    kind, why = standby.choose_kind(["tcpmux"], "tcpmux", {}, NOW)
    assert kind == "" and "no other tunnel type" in why                                                              # the tunnel's own type is never its standby


def test_a_standby_on_the_carrier_of_its_tunnel_is_replaced_only_by_a_known_type_of_another_carrier():
    """(the drill with real tunnels, 2026-10-18: the Doctor brought a swapped tunnel back to Backhaul over GRE while its standby was Backhaul over GRE too - a GRE filter would have cut both)"""
    good = lambda kind, score=70: knowledge.merge(None, {"kind": kind, "ok": True, "score": score, "latency_ms": 120.0, "loss_pct": 0.0}, "latency", NOW)
    failed = lambda kind: knowledge.merge(None, {"kind": kind, "ok": False, "error": "no data passes"}, "latency", NOW)
    kinds = ["tcpmux", "wssmux", "bp:wssmux", "chisel:tls"]
    pair = {"kinds": {"wssmux": good("wssmux"), "bp:wssmux": good("bp:wssmux"), "chisel:tls": good("chisel:tls", 90)}}
    better = standby.better_carrier
    assert better(kinds, "tcpmux", "wssmux", pair, NOW) == "bp:wssmux"                                                 # both on GRE: the first type of ANOTHER carrier that is known to connect
    assert better(kinds, "tcpmux", "bp:wssmux", pair, NOW) == ""                                                       # another carrier already: nothing to do
    assert better(kinds, "tcpmux", "wssmux", {"kinds": {"wssmux": good("wssmux")}}, NOW) == ""                         # nobody knows that another carrier connects here: the standby stays (it still saves a crashed service)
    assert better(kinds, "tcpmux", "wssmux", {}, NOW) == ""
    assert better(kinds, "tcpmux", "wssmux", {"kinds": {**pair["kinds"], "bp:wssmux": failed("bp:wssmux")}}, NOW) == "chisel:tls"      # what failed on this pair lately is not a replacement
    assert better(kinds, "tcpmux", "wssmux", pair, NOW, skip={"bp:wssmux"}) == "chisel:tls"                          # nor what a standby build failed with lately
    assert better(kinds, "tcpmux", "wssmux", pair, NOW, allowed=lambda k: k not in ("bp:wssmux", "chisel:tls")) == ""    # the licence decides what may be built
    assert better(["bp:wss", "wssmux"], "bp:wssmux", "bp:wss", {"kinds": {"wssmux": good("wssmux")}}, NOW) == "wssmux"       # a BackPack tunnel and a BackPack standby: the first known type that is not BackPack
    assert better(["tcpmux", "wssmux"], "tcpmux", "wssmux", {"kinds": {"tcpmux": good("tcpmux"), "wssmux": good("wssmux")}}, NOW) == ""       # (the tunnel's own type is never a replacement, and a GRE type is no other carrier)


def test_a_failed_build_waits_two_four_eight_minutes_up_to_an_hour():
    assert [standby.retry_wait(n) for n in (0, 1, 2, 3, 5, 6, 12)] == [0, 120, 240, 480, 1920, 3600, 3600]


def test_the_redirects_on_a_server_are_read_by_standby_and_protocol():
    out = "STANDBY de-ir1-sb tcp 80 20080\nSTANDBY de-ir1-sb tcp 443 20443\nSTANDBY other-sb udp 53 20053\nnoise\nSTANDBY x tcp a 1\nNOW 12\n"
    assert standby.parse_list(out) == {"de-ir1-sb": [("tcp", 80, 20080), ("tcp", 443, 20443)], "other-sb": [("udp", 53, 20053)]}
    assert standby.parse_list("") == {} and standby.nat_map([80, 443], [20080, 20443]) == "80:20080,443:20443"

    for bad in (([80], [20080, 20443]), ([], [])):
        with pytest.raises(ValueError):
            standby.nat_map(*bad)


# ------------------------------------------------------------------ the decisions
def test_the_tunnel_is_handed_over_after_the_delay_when_the_standby_is_up_and_not_before():
    d = lambda p, s, **kw: standby.decide(p, s, now=NOW, auto=True, **kw)
    assert d(P("down", 3), S())[0] == "wait" and "5 s" in d(P("down", 3), S())[1]                                    # a blip is not a cut
    assert d(P("down", 6), S())[0] == "handover" and d(P("down", 0), S(), after=0)[0] == "handover"
    assert d(P("down", 60), S("down"))[0] == "wait" and "its standby is down" in d(P("down", 60), S("down"))[1]       # a standby that is not up gets no users
    assert d(P("down", 60), None)[0] == "wait"
    assert d(P("down", 60, off=True), S())[0] == "wait"                                                               # a server is off: the standby is on the same server
    assert d(P("down", 60), S(stale="the tunnel runs between other servers now"))[0] == "wait"                       # a standby that does not fit the tunnel gets nobody
    assert d(P("degraded", 600), S())[0] == "wait" and d(P("unknown", 600), S())[0] == "wait"                         # only a tunnel that is DOWN


def test_the_users_go_back_when_the_tunnel_has_held_for_a_minute_and_nothing_repairs_it():
    use = {"since": NOW - 100, "map": "80:20080"}
    d = lambda p, s, **kw: standby.decide(p, s, now=NOW, auto=True, **kw)
    assert d(P("up", 30), S(use=use))[0] == "wait" and d(P("down", 600), S(use=use))[0] == "wait"                      # the tunnel is up for 30 s only / still down: the users stay
    assert d(P("up", 61), S(use=use))[0] == "back" and "up for 61 s" in d(P("up", 61), S(use=use))[1]
    assert d(P("up", 61), S(use=use), repairing=True)[0] == "wait" and d(P("up", 61, off=True), S(use=use))[0] == "wait"
    assert d(P("up", 61, last_check_at=NOW - 500), S(use=use))[0] == "wait"                                            # the last look is old: it is not "up" for a minute, it is unknown
    assert d(P("up", 61), S(use={**use, "manual": True}))[0] == "wait"                                                 # handed over by hand: it stays until Back now
    assert d(P("down", 600), S(use=use, stale="the forwarded ports of the tunnel changed"))[0] == "back"              # a standby that no longer fits gives the users back first
    assert d(P("down", 600), S("down", 20, use=use))[0] == "restart" and d(P("down", 600), S("down", 5, use=use))[0] == "wait"       # the users are on it and it is down: its services are restarted at once


def test_the_standby_is_built_when_the_tunnel_is_healthy_and_mended_quietly_when_it_is_not():
    d = lambda p, s, **kw: standby.decide(p, s, now=NOW, auto=True, **kw)
    assert d(P(), None)[0] == "build"
    assert d(P(), None, building=True)[0] == "wait" and d(P("degraded"), None)[0] == "wait" and d(P(off=True), None)[0] == "wait"
    assert d(P(), None, repairing=True)[0] == "wait" and d(P(), None, storm=True)[0] == "wait"                         # nothing is built while a repair works on the servers or several servers are off
    assert d(P(), None, cannot_build="server x is busy")[1] == "server x is busy"
    assert d(P(), None, wait_until=NOW + 90)[0] == "wait" and "90 s" in d(P(), None, wait_until=NOW + 90)[1]            # the last build failed
    assert d(P(), S())[0] == "wait" and d(P(), S())[1] == "ready"
    assert d(P(), S("down", 20))[0] == "wait"                                                                         # not yet
    assert d(P(), S("down", standby.UNHEALTHY_AFTER + 1))[0] == "restart" and d(P(), S("starting", standby.REBUILD_AFTER + 1))[0] == "rebuild"
    assert d(P(), S("down", 900), repairing=True)[0] == "wait" and d(P("degraded"), S("down", 900))[0] == "wait"       # a standby is mended when the TUNNEL is fine
    assert d(P(), S(stale="the forwarded ports of the tunnel changed"))[0] == "drop"
    assert d(None, S())[0] == "wait"                                                                                   # the row of the tunnel is gone (being rebuilt): nothing is touched


def test_a_standby_that_has_the_carrier_of_its_tunnel_is_built_again_only_when_everything_is_calm():
    old = {"created_at": NOW - standby.CLASH_AFTER - 10, "parked_at": 0}
    d = lambda p, s, **kw: standby.decide(p, s, now=NOW, auto=True, better="bp:wssmux", **kw)
    act, why = d(P(), S(**old))
    assert act == "rebuild" and "carrier" in why and "bp:wssmux" in why
    assert standby.decide(P(), S(**old), now=NOW, auto=True)[0] == "wait"                                              # nothing better is known: it stays
    assert d(P(), S(**{**old, "parked_at": NOW - standby.CLASH_QUIET - 5}))[0] == "rebuild"                              # the last way back is long over
    assert d(P("up", standby.RETURN_AFTER - 1), S(**old))[0] == "wait"                                                 # the tunnel has just come back (the Doctor may still be swapping it)
    assert d(P("up", 300, last_check_at=NOW - 500), S(**old))[0] == "wait"                                              # its last look is old: it is not known to be up
    assert d(P(), S(created_at=NOW - 20, parked_at=0))[0] == "wait"                                                    # a standby that was just built is not built again at once (no loop while the Doctor swaps)
    assert d(P(), S(created_at=NOW - 1000, parked_at=NOW - 60))[0] == "wait"                                           # the way back was a minute ago: open connections still stand on the standby
    assert d(P(), S(**old), repairing=True)[0] == "wait" and d(P(), S(**old), storm=True)[0] == "wait" and d(P(off=True), S(**old))[0] == "wait"
    assert d(P("down", 600), S(**old))[0] == "handover" and d(P("degraded", 600), S(**old))[0] == "wait"               # a cut tunnel is handed over, not touched
    assert d(P("up", 300), S(**old, use={"since": NOW - 100}))[0] == "back"                                            # the users are on the standby: nothing is rebuilt under them
    assert d(P("up", 30), S(**old, use={"since": NOW - 100}))[0] == "wait"
    assert d(P(), S("down", 20, **old))[0] == "wait"                                                                   # a standby that is not up is mended first (restart, rebuild): the old rules
    assert standby.decide(P(), S(**old), now=NOW, auto=False, better="bp:wssmux")[0] == "wait"                         # mode off: nothing by itself
    assert d(P(), S(**old, stale="the forwarded ports of the tunnel changed"))[0] == "drop"


def test_in_the_doctors_mode_off_nothing_happens_by_itself():
    for p, s in ((P("down", 60), S()), (P(), None), (P("up", 61), S(use={"since": NOW - 100})), (P(), S("down", 900))):
        act, why = standby.decide(p, s, now=NOW, auto=False)
        assert act == "wait" and "the Doctor is off" in why


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
            out += ["-p", spec[i + 1], "-m", spec[i + 1]]          # (iptables prints "-p tcp -m tcp --dport 80" - and takes that form back)
            i += 4 if spec[i + 2:i + 4] == ["-m", spec[i + 1]] else 2
        elif spec[i] == "--comment":
            out += ["--comment", '"' + spec[i + 1] + '"']          # (and quotes a comment)
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


class Server_:
    """A scratch 'server': a fake iptables on the PATH with its rules in a file, and the two directories the actions write to."""

    def __init__(self, tmp: Path):
        self.tmp, self.state = tmp, tmp / "rules.json"
        (tmp / "bin").mkdir()
        (tmp / "sbin").mkdir()
        (tmp / "systemd").mkdir()
        (tmp / "bin" / "iptables").write_text(FAKE_IPTABLES)
        (tmp / "bin" / "systemctl").write_text("#!/bin/sh\nexit 0\n")

        for f in ("iptables", "systemctl"):
            (tmp / "bin" / f).chmod(0o755)

    def run(self, text: str) -> str:
        text = text.replace("/usr/local/sbin", str(self.tmp / "sbin")).replace("/etc/systemd/system", str(self.tmp / "systemd"))
        r = subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=60, check=False, env={"PATH": f"{self.tmp / 'bin'}:/usr/bin:/bin", "FAKE_IPT_STATE": str(self.state)})
        assert r.returncode == 0, r.stderr
        return r.stdout

    def action(self, name, **params):
        return self.run(ssh.render_action(name, params))

    def rules(self):
        return [tuple(r) for r in json.loads(self.state.read_text())] if self.state.exists() else []


needs_shell = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("python3") is None or shutil.which("awk") is None, reason="needs bash, python3 and awk")


@needs_shell
def test_the_handover_puts_tagged_redirects_again_and_again_and_takes_exactly_those_off(tmp_path):
    srv = Server_(tmp_path)
    assert srv.action("standby_on", NAME="de-sb", MAP="80:20080,443:20443", PROTOS="tcp").strip() == "STANDBY_ON de-sb 2"
    nat = [r for r in srv.rules() if r[0] == "nat"]
    assert len(nat) == 2 and all(r[1] == "PREROUTING" and "-j REDIRECT --to-ports" in r[2] and '--comment "nikatel-standby-de-sb"' in r[2] for r in nat)
    assert any("--dport 80 " in r[2] and r[2].endswith("--to-ports 20080") for r in nat) and any("--dport 443 " in r[2] and r[2].endswith("--to-ports 20443") for r in nat)
    inp = [r for r in srv.rules() if r[1] == "INPUT"]
    assert len(inp) == 2 and all(r[0] == "filter" and r[2].endswith("-j ACCEPT") for r in inp) and any("--dport 20080 " in r[2] for r in inp)       # what was redirected must be let in
    assert (tmp_path / "sbin" / "nikatel-standby-de-sb").stat().st_mode & 0o111 and "ExecStart=" in (tmp_path / "systemd" / "nikatel-standby-de-sb.service").read_text()
    again = srv.rules()
    assert srv.action("standby_on", NAME="de-sb", MAP="80:20080,443:20443", PROTOS="tcp").strip() == "STANDBY_ON de-sb 2" and srv.rules() == again          # idempotent: nothing is added twice
    assert sorted(srv.run(ssh._script_text("standby_list", {})).split("NOW")[0].split("\n")[:2]) == ["STANDBY de-sb tcp 443 20443", "STANDBY de-sb tcp 80 20080"]
    assert {k: sorted(v) for k, v in standby.parse_list(srv.run(ssh._script_text("standby_list", {}))).items()} == {"de-sb": [("tcp", 80, 20080), ("tcp", 443, 20443)]}      # what the panel reads is what it put

    srv.action("standby_on", NAME="de-sb2", MAP="80:21080", PROTOS="tcp udp")                                                   # another standby whose name starts with the first one's, and a tag nobody owns
    assert len([r for r in srv.rules() if r[0] == "nat" and "nikatel-standby-de-sb2" in r[2]]) == 2
    srv.state.write_text(json.dumps(srv.rules() + [["nat", "PREROUTING", '-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998'], ["filter", "INPUT", "-j DROP"]]))

    def mine(tag):
        return [r for r in srv.rules() if f'"nikatel-standby-{tag}"' in r[2]]                                                  # (the rules of exactly this tag)

    # the way back (PARK=1): nothing redirects any more, but a rule of the tag STAYS in the NAT chain (an empty chain makes the kernel reset the open connections) and the accepts keep only the established ones
    assert srv.action("standby_off", NAME="de-sb", PARK="1").strip() == "STANDBY_OFF de-sb 0"
    nat, inp = [r for r in mine("de-sb") if r[0] == "nat"], [r for r in mine("de-sb") if r[1] == "INPUT"]
    assert len(nat) == 2 and all(r[2].endswith("-j RETURN") for r in nat) and not any("REDIRECT" in r[2] for r in mine("de-sb"))
    assert len(inp) == 2 and all("--ctstate ESTABLISHED,RELATED" in r[2] and r[2].endswith("-j ACCEPT") for r in inp)
    assert {k: sorted(v) for k, v in standby.parse_list(srv.run(ssh._script_text("standby_list", {}))).items()} == {"de-sb2": [("tcp", 80, 21080), ("udp", 80, 21080)]}            # (a parked rule is not a redirect)
    assert not (tmp_path / "sbin" / "nikatel-standby-de-sb").exists() and not (tmp_path / "systemd" / "nikatel-standby-de-sb.service").exists()       # the boot unit goes: a reboot puts nothing again
    assert len([r for r in srv.rules() if "nikatel-standby-de-sb2" in r[2]]) == 4                                                # the one with the longer name is untouched
    assert srv.action("standby_off", NAME="de-sb", PARK="1").strip() == "STANDBY_OFF de-sb 0" and len(mine("de-sb")) == 4        # the way back again: nothing is added twice

    # the handover again: the parked rules go, the redirects are at the TOP of the chain
    assert srv.action("standby_on", NAME="de-sb", MAP="80:20080,443:20443", PROTOS="tcp").strip() == "STANDBY_ON de-sb 2"
    assert not any(r[2].endswith("-j RETURN") for r in mine("de-sb")) and not any("--ctstate" in r[2] for r in mine("de-sb")) and len(mine("de-sb")) == 4
    top = next(r for r in srv.rules() if r[0] == "nat")
    assert '"nikatel-standby-de-sb"' in top[2] and "-j REDIRECT" in top[2]                                                       # (the redirect is at the top of the chain: before anything that was parked or is somebody else's)

    # the standby goes for good (PARK=0): every rule of the tag, parked or not
    srv.action("standby_off", NAME="de-sb", PARK="1")
    assert srv.action("standby_off", NAME="de-sb", PARK="0").strip() == "STANDBY_OFF de-sb 0"
    left = srv.rules()
    assert mine("de-sb") == []                                                                                                    # the redirects, the parked rules and the accepts of this standby are gone ...
    assert len([r for r in left if "nikatel-standby-de-sb2" in r[2]]) == 4 and ("nat", "PREROUTING", "-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998") in left and ("filter", "INPUT", "-j DROP") in left      # ... and nothing else (the one with the longer name, a rule of somebody else)
    assert srv.action("standby_off", NAME="de-sb", PARK="0").strip() == "STANDBY_OFF de-sb 0"                                      # taking off what is not there is fine
    assert srv.action("standby_off", NAME="de-sb2", PARK="0").strip() == "STANDBY_OFF de-sb2 0" and len(srv.rules()) == 2


@needs_shell
def test_a_server_without_iptables_says_so_and_changes_nothing(tmp_path):
    srv = Server_(tmp_path)
    (tmp_path / "bin" / "iptables").unlink()
    r = subprocess.run([shutil.which("bash"), "-c", ssh.render_action("standby_on", {"NAME": "de-sb", "MAP": "80:20080", "PROTOS": "tcp"}).replace("/usr/local/sbin", str(tmp_path / "sbin"))], capture_output=True, text=True, timeout=30,
                       check=False, env={"PATH": f"{tmp_path / 'empty'}", "FAKE_IPT_STATE": str(srv.state)})
    assert "STANDBY_FAIL this server has no iptables" in r.stdout and not list((tmp_path / "sbin").iterdir())


def test_the_actions_take_only_a_name_ports_and_a_protocol():
    on = ssh.render_action("standby_on", {"NAME": "de-ir1-sb", "MAP": "80:20080,443:20443", "PROTOS": "tcp udp"})
    assert "REDIRECT" in on and "PREROUTING" in on and "nikatel-standby-" in on and "80:20080,443:20443" in on and "tcp udp" in on
    assert "OUTPUT" not in on and "flush" not in on.lower() and "-F" not in on                                          # not the request the panel makes on the server itself; nothing flushed
    off = ssh.render_action("standby_off", {"NAME": "de-ir1-sb", "PARK": "1"})
    assert "-j RETURN" in off and "sed 's/^-A /-D /'" in off and " -F" not in off and "reboot" not in off and "ESTABLISHED,RELATED" in off     # (the way back leaves a harmless rule of the tag in the chain on purpose)
    assert ssh.render_action("standby_off", {"NAME": "de-ir1-sb", "PARK": "0"})
    assert "iptables -t nat -S PREROUTING" in ssh._script_text("standby_list", {}) and "-A " not in ssh._script_text("standby_list", {})            # reading only

    for bad in ({"NAME": "x; reboot", "MAP": "80:20080", "PROTOS": "tcp"}, {"NAME": "Upper", "MAP": "80:20080", "PROTOS": "tcp"}, {"NAME": "de-sb", "MAP": "80:20080;id", "PROTOS": "tcp"},
                {"NAME": "de-sb", "MAP": "80", "PROTOS": "tcp"}, {"NAME": "de-sb", "MAP": "80:20080", "PROTOS": "sctp"}, {"NAME": "de-sb", "MAP": "80:20080", "PROTOS": "tcp; id"}, {"NAME": "", "MAP": "80:20080", "PROTOS": "tcp"}):
        with pytest.raises(ValueError):
            ssh.render_action("standby_on", bad)

    for bad in ({"NAME": "a b", "PARK": "1"}, {"NAME": "de-sb", "PARK": "2"}, {"NAME": "de-sb", "PARK": ""}, {"NAME": "de-sb"}, {"NAME": "de-sb", "PARK": "1; id"}):
        with pytest.raises(ValueError):
            ssh.render_action("standby_off", bad)

    with pytest.raises(ValueError):
        ssh._script_text("standby_list", {"NAME": "x"})


# ------------------------------------------------------------------ a standby is never a cut of the tunnel
def test_a_standby_is_in_no_list_no_count_no_alert_and_no_repair(client, monkeypatch):
    w = world("hid1", state="up")
    other = world("hid2", state="down", since=20)
    sid = add_standby(w, state="down", since=900)
    sid2 = add_standby(other, state="down", since=900)
    assert is_standby(Tunnel(record={"standby_of": "x"})) and not is_standby(Tunnel(record={})) and not is_standby(Tunnel())

    names = [t["name"] for t in client.get("/api/tunnels").json()]
    assert names == ["hid1", "hid2"] and client.get(f"/api/tunnels/{sid}").status_code == 404                              # not in the list, not by its id
    ov = client.get("/api/overview").json()["tunnels"]
    assert ov == {"total": 2, "up": 1, "down": 1, "degraded": 0, "unknown": 0}                                       # two standbys that are down are not two tunnels that are down
    text = report.build()
    assert "hid1-sb" not in text and "hid2-sb" not in text and "Tunnels: 1/2 up, 1 down" in text and "Down now: hid2" in text                        # (the daily report counts the tunnels of the list)

    assert [b["name"] for b in ap.broken_tunnels()] == ["hid2"] and ap.outage_state()["tunnels_down"] == ["hid2"]
    sent = []
    monkeypatch.setattr(alerts, "_deliver", lambda level, text_: sent.append(text_))
    alerts.run()

    with session() as db:
        from nikatel.models import AlertState

        assert {a.ref for a in db.scalars(select(AlertState).where(AlertState.kind == "tunnel"))} == {"hid1", "hid2"}            # no alert state for a standby: it can never be "down" in a message

    assert not any("-sb" in m for m in sent)
    writable(monkeypatch)
    started, smart = [], []
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(actions, "fix", lambda tunnel_id, source="manual", dry_run=False: started.append(tunnel_id) or 1)
    monkeypatch.setattr(ap, "outage_state", lambda: {"storm": False})
    monkeypatch.setattr(ap, "smart_fix_start", lambda tunnel_id, level="switch", source="manual": smart.append(tunnel_id) or 1)
    actions.auto_repair()
    ap.auto_escalate()
    assert started == [other["id"]] and sid2 not in started and sid not in started and smart == []                       # the Doctor restarts the tunnel that is cut, never the standby of any
    assert [w_.name for w_ in sentinel._load()[0]] == ["hid1"]                                                           # (the sentinel reads the enabled tunnels that are up)
    seen = []
    monkeypatch.setattr(tn, "check_tunnel", lambda tunnel_id, fresh_ext=False, quick=False: seen.append(tunnel_id))
    monkeypatch.setattr(tn, "external_probe_all", lambda: None)
    tn.check_all()
    tn.check_unhealthy()
    assert set(seen) == {w["id"], other["id"]} and sid not in seen and sid2 not in seen                                   # neither the minute cycle nor the fast lane looks at a standby: the standby job does

    with session() as db:
        assert [t.name for t in db.scalars(select(Tunnel).where(Tunnel.enabled.is_(True)))] == ["hid1", "hid2"]


def quiet_world(monkeypatch, state_of_units):
    """Every check answers `state_of_units` ('active' / 'inactive') for all the units; returns the list of jobs that were woken."""
    woken = []

    def query(node, plan, is_iran, quick=False):
        units = plan.iran_units if is_iran else plan.foreign_units
        text = "".join(f"UNIT {u} {state_of_units}\n" for u in units)

        if is_iran and plan.iface:
            text += f"IFACE {plan.iface} UP\n"

        return tn.parse_side(text + "NOW 1000\n")

    monkeypatch.setattr(tn, "_query", query)
    monkeypatch.setattr(tn, "external_probe_one", lambda *a, **k: None)
    monkeypatch.setattr(tn.jobs, "trigger", lambda name: woken.append(name))
    monkeypatch.setattr(tn.jobs, "stopping", lambda: False)
    return woken


def test_a_standby_that_falls_makes_no_event_no_history_and_wakes_nobody(monkeypatch):
    w = world("qt1")
    sid = add_standby(w, state="up")
    woken = quiet_world(monkeypatch, "inactive")
    tn._check_tunnel(sid)

    with session() as db:
        t = db.get(Tunnel, sid)
        assert t.state == "down" and "is inactive" in t.last_error and t.last_check_at > clock() - 5                      # it is looked at: its own state is kept
        assert db.scalars(select(TunnelCheck).where(TunnelCheck.tunnel_id == sid)).all() == []                          # but no history (it is looked at every few seconds)
        assert db.scalars(select(Event).where(Event.ref == t.name)).all() == []                                         # no event: nobody is told that a spare is being mended

    assert woken == []                                                                                                  # the repair job does not wake for a standby

    tn._check_tunnel(w["id"])                                                                                          # the tunnel itself falls: the repair AND the standby job wake
    assert sorted(woken) == ["repair", "standby"]

    with session() as db:
        assert db.get(Tunnel, w["id"]).state == "down" and len(db.scalars(select(Event).where(Event.ref == "qt1")).all()) == 1


def test_a_tunnel_whose_ports_are_on_its_standby_is_judged_without_the_look_from_another_server(monkeypatch):
    """The users' ports are redirected: another Iran server's request to them is answered by the STANDBY - it must not make the tunnel look up."""
    w = world("hd3", detail={"data_seen": True, "data_fails": 3})
    port = w["port"]

    def query(node, plan, is_iran, quick=False):
        text = "".join(f"UNIT {u} active\n" for u in (plan.iran_units if is_iran else plan.foreign_units))

        if is_iran:
            text += f"IFACE {plan.iface} UP\nPEER {plan.iran_peer} 1.5 0\nDATA {port} 000\nLISTEN {port} 1\n"      # on the server itself nothing comes back through the tunnel

        return tn.parse_side(text + "NOW 1000\n")

    monkeypatch.setattr(tn, "_query", query)
    monkeypatch.setattr(tn, "ext_for", lambda *a, **k: ("200", 5.0, "another-iran"))                               # ... but the other Iran server gets an answer (from the standby)
    monkeypatch.setattr(tn, "external_probe_one", lambda *a, **k: None)
    monkeypatch.setattr(tn.jobs, "trigger", lambda name: None)
    tn.HANDED.discard("hd3")
    tn._check_tunnel(w["id"])

    with session() as db:
        assert db.get(Tunnel, w["id"]).state == "up"                                                                   # (as it always was: an answer from outside proves the users' path)
        db.get(Tunnel, w["id"]).detail = {"data_seen": True, "data_fails": 3}

    tn.HANDED.add("hd3")

    try:
        tn._check_tunnel(w["id"])
    finally:
        tn.HANDED.discard("hd3")

    with session() as db:
        t = db.get(Tunnel, w["id"])
        assert t.state == "down" and "no data comes back" in t.last_error and t.detail["ext"] is None                  # judged by what IT carries: nothing


# ------------------------------------------------------------------ the handover and the way back
def fake_server(monkeypatch, fail=False):
    """The Iran server answers standby_on / standby_off like the real action; returns the list of calls."""
    calls = []

    def run_action(node, name, params=None, timeout=90):
        calls.append((name, dict(params or {})))

        if name == "standby_on":
            if fail:
                return "STANDBY_FAIL this server has no iptables\n"

            return f"STANDBY_ON {params['NAME']} {len(params['MAP'].split(',')) * len(params['PROTOS'].split())}\n"

        return f"STANDBY_OFF {params['NAME']} 0\n"

    monkeypatch.setattr(standby, "run_action", run_action)
    monkeypatch.setattr(tn, "check_tunnel", lambda *a, **k: None)
    monkeypatch.setattr(tn.jobs, "trigger", lambda name: None)
    tn.HANDED.clear()
    return calls


def test_a_handover_puts_the_redirects_writes_the_ledger_and_the_event_and_a_return_takes_them_off(monkeypatch):
    writable(monkeypatch)
    w = world("ho1", state="down", since=12)
    sid = add_standby(w)
    calls = fake_server(monkeypatch)
    standby._mark("ho1", True)
    r = standby.handover("ho1", "the tunnel has been down for 12 s")

    assert r["ok"] and r["kind"] == "chisel:tls" and r["standby"] == "ho1-sb" and r["secs"] < 5
    assert calls == [("standby_on", {"NAME": "ho1-sb", "MAP": f"{w['port']}:{standby.shifted(w['port'])}", "PROTOS": "tcp"})]
    assert "ho1" in tn.HANDED and standby.status("ho1")["state"] == "in_use"

    with session() as db:
        use = db.get(Tunnel, sid).record["use"]
        assert use["map"] == f"{w['port']}:{standby.shifted(w['port'])}" and use["move"].startswith("standby-ho1-") and "manual" not in use
        ev = db.scalars(select(Event).where(Event.ref == "ho1")).all()
        assert len(ev) == 1 and ev[0].level == "warn" and "handed to its standby tunnel (chisel:tls)" in ev[0].text and "s after the cut" in ev[0].text

    moves = doctor._state()["moves"]
    assert len(moves) == 1 and moves[0]["reason"] == "standby" and moves[0]["asked"] == ["ho1"] and moves[0]["tunnels"] == [] and moves[0]["returned"] is False
    assert doctor.may_move("ho1", moves, NOW) == ""                                                                     # a handover is not a MOVE of the tunnel: the rails of the moves do not count it
    assert standby.handover("ho1")["already"] is True and len(calls) == 1                                               # (again: nothing happens twice)

    r = standby.give_back("ho1", "up for a minute")
    assert r["ok"] and calls[-1] == ("standby_off", {"NAME": "ho1-sb", "PARK": "1"}) and "ho1" not in tn.HANDED                 # (parked: the open connections stay on the standby)

    with session() as db:
        assert "use" not in db.get(Tunnel, sid).record
        assert [e.level for e in db.scalars(select(Event).where(Event.ref == "ho1").order_by(Event.id))] == ["warn", "ok"]

    moves = doctor._state()["moves"]
    assert moves[0]["returned"] is True and moves[0]["returned_at"] and standby.status("ho1")["state"] != "in_use"
    assert standby.give_back("ho1")["already"] is True


def test_a_handover_that_the_server_did_not_do_leaves_nothing_behind(monkeypatch):
    writable(monkeypatch)
    w = world("ho2", state="down")
    sid = add_standby(w)
    calls = fake_server(monkeypatch, fail=True)

    with pytest.raises(RuntimeError, match="the handover failed: the redirects were not put: this server has no iptables"):
        standby.handover("ho2")

    assert [c[0] for c in calls] == ["standby_on", "standby_off"] and "ho2" not in tn.HANDED                              # a half-put handover is taken off again

    with session() as db:
        assert "use" not in db.get(Tunnel, sid).record and db.scalars(select(Event).where(Event.ref == "ho2")).all() == []

    assert doctor._state()["moves"] == []


def test_removing_a_standby_takes_off_the_rules_the_way_back_left_and_keeps_a_standby_it_cannot_clean(monkeypatch):
    writable(monkeypatch)
    calls = fake_server(monkeypatch)
    removed = []
    monkeypatch.setattr(builder, "delete_tunnel", lambda tunnel_id, keep_record=False, trash=False, skip_offline=False: removed.append((tunnel_id, skip_offline)) or ["removed"])
    use = {"since": clock() - 50, "map": "1:2", "protos": "tcp", "move": ""}

    handed = world("rs1")
    sid = add_standby(handed, use={**use, "map": f"{handed['port']}:{standby.shifted(handed['port'])}"})
    standby.remove_standby("rs1")
    assert calls == [("standby_off", {"NAME": "rs1-sb", "PARK": "1"}), ("standby_off", {"NAME": "rs1-sb", "PARK": "0"})] and removed == [(sid, False)]       # the users go back (parked), then every rule of the tag goes, then the tunnel

    calls.clear()
    removed.clear()
    parked = world("rs2")
    sid = add_standby(parked)

    with session() as db:
        db.get(Tunnel, sid).record = {**db.get(Tunnel, sid).record, "parked": True}

    standby.remove_standby("rs2")
    assert calls == [("standby_off", {"NAME": "rs2-sb", "PARK": "0"})] and removed == [(sid, False)]                                     # the way back left a rule on the server: it goes with the standby

    calls.clear()
    removed.clear()
    never = world("rs3")
    sid = add_standby(never)
    standby.remove_standby("rs3")
    assert calls == [] and removed == [(sid, False)]                                                                                    # a standby that never took the users: nothing to clean, no SSH

    calls.clear()
    removed.clear()
    stuck = world("rs4")
    add_standby(stuck, use={**use, "map": f"{stuck['port']}:{standby.shifted(stuck['port'])}"})
    real = standby.run_action

    def refuse(node, name, params=None, timeout=90):
        if (params or {}).get("PARK") == "0":
            raise ssh.SSHError("the Iran server does not answer")

        return real(node, name, params, timeout)

    monkeypatch.setattr(standby, "run_action", refuse)

    with pytest.raises(ssh.SSHError):
        standby.remove_standby("rs4")

    assert removed == []                                                                                                                # the rules cannot be taken off: the standby is KEPT (its boot unit would put them again on a port nobody listens on)

    off = world("rs5")

    with session() as db:
        db.get(Server, off["iran"]).online = False

    add_standby(off, use={**use, "map": f"{off['port']}:{standby.shifted(off['port'])}"})
    calls.clear()
    monkeypatch.setattr(standby, "run_action", real)
    standby.remove_standby("rs5")
    assert calls == [] and len(removed) == 1 and removed[0][1] is True                                                                  # the Iran server is off: nothing to ask it; the files are purged when it is back (skip_offline)


def test_a_handover_is_refused_when_it_would_do_harm(monkeypatch):
    w = world("ho3", state="down")
    add_standby(w)
    fake_server(monkeypatch)

    with pytest.raises(ValueError, match="read-only"):
        standby.handover("ho3")

    writable(monkeypatch)

    with pytest.raises(ValueError, match="no standby"):
        standby.handover("nothing")

    sick = world("ho4", state="down")
    add_standby(sick, state="down")

    with pytest.raises(ValueError, match="the standby is down"):
        standby.handover("ho4")

    stale = world("ho5", state="down")
    add_standby(stale, mapping="21111:9")

    with pytest.raises(ValueError, match="does not fit"):
        standby.handover("ho5")


def test_the_job_hands_over_after_five_seconds_and_gives_back_after_a_minute(monkeypatch):
    writable(monkeypatch)
    w = world("jb1", state="down", since=2)
    sid = add_standby(w)
    calls = fake_server(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(ap, "repairing_names", list)
    standby._mark("jb1", True)
    standby._CHECKED[sid] = time.time()

    standby._step("jb1")
    assert calls == []                                                                                                  # down for 2 s: a blip is not a cut

    with session() as db:
        db.get(Tunnel, w["id"]).state_since = clock() - 8

    standby._step("jb1")
    assert [c[0] for c in calls] == ["standby_on"] and "jb1" in tn.HANDED                                               # the users' ports are on the standby
    standby._step("jb1")
    assert [c[0] for c in calls] == ["standby_on"]                                                                      # (and the job does not do it twice)

    with session() as db:
        t = db.get(Tunnel, w["id"])
        t.state, t.state_since, t.last_check_at = "up", clock() - 30, clock()

    standby._step("jb1")
    assert [c[0] for c in calls] == ["standby_on"]                                                                      # up for 30 s: not yet

    with session() as db:
        db.get(Tunnel, w["id"]).state_since = clock() - 70

    standby._step("jb1")
    assert [c[0] for c in calls] == ["standby_on", "standby_off"] and "jb1" not in tn.HANDED                           # held for a minute: the users go back


def test_a_manual_handover_stays_until_back_now(monkeypatch):
    writable(monkeypatch)
    w = world("jb2")
    sid = add_standby(w)
    calls = fake_server(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(ap, "repairing_names", list)
    standby._mark("jb2", True)
    standby._CHECKED[sid] = time.time()
    standby.handover("jb2", "handed over by you", manual=True)
    standby._step("jb2")
    standby._step("jb2")
    assert [c[0] for c in calls] == ["standby_on"]                                                                      # the tunnel is up for minutes and the users are still on the standby: the owner asked for it
    standby.give_back("jb2")
    assert [c[0] for c in calls] == ["standby_on", "standby_off"]


def test_in_the_doctors_mode_off_the_job_hands_over_nothing_but_the_buttons_work(monkeypatch):
    writable(monkeypatch)
    w = world("jb3", state="down", since=60)
    sid = add_standby(w)
    calls = fake_server(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(ap, "repairing_names", list)
    standby._mark("jb3", True)
    standby._CHECKED[sid] = time.time()
    standby._step("jb3")
    assert calls == []
    assert standby.handover("jb3")["ok"] and [c[0] for c in calls] == ["standby_on"]                                     # by hand it works in every mode


def test_a_standby_that_is_unhealthy_is_rebuilt_quietly_and_never_counted_as_a_cut(monkeypatch):
    writable(monkeypatch)
    w = world("jb4")
    sid = add_standby(w, state="down", since=standby.REBUILD_AFTER + 20)
    fake_server(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "move")
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(ap, "repairing_names", list)
    removed, built = [], []
    monkeypatch.setattr(builder, "delete_tunnel", lambda tunnel_id, keep_record=False, trash=False, skip_offline=False: removed.append((tunnel_id, skip_offline)) or ["removed"])
    monkeypatch.setattr(standby, "build", lambda name: built.append(name) or 1)
    standby._mark("jb4", True)
    standby._CHECKED[sid] = time.time()
    standby._step("jb4")
    assert removed == [(sid, False)]                                                                                   # not up for five minutes while the tunnel is fine: it is built again

    with session() as db:
        db.delete(db.get(Tunnel, sid))

    standby._step("jb4")
    assert built == ["jb4"]                                                                                            # ... and the next turn builds the new one

    with session() as db:
        assert db.get(Tunnel, w["id"]).state == "up" and db.scalars(select(Event)).all() == []                         # the tunnel was never touched, and nobody was told
        assert db.scalars(select(ActionLog)).all() == []

    assert ap.broken_tunnels() == []


def test_a_standby_that_the_users_are_on_is_restarted_at_once_when_it_falls(monkeypatch):
    writable(monkeypatch)
    w = world("jb5", state="down", since=200)
    sid = add_standby(w, state="down", since=40, use={"since": NOW - 60, "map": f"{w['port']}:{standby.shifted(w['port'])}", "protos": "tcp", "move": ""})
    fake_server(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(ap, "repairing_names", list)
    restarted = []
    monkeypatch.setattr(standby, "_restart", lambda s: restarted.append(s["name"]))
    standby._mark("jb5", True)
    standby._CHECKED[sid] = time.time()
    standby._RESTARTED.clear()
    standby._step("jb5")
    end = time.time() + 3

    while time.time() < end and not restarted:
        time.sleep(0.05)

    assert restarted == ["jb5-sb"]


def test_a_standby_on_the_carrier_of_its_tunnel_is_built_again_by_the_job(monkeypatch):
    writable(monkeypatch)
    w = world("jb6")
    sid = add_standby(w, since=900)

    with session() as db:
        db.get(Tunnel, sid).created_at = clock() - 1000
        db.get(Tunnel, sid).type, db.get(Tunnel, sid).transport = "backhaul", "wssmux"            # (the standby is Backhaul over GRE, like its tunnel)
        db.get(Tunnel, sid).record = {**db.get(Tunnel, sid).record, "kind": "wssmux"}

    fake_server(monkeypatch)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(ap, "repairing_names", list)
    asked, removed = [], []
    monkeypatch.setattr(standby, "_BETTER", {})
    monkeypatch.setattr(standby.knowledge, "plan_for", lambda *a, **k: (["tcpmux", "wssmux", "bp:wssmux"], ""))
    monkeypatch.setattr(standby, "better_carrier", lambda *a, **k: asked.append(1) or "bp:wssmux")                     # (a type of another carrier is known to connect on this pair)
    monkeypatch.setattr(standby, "remove_standby", lambda name: removed.append(name) or [])
    monkeypatch.setattr(standby, "build", lambda name: 1)
    standby._mark("jb6", True)
    standby._CHECKED[sid] = time.time()
    standby._step("jb6")
    assert removed == ["jb6"]                                                                                           # the standby shares the carrier of its tunnel and a type of another carrier is known: it is built again

    standby._step("jb6")
    assert len(asked) == 1                                                                                              # (the knowledge is asked once a minute, not at every turn of the job)

    with session() as db:
        t = db.get(Tunnel, sid)
        t.type, t.transport = "chisel", "wss"
        t.record = {**t.record, "kind": "chisel"}

    removed.clear()
    standby._BETTER.clear()
    standby._step("jb6")
    assert removed == [] and len(asked) == 1                                                                            # another carrier (Chisel against Backhaul over GRE): nothing is asked, nothing is done


def test_a_stopped_gre_card_of_a_standby_is_restarted_first_with_its_services(monkeypatch):
    w = world("jb7")
    sid = add_standby(w, state="down", since=60)

    with session() as db:
        t = db.get(Tunnel, sid)
        t.type, t.transport, t.port = "backhaul", "tcpmux", 9651

    calls = []
    monkeypatch.setattr(standby, "run_action", lambda node, name, params=None, timeout=90: calls.append((node.host, name, (params or {}).get("UNITS"))) or "")
    monkeypatch.setattr(tn, "check_tunnel", lambda *a, **k: None)
    monkeypatch.setattr(standby.time, "sleep", lambda s: None)
    standby._restart({"id": sid, "name": "jb7-sb"})
    assert [c[1] for c in calls] == ["restart_units", "restart_units"]
    assert calls[0][2] == "nikatel-gre-jb7-sb.service backhaul-iran-tcpmux-9651.service"                                # the Iran side: the GRE card first (it carries the rest), then the Backhaul service on it
    assert calls[1][2] == "nikatel-gre-jb7-sb.service backhaul-kharej-tcpmux-9651.service"                              # ... and the same on the foreign side


def test_the_last_way_back_is_written_down_and_cleared_by_the_next_handover():
    w = world("jb8")
    sid = add_standby(w)
    standby._set_use(sid, None, parked=True)
    s = standby._view("jb8")["s"]
    assert s["parked"] and abs(s["parked_at"] - clock()) < 5
    standby._set_use(sid, {"since": clock(), "map": "1:2", "protos": "tcp", "move": ""})
    s = standby._view("jb8")["s"]
    assert not s["parked"] and s["parked_at"] == 0 and s["use"]


# ------------------------------------------------------------------ the build
def test_the_plan_names_the_standby_picks_its_type_and_shifts_the_ports(monkeypatch):
    w = world("bp1")
    knowledge.record(w["iran"], w["foreign"], {"kind": "chisel:tls", "ok": True, "score": 70, "latency_ms": 100.0, "loss_pct": 0.0}, "latency")
    plan = standby.build_plan("bp1")
    assert plan["name"] == "bp1-sb" and plan["kind"] == "chisel:tls" and plan["ports"] == f"{standby.shifted(w['port'])}:{w['port']}" and plan["mapping"] == {w["port"]: standby.shifted(w["port"])}
    assert "known to connect" in plan["note"]

    with session() as db:
        db.get(Server, w["iran"]).facts = {"cpu": 93.0, "ram": 40.0}

    with pytest.raises(ValueError, match="is busy"):
        standby.build_plan("bp1")                                                                                       # a second tunnel doubles the processes of the pair

    with session() as db:
        db.get(Server, w["iran"]).facts = {}
        db.get(Server, w["foreign"]).online = False

    with pytest.raises(ValueError, match="is off"):
        standby.build_plan("bp1")

    with session() as db:
        db.get(Server, w["foreign"]).online = True

    monkeypatch.setattr(standby.license, "tunnel_kind_allowed", lambda k: False)

    with pytest.raises(ValueError, match="no other tunnel type"):
        standby.build_plan("bp1")                                                                                       # the free tier builds one type only: no second one

    other = world("bp2")

    with session() as db:
        db.get(Tunnel, other["id"]).forwarded_ports = []

    with pytest.raises(ValueError, match="forwards no port"):
        standby.build_plan("bp2")


def test_the_build_is_started_as_a_standby_with_its_own_name_and_the_shifted_ports(monkeypatch):
    writable(monkeypatch)
    w = world("bd1")
    knowledge.record(w["iran"], w["foreign"], {"kind": "chisel:tls", "ok": True, "score": 70, "latency_ms": 100.0, "loss_pct": 0.0}, "latency")
    got = []
    monkeypatch.setattr(bo, "start_build", lambda *a, **k: got.append(("other", a, k)) or 41)
    monkeypatch.setattr(builder, "start_build", lambda *a, **k: got.append(("backhaul", a, k)) or 42)
    monkeypatch.setattr(ap, "repairing_names", list)
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    standby._BUILDS.clear()
    assert standby.build("bd1") == 41
    kind, a, k = got[0]
    assert kind == "other" and a == (w["iran"], w["foreign"], f"{standby.shifted(w['port'])}:{w['port']}", "chisel", "wss", {}) and k == {"forced_name": "bd1-sb", "measure": "latency", "standby_of": "bd1"}
    assert standby._BUILDS["bd1"]["kind"] == "chisel:tls" and standby.status("bd1")["state"] in ("waiting", "off")          # (not marked yet)

    with pytest.raises(ValueError, match="being built"):
        standby.build("bd1")

    standby._BUILDS.clear()
    knowledge.record(w["iran"], w["foreign"], {"kind": "wssmux", "ok": True, "score": 99, "latency_ms": 10.0, "loss_pct": 0.0}, "latency")
    monkeypatch.setattr(standby, "choose_kind", lambda *a, **k: ("wg:tcpmux", "x"))
    standby.build("bd1")
    kind, a, k = got[1]
    assert kind == "backhaul" and a[3] == "tcpmux" and k["opts"] == {"link": "wg"} and k["standby_of"] == "bd1"       # Backhaul over WireGuard: the link is the type's; an automatic build never installs a package
    standby._BUILDS.clear()


def test_the_build_is_refused_while_the_tunnel_is_not_up_a_repair_works_on_it_or_a_standby_stands(monkeypatch):
    fake_server(monkeypatch)
    w = world("bd2")

    with pytest.raises(ValueError, match="read-only"):
        standby.build("bd2")

    writable(monkeypatch)
    monkeypatch.setattr(ap, "repairing_names", list)
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    standby._BUILDS.clear()
    monkeypatch.setattr(bo, "start_build", lambda *a, **k: 1)

    with pytest.raises(ValueError, match="tunnel not found"):
        standby.build("nothing")

    monkeypatch.setattr(ap, "repairing_names", lambda: ["bd2"])

    with pytest.raises(ValueError, match="a repair works"):
        standby.build("bd2")

    monkeypatch.setattr(ap, "repairing_names", list)
    monkeypatch.setattr(ap, "storm_now", lambda: True)

    with pytest.raises(ValueError, match="nothing is built now"):
        standby.build("bd2")

    monkeypatch.setattr(ap, "storm_now", lambda: False)
    down = world("bd3", state="down")

    with pytest.raises(ValueError, match="tunnel is down"):
        standby.build("bd3")

    add_standby(w)

    with pytest.raises(ValueError, match="already has a standby"):
        standby.build("bd2")

    assert down["id"]


def test_a_build_that_ended_is_counted_and_the_type_that_failed_is_not_chosen_again(monkeypatch):
    w = world("cl1")
    kinds = ["bp:wssmux", "chisel:tls"]
    pair = knowledge.pair(w["iran"], w["foreign"])

    with session() as db:
        e = ActionLog(ref="cl1-sb", action="build", source="manual", story=["Building…", "Stopped: the chisel client did not connect"], ok=False, finished_at=NOW)
        db.add(e)
        db.flush()
        aid = e.id

    standby._BUILDS["cl1"] = {"aid": aid, "name": "cl1-sb", "kind": "bp:wssmux", "at": NOW - 30}
    standby._collect()
    f = standby._failed("cl1")
    assert "cl1" not in standby._BUILDS and f["n"] == 1 and "did not connect" in f["why"] and "bp:wssmux" in f["kinds"]
    assert standby.choose_kind(kinds, "tcpmux", pair, NOW, skip=set(f["kinds"]))[0] == "chisel:tls"                      # the next try is another type
    assert standby.status("cl1")["state"] == "off"                                                                       # (not marked)
    standby._mark("cl1", True)
    s = standby.status("cl1")
    assert s["state"] == "failed" and "the next try is in" in s["why"]                                                   # the page says why nothing happens

    with session() as db:
        assert len(db.scalars(select(Event).where(Event.ref == "cl1")).all()) == 1                                       # told once, quietly

    with session() as db:
        e = ActionLog(ref="cl1-sb", action="build", source="manual", story=["Tunnel cl1-sb is ready."], ok=True, finished_at=NOW)
        db.add(e)
        db.flush()
        aid = e.id

    standby._BUILDS["cl1"] = {"aid": aid, "name": "cl1-sb", "kind": "chisel:tls", "at": NOW}
    standby._collect()
    assert standby._failed("cl1") == {}                                                                                  # a good build clears the failures


def test_a_standby_build_makes_a_switched_off_row_without_an_event_and_its_own_lock(monkeypatch):
    from nikatel.services.builder import parse_ports

    with session() as db:
        ir, fr = Server(name="ir1", host="203.0.113.7", role="iran"), Server(name="fr1", host="198.51.100.7", role="foreign")
        db.add_all([ir, fr])
        db.flush()
        db.expunge(ir)
        db.expunge(fr)

    facts = {"units": [], "tcp": set(), "udp": set(), "ufw": False, "root": True, "python": True, "arch": "x86_64", "ips": []}
    monkeypatch.setattr(bo, "_node", lambda s: s.host)
    monkeypatch.setattr(bo, "_preflight", lambda b, i, f: (dict(facts), dict(facts)))
    monkeypatch.setattr(bo.engines, "ensure_on_node", lambda n, e: False)
    monkeypatch.setattr(bo, "_install_sink", lambda b, f, k, bind="": b.created.append(f))
    monkeypatch.setattr(bo, "run_action", lambda n, a, p=None, timeout=0: "")
    monkeypatch.setattr(bo, "run_script", lambda n, s, p=None, timeout=0: "FP=PIN=\n" if s == "chisel_fp" else "")
    monkeypatch.setattr(bo, "_put", lambda *a, **k: None)
    monkeypatch.setattr(bo, "_start", lambda *a, **k: None)
    monkeypatch.setattr(bo, "_wait_for_data", lambda *a, **k: None)
    monkeypatch.setattr(bo, "_measure", lambda b, i, k: None)
    monkeypatch.setattr(bo, "_say", lambda aid, line, done=None, ok=None: None)
    checked = []
    monkeypatch.setattr(bo, "check_tunnel", lambda tid: checked.append(tid))
    b = bo.Build(aid=1, kind="chisel", iran=ir, foreign=fr, ports=parse_ports("28072:8072"), transport="wss", name="x-sb", standby_of="x")
    bo._build(b)

    with session() as db:
        t = db.scalar(select(Tunnel))
        tid = t.id
        assert t.name == "x-sb" and t.enabled is False and t.record["standby_of"] == "x" and t.forwarded_ports == [28072] and t.record["port_mapping"] == "28072:8072"
        assert db.scalars(select(Event)).all() == [] and is_standby(t)                                                   # not a tunnel of the list: no "built" event

    assert checked == [tid]                                                                                              # (it is looked at once, as any tunnel is, the standby job goes on from there)

    # the lock: the standby of a pair is built beside the real tunnel of the pair (a repair), under its own key
    monkeypatch.setattr(builder, "_build_worker", lambda b_, key: None)
    writable(monkeypatch)
    builder._BUILDING.clear()
    iran_id, foreign_id = ir.id, fr.id
    builder.start_build(iran_id, foreign_id, "28072:8072", "tcpmux", forced_name="y-sb", standby_of="y")
    builder.start_build(iran_id, foreign_id, "8072", "tcpmux", forced_name="y")                                          # the real tunnel's repair builds at the same time: no "already being built"

    with pytest.raises(ValueError, match="already being built"):
        builder.start_build(iran_id, foreign_id, "28072:8072", "tcpmux", forced_name="y-sb", standby_of="y")               # (one standby of a tunnel at a time)

    assert any("standby y-sb" in k for k in builder._BUILDING) and any(k == "ir1 ⇄ fr1" for k in builder._BUILDING)
    builder._BUILDING.clear()

    with session() as db:
        kinds = {a.ref: a.action for a in db.scalars(select(ActionLog))}

    assert kinds["y-sb"] == "standby" and kinds["y"] == "build"                                                          # (a standby build is not "a build that runs" for the Create window)


def test_a_standby_that_is_removed_makes_no_event_and_a_tunnel_that_is_removed_does(monkeypatch):
    writable(monkeypatch)
    w = world("qr1")
    sid = add_standby(w)
    monkeypatch.setattr(bo, "run_action", lambda n, a, p=None, timeout=0: "")
    monkeypatch.setattr(builder, "run_action", lambda n, a, p=None, timeout=0: "")
    res = builder.delete_tunnel(sid)
    assert res == ["Iran: removed", "foreign: removed"]

    with session() as db:
        assert db.get(Tunnel, sid) is None and db.scalars(select(Event)).all() == []                                      # a spare that is replaced is not news

    builder.delete_tunnel(w["id"])

    with session() as db:
        assert [e.ref for e in db.scalars(select(Event))] == ["qr1"]                                                      # the tunnel itself is


# ------------------------------------------------------------------ the owner's switch, the page and the API
def test_the_switch_builds_at_once_and_turning_it_off_removes_the_standby(client, monkeypatch):
    writable(monkeypatch)
    w = world("sw1")
    built, removed = [], []

    def fake_build(name):
        built.append(name)
        standby._BUILDS[name] = {"aid": 910001, "name": name + "-sb", "kind": "chisel:tls", "at": int(time.time())}
        return 910001

    monkeypatch.setattr(standby, "build", fake_build)
    monkeypatch.setattr(standby, "remove_standby", lambda name: removed.append(name) or [])
    standby._BUILDS.clear()
    r = client.get(f"/api/tunnels/{w['id']}/standby").json()
    assert r["on"] is False and r["state"] == "off"

    r = client.put(f"/api/tunnels/{w['id']}/standby", json={"on": True})
    assert r.status_code == 200 and r.json()["on"] is True and r.json()["state"] == "building" and built == ["sw1"]
    row = next(t for t in client.get("/api/tunnels").json() if t["name"] == "sw1")
    assert row["standby"]["on"] is True and row["standby"]["state"] == "building" and row["standby"]["kind"] == "chisel:tls"            # the tunnel window reads it from the list
    assert "sw1" in standby._marks()

    r = client.put(f"/api/tunnels/{w['id']}/standby", json={"on": False})
    assert r.status_code == 200 and r.json()["on"] is False
    end = time.time() + 5

    while time.time() < end and not removed:
        time.sleep(0.05)

    assert removed == ["sw1"] and "sw1" not in standby._marks()
    assert client.put("/api/tunnels/9999/standby", json={"on": True}).status_code == 400
    assert client.get("/api/tunnels/9999/standby").status_code == 404 and client.post("/api/tunnels/9999/standby/back").status_code == 404
    standby._BUILDS.clear()
    builder.CANCEL.discard(910001)                                                                                       # (the stop of a build that does not exist: not left for the tests that follow)


def test_the_switch_is_refused_while_read_only_and_for_a_tunnel_without_ports(client, monkeypatch):
    w = world("sw2")
    assert client.put(f"/api/tunnels/{w['id']}/standby", json={"on": True}).status_code == 400                              # read-only
    writable(monkeypatch)

    with session() as db:
        db.get(Tunnel, w["id"]).forwarded_ports = []

    r = client.put(f"/api/tunnels/{w['id']}/standby", json={"on": True})
    assert r.status_code == 400 and "forwards no port" in r.json()["error"]
    sid = add_standby(world("sw3"))
    assert client.put(f"/api/tunnels/{sid}/standby", json={"on": True}).status_code == 400                                 # a standby has no standby


def test_a_tunnel_that_cannot_be_built_now_is_marked_and_says_why(client, monkeypatch):
    writable(monkeypatch)
    w = world("sw4", state="down")
    r = client.put(f"/api/tunnels/{w['id']}/standby", json={"on": True}).json()
    assert r["on"] is True and r["state"] == "waiting" and "tunnel is down" in r["why"]                                     # the standby job builds it when the tunnel is up


def test_the_buttons_hand_over_and_take_back_through_the_api(client, monkeypatch):
    writable(monkeypatch)
    w = world("sw5", state="down", since=20)
    sid = add_standby(w)
    calls = fake_server(monkeypatch)
    standby._mark("sw5", True)
    r = client.post(f"/api/tunnels/{w['id']}/standby/handover")
    assert r.status_code == 200 and r.json()["state"] == "in_use" and calls[0][0] == "standby_on"

    with session() as db:
        assert db.get(Tunnel, sid).record["use"]["manual"] is True                                                         # by hand: until Back now

    row = next(t for t in client.get("/api/tunnels").json() if t["name"] == "sw5")
    assert row["standby"]["state"] == "in_use" and row["standby"]["ports"] == f"{w['port']} → {standby.shifted(w['port'])}"
    assert any(m["reason"] == "standby" and not m["returned"] for m in client.get("/api/doctor").json()["moves"])             # in the Moves tab
    r = client.post(f"/api/tunnels/{w['id']}/standby/back")
    assert r.status_code == 200 and r.json()["state"] == "ready" and calls[-1][0] == "standby_off"
    assert all(m["returned"] for m in client.get("/api/doctor").json()["moves"])

    monkeypatch.setattr(get_settings(), "read_only", True)
    assert client.post(f"/api/tunnels/{w['id']}/standby/handover").status_code == 400

    monkeypatch.setattr(get_settings(), "read_only", False)
    sick = world("sw6", state="down")
    add_standby(sick, state="down")
    standby._mark("sw6", True)
    r = client.post(f"/api/tunnels/{sick['id']}/standby/handover")
    assert r.status_code == 400 and "the standby is down" in r.json()["error"]                                              # refused: the standby is not up


def test_removing_a_tunnel_removes_its_standby_with_it(client, monkeypatch):
    writable(monkeypatch)
    w = world("rm1")
    forgotten = []
    monkeypatch.setattr(builder, "delete_tunnel", lambda tunnel_id, keep_record=False, trash=False, skip_offline=False: ["Iran: removed"])
    monkeypatch.setattr(standby, "forget", lambda name: forgotten.append(name))
    r = client.delete(f"/api/tunnels/{w['id']}")
    assert r.status_code == 200 and forgotten == ["rm1"]


def test_forgetting_a_tunnel_drops_the_mark_and_the_standby_and_a_rebuild_keeps_both(monkeypatch):
    writable(monkeypatch)
    w = world("fg1")
    add_standby(w)
    fake_server(monkeypatch)
    removed = []
    monkeypatch.setattr(standby, "remove_standby", lambda name: removed.append(name) or [])
    standby._mark("fg1", True)
    standby.forget("fg1")
    end = time.time() + 5

    while time.time() < end and not removed:
        time.sleep(0.05)

    assert removed == ["fg1"] and "fg1" not in standby._marks()

    # the row of a marked tunnel is gone for a while (a repair rebuilds it): the mark and the standby stay - until it is clear it is not coming back
    standby._mark("fg1", True)
    removed.clear()

    with session() as db:
        db.delete(db.get(Tunnel, w["id"]))

    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    standby._CHECKED.clear()
    standby._MISSING.clear()
    standby._step("fg1")
    assert removed == [] and "fg1" in standby._marks() and "fg1" in standby._MISSING
    standby._MISSING["fg1"] = clock() - standby.MISSING_GRACE - 5
    standby._step("fg1")
    assert removed == ["fg1"] and "fg1" not in standby._marks()


def test_a_standby_whose_tunnel_moved_to_another_iran_server_gives_the_users_back_and_goes(monkeypatch):
    writable(monkeypatch)
    w = world("mv1")
    other = world("mv2")
    sid = add_standby(w, use={"since": NOW - 100, "map": f"{w['port']}:{standby.shifted(w['port'])}", "protos": "tcp", "move": ""})

    with session() as db:
        db.get(Tunnel, w["id"]).iran_server_id = other["iran"]                                                             # the failover moved the tunnel

    calls = fake_server(monkeypatch)
    tn.HANDED.add("mv1")
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(ap, "repairing_names", list)
    standby._mark("mv1", True)
    standby._CHECKED[sid] = time.time()
    standby._step("mv1")
    assert [c[0] for c in calls] == ["standby_off"] and "mv1" not in tn.HANDED                                          # the redirects of the old pair are taken off first


def test_a_rename_carries_the_mark_and_the_standby_to_the_new_name(monkeypatch):
    w = world("rn1")
    sid = add_standby(w)
    standby._mark("rn1", True)
    standby._set_failed("rn1", {"n": 2, "at": NOW, "why": "x", "kinds": {}})
    tn.HANDED.add("rn1")
    assert builder.rename_tunnel(w["id"], "rn9") == "rn9"
    standby._apply_renames()
    assert "rn9" in standby._marks() and "rn1" not in standby._marks() and standby._failed("rn9")["n"] == 2 and "rn9" in tn.HANDED and "rn1" not in tn.HANDED
    tn.HANDED.discard("rn9")

    with session() as db:
        assert db.get(Tunnel, sid).record["standby_of"] == "rn9"

    assert standby._view("rn9")["s"]["stale"] == "" and standby._view("rn9")["p"]["name"] == "rn9"


def test_the_redirects_nobody_asked_for_are_taken_off_and_a_handover_that_lost_its_redirects_is_put_again(monkeypatch):
    writable(monkeypatch)
    w = world("rc1")
    add_standby(w, use={"since": NOW - 100, "map": f"{w['port']}:{standby.shifted(w['port'])}", "protos": "tcp", "move": ""})
    standby._touch(w["iran"])
    calls = []

    def run_action(node, name, params=None, timeout=90):
        calls.append((name, dict(params or {})))
        return ""

    monkeypatch.setattr(standby, "run_action", run_action)
    monkeypatch.setattr(standby, "run_script", lambda node, name, params=None, timeout=40: "STANDBY ghost-sb tcp 80 20080\nNOW 1\n")
    standby.reconcile()
    assert ("standby_off", {"NAME": "ghost-sb", "PARK": "0"}) in calls                                                             # a redirect of a standby that does not exist: off
    assert ("standby_on", {"NAME": "rc1-sb", "MAP": f"{w['port']}:{standby.shifted(w['port'])}", "PROTOS": "tcp"}) in calls     # the handover stands but the server has none (a flushed firewall): put again
    calls.clear()
    monkeypatch.setattr(standby, "run_script", lambda node, name, params=None, timeout=40: f"STANDBY rc1-sb tcp {w['port']} {standby.shifted(w['port'])}\nNOW 1\n")
    standby.reconcile()
    assert calls == []                                                                                                   # what stands is what is believed: nothing is done


def test_after_a_start_of_the_panel_the_tunnels_on_their_standbys_are_known_again():
    w = world("it1")
    add_standby(w, use={"since": NOW - 100, "map": "1:2", "protos": "tcp", "move": ""})
    tn.HANDED.clear()
    standby._STATE["init"] = False
    standby._init_once()
    assert tn.HANDED == {"it1"}
    tn.HANDED.clear()


# ------------------------------------------------------------------ the job and the page
def test_the_standby_job_runs_every_five_seconds_and_does_nothing_when_nothing_is_marked(monkeypatch):
    import inspect

    from nikatel import main

    assert 'jobs.register("standby", 5, standby.run)' in inspect.getsource(main._register_jobs)
    monkeypatch.setattr(jobs, "stopping", lambda: False)
    seen = []
    monkeypatch.setattr(standby, "_step", lambda name: seen.append(name))
    world("jn1")
    standby.run()
    assert seen == []                                                                                                    # no mark, no standby: not even a look at a tunnel
    standby._mark("jn1", True)
    standby.run()
    assert seen == ["jn1"]
    standby._mark("jn1", False)


def test_the_tunnel_window_and_the_moves_tab_know_the_standby():
    web = Path(__file__).resolve().parent.parent / "nikatel" / "web"
    app = (web / "app.js").read_text(encoding="utf-8")
    assert "function tunStandby(t)" in app and "tunBanner(t) + tunStandby(t) + tunBwBox(t) + tunGrid(t)" in app                         # a bar in the window, under the banner
    assert all(f"window.{f} = async" in app for f in ("tunStandbySet", "tunStandbyNow", "tunStandbyBack", "apStandbyBack"))
    assert "/standby/handover" in app and "/standby/back" in app and "{method: \"PUT\", body: JSON.stringify({on})}" in app
    assert 'standby: "standby"' in app and "apStandbyBack(" in app                                                      # the Moves tab: the reason, and Back now on the entry
    assert "Standby · {}" in (web / "i18n.fa.doctor.js").read_text(encoding="utf-8") and "Back now" in (web / "i18n.fa.doctor.js").read_text(encoding="utf-8")


def test_the_marks_are_written_one_at_a_time():
    errors = []

    def work(n):
        try:
            for i in range(10):
                standby._mark(f"t{n}-{i}", True)
        except (SQLAlchemyError, RuntimeError, ValueError) as e:      # the test collects it
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    assert errors == [] and len(standby._marks()) == 40                                                                  # (a mark written between the read and the write of another would be lost)
