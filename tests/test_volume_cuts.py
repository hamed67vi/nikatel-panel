"""The volume of the Doctor's own speed tests (1.69.282; the owner, 2026-11-16, «بله، همه را بساز» to the three cuts: a byte cap on the pair speed tests the Doctor asks for, the poor-score rule that measured a line of 170 ms every ten minutes
for ever - its tests are in test_bandwidth.py - and the volume kept under the name of what asked for it - test_testbudget.py).
The probe script runs here for real, against the real sink, on the loopback."""
import json
import re
import shutil
import socket
import subprocess
import sys
import time

import pytest

from nikatel import ssh
from nikatel.services import build_other, testbudget, tunnel_test

KEY = "abcdef0123456789"
needs_python = pytest.mark.skipif(shutil.which("python3") is None or shutil.which("bash") is None, reason="needs python3 and bash")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _sink(tmp_path, port: int):
    """The REAL sink script (the one `sink_install` writes on a foreign server), run here on the loopback."""
    text = ssh.render_action("sink_install", {"NAME": "t1", "PORT": str(port), "KEY": KEY, "BIND": "127.0.0.1"})
    py = text.split("<<'__NK_SINK__'\n", 1)[1].split("\n__NK_SINK__", 1)[0]
    f = tmp_path / "sink.py"
    f.write_text(py)
    proc = subprocess.Popen([sys.executable, str(f), str(port), KEY, "127.0.0.1"])

    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc
        except OSError:
            time.sleep(0.05)

    proc.kill()
    raise AssertionError("the sink did not start")


def _probe_here(port: int, **extra) -> dict:
    params = {"PORT": str(port), "SECS": "2", "STREAMS": "4", "PINGS": "3", "KEY": KEY, "TO": "2.0", "UDPN": "0", **extra}
    r = subprocess.run(["bash", "-c", ssh._script_text("probe_run", params)], capture_output=True, text=True, check=False, timeout=60)
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("NKPROBE "))
    return json.loads(line[8:])


def test_the_probe_script_takes_a_cap_of_its_own_for_the_upload():
    probe, rules = ssh.SCRIPTS["probe_run"]
    assert "'@UBYTES@'" in probe and "ULIM = int(sys.argv[11]) if len(sys.argv) > 11 and sys.argv[11] else LIM" in probe and "(not ULIM or sent < ULIM)" in probe
    assert "(not LIM or n < LIM)" in probe                                                                          # (the download keeps its own)
    assert all(re.fullmatch(rules["UBYTES"], v) for v in ("", "0", "12000000")) and not any(re.fullmatch(rules["UBYTES"], v) for v in ("1234567890", "1e6", "-1", "9; id"))


@needs_python
def test_a_stream_stops_by_bytes_and_the_upload_by_its_own_cap(tmp_path):
    port = _free_port()
    sink = _sink(tmp_path, port)

    try:
        d = _probe_here(port, BYTES="2000000", UBYTES="1000000")
        assert d["down_mbps"] > 0 and d["up_mbps"] > 0
        assert 4 * 2_000_000 + 4 * 1_000_000 <= d["w_moved"] <= 4 * (2_000_000 + 1_048_576) + 4 * (1_000_000 + 65_536)             # (4 streams each way, on a loopback that would move GB in the 2 seconds: the bytes stopped them)
        same = _probe_here(port, BYTES="2000000")                                                                                   # (no UBYTES: the upload stops at the same cap, as before)
        assert 4 * 2_000_000 + 4 * 2_000_000 <= same["w_moved"] <= 4 * (2_000_000 + 1_048_576) + 4 * (2_000_000 + 65_536)
    finally:
        sink.kill()


def test_the_cap_travels_to_the_probe_only_when_it_is_asked_for(monkeypatch):
    seen = []
    monkeypatch.setattr(build_other, "run_script", lambda node, name, params=None, timeout=40: seen.append(dict(params)) or 'NKPROBE {"latency_ms": 80.0}')
    build_other._probe(None, 39100, "k" * 8, secs=4, streams=8)
    build_other._probe(None, 39100, "k" * 8, secs=4, streams=8, cap=(24_000_000, 12_000_000))
    assert "BYTES" not in seen[0] and "UBYTES" not in seen[0] and seen[1]["BYTES"] == "24000000" and seen[1]["UBYTES"] == "12000000"


class _B:
    """A tunnel being built: only what `_measure` reads."""
    aid, test_port, measure = 1, 39100, "full"

    def __init__(self, name, capped):
        self.name, self.capped, self.quality = name, capped, {}


def _measure_with(monkeypatch, b):
    seen = []

    def fake(iran, port, key, secs=0, streams=8, pings=3, to=1.2, **kw):
        seen.append((streams, kw.get("cap"), testbudget.purpose_now()))
        return {"latency_ms": 80.0, "jitter_ms": 1.0, "loss_pct": 0.0, "down_mbps": 400.0, "up_mbps": 380.0} if streams == 8 else {"down_mbps": 55.0, "up_mbps": 41.0}

    monkeypatch.setattr(build_other, "_probe", fake)
    monkeypatch.setattr(build_other, "_say", lambda *a, **k: None)
    build_other._measure(b, None, "k")
    return seen


def test_a_pair_test_the_doctor_asks_for_is_capped_and_counted_under_its_name_and_the_owners_is_not(monkeypatch):
    assert _measure_with(monkeypatch, _B("test-1", True)) == [(8, build_other.PAIR_STREAM_CAP, "pair-survey"), (1, build_other.PAIR_ONE_CAP, "pair-survey")]
    assert _measure_with(monkeypatch, _B("test-2", False)) == [(8, None, "pair-test"), (1, None, "pair-test")]                 # (Compare, Test now, a repair's candidate: the whole speed test)
    assert _measure_with(monkeypatch, _B("ir-fr-tcpmux", False)) == [(8, None, "new-tunnel"), (1, None, "new-tunnel")]         # (a tunnel the owner built: its first measurement)
    assert testbudget.purpose_now() == "probe"                                                                                  # (the name does not leak out of the test)
    cap_down, cap_up = build_other.PAIR_STREAM_CAP
    assert 8 * (cap_down + cap_up) + sum(build_other.PAIR_ONE_CAP) == 480_000_000                                              # (a capped test moves 480 MB at the most, whatever the line)


def test_a_name_belongs_to_its_own_thread(monkeypatch):
    import threading

    got = []

    with testbudget.purpose("bandwidth"):
        t = threading.Thread(target=lambda: got.append(testbudget.purpose_now()))
        t.start()
        t.join()
        assert testbudget.purpose_now() == "bandwidth"

    assert got == ["probe"] and testbudget.purpose_now() == "probe"


def test_only_the_verify_survey_is_capped(monkeypatch):
    monkeypatch.setattr(tunnel_test, "_compare_worker", lambda job: None)
    assert tunnel_test.CAPPED_SOURCES == ("survey-verify",)
    flags = {}

    for src in ("survey-verify", "survey-manual", "survey", "survey-cold", "manual"):
        tunnel_test._COMPARE.clear()
        jid = tunnel_test.compare_start(1, 2, ["tcpmux"], source=src)
        flags[src] = tunnel_test._COMPARE[jid]["capped"]

    tunnel_test._COMPARE.clear()
    assert flags == {"survey-verify": True, "survey-manual": False, "survey": False, "survey-cold": False, "manual": False}


def test_a_capped_job_builds_its_tests_capped_and_another_does_not(monkeypatch):
    built = []
    monkeypatch.setattr(build_other, "start_build", lambda *a, **k: built.append(k) or 41)
    monkeypatch.setattr(tunnel_test, "purge_leftovers", lambda *a, **k: None)
    monkeypatch.setattr(tunnel_test.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    tunnel_test.start_other(1, 2, "bp:tcpmux", "full", capped=True)
    tunnel_test.start_other(1, 2, "bp:tcpmux", "full")
    assert built[0]["capped"] is True and "capped" not in built[1] and built[0]["measure"] == "full"
