# Links carried inside another tunnel (1.69.41)

A link model of the panel (GRE, GRE over FOU/GUE, IPIP, WireGuard, IPsec ...) puts a kernel interface between the two servers, and Backhaul runs over the interface's 10.x addresses. Every one of the nine plain models
needs something to pass **directly between the two servers**: IP protocol 47, 4 or 50, or a UDP port. On a route that drops all of those, no plain model passes.

A **carried link** puts the packets of a WireGuard link inside a tunnel the route does let through, and Backhaul runs over the WireGuard addresses as before.

| kind | what it is | who dials | what must pass between the servers |
| --- | --- | --- | --- |
| `wgchisel:<transport>` | WireGuard inside a Chisel TLS WebSocket | the foreign server dials the Iran server | one TCP port (9400-9499) |
| `wgchiselfwd:<transport>` | the same | the Iran server dials the foreign server | one TCP port (9400-9499) |

`<transport>` is any Backhaul transport (`tcpmux`, `tcp`, `wsmux`, `wssmux`), like every other kind (`kind_parts`, `kind_link`, `split_kind`, Compare, Create tunnel). The names follow the other links (`wg:tcpmux`).

## The decision: why Chisel

The idea of the author of simple-tun-link: "if it gets harder, create the local IP with packet (paqet) or wstunnel, then run Backhaul on top."

| carrier | forwards UDP? | installed by the panel? | verdict |
| --- | --- | --- | --- |
| **Chisel** (`nikatel-chisel`, jpillora/chisel 1.12) | yes: a remote `.../udp` carries a UDP flow over the same WebSocket | **yes**: a core the panel puts on every server (`engines.ensure_on_node`) | **chosen**: TLS WebSocket on one TCP port, the key of the server pinned, a password in a file of 0600 |
| paqet | its forwards are `tcp` and `udp` (`forward_rows`), but it needs raw sockets and a TCP port of its own that the kernel must not answer; it is a KCP stream, not a way to carry a datagram flow for a kernel link | yes | not chosen: it fails where raw packets do not pass, which is the case this feature is for |
| wstunnel | yes | **no**: there is no installer for it, and a binary that is not installed the way the other cores are is not added | not chosen: adding it means a new core (download, checksum, cache, update) - a larger job than this one |

Chisel already ships with the panel, runs as a reviewed unit (`chisel_prepare`, `chisel_fp`), and a Chisel tunnel is known to pass on the owner's routes.

## How the packets go

```
   Iran server                                                                    foreign server
 wg (starts the talk) --UDP--> 127.0.0.1:Q [chisel] ==== TLS WebSocket, TCP port C ==== [chisel] --UDP--> 127.0.0.1:P  wg (answers)
```

* **One number N** (52000-52099) gives every port: `P = N` (the WireGuard port, both servers), `Q = N + 100` (the loopback "door" on the Iran side), `C = 9400 + (N - 52000)` (the TCP port of the Chisel server, on the Iran
  server for `wgchisel`, on the foreign one for `wgchiselfwd`). The ranges meet no port of another engine (Backhaul 9900-9999, Chisel 9600-9700, Paqet 9700-9800, BackPack Direct 9850-9950, the plain links' UDP ranges).
* **One UDP flow per link.** The Iran WireGuard end sends to its door; Chisel carries each datagram through the WebSocket and hands it to the foreign WireGuard. The foreign end has no endpoint: WireGuard learns it from the first
  packet (roaming) and answers the same way back. Both directions use the one flow, and every tunnel has its own Chisel connection, its own number and its own password.
* **Nothing but the TCP port is exposed**: the WireGuard sockets listen on all addresses, but the only packets that reach them come over the loopback; the firewall rules (tag `nikatel-gre-<name>`, removed with the link) name the
  exact peer address and the TCP port (as the destination on the dialling end, as the source on the other), and let the loopback through.
* **Units and files** (names the rest of the panel knows): the link unit `nikatel-gre-<name>` (up/down scripts, the health timer, the check script - the same as every link) and, beside it, `nikatel-carrier-<name>.service`
  (the Chisel server or client; no `[Install]`: the link unit starts it in its up script and stops it in its down script, so there is one owner). `/etc/nikatel-chisel/<name>.{key,crt,tls.key,srv.env|cli.env}` hold the
  server key, the self-signed certificate and the password (AUTH, 0600). The WireGuard private key stays in `/etc/nikatel/wg/<name>.key`, made on its server.
* **MTU**: the path between the servers does not limit the packet inside - it travels in a TCP stream - so no path MTU is measured. Chisel carries a datagram of up to 9012 bytes and WireGuard adds 60, so the link MTU is **8000**
  (`linkmodels.CARRIER_MTU`). Measured in two network namespaces (2026-10-20), one TCP stream through the link: MTU 1380 -> 120 Mb/s, 4000 -> 170-190, 8800 -> 235-245 (bigger and fewer packets cost the user-space relay less).
  The API accepts an MTU up to this for a carried link (`builder.mtu_limit`).

## What was proved (2026-10-20, the build server, no real route)

Two network namespaces, the policy of every chain set to DROP, so that **only what the generated firewall rules open passes** (the ends cannot ping each other). The files are the ones `builder.link_scripts` makes, the
actions are the reviewed ones (`chisel_prepare`, `chisel_fp`, `remove_tunnel_files`), Chisel (1.12), WireGuard and Backhaul (tcpmux, the toml of `builder.backhaul_texts`) are the real programs; only `systemctl` and `journalctl` are shims.

| | `wgchisel` (foreign dials) | `wgchiselfwd` (Iran dials) |
| --- | --- | --- |
| first ping | answered after 2 tries | answered after 1 try |
| 10 pings each way | 10 / 10 and 10 / 10, ~1.8 ms | 10 / 10 and 10 / 10, ~1.7 ms |
| 40 MB through the Backhaul port over the link | 16.8 MB/s (about 135 Mb/s) | 24.0 MB/s (about 192 Mb/s) |
| after 30 s of quiet | 3 / 3 pings | 3 / 3 pings |
| removal | no rule of the tag, no interface, no process, no file left; nothing written to the host | the same |

WireGuard alone through a Chisel port (hand-made files): MTU 1380 / 4000 / 8800 -> 120 / 170-190 / 235-245 Mb/s for one TCP stream. **Not proved**: a real route (the namespaces have 1-2 ms and no loss), the speed on the
owner's servers (a TCP stream carries the packets: expect less than the plain links; TCP over TCP), `wireguard-tools` installed by apt on a real server, a reboot of a server with a carried link, and the Doctor's last resort on a
real cut (it is tested with faked builds and a faked probe).

## Build, health, removal

* **Build** (`builder._build` -> `_prepare_link` -> `carrier_ends`): both servers can load WireGuard; `wireguard-tools` is installed **only when the owner asked** (`install_wg`, as for `wg`: Create tunnel, Compare, a survey the owner started);
  the Doctor and the night survey never install a package. Chisel is put on a server that lacks it (`only_missing`). Each server makes its own WireGuard key; the panel makes one carrier password. The end that runs the Chisel
  server is made **first** (key and certificate, unit, fingerprint read from its log), the other end dials with the fingerprint pinned. The link must answer ten pings each way after a warm-up of up to 14 single pings
  (`carrier_probe_ping`: the carrier opens its UDP flow with the first packets, and WireGuard repeats a lost handshake after 5 s), and still hold twenty seconds later. Every file is noted in `b.created` before it exists, so
  a failure removes both servers' files (`remove_tunnel_files`: the carrier unit, the `/etc/nikatel-chisel/<name>.*` files, the link, the key).
* **Health**: `tunnels.plan_for` lists `nikatel-carrier-<name>.service` beside the link unit for both ends; the check script of the link (3 missed pings -> restart the link unit) restarts the carrier with it; Chisel itself
  reconnects (`--max-retry-interval 5s`) and the unit restarts on failure. A carried link that does not answer is "gre_blocked" in the Doctor's words, and the sentence names the TCP port of the carrier.
* **Removal / rebuild / standby / move**: by name, as every tunnel (`delete_tunnel`, `rebuild_tunnel`, the warm standby, Move the tunnels): the record keeps `link`, `link_port` (= N), `wg` (public keys) and `carrier`
  (`kind`, `dial`, `tcp_port`, `wg_port`, `door`, `pinned` - no secret).

## Where the Doctor uses it

* **Not in the standard order** (`knowledge.PRIOR`, `READY`, `CUT_LINKS`) and not in the night survey: it is the slowest way (TCP carries the packets), so a cut tunnel is never repaired by walking it. The 8 kinds are in the pair test
  (`tunnel_test.GRID_ONLY`) and in Create tunnel.
* **The last resort of a cut** (`autopilot._carrier_last_resort`): after every kind of the list failed (and the link probe left the plain links out), the Doctor asks the link probe about the two carried links - the probe makes a
  temporary carried link on both servers, pings it and removes it - and builds, as the real tunnel, only a kind the probe saw pass. When `wireguard-tools` is not on the servers the story says what to do once (build a WireGuard
  tunnel from Create tunnel: that installs it); nothing is installed by the Doctor.
* **The walk of a build** (`builder.FALLBACK_LINKS`): a build the owner starts from Create tunnel whose link does not pass tries the other models; the carried ones are the very last (`wg`, `wgchisel`, `wgchiselfwd`), and, like `wg`,
  only where the owner allowed `wireguard-tools`.

## Not done, on purpose

* GRE / IPIP inside a carrier ("optionally GRE/IPIP in UDP"): GRE over FOU needs one UDP flow **per direction** (it has no roaming), a loopback tunnel whose two ends are 127.0.0.1, and it is not encrypted. WireGuard needs one flow
  and is encrypted. The registry (`CARRIER_LINKS`, `carrier_files`) is the place to add them; they were not built because they could not be proved here.
* A second carrier (paqet, wstunnel): see the table above.
