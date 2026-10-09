
from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Event

router = APIRouter(tags=["events"], dependencies=[Depends(current_user)])


@router.get("/events")
def list_events(limit: int = 100, domain: str | None = None, level: str | None = None, after: int = 0):
    with session() as db:
        q = select(Event).order_by(Event.id.desc()).limit(min(limit, 500))

        if domain:
            q = q.where(Event.domain == domain)

        if level:
            q = q.where(Event.level == level)

        if after:
            q = q.where(Event.id > after)

        return {"ok": True, "events": [{"id": e.id, "ts": e.ts, "level": e.level, "domain": e.domain, "text": e.text, "ref": e.ref}
                                       for e in db.scalars(q)]}
