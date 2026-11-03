"""The PasarGuard panel and its nodes updated by themselves at night (the owner, 2026-11-03: "a tick box so the panel, its nodes and Xray update by themselves at 03:00 Iran time - test first, very safe, roll back or fix
on a problem, a complete update").

Proved here, with a fake panel API, fake servers (the actions answer the way the real scripts do), a fake probe and a fake Telegram: the switch and the clock (off by default, only at the hour, once a night, never over
a manual update, never read-only); the whole night end to end (the nodes in order with the users looked at after each, then the panel kept, updated and checked, the summary in the settings, in the feed and on
Telegram); a node whose users do not pass gets both old versions back, the releases are held back and the next night leaves them alone; a node that does not come back stops the night before the panel; the time
budget; the panel's window; the panel put back when its sign-in fails after the update, restarted once when it does not answer, the owner called when the way back fails; the rehearsal walks every step and changes
nothing; the scripts of the panel run by bash against a fake docker (the image kept with a note, the compose files and a database dump copied, the panel service alone pulled and recreated, SAME_IMAGE, the old
image back under its name, a stale note never used, the restart); the routes, the switch on the card, the job and the settings.
NOT proved: a real PasarGuard panel or node, real docker, a real probe, a real night."""
import os
import shutil
import stat
import subprocess
import time as _time
import types
from pathlib import Path

import pytest

from nikatel import ssh
from nikatel.db import session
from nikatel.models import ActionLog, Event
from nikatel.services import (
    autopilot,
    extras,
    panels,
    pg_auto,
    pg_nodes,
    pg_safe,
    pg_update,
    probe,
    telegram,
)
from nikatel.services import settings as cfg
from nikatel.ssh import Node

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
OFF = 210 * 60                   # the panel clock: Tehran, the default of the daily report


def at_local(hour: int, minute: int = 0) -> float:
    """A moment (UTC seconds) whose time on the panel clock is hour:minute, on one fixed day."""
    day0 = (1_793_400_000 + OFF) // 86400 * 86400
    return float(day0 + hour * 3600 + minute * 60 - OFF)


DAY = _time.strftime("%Y-%m-%d", _time.gmtime(at_local(3) + OFF))


# ------------------------------------------------------------------ pure
def test_the_look_at_the_panel_server_is_read_into_a_dict():
    d = pg_auto.parse_panel_info("CLI no\nBACKUP no\nDIR none\nDOCKER 3\nDF 50000000\nCONTAINER pasarguard-pasarguard-1\nREF pasarguard/panel:latest\nID sha256:abc\nSERVICE pasarguard\nPROJECT pasarguard\nWDIR /opt/pasarguard\nDB postgres\nOVERRIDES 2\nnoise here\n")
    assert d["container"] == "pasarguard-pasarguard-1" and d["db"] == "postgres" and d["overrides"] == "2" and d["wdir"] == "/opt/pasarguard" and d["cli"] == "no" and d["id"] == "sha256:abc" and "noise" not in d
    assert pg_auto.parse_panel_info("OVERRIDES \n")["overrides"] == "" and pg_auto.parse_panel_info("") == {}


def test_the_verdict_of_the_fresh_tests_of_the_probe():
    seen = {"up1": [(30, "down"), (60, "up")], "down1": [(30, "down"), (60, "down")], "pend": [(30, "down")], "none": []}
    assert pg_auto.judge(seen) == (["up1"], ["down1"], ["pend", "none"])


def test_a_release_that_is_held_back_is_known_with_or_without_the_v():
    bad = {"node": {"version": "v0.5.7", "where": "a", "day": "d"}}
    assert pg_auto._held(bad, "node", "0.5.7") == bad["node"] and pg_auto._held(bad, "node", "v0.5.8") is None and pg_auto._held(bad, "xray", "v0.5.7") is None and pg_auto._held({}, "node", "") is None


def test_the_summary_and_the_telegram_message():
    got = {"updated": ["a (software)"], "back": [], "left": ["the panel main: the window closed"], "needs": [], "skipped": [], "dry": False, "day": "2026-11-04", "aid": 5}
    assert pg_auto.summary(got) == "updated a (software). left for the next night: the panel main: the window closed"
    m = pg_auto.message(got)
    assert m.startswith("PasarGuard nightly update - 2026-11-04\nUpdated: a (software)\nPut back: -\nLeft for the next night: the panel main: the window closed\nNeeds you: -\n") and "action #5" in m
    assert pg_auto.summary({**got, "updated": [], "left": []}) == "nothing to do" and pg_auto.summary({**got, "dry": True}).startswith("would update a")


# ------------------------------------------------------------------ the scripts of the panel, run by bash against a fake docker
@pytest.fixture
def bash_ok():
    if shutil.which("bash") is None:
        pytest.skip("no bash")


def _fake_docker(tmp_path, running=True, pg=True, same=False):
    """A `docker` that knows one compose project 'pasarguard' (the panel service and, with `pg`, a postgres service), keeps the id of the tag pasarguard/panel:latest and of the running container in files, and
    answers `compose pull` with a new image unless `same`."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.txt"
    log.write_text("")
    st = tmp_path / "dstate"
    st.mkdir(exist_ok=True)
    opt = tmp_path / "opt"
    opt.mkdir(exist_ok=True)
    (opt / "docker-compose.yml").write_text("services:\n  pasarguard:\n    image: pasarguard/panel:latest\n")
    (opt / "docker-compose.override.yml").write_text("services:\n  pasarguard:\n    volumes:\n      - /x/a.py:/code/app/a.py:ro\n      - /x/b.py:/code/app/b.py:ro\n")
    (opt / ".env").write_text("UVICORN_PORT=8000\n")
    ps = "pasarguard-pasarguard-1 pasarguard/panel:latest\\n" + ("pasarguard-postgresql-1 postgres:latest\\n" if pg else "")
    images = "pasarguard/panel:latest\\n" + ("postgres:latest\\n" if pg else "")
    (bindir / "docker").write_text(
        "#!/bin/bash\n"
        f'echo "docker $*" >> {log}\n'
        f"S={st}\n"
        'case "$1 $2" in\n'
        f'  "ps --format") [ -n "{"1" if running else ""}" ] || exit 0; printf \'{ps}\';;\n'
        f'  "ps --filter") case "$*" in *Names*) printf \'{ps}\';; *) printf \'{images}\';; esac;;\n'
        '  "inspect --format") case "$3" in\n'
        "      '{{.Config.Image}}') echo pasarguard/panel:latest;;\n"
        "      '{{.Image}}') cat $S/running 2>/dev/null || echo sha256:OLDPANEL;;\n"
        "      *compose.service*) echo pasarguard;;\n"
        f"      *working_dir*) echo {opt};;\n"
        "      *compose.project*) echo pasarguard;;\n"
        "    esac;;\n"
        '  "image inspect") if [ "$3" = "--format" ]; then cat $S/tag 2>/dev/null || echo sha256:OLDPANEL; else exit 0; fi;;\n'
        '  "tag "*) [ "$3" = "nikatel/pg-panel-prev:keep" ] || echo "$2" > $S/tag;;\n'
        '  "compose version") echo "Docker Compose version v2.0";;\n'
        f'  "compose pull") [ -n "{"1" if same else ""}" ] || echo sha256:NEWPANEL > $S/tag;;\n'
        '  "compose up") cp $S/tag $S/running 2>/dev/null || echo sha256:OLDPANEL > $S/running;;\n'
        '  "compose restart") ;;\n'
        '  "exec "*) echo PGDUMPDATA;;\n'
        "esac\nexit 0\n")
    (bindir / "docker").chmod((bindir / "docker").stat().st_mode | stat.S_IEXEC)
    return bindir, log, opt


def _bash(text, bindir, tmp_path):
    text = text.replace("/var/lib/nikatel-install", str(tmp_path / "state")).replace("/tmp/nikatel-pgpanel", str(tmp_path / "log-pgpanel")).replace("/var/lib/pasarguard/db.sqlite3", str(tmp_path / "db.sqlite3"))
    (tmp_path / "state").mkdir(exist_ok=True)
    return subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=30, check=False, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}).stdout


def test_the_keep_tags_the_image_copies_the_compose_files_and_dumps_the_database(tmp_path, bash_ok):
    bindir, log, opt = _fake_docker(tmp_path)
    out = _bash(ssh.render_action("pgpanel_keep", {}), bindir, tmp_path)
    assert "PANEL_KEPT sha256:OLDPANEL" in out and "DUMP postgres" in out and "OVERRIDES 2" in out, out
    calls = log.read_text().splitlines()
    assert "docker tag sha256:OLDPANEL nikatel/pg-panel-prev:keep" in calls and any(c.startswith("docker exec pasarguard-postgresql-1 sh -c exec pg_dump") for c in calls)
    k = tmp_path / "state" / "pgpanel-prev"
    assert (k / "note.txt").read_text().startswith("REF=pasarguard/panel:latest\nID=sha256:OLDPANEL\nNAME=pasarguard-pasarguard-1\nPROJECT=pasarguard\nSERVICE=pasarguard\nDIR=" + str(opt) + "\n")
    assert (k / "docker-compose.yml").exists() and (k / "docker-compose.override.yml").exists() and (k / ".env").exists()
    dumps = list(k.glob("db-*.dump"))
    assert len(dumps) == 1 and dumps[0].read_text().strip() == "PGDUMPDATA"
    assert not any(" up " in c or " down" in c or "restart" in c for c in calls)                                        # (the keep restarts nothing)


def test_a_keep_without_the_panel_container_says_so_and_a_sqlite_panel_gets_a_copy(tmp_path, bash_ok):
    bindir, _log, _ = _fake_docker(tmp_path, running=False)
    out = _bash(ssh.render_action("pgpanel_keep", {}), bindir, tmp_path)
    assert "NO_CONTAINER" in out and "PANEL_KEPT" not in out and not (tmp_path / "state" / "pgpanel-prev" / "note.txt").exists()
    bindir, _log, _ = _fake_docker(tmp_path, pg=False)
    (tmp_path / "db.sqlite3").write_text("sqlite")
    out = _bash(ssh.render_action("pgpanel_keep", {}), bindir, tmp_path)
    assert "DUMP sqlite" in out and "PANEL_KEPT" in out and list((tmp_path / "state" / "pgpanel-prev").glob("db-*.sqlite3"))


def test_the_update_needs_a_fresh_keep_then_pulls_and_recreates_the_panel_service_alone(tmp_path, bash_ok):
    bindir, log, _ = _fake_docker(tmp_path)
    assert "NO_KEEP" in _bash(ssh.render_action("pgpanel_update", {}), bindir, tmp_path) and log.read_text() == ""          # (no keep: no update, docker not even asked)
    _bash(ssh.render_action("pgpanel_keep", {}), bindir, tmp_path)
    log.write_text("")
    out = _bash(ssh.render_action("pgpanel_update", {}), bindir, tmp_path)
    calls = log.read_text().splitlines()
    assert "PANEL_UPDATED sha256:NEWPANEL" in out and "docker compose pull -q pasarguard" in calls and "docker compose up -d --no-deps pasarguard" in calls, out
    assert not any(" down" in c for c in calls) and (tmp_path / "dstate" / "running").read_text().strip() == "sha256:NEWPANEL"


def test_a_registry_with_nothing_newer_changes_nothing(tmp_path, bash_ok):
    bindir, log, _ = _fake_docker(tmp_path, same=True)
    _bash(ssh.render_action("pgpanel_keep", {}), bindir, tmp_path)
    log.write_text("")
    out = _bash(ssh.render_action("pgpanel_update", {}), bindir, tmp_path)
    assert "SAME_IMAGE" in out and not any(" up " in c for c in log.read_text().splitlines())


def test_the_old_image_comes_back_under_its_name_and_a_stale_note_is_never_used(tmp_path, bash_ok):
    bindir, log, _ = _fake_docker(tmp_path)
    _bash(ssh.render_action("pgpanel_keep", {}), bindir, tmp_path)
    _bash(ssh.render_action("pgpanel_update", {}), bindir, tmp_path)
    log.write_text("")
    out = _bash(ssh.render_action("pgpanel_rollback", {}), bindir, tmp_path)
    calls = log.read_text().splitlines()
    assert "ROLLED_BACK" in out and "docker tag sha256:OLDPANEL pasarguard/panel:latest" in calls and "docker compose up -d --no-deps --force-recreate pasarguard" in calls, out
    assert (tmp_path / "dstate" / "running").read_text().strip() == "sha256:OLDPANEL"
    note = tmp_path / "state" / "pgpanel-prev" / "note.txt"
    old = note.stat().st_mtime - 4 * 3600
    os.utime(note, (old, old))
    log.write_text("")
    assert "NO_PREV" in _bash(ssh.render_action("pgpanel_rollback", {}), bindir, tmp_path) and log.read_text() == ""


def test_the_restart_restarts_the_panel_service_alone(tmp_path, bash_ok):
    bindir, log, _ = _fake_docker(tmp_path)
    assert "NO_PREV" in _bash(ssh.render_action("pgpanel_restart", {}), bindir, tmp_path)
    _bash(ssh.render_action("pgpanel_keep", {}), bindir, tmp_path)
    log.write_text("")
    assert "RESTARTED" in _bash(ssh.render_action("pgpanel_restart", {}), bindir, tmp_path) and "docker compose restart pasarguard" in log.read_text().splitlines()


def test_the_look_at_the_panel_server_names_the_container_the_database_and_the_overrides_and_changes_nothing(tmp_path, bash_ok):
    bindir, log, opt = _fake_docker(tmp_path)
    out = subprocess.run(["bash", "-c", ssh.SCRIPTS["pgpanel_info"][0]], capture_output=True, text=True, timeout=30, check=False, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}).stdout
    d = pg_auto.parse_panel_info(out)
    assert d["container"] == "pasarguard-pasarguard-1" and d["service"] == "pasarguard" and d["db"] == "postgres" and d["overrides"] == "2" and d["wdir"] == str(opt) and d["id"] == "sha256:OLDPANEL", out
    assert not any(c.startswith(("docker tag", "docker compose", "docker exec")) for c in log.read_text().splitlines())


# ------------------------------------------------------------------ the night, with a fake panel, fake servers, a fake probe and a fake Telegram
class Clock:
    def __init__(self, t):
        self.t = float(t)

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(float(s), 1.0)

    strftime = staticmethod(_time.strftime)
    gmtime = staticmethod(_time.gmtime)


class World:
    """The nodes as the panel sees them, the panel's own answers, what the commands on the servers do, what the probe says about the configs."""

    def __init__(self, clock):
        self.clock = clock
        self.nodes: dict[int, dict] = {}
        self.actions: list[tuple] = []
        self.asked: list[int] = []
        self.hang: set[int] = set()              # the node does not connect again after its software update
        self.users_fail: set[int] = set()        # the configs that ride the node say down after its update
        self.prev: dict[int, tuple] = {}
        self.cfgs: dict[int, list[str]] = {}     # server id -> the configs that ride it
        self.cfg_down: set[str] = set()
        self.tg: list[str] = []
        self.panel = types.SimpleNamespace(version="5.5.1", up=True, same=False, dead_after=False, restart_helps=False, login_ok=True, door_ok=True, nodes_back=True, rollback_ok=True, keep_ok=True, updated=False, restarted=0, rolled=False)

    def add(self, i, name, version="0.5.6", xray="26.10.29"):
        self.nodes[i] = {"id": i, "name": name, "status": "connected", "version": version, "xray": xray, "message": ""}

    def row(self, c, i):
        return dict(self.nodes[i])

    def run_script(self, node, name, params=None, timeout=0):
        if name == "pgnode_info":
            return "CLI yes\nDOCKER yes\nRUNNING 1\nREF pasarguard/node:latest\nID sha256:old\nDIR /opt/pg-node\nDF 900000\n"

        if name == "pgpanel_info":
            return "CLI no\nBACKUP no\nDIR none\nDOCKER 3\nDF 50000000\nCONTAINER pasarguard-pasarguard-1\nREF pasarguard/panel:latest\nID sha256:OLDPANEL\nSERVICE pasarguard\nPROJECT pasarguard\nWDIR /opt/pasarguard\nDB postgres\nOVERRIDES 2\n"

        raise AssertionError(name)

    def run_action(self, node, name, params=None, timeout=0):
        self.actions.append((name, node.host, dict(params or {})))
        p = self.panel

        if name == "pgpanel_keep":
            return "DUMP postgres /k/db.dump 42000000\nOVERRIDES 2\nPANEL_KEPT sha256:OLDPANEL\n" if p.keep_ok else "DUMP_FAILED postgres no space left\n"

        if name == "pgpanel_update":
            if p.same:
                return "SAME_IMAGE\n"

            p.updated, p.version, p.up = True, "5.6.0", not p.dead_after
            return "PANEL_UPDATED sha256:NEWPANEL\n"

        if name == "pgpanel_restart":
            p.restarted += 1

            if p.restart_helps:
                p.up = True

            return "RESTARTED\n"

        if name == "pgpanel_rollback":
            if not p.rollback_ok:
                return "ROLLBACK_FAILED 1\n"

            p.rolled, p.version, p.up, p.updated = True, "5.5.1", True, False
            return "ROLLED_BACK\n"

        i = int(node.host.rsplit(".", 1)[1])
        n = self.nodes[i]

        if name == "pgnode_update":
            self.prev[i] = (n["version"], n["xray"])
            n["version"] = "0.5.7"

            if i in self.hang:
                n["status"] = "connecting"

            if i in self.users_fail:
                self.cfg_down |= set(self.cfgs.get(i, []))

            return "PREV kept\nNODE_UPDATED\n"

        if name == "pgnode_rollback":
            n["version"], n["status"] = self.prev[i][0], "connected"
            self.cfg_down -= set(self.cfgs.get(i, []))
            return "ROLLED_BACK\n"

        if name == "pgnode_core":
            n["xray"] = params["VERSION"].lstrip("v")
            return "CORE_UPDATED\n"

        raise AssertionError(name)

    def check(self, c):
        p = self.panel

        if not p.up:
            return {"ok": False, "error": "no answer from the panel (ConnectError)", "nodes": [], "stats": {}}

        nodes = [{"id": i, "name": n["name"], "status": "connected" if (p.nodes_back or not p.updated) else "connecting"} for i, n in self.nodes.items()]
        return {"ok": True, "error": "", "stats": {"version": p.version, "users_total": 7}, "nodes": nodes}

    def health(self):
        at = int(self.clock.t // 30 * 30)
        return {"configs": [{"name": n, "status": "down" if n in self.cfg_down else "up", "checked_at": at} for names in self.cfgs.values() for n in names]}


def _row_of(i, name, panel_id=1):
    return {"panel_id": panel_id, "node_id": i, "name": name, "server": {"id": i, "name": f"S{i}"}, "usage": 1.0, "usage_text": "1.0 Mbit/s", "version": "0.5.6", "xray": "26.10.29", "do": ["software", "Xray"]}


PANEL = {"id": 1, "name": "main", "version": "5.5.1", "latest": "v5.6.0", "host": {"id": 99, "name": "boxB"}, "skip": ""}


def new_action():
    with session() as db:
        e = ActionLog(ref="t", action="pgupdate", source="auto", story=["Starting…"])
        db.add(e)
        db.flush()
        return e.id


def story(aid):
    with session() as db:
        e = db.get(ActionLog, aid)
        return list(e.story or []), e.ok, bool(e.finished_at)


def events():
    with session() as db:
        return [(e.level, e.text) for e in db.query(Event).order_by(Event.id)]


@pytest.fixture
def world(monkeypatch):
    clock = Clock(at_local(3, 2))
    w = World(clock)

    for m in (pg_auto, pg_safe, pg_update):
        monkeypatch.setattr(m, "time", clock)

    monkeypatch.setattr(pg_update, "POLL", 5.0)
    monkeypatch.setattr(pg_update, "NODE_LIMIT", 20.0)
    monkeypatch.setattr(pg_update, "PANEL_LIMIT", 30.0)
    monkeypatch.setattr(pg_safe, "SOAK", 15.0)
    monkeypatch.setattr(pg_safe, "AGAIN", 10.0)
    monkeypatch.setattr(pg_safe, "CORE_RETRY_WAIT", 1.0)
    monkeypatch.setattr(pg_auto, "USERS_LIMIT", 90.0)
    monkeypatch.setattr(pg_auto, "NODES_BACK", 20.0)
    monkeypatch.setattr(pg_auto, "PANEL_AGAIN", 10.0)
    monkeypatch.setattr(pg_safe, "_row", lambda c, i: w.row(c, i))
    monkeypatch.setattr(pg_safe, "run_script", w.run_script)
    monkeypatch.setattr(pg_safe, "run_action", w.run_action)
    monkeypatch.setattr(pg_auto, "run_script", w.run_script)
    monkeypatch.setattr(pg_auto, "run_action", w.run_action)
    monkeypatch.setattr(pg_nodes, "node_reconnect", lambda c, i: w.asked.append(i) or {})
    monkeypatch.setattr(panels, "cred_for", lambda pid: {"id": pid, "url": "https://x", "username": "a", "password": "b", "verify_tls": True})
    monkeypatch.setattr(panels, "check", w.check)
    monkeypatch.setattr(extras, "_node", lambda sid: (Node(f"203.0.113.{sid}", "root", 22), f"S{sid}", f"203.0.113.{sid}"))
    monkeypatch.setattr(pg_auto, "login_check", lambda c: (w.panel.login_ok, "signed in (fake)"))
    monkeypatch.setattr(pg_auto, "front_door", lambda c: (w.panel.door_ok, "HTTP 404 (fake)"))
    monkeypatch.setattr(pg_auto, "configs_of_server", lambda sid: [{"name": n, "status": "up"} for n in w.cfgs.get(sid, [])])
    monkeypatch.setattr(probe, "health", w.health)
    monkeypatch.setattr(telegram, "send_text", lambda text, require_enabled=True: w.tg.append(text) or (True, "sent"))
    cfg.put("pg_auto", {"auto": True})
    return w


def _night(w, monkeypatch, nodes, panels_=None, dry=False):
    pl = {"scope": "all", "can_start": True, "nothing": False, "problems": [], "nodes": nodes, "skipped": [], "panels": panels_ or [], "steps": [], "note": "", "latest": {"node": "v0.5.7", "xray": "v26.11.1", "panel": "v5.6.0"}}
    monkeypatch.setattr(pg_safe, "plan", lambda resolver=None, force=False, scope="all": pl)
    aid = new_action()
    pg_auto._night_story(aid, dry)
    return aid


def test_the_whole_night_the_nodes_in_order_with_the_users_looked_at_then_the_panel_with_a_summary(world, monkeypatch):
    world.add(1, "a")
    world.add(2, "b")
    world.cfgs = {1: ["cfg-a1", "cfg-a2"], 2: ["cfg-b"]}
    aid = _night(world, monkeypatch, [_row_of(2, "b"), _row_of(1, "a")], [dict(PANEL)])
    lines, ok, done = story(aid)
    text = "\n".join(lines)
    assert done and ok is True, text
    assert [a[0] for a in world.actions] == ["pgnode_update", "pgnode_core", "pgnode_update", "pgnode_core", "pgpanel_keep", "pgpanel_update"]
    assert [a[1] for a in world.actions[:4]] == ["203.0.113.2", "203.0.113.2", "203.0.113.1", "203.0.113.1"]                       # (the quietest first, the panel LAST)
    assert "users: 1 of 1 config that ride it passes again." in text and "users: 2 of 2 configs that ride it pass again." in text
    assert "kept: the image, the compose files, a postgres dump (42 MB)" in text
    assert "the panel answers: version 5.5.1 → 5.6.0, the sign-in works (signed in (fake)), the subscription front door answers (HTTP 404 (fake)), nodes connected 2 → 2." in text
    assert lines[-1] == "Done: updated b (software, Xray), a (software, Xray), the panel main (5.5.1 → 5.6.0)"
    last = cfg.get("pg_auto")["last"]
    assert last["ok"] is True and last["day"] == DAY and last["action_id"] == aid and last["updated"] == ["b (software, Xray)", "a (software, Xray)", "the panel main (5.5.1 → 5.6.0)"]
    assert len(world.tg) == 1 and "Updated: b (software, Xray), a (software, Xray), the panel main (5.5.1 → 5.6.0)" in world.tg[0] and "Needs you: -" in world.tg[0] and f"action #{aid}" in world.tg[0]
    ev = events()
    assert ("ok", "PasarGuard panel main updated by itself at night: 5.5.1 → 5.6.0.") in ev and any(lvl == "info" and t.startswith("Nightly PasarGuard update: updated b") for lvl, t in ev)
    assert cfg.get("pg_auto")["bad"] == {}


def test_a_node_whose_users_do_not_pass_gets_both_old_versions_back_and_the_releases_are_held_back(world, monkeypatch):
    world.add(1, "a")
    world.add(2, "b")
    world.cfgs = {1: ["c1", "c2"]}
    world.users_fail.add(1)
    aid = _night(world, monkeypatch, [_row_of(1, "a"), _row_of(2, "b")], [dict(PANEL)])
    lines, ok, done = story(aid)
    text = "\n".join(lines)
    assert done and ok is False, text
    assert [a[0] for a in world.actions] == ["pgnode_update", "pgnode_core", "pgnode_core", "pgnode_rollback"] and world.actions[2][2] == {"VERSION": "26.10.29"}      # (Xray back first, then the image)
    assert world.nodes[1]["version"] == "0.5.6" and world.nodes[1]["xray"] == "26.10.29" and world.nodes[1]["status"] == "connected" and world.nodes[2]["version"] == "0.5.6"
    assert "users: 0 of 2 configs that ride it pass again; c1, c2 do not (2 tests each)." in text
    assert "Stopped at 1/2: a - the old Xray 26.10.29 is back and the node is connected; the old version 0.5.6 is back and connected. The nodes after it were not touched." in text
    assert "2/2" not in text and "Panel main" not in text
    bad = cfg.get("pg_auto")["bad"]
    assert bad["node"]["version"] == "v0.5.7" and bad["xray"]["version"] == "v26.11.1" and bad["node"]["where"] == "a" and bad["node"]["day"] == DAY and "do not" in bad["node"]["why"]
    assert lines[-1].startswith("Done: put back a: 0 of 2 configs that ride it pass again; c1, c2 do not (2 tests each) - the old Xray 26.10.29 is back") and "left for the next night: the panel main: the run stopped at a node before the panel" in lines[-1]
    assert cfg.get("pg_auto")["last"]["ok"] is False and "Put back: a: 0 of 2 configs" in world.tg[0] and any(lvl == "warn" and t.startswith("Nightly PasarGuard update stopped at a node") for lvl, t in events())
    # the next night: both releases are held back, so the nodes are left alone and the owner is told
    world.actions.clear()
    world.tg.clear()
    cfg.put("pg_auto", {"last": {}})
    lines2, _ok2, done2 = story(_night(world, monkeypatch, [_row_of(1, "a"), _row_of(2, "b")]))
    assert done2 and world.actions == [] and "not touched: a: only a release that is held back; b: only a release that is held back" in lines2[-1] and "NEEDS YOU: the node software 0.5.7 is held back" in lines2[-1]


def test_a_node_that_does_not_come_back_stops_the_night_before_the_panel(world, monkeypatch):
    world.add(1, "a")
    world.add(2, "b")
    world.hang.add(1)
    aid = _night(world, monkeypatch, [_row_of(1, "a"), _row_of(2, "b")], [dict(PANEL)])
    lines, ok, done = story(aid)
    text = "\n".join(lines)
    assert done and ok is False and [a[0] for a in world.actions] == ["pgnode_update", "pgnode_rollback"]
    assert "asking the panel once more to connect to it" in text and "Stopped at 1/2: a - the old version 0.5.6 is back and connected. The nodes after it were not touched." in text and "Panel main" not in text
    bad = cfg.get("pg_auto")["bad"]
    assert bad["node"]["version"] == "v0.5.7" and "xray" not in bad                                                      # (only the release that was tried is held back)
    assert lines[-1] == "Done: put back a: the old version 0.5.6 is back and connected. left for the next night: the panel main: the run stopped at a node before the panel"


def test_no_node_and_not_the_panel_is_started_after_the_time_budget(world, monkeypatch):
    for i, n in ((1, "a"), (2, "b"), (3, "c")):
        world.add(i, n)

    cfg.put("pg_auto", {"budget_minutes": 5})
    monkeypatch.setattr(pg_safe, "_update_node", lambda aid, i, total, row, nt, xt: world.clock.sleep(200) or (True, "", ""))
    monkeypatch.setattr(pg_auto, "users_check", lambda watch, base: (True, "fake"))
    aid = _night(world, monkeypatch, [_row_of(1, "a"), _row_of(2, "b"), _row_of(3, "c")], [dict(PANEL)])
    lines, ok, done = story(aid)
    text = "\n".join(lines)
    assert done and ok is True and "The time budget of 5 minutes is used up: c waits for the next night." in text and world.actions == []
    assert "left for the next night: 1 node (c): the time budget of 5 minutes was used up; the panel main: it is only started in the first 45 minutes of its hour and inside the time budget" in lines[-1]
    assert "Left for the next night: 1 node (c)" in world.tg[0]


def test_the_panel_is_only_started_in_the_first_minutes_of_its_hour(world, monkeypatch):
    world.clock.t = at_local(3, 50)
    aid = _night(world, monkeypatch, [], [dict(PANEL)])
    lines, ok, done = story(aid)
    assert done and ok is True and world.actions == [] and "The panel main is not started now (03:50 on the panel clock): it waits for the next night." in "\n".join(lines)


def test_the_panel_is_put_back_when_its_sign_in_fails_after_the_update(world, monkeypatch):
    world.add(1, "a")
    world.panel.login_ok = False
    aid = _night(world, monkeypatch, [], [dict(PANEL)])
    lines, ok, done = story(aid)
    text = "\n".join(lines)
    assert done and ok is False and [a[0] for a in world.actions] == ["pgpanel_keep", "pgpanel_update", "pgpanel_rollback"], text
    assert "the panel answers (5.6.0) but the sign-in does not work (signed in (fake)); putting the old image back…" in text and "the old image is back and answers (5.5.1), nodes connected 1 of 1." in text
    assert cfg.get("pg_auto")["bad"]["panel"]["version"] == "v5.6.0" and world.panel.rolled and "Put back: the panel main: the panel answers (5.6.0) but the sign-in does not work" in world.tg[0]


def test_a_panel_that_does_not_answer_after_its_update_is_restarted_once_and_then_holds(world, monkeypatch):
    world.add(1, "a")
    world.panel.dead_after = True
    world.panel.restart_helps = True
    aid = _night(world, monkeypatch, [], [dict(PANEL)])
    lines, ok, done = story(aid)
    text = "\n".join(lines)
    assert done and ok is True and [a[0] for a in world.actions] == ["pgpanel_keep", "pgpanel_update", "pgpanel_restart"], text
    assert "its service is restarted once" in text and "version 5.5.1 → 5.6.0" in text and world.panel.restarted == 1


def test_the_nodes_are_asked_once_more_to_connect_and_the_panel_goes_back_when_they_do_not(world, monkeypatch):
    world.add(1, "a")
    world.add(2, "b")
    world.panel.nodes_back = False
    aid = _night(world, monkeypatch, [], [dict(PANEL)])
    lines, ok, done = story(aid)
    text = "\n".join(lines)
    assert done and ok is False and [a[0] for a in world.actions] == ["pgpanel_keep", "pgpanel_update", "pgpanel_rollback"] and sorted(world.asked) == [1, 2]
    assert "2 nodes not connected again after 0 minutes (a, b); asking the panel once more to connect to them" in text and "but 2 nodes that were connected before are not after (a, b); putting the old image back" in text


def test_the_owner_is_called_when_the_way_back_of_the_panel_fails(world, monkeypatch):
    world.add(1, "a")
    world.panel.dead_after = True
    world.panel.rollback_ok = False
    aid = _night(world, monkeypatch, [], [dict(PANEL)])
    lines, ok, done = story(aid)
    assert done and ok is False and [a[0] for a in world.actions] == ["pgpanel_keep", "pgpanel_update", "pgpanel_restart", "pgpanel_rollback"]
    assert "NEEDS YOU: the panel main: the panel does not answer after its update" in lines[-1] and "could NOT be put back" in lines[-1] and "/var/lib/nikatel-install/pgpanel-prev" in lines[-1]
    assert "Needs you: the panel main:" in world.tg[0] and [lvl for lvl, t in events() if t.startswith("Nightly PasarGuard update")] == ["bad"]


def test_a_keep_that_fails_changes_nothing_and_a_registry_without_a_newer_image_waits(world, monkeypatch):
    world.add(1, "a")
    world.panel.keep_ok = False
    lines, ok, _ = story(_night(world, monkeypatch, [], [dict(PANEL)]))
    assert ok is False and [a[0] for a in world.actions] == ["pgpanel_keep"] and "NEEDS YOU: the panel main: it could not be kept before the update (DUMP_FAILED postgres no space left) - nothing was changed" in lines[-1]
    world.actions.clear()
    world.panel.keep_ok, world.panel.same = True, True
    cfg.put("pg_auto", {"last": {}})
    lines, ok, _ = story(_night(world, monkeypatch, [], [dict(PANEL)]))
    assert ok is True and [a[0] for a in world.actions] == ["pgpanel_keep", "pgpanel_update"] and "left for the next night: the panel main: the registry has no newer image than the one that runs yet (GitHub says 5.6.0)" in lines[-1]


def test_a_panel_whose_server_is_not_in_the_list_is_for_the_owner_and_nothing_to_do_is_said(world, monkeypatch):
    pan = {**PANEL, "host": None, "skip": "its server is not in the Servers list: add it first (Servers → Add server), then the panel can be updated over SSH"}
    lines, ok, done = story(_night(world, monkeypatch, [], [pan]))
    assert done and ok is False and world.actions == [] and lines[-1] == "Done: NEEDS YOU: the panel main: its server is not in the Servers list: add it first (Servers → Add server), then the panel can be updated over SSH"
    assert "Needs you: the panel main: its server is not in the Servers list" in world.tg[0]
    world.tg.clear()
    cfg.put("pg_auto", {"last": {}})
    lines, ok, done = story(_night(world, monkeypatch, [], []))
    assert done and ok is True and lines[-1] == "Done: everything is up to date, nothing to do tonight" and cfg.get("pg_auto")["last"]["ok"] is True and len(world.tg) == 1


def test_the_rehearsal_walks_every_step_and_changes_nothing(world, monkeypatch):
    world.add(1, "a")
    world.cfgs = {1: ["c1"]}
    world.clock.t = at_local(14, 0)                                     # (in the afternoon: the window does not matter for a rehearsal)
    aid = _night(world, monkeypatch, [_row_of(1, "a")], [dict(PANEL)], dry=True)
    lines, ok, done = story(aid)
    text = "\n".join(lines)
    assert done and ok is True and world.actions == [] and world.tg == [] and cfg.get("pg_auto")["last"] == {} and world.nodes[1]["version"] == "0.5.6"
    assert "1/1 a (on S1, 1.0 Mbit/s now): would get software + Xray; 1 of 1 config that ride it is up now and would be checked after (c1)." in text
    assert "would keep the image that runs, the compose files and a dump of the postgres database" in text and lines[-1] == "Rehearsal done: would update a (software, Xray), the panel main (5.5.1 → 5.6.0)"


# ------------------------------------------------------------------ the switch and the clock
def test_nothing_runs_while_the_switch_is_off_or_it_is_not_the_hour_and_only_once_a_night(monkeypatch):
    monkeypatch.setattr(pg_auto, "get_settings", lambda: types.SimpleNamespace(read_only=False))
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn, a, k)) or 7)
    at = at_local(3, 2)
    assert pg_auto.state() == {"auto": False, "hour": 3, "budget_minutes": 60, "bad": {}, "last": {}} and pg_auto.due(at) is False and pg_auto.run(at) is None       # (off by default)
    cfg.put("pg_auto", {"auto": True})
    assert pg_auto.due(at_local(2, 59)) is False and pg_auto.due(at_local(4, 0)) is False and pg_auto.run(at_local(5, 0)) is None
    assert pg_auto.due(at) is True and pg_auto.run(at) == 7
    assert started == [(pg_safe.LABEL, pg_auto._night_story, (False,), {"action": "pgupdate", "source": "auto"})]
    assert cfg.get("pg_auto")["last"]["text"] == "running" and pg_auto.due(at) is False and pg_auto.run(at_local(3, 40)) is None       # (once a night: the day is marked first)
    cfg.put("pg_auto", {"last": {}})
    monkeypatch.setitem(autopilot._BUSY, "pg node updates", 3)
    assert pg_auto.run(at) is None and len(started) == 1                                                                  # (never over a manual update)
    autopilot._BUSY.pop("pg node updates")
    monkeypatch.setattr(pg_auto, "get_settings", lambda: types.SimpleNamespace(read_only=True))
    assert pg_auto.run(at) is None and len(started) == 1
    cfg.put("pg_auto", {"hour": 4})
    monkeypatch.setattr(pg_auto, "get_settings", lambda: types.SimpleNamespace(read_only=False))
    assert pg_auto.due(at) is False and pg_auto.due(at_local(4, 7)) is True                                                 # (the owner's hour)


def test_the_manual_safe_update_refuses_while_the_night_runs_and_the_night_refuses_while_a_button_runs(monkeypatch):
    monkeypatch.setitem(autopilot._BUSY, pg_safe.LABEL, 5)
    monkeypatch.setattr(pg_safe, "plan", lambda resolver=None, force=False, scope="all": {"can_start": True, "nothing": False, "problems": [], "nodes": [_row_of(1, "a")], "skipped": [], "panels": [], "steps": [], "note": "", "latest": {}})

    with pytest.raises(ValueError, match="running right now"):
        pg_safe.start()

    with pytest.raises(ValueError, match="running right now"):
        pg_auto.rehearse()


# ------------------------------------------------------------------ the routes, the card, the job, the settings
def test_the_switch_is_a_route_off_by_default_the_overview_carries_it_and_the_rehearsal_is_an_action(client, monkeypatch):
    monkeypatch.setattr(pg_update, "overview", lambda force=False, resolver=None: {"latest": {}, "panels": [], "nodes": [], "read_only": True, "checked_at": 0})
    r = client.get("/api/pg-updates")
    assert r.status_code == 200 and r.json()["auto"]["auto"] is False and r.json()["auto"]["hour"] == 3 and r.json()["auto"]["budget_minutes"] == 60
    assert client.get("/api/pg-updates/auto").json()["auto"] is False
    r = client.post("/api/pg-updates/auto", json={"enabled": True, "hour": 4})
    assert r.status_code == 200 and r.json()["auto"] is True and r.json()["hour"] == 4 and cfg.get("pg_auto")["auto"] is True
    assert client.post("/api/pg-updates/auto", json={"enabled": False}).json()["auto"] is False
    assert client.post("/api/pg-updates/auto", json={"enabled": True, "hour": 30}).status_code == 422
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: 42)
    r = client.post("/api/pg-updates/auto/rehearse")
    assert r.status_code == 202 and r.json() == {"ok": True, "action_id": 42}
    monkeypatch.setitem(autopilot._BUSY, pg_safe.LABEL, 1)
    assert client.post("/api/pg-updates/auto/rehearse").status_code == 400


def test_the_card_has_the_switch_under_the_two_buttons_the_job_is_registered_and_the_setting_is_off():
    js = (WEB / "pg_update.js").read_text(encoding="utf-8")
    assert "Update automatically every night at ${hh}:00 (Iran time)" in js and 'pgSw(!!au.auto, "pgUpdAuto(this.checked)"' in js and "return hero + go + autoRow;" in js and '<div class="eng-auto pgu-auto">' in js
    assert "window.pgUpdAuto = async on =>" in js and 'api("/api/pg-updates/auto", {method: "POST", body: JSON.stringify({enabled: !!on})})' in js
    assert "window.pgUpdRehearse = async () =>" in js and 'api("/api/pg-updates/auto/rehearse", {method: "POST"})' in js and "Rehearse tonight's run" in js and "Held back: " in js
    src = (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert 'jobs.register("pg-auto", 300, pg_auto.run)' in src
    assert cfg.DEFAULTS["pg_auto"] == {"auto": False, "hour": 3, "budget_minutes": 60, "bad": {}, "last": {}}
