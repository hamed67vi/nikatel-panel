"""The bandwidth meter of the tunnel list is drawn for every tunnel that runs, with the TOTAL on the left and the users' use beside it, and it is live (the owner, 2026-10-21, a picture of the list where only the tunnels with
a problem had it, and some of those showed a dash for the users: "show the bar for the others too", "the total bandwidth on the left", "the users' use must be shown, it was to be live").

What is tested: the cell draws the meter for a tunnel without a problem (no word), a word and a colour for a drop, a very slow or a nearly full tunnel, the total comes first, the list asks the live reader and paints the
meters in place, and the CSS."""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"


def test_every_tunnel_that_runs_has_the_meter_and_only_a_problem_has_a_word():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    cell = js.split("const bwCell = t => {", 1)[1].split("/* the Ping / Loss cell", 1)[0]
    assert "return bwMeterFor(t) + why;" in cell and 'if (!["up", "degraded"].includes(t.state)) return `<span class="cut-tag' in cell                       # (a cut tunnel: still its tag, no meter)
    fn = js.split("const bwMeterFor = t => {", 1)[1].split("const bwTone", 1)[0]
    assert 'prob ? (stall || b.state === "drop" ? "drop" : "slow") : full ? "full" : "ok"' in fn and 'prob ? (stall ? "no data" : b.state === "drop" ? "dropped" : "very slow") : full ? "nearly full" : idle ? "no users" : ""' in fn


def test_the_total_is_on_the_left_and_the_users_use_beside_it():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    meter = js.split("const bwMeter = (t, tone, word, tip) => {", 1)[1].split("const bwMeterFor", 1)[0]
    assert meter.index("${cap != null ? bwNum(cap) : \"—\"}") < meter.index("↓ ${u ? bwNum(u.down) : \"—\"}") and 'data-k="${esc(k)}"' in meter


def test_the_list_is_live_while_it_is_open():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "document.querySelector('[id^=\"tun-use-\"], .tun-tbl')" in js.split("const tunLiveTick = async () => {", 1)[1].split("};", 1)[0]
    paint = js.split("const tunLivePaint = () => {", 1)[1].split("const tunLiveStop", 1)[0]
    assert '.tun-tbl td.bwc[data-tid]' in paint and "bwMeterFor(t)" in paint and "nm.dataset.k !== m.dataset.k) m.replaceWith(nm)" in paint            # (only a meter whose numbers changed is replaced)
    assert 'setInterval(() => { if (state.page === "tunnels" && !document.hidden && !tunLiveT && document.querySelector(".tun-tbl")) tunLiveStart(); }, 2000);' in js


def test_the_css_hides_an_empty_word_and_styles_the_users_number():
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "html body #page .tun-tbl .bwm-t small:empty { display: none; }" in css and "html body #page .tun-tbl .bwm-t .bwm-u {" in css
