"""Application factory and command line."""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, auth, jobs
from . import log as logmod
from .api import router as api_router
from .config import get_settings
from .db import init_db
from .services import (
    alerts,
    autopilot,
    backup,
    balance,
    besttoday,
    builder,
    configs,
    demo,
    doctor,
    extras,
    facts,
    housekeeping,
    netwatch,
    nodes,
    panels,
    probe,
    pulse,
    report,
    tunnel_test,
    tunnels,
    underuse,
)

log = logmod.get_logger("http")
WEB = Path(__file__).parent / "web"
ERROR_CODES = {400: "bad_request", 401: "not_signed_in", 402: "license_needed", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid",
               429: "too_many_requests", 500: "internal", 502: "upstream_failed", 503: "unavailable"}      # docs/api.md: one error shape, these codes


def error_json(status: int, message: str, code: str | None = None, headers: dict | None = None) -> JSONResponse:
    """The one error shape of the API: {"ok": false, "error": <sentence>, "code": <slug>}."""
    return JSONResponse({"ok": False, "error": str(message), "code": code or ERROR_CODES.get(status, "error")}, status_code=status, headers=headers)


SLOW_MS = 3000          # an API answer slower than this is written to the log, so the slow routes are found instead of guessed


def _index_html() -> str:
    """index.html; the script and stylesheet links carry their change time, so a deploy never shows an old copy."""
    html = (WEB / "index.html").read_text(encoding="utf-8")

    for name in ("app.js", "analyst.js", "app.css", "mobile.css", "i18n.js", "i18n.fa.ui.js", "i18n.fa.msg.js", "i18n.fa.doctor.js", "i18n.fa.ui2.js"):
        try:
            stamp = int((WEB / name).stat().st_mtime)
        except OSError:
            stamp = 0

        html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")

    return html


# the public demo: what it never serves (a key pair is made on the first read, the API description and the log are for the operator, the rest reach out of the machine)
_DEMO_BLOCK = ("/api/nodes/user-key", "/api/system/", "/api/arvan", "/api/radar", "/api/logs", "/api/openapi.json", "/api/docs", "/launcher")
_DEMO_HITS: dict[str, list[float]] = {}
_DEMO_LIMIT, _DEMO_WINDOW = 300, 60.0         # requests per client address per minute: a page load is a few dozen, a group of people looking is fine, a script hammering is not


def _demo_refuse(request: Request) -> JSONResponse | None:
    """The three things the demo refuses: a change, a route that is not for visitors, a client that asks too often. None = let it through."""
    p = request.url.path

    if not p.startswith("/api/"):
        return None

    msg = "This is a read-only demo with invented data - nothing can be changed here."

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return error_json(403, msg, "read_only_demo")

    if any(b in p for b in _DEMO_BLOCK):
        return error_json(403, "Not available in the demo.", "read_only_demo")

    ip, now = auth.client_ip(request), time.time()
    hits = [t for t in _DEMO_HITS.get(ip, []) if now - t < _DEMO_WINDOW]
    hits.append(now)
    _DEMO_HITS[ip] = hits

    if len(_DEMO_HITS) > 5000:
        for k in [k for k, v in _DEMO_HITS.items() if not v or now - v[-1] > _DEMO_WINDOW]:
            _DEMO_HITS.pop(k, None)

    if len(hits) > _DEMO_LIMIT:
        return error_json(429, "Too many requests - slow down a little.", headers={"Retry-After": "30"})

    return None


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
    from . import ssh as _ssh

    _ssh.set_jump_provider(nodes.jump_candidates)                # a server whose direct SSH stalls is reached through another Iran server (ssh.py)
    jobs.register("pulse", 5, pulse.run)                        # a server that is switched off is called off within ~12 s, a server that came back is put back within seconds
    jobs.register("tunnels", s.tunnel_check_interval, tunnels.check_all)
    jobs.register("tunnels-fast", 8, tunnels.check_unhealthy)   # the tunnels that are not up are looked at again every ~8 s (and at once when a server goes off or comes back)
    jobs.register("alerts", 60, alerts.run)
    jobs.register("report", 300, report.run)
    jobs.register("backup", 300, backup.scheduled)
    jobs.register("repair", 20, autopilot.repair_cycle)         # the plain restarts every 20 s (a tunnel that is down waits only its grace, not a whole minute more); the smart repair and the look at lossy tunnels every five minutes (one job, not three)
    jobs.register("netwatch", 300, netwatch.probe_all)
    jobs.register("cpu-balance", 600, balance.run)              # a busy server whose network work sits on one core gets RPS (the Doctor does it by itself; no tunnel is touched)
    jobs.register("underuse", 1800, underuse.run)               # a server that carries little is looked at: its own network may be the reason; what the kernel settings can fix is fixed
    jobs.register("quality", 1800, besttoday.refresh)           # the latency, jitter and loss of every tunnel are measured again every half hour (read-only): the ranking of the best tunnels of the day uses them
    jobs.register("temp-purge", 600, tunnel_test.purge_stale)
    jobs.register("housekeeping", 6 * 3600, housekeeping.run)   # old events and finished repair stories go; the write-ahead log is folded back
    jobs.register("doctor", 60, doctor.run)                     # the Doctor's moves (mode "move"): dead Iran servers, exhausted ones, the returns; nothing in mode off / fix

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

    if s.demo:
        s.read_only = True
        demo.seed()                          # only into an empty database
        jobs.register("demo", 30, demo.tick)
        jobs.start_all()
    else:
        probe.bootstrap()
        panels.bootstrap()

    if app.state.run_jobs and not s.demo:
        _register_jobs()
        jobs.start_all()

        from .api import dns as dns_api

        dns_api.warm()                         # the records of the DNS accounts are read once now, so the first open of the DNS page does not wait

        def resume() -> None:
            """What a restart interrupted is picked up again - in the background: it talks to servers for seconds, and the panel must answer /api/health at once."""
            time.sleep(2)

            try:
                builder.resume_pending()
                extras.resume_pending()
                tunnel_test.purge_stale()      # a test that a restart cut short: its temporary tunnel goes now, not in ten minutes
            except Exception:
                log.exception("resuming interrupted work failed")

        import threading

        threading.Thread(target=resume, name="resume", daemon=True).start()

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

            if get_settings().demo:
                refused = _demo_refuse(request)

                if refused is not None:
                    refused.headers["X-Request-Id"] = rid
                    _harden(refused)
                    return refused

            if not gate.allowed(request):
                return error_json(404, "Not Found", headers={"X-Request-Id": rid})   # looks exactly like a missing page

            if p in ("/api/docs", "/api/openapi.json") and not auth.session_user(request):   # the API description is for the signed-in operator only
                return error_json(401, "not logged in", headers={"X-Request-Id": rid})

            response = await call_next(request)
        except Exception:  # the boundary: never a traceback to the browser
            log.exception("unhandled %s %s", request.method, request.url.path)
            return error_json(500, "internal error (request id " + rid + ")", headers={"X-Request-Id": rid})
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

        ms = (time.time() - t0) * 1000

        if request.url.path.startswith("/api/") and response.status_code >= 400:
            log.warning("%s %s -> %s in %dms", request.method, request.url.path, response.status_code, ms)
        elif request.url.path.startswith("/api/") and ms > SLOW_MS:
            log.warning("slow %s %s -> %s in %dms", request.method, request.url.path, response.status_code, ms)      # a route that blocks this long belongs behind an action id (docs/api.md)

        return response

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        """Every HTTPException a route raises, and every unknown path, in the one shape."""
        d = exc.detail

        if isinstance(d, dict):
            return error_json(exc.status_code, d.get("error") or d.get("detail") or "", d.get("code"), getattr(exc, "headers", None))

        return error_json(exc.status_code, d, None, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        """A body or query that does not fit the model: one sentence per field."""
        parts = []

        for e in exc.errors()[:6]:
            loc = ".".join(str(x) for x in e.get("loc", ()) if x not in ("body", "query", "path"))
            parts.append((loc + ": " if loc else "") + str(e.get("msg", "invalid")))

        return error_json(422, "; ".join(parts) or "invalid request", "invalid")

    @app.exception_handler(Exception)
    async def _any(request: Request, exc: Exception):
        log.error("error in %s %s: %s", request.method, request.url.path, exc, exc_info=exc)
        return error_json(500, "internal error: " + str(exc)[:160])

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
    sub.add_parser("demo-seed", help="fill an empty database with invented demo data")
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

    if a.cmd == "demo-seed":
        init_db()
        print(demo.seed())
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

    if not s.demo and s.tls_cert and s.tls_key and s.tls_cert.exists() and s.tls_key.exists():      # the demo is plain HTTP (a self-signed certificate would only scare the people who look)
        kw = {"ssl_certfile": str(s.tls_cert), "ssl_keyfile": str(s.tls_key)}

    uvicorn.run(create_app(), host=s.listen_host, port=s.listen_port, log_config=None, access_log=False, timeout_graceful_shutdown=4, server_header=False, **kw)      # a restart waits 4 s for open connections, not 16 s of 502s
    return 0


if __name__ == "__main__":
    sys.exit(cli())
