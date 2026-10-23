# Warm standby of a tunnel (1.68.1; drill and two corrections 1.69.6)

The owner, 2026-10-17: "bring the delay down to the minimum - do items 1 to 9". Items 1 - 8 (the sentinel, the wake-ups, the quick read, the retries, the fast lane, the parallel builds, the cold-pair survey) made the Doctor notice a cut in about 5 s
and start the repair at once. What cannot be made shorter by waiting less is the last part: **building a tunnel of another type takes 60 - 90 s** (an average of 86 s over the real repairs of two weeks on the test panel). A warm standby
removes it: another tunnel of another type is built BEFORE the cut, kept idle, and the users' ports are handed to it in a few seconds. This is item 9; the design that came first is the origin of this page.

It ships **switched off everywhere**: nothing is built until the owner marks a tunnel (*Tunnel window → Standby → Turn on*).

## What it is

* A **real tunnel** between the same two servers, named `<name>-sb` (at most 15 characters: the name is the GRE card of a Backhaul standby), of the type the Doctor's knowledge ranks best for this pair and that does **not share the carrier** of the tunnel
  (`standby.carrier`: GRE kinds · WireGuard / GRE-in-UDP kinds · BackPack · BackPack Direct · Chisel · Paqet). A Backhaul-over-GRE tunnel gets a BackPack or a Chisel standby when one is known to connect or untested; a GRE standby only when nothing else is left,
  because a filtered GRE would cut both. A type known to connect on the pair comes before an unknown one; what failed on the pair lately, what the licence does not allow and what a standby build failed with in the last 6 hours are left out.
* On **shifted listen ports**: the users' port `P` is `20000 + P` on the Iran server (`P - 20000` above 45535); the port on the foreign side stays (`443=2096` becomes `20443=2096`). The shifted port is refused when another tunnel listens on it.
* Its row is **switched off** (`enabled` False, `record.standby_of` = the name of the tunnel; `models.is_standby`). Every place that lists, counts, alerts on, repairs, surveys, measures or moves the enabled tunnels leaves it alone by itself - the list, the
  dashboard counts, the daily report, the alerts, the Doctor's restarts and smart repair, the sentinel, the minute cycle and the fast lane, the bandwidth and live numbers, the analyst, the night survey, the failover. (The first design said `enabled` True with a
  second predicate on 21 places; one flag that 40 places already read is safer than 21 new conditions.) The places that must SEE it (the ports and names taken, the control ports, a server that cannot be deleted while it carries a tunnel) read the table without that filter.
* The **owner's mark** is kept by the tunnel's NAME (settings key `standby`), not on its row: a repair or a rebuild gives a tunnel a new row but keeps its name, and the mark and the standby stay. A rename moves both; removing the tunnel from its window removes both;
  a tunnel whose row has been missing for 3 hours is not coming back and loses them.

## The handover

When the tunnel is **down** for `after_seconds` (default 5) and its standby is **up** (looked at again first when its last look is older than 30 s), the standby job runs ONE reviewed action on the Iran server, `standby_on` (`ssh.py`):

* a NAT redirect in `PREROUTING` from the users' port to the shifted port (`-j REDIRECT --to-ports`), tagged `nikatel-standby-<standby name>` so that nothing else is ever touched, and an `ACCEPT` for the shifted port in `INPUT` (a closed INPUT chain would drop what
  was redirected); both are checked before they are added (`-C`), so the action can be run again and again;
* a small script and a boot unit (`nikatel-standby-<name>.service`) that put the same rules again after a reboot while the handover stands; the panel also reads the rules every two minutes (`standby_list`) and puts back a handover whose rules are gone (a flushed
  firewall) and takes off rules nobody asked for (a standby that does not exist any more, a give-back made while the server was off).

It takes about two seconds. The Doctor's ladder (restart, rebuild, another type ...) goes on with the tunnel in the background and the users do not wait for it. A handover is an entry of the Doctor's ledger (*Tunnel Doctor → Auto Moves*, reason `standby`, with
**Back now**) and an event; it does not count as a move for the rails of the moves (3 a day, 2 hours apart).

**What is NOT redirected: the request the panel makes on the server itself (127.0.0.1).** The first design also redirected `OUTPUT` "for the panel's own probes"; but then the tunnel that is cut would be judged by the standby's answers and look up the whole time. Now
the local request still goes to the tunnel, and a tunnel whose ports are on its standby (`tunnels.HANDED`) is judged **without** the look from another Iran server (that look would be answered by the standby): by its units, its link, its listening ports and the
request on the server, which is what IT carries.

**The way back.** When the tunnel has been **up for 60 s** (two checks; no repair running on it; the last look not older than 2 minutes) `standby_off` takes the redirects off: new connections go to the tunnel, connections that are open stay on the standby until they close (conntrack keeps their translation) - **provided a rule of ours is left in the NAT chain**: measured on the
build server (iptables 1.8.10 on nf_tables, two network namespaces), deleting the LAST rule of the chain makes the kernel forget the translation of every open connection and they are reset, so the way back first puts a harmless tagged `RETURN` at the top of the chain and then
deletes the redirect (and turns the `ACCEPT` of the shifted port into an accept of the established connections only). Those parked rules go with the standby (`standby_off` with `PARK=0`). Nothing is deleted. A handover made by hand (*Switch now*, a drill) stays until *Back now*.

## Never a cut of the tunnel

A standby is checked by its own job every 15 s with the same check as any tunnel (`tunnels.check_tunnel`: the units of both ends, its link, the ports, the request through the shifted port), but the check is quiet for it: no event, no history rows, no wake-up of the repair
job. A standby that is not up for 45 s has its services restarted (on both servers: the **GRE card first**, then the services on it - a card that was stopped is not started by anything else; before 1.69.6 the cards were left out and such a
standby waited for the rebuild); not up for 5 minutes it is **built again** (only while the tunnel itself is up and no repair works on it). It never shows in a list, a count or a message.

**The carrier after a swap (1.69.6).** The type of the standby is chosen when it is built, but the tunnel can change type later (the Doctor's smart repair swaps a cut tunnel to a type that connects, and the way back to the better tunnel brings it home): the drill of 2026-10-18 ended with
a Backhaul-over-GRE tunnel and a Backhaul-over-GRE standby - a GRE filter would have cut both. So a standby that has **the carrier of its tunnel** is built again on another carrier (`standby.better_carrier`) - when a type of another carrier is **known to connect on this pair** (an untested
type is not a reason to remove a working standby), the tunnel has been up for 60 s with a fresh look, no repair or storm is on, the standby is **older than 5 minutes** (no loop of rebuilds while the Doctor swaps the tunnel), and the last way back is **older than 15 minutes** (removing the
standby ends the connections that stayed on it). Never while the users' ports are on the standby. The knowledge is asked at most once a minute, and only when the two carriers are the same.

## The rails

* One standby per tunnel; none while the panel is read-only; nothing at all automatic in the Doctor's mode **off** (the buttons still work by hand); *fix* and *move* build, mend and hand over by themselves.
* A standby is built only when the tunnel is **up**, no repair works on it, no storm (several servers off or many tunnels down) is on, both servers are on and not busy (CPU under 85 %, memory under 90 %: a second tunnel doubles the processes of the pair) and the
  pair has a type that can be a standby. A build that is running when the tunnel falls is stopped, so the servers are free for the repair (it is built beside the tunnel, under its own lock, but two builds on the same server still wait for each other).
* A failed build waits 2, 4, 8 ... at most 60 minutes, and the type it failed with is not chosen again for 6 hours.
* A handover is refused when the standby is not up, or no longer fits the tunnel (other servers after a move, other ports after *Ports*: it is removed and built again), or the panel is read-only.
* An automatic build never installs a package on a server (the Doctor's rule): a WireGuard standby is built only where `wireguard-tools` is already there.

## The page

*Tunnel window → Overview*: a bar under the banner - **Standby · Off / Building / Ready / In use / Being mended / Waiting / Not built** - with the type and the shifted ports, and the buttons *Turn on*, *Turn off*, *Switch now* (a drill: hands the ports over until
*Back now*) and *Back now*. The standby is not a row of the list. *Tunnel Doctor → Auto Moves* shows every handover with its *Back now*. Persian texts are in `i18n.fa.doctor.js`.

API: `GET /api/tunnels/{id}/standby`, `PUT` (`{"on": true|false}`), `POST .../standby/handover`, `POST .../standby/back`; `GET /api/tunnels` carries `standby` on every tunnel (see [api.md](api.md)).

## What it costs, what it does not do

A second tunnel on every marked pair: its processes, its control port, its shifted ports, a build that runs once (and again when the standby is rebuilt), and one check of it every 15 s (two SSH commands). Mark only the tunnels whose users cannot wait a minute.

It makes a cut of the tunnel itself (a service, the GRE link, a filtered carrier) cost the users a few seconds. It does **not** help when the Iran server or the foreign server is off (that is the failover, or nothing the panel can do), nor when the route between the two
servers is cut for every type (the standby would be cut too - *Tunnel Doctor → Knowledge* says which types connect). While the ports are on the standby, the live numbers of a tunnel that is read from the users' own connections show the shifted port; the bar of the
tunnel is not the traffic of its users until they are back.

## How it is proved

* `tests/test_standby.py`: the pure rules (ports, names, carriers, `decide` in every state and mode); the reviewed actions **run for real** against a fake `iptables` (idempotent, only the tagged rules go, a longer name with the same start is untouched, the way back parks a rule);
  the hidden row through every list, count, alert, repair and survey; the handover, the way back and the failures; the job; the build; the switch and the API; the page.
* **The redirect on a real kernel** (2026-10-17, the build server, never the live panel): the actions of the panel, rendered by its own code, run in two network namespaces joined by a veth pair (a user at 10.77.0.2, the "Iran server" at 10.77.0.1 with a "tunnel" on 8080 and a "standby" on 28080).
  Measured: the tunnel cut -> a connection refused; `standby_on` -> a NEW user gets the standby; the request on the server itself (127.0.0.1 and its own address) is NOT redirected and still sees the tunnel; the tunnel comes back -> the server sees it again, users stay on the standby; `standby_off`
  -> new users are on the tunnel again and a connection opened during the handover goes on working on the standby (with the parked rule; without it: reset by peer); a foreign redirect in the same chain survives; nothing is left behind.
* **The drill with REAL tunnels** (2026-10-18, v1.69.5; a PRIVATE copy of the panel on the build server - its own data and port, the Doctor in mode *fix*; the Iran test server and the build server as the pair; users = five requests a second to the users' port from outside the Iran server):

  | case | measured |
  |---|---|
  | build | the tunnel (Backhaul tcpmux over GRE) 57 s; its standby (BackPack WSS mux) 42 s later; the list still shows one tunnel |
  | the GRE card of the tunnel stopped | judged down after 9.0 s, the users' ports handed over 1.7 s later, the first answer again 11.7 s after the cut: **10.8 s of outage** for the users (11 failed requests of 252); the Doctor mended the tunnel at +22.7 s, the way back came 60 s later |
  | the foreign end of a BackPack tunnel stopped | down after 8.8 s, handed over (5.8 s for the action that time), outage 12.3 s; the Doctor swapped the tunnel, the way back to the better tunnel (1.69.5) followed, the users went back to it after 132 s on the standby |
  | GRE (protocol 47) dropped between the two servers | down after 10.7 s, handed over 6 s after the cut; the Doctor removed the tunnel and built it as another type (2 min) - the users' port answered through the standby the whole time, also while the tunnel row did not exist; back after the new tunnel held 60 s |
  | the redirects deleted by hand on the Iran server | put again by the panel after 24 s |
  | a reboot (the boot unit started by hand: the rules are gone, the unit puts them) | the redirect is back at once, no failed request from +1 s |
  | *Switch now* / *Back now* / the standby turned off | 2.6 s / 2.6 s / 10 s, nothing left on either server |
  | the standby itself falls while the tunnel is fine | the tunnel stays up and nothing is said (no event); the GRE card of the standby that was stopped was **not** restarted (fixed in 1.69.6, see above) and the standby was built again after 5 minutes |
  | the carrier | after the swap and the way back the tunnel and its standby were both Backhaul over GRE (fixed in 1.69.6) |

  The failures around a way back onto a **BackPack Direct PCK** tunnel are that tunnel's own (measured alone, nothing else going on: 26 of 150 requests took over 1 s and 3 failed at 5 s, median 0.32 s). One request of 252 (1 s timeout) failed 8 s after the first way back, onto a
  Backhaul tunnel; it was not explained and did not come back in the other ways back.
  The standby stays **off by default**: mark only the tunnels whose users cannot wait a minute.

## Lanes (1.69.52)

When a tunnel is nearly full its standby can be put to work beside it - a NAT rule sends a share of the NEW connections to it, nobody is cut ([lanes.md](lanes.md)). The handover above is unchanged and stays on top: its full redirect is put above a lane's rule, its way back leaves a parked `RETURN` that `lane_on` takes off for the same port, and removing the standby takes its lane rules off first.

## The swap beside the tunnel (1.69.78)

When the Doctor itself changes the type of a dropped or very slow tunnel, it no longer removes and rebuilds the tunnel (a cut of a minute or two). The new type is built **as the standby** (`standby.build(name, kind, by_swap=True)`; `standby.want` makes the standby job build the same type), measured by a speed test through itself, given the users' new connections with a manual handover, watched for 90 s, and only when the open connections of the old tunnel have ended is the old tunnel removed and built again as the new type - the users stand on the standby meanwhile - and the new connections go back. A new type that is worse sends the users back and the old tunnel was never touched. When the swap cannot be made beside the tunnel the change is only a suggestion on the Doctor page. Code and the full order: `nikatel/services/swap.py`; tests: `tests/test_swap_beside.py`.

## The Trident (1.69.124)

A tunnel can also be marked for a **Trident** ([trident.md](trident.md)): the tunnel, this standby and one more tunnel of a third kind share the users' NEW connections by live quality in one NAT chain. While the Trident is active (marked, a plan put, its job answered in the last minute) it is the only writer of the users' ports: this job does not hand the ports over by itself and `Switch now` is refused; a handover that stands is still given back when the tunnel has held. When the Trident is not active (or its job stands still) the handover works exactly as described above.