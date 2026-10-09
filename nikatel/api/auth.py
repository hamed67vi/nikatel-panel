from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth
from ..log import get_logger

log = get_logger("auth")
router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    user: str
    password: str


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    if not auth.verify(body.user, body.password):
        log.warning("login failed user=%s ip=%s", body.user, request.client.host if request.client else "?")
        raise HTTPException(401, "wrong user or password")

    response.set_cookie(auth.COOKIE, auth.make_session(body.user), httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=auth.get_settings().session_hours * 3600)
    log.info("login ok user=%s", body.user)
    return {"ok": True, "user": body.user}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(user: str = Depends(auth.current_user)):
    return {"ok": True, "user": user}


@router.post("/auth/password")
def change_password(body: PasswordIn, user: str = Depends(auth.current_user)):
    if not auth.verify(user, body.current):
        raise HTTPException(400, "the current password is wrong")

    try:
        auth.set_password(body.new)
    except ValueError as e:
        raise HTTPException(400, str(e))

    log.info("password changed user=%s", user)
    return {"ok": True}
