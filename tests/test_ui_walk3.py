"""The third walk of the test panel (1.69.33): the CDN page with DNS accounts, the first-run card, the Shop bot page, and the tooltips that stayed English in Persian mode.
What the walk measured (cut texts, a box wider than its parent, a button pushed out of the window, a counter squeezed to a sliver) is kept here as checks of the stylesheet and of the pages' source;
the Persian texts are run through the real translation layer with node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
FLUID_CSS = (WEB / "fluid.css").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
PERSIAN_ON = "const PERSIAN_ON = true;" in I18N
SKIP_PERSIAN = pytest.mark.skipif(NODE is None or not PERSIAN_ON, reason="node is not installed, or Persian is switched off for now (PERSIAN_ON in i18n.js; the owner, 2026-10-21: Persian at the very end)")

# node: the translation layer with the dictionaries, a stand-in for the page; reads the strings to look up from stdin
LAYER = r"""
const fs = require("fs"), vm = require("vm"), path = require("path");
const dir = process.argv[1];
class MO { observe() {} disconnect() {} takeRecords() { return []; } }
const sb = {localStorage: {getItem: k => k === "nk.lang" ? "fa" : null, setItem() {}}, document: {addEventListener() {}, documentElement: {}, querySelectorAll: () => [], body: {}}, MutationObserver: MO, NodeFilter: {}, queueMicrotask, console, setTimeout, clearTimeout};
sb.window = sb; vm.createContext(sb);
for (const f of ["i18n.js", "i18n.fa.ui.js", "i18n.fa.msg.js", "i18n.fa.doctor.js", "i18n.fa.ui2.js"]) vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), sb, {filename: f});
const input = JSON.parse(fs.readFileSync(0, "utf8"));
console.log(JSON.stringify(input.map(s => sb.I18N.tr(s))));
"""


def _translate(texts: list[str]) -> list[str]:
    out = subprocess.run([NODE, "-e", LAYER, str(WEB)], input=json.dumps(texts), check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_cdn_bar_keeps_its_buttons_and_tabs_whole_and_wraps_when_it_must():
    """Tunnels > CDN: "Add record", "Records 6", "Changes", "Failover" and the counts were all cut at 1280 px and below (every item of the row shrank alike) and the tabs ran out of the box under 800 px."""
    assert ".toolbar.dns-bar { container: dnsbar / inline-size; }" in FLUID_CSS      # (a container cannot ask about its own width: the bar is the container, the row is inside)
    assert ".dns-bar .dns-r.dns-main > :is(.cta, .tabs, .dns-stats) { flex: none; }" in FLUID_CSS
    assert ".dns-bar .dns-r.dns-main .tabs > button { flex: none; overflow: visible; text-overflow: clip; }" in FLUID_CSS
    assert ".dns-bar .dns-dsel select { box-sizing: border-box; min-width: 0; }" in FLUID_CSS      # (the domain box was 51 px wider than its label)
    assert "@container dnsbar (max-width: 1060px)" in FLUID_CSS and "@container dnsbar (max-width: 840px)" in FLUID_CSS and "flex-wrap: wrap" in FLUID_CSS[FLUID_CSS.index("@container dnsbar (max-width: 840px)"):]


def test_a_record_shows_its_address_even_where_its_column_has_no_room():
    """On a phone the table kept Type, Name and the buttons: the address - what the table is for - was gone. It is shown under the name when its column drops."""
    assert '<th class="dns-vh" data-min="150">Value</th>' in APP and '<th data-min="76">Port</th>' in APP and '<th data-fit="200">Name</th>' in APP
    assert '<small class="dns-sub mut">${esc(r.type === "A" ? r.ips.join(" · ") : r.value)}</small>' in APP
    assert "td.name > .dns-sub { display: none; }" in FLUID_CSS and ".dns-tbl:has(> thead th.dns-vh.fl-off) td.name > .dns-sub { display: block;" in FLUID_CSS
    # a cut value, a long account name and a change line tell themselves in a tip; "who did what" wraps (the verb - made, changed, deleted - was the cut part)
    assert '<td class="val" title="${esc(r.type === "A" ? r.ips.join(" · ") : r.value)}">' in APP
    assert '<td class="name" title="${esc(a.name)}${a.main ?' in APP
    assert 'class="dns-who" title=' in APP and ".dns-box table.tbl td.dns-who { white-space: normal; overflow-wrap: anywhere; }" in FLUID_CSS


def test_the_address_of_a_dns_record_is_not_asked_for_in_a_hint_wider_than_the_box():
    assert "203.0.113.10, 203.0.113.11" in APP and "several: separate with commas" not in APP      # (the placeholder was 344 px in a 298 px box on a phone)
    assert 'placeholder="sub  (or @)" title="@ = the domain itself"' in APP and "sub  (@ = the domain itself)" not in APP
    assert 'el.title = n && ips.length ?' in APP      # (the preview line of the form is cut on a phone: its tip says it all)


def test_the_page_header_gives_way_to_its_buttons():
    """The title block would not shrink below its subtitle (900 px): on a 1280 px screen the Persian and day/night buttons ended 19 px outside the window, on every page."""
    assert "html[data-fluid] body .head > div:first-child { flex: 1 1 0; min-width: 0; }" in FLUID_CSS
    assert "html[data-fluid] body .head p#subtitle { max-width: 100%; }" in FLUID_CSS and "html[data-fluid] body .head > .head-r { flex: none; }" in FLUID_CSS
    assert '$("#subtitle").textContent = $("#subtitle").title = INFO[page][1];' in APP      # (a subtitle cut with a dash says all of itself in a tip)


def test_a_grid_that_holds_a_text_that_must_not_wrap_may_still_be_narrower_than_the_text():
    """A grid without a column size is one column as wide as its widest item: the inputs of the setup card ran 28 px out of their card and the numbers of the health list were cut."""
    for sel in (".su-b { grid-template-columns: minmax(0, 1fr); }", ".su-f { grid-template-columns: minmax(0, 1fr); }", ".dash-cols :is(.hl-list, .pb-list) { grid-template-columns: minmax(0, 1fr); }"):
        assert "html[data-fluid] body #page " + sel in FLUID_CSS, sel
    assert ".su-i:not(.done) .su-b small { white-space: normal;" in FLUID_CSS      # (the hint of a card is what tells a new user what to do)
    assert 'suIn("token", "bot token", "password", "The token of your bot from @BotFather' in APP and "bot token 123456789:AA" not in APP


def test_the_shop_bot_head_does_not_squeeze_its_counters():
    """The Services box (all its chips in ONE row) took 732 px of 970 and the four counters were 52 px wide slivers with their numbers drawn outside."""
    assert "html[data-fluid] body #page .sb-head { grid-template-columns: repeat(auto-fit, minmax(min(100%, 430px), 1fr)); }" in FLUID_CSS
    assert "html[data-fluid] body #page .sb-svc, html[data-fluid] body #page .sb-svc .sb-chips { flex-wrap: wrap; }" in FLUID_CSS
    assert "@container sbhead (max-width: 440px)" in FLUID_CSS and "grid-template-columns: minmax(0, 1fr) !important;" in FLUID_CSS      # (the phone rules of app.css say two columns with !important)
    assert "${errs ? errs + (errs === 1 ? \" error\" : \" errors\") : \"No errors logged\"}" in APP      # ("1 errors")


@SKIP_PERSIAN
def test_a_text_box_gets_its_tip_and_hint_in_persian():
    """i18n.js skipped a TEXTAREA with everything inside it (right: its text is the user's), and with its attributes too (wrong: the title and the placeholder are texts of the panel)."""
    assert 'if (root.nodeType === 1 && root.tagName === "TEXTAREA") attrs(root);' in I18N
    assert 'if (n.tagName === "TEXTAREA") attrs(n);' in I18N
    assert 'b.setAttribute("aria-label", fa ? "زبان" : "Language")' in I18N


@SKIP_PERSIAN
def test_the_tooltips_that_stayed_english_are_translated():
    shown = ["Look at this server's own network now (about ten seconds; read only)", "Choose a domain: its records are listed below", "Actions of ir-north-1", "Enable or disable ir-north-1",
             "What the server gives out right now: 55.6 Mb/s (what it takes in: 90.8 Mb/s)", "DOWN - the foreign side does not answer", "UNSTABLE - 18 of 100 pings lost",
             "23:39 - 00:09: answered now and then", "A server of this tunnel is off (ae-edge-1): Fix works when it is back", "Getting started", "Continue setup", "Network", "General", "Admins",
             "Users use it now: ↓ 34 Mb/s (download) ↑ 8.0 Mb/s (upload) - counted from the link of the tunnel on its Iran server over about 15 seconds; from the last check, 955 s ago (the live reading starts in a few seconds)",
             "11 background jobs, late: servers, pulse · the panel's SSH key is missing", "Connect your panel", "Marzban or PasarGuard: its address and an API key", "＋ Add an account", "Fix…", "Speed test…", "Rename…",
             "an empty token keeps the saved one", "bot token", "3 to 34 characters", "2 panels", "1 error", "(main · the failover uses it)", "Its mode is", "Click to copy", "Remove this device", "Whole Tehran days: a day starts at 00:00 Tehran = 20:30 UTC of the day before"]
    out = _translate(shown)
    stayed = [s for s, t in zip(shown, out, strict=True) if s == t]
    assert not stayed, stayed
    assert all(re.search(r"[\u0600-\u06FF]", t) for t in out)
    # a number or a name inside a tip is kept as it is
    assert "55.6" in out[shown.index("What the server gives out right now: 55.6 Mb/s (what it takes in: 90.8 Mb/s)")] and "ir-north-1" in out[3]
    assert "23:39" in out[7] and "00:09" in out[7]
    # a family of texts that carry a number is matched by the number only: a name that merely ends in the same word stays as it is
    plain = _translate(["Demo panel", "My account", "Connection error", "Open the front door", "Tick every row", "Remove everything"])
    assert plain == ["Demo panel", "My account", "Connection error", "Open the front door", "Tick every row", "Remove everything"], plain


@SKIP_PERSIAN
def test_no_tooltip_written_in_the_code_is_left_without_persian():
    """Every title / placeholder / aria-label that is written as a sentence in the pages' source has a Persian text (a new one without it fails here: add it to i18n.fa.ui2.js).
    What is not looked at: the ones that are built from a condition (they are found by looking at the page) and the single technical words and examples (a host name, a path, a model name)."""
    attr = re.compile(r"""\b(?:title|placeholder|aria-label)=(?:"([^"\n]{3,})"|'([^'\n]{3,})')""")
    obj = re.compile(r"""\b(?:tip|title|hint|placeholder)\s*:\s*(?:"([^"\n]{3,})"|`([^`\n]{3,})`)""")
    texts: dict[str, str] = {}

    for f in sorted(WEB.glob("*.js")):
        if f.name.startswith("i18n"):
            continue

        src = f.read_text(encoding="utf-8")

        for rx in (attr, obj):
            for m in rx.finditer(src):
                t = (m.group(1) or m.group(2)).replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

                if "${" in t or re.search(r"[?(]\s*$|^['\"]|\s\+\s", t) or not re.search(r"[A-Za-z]{3}", t) or len(t.split()) < 2:      # a condition or a piece of a sum, a symbol, one word
                    continue

                if re.match(r"^(?:https?://|vless://|\{|-----|/|[A-Za-z0-9_.+-]+(?:\s*,\s*[A-Za-z0-9_.+-]+)*$|SELECT |pg_key|203\.0\.|v2rayNG|October news|e\.g\.)", t) or t.startswith(("one per line: name", "ca.example")):
                    continue

                texts.setdefault(t, f.name)

    out = _translate(list(texts))
    left = [f"{texts[s]}: {s[:90]}" for s, t in zip(texts, out, strict=True) if s == t]
    assert len(texts) > 300, "the scan found too few tooltips: the pattern is wrong"
    assert not left, "\n".join(left)