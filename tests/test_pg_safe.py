"""Safe update of PasarGuard - one button for the nodes and the panel (the owner, 2026-10-31, with a picture of the PasarGuard card: "ONE button for the panel and the nodes, called Safe update - update everything so
that customers are not cut, start from the node with the LEAST usage").

Proved here: the three scripts that run on a node (the real text, run by bash against a fake `docker` / `pg-node`: the image that runs is kept, a stale note is never used to go back, the old image comes back, the
Xray command takes a version), the plan (the quietest node first, what is skipped and why, the panel only when no node would stop its core while it is away), the story with a fake panel and fake servers (in order,
a node that does not come back or does not stay connected gets its old version back and the run stops, a server that is not ready is not touched, a failed Xray leaves the node as it was, the panel last), the start's
refusals, the routes and the button. NOT proved: a real PasarGuard node, a real `pg-node`, a real panel, real docker (nothing was updated anywhere)."""
import os
import shutil
import stat
import subprocess
import types
from pathlib import Path

import pytest

from nikatel import ssh
from nikatel.db import session
from nikatel.models import ActionLog
from nikatel.services import autopilot, extras, panels, pg_nodes, pg_safe, pg_update
from nikatel.ssh import Node

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"


# ------------------------------------------------------------------ pure
def test_the_look_at_a_node_server_is_read_into_a_dict():
    d = pg_safe.parse_node_info("CLI yes\nDOCKER yes\nRUNNING 1\nREF pasarguard/node:latest\nID sha256:abc\nDIR /opt/pg-node\nDF 812345\nnoise here\n")
    assert d == {"cli": "yes", "docker": "yes", "running": "1", "ref": "pasarguard/node:latest", "id": "sha256:abc", "dir": "/opt/pg-node", "df": "812345"}
    assert pg_safe.parse_node_info("") == {}


def test_what_stands_in_the_way_of_a_node_is_said():
    ok = {"cli": "yes", "docker": "yes", "running": "1", "df": "900000"}
    assert pg_safe.node_blockers(ok) == [] and pg_safe.node_blockers({**ok, "df": ""}) == []                       # (free space that is not known does not block)
    assert "pg-node command" in pg_safe.node_blockers({**ok, "cli": "no"})[0]
    assert pg_safe.node_blockers({**ok, "docker": "no"}) == ["docker is not on its server"]
    assert pg_safe.node_blockers({**ok, "running": "0"}) == ["the node container is not running on its server"]
    assert "only 97 MB" in pg_safe.node_blockers({**ok, "df": "100000"})[0]


def test_the_quietest_node_is_first_and_one_that_reports_nothing_is_after_those_that_do():
    rows = [{"panel_id": 1, "node_id": i, "name": n} for i, n in ((1, "busy"), (2, "quiet"), (3, "mid"), (4, "silent-b"), (5, "silent-a"))]
    usage = {(1, 1): 9e6, (1, 2): 1e5, (1, 3): 2e6}
    assert [r["name"] for r in pg_safe.rank_nodes(rows, usage)] == ["quiet", "mid", "busy", "silent-a", "silent-b"]


def test_a_node_that_stops_its_core_when_the_panel_is_away_is_named():
    nodes = [{"name": "never", "keep_alive": 0}, {"name": "short", "keep_alive": 60}, {"name": "long", "keep_alive": 3600}, {"name": "edge", "keep_alive": 899}, {"name": "ok", "keep_alive": 900}, {"name": "x", "keep_alive": None}]
    assert pg_safe.keep_alive_blockers(nodes) == [{"name": "short", "keep_alive": 60}, {"name": "edge", "keep_alive": 899}]


def test_versions_with_and_without_the_v_are_the_same_version_and_the_speed_reads_in_mbit():
    assert pg_safe.short("v0.5.6") == pg_safe.short("0.5.6") == "0.5.6" and pg_safe.short(None) == ""
    assert pg_safe.mbit(None) == "—" and pg_safe.mbit(125000) == "1.0 Mbit/s"


def test_the_usage_is_the_mean_of_a_few_looks(monkeypatch):
    looks = iter([{"1": {"rx": 100, "tx": 100}, "2": None}, {"1": {"rx": 300, "tx": 100}, "2": {"rx": 5, "tx": 5}}, {"1": {"rx": 200, "tx": 200}, "x": {"rx": 1, "tx": 1}}])
    monkeypatch.setattr(pg_nodes, "_live_all", lambda c, w: next(looks))
    monkeypatch.setattr(pg_safe.time, "sleep", lambda s: None)
    assert pg_safe.sample_usage({}, rounds=3, gap=0) == {1: pytest.approx(333.3333, rel=1e-3), 2: 10.0}


# ------------------------------------------------------------------ the scripts, run by bash against a fake docker and a fake pg-node
@pytest.fixture
def bash_ok():
    if shutil.which("bash") is None:
        pytest.skip("no bash")


def _fakes(tmp_path, pgn_rc=0, running=True, label_dir=None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.txt"
    log.write_text("")
    label = str(label_dir or tmp_path)
    up = "1" if running else ""
    (bindir / "docker").write_text(
        "#!/bin/bash\n"
        f'echo "docker $*" >> {log}\n'
        'case "$1" in\n'
        f'  ps) [ -n "{up}" ] || exit 0; case "$*" in *"{{{{.Image}}}}"*) echo "node pasarguard/node:latest";; *) echo node;; esac;;\n'
        f'  inspect) [ -n "{up}" ] || exit 1; case "$3" in\n'
        "    '{{.Image}}') echo sha256:OLDIMAGE;;\n"
        "    '{{.Config.Image}}') echo pasarguard/node:latest;;\n"
        f"    *Labels*) echo '{label}';;\n"
        "  esac;;\n"
        "  image) exit 0;;\n"
        "  compose) exit 0;;\n"
        "esac\nexit 0\n")
    (bindir / "pg-node").write_text(f'#!/bin/bash\necho "pg-node $*" >> {log}\nexit {pgn_rc}\n')

    for f in ("docker", "pg-node"):
        (bindir / f).chmod((bindir / f).stat().st_mode | stat.S_IEXEC)

    return bindir, log


def _bash(text, bindir, tmp_path):
    text = text.replace("/var/lib/pg-node", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(exist_ok=True)
    return subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=30, check=False, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}).stdout


def test_the_update_keeps_the_image_that_runs_and_then_runs_the_official_command(tmp_path, bash_ok):
    bindir, log = _fakes(tmp_path)
    out = _bash(ssh.render_action("pgnode_update", {}), bindir, tmp_path)
    calls = log.read_text().splitlines()
    assert "PREV kept" in out and "NODE_UPDATED" in out and "NODE_UPDATE_FAILED" not in out
    assert "docker tag sha256:OLDIMAGE nikatel/pg-node-prev:keep" in calls and "pg-node update -y" in calls
    assert calls.index("docker tag sha256:OLDIMAGE nikatel/pg-node-prev:keep") < calls.index("pg-node update -y")      # (kept BEFORE the update)
    note = (tmp_path / "state" / "nikatel-prev.txt").read_text()
    assert "REF=pasarguard/node:latest" in note and "ID=sha256:OLDIMAGE" in note and "NAME=node" in note


def test_a_failed_official_command_is_reported_and_the_image_is_still_kept(tmp_path, bash_ok):
    bindir, _ = _fakes(tmp_path, pgn_rc=1)
    out = _bash(ssh.render_action("pgnode_update", {}), bindir, tmp_path)
    assert "NODE_UPDATE_FAILED 1" in out and "NODE_UPDATED" not in out and (tmp_path / "state" / "nikatel-prev.txt").exists()


def test_a_server_without_the_node_container_is_not_touched_and_a_stale_note_is_removed(tmp_path, bash_ok):
    bindir, log = _fakes(tmp_path, running=False)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "nikatel-prev.txt").write_text("REF=x\nID=y\nNAME=node\n")
    out = _bash(ssh.render_action("pgnode_update", {}), bindir, tmp_path)
    assert "NO_CONTAINER" in out and not any(c.startswith("pg-node") for c in log.read_text().splitlines())
    assert not (tmp_path / "state" / "nikatel-prev.txt").exists()                                               # (an old note is never left to be used by a later rollback)


def test_the_old_image_comes_back_under_the_name_the_compose_file_uses(tmp_path, bash_ok):
    bindir, log = _fakes(tmp_path)
    _bash(ssh.render_action("pgnode_update", {}), bindir, tmp_path)
    log.write_text("")
    out = _bash(ssh.render_action("pgnode_rollback", {}), bindir, tmp_path)
    calls = log.read_text().splitlines()
    assert "ROLLED_BACK" in out and "docker tag sha256:OLDIMAGE pasarguard/node:latest" in calls and "docker compose up -d --force-recreate" in calls


def test_a_note_that_is_not_fresh_is_never_used_to_go_back(tmp_path, bash_ok):
    bindir, log = _fakes(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    note = state / "nikatel-prev.txt"
    note.write_text("REF=pasarguard/node:latest\nID=sha256:ANCIENT\nNAME=node\n")
    old = note.stat().st_mtime - 3 * 3600
    os.utime(note, (old, old))
    out = _bash(ssh.render_action("pgnode_rollback", {}), bindir, tmp_path)
    assert "NO_PREV" in out and not any(c.startswith("docker tag") or " up " in c for c in log.read_text().splitlines())


def test_the_xray_command_takes_a_version_with_or_without_the_v_and_nothing_else(tmp_path, bash_ok):
    bindir, log = _fakes(tmp_path)

    for v in ("26.10.29", "v26.10.29"):
        log.write_text("")
        out = _bash(ssh.render_action("pgnode_core", {"VERSION": v}), bindir, tmp_path)
        assert "CORE_UPDATED" in out and log.read_text().splitlines() == ["pg-node core-update --version v26.10.29 -y"]

    for bad in ("latest", "26.10", "26.10.29; reboot", "$(id)"):
        with pytest.raises(ValueError):
            ssh.render_action("pgnode_core", {"VERSION": bad})


def test_the_look_at_a_node_server_changes_nothing(tmp_path, bash_ok):
    bindir, log = _fakes(tmp_path)
    out = subprocess.run(["bash", "-c", ssh.SCRIPTS["pgnode_info"][0]], capture_output=True, text=True, timeout=30, check=False, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}).stdout
    d = pg_safe.parse_node_info(out)
    assert d["cli"] == "yes" and d["docker"] == "yes" and d["running"] == "1" and d["ref"] == "pasarguard/node:latest" and d["id"] == "sha256:OLDIMAGE"
    assert not any(c.startswith(("docker tag", "docker compose", "pg-node")) for c in log.read_text().splitlines())


# ------------------------------------------------------------------ the plan
def _data(nodes, panels_=None, read_only=False, latest=None):
    return {"latest": latest or {"panel": {"version": "v5.6.0"}, "node": {"version": "v0.5.7"}, "xray": {"version": "v26.11.1"}}, "panels": panels_ or [], "nodes": nodes, "read_only": read_only, "checked_at": 0}


def _node(i, name, behind=True, xray_behind=False, status="connected", server=True, keep=0, version="0.5.6", xray="26.10.29", pid=1):
    return {"panel_id": pid, "panel": "P", "node_id": i, "name": name, "address": f"203.0.113.{i}", "status": status, "version": version, "xray": xray, "keep_alive": keep, "behind": behind, "xray_behind": xray_behind,
            "server": {"id": i, "name": f"S{i}"} if server else None}


def _panel(behind=True, host=True, online=True, error=""):
    return {"id": 1, "name": "main", "url": "https://x", "version": "5.5.1", "latest": "v5.6.0", "behind": behind, "error": error, "enabled": True,
            "host": {"id": 99, "name": "boxB", "online": online, "enabled": True} if host else None, "host_name": "x"}


def _plan(monkeypatch, data, usage=None):
    monkeypatch.setattr(pg_update, "overview", lambda force=False, resolver=None: data)
    monkeypatch.setattr(pg_safe, "sample_usage", lambda c, **k: dict(usage or {}))
    monkeypatch.setattr(panels, "cred_for", lambda pid: {})
    return pg_safe.plan()


def test_the_plan_orders_the_nodes_the_quietest_first_and_says_what_each_gets(monkeypatch):
    data = _data([_node(1, "busy"), _node(2, "quiet", xray_behind=True), _node(3, "new", behind=False)])
    p = _plan(monkeypatch, data, {1: 5e6, 2: 1e5})
    assert [n["name"] for n in p["nodes"]] == ["quiet", "busy"] and p["can_start"] is True and p["nothing"] is False
    assert p["nodes"][0]["do"] == ["software 0.5.6 → 0.5.7", "Xray 26.10.29 → 26.11.1"] and p["nodes"][1]["do"] == ["software 0.5.6 → 0.5.7"]
    assert p["nodes"][0]["usage_text"] == "0.8 Mbit/s" and p["steps"] and "cannot be made zero" in p["note"]


def test_a_node_that_cannot_be_updated_is_listed_with_the_reason_and_not_touched(monkeypatch):
    p = _plan(monkeypatch, _data([_node(1, "off", status="disconnected"), _node(2, "alien", server=False), _node(3, "fine")]))
    assert [n["name"] for n in p["nodes"]] == ["fine"] and {s["name"] for s in p["skipped"]} == {"off", "alien"}
    assert any("connect it first" in s["why"] for s in p["skipped"]) and any("no server of ours" in s["why"] for s in p["skipped"])


def test_the_panel_is_part_of_the_plan_only_when_it_can_be_and_no_node_would_stop_its_core(monkeypatch):
    ok = _plan(monkeypatch, _data([_node(1, "a", keep=0)], [_panel()]))
    assert ok["panels"][0]["skip"] == "" and ok["can_start"] is True
    ka = _plan(monkeypatch, _data([_node(1, "a", keep=60), _node(2, "b", keep=0)], [_panel()]))
    assert "a (60 s)" in ka["panels"][0]["skip"] and "at least 900" in ka["panels"][0]["skip"] and ka["can_start"] is True        # (the nodes still go; the panel waits)
    assert "not in the Servers list" in _plan(monkeypatch, _data([], [_panel(host=False)]))["panels"][0]["skip"]
    assert "does not answer" in _plan(monkeypatch, _data([], [_panel(online=False)]))["panels"][0]["skip"]


def test_nothing_to_update_and_a_read_only_panel_are_said(monkeypatch):
    none = _plan(monkeypatch, _data([_node(1, "a", behind=False)], [_panel(behind=False)]))
    assert none["nothing"] is True and none["can_start"] is False
    ro = _plan(monkeypatch, _data([_node(1, "a")], read_only=True))
    assert ro["can_start"] is False and "read-only" in ro["problems"][0]


# ------------------------------------------------------------------ the story, with a fake panel and fake servers
class Clock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(float(s), 1.0)


class Net:
    """The nodes as the panel sees them, and what the commands on their servers do to them."""

    def __init__(self):
        self.nodes: dict[int, dict] = {}
        self.actions: list[tuple] = []
        self.hang: set[int] = set()              # the node does not connect again after its software update
        self.flap: set[int] = set()              # the node connects, then drops again while it is watched
        self.core_fail: set[int] = set()
        self.no_cli: set[int] = set()
        self.updated: dict[int, int] = {}
        self.reads: dict[int, int] = {}
        self.prev: dict[int, tuple] = {}

    def add(self, i, name, version="0.5.6", xray="26.10.29"):
        self.nodes[i] = {"id": i, "name": name, "status": "connected", "version": version, "xray": xray, "message": ""}

    def row(self, c, i):
        n = dict(self.nodes[i])
        self.reads[i] = self.reads.get(i, 0) + 1

        if i in self.flap and i in self.updated and self.reads[i] - self.updated[i] >= 3 and n["version"] == "0.5.7":
            n["status"] = "disconnected"

        return n

    def run_script(self, node, name, params=None, timeout=0):
        i = int(node.host.rsplit(".", 1)[1])
        return ("CLI no" if i in self.no_cli else "CLI yes") + "\nDOCKER yes\nRUNNING 1\nREF pasarguard/node:latest\nID sha256:old\nDIR /opt/pg-node\nDF 900000\n"

    def run_action(self, node, name, params=None, timeout=0):
        i = int(node.host.rsplit(".", 1)[1])
        n = self.nodes[i]
        self.actions.append((name, n["name"], dict(params or {})))

        if name == "pgnode_update":
            self.prev[i] = (n["version"], n["xray"])
            n["version"] = "0.5.7"

            if i in self.hang:
                n["status"] = "connecting"

            self.updated[i] = self.reads.get(i, 0)
            return "PREV kept\nNODE_UPDATED\n"

        if name == "pgnode_rollback":
            n["version"], n["status"] = self.prev[i][0], "connected"
            self.updated.pop(i, None)
            return "ROLLED_BACK\n"

        if name == "pgnode_core":
            if i in self.core_fail:
                return "CORE_UPDATE_FAILED 1\n"

            n["xray"] = params["VERSION"].lstrip("v")
            return "CORE_UPDATED\n"

        raise AssertionError(name)


def _row_of(i, name, panel_id=1, usage="1.0 Mbit/s"):
    return {"panel_id": panel_id, "node_id": i, "name": name, "server": {"id": i, "name": f"S{i}"}, "usage": 1.0, "usage_text": usage, "version": "0.5.6", "xray": "26.10.29", "do": ["software", "Xray"]}


def new_action():
    with session() as db:
        e = ActionLog(ref="t", action="pgupdate", source="manual", story=["Starting…"])
        db.add(e)
        db.flush()
        return e.id


def story(aid):
    with session() as db:
        e = db.get(ActionLog, aid)
        return list(e.story or []), e.ok, bool(e.finished_at)


@pytest.fixture
def world(monkeypatch):
    net, clock = Net(), Clock()
    monkeypatch.setattr(pg_safe, "time", clock)
    monkeypatch.setattr(pg_update, "POLL", 5.0)
    monkeypatch.setattr(pg_update, "NODE_LIMIT", 20.0)
    monkeypatch.setattr(pg_safe, "SOAK", 15.0)
    monkeypatch.setattr(pg_safe, "_row", lambda c, i: net.row(c, i))
    monkeypatch.setattr(pg_safe, "run_script", net.run_script)
    monkeypatch.setattr(pg_safe, "run_action", net.run_action)
    monkeypatch.setattr(pg_nodes, "node_reconnect", lambda c, i: {})
    monkeypatch.setattr(panels, "cred_for", lambda pid: {})
    monkeypatch.setattr(extras, "_node", lambda sid: (Node(f"203.0.113.{sid}", "root", 22), f"S{sid}", f"203.0.113.{sid}"))
    return types.SimpleNamespace(net=net, clock=clock)


def _run(monkeypatch, nodes, panels_=None, skipped=None):
    pl = {"can_start": True, "nothing": False, "problems": [], "nodes": nodes, "skipped": skipped or [], "panels": panels_ or [], "steps": [], "note": "", "latest": {"node": "v0.5.7", "xray": "v26.11.1", "panel": "v5.6.0"}}
    monkeypatch.setattr(pg_safe, "plan", lambda resolver=None, force=False: pl)
    aid = new_action()
    pg_safe._safe_story(aid)
    return aid


def test_every_node_goes_in_the_order_of_the_plan_and_the_story_says_so(world, monkeypatch):
    for i, n in ((1, "a"), (2, "b"), (3, "c")):
        world.net.add(i, n)

    aid = _run(monkeypatch, [_row_of(3, "c"), _row_of(2, "b"), _row_of(1, "a")])
    lines, ok, done = story(aid)
    assert done and ok is True
    assert [a[1] for a in world.net.actions if a[0] == "pgnode_update"] == ["c", "b", "a"] and [a[1] for a in world.net.actions if a[0] == "pgnode_core"] == ["c", "b", "a"]
    assert all(a[2] == {"VERSION": "v26.11.1"} for a in world.net.actions if a[0] == "pgnode_core")
    assert all(n["version"] == "0.5.7" and n["xray"] == "26.11.1" for n in world.net.nodes.values())
    text = "\n".join(lines)
    assert "1/3 c" in text and "2/3 b" in text and "3/3 a" in text and "Done: 3 nodes updated" in text and "Order: c (1.0 Mbit/s), b" in text


def test_a_node_that_does_not_come_back_gets_its_old_version_back_and_the_run_stops(world, monkeypatch):
    for i, n in ((1, "a"), (2, "b"), (3, "c")):
        world.net.add(i, n)

    world.net.hang.add(2)
    aid = _run(monkeypatch, [_row_of(1, "a"), _row_of(2, "b"), _row_of(3, "c")])
    lines, ok, done = story(aid)
    assert done and ok is False
    assert [a[:2] for a in world.net.actions if a[0] in ("pgnode_update", "pgnode_rollback")] == [("pgnode_update", "a"), ("pgnode_update", "b"), ("pgnode_rollback", "b")]
    assert world.net.nodes[2]["version"] == "0.5.6" and world.net.nodes[2]["status"] == "connected" and world.net.nodes[3]["version"] == "0.5.6"      # b is as it was; c was never asked
    text = "\n".join(lines)
    assert "Stopped at 2/3: b" in text and "the old version 0.5.6 is back and connected" in text and "The nodes after it were not touched" in text and "3/3" not in text


def test_a_node_that_connects_and_then_drops_while_watched_is_put_back_too(world, monkeypatch):
    world.net.add(1, "a")
    world.net.flap.add(1)
    aid = _run(monkeypatch, [_row_of(1, "a")])
    lines, ok, _ = story(aid)
    assert ok is False and [a[0] for a in world.net.actions] == ["pgnode_update", "pgnode_rollback"] and world.net.nodes[1]["version"] == "0.5.6"
    assert "after it had come back" in "\n".join(lines) and "the old version 0.5.6 is back and connected" in "\n".join(lines)


def test_a_server_that_is_not_ready_is_not_touched(world, monkeypatch):
    world.net.add(1, "a")
    world.net.no_cli.add(1)
    aid = _run(monkeypatch, [_row_of(1, "a")])
    lines, ok, _ = story(aid)
    assert ok is False and world.net.actions == [] and "pg-node command is not on its server" in "\n".join(lines) and "Nothing was changed" in "\n".join(lines)


def test_a_failed_xray_update_leaves_the_node_as_it_was(world, monkeypatch):
    world.net.add(1, "a")
    world.net.core_fail.add(1)
    aid = _run(monkeypatch, [_row_of(1, "a")])
    lines, ok, _ = story(aid)
    text = "\n".join(lines)
    assert ok is False and world.net.nodes[1]["xray"] == "26.10.29" and "the node is connected with its old Xray" in text
    assert [a[0] for a in world.net.actions] == ["pgnode_update", "pgnode_core"]                                  # (the software stays updated: it was watched and held; only Xray did not)


def test_a_node_that_is_the_newest_already_is_skipped(world, monkeypatch):
    world.net.add(1, "a", version="0.5.7", xray="26.11.1")
    aid = _run(monkeypatch, [_row_of(1, "a")])
    lines, ok, _ = story(aid)
    assert ok is True and world.net.actions == [] and "is the newest already - skipped" in "\n".join(lines)


def test_the_panel_comes_last_and_ends_the_action_with_its_own_story(world, monkeypatch):
    world.net.add(1, "a")
    seen = []

    def panel_story(aid, pid, sid, backup):
        seen.append((pid, sid, backup, [x[0] for x in world.net.actions]))
        autopilot._say(aid, "the panel was updated (fake)", done=True, ok=True)

    monkeypatch.setattr(pg_update, "_panel_story", panel_story)
    pan = [{"id": 1, "name": "main", "version": "5.5.1", "latest": "v5.6.0", "host": {"id": 99, "name": "boxB"}, "skip": ""}]
    aid = _run(monkeypatch, [_row_of(1, "a")], pan)
    lines, ok, done = story(aid)
    assert done and ok is True and seen == [(1, 99, True, ["pgnode_update", "pgnode_core"])]                           # (the nodes were done BEFORE the panel was started; with a backup)
    assert "Now the panel main (5.5.1 → 5.6.0)" in "\n".join(lines)


def test_a_node_that_goes_wrong_means_the_panel_is_not_started(world, monkeypatch):
    world.net.add(1, "a")
    world.net.hang.add(1)
    monkeypatch.setattr(pg_update, "_panel_story", lambda *a: pytest.fail("the panel must wait"))
    pan = [{"id": 1, "name": "main", "version": "5.5.1", "latest": "v5.6.0", "host": {"id": 99, "name": "boxB"}, "skip": ""}]
    _, ok, done = story(_run(monkeypatch, [_row_of(1, "a")], pan))
    assert done and ok is False


def test_a_panel_that_is_skipped_says_why_and_the_run_is_not_called_complete(world, monkeypatch):
    world.net.add(1, "a")
    pan = [{"id": 1, "name": "main", "version": "5.5.1", "latest": "v5.6.0", "host": None, "skip": "its server is not in the Servers list"}]
    lines, ok, done = story(_run(monkeypatch, [_row_of(1, "a")], pan))
    text = "\n".join(lines)
    assert done and ok is False and "The panel main was NOT updated: its server is not in the Servers list" in text and "Done: 1 node updated" in text


# ------------------------------------------------------------------ the start, the routes, the button
def _stub_plan(monkeypatch, **over):
    pl = {"can_start": True, "nothing": False, "problems": [], "nodes": [_row_of(1, "a")], "skipped": [], "panels": [], "steps": ["s"], "note": "n", "latest": {}}
    pl.update(over)
    monkeypatch.setattr(pg_safe, "plan", lambda resolver=None, force=False: pl)
    return pl


def test_the_start_refuses_what_it_should_and_starts_nothing_otherwise(monkeypatch):
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append(label) or 7)
    _stub_plan(monkeypatch, problems=["The panel is read-only: x"])

    with pytest.raises(ValueError, match="read-only"):
        pg_safe.start()

    _stub_plan(monkeypatch, nothing=True, nodes=[], skipped=[{"name": "n", "why": "connect it first"}])

    with pytest.raises(ValueError, match="nothing to update now: connect it first"):
        pg_safe.start()

    pan = [{"id": 1, "name": "main", "version": "5.5.1", "latest": "v5.6.0", "host": {"id": 99, "name": "b"}, "skip": ""}]
    _stub_plan(monkeypatch, panels=pan)

    with pytest.raises(ValueError, match="type the name of the panel"):
        pg_safe.start("wrong")

    assert started == []
    monkeypatch.setitem(autopilot._BUSY, "pg node updates", 3)

    with pytest.raises(ValueError, match="running right now"):
        pg_safe.start("main")

    autopilot._BUSY.pop("pg node updates")
    assert pg_safe.start("main") == 7 and started == ["pg safe update"]
    _stub_plan(monkeypatch)                                                                                         # (no panel in it: no name to type)
    assert pg_safe.start() == 7


def test_the_routes_answer_and_a_refusal_is_a_400(client, monkeypatch):
    _stub_plan(monkeypatch)
    r = client.get("/api/pg-updates/safe/plan")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["can_start"] is True and r.json()["nodes"][0]["name"] == "a"
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: 42)
    ok = client.post("/api/pg-updates/safe", json={})
    assert ok.status_code == 202 and ok.json() == {"ok": True, "action_id": 42}
    _stub_plan(monkeypatch, nothing=True, nodes=[])
    assert client.post("/api/pg-updates/safe", json={}).status_code == 400


def test_the_overview_carries_the_keep_alive_of_each_node():
    src = (Path(__file__).resolve().parent.parent / "nikatel" / "services" / "pg_update.py").read_text(encoding="utf-8")
    assert '"keep_alive": n["keep_alive"], "behind": newer(tag("node"), n["version"])' in src


def test_the_button_and_its_window():
    js = (WEB / "pg_update.js").read_text(encoding="utf-8")
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert 'class="sm pri pgu-safe"' in js and 'onclick="pgUpdSafe()"' in js and '>Safe update…</button>' in js and 'class="pgu-go">${safeBtn}<button' in js
    assert "window.pgUpdSafe = async () =>" in js and 'api("/api/pg-updates/safe/plan")' in js and 'api("/api/pg-updates/safe", {method: "POST"' in js and "Start Safe update" in js
    assert "Type the name of the panel to confirm" in js and "Order - the quietest first" in js
    assert "html body #page .abt-pg .pgu-go .pgu-safe { grid-column: 1 / -1;" in css
