"""1.69.221: the Configs monitoring follows the subscription by itself and does not hide a broken direct config, and the Doctor looks at the name lookups of the foreign servers.
(The owner, 2026-11-05: "the Emirates config is down but the Doctor knows nothing, its tunnel shows connected" - the foreign server listed a dead resolver first, every lookup waited 5 s; the monitor called the config up because its port opened.
 And: "the panel must always keep the subscription up to date by itself".)"""
import time

from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Config, Event, Server, Subscription, Tunnel
from nikatel.services import actions, autopilot, configs, nodedns, probe, userq
from nikatel.services import settings as st

NOW = 1_800_000_000


def _node(i, host="h.example"):
    return {"name": f"N{i}", "uri": f"vless://u@{host}:{80 + i}?type=ws#N{i}", "proto": "vless"}


def _fake_probe(monkeypatch):
    calls = {"added": [], "updated": [], "deleted": []}
    monkeypatch.setattr(probe, "config_uri", lambda name: next(n["uri"] for n in calls.get("links", []) if n["name"] == name))
    monkeypatch.setattr(probe, "add_many", lambda items: calls["added"].extend(i["name"] for i in items) or {i["name"]: None for i in items})
    monkeypatch.setattr(probe, "update_many", lambda items: calls["updated"].extend(i["name"] for i in items) or {i["name"]: None for i in items})
    monkeypatch.setattr(probe, "delete_many", lambda names: calls["deleted"].extend(names) or {n: None for n in names})
    monkeypatch.setattr(configs, "_spawn", lambda fn, *a: fn(*a))
    return calls


# ------------------------------------------------------------------ the groups follow their subscriptions
def test_the_follow_runs_every_few_minutes_and_a_host_change_asks_for_it_at_once():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    main = (root / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert 'jobs.register("subscriptions", configs.FOLLOW_EVERY, configs.sync_all_subscriptions)' in main and configs.FOLLOW_EVERY <= 600
    assert 'jobs.register("subscriptions-soon", 10, configs.follow_due)' in main
    assert 'jobs.register("nodedns", nodedns.EVERY, nodedns.run)' in main
    api = (root / "nikatel" / "api" / "pg_hosts.py").read_text(encoding="utf-8")

    for fn in ("host_create", "host_update", "host_clone", "host_delete", "host_restore", "bulk"):
        assert f"_wrap_change(pg_hosts.{fn}," in api                     # every call that changes a host asks the groups to follow


def test_follow_soon_is_one_run_after_the_delay_and_nothing_before(monkeypatch):
    runs = []
    monkeypatch.setattr(configs, "sync_all_subscriptions", lambda: runs.append(1))
    configs.follow_soon(30)
    configs.follow_soon(5)                          # (a second ask while one waits changes nothing: calls in a row are one run)
    configs.follow_due()
    assert runs == []                               # not yet: the moment has not come
    configs._FOLLOW_AT[0] = time.time() - 1
    configs.follow_due()
    configs.follow_due()
    assert runs == [1] and configs._FOLLOW_AT[0] == 0.0


def test_a_look_that_sees_the_same_again_stops_after_the_download(client, monkeypatch):
    calls = _fake_probe(monkeypatch)
    nodes = [_node(1), _node(2)]
    calls["links"] = nodes
    fetched = []
    monkeypatch.setattr(configs, "fetch_subscription", lambda url: fetched.append(1) or nodes)

    with session() as db:
        db.add(Subscription(name="grp", url="https://sub.example.net/x", nodes=[], excluded=[]))

    first = configs.sync_subscription("grp")
    assert sorted(first["added"]) == ["N1", "N2"] and not first.get("same")
    probe_calls_after_first = len(calls["added"])
    again = configs.sync_subscription("grp")
    assert again.get("same") is True and again["added"] == [] and len(calls["added"]) == probe_calls_after_first and len(fetched) >= 3      # (it downloaded again - one download is the whole cost of a look that finds nothing)
    nodes.append(_node(3))                                                    # the owner adds a host: the subscription changed, the look is done in full
    calls["links"] = nodes
    third = configs.sync_subscription("grp")
    assert third["added"] == ["N3"] and not third.get("same")
    assert configs.sync_subscription("grp", force=True).get("same") is None      # the operator's own button always does the whole look


def test_a_config_deleted_on_the_probe_comes_back_at_the_next_look(client, monkeypatch):
    calls = _fake_probe(monkeypatch)
    nodes = [_node(1), _node(2)]
    calls["links"] = nodes
    monkeypatch.setattr(configs, "fetch_subscription", lambda url: nodes)

    with session() as db:
        db.add(Subscription(name="grp", url="https://sub.example.net/x", nodes=[], excluded=[]))

    configs.sync_subscription("grp")

    with session() as db:                                                   # N2 is gone from the monitor (deleted by hand on the probe)
        db.delete(db.scalar(select(Config).where(Config.name == "N2")))

    r = configs.sync_subscription("grp")
    assert r["added"] == ["N2"]                                             # the list of the monitor is part of what a look compares, so the look is not skipped


def test_a_half_empty_answer_does_not_empty_the_group(client, monkeypatch):
    calls = _fake_probe(monkeypatch)
    full = [_node(i) for i in range(1, 9)]
    calls["links"] = full
    current = {"v": full}
    monkeypatch.setattr(configs, "fetch_subscription", lambda url: current["v"])

    with session() as db:
        db.add(Subscription(name="grp", url="https://sub.example.net/x", nodes=[], excluded=[]))

    configs.sync_subscription("grp")
    current["v"] = full[:2]                                                 # the panel answers with 2 of the 8 nodes for a moment
    r = configs.sync_subscription("grp")
    assert r["removed"] == 0 and calls["deleted"] == []
    assert len(client.get("/api/configs").json()["configs"]) == 8

    with session() as db:
        assert any("lacks 6 of its 8 nodes" in e.text for e in db.scalars(select(Event)))
        assert len(db.scalar(select(Subscription)).nodes) == 8             # the group still remembers all eight: a later, real removal is still seen

    assert configs.sync_subscription("grp").get("same") is True             # the next look sees the same half-empty answer and stops (no second warning)
    assert configs.sync_subscription("grp", force=True)["removed"] == 6      # the operator who means it presses Sync


def test_unticked_nodes_are_removed_even_when_they_are_most_of_the_group(client, monkeypatch):
    calls = _fake_probe(monkeypatch)
    nodes = [_node(i) for i in range(1, 7)]
    calls["links"] = nodes
    monkeypatch.setattr(configs, "fetch_subscription", lambda url: nodes)

    with session() as db:
        db.add(Subscription(name="grp", url="https://sub.example.net/x", nodes=[], excluded=[]))

    configs.sync_subscription("grp")

    with session() as db:
        db.scalar(select(Subscription)).excluded = ["N1", "N2", "N3", "N4", "N5"]

    r = configs.sync_subscription("grp")
    assert r["removed"] == 5                                                # the operator's own choice is never held back by the guard


# ------------------------------------------------------------------ the monitor does not hide a broken direct config
def _health(rows):
    return {"ok": True, "configs": [{"name": n, "slug": n.lower(), "port": 11000 + i, "status": s, "ping": 700 if s == "up" else 0, "checked_at": 1, "error": "" if s == "up" else "curl: (28) SSL connection timeout", "speed": {}} for i, (n, s) in enumerate(rows)]}


def test_a_direct_config_whose_full_test_fails_is_not_called_up_when_its_siblings_pass(client, monkeypatch):
    rows = [("D1", "up"), ("D2", "up"), ("D3", "up"), ("Bad", "down")]
    monkeypatch.setattr(probe, "health", lambda: _health(rows))
    monkeypatch.setattr(probe, "config_uri", lambda name: f"vless://u@{name.lower()}.example:443?type=tcp&security=tls#{name}")
    client.post("/api/servers", json={"name": "ir1", "host": "9.9.9.9", "role": "iran"})
    monkeypatch.setattr(configs, "run_script", lambda node, name, params=None, timeout=25: "".join(f"REACH {n.lower()}.example:443 1 40\n" for n, _ in rows))

    for _ in range(4):
        configs.sync()

    by = {c["name"]: c for c in client.get("/api/configs").json()["configs"]}
    assert by["D1"]["status"] == "up"
    assert by["Bad"]["status"] == "down" and "full test through it fails while 3 other direct configs pass" in by["Bad"]["last_error"]       # not "up on the open port alone"


def test_when_no_direct_config_passes_the_open_port_still_counts(client, monkeypatch):
    rows = [("D1", "down"), ("D2", "down")]
    monkeypatch.setattr(probe, "health", lambda: _health(rows))
    monkeypatch.setattr(probe, "config_uri", lambda name: f"vless://u@{name.lower()}.example:443?type=tcp&security=tls#{name}")
    client.post("/api/servers", json={"name": "ir1", "host": "9.9.9.9", "role": "iran"})
    monkeypatch.setattr(configs, "run_script", lambda node, name, params=None, timeout=25: "".join(f"REACH {n.lower()}.example:443 1 40\n" for n, _ in rows))

    for _ in range(4):
        configs.sync()

    by = {c["name"]: c for c in client.get("/api/configs").json()["configs"]}
    assert by["D1"]["status"] == "up" and "port opens from Iran" in by["D1"]["last_error"]          # the probe is blind on this path (nobody passes): the old rule stands


# ------------------------------------------------------------------ the Doctor looks at the name lookups
LOOK_SLOW = "DNS_MS 5025\nDNS_LINK 0\nDNS_FIRST 8.8.8.8\n"
LOOK_FAST = "DNS_MS 21\nDNS_LINK 0\nDNS_FIRST 8.8.4.4\n"


def test_the_look_and_the_heal_answers_are_read():
    assert nodedns.parse_look(LOOK_SLOW) == {"ms": 5025.0, "link": False, "first": "8.8.8.8"}
    assert nodedns.parse_look("DNS_MS -1\n")["ms"] is None                                  # (curl is missing: unknown, never slow)
    assert nodedns.parse_look("DNS_MS 12\nDNS_LINK 1\nDNS_FIRST 127.0.0.53\n")["link"] is True

    try:
        nodedns.parse_look("garbage")
        raise AssertionError("not an answer")
    except ValueError:
        pass

    assert nodedns.parse_heal("noise\nDNS_OK 5025 21 8.8.4.4\n") == {"result": "ok", "before": 5025, "after": 21, "first": "8.8.4.4", "why": ""}
    assert nodedns.parse_heal("DNS_SKIP fewer than two public resolvers answer from this server")["result"] == "skip"
    assert nodedns.parse_heal("DNS_FAIL the lookup did not get better (5025 ms -> 5030 ms); the old files are back")["result"] == "fail"
    assert nodedns.parse_heal("")["result"] == "fail"


def test_a_slow_lookup_is_confirmed_by_a_second_look_not_by_a_blip():
    s, v = nodedns.judge(None, nodedns.parse_look(LOOK_SLOW), NOW)
    assert v == "wait" and s["n"] == 1
    s, v = nodedns.judge(s, nodedns.parse_look(LOOK_SLOW), NOW + 30)                       # (too soon after the first: still one look)
    assert v == "wait" and s["n"] == 1
    s, v = nodedns.judge(s, nodedns.parse_look(LOOK_SLOW), NOW + nodedns.MIN_GAP + 1)
    assert v == "slow" and s["n"] == 2
    s, v = nodedns.judge(s, nodedns.parse_look(LOOK_FAST), NOW + 900)
    assert v == "ok" and s["n"] == 0                                                       # one good look clears it
    assert nodedns.judge({"n": 1, "last_slow_at": NOW}, {"ms": None}, NOW + 900)[1] == "unknown"


def test_the_heal_rails_are_once_a_day_per_server_and_a_few_a_day():
    assert nodedns.rails([], {}, 5, NOW) == ""
    assert "less than a day" in nodedns.rails([], {"5": NOW - 3600}, 5, NOW)
    assert nodedns.rails([], {"5": NOW - 90000}, 5, NOW) == ""
    assert "heals were done" in nodedns.rails([NOW - 10] * nodedns.MAX_PER_DAY, {}, 5, NOW)


def _world():
    with session() as db:
        ir, ua, panel = (Server(name="ir1", host="203.0.113.1", role="iran", online=True), Server(name="UA", host="203.0.113.2", role="foreign", online=True),
                         Server(name="Panel", host="203.0.113.3", role="foreign", online=True))
        db.add_all([ir, ua, panel])
        db.flush()
        db.add(Tunnel(name="ua-ir1", type="backpack", transport="l3:pck", iran_server_id=ir.id, foreign_server_id=ua.id, state="up", forwarded_ports=[11426], enabled=True))
        return ua.id


def test_a_foreign_server_with_a_dead_first_resolver_is_healed_after_the_second_slow_look(monkeypatch):
    uid = _world()
    ran = {"look": [], "heal": []}
    answers = {"look": LOOK_SLOW, "heal": "DNS_OK 5025 21 8.8.4.4\n"}
    monkeypatch.setattr(nodedns, "run_script", lambda node, name, params=None, timeout=60: ran["look"].append(node.host) or answers["look"])
    monkeypatch.setattr(nodedns, "run_action", lambda node, name, params=None, timeout=90: ran["heal"].append((node.host, name)) or answers["heal"])
    monkeypatch.setattr(nodedns.jobs, "stopping", lambda: False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(nodedns, "get_settings", lambda: type("S", (), {"read_only": False})())

    nodedns.run()
    assert ran["look"] == ["203.0.113.2"] and ran["heal"] == []                               # only the server a tunnel ends on is looked at (not "Panel"); one slow look heals nothing
    nodedns._STATE[uid]["last_slow_at"] -= nodedns.MIN_GAP + 1
    nodedns.run()
    assert ran["heal"] == [("203.0.113.2", "dns_heal")]
    assert nodedns.last_of(uid) == 21.0                                                       # the memory shows the healed lookup
    doc = st.get("nodedns")
    assert str(uid) in doc["healed"] and len(doc["done"]) == 1

    with session() as db:
        assert any("Server UA answered every name lookup in 5.0 s" in e.text and "21 ms" in e.text for e in db.scalars(select(Event)))

    nodedns.run()
    nodedns._STATE[uid] = {"ms": 5025.0, "at": NOW, "n": 1, "last_slow_at": 1}
    nodedns.run()
    assert len(ran["heal"]) == 1                                                              # once a day per server


def test_nothing_is_changed_in_mode_off_or_on_a_link_or_when_the_heal_cannot_help(monkeypatch):
    uid = _world()
    ran = {"heal": []}
    state = {"look": LOOK_SLOW, "mode": "off", "heal": "DNS_SKIP fewer than two public resolvers answer from this server"}
    monkeypatch.setattr(nodedns, "run_script", lambda node, name, params=None, timeout=60: state["look"])
    monkeypatch.setattr(nodedns, "run_action", lambda node, name, params=None, timeout=90: ran["heal"].append(name) or state["heal"])
    monkeypatch.setattr(nodedns.jobs, "stopping", lambda: False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: state["mode"])
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(nodedns, "get_settings", lambda: type("S", (), {"read_only": False})())
    nodedns.run()
    assert ran["heal"] == [] and nodedns.last_of(uid) is None                                 # mode off: not even a look

    state["mode"] = "fix"
    state["look"] = "DNS_MS 5025\nDNS_LINK 1\nDNS_FIRST 127.0.0.53\n"
    nodedns.run()
    nodedns._STATE[uid]["last_slow_at"] -= nodedns.MIN_GAP + 1
    nodedns.run()
    assert ran["heal"] == []                                                                  # a link (systemd-resolved) is told, never touched

    with session() as db:
        assert any("systemd-resolved" in e.text and "does not touch it" in e.text for e in db.scalars(select(Event)))

    state["look"] = LOOK_SLOW
    nodedns._TOLD.clear()
    nodedns._STATE.clear()
    nodedns.run()
    nodedns._STATE[uid]["last_slow_at"] -= nodedns.MIN_GAP + 1
    nodedns.run()
    assert ran["heal"] == ["dns_heal"]                                                        # the heal ran, said it cannot help

    with session() as db:
        assert any("could not fix it by itself" in e.text and "fewer than two" in e.text for e in db.scalars(select(Event)))


def test_the_look_at_suffering_users_names_a_slow_lookup_as_the_cause():
    base = {"state": "up", "known": 1, "n_bad": 1, "bw_state": "ok", "bw_text": "282 Mb/s", "loss": 0, "rtt": 100, "rtt_ref": 100, "iran": {"name": "ir-node", "cpu": 20}, "foreign": {"name": "UA", "cpu": 5}}
    assert userq.explain_users(base)[0] == "beyond"
    code, text = userq.explain_users({**base, "dns_ms": 5025.0})
    assert code == "foreign_dns" and "UA answers every name lookup in 5.0 s" in text
    assert userq.explain_users({**base, "dns_ms": 21.0})[0] == "beyond"
    assert userq.explain_users({**base, "dns_ms": 5025.0, "loss": 12})[0] == "tunnel_loss"        # the tunnel's own faults come first


def test_the_scripts_are_declared_and_the_heal_is_an_action():
    from nikatel import ssh

    assert "dns_look" in ssh.SCRIPTS and "dns_heal" in ssh.ACTIONS and "dns_heal" not in ssh.SCRIPTS
    look, heal = ssh.SCRIPTS["dns_look"][0], ssh.ACTIONS["dns_heal"][0]

    for needle in ("DNS_MS", "DNS_LINK", "DNS_FIRST"):
        assert needle in look

    for needle in ("DNS_SKIP", "DNS_FAIL", "DNS_OK", "options timeout:2 attempts:2", "resolv.conf.nikatel-bak", "[ -L $RC ]"):
        assert needle in heal

    assert "rm -f" not in heal and "/etc/resolv.conf.nk-new" not in heal.replace("$RC.nk-new", "")      # (it writes a new file beside the old one and moves it over: never a half-written resolver file)
