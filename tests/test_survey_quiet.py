"""The night survey builds and removes hundreds of temporary test tunnels; its leftovers must not look like trouble (the owner, 2026-10-24: "check the program, I think it is confused"; the live log: a yellow
"Tunnel test-1 removed" event every 30 s and "build crashed" errors with a traceback for a server without python3). A temporary tunnel that was removed cleanly is no event; a server that cannot be tested is a
known stop (one warning line), not a crash."""
import logging
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from nikatel.services import build_other

SRC = Path(__file__).resolve().parents[1] / "nikatel" / "services"


def _run_worker(monkeypatch, exc):
    said = []

    @contextmanager
    def no_lock(ids, aid):
        yield

    monkeypatch.setattr(build_other, "server_locks", no_lock)
    monkeypatch.setattr(build_other, "_build", lambda b: (_ for _ in ()).throw(exc))
    monkeypatch.setattr(build_other, "_rollback", lambda b: None)
    monkeypatch.setattr(build_other, "_say", lambda aid, text, done=False, ok=None: said.append((text, done, ok)))
    from nikatel.services import builder

    monkeypatch.setattr(builder, "pending_drop", lambda name: None)
    b = SimpleNamespace(aid=9001, iran=SimpleNamespace(id=1), foreign=SimpleNamespace(id=2), name="test-1")
    build_other._BUILDING.add("pair-x")
    build_other._worker(b, "pair-x")
    return said


def test_a_server_that_cannot_be_tested_is_one_warning_line_and_the_story_says_why(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    said = _run_worker(monkeypatch, build_other.PreflightError("Foreign server X1 has no python3 (needed for the test door)"))
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "stopped" in r.getMessage()]
    assert said and said[-1][1] is True and said[-1][2] is False and "has no python3" in said[-1][0] and "Everything created was removed again" in said[-1][0]
    assert "pair-x" not in build_other._BUILDING


def test_a_real_crash_is_still_an_error_with_its_traceback(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _run_worker(monkeypatch, ValueError("something broke"))
    assert [r for r in caplog.records if r.levelno == logging.ERROR and "crashed" in r.getMessage()]


def test_the_three_preflight_stops_are_known_stops_and_a_clean_removal_of_a_temporary_tunnel_is_no_event():
    b = (SRC / "build_other.py").read_text(encoding="utf-8")
    assert b.count("raise PreflightError(") == 3 and "class PreflightError(RuntimeError)" in b
    s = (SRC / "builder.py").read_text(encoding="utf-8")
    assert 'if not quiet and (not is_temp(name) or any("not" in r for r in results)):' in s
