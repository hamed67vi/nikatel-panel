"""One admin account. Password hashed with scrypt, session in a signed cookie."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from fastapi import Cookie, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import get_settings
from .log import get_logger

log = get_logger("auth")
COOKIE = "nikatel_session"


def _auth_file() -> Path:
    return get_settings().data_dir / "auth.json"


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def ensure_admin() -> str | None:
    """Create the admin on first start; returns the generated password once, else None."""
    p = _auth_file()

    if p.exists():
        return None

    password = secrets.token_urlsafe(12)
    set_password(password)
    log.info("admin account created (first start)")
    return password


def set_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("the password needs at least 8 characters")

    salt = os.urandom(16)
    p = _auth_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"user": "admin", "salt": salt.hex(), "hash": _hash(password, salt).hex()}))
    p.chmod(0o600)


def verify(user: str, password: str) -> bool:
    try:
        d = json.loads(_auth_file().read_text())
    except (OSError, ValueError):
        return False

    if user != d.get("user"):
        return False

    return hmac.compare_digest(_hash(password, bytes.fromhex(d["salt"])), bytes.fromhex(d["hash"]))


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key(), salt="session")


def make_session(user: str) -> str:
    return _serializer().dumps({"u": user})


def current_user(request: Request, nikatel_session: str | None = Cookie(default=None)) -> str:
    """FastAPI dependency: the logged-in user, or 401."""
    if not nikatel_session:
        raise HTTPException(401, "not logged in")

    try:
        data = _serializer().loads(nikatel_session, max_age=get_settings().session_hours * 3600)
    except SignatureExpired:
        raise HTTPException(401, "session expired")
    except BadSignature:
        raise HTTPException(401, "bad session")

    return str(data.get("u", ""))
