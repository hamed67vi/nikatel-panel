# Security

What the panel protects, how, and what to do when something looks wrong. The panel can change
servers, create accounts on panels and move money in the shop bot, so this page is part of the
product, not an afterthought.

## The model in one paragraph

One admin, one process, one database on one server. The browser only ever talks to `/api/*` with a
signed session cookie. The panel talks to servers over SSH with its own key and a fixed list of
reviewed scripts; to Marzban / PasarGuard over their HTTP API with credentials kept in the database;
to the shop bot's server over the same SSH model (the bot token and the database password never
leave that server); and the Mini App talks to the bot's database only after Telegram's signature on
the request has been verified. Nothing that identifies the real infrastructure is in this repository.

## Layers

| layer | what is done |
|---|---|
| **Login** | one admin, password hashed with scrypt, a signed cookie (`secret.key` on the server), a constant-time check even for a wrong user; optional **2FA** (TOTP, RFC 6238, with one-time recovery codes and replay protection) from Settings → Account; password reset by a code sent to the alert Telegram chat or by a recovery code; a session epoch - changing the password or the 2FA setup signs every other session out. The initial password is written to a file only root can read and shown once; `nikatel set-password` on the server changes it. |
| **HTTP** | `Content-Security-Policy` (`default-src 'self'`, scripts and styles from the panel itself, images from the panel and the flag CDN, no frames), `X-Content-Type-Options`, `Referrer-Policy`, no caching of the app files; `/api/docs` and `/api/openapi.json` need a session. Behind Caddy (Let's Encrypt) in production; give the panel `NIKATEL_LISTEN_HOST=127.0.0.1` there (the default is every interface, so that a first install can be reached). |
| **SSH** | the panel's own key (`/root/.ssh/...` on the panel server, never in the repo). Only the scripts in `ssh.SCRIPTS` (read-only) and `ssh.ACTIONS` (changing) run; every parameter is checked against a regex before it is put into the script; a script never takes free text (free text travels as base64); every command line that names a server takes `user@host` from `ssh.target` (checked) after `--`, so a user or host that looks like an option never reaches ssh / scp. Actions are refused in read-only mode (`NIKATEL_READ_ONLY`), which can only be turned off on the server; read-only covers the servers - a change to a Marzban / PasarGuard panel is the decision of the signed-in admin. **The console** of a server runs the command the signed-in admin types (an admin session only - never an API token); it is logged. |
| **Repairs** | named, logged (`action_log` with the story), rate-limited (2 per tunnel per hour), never when a server is unreachable, auto-repair off by default. The AI only reads facts and may add *safe* steps from a fixed list; it cannot run anything else. |
| **Shop bot** | SQL goes through `bot_query` (SELECT / SHOW only) and `bot_sql` (one UPDATE / INSERT / DELETE / CREATE TABLE IF NOT EXISTS), both regex-limited; every text literal is a `FROM_BASE64("…")` so quotes, backslashes and semicolons can never break out. Telegram calls go through `bot_tg_json` on the bot server with the bot's own token; the method list is fixed (send / edit / set, never read). Switch values are sanitised; approve / decline change a payment's state only once. |
| **Mini App** | every request carries Telegram's `initData`, verified with HMAC-SHA256 against the bot token and refused when older than 24 h; the user id comes from the signature, never from the client. Prepared statements everywhere; the wallet is debited inside a transaction with a row lock, and the panel call that creates the account happens inside it (a refusal rolls back). Receipt uploads are limited to 6 MB, checked by real MIME type, sent to Telegram and never stored. Errors go to a log file; the client sees a sentence. |
| **Secrets at rest** | `Tunnel.secrets`, panel passwords, the Telegram token and the AI key stay in the database; responses mask them (`api_key_tail` only). Backups are encrypted (`openssl aes-256`, passphrase set once). The log formatter masks tokens and keys (Telegram, Anthropic, PasarGuard `pg_key_`, panel `nkt_` tokens, private keys). **API tokens** (`read` / `act` / `admin`) never reach the sign-in, the settings, the backups (a restore replaces the sign-in), the private key of the panel, the SSH passwords, the console or the shop bot's payment keys. |
| **Repository** | `scripts/check_no_secrets.sh` (pre-commit and CI) refuses public IPv4 addresses, our hostnames, tokens and private keys; gitleaks runs in CI. Tests use documentation addresses (`203.0.113.x`) and generic names. |

## Operating it safely

* Switch 2FA on (Settings → Account) and keep the recovery codes somewhere safe. Keep the panel
  behind TLS and, if possible, an allow-list of your own addresses at the reverse proxy.
* Rotate the SSH key with `ssh-keygen`, add the new public key to every server, then replace the
  key file on the panel server; the panel picks it up on restart.
* Read-only mode (`NIKATEL_READ_ONLY=true`) is the safe state when you only want to watch.
* The bot's `db.env` and `baseInfo.php` are root- / web-server-only files on the bot server; the
  panel reads them through the scripts and never copies them anywhere.
* `scripts/backup.sh` nightly; test a restore once.

## When something is wrong

1. Settings → Account: change the password (every other session ends). On the server, when you
   cannot sign in: `nikatel set-password '<new>'`.
2. Set `NIKATEL_READ_ONLY=true` and restart — the panel can then change nothing.
3. Revoke the SSH key on the servers (`authorized_keys`), the AI key at Anthropic, and the bot
   token at BotFather if the bot server is in doubt.
4. Read `/var/lib/nikatel/logs/nikatel.log` (every request has an id) and the Events page.

Report a vulnerability privately to the repository owner; please do not open a public issue with
details.
