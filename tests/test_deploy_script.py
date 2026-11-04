"""scripts/deploy.sh in a sandbox: a copy of the tree in a temporary git repository, a fake venv, systemctl and curl.
The owner, 2026-10-17: the update took ten minutes because it ran the whole test suite on the panel's own server. What stays is what only that machine can answer - in seconds - and a version that
does not answer is rolled back, with the files AND the branch, so the next update is a plain fast-forward again."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash") or not shutil.which("git"), reason="needs bash and git")
VERSION_FILE = '"""Nikatel panel."""\n\n__version__ = "9.9.9"{note}\n'


class Box:
    """A sandbox: `src` is a git repository with the tree of the panel; the commands deploy.sh calls are fakes that write what they were asked into `calls`."""

    def __init__(self, tmp: Path):
        self.tmp, self.src, self.state, self.bin, self.venv = tmp, tmp / "src", tmp / "state", tmp / "bin", tmp / "venv"
        self.calls, self.health = tmp / "calls.log", tmp / "health"
        shutil.copytree(ROOT / "nikatel", self.src / "nikatel", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "scripts", self.src / "scripts")
        shutil.copy(ROOT / "pyproject.toml", self.src / "pyproject.toml")
        (self.src / ".gitignore").write_text("__pycache__/\n*.py[cod]\n")
        self.state.mkdir()
        self.bin.mkdir()
        (self.venv / "bin").mkdir(parents=True)
        self.health.write_text("ok\n")
        self.fake(self.venv / "bin" / "python", f'echo "python $*" >> "{self.calls}"\ncase "$1 $2" in "-m pytest") exit 0;; esac\nexec "{sys.executable}" "$@"')
        self.fake(self.venv / "bin" / "pip", f'echo "pip $*" >> "{self.calls}"')
        self.fake(self.bin / "systemctl", f'echo "systemctl $*" >> "{self.calls}"\nif [ "$1" = restart ]; then if grep -q BROKEN "{self.src}/nikatel/__init__.py"; then echo down > "{self.health}"; else echo ok > "{self.health}"; fi; fi')
        self.fake(self.bin / "curl", f'grep -q ok "{self.health}" && echo \'{{"ok":true}}\'')
        self.fake(self.bin / "journalctl", "true")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "one")
        self.good = self.head()
        (self.state / "last-good-commit").write_text(self.good + "\n")

    @staticmethod
    def fake(path: Path, body: str) -> None:
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def env(self, **extra: str) -> dict:
        who = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        return {**os.environ, **who, "VENV": str(self.venv), "STATE_DIR": str(self.state), "ENV_FILE": str(self.tmp / "no.env"), "UNIT": str(self.tmp / "nikatel.service"),
                "SYSTEMCTL": str(self.bin / "systemctl"), "HEALTH_TRIES": "3", "HEALTH_SLEEP": "0", "UPDATE_RUN": "123", "UPDATE_FROM": "1.0.0",
                "PATH": str(self.bin) + os.pathsep + os.environ["PATH"], **extra}

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.src, env=self.env(), capture_output=True, text=True, check=True).stdout.strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def commit(self, files: dict[str, str]) -> None:
        for name, text in files.items():
            (self.src / name).write_text(text)

        self.git("add", "-A")
        self.git("commit", "-q", "-m", "next")

    def deploy(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "scripts/deploy.sh", *args], cwd=self.src, env=self.env(**env), capture_output=True, text=True, timeout=240, check=False)

    def progress(self) -> dict:
        return json.loads((self.state / "update-progress.json").read_text())

    def called(self) -> str:
        return self.calls.read_text() if self.calls.exists() else ""


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def test_the_script_is_valid_bash_and_the_tests_are_opt_in():
    path = ROOT / "scripts" / "deploy.sh"
    assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0
    src = path.read_text()
    assert 'FORCE_TESTS:-0}" = 1' in src and src.count("-m pytest") == 1                # the only call of pytest is the one behind FORCE_TESTS


def test_a_normal_update_runs_no_tests_and_no_pip_and_ends_done(box):
    box.commit({"nikatel/__init__.py": VERSION_FILE.format(note="")})
    r = box.deploy()
    assert r.returncode == 0, r.stdout + r.stderr
    marks = ["== no secrets in the repo", "== tests skipped", "== compile", "== migrations on a copy", "== install ", "DEPLOY OK"]
    where = [r.stdout.index(m) for m in marks]
    assert where == sorted(where)                                                       # (the order an older page reads from the log: an update started by an older panel still draws its bar)
    assert "the whole panel imports" in r.stdout                                        # (even on a machine with no database yet)
    seen = box.called()
    assert "-m pytest" not in seen and "pip " not in seen and seen.count("systemctl restart nikatel") == 1
    p = box.progress()
    assert (p["step"], p["to"], p["from"], p["run"]) == ("done", "9.9.9", "1.0.0", 123)
    assert (box.state / "last-good-commit").read_text().strip() == box.head()


def test_changed_libraries_are_installed_and_the_tests_run_when_asked(box):
    box.commit({"pyproject.toml": (ROOT / "pyproject.toml").read_text() + "\n# a library changed\n"})
    r = box.deploy(FORCE_TESTS="1")
    assert r.returncode == 0, r.stdout + r.stderr
    seen = box.called()
    assert "pip install -q -e" in seen and "python -m pytest -q" in seen and "== tests\n" in r.stdout and "tests skipped" not in r.stdout


def test_a_file_that_does_not_compile_stops_before_the_restart_and_the_files_go_back(box):
    box.commit({"nikatel/services/broken_probe.py": "def x(:\n"})
    (box.src / "notes.txt").write_text("somebody's work\n")
    r = box.deploy()
    assert r.returncode != 0 and "rolled back" in r.stdout and "uncommitted work put aside" in r.stdout
    assert "systemctl restart" not in box.called()                                      # (the panel was not touched)
    assert not (box.src / "nikatel/services/broken_probe.py").exists() and box.head() == box.good
    assert not list((box.src / "nikatel").rglob("__pycache__"))                         # (what the compile of the failed version wrote is gone: a same-size file in the same second would pass for the old one)
    assert not (box.src / "notes.txt").exists() and "deploy failed" in box.git("stash", "list")      # (uncommitted work is put aside, not lost)
    p = box.progress()
    assert p["step"] == "failed" and "compile" in p["reason"]


def test_a_version_that_does_not_answer_is_rolled_back(box):
    box.commit({"nikatel/__init__.py": VERSION_FILE.format(note="  # BROKEN")})
    r = box.deploy()
    assert r.returncode == 1 and "HEALTH CHECK FAILED" in r.stdout and "rolled back: the panel runs" in r.stdout
    assert box.called().count("systemctl restart nikatel") == 2                         # the new one, and the old one again
    assert box.head() == box.good and "BROKEN" not in (box.src / "nikatel/__init__.py").read_text()
    p = box.progress()
    assert p["step"] == "failed" and "old version runs again" in p["reason"]
    assert (box.state / "last-good-commit").read_text().strip() == box.good


def test_the_restart_waits_for_a_command_on_a_node_and_a_stale_note_does_not_hold_it(box):
    """A PasarGuard update (the night run, the Safe update buttons) has a command running on a node: the panel wrote $STATE_DIR/pg-node-step.json (services/pg_safe.py). The restart waits until the note is gone
    (up to NODE_WAIT), so the command is not cut in the middle (2026-11-04: the owner's Update cut the first real night while a node was updating); a note older than 20 minutes is stale and holds nothing."""
    import threading
    import time

    box.commit({"nikatel/__init__.py": VERSION_FILE.format(note="")})
    note, seen = box.state / "pg-node-step.json", []
    note.write_text(json.dumps({"node": "ECO-test", "step": "software", "at": int(time.time())}))

    def node_done():      # (the node's command ends a moment after the script began to wait for it)
        for _ in range(240):
            time.sleep(0.5)

            try:
                p = box.progress()
            except (OSError, ValueError):
                continue

            if p.get("step") == "wait":
                seen.append(p)
                time.sleep(1.5)
                note.unlink()
                return

    threading.Thread(target=node_done, daemon=True).start()
    r = box.deploy(NODE_POLL="1", NODE_WAIT="30")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "a PasarGuard update has a command running on a node: ECO-test (software) - waiting for it before the restart (up to 30 s)" in r.stdout and "the node is done" in r.stdout and "restarting anyway" not in r.stdout
    assert seen and seen[0]["step"] == "wait" and "ECO-test (software)" in seen[0]["reason"] and box.progress()["step"] == "done"      # (the page saw the wait; the restart came after the node)
    assert r.stdout.index("waiting for it") < r.stdout.index("DEPLOY OK")
    note.write_text(json.dumps({"node": "old", "step": "xray", "at": int(time.time()) - 1300}))
    r = box.deploy(NODE_POLL="1", NODE_WAIT="5")
    assert r.returncode == 0 and "waiting for it" not in r.stdout and "DEPLOY OK" in r.stdout
    note.write_text(json.dumps({"node": "slow", "step": "software", "at": int(time.time())}))
    r = box.deploy(NODE_POLL="1", NODE_WAIT="2")
    assert r.returncode == 0 and "still working on slow (software) after 2 s: restarting anyway (the night run goes on after the restart)" in r.stdout and "DEPLOY OK" in r.stdout
    note.unlink()


def test_the_manual_rollback_moves_the_branch_back_and_leaves_no_detached_head(box):
    box.commit({"nikatel/__init__.py": VERSION_FILE.format(note="")})
    assert box.deploy().returncode == 0
    good = box.head()
    box.commit({"nikatel/__init__.py": VERSION_FILE.format(note="  # BROKEN")})
    r = box.deploy("--rollback")
    assert r.returncode == 0 and "rolled back to" in r.stdout, r.stdout + r.stderr
    assert box.head() == good and box.git("symbolic-ref", "HEAD") == "refs/heads/main"      # (a detached head would stop the next `git pull --ff-only`)
