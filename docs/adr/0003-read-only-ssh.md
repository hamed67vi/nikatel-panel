# ADR 0003 - Own SSH key, named read-only scripts

**Date:** 2026-10-09 · **Status:** accepted

## Context

The panel looks at ~40 servers over SSH as root. A bug or an injection in a command could take
down the tunnels users depend on.

## Decision

* The new panel has **its own key**, added to every server. Revoking it = removing one line in
  `authorized_keys`; the old panel's key is untouched.
* Only **named scripts** in `nikatel.ssh.SCRIPTS` run. Parameters are substituted after a regex
  check (unit names, interface names, IPv4, port lists). No other code builds shell commands.
* `run_raw` exists for future *actions* but raises while `NIKATEL_READ_ONLY=true`. Read-only
  can only be turned off in the server's env file, never from the UI.
* Known hosts are pinned in the panel's own file; a changed host key is an error the operator
  clears by hand.

## Consequences

* Observing something new = adding a script with validation, visible in the PR.
* Phase 3 introduces *actions*: named, logged, dry-run, reversible, rate-limited. They will
  not use `run_raw` from handlers either.
