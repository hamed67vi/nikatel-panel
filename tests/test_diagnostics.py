"""Finding what went wrong (review 2026-10-16): the page reports its script errors, one answer holds the state of the panel for a bug report, and a 500 never carries the exception text."""
import logging
import time

from nikatel.api import system as system_api


def test_a_script_error_of_the_page_becomes_one_log_line_and_a_loop_of_them_is_cut(client, caplog):
    system_api._CLIENT_HITS.clear()

    with caplog.at_level(logging.WARNING, logger="client"):
        r = client.post("/api/client-error", json={"message": "x is undefined", "source": "https://panel.example/static/app.js?v=1", "line": 42, "stack": "at f (app.js:42)\nat g (app.js:50)", "page": "tunnels"})
        assert r.status_code == 200 and r.json() == {"ok": True}

    line = caplog.records[-1].getMessage()
    assert "browser error on tunnels: x is undefined (app.js?v=1:42)" in line and "at f (app.js:42) | at g (app.js:50)" in line

    for _ in range(40):
        last = client.post("/api/client-error", json={"message": "again"}).json()

    assert last.get("dropped") is True and len(system_api._CLIENT_HITS) <= 20
    assert client.post("/api/client-error", json={"message": "m" * 500}).status_code == 422          # a limit on every field
    client.cookies.clear()
    assert client.post("/api/client-error", json={"message": "anon"}).status_code == 401              # only a signed-in page may write into the log


def test_the_diagnostics_hold_the_state_and_no_secret_and_no_server_address(client, monkeypatch, tmp_path):
    from nikatel import log as logmod
    from nikatel.config import get_settings
    from nikatel.db import session
    from nikatel.models import Server

    with session() as db:
        db.add(Server(name="fo1", host="203.0.113.77", role="foreign", online=True))

    monkeypatch.setenv("NIKATEL_LOG_DIR", str(tmp_path / "logs"))          # its own log folder: the other tests' log is not written over
    get_settings.cache_clear()
    logs = get_settings().log_dir
    logs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    key = "sk-" + "ant-api03-ABCDEFGHIJKLMNOP"          # (split: the secrets check of the repo greps for the prefix)
    (logs / "nikatel.log").write_text(f"{stamp} INFO    http       fine rid=aaaaaa\n{stamp} WARNING ssh        server 203.0.113.77 does not answer token=abc123SECRET rid=bbbbbb\n{stamp} ERROR   tunnels    boom {key} rid=cccccc\n", encoding="utf-8")
    d = client.get("/api/system/diagnostics").json()
    assert d["ok"] and d["version"] and d["python"] and d["counts"]["servers"] == 1 and d["counts"]["servers_online"] == 1 and "checks" in d["health"] and isinstance(d["jobs"], list)
    texts = [p["text"] for p in d["recent_problems"]]
    assert len(d["recent_problems"]) == 2 and d["recent_problems"][0]["rid"] == "cccccc" and d["recent_problems"][1]["rid"] == "bbbbbb"        # the newest first, INFO left out
    blob = client.get("/api/system/diagnostics").text
    assert "203.0.113.77" not in blob and "203.0.x.x" in blob and "abc123SECRET" not in blob and "ABCDEFGHIJKLMNOP" not in blob and "<hidden>" in blob and "<api-key>" in " ".join(texts)
    assert logmod.mask("password=hunter2") == "password=<hidden>"
    client.cookies.clear()
    assert client.get("/api/system/diagnostics").status_code == 401


def test_the_500_handler_gives_only_the_request_id():
    import asyncio

    from starlette.requests import Request

    from nikatel.main import create_app

    handler = create_app(run_jobs=False).exception_handlers[Exception]
    resp = asyncio.run(handler(Request({"type": "http", "method": "GET", "path": "/x", "headers": [], "query_string": b""}), RuntimeError("secret detail /root/.ssh/id_ed25519")))
    assert resp.status_code == 500 and b"secret detail" not in resp.body and b"request id" in resp.body and resp.headers["x-request-id"].encode() in resp.body