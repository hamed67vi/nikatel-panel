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
    """The AI assistant settings: on or off, model, whether a key is kept (masked)."""
    return {"ok": True, **st.public("ai")}


@router.put("/settings/ai")
def ai_put(body: AiIn):
    """Save the AI settings and key (an empty key keeps the stored one)."""
    if body.model and len(body.model) > 60:
        raise HTTPException(400, "model name too long")

    before = st.get("ai").get("model")
    st.put("ai", {"enabled": body.enabled, "api_key": body.api_key.strip(), "model": body.model.strip() or "claude-sonnet-5-5", "actions": body.actions})

    if (body.api_key.strip() or (body.model.strip() or "claude-sonnet-5-5") != before) and st.get("ai").get("api_key"):   # a new key or model: find out at once whether it works
        ai.check()

    return ai_get()


@router.delete("/settings/ai/key")
def ai_forget_key():
    """Forget the AI key and switch the assistant off."""
    st.put("ai", {"api_key": "-"})          # a non-empty value replaces the key ...
    st.put("ai", {"enabled": False, "status": {}})

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
    """Try the AI key with one small request."""
    res = ai.check()

    if not res["ok"]:
        raise HTTPException(502, res["error"])

    return res
