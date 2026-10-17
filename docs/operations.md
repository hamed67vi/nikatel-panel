# Operations

Addresses, names and credentials of the real servers are deliberately **not** in this
repository. They live in the database on the panel server.

## Layout on the panel server

| | |
|---|---|
| code | `/opt/nikatel/src` (git checkout of `main`) |
| venv | `/opt/nikatel/venv` |
| data | `/var/lib/nikatel/data/` - `nikatel.db`, `auth.json`, `secret.key`, `known_hosts` |
| backups | `/var/lib/nikatel/backups/` |
| logs | `journalctl -u nikatel -f` and `/var/lib/nikatel/logs/nikatel.log` (10 MB × 14) |
| config | `/etc/nikatel/nikatel.env` · TLS `/etc/nikatel/tls.{crt,key}` |
| service | `systemctl status nikatel` |
| URL | `https://<server>:9443` · API docs `/api/docs` · liveness `/api/health` |

## Settings (`/etc/nikatel/nikatel.env`)

Every value of `nikatel/config.py` can be set as `NIKATEL_<NAME>`; a change needs `systemctl restart nikatel`.

| variable | default | what |
|---|---|---|
| `NIKATEL_LISTEN_HOST` / `NIKATEL_LISTEN_PORT` | `0.0.0.0` / `9443` | where the panel listens |
| `NIKATEL_TLS_CERT` / `NIKATEL_TLS_KEY` | `/etc/nikatel/tls.{crt,key}` | the certificate; plain HTTP when the files do not exist |
| `NIKATEL_DATA_DIR` / `NIKATEL_LOG_DIR` | `/var/lib/nikatel/{data,logs}` | database, keys, backups / the log file |
| `NIKATEL_BOOTSTRAP_DIR` | `/etc/nikatel` | `probe.json` / `panels.json` read once on the first start |
| `NIKATEL_READ_ONLY` | `true` | only the reviewed read-only scripts may run (turn off here, never in the UI) |
| `NIKATEL_SSH_KEY` | `/root/.ssh/nikatel_v2_node` | the panel's own key for the servers |
| `NIKATEL_SSH_CONNECT_TIMEOUT` / `NIKATEL_SSH_MULTIPLEX` / `NIKATEL_SSH_WORKERS` | `10` / `true` / `8` | SSH connect timeout, one shared connection per server, parallel checks |
| `NIKATEL_SERVER_SAMPLE_INTERVAL` / `NIKATEL_TUNNEL_CHECK_INTERVAL` | `120` / `60` | seconds between server samples / tunnel checks |
| `NIKATEL_SESSION_HOURS` | `24` | how long a browser session lives |
| `NIKATEL_LOG_LEVEL` | `INFO` | `DEBUG` shows every SSH call |
| `NIKATEL_LICENSE` | empty | `owner` on the vendor's own installs; empty = a token from Settings or the free tier |
| `NIKATEL_TRUSTED_PROXIES` | empty | addresses of reverse proxies whose `X-Forwarded-For` / `-Proto` are believed (the local machine always is) |
| `NIKATEL_DEMO` | `false` | the public demo with invented data and no sign-in - never on a real install (`docs/demo.md`) |

## Install / recover

```bash
curl -fsSL https://raw.githubusercontent.com/hamed67vi/nikatel-panel/main/install.sh | bash
```

Then restore `/var/lib/nikatel/data/` from a backup, **or** import the inventory of the old
panel: `/opt/nikatel/venv/bin/nikatel import <dir>` where the dir holds `iran-servers.json`,
`foreign-servers.json`, `tunnels.json`. On a new install add the printed public key to every
server's `~/.ssh/authorized_keys`.

## Deploy a change

```bash
cd /opt/nikatel/src && git pull --ff-only && bash scripts/deploy.sh
```

Secrets check → compile → libraries (only when `pyproject.toml` changed) → the whole panel is imported and
its migrations run on a **copy** of the live database → unit file → restart → `/api/health`. About fifteen
seconds. The tests are **not** run here: the whole suite runs on the build server before a version is
published (a server that ran it again spent ten minutes of its two cores on it); `FORCE_TESTS=1 bash
scripts/deploy.sh` runs it here too. On failure the files **and the branch** go back to the last good
commit by itself, so the next update is a plain fast-forward again. Manual: `bash scripts/deploy.sh --rollback`.

The panel's **Update** button runs the same script in its own systemd unit (so the panel can restart under
it) after `git pull --ff-only`. The script writes where it is to `/var/lib/nikatel/update-progress.json`
at every step (`GET /api/system/update/progress` reads it): the percentage on the card is counted by the
server's clock, so a refreshed page or a second tab shows the same number, and a run whose last step is
older than seven minutes counts as stopped. The log of the runs is `/var/lib/nikatel/update.log`.

## Backup

`bash scripts/backup.sh` (cron it nightly). It copies the database with SQLite's online backup
plus `auth.json`, `secret.key`, `known_hosts` into one tarball and keeps 14 days. Restore =
stop the service, unpack into `/var/lib/nikatel/data/`, start.

## Logs

```
2026-10-09T17:26:48Z INFO    tunnels    check name=t1 state=up rtt=81.2 rx=18.6 tx=0.7 rid=job:tunnels
2026-10-09T17:26:50Z WARNING http       POST /api/auth/login -> 401 in 3ms rid=a1b2c3
```

`rid` ties a line to a request (the browser shows it on a 500) or a job. `NIKATEL_LOG_LEVEL=DEBUG`
shows every SSH call. Tokens and passwords are masked before they are written.

## Common questions

**A tunnel is "unknown".** The panel could not reach one of its servers; open the row, the
failing side shows its SSH error. "key is not accepted" = the public key is missing there.

**Everything is "unknown" right after install.** Jobs run 3 s after start, then every 60 s
(tunnels) / 120 s (servers). Jobs → Run now.

**A server's host key changed.** The panel refuses to connect (it says so in the row). If the
server was really reinstalled, delete its line from `/var/lib/nikatel/data/known_hosts`.

**Change the admin password.** `/opt/nikatel/venv/bin/nikatel set-password '<new>'`.

**Turn read-only off** (phase 3): `NIKATEL_READ_ONLY=false` in `/etc/nikatel/nikatel.env`,
`systemctl restart nikatel`. On purpose this cannot be done from the UI.

## Alerts

Settings → Alerts → Alert rules. The panel confirms a fault itself first (a tunnel after its own confirming checks, a server after
two missed 5-second pulses, a config after two different tests of the probe, a panel after two reads), then looks at the states **every 5 seconds**.
A thing goes DOWN once, after it has stayed bad for `down_seconds` (default **10**); UP is sent after it has stayed good for `up_seconds`
(default **15**). The clock starts at the moment the checker changed the state, not at the panel's next look. Typical delays from the event to the Telegram
message: a server switched off or on ~15-25 s, a tunnel cut by a stopped unit ~15-20 s (a link that goes silent ~25 s; a tunnel that only stops carrying data
~45 s), a config ~40-70 s (the probe tests each config about every 30 s and two tests must agree). Reminders (`remind_hours`) are off by default (0).
If something goes down `flap_max` (3) times within an hour you get one FLAPPING message and silence about it for `flap_quiet_hours` (6).
Mute an item or add a maintenance window to silence it on purpose. Too many messages? Raise `down_seconds` (a blip shorter than it says nothing)
or lower `flap_max`. A message Telegram does not take (a timeout, "too many requests") is kept and sent again every 10 seconds, in order, for half an hour.
**One cause, one message (1.68.2).** Many things of one kind that fall at the same moment have one cause, not many faults (the typical one: the probe's own path to its test address makes every config fail with the same curl error).
When at least 4 configs and half of them (3 servers and 40 %, 4 tunnels and 30 %) are bad at once, their DOWN messages wait - configs 90 s, servers 30 s, tunnels 20 s - and what is sent is ONE message: the count, the error they share, a few names and the likely cause.
A disturbance that is over by then is not told at all; the way back is one message (`UP again (14)`). The events page does the same: four or more configs that change status in one read of the probe are one event.
A few things (two configs, one server) are told one by one, `down_seconds` after the fault, as before.
The rules saved by versions before 1.67.0 (failed checks in a row, `min_down_minutes`, `up_stable_seconds`) became these two numbers: the old
defaults give the new defaults, a value the owner had changed is kept (minutes → seconds).

## The shop bot server

Settings → Shop bot holds where the bot runs: host, SSH user / port, the bot directory (the one
with `baseInfo.php`), the env file and the public URL of `bot.php`. The panel's SSH key must be in
that server's `authorized_keys`. What the Shop bot pages need there:

| | |
|---|---|
| `<env file>` (e.g. `/opt/<bot>/db.env`) | `DB_USER=…`, `DB_PASS=…`, `DB_NAME=…` - read only by the scripts, never copied |
| `<bot dir>/baseInfo.php` | the bot's own config; the token is read from it on the bot server for Telegram calls |
| `<bot dir>/settings/text_overrides.json`, `button_overrides.json` | written by Texts; the first save adds one `include` line to `config.php` (a backup `config.php.bak-texts-*` is kept, `php -l` checked) |
| `/opt/<bot>/backups/` | dumps made by *Dump DB* and before every import (`gzip`) |
| `<bot dir>/app/` | the Mini App (`api.php`, `index.html`, `app.js`, `app.css`); previous files kept as `.prev`; its errors in `<bot dir>/settings/miniapp_error.log` |

**Install / update the Mini App**: Shop bot → Tools → Mini App → *Install* / *Update* (the files
come from this repository on the panel server), or by hand from a checkout:
`bash scripts/deploy-miniapp.sh root@<bot host> /var/www/<bot>`. The bot shows the button when
`BOT_STATES.miniAppState` is on (Shop bot → Settings → Extras) and `$botUrl` is set in
`baseInfo.php`; the app's address is `<botUrl>/app/`.

**The real database**: Shop bot → Overview → *Import the real database* copies every table of the
staging database over the live one (dump first; broadcast queues left out).
