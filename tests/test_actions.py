import json
import time

import pytest

from nikatel.services import actions
from nikatel.services.actions import _plan_steps
from nikatel.services.importer import import_seed_dir
from nikatel.services.tunnels import Plan, Side
from nikatel.ssh import Node, SSHError, render_action, run_action


def _plan():
    return Plan(["backhaul-iran-tcpmux-9443.service", "nikatel-gre-x.service"],
                ["backhaul-kharej-tcpmux-9443.service", "nikatel-gre-x.service"], iface="x", iran_peer="10.2.0.2", ports=[80])


def _side(units, iface="UNKNOWN", loss=0.0):
    s = Side()
    s.units = units
    s.iface_state = iface
    s.loss_pct = loss
    s.rtt_ms = 50.0 if loss < 100 else None
    return s


def test_a_stopped_service_is_restarted_without_the_grace_period():
    assert actions.grace_for("foreign: backhaul-kharej-tcpmux-9900.service is inactive", 180) == 0
    assert actions.grace_for("Iran: nikatel-gre-x.service is failed", 180) == 0
    assert actions.grace_for("the GRE interface x is missing on the Iran server", 180) == 0
    assert actions.grace_for("no answer from the foreign end over the GRE link", 180) == 180      # a cut link may be a blip: wait
    assert actions.grace_for("", 240) == 240


def test_plan_restarts_only_what_is_broken_first():
    iran = _side({"backhaul-iran-tcpmux-9443.service": "active", "nikatel-gre-x.service": "active"})
    foreign = _side({"backhaul-kharej-tcpmux-9443.service": "failed", "nikatel-gre-x.service": "active"})
    steps = _plan_steps(_plan(), iran, foreign)
    assert steps[0]["side"] == "foreign" and steps[0]["units"] == ["backhaul-kharej-tcpmux-9443.service"]
    # last resort steps come after, Iran before foreign
    sides = [(s["side"], s["units"]) for s in steps]
    assert ("iran", ["backhaul-iran-tcpmux-9443.service"]) in sides


def test_plan_gre_problem_restarts_gre_both_sides():
    iran = _side({"backhaul-iran-tcpmux-9443.service": "active", "nikatel-gre-x.service": "active"}, iface="DOWN", loss=100.0)
    foreign = _side({"backhaul-kharej-tcpmux-9443.service": "active", "nikatel-gre-x.service": "active"})
    steps = _plan_steps(_plan(), iran, foreign)
    assert steps[0]["units"] == ["nikatel-gre-x.service"] and steps[0]["side"] == "iran"
    assert steps[1]["units"] == ["nikatel-gre-x.service"] and steps[1]["side"] == "foreign"


def test_actions_refused_in_read_only_and_validated():
    with pytest.raises(SSHError):
        run_action(Node("127.0.0.1"), "restart_units", {"UNITS": "a.service"})

    with pytest.raises(ValueError):
        render_action("restart_units", {"UNITS": "a.service; reboot"})

    with pytest.raises(ValueError):
        render_action("nope", {})

    assert "systemctl restart" in render_action("restart_units", {"UNITS": "a.service b.service"})


def _seed(tmp_path):
    (tmp_path / "iran-servers.json").write_text(json.dumps([{"name": "ir1", "host": "1.1.1.1"}]))
    (tmp_path / "foreign-servers.json").write_text(json.dumps([{"name": "fi1", "host": "2.2.2.2"}]))
    (tmp_path / "tunnels.json").write_text(json.dumps({"tunnels": [{"name": "t1", "type": "backhaul", "transport": "tcpmux", "port": 9443,
                                                                    "iran_host": "1.1.1.1", "foreign_host": "2.2.2.2", "foreign_tunnel_ip": "10.2.0.2"}]}))
    import_seed_dir(tmp_path)


def test_dry_run_through_api_writes_a_story(client, tmp_path, monkeypatch):
    _seed(tmp_path)
    tid = client.get("/api/tunnels").json()[0]["id"]

    # no real ssh: both sides answer with a broken foreign unit
    def fake_query(node, plan, is_iran):
        if is_iran:
            return _side({u: "active" for u in plan.iran_units})
        return _side({plan.foreign_units[0]: "inactive", plan.foreign_units[1]: "active"})

    monkeypatch.setattr(actions, "_query", fake_query)
    r = client.post(f"/api/tunnels/{tid}/fix?dry_run=true")
    assert r.status_code == 200
    aid = r.json()["id"]

    for _ in range(50):
        s = client.get(f"/api/actions/{aid}").json()
        if s["done"]:
            break
        time.sleep(0.1)

    assert s["done"] and s["ok"] is True and s["dry_run"] is True
    text = "\n".join(s["story"])
    assert "Found:" in text and "Plan:" in text and "[dry run]" in text and "systemctl restart" in text and "nothing was changed" in text
    assert client.get("/api/actions").json()["actions"][0]["id"] == aid


def test_real_fix_in_read_only_turns_into_dry_run(client, tmp_path, monkeypatch):
    _seed(tmp_path)
    tid = client.get("/api/tunnels").json()[0]["id"]
    monkeypatch.setattr(actions, "_query", lambda node, plan, is_iran: _side({u: "inactive" for u in (plan.iran_units if is_iran else plan.foreign_units)}))
    aid = client.post(f"/api/tunnels/{tid}/fix").json()["id"]

    for _ in range(50):
        s = client.get(f"/api/actions/{aid}").json()
        if s["done"]:
            break
        time.sleep(0.1)

    assert s["done"] and "read-only" in s["story"][1] and "nothing was changed" in s["story"][-1]


def test_auto_repair_setting_needs_read_write(client):
    assert client.put("/api/settings/repair", json={"auto": True, "after_seconds": 180}).status_code == 400
    assert client.put("/api/settings/repair", json={"auto": False, "after_seconds": 300}).json()["after_seconds"] == 300
    assert client.get("/api/settings/repair").json()["read_only"] is True


def test_fix_budget_counts_only_real_attempts(client, tmp_path, monkeypatch):
    from nikatel.config import get_settings
    from nikatel.db import session
    from nikatel.models import ActionLog

    _seed(tmp_path)
    tid = client.get("/api/tunnels").json()[0]["id"]
    monkeypatch.setattr(get_settings(), "read_only", False)
    now = int(time.time())

    with session() as db:                                    # two real restarts in the last hour, plus refusals that must not count
        for a in ("fix", "fix", "fix-skip", "fix-skip", "fix-skip"):
            db.add(ActionLog(ref="t1", action=a, source="auto", dry_run=False, ts=now - 600, story=["x"]))

    assert actions.recent_fixes("t1") == 2
    aid = actions.fix(tid, source="auto")

    for _ in range(50):
        s = actions.status(aid)
        if s["done"]:
            break
        time.sleep(0.1)

    assert s["done"] and s["ok"] is False and "already repaired 2 times" in s["story"][-1] and s["action"] == "fix-skip"
    assert actions.recent_fixes("t1") == 2                   # the refusal did not eat the budget
