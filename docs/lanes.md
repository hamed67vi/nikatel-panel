# Lanes - a tunnel that is nearly full shares its NEW connections with its second tunnel

The owner, 2026-10-20: "when the capacity of a tunnel is nearly full - capacity 76, users use 69 - the Doctor must add capacity without cutting any customer: load balancing between tunnels, or anything else that raises capacity."
Steps 1 - 3 of that wish (the saturation is seen, a better type replaces a slow one, the tunnels move to a lighter Iran server) are the Doctor's other pages. This is step 4: **a second lane beside the tunnel**.

It ships **switched off**: the owner turns it on (*Tunnel Doctor → Add capacity by itself*). The *Share now* button of a tunnel window works by hand once it is on.

## The idea

The tunnel already has a warm standby ([standby.md](standby.md)): a real tunnel of another type between the same two servers on shifted ports (`P` → `20000 + P`), looked at every 15 s. A **lane** puts that second tunnel to work:
on the Iran server one NAT rule per users' port sends a random share of the **new** connections to the shifted port,

```
-t nat -I PREROUTING 1 -p tcp --dport 443 -m statistic --mode random --probability 0.43 -m comment --comment nikatel-lane-<standby> -j REDIRECT --to-ports 20443
```

and the rest go on to the tunnel. The NAT table only sees the **first packet** of a flow, and conntrack keeps what it decided: a connection that is open is **never touched**, so nobody is cut. The relief comes as connections end and new ones are spread.
The foreign side does not change: the standby's shifted port leads to the same target as the tunnel's port. DNS is never touched.

What is balanced: **per new connection** (not per byte), on every forwarded port of the tunnel with the same probability, TCP and UDP when the standby carries UDP. Long connections stay where they started - that is the price of "nobody is cut".

## The share

`share to the second tunnel = cap_s / (cap_p + cap_s)`, kept between 10 and 90 %:

* `cap_p` - the usual level of the tunnel's light test (the Doctor's bandwidth, `bw.ref`, never below its last test; no number older than a week; under 20 Mb/s is "very slow" and has another story);
* `cap_s` - one light test of the **standby** (`bandwidth.measure`: two connections, 3 s, at most 60 MB each), asked when a lane starts and kept for 6 hours. The test runs before any lock is taken, so a handover never waits for it.
* A standby that would add under max(10 Mb/s, 25 % of `cap_p`) is no lane. The rule is **replaced in place** (`iptables -R`: never a gap) when the share moves by 8 points, at most every 10 minutes.

## When it starts, when it stops (automatic, in the Doctor's modes *fix* and *move*)

**Starts** when all of this holds: the owner switched lanes on · the panel is not read-only · both tunnels have been up for a minute with a fresh look · no repair, no storm · the servers are calm (CPU under 85 %, memory under 90 %) ·
the Doctor's own detector (`services/capacity.py`, step 1 of the wish: the users use ≥ 85 % of the capacity in 60 % of the last ten minutes) has called the tunnel **FULL for four minutes** (its story - the swap without a cut, the move: steps 2 and 3 - starts after three and goes first: a lane waits while `capacity <tunnel>` works; the detector knows nothing for a minute after a start of the panel) · no automatic lane ended in the last hour.
**Stops** when the *total* load (tunnel + lane) stayed under **50 %** of `cap_p` for 20 minutes and the lane is at least 30 minutes old (85 / 50: no flapping); when the owner presses *Stop*; when lanes (or the Doctor) are switched off - a lane the owner started by hand stays until *Stop*.

## Failures and the standby

* The second tunnel falls (or no longer fits the tunnel): the lane is **parked** - every new connection goes to the tunnel; the wish stays and the lane is put again when both tunnels have been up for a minute. (The connections that were on the second tunnel end with it, as they would have without the lane.)
* The tunnel falls: the standby's own handover (a full redirect put **above** the lane rule) sends everything to the standby, as before. When the tunnel comes back and the way back parks its `RETURN` above the lane rule (that would make it dead), `lane_on` takes that `RETURN` off and the lane is freed at once (and by the 20-second look at the server).
* A reboot or a flushed firewall: there is **no boot unit** - the lane rules are gone and everything goes to the tunnel (the safe state); the job puts the lane again.
* Rules nobody asked for are parked; rules of a standby that does not exist any more are removed. When the standby is removed or rebuilt its lane rules go first (`PARK=0`); if the Iran server does not answer, the standby is **kept**.
* Rename and removal of the tunnel carry the wish and end it.

## Taking it off the careful way

Deleting the **last** rule of a NAT chain makes the kernel reset every connection it had translated (measured again for this feature, below). So `lane_off` first puts a tagged `RETURN` at the top of the chain and deletes the redirect after it (`PARK=1`); the INPUT accept keeps only the established connections.
Every few minutes `PARK=2` asks: *is a connection to the shifted ports left?* (`ss`) - only when none is, the parked rules are removed; otherwise they stay. `PARK=0` (the standby goes away) removes everything of the tag.

## The page

*Tunnel window → Overview*: a bar under the standby's - **Lane · Sharing 57 % + 43 %** (by capacity 76 + 57 Mb/s · users now 62 + 18 Mb/s · since 14:05), **Lane · Paused** (with the reason) and, when lanes are on and the tunnel is at 70 % of its capacity or more, **Lane · Nearly full** with *Share now*.
Nothing is drawn while a lane has nothing to say. *Tunnel Doctor* carries the switch *Add capacity by itself* (off by default). Persian texts: `i18n.fa.doctor.js`.

API: `GET|PUT /api/lanes/settings` (`{"on": bool}`), `GET /api/tunnels/{id}/lane`, `POST …/lane/start`, `POST …/lane/stop`; `GET /api/tunnels` carries `lane` per tunnel (`on`, `state` off · idle · waiting · sharing · paused, `why`, `pct`, `kind`, `manual`, `since`, `cap_p`, `cap_s`, `use_p`, `use_s`).

## What it costs, what it does not do

A lane needs a **ready standby** (the standby is built by itself for every tunnel in the modes fix and move, so there is nothing to mark). It adds one light test of the standby when a lane starts, one SSH read of the Iran server every 20 s while a lane exists (every 5 minutes after one existed), and the live read of the rates of both lanes while it works (to know when it is not needed any more).
It does **not** rebalance connections that are open (that would cut them), it does **not** add a third lane, it does **not** help when the *line* between the two servers is the limit for every type (both lanes share it), and it does not move users to another Iran server (that is the Doctor's *move*).
The users' rate of the tunnel (`use_p`) and of the lane (`use_s`) are the two lanes' own numbers; the tunnel's 24-hour peak in the list is the tunnel's alone while a lane works.

## How it is proved

* `tests/test_lanes.py`: the share, the probability, the capacity, "nearly full" and "not needed" over their windows, `decide` in every state and mode; the three reviewed actions **run for real** against a fake `iptables` (one rule per port, replaced in place, the standby's parked `RETURN` taken off, the careful end, `PARK=2` only when nothing is left, a foreign rule untouched); start / adjust / park / stop / clean / refresh with the failures; the job (starts only when nearly full, parks and puts again, ends an automatic lane, backs off a minute after a failure); the reconcile; the removal of a standby with a lane; the API and the page.
* **On a real kernel** (2026-10-21, the build server, iptables 1.8.10 on nf_tables, kernel 6.8, two network namespaces joined by a veth pair, the panel's own rendered actions; `lane-netns-proof.sh`, 42 checks, `FAILS=0`):

  | case | measured |
  |---|---|
  | a lane of 40 % | of 1000 new connections 395 went to the standby (allowed 340 - 460), none refused |
  | 20 connections open before the lane, 10 opened through it | every one of them answered from the same side in every later round |
  | the share changed to 70 % (`-R`) | still one rule; 691 of 1000 (allowed 640 - 760); open connections untouched |
  | a full handover (the standby's `standby_on`) over the lane | 100 of 100 to the standby; open connections untouched |
  | the standby's way back (parks a `RETURN` above the lane rule) | the lane is dead (0 of 400), `lane_list` says "shadowed", `lane_on` frees it: 698 of 1000 at 70 % |
  | the lane ends (`PARK=1`) | 500 of 500 new connections to the tunnel, the connections open on the standby still answered |
  | `PARK=2` with connections open | kept the rules, said "3 connections"; after they closed: nothing of the tag left; a foreign rule untouched |
  | the tunnel is cut while a lane is on | 79 of 200 new connections refused (the share that went to it); with the standby's full redirect 200 of 200 served; the connections that were on the standby kept working |
  | the standby dies while a lane is on | 85 of 200 refused; after `PARK=1` 300 of 300 served by the tunnel |
  | **negative control**: the last rule of the chain deleted outright | every translated connection was reset (11 of 11, 9 of 9; in one of three long runs 0 - not deterministic there; 9 of 9 in the isolated `lane-neg.sh`); with another rule left in the chain 0 of 6 - 13 reset |
  
## Not proved

A lane on **real tunnels with real users**: the real Backhaul / BackPack / Chisel standbys, their light tests, the share they get and how the users' rate moves over minutes (connection lifetimes decide how fast a full tunnel is relieved) - only the kernel rules, the decisions and the panel code are proved. How the light test of a standby of each type behaves (door-based standbys are measured by their quality test) was not measured here. The users' rate of a tunnel without a card (BackPack, Chisel) comes from its TCP connections and was not compared with a lane. The interplay with the other steps of the wish (a type swap or a move while a lane works) is covered by "the lane waits while the tunnel is cut or rebuilt", not by a drill.
