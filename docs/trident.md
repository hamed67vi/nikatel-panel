# Trident - one tunnel carried by several tunnels of different kinds

The owner, 2026-10-23: "64 tunnel types can be built in the panel - can you combine them into a strong multi-layer tunnel that gives the best connection in every condition? Make it our own method."

The panel builds 64 kinds of tunnel. Stacking them (one inside another) is not the answer: every layer adds its own header (the MTU shrinks), its own encryption and its own retransmission (TCP over TCP stalls), and the weakest layer sets the speed. What makes a route survive is the opposite: **several tunnels of kinds that cannot be cut by the same filter, all alive at once, and the users' connections spread between them by how well each one works right now.** That is the Trident.

It ships **switched off**: the owner marks a tunnel (*Tunnel window → Trident → Turn on*, or `PUT /api/tunnels/{id}/trident`). Nothing is built, put or changed before that.

The name is the owner's (2026-10-23: "give our three-layer tunnel a cool technical name and put it there [in the type list of Create tunnel]"): a trident has three prongs on one shaft - three strands, one tunnel. (The working name while it was built was *Weave*; nothing of it is left in the code.)

## Where it is

* **Create tunnel → Type → Automatic → Trident**: the tunnel is built as *Best of the day* (the kind that connects best between the two servers, `knowledge.best_kinds`, with the same fall-through to the next best kinds), and when it is built it is marked for a Trident - the story of the build says so (`Trident: <name> is marked - ...`), and the standby and the extra strand follow by themselves. API: `POST /api/tunnels` with `kind: "trident"` (the answer carries `trident: true`, the kind and the reason). The Iran server must be chosen (the smart choice of the Iran server cannot know the pair).
* **An existing tunnel**: *Tunnel window → Trident → Turn on*, or `PUT /api/tunnels/{id}/trident {"on": true}`.

## What it is

A **mode of an existing tunnel**, with up to three strands between the same two servers:

| strand | what | ports on the Iran server |
|---|---|---|
| 0 - the tunnel | the tunnel the users already use | the users' ports `P` |
| 1 - the standby | its warm standby ([standby.md](standby.md)), built by the standby job as always | `20000 + P` |
| 2 - the extra strand | one more real tunnel of a **third kind**, hidden like a standby (`enabled` False, `record.strand_of = <tunnel>`; in no list, count, alert, repair or survey), named by the standby rule (`<tunnel>-sb2`) | `30000 + P` |

The foreign side of each strand leads to the same target as the tunnel's port, so a connection that reaches any strand ends up at the same place. DNS is never touched, customers' configs do not change.

## The kinds do not share a fate (`traits`, `pick_kind`)

A filter on the route sees a few things of a tunnel: what must pass between the two servers (**family**: GRE · IPIP · ESP · UDP · TCP · ICMP), **which server opens** the connection (a route filtered one way only cuts one side), the **packets** (TCP, raw TCP, UDP, ICMP) and whether it looks like **HTTPS**. `trident.traits(kind)` says this for each of the 64 kinds; `pick_kind` chooses the next strand to share the least fate with the ones already there - another family first, then another side that dials, then other packets - and, among equals, a kind **known to connect on this pair** (`knowledge`) before one nobody tried; it leaves out what failed on the pair lately, what the licence refuses and what failed as a strand lately. Example: a tunnel that is Backhaul over GRE with a BackPack TLS standby gets a UDP strand (WireGuard-link Backhaul or BackPack Direct UDP), because no filter that cuts GRE or TCP cuts it.

## How the new connections are shared (`ssh.py trident_set`)

On the Iran server **one chain** of the NAT table, `NKTR_<10 hex of sha1(name)>`, called by one jump from PREROUTING per users' port (`-m comment --comment nikatel-trident-<name>`). The chain holds one rule per strand and users' port:

```
-A NKTR_x -p tcp --dport 443 -m statistic --mode random --probability 0.10000000 -m comment --comment nikatel-trident-<name> -j REDIRECT --to-ports 20443
-A NKTR_x -p tcp --dport 443 -m statistic --mode random --probability 0.11111111 -m comment --comment nikatel-trident-<name> -j REDIRECT --to-ports 30443
```

The chance of a rule is the chance **given that the rules above it did not take the connection** (`chain_probs`: shares 0.1 and 0.1 → 0.1 and 0.1/0.9); what no rule takes goes on to the tunnel; a rule that must take everything that reaches it (the tunnel is dead) has no statistic match. Properties, all proved on a real kernel (below):

* **Nobody is cut.** The NAT table sees only the first packet of a flow and conntrack keeps what it decided: a connection that is open is never touched, whatever the shares do. The share of a strand that falls is lost for *its own* connections only (a third, not all); the next ones go to the others.
* **All or nothing.** The chain is replaced with ONE `iptables-restore --noflush` transaction (flush + the new rules). There is never a moment with half of the old shares and half of the new; a mistake in the text leaves the old chain as it was (`TRIDENT_FAIL`).
* **The jumps never leave while the trident stands.** Deleting the last rule of a NAT chain makes the kernel forget the translation of every open connection ([standby.md](standby.md), [lanes.md](lanes.md)); an empty chain behind a standing jump does not.
* **The accepts come first.** INPUT gets one tagged ACCEPT per shifted port (a closed INPUT chain would drop what was redirected) *before* the chain is switched. Measured: with the accepts last, the first SYNs of every new strand were dropped at every change of plan. The accept of a shifted port that has left the plan lets only the established connections go on.
* One writer: while the Trident is active the standby does not hand the ports over by itself and lanes stand aside (below).

No boot unit: after a reboot everything goes to the tunnel (the safe state) and the panel puts the plan again within a minute.

## The weights (`target_weights`)

Every few seconds, for each strand that is usable (up or degraded, fitting the tunnel, settled for `USABLE_AFTER` = 30 s):

* **capacity** - the tunnel's usual light test (`bandwidth`), the strands' own light test (`bandwidth.measure`: two connections, 3 s, kept 6 h, in a thread, only while everything is calm); a strand nobody measured counts as the median;
* **quality** - `1 / (1 + (loss − 1) / 3)` (a loss up to 1 % is free, every 3 more points halve it) × `(best delay + 40) / (its delay + 40)` × the **stability of the kind** on this pair (`knowledge.stability`, 0.85 while unproven, never below 0.3);
* share ∝ capacity × quality, then held inside **[floor 5 %, ceiling 70 %]** (90 % while only two are usable): a healthy strand stays warm and measured, and no strand becomes the new single point of failure;
* **the way in**: a new strand, a strand that came back and a tunnel that was down start on step 0 and move up every `RAMP_STEP` = 120 s while healthy: at most 10 % → 25 % → 50 % → its own weight. A tunnel the trident meets for the first time is fully in at once. When the ceilings cannot add up to 100 % (everybody is on the first steps) the ones that are fully in carry the rest;
* **a strand that is not usable gets 0 at once** and its step goes back to 0. "At once" is from the moment the panel *sees* the fall: the tunnel within seconds (the sentinel, [tunnels.md](tunnels.md)), the standby within its 15 s look, the extra strand within its 10 s look; until then a share of the new connections still goes to it (a strand with a share of 30 % costs at most 30 % of the new connections for those seconds - the connections that were on it are lost anyway).
* **a tunnel whose row is missing** (the Doctor removed it and builds it again, a minute or two) gives its share to the strands at once (`_gone_share`, from the ports the last plan remembered), so that a rebuild of a tunnel that was still up cuts nothing for the new connections.

A rewrite of the rules is not made for a small drift (`should_apply`): at once when a strand fell, came back or took a step; a drift of 25 points after a minute; of 8 points after ten minutes.

## What it learns from the day (`services/weather.py`, 1.69.126)

The owner, 2026-10-24: "I want the tunnel to be strong against the day's data. I do not want a radar to look at - I only want its data to help the Trident tunnel. Take help from any source needed." So there is no page: every minute the panel works out **the weather of every route** - what each family of carriers (gre · udp · tcp · icmp · esp · ipip) does between that Iran server and that foreign server *right now*: **open**, **under pressure** or **blocked**, and since when - and the Trident reads it.

| source | what it says | strength |
|---|---|---|
| **the fleet** - every real tunnel and every hidden strand of the panel is a sensor | on one route, a tunnel of a family down with a cut-like cause while a tunnel of another family on the same route carries = that family is blocked there; on one Iran server (any foreign server), the Doctor's kind_wide rule (≥ 2 and ≥ 60 % of the family's tunnels down while another family is fine) = blocked on the server's other routes, one down = pressure. A server that is off is no evidence | strong on the route, weaker from the server |
| **the probes** | the link probe (gre, fou = udp, ipip, ipsec = esp; nothing stays on the servers) and the reach probe (`ssh.py reach_probe`: five pings and three TCP handshakes from the Iran server to the foreign server), asked for every pair that carries a Trident every 10 minutes, one pair at a time, only while both servers are under 60 % CPU and no repair runs | strong |
| **the builds** | a kind that failed on the pair because its carrier is filtered ("probably filtered", "cannot pass", "no data passes") = pressure on its family, blocked when two kinds of the family failed | medium |
| **open** | a tunnel of the family up on the route, a probe that passed - and open evidence newer than every failure wins: what carries now beats what failed an hour ago | - |

A block seen within two hours stands, pressure within one hour, open evidence must be younger than an hour; nothing known = nothing changes. Per route and family the panel also keeps the **pattern**: the minutes seen and blocked in each hour of the day (Iran time) over the last seven days - "usually blocked at this hour" = blocked in half of the minutes seen, an hour seen at least.

What the Trident does with it:

* **the weights** - a strand whose family is under pressure on its route counts × 0.5, blocked × 0.1 (never 0: the strand's own check decides death), and when the family is *usually* blocked within the next 30 minutes × 0.7 - **before** anything falls (`weather.factor`, `target_weights` item `wx`);
* **the choice of a kind** - a new strand is never a kind of a blocked family while another is left, a family under pressure comes after an open one (`pick_kind(states=…)`); *Best of the day* and the standby's choice put blocked families last too (`knowledge.weather_last`, `standby.choose_kind(blocked_families=…)`);
* **the renewal** - an extra strand whose family has been under pressure or blocked on the route for two hours while another family that no strand uses is open is removed and built again (the normal build path picks the kind; at most once in six hours per tunnel; `renew_due`);
* **the words** - the only place it shows: under the Trident bar, "udp blocked on this route for 2 h", and in the strands' rows (`weather` per strand).

Without weather every one of these does exactly what it did before.

## Running it (`trident.run`, every 5 s)

1. The tunnel must be marked, the Doctor not *off*, the panel not read-only.
2. The standby (strand 1) is marked and built by the standby job; the extra strand is built beside the tunnel **only when the tunnel and the standby are up**, no repair or storm is on and both servers are calm (`standby._busy_reason`); a failed build waits 2, 4, 8 … 60 minutes and its kind is not chosen again for 6 hours.
3. The extra strand is looked at every 10 s (`tunnels.check_tunnel`, in a thread; quiet: no event, no history). Not up for 45 s → its units are restarted (the GRE card first); 5 minutes → it is removed and built again, **after** the plan has taken its share away. A strand whose ports no longer match the tunnel's is removed the same way.
4. The plan is put (`trident_set`) or, when no strand has a share, the chain is emptied (`trident_off PARK=1`, state *idle*: everything goes to the tunnel). The tunnel is judged by what *it* carries while a plan stands (`tunnels.HANDED`: a request from another Iran server to the users' port may be answered by a strand).
5. Every two minutes the rules on the Iran servers are read (`trident_list`) and compared with what the panel put: rules that are gone (a reboot, a flushed firewall) are put again; rules nobody asked for are parked. `trident_list` also says when a rule of somebody else (a handover, a lane, a DNAT) stands **above** the jump and would take the first packet.

## The rails

* **Off by default**, marked by tunnel name; read-only panel: nothing; Doctor *off*: nothing by itself.
* **The safety net stays.** The Trident "owns" the users' ports (`owns`) only while it is marked, a plan has been put and its job answered in the last minute. Otherwise the standby hands the ports over and the lanes work exactly as before; a Trident whose job stands still must not leave a cut tunnel without a spare. A handover that stands is always given back when the tunnel has held (the Trident waits for it: `a handover of the standby stands`).
* **One writer** to the NAT chain of the users' ports: `standby.handover` (and its job) and `lanes.start` / the lane job refuse while the Trident owns them; turning the Trident on stops a lane first and refuses while a handover stands.
* Turning it **off** parks it: the chain is emptied (`PARK=1`), the jumps stay, the accepts keep the established connections; the extra strand is removed; the parked rules go (`PARK=2`) only when `ss` shows no connection left on the shifted ports. A removed tunnel takes everything with it (`PARK=0`); a rename parks the old name's rules at once and starts a chain under the new name.
* Nothing is installed on a server (an automatic build never installs a package: a WireGuard strand is built only where `wireguard-tools` is there).
* It costs a second and a third tunnel on the pair: their processes, control ports, shifted ports, one build each, and a look every 10 - 15 s.

## How it is proved

* `tests/test_trident.py` (45): Create tunnel → Trident (the build through `create.start`, the mark, the story), the traits of all 64 kinds and the choice of kinds; quality, weights (sums, floor, ceilings, the way in, a dead strand, a lone strand), `advance`, `should_apply`, `chain_probs` (the chain really gives the weights), the plan and what is read back; the reviewed actions **run for real** against a fake `iptables` / `iptables-restore` (the order accepts → chain → jumps → parked accepts, idempotent, a refused chain leaves the old one, the careful end, `PARK=2` only when nothing is open, other tridents and foreign rules untouched); the service with stand-ins (the first plan, the steps, a strand that falls / comes back, the tunnel that falls, a refused plan, waiting for the standby, a standing handover, restart / rebuild / stale strand, switching on and off, forget, rename, reconcile, rebalance); the API; the bar in the window (run through node); the job.
* **On a real kernel** (2026-10-23, the build server, two network namespaces, the panel's own rendered actions, a closed INPUT chain, three listeners: the tunnel and two strands) `scripts/trident-netns-proof.sh <tree> nft|legacy` - **26 of 26 checks on iptables-nft and 26 of 26 on iptables-legacy** (1.8.10):

  | check | measured |
  |---|---|
  | no trident | 100 of 100 connections reach the tunnel |
  | shares 40 / 30 / 30 | 900 connections: tunnel 342-385, A 258-265, B 250-300, 0 failed |
  | idempotent; a foreign NAT rule | the same call changes nothing; the foreign rule is untouched |
  | `trident_list` | two rules, one jump, not shadowed; a redirect of somebody else above the jump is reported as shadowing |
  | all or nothing | 48 - 58 plan changes ("everything to A" ↔ "everything to B") while 1500 connections arrive: **0 failed, 0 reached the tunnel**, both strands took about half |
  | open connections | 25 long connections opened before two plan changes and the careful end: **25 of 25 still answer**; new connections follow the new plan; a NEW direct connection to a shifted port is dropped again |
  | the end | `PARK=2` with no connection left restores the NAT table byte for byte; with 3 open connections it keeps the parked rules and says 3; `PARK=0` removes everything; a bad plan (`TRIDENT_FAIL`) leaves the two old rules |

* **On a REAL pair** (2026-10-24, the owner's throw-away Iran server at MTServer ⇄ the build server in Germany, 72 ms; a private copy of the panel, the Doctor in mode *fix*; users = four threads of short connections to the users' port from outside the Iran server, about eleven a second). The panel chose the kinds by itself: the tunnel **BackPack WSS mux** (Best of the day: GRE is filtered on this route; 761 Mb/s at the build, 1510 in the light test), the standby **Chisel TLS** (518 Mb/s), the extra strand **BackPack Direct xDi (ICMP)** - after `bpd:udp` and `bpd:quic` had shown that UDP does not pass either. The way in as designed: 10 % at once, 25 % two minutes later, then by quality (42 → 32 %); the kernel's own counters: 193 of 668 new connections went to the standby (plan 31.6 %).

  | step | what the panel did | the users |
  |---|---|---|
  | baseline, 60 s | shares 68 / 32 | 648 connections, **0 failed**, median 219 ms |
  | the standby's service killed on the Iran server | its share 0 after **17.5 s** (its look is every 15 s): "idle, everything to the tunnel" | 50 of 210 failed in those 17 s (its share), then 284 of 284 ok |
  | the standby's service started again | let in at 10 % after 47.8 s (it settles 30 s first); the third strand joined at 10 % meanwhile | 1179 of 1179 ok over 109 s |
  | the tunnel's own service killed on the Iran server | the strands carried everything after **6.5 s** (the sentinel); no standby handover (one writer); the Doctor started the service again within half a minute, the tunnel came back by steps | 59 of 90 failed in those 6.5 s (its share), then 16 of 238 (6.7 %, one answer of 1.5 s) while two strands of one minute carried alone, then 0 |
  | *Turn off* | the chain emptied in the same second, every new connection to the tunnel; the parked rules and the strands removed with the tunnel | 176 of 176 ok |

  3389 connections in all, 125 failed (3.7 %) - all but 16 inside the detection windows. Three things the drill found, all fixed in this version: (1) *Best of the day* on an untested pair tried three GRE kinds in a row (`best_kinds` now takes at most one GRE kind, `create.start` leaves the others out once a build says GRE is filtered); (2) the standby then tried the GRE kinds again, a minute each with growing waits - a real build that finds GRE filtered now teaches the memory of the pair (`knowledge.note_gre_filtered`, every GRE kind blocked for two weeks); (3) when every strand was on its first step the ceilings were all lifted at once, so a tunnel that had just come back took 56 % in the same second - the ceilings are now lifted one at a time, the strand that has been in the longest first. Both servers were clean afterwards (no unit, no link, no rule of the drill left). Scripts: `scratchpad` of the session (`wv_setup.sh`, `wv_real.sh`, `wv_cleanup.sh`).

## Not proved - read before trusting it with users

* **The weather is new (1.69.126).** Its rules are proved on made-up fleets and pairs (`tests/test_weather.py`), not yet on the real panel over days: whether a service crash is told from a filter quickly enough (a crash marks the family blocked until the Doctor's restart brings open evidence - a minute), whether 0.5 / 0.1 / 0.7 are the right factors (`weather.FACTOR`, one place), and whether the hourly pattern says anything useful after a week. A replay of the live panel's history through the fusion needs the owner's read-only yes. The fourth "refuge" strand (a WireGuard-in-Chisel kind on one TCP port when two families are blocked) is not built yet.
* **One route, one day.** The real-pair drill above ran once, on one route (GRE and UDP filtered), with short connections only; long downloads over a strand that falls, a route that flaps, and weeks of running are not measured. The three kinds of that route were chosen by the panel; a route that passes GRE gets other kinds.
* The failures of a cut are bounded by the detection window (the sentinel 5 s for the tunnel, 10 - 15 s for a strand) times the share of what fell; a strand with a big share costs more in those seconds. The two "young" strands that carried alone for 20 s lost 16 of 238 connections (an ICMP strand of one minute): a new strand is cheap insurance, not yet a proven road.
* The shares are by **new connection**, not by byte or packet: a long download stays on the strand it began on. The capacity numbers are light tests (3 s), not the day's speed.
* The live numbers of a tunnel (the bandwidth bar) show only its own share while a plan stands; the sum over the strands is not drawn yet.
* UDP is shared only when every usable strand carries UDP (they are built with TCP ports by default): in practice the Trident shares TCP.
* The delay and loss of a strand are those of its own check (the ping over its link or its door), not of the users' traffic through it.
* Per-port policy (UDP only on a KCP strand, a port pinned to a strand) and a fourth strand (a WireGuard-in-Chisel "carried" strand as the last refuge) are not in this version.
* A route that is cut for **every** kind (the international internet is down) cannot be helped by any Trident; that is what the Bale relay is for ([bale.md](bale.md)).
