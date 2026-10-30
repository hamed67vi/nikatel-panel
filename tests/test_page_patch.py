"""The page is no longer drawn again from scratch by every refresh (1.69.73; the owner, 2026-10-21: "in every page it is as if it is auto-refreshing, the page keeps jumping - fix it at the root").

Before, a refresh of the page - the round of the ticker, a tunnel or a server that changed its state, a tunnel being worked on - threw away every node of the page and built them again from a new string of HTML: the lists
lost their scroll, the pointer lost the button under it, the flags and the animations were made again. Now such a refresh (`refresh(true)`) is drawn IN PLACE by patch.js: the HTML is turned into a raw tree, compared with the raw
tree of the draw before, and only the nodes the page changed are touched. Tested: the shape of the wiring (what is drawn in place and what is not), the one pure helper with node, and - when a Chrome is on the machine - the
fuzz test of the whole engine in a real browser (random pages, random changes, the layers of the panel done by hand; the page in place must be exactly the page from scratch)."""
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
LAB = (WEB / "lab.js").read_text(encoding="utf-8")
PATCH = (WEB / "patch.js").read_text(encoding="utf-8")
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
PGCOMMON = (WEB / "pg_common.js").read_text(encoding="utf-8")


def test_patch_js_is_loaded_before_app_js_and_gives_the_one_name_the_setter_needs():
    assert INDEX.index('<script src="/static/patch.js"></script>') < INDEX.index('<script src="/static/app.js"></script>')
    assert "window.NKPatch = {make: make, decls: decls, swap: function (oldLive, newLive)" in PATCH


def test_only_a_refresh_nobody_asked_for_is_drawn_in_place_a_click_is_drawn_from_scratch():
    assert "async function refresh(quiet) {" in APP and "refresh.quiet = quiet === true;" in APP
    assert "if (quiet !== true) refresh.quiet = false; return refresh.p; }" in APP                         # (a person's draw is never weaker than a timer's, also when it joins a quiet one that runs)
    assert "if (refresh.p === p) { refresh.p = null; refresh.quiet = false; }" in APP                      # (a draw outside a refresh - a tab of the Servers page - is never quiet)
    ticker = next(ln for ln in APP.split("\n") if ln.startswith("setInterval(() => { if (Date.now() - lastTick <"))
    assert "refresh(true)" in ticker and "refresh();" not in ticker
    assert "refresh(true); }, 30000); };" in LAB                                                             # (the Servers page that looks again every 30 s)
    assert "if (!labPatch()) refresh(true);" in LAB
    assert 'state.page === "tunnels" && !state.open && !state.build) refresh(true); }, 4000); }' in APP      # (the Tunnels page while a tunnel is worked on)
    assert "setTimeout(() => { if (seq === navSeq) refresh(); }," in APP                                     # (the retry after a failed read stays a draw from scratch: the page on the screen is an error box)
    assert "window.nkQuietDraw = () => refresh.quiet === true && !nkInputAfter(refresh.qStart || 0);" in APP  # (a click, a key or a field after the round began makes it from scratch too)
    assert 'quiet = !!draw && nkQuietDraw() && !busy && sig === lastSig;' in APP                           # (not over a loading box or an error box, not for another page or tab than the draw before)


def test_the_setter_of_the_page_patches_or_skips_and_falls_back_to_a_draw_from_scratch():
    setter = APP.split('const off = (() => { try { return localStorage.getItem("nk.patch") === "0"', 1)[1].split("\n})();", 1)[0]
    assert "NKPatch.make(pg, d.set)" in setter and "!off ?" in setter                                         # (and one switch to go back: localStorage nk.patch = 0)
    assert "if (quiet && v === last) { stat.same++; return; }" in setter                                      # (the same HTML in a quiet round: nothing is touched, whatever a layer did to the nodes)
    assert "if (!quiet && v === last && lastDom !== null && d.get.call(pg) === lastDom && !busy) return;" in setter   # (the rule of before stays for everything else)
    assert "const r = draw(v, quiet);" in setter and "else d.set.call(pg, v);" in setter


def test_the_box_a_layer_puts_around_a_table_is_marked_so_that_a_row_that_goes_takes_the_box_with_it():
    assert 'w.setAttribute("data-wrap", ""); tb.replaceWith(w);' in APP
    assert 'var WRAP = "data-wrap";' in PATCH and "t.parentNode.hasAttribute(WRAP)" in PATCH


def test_what_a_page_fills_in_later_by_itself_is_not_looked_into_by_the_draw_of_the_page():
    assert 'id="pn-body" data-own class=' in APP and '<div id="pn-ctl" class="pn-ctl" data-own></div>' in APP and '`<div id="pn-body" data-own>' in APP
    assert 'a.hasAttribute("data-own") !== b.hasAttribute("data-own")' in PATCH
    assert "box._qs = nkQuietDraw() ? refresh.qStart : 0;" in APP
    assert "const same = box._qs && !nkInputAfter(box._qs) && box.dataset.tab === pn.tab && box._ph === html && !box.querySelector(\":scope > .loading\");" in PGCOMMON and "  if (same) return;\n  box._ph = html;" in PGCOMMON


def test_a_node_the_page_replaces_by_itself_costs_a_local_rebuild_not_the_whole_page():
    """1.69.116, the owner (2026-10-23, a picture of the tunnel list): "the list of cards keeps jumping, I think while a tunnel is repaired". While a tunnel is worked on the page is drawn every 4 s, and the live reading of
    the list replaces the meter of a row (`m.replaceWith(nm)`) when its kind changes: the raw tree then stood for a node that was gone and the whole page was drawn from scratch - on every round."""
    assert "if (nm && nm.dataset.k !== m.dataset.k) { m.replaceWith(nm); if (window.NKPatch) NKPatch.swap(m, nm); }" in APP        # (told: the next draw finds the new meter)
    assert "function swap(oldLive, newLive) {" in PATCH and "swap: function (oldLive, newLive)" in PATCH
    assert "function resync(n, live) {" in PATCH and 'if (live === el || !live.isConnected || live.querySelector("[data-own]")) throw e;' in PATCH     # (only below the page, never around something the page fills in by itself)
    assert 'draw.note.forEach(x => { stat.resync = (stat.resync || 0) + 1;' in APP


def test_the_patch_never_trusts_a_tree_the_page_changed_by_itself():
    # a node the page took out, a child it added, a child it took out: the live node is not what the raw tree says - the page is drawn from scratch
    assert 'throw new Error("gone " + desc(r))' in PATCH and 'throw new Error("shape " + desc(o))' in PATCH and 'throw new Error("moved")' in PATCH
    assert "if (live.childNodes.length !== oc.length) throw" in PATCH
    assert "return full(html);      // some of it was done: a draw from scratch sets everything right" in PATCH
    assert 'inert = document.implementation.createHTMLDocument("")' in PATCH                                    # (the raw trees load nothing and run nothing)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
def test_the_style_of_a_node_is_read_declaration_by_declaration_and_a_semicolon_in_an_url_does_not_end_one():
    js = "const window = {}, document = {};\n" + PATCH + """
console.log(JSON.stringify([
  window.NKPatch.decls("width:21%;--flag:url(/flags/de.png)"),
  window.NKPatch.decls("background:url(data:image/svg+xml;utf8,<svg/>) no-repeat; color: red ;"),
  window.NKPatch.decls("content:'a;b';opacity:.5"),
  window.NKPatch.decls(""), window.NKPatch.decls(null), window.NKPatch.decls("junk;;")
]));
"""
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out[0] == [["width", "21%"], ["--flag", "url(/flags/de.png)"]]
    assert out[1] == [["background", "url(data:image/svg+xml;utf8,<svg/>) no-repeat"], ["color", "red"]]
    assert out[2] == [["content", "'a;b'"], ["opacity", ".5"]]
    assert out[3] == [] and out[4] == [] and out[5] == []


def _chrome() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)

        if found:
            return found

    for path in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if Path(path).exists():
            return path

    return None


@pytest.mark.skipif(_chrome() is None, reason="no Chrome on this machine: the engine is fuzzed in a real browser")
def test_the_engine_gives_the_same_page_as_a_draw_from_scratch_for_two_thousand_random_changes(tmp_path):
    page = ROOT / "tests" / "js" / "patch_fuzz.html"
    run = subprocess.run([_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox", f"--user-data-dir={tmp_path}", "--virtual-time-budget=120000", "--dump-dom", page.as_uri()],
                         check=False, capture_output=True, text=True, timeout=180)
    m = re.search(r'data-result="([^"]*)"', run.stdout)
    assert m, run.stderr[-500:] or run.stdout[-500:]
    res = json.loads(html.unescape(m.group(1)))
    assert res["runs"] == 2400
    assert res["fail"] == 0, res["examples"]                                      # (every in-place draw equals the draw from scratch, the layers aside)
    assert res["patched"] > 1500                                                  # (and it really patched: it did not just fall back every time)
    assert res["kept"] > 0.9 * res["keptTotal"]                                   # (a row whose HTML did not change is the very same node afterwards)
    assert res["tamper"] > 300 and res["tamperLocal"] > 300                       # (and when the page had changed its own nodes in between, only those nodes were made again and the page is still right)
    assert res["swap"] > 200 and not res.get("swapGaveUp")                        # (a node the page replaced and told the draw about is patched, nothing is rebuilt)
