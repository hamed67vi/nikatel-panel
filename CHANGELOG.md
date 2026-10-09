# Changelog

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
