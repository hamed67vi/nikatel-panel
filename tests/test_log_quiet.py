"""The ghost failed unit and a log that says each thing once (1.69.274). The owner, 2026-11-15, after the review of the project and its error log: "بله، درست کن". Two ghost units (unit files gone, failed state kept by
systemd) blocked every tunnel test of their servers - 82 ERRORs a week - and the log repeated the same lines of servers that are off about 2,300 times a day."""
import logging
import re
import subprocess

from nikatel import log as nklog
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import facts, tunnel_test, witness
from nikatel.ssh import ACTIONS, SCRIPTS, SSHError, render_action


class Rec:
    """A stand-in for a module's logger: (level, text) of every call."""

    def __init__(self):
        self.calls = []

    def _add(self, level, args):
        self.calls.append((level, args[0] % args[1:] if len(args) > 1 else args[0]))

    def warning(self, *a, **k):
        self._add("warning", a)

    def debug(self, *a, **k):
        self._add("debug", a)

    def info(self, *a, **k):
        self._add("info", a)


def levels(rec):
    return [c[0] for c in rec.calls]


# ------------------------------------------------------------------ the ghost unit
def test_the_purge_resets_the_failed_state_of_what_it_removes():
    script = render_action("purge_test_leftovers", {"TESTPORT": "39400"})
    assert 'systemctl reset-failed "$u"' in script                                    # (each unit it removes)
    assert "reset-failed" in script.split("daemon-reload")[1] and "nikatel-gre-test-*" in script.split("daemon-reload")[1]          # (and the test-unit patterns at the end)


def test_the_precheck_counts_only_failed_units_that_still_exist():
    line = next(ln for ln in SCRIPTS["build_facts"][0].splitlines() if ln.startswith('echo "BROKEN='))
    assert '$2=="loaded" && $3=="failed"' in line
    prog = re.search(r"awk '([^']+)'", line).group(1)
    listing = ("nikatel-gre-test-1.service not-found failed failed nikatel-gre-test-1.service\n"
               "backhaul-iran-tcpmux-9935.service loaded failed failed Nikatel Backhaul Tunnel\n"
               "backhaul-kharej-tcpmux-9935.service loaded active running Nikatel Backhaul Tunnel\n")
    assert subprocess.run(["awk", prog], input=listing, capture_output=True, text=True, check=False).stdout.split() == ["backhaul-iran-tcpmux-9935.service"]          # (the ghost is nothing to repair, the real failed unit is)


def test_the_orphan_sweep_does_not_list_a_ghost_as_a_leftover():
    script = SCRIPTS["orphan_look"][0] if "orphan_look" in SCRIPTS else ACTIONS["orphan_look"][0]
    line = next(ln for ln in script.splitlines() if ln.startswith("systemctl list-units --all"))
    assert '$2!="not-found"' in line
    prog = re.search(r"awk '([^']+)'", line).group(1)
    listing = "chisel-old-sb.service not-found failed failed chisel-old-sb.service\nchisel-new.service loaded active running chisel\n"
    assert subprocess.run(["awk", prog], input=listing, capture_output=True, text=True, check=False).stdout.split() == ["chisel-new.service", "active"]          # (a ghost has no files: nothing to take off, and it came back every hour)


# ------------------------------------------------------------------ what is an ERROR
def test_a_known_stop_is_one_warning_and_a_bug_is_an_error(caplog):      # (a RuntimeError - SSHError is one - is a stop the code raised itself; anything else is a crash)
    lg = logging.getLogger("test_quiet_stop")

    with caplog.at_level(logging.DEBUG, logger="test_quiet_stop"):
        nklog.known_stop_or_crash(lg, "build", 7, RuntimeError("the tunnel started but no data passes through it"))
        nklog.known_stop_or_crash(lg, "build", 8, SSHError("timeout after 40s"))
        nklog.known_stop_or_crash(lg, "build", 9, ValueError("something broke"))          # (a ValueError may be a bug: Python raises it too)

        try:
            raise KeyError("x")
        except KeyError as e:
            nklog.known_stop_or_crash(lg, "tunnel test", 10, e)

    recs = [r for r in caplog.records if r.name == "test_quiet_stop"]
    assert [r.levelno for r in recs] == [logging.WARNING, logging.WARNING, logging.ERROR, logging.ERROR]
    assert all(r.exc_info is None for r in recs[:2]) and recs[2].exc_info is not None and recs[3].exc_info is not None
    assert "build #7 stopped: the tunnel started but no data passes through it" in recs[0].getMessage() and "tunnel test #10 crashed" in recs[3].getMessage()


def test_the_quality_job_takes_a_tunnel_that_carries_nothing_as_its_result(monkeypatch):
    with session() as db:
        ir, fr = Server(name="lq-ir-1", host="203.0.113.211", role="iran", online=True), Server(name="lq-fr-1", host="198.51.100.211", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name="lq-t-1", type="backhaul", transport="tcpmux", port=9911, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8072], record={})
        db.add(t)
        db.flush()
        tid = t.id

    def carries_nothing(tunnel_id, speed=True):
        raise RuntimeError("no data passes through the tunnel right now")

    monkeypatch.setattr(tunnel_test, "quality", carries_nothing)
    mine = [r for r in tunnel_test.quality_all(speed=False) if r["id"] == tid]
    assert mine and "no data passes" in mine[0]["error"]                             # (it used to end the whole job with an ERROR and a traceback)


# ------------------------------------------------------------------ said once
def test_a_witness_that_fails_is_said_once_and_again_after_it_answered(monkeypatch):
    rec = Rec()
    monkeypatch.setattr(witness, "log", rec)
    monkeypatch.setattr(witness, "_FAILING", set())

    for _ in range(5):
        witness._failing("read", "w1", "does not answer: timed out")

    assert levels(rec) == ["warning", "debug", "debug", "debug", "debug"]
    witness._failing("mirror", "w1", "not mirrored: timed out")                      # (another thing of the same witness is its own line)
    assert levels(rec)[-1] == "warning"
    witness._answers("read", "w1")
    assert rec.calls[-1][0] == "info" and "answers again" in rec.calls[-1][1]
    witness._answers("read", "w1")                                                   # (nothing to say twice)
    assert rec.calls[-1][0] == "info" and len(rec.calls) == 7
    witness._failing("read", "w1", "does not answer: timed out")
    assert rec.calls[-1][0] == "warning"                                             # (it fails again: said again)


def test_a_server_whose_sample_fails_is_said_once_until_a_sample_passes(monkeypatch):
    rec = Rec()
    monkeypatch.setattr(facts, "log", rec)
    monkeypatch.setattr(facts, "_SAMPLE_FAILING", set())

    for _ in range(4):
        facts._sample_failed(34, "srv-off", "203.0.113.9", "ssh connection lost")

    facts._sample_failed(35, "srv-two", "203.0.113.10", "ssh connection lost")
    assert levels(rec) == ["warning", "debug", "debug", "debug", "warning"]          # (each server has its own first line)
    facts._SAMPLE_FAILING.discard(34)                                                # (what `sample_server` does when a sample passes)
    facts._sample_failed(34, "srv-off", "203.0.113.9", "ssh connection lost")
    assert levels(rec)[-1] == "warning"
