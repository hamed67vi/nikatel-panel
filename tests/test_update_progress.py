"""The progress of an update run, from its log (api/system.py: update_progress). The owner, 2026-10-15: no log box, only the percentage while it updates."""
from nikatel.api.system import update_progress


def test_an_update_has_a_percentage_from_its_steps_and_the_tests_take_most_of_it():
    assert update_progress("") == {"state": "idle", "percent": 0}
    assert update_progress("== update started\n") == {"state": "running", "percent": 3}
    assert update_progress("== update started\n== no secrets in the repo\nok\n")["percent"] == 5
    tests = "== update started\n== no secrets in the repo\n== tests\n" + "." * 70 + " [ 50%]\n"
    assert update_progress(tests) == {"state": "running", "percent": 45}                                  # 10 + 0.7 * 50
    assert update_progress(tests + "== migrations on a copy of the live database\n")["percent"] == 82
    assert update_progress(tests + "== migrations\n== install abc1234\n")["percent"] == 88
    assert update_progress(tests + "== install abc1234\nDEPLOY OK abc1234\n") == {"state": "done", "percent": 100}


def test_a_failed_or_silent_run_is_failed_and_an_old_run_does_not_count():
    old = "== update started\n== no secrets in the repo\nDEPLOY OK 1111111\n"
    assert update_progress(old + "== update started\n")["state"] == "running"                             # (the new run starts at its own marker: the old DEPLOY OK is behind it)
    assert update_progress("== update started\nfatal: not possible to fast-forward\n")["state"] == "failed"
    assert update_progress("== update started\n== no secrets in the repo\n== install x\nHEALTH CHECK FAILED\nrolled back: the panel runs 1 again\n")["state"] == "failed"
    assert update_progress("== update started\n== no secrets in the repo\n== tests\n..", quiet_s=300)["state"] == "failed"      # (nothing written for four minutes and not finished)
    assert update_progress("== update started\n== no secrets in the repo\n== tests\n..", quiet_s=30)["state"] == "running"