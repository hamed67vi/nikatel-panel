"""What the panel does with a server right after it is added or its login is set (the owner, 2026-10-16: "from the very first moment show what is being done", and "the tunnel cores must be
installed by themselves"): look at it - the first reading, so its row has numbers at once and not after the next round two minutes later -, check its GRE links, and put the tunnel
cores on it from the panel's cache. It runs in the background; each step is written on the row of the server (extra.busy) while it runs, and the outcome stays in extra.onboard.
The steps are the three of STEPS; the cores are the only one that changes the server (they are left out in read-only mode and when the owner does not tick them)."""
from __future__ import annotations

import threading
import time

from ..db import session
from ..log import get_logger
from ..models import Server
from ..ssh import SSHError
from . import engines, facts, nodes
from .events import emit

log = get_logger("onboard")
STEPS = ("read", "gre", "cores")                 # what a server gets after it is added
ALL_STEPS = STEPS + ("wg",)                       # "wg" (wireguard-tools, with apt) only when the owner asks for it: the menu of a server, Install -> WireGuard tools
_RUNNING: set[int] = set()
_LOCK = threading.Lock()


def _say(server_id: int, what: str, detail: str = "") -> None:
    nodes.set_busy(server_id, what, detail, unless="node")                    # (the steps of making a node are not overwritten by these)


def _state(server_id: int) -> dict | None:
    with session() as db:
        s = db.get(Server, server_id)
        return None if s is None else {"online": s.online, "error": s.last_error or ""}


def run(server_id: int, steps=STEPS) -> dict:
    """The steps, one after the other, in this thread: {step: "ok" | "failed: why" | "skipped: why"}, and `cores_detail` (what happened to each core). A server that does not answer ends it after
    the first step: there is nothing to put on it."""
    steps = [s for s in ALL_STEPS if s in steps]
    out: dict = {}
    t0 = int(time.time())

    try:
        nodes._srv(server_id)                                                   # (a server that is not there: ValueError, nothing to do)

        if "read" in steps:
            _say(server_id, "Reading the server")
            facts.sample_server(server_id)
            now = _state(server_id)

            if now is None or now["online"] is not True:
                out["read"] = "failed: " + (now["error"] if now and now["error"] else "the server does not answer")
                return out

            out["read"] = "ok"

        if "gre" in steps:
            _say(server_id, "Checking the GRE links")

            try:
                nodes.gre_check(server_id)
                out["gre"] = "ok"
            except (SSHError, ValueError, RuntimeError) as e:
                out["gre"] = "failed: " + str(e)[:100]

        if "cores" in steps:
            try:
                res = engines.install_on(server_id, step=lambda title, i, n: _say(server_id, "Installing the tunnel cores", f"{title} · {i} of {n}"))
                bad = {k: v for k, v in res.items() if v.startswith("failed")}
                out["cores"] = "ok" if not bad else "failed: " + "; ".join(f"{k} {v[8:]}" for k, v in bad.items())
                out["cores_detail"] = res
            except ValueError as e:
                out["cores"] = "skipped: " + str(e)

        if "wg" in steps:
            _say(server_id, "Installing WireGuard tools")

            try:
                out["wg"] = "ok"
                engines.install_wireguard_on(server_id)
            except (SSHError, ValueError, RuntimeError) as e:
                out["wg"] = "failed: " + str(e)[:200]

        return out
    except ValueError:
        return out
    finally:
        _finish(server_id, out, t0)


def _finish(server_id: int, out: dict, t0: int) -> None:
    """The row is quiet again: the busy text is gone and the outcome is kept (the row shows what failed, an event tells it in the log)."""
    try:
        steps = {k: v for k, v in out.items() if k != "cores_detail"}
        nodes._set_extra(server_id, busy=None, onboard={"at": int(time.time()), "started": t0, "steps": steps, "cores": out.get("cores_detail") or {}})
        failed = [f"{k}: {v[8:]}" for k, v in steps.items() if str(v).startswith("failed")]

        with session() as db:
            s = db.get(Server, server_id)

            if s is not None and steps:
                done = "read, GRE checked" + (", tunnel cores in place" if steps.get("cores") == "ok" else "") + (", WireGuard tools ready" if steps.get("wg") == "ok" else "")
                emit(db, "warn" if failed else "ok", "servers", f"Server {s.name} prepared" + (" - " + "; ".join(failed)[:200] if failed else ": " + done) + ".", s.name)
    except Exception:  # the outcome is a courtesy: it must never take the thread down
        log.exception("onboard: could not keep the outcome of server %s", server_id)


def start(server_id: int, steps=STEPS) -> bool:
    """Run the steps in the background; False when this server is being prepared already."""
    with _LOCK:
        if server_id in _RUNNING:
            return False

        _RUNNING.add(server_id)

    def work() -> None:
        try:
            run(server_id, steps)
        except Exception:  # a background step never raises into nothing: it is logged
            log.exception("onboard of server %s failed", server_id)
        finally:
            with _LOCK:
                _RUNNING.discard(server_id)

    threading.Thread(target=work, name=f"onboard-{server_id}", daemon=True).start()
    return True
