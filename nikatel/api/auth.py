from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from ..db import session
from ..log import get_logger
from ..services import gate
from ..services import settings as st
from ..services.events import emit
from ..services.telegram import send_text

log = get_logger("auth")
router = APIRouter(tags=["auth"])


def _audit(level: str, text: str) -> None:
    with session() as db:
        emit(db, level, "security", text, "")


def _cookie(request: Request, response: Response, user: str) -> None:
    response.set_cookie(auth.COOKIE, auth.make_session(user), httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=auth.get_settings().session_hours * 3600)


class LoginIn(BaseModel):
    user: str = Field(max_length=64)
    password: str = Field(max_length=256)


class CodeIn(BaseModel):
    ticket: str = Field(max_length=600)
    code: str = Field(max_length=40)


class PasswordIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


class ResetIn(BaseModel):
    code: str = Field(max_length=40)
    new: str = Field(max_length=256)


class PwCodeIn(BaseModel):
    password: str = Field(max_length=256)
    code: str = Field("", max_length=40)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = auth.client_ip(request)
    auth.throttle_check(ip)

    if not auth.verify(body.user, body.password):
        auth.throttle_fail(ip)
        log.warning("login failed user=%s ip=%s", body.user[:32], ip)
        _audit("warn", f"Failed sign-in as {body.user[:32]!r} from {ip}.")
        raise HTTPException(401, "wrong user or password")

    if auth.totp_enabled():            # password right: the second step decides
        return {"ok": True, "need_2fa": True, "ticket": auth.make_ticket(body.user)}

    auth.throttle_ok(ip)
    _cookie(request, response, body.user)
    _audit("info", f"Signed in from {ip}.")
    return {"ok": True, "user": body.user}


@router.post("/auth/login/2fa")
def login_2fa(body: CodeIn, request: Request, response: Response):
    ip = auth.client_ip(request)
    auth.throttle_check(ip)
    user = auth.read_ticket(body.ticket)
    code = body.code.strip()

    if auth.totp_verify_login(code):
        how = "authenticator code"
    elif len(code.replace("-", "")) == 12 and auth.recovery_use(code):
        how = f"recovery code ({auth.recovery_left()} left)"
    else:
        auth.throttle_fail(ip)
        _audit("warn", f"Wrong second-step code from {ip}.")
        raise HTTPException(401, "wrong code")

    auth.throttle_ok(ip)
    _cookie(request, response, user)
    _audit("info", f"Signed in from {ip} with a {how}.")
    return {"ok": True, "user": user}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(user: str = Depends(auth.current_user)):
    return {"ok": True, "user": user}


@router.post("/auth/password")
def change_password(body: PasswordIn, request: Request, response: Response, user: str = Depends(auth.current_user)):
    ip = auth.client_ip(request)
    auth.throttle_check(ip)

    if not auth.verify(user, body.current):
        auth.throttle_fail(ip)
        raise HTTPException(400, "the current password is wrong")

    try:
        auth.set_password(body.new)
    except ValueError as e:
        raise HTTPException(400, str(e))

    _cookie(request, response, user)       # this browser stays signed in, every other session ends
    _audit("warn", f"The password was changed (from {ip}); every other session was signed out.")
    return {"ok": True}


# ------------------------------------------------------------------ forgotten password
@router.get("/auth/recovery-options")
def recovery_options():
    """What the login page may offer. Says nothing about the account itself."""
    t = st.get("telegram")
    return {"ok": True, "telegram": bool(t.get("enabled") and t.get("token") and t.get("chat_id")), "recovery_codes": True}


@router.post("/auth/recover/telegram")
def recover_telegram(request: Request):
    ip = auth.client_ip(request)
    auth.throttle_check(ip)
    auth.throttle_fail(ip)             # asking for a code counts as an attempt: no flooding of your chat
    code = auth.reset_code_issue()
    ok, why = send_text(f"Nikatel password reset code: {code}\nValid 10 minutes. Someone asked for it from {ip}. Ignore this message if it was not you.")

    if not ok:
        raise HTTPException(400, "Telegram is not set up (" + why + "). Use a recovery code, or run `nikatel set-password` on the server.")

    _audit("warn", f"A password reset code was sent to Telegram (asked from {ip}).")
    return {"ok": True}


@router.post("/auth/recover")
def recover(body: ResetIn, request: Request):
    """Set a new password with a Telegram code or one of the recovery codes. Two-factor stays on."""
    ip = auth.client_ip(request)
    auth.throttle_check(ip)

    try:
        auth.check_strength(body.new)       # before the code is used up: a weak password must not burn a recovery code
    except ValueError as e:
        raise HTTPException(400, str(e))

    code = body.code.strip()
    clean = code.replace("-", "")
    good = (len(clean) == 8 and clean.isdigit() and auth.reset_code_use(clean)) or (len(clean) == 12 and auth.recovery_use(code))

    if not good:
        auth.throttle_fail(ip)
        _audit("warn", f"A password reset with a wrong code was tried from {ip}.")
        raise HTTPException(400, "wrong or used code")

    try:
        auth.set_password(body.new)
    except ValueError as e:
        raise HTTPException(400, str(e))

    auth.throttle_ok(ip)
    _audit("bad", f"The password was reset with a recovery code from {ip}. Every session was signed out.")
    return {"ok": True}


# ------------------------------------------------------------------ security settings (signed in)
@router.get("/auth/security")
def security(user: str = Depends(auth.current_user)):
    d = auth.load()
    t = d.get("totp") or {}
    return {"ok": True, "totp_enabled": auth.totp_enabled(), "totp_pending": bool(t.get("secret") and not t.get("enabled")), "recovery_left": auth.recovery_left(),
            "password_changed_at": d.get("changed_at"), "entry_path": gate.entry_path(), "min_password": auth.MIN_PASSWORD}


@router.post("/auth/2fa/setup")
def totp_setup(body: PwCodeIn, request: Request, user: str = Depends(auth.current_user)):
    ip = auth.client_ip(request)
    auth.throttle_check(ip)

    if not auth.verify(user, body.password):
        auth.throttle_fail(ip)
        raise HTTPException(400, "the password is wrong")

    if auth.totp_enabled():
        raise HTTPException(400, "two-factor sign-in is already on")

    secret = auth.totp_begin()
    uri = auth.totp_uri(secret, user)
    svg = None

    try:
        import qrcode
        import qrcode.image.svg

        svg = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2).to_string().decode()
    except Exception:  # noqa: BLE001  the QR is a convenience; the key can be typed in
        svg = None

    return {"ok": True, "secret": secret, "uri": uri, "qr_svg": svg}


@router.post("/auth/2fa/enable")
def totp_enable(body: PwCodeIn, request: Request, response: Response, user: str = Depends(auth.current_user)):
    ip = auth.client_ip(request)
    auth.throttle_check(ip)

    try:
        codes = auth.totp_enable(body.code)
    except ValueError as e:
        auth.throttle_fail(ip)
        raise HTTPException(400, str(e))

    _cookie(request, response, user)
    _audit("warn", f"Two-factor sign-in was switched on (from {ip}).")
    return {"ok": True, "recovery_codes": codes}


@router.post("/auth/2fa/disable")
def totp_disable(body: PwCodeIn, request: Request, response: Response, user: str = Depends(auth.current_user)):
    ip = auth.client_ip(request)
    auth.throttle_check(ip)

    if not auth.verify(user, body.password) or not (auth.totp_verify_login(body.code) or auth.recovery_use(body.code)):
        auth.throttle_fail(ip)
        raise HTTPException(400, "the password or the code is wrong")

    auth.totp_disable()
    _cookie(request, response, user)
    _audit("bad", f"Two-factor sign-in was switched OFF (from {ip}).")
    return {"ok": True}


@router.post("/auth/recovery/regenerate")
def recovery_regenerate(body: PwCodeIn, request: Request, user: str = Depends(auth.current_user)):
    ip = auth.client_ip(request)
    auth.throttle_check(ip)

    if not auth.verify(user, body.password):
        auth.throttle_fail(ip)
        raise HTTPException(400, "the password is wrong")

    _audit("warn", f"New recovery codes were made (from {ip}); the old ones no longer work.")
    return {"ok": True, "recovery_codes": auth.recovery_regenerate()}


class EntryIn(BaseModel):
    path: str = Field("", max_length=64)
    password: str = Field(max_length=256)


@router.put("/auth/entry-path")
def entry_path(body: EntryIn, request: Request, response: Response, user: str = Depends(auth.current_user)):
    ip = auth.client_ip(request)
    auth.throttle_check(ip)

    if not auth.verify(user, body.password):
        auth.throttle_fail(ip)
        raise HTTPException(400, "the password is wrong")

    try:
        path = gate.set_entry_path(body.path)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if path:       # this browser keeps working: it gets the new gate cookie now
        response.set_cookie(auth.GATE_COOKIE, gate.make_cookie(path), httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=30 * 86400, path="/")

    _audit("warn", "The secret entry path was " + ("changed." if path else "removed: the panel answers on / again."))
    return {"ok": True, "path": path}


@router.get("/auth/security-log")
def security_log(user: str = Depends(auth.current_user)):
    from sqlalchemy import select

    from ..models import Event

    with session() as db:
        rows = db.scalars(select(Event).where(Event.domain == "security").order_by(Event.id.desc()).limit(15))
        return {"ok": True, "events": [{"ts": e.ts, "level": e.level, "text": e.text} for e in rows]}
