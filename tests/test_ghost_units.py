"""The Doctor clears GHOST failed units by itself (1.69.275). The owner, 2026-11-15: "I want the Doctor to always do the needed cleanups itself". A unit whose file is gone but whose failed state systemd keeps
(`not-found failed`) made the builder refuse every tunnel test of its server for days; the orphan sweep never saw it because there was nothing to remove."""
import re
import subprocess

from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event
from nikatel.services import autopilot, orphans
from nikatel.services import settings as st
from nikatel.ssh import ACTIONS, render_action

LISTING = ("nikatel-gre-test-1.service not-found failed failed nikatel-gre-test-1.service\n"
           "chisel-old-sb.service not-found failed failed chisel-old-sb.service\n"
           "backhaul-iran-tcpmux-9935.service loaded failed failed Nikatel Backhaul\n"
           "backhaul-kharej-tcpmux-9935.service loaded active running Nikatel Backhaul\n"
           "nikatel-sink-bw7.service not-found inactive dead nikatel-sink-bw7.service\n")


def awk_of(line):
    return re.search(r"awk '([^']+)'", line).group(1)


def awk(prog, text):
    return subprocess.run(["awk", prog], input=text, capture_output=True, text=True, check=False).stdout


def test_the_look_prints_the_ghosts_and_only_the_ghosts():
    script = ACTIONS["orphan_look"][0]
    line = next(ln for ln in script.splitlines() if '"G"' in ln)
    assert "'nikatel-*'" in line
    assert awk(awk_of(line), LISTING).split() == ["G", "nikatel-gre-test-1.service", "G", "chisel-old-sb.service"]          # (not a real failed unit, not an inactive ghost: only not-found AND failed)


def test_the_action_resets_only_not_found_failed_units_and_changes_nothing_else():
    script = render_action("ghost_reset", {})
    assert 'systemctl reset-failed "$u"' in script and 'GHOSTS_RESET' in script
    assert "disable" not in script and "rm " not in script and " stop " not in script and "kill" not in script
    line = next(ln for ln in script.splitlines() if "list-units" in ln)
    assert awk(awk_of(line), LISTING).split() == ["nikatel-gre-test-1.service", "chisel-old-sb.service"]


def test_parse_ghosts_reads_each_unit_once_and_ignores_the_rest():
    out = "U backhaul-iran-tcpmux-9935.service active 3\nG nikatel-gre-test-1.service\nL tun1 0\nG chisel-old-sb.service\nG nikatel-gre-test-1.service\nG bad name.service\nG ../etc/passwd\nG x\nnoise\n"
    assert orphans.parse_ghosts(out) == ["nikatel-gre-test-1.service", "chisel-old-sb.service"]
    assert orphans.parse_ghosts("") == [] and orphans.parse_ghosts("U a.service active 0\n") == []


def _world(monkeypatch, out, mode="fix"):
    monkeypatch.setattr(get_settings(), "read_only", False)          # (the tests run read-only)
    monkeypatch.setattr(orphans.jobs, "stopping", lambda: False)      # (another test of the whole run may have begun a shutdown: the flag is global)
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    st.put("repair", {"mode": mode})
    st.put("orphans", {"seen": {}, "done": []})
    world = {"expected": {7: set()}, "ifaces": {7: set()}, "slugs": {7: set()}, "ids": set(), "names": set(), "rows": []}
    monkeypatch.setattr(orphans, "_world", lambda: world)
    monkeypatch.setattr(orphans, "_servers", lambda: [{"id": 7, "name": "ir7", "host": "203.0.113.7", "user": "root", "port": 22}])
    monkeypatch.setattr(orphans, "_TOLD", {})
    log = []
    monkeypatch.setattr(orphans, "_look", lambda s: out)
    monkeypatch.setattr(orphans, "_backup", lambda s: log.append(("backup",)) or "/var/lib/nikatel-orphans/before-1.tgz")
    monkeypatch.setattr(orphans, "_act", lambda s, a, p: log.append((a, p)) or "GHOSTS_RESET 2")
    return log


def events():
    with session() as db:
        return [e for e in db.scalars(select(Event)) if "ghost failed unit" in e.text]


def test_the_sweep_clears_the_ghosts_of_a_server_at_the_first_look_without_a_backup_and_says_it_once_a_day(monkeypatch):
    before = len(events())
    log = _world(monkeypatch, "G nikatel-gre-test-1.service\nG chisel-old-sb.service\nU backhaul-iran-tcpmux-9935.service active 4\n")
    orphans.run()
    assert log == [("ghost_reset", {})]                                                  # (no second look, no backup: nothing is deleted)
    said = events()[before:]
    assert len(said) == 1 and "ir7" in said[0].text and "2 ghost failed unit(s) cleared" in said[0].text and "nikatel-gre-test-1.service" in said[0].text and said[0].level == "info"
    orphans.run()                                                                         # (the next look, the same ghosts: cleared again, told no more today)
    assert log == [("ghost_reset", {}), ("ghost_reset", {})] and len(events()[before:]) == 1


def test_nothing_is_cleared_in_the_mode_off_in_a_storm_or_when_the_server_cannot_be_asked(monkeypatch):
    log = _world(monkeypatch, "G nikatel-gre-test-1.service\n", mode="off")
    orphans.run()
    assert log == []
    st.put("repair", {"mode": "fix"})
    monkeypatch.setattr(autopilot, "storm_now", lambda: True)
    orphans.run()
    assert log == []
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(orphans, "_act", lambda s, a, p: (_ for _ in ()).throw(RuntimeError("no answer")))
    before = len(events())
    orphans.run()                                                                         # (the action fails: a line in the log, no event, the sweep goes on)
    assert len(events()) == before


def test_a_server_without_ghosts_gets_no_action(monkeypatch):
    log = _world(monkeypatch, "U backhaul-iran-tcpmux-9935.service active 4\n")
    orphans.run()
    assert ("ghost_reset", {}) not in log
