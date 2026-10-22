# WireGuard in the panel (complete since 1.69.44)

WireGuard is a link model like the other eight (`wg:<transport>`: `wg:tcpmux`, `wg:tcp`, `wg:wsmux`, `wg:wssmux`; and the two carried ones, `wgchisel` / `wgchiselfwd`, see [`carried-links.md`](carried-links.md)).
It is the only model that needs a package on the servers (`wireguard-tools`: the `wg` command; the kernel module comes with Linux 5.6 or newer). Until 1.69.43 it was never really used: nothing put the tools on a server,
and the probe, the survey and the Doctor never install a package - so every probe said "unknown" and the night survey counted WireGuard as **blocked** on every pair.

## Who may install the tools - the rule

**Only the owner.** The Doctor, the link probe, the night survey and the repair of a cut tunnel never put a package on a server. What the owner does to ask for it:

| the owner presses | what happens |
| --- | --- |
| Settings -> About & update -> Tunnel cores -> **WireGuard tools -> Put on N servers** (`POST /api/engines/wireguard/install`) | `wg_install` on every enabled server that lacks the tools, four at a time, in the background; the card shows the state of every server and the result |
| a server's menu -> Install -> **WireGuard tools…** (`POST /api/servers/<id>/onboard` with `{"steps": ["wg"]}`) | the same, for that one server; the step is on its row while it runs |
| Create tunnel / Compare / Smart test / a survey started by hand, with a WireGuard type | the build installs the tools on the servers that lack them (the existing `install_wg` option), as before |

Adding a server never installs them (`onboard.STEPS` is unchanged; `wg` is an extra step, `ALL_STEPS`), and they are not a core of any tunnel type that a build installs by itself (a tunnel installs only its own core since 1.69.104): `apt` on a server inside
Iran can take minutes or be blocked, and it changes the packages of the server - that is the owner's call, with a button.

The install is `apt` only (the reviewed action `wg_install`; nothing is downloaded or copied from another machine). It says plainly what went wrong: no `apt-get` on the server, apt failed (its last lines), or the kernel
cannot load the module (a Linux older than 5.6 without the module package). After it, the module is loaded (`modprobe wireguard`) and checked.

## What every part says now

* **The cores box** reads three facts of every server (`engine_versions`: `WGTOOL`, `WGMOD`, `WGAPT`) and shows *installed / not installed / the kernel cannot load the module / no apt-get / unreachable* per server.
* **The link probe** (`linkprobe.plan`) no longer answers a bare "unknown": `wireguard-tools: installed on the Iran server, MISSING on the foreign server (...)`, with `skipped: "tools"` and `tools: {iran, foreign}`.
  The Doctor leaves out a link the probe could not try for this reason (a build would fail at once).
* **What the Doctor remembers** (`knowledge`): a kind that could not be built because a server lacks the tools is **"untested"** - not "failed", never in the blocked list, not kept out for 14 days. It is asked again after a day,
  and at once when the owner installs the tools on one of its servers (`knowledge.forget_tool_skips`). The Doctor page, Knowledge, shows "⚠ not tested: ... · tools missing" beside the pair. The link probe keeps the same
  mark for the link (`ok: null, skipped: "tools"`; `link_state` is unknown, not "does not pass").
* **The pair test, the survey and the fall-back walk** use WireGuard whenever the tools are there: the night survey builds `wg:tcpmux` like any kind; a build the owner starts from Create tunnel whose link does not pass
  is followed by WireGuard (then by the carried links) when `install_wg` was asked **or the tools and the module are already on both servers** (`builder._wg_ready`: nothing is installed).

## The link's own health

* **Keys**: each server makes its own private key (`wg_keygen`: umask 077, a folder of 0700, a file of 0600) and prints only the public half; the up script names the *file* (`private-key "$KEY"`), no key is in a command
  line, a script or the tunnel record (public keys only).
* **Handshake**: the health check reads the age of the last handshake (`WGHS` in `tunnel_side`, also in the quick look): older than 300 s (a link renews it about every two minutes while it is used) or none yet is added to
  the sentence of a link that loses packets or does not answer ("...; the last WireGuard handshake was 4000 s ago (the other end has not answered for that long)").
* **Restart on a stale handshake**: the check script of a WireGuard link (`builder.wg_check_script`, also for the carried links) pings the peer every minute and restarts the link unit after **three** missed checks - after
  **two** when the last handshake is older than 300 s (a link without a session does not come back by waiting).
* **MTU**: the packet inside is the path MTU minus what the link adds (`linkmodels.inner_mtu`: WireGuard 80 bytes, the worst case over IPv6; at most 1360, at least 1200). On a path of 1500 bytes nothing changes (1360); on a
  path of 1400 a WireGuard link now gets 1320 (it got 1360: the packets were cut in two).
* **Removal, rebuild, standby, move**: the key and the scripts are removed with the tunnel (`remove_tunnel_files`), a rebuild keeps the link, the port and the MTU, a standby over WireGuard without the tools fails at once
  and installs nothing.
* **Every transport builds**: `wg:tcpmux`, `wg:tcp`, `wg:wsmux`, `wg:wssmux` (the TLS ones also make the certificate) - tested against a faked pair of servers.
