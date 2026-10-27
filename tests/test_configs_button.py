"""The "show configs" button of the shop bot (the owner, 2026-10-27, translated from Persian: "when the subscription is sent to the user in the bot, send only the subscription link - but add a 'show configs' button to it").

The whole thing runs on the real bot's files (bot.php / config.php / buy_helpers.php) in tests/configs_button/bot_cfg.py (a private MariaDB, php -S, a fake Telegram, a fake Marzban-shaped panel: opt in with
NIKATEL_CFG_E2E=1 NIKATEL_CFG_BOT_COPY=<a copy of the bot's files>). The tests below need only php and bash and keep the pieces from being undone:
the installer (five marked lines, twice = once, uninstall = byte for byte, refuses a place that is not as expected), the helpers of configs_button.php, and the two Mini App messages that carry the button."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-configs-button.sh"
MODULE = ROOT / "bot" / "menu" / "configs_button.php"
MINI = ROOT / "bot" / "miniapp"
HARNESS = ROOT / "tests" / "configs_button"
BASH, PHP = shutil.which("bash"), shutil.which("php")

pytestmark = pytest.mark.skipif(not (BASH and PHP), reason="needs bash and php")

QRSUB = "            $flatButtons[] = ['text' => $buttonValues['qr_sub'], 'callback_data' => \"showQrSub{$id}\"];\n"
QRLINE = "        $qrLink = ($linkCount <= 3 && !empty($link)) ? $link : $mainSubLink;\n"
SENDTO = "        if (!$sendToUser) {\n"
FILES = ("bot.php", "config.php", "buy_helpers.php")


def _bot(tmp_path, crlf=False):
    d = tmp_path / "bot"
    d.mkdir()
    files = {
        "bot.php": "<?php\nrequire_once 'config.php';\nrequire_once 'buy_helpers.php';\n\ncheck();\nif (function_exists('ctcStrip')) ctcStrip(); // [card-text]\n\n$x = 1;\n",
        "config.php": "<?php\nfunction getOrderDetailKeys($from_id, $id) {\n    global $botState, $buttonValues;\n    $flatButtons = [];\n    if ($found) {\n"
                      "        if (($botState['qrSubState'] ?? \"off\") == \"on\")\n" + QRSUB + "\n        $flatButtons[] = ['text' => 'x', 'callback_data' => 'y'];\n    }\n}\n",
        "buy_helpers.php": "<?php\nfunction buildLinksAndSendQr() {\n" + QRLINE + "        if (empty($qrLink)) $qrLink = $vraylink[0];\n        $keys = '{}';\n\n" + SENDTO + "            return [$token, $keys];\n        }\n}\n",
    }
    for name, text in files.items():
        (d / name).write_bytes((text.replace("\n", "\r\n") if crlf and name == "config.php" else text).encode())
    return d


def _run(tmp_path, *args):
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, check=False, env=dict(os.environ, BACKUP_DIR=str(tmp_path / "bk")))


def _snap(d):
    return {n: (d / n).read_bytes() for n in FILES}


def test_the_installer_puts_in_five_marked_lines_where_they_belong(tmp_path):
    d = _bot(tmp_path)
    r = _run(tmp_path, str(d))
    assert r.returncode == 0, r.stdout + r.stderr
    count = {n: (d / n).read_text(encoding="utf-8").count("[configs-button]") for n in FILES}
    assert count == {"bot.php": 2, "config.php": 1, "buy_helpers.php": 2}
    bot = (d / "bot.php").read_text(encoding="utf-8")
    assert bot.index("configs_button.php") < bot.index("require_once 'config.php'")      # (the module is there before the bot's flow starts)
    assert bot.index("check();") < bot.index("cfgBtnHandle()") < bot.index("ctcStrip()")      # (a tap on the button is answered right after the bot's own gate, before any menu code)
    assert "cfgBtnHandle()) exit;" in bot
    cfg = (d / "config.php").read_text(encoding="utf-8")
    assert cfg.index("showQrSub") < cfg.index("cfgBtnFlat(") < cfg.index("'y'")      # (after the QR buttons, before the rest of the page's buttons)
    buy = (d / "buy_helpers.php").read_text(encoding="utf-8")
    assert buy.index(QRLINE.strip()) < buy.index("cfgBtnQr(") < buy.index("if (empty($qrLink))")      # (the QR rule runs before the old fallback for an empty QR)
    assert buy.index("cfgBtnKeys(") < buy.index("if (!$sendToUser)")      # (the keyboard is final before it is returned or sent)
    assert (d / "configs_button.php").read_bytes() == MODULE.read_bytes()
    for f in (*FILES, "configs_button.php"):
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
    assert _snap(d) == before
    assert not (d / "configs_button.php").exists()


def test_a_config_php_with_windows_line_ends_is_kept_that_way(tmp_path):
    d = _bot(tmp_path, crlf=True)
    before = _snap(d)
    assert _run(tmp_path, str(d)).returncode == 0
    raw = (d / "config.php").read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") and b"[configs-button]" in raw
    assert _run(tmp_path, "--uninstall", str(d)).returncode == 0
    assert (d / "config.php").read_bytes() == before["config.php"]


def test_a_place_that_is_not_as_expected_stops_the_installer_before_any_file_is_changed(tmp_path):
    d = _bot(tmp_path)
    (d / "buy_helpers.php").write_text("<?php\nfunction buildLinksAndSendQr() {\n    return 1;\n}\n", encoding="utf-8")
    before = _snap(d)
    r = _run(tmp_path, str(d))
    assert r.returncode == 3 and "not installing" in r.stderr
    assert _snap(d) == before and not (d / "configs_button.php").exists()


def test_a_dry_run_changes_and_copies_nothing(tmp_path):
    d = _bot(tmp_path)
    before = _snap(d)
    r = _run(tmp_path, "--dry-run", str(d))
    assert r.returncode == 0 and "dry run" in r.stdout
    assert _snap(d) == before and not (d / "configs_button.php").exists()


def test_the_card_text_installer_still_finds_its_places_after_this_one(tmp_path):
    """both installers put a line right after check(); the second one must not be confused by the first"""
    d = _bot(tmp_path)
    assert _run(tmp_path, str(d)).returncode == 0
    bot = (d / "bot.php").read_text(encoding="utf-8")
    assert bot.count("\ncheck();\n") == 1 and bot.count("[card-text]") == 1
    assert _run(tmp_path, "--uninstall", str(d)).returncode == 0
    assert (d / "bot.php").read_text(encoding="utf-8").count("[card-text]") == 1      # (the other module's line is untouched)


def _php_json(tmp_path, body):
    s = tmp_path / "t.php"
    s.write_text("<?php\nrequire " + json.dumps(str(MODULE)) + ";\n" + body, encoding="utf-8")
    r = subprocess.run([PHP, "-d", "display_errors=1", str(s)], capture_output=True, text=True, check=False)
    assert r.returncode == 0 and "Warning" not in r.stdout and "Notice" not in r.stdout, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_the_keyboard_of_the_new_subscription_message_gets_the_button_as_its_first_row(tmp_path):
    out = _php_json(tmp_path, r"""
$kb = json_encode(['inline_keyboard' => [[['text' => 'Start', 'url' => 'https://t.me/x?start=mainMenu']]]]);
$long = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.' . str_repeat('A', 200) . '.sig';
$res = [];
$on = json_decode(cfgBtnKeys($kb, $long, ['configLinkState' => 'off']), true)['inline_keyboard'];
$res['rows'] = $on;
$res['same_hash'] = cfgBtnKeys($kb, $long, ['configLinkState' => 'off']) === cfgBtnKeys($kb, $long, ['configLinkState' => 'off']);
$res['len'] = strlen($on[0][0]['callback_data']);
$res['shown'] = cfgBtnKeys($kb, $long, ['configLinkState' => 'on']) === $kb;
$res['unset'] = cfgBtnKeys($kb, $long, []) === $kb;      // (nothing says the configs are hidden: nothing changes)
$res['switched_off'] = cfgBtnKeys($kb, $long, ['configLinkState' => 'off', 'cfgButton' => 'off']) === $kb;
$res['no_token'] = cfgBtnKeys($kb, '', ['configLinkState' => 'off']) === $kb;
$res['bad_json'] = cfgBtnKeys('not json', $long, ['configLinkState' => 'off']) === 'not json';
$res['no_keyboard'] = cfgBtnKeys('{"a":1}', $long, ['configLinkState' => 'off']) === '{"a":1}';
$res['other_tokens_differ'] = cfgBtnHash('a') !== cfgBtnHash('b') && strlen(cfgBtnHash('a')) === 12;
echo json_encode($res, JSON_UNESCAPED_UNICODE);
""")
    assert out["rows"][0] == [{"text": "📋 نمایش کانفیگ‌ها", "callback_data": out["rows"][0][0]["callback_data"]}]
    assert re.fullmatch(r"showCfgT[0-9a-f]{12}", out["rows"][0][0]["callback_data"])
    assert out["rows"][1][0]["text"] == "Start" and len(out["rows"]) == 2
    assert out["len"] <= 64      # (a callback may not be longer than 64 bytes: a Marzban token is)
    assert out["same_hash"] and out["shown"] and out["unset"] and out["switched_off"] and out["no_token"] and out["bad_json"] and out["no_keyboard"] and out["other_tokens_differ"]


def test_the_qr_is_the_subscription_link_only_while_the_configs_are_hidden(tmp_path):
    out = _php_json(tmp_path, r"""
echo json_encode([
  cfgBtnQr('vless://cfg', 'https://p/sub/t', ['configLinkState' => 'off']),
  cfgBtnQr('vless://cfg', 'https://p/sub/t', ['configLinkState' => 'on']),
  cfgBtnQr('vless://cfg', '', ['configLinkState' => 'off']),
  cfgBtnQr('vless://cfg', 'https://p/sub/t', []),
  cfgBtnQr('vless://cfg', 'https://p/sub/t', ['configLinkState' => 'off', 'cfgButton' => 'off']),
]);
""")
    assert out == ["https://p/sub/t", "vless://cfg", "vless://cfg", "vless://cfg", "vless://cfg"]


def test_the_button_of_the_service_page_and_the_list_of_configs(tmp_path):
    out = _php_json(tmp_path, r"""
$flat = [['text' => 'a', 'callback_data' => 'b']];
echo json_encode([
  cfgBtnFlat($flat, 42, ['configLinkState' => 'off']),
  cfgBtnFlat($flat, 42, ['configLinkState' => 'on']) === $flat,
  cfgBtnFlat($flat, 0, ['configLinkState' => 'off']) === $flat,
  cfgBtnFlat('x', 42, ['configLinkState' => 'off']),
  cfgBtnLinks('["vless://a", "", " vmess://b ", 5, null]'),
  cfgBtnLinks(['x', ['y']]),
  cfgBtnLinks((object) ['0' => 'z']),
  cfgBtnLinks('not json'),
  cfgBtnLinks(null),
], JSON_UNESCAPED_UNICODE);
""")
    assert out[0] == [{"text": "a", "callback_data": "b"}, {"text": "📋 نمایش کانفیگ‌ها", "callback_data": "showCfgO42"}]
    assert out[1] is True and out[2] is True and out[3] == "x"
    assert out[4] == ["vless://a", "vmess://b"] and out[5] == ["x"] and out[6] == ["z"] and out[7] == [] and out[8] == []


def test_the_handler_leaves_every_other_tap_alone(tmp_path):
    out = _php_json(tmp_path, r"""
$res = [];
foreach (['mainMenu', 'showQrConfig5', 'showCfgO', 'showCfgOx', 'showCfgT123', 'showCfgTXYZXYZXYZXYZ', 'showCfgO5 ', '', null] as $d) { $data = $d; $res[] = cfgBtnHandle(); }
echo json_encode($res);
""")
    assert out == [False] * 9


def test_the_mini_app_messages_with_the_subscription_link_carry_the_button():
    api = (MINI / "api.php").read_text(encoding="utf-8")
    helper = api[api.index("function cfgButton("):]
    helper = helper[:helper.index("\n}\n")]
    assert "is_file(__DIR__ . '/../configs_button.php')" in helper and "($botState['cfgButton'] ?? '') === 'off'" in helper      # (no file, no button; the owner's switch)
    assert "'callback_data' => 'showCfgO' . $orderId" in helper
    assert 'cfgButton((int) $r[\'order_id\']));' in api      # (the purchase message: the order just made)
    assert 'cfgButton((int) $o[\'id\']));' in api      # (the "your link changed" message: the order whose link changed)
    assert api.count("cfgButton(") == 3      # (the definition and exactly these two messages)


@pytest.mark.skipif(not os.environ.get("NIKATEL_CFG_E2E") or not os.environ.get("NIKATEL_CFG_BOT_COPY") or not shutil.which("mariadbd") or not shutil.which("openssl"),
                    reason="the end-to-end run needs NIKATEL_CFG_E2E=1, NIKATEL_CFG_BOT_COPY=<a copy of the bot's files>, mariadbd and openssl (a private MariaDB, php -S, ports 49534-49538)")
def test_end_to_end_on_the_real_bot_files(tmp_path):
    env = dict(os.environ, SEC_SCRATCH=str(tmp_path / "sec"))
    setup = ROOT / "tests" / "miniapp_security" / "sec_setup_db.sh"
    subprocess.run([BASH, str(setup), "start"], check=True, env=env, capture_output=True)
    try:
        r = subprocess.run([sys.executable, str(HARNESS / "bot_cfg.py"), str(ROOT), os.environ["NIKATEL_CFG_BOT_COPY"]], env=env, check=False, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0 and "after: 0 HOLE" in r.stdout, r.stdout[-3000:] + r.stderr[-1000:]
    finally:
        subprocess.run([BASH, str(setup), "stop"], check=False, env=env, capture_output=True)
