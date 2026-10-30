"""Safe update of PasarGuard: ONE button for the panel and its nodes (the owner, 2026-10-31, with a picture of the PasarGuard card: "put ONE button for the panel and the nodes together, called Safe update - it must update
everything in a way that the customers are not cut at all - and it must start from the node with the LEAST usage").

What it does, and what it cannot do:
  * The order: the nodes first, the one that carries the LEAST traffic now first (the traffic each node reports about itself, looked at three times), one node at a time; the panel itself LAST, after every node
    proved the new software. The panel is the one machine whose restart touches everything, so it comes when the new software has already been tried on the quietest nodes.
  * Each node is looked at BEFORE (its server: the pg-node command, docker, the container, free space; the panel: connected) and AFTER every step (connected again with the new version, then watched for 45 s). Its
    old image is kept before the update (`docker tag`, noted in /var/lib/pg-node/nikatel-prev.txt) and its old Xray version is known: a node that does not come back, or does not stay connected, gets the OLD
    version back at once, and the run stops there - the nodes after it are not touched.
  * The panel's own route for updating a node does not work on the owner's nodes (503: they were installed without the pg-node maintenance service), so the nodes are updated over SSH with the official commands
    `pg-node update -y` and `pg-node core-update --version vX -y`, then the panel is asked to connect to the node again.
  * The PANEL: a node whose keep-alive is on (more than 0) stops its Xray core when the panel has been silent that long (pg-node `keepAliveTracker`), which drops everybody on it. The panel update is only started
    when no node has a keep-alive between 1 and 900 seconds (0 = never stops); otherwise it is NOT started and the story says which nodes and what to change - nothing is changed in a node's settings by this.
    A node that keeps a running core with the same config does NOT restart it when the panel connects again (pg-node `StartOrAttach`: a heartbeat), so the users stay.
  * What it cannot do: a node that is updated RESTARTS (its container, then its Xray): the connections through it drop for about 10-20 seconds each time. Least used first, one at a time and the way back make that
    as small as it can be; it is not zero.
Nothing here is automatic: it is one click and one question, and the name of the panel typed back when the panel is part of it."""
from __future__ import annotations

import time

from ..db import session
from ..log import get_logger
from ..ssh import SSHError, run_action, run_script
from . import autopilot, extras, panels, pg_nodes, pg_update
from .events import emit

log = get_logger("pg_safe")
KEEP_ALIVE_SAFE = 900                 # seconds: a node whose keep-alive is on and shorter than this stops its core when the panel is away that long
FREE_KB = 400_000                     # free space on /var/lib a node needs for a new image (about 400 MB)
SOAK = 45.0                           # seconds a node is watched after it came back
BACK_LIMIT = 150.0                    # seconds an old version gets to be connected again
RUN_TIMEOUT = 420                     # seconds an update command may run on a node
USAGE_ROUNDS = 3
USAGE_GAP = 2.0
LABEL = "pg safe update"

NOTE = ("A node that is updated restarts: the connections through it drop for about 10-20 seconds, once for its software and once more for Xray. The quietest node goes first, one at a time, each is looked at "
        "before and after, and its old version is kept: a node that does not come back or does not stay connected gets the old version back and the run stops there. It cannot be made zero - it is made as small as it can be.")


# ------------------------------------------------------------------ pure
def parse_node_info(out: str) -> dict:
    """The lines of the read-only look at a node's server ('CLI yes', 'DOCKER yes', 'RUNNING 1', 'REF pasarguard/node:latest', 'ID sha256:..', 'DIR /opt/pg-node', 'DF 123456') -> a dict."""
    d: dict = {}

    for ln in str(out).splitlines():
        p = ln.strip().split(None, 1)

        if len(p) == 2 and p[0] in ("CLI", "DOCKER", "RUNNING", "REF", "ID", "DIR", "DF"):
            d[p[0].lower()] = p[1].strip()

    return d


def node_blockers(info: dict) -> list[str]:
    """Why a node's server cannot be updated safely now (empty: it can). Unknown free space does not block."""
    why: list[str] = []

    if info.get("cli") != "yes":
        why.append("the pg-node command is not on its server (the node was not installed with the official script)")

    if info.get("docker") != "yes":
        why.append("docker is not on its server")
    elif info.get("running") != "1":
        why.append("the node container is not running on its server")

    try:
        free = int(info.get("df", ""))
    except ValueError:
        free = 0

    if 0 < free < FREE_KB:
        why.append(f"only {free // 1024} MB are free on /var/lib of its server (a new image needs about {FREE_KB // 1024} MB)")

    return why


def rank_nodes(rows: list[dict], usage: dict) -> list[dict]:
    """The quietest node first (`usage`: {(panel id, node id): bytes per second}); a node whose traffic is not known goes after those that are, then by name."""
    def key(r):
        k = (r["panel_id"], r["node_id"])
        return (k not in usage, usage.get(k, 0.0), r["name"])

    return sorted(rows, key=key)


def keep_alive_blockers(nodes: list[dict], minimum: int = KEEP_ALIVE_SAFE) -> list[dict]:
    """The nodes that would stop their core while the panel is away: a keep-alive that is on (more than 0) and shorter than `minimum` seconds."""
    out = []

    for n in nodes:
        try:
            ka = int(n.get("keep_alive") or 0)
        except (TypeError, ValueError):
            ka = 0

        if 0 < ka < minimum:
            out.append({"name": n.get("name", "?"), "keep_alive": ka})

    return out


def mbit(bps) -> str:
    return "—" if bps is None else f"{float(bps) * 8 / 1e6:.1f} Mbit/s"


def short(v) -> str:
    """'v0.5.6' and '0.5.6' are the same version: what a node reports has no 'v', what GitHub calls a release has."""
    return str(v or "").strip().lstrip("v")


# ------------------------------------------------------------------ how busy each node is
def sample_usage(c: dict, rounds: int = USAGE_ROUNDS, gap: float = USAGE_GAP) -> dict[int, float]:
    """{node id: bytes per second}: the mean of a few looks at what each node reports about itself (/api/nodes/realtime_stats: its network speed, in and out). A node that reports nothing is not in it."""
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}

    for i in range(rounds):
        for k, v in pg_nodes._live_all(c, []).items():
            if not v:
                continue

            try:
                nid = int(k)
            except ValueError:
                continue

            sums[nid] = sums.get(nid, 0.0) + float(v.get("rx") or 0) + float(v.get("tx") or 0)
            counts[nid] = counts.get(nid, 0) + 1

        if i + 1 < rounds:
            time.sleep(gap)

    return {k: sums[k] / counts[k] for k in sums}


# ------------------------------------------------------------------ the plan (nothing is changed, no server is asked)
def plan(resolver=None, force: bool = False) -> dict:
    """What a Safe update would do now, in order, and what stands in the way: {can_start, nothing, problems, nodes [the order], skipped, panels, steps, note}."""
    data = pg_update.overview(force=force, resolver=resolver)
    problems: list[str] = []

    if data.get("read_only"):
        problems.append("The panel is read-only: set NIKATEL_READ_ONLY=false on the panel server to let it update a server.")

    todo, skipped = [], []

    for n in data["nodes"]:
        sw, xr = n["behind"] is True, n["xray_behind"] is True

        if not sw and not xr:
            continue

        if n["status"] != "connected":
            skipped.append({"name": n["name"], "why": f"it is {n['status'] or 'not connected'}: connect it first (an update of a node that does not answer would not tell what it did)"})
        elif not n["server"]:
            skipped.append({"name": n["name"], "why": "no server of ours has its address, so it cannot be updated over SSH"})
        else:
            todo.append(n)

    usage: dict = {}

    for pid in sorted({n["panel_id"] for n in todo}):
        try:
            for nid, bps in sample_usage(panels.cred_for(pid)).items():
                usage[(pid, nid)] = bps
        except (panels.PanelError, ValueError):
            pass

    rows = []

    for n in rank_nodes(todo, usage):
        k = (n["panel_id"], n["node_id"])
        what = (["software " + short(n["version"]) + " → " + short((data["latest"].get("node") or {}).get("version")) if n["behind"] is True else ""]
                + ["Xray " + short(n["xray"]) + " → " + short((data["latest"].get("xray") or {}).get("version")) if n["xray_behind"] is True else ""])
        rows.append({"panel_id": n["panel_id"], "node_id": n["node_id"], "name": n["name"], "server": n["server"], "usage": usage.get(k), "usage_text": mbit(usage.get(k)),
                     "version": n["version"], "xray": n["xray"], "do": [w for w in what if w]})

    panel_rows = []

    for p in data["panels"]:
        if p["behind"] is not True:
            continue

        mine = [x for x in data["nodes"] if x["panel_id"] == p["id"]]
        ka = keep_alive_blockers(mine)
        skip = ""

        if p.get("error"):
            skip = p["error"]
        elif not p["host"]:
            skip = "its server is not in the Servers list: add it first (Servers → Add server), then the panel can be updated over SSH"
        elif p["host"].get("online") is False:
            skip = f"its server {p['host']['name']} does not answer"
        elif ka:
            skip = ("these nodes stop their Xray when the panel is away longer than their keep-alive and would drop everybody on them: " + ", ".join(f"{k['name']} ({k['keep_alive']} s)" for k in ka[:6])
                    + f". Set the keep-alive of each to 0 (never) or at least {KEEP_ALIVE_SAFE} in the node's settings, then run Safe update again")

        panel_rows.append({"id": p["id"], "name": p["name"], "version": p["version"], "latest": p["latest"], "host": p["host"], "skip": skip})

    nothing = not rows and not any(not p["skip"] for p in panel_rows)
    steps = []

    if rows:
        steps.append(f"{len(rows)} node{'s' if len(rows) != 1 else ''}, the quietest first, one at a time: its server is looked at, its old version is kept, it is updated over SSH, connected again and watched for {int(SOAK)} s.")
        steps.append("A node that does not come back or does not stay connected gets its old version back, and the run stops there.")

    if any(not p["skip"] for p in panel_rows):
        steps.append("Then the panel, on its own server: a backup first, `pasarguard update`, and a look at the panel and its nodes after.")

    return {"can_start": not problems and not nothing, "nothing": nothing, "problems": problems, "nodes": rows, "skipped": skipped, "panels": panel_rows, "steps": steps, "note": NOTE,
            "latest": {k: (v or {}).get("version", "") for k, v in (data.get("latest") or {}).items()}}


# ------------------------------------------------------------------ one node
def _row(c: dict, node_id: int) -> dict:
    return pg_nodes.public_node(pg_nodes._node_raw(c, node_id))


def _wait_state(c: dict, node_id: int, key: str, want: str, limit: float) -> tuple[bool, dict]:
    """Look at the node until it is connected AND shows `want` for `key` (version / xray), or the time is up: (it did, the last row)."""
    end, last = time.time() + limit, {}

    while True:
        try:
            last = _row(c, node_id)
        except (panels.PanelError, ValueError):
            last = last or {}

        if last.get("status") == "connected" and short(last.get(key)) == short(want) and short(want):
            return True, last

        if time.time() >= end:
            return False, last

        time.sleep(pg_update.POLL)


def _soak(c: dict, node_id: int, key: str, want: str, seconds: float = SOAK) -> tuple[bool, str]:
    """The node must stay connected with `want` for `seconds`: two looks in a row that say otherwise are a failure (the sentence)."""
    end, bad = time.time() + seconds, 0

    while time.time() < end:
        time.sleep(pg_update.POLL)

        try:
            row = _row(c, node_id)
        except (panels.PanelError, ValueError):
            row = {}

        if row.get("status") == "connected" and short(row.get(key)) == short(want):
            bad = 0
            continue

        bad += 1

        if bad >= 2:
            return False, f"it is {row.get('status') or 'not answering'}" + (f" ({row.get('message')})" if row.get("message") else "") + " after it had come back"

    return True, ""


def _reconnect(c: dict, node_id: int) -> None:
    try:
        pg_nodes.node_reconnect(c, node_id)
    except (panels.PanelError, ValueError) as e:
        log.warning("reconnect of node %s: %s", node_id, e)             # (the wait that follows says what the node does)


def _back_software(node, c: dict, node_id: int, before: dict) -> str:
    """Put the old image back (the one kept before the update) and see the node connected with its old version: the sentence."""
    try:
        out = run_action(node, "pgnode_rollback", {}, timeout=300)
    except (SSHError, ValueError) as e:
        return f"the old image could not be put back ({str(e)[:100]}): look at the node by hand"

    if "NO_PREV" in out or "NO_IMAGE" in out:
        return "no old image was kept on its server: look at the node by hand"

    if "ROLLED_BACK" not in out:
        return "the old image could not be put back (" + out.strip()[-100:] + "): look at the node by hand"

    _reconnect(c, node_id)
    ok, last = _wait_state(c, node_id, "version", before.get("version", ""), BACK_LIMIT)
    return f"the old version {before.get('version') or '?'} is back and connected" if ok else f"the old image is back but the node is {last.get('status') or 'not answering'} - look at it by hand"


def _back_core(node, c: dict, node_id: int, before: dict) -> str:
    """Put the old Xray back (the same command with the old version) and see the node connected with it: the sentence."""
    old = before.get("xray", "")

    if not old:
        return "the old Xray version is not known: look at the node by hand"

    try:
        out = run_action(node, "pgnode_core", {"VERSION": old}, timeout=RUN_TIMEOUT)
    except (SSHError, ValueError) as e:
        return f"the old Xray could not be put back ({str(e)[:100]}): look at the node by hand"

    if "CORE_UPDATED" not in out:
        return "the old Xray could not be put back (" + out.strip()[-100:] + "): look at the node by hand"

    ok, last = _wait_state(c, node_id, "xray", old, BACK_LIMIT)
    return f"the old Xray {old} is back and the node is connected" if ok else f"the old Xray is back but the node is {last.get('status') or 'not answering'} - look at it by hand"


def _update_node(aid: int, i: int, total: int, row: dict, node_tag: str, xray_tag: str) -> bool:
    """Update one node (its software, then its Xray). True = done (or nothing to do); False = it went wrong, the story is ended (done, not ok) after the way back was tried."""
    say = autopilot._say
    name = row["name"]

    def stop(text: str) -> bool:
        say(aid, f"Stopped at {i}/{total}: {name} - {text} The nodes after it were not touched.", done=True, ok=False)
        return False

    try:
        c = panels.cred_for(row["panel_id"])
        node, sname, _ip = extras._node(row["server"]["id"])
        before = _row(c, row["node_id"])
    except (panels.PanelError, ValueError) as e:
        return stop(f"it could not be read ({str(e)[:140]}).")

    say(aid, f"{i}/{total} {name} (on {sname}, {row.get('usage_text') or '—'} now): looking at its server (read-only)…")

    if before.get("status") != "connected":
        return stop(f"it is {before.get('status') or 'not connected'} now: connect it first.")

    try:
        info = parse_node_info(run_script(node, "pgnode_info", timeout=40))
    except (SSHError, ValueError) as e:
        return stop(f"its server did not answer ({str(e)[:120]}). Nothing was changed.")

    why = node_blockers(info)

    if why:
        return stop("; ".join(why) + ". Nothing was changed.")

    do_sw = pg_update.newer(node_tag, before.get("version")) is True
    do_core = pg_update.newer(xray_tag, before.get("xray")) is True

    if do_sw:
        say(aid, f"    software {before.get('version') or '?'} → {node_tag}: its old image is kept, then pg-node update (the node restarts: its users drop for 10-20 s)…")

        try:
            out = run_action(node, "pgnode_update", {}, timeout=RUN_TIMEOUT)
        except (SSHError, ValueError) as e:
            out = "ERROR " + str(e)[:120]

        if any(w in out for w in ("NO_CLI", "NO_CONTAINER", "TAG_FAILED")):
            return stop(f"the update could not start on its server ({out.strip()[-120:]}). Nothing was changed.")

        if "NODE_UPDATED" not in out:
            say(aid, f"    the update command did not finish well ({out.strip()[-140:]}); putting the old image back…")
            return stop(_back_software(node, c, row["node_id"], before) + ".")

        _reconnect(c, row["node_id"])
        ok, last = _wait_state(c, row["node_id"], "version", node_tag, pg_update.NODE_LIMIT)

        if ok:
            ok, why2 = _soak(c, row["node_id"], "version", last.get("version", ""))
        else:
            why2 = f"it is {last.get('status') or 'not answering'} and shows {last.get('version') or '?'} after {int(pg_update.NODE_LIMIT // 60)} minutes"

        if not ok:
            say(aid, f"    {why2}; putting the old image back…")
            return stop(_back_software(node, c, row["node_id"], before) + ".")

        say(aid, f"    {name} is connected again: software {last.get('version')}, watched for {int(SOAK)} s.")
        before = last

    if do_core:
        say(aid, f"    Xray {before.get('xray') or '?'} → {xray_tag}: pg-node core-update (Xray restarts: its users drop for 10-20 s)…")

        try:
            out = run_action(node, "pgnode_core", {"VERSION": xray_tag}, timeout=RUN_TIMEOUT)
        except (SSHError, ValueError) as e:
            out = "ERROR " + str(e)[:120]

        if "CORE_UPDATED" not in out:
            say(aid, f"    the Xray update did not finish well ({out.strip()[-140:]}); checking the node…")
            ok0, _last0 = _wait_state(c, row["node_id"], "xray", before.get("xray", ""), 60)
            return stop("the node is connected with its old Xray." if ok0 else _back_core(node, c, row["node_id"], before) + ".")

        ok, last = _wait_state(c, row["node_id"], "xray", xray_tag, pg_update.NODE_LIMIT)

        if ok:
            ok, why2 = _soak(c, row["node_id"], "xray", last.get("xray", ""))
        else:
            why2 = f"it is {last.get('status') or 'not answering'} and shows Xray {last.get('xray') or '?'} after {int(pg_update.NODE_LIMIT // 60)} minutes"

        if not ok:
            say(aid, f"    {why2}; putting the old Xray back…")
            return stop(_back_core(node, c, row["node_id"], before) + ".")

        say(aid, f"    {name} is connected again: Xray {last.get('xray')}, watched for {int(SOAK)} s.")

    if not do_sw and not do_core:
        say(aid, f"    {name} is the newest already - skipped.")

    return True


# ------------------------------------------------------------------ the run
def _safe_story(aid: int) -> None:
    say = autopilot._say
    pl = plan(force=True)

    if pl["problems"]:
        say(aid, "Stopped: " + pl["problems"][0], done=True, ok=False)
        return

    nodes = pl["nodes"]
    node_tag, xray_tag = (pl["latest"].get("node") or ""), (pl["latest"].get("xray") or "")
    todo_panels = [p for p in pl["panels"] if not p["skip"]]
    say(aid, f"Safe update: {len(nodes)} node{'s' if len(nodes) != 1 else ''}, the quietest first, one at a time" + (f", then the panel {todo_panels[0]['name']}" if todo_panels else "") + ". Each node is looked at before and after and its old version is kept; one that does not hold gets it back and the run stops.")

    if nodes:
        say(aid, "Order: " + ", ".join(f"{n['name']} ({n['usage_text']})" for n in nodes[:12]) + (", …" if len(nodes) > 12 else ""))

    for i, row in enumerate(nodes, 1):
        if not _update_node(aid, i, len(nodes), row, node_tag, xray_tag):
            with session() as db:
                emit(db, "warn", "panels", f"PasarGuard Safe update stopped at {row['name']} ({i} of {len(nodes)}).", "")

            return

    if nodes:
        with session() as db:
            emit(db, "ok", "panels", f"PasarGuard Safe update: {len(nodes)} node{'s' if len(nodes) != 1 else ''} updated.", "")

    for p in pl["panels"]:
        if p["skip"]:
            say(aid, f"The panel {p['name']} was NOT updated: {p['skip']}.")

    if todo_panels:
        p = todo_panels[0]
        say(aid, f"Nodes done. Now the panel {p['name']} ({short(p['version'])} → {short(p['latest'])}): a backup, then the official update; the nodes keep serving the users that are connected.")
        pg_update._panel_story(aid, p["id"], p["host"]["id"], True)         # (it ends the action with its own last line)
        return

    skipped = [p for p in pl["panels"] if p["skip"]]
    say(aid, f"Done: {len(nodes)} node{'s' if len(nodes) != 1 else ''} updated." + (" The panel was not updated (the lines above say why)." if skipped else ""), done=True, ok=not skipped)


def start(typed: str = "", resolver=None) -> int:
    """Start a Safe update as an action (returns its id; the story is read with /api/actions/{id}). Refused with the first problem the plan finds, when there is nothing to update, and - when the panel is part
    of it - unless the name of the panel is typed back."""
    pl = plan(resolver, force=True)

    if pl["problems"]:
        raise ValueError(pl["problems"][0])

    if pl["nothing"]:
        why = [s["why"] for s in pl["skipped"]] + [p["skip"] for p in pl["panels"] if p["skip"]]
        raise ValueError("there is nothing to update now" + (": " + why[0] if why else ": everything is up to date"))

    todo_panels = [p for p in pl["panels"] if not p["skip"]]

    if todo_panels and str(typed or "").strip() != todo_panels[0]["name"]:
        raise ValueError(f"type the name of the panel ({todo_panels[0]['name']}) to confirm")

    if LABEL in autopilot._BUSY or "pg node updates" in autopilot._BUSY or any(k.startswith("pgpanel ") for k in autopilot._BUSY):
        raise ValueError("a PasarGuard update is running right now: follow it in the history of the Tunnel Doctor")

    return autopilot._start(LABEL, _safe_story, action="pgupdate", source="manual")
