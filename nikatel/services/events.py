"""Events: the sentences the operator reads. Emit one when something changes, not on every check."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..log import get_logger
from ..models import Event

log = get_logger("events")


def emit(db: Session, level: str, domain: str, text: str, ref: str = "") -> Event:
    ev = Event(level=level, domain=domain, text=text[:900], ref=ref)
    db.add(ev)
    getattr(log, {"bad": "warning", "warn": "warning"}.get(level, "info"))("%s %s", domain, text)      # what happens to servers, tunnels and configs is a finding, not a failure of the panel: never ERROR
    return ev
