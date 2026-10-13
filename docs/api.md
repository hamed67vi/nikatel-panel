# The API contract

The panel is a set of HTTP routes under `/api`; the web page is one client of them and does nothing they do not offer. A script, a
Telegram bot or an AI agent drives the panel through the same routes. The complete list, with a sentence per route and the request and
response shapes, is the OpenAPI document at `/api/openapi.json` (`/api/docs` shows it; both need a signed-in session).

## 1. Answers

Every answer is JSON. A success carries `"ok": true` and the data. Every error, from every route, has one shape:

```json
{"ok": false, "error": "one sentence that says what went wrong and what to do", "code": "stable_slug"}
```

with the HTTP status that fits. `error` is for people, `code` is for programs:

| status | code | when |
|---|---|---|
| 400 | `bad_request` | the input is wrong (the sentence says which part) |
| 401 | `not_signed_in` | no session, or an ended one |
| 402 | `license_needed` | the feature is not in the licence |
| 403 | `forbidden`, `read_only`, `read_only_demo` | the panel is read-only, or the demo |
| 404 | `not_found` | no such thing (also every unknown path) |
| 409 | `conflict` | it already exists, or something else is running on it |
| 422 | `invalid` | the body or the query does not fit the model (`field: message; ...`) |
| 429 | `too_many_requests` | wrong sign-ins, or the demo's rate limit |
| 500 | `internal` | our bug; the answer and the log carry the same request id (`X-Request-Id`) |
| 502 | `upstream_failed` | a server, a panel, the probe or a DNS account did not answer |

A route may set a more specific `code`; the status classes above never change meaning.

## 2. Long work

A route that would take more than a few seconds does not block. It starts the work and answers at once:

```
POST /api/tunnels/health-all      ->  202 {"ok": true, "action_id": 812}
GET  /api/actions/812             ->  {"ok": true, "done": false, "story": ["Starting…", "…"], ...}
GET  /api/actions/812             ->  {"ok": true, "done": true, "ok": true|false, "story": [..., "the last line is the result"]}
```

`story` is the list of sentences the operator reads; the last line, once `done`, is the outcome. `source` says who started it (`manual`,
`auto`). Everything a repair, a build, a test or a check does on a server goes through this: it is logged, visible in the Tunnel Doctor's
History, and restartable after a restart of the panel. The page follows an action with `followAction(id)`.

Routes that read one thing from one server and answer within about ten seconds stay synchronous (a tunnel's health, a server's
sample, a panel's status); their description says so. Anything that touches every server, or several, is an action. An answer slower
than three seconds is written to the log as `slow …`, so the list of what should become an action is measured, not guessed.

The long routes added in 1.30 - 1.35 follow the same rule:

| Route | What it does |
|---|---|
| `POST /api/checkall` · `GET /api/checkall` · `POST /api/checkall/stop` | check every enabled server (answers, GRE through a temporary link, filtering in Iran); one action on the panel, so a reload of the page does not stop it; the status says `running`, `done`, `total` |
| `POST /api/servers/{id}/gre-probe` | does GRE work to this server: an Iran server builds a temporary GRE link, pings the inner address, removes the link (up to a minute, synchronous) |
| `POST /api/servers/{id}/install` | install on a server (Marzban node with the panel's certificate, PasarGuard node with its API key, the torrent block, the update of the system packages); an action; `GET /api/servers/{id}/pasarguard-cert` shows the node's public certificate |
| `POST /api/tunnels/builds/{action_id}/stop` | stop a build that runs: it rolls back what it made |
| `GET /api/besttoday?hours=24` | the tunnels ranked by how they behaved (availability, latency, jitter, loss, breaks); a job measures every tunnel again every half hour |
| `GET /api/logs?origin=panel\|server\|outside` · `GET /api/logs/summary?hours=24` | the log, with where each problem is; the last hours grouped |
| `GET /api/health/detail` | what is wrong with the panel itself: database, jobs, SSH key, free disk, last backup, errors of the last hour (`/api/health` stays the open liveness check) |
| `GET /api/settings/repair` · `PUT` | the Tunnel Doctor: `mode` off \| fix \| move, `after_seconds` (a tunnel down this long is repaired), `server_minutes` (an Iran server off this long is replaced, mode move) |
| `GET /api/doctor` | the Doctor's state: mode, the ledger of moves (what can be returned), the suggestions, which servers are back since when, the rails |
| `GET /api/doctor/plan/{server_id}` | what would happen if this Iran server went off: tunnels, target, the DNS records of every account (nothing changes) |
| `POST /api/doctor/moves/{id}/return` · `POST /api/doctor/moves/{id}/undo-dns` | bring the tunnels of a move back now (an action) · point only its DNS records back |
| `POST /api/doctor/suggestions/{id}/apply` · `/dismiss` | do a suggested move (an action) · drop it |
| `GET /api/accounts?search=&limit=` | the accounts that match a search on any Marzban / PasarGuard panel: the same person on every panel (same username, or a hand-made link), with the status of each panel; every panel is asked at that moment through its REST API |
| `GET /api/accounts/links` · `POST /api/accounts/links` · `DELETE /api/accounts/links/{id}` | the links made by hand between users with different names: list, make (each member must exist on its panel), undo (nothing changes on the panels) |
| `GET /api/outbound` | the outbound: the pool of exit configs (never their links) with how many Iran servers see each alive, the mode, the Iran servers that carry it with what the last check found, the forwards |
| `POST /api/outbound/configs` · `PATCH` / `DELETE /api/outbound/configs/{id}` | add exit configs (paste links, one per line; answers added / skipped with the reason), switch one on or off, remove one |
| `PUT /api/outbound/mode` · `PUT /api/outbound/gateways` | how the exits are used (spread / fast / auto) · the Iran servers that carry the outbound |
| `GET /api/outbound/suggest` · `POST /api/outbound/routes` · `/routes/auto` · `/routes/drop` · `PATCH /routes/{id}` | what the panels offer to forward to (nodes and inbound ports) · add forwards by hand or for chosen nodes x ports · remove · switch one on or off |
| `POST /api/outbound/routes/tunnels` · `GET /api/outbound/connect` · `POST /api/outbound/connect` · `POST /api/outbound/connect/undo` | a forward per port of every TCP tunnel (its own listen port on the Iran server) · the ArvanCloud records that would go through them · point the chosen records at the forwards (an action; nothing else changes, every change can be put back) · put them back on the tunnels |
| `PUT /api/outbound/hubs` · `POST /api/outbound/hubs/{id}/remove` · `PUT /api/outbound/users` | the foreign servers that run the relay behind the exits (Shadowsocks: an exit cannot read or re-route the traffic) · take a relay off its server · how the users are connected: manual, or auto (the panel points the records of a tunnel that stays down at the pool and puts them back when it is healthy) |
| `POST /api/outbound/apply` · `POST /api/outbound/check` · `POST /api/outbound/servers/{id}/remove` | put the pool and the forwards on every chosen Iran server (an action) · look at the pool from the Iran servers now · take it off one server |
| `POST /api/autopilot/move` | move one tunnel to an Iran server: built there first, the DNS records that serve only it follow (every account), the old one goes after the TTL |

## 3. Authentication

The browser signs in once and carries the `nikatel_session` cookie (24 h, two-step sign-in and a secret entry path when enabled).
Machine access (a bot, a script, an AI) uses an API token made under Settings → Security → API tokens (the admin password is asked;
the token is shown once; the panel keeps only its hash) and sent on every request as `Authorization: Bearer nkt_…`. Scopes:

| scope | may |
|---|---|
| `read` | every GET except the settings, the sign-in, the tokens, the log and the licence |
| `act` | `read`, plus the routes that start work: checks, repairs, builds, tests, DNS records |
| `admin` | everything, including settings, tokens (it may make tokens without a password), the log |

A token that is revoked stops at once. Every use is logged on the token (`last_used_at`); making and revoking are written to the
security log. A valid token passes the secret entry path; a wrong one sees the missing page like everyone else. The answer to a
request a token may not make is `403` with `"code": "token_scope"`.

## 4. Versions

`/api` is version 1 and stays compatible: fields are added, never renamed or removed; a route is never repurposed. A breaking change
ships beside the old one under `/api/v2/...` and the old route keeps answering for at least two minor releases. The panel's version is in
`GET /api/health` (no sign-in needed; liveness only) and in the `CHANGELOG`.

## 5. Rules for a new route

1. One sentence in the docstring: it is the description in the OpenAPI document (a check in CI fails without it).
2. A Pydantic model for the body; the response is a dict with `ok` first.
3. Errors are raised as `HTTPException(status, "sentence")`; the handler makes the shape. Never build the error dict by hand.
4. Work on a server goes through an action (`autopilot._start` or `actions.fix`), never inline in the route.
5. A test for the route in `tests/` and a line in `CHANGELOG.md`.
