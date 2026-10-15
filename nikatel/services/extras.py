"""Things that can be installed on a server (right after it is added, or later from the Install view of its window): the torrent-tracker block, a Marzban node, a PasarGuard node and the update of the system packages.

Each is one reviewed action in ssh.py. They take minutes (thousands of iptables rules, Docker), so they run DETACHED on the server: the action only
starts them and the panel asks `install_status` how far they are, telling it in one story. Neither a slow install nor a restart of the panel can kill them:
the running installs are kept in the settings and picked up again when the panel starts (`resume_pending`)."""
from __future__ import annotations

import base64
import re
import threading
import time

from ..db import session
from ..log import get_logger
from ..models import ActionLog, Server
from ..ssh import Node, SSHError, run_action, run_script
from . import autopilot
from . import settings as st
from .events import emit

log = get_logger("extras")
ITEMS = ("btblock", "marzban_node", "pasarguard_node", "os_update")
LABEL = {"btblock": "torrent block", "marzban_node": "Marzban node", "pasarguard_node": "PasarGuard node", "os_update": "system update", "pasarguard_panel": "PasarGuard panel update"}      # (the last is not an install: services/pg_update.py runs it with _run_item, it is not in ITEMS)
KEY = {"btblock": "btblock", "marzban_node": "mznode", "pasarguard_node": "pgnode", "os_update": "osupdate", "pasarguard_panel": "pgpanel"}          # the name of the install on the server
ACTION = {"btblock": "install_btblock", "marzban_node": "install_marzban_node", "pasarguard_node": "install_pasarguard_node", "os_update": "install_os_update", "pasarguard_panel": "install_pgpanel_update"}
BUSY_TEXT = {"btblock": "Torrent block: resolving the trackers (about a minute)", "marzban_node": "Marzban node (minutes)", "pasarguard_node": "PasarGuard node (minutes)", "os_update": "System update (minutes)", "pasarguard_panel": "PasarGuard panel update (minutes)"}      # (the short line of the row of the server while the install runs)
LIMIT = {"btblock": 2700, "marzban_node": 1500, "pasarguard_node": 1200, "os_update": 2700, "pasarguard_panel": 2400}                   # seconds the panel waits for each
POLL = 10


def _node(server_id: int) -> tuple[Node, str, str]:
    with session() as db:
        s = db.get(Server, server_id)

        if s is None:
            raise ValueError("server not found")

        return Node(s.host, s.ssh_user, s.ssh_port), s.name, s.host


def check_cert(cert: str) -> str:
    """The certificate the Marzban panel shows for a node: whole, without blank lines."""
    c = "\n".join(line.rstrip() for line in str(cert).strip().splitlines() if line.strip())

    if "BEGIN CERTIFICATE" not in c or "END CERTIFICATE" not in c or len(c) > 6000:
        raise ValueError("paste the whole certificate of the Marzban panel (from -----BEGIN CERTIFICATE----- to -----END CERTIFICATE-----)")

    return c


def parse_status(out: str) -> tuple[str, str]:
    """Pure: 'STATE x' + text -> (x, the last line of text)."""
    lines = [ln.strip() for ln in str(out).splitlines() if ln.strip()]
    state = next((ln.split()[1] for ln in lines if ln.startswith("STATE ") and len(ln.split()) >= 2), "none")
    rest = [ln for ln in lines if not ln.startswith("STATE ")]
    return state, (rest[-1][:160] if rest else "")


def _start_action(node: Node, item: str, cert: str) -> str:
    """Start the detached install; the first word of the answer: STARTED, ALREADY_RUNNING, MZNODE_EXISTS, BAD_CERT, NOT_ROOT or an error."""
    key, *ports = str(cert).split(":")          # (a PasarGuard node: the API key, and - when 62050 / 62051 are taken - ":port:api_port" after it)
    params = {"B64": base64.b64encode(cert.encode()).decode()} if item == "marzban_node" else {"KEY": key, "PORT": ports[0] if ports else "62050", "APIPORT": ports[1] if len(ports) > 1 else "62051"} if item == "pasarguard_node" else {"BACKUP": cert} if item == "pasarguard_panel" else {}      # for a PasarGuard node `cert` carries the API key of the panel, for the update of a PasarGuard panel 1 or 0 (a backup first)
    return run_action(node, ACTION[item], params, timeout=60).strip()


def _poll(aid: int, node: Node, item: str) -> tuple[str, str]:
    """Wait for the detached install: ('ok' | 'failed' | 'timeout', its last lines). A few SSH hiccups are tolerated."""
    end, last_say, none_n, hiccups, key = time.time() + LIMIT[item], 0.0, 0, 0, KEY[item]

    while time.time() < end:
        time.sleep(POLL)

        try:
            state, line = parse_status(run_script(node, "install_status", {"NAME": key}, timeout=30))
            hiccups = 0
        except (SSHError, ValueError) as e:
            hiccups += 1

            if hiccups >= 12:
                return "failed", f"the server stopped answering: {str(e)[:100]}"

            continue

        if state in ("ok", "failed"):
            return state, line

        none_n = none_n + 1 if state == "none" else 0

        if none_n >= 3:
            return "failed", "the install is not running any more and left no result"

        if time.time() - last_say >= 60:
            last_say = time.time()
            autopilot._say(aid, f"{LABEL[item]}: still working…" + (f" ({line})" if line else ""))

    return "timeout", "it did not finish in time; it keeps running on the server - look at /var/lib/nikatel-install/"


def _result(aid: int, item: str, state: str, text: str, ip: str) -> bool:
    if state == "ok":
        if item == "marzban_node":
            autopilot._say(aid, f"Marzban node: running. In the Marzban panel add a node: address {ip}, port 62050, API port 62051.")
        elif item == "pasarguard_node":
            autopilot._say(aid, f"PasarGuard node: installed. Next: in this server's Install view press Show certificate, copy it into the PasarGuard panel (Nodes, address {ip}) and set the node there.")
        elif item == "os_update":
            autopilot._say(aid, "System update: " + (text.replace("OSUPDATE_OK ", "") or "done") + ".")
        elif item == "pasarguard_panel":
            autopilot._say(aid, "PasarGuard panel: " + (text.replace("PGPANEL_OK ", "") or "the update command finished") + ".")
        else:
            autopilot._say(aid, "Torrent block: installed (" + (text.replace("BT_OK ", "").replace("_", " ") or "done") + "); cron refreshes the rules every day.")

        return True

    autopilot._say(aid, f"{LABEL[item]}: failed - {text or state}")
    return False


def _run_item(aid: int, node: Node, ip: str, item: str, cert: str, resume: bool = False) -> bool:
    """One install: start it (unless it already runs / ran) and follow it to the end."""
    key = KEY[item]

    if resume:                                                    # the panel was away: where is it?
        try:
            state, line = parse_status(run_script(node, "install_status", {"NAME": key}, timeout=30))
        except (SSHError, ValueError):
            state, line = "none", ""

        if state in ("ok", "failed"):
            return _result(aid, item, state, line, ip)

        if state == "running":
            autopilot._say(aid, f"{LABEL[item]}: the panel was restarted; the install kept running, following it again…")
            return _result(aid, item, *_poll(aid, node, item), ip)

    autopilot._say(aid, f"{LABEL[item]}: starting" + {"marzban_node": " (Docker, the node image and its service; a few minutes)…", "pasarguard_node": " (the official installer, Docker and the node; a few minutes)…", "os_update": " (apt update and upgrade; the server is not rebooted; a few minutes)…", "pasarguard_panel": " (the pasarguard command on the server: backup, then update; a few minutes)…"}.get(item, " (the tracker list, each name resolved once, one ipset; about a minute)…"))

    try:
        out = _start_action(node, item, cert)
    except (SSHError, ValueError) as e:
        autopilot._say(aid, f"{LABEL[item]}: could not be started: {str(e)[:200]}")
        return False

    word = out.split()[0] if out else ""

    if word == "MZNODE_EXISTS":
        autopilot._say(aid, "Marzban node: already installed on this server (/opt/marzban-node), nothing changed.")
        return True

    if word == "PGNODE_EXISTS":
        autopilot._say(aid, "PasarGuard node: already installed on this server (the pg-node command exists), nothing changed.")
        return True

    if word not in ("STARTED", "ALREADY_RUNNING"):
        why = {"BAD_CERT": "the certificate is not valid", "NOT_ROOT": "the SSH user is not root", "NO_APT": "this server does not use apt (Debian / Ubuntu)", "NO_CLI": "the pasarguard command is not on this server"}.get(word, out[-160:] or "no answer")
        autopilot._say(aid, f"{LABEL[item]}: not started - {why}")
        return False

    return _result(aid, item, *_poll(aid, node, item), ip)


# ------------------------------------------------------------------ the story, and picking it up again after a restart
def _pending(change) -> None:
    items = list(st.get("pending_installs").get("items", []))
    st.put("pending_installs", {"items": change(items)})


def _work(aid: int, server_id: int, items: list[str], cert: str, resume: bool = False) -> None:
    from . import nodes

    node, name, ip = _node(server_id)
    results = {}

    try:
        for item in items:
            nodes.set_busy(server_id, "installing", BUSY_TEXT.get(item, LABEL[item]), unless="node")
            results[item] = _run_item(aid, node, ip, item, cert, resume)
            resume = False
    except Exception as e:
        log.exception("install on %s crashed", name)
        autopilot._say(aid, f"Stopped: {str(e)[:200]}", done=True, ok=False)
        results = {}
    finally:
        nodes.set_busy(server_id, "", unless="node")
        _pending(lambda rows: [r for r in rows if r["aid"] != aid])

    if not results:
        return

    ok = all(results.values())

    with session() as db:
        emit(db, "ok" if ok else "warn", "servers", f"Installed on {name}: " + ", ".join(f"{LABEL[k]} {'✓' if v else '✕'}" for k, v in results.items()), name)

    autopilot._say(aid, "Done." if ok else "Done with problems: look at the lines above.", done=True, ok=ok)


def start(server_id: int, items: list[str], cert: str = "") -> int:
    items = [i for i in ITEMS if i in items]

    if not items:
        raise ValueError("choose something to install")

    _node(server_id)
    if "pasarguard_node" in items:
        if len(items) != 1:
            raise ValueError("install the PasarGuard node on its own")

        cert = str(cert).strip()

        if not re.match(r"^[A-Za-z0-9._:+/=-]{8,200}$", cert):
            raise ValueError("paste the API key that the PasarGuard panel gives for the node")
    else:
        cert = check_cert(cert) if "marzban_node" in items else ""

    def work(aid: int) -> None:
        autopilot._say(aid, "Installing: " + ", ".join(LABEL[i] for i in items) + " (they keep running on the server even if the panel restarts)")
        _pending(lambda rows: rows + [{"aid": aid, "server_id": server_id, "items": items, "cert": "" if "pasarguard_node" in items else cert}])      # an API key is never kept
        _work(aid, server_id, items, cert)

    return autopilot._start(f"install {_node(server_id)[1]}", work, action="install")


def resume_pending() -> None:
    """Called when the panel starts: the installs that were being followed are followed again, in their own story."""
    for row in st.get("pending_installs").get("items", []):
        with session() as db:
            e = db.get(ActionLog, row["aid"])
            alive = e is not None and not e.finished_at

        if not alive:
            _pending(lambda rows, a=row["aid"]: [r for r in rows if r["aid"] != a])
            continue

        autopilot._say(row["aid"], "The panel started again: following the install…")
        threading.Thread(target=_work, args=(row["aid"], row["server_id"], row["items"], row.get("cert", ""), True), name=f"install-resume-{row['aid']}", daemon=True).start()


def ports_busy(server_id: int) -> dict[int, str]:
    """Who listens on the ports 62000-62300 of this server (where a PasarGuard node lives): {62050: "python (pid 1004)"}; empty = all free (read-only over SSH)."""
    node, _name, _ip = _node(server_id)
    out: dict[int, str] = {}

    for ln in run_script(node, "ports_busy", timeout=30).splitlines():
        m = re.match(r"^(\d+)\s+(.*)$", ln.strip())

        if m:
            who = re.search(r'\(\("([^"]+)",pid=(\d+)', m.group(2))
            out[int(m.group(1))] = f"{who.group(1)} (pid {who.group(2)})" if who else "another program"

    return out

def pgnode_running(server_id: int) -> bool:
    """Is a PasarGuard node container running on this server (read-only over SSH)? A question that could not be asked says True: only a clear "no container" says False."""
    try:
        node, _name, _ip = _node(server_id)
        out = run_script(node, "pgnode_running", timeout=30).strip()
    except Exception:  # noqa: BLE001 - the plan must not fail because of this one question
        return True

    return bool(out)          # (no output = no Docker, or no such container: the node is not running)

def pasarguard_cert(server_id: int) -> dict:
    """The public certificate the PasarGuard node made, to be set in the PasarGuard panel (read-only on the server)."""
    node, _name, _ip = _node(server_id)
    out = run_script(node, "pgnode_cert", timeout=30).strip()

    if "BEGIN CERTIFICATE" not in out:
        raise ValueError("no certificate found: install the PasarGuard node first (or it has not made one yet)")

    return {"cert": out[:6000]}
