# Roadmap

The old panel keeps running untouched. The new one grows beside it and takes a job over only
when it does that job better. The one thing that must never happen: a tunnel users depend on
goes down because of the panel.

Each milestone = GitHub milestone; each line = one issue = one PR with tests (`[x]` done, `[~]` carried by another line). Branch `main` is
always installable; a milestone ends with a tag.

## M0 · Foundation
- [x] #1 Project skeleton: config, log, db, models, ssh, jobs, auth, api, services, web, tests
- [x] #2 CI: tests on 3.10/3.12, ruff, JS syntax, shell syntax, secrets check, gitleaks
- [x] #3 One-line installer (`install.sh`): questions or flags, dependencies, service, TLS (self-signed / Let's Encrypt), upgrade on re-run
- [x] #4 `deploy.sh` with health check and rollback; `backup.sh`
- [x] #5 Docs: README, ARCHITECTURE, operations, development, logging, ADRs, issue/PR templates
- [x] #6 Pre-commit hook that runs the secrets check locally

## M1 · See (read-only)
- [x] #7 Import of the old inventory (servers, tunnels), idempotent, secrets kept apart
- [x] #8 Server facts every 2 min; 24 h history endpoint
- [x] #9 Tunnel health every 1 min: units both sides, GRE interface, peer ping, forwarded ports → up / degraded / down / unknown with a reason
- [x] #10 UI: Dashboard, Servers, Tunnels (detail row), Events, Jobs
- [x] #11 Charts: CPU/RAM/traffic per server, RTT/throughput per tunnel (24 h)
- [x] #12 Add / edit / disable a server from the UI; "forget host key"
- [x] #13 Settings page: change password, intervals, read-only switch (read-only can only be turned off from the server, not the UI)
- [x] #14 Log viewer in the UI (last 500 lines, filter by domain / level / rid)
- [x] #15 **Exit criterion**: one week beside the old panel; same state for every tunnel in both (shortened by decision of the operator on 2026-10-09: both panels agreed on every tunnel during the day they ran side by side)

## M2 · Alert
- [x] #16 Telegram: bot token + chat, test message
- [x] #17 Alert rules with flapping control (N failed checks, UP only after stable), per tunnel/server mute
- [x] #18 Daily report; maintenance windows
- [x] #19 Encrypted backup to Telegram + one-command restore

## M3 · Repair (first time the panel changes servers)
- [x] #20 Action framework: named, logged, dry-run, rate-limited (`read_only` off needed)
- [x] #21 Actions: restart unit, restart GRE (rebuild moves to M4 #23, it needs the tunnel builder)
- [x] #22 "Fix" button with a live story; auto-repair with limits (max 2/hour, never when a server is down, off by default)

## M4 · Build
- [x] #23 Create tunnel (backhaul over GRE), with port allocation, MTU measurement, hold test, rollback; remove; rebuild
- [x] #24 Tunnel test between two servers (temporary backhaul-over-GRE tunnel, latency / jitter / loss / speed, removed again)
- [x] #25 Iran gateway: Xray pushed over SSH, ports → node, borrowed configs with automatic fallback

## M5 · Configs & probe
- [x] #26 Probe API client, configs list / health / speed, history, alerts for configs
- [x] #27 Add / edit configs (v2rayN-style editor), subscriptions import
- [x] #28 Probe installer (probe/ in this repo + cached Xray) and standby probes with automatic failover

## M6 · Panels & bot
- [x] #29 Marzban / PasarGuard: status, nodes (auto reconnect), users, hosts; alerts for panels and nodes
- [x] #30 Core outbounds manager with validation, backup/restore and automatic rollback
- [x] #31 Shop bot: status, switches, users, tables, read-only SQL, backups, import of the real database
- [x] #32 Retire the old panel (service stopped and disabled on 2026-10-10, files kept); repository private; Telegram alerts moved; auto-repair on

## M7 · Parity with the old panel (everything it could do, in the new structure)
- [x] #33 Navigation like the old panel: Nodes (Iran / Foreign / Monitoring / Gateway / Events / Jobs / Logs), Tunnels (list / create / repairs), Settings tabs; liquid-glass look
- [x] #34 Node tools: console with presets, connect with a password, add several servers at once, open in ssh / PuTTY / MobaXterm with your own key, Abuse-Defender, reboot, country lookup, "filtered in Iran?" check (manual + daily)
- [x] #35 Tunnel tools: quality per tunnel and for all, pair test over several transports with comparison, edit forwarded ports, detect tunnels already on the servers, automatic choice of the Iran server
- [x] #36 Engines: check / update the tunnel cores (backhaul) and Xray on every server from the panel (Tunnel → Core update)
- [x] #37 AI: Anthropic key in Settings, AI advice and safe steps inside Fix
- [x] #38 Appearance: themes (Aurora glass, Gold luxury, Cyber neon, Minimal), day / night, text size; About: version and one-click update from GitHub
- [x] #39 Dashboard like the old one: infrastructure health rows, quick access; Configs page with stat tiles, bulk actions and grid / compact / list display; Panels inside Configs with a test config per panel on the probe
- [x] #40 Configs like the old panel (group bar, inline tunnel / CDN port, flags, stat cards) and Settings in eight tidy tabs

## M8 · The other tunnel engines and the last details
- [x] #41 Create tunnel: BackPack (reverse, all transports), BackPack Direct (udp / quic / pck / sni / xdi), Paqet, Chisel - ported from the old panel, bugs fixed, remove / rebuild per type
- [x] #42 Tunnel test over every engine, grouped (Backhaul / BackPack / BackPack Direct / Paqet / Chisel), with cleanup
- [x] #43 Real test of every tunnel type on a pair without users; fix what breaks (2026-10-12: all 14 kinds built, measured and removed on one pair; the scores are in the changelog of 1.19.0)
- [x] #44 Shop bot: the panel's own bot pages, fully customised (payments, orders, users, plans, categories, servers, discounts, gifts, broadcast, tickets, texts, settings, tools)
- [ ] #45 Language switch (English / Persian)
- [x] #46 Telegram Mini App for the bot's customers (`bot/miniapp/`): services, buying from the wallet, top-up with receipt, renew / extra volume / days, tickets, referral, apps, wheel; installed from the panel
- [x] #47 Smart repair rewritten: one attention list, Fix all with AI, Explain; the dashboard's Problems box
- [x] #48 UI robustness: page switches never show the old page, automatic retry, status bar, fixed log boxes, hover fade, phone layout; CSP and session-only API docs
- [x] #49 Import the real bot database (the operator provides it) and run the bot pages + Mini App against it (2026-10-12: imported into a test copy of the bot; its scheduled jobs stay off until the operator starts them from the cron window)

## M9 · Selling it and keeping it light
- [x] #50 Shop bot pages rebuilt from their designs: one list engine (whole rows, numbered pagers, no scroll, no jump), windows in one style, settings on one page, the cron window, inbound picking with "apply to the accounts already bought"
- [x] #51 Tunnel Doctor hardening: a rebuild that puts the old tunnel back when the new build fails, a new GRE link tested beside the tunnel before a relink, no crash on a tunnel removed meanwhile; chaos tests on the pair without users (unit stopped, GRE link deleted, GRE filtered)
- [x] #52 DNS · IP switch under Tunnels: ArvanCloud records with Point and Restore, the failover on again (only with a key), a server's new address followed by its tunnels and records
- [x] #53 License tiers (owner / licensed / free) with signed tokens and the gates in the code; `docs/licensing.md`
- [ ] #54 License server and AI proxy on the vendor's side (token issue, metered AI credits, releases, revocation)
- [x] #55 Mini App in the panel's glass look (pill buttons, glass cards, floating tab bar, hero balance)
- [x] #56 Phone layout of the shop bot lists: rows become cards, no sideways scroll
- [~] #57 Split `app.js` / `app.css` into one file per page (same order, no build step) - carried by #65
- [~] #58 Language switch for the strings still in English only (#45 continued) - carried by #67
- [~] #59 "Mark receipts handled" and an AI desk for the shop bot - parked, see #68 and #73

## M10 · Stable and API-first (the way forward - no new features; see docs/engineering-plan.md)
- [x] #60 API contract: one error shape (`ok`, `error`, `code`) from one handler on every route, one long-work pattern (202 + action id + /api/actions/{id}) on every slow route, versioning policy written down (`/api` stays v1), `docs/api.md`
- [x] #61 Machine access: API tokens with scopes (read / act / admin) made in Settings, shown once, hashed at rest, revocable, every use logged; the browser keeps the session cookie, every bot or AI uses a token
- [x] #62 The OpenAPI document as the manual: a one-sentence description and typed request / response models on all 281 routes; a CI check that fails on a route without a description
- [x] #63 Test depth: coverage measured and shown in CI, the pure helpers of every service covered, the SSH layer behind one fake; install on a clean container in CI; upgrade from the previous tag with data intact
- [ ] #64 Operations: `/api/health/detail` says what is wrong (database, late job, SSH key, disk, backup, errors) and the dashboard shows it; every warning and error is read as panel / your server / outside (Logs summary); still open: self-check on start, structured logs with the request id everywhere, backup-restore and release-rollback rehearsed and documented with timings
- [ ] #65 Front end as a pure client: split one file per page (#57), every JS file syntax-checked in CI, an audit of every `api()` call against the documented routes, no logic in the UI the API does not offer
- [ ] #66 Pilot with ten operators: install from the GitHub release by the README alone, a "Report a problem" page (version, last log lines without secrets, health), one month of fixes only, exit when a week passes without a new bug
- [ ] #67 Persian finished (#45, #58) with an i18n coverage test: every key in both languages, no unused key, right-to-left checked on every page

## M11 · Parked ideas (written down so the design leaves room; not built until M10 is done)
- [ ] #68 Telegram command bot and AI desk that drive the panel over its API with a token ("check this tunnel", "repair", "report"): depends on #61
- [ ] #69 Outbound events: signed webhooks and an event stream, so external tools react to what the panel sees
- [x] #70 Tunnel-move load balancing in the Tunnel Doctor: a suggestion in mode fix, an automatic move of the busiest tunnel of an exhausted server in mode move (1.36.0)
- [ ] #71 Move or copy users between the two panel types with a dry-run plan, node pause during bulk writes, the subscription token kept, a result report; designed from a migration tool studied on 2026-10-12 and written from scratch (its licence does not allow copying)
- [ ] #72 PasarGuard API details the panel does not use yet (subscription URL only with `load_sub`, named cores, `proxy_settings`, ISO dates, 403 for a non-sudo admin, pages of 500)
- [ ] #73 Shop bot admin menu redesign: grouped menus (users, sales, support, settings), a pending-receipts list, quick user search
- [ ] #74 Public demo behind HTTPS through a reverse proxy on the panel server (the demo mode exists: `docs/demo.md`)
- [ ] #75 Server cost view: a price per server entered once; the Analyst says what each idle or redundant server costs per month
- [ ] #76 Verified self-update: release archives with checksums, the update checks the checksum and rolls the binary back by itself
- [ ] #77 Metrics endpoint for operators who run Grafana (servers, tunnels, configs, jobs as gauges)
- [ ] #78 Central Abuse-Defender: one rule list kept on the panel server, pushed to the nodes as an atomic ipset / nftables set, with every tunnel (any engine: its peers and ports) and every Iran server excluded by construction, and a check of the tunnels before and after each push; the per-node install that took minutes becomes a push of seconds
- [ ] #79 Shop bot section of the panel: its name stays in the menu but it is switched off until the other sections are finished; then switch it on again and finish it (its pages and the Mini App links are untouched in the code)
- [ ] #80 CDN: more record types and editing them (AAAA, CNAME and TXT can be made and deleted since 1.35.24; still open: edit them in place, ANAME, NS, MX, SRV, and a test against the real APIs) - a type chooser beside the name in the record form, and make / edit / delete / restore for each type on ArvanCloud and Cloudflare (today every DNS path of the panel - edit, delete, restore, the failover - is built for A records only; each type needs its own body format and a test against the real API)

## M12 · One account, many panels (API-driven; decided 2026-10-13, see docs/unified-core.md; the goal: this panel is the most complete management panel on top of the panels' APIs)

- [x] #81 One account over many panels (1.37.0): an account = its users on each Marzban / PasarGuard panel, linked by username or by hand, a Users page that searches every panel at once (read through the panels' REST API, nothing is copied out of their databases)
- [x] #82 One action for every panel (1.40.0): create a user on several panels at once (same name, limit, end; a panel with several groups needs the group chosen), and enable / disable / add days / set the limit / reset the usage / revoke the link / delete on the user of every panel of an account, with a result per panel (a panel that is down does not stop the others); `POST /api/accounts/create` and `/api/accounts/action`, the "＋ New account" form and the "On every panel…" menu of the account window
- [x] #83 One subscription link (1.40.0): `/sub/<token>` merges the subscriptions of the account's panels (the v2ray base64 list; the token is signed, nothing is stored, "reset all links" cancels every link made). Still open: Clash / sing-box formats; WireGuard comes through a PasarGuard group that has a WireGuard inbound
- [ ] #84 One API for the shop bot and the Mini App: they talk to this panel (one base URL) and it forwards to the right panel by the plan. The pieces exist since 1.40.0 (an API token can call create / action / merged link); still open and the owner's decision: how a shop plan maps to panels, groups and inbounds, and when the bot and the Mini App switch their base URL
- [x] #85 Usage and expiry of an account summed over its panels (1.40.0): used of the sum of the limits (no sum when one panel has no limit) and the earliest end, in the Users page and the account window
- [x] #93 TLS copies of the plain WebSocket hosts (1.41.0): in Panels → Hosts, tick hosts → "TLS copies on 443": each gets a copy with port 443, security tls, SNI = its Host header, alpn http/1.1, fingerprint chrome; a real test from the panel server (TCP, TLS with the SNI, WebSocket upgrade) decides whether the copy is on; the originals are untouched, the hosts are saved to a file first, the copies can be removed. Still open: the same test from a server inside Iran (the filter and the SNI), and turning HTTPS on at ArvanCloud from the panel
- [x] #94 PasarGuard from one place (1.42.0): the Panels page of a PasarGuard panel has the tabs Users, Hosts, Groups, Templates, Nodes (beside the servers of this panel; "make this server a node"), Core (the Xray config editor: dry run, backup, read-back, rollback), Admins, API keys, Settings (7 sections) and Usage (counters, by node and Tehran day, Jalali dates; Marzban too). Every tab is a route first, `/api/panels/{id}/pg/…` (docs/api.md). Still open: the install of a node tried for real (it was read from the official script, not run), the settings writes tried on a real panel (fake + the real models only), and the rest of PasarGuard's API: client templates, HWID devices, admin roles, the Reality scan, the node's usage charts; Marzban keeps only Users, Hosts, nodes and Usage (its API has no groups, keys or settings)
- [ ] #95 The Doctor's move: no error after the old tunnel is removed. Measured (2026-10-14): 30 s of errors right after the removal, 7 minutes after the DNS change - the CDN edge keeps the old origin address longer than the TTL. To decide with more measurements: a longer grace for ArvanCloud, or removing the old tunnel only when no new connection has reached it for a while
- [x] #96 A panel is connected by an API key or an access token, not by a password (1.42.0): PasarGuard 5.x has API keys (`pg_key_…`, header `X-Api-Key`, own end date, revocable), Marzban only the token; "Sign in once" gets a token and forgets the password; the page shows what is kept, who the key acts as and when it ends, warns before it ends, replaces it (tried first) and forgets a saved login (refused for a 24 h token); old panels keep their login until the owner decides. docs/panels.md
- [ ] #97 PasarGuard: make the API key by itself. "Sign in once" on a PasarGuard panel could make a key named after this panel (`POST /api/api_key`), keep only the key and drop the sign-in token, and a login-only panel could be switched to a key with one click. Not built: it writes a new key on the panel, so it waits for the owner's word (M11 idea)
- [x] #98 The Configs section is switched off in the menu and not shown at all (1.43.0), until the project grows: one flag, `CONFIGS_UI` in `web/app.js`, brings it back (the pages, the API and the tests stay); the Panels section is called **Users**, and the dashboard shows no configs while the flag is off
- [x] #99 PasarGuard, every section of the API (1.43.0): admin roles, client templates, the System tab, node bulk actions / usage / latency, cores (make, duplicate, delete, an inbound from a form with REALITY keys), the whole user (on hold, devices, next plan, owner, devices and subscription fetches), many users (shift end dates and data limits after a count, clean-up, users from a template), every field of a host with the advanced settings checked, and the Add server form with "also a PasarGuard node"; the route matrix is in `docs/pasarguard-coverage.md`. The rest was closed in 1.46.0: the bulk admin actions, the users-over-time and subscription-app charts (Panel → Usage), a user's subscription per client format (the user window), the shadowsocks method of many users, the bulk delete of cores and client templates - every route that was left has a button now (what is left out is on purpose and listed there)
- [ ] #100 PasarGuard node installed for real from the Add server form: tried only as far as a machine that already runs a Marzban node allowed (its port was taken, nothing changed); needs a spare machine
- [x] #101 The whole panel on a phone and on a narrow screen (1.44.0, the last job of 2026-10-14): boxes shrink with the screen, rows stay rows, chips and tabs never wrap, tables keep their columns with an ellipsis and drop the less important ones by the width of their box (`web/fluid.js`), a window header goes on a second row instead of lying over its name, and the page **locks** at 560 px on a desktop window (the owner's idea: it scrolls sideways instead of laying texts over each other). `web/early.js` picks the layout, `web/fluid.css` is the desktop and the tablet, `web/mobile2.css` the phone; the old card layout is `web/mobile.css` (`?fluid=0`). Every page and window was measured at 375 / 560 / 600 / 700 / 820 / 1000 / 1280 px
- [ ] #102 PasarGuard inside the panel's own structure (the owner, 2026-10-14): the sections of PasarGuard are not ten tabs of one page but part of the panel. Done in 1.45.0: a PasarGuard group in the left menu (Users, Hosts, Access = groups + templates, Core, Admins, Panel = usage + system + settings, each with the bar of the panels on top), the nodes as a tab of Servers (Foreign · Iran · Nodes), and a line for the nodes of a server in its window. Still open: all the node actions in the server window itself (restart, disable, usage, certificate, now one click away in the Nodes tab), **Hosts** next to the DNS records they point at, **Usage and System** as cards of the Dashboard, the panel's own settings inside our Settings, ONE panel selector that is not drawn on every page
## M13 · Outbound (when Iran is cut off from the world; decided 2026-10-13)

- [x] #86 The Outbound section from scratch (1.38.0): a pool of exit configs (vless / vmess / trojan / ss, up to 30, pasted many at once), the mode (spread = the load is shared by every alive exit, fast, auto = direct first), the Iran servers that carry it, forwards to the foreign nodes (made by hand or for chosen nodes x ports of the panels), applied to the servers through the Xray gateway, the pool measured from each Iran server every 3 minutes with a Telegram notice when it degrades or goes down. Tested end to end on a real Iran server with two real exits (spread 15 / 15, a dead exit skipped, no connection lost)
- [x] #87 Connect the users (1.39.0): the ArvanCloud records that lead to a tunnel get the forward's port as their origin port - one click or by the panel itself (manual / automatic), put back with one click; every change in the DNS log. (Not done: records of Cloudflare accounts - they cannot have an origin port - and records with several Iran addresses.)
- [x] #88 The relay behind the exits (1.39.0): a Shadowsocks relay on a foreign server so that an exit cannot read or re-route the traffic (without it a user whose Host / SNI is a name breaks). Still open: new tunnels whose Iran side dials the foreign server through the pool, and a check of which direction works per foreign server
- [x] #90 Outbound on one page (1.40.0): three steps - configs; tunnels (Iran servers x foreign servers x ports, made through the configs, with the relay picked by itself); the `Iran address : port` list to put behind the CDN by hand. Faster (the page and the records). The records of the CDN are never changed by the panel itself for now
- [ ] #92 Outbound section of the panel (Tunnels → Outbound): it is finished as a page (1.40.3) but its tab is the last one of Tunnels and switched off, like the Shop bot, until the important work is done; then take the "off" mark away in `SUB.tunnels` (web/app.js) and use it for real: configs, Iran x foreign x ports, Start, the addresses behind the CDN, a test on the live servers
- [ ] #91 Turn the automatic switch of CDN records back on (`AUTO_USERS` in services/outbound.py; its mode, job and tests exist) once its tests are finished: a real tunnel that breaks and comes back, with several records, with the DNS provider slow or refusing, and with Cloudflare records
- [ ] #89 Outbound alerts and history: how much each exit carried, when it died and came back, a warning before an exit's data runs out
## Rules
* Commit message: `<area>: <imperative sentence>` (`tunnels: treat lossy GRE as degraded`).
* Nothing identifying the real infrastructure in the repository - ever. `scripts/check_no_secrets.sh`.
* A phase that changes servers starts with a dry-run on a test pair and a written rollback.
