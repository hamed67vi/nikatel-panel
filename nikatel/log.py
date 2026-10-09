"""Logging: one line per event, request id on every line, secrets masked.

    2026-10-09T17:26:48Z INFO  tunnels  check ok name=fi-ir1 rtt=81 rid=a1b2c3

Use `log = get_logger("tunnels")` and `log.info("check ok name=%s rtt=%s", name, rtt)`.
"""
from __future__ import annotations

import contextvars
import logging
import logging.handlers
import re
import sys
import time
from pathlib import Path

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_SECRET_PATTERNS = [
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),          # telegram bot token
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"),               # anthropic key
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._-]{12,}"),
    re.compile(r"(?i)(password|passwd|token|secret|api_key)(=|: ?)(\S+)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
]


def mask(text: str) -> str:
    """Replace anything that looks like a credential."""
    t = str(text)
    t = _SECRET_PATTERNS[0].sub("<bot-token>", t)
    t = _SECRET_PATTERNS[1].sub("<api-key>", t)
    t = _SECRET_PATTERNS[2].sub(r"\1<token>", t)
    t = _SECRET_PATTERNS[3].sub(r"\1\2<hidden>", t)
    t = _SECRET_PATTERNS[4].sub("<private-key>", t)
    return t


class _Formatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", self.converter(record.created))
        msg = mask(record.getMessage())
        line = f"{ts} {record.levelname:<7} {record.name:<10} {msg} rid={request_id.get()}"

        if record.exc_info:
            line += "\n" + mask(self.formatException(record.exc_info))

        return line


def setup(level: str = "INFO", log_dir: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())

    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = _Formatter()
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(fmt)
    root.addHandler(out)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "nikatel.log", maxBytes=10_000_000, backupCount=14)
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # chatty libraries
    for name in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(name).setLevel("WARNING")


def get_logger(domain: str) -> logging.Logger:
    """One logger per domain (servers, tunnels, ssh, jobs, http, auth, ...)."""
    return logging.getLogger(domain)
