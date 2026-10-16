# Development

## Loop

```bash
git clone git@github.com:hamed67vi/nikatel-panel.git && cd nikatel-panel
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" ruff
pytest -q && ruff check nikatel tests && bash scripts/check_no_secrets.sh
NIKATEL_DATA_DIR=./data NIKATEL_TLS_CERT= NIKATEL_TLS_KEY= NIKATEL_LISTEN_PORT=8000 nikatel serve   # http://localhost:8000
```

Pick an issue → branch `issue-<n>-<slug>` → PR → CI green → merge → `scripts/deploy.sh` on the
panel server. Small PRs; one issue each.

## Where things go

| You want to… | Edit |
|---|---|
| read something new from a server | `nikatel/ssh.py` → `SCRIPTS` (script + parameter regexes), then a parser in `services/` |
| add a table or column | `models.py` + an entry in `db.MIGRATIONS` |
| add an endpoint | `api/<resource>.py`; register in `api/__init__.py` |
| add a page | `web/app.js` → `PAGES.<name>` + `INFO` + a nav link in `index.html`; styles in `app.css` |
| add a background job | a function in `services/`, `jobs.register(...)` in `main._register_jobs` |
| change a default | `config.py`; document it in `scripts/nikatel.env.example` |

## Style

* Python 3.10+, type hints, `ruff` clean. Functions small; names say what they do.
* Pure logic first (strings → dataclasses → decision), I/O at the edge. Test the pure part.
* Every network call has a timeout. Loops catch, log with `exc_info`, continue.
* User-facing text: plain English sentences that say what happened and what to do.
* No `print` - `log = get_logger("<domain>")`.
* Never put a real address, hostname, token or password anywhere in the repo, including tests
  and issues. Use `1.1.1.1 … 9.9.9.9` and names like `ir1`, `fi1` in tests.

## Tests

`tests/conftest.py` gives every test a fresh SQLite in a temp dir and a logged-in `client`.
SSH is never real: test `parse_*` / `judge` with captured output strings, test the API with
imported fixtures. A bug fix adds the failing case first.

CI measures the coverage (`pytest --cov=nikatel`, the total in the job summary), runs `tests/test_upgrade.py` (a database
made by the previous tag opens with the current code and every migration runs: `git archive <tag>` in a temp dir), and installs
the panel on a clean runner with `install.sh` from the checkout, then runs the installer again as an upgrade. `scripts/deploy.sh`
imports the whole panel and runs the migrations against a copy of the live database before every restart (the suite itself does not run on the
panel's server: it runs here, before a version is published; `tests/test_deploy_script.py` runs deploy.sh in a sandbox with a fake venv, systemctl and curl).

## Alignment

`tests/test_alignment.py` fails when the docs and the code drift apart: every service and API module must be named in
`ARCHITECTURE.md`, every setting of `config.py` documented in `docs/operations.md`, every API router behind the sign-in,
every SSH script used somewhere, and no byte-order mark or Windows line ending anywhere. When you add a module, a setting
or a script, the test tells you where to write about it. `python3 scripts/audit_code.py` prints the wider
picture (swallowed exceptions, routes that block on a server, retention, long functions, threads, unused scripts, the
front-end files) - run it before every stabilisation round and fix what it shows.

Hotspots (functions over 100 lines, read with care, split only with tests around them): `builder._build`,
`build_other._build`, `configs.import_subscription`, `create.start`, `actions._fix`, `autopilot.smart_fix`,
`autopilot.diagnose`, `tunnels._check_tunnel`, `alerts.run`, `main.create_app`.

## Releases

`nikatel/__init__.py` holds the version. A milestone ends with a `CHANGELOG.md` section and
`git tag vX.Y.Z && git push --tags`.
