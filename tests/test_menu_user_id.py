"""The customer's own numeric id, monospace, in the main-menu text of the shop bot (the owner, 2026-10-25, translated: the menu text ends with the customer's numeric id; "the numbers must be monospace").

scripts/install-menu-user-id.sh copies bot/menu/menu_user_id.php and puts two marked lines into config.php (the include, and a call inside the bot's own bot() function, right after the premium-emoji line). These tests run the installer on
a stand-in config.php (CRLF like the bot's own) and run the stand-in bot() with the installed lines; the whole bot is run by tests/support_section/bot_support.py, scenario MU1 (opt in, see tests/test_support_section.py)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-menu-user-id.sh"
HELPER = ROOT / "bot" / "menu" / "menu_user_id.php"
PHP, BASH, PY = shutil.which("php"), shutil.which("bash"), shutil.which("python3")
pytestmark = pytest.mark.skipif(not (PHP and BASH and PY), reason="php, bash and python3 are needed")

CONFIG = """<?php
function peOutgoing($d) { if (!str_contains($d['text'] ?? '', 'PE')) return [$d, null]; $s = $d; $s['text'] = str_replace('PE', '*', $d['text']); $s['parse_mode'] = 'HTML'; return [$s, $d]; }

function bot($method, $datas = []) {
    global $botToken;
    $__peBack = null; if(function_exists('peOutgoing') && is_array($datas)){ [$datas, $__peBack] = peOutgoing($datas); } // [pe-support]
    $url = "https://api.telegram.org/bot" . $botToken . "/" . $method;
    echo json_encode([$datas, $__peBack], JSON_UNESCAPED_UNICODE), "\\n";
}
"""


def _install(*args, check=True, env=None):
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, check=check, env=dict(os.environ, **(env or {})))


@pytest.fixture
def bot(tmp_path):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "config.php").write_bytes(CONFIG.replace("\n", "\r\n").encode("utf-8"))      # (the bot's config.php has CRLF line ends)
    os.chmod(d / "config.php", 0o640)
    return d


def test_the_installer_puts_two_marked_lines_and_takes_them_out_byte_for_byte(bot, tmp_path):
    env = {"BACKUP_DIR": str(tmp_path / "bk")}
    before = (bot / "config.php").read_bytes()
    assert "nothing replaced" in _install("--dry-run", str(bot), env=env).stdout and (bot / "config.php").read_bytes() == before and not (bot / "menu_user_id.php").exists()
    assert "2 line(s) put in" in _install(str(bot), env=env).stdout
    after = (bot / "config.php").read_bytes()
    assert after.count(b"// [menu-id]") == 2 and after.count(b"\r\n") == before.count(b"\r\n") + 2 and after.replace(b"\r\n", b"").count(b"\n") == 0      # (still CRLF all through)
    text = after.decode("utf-8")
    assert text.index("peOutgoing($datas); } // [pe-support]") < text.index("menuIdFormat($datas)") < text.index("$url = ")      # (after the premium-emoji line, never in front of it)
    assert (bot / "menu_user_id.php").read_bytes() == HELPER.read_bytes() and oct((bot / "config.php").stat().st_mode & 0o777) == "0o640"
    assert any(p.read_bytes() == before for p in (tmp_path / "bk").glob("config.php.before-menu-id-*"))
    assert "already installed" in _install(str(bot), env=env).stdout
    assert "taken out" in _install("--uninstall", str(bot), env=env).stdout and (bot / "config.php").read_bytes() == before and not (bot / "menu_user_id.php").exists()
    assert "not installed" in _install("--uninstall", str(bot), env=env).stdout


def test_the_installer_refuses_when_the_place_is_missing_or_twice(bot, tmp_path):
    env = {"BACKUP_DIR": str(tmp_path / "bk")}
    original = (bot / "config.php").read_bytes().decode("utf-8")      # (bytes: the file is CRLF and read_text would turn it into LF)
    pe ="    $__peBack = null; if(function_exists('peOutgoing') && is_array($datas)){ [$datas, $__peBack] = peOutgoing($datas); } // [pe-support]\r\n"
    for broken in (original.replace("peOutgoing($datas); }", "peOutgoing($datas); } "), original.replace(pe, pe * 2)):
        (bot / "config.php").write_bytes(broken.encode("utf-8"))
        before = (bot / "config.php").read_bytes()
        r = _install(str(bot), check=False, env=env)
        assert r.returncode == 3 and "not installing" in r.stderr and (bot / "config.php").read_bytes() == before and not (bot / "menu_user_id.php").exists()


def _sent(bot, payload, **globals_):
    code = "require 'config.php'; " + "".join(f"${k} = {json.dumps(v)}; " for k, v in globals_.items()) + f"bot('sendMessage', json_decode({json.dumps(json.dumps(payload))}, true));"
    r = subprocess.run([PHP, "-d", "display_errors=1", "-r", code], capture_output=True, text=True, cwd=bot, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.splitlines()[-1])


def test_the_id_is_written_for_the_parse_mode_of_the_message(bot, tmp_path):
    _install(str(bot), env={"BACKUP_DIR": str(tmp_path / "bk")})
    text = "Hello\n\nID: MENU-ID"
    for mode, want in (("HTML", "ID: <code>123456</code>"), ("MarkDown", "ID: `123456`"), ("Markdown", "ID: `123456`"), ("MarkdownV2", "ID: `123456`"), (None, "ID: 123456")):
        payload = {"chat_id": 123456, "text": text} | ({"parse_mode": mode} if mode else {})
        sent, back = _sent(bot, payload)
        assert sent["text"].endswith(want) and "MENU-ID" not in sent["text"] and back is None, (mode, sent)
    sent, _ = _sent(bot, {"chat_id": "-100777", "caption": "C MENU-ID", "parse_mode": "HTML"})      # (a caption too; a chat id may be negative)
    assert sent["caption"] == "C <code>-100777</code>"
    sent, _ = _sent(bot, {"chat_id": "@channel", "text": "ID: MENU-ID"}, from_id=77)                  # (not a numeric chat: the id of the person of the update)
    assert sent["text"] == "ID: 77"
    for payload in ({"chat_id": 5, "text": "no word here", "parse_mode": "HTML"}, {"chat_id": 5, "text": "USER-ID stays", "parse_mode": "HTML"}, {"chat_id": 5}):
        sent, _ = _sent(bot, payload)
        assert sent == payload      # (a text without the word is not touched)


def test_the_premium_emoji_retry_copy_is_done_the_same_way(bot, tmp_path):
    """the premium-emoji layer turns a message into HTML and keeps the original for a retry: the sent one must carry <code>, the kept one the markup of the original parse mode"""
    _install(str(bot), env={"BACKUP_DIR": str(tmp_path / "bk")})
    sent, back = _sent(bot, {"chat_id": 123456, "text": "PE ID: MENU-ID", "parse_mode": "Markdown"})
    assert sent["parse_mode"] == "HTML" and sent["text"] == "* ID: <code>123456</code>"
    assert back["text"] == "PE ID: `123456`" and back["parse_mode"] == "Markdown"
