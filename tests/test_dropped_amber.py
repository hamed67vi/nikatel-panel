"""A dropped capacity says nothing unless the users feel it (the owner, 2026-10-21: "write dropped, but red only when the users' bandwidth is reduced"; 2026-11-03, his picture of the list - a grey "dropped" beside 1192 | ↓ 41:
"remove this dropped - show a warning only if the bandwidth the customer gets is low for its users"): the users use at least half of the dropped capacity (or nothing moves at all) - then the row says "low for users" in red
and lifts; otherwise the meter has no word and the tunnel stays where it is in the list. A very slow capacity keeps its quiet grey-blue word. The rule runs through node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
APP, CSS = (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "lab.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_dropped_capacity_is_red_only_when_the_users_use_a_big_part_of_it():
    start, end = APP.index("const BW_HURT = 0.5;"), APP.index("const bwMeterFor = t =>")
    prog = ("const BW_STALL = 0.5; const useOf = t => t._u;\n" + APP[start:end] + """
      const row = (down, use, state = 'drop') => ({bw: {down, state}, _u: use == null ? null : {down: use}});
      console.log(JSON.stringify([bwHurts(row(575, 9.8)), bwHurts(row(75, 4.6)), bwHurts(row(100, 50)), bwHurts(row(100, 80)), bwHurts(row(0.2, 1)), bwHurts(row(0.2, null)), bwHurts(row(100, null)), bwHurts(row(40, 19.9))]));
    """)
    out = subprocess.run([NODE, "-e", prog], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [False, False, True, True, True, True, False, False]          # (the list of the owner's screenshot: 9.8 of 575, 4.6 of 75 - not red; half of the capacity or more - red; no data - red; no reading of the users - not red)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_meter_of_a_drop_has_no_word_unless_the_users_are_held_back():
    start, end = APP.index("const BW_HURT = 0.5;"), APP.index("const bwTone = t =>")
    prog = ("const BW_STALL = 0.5; const useOf = t => t._u; const bwMeter = (t, tone, word) => [tone, word]; const bwTip = () => ''; const capTip = () => '';\n" + APP[start:end] + """
      const row = (down, use, state = 'drop', extra = {}) => ({bw: {down, state}, _u: use == null ? null : {down: use}, record: {}, ...extra});
      console.log(JSON.stringify([
        bwMeterFor(row(1192, 41)),                 // the owner's picture: 1192 | down 41 - a drop the users do not feel
        bwMeterFor(row(100, 80)),                  // the users use most of what is left: a warning
        bwMeterFor(row(0.2, 1)),                   // nothing moves
        bwMeterFor(row(100, null)),                // no reading of the users: nothing to say
        bwMeterFor(row(15, 2, 'slow')),            // very slow keeps its word, quiet while the users do not feel it
        bwMeterFor(row(15, 12, 'slow')),
        bwMeterFor(row(500, 100, 'ok', {cap: {state: 'full'}})),
        bwFelt(row(100, 80)), bwFelt(row(100, 80, 'slow')), bwFelt(row(1192, 41))]));
    """)
    out = subprocess.run([NODE, "-e", prog], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [["ok", ""], ["drop", "low for users"], ["drop", "no data"], ["ok", ""], ["dip", "very slow"], ["slow", "very slow"], ["full", "nearly full"], True, False, False]


def test_the_word_dropped_is_gone_and_only_a_drop_the_users_feel_is_red_lifts_the_row_and_opens_the_banner():
    fn = APP.split("const bwMeterFor = t => {", 1)[1].split("const bwTone", 1)[0]
    assert 'prob = stall || bwFelt(t) || b.state === "slow"' in fn and 'hurt = prob && bwHurts(t)' in fn and 'prob ? (!hurt ? "dip" : stall || b.state === "drop" ? "drop" : "slow") : full ? "full" : "ok"' in fn
    assert 'prob ? (stall ? "no data" : b.state === "drop" ? "low for users" : "very slow")' in fn and '"dropped"' not in fn
    assert "const bwFelt = t => (t.bw || {}).state === \"drop\" && bwHurts(t);" in APP
    assert 'bwFelt(t) || b.state === "slow" || (cap != null && cap < BW_STALL) ? (bwHurts(t) ? "drop" : "dip")' in APP and 'tone === "dip" ? bwWhy(t) +' in APP and "so they do not feel the drop" in APP
    banner = APP.split("function tunBanner(t) {", 1)[1].split("const bad = t.state", 1)[0]
    assert 'if (up && (nodata || bwFelt(t) || bwb.state === "slow"))' in banner and "what it can carry is too low for its users" in banner and "what it can carry dropped" not in APP
    assert "if (bwFelt(t)) parts.push(" in APP and '"DROPPED"' not in APP          # (the hover of the meter says it only when the users feel it)
    assert ".bwm.dip .bwm-t small { color: var(--mut);" in CSS and ".bwm.dip .bwm-bar u" not in CSS and ".bwbig.dip" not in CSS          # one quiet colour, no amber for a dip (the owner, 2026-10-22)
    sev = APP.split("const tunSev = t =>", 1)[1].split("\n", 1)[0]
    assert '(["drop", "slow"].includes((t.bw || {}).state) && bwHurts(t)) ? "sev-yellow"' in sev          # a drop the users do not feel does not lift the row (the order, the Problems filter and its counter follow tunSev)
