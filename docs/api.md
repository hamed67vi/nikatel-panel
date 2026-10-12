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
