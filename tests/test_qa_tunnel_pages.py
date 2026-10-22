"""The tunnel pages walked in a real browser (QA 2026-10-22): what was found there and fixed stays fixed. The files are read here (the browser is not part of the suite)."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
LAB = (WEB / "lab.js").read_text(encoding="utf-8")
CSS = (WEB / "lab.css").read_text(encoding="utf-8")


def test_the_menu_of_a_row_opens_the_tunnel_window_before_the_items_that_draw_into_it():
    """Fix, Dry run, Speed test, History and Rebuild draw INTO the window of the tunnel (its #fix-<id> sheet, its body): from the "⋯" of a row of the list that window was not open and they did nothing at all."""
    menu = LAB.split("function tunMenuItems(id) {", 1)[1].split("\nfunction labNodeParts", 1)[0]
    assert "const inWin = fn => () => { if (typeof tunWinOpen === \"function\" && !tunWinOpen(t.id)) return; return fn(); };" in menu
    for call in ("fixTunnel(t.id, false)", "fixTunnel(t.id, true)", "tunSpeed(t.id, t.name)", "tunHistory(t.id, t.name)", "rebuildTunnel(t.id, t.name)"):
        assert f"inWin(() => {call})" in menu, call

    for call in ("healthTunnel(t.id)", "restartTunnel(t.id, t.name)", "renameTunnel(t.id, t.name)", "deleteTunnel(t.id, t.name)"):
        assert f"inWin(() => {call})" not in menu, call          # (these need no window: they ask, or tell, by themselves)

    fn = APP.split("window.tunWinOpen = id => {", 1)[1].split("window.tunBack", 1)[0]
    assert 'document.getElementById("tun-detail-" + id)' in fn and "tunModal(t); tunAfter(t);" in fn and 'if (!t || t.state === "starting") return false;' in fn


def test_restarting_one_tunnel_asks_first_like_restart_all():
    fn = APP.split("window.restartTunnel = async (id, name) => {", 1)[1].split("\n", 1)[0]
    assert fn.lstrip().startswith("if (!await gconfirm(") and "return;" in fn.split("toast(", 1)[0]


def test_the_sheet_of_a_repair_in_the_tunnel_window_is_as_high_as_its_text_and_has_a_way_out():
    assert 'html body #modal .tunwin .fix[id^="fix-"]:not(.hidden) { height: auto;' in CSS
    click = APP.split('document.addEventListener("click", e => {      // the sheet of a finished repair', 1)[1].split("\n});", 1)[0]
    assert "#modal .tunwin .fix[id^=\"fix-\"]" in click and ".fx-line.run, .loading" in click and 'b.classList.add("hidden")' in click


def test_a_search_that_finds_nothing_says_so():
    q = APP.split("function applyQ() {", 1)[1].split("\n}\n", 1)[0]
    assert 'n.className = "q-none empty"' in q and "Nothing matches" in q and 'b.querySelector("tr[data-q]:not(.q-hide)")' in q
    assert "#page .lbox > .q-none" in CSS


def test_a_window_that_is_open_is_not_drawn_away_by_the_round_of_the_page_behind():
    tick = APP.split("let lastTick = Date.now();", 1)[1].split("\n", 2)[1]
    assert '#modal .modal:not(.keep)' in tick and "refresh();" in tick
    fn = APP.split("window.trashRestore = async id => {", 1)[1].split("window.trashForget", 1)[0]
    assert fn.index("showModal(") < fn.index("state.keepWin = true") < fn.index("try {") < fn.index("refresh();")          # (the error of a restore stayed on the screen for a blink)


def test_check_all_counts_the_tunnels_whose_servers_did_not_answer():
    fn = APP.split("window.checkAll = async () => {", 1)[1].split("const LOSS_HIGH_PCT", 1)[0]
    assert '${r.unknown ? " · " + r.unknown + " not answering" : ""}' in fn and "r.down || r.unknown || r.high_loss ? \"bad\" : \"ok\"" in fn


def test_a_row_of_the_tunnel_list_opens_with_the_keyboard():
    assert 'title="Click for the details" tabindex="0"' in APP
    key = APP.split('document.addEventListener("keydown", e => {      // a row of the tunnel list can be reached', 1)[1].split("\n});", 1)[0]
    assert '#page .tun-tbl tr.clk[tabindex]' in key and 'e.key === "Enter" || e.key === " "' in key and "r.click()" in key
    assert "html body #page .tun-tbl tr.clk:focus-visible" in CSS


def test_a_long_server_name_in_the_tunnel_list_ends_in_an_ellipsis_and_has_its_tooltip():
    assert '<td class="td-node ir"><b title="${esc(t.iran_name)}"><span class="nm">${esc(t.iran_name)}</span></b>' in APP
    assert '<td class="td-node"><b title="${esc(t.foreign_name)}"><span class="nm">${esc(t.foreign_name)}</span>${dupMark(t)}</b>' in APP          # (the "⚠ ×2" mark stays whole beside the cut name)
    assert "html body #page .tun-tbl td.td-node > b > .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }" in CSS
    assert "html body #page .tun-tbl td.td-node > b > :not(.nm) { flex: none; }" in CSS


def test_the_build_form_starts_on_a_foreign_server_that_answers():
    assert "const firstOn = role =>" in APP and re.search(r'role === "foreign" && \(v\[name\] == null \|\| v\[name\] === ""\) && s\.id === firstOn\(role\)', APP)
