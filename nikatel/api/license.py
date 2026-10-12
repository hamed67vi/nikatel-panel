from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import license as lic

router = APIRouter(tags=["license"], dependencies=[Depends(current_user)])


class TokenIn(BaseModel):
    token: str = Field("", max_length=4000)


@router.get("/license")
def license_get():
    """The mode (owner / licensed / free), the features that are on, the install id a token can be bound to."""
    return {"ok": True, **lic.status(), "all_features": lic.FEATURES, "free_kinds": sorted(lic.FREE_TUNNEL_KINDS)}


@router.put("/license")
def license_put(body: TokenIn):
    """Keep a token (checked first); an empty token removes the license."""
    try:
        return {"ok": True, **lic.set_token(body.token)}
    except ValueError as e:
        raise HTTPException(400, str(e))
