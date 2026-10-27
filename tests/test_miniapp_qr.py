"""The QR buttons of the link cards of the Mini App (the owner, 2026-10-27, translated from Persian, with screenshots: "remove the copy button and put the QR in its place - put it for the helper link too" / "instead of this
picture a real QR [icon]" / "make this one like the one above and move it into the row of its name").

The card of the subscription link has a "QR" button with a real QR icon where the "copy" button was (a tap on the link itself still copies it); the card of the helper links has the same button in the row of its name (one
button for all its links, one QR for each in the sheet); on the service page and on the page after a purchase; the QR opens in a sheet with the link under it. The behaviour runs in tests/miniapp_driver.js (mode "qr":
the real app.js in a bare vm with a fake document, a fake api and a fake QR library)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "bot" / "miniapp"
DRIVER = ROOT / "tests" / "miniapp_driver.js"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_link_cards_have_qr_buttons_where_the_copy_button_was():
    r = subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), "qr"], capture_output=True, text=True, check=True, timeout=90)
    out = json.loads(r.stdout)
    assert out["errors"] == [] and "crash" not in out, out
    res = out["values"]["qr"]
    assert all(res.values()), {k: v for k, v in res.items() if not v}      # (every answer of the mode is True)
    assert len(res) >= 16


def test_the_old_inline_qr_and_the_grid_character_are_gone():
    js = (MINI / "app.js").read_text(encoding="utf-8")
    assert "function showQr(" not in js and 'id="qr"' not in js and "showQr(" not in js and "qrSheet(" not in js      # (one way to show the QR of a link, not three)
    assert "▦" not in js      # (the grid character looked like a table: every QR button has the picture)
    assert js.count("function qrOf(") == 1 and js.count("function qrMany(") == 1 and js.count("const QR_ICON = ") == 1
    assert js.count('onclick="qrOf(') == 2      # (the main link of the service page and of the page after a purchase)
    assert js.count('onclick="qrMany(') == 1      # (the heading row of the helper links' card, used by both pages)
    assert "📋 کپی</button></h3>" not in js      # (no copy button in a card header any more)
