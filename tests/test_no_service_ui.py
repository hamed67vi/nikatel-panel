"""What the screen says about a tunnel that is up but leads nowhere (the owner, 2026-10-25: "connected but no service"): a small amber tag in the list, the row among the problems, a box in the tunnel window with the
button that changes the ports - and nothing for a tunnel that is down (its own words win) or whose look is not confirmed yet. The real functions of app.js run in node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
HELPERS = APP[APP.index("const noSvcText = "): APP.index("/* the ORDER of the tunnel list")]
BANNER = APP[APP.index("function tunBanner(t) {"): APP.index("/* two boxes under the grid")]
LIST = APP[APP.index("PAGES.tunnelsList = async"): APP.index("function tunGrid(t)")]


@pytest.fixture()
def run(tmp_path):
    node = shutil.which("node")

    if node is None:
        pytest.skip("node is not installed here")

    def go(script):
        driver = tmp_path / "nosvc.js"
        driver.write_text(
            "globalThis.window = globalThis;\n"
            "const esc = s => String(s).replace(/[&<>\"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', \"'\": '&#39;'}[c]));\n"
            "const SHOW_LOSS = 20, BW_STALL = 1; const tunLoss = t => ({v: null, blocked: false}); const bwHurts = () => false;\n"
            "const tunOffServers = () => ''; const outWhy = e => e; const durText = s => Math.round(s) + ' s'; const bwWhy = () => '';\n"
            + HELPERS + "\n" + BANNER + "\n(() => { const res = {};\n" + script + "\nprocess.stdout.write(JSON.stringify(res)); })();\n", encoding="utf-8")
        r = subprocess.run([node, str(driver)], capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        assert r.returncode == 0, r.stderr[-800:]
        return json.loads(r.stdout)

    return go


T = "{id: 7, name: 'us-tun1', type: 'backpack', state: 'up', foreign_name: 'US', forwarded_ports: [8079], record: {port_mapping: '8079:8075'}, last_error: '', detail: {no_service: {ports: [8075], all: true, confirmed: true, first: Date.now() / 1000 - 300}}}"


def test_a_confirmed_no_service_lifts_the_row_among_the_problems_but_not_a_cut_one(run):
    res = run(
        f"const t = {T};\n"
        "res.sev = tunSev(t); res.on = !!tunNoSvc(t);\n"
        "res.unconfirmed = tunSev({...t, detail: {no_service: {ports: [8075], confirmed: false}}}); res.none = tunSev({...t, detail: {no_service: null}}); res.nodetail = tunSev({...t, detail: undefined});\n"
        "res.down = [tunSev({...t, state: 'down'}), !!tunNoSvc({...t, state: 'down'})]; res.degraded = !!tunNoSvc({...t, state: 'degraded'});\n")
    assert res["sev"] == "sev-yellow" and res["on"] is True                                                    # (counted by the Problems filter, sorted up with the other yellow rows)
    assert res["unconfirmed"] == "" and res["none"] == "" and res["nodetail"] == ""                            # (one look is not a verdict; a tunnel without the flag is as it was)
    assert res["down"] == ["sev-red", False]                                                                   # (a tunnel that is down is red and says why it is down)
    assert res["degraded"] is True


def test_the_window_says_connected_but_no_service_and_offers_the_ports_dialog(run):
    res = run(
        f"const t = {T};\n"
        "window.editPorts = () => {};\n"
        "res.box = tunBanner(t);\n"
        "res.some = tunBanner({...t, detail: {no_service: {ports: [443], all: false, confirmed: true}}});\n"
        "res.gre = tunBanner({...t, type: 'gre'});\n"
        "res.healthy = tunBanner({...t, detail: {}});\n"
        "res.down = tunBanner({...t, state: 'down', last_error: 'the Iran server does not answer'});\n")
    box = res["box"]
    assert 'data-b="nosvc"' in box and "Connected, but no service" in box and "Nothing listens on port 8075 of US - add the inbound there or change the port" in box
    assert "port 8075 of the foreign server US" in box and "nobody answers behind it" in box                         # (the whole sentence is the tip of the line)
    assert re.search(r"seen for 30[01] s</span>", box)                                                               # (how long, in the words of the other boxes)
    assert "editPorts(7, '8079:8075')" in box and "Health check" in box                                              # (the dialog opens with the map as it is: change 8079:8075 into what the foreign server really has)
    assert "(its other ports answer)" in res["some"] and "its other ports" not in box
    assert "editPorts" not in res["gre"] and "Connected, but no service" in res["gre"]                               # (a type whose ports cannot be changed here gets no button that cannot work)
    assert "Healthy" in res["healthy"] and "no service" not in res["healthy"]
    assert "Needs attention" in res["down"] and "no service" not in res["down"]


def test_the_list_shows_one_amber_tag_for_it():
    cell = LIST[LIST.index("const bwCell = t =>"): LIST.index("/* the Ping / Loss cell")]
    assert '<span class="cut-tag svc" title="${esc(noSvcText(t, ns))}">no service</span>' in cell and "if (ns) return nsTag + bwMeterFor(t) + why;\n    return bwMeterFor(t) + why;" in cell
    assert "const ns = tunNoSvc(t)" in cell
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert ".tun-tbl .cut-tag.svc" in css and 'html[data-mode="light"] body #page .tun-tbl .cut-tag.svc' in css
