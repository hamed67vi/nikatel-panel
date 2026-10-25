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
    assert '<th data-prio="0" data-fit="192">Name</th><th data-prio="1" data-min="168">IP</th>' in LAB and 'data-min="126" title="The line of the server in megabits per second' in LAB
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


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_rows_of_the_server_list_keep_their_places_while_the_list_is_scrolled():
    """The owner, 2026-10-19: "the page jumps while a server is being installed". The end of an install gave the server a problem, the sort moved its row to the TOP and every row under it
    went down while the list stayed where it was scrolled. Scrolled away from the head the order is kept (a new row goes last); at the head the sort is fresh (the problems come first)."""
    code = ("const labCmp = (a, b) => a.k - b.k;\n" + "const labOrder = {ids: []};\n" + _function(LAB, "function labSorted(shown, scrolled) {") + "\n" + """
      const P = (id, k) => ({id, k}), ids = rows => rows.map(p => p.id);
      const a = [P(1, 1), P(2, 2), P(3, 3), P(4, 4)], out = [];
      out.push(ids(labSorted(a, false)));                                            // the first draw: a fresh sort
      a[3].k = 0;                                                                    // server 4 has a problem now: the sort wants it first
      out.push(ids(labSorted(a, true)));                                             // scrolled: nothing moves
      out.push(ids(labSorted(a.concat([P(5, 0)]), true)));                           // a new row while scrolled: at the end
      out.push(ids(labSorted(a, true)));                                             // the row that went away is gone, the others keep their places
      out.push(ids(labSorted(a, false)));                                            // back at the head: a fresh sort, the problem first
      console.log(JSON.stringify(out));
      """)
    out = subprocess.run([NODE, "-e", code], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [[1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4, 5], [1, 2, 3, 4], [4, 1, 2, 3]]
    assert "const box = $(\".lbox\"), rows = labSorted(shown, !!box && box.scrollTop > 40);" in LAB
    assert "const snap = scrollSnapshot(); el.innerHTML = labBox(labShown()); applyQ(); scrollRestore(snap); pendScroll = snap;" in LAB      # (a list drawn from scratch keeps its scroll too)


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

def test_the_question_box_is_the_alert_of_macos_and_leaves_the_plan_box_and_the_choice_lists_alone():
    """The owner, 2026-10-20: "make this one MacBook style" (the Update from GitHub question): a rounded-square icon, the words centred, two buttons of one width side by side."""
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    box = "html body #ask .modal.ask-box:not(:has(.mv-plan))"
    assert box + " { width: min(344px" in css and "border-radius: 16px; text-align: center" in css
    acts = box + " .ask-act:not(.ask-choices)"
    assert acts + " { display: flex; width: 100%;" in css and acts + " button { flex: 1 1 0;" in css
    assert acts + " .ask-yes { background: #0a84ff;" in css and acts + " .ask-yes.danger-fill { background: #ff453a; }" in css      # (blue, red for a dangerous question)
    assert 'html[data-mode="light"] body #ask .modal.ask-box:not(:has(.mv-plan))' in css                                                         # (the plan of "Move the tunnels" keeps its wide left-aligned box)


def test_the_update_question_has_the_update_emoji_and_its_buttons_are_flat_in_every_theme():
    """The owner, 2026-10-20: "put the update emoji in place of the question mark" (the Update from GitHub box); his theme gave the blue button a gradient, a glow and a gloss on top of the alert."""
    assert '/^(Update|Upgrade)$/i.test(verb) ? "🔄" : "?"' in APP
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    acts = "html body #ask .modal.ask-box:not(:has(.mv-plan)) .ask-act:not(.ask-choices)"
    assert acts + " .ask-yes, " + acts + " .ask-no { background-image: none !important; box-shadow: none !important; text-shadow: none !important; border: 0 !important;" in css
    assert acts + " .ask-yes::before, " in css and acts + " .ask-yes { background-color: #0a84ff !important;" in css

def test_the_page_with_a_list_box_cannot_be_scrolled_and_its_box_ends_where_the_page_area_ends():
    """The owner, 2026-10-21 (a picture of the Servers page): after switching, the page was cut at the top - the title and the status line were gone and the toolbar was at the top of the window. The box of the list ended
    at the bottom of the WINDOW, 24 px under the end of the page area (which keeps 30 px of padding), so the page area (a box with its scroll locked) was a little taller than itself; when the bar above it grew (a status
    line, a wrapped title) it was a lot taller, and a focus on a row at the bottom (the page redraws and puts the keyboard focus back) scrolled the locked area up: the header went off the top. Measured in the browser
    with the status line made 116 px taller: the focus moved the area by 117 px before, by 0 now."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    assert ".main:has(.lbox) { overflow-y: hidden; overflow: clip; min-height: 0; min-width: 0; }" in css     # (clip on both axes: with one axis hidden the other clip turns into hidden and the area is still scrollable by a focus; min-height 0: no scroll container any more must not grow to its content)
    assert 'const mn = pg.closest(".main"), room = mn ? Math.min(window.innerHeight - 24, mn.getBoundingClientRect().bottom - (parseFloat(getComputedStyle(mn).paddingBottom) || 0)) : window.innerHeight - 24;' in APP
    assert "const mh = Math.max(150, Math.floor(room - b.getBoundingClientRect().top));" in APP
    assert 'new ResizeObserver(() => requestAnimationFrame(fit))' in APP and '[".main > .head", "#statusbar", "#subnav"]' in APP      # (the bar above the page changes height: the box follows at once)

def test_the_about_box_is_compact_and_macos_like_and_the_license_box_is_hidden_behind_a_flag():
    """The owner, 2026-10-21: "make these two boxes tighter and MacBook style", then "disable the license section for now and do not show it". The top boxes of Settings -> About & update (`.abt-top`) are compact
    and look like the System Settings of macOS; with `LICENSE_UI = false` there is no License box and no call of /api/license, and the About box is ONE strip over the full width."""
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "html body #page .abt-grid { grid-template-rows: auto 316px; align-content: start; }" in css
    assert "html body #page .abt-grid > .abt-card:not(.abt-top) { align-self: stretch; }" in css      # (the cores and the PasarGuard box keep their height)
    first = "html body #page .abt-grid > .abt-top {"
    assert first in css and "border-radius: 12px" in css.split(first, 1)[1].split("}", 1)[0]
    assert "html body #page .abt-top .abt-ti { width: 30px; height: 30px; border-radius: 8px;" in css
    assert "html body #page .abt-top .abt-t1 b { font-size: 13.5px; font-weight: 600;" in css
    assert "html body #page .abt-top .abt-row { gap: 9px; height: 34px;" in css and "html body #page .abt-top .abt-pill { height: 22px;" in css
    assert 'html[data-mode="light"] body #page .abt-grid > .abt-top' in css      # (and a light colour set)
    assert "@media (max-width: 1000px) { html body #page .abt-grid { grid-template-rows: none; grid-auto-rows: auto; }" in css      # (a phone: one column, each box as tall as its content)
    assert "html body #page .abt-grid.abt-nolic > #about-hero { grid-column: 1 / -1; flex-direction: row;" in css and ":nth-child(-n+2)" not in css.split("/* 1.69.78", 1)[1]
    # the flag
    assert "const LICENSE_UI = false;" in APP
    assert '(!LICENSE_UI ? "" : `<div class="abt-card abt-top"><div class="abt-ch"><i class="abt-ti">${SI.shield(false)}' in APP
    assert "aboutLoad(); enginesLoad(); if (LICENSE_UI) licLoad();" in APP      # (no call of /api/license while the box is hidden)
    assert '<div class="abt-grid${LICENSE_UI ? "" : " abt-nolic"}">' in APP and '<div class="abt-card abt-top" id="about-hero">' in APP
    assert 'LICENSE_UI ? "This panel, its license, the tunnel cores on your servers' in APP and "This panel, the tunnel cores on your servers, the PasarGuard panels and their nodes, and what the service runs with." in (WEB / "i18n.fa.ui2.js").read_text(encoding="utf-8")


def test_the_question_with_several_answers_is_a_stack_of_flat_macos_buttons_in_every_theme():
    """The owner, 2026-10-24, with a picture of "SW already has a tunnel ... Remove it and replace it / Add as a second tunnel / Cancel": "make this MacBook style". The answers of a glassChoice (the recommended one `pri`,
    a destructive one `danger` / `danger-fill`) had the gradient, the glow and the gloss of the theme; they are flat now: grey, the recommended one blue, a destructive one red text on a soft red, the note under the
    label smaller and quieter. Measured in the browser in the calm, aurora and classic themes, dark and light: no gradient, no shadow, no text shadow, no gloss; the box 339 px wide, nothing out of it."""
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    base = "html body #ask .ask-box .ask-act.ask-choices button {"
    assert base in css
    rule = css.split(base, 1)[1].split("}", 1)[0]
    for part in ("width: 100%", "min-height: 40px", "border-radius: 10px", "font-size: 13.5px", "background-image: none !important", "box-shadow: none !important", "text-shadow: none !important", "border: 0 !important"):
        assert part in rule
    assert "html body #ask .ask-box .ask-act.ask-choices button.pri { background-color: #0a84ff !important; color: #fff !important; }" in css
    assert "html body #ask .ask-box .ask-act.ask-choices :is(button.danger, button.danger-fill) { background-color: rgba(255, 69, 58, .16) !important; color: #ff6961 !important; }" in css
    assert "html body #ask .ask-box .ask-act.ask-choices button::before, html body #ask .ask-box .ask-act.ask-choices button::after { display: none !important; }" in css
    assert 'html[data-mode="light"] body #ask .ask-box .ask-act.ask-choices button { background-color: rgba(60, 70, 100, .09) !important; }' in css
    assert 'cls: "danger-fill", note: "the old one is removed first, then the new one is built"' in APP and 'cls: "pri", note: "the old one stays; both run"' in APP      # (the two answers of the picture)

def test_the_warning_sign_of_the_tunnel_list_is_only_for_two_tunnels_between_the_same_two_servers_and_has_no_number():
    """The owner, 2026-10-25, with a picture of "PDE01 ⚠ ×4": "remove these numbers x4 altogether", "show no warning at all - unless the tunnel has the same Iran server AND the same foreign server": the sign (a
    pale 9 px "⚠", since 1.69.132) is drawn only for a tunnel that has another tunnel between the same pair; a foreign server that ends several tunnels from different Iran servers has none, and the sign carries no count."""
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    rule = css.split("html body #page .dup {", 1)[1].split("}", 1)[0]
    for part in ("font-size: 9px", "opacity: .6"):
        assert part in rule
    assert "fcount" not in APP and "×${" not in APP.split("const dupMark = t =>", 1)[1].split("\n", 1)[0]      # (no count of the foreign server, no number in the sign)
    line = APP.split("const pairKey = t =>", 1)[1].split("\n", 1)[0]
    assert "t.iran_name" in line and "t.foreign_name" in line and "pcount[pairKey(t)] = (pcount[pairKey(t)] || 0) + 1" in line
    mark = APP.split("const dupMark = t =>", 1)[1].split("\n", 1)[0]
    assert "pcount[pairKey(t)] > 1" in mark and ">⚠</span>" in mark and "Another tunnel joins the same two servers" in mark
