"""The layered look (1.69.290; the owner, 2026-11-19): a LIGHT look at every layer between a user and the internet - the node from abroad, the tunnel on its Iran server itself, the Iran network to it - in parallel, with a verdict
(the first layer that fails), a restart of a tunnel that resets new connections, and a small tag on a foreign server whose node really does not answer."""
import shutil
import socket
import subprocess
import threading
import time

import pytest

from nikatel import ssh
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Config, Server, Tunnel
from nikatel.services import actions, autopilot, pathprobe, swap, testbudget
from nikatel.services import settings as st


def R(tls, n=8, conn=None, err=""):
    return {"n": n, "conn": n if conn is None else conn, "tls": tls, "p50": 110, "p90": 130, "err": err}


# ------------------------------------------------------------------ the pure parts
def test_the_script_lines_are_parsed_and_a_bad_line_is_left_out():
    out = "PORT=11436 N=8 CONN=8 TLS=6 P50=626 P90=2585 ERR=ConnectionResetError\nnoise\nPORT=x N=1\nPORT=31436 N=8 CONN=8 TLS=8 P50=109 P90=122 ERR=\n"
    assert pathprobe.parse_probe(out) == {11436: {"n": 8, "conn": 8, "tls": 6, "p50": 626, "p90": 2585, "err": "ConnectionResetError"}, 31436: {"n": 8, "conn": 8, "tls": 8, "p50": 109, "p90": 122, "err": ""}}
    assert pathprobe.parse_probe("") == {} and pathprobe.parse_probe(None) == {}


def test_a_layer_passes_with_three_quarters_of_its_handshakes_and_none_is_not_measured():
    f = pathprobe.layer_state
    assert f(R(8)) == "ok" and f(R(6)) == "ok" and f(R(5)) == "bad" and f(R(0)) == "bad" and f(None) == "none" and f({}) == "none"
    assert pathprobe.merge([R(8), R(4, err="X"), None])["tls"] == 12 and pathprobe.merge([R(8), R(4, err="X"), None])["err"] == "X" and pathprobe.merge([None]) is None
    assert pathprobe.best([R(3), R(8), None]) == R(8) and pathprobe.best([None, {}]) is None                      # (a layer fails only when EVERY vantage fails)


def test_the_verdict_is_the_first_layer_that_fails_and_a_layer_that_was_not_measured_accuses_nobody():
    v = pathprobe.verdict
    assert v(R(8), R(8), None)[0] == "ok" and v(None, None, None)[0] == "unknown"
    assert v(R(1), R(8), R(8))[0] == "node" and v(R(1), R(1), None)[0] == "node"                                    # (the node first)
    code, text = v(R(8), R(5, err="ConnectionResetError"), None)
    assert code == "tunnel" and "only 5 of 8" in text and "ConnectionResetError" in text and "resets or stalls" in text
    assert v(None, R(2), None)[0] == "tunnel_or_node"                                                               # (the node was not measured: nobody is accused)
    assert v(R(8), R(8), R(3))[0] == "path" and v(R(8), R(8), R(7))[0] == "ok"


def test_a_verdict_changes_only_after_two_looks_in_a_row_that_agree():
    s, changed = pathprobe.settle(None, "ok", 100)
    assert s["code"] == "ok" and changed                                                                            # (the first look of all is taken at once when it finds nothing wrong)
    s0, changed0 = pathprobe.settle(None, "tunnel", 100)
    assert s0["code"] == "unknown" and not changed0                                                                 # (a first look that finds a fault is not believed yet)
    s0, changed0 = pathprobe.settle(s0, "tunnel", 200)
    assert s0["code"] == "tunnel" and changed0 and s0["since"] == 200
    s, changed = pathprobe.settle(s, "tunnel", 200)
    assert s["code"] == "ok" and not changed and s["pend"] == {"code": "tunnel", "n": 1}                            # (one noisy look is nothing)
    s, changed = pathprobe.settle(s, "ok", 300)
    assert s["code"] == "ok" and s["pend"]["code"] == "ok"
    s, _ = pathprobe.settle(s, "tunnel", 400)
    s, changed = pathprobe.settle(s, "tunnel", 500)
    assert s["code"] == "tunnel" and changed and s["since"] == 500


def test_the_node_port_is_the_target_of_the_user_port():
    rec = {"port_mapping": "8079:8075,1829:1820,11429:8680"}
    assert pathprobe.node_target(rec, 11429) == 8680 and pathprobe.node_target(rec, 8079) == 8075 and pathprobe.node_target(rec, 9999) == 9999
    assert pathprobe.node_target({"port_mapping": "8075,11434:8680"}, 8075) == 8075 and pathprobe.node_target(None, 1) == 1


def test_a_probe_against_a_server_that_hangs_up_is_a_failed_layer_and_stops_early(monkeypatch):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(50)
    port = srv.getsockname()[1]

    def hang_up():
        while True:
            try:
                c, _ = srv.accept()
                c.close()
            except OSError:
                return

    threading.Thread(target=hang_up, daemon=True).start()
    monkeypatch.setattr(pathprobe, "TIMEOUT", 2)
    r = pathprobe.handshakes("127.0.0.1", port, "example.test", 8)
    srv.close()
    assert r["n"] == 8 and r["tls"] == 0 and r["conn"] <= 4 and r["err"] and pathprobe.layer_state(r) == "bad"        # (it stops once the share cannot be reached: three failures)
    s2 = socket.socket()
    s2.bind(("127.0.0.1", 0))
    gone = s2.getsockname()[1]
    s2.close()                                                                                                      # (a port nobody listens on)
    closed = pathprobe.handshakes("127.0.0.1", gone, "example.test", 8)
    assert closed["conn"] == 0 and closed["tls"] == 0 and closed["err"] == "ConnectionRefusedError"


# ------------------------------------------------------------------ the script on a server
def test_the_script_takes_only_checked_parameters():
    ok = {"HOST": "127.0.0.1", "PORTS": "11436 31436", "SNI": "api.example.test", "COUNT": "8"}
    assert "11436 31436" in ssh._script_text("tls_probe", ok)

    for key, bad in (("HOST", "127.0.0.1;rm"), ("PORTS", "1; id"), ("SNI", "a b"), ("COUNT", "x"), ("PORTS", "")):
        with pytest.raises(ValueError):
            ssh._script_text("tls_probe", {**ok, key: bad})


def _script(host, ports, count=8):
    return ssh._script_text("tls_probe", {"HOST": host, "PORTS": " ".join(str(p) for p in ports), "SNI": "api.example.test", "COUNT": str(count)})


def test_the_script_counts_connects_and_handshakes_for_every_port_at_once():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(50)
    up = srv.getsockname()[1]
    s2 = socket.socket()
    s2.bind(("127.0.0.1", 0))
    down = s2.getsockname()[1]
    s2.close()                                                                                                    # (a port nobody listens on)

    def hang_up():
        while True:
            try:
                c, _ = srv.accept()
                c.close()
            except OSError:
                return

    threading.Thread(target=hang_up, daemon=True).start()
    t0 = time.time()
    out = subprocess.run(["bash", "-c", _script("127.0.0.1", [up, down])], capture_output=True, text=True, timeout=60, check=False).stdout
    srv.close()
    got = pathprobe.parse_probe(out)
    assert set(got) == {up, down} and got[up]["n"] == 8 and got[up]["tls"] == 0 and got[up]["conn"] >= 1 and got[up]["err"]
    assert got[down]["conn"] == 0 and got[down]["tls"] == 0 and got[down]["err"] == "ConnectionRefusedError"
    assert time.time() - t0 < 40


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command to make a certificate")
def test_the_script_counts_a_good_handshake(tmp_path):
    import ssl

    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt), "-days", "1", "-subj", "/CN=api.example.test"], capture_output=True, check=True, timeout=60)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(50)
    port = srv.getsockname()[1]

    def serve():
        while True:
            try:
                c, _ = srv.accept()
                w = ctx.wrap_socket(c, server_side=True)
                w.close()
            except OSError:                                                                                         # (ssl.SSLError is one; a closed listening socket ends the thread)
                if srv.fileno() < 0:
                    return

    threading.Thread(target=serve, daemon=True).start()
    out = subprocess.run(["bash", "-c", _script("127.0.0.1", [port])], capture_output=True, text=True, timeout=60, check=False).stdout
    got = pathprobe.parse_probe(out)
    srv.close()
    assert got[port]["n"] == 8 and got[port]["tls"] == 8 and got[port]["conn"] == 8 and got[port]["err"] == "" and pathprobe.layer_state(got[port]) == "ok"


# ------------------------------------------------------------------ the job
def _world(n_tunnels=1, grade="", protected=False):
    """ir1, ir2 (Iran), one foreign server per tunnel; tunnel `tN` forwards 1143N:8680 and one config rides it by its label."""
    with session() as db:
        ir1, ir2 = Server(name="ir1", host="203.0.113.10", role="iran", online=True), Server(name="ir2", host="203.0.113.11", role="iran", online=True)
        db.add_all([ir1, ir2])
        db.flush()
        out = {"ir1": ir1.id, "ir2": ir2.id, "t": []}

        for i in range(n_tunnels):
            fi = Server(name=f"fi{i}", host=f"203.0.113.{50 + i}", role="foreign", online=True)
            db.add(fi)
            db.flush()
            rec = {"port_mapping": f"1143{i}:8680", "ux": {"grade": grade}} if grade else {"port_mapping": f"1143{i}:8680"}

            if protected:
                rec["protected"] = True

            t = Tunnel(name=f"t{i}", type="backhaul", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi.id, state="up", forwarded_ports=[11430 + i], record=rec)
            db.add(t)
            db.add(Config(name=f"c{i}", enabled=True, tunnel=f"t{i}", meta={"port": 11430 + i, "sni": "api.example.test", "address": "x.example.test"}))
            db.flush()
            out["t"].append((t.id, fi.id))

        return out


@pytest.fixture
def calm(monkeypatch):
    """The Doctor in fix mode, nothing disturbing it; `node` / `tunnel` / `path` are the answers the fake layers give; `started` collects the stories that would start; `spent` the volume counted."""
    layers = {"node": R(8), "tunnel": R(8), "path": R(8)}
    started, spent, calls = [], [], []
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    monkeypatch.setattr(swap, "at_once", lambda: 0)
    monkeypatch.setattr(pathprobe.jobs, "stopping", lambda: False)
    monkeypatch.setattr(pathprobe, "handshakes", lambda host, port, sni, n=8: calls.append(("node", host, port)) or dict(layers["node"]))

    def remote(node, host, ports, sni):
        calls.append(("remote", host, tuple(ports)))
        key = "tunnel" if host == "127.0.0.1" else "path"
        return {p: dict(layers[key]) for p in ports}

    monkeypatch.setattr(pathprobe, "_remote", remote)
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn.__name__, a, k)) or 1)
    monkeypatch.setattr(testbudget, "spend", lambda nbytes, what="probe", now=None: spent.append((nbytes, what)))
    st.put("pathprobe", {"restarted": {}})
    return layers, started, spent, calls


def _layers_of(tid):
    with session() as db:
        return dict(db.get(Tunnel, tid).record.get("layers") or {})


def test_a_fleet_that_is_fine_is_looked_at_in_parallel_and_costs_a_few_kilobytes(calm):
    _layers, started, spent, calls = calm
    w = _world(3)
    assert pathprobe.run() == [] and started == []

    for tid, _fid in w["t"]:
        lay = _layers_of(tid)
        assert lay["state"]["code"] == "ok" and lay["node"]["tls"] == 8 and lay["tunnel"]["tls"] == 8 and lay["path"] is None

    assert sorted(c[0] for c in calls) == ["node"] * 3 + ["remote"]                                                 # (a node look each; ONE ssh call for the three ports of the Iran server)
    assert next(c for c in calls if c[0] == "remote")[2] == (11430, 11431, 11432)
    assert spent == [((3 * 8 + 3 * 8) * pathprobe.HANDSHAKE_BYTES, "pathprobe")]                                      # (three nodes and three tunnel ports, eight handshakes each: the day's volume of the Doctor's tests counts it)


def test_a_tunnel_that_resets_new_connections_is_restarted_once_after_two_looks_in_a_row(calm):
    layers, started, _spent, _calls = calm
    (tid, _fid), = _world(1)["t"]
    layers["tunnel"] = R(5, err="ConnectionResetError")
    assert pathprobe.run() == [] and _layers_of(tid)["state"]["code"] == "unknown"                                  # (the first bad look is one noisy look)
    assert pathprobe.run() == ["t0"]
    lay = _layers_of(tid)
    assert lay["state"]["code"] == "tunnel" and "resets or stalls" in lay["text"] and "ConnectionResetError" in lay["text"]
    (label, fn, args, kw), = started
    assert label == "t0" and fn == "restart_story" and args[0] == tid and "restarted on both ends" in args[1] and kw == {"action": "autopilot", "source": "auto"}
    assert "t0" in st.get("pathprobe")["restarted"]
    assert pathprobe.run() == [] and len(started) == 1                                                              # (once in six hours per tunnel)


def test_the_restart_obeys_every_rail(calm, monkeypatch):
    layers, started, _spent, _calls = calm
    _world(1, protected=True)
    layers["tunnel"] = R(4)
    pathprobe.run()
    assert pathprobe.run() == [] and started == []                                                                  # (a protected tunnel is never restarted)

    with session() as db:
        t = db.query(Tunnel).one()
        t.record = {k: v for k, v in t.record.items() if k != "protected"}

    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    assert pathprobe.run() == [] and started == []                                                                  # (the Doctor off: not even a look)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "observe")
    assert pathprobe.run() == [] and started == []                                                                  # (looked at, never restarted outside fix and move)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "quiet_now", lambda: True)
    assert pathprobe.run() == [] and started == []
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert pathprobe.run() == [] and started == []
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(swap, "at_once", lambda: 1)
    assert pathprobe.run() == [] and started == []
    monkeypatch.setattr(swap, "at_once", lambda: 0)
    assert pathprobe.run() == ["t0"]


def test_a_tunnel_layer_that_fails_while_the_node_was_not_measured_accuses_nobody(calm):
    layers, started, _spent, _calls = calm
    _world(3)
    layers["node"] = R(0, err="TimeoutError")                                                                       # (every node fails from the panel's host: it is the blind one)
    layers["tunnel"] = R(3)
    pathprobe.run()
    pathprobe.run()
    assert started == []

    with session() as db:
        assert all(not (s.extra or {}).get("health") for s in db.query(Server))                                     # (no tag when most nodes fail at once)
        recs = [dict(t.record.get("layers") or {}) for t in db.query(Tunnel)]

    assert all(r["state"]["code"] == "tunnel_or_node" and r["node"] is None for r in recs)


def test_a_foreign_server_whose_node_does_not_answer_gets_a_small_tag_and_loses_it_when_it_answers_again(calm):
    layers, started, _spent, _calls = calm
    w = _world(1)
    (_tid, fid), = w["t"]
    layers["node"] = R(2, err="TimeoutError")
    pathprobe.run()

    with session() as db:
        assert not (db.get(Server, fid).extra or {}).get("health")                                                  # (one look is not enough)

    pathprobe.run()

    with session() as db:
        h = db.get(Server, fid).extra["health"]

    assert h["code"] == "node" and "2 of 8" in h["text"] and h["short"] == "node problem" and started == []        # (a tag, no restart: the tunnel is not what fails)
    layers["node"] = R(8)
    pathprobe.run()

    with session() as db:
        assert db.get(Server, fid).extra["health"]["code"] == "node"                                                # (one good look does not clear it)

    pathprobe.run()

    with session() as db:
        assert "health" not in db.get(Server, fid).extra and "health_ok" not in db.get(Server, fid).extra


def test_the_path_is_looked_at_only_for_a_tunnel_whose_users_suffer_and_fails_only_when_every_vantage_fails(calm, monkeypatch):
    layers, _started, _spent, calls = calm
    w = _world(1, grade="poor")
    (tid, _fid), = w["t"]
    layers["path"] = R(2, err="TimeoutError")
    pathprobe.run()
    pathprobe.run()
    assert any(c[0] == "remote" and c[1] == "203.0.113.10" for c in calls)                                          # (the Iran server's own address, asked from the other Iran server)
    assert _layers_of(tid)["state"]["code"] == "path" and "reach the Iran server" in _layers_of(tid)["text"]
    calls.clear()
    layers["path"] = R(8)
    pathprobe.run()
    pathprobe.run()
    assert _layers_of(tid)["state"]["code"] == "ok" and any(c[1] == "203.0.113.10" for c in calls)


def test_a_config_whose_port_the_tunnel_does_not_forward_is_not_looked_at(calm):
    _layers, _started, _spent, calls = calm
    _world(1)

    with session() as db:
        db.add(Config(name="cdn", enabled=True, tunnel="t0", meta={"port": 2083, "sni": "cdn.example.test", "address": "cdn.example.test"}))

    assert [i["listen"] for i in pathprobe.plan()] == [11430]
    pathprobe.run()
    assert all(c[2] in (8680, (11430,)) for c in calls)


def test_the_job_does_not_run_twice_at_once(calm):
    _world(1)
    pathprobe._RUNNING[0] = True

    try:
        assert pathprobe.run() == []
    finally:
        pathprobe._RUNNING[0] = False


def test_the_job_is_registered_and_the_module_is_in_the_architecture():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert 'jobs.register("pathprobe", pathprobe.EVERY, pathprobe.run)' in (root / "nikatel" / "main.py").read_text(encoding="utf-8") and "pathprobe.py" in (root / "ARCHITECTURE.md").read_text(encoding="utf-8")
