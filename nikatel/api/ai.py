import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..services import ai
from ..services import settings as st

router = APIRouter(tags=["ai"], dependencies=[Depends(current_user)])


class AiIn(BaseModel):
    enabled: bool = False
    api_key: str = ""
    model: str = "claude-sonnet-5-5"
    actions: bool = True


@router.get("/settings/ai")
def ai_get():
    return {"ok": True, **st.public("ai")}


@router.put("/settings/ai")
def ai_put(body: AiIn):
    if body.model and len(body.model) > 60:
        raise HTTPException(400, "model name too long")

    st.put("ai", {"enabled": body.enabled, "api_key": body.api_key.strip(), "model": body.model.strip() or "claude-sonnet-5-5", "actions": body.actions})
    return ai_get()


@router.delete("/settings/ai/key")
def ai_forget_key():
    st.put("ai", {"api_key": "-"})          # a non-empty value replaces the key ...
    st.put("ai", {"enabled": False})

    from ..db import session
    from ..models import Setting

    with session() as db:                   # ... and then it is cleared for real
        row = db.get(Setting, "ai")

        if row is not None and isinstance(row.value, dict):
            v = dict(row.value)
            v["api_key"] = ""
            row.value = v

    return ai_get()


@router.post("/settings/ai/test")
def ai_test():
    try:
        return {"ok": True, "answer": ai.test_key()}
    except (RuntimeError, httpx.HTTPError) as e:
        raise HTTPException(502, str(e))
