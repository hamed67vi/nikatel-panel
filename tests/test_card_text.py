"""The card-to-card screen and the currency payment of the shop bot and of its Mini App (the owner, 2026-10-27, translated from Persian: "in the card-to-card part show only this text - if the customer is an old
one show my card number, if not put the support id so they message" / "add the currency payment under card-to-card" / "delete the red outband text entirely" / "install it on the Mini App too").

The whole thing runs on the real bot's files (bot.php / config.php / buy_helpers.php) in tests/card_text/bot_card.py and on the real Mini App api.php in tests/card_text/miniapp_card.py (a private MariaDB, php -S,
a fake Telegram: opt in with NIKATEL_CARD_E2E=1 NIKATEL_CARD_BOT_COPY=<a copy of the bot's files>). The tests below need only php, bash and node and keep the pieces from being undone:
the installer (four marked lines, the red line deleted, twice = once, uninstall = byte for byte, refuses a place that is not as expected), the words and the button of card_text.php, and the Mini App pages."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-card-text.sh"
MODULE = ROOT / "bot" / "menu" / "card_text.php"
MINI = ROOT / "bot" / "miniapp"
HARNESS = ROOT / "tests" / "card_text"
DRIVER = ROOT / "tests" / "miniapp_driver.js"
BASH, PHP, NODE = shutil.which("bash"), shutil.which("php"), shutil.which("node")

pytestmark = pytest.mark.skipif(not (BASH and PHP), reason="needs bash and php")

PE = "    $__peBack = null; if(function_exists('peOutgoing') && is_array($datas)){ [$datas, $__peBack] = peOutgoing($datas); } // [pe-support]\n"
SIG = "function buildCartToCartInfoMessage(array $paymentKeys, array $mainValues, int $priceToman, int $userId = 0): string {\n"
RED = "🔴 قبل از <b>واریز تتر برای اوتباند</b> مقدار دقیق تتر را با پشتیبانی هماهنگ کنید.\n"
NOTE = "در صورتی که <b>مشتری قدیمی یا معرفی شده</b> هستید برای دریافت <b>شماره کارت</b> به پشتیبانی پیام ارسال بفرمایید"


def _bot(tmp_path, crlf=False):
    d = tmp_path / "bot"
    (d / "settings").mkdir(parents=True)
    files = {
        "bot.php": "<?php\nrequire_once 'config.php';\nrequire_once 'buy_helpers.php';\ncheck();\n\n$x = 1;\n",
        "config.php": "<?php\nfunction bot($method, $datas = []) {\n    global $botToken;\n" + PE + "    return 1;\n}\n",
        "buy_helpers.php": "<?php\n" + SIG + "    return 'old';\n}\n",
        "settings/values.php": "<?php\n$mainValues = [\n'brw_cart_to_cart_usdt'=>\"a\n" + RED + "b\",\n];\n",
    }
    for name, text in files.items():
        (d / name).write_bytes((text.replace("\n", "\r\n") if crlf and name == "config.php" else text).encode())
    return d


def _run(tmp_path, *args):
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, check=False, env=dict(os.environ, BACKUP_DIR=str(tmp_path / "bk")))


def _snap(d):
    return {n: (d / n).read_bytes() for n in ("bot.php", "config.php", "buy_helpers.php", "settings/values.php")}


def test_the_installer_puts_in_four_marked_lines_and_deletes_the_red_line(tmp_path):
    d = _bot(tmp_path)
    r = _run(tmp_path, str(d))
    assert r.returncode == 0, r.stdout + r.stderr
    count = {n: (d / n).read_text(encoding="utf-8").count("[card-text]") for n in ("bot.php", "config.php", "buy_helpers.php")}
    assert count == {"bot.php": 2, "config.php": 1, "buy_helpers.php": 1}
    bot = (d / "bot.php").read_text(encoding="utf-8")
    assert bot.index("card_text.php") < bot.index("require_once 'config.php'")      # (the module is there before the bot's flow starts)
    assert bot.index("ctcStrip()") > bot.index("check();")
    cfg = (d / "config.php").read_text(encoding="utf-8")
    assert cfg.index("ctcOutgoing") < cfg.index("peOutgoing")      # (our button is in the request before the premium-emoji layer makes its copy of it)
    assert "ctcMessage(" in (d / "buy_helpers.php").read_text(encoding="utf-8").split(SIG)[1].splitlines()[0]
    assert "اوتباند" not in (d / "settings" / "values.php").read_text(encoding="utf-8")
    assert (d / "card_text.php").read_bytes() == MODULE.read_bytes()
    for f in ("bot.php", "config.php", "buy_helpers.php", "settings/values.php", "card_text.php"):
        assert subprocess.run([PHP, "-l", str(d / f)], capture_output=True, check=False).returncode == 0, f


def test_installing_twice_changes_nothing_and_uninstall_gives_the_old_files_back(tmp_path):
    d = _bot(tmp_path)
    before = _snap(d)
    assert _run(tmp_path, str(d)).returncode == 0
    once = _snap(d)
    r = _run(tmp_path, str(d))
    assert r.returncode == 0 and "already installed" in r.stdout
    assert _snap(d) == once
    assert _run(tmp_path, "--uninstall", str(d)).returncode == 0
    after = _snap(d)
    for n in ("bot.php", "config.php", "buy_helpers.php"):
        assert after[n] == before[n], n
    assert "اوتباند" not in after["settings/values.php"].decode()      # (the owner's red line stays deleted)
    assert not (d / "card_text.php").exists()


def test_a_config_php_with_windows_line_ends_is_kept_that_way(tmp_path):
    d = _bot(tmp_path, crlf=True)
    before = _snap(d)
    assert _run(tmp_path, str(d)).returncode == 0
    raw = (d / "config.php").read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") and b"[card-text]" in raw
    assert _run(tmp_path, "--uninstall", str(d)).returncode == 0
    assert (d / "config.php").read_bytes() == before["config.php"]


def test_a_place_that_is_not_as_expected_stops_the_installer_before_any_file_is_changed(tmp_path):
    d = _bot(tmp_path)
    (d / "config.php").write_text("<?php\nfunction bot($method, $datas = []) {\n    return 1;\n}\n", encoding="utf-8")
    before = _snap(d)
    r = _run(tmp_path, str(d))
    assert r.returncode == 3 and "not installing" in r.stderr
    assert _snap(d) == before and not (d / "card_text.php").exists()


def test_a_dry_run_changes_and_copies_nothing(tmp_path):
    d = _bot(tmp_path)
    before = _snap(d)
    r = _run(tmp_path, "--dry-run", str(d))
    assert r.returncode == 0 and "dry run" in r.stdout
    assert _snap(d) == before and not (d / "card_text.php").exists()


def _php_json(tmp_path, body):
    s = tmp_path / "t.php"
    s.write_text("<?php\nrequire " + json.dumps(str(MODULE)) + ";\n" + body, encoding="utf-8")
    r = subprocess.run([PHP, "-d", "display_errors=1", str(s)], capture_output=True, text=True, check=False)
    assert r.returncode == 0 and "Warning" not in r.stdout and "Notice" not in r.stdout, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_the_currency_button_goes_right_under_every_card_to_card_button(tmp_path):
    out = _php_json(tmp_path, r"""
$GLOBALS['botState'] = []; $GLOBALS['paymentKeys'] = ['tronwallet' => '0xWALLET'];
$h = 'Ab3dEf9Gh';
$cases = ['payCustomWithCartToCart', 'payBuyWithCartToCart', 'payIncreaseDayWithCartToCart', 'payIncreaseWithCartToCart', 'payIncreaseWalletWithCartToCart', 'payRenewWithCartToCart'];
$res = [];
foreach (array_merge($cases, ['prePayCheck_Renew_CartToCart_']) as $c) {
    $kb = ['inline_keyboard' => [[['text' => 'card', 'callback_data' => $c . $h]], [['text' => 'wallet', 'callback_data' => 'payBuyWithWallet' . $h]], [['text' => 'cancel', 'callback_data' => 'mainMenu']]]];
    $d = ctcOutgoing(['chat_id' => 1, 'reply_markup' => json_encode($kb)]);
    $res[$c] = json_decode($d['reply_markup'], true)['inline_keyboard'];
}
$plain = json_encode(['inline_keyboard' => [[['text' => 'x', 'callback_data' => 'mainMenu']]]]);
$res['plain'] = ctcOutgoing(['reply_markup' => $plain])['reply_markup'] === $plain;
$adm = json_encode(['inline_keyboard' => [[['text' => 'ok', 'callback_data' => 'approveCartToCart123']]]]);
$res['admin'] = ctcOutgoing(['reply_markup' => $adm])['reply_markup'] === $adm;
$res['array_markup'] = ctcOutgoing(['reply_markup' => ['inline_keyboard' => []]]) === ['reply_markup' => ['inline_keyboard' => []]];
$GLOBALS['botState'] = ['ctcCurrency' => 'off'];
$off = json_encode(['inline_keyboard' => [[['text' => 'c', 'callback_data' => 'payBuyWithCartToCart' . $h]]]]);
$res['off'] = ctcOutgoing(['reply_markup' => $off])['reply_markup'] === $off;
$GLOBALS['botState'] = []; $GLOBALS['paymentKeys'] = ['tronwallet' => ' '];
$res['nowallet'] = ctcOutgoing(['reply_markup' => $off])['reply_markup'] === $off;
echo json_encode($res, JSON_UNESCAPED_UNICODE);
""")
    for c, rows in out.items():
        if isinstance(rows, list):
            assert len(rows) == 4, c
            assert rows[1] == [{"text": "💎 پرداخت ارزی", "callback_data": "cuz_" + rows[0][0]["callback_data"]}], c
            assert rows[2][0]["text"] == "wallet" and rows[3][0]["text"] == "cancel"
            assert len(rows[1][0]["callback_data"]) <= 64
    assert out["plain"] is True and out["admin"] is True and out["array_markup"] is True and out["off"] is True and out["nowallet"] is True


def test_the_prefix_of_the_currency_button_is_taken_off_and_remembered(tmp_path):
    out = _php_json(tmp_path, r"""
$data = 'cuz_prePayCheck_Renew_CartToCart_Ab3dEf9Gh'; ctcStrip(); $a = [$data, !empty($GLOBALS['ctcCurrency'])];
unset($GLOBALS['ctcCurrency']); $data = 'payBuyWithCartToCartAb3dEf9Gh'; ctcStrip(); $b = [$data, !empty($GLOBALS['ctcCurrency'])];
$data = null; ctcStrip();
echo json_encode([$a, $b]);
""")
    assert out == [["prePayCheck_Renew_CartToCart_Ab3dEf9Gh", True], ["payBuyWithCartToCartAb3dEf9Gh", False]]


def test_the_support_line_takes_the_support_id_in_every_form_the_bot_allows(tmp_path):
    out = _php_json(tmp_path, r"""
$res = [];
foreach (['@nikatelsup2', 'nikatelsup2', 'https://t.me/nikatelsup2', 'https://t.me/nikatelsup2/', '', '  ', 'x y z', '@ab', '<b>x&y'] as $id) { $GLOBALS['botState'] = ['supportID' => $id]; $res[] = ctcSupportLine(); }
$GLOBALS['botState'] = [];  $res[] = ctcSupportLine();
echo json_encode($res, JSON_UNESCAPED_UNICODE);
""")
    want = "🎧 پشتیبانی: @nikatelsup2"
    assert out[:4] == [want] * 4
    assert out[4:6] == ["", ""]      # (nothing or blanks: no line)
    assert out[6:9] == ["🎧 پشتیبانی: x y z", "🎧 پشتیبانی: @ab", "🎧 پشتیبانی: &lt;b&gt;x&amp;y"]      # (whatever else the owner typed is shown as typed, and markup in it is escaped)
    assert out[9] == ""      # (no setting at all)


def test_the_words_of_the_card_to_card_screen_are_the_owners(tmp_path):
    src = MODULE.read_text(encoding="utf-8")
    assert NOTE in src and "اوتباند" not in src      # (the star line as he wrote it; the red line is nowhere)
    out = _php_json(tmp_path, "echo json_encode([CTC_NOTE_DEFAULT, CTC_CURRENCY_DEFAULT, CTC_BUTTON_DEFAULT], JSON_UNESCAPED_UNICODE);")
    assert out[0].startswith("⭐") and NOTE in out[0]
    assert "PRICE-USDT" in out[1] and "USDT-WALLET" in out[1] and "BEP20" in out[1] and "مشتری قدیمی" not in out[1] and "اوتباند" not in out[1]
    assert out[2] == "💎 پرداخت ارزی"


def test_the_mini_app_has_the_same_rules_and_words():
    api = (MINI / "api.php").read_text(encoding="utf-8")
    assert NOTE in api and "اوتباند" not in api
    assert "'currency' => ctcCurrencyOn()" in api and "$method === 'currency' && !ctcCurrencyOn()" in api
    topup = api.split("case 'topup':")[1].split("case 'cancel_topup'")[0]
    assert "'usdt_wallet' => $type === 'currency'" in topup and "'support' => $type === 'gift'" in topup and "'v' => 2" in topup
    assert "$type !== 'detail'" in topup      # (anybody the bot does not trust yet gets the star line + the support id, never the card or the wallet)
    html = (MINI / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"\?v=(\d+\.\d+\.\d+)", html) == ["3.0.21", "3.0.21"]      # (a new Mini App: Telegram must not keep the old files)


def test_the_mini_app_support_name_helper(tmp_path):
    api = (MINI / "api.php").read_text(encoding="utf-8")
    start = api.index("function ctcSupportName()")
    fn = api[start:api.index("\n", start)]
    s = tmp_path / "t.php"
    s.write_text("<?php\n" + fn + "\n$res = []; foreach (['@nikatelsup2', 'nikatelsup2', 'https://t.me/nikatelsup2', '', 'x y', '@ab'] as $id) { $botState = ['supportID' => $id]; $res[] = ctcSupportName(); }\necho json_encode($res);\n", encoding="utf-8")
    r = subprocess.run([PHP, str(s)], capture_output=True, text=True, check=False)
    assert json.loads(r.stdout) == ["nikatelsup2", "nikatelsup2", "nikatelsup2", "", "", ""], r.stdout + r.stderr


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_new_mini_app_pages_draw_and_their_handlers_are_javascript():
    r = subprocess.run([NODE, str(DRIVER), str(MINI / "app.js")], capture_output=True, text=True, check=True, timeout=90)
    out = json.loads(r.stdout)
    assert out["errors"] == []
    assert out["values"]["unknownCalls"] == []
    assert any("0xAb" in h for h in out["values"]["copyHandlers"])      # (the wallet of the currency page is copyable; the driver gives it a quote to break an attribute with)
    assert any('sup_p\\"ort' in h for h in out["values"]["openLinks"])      # (the button of the "message support" page opens the chat with the support id; the driver gives it a quote too)


@pytest.mark.skipif(not os.environ.get("NIKATEL_CARD_E2E") or not os.environ.get("NIKATEL_CARD_BOT_COPY") or not shutil.which("mariadbd") or not shutil.which("openssl"),
                    reason="the end-to-end run needs NIKATEL_CARD_E2E=1, NIKATEL_CARD_BOT_COPY=<a copy of the bot's files>, mariadbd and openssl (a private MariaDB, php -S, ports 49530-49541)")
def test_end_to_end_on_the_real_bot_files_and_the_real_api(tmp_path):
    env = dict(os.environ, SEC_SCRATCH=str(tmp_path / "sec"))
    setup = ROOT / "tests" / "miniapp_security" / "sec_setup_db.sh"
    subprocess.run([BASH, str(setup), "start"], check=True, env=env, capture_output=True)
    try:
        r = subprocess.run([sys.executable, str(HARNESS / "bot_card.py"), str(ROOT), os.environ["NIKATEL_CARD_BOT_COPY"]], env=env, check=False, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0 and "after: 0 HOLE" in r.stdout, r.stdout[-3000:] + r.stderr[-1000:]
        r = subprocess.run([sys.executable, str(HARNESS / "miniapp_card.py"), str(MINI), os.environ["NIKATEL_CARD_BOT_COPY"]], env=env, check=False, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0 and "after: 0 HOLE" in r.stdout, r.stdout[-3000:] + r.stderr[-1000:]
    finally:
        subprocess.run([BASH, str(setup), "stop"], check=False, env=env, capture_output=True)
