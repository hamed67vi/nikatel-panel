"""The secret entry path. With `security.entry_path` set, the panel looks like it does not exist:
every URL answers 404 until the browser has opened /<entry_path> once; that visit sets a signed gate
cookie (valid 30 days, bound to the current path) and only then the login page, the static files and the API
answer. Requests that come straight from this machine (no reverse proxy header) are never gated: the
deploy health check and the local smoke test use them."""
from __future__ import annotations

import hashlib
import re
import time

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .. import auth
from ..config import get_settings
from . import settings as st

PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
_CACHE = {"at": 0.0, "path": ""}


def entry_path() -> str:
    if time.time() - _CACHE["at"] > 5:
        _CACHE["path"] = str(st.get("security").get("entry_path") or "")
        _CACHE["at"] = time.time()

    return _CACHE["path"]


def set_entry_path(path: str) -> str:
    path = str(path or "").strip().strip("/")

    if path and not PATH_RE.match(path):
        raise ValueError("the path needs 8-64 characters: letters, digits, - and _")

    if path in ("api", "static", "docs", "health"):
        raise ValueError("this word is reserved")

    st.put("security", {"entry_path": path})
    _CACHE["at"] = 0.0
    return path


def _ser() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key(), salt="gate")


def _tag(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:16]


def make_cookie(path: str) -> str:
    return _ser().dumps({"g": _tag(path)})


def cookie_ok(value: str | None, path: str) -> bool:
    if not value:
        return False

    try:
        data = _ser().loads(value, max_age=30 * 86400)
    except (BadSignature, SignatureExpired):
        return False

    return data.get("g") == _tag(path)


def is_local_direct(request: Request) -> bool:
    peer = request.client.host if request.client else ""
    return peer in ("127.0.0.1", "::1") and not request.headers.get("x-forwarded-for") and not request.headers.get("x-real-ip")


def allowed(request: Request) -> bool:
    """True when the request may see the panel at all."""
    path = entry_path()

    if not path or is_local_direct(request):
        return True

    return cookie_ok(request.cookies.get(auth.GATE_COOKIE), path)
