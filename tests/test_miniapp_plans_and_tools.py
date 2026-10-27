"""The plan cards of the Buy page and the manage card of the service page of the Mini App (the owner, 2026-10-27, translated from Persian, with screenshots: "make these boxes much more elegant and professional" /
"add update-subscription here; show disable and delete only to the agent" / "a prettier emoji for buying").

Both run in tests/miniapp_driver.js (modes "plans" and "svctools": the real app.js in a bare vm with a fake document and a fake api); the default mode of the driver already checks that every tag parses and every inline
handler is JavaScript and calls a function the app has. The api side of the same rules (who gets which tool, the stage, the limits) is the attack harness (tests/miniapp_security: SV1, SV2, SV4, SV5, AG1)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "bot" / "miniapp"
DRIVER = ROOT / "tests" / "miniapp_driver.js"
NODE = shutil.which("node")
BAG = "\U0001F6CD"      # (the shopping bags: Windows draws it as a small black picture)


def _mode(mode):
    r = subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), mode], capture_output=True, text=True, check=True, timeout=90)
    out = json.loads(r.stdout)
    assert out["errors"] == [] and "crash" not in out, out
    res = out["values"][mode]
    assert all(res.values()), {k: v for k, v in res.items() if not v}      # (every answer of the mode is True)
    return res


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_plan_cards_have_a_name_chips_a_short_list_and_a_price_with_a_buy_button():
    assert len(_mode("plans")) >= 14


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_manage_card_has_update_for_the_stage_and_disable_and_delete_for_agents_only():
    assert len(_mode("svctools")) >= 10


def test_the_plan_card_styles_and_the_buy_icon():
    css = (MINI / "app.css").read_text(encoding="utf-8")
    for rule in (".plan::before", ".plan .pc span", ".plan .pf.clamp:not(.open)", ".plan .pb", ".plan .pgo", ".plan.off .pgo"):
        assert rule in css, rule
    for old in (".plan .pr ", ".plan .pd "):
        assert old not in css, old      # (the old two-column card is gone)
    assert ".qri" in css      # (the QR icon of the link buttons)
    js = (MINI / "app.js").read_text(encoding="utf-8")
    html = (MINI / "index.html").read_text(encoding="utf-8")
    assert BAG not in js and BAG not in html      # (every "buy" icon of the page is the cart)
    assert "\U0001F6D2" in js and "\U0001F6D2" in html
    assert "r.text" not in js.split("AFTER.referral")[1].split("VIEWS.wheel")[0]      # (the invite page shows the link once - the banner text with the same link under it is gone)
