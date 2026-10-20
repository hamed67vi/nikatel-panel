"""After an update the page loads again by itself, and the update state belongs to the panel that writes it (the owner, 2026-10-20: "after the update the page does not refresh by itself, it seems stuck").

What is tested: the page waits for the panel to answer as the NEW version before it asks for the page, tries again when the load hangs and ends with a visible link; the state files of the update follow the data
directory of the panel (a second panel or a test run never writes the live panel's progress), and deploy.sh is told where they are."""
from pathlib import Path

from nikatel.config import get_settings
from nikatel.services import updater

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def test_the_page_waits_for_the_new_version_and_does_not_trust_a_single_reload():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "paint(\"Updated 100%\", \"gw-ok\"); stop(); aboutReload(); return; }" in js and "setTimeout(() => location.reload(), 900)" not in js
    body = js.split("const aboutReload = async () => {", 1)[1].split("let aboutAuto = null", 1)[0]
    assert 'api("/api/system/update")' in body and "u.version !== aboutV0" in body and "ok < 2" in body                         # (the NEW version, twice in a row)
    assert "Date.now() - t0 < 90000" in body                                                                                   # (a rollback keeps the old version: after 90 s it loads anyway)
    assert body.count("location.replace(fresh())") == 2 and "setTimeout(() => location.replace(fresh()), 10000)" in body       # (a load that hangs is asked again with a fresh address)
    assert "The page did not reload by itself: click here" in body and "history.replaceState(null, \"\", location.pathname + location.hash)" in js      # (and the ?r= is taken off the address)


def test_the_state_files_of_the_update_follow_the_data_directory_not_a_fixed_path():
    assert updater.STATE_DIR == get_settings().data_dir.parent and str(updater.STATE_DIR) != "/var/lib/nikatel"           # (the tests run with a data directory of their own)
    assert updater.PROGRESS_FILE.parent == updater.STATE_DIR and updater.LOG_FILE.parent == updater.STATE_DIR


def test_the_update_unit_is_told_where_the_state_is(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updater, "PROGRESS_FILE", tmp_path / "update-progress.json")
    monkeypatch.setattr(updater, "LOG_FILE", tmp_path / "update.log")
    monkeypatch.setattr(updater, "STATE_DIR", tmp_path)
    monkeypatch.setattr(updater, "_tree_version", lambda: "9.9.9")

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(updater.subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)) or Done())
    r = updater.start()
    assert r["ok"] is True and any(c[0] == "systemd-run" and f"--setenv=STATE_DIR={tmp_path}" in c for c in calls)
