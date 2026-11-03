"""The window of a tunnel: the Bandwidth row is one block, the Capacity row is not cut, and a tunnel that carries nothing is not called healthy (the owner, 2026-10-21, a picture of the window: "fix these errors in the box,
it is very messy" - the bandwidth box twice, "ually 1328 Mb/s", "Healthy - nothing needs attention" over a red box); since 2026-11-03 the boxes are ONE summary table.

What is tested: the live paint replaces only the block that is a child of the cell (the heading's "Mb/s" was a .mut too), the reason sentences stand under the Capacity row, the bar is empty when the test carried nothing,
the banner says "Up, but ..." for a drop the users feel, a very slow tunnel or a test of 0, and the CSS wraps the table. A node run of the paint with a small fake DOM shows the block once."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def _js():
    return (WEB / "app.js").read_text(encoding="utf-8")


def test_the_live_paint_replaces_only_the_block_of_the_cell_not_a_label():
    js = _js()
    assert 'w = bx.querySelector(":scope > .bwbig, :scope > .mut"); if (w) w.outerHTML = tunBwBoxInner(t);' in js and 'w = bx.querySelector(".bwbig, .mut")' not in js
    assert "const bx = document.querySelector('[id^=\"tun-bwbox-\"]');" in js and "+bx.id.slice(10)" in js          # ("tun-bwbox-" is ten characters)
    paint = js.split("const tunLivePaint = () => {", 1)[1].split("const tunLiveStop", 1)[0]
    assert 'tp.innerHTML = tunBanner(t);' in paint and 'cur.classList.contains("lab-ban")' in paint and "nb.outerHTML !== cur.outerHTML) cur.replaceWith(nb)" in paint          # (the banner follows the live numbers: a drop the users start to feel turns "Healthy" into "Up, but ...")


def test_the_capacity_row_does_not_repeat_the_sentence_of_the_bandwidth_row():
    js = _js()
    grid = js.split("function tunGrid(t) {", 1)[1].split("/* the \"times the tunnel was down\" box", 1)[0]
    cap = grid.split("const cap = (", 1)[1].split("const lat = ", 1)[0]
    big = js.split("const tunBwBoxInner = t => {", 1)[1].split("const capTip", 1)[0]
    assert "bwWhy(t)" not in cap and cap.count("bw-why") == 1 and 't.cap && t.cap.state === "full" ? `<div class="bw-why">${esc(capTip(t))}</div>`' in cap and "measure now</a>" in cap          # (the sentence about a drop was twice in the window, one row under the other; only what the Doctor does about a full tunnel is said here)
    assert 'class="bwbig-w"' in big and "bwWhy(t)" in big and "Nearly full" in big


def test_the_bar_is_empty_when_the_test_carried_nothing_and_the_banner_does_not_call_it_healthy():
    js = _js()
    assert "${u && !(cap != null && cap < BW_STALL) ? p(u.down).toFixed(1) : 0}%" in js
    banner = js.split("function tunBanner(t) {", 1)[1].split("const bad = t.state", 1)[0]
    assert 'nodata = bwb.down != null && bwb.down < BW_STALL' in banner and "<b>Up, but ${nodata ? " in banner and banner.index("Up, but") < banner.index("<b>Healthy</b>")
    assert 'bwb.state === "slow" ? "very slow" : "what it can carry is too low for its users"' in banner and "healthTunnel(${t.id})" in banner


def test_the_summary_table_wraps_a_long_value():
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "html body #modal .tunwin .tsum td { min-width: 0;" in css and "overflow-wrap: anywhere;" in css.split("html body #modal .tunwin .tsum td {", 1)[1].split("}", 1)[0]
    assert "html body #modal .tunwin table.tsum { width: 100%; border-collapse: collapse; table-layout: fixed; }" in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_node_run_of_the_paint_leaves_the_cell_with_one_block():
    """A small fake DOM: the cell has a label with a small.mut and one .bwbig; the paint of 1.69.65 replaced the heading's small, the new one the block."""
    js = _js()
    sel = js.split("w = bx.querySelector(", 1)[1].split(");", 1)[0]
    code = ("const box = {children: [{cls: 'h5', kids: [{cls: 'mut'}]}, {cls: 'bwbig'}]};"
            "const matchesOld = box.children.flatMap(c => [c, ...(c.kids || [])]).filter(c => c.cls === 'bwbig' || c.cls === 'mut');"
            "const matchesNew = box.children.filter(c => c.cls === 'bwbig' || c.cls === 'mut');"
            "console.log(JSON.stringify([matchesOld[0].cls, matchesNew[0].cls, " + json.dumps(sel) + "]));")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    first_old, first_new, text = json.loads(out)
    assert first_old == "mut" and first_new == "bwbig" and ":scope > .bwbig" in text          # (the old selector met a label's small first: the new one only the child blocks)
