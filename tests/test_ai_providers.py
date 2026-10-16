"""The AI models of the panel (the owner, 2026-10-16: "add the other AI models too: DeepSeek, ChatGPT, Grok, Gemini and more"): Anthropic as it always was, and every other AI
by the OpenAI chat protocol; one of them is the active one that every call uses. No test reaches a real AI: the calls are answered by a fake."""
import copy
import json

import httpx
import pytest

from nikatel.services import ai
from nikatel.services import settings as st

KEY = "sk-" + "test-" + "abcdefghij1234"      # (the secrets check refuses the real prefixes: this only looks like a key)


class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._d, self.text = status, data, text or json.dumps(data or {})

    def json(self):
        if self._d is None:
            raise ValueError("not json")

        return self._d


def fake(monkeypatch, *answers):
    calls, it = [], iter(answers)

    def post(url, **kw):
        calls.append({"url": url, **copy.deepcopy(kw)})      # (a copy: the caller changes its body when it tries the other spelling)
        return next(it)

    monkeypatch.setattr(ai.httpx, "post", post)
    return calls


def chat(text="OK", model="m-1"):
    return Resp(200, {"model": model, "choices": [{"message": {"content": text}}]})


def test_the_panel_knows_the_ais_the_owner_named_and_anthropic_is_the_default():
    assert ai.active_id() == "anthropic" and not ai.ready() and not ai.has_key()
    assert list(ai.PROVIDERS)[:5] == ["anthropic", "openai", "gemini", "grok", "deepseek"] and "custom" in ai.PROVIDERS
    p = ai.resolve()
    assert p["id"] == "anthropic" and p["kind"] == "anthropic" and p["model"] == "claude-sonnet-5-5" and p["base"] == "https://api.anthropic.com"
    assert ai.resolve("deepseek")["base"] == "https://api.deepseek.com/v1" and ai.resolve("gemini")["base"].endswith("/v1beta/openai") and ai.resolve("grok")["base"] == "https://api.x.ai/v1"
    assert all(m["kind"] in ("anthropic", "openai") and (m["model"] or k == "custom") and (k == "custom" or m["base"].startswith("https://")) for k, m in ai.PROVIDERS.items())
    rows = ai.catalog()
    assert [r["id"] for r in rows] == list(ai.PROVIDERS) and [r["id"] for r in rows if r["active"]] == ["anthropic"] and not any(r["has_key"] for r in rows)
    assert ai.resolve("openai")["model"] == "gpt-5" and next(r for r in rows if r["id"] == "deepseek")["models"][0][0] == "deepseek-chat"
    with pytest.raises(RuntimeError, match="unknown AI"):
        ai.resolve("nobody")


def test_saving_a_key_makes_the_first_ai_the_active_one_and_the_next_ones_wait():
    assert ai.save_provider("deepseek", KEY, "") is True
    assert ai.active_id() == "deepseek" and ai.ready() and ai.has_key("deepseek") and not ai.has_key("openai")      # nothing worked before: this one is the AI now, and the assistant is on
    assert st.get("ai")["providers"]["deepseek"]["api_key"] == KEY and st.get("ai")["api_key"] == ""
    assert ai.save_provider("openai", KEY + "x", "gpt-5-mini") is True and ai.active_id() == "deepseek"                  # the second one waits until it is chosen
    assert ai.resolve("openai")["model"] == "gpt-5-mini" and ai.resolve("deepseek")["model"] == "deepseek-chat"
    assert ai.save_provider("openai", "", "gpt-5-mini") is False                                                          # the same model and no new key: nothing to test again
    assert ai.save_provider("openai", "", "gpt-4.1") is True and ai.resolve("openai")["api_key"] == KEY + "x"             # another model, the key stays
    ai.use("openai")
    assert ai.active_id() == "openai" and ai.summary() == "key saved (ChatGPT)"
    with pytest.raises(ValueError, match="save a key"):
        ai.use("grok")

    with pytest.raises(ValueError, match="unknown AI"):
        ai.save_provider("nobody", KEY)


def test_the_anthropic_key_stays_where_it_always_was():
    ai.save_provider("anthropic", KEY, "claude-haiku-5-5")
    s = st.get("ai")
    assert s["api_key"] == KEY and s["model"] == "claude-haiku-5-5" and s["providers"] == {} and ai.active_id() == "anthropic" and ai.ready()
    assert ai.resolve()["model"] == "claude-haiku-5-5"


def test_forgetting_the_active_ai_hands_over_to_the_next_one_with_a_key_or_switches_the_assistant_off():
    ai.save_provider("anthropic", KEY)
    ai.save_provider("grok", KEY + "g")
    ai.use("grok")
    ai.forget("grok")
    assert ai.active_id() == "anthropic" and ai.ready() and not ai.has_key("grok") and st.get("ai")["providers"]["grok"]["api_key"] == ""
    ai.forget("anthropic")
    assert not ai.ready() and st.get("ai")["enabled"] is False and st.get("ai")["api_key"] == "" and st.get("ai")["status"] == {}
    ai.forget("openai")                                                                                                   # an AI that was never set: nothing happens
    assert not ai.has_key("openai")


def test_a_call_to_an_openai_style_ai_has_the_right_address_header_body_and_limit(monkeypatch):
    ai.save_provider("deepseek", KEY)
    calls = fake(monkeypatch, chat("Hello", "deepseek-chat"))
    assert ai.ask("be brief", "hi", max_tokens=300) == "Hello" and ai.LAST["model"] == "deepseek-chat"
    c = calls[0]
    assert c["url"] == "https://api.deepseek.com/v1/chat/completions" and c["headers"]["authorization"] == "Bearer " + KEY and c["follow_redirects"] is False
    assert c["json"]["model"] == "deepseek-chat" and c["json"]["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert c["json"]["max_tokens"] == 1024 and "max_completion_tokens" not in c["json"] and c["timeout"] >= 60      # (a model that thinks first needs room: the limit is only a ceiling)
    ai.save_provider("openai", KEY)
    ai.use("openai")
    calls = fake(monkeypatch, chat("Hi"))
    assert ai.ask("s", "u", max_tokens=700) == "Hi" and calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert calls[0]["json"]["max_completion_tokens"] == 2100 and "max_tokens" not in calls[0]["json"]                 # OpenAI's newer models only take this spelling


def test_the_other_spelling_of_the_limit_is_tried_when_an_ai_refuses_one(monkeypatch):
    ai.save_provider("openai", KEY)
    calls = fake(monkeypatch, Resp(400, {"error": {"message": "Unsupported parameter: 'max_completion_tokens' is not supported with this model. Use 'max_tokens'."}}), chat("Done"))
    assert ai.ask("s", "u") == "Done" and len(calls) == 2
    assert "max_completion_tokens" in calls[0]["json"] and "max_tokens" in calls[1]["json"] and "max_completion_tokens" not in calls[1]["json"]


def test_a_text_in_parts_an_empty_answer_and_an_error_are_all_handled(monkeypatch):
    ai.save_provider("gemini", KEY)
    fake(monkeypatch, Resp(200, {"model": "g", "choices": [{"message": {"content": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]}}]}))
    assert ai.ask("s", "u") == "AB"
    fake(monkeypatch, Resp(200, {"model": "g", "choices": [{"message": {"content": None}, "finish_reason": "length"}]}))
    assert ai.ask("s", "u") == ""
    fake(monkeypatch, Resp(401, {"error": {"message": "API key not valid.\nPlease pass a valid key"}}))
    with pytest.raises(RuntimeError, match=r"Google Gemini answered 401: API key not valid\. Please pass a valid key"):
        ai.ask("s", "u")

    fake(monkeypatch, Resp(502, None, text="<html>bad gateway</html>"))
    with pytest.raises(RuntimeError, match="502: <html>bad gateway"):
        ai.ask("s", "u")


def test_anthropic_is_called_as_before(monkeypatch):
    ai.save_provider("anthropic", KEY, "claude-opus-5-5")
    calls = fake(monkeypatch, Resp(200, {"model": "claude-opus-5-5-2026", "content": [{"type": "text", "text": "Hello"}]}))
    assert ai.ask("sys", "usr", max_tokens=50) == "Hello"
    c = calls[0]
    assert c["url"] == "https://api.anthropic.com/v1/messages" and c["headers"]["x-api-key"] == KEY and c["json"] == {"model": "claude-opus-5-5", "max_tokens": 50, "system": "sys", "messages": [{"role": "user", "content": "usr"}]}


def test_a_call_without_a_key_or_without_an_address_says_what_is_missing():
    with pytest.raises(RuntimeError, match=r"no API key saved for Anthropic \(Claude\)"):
        ai.ask("s", "u")

    ai.save_provider("custom", KEY, "my-model")
    ai.use("custom")
    with pytest.raises(RuntimeError, match="type the address"):
        ai.ask("s", "u")

    with pytest.raises(ValueError, match="http"):
        ai.save_provider("custom", "", "my-model", "ftp://example.test/v1")

    ai.save_provider("custom", "", "my-model", "https://llm.example.test/v1/")
    assert ai.resolve()["base"] == "https://llm.example.test/v1" and ai.resolve()["model"] == "my-model"


def test_check_keeps_the_answer_with_the_ai_that_was_asked(monkeypatch):
    ai.save_provider("anthropic", KEY)
    ai.save_provider("grok", KEY + "g")
    fake(monkeypatch, chat("OK", "grok-4-0709"))
    r = ai.check("grok")
    assert r["ok"] is True and r["provider"] == "xAI Grok" and r["provider_id"] == "grok" and r["model"] == "grok-4-0709" and r["ms"] >= 0
    assert st.get("ai")["providers"]["grok"]["status"]["ok"] is True and st.get("ai")["status"] == {}                       # kept with Grok, not with Anthropic
    fake(monkeypatch, Resp(401, {"error": {"message": "invalid x-api-key"}}))
    bad = ai.check("anthropic")
    assert bad["ok"] is False and "401" in bad["error"] and st.get("ai")["status"]["ok"] is False
    rows = {r["id"]: r for r in ai.catalog()}
    assert rows["grok"]["status"]["ok"] is True and rows["anthropic"]["status"]["ok"] is False and rows["grok"]["has_key"] and rows["grok"]["key_tail"] == "234g"


def test_the_live_list_of_models_is_read_with_the_key_and_the_chat_ones_are_kept(monkeypatch):
    seen = {}

    def get(url, **kw):
        seen.update({"url": url, **kw})
        return Resp(200, {"data": [{"id": "gpt-5"}, {"id": "text-embedding-3-small"}, {"id": "whisper-1"}, {"id": "gpt-4o"}, {"id": "gpt-4o-mini-tts"}, {"id": "models/gemini-2.5-flash"}]})

    monkeypatch.setattr(ai.httpx, "get", get)
    r = ai.models("openai")
    assert r["live"] == [] and "save a key" in r["error"] and r["suggested"][0][0] == "gpt-5"                              # without a key only the suggestions
    ai.save_provider("openai", KEY)
    r = ai.models("openai")
    assert seen["url"] == "https://api.openai.com/v1/models" and seen["headers"]["authorization"] == "Bearer " + KEY
    assert r["live"] == ["gemini-2.5-flash", "gpt-4o", "gpt-5"] and r["error"] == ""

    monkeypatch.setattr(ai.httpx, "get", lambda url, **kw: Resp(401, {"error": {"message": "bad key"}}))
    r = ai.models("openai")
    assert r["live"] == [] and "401" in r["error"] and r["suggested"]
    monkeypatch.setattr(ai.httpx, "get", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("no route")))
    assert "no route" in ai.models("openai")["error"]


def test_the_api_lists_every_ai_and_never_shows_a_key(client, monkeypatch):
    fake(monkeypatch, chat("OK", "deepseek-chat"))
    r = client.get("/api/settings/ai").json()
    assert r["active"] == "anthropic" and len(r["catalog"]) == len(ai.PROVIDERS) and r["providers"] == {} and not any(c["has_key"] for c in r["catalog"])
    r = client.put("/api/settings/ai/providers/deepseek", json={"api_key": KEY, "model": ""})
    assert r.status_code == 200 and KEY not in r.text and "abcdefghij1234" not in r.text
    d = r.json()
    row = next(c for c in d["catalog"] if c["id"] == "deepseek")
    assert d["active"] == "deepseek" and d["enabled"] is True and row["has_key"] and row["key_tail"] == "1234" and row["status"]["ok"] is True and row["status"]["model"] == "deepseek-chat"
    assert d["providers"]["deepseek"]["api_key"] == "" and d["providers"]["deepseek"]["has_api_key"] is True and d["providers"]["deepseek"]["api_key_tail"] == "1234"
    assert KEY not in client.get("/api/settings/ai").text and KEY not in json.dumps(st.public("ai"))
    assert client.put("/api/settings/ai/providers/nobody", json={"api_key": KEY}).status_code == 400
    assert client.put("/api/settings/ai/providers/custom", json={"api_key": KEY, "model": "m", "base_url": "ftp://x"}).status_code == 400


def test_the_api_tests_chooses_and_forgets_one_ai(client, monkeypatch):
    monkeypatch.setattr(ai, "ask", lambda *a, **k: "OK")
    client.put("/api/settings/ai", json={"enabled": True, "api_key": KEY, "model": "claude-sonnet-5-5", "actions": True})
    assert client.put("/api/settings/ai/providers/grok", json={"api_key": KEY + "g"}).json()["active"] == "anthropic"
    assert client.post("/api/settings/ai/use", json={"provider": "grok"}).json()["active"] == "grok"
    assert client.post("/api/settings/ai/use", json={"provider": "openai"}).status_code == 400                            # no key for it
    t = client.post("/api/settings/ai/providers/grok/test")
    assert t.status_code == 200 and t.json()["provider_id"] == "grok" and client.post("/api/settings/ai/test").json()["provider_id"] == "grok"      # the test without a name asks the AI in use

    def boom(*a, **k):
        raise RuntimeError("xAI Grok answered 403: no credit")

    monkeypatch.setattr(ai, "ask", boom)
    bad = client.post("/api/settings/ai/providers/grok/test")
    assert bad.status_code == 502 and "403" in bad.json()["error"] and client.post("/api/settings/ai/providers/nobody/test").status_code == 404
    d = client.delete("/api/settings/ai/providers/grok").json()
    assert d["active"] == "anthropic" and next(c for c in d["catalog"] if c["id"] == "grok")["has_key"] is False
    monkeypatch.setattr(ai.httpx, "get", lambda url, **kw: Resp(200, {"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-5-5"}]}))
    m = client.get("/api/settings/ai/providers/anthropic/models")
    assert m.status_code == 200 and m.json()["suggested"][0][0] == "claude-fable-5-1" and m.json()["live"] == ["claude-haiku-5-5", "claude-opus-5-5"] and client.get("/api/settings/ai/providers/nobody/models").status_code == 400


def test_the_setup_checklist_and_the_callers_use_the_active_ai(client, monkeypatch):
    from nikatel.services import autopilot, setup

    assert next(i for i in setup.state()["items"] if i["key"] == "ai")["detail"] == "no key"
    ai.save_provider("gemini", KEY)
    it = next(i for i in setup.state()["items"] if i["key"] == "ai")
    assert it["done"] is True and it["detail"] == "key saved (Gemini)"
    monkeypatch.setattr(ai, "ask", lambda system, user, max_tokens=700, timeout=45: "Look at the down tunnel first.")
    assert autopilot.explain()["text"].startswith("Look")                                                                  # the Explain button works with any AI
    st.put("ai", {"providers": {}, "provider": "anthropic"})
    with pytest.raises(ValueError, match="Api & Key"):
        autopilot.explain()

def test_the_refusal_of_each_service_is_read_whatever_its_shape(monkeypatch):
    ai.save_provider("mistral", KEY)

    for body, want in (({"message": "Unauthorized", "request_id": "x"}, "Unauthorized"), ({"detail": "Unauthorized"}, "Unauthorized"), ({"error": "plain text"}, "plain text"),
                       ({"error": {"message": "nested"}}, "nested"), ({"detail": [{"msg": "bad"}]}, "bad"), ([{"error": {"code": 400, "message": "Please pass a valid API key"}}], "Please pass a valid API key")):
        fake(monkeypatch, Resp(401, body))
        with pytest.raises(RuntimeError, match=f"Mistral answered 401: .*{want}"):
            ai.ask("s", "u")


def test_saves_at_the_same_time_keep_every_key():
    import threading

    names, errs = ("openai", "gemini", "grok", "deepseek", "mistral", "groq", "kimi", "glm"), []

    def save(pid):
        try:
            ai.save_provider(pid, KEY + pid)
            ai._keep_status(pid, {"ok": True, "at": 1})
        except Exception as e:                      # noqa: BLE001 - the test only collects what went wrong in a thread
            errs.append(e)

    threads = [threading.Thread(target=save, args=(p,)) for p in names]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    assert not errs and all(ai.has_key(p) for p in names) and all(st.get("ai")["providers"][p]["status"]["ok"] for p in names)      # (a key and a test result written by two requests at once must not overwrite each other)
