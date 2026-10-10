# Nikatel panel

Control panel for a tunnel-based network: Iranian and foreign servers, the tunnels between
them, configs on a probe inside Iran, Marzban / PasarGuard panels, a Telegram shop bot and its
Mini App. A clean rebuild of an earlier single-file panel - written to be read, tested, operated
and extended.

[![ci](https://github.com/hamed67vi/nikatel-panel/actions/workflows/ci.yml/badge.svg)](https://github.com/hamed67vi/nikatel-panel/actions)

## Install (one line, Debian/Ubuntu, as root)

```bash
curl -fsSL https://raw.githubusercontent.com/hamed67vi/nikatel-panel/main/install.sh | bash
```

It asks for the port, an optional domain (Let's Encrypt) and the admin password, installs
what is missing, and starts the panel on `https://<server>:9443`. Run it again to upgrade.
Non-interactive: `curl … | bash -s -- --yes --port 9443`.

## Documentation

| | |
|---|---|
| **Use it** | [`docs/features.md`](docs/features.md) what it does · [`docs/operations.md`](docs/operations.md) install, upgrade, back up, logs, the bot server · [`docs/shop-bot.md`](docs/shop-bot.md) the shop bot from the panel · [`bot/miniapp/README.md`](bot/miniapp/README.md) the Telegram Mini App |
| **Trust it** | [`SECURITY.md`](SECURITY.md) protections and what to do when something is wrong |
| **Change it** | [`ARCHITECTURE.md`](ARCHITECTURE.md) code map and rules · [`docs/development.md`](docs/development.md) · [`docs/logging.md`](docs/logging.md) · [`docs/adr/`](docs/adr/) decisions |
| **Follow it** | [`CHANGELOG.md`](CHANGELOG.md) · [`docs/roadmap.md`](docs/roadmap.md) · index: [`docs/README.md`](docs/README.md) |

## Status

**1.5 - M0 to M7 done, M8 mostly done.** Everything the old panel could do, in the new
structure, plus the shop bot run entirely from the panel and a Telegram Mini App for its
customers. Open: a real-life run of every tunnel engine on a spare pair, and the language switch.

## What it does

**Dashboard** - infrastructure health rows (servers, tunnels, configs, panels, bot), a Problems
box with the live state and *Fix all*, and the activity feed with categories. Each server's window
shows its hosting provider (from registry data) with a link to the provider's site and whether that
site answers.

**Nodes** - Iran and foreign servers with facts every 2 minutes (CPU, RAM, disk, swap, load,
traffic, busiest process, Abuse-Defender rules), 24 h charts, a console with presets, connect with
a password once (installs the panel's key), add several servers at once, open in ssh / PuTTY /
MobaXterm with your own key, reboot, country by majority vote, "filtered in Iran?" check, the
Iran gateway (plain Xray pushed over SSH, ports forwarded to foreign nodes, borrowed configs
during internet cuts) and the panels' Xray outbounds with backup and rollback.

**Tunnels** - health every minute with hysteresis (units both sides, GRE, 20 pings, forwarded
ports, one HTTP request through the tunnel), reasons for every state, alerts with flapping control,
Smart repair (diagnose both sides → plan → restart only what is broken, Iran first → re-check;
*Fix with AI*, *Fix all*, *Explain*), auto-repair with limits, create backhaul-over-GRE tunnels
(preflight, MTU, hold test, rollback), remove, rebuild, change ports, adopt tunnels already on the
servers, choose the Iran side automatically, quality per tunnel, pair test over several transports
with a comparison table, core / Xray update on every server.

**Panels** - configs on the probe (status, ping, daily speed, 24 h history, groups per
subscription, inline tunnel and CDN port, v2rayN-style editor, subscriptions import and sync, test
all / delete all, direct configs judged by TCP reachability from Iran) and the Marzban / PasarGuard
panels (status, nodes with auto reconnect, users with bulk actions, hosts, a test config per panel on
the probe).

**Shop bot** - the PHP bot on its own server, over SSH through reviewed scripts: payments to
confirm with ✅ / ❌ that credit the wallet like the bot, orders, users (wallet, discount, agent,
ban, message), plans with the panel settings JSON, categories, the panels it sells from, discount
codes, gift codes, broadcasts, tickets with reply, every text and button (overrides), every switch
and the payment keys, apps list, top-up prices, backups, tables, read-only SQL, stats, webhook,
command menu, restart PHP, dump the database, clear logs, import the real database, and the
**Mini App** install / update button.

**Mini App** (`bot/miniapp/`) - the customers' app inside Telegram: services with live usage,
buying from the wallet (the account is created on the plan's panel), subscription link, QR and
configs, renew, extra volume and days, card-to-card top-up with the receipt sent to the admins with
the bot's own buttons, payment history and cashback, gift codes, lucky wheel, tickets, referral
link, connection apps, outage reports.

**Settings** - Account (password), Alerts (Telegram, flapping, reminders, daily report),
Backups (encrypted, to Telegram), Probe (primary + standby, installer), Repairs & AI (auto-repair,
Anthropic key), Shop bot (where it runs), Appearance (themes, day / night, text size, animations),
Update (one click from GitHub with rollback).

Works on a phone: bottom icon navigation, tables that scroll inside the page, full-screen windows.

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
