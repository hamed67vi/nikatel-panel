"""The tunnel cores keep the build they replace and go back to it (the owner, 2026-10-31, with a picture of the Tunnel cores card): "the Tunnel Doctor must always keep these up to date - and if after an update it sees a
problem, it must have a backup of the previous version and go back to it".

What is proved here: the real text of the `engine_swap` and `engine_rollback` actions run by bash against a fake `systemctl` (the old build is kept, a unit that does not come back or restarts again by itself is named,
the old build comes back), the update with a fake server layer (the quietest server first, the first server whose new build does not hold gets the old one back and stops the run, the release is remembered as bad,
a tunnel that was up and is not after the swap counts as a problem), the nightly run (only when the switch is on, at the hour, once a day, never a release that was put back) and the card. NOT proved here: a real
server, a real Backhaul / Xray, real systemd."""
import hashlib
import os
import shutil
import stat
import subprocess
import types
from pathlib import Path

import pytest

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Server, ServerSample, Tunnel
from nikatel.services import engines
from nikatel.services import settings as cfg

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"


# ------------------------------------------------------------------ the scripts, run by bash
def _fake_systemctl(tmp_path, running: list[str], down_after_restart: list[str] = (), crash: list[str] = ()):
    """A `systemctl` that lists `running`, lets `down_after_restart` stay inactive, and for `crash` counts one more NRestarts every time it is asked (a unit that restarts again by itself)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.txt"
    log.write_text("")
    listing = "".join(f"echo '{u} loaded active running x'; " for u in running)
    (bindir / "systemctl").write_text(
        "#!/bin/bash\n"
        f'echo "$@" >> {log}\n'
        'case "$1" in\n'
        f"  list-units) {listing}true;;\n"
        "  restart) ;;\n"
        '  is-active) u="${@: -1}"; case " ' + " ".join(down_after_restart) + ' " in *" $u "*) exit 3;; esac;;\n'
        '  show) u="${@: -1}"; f=' + str(tmp_path) + '/n_$u; n=$(cat $f 2>/dev/null || echo 0); echo $n; case " ' + " ".join(crash) + ' " in *" $u "*) echo $((n+1)) > $f;; esac;;\n'
        "esac\nexit 0\n")
    (bindir / "sleep").write_text("#!/bin/bash\nexit 0\n")

    for f in ("systemctl", "sleep"):
        (bindir / f).chmod((bindir / f).stat().st_mode | stat.S_IEXEC)

    return bindir, log


def _run(text, bindir, tmp_path):
    return subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=30, check=False, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}).stdout


def _swap_text(core, sha, units="backhaul-*.service", watch="6"):
    return ssh.render_action("engine_swap", {"BIN": "/usr/local/bin/backhaul", "SHA": sha, "UNITS": units, "WATCH": watch}).replace("/usr/local/bin/backhaul", str(core))


def _rollback_text(core, units="backhaul-*.service"):
    return ssh.render_action("engine_rollback", {"BIN": "/usr/local/bin/backhaul", "UNITS": units}).replace("/usr/local/bin/backhaul", str(core))


@pytest.fixture
def bash_ok():
    if shutil.which("bash") is None:
        pytest.skip("no bash")


def _core(tmp_path, old=b"old build", new=b"new build"):
    core = tmp_path / "backhaul"
    if old is not None:
        core.write_bytes(old)

    (tmp_path / "backhaul.nikatel.new").write_bytes(new)
    return core, hashlib.sha256(new).hexdigest()


def test_the_swap_keeps_the_build_it_replaces_and_a_healthy_unit_says_nothing_else(tmp_path, bash_ok):
    bindir, log = _fake_systemctl(tmp_path, ["backhaul-a.service"])
    core, sha = _core(tmp_path)
    out = _run(_swap_text(core, sha), bindir, tmp_path)
    assert core.read_bytes() == b"new build" and (tmp_path / "backhaul.nikatel.prev").read_bytes() == b"old build"          # (one step back is kept next to it)
    assert "PREV kept" in out and "SWAPPED units=backhaul-a.service" in out and engines.swap_unhealthy(out) == []
    assert [c for c in log.read_text().splitlines() if c.startswith("restart")] == ["restart backhaul-a.service"]


def test_a_first_install_has_nothing_to_keep(tmp_path, bash_ok):
    bindir, _ = _fake_systemctl(tmp_path, [])
    core, sha = _core(tmp_path, old=None)
    out = _run(_swap_text(core, sha), bindir, tmp_path)
    assert "PREV none" in out and core.read_bytes() == b"new build" and not (tmp_path / "backhaul.nikatel.prev").exists()


def test_a_unit_that_does_not_come_back_is_named(tmp_path, bash_ok):
    bindir, _ = _fake_systemctl(tmp_path, ["backhaul-a.service", "backhaul-b.service"], down_after_restart=["backhaul-b.service"])
    core, sha = _core(tmp_path)
    out = _run(_swap_text(core, sha), bindir, tmp_path)
    assert "NOT_ACTIVE: backhaul-b.service" in out and engines.swap_unhealthy(out) == ["backhaul-b.service"] and "UNSTABLE" not in out


def test_a_unit_that_is_active_but_restarts_again_by_itself_is_unstable(tmp_path, bash_ok):
    bindir, _ = _fake_systemctl(tmp_path, ["backhaul-a.service", "backhaul-b.service"], crash=["backhaul-b.service"])
    core, sha = _core(tmp_path)
    out = _run(_swap_text(core, sha), bindir, tmp_path)
    assert "NOT_ACTIVE" not in out and "UNSTABLE: backhaul-b.service" in out and engines.swap_unhealthy(out) == ["backhaul-b.service"]


def test_the_old_build_comes_back_and_the_new_one_is_kept_for_a_look(tmp_path, bash_ok):
    bindir, log = _fake_systemctl(tmp_path, ["backhaul-a.service"])
    core, sha = _core(tmp_path)
    _run(_swap_text(core, sha), bindir, tmp_path)
    log.write_text("")
    out = _run(_rollback_text(core), bindir, tmp_path)
    assert core.read_bytes() == b"old build" and (tmp_path / "backhaul.nikatel.bad").read_bytes() == b"new build" and not (tmp_path / "backhaul.nikatel.prev").exists()
    assert "ROLLED_BACK units=backhaul-a.service" in out and engines.swap_unhealthy(out) == []
    assert [c for c in log.read_text().splitlines() if c.startswith("restart")] == ["restart backhaul-a.service"]


def test_nothing_to_go_back_to_is_said_and_nothing_is_touched(tmp_path, bash_ok):
    bindir, log = _fake_systemctl(tmp_path, ["backhaul-a.service"])
    core = tmp_path / "backhaul"
    core.write_bytes(b"the only build")
    out = _run(_rollback_text(core), bindir, tmp_path)
    assert "NO_PREV" in out and core.read_bytes() == b"the only build" and not any(c.startswith("restart") for c in log.read_text().splitlines())


def test_the_new_actions_take_only_the_paths_and_numbers_they_should():
    with pytest.raises(ValueError):
        ssh.render_action("engine_swap", {"BIN": "/usr/local/bin/backhaul", "SHA": "0" * 64, "UNITS": "backhaul-*.service", "WATCH": "6; reboot"})

    with pytest.raises(ValueError):
        ssh.render_action("engine_rollback", {"BIN": "/etc/passwd", "UNITS": "backhaul-*.service"})


# ------------------------------------------------------------------ the update
def _servers(client, names, role="foreign", base=150):
    for i, n in enumerate(names):
        assert client.post("/api/servers", json={"name": n, "host": f"203.0.113.{base + i}", "role": role}).status_code == 201


def _setup(monkeypatch, tmp_path, names, engine="backhaul"):
    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data", read_only=False))
    monkeypatch.setattr(engines, "_download", lambda eng: ("v9.9.9", b"b" * 1_200_000))
    engines._STATE["result"] = {engine: {"nodes": [{"name": n, "state": "outdated"} for n in names]}}
    engines._STATE["job"] = None
    monkeypatch.setattr(engines, "_copy_to_node", lambda node, eng, f: None)
    monkeypatch.setattr(engines, "tunnel_states", lambda eng, sid: {})


def _sample(name, mbps):
    with session() as db:
        s = db.query(Server).filter_by(name=name).one()
        db.add(ServerSample(server_id=s.id, ts=1_000_000, cpu=1, ram=1, disk=1, load1=0, rx_mbps=mbps, tx_mbps=0, conn=1))


def test_the_quietest_server_goes_first_and_one_with_no_sample_goes_last(client, monkeypatch, tmp_path):
    names = ["busy", "quiet", "middle", "unknown"]
    _servers(client, names)
    _setup(monkeypatch, tmp_path, names)

    for n, m in (("busy", 90.0), ("quiet", 1.0), ("middle", 20.0)):
        _sample(n, m)

    order = []

    def action(node, name, params=None, timeout=0):
        order.append((name, node.host))
        return "SWAPPED units=backhaul-a.service \n"

    monkeypatch.setattr(engines, "run_action", action)
    job = engines.update("backhaul")
    assert [i["name"] for i in job["items"]] == ["quiet", "middle", "busy", "unknown"] and all(i["state"] == "ok" for i in job["items"])
    assert all(p[0] == "engine_swap" for p in order) and len(order) == 4


def test_the_swap_is_asked_to_watch_the_units(client, monkeypatch, tmp_path):
    _servers(client, ["w1"])
    _setup(monkeypatch, tmp_path, ["w1"])
    seen = []
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: seen.append((name, dict(params or {}), timeout)) or "SWAPPED units=\n")
    engines.update("backhaul")
    assert seen[0][0] == "engine_swap" and seen[0][1]["WATCH"] == str(engines.WATCH) and seen[0][2] >= 120 + engines.WATCH      # (the call waits for the watch)


def test_a_new_build_that_does_not_hold_is_taken_off_the_run_stops_and_the_release_is_remembered(client, monkeypatch, tmp_path):
    names = ["a1", "a2", "a3"]
    _servers(client, names)
    _setup(monkeypatch, tmp_path, names)
    calls = []

    def action(node, name, params=None, timeout=0):
        calls.append((name, node.host))

        if name == "engine_swap":
            return "PREV kept\nSWAPPED units=backhaul-a.service \nUNSTABLE: backhaul-a.service\n"

        return "ROLLED_BACK units=backhaul-a.service \n"

    monkeypatch.setattr(engines, "run_action", action)
    job = engines.update("backhaul")
    assert [c[0] for c in calls] == ["engine_swap", "engine_rollback"] and calls[0][1] == calls[1][1]                      # (the old build went back on the SAME server, at once, and nothing else was touched)
    assert "kept restarting" in job["halt"] and "the old build is back" in job["halt"] and "the other servers were not touched" in job["halt"]
    assert [i["state"] for i in job["items"]] == ["failed", "failed", "failed"] and job["items"][1]["detail"].startswith("not tried") and job["items"][2]["detail"].startswith("not tried")
    assert engines.bad_versions() == {"backhaul": "v9.9.9"}


def test_a_build_that_cannot_be_taken_off_says_so(client, monkeypatch, tmp_path):
    _servers(client, ["c1"])
    _setup(monkeypatch, tmp_path, ["c1"])
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: "SWAPPED units=backhaul-a.service \nNOT_ACTIVE: backhaul-a.service\n" if name == "engine_swap" else "NO_PREV\n")
    job = engines.update("backhaul")
    assert "did not come back" in job["halt"] and "no old build was kept on this server" in job["halt"]


def test_a_tunnel_that_was_up_and_is_not_after_the_swap_is_a_problem(client, monkeypatch, tmp_path):
    _servers(client, ["t1", "t2"])
    _setup(monkeypatch, tmp_path, ["t1", "t2"])
    monkeypatch.setattr(engines, "tunnel_states", lambda eng, sid: {7: "up", 8: "down"})
    monkeypatch.setattr(engines, "tunnels_after", lambda eng, sid, before, wait=0: [7])
    calls = []
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: calls.append(name) or ("SWAPPED units=backhaul-a.service \n" if name == "engine_swap" else "ROLLED_BACK units=backhaul-a.service \n"))
    job = engines.update("backhaul")
    assert calls == ["engine_swap", "engine_rollback"] and "1 tunnel on" in job["halt"] and "was up is not up after the update" in job["halt"]


def test_the_reading_of_the_tunnels_after_a_swap():
    assert engines.tunnels_not_back({1: "up", 2: "up", 3: "down", 4: "unknown"}, {1: "up", 2: "degraded", 3: "up"}) == [2]
    assert engines.tunnels_not_back({1: "up"}, {}) == [1] and engines.tunnels_not_back({}, {5: "down"}) == [] and engines.tunnels_after("backhaul", 1, {3: "down"}) == []      # (a tunnel that was already down is not blamed on the swap)


def test_only_the_tunnels_of_the_core_that_end_on_the_server_are_looked_at(client):
    _servers(client, ["i1"], role="iran", base=100)
    _servers(client, ["f1", "f2"])

    with session() as db:
        i1, f1, f2 = (db.query(Server).filter_by(name=n).one().id for n in ("i1", "f1", "f2"))
        db.add_all([Tunnel(name="bh1", type="backhaul", iran_server_id=i1, foreign_server_id=f1, state="up"), Tunnel(name="bh2", type="backhaul", iran_server_id=i1, foreign_server_id=f2, state="down"),
                    Tunnel(name="pq1", type="paqet", iran_server_id=i1, foreign_server_id=f1, state="up")])

    st = engines.tunnel_states("backhaul", f1)
    assert list(st.values()) == ["up"] and len(engines.tunnel_states("backhaul", i1)) == 2 and engines.tunnel_states("chisel", i1) == {}


# ------------------------------------------------------------------ the Doctor, once a night
NIGHT = 1_793_400_000          # a fixed moment; the hour of it is worked out from the settings' offset below


def _hour_of(ts):
    return engines._local(ts).tm_hour


def test_nothing_runs_while_the_switch_is_off_or_it_is_not_the_hour(client, monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data", read_only=False))
    monkeypatch.setattr(engines, "check", lambda: pytest.fail("must not look"))
    assert engines.auto_due(NIGHT) is False and engines.auto_run(NIGHT) is None                                           # (off by default)
    cfg.put("engines", {"auto": True, "hour": (_hour_of(NIGHT) + 3) % 24})
    assert engines.auto_due(NIGHT) is False and engines.auto_run(NIGHT) is None
    cfg.put("engines", {"hour": _hour_of(NIGHT)})
    assert engines.auto_due(NIGHT) is True


def test_the_nightly_run_updates_what_is_behind_leaves_a_release_that_was_put_back_and_runs_once_a_day(client, monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data", read_only=False))
    cfg.put("engines", {"auto": True, "hour": _hour_of(NIGHT), "bad": {"paqet": "v2.0.0"}})
    info = {"backhaul": {"title": "Backhaul", "latest": "v1.0.0", "outdated": 2, "held_back": False, "nodes": []}, "paqet": {"title": "Paqet", "latest": "v2.0.0", "outdated": 3, "held_back": True, "nodes": []},
            "chisel": {"title": "Chisel", "latest": "v3.0.0", "outdated": 0, "held_back": False, "nodes": []}, "xray": {"title": "Xray", "latest": "v4.0.0", "outdated": 1, "held_back": False, "nodes": []}}
    monkeypatch.setattr(engines, "check", lambda: {"engines": info})
    engines._STATE["job"] = None
    updated = []
    monkeypatch.setattr(engines, "update", lambda eng, only_missing=False: updated.append(eng) or {"items": [{"state": "ok"}] * (2 if eng == "backhaul" else 1), "version": "x"})
    r = engines.auto_run(NIGHT)
    assert updated == ["backhaul", "xray"] and [x["engine"] for x in r["ran"]] == ["backhaul", "xray"] and all(x["ok"] for x in r["ran"])      # (Paqet is held back, Chisel is not behind)
    assert "Backhaul v1.0.0: 2 of 2 servers" in r["text"] and cfg.get("engines")["last"]["day"] == r["day"]
    assert engines.auto_due(NIGHT) is False and engines.auto_run(NIGHT) is None and updated == ["backhaul", "xray"]               # (once a day)


def test_the_nightly_run_stops_at_the_first_core_that_fails(client, monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data", read_only=False))
    cfg.put("engines", {"auto": True, "hour": _hour_of(NIGHT)})
    info = {k: {"title": k, "latest": "v1", "outdated": 1, "held_back": False, "nodes": []} for k in ("backhaul", "backpack")}
    monkeypatch.setattr(engines, "check", lambda: {"engines": info})
    engines._STATE["job"] = None
    updated = []
    monkeypatch.setattr(engines, "update", lambda eng, only_missing=False: updated.append(eng) or {"items": [{"state": "failed"}], "halt": "x did not come back: the old build is back."})
    r = engines.auto_run(NIGHT)
    assert updated == ["backhaul"] and r["ran"][0]["ok"] is False and "stopped" in r["text"]


def test_the_check_marks_a_release_that_was_put_back(client, monkeypatch):
    engines.mark_bad("backhaul", "v1.0.0")
    assert engines.bad_versions() == {"backhaul": "v1.0.0"}
    monkeypatch.setattr(engines, "latest", lambda eng: {"version": "v1.0.0", "page": "", "url": ""})
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=0: "ENGINE backhaul v0.9.0\n")
    _servers(client, ["k1"])
    res = engines.check()["engines"]
    assert res["backhaul"]["held_back"] is True and res["backhaul"]["outdated"] == 1


def test_the_switch_is_an_api_route_and_off_by_default(client):
    assert client.get("/api/engines").json()["auto"]["auto"] is False
    r = client.post("/api/engines/auto", json={"enabled": True, "hour": 4})
    assert r.status_code == 200 and r.json()["auto"]["auto"] is True and r.json()["auto"]["hour"] == 4
    assert client.post("/api/engines/auto", json={"enabled": False}).json()["auto"]["auto"] is False
    assert client.post("/api/engines/auto", json={"enabled": True, "hour": 30}).status_code == 422


def test_the_job_is_registered():
    src = (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert 'jobs.register("engines-auto", 600, engines.auto_run)' in src


# ------------------------------------------------------------------ the card
def test_the_card_has_the_switch_the_held_back_word_and_tells_what_the_update_does():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert 'pgSw(!!au.auto, "engineAuto(this.checked)"' in app and "window.engineAuto = async on =>" in app and 'api("/api/engines/auto", {method: "POST", body: JSON.stringify({enabled: !!on})})' in app
    assert ": outdated ? upd + held(e) :" in app and ">held back</small>" in app and "${autoRow}" in app
    assert "the old build is put back on that server and the other servers are not touched" in app and "The quietest servers go first" in app
    assert "html body #page .eng-auto {" in css
