"""The emoji list of the shop bot's "edit buttons" panel (the owner, 2026-10-25, translated: "check that the list of the bot's emoji is complete, add the support's too").

scripts/install-emoji-extra.sh puts two marked lines into premium_emoji.php (the include, and a call in peEmojiList()) and copies bot/menu/emoji_extra.php, which gives the list the emoji of the buttons that are written in the bot's code
and of the buttons the admin made himself. These tests run the installer on a stand-in premium_emoji.php and the helper on its own; the whole bot (a copy of the real files, a private MariaDB) is run by
tests/support_section/bot_support.py, scenario EL1 (opt in, see tests/test_support_section.py)."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-emoji-extra.sh"
HELPER = ROOT / "bot" / "menu" / "emoji_extra.php"
PHP, BASH, PY = shutil.which("php"), shutil.which("bash"), shutil.which("python3")
pytestmark = pytest.mark.skipif(not (PHP and BASH and PY), reason="php, bash and python3 are needed")

PE = """<?php
/**
 * Premium (custom) emoji for the shop bot.
 */
function peEmojiList(){
    global $buttonValues, $connection;
    $seen = [];
    $add = function ($label, $server) use (&$seen) {
        if (preg_match_all('/[\\x{1F000}-\\x{1FFFF}\\x{2600}-\\x{27BF}]/u', $label, $m)) foreach ($m[0] as $e) $seen[$e] = ($seen[$e] ?? 0) + 1;
    };
    if (isset($connection) && $connection instanceof mysqli) {
        $add('server', true);
    }
    if (is_array($buttonValues)) foreach ($buttonValues as $v) if (is_string($v) && $v !== '') $add($v, false);
    return $seen;
}
"""


def _install(*args, check=True, env=None):
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, check=check, env=dict(os.environ, **(env or {})))


@pytest.fixture
def bot(tmp_path):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "premium_emoji.php").write_text(PE, encoding="utf-8")
    os.chmod(d / "premium_emoji.php", 0o640)
    return d


def test_the_installer_puts_two_marked_lines_and_takes_them_out_byte_for_byte(bot, tmp_path):
    env = {"BACKUP_DIR": str(tmp_path / "bk")}
    before = (bot / "premium_emoji.php").read_bytes()
    assert "nothing replaced" in _install("--dry-run", str(bot), env=env).stdout and (bot / "premium_emoji.php").read_bytes() == before and not (bot / "emoji_extra.php").exists()
    assert "2 line(s) put in" in _install(str(bot), env=env).stdout
    src = (bot / "premium_emoji.php").read_text(encoding="utf-8")
    assert src.count("// [emoji-extra]") == 2 and src.index("foreach ($buttonValues as $v)") < src.index("peExtraLabels()") < src.index("return $seen;")      # (after the labels of values.php, before the list is returned)
    assert (bot / "emoji_extra.php").read_bytes() == HELPER.read_bytes() and oct((bot / "premium_emoji.php").stat().st_mode & 0o777) == "0o640"
    assert any(p.read_bytes() == before for p in (tmp_path / "bk").glob("premium_emoji.php.before-emoji-extra-*"))
    assert "already installed" in _install(str(bot), env=env).stdout
    assert "taken out" in _install("--uninstall", str(bot), env=env).stdout and (bot / "premium_emoji.php").read_bytes() == before and not (bot / "emoji_extra.php").exists()
    assert "not installed" in _install("--uninstall", str(bot), env=env).stdout


def test_the_installer_refuses_a_file_that_is_not_as_expected_and_completes_a_half_installed_one(bot, tmp_path):
    env = {"BACKUP_DIR": str(tmp_path / "bk")}
    original = (bot / "premium_emoji.php").read_text(encoding="utf-8")
    loop = "    if (is_array($buttonValues)) foreach ($buttonValues as $v) if (is_string($v) && $v !== '') $add($v, false);\n"
    for broken in (original.replace("$buttonValues as $v", "$buttonValues as $w"), original.replace(loop, loop * 2)):
        (bot / "premium_emoji.php").write_text(broken, encoding="utf-8")
        before = (bot / "premium_emoji.php").read_bytes()
        r = _install(str(bot), check=False, env=env)
        assert r.returncode == 3 and "not installing" in r.stderr and (bot / "premium_emoji.php").read_bytes() == before and not (bot / "emoji_extra.php").exists()
    (bot / "premium_emoji.php").write_text(original, encoding="utf-8")
    _install(str(bot), env=env)
    full = (bot / "premium_emoji.php").read_text(encoding="utf-8")
    (bot / "premium_emoji.php").write_text("".join(ln for ln in full.splitlines(keepends=True) if "peExtraLabels()" not in ln), encoding="utf-8")      # (the second line is gone)
    assert "1 line(s) put in, 1 already there" in _install(str(bot), env=env).stdout and (bot / "premium_emoji.php").read_text(encoding="utf-8") == full


def _list(bot, values):
    code = f"require 'premium_emoji.php'; $buttonValues = json_decode({json.dumps(json.dumps(values))}, true); echo json_encode(peEmojiList(), JSON_UNESCAPED_UNICODE);"
    r = subprocess.run([PHP, "-d", "display_errors=1", "-r", code], check=False, capture_output=True, text=True, cwd=bot)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_the_list_gets_the_emoji_of_the_buttons_written_in_the_code(bot, tmp_path):
    values = {"support_section": "🎧 x", "plain": "no emoji"}
    assert set(_list(bot, values)) == {"🎧"}
    _install(str(bot), env={"BACKUP_DIR": str(tmp_path / "bk")})
    got = _list(bot, values)
    assert "🎧" in got and "🔰" in got and "🔙" in got and len(got) > 40      # (the emoji of the code's own buttons are in now)
    assert got["🎧"] == 1                                                                         # (an emoji the list had keeps its count: the extra labels only cover emoji it did NOT have)


def test_the_extra_labels_are_labels_with_emoji_and_the_helper_never_throws():
    out = subprocess.run([PHP, "-d", "display_errors=1", "-r", f"require {json.dumps(str(HELPER))}; $labels = peExtraLabels(); echo json_encode($labels, JSON_UNESCAPED_UNICODE);"], check=False, capture_output=True, text=True)
    labels = json.loads(out.stdout)
    assert len(labels) >= 60 and len(set(labels)) == len(labels) and all(isinstance(x, str) and x.strip() == x and re.search("[\U0001F000-\U0001FFFF←-⇿⌀-⏿■-◿☀-➿⤀-⥿⬀-⯿]", x) for x in labels)
    assert not any("$" in x or "{" in x for x in labels)                  # (plain words only: nothing that is a variable of the code)
    broken = subprocess.run([PHP, "-d", "display_errors=1", "-r", f"require {json.dumps(str(HELPER))}; $connection = new class {{ }}; echo count(peExtraLabels());"], check=False, capture_output=True, text=True)
    assert broken.returncode == 0 and int(broken.stdout) == len(labels)    # (no database: the fixed examples only)
