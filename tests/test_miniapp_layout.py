"""The Mini App of the shop bot (the owner, 2026-10-20, three pictures from a phone): the label of "Pay ... from the wallet" was cut on both sides, a wallet address came out of its card on the
left, and the invite page drew a raw {"type":"text","text":"\u0644..."} record as if it were words.
The walk of the whole app (every tab, view and bottom sheet at 320-800 px, dark and light, with names and values far longer than real ones) found more, kept here:
the copy / QR / share / open-link buttons never worked (the quotes of the JSON ended the attribute), a long name pushed the wallet off the screen, buttons cut their labels at 320 px,
and api.php could pay a gift code or a wheel prize twice."""
import hashlib
import hmac
import json
import shutil
import subprocess
import textwrap
import time
import urllib.parse
from pathlib import Path

import pytest

MINI = Path(__file__).resolve().parents[1] / "bot" / "miniapp"
NODE, PHP = shutil.which("node"), shutil.which("php")
DRIVER = Path(__file__).with_name("miniapp_driver.js")


def _css() -> str:
    return (MINI / "app.css").read_text(encoding="utf-8")


def _drive(mode: str = "") -> dict:
    """app.js in a bare node vm with a fake page and a fake api: every view and sheet drawn with awkward data (see miniapp_driver.js)"""
    out = subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), mode], capture_output=True, text=True, timeout=90, check=True).stdout
    return json.loads(out)


def _php_function(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    return src[start:src.index("\n}\n", start) + 3]


def _php(tmp_path, body: str, *args: str) -> str:
    script = tmp_path / "t.php"
    script.write_text("<?php\n" + body, encoding="utf-8")
    return subprocess.run([PHP, str(script), *args], capture_output=True, text=True, check=True).stdout


def test_a_row_of_buttons_never_cuts_a_label():
    css = _css()
    assert ".btns { display: flex; flex-wrap: wrap;" in css
    row = next(ln for ln in css.splitlines() if ln.startswith(".btns .btn {"))
    assert "white-space: normal" in row and "height: auto" in row and "min-width: 0" in row      # (the label goes on a second line, the button grows; the base .btn is nowrap with a fixed height)
    assert ".btns .btn.pri { flex: 2 1 60%; }" in css


def test_the_buttons_of_the_home_card_and_of_a_service_wrap_their_label_too_on_a_320_px_phone():
    """"💳 شارژ کیف پول" (124 px of text in a 112 px button) and "🛠 گزارش اختلال" were cut by the edge of their button"""
    css = _css()
    rule = next(ln for ln in css.splitlines() if ln.startswith(".hero .hbtns .btn, .grid > .btn {"))
    assert "white-space: normal" in rule and "height: auto" in rule and "min-height: 44px" in rule and "min-width: 0" in rule


def test_a_long_value_wraps_inside_its_row_and_the_copy_button_keeps_its_place():
    css = _css()
    assert ".copyrow > div { flex: 1; min-width: 0; } .copyrow > .btn { flex: none; }" in css
    assert ".copyrow b { display: block; overflow-wrap: anywhere; word-break: break-all; }" in css
    assert ".note code, .note pre, .note kbd { white-space: pre-wrap;" in css and ".note { overflow-wrap: anywhere; }" in css


def test_a_word_with_no_space_breaks_at_the_edge_of_its_box_instead_of_widening_the_page():
    """a 64-character name, a plan title or a remark without spaces made the page 635-733 px wide on a 320-360 px phone"""
    css = _css()
    assert ".page, .sheet { overflow-wrap: anywhere; }" in css
    assert ".list .item > .amt { flex: none; white-space: nowrap; }" in css and ".list .item.static .tx .row { flex-wrap: wrap;" in css      # (an amount keeps its width, the state pill goes under the title in a narrow row)


def test_a_long_name_never_pushes_the_wallet_out_of_the_header():
    css = _css()
    assert ".who > div:not(.ava) { min-width: 0; }" in css
    name = next(ln for ln in css.splitlines() if ln.startswith(".name {"))
    assert "overflow-wrap: anywhere" in name and "-webkit-line-clamp: 2" in name and "overflow: hidden" in name
    assert ".wal { flex: none; display: grid;" in css


def test_every_tap_target_is_at_least_40_px_high():
    css = _css()
    assert ".btn.sm { height: 40px;" in css and ".seg button { flex: 0 0 auto; height: 40px;" in css and "min-height: 40px; border-radius: 14px; word-break: break-all;" in css


def test_the_qr_is_shown_with_whole_pixels_per_square_and_a_phone_sheet_follows_the_visible_height():
    css, js = _css(), (MINI / "app.js").read_text(encoding="utf-8")
    assert ".qr img { display: block; max-width: 100%; height: auto; image-rendering: pixelated; }" in css      # (it was 190 px whatever the picture: squares of 4.2 px blurred into each other)
    assert "createDataURL(cell, 0)" in js and "Math.floor(216 / q.getModuleCount())" in js
    assert "max-height: 88vh; max-height: 88dvh;" in css and "border-radius: 22px; background: color-mix(in srgb, var(--bg2) 92%, transparent)" in css      # (the toast is no longer a pill: three lines of text were cut by its round ends)


def test_the_light_theme_has_readable_status_colours_and_the_wheel_lays_many_amounts_along_their_spokes():
    css, js = _css(), (MINI / "app.js").read_text(encoding="utf-8")
    assert "  --ok: #0a7a55; --bad: #c82c50; --warn: #8f5d0d; --info: #0b7592;" in css      # (4.7 - 5.5 on the white glass; the old #0f9f6e / #b7791f / #0e8fb3 were 3.3 - 3.7 for 11 px bold text)
    assert css.count("text-shadow: 0 1px 2px rgba(8,20,70,.4)") == 3      # (the white label of a gradient button, the chosen chip and the price tag)
    assert "n > 8 ? `rotate(${a}deg) translate(74px) translate(-50%,-50%)`" in js      # (with 12 segments "2,500,000" ran into "100,000")


def test_the_description_of_a_plan_keeps_its_lines_and_the_files_have_a_new_version_for_the_cache():
    js, html = (MINI / "app.js").read_text(encoding="utf-8"), (MINI / "index.html").read_text(encoding="utf-8")
    assert '<div class="note descr mt">${esc(x.descr)}</div>' in js and ".note.descr { white-space: pre-line; }" in _css()
    assert "app.css?v=3.0.11" in html and "app.js?v=3.0.11" in html


def test_the_font_never_holds_the_first_paint_back():
    """the Google Fonts stylesheet was a render-blocking link: where Google is slow or filtered the customer stared at nothing until it timed out"""
    html = (MINI / "index.html").read_text(encoding="utf-8")
    line = next(ln for ln in html.splitlines() if ln.startswith('<link href="https://fonts.googleapis.com/css2'))
    assert 'media="print" onload="this.media=\'all\'"' in line and "display=swap" in line
    assert '<noscript><link href="https://fonts.googleapis.com/css2' in html


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_every_tag_and_every_inline_handler_of_every_view_and_sheet_is_valid():
    """the copy / QR / share / app link / open ticket buttons were onclick="copy("https://...")": the quote of the JSON ended the attribute, the handler became `copy(` and a tap did nothing"""
    res = _drive()
    assert "crash" not in res, res
    assert res["errors"] == []
    v = res["values"]
    assert v["handlers"] > 80
    assert any(h.startswith('copy("https://h.example/sub/') for h in v["copyHandlers"]) and any(h.startswith('copy("6037997512345678"') for h in v["copyHandlers"])      # the subscription and the card number
    assert any("\\nvmess://zzz" in h for h in v["copyHandlers"])      # "copy all": the new line is the JSON escape inside the quotes of a string


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_the_numbers_dates_and_names_of_the_app_read_right():
    v = _drive()["values"]
    assert v["avatar"] == "🔥"                                                       # (charAt(0) cut the emoji at the start of a name in half: a broken character)
    assert v["left"] == {"expired": "منقضی", "three": "۳ روز", "none": "نامحدود"}   # (a service that ended an hour ago read "today")
    assert v["leftTx"] == {"hold": "از اولین اتصال", "days": "۳ روز مانده", "expired": "منقضی", "none": "نامحدود"}      # (the list wrote "expired remaining" and "unlimited remaining")
    assert v["GB"] == {"a": "۸٫۵", "b": "۵۰", "c": "۱٬۲۳۵"}                       # (the volume was in Latin digits between Persian ones: 8.50 / 50)
    assert v["vol"] == {"zero": "نامحدود", "g": "۵۰ گیگ", "t": "۲ ترابایت"}          # (volume 0 is unlimited in the bot, not "0 GB")
    assert v["ltr"] == '<bdi dir="ltr">@sup_port</bdi>'                           # (a leading @ jumped to the other end of the name inside Persian text)
    assert v["js"] == "&quot;a\\&quot;b&#39;c&quot;"
    assert v["min_topup"] == 20000                                                # (api.php sends min_topup; the app kept its own 10000)
    assert v["hist"] == ['<b class="ltr amt">+۱۰۰</b>', '<b class="ltr amt mut">۲۰۰</b>', '<b class="ltr amt">−۳۰۰</b>', '<b class="ltr amt mut">۴۰۰</b>', '<b class="ltr amt">+۵۰</b>']      # (a refused top-up showed "+200" as if the wallet had grown)


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_a_tab_before_the_server_answered_tries_to_connect_again_and_never_draws_a_half_empty_page():
    v = _drive("noconnect")["values"]["noconnect"]
    assert v == {"me": 2, "history": 0}


def test_the_api_puts_the_invite_link_where_the_text_says_LINK():
    src = (MINI / "api.php").read_text(encoding="utf-8")
    assert "safeHtml(str_replace('LINK', 'https://t.me/' . ($botUsername ?? '') . '?start=' . $uid, bannerText(settingValue('INVITE_BANNER_TEXT'))))" in src


@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_the_invite_text_is_words_or_nothing(tmp_path):
    src = (MINI / "api.php").read_text(encoding="utf-8")
    out = _php(tmp_path, _php_function(src, "bannerText") + textwrap.dedent(r"""
        echo json_encode([
            bannerText('{"type":"text","text":"\u0644\u06cc\u0646\u06a9 LINK"}'),
            bannerText('plain words'),
            bannerText('{"broken \\u0634\\u0645\\u0627'),
            bannerText('{"type":"photo"}'),
            bannerText(null),
        ], JSON_UNESCAPED_UNICODE);
        """))
    assert out == '["لینک LINK","plain words","","",""]'


@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_the_html_of_the_bot_texts_keeps_its_few_tags_and_no_attribute(tmp_path):
    """strip_tags keeps the attributes of the tags it allows: <b onmouseover=...> reached the page"""
    src = (MINI / "api.php").read_text(encoding="utf-8")
    out = _php(tmp_path, _php_function(src, "safeHtml") + textwrap.dedent(r"""
        echo json_encode([
            safeHtml('a <b onmouseover="x()">bold</b> <i style="x">it</i><br/>t<script>alert(1)</script><a href="javascript:1">l</a>'),
            safeHtml("one\ntwo <code class=x>c</code> <BR clear=all>"),
            safeHtml('<img src=x onerror=y> plain'),
        ], JSON_UNESCAPED_UNICODE);
        """))
    got = json.loads(out)
    assert got[0] == "a <b>bold</b> <i>it</i><br/>talert(1)l"
    assert got[1] == "one<br>\ntwo <code>c</code> <BR>"
    assert got[2] == " plain"


@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_a_signature_that_is_a_day_old_is_told_apart_from_a_forged_one(tmp_path):
    """a Mini App left open for a day answered every tap with "this page only opens inside Telegram": the customer only has to open it again, and is told so"""
    token = "123456:TEST"

    def init(auth_date, key=token):
        user = json.dumps({"id": 7, "first_name": "A"}, separators=(",", ":"))
        d = {"auth_date": str(auth_date), "query_id": "q", "user": user}
        secret = hmac.new(b"WebAppData", key.encode(), hashlib.sha256).digest()
        d["hash"] = hmac.new(secret, "\n".join(f"{k}={d[k]}" for k in sorted(d)).encode(), hashlib.sha256).hexdigest()
        return urllib.parse.urlencode(d)

    src = (MINI / "api.php").read_text(encoding="utf-8")
    out = _php(tmp_path, _php_function(src, "tgVerify") + textwrap.dedent(r"""
        $r = [];
        foreach ([$argv[1], $argv[2], $argv[3], '', 'user[]=1&hash=abc'] as $i) { $e = false; $u = tgVerify($i, $argv[4], $e); $r[] = [$u['id'] ?? null, $e]; }
        echo json_encode($r);
        """), init(int(time.time())), init(int(time.time()) - 2 * 86400), init(int(time.time()), key="other:token"), token)
    assert json.loads(out) == [[7, False], [None, True], [None, False], [None, False], [None, False]]


def test_the_api_pays_a_gift_code_and_a_wheel_prize_once_and_answers_when_the_table_is_missing():
    """a double tap (or two scripts at once) could pass the "unused" check together and credit the wallet twice: the wheel did pay twice in a run of six requests at once on a real MariaDB;
    the panel's Gift codes page is what writes gift_codes, and a bot database that never had it answered every code with an internal error"""
    src = (MINI / "api.php").read_text(encoding="utf-8")
    gift = src[src.index("case 'gift': {"):src.index("case 'wheel': {")]
    wheel = src[src.index("case 'wheel': {"):src.index("case 'referral': {")]
    assert gift.index("CREATE TABLE IF NOT EXISTS gift_codes") < gift.index("begin_transaction") < gift.index("FOR UPDATE") < gift.index("db()->commit()")
    assert wheel.index("begin_transaction") < wheel.index("FOR UPDATE") < wheel.index("INSERT INTO wheel_log") < wheel.index("db()->commit()")
    assert "$left = $last + $hours * 3600 - time();" in wheel[wheel.index("FOR UPDATE"):]      # (the last spin is read again after the lock)
    assert "h((string) $me['name'])" in src and "h((string) $me['username'])" in src      # (the receipt caption is HTML: a name like "A<B" made Telegram refuse the picture)
    assert "JSON_INVALID_UTF8_SUBSTITUTE" in src


def test_the_new_column_texts_are_in_persian():
    ui2 = (Path(__file__).resolve().parents[1] / "nikatel" / "web" / "i18n.fa.ui2.js").read_text(encoding="utf-8")
    assert '"Bandwidth Mbit/s": "پهنای باند Mbit/s"' in ui2 and "The line of the server in megabits per second:" in ui2
    assert "The speed of the server's line: its network card reports" in ui2 and "The network card does not report its speed" in ui2
