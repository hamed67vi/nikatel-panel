"""A server whose direct SSH session stalls is reached through another Iran server; the panel remembers it and tries direct again later."""
import time

import pytest

from nikatel import ssh
from nikatel.ssh import Node, SSHError


@pytest.fixture(autouse=True)
def _clean():
    ssh._VIA.clear()
    ssh.set_jump_provider(None)
    yield
    ssh._VIA.clear()
    ssh.set_jump_provider(None)


def test_a_stalled_direct_session_goes_through_a_jump_and_is_remembered(monkeypatch):
    calls = []
    target, jump = Node("203.0.113.9"), Node("203.0.113.7")

    def once(node, script, timeout, j=None):
        calls.append((node.host, j.host if j else None, timeout))

        if j is None:
            raise SSHError("timeout after 35s")

        return "OK"

    monkeypatch.setattr(ssh, "_exec_once", once)
    ssh.set_jump_provider(lambda n: [jump])
    assert ssh._exec(target, "x", 35) == "OK"
    assert calls == [("203.0.113.9", None, 35), ("203.0.113.9", "203.0.113.7", 35)]
    assert ssh.via_of("203.0.113.9") == "203.0.113.7"
    calls.clear()
    assert ssh._exec(target, "x", 35) == "OK"                   # the next call goes the known way at once, no time-out first
    assert calls == [("203.0.113.9", "203.0.113.7", 35)]


def test_after_the_ten_minutes_direct_is_tried_again_with_a_short_time_out(monkeypatch):
    target, jump = Node("203.0.113.9"), Node("203.0.113.7")
    ssh._VIA["203.0.113.9"] = (jump, time.time() - 1)           # expired
    seen = []

    def once(node, script, timeout, j=None):
        seen.append((j.host if j else None, timeout))
        return "DIRECT"

    monkeypatch.setattr(ssh, "_exec_once", once)
    ssh.set_jump_provider(lambda n: [jump])
    assert ssh._exec(target, "x", 40) == "DIRECT" and seen == [(None, 20)]


def test_a_script_error_or_a_refused_key_is_not_a_reason_to_take_a_detour(monkeypatch):
    calls = []

    def once(node, script, timeout, j=None):
        calls.append(j)
        raise SSHError("the panel's key is not accepted (authorized_keys)", 255)

    monkeypatch.setattr(ssh, "_exec_once", once)
    ssh.set_jump_provider(lambda n: [Node("203.0.113.7")])

    with pytest.raises(SSHError):
        ssh._exec(Node("203.0.113.9"), "x", 35)

    assert calls == [None]

    def failing_script(node, script, timeout, j=None):
        raise SSHError("exit 1", 1)

    monkeypatch.setattr(ssh, "_exec_once", failing_script)

    with pytest.raises(SSHError):
        ssh._exec(Node("203.0.113.9"), "x", 35)


def test_the_jump_command_is_one_quoted_proxy_option():
    opt = ssh._proxy_option(Node("203.0.113.7", "root", 22))
    assert opt[0] == "-o" and opt[1].startswith("ProxyCommand=ssh ") and " -W %h:%p -- root@203.0.113.7" in opt[1]


# a build to an Iran server that is cut from abroad primes the jump up front, so no step waits on a direct time-out (the owner, 2026-11-02)
def test_ensure_via_keeps_a_fresh_jump_without_probing(monkeypatch):
    target, jump = Node("203.0.113.9"), Node("203.0.113.7")
    ssh._VIA["203.0.113.9"] = (jump, time.time() + 300)
    probed = []
    monkeypatch.setattr(ssh, "_exec_once", lambda *a, **k: probed.append(1) or "OK")
    ssh.set_jump_provider(lambda n: [jump])
    assert ssh.ensure_via(target) == "203.0.113.7" and probed == []                     # the known jump is kept, nothing is probed


def test_ensure_via_primes_the_first_jump_that_answers_and_the_next_call_goes_straight_through_it(monkeypatch):
    target, j1, j2 = Node("203.0.113.9"), Node("203.0.113.6"), Node("203.0.113.7")
    tried = []

    def once(node, script, timeout, j=None):
        tried.append(j.host if j else None)

        if j is None or j.host == "203.0.113.6":
            raise SSHError("timeout after 20s")

        return "nikatel-via-ok"

    monkeypatch.setattr(ssh, "_exec_once", once)
    ssh.set_jump_provider(lambda n: [j1, j2])
    assert ssh.ensure_via(target) == "203.0.113.7" and tried == ["203.0.113.6", "203.0.113.7"]      # the first that answers is kept
    assert ssh.via_of("203.0.113.9") == "203.0.113.7"
    tried.clear()
    assert ssh._exec(target, "x", 40) == "nikatel-via-ok" and tried == ["203.0.113.7"]              # no direct attempt first


def test_ensure_via_does_nothing_when_no_jump_is_set_or_none_answers(monkeypatch):
    target = Node("203.0.113.9")
    assert ssh.ensure_via(target) == "" and "203.0.113.9" not in ssh._VIA                           # no provider: the build behaves as before
    ssh.set_jump_provider(lambda n: [Node("203.0.113.7")])
    monkeypatch.setattr(ssh, "_exec_once", lambda *a, **k: (_ for _ in ()).throw(SSHError("timeout after 20s")))
    assert ssh.ensure_via(target) == "" and "203.0.113.9" not in ssh._VIA                           # none answered: nothing cached


def test_a_build_primes_the_jump_for_a_cut_from_abroad_iran_server():
    import inspect

    from nikatel.services import builder

    src = inspect.getsource(builder._build)
    assert "ensure_via(iran)" in src and "IRAN_ONLY" in src and "via_of(ihost)" in src              # the build routes through a jump when the Iran server is reached only that way
