"""The QA round of the tunnel engine (2026-10-22), part one: the engine can not be wedged.

  a pair is never left "being built"    a start of a build that fails after it took the pair (a locked database at the flush), and a build whose last step fails, give the pair back
  one test at a time                    two tests asked in the same moment: one runs, the other is refused; the id of a test is never shared; a test that cannot start a thread does not block the next ones
  a story is always closed              a test whose cleanup crashes still ends its story
"""
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, Server
from nikatel.services import build_other, builder, tunnel_test


def _servers(tag="qa"):
    with session() as db:
        n = len(db.query(Server).all()) + 40
        ir = Server(name=f"ir-{tag}", host=f"203.0.113.{n}", role="iran", online=True, enabled=True)
        fo = Server(name=f"fo-{tag}", host=f"198.51.100.{n}", role="foreign", online=True, enabled=True)
        db.add_all([ir, fo])
        db.flush()
        return ir.id, fo.id


def _writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)


# ------------------------------------------------------------------ a pair is never left "being built"
@pytest.mark.parametrize("module,kwargs", [(builder, {"transport": "tcpmux"}), (build_other, {"kind": "chisel", "transport": "ws"})])
def test_a_start_that_fails_after_it_took_the_pair_gives_the_pair_back(monkeypatch, module, kwargs):
    _writable(monkeypatch)
    ir, fo = _servers("lk" + module.__name__[-3:])

    def start():
        if module is builder:
            return module.start_build(ir, fo, "8075", kwargs["transport"])

        return module.start_build(ir, fo, "8075", kwargs["kind"], kwargs["transport"], {})

    def boom(*a, **k):
        raise OperationalError("flush", {}, Exception("database is locked"))

    monkeypatch.setattr(module, "Build", boom)

    with pytest.raises(OperationalError):
        start()

    assert module._BUILDING == set()          # (before: the pair stayed "being built" until the panel was restarted)

    # a thread that cannot be started is the same
    monkeypatch.undo()
    _writable(monkeypatch)

    class NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module.threading, "Thread", NoThread)

    with pytest.raises(RuntimeError):
        start()

    assert module._BUILDING == set()


@pytest.mark.parametrize("module", [builder, build_other])
def test_a_refusal_because_the_pair_is_being_built_does_not_free_the_other_build(monkeypatch, module):
    _writable(monkeypatch)
    ir, fo = _servers("rf" + module.__name__[-3:])
    module._BUILDING.add("someone else's key")

    with session() as db:
        pair = f"{db.get(Server, ir).name} ⇄ {db.get(Server, fo).name}"

    module._BUILDING.add(pair)

    try:
        with pytest.raises(ValueError, match="already being built"):
            builder.start_build(ir, fo, "8075", "tcpmux") if module is builder else build_other.start_build(ir, fo, "8075", "chisel", "ws", {})

        assert pair in module._BUILDING          # (the build that holds it is not disturbed by the refusal)
    finally:
        module._BUILDING.discard(pair)
        module._BUILDING.discard("someone else's key")


@pytest.mark.parametrize("module", [builder, build_other])
def test_the_pair_is_free_again_even_when_the_last_step_of_the_build_fails(monkeypatch, module):
    ir, fo = _servers("wk" + module.__name__[-3:])
    b = SimpleNamespace(aid=0, iran=SimpleNamespace(id=ir), foreign=SimpleNamespace(id=fo), name="qa-t", created=[])
    module._BUILDING.add("pair-x")

    def crash(_):
        raise RuntimeError("the build fell over")

    def locked(_):
        raise OperationalError("update", {}, Exception("database is locked"))

    monkeypatch.setattr(module, "_build", crash)
    monkeypatch.setattr(builder, "pending_drop", locked)          # (build_other takes it from builder)
    (builder._build_worker if module is builder else build_other._worker)(b, "pair-x")
    assert "pair-x" not in module._BUILDING


# ------------------------------------------------------------------ one test at a time
def test_two_tests_asked_in_the_same_moment_make_one_test(monkeypatch):
    ir, fo = _servers("cmp")
    gate = threading.Event()
    started = []
    monkeypatch.setattr(tunnel_test, "_compare_worker", lambda job: (started.append(job["id"]), gate.wait(5)))
    barrier = threading.Barrier(8)
    results = []

    def ask():
        barrier.wait()

        try:
            results.append(("ok", tunnel_test.compare_start(ir, fo, ["tcpmux"])))
        except RuntimeError as e:
            results.append(("refused", str(e)))

    threads = [threading.Thread(target=ask) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    gate.set()
    assert sorted(r[0] for r in results) == ["ok"] + ["refused"] * 7
    assert all("still running" in r[1] for r in results if r[0] == "refused")
    tunnel_test._COMPARE.clear()


def test_a_test_started_in_the_same_second_as_an_old_one_has_an_id_of_its_own(monkeypatch):
    ir, fo = _servers("cmp2")
    monkeypatch.setattr(tunnel_test, "_compare_worker", lambda job: None)
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)
    first = tunnel_test.compare_start(ir, fo, ["tcpmux"])
    tunnel_test._COMPARE[first]["done"] = True
    second = tunnel_test.compare_start(ir, fo, ["tcpmux"])
    assert first != second and set(tunnel_test._COMPARE) >= {first, second}
    tunnel_test._COMPARE.clear()


def test_a_test_that_cannot_start_its_thread_does_not_block_every_later_test(monkeypatch):
    ir, fo = _servers("cmp3")

    class NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(tunnel_test.threading, "Thread", NoThread)

    with pytest.raises(RuntimeError, match="thread"):
        tunnel_test.compare_start(ir, fo, ["tcpmux"])

    assert all(j["done"] for j in tunnel_test._COMPARE.values())
    tunnel_test._COMPARE.clear()


# ------------------------------------------------------------------ a story is always closed
def test_a_test_whose_cleanup_crashes_still_closes_its_story(monkeypatch):
    with session() as db:
        e = ActionLog(ref="test-9", action="tunnel_test", source="manual", story=["start"])
        db.add(e)
        db.flush()
        aid = e.id

    monkeypatch.setattr(tunnel_test, "_run", lambda *a: (True, "fine"))
    monkeypatch.setattr(tunnel_test, "_cleanup", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the database fell over")))
    tunnel_test._worker(aid, 0, "test-9")

    with session() as db:
        e = db.get(ActionLog, aid)
        assert e.finished_at > 0 and e.story[-1] == "fine"
