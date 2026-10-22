"""The Marzban node card of a server says "Node installed" when a node runs there (the owner, 2026-10-22: "when the node is installed it should say Node installed"). The read script of the server counts the
marzban-node containers that are up (MZNODE), the facts carry `mz_node`, a finished panel install is written down at once (`extra.mznode`), and the card prefers the server's own reading."""
import subprocess
import time
from pathlib import Path

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import autopilot, extras, facts
from nikatel.ssh import SCRIPTS, Node

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
BASE = "CPU=100 50 200 120\nNET=0 0 1250000 2500000\nMEM=4000000 1000000 2000000 1500000\nDISK=100000000 40000000\nLOAD=0.5 0.4 0.3 4\nUP=86400\nCONN=12\nHOST=fo1 5.15.0\nTOP=xray 37.5\nABUSE=0\n"


def test_the_facts_carry_the_number_of_marzban_node_containers_that_are_up():
    assert facts.parse_facts(BASE + "MZNODE=1\n")["mz_node"] == 1
    assert facts.parse_facts(BASE + "MZNODE=0\n")["mz_node"] == 0
    assert facts.parse_facts(BASE + "MZNODE=\n")["mz_node"] == 0                    # (no docker on the server: nothing is installed)
    assert facts.parse_facts(BASE)["mz_node"] is None                                  # (an older reading did not say: the card falls back to what the panel wrote down)


def test_the_read_script_counts_the_node_containers_read_only_and_is_valid_shell(tmp_path):
    src = SCRIPTS["facts"][0] if "facts" in SCRIPTS else next(v[0] for k, v in SCRIPTS.items() if "ABUSE=" in v[0])
    line = next(ln for ln in src.splitlines() if ln.startswith('echo "MZNODE='))
    assert "timeout 3 docker ps" in line and "marzban-node.* Up" in line and "command -v docker" in line
    assert "docker rm" not in src and "docker stop" not in src
    f = tmp_path / "s.sh"
    f.write_text(src, encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True, timeout=30, check=False).returncode == 0


def test_a_finished_panel_install_is_written_down_and_a_failed_one_is_not(monkeypatch):
    with session() as db:
        s = Server(name="mz-fo", host="198.51.100.231", role="foreign", online=True)
        db.add(s)
        db.flush()
        sid = s.id

    monkeypatch.setattr(extras, "_node", lambda i: (Node("198.51.100.231", "root", 22), "mz-fo", "198.51.100.231"))
    monkeypatch.setattr(extras, "_pending", lambda change: None)
    monkeypatch.setattr(autopilot, "_say", lambda *a, **k: None)
    monkeypatch.setattr(extras, "_run_item", lambda aid, node, ip, item, cert, resume=False: False)
    extras._work(1, sid, ["marzban_node"], "cert")

    with session() as db:
        assert "mznode" not in (db.get(Server, sid).extra or {})

    monkeypatch.setattr(extras, "_run_item", lambda aid, node, ip, item, cert, resume=False: True)
    before = int(time.time())
    extras._work(2, sid, ["marzban_node"], "cert")

    with session() as db:
        z = (db.get(Server, sid).extra or {}).get("mznode") or {}

    assert z.get("installed") is True and z.get("at", 0) >= before


def test_the_card_says_node_installed_and_the_servers_own_reading_wins():
    card = APP.split("function installHtml(s) {", 1)[1].split("return `<div class=\"ins3\">", 1)[0]
    assert "const mzf = (s.facts || {}).mz_node, mzOn = mzf != null ? mzf > 0 : !!((s.extra || {}).mznode || {}).installed;" in card
    body = APP.split("function installHtml(s) {", 1)[1].split("window.insCopy", 1)[0]
    assert '${mzOn ? \'<span class="pill ins-chip ok"><i></i>Installed</span>\' : ""}' in body
    assert '<button type="button" class="ins-btn ok" disabled title="A Marzban node is running on this server">✓ Node installed</button>' in body
    assert "btn(\"dl\", \"Install node\"" in body                                      # (a server without a node keeps its Install button)
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "html body #modal .ins-btn.ok" in css
