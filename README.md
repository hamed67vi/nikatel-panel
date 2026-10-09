# Nikatel panel

Control panel for a tunnel-based network: Iranian and foreign servers, the tunnels between
them, configs, Marzban / PasarGuard panels and a Telegram shop bot. A clean rebuild of an
earlier single-file panel - written to be read, tested, operated and extended.

[![ci](https://github.com/hamed67vi/nikatel-panel/actions/workflows/ci.yml/badge.svg)](https://github.com/hamed67vi/nikatel-panel/actions)

## Install (one line, Debian/Ubuntu, as root)

```bash
curl -fsSL https://raw.githubusercontent.com/hamed67vi/nikatel-panel/main/install.sh | bash
```

It asks for the port, an optional domain (Let's Encrypt) and the admin password, installs
what is missing, and starts the panel on `https://<server>:9443`. Run it again to upgrade.
Non-interactive: `curl … | bash -s -- --yes --port 9443`.

## Read

* [`ARCHITECTURE.md`](ARCHITECTURE.md) - the map of the code and the rules.
* [`docs/roadmap.md`](docs/roadmap.md) - milestones and issues, where we are.
* [`docs/operations.md`](docs/operations.md) - run, upgrade, back up, read logs.
* [`docs/development.md`](docs/development.md) - change it, test it, ship it.
* [`docs/adr/`](docs/adr/) - why things are the way they are.

## Status

**1.4 - M0 to M7 done, M8 in progress.** Servers and tunnels (monitor, alert, repair, build, test),
the Iran gateway, configs and the probe, Marzban / PasarGuard panels with their outbounds, the shop
bot, and everything the old panel could do, in the new structure. The old panel is retired. Next
(M8): the other tunnel engines (BackPack, Paqet, Chisel) in Create and Test, a fully customised shop
bot, a language switch. API docs at `/api/docs`.

## How it is built, in one paragraph

One Python process (FastAPI) with SQLite. The browser talks to `/api/*`; the panel talks to servers
only over SSH with its own key and a fixed list of reviewed scripts (`nikatel/ssh.py`), to Marzban /
PasarGuard over their HTTP API, and to the probe inside Iran over its API. Background jobs sample
servers, check tunnels with hysteresis, alert through Telegram, back up, and repair. The UI is three
files with no build step: `index.html`, `app.css`, `app.js` (one function per page). The full map is
in [`ARCHITECTURE.md`](ARCHITECTURE.md).

The repository contains no addresses, names or credentials of the real infrastructure; those live
only in the database on the panel server. `scripts/check_no_secrets.sh` enforces this on every
commit and in CI.
