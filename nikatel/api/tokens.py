from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import auth
from ..db import session
from ..services import tokens
from ..services.events import emit

router = APIRouter(tags=["tokens"], dependencies=[Depends(auth.current_user)])


class TokenIn(BaseModel):
    name: str = Field(max_length=40)
    scope: str = Field(pattern=r"^(read|act|admin)$")
    password: str = Field("", max_length=256)       # the admin password, when the request comes from the browser (a token of scope admin needs no password)


def _audit(text: str) -> None:
    with session() as db:
        emit(db, "info", "security", text, "")


@router.get("/tokens")
def tokens_list():
    """The live API tokens: name, scope, the first characters, when made and last used. The token itself is never shown again."""
    return {"ok": True, "tokens": tokens.rows(), "scopes": tokens.SCOPES}


@router.post("/tokens", status_code=201)
def tokens_create(body: TokenIn, request: Request, user: str = Depends(auth.current_user)):
    """Make an API token (shown once in the answer). From the browser the admin password is required; an admin token may make tokens without it."""
    if not user.startswith("token:"):
        ip = auth.client_ip(request)
        auth.throttle_check(ip)

        if not auth.verify("admin", body.password):
            auth.throttle_fail(ip)
            raise HTTPException(400, "the password is wrong")

        auth.throttle_ok(ip)

    try:
        token, row = tokens.create(body.name, body.scope, by=user)
    except ValueError as e:
        raise HTTPException(400, str(e))

    _audit(f"API token “{row['name']}” ({row['scope']}) made by {user}.")
    return {"ok": True, "token": token, **row}


@router.delete("/tokens/{token_id}")
def tokens_revoke(token_id: int, user: str = Depends(auth.current_user)):
    """Revoke a token: it stops working at once."""
    try:
        row = tokens.revoke(token_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    _audit(f"API token “{row['name']}” revoked by {user}.")
    return {"ok": True, **row}
