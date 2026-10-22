
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from ..auth import current_user
from ..db import session
from ..models import Event

router = APIRouter(tags=["events"], dependencies=[Depends(current_user)])


@router.get("/events")
def list_events(limit: int = Query(100, ge=1, le=500), domain: str | None = Query(None, max_length=32), level: str | None = Query(None, max_length=8), after: int = Query(0, ge=0)):
    """The events the operator reads (what happened to servers, tunnels, configs, repairs), newest first; filters by domain, level and id."""
    with session() as db:
        q = select(Event).order_by(Event.id.desc()).limit(limit)       # (`limit=-1` used to be SQLite's "no limit": the whole table, 19,000 rows on the live panel)

        if domain:
            q = q.where(Event.domain == domain)

        if level:
            q = q.where(Event.level == level)

        if after:
            q = q.where(Event.id > after)

        total = db.scalar(select(func.count(Event.id)).where(*(([Event.domain == domain] if domain else []) + ([Event.level == level] if level else []) + ([Event.id > after] if after else [])))) or 0

        return {"ok": True, "total": total, "events": [{"id": e.id, "ts": e.ts, "level": e.level, "domain": e.domain, "text": e.text, "ref": e.ref}
                                       for e in db.scalars(q)]}
