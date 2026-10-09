# Architecture

## 1. Landscape

```
 browser ── HTTPS ──► panel (FastAPI + SQLite) ── SSH (own key, reviewed read-only scripts) ──► Iran servers
 Telegram ◄── alerts ─┘       │                                                               foreign servers
                              └── HTTPS ──► Marzban / PasarGuard panels   ── HTTP ──► probe
```

One Python process. SQLite for state. SSH is the only way the panel touches a server, and in
read-only mode only reviewed scripts run. The tunnels themselves are systemd services on the
servers; the panel observes and (later) repairs them, it never carries traffic.

## 2. Code map

```
nikatel/
  config.py        Settings - the only place that reads the environment (NIKATEL_*)
  log.py           log format, request id, secret masking
  db.py            engine, sessions, numbered migrations (MIGRATIONS, _add_column)
  models.py        tables: Server (+extra), ServerSample, Tunnel, TunnelCheck, Event, Job, AlertState,
                   Maintenance, Config, ConfigSample, Subscription, Panel, Action
  ssh.py           Node, run_script(name, params) over the reviewed SCRIPTS and ACTIONS; run_raw only read-write
  auth.py          one admin, scrypt, signed cookie
  jobs.py          background loops with their status in the jobs table; stop_all waits for in-flight SSH
  services/        the logic, importable without HTTP
    facts.py         server resources (parse_facts is pure)
    tunnels.py       tunnel health with hysteresis (plan_for / parse_side / judge / settle are pure)
    builder.py       create / remove / rebuild a backhaul-over-GRE tunnel; set_ports
    detect.py        find tunnels already running on the servers and adopt them; auto choice of the Iran side
    tunnel_test.py   quality of one / all tunnels; pair test over several transports with comparison
    gateway.py       Iran Xray gateway: ports → node, borrowed configs, exit test
    engines.py       check / update backhaul and Xray on every server (GitHub releases, cached binaries)
    nodes.py         node tools: console presets, connect, add many, launchers, Abuse-Defender, reboot, geo, Iran filter check
    actions.py       repair framework: named, logged, dry-run, rate-limited; Fix with a story
    ai.py            Anthropic advice inside Fix (key in settings, never in the repo)
    alerts.py        flapping control → Telegram; report.py daily report; telegram.py; backup.py (encrypted)
    configs.py       configs on the probe, subscriptions; probe.py probe API client; probe_install.py
    panels.py        Marzban / PasarGuard client: status, nodes, users, hosts, test config per panel
    core.py          Xray core outbounds of a panel with backup / rollback
    shopbot.py       the shop bot's server over SSH: services, switches, users, SQL, backups, DB import
    importer.py      the old panel's JSON -> rows, idempotent
    settings.py      typed settings in the database (secrets masked in responses)
    events.py        the sentences the operator reads
  api/             FastAPI routers, one file per resource; thin: validate, call a service, answer
                   (servers, tunnels, nodes, gateway, engines, actions, ai, configs, panels, shopbot,
                    alerts, events, jobs, system = update of the panel itself)
  web/             index.html + app.css + app.js - no build step; PAGES.<name> per page, SUB tabs,
                   glass modal windows (showModal), themes (LOOK)
  main.py          create_app(), lifespan, `nikatel` CLI (serve / import / set-password / run-job / restore)
tests/             pytest; services tested pure, the API through TestClient, ssh never real
scripts/           deploy.sh (secrets → tests → unit → restart → health → rollback), backup.sh, unit, secrets check
install.sh         the one-line installer (also used by the panel's own "Update" button through deploy.sh)
docs/              operations, development, logging, roadmap, adr/
```

## 2b. The UI, page by page

* **Dashboard** - health rows, quick access in the header, activity feed that scrolls; auto-refresh.
* **Nodes** - Iran / Foreign / Gateway & outbounds. A click on a node opens its glass window: overview,
  charts, tunnels, filtering, tools (console, connect, reboot, Abuse-Defender, geo, launchers).
* **Tunnels** - List (with quality and ports), Create, Test (pair test over several transports), Repairs.
* **Panels** - Configs (group bar, grid / compact / list, inline tunnel and CDN port, subscriptions)
  and Panels (Marzban / PasarGuard: nodes, users, hosts; each panel gets a test config on the probe).
* **Shop bot** - the bot's server: status, switches, users, tables, SQL, backups, import.
* **Settings** - Account · Alerts · Backups · Probe · Repairs & AI · Shop bot · Appearance · Update.

## 3. Rules

1. **Layers call downward only**: api → services → (db, ssh). Services never import FastAPI.
2. **Pure before impure**: parsing and judging are functions of strings and dataclasses, tested
   without a server. SSH and the database sit at the edges.
3. **Named scripts only** over SSH. Something new to read on a server = a new entry in
   `ssh.SCRIPTS` with parameter validation, reviewed in the PR. No API handler builds a shell
   command.
4. **Secrets stay in**: `Tunnel.secrets` and `auth.json` never reach a response; the log
   formatter masks tokens; `.gitignore` and `scripts/check_no_secrets.sh` keep keys, data and
   real addresses out of the public repository.
5. **State changes are events**: a transition (up→down, offline→online) writes one Event; a
   check that changes nothing writes none.
6. **Jobs never die**: `jobs.run_once` is the boundary; a failure lands in `jobs.last_error`
   and the UI, not in a dead thread.
7. **Errors are sentences**: 400 bad input · 401 login · 404 no such thing · 409 conflict ·
   502 a server or panel did not answer · 500 our bug (logged with the request id the browser
   shows). Never a traceback to the browser.
8. **Every change ships with a test**; `scripts/deploy.sh` and CI refuse a red suite.

## 4. Data

SQLite in WAL mode at `<data_dir>/nikatel.db`. Samples kept 7 days, tunnel checks 14 days,
events forever (small). Schema changes are entries in `db.MIGRATIONS`, applied on start.

## 5. Adding a feature

1. Model: columns or a table in `models.py`, a migration in `db.MIGRATIONS`.
2. Service: logic in `services/<name>.py`, pure parts first, tests in `tests/test_<name>.py`.
3. SSH: if the servers must be read, a named script in `ssh.SCRIPTS`.
4. API: a router in `api/<name>.py`, registered in `api/__init__.py`.
5. UI: `PAGES.<name>` in `web/app.js`, a nav entry, styles under a `/* ---- <name> */` block.
6. Docs: a `CHANGELOG.md` line; `docs/operations.md` if it changes how the panel is run.

## 6. Decisions

`docs/adr/`: 0001 fresh start beside the old panel · 0002 FastAPI + SQLite + plain front-end ·
0003 separate key, read-only named scripts · 0004 public repository without infrastructure data.
