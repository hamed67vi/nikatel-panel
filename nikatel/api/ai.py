from typing import NoReturn

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


class AiProviderIn(BaseModel):
    api_key: str = ""
    model: str = ""
    base_url: str = ""


class AiUseIn(BaseModel):
    provider: str


def _doc() -> dict:
    """The settings of the AI assistant as the page needs them: the old fields (the Anthropic record), the AI in use, and `catalog` - one row for every AI the panel can talk to."""
    s = st.public("ai")
    return {"ok": True, **s, "catalog": ai.catalog(st.get("ai")), "active": ai.active_id(st.get("ai"))}


def _bad(e: ValueError) -> NoReturn:
    raise HTTPException(400, str(e)) from e


@router.get("/settings/ai")
def ai_get():
    """The AI assistant settings: on or off, the AI in use, one row per AI the panel knows (a key saved or not, its model, the last test); no key is ever shown."""
    return _doc()


@router.put("/settings/ai")
def ai_put(body: AiIn):
    """Save the AI settings and the Anthropic key (an empty key keeps the stored one); the other AIs have their own route."""
    if body.model and len(body.model) > 60:
        raise HTTPException(400, "model name too long")

    before = st.get("ai").get("model")
    st.put("ai", {"enabled": body.enabled, "api_key": body.api_key.strip(), "model": body.model.strip() or "claude-sonnet-5-5", "actions": body.actions})

    if (body.api_key.strip() or (body.model.strip() or "claude-sonnet-5-5") != before) and st.get("ai").get("api_key"):   # a new key or model: find out at once whether it works
        ai.check("anthropic")

    return _doc()


@router.delete("/settings/ai/key")
def ai_forget_key():
    """Forget the Anthropic key (another AI with a key takes over, or the assistant is switched off)."""
    ai.forget("anthropic")
    return _doc()


@router.post("/settings/ai/test")
def ai_test():
    """Try the AI in use with one small request."""
    res = ai.check()

    if not res["ok"]:
        raise HTTPException(502, res["error"])

    return res


@router.put("/settings/ai/providers/{pid}")
def ai_provider_put(pid: str, body: AiProviderIn):
    """Save the key and the model of one AI (and the address of a "custom" one); an empty key keeps the stored one. The first AI with a key becomes the one in use; a new key or model is tried at once."""
    try:
        changed = ai.save_provider(pid, body.api_key, body.model, body.base_url)
    except ValueError as e:
        _bad(e)

    if changed and ai.has_key(pid):
        ai.check(pid)

    return _doc()


@router.delete("/settings/ai/providers/{pid}")
def ai_provider_forget(pid: str):
    """Forget the key of one AI; when it was the one in use another AI with a key takes over, or the assistant is switched off."""
    try:
        ai.forget(pid)
    except ValueError as e:
        _bad(e)

    return _doc()


@router.post("/settings/ai/providers/{pid}/test")
def ai_provider_test(pid: str):
    """Try one AI with one small request and keep the answer (which model answered, how fast, or why not)."""
    if pid not in ai.PROVIDERS:
        raise HTTPException(404, "unknown AI")

    res = ai.check(pid)

    if not res["ok"]:
        raise HTTPException(502, res["error"])

    return res


@router.get("/settings/ai/providers/{pid}/models")
def ai_provider_models(pid: str):
    """The models of one AI: the live list of the account (read with its key) and the panel's suggestions; a list that cannot be read comes with the reason."""
    try:
        return {"ok": True, **ai.models(pid)}
    except ValueError as e:
        _bad(e)


@router.post("/settings/ai/use")
def ai_use(body: AiUseIn):
    """Choose the AI every call uses (it must have a key saved) and switch the assistant on."""
    try:
        ai.use(body.provider)
    except ValueError as e:
        _bad(e)

    return _doc()
