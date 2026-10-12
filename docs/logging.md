# Logging

**Goal:** a problem reported by the operator traces to one request or one job and one log line
in under a minute; a job that fails silently does not exist; logs never contain secrets.

## Format

```
ts(UTC)               LEVEL   domain     message key=value …          rid
2026-10-09T17:26:48Z  INFO    tunnels    check name=t1 state=up rtt=81.2 rid=job:tunnels
```

* `domain` is the logger name: `http`, `auth`, `servers`, `tunnels`, `ssh`, `jobs`, `db`, `events`, `import`.
* `rid` is the request id (6 hex chars, also sent as `X-Request-Id` and shown by the UI on a
  500) or `job:<name>` for background work. It is a `contextvar`, set by the middleware /
  `jobs.run_once`, so every line inside carries it automatically.

## Levels

| | |
|---|---|
| DEBUG | each SSH call, parsed outputs. `NIKATEL_LOG_LEVEL=DEBUG`. |
| INFO | state changes: sample ok, check result, login, migration, job start. |
| WARNING | degraded but working: a server did not answer, a 4xx on the API, a job slower than its interval. |
| ERROR | a job failed, an unhandled exception in a request. Always with `exc_info`. Never an event: what happens to servers, tunnels and configs is written at WARNING / INFO, so ERROR means the panel itself. |

## Sinks

stdout → journald (`journalctl -u nikatel`), and a rotating file `<log_dir>/nikatel.log`
(10 MB × 14) for the in-panel viewer (M1 #14).

## Events vs logs

An **Event** (`events` table, Dashboard → Activity) is a sentence for the operator, written on a
*transition* only. A **log line** is for whoever debugs, written on every step. `services.events.emit`
writes both.

## Secrets

`log.mask()` runs on every message and traceback: Telegram bot tokens, `sk-ant-…`, bearer
tokens, `password=`/`token=` values and PEM blocks become `<hidden>`. `tests/test_ssh_and_log.py`
asserts it. Do not log request bodies.

## Error policy

* Narrow `except` where the type is known; `except Exception` only at the two boundaries
  (`jobs.run_once`, the HTTP middleware), always logged.
* `except Exception: pass` does not exist in this codebase. Minimum is `log.debug(..., exc_info=True)`.
* API errors: `HTTPException(code, "<plain sentence>")`. 500s are logged with `rid`; the
  browser gets `{"ok": false, "error": "internal error", "rid": "…"}`.

## Reading a problem: the panel, your server, or outside?

`services/diagnosis.py` classifies every sentence the panel writes about a failure (a log line, the story of a repair) into one origin, with a plain hint:

| origin | meaning | example |
|---|---|---|
| `outside` | the panel works; something it reaches does not answer | ssh connection timed out, the route between two servers cut, a probe test that timed out, a DNS provider answering 502 |
| `server` | a service on one of your servers stopped or is blocked | tunnel units not running, the control port closed, the GRE interface missing |
| `panel` | the panel itself | a bug (`internal error` with a request id), a locked database, read-only mode, being busy with another job, its own safety limits |
| `unknown` | no rule matched: nothing is guessed, the full text is shown | |

A repair's `summary` (in `GET /api/actions/{id}` and the History) has the problem, what was tried, the result, and the origin with its hint. The Tunnel Doctor shows two lists: **In progress** and **Finished** (fixed, or could not be fixed and why).
