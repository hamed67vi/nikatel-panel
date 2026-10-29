"""A panel whose HTTPS certificate has run out says so (the owner, 2026-10-29, deleting a server: "no answer from the panel (ConnectError) - see what the problem is").

What happened: the Let's Encrypt certificate of the PasarGuard panel ended on 2026-10-27 and was not renewed. httpx reports that as a ConnectError, the same word as a panel that is switched off, so the screen
said "no answer" for two days and the owner looked for a server that did not answer. The words now name the certificate (and still start with "no answer from the panel", which other screens test for)."""
from pathlib import Path

import httpx
import pytest

from nikatel.services import panels, pg_nodes

EXPIRED = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired (_ssl.c:1000)"
ROOT = Path(__file__).resolve().parent.parent / "nikatel" / "web"


def test_an_expired_certificate_is_named():
    out = panels.no_answer(httpx.ConnectError(EXPIRED))
    assert out == "no answer from the panel (its HTTPS certificate has expired - renew it on the panel's own server)"


def test_the_cause_is_read_from_the_chain_too():
    inner = httpx.ConnectError(EXPIRED)
    outer = httpx.ConnectError("")
    outer.__cause__ = inner
    assert "has expired" in panels.no_answer(outer)


@pytest.mark.parametrize("raw,why", [
    ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate (_ssl.c:1000)", "self-signed certificate"),
    ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1000)", "unable to get local issuer certificate"),
    ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'a.example'. (_ssl.c:1000)", "Hostname mismatch, certificate is not valid for 'a.example'"),
])
def test_another_certificate_fault_gives_its_reason(raw, why):
    assert panels.no_answer(httpx.ConnectError(raw)) == f"no answer from the panel (its HTTPS certificate is not accepted: {why})"


def test_a_panel_that_is_really_off_keeps_the_old_words():
    assert panels.no_answer(httpx.ConnectError("[Errno 111] Connection refused")) == "no answer from the panel (ConnectError)"
    assert panels.no_answer(httpx.ReadTimeout("timed out")) == "no answer from the panel (ReadTimeout)"
    assert panels.no_answer(httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1000)")) == "no answer from the panel (ConnectError)"


def test_a_request_to_a_panel_with_an_expired_certificate_says_so(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError(EXPIRED)

    monkeypatch.setattr(panels.httpx, "request", boom)

    with pytest.raises(panels.PanelError) as e:
        panels._send({"url": "https://panel.example", "verify_tls": True}, "GET", "/api/system")

    assert "certificate has expired" in str(e.value) and str(e.value).startswith("no answer from the panel")


def test_the_log_stream_of_a_node_says_so_too(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError(EXPIRED)

    monkeypatch.setattr(panels, "_token", lambda c, fresh=False: "tok")
    monkeypatch.setattr(pg_nodes.httpx, "stream", boom)

    with pytest.raises(panels.PanelError) as e:
        list(pg_nodes._sse_lines({"url": "https://panel.example", "verify_tls": True}, "/api/node/1/logs"))

    assert "certificate has expired" in str(e.value)


def test_the_server_delete_says_which_node_stayed_and_why():
    js = (ROOT / "pg_srvnode.js").read_text(encoding="utf-8")
    assert "is removed from the panel, but the node ${x.name} is still in ${x.panel}: ${e.message}" in js      # (the server is gone when the node cannot be deleted: the toast said only "no answer", as if the delete had failed)
