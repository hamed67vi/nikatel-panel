# ADR 0002 - FastAPI + SQLite + plain front-end

**Date:** 2026-10-09 · **Status:** accepted

## Decision

* **FastAPI**: typed request/response models, OpenAPI for free, dependency injection for auth.
* **SQLite via SQLAlchemy 2.0**, WAL mode: one file, copied for backup, migrations in code.
  The data is small (tens of servers and tunnels, time series pruned to days).
* **No front-end framework, no build step**: `index.html` + `app.css` + `app.js`, one function
  per page. The UI is mostly tables and forms; a build chain would slow every small change.
* **Python 3.10+** (the lowest version on our servers), tested on 3.10 and 3.12 in CI.

## Rejected

* Refactoring the monolith - ADR 0001.
* Go - faster binary, slower iteration on a UI-heavy tool, second language.
* React/Vite - better components but Node, a build, and a second toolchain for one developer.
* PostgreSQL - another service to run and back up for data that fits in memory.

## Consequences

* `pip install -e .` is the whole build; `scripts/deploy.sh` is tests + restart.
* If the UI outgrows tables and forms, revisit the front-end decision.
