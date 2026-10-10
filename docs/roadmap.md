# Roadmap

The old panel keeps running untouched. The new one grows beside it and takes a job over only
when it does that job better. The one thing that must never happen: a tunnel users depend on
goes down because of the panel.

Each milestone = GitHub milestone; each line = one issue = one PR with tests. Branch `main` is
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
- [ ] #43 Real test of every tunnel type on a pair without users; fix what breaks
- [x] #44 Shop bot: the panel's own bot pages, fully customised (payments, orders, users, plans, categories, servers, discounts, gifts, broadcast, tickets, texts, settings, tools)
- [ ] #45 Language switch (English / Persian)
- [x] #46 Telegram Mini App for the bot's customers (`bot/miniapp/`): services, buying from the wallet, top-up with receipt, renew / extra volume / days, tickets, referral, apps, wheel; installed from the panel
- [x] #47 Smart repair rewritten: one attention list, Fix all with AI, Explain; the dashboard's Problems box
- [x] #48 UI robustness: page switches never show the old page, automatic retry, status bar, fixed log boxes, hover fade, phone layout; CSP and session-only API docs
- [ ] #49 Import the real bot database (the operator provides it) and run the bot pages + Mini App against it

## Rules
* Commit message: `<area>: <imperative sentence>` (`tunnels: treat lossy GRE as degraded`).
* Nothing identifying the real infrastructure in the repository - ever. `scripts/check_no_secrets.sh`.
* A phase that changes servers starts with a dry-run on a test pair and a written rollback.
