"""The ports of a BackPack (reverse / direct), Paqet or Chisel tunnel can be changed from its menu (the owner, 2026-10-21: the menu of a BackPack direct tunnel had no "Ports…" - "add it for every tunnel that needs it")."""
import base64
import re
from pathlib import Path

import pytest

from nikatel import ssh
from nikatel.services import build_other, builder


def _tunnel(client, kind, ttype, direction="reverse"):
    client.post("/api/servers", json={"name": "ir1", "host": "1.1.1.1", "role": "iran"})
    client.post("/api/servers", json={"name": "de3", "host": "2.2.2.2", "role": "foreign"})
    from nikatel.db import session
    from nikatel.models import Tunnel

    with session() as db:
        db.add(Tunnel(name="de3-ir1", type=ttype, transport="pck", iran_server_id=1, foreign_server_id=2, port=9905, forwarded_ports=[443],
                      record={"kind": kind, "direction": direction, "test_port": 41234, "port_mapping": "443"}))


def _spy(monkeypatch, answers):
    calls = []

    def fake(node, name, params=None, timeout=90):
        calls.append((node.host, name, dict(params or {})))
        return answers.get(name, "")

    monkeypatch.setattr(build_other, "run_action", fake)
    return calls


@pytest.mark.parametrize("kind,ttype,action", [("backpack_direct", "backpack", "set_ports_bp"), ("backpack", "backpack", "set_ports_bp"), ("paqet", "paqet", "set_ports_paqet")])
def test_the_list_is_rewritten_on_the_iran_server_with_the_test_door_kept(client, monkeypatch, kind, ttype, action):
    _tunnel(client, kind, ttype)
    calls = _spy(monkeypatch, {action: "PORTS_OK\n"})
    r = client.put("/api/tunnels/1/ports", json={"ports": "443, 8443=2096, 10000-10001"})
    assert r.status_code == 200 and r.json()["ports"] == [443, 8443, 10000, 10001], r.text
    host, name, params = calls[0]
    assert host == "1.1.1.1" and name == action and params["CFG"] == "de3-ir1" and params["CHECK"] == "443 8443 10000 10001"
    body = base64.b64decode(params["B64"]).decode()
    assert "41234" in body and "2096" in body
    assert client.get("/api/tunnels").json()[0]["forwarded_ports"] == [443, 8443, 10000, 10001]
    _spy(monkeypatch, {action: "NOTLISTEN\n"})
    assert client.put("/api/tunnels/1/ports", json={"ports": "9"}).status_code == 502
    assert client.get("/api/tunnels").json()[0]["forwarded_ports"] == [443, 8443, 10000, 10001]          # (nothing changed in the record when the server put the old file back)


@pytest.mark.parametrize("direction,client_host,unit", [("reverse", "2.2.2.2", "chisel-de3-ir1-kharej.service"), ("forward", "1.1.1.1", "chisel-de3-ir1.service")])
def test_chisel_remotes_are_changed_on_the_side_that_dials(client, monkeypatch, direction, client_host, unit):
    _tunnel(client, "chisel", "chisel", direction)
    calls = _spy(monkeypatch, {"set_ports_chisel": "EDITED\n", "ports_wait": "LISTENING\n"})
    assert client.put("/api/tunnels/1/ports", json={"ports": "443, 8443=2096"}).status_code == 200
    (h1, n1, p1), (h2, n2, p2) = calls
    assert (h1, n1, p1["UNIT"]) == (client_host, "set_ports_chisel", unit) and (h2, n2, p2["CHECK"]) == ("1.1.1.1", "ports_wait", "443 8443")
    remotes = base64.b64decode(p1["B64"]).decode()
    assert ("'R:0.0.0.0:8443:127.0.0.1:2096'" in remotes) == (direction == "reverse") and "'0.0.0.0:8443:127.0.0.1:2096'" in remotes.replace("R:", "")
    calls2 = _spy(monkeypatch, {"set_ports_chisel": "EDITED\n", "ports_wait": "NOTLISTEN\n", "unit_restore": "RESTORED\n"})
    assert client.put("/api/tunnels/1/ports", json={"ports": "9"}).status_code == 502
    assert [c[1] for c in calls2] == ["set_ports_chisel", "ports_wait", "unit_restore"]          # (the old unit is put back when a port does not listen)


def test_other_kinds_are_refused_and_the_actions_are_guarded(client):
    _tunnel(client, "wireguard", "gre")
    assert client.put("/api/tunnels/1/ports", json={"ports": "443"}).status_code == 400
    rx = ssh.ACTIONS["set_ports_bp"][1]["CFG"]
    assert re.match(rx, "de3-ir1") and not re.match(rx, "../etc/passwd") and not re.match(rx, "a b")

    for act in ("set_ports_chisel", "unit_restore"):
        assert re.match(ssh.ACTIONS[act][1]["UNIT"], "chisel-de3-ir1-kharej.service") and not re.match(ssh.ACTIONS[act][1]["UNIT"], "ssh.service")

    assert "NOTLISTEN" in ssh.ACTIONS["set_ports_bp"][0] and "bak-ports" in ssh.ACTIONS["set_ports_paqet"][0] and "daemon-reload" in ssh.ACTIONS["set_ports_chisel"][0]


def test_the_menu_offers_ports_for_every_kind_that_has_them():
    js = (Path(builder.__file__).parent.parent / "web" / "lab.js").read_text(encoding="utf-8")
    assert '["backhaul", "backpack", "paqet", "chisel"].includes(t.type) ? [labIt("Ports…"' in js


def _rewrite(action, text, tmp_path, new):
    """Run the python part of a shell action on `text` (the real file text a build writes) and give the new text back."""
    import subprocess
    import sys

    script = ssh.ACTIONS[action][0]
    code = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0].replace("/tmp/.nikatel-ports.txt", str(tmp_path / "new.txt"))
    (tmp_path / "new.txt").write_text(new, encoding="utf-8")
    f = tmp_path / "file.cfg"
    f.write_text(text, encoding="utf-8")
    r = subprocess.run([sys.executable, "-c", code, str(f)], check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return f.read_text(encoding="utf-8")


def test_the_python_of_the_actions_edits_the_real_files_of_a_build(tmp_path):
    from nikatel.services.builder import parse_ports

    old, new = parse_ports("443"), parse_ports("443, 8443=2096")
    # BackPack, reverse and direct: only the ports list changes
    srv, _ = build_other.backpack_texts("1.1.1.1", 9905, "tok", "tcpmux", old, 41234)
    out = _rewrite("set_ports_bp", srv, tmp_path, "\n".join(f'    "{e}",' for e in build_other._entries(new, 41234)))
    assert '"8443=127.0.0.1:2096",' in out and '"127.0.0.1:41234=41234",' in out and 'token = "tok"' in out and out.count("ports = [") == 1 and out.rstrip().endswith("]")
    iran, _ = build_other.direct_texts("2.2.2.2", 9905, "tok", "pck", 7, old, 41234)
    out = _rewrite("set_ports_bp", iran, tmp_path, "\n".join(f'    "{e}",' for e in build_other._entries(new, 41234)))
    assert '"8443=127.0.0.1:2096",' in out and 'local_ip = "10.10.7.1/30"' in out and 'carrier = "pck"' in out
    # Paqet: the forward block changes, the network block under it stays
    iran_yaml, _ = build_other.paqet_texts({"IF": "eth0", "IP": "1.1.1.1", "MAC": "aa:bb"}, {"IF": "eth0", "IP": "2.2.2.2", "MAC": "cc:dd"}, 9905, "key", "2.2.2.2", old, 41234)
    body = "\n".join(f'  - listen: "{ls}"\n    target: "{tg}"\n    protocol: "{pr}"' for ls, tg, pr in build_other.forward_rows(new, 41234, False))
    out = _rewrite("set_ports_paqet", iran_yaml, tmp_path, body)
    assert 'listen: "0.0.0.0:8443"' in out and 'target: "127.0.0.1:2096"' in out and "network:" in out and 'router_mac: "aa:bb"' in out and out.count("forward:") == 1
    # Chisel: the remotes end the ExecStart line, the rest of the unit stays
    for fwd in (False, True):
        unit = build_other.chisel_unit("de3-ir1", "iran" if fwd else "foreign", 9905, build_other.chisel_remotes(old, 41234, False, fwd), "1.1.1.1", True, "fp123", fwd)
        remotes = " ".join(f"'{r}'" for r in build_other.chisel_remotes(new, 41234, False, fwd))
        out = _rewrite("set_ports_chisel", unit, tmp_path, remotes)
        line = next(x for x in out.splitlines() if x.startswith("ExecStart="))
        assert line.endswith(remotes) and "--fingerprint fp123" in line and "8443:127.0.0.1:2096" in line and "Restart=always" in out