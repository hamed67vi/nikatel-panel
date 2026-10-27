"""The helper subscription link shows in the Mini App only (the owner, 2026-10-27, translated from Persian: "change this emoji" / "turn its display off in the bot - let it be active only in this Mini App" /
"keep the Mini App's and the bot's settings in step so no bug comes of it").

The whole thing runs on the real bot's files in tests/configs_button/bot_cfg.py (scenarios HL1-HL3, IN2: a private MariaDB, php -S, a fake Telegram, a fake panel; opt in with NIKATEL_CFG_E2E=1
NIKATEL_CFG_BOT_COPY=<a copy of the bot's files>, see test_configs_button.py). The tests below need only php and bash and keep the pieces from being undone: the installer (two marked lines in config.php, twice = once,
uninstall = byte for byte, refuses a place that is not as expected), the cut itself (hlStrip / hlOutgoing on the bot's real sentences), and the Mini App's side (the same switch, the new emoji)."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-helper-link.sh"
MODULE = ROOT / "bot" / "menu" / "helper_link.php"
MINI = ROOT / "bot" / "miniapp"
BASH, PHP = shutil.which("bash"), shutil.which("php")

pytestmark = pytest.mark.skipif(not (BASH and PHP), reason="needs bash and php")

PE = "    $__peBack = null; if(function_exists('peOutgoing') && is_array($datas)){ [$datas, $__peBack] = peOutgoing($datas); } // [pe-support]\n"
CTC = "    if (function_exists('ctcOutgoing') && is_array($datas)) $datas = ctcOutgoing($datas); // [card-text]\n"


def _bot(tmp_path, crlf=False, card_text=True):
    d = tmp_path / "bot"
    d.mkdir()
    text = "<?php\nrequire_once 'x.php';\nfunction bot($method, $datas = []) {\n    global $botToken;\n" + (CTC if card_text else "") + PE + "    return 1;\n}\n"
    (d / "config.php").write_bytes((text.replace("\n", "\r\n") if crlf else text).encode())
    return d


def _run(tmp_path, *args):
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, check=False, env=dict(os.environ, BACKUP_DIR=str(tmp_path / "bk")))


def test_the_installer_puts_in_two_marked_lines_where_they_belong(tmp_path):
    d = _bot(tmp_path)
    r = _run(tmp_path, str(d))
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = (d / "config.php").read_text(encoding="utf-8")
    assert cfg.count("[helper-link]") == 2
    assert cfg.index("helper_link.php") < cfg.index("function bot(")      # (the module is there before bot() is ever called)
    assert cfg.index("ctcOutgoing") < cfg.index("hlOutgoing(") < cfg.index("peOutgoing($datas)")      # (our cut is made before the premium-emoji layer copies the request)
    assert (d / "helper_link.php").read_bytes() == MODULE.read_bytes()
    for f in ("config.php", "helper_link.php"):
        assert subprocess.run([PHP, "-l", str(d / f)], capture_output=True, check=False).returncode == 0, f


def test_installing_twice_changes_nothing_and_uninstall_gives_the_old_file_back(tmp_path):
    d = _bot(tmp_path)
    before = (d / "config.php").read_bytes()
    assert _run(tmp_path, str(d)).returncode == 0
    once = (d / "config.php").read_bytes()
    r = _run(tmp_path, str(d))
    assert r.returncode == 0 and "already installed" in r.stdout
    assert (d / "config.php").read_bytes() == once
    assert _run(tmp_path, "--uninstall", str(d)).returncode == 0
    assert (d / "config.php").read_bytes() == before
    assert not (d / "helper_link.php").exists()


def test_a_config_php_with_windows_line_ends_is_kept_that_way(tmp_path):
    d = _bot(tmp_path, crlf=True)
    before = (d / "config.php").read_bytes()
    assert _run(tmp_path, str(d)).returncode == 0
    raw = (d / "config.php").read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") and b"[helper-link]" in raw
    assert _run(tmp_path, "--uninstall", str(d)).returncode == 0
    assert (d / "config.php").read_bytes() == before


def test_a_place_that_is_not_as_expected_stops_the_installer_before_the_file_is_changed(tmp_path):
    d = _bot(tmp_path)
    (d / "config.php").write_text("<?php\nfunction bot($method, $datas = []) {\n    return 1;\n}\n", encoding="utf-8")
    before = (d / "config.php").read_bytes()
    r = _run(tmp_path, str(d))
    assert r.returncode == 3 and "not installing" in r.stderr
    assert (d / "config.php").read_bytes() == before and not (d / "helper_link.php").exists()


def test_a_dry_run_changes_and_copies_nothing(tmp_path):
    d = _bot(tmp_path)
    before = (d / "config.php").read_bytes()
    r = _run(tmp_path, "--dry-run", str(d))
    assert r.returncode == 0 and "dry run" in r.stdout
    assert (d / "config.php").read_bytes() == before and not (d / "helper_link.php").exists()


def _php_json(tmp_path, body):
    s = tmp_path / "t.php"
    s.write_text("<?php\nrequire " + json.dumps(str(MODULE)) + ";\n" + body, encoding="utf-8")
    r = subprocess.run([PHP, "-d", "display_errors=1", str(s)], capture_output=True, text=True, check=False)
    assert r.returncode == 0 and "Warning" not in r.stdout and "Notice" not in r.stdout, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_the_cut_takes_the_block_and_the_sentences_out_and_nothing_else(tmp_path):
    out = _php_json(tmp_path, r"""
$delivery = "🛍 <b>اطلاعات اشتراک جدید شما</b>\n\n🎈 نام کاربری اشتراک:\n<code>r</code>\n\n🌐 لینک اصلی اشتراک:\n<code>https://p/sub/T</code>"
  . "\n\n🌐 لینک کمکی اشتراک:\n<code>https://a/sub/T</code>\n\n<code>https://b/sub/T</code>"
  . "\n\n💡 همیشه اکانت را با لینک اشتراک وارد کنید تا به همه کانفیگ ها دسترسی داشته باشد.\n⚠️ در صورتی که لینک اصلی اشتراک کار نکرد از لینک کمکی اشتراک استفاده کنید.";
$page = "STATE\n🔌 NAME\n🌐 SUB-LINK:\n<code>https://p/sub/T</code>" . "\n🔁 لینک کمکی اشتراک:\n<code>https://a/sub/T</code>\n\n<code>https://b/sub/T</code>" . "\n\n♻️ در صورتی که قصد دارید حجم دیگری خریداری کنید";
$upd = "<b>✅ بروزرسانی موفق بود!</b>\n\n🔄 <b>در صورتی که کانفیگ ها با لینک اشتراک اصلی یا کمکی برای شما لود نمی شوند، لطفاً مراحل زیر را انجام دهید:</b>\n\n1. a";
$chg = "3) لینک را وارد نرم افزار کنید تا کانفیگ ها لود شوند اگر لینک اصلی کار نکرد از لینک کمکی استفاده کنید.\n\n📱 x";
$crlf = str_replace("\n⚠️", "\r\n⚠️", $delivery);
echo json_encode([hlStrip($delivery), hlStrip($page), hlStrip($upd), hlStrip($chg), hlStrip($crlf)], JSON_UNESCAPED_UNICODE);
""")
    d, p, u, c, crlf = out
    assert d == "🛍 <b>اطلاعات اشتراک جدید شما</b>\n\n🎈 نام کاربری اشتراک:\n<code>r</code>\n\n🌐 لینک اصلی اشتراک:\n<code>https://p/sub/T</code>\n\n💡 همیشه اکانت را با لینک اشتراک وارد کنید تا به همه کانفیگ ها دسترسی داشته باشد."
    assert p == "STATE\n🔌 NAME\n🌐 SUB-LINK:\n<code>https://p/sub/T</code>\n\n♻️ در صورتی که قصد دارید حجم دیگری خریداری کنید"
    assert "کمکی" not in u and "با لینک اشتراک برای شما لود نمی شوند" in u and u.endswith("1. a")
    assert c == "3) لینک را وارد نرم افزار کنید تا کانفیگ ها لود شوند.\n\n📱 x"
    assert crlf == d      # (the tip line is a line of the bot's source: it is found with \r\n too)


def test_texts_that_do_not_name_the_helper_link_come_back_exactly_as_they_were(tmp_path):
    out = _php_json(tmp_path, r"""
$same = [
  "", "سلام", "🌐 لینک اصلی اشتراک:\n<code>x</code>", "تابع کمکی کیبورد", "🔗 لطفاً لینک‌های کمکی اشتراک سرور را وارد کنید.\n📌 لینک‌ها را با کاما (,) از هم جدا کنید.",
  "🌐 لینک کمکی اشتراک:\nبدون کد", "🌐 لینک کمکی اشتراک:\n<code>a</code>x",
];
$res = [];
foreach ($same as $t) $res[] = hlStrip($t) === $t || strpos(hlStrip($t), 'کمکی') === false;
echo json_encode([$res, hlStrip("تابع کمکی کیبورد"), hlStrip("🔗 لطفاً لینک‌های کمکی اشتراک سرور را وارد کنید.")], JSON_UNESCAPED_UNICODE);
""")
    assert all(out[0])
    assert out[1] == "تابع کمکی کیبورد" and out[2] == "🔗 لطفاً لینک‌های کمکی اشتراک سرور را وارد کنید."      # (the admin's own menu text is not a customer message with the block: it stays)


def test_the_outgoing_hook_follows_the_stage_and_touches_only_text_and_caption(tmp_path):
    out = _php_json(tmp_path, r"""
$block = "x\n\n🌐 لینک کمکی اشتراک:\n<code>https://a/sub/T</code>";
$req = ['chat_id' => 5, 'text' => $block, 'caption' => $block, 'parse_mode' => 'HTML', 'other' => $block, 'reply_markup' => '{"a":1}'];
$res = [];
foreach ([null, 'mini', 'off', 'junk', 'both'] as $stage) {
    $GLOBALS['botState'] = $stage === null ? [] : ['helperLink' => $stage];
    $o = hlOutgoing($req);
    $res[(string) $stage] = [strpos($o['text'], 'کمکی') === false, strpos($o['caption'], 'کمکی') === false, $o['other'] === $block, $o['chat_id'] === 5 && $o['reply_markup'] === '{"a":1}', hlStage()];
}
$GLOBALS['botState'] = [];
$res['nontext'] = hlOutgoing(['text' => 5, 'caption' => null, 'photo' => 'x']) === ['text' => 5, 'caption' => null, 'photo' => 'x'];
echo json_encode($res);
""")
    for stage in ("", "mini", "off", "junk"):
        assert out[stage] == [True, True, True, True, "mini" if stage in ("", "mini", "junk") else "off"], stage
    assert out["both"] == [False, False, True, True, "both"]
    assert out["nontext"] is True


def test_the_mini_app_reads_the_same_switch_and_has_the_new_emoji():
    api = (MINI / "api.php").read_text(encoding="utf-8")
    fn = api[api.index("function subAlt("):]
    fn = fn[:fn.index("\n    $token = $savedToken;")]
    assert "($botState['helperLink'] ?? 'mini') === 'off'" in fn and "global $botState;" in fn      # (off = nowhere; mini and both = shown here)
    js = (MINI / "app.js").read_text(encoding="utf-8")
    assert "<h3>📎 لینک کمکی اشتراک " in js and "🔁" not in js.split("const altBox")[1].split("\n")[0]
    html = (MINI / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"\?v=(\d+\.\d+\.\d+)", html) == ["3.0.21", "3.0.21"]      # (a new Mini App: Telegram must not keep the old files)
