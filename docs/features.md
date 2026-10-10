# Features

Everything the panel does, by area. Where a feature has its own page in the UI the name is in **bold**.
(The panel's interface is English; the shop bot and its Mini App are Persian.)

## 1. Dashboard
| | |
|---|---|
| Infrastructure health | tunnels up / total, SSH-connected nodes, configs answering on the probe, servers reachable from Iran, last filter checks, background jobs |
| Problems | only what is wrong *now*, read from the live state (a solved problem leaves by itself): tunnels down / degraded, servers offline or overloaded, configs down, filtered servers, failing or stale jobs. *Fix with AI* on each, *Fix all* for every broken tunnel |
| Activity | the event feed with categories (Tunnels, Configs, Servers, Alerts, …) and a "problems only" filter; click an event for details |
| Message bar | the panel's own messages in one bar under the header (click for the last ones) |

## 2. Nodes (servers)
* **Iran / Foreign lists** with the monitoring view inside: CPU, RAM, swap, disk, load, traffic, connections, busiest process, Abuse-Defender rules; sampled every 2 minutes over SSH; 24 h charts.
* **Server window** (More): Overview (system, resources, access, *Provider* with its website link and a site check, location and security, tunnels on it) · Charts 24 h · Tools · Console.
* **Tools**: GRE test, console with presets, connect with the password once (installs the panel's key), forget host key, "filtered in Iran?" check (your Iran servers + check-host.net), Abuse-Defender install / remove, open in Windows ssh / PuTTY / MobaXterm with your own key, reboot.
* Add one server or many at once; edit, disable, delete (nothing changes on the server); country of foreign servers by majority vote of four services; hosting provider (name, website, AS) from registry data.
* **Gateway & outbounds**: a plain Xray pushed to an Iran server over SSH (ports forwarded to foreign nodes, borrowed vless / vmess / trojan / ss configs as the way out during internet cuts, modes auto / borrowed / direct, exit test per config) and the Xray outbounds of every panel with backup, validation and automatic rollback.

## 3. Tunnels
* **Tunnel manager**: health every minute (units on both sides, GRE interface, 20 pings, forwarded ports, one HTTP request through the tunnel) with a reason per state and hysteresis against flapping; More window with quality, ports, rebuild, history, remove; detect tunnels already on the servers and adopt them.
* **Create**: backhaul-over-GRE (preflight of both servers, free control port, path-MTU measurement, kernel tuning, hold test, rollback on failure), plus BackPack, BackPack Direct, Paqet and Chisel engines; "choose for me" ranks the Iran servers for the foreign one.
* **Tunnel test**, in two tabs. *Tunnel test*: builds a temporary tunnel per chosen transport, measures latency / jitter / loss / download, compares, removes it; Advanced lists the transports; the live log stays on the page. *Network advisor*: for an existing tunnel, tests every type between its two servers and says which is best today; "Use" replaces the tunnel with that type (same name and ports, the old type comes back if the new one fails).
* **Core update**: backhaul / Xray version on every server against the newest release, verified download.
* **Smart repair** (tunnels only), in two tabs. *Needs attention*: the tunnels that are down, degraded or lossy; *Diagnose* (both sides, the route, GRE, control port, data path), *Fix with AI* (restart → rebuild → another type, tells you when only a move helps), *Fix all with AI*, *Explain with AI*; under it the History of every repair and, next to it, the **DNS changes** box. *Failover · CDN*: when an Iran server stays down (default 5 minutes) its tunnels move to the best other Iran server (same names, ports and types) and the ArvanCloud A records that pointed at it are pointed at the new one - only after every tunnel moved, only the address of a record changes, and the result is read back. Starts in "ask me first" (Approve / Dismiss on the page, a Telegram notice); "automatic" runs clean plans only. Every re-pointed record is listed with a *Restore* that puts the previous address back; a whole failover can be undone at once. Auto-repair with limits (2 per tunnel per hour, never when a server is unreachable).

## 4. Panels (configs and Marzban / PasarGuard)
* **Configs**: the probe's configs with status, ping (green < 800 ms, yellow 800-999, red above), daily speed, 24 h history; groups per subscription with chips; inline tunnel and CDN port; v2rayN-style editor; subscriptions import / sync / edit (nodes in categories, untick = never comes back); Ping all, Speed test all, Delete all; single delete without a question; direct configs are judged by TCP reachability from Iran, CDN-fronted ones by name (SNI) too.
* **Panels**: Overview (nodes with reconnect and log, about, test config) · Users (search, status filter, edit, bulk disable / enable / renew) · Hosts (edit, clone, disable). Users and hosts are read from the panel only when their tab opens. A small test user per panel keeps a test config alive on the probe.

## 5. Shop bot
The PHP bot on its own server, run from the panel (see [`shop-bot.md`](shop-bot.md)): Overview · Payments (to confirm → ✅ / ❌ like the bot) · Orders · Users · Plans (quick add: category, server, time, volume, price) · Categories · Servers (the panels it sells from) · Discounts · Gift codes · Broadcast · Tickets · Texts and buttons · Settings (switches, payment keys) · Tools (**Mini App**, backups, apps list, top-up prices, tables, SQL, stats, webhook, command menu, restart PHP, dump, clear logs, import the real database).

## 6. Telegram Mini App
The customers' app inside Telegram, installed from the panel: services with live usage, buying from the wallet, renew, extra volume and days, card-to-card top-up with the receipt sent to the admins, history and cashback, gift codes, lucky wheel, tickets, referral, apps, outage reports. Details in [`../bot/miniapp/README.md`](../bot/miniapp/README.md).

## 7. Alerts, reports, backups
* **Alerts** (Telegram): one DOWN after N failed checks *and* a minimum down time (default 4 min), one UP after a stable period, reminders optional (off by default), a FLAPPING notice then quiet for hours when something goes down and up repeatedly; mute per item; maintenance windows.
* **Daily report** at a chosen hour; preview and send now.
* **Encrypted backups** (aes-256, passphrase set once) kept locally and sent to Telegram; one-command restore.

## 8. Settings
Account (password, two-step sign-in with recovery codes, reset by Telegram code) · Alerts · Backups · Probe (primary + standby with failover, installer for any Iran server) · Repairs & AI (auto-repair level and delay, Anthropic key) · Shop bot (where it runs) · Appearance (themes, day / night, text size, animations) · Update (one click from GitHub with rollback, tunnel cores).

## 9. Interface
Plain JS, no build step; hover fade on every button and row; a loading box and automatic retry so a page switch never shows the old page; fixed-height log boxes; phone layout with a bottom bar; the installer, the updater and the docs are in the repository.
