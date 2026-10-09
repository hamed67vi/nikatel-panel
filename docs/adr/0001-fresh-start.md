# ADR 0001 - A fresh project beside the single-file panel

**Date:** 2026-10-09 · **Status:** accepted

## Context

The existing panel is one ~55,000-line Python file (450 functions, a 1.1 MB HTML/CSS/JS string,
99 routes in a 6,400-line `do_POST`) built as a snapshot plus ~140 text patches kept outside
version control. No logging framework, ~185 silent `except Exception`. It works and carries
the production tunnels.

## Decision

Build a new panel from scratch beside it with a proper architecture, and migrate
responsibilities phase by phase (`docs/roadmap.md`). The old panel is not modified; its
inventory is imported read-only. The new panel starts in read-only mode so it cannot affect
the tunnels.

## Consequences

* Two panels for a while; the old one stays authoritative for changes until a phase hands a
  job over explicitly.
* Every ported behaviour is re-derived from what the servers actually do (unit names,
  interfaces, scripts), not copied from the old code.
* Slower start, much faster after the foundation is in.
