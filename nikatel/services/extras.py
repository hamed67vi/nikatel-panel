"""Things that can be installed on a server right after it is added (Nodes → Add server): the torrent-tracker block and a Marzban node.

Each is one reviewed action in ssh.py. They take minutes (thousands of iptables rules, Docker), so they run DETACHED on the server: the action only
starts them and the panel asks `install_status` how far they are, telling it in one story. Neither a slow install nor a restart of the panel can kill them:
the running installs are kept in the settings and picked up again when the panel starts (`resume_pending`)."""
from __future__ import annotations

import base64
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
ITEMS = ("btblock", "marzban_node")
LABEL = {"btblock": "torrent block", "marzban_node": "Marzban node"}
KEY = {"btblock": "btblock", "marzban_node": "mznode"}          # the name of the install on the server
LIMIT = {"btblock": 2700, "marzban_node": 1500}                   # seconds the panel waits for each
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
    params = {"B64": base64.b64encode(cert.encode()).decode()} if item == "marzban_node" else {}
    return run_action(node, "install_" + ("btblock" if item == "btblock" else "marzban_node"), params, timeout=60).strip()


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

    autopilot._say(aid, f"{LABEL[item]}: starting" + (" (Docker, the node image and its service; a few minutes)…" if item == "marzban_node" else " (a tracker list and thousands of rules; a few minutes)…"))

    try:
        out = _start_action(node, item, cert)
    except (SSHError, ValueError) as e:
        autopilot._say(aid, f"{LABEL[item]}: could not be started: {str(e)[:200]}")
        return False

    word = out.split()[0] if out else ""

    if word == "MZNODE_EXISTS":
        autopilot._say(aid, "Marzban node: already installed on this server (/opt/marzban-node), nothing changed.")
        return True

    if word not in ("STARTED", "ALREADY_RUNNING"):
        why = {"BAD_CERT": "the certificate is not valid", "NOT_ROOT": "the SSH user is not root"}.get(word, out[-160:] or "no answer")
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
            nodes.set_busy(server_id, "installing", LABEL[item])
            results[item] = _run_item(aid, node, ip, item, cert, resume)
            resume = False
    except Exception as e:
        log.exception("install on %s crashed", name)
        autopilot._say(aid, f"Stopped: {str(e)[:200]}", done=True, ok=False)
        results = {}
    finally:
        nodes.set_busy(server_id, "")
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
    cert = check_cert(cert) if "marzban_node" in items else ""

    def work(aid: int) -> None:
        autopilot._say(aid, "Installing: " + ", ".join(LABEL[i] for i in items) + " (they keep running on the server even if the panel restarts)")
        _pending(lambda rows: rows + [{"aid": aid, "server_id": server_id, "items": items, "cert": cert}])
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
