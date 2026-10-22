"""The update of the panel itself (services/updater.py): the percentage from the small file scripts/deploy.sh keeps, what the About card may say, and the start of a run.
The owner, 2026-10-17: the update was very slow, the percentage started again when the page was refreshed, and the card said a new version existed while the update was not finished."""
import json
import subprocess
import time

import pytest

from nikatel import __version__
from nikatel.services import updater

NOW = 1_800_000_000.0


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "PROGRESS_FILE", tmp_path / "update-progress.json")
    monkeypatch.setattr(updater, "LOG_FILE", tmp_path / "update.log")
    monkeypatch.setattr(updater, "_remote", {"at": 0.0, "head": "", "tag": "", "ok": False})
    return tmp_path


def put(**rec):
    updater.PROGRESS_FILE.write_text(json.dumps({"run": 1, "at": time.time(), "from": __version__, "to": "999.0.0", "reason": "", **rec}))


def fake_git(monkeypatch, cur="abc1234", head="abc1234fff", tag="v" + __version__):
    calls = []

    def git(*args, timeout=40):
        calls.append(args)

        if args[:1] == ("rev-parse",):
            return cur

        if args[:1] == ("ls-remote",):
            return (f"{head}\tHEAD\n" if head else "") + (f"deadbeef\trefs/tags/{tag}\ndeadbeef\trefs/tags/{tag}^{{}}\nfeedface\trefs/tags/v0.0.1\n" if tag else "")

        if args[:2] == ("remote", "get-url"):
            return "https://github.com/example/panel.git"

        return ""

    monkeypatch.setattr(updater, "_git", git)
    return calls


def test_the_percentage_creeps_inside_a_step_and_never_reaches_the_next_one():
    order = ["download", "checks", "install", "restart", "health"]

    for step, nxt in zip(order, order[1:] + [""]):
        a, b, _ = updater.STEPS[step]
        seen = [updater.percent_of(step, t) for t in (0, 1, 3, 8, 30, 600)]
        assert seen[0] == a and seen == sorted(seen) and seen[-1] < b                  # forward only, and short of the next step

        if nxt:
            assert updater.STEPS[nxt][0] == b                                           # the next step starts where this one stops: the bar never jumps

    assert updater.percent_of("something-new", 5) == updater.percent_of("download", 5)      # a step the page does not know: the first band
    assert updater.percent_of("install", -5) == updater.STEPS["install"][0]                # a clock that went back


def test_a_run_is_idle_running_done_or_failed_by_what_the_script_wrote():
    assert updater.progress(None, NOW) == {"state": "idle", "percent": 0} and updater.progress({}, NOW)["state"] == "idle"
    run = {"run": 1, "step": "install", "at": NOW - 6, "from": "1.0.0", "to": "1.0.1"}
    p = updater.progress(run, NOW)
    assert p["state"] == "running" and 30 < p["percent"] < 55 and (p["from"], p["to"], p["step"]) == ("1.0.0", "1.0.1", "install")
    done = updater.progress({**run, "step": "done", "at": NOW - 10 ** 6}, NOW)
    assert done["state"] == "done" and done["percent"] == 100                           # a finished run does not go stale
    f = updater.progress({**run, "step": "failed", "reason": "the new version did not answer"}, NOW)
    assert f["state"] == "failed" and f["reason"] == "the new version did not answer"
    assert updater.progress({**run, "step": "failed"}, NOW)["reason"] == "the update stopped"
    stale = updater.progress({**run, "at": NOW - updater.STALE_AFTER - 1}, NOW)
    assert stale["state"] == "failed" and "stopped" in stale["reason"]                  # the script died without a word: it is not "running" for ever


def test_every_page_sees_the_same_percentage_at_the_same_moment(files):
    put(step="checks", at=NOW - 5)
    a = updater.read_progress(NOW)
    assert updater.read_progress(NOW) == a                                              # a refresh asks again and gets the same number: nothing starts from 1
    assert updater.read_progress(NOW + 3)["percent"] > a["percent"] > updater.STEPS["checks"][0]


def test_a_missing_or_broken_file_is_idle_and_the_file_is_replaced_in_one_move(files):
    assert updater.read_progress(NOW)["state"] == "idle"
    updater.PROGRESS_FILE.write_text("{not json")
    assert updater.read_progress(NOW)["state"] == "idle"
    updater.PROGRESS_FILE.write_text("[1, 2]")
    assert updater.read_progress(NOW)["state"] == "idle"
    updater.write_progress({"run": 1, "step": "download", "at": NOW})
    updater.write_progress({"run": 1, "step": "checks", "at": NOW + 1})
    assert json.loads(updater.PROGRESS_FILE.read_text())["step"] == "checks" and not list(files.glob("*.tmp"))


def test_versions_compare_as_numbers():
    assert updater.version_key("1.62.1") > updater.version_key("1.62.0") and updater.version_key("1.10.0") > updater.version_key("1.9.0")
    assert updater.version_key("") == () and updater.version_key("not.a.version") == ()


def test_the_card_is_up_to_date_only_when_it_is_known_and_nothing_runs(files, monkeypatch):
    fake_git(monkeypatch)
    s = updater.status()
    assert s["up_to_date"] is True and s["checked"] is True and s["newer"] is False and s["notes_url"] == "" and s["progress"]["state"] == "idle"
    assert s["version"] == __version__ and s["installed"] == "abc1234" and s["latest"] == __version__      # (the peeled tag line and the old tag do not count)


def test_a_newer_version_has_its_notes_and_is_not_up_to_date(files, monkeypatch):
    fake_git(monkeypatch, tag="v999.0.0")
    s = updater.status()
    assert s["up_to_date"] is False and s["newer"] is True and s["latest"] == "999.0.0" and s["notes_url"] == "https://github.com/example/panel/releases/tag/v999.0.0"


def test_a_commit_ahead_of_this_one_is_not_up_to_date_but_it_is_no_new_version(files, monkeypatch):
    fake_git(monkeypatch, cur="abc1234", head="fff9999aaa")
    s = updater.status()
    assert s["up_to_date"] is False and s["newer"] is False and s["notes_url"] == ""


def test_while_an_update_runs_the_card_is_neither_up_to_date_nor_a_new_version_but_the_run(files, monkeypatch):
    fake_git(monkeypatch, cur="abc1234", head="abc1234fff", tag="v999.0.0")           # (the new files are pulled: this commit IS the newest one, the process still runs the old version)
    put(step="checks", to="999.0.0")
    s = updater.status()
    assert s["progress"]["state"] == "running" and s["progress"]["to"] == "999.0.0" and s["up_to_date"] is False


def test_after_a_failed_update_the_old_version_is_offered_again_not_called_up_to_date(files, monkeypatch):
    fake_git(monkeypatch, cur="abc1234", head="abc1234fff", tag="v999.0.0")           # (the disk is at the newest commit, the running version is older)
    put(step="failed", reason="the new version did not answer: the old version runs again")
    s = updater.status()
    assert s["progress"]["state"] == "failed" and s["up_to_date"] is False and s["newer"] is True


def test_when_github_does_not_answer_the_card_says_so_and_claims_nothing(files, monkeypatch):
    fake_git(monkeypatch, head="", tag="")
    s = updater.status()
    assert s["checked"] is False and s["up_to_date"] is False and s["newer"] is False


def test_github_is_asked_once_in_half_a_minute(files, monkeypatch):
    calls = fake_git(monkeypatch)
    updater.status()
    updater.status()
    asked = [c for c in calls if c[0] == "ls-remote"]
    assert len(asked) == 1 and "HEAD" in asked[0] and "refs/tags/v*" in asked[0]      # (one call asks for the commit AND the tags)
    updater._remote["at"] = 0.0
    updater.status()
    assert len([c for c in calls if c[0] == "ls-remote"]) == 2


def test_the_progress_route_reads_only_the_file(client, files, monkeypatch):
    def no_git(*_a, **_k):
        raise AssertionError("the page asks this every second: it must not touch git or GitHub")

    monkeypatch.setattr(updater, "_git", no_git)
    put(step="install")
    r = client.get("/api/system/update/progress").json()
    assert r["ok"] is True and r["version"] == __version__ and r["progress"]["state"] == "running" and r["progress"]["step"] == "install"


def test_the_status_route_has_what_the_card_reads(client, files, monkeypatch):
    fake_git(monkeypatch)
    r = client.get("/api/system/update").json()
    assert {"version", "installed", "newest", "checked", "up_to_date", "latest", "newer", "notes_url", "progress", "last_log"} <= set(r) and r["progress"]["state"] == "idle"


def test_a_second_run_is_refused_and_a_failed_download_is_told_at_once(files, monkeypatch):
    ran = []

    def fake_run(cmd, **_kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="From github.com:example/panel\nfatal: Not possible to fast-forward, aborting.\n")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    out = updater.start()
    assert out["ok"] is False and "Not possible to fast-forward" in out["error"] and not any(c[0] == "systemd-run" for c in ran)
    p = updater.read_progress()
    assert p["state"] == "failed" and "Not possible to fast-forward" in p["reason"]      # (the card shows it after a refresh too)
    put(step="install")
    assert updater.start() == {"ok": False, "error": "An update is already running", "code": "conflict"} and len(ran) == 1


def test_a_run_downloads_writes_its_first_step_and_starts_deploy_in_its_own_unit(files, monkeypatch, tmp_path):
    src = tmp_path / "src"
    (src / "nikatel").mkdir(parents=True)
    (src / "nikatel" / "__init__.py").write_text('"""Nikatel panel."""\n\n__version__ = "7.7.7"\n')
    monkeypatch.setattr(updater, "SRC", src)
    ran = []

    def fake_run(cmd, **_kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    assert updater.start() == {"ok": True, "started": True, "to": "7.7.7"}
    pull, unit = ran
    assert pull[:3] == ["git", "-C", str(src)] and "--ff-only" in pull and unit[0] == "systemd-run"
    assert any(a.startswith("--setenv=UPDATE_RUN=") for a in unit) and f"--setenv=UPDATE_FROM={__version__}" in unit and unit[-1].endswith("bash scripts/deploy.sh")
    p = updater.read_progress()
    assert p["state"] == "running" and p["step"] == "download" and p["to"] == "7.7.7" and p["from"] == __version__      # (the card has a run to show before the script wrote anything)


def test_an_update_that_cannot_start_its_unit_says_so(files, monkeypatch, tmp_path):
    (tmp_path / "nikatel").mkdir()
    (tmp_path / "nikatel" / "__init__.py").write_text('__version__ = "7.7.7"\n')
    monkeypatch.setattr(updater, "SRC", tmp_path)

    def fake_run(cmd, **_kw):
        if cmd[0] == "systemd-run":
            raise FileNotFoundError("systemd-run")

        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    out = updater.start()
    assert out["ok"] is False and "could not start the update" in out["error"] and updater.read_progress()["state"] == "failed"
