"""Updates of PasarGuard from Settings (roadmap #103): versions compared by number, the newest releases kept for an hour, the overview of panels and nodes, the nodes updated one at a time (a node
that does not come back stops the run), and the panel itself updated on its own server with the official command. A fake PasarGuard panel and a fake server stand in; nothing reaches GitHub,
a real panel or a real machine."""
import types

import pytest

from nikatel import ssh
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, Panel, Server
from nikatel.services import autopilot, extras, panels, pg_nodes, pg_update
from nikatel.services.panels import PanelError

TAGS = {"panel": {"version": "v5.4.1", "page": "p", "published": "2026-10-01"}, "node": {"version": "v0.5.4", "page": "n", "published": "2026-09-30"}, "xray": {"version": "v26.3.27", "page": "x", "published": "2026-09-01"}}


class FakePanel:
    """GET /api/system, /api/nodes, /api/node/{id}; the node update routes bump the version the way a node that updates itself does (or, for `stuck`, do nothing)."""

    def __init__(self):
        self.version = "5.4.0"
        self.nodes: dict[int, dict] = {}
        self.calls: list[tuple] = []
        self.stuck: set[int] = set()
        self.fail: set[str] = set()
        self.next = 1

    def add(self, name, address, version="0.5.3", xray="26.3.20", status="connected") -> int:
        i, self.next = self.next, self.next + 1
        self.nodes[i] = {"id": i, "name": name, "address": address, "port": 62050, "api_port": 62051, "usage_coefficient": 1.0, "connection_type": "grpc", "keep_alive": 60, "core_config_id": 1, "data_limit": 0,
                         "data_limit_reset_strategy": "no_reset", "reset_time": -1, "default_timeout": 10, "internal_timeout": 15, "xray_version": xray, "node_version": version, "status": status, "message": "",
                         "uplink": 0, "downlink": 0, "lifetime_uplink": 0, "lifetime_downlink": 0}
        return i

    def __call__(self, c, method, path, **kw):
        self.calls.append((method, path, kw.get("body")))
        base = path.split("?")[0]

        if base in self.fail:
            raise PanelError("HTTP 500: broken by the fake", 500)

        if method == "GET" and base == "/api/system":
            return {"version": self.version, "total_user": 7, "online_users": 1}

        if method == "GET" and base == "/api/nodes":
            return {"nodes": [dict(n) for n in self.nodes.values()]}

        parts = [p for p in base.split("/") if p][1:]

        if parts[:1] == ["node"] and len(parts) >= 2 and parts[1].isdigit():
            nid, rest = int(parts[1]), parts[2:]
            node = self.nodes.get(nid)

            if node is None:
                raise PanelError("HTTP 404: Node not found", 404)

            if method == "GET" and not rest:
                return dict(node)

            if method == "POST" and rest == ["update"]:
                if nid not in self.stuck:
                    node["node_version"] = "0.5.4"

                return {}

            if method == "POST" and rest == ["core_update"]:
                if nid not in self.stuck:
                    node["xray_version"] = kw["body"]["core_version"]

                return {}

        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self):
        return [x for x in self.calls if x[0] != "GET"]


@pytest.fixture
def world(monkeypatch):
    fake = FakePanel()
    monkeypatch.setattr(panels, "call", fake)
    monkeypatch.setattr(pg_update, "_fetch", lambda repo: dict(TAGS[{"PasarGuard/panel": "panel", "PasarGuard/node": "node", "XTLS/Xray-core": "xray"}[repo]]))
    monkeypatch.setattr(pg_nodes, "resolve_names", lambda names, timeout=3.0: {})
    monkeypatch.setattr(pg_update, "POLL", 0)
    monkeypatch.setattr(pg_nodes, "POLL", 0)
    monkeypatch.setattr(pg_update.time, "sleep", lambda s: None)
    monkeypatch.setattr(get_settings(), "read_only", False)
    pg_update._LATEST.clear()
    return types.SimpleNamespace(pg=fake)


def add_server(name, host, role="foreign", online=True, enabled=True):
    with session() as db:
        s = Server(name=name, host=host, role=role, online=online, enabled=enabled)
        db.add(s)
        db.flush()
        return s.id


def add_panel(name="alpha", url="https://203.0.113.5:8443", version="5.4.0", enabled=True, typ="pasarguard"):
    with session() as db:
        p = Panel(name=name, type=typ, url=url, username="admin", secrets={"password": "x"}, status="up", version=version, enabled=enabled)
        db.add(p)
        db.flush()
        return p.id


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


# ------------------------------------------------------------------ pure: versions
def test_versions_are_compared_by_number_not_by_text():
    assert pg_update.ver("v5.4.1") == (5, 4, 1) and pg_update.ver("26.3.27") == (26, 3, 27) and pg_update.ver("v0.5") == (0, 5, 0) and pg_update.ver("v1.2.3-rc1") == (1, 2, 3)
    assert pg_update.ver("") is None and pg_update.ver(None) is None and pg_update.ver("latest") is None
    assert pg_update.newer("v5.4.10", "5.4.9") is True and pg_update.newer("v5.4.1", "5.4.1") is False and pg_update.newer("26.10.1", "26.3.27") is True and pg_update.newer("v0.5.4", "v0.6.0") is False
    assert pg_update.newer("v5.4.1", "") is None and pg_update.newer("", "5.4.0") is None          # a version that is not known claims nothing


def test_the_newest_versions_are_kept_for_an_hour_and_the_last_answer_survives_a_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(pg_update, "_fetch", lambda repo: calls.append(repo) or {"version": "v1.0.0", "page": "", "published": ""})
    pg_update._LATEST.clear()
    pg_update.latest_tags()
    pg_update.latest_tags()
    assert len(calls) == 3                                                                                        # three repositories, once
    pg_update.latest_tags(force=True)
    assert len(calls) == 6

    def broken(repo):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(pg_update, "_fetch", broken)
    assert pg_update.latest_tags(force=True)["panel"]["version"] == "v1.0.0"                                       # GitHub does not answer: the last answer is shown
    pg_update._LATEST.clear()
    assert pg_update.latest_tags()["panel"] == {"error": "rate limited"}


# ------------------------------------------------------------------ the overview
def test_the_overview_marks_what_is_behind_and_finds_the_server_that_runs_the_panel(world):
    host = add_server("pg-host", "203.0.113.5")
    other = add_server("fr2", "203.0.113.20")
    pid = add_panel()
    add_panel("marz", "https://marz.example.test", "0.8.4", typ="marzban")                                          # a Marzban panel is not part of this
    world.pg.add("n-behind", "203.0.113.20")
    world.pg.add("n-new", "198.51.100.9", version="0.5.4", xray="26.3.27")
    world.pg.add("n-down", "198.51.100.10", version="", xray="", status="error")
    j = pg_update.overview()
    assert [p["name"] for p in j["panels"]] == ["alpha"] and j["panels"][0]["host"]["id"] == host and j["panels"][0]["behind"] is True and j["panels"][0]["latest"] == "v5.4.1"
    nodes = {n["name"]: n for n in j["nodes"]}
    assert nodes["n-behind"]["behind"] is True and nodes["n-behind"]["xray_behind"] is True and nodes["n-behind"]["server"] == {"id": other, "name": "fr2"}
    assert nodes["n-new"]["behind"] is False and nodes["n-new"]["xray_behind"] is False and nodes["n-new"]["server"] is None
    assert nodes["n-down"]["behind"] is None and nodes["n-down"]["xray_behind"] is None                            # never connected: no version, no claim
    assert j["latest"]["node"]["version"] == "v0.5.4" and j["read_only"] is False and pid == j["panels"][0]["id"]
    assert "api_key" not in str(j)


def test_a_panel_that_does_not_answer_is_listed_with_its_reason_and_the_others_still_are(world):
    add_panel("alpha", "https://a.example.test")
    world.pg.add("n1", "203.0.113.20")
    add_panel("beta", "https://b.example.test", enabled=False)
    world.pg.fail = {"/api/nodes"}
    j = pg_update.overview()
    assert [p["name"] for p in j["panels"]] == ["alpha", "beta"] and "500" in j["panels"][0]["error"] and "switched off" in j["panels"][1]["error"] and j["nodes"] == []


def test_the_overview_works_when_github_does_not_answer(world, monkeypatch):
    add_panel()

    def broken(repo):
        raise RuntimeError("no route to github")

    monkeypatch.setattr(pg_update, "_fetch", broken)
    j = pg_update.overview(force=True)
    assert j["latest"]["panel"] == {"error": "no route to github"} and j["panels"][0]["behind"] is None and j["panels"][0]["latest"] == ""


# ------------------------------------------------------------------ the nodes, one at a time
def test_a_rolling_update_goes_one_node_at_a_time_and_stops_at_the_first_that_does_not_come_back(world, monkeypatch):
    monkeypatch.setattr(pg_update, "NODE_LIMIT", 0)
    pid = add_panel()
    a, b, c3 = world.pg.add("a", "203.0.113.31"), world.pg.add("b", "203.0.113.32"), world.pg.add("c", "203.0.113.33")
    world.pg.stuck.add(b)
    aid = new_action()
    pg_update._nodes_story(aid, [{"panel_id": pid, "node_id": n, "what": "node"} for n in (a, b, c3)])
    lines, ok, done = story(aid)
    assert done and ok is False
    assert world.pg.nodes[a]["node_version"] == "0.5.4"                                                           # a: updated, connected again
    assert [x for x in world.pg.writes() if x[1].endswith("/update")] == [("POST", f"/api/node/{a}/update", {}), ("POST", f"/api/node/{b}/update", {})]      # c was never asked
    text = "\n".join(lines)
    assert "1/3 a" in text and "connected again" in text and "Stopped at 2/3" in text and "The nodes after it were not touched" in text and "3/3" not in text


def test_a_rolling_update_skips_what_is_the_newest_and_refuses_a_node_that_is_not_connected(world, monkeypatch):
    monkeypatch.setattr(pg_update, "NODE_LIMIT", 0)
    pid = add_panel()
    new = world.pg.add("new", "203.0.113.31", version="0.5.4")
    down = world.pg.add("down", "203.0.113.32", status="error")
    aid = new_action()
    pg_update._nodes_story(aid, [{"panel_id": pid, "node_id": new, "what": "node"}])
    lines, ok, _ = story(aid)
    assert ok is True and "the newest already - skipped" in "\n".join(lines) and world.pg.writes() == []
    aid = new_action()
    pg_update._nodes_story(aid, [{"panel_id": pid, "node_id": down, "what": "node"}])
    lines, ok, _ = story(aid)
    assert ok is False and "error now" in "\n".join(lines) and world.pg.writes() == []                            # nothing was asked of a node that does not answer


def test_the_xray_of_a_node_is_updated_to_the_newest_release(world, monkeypatch):
    monkeypatch.setattr(pg_update, "NODE_LIMIT", 0)
    pid = add_panel()
    n = world.pg.add("n", "203.0.113.31", version="0.5.4", xray="26.3.20")
    aid = new_action()
    pg_update._nodes_story(aid, [{"panel_id": pid, "node_id": n, "what": "core"}])
    lines, ok, _ = story(aid)
    assert ok is True and world.pg.nodes[n]["xray_version"] == "26.3.27" and ("POST", f"/api/node/{n}/core_update", {"core_version": "26.3.27"}) in world.pg.writes() and "Xray 26.3.27" in lines[-2]


def test_the_nodes_update_is_checked_before_it_starts(world, client):
    pid = add_panel()
    n = world.pg.add("n", "203.0.113.31")
    assert client.post("/api/pg-updates/nodes", json={"items": []}).status_code == 422
    assert client.post("/api/pg-updates/nodes", json={"items": [{"panel_id": pid, "node_id": n, "what": "geo"}]}).status_code == 422
    assert client.post("/api/pg-updates/nodes", json={"items": [{"panel_id": pid, "node_id": 99}]}).status_code == 400           # a node the panel does not have
    assert client.post("/api/pg-updates/nodes", json={"items": [{"panel_id": 99, "node_id": n}]}).status_code == 400
    assert client.post("/api/pg-updates/nodes", json={"items": [{"panel_id": pid, "node_id": i} for i in range(1, 42)]}).status_code == 422
    assert pg_update._clean_items([{"panel_id": 1, "node_id": 2, "what": "node"}, {"panel_id": 1, "node_id": 2, "what": "node"}, {"panel_id": 1, "node_id": 2, "what": "core"}]) == [
        {"panel_id": 1, "node_id": 2, "what": "node"}, {"panel_id": 1, "node_id": 2, "what": "core"}]                           # the same twice is once
    assert world.pg.writes() == []


# ------------------------------------------------------------------ the panel itself
def test_the_plan_of_a_panel_update_says_what_stands_in_the_way(world, monkeypatch):
    pid = add_panel()
    assert "not in the Servers list" in pg_update.panel_plan(pid)["problems"][0]
    host = add_server("pg-host", "203.0.113.5")
    ok = pg_update.panel_plan(pid)
    assert ok["ok"] is True and ok["host"] == {"id": host, "name": "pg-host"} and ok["latest"] == "v5.4.1" and len(ok["steps"]) == 4
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert "read-only" in pg_update.panel_plan(pid)["problems"][0]
    monkeypatch.setattr(get_settings(), "read_only", False)
    with session() as db:
        db.get(Server, host).online = False
    assert "does not answer" in pg_update.panel_plan(pid)["problems"][0]
    with session() as db:
        s = db.get(Server, host)
        s.online, s.enabled = True, False
    assert "switched off" in pg_update.panel_plan(pid)["problems"][0]
    with session() as db:
        db.get(Server, host).enabled = True
        db.get(Panel, pid).version = "5.4.1"
    assert "newest" in pg_update.panel_plan(pid)["problems"][0]
    with pytest.raises(ValueError, match="no such"):
        pg_update.panel_plan(9999)


def test_a_dns_name_in_the_url_finds_the_server_that_has_the_address(world, monkeypatch):
    add_server("pg-host", "203.0.113.5")
    monkeypatch.setattr(pg_nodes, "resolve_names", lambda names, timeout=3.0: {"panel.example.test": ["203.0.113.5"]})
    assert pg_update.host_server("https://panel.example.test:8000")["name"] == "pg-host"
    assert pg_update.host_server("https://elsewhere.example.test") is None and pg_update.host_server("") is None


def test_the_panel_update_needs_the_name_typed_back_and_starts_nothing_otherwise(world, client):
    pid = add_panel()
    add_server("pg-host", "203.0.113.5")
    started = []
    r = client.post(f"/api/pg-updates/panels/{pid}", json={"backup": True, "typed": "wrong"})
    assert r.status_code == 400 and "type the name of the panel (alpha)" in r.text
    j = client.get(f"/api/pg-updates/panels/{pid}/plan").json()
    assert j["ok"] is True and j["panel"]["name"] == "alpha" and not started and world.pg.writes() == []
    assert client.get("/api/pg-updates/panels/9999/plan").status_code == 400


def test_the_story_of_a_panel_update_compares_the_panel_before_and_after(world, monkeypatch):
    pid, sid = add_panel(), add_server("pg-host", "203.0.113.5")
    world.pg.add("n1", "203.0.113.20")
    asked = []
    monkeypatch.setattr(pg_update, "run_script", lambda node, name, params=None, timeout=40: "CLI yes\nBACKUP yes\nDIR /opt/pasarguard\nDOCKER 3\nDF 9999\n")

    def run_item(aid, node, ip, item, cert, resume=False):
        asked.append((item, cert))
        world.pg.version = "5.4.1"                                                                                  # the panel restarted with the new version
        autopilot._say(aid, "PasarGuard panel update: starting")
        return True

    monkeypatch.setattr(extras, "_run_item", run_item)
    aid = new_action()
    pg_update._panel_story(aid, pid, sid, True)
    lines, ok, done = story(aid)
    text = "\n".join(lines)
    assert done and ok is True and asked == [("pasarguard_panel", "1")]
    assert "pasarguard command found; 3 PasarGuard container(s) running; backup command: yes" in text and "version 5.4.0 → 5.4.1" in text and "users 7 → 7" in text and "nodes connected 1 of 1 → 1 of 1" in text
    aid = new_action()
    pg_update._panel_story(aid, pid, sid, False)
    assert asked[-1] == ("pasarguard_panel", "0") and "no backup was asked for" in "\n".join(story(aid)[0])


def test_the_story_of_a_panel_update_stops_where_it_goes_wrong_and_says_what_is_left(world, monkeypatch):
    pid, sid = add_panel(), add_server("pg-host", "203.0.113.5")
    calls = []
    monkeypatch.setattr(pg_update, "run_script", lambda node, name, params=None, timeout=40: "CLI no\nBACKUP no\nDIR none\nDOCKER 0\nDF 1\n")
    monkeypatch.setattr(extras, "_run_item", lambda *a, **k: calls.append(a) or True)
    aid = new_action()
    pg_update._panel_story(aid, pid, sid, True)
    lines, ok, _ = story(aid)
    assert ok is False and "official script" in lines[-1] and "Nothing was changed" in lines[-1] and not calls                       # no pasarguard command: nothing was started

    def refuse(node, name, params=None, timeout=40):
        raise ssh.SSHError("connection refused")

    monkeypatch.setattr(pg_update, "run_script", refuse)
    aid = new_action()
    pg_update._panel_story(aid, pid, sid, True)
    assert story(aid)[1] is False and "Stopped at step 1" in story(aid)[0][-1]
    monkeypatch.setattr(pg_update, "run_script", lambda node, name, params=None, timeout=40: "CLI yes\nBACKUP yes\n")
    monkeypatch.setattr(extras, "_run_item", lambda *a, **k: False)                                                  # the update (or its backup) failed on the server
    aid = new_action()
    pg_update._panel_story(aid, pid, sid, True)
    lines, ok, _ = story(aid)
    assert ok is False and "Stopped at step 2" in lines[-1] and "pasarguard logs" in lines[-1]
    world.pg.fail = {"/api/system"}
    monkeypatch.setattr(extras, "_run_item", lambda *a, **k: True)
    monkeypatch.setattr(pg_update, "PANEL_LIMIT", 0)
    aid = new_action()
    pg_update._panel_story(aid, pid, sid, True)
    lines, ok, _ = story(aid)
    assert ok is False and "Stopped at step 3" in lines[-1] and "does not answer" in lines[-1]                           # the panel did not come back


# ------------------------------------------------------------------ the reviewed scripts
def test_the_update_script_is_what_the_owner_would_type_and_nothing_else():
    s = ssh.render_action("install_pgpanel_update", {"BACKUP": "1"})
    assert "pasarguard backup" in s and "pasarguard update" in s and s.count("timeout ") == 2 and s.count("< /dev/null") >= 2 and "NO_CLI" in s and "NOT_ROOT" in s
    assert "BACKUP_FAILED" in s and s.index("pasarguard backup") < s.index("pasarguard update")                    # a backup that fails stops before the update
    assert 'if [ "1" = "1" ]' in s and 'if [ "0" = "1" ]' in ssh.render_action("install_pgpanel_update", {"BACKUP": "0"})
    for bad in ("2", "", "1; reboot", "yes"):
        with pytest.raises(ValueError):
            ssh.render_action("install_pgpanel_update", {"BACKUP": bad})

    for word in ("reboot", "shutdown", "rm -rf", "docker system prune", "uninstall", "down "):
        assert word not in s


def test_the_look_at_the_panel_server_changes_nothing_and_the_status_script_knows_the_update():
    import re

    script = ssh.SCRIPTS["pgpanel_info"][0]
    assert "CLI" in script and "BACKUP" in script and "pasarguard update" not in script and "docker compose" not in script and "rm " not in script
    assert re.match(ssh.SCRIPTS["install_status"][1]["NAME"], "pgpanel") and not re.match(ssh.SCRIPTS["install_status"][1]["NAME"], "pgpanel2")
    assert "pasarguard_panel" not in extras.ITEMS and extras.KEY["pasarguard_panel"] == "pgpanel" and extras.ACTION["pasarguard_panel"] == "install_pgpanel_update"       # it is not an install of the Install view


def test_the_overview_route_answers_and_needs_a_sign_in(world, client):
    from fastapi.testclient import TestClient

    add_panel()
    world.pg.add("n1", "203.0.113.20")
    j = client.get("/api/pg-updates").json()
    assert j["ok"] is True and j["panels"][0]["name"] == "alpha" and j["nodes"][0]["name"] == "n1" and j["latest"]["xray"]["version"] == "v26.3.27"
    assert TestClient(client.app).get("/api/pg-updates").status_code == 401
