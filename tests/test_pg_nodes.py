"""PasarGuard Nodes tab: the join with our servers, the pure helpers, every action against a fake panel, the backups, the unified flow "make this server a node", the API (roadmap #94)."""
import ipaddress
import json
import os
import time
import types
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from nikatel import db as nkdb
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, Event, Panel, Server, ServerSample
from nikatel.services import autopilot, extras, panels, pg_nodes
from nikatel.services.panels import PanelError
from nikatel.ssh import SSHError


def make_cert(cn="node-a.example.test", days=3650, san=("localhost", "203.0.113.10")):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)

    def entry(s):
        try:
            return x509.IPAddress(ipaddress.ip_address(s))
        except ValueError:
            return x509.DNSName(s)

    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([entry(s) for s in san]), critical=False).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


CERT_A = make_cert("node-a.example.test")
CERT_B = make_cert("node-b.example.test")
KEY_A = "123e4567-e89b-42d3-a456-426614174000"
KEY_B = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
NO_CERT = "no certificate found: install the PasarGuard node first (or it has not made one yet)"


class FakePG:
    """The parts of the PasarGuard API the Nodes tab uses, with the behaviours that matter: a PUT that forgets api_port writes 62051, a PUT starts the core again, a delete drops the node."""

    def __init__(self):
        self.nodes: dict[int, dict] = {}
        self.calls: list[tuple] = []
        self.restarts: list[int] = []
        self.cores = [{"id": 1, "name": "Default Core Config", "type": "xray"}]
        self.live: dict[int, dict | None] = {}
        self.usage: dict[int, list] = {}
        self.settings = {"min_node_version": "v1.0.0"}
        self.fail: dict[str, int] = {}
        self.on_get_node = None
        self.next = 1

    def add(self, name, address, **kw) -> int:
        i, self.next = self.next, self.next + 1
        node = {"id": i, "name": name, "address": address, "port": 62050, "api_port": 62051, "usage_coefficient": 1.0, "connection_type": "grpc", "server_ca": CERT_A, "keep_alive": 60,
                "core_config_id": 1, "api_key": KEY_A, "data_limit": 0, "data_limit_reset_strategy": "no_reset", "reset_time": -1, "default_timeout": 10, "internal_timeout": 15,
                "proxy_url": "", "xray_version": "26.3.27", "node_version": "0.5.4", "status": "connected", "message": "", "uplink": 100, "downlink": 900,
                "lifetime_uplink": 100, "lifetime_downlink": 900}
        node.update(kw)
        self.nodes[i] = node
        return i

    def writes(self):
        return [x for x in self.calls if x[0] != "GET"]

    def _dup(self, name, me=None):
        if any(n["name"] == name and n["id"] != me for n in self.nodes.values()):
            raise PanelError(f'HTTP 409: Node "{name}" already exists', 409)

    def __call__(self, c, method, path, **kw):
        body = kw.get("body")
        self.calls.append((method, path, body))
        base, _, query = path.partition("?")

        for key, code in self.fail.items():
            verb, _, where = key.rpartition(" ")                       # "POST /api/node" fails that route only, "/api/nodes" fails every route that starts so

            if (verb and verb == method and base == where) or (not verb and base.startswith(where)):
                raise PanelError(f"HTTP {code}: refused by the fake", code)

        parts = [p for p in base.split("/") if p][1:]

        if method == "GET" and parts == ["nodes"]:
            return {"nodes": [dict(n) for n in self.nodes.values()], "total": len(self.nodes)}

        if method == "GET" and parts == ["node", "settings"]:
            return dict(self.settings)

        if method == "GET" and parts == ["nodes", "realtime_stats"]:
            return {str(i): self.live.get(i) for i in self.nodes}

        if method == "GET" and parts == ["cores", "simple"]:
            return {"cores": self.cores, "total": len(self.cores)}

        if method == "GET" and parts == ["node", "usage"]:
            q = dict(x.split("=", 1) for x in query.split("&"))
            return {"period": q["period"], "stats": {q["node_id"]: self.usage.get(int(q["node_id"]), [])}}

        if method == "POST" and parts == ["node"]:
            self._dup(body["name"])
            i = self.add(body["name"], body["address"], **{k: v for k, v in body.items() if k not in ("name", "address")}, status="connecting", xray_version=None, node_version=None, uplink=0, downlink=0)
            return dict(self.nodes[i])

        if method == "POST" and parts == ["nodes", "bulk", "enable"]:
            for i in body["ids"]:
                self.nodes[i]["status"] = "connecting"

            return {"nodes": [self.nodes[i]["name"] for i in body["ids"]], "count": len(body["ids"])}

        if method == "POST" and parts == ["nodes", "bulk", "disable"]:
            for i in body["ids"]:
                self.nodes[i]["status"] = "disabled"

            return {"nodes": [self.nodes[i]["name"] for i in body["ids"]], "count": len(body["ids"])}

        if parts[:1] == ["node"] and len(parts) >= 2 and parts[1].isdigit():
            nid, rest = int(parts[1]), parts[2:]
            node = self.nodes.get(nid)

            if node is None:
                raise PanelError("HTTP 404: Node not found", 404)

            if method == "GET" and not rest:
                if self.on_get_node:
                    self.on_get_node(node)

                return dict(node)

            if method == "GET" and rest == ["realtime_stats"]:
                if self.live.get(nid) is None:
                    raise PanelError("HTTP 406: Node is not connected", 406)

                return self.live[nid]

            if method == "PUT" and not rest:
                self._dup(body.get("name"), nid)

                for k in ("name", "address", "port", "usage_coefficient", "connection_type", "server_ca", "keep_alive", "core_config_id", "api_key", "data_limit",
                          "data_limit_reset_strategy", "reset_time", "default_timeout", "internal_timeout"):
                    if body.get(k) is not None:
                        node[k] = body[k]

                node["api_port"] = body.get("api_port", 62051)              # the inherited default of the panel's model: written when the body forgets it
                node.update(xray_version=None, node_version=None, message=None)

                if node["status"] not in ("disabled", "limited"):
                    node["status"] = "connecting"
                    self.restarts.append(nid)

                return dict(node)

            if method == "DELETE" and not rest:
                del self.nodes[nid]
                return None

            if method == "PUT" and rest == ["sync"]:
                if node["status"] != "connected":
                    raise PanelError("HTTP 406: Node is not connected", 406)

                return None

            if method == "POST" and rest == ["reconnect"]:
                self.restarts.append(nid)
                return {}

            if method == "POST" and rest == ["reset"]:
                node.update(uplink=0, downlink=0)
                return dict(node)

            if method == "POST" and rest in (["update"], ["core_update"], ["geofiles"]):
                return {}

        raise AssertionError(f"unexpected call {method} {path}")


class FakeServers:
    """The node certificates the servers would give over SSH (pasarguard_cert)."""

    def __init__(self):
        self.certs: dict[int, str] = {}
        self.silent: set[int] = set()
        self.calls: list[int] = []

    def cert(self, server_id):
        self.calls.append(server_id)

        if server_id in self.silent:
            raise SSHError("connection refused")

        if server_id not in self.certs:
            raise ValueError(NO_CERT)

        return {"cert": self.certs[server_id]}


@pytest.fixture
def world(monkeypatch):
    fake, ssh = FakePG(), FakeServers()
    monkeypatch.setattr(panels, "call", fake)
    monkeypatch.setattr(extras, "pasarguard_cert", ssh.cert)
    monkeypatch.setattr(pg_nodes, "POLL", 0)
    monkeypatch.setattr(get_settings(), "read_only", False)
    return types.SimpleNamespace(pg=fake, ssh=ssh)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No real DNS, and no backup files left over from another test."""
    monkeypatch.setattr(pg_nodes, "resolve_names", lambda names, timeout=3.0: {})
    d = get_settings().data_dir / "pg-backups"

    if d.exists():
        for f in d.glob("nodes-p*.json"):
            f.unlink()


def add_server(name, host, role="foreign", online=True, enabled=True):
    with session() as db:
        s = Server(name=name, host=host, role=role, online=online, enabled=enabled)
        db.add(s)
        db.flush()
        return s.id


def add_sample(sid, ts, cpu=10.0, ram=20.0):
    with session() as db:
        db.add(ServerSample(server_id=sid, ts=ts, cpu=cpu, ram=ram, disk=30.0, load1=0.5, rx_mbps=1.0, tx_mbps=2.0, conn=7))


def add_panel(typ="pasarguard", name="alpha"):
    with session() as db:
        p = Panel(name=name, type=typ, url="https://panel-a.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


@pytest.fixture
def pid():
    return add_panel()


@pytest.fixture
def C(pid):
    return {"id": pid, "type": "pasarguard"}


# ------------------------------------------------------------------ pure: addresses, names, keys, fields
def test_addresses_are_compared_the_way_the_panel_means_them():
    assert pg_nodes.norm("  Node-B.Example.TEST. ") == "node-b.example.test" and pg_nodes.norm("[2001:DB8::1]") == "2001:db8::1" and pg_nodes.norm(None) == ""
    assert pg_nodes.ip_of("2001:0db8:0000:0000:0000:0000:0000:0001") == "2001:db8::1" and pg_nodes.ip_of(" 203.0.113.10 ") == "203.0.113.10"
    assert pg_nodes.ip_of("node-b.example.test") == "" and pg_nodes.ip_of("203.0.113.256") == "" and pg_nodes.ip_of("") == ""
    assert pg_nodes.check_address(" Node-B.Example.test ") == "node-b.example.test" and pg_nodes.check_address("localhost") == "localhost"
    assert pg_nodes.check_address("[2001:db8::1]") == "2001:db8::1" and pg_nodes.check_address("203.0.113.10") == "203.0.113.10"

    for bad in ("", "  ", "not a host", "no_tld", "a..example.test", "x" * 300 + ".example.test", "203.0.113.999", "http://node.example.test", "node.example.test/x"):
        with pytest.raises(ValueError):
            pg_nodes.check_address(bad)


def test_names_and_keys():
    assert pg_nodes.check_name("  node-a 1 ") == "node-a 1"

    for bad in ("", "x" * 41, "a/b", "a;b", "<b>"):
        with pytest.raises(ValueError):
            pg_nodes.check_name(bad)

    assert pg_nodes.check_key(KEY_A.upper()) == KEY_A and pg_nodes.check_key(KEY_A.replace("-", "")) == KEY_A

    for bad in ("", "abc", "not-a-uuid-at-all-not-a-uuid-at-all", None):
        with pytest.raises(ValueError):
            pg_nodes.check_key(bad)


def test_the_key_of_a_node_this_panel_installs_is_derived_not_kept():
    a = pg_nodes.node_key(1, 5)
    assert a == pg_nodes.node_key(1, 5) and a != pg_nodes.node_key(1, 6) and a != pg_nodes.node_key(2, 5)       # the same on every try, one per panel and server
    u = uuid.UUID(a)
    assert u.version == 4 and str(u) == a                                                                      # what the installer would make: a UUID the panel accepts
    assert pg_nodes.check_key(a) == a


def test_every_field_is_checked_the_way_the_panel_checks_it():
    ok = {"name": "n1", "address": "203.0.113.5", "port": "62050", "api_port": 62051, "usage_coefficient": "1.5", "connection_type": "REST", "keep_alive": 60, "core_config_id": "2",
          "data_limit": 0, "data_limit_reset_strategy": "month", "reset_time": -1, "default_timeout": 10, "internal_timeout": 15}
    out = {k: pg_nodes.check_field(k, v) for k, v in ok.items()}
    assert out["port"] == 62050 and out["usage_coefficient"] == 1.5 and out["connection_type"] == "rest" and out["core_config_id"] == 2

    bad = {"port": 0, "api_port": 70000, "usage_coefficient": -1, "connection_type": "tcp", "keep_alive": -1, "core_config_id": 0, "data_limit": -5,
           "data_limit_reset_strategy": "hourly", "reset_time": -2, "default_timeout": 2, "internal_timeout": 61}

    for k, v in bad.items():
        with pytest.raises(ValueError):
            pg_nodes.check_field(k, v)

    with pytest.raises(ValueError):
        pg_nodes.check_field("status", "disabled")                         # a status is changed by enable / disable, not by a field
    with pytest.raises(ValueError):
        pg_nodes.check_field("usage_coefficient", float("nan"))


# ------------------------------------------------------------------ pure: certificates
def test_a_certificate_is_read_like_the_panel_reads_it():
    block, info = pg_nodes.read_cert("  \n" + CERT_A + "\n  ")
    assert block.startswith("-----BEGIN CERTIFICATE-----") and block.endswith("-----END CERTIFICATE-----") and len(block) < 2048
    assert "node-a.example.test" in info["subject"] and info["days_left"] > 3000 and len(info["fingerprint"]) == 64 and "203.0.113.10" in info["san"] and "localhost" in info["san"]

    with pytest.raises(ValueError, match="private key"):
        pg_nodes.read_cert(CERT_A + "\n-----BEGIN " + "PRIVATE KEY-----\nabc\n-----END " + "PRIVATE KEY-----")

    with pytest.raises(ValueError, match="paste the whole certificate"):
        pg_nodes.read_cert("hello")

    with pytest.raises(ValueError, match="not a valid certificate"):
        pg_nodes.read_cert("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----")

    with pytest.raises(ValueError, match="2048"):
        pg_nodes.read_cert("-----BEGIN CERTIFICATE-----\n" + "A" * 2100 + "\n-----END CERTIFICATE-----")

    assert pg_nodes.cert_summary("junk") is None and pg_nodes.cert_summary(None) is None and pg_nodes.cert_summary(CERT_A)["fingerprint"]


def test_two_certificates_are_the_same_by_their_fingerprint_not_their_spelling():
    assert pg_nodes.same_cert(CERT_A, CERT_A.replace("\n", "\r\n")) and pg_nodes.same_cert(CERT_A, "x\n" + CERT_A + "\ny")
    assert not pg_nodes.same_cert(CERT_A, CERT_B) and not pg_nodes.same_cert("", CERT_A) and not pg_nodes.same_cert(None, None)
    assert pg_nodes.same_cert("junk text", "junk  text") and not pg_nodes.same_cert("junk text", "other")


# ------------------------------------------------------------------ pure: rows
def test_a_node_row_says_set_or_not_set_for_everything_secret():
    raw = {"id": 7, "name": "n1", "address": "203.0.113.5", "port": 62050, "api_port": 62051, "status": "Connected", "xray_version": "26.3.27", "node_version": "0.5.4",
           "usage_coefficient": 1.5, "server_ca": CERT_A, "api_key": KEY_A, "proxy_url": "http://user:secret@proxy.example.test:3128", "uplink": 5, "downlink": 9, "message": None}
    row = pg_nodes.public_node(raw)
    assert row["id"] == 7 and row["status"] == "connected" and row["xray"] == "26.3.27" and row["version"] == "0.5.4" and row["coefficient"] == 1.5 and row["message"] == ""
    assert row["has_key"] is True and row["has_ca"] is True and row["has_proxy"] is True
    blob = json.dumps(row)
    assert KEY_A not in blob and "BEGIN CERTIFICATE" not in blob and "secret" not in blob and "proxy.example.test" not in blob
    empty = pg_nodes.public_node({"id": True, "server_ca": "", "api_key": None})
    assert empty["id"] is None and empty["has_key"] is False and empty["has_ca"] is False and empty["coefficient"] == 1.0 and empty["reset_time"] == -1


def test_a_row_never_crashes_on_an_answer_it_does_not_know():
    for junk in (None, "text", 5, [], {"id": "x", "port": "y", "uplink": None, "usage_coefficient": "n/a", "core_config_id": "1"}):
        row = pg_nodes.public_node(junk)
        assert row["id"] is None and row["port"] == 0 and row["coefficient"] == 1.0 and row["core_config_id"] is None

    assert pg_nodes.live_row(None) is None and pg_nodes.live_row("x") is None
    live = pg_nodes.live_row({"mem_total": 4000, "mem_used": 1000, "cpu_usage": 4.56852, "cpu_cores": 2, "incoming_bandwidth_speed": 10, "outgoing_bandwidth_speed": 20, "uptime": 99})
    assert live == {"cpu": 4.6, "ram": 25.0, "mem_total": 4000, "cores": 2, "rx": 10, "tx": 20, "uptime": 99}
    assert pg_nodes.live_row({"mem_total": 0})["ram"] is None and pg_nodes.live_row({})["cpu"] is None


SERVERS = [{"id": 1, "name": "node-a", "host": "203.0.113.10", "role": "foreign"}, {"id": 2, "name": "node-b", "host": "node-b.example.test", "role": "foreign"},
           {"id": 3, "name": "node-c", "host": "2001:db8::10", "role": "foreign"}, {"id": 4, "name": "iran-a", "host": "198.51.100.40", "role": "iran"}]


def test_a_node_and_a_server_are_one_when_the_address_says_so():
    nodes = [{"name": "n1", "address": "203.0.113.10"}, {"name": "n2", "address": " 203.0.113.10 "}, {"name": "n3", "address": "NODE-B.example.test."},
             {"name": "n4", "address": "2001:0db8:0000:0000:0000:0000:0000:0010"}, {"name": "n5", "address": "alias.example.test"}, {"name": "node-c", "address": "198.51.100.99"},
             {"name": "n7", "address": "198.51.100.77"}, {"name": "n8", "address": ""}]
    pairs, free = pg_nodes.match_servers(nodes, SERVERS, {"alias.example.test": ["198.51.100.40"], "node-b.example.test": ["203.0.113.20"]})
    assert [(p["server"] or {}).get("id") for p in pairs] == [1, 1, 2, 3, 4, None, None, None]                 # two nodes may sit on one server
    assert [p["how"] for p in pairs] == ["address", "address", "address", "address", "dns", "", "", ""]
    assert free == [] and all(p["hint"] is None for p in pairs)                                                # the same name as node-c, but its server is taken by n4: no hint


def test_an_exact_match_beats_a_dns_match_and_the_lowest_server_wins_a_tie():
    servers = [{"id": 5, "name": "s5", "host": "web.example.test", "role": "foreign"}, {"id": 6, "name": "s6", "host": "203.0.113.20", "role": "foreign"}, {"id": 7, "name": "s7", "host": "web2.example.test", "role": "foreign"}]
    resolved = {"web.example.test": ["203.0.113.20"], "web2.example.test": ["203.0.113.20"]}
    pairs, free = pg_nodes.match_servers([{"name": "x", "address": "203.0.113.20"}], servers, resolved)
    assert pairs[0]["server"]["id"] == 6 and pairs[0]["how"] == "address" and [s["id"] for s in free] == [5, 7]        # the server with that very address, not the names that point at it
    pairs, _ = pg_nodes.match_servers([{"name": "x", "address": "203.0.113.20"}], [servers[2], servers[0]], resolved)
    assert pairs[0]["server"]["id"] == 5 and pairs[0]["how"] == "dns"                                                   # two names, one address: the lower id
    pairs, _ = pg_nodes.match_servers([{"name": "x", "address": "web.example.test"}], [servers[1]], resolved)
    assert pairs[0]["server"]["id"] == 6 and pairs[0]["how"] == "dns"                                                   # a node with a name, a server with an address
    pairs, _ = pg_nodes.match_servers([{"name": "x", "address": "web.example.test"}], [servers[1]], {})
    assert pairs[0]["server"] is None                                                                                  # the DNS did not answer: no guess


def test_a_server_with_the_same_name_is_a_hint_never_a_match():
    servers = [{"id": 9, "name": "node-f", "host": "203.0.113.50", "role": "foreign"}]
    pairs, free = pg_nodes.match_servers([{"name": "Node-F", "address": "198.51.100.250"}], servers, None)
    assert pairs[0]["server"] is None and pairs[0]["how"] == "" and pairs[0]["hint"]["id"] == 9 and [s["id"] for s in free] == [9]


def test_the_nodes_of_other_panels_are_put_on_the_servers_they_run_on():
    others = [{"panel": "beta", "type": "marzban", "name": "mz-1", "address": "203.0.113.10", "status": "connected"}, {"panel": "beta", "type": "marzban", "name": "mz-2", "address": "NODE-B.example.test", "status": "error"},
              {"panel": "gamma", "type": "pasarguard", "name": "pg-1", "address": "alias.example.test", "status": "connected"}, {"panel": "beta", "type": "marzban", "name": "mz-3", "address": "198.51.100.250", "status": "connected"}]
    on = pg_nodes.others_on(others, SERVERS, {"alias.example.test": ["203.0.113.10"]})
    assert on == {1: [{"panel": "beta", "type": "marzban", "node": "mz-1", "status": "connected"}, {"panel": "gamma", "type": "pasarguard", "node": "pg-1", "status": "connected"}],
                  2: [{"panel": "beta", "type": "marzban", "node": "mz-2", "status": "error"}]}                       # a name that resolves to the server counts; an address no server has is nobody's
    assert pg_nodes.others_on([], SERVERS) == {}


def test_only_the_names_the_matching_needs_are_looked_up():
    nodes = [{"address": "203.0.113.10"}, {"address": "Alias.example.test"}, {"address": "node-b.example.test"}, {"address": ""}]
    assert pg_nodes.names_to_resolve(nodes, SERVERS) == ["alias.example.test", "node-b.example.test"]
    assert pg_nodes.names_to_resolve([{"address": "203.0.113.10"}], [{"host": "203.0.113.10"}]) == []


def test_the_dns_is_asked_once_in_ten_minutes_and_never_for_long(monkeypatch):
    pg_nodes._DNS.clear()
    monkeypatch.undo()                                                                                          # the autouse stub of resolve_names is not wanted here
    asked = []
    monkeypatch.setattr(pg_nodes, "_lookup", lambda n: asked.append(n) or (["203.0.113.20"] if n == "a.example.test" else []))
    assert pg_nodes.resolve_names(["a.example.test", "b.example.test"]) == {"a.example.test": ["203.0.113.20"], "b.example.test": []}
    assert pg_nodes.resolve_names(["a.example.test", "b.example.test"]) == {"a.example.test": ["203.0.113.20"], "b.example.test": []} and sorted(asked) == ["a.example.test", "b.example.test"]

    def slow(name):
        time.sleep(0.6)
        return ["203.0.113.30"]

    monkeypatch.setattr(pg_nodes, "_lookup", slow)
    t0 = time.time()
    assert pg_nodes.resolve_names(["slow.example.test"], timeout=0.05) == {"slow.example.test": []} and time.time() - t0 < 0.4        # a lookup that hangs does not hold the page
    pg_nodes._DNS.clear()


# ------------------------------------------------------------------ pure: the bodies sent to the panel
def test_the_body_of_a_new_node_is_complete_and_checked():
    body = pg_nodes.create_body({"name": "n1", "address": "203.0.113.5", "core_config_id": 1, "api_key": KEY_A, "server_ca": "junk\n" + CERT_A})
    assert body["port"] == 62050 and body["api_port"] == 62051 and body["connection_type"] == "grpc" and body["keep_alive"] == 60 and body["usage_coefficient"] == 1.0
    assert body["server_ca"].startswith("-----BEGIN CERTIFICATE-----") and body["server_ca"].endswith("-----END CERTIFICATE-----") and body["api_key"] == KEY_A
    assert set(body) >= {"name", "address", "port", "api_port", "usage_coefficient", "connection_type", "keep_alive", "core_config_id", "data_limit", "data_limit_reset_strategy",
                         "reset_time", "default_timeout", "internal_timeout", "api_key", "server_ca"}

    for missing in ("name", "address", "core_config_id", "api_key", "server_ca"):
        with pytest.raises(ValueError, match=missing.replace("_", " ")):
            pg_nodes.create_body({k: v for k, v in {"name": "n1", "address": "203.0.113.5", "core_config_id": 1, "api_key": KEY_A, "server_ca": CERT_A}.items() if k != missing})

    with pytest.raises(ValueError, match="private key"):
        pg_nodes.create_body({"name": "n1", "address": "203.0.113.5", "core_config_id": 1, "api_key": KEY_A, "server_ca": "-----BEGIN " + "PRIVATE KEY-----\nx\n-----END " + "PRIVATE KEY-----"})


def test_an_edit_sends_back_every_field_so_the_panel_cannot_write_its_default_over_one():
    cur = {"id": 3, "name": "n1", "address": "203.0.113.5", "port": 62050, "api_port": 62099, "usage_coefficient": 1.0, "connection_type": "grpc", "keep_alive": 60, "core_config_id": 1,
           "data_limit": 0, "data_limit_reset_strategy": "no_reset", "reset_time": -1, "default_timeout": 10, "internal_timeout": 15, "api_key": KEY_A, "server_ca": CERT_A, "status": "connected",
           "xray_version": "26.3.27", "uplink": 5}
    body, changed = pg_nodes.edit_body(cur, {"usage_coefficient": 2})
    assert changed == ["usage_coefficient"] and body["usage_coefficient"] == 2.0 and body["api_port"] == 62099 and body["name"] == "n1" and body["port"] == 62050
    assert "api_key" not in body and "server_ca" not in body and "status" not in body and "uplink" not in body and "id" not in body       # secrets only when they change; never a status
    body, changed = pg_nodes.edit_body(cur, {"name": "n2", "address": "node-b.example.test", "api_key": KEY_B, "server_ca": CERT_B, "usage_coefficient": 1.0, "port": None})
    assert sorted(changed) == ["address", "api_key", "name", "server_ca"] and body["api_key"] == KEY_B and body["server_ca"].startswith("-----BEGIN CERTIFICATE-----")

    for nothing in ({}, {"name": "n1"}, {"usage_coefficient": 1}, {"api_key": KEY_A}, {"server_ca": CERT_A}, {"port": None}):
        with pytest.raises(ValueError, match="nothing to change"):
            pg_nodes.edit_body(cur, nothing)                                                                      # a PUT that changes nothing still restarts the core

    with pytest.raises(ValueError, match="cannot change"):
        pg_nodes.edit_body(cur, {"status": "disabled"})
    with pytest.raises(ValueError):
        pg_nodes.edit_body(cur, {"port": 70000})


def test_a_saved_node_gives_the_body_that_puts_it_back():
    raw = {"id": 3, "name": "n1", "address": "203.0.113.5", "port": 62050, "api_port": 62051, "api_key": KEY_A, "server_ca": CERT_A, "proxy_url": "http://proxy.example.test:3128", "status": "connected", "uplink": 4}
    put, post = pg_nodes.snapshot_body(raw, False), pg_nodes.snapshot_body(raw, True)
    assert put["api_key"] == KEY_A and put["server_ca"] == CERT_A and "proxy_url" not in put and "status" not in put and "uplink" not in put and "id" not in put
    assert post["proxy_url"] == "http://proxy.example.test:3128" and post["api_port"] == 62051
    assert "proxy_url" not in pg_nodes.snapshot_body({**raw, "proxy_url": ""}, True)


def test_usage_rows_and_the_event_stream_are_read_tolerantly():
    ans = {"stats": {"3": [{"period_start": "2026-10-12T00:00:00Z", "uplink": 5, "downlink": 9}, "junk", {"uplink": "x"}]}}
    assert pg_nodes.usage_rows(ans, 3) == [{"t": "2026-10-12T00:00:00Z", "up": 5, "down": 9}, {"t": "", "up": 0, "down": 0}]
    assert pg_nodes.usage_rows({"stats": {"-1": [{"period_start": "t", "uplink": 1, "downlink": 2}]}}, 3) == [{"t": "t", "up": 1, "down": 2}]          # a single list is the node's
    assert pg_nodes.usage_rows({"stats": {"4": [], "5": []}}, 3) == [] and pg_nodes.usage_rows(None, 3) == [] and pg_nodes.usage_rows({"stats": [{"uplink": 1}]}, 3)[0]["up"] == 1
    lines = ["data: 2026/10/14 [Warning] one", "", ": keep-alive", "event: log", "data:no space", "id: 5", "data: ", "x"]
    assert list(pg_nodes.parse_sse(lines)) == ["2026/10/14 [Warning] one", "no space", ""]


# ------------------------------------------------------------------ the list: nodes joined with our servers
def test_the_list_joins_every_node_with_the_server_it_runs_on(world, C):
    a = add_server("node-a", "203.0.113.10")
    b = add_server("node-b", "203.0.113.11")
    free = add_server("node-c", "203.0.113.12")
    add_server("iran-a", "198.51.100.40", role="iran")
    off = add_server("node-d", "203.0.113.13", online=False, enabled=False)
    now = int(time.time())

    for i in range(30):
        add_sample(a, now - 3000 + i * 100, cpu=1.0 + i, ram=50.0)                                         # many samples: only the newest counts
    add_sample(b, now - 60, cpu=77.5, ram=61.0)
    add_sample(free, now - 90, cpu=5.0, ram=9.0)
    n1 = world.pg.add("nd-one", "203.0.113.10", status="connected", uplink=111, downlink=222)
    n2 = world.pg.add("nd-two", "203.0.113.11", status="error", message="the certificate is not valid")
    n3 = world.pg.add("elsewhere", "198.51.100.200", status="disabled")
    world.pg.live ={n1: {"mem_total": 4000, "mem_used": 1000, "cpu_cores": 2, "cpu_usage": 3.25, "incoming_bandwidth_speed": 7, "outgoing_bandwidth_speed": 8, "uptime": 60}, n2: None}
    out = pg_nodes.overview(C)
    rows = {r["id"]: r for r in out["nodes"]}
    assert rows[n1]["server"]["name"] == "node-a" and rows[n1]["match"] == "address" and rows[n1]["server"]["cpu"] == 30.0 and rows[n1]["server"]["ram"] == 50.0       # the newest sample (cpu 1 + 29)
    assert rows[n1]["live"]["cpu"] == 3.2 and rows[n1]["live"]["ram"] == 25.0 and rows[n1]["downlink"] == 222 and rows[n1]["xray"] == "26.3.27"
    assert rows[n2]["server"]["name"] == "node-b" and rows[n2]["server"]["cpu"] == 77.5 and rows[n2]["status"] == "error" and rows[n2]["message"] == "the certificate is not valid" and rows[n2]["live"] is None
    assert rows[n3]["server"] is None and rows[n3]["match"] == "" and rows[n3]["live"] is None
    assert [s["name"] for s in out["free"]] == ["node-c", "node-d"] and out["free"][0]["cpu"] == 5.0 and out["free"][1]["enabled"] is False and off in {s["id"] for s in out["free"]}   # foreign only: iran-a is not offered
    assert free in {s["id"] for s in out["free"]} and b not in {s["id"] for s in out["free"]}
    assert out["totals"] == {"nodes": 3, "connected": 1, "with_server": 2, "free": 2, "candidates": 2} and out["min_node_version"] == "v1.0.0" and out["cores"] == [{"id": 1, "name": "Default Core Config", "type": "xray"}]
    blob = json.dumps(out)
    assert KEY_A not in blob and "BEGIN CERTIFICATE" not in blob and "api_key" not in blob and "server_ca" not in blob
    assert world.pg.writes() == []                                                                          # looking changes nothing


def other_panel(nodes):
    with session() as db:
        db.add(Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="a", secrets={"password": "x"}, status="up", nodes=nodes))


def test_a_server_that_carries_a_node_of_another_panel_is_marked_and_not_offered(world, C):
    add_server("node-a", "203.0.113.10")
    c1 = add_server("node-c", "203.0.113.12")
    d = add_server("node-d", "203.0.113.13")
    other_panel([{"id": 1, "name": "mz-1", "address": "203.0.113.12", "status": "connected"}, {"id": 2, "name": "mz-2", "address": "203.0.113.99", "status": "error"}, {"name": "no address"}, "junk"])
    world.pg.add("nd-one", "203.0.113.10")
    out = pg_nodes.overview(C)
    assert [s["name"] for s in out["free"]] == ["node-d", "node-c"]                                          # the servers a node can go to first
    assert out["free"][0]["elsewhere"] == [] and out["free"][1]["elsewhere"] == [{"panel": "beta", "type": "marzban", "node": "mz-1", "status": "connected"}]
    assert out["totals"]["free"] == 2 and out["totals"]["candidates"] == 1 and out["nodes"][0]["server"]["elsewhere"] == []
    p = pg_nodes.make_plan(C, c1)
    assert not p["ok"] and "already carries a node of another panel: mz-1 of beta (marzban)" in p["problems"][0] and "62050" in p["problems"][0] and "62051" in p["problems"][0]
    assert pg_nodes.make_plan(C, d)["ok"]
    with pytest.raises(ValueError, match="carries a node of another panel"):
        pg_nodes.make_start(C, c1)
    world.ssh.certs[c1] = CERT_A                                                                              # a PasarGuard node is on it already: a warning, not a refusal
    p = pg_nodes.make_plan(C, c1)
    assert p["ok"] and "also a node of another panel: mz-1 of beta (marzban)" in p["warnings"][0]
    assert pg_nodes.register(C, c1)["warnings"] == ["This server is also a node of another panel: mz-1 of beta (marzban)."]


def test_the_list_matches_a_node_that_has_a_name_through_the_dns(world, C):
    add_server("node-b", "203.0.113.11")
    world.pg.add("nb", "Node-B.example.test")
    asked = []

    def resolver(names):
        asked.append(names)
        return {"node-b.example.test": ["203.0.113.11"]}

    out = pg_nodes.overview(C, resolver)
    assert asked == [["node-b.example.test"]] and out["nodes"][0]["server"]["name"] == "node-b" and out["nodes"][0]["match"] == "dns" and out["free"] == []
    assert pg_nodes.overview(C)["nodes"][0]["server"] is None                                               # the stub resolves nothing: no guess


def test_the_samples_table_is_read_one_row_per_server_never_in_bulk(world, C):
    a = add_server("node-a", "203.0.113.10")
    now = int(time.time())

    with session() as db:
        db.add_all([ServerSample(server_id=a, ts=now - i, cpu=1.0, ram=2.0, disk=3.0, load1=0.1, rx_mbps=0.0, tx_mbps=0.0, conn=1) for i in range(400)])

    world.pg.add("nd-one", "203.0.113.10")
    seen = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        if "server_samples" in statement:
            seen.append(statement)

    from sqlalchemy import event
    event.listen(nkdb.engine(), "before_cursor_execute", spy)

    try:
        out = pg_nodes.overview(C)
    finally:
        event.remove(nkdb.engine(), "before_cursor_execute", spy)

    assert out["nodes"][0]["server"]["cpu"] == 1.0 and len(seen) == 1 and "LIMIT" in seen[0].upper() and "ORDER BY" in seen[0].upper()
    assert all(col in seen[0] for col in ("server_samples.ts", "server_samples.cpu", "server_samples.ram")) and "server_samples.id" not in seen[0]      # the columns of the table, nothing else


def test_the_list_survives_what_the_panel_does_not_answer(world, C):
    add_server("node-a", "203.0.113.10")
    world.pg.add("nd-one", "203.0.113.10")
    world.pg.fail.update({"/api/nodes/realtime_stats": 500, "/api/node/settings": 404, "/api/cores/simple": 500})
    out = pg_nodes.overview(C)
    assert len(out["nodes"]) == 1 and out["nodes"][0]["live"] is None and out["min_node_version"] == "" and out["cores"] == [] and len(out["warnings"]) == 3
    world.pg.fail = {"/api/nodes": 500}
    with pytest.raises(PanelError):
        pg_nodes.overview(C)                                                                                # the list itself is the one thing it cannot do without


def test_one_node_in_full(world, C):
    add_server("node-a", "203.0.113.10")
    n = world.pg.add("nd-one", "203.0.113.10")
    world.pg.live[n] = {"mem_total": 100, "mem_used": 50, "cpu_usage": 1.0}
    row = pg_nodes.node_get(C, n)
    assert row["name"] == "nd-one" and row["server"]["name"] == "node-a" and row["live"]["ram"] == 50.0 and row["has_key"] and KEY_A not in json.dumps(row)
    world.pg.live[n] = None
    assert pg_nodes.node_get(C, n)["live"] is None                                                           # a node that is not connected reports nothing
    with pytest.raises(PanelError):
        pg_nodes.node_get(C, 99)


# ------------------------------------------------------------------ logs and usage
def test_the_log_is_the_last_lines_of_the_event_stream(world, C, monkeypatch):
    seen = {}

    def stream(c, path, quiet=1.5, limit=8.0):
        seen["path"] = path
        yield from [f"data: line {i}" for i in range(700)] + ["", ": ping"]

    monkeypatch.setattr(pg_nodes, "_sse_lines", stream)
    out = pg_nodes.node_logs(C, 5, 9999)
    assert seen["path"] == "/api/node/5/logs" and len(out["lines"]) == 500 and out["lines"][-1] == "line 699" and out["lines"][0] == "line 200"        # at most 500, the newest
    assert len(pg_nodes.node_logs(C, 5, 1)["lines"]) == 10                                                   # at least 10
    monkeypatch.setattr(pg_nodes, "_sse_lines", lambda *a, **k: iter(["data: " + "x" * 2000]))
    assert len(pg_nodes.node_logs(C, 5)["lines"][0]) == 600                                                 # a very long line is cut


class FakeStream:
    def __init__(self, status=200, lines=(), end="quiet"):
        self.status_code, self.lines, self.end = status, list(lines), end

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        yield from self.lines

        if self.end == "quiet":
            raise httpx.ReadTimeout("quiet")

        if self.end == "broken":
            raise httpx.ReadError("reset")


def test_the_stream_reader_stops_when_the_node_is_quiet_and_logs_in_again_once(monkeypatch):
    tokens = []
    monkeypatch.setattr(panels, "_token", lambda c, fresh=False: tokens.append(fresh) or "tok")
    opened = []
    answers = iter([FakeStream(401), FakeStream(200, ["data: a", "", "data: b"])])
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: opened.append(k["headers"]) or next(answers))
    c = {"id": 1, "url": "https://panel-a.example.test", "verify_tls": True}
    assert list(pg_nodes._sse_lines(c, "/api/node/1/logs")) == ["data: a", "", "data: b"] and tokens == [False, True]          # 401: a new token, once; the quiet end is not an error
    assert opened[0]["Authorization"] == "Bearer tok" and opened[0]["Accept"] == "text/event-stream"
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeStream(404))
    with pytest.raises(PanelError, match="404"):
        list(pg_nodes._sse_lines(c, "/api/node/1/logs"))
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeStream(200, ["data: a"], end="broken"))
    with pytest.raises(PanelError, match="no answer"):
        list(pg_nodes._sse_lines(c, "/api/node/1/logs"))
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeStream(401))
    with pytest.raises(PanelError, match="401"):
        list(pg_nodes._sse_lines(c, "/api/node/1/logs"))                                                    # a second 401 is the answer


def test_the_stream_reader_sends_an_api_key_in_its_own_header(monkeypatch):
    key = "pg_key_11111111-2222-4333-8444-555555555555"
    monkeypatch.setattr(panels, "_token", lambda c, fresh=False: key)
    opened = []
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: opened.append(k["headers"]) or FakeStream(200, ["data: a"]))
    assert list(pg_nodes._sse_lines({"id": 1, "url": "https://panel-a.example.test", "verify_tls": True}, "/api/node/1/logs")) == ["data: a"]
    assert opened[0]["X-Api-Key"] == key and "Authorization" not in opened[0] and opened[0]["Accept"] == "text/event-stream"


def test_the_usage_of_a_node(world, C):
    n = world.pg.add("nd-one", "203.0.113.10")
    world.pg.usage[n] = [{"period_start": "2026-10-12T00:00:00Z", "uplink": 10, "downlink": 100}, {"period_start": "2026-10-13T00:00:00Z", "uplink": 20, "downlink": 200}]
    out = pg_nodes.node_usage(C, n, "day", 3)
    assert out["total_up"] == 30 and out["total_down"] == 300 and out["period"] == "day" and out["days"] == 3 and len(out["rows"]) == 2
    path = world.pg.calls[-1][1]
    assert path.startswith("/api/node/usage?") and f"node_id={n}" in path and "period=day" in path and "start=" in path and "end=" in path
    assert pg_nodes.node_usage(C, n, "hour", 100)["days"] == 14 and pg_nodes.node_usage(C, n, "month", 9999)["days"] == 366 and pg_nodes.node_usage(C, n, "day", 0)["days"] == 1
    with pytest.raises(ValueError, match="period"):
        pg_nodes.node_usage(C, n, "week")


# ------------------------------------------------------------------ the actions on one node
def test_add_a_node(world, C):
    out = pg_nodes.node_add(C, {"name": "n1", "address": "203.0.113.5", "core_config_id": 1, "api_key": KEY_A, "server_ca": CERT_A, "usage_coefficient": 1.5})
    assert out["name"] == "n1" and out["status"] == "connecting" and out["coefficient"] == 1.5 and KEY_A not in json.dumps(out)
    post = world.pg.writes()[0]
    assert post[0] == "POST" and post[1] == "/api/node" and post[2]["api_key"] == KEY_A and post[2]["server_ca"].startswith("-----BEGIN CERTIFICATE-----") and post[2]["api_port"] == 62051
    with pytest.raises(ValueError, match="exists already"):
        pg_nodes.node_add(C, {"name": "N1", "address": "203.0.113.6", "core_config_id": 1, "api_key": KEY_A, "server_ca": CERT_A})
    assert len(world.pg.writes()) == 1                                                                       # the second one never reached the panel
    with pytest.raises(ValueError):
        pg_nodes.node_add(C, {"name": "n2", "address": "bad address", "core_config_id": 1, "api_key": KEY_A, "server_ca": CERT_A})
    world.pg.fail = {"POST /api/node": 422}
    with pytest.raises(PanelError, match="422"):
        pg_nodes.node_add(C, {"name": "n3", "address": "203.0.113.7", "core_config_id": 1, "api_key": KEY_A, "server_ca": CERT_A})      # the panel's own refusal comes through


def test_an_edit_keeps_the_api_port_saves_the_node_first_and_can_be_undone(world, C):
    n = world.pg.add("nd-one", "203.0.113.10", api_port=62099, usage_coefficient=1.0)
    out = pg_nodes.node_edit(C, n, {"usage_coefficient": 2.5})
    assert out["changed"] == ["usage_coefficient"] and out["node"]["coefficient"] == 2.5 and world.pg.nodes[n]["api_port"] == 62099          # the panel would have written 62051
    assert world.pg.restarts == [n]                                                                         # and it did start the core again, which is why the page asks first
    saved = json.loads((get_settings().data_dir / "pg-backups" / out["backup"]).read_text(encoding="utf-8"))
    assert saved["kind"] == "edit" and saved["node"]["usage_coefficient"] == 1.0 and saved["node"]["api_key"] == KEY_A and saved["panel_id"] == C["id"]
    undone = pg_nodes.restore(C, out["backup"])
    assert undone["recreated"] is False and world.pg.nodes[n]["usage_coefficient"] == 1.0 and world.pg.nodes[n]["api_port"] == 62099 and undone["backup"]
    assert [b["kind"] for b in pg_nodes.backups(C)] == ["restore", "edit"]                                   # the state before the undo is kept too
    with pytest.raises(ValueError, match="nothing to change"):
        pg_nodes.node_edit(C, n, {"usage_coefficient": 1.0})
    before = len(world.pg.writes())
    with pytest.raises(ValueError, match="nothing to change"):
        pg_nodes.node_edit(C, n, {})
    assert len(world.pg.writes()) == before and len(pg_nodes.backups(C)) == 2                              # a refused edit writes no backup and nothing to the panel
    other = world.pg.add("nd-two", "203.0.113.11")
    with pytest.raises(PanelError, match="409"):
        pg_nodes.node_edit(C, other, {"name": "nd-one"})


def test_an_edit_can_change_the_key_and_the_certificate_without_ever_returning_them(world, C):
    n = world.pg.add("nd-one", "203.0.113.10")
    out = pg_nodes.node_edit(C, n, {"api_key": KEY_B, "server_ca": CERT_B})
    assert sorted(out["changed"]) == ["api_key", "server_ca"] and world.pg.nodes[n]["api_key"] == KEY_B and pg_nodes.same_cert(world.pg.nodes[n]["server_ca"], CERT_B)
    assert KEY_B not in json.dumps(out) and "BEGIN CERTIFICATE" not in json.dumps(out)


def test_a_delete_needs_confirm_saves_the_node_and_can_be_made_again(world, C):
    n = world.pg.add("nd-one", "203.0.113.10", uplink=5, downlink=7, proxy_url="http://proxy.example.test:3128")
    keep = world.pg.add("nd-two", "203.0.113.11")
    with pytest.raises(ValueError, match="confirm"):
        pg_nodes.node_delete(C, n, False)
    assert n in world.pg.nodes and world.pg.writes() == [] and pg_nodes.backups(C) == []
    out = pg_nodes.node_delete(C, n, True)
    assert out == {"deleted": "nd-one", "backup": out["backup"], "uplink": 5, "downlink": 7} and n not in world.pg.nodes and keep in world.pg.nodes
    assert world.pg.writes()[-1][:2] == ("DELETE", f"/api/node/{n}")
    listing = pg_nodes.backups(C)
    assert len(listing) == 1 and listing[0]["kind"] == "delete" and listing[0]["node"] == "nd-one" and listing[0]["address"] == "203.0.113.10"
    assert KEY_A not in json.dumps(listing) and "BEGIN CERTIFICATE" not in json.dumps(listing)                # the list never carries the secrets
    back = pg_nodes.restore(C, out["backup"])
    new = world.pg.nodes[back["node"]["id"]]
    assert back["recreated"] is True and new["name"] == "nd-one" and new["api_key"] == KEY_A and pg_nodes.same_cert(new["server_ca"], CERT_A) and new["proxy_url"] == "http://proxy.example.test:3128"
    with pytest.raises(ValueError, match="exists now"):
        pg_nodes.restore(C, out["backup"])                                                                   # not twice
    with pytest.raises(PanelError, match="404"):
        pg_nodes.node_delete(C, 999, True)


def test_restore_refuses_what_is_not_a_backup_of_this_panel(world, C, pid):
    n = world.pg.add("nd-one", "203.0.113.10")
    name = pg_nodes.node_delete(C, n, True)["backup"]

    for bad in ("", "../../etc/passwd", "nodes-p1-1760000000-n1-edit.json.bak", "nodes-p99-1760000000-n1-delete.json", "other.json", name + "x", None):
        with pytest.raises(ValueError, match="no such backup"):
            pg_nodes.restore(C, bad)

    with pytest.raises(ValueError, match="no such backup"):
        pg_nodes.restore({"id": pid + 1}, name)                                                              # another panel's file
    path = get_settings().data_dir / "pg-backups" / name
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be read"):
        pg_nodes.restore(C, name)
    path.write_text(json.dumps({"node": {"id": 1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="holds no node"):
        pg_nodes.restore(C, name)
    e = pg_nodes.node_add(C, {"name": "n9", "address": "203.0.113.9", "core_config_id": 1, "api_key": KEY_A, "server_ca": CERT_A})
    edit = pg_nodes.node_edit(C, e["id"], {"usage_coefficient": 3})["backup"]
    world.pg.nodes.pop(e["id"])
    with pytest.raises(ValueError, match="not in the panel any more"):
        pg_nodes.restore(C, edit)


def test_backups_are_private_files_and_only_the_last_twenty_of_a_panel_stay(world, C):
    n = world.pg.add("nd-one", "203.0.113.10")
    names = [pg_nodes.backup_node(C, world.pg.nodes[n], "edit") for _ in range(25)]
    assert len(set(names)) == 25                                                                              # two in one second keep both
    other = [pg_nodes.backup_node({"id": 77}, world.pg.nodes[n], "delete") for _ in range(3)]
    mine = pg_nodes.backups(C)
    assert len(mine) == 20 and mine[0]["name"] == names[-1] and names[0] not in {b["name"] for b in mine} and all(b["node"] == "nd-one" for b in mine)
    assert len(pg_nodes.backups({"id": 77})) == 3 and all((get_settings().data_dir / "pg-backups" / o).exists() for o in other)
    mode = (get_settings().data_dir / "pg-backups" / names[-1]).stat().st_mode & 0o777

    if os.name == "posix":
        assert mode == 0o600                                                                                  # only this server can read the key inside


def test_reconnect_resync_and_enable_use_the_panels_own_routes(world, C):
    n = world.pg.add("nd-one", "203.0.113.10")
    assert pg_nodes.node_reconnect(C, n) == {"name": "nd-one"} and world.pg.writes()[-1][:2] == ("POST", f"/api/node/{n}/reconnect") and world.pg.restarts == [n]
    pg_nodes.node_sync(C, n)
    assert world.pg.writes()[-1][:2] == ("PUT", f"/api/node/{n}/sync?flush_users=true")
    pg_nodes.node_sync(C, n, False)
    assert world.pg.writes()[-1][:2] == ("PUT", f"/api/node/{n}/sync?flush_users=false")
    assert pg_nodes.node_state(C, n, False) == {"name": "nd-one", "enabled": False} and world.pg.nodes[n]["status"] == "disabled"
    assert world.pg.writes()[-1] == ("POST", "/api/nodes/bulk/disable", {"ids": [n]})
    with pytest.raises(PanelError, match="406"):
        pg_nodes.node_sync(C, n)                                                                              # the panel refuses a node that is not connected: its reason comes through
    pg_nodes.node_state(C, n, True)
    assert world.pg.writes()[-1] == ("POST", "/api/nodes/bulk/enable", {"ids": [n]}) and world.pg.nodes[n]["status"] == "connecting"
    with pytest.raises(PanelError, match="404"):
        pg_nodes.node_reconnect(C, 999)
    events = [e.text for e in _events()]
    assert any("restarted" in t for t in events) and any("disabled" in t for t in events) and any("enabled" in t for t in events)


def _events():
    with session() as db:
        return list(db.query(Event).order_by(Event.id))


def test_the_counters_can_be_reset_and_the_node_can_be_asked_to_update_itself(world, C):
    n = world.pg.add("nd-one", "203.0.113.10", uplink=5, downlink=7)
    assert pg_nodes.node_reset_usage(C, n) == {"name": "nd-one", "uplink": 5, "downlink": 7} and world.pg.nodes[n]["uplink"] == 0
    assert pg_nodes.node_maintenance(C, n, "node")["said"].startswith("software") and world.pg.writes()[-1][:2] == ("POST", f"/api/node/{n}/update")
    pg_nodes.node_maintenance(C, n, "core", core_version="v25.8.31")
    assert world.pg.writes()[-1] == ("POST", f"/api/node/{n}/core_update", {"core_version": "v25.8.31"})
    pg_nodes.node_maintenance(C, n, "core")
    assert world.pg.writes()[-1][2] == {"core_version": "latest"}
    pg_nodes.node_maintenance(C, n, "geofiles", region="russia")
    assert world.pg.writes()[-1] == ("POST", f"/api/node/{n}/geofiles", {"region": "russia"})
    before = len(world.pg.writes())

    for kw in ({"what": "reboot"}, {"what": "core", "core_version": "1.2"}, {"what": "core", "core_version": "latest; rm"}, {"what": "geofiles", "region": "mars"}):
        with pytest.raises(ValueError):
            pg_nodes.node_maintenance(C, n, **kw)

    assert len(world.pg.writes()) == before                                                                   # a refused request never reaches the panel


# ------------------------------------------------------------------ the certificate: the panel's copy against the node on our server
def test_the_certificate_in_the_panel_is_compared_with_the_one_on_the_server(world, C):
    a = add_server("node-a", "203.0.113.10")
    n = world.pg.add("nd-one", "203.0.113.10", server_ca=CERT_A)
    world.ssh.certs[a] = CERT_A
    cmp = pg_nodes.cert_compare(C, n)
    assert cmp["same"] is True and cmp["problem"] == "" and cmp["server"]["name"] == "node-a" and cmp["in_panel"]["fingerprint"] == cmp["on_server"]["fingerprint"]
    assert "BEGIN CERTIFICATE" not in json.dumps(cmp)                                                         # a summary, never the text
    assert pg_nodes.cert_apply(C, n) == {"changed": False, "backup": "", "node": "nd-one"} and world.pg.writes() == []
    world.ssh.certs[a] = CERT_B                                                                               # the node made a new certificate (renew-cert, a re-install)
    assert pg_nodes.cert_compare(C, n)["same"] is False
    out = pg_nodes.cert_apply(C, n)
    assert out["changed"] is True and out["backup"] and pg_nodes.same_cert(world.pg.nodes[n]["server_ca"], CERT_B)
    assert [b["kind"] for b in pg_nodes.backups(C)] == ["cert"] and pg_nodes.restore(C, out["backup"])["recreated"] is False and pg_nodes.same_cert(world.pg.nodes[n]["server_ca"], CERT_A)


def test_the_certificate_check_says_why_it_cannot_be_done(world, C):
    a = add_server("node-a", "203.0.113.10")
    lone = world.pg.add("alone", "198.51.100.200")
    n = world.pg.add("nd-one", "203.0.113.10")
    assert "No server of this panel" in pg_nodes.cert_compare(C, lone)["problem"]
    assert NO_CERT.split(":")[0] in pg_nodes.cert_compare(C, n)["problem"]
    world.ssh.silent.add(a)
    assert "did not answer over SSH" in pg_nodes.cert_compare(C, n)["problem"]

    for node in (lone, n):
        with pytest.raises(ValueError):
            pg_nodes.cert_apply(C, node)

    assert world.pg.writes() == []


def test_the_certificate_of_a_server_is_public_and_comes_with_what_it_says(world):
    a = add_server("node-a", "203.0.113.10")
    world.ssh.certs[a] = CERT_A
    out = pg_nodes.server_cert(a)
    assert out["cert"].strip() == CERT_A.strip() and out["server"]["name"] == "node-a" and out["days_left"] > 3000 and out["fingerprint"]
    with pytest.raises(ValueError, match="server not found"):
        pg_nodes.server_cert(999)
    world.ssh.silent.add(a)
    with pytest.raises(pg_nodes.ServerSilent):
        pg_nodes.server_cert(a)


# ------------------------------------------------------------------ the unified flow: a server of ours becomes a node
def test_the_plan_lists_the_steps_and_changes_nothing(world, C):
    a = add_server("node-a", "203.0.113.10")
    plan = pg_nodes.make_plan(C, a)
    assert plan["ok"] is True and plan["problems"] == [] and plan["installed"] is False and plan["server"]["name"] == "node-a"
    assert plan["steps"][0].startswith("Install the PasarGuard node") and len(plan["steps"]) == 4 and "203.0.113.10:62050" in plan["steps"][2]
    assert plan["options"] == {"name": "node-a", "usage_coefficient": 1.0, "core_config_id": 1, "port": 62050, "api_port": 62051, "connection_type": "grpc"}
    assert plan["min_node_version"] == "v1.0.0" and plan["cores"] == [{"id": 1, "name": "Default Core Config", "type": "xray"}]
    assert world.pg.writes() == [] and world.ssh.calls == [a]                                                 # one read-only look at the server, nothing else


def test_the_plan_sees_a_node_that_is_installed_already(world, C):
    a = add_server("node-a", "203.0.113.10")
    world.ssh.certs[a] = CERT_A
    plan = pg_nodes.make_plan(C, a, {"name": "other", "port": 62060, "api_port": 62061, "connection_type": "rest"})
    assert plan["installed"] is True and plan["steps"][0].startswith("The node is installed on the server already") and plan["ok"]
    assert plan["options"]["port"] == 62060 and plan["options"]["connection_type"] == "rest" and plan["options"]["name"] == "other"         # when it is there, its ports are the operator's to say
    plan = pg_nodes.make_plan(C, a, {"port": 62060})
    assert plan["options"]["name"] == "node-a"


def test_the_plan_says_what_stands_in_the_way(world, C, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    off = add_server("node-off", "203.0.113.11", online=False)
    disabled = add_server("node-dis", "203.0.113.12", enabled=False)
    ir = add_server("iran-a", "198.51.100.40", role="iran")
    taken = add_server("node-t", "203.0.113.13")
    world.pg.add("existing", "203.0.113.13")
    world.pg.add("node-a", "198.51.100.200")
    p = pg_nodes.make_plan(C, off)
    assert not p["ok"] and "offline" in p["problems"][0] and p["installed"] is None and off not in world.ssh.calls                    # a server known to be offline is not asked
    assert "turned off" in pg_nodes.make_plan(C, disabled)["problems"][0] and disabled not in world.ssh.calls
    assert any("already the node existing" in x for x in pg_nodes.make_plan(C, taken)["problems"])
    assert any("node-a exists already" in x for x in pg_nodes.make_plan(C, a)["problems"])                      # the name is taken by another node
    assert pg_nodes.make_plan(C, a, {"name": "node-a2"})["ok"]
    world.ssh.silent.add(ir)
    p = pg_nodes.make_plan(C, ir)
    assert any("did not answer over SSH" in x for x in p["problems"]) and p["installed"] is None and p["warnings"] and "Iran" in p["warnings"][0]
    monkeypatch.setattr(get_settings(), "read_only", True)
    p = pg_nodes.make_plan(C, a, {"name": "node-a2"})
    assert not p["ok"] and "read-only" in p["problems"][0]                                                    # the install needs a panel that may change servers
    world.ssh.certs[a] = CERT_A
    assert pg_nodes.make_plan(C, a, {"name": "node-a2"})["ok"]                                                # ... but adding a node that is there needs only the panel
    monkeypatch.setattr(get_settings(), "read_only", False)
    with pytest.raises(ValueError, match="server not found"):
        pg_nodes.make_plan(C, 999)
    with pytest.raises(ValueError):
        pg_nodes.make_plan(C, a, {"name": "bad/name"})
    assert world.pg.writes() == []


def test_the_plan_needs_a_core_to_run_and_asks_when_there_are_several(world, C):
    a = add_server("node-a", "203.0.113.10")
    world.pg.cores = []
    assert "no core config" in pg_nodes.make_plan(C, a)["problems"][0]
    world.pg.cores = [{"id": 1, "name": "One", "type": "xray"}, {"id": 2, "name": "Two", "type": "xray"}]
    p = pg_nodes.make_plan(C, a)
    assert "several core configs" in p["problems"][0] and [x["id"] for x in p["cores"]] == [1, 2]
    assert pg_nodes.make_plan(C, a, {"core_config_id": 2})["options"]["core_config_id"] == 2
    assert "no core config 7" in pg_nodes.make_plan(C, a, {"core_config_id": 7})["problems"][0]
    world.pg.cores = [{"id": 1, "name": "One", "type": "xray"}, {"id": 2, "name": "Wire", "type": "wg"}]
    assert pg_nodes.make_plan(C, a)["options"]["core_config_id"] == 1                                         # the only Xray one


def test_starting_the_flow_is_an_action_and_refuses_with_the_first_problem(world, C, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *args, **kw: started.append((label, fn, args, kw)) or 7)
    assert pg_nodes.make_start(C, a) == 7
    label, fn, args, kw = started[0]
    assert label == "node node-a" and fn is pg_nodes._make_story and args[0] == C["id"] and args[1] == a and args[2]["installed"] is False and args[2]["name"] == "node-a"
    assert kw == {"action": "pgnode", "source": "manual", "allow_read_only": False}
    world.ssh.certs[a] = CERT_A
    pg_nodes.make_start(C, a, {"name": "n2"})
    assert started[1][3]["allow_read_only"] is True and started[1][2][2]["installed"] is True                  # nothing on the server changes: allowed while the panel is read-only
    world.pg.add("n3", "203.0.113.10")
    with pytest.raises(ValueError, match="already the node n3"):
        pg_nodes.make_start(C, a)
    assert len(started) == 2


def test_a_second_click_while_the_first_run_goes_is_refused_in_plain_words(world, C, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    monkeypatch.setitem(autopilot._BUSY, "node node-a", 5)
    monkeypatch.setattr(autopilot, "_start", lambda *args, **kw: pytest.fail("it must not start twice"))
    with pytest.raises(ValueError, match="being made a node right now"):
        pg_nodes.make_start(C, a)


class Installer:
    """extras.start as the Install view runs it: an action of its own with a story, ending ok or not; a good one leaves the node's certificate on the server."""

    def __init__(self, world, server_id, ok=True, cert=CERT_A):
        self.world, self.server_id, self.ok, self.cert, self.keys = world, server_id, ok, cert, []

    def __call__(self, server_id, items, key=""):
        self.keys.append((server_id, list(items), key))
        story = ["Installing: PasarGuard node (they keep running on the server even if the panel restarts)", "PasarGuard node: starting (the official installer, Docker and the node; a few minutes)…"]
        story += ["PasarGuard node: installed. Next: in this server's Install view press Show certificate, copy it into the PasarGuard panel (Nodes, address 203.0.113.10) and set the node there.", "Done."] if self.ok \
            else ["PasarGuard node: failed - DOWNLOAD_FAILED", "Done with problems: look at the lines above."]

        with session() as db:
            e = ActionLog(ref="install node-a", action="install", source="manual", story=story, ok=self.ok, finished_at=int(time.time()))
            db.add(e)
            db.flush()
            aid = e.id

        if self.ok and self.cert:
            self.world.ssh.certs[server_id] = self.cert

        return aid


def story(monkeypatch):
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append((line, done, ok)))
    return said


def test_the_whole_flow_installs_reads_the_certificate_adds_the_node_and_waits(world, C, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    inst = Installer(world, a)
    monkeypatch.setattr(extras, "start", inst)
    seen = {"n": 0}

    def connect(node):
        seen["n"] += 1

        if seen["n"] >= 2 and node["status"] == "connecting":
            node.update(status="connected", xray_version="26.3.27", node_version="0.5.4")

    world.pg.on_get_node = connect
    said = story(monkeypatch)
    plan = pg_nodes.make_plan(C, a, {"usage_coefficient": 2})
    pg_nodes._make_story(5, C["id"], a, dict(plan["options"], installed=False))
    lines = [x[0] for x in said]
    key = pg_nodes.node_key(C["id"], a)
    assert inst.keys == [(a, ["pasarguard_node"], key)]                                                       # the install gets the key this panel derives, nothing the operator typed
    assert [x[:3] for x in lines if x[:1].isdigit()] == ["1/4", "2/4", "3/4", "4/4"]
    assert any("starting" in x and x.startswith("    ") for x in lines) and not any("Next: in this server's Install view" in x or x.strip() == "Done." for x in lines)      # the install's lines are repeated, its old advice is not
    post = next(w for w in world.pg.writes() if w[0] == "POST")
    assert post[1] == "/api/node" and post[2]["api_key"] == key and post[2]["address"] == "203.0.113.10" and post[2]["name"] == "node-a" and post[2]["usage_coefficient"] == 2.0
    assert post[2]["port"] == 62050 and post[2]["api_port"] == 62051 and post[2]["connection_type"] == "grpc" and pg_nodes.same_cert(post[2]["server_ca"], CERT_A)
    assert said[-1][1] is True and said[-1][2] is True and "is connected" in said[-1][0] and "node-a is a node now" in said[-1][0]
    assert key not in " ".join(lines) and "BEGIN CERTIFICATE" not in " ".join(lines)                           # no secret in the story the page shows
    assert any("made the node" in e.text for e in _events())


def test_when_the_node_is_there_already_the_install_is_skipped(world, C, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    world.ssh.certs[a] = CERT_A
    monkeypatch.setattr(extras, "start", lambda *a, **k: pytest.fail("no install when the node is there"))
    world.pg.on_get_node = lambda node: node.update(status="connected")
    said = story(monkeypatch)
    pg_nodes._make_story(5, C["id"], a, dict(pg_nodes.make_plan(C, a)["options"], installed=True))
    assert any("skipped" in x[0] for x in said) and said[-1][2] is True and any(w[0] == "POST" for w in world.pg.writes())


def test_a_failed_install_stops_the_flow_before_anything_reaches_the_panel(world, C, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    monkeypatch.setattr(extras, "start", Installer(world, a, ok=False))
    said = story(monkeypatch)
    pg_nodes._make_story(5, C["id"], a, dict(pg_nodes.make_plan(C, a)["options"], installed=False))
    assert said[-1][1] is True and said[-1][2] is False and "Stopped at step 1" in said[-1][0] and "Nothing was added to the panel" in said[-1][0] and "DOWNLOAD_FAILED" in said[-1][0]
    assert world.pg.writes() == []

    def refuse(*a, **k):
        raise ValueError("the panel is read-only")

    monkeypatch.setattr(extras, "start", refuse)
    said.clear()
    pg_nodes._make_story(5, C["id"], a, dict(pg_nodes.make_plan(C, a)["options"], installed=False))
    assert "could not start" in said[-1][0] and said[-1][2] is False and world.pg.writes() == []


def test_a_server_that_gives_no_certificate_after_the_install_stops_at_step_two(world, C, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    monkeypatch.setattr(extras, "start", Installer(world, a, cert=""))
    said = story(monkeypatch)
    pg_nodes._make_story(5, C["id"], a, dict(pg_nodes.make_plan(C, a)["options"], installed=False))
    assert "Stopped at step 2" in said[-1][0] and "no certificate found" in said[-1][0] and said[-1][2] is False and len(world.ssh.calls) >= 6 and world.pg.writes() == []      # it tried five times


def test_a_panel_that_refuses_the_node_stops_at_step_three_and_says_what_is_left(world, C, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    monkeypatch.setattr(extras, "start", Installer(world, a))
    opts = dict(pg_nodes.make_plan(C, a)["options"], installed=False)
    world.pg.fail = {"POST /api/node": 409}
    said = story(monkeypatch)
    pg_nodes._make_story(5, C["id"], a, opts)
    assert "Stopped at step 3" in said[-1][0] and "409" in said[-1][0] and "the install is skipped" in said[-1][0] and "Add node" in said[-1][0] and said[-1][2] is False


def test_a_node_that_never_connects_is_left_in_the_panel_with_the_way_to_fix_it(world, C, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    monkeypatch.setattr(extras, "start", Installer(world, a))
    world.pg.on_get_node = lambda node: node.update(status="error", message="authentication failed")
    said = story(monkeypatch)
    pg_nodes._make_story(5, C["id"], a, dict(pg_nodes.make_plan(C, a)["options"], installed=False))
    last = said[-1][0]
    assert said[-1][2] is False and "status error: authentication failed" in last and "API key" in last and "62050" in last and "62051" in last and "Certificate" in last
    assert len(world.pg.nodes) == 1                                                                           # the node stays: Edit can set the right key
    world.pg.nodes.clear()
    monkeypatch.setattr(pg_nodes, "CONNECT_LIMIT", 0)
    world.pg.on_get_node = lambda node: node.update(status="connecting")
    said.clear()
    pg_nodes._make_story(6, C["id"], a, dict(pg_nodes.make_plan(C, a, {"name": "again"})["options"], installed=True))
    assert said[-1][2] is False and "status connecting" in said[-1][0]


def test_the_story_stops_with_a_sentence_when_the_server_or_the_panel_is_gone(world, C, monkeypatch):
    said = story(monkeypatch)
    pg_nodes._make_story(5, 999, 999, {"name": "x"})
    assert said[-1][2] is False and said[-1][1] is True and "Stopped" in said[-1][0]


def test_an_installed_node_can_be_registered_with_its_own_key_or_the_one_this_panel_made(world, C):
    a = add_server("node-a", "203.0.113.10")
    b = add_server("node-b", "203.0.113.11")
    world.ssh.certs[a] = CERT_A
    world.ssh.certs[b] = CERT_B
    out = pg_nodes.register(C, a)
    assert out["key"] == "made by this panel" and out["node"]["name"] == "node-a" and world.pg.nodes[out["node"]["id"]]["api_key"] == pg_nodes.node_key(C["id"], a) and KEY_A not in json.dumps(out)
    out = pg_nodes.register(C, b, {"name": "bee", "port": 62060, "api_port": 62061, "connection_type": "rest", "usage_coefficient": 2}, KEY_B)
    new = world.pg.nodes[out["node"]["id"]]
    assert out["key"] == "given" and new["api_key"] == KEY_B and new["port"] == 62060 and new["connection_type"] == "rest" and new["usage_coefficient"] == 2.0 and new["address"] == "203.0.113.11"
    with pytest.raises(ValueError, match="already the node"):
        pg_nodes.register(C, a)
    c2 = add_server("node-c", "203.0.113.12")
    with pytest.raises(ValueError, match="no certificate found"):
        pg_nodes.register(C, c2)                                                                              # not installed: the sentence says what to do
    world.ssh.certs[c2] = CERT_A
    with pytest.raises(ValueError, match="UUID"):
        pg_nodes.register(C, c2, None, "not a key")
    world.pg.cores = []
    with pytest.raises(ValueError, match="No core config|no core config"):
        pg_nodes.register(C, c2)
    world.ssh.silent.add(c2)
    world.pg.cores = [{"id": 1, "name": "One", "type": "xray"}]
    with pytest.raises(pg_nodes.ServerSilent):
        pg_nodes.register(C, c2)


def add_panel_with_nodes(typ, name, nodes):
    with session() as db:
        p = Panel(name=name, type=typ, url=f"https://{name}.example.test", username="admin", secrets={"password": "x"}, status="up", nodes=nodes)
        db.add(p)
        db.flush()
        return p.id


def test_the_servers_page_gets_the_nodes_of_every_panel_on_the_server_they_run_on(world, monkeypatch):
    a = add_server("fr1", "203.0.113.10")
    b = add_server("de1", "node-b.example.test")
    c = add_server("nl1", "203.0.113.30")
    pa = add_panel_with_nodes("pasarguard", "alpha", [{"id": 1, "name": "n1", "address": "203.0.113.10", "status": "connected", "message": ""},
                                                       {"id": 2, "name": "far", "address": "198.51.100.9", "status": "connected"},
                                                       {"id": 3, "name": "by-name", "address": "node-b.example.test", "status": "error", "message": "refused"}])
    pm = add_panel_with_nodes("marzban", "beta", [{"id": 7, "name": "m1", "address": "203.0.113.10", "status": "error", "message": "boom"}, {"name": "no-id", "address": "203.0.113.30", "status": "connected"}, {"id": 9, "address": ""}])
    got = pg_nodes.by_server()
    assert sorted(got) == sorted([a, b, c])                                                                    # nl1 carries "no-id" (a node without an id still counts), fr1 two nodes, de1 one
    assert [(r["panel_id"], r["panel"], r["type"], r["node_id"], r["name"], r["status"]) for r in got[a]] == [(pa, "alpha", "pasarguard", 1, "n1", "connected"), (pm, "beta", "marzban", 7, "m1", "error")]
    assert got[b][0]["match"] == "address" and got[b][0]["message"] == "refused"
    assert got[c][0]["node_id"] is None and got[c][0]["name"] == "no-id"                                       # a node without an id is shown, but nothing can be done to it from here
    assert all("api_key" not in r for rows in got.values() for r in rows)
    assert not any(r["name"] == "far" for rows in got.values() for r in rows)                                  # a node on an address that is none of our servers is nobody's


def test_the_dns_matches_a_node_that_has_a_name_to_the_server_with_the_ip(world, monkeypatch):
    s = add_server("fr1", "203.0.113.10")
    add_panel_with_nodes("pasarguard", "alpha", [{"id": 1, "name": "n1", "address": "n1.example.test", "status": "connected"}])
    monkeypatch.setattr(pg_nodes, "resolve_names", lambda names, timeout=3.0: {"n1.example.test": ["203.0.113.10"]})
    got = pg_nodes.by_server()
    assert list(got) == [s] and got[s][0]["match"] == "dns"


def test_the_api_gives_the_servers_page_its_nodes_without_asking_any_panel(client, world):
    s = add_server("fr1", "203.0.113.10")
    add_panel_with_nodes("pasarguard", "alpha", [{"id": 1, "name": "n1", "address": "203.0.113.10", "status": "connected"}])
    j = client.get("/api/servers/panel-nodes").json()
    assert j["ok"] is True and list(j["servers"]) == [str(s)] and j["servers"][str(s)][0]["name"] == "n1" and world.pg.calls == []      # the health job already knows: no call to a panel


# ------------------------------------------------------------------ the API
def test_the_api_lists_nodes_and_refuses_what_is_not_a_pasarguard_panel(client, world, pid):
    add_server("node-a", "203.0.113.10")
    world.pg.add("nd-one", "203.0.113.10")
    j = client.get(f"/api/panels/{pid}/pg/nodes").json()
    assert j["ok"] is True and j["nodes"][0]["server"]["name"] == "node-a" and j["totals"]["nodes"] == 1 and "api_key" not in json.dumps(j)
    mz = add_panel("marzban", "beta")
    r = client.get(f"/api/panels/{mz}/pg/nodes")
    assert r.status_code == 400 and r.json()["code"] == "bad_request" and "PasarGuard" in r.json()["error"]
    assert client.get("/api/panels/9999/pg/nodes").status_code == 404
    world.pg.fail = {"/api/nodes": 500}
    r = client.get(f"/api/panels/{pid}/pg/nodes")
    assert r.status_code == 502 and r.json()["code"] == "upstream_failed" and "500" in r.json()["error"]            # the panel is down: a sentence, not a crash


def test_the_api_needs_a_sign_in(client, pid):
    from fastapi.testclient import TestClient

    stranger = TestClient(client.app)
    assert stranger.get(f"/api/panels/{pid}/pg/nodes").status_code == 401 and stranger.post(f"/api/panels/{pid}/pg/nodes/make", json={"server_id": 1}).status_code == 401


def test_the_api_adds_edits_and_deletes_with_the_checks_in_front(client, world, pid):
    body = {"name": "n1", "address": "203.0.113.5", "core_config_id": 1, "api_key": KEY_A, "server_ca": CERT_A, "usage_coefficient": 1.5, "data_limit_gb": 2}
    j = client.post(f"/api/panels/{pid}/pg/nodes", json=body).json()
    nid = j["node"]["id"]
    assert j["ok"] and j["node"]["name"] == "n1" and world.pg.nodes[nid]["data_limit"] == 2 * 1024 ** 3 and KEY_A not in json.dumps(j)
    assert client.post(f"/api/panels/{pid}/pg/nodes", json=body).status_code == 400                         # the same name
    assert client.post(f"/api/panels/{pid}/pg/nodes", json={**body, "name": "n2", "server_ca": "junk"}).status_code == 400
    assert client.post(f"/api/panels/{pid}/pg/nodes", json={**body, "name": "n2", "port": 70000}).status_code == 422
    assert client.post(f"/api/panels/{pid}/pg/nodes", json={**body, "name": "n2", "connection_type": "tcp"}).status_code == 422
    assert client.post(f"/api/panels/{pid}/pg/nodes", json={**body, "name": "n2", "api_key": "short"}).status_code == 422
    assert client.post(f"/api/panels/{pid}/pg/nodes", json={k: v for k, v in body.items() if k != "core_config_id"}).status_code == 422
    r = client.put(f"/api/panels/{pid}/pg/nodes/{nid}", json={"usage_coefficient": 3})
    assert r.status_code == 200 and r.json()["changed"] == ["usage_coefficient"] and r.json()["backup"] and world.pg.nodes[nid]["api_port"] == 62051
    assert client.put(f"/api/panels/{pid}/pg/nodes/{nid}", json={"usage_coefficient": 3}).status_code == 400    # nothing to change
    assert client.put(f"/api/panels/{pid}/pg/nodes/{nid}", json={"port": 0}).status_code == 422
    assert client.put(f"/api/panels/{pid}/pg/nodes/{nid}", json={"status": "disabled"}).status_code == 400 and world.pg.nodes[nid]["status"] != "disabled"      # a status is not a field of an edit
    assert client.put(f"/api/panels/{pid}/pg/nodes/999", json={"usage_coefficient": 3}).status_code == 502   # the panel's 404 comes through
    assert client.delete(f"/api/panels/{pid}/pg/nodes/{nid}").status_code == 400 and nid in world.pg.nodes
    r = client.delete(f"/api/panels/{pid}/pg/nodes/{nid}?confirm=true")
    assert r.status_code == 200 and r.json()["deleted"] == "n1" and nid not in world.pg.nodes
    b = client.get(f"/api/panels/{pid}/pg/nodes/backups").json()["backups"]
    assert [x["kind"] for x in b] == ["delete", "edit"] and "api_key" not in json.dumps(b)                  # newest first
    r = client.post(f"/api/panels/{pid}/pg/nodes/restore/{next(x['name'] for x in b if x['kind'] == 'delete')}")
    assert r.status_code == 200 and r.json()["recreated"] is True and any(n["name"] == "n1" for n in world.pg.nodes.values())
    assert client.post(f"/api/panels/{pid}/pg/nodes/restore/nope.json").status_code == 400


def test_the_api_acts_on_one_node(client, world, pid, monkeypatch):
    n = world.pg.add("nd-one", "203.0.113.10")
    base = f"/api/panels/{pid}/pg/nodes/{n}"
    assert client.get(base).json()["node"]["name"] == "nd-one"
    assert client.post(base + "/reconnect").json() == {"ok": True, "name": "nd-one"} and world.pg.restarts == [n]
    assert client.post(base + "/sync").status_code == 200 and client.post(base + "/sync", json={"flush_users": False}).status_code == 200
    assert world.pg.writes()[-1][1].endswith("flush_users=false")
    assert client.post(base + "/state", json={"enabled": False}).json()["enabled"] is False and world.pg.nodes[n]["status"] == "disabled"
    assert client.post(base + "/state", json={}).status_code == 422
    assert client.post(base + "/reset-usage").json()["uplink"] == 100 and world.pg.nodes[n]["uplink"] == 0
    assert client.post(base + "/maintenance", json={"what": "core", "core_version": "v25.8.31"}).status_code == 200
    assert client.post(base + "/maintenance", json={"what": "core", "core_version": "x; reboot"}).status_code == 422
    assert client.post(base + "/maintenance", json={"what": "format"}).status_code == 422
    assert client.post(base + "/maintenance", json={"what": "geofiles", "region": "mars"}).status_code == 422
    world.pg.usage[n] = [{"period_start": "t", "uplink": 1, "downlink": 2}]
    j = client.get(base + "/usage?period=hour&days=2").json()
    assert j["total_up"] == 1 and j["total_down"] == 2 and j["period"] == "hour"
    assert client.get(base + "/usage?period=week").status_code == 422
    monkeypatch.setattr(pg_nodes, "_sse_lines", lambda c, path, **k: iter(["data: one", "", "data: two"]))
    assert client.get(base + "/logs").json()["lines"] == ["one", "two"]
    assert client.post(f"/api/panels/{pid}/pg/nodes/999/reconnect").status_code == 502


def test_the_api_reads_and_copies_the_certificate_of_a_node_server(client, world, pid):
    a = add_server("node-a", "203.0.113.10")
    n = world.pg.add("nd-one", "203.0.113.10", server_ca=CERT_A)
    world.ssh.certs[a] = CERT_B
    j = client.get(f"/api/panels/{pid}/pg/nodes/{n}/cert").json()
    assert j["same"] is False and j["server"]["name"] == "node-a" and "BEGIN CERTIFICATE" not in json.dumps(j)
    j = client.get(f"/api/panels/{pid}/pg/nodes/servers/{a}/cert").json()
    assert j["cert"].strip() == CERT_B.strip() and j["server"]["name"] == "node-a"                           # the public certificate is what the copy button copies
    assert client.post(f"/api/panels/{pid}/pg/nodes/{n}/cert").json()["changed"] is True and pg_nodes.same_cert(world.pg.nodes[n]["server_ca"], CERT_B)
    assert client.post(f"/api/panels/{pid}/pg/nodes/{n}/cert").json()["changed"] is False
    assert client.get(f"/api/panels/{pid}/pg/nodes/servers/9999/cert").status_code == 400
    world.ssh.silent.add(a)
    r = client.get(f"/api/panels/{pid}/pg/nodes/servers/{a}/cert")
    assert r.status_code == 400 and "SSH" in r.json()["error"]


def test_the_api_makes_a_server_a_node_as_an_action(client, world, pid, monkeypatch):
    a = add_server("node-a", "203.0.113.10")
    j = client.post(f"/api/panels/{pid}/pg/nodes/make/plan", json={"server_id": a}).json()
    assert j["ok"] is True and j["installed"] is False and len(j["steps"]) == 4 and world.pg.writes() == []
    assert client.post(f"/api/panels/{pid}/pg/nodes/make/plan", json={"server_id": 999}).status_code == 400
    assert client.post(f"/api/panels/{pid}/pg/nodes/make/plan", json={"server_id": a, "name": "x" * 50}).status_code == 422
    assert client.post(f"/api/panels/{pid}/pg/nodes/make/plan", json={}).status_code == 422
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *args, **kw: started.append((label, args[1])) or 11)
    r = client.post(f"/api/panels/{pid}/pg/nodes/make", json={"server_id": a, "usage_coefficient": 2})
    assert r.status_code == 202 and r.json() == {"ok": True, "action_id": 11} and started == [("node node-a", a)]
    world.pg.add("hold", "203.0.113.10")
    r = client.post(f"/api/panels/{pid}/pg/nodes/make", json={"server_id": a})
    assert r.status_code == 400 and "already the node hold" in r.json()["error"] and len(started) == 1


def test_the_api_registers_an_installed_node(client, world, pid):
    a = add_server("node-a", "203.0.113.10")
    r = client.post(f"/api/panels/{pid}/pg/nodes/register", json={"server_id": a})
    assert r.status_code == 400 and "no certificate found" in r.json()["error"]
    world.ssh.certs[a] = CERT_A
    r = client.post(f"/api/panels/{pid}/pg/nodes/register", json={"server_id": a, "api_key": KEY_B, "name": "bee", "port": 62060})
    j = r.json()
    assert r.status_code == 200 and j["key"] == "given" and j["node"]["name"] == "bee" and world.pg.nodes[j["node"]["id"]]["api_key"] == KEY_B and KEY_B not in json.dumps(j)
    assert client.post(f"/api/panels/{pid}/pg/nodes/register", json={"server_id": a}).status_code == 400


def test_the_static_routes_are_not_taken_for_a_node_id(client, world, pid):
    assert client.get(f"/api/panels/{pid}/pg/nodes/backups").status_code == 200
    assert client.get(f"/api/panels/{pid}/pg/nodes/not-a-number").status_code == 422


def test_every_route_has_one_short_sentence_for_a_docstring():
    from nikatel.api import pg_nodes as api

    routes = [r for r in api.router.routes if hasattr(r, "endpoint")]
    assert len(routes) >= 20

    for r in routes:
        doc = (r.endpoint.__doc__ or "").strip()
        assert doc and len(doc) <= 300, (r.path, len(doc))
        assert doc.endswith(".") and ". " not in doc, (r.path, doc)                                          # one sentence
        assert r.path.startswith("/panels/{panel_id}/pg/nodes"), r.path
