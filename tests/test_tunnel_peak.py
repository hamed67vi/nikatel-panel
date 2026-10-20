"""The "Real use by the users" card of the Monitor tab of a tunnel (1.69.47): the peak and when, the busiest hour of the day, the part of the capacity used at the peak - computed in the page from the checks the tab already reads. The two functions run through node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _run(body: str):
    start, end = APP.index("function tunPeak(ts, rx, tx) {"), APP.index("async function loadTunnelCharts(id) {")
    prog = ("const avgOf = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null; const outWhen = ts => 'T' + ts;\n"
            "const mtile = (l, b, s, c = '') => `<div class=\"mtile ${c}\"><small>${l}</small><b>${b}</b><span>${s}</span></div>`;\n" + APP[start:end] + "\n" + body)
    out = subprocess.run([NODE, "-e", prog], check=False, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_peak_and_the_busiest_hour_come_from_the_readings():
    r = _run("""
      // 2 days of one reading per 10 minutes: 30 Mb/s down at 21:00 local time, 4 at the other hours; one single reading of 90 at 03:30
      const ts = [], rx = [], tx = []; const base = new Date(2026, 9, 18, 0, 0, 0).getTime() / 1000;
      for (let i = 0; i < 2 * 144; i++) { const t = base + i * 600, h = new Date(t * 1000).getHours(); ts.push(t); rx.push(h === 21 ? 30 : 4); tx.push(1); }
      rx[21] = 90; rx[130] = null;
      const u = tunPeak(ts, rx, tx);
      console.log(JSON.stringify({peak: u.down.peak, at: u.down.at - base, avg: Math.round(u.down.avg * 100) / 100, n: u.down.n, up: u.up.peak, busy: u.busy.h, h3: u.hours[3].peak, h21: u.hours[21].avg, span: u.span}));
    """)
    assert r["peak"] == 90 and r["at"] == 21 * 600 and r["n"] == 287 and r["up"] == 1 and r["busy"] == 21 and r["h3"] == 90 and r["h21"] == 30
    assert r["span"] == (2 * 144 - 1) * 600 and r["avg"] < 8                                                  # one spike of 90 does not make the average, the busy hour is found by its average (30), not by the spike (03:00)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_card_says_what_is_missing_and_warns_near_the_capacity():
    r = _run("""
      const empty = tunPeakHtml(tunPeak([], [], []), 50);
      const ts = [], rx = [], tx = []; for (let i = 0; i < 300; i++) { ts.push(1760000000 + i * 60); rx.push(i === 100 ? 45 : 5); tx.push(null); }
      const short = tunPeakHtml(tunPeak(ts, rx, tx), 50);                                                    // 5 hours of readings: no bars yet
      const ts2 = [], rx2 = []; for (let i = 0; i < 1500; i++) { ts2.push(1760000000 + i * 60); rx2.push(i === 100 ? 45 : 5); }
      const long = tunPeakHtml(tunPeak(ts2, rx2, rx2.map(() => 1)), 50), nocap = tunPeakHtml(tunPeak(ts2, rx2, rx2), null);
      console.log(JSON.stringify({empty: empty.includes("not enough readings"), short: short.includes("Choose 24 h or 7 days") && !short.includes("pk-bars") && short.includes("90 %"), warn: short.includes("mtile warn"),
        bars: (long.match(/<i class=/g) || []).length, nocap: nocap.includes("no capacity test yet") && !nocap.includes("mtile warn"), peak: long.includes("45 Mb/s")}));
    """)
    assert r == {"empty": True, "short": True, "warn": True, "bars": 24, "nocap": True, "peak": True}


def test_the_card_is_in_the_monitor_tab_before_the_timeline_with_its_style_and_words():
    web = Path(__file__).resolve().parent.parent / "nikatel" / "web"
    css, fa = (web / "lab.css").read_text(encoding="utf-8"), (web / "i18n.fa.ui2.js").read_text(encoding="utf-8")
    assert APP.index("${tunPeakHtml(tunPeak(ts, col(\"rx_mbps\"), col(\"tx_mbps\"))") < APP.index('<div class="mcard mwide"><h6>Up / down timeline</h6>')
    assert ".mcard.pk .pk-bars { display: grid; grid-template-columns: repeat(24" in css and re.search(r'"Real use by the users": "', fa) and '"Busiest hour": "' in fa