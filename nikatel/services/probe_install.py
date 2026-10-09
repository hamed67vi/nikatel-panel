"""Install the probe (probe/ in this repository) on an Iran server, with the Xray binary from the
panel's engine cache, and register it as the primary (or a standby) probe."""
from __future__ import annotations

import base64
import json
import secrets
import subprocess
from pathlib import Path

from ..config import get_settings
from ..db import session
from ..log import get_logger
from ..models import Server
from ..ssh import Node, run_action
from . import settings as st
from .events import emit

log = get_logger("probe-install")
SRC = Path(__file__).resolve().parent.parent.parent / "probe"
UNITS = {
    "nikatel-speed-api.service": "[Unit]\nDescription=Nikatel Speed Test API\nAfter=network-online.target\n\n[Service]\nExecStart=/usr/bin/python3 /opt/nikatel-probe-manager/speed-api.py\nRestart=always\nRestartSec=3\nUser=root\n\n[Install]\nWantedBy=multi-user.target\n",
    "nikatel-probe-manager.service": "[Unit]\nDescription=Nikatel Multi Config Probe Manager\nAfter=network-online.target\n\n[Service]\nExecStart=/usr/bin/python3 /opt/nikatel-probe-manager/run-all.py\nRestart=always\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n",
    "nikatel-probe-monitor.service": "[Unit]\nDescription=Nikatel Multi Config Health Monitor\nAfter=network-online.target nikatel-probe-manager.service\n\n[Service]\nExecStart=/usr/bin/python3 /opt/nikatel-probe-manager/monitor-all.py\nRestart=always\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n",
    "nikatel-daily-speed.service": "[Unit]\nDescription=Nikatel daily speed run\n\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /opt/nikatel-probe-manager/daily-speed.py\n",
    "nikatel-daily-speed.timer": "[Unit]\nDescription=Nikatel daily speed run\n\n[Timer]\nOnCalendar=*-*-* 04:00:00\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n",
}


def _put(node: Node, path: str, data: bytes, mode: str) -> None:
    run_action(node, "put_file", {"PATH": path, "B64": base64.b64encode(data).decode(), "MODE": mode})


def install(server_id: int, as_standby: bool = False) -> dict:
    s = get_settings()

    if s.read_only:
        raise ValueError("the panel is read-only; set NIKATEL_READ_ONLY=false on the panel server to install a probe")

    eng = s.data_dir.parent / "engines"
    xray = eng / "xray"

    if not xray.exists() or xray.stat().st_size < 5_000_000:
        raise RuntimeError("the panel has no Xray binary cached; run scripts/fetch-xray.sh on the panel server")

    files = sorted(SRC.glob("*.py"))

    if not files:
        raise RuntimeError("the probe sources are missing from the panel's checkout (probe/*.py)")

    with session() as db:
        srv = db.get(Server, server_id)

        if srv is None or srv.role != "iran":
            raise ValueError("choose an Iran server")

        node, name, host = Node(srv.host, srv.ssh_user, srv.ssh_port), srv.name, srv.host

    run_action(node, "probe_prepare", timeout=180)

    for f in files:
        _put(node, f"/opt/nikatel-probe-manager/{f.name}", f.read_bytes(), "0644")

    token = secrets.token_hex(32)
    _put(node, "/opt/nikatel-probe-manager/speed-api.token", token.encode(), "0600")
    _put(node, "/opt/nikatel-probe-manager/configs.json", json.dumps({"settings": {"check_interval": 30, "timeout": 20, "start_port": 11000, "test_url": "https://www.gstatic.com/generate_204"}, "configs": []}, indent=1).encode(), "0600")

    for unit, text in UNITS.items():
        _put(node, f"/etc/systemd/system/{unit}", text.encode(), "0644")

    opts = ["-i", str(s.ssh_key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={s.known_hosts}", "-o", "ConnectTimeout=15"]

    for fname in ("xray", "geoip.dat", "geosite.dat"):
        p = eng / fname

        if not p.exists():
            continue

        r = subprocess.run(["scp", *opts, "-P", str(node.port), str(p), f"{node.user}@{node.host}:/opt/nikatel-probe/xray/{fname}"], capture_output=True, text=True, timeout=600, check=False)

        if r.returncode != 0:
            raise RuntimeError(f"could not copy {fname} to the server: " + r.stderr.strip()[:120])

    out = run_action(node, "probe_start", timeout=120)
    states = {ln.split()[1]: ln.split()[2] for ln in out.splitlines() if ln.startswith("UNIT ") and len(ln.split()) >= 3}

    if states.get("nikatel-speed-api") != "active":
        raise RuntimeError("the probe API did not start: " + str(states))

    api_url = f"http://{host}:18080"
    cur = st.get("probe")

    if as_standby or cur.get("api_url"):
        standby = [x for x in (cur.get("standby") or []) if x.get("api_url") != api_url] + [{"name": name, "api_url": api_url, "api_token": token}]
        st.put("probe", {"standby": standby})
        role = "standby"
    else:
        st.put("probe", {"api_url": api_url, "api_token": token, "server_id": server_id})
        role = "primary"

    with session() as db:
        emit(db, "ok", "probe", f"Probe installed on {name} and registered as {role}.", name)

    log.info("probe installed on %s as %s", name, role)
    return {"server": name, "role": role, "units": states}
