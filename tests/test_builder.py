import random

import pytest

from nikatel.services.builder import (
    PortMap,
    allocate_network,
    backhaul_texts,
    gre_scripts,
    parse_facts,
    parse_ports,
    pretty_name,
    start_build,
)
from nikatel.ssh import render_action


def test_parse_ports_forms():
    ps = parse_ports("8072, 443=2096, 10000-10002, 443")
    assert [p.spec() for p in ps] == ["8072", "443:2096", "10000", "10001", "10002"]
    assert ps[1].entry() == "443=127.0.0.1:2096" and ps[0].entry() == "8072"

    for bad in ("", "abc", "70000", "10-9", "1-500", "80=0"):
        with pytest.raises(ValueError):
            parse_ports(bad)


def test_pretty_name_limits_and_uniqueness():
    assert pretty_name("FI", "ir1", set()) == "fi-ir1"
    assert pretty_name("FI", "ir1", {"fi-ir1"}) == "fi-ir1-2"
    n = pretty_name("Netherlands-Amsterdam", "Respina Tehran 03", set())
    assert len(n) <= 15 and "-" in n


def test_allocate_network_avoids_taken():
    rnd = random.Random(1)
    net, a, b = allocate_network(["10.0.0.0/8"], rnd=random.Random(2)) if False else allocate_network(["10.74.245.88/30", "192.0.2.1/24"], rnd)
    assert net.startswith("10.") and net.endswith("/30") and a != b and a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]

    with pytest.raises(RuntimeError):
        allocate_network(["10.0.0.0/8"], random.Random(3))


def test_gre_scripts_match_server_layout():
    files = gre_scripts("fi-ir1", "1.1.1.1", "2.2.2.2", 12345, "10.9.9.1/30", 1360)
    assert set(files) == {"/usr/local/sbin/nikatel-gre-up-fi-ir1", "/usr/local/sbin/nikatel-gre-down-fi-ir1", "/usr/local/sbin/nikatel-gre-check-fi-ir1",
                          "/etc/systemd/system/nikatel-gre-fi-ir1.service", "/etc/systemd/system/nikatel-gre-check-fi-ir1.service", "/etc/systemd/system/nikatel-gre-check-fi-ir1.timer"}
    up = files["/usr/local/sbin/nikatel-gre-up-fi-ir1"]
    assert "KEY=12345" in up and "ADDR=10.9.9.1/30" in up and "MTU=1360" in up and "clamp-mss-to-pmtu" in up
    assert "PEER=10.9.9.2" in files["/usr/local/sbin/nikatel-gre-check-fi-ir1"]
    assert "ExecStart=/usr/local/sbin/nikatel-gre-up-fi-ir1" in files["/etc/systemd/system/nikatel-gre-fi-ir1.service"]


def test_backhaul_texts():
    iran, foreign = backhaul_texts("10.9.9.1", 9901, "tok", "tcpmux", [PortMap(8072), PortMap(443, 2096)])
    assert 'bind_addr = "10.9.9.1:9901"' in iran and '"8072",' in iran and '"443=127.0.0.1:2096",' in iran and "mux_con = 8" in iran
    assert 'remote_addr = "10.9.9.1:9901"' in foreign and "connection_pool = 24" in foreign and "mux_streambuffer = 524288" in iran
    iran_tcp, _ = backhaul_texts("10.9.9.1", 9901, "tok", "tcp", [PortMap(80)])
    assert "mux_con" not in iran_tcp


def test_backhaul_nodelay_switch():
    iran, foreign = backhaul_texts("10.1.2.1", 9900, "tok", "tcpmux", parse_ports("8072"))
    assert "nodelay = true" in iran and "nodelay = true" in foreign
    iran, foreign = backhaul_texts("10.1.2.1", 9900, "tok", "tcpmux", parse_ports("8072"), 75, False)
    assert "nodelay = false" in iran and "nodelay = false" in foreign and "nodelay = true" not in iran


def test_backhaul_advanced_options():
    from nikatel.services.builder import clean_bh_opts

    o = clean_bh_opts({"heartbeat": "30", "connection_pool": 48, "log_level": "info", "aggressive_pool": True, "tune": False, "mux_con": ""})
    assert o == {"heartbeat": 30, "connection_pool": 48, "log_level": "info", "aggressive_pool": True, "tune": False}
    iran, foreign = backhaul_texts("10.1.2.1", 9900, "tok", "tcpmux", parse_ports("8072"), 90, True, o)
    assert "heartbeat = 30" in iran and 'log_level = "info"' in iran and "mux_con = 8" in iran and "keepalive_period = 90" in iran
    assert "connection_pool = 48" in foreign and "aggressive_pool = true" in foreign and 'log_level = "info"' in foreign
    plain = backhaul_texts("10.1.2.1", 9900, "tok", "tcp", parse_ports("8072"))[1]
    assert "connection_pool = 32" in plain and "aggressive_pool = false" in plain and "dial_timeout = 10" in plain

    for bad in ({"heartbeat": 1}, {"log_level": "loud"}, {"connection_pool": "many"}):
        with pytest.raises(ValueError):
            clean_bh_opts(bad)


def test_parse_facts():
    f = parse_facts("IPS=1.1.1.1/24 10.1.1.1/30\nLINKS=eth0 fi-ir1\nLISTEN=22 9900\nUNITS=backhaul-iran-tcpmux-9900.service\nBROKEN=\nGRE_MOD=1\nBACKHAUL=v0.7.2\nIPTABLES=1\n")
    assert f["listen"] == {22, 9900} and f["gre_mod"] and f["backhaul"] and "fi-ir1" in f["links"] and f["broken"] == []


def test_actions_are_validated():
    assert "base64 -d" in render_action("put_file", {"PATH": "/etc/backhaul/iran-tcpmux-9901.toml", "B64": "aGk=", "MODE": "0600"})

    with pytest.raises(ValueError):
        render_action("put_file", {"PATH": "/etc/passwd", "B64": "aGk=", "MODE": "0600"})

    with pytest.raises(ValueError):
        render_action("remove_tunnel_files", {"NAME": "x; rm -rf /", "PORT": "9900", "TRANSPORT": "tcpmux"})

    assert "nikatel-gre-down-$n" in render_action("remove_tunnel_files", {"NAME": "fi-ir1", "PORT": "9900", "TRANSPORT": "tcpmux"})


def test_start_build_refused_in_read_only(client):
    with pytest.raises(ValueError):
        start_build(1, 2, "8072")

    r = client.post("/api/tunnels", json={"iran_id": 1, "foreign_id": 2, "ports": "8072"})
    assert r.status_code == 400 and "read-only" in r.json()["detail"]
    r = client.post("/api/tunnels", json={"iran_id": 1, "foreign_id": 2, "ports": "8072", "transport": "udp"})
    assert r.status_code == 400 and "transport must be" in r.json()["detail"]
    assert client.post("/api/tunnels", json={"iran_id": 1, "foreign_id": 2, "ports": "8072", "transport": "u dp"}).status_code == 422


def test_a_rebuild_interrupted_by_a_restart_is_resumed_once(client, monkeypatch):
    from nikatel.db import session
    from nikatel.models import Server, Tunnel
    from nikatel.services import builder

    with session() as db:
        a, b = Server(name="i1", host="10.0.0.1", role="iran"), Server(name="f1", host="10.0.0.2", role="foreign")
        db.add_all([a, b])
        db.flush()
        ids = (a.id, b.id)

    item = {"name": "f1-i1", "kind": "backhaul", "args": {"iran_id": ids[0], "foreign_id": ids[1], "ports_text": "80", "transport": "tcpmux", "mtu": 0, "keepalive": 75, "nodelay": True, "opts": {}}}
    builder.pending_put(item)
    calls = []
    monkeypatch.setattr(builder, "start_build", lambda *a, **k: calls.append((a, k)) or 1)
    assert builder.resume_pending() == 1 and calls[0][1]["forced_name"] == "f1-i1" and calls[0][0][2] == "80"

    with session() as db:       # the tunnel exists again (the build finished before the restart): nothing to resume, the note is dropped
        db.add(Tunnel(name="f1-i1", type="backhaul", iran_server_id=ids[0], foreign_server_id=ids[1]))

    assert builder.resume_pending() == 0 and len(calls) == 1
    builder.pending_put(item)
    builder.pending_drop("f1-i1")                    # a build that ended (well or badly) removes its note
    assert builder.resume_pending() == 0


def test_rebuild_with_a_fresh_link_avoids_the_old_inner_network(client, monkeypatch):
    from nikatel.config import get_settings
    from nikatel.db import session
    from nikatel.models import Server, Tunnel
    from nikatel.services import builder

    monkeypatch.setattr(get_settings(), "read_only", False)

    with session() as db:
        a, b = Server(name="i1", host="10.0.0.1", role="iran"), Server(name="f1", host="10.0.0.2", role="foreign")
        db.add_all([a, b])
        db.flush()
        t = Tunnel(name="f1-i1", type="backhaul", transport="tcpmux", iran_server_id=a.id, foreign_server_id=b.id, port=9909, forwarded_ports=[80],
                   record={"network": "10.237.77.0/30", "mtu": 1360, "keepalive": 75, "opts": {"tune": True}, "port_mapping": "80"})
        db.add(t)
        db.flush()
        tid = t.id

    seen = {}
    monkeypatch.setattr(builder, "delete_tunnel", lambda tunnel_id, keep_record=False: ["removed"])
    monkeypatch.setattr(builder, "pending_put", lambda item: seen.setdefault("pending", item))
    monkeypatch.setattr(builder, "start_build", lambda iran_id, foreign_id, ports, transport, mtu, keepalive, forced_name="", nodelay=True, opts=None: seen.update(opts=dict(opts or {}), name=forced_name) or 5)
    assert builder.rebuild_tunnel(tid, fresh_link=True) == 5
    assert seen["opts"]["avoid_net"] == "10.237.77.0/30" and seen["opts"]["tune"] is True and seen["name"] == "f1-i1"
    seen.clear()
    builder.rebuild_tunnel(tid)
    assert "avoid_net" not in seen["opts"]                                                                  # a plain rebuild keeps its old behaviour
    net, _, _ = builder.allocate_network(["10.237.77.0/30"])
    assert net != "10.237.77.0/30"


def test_retune_changes_two_lines_only_and_only_for_multiplexed_tunnels():
    from nikatel.services.builder import PortMap, backhaul_texts
    from nikatel.services.retune import POOL, STREAMBUF, retune_texts

    iran, foreign = backhaul_texts("10.9.9.1", 9901, "tok", "tcpmux", [PortMap(80)], 75, True, {"connection_pool": 16, "mux_streambuffer": 262144})
    i2, f2, changed = retune_texts(iran, foreign)
    assert changed and f"connection_pool = {POOL}" in f2 and f"mux_streambuffer = {STREAMBUF}" in f2 and f"mux_streambuffer = {STREAMBUF}" in i2
    assert [ln for ln in f2.splitlines() if ln not in foreign.splitlines()] == [f"connection_pool = {POOL}", f"mux_streambuffer = {STREAMBUF}"]       # nothing else moved
    assert retune_texts(i2, f2)[2] is False                                                                                                  # already tuned: left alone
    big_i, big_f = backhaul_texts("10.9.9.1", 9901, "tok", "tcpmux", [PortMap(80)], 75, True, {"connection_pool": 48, "mux_streambuffer": 1048576})
    assert retune_texts(big_i, big_f)[2] is False                                                                                            # an operator's bigger value is never lowered
    plain_i, plain_f = backhaul_texts("10.9.9.1", 9901, "tok", "tcp", [PortMap(80)])
    assert retune_texts(plain_i, plain_f) == (plain_i, plain_f, False)                                                                       # a plain tcp tunnel is not touched
    assert retune_texts("garbage", "garbage") == ("garbage", "garbage", False)
