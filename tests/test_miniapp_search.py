"""The small search button of "my services" in the Mini App (the owner, 2026-10-27, translated from Persian, with a screenshot of the "my services" card: "put a small search button up there too").

A 🔍 beside the count in the header of the home card and of the services page; typing looks through ALL the customer's services (name and server), active or not, and tells Persian / Arabic digits and letters
apart from nothing; the home card shows eight and a button for the rest, the services page shows them all; the typed words survive "open a service and come back" and start empty on a tab switch.
The behaviour runs in tests/miniapp_driver.js (mode "search": the real app.js in a bare vm with a fake document and a fake api); the other modes of the driver already check every tag and every inline handler."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "bot" / "miniapp"
DRIVER = ROOT / "tests" / "miniapp_driver.js"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_search_of_my_services_finds_filters_and_keeps_its_words():
    r = subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), "search"], capture_output=True, text=True, check=True, timeout=90)
    out = json.loads(r.stdout)
    assert out["errors"] == [] and "crash" not in out, out
    res = out["values"]["search"]
    assert all(res.values()), {k: v for k, v in res.items() if not v}      # (every answer of the mode is True)
    assert len(res) >= 18


def test_the_button_is_small_and_sits_beside_the_count_of_both_headers():
    js = (MINI / "app.js").read_text(encoding="utf-8")
    home = js[js.index('<h3>سرویس‌های من <span style="display:flex'):]
    home = home[:home.index("</h3>")]
    assert 'class="pill"' in home and 'onclick="homeSearch()"' in home and 'id="h-count"' in home and "🔍" in home      # (the same small round pill as the count, no new style)
    assert home.index("homeSearch()") < home.index('id="h-count"')      # (the count stays on the outer edge, the button next to it)
    page = js[js.index("VIEWS.services = async"):]
    page = page[:page.index("let svcMode")]
    assert 'onclick="svcSearch()"' in page and 'id="s-count"' in page and 'value="${esc(q)}"' in page      # (what he typed is escaped)
    assert 'hidden' in page and 'id="h-sbox" hidden' in js      # (both boxes start closed)
    html = (MINI / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"\?v=(\d+\.\d+\.\d+)", html) == ["3.0.21", "3.0.21"]      # (a new Mini App: Telegram must not keep the old files)
