"""One admin account. Password hashed with scrypt, session in a signed cookie.

Security features kept here (pure, tested):
* two-factor login with a time-based code (Google Authenticator and compatible apps, RFC 6238),
  one-time recovery codes, replay protection for the codes
* a session epoch: changing the password or the 2FA setup signs every other session out
* login throttling per client address (5 failures -> locked for 15 minutes)
* password reset by a code sent to Telegram, or by a recovery code
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import threading
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import Cookie, Header, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import get_settings
from .log import get_logger

log = get_logger("auth")
COOKIE = "nikatel_session"
GATE_COOKIE = "nikatel_gate"
MIN_PASSWORD = 10


def _auth_file() -> Path:
    return get_settings().data_dir / "auth.json"


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


_LOCK = threading.Lock()


def load() -> dict:
    try:
        return json.loads(_auth_file().read_text())
    except (OSError, ValueError):
        return {}


def save(d: dict) -> None:
    p = _auth_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.chmod(0o600)
    tmp.replace(p)


def ensure_admin() -> str | None:
    """Create the admin on first start; returns the generated password once, else None."""
    if _auth_file().exists():
        return None

    password = secrets.token_urlsafe(14)
    set_password(password)
    log.info("admin account created (first start)")
    return password


def check_strength(password: str, user: str = "admin") -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"the password needs at least {MIN_PASSWORD} characters")

    if password.lower() == user.lower() or password.lower() in ("password123", "1234567890", "qwertyuiop"):
        raise ValueError("this password is too easy to guess")


def set_password(password: str, logout_others: bool = True) -> None:
    """Store a new password. Keeps the 2FA / recovery settings, bumps the session epoch (every other session ends,
    unless `logout_others` is False), and removes the first-start password file."""
    check_strength(password)

    with _LOCK:
        d = load()
        salt = os.urandom(16)
        d.update({"user": "admin", "salt": salt.hex(), "hash": _hash(password, salt).hex(), "epoch": int(d.get("epoch", 0)) + (1 if logout_others else 0), "changed_at": int(time.time())})
        save(d)

    try:
        (get_settings().data_dir / "initial-admin-password.txt").unlink()
    except OSError:
        pass


_DUMMY = (b"\x00" * 16, _hash("dummy-password-for-timing", b"\x00" * 16))


def verify(user: str, password: str) -> bool:
    d = load()
    salt = bytes.fromhex(d["salt"]) if d.get("salt") else _DUMMY[0]
    want = bytes.fromhex(d["hash"]) if d.get("hash") else _DUMMY[1]
    ok = hmac.compare_digest(_hash(password, salt), want)        # always one scrypt: the time does not tell whether the user exists
    return bool(ok and user == d.get("user"))


def epoch() -> int:
    return int(load().get("epoch", 0))


# ------------------------------------------------------------------ sessions
def _serializer(salt: str = "session") -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key(), salt=salt)


def make_session(user: str) -> str:
    return _serializer().dumps({"u": user, "e": epoch()})


def current_user(request: Request, nikatel_session: str | None = Cookie(default=None), authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency: the signed-in user ("admin" from the browser, "token:<name>" from a machine), or 401."""
    if get_settings().demo:
        return "demo"           # the public demo shows itself without a sign-in (it holds invented data and refuses every change)

    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):          # a machine: an API token with a scope (docs/api.md section 3)
        from .services import tokens

        row = tokens.verify(authorization[7:])

        if row is None:
            raise HTTPException(401, "this API token is not valid (revoked, or never made here)")

        if not tokens.allowed(row["scope"], request.method, request.url.path):
            raise HTTPException(403, {"error": f"the token {row[chr(110) + chr(97) + chr(109) + chr(101)]!r} has the scope {row[chr(115) + chr(99) + chr(111) + chr(112) + chr(101)]}: it may not do this", "code": "token_scope"})

        request.state.token = row
        return "token:" + row["name"]

    if not nikatel_session:
        raise HTTPException(401, "not logged in")

    try:
        data = _serializer().loads(nikatel_session, max_age=get_settings().session_hours * 3600)
    except SignatureExpired:
        raise HTTPException(401, "session expired")
    except BadSignature:
        raise HTTPException(401, "bad session")

    if int(data.get("e", 0)) != epoch():
        raise HTTPException(401, "session ended (the password or the security settings changed)")

    return str(data.get("u", ""))


def session_user(request: Request) -> str | None:
    """The signed-in user of a request, or None - the same check as `current_user`, usable outside a dependency."""
    try:
        return current_user(request, request.cookies.get(COOKIE), request.headers.get("authorization"))
    except HTTPException:
        return None


def make_ticket(user: str) -> str:
    """Step between the password and the 2FA code: valid 5 minutes."""
    return _serializer("2fa").dumps({"u": user, "e": epoch()})


def read_ticket(ticket: str) -> str:
    try:
        data = _serializer("2fa").loads(ticket, max_age=300)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "this login step expired - sign in again")

    if int(data.get("e", 0)) != epoch():
        raise HTTPException(401, "this login step expired - sign in again")

    return str(data.get("u", ""))


# ------------------------------------------------------------------ throttling
_FAILS: dict[str, list[float]] = {}
LIMIT, WINDOW = 5, 15 * 60


def is_https(request: Request) -> bool:
    """The browser talks TLS: either to the panel itself or to the local reverse proxy in front of it (X-Forwarded-Proto, trusted from
    localhost only). The session and gate cookies carry the Secure flag then, so they never travel in clear."""
    if request.url.scheme == "https":
        return True

    peer = request.client.host if request.client else ""
    return _from_proxy(peer) and request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


def _from_proxy(peer: str) -> bool:
    """The request came from the local reverse proxy, or from one named in NIKATEL_TRUSTED_PROXIES."""
    return peer in ("127.0.0.1", "::1") or peer in {p.strip() for p in get_settings().trusted_proxies.split(",") if p.strip()}


def client_ip(request: Request) -> str:
    """The real client: X-Forwarded-For only when the request came from the local reverse proxy."""
    peer = request.client.host if request.client else "?"

    if _from_proxy(peer):
        xff = request.headers.get("x-forwarded-for", "")

        if xff:
            return xff.split(",")[0].strip()[:64]

    return peer


def throttle_check(ip: str) -> None:
    now = time.time()
    hits = [t for t in _FAILS.get(ip, []) if now - t < WINDOW]
    _FAILS[ip] = hits

    if len(hits) >= LIMIT:
        wait = int(WINDOW - (now - hits[0])) // 60 + 1
        raise HTTPException(429, f"too many wrong attempts - try again in {wait} min")


def throttle_fail(ip: str) -> None:
    _FAILS.setdefault(ip, []).append(time.time())

    if len(_FAILS) > 5000:       # never grow without bound
        for k in [k for k, v in _FAILS.items() if not v or time.time() - v[-1] > WINDOW]:
            _FAILS.pop(k, None)


def throttle_ok(ip: str) -> None:
    _FAILS.pop(ip, None)


# ------------------------------------------------------------------ TOTP (RFC 6238)
def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def totp_at(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def totp_check(secret: str, code: str, now: float | None = None, last_counter: int = 0) -> int | None:
    """The counter the code belongs to (window of +-1 step), or None. A counter at or below `last_counter` is refused (replay)."""
    code = "".join(ch for ch in str(code) if ch.isdigit())

    if len(code) != 6:
        return None

    cur = int((now if now is not None else time.time()) // 30)

    for c in (cur, cur - 1, cur + 1):
        if c > last_counter and hmac.compare_digest(totp_at(secret, c), code):
            return c

    return None


def totp_uri(secret: str, user: str = "admin", issuer: str = "Nikatel") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(user)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def totp_enabled() -> bool:
    t = load().get("totp") or {}
    return bool(t.get("enabled") and t.get("secret"))


def totp_begin() -> str:
    with _LOCK:
        d = load()
        secret = new_totp_secret()
        d["totp"] = {"secret": secret, "enabled": False, "last": 0}
        save(d)

    return secret


def totp_verify_login(code: str) -> bool:
    """Check a code against the enabled secret (replay-protected)."""
    with _LOCK:
        d = load()
        t = d.get("totp") or {}

        if not (t.get("enabled") and t.get("secret")):
            return False

        c = totp_check(t["secret"], code, last_counter=int(t.get("last", 0)))

        if c is None:
            return False

        t["last"] = c
        d["totp"] = t
        save(d)
        return True


def totp_enable(code: str) -> list[str]:
    """Confirm the first code from the app, switch 2FA on, return fresh one-time recovery codes. Other sessions end."""
    with _LOCK:
        d = load()
        t = d.get("totp") or {}

        if not t.get("secret") or t.get("enabled"):
            raise ValueError("start the set-up again")

        c = totp_check(t["secret"], code)

        if c is None:
            raise ValueError("that code is wrong - check the time on your phone and try again")

        t.update({"enabled": True, "last": c, "enabled_at": int(time.time())})
        d["totp"] = t
        codes = _new_recovery(d)
        d["epoch"] = int(d.get("epoch", 0)) + 1
        save(d)

    return codes


def totp_disable() -> None:
    with _LOCK:
        d = load()
        d.pop("totp", None)
        d["epoch"] = int(d.get("epoch", 0)) + 1
        save(d)


# ------------------------------------------------------------------ recovery codes
def _rc_hash(code: str) -> str:
    return hmac.new(get_settings().secret_key(), code.strip().lower().replace("-", "").encode(), hashlib.sha256).hexdigest()


def _new_recovery(d: dict) -> list[str]:
    raw = [secrets.token_hex(6) for _ in range(10)]                  # 48 bits each, single use
    d["recovery"] = [_rc_hash(c) for c in raw]
    return [c[:6] + "-" + c[6:] for c in raw]


def recovery_regenerate() -> list[str]:
    with _LOCK:
        d = load()
        codes = _new_recovery(d)
        save(d)

    return codes


def recovery_left() -> int:
    return len(load().get("recovery") or [])


def recovery_use(code: str) -> bool:
    """Consume one recovery code."""
    h = _rc_hash(code)

    with _LOCK:
        d = load()
        left = list(d.get("recovery") or [])
        hit = next((x for x in left if hmac.compare_digest(x, h)), None)

        if hit is None:
            return False

        left.remove(hit)
        d["recovery"] = left
        save(d)
        return True


# ------------------------------------------------------------------ password reset by a code sent to Telegram
_RESET: dict = {}


def reset_code_issue() -> str:
    code = "".join(secrets.choice("0123456789") for _ in range(8))
    _RESET.clear()
    _RESET.update({"hash": hashlib.sha256(code.encode()).hexdigest(), "until": time.time() + 600, "tries": 0})
    return code


def reset_code_use(code: str) -> bool:
    if not _RESET or time.time() > _RESET["until"] or _RESET["tries"] >= 5:
        _RESET.clear()
        return False

    _RESET["tries"] += 1

    if hmac.compare_digest(_RESET["hash"], hashlib.sha256(str(code).strip().encode()).hexdigest()):
        _RESET.clear()
        return True

    return False


def disable_two_factor_cli() -> None:
    totp_disable()
