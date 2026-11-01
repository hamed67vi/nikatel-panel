# The L2 carriers from C-Tunnel (1.69.180)

A link model of the panel puts a point-to-point kernel interface between the two servers, and Backhaul runs over the interface's inner /30 (see `docs/simple-tun-link.md` and `docs/carried-links.md`). The nine plain models (GRE, GRE / IPIP
over FOU / GUE, WireGuard, IPsec ESP / NAT-T) are all **L3**. The project <https://github.com/CIAUB/C-Tunnel> adds a family of **L2** carriers - an ethernet pseudowire rather than a routed link. This release takes the four the Linux kernel
makes by itself, with no daemon and no third-party binary: the panel puts the inner /30 **on** the ethernet interface and Backhaul runs over it exactly as over a GRE link, so nothing above the link changed.

| kind | what the kernel makes | what must pass between the servers | encrypted | one per pair? |
| --- | --- | --- | --- | --- |
| `l2tpv3:<transport>` | a static L2TPv3 pseudowire, IP encapsulation (`ip l2tp add tunnel … encap ip` + `ip l2tp add session`) | **IP protocol 115** | no | no (tunnel / session ids) |
| `vxlan:<transport>` | VXLAN (`ip link add type vxlan`) | UDP 4789-4799 (the VNI tells tunnels apart, so the standard port is the base) | no | no (VNI) |
| `geneve:<transport>` | Geneve (`ip link add type geneve`) | UDP 6081-6091 | no | no (VNI) |
| `gretap:<transport>` | ethernet over GRE (`ip link add type gretap`) | **IP protocol 47** (like GRE) | no | no (GRE key) |

`<transport>` is any Backhaul transport (`tcpmux`, `tcp`, `wsmux`, `wssmux`), like every other link. None is encrypted on its own; put an encrypted tunnel above it, or use WireGuard / IPsec where their protocol passes. The kernel modules a build / probe needs
are reported by `ssh.py` `link_facts` (`l2tp_eth`, `l2tp_ip`, `vxlan`, `geneve`, `ip_gre`) and loaded before use.

## Why L2TPv3 matters

The owner's route of 2026-11 between a filtered foreign server and an Iran server dropped TCP, UDP, ICMP and IPIP in the foreign→Iran direction while letting **GRE (47), ESP (50) and L2TPv3 (115)** through (measured with tcpdump on each end).
The panel already builds GRE and IPsec (ESP); L2TPv3 is the third protocol that passes such a route, and it was the one carrier of either project not already covered. GRE-TAP rides protocol 47, so it passes wherever plain GRE does (an L2 link where
one is wanted). VXLAN and Geneve ride UDP, so they pass only where a UDP port is open - not on that particular route.

## How the L2TPv3 ids are chosen

L2TPv3 over IP demuxes an incoming data packet by its **session id**, so the two ends' ids must differ and be unique on a server across its l2tpv3 links. `linkmodels.l2_ids` derives a ~30-bit value from the GRE key and gives the two ends the
mirror of it (the end whose public address is the smaller takes one, the other takes the same value with a high bit set); session id = tunnel id. Both ends agree without the panel sending anything per end, as GRE and IPIP agree on one key, and two
links of one server collide about one time in a billion.

## What was proved (2026-11-02)

* **Netns, both real kernels.** The four carriers were brought up and pinged 10/10 each way in an isolated network namespace pair on iproute2 5.15 (kernel 5.15) and iproute2 6.19 (kernel 7.0) with the exact `ip` commands the scripts generate.
* **A real Iran ⇄ foreign pair, the panel's own pair test** (build, measure, remove; nothing left on either server, checked). On the route described above:
  * **L2TPv3** - link 10/10 both ways, 77 ms, held 20/20 after 10 s, Backhaul control up, data path HTTP 200, **160 Mb/s** down, score 66.
  * **GRE-TAP** - link 10/10 both ways, 71 ms, **190 Mb/s** down, score 71.
  * **VXLAN** - the link came up but 0/10 both ways (its UDP port is blocked on that route); the build said so and removed everything. This is the correct behaviour where UDP is filtered, and it exercises the build / MTU / firewall / detection
    path of the UDP L2 carriers.
* **Not proved:** VXLAN / Geneve on a route where UDP is open (they fail by design where it is not), and hours of stability under real traffic.

## Not built, on purpose

* **C-Tunnel's EoIP and EtherIP.** The kernel has no native netdevice for either (EtherIP, protocol 97, and MikroTik EoIP are userspace in C-Tunnel: a TAP ⇄ raw-socket relay). They would need a new daemon, they are L2-only, GRE-TAP already gives an
  L2-over-GRE link for server-to-server use, and EtherIP (97) was measured **not** to pass on the route this work is for. If wanted later, the place to add them is a reviewed relay unit beside the carrier, like the Chisel carrier of `docs/carried-links.md`.
* **C-Tunnel's UDP-Crypt** (an AES-256-GCM UDP carrier shaped like DTLS / QUIC / RTP / WireGuard / DNS / NTP / OpenVPN). This is a brand-new encrypted user-space data plane with per-shape header mimicry; done wrong it is worse than not shipping (a
  nonce reuse breaks GCM), it cannot be proved on the route this work is for (every UDP shape is dead there), and the panel already has kernel WireGuard and IPsec (audited AES-GCM) for an encrypted UDP link. Its one novel part is DPI-shaping, which
  deserves its own careful review and a per-shape netns proof. Left as a follow-up with this note.
* **simple-tun-link's WireGuard over QUIC / WSS.** WireGuard over a TLS WebSocket on one TCP port is already the panel's `wgchisel` / `wgchiselfwd` (WireGuard inside a Chisel carrier - the same idea with a carrier the panel already installs). WireGuard
  over QUIC (Hysteria, optional Salamander obfuscation) needs the Hysteria binary as a new core and a UDP port, and UDP was dead on the route this work is for; not added.
* **SIT / IP6GRE (IPv6 underlay).** Both real servers have global IPv6, but IPv6 routing between two different providers is not proved, and the panel's links are IPv4 throughout. Left until a pair whose IPv6 path is known is at hand.
