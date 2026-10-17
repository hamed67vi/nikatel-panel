# simple-tun-link (STL) - what it is, what the panel had, what was taken (2026-10-17)

The owner asked (2026-10-17) for a close look at <https://github.com/ach1992/simple-tun-link> ("add whatever we do not have, replace what must be replaced") and for WireGuard. This is what was read, what the project does,
how it compares with the panel, and what 1.66.0 took from it.

**What was read:** the README, `docs/ARCHITECTURE.md`, `docs/DIAGNOSTICS.md`, the file tree (about 330 files, most of them tests), the WireGuard backend (`internal/backend/wireguard/apply.go`, `model.go`), the GRE backend's apply
order, and the firewall module (`internal/linux/firewall.go`). **Not read line by line:** the IPsec/strongSwan backend (about a third of the code), the installer script (59 KB), the state / lock / persistence engine, the pairing codec.
The project's own words on its testing: its CI does not certify every real two-server WAN, NAT, provider firewall or reboot case - and neither does this review: the links below were built and measured for real on the test servers (see the changelog).

## What STL is

A Go tool for Linux that makes **point-to-point IP links** between two servers you run, each end with a private *Link Address*, so that other software can talk over the link without caring how it is carried. Backends: **GRE** and **IPIP**
(native, or inside UDP: FOU / GUE), **WireGuard** (UDP, encrypted) and **IPsec** (strongSwan, IKEv2, XFRM). The kernel carries the packets; Go is only the control plane. A *Setup Link* (a coded string) carries one end's settings to the other
server; commands: `stl menu`, `stl link list|status|diagnose|remove|ensure`, `stl doctor ipsec`.

## STL against the panel

| Part of STL | The panel before 1.66.0 | 1.66.0 |
|---|---|---|
| **GRE link**, keyed, MTU, TTL | yes - the link under every Backhaul tunnel (`nikatel-gre-<name>`), MTU measured, firewall rules tagged | unchanged |
| **GRE inside UDP (FOU)** - passes where IP protocol 47 is filtered | no | **added**: `Backhaul · TCP Mux over GRE-in-UDP` (kind `fou:tcpmux`), one symmetric UDP port (5500-5599), the same GRE inside |
| **WireGuard** link | no | **added**: `Backhaul · TCP Mux / TCP over WireGuard` (kinds `wg:tcpmux`, `wg:tcp`): a kernel WireGuard link on one UDP port (51820-51919), keys made ON each server |
| IPIP (native / FOU / GUE) | no | not taken: no key (one IPIP link per server pair), nothing GRE does not do here |
| IPsec / XFRM (strongSwan) | no | not taken: protocol 50 is filtered like protocol 47, NAT-T needs UDP like WireGuard does, and it needs a strongSwan runtime on every server; WireGuard is the encrypted link |
| Keys that never leave their server, never in argv or logs | (no keys before) | **taken**: `wg_keygen` runs on the server, writes `/etc/nikatel/wg/<name>.key` (0600) and prints only the public key; the panel keeps public keys only (`record.wg`); the up script reads the key from the file |
| Owner tag on the interface (`stl:<id>` alias) so that only an own link is deleted | no (deleted by name) | **taken** for the new links: alias `nikatel:<name>`, and the down script deletes a link only when it is of the expected type |
| Firewall rule that names the exact peer, local address, protocol, port | rules per interface, GRE rules per peer | **taken**: UDP rules name the peer, the local address and the port; every `iptables` call of a link waits up to 5 s for the xtables lock (`-w 5`, as STL does) |
| Exact overhead per backend for the MTU (WireGuard 60 bytes) | the path MTU is measured (DF pings) and capped at 1360 | unchanged: 1360 + 60 fits a 1500 path |
| Restore after a reboot by one systemd unit | yes - the unit of each link is enabled; a timer checks the peer every minute | unchanged (the new links use the same units) |
| Diagnose: reachability probe with DF, MTU, latency / loss / jitter, throughput (opt-in) | yes - MTU probe at build, path scan, Doctor's quality test, the bandwidth job, Check all | unchanged |
| "Test connectivity" | Check all, the tunnel window | unchanged |
| Setup Links (pairing the other end by a coded string) | not needed: the panel configures both ends over SSH | not taken |
| Interactive menu / CLI | the web panel | not taken |
| State file with schema versions, operation journals, recovery docs | the panel database, the action log, "Recently removed" | not taken |
| Typed confirmation of every change | the panel asks where a change is risky | not taken |

## How the new links work in the panel

Everything above the link is as it was: the Backhaul units run over the inner addresses of a /30, the checks, the live numbers, the bandwidth job and the Doctor treat the link like a GRE link. The link keeps the names a GRE link has (the
interface is called like the tunnel, the units are `nikatel-gre-<name>`, `nikatel-gre-check-<name>.timer`) so that nothing else had to change; what differs is the up and down scripts (`builder.link_scripts`).

- **WireGuard:** `ip link add <name> type wireguard`, then `wg set <name> listen-port P private-key <file> peer <public key of the other end> endpoint <peer>:P allowed-ips <inner peer>/32 persistent-keepalive 25`, the inner /30 address, MTU 1360.
  The server needs the kernel module (Linux 5.6 or newer, or the module) and `wireguard-tools`. **A tool is installed with apt only when the owner asked for the build** (Create tunnel, a test he started, *Test now*); the Doctor and the
  night survey never install a package on a server: they fail with the sentence that says what to do, and the Doctor tries the next tunnel type.
- **GRE inside UDP:** `ip fou add port P ipproto 47` on both ends, then `ip link add <name> type gre local .. remote .. key K ttl 255 encap fou encap-sport auto encap-dport P`. Not encrypted (like GRE), no package needed (kernel modules `fou`, `ip_gre`).
- **The Doctor:** the kinds `wg:tcpmux`, `wg:tcp`, `fou:tcpmux` are in the pair test, in the night survey (`READY`) and - last in the static order, first when a survey found one connecting - in the replacements of a cut tunnel. A tunnel whose own UDP
  link is cut does not get the other UDP links as replacement (they would be cut the same way); a GRE tunnel cut by a protocol-47 filter does. The diagnosis says "the UDP port of the link is filtered" for a UDP link.
- **What a UDP link cannot do:** pass where UDP is filtered (BackPack TCP kinds, Chisel, the TLS WebSocket kinds remain for that), and WireGuard is not hidden: a filter that blocks WireGuard's handshake pattern blocks it. GRE-in-UDP is plain.

## Later, if it is wanted

IPv6 underlay (STL v0.1 has none either), GUE (GRE or IPIP with a UDP header of its own), a single WireGuard link without Backhaul (a bare L3 link: it needs port forwarding by NAT, which changes how the checks judge a tunnel), and
a per-link read-only "diagnose" in the tunnel window in STL's words (observed / blocked / unknown / interrupted).
