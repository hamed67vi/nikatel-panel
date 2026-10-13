"""The public merged subscription link (roadmap #83): /sub/<token>. The token is signed (see services/accounts.py), so it needs no login and no door cookie;
a wrong or damaged token sees the same missing page as any unknown address."""
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from .. import auth
from ..services import accounts

router = APIRouter(tags=["subscription"])

_HITS: dict[str, list[float]] = {}
_LOCK = threading.Lock()
LIMIT, WINDOW = 30, 60.0         # a client app asks once an hour; a person pasting it a few times is fine; a loop is not


def _too_many(ip: str) -> bool:
    now = time.time()

    with _LOCK:
        hits = [t for t in _HITS.get(ip, []) if now - t < WINDOW]
        hits.append(now)
        _HITS[ip] = hits

        if len(_HITS) > 2000:
            for k in [k for k, v in _HITS.items() if not v or now - v[-1] > WINDOW]:
                _HITS.pop(k, None)

        return len(hits) > LIMIT


@router.get("/sub/{token}", include_in_schema=False)
def merged_subscription(token: str, request: Request):
    """One subscription for one person on several panels: the links of every panel, each once, as the base64 list every client app reads."""
    if _too_many(auth.client_ip(request)):
        return PlainTextResponse("Too many requests", status_code=429, headers={"Retry-After": "30"})

    got = accounts.serve_sub(token)

    if got is None:
        return PlainTextResponse("Not Found", status_code=404)

    body, info, answered, total = got

    if not answered:
        return PlainTextResponse("no panel answered", status_code=502, headers={"Retry-After": "30"})

    return PlainTextResponse(body, headers={"subscription-userinfo": info, "profile-update-interval": "1", "X-Panels": f"{answered}/{total}", "Cache-Control": "no-store"})