"""One TLS client context for the whole panel: the one httpx itself would make, made once.

Every `httpx.request(...)` without a client builds a new SSL context, and building it reads the whole bundle of CA certificates from disk and parses it - about 20 ms of CPU each time. The panel asks the PasarGuard
panels, the DNS providers and the probe many times a minute (py-spy on the live panel, 2026-11-04: 4 % of its busy time was `create_default_context`). A context is safe to share between threads and connections;
only the callers that verify the certificate use it (a panel with `verify_tls` off still gets httpx's own unverified one)."""
from __future__ import annotations

import ssl
import threading

import httpx

_CTX: ssl.SSLContext | None = None
_LOCK = threading.Lock()


def shared() -> ssl.SSLContext:
    """The context a verifying request is made with."""
    global _CTX

    if _CTX is None:
        with _LOCK:
            if _CTX is None:
                _CTX = httpx.create_ssl_context()

    return _CTX
