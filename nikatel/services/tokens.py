"""API tokens: the sign-in of a machine (a script, a Telegram bot, an AI agent) - docs/api.md section 3.

A token is `nkt_` plus 40 random characters, shown ONCE when it is made; the panel keeps only its SHA-256. Each token has a scope:
`read` (GET only), `act` (also the routes that start work on servers), `admin` (everything, including settings, tokens, sign-in and the log).
A token can be revoked; every use moves `last_used_at` (at most once a minute). `allowed` is pure and tested."""
from __future__ import annotations

import hashlib
import secrets
import time

from sqlalchemy import select

from ..db import session
from ..models import ApiToken

SCOPES = ("read", "act", "admin")
PREFIX = "nkt_"
ADMIN_PATHS = ("/api/auth", "/api/tokens", "/api/settings", "/api/license", "/api/system", "/api/logs", "/api/dns/accounts", "/api/shopbot/query", "/api/shopbot/table")
_LAST_USED: dict[int, float] = {}


def allowed(scope: str, method: str, path: str) -> bool:
    """What a token of this scope may do with this request. Pure."""
    if scope == "admin" or path in ("/api/auth/me", "/api/health"):          # every token may ask who it is
        return True

    if any(path == p or path.startswith(p + "/") for p in ADMIN_PATHS):
        return False

    if scope == "read":
        return method.upper() in ("GET", "HEAD", "OPTIONS")

    return scope == "act"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public(t: ApiToken) -> dict:
    return {"id": t.id, "name": t.name, "scope": t.scope, "prefix": t.prefix, "created_at": t.created_at, "created_by": t.created_by,
            "last_used_at": t.last_used_at, "revoked_at": t.revoked_at}


def rows() -> list[dict]:
    with session() as db:
        return [public(t) for t in db.scalars(select(ApiToken).where(ApiToken.revoked_at == 0).order_by(ApiToken.id))]


def create(name: str, scope: str, by: str = "admin") -> tuple[str, dict]:
    """Make a token; returns (the token - the only time it exists in clear -, its public row)."""
    name = str(name or "").strip()[:40]

    if not name:
        raise ValueError("give the token a name (what will use it)")

    if scope not in SCOPES:
        raise ValueError("the scope must be read, act or admin")

    token = PREFIX + secrets.token_urlsafe(30)

    with session() as db:
        if db.scalar(select(ApiToken).where(ApiToken.name == name, ApiToken.revoked_at == 0)) is not None:
            raise ValueError("a token with this name exists; revoke it first or choose another name")

        t = ApiToken(name=name, scope=scope, token_hash=_hash(token), prefix=token[:10], created_by=by)
        db.add(t)
        db.flush()
        row = public(t)

    return token, row


def revoke(token_id: int) -> dict:
    with session() as db:
        t = db.get(ApiToken, token_id)

        if t is None or t.revoked_at:
            raise ValueError("no such token")

        t.revoked_at = int(time.time())
        return public(t)


def verify(token: str) -> dict | None:
    """The row of a live token, or None. Touches last_used_at at most once a minute."""
    token = str(token or "").strip()

    if not token.startswith(PREFIX) or len(token) < 20:
        return None

    h = _hash(token)

    with session() as db:
        t = db.scalar(select(ApiToken).where(ApiToken.token_hash == h, ApiToken.revoked_at == 0))

        if t is None:
            return None

        now = time.time()

        if now - _LAST_USED.get(t.id, 0) > 60:
            _LAST_USED[t.id] = now
            t.last_used_at = int(now)

        return public(t)
