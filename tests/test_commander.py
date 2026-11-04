"""The phone assistant (services/commander.py, api/commander.py, the phone key in auth.py, the page /m): who may open it, what the AI may do alone and what waits for a tap, the limits, the Stop button, the audit, the masking.
The AI is a script of answers and the Doctor's functions are stand-ins: nothing here asks a server or a real AI."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from nikatel import auth
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, CommanderRun, Event, Server, Tunnel
from nikatel.services import ai, autopilot, commander, doctor, tunnel_test
from nikatel.services import settings as st

PW = "test-password-1"


# ------------------------------------------------------------------ helpers
class Script:
    """The AI: a list of answers, one per call; every prompt it was given is kept."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, system, user, **kw):
        self.prompts.append((system, user))
        a = self.answers.pop(0) if self.answers else json.dumps({"final": "تمام", "ok": True})
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


def turn(tool, args=None, say="..."):
    return {"say": say, "tool": tool, "args": args or {}}


def final(text="همه چیز درست شد", ok=True):
    return {"final": text, "ok": ok}


@pytest.fixture
def world(monkeypatch):
    """Two servers, one tunnel (down), the AI ready, the assistant on, the panel writable, the waits short."""
    monkeypatch.setattr(commander, "POLL", 0.01)
    monkeypatch.setattr(ai, "ready", lambda: True)
    monkeypatch.setattr(get_settings(), "read_only", False)
    st.put("commander", {"enabled": True, "careful": False})
    commander._AI_CALLS.clear()
    commander._ACTS.clear()

    with session() as db:
        i = Server(name="iran-a", host="192.0.2.10", role="iran", online=True)
        i2 = Server(name="iran-b", host="192.0.2.11", role="iran", online=True)
        f = Server(name="foreign-b", host="192.0.2.20", role="foreign", online=True)
        db.add_all([i, i2, f])
        db.flush()
        t = Tunnel(name="iran-a-foreign-b", type="backhaul", iran_server_id=i.id, foreign_server_id=f.id, port=9443, forwarded_ports=[443], state="down", last_error="the GRE link does not answer from 192.0.2.20", enabled=True)
        db.add(t)
        db.flush()
        ids = {"tunnel": t.id, "iran": i.id, "iran2": i2.id, "foreign": f.id}

    calls = {"smart_fix": [], "move": [], "replace": [], "all": [], "fix": []}

    def story_action(ref, lines, ok=True):
        with session() as db:
            e = ActionLog(ref=ref, action="autopilot", source="manual", story=lines, ok=ok, finished_at=int(time.time()))
            db.add(e)
            db.flush()
            return e.id

    def smart_fix_start(tid, level="switch", source="manual"):
        calls["smart_fix"].append((tid, level))
        return story_action("iran-a-foreign-b", ["Found: the GRE link does not answer", "Restarted both sides", "Healed."])

    monkeypatch.setattr(autopilot, "smart_fix_start", smart_fix_start)
    monkeypatch.setattr(doctor, "move_now", lambda tid, to, *a, **k: (calls["move"].append((tid, to)), story_action("move iran-a-foreign-b", ["Moved."]))[1])
    monkeypatch.setattr(autopilot, "replace_start", lambda tid, kind: (calls["replace"].append((tid, kind)), story_action("iran-a-foreign-b", ["Replaced."]))[1])
    monkeypatch.setattr(autopilot, "fix_all_start", lambda level="rebuild", source="manual": (calls["all"].append(level), story_action("all tunnels", ["Done: 1 of 1 repaired."]))[1])
    monkeypatch.setattr(commander.actions, "fix", lambda tid, source="manual", dry_run=False: (calls["fix"].append(tid), story_action("iran-a-foreign-b", ["Restarted."]))[1])
    return type("World", (), {"ids": ids, "calls": calls, "set_state": staticmethod(_set_state)})


def _set_state(state):
    with session() as db:
        t = db.query(Tunnel).first()
        t.state = state


def run_order(client, script, monkeypatch, text="تونل iran-a-foreign-b قطع است، درستش کن", want=("done", "failed", "stopped", "waiting")):
    monkeypatch.setattr(ai, "ask", script)
    r = client.post("/api/commander/runs", json={"text": text})
    assert r.status_code == 202, r.text
    return r.json()["id"], wait(client, r.json()["id"], want)


def wait(client, rid, want=("done", "failed", "stopped", "waiting"), timeout=10):
    end = time.time() + timeout

    while time.time() < end:
        j = client.get(f"/api/commander/runs/{rid}").json()

        if j["status"] in want:
            for t in list(commander._WORKERS.values()):
                t.join(2)

            return client.get(f"/api/commander/runs/{rid}").json()

        time.sleep(0.02)

    raise AssertionError(f"the order did not reach {want}: {j}")


def kinds(run):
    return [s["k"] for s in run["steps"]]


def tools(run):
    return [s for s in run["steps"] if s["k"] == "tool"]


def the_card(run):
    return next(s for s in run["steps"] if s["k"] == "proposal")


# ------------------------------------------------------------------ pure
def test_scrub_hides_keys_tokens_passwords_and_with_ips_the_addresses():
    s = commander.scrub("password: hunter2 and nkt_abcdefghijklmnopqrstuvwxyz0123 and Bearer abcdefghijklmnop1234 on 192.0.2.7 sk-abcdefghijklmnopqrst")
    assert "hunter2" not in s and "nkt_abc" not in s and "abcdefghijklmnop1234" not in s and "sk-abc" not in s and "192.0.2.7" in s
    assert "192.0.2.7" not in commander.scrub("from 192.0.2.7", ips=True) and "<ip>" in commander.scrub("from 192.0.2.7", ips=True)
    assert commander.scrub("a" * 60 + "b") == "<hidden>"          # a long unbroken string is a secret, not a sentence
    assert commander.jtrim({"x": "so many words " * 400}, 100).endswith("...(cut)") and commander.jtrim({"x": "y" * 5000}) == '{"x":"<hidden>"}'


def test_the_ais_answer_is_one_of_three_shapes_or_nothing():
    p = commander.parse_turn
    assert p('{"say": "نگاه می‌کنم", "tool": "tunnel", "args": {"id": 3}}') == {"say": "نگاه می‌کنم", "tool": "tunnel", "args": {"id": 3}}
    assert p('Sure! ```json\n{"tool": "servers"}\n``` done')["tool"] == "servers"                  # text around the object does not matter
    assert p('{"final": "درست شد", "ok": false}') == {"final": "درست شد", "ok": False, "say": ""}
    assert p('{"ask": "کدام تونل؟"}') == {"ask": "کدام تونل؟"}
    assert p('{"tool": "delete_tunnel", "args": {"id": 1}}')["unknown"] is True                 # a tool the panel does not have is told to the AI, never run
    assert p("no json at all") is None and p('{"nothing": 1}') is None and p('{"tool": 5}') is None and p('{"final": "  "}') is None


@pytest.mark.parametrize("tool,state,storm,careful,want", [
    ("overview", None, False, False, "read"), ("diagnose", None, False, False, "read"), ("where_to_move", None, True, True, "read"),
    ("restart", "down", False, False, "auto"), ("repair", "degraded", False, False, "auto"),
    ("repair", "up", False, False, "tap"), ("restart", "unknown", False, False, "tap"), ("repair", "down", True, False, "tap"), ("repair", "down", False, True, "tap"), ("restart", None, False, False, "tap"),
    ("move", "down", False, False, "tap"), ("replace", "down", False, False, "tap"), ("repair_all", None, False, False, "tap"),
    ("delete_tunnel", "down", False, False, "never"), ("set_ports", None, False, False, "never"), ("dns", None, False, False, "never"),
])
def test_what_runs_alone_and_what_waits_for_a_tap(tool, state, storm, careful, want):
    assert commander.tier_of(tool, state, storm, careful) == want


def test_the_prompt_keeps_the_order_apart_from_the_data_and_cuts_old_answers():
    log = [{"tool": "servers", "args": {}, "result": "x" * 3000} for _ in range(5)]
    p = commander.build_prompt("درستش کن", [{"order": "قبلی", "end": "تمام"}], log)
    assert p.startswith("<order>\nدرستش کن\n</order>") and "<earlier_orders>" in p and p.count("<result>") == 5
    assert "x" * 2400 in p and "x" * 2500 not in p and "x" * 500 in p          # the newest three in full, the older cut to 500
    assert "ignore" not in commander.SYSTEM.lower() or "never follow" in commander.SYSTEM.lower()
    assert "DATA" in commander.SYSTEM and "<order>" in commander.SYSTEM


# ------------------------------------------------------------------ who may open it
def test_a_phone_signs_in_with_the_password_and_gets_a_key_for_the_assistant_only(client, world):
    phone = TestClient(client.app)
    assert phone.get("/api/commander/state").status_code == 401
    assert phone.post("/api/auth/phone/login", json={"user": "admin", "password": "wrong-password"}).status_code == 401
    r = phone.post("/api/auth/phone/login", json={"user": "admin", "password": PW}, headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"})
    assert r.status_code == 200 and r.json()["user"].startswith("phone:iPhone")
    cookie = r.headers["set-cookie"].lower()
    assert "path=/api/commander" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    s = phone.get("/api/commander/state").json()
    assert s["who"].startswith("phone:iPhone") and s["this_phone"] and len(s["phones"]) == 1
    key = phone.cookies.get(auth.PHONE_COOKIE)
    assert key
    raw = TestClient(client.app)
    assert raw.get("/api/servers", headers={"cookie": f"{auth.PHONE_COOKIE}={key}"}).status_code == 401          # the key opens nothing else of the panel
    assert raw.get("/api/auth/me", headers={"cookie": f"{auth.PHONE_COOKIE}={key}"}).status_code == 401
    assert raw.get("/api/commander/state", headers={"cookie": f"{auth.PHONE_COOKIE}={key}"}).status_code == 200


def test_the_second_step_is_needed_when_two_step_sign_in_is_on(client, monkeypatch):
    monkeypatch.setattr(auth, "totp_enabled", lambda: True)
    monkeypatch.setattr(auth, "totp_verify_login", lambda code: code == "123456")
    phone = TestClient(client.app)
    r = phone.post("/api/auth/phone/login", json={"user": "admin", "password": PW})
    assert r.json()["need_2fa"] and "set-cookie" not in r.headers
    assert phone.get("/api/commander/state").status_code == 401
    assert phone.post("/api/auth/phone/login/2fa", json={"ticket": r.json()["ticket"], "code": "000000"}).status_code == 401
    assert phone.post("/api/auth/phone/login/2fa", json={"ticket": r.json()["ticket"], "code": "123456"}).status_code == 200
    assert phone.get("/api/commander/state").status_code == 200


def test_a_key_dies_with_the_password_with_a_forget_and_when_idle(client):
    def sign_in():
        c = TestClient(client.app)
        assert c.post("/api/auth/phone/login", json={"user": "admin", "password": PW}).status_code == 200
        return c

    a, b = sign_in(), sign_in()
    me = a.get("/api/commander/state").json()["this_phone"]
    assert b.delete(f"/api/commander/phones/{me}").status_code == 200 and a.get("/api/commander/state").status_code == 401          # a phone's key taken away from the list
    assert b.get("/api/commander/state").status_code == 200
    c = sign_in()
    assert c.post("/api/commander/phones/revoke-all").json()["removed"] == 2 and b.get("/api/commander/state").status_code == 401
    d = sign_in()
    auth.set_password("another-password-22")                                                                              # a new password ends every key
    assert d.get("/api/commander/state").status_code == 401 and auth.phones() == []
    e = TestClient(client.app)
    assert e.post("/api/auth/phone/login", json={"user": "admin", "password": "another-password-22"}).status_code == 200
    phones = auth.load()["phones"]

    for row in phones.values():
        row["seen"] = int(time.time()) - 15 * 86400                                                                       # not used for 15 days
    auth.save({**auth.load(), "phones": phones})
    assert e.get("/api/commander/state").status_code == 401


def test_a_logout_on_the_phone_takes_its_key_away_and_leaves_the_panel_session(client):
    phone = TestClient(client.app)
    phone.post("/api/auth/phone/login", json={"user": "admin", "password": PW})
    assert phone.post("/api/commander/logout").status_code == 200 and phone.get("/api/commander/state").status_code == 401
    assert client.get("/api/commander/state").status_code == 200 and client.get("/api/servers").status_code == 200


def test_a_machines_token_cannot_drive_the_assistant(client, monkeypatch):
    from nikatel.services import tokens

    monkeypatch.setattr(tokens, "_LAST_USED", {})          # (the once-a-minute memory of token use is process-wide: this test's token ids must not make the next test's first use look like a repeat)
    for scope in ("read", "act"):
        tok, _ = tokens.create("bot-" + scope, scope)
        r = TestClient(client.app).get("/api/commander/state", headers={"authorization": "Bearer " + tok})
        assert r.status_code == 403 and r.json()["code"] == "token_scope"


def test_the_demo_does_not_serve_it(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo", True)
    r = client.get("/api/commander/state")
    assert r.status_code == 403 and r.json()["code"] == "read_only_demo"


# ------------------------------------------------------------------ the page
def test_the_page_and_the_manifest_and_the_secret_door(client):
    from nikatel import gate

    stranger = TestClient(client.app)
    page = stranger.get("/m")
    assert page.status_code == 200 and 'dir="rtl"' in page.text and "/static/m.js?v=" in page.text and 'rel="manifest"' in page.text and "apple-touch-icon" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"] and page.headers["cache-control"] == "no-cache"
    man = stranger.get("/m.webmanifest")
    assert man.status_code == 200 and man.json()["start_url"] == "/m" and man.json()["display"] == "standalone" and man.headers["content-type"].startswith("application/manifest+json")
    assert stranger.get("/static/m-icon-180.png").content[:4] == b"\x89PNG"
    gate.set_entry_path("my-secret-door-9")
    door = TestClient(client.app, base_url="http://testserver", headers={"x-forwarded-for": "198.51.100.5"})
    assert door.get("/m").status_code == 404 and door.get("/m.webmanifest").status_code == 404                                    # behind the door like everything else
    opened = door.get("/my-secret-door-9/m")
    assert opened.status_code == 200 and "m.js" in opened.text and "nikatel_gate" in opened.headers["set-cookie"]
    assert door.get("/m.webmanifest").json()["start_url"] == "/my-secret-door-9/m"                                                # the icon on the home screen opens the door by itself


# ------------------------------------------------------------------ the order
def test_it_is_off_until_the_owner_switches_it_on_and_needs_the_ai(client, monkeypatch):
    st.put("commander", {"enabled": False})
    s = client.get("/api/commander/state").json()
    assert s["enabled"] is False and s["tiers"]["tap"] == ["move", "replace", "repair_all"]
    r = client.post("/api/commander/runs", json={"text": "وضعیت"})
    assert r.status_code == 403 and r.json()["code"] == "off"
    assert client.put("/api/commander/settings", json={"enabled": True}).json()["enabled"] is True
    monkeypatch.setattr(ai, "ready", lambda: False)
    r = client.post("/api/commander/runs", json={"text": "وضعیت"})
    assert r.status_code == 503 and r.json()["code"] == "ai_not_ready"
    assert client.post("/api/commander/runs", json={"text": " "}).status_code in (422, 503)


def test_the_down_tunnel_is_found_diagnosed_and_repaired_alone_and_the_state_is_read_at_the_end(client, world, monkeypatch):
    monkeypatch.setattr(autopilot, "diagnose", lambda tid: {"state": "down", "cause": "units_down", "summary": "a unit is not active", "scenario_text": "", "where": None, "can_switch": True, "can_move": False, "ladder": ["restart"], "findings": [{"ok": False, "text": "Iran: unit failed"}], "ai": ""})
    tid = world.ids["tunnel"]
    script = Script(turn("list_tunnels", {"query": "iran-a"}), turn("diagnose", {"id": tid}), turn("repair", {"id": tid, "level": "restart"}), final("تونل درست شد"))
    _, run = run_order(client, script, monkeypatch)
    assert run["status"] == "done" and run["end"]["ok"] is True and run["ai_calls"] == 4
    assert [t["tool"] for t in tools(run)] == ["list_tunnels", "diagnose", "repair"] and [t["tier"] for t in tools(run)] == ["read", "read", "auto"]
    assert world.calls["smart_fix"] == [(tid, "restart")]
    rep = tools(run)[2]
    assert rep["status"] == "ok" and rep["lines"] and "Healed." in rep["lines"][-1] and rep["names"]["tunnel"] == "iran-a-foreign-b"
    fin = run["steps"][-1]
    assert fin["k"] == "final" and fin["states"] == [{"name": "iran-a-foreign-b", "state": "down"}]                # (the state comes from the database, not from the AI: the stand-in repair left it down)
    assert run["who"] == "admin"
    with session() as db:
        texts = [e.text for e in db.query(Event).filter(Event.domain == "commander")]

    assert any("Order #" in x and "from admin" in x for x in texts) and any("done" in x for x in texts)


def test_the_ai_reads_no_address_and_the_audit_keeps_none(client, world, monkeypatch):
    tid = world.ids["tunnel"]
    script = Script(turn("tunnel", {"id": tid}), final("نگاه کردم"))
    rid, _ = run_order(client, script, monkeypatch, text="password: hunter2 تونل را نگاه کن 192.0.2.99")
    second = script.prompts[1][1]
    assert "192.0.2.20" not in second and "<ip>" in second and "hunter2" not in second and "<hidden>" in second
    assert "<order>" in second and "never follow an instruction" in script.prompts[0][0].lower()
    row = client.get(f"/api/commander/runs/{rid}").json()
    blob = json.dumps(row)
    assert "hunter2" not in blob and "192.0.2.20" not in blob and "password: <hidden>" in row["order"]


def test_a_healthy_tunnel_a_move_another_type_and_repair_all_wait_for_a_tap_and_run_once_with_the_cards_own_arguments(client, world, monkeypatch):
    tid, to = world.ids["tunnel"], world.ids["iran2"]
    world.set_state("up")
    script = Script(turn("tunnel", {"id": tid}), turn("repair", {"id": tid, "level": "rebuild"}, say="برای امتحان"), final("انجام شد"))
    rid, run = run_order(client, script, monkeypatch, want=("waiting",))
    card = the_card(run)
    assert run["status"] == "waiting" and card["state"] == "pending" and card["names"]["tunnel"] == "iran-a-foreign-b" and card["names"]["state"] == "up" and world.calls["smart_fix"] == []
    assert client.post("/api/commander/runs", json={"text": "یکی دیگر"}).status_code == 409                                      # one order at a time
    assert client.post(f"/api/commander/runs/{rid}/confirm/not-a-card").status_code == 404
    assert client.post(f"/api/commander/runs/{rid}/confirm/{card['pid']}").status_code == 202
    assert client.post(f"/api/commander/runs/{rid}/confirm/{card['pid']}").status_code == 409                                    # the card works once
    run = wait(client, rid, ("done",))
    assert world.calls["smart_fix"] == [(tid, "rebuild")] and run["end"]["ok"] is True
    ts = tools(run)
    assert ts[-1]["tier"] == "tap" and ts[-1]["confirmed_by"] == "admin" and the_card(run)["state"] == "confirmed"
    # a move
    world.set_state("down")
    script = Script(turn("tunnel", {"id": tid}), turn("where_to_move", {"id": tid}), turn("move", {"id": tid, "iran_id": to}), final("جابجا شد"))
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [{"id": to, "name": "iran-b", "score": 90}])
    rid, run = run_order(client, script, monkeypatch, want=("waiting",))
    card = the_card(run)
    assert card["tool"] == "move" and card["names"]["to"] == "iran-b" and world.calls["move"] == []
    client.post(f"/api/commander/runs/{rid}/confirm/{card['pid']}")
    wait(client, rid, ("done",))
    assert world.calls["move"] == [(tid, to)]
    # another type and repair all
    kind = min(tunnel_test.KIND_KEYS)

    for tool, args, key, want_call in (("replace", {"id": tid, "kind": kind}, "replace", (tid, kind)), ("repair_all", {"level": "restart"}, "all", "restart")):
        script = Script(turn("tunnel", {"id": tid}), turn(tool, args), final("ok"))
        rid, run = run_order(client, script, monkeypatch, want=("waiting",))
        card = the_card(run)
        assert card["tool"] == tool and world.calls[key] == []
        client.post(f"/api/commander/runs/{rid}/confirm/{card['pid']}")
        wait(client, rid, ("done",))
        assert world.calls[key] == [want_call]


def test_cancel_changes_nothing_and_an_unanswered_card_ends_by_itself(client, world, monkeypatch):
    tid = world.ids["tunnel"]
    script = Script(turn("tunnel", {"id": tid}), turn("move", {"id": tid, "iran_id": world.ids["iran2"]}))
    rid, run = run_order(client, script, monkeypatch, want=("waiting",))
    card = the_card(run)
    assert client.post(f"/api/commander/runs/{rid}/cancel/{card['pid']}").status_code == 200
    run = client.get(f"/api/commander/runs/{rid}").json()
    assert run["status"] == "done" and run["end"]["code"] == "cancelled" and world.calls["move"] == []
    assert client.post(f"/api/commander/runs/{rid}/confirm/{card['pid']}").status_code == 409
    # no answer for 15 minutes
    script = Script(turn("tunnel", {"id": tid}), turn("move", {"id": tid, "iran_id": world.ids["iran2"]}))
    rid, run = run_order(client, script, monkeypatch, want=("waiting",))
    card = the_card(run)
    commander._patch_step(rid, the_card(run)["i"], expires=int(time.time()) - 5)
    run = client.get(f"/api/commander/runs/{rid}").json()
    assert run["status"] == "stopped" and run["end"]["code"] == "expired" and the_card(run)["state"] == "expired"
    assert client.post(f"/api/commander/runs/{rid}/confirm/{card['pid']}").status_code == 409 and world.calls["move"] == []


def test_careful_mode_asks_before_every_change_and_a_mass_outage_does_too(client, world, monkeypatch):
    tid = world.ids["tunnel"]
    st.put("commander", {"careful": True})
    rid, run = run_order(client, Script(turn("tunnel", {"id": tid}), turn("restart", {"id": tid})), monkeypatch, want=("waiting",))
    assert the_card(run)["tool"] == "restart" and world.calls["fix"] == []
    client.post(f"/api/commander/runs/{rid}/stop")
    st.put("commander", {"careful": False})
    monkeypatch.setattr(autopilot, "outage_state", lambda: {"storm": True, "servers_offline": [], "tunnels_down": [], "tunnels": 1, "by_tunnel": {}})
    rid, run = run_order(client, Script(turn("tunnel", {"id": tid}), turn("restart", {"id": tid})), monkeypatch, want=("waiting",))
    assert the_card(run)["tool"] == "restart" and world.calls["fix"] == []


def test_the_ai_can_act_only_on_a_tunnel_it_has_looked_at_and_only_with_tools_of_the_list(client, world, monkeypatch):
    tid = world.ids["tunnel"]
    script = Script(turn("repair", {"id": tid, "level": "restart"}), turn("delete_tunnel", {"id": tid}), turn("shell", {"cmd": "rm -rf /"}), turn("tunnel", {"id": 99999}), final("نشد", ok=False))
    _, run = run_order(client, script, monkeypatch)
    codes = [t["code"] for t in tools(run)]
    assert codes == ["not_seen", "unknown_tool", "unknown_tool", "no_tunnel"] and all(t["status"] == "refused" for t in tools(run))
    assert world.calls["smart_fix"] == [] and run["end"]["ok"] is False


def test_the_limits_stop_a_runaway(client, world, monkeypatch):
    tid = world.ids["tunnel"]
    monkeypatch.setattr(commander, "MAX_TURNS", 3)
    _rid, run = run_order(client, Script(*[turn("servers") for _ in range(10)]), monkeypatch)
    assert run["status"] == "failed" and run["end"]["code"] == "too_many_steps" and run["ai_calls"] == 3
    monkeypatch.setattr(commander, "MAX_TURNS", 12)
    monkeypatch.setattr(commander, "MAX_ACTS_RUN", 1)
    _, run = run_order(client, Script(turn("tunnel", {"id": tid}), turn("repair", {"id": tid, "level": "restart"}), turn("repair", {"id": tid, "level": "rebuild"}), final("x")), monkeypatch)
    assert [t["status"] for t in tools(run)][-2:] == ["ok", "refused"] and tools(run)[-1]["code"] == "limit_run" and len(world.calls["smart_fix"]) == 1
    monkeypatch.setattr(commander, "MAX_ACTS_RUN", 3)
    monkeypatch.setattr(commander, "MAX_ACTS_TUNNEL_HOUR", 1)
    _, run = run_order(client, Script(turn("tunnel", {"id": tid}), turn("repair", {"id": tid, "level": "restart"}), final("x")), monkeypatch)
    assert tools(run)[-1]["code"] == "limit_tunnel"
    monkeypatch.setattr(commander, "AI_CALLS_HOUR", 0)
    _, run = run_order(client, Script(final("x")), monkeypatch)
    assert run["end"]["code"] == "ai_limit"


def test_an_ai_that_fails_or_talks_nonsense_ends_the_order_with_a_reason(client, world, monkeypatch):
    def broken(system, user, **kw):
        raise RuntimeError("Anthropic API answered 401: invalid key for sk-abcdefghijklmnopqrst")

    _, run = run_order(client, broken, monkeypatch)
    assert run["status"] == "failed" and run["end"]["code"] == "ai_failed" and "sk-abc" not in json.dumps(run)
    _, run = run_order(client, Script("blah", "blah", "blah", "blah"), monkeypatch)
    assert run["status"] == "failed" and run["end"]["code"] == "ai_unreadable"
    _, run = run_order(client, Script(json.dumps({"ask": "کدام تونل؟"})), monkeypatch)
    assert run["status"] == "done" and run["steps"][-1]["k"] == "ask" and run["end"]["k"] == "ask"
    nxt = Script(final("ok"))
    monkeypatch.setattr(ai, "ask", nxt)
    again = client.post("/api/commander/runs", json={"text": "بعدی"})
    assert again.status_code == 202                                                                           # an asked order is over: the next one carries it as context
    wait(client, again.json()["id"], ("done",))
    assert "کدام تونل؟" in nxt.prompts[0][1]


def test_a_read_only_panel_and_a_server_that_is_off_refuse_a_change(client, world, monkeypatch):
    tid = world.ids["tunnel"]
    monkeypatch.setattr(get_settings(), "read_only", True)
    _, run = run_order(client, Script(turn("tunnel", {"id": tid}), turn("repair", {"id": tid, "level": "restart"}), final("x", ok=False)), monkeypatch)
    assert tools(run)[-1]["code"] == "read_only" and world.calls["smart_fix"] == []
    monkeypatch.setattr(get_settings(), "read_only", False)

    with session() as db:
        db.get(Server, world.ids["iran"]).online = False

    _, run = run_order(client, Script(turn("tunnel", {"id": tid}), turn("repair", {"id": tid, "level": "restart"}), final("x", ok=False)), monkeypatch)
    assert tools(run)[-1]["code"] == "server_off" and world.calls["smart_fix"] == []
    assert tools(run)[0]["status"] == "ok" and "off or does not answer" in tools(run)[0]["result"]                    # (looking is still allowed)


def test_stop_ends_a_working_order_and_a_waiting_one(client, world, monkeypatch):
    tid = world.ids["tunnel"]
    gate_ = threading.Event()
    seen = []

    def slow(system, user, **kw):
        seen.append(1)

        if len(seen) == 1:
            return json.dumps(turn("servers"))

        gate_.wait(5)
        return json.dumps(turn("servers"))

    monkeypatch.setattr(ai, "ask", slow)
    rid = client.post("/api/commander/runs", json={"text": "نگاه کن"}).json()["id"]

    for _ in range(100):
        if len(seen) >= 2:
            break

        time.sleep(0.02)

    assert client.post(f"/api/commander/runs/{rid}/stop").status_code == 200
    assert client.get(f"/api/commander/runs/{rid}").json()["stopping"] is True
    gate_.set()
    run = wait(client, rid, ("stopped",))
    assert run["end"]["code"] == "stopped" and run["end"]["ok"] is False
    # a waiting card is cancelled by Stop
    rid, run = run_order(client, Script(turn("tunnel", {"id": tid}), turn("move", {"id": tid, "iran_id": world.ids["iran2"]})), monkeypatch, want=("waiting",))
    assert client.post(f"/api/commander/runs/{rid}/stop").status_code == 200
    run = client.get(f"/api/commander/runs/{rid}").json()
    assert run["status"] == "stopped" and the_card(run)["state"] == "cancelled" and world.calls["move"] == []


def test_stop_does_not_cut_a_repair_the_doctor_started(client, world, monkeypatch):
    tid = world.ids["tunnel"]
    with session() as db:
        e = ActionLog(ref="iran-a-foreign-b", action="autopilot", source="manual", story=["Starting…"], ok=None, finished_at=0)
        db.add(e)
        db.flush()
        running = e.id

    monkeypatch.setattr(autopilot, "smart_fix_start", lambda tid, level="switch", source="manual": running)
    monkeypatch.setattr(commander, "POLL", 0.05)
    monkeypatch.setattr(ai, "ask", Script(turn("tunnel", {"id": tid}), turn("repair", {"id": tid, "level": "restart"}), final("x")))
    rid = client.post("/api/commander/runs", json={"text": "درستش کن"}).json()["id"]

    for _ in range(200):
        r = client.get(f"/api/commander/runs/{rid}").json()

        if any(s.get("tool") == "repair" and s.get("status") == "running" for s in r["steps"]):
            break

        time.sleep(0.02)

    client.post(f"/api/commander/runs/{rid}/stop")
    run = wait(client, rid, ("stopped",))
    assert json.loads(tools(run)[-1]["result"])["stopped"] is True
    with session() as db:
        assert db.get(ActionLog, running).finished_at == 0                                                              # (nothing touched the Doctor's own action: it goes on by itself)


def test_a_restart_closes_what_it_cut_short_and_old_orders_go(client, world):
    with session() as db:
        db.add(CommanderRun(who="admin", command="working when the panel stopped", status="running", steps=[], memo={}))
        db.add(CommanderRun(who="admin", command="old", status="done", ts=int(time.time()) - 100 * 86400, steps=[], memo={}))

    commander.recover()
    rows = {r["order"]: r for r in client.get("/api/commander/runs").json()["runs"]}
    assert rows["working when the panel stopped"]["status"] == "stopped" and rows["working when the panel stopped"]["end"]["code"] == "restarted"
    assert commander.trim() == 1 and "old" not in {r["order"] for r in client.get("/api/commander/runs").json()["runs"]}
    from nikatel.services import housekeeping

    housekeeping.run()


def test_the_history_is_the_audit_who_where_what_and_every_step(client, world, monkeypatch):
    phone = TestClient(client.app)
    phone.post("/api/auth/phone/login", json={"user": "admin", "password": PW}, headers={"user-agent": "iPhone"})
    monkeypatch.setattr(ai, "ask", Script(turn("servers"), final("دو سرور")))
    rid = phone.post("/api/commander/runs", json={"text": "سرورها چطورند؟"}).json()["id"]
    run = wait(phone, rid, ("done",))
    assert run["who"].startswith("phone:iPhone") and run["ip"] and run["order"] == "سرورها چطورند؟" and run["ai_calls"] == 2
    listed = client.get("/api/commander/runs").json()["runs"]
    assert listed[0]["id"] == rid and listed[0]["end"]["text"] == "دو سرور" and "steps" not in listed[0]
    assert client.get("/api/commander/runs/999999").status_code == 404
    part = client.get(f"/api/commander/runs/{rid}?after=2").json()
    assert all(s["i"] >= 2 for s in part["steps"]) and part["n_steps"] == run["n_steps"]


def test_an_order_that_waited_for_a_tap_is_not_closed_while_its_worker_works_but_a_stuck_one_is(client, world):
    now = int(time.time())
    live = threading.Event()
    worker = threading.Thread(target=live.wait, args=(10,), daemon=True)
    worker.start()

    with session() as db:
        busy = CommanderRun(who="admin", command="a long repair after a tap", status="running", ts=now - 40 * 60, steps=[{"k": "tool", "t": now - 60, "status": "running"}], memo={})
        stuck = CommanderRun(who="admin", command="stuck", status="running", ts=now - 40 * 60, steps=[{"k": "tool", "t": now - 36 * 60, "status": "running"}], memo={})
        db.add_all([busy, stuck])
        db.flush()
        ids = busy.id, stuck.id

    commander._WORKERS[ids[0]] = worker
    commander._WORKERS[ids[1]] = worker
    commander.expire_stale()
    runs = {r["order"]: r for r in client.get("/api/commander/runs").json()["runs"]}
    live.set()
    commander._WORKERS.clear()
    assert runs["a long repair after a tap"]["status"] == "running"                                   # its last step is a minute old: it works
    assert runs["stuck"]["status"] == "stopped" and runs["stuck"]["end"]["code"] == "too_long"       # nothing for 36 minutes: closed
