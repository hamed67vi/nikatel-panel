# ADR 0004 - Public repository without infrastructure data

**Date:** 2026-10-09 · **Status:** accepted (until the panel is complete, then private)

## Context

The GitHub account is on the free plan; public repositories get unlimited CI minutes. The
owner wants CI and wants nothing about the real infrastructure to leak.

## Decision

* The repository is public while the panel is built, private afterwards.
* **Nothing identifying the infrastructure is committed**: no IP addresses, hostnames,
  provider names, domains, tokens, passwords, keys, inventories or database files. Tests use
  documentation addresses and generic names.
* `scripts/check_no_secrets.sh` scans every tracked file for public IPv4 addresses, known
  hostname patterns, token formats and PEM blocks; CI runs it plus `gitleaks` on every push;
  `deploy.sh` runs it before every deploy.
* Operational details that are specific to our servers live in the database on the panel
  server and in the operator's own notes, not in `docs/`.

## Consequences

* Docs say `<server>`; the real address is typed by the operator.
* Issues and PR descriptions follow the same rule (templates remind of it).
