"""The tunnel list in groups by type (the owner, 2026-10-22: "group them by the type of the tunnel"). The grouping and its order run through node on the real functions of app.js; the head row, the fold, the search and the
stylesheet are checked in the source."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _groups(tunnels):
    start, end = APP.index("const tunKeyRow = t =>"), APP.index("\n", APP.index("const tunCmp = (x, y) =>"))
    g0, g1 = APP.index("const tunKind = t =>"), APP.index("const tunOff = () =>")
    prog = ("const state = {serversCache: [], tunRowCache: new Map()}; const tunSev = t => t.sev || '';\n" + APP[start:end] + "\n" + APP[g0:g1]
            + "\nconst rows = " + json.dumps(tunnels) + "; console.log(JSON.stringify(tunGroups(rows).map(g => [g.k, g.a.map(t => t.name), g.bad])));")
    out = subprocess.run([NODE, "-e", prog], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _t(name, type_, transport, sev="", id_=1):
    return {"id": id_, "name": name, "foreign_name": "X", "type": type_, "transport": transport, "sev": sev}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_tunnels_are_grouped_by_the_type_the_type_column_says_the_biggest_group_first():
    rows = [_t("b1", "backhaul", "tcpmux"), _t("p1", "backpack", "wssmux"), _t("b2", "backhaul", "tcpmux"), _t("p2", "backpack", "wssmux"), _t("p3", "backpack", "wssmux"), _t("k1", "backpack", "tcpmux"),
            _t("l3", "backhaul", "l3:tcp")]
    got = _groups(rows)
    assert [g[0] for g in got] == ["BACKPACK · wssmux", "BACKHAUL · tcpmux", "BACKHAUL · tcp", "BACKPACK · tcpmux"]          # (3, 2, then the two of one - by name; "l3:" is not shown)
    assert got[0][1] == ["p1", "p2", "p3"] and got[1][1] == ["b1", "b2"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_group_with_a_cut_comes_first_then_one_with_another_problem_and_inside_a_group_the_problem_is_first():
    rows = [_t("a1", "backhaul", "tcpmux"), _t("a2", "backhaul", "tcpmux"), _t("a3", "backhaul", "tcpmux"), _t("y1", "backpack", "wssmux", "sev-yellow"), _t("y2", "backpack", "wssmux"),
            _t("r1", "paqet", "", "sev-red"), _t("c1", "chisel", "fwd")]
    got = _groups(rows)
    assert [g[0] for g in got] == ["PAQET", "BACKPACK · wssmux", "BACKHAUL · tcpmux", "CHISEL · fwd"]
    assert [g[2] for g in got] == [1, 1, 0, 0] and got[1][1] == ["y1", "y2"]


def test_a_head_row_is_in_the_same_table_with_one_cell_over_every_column_and_a_click_folds_it():
    cols = APP.split('<table class="tbl lift tun-tbl"><colgroup>', 1)[1].split("</colgroup>", 1)[0]
    assert len(re.findall(r"<col ", cols)) == 8
    assert '<td colspan="8"><div class="gh">' in APP and 'onclick="tunGroupToggle(this.dataset.gk)"' in APP
    assert "tunGroups(rows).map(g => tunGroupHead(g, off.has(g.k))" in APP and '`<tr data-g="${esc(g.k)}" class="${off.has(g.k) ? "g-off " : ""}`' in APP
    assert 'localStorage.setItem("nk.tgoff"' in APP and "tr.tun-ghead" in APP


def test_the_search_hides_an_empty_group_and_opens_the_folded_ones_and_the_stylesheet_folds():
    q = APP.split("function applyQ() {", 1)[1].split("\n}\n", 1)[0]
    assert 'tr.tun-ghead").forEach(h => {' in q and 'h.classList.toggle("q-hide", !any)' in q and 'tb.classList.toggle("q-on", !!q)' in q
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "html body #page .tun-tbl tr.g-off { display: none; }" in css and "html body #page .tun-tbl.q-on tr.g-off:not(.q-hide) { display: table-row; }" in css
