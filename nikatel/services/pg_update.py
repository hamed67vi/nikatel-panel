"""PasarGuard updates, from Settings (the owner, 2026-10-14: "add the update of the nodes and of PasarGuard itself to Settings").

What there is to update, and how each is done:
  * a NODE (its own software, and the Xray core on it): the panel has routes for both (`POST /api/node/{id}/update`, `.../core_update`, services/pg_nodes.node_maintenance). A node that is updated
    restarts, and the users on it drop - so several nodes are updated ROLLING (`nodes_start`): one at a time, each until it is connected again with the new version; one that does not come back
    stops the run, and the nodes after it are left as they were.
  * the PANEL itself: PasarGuard has no route for it. The official command on the panel's own server is `pasarguard update` (pull the new images, restart), so this panel does what the owner
    would type over SSH: on the server that runs the panel (found by the address of its URL among our servers) one reviewed, detached action (`install_pgpanel_update`: a backup first when the
    command has one, then the update) that the panel follows, and then waits until the panel answers with the new version. It is refused when the server that runs the panel is not in the
    Servers list (nothing can be done over SSH to a machine the panel has no login for), when the name of the panel is not typed back, and in read-only mode.
  * what is the newest: the releases of PasarGuard/panel, PasarGuard/node and XTLS/Xray-core on GitHub, kept for an hour (`latest_tags`).
Nothing here is automatic: a version that is behind is only shown; every update is a click and a question."""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Panel
from ..ssh import SSHError, run_script
from . import autopilot, extras, panels, pg_nodes
from .events import emit

log = get_logger("pg_update")
REPOS = {"panel": "PasarGuard/panel", "node": "PasarGuard/node", "xray": "XTLS/Xray-core"}
CACHE_TTL = 3600.0
NODE_LIMIT = 300.0                    # seconds a node gets to come back after its update
PANEL_LIMIT = 420.0                   # seconds the panel gets to answer again after `pasarguard update`
MAX_NODES = 40
POLL = 5.0
WHAT = ("node", "core")
_LATEST: dict[str, tuple[float, dict]] = {}


# ------------------------------------------------------------------ pure: versions
def ver(text) -> tuple[int, int, int] | None:
    """(major, minor, patch) of 'v5.4.1', '5.4.1', '26.3.27', 'v0.5': a missing patch is 0, a suffix ('-rc1', ' (build 3)') is ignored; None when there is no version in it."""
    m = re.match(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def newer(latest, current) -> bool | None:
    """Is `latest` newer than `current`? None when either is not a version (a node that was never connected knows none): then nothing is claimed."""
    a, b = ver(latest), ver(current)
    return None if a is None or b is None else a > b


# ------------------------------------------------------------------ what is the newest
def _fetch(repo: str) -> dict:
    r = httpx.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=15, headers={"Accept": "application/vnd.github+json", "User-Agent": "nikatel-panel"})
    r.raise_for_status()
    rel = r.json()
    tag = str(rel.get("tag_name", "")).strip()

    if ver(tag) is None:
        raise RuntimeError("the release has no version number")

    return {"version": "v" + tag.lstrip("v"), "page": str(rel.get("html_url", ""))[:200], "published": str(rel.get("published_at", ""))[:10]}


def latest_tags(force: bool = False) -> dict[str, dict]:
    """{panel, node, xray: {version, page, published} or {error}}: GitHub is asked once an hour (60 requests an hour are allowed without a login) or when `force` says so."""
    now, out = time.time(), {}

    for key, repo in REPOS.items():
        hit = _LATEST.get(key)

        if hit and not force and hit[0] > now:
            out[key] = hit[1]
            continue

        try:
            out[key] = _fetch(repo)
            _LATEST[key] = (now + CACHE_TTL, out[key])
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            out[key] = hit[1] if hit else {"error": str(e)[:140]}          # the last answer is better than none when GitHub does not answer now

    return out


# ------------------------------------------------------------------ what is installed
def host_server(url: str, resolver=None) -> dict | None:
    """The server of ours that runs the panel at `url` (its host name or address is the host of a server, or the DNS says so), None when there is none."""
    host = (urlparse(url or "").hostname or "").strip()

    if not host:
        return None

    servers, probe = pg_nodes._servers(), [{"address": host, "name": ""}]
    names = pg_nodes.names_to_resolve(probe, servers)
    resolved = (resolver or pg_nodes.resolve_names)(names) if names else {}
    pairs, _free = pg_nodes.match_servers(probe, servers, resolved)
    return pairs[0]["server"]


def _panels() -> list[dict]:
    with session() as db:
        return [{"id": p.id, "name": p.name, "url": p.url, "version": p.version or "", "status": p.status, "enabled": bool(p.enabled)}
                for p in db.scalars(select(Panel).where(Panel.type == "pasarguard").order_by(Panel.id))]


def overview(force: bool = False, resolver=None) -> dict:
    """What Settings shows: the newest versions, every PasarGuard panel (installed version, the server that runs it) and every node of them (software and Xray, behind or not). A panel that does not
    answer is listed with the reason, the others still are."""
    tags = latest_tags(force)
    tag = lambda k: (tags.get(k) or {}).get("version", "")
    out_panels, out_nodes = [], []

    for p in _panels():
        host = host_server(p["url"], resolver)
        row = {**p, "latest": tag("panel"), "behind": newer(tag("panel"), p["version"]), "host": {"id": host["id"], "name": host["name"], "online": host["online"], "enabled": host["enabled"]} if host else None,
               "host_name": (urlparse(p["url"]).hostname or ""), "error": ""}
        out_panels.append(row)

        if not p["enabled"]:
            row["error"] = "this panel is switched off in the list of panels"
            continue

        try:
            c = panels.cred_for(p["id"])
            raw = pg_nodes._nodes_raw(c)
            pg_nodes.keep_cache(c, raw)
            rows = [pg_nodes.public_node(n) for n in raw]
            joined, _free = pg_nodes._join(rows, resolver)
        except (panels.PanelError, ValueError) as e:
            row["error"] = str(e)[:160]
            continue

        for n, j in zip(rows, joined):
            s = j.get("server")
            out_nodes.append({"panel_id": p["id"], "panel": p["name"], "node_id": n["id"], "name": n["name"], "address": n["address"], "status": n["status"], "version": n["version"], "xray": n["xray"],
                              "keep_alive": n["keep_alive"], "behind": newer(tag("node"), n["version"]), "xray_behind": newer(tag("xray"), n["xray"]), "server": {"id": s["id"], "name": s["name"]} if s else None})

    return {"latest": tags, "panels": out_panels, "nodes": out_nodes, "read_only": get_settings().read_only, "checked_at": int(time.time())}


# ------------------------------------------------------------------ the nodes, one at a time
def _clean_items(items: list[dict]) -> list[dict]:
    out, seen = [], set()

    for it in items or []:
        try:
            pid, nid, what = int(it["panel_id"]), int(it["node_id"]), str(it.get("what", "node"))
        except (KeyError, TypeError, ValueError):
            raise ValueError("each item needs panel_id, node_id and what (node or core)")

        if what not in WHAT:
            raise ValueError("what is node (the software of the node) or core (Xray)")

        if (pid, nid, what) not in seen:
            seen.add((pid, nid, what))
            out.append({"panel_id": pid, "node_id": nid, "what": what})

    if not out:
        raise ValueError("choose at least one node")

    if len(out) > MAX_NODES:
        raise ValueError(f"at most {MAX_NODES} updates in one run")

    return out


def _wait_back(c: dict, node_id: int, what: str, before: dict, limit: float) -> tuple[bool, dict]:
    """Look at the node until it is connected again with a new version (software or Xray), or the time is up: (it came back changed, the last row). A node that is connected but still shows the old
    version is looked at to the end of the time: the update may need a moment to start."""
    end, last = time.time() + limit, {}
    key = "version" if what == "node" else "xray"

    while True:
        try:
            last = pg_nodes.public_node(pg_nodes._node_raw(c, node_id))
        except (panels.PanelError, ValueError):
            last = last or {}

        if last.get("status") == "connected" and last.get(key) and last.get(key) != before.get(key):
            return True, last

        if time.time() >= end:
            return False, last

        time.sleep(POLL)


def _nodes_story(aid: int, items: list[dict]) -> None:
    say, tags = autopilot._say, latest_tags()
    node_tag, xray_tag = (tags.get("node") or {}).get("version", ""), (tags.get("xray") or {}).get("version", "")
    todo = len(items)
    say(aid, f"{todo} update{'s' if todo != 1 else ''} to do, one at a time: each node must be connected again with its new version before the next one starts.")
    done = 0

    for i, it in enumerate(items, 1):
        try:
            c = panels.cred_for(it["panel_id"])
            before = pg_nodes.public_node(pg_nodes._node_raw(c, it["node_id"]))
        except (panels.PanelError, ValueError) as e:
            say(aid, f"Stopped at {i}/{todo}: the node could not be read ({str(e)[:140]}). The nodes after it were not touched.", done=True, ok=False)
            return

        name, what = before.get("name") or f"#{it['node_id']}", it["what"]
        label = "software" if what == "node" else "Xray"
        now, want = (before.get("version"), node_tag) if what == "node" else (before.get("xray"), xray_tag)

        if newer(want, now) is False:
            say(aid, f"{i}/{todo} {name}: {label} {now} is the newest already - skipped.")
            done += 1
            continue

        if before.get("status") != "connected":
            say(aid, f"Stopped at {i}/{todo}: {name} is {before.get('status') or 'not connected'} now. An update of a node that does not answer would not tell what it did; connect it first. The nodes after it were not touched.", done=True, ok=False)
            return

        say(aid, f"{i}/{todo} {name}: {label} {now or '?'} → {want or 'newest'}: asking the panel to update it (its users drop for a moment)…")

        try:
            if what == "node":
                pg_nodes.node_maintenance(c, it["node_id"], "node")
            else:
                pg_nodes.node_maintenance(c, it["node_id"], "core", want.lstrip("v") if want else "latest")
        except (panels.PanelError, ValueError) as e:
            say(aid, f"Stopped at {i}/{todo}: the panel refused - {str(e)[:160]}. The nodes after it were not touched.", done=True, ok=False)
            return

        back, last = _wait_back(c, it["node_id"], what, before, NODE_LIMIT)

        if not back:
            why = last.get("status") or "no answer"
            say(aid, f"Stopped at {i}/{todo}: {name} is {why}" + (f" ({last.get('message')})" if last.get("message") else "") + f" and still shows {label} {(last.get('version') if what == 'node' else last.get('xray')) or '?'} after {int(NODE_LIMIT // 60)} minutes. "
                "Look at its log in the Node view of its server (Servers → the server → Node → Log). The nodes after it were not touched.", done=True, ok=False)
            return

        done += 1
        say(aid, f"    {name} is connected again: {label} {(last.get('version') if what == 'node' else last.get('xray'))}.")

    say(aid, f"Done: {done} of {todo} updated or already the newest.", done=True, ok=True)

    with session() as db:
        emit(db, "ok", "panels", f"PasarGuard nodes updated one at a time ({done} of {todo}).", "")


def nodes_start(items: list[dict]) -> int:
    """Start the rolling update of nodes as an action (returns its id; the story is read with /api/actions/{id}). Every node must exist, and only one such run goes at a time."""
    items = _clean_items(items)

    for it in items:
        try:
            pg_nodes._node_raw(panels.cred_for(it["panel_id"]), it["node_id"])
        except (panels.PanelError, ValueError) as e:
            raise ValueError(f"node {it['node_id']} of panel {it['panel_id']}: {str(e)[:120]}")

    return autopilot._start("pg node updates", _nodes_story, items, action="pgupdate", source="manual", allow_read_only=True)          # (a panel's own API, like every other action on a node)


# ------------------------------------------------------------------ the panel itself
def parse_info(out: str) -> dict:
    """Pure: the lines of the read-only look at the panel's server ('CLI yes', 'BACKUP no', 'DIR /opt/pasarguard', 'DOCKER 3', 'DF 123456') -> a dict."""
    d: dict = {}

    for ln in str(out).splitlines():
        p = ln.strip().split(None, 1)

        if len(p) == 2 and p[0] in ("CLI", "BACKUP", "DIR", "DOCKER", "DF"):
            d[p[0].lower()] = p[1].strip()

    return d


def panel_plan(panel_id: int, resolver=None) -> dict:
    """What updating the panel needs and what stands in the way (nothing is changed, nothing is asked of the server): the panel, the server that runs it, the versions."""
    p = next((x for x in _panels() if x["id"] == int(panel_id)), None)

    if p is None:
        raise ValueError("no such PasarGuard panel")

    tags, host, problems = latest_tags(), host_server(p["url"], resolver), []
    latest = (tags.get("panel") or {}).get("version", "")

    if get_settings().read_only:
        problems.append("The panel is read-only: set NIKATEL_READ_ONLY=false on the panel server to let it update a server.")

    if not p["enabled"]:
        problems.append("This panel is switched off in the list of panels.")

    if host is None:
        problems.append(f"The server that runs this panel ({urlparse(p['url']).hostname or p['url']}) is not in the Servers list: add it (Servers → Add server, with its SSH login) and this panel can update it over SSH.")
    elif not host["enabled"]:
        problems.append(f"The server {host['name']} is switched off in the Servers list.")
    elif host["online"] is False:
        problems.append(f"The server {host['name']} does not answer.")

    if newer(latest, p["version"]) is False:
        problems.append(f"The panel is at {p['version']}: that is the newest ({latest}).")

    return {"ok": not problems, "problems": problems, "panel": p, "latest": latest, "host": {"id": host["id"], "name": host["name"]} if host else None,
            "steps": ["Look at the server (read-only): is the pasarguard command there?", "A backup first (the pasarguard backup command, when it has one).", "pasarguard update: the new images are pulled and the panel restarts (a minute or two).",
                      "Wait until the panel answers again, and compare the version and the nodes that are connected."]}


def _panel_up(c: dict, limit: float) -> dict:
    """Ask the panel until it answers (or the time is up): its /api/system as panels.check reads it."""
    end, last = time.time() + limit, {"ok": False, "error": "no answer"}

    while True:
        last = panels.check(c)

        if last.get("ok") or time.time() >= end:
            return last

        time.sleep(POLL)


def _panel_story(aid: int, panel_id: int, server_id: int, backup: bool) -> None:
    say = autopilot._say

    try:
        c = panels.cred_for(panel_id)
        node, sname, ip = extras._node(server_id)
    except ValueError as e:
        say(aid, f"Stopped: {e}", done=True, ok=False)
        return

    pname = next((x["name"] for x in _panels() if x["id"] == panel_id), "the panel")
    before = panels.check(c)
    v0, users0 = (before.get("stats") or {}).get("version", ""), (before.get("stats") or {}).get("users_total")
    n0 = sum(1 for n in before.get("nodes", []) if n.get("status") == "connected"), len(before.get("nodes", []))
    say(aid, f"Updating the PasarGuard panel {pname} ({v0 or 'version unknown'}) on {sname}: a backup, the new images, a restart, then a look at the panel and its nodes.")
    say(aid, "1/4 Looking at the server (read-only)…")

    try:
        info = parse_info(run_script(node, "pgpanel_info", timeout=30))
    except (SSHError, ValueError) as e:
        say(aid, f"Stopped at step 1: the server did not answer ({str(e)[:140]}). Nothing was changed.", done=True, ok=False)
        return

    if info.get("cli") != "yes":
        say(aid, "Stopped at step 1: the pasarguard command is not on that server, so this panel was not installed with the official script (or it runs somewhere else). Nothing was changed: update it the way it was installed.", done=True, ok=False)
        return

    say(aid, f"    pasarguard command found; {info.get('docker', '0')} PasarGuard container(s) running; backup command: {info.get('backup', 'no')}.")
    say(aid, f"2/4 {'Backing up and updating' if backup else 'Updating (no backup was asked for)'} on {sname}: the panel is away for a minute or two; its nodes keep serving the users that are connected.")
    ok = extras._run_item(aid, node, ip, "pasarguard_panel", "1" if backup else "0")

    if not ok:
        say(aid, "Stopped at step 2: the update did not end well (the lines above). The panel may still run the old version - look at it on the server: pasarguard logs. Nothing else was done.", done=True, ok=False)
        return

    say(aid, f"3/4 Waiting until the panel answers again (up to {int(PANEL_LIMIT // 60)} minutes)…")
    time.sleep(POLL)
    after = _panel_up(c, PANEL_LIMIT)

    if not after.get("ok"):
        say(aid, f"Stopped at step 3: the panel does not answer ({after.get('error') or 'no answer'}). Look at it on the server: pasarguard status, pasarguard logs.", done=True, ok=False)
        return

    v1, users1 = (after.get("stats") or {}).get("version", ""), (after.get("stats") or {}).get("users_total")
    time.sleep(POLL * 4)                                                                               # the nodes need a moment to connect to the restarted panel
    now = panels.check(c)
    n1 = sum(1 for n in now.get("nodes", []) if n.get("status") == "connected"), len(now.get("nodes", []))
    say(aid, f"4/4 The panel answers: version {v0 or '?'} → {v1 or '?'}; users {users0 if users0 is not None else '?'} → {users1 if users1 is not None else '?'}; nodes connected {n0[0]} of {n0[1]} → {n1[0]} of {n1[1]}.")
    changed = bool(v1) and v1 != v0
    lost = n1[0] < n0[0]

    with session() as db:
        emit(db, "ok" if changed and not lost else "warn", "panels", f"PasarGuard panel {pname} updated: {v0 or '?'} → {v1 or '?'}.", pname)

    if lost:
        say(aid, "Some nodes are not connected yet: they reconnect by themselves within a minute or two; if one does not, open it in Servers → its window → Node.", done=True, ok=True)
    elif not changed:
        say(aid, "The version did not change: the panel was at the newest already, or the update did not take. Compare with the newest release (Settings → PasarGuard).", done=True, ok=True)
    else:
        say(aid, "Done.", done=True, ok=True)


def panel_start(panel_id: int, backup: bool, typed: str, resolver=None) -> int:
    """Start the update of a PasarGuard panel as an action (returns its id). Refused with the first problem the plan finds, and unless the name of the panel is typed back."""
    plan = panel_plan(panel_id, resolver)

    if plan["problems"]:
        raise ValueError(plan["problems"][0])

    p = plan["panel"]

    if str(typed or "").strip() != p["name"]:
        raise ValueError(f"type the name of the panel ({p['name']}) to confirm")

    label = f"pgpanel {p['name']}"[:60]

    if label in autopilot._BUSY:
        raise ValueError(f"{p['name']} is being updated right now: follow it in the history of the Tunnel Doctor")

    return autopilot._start(label, _panel_story, int(panel_id), int(plan["host"]["id"]), bool(backup), action="pgupdate", source="manual")
