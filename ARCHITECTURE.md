# Architecture

## 1. Landscape

```
 browser ── HTTPS ──► panel (FastAPI + SQLite) ── SSH (own key, reviewed scripts + actions) ──► Iran servers
 Telegram ◄── alerts ─┘       │                                                                 foreign servers
                              ├── HTTPS ──► Marzban / PasarGuard panels   ── HTTP ──► probe (inside Iran)
                              └── SSH ──► shop bot server (PHP bot + MariaDB + Mini App)  ◄── Telegram ◄── customers
```

One Python process. SQLite for state. SSH is the only way the panel touches a server, and in
read-only mode only reviewed scripts run. The tunnels themselves are systemd services on the
servers; the panel observes and repairs them, it never carries traffic. The shop bot is a separate
PHP application on its own server; the panel administers it over SSH through the same reviewed
scripts, and the Mini App (`bot/miniapp/`) is deployed next to the bot and talks to the bot's
database directly, after verifying Telegram's signature on every request.

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
    autopilot.py     Smart repair: the situation (tunnels down / lossy, servers offline, configs down), smart_fix,
                     fix_all, explain (the AI reads the whole situation), config diagnosis (SNI, reachability)
    ai.py            Anthropic advice inside Fix (key in settings, never in the repo)
    alerts.py        flapping control → Telegram; report.py daily report; telegram.py; backup.py (encrypted)
    configs.py       configs on the probe, subscriptions, group sync; probe.py probe API client; probe_install.py
    panels.py        Marzban / PasarGuard client: status, nodes, users, hosts, test config per panel
    core.py          Xray core outbounds of a panel with backup / rollback
    radar.py         Arvan Radar (state of the Iranian data centres), cached, refreshed in the background (API only)
    dcs.py           the hosting providers our servers sit in (from the geo lookup's provider / domain), servers and
                     tunnels per provider, the provider's website checked from the panel server (cached)
    shopbot.py       the shop bot's server over SSH: services, switches, users, SQL, backups, DB import
    shopbot_admin.py the bot's administration: payments (approve / decline like the bot), orders, users, plans,
                     categories, servers, discounts, gifts, broadcasts, tickets, texts (overrides), settings
                     documents, apps, top-ups, backups, command menu, Mini App status / deploy
    importer.py      the old panel's JSON -> rows, idempotent
    settings.py      typed settings in the database (secrets masked in responses)
    events.py        the sentences the operator reads
  api/             FastAPI routers, one file per resource; thin: validate, call a service, answer
                   (servers, tunnels, nodes, gateway, engines, actions, autopilot, ai, configs, panels,
                    radar, shopbot, alerts, events, jobs, system = update of the panel itself)
  web/             index.html + app.css + app.js - no build step; PAGES.<name> per page, SUB tabs,
                   glass modal windows (showModal), themes (LOOK), a loading box on every page change
                   and automatic retry when a load fails, a status bar for messages, phone layout
  main.py          create_app(), lifespan, security headers (CSP), `nikatel` CLI (serve / import / set-password / run-job / restore)
bot/miniapp/       the Telegram Mini App of the shop bot (api.php + index.html + app.js + app.css), deployed
                   next to the bot by the panel (Shop bot → Tools → Mini App) or scripts/deploy-miniapp.sh
tests/             pytest; services tested pure, the API through TestClient, ssh never real
scripts/           deploy.sh (secrets → tests → unit → restart → health → rollback), backup.sh, deploy-miniapp.sh,
                   unit, secrets check
install.sh         the one-line installer (also used by the panel's own "Update" button through deploy.sh)
docs/              operations, development, logging, roadmap, adr/
```

## 2b. The UI, page by page

* **Dashboard** - health rows, Problems box (live state, Fix with AI, Fix all), activity feed with
  categories; auto-refresh.
* **Nodes** - Iran / Foreign / Gateway & outbounds. More / Edit / Delete on every row; the More window
  has overview, charts, tunnels, filtering, tools (console, connect, reboot, Abuse-Defender, geo, launchers).
* **Tunnels** - List (More window with quality and ports, Health / Restart / Rebuild), Create, Test (pair
  test over several engines and transports, Advanced for the transports), Core update, Smart repair.
* **Panels** - Configs (group bar, grid / compact / list, inline tunnel and CDN port, subscriptions,
  GRE / filter checks) and Panels (Marzban / PasarGuard: nodes, users, hosts; a test config per panel).
* **Shop bot** - Overview · Payments · Orders · Users · Plans · Categories · Servers · Discounts ·
  Gift codes · Broadcast · Tickets · Texts · Settings · Tools (Mini App, backups, apps, top-ups, tables,
  SQL, stats, webhook, commands, restart, dump, logs, import).
* **Settings** - Account · Alerts · Backups · Probe · Repairs & AI · Shop bot · Appearance · Update.

## 2c. The shop bot and its Mini App

The bot is a PHP application (WizWiz-based) with a MariaDB database on its own server. The panel
never imports its code: it reads and writes the database through two reviewed scripts
(`bot_query` for SELECT / SHOW, `bot_sql` for one UPDATE / INSERT / DELETE), with every text literal
as `FROM_BASE64("…")` (`shopbot_admin.lit`), talks to Telegram through `bot_tg_json` on the bot
server (the token stays there), reads the bot's texts with `bot_texts` (`php -r` over `values.php`)
and writes overrides as JSON files that one line in `config.php` merges (`bot_texts_hook`). The
semantics the panel mirrors - payment states (`pending` → `have_sent` → `approved` / `declined`),
what approve does (wallet, admin messages, user message), the order columns, the settings
documents (`BOT_STATES`, `PAYMENT_KEYS`) - are documented in `shopbot_admin.py` next to the code.

The Mini App (`bot/miniapp/`) is a static page plus `api.php` living in `<bot dir>/app/`. It
verifies Telegram's `initData` (HMAC with the bot token), then uses the bot's database and the
panels' HTTP APIs directly, writing the same rows the bot writes (see `bot/miniapp/README.md`). The
panel installs and updates it through the `bot_miniapp_put` action (base64, `php -l` before the
old file is replaced, previous copy kept) and reads its state with `bot_miniapp_info`.

## 3. Rules

1. **Layers call downward only**: api → services → (db, ssh). Services never import FastAPI.
2. **Pure before impure**: parsing and judging are functions of strings and dataclasses, tested
   without a server. SSH and the database sit at the edges.
3. **Named scripts only** over SSH. Something new to read on a server = a new entry in
   `ssh.SCRIPTS` with parameter validation, reviewed in the PR. No API handler builds a shell
   command.
4. **Secrets stay in**: `Tunnel.secrets` and `auth.json` never reach a response; the log
   formatter masks tokens; `.gitignore` and `scripts/check_no_secrets.sh` keep keys, data and
   real addresses out of the repository. The bot token and database password never leave the bot
   server (see `SECURITY.md`).
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
