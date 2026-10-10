"""Things that can be installed on a server right after it is added (Nodes → Add server): the torrent-tracker block and a Marzban node.
Each is one reviewed action in ssh.py; this module only runs them one after the other inside one story and says what happened."""
from __future__ import annotations

import base64

from ..db import session
from ..log import get_logger
from ..models import Server
from ..ssh import Node, SSHError, run_action
from . import autopilot
from .events import emit

log = get_logger("extras")
ITEMS = ("btblock", "marzban_node")
LABEL = {"btblock": "torrent block", "marzban_node": "Marzban node"}


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


def _install_bt(aid: int, node: Node) -> bool:
    autopilot._say(aid, "Torrent block: downloading the tracker list and setting the rules…")

    try:
        out = run_action(node, "install_btblock", {}, timeout=240)
    except (SSHError, ValueError) as e:
        autopilot._say(aid, f"Torrent block: failed: {str(e)[:200]}")
        return False

    if "BT_OK" in out:
        autopilot._say(aid, "Torrent block: installed (" + out.strip().splitlines()[-1].replace("BT_OK ", "").replace("_", " ") + "); cron refreshes the rules every day.")
        return True

    autopilot._say(aid, "Torrent block: the script did not finish: " + out.strip()[-160:])
    return False


def _install_node(aid: int, node: Node, ip: str, cert: str) -> bool:
    autopilot._say(aid, "Marzban node: installing (Docker, the node image and its service; this can take a few minutes)…")

    try:
        out = run_action(node, "install_marzban_node", {"B64": base64.b64encode(cert.encode()).decode()}, timeout=420)
    except (SSHError, ValueError) as e:
        autopilot._say(aid, f"Marzban node: failed: {str(e)[:300]}")
        return False

    if "MZNODE_EXISTS" in out:
        autopilot._say(aid, "Marzban node: already installed on this server (/opt/marzban-node), nothing changed.")
        return True

    if "MZNODE_OK" in out:
        autopilot._say(aid, f"Marzban node: running. In the Marzban panel add a node: address {ip}, port 62050, API port 62051.")
        return True

    reason = {"BAD_CERT": "the certificate is not valid", "NOT_ROOT": "the SSH user is not root", "DOWNLOAD_FAILED": "the installer could not be downloaded"}
    why = next((v for k, v in reason.items() if k in out), "the node did not start: " + out.strip()[-240:])
    autopilot._say(aid, f"Marzban node: failed - {why}")
    return False


def start(server_id: int, items: list[str], cert: str = "") -> int:
    items = [i for i in ITEMS if i in items]

    if not items:
        raise ValueError("choose something to install")

    node, name, ip = _node(server_id)
    cert = check_cert(cert) if "marzban_node" in items else ""

    def work(aid: int) -> None:
        autopilot._say(aid, f"Installing on {name}: " + ", ".join(LABEL[i] for i in items))
        results = {}

        if "btblock" in items:
            results["btblock"] = _install_bt(aid, node)

        if "marzban_node" in items:
            results["marzban_node"] = _install_node(aid, node, ip, cert)

        ok = all(results.values())

        with session() as db:
            emit(db, "ok" if ok else "warn", "servers", f"Installed on {name}: " + ", ".join(f"{LABEL[k]} {'✓' if v else '✕'}" for k, v in results.items()), name)

        autopilot._say(aid, "Done." if ok else "Done with problems: look at the lines above.", done=True, ok=ok)

    return autopilot._start(f"install {name}", work, action="install")
