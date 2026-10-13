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
                   Maintenance, Config, ConfigSample, Subscription, Panel, ActionLog, Gateway, RepairCase, ApiToken
  ssh.py           Node, run_script(name, params) over the reviewed SCRIPTS and ACTIONS; run_raw only read-write
  auth.py          one admin, scrypt, signed cookie
  jobs.py          background loops with their status in the jobs table; stop_all waits for in-flight SSH
  services/        the logic, importable without HTTP
    facts.py         server resources (parse_facts is pure)
    tunnels.py       tunnel health with hysteresis (plan_for / parse_side / judge / settle are pure)
    builder.py       create / remove / rebuild a backhaul-over-GRE tunnel; set_ports; pending rebuilds that survive a
                     restart of the panel; follow_server_rename / follow_server_host (a server's new name or address)
    build_other.py   the other engines: BackPack (reverse and direct), Paqet, Chisel - plan, install, units, proof, rollback
    create.py        the Create story: precheck of ports and conflicts, Smart choice of the Iran side, replace, DNS follow
    detect.py        find tunnels already running on the servers and adopt them; auto choice of the Iran side
    trash.py         removed tunnels kept for a while with everything needed to put them back
    retune.py        apply newer tunnel defaults one tunnel at a time, checked, put back when worse
    pulse.py         a 5 s heartbeat that marks a switched-off server off within seconds and wakes the fast lane of the tunnel checks
    pathscan.py      the route of a tunnel measured from both servers (where the loss is: this pair, one server's own network, the anchors)
    extras.py        installs started right after a server is added (torrent block, Marzban node): detached on the server, followed by polling
    netwatch.py      the network disturbances of a server (its own route is poor: repair only watches)
    tunnel_test.py   quality of one / all tunnels; pair test over several transports with comparison
    gateway.py       Iran Xray gateway: ports → node, borrowed configs, exit test
    engines.py       check / update backhaul and Xray on every server (GitHub releases, cached binaries)
    nodes.py         node tools: console presets, connect, add many, launchers, Abuse-Defender, reboot, geo, Iran filter check
    actions.py       repair framework: named, logged, dry-run, rate-limited; Fix with a story
    autopilot.py     the Tunnel Doctor: classify (pure) → ladder restart / rebuild / relink / switch / move, limited by the
                     repair level; _rebuild_safe (the old tunnel comes back when the new build fails); relink probes a new
                     GRE link beside the tunnel first; storm detection; observe (lossy tunnels); repair_cycle (one job);
                     fix_all, explain (the AI reads the whole situation), config diagnosis (SNI, reachability)
    doctor.py        the Tunnel Doctor's three modes (off / fix / move) and its moves: a dead Iran server (server by server in a
                     provider-wide outage), an exhausted server (one tunnel), never for packet loss (suggested instead), the return
                     after three healthy hours; the ledger of moves, the suggestions, the rails (pure: is_saturated, may_move, ready_to_return)
    failover.py      HOW a move is done: plans (the dead-server plan, the route plan that is only suggested, replace_plan after Create),
                     _work = make before break when the source is on (new tunnel first, DNS, TTL wait, old tunnel last), the DNS
                     records of EVERY account re-pointed in their own account, dns_log with restore / undo, Telegram notices
    arvan.py         ArvanCloud CDN API client: records of every domain, swap_ip (pure), apply with read-back, test; create / edit / delete A records
    cloudflare.py    Cloudflare DNS API client (one address per record, proxied flag); dnsprov.py: the DNS accounts (several ArvanCloud, Cloudflare) under one list
    gate.py          the secret entry path: the panel answers 404 to everyone who does not know it (a live API token passes it)
    tokens.py        API tokens for machines: hashed at rest, shown once, scoped read / act / admin (`allowed` is pure), revocable
    license.py       owner / licensed / free: a signed NK1 token (Ed25519), features, the gates the rest of the panel asks
    ai.py            Anthropic advice inside Fix (key in settings, never in the repo)
    balance.py       CPU on the cores: a busy server whose packets sit on one core gets RPS (probe / judge pure, apply with before-after and rollback)
    analyst.py       what the history says: tunnel profiles (availability, outages, latency, weak hours), outages that come together, what fixes what,
                     servers at the peak (idle / redundant); hints the smart repair asks before it climbs (RepairCase rows)
    diagnosis.py     reads a failure: is it the panel, a service on your server, or outside it? (pure rules; a repair's plain summary and the log summary use it)
    underuse.py      a server that carries little is looked at (net_health probe): what the kernel settings can fix is fixed (sysctl_tune, measured,
                     rolled back when worse); a fault of the provider's network is reported once
    besttoday.py     the best tunnels of the day: ranked from the checks of the last hours (up, latency, jitter, loss, breaks); a job measures every tunnel again each half hour
    checkall.py      check every server in one go (answers, GRE through a temporary link, filtering in Iran): ONE action on the panel server, progress from the
                     action log, Stop ends it, a page reload does not
    housekeeping.py  the growing tables trimmed (events 60 days, finished stories 90 days), the write-ahead log folded back
    demo.py          the public demo: invented data, no sign-in, every change refused (NIKATEL_DEMO=true; docs/demo.md)
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
                   (auth, tokens, servers, tunnels, nodes, gateway, engines, actions, autopilot, ai, configs, panels, radar, shopbot,
                    alerts, events, jobs, failover, doctor, dns, license, balance, analyst, checkall, best, system = health, logs, update of the panel itself)
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
  test over several engines and transports, Advanced for the transports), Core update, Tunnel Doctor
  (attention list, live repair stories, history), DNS · IP switch (ArvanCloud records, Point, failover plans,
  the DNS change log with Restore).
* **Panels** - Configs (group bar, grid / compact / list, inline tunnel and CDN port, subscriptions,
  GRE / filter checks) and Panels (Marzban / PasarGuard: nodes, users, hosts; a test config per panel).
* **Shop bot** - Overview · Payments · Orders · Users · Plans · Categories · Servers · Discounts ·
  Gift codes · Broadcast · Tickets · Texts · Settings · Tools (Mini App, backups, apps, top-ups, tables,
  SQL, stats, webhook, commands, restart, dump, logs, import).
* **Settings** - Account · Alerts · Backups · Probe · Repairs & AI · Shop bot · Appearance · About & update
  (version, update, license, tunnel cores).

## 2d. The license

`services/license.py` answers one question for the rest of the panel: *is this feature on?* The modes are
owner (`NIKATEL_LICENSE=owner`), licensed (a token under Settings → About, checked with the vendor's Ed25519
public key, cached a minute) and free (no token: Backhaul TCP Mux only). The gates: `builder.start_build`,
`build_other.start_build`, `tunnel_test.compare_start`, `autopilot._recommend_now`, `ai.ask`,
`failover.has_key` and the `/api/shopbot` router (402). The check is honest about its limits -
`docs/licensing.md` explains why the valuable parts (metered AI, releases) belong on the license server.

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
   shows). Never a traceback to the browser. One handler gives every error the one shape
   `{"ok": false, "error": <sentence>, "code": <slug>}`, and work longer than a few seconds answers 202 with an
   action id (`docs/api.md`).
8. **Every change ships with a test**; `scripts/deploy.sh` and CI refuse a red suite.

## 4. Data

SQLite in WAL mode at `<data_dir>/nikatel.db`. What is kept: server and config samples 7 days, tunnel checks 14 days,
events 60 days, finished repair stories 90 days, the last 2,000 repair cases (`services/housekeeping.py` runs every 6 hours). Schema changes are entries in `db.MIGRATIONS`, applied on start.

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
