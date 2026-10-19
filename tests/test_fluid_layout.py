"""The fluid layer on narrow screens (found by walking the test panel at 1024, 768 and 375 px, 1.69.29): a name was cut to one letter, an address to "198.51.1…", the last column (the ⋯ button) vanished
after the first refresh, two buttons of the tunnel bar were empty circles, a grid of cards was 48 px wider than a phone. The pieces that can be run are run with node (a small stand-in for the DOM);
the rest is checked in the source of the pages."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
LAB = (WEB / "lab.js").read_text(encoding="utf-8")
FLUID_JS = (WEB / "fluid.js").read_text(encoding="utf-8")
FLUID_CSS = (WEB / "fluid.css").read_text(encoding="utf-8")
FA = (WEB / "i18n.fa.ui2.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _function(src: str, head: str) -> str:
    """The text of one top-level function of a script: from its head to the brace that closes it."""
    start = src.index(head)
    depth, i = 0, src.index("{", start)

    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1

            if depth == 0:
                return src[start:i + 1]

        i += 1


# a stand-in for the few parts of the DOM that morphNode touches
DOM = r"""
class N { constructor(t) { this.nodeType = t; this.childNodes = []; this.parentNode = null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  appendChild(c) { c.parentNode = this; this.childNodes.push(c); return c; }
  removeChild(c) { this.childNodes.splice(this.childNodes.indexOf(c), 1); return c; }
  replaceChild(n, o) { this.childNodes[this.childNodes.indexOf(o)] = n; n.parentNode = this; return o; } }
class T extends N { constructor(v) { super(3); this.nodeName = "#text"; this.nodeValue = v; } cloneNode() { return new T(this.nodeValue); } }
class E extends N { constructor(tag, attrs = {}, kids = []) { super(1); this.nodeName = this.tagName = tag.toUpperCase(); this.attrs = new Map(Object.entries(attrs)); kids.forEach(k => this.appendChild(typeof k === "string" ? new T(k) : k)); }
  get attributes() { return [...this.attrs].map(([name, value]) => ({name, value})); }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n, String(v)); } removeAttribute(n) { this.attrs.delete(n); } hasAttribute(n) { return this.attrs.has(n); }
  get classList() { const c = (this.attrs.get("class") || "").split(/\s+/).filter(Boolean); return {contains: x => c.includes(x)}; }
  cloneNode() { const e = new E(this.tagName, Object.fromEntries(this.attrs)); this.childNodes.forEach(k => e.appendChild(k.cloneNode())); return e; } }
globalThis.document = {activeElement: null};
"""


def _run_morph(case: str) -> dict:
    code = DOM + _function(APP, "function morphNode(a, b) {") + "\n" + case
    out = subprocess.run([NODE, "-e", code], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_row_patched_in_place_keeps_the_mark_of_a_column_the_fluid_layer_dropped():
    """labPatch() rewrites the rows of the server list where they stand (morphNode). The class "fl-off" of a cell is the fluid layer's own mark, not data: it was stripped, the dropped
    cells showed again and pushed the ⋯ button out of the box (a tablet, a phone) - after the first quiet refresh, 8 seconds in."""
    r = _run_morph("""
      const old = new E("tr", {}, [new E("td", {class: "name"}, ["a"]), new E("td", {class: "lab-trf lab-used fl-off"}, ["1 GB"]), new E("td", {class: "fl-off"}, ["x"]), new E("td", {class: "lab-act"}, ["menu"])]);
      const fresh = new E("tr", {}, [new E("td", {class: "name"}, ["b"]), new E("td", {class: "lab-trf lab-used"}, ["2 GB"]), new E("td", {}, ["y"]), new E("td", {class: "lab-act"}, ["menu"])]);
      morphNode(old, fresh);
      console.log(JSON.stringify(old.childNodes.map(td => [td.getAttribute("class"), td.childNodes[0].nodeValue])));
      """)
    assert r == [["name", "b"], ["lab-trf lab-used fl-off", "2 GB"], ["fl-off", "y"], ["lab-act", "menu"]]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_stale_class_of_an_ordinary_cell_is_still_removed():
    r = _run_morph("""
      const old = new E("td", {class: "a b", title: "t"}, ["1"]);
      morphNode(old, new E("td", {class: "a"}, ["2"]));
      console.log(JSON.stringify([old.getAttribute("class"), old.getAttribute("title"), old.childNodes[0].nodeValue]));
      """)
    assert r == ["a", None, "2"]


def test_the_fluid_script_looks_again_when_the_cells_lost_their_marks():
    assert "function marked(tbl, off)" in FLUID_JS
    assert "var kept = same && marked(tbl, off);" in FLUID_JS and "if (tbl.__v === vsig && tbl.__w === wsig && kept) return;" in FLUID_JS and "if (tbl.__v !== vsig || !kept) {" in FLUID_JS


def test_a_header_can_say_its_least_width_or_that_it_fits_its_content():
    assert 'th.getAttribute("data-min")' in FLUID_JS and 'th.getAttribute("data-fit")' in FLUID_JS
    assert "natural(tbl, i, 40)" in FLUID_JS      # (the widest of the first 40 cells, measured with the font in use - Persian too - not the first three)
    # the server list: the name keeps its lamp, icon and letters; an address and the speed are not cut
    assert '<th data-prio="0" data-fit="192">Name</th><th data-prio="1" data-min="168">IP</th>' in LAB and 'data-min="126" title="Megabits per second' in LAB
    assert '<th data-prio="2" data-min="128" class="lab-ld">Load</th>' in LAB
    # the tunnel list: the name, the type badge, both nodes with their addresses, the bandwidth
    assert '<th data-fit="196">Tunnel</th><th data-min="176">Type</th>' in APP and '<th class="r" data-fit="190">Iran node</th>' in APP and '<th data-fit="190">Foreign node</th>' in APP and 'data-prio="1" data-min="130"' in APP


def test_no_button_is_only_words_that_go_on_a_narrow_screen():
    """Under 940 px the words of a button (.bt) are hidden: a button that holds nothing else is an empty round button (Check all, Restart all of the tunnels were)."""
    bad = []

    for f in sorted(WEB.glob("*.js")):
        for m in re.finditer(r'<button[^>]*>\s*(?:<i class="spin"></i>)?\s*<span class="bt">', f.read_text(encoding="utf-8")):
            bad.append(f"{f.name}: {m.group(0)[:80]}")

    assert not bad, bad
    assert 'class="tb-ic">${SI.pulse}</i><span class="bt">Check all</span>' in APP and 'class="tb-ic">${SI.reset}</i><span class="bt">Restart all</span>' in APP
    assert ".toolbar button > .tb-ic svg" in FLUID_CSS


def test_the_tunnel_bar_gives_way_with_its_search_and_keeps_its_buttons_whole():
    assert ".toolbar:has(> .tcount) > :is(button, .tcount, .tabs) { flex: none; }" in FLUID_CSS
    assert ".toolbar:has(> .tcount) > .tb-search { flex: 0 1 184px; min-width: 120px; }" in FLUID_CSS
    assert "@container tunbar (max-width: 960px)" in FLUID_CSS
    assert 'title="Tunnels that are up"' in APP and "Tunnels that are up" in FA     # (the words go on a narrow bar: the dot and the number keep their tip, and the tip has its Persian)
    phone = FLUID_CSS[FLUID_CSS.index("@media (max-width: 560px)"):]
    assert ".toolbar:has(> .tcount) > .tabs { flex: none; }" in phone      # (the All / Problems tabs were an 8 px sliver on a phone)


def test_a_grid_of_cards_never_asks_for_more_than_its_box_has():
    """repeat(auto-fit, minmax(340px, 1fr)) is 48 px wider than a phone: the least width is min(N px, 100%)."""
    bad = []

    for f in sorted(WEB.glob("*.css")):
        for m in re.finditer(r"repeat\(auto-(?:fit|fill), *minmax\((\d+)px, *1fr\)", f.read_text(encoding="utf-8")):
            if int(m.group(1)) >= 300:
                bad.append(f"{f.name}: {m.group(0)}")

    assert not bad, bad


def test_the_line_under_the_load_bar_is_cut_at_its_end_and_a_note_over_the_columns_wraps():
    lab = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "html body #page .lab-tbl .lab-ldx > small { min-width: 0; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in lab
    assert "table.tbl td[colspan] { white-space: normal; overflow-wrap: anywhere; }" in FLUID_CSS
    assert "@media (max-width: 480px) { html[data-fluid] body #page .ap-tasks .ap-row { flex-wrap: wrap; row-gap: 6px; } }" in FLUID_CSS


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_healthy_row_of_the_server_list_has_no_class_called_undefined():
    """labTrCls() looked the severity up in a table that has no entry for "ok" and wrote the word "undefined" into the class of every healthy row."""
    line = re.search(r"const labTrCls = [^\n]*?\.trim\(\);", LAB)
    assert line, "labTrCls is not trimmed any more"
    out = subprocess.run([NODE, "-e", line.group(0) + "\nconsole.log(JSON.stringify([labTrCls('ok', true, true), labTrCls('bad', true, false), labTrCls('off', false, null), labTrCls('warn', true, null), labTrCls('ok', true, null)]))"], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["up", "down sev-red", "srv-off", "sev-yellow", ""]


def test_the_persian_layer_takes_the_waiting_changes_before_it_disconnects():
    """MutationObserver.disconnect() drops the changes that are waiting for the observer. A page written in a microtask between the observer's call and the flush stayed English in Persian
    (Settings → Api & Key, every time) until the language was switched twice."""
    i18n = (WEB / "i18n.js").read_text(encoding="utf-8")
    flush = re.search(r"const flush = \(\) => \{[^\n]*\n", i18n)
    assert flush, "the flush of the Persian layer is gone"
    assert flush.group(0).index("take(obs.takeRecords())") < flush.group(0).index("obs.disconnect()")


def test_a_redrawn_page_gives_the_keyboard_focus_back():
    """The page is drawn again every few seconds while nothing is open; a focused button (the ⋯ of a row) was a removed element after that and the focus went to the page: the next Tab began at the top."""
    assert "const focusSnap = () => {" in APP and "const focusBack = s => {" in APP
    refresh = APP[APP.index("async function refreshNow() {"):APP.index("let lastTick = Date.now();")]
    assert "fsnap = focusSnap()" in refresh and "focusBack(fsnap);" in refresh
    assert refresh.index("await PAGES[state.page]();") < refresh.index("focusBack(fsnap);")      # (after the page is drawn, not before)
    assert "if (b && c.textContent !== String(labCount(b.dataset.k))) c.textContent = labCount(b.dataset.k);" in LAB      # (a count that did not change is not written: every quiet refresh replaced the text of every tab count)


def test_a_text_cut_with_a_dash_tells_itself_in_a_tip_and_a_task_row_keeps_its_button_inside():
    assert 'document.addEventListener("mouseover", function (e) {' in FLUID_JS
    tip = FLUID_JS[FLUID_JS.index('document.addEventListener("mouseover"'):FLUID_JS.index('addEventListener("resize", later);')]
    assert 'getComputedStyle(el).textOverflow !== "ellipsis"' in tip and "if (el.title && !el.__nkTip) continue;" in tip and "el.title = t;" in tip      # (a text that has a tip of its own keeps it)
    assert ".ap-tasks .ap-row > .mut:not(.work-chip):not(.ap-why) { flex: 0 1 auto; min-width: 60px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in FLUID_CSS
    assert '<th data-min="132">Expires</th>' in APP      # (the date pill of the users table was cut at 768 px)


def test_the_tunnels_of_a_server_in_its_window_wrap_instead_of_being_cut():
    lab = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "html body #modal .kv2 > div:has(> .tchip) { flex-wrap: wrap; overflow: visible; row-gap: 4px; }" in lab and "html body #modal .kv2 > div > .tchip { flex: none; }" in lab


def test_the_doctor_page_and_the_settings_boxes_have_their_persian_texts():
    for key in ["Doctor Mode", "Auto Fix Tunnels", "Auto Move to Iran Server", "Active Tasks", "Traffic used", "Bandwidth", "Only the password protects the panel", "Backup password", "AI models", "Pro slate",
                "About & Update", "Everything is up to date", "Tunnels up", "What changed in v{} on GitHub ↗", "key from {}"]:
        assert f'"{key}":' in FA or any(f'"{key}":' in (WEB / f).read_text(encoding="utf-8") for f in ("i18n.fa.ui.js", "i18n.fa.msg.js", "i18n.fa.doctor.js")), key


def test_the_panel_pages_keep_their_bar_whole_and_their_columns_uncut():
    lab = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "html body #page .pn-bar.pn-pick { height: auto; min-height: 56px; }" in lab and "html body #page .pn-bar { height: 56px; box-sizing: border-box; overflow: hidden; }" in lab      # (the bar of General stays fixed, the owner 2026-10-15)
    assert "html[data-fluid] body #page .pn-bar.pn-pick .pn-one > .pn-ksw { flex: 0 0 auto; max-width: 40%; }" in FLUID_CSS and "html[data-fluid] body #page .pn-bar.pn-pick .pn-ctl { flex: 1 1 0; }" in FLUID_CSS
    assert '<th data-fit="150">User</th><th data-min="96">Status</th><th data-min="150" title="Used so far' in APP
    admin = (WEB / "pg_admin.js").read_text(encoding="utf-8")
    assert 'data-prio="0" data-fit="170"' in admin and '<th data-min="132">Status</th>' in admin and 'data-min="76"' in admin and 'data-min="165"' in admin


def test_a_long_user_name_gets_its_dash_and_the_profile_suggestions_wrap_and_the_light_chips_are_dark():
    app_css = (WEB / "app.css").read_text(encoding="utf-8")
    assert ".usr-nm > b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in app_css and "grid-template-columns: minmax(0, 1fr); gap: 1px; min-width: 0; }" in app_css
    assert "html body .lab-np .lab-np-chips { flex-wrap: wrap; gap: 6px; min-width: 0; }" in (WEB / "lab.css").read_text(encoding="utf-8")
    assert 'html[data-mode="light"] body #page .ap-tasks .ap-row .work-chip { color: #3a4a6b;' in (WEB / "light.css").read_text(encoding="utf-8")
