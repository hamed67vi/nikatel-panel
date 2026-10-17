# Warm standby for a tunnel - design (not built yet)

The owner, 2026-10-17: "bring the delay down to the minimum". After 1.65.0 the Doctor notices a cut in about 10 s, restarts after 20 s of grace and starts the smart repair within a minute. What is left is the part that
cannot be made shorter by waiting less: **building a tunnel of another type takes 60 - 90 s** (an average of 86 s over the real repairs of two weeks on the test panel). A warm standby removes it: another tunnel of the best
alternative type is built BEFORE the cut, kept idle, and the users' port is handed to it in a few seconds.

This file is the design and the plan to build it. Nothing of it is in the code yet, on purpose: it changes who owns a port on a server that carries users, so it has to be proved on throw-away servers first.

## What it does

- For a tunnel the owner marks (*Tunnel window → Standby*, off by default), the Doctor builds a second tunnel between the same two servers, of the type the knowledge ranks best for this pair (`knowledge.plan_for`, the playbook
  first), under another name (`<name>-sb`) and on **shifted listen ports**: the users' port P becomes `20000 + P` on the Iran server (a mapping `20080=80`, which `PortMap` already supports). It carries no users and nothing but its own checks.
- When the primary is cut and the smart repair has not healed it within `STANDBY_AFTER` seconds (the restart is tried first: it costs 15 - 50 s and often heals), the Doctor **hands the port over**: one reviewed action on the
  Iran server puts a NAT rule that sends the users' port P to `20000 + P` (an `nft` / `iptables` REDIRECT in PREROUTING for the users, and in OUTPUT for the panel's own probes on the server). The handover is one command: about 2 s.
- The smart repair goes on with the primary in the background (the ladder as it is). When the primary is up and stable again (two checks, 60 s), the rule is taken off and the users are back on the primary. Nothing is deleted.
- Every handover and every return is an event, in the Moves tab, with a button *Back now*.

## What has to be built

| Part | Work |
|---|---|
| Standby tunnel | a `Tunnel` row with `record.standby_of = <primary id>`, `enabled = True`, built by `_build_kind` with the shifted mapping; **hidden** like a temporary test tunnel. 21 places already ask `is_temp(name)` (the list, the checks, the survey, the bandwidth, the live numbers, the Doctor's pairs ...): a second predicate `is_standby(t)` joins them, and the places that list or count tunnels without it (the dashboard counts, the alerts, the report) are found by a test that creates a standby and walks every endpoint |
| Its own watch | the sentinel and the minute cycle check a standby like any tunnel (units, GRE card, the HTTP answer on the shifted port); a standby that is not healthy is **rebuilt quietly**, never counted as a cut of the tunnel |
| The handover | two reviewed actions (`standby_on`, `standby_off`) in `ssh.py`: add / remove the NAT rules, idempotent, with a comment tag so that nothing else is touched, listed by a read-only script (`standby_list`); a unit that re-applies them after a reboot while a handover stands |
| The rules of the road | at most one standby per tunnel; none while the panel is read-only; never in a storm; the standby is not built for a pair whose knowledge says every type fails; a handover is refused when the standby's own check is not up; CPU / ports are checked before the build (a second tunnel doubles the processes of the pair) |
| The page | a switch and a state in the tunnel window (*Standby: ready (chisel:tls) / in use since 14:02 / building / off*), the standby row inside the window - not in the list |

## What it costs

A second tunnel on every marked pair: its processes, its control port, its shifted ports, and a build that runs once (and again when the standby is rebuilt). Mark only the tunnels whose users cannot wait a minute.

## How it must be proved before it is shipped

1. Unit tests of the pure parts: the choice of the type and the ports, the NAT rule texts, `is_standby` on every endpoint.
2. A real test on the two throw-away test servers (the e2e panel, a pruned database): build a tunnel and its standby, cut the primary (stop its unit), watch the handover, measure the time from the cut to the first answer
   through the users' port, bring the primary back, watch the return. Repeat with a GRE-filtered route (the primary's GRE dropped) and with a reboot of the Iran server while the handover stands.
3. Only then: ship, with the switch off everywhere.

## What it will and will not do

It makes a cut of the tunnel itself (a service, the GRE link, a filtered route) cost the users a few seconds. It does **not** help when the Iran server or the foreign server is off - that is the failover (Iran server)
or nothing the panel can do (foreign server) - and it does not help when the route between the two servers is cut for every type: the standby would be cut too (the survey of the pair says in advance which types connect).
