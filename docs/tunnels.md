# Tunnel catalogue

Every tunnel type the panel can **build, monitor, test, repair and remove**. All of them join an Iran server
(the side users connect to) with a foreign server (where traffic leaves). The panel never carries traffic itself:
tunnels are systemd services on the servers; the panel builds them over SSH, checks them every minute and
repairs them. Names of the real servers and tunnels live only in the panel's database, never in this repository.

## Engines at a glance

| Engine | Who dials whom | Carrier | Needs root raw sockets | Best for |
|---|---|---|---|---|
| **Backhaul over GRE** | foreign server dials the Iran server | a GRE link with private inner addresses, Backhaul on top | no | the default: stable, many users over few connections |
| **BackPack** | foreign server dials the Iran server | TCP, UDP, WebSocket, QUIC, raw TCP, ICMP (12 transports) | some | heavy filtering, lossy or throttled routes |
| **BackPack Direct** | Iran server dials the foreign server | an L3 link on a private `/30` network | PCK, SNI, xDi | routes where the foreign side cannot dial in |
| **Paqet** | foreign server listens, Iran server opens the ports | KCP over raw sockets | yes | routes that stall ordinary TCP |
| **Chisel** | reverse: the Iran server accepts, the foreign client dials · **forward**: the foreign server accepts, the Iran client dials | HTTP / TLS WebSocket, one inbound port | no | places only HTTP(S)-looking traffic passes; **forward = fast downloads for customers** |
| **Legacy GRE** | (found on the servers) | adopted tunnels built outside the panel | — | tunnels that existed before the panel; judged by backhaul + data path |

## Backhaul over GRE

A GRE interface on both servers (private `10.x.y.z/30` inner network, MTU measured on the real path), Backhaul
running on top. The panel measures the path MTU, tunes the kernel (BBR, fq, big buffers), proves the link with
pings in both directions and a 10-second hold test, and rolls everything back when a step fails.

| Transport | Description |
|---|---|
| TCP Mux | many users over few connections — **recommended** |
| TCP | plain TCP, one connection per user |
| WebSocket Mux | clear-text WebSocket, multiplexed |
| WebSocket TLS Mux | TLS WebSocket, multiplexed |

Tuned for many users (channel size, connection pool, keepalive, heartbeat, TCP no-delay, mux settings — all in the
*Advanced* box of Create tunnel). Ports can be changed afterwards without rebuilding (`443`, `443=2096`, `10000-10009`).

## BackPack (reverse tunnels, the foreign server dials in)

| Transport | Description | Root |
|---|---|:-:|
| TCP Mux | many users, few connections — recommended | |
| TCP | plain, lowest overhead on a clean route | |
| TCP + Stealth | looks like random bytes, for heavy filtering | |
| TCP + PCK | raw TCP segments, for routes that stall plain TCP | ✓ |
| UDP + KCP + FEC | lossy routes, games (UDP must be open) | |
| UDP | UDP services only, no reliability layer | |
| UDP + QUIC | TLS 1.3 over QUIC | |
| WebSocket | clear text, only behind your own TLS | |
| WebSocket Mux | WebSocket with multiplexing | |
| WebSocket TLS | TLS + decoy page, works behind a CDN | |
| WebSocket TLS Mux | TLS WebSocket with multiplexing | |
| xDi (ICMP) | KCP inside ICMP echo, slow, last resort | ✓ |

## BackPack Direct (the Iran server dials the foreign server)

| Carrier | Description | Root |
|---|---|:-:|
| UDP | plain UDP | |
| QUIC | a real QUIC session on UDP | |
| PCK | looks like an ordinary TCP flow | ✓ |
| SNI spoofing | raw TCP (PCK) that opens with a **fake TLS hello** naming a domain your route allows: a filter that only reads the name in the hello lets it pass; the rest is not TLS (it is the tunnel's own sealed frames) | ✓ |
| IP spoofing | raw packets whose **source address** you choose (BackPack's `spoof` carrier); profiles udp (default), icmp, tcp, icmpv6, proto58, ipip, gre; for paths that block, throttle or count traffic by source address | ✓ |
| xDi (ICMP) | inside ping, for routes that filter UDP and TCP | ✓ |

**SNI spoofing and IP spoofing are two different carriers** (the owner, 2026-10-17: "SNI spoofing is not there at all - fix its name or description"; "why did you not add the spoof of this project?"). SNI spoofing forges a *name* inside the first packet (a TLS hello); IP spoofing forges the *source address* of every packet.

**IP spoofing** works only where the provider lets packets with a forged source out - many do not, and a tunnel with a forged source that is dropped connects and carries nothing. So the panel builds it the way the project advises: **first without forging anything** (the tunnel uses the real addresses and proves the carrier works), the Iran server's real address is given to the listener (`spoof_peer_ip`, required there), the same packet profile goes to both ends; a forged source (`spoof_src_ip` on the Iran side, `spoof_peer_src_ip` on the listener) is set only when you type one. Whether a server can forge at all is answered by **Servers → the server → ⋯ → Tests → Spoof test…**: it pings another server of the panel five times as it is and ten times with the source rewritten (a tagged SNAT rule for ICMP echo requests to that server only, taken out again whatever happens), and the other server listens with tcpdump. The ordinary pings come first, so a provider that drops forged sources ("does not spoof") is told from one that drops ping altogether ("no answer"). The answer is kept in the Last results of the server ("Spoofing").

## Paqet and Chisel

* **Paqet** — KCP over raw sockets: the foreign server listens, the Iran server opens the user ports. Server key pinned on the client.
* **Chisel** — a tunnel over one WebSocket port, plain HTTP or TLS (self-signed certificate, key pinned). **Reverse** (`ws` / `wss`): the port is on the Iran server and the foreign server dials it. **Forward** (`fws` / `fwss`): the port is on the foreign server, the Iran server dials it and opens the user ports itself (Chisel server on the foreign side, client with its own listeners on the Iran side).

## Which side opens the connection - real tests

Measured on one pair (an Iran server and a foreign server 114 ms away, 78 ms through GRE) with the test door and random data (a tunnel that compresses must not look faster than it is): what a **customer downloads** (foreign → Iran), in Mb/s, over one connection, four together and eight together. Another route gives other numbers - use *Test this pair* before you decide.

| Tunnel | Who dials | 1 | 4 | 8 |
|---|---|--:|--:|--:|
| Backhaul · TCP Mux over GRE | inside GRE | 174 | 601 | 1079 |
| BackPack Direct · PCK | Iran | 665 | — | 666 |
| BackPack Direct · UDP | Iran | 432 | — | 324 |
| Chisel · forward WebSocket | Iran | 110 – 159 | 526 – 730 | 668 – 748 |
| Chisel · forward TLS WebSocket | Iran | 134 | 400 | 700 |
| Paqet (the defaults of 1.59.2) | Iran | 165 – 210 | 308 – 351 | 301 – 327 |
| BackPack · TCP Mux / TCP / WebSocket TLS (pool 16 – 64, three presets) | foreign | 9 – 142 | 26 – 130 | 60 – 130 |
| Chisel · WebSocket / TLS WebSocket | foreign | 82 – 86 | 19 – 109 | 44 – 65 |

(BackPack Direct · QUIC did not come up on this route.)

* **The route slows the data that flows away from the side that opened a TCP connection**, whichever of the two servers opened it. A tunnel that the **foreign server dials** (BackPack, Chisel) gives one customer connection 10 – 140 Mb/s, and all customers together stayed at 20 – 130 whatever the settings (pool, mux, preset were all tried); the "upload" of the same test shows 400 – 1000 because that data runs the fast way, and means nothing for customers. A tunnel that the **Iran server dials** (Chisel forward, Paqet, BackPack Direct) and the GRE link of Backhaul get the download the fast way; the customers' upload of a forward tunnel is slowed in its turn (70 – 110 Mb/s for Chisel forward), which matters less.
* **Prefer GRE where it passes.** Otherwise a tunnel the Iran server dials: Chisel forward needs no root and no raw sockets; BackPack Direct · PCK needs root and is the fastest for one connection; Paqet needs raw sockets and a provider that passes them.
* **Paqet** is bound by one CPU core on each server (about 320 Mb/s here; the cipher costs nothing, more connections change nothing). The window (4096 packets) and the buffers (16 MB / 8 MB) are what lift one connection from 52 to 200; MTU 1450 and more carries nothing - the panel stops at 1400.

## What the panel does with each tunnel

| Stage | What happens |
|---|---|
| **Create** | preflight of both servers (root, python3, x86_64, free ports, no broken units), port and network allocation, install of the engine binary (verified download, copied from the panel's cache when a server has no internet), configs and systemd units on both sides, two-way ping and data-path check, rollback of everything created when any step fails |
| **Monitor** | every minute: units on both sides, the carrier interface, 20 pings (loss ≥ 15 % = degraded; Iran–abroad paths normally lose 3–13 %), forwarded ports, and one HTTP request through the first forwarded port. A change of state needs two agreeing checks (hysteresis) except *down*, which applies at once |
| **Alert** | one DOWN after the configured checks and minimum down time, one UP after a stable period, a FLAPPING notice and then silence for tunnels that go up and down repeatedly |
| **Repair** | *Smart repair*: diagnose both servers, the route, the carrier, the control port and the data path → restart only what is broken (Iran side first) → re-check after each step → rebuild → (GRE filtered or lossy) the link again from scratch with a new inner network and a new GRE key → another tunnel type → a move to another Iran server with its CDN records (the only step that asks first). Nothing else asks for approval. Limits: 2 restarts per tunnel per hour, never when a server is unreachable. Every run is stored with its full story |
| **Test** | *Tunnel test*: builds a temporary tunnel per chosen transport between two servers, measures latency, jitter, loss and download speed, scores it 0–100, removes it and compares all of them in one table (22 test kinds in 5 groups) |
| **Advise** | *Network advisor*: ranks every tunnel type on today's network for a route |
| **Change** | edit forwarded ports, rebuild with the same name and ports, remove from both servers (typed confirmation), adopt tunnels found on the servers |
| **Update** | check and update Backhaul and Xray on every server (verified download, units restarted) |

## Choosing a type

| Situation | Start with |
|---|---|
| Normal route, many users | Backhaul · TCP Mux |
| Customers' downloads are slow through a reverse tunnel | Chisel · forward, BackPack Direct · PCK, Paqet (the Iran server dials) |
| Provider throttles or stalls plain TCP | BackPack · TCP + PCK, Paqet |
| Deep packet inspection | BackPack · TCP + Stealth, WebSocket TLS (behind a CDN) |
| Lossy route, games, voice | BackPack · UDP + KCP + FEC, BackPack Direct · UDP |
| Only a whitelisted domain passes | BackPack Direct · SNI spoofing |
| UDP and TCP both blocked | BackPack · xDi (ICMP) |
| The foreign server cannot be dialled | BackPack Direct (the Iran side dials) |

When unsure, run *Tunnel test* on the pair: it tries the engines you tick and says which one works best today.

## What the repair does when things break (tested for real, 2026-10-12)

Three faults were caused on purpose on a Backhaul · TCP Mux tunnel between a pair of servers without users, with the
panel's defaults (check every minute, plain repair after 3 minutes down, 2 plain repairs per tunnel per hour, smart
repair level *switch*). What the panel did, by itself:

| Fault | Seen as | What happened | Users had no service for |
|---|---|---|---|
| the tunnel service stopped on the foreign server | *down* after 108 s: "backhaul-kharej… is inactive" | plain repair restarted the stopped service → up. (Found and fixed here: the check right after the restart still used the external probe's answer from before it and called the tunnel "still down"; the repair now asks again, up to four times over 40 s. A stopped service is now restarted at the next minute, without the 3-minute grace.) | ~6 min then, ~2 min now |
| the GRE interface deleted on the Iran server | *down* after 44 s: "the GRE interface … is missing" | the on-server watchdog timer (`nikatel-gre-check-<name>.timer`, every minute, installed with the tunnel) put the link back after 87 s; the panel logged "works again" and did nothing else (a missing interface is now also restarted at the next minute should the watchdog not be there) | ~1.5 min |
| GRE (protocol 47) filtered on the route (a DROP in the foreign server's raw table, where the watchdog cannot open it again) | *down* after 65 s: "no answer from the foreign end over the GRE link"; the Doctor's look said "GRE is filtered on this route" | plain repair restarted the GRE link and both services (no effect) → smart repair: a new GRE link with a new inner network and key was tested **beside** the tunnel (it did not pass either: "nothing was removed, the tunnel stays as it is") → four other kinds were tested → the tunnel was replaced by BackPack · TCP Mux on the same ports (score 79) | ~9.5 min |

Two things the tests also showed: a GRE filter inside the server's own firewall is undone by the watchdog within a
minute (it re-inserts its ACCEPT rules above anything else), and a repair never deletes a tunnel unless the
replacement already carries data.


## The Doctor and a tunnel that is cut (2026-10-16)

Found on a real panel: the foreign server of a Backhaul tunnel was filtered (GRE dropped). Three other tunnel types **did** connect to it, but the Doctor scored them by delay alone (36 - 42, a route of 150 ms cannot do
better without a speed number) against a line of 45, said "no other tunnel type works well" and left the tunnel cut for hours; a restart "fixed" another tunnel that was cut again a minute later and was restarted
again and again. What the Doctor does now:

| Step | What | Why |
|---|---|---|
| detect | a tunnel is *down* at once when its services stop or the GRE link is silent; the Doctor acts **at once** - *Auto Fix Tunnels* has no waiting time any more (1.65.1; it was 60 s, 20 s in 1.65.0). A **sentinel** asks every server every 10 s, in ONE command, for the state of every unit, every GRE card and three quick pings of every GRE peer: a tunnel that is up and loses one of them is checked at once instead of at the next turn of the minute cycle. A degraded tunnel that moves no data is looked at by the fast lane (every ~8 s), so it is called *down* within a minute, not four | a blip is not a cut |
| one restart, only where it can help | a stopped service or a stuck data path gets **one** plain restart (every unit of both sides in one go, then ONE look - about 50 s, it used to be four restarts with a look after each, three minutes). A silent route (GRE does not answer, the port is cut in front of the server) that was cut like this in the last 14 days gets **none** | a restart cannot undo a filter |
| diagnose | ping both ways, the GRE link, and **can each server still open a TCP connection to the other** (the SSH port): it decides which tunnel types can work | the foreign address filtered from Iran = only the foreign server can dial |
| switch | the cut tunnel is replaced by the first tunnel type that **connects**, each one built as the real tunnel (same name, same ports) and checked for data - there is nothing to test beside a cut tunnel. The order: what healed this pair last time (the playbook), what is known to connect on this pair, what connects on the other pairs of the same foreign server, then the order of a cut tunnel (below). A type that failed here is not tried again for two weeks | the GRE link is not tested again where GRE is filtered; any tunnel that connects beats none (no score line) |
| hold | a repair counts only when the tunnel is up in two checks in a row, and a restart or rebuild that healed it must still be up 25 s later (it was 60); a step that healed the tunnel less than 30 minutes ago and it is cut again is not repeated | "fixed" must mean connected |
| retry | a tunnel that is down gets a new smart repair 1, 2 and 5 minutes after the END of the last one, then every 10 minutes (the types that failed on the route are not tried again for two weeks, so a quick retry costs little); one that only flaps every 30 minutes; none waits more than 5 minutes past its grace for a restart that never came. Up to 4 smart repairs run at the same time (it was 2) | nothing is left unrepaired |
| wake-ups | the repair job ticks every 5 s (it was 20) and runs at once when a tunnel is cut and when a restart or a smart repair ends: the next step is never waited for until the next tick | each hand-off used to cost 10 - 20 s |
| remember | every cut is kept (`knowledge.incidents`): the cause the Doctor found, the steps in order with the type each built, what healed it, how long it took. The next cut of the pair starts with the type that healed it | "keep it in memory so that next time it knows what to do" |

**The order of a cut tunnel** (robustness first, speed afterwards): when only the foreign server can dial (the Iran server cannot open a TCP connection to it) the reverse kinds go first - BackPack WebSocket TLS Mux, Chisel TLS,
BackPack TCP Mux, BackPack WebSocket TLS, BackPack TCP, Chisel, Stealth ... - and the forward kinds last; when the Iran server can dial, the fast forward kinds go first (BackPack Direct PCK, Chisel forward, Paqet ...). GRE
kinds are left out where GRE (or the whole route) is cut.

**Readiness - the night survey** (Doctor page, Knowledge; on by default): between 02:00 and 06:00 (the report time zone), when no repair runs, no server is over 60 % CPU and the tunnels of the two servers carry under
150 Mb/s, the pair with the least knowledge is tested - ten tunnel types (`READY`: the ones a repair reaches for), one pair at a time, temporary tunnels that are removed again, **delay and loss only** (a few pings
through the temporary tunnel - no speed test on a server that carries users; the speed of a type is known from the quality test of the live tunnels and from *Test now*). A repair takes the place of a running
survey at once. Every pair test the panel runs and the quality test of every live tunnel with a test door feed the same knowledge. *Test now* surveys all 24 types of a pair by hand (about 40 minutes).
A pair that has **never** been surveyed (a new tunnel) is surveyed by day too, as soon as the tunnels of its two servers carry under 30 Mb/s (1.65.0): without it a cut on that pair is repaired by trying the types one by one, the longest part of a repair.

**What a build asks the two servers** (1.65.0): the facts, the MTU probe, the kernel tuning, the two GRE ends and the GRE check are asked of the Iran and the foreign server at the same time (they were one after the other), and a built tunnel must still run 5 s later (it was 8).

**The engine of a slow tunnel**: when only one tunnel is slower (the servers are idle, the line is clean, its delay is as usual) the Doctor restarts its tunnel service on both servers, measures again and writes whether
the restart brought the speed back (at most once in six hours; the result goes to the memory as the cause `slow`).

**Tested for real (2026-10-17, a Backhaul tunnel between two test servers, the Doctor in *Auto Fix*, grace 30 s):**

| Cut | Detected | The repair | Users connected again |
|---|---|---|---|
| GRE (protocol 47) filtered, first time (this release without its last two changes) | 41 s | two plain restarts (80 s each), diagnosis 51 s, then BackPack Direct · PCK built as the real tunnel | 6 min 27 s |
| the same, after the Doctor remembered it (the final code) | 41 s | **no restart** (this pair was cut like this before), diagnosis 9 s, the type that healed it last time first: BackPack Direct · PCK | **3 min 12 s** |
| the foreign server filtered from Iran (what the Iran server starts - GRE, new TCP connections, pings - dropped; what the foreign server starts passes) | 51 s | no restart; the Iran server cannot dial, so the reverse kinds first: BackPack WebSocket TLS Mux connected on the first try | **3 min 36 s** |

A raw-socket tunnel (BackPack · PCK, Paqet) reads its packets before the firewall: no rule of the firewall cuts it, which is why the second cut was made on a GRE tunnel.
**What is not instant**: a tunnel type built from nothing takes about a minute to connect, so with this the users are connected again about three minutes after a cut instead of hours. Seconds need a tunnel that is
already built and waiting (a standby), which is the next step.

## The Bandwidth column: the capacity and what the users use (2026-10-17)

The owner: "show the capacity, and beside it the bandwidth the users are using - the numbers must be real, reliable and live". Every running tunnel shows two numbers and a bar in one cell:

| What | Number | Where it comes from | How fresh |
|---|---|---|---|
| **capacity** (blue) | the download speed the tunnel can carry | the Doctor's last light test (`services/bandwidth.py`): two connections over the GRE link from a sink on the foreign server, or the quality test through the test door of BackPack / Paqet / Chisel | every 90 minutes (every half hour with a door); it CANNOT be live - a test moves data over the tunnel the users are on - so its age is in the tooltip |
| **in use** (↓ ↑) | what the users use of it now, down and up | the byte counters of the tunnel's own link on its Iran server (the GRE link, the `bp<N>` link of a BackPack Direct tunnel); for a reverse proxy with no link (BackPack and Chisel over TCP, WS, WSS or mux) the byte counters of its TCP connections - see below - counted over the last ~15 s (`services/livebw.py`) | every 5 s while the Tunnels page is open (the page asks `GET /api/tunnels/live` every 4 s and changes the cells in place), every 30 s otherwise |
| **bar** | the part of the capacity the users use | in use (down) / capacity | blue; amber from 70 %, red from 90 % |

What makes the numbers trustworthy:

* They are the kernel's own counters (`/sys/class/net/<card>/statistics`), read by ONE light command per Iran server for all its cards, on the server's own uptime clock - the time an ssh answer takes does not move the rate.
* Download is what comes IN over the link (foreign -> Iran: what the users download), upload what goes out.
* A tunnel with **no card of its own** (a reverse proxy: BackPack and Chisel over TCP, WS, WSS or mux) is read from its **TCP connections**: the kernel keeps a byte counter for every socket (`ss -ti`: `bytes_acked` out, `bytes_received` in), and the panel adds up the connections that came in on the control port of the Iran server (the foreign server dialled in) - or, for a forward tunnel, the ones the Iran server opened to the foreign server - and takes the difference between two reads of each connection. They include the framing of the tunnel (mux, WebSocket, TLS: a few percent) and what a very short connection moved after the last read is lost; a connection that is new counts what it has, one that is gone says nothing more. Tried for real (a download of 42 Mb/s limited by curl through each kind): BackPack `tcpmux` 41.9 (curl 41.9), `wssmux` 41.8 (42.0), `tcp` 42.2 (41.9), Chisel `ws` 42.2 (41.9) and `fws` forward 42.0 (41.9).
* What has **no TCP connection between its servers** either (UDP, QUIC, KCP, raw sockets: **Paqet**, BackPack over UDP) is read from the **users' own connections** to its forwarded ports on the Iran server (the owner, 2026-10-17, his picture of two Paqet tunnels with a dash: "show the users' traffic in the different tunnels"): the users connect over TCP to the Iran server whatever carries their data on, the Iran server (the Paqet client listens on the port itself) accepts them, and the kernel's byte counters of those connections are exactly what the users move - what the Iran server sends them is their **download**, what it receives their upload (`via: "users"`). No framing is in these numbers. Looked at on the real Iran server of his two Paqet tunnels (read only): 109 connections on port 80, 0.68 Mb/s down and 0.09 Mb/s up over 8 s. A tunnel with no forwarded port has no counter: its cell says "-", never a made-up zero. A server that does not answer is asked less and less (5 s, 10 s ... a minute) and never sends the panel round another server (`run_light`: no jump search).
* The unit is written once, in the header of the column ("Bandwidth Mb/s"); the numbers carry none, and a speed above 999 Mb/s is still written in Mb (1234, not "1.2 Gb/s") - the owner's rule of 2026-10-17. The header says nothing else (what "capacity" and "in use" are is in its tooltip).
* The panel's own tests of the link (the bandwidth test, the quality test, the speed tests) are **left out** (`livebw.quiet`): what they move is not the users'. The numbers wait for them and start again a few seconds after.
* A counter that went back (the card was made again), a clock that went back (a reboot) or a rate no card can carry starts the window again; a number older than two minutes is not shown; the number of the last check (about a minute old) fills in until the live one is there and is shown dimmed.
* The check's own rate (`rx_mbps`, `tx_mbps` in the list) is no longer averaged over a gap: two checks more than five minutes apart (the panel was off) give no rate.

The **Ping / Loss** cell beside it (the owner's picture): the ping on top, an arrow at each end of a dotted line, the loss below - pale blue while the link runs, pale red with a cross while it is cut (and then no numbers), the loss amber from the line the panel calls lossy, "no ping" where the provider drops ICMP (the loss is then counted with UDP packets through the bandwidth sink). The arrows are pale on purpose: the numbers are what is read.

The **end of a row** is the "⋯" button of the server list (the owner, 2026-10-17: three stacked dots in place of the soft "›"): it opens the menu of the tunnel window - Repair (Fix, Dry run, Health check, Speed test, Restart), History, Mute alerts, Rename, Ports, Rebuild and, last and red, Remove - and a click on the row itself still opens the window. A tunnel that is being rebuilt shows its spinner in place of the button. An address copies when it is clicked; the copy mark shows **inside** the address box on the free side (left of an Iran address, right of a foreign one), so a cell never cuts it - in a tight cell the address gives way with "...".

The columns (2026-10-17): the middle block - Bandwidth, Iran node, ping / loss, Foreign node - has the widths of its content (150 / 170 / 140 / 170 px) so the numbers and the nodes sit close together; the room that is left goes to the name, the type and the ports (19 % / 19 % / 12 %). The Bandwidth column is an important column (`data-prio="1"` for `fluid.js`: it is the last data column to drop in a narrow window) and keeps at least 118 px. The tabs of the Network page are **Tunnel manager · Tunnel Doctor · CDN**.

## The tunnel window: six boxes (2026-10-17)

The owner: "show the stability and the quality of the users' internet inside their own box, not in the cards - I do not want the cards to be crowded". The rows of the list carry neither; the window of a tunnel has two rows of three boxes:

| Row | Boxes |
|---|---|
| 1 | **Servers** · **Status** · **Link** (latency, packet loss, speed, capacity, the GRE link and the units of both sides) |
| 2 | **Users' internet** · **Stability** · **Last results** |

* **Users' internet** reads what the Doctor already keeps of the configs that ride the tunnel (`record.ux`, `services/userq.py`): the grade (Good / Fair / Poor / Bad - or "not known yet" when no config of the probe rides the tunnel, with the way to tie them in the Doctor page), how many configs are fine, what is wrong (slow / unstable / down), the two worst configs with their sentence and, for a poor or bad grade, the Doctor's reading of where the problem is (two lines, the whole of it in the tooltip).
* **Stability** is `GET /api/tunnels/{id}/stability`: the share of the checks that answered over 24 hours and over 7 days, the cuts (the analyst's rule: two failed checks in a row; one lost check is noise and counts only against the share), the last cut and how long it lasted, how long the tunnel has been up, the verdict of the analyst (Stable / Watch / Unstable) and the strip of the last day (48 cells of half an hour).
