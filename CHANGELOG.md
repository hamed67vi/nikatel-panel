# Changelog

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
