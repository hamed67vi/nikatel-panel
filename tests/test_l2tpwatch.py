"""The L2TP watch (services/l2tpwatch.py, 1.69.257). The owner, 2026-11-13: "the Doctor must always check the health of the config, the server and its tunnel, and fix any problem" and, of the Tunnel manager,
"the L2TP tunnel must be marked here". No test touches a server: the reads and the actions are replaced by fakes."""
import calendar
import time
from pathlib import Path

import pytest

from nikatel.db import session
from nikatel.models import Server, Tunnel, is_protected
from nikatel.services import l2tp, l2tpwatch, purpose
from nikatel.services import settings as st

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "nikatel" / "web"
NOW = int(time.time())
STAMP = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 60))          # (the mirror's last word, a minute ago)
ENTRY_OK = f"""L2TP ok
UNIT strongswan active
UNIT xl2tpd active
UNIT tick active
UDP 500 4500 1701
EXITIP 10.0.0.2
ROUTE 10.0.0.2
PING ok
MIRRORTIMER active
MIRROR {STAMP} 10.0.0.2 {{"ok": true, "added": 0, "updated": 0, "removed": 0, "total": 2, "key": "same"}}
INFO {{"pool": "10.250.0.0/22", "accounts": 2, "online": 1, "version": 5, "features": ["speed", "quota", "ip", "strict_limit"]}}
SESSIONS 1
DONE
"""
EXIT_OK = """EXIT ok
ROUTES 10.250.0.0/22 10.0.0.1;
RULES 12
FORWARD 1
TIMER active
DONE
"""
TUN = {"id": 1, "name": "x-y", "state": "up", "protected": True, "iran_ip": "10.0.0.1", "foreign_ip": "10.0.0.2"}


def entry(**over):
    d = l2tpwatch.parse_watch(ENTRY_OK)
    d.update(over)
    return d


def exit_(**over):
    d = l2tp.parse_exit(EXIT_OK)
    d.update(over)
    return d


def keys(findings):
    return [f["key"] for f in findings]


# ------------------------------------------------------------------ pure
def test_the_watch_read_is_parsed_and_the_mirrors_word_is_dated():
    d = l2tpwatch.parse_watch(ENTRY_OK)
    assert d["installed"] and d["units"] == {"strongswan": "active", "xl2tpd": "active", "tick": "active"} and d["udp"] == {500, 4500, 1701}
    assert d["exit_ip"] == "10.0.0.2" and d["route"] == "10.0.0.2" and d["ping"] == "ok" and d["mirror_timer"] == "active" and d["sessions"] == 1 and d["info"]["pool"] == "10.250.0.0/22"
    assert d["mirror"]["ok"] is True and d["mirror"]["at"] == calendar.timegm(time.strptime(STAMP, "%Y-%m-%dT%H:%M:%SZ")) == NOW - 60 and d["mirror"]["raw"].startswith(STAMP)
    assert l2tpwatch._parse_mirror("2026-11-09T01:20:54Z FAILED no answer from 10.0.0.2")["ok"] is False and l2tpwatch._parse_mirror("")["ok"] is None and l2tpwatch._parse_mirror("garbage")["at"] == 0
    assert l2tpwatch.parse_watch("L2TP none\n")["installed"] is False
    n = l2tpwatch.parse_watch("L2TP ok\nUDP \nMIRROR \nINFO notjson\nSESSIONS x\nDONE\n")
    assert n["udp"] == set() and n["mirror"]["ok"] is None and n["info"] == {} and n["sessions"] == 0


def test_a_healthy_product_has_no_finding():
    assert l2tpwatch.judge(entry(), exit_(), TUN, None, NOW) == []
    assert l2tpwatch.judge(entry(), exit_(), TUN, entry(), NOW) == []          # with a direct server and a fresh mirror
    assert l2tpwatch.judge(None, exit_(), TUN, None, NOW) == []                # no entry chosen: nothing to watch


def test_the_entry_is_judged_services_ports_timer_route_and_the_far_end():
    f = l2tpwatch.judge(entry(units={"strongswan": "inactive", "xl2tpd": "active", "tick": "active"}), exit_(), TUN, None, NOW)
    assert keys(f) == ["entry.services"] and f[0]["level"] == "bad" and f[0]["fix"] == {"where": "entry", "action": "l2tp_heal", "params": {"MODE": "restart"}, "what": "restarted the IPsec and L2TP services on the entry"}
    f = l2tpwatch.judge(entry(udp={500}), exit_(), TUN, None, NOW)
    assert keys(f) == ["entry.ports"] and "UDP 1701, 4500" in f[0]["text"] and f[0]["fix"]["params"] == {"MODE": "restart"}
    f = l2tpwatch.judge(entry(units={"strongswan": "active", "xl2tpd": "active", "tick": "inactive"}), exit_(), TUN, None, NOW)
    assert keys(f) == ["entry.tick"] and f[0]["level"] == "warn"
    f = l2tpwatch.judge(entry(exit_ip="10.0.0.9", route="10.0.0.9"), exit_(), TUN, None, NOW)          # the tunnel was rebuilt: its far end changed under the product
    assert keys(f) == ["entry.exitip"] and f[0]["fix"] == {"where": "entry", "action": "l2tp_heal", "params": {"MODE": "route", "IP": "10.0.0.2"}, "what": "pointed the entry's customers at 10.0.0.2, the tunnel's far end"}
    f = l2tpwatch.judge(entry(route=""), exit_(), TUN, None, NOW)
    assert keys(f) == ["entry.route"] and f[0]["fix"]["params"] == {"MODE": "tick"}
    f = l2tpwatch.judge(entry(ping="no"), exit_(), TUN, None, NOW)
    assert keys(f) == ["entry.ping"] and f[0]["fix"] is None and "does not answer" in f[0]["text"]
    assert l2tpwatch.judge(entry(ping="no"), exit_(), {**TUN, "state": "down"}, None, NOW)[0]["key"] == "tunnel.down"          # (the tunnel is down: that is the finding, not the ping)
    f = l2tpwatch.judge({"error": "ssh connect failed"}, exit_(), TUN, None, NOW)
    assert keys(f) == ["entry.reach"] and f[0]["fix"] is None
    assert keys(l2tpwatch.judge(entry(installed=False), exit_(), TUN, None, NOW)) == ["entry.installed"]
    assert keys(l2tpwatch.judge(entry(), exit_(), None, None, NOW)) == []          # no tunnel told: the route cannot be judged


def test_the_exit_is_judged_route_rules_forwarding_and_timer():
    f = l2tpwatch.judge(entry(), exit_(routes=["10.250.0.0/22 10.0.0.7"]), TUN, None, NOW)
    assert keys(f) == ["exit.route"] and f[0]["fix"] == {"where": "exit", "action": "l2tp_exit_heal", "params": {"MODE": "add", "POOL": "10.250.0.0/22", "IP": "10.0.0.1"}, "what": "routed the pool back to 10.0.0.1, the entry's inner address"}
    f = l2tpwatch.judge(entry(), exit_(routes=[]), TUN, None, NOW)
    assert keys(f) == ["exit.route"] and "no route" in f[0]["text"]
    for over in ({"rules": 0}, {"forward": False}, {"timer": "inactive"}):
        f = l2tpwatch.judge(entry(), exit_(**over), TUN, None, NOW)
        assert keys(f) == ["exit.rules"] and f[0]["fix"]["params"] == {"MODE": "reconcile"}, over
    assert keys(l2tpwatch.judge(entry(), {"error": "timed out"}, TUN, None, NOW)) == ["exit.reach"]
    assert keys(l2tpwatch.judge(entry(), exit_(installed=False), TUN, None, NOW)) == ["exit.installed"]


def test_the_tunnel_the_direct_server_and_the_mirror_are_judged():
    f = l2tpwatch.judge(entry(), exit_(), {**TUN, "state": "down"}, None, NOW)
    assert keys(f) == ["tunnel.down"] and f[0]["fix"] is None
    f = l2tpwatch.judge(entry(), exit_(), {**TUN, "protected": False}, None, NOW)
    assert keys(f) == ["tunnel.protected"] and f[0]["level"] == "warn" and f[0]["fix"]["where"] == "panel" and f[0]["fix"]["action"] == "protect"
    f = l2tpwatch.judge(entry(), exit_(), TUN, entry(units={"strongswan": "active", "xl2tpd": "inactive", "tick": "active"}), NOW)
    assert keys(f) == ["direct.services"] and f[0]["fix"] == {"where": "direct", "action": "l2tp_heal", "params": {"MODE": "restart"}, "what": "restarted the IPsec and L2TP services on the direct server"}
    assert keys(l2tpwatch.judge(entry(), exit_(), TUN, entry(udp={500, 4500}), NOW)) == ["direct.ports"]
    assert keys(l2tpwatch.judge(entry(), exit_(), TUN, {"error": "no route"}, NOW)) == ["direct.reach"]
    f = l2tpwatch.judge(entry(mirror_timer="inactive"), exit_(), TUN, entry(), NOW)
    assert keys(f) == ["mirror.timer"] and f[0]["fix"]["params"] == {"MODE": "mirror"}
    f = l2tpwatch.judge(entry(mirror={"raw": "x", "at": NOW - 2000, "ok": True}), exit_(), TUN, entry(), NOW)
    assert keys(f) == ["mirror.stale"] and "33 minutes old" in f[0]["text"]
    f = l2tpwatch.judge(entry(mirror={"raw": "x FAILED", "at": NOW - 10, "ok": False}), exit_(), TUN, entry(), NOW)
    assert keys(f) == ["mirror.stale"] and "last run failed" in f[0]["text"]
    assert keys(l2tpwatch.judge(entry(mirror_timer="inactive"), exit_(), TUN, None, NOW)) == []          # no direct server: the mirror is nobody's business


def test_the_findings_come_worst_first_and_the_plan_keeps_the_rails():
    f = l2tpwatch.judge(entry(units={"strongswan": "active", "xl2tpd": "active", "tick": "inactive"}, ping="no"), exit_(rules=0), {**TUN, "protected": False}, None, NOW)
    assert keys(f) == ["entry.ping", "exit.rules", "entry.tick", "tunnel.protected"]          # (bad before warn, then the order of the parts)

    todo, held = l2tpwatch.plan(f, {}, NOW)
    assert keys(todo) == ["exit.rules", "entry.tick", "tunnel.protected"] and [(h["key"], h["state"]) for h in held] == [("entry.ping", "hand")]          # (nothing here can mend a far end that does not answer)
    key = l2tpwatch.repair_key(f[1])
    assert key == "exit:l2tp_exit_heal:reconcile" and l2tpwatch.repair_key(f[3]) == "panel:protect:protect"

    todo, held = l2tpwatch.plan(f, {key: [NOW - 100]}, NOW)
    assert "exit.rules" not in keys(todo) and ("exit.rules", "working") in [(h["key"], h["state"]) for h in held]          # (repaired a moment ago: not again within the cooldown)
    todo, held = l2tpwatch.plan(f, {key: [NOW - 700]}, NOW)
    assert "exit.rules" in keys(todo)
    todo, held = l2tpwatch.plan(f, {key: [NOW - 3600 * i for i in range(1, 7)]}, NOW)
    assert ("exit.rules", "hand") in [(h["key"], h["state"]) for h in held]          # (six times today: the watch stops here)
    todo, held = l2tpwatch.plan(f, {key: [NOW - 86400 - 10] * 6}, NOW)
    assert "exit.rules" in keys(todo)          # (yesterday's repairs do not count)

    two = l2tpwatch.judge(entry(units={"strongswan": "inactive", "xl2tpd": "inactive", "tick": "inactive"}), exit_(), TUN, None, NOW)
    todo, held = l2tpwatch.plan(two, {}, NOW)
    assert len(todo) == 1          # (one restart mends both: the same repair is not run twice in one look)

    fl = l2tpwatch.flag_of(f[1], "working", [NOW - 100], NOW - 500)
    assert fl == {"part": "exit", "key": "exit.rules", "state": "working", "level": "bad", "text": f[1]["text"], "since": NOW - 500, "tries": 1, "max": 6, "last": NOW - 100, "next": NOW - 100 + l2tpwatch.COOLDOWN}
    assert l2tpwatch.flag_of(f[0], "hand", [], NOW)["max"] == 0 and l2tpwatch.flag_of(f[0], "hand", [], NOW)["next"] == 0


# ------------------------------------------------------------------ the job, with fakes
@pytest.fixture
def watch(client, monkeypatch):
    """A product: entry iran-a, exit foreign-a, tunnel fa-ia between them, the direct server foreign-d; the reads answer with what FAKE holds, the actions are recorded."""
    class S:
        read_only = False

    with session() as db:
        a = Server(name="iran-a", host="192.0.2.10", role="iran")
        b = Server(name="foreign-a", host="192.0.2.20", role="foreign")
        d = Server(name="foreign-d", host="192.0.2.30", role="foreign")
        db.add_all([a, b, d])
        db.flush()
        t = Tunnel(name="fa-ia", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, state="up", iran_tunnel_ip="10.0.0.1", foreign_tunnel_ip="10.0.0.2", record={"protected": True})
        db.add(t)
        db.flush()
        ids = (a.id, b.id, t.id, d.id)

    l2tp.set_conf(ids[0], ids[1], ids[2], "Nikatel Shop", "", ids[3])
    fake = {"entry": ENTRY_OK, "exit": EXIT_OK, "direct": ENTRY_OK, "actions": [], "fail": False}
    roles = {"192.0.2.10": "entry", "192.0.2.20": "exit", "192.0.2.30": "direct"}

    def script(node, name, params=None, timeout=40):
        return fake[roles[node.host]]

    def action(node, name, params=None, timeout=90):
        if fake["fail"]:
            raise RuntimeError("ssh connect failed")

        fake["actions"].append((roles[node.host], name, dict(params or {})))
        return "HEAL done\n"

    monkeypatch.setattr(l2tpwatch, "run_script", script)
    monkeypatch.setattr(l2tpwatch, "run_action", action)
    monkeypatch.setattr(l2tp, "run_script", script)          # (the L2TP tab's own reads, for /api/l2tp: the same fake entry, no server)
    monkeypatch.setattr(l2tp, "run_action", action)
    monkeypatch.setattr(l2tpwatch.jobs, "stopping", lambda: False)
    monkeypatch.setattr(l2tpwatch, "get_settings", lambda: S())
    monkeypatch.setattr(l2tpwatch.license, "has", lambda f: True)
    st.put("l2tpwatch", {"on": True, "done": {}, "told": {}, "flags": {}, "seen": {}})
    fake["ids"] = ids
    return fake


def events(client):
    return [e["text"] for e in client.get("/api/events?domain=l2tp").json()["events"]]


def test_a_healthy_product_is_seen_as_such_and_the_pages_get_it(client, watch):
    l2tpwatch.run()
    v = l2tpwatch.view()
    assert v["configured"] and v["direct"] and v["ok"] is True and v["flags"] == [] and v["parts"] == {"entry": "ok", "exit": "ok", "tunnel": "ok", "direct": "ok", "mirror": "ok"} and v["at"] > 0
    assert watch["actions"] == [] and events(client) == []
    assert client.get("/api/doctor").json()["l2tp"]["ok"] is True
    assert client.get("/api/l2tp").json()["watch"]["parts"]["entry"] == "ok"          # (the strip's Doctor cell)
    with session() as db:
        assert db.get(Tunnel, watch["ids"][2]).record.get("purpose") == ["l2tp"]          # (the Tunnel manager marks it the moment the gear is saved)


def test_a_dead_service_is_restarted_once_told_and_then_the_finding_is_over(client, watch):
    watch["entry"] = ENTRY_OK.replace("UNIT xl2tpd active", "UNIT xl2tpd inactive")
    l2tpwatch.run()
    assert watch["actions"] == [("entry", "l2tp_heal", {"MODE": "restart"})]
    assert any("IPsec is active and L2TP is inactive" in t and "restarted the IPsec and L2TP services on the entry" in t for t in events(client))
    v = l2tpwatch.view()
    assert v["ok"] is False and v["parts"]["entry"] == "bad" and v["flags"][0]["key"] == "entry.services" and v["flags"][0]["state"] == "working" and v["flags"][0]["tries"] == 1 and v["flags"][0]["next"] > int(time.time())

    l2tpwatch.run()
    assert len(watch["actions"]) == 1          # (the cooldown: not again two minutes later)
    assert len([t for t in events(client) if "restarted" in t]) == 1

    watch["entry"] = ENTRY_OK          # the service is back
    l2tpwatch.run()
    v = l2tpwatch.view()
    assert v["ok"] is True and v["flags"] == [] and any(t.startswith("L2TP: over - ") for t in events(client))
    assert st.get("l2tpwatch")["told"] == {}


def test_a_rebuilt_tunnel_moves_the_entrys_route_and_the_exits_route(client, watch):
    with session() as db:
        t = db.get(Tunnel, watch["ids"][2])
        t.iran_tunnel_ip, t.foreign_tunnel_ip = "10.9.9.1", "10.9.9.2"          # the panel rebuilt the tunnel: new inner addresses

    l2tpwatch.run()
    assert watch["actions"] == [("entry", "l2tp_heal", {"MODE": "route", "IP": "10.9.9.2"}), ("exit", "l2tp_exit_heal", {"MODE": "add", "POOL": "10.250.0.0/22", "IP": "10.9.9.1"})]
    assert any("far end of tunnel fa-ia is 10.9.9.2" in t for t in events(client)) and any("routed the pool back to 10.9.9.1" in t for t in events(client))


def test_an_unprotected_tunnel_is_protected_by_the_panel_itself(client, watch):
    with session() as db:
        t = db.get(Tunnel, watch["ids"][2])
        t.record = {}

    l2tpwatch.run()

    with session() as db:
        assert is_protected(db.get(Tunnel, watch["ids"][2]))

    assert watch["actions"] == [] and any("marked tunnel fa-ia protected" in t for t in events(client))


def test_what_the_watch_cannot_mend_is_told_every_three_hours_and_a_failed_repair_is_tried_again(client, watch):
    watch["entry"] = ENTRY_OK.replace("PING ok", "PING no")
    l2tpwatch.run()
    l2tpwatch.run()
    bad = [t for t in events(client) if "does not answer the entry through tunnel fa-ia" in t and "needs a look by hand" in t]
    assert len(bad) == 1 and l2tpwatch.view()["flags"][0]["state"] == "hand" and l2tpwatch.view()["flags"][0]["max"] == 0
    st.put("l2tpwatch", {"told": {"entry.ping": int(time.time()) - l2tpwatch.REMIND - 1}})
    l2tpwatch.run()
    assert len([t for t in events(client) if "needs a look by hand" in t]) == 2

    watch["entry"] = ENTRY_OK.replace("UNIT xl2tpd active", "UNIT xl2tpd inactive")
    watch["fail"] = True
    l2tpwatch.run()
    assert watch["actions"] == [] and st.get("l2tpwatch")["done"] == {} and "entry.services" in [f["key"] for f in l2tpwatch.view()["flags"]]          # (a repair that could not even be sent is not counted: it is tried again)
    watch["fail"] = False
    l2tpwatch.run()
    assert watch["actions"] == [("entry", "l2tp_heal", {"MODE": "restart"})]


def test_the_days_repairs_used_the_watch_stops_and_says_so(client, watch):
    watch["entry"] = ENTRY_OK.replace("UNIT xl2tpd active", "UNIT xl2tpd inactive")
    now = int(time.time())
    st.put("l2tpwatch", {"done": {"entry:l2tp_heal:restart": [now - 3600 * i for i in range(1, 7)]}})
    l2tpwatch.run()
    assert watch["actions"] == [] and l2tpwatch.view()["flags"][0]["state"] == "hand"
    assert any("repaired it 6 times in the last 24 hours and stops here" in t for t in events(client))


def test_nothing_happens_while_the_switch_is_off_the_panel_is_read_only_or_no_entry_is_chosen(client, watch, monkeypatch):
    watch["entry"] = ENTRY_OK.replace("UNIT xl2tpd active", "UNIT xl2tpd inactive")
    st.put("l2tpwatch", {"on": False})
    l2tpwatch.run()
    assert watch["actions"] == [] and l2tpwatch.view()["on"] is False
    st.put("l2tpwatch", {"on": True})

    class RO:
        read_only = True

    class RW:
        read_only = False

    monkeypatch.setattr(l2tpwatch, "get_settings", lambda: RO())
    l2tpwatch.run()
    assert watch["actions"] == []
    monkeypatch.setattr(l2tpwatch, "get_settings", lambda: RW())
    monkeypatch.setattr(l2tpwatch.license, "has", lambda f: False)
    l2tpwatch.run()
    assert watch["actions"] == []          # (not licensed: nothing)
    monkeypatch.setattr(l2tpwatch.license, "has", lambda f: True)
    _a, b, t, _d = watch["ids"]
    l2tp.set_conf(0, b, t, "Nikatel Shop", "", 0)          # no entry chosen
    l2tpwatch.run()
    assert watch["actions"] == [] and l2tpwatch.view()["configured"] is False


def test_the_direct_server_is_told_in_the_gear_and_refused_when_it_is_not_a_foreign_server(client, watch):
    a, b, t, d = watch["ids"]
    assert client.put("/api/l2tp/config", json={"entry_id": a, "exit_id": b, "tunnel_id": t, "direct_id": a, "title": "Nikatel Shop"}).status_code == 400
    assert client.put("/api/l2tp/config", json={"entry_id": a, "exit_id": b, "tunnel_id": t, "direct_id": 999, "title": "Nikatel Shop"}).status_code == 400
    r = client.put("/api/l2tp/config", json={"entry_id": a, "exit_id": b, "tunnel_id": t, "direct_id": d, "title": "Nikatel Shop"})
    assert r.status_code == 200 and r.json()["conf"]["direct_id"] == d
    assert l2tp.state()["names"] == {"entry": "iran-a", "exit": "foreign-a", "direct": "foreign-d"}
    r = client.put("/api/l2tp/config", json={"entry_id": a, "exit_id": b, "tunnel_id": 0, "direct_id": 0, "title": "Nikatel Shop"})          # the tunnel is no longer told: its label goes
    assert r.status_code == 200 and purpose.l2tp_tunnel_id() == 0

    with session() as db:
        assert db.get(Tunnel, t).record.get("purpose") is None


def test_the_label_lives_beside_the_hosts_labels():
    with session() as db:
        a = Server(name="iran-z", host="192.0.2.11", role="iran")
        b = Server(name="foreign-z", host="192.0.2.21", role="foreign")
        db.add_all([a, b])
        db.flush()
        t = Tunnel(name="fz-iz", type="backhaul", iran_server_id=a.id, foreign_server_id=b.id, state="up", record={"purpose": ["ai"]})
        db.add(t)
        db.flush()
        tid = t.id

    assert purpose.store({tid: ["ai"]}, l2tp_id=tid) == 1

    with session() as db:
        assert db.get(Tunnel, tid).record["purpose"] == ["ai", "l2tp"]

    assert purpose.store({tid: ["eco"]}, l2tp_id=0) == 1          # (the hosts' labels change, the l2tp label goes with the product's choice)

    with session() as db:
        assert db.get(Tunnel, tid).record["purpose"] == ["eco"]


def test_the_pages_the_scripts_and_the_job_are_in_place():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    js = (WEB / "l2tp.js").read_text(encoding="utf-8")
    main = (ROOT / "nikatel" / "main.py").read_text(encoding="utf-8")
    arch = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert 'l2tp: ["L2TP", "L2TP: the entry of the L2TP VPN product' in app and ".purpose.l2tp {" in (WEB / "app.css").read_text(encoding="utf-8")          # (the Tunnel manager's tag)
    assert "const l2w = dc && dc.l2tp" in app and "const l2Box = l2w && l2w.on && l2w.configured ?" in app and "${warpBox}${l2Box}${modes}" in app and 'class="dx-iss l2tp ${f.state}"' in app
    assert "html body #page .dx-l2p {" in (WEB / "doctor.css").read_text(encoding="utf-8") and "html body #page .dx-tag.ok {" in (WEB / "doctor.css").read_text(encoding="utf-8")
    assert "function l2WatchCell(w)" in js and "l2WatchCell(d.watch)" in js and 'onclick="go(\'tunnels\', \'repairs\')"' in js and 'id="l2c-direct"' in js and 'direct_id: +l2V("l2c-direct") || 0' in js
    assert 'jobs.register("l2tpwatch", l2tpwatch.EVERY, l2tpwatch.run)' in main and "l2tpwatch.py" in arch and "`l2tp_watch`" in arch and "`l2tp_heal`" in arch

    from nikatel import ssh

    assert "l2tp_watch" in ssh.SCRIPTS and 'd.pop("psk",None)' in ssh.SCRIPTS["l2tp_watch"][0]
    for name, params in (("l2tp_heal", {"MODE": "restart", "IP": ""}), ("l2tp_heal", {"MODE": "route", "IP": "10.0.0.2"}), ("l2tp_exit_heal", {"MODE": "add", "POOL": "10.250.0.0/22", "IP": "10.0.0.1"}), ("l2tp_exit_heal", {"MODE": "reconcile", "POOL": "", "IP": ""})):
        assert ssh.render_action(name, params)

    for name, params in (("l2tp_heal", {"MODE": "rm -rf", "IP": ""}), ("l2tp_heal", {"MODE": "route", "IP": "10.0.0.2; id"}), ("l2tp_exit_heal", {"MODE": "add", "POOL": "x", "IP": "10.0.0.1"})):
        with pytest.raises(ValueError):
            ssh.render_action(name, params)
