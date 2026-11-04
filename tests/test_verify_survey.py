"""The Doctor tests the running types of the live pairs again and again (1.69.220; the owner, 2026-11-05: "test which type is the best and the steadiest - always examine the network, not only when there is a problem -
and always replace a tunnel when a better one exists"). Before, a better type could never be called "proven steady": the night survey tests a kind once a week and three tests are needed, and it asks only delay and
loss, while an upgrade needs a FULL test (a speed) of the running type and of the other one. Now a live pair gets full tests of its running type and its best rivals, spread over the day, until each has three."""
from nikatel.config import get_settings
from nikatel.services import autopilot, knowledge
from nikatel.services import settings as st

NOW = 1_800_000_000
H = 3600


def kind(score=70, down=500.0, tests=()):
    """One entry of the pair's memory: its last full test and a line for every test kept ((hours ago, measure))."""
    full = {"score": score, "at": NOW - 3 * H, "down_mbps": down, "latency_ms": 90.0, "loss_pct": 0.0, "jitter_ms": 2.0}
    return {"last": {"ok": True, "measured": True, "at": NOW - 3 * H, "measure": "full", "score": score, "error": ""}, "full": full,
            "hist": [{"at": NOW - h * H, "ok": True, "m": m, "down": down} for h, m in tests]}


def pair(**kinds):
    return {"at": NOW, "kinds": kinds}


def test_the_running_type_and_its_best_rivals_are_tested_until_each_has_three_full_tests():
    p = pair(running=kind(60), a=kind(90), b=kind(80), c=kind(70), d=kind(65), zero=kind(10))
    assert knowledge.verify_kinds(p, NOW, "running") == ["running", "a", "b", "c"]                                      # (the running type first, then its three best rivals)
    p = pair(running=kind(60, tests=[(30, "full"), (20, "full"), (10, "full")]), a=kind(90, tests=[(8, "full")]), b=kind(80, tests=[(2, "full")]), c=kind(70))
    assert knowledge.verify_kinds(p, NOW, "running") == ["a", "c"]                                                       # (three full tests: done; one 8 h ago: another is due; one 2 h ago: too soon)
    p = pair(running=kind(60, tests=[(30, "latency"), (20, "latency"), (10, "latency")]))
    assert knowledge.verify_kinds(p, NOW, "running") == ["running"]                                                      # (latency tests are no speed: they do not count)
    p = pair(running=kind(60, tests=[(200, "full")]), a=kind(90))
    assert "running" in knowledge.verify_kinds(p, NOW, "running")                                                        # (a full test of eight days ago is old)


def test_the_licence_and_a_type_that_never_worked_keep_a_kind_out():
    p = pair(running=kind(60), a=kind(90), bad={"last": {"ok": False, "measured": False, "at": NOW, "measure": "full", "score": None, "error": "did not connect"}, "hist": []})
    assert knowledge.verify_kinds(p, NOW, "running", allowed=lambda k: k != "a") == ["running"]
    assert knowledge.verify_kinds(p, NOW, "running") == ["running", "a"]                                                 # (a kind that did not connect is nobody's rival)
    assert knowledge.verify_kinds(None, NOW, "running") == ["running"]                                                   # (nothing known about the pair: the running type is the first thing to know)


def test_a_type_that_carries_one_and_a_half_times_and_fifty_mbit_more_is_better():
    cur = kind(60, down=100.0)
    better = knowledge.upgrade_kinds(pair(running=cur, fast=kind(70, down=160.0)), NOW, "running")
    assert [r["kind"] for r in better] == ["fast"]
    assert knowledge.upgrade_kinds(pair(running=cur, near=kind(70, down=140.0)), NOW, "running") == []                  # (1.4 times: not enough)
    assert knowledge.upgrade_kinds(pair(running=kind(60, down=20.0), small=kind(70, down=40.0)), NOW, "running") == []   # (twice as much, but only 20 Mb/s more: a few Mb/s is no reason to touch a tunnel)


# ------------------------------------------------------------------ the job
def _world(monkeypatch, cold=(), verify=((1, 2, ["a", "b"]),), quiet=""):
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(knowledge.jobs, "stopping", lambda: False)
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(autopilot, "repairing_names", list)
    st.put("knowledge", {"on": True, "from_hour": 0, "to_hour": 0})
    started = []
    monkeypatch.setattr(knowledge, "pending_pairs", lambda now, cold_only=False: list(cold))
    monkeypatch.setattr(knowledge, "verify_pairs", lambda now: list(verify))
    monkeypatch.setattr(knowledge, "not_now", lambda i, f, now, quiet_mbps=knowledge.QUIET_MBPS: quiet)
    monkeypatch.setattr(knowledge, "survey_start", lambda i, f, source="survey-manual", kinds=None: started.append((i, f, source, kinds)) or "job")
    monkeypatch.setattr(knowledge, "_running", lambda: None)
    return started


def test_the_job_starts_the_verify_survey_when_every_pair_has_been_surveyed_and_the_servers_are_quiet(monkeypatch):
    started = _world(monkeypatch)
    knowledge.run()
    assert started == [(1, 2, "survey-verify", ["a", "b"])]


def test_a_pair_nobody_has_tested_comes_first_and_a_busy_pair_waits(monkeypatch):
    started = _world(monkeypatch, cold=[(5, 6, ["tcpmux"])])
    knowledge.run()
    assert [s[2] for s in started] == ["survey"] and started[0][:2] == (5, 6)                                            # (the cold pair is surveyed, not verified)
    started = _world(monkeypatch, quiet="its servers carry 900 Mb/s for users")
    knowledge.run()
    assert started == []


def test_nothing_is_verified_when_the_survey_is_off_or_a_repair_runs(monkeypatch):
    started = _world(monkeypatch)
    st.put("knowledge", {"on": False})
    knowledge.run()
    assert started == []
    st.put("knowledge", {"on": True})
    monkeypatch.setattr(autopilot, "repairing_names", lambda: ["t1"])
    knowledge.run()
    assert started == []
