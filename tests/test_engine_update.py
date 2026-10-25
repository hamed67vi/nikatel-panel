"""The Update button of the cores (Settings -> About; the owner, 2026-10-25, with a picture of "Xray 29/38 - Update": "I pressed update here - take care that the users are not cut").

What happened: the press failed on ALL 29 servers within six seconds with "bad value for PATH: '/opt/nikatel-xgw/xray.nikatel.new'" - the update sent the core as base64 through the `put_file` action, whose path and size
rules refuse a core. Nothing was copied or restarted, so nobody was cut. Now the core is copied with scp (the way a build puts it), the swap restarts only units that RUN, one server at a time, and the first unit that does
not come back active after its restart stops the update there."""
import hashlib
import os
import shutil
import stat
import subprocess
import threading
import time
import types
from pathlib import Path

import pytest

from nikatel import ssh
from nikatel.services import engines


# ------------------------------------------------------------------ the swap script
def test_a_core_is_never_sent_as_base64_through_put_file_again():
    src = Path(engines.__file__).read_text(encoding="utf-8")
    update = src[src.index("def update(engine"): src.index("# ------------------------------------------------------------------ WireGuard tools")]
    assert "put_file" not in update and "b64encode" not in update and "_copy_to_node(node, engine, newest)" in update


def test_the_pure_reading_of_the_swap_answer():
    assert engines.swap_unhealthy("SWAPPED units=nikatel-xgw.service \nNOT_ACTIVE: nikatel-xgw.service") == ["nikatel-xgw.service"]
    assert engines.swap_unhealthy("SWAPPED units=a.service b.service \nNOT_ACTIVE: a.service b.service\n") == ["a.service", "b.service"]
    assert engines.swap_unhealthy("SWAPPED units=a.service \n") == [] and engines.swap_unhealthy("SWAPPED units=\n") == [] and engines.swap_unhealthy("") == []


def _swap(tmp_path, units_running: list[str], stays_down: list[str]):
    """The real text of the `engine_swap` action run by bash against a fake `systemctl` / `sleep`: `units_running` are what `list-units` shows, `stays_down` do not come back after `restart`. Returns (answer, calls of systemctl, the binary)."""
    if shutil.which("bash") is None:
        pytest.skip("no bash")

    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.txt"
    log.write_text("")
    listing = "".join(f"echo '{u} loaded active running x'; " for u in units_running)
    (bindir / "systemctl").write_text(
        "#!/bin/bash\n"
        f'echo "$@" >> {log}\n'
        'case "$1" in\n'
        f'  list-units) {listing}true;;\n'
        '  restart) ;;\n'
        '  is-active) u="${@: -1}"; case " ' + " ".join(stays_down) + ' " in *" $u "*) exit 3;; esac;;\n'
        "esac\nexit 0\n")
    (bindir / "sleep").write_text("#!/bin/bash\nexit 0\n")

    for f in ("systemctl", "sleep"):
        (bindir / f).chmod((bindir / f).stat().st_mode | stat.S_IEXEC)

    core = tmp_path / "xray"
    core.write_bytes(b"old")
    (tmp_path / "xray.nikatel.new").write_bytes(b"new core")
    sha = hashlib.sha256(b"new core").hexdigest()
    text = ssh.render_action("engine_swap", {"BIN": "/usr/local/bin/xray", "SHA": sha, "UNITS": "nikatel-xgw.service"}).replace("/usr/local/bin/xray", str(core))
    out = subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=30, check=False, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"})
    return out.stdout, log.read_text().splitlines(), core.read_bytes()


def test_a_server_that_runs_no_unit_of_the_core_restarts_nothing(tmp_path):
    out, calls, binary = _swap(tmp_path, [], [])
    assert binary == b"new core" and "SWAPPED units=" in out and "NOT_ACTIVE" not in out
    assert not any(c.startswith("restart") for c in calls)                                                 # (the binary is replaced; nobody is touched)
    assert any(c.startswith("list-units") and "--state=active,activating,failed" in c and "--all" not in c for c in calls)      # (a unit that is stopped on purpose is not listed, so an update never starts it)


def test_a_running_unit_is_restarted_and_must_come_back(tmp_path):
    out, calls, binary = _swap(tmp_path, ["nikatel-xgw.service"], [])
    assert binary == b"new core" and "SWAPPED units=nikatel-xgw.service" in out and "NOT_ACTIVE" not in out
    assert [c for c in calls if c.startswith("restart")] == ["restart nikatel-xgw.service"]


def test_a_unit_that_does_not_come_back_is_named(tmp_path):
    out, _calls, _ = _swap(tmp_path, ["nikatel-xgw.service"], ["nikatel-xgw.service"])
    assert "NOT_ACTIVE: nikatel-xgw.service" in out and engines.swap_unhealthy(out) == ["nikatel-xgw.service"]


# ------------------------------------------------------------------ the update
def _servers(client, names):
    for i, n in enumerate(names):
        assert client.post("/api/servers", json={"name": n, "host": f"203.0.113.{140 + i}", "role": "foreign"}).status_code == 201


def _setup(monkeypatch, tmp_path, names):
    monkeypatch.setattr(engines, "get_settings", lambda: types.SimpleNamespace(data_dir=tmp_path / "data", read_only=False))
    monkeypatch.setattr(engines, "_download", lambda engine: ("v9.9.9", b"b" * 1_200_000))
    engines._STATE["result"] = {"xray": {"nodes": [{"name": n, "state": "outdated"} for n in names]}}
    engines._STATE["job"] = None


def test_the_update_copies_with_scp_swaps_one_server_at_a_time_and_never_uses_put_file(client, monkeypatch, tmp_path):
    names = ["u1", "u2", "u3"]
    _servers(client, names)
    _setup(monkeypatch, tmp_path, names)
    copied, swaps, now_in, worst = [], [], [0], [0]
    lock = threading.Lock()

    def action(node, name, params=None, timeout=0):
        assert name != "put_file"                                                                           # (the core is not sent as base64)

        with lock:
            now_in[0] += 1
            worst[0] = max(worst[0], now_in[0])

        time.sleep(0.05)
        swaps.append((node.host, dict(params or {})))

        with lock:
            now_in[0] -= 1

        return "SWAPPED units=nikatel-xgw.service \n"

    monkeypatch.setattr(engines, "_copy_to_node", lambda node, engine, f: copied.append((node.host, engine, f.name)))
    monkeypatch.setattr(engines, "run_action", action)
    job = engines.update("xray")
    assert sorted(i["name"] for i in job["items"]) == names and all(i["state"] == "ok" for i in job["items"])
    assert sorted(c[0] for c in copied) == sorted(h for h, _ in swaps) and all(c[1] == "xray" and c[2] == "xray" for c in copied)
    assert len(swaps) == 3 and worst[0] == 1                                                                 # (the swaps, which restart units, never run two at once)
    assert all(p["UNITS"] == "nikatel-xgw.service" and p["BIN"] == "/opt/nikatel-xgw/xray" for _, p in swaps) and not job.get("halt")


def test_the_first_unit_that_does_not_come_back_stops_the_update_there(client, monkeypatch, tmp_path):
    names = ["h1", "h2", "h3"]
    _servers(client, names)
    _setup(monkeypatch, tmp_path, names)
    swaps = []

    def action(node, name, params=None, timeout=0):
        swaps.append(node.host)
        return "SWAPPED units=nikatel-xgw.service \nNOT_ACTIVE: nikatel-xgw.service\n"

    monkeypatch.setattr(engines, "_copy_to_node", lambda node, engine, f: None)
    monkeypatch.setattr(engines, "run_action", action)
    job = engines.update("xray")
    assert len(swaps) == 1                                                                                   # (one server was swapped; the others were not touched)
    states = sorted((i["state"], i["detail"][:9]) for i in job["items"])
    assert states == [("failed", "nikatel-x"), ("failed", "not tried"), ("failed", "not tried")] and "did not come back" in job["halt"]


def test_a_server_that_cannot_be_reached_does_not_stop_the_others(client, monkeypatch, tmp_path):
    names = ["r1", "r2"]
    _servers(client, names)
    _setup(monkeypatch, tmp_path, names)

    def copy(node, engine, f):
        if node.host.endswith(".140"):
            raise RuntimeError("could not copy Xray to " + node.host + ": Connection timed out")

    monkeypatch.setattr(engines, "_copy_to_node", copy)
    monkeypatch.setattr(engines, "run_action", lambda node, name, params=None, timeout=0: "SWAPPED units=\n")
    job = engines.update("xray")
    by = {i["name"]: i for i in job["items"]}
    assert by["r1"]["state"] == "failed" and "Connection timed out" in by["r1"]["detail"] and by["r2"]["state"] == "ok" and not job.get("halt")


def test_the_screen_tells_the_truth_about_a_failed_update():
    app = (Path(__file__).resolve().parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")
    btn = app[app.index("window.engineUpdate = async k =>"): app.index("/* A tunnel the Doctor REPLACES")]
    assert "ok === n ? `✓ ${ok} updated` : `${ok} of ${n} updated - ${n - ok} failed" in btn and 'r.halt ? "✕ Stopped: " + r.halt' in btn      # (it said "✓ 0 updated" when all 29 servers had failed)
    assert "One server at a time" in btn and "the other servers are not touched" in btn                                                       # (the question before it says how it works)
    assert "d.job.halt" in app and "first failure:" in app                                                                                    # (the line of the last update says where it stopped and the first reason)