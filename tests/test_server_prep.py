"""What the panel does with a new server (1.69.23, the owner, 2026-10-19): the GRE test of an Iran server goes from Iran TOWARD a foreign server; the spoof test picks its target BY ITSELF (a server of the other kind, a proven
listener first, the next one when a target cannot be used) and shows no list; the tunnel cores are not put on a server when it is added but with the first tunnel built on it - all of them."""
import itertools

import pytest
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import engines, nodes, onboard

HOSTS = itertools.count(10)


def srv(name, role, online=True, enabled=True, extra=None):
    with session() as db:
        s = Server(name=name, host=f"203.0.113.{next(HOSTS)}", role=role, online=online, enabled=enabled, extra=extra or {})
        db.add(s)
        db.flush()
        return s.id


def test_the_gre_test_of_an_iran_server_goes_toward_a_foreign_server_and_that_of_a_foreign_server_from_an_iran_one(monkeypatch):
    ir, ir2, fo, fo_off = srv("pr-ir", "iran"), srv("pr-ir2", "iran"), srv("pr-fo", "foreign"), srv("pr-fo-off", "foreign", online=False)
    seen = []
    monkeypatch.setattr(nodes, "_probe_pair", lambda tester, target: seen.append((tester["name"], target["name"])) or {"tester": tester["name"], "target": target["name"], "ok": True, "received": 10, "detail": "10 of 10 pings came back"})
    res = nodes.gre_probe(ir)
    assert seen == [("pr-ir", "pr-fo")] and res["ok"] and res["via"] == "pr-fo"                                   # (an Iran server is the TESTER, a foreign one the peer; a foreign server that is off is not used; another Iran server never)
    seen.clear()
    res = nodes.gre_probe(fo)
    assert seen == [("pr-ir", "pr-fo")] and res["via"] == "pr-ir"                                                  # a foreign server: tested from an Iran server, as before
    _ = (ir2, fo_off)


def test_an_iran_server_with_no_foreign_server_to_test_toward_says_so(monkeypatch):
    ir = srv("pr-only-ir", "iran")

    with session() as db:
        for s in db.scalars(select(Server).where(Server.role == "foreign")):
            s.online = False

    with pytest.raises(ValueError, match="no foreign server is online"):
        nodes.gre_probe(ir)


def test_the_spoof_target_is_a_server_of_the_other_kind_a_proven_listener_first():
    rows = [{"id": 1, "name": "ir-a", "role": "iran", "enabled": True, "online": True}, {"id": 2, "name": "fo-b", "role": "foreign", "enabled": True, "online": True},
            {"id": 3, "name": "fo-a", "role": "foreign", "enabled": True, "online": True}, {"id": 4, "name": "fo-off", "role": "foreign", "enabled": True, "online": False},
            {"id": 5, "name": "fo-dis", "role": "foreign", "enabled": False, "online": True}, {"id": 6, "name": "ir-b", "role": "iran", "enabled": True, "online": True}]
    assert nodes.spoof_candidates({"id": 1, "role": "iran"}, rows) == [3, 2]                                       # foreign, on, enabled, by name
    assert nodes.spoof_candidates({"id": 1, "role": "iran"}, [{**r, "spoof_target_ok": r["id"] == 2} for r in rows]) == [2, 3]            # the proven listener first
    assert nodes.spoof_candidates({"id": 2, "role": "foreign"}, rows) == [1, 6]                                    # a foreign sender: toward an Iran server
    assert nodes.spoof_candidates({"id": 1, "role": "iran"}, [r for r in rows if r["role"] == "iran"]) == [6]      # no server of the other kind: the same kind, rather than nothing
    assert nodes.spoof_candidates({"id": 1, "role": "iran"}, rows[:1]) == []


def test_the_spoof_test_tries_the_next_target_when_one_cannot_be_used_and_stops_at_the_first_answer(monkeypatch):
    ir, fo1, fo2, fo3 = srv("sp-ir", "iran"), srv("sp-fo1", "foreign"), srv("sp-fo2", "foreign"), srv("sp-fo3", "foreign")
    calls = []
    answers = {fo1: ValueError("sp-fo1 could not listen: tcpdump failed"), fo2: {"level": "blind", "verdict": "Nothing arrived at the other server", "can": False, "target": "sp-fo2"}, fo3: {"level": "yes", "verdict": "This server can spoof", "can": True, "target": "sp-fo3"}}

    def fake(server_id, target_id, ip):
        calls.append(target_id)
        a = answers[target_id]

        if isinstance(a, Exception):
            raise a

        return dict(a)

    monkeypatch.setattr(nodes, "spoof_test", fake)
    res = nodes.spoof_auto(ir, "10.10.10.10")
    assert calls == [fo1, fo2, fo3] and res["level"] == "yes" and res["tried_targets"] == ["sp-fo1", "sp-fo2", "sp-fo3"]       # a target that cannot listen, one no ping reaches, then one that answers
    calls.clear()
    answers[fo1] = {"level": "no", "verdict": "This server does not spoof", "can": False, "target": "sp-fo1"}
    assert nodes.spoof_auto(ir, "10.10.10.10")["level"] == "no" and calls == [fo1]                                  # an answer about the SENDER ("it does not spoof") is final: no more targets
    answers[fo1] = ValueError("sp-ir: iptables is not installed on the sending server")
    calls.clear()

    with pytest.raises(ValueError, match="iptables"):
        nodes.spoof_auto(ir, "10.10.10.10")

    assert calls == [fo1]                                                                                           # a problem of the sender is the same with every target: it stops at once
    answers[fo1] = answers[fo2] = answers[fo3] = ValueError("sp-x could not listen")

    with pytest.raises(ValueError, match="no target could be used"):
        nodes.spoof_auto(ir, "10.10.10.10")


def test_the_cores_come_with_the_first_tunnel_all_of_them_and_only_once(monkeypatch):
    a, b = srv("co-a", "iran"), srv("co-b", "foreign")
    installed, said = [], []
    monkeypatch.setattr(engines, "install_on", lambda sid, names=None, step=None: installed.append(sid) or {"backhaul": "installed", "paqet": "present"})
    onboard.ensure_all_cores([a, b], said.append)
    assert installed == [a, b] and any("all in place" in s for s in said)

    with session() as db:
        assert db.get(Server, a).extra.get("cores_all") and db.get(Server, b).extra.get("cores_all")

    installed.clear()
    onboard.ensure_all_cores([a, b], said.append)
    assert installed == []                                                                                          # the second tunnel finds them there
    c = srv("co-c", "iran")
    monkeypatch.setattr(engines, "install_on", lambda sid, names=None, step=None: {"backhaul": "failed: no cache", "paqet": "installed"})
    onboard.ensure_all_cores([c], said.append)

    with session() as db:
        assert not db.get(Server, c).extra.get("cores_all")                                                         # a core that failed: not marked, the next tunnel tries again, the build was not stopped

    monkeypatch.setattr(engines, "install_on", lambda sid, names=None, step=None: (_ for _ in ()).throw(ValueError("the panel is read-only")))
    onboard.ensure_all_cores([c], said.append)                                                                      # (never raises into the build)


def test_the_page_has_no_target_list_for_the_spoof_test_and_does_not_put_the_cores_on_by_default():
    from pathlib import Path

    web = Path(onboard.__file__).resolve().parents[1] / "web"
    lab, app = (web / "lab.js").read_text(encoding="utf-8"), (web / "app.js").read_text(encoding="utf-8")
    spoof = lab.split("window.srvSpoof = async", 1)[1].split("let labFollowT", 1)[0]
    assert "glassChoice" not in spoof and "target_id" not in spoof and "spoof_ip: ip.trim()" in spoof
    assert '<input name="x_cores" type="checkbox" checked>' not in app and '<input name="x_cores" type="checkbox">' in app


def test_a_server_that_does_not_answer_ping_but_opens_a_tcp_connection_from_iran_is_not_called_filtered(monkeypatch):
    """The owner, 2026-10-19 (the server UA): "Blocked in Iran (0 replies everywhere)" - 13 Iran servers and 7 locations of a public site got no ping answer - while its tunnel ran and a public site said it was not filtered:
    the server closes ICMP. The verdict of pings is taken back when a TCP connection opens from the Iran servers."""
    assert nodes.tcp_override("blocked", "Blocked in Iran (0 replies everywhere)", 5, 5)[0] == "clean"
    level, text = nodes.tcp_override("blocked", "x", 3, 5)
    assert level == "clean" and "3 of 5 Iran servers" in text and "ICMP is closed" in text
    assert nodes.tcp_override("filtered", "Filtered in Iran", 2, 4)[0] == "clean"                      # half is enough
    assert nodes.tcp_override("blocked", "Blocked in Iran (0 replies everywhere)", 1, 5) == ("blocked", "Blocked in Iran (0 replies everywhere)")        # a real filter: the connection opens from few or none
    assert nodes.tcp_override("blocked", "b", 0, 0) == ("blocked", "b")                                  # (nobody could be asked: the verdict stands)
    assert nodes.tcp_override("clean", "ok", 0, 5) == ("clean", "ok") and nodes.tcp_override("unknown", "u", 5, 5) == ("unknown", "u")


def test_the_tcp_look_asks_the_iran_servers_with_the_script_the_configs_already_use(monkeypatch):
    a, b = srv("tc-a", "iran"), srv("tc-b", "iran")
    srv("tc-fo", "foreign")
    asked = []
    monkeypatch.setattr(nodes, "run_script", lambda node, name, params, timeout=30: asked.append((node.host, name, params)) or ("REACH 198.51.100.9:22 1 40" if len(asked) == 1 else "REACH 198.51.100.9:22 0 0"))
    opened, total = nodes._tcp_rows("198.51.100.9", 22)
    assert (opened, total) == (1, 2) and all(x[1] == "tcp_reach" and x[2] == {"TARGETS": "198.51.100.9:22"} for x in asked)          # only the Iran servers are asked
    _ = (a, b)
