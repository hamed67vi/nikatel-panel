"""The note of a failed update in the About card is one row that says whether an update fixes it (the owner, 2026-10-20: "show the error briefly in one row, and if the update fixes it, say so").

What is tested: the page code (one row, the short reason, the full text in the tip, "updating to vX should fix it" only when a newer version than the failed one is out) and the CSS of the row."""
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def test_the_failed_update_is_one_row_with_the_short_reason_and_the_full_text_in_the_tip():
    js, css = (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "lab.css").read_text(encoding="utf-8")
    row = js.split('(failed ? `<div class="err abt-fail"', 1)[1].split(': "") + (', 1)[0]
    assert 'String(p.reason || "it stopped").split(":")[0]' in row and "Last update did not finish (${ago(p.at)} ago)" in row          # (the short reason: what stands before the first colon)
    assert "the version that was running stays" in row and "u.last_log" in row                                                       # (the rest is in the tip)
    assert "updating to v${esc(u.latest)} should fix it" in row and "fixes ?" in row
    assert "The version that was running stays.</div>" not in js                                                                    # (the paragraph of three lines is gone)
    assert "html body #about-box .abt-fail { white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_hint_appears_only_when_a_newer_version_than_the_failed_one_is_out():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    ver = js.split("const verGt = ", 1)[1].split(";\n", 1)[0]
    code = ("const verGt = " + ver + ";\n"
            "const fixes = (u, p) => u.newer && u.latest && (!p.to || verGt(u.latest, p.to));\n"
            "console.log(JSON.stringify([fixes({newer: true, latest: '1.69.50'}, {to: ''}), fixes({newer: true, latest: '1.69.50'}, {to: '1.69.50'}), fixes({newer: true, latest: '1.69.51'}, {to: '1.69.50'}),"
            " fixes({newer: false, latest: '1.69.50'}, {to: ''}), verGt('1.69.10', '1.69.9'), verGt('1.69.9', '1.69.10')]));")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    assert out == "[true,false,true,false,true,false]"          # (the download failed: any newer version; a failed version again: not itself, a newer one; nothing newer: no hint; 10 is after 9)
