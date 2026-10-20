"""A restart asked from the panel (the owner, 2026-10-20: "I rebooted the server - the row must show a reboot mark"): the row says "Rebooting" (the blue busy mark, not a red "offline" row that jumps to the top
of the list) until the server answers again after a restart; the stop of its SSH is no alarm while it restarts."""
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Event, Server
from nikatel.services import facts, nodes


def _facts(uptime):
    return {"ts": int(time.time()), "cpu": 5.0, "ram": 20.0, "disk": 30.0, "load1": 0.1, "rx_mbps": 1.0, "tx_mbps": 2.0, "conn": 10, "uptime": uptime}


@pytest.fixture
def srv(client, monkeypatch):
    with session() as db:
        db.add(Server(name="s1", host="203.0.113.5", role="foreign", online=True, extra={}))
        sid = db.scalar(select(Server.id))

    monkeypatch.setattr(nodes, "run_action", lambda *a, **k: "")
    return sid


def _busy(sid):
    with session() as db:
        return ((db.get(Server, sid).extra or {}).get("busy")) or {}


def _ask(monkeypatch, answer):
    def run(node, script, *a, **k):
        if isinstance(answer, Exception):
            raise answer

        return ""

    monkeypatch.setattr(facts, "run_script", run)
    monkeypatch.setattr(facts, "parse_facts", lambda out: answer)


def test_a_reboot_asked_from_the_panel_puts_the_mark_on_the_row(srv):
    nodes.reboot(srv)
    b = _busy(srv)
    assert b["what"] == nodes.REBOOTING == "Rebooting" and b["detail"] == "waiting for it to come back" and abs(b["at"] - time.time()) < 5


def test_the_stop_of_the_ssh_while_it_restarts_is_no_alarm_and_the_mark_stays(srv, monkeypatch):
    nodes.reboot(srv)
    _ask(monkeypatch, facts.SSHError("ssh connection lost"))
    facts.sample_server(srv)
    facts.sample_server(srv)

    with session() as db:
        assert db.get(Server, srv).online is False
        assert not [e for e in db.scalars(select(Event)) if e.level == "bad"]                 # (no "does not answer" alarm for a restart that was asked for)

    assert _busy(srv).get("what") == "Rebooting"                                              # the row stays "Rebooting" while it is down


def test_a_server_that_does_not_restart_for_real_still_gives_the_alarm(srv, monkeypatch):
    _ask(monkeypatch, facts.SSHError("ssh connection lost"))
    facts.sample_server(srv)

    with session() as db:
        assert [e for e in db.scalars(select(Event)) if e.level == "bad"]


def test_the_mark_goes_when_the_server_answers_with_an_uptime_shorter_than_the_wait(srv, monkeypatch):
    nodes.reboot(srv)
    _ask(monkeypatch, _facts(uptime=12 * 86400))                                              # it answers in the first seconds, before it goes down: it has not restarted yet
    facts.sample_server(srv)
    assert _busy(srv).get("what") == "Rebooting"
    _ask(monkeypatch, _facts(uptime=20))                                                      # started again: its uptime is 20 s, the restart was asked for longer ago than that
    with session() as db:
        s = db.get(Server, srv)
        ex = dict(s.extra)
        ex["busy"] = {**ex["busy"], "at": int(time.time()) - 90}
        s.extra = ex

    facts.sample_server(srv)
    assert _busy(srv) == {}
    with session() as db:
        assert db.get(Server, srv).online is True


def test_a_restart_that_did_not_happen_loses_its_mark_after_the_wait(srv, monkeypatch):
    nodes.reboot(srv)
    with session() as db:
        s = db.get(Server, srv)
        ex = dict(s.extra)
        ex["busy"] = {**ex["busy"], "at": int(time.time()) - facts.REBOOT_WAIT - 5}
        s.extra = ex

    _ask(monkeypatch, _facts(uptime=30 * 86400))                                              # it answers with the old uptime, long after: the command did nothing
    facts.sample_server(srv)
    assert _busy(srv) == {}


def test_another_busy_mark_is_left_alone(srv, monkeypatch):
    nodes.set_busy(srv, "installing", "Marzban node (minutes)")
    _ask(monkeypatch, _facts(uptime=20))
    facts.sample_server(srv)
    assert _busy(srv).get("what") == "installing"

def test_the_bandwidth_of_a_pasarguard_server_is_the_line_of_the_server_not_what_its_users_use():
    """The owner, 2026-10-20: "show the server's own bandwidth in the Bandwidth column, not the bandwidth the users use" (and later: its line, not the traffic). The rows of the PasarGuard profile
    showed the up and the down of the users of the node (what the panel counts); they show the line of the server they run on, as every other row."""
    lab = (Path(__file__).resolve().parents[1] / "nikatel" / "web" / "lab.js").read_text(encoding="utf-8")
    node = lab.split("function labNodeRowHtml(p) {", 1)[1].split("const labGoneRow", 1)[0]
    assert "labCapCell(srv, !!(srv && srv.enabled))" in node and "live.rx" not in node.split("labCapCell(srv", 1)[1].split("</tr>", 1)[0]
    ui2 = (Path(__file__).resolve().parents[1] / "nikatel" / "web" / "i18n.fa.ui2.js").read_text(encoding="utf-8")
    assert '"Rebooting":' in ui2 and '"waiting for it to come back":' in ui2

def test_a_server_that_is_being_rebooted_keeps_the_place_of_its_problems_in_the_list():
    """The owner, 2026-10-20: "a server had a problem, I rebooted it, and it went to the middle of the list": the busy mark ranked it with the healthy ones. A busy server that still has a problem of its own
    (Filtered, Bad route, a tunnel down ...) stays with the problems at the top; one whose only "problem" is that it does not answer while it restarts does not jump there."""
    import json
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")

    lab = (Path(__file__).resolve().parents[1] / "nikatel" / "web" / "lab.js").read_text(encoding="utf-8")
    src = "\n".join(re.search(rf"^{re.escape(k)}.*$", lab, re.MULTILINE).group(0) for k in ("const labBusyOk", "const labRank"))
    code = (
        "const TONE = {1: 'busy', 2: 'busy', 3: 'busy', 4: 'ok', 5: 'bad'}, ISS = {1: ['Filtered', 'Bad route'], 2: ['Offline', 'SSH ✕'], 3: ['Offline', 'Filtered'], 4: [], 5: ['Offline']};\n"
        "const labTone = p => p.kind === 'node' ? 'busy' : TONE[p.id], labServer = p => ({id: p.id}), labIssues = s => ISS[s.id].map(txt => ({txt}));\n"
        + src
        + "\nprocess.stdout.write(JSON.stringify([1, 2, 3, 4, 5].map(id => labRank({id})).concat([labRank({id: 1, kind: 'node'})])));"
    )
    out = subprocess.run([node, "-e", code], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [1, 2, 1, 3, 0, 2]      # (busy + Filtered / Bad route: with the problems; busy + only "does not answer": as a busy one; a busy node: as before; red stays first)