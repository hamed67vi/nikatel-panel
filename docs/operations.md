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

Secrets check → tests → reinstall → restart → `/api/health`. On failure it returns to the last
good commit by itself. Manual: `bash scripts/deploy.sh --rollback`.

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
