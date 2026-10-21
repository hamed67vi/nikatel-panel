"""The word "dropped" stays on a tunnel whose capacity fell, but the colour is red only when the users feel it (the owner, 2026-10-21: "write dropped, but red only when the users' bandwidth is reduced"): the users use at least half of
the dropped capacity (or nothing moves at all); otherwise it is amber ("dip"). The rule runs through node."""
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


def test_the_word_stays_and_the_colour_is_amber_in_the_list_and_the_window():
    fn = APP.split("const bwMeterFor = t => {", 1)[1].split("const bwTone", 1)[0]
    assert 'hurt = prob && bwHurts(t)' in fn and 'prob ? (!hurt ? "dip" : stall || b.state === "drop" ? "drop" : "slow") : full ? "full" : "ok"' in fn
    assert 'prob ? (stall ? "no data" : b.state === "drop" ? "dropped" : "very slow")' in fn          # the word is the same, red or not
    assert '? (bwHurts(t) ? "drop" : "dip")' in APP and 'tone === "dip" ? bwWhy(t) +' in APP and "so they do not feel the drop" in APP
    assert ".bwm.dip .bwm-bar u" in CSS and ".bwm.dip .bwm-t small" in CSS and ".bwbig.dip .bwbig-track u" in CSS