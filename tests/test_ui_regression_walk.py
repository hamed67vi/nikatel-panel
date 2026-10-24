"""The walk of the whole panel in a real browser after 1.69.36-1.69.49 (every page, 3 themes, dark and light, English and Persian, 1440 / 1280 / 1024 / 768 / 390 px). What was found:
the fluid layer did not understand a Persian header (no column ever dropped), the question box was a full-screen sheet on a phone and its list of choices 99 px wide, a tall question went above the window,
the tabs under the title were cut on a phone, a server that is being rebooted jumped to the top of the list with a red "Offline" / "GRE ✕", and the range buttons of "Best today" were never lit in Persian.
The pieces that can be run are run with node (a small stand-in for the DOM); the style rules are pinned in the source of the sheets."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"
FLUID_JS = (WEB / "fluid.js").read_text(encoding="utf-8")
FLUID_CSS = (WEB / "fluid.css").read_text(encoding="utf-8")
I18N_JS = (WEB / "i18n.js").read_text(encoding="utf-8")
LAB_JS = (WEB / "lab.js").read_text(encoding="utf-8")
LAB_CSS = (WEB / "lab.css").read_text(encoding="utf-8")
APP_JS = (WEB / "app.js").read_text(encoding="utf-8")
ANALYST_JS = (WEB / "analyst.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _node(code: str):
    out = subprocess.run([NODE, "-e", code], check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _line(src: str, head: str) -> str:
    return re.search(rf"^{re.escape(head)}.*$", src, re.MULTILINE).group(0)


def _function(src: str, head: str) -> str:
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


# ---------------------------------------------------------------- 1. the fluid layer reads the header as the code wrote it, not as it is translated

# the server list at 768 px, with its real colgroup and its real headers (data-prio / data-min / data-fit as in lab.js)
FLUID_HARNESS = r"""
const SRC = %(src)s;
const HEADS = [["Name", "نام", {"data-prio": "0", "data-fit": "192"}], ["IP", "IP", {"data-prio": "1", "data-min": "168"}], ["Problems", "مشکلات", {"data-prio": "3", "data-min": "92"}],
  ["Load", "بار", {"data-prio": "2", "data-min": "128"}], ["Traffic used", "ترافیک مصرف‌شده", {"data-prio": "3"}], ["Bandwidth Mbit/s", "پهنای باند Mbit/s", {"data-prio": "4", "data-min": "126"}], ["", "", {}]];
const PCT = ["21%", "15%", "15%", "18%", "13%", "12%", "6%"];
function run(persian, withSource) {
  const ths = HEADS.map(([en, fa, attrs]) => ({en, textContent: persian ? fa : en, getAttribute: n => (n in attrs ? attrs[n] : null), querySelector: () => null, off: null, classList: {toggle(c, on) { this.state = !!on; }, state: false}}));
  const cols = PCT.map(w => ({style: {width: w}, classList: {toggle(c, on) { this.state = !!on; }, state: false}}));
  const el = () => ({style: {}, setAttribute() {}, appendChild() {}, querySelectorAll: () => [], remove() {}});
  const tbl = {className: "tbl", parentElement: {clientWidth: 657, appendChild() {}}, tBodies: [{rows: []}], rows: [], dispatchEvent() {},
    querySelectorAll: sel => (sel.includes("thead") ? ths : sel.includes("colgroup") ? cols : [])};
  const win = {NK_FLUID: true};
  if (withSource) win.I18N = {source: th => th.en};
  new Function("window", "document", "getComputedStyle", "MutationObserver", "addEventListener", "Event", SRC)(win,
    {querySelectorAll: () => [tbl], addEventListener() {}, createElement: el, body: {}}, () => ({paddingLeft: "0px", paddingRight: "0px", borderLeftWidth: "0px", borderRightWidth: "0px", borderCollapse: "collapse", borderSpacing: "0px"}),
    class { observe() {} }, () => {}, class {});
  return ths.map(t => t.classList.state);
}
console.log(JSON.stringify({en: run(false, false), faAware: run(true, true), faBlind: run(true, false)}));
"""


@needs_node
def test_a_persian_header_drops_the_same_columns_as_the_english_one():
    """Found at 768 px in Persian: fluid.js read the TRANSLATED header, which has no a-z letter, so every column was "no title" - none ever dropped, the name column was squeezed to 75 px (a lamp and a letter)
    where the English list has 184 px and has dropped Traffic used and Bandwidth. The script asks the Persian layer for the text as the code wrote it (I18N.source)."""
    r = _node(FLUID_HARNESS.replace("%(src)s", json.dumps(FLUID_JS)))
    assert any(r["en"]), "the English list drops a column at 768 px (the test is about the same columns going in Persian)"
    assert r["faAware"] == r["en"]
    assert r["faBlind"] != r["en"], "without the source of the text a Persian header drops nothing: that is what this test guards against"


I18N_HARNESS = r"""
globalThis.window = {};
globalThis.localStorage = {getItem: () => "en", setItem() {}};
globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.NodeFilter = {SHOW_TEXT: 4, SHOW_ELEMENT: 1, FILTER_ACCEPT: 1, FILTER_REJECT: 2};
class N { constructor(t) { this.nodeType = t; this.childNodes = []; this.parentNode = null; }
  get parentElement() { return this.parentNode; } get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() { const p = this.parentNode; return p ? p.childNodes[p.childNodes.indexOf(this) + 1] || null : null; }
  appendChild(c) { c.parentNode = this; this.childNodes.push(c); return c; } }
class T extends N { constructor(v) { super(3); this.nodeValue = v; } }
class E extends N { constructor(tag, kids = []) { super(1); this.tagName = tag.toUpperCase(); this.a = new Map(); kids.forEach(k => this.appendChild(typeof k === "string" ? new T(k) : k)); }
  hasAttribute(n) { return this.a.has(n); } getAttribute(n) { return this.a.get(n) ?? null; } setAttribute(n, v) { this.a.set(n, v); } }
const th = new E("th", ["Traffic used"]), th2 = new E("th", ["Bandwidth"]), body = new E("body", [new E("table", [new E("thead", [new E("tr", [th, th2])])])]);
globalThis.document = {title: "", documentElement: new E("html"), body, addEventListener() {}, querySelectorAll: () => [],
  createTreeWalker(root, what, filter) { const list = []; const visit = n => { for (const c of n.childNodes) { const r = filter.acceptNode(c); if (r === 1) list.push(c); if (r !== 2 && c.nodeType === 1) visit(c); } }; visit(root); let i = 0; return {nextNode: () => list[i++] || null}; }};
new Function(%(src)s)();
const I18N = window.I18N;
I18N.add({"Traffic used": "ترافیک مصرف‌شده", "Bandwidth": "پهنای باند"});
I18N.set("fa");
const fa = [th.firstChild.nodeValue, I18N.source(th), I18N.source(body)];
th2.firstChild.nodeValue = "Other";      // the page wrote a new text after the translation
const changed = I18N.source(th2);
I18N.set("en");
console.log(JSON.stringify({fa, changed, en: [th.firstChild.nodeValue, I18N.source(th)]}));
"""


@needs_node
def test_the_persian_layer_gives_back_the_text_as_the_code_wrote_it():
    r = _node(I18N_HARNESS.replace("%(src)s", json.dumps(I18N_JS)))
    assert r["fa"][0] == "ترافیک مصرف‌شده"      # (the screen is Persian)
    assert r["fa"][1] == "Traffic used" and r["fa"][2] == "Traffic usedBandwidth"      # (the source is not)
    assert r["changed"] == "Other"      # (a text the page changed itself after the translation is read as it is)
    assert r["en"] == ["Traffic used", "Traffic used"]
    assert "window.I18N" in FLUID_JS and "I18N.source(th)" in FLUID_JS


# ---------------------------------------------------------------- 2. the range buttons of "Best today" are found by their number

def test_the_range_buttons_of_best_today_are_lit_by_their_number_not_their_words():
    """In Persian no button said "6 hours", so a press unlit all three."""
    assert 'data-h="${h}" onclick="bestRange(${h})"' in ANALYST_JS
    fn = _line(APP_JS, "window.bestRange = h =>")
    assert "+b.dataset.h === h" in fn and "textContent" not in fn


# ---------------------------------------------------------------- 3. a rebooted server keeps its place and shows no red alarm for what the restart itself causes

RANK_HARNESS = r"""
const S = {};
const labTone = p => p.kind === "node" ? "busy" : S[p.id].tone, labServer = p => ({id: p.id}), labIssues = s => S[s.id].iss;
%(src)s
const set = (id, tone, iss) => { S[id] = {tone, iss: iss.map(i => typeof i === "string" ? {txt: i} : i)}; return labRank({id}); };
const cut = txt => (txt.startsWith("GRE") ? {txt, gre: true} : {txt});      // (the GRE tag carries gre: true, "N tunnels down" is known by its words)
const out = {};
out.healthy_then_rebooted = [set(6, "ok", []), set(6, "busy", ["Offline", cut("GRE ✕"), cut("2 tunnels down")])];
out.problem_then_rebooted = [set(7, "warn", ["CPU 99%"]), set(7, "busy", ["CPU 99%", "Offline"])];
out.tunnel_problem_then_rebooted = [set(8, "warn", [cut("2 tunnels down")]), set(8, "busy", ["Offline", cut("2 tunnels down")])];
out.healthy_then_a_new_problem_while_busy = [set(9, "ok", []), set(9, "busy", ["Filtered"])];
out.no_memory_only_restart = set(10, "busy", ["Offline", "SSH ✕", cut("GRE ✕")]);
out.no_memory_a_real_problem = set(11, "busy", ["Offline", "Bad route"]);
out.back_to_healthy = [set(12, "busy", ["Offline"]), set(12, "ok", []), set(12, "busy", ["Offline", cut("GRE ✕")])];
out.red_stays_first = set(13, "bad", ["Offline"]);
console.log(JSON.stringify(out));
"""


@needs_node
def test_a_healthy_server_that_is_rebooted_does_not_jump_to_the_top_when_its_tunnels_go_down():
    """The owner, 2026-10-20 (1.69.37, 1.69.41): "no jump". 1.69.49 now marks the tunnels of a server that goes off down within seconds, and the rank of a busy server counted them as problems of its own
    ("GRE ✕", "2 tunnels down" are not the "Offline" the rank already ignored): a healthy server that was being rebooted went to the top of the list."""
    src = "\n".join(_line(LAB_JS, k) for k in ("const labBusyOk", "const labRestartCut", "const labRankLast", "const labRank ="))
    r = _node(RANK_HARNESS.replace("%(src)s", src))
    assert r["healthy_then_rebooted"] == [3, 2]      # (healthy, then busy: the group of the healthy, not of the problems)
    assert r["problem_then_rebooted"] == [1, 1]      # (a problem of its own stays with the problems)
    assert r["tunnel_problem_then_rebooted"] == [1, 1]      # (and a server whose only problem was a tunnel does not drop to the middle either)
    assert r["healthy_then_a_new_problem_while_busy"] == [3, 1]      # (Filtered is not what a restart causes: it goes up)
    assert r["no_memory_only_restart"] == 2 and r["no_memory_a_real_problem"] == 1      # (a page opened in the middle of a reboot has no memory: the same rules by the issues)
    assert r["back_to_healthy"] == [2, 3, 2]
    assert r["red_stays_first"] == 0


CHIPS_HARNESS = r"""
const esc = x => String(x), LAB_ISS_IC = {bad: "", warn: ""};
const srvBusy = s => s.busy, labIssues = s => s.iss;
%(src)s
%(chips)s
const iss = [{sev: "bad", txt: "Offline"}, {sev: "bad", txt: "GRE ✕", gre: true}, {sev: "bad", txt: "Filtered"}];
const names = html => [...html.matchAll(/<\/svg>?([^<]+)<\/span>|">([^<>]+)<\/span>/g)].map(m => m[1] || m[2]);
console.log(JSON.stringify({idle: names(labIssueChips({iss})), rebooting: names(labIssueChips({iss, busy: {what: "Rebooting"}})), installing: names(labIssueChips({iss, busy: {what: "installing"}}))}));
"""


@needs_node
def test_the_row_that_says_rebooting_shows_no_red_chips_for_what_the_restart_causes():
    src = "\n".join(_line(LAB_JS, k) for k in ("const labBusyOk", "const labRestartCut"))
    r = _node(CHIPS_HARNESS.replace("%(src)s", src).replace("%(chips)s", _function(LAB_JS, "const labIssueChips = s => {")))
    assert r["idle"] == ["Offline"]      # (1.69.130, the owner: a server that is offline says only Offline - no GRE, no Filtered, no tunnel tags)
    assert r["rebooting"] == ["Filtered"]      # (a real problem is still said)
    assert r["installing"] == ["Offline"]      # (only a restart explains an offline server; while it installs it is still just Offline)


def test_the_issues_a_restart_causes_are_the_offline_ones_the_gre_tag_and_the_tunnels_down():
    """What labIssues says about the tunnels of a server (the GRE tag is flagged gre, "N tunnels down" is a sentence) is what a restart cuts: the call sites of labIssues are not changed (test_web_menu pins them)."""
    line = _line(LAB_JS, "const labRestartCut")
    assert "labBusyOk.has(i.txt)" in line and "i.gre" in line and r"/^\d+ tunnels? down$/" in line
    iss = _function(LAB_JS, "function labIssues(s) {")
    assert '"GRE ✕" : "GRE ✓/✕"' in iss and "tunnel${down.length === 1 ? \"\" : \"s\"} down`" in iss

# ---------------------------------------------------------------- 4. the question box on a phone, with a list of choices, and in a short window

def _rule(css: str, selector: str) -> str:
    """The text between the braces of the first rule whose selector is exactly this one."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return m.group(1)


def test_the_question_box_is_a_small_alert_on_a_phone_not_a_full_screen_sheet():
    """On a 390 px phone the box measured 353 x 800: the sheet rules of mobile2.css (".modal { height: 100%; min-height: 100% !important; border-radius: 0 !important }") reached it, so the
    two buttons were 239 px tall. Its own rules come after them in lab.css and name the id, so they win over an !important of a lower specificity only when they are !important too."""
    block = LAB_CSS[LAB_CSS.index("/* The question box on a phone."):]
    assert "@media (max-width: 760px) and (pointer: coarse) {" in block
    box = _rule(block, "html body #ask .modal.ask-box")
    for need in ("width: min(344px, calc(100vw - 40px)) !important", "height: auto !important", "min-height: 0 !important", "max-height: 100% !important", "border-radius: 16px !important"):
        assert need in box
    assert "!important" in _rule(block, "html body #ask .overlay") and "align-items: center" in _rule(block, "html body #ask .overlay")
    assert "width: min(420px, calc(100vw - 32px)) !important" in _rule(block, "html body #ask .modal.ask-box:has(.mv-plan)")      # (the plan of "Move the tunnels" is wider)
    mobile = (WEB / "mobile2.css").read_text(encoding="utf-8")
    assert "min-height: 100% !important" in mobile      # (the reason the rules above need their !important)
    assert LAB_CSS.index("/* The question box on a phone.") > LAB_CSS.index("html body #ask .modal.ask-box:not(:has(.mv-plan))")


def test_a_list_of_choices_and_the_tick_row_fill_the_question_box():
    """The 1.69.39 alert centres its rows: a list of choices was as wide as its widest word (99 px buttons in a 344 px box) and the tick row 221 px."""
    sel = "html body #ask .modal.ask-box:not(:has(.mv-plan)) :is(.ask-act.ask-choices, .ask-check)"
    body = _rule(LAB_CSS, sel)
    assert "width: 100%" in body and "box-sizing: border-box" in body


def test_a_tall_question_never_goes_above_a_short_window():
    """A plan of 615 px in a window of 480 px lost its first 55 px above the window (a centred box cannot be scrolled up to), a list of 12 choices 183 px."""
    body = _rule(LAB_CSS, "html body #ask .modal.ask-box")
    assert "max-height: 100%" in body and "overflow-y: auto" in body


# ---------------------------------------------------------------- 5. the tabs under the title on a phone

def test_the_tabs_under_the_title_keep_their_size_and_scroll_on_a_phone():
    """#subnav is outside #page, so the fluid rules written for "#page .tabs" never reached it: on a 390 px phone "Tunnel manager" / "Tunnel Doctor" / "CDN" shrank alike and were cut mid-word."""
    block = FLUID_CSS[FLUID_CSS.index("the tabs under the header (Tunnel manager"):]
    assert "@media (max-width: 760px) {" in block
    assert "overflow-x: auto" in _rule(block, "html[data-fluid] body #subnav .tabs.sub-tabs")
    assert "flex: none" in _rule(block, "html[data-fluid] body #subnav .tabs.sub-tabs > button")


def test_the_open_tab_of_a_scrolled_strip_is_brought_into_view_only_when_it_is_hidden():
    fn = _function(APP_JS, "function renderSub() {")
    assert "bar.scrollWidth > bar.clientWidth + 1" in fn and "r.left < b.left || r.right > b.right" in fn and "bar.scrollLeft +=" in fn


# ---------------------------------------------------------------- 6. the number cards of a server window, a hint wider than its field

def test_the_note_of_a_number_card_is_not_cut_in_the_middle_of_a_digit():
    """Server window, Monitor ("Connections 3073 / peak 4268" at 1280 px): the old rule `.modal .mtile span { max-width: 62% }` cut the note to "peak 4" and the card scrolled 12 px inside itself."""
    assert "max-width: 62%" in (WEB / "app.css").read_text(encoding="utf-8")      # (the old rule is still there: the new one overrides it for these windows)
    body = _rule(LAB_CSS, "html body #modal :is(.srvwin, .tunwin) .mtile span")
    assert "max-width: none" in body and "min-width: 0" in body and "text-overflow: ellipsis" in body and "overflow: hidden" in body
    assert "minmax(0, 1fr)" in _rule(LAB_CSS, "html body #modal :is(.srvwin, .tunwin) .mtile")


def test_a_hint_wider_than_its_field_ends_with_a_dash():
    """Create tunnel on a phone: "443, 443=2096, 10000-10009" stopped in the middle of the last number (230 px of hint in a 212 px field). The dash is the field's own text-overflow (the browser does not read it from ::placeholder)."""
    assert "text-overflow: ellipsis" in _rule(FLUID_CSS, "html[data-fluid] body input:not(:focus)")


# ---------------------------------------------------------------- 7. a double click on the OK of a question

def test_the_second_click_of_a_double_click_on_ok_does_not_reach_the_page_under_the_question():
    """Found in the browser with a real double click on "Remove ir-east-1?": the box went at the first click, the second landed on the row under the button and opened that server's window. The box now leaves an
    invisible layer for 450 ms that takes the second click, and the keyboard focus goes back to what had it."""
    helper = _function(APP_JS, "function askLeave(el, opener) {")
    assert 'el.classList.add("ask-out")' in helper and "setTimeout(() => el.remove(), 450)" in helper and "opener.focus(" in helper
    for head, end in (("function glassAsk(", "/* a question with several answers"), ("function glassChoice(", "window.gconfirm = ")):
        fn = APP_JS[APP_JS.index(head):APP_JS.index(end)]
        assert "askLeave(el, opener)" in fn and "const opener = document.activeElement" in fn
        assert "el.remove(); resolve(v)" not in fn      # (the old way: gone at once)
    body = _rule(LAB_CSS, "html body #ask.ask-out .overlay, html body #ask.ask-out .modal")
    assert "opacity: 0 !important" in body and "animation: none !important" in body
    assert "pointer-events: none" not in body      # (a layer that lets the click through is no layer)


# ---------------------------------------------------------------- 8. the neighbours of a server that is being rebooted

PEER_HARNESS = r"""
globalThis.window = {};
const noLogin = () => false;
const NOW = Date.now() / 1000;
const mk = (id, name, busy) => ({id, name, enabled: true, online: true, facts: {}, extra: busy ? {busy: {what: busy, at: NOW}} : {}});
%(src)s
function names(serversBusy, tunnelState) {
  const ir = mk(1, "ir-a", serversBusy), de = mk(2, "de-a", ""), other = mk(3, "de-b", "");
  globalThis.state = {serversCache: [ir, de, other], tunnelsCache: [{iran_name: "ir-a", foreign_name: "de-a", type: "backhaul", state: tunnelState}, {iran_name: "ir-a", foreign_name: "de-b", type: "backhaul", state: "up"}]};
  return {peer: labIssues(de).map(i => i.txt), self: labIssues(ir).map(i => i.txt), bystander: labIssues(other).map(i => i.txt)};
}
console.log(JSON.stringify({rebooting: names("Rebooting", "down"), installing: names("installing", "down"), idle: names("", "down"), healthy: names("Rebooting", "up")}));
"""


@needs_node
def test_a_tunnel_that_is_down_because_its_other_server_is_being_rebooted_is_no_problem_of_this_server():
    """Found by watching the list while a server was rebooted (layout-shift observer): the foreign server of its tunnel rose from the middle of the list to the third row with "1 tunnel down" - 1.69.49 marks the
    tunnels of a server that goes off down within seconds - and went back when the reboot ended: every restart of an Iran server moved its neighbours. The tunnel is still in the tunnel list, red; the neighbour's
    row does not say it, and does not rise."""
    src = "\n".join([_line(APP_JS, "const srvBusy = s =>"), _line(LAB_JS, "const labPeerRebooting = "), _function(LAB_JS, "function labIssues(s) {")])
    r = _node(PEER_HARNESS.replace("%(src)s", src))
    assert r["rebooting"]["peer"] == []      # (the neighbour: nothing to say)
    assert r["rebooting"]["self"] == ["GRE ✓/✕"]      # (the server itself still lists it - one of its two tunnels is down: the row is busy and labRank / the chips know what a restart causes)
    assert r["rebooting"]["bystander"] == []
    assert r["installing"]["peer"] == ["GRE ✕"]      # (only a reboot explains it: an install does not cut a tunnel)
    assert r["idle"]["peer"] == ["GRE ✕"]      # (a tunnel that is down with nothing being done to the other end is a problem)
    assert r["healthy"]["peer"] == [] and r["healthy"]["self"] == []


def test_a_long_value_of_a_record_that_is_not_an_address_breaks_and_ends_with_a_dash():
    """CDN page: the value of a TXT record ("v=DMARC1; p=none; rua=mailto:...") has no space to break at, so it ran 57 px past its cell and was cut without a dash; it is clamped to the two lines the row has."""
    body = _rule(FLUID_CSS, "html body #page .dns-tbl td.val > .mut")
    assert "-webkit-line-clamp: 2" in body and "overflow: hidden" in body and "overflow-wrap: anywhere" in body
    assert 'class="val" title="${esc(r.type === "A" ? r.ips.join(" · ") : r.value)}"' in APP_JS      # (the whole value is the tip of the cell)


def test_the_body_of_a_box_of_api_and_key_has_a_column_that_may_be_narrower_than_its_rows():
    """Settings, Api & Key, with a DNS account at 768 px: the box is a grid with ONE unsized column, so the row of the account (name, pill, Test / Edit / Remove) made it 676 px in a box of 640 and the
    Remove button ran past the box and the screen (the same trap as the setup cards and the health list of the Dashboard, a few lines above the rule)."""
    assert "grid-template-columns: minmax(0, 1fr)" in _rule(FLUID_CSS, "html[data-fluid] body #page .ab-b")
    assert "html body #page .ab-b { display: grid;" in LAB_CSS      # (the grid with no column size is the rule above it)


# ---------------------------------------------------------------- 9. the tabs of the profiles on the Servers page

REVEAL_HARNESS = r"""
const rect = (l, w) => ({left: l, right: l + w, width: w});
const mk = (scrollW, clientW, stripLeft, stripW, onLeft, onW, scrollLeft = 0) => { const on = {getBoundingClientRect: () => rect(onLeft, onW)}; const ks = {scrollWidth: scrollW, clientWidth: clientW, scrollLeft, getBoundingClientRect: () => rect(stripLeft, stripW), querySelector: () => on}; return ks; };
let KS = null; const $ = () => KS;
%(src)s
const run = ks => { KS = ks; labTabsReveal(); return Math.round(ks.scrollLeft); };
console.log(JSON.stringify({
  fits: run(mk(300, 300, 100, 300, 120, 80)),            // everything fits: nothing moves
  visible: run(mk(700, 300, 100, 300, 150, 80, 10)),     // the open tab is inside the strip: nothing moves (a strip the owner scrolled stays)
  hiddenRight: run(mk(700, 300, 100, 300, 500, 80)),     // hidden on the right: brought to the middle (500 - 100 - (300 - 80) / 2 = 290)
  wider: run(mk(700, 100, 100, 100, 400, 190)),          // wider than the strip: its left edge is at the left of the strip (400 - 100)
}));
"""


@needs_node
def test_the_open_profile_tab_is_brought_into_view_only_when_it_is_hidden():
    src = _line(LAB_JS, "const labTabsReveal = ")
    r = _node(REVEAL_HARNESS.replace("%(src)s", src))
    assert r == {"fits": 0, "visible": 10, "hiddenRight": 290, "wider": 300}
    page = _function(LAB_JS, "PAGES.lab = async () => {")
    assert "labTabsReveal();" in page and "ks1.scrollLeft = sl0" in page      # (and a strip the owner scrolled keeps its place when the page is drawn again)
    assert "labTabsReveal();" in _line(LAB_JS, "window.labKind = k =>")


def test_the_tabs_of_the_profiles_keep_their_names_and_scroll_when_they_do_not_fit():
    """With five profiles at 1280 px every name shrank to 0 px (a flag and a 6): the tab was allowed down to 46 px and the label gave way first. The fluid rule `html[data-fluid] body #page .tabs > button
    { flex: 0 1 auto; min-width: 0; overflow: hidden }` comes after lab.css, so the new rule has to name `html[data-fluid]` too."""
    block = LAB_CSS[LAB_CSS.index("/* The tabs of the profiles (All | Iran"):]
    tab = _rule(block, "html[data-fluid] body #page .tabs.lab-kinds > button, html body #page .tabs.lab-kinds > button")
    assert "flex: none" in tab and "min-width: auto" in tab and "overflow: visible" in tab
    assert "padding: 0 9px" in _rule(block, "html[data-fluid] body #page .lab-bar:has(.lab-kinds > button:nth-child(5)) .tabs.lab-kinds > button")
    assert "@container labbar (max-width: 1000px) {" in block and ".lab-chip > span { display: none; }" in block      # (five tabs or more: the chips lose their words earlier)
    assert "html body #page .tabs.lab-kinds { flex: 0 1 auto !important; min-width: 0; overflow-x: auto; }" in LAB_CSS      # (the strip itself scrolls: it is the buttons that no longer shrink)


@needs_node
def test_an_offline_server_says_only_offline_and_a_server_that_answers_keeps_all_its_tags():
    """The owner, 2026-10-24, with a picture of a row "Offline" + "GRE ✕": "when a server is offline no GRE tag or any other tag, only Offline" - the tunnels of a server that does not answer are down because it is off, and the
    other tags (Filtered, a bad route, CPU ...) cannot be read from a server that does not answer."""
    src = "\n".join(_line(LAB_JS, k) for k in ("const labBusyOk", "const labRestartCut"))
    code = CHIPS_HARNESS.split("const iss = ", 1)[0].replace("%(src)s", src).replace("%(chips)s", _function(LAB_JS, "const labIssueChips = s => {")) + """
const names = html => [...html.matchAll(/<\\/svg>?([^<]+)<\\/span>|">([^<>]+)<\\/span>/g)].map(m => m[1] || m[2]);
const off = [{sev: "bad", txt: "GRE ✕", gre: true}, {sev: "bad", txt: "Offline"}, {sev: "bad", txt: "3 tunnels down"}, {sev: "warn", txt: "CPU 91%"}];
const up = [{sev: "bad", txt: "GRE ✕", gre: true}, {sev: "bad", txt: "Filtered"}, {sev: "warn", txt: "CPU 91%"}, {sev: "warn", txt: "RAM 90%"}];
console.log(JSON.stringify({off: names(labIssueChips({iss: off})), up: names(labIssueChips({iss: up})), none: names(labIssueChips({iss: []}))}));
"""
    r = _node(code)
    assert r["off"] == ["Offline"]      # (whatever the order the issues come in)
    assert r["up"][:3] == ["GRE ✕", "Filtered", "CPU 91%"] and len(r["up"]) == 4      # (three chips and "+1": a server that answers keeps all its tags)
    assert r["none"] == []
