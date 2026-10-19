"""The Mini App of the shop bot (the owner, 2026-10-20, three pictures from a phone): the label of "Pay ... from the wallet" was cut on both sides, a wallet address came out of its card on the
left, and the invite page drew a raw {"type":"text","text":"\u0644..."} record as if it were words."""
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

MINI = Path(__file__).resolve().parents[1] / "bot" / "miniapp"


def test_a_row_of_buttons_never_cuts_a_label():
    css = (MINI / "app.css").read_text(encoding="utf-8")
    assert ".btns { display: flex; flex-wrap: wrap;" in css
    row = next(ln for ln in css.splitlines() if ln.startswith(".btns .btn {"))
    assert "white-space: normal" in row and "height: auto" in row and "min-width: 0" in row      # (the label goes on a second line, the button grows; the base .btn is nowrap with a fixed height)
    assert ".btns .btn.pri { flex: 2 1 60%; }" in css


def test_a_long_value_wraps_inside_its_row_and_the_copy_button_keeps_its_place():
    css = (MINI / "app.css").read_text(encoding="utf-8")
    assert ".copyrow > div { flex: 1; min-width: 0; } .copyrow > .btn { flex: none; }" in css
    assert ".copyrow b { display: block; overflow-wrap: anywhere; word-break: break-all; }" in css
    assert ".note code, .note pre, .note kbd { white-space: pre-wrap;" in css and ".note { overflow-wrap: anywhere; }" in css


def test_the_description_of_a_plan_keeps_its_lines_and_the_files_have_a_new_version_for_the_cache():
    js, html = (MINI / "app.js").read_text(encoding="utf-8"), (MINI / "index.html").read_text(encoding="utf-8")
    assert '<div class="note descr mt">${esc(x.descr)}</div>' in js and ".note.descr { white-space: pre-line; }" in (MINI / "app.css").read_text(encoding="utf-8")
    assert "app.css?v=3.0.2" in html and "app.js?v=3.0.2" in html


@pytest.mark.skipif(shutil.which("php") is None, reason="php is not installed here")
def test_the_invite_text_is_words_or_nothing(tmp_path):
    src = (MINI / "api.php").read_text(encoding="utf-8")
    start = src.index("function bannerText(")
    fn = src[start:src.index("\n}\n", start) + 3]
    script = tmp_path / "t.php"
    script.write_text("<?php\n" + fn + textwrap.dedent(r"""
        echo json_encode([
            bannerText('{"type":"text","text":"\u0644\u06cc\u0646\u06a9 LINK"}'),
            bannerText('plain words'),
            bannerText('{"broken \\u0634\\u0645\\u0627'),
            bannerText('{"type":"photo"}'),
            bannerText(null),
        ], JSON_UNESCAPED_UNICODE);
        """), encoding="utf-8")
    out = subprocess.run(["php", str(script)], capture_output=True, text=True, check=True).stdout
    assert out == '["لینک LINK","plain words","","",""]'


def test_the_api_puts_the_invite_link_where_the_text_says_LINK():
    src = (MINI / "api.php").read_text(encoding="utf-8")
    assert "safeHtml(str_replace('LINK', 'https://t.me/' . ($botUsername ?? '') . '?start=' . $uid, bannerText(settingValue('INVITE_BANNER_TEXT'))))" in src


def test_the_new_column_texts_are_in_persian():
    ui2 = (Path(__file__).resolve().parents[1] / "nikatel" / "web" / "i18n.fa.ui2.js").read_text(encoding="utf-8")
    assert '"Bandwidth Mbit/s": "پهنای باند Mbit/s"' in ui2 and "The line of the server in megabits per second:" in ui2
    assert "The speed of the server's line: its network card reports" in ui2 and "The network card does not report its speed" in ui2