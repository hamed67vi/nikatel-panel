# Changelog

## 1.9.7 - 2026-10-10 - Where is the packet loss

* **The diagnosis places the loss**: for a lossy, flapping or down tunnel the panel measures packet loss from five sides at once (20 pings each): this Iran server toward this foreign server, toward other foreign servers, the internet and other Iran servers; this foreign server toward this Iran server and the internet; other Iran servers toward this foreign server. The verdict - *Iran side*, *foreign side*, *only this pair's route*, *both* or *none* - is part of the cause, listed in what was checked, and handed to the AI with every number. Rows and small cards grow a little under the pointer.

## 1.9.6 - 2026-10-10 - Severity colours

* Server and tunnel rows are **red** when down or critical (tunnel: down or 15 % loss or more; server: offline, no login, CPU/RAM 92 % or more, disk 95 % or more) and **yellow** when bearable (degraded or 5 % loss or more, slow, CPU 80 %, RAM/disk 85 %, not checked yet). Rows and cards fade up softly under the pointer.

## 1.9.5 - 2026-10-10 - Automatic repair on by default, with a switch

* The smart repair is **on by default** (level: everything up to another tunnel type). Smart repair → Needs attention has an **Automatic repair** switch: off, the panel only watches and shows Diagnose / Fix with AI to work by hand.

## 1.9.4 - 2026-10-10 - Change SSH password

* **Nodes → a server → Tools → Change SSH password**: sets a new login password for the server's SSH user over the panel's key, or generates a strong one and shows it once (the panel keeps no password). Payloads of actions (files, passwords, keys) are no longer written to the log.

## 1.9.3 - 2026-10-10 - One Check all button

* *Quality test all* and *Health all* are one button, **Check all**: every tunnel is checked for real (up / degraded / down, does data pass), then its packet loss and latency are measured (six at a time), and a list shows the worst first. A loss of 10 % or more is marked *users feel it*, 3 % or more *some*. It only shows - nothing is repaired because of it.

## 1.9.2 - 2026-10-10 - The Iran filter check no longer cries wolf

* A clean address showed *Filtered in Iran* because 3 of 21 locations lost a ping. A server is now filtered only when many locations fail: 40 % or more with half or more of the pings lost, or at least a quarter (and 3) fully dead; a few weak routes are reported as such on a clean result. The row shows *GRE ✕* instead of *GRE ✕ not OK*; the footer text is dimmer.

## 1.9.1 - 2026-10-10 - A wrong SSH password is not silent

* Adding a server with a wrong password now asks for the password again (three tries) with the reason, instead of leaving a server that never logs in. A server the panel cannot log in to shows *no login* with the reason under it and a *🔑 Password* button on its row until the key is installed. Also the default Marzban certificate for Add server (Install after adding), kept in the panel database.

## 1.9.0 - 2026-10-10 - Install after adding a server; menu and footer polish

* **Add server → Install after adding** (optional, needs root): *Block public torrents* (the public Heclalava/blockpublictorrent-iptables script: tracker list, iptables DROP rules, refreshed daily by cron; it is run once at the end so the rules are active at once) and *Marzban node* (Docker and the official Gozargah installer; paste the certificate the Marzban panel shows for a node; ports 62050 / 62051). The server is added first, then one story tells what each install did and, for the node, what to enter in Marzban. Both are reviewed actions (only base64 reaches the script) and run only when the panel is not read-only.
* **Menu text in 3D** (a gradient face with a clean extruded edge, tilt on hover, a light running over the letters), a faded picture of connected nodes behind the menu, a footer box with "Powered by" and the version, and a Sign out animation (the panel folds away in 3D with a light sweep).
* Temporary test tunnels are called *test-N* (they were nkt-N). The smart-repair list is a fixed box.

## 1.8.2 - 2026-10-10 - ArvanCloud failover switched off for now

* The ArvanCloud part (Failover · CDN tab, DNS changes box, DNS names in Create tunnel, the move of tunnels to another Iran server) is hidden and does nothing until the tunnels and the smart repair are tested further. Nothing was deleted: the code, the saved key and the settings stay (ARVAN_UI in the web app and ailover.ENABLED on the server bring it back). Smart repair has one tab again and says plainly when a route problem needs a human.

## 1.8.1 - 2026-10-10 - A flapping link is not green; DNS names after the build

* **A link that answers only now and then is shown degraded, not up.** Before, a GRE link that failed and recovered every few minutes turned the row green between two failures (two good checks were enough), while users saw cuts. Now a check that passes on a link that failed 3 of the last 10 checks gives "degraded: the link flaps", the diagnosis names it a route problem, and the automatic smart repair climbs for it (other tunnel types, then the failover) without waiting for restarts that cannot help.
* **The users' path decides the state.** Every minute, besides the request on the Iran server itself, one HTTP request is made to each tunnel's forwarded port from another Iran server (the probe server first; a server never looks at itself). A tunnel whose data passes is *up* even when the ping over the GRE link is blocked (some routes drop ICMP inside GRE while TCP passes - this showed "down · 100 % loss" with 10 Mb/s flowing); the latency then comes from that request. A port that answers on the server but not from Iran is *degraded*, after four minutes *down*, with "blocked in front of the server"; the diagnosis treats it as a cut route, so the failover is the remedy. The Score column left the tunnel list (the quality test stays in the Traffic cell's tooltip); the reason now sits under the state.
* **New repair step - the link again from scratch**: for a GRE link that is filtered or lossy, the smart repair removes the tunnel once and builds it again with a NEW inner network (the old /30 is excluded) and a new GRE key, then checks it; only after that come the other tunnel types and, last, the move to another Iran server.
* **Repairs ask nothing any more**: restart, rebuild and another tunnel type run by themselves (two minutes after the restarts were used up, again after 30 minutes if needed; a manual smart repair no longer blocks the automatic one for six hours); "Fix all with AI" starts at once. Only a move to another Iran server - with its CDN records - waits for approval under Failover · CDN.
* **DNS names after the build**: a tick in Create tunnel points the chosen names (A records of the ArvanCloud account, picked from a list) at the new Iran server as soon as the build finished; the same small form sits under the DNS changes box for any server. A ticked replacement also re-points the records of the replaced tunnel at once (the tick is the approval). Every change is listed with a Restore.
## 1.8.0 - 2026-10-10 - Create tunnel: smart Iran choice, port check, replace a dead tunnel

* **✨ Smart** in the Iran server list: the panel pings the foreign server from every Iran server, looks at free resources, how many tunnels each one carries and whether the ports are free, and builds on the best one (the story says which and why).
* **Port check before the build**: a port already used on the chosen Iran server - by a tunnel of the panel, or, checked for real when Build is pressed, by any process - stops the build with a clear sentence: choose another Iran server or port. Nothing is touched.
* **Replace a dead tunnel**: when another tunnel already carries the port to the same foreign server, the form says so (its Iran server, state, how long down); a dead one is ticked for replacement by default: it is removed first, the new one is built, and the CDN records that served only it follow the new Iran server through the failover (asks first, or automatic). Records that also serve a tunnel that stays are left alone and listed.
* **Fix**: the Create form sent an empty transport (the type list keeps it in memory, not in a field), so every backhaul build was refused with "transport must be tcp, tcpmux, wsmux or wssmux"; the form now sends the chosen transport, and an empty one means the engine's default.
## 1.7.1 - 2026-10-10 - Failover for a broken route, test cleanup, fixes

* **Failover for a broken route**: when the smart repair finds that GRE is filtered (or the route is cut / lossy / the port blocked) and no other tunnel type works, it hands over to the failover: the broken tunnels of that Iran server move to the Iran server that ranks best for them (ping to the foreign server, free resources, how many tunnels it already carries, free ports) and the CDN records that serve only those tunnels are pointed at the new server. A record that also serves a tunnel that stays would break it, so such tunnels are moved as well and the plan says so; records with no known config are left alone and listed. Mode "ask me first" keeps the plan on Smart repair → Failover · CDN (with a Telegram notice); "automatic" runs it inside the same repair story. The chain restart → rebuild → other types → failover runs by itself only when Settings → Repairs allows "switch".
* **Temporary test tunnels are always removed**: a forced name (a test, a rebuild under the same name) is kept by the engine builders; leftover units of an interrupted earlier run are removed before the port check instead of silently renaming the new tunnel (which left a "temporary" tunnel in the list and the test port in use). The test's cleanup also removes any tunnel of the pair that forwards only the test port.
* **Fixes**: the failover forgot to save that a server answered again (the next outage would have skipped the waiting time); the task-dock chips no longer jump every 1.5 s (they are updated in place) and show the text without the "·" marker. A fix that did nothing (refused by the hourly limit, nothing to repair, a server unreachable) no longer counts against the two-per-hour budget - before, the refusals kept each other alive, a tunnel that stayed down got an entry every minute and never a real restart again, and a manual Fix after one automatic one was refused; the automatic smart repair now climbs after two real restarts since the outage began, not only within the last hour.
## 1.7.0 - 2026-10-10 - Failover of an Iran server with ArvanCloud DNS

* **Failover**: an Iran server that stays down (default 5 minutes) gets a plan: all its tunnels move to the best other Iran server (same names, ports and types) and the ArvanCloud A records that pointed at the dead server are pointed at the new one. Only the address of a record changes; every other field is sent back as read, and the result is read back to be sure. The DNS is touched only after every tunnel moved.
* **Starts with approval**: the plan waits on Smart repair (Approve / Dismiss) and a Telegram notice is sent. Mode "automatic" exists but only runs clean plans. A finished failover can be undone (DNS back to the old address).
* **Smart repair → Failover · CDN tab**: connection status (the domains and records the key sees), a preview per Iran server ("what would happen if it went down"), the settings (API key kept on the server and never shown again, mode, minutes, optional domain list, a read-only key test) and a short guide to creating the key at ArvanCloud (machine user with the CDN access rule).
* **DNS changes box** next to the History: every record the failover re-pointed, with old and new address, which server, when, and a *Restore* per record that puts the previous address back (a whole failover can still be undone at once).
* **Pages in tabs**: Smart repair (Needs attention with the History and the DNS changes · Failover) and Tunnel test (the test with its live log · Network advisor, moved here from Smart repair).
* **Task dock**: a running window (a repair, a build, a test) can be minimised with — to a chip at the bottom; it keeps running, the chip shows how far it is, a click brings it back.
* **Clear text for 502 / 503 / 504** while the panel restarts after an update; the Messages list shows the whole text of each message.
* **Windows open with a spring animation**: blur clears, the box rises and settles, a light passes over it once.
## 1.6.0 - 2026-10-10 - Persian and English, glass-gold AI buttons, animated buttons

* **Two languages**: a *فا / EN* button in the header (and on the sign-in page) switches the whole panel between English and Persian, right to left with the Vazirmatn font. One dictionary (`i18n.fa.ui.js` for the interface, `i18n.fa.msg.js` for what the server says) translates everything that reaches the screen - events, repair and build stories, errors; curl, ssh and TLS errors are explained in plain Persian, cause first. The choice is remembered per browser.
* **Buttons**: the AI buttons (anything with ✨) are gold glass and a magic wand flicks in beside the text on hover; every other button, and the sidebar menu, gets a lift with a glow ring and a small white curtain passing over it on hover (nothing happens on click); the Create tunnel button is larger with a slow fade-breathing glow.
* **Branding**: the logo in the README and the sidebar signature *Powered by v2v2H*.
* **Add a config** takes only the link (the name comes from the link, the config goes into *All*).
## 1.5.1 - 2026-10-10 - quiet alerts, data centres on the dashboard, tidier windows

* **Alerts say it once**: a thing must be down for at least `min_down_minutes` (default 4) on top of the failed checks before one DOWN goes out; reminders are off by default (`remind_hours` 0 = never: one DOWN, one UP, nothing in between); a thing that goes down and up three times inside an hour gets one FLAPPING notice and is then kept quiet for 6 hours (Settings → Alerts: `flap_max`, `flap_quiet_hours`); configs have their own `config_fails`. Migration 10 adds the flapping columns.
* **Hosting providers**: found for every server from registry data (name, website, AS; once an hour). The server window's Provider row shows the site as a link and whether it answers (Iranian providers checked from an Iran server, foreign ones from the panel server, every 10 minutes). `GET /api/datacentres` groups servers and tunnels per provider. The dashboard's Arvan Radar box is gone.
* **Shop bot → Plans**: quick add (category, server, time, volume, price → a complete plan with automatic title and the server's panel settings), new-category button, consistent button sizes.
* **Branding**: a detailed SVG icon (favicon, sidebar, login). Docs: `docs/features.md`, `docs/shop-bot.md`, `docs/README.md` index.
* **Server window**: no header buttons while the edit form is open. **Settings**: each tab's card is as wide as its content.
* **Panels page**: Overview (nodes, about, test config) is the first tab; the user list and the hosts are read from the panel only when you open their tab, and kept until you reload (↻) or change the search.
* **Subscription editor**: the nodes come in categories (Countries, families like TCP-1…5 or Cloud-1…4, Other) with tick all / untick all per category.
* **Smart repair** lists tunnels only (down, degraded, lossy); configs and servers stay on the dashboard's Problems box, under Panels and under Nodes.
* **Server window → Tools**: Health check tile removed; every tile the same size with one short line; results (GRE, filtering, Abuse-Defender, last sample) only in *Last results*.
* **UI**: the message bar sits under the header (sticky) instead of the bottom; a polished sidebar (icon column, counts, glowing marker, hover fade); deleting one config asks nothing (Delete all still does); fixed: Settings tabs could show a stale card and two tab rows after a refresh (the page's own DOM changes were mistaken for an unchanged page).

## 1.5.0 - 2026-10-10 - the shop bot run from the panel, a Telegram Mini App, a smoother and mobile-ready UI

* **Shop bot pages** (Shop bot → Overview · Payments · Orders · Users · Plans · Categories · Servers · Discounts · Gift codes · Broadcast · Tickets · Texts · Settings · Tools): everything the bot's admin menu can do, from the panel. Payments to confirm (`have_sent`) with ✅ / ❌ that credit the wallet and edit the admins' messages exactly like the bot; orders with status; user detail with wallet, discount (the bot's JSON kept intact), agent, ban, message; plans with the panel settings JSON (`custom_sni`); categories; the panels the bot sells from (add one from the panels this panel already knows); discount codes with audience and target; gift codes; broadcasts (queue, cancel); tickets with reply and close; every text and button of the bot editable with overrides (`settings/text_overrides.json`, hooked into `config.php` once); every switch grouped (Sales, Wallet and payment, Prices, Support and channels, Users, Links and configs, Extras) and the payment keys; tools: apps list, top-up prices, backups, tables, read-only SQL, stats, webhook, command menu, restart PHP, dump, clear logs. All SQL goes through the reviewed scripts with text as base64 literals; the bot token never leaves the bot server (`bot_tg_json`).
* **Telegram Mini App** (`bot/miniapp/`, installed from Shop bot → Tools → Mini App or `scripts/deploy-miniapp.sh`): the customers' app inside Telegram — services with live usage from the panel, buying from the wallet with the account created on Marzban / PasarGuard / Marzneshin, subscription link, QR and configs, renew, extra volume and days, card-to-card top-up with the receipt photo sent to the admins with the bot's own approve / decline buttons, payment history and cashback, gift codes, lucky wheel, tickets, referral link, connection apps, outage reports. RTL Persian, follows the Telegram theme, deep links (`svc_<id>`, `plan_<id>`, `wallet`, `support`). Every write mirrors the bot's own rows, so the bot and the panel see what the app did.
* **Smart repair, rewritten**: one list of everything that needs attention (tunnels down or lossy, servers offline, configs down grouped by address) with *Fix with AI* on each, *Fix all with AI* for every broken tunnel in one story, *Explain* (the AI reads the whole situation), *Ask the AI* and *Check again* inside a diagnosis; the dashboard's Problems box has *Fix all*. Rebuilds interrupted by a restart resume on start (`builder.resume_pending`).
* **UI**: switching pages never shows the old page again — a loading box appears at once, the previous page's requests are cancelled, a failed load shows an error box and retries by itself (deploy restarts, network blips). Status bar instead of toasts; fixed-height log boxes everywhere (no jumping); hover fade on every button, row and card (not on boxes); compact tunnel-test engine tiles with *Advanced* for the transports; packet loss in red when it matters; More / Edit / Delete buttons on server and tunnel rows instead of row clicks; country codes instead of flags in config lists; Arvan Radar box loads after the dashboard and refreshes in the background (stale-while-revalidate) — the dashboard no longer waits for it.
* **Phones**: bottom icon navigation, one-column tiles, tables scroll inside the page, full-screen windows, the status bar above the nav.
* **Security**: `Content-Security-Policy` on every page; `/api/docs` and `/api/openapi.json` need a session; bot switches sanitised; user discount updates keep the bot's JSON; receipt uploads checked by size and MIME and never stored.

## 1.4.0 - 2026-10-10 - configs like the old panel, tidy settings

* Configs: group bar with chips (All / Manual / one chip per subscription group, with counts and a remove button that asks whether to delete the configs too), country flag, ONLINE / OFFLINE label, inline tunnel select and CDN port (Enter saves), download / upload columns, Edit / Test / Delete per row; grid cards with PING / DOWN / UP stat boxes; "Test all" and "Delete all" act on the current view. Sync of a group reuses the saved subscription link.
* Settings: eight clean tabs (Account, Alerts, Backups, Probe, Repairs & AI, Shop bot, Appearance, Update), one tab = its cards, in a fixed order.
* Gateway & outbounds in two tabs: Iran gateway (settings strip, ports, borrowed configs, exit test) and Panel outbounds.
* Sidebar: Sign out in its own box with the panel version under it.

## 1.3.0 - 2026-10-10 - the rest of the old tunnel tools

* Tunnel test like the old one: tick the transports to try (TCP Mux, TCP, WebSocket Mux, WebSocket TLS Mux); the panel builds a temporary tunnel for each, one after the other, measures latency / jitter / loss / download / score, removes it, and shows a comparison table with the best one. Stop ends after the current transport.
* Tunnel list: Health and Quality buttons per tunnel, "Quality test all", "Detect on servers" (tunnels that exist on the servers but not in the panel, one-click adopt), "Ports" to change the forwarded ports of a backhaul tunnel (the Iran side rewrites its config, restarts, checks every port listens, or puts the old file back).
* Create tunnel: "choose for me" ranks the Iran servers for the chosen foreign server (ping quality, free resources, tunnels carried, wanted ports free) and picks the best.
* Events, Jobs and Logs are no longer shown as tabs.

## 1.2.0 - 2026-10-10 - the old panel's structure

* Navigation like the old panel: Dashboard (infrastructure health, quick access, activity), Configs (configs + Panels in one place, stat tiles, Ping all / Speed test all / Delete all, grid / compact / list display), Nodes (Iran / Foreign with the monitoring view inside, Gateway, Events, Jobs, Logs), Tunnels (list / create / tunnel test / quality / core update / repairs), Shop bot, Settings (Account / Telegram & backup / Probe / Appearance & about / AI & automation). Tabs the size of the old ones.
* Every panel can keep a test config on the probe: a small test user is created on the panel and its subscription imported as the panel's group (automatically when a panel is added, or with one button).
* Tunnel quality: latency / jitter / loss / score over the GRE link of an existing tunnel, one or all.
* Core update: which backhaul / Xray version every server runs against the newest release; update outdated servers with a verified download.
* AI assistant: Anthropic key in Settings; inside Fix the AI reads the facts, gives a diagnosis and may add safe steps.
* Appearance: themes, day / night button in the header, text size, animations off. About: installed vs newest commit, one-click update from GitHub (deploy with rollback).

## 1.1.0 - 2026-10-10 - the old panel's node tools, new navigation, glass look

* Navigation like the old panel: Nodes (Iran / Foreign / Monitoring / Gateway & outbounds / Events / Jobs / Logs), Tunnels (list / create / repairs), Settings in tabs. Liquid-glass look, hover lift on rows and cards, tunnels grouped by type.
* Nodes: console with presets (logs, ports, failed services, busiest processes, disk, network, tunnel units); connect a server with its password once (installs the panel's key); add several servers at once; open a server in Windows ssh / PuTTY / MobaXterm with your own key; Abuse-Defender on foreign servers (install / remove, nightly list refresh); reboot; country of foreign servers by majority vote of four services; "filtered in Iran?" check from your Iran servers and check-host.net, manual and once a day.
* Facts now include swap, the busiest process and the Abuse-Defender rule count.
* The panel runs on the monitoring server behind Caddy (`NIKATEL_LISTEN_HOST=127.0.0.1`); `install.sh` installs sshpass.

## 1.0.0 - 2026-10-10 - the old panel is retired

* All milestones done; the new panel is the only one running. Telegram alerts moved over, auto-repair switched on (2 per tunnel per hour, Iran side first, never when a server is unreachable).
* Tunnel checks: an ssh connection that drops mid-run is retried once; a single failed check no longer turns a working tunnel "unknown" (two in a row do). ssh failures are logged with their output.
* Restarts no longer produce "cannot be checked" events: the service stops with `KillMode=mixed`, running jobs get up to 40 s to finish, and a check that fails while the panel is shutting down is dropped. `deploy.sh` installs the unit file when it changed.

## 0.10.0 - 2026-10-09 - Milestone 6, part 2: shop bot

* Shop bot page: the PHP bot on its own server, over SSH through reviewed scripts — services, cron, Telegram webhook, sales today / 7 d / 30 d, switches (BOT_STATES), users (wallet, discount, agent, ban), tables, a read-only SQL box, stats; actions: set webhook, restart PHP, dump the database, clear logs.
* One-click import of the real database from the staging copy (the live database is dumped first; broadcast queues are left out).
* Settings → Shop bot: where the bot runs and its public URL. Database credentials never leave the bot server.

## 0.9.0 - 2026-10-09 - Milestone 6, part 1: panels and outbounds

* Panels page: Marzban / PasarGuard panels checked every 2 minutes (version, users, load, nodes); a node that stays disconnected gets a reconnect request; alerts for panels and nodes with the usual flapping rules.
* Users: search, filter, paging; edit status / expiry / limit / reset / note; enable, disable, reset, renew, revoke, delete; bulk actions on ticked users.
* Hosts: list with the effective port, edit every field, enable / disable, clone, delete.
* Outbounds: the panel's Xray core config — add an outbound from a link, remove, make default, raw JSON edit; every change validated, backed up to `core-backups/`, read back and rolled back when the panel did not keep it; one-click restore of any backup.
* First start imports the panels from `/etc/nikatel/panels.json` (passwords stay in the database, never in the API).

## 0.8.1 - 2026-10-09 - quieter tunnel health

* The GRE peer check sends 20 pings instead of 5, so one lost packet is 5 %, not 20 %.
* Loss under 15 % is normal for the Iran-abroad paths (measured 3-13 % on a normal day) and no longer makes a tunnel "degraded".
* up ⇄ degraded flips need two consecutive checks that agree (`tunnels.settle`); a stopped unit still counts at once.

## 0.8.0 - 2026-10-09 - Milestone 5: configs and probe

* Configs page: the probe's configs with status, ping, daily speed, 24 h history, grouped by subscription; add a link; edit field by field (v2rayN-style) or paste a new link; delete; speed test on demand (over SSH on the probe server); copy link.
* Subscriptions: preview the nodes of a link, import the ticked ones as a group.
* Probe: primary + standby list with automatic failover; test; config alerts use the same flapping rules as tunnels.
* Probe installer: the probe sources now live in `probe/`; Settings → Probe installs them with cached Xray on any Iran server and registers it.

## 0.7.0 - 2026-10-09 - Milestone 4 complete: tunnel test

* "Test this pair first": builds a temporary backhaul-over-GRE tunnel, measures latency, jitter and loss over GRE and the real download speed through a forwarded port, gives a 0-100 score, and removes everything again. Results stay in the Repairs history.
* Gateway: borrowed mode blocks instead of leaking to direct before the first probe.

## 0.6.0 - 2026-10-09 - Milestone 4, part 2: Iran gateway

* Gateway page: a plain Xray on an Iran server, installed from the panel's cache over SSH (the server needs no internet); ports forward to foreign nodes; borrowed vless/vmess/trojan/ss configs as the way out during internet cuts, with modes auto (balancer + fallback) / borrowed / direct; exit test per config with IP and country; status, log, remove.
* `scripts/fetch-xray.sh` keeps the latest Xray in `/var/lib/nikatel/engines`.

## 0.5.0 - 2026-10-09 - Milestone 4, part 1: build tunnels

* Create a backhaul-over-GRE tunnel from the UI: preflight of both servers, free control port, path-MTU measurement, kernel tuning, GRE scripts + unit + health timer on both sides, two-way ping and a 10 s hold test, tuned backhaul configs, control-connection and data-path checks; a failed build removes what it created.
* Remove a tunnel from both servers (typed confirmation) and rebuild it with the same name and ports.
* New reviewed read-only scripts (`build_facts`, `mtu_probe`, `gre_verify`, `gre_hold`, `estab_check`, `http_probe`, `unit_log`) and actions (`put_file`, `unit_enable_restart`, `remove_tunnel_files`, `sysctl_tune`, `data_test_server`), all parameter-validated.

## 0.4.0 - 2026-10-09 - Milestone 3 (repair)

* Named actions (`ssh.ACTIONS`): the only way the panel changes a server; refused in read-only mode.
* Fix: diagnose both sides → plan → restart only what is broken, Iran first → re-check after each step; dry run shows the exact script; every run stored in `action_log` with its story.
* Limits: 2 repairs per tunnel per hour, never when a server is unreachable, auto-repair off by default and only in read-write mode.
* Repairs page and Fix / Dry run buttons in the tunnel detail; pre-commit hook (`scripts/install-hooks.sh`).

## 0.3.0 - 2026-10-09 - Milestone 2 (alert)

* Telegram: token and chat id in Settings (token never shown again), test message.
* Alerts with flapping control: N failed checks before DOWN, UP only after a stable period, reminders while down, per-tunnel/server mute, maintenance windows.
* Daily report at a local hour; preview and send-now.
* Encrypted backups (openssl aes-256, passphrase set once) kept locally and sent to Telegram; `nikatel restore <file>`.

## 0.2.0 - 2026-10-09 - Milestone 1 (see) complete except the one-week comparison

* Servers: add / edit / disable / remove from the UI; "forget host key" after a reinstall; detail row with host facts.
* 24-hour charts: CPU, RAM, traffic, load, connections per server; up/down strip, RTT and throughput per tunnel.
* Settings page: change password, effective settings (read-only mode stays server-side on purpose).
* Logs page: the panel's own log with level / domain / text filters, request ids, secrets masked.

## 0.1.0 - 2026-10-09 - Milestone 0 and the first half of Milestone 1

* Project skeleton: settings, logging with request ids and secret masking, SQLite with
  migrations, models, SSH with named read-only scripts, background jobs with status, one-admin
  auth, FastAPI routers, plain-JS UI.
* Import of the old panel's inventory (servers, tunnels); secrets kept in a separate column.
* Server facts every 2 minutes; tunnel health every minute with a reason per state.
* One-line installer, deploy with health check and rollback, nightly backup script.
* CI: tests on 3.10 and 3.12, ruff, JS/shell syntax, secrets check, gitleaks.
* Docs: architecture, roadmap, operations, development, logging, ADR 0001-0004.
