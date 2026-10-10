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
| **Chisel** | Iran server accepts, foreign client dials | HTTP / TLS WebSocket, one inbound port | no | places only HTTP(S)-looking traffic passes |
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
* **Chisel** — reverse tunnel over one WebSocket port on the Iran server, plain HTTP or TLS (self-signed certificate, key pinned).

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
| Provider throttles or stalls plain TCP | BackPack · TCP + PCK, Paqet |
| Deep packet inspection | BackPack · TCP + Stealth, WebSocket TLS (behind a CDN) |
| Lossy route, games, voice | BackPack · UDP + KCP + FEC, BackPack Direct · UDP |
| Only a whitelisted domain passes | BackPack Direct · SNI spoofing |
| UDP and TCP both blocked | BackPack · xDi (ICMP) |
| The foreign server cannot be dialled | BackPack Direct (the Iran side dials) |

When unsure, run *Tunnel test* on the pair: it tries the engines you tick and says which one works best today.
