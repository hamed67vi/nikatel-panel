"""Application factory and command line."""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, jobs
from . import log as logmod
from .api import router as api_router
from .config import get_settings
from .db import init_db
from .services import (
    alerts,
    autopilot,
    backup,
    builder,
    configs,
    extras,
    facts,
    failover,
    netwatch,
    nodes,
    panels,
    probe,
    pulse,
    report,
    tunnel_test,
    tunnels,
)

log = logmod.get_logger("http")
WEB = Path(__file__).parent / "web"


def _index_html() -> str:
    """index.html; the script and stylesheet links carry their change time, so a deploy never shows an old copy."""
    html = (WEB / "index.html").read_text(encoding="utf-8")

    for name in ("app.js", "app.css", "mobile.css", "i18n.js", "i18n.fa.ui.js", "i18n.fa.msg.js", "i18n.fa.doctor.js", "i18n.fa.ui2.js"):
        try:
            stamp = int((WEB / name).stat().st_mtime)
        except OSError:
            stamp = 0

        html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")

    return html


def _harden(resp) -> None:
    """Browser-side protection on every answer: no framing, no type sniffing, no referrer leaks, no powerful features."""
    h = resp.headers
    h["X-Frame-Options"] = "DENY"
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "no-referrer"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    h["Cross-Origin-Opener-Policy"] = "same-origin"
    h["Cross-Origin-Resource-Policy"] = "same-origin"
    h["Strict-Transport-Security"] = "max-age=31536000"
    h["X-Robots-Tag"] = "noindex, nofollow"
    # what the browser may load and where it may send: only this origin (plus the flag pictures), never inside a frame
    h["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                    "font-src 'self'; img-src 'self' data:; connect-src 'self'; "
                                    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def _register_jobs() -> None:
    s = get_settings()
    jobs.register("servers", s.server_sample_interval, facts.sample_all)
    jobs.register("pulse", 10, pulse.run)                       # a server that is switched off is called off within ~20 s, not at the next full sample
    jobs.register("tunnels", s.tunnel_check_interval, tunnels.check_all)
    jobs.register("alerts", 60, alerts.run)
    jobs.register("report", 300, report.run)
    jobs.register("backup", 300, backup.scheduled)
    jobs.register("repair", 60, autopilot.repair_cycle)         # the plain restarts every minute; the smart repair and the look at lossy tunnels every five minutes (one job, not three)
    jobs.register("netwatch", 300, netwatch.probe_all)
    jobs.register("temp-purge", 600, tunnel_test.purge_stale)
    jobs.register("failover", 60, failover.check)              # does nothing until a key and a mode are set (Tunnels → DNS · IP switch)

    jobs.register("configs", 120, configs.sync)
    jobs.register("panels", 30, panels.check_all)          # a node that drops off a Marzban / PasarGuard panel shows within half a minute
    jobs.register("geo", 3600, nodes.geo_missing_job)
    jobs.register("filter", 3600, nodes.filter_daily_job)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    backup.apply_pending_restore()          # a restore asked for in the Backups card: the files are put in place before the database is opened
    init_db()
    pw = auth.ensure_admin()

    if pw:
        (s.data_dir / "initial-admin-password.txt").write_text(pw + "\n")
        (s.data_dir / "initial-admin-password.txt").chmod(0o600)
        log.info("first start: the admin password is in %s", s.data_dir / "initial-admin-password.txt")

    probe.bootstrap()
    panels.bootstrap()

    if app.state.run_jobs:
        _register_jobs()
        jobs.start_all()

        try:
            builder.resume_pending()
            extras.resume_pending()
            tunnel_test.purge_stale()          # a test that a restart cut short: its temporary tunnel goes now, not in ten minutes
        except Exception:
            log.exception("resuming interrupted rebuilds failed")

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
            from .services import gate

            entry = gate.entry_path()
            p = request.url.path

            if entry and p.rstrip("/") == "/" + entry and request.method == "GET":
                # the secret door: set the gate cookie and show the login page
                resp = HTMLResponse(_index_html())
                resp.set_cookie(auth.GATE_COOKIE, gate.make_cookie(entry), httponly=True, samesite="strict", secure=auth.is_https(request), max_age=30 * 86400, path="/")
                resp.headers["X-Request-Id"] = rid
                _harden(resp)
                return resp

            if not gate.allowed(request):
                return JSONResponse({"detail": "Not Found"}, status_code=404, headers={"X-Request-Id": rid})   # looks exactly like a missing page

            if p in ("/api/docs", "/api/openapi.json") and not auth.session_user(request):   # the API description is for the signed-in operator only
                return JSONResponse({"detail": "not logged in"}, status_code=401, headers={"X-Request-Id": rid})

            response = await call_next(request)
        except Exception:  # the boundary: never a traceback to the browser
            log.exception("unhandled %s %s", request.method, request.url.path)
            return JSONResponse({"ok": False, "error": "internal error", "rid": rid}, status_code=500, headers={"X-Request-Id": rid})
        finally:
            logmod.request_id.reset(token)

        response.headers["X-Request-Id"] = rid
        _harden(response)

        if request.url.path.startswith("/static/") and "v" in request.query_params:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"   # the page links its files with their change time (?v=): a deploy changes the link, so a reload asks for nothing
        elif request.url.path.startswith("/static/fonts/"):
            response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        elif request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"   # the browser revalidates (ETag), so a deploy shows at once
        elif request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"

        if request.url.path.startswith("/api/") and response.status_code >= 400:
            log.warning("%s %s -> %s in %dms", request.method, request.url.path, response.status_code, (time.time() - t0) * 1000)

        return response

    @app.exception_handler(Exception)
    async def _any(request: Request, exc: Exception):
        log.error("error in %s %s: %s", request.method, request.url.path, exc, exc_info=exc)
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=500)

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/flags/{cc}.png", include_in_schema=False, dependencies=[Depends(auth.current_user)])
        def flag(cc: str):
            """A country flag: the panel server fetches it once and keeps it, so the browser never has to reach a foreign server for it."""
            import httpx
            from fastapi.responses import FileResponse, Response

            cc = cc.lower()

            if len(cc) != 2 or not cc.isalpha():
                return Response(status_code=404)

            d = get_settings().data_dir / "flags"
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{cc}.png"

            if not f.exists():
                try:
                    r = httpx.get(f"https://flagcdn.com/w80/{cc}.png", timeout=8)
                    r.raise_for_status()
                    f.write_bytes(r.content)
                except Exception:  # noqa: BLE001  no flag is not an error: the page shows the country code
                    return Response(status_code=404)

            return FileResponse(f, media_type="image/png", headers={"Cache-Control": "private, max-age=2592000, immutable"})

        @app.get("/", include_in_schema=False)
        def index():
            return HTMLResponse(_index_html())

    return app


def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nikatel")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the panel")
    imp = sub.add_parser("import", help="import the old panel's JSON files")
    imp.add_argument("seed_dir")
    pw = sub.add_parser("set-password", help="set the admin password")
    pw.add_argument("password")
    sub.add_parser("disable-2fa", help="switch two-step sign-in off (lost phone and recovery codes) - run on the server")
    ep = sub.add_parser("entry-path", help="show / set / clear the secret entry path - run on the server")
    ep.add_argument("--set", dest="path", default=None)
    ep.add_argument("--clear", action="store_true")
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

    if a.cmd == "disable-2fa":
        auth.totp_disable()
        print("two-step sign-in is off; every session was signed out")
        return 0

    if a.cmd == "entry-path":
        from .services import gate
        from .services import settings as st

        init_db()

        if a.clear:
            gate.set_entry_path("")
            print("secret path removed: the panel answers on / again")
        elif a.path:
            print("secret path set: /" + gate.set_entry_path(a.path))
        else:
            print("secret path: " + (("/" + str(st.get("security").get("entry_path"))) if st.get("security").get("entry_path") else "(none)"))

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

    uvicorn.run(create_app(), host=s.listen_host, port=s.listen_port, log_config=None, access_log=False, timeout_graceful_shutdown=4, server_header=False, **kw)      # a restart waits 4 s for open connections, not 16 s of 502s
    return 0


if __name__ == "__main__":
    sys.exit(cli())
