# The way forward: stable first, API-first, features later

Decided 2026-10-12. This document is the rule book for the next phase. Everything new that is not on this list goes to the
roadmap as a parked idea (milestone M11) and is **not built now**.

## Why

The panel does a lot. What makes it a product is not the next feature: it is that what exists is stable, that every part of it
can be driven without a human in front of a browser, and that someone else can read, test and change the code without breaking
it. So the order is: (1) a clean contract, (2) depth of testing and operations, (3) a pilot with real operators, (4) only then
new features, each on the same contract.

## Principles

1. **API-first.** Every capability is an API route with a documented request and response. The web UI is one client of that
   API and does nothing the API does not offer. An external client (a Telegram bot, an AI agent, a script) must be able to do
   everything the UI can, with a token, without ever reading the UI.
2. **One contract.** One error shape, one pattern for long-running work (an action id and its story), one way to authenticate a
   machine, one place where the whole API is described (the OpenAPI document). Breaking changes only behind a new version prefix.
3. **Nothing changes a server without a story.** Every action that touches a server is named, logged with its steps, rate
   limited, dry-runnable, and reversible or at least restartable. This stays the invariant of the product.
4. **Tested where it matters.** The pure logic (judging, planning, parsing, deciding) is covered by unit tests; the server-touching
   layer is covered by fakes; the whole install/upgrade path is run in CI on a clean machine.
5. **Boring operations.** Install, upgrade, backup, restore, rollback, logs and health are scripted, documented and rehearsed.
6. **Future in sight, not in the code.** Ideas are written down as issues so the design leaves room for them (hooks, tables,
   naming), but they are not implemented until the base is stable.

## Where we are (measured 2026-10-12)

| what | now |
|---|---|
| API routes | 281 (the UI makes 317 calls to them: the UI is already a client of the API) |
| routes with a description in the OpenAPI document | 45 of 281 |
| error shapes | two: FastAPI's `{"detail": ...}` on 130 raises, `{"ok": false, "error": ...}` on the 500 handler and a few routes |
| machine authentication | none: only the browser session cookie (a script must mint a session with the server's own key) |
| long-running work | one good pattern exists (action id + story, polled at `/api/actions/{id}`) but not every long route uses it |
| front end | one file of 3,934 lines plus the analyst file; CI syntax-checks only the first |
| tests | 36 files, ~300 tests, all pure logic; coverage not measured; no install/upgrade test in CI |
| operations | install script, deploy with rollback, encrypted backup/restore, log viewer; no self-check, no "what is wrong" in health |

## Phases

### Phase 1 · The contract (API-first)
- **Error shape.** Every API answer that is not a success is `{"ok": false, "error": "<one sentence>", "code": "<stable_slug>"}`
  with the right HTTP status. One exception handler does it; the routes only raise. The UI reads `error`.
- **Machine tokens.** Settings → Security → API tokens: name, scope (`read`, `act`, `admin`), shown once, stored hashed, revocable,
  every use logged with the token's name. `Authorization: Bearer <token>`. The session cookie stays for the browser only.
- **Long work.** Every route that takes more than a few seconds answers `202 {"ok": true, "action_id": N}` and the client follows
  `/api/actions/{id}` (story, done, ok). No route blocks for a minute.
- **The manual is the OpenAPI document.** Every route gets a one-sentence description and typed request/response models. A CI
  check fails on a route without a description. `docs/api.md` explains the three patterns above in one page.
- **Versioning policy.** `/api` is v1 and stays compatible; a breaking change ships under `/api/v2` beside it, never in place.

### Phase 2 · Depth (stability)
- Coverage measured in CI and shown; the pure helpers of every service covered; the SSH layer behind one fake in tests.
- Install on a clean container in CI; upgrade from the previous tag to this one with data intact (migrations really run).
- `/api/health` says what is wrong (database, a job that is late, the SSH key, disk, the probe) and the panel self-checks on start.
- Every JS file syntax-checked; the front end split one file per page (no build step), each page only talking to the API.
- Operations rehearsed: backup → restore on a fresh machine, documented with timings; rollback of a bad release documented.

### Phase 3 · Pilot
- A release (tag, GitHub release, checksums) installed by ten operators on their own servers from the README alone.
- A "Report a problem" page: version, the last log lines with secrets removed, the health answer - copied into an issue.
- One month: no new features, only fixes. Exit: a week without a new bug report that is not a feature request.

### Phase 4 · Features, one at a time
Each from the parked list (M11), each with: API route + description + tests + docs + changelog, in that order.

## Definition of done (every change from now on)
- the behaviour is reachable through the API and described in the OpenAPI document
- tests for the pure part, a fake for the server-touching part
- docs and CHANGELOG updated, no real address or name in the repository
- the deploy's own checks pass (lint, JS syntax, secrets, tests, health) before the commit exists
