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
    assert 'remote_addr = "10.9.9.1:9901"' in foreign and "connection_pool = 16" in foreign
    iran_tcp, _ = backhaul_texts("10.9.9.1", 9901, "tok", "tcp", [PortMap(80)])
    assert "mux_con" not in iran_tcp


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
    assert client.post("/api/tunnels", json={"iran_id": 1, "foreign_id": 2, "ports": "8072", "transport": "udp"}).status_code == 422
