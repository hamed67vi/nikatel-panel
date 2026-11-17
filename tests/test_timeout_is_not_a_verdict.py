"""A build that timed out is not a verdict on the route (1.69.283; the owner, 2026-11-16, «بله، درست کن»): a build step that stopped with `timeout after 30s` (the panel's ssh gave up waiting for a server) was kept as a plain FAILED kind and
the pair left that kind alone for 14 days. On 2026-11-16 ten kinds on three pairs were blocked that way - three of them on the new Turkish pair within the hour they were first tried. A timeout says nothing about the route: like a server that
went off, the kind is "not tested" and asked again after a day."""
from nikatel.services import knowledge

NOW = 1_800_000_000
DAY = 86400
TIMEOUT = "Build failed: Stopped: timeout after 30s. Everything created was removed again."          # (as the Doctor kept it on 2026-11-16)
TIMEOUT20 = "Build failed: Stopped: timeout after 20s. Everything created was removed again."
IPIP = "Build failed: Stopped: the IPIP link is up but the inner addresses cannot ping each other (Iran→foreign 0/10, foreign→Iran 0/10)"
SLOW_LINK = "Build failed: Stopped: the GRE over UDP (FOU) link answered at first but then lost its packets (3/20 after 10 s): the path filters this"


def test_a_timeout_is_a_server_that_did_not_answer_and_a_real_failure_is_not():
    assert knowledge.server_lost(TIMEOUT) and knowledge.server_lost(TIMEOUT20) and knowledge.server_lost(["Preflight: both servers inspected", "Stopped: timeout after 120s"])
    assert not knowledge.server_lost(IPIP) and not knowledge.server_lost(SLOW_LINK) and not knowledge.server_lost("Score 43/100 - poor.") and not knowledge.server_lost("the test door did not answer")        # (what the route itself said stays a verdict)


def test_a_kind_that_timed_out_is_not_tested_and_is_not_kept_as_a_failure():
    e = knowledge.merge(None, {"kind": "bpd:pck", "ok": False, "error": TIMEOUT}, "full", NOW)
    assert e["last"]["skipped"] == "server" and knowledge.verdict(e) == "untested" and "hist" not in e                         # (no line in the history: the stability of a type is not made of timeouts)
    real = knowledge.merge(None, {"kind": "bpd:pck", "ok": False, "error": IPIP}, "full", NOW)
    assert knowledge.verdict(real) == "failed" and len(real["hist"]) == 1
    carried = knowledge.merge(None, {"kind": "bpd:pck", "ok": True, "works": True, "score": 70, "error": TIMEOUT}, "latency", NOW)
    assert knowledge.verdict(carried) == "good"                                                                                # (a build that carried data is never "not tested", whatever its story says)


def test_what_was_kept_before_is_read_again_as_not_tested_and_is_asked_after_a_day_not_after_two_weeks():
    old = {"last": {"ok": False, "measured": False, "at": NOW, "measure": "full", "score": None, "error": TIMEOUT[:140]}, "hist": [{"at": NOW, "ok": False, "m": "full"}]}          # the entry as 1.69.281 kept it: a failure
    bad = {"last": {"ok": False, "measured": False, "at": NOW, "measure": "full", "score": None, "error": IPIP[:140]}, "hist": [{"at": NOW, "ok": False, "m": "full"}]}
    pair = {"kinds": {"bpd:pck": old, "paqet": {**old, "last": {**old["last"], "error": TIMEOUT20[:140]}}, "bp:ws": bad}}
    assert knowledge.verdict(old) == "untested" and knowledge.blocked(pair, NOW + 3600) == ["bp:ws"]                           # (the timeouts are not blocked any more, the real failure still is)
    assert [x["kind"] for x in knowledge.untested(pair)] == ["bpd:pck", "paqet"]
    assert knowledge.due_kinds(pair, ["bpd:pck", "paqet", "bp:ws"], NOW + 3600) == []                                          # (an hour later: nothing is due yet)
    assert knowledge.due_kinds(pair, ["bpd:pck", "paqet", "bp:ws"], NOW + DAY + 60) == ["bpd:pck", "paqet"]                    # (a day later the two are tried again - the real failure waits its two weeks)
    assert knowledge.due_kinds(pair, ["bp:ws"], NOW + knowledge.FAIL_FRESH + 60) == ["bp:ws"]
