"""Application factory and command line."""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, jobs
from . import log as logmod
from .api import router as api_router
from .config import get_settings
from .db import init_db
from .services import alerts, backup, facts, report, tunnels

log = logmod.get_logger("http")
WEB = Path(__file__).parent / "web"


def _register_jobs() -> None:
    s = get_settings()
    jobs.register("servers", s.server_sample_interval, facts.sample_all)
    jobs.register("tunnels", s.tunnel_check_interval, tunnels.check_all)
    jobs.register("alerts", 60, alerts.run)
    jobs.register("report", 300, report.run)
    jobs.register("backup", 600, backup.scheduled)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    pw = auth.ensure_admin()

    if pw:
        (s.data_dir / "initial-admin-password.txt").write_text(pw + "\n")
        (s.data_dir / "initial-admin-password.txt").chmod(0o600)
        log.info("first start: the admin password is in %s", s.data_dir / "initial-admin-password.txt")

    if app.state.run_jobs:
        _register_jobs()
        jobs.start_all()

    log.info("nikatel %s listening on %s:%s read_only=%s", __version__, s.listen_host, s.listen_port, s.read_only)
    yield
    jobs.stop_all()


def create_app(run_jobs: bool = True) -> FastAPI:
    app = FastAPI(title="Nikatel panel", version=__version__, lifespan=_lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.run_jobs = run_jobs
    app.include_router(api_router)

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        rid = secrets.token_hex(3)
        token = logmod.request_id.set(rid)
        t0 = time.time()

        try:
            response = await call_next(request)
        except Exception:  # the boundary: never a traceback to the browser
            log.exception("unhandled %s %s", request.method, request.url.path)
            return JSONResponse({"ok": False, "error": "internal error", "rid": rid}, status_code=500, headers={"X-Request-Id": rid})
        finally:
            logmod.request_id.reset(token)

        response.headers["X-Request-Id"] = rid

        if request.url.path.startswith("/api/") and response.status_code >= 400:
            log.warning("%s %s -> %s in %dms", request.method, request.url.path, response.status_code, (time.time() - t0) * 1000)

        return response

    @app.exception_handler(Exception)
    async def _any(request: Request, exc: Exception):
        log.error("error in %s %s: %s", request.method, request.url.path, exc, exc_info=exc)
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=500)

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(WEB / "index.html")

    return app


def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nikatel")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the panel")
    imp = sub.add_parser("import", help="import the old panel's JSON files")
    imp.add_argument("seed_dir")
    pw = sub.add_parser("set-password", help="set the admin password")
    pw.add_argument("password")
    sub.add_parser("run-job").add_argument("name")
    sub.add_parser("backup", help="make an encrypted backup now (and send it to Telegram when configured)")
    rs = sub.add_parser("restore", help="restore a backup into the data dir - stop the service first")
    rs.add_argument("file")
    rs.add_argument("--passphrase", default="", help="omit to be asked")
    a = p.parse_args(argv)

    s = get_settings()
    logmod.setup(s.log_level, s.log_dir)

    if a.cmd == "import":
        from .services.importer import import_seed_dir

        init_db()
        print(import_seed_dir(Path(a.seed_dir)))
        return 0

    if a.cmd == "set-password":
        init_db()
        auth.set_password(a.password)
        print("ok")
        return 0

    if a.cmd == "run-job":
        init_db()
        _register_jobs()
        jobs.run_once(a.name)
        return 0

    if a.cmd == "backup":
        init_db()
        print(backup.run())
        return 0

    if a.cmd == "restore":
        import getpass

        pw = a.passphrase or getpass.getpass("backup passphrase: ")
        files = backup.restore(Path(a.file), pw)
        print("restored:", ", ".join(files), "- start the service again")
        return 0

    import uvicorn

    kw = {}

    if s.tls_cert and s.tls_key and s.tls_cert.exists() and s.tls_key.exists():
        kw = {"ssl_certfile": str(s.tls_cert), "ssl_keyfile": str(s.tls_key)}

    uvicorn.run(create_app(), host=s.listen_host, port=s.listen_port, log_config=None, access_log=False, **kw)
    return 0


if __name__ == "__main__":
    sys.exit(cli())
