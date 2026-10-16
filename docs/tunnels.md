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
| SNI spoofing | PCK plus a TLS hello naming a domain your route allows | ✓ |
| xDi (ICMP) | inside ping, for routes that filter UDP and TCP | ✓ |

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
