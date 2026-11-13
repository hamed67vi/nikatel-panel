"""A move of a dead Iran server must not die on one target (the owner, 2026-11-13: "an Iran server went down - why were its tunnels not moved to another one? ... fix it altogether, so that if it meets such a
problem again, it solves it").

What happened: the Doctor planned a dead server's tunnels onto another Iran server a minute after it went off and moved 0 of 3: every build on that target died in `scp` (a command answered there in a second, a bulk copy
from the panel's host stalled), and the panel's jump route was used for commands only. Then a failover that moved nothing was kept as "done" for six hours, and every later try picked the same target.

  * `ssh.copy_file`  - the panel's scp: ServerAlive options (a stalled copy ends in ~30 s) and a jump server when the direct copy stalls or drops, the same rule as a command
  * `ssh.push_probe` - 1 MB pushed to `cat >/dev/null`, direct then through a jump
  * failover         - a failed failover is tried again after RETRY_FAILED seconds (at most FAIL_TRIES times), a target that took nothing (or cannot be pushed a file) is left out for BAD_TARGET_TTL,
                       the target is vetted before a plan runs and before a move touches anything"""
import subprocess
import time
import types

import pytest

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot, failover
from nikatel.ssh import Node, SSHError

TARGET = Node("192.0.2.5", "root", 22)
JUMP = Node("192.0.2.9", "root", 22)


@pytest.fixture(autouse=True)
def _clean_jumps():
    ssh._VIA.clear()
    old = ssh._JUMPS
    ssh._JUMPS = None
    yield
    ssh._JUMPS = old
    ssh._VIA.clear()


def done(rc=0, err="", out=b""):
    return types.SimpleNamespace(returncode=rc, stderr=err, stdout=out)


def fake_run(monkeypatch, handler):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return handler(cmd, kw)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def via_jump(cmd) -> bool:
    return any(str(x).startswith("ProxyCommand=") for x in cmd)


# ------------------------------------------------------------------ ssh.copy_file
def test_a_copy_goes_direct_first_and_a_stall_ends_in_about_thirty_seconds(monkeypatch):
    calls = fake_run(monkeypatch, lambda cmd, kw: done())
    assert ssh.copy_file(TARGET, "/var/lib/x/backhaul", "/usr/local/bin/.b.tmp", timeout=240) == ""
    cmd, kw = calls[0]
    assert cmd[0] == "scp" and "ServerAliveInterval=10" in cmd and "ServerAliveCountMax=3" in cmd and "BatchMode=yes" in cmd and "ConnectTimeout=15" in cmd
    assert cmd[-2:] == ["/var/lib/x/backhaul", "root@192.0.2.5:/usr/local/bin/.b.tmp"] and not via_jump(cmd) and "-P" in cmd
    assert kw["timeout"] == 240 and len(calls) == 1


def test_a_copy_from_a_server_has_the_server_first(monkeypatch):
    calls = fake_run(monkeypatch, lambda cmd, kw: done())
    ssh.copy_file(TARGET, "/var/lib/x/backhaul", "/usr/local/bin/backhaul", to_server=False)
    assert calls[0][0][-2:] == ["root@192.0.2.5:/usr/local/bin/backhaul", "/var/lib/x/backhaul"]


@pytest.mark.parametrize("stall", ["timeout", "keepalive"])
def test_a_stalled_direct_copy_goes_through_a_jump_server_and_the_jump_is_kept(monkeypatch, stall):
    ssh._JUMPS = lambda node: [JUMP]

    def handler(cmd, kw):
        if via_jump(cmd):
            return done()

        if stall == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])

        return done(255, "Timeout, server 192.0.2.5 not responding.\n")

    calls = fake_run(monkeypatch, handler)
    assert ssh.copy_file(TARGET, "/f", "/r") == JUMP.host
    assert [via_jump(c[0]) for c in calls] == [False, True] and ssh.via_of(TARGET.host) == JUMP.host
    assert "-W" in " ".join(calls[1][0]) and "ServerAliveInterval=10" in calls[1][0]           # (the hop is the ssh -W of a command)
    ssh.copy_file(TARGET, "/f", "/r")
    assert [via_jump(c[0]) for c in calls] == [False, True, True]                              # the next copy goes the way that worked, at once


def test_a_jump_that_fails_is_forgotten_and_the_copy_tries_direct_again(monkeypatch):
    ssh._VIA[TARGET.host] = (JUMP, time.time() + 600)
    seen = []

    def handler(cmd, kw):
        seen.append(via_jump(cmd))
        return done(255, "Connection closed by 192.0.2.9\n") if via_jump(cmd) else done()

    fake_run(monkeypatch, handler)
    assert ssh.copy_file(TARGET, "/f", "/r") == ""
    assert seen == [True, False] and ssh.via_of(TARGET.host) == ""


def test_an_error_that_is_not_the_link_is_not_tried_through_a_jump(monkeypatch):
    ssh._JUMPS = lambda node: [JUMP]
    calls = fake_run(monkeypatch, lambda cmd, kw: done(1, "scp: /usr/local/bin/.b.tmp: No space left on device\n"))

    with pytest.raises(SSHError, match="No space left on device"):
        ssh.copy_file(TARGET, "/f", "/r")

    assert len(calls) == 1


def test_a_copy_that_no_way_carries_raises_the_first_error(monkeypatch):
    ssh._JUMPS = lambda node: [JUMP, Node("192.0.2.10", "root", 22), Node("192.0.2.11", "root", 22)]
    calls = fake_run(monkeypatch, lambda cmd, kw: done(255, "Timeout, server x not responding\n"))

    with pytest.raises(SSHError, match="ssh connection lost"):
        ssh.copy_file(TARGET, "/f", "/r")

    assert len(calls) == 3                                                                     # direct + two jump servers, no more


def test_the_error_of_a_stalled_copy_is_a_link_error_and_a_refused_key_is_not():
    stall = ssh._fail_of(255, "Timeout, server 192.0.2.5 not responding.")
    assert str(stall).startswith("ssh connection lost") and ssh._link_error(stall)
    key = ssh._fail_of(255, "root@192.0.2.5: Permission denied (publickey).")
    assert "not accepted" in str(key) and not ssh._link_error(key)


# ------------------------------------------------------------------ ssh.push_probe
def test_the_push_probe_sends_a_megabyte_to_nowhere_and_goes_through_a_jump_when_direct_stalls(monkeypatch):
    ssh._JUMPS = lambda node: [JUMP]

    def handler(cmd, kw):
        if via_jump(cmd):
            return done(0, out=b"nikatel-push-ok\n")

        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    calls = fake_run(monkeypatch, handler)
    assert ssh.push_probe(TARGET) == JUMP.host
    cmd, kw = calls[0]
    assert cmd[0] == "ssh" and cmd[-1] == "cat >/dev/null; echo nikatel-push-ok" and len(kw["input"]) == 1024 * 1024 and kw["timeout"] == 30
    assert len(calls) == 2


def test_the_push_probe_is_direct_when_direct_works_and_raises_when_nothing_does(monkeypatch):
    calls = fake_run(monkeypatch, lambda cmd, kw: done(0, out=b"nikatel-push-ok\n"))
    assert ssh.push_probe(TARGET) == "" and len(calls) == 1
    ssh._JUMPS = lambda node: [JUMP]
    fake_run(monkeypatch, lambda cmd, kw: done(0, out=b"banner only\n"))                        # (answered, but the command did not run: not a push)

    with pytest.raises(SSHError):
        ssh.push_probe(TARGET)


# ------------------------------------------------------------------ the callers use it
def test_every_file_copy_of_the_panel_goes_through_copy_file():
    from pathlib import Path

    nik = Path(__file__).resolve().parent.parent / "nikatel"
    users = []

    for f in nik.rglob("*.py"):
        if f.name != "ssh.py" and '"scp"' in f.read_text(encoding="utf-8"):
            users.append(str(f.relative_to(nik)))

    assert users == [], f"these files still run scp themselves (direct, no jump, no stall limit): {users}"


# ------------------------------------------------------------------ failover: _busy
def test_a_failover_that_moved_nothing_is_tried_again_after_a_minute_not_in_six_hours():
    now = int(time.time())
    fail = {"server_id": 5, "reason": "down", "at": now - 30, "moved": 0, "gone": 0, "ok": False}
    assert failover._busy({"pending": [], "history": [fail]}, 5, now) is True                  # half a minute ago: not yet
    fail["at"] = now - 65
    assert failover._busy({"pending": [], "history": [fail]}, 5, now) is False                 # a minute ago: again (on another target)


def test_a_server_whose_failovers_fail_again_and_again_is_tried_less_and_less_often_but_never_given_up():
    now = int(time.time())

    for n, gap in ((1, 60), (2, 120), (3, 240), (4, 480), (5, 600), (9, 600)):
        tries = [{"server_id": 5, "reason": "down", "at": now - 3000 - 100 * k, "moved": 0, "gone": 0, "ok": False} for k in range(n - 1)] + [{"server_id": 5, "reason": "down", "at": now - gap + 5, "moved": 0, "gone": 0, "ok": False}]
        assert failover._busy({"pending": [], "history": tries}, 5, now) is True, n               # just inside the wait of the n-th failure
        tries[-1]["at"] = now - gap - 5
        assert failover._busy({"pending": [], "history": tries}, 5, now) is False, n              # just after it: tried again

    assert failover.RETRY_STEPS[0] == 60 and max(failover.RETRY_STEPS) == 600 and not hasattr(failover, "FAIL_TRIES")


def test_a_failover_that_moved_something_still_holds_for_six_hours():
    now = int(time.time())
    some = {"server_id": 5, "reason": "down", "at": now - 7200, "moved": 1, "gone": 0, "ok": False, "tunnels": 3}
    assert failover._busy({"pending": [], "history": [some]}, 5, now) is True
    some["at"] = now - 6 * 3600 - 60
    assert failover._busy({"pending": [], "history": [some]}, 5, now) is False
    assert failover._busy({"pending": [], "history": [{"server_id": 5, "reason": "down", "at": now - 7200, "moved": 0, "gone": 2, "ok": False}]}, 5, now) is True      # (taken by another move: done)
    assert failover._busy({"pending": [], "history": [{"server_id": 5, "reason": "down", "at": now - 7200, "moved": 0, "gone": 0, "ok": True}]}, 5, now) is True


def test_a_failed_failover_of_another_server_says_nothing():
    now = int(time.time())
    assert failover._busy({"pending": [], "history": [{"server_id": 6, "reason": "down", "at": now - 60, "moved": 0, "gone": 0, "ok": False}]}, 5, now) is False


# ------------------------------------------------------------------ failover: the targets that failed
def test_a_target_that_took_nothing_is_not_a_target_for_two_hours(monkeypatch):
    now = time.time()
    failover.mark_bad_target(7, "a failover moved not one tunnel to it", now)
    assert failover.bad_targets(now + 60) == frozenset({7})
    assert failover.bad_targets(now + failover.BAD_TARGET_TTL + 1) == frozenset()
    failover.clear_bad_target(7)
    assert failover.bad_targets(now + 60) == frozenset()
    seen = {}
    monkeypatch.setattr(failover, "_make_plan", lambda *a, **k: seen.update(k) or {})
    failover.mark_bad_target(8, "x")
    failover.plan_dead(1)
    assert seen["exclude"] == frozenset({8}) and seen["defer"] is True                         # the plan of a dead server leaves it out of the ranking


def test_the_bad_targets_are_kept_with_the_failover_record():
    failover.mark_bad_target(9, "no push")
    assert failover._state()["bad"]["9"]["why"] == "no push"
    assert failover._state() is not None and "bad" in failover._state()


# ------------------------------------------------------------------ failover: the push probe before a move
def servers():
    with session() as db:
        dead = Server(name="dead", host="203.0.113.10", role="iran", online=False)
        a = Server(name="alpha", host="203.0.113.51", role="iran", online=True)
        b = Server(name="bravo", host="203.0.113.52", role="iran", online=True)
        f = Server(name="fx", host="203.0.113.200", role="foreign", online=True)
        db.add_all([dead, a, b, f])
        db.flush()
        db.add(Tunnel(name="d-t1", type="backhaul", transport="tcpmux", iran_server_id=dead.id, foreign_server_id=f.id, state="down", forwarded_ports=[8001]))
        return {"dead": dead.id, "a": a.id, "b": b.id, "f": f.id}


def plan_to(ids, target):
    return {"server_id": ids["dead"], "server": "dead", "ip": "203.0.113.10", "reason": "down", "why": "the server does not answer", "tunnels": [{"id": 1, "name": "d-t1"}], "dns": [], "pulled": [],
            "problems": [], "target": {"id": ids[target], "name": target, "ip": "203.0.113.51"}, "id": "t-1"}


def test_the_push_probe_of_a_target_is_asked_once_for_a_few_minutes(monkeypatch):
    ids = servers()
    asked = []
    monkeypatch.setattr(failover, "push_probe", lambda node: asked.append(node.host) or "")
    assert failover.target_pushable(ids["a"]) == (True, "direct") and failover.target_pushable(ids["a"]) == (True, "direct")
    assert asked == ["203.0.113.51"]                                                           # (the second answer came from the memory)
    monkeypatch.setattr(failover, "push_probe", lambda node: (_ for _ in ()).throw(SSHError("timeout after 30s")))
    ok, why = failover.target_pushable(ids["b"])
    assert ok is False and "cannot push a file" in why and "timeout after 30s" in why
    assert failover.target_pushable(999) == (False, "it is not in the list any more")
    monkeypatch.setattr(failover, "push_probe", lambda node: "192.0.2.9")
    failover._PUSH.clear()
    assert failover.target_pushable(ids["a"]) == (True, "through 192.0.2.9")


def test_a_plan_whose_target_cannot_take_a_file_is_made_again_without_it(monkeypatch):
    ids = servers()
    monkeypatch.setattr(failover, "PUSH_CHECK", True)
    monkeypatch.setattr(failover, "target_pushable", lambda tid, now=None: (tid != ids["a"], "the panel cannot push a file to it: timeout after 30s" if tid == ids["a"] else "direct"))
    again = []

    def plan_dead(sid):
        again.append(failover.bad_targets())
        return {**plan_to(ids, "b"), "target": {"id": ids["b"], "name": "bravo", "ip": "203.0.113.52"}}

    monkeypatch.setattr(failover, "plan_dead", plan_dead)
    p = failover._vet_target(plan_to(ids, "a"), ids["dead"])
    assert p["target"]["id"] == ids["b"] and again == [frozenset({ids["a"]})]                  # the plan was made again with the bad one left out
    assert any(x.startswith("a: ") and "cannot push" in x for x in p["problems"]), p["problems"]          # (the plan names its target "a")


def test_when_no_target_can_take_a_file_the_plan_has_none_and_says_why(monkeypatch):
    ids = servers()
    monkeypatch.setattr(failover, "PUSH_CHECK", True)
    monkeypatch.setattr(failover, "target_pushable", lambda tid, now=None: (False, "the panel cannot push a file to it: timeout after 30s"))
    monkeypatch.setattr(failover, "plan_dead", lambda sid: {**plan_to(ids, "b"), "target": None, "problems": ["no other Iran server can carry all of these tunnels"]})
    p = failover._vet_target(plan_to(ids, "a"), ids["dead"])
    assert p["target"] is None and any(x.startswith("a: ") for x in p["problems"]), p["problems"]
    assert failover.bad_targets() == frozenset({ids["a"]})


def test_a_good_target_is_left_alone_and_the_switch_off_asks_nothing(monkeypatch):
    ids = servers()
    monkeypatch.setattr(failover, "PUSH_CHECK", True)
    monkeypatch.setattr(failover, "target_pushable", lambda tid, now=None: (True, "direct"))
    p = plan_to(ids, "a")
    assert failover._vet_target(p, ids["dead"]) is p
    monkeypatch.setattr(failover, "PUSH_CHECK", False)
    monkeypatch.setattr(failover, "target_pushable", lambda *a, **k: pytest.fail("asked although the check is off"))
    assert failover._vet_target(p, ids["dead"]) is p


def test_a_move_whose_target_cannot_take_a_file_touches_nothing(monkeypatch):
    ids = servers()
    monkeypatch.setattr(failover, "PUSH_CHECK", True)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append((line, done, ok)))
    monkeypatch.setattr(failover, "target_pushable", lambda tid, now=None: (False, "the panel cannot push a file to it: timeout after 30s"))
    monkeypatch.setattr(autopilot, "_replace", lambda *a, **k: pytest.fail("a tunnel was touched"))
    monkeypatch.setattr(autopilot, "_snapshot", lambda *a, **k: pytest.fail("a tunnel was touched"))
    assert failover._work(7, plan_to(ids, "a")) is False
    assert said[-1][1] is True and said[-1][2] is False and "Nothing was touched" in said[-1][0] and "the Doctor does that by itself" in said[-1][0]
    assert failover.bad_targets() == frozenset({ids["a"]})
    assert failover._state()["history"] == []                                                  # (it never started: no record of a move)


def test_a_failover_that_moved_nothing_marks_its_target_and_one_that_moved_something_clears_it(monkeypatch):
    ids = servers()
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: None)
    monkeypatch.setattr(autopilot, "_replace", lambda *a, **k: False)                          # (every build fails, as on a target that cannot be pushed a file)
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: {"id": tid, "name": "d-t1", "foreign_id": ids["f"], "type": "backhaul", "transport": "tcpmux", "record": {}, "port": 9900})
    monkeypatch.setattr(failover, "_best_kind", lambda *a, **k: "tcpmux")
    assert failover._work(7, plan_to(ids, "a")) is False
    hist = failover._state()["history"]
    assert hist[-1]["moved"] == 0 and hist[-1]["ok"] is False and ids["a"] in failover.bad_targets()
    assert failover._busy(failover._state(), ids["dead"], int(time.time())) is True            # (a moment ago: wait)
    assert failover._busy(failover._state(), ids["dead"], int(time.time()) + failover.RETRY_STEPS[0] + 5) is False      # (and then it is tried again - on another target: plan_dead leaves `a` out)
    monkeypatch.setattr(autopilot, "_replace", lambda *a, **k: True)
    monkeypatch.setattr("nikatel.services.builder.follow_move", lambda name, custom=False: name)
    assert failover._work(8, {**plan_to(ids, "a"), "id": "t-2"}) is True
    assert ids["a"] not in failover.bad_targets()


# ------------------------------------------------------------------ faster: the DNS follows early, the type is built at once
def dead_server_with(n):
    with session() as db:
        dead = Server(name="dead", host="203.0.113.10", role="iran", online=False)
        a = Server(name="alpha", host="203.0.113.51", role="iran", online=True)
        f = Server(name="fx", host="203.0.113.200", role="foreign", online=True)
        db.add_all([dead, a, f])
        db.flush()
        ts = [Tunnel(name=f"d-t{k}", type="backhaul", transport="tcpmux", iran_server_id=dead.id, foreign_server_id=f.id, state="down", forwarded_ports=[8000 + k]) for k in range(1, n + 1)]
        db.add_all(ts)
        db.flush()
        return {"dead": dead.id, "a": a.id, "f": f.id, "t": [x.id for x in ts]}


def dead_plan(ids, dns):
    return {"server_id": ids["dead"], "server": "dead", "ip": "203.0.113.10", "reason": "down", "why": "the server does not answer", "whole": True, "pulled": [], "problems": [], "id": "t-9",
            "tunnels": [{"id": i, "name": f"d-t{k}"} for k, i in enumerate(ids["t"], 1)], "dns": dns, "left_alone": [], "target": {"id": ids["a"], "name": "alpha", "ip": "203.0.113.51"}}


def rec(name, serves):
    return {"id": name, "fqdn": f"{name}.example.test", "domain": "example.test", "account": "arvan", "serves": serves, "configs": []}


def test_a_dns_record_follows_as_soon_as_every_tunnel_it_serves_has_moved(monkeypatch):
    import threading

    ids = dead_server_with(2)
    t1, t2 = ids["t"]
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: None)
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: {"id": tid, "name": f"d-t{tid}", "foreign_id": ids["f"], "foreign_name": "fx", "type": "backhaul", "transport": "tcpmux", "record": {}, "port": 9900})
    monkeypatch.setattr(failover, "_best_kind", lambda *a, **k: "tcpmux")
    monkeypatch.setattr("nikatel.services.builder.follow_move", lambda name, custom=False: name)
    first_dns = threading.Event()
    applied, order = [], []

    def replace(aid, tunnel_id, iran_id, foreign_id, kind, why, skip_offline=False, **kw):
        if tunnel_id == t2:
            assert first_dns.wait(10), "the record of the first tunnel was not re-pointed before the second tunnel was built"          # (the second build starts only after the first record followed)

        order.append(("built", tunnel_id))
        return True

    def apply(aid, pp):
        names = [d["id"] for d in pp["dns"]]
        applied.append(names)
        order.append(("dns", names))

        if names == ["r1"]:
            first_dns.set()

        return [{"fqdn": d["fqdn"], "account": "arvan"} for d in pp["dns"]], [], []

    monkeypatch.setattr(autopilot, "_replace", replace)
    monkeypatch.setattr(failover, "_apply_dns", apply)
    dns = [rec("r1", [t1]), rec("r2", [t2]), rec("r3", [t1, t2]), rec("r4", [])]
    # r1 serves only the first tunnel, r2 only the second, r3 both, r4 none (a record of the whole server)
    assert failover._work(7, dead_plan(ids, dns)) is True
    assert applied == [["r1"], ["r2", "r3"], ["r4"]]                           # r1 as soon as t1 moved; r2 and r3 as soon as t2 moved (r3 needs both); r4 (tied to no tunnel) at the end
    assert order.index(("dns", ["r1"])) < order.index(("built", t2)) < order.index(("dns", ["r2", "r3"]))
    hist = failover._state()["history"][-1]
    assert hist["moved"] == 2 and hist["ok"] is True and sorted(d["fqdn"] for d in hist["dns"]) == [f"r{k}.example.test" for k in (1, 2, 3, 4)]      # (the history holds every record once)


def test_a_partial_move_keeps_the_early_records_and_leaves_the_rest_alone(monkeypatch):
    ids = dead_server_with(2)
    t1, t2 = ids["t"]
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: {"id": tid, "name": f"d-t{tid}", "foreign_id": ids["f"], "foreign_name": "fx", "type": "backhaul", "transport": "tcpmux", "record": {}, "port": 9900})
    monkeypatch.setattr(failover, "_best_kind", lambda *a, **k: "tcpmux")
    monkeypatch.setattr("nikatel.services.builder.follow_move", lambda name, custom=False: name)
    monkeypatch.setattr(autopilot, "_replace", lambda aid, tunnel_id, *a, **k: tunnel_id == t1)          # (the second tunnel does not come up)
    calls = []
    monkeypatch.setattr(failover, "_apply_dns", lambda aid, pp: (calls.append([d["id"] for d in pp["dns"]]), ([{"fqdn": d["fqdn"], "account": "arvan"} for d in pp["dns"]], [], []))[1])
    dns = [rec("r1", [t1]), rec("r2", [t2]), rec("r3", [t1, t2]), rec("r4", [])]
    assert failover._work(7, dead_plan(ids, dns)) is False
    assert calls == [["r1"], []]                                               # r1 followed early; at the end nothing else qualifies (r2 and r3 serve the tunnel that did not move, r4 serves nothing)
    hist = failover._state()["history"][-1]
    assert hist["moved"] == 1 and [d["fqdn"] for d in hist["dns"]] == ["r1.example.test"]
    assert any("only the 1 DNS record(s)" in x for x in said) and any("r2.example.test" in x and "left alone" in x for x in said)


def test_a_tunnel_is_built_as_the_type_it_has_when_the_doctors_order_puts_it_first(monkeypatch):
    from nikatel.services import knowledge

    monkeypatch.setattr(failover, "PICK_BEST", True)
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    snap = {"name": "d-t1", "foreign_id": 3, "foreign_name": "fx", "type": "backhaul", "transport": "tcpmux", "record": {}}
    target = {"id": 4, "name": "alpha"}
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: (["tcpmux", "paqet"], []))
    assert failover._fast_kind(1, snap, target) == "tcpmux" and "built at once" in said[-1]
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: (["chisel:fwd", "tcpmux"], []))
    assert failover._fast_kind(1, snap, target) == ""                           # another type comes first: the test runs as before
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: ([], []))
    assert failover._fast_kind(1, snap, target) == ""
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no knowledge")))
    assert failover._fast_kind(1, snap, target) == ""                           # a look that fails does not stop a move
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: (["tcpmux"], []))
    monkeypatch.setattr(failover, "FAST_KIND", False)
    assert failover._fast_kind(1, snap, target) == ""
    monkeypatch.setattr(failover, "FAST_KIND", True)
    monkeypatch.setattr(failover, "PICK_BEST", False)
    assert failover._fast_kind(1, snap, target) == ""


def test_the_type_built_at_once_that_does_not_come_up_is_followed_by_the_test_and_a_second_build(monkeypatch):
    ids = dead_server_with(1)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: {"id": tid, "name": "d-t1", "foreign_id": ids["f"], "foreign_name": "fx", "type": "backhaul", "transport": "tcpmux", "record": {}, "port": 9900})
    monkeypatch.setattr(failover, "_fast_kind", lambda aid, snap, target: "tcpmux")
    tested, built = [], []
    monkeypatch.setattr(failover, "_best_kind", lambda *a, **k: tested.append(1) or "paqet")
    monkeypatch.setattr(autopilot, "_replace", lambda aid, tid, iran, foreign, kind, why, skip_offline=False, **kw: built.append(kind) or kind == "paqet")
    monkeypatch.setattr("nikatel.services.builder.follow_move", lambda name, custom=False: name)
    monkeypatch.setattr(failover, "_apply_dns", lambda aid, pp: ([], [], []))
    assert failover._work(7, dead_plan(ids, [])) is True
    assert built == ["tcpmux", "paqet"] and tested == [1] and any("did not build" in x and "tested now" in x for x in said)
    built.clear()
    tested.clear()
    monkeypatch.setattr(failover, "_fast_kind", lambda aid, snap, target: "")        # (nothing known: tested first, built once - as before)
    failover._state()["history"].clear()
    assert failover._work(8, {**dead_plan(ids, []), "id": "t-10"}) is True
    assert built == ["paqet"] and tested == [1]
