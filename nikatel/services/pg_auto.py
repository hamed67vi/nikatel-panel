"""The PasarGuard panel and its nodes updated by themselves at night (the owner, 2026-11-03: "a tick box so that the PasarGuard panel, its nodes and Xray update by themselves at 03:00 Iran time - test first, very
safe, roll back or fix on a problem, a complete update").

What it is: the Safe update of 1.69.165 (services/pg_safe.py - the same looks before and after, the same kept images, the same way back), started by a job once a night at the hour the owner chose (03:00 on the
panel clock, Tehran by default - the clock of the daily report), with what a night needs on top:
  * a time budget: no node is started after `budget_minutes` (60) from the start, and the panel only in the first PANEL_BY (45) minutes of its hour; the rest waits for the next night and the story says so. The
    panel is never touched outside its hour (on the night of 2026-11-03 the shop bot's approvals timed out while the panel was slow for a moment: a night job that runs into the morning is not wanted).
  * a look at the USERS after every node: the configs of the Configs section that ride a tunnel ending on that node's server and were up before are read again from the probe - two fresh tests each; one that is
    down after the update means the new version does not serve them, so the old versions come back (Xray, then the image) and the run stops there.
  * the panel on its own server: the image that runs, its compose files and a dump of its database (pg_dump through the postgres container of the same compose project, or a copy of the SQLite file) are kept,
    the new image is pulled and the panel service ALONE is recreated (the official `pasarguard update` takes every service of the compose file down, the database too; the owner's panel has no pasarguard
    command at all - it runs from a compose file of his own). After it the panel must answer /api/system, sign in with the saved login, answer at its subscription front door, and have every node that was
    connected before connected again; one known fix is tried (the panel service restarted once, the nodes asked once more to connect) and then the old image is put back and looked at again. The database is
    NOT put back by itself (the dump is there for the owner: a new version may have changed its tables).
  * what is remembered: a release that did not hold is held back (settings `pg_auto.bad`) and never tried again by itself - the Safe update buttons still try it. What a night did is the story of an action in
    the history of the Tunnel Doctor, one event in the feed, and one message on the Telegram channel of the alerts: what was updated, what came back, what was left for the next night, what needs the owner.
  * the fixes it tries are only the known, safe ones (the panel asked once more to connect to a node, the Xray command run once more, the panel service restarted once). Anything else is the way back and the report.
OFF until the owner switches it on (Settings → About & update, the PasarGuard card). A rehearsal (`rehearse`, the link under the switch) walks the same steps now, reading everything and changing nothing."""
from __future__ import annotations

import time

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Tunnel
from ..ssh import SSHError, run_action, run_script
from . import (
    autopilot,
    engines,
    extras,
    panels,
    pg_nodes,
    pg_safe,
    pg_update,
    probe,
    telegram,
    userq,
)
from . import settings as cfg
from .configs import parse_health
from .events import emit

log = get_logger("pg_auto")
LABEL = pg_safe.LABEL                 # one lock with the Safe update buttons: never two PasarGuard updates at once
PANEL_BY = 45                         # minutes after the hour before which the panel step must START, so that it is over inside the hour
USERS_LIMIT = 150.0                   # seconds the probe gets to test the configs of a node twice after the node came back
USERS_POLL = 15.0
USERS_TESTS = 2                       # fresh tests of the probe that must say down before a config counts as not passing (one test from a data centre is noisy)
KEEP_TIMEOUT = 900                    # seconds the keep of the panel may take (a dump of its database)
UPDATE_TIMEOUT = 1500                 # seconds the pull of the new image and the recreate may take
BACK_TIMEOUT = 600
NODES_BACK = 240.0                    # seconds the nodes get to connect to the restarted panel
PANEL_AGAIN = 120.0                   # seconds the panel gets after the one restart that is tried
SUB_PATH = "/sub/nikatel-nightly-check"   # the subscription front door: any answer below 500 means the web side of the panel is up (an unknown token is a 404)
KEEP_DIR = "/var/lib/nikatel-install/pgpanel-prev"


# ------------------------------------------------------------------ the switch and the clock
def state() -> dict:
    s = cfg.get("pg_auto")
    return {"auto": bool(s.get("auto")), "hour": int(3 if s.get("hour") is None else s.get("hour")), "budget_minutes": int(s.get("budget_minutes") or 60), "bad": s.get("bad") or {}, "last": s.get("last") or {}}


def _day(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", engines._local(now))


def due(now: float | None = None) -> bool:
    """Is it the night's hour, the switch on, and tonight's run not made yet?"""
    s = state()

    if not s["auto"]:
        return False

    t = engines._local(now)
    return t.tm_hour == s["hour"] and (s["last"] or {}).get("day") != time.strftime("%Y-%m-%d", t)


def _busy() -> bool:
    return LABEL in autopilot._BUSY or "pg node updates" in autopilot._BUSY or any(k.startswith("pgpanel ") for k in autopilot._BUSY)


def run(now: float | None = None) -> int | None:
    """The job (every 5 minutes): at the hour, with the switch on and no PasarGuard update running, tonight's run starts as an action of the panel (its story is in the history of the Tunnel Doctor). Returns the id
    of the action, None when it was not the time. The day is marked FIRST: a run cut short by a restart of the panel is not started over the same night."""
    if get_settings().read_only or not due(now) or _busy():
        return None

    day = _day(now)
    cfg.put("pg_auto", {"last": {"day": day, "at": int(time.time()), "text": "running", "ok": None}})

    try:
        return autopilot._start(LABEL, _night_story, False, action="pgupdate", source="auto")
    except ValueError as e:                                                        # (Busy is one: a button was pressed in the same second)
        log.warning("nightly PasarGuard update not started: %s", e)
        cfg.put("pg_auto", {"last": {"day": day, "at": int(time.time()), "text": f"not started: {str(e)[:160]}", "ok": False}})
        return None


def rehearse() -> int:
    """Tonight's run walked NOW, reading everything and changing nothing (an action; its story says what would be done, in order, what would be checked, and what is left out and why)."""
    if _busy():
        raise ValueError("a PasarGuard update is running right now: follow it in the history of the Tunnel Doctor")

    return autopilot._start(LABEL, _night_story, True, action="pgupdate", source="manual", allow_read_only=True)


# ------------------------------------------------------------------ pure helpers
def parse_panel_info(out: str) -> dict:
    """Every 'KEY rest' line of the read-only look at the panel's server (CLI, BACKUP, DIR, DOCKER, DF, CONTAINER, REF, ID, SERVICE, PROJECT, WDIR, DB, OVERRIDES) -> a dict with lower-case keys."""
    d: dict = {}

    for ln in str(out).splitlines():
        p = ln.strip().split(None, 1)

        if len(p) == 2 and p[0].isupper() and p[0].isalpha():
            d[p[0].lower()] = p[1].strip()
        elif len(p) == 1 and p[0].isupper() and p[0].isalpha():
            d[p[0].lower()] = ""

    return d


def _held(bad: dict, key: str, version: str) -> dict | None:
    """The record of a release that is held back, when `version` is the one that did not hold (with or without the 'v')."""
    b = (bad or {}).get(key) or {}
    return b if b.get("version") and version and pg_safe.short(b["version"]) == pg_safe.short(version) else None


def _hold(key: str, version: str, where: str, why: str, day: str) -> None:
    bad = dict(state()["bad"])
    bad[key] = {"version": version, "where": str(where)[:80], "why": str(why)[:200], "day": day}
    cfg.put("pg_auto", {"bad": bad})


def judge(seen: dict[str, list[tuple[int, str]]], tests: int = USERS_TESTS) -> tuple[list[str], list[str], list[str]]:
    """Pure: from the fresh tests of each config -> (up, down, pending). Up: a fresh test said up. Down: `tests` fresh tests and all of them down. Pending: not enough tests yet."""
    up, down, pend = [], [], []

    for n, t in seen.items():
        if any(s == "up" for _, s in t):
            up.append(n)
        elif len(t) >= tests and all(s == "down" for _, s in t):
            down.append(n)
        else:
            pend.append(n)

    return up, down, pend


def summary(got: dict) -> str:
    parts = []

    if got["updated"]:
        parts.append(("would update " if got["dry"] else "updated ") + ", ".join(got["updated"]))

    if got["back"]:
        parts.append("put back " + "; ".join(got["back"]))

    if got["left"]:
        parts.append("left for the next night: " + "; ".join(got["left"]))

    if got["skipped"]:
        parts.append("not touched: " + "; ".join(got["skipped"]))

    if got["needs"]:
        parts.append("NEEDS YOU: " + "; ".join(got["needs"]))

    return ". ".join(parts) if parts else "nothing to do"


def message(got: dict) -> str:
    """The Telegram summary of a night (plain text, the alerts channel)."""
    return "\n".join([f"PasarGuard nightly update - {got['day']}", f"Updated: {', '.join(got['updated']) or '-'}", f"Put back: {'; '.join(got['back']) or '-'}",
                      f"Left for the next night: {'; '.join(got['left']) or '-'}", f"Needs you: {'; '.join(got['needs']) or '-'}", f"The whole story: Tunnel Doctor → history, action #{got['aid']}"])[:4000]


def _last(out: str) -> str:
    s = str(out or "").strip()
    return s.splitlines()[-1][:140] if s else "no answer"


def _mb(n) -> str:
    try:
        return f"{int(n) / 1e6:.0f} MB"
    except (TypeError, ValueError):
        return "?"


# ------------------------------------------------------------------ the users: the configs that ride a node
def configs_of_server(server_id: int) -> list[dict]:
    """The configs of the Configs section whose tunnel ends on this server (the server of a node), each with the status the panel holds for it: [{name, status}]. A pure read."""
    with session() as db:
        cfgs, placed = userq._placement(db)
        mine = set(db.scalars(select(Tunnel.id).where(Tunnel.foreign_server_id == int(server_id))))
        return [{"name": c.name, "status": c.status} for c in cfgs if (placed.get(c.name) or {}).get("tunnel") in mine]


def _probe_read() -> dict[str, dict]:
    """{name: the probe's row} as the probe says now (a probe that sends no time of its test: one test per 30 s is assumed, as services/configs.py does); RuntimeError when no probe answers."""
    now = int(time.time())
    return {r["name"]: {**r, "checked_at": int(r.get("checked_at") or 0) or now // 30 * 30} for r in parse_health(probe.health())}


def _probe_base() -> dict[str, dict] | None:
    try:
        return _probe_read()
    except RuntimeError:
        return None


def _watch_text(watch: list[dict], up: list[str]) -> str:
    if not watch:
        return "no config of the Configs section rides it (only the panel's view of it is checked after)"

    return f"{len(up)} of {len(watch)} config{'s' if len(watch) != 1 else ''} that ride it {'are' if len(up) != 1 else 'is'} up now and would be checked after ({', '.join(up[:6])}{'…' if len(up) > 6 else ''})"


def users_check(watch: list[dict], base: dict | None, limit: float = USERS_LIMIT) -> tuple[bool, str]:
    """After a node came back: the configs that were up before (`watch`, read before the update) must pass again - a fresh test of the probe that says up, or USERS_TESTS fresh tests that say down (fresh: newer than
    `base`, the probe's answer read before the update). (they pass, the sentence). No config, no probe before: they pass, with the reason - the panel's own view of the node is all that can be said."""
    names = [w["name"] for w in watch if w["status"] == "up"]

    if not watch:
        return True, "no config of the Configs section rides this node: only the panel's view of it was checked."

    if not names:
        return True, f"{len(watch)} config{'s' if len(watch) != 1 else ''} ride this node but none was up before the update: nothing to compare."

    if base is None:
        return True, "the probe could not be asked before the update: the configs were not compared."

    end = time.time() + limit
    seen: dict[str, list[tuple[int, str]]] = {n: [] for n in names}
    err = ""

    while True:
        try:
            now_ = _probe_read()
            err = ""
        except RuntimeError as e:
            now_, err = {}, str(e)[:100]

        for n in names:
            r = now_.get(n)

            if r is None:
                continue

            at = int(r["checked_at"])

            if at > int((base.get(n) or {}).get("checked_at") or 0) and at not in [a for a, _ in seen[n]]:
                seen[n].append((at, r["status"]))

        up, down, pend = judge(seen)

        if not pend or time.time() >= end:
            break

        time.sleep(USERS_POLL)

    text = f"{len(up)} of {len(names)} config{'s' if len(names) != 1 else ''} that ride it pass{'es' if len(names) == 1 else ''} again"

    if down:
        text += f"; {', '.join(down[:8])} {'does' if len(down) == 1 else 'do'} not ({USERS_TESTS} tests each)"

    if pend:
        text += f"; {len(pend)} not tested in time"

    if err:
        text += f"; the probe did not answer ({err})"

    return not down, text + "."


def _back_both(row: dict, sw: str, xr: str) -> str:
    """After the users check failed: the old Xray first (the same command with the old version), then the old image. The sentence."""
    try:
        c = panels.cred_for(row["panel_id"])
        node, _sname, _ip = extras._node(row["server"]["id"])
    except (panels.PanelError, ValueError) as e:
        return f"the way back could not start ({str(e)[:100]}): look at the node by hand"

    before = {"version": row.get("version", ""), "xray": row.get("xray", "")}
    parts = []

    if xr:
        parts.append(pg_safe._back_core(node, c, row["node_id"], before))

    if sw:
        parts.append(pg_safe._back_software(node, c, row["node_id"], before))

    return "; ".join(parts) or "nothing to put back"


def _after_failure(got: dict, name: str, parts: list[str], why: str, latest: dict, day: str) -> None:
    """A node that did not hold: the releases it got are held back, and the summary says what came back - or what needs the owner (a way back that did not work)."""
    for part in parts:
        key = "node" if part == "software" else "xray"

        if latest.get(key):
            _hold(key, latest[key], name, why, day)

    why = why.rstrip(".")

    if not parts:
        got["left"].append(f"{name}: {why}")
    elif "by hand" in why:
        got["needs"].append(f"{name}: {why}")
    else:
        got["back"].append(f"{name}: {why}")


# ------------------------------------------------------------------ the panel
def login_check(c: dict) -> tuple[bool, str]:
    """The sign-in of the panel after its update: a fresh sign-in with the saved login (the token the panel answers with); with only an API key or token, the key is tried on /api/admin."""
    try:
        if c.get("username") and c.get("password"):
            panels._login(c)
            return True, "signed in with the saved login"

        panels.call(c, "GET", "/api/admin")
        return True, "the saved key is accepted"
    except (panels.PanelError, ValueError) as e:
        return False, str(e)[:160]


def front_door(c: dict) -> tuple[bool, str]:
    """The subscription front door of the panel: a request without any login must get an answer below 500 (an unknown token is a 404; a proxy with a dead panel behind it answers 502)."""
    url = str(c.get("url") or "").rstrip("/") + SUB_PATH

    try:
        r = httpx.get(url, timeout=15, verify=bool(c.get("verify_tls", True)), follow_redirects=False, headers={"User-Agent": "nikatel-panel"})
    except httpx.HTTPError as e:
        return False, f"no answer at {SUB_PATH} ({type(e).__name__})"

    return r.status_code < 500, f"HTTP {r.status_code} at {SUB_PATH}"


def _was_connected(res: dict) -> dict[str, int]:
    return {str(n.get("name")): int(n.get("id") or 0) for n in res.get("nodes", []) if n.get("status") == "connected"}


def _nodes_back(c: dict, was: dict[str, int], limit: float) -> tuple[int, list[str]]:
    """Wait until every node that was connected before is connected again, or the time is up: (how many are connected, the names of those that were and are not)."""
    end = time.time() + limit

    while True:
        now = panels.check(c)
        have = _was_connected(now) if now.get("ok") else {}
        missing = [n for n in was if n not in have]

        if not missing or time.time() >= end:
            return len(have), missing

        time.sleep(pg_update.POLL)


def _panel_verify(aid: int, node, c: dict, v0: str, was: dict[str, int]) -> tuple[bool, str]:
    """After the panel service was recreated: it answers (one restart tried when it does not), the sign-in works, the subscription front door answers, every node that was connected is again (asked once more when
    not). (it holds, the new version | why not)."""
    say = autopilot._say
    after = pg_update._panel_up(c, pg_update.PANEL_LIMIT)

    if not after.get("ok"):
        say(aid, f"    the panel does not answer ({after.get('error') or 'no answer'}); its service is restarted once…")

        try:
            out = run_action(node, "pgpanel_restart", {}, timeout=400)
        except (SSHError, ValueError) as e:
            out = "ERROR " + str(e)[:100]

        if "RESTARTED" in out:
            after = pg_update._panel_up(c, PANEL_AGAIN)

        if not after.get("ok"):
            return False, f"the panel does not answer after its update ({after.get('error') or 'no answer'}), not after one restart either"

    v1 = (after.get("stats") or {}).get("version", "")
    ok_l, text_l = login_check(c)

    if not ok_l:
        return False, f"the panel answers ({v1 or '?'}) but the sign-in does not work ({text_l})"

    ok_f, text_f = front_door(c)

    if not ok_f:
        return False, f"the panel answers ({v1 or '?'}) but its subscription front door does not ({text_f})"

    n1, missing = _nodes_back(c, was, NODES_BACK)

    if missing:
        say(aid, f"    {len(missing)} node{'s' if len(missing) != 1 else ''} not connected again after {int(NODES_BACK // 60)} minutes ({', '.join(missing[:8])}); asking the panel once more to connect to them…")

        for name in missing:
            try:
                pg_nodes.node_reconnect(c, was[name])
            except (panels.PanelError, ValueError) as e:
                log.warning("reconnect of %s: %s", name, e)

        n1, missing = _nodes_back(c, was, 90)

        if missing:
            return False, f"the panel answers ({v1 or '?'}) but {len(missing)} node{'s' if len(missing) != 1 else ''} that {'were' if len(missing) != 1 else 'was'} connected before {'are' if len(missing) != 1 else 'is'} not after ({', '.join(missing[:8])})"

    say(aid, f"    the panel answers: version {v0 or '?'} → {v1 or '?'}, the sign-in works ({text_l}), the subscription front door answers ({text_f}), nodes connected {len(was)} → {n1}.")

    if v1 and v0 and pg_safe.short(v1) == pg_safe.short(v0):
        say(aid, "    the version did not change: the image that was pulled is the one that ran, or the panel reports its old version - compare with GitHub tomorrow.")

    return True, v1 or "?"


def _panel_back(aid: int, node, c: dict, p: dict, got: dict, was: dict[str, int], why: str) -> None:
    """The old image back and looked at; what the summary says about it."""
    say = autopilot._say
    name = p["name"]
    _hold("panel", p["latest"], name, why, got["day"])

    try:
        out = run_action(node, "pgpanel_rollback", {}, timeout=BACK_TIMEOUT)
    except (SSHError, ValueError) as e:
        out = "ERROR " + str(e)[:120]

    if "ROLLED_BACK" not in out:
        got["needs"].append(f"the panel {name}: {why}; the old image could NOT be put back ({_last(out)}) - look at it on its server now (the kept image, compose files and database dump are in {KEEP_DIR})")
        say(aid, f"    the old image could not be put back ({_last(out)}): look at the panel on its server now.")
        return

    after = pg_update._panel_up(c, pg_update.PANEL_LIMIT)

    if not after.get("ok"):
        got["needs"].append(f"the panel {name}: {why}; the old image is back but the panel does not answer ({after.get('error') or 'no answer'}) - look at it on its server now; the dump of its database from before the update is in {KEEP_DIR}")
        say(aid, f"    the old image is back but the panel does not answer ({after.get('error') or 'no answer'}): look at it on its server now.")
        return

    v1 = (after.get("stats") or {}).get("version", "")
    n1, missing = _nodes_back(c, was, NODES_BACK)
    text = f"the old image is back and answers ({v1 or '?'}), nodes connected {n1} of {len(was)}" + (f" - {', '.join(missing[:6])} not yet" if missing else "")
    got["back"].append(f"the panel {name}: {why}; {text}")
    say(aid, f"    {text}.")


def _panel_step(aid: int, p: dict, got: dict, dry: bool) -> None:
    """One PasarGuard panel: looked at, kept (its image, its compose files, a dump of its database), the new image pulled and the panel service alone recreated, looked at after (the version, the sign-in, the front
    door, the nodes), one restart tried when it does not answer, the old image put back when it still does not hold."""
    say = autopilot._say
    name = p["name"]

    try:
        c = panels.cred_for(p["id"])
        node, sname, _ip = extras._node(p["host"]["id"])
    except (panels.PanelError, ValueError) as e:
        got["needs"].append(f"the panel {name}: it could not be read ({str(e)[:120]})")
        return

    say(aid, f"Panel {name} ({pg_safe.short(p['version'])} → {pg_safe.short(p['latest'])}) on {sname}: looking at the server (read-only)…")

    try:
        info = parse_panel_info(run_script(node, "pgpanel_info", timeout=40))
    except (SSHError, ValueError) as e:
        got["left"].append(f"the panel {name}: its server did not answer ({str(e)[:100]})")
        say(aid, f"    its server did not answer ({str(e)[:120]}): nothing was changed.")
        return

    if info.get("container", "none") in ("", "none"):
        got["needs"].append(f"the panel {name}: no PasarGuard panel container runs on {sname} (the nightly update knows the panel as a service of a compose file: the official install, or one of your own)")
        say(aid, "    no PasarGuard panel container runs on that server: nothing was changed.")
        return

    over = int(info.get("overrides") or 0)
    db = info.get("db", "unknown")
    say(aid, f"    container {info['container']} ({info.get('ref', '?')}), compose service {info.get('service', '?')} in {info.get('wdir', '?')}, database: {db}"
        + (f"; {over} file{'s' if over != 1 else ''} of the panel replaced by your own through the override file - a new version may change them, look after the update" if over else "") + ".")

    if db not in ("postgres", "sqlite"):
        got["needs"].append(f"the panel {name}: its database is not a postgres container of its compose project and not the SQLite file, so no dump can be made and it is not updated by itself")
        say(aid, "    its database could not be found for a dump: the panel is not updated by itself.")
        return

    if dry:
        say(aid, f"    would keep the image that runs, the compose files and a dump of the {db} database; pull the new image and recreate the panel service alone; then check the version, the sign-in, the subscription front door and the nodes - and put the old image back when one of them fails.")
        got["updated"].append(f"the panel {name} ({pg_safe.short(p['version'])} → {pg_safe.short(p['latest'])})")
        return

    before = panels.check(c)

    if not before.get("ok"):
        got["left"].append(f"the panel {name}: it did not answer just before its update ({before.get('error') or 'no answer'})")
        say(aid, f"    the panel does not answer now ({before.get('error') or 'no answer'}): nothing was changed.")
        return

    v0, was = (before.get("stats") or {}).get("version", ""), _was_connected(before)
    say(aid, "    keeping the image that runs, the compose files and a dump of the database…")

    try:
        out = run_action(node, "pgpanel_keep", {}, timeout=KEEP_TIMEOUT)
    except (SSHError, ValueError) as e:
        out = "ERROR " + str(e)[:120]

    if "PANEL_KEPT" not in out or not any(w in out for w in ("DUMP postgres", "DUMP sqlite")):
        got["needs"].append(f"the panel {name}: it could not be kept before the update ({_last(out)}) - nothing was changed")
        say(aid, f"    the keep did not work ({_last(out)}): nothing was changed.")
        return

    dump = next((ln.split() for ln in out.splitlines() if ln.startswith("DUMP ")), [])
    say(aid, f"    kept: the image, the compose files, a {dump[1] if len(dump) > 1 else ''} dump" + (f" ({_mb(dump[3])})" if len(dump) > 3 else "") + f" in {KEEP_DIR}.")
    say(aid, "    pulling the new image and recreating the panel service alone (its database is not touched; the panel is away for a minute or two, the nodes keep serving the users that are connected)…")

    try:
        out = run_action(node, "pgpanel_update", {}, timeout=UPDATE_TIMEOUT)
    except (SSHError, ValueError) as e:
        out = "ERROR " + str(e)[:120]

    if "SAME_IMAGE" in out:
        got["left"].append(f"the panel {name}: the registry has no newer image than the one that runs yet (GitHub says {pg_safe.short(p['latest'])})")
        say(aid, "    the registry has no newer image than the one that runs: nothing was changed (the release is on GitHub, its image is not out yet).")
        return

    if any(w in out for w in ("PULL_FAILED", "NO_KEEP", "NO_DIR")) or "ERROR" in out.split("\n")[0]:
        got["left"].append(f"the panel {name}: the new image could not be pulled ({_last(out)}) - nothing was changed")
        say(aid, f"    the new image could not be pulled ({_last(out)}): nothing was changed.")
        return

    if "PANEL_UPDATED" not in out:
        say(aid, f"    the recreate did not finish well ({_last(out)}); putting the old image back…")
        _panel_back(aid, node, c, p, got, was, f"the recreate did not finish well ({_last(out)})")
        return

    say(aid, f"    the panel service was recreated with the new image; waiting until the panel answers (up to {int(pg_update.PANEL_LIMIT // 60)} minutes)…")
    ok, text = _panel_verify(aid, node, c, v0, was)

    if ok:
        got["updated"].append(f"the panel {name} ({pg_safe.short(v0)} → {pg_safe.short(text)})")

        with session() as db_:
            emit(db_, "ok", "panels", f"PasarGuard panel {name} updated by itself at night: {v0 or '?'} → {text}.", name)

        return

    say(aid, f"    {text}; putting the old image back…")
    _panel_back(aid, node, c, p, got, was, text)


# ------------------------------------------------------------------ the night
def _end(aid: int, got: dict, text: str | None, ok: bool) -> None:
    """The last line of the story, the settings' `last`, one event, one Telegram message (not for a rehearsal)."""
    text = text or summary(got)
    done_ok = bool(ok) and not got["needs"]
    autopilot._say(aid, ("Rehearsal done: " if got["dry"] else "Done: ") + text, done=True, ok=done_ok)

    if got["dry"]:
        return

    cfg.put("pg_auto", {"last": {"day": got["day"], "at": int(time.time()), "text": text[:400], "ok": done_ok, "action_id": aid, **{k: got[k][:40] for k in ("updated", "back", "left", "needs")}}})

    with session() as db:
        emit(db, "bad" if got["needs"] else "warn" if got["back"] or not ok else "info", "panels", "Nightly PasarGuard update: " + text[:600], "auto")

    sent, why = telegram.send_text(message(got))

    if not sent:
        log.info("nightly summary not sent to Telegram: %s", why)


def _night_story(aid: int, dry: bool = False) -> None:
    say = autopilot._say
    s = state()
    t0 = time.time()
    local = engines._local(t0)
    day = time.strftime("%Y-%m-%d", local)
    budget = max(5, s["budget_minutes"]) * 60
    got: dict = {"updated": [], "back": [], "left": [], "needs": [], "skipped": [], "dry": dry, "day": day, "aid": aid}
    say(aid, ("Rehearsal of the nightly update: every step is walked, nothing is changed" if dry else f"Nightly update of PasarGuard ({day} {local.tm_hour:02d}:{local.tm_min:02d}, panel clock)")
        + f". The panel and its nodes are looked at first; no node is started after {budget // 60} minutes and the panel only in the first {PANEL_BY} minutes of its hour - the rest waits for the next night.")

    try:
        pl = pg_safe.plan(force=True, scope="all")
    except (panels.PanelError, ValueError, RuntimeError, httpx.HTTPError) as e:
        _end(aid, got, f"the panel, its nodes or GitHub could not be read ({str(e)[:160]}): nothing was done", ok=False)
        return

    if pl["problems"]:
        _end(aid, got, pl["problems"][0], ok=False)
        return

    latest, bad = pl["latest"], s["bad"]
    node_tag, xray_tag = latest.get("node") or "", latest.get("xray") or ""
    held_sw, held_xr = _held(bad, "node", node_tag), _held(bad, "xray", xray_tag)

    for h, what in ((held_sw, "the node software"), (held_xr, "Xray")):
        if h:
            got["needs"].append(f"{what} {pg_safe.short(h['version'])} is held back: it did not hold on {h.get('where') or '?'} on {h.get('day') or '?'} ({h.get('why') or ''}) - not tried again by itself; the Safe update buttons still try it")

    todo = []

    for n in pl["nodes"]:
        do = [d for d in n["do"] if not ((d.startswith("software") and held_sw) or (d.startswith("Xray") and held_xr))]

        if do:
            todo.append({**n, "do": do})
        else:
            got["skipped"].append(f"{n['name']}: only a release that is held back")

    got["skipped"] += [f"{x['name']}: {x['why']}" for x in pl["skipped"]]
    panel_todo = []

    for p in pl["panels"]:
        h = _held(bad, "panel", p["latest"])

        if p["skip"]:
            (got["needs"] if "Servers list" in p["skip"] or "keep-alive" in p["skip"] else got["left"]).append(f"the panel {p['name']}: {p['skip']}")
        elif h:
            got["needs"].append(f"the panel {p['name']}: {pg_safe.short(h['version'])} is held back (it did not hold on {h.get('day') or '?'}: {h.get('why') or ''}) - the Safe update button still tries it")
        else:
            panel_todo.append(p)

    if not todo and not panel_todo:
        _end(aid, got, None if got["skipped"] or got["left"] or got["needs"] else "everything is up to date, nothing to do tonight", ok=True)
        return

    if todo:
        say(aid, f"{len(todo)} node{'s' if len(todo) != 1 else ''}, the quietest first: " + ", ".join(f"{n['name']} ({n['usage_text']}: {' + '.join(n['do'])})" for n in todo[:20]) + ("…" if len(todo) > 20 else "") + ".")

    if panel_todo:
        say(aid, f"Then the panel {panel_todo[0]['name']} ({pg_safe.short(panel_todo[0]['version'])} → {pg_safe.short(panel_todo[0]['latest'])}) on {panel_todo[0]['host']['name']}.")

    stopped = False

    for i, row in enumerate(todo, 1):
        if time.time() - t0 > budget:
            names = [r["name"] for r in todo[i - 1:]]
            got["left"].append(f"{len(names)} node{'s' if len(names) != 1 else ''} ({', '.join(names[:12])}{'…' if len(names) > 12 else ''}): the time budget of {budget // 60} minutes was used up")
            say(aid, f"The time budget of {budget // 60} minutes is used up: {', '.join(names)} wait{'s' if len(names) == 1 else ''} for the next night.")
            break

        watch = configs_of_server(row["server"]["id"])
        up = [w["name"] for w in watch if w["status"] == "up"]
        sw_tag = node_tag if any(d.startswith("software") for d in row["do"]) else ""
        xr_tag = xray_tag if any(d.startswith("Xray") for d in row["do"]) else ""

        if dry:
            say(aid, f"{i}/{len(todo)} {row['name']} (on {row['server']['name']}, {row['usage_text']} now): would get " + " + ".join(row["do"]) + "; " + _watch_text(watch, up) + ".")
            got["updated"].append(f"{row['name']} ({', '.join(row['do'])})")
            continue

        base = _probe_base()
        ok, why, part = pg_safe._update_node(aid, i, len(todo), row, sw_tag, xr_tag)

        if not ok:
            say(aid, pg_safe.stop_line(i, len(todo), row["name"], why))

            if part == "xray" and sw_tag:
                got["updated"].append(f"{row['name']} (software only)")

            _after_failure(got, row["name"], [part] if part else [], why, latest, day)
            stopped = True
            break

        ok_u, text_u = users_check(watch, base)
        say(aid, f"    users: {text_u}")

        if not ok_u:
            say(aid, f"    the users do not pass through {row['name']} with the new version: putting the old versions back…")
            back = _back_both(row, sw_tag, xr_tag)
            say(aid, pg_safe.stop_line(i, len(todo), row["name"], back + "."))
            _after_failure(got, row["name"], [p_ for p_, t in (("software", sw_tag), ("xray", xr_tag)) if t], text_u.rstrip(".") + " - " + back, latest, day)
            stopped = True
            break

        got["updated"].append(f"{row['name']} ({', '.join(row['do'])})")

    if stopped:
        for p in panel_todo:
            got["left"].append(f"the panel {p['name']}: the run stopped at a node before the panel")

        with session() as db:
            emit(db, "warn", "panels", f"Nightly PasarGuard update stopped at a node that did not hold ({got['back'][-1] if got['back'] else got['needs'][-1] if got['needs'] else '?'})"[:600], "auto")

        _end(aid, got, None, ok=False)
        return

    if panel_todo:
        p = panel_todo[0]
        loc = engines._local(time.time())

        if not dry and (time.time() - t0 > budget or loc.tm_hour != s["hour"] or loc.tm_min >= PANEL_BY):
            got["left"].append(f"the panel {p['name']}: it is only started in the first {PANEL_BY} minutes of its hour and inside the time budget (it was {loc.tm_hour:02d}:{loc.tm_min:02d} on the panel clock)")
            say(aid, f"The panel {p['name']} is not started now ({loc.tm_hour:02d}:{loc.tm_min:02d} on the panel clock): it waits for the next night.")
        else:
            _panel_step(aid, p, got, dry)

    _end(aid, got, None, ok=not got["back"] and not got["needs"])
