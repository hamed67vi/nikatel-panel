# Panels: how a panel is connected

A Marzban or PasarGuard panel is given a **credential, not a password**. After that everything (users, hosts, nodes, cores, ...) is done from this panel through the panel's REST API.
This page says what each panel accepts, what is kept here, and what the page shows and does about it. Code: `nikatel/services/panels.py` (`add`, `update`, `forget_login`, `key_info`, `auth_of`), routes in `nikatel/api/panels.py`.

## What the panels accept (read from the source and tried on real panels, 2026-10-14)

| | PasarGuard | Marzban |
|---|---|---|
| API key | **yes**: `pg_key_<uuid4>`, header `X-Api-Key` (or `Authorization: ApiKey ...`). Made in the dashboard under *API Keys* (shown once). Optional end date, revocable one by one, acts as its admin: with `inherit_permissions` it has the admin's rights, otherwise only the permissions stored on the key (never owner level). Routes: `/api/api_key`, `/api/api_keys` | no |
| Access token (JWT) | yes: `POST /api/admin/token` (username + password), header `Authorization: Bearer` | yes, the same |
| Lifetime of a token | `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` of the panel, default 1440 (24 h), 0 = never | the same setting; a real Marzban panel measured: 1,000,000 minutes = 694 days |

A key sent as a Bearer token is refused (401): the header decides. A JWT cannot be revoked (the panel keeps no list), it only ends at `exp`.

## What is kept here

`Panel.secrets` (JSON, never returned by the API, never logged; backups are encrypted by the passphrase):

- `token` — the API key or the access token the owner gave (or a sign-in produced);
- `key_meta` — what the panel said about its key: name, end date, status, the admin and role it acts as (not secret; refreshed at most every 12 hours, and by *Check now*);
- `password` — a saved login, **only when the owner asked for it** (`keep_login`), to renew a token that ends.

`GET /api/panels` gives every panel an `auth` object instead: `kind` (`api_key` · `token` · `login` · `none`), `admin`, `access`, `key_name`, `exp`, `left_s`, `short`, `level` (`ok` · `warn` · `bad`), `note`, `login`.

## Ways to connect (Add panel / Edit)

1. **API key** (PasarGuard) — paste the key. It is asked what it is (`GET /api/admin`, `GET /api/api_keys`: name, end date, still active, admin, role) and tried (`/api/system`, `/api/nodes`) before anything is saved.
2. **Access token** — paste the JWT (the panel's own sign-in gives one). Its end date is read from the token. An already ended token is refused.
3. **Sign in once** — a username and password are used once to get a token; the **password is forgotten**, only the token is kept. (An old API client that posts `username` + `password` to `POST /api/panels` gets exactly this.)
4. **Saved login** — the old way. Still works for panels already added (their stored password is untouched). `keep_login: true`, or a `password` typed on an existing panel, saves it; nothing saves it by itself.

Order of use for a request (`_token`): a token a saved login made (kept in memory 45 minutes) → the stored key / token while it lasts → a sign-in with the saved login. After a 401 only the saved login can still help; without one the error says to paste a new key or token.

## Keeping the connection alive

- **Shown**: a small mark in the bar of the panel (`API key`, `token`, `saved login`; grey = only a login, amber / red when a key or token is about to end) and, in *Edit*, what is kept, the admin it acts as and when it ends.
- **Warned**: a key or token kept **without** a login raises an event (`warn` below 7 days, `bad` below 1 day or when it ended or the key was revoked), once per level. A saved login renews the connection itself, so it never warns.
- **Replaced**: `PATCH /api/panels/{id}` with `token` (or `username` + `password`). The new credential is tried on the panel first; the old one stays when the panel refuses it. It never wipes a saved login.
- **Forget the saved login**: `POST /api/panels/{id}/forget-login` signs in one last time, keeps the token it gives and wipes the password. Refused, with the reason, when that token ends within 30 days (a 24 h token would stop the panel tomorrow); `force: true` does it anyway. With an API key already stored no sign-in is needed.

## What the real tests showed

- A real PasarGuard 5.x panel with an owner-level key: added by the key alone → up, users and hosts read, nodes listed, the key's name, end date (372 days), admin and role found; a wrong key → refused with the panel's reason; nothing was saved.
- A panel that only had a saved login: its first `forget-login` was refused (the panel gives 24 h tokens); after the key was pasted, `forget-login` worked and the panel went on answering with the key only.
- A real Marzban panel: the token its sign-in gives lasts 694 days, so `forget-login` works at once and the panel answers with the token only.

## Limits

- A JWT is a bearer secret kept in the database in plain JSON (the password was too). The data directory is `0700`; keep backups encrypted.
- A token from a PasarGuard sign-in lasts 24 h unless the panel's setting was raised: use an API key.
- A key with `inherit_permissions: false` may lack rights: a tab that needs more shows the panel's own 403 text.
- Marzban has no API keys: raise `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` on the panel (0 = never) before keeping only a token there.
- The stored password of a panel that was added the old way is not touched; the owner decides (*Keep only a token* / *Forget the saved login* in Edit).
