"""A core binary that a stalled old copy still holds open for writing ("Text file busy"; the owner, 2026-11-13: the move of two tunnels to an Iran server failed on it after the copies had gone through, six
hours after the copies that stalled): the probe counts the writers, every copy makes a NEW file, a core that is held is put there again, and a unit that fails to start for it is repaired inside the build."""
import subprocess
import time
import types

import pytest

from nikatel import ssh
from nikatel.services import build_other, engines
from nikatel.ssh import Node, SSHError

NODE = Node("192.0.2.5", "root", 22)


# ------------------------------------------------------------------ the probe: the REAL script, run by bash against a file that is held open for writing
def run_probe(core) -> str:
    text = ssh.SCRIPTS["engine_sha"][0].replace("@BIN@", str(core))
    return subprocess.run(["bash", "-s"], input=text, capture_output=True, text=True, timeout=30, check=False).stdout


def test_the_probe_counts_the_processes_that_hold_the_core_open_for_writing(tmp_path):
    core = tmp_path / "backpack"
    core.write_bytes(b"x" * 100)
    core.chmod(0o755)
    assert "WRITERS=0" in run_probe(core)
    holder = subprocess.Popen(["bash", "-c", f"exec 9>>{core}; sleep 30"])          # (a writer, as the sftp-server of a stalled copy is)
    reader = subprocess.Popen(["bash", "-c", f"exec 8<{core}; sleep 30"])           # (a reader does not make the file busy: it is not counted)

    try:
        time.sleep(0.5)
        out = run_probe(core)
        assert "WRITERS=1" in out and "SHA=" in out and "ARCH=" in out
    finally:
        holder.kill()
        reader.kill()
        holder.wait()
        reader.wait()

    assert "WRITERS=0" in run_probe(core)


def test_a_core_that_is_held_for_writing_cannot_run_which_is_what_the_probe_finds(tmp_path):
    """The trouble itself, on this machine: exec of a file that a process holds open for writing fails with 'Text file busy'."""
    script = tmp_path / "engine"
    script.write_bytes(b"#!/bin/sh\necho ran\n")
    script.chmod(0o755)
    holder = subprocess.Popen(["bash", "-c", f"exec 9>>{script}; sleep 30"])

    try:
        time.sleep(0.5)
        with pytest.raises(OSError, match="Text file busy"):
            subprocess.run([str(script)], capture_output=True, text=True, check=False)

        assert "WRITERS=1" in run_probe(script)
    finally:
        holder.kill()
        holder.wait()


def test_the_writers_are_read_from_the_answer_and_an_older_answer_has_none():
    assert engines.parse_writers("SHA=abc\nARCH=x86_64\nWRITERS=3\n") == 3
    assert engines.parse_writers("SHA=abc\nARCH=x86_64\n") == 0 and engines.parse_writers("") == 0 and engines.parse_writers(None) == 0


def test_the_new_action_takes_one_temp_file_away_and_only_in_the_engine_directories():
    assert ssh.render_action("engine_prep", {"FILE": "/usr/local/bin/backpack.nikatel.new"}).startswith("rm -f '/usr/local/bin/backpack.nikatel.new'")

    for bad in ("/etc/passwd", "/usr/local/bin/../../etc/passwd", "/usr/local/bin/a b", "/usr/local/bin/x;id", "/tmp/backpack.nikatel.new"):
        with pytest.raises(ValueError):
            ssh.render_action("engine_prep", {"FILE": bad})


# ------------------------------------------------------------------ the copy makes a NEW file, the install puts a held core there again
@pytest.fixture
def fake_node(monkeypatch, tmp_path):
    cache = tmp_path / "backpack"
    cache.write_bytes(b"core-bytes" * 100)
    monkeypatch.setattr(engines, "cache_file", lambda e, arch="amd64": cache)
    calls = []
    monkeypatch.setattr(engines, "copy_file", lambda node, f, remote, **k: calls.append(("copy", remote)) or "")

    def action(node, name, params=None, timeout=0):
        calls.append((name, dict(params or {})))
        return "SWAPPED units=\n" if name == "engine_swap" else "PREPARED\n" if name == "engine_prep" else ""

    monkeypatch.setattr(engines, "run_action", action)
    import hashlib

    return calls, hashlib.sha256(cache.read_bytes()).hexdigest()


def test_every_copy_first_takes_the_old_temp_file_away(fake_node, monkeypatch):
    calls, _ = fake_node
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=0: "SHA=none\nARCH=x86_64\nWRITERS=0\n")
    assert engines.ensure_on_node(NODE, "backpack") is True
    assert [c[0] for c in calls] == ["engine_prep", "copy", "engine_swap"]                      # prep first: the copy is a new file
    assert calls[0][1] == {"FILE": "/usr/local/bin/backpack.nikatel.new"} and calls[1][1] == "/usr/local/bin/backpack.nikatel.new"


def test_the_same_build_that_nobody_holds_is_left_alone_and_the_one_that_is_held_is_put_again(fake_node, monkeypatch):
    calls, sha = fake_node
    said = []
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=0: f"SHA={sha}\nARCH=x86_64\nWRITERS=0\n")
    assert engines.ensure_on_node(NODE, "backpack") is False and calls == []                     # the same build, not held: nothing is copied
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=0: f"SHA={sha}\nARCH=x86_64\nWRITERS=2\n")
    assert engines.ensure_on_node(NODE, "backpack", say=said.append) is True                     # the same build, held by two old copies: put there again, as a new file
    assert [c[0] for c in calls] == ["engine_prep", "copy", "engine_swap"] and "2 old copy(ies)" in said[0] and "Text file busy" in said[0]
    calls.clear()
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=0: f"SHA={sha}\nARCH=x86_64\n")
    assert engines.ensure_on_node(NODE, "backpack") is False                                     # an answer without the count (an older script): as before
    assert engines.force_install(NODE, "backpack") is True and [c[0] for c in calls] == ["engine_prep", "copy", "engine_swap"]


def test_a_prep_that_cannot_run_does_not_stop_the_copy(fake_node, monkeypatch):
    calls, _ = fake_node

    def action(node, name, params=None, timeout=0):
        if name == "engine_prep":
            raise SSHError("ssh connection lost (Timeout)")

        calls.append((name, params))
        return "SWAPPED units=\n"

    monkeypatch.setattr(engines, "run_action", action)
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=0: "SHA=none\nARCH=x86_64\n")
    assert engines.ensure_on_node(NODE, "backpack") is True and [c[0] for c in calls] == ["copy", "engine_swap"]


# ------------------------------------------------------------------ a unit that cannot start because of it is repaired in the build
class B:
    aid = 1


def busy_logs(path="/usr/local/bin/backpack"):
    return f"Nov 13 11:32:15 host (backpack)[1]: backpack-x-mv.service: Failed to execute {path}: Text file busy\nNov 13 11:32:15 host systemd[1]: Main process exited, code=exited, status=203/EXEC\n"


def test_a_unit_that_fails_with_text_file_busy_gets_its_core_again_and_starts(monkeypatch):
    said, did = [], []
    states = iter(["activating", "active"])
    monkeypatch.setattr(build_other, "_unit", lambda node, unit: next(states))
    monkeypatch.setattr(build_other, "run_script", lambda node, name, params=None, timeout=0: busy_logs())
    monkeypatch.setattr(build_other, "_say", lambda aid, line: said.append(line))
    monkeypatch.setattr(build_other.engines, "force_install", lambda node, eng, say=None: did.append(("install", eng)) or True)
    monkeypatch.setattr(build_other, "run_action", lambda node, name, params=None, timeout=0: did.append((name, dict(params))) or "UNIT x active\n")
    monkeypatch.setattr(build_other.time, "sleep", lambda s: None)
    b = types.SimpleNamespace(aid=1, created=[])
    build_other._start(NODE, "Iran", "backpack-x-mv.service", b)                                 # no error: the unit runs after the repair
    assert did == [("install", "backpack"), ("restart_units", {"UNITS": "backpack-x-mv.service"})]
    assert any("Text file busy" in x and "again as a new file" in x for x in said)


def test_the_repair_that_does_not_help_and_any_other_failure_say_what_the_unit_said(monkeypatch):
    monkeypatch.setattr(build_other, "_say", lambda aid, line: None)
    monkeypatch.setattr(build_other.time, "sleep", lambda s: None)
    monkeypatch.setattr(build_other, "run_action", lambda *a, **k: "")
    monkeypatch.setattr(build_other.engines, "force_install", lambda *a, **k: True)
    monkeypatch.setattr(build_other, "_unit", lambda node, unit: "activating")                 # (still not running after the repair)
    monkeypatch.setattr(build_other, "run_script", lambda node, name, params=None, timeout=0: busy_logs())
    b = types.SimpleNamespace(aid=1, created=[])

    with pytest.raises(RuntimeError, match="Iran side did not start"):
        build_other._start(NODE, "Iran", "backpack-x-mv.service", b)

    monkeypatch.setattr(build_other.engines, "force_install", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no cache")))

    with pytest.raises(RuntimeError, match="Iran side did not start"):
        build_other._start(NODE, "Iran", "backpack-x-mv.service", b)

    called = []
    monkeypatch.setattr(build_other.engines, "force_install", lambda *a, **k: called.append(1))
    monkeypatch.setattr(build_other, "run_script", lambda node, name, params=None, timeout=0: "systemd[1]: backpack-x-mv.service: Failed with result 'exit-code'.")      # (another trouble: not repaired this way)

    with pytest.raises(RuntimeError, match="Iran side did not start"):
        build_other._start(NODE, "Iran", "backpack-x-mv.service", b)

    assert called == []
    monkeypatch.setattr(build_other, "_unit", lambda node, unit: "active")
    build_other._start(NODE, "Iran", "backpack-x-mv.service", b)                                # a unit that runs: nothing to repair


def test_the_busy_line_names_the_core_by_its_path_and_nothing_else():
    for path, eng in (("/usr/local/bin/backpack", "backpack"), ("/usr/local/bin/nikatel-chisel", "chisel"), ("/usr/local/bin/paqet", "paqet"), ("/usr/local/bin/backhaul", "backhaul")):
        m = build_other.BUSY_RX.search(busy_logs(path))
        assert m and next(k for k, v in engines.ENGINES.items() if v["bin"] == m.group(1)) == eng

    assert build_other.BUSY_RX.search("Failed to execute /usr/local/bin/backpack: No such file or directory") is None
    assert build_other.BUSY_RX.search("Failed to execute /etc/passwd: Text file busy") is None
