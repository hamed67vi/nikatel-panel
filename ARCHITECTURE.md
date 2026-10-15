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
  models.py        tables: Server (+extra, profile_id), Profile, ServerSample, Tunnel, TunnelCheck, Event, Job, AlertState,
                   Maintenance, Config, ConfigSample, Subscription, Panel, ActionLog, Gateway, RepairCase, ApiToken
  ssh.py           Node, run_script(name, params) over the reviewed SCRIPTS and ACTIONS; run_raw only read-write
  auth.py          one admin, scrypt, signed cookie
  gate.py          the secret entry path: the panel answers 404 to everyone who does not know it (a live API token passes it); it works on the HTTP request, so it sits beside auth.py and not in services/
  jobs.py          background loops with their status in the jobs table; stop_all waits for in-flight SSH
  services/        the logic, importable without HTTP
    facts.py         server resources (parse_facts is pure)
    profiles.py      profiles: what is on the Lab page - an Iran server, a foreign server or a PasarGuard node (1.49.0); a server that is made gets its profile (ensure_server), the servers the panel already has are picked in Add profile; api/profiles.py, web/lab.js
    uptime.py        the strip of cells that shows how a server looked over the last hours, read from the samples only (build_strip / up_percent are pure; cpu_series averages the CPU in cells for the sparkline of a row); api/servers.py and api/profiles.py serve it
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
    cloudflare.py    Cloudflare DNS API client (one address per record, proxied flag); dnsprov.py: the DNS accounts (several ArvanCloud, Cloudflare) under one list;
                     dnscache.py: the ONE copy of the records of every account that the DNS page and the Outbound page share (read in the background, forgotten on every write)
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
    panels.py        Marzban / PasarGuard client: how a panel is connected (API key / token / sign in once, docs/panels.md), status, nodes, users, hosts, test config per panel
    hosttls.py       TLS copies of the plain WebSocket hosts of a panel (port 443, security tls, SNI = the Host header): the plan, a real test from the panel server (TCP, TLS with the SNI, WebSocket upgrade), the copies with a backup file, the removal
    pg_access.py     PasarGuard groups and user templates (roadmap #94), api/pg_access.py, web/pg_access.js
    pg_nodes.py      PasarGuard nodes joined with the servers of this panel by address (add / install a node from the Add server form, web/pg_srvnode.js), api/pg_nodes.py, web/pg_nodes.js
    pg_nodes_extra.py  PasarGuard nodes, the rest of the API: bulk enable / disable / reset / reconnect / delete, usage and latency curves, user counts, clearing usage; routes in api/pg_nodes.py
    pg_update.py     PasarGuard updates (Settings → PasarGuard, roadmap #103): the newest releases from GitHub, what every panel and node runs, nodes updated ONE AT A TIME (a node that does not come back stops the run), the panel itself on its own server with `pasarguard backup` + `pasarguard update` over SSH (a detached action of ssh.py, followed by extras._run_item); api/pg_update.py, web/pg_update.js
    pg_cores.py      PasarGuard cores (Xray configs): make / duplicate / delete (also several at once), inbound add / change / delete with a REALITY builder, dry run, save with a backup and a read-back, restart, rollback, api/pg_cores.py, web/pg_cores.js
    pg_admin.py      PasarGuard admins (also several at once through the panel's bulk calls), API keys and settings (7 sections as forms), api/pg_admin.py, web/pg_admin.js
    pg_roles.py      PasarGuard admin roles (permissions, limits, access, device rules) with backups and restore, api/pg_roles.py, web/pg_roles.js
    pg_client_templates.py  PasarGuard client (subscription) templates - Clash, Xray, sing-box, User-Agent lists - with backups, delete several in one call, api/pg_client_templates.py, web/pg_client.js
    pg_system.py     PasarGuard System tab: version, resources, user counters and the other numbers the panel keeps, api/pg_system.py, web/pg_system.js
    pg_users.py      PasarGuard users in full: the whole user (groups, on hold, devices limit, next plan, owner), devices, subscription fetches and the subscription in each client format, bulk by name, shift of end dates and data limits, the Shadowsocks method of many, clean-up, users from a template; api/pg_users.py, web/pg_users.js
    pg_hosts.py      PasarGuard hosts in full: every field, the advanced settings as checked JSON, make / change / clone / delete / bulk, backups; api/pg_hosts.py, web/pg_hosts.js
    pg_kinds.py      One step makes a whole KIND of account on a PasarGuard panel (eleven kinds, only the protocol is asked: the rest is chosen): the inbound (core), the group (access), the host and - when asked - a user template together, checked first, read back, taken back when a step fails; api/pg_kinds.py, web/pg_kinds.js
    pg_usage.py      Usage analytics of a PasarGuard / Marzban panel, read only: counters, usage by node and Tehran day, admin / user lookups, top users, users over time (online / expired / limited), the apps that fetch the subscription, Jalali calendar; api/pg_usage.py, web/pg_usage.js
    accounts.py      one account over many panels: the users of every panel asked through their API and put side by side, one action / a new user on every panel, the signed merged subscription link (/sub/<token>, api/sub.py)
                     (same username, or a hand-made link); nothing is copied out of the panels (roadmap M12)
    outbound.py      the Outbound section: a pool of exit configs (vless / vmess / trojan / ss) and forwards to the foreign nodes, put on the
                     chosen Iran servers through gateway.py (spread / fast / auto), a Shadowsocks relay on a foreign server behind the exits, the ArvanCloud
                     records of the tunnels pointed at the forwards (manual or automatic, put back through the DNS log), the pool measured from each Iran server; exit links never listed
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
                   (auth, tokens, servers, tunnels, nodes, gateway, engines, actions, autopilot, ai, configs, panels, accounts, outbound, radar, shopbot,
                    alerts, events, jobs, failover, doctor, dns, license, balance, analyst, checkall, best, system = health, logs, update of the panel itself)
  web/             plain JS and CSS, no build step. index.html loads everything; app.js: PAGES.<name> per page, SUB tabs, glass modal windows (showModal), themes (LOOK),
                   a loading box on every page change and automatic retry when a load fails, a status bar for messages; pg_*.js: one file per PasarGuard tab, each
                   registers itself in pgTabs (pg_common.js) and is drawn by the pages of the PasarGuard group (see 2b); app.css + pg_*.css: the looks.
                   The fluid layer (1.44.0, roadmap #101): early.js picks the layout (`html[data-fluid]`), fluid.css is the desktop and the tablet (bars are one row,
                   windows, the lock at 560 px), fluid.js fits a table to its box (columns shrink, the less important ones drop; a header can say its own
                   importance with data-prio, 0 = never drops - for a table whose first column is a tick box), mobile2.css is the phone,
                   mobile.css the old card layout (`?fluid=0`); i18n*.js the Persian words
  main.py          create_app(), lifespan, security headers (CSP), `nikatel` CLI (serve / import / set-password / run-job / restore)
bot/miniapp/       the Telegram Mini App of the shop bot (api.php + index.html + app.js + app.css), deployed
                   next to the bot by the panel (Shop bot → Tools → Mini App) or scripts/deploy-miniapp.sh
tests/             pytest; services tested pure, the API through TestClient, ssh never real
scripts/           deploy.sh (secrets → tests → unit → restart → health → rollback), backup.sh, deploy-miniapp.sh,
                   unit, secrets check, pg_coverage.py (which routes of a PasarGuard panel the tests send: a recorder on the calls to a panel
                   matched with the OpenAPI document of a live panel)
install.sh         the one-line installer (also used by the panel's own "Update" button through deploy.sh)
docs/              operations, development, logging, roadmap, adr/
```

## 2b. The UI, page by page

The left menu (`web/index.html`): **Dashboard · Servers · Tunnels · Panel**, Shop bot (switched off) and **Settings**. Every page with tabs is a group like the sidebar of PasarGuard: `navBuild()`
(app.js) puts the tabs of the page - `SUB.<page>`, or `SET_TABS` for Settings - under its head, the arrow opens and closes the list (`navGroup`), `navMark` says which item is the open page and tab, and
a wide window does not draw the bar of tabs under the header while the group is open (see app.css; the rail and the phone show the head alone). `PAGES.<name>` draws a page, `SUB` holds the tabs of a
page, `go(page, tab)` opens a page on a tab, `NAV_ALIAS` keeps the old page names working (`"page/tab"` = on that tab).

* **Dashboard** - health rows, Problems box (live state, Fix with AI, Fix all), activity feed with
  categories; auto-refresh.
* **Servers** - Foreign · Iran · **Nodes** (the PasarGuard nodes of the picked panel beside our servers). More / Edit / Delete on every row; the window of a server has its buttons
  (console, install, connect, reboot, Abuse-Defender, geo), the line of its nodes (with the **Node** view for them, 1.48.0), three boxes (System, Resources, Access & status) and the charts.
* **Tunnels** - List (More window with quality and ports, Health / Restart / Rebuild), Create, Test (pair
  test over several engines and transports, Advanced for the transports), Core update, Tunnel Doctor
  (attention list, live repair stories, history), DNS · IP switch (ArvanCloud records, Point, failover plans,
  the DNS change log with Restore).
* **Panel** (Marzban panels have Users, Hosts and Usage only) - ONE page with a tab for each section of PasarGuard (`SUB.pgpanel`): **Users** (users, accounts over every panel) · **Hosts** ·
  **Access** (Groups, Templates, client templates) · **Core** · **Admins** (admins, API keys, roles), and **General** as the FIRST tab (Usage, System, Settings; the owner, 2026-10-14). Every tab draws the bar of the panels (`pnBar`:
  which panel) and its own tab(s); those are `pgTabs` entries of the `web/pg_*.js` files, each a route first (`/api/panels/{id}/pg/…`). `PG_SUBS` (app.js) says which `pgTabs` each tab holds,
  `pnTab(t)` opens the tab that holds one (a test fails when a registered tab has no place). **The nodes of a PasarGuard panel are not a tab** (1.48.0): a node and the server it runs on are one row of Servers -
  `pg_nodes.js` is the **Node view** of a server window (and of the window of a node that runs on no server of ours), `pg_srvnode.js` the chips, the ticks, the Nodes… menu and the Add server tick;
  the page asks `GET /api/servers/panel-nodes` (the health job's copy of every panel's nodes, placed on the servers by `pg_nodes.placement`) and the Node view `GET /api/panels/{id}/pg/nodes`
  (which also refreshes that copy). Configs (the probe's client configs) are switched off.
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
