"""The row of a tunnel the Doctor is repairing opens a window (the owner, 2026-10-24, with a picture of a row "repairing…" that had no menu: "its card does not open at all").

While the Doctor builds a tunnel again its row is a placeholder (no id: `rebuilding_rows` of the panel, or the old row kept by `tunGhosts`). It had no click, no menu and no window. Now the click opens
`tunRepairCard`: what the Doctor is doing with the tunnel, live (the story of its running repair), held on the screen while the list behind is drawn again, closed only by its Close button.
(That a repair does not take the tunnel off when a server is off - which is what made this row last 17 minutes - is tests/test_doctor_server_off.py.)
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")
CARD = APP[APP.index("/* a tunnel the Doctor took off its servers"): APP.index("window.apFixAll = ")]


def test_every_row_of_the_list_opens_something_and_a_placeholder_opens_the_repair_window():
    page = APP[APP.index("PAGES.tunnelsList = async"): APP.index("function tunGrid(t)")]
    row = page[page.index("const row = t =>"): page.index("const creating = false;")]
    assert 'return `<tr class="${t.state} ${tunSev(t)} clk" ${t.id < 0 ? `onclick="tunRepairCard(\'${esc(t.name)}\')"' in row                  # (a placeholder is clickable too - it was the only row that was not)
    assert 'title="The Tunnel Doctor is working on this tunnel - click to see what it is doing" tabindex="0"`' in row                              # (reachable by the keyboard like the others: tests/test_qa_tunnel_pages.py)
    assert '`onclick="toggleDetail(${t.id})" title="Click for the details" tabindex="0"`' in row                                                    # (a real row opens its window as before)
    assert 'class="busy-chip" title="the tunnel is being rebuilt"' in row                                                                           # (its spinner is still in the place of the button)


@pytest.fixture()
def run(tmp_path):
    node = shutil.which("node")

    if node is None:
        pytest.skip("node is not installed here")

    def go(script):
        driver = tmp_path / "card.js"
        driver.write_text(
            "globalThis.window = globalThis;\n"
            "const esc = s => String(s).replace(/[&<>\"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', \"'\": '&#39;'}[c]));\n"
            "const state = {keepWin: null, open: 7, tunnelsCache: [{id: -1, name: 'fix-tun', iran_name: 'ir-a', foreign_name: 'fo-b'}], tunRowCache: new Map([['ghost-tun', {id: 4, name: 'ghost-tun', iran_name: 'ir-c', foreign_name: 'fo-d'}]])};\n"
            "const shown = [], followed = [], box = {innerHTML: ''}; let acts = [];\n"
            "const showModal = (title, body, cls) => shown.push({title, body, cls});\n"
            "const $ = sel => sel === '#ap-story' ? box : null;\n"
            "const storyHtml = a => 'STORY:' + a.id + ':' + (a.done ? 'done' : 'running');\n"
            "const followStory = (id) => { followed.push(id); return Promise.resolve(); };\n"
            "const refresh = () => {};\n"
            "const api = async url => ({actions: acts});\n"
            + CARD + "\n(async () => { const res = {};\n" + script + "\nprocess.stdout.write(JSON.stringify(res)); })().catch(e => { console.error(e); process.exit(1); });\n", encoding="utf-8")
        r = subprocess.run([node, str(driver)], capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        assert r.returncode == 0, r.stderr[-800:]
        return json.loads(r.stdout)

    return go


def test_the_window_shows_the_running_repair_of_that_tunnel_and_stays_open(run):
    res = run(
        "acts = [{id: 3, ref: 'other-tun', action: 'autopilot', done: false}, {id: 9, ref: 'fix-tun', action: 'autopilot', done: true}, {id: 12, ref: 'fix-tun', action: 'autopilot', done: false}];\n"
        "await window.tunRepairCard('fix-tun');\n"
        "res.state = [state.keepWin, state.open]; res.win = shown[0]; res.box = box.innerHTML; res.followed = [...followed];\n"
        "acts = [{id: 9, ref: 'fix-tun', action: 'autopilot', done: true}, {id: 8, ref: 'fix-tun', action: 'fix', done: true}]; followed.length = 0;\n"
        "await window.tunRepairCard('fix-tun'); res.last = box.innerHTML; res.followed2 = [...followed];\n"
        "acts = []; await window.tunRepairCard('fix-tun'); res.none = box.innerHTML;\n"
        "await window.tunRepairCard('ghost-tun'); res.ghost = shown[shown.length - 1].title;\n"
        "await window.tunRepairCard('x<b>'); res.safe = shown[shown.length - 1].title;\n")
    assert res["state"] == [True, None]                                                                         # (held: the list behind is drawn again every few seconds while the Doctor works; no tunnel window is "open")
    assert res["win"]["cls"] == "wide keep repaircard" and "fix-tun" in res["win"]["title"] and "ir-a ⇄ fo-b" in res["win"]["title"]
    assert "Being repaired" in res["win"]["body"] and 'id="ap-story"' in res["win"]["body"] and "puts the tunnel back in the list as down" in res["win"]["body"]
    assert res["box"] == "STORY:12:running" and res["followed"] == [12]                                         # (the running repair of THIS tunnel, followed live - not another tunnel's, not an old one)
    assert res["last"] == "STORY:9:done" and res["followed2"] == []                                             # (nothing running: the last one, not followed)
    assert "Nothing to show yet" in res["none"] and "ir-c ⇄ fo-d" in res["ghost"]                               # (a placeholder drawn from the old row of the tunnel knows its servers)
    assert "x<b>" not in res["safe"] and "x&lt;b&gt;" in res["safe"]                                                  # (a name is never put in the page as it is)


def test_the_doctors_own_live_window_is_unchanged(run):
    res = run(
        "acts = [{id: 5, ref: 'fix-tun', action: 'autopilot', done: true}];\n"
        "await window.apLive(21, 'fix-tun'); res.win = shown[0]; res.box = box.innerHTML; res.state = [state.keepWin, state.open];\n")
    assert 'onclick="apDiagnose(21)"' in res["win"]["body"] and res["win"]["cls"] == "wide" and res["box"] == "STORY:5:done" and res["state"] == [None, 7]          # (the Doctor page's window: its button, not held)


def test_the_window_that_only_shows_the_repair_closes_where_a_working_window_is_put_in_the_bar(run):
    """`closeOrDock` puts a window that still has a running line into the bar (it keeps working there). The repair window works on nothing - closing it loses nothing and the row opens it again."""
    line = APP[APP.index("window.closeOrDock = "): APP.index("document.addEventListener(\"keydown\", e => { if (e.key === \"Escape\"")]
    assert '!ov.querySelector(".modal.repaircard")' in line
    res = run(
        "const calls = []; const minModal = () => calls.push('min'), toast = t => calls.push('toast'), closeModal = () => calls.push('close');\n"
        "const mk = (running, repair) => ({firstElementChild: {querySelector: sel => sel === '.fx-line.run' ? running : sel === '.modal.repaircard' ? repair : null}});\n"
        "let modalEl = mk(true, false); const $m = () => modalEl; const $ = s => s === '#modal' ? $m() : null;\n"
        + line.replace("window.closeOrDock", "const closeOrDock")
        + "\ncloseOrDock(); res.working = calls.splice(0);\n"
        "modalEl = mk(true, true); closeOrDock(); res.repair = calls.splice(0);\n"
        "modalEl = mk(false, false); closeOrDock(); res.idle = calls.splice(0);\n")
    assert res == {"working": ["min", "toast"], "repair": ["close"], "idle": ["close"]}