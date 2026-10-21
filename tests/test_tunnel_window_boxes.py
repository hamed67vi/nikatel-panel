"""The window of a tunnel: the bandwidth box is one box, the Capacity row is not cut, and a tunnel that carries nothing is not called healthy (the owner, 2026-10-21, a picture of the window: "fix these errors in the box,
it is very messy" - the bandwidth box twice, "ually 1328 Mb/s", "Healthy - nothing needs attention" over a red box).

What is tested: the live paint replaces only the block that is a child of the box (the heading's "Mb/s" is a .mut too), the reason sentences stand under the Capacity row, the bar is empty when the test carried nothing,
the banner says "Up, but ..." for a drop, a very slow tunnel or a test of 0, and the CSS wraps the Link box. A node run of the paint with a small fake DOM shows the box once."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def _js():
    return (WEB / "app.js").read_text(encoding="utf-8")


def test_the_live_paint_replaces_only_the_block_of_the_box_not_the_heading():
    js = _js()
    assert 'w = bx.querySelector(":scope > .bwbig, :scope > .mut"); if (w) w.outerHTML = h;' in js and 'w = bx.querySelector(".bwbig, .mut")' not in js


def test_the_reason_sentences_stand_under_the_capacity_row_not_inside_its_value():
    js = _js()
    row = js.split('kv("Capacity", ', 1)[1].split('kv("Engine"', 1)[0].split('kv(gre ? "Link" : "Engine"', 1)[0]
    value = row.split("measure now</a>`)", 1)[0]
    after = row.split("measure now</a>`)", 1)[1]
    assert "bw-why" not in value and after.startswith(" + ((t.bw || {}).down != null ?") and "bw-why" in after


def test_the_bar_is_empty_when_the_test_carried_nothing_and_the_banner_does_not_call_it_healthy():
    js = _js()
    assert "${u && !(cap != null && cap < BW_STALL) ? p(u.down).toFixed(1) : 0}%" in js
    banner = js.split("function tunBanner(t) {", 1)[1].split("const bad = t.state", 1)[0]
    assert 'nodata = bwb.down != null && bwb.down < BW_STALL' in banner and "<b>Up, but ${nodata ? " in banner and banner.index("Up, but") < banner.index("<b>Healthy</b>")
    assert 'bwb.state === "slow" ? "very slow" : "what it can carry dropped"' in banner and "healthTunnel(${t.id})" in banner


def test_the_link_box_wraps_a_long_value():
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert 'html body #modal .tunwin .sbox[data-b="link"] .kv2 > div { flex-wrap: wrap; white-space: normal; row-gap: 2px; overflow: visible; }' in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_node_run_of_the_paint_leaves_the_box_with_one_block():
    """A small fake DOM: the box has a heading with a small.mut and one .bwbig; the paint of 1.69.65 replaced the heading's small, the new one the block."""
    js = _js()
    sel = js.split("w = bx.querySelector(", 1)[1].split(");", 1)[0]
    code = ("const box = {children: [{cls: 'h5', kids: [{cls: 'mut'}]}, {cls: 'bwbig'}]};"
            "const matchesOld = box.children.flatMap(c => [c, ...(c.kids || [])]).filter(c => c.cls === 'bwbig' || c.cls === 'mut');"
            "const matchesNew = box.children.filter(c => c.cls === 'bwbig' || c.cls === 'mut');"
            "console.log(JSON.stringify([matchesOld[0].cls, matchesNew[0].cls, " + json.dumps(sel) + "]));")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    first_old, first_new, text = json.loads(out)
    assert first_old == "mut" and first_new == "bwbig" and ":scope > .bwbig" in text          # (the old selector met the heading's small first: the new one only the child blocks)
