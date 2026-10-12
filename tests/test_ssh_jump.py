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
    assert opt[0] == "-o" and opt[1].startswith("ProxyCommand=ssh ") and " -W %h:%p root@203.0.113.7" in opt[1]
