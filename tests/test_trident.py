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
STALL_ASYNC = trident._stall_async          # (the real thread of the stall look, kept before the fixture below replaces it)


def clock() -> int:
    return int(time.time())


def writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)


# ------------------------------------------------------------------ the kinds
def test_every_one_of_the_80_kinds_has_traits_and_the_families_are_what_a_filter_sees():
    kinds = [k[0] for k in tunnel_test.KINDS]
    assert len(kinds) == 80          # 64 + the four C-Tunnel L2 carriers x 4 transports

    for k in kinds:
        t = trident.traits(k)
        assert set(t) == {"family", "l4", "dial", "tls", "enc", "over"} and isinstance(t["enc"], bool) and 0 <= t["over"] <= 100 and t["family"] in ("gre", "ipip", "esp", "udp", "tcp", "icmp", "l2tp") and t["dial"] in ("iran", "foreign")
    assert trident.traits("gretap:tcpmux")["family"] == "gre" and trident.traits("vxlan:tcpmux")["family"] == "udp" and trident.traits("l2tpv3:tcpmux")["family"] == "l2tp"

    assert trident.traits("tcpmux")["family"] == "gre" and trident.traits("wssmux")["tls"] is True
    assert trident.traits("wg:tcpmux")["family"] == "udp" and trident.traits("fou:tcpmux")["family"] == "udp" and trident.traits("ipip:tcpmux")["family"] == "ipip" and trident.traits("ipsec:tcpmux")["family"] == "esp"
    assert trident.traits("bp:wssmux") == {"family": "tcp", "l4": "tcp", "dial": "foreign", "tls": True, "enc": True, "over": 0}
    assert trident.traits("bp:kcp")["family"] == "udp" and trident.traits("bp:xdi")["family"] == "icmp" and trident.traits("bp:pck")["l4"] == "tcp-raw"
    assert trident.traits("bpd:pck")["dial"] == "iran" and trident.traits("bpd:udp")["family"] == "udp"
    assert trident.traits("chisel:fwdtls") == {"family": "tcp", "l4": "tcp", "dial": "iran", "tls": True, "enc": True, "over": 0} and trident.traits("chisel")["dial"] == "foreign"
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
    for d in (trident._BUILDS, trident._NOTE, trident._ALIVE, trident._CHECKED, trident._RESTARTED, trident._MISSING, trident._RETIRED, trident._LOCKS, trident._RETRY_AT, trident._STALL, trident._STALL_AT):
        d.clear()

    for s in (trident._FORCE, trident._CHECKING, trident._REMOVING, trident._MEASURING, trident._STALLING):
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
    monkeypatch.setattr(trident, "_stall_async", lambda name, p, items: None)          # (the stall look of 1.69.226: a thread and an ssh - the tests of the look run it by hand with a stand-in for run_script)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    yield


def world(name, state="up", since=300, bw=100.0, **kw):
    """Two servers and a Backhaul tunnel (capacity `bw` Mb/s by its light test) forwarding one port."""
    trident._LOSS.clear()                  # (the loss history and the stall looks of the strands are kept in memory by row id: a new world's rows take the same ids)
    trident._STALL.clear()
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


def fake_actions(monkeypatch, fail=False, off="TRIDENT_OFF {n} 0 0 0", relay_reach=None):
    """The reviewed actions of the trident, answered like the real ones (nothing leaves the test). `relay_reach`: how many of the relay's targets answer (None = all)."""
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

        if name == "relay_on":
            n = len(params["SPEC"].split(","))
            return f"RELAY_ON {params['NAME']} {n} {n if relay_reach is None else relay_reach}/{n}\n"

        if name == "relay_off":
            return f"RELAY_OFF {params['NAME']}\n"

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
    trident._step("de-ir1")                                                                                               # (the steps are full, an hour has passed: the shares follow the strands' own numbers, none above 94 % - the Trident is lean by default)
    a = applied("de-ir1")
    assert len(plans(calls)) == 1 and max(a["w"]) <= trident.CEIL_LEAN + 1e-9 and min(a["w"]) >= trident.FLOOR_LEAN - 1e-9 and a["w"][0] > a["w"][2]            # (the tunnel has the best delay)

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
    assert "function tunTrident(t)" in app and "tunStandby(t) + tunTrident(t) + tunLane(t) + tunGrid(t)" in app                           # a bar under the standby's
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
            "const strands = [{kind: 'tcpmux', usable: true, weight: 80, step: 3, overhead: 5, stalls: 0.4}, {kind: 'bp:wssmux', usable: true, weight: 10, step: 0, overhead: 34, stalls: 2.5, stall_p95: 1.8}, {kind: 'bpd:udp', usable: false, weight: 0, step: 0, overhead: 2, stalls: null}, {role: 'relay', kind: 'chisel:tls', usable: true, weight: 5, step: 3, overhead: 4}];\n"
            "const out = {off: bar({state: 'off'}), none: tunTrident({id: 7}), waiting: bar({state: 'waiting', why: 'waiting for the standby (strand 1) to be built'}), building: bar({state: 'building', why: 'the extra strand (bpd:udp) is being built'}),\n"
            "  active: bar({state: 'active', strands, lean: true}), activeNoRows: bar({state: 'active', weights: [80, 10, 10]}), failed: bar({state: 'failed', why: 'the last strand build failed (x)'}), ending: bar({state: 'winding_down', why: 'ending'})};\n"
            "console.log(JSON.stringify(out));")
    out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert "Trident · Off" in out["off"] and "tunTridentSet(7, true)" in out["off"] and out["off"] == out["none"] and "Nobody is cut" in out["off"]
    assert "Trident · Starting…" in out["waiting"] and "waiting for the standby" in out["waiting"] and "tunTridentSet(7, false)" in out["waiting"] and "tunTridentSet(7, true)" not in out["waiting"]
    assert "Trident · Building…" in out["building"] and "bpd:udp" in out["building"]
    assert "Trident · Active" in out["active"] and "tcpmux 80 %" in out["active"] and "bp:wssmux 10 % ↗" in out["active"] and "bpd:udp down" in out["active"] and "tunTridentRebalance(7)" in out["active"] and "tunTridentSet(7, false)" in out["active"]
    assert "relay chisel:tls 5 %" in out["active"]                                                                           # the relay strand (through another foreign server) is named as such
    assert "(+34 % on the line)" in out["active"] and "(+5 %" not in out["active"] and "(+4 %" not in out["active"]          # a heavy kind says what it costs on the Iran server's line; a light one says nothing
    assert ">lean</span>" in out["active"] and "lean" not in out["activeNoRows"]                                              # the lean Trident is named in the bar
    assert "stalls 2.5 %" in out["active"] and out["active"].count("stalls ") == 1 and "stalls 0" not in out["active"]      # the stall look (1.69.226): a strand that stalled says so from 1 %; one under it and one never looked at say nothing
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
    assert s["strands"][1]["kind"] and s["strands"][0]["step"] == 3 and s["strands"][2]["step"] == 0 and s["lean"] is True and all("overhead" in r for r in s["strands"])
    sm = trident.summaries(["de-ir1", "nobody"])
    assert sm["nobody"] == {"on": False, "state": "off", "why": ""} and sm["de-ir1"]["weights"] == [80, 10, 10] and sm["de-ir1"]["strands"] == s["strands"] and sm["de-ir1"]["state"] == "active"
    trident._BUILDS["de-ir1"] = {"aid": 1, "name": "x", "kind": "bpd:udp", "at": clock()}
    assert trident.status("de-ir1")["state"] == "building" and "bpd:udp" in trident.status("de-ir1")["why"]


# ------------------------------------------------------------------ what a strand costs and what it hides (1.69.126)
def test_every_kind_has_the_bytes_it_adds_to_a_packet_and_whether_an_observer_can_read_it():
    """The owner, 2026-10-24: "stable in every condition and still a light tunnel"."""
    t = trident.traits
    assert t("tcpmux")["over"] == 24 and t("tcpmux")["enc"] is False and t("wssmux")["enc"] is True and t("wssmux")["over"] == 24
    assert t("bp:wssmux")["over"] == 0 and t("bp:wssmux")["enc"] is True and t("bp:tcpmux")["over"] == 0 and t("bp:tcpmux")["enc"] is False          # (a TCP stream cuts its packets to size by itself)
    assert t("bpd:pck")["over"] == 40 and t("bpd:xdi")["over"] == 52 and t("bpd:udp")["over"] == 28 and t("bp:kcp")["over"] == 28
    assert t("ipsec:tcpmux")["over"] == 56 and t("ipsec:tcpmux")["enc"] and t("wg:tcpmux")["over"] == 60 and t("wg:tcpmux")["enc"] and t("ipip:tcpmux")["over"] == 20 and not t("ipip:tcpmux")["enc"]
    assert t("bp:quic")["enc"] and t("bpd:quic")["enc"] and t("chisel:tls")["enc"] and not t("chisel:fwd")["enc"] and t("wgchisel:tcpmux")["over"] == 80
    assert trident.mtu_of("tcpmux") == 1476 and trident.mtu_of("bpd:xdi") == 1448 and trident.mtu_of("tcpmux", 1320) == 1320 and trident.mtu_of("tcpmux", 0) == 1476 and trident.mtu_of("tcpmux", "x") == 1476


def test_a_fat_header_lowers_the_quality_a_little_and_never_below_half():
    assert trident.mtu_factor(None) == 1.0 and trident.mtu_factor(1476) == 1.0 and trident.mtu_factor(1400) == 1.0
    assert abs(trident.mtu_factor(1280) - 1280 / 1400) < 1e-9 and trident.mtu_factor(300) == 0.5
    base = trident.quality(0.0, 50.0, 50.0, 1.0)
    assert trident.quality(0.0, 50.0, 50.0, 1.0, 1320) < base and trident.quality(0.0, 50.0, 50.0, 1.0, 1476) == base


def test_among_strands_that_are_alike_the_lighter_one_gets_the_bigger_share():
    one = {"usable": True, "cap": 100.0, "rtt": 50.0, "loss": 0.0, "stab": 1.0, "stage": 3}
    w = trident.target_weights([{**one, "mtu": 1476}, {**one, "mtu": 1300}])
    assert w[0] > w[1] and abs(sum(w) - 1.0) < 1e-9
    same = trident.target_weights([{**one, "mtu": 1476}, {**one, "mtu": 1476}])
    assert abs(same[0] - same[1]) < 1e-9                                                               # (equal strands share equally, as before)


# ------------------------------------------------------------------ the lean Trident (1.69.197): the least overhead, less to pay for on the Iran servers' line
def test_every_kind_has_a_wire_overhead_and_kcp_with_its_error_correction_is_the_heaviest():
    """The owner, 2026-11-05: "in the tunnel we have, the least overhead; save on the traffic cost of the Iran servers"."""
    o = trident.wire_overhead
    from nikatel.services import overhead

    assert o("bp:tcpmux") == pytest.approx(0.04) and o("bp:wssmux") == pytest.approx(0.08) and o("chisel:tls") == pytest.approx(0.13)         # a TCP stream: its outer headers, mux / WebSocket / TLS framing on top
    assert o("tcpmux") == pytest.approx(0.06) and o("fou:tcpmux") == pytest.approx(0.06)                                                       # Backhaul inside GRE / a FOU link: the stream plus the link's bytes
    assert o("bpd:udp") == pytest.approx(0.07) and o("bpd:pck") == pytest.approx(0.06)                                                         # raw packets and a UDP carrier with its acknowledgements
    assert o("bp:kcp") == pytest.approx(0.60) and o("bp:xdi") > o("bp:kcp") > o("paqet") > o("bp:quic") > o("bp:tcpmux")                     # KCP with forward error correction (3 parity packets for every 10, the resends) - the Doctor's model (services/overhead.py, 1.69.223)
    assert all(o(k[0]) == pytest.approx(overhead.model_cost(k[0]) - 1.0) and 0.0 <= o(k[0]) < 1.0 for k in tunnel_test.KINDS) and len(tunnel_test.KINDS) == 80          # one table for the Trident and the Doctor; every kind answers


def test_a_heavier_kind_gets_less_of_the_share_and_lean_holds_the_best_strand_at_94_and_the_rest_at_3():
    w = trident.target_weights([item(), item(overhead=0.30)])
    assert w[0] == pytest.approx(1.3 / 2.3) and w[0] > w[1]                                                                                 # equal strands, one puts 30 % more on the line: it carries 23 % less
    w = trident.target_weights([item(cap=1000), item(cap=10), item(cap=10)], lean=True)
    assert w[0] == pytest.approx(trident.CEIL_LEAN) and w[1] == pytest.approx(trident.FLOOR_LEAN) and w[2] == pytest.approx(trident.FLOOR_LEAN) and sum(w) == pytest.approx(1.0)
    w = trident.target_weights([item(cap=1000), item(cap=1)], lean=True)
    assert w[0] == pytest.approx(trident.CEIL_LEAN) and w[1] == pytest.approx(1 - trident.CEIL_LEAN)                                        # (two usable: the same ceiling while lean)
    assert trident.target_weights([item(), item(), item()], lean=True) == pytest.approx([1 / 3] * 3)                                         # equal strands share equally, lean or not
    assert trident.target_weights([item(cap=100, stage=3), item(cap=100, stage=0), item(cap=100, stage=1)], lean=True) == pytest.approx([0.65, 0.10, 0.25])      # the way in by steps is the same
    assert trident.target_weights([item(usable=False), item(), item(usable=False)], lean=True) == [0.0, 1.0, 0.0]


def test_the_loss_the_weights_see_is_the_least_of_the_last_three_checks():
    from collections import deque

    h = deque(maxlen=trident.LOSS_LOOKS)
    assert trident.smooth_loss(h, 100, 0.0) == 0.0 and trident.smooth_loss(h, 110, 5.0) == 0.0 and trident.smooth_loss(h, 120, 5.0) == 0.0     # one lost ping, then another: noise
    assert trident.smooth_loss(h, 130, 5.0) == 5.0 and trident.smooth_loss(h, 140, 40.0) == 5.0                                              # three checks in a row with a loss: seen whole
    assert trident.smooth_loss(h, 140, 0.0) == 0.0 and len(h) == 3                                                                             # the same check read again replaces its reading
    assert trident.smooth_loss(h, 150, None) is None and len(h) == 3                                                                           # the ping is blocked: nothing known, the history untouched


def test_lean_is_on_by_default_the_window_says_so_and_the_switch_puts_the_plans_again(monkeypatch):
    assert trident.lean_on() is True
    _w, _sid, xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    trident._step("de-ir1")
    s = trident.status("de-ir1")
    assert s["lean"] is True and s["strands"][0]["overhead"] == 6 and all(0 <= r["overhead"] < 100 for r in s["strands"])                  # the tunnel (Backhaul over GRE) costs about 6 % on the line
    a = applied("de-ir1")
    st.put("trident", {"applied": {"de-ir1": {**a, "at": clock() - 3600, "stages": [3, 3, 3], "stage_at": [clock() - 9999] * 3}}})
    calls.clear()
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert len(plans(calls)) == 1 and max(a["w"]) <= trident.CEIL_LEAN + 1e-9 and min(a["w"]) >= trident.FLOOR_LEAN - 1e-9 and sum(a["w"]) == pytest.approx(1.0, abs=1e-3)          # (the applied shares are rounded to 4 places)

    assert trident._cap_of({"idx": 2, "kind": "x"}, {"caps": {"2": {"v": 50.0, "at": clock() - 7 * 3600, "kind": "x"}}}, clock()) == 50.0          # a capacity of 7 hours is still used while lean ...
    monkeypatch.setattr(trident, "wake", lambda: None)
    assert trident.set_lean(False) == {"lean": False} and trident.lean_on() is False and "de-ir1" in trident._FORCE
    assert trident._cap_of({"idx": 2, "kind": "x"}, {"caps": {"2": {"v": 50.0, "at": clock() - 7 * 3600, "kind": "x"}}}, clock()) is None          # ... not when lean is off (6 hours)
    calls.clear()
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert len(plans(calls)) == 1 and max(a["w"]) <= trident.CEIL_MANY + 1e-9 and min(a["w"]) >= trident.FLOOR - 1e-9 and trident.status("de-ir1")["lean"] is False      # the shares of before
    assert trident.set_lean(True) == {"lean": True}

    with session() as db:                                                                                                                      # one check with 5 % loss (one lost ping of twenty) does not move the strand's loss ...
        db.get(Tunnel, xid).detail = {"loss_pct": 5.0}
        db.get(Tunnel, xid).last_check_at = clock() + 10

    assert trident._items("de-ir1", trident._read("de-ir1"), applied("de-ir1"), clock())[2]["loss"] == 0.0

    for n in (20, 30):                                                                                                                         # ... three in a row do
        with session() as db:
            db.get(Tunnel, xid).last_check_at = clock() + n

        trident._items("de-ir1", trident._read("de-ir1"), applied("de-ir1"), clock())

    assert trident._items("de-ir1", trident._read("de-ir1"), applied("de-ir1"), clock())[2]["loss"] == 5.0


def test_the_lean_switch_has_a_route(client, monkeypatch):
    monkeypatch.setattr(trident, "wake", lambda: None)
    assert client.get("/api/trident/lean").json() == {"ok": True, "lean": True}
    assert client.put("/api/trident/lean", json={"on": False}).json() == {"ok": True, "lean": False} and trident.lean_on() is False
    assert client.put("/api/trident/lean", json={"on": True}).json()["lean"] is True and trident.lean_on() is True


def test_a_tie_between_kinds_goes_to_the_encrypted_one_then_to_the_lighter_one():
    kinds = ["ipip:tcpmux", "wg:tcpmux"]                                                               # the same fate as "tcpmux" for the filter (another family, the same dialling side, the same packets), nobody tested either
    k, why = trident.pick_kind(kinds, ["tcpmux"], None, NOW)
    assert k == "wg:tcpmux" and "not tested" in why                                                    # (listed second: it is encrypted)
    k, _ = trident.pick_kind(["fou:tcpmux", "gue:tcpmux"], ["tcpmux"], None, NOW)
    assert k == "fou:tcpmux"                                                                           # (both plain UDP links: 28 bytes against 36)


# ------------------------------------------------------------------ the relay strand (the owner, 2026-11-03: "the Iran server's address is reachable but the foreign address is filtered - it must still answer")
def join_threads(prefix):
    import threading

    for t in threading.enumerate():
        if t.name.startswith(prefix):
            t.join(3)


def add_foreign2(w, up=True, name="fr2"):
    """Another foreign server - with (or without) a tunnel from the same Iran server that is up."""
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        f2 = Server(name=f"{name}-{w['name']}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add(f2)
        db.flush()

        if up:
            t = Tunnel(name=f"o-{w['name']}"[:15], type="backhaul", transport="tcpmux", port=9400 + n, iran_server_id=w["iran"], foreign_server_id=f2.id, state="up", state_since=clock() - 3000,
                       last_check_at=clock() - 2, forwarded_ports=[8800 + n], record={"port_mapping": str(8800 + n)}, rtt_ms=55.0, detail={"loss_pct": 0.0})
            db.add(t)
            db.flush()

        return f2.id


def add_relay(w, to, state="up", since=300, rtt=90.0):
    """The relay strand of w's tunnel: a hidden tunnel from its Iran server to `to` on the relay ports, its record naming the tunnel's foreign server and the ports the unit forwards."""
    name = w["name"][:11] + "-rl"
    lp = w["port"] + trident.RELAY_SHIFT

    with session() as db:
        host = db.get(Server, w["foreign"]).host
        t = Tunnel(name=name, type="chisel", transport="wss", enabled=False, port=9652, iran_server_id=w["iran"], foreign_server_id=to, state=state, state_since=clock() - since, last_check_at=clock() - 2,
                   forwarded_ports=[lp], rtt_ms=rtt, detail={"loss_pct": 0.0},
                   record={"strand_of": w["name"], "port_mapping": f"{lp}:{lp}", "kind": "chisel:tls", "built_by": "nikatel-panel", "relay": {"to": w["foreign"], "to_host": host, "ports": [[lp, w["port"]]]}})
        db.add(t)
        db.flush()
        return t.id


def test_the_relay_ports_are_shifted_at_both_ends_and_the_spec_names_the_tunnels_foreign_server():
    text, triples = trident.relay_mapping("443=2096, 80", set())
    assert text == "40443:42096,40080:40080" and triples == [(443, 40443, 2096), (80, 40080, 80)]                           # the users' port +40000 on the Iran server; the tunnel's target port +40000 on the second server
    assert trident.relay_spec(triples, "198.51.100.7") == "42096:198.51.100.7:2096,40080:198.51.100.7:80"

    with pytest.raises(ValueError, match="used"):
        trident.relay_mapping("443", {40443})

    with pytest.raises(ValueError):
        trident.relay_mapping("30000", set())                                                                                # 70000 is no port, 30000 - 40000 neither

    assert trident.relay_name("de-ir1", set()) == "de-ir1-rl" and trident.relay_name("de-ir1", {"de-ir1-rl"}) == "de-ir1-rl2"
    assert len(trident.relay_name("a-very-long-tunnel-name", {"a-very-long-rl"})) <= 15 and trident.ROLES == ("tunnel", "standby", "strand", "relay")


def test_a_relay_is_due_when_the_address_is_cut_or_nothing_carried_for_a_minute_and_it_stays_half_an_hour_after_the_own_strands_are_back():
    now = clock()
    cut = {"tcp": {"state": "blocked"}, "icmp": {"state": "blocked"}, "udp": {"state": "blocked"}}
    assert "filtered" in trident.relay_due([True, True, False], cut, 0, now, False)                                           # the weather says the address is cut: even while a raw strand still carries
    assert trident.relay_due([True, True, False], cut, 0, now, True) == ""                                                   # one relay at a time
    assert trident.relay_due([False, False, False], {}, now - trident.RELAY_AFTER, now, False).startswith("no strand has carried")
    assert trident.relay_due([False, False, False], {}, now - 10, now, False) == "" and trident.relay_due([False, False, False], {}, 0, now, False) == ""
    assert trident.relay_due([True, False, False], {}, now - 9999, now, False) == ""
    assert trident.relay_keep([False, False, False], 0, now) is True
    assert trident.relay_keep([True, False, False], now - trident.RELAY_KEEP + 10, now) is True
    assert trident.relay_keep([True, False, False], now - trident.RELAY_KEEP, now) is False
    assert trident.relay_keep([True, False, False], now - trident.RELAY_KEEP, now, cut) is True                              # ... unless the weather still says the address is cut


def test_the_relay_actions_put_one_unit_on_the_second_foreign_server_that_listens_on_localhost_only():
    on = ssh.render_action("relay_on", {"NAME": "de-ir1", "SPEC": "42096:198.51.100.7:2096,40080:198.51.100.7:80"})
    assert "n='de-ir1'" in on and 'u="nikatel-relay-$n"' in on and '"127.0.0.1"' in on and "asyncio.start_server" in on and "/dev/tcp/$h/$p" in on and "systemctl enable" in on and 'echo "RELAY_ON' in on
    assert "iptables" not in on and "reboot" not in on and "0.0.0.0" not in on and "apt-get" not in on and "pip install" not in on and "curl" not in on      # nothing installed, nothing in the firewall, nothing public
    off = ssh.render_action("relay_off", {"NAME": "de-ir1"})
    assert "disable --now" in off and 'u="nikatel-relay-$n"' in off and "n='de-ir1'" in off and "rm -f /etc/systemd/system/$u.service" in off and "iptables" not in off

    for bad in ({"NAME": "de-ir1", "SPEC": "42096:example.com:2096"}, {"NAME": "de-ir1", "SPEC": "42096:198.51.100.7:2096; reboot"}, {"NAME": "x; reboot", "SPEC": "42096:198.51.100.7:2096"},
                {"NAME": "de-ir1", "SPEC": ""}, {"NAME": "de-ir1", "SPEC": "42096:198.51.100.7"}, {"NAME": "de-ir1", "SPEC": ",".join(["42096:198.51.100.7:2096"] * 201)}):
        with pytest.raises(ValueError):
            ssh.render_action("relay_on", bad)

    with pytest.raises(ValueError):
        ssh.render_action("relay_off", {"NAME": "Upper"})


def test_the_relay_goes_through_the_foreign_server_this_iran_server_reaches_best(monkeypatch):
    w = world("de-ir1")
    assert trident.relay_target(w["iran"], w["foreign"])[0] == 0                                                            # no other foreign server
    f2 = add_foreign2(w, up=False)
    to, why = trident.relay_target(w["iran"], w["foreign"])
    assert to == 0 and "no other foreign server is known to be reachable" in why                                             # nothing says the Iran server reaches it
    f3 = add_foreign2(w, up=True, name="fr3")
    to, why = trident.relay_target(w["iran"], w["foreign"])
    assert to == f3 and "1 tunnel(s) up" in why and "55 ms" in why                                                           # an up tunnel from this Iran server is the surest sign

    monkeypatch.setattr(trident.weather, "weather_of", lambda i, f: {"tcp": {"state": "open"}} if f == f2 else {})
    monkeypatch.setattr(trident.weather, "open_families", lambda i, f: {"tcp"} if f == f2 else set())
    assert trident.relay_target(w["iran"], w["foreign"])[0] == f3                                                            # (still: a tunnel that is up beats a probe)

    with session() as db:
        db.get(Server, f3).online = False

    to, why = trident.relay_target(w["iran"], w["foreign"])
    assert to == f2 and "open families: tcp" in why                                                                          # the server that is off is never chosen; open weather is enough
    assert trident.relay_target(w["iran"], f2)[0] == w["foreign"]                                                            # never the tunnel's own foreign server


def test_the_relay_strand_is_planned_on_the_relay_ports_and_built_hidden_with_its_relay_record(monkeypatch):
    writable(monkeypatch)
    w = world("de-ir1")

    with pytest.raises(ValueError, match="no other foreign server"):
        trident.relay_plan("de-ir1")

    f2 = add_foreign2(w)
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: (["bp:wssmux", "tcpmux"], []))
    plan = trident.relay_plan("de-ir1")
    assert plan["name"] == "de-ir1-rl" and plan["via"] == f2 and plan["to"] == w["foreign"] and plan["ports"] == f"{w['port'] + 40000}:{w['port'] + 40000}" and plan["triples"] == [(w["port"], w["port"] + 40000, w["port"])]

    with session() as db:
        assert plan["to_host"] == db.get(Server, w["foreign"]).host                                                      # the unit carries on to the TUNNEL's foreign server

    assert plan["kind"] in ("bp:wssmux", "tcpmux") and plan["note"].startswith("through fr2-de-ir1")

    started = []
    monkeypatch.setattr(trident.build_other, "start_build", lambda *a, **k: started.append((a, k)) or 91)
    monkeypatch.setattr(trident.builder, "start_build", lambda *a, **k: started.append((a, k)) or 92)
    aid = trident.build_relay("de-ir1")
    a, k = started[0]
    assert aid in (91, 92) and a[0] == w["iran"] and a[1] == f2 and k["standby_of"] == "de-ir1" and k["hidden_key"] == "strand_of" and k["forced_name"] == "de-ir1-rl"
    assert k["extra_record"]["relay"]["to"] == w["foreign"] and k["extra_record"]["relay"]["ports"] == [[w["port"] + 40000, w["port"]]] and trident._BUILDS["de-ir1#relay"]["aid"] == aid

    with session() as db:
        assert any("relay strand" in e.text for e in db.scalars(select(Event)))                                             # one sentence in the events: the address looks filtered, a relay is being built

    with pytest.raises(ValueError, match="being built"):
        trident.build_relay("de-ir1")

    assert trident._state_of("de-ir1", True, None, clock())[0] == "building" and "relay strand" in trident._state_of("de-ir1", True, None, clock())[1]
    trident._BUILDS.clear()
    rid = add_relay(w, f2)

    with pytest.raises(ValueError, match="already has"):
        trident.build_relay("de-ir1")

    with session() as db:
        db.delete(db.get(Tunnel, rid))
        db.get(Server, w["foreign"]).online = False

    with pytest.raises(ValueError, match="foreign server itself is off"):
        trident.build_relay("de-ir1")

    with session() as db:
        db.get(Server, w["foreign"]).online = True

    monkeypatch.setattr(get_settings(), "read_only", True)

    with pytest.raises(ValueError, match="read-only"):
        trident.build_relay("de-ir1")


def test_when_nothing_carries_for_a_minute_or_the_weather_says_the_address_is_cut_a_relay_strand_is_built(monkeypatch):
    w, sid, xid = up_world(monkeypatch)
    fake_actions(monkeypatch)
    add_foreign2(w)
    built = []
    monkeypatch.setattr(trident, "build_relay", lambda n: built.append(n) or 1)
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert built == [] and a["all_down_since"] == 0 and a["own_up_since"] > 0

    for tid in (w["id"], sid, xid):
        set_state(tid, "down", since=3)

    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["state"] == "idle" and a["all_down_since"] > 0 and a["own_up_since"] == 0 and built == []                       # the first minute is the Doctor's: a restart may bring the tunnel back
    st.put("trident", {"applied": {"de-ir1": {**a, "all_down_since": clock() - trident.RELAY_AFTER - 1}}})
    trident._step("de-ir1")
    assert built == ["de-ir1"]

    built.clear()
    monkeypatch.setattr(trident, "build_relay", lambda n: (_ for _ in ()).throw(ValueError("no other foreign server is on")))
    trident._step("de-ir1")
    assert "cannot be built" in trident.status("de-ir1")["why"] and "no other foreign server" in trident.status("de-ir1")["why"]       # the reason is in the window, tried again in half a minute
    trident._RETRY_AT.clear()

    monkeypatch.setattr(trident, "build_relay", lambda n: built.append(n) or 1)

    for tid in (w["id"], sid, xid):
        set_state(tid, "up", since=300)

    monkeypatch.setattr(trident.weather, "weather_of", lambda i, f: {"tcp": {"state": "blocked"}, "icmp": {"state": "blocked"}, "udp": {"state": "blocked"}} if f == w["foreign"] else {})
    trident._step("de-ir1")
    assert built == ["de-ir1"] and applied("de-ir1")["own_up_since"] > 0                                                     # the address is cut for every ordinary protocol: the road is built while the raw strands still carry


def test_a_relay_strand_that_is_up_gets_its_unit_on_the_second_server_then_carries_and_goes_half_an_hour_after_the_own_strands_are_back(monkeypatch):
    w, sid, xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    f2 = add_foreign2(w)
    trident._step("de-ir1")                                                                                                 # (a plan stands: the tunnel and its two strands)
    rid = add_relay(w, f2)

    with session() as db:
        host = db.get(Server, w["foreign"]).host

    for tid in (w["id"], sid, xid):
        set_state(tid, "down", since=3)

    calls.clear()
    trident._step("de-ir1")
    a = applied("de-ir1")
    ons = [c for c in calls if c[0] == "relay_on"]
    assert len(ons) == 1 and ons[0][1] == {"NAME": "de-ir1", "SPEC": f"{w['port'] + 40000}:{host}:{w['port']}"} and a["relay_ok"] is True and a["relay_seen"] > 0 and a["relay_reach"] == "1/1"
    assert a["usable"] == [False, False, False, False] and a["state"] == "idle"                                              # the unit was put this turn: the relay carries from the next

    calls.clear()
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["usable"] == [False, False, False, True] and a["w"] == pytest.approx([0.0, 0.0, 0.0, 1.0]) and plans(calls) == [f"tcp:{w['port']}:{w['port'] + 40000}:1"] and a["state"] == "active"
    s = trident.status("de-ir1")
    assert [r["role"] for r in s["strands"]] == ["tunnel", "standby", "strand", "relay"] and s["strands"][3]["weight"] == 100 and s["strands"][3]["usable"] and s["strands"][3]["kind"] == "chisel:tls"
    assert trident.summaries(["de-ir1"])["de-ir1"]["weights"] == [0, 0, 0, 100]

    calls.clear()
    trident._step("de-ir1")
    assert [c for c in calls if c[0] == "relay_on"] == [] and plans(calls) == []                                              # the unit is put again only every RELAY_EVERY; nothing is rewritten for nothing

    for tid in (w["id"], sid, xid):
        set_state(tid, "up", since=300)

    calls.clear()
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["usable"] == [True, True, True, True] and 0 < a["w"][3] < 1 and a["own_up_since"] > 0 and len(plans(calls)) == 1 and plans(calls)[0].count("tcp:") == 3      # the own strands are back: they are let in by steps beside the relay

    removed = []
    monkeypatch.setattr(trident, "remove_relay", lambda n: removed.append(n) or [])
    st.put("trident", {"applied": {"de-ir1": {**a, "own_up_since": clock() - trident.RELAY_KEEP - 1, "relay_seen": clock() - trident.RELAY_KEEP - 1}}})
    calls.clear()
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["relay_retire"] > 0 and a["relay_ok"] is False and removed == []                                                # half an hour later: its share goes first ...
    st.put("trident", {"applied": {"de-ir1": {**a, "relay_retire": clock() - trident.TICK - 1}}})
    trident._step("de-ir1")
    join_threads("trident-remove-relay")
    a = applied("de-ir1")
    assert removed == ["de-ir1"] and a["usable"][3] is False and f":{w['port'] + 40000}:" not in a["plan"]                   # ... then the strand

    # a relay strand whose ports no longer are the tunnel's is removed like any strand
    removed.clear()
    trident._REMOVING.clear()
    st.put("trident", {"applied": {"de-ir1": {**a, "relay_retire": 0, "relay_ok": True, "relay_at": clock()}}})

    with session() as db:
        t = db.get(Tunnel, rid)
        t.record = {**t.record, "port_mapping": f"41999:{w['port'] + 40000}"}

    trident._step("de-ir1")
    join_threads("trident-remove-relay")
    assert removed == ["de-ir1"]


def test_a_relay_whose_targets_do_not_answer_from_the_second_server_gets_no_share_and_says_why(monkeypatch):
    w, sid, xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch, relay_reach=0)
    f2 = add_foreign2(w)
    trident._step("de-ir1")
    add_relay(w, f2)
    calls.clear()

    for tid in (w["id"], sid, xid):
        set_state(tid, "down", since=3)

    trident._step("de-ir1")
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["relay_ok"] is False and "does not answer" in a["relay_why"] and a["usable"] == [False, False, False, False] and a["state"] == "idle"
    assert "does not answer" in trident.status("de-ir1")["why"] and len([c for c in calls if c[0] == "relay_on"]) == 1       # said in the window; tried again after RELAY_RETRY, not every turn


def test_removing_the_relay_takes_the_unit_off_the_second_server_then_the_tunnel(monkeypatch):
    w = world("de-ir1")
    f2 = add_foreign2(w)
    rid = add_relay(w, f2)
    calls = fake_actions(monkeypatch)
    deleted = []
    monkeypatch.setattr(trident.builder, "delete_tunnel", lambda tid, **k: deleted.append(tid) or ["x"])
    assert trident.remove_relay("nobody") == []
    assert trident.remove_relay("de-ir1") == ["x"] and deleted == [rid] and [c for c in calls if c[0] == "relay_off"] == [("relay_off", {"NAME": "de-ir1"})]
    assert trident.strand_exists("de-ir1") is True                                                                           # (the row is still there: the delete is a stand-in)


def test_forgetting_and_renaming_take_the_relay_strand_with_them(monkeypatch):
    w, _sid, _xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    f2 = add_foreign2(w)
    rid = add_relay(w, f2)
    trident._step("de-ir1")
    trident._BUILDS["de-ir1#relay"] = {"aid": 5, "name": "de-ir1-rl", "kind": "chisel:tls", "at": clock()}
    trident._set_failed("de-ir1#relay", {"n": 1, "at": clock(), "why": "x", "kinds": {}})
    trident.renamed("de-ir1", "de-ir9")
    join_threads("trident-retire")
    join_threads("trident-relay-retire")
    assert [c for c in calls if c[0] == "relay_off" and c[1]["NAME"] == "de-ir1"]                                            # the unit carried the old name: it goes, the job puts it again under the new one
    assert applied("de-ir9")["relay_ok"] is False and "de-ir9#relay" in trident._BUILDS and "de-ir1#relay" not in trident._BUILDS and trident._failed("de-ir9#relay")["n"] == 1

    with session() as db:
        assert (db.get(Tunnel, rid).record or {}).get("strand_of") == "de-ir9"

    removed = []
    monkeypatch.setattr(trident, "remove_strand", lambda n: removed.append(("strand", n)) or [])
    monkeypatch.setattr(trident, "remove_relay", lambda n: removed.append(("relay", n)) or [])
    trident.forget("de-ir9")
    join_threads("trident-forget")
    assert ("relay", "de-ir9") in removed and ("strand", "de-ir9") in removed and 5 in trident.builder.CANCEL and trident._failed("de-ir9#relay") == {}
    trident.builder.CANCEL.discard(5)


# ------------------------------------------------------------------ the stall look (1.69.226; the owner, 2026-11-05: "check whether the tunnels we build with this method have پرش [stalls]")
def test_the_strand_probe_script_reads_only_asks_each_port_on_localhost_and_takes_only_ports_and_a_count():
    """A connection that hangs for 3 - 7 s is not seen by the 10 s check of a strand (its delay and loss are pings): on the live UK Trident (2026-11-04) the KCP strand stalled 0.7 % of 450 connections, the TCP
    strand none, and both kept a share. The look is a handful of small requests made ON the Iran server to each strand's own port - they never pass the Trident's NAT chain (PREROUTING only)."""
    assert "strand_probe" in ssh.SCRIPTS and "strand_probe" not in ssh.ACTIONS
    s = ssh._script_text("strand_probe", {"PORTS": "8075 28075 38075 48075", "N": "8"})
    assert "for p in 8075 28075 38075 48075; do" in s and "n='8'" in s and '"http://127.0.0.1:$p/"' in s and "-m 6" in s and "-o /dev/null" in s and "sleep 0.1" in s          # each port, on localhost, small, sequential, 6 s at most
    assert "STRAND %s n=%d ok=%d stalls=%d p95=%.3f max=%.3f" in s and "> 1.5" in s and ") < /dev/null & done; wait" in s                                                       # one line per port; the ports at the same time
    assert "systemctl" not in s and "iptables" not in s and " -j " not in s and "rm " not in s and "apt" not in s and ">>" not in s and " > /" not in s                       # nothing installed, nothing changed, nothing written

    for bad in ({"PORTS": "8075; reboot", "N": "8"}, {"PORTS": "8075", "N": "0"}, {"PORTS": "8075", "N": "100"}, {"PORTS": "8075", "N": "8x"}, {"PORTS": " ".join(str(8000 + i) for i in range(9)), "N": "8"},
                {"PORTS": "", "N": "8"}, {"PORTS": "8075", "N": "8", "HOST": "203.0.113.9"}):
        with pytest.raises(ValueError):
            ssh._script_text("strand_probe", bad)


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("curl") is None, reason="needs bash and curl")
def test_the_strand_probe_script_runs_here_against_a_quick_a_stalling_and_a_dead_listener():
    """The script for real (bash + curl on this machine), in front of three ports: one that answers at once, one that answers after two seconds, one where nothing listens."""
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        wait = 0.0

        def do_GET(self):
            time.sleep(self.wait)
            body = b"no\n"
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class Slow(H):
        wait = 2.0

    quick, slow = ThreadingHTTPServer(("127.0.0.1", 0), H), ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()                                                                                   # (a port that was free a moment ago: nothing listens there)
    threads = [threading.Thread(target=srv.serve_forever, daemon=True) for srv in (quick, slow)]

    for t in threads:
        t.start()

    try:
        ports = [quick.server_address[1], slow.server_address[1], dead_port]
        t0 = time.time()
        out = subprocess.run(["bash", "-c", ssh._script_text("strand_probe", {"PORTS": " ".join(str(p) for p in ports), "N": "4"})], capture_output=True, text=True, timeout=60, check=False).stdout
        took = time.time() - t0
    finally:
        quick.shutdown()
        slow.shutdown()

    got = trident.parse_probe(out)
    assert set(got) == set(ports), out
    q, s, d = got[ports[0]], got[ports[1]], got[ports[2]]
    assert q["n"] == 4 and q["ok"] == 4 and q["stalls"] == 0 and q["p95"] < 1.5 and q["max"] < 1.5                       # answered at once
    assert s["n"] == 4 and s["ok"] == 4 and s["stalls"] == 4 and 2.0 <= s["p95"] <= 6.0 and s["max"] >= s["p95"]           # answered, but every answer took over 1.5 s: four stalls
    assert d["n"] == 4 and d["ok"] == 0 and d["stalls"] == 0 and d["max"] < 1.5                                            # refused at once: neither an answer nor a stall (the strand's own check says it is down)
    assert took < 4 * 2.2 + 6, took                                                                                        # the ports are asked at the same time: the slow one sets the pace, not the sum
    assert all(float(line.rsplit("kb=", 1)[1]) < 1.0 for line in out.splitlines() if line.startswith("STRAND "))          # a few bytes per answer


def test_the_stall_factor_is_free_under_one_percent_and_falls_to_the_floor_at_ten():
    f = trident.stall_factor
    assert f(None, None) == 1.0 and f(0.0, 0.3) == 1.0 and f(0.9, 0.99) == 1.0 and f(1.0, 1.0) == 1.0                   # nothing known, or under 1 % and under 1 s: free
    assert f(2.0, 0.5) == pytest.approx(0.5) and f(5.0, 0.5) == pytest.approx(0.2) and f(10.0, 0.5) == 0.0 and f(40.0, 0.5) == 0.0          # 2 % half, 5 % a fifth, 10 % and more the floor only
    assert 0.5 < f(1.5, 0.5) < 1.0 and 0.2 < f(3.0, 0.5) < 0.5 and 0.0 < f(7.5, 0.5) < 0.2                               # straight lines between
    assert f(0.0, 3.0) == pytest.approx(0.2) and f(0.0, 5.0) == pytest.approx(0.2) and 0.2 < f(0.0, 2.0) < 1.0 and f(0.0, None) == 1.0          # slow at p95 counts too: a fifth from 3 s
    assert f(2.0, 3.5) == pytest.approx(0.2) and f(5.0, 2.0) == pytest.approx(0.2)                                        # the lower of the two
    vals = [f(x / 10, 0.5) for x in range(0, 120, 3)]
    assert vals == sorted(vals, reverse=True) and all(0.0 <= v <= 1.0 for v in vals)                                      # never up with more stalls, always 0 - 1


def test_parse_probe_reads_the_script_lines_and_stall_of_pools_the_last_looks():
    out = "STRAND 8075 n=8 ok=8 stalls=1 p95=1.800 max=4.100 kb=1.2\nSTRAND 28075 n=8 ok=8 stalls=0 p95=0.210 max=0.300 kb=1.1\nSTRAND 38075 n=8 ok=0 stalls=0 p95=0.001 max=0.001 kb=0.0\ngarbage\nSTRAND x n=1\n"
    got = trident.parse_probe(out)
    assert got == {8075: {"n": 8, "ok": 8, "stalls": 1, "p95": 1.8, "max": 4.1}, 28075: {"n": 8, "ok": 8, "stalls": 0, "p95": 0.21, "max": 0.3}, 38075: {"n": 8, "ok": 0, "stalls": 0, "p95": 0.001, "max": 0.001}}
    assert trident.parse_probe("") == {} and trident.parse_probe("TRIDENT_SET x 2\n") == {}

    from collections import deque

    h = deque(maxlen=trident.STALL_LOOKS)
    assert trident.stall_of(h) == (None, None, 0)
    h.append(got[38075])
    assert trident.stall_of(h) == (None, None, 0)                                                                         # a port that refused: nothing known
    h.append(got[28075])
    assert trident.stall_of(h) == (0.0, 0.21, 8)
    h.append(got[8075])
    assert trident.stall_of(h) == (pytest.approx(6.2), 1.8, 16)                                                           # 1 of 16 = 6.2 %; the p95 the middle look's (two looks: the slower)

    for _ in range(6):                                                                                                    # six more clean looks: only the last five looks count, the stalled one is gone
        h.append({"n": 8, "ok": 8, "stalls": 0, "p95": 0.3, "max": 0.5})

    assert len(h) == trident.STALL_LOOKS and trident.stall_of(h) == (0.0, 0.3, 40)
    h.append({"n": 8, "ok": 6, "stalls": 2, "p95": 6.0, "max": 6.0})                                                      # two timeouts of eight (they count as stalls, not as answers)
    assert trident.stall_of(h) == (5.0, 0.3, 40)                                                                           # 2 of 40; the p95 is the median of the looks: one bad look does not make the strand slow


def stall_probe(monkeypatch, answers):
    """A stand-in for ssh.run_script: `answers` = {port: "n=8 ok=8 stalls=1 p95=1.800 max=4.100"}; records every call."""
    calls = []

    def run_script(node, name, params=None, timeout=40):
        calls.append((name, dict(params or {}), timeout))
        assert name == "strand_probe"
        ssh._script_text(name, params)                                                                                      # (the real guards on the parameters the look sends)
        return "".join(f"STRAND {p} {answers[int(p)]} kb=1.0\n" for p in params["PORTS"].split() if int(p) in answers)

    monkeypatch.setattr(trident, "run_script", run_script)
    return calls


def test_the_look_asks_the_iran_server_for_every_strand_that_is_up_and_keeps_the_answers_by_row(monkeypatch):
    w, sid, xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    P = w["port"]
    probes = stall_probe(monkeypatch, {P: "n=8 ok=8 stalls=2 p95=1.800 max=4.100", P + 20000: "n=8 ok=8 stalls=0 p95=0.210 max=0.300", P + 30000: "n=8 ok=0 stalls=0 p95=0.001 max=0.001"})
    looked = []
    monkeypatch.setattr(trident, "_stall_async", lambda name, p, items: looked.append([it["idx"] for it in items]) or trident._stall_look(name, None, [(it["id"], it["born"], it["ports"][0]) for it in items]))
    trident._step("de-ir1")
    assert plans(calls) and looked == [[0, 1, 2]] and len(probes) == 1 and probes[0][1] == {"PORTS": f"{P} {P + 20000} {P + 30000}", "N": str(trident.STALL_N)} and probes[0][2] >= trident.STALL_N * 6           # every strand that is up, its first port, in one ssh with time for N slow answers
    assert trident._STALL[w["id"]][1][-1]["stalls"] == 2 and trident._STALL[sid][1][-1]["stalls"] == 0 and trident._STALL[xid][1][-1]["ok"] == 0
    s = trident.status("de-ir1")["strands"]
    assert s[0]["stalls"] == 25.0 and s[0]["stall_p95"] == 1.8 and s[1]["stalls"] == 0.0 and s[2]["stalls"] is None                                                          # the window: percent per strand; the refused port says nothing yet

    looked.clear()
    trident._step("de-ir1")
    assert looked == []                                                                                                   # not again within STALL_EVERY
    trident._STALL_AT["de-ir1"] = 0.0
    set_state(xid, "down", since=3)
    trident._step("de-ir1")
    assert looked == [[0, 1]]                                                                                             # a strand that is down is not asked (its port refuses: nothing to learn)

    trident._STALL_AT["de-ir1"] = 0.0
    set_state(xid, "up", since=300)
    set_state(w["id"], "down", since=3)
    set_state(sid, "down", since=3)
    looked.clear()
    trident._step("de-ir1")
    assert looked == []                                                                                                   # one usable strand carries everything whatever it does: no look

    trident._STALL_AT["de-ir1"] = 0.0
    set_state(w["id"], "up", since=300)
    set_state(sid, "up", since=300)
    monkeypatch.setattr(autopilot, "storm_now", lambda: True)
    trident._step("de-ir1")
    assert looked == []                                                                                                   # not in a storm

    looks = trident._STALL[w["id"]][1]
    it = trident._items("de-ir1", trident._read("de-ir1"), applied("de-ir1"), clock())[0]
    assert len(looks) == 2 and it["stall"] == 0.0 and it["stall_pct"] == 25.0 and it["stall_p95"] == 1.8                   # the tunnel was looked at twice: 4 of 16 stalled = 25 %, the floor only
    looks.pop()
    it = trident._items("de-ir1", trident._read("de-ir1"), applied("de-ir1"), clock())[0]
    assert it["stall"] == 1.0 and it["stall_pct"] == 25.0                                                                 # one look (8 answers) is not enough to bench a strand: the percent shows, the factor waits
    trident._STALL[w["id"]] = (trident._STALL[w["id"]][0] + 1, looks)                                                     # a rebuilt row (another creation time) starts anew
    it = trident._items("de-ir1", trident._read("de-ir1"), applied("de-ir1"), clock())[0]
    assert it["stall"] == 1.0 and it["stall_pct"] is None and trident.status("de-ir1")["strands"][0]["stalls"] is None


def test_a_strand_that_stalls_loses_its_share_and_a_look_that_fails_changes_nothing(monkeypatch):
    """The live UK Trident (2026-11-04): the KCP tunnel stalled, the TCP standby did not, and the tunnel kept the biggest share. Here the TUNNEL stalls 2 of 8 in every look: after two looks it is held at the floor
    and the standby carries; when the looks come back clean (the last five looks count) the tunnel is let back by its own numbers."""
    w, sid, xid = up_world(monkeypatch)
    calls = fake_actions(monkeypatch)
    P = w["port"]
    answers = {P: "n=8 ok=8 stalls=2 p95=1.800 max=4.100", P + 20000: "n=8 ok=8 stalls=0 p95=0.210 max=0.300", P + 30000: "n=8 ok=8 stalls=0 p95=0.300 max=0.400"}
    stall_probe(monkeypatch, answers)
    monkeypatch.setattr(trident, "_stall_async", lambda name, p, items: trident._stall_look(name, None, [(it["id"], it["born"], it["ports"][0]) for it in items]))

    def settled():
        a = applied("de-ir1")
        st.put("trident", {"applied": {"de-ir1": {**a, "at": clock() - 3600, "stages": [3, 3, 3], "stage_at": [clock() - 9999] * 3}}})
        trident._STALL_AT["de-ir1"] = 0.0
        calls.clear()

    trident._step("de-ir1")                                                                                               # the first plan + the first look (8 answers: nothing is judged yet)
    settled()
    trident._step("de-ir1")                                                                                               # the second look
    a = applied("de-ir1")
    assert a["w"][0] > a["w"][1] > a["w"][2] and a["w"][0] > 0.4                                                           # the tunnel still carries the most (the best delay)
    settled()
    trident._step("de-ir1")                                                                                               # now 16 answers, 4 stalled = 25 %: the tunnel is held at the floor, the standby takes the share
    a = applied("de-ir1")
    assert len(plans(calls)) == 1 and a["w"][0] == pytest.approx(trident.FLOOR_LEAN) and a["w"][1] > a["w"][2] > 0 and sum(a["w"]) == pytest.approx(1.0, abs=1e-3)
    s = trident.status("de-ir1")["strands"]
    assert s[0]["stalls"] == 25.0 and s[1]["stalls"] == 0.0 and s[2]["stalls"] == 0.0 and s[0]["weight"] == 3

    answers[P] = "n=8 ok=8 stalls=0 p95=0.150 max=0.200"                                                                 # the tunnel answers again (three stalled looks are in memory: 6 of 24)
    for _ in range(4):                                                                                                    # four turns: the plan sees 6/24, 6/32, 6/40, then 4/40 (the first stalled look left the five) - the floor each time
        settled()
        trident._step("de-ir1")

    assert applied("de-ir1")["w"][0] == pytest.approx(trident.FLOOR_LEAN) and trident.status("de-ir1")["strands"][0]["stalls"] == 5.0           # (the window reads the memory after the fourth clean look: 2 of 40)
    settled()
    trident._step("de-ir1")
    assert trident.FLOOR_LEAN < applied("de-ir1")["w"][0] < 0.5 and trident.status("de-ir1")["strands"][0]["stalls"] == 0.0                  # 2 of 40 = 5 %: a fifth of its raw share - back, but small
    settled()
    trident._step("de-ir1")
    a = applied("de-ir1")
    assert a["w"][0] > a["w"][1] > a["w"][2] and a["w"][0] > 0.4                                                                              # clean for five looks: the tunnel carries the most again

    # a look that fails (the server does not answer, or says nothing the panel understands) changes nothing, and the next look is not blocked
    before = [list(x[1]) for x in (trident._STALL[w["id"]], trident._STALL[sid], trident._STALL[xid])]
    p = trident._read("de-ir1")["p"]
    items = [it for it in trident._items("de-ir1", trident._read("de-ir1"), applied("de-ir1"), clock()) if it is not None]

    def lost(node, name, params=None, timeout=40):
        raise ssh.SSHError("ssh connection lost (network)", 255)

    for stand_in in (lost, lambda node, name, params=None, timeout=40: "garbage\n"):
        monkeypatch.setattr(trident, "run_script", stand_in)
        STALL_ASYNC("de-ir1", p, items)                                                                                   # (the real thread)
        join_threads("trident-stall-")
        assert [list(x[1]) for x in (trident._STALL[w["id"]], trident._STALL[sid], trident._STALL[xid])] == before and "de-ir1" not in trident._STALLING

    assert trident.target_weights([item(), item(stall=0.0), item(stall=0.5)]) == pytest.approx([0.95 * 2 / 3, trident.FLOOR, 0.95 / 3])       # the pure weights: a factor of 0 is the floor, 0.5 half the raw share; an item without one keeps its numbers
