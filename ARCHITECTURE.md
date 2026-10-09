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
  db.py            engine, sessions, numbered migrations
  models.py        tables: Server, ServerSample, Tunnel, TunnelCheck, Event, Job
  ssh.py           Node, run_script(name, params) over the reviewed SCRIPTS; run_raw refused in read-only
  auth.py          one admin, scrypt, signed cookie
  jobs.py          background loops with their status in the jobs table
  services/        the logic, importable without HTTP
    facts.py         server resources (parse_facts is pure)
    tunnels.py       tunnel health (plan_for / parse_side / judge are pure)
    importer.py      the old panel's JSON -> rows, idempotent
    events.py        the sentences the operator reads
  api/             FastAPI routers, one file per resource; thin: validate, call a service, answer
  web/             index.html + app.css + app.js - no build step, one function per page
  main.py          create_app(), lifespan, `nikatel` CLI (serve / import / set-password / run-job)
tests/             pytest; services tested pure, the API through TestClient, ssh never real
scripts/           deploy.sh (secrets → tests → restart → health → rollback), backup.sh, unit, secrets check
install.sh         the one-line installer
docs/              operations, development, logging, roadmap, adr/
```

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
