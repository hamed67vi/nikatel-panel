"""The installer is paused (the owner, 2026-11-11: "for now nobody should be allowed to install the script"): a NEW install is refused before anything on the machine is touched; an upgrade of a machine that already has the panel still runs."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")


def _gate():
    m = re.search(r"^# >>> install-pause\n(.*?)^# <<< install-pause\n", INSTALL, re.DOTALL | re.MULTILINE)
    assert m, "the pause block is gone from install.sh"
    return m.group(1)


def _run(tmp_path, with_git, switch=None):
    src = tmp_path / "src"

    if with_git:
        (src / ".git").mkdir(parents=True)

    env = {"PATH": "/usr/bin:/bin"}

    if switch is not None:
        env["NIKATEL_ALLOW_INSTALL"] = switch

    script = f'set -euo pipefail\nSRC="{src}"\ndie() {{ echo "!! $*" >&2; exit 1; }}\n{_gate()}echo PASSED\n'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=20, check=False)


def test_a_new_install_is_refused(tmp_path):
    r = _run(tmp_path, with_git=False)
    assert r.returncode == 1 and "paused" in r.stderr and "PASSED" not in r.stdout
    assert "nothing was changed" in r.stderr


def test_an_upgrade_of_a_machine_that_has_the_panel_still_runs(tmp_path):
    r = _run(tmp_path, with_git=True)
    assert r.returncode == 0 and "PASSED" in r.stdout


def test_the_maintainer_switch_lifts_it_only_when_it_is_exactly_one(tmp_path):
    assert "PASSED" in _run(tmp_path, with_git=False, switch="1").stdout

    for other in ("0", "true", "yes", ""):
        r = _run(tmp_path, with_git=False, switch=other)
        assert r.returncode == 1 and "PASSED" not in r.stdout, other


def test_the_pause_sits_before_anything_is_touched():
    at = INSTALL.index("# >>> install-pause")

    for later in ('id -u', "apt-get", "git clone", "mkdir", "systemctl", '/bin/pip" install', "openssl", "ssh-keygen", "certbot", "cp "):
        assert INSTALL.index(later, at) > INSTALL.index("# <<< install-pause"), later

    assert INSTALL.index("die()") < at and INSTALL.index("SRC=/opt/nikatel/src") < at


def test_the_whole_installer_is_still_valid_bash():
    r = subprocess.run(["bash", "-n", str(ROOT / "install.sh")], capture_output=True, text=True, timeout=20, check=False)
    assert r.returncode == 0, r.stderr


def test_the_ci_installs_carry_the_switch():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lines = [ln for ln in ci.splitlines() if "bash install.sh" in ln]
    assert len(lines) == 2 and all("NIKATEL_ALLOW_INSTALL=1" in ln for ln in lines)


def test_the_readme_says_installs_are_paused():
    assert "Installs are paused for now" in (ROOT / "README.md").read_text(encoding="utf-8")
