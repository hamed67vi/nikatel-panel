# Warm standby of a tunnel (1.68.1)

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
job. A standby that is not up for 45 s has its services restarted; not up for 5 minutes it is **built again** (only while the tunnel itself is up and no repair works on it). It never shows in a list, a count or a message.

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
* **Not yet done - the proof on throw-away servers with a real tunnel** (the design asked for it before anything ships): build a tunnel and its standby between the two test servers, stop the tunnel's unit, measure the time from the cut to the first answer through the users' port, bring the
  tunnel back and watch the return; repeat with GRE dropped on the route and with a reboot of the Iran server while the handover stands. The release ships with the switch off for this reason: do it on the test panel before a tunnel with real users is marked.
