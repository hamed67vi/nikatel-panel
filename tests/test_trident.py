"""The Trident (the owner, 2026-10-23: "64 tunnel types can be built in the panel - combine them into a strong multi-layer tunnel that gives the best connection in every condition - our own method"; services/trident.py,
docs/trident.md): a tunnel and its strands (its warm standby and one more tunnel of another kind, between the same two servers) share the users' NEW connections by live quality, in ONE NAT chain on the Iran server.

  the kinds       what a filter sees of each of the 64 kinds (`traits`), the choice of a kind that does not share a fate with the others (`pick_kind`)
  the numbers     quality, the weights (capacity x quality, floor, ceiling, the way in by steps), when a rewrite is worth it, the probabilities of the chain, the plan, what is read back
  the rules       the reviewed actions: what the scripts do and in which ORDER (the accepts before the chain), run for real against a fake iptables / iptables-restore
  the service     the mark, who owns the users' ports, the build of the extra strand, the conductor's turn in every state, the way out, a rename, the reconcile, what the window shows
The same rules on a REAL kernel (network namespaces, iptables-nft and iptables-legacy, the panel's own rendered actions, 26 checks each): scripts/trident-netns-proof.sh, recorded in docs/trident.md."""
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
from nikatel.models import Event, Server, Tunnel, is_standby
from nikatel.services import (
    actions,
    autopilot,
    knowledge,
    lanes,
    standby,
    trident,
    tunnel_test,
    tunnels,
)
from nikatel.services import settings as st

NOW = int(time.time())


def clock() -> int:
    return int(time.time())


def writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)


# ------------------------------------------------------------------ the kinds
def test_every_one_of_the_64_kinds_has_traits_and_the_families_are_what_a_filter_sees():
    kinds = [k[0] for k in tunnel_test.KINDS]
    assert len(kinds) == 64

    for k in kinds:
        t = trident.traits(k)
        assert set(t) == {"family", "l4", "dial", "tls"} and t["family"] in ("gre", "ipip", "esp", "udp", "tcp", "icmp") and t["dial"] in ("iran", "foreign")

    assert trident.traits("tcpmux")["family"] == "gre" and trident.traits("wssmux")["tls"] is True
    assert trident.traits("wg:tcpmux")["family"] == "udp" and trident.traits("fou:tcpmux")["family"] == "udp" and trident.traits("ipip:tcpmux")["family"] == "ipip" and trident.traits("ipsec:tcpmux")["family"] == "esp"
    assert trident.traits("bp:wssmux") == {"family": "tcp", "l4": "tcp", "dial": "foreign", "tls": True}
    assert trident.traits("bp:kcp")["family"] == "udp" and trident.traits("bp:xdi")["family"] == "icmp" and trident.traits("bp:pck")["l4"] == "tcp-raw"
    assert trident.traits("bpd:pck")["dial"] == "iran" and trident.traits("bpd:udp")["family"] == "udp"
    assert trident.traits("chisel:fwdtls") == {"family": "tcp", "l4": "tcp", "dial": "iran", "tls": True} and trident.traits("chisel")["dial"] == "foreign"
    assert trident.traits("paqet")["dial"] == "iran" and trident.traits("wgchiselfwd:tcpmux")["dial"] == "iran" and trident.traits("something-new")["family"] == "tcp"


def test_a_strand_is_chosen_to_share_the_least_fate_with_the_ones_that_are_there():
    now = clock()
    kinds = ["tcpmux", "bp:wssmux", "bp:tcpmux", "bpd:pck", "chisel:fwd", "bpd:udp", "wg:tcpmux"]
    # the tunnel is Backhaul over GRE, the standby BackPack TLS: the third strand is not GRE and not TCP-through-a-foreign-dial: a UDP or an Iran-dialled kind
    k, why = trident.pick_kind(kinds, ["tcpmux", "bp:wssmux"], None, now)
    assert k == "bpd:udp" or k == "wg:tcpmux" or k == "bpd:pck"
    assert trident.traits(k)["family"] not in ("gre", "tcp") or k == "bpd:pck"
    assert "another family" in why
    assert trident.clash("bpd:udp", ["tcpmux", "bp:wssmux"])[0] == 0 and trident.clash("bp:tcpmux", ["tcpmux", "bp:wssmux"])[0] == 1

    # a kind known to connect on the pair comes before one nobody tried, among those with the same clash
    pair = {"kinds": {"bpd:udp": {"last": {"ok": True, "measured": True, "at": now, "score": 80}, "full": {"score": 80, "at": now}}}}
    assert trident.pick_kind(["wg:tcpmux", "bpd:udp"], ["tcpmux"], pair, now)[0] == "bpd:udp"

    # what failed on the pair, what the licence refuses and what failed as a strand are left out
    failed = {"kinds": {"bpd:udp": {"last": {"ok": False, "measured": False, "at": now, "error": "x"}}}}
    assert trident.pick_kind(["bpd:udp", "wg:tcpmux"], ["tcpmux"], failed, now)[0] == "wg:tcpmux"
    assert trident.pick_kind(["bpd:udp", "wg:tcpmux"], ["tcpmux"], None, now, allowed=lambda k: k != "bpd:udp")[0] == "wg:tcpmux"
    assert trident.pick_kind(["bpd:udp", "wg:tcpmux"], ["tcpmux"], None, now, skip={"bpd:udp", "wg:tcpmux"})[0] == ""
    assert trident.pick_kind([], ["tcpmux"], None, now)[1].startswith("no other tunnel type is left")

    # when only the same family is left it is taken, and the sentence says so
    k, why = trident.pick_kind(["tcpmux", "tcp"], ["tcpmux"], None, now)
    assert k == "tcp" and "same family" in why


# ------------------------------------------------------------------ the numbers
def test_quality_falls_with_loss_with_delay_and_with_an_unproven_kind_and_never_below_a_floor():
    assert trident.quality(0.0, 50, 50, 1.0) == 1.0 and trident.quality(1.0, 50, 50, 1.0) == 1.0                        # loss up to 1 % costs nothing
    assert trident.quality(4.0, 50, 50, 1.0) == pytest.approx(0.5)                                                    # every 3 points over halve it
    assert trident.quality(0.0, 130, 50, 1.0) == pytest.approx(90 / 170)                                              # 80 ms slower than the best
    assert trident.quality(None, None, None, None) == pytest.approx(trident.STAB_UNKNOWN)                               # nothing known: only the unproven kind counts a little less
    assert trident.quality(0.0, 50, 50, 0.1) == 0.3 and trident.quality(80.0, 900, 10, 0.0) == 0.01                     # the stability is held at 0.3 - 1, the whole thing at 0.01 - 1
    assert trident.quality(2.0, 60, 50, 0.9) > trident.quality(6.0, 60, 50, 0.9) > trident.quality(6.0, 160, 50, 0.9)


def item(usable=True, cap=100.0, rtt=50.0, loss=0.0, stab=1.0, stage=3, **kw):
    return {"usable": usable, "cap": cap, "rtt": rtt, "loss": loss, "stab": stab, "stage": stage, **kw}


def test_the_weights_add_up_to_one_follow_capacity_and_quality_and_keep_to_the_floor_and_the_ceiling():
    w = trident.target_weights([item(), item(), item()])
    assert sum(w) == pytest.approx(1.0) and all(x == pytest.approx(1 / 3) for x in w)

    w = trident.target_weights([item(cap=300), item(cap=100)])
    assert w[0] == pytest.approx(0.75) and w[1] == pytest.approx(0.25)                                              # (two usable strands: the ceiling is 90 %)

    w = trident.target_weights([item(cap=1000), item(cap=10), item(cap=10)])
    assert w[0] == pytest.approx(trident.CEIL_MANY) and sum(w) == pytest.approx(1.0) and w[1] == pytest.approx(w[2]) and w[1] == pytest.approx(0.15)    # nobody carries more than 70 % while three are usable

    w = trident.target_weights([item(cap=1000), item(cap=1)])
    assert w[0] == pytest.approx(trident.CEIL_TWO) and w[1] == pytest.approx(0.10)                                    # ... and 90 % while two are

    w = trident.target_weights([item(cap=1000), item(cap=1), item(cap=1)])
    assert min(w) >= trident.FLOOR - 1e-9                                                                             # a usable strand keeps its floor: it stays warm and measured

    w = trident.target_weights([item(loss=0.0), item(loss=12.0), item(loss=0.0)])
    assert w[1] < w[0] and w[1] < w[2] and w[1] > 0 and sum(w) == pytest.approx(1.0)                                # a lossy strand carries less, not nothing

    w = trident.target_weights([item(rtt=40), item(rtt=40), item(rtt=400)])
    assert w[2] < w[0]

    w = trident.target_weights([item(stab=1.0), item(stab=0.4)])
    assert w[1] < w[0]


def test_a_strand_that_is_down_gets_nothing_and_a_lone_strand_gets_everything():
    w = trident.target_weights([item(usable=False), item(), item()])
    assert w[0] == 0.0 and w[1] == pytest.approx(0.5) and w[2] == pytest.approx(0.5)
    assert trident.target_weights([item(usable=False), item(), item(usable=False)]) == [0.0, 1.0, 0.0]
    assert trident.target_weights([item(usable=False), item(usable=False)]) == [0.0, 0.0]
    assert trident.target_weights([item(), {"usable": False}, {"usable": False}]) == [1.0, 0.0, 0.0]

    for items in ([item(), item(), item(usable=False)], [item(cap=None), item(), item(cap=7)], [item(loss=99.0), item(loss=99.0), item(loss=99.0)]):
        w = trident.target_weights(items)
        assert sum(w) == pytest.approx(1.0) and all(x >= 0 for x in w)


def test_a_strand_that_comes_back_is_let_in_by_steps_and_the_ones_that_are_fully_in_carry_the_rest():
    # the tunnel is fully in, the standby is on its first step (10 %), the new strand on its second (25 %)
    w = trident.target_weights([item(cap=100, stage=3), item(cap=100, stage=0), item(cap=100, stage=1)])
    assert w[1] == pytest.approx(0.10) and w[2] == pytest.approx(0.25) and w[0] == pytest.approx(0.65) and sum(w) == pytest.approx(1.0)

    # everybody is on the first steps: the ceilings cannot add up to 1, the fully-in ones take the rest
    w = trident.target_weights([item(stage=3), item(stage=0), item(stage=0)])
    assert w[1] == pytest.approx(0.10) and w[2] == pytest.approx(0.10) and w[0] == pytest.approx(0.80) and sum(w) == pytest.approx(1.0)
    # nobody is fully in: the ceilings are lifted ONE AT A TIME, the strand that has been in the longest first - the one that just came back keeps its small step (the real pair, 2026-10-23: a tunnel that had
    # just come back took 56 % beside two one-minute strands)
    w = trident.target_weights([item(stage=0, stage_at=1000), item(stage=0, stage_at=800), item(stage=0, stage_at=900)])
    assert w[1] == pytest.approx(0.80) and w[0] == pytest.approx(0.10) and w[2] == pytest.approx(0.10) and sum(w) == pytest.approx(1.0)
    w = trident.target_weights([item(stage=0, stage_at=1000), item(stage=1, stage_at=990), item(stage=0, stage_at=900)])
    assert w[1] == pytest.approx(0.80) and w[0] == pytest.approx(0.10) and w[2] == pytest.approx(0.10)                     # (a higher step counts before an earlier time)
    w = trident.target_weights([item(usable=False), item(stage=0, stage_at=800), item(stage=0, stage_at=900)])
    assert w == pytest.approx([0.0, 0.90, 0.10])                                                                     # the tunnel is dead, both strands are new: the older one carries, the newer stays on its step

    # the way in: back to 0 when down, a step after RAMP_STEP while healthy, never past 3
    assert trident.advance(3, False, 0, 999) == 0 and trident.advance(2, False, 500, 999) == 0
    assert trident.advance(0, True, 100, trident.RAMP_STEP - 1) == 0 and trident.advance(0, True, 100, trident.RAMP_STEP) == 1 and trident.advance(3, True, 9999, 9999) == 3
    assert trident.advance(1, True, trident.USABLE_AFTER - 1, 9999) == 1                                                # (not before it settled)


def test_a_rewrite_waits_for_a_real_change_but_never_for_a_strand_that_fell_or_came_back():
    a, b = [0.4, 0.3, 0.3], [0.42, 0.28, 0.30]
    assert trident.should_apply(None, a, 1000, 0, False) is True                                                      # nothing stands yet
    assert trident.should_apply(a, b, 1000, 990, False) is False                                                      # a small drift: left
    assert trident.should_apply(a, [0.5, 0.2, 0.3], 1000, 1000 - trident.ADJUST_EVERY + 1, False) is False             # 10 points, too soon
    assert trident.should_apply(a, [0.5, 0.2, 0.3], 1000, 1000 - trident.ADJUST_EVERY, False) is True                  # ... and after the wait
    assert trident.should_apply(a, [0.7, 0.0, 0.3], 1000, 1000 - trident.ADJUST_BIG_EVERY, False) is True              # a big drift only waits a minute
    assert trident.should_apply(a, [0.7, 0.0, 0.3], 1000, 1000 - trident.ADJUST_BIG_EVERY + 1, False) is False
    assert trident.should_apply(a, a, 1000, 999, True) is True                                                        # urgent: a strand fell, came back, took a step
    assert trident.should_apply(a, [0.5, 0.5], 1000, 999, False) is True                                              # another number of strands


def test_the_chain_gives_each_rule_its_chance_given_that_the_rules_above_did_not_take_the_connection():
    assert trident.chain_probs([0.3, 0.3]) == pytest.approx([0.3, 0.3 / 0.7])
    assert trident.chain_probs([0.5, 0.5]) == [0.5, 1.0]                                                              # the tunnel is dead: the last rule takes whatever reaches it
    assert trident.chain_probs([0.0, 1.0]) == [0.0, 1.0] and trident.chain_probs([1.0, 0.0]) == [1.0, 0.0]
    assert trident.chain_probs([0.0, 0.0]) == [0.0, 0.0] and trident.chain_probs([0.25]) == [0.25]

    # the chain really gives the weights: the chance to reach strand k is p_k times the chance to get past the ones above it
    for ws in ([0.1, 0.2], [0.33, 0.33], [0.6, 0.05], [0.0, 0.4], [0.7, 0.3]):
        ps, left, got = trident.chain_probs(ws), 1.0, []

        for p in ps:
            got.append(left * p)
            left *= 1 - p

        assert got == pytest.approx(ws, abs=1e-9)

    assert trident.prob_text(1.0) == "1" and trident.prob_text(0.999999999) == "1" and trident.prob_text(0.3) == "0.30000000" and trident.prob_text(0.428571428) == "0.42857143"


def test_the_plan_lists_every_protocol_every_port_and_every_strand_with_a_chance_in_the_order_of_the_rules():
    plan = trident.plan_text([80, 443], [[20080, 20443], [30080, 30443]], [0.3, 1.0], ["tcp"])
    assert plan == "tcp:80:20080:0.30000000,tcp:80:30080:1,tcp:443:20443:0.30000000,tcp:443:30443:1"
    assert trident.plan_text([443], [[20443], [30443]], [0.0, 0.5], ["tcp", "udp"]) == "tcp:443:30443:0.50000000,udp:443:30443:0.50000000"          # a strand with no chance has no rule
    assert trident.plan_text([443], [[20443]], [0.0], ["tcp"]) == ""

    with pytest.raises(ValueError, match="does not forward the ports"):
        trident.plan_text([80, 443], [[20080]], [0.5], ["tcp"])

    with pytest.raises(ValueError, match="too many"):
        trident.plan_text(list(range(1000, 1400)), [[p + 20000 for p in range(1000, 1400)], [p + 30000 for p in range(1000, 1400)]], [0.3, 1.0], ["tcp", "udp"])


def test_what_is_read_back_from_the_server_is_compared_with_the_plan():
    out = "TJUMP de-ir1 tcp 443 NKTR_a67e9f0ceb 0\nTRULE de-ir1 tcp 443 20443 0.29999999981\nTRULE de-ir1 tcp 443 30443 1\nTRULE other tcp 80 20080 0.5\nTJUMP other tcp 80 NKTR_1 2\nNOW 12\nTRULE broken tcp x 1 0.5\n"
    got = trident.parse_list(out)
    assert got["de-ir1"] == {"rules": [("tcp", 443, 20443, 0.29999999981), ("tcp", 443, 30443, 1.0)], "jumps": [("tcp", 443, "NKTR_a67e9f0ceb", 0)]}
    assert got["other"]["jumps"] == [("tcp", 80, "NKTR_1", 2)] and "broken" not in got
    assert trident.same_plan(got["de-ir1"]["rules"], "tcp:443:20443:0.30000000,tcp:443:30443:1") is True
    assert trident.same_plan(got["de-ir1"]["rules"], "tcp:443:20443:0.40000000,tcp:443:30443:1") is False
    assert trident.same_plan(got["de-ir1"]["rules"], "tcp:443:20443:0.30000000") is False and trident.same_plan([], "tcp:443:20443:1") is False and trident.same_plan([], "") is True


# ------------------------------------------------------------------ the reviewed actions
def test_the_actions_take_only_a_name_a_plan_and_a_way_out_and_the_accepts_come_before_the_chain():
    plan = "tcp:443:20443:0.30000000,tcp:443:30443:1"
    on = ssh.render_action("trident_set", {"NAME": "de-ir1", "PLAN": plan})
    assert "iptables-restore --noflush" in on and "-m statistic --mode random --probability" in on and "REDIRECT" in on and "PREROUTING" in on and "nikatel-trident-" in on and "NKTR_" in on
    assert "OUTPUT" not in on and "systemctl" not in on and "reboot" not in on and "-t nat -F PREROUTING" not in on and "iptables -F" not in on         # not the request on the server itself; no boot unit; nothing flushed but the trident's own chain
    assert on.index("-j ACCEPT") < on.index("| iptables-restore --noflush") < on.index("-j \"$ch\"")        # the accepts of the shifted ports, then the chain, then the jumps (measured: accepts last dropped the first SYNs of a new strand)
    assert on.index("| iptables-restore --noflush") < on.rindex("conntrack --ctstate ESTABLISHED,RELATED")           # the accepts of the ports that left the plan are parked only after the chain no longer sends anybody there

    for park in "012":
        off = ssh.render_action("trident_off", {"NAME": "de-ir1", "PARK": park})
        assert "ss -Htn state established" in off and "ESTABLISHED,RELATED" in off and "reboot" not in off and "systemctl" not in off

    assert "-X \"$ch\"" in ssh.render_action("trident_off", {"NAME": "de-ir1", "PARK": "2"})
    lst = ssh._script_text("trident_list", {})
    assert "iptables -t nat -S" in lst and "-A " not in lst.replace("^-A ", "").replace("/^-A /", "")             # reading only

    for bad in ({"NAME": "x; reboot", "PLAN": plan}, {"NAME": "de-ir1", "PLAN": "tcp:443:20443:0.4"}, {"NAME": "de-ir1", "PLAN": "tcp:443:20443:1.0"}, {"NAME": "de-ir1", "PLAN": "tcp:443:20443:0.40000000;id"},
                {"NAME": "de-ir1", "PLAN": "sctp:443:20443:1"}, {"NAME": "de-ir1", "PLAN": "tcp:443:20443"}, {"NAME": "de-ir1", "PLAN": ""}, {"NAME": "Upper", "PLAN": plan}, {"NAME": "de-ir1", "PLAN": ",".join([plan] * 700)}):
        with pytest.raises(ValueError):
            ssh.render_action("trident_set", bad)

    with pytest.raises(ValueError):
        ssh.render_action("trident_off", {"NAME": "de-ir1", "PARK": "3"})

    assert ssh.render_action("trident_set", {"NAME": "de-ir1", "PLAN": "tcp:443:20443:0.30000000"})


FAKE_IPT = '''#!/usr/bin/env python3
import json, os, sys

path = os.environ["FAKE_IPT_STATE"]
state = json.load(open(path)) if os.path.exists(path) else {"rules": [], "chains": []}
args = [a for a in sys.argv[1:] if a != "-w"]
table = "filter"

if args[:1] == ["-t"]:
    table, args = args[1], args[2:]


def norm(spec):
    out, i = [], 0

    while i < len(spec):
        if spec[i] == "-p":
            out += ["-p", spec[i + 1], "-m", spec[i + 1]]
            i += 4 if spec[i + 2:i + 4] == ["-m", spec[i + 1]] else 2
        elif spec[i] == "--comment":
            out += ["--comment", '"' + spec[i + 1] + '"'] if False else ["--comment", spec[i + 1]]
            i += 2
        else:
            out.append(spec[i])
            i += 1

    return " ".join(out)


def save():
    json.dump(state, open(path, "w"))


op = args[0]
rules = state["rules"]

if op == "-L":
    sys.exit(0)

if op == "-S":
    chain = args[1] if len(args) > 1 else None

    for t, c, spec in rules:
        if t == table and (chain is None or c == chain):
            print("-A " + c + " " + spec)

    sys.exit(0)

chain = args[1]

if op == "-F":
    state["rules"] = [r for r in rules if not (r[0] == table and r[1] == chain)]
    save()
    sys.exit(0)

if op == "-X":
    if any(r[0] == table and r[2].endswith("-j " + chain) for r in rules):
        sys.exit(1)

    state["chains"] = [c for c in state["chains"] if c != chain]
    state["rules"] = [r for r in rules if not (r[0] == table and r[1] == chain)]
    save()
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

save()
'''
FAKE_RESTORE = '''#!/usr/bin/env python3
import json, os, sys

path = os.environ["FAKE_IPT_STATE"]
state = json.load(open(path)) if os.path.exists(path) else {"rules": [], "chains": []}
sys.argv.pop(0)
assert sys.argv and sys.argv[0] == "--noflush", sys.argv
lines = [x.strip() for x in sys.stdin.read().splitlines() if x.strip()]
assert lines[0] == "*nat" and lines[-1] == "COMMIT", lines
new = [list(r) for r in state["rules"]]
chains = list(state["chains"])
os.environ.get("FAKE_RESTORE_FAIL") and sys.exit(os.write(2, b'iptables-restore: unknown option "--bogus"\\n') and 2)

for line in lines[1:-1]:
    if line.startswith(":"):
        c = line[1:].split()[0]
        chains.append(c) if c not in chains else None
    elif line.startswith("-F "):
        c = line.split()[1]
        new = [r for r in new if not (r[0] == "nat" and r[1] == c)]
    elif line.startswith("-A "):
        parts = line.split()
        c = parts[1]
        assert c in chains, c
        spec = parts[2:]
        out, i = [], 0

        while i < len(spec):
            if spec[i] == "-p":
                out += ["-p", spec[i + 1], "-m", spec[i + 1]]
                i += 2
            else:
                out.append(spec[i])
                i += 1

        new.append(["nat", c, " ".join(out)])

state["rules"], state["chains"] = new, chains
json.dump(state, open(path, "w"))
'''
FAKE_SS = ('#!/bin/sh\n'
           'n=$(cat "$FAKE_SS_FILE" 2>/dev/null || echo 0); case "$*" in *":20443 "*|*":20080 "*) ;; *) exit 0 ;; esac\n'          # (only the connections to these two shifted ports are "open")
           'i=0; while [ "$i" -lt "$n" ]; do echo "ESTAB 0 0 10.0.0.1:20443 10.0.0.9:5555"; i=$((i+1)); done\n')


class Box:
    """A scratch 'Iran server': a fake iptables and iptables-restore on the PATH with their rules in a file, and a fake `ss` that reports as many open connections as the test says."""

    def __init__(self, tmp: Path):
        self.tmp, self.state, self.ss = tmp, tmp / "rules.json", tmp / "ss.n"
        (tmp / "bin").mkdir()

        for f, text in (("iptables", FAKE_IPT), ("iptables-restore", FAKE_RESTORE), ("ss", FAKE_SS), ("systemctl", "#!/bin/sh\nexit 0\n")):
            (tmp / "bin" / f).write_text(text)
            (tmp / "bin" / f).chmod(0o755)

    def run(self, text: str, env: dict | None = None) -> str:
        r = subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=60, check=False,
                           env={"PATH": f"{self.tmp / 'bin'}:/usr/bin:/bin", "FAKE_IPT_STATE": str(self.state), "FAKE_SS_FILE": str(self.ss), **(env or {})})
        assert r.returncode == 0, r.stderr
        return r.stdout

    def action(self, name, env=None, **params):
        return self.run(ssh.render_action(name, params), env)

    def rules(self):
        return [tuple(r) for r in json.loads(self.state.read_text())["rules"]] if self.state.exists() else []

    def mine(self, tag):
        return [r for r in self.rules() if f"nikatel-{tag}" in r[2]]


needs_shell = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("python3") is None or shutil.which("awk") is None or shutil.which("sha1sum") is None, reason="needs bash, python3, awk and sha1sum")
PLAN2 = "tcp:443:20443:0.30000000,tcp:443:30443:0.42857143"


@needs_shell
def test_the_chain_is_put_in_one_transaction_with_a_jump_and_an_accept_for_every_port_and_idempotently(tmp_path):
    box = Box(tmp_path)
    box.state.write_text(json.dumps({"rules": [["nat", "PREROUTING", "-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998"], ["filter", "INPUT", "-j DROP"]], "chains": []}))              # somebody else's rules
    assert box.action("trident_set", NAME="de-ir1", PLAN=PLAN2).strip() == "TRIDENT_SET de-ir1 2"
    chain = [r for r in box.rules() if r[0] == "nat" and r[1].startswith("NKTR_")]
    assert len(chain) == 2 and all("REDIRECT" in r[2] for r in chain) and "--probability 0.30000000" in chain[0][2] and chain[0][2].endswith("--to-ports 20443") and chain[1][2].endswith("--to-ports 30443")
    jumps = [r for r in box.rules() if r[0] == "nat" and r[1] == "PREROUTING" and "nikatel-trident-de-ir1" in r[2]]
    assert len(jumps) == 1 and jumps[0][2].endswith("-j " + chain[0][1]) and "--dport 443" in jumps[0][2]
    inp = [r for r in box.mine("trident-de-ir1") if r[1] == "INPUT"]
    assert len(inp) == 2 and all(r[0] == "filter" and r[2].endswith("-j ACCEPT") for r in inp)                      # what was redirected must be let in
    assert ("nat", "PREROUTING", "-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998") in box.rules() and ("filter", "INPUT", "-j DROP") in box.rules()
    before = box.rules()
    assert box.action("trident_set", NAME="de-ir1", PLAN=PLAN2).strip() == "TRIDENT_SET de-ir1 2" and box.rules() == before        # idempotent

    # a new plan REPLACES the chain: the rule with no statistic match ("1") has none, no rule is added twice
    assert box.action("trident_set", NAME="de-ir1", PLAN="tcp:443:30443:1").strip() == "TRIDENT_SET de-ir1 1"
    chain = [r for r in box.rules() if r[0] == "nat" and r[1].startswith("NKTR_")]
    assert len(chain) == 1 and "statistic" not in chain[0][2] and chain[0][2].endswith("--to-ports 30443")
    assert len([r for r in box.rules() if r[0] == "nat" and r[1] == "PREROUTING" and "nikatel-trident-de-ir1" in r[2]]) == 1
    acc = [r for r in box.mine("trident-de-ir1") if r[1] == "INPUT"]
    assert any("--dport 30443" in r[2] and "ESTABLISHED" not in r[2] for r in acc) and any("--dport 20443" in r[2] and "ESTABLISHED,RELATED" in r[2] for r in acc)          # the strand that left the plan keeps an accept for its established connections only

    # what the panel reads is what it put
    got = trident.parse_list(box.run(ssh._script_text("trident_list", {})))
    assert got["de-ir1"]["rules"] == [("tcp", 443, 30443, 1.0)] and got["de-ir1"]["jumps"][0][:2] == ("tcp", 443) and got["de-ir1"]["jumps"][0][3] == 0

    # a second trident and a port that leaves the plan
    assert box.action("trident_set", NAME="de-ir2", PLAN="tcp:80:20080:0.50000000").strip() == "TRIDENT_SET de-ir2 1"
    assert box.action("trident_set", NAME="de-ir1", PLAN="tcp:443:30443:1,tcp:80:20080:0.50000000").strip() == "TRIDENT_SET de-ir1 2"
    assert box.action("trident_set", NAME="de-ir1", PLAN="tcp:80:20080:0.50000000").strip() == "TRIDENT_SET de-ir1 1"
    jumps = [r for r in box.rules() if r[0] == "nat" and r[1] == "PREROUTING" and "nikatel-trident-de-ir1" in r[2]]
    assert len(jumps) == 1 and "--dport 80" in jumps[0][2] and len(box.mine("trident-de-ir2")) == 3        # (a jump per port; de-ir2 is untouched)


@needs_shell
def test_a_chain_the_kernel_refuses_leaves_the_old_one_and_says_so(tmp_path):
    box = Box(tmp_path)
    box.action("trident_set", NAME="de-ir1", PLAN=PLAN2)
    before = box.rules()
    out = box.action("trident_set", env={"FAKE_RESTORE_FAIL": "1"}, NAME="de-ir1", PLAN="tcp:443:20443:1")
    assert out.startswith("TRIDENT_FAIL the kernel did not take the chain") and box.rules() == before


@needs_shell
def test_the_trident_ends_the_careful_way_and_the_parked_rules_go_only_when_no_connection_is_left_on_them(tmp_path):
    box = Box(tmp_path)
    box.state.write_text(json.dumps({"rules": [["nat", "PREROUTING", "-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998"]], "chains": []}))
    box.action("trident_set", NAME="de-ir1", PLAN=PLAN2)
    box.action("trident_set", NAME="de-ir2", PLAN="tcp:80:20080:0.50000000")                                           # another trident

    # PARK=1: nothing redirects any more, the JUMP is left in the chain on purpose, the accepts keep only the established connections
    box.ss.write_text("3")
    assert box.action("trident_off", NAME="de-ir1", PARK="1").strip() == "TRIDENT_OFF de-ir1 0 3 3"
    assert not [r for r in box.rules() if r[0] == "nat" and r[1].startswith("NKTR_") and "REDIRECT" in r[2] and "nikatel-trident-de-ir1" in r[2]]
    assert len([r for r in box.rules() if r[0] == "nat" and r[1] == "PREROUTING" and "nikatel-trident-de-ir1" in r[2]]) == 1
    inp = [r for r in box.mine("trident-de-ir1") if r[1] == "INPUT"]
    assert len(inp) == 2 and all("--ctstate ESTABLISHED,RELATED" in r[2] and r[2].endswith("-j ACCEPT") for r in inp)
    assert len(box.mine("trident-de-ir2")) == 3                                                                         # the other trident is untouched (its rule, its jump, its accept)
    assert box.action("trident_off", NAME="de-ir1", PARK="1").strip() == "TRIDENT_OFF de-ir1 0 3 3"                       # again: nothing is added twice

    # PARK=2: while a connection to the shifted ports is open the rules stay (and it says so) ...
    assert box.action("trident_off", NAME="de-ir1", PARK="2").strip() == "TRIDENT_OFF de-ir1 0 3 3"
    # ... when none is left they all go
    box.ss.write_text("0")
    assert box.action("trident_off", NAME="de-ir1", PARK="2").strip() == "TRIDENT_OFF de-ir1 0 0 0"
    assert box.mine("trident-de-ir1") == [] and not [r for r in box.rules() if r[1].startswith("NKTR_") and "nikatel-trident-de-ir1" in r[2]]
    assert len(box.mine("trident-de-ir2")) == 3 and ("nat", "PREROUTING", "-p tcp -m tcp --dport 9999 -j REDIRECT --to-ports 9998") in box.rules()

    # PARK=0 (the tunnel is going away): every rule of the tag, whatever is open
    box.ss.write_text("5")
    assert box.action("trident_off", NAME="de-ir2", PARK="0").strip() == "TRIDENT_OFF de-ir2 0 0 5"
    assert [r for r in box.rules() if "nikatel-trident-" in r[2]] == [] and box.action("trident_off", NAME="de-ir2", PARK="0").strip() == "TRIDENT_OFF de-ir2 0 0 0"


def test_a_server_without_iptables_restore_says_so_and_changes_nothing(tmp_path):
    out = subprocess.run([shutil.which("bash") or "bash", "-c", ssh.render_action("trident_set", {"NAME": "de-ir1", "PLAN": PLAN2})], capture_output=True, text=True, timeout=30, check=False, env={"PATH": str(tmp_path / "empty")})
    assert "TRIDENT_FAIL this server has no iptables-restore" in out.stdout


# ------------------------------------------------------------------ the service: a world to work in
@pytest.fixture(autouse=True)
def _clean_memory(monkeypatch):
    """What the module keeps in memory must not leak from one test to the next."""
    for d in (trident._BUILDS, trident._NOTE, trident._ALIVE, trident._CHECKED, trident._RESTARTED, trident._MISSING, trident._RETIRED, trident._LOCKS, trident._RETRY_AT):
        d.clear()

    for s in (trident._FORCE, trident._CHECKING, trident._REMOVING, trident._MEASURING):
        s.clear()

    trident._STATE.update({"init": False, "reconciled": 0.0, "cleaned": 0.0})
    tunnels.HANDED.clear()
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(autopilot, "repairing_names", lambda: set())
    monkeypatch.setattr(standby, "_busy_reason", lambda *a: "")
    monkeypatch.setattr(standby, "wake", lambda: None)
    monkeypatch.setattr(trident, "wake", lambda: None)
    monkeypatch.setattr(trident, "_check_async", lambda sid: None)
    monkeypatch.setattr(trident, "_measure_async", lambda name, it: None)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    yield


def world(name, state="up", since=300, bw=100.0, **kw):
    """Two servers and a Backhaul tunnel (capacity `bw` Mb/s by its light test) forwarding one port."""
    rec = {"bw": {"down": bw, "ref": bw, "at": clock()}} if bw else {}

    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        port = 8300 + n
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9300 + n, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, state_since=clock() - since, last_check_at=clock() - 2,
                   forwarded_ports=[port], foreign_tunnel_ip=f"10.8.{n}.2", record={**rec, "port_mapping": str(port)}, rtt_ms=40.0, detail={"loss_pct": 0.0}, **kw)
        db.add(t)
        db.flush()
        return {"id": t.id, "name": name, "iran": ir.id, "foreign": fr.id, "port": port}


def add_standby(w, state="up", since=300, use=None, rtt=60.0, loss=0.0):
    name = w["name"] + "-sb"
    shifted = [standby.shifted(w["port"])]

    with session() as db:
        t = Tunnel(name=name, type="chisel", transport="wss", enabled=False, port=9650, iran_server_id=w["iran"], foreign_server_id=w["foreign"], state=state, state_since=clock() - since,
                   last_check_at=clock() - 2, forwarded_ports=shifted, rtt_ms=rtt, detail={"loss_pct": loss},
                   record={"standby_of": w["name"], "port_mapping": f"{shifted[0]}:{w['port']}", "kind": "chisel", "built_by": "nikatel-panel", **({"use": use} if use else {})})
        db.add(t)
        db.flush()
        return t.id


def add_strand(w, state="up", since=300, rtt=80.0, loss=0.0, ports=None):
    name = w["name"] + "-sb2"
    shifted = ports if ports is not None else [standby.shifted(w["port"], trident.STRAND_SHIFT)]

    with session() as db:
        t = Tunnel(name=name, type="backpack", transport="udp", enabled=False, port=9651, iran_server_id=w["iran"], foreign_server_id=w["foreign"], state=state, state_since=clock() - since,
                   last_check_at=clock() - 2, forwarded_ports=shifted, rtt_ms=rtt, detail={"loss_pct": loss},
                   record={"strand_of": w["name"], "port_mapping": f"{shifted[0]}:{w['port']}", "kind": "bp:kcp", "built_by": "nikatel-panel"})
        db.add(t)
        db.flush()
        return t.id


def mark(name, on=True):
    st.put("trident", {"tunnels": {name: {"on": True, "since": clock(), "manual": True}} if on else {}})


def set_state(tid, state, since=300, **kw):
    with session() as db:
        t = db.get(Tunnel, tid)
        t.state, t.state_since = state, clock() - since

        for k, v in kw.items():
            setattr(t, k, v)


def fake_actions(monkeypatch, fail=False, off="TRIDENT_OFF {n} 0 0 0"):
    """The reviewed actions of the trident, answered like the real ones (nothing leaves the test)."""
    calls = []

    def run_action(node, name, params=None, timeout=90):
        calls.append((name, dict(params or {})))

        if name == "trident_set":
            if fail:
                return "TRIDENT_FAIL the kernel did not take the chain: x\n"

            return f"TRIDENT_SET {params['NAME']} {len(params['PLAN'].split(','))}\n"

        if name == "trident_off":
            return off.format(n=params["NAME"]) + "\n"

        if name == "standby_off":
            return f"STANDBY_OFF {params['NAME']} 0\n"

        if name == "standby_on":
            return f"STANDBY_ON {params['NAME']} {len(params['MAP'].split(',')) * len(params['PROTOS'].split())}\n"

        return "\n"

    monkeypatch.setattr(trident, "run_action", run_action)
    monkeypatch.setattr(standby, "run_action", run_action)
    return calls


def applied(name):
    return st.get("trident")["applied"].get(name) or {}


def plans(calls):
    return [c[1]["PLAN"] for c in calls if c[0] == "trident_set"]


# ------------------------------------------------------------------ the mark, who owns the users' ports
def test_a_hidden_strand_is_hidden_like_a_standby_but_is_not_the_standby_of_its_tunnel():
    w = world("de-ir1")
    sid = add_standby(w)
    xid = add_strand(w)

    with session() as db:
        assert is_standby(db.get(Tunnel, sid)) and is_standby(db.get(Tunnel, xid))
        assert db.get(Tunnel, xid).enabled is False

    v = standby._view("de-ir1")
    assert v["s"]["id"] == sid                                                                                         # the standby of the tunnel is the standby, never the strand
    assert trident._read("de-ir1")["s2"]["id"] == xid and trident._read("de-ir1")["s1"]["id"] == sid
    only_strand = world("fr-only")
    add_strand(only_strand)
    assert standby._view("fr-only")["s"] is None and trident._read("fr-only")["s1"] is None and trident._read("fr-only")["s2"] is not None


def test_the_trident_owns_the_users_ports_only_when_marked_applied_and_alive(monkeypatch):
    assert trident.owns("de-ir1") is False
    mark("de-ir1")
    assert trident.owns("de-ir1") is False                                                                               # marked, but nothing is put yet
    st.put("trident", {"applied": {"de-ir1": {"at": clock(), "state": "active"}}})
    assert trident.owns("de-ir1") is False                                                                               # ... and its job has not answered
    trident._ALIVE["de-ir1"] = time.time()
    assert trident.owns("de-ir1") is True
    trident._ALIVE["de-ir1"] = time.time() - trident.ALIVE_FOR - 1
    assert trident.owns("de-ir1") is False                                                                               # a job that stands still gives the standby its handover back
    trident._ALIVE["de-ir1"] = time.time()
    mark("de-ir1", on=False)
    assert trident.owns("de-ir1") is False


def test_while_the_trident_owns_the_ports_the_standby_does_not_hand_over_but_still_gives_back(monkeypatch):
    writable(monkeypatch)
    w = world("de-ir1", state="down", since=60)
    add_standby(w)
    calls = fake_actions(monkeypatch)
    st.put("standby", {"marked": {"de-ir1": {"manual": True}}})
    monkeypatch.setattr(standby, "_apply_renames", lambda: None)

    monkeypatch.setattr(trident, "owns", lambda n: False)
    standby._step("de-ir1")
    assert any(c[0] == "standby_on" for c in calls)                                                                   # without a Trident: the handover, as ever

    calls.clear()
    monkeypatch.setattr(trident, "owns", lambda n: True)
    standby.give_back("de-ir1", "test")
    standby._step("de-ir1")
    assert not any(c[0] == "standby_on" for c in calls)                                                                # with one: the Trident decides, the standby waits

    with pytest.raises(ValueError, match="Trident"):
        standby.handover("de-ir1", "by hand", manual=True)

    with pytest.raises(ValueError, match="no lane"):
        lanes.start("de-ir1", manual=True)


# ------------------------------------------------------------------ the extra strand
def test_the_extra_strand_is_planned_on_shifted_ports_and_a_kind_that_shares_no_fate(monkeypatch):
    w = world("de-ir1")
    add_standby(w)
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: (["tcpmux", "chisel:tls", "bp:tcpmux", "bpd:udp", "wg:tcpmux"], []))
    plan = trident.build_plan("de-ir1")
    assert plan["name"] == "de-ir1-sb2" and plan["ports"] == f"{w['port'] + 30000}:{w['port']}" and plan["mapping"] == {w["port"]: w["port"] + 30000}
    assert plan["kind"] in ("bpd:udp", "wg:tcpmux") and trident.traits(plan["kind"])["family"] == "udp"                    # not GRE (the tunnel), not TCP (the standby)

    with pytest.raises(ValueError, match="asked for|cannot be"):
        trident.build_plan("de-ir1", kind="tcpmux")

    assert trident.build_plan("de-ir1", kind="wg:tcpmux")["kind"] == "wg:tcpmux"

    with pytest.raises(ValueError, match="tunnel not found"):
        trident.build_plan("nobody")


def test_the_extra_strand_is_built_beside_a_tunnel_that_is_up_and_a_standby_that_is_up(monkeypatch):
    writable(monkeypatch)
    w = world("de-ir1")
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: (["bpd:udp"], []))
    started = []
    monkeypatch.setattr(trident.build_other, "start_build", lambda *a, **k: started.append((a, k)) or 77)
    monkeypatch.setattr(trident.builder, "start_build", lambda *a, **k: started.append((a, k)) or 78)

    with pytest.raises(ValueError, match="after the standby is up"):
        trident.build_strand("de-ir1")

    sid = add_standby(w)
    aid = trident.build_strand("de-ir1")
    assert aid == 77 and started[0][1]["standby_of"] == "de-ir1" and started[0][1]["hidden_key"] == "strand_of" and started[0][1]["forced_name"] == "de-ir1-sb2" and trident._BUILDS["de-ir1"]["aid"] == 77

    with pytest.raises(ValueError, match="being built"):
        trident.build_strand("de-ir1")

    trident._BUILDS.clear()
    set_state(sid, "down")

    with pytest.raises(ValueError, match="after the standby is up"):
        trident.build_strand("de-ir1")

    set_state(sid, "up")
    set_state(w["id"], "down")

    with pytest.raises(ValueError, match="tunnel is down"):
        trident.build_strand("de-ir1")

    set_state(w["id"], "up")
    monkeypatch.setattr(autopilot, "repairing_names", lambda: {"de-ir1"})

    with pytest.raises(ValueError, match="repair"):
        trident.build_strand("de-ir1")

    monkeypatch.setattr(autopilot, "repairing_names", lambda: set())
    add_strand(w)

    with pytest.raises(ValueError, match="already has"):
        trident.build_strand("de-ir1")

    monkeypatch.setattr(get_settings(), "read_only", True)

    with pytest.raises(ValueError, match="read-only"):
        trident.build_strand("de-ir1")


def test_a_build_that_failed_is_counted_and_its_kind_is_not_chosen_again_for_a_while(monkeypatch):
    from nikatel.models import ActionLog

    with session() as db:
        e = ActionLog(ref="de-ir1-sb2", action="standby", source="manual", story=["Building", "no route to host"], finished_at=clock(), ok=False)
        db.add(e)
        db.flush()
        aid = e.id

    trident._BUILDS["de-ir1"] = {"aid": aid, "name": "de-ir1-sb2", "kind": "bpd:udp", "at": clock()}
    trident._collect()
    f = st.get("trident")["failed"]["de-ir1"]
    assert "de-ir1" not in trident._BUILDS and f["n"] == 1 and "bpd:udp" in f["kinds"] and "no route" in f["why"]

    w = world("de-ir1")
    add_standby(w)
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: (["bpd:udp", "wg:tcpmux"], []))
    assert trident.build_plan("de-ir1")["kind"] == "wg:tcpmux"

    trident._BUILDS["de-ir1"] = {"aid": aid, "name": "x", "kind": "wg:tcpmux", "at": clock() - trident.BUILD_WAIT - 1}
    with session() as db:
        db.get(ActionLog, aid).finished_at = 0

    trident._collect()
    assert "de-ir1" not in trident._BUILDS                                                                                # a build that never finishes is given up


# ------------------------------------------------------------------ the conductor
def up_world(monkeypatch, name="de-ir1", strand=True, **kw):
    writable(monkeypatch)
    w = world(name, **kw)
    sid = add_standby(w)
    xid = add_strand(w) if strand else None
    mark(name)
    return w, sid, xid


def test_with_all_three_up_the_first_plan_lets_the_new_strands_in_by_steps_and_the_tunnel_carries_the_rest(monkeypatch):
    w, _sid, _xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    p = plans(calls)
    assert len(p) == 1
    a = applied("de-ir1")
    assert a["state"] == "active" and a["stages"] == [3, 0, 0] and a["usable"] == [True, True, True]
    assert a["w"][1] == pytest.approx(0.10) and a["w"][2] == pytest.approx(0.10) and a["w"][0] == pytest.approx(0.80)   # the standby and the strand are on their first step
    assert p[0].count("tcp:") == 2 and f"tcp:{w['port']}:{w['port'] + 20000}:0.10000000" in p[0] and f"tcp:{w['port']}:{w['port'] + 30000}:0.11111111" in p[0]
    assert trident.owns("de-ir1") is True and "de-ir1" in tunnels.HANDED                                                   # judged by what IT carries: a request from another server may be answered by a strand
    assert trident.status("de-ir1")["state"] == "active"

    # nothing changed a moment later: nothing is rewritten
    calls.clear()
    trident._step("de-ir1")
    assert plans(calls) == []


def test_the_strands_step_up_every_ramp_step_while_they_stay_healthy(monkeypatch):
    _w, _sid, _xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    a = applied("de-ir1")
    st.put("trident", {"applied": {"de-ir1": {**a, "stage_at": [clock() - 1000, clock() - trident.RAMP_STEP - 5, clock() - trident.RAMP_STEP - 5]}}})
    calls.clear()
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["stages"] == [3, 1, 1] and len(plans(calls)) == 1 and a["w"][1] == pytest.approx(0.25) and a["w"][2] == pytest.approx(0.25)


def test_a_strand_that_falls_loses_its_share_at_once_and_the_tunnel_that_falls_gives_everything_to_the_strands(monkeypatch):
    w, sid, xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    a = applied("de-ir1")
    st.put("trident", {"applied": {"de-ir1": {**a, "at": clock() - 3600, "stages": [3, 3, 3], "stage_at": [clock() - 9999] * 3}}})
    calls.clear()
    trident._step("de-ir1")                                                                                               # (the steps are full, an hour has passed: the shares follow the strands' own numbers, none above 70 %)
    a = applied("de-ir1")
    assert len(plans(calls)) == 1 and max(a["w"]) <= trident.CEIL_MANY + 1e-9 and min(a["w"]) >= trident.FLOOR and a["w"][0] > a["w"][2]            # (the tunnel has the best delay)

    calls.clear()
    set_state(xid, "down", since=3)
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["usable"] == [True, True, False] and a["w"][2] == 0.0 and len(plans(calls)) == 1 and f":{w['port'] + 30000}:" not in plans(calls)[0]          # the strand that fell has no rule
    assert sum(a["w"]) == pytest.approx(1.0)

    with session() as db:
        assert any("fell" in e.text for e in db.scalars(select(Event)))                                                  # one sentence in the events

    calls.clear()
    set_state(w["id"], "down", since=3)                                                                                 # now the tunnel as well: the standby takes everything, as ONE unconditional rule
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["usable"] == [False, True, False] and a["w"] == pytest.approx([0.0, 1.0, 0.0]) and plans(calls) == [f"tcp:{w['port']}:{w['port'] + 20000}:1"]

    calls.clear()
    set_state(sid, "down", since=3)                                                                                     # everything down: nothing to share, the chain is emptied and parked
    trident._step("de-ir1")
    assert applied("de-ir1")["state"] == "idle" and [c for c in calls if c[0] == "trident_off" and c[1]["PARK"] == "1"] and "de-ir1" not in tunnels.HANDED


def test_a_tunnel_that_is_being_rebuilt_gives_its_share_to_the_strands_until_its_row_is_back(monkeypatch):
    w, _sid, _xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["w"][0] == pytest.approx(0.80) and a["ports"] == [w["port"]]

    with session() as db:                                                                                              # the Doctor removed the tunnel and builds it again: the row is gone for a minute or two
        db.delete(db.get(Tunnel, w["id"]))

    calls.clear()
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["usable"] == [False, True, True] and a["w"][0] == 0.0 and sum(a["w"]) == pytest.approx(1.0) and len(plans(calls)) == 1
    assert plans(calls)[0].endswith(":1") and plans(calls)[0].count("tcp:") == 2 and "being rebuilt" in a["why"]       # the last rule takes whatever reaches it
    calls.clear()
    trident._step("de-ir1")
    assert plans(calls) == [] and trident.owns("de-ir1") is True                                                          # (done once; the trident stays alive)

    # a tunnel whose share is already 0 (it was cut before the rebuild) or a trident that is not active is left alone
    calls.clear()
    st.put("trident", {"applied": {"de-ir1": {**a, "state": "idle"}}})
    trident._step("de-ir1")
    assert plans(calls) == []


def test_a_strand_that_comes_back_starts_on_the_first_step_and_only_after_it_settled(monkeypatch):
    _w, _sid, xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    st.put("trident", {"applied": {"de-ir1": {"at": clock() - 3600, "state": "active", "usable": [True, True, False], "stages": [3, 3, 0], "stage_at": [clock() - 9999] * 3, "w": [0.5, 0.5, 0.0], "plan": "x"}}})
    set_state(xid, "up", since=5)                                                                                       # it just came back: it has to settle
    trident._step("de-ir1")
    assert plans(calls) == [] or applied("de-ir1")["usable"][2] is False

    set_state(xid, "up", since=trident.USABLE_AFTER + 5)
    calls.clear()
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["usable"][2] is True and a["stages"][2] == 0 and a["w"][2] == pytest.approx(0.10) and len(plans(calls)) == 1


def test_a_lossy_or_slow_strand_carries_less_and_small_drifts_are_not_rewritten(monkeypatch):
    _w, _sid, xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    st.put("trident", {"applied": {"de-ir1": {"at": clock() - 3600, "state": "active", "usable": [True, True, True], "stages": [3, 3, 3], "stage_at": [clock() - 9999] * 3, "w": [1 / 3, 1 / 3, 1 / 3], "plan": "x"}}})
    with session() as db:
        db.get(Tunnel, xid).detail = {"loss_pct": 10.0}

    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["w"][2] < a["w"][0] and a["w"][2] > 0 and len(plans(calls)) == 1                                           # a big drift, after the wait: rewritten

    calls.clear()
    with session() as db:
        db.get(Tunnel, xid).detail = {"loss_pct": 10.5}

    trident._step("de-ir1")
    assert plans(calls) == []                                                                                          # a small drift: left


def test_a_server_that_does_not_take_the_chain_keeps_what_stood_and_says_why(monkeypatch):
    _w, _sid, _xid = up_world(monkeypatch)
    fake_actions(monkeypatch, fail=True)
    trident._step("de-ir1")
    assert not applied("de-ir1").get("at") and "could not be put" in trident._NOTE["de-ir1"] and trident.owns("de-ir1") is False
    assert trident.status("de-ir1")["state"] == "waiting" and "could not be put" in trident.status("de-ir1")["why"]


def test_the_trident_waits_for_the_standby_builds_the_strand_and_does_nothing_when_the_doctor_is_off(monkeypatch):
    writable(monkeypatch)
    w = world("de-ir1")
    mark("de-ir1")
    calls = fake_actions(monkeypatch)
    marked = []
    monkeypatch.setattr(standby, "_mark", lambda n, on, manual=False: marked.append((n, on)))
    trident._step("de-ir1")
    assert marked == [("de-ir1", True)] and "waiting for the standby" in trident._NOTE["de-ir1"] and plans(calls) == []

    sid = add_standby(w)
    built = []
    monkeypatch.setattr(trident, "build_strand", lambda n, kind="": built.append(n) or 5)
    trident._step("de-ir1")
    assert built == ["de-ir1"]
    assert applied("de-ir1")["state"] == "active"                                                                       # (the tunnel and the standby are enough to start: the third strand joins when it is up)

    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    calls.clear()
    set_state(sid, "down", since=3)
    trident._step("de-ir1")
    assert calls == [] and "Doctor is off" in trident._NOTE["de-ir1"]


def test_a_handover_that_stands_makes_the_trident_wait_until_the_ports_are_back(monkeypatch):
    w, sid, _xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    with session() as db:
        t = db.get(Tunnel, sid)
        t.record = {**t.record, "use": {"since": clock(), "map": "x", "protos": "tcp", "iran_id": w["iran"]}}

    trident._step("de-ir1")
    assert plans(calls) == [] and "handover" in trident._NOTE["de-ir1"]


def test_a_strand_that_is_not_up_is_restarted_then_built_again_and_one_that_no_longer_fits_is_removed(monkeypatch):
    w, _sid, xid = up_world(monkeypatch)
    fake_actions(monkeypatch)
    restarts, removed = [], []
    monkeypatch.setattr(trident, "_restart_strand", lambda s: restarts.append(s["name"]))
    monkeypatch.setattr(trident, "remove_strand", lambda n: removed.append(n) or [])
    set_state(xid, "down", since=trident.UNHEALTHY_AFTER + 5)
    trident._step("de-ir1")
    import threading
    for t in threading.enumerate():
        if t.name.startswith("trident-restart"):
            t.join(2)

    assert restarts == ["de-ir1-sb2"] and removed == []

    set_state(xid, "down", since=trident.REBUILD_AFTER + 5)
    trident._RESTARTED.clear()
    trident._step("de-ir1")
    for t in threading.enumerate():
        if t.name.startswith("trident-remove"):
            t.join(2)

    assert removed == ["de-ir1"]

    # a strand whose ports no longer are the tunnel's: removed (and built again by the next turn), its share is 0 meanwhile
    removed.clear()
    set_state(xid, "up")
    with session() as db:
        t = db.get(Tunnel, xid)
        t.record = {**t.record, "port_mapping": "31999:" + str(w["port"])}

    trident._REMOVING.clear()
    trident._step("de-ir1")
    for t in threading.enumerate():
        if t.name.startswith("trident-remove"):
            t.join(2)

    assert removed == ["de-ir1"] and applied("de-ir1")["usable"][2] is False


# ------------------------------------------------------------------ the way out
def test_switching_on_marks_the_tunnel_stops_a_lane_and_refuses_a_standing_handover(monkeypatch):
    writable(monkeypatch)
    w = world("de-ir1")
    sid = add_standby(w)
    marked = []
    monkeypatch.setattr(standby, "_mark", lambda n, on, manual=False: marked.append((n, on)))
    stopped = []
    monkeypatch.setattr(lanes, "status", lambda n: {"state": "sharing"})
    monkeypatch.setattr(lanes, "stop", lambda n, why="": stopped.append(n) or {"ok": True})
    r = trident.switch(w["id"], True)
    assert r["on"] is True and "de-ir1" in trident.marks() and stopped == ["de-ir1"] and ("de-ir1", True) in marked

    trident.marks().clear()
    st.put("trident", {"tunnels": {}})
    with session() as db:
        t = db.get(Tunnel, sid)
        t.record = {**t.record, "use": {"since": clock()}}

    with pytest.raises(ValueError, match="handover"):
        trident.switch(w["id"], True)

    with session() as db:
        t = db.get(Tunnel, w["id"])
        t.forwarded_ports = []

    with pytest.raises(ValueError, match="no port"):
        trident.switch(w["id"], True)

    with session() as db:
        x = db.get(Tunnel, sid)

    with pytest.raises(ValueError, match="no Trident"):
        trident.switch(x.id, True)

    with pytest.raises(ValueError, match="not found"):
        trident.switch(99999, True)

    monkeypatch.setattr(get_settings(), "read_only", True)

    with pytest.raises(ValueError, match="read-only"):
        trident.switch(w["id"], True)


def test_switching_off_parks_the_chain_and_the_parked_rules_go_when_no_connection_is_left(monkeypatch):
    w, _sid, _xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    removed = []
    monkeypatch.setattr(trident, "remove_strand", lambda n: removed.append(n) or [])
    import threading
    trident.switch(w["id"], False)

    for t in threading.enumerate():
        if t.name.startswith("trident-off"):
            t.join(3)

    assert "de-ir1" not in trident.marks() and removed == ["de-ir1"] and [c for c in calls if c[0] == "trident_off" and c[1]["PARK"] == "1"] and "de-ir1" not in tunnels.HANDED
    a = applied("de-ir1")
    assert a["parked"] is True and a["off"] is True and trident.owns("de-ir1") is False and trident.status("de-ir1")["state"] == "winding_down"

    # connections still open on the shifted ports: the parked rules stay; the job asks again
    monkeypatch.setattr(trident, "_off", lambda node, n, park: (0, 3, 4))
    trident._clean_parked()
    assert applied("de-ir1")
    monkeypatch.setattr(trident, "_off", lambda node, n, park: (0, 0, 0))
    trident._clean_parked()
    assert applied("de-ir1") == {}


def test_forgetting_a_tunnel_takes_the_rules_the_mark_and_the_strand_away(monkeypatch):
    _w, _sid, _xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    removed = []
    monkeypatch.setattr(trident, "remove_strand", lambda n: removed.append(n) or [])
    import threading
    trident.forget("de-ir1")

    for t in threading.enumerate():
        if t.name.startswith("trident-forget"):
            t.join(3)

    assert "de-ir1" not in trident.marks() and applied("de-ir1") == {} and removed == ["de-ir1"] and [c for c in calls if c[0] == "trident_off" and c[1]["PARK"] == "0"]
    assert st.get("trident")["failed"] == {}


def test_a_rename_moves_the_record_and_the_old_rules_are_parked_so_the_new_name_starts_its_own_chain(monkeypatch):
    w, _sid, xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    import threading
    trident.renamed("de-ir1", "de-ir9")

    for t in threading.enumerate():
        if t.name.startswith("trident-retire"):
            t.join(3)

    assert "de-ir9" in trident.marks() and "de-ir1" not in trident.marks()
    assert [c for c in calls if c[0] == "trident_off" and c[1] == {"NAME": "de-ir1", "PARK": "1"}] and trident._RETIRED["de-ir1"] == (w["iran"],)
    assert applied("de-ir9")["at"] == 0 and trident.owns("de-ir9") is False                                              # the new name has put nothing yet: the standby covers it meanwhile

    with session() as db:
        assert (db.get(Tunnel, xid).record or {}).get("strand_of") == "de-ir9"


def test_the_reconcile_puts_again_what_a_reboot_or_a_flushed_firewall_took_and_parks_what_nobody_asked_for(monkeypatch):
    _w, _sid, _xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    plan = applied("de-ir1")["plan"]
    assert plan

    answers = {"out": ""}
    monkeypatch.setattr(trident, "run_script", lambda node, name, timeout=20: answers["out"])
    trident.reconcile()                                                                                                    # the server has none of it
    assert "de-ir1" in trident._FORCE

    trident._FORCE.clear()
    lines = []

    for e in plan.split(","):
        proto, port, to, prob = e.split(":")
        lines.append(f"TRULE de-ir1 {proto} {port} {to} {prob}")

    answers["out"] = "\n".join(lines) + "\nTJUMP de-ir1 tcp 1 NKTR_x 0\nTRULE ghost tcp 80 20080 0.5\n"
    calls.clear()
    trident.reconcile()
    assert "de-ir1" not in trident._FORCE                                                                                  # what stands is what was put
    assert [c for c in calls if c[0] == "trident_off" and c[1] == {"NAME": "ghost", "PARK": "1"}]                          # a trident nobody asked for is parked


def test_rebalance_puts_the_plan_at_the_next_turn_whatever_the_weights_did(monkeypatch):
    _w, _sid, _xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    calls.clear()
    trident._step("de-ir1")
    assert plans(calls) == []
    trident.rebalance("de-ir1")
    trident._step("de-ir1")
    assert len(plans(calls)) == 1 and "de-ir1" not in trident._FORCE

    with pytest.raises(ValueError, match="no Trident"):
        trident.rebalance("nobody")


def test_a_tunnel_whose_row_is_gone_keeps_its_rules_for_a_while_and_loses_the_mark_after_hours(monkeypatch):
    writable(monkeypatch)
    mark("ghost")
    forgot = []
    monkeypatch.setattr(trident, "forget", lambda n: forgot.append(n))
    trident._step("ghost")
    assert forgot == [] and trident.owns("ghost") is False
    trident._MISSING["ghost"] = clock() - trident.MISSING_GRACE - 1
    trident._step("ghost")
    assert forgot == ["ghost"]


# ------------------------------------------------------------------ the page, the API, the job
def test_the_api_switches_the_trident_reads_it_and_every_tunnel_carries_it(client, monkeypatch):
    writable(monkeypatch)
    w = world("de-ir1")
    add_standby(w)
    marked = []
    monkeypatch.setattr(standby, "_mark", lambda n, on, manual=False: marked.append((n, on)))
    monkeypatch.setattr(standby, "_marks", lambda: {"de-ir1": {}})
    r = client.get(f"/api/tunnels/{w['id']}/trident")
    assert r.status_code == 200 and r.json()["state"] == "off" and r.json()["on"] is False

    r = client.put(f"/api/tunnels/{w['id']}/trident", json={"on": True})
    assert r.status_code == 200 and r.json()["on"] is True and "de-ir1" in trident.marks()
    one = next(t for t in client.get("/api/tunnels").json() if t["name"] == "de-ir1")
    assert one["trident"]["on"] is True and one["trident"]["state"] == "waiting" and client.get(f"/api/tunnels/{w['id']}").json()["trident"]["on"] is True

    calls = fake_actions(monkeypatch)
    assert client.post(f"/api/tunnels/{w['id']}/trident/rebalance").status_code == 200 and "de-ir1" in trident._FORCE and calls == []
    r = client.put(f"/api/tunnels/{w['id']}/trident", json={"on": False})
    assert r.status_code == 200 and r.json()["on"] is False

    assert client.get("/api/tunnels/99999/trident").status_code == 404 and client.put("/api/tunnels/99999/trident", json={"on": True}).status_code == 400
    assert client.post("/api/tunnels/99999/trident/rebalance").status_code == 404

    hidden = add_strand(w)
    assert client.get(f"/api/tunnels/{hidden}/trident").status_code == 404                                                # a strand has no Trident of its own
    assert client.put(f"/api/tunnels/{w['id']}/trident", json={"on": "maybe"}).status_code == 422


def test_trident_in_create_tunnel_builds_best_of_the_day_and_marks_the_tunnel(client, monkeypatch):
    """Create tunnel → Automatic → Trident (the owner, 2026-10-23: "give our three-layer tunnel a cool technical name and put it there")."""
    from nikatel.models import ActionLog
    from nikatel.services import create

    writable(monkeypatch)

    with session() as db:
        ir, fr = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        irid, frid = ir.id, fr.id

    monkeypatch.setattr(knowledge, "best_kinds", lambda i, f, allowed=None, now=None, limit=1: ([("bpd:pck", "the fastest on this pair"), ("chisel:fwd", "the second")], ""))
    built = []

    def fake_build(iran_id, foreign_id, ports, kind, transport="", opts=None, forced_name="", measure="full", standby_of="", hidden_key="standby_of"):
        built.append((kind, transport))

        with session() as db:
            db.add(Tunnel(name="fi1-ir1", type="backpack_direct", transport="l3:pck", iran_server_id=iran_id, foreign_server_id=foreign_id, state="up", state_since=clock(), forwarded_ports=[8075], record={"port_mapping": "8075", "kind": "bpd:pck"}))
            e = ActionLog(ref="fi1-ir1", action="build", source="manual", story=["Built."], ok=True, finished_at=clock())
            db.add(e)
            db.flush()
            return e.id

    monkeypatch.setattr(trident.build_other, "start_build", fake_build)
    monkeypatch.setattr(create, "_POLL", 0.05)
    marked = []
    monkeypatch.setattr(standby, "_mark", lambda n, on, manual=False: marked.append((n, on)))
    r = client.post("/api/tunnels", json={"iran_id": irid, "foreign_id": frid, "ports": "8075", "kind": "trident"})
    assert r.status_code == 202, r.text
    assert r.json()["trident"] is True and r.json()["kind"] == "backpack_direct" and "fastest" in r.json()["why"]
    aid = r.json()["id"]

    for _ in range(100):
        s = client.get(f"/api/actions/{aid}").json()

        if s["done"]:
            break

        time.sleep(0.1)

    text = "\n".join(s["story"])
    assert s["done"] and s["ok"] is True and built == [("backpack_direct", "pck")] and "Trident: fi1-ir1 is marked" in text, text
    assert "fi1-ir1" in trident.marks() and ("fi1-ir1", True) in marked and trident.status("fi1-ir1")["state"] == "waiting"
    assert client.post("/api/tunnels", json={"iran_id": 0, "foreign_id": frid, "ports": "8075", "kind": "trident"}).status_code == 400        # the pair must be known: the smart choice of the Iran server cannot be
    assert "Trident" in client.post("/api/tunnels", json={"iran_id": 0, "foreign_id": frid, "ports": "8075", "kind": "trident"}).text


def test_deleting_a_tunnel_takes_its_trident_with_it(client, monkeypatch):
    writable(monkeypatch)
    w = world("de-ir1")
    add_standby(w)
    mark("de-ir1")
    gone = []
    monkeypatch.setattr(trident, "forget", lambda n: gone.append(n))
    monkeypatch.setattr(standby, "forget", lambda n: trident.forget(n))
    monkeypatch.setattr("nikatel.api.tunnels.builder.delete_tunnel", lambda *a, **k: [])
    assert client.delete(f"/api/tunnels/{w['id']}").status_code == 200 and gone == ["de-ir1"]


def test_the_tunnel_window_and_the_job_know_the_trident():
    import inspect

    from nikatel import main

    web = Path(__file__).resolve().parent.parent / "nikatel" / "web"
    app = (web / "app.js").read_text(encoding="utf-8")
    assert "function tunTrident(t)" in app and "tunStandby(t) + tunTrident(t) + tunBwBox(t)" in app                           # a bar under the standby's
    assert all(f"window.{f} = async" in app for f in ("tunTridentSet", "tunTridentRebalance")) and "/trident/rebalance" in app and "`/api/tunnels/${id}/trident`" in app
    assert '{key: "trident", label: "Trident"}' in app and '"auto:trident": "three tunnels of different kinds' in app                 # Create tunnel → Automatic → Trident
    assert 'kind: bk.kind === "auto" && bk.transport === "trident" ? "trident" : bk.kind' in app and 'K.key === "auto" && tr.key === "trident"' in app and "🔱 Trident" in app
    assert 'jobs.register("trident", 5, trident.run)' in inspect.getsource(main._register_jobs)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_bar_of_the_trident_says_what_every_state_means_and_shows_the_shares():
    web = Path(__file__).resolve().parent.parent / "nikatel" / "web"
    js = (web / "app.js").read_text(encoding="utf-8")
    fn = "const TR_WORD" + js.split("const TR_WORD", 1)[1].split("/* the warm standby", 1)[0]
    code = ("const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');\n" + fn + "\n"
            "const bar = w => tunTrident({id: 7, trident: w});\n"
            "const strands = [{kind: 'tcpmux', usable: true, weight: 80, step: 3}, {kind: 'bp:wssmux', usable: true, weight: 10, step: 0}, {kind: 'bpd:udp', usable: false, weight: 0, step: 0}];\n"
            "const out = {off: bar({state: 'off'}), none: tunTrident({id: 7}), waiting: bar({state: 'waiting', why: 'waiting for the standby (strand 1) to be built'}), building: bar({state: 'building', why: 'the extra strand (bpd:udp) is being built'}),\n"
            "  active: bar({state: 'active', strands}), activeNoRows: bar({state: 'active', weights: [80, 10, 10]}), failed: bar({state: 'failed', why: 'the last strand build failed (x)'}), ending: bar({state: 'winding_down', why: 'ending'})};\n"
            "console.log(JSON.stringify(out));")
    out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert "Trident · Off" in out["off"] and "tunTridentSet(7, true)" in out["off"] and out["off"] == out["none"] and "Nobody is cut" in out["off"]
    assert "Trident · Starting…" in out["waiting"] and "waiting for the standby" in out["waiting"] and "tunTridentSet(7, false)" in out["waiting"] and "tunTridentSet(7, true)" not in out["waiting"]
    assert "Trident · Building…" in out["building"] and "bpd:udp" in out["building"]
    assert "Trident · Active" in out["active"] and "tcpmux 80 %" in out["active"] and "bp:wssmux 10 % ↗" in out["active"] and "bpd:udp down" in out["active"] and "tunTridentRebalance(7)" in out["active"] and "tunTridentSet(7, false)" in out["active"]
    assert "80 / 10 / 10 %" in out["activeNoRows"] and "lab-ban ok" in out["active"] and "lab-ban bad" in out["failed"] and "lab-ban warn" in out["building"]
    assert "Trident · Ending…" in out["ending"] and "tunTridentSet" not in out["ending"]


def test_the_trident_job_has_nothing_to_do_without_a_mark_and_a_turn_for_each_marked_tunnel(monkeypatch):
    from nikatel import jobs

    monkeypatch.setattr(jobs, "stopping", lambda: False)
    seen = []
    monkeypatch.setattr(trident, "_step", lambda name: seen.append(name))
    monkeypatch.setattr(trident, "reconcile", lambda: None)
    monkeypatch.setattr(trident, "_clean_parked", lambda: None)
    trident.run()
    assert seen == []
    st.put("trident", {"tunnels": {"b": {"on": True}, "a": {"on": True}}})
    trident.run()
    assert sorted(seen) == ["a", "b"]


# ------------------------------------------------------------------ what the tunnel window shows
def test_the_window_shows_one_row_per_strand_with_its_share_and_the_state_of_the_trident(monkeypatch):
    _w, _sid, _xid = up_world(monkeypatch)
    fake_actions(monkeypatch)
    assert trident.status("de-ir1")["state"] == "waiting" and trident.status("nobody")["state"] == "off"
    trident._step("de-ir1")
    s = trident.status("de-ir1")
    assert s["on"] is True and s["state"] == "active" and [r["role"] for r in s["strands"]] == ["tunnel", "standby", "strand"] and [r["weight"] for r in s["strands"]] == [80, 10, 10]
    assert s["strands"][1]["kind"] and s["strands"][0]["step"] == 3 and s["strands"][2]["step"] == 0
    sm = trident.summaries(["de-ir1", "nobody"])
    assert sm["nobody"] == {"on": False, "state": "off", "why": ""} and sm["de-ir1"]["weights"] == [80, 10, 10] and sm["de-ir1"]["strands"] == s["strands"] and sm["de-ir1"]["state"] == "active"
    trident._BUILDS["de-ir1"] = {"aid": 1, "name": "x", "kind": "bpd:udp", "at": clock()}
    assert trident.status("de-ir1")["state"] == "building" and "bpd:udp" in trident.status("de-ir1")["why"]
