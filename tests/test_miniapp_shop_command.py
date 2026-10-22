"""The /shop command of the shop bot (the owner, 2026-10-23: "can't the normal Menu button stay and the Mini App be one of its options?" - Telegram's Menu button is either the command list or one Mini App,
so the Mini App became a command of the list: /shop answers with a message and a button that opens it).
scripts/install-shop-command.sh puts one block into the bot's bot.php; these tests run the installer on a stand-in bot.php and the block itself under php with the bot's functions stubbed."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-shop-command.sh"
PHP, BASH, PY = shutil.which("php"), shutil.which("bash"), shutil.which("python3")
pytestmark = pytest.mark.skipif(not (PHP and BASH and PY), reason="php, bash and python3 are needed")

FAKE = """<?php
function sendMessage($txt, $key = null, $parse = "MarkDown") { echo json_encode(["txt" => $txt, "key" => json_decode($key, true), "parse" => $parse], JSON_UNESCAPED_UNICODE), "\\n"; }
$update = json_decode(getenv("UPDATE"));
$botUrl = getenv("BOTURL") === false ? "https://bot.example/" : getenv("BOTURL");

if ($update->banned ?? false) { echo "BANNED\\n"; exit(); }

// Pre Pay Check By ACh
echo "CONTINUED\\n";
"""


def _run(*args, check=True):
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, check=check)


@pytest.fixture
def bot(tmp_path, monkeypatch):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "bot.php").write_text(FAKE, encoding="utf-8")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "bk"))
    return d


def _answer(bot_dir, update, **env):
    import os
    r = subprocess.run([PHP, str(bot_dir / "bot.php")], capture_output=True, text=True, env=dict(os.environ, UPDATE=json.dumps(update), **env), check=True)
    return r.stdout.strip().splitlines()


def test_the_installer_adds_one_block_once_and_takes_it_out_byte_for_byte(bot, tmp_path):
    before = (bot / "bot.php").read_bytes()
    r = _run("--dry-run", str(bot))
    assert "dry run" in r.stdout and (bot / "bot.php").read_bytes() == before                      # nothing replaced
    assert "block added" in _run(str(bot)).stdout
    text = (bot / "bot.php").read_text(encoding="utf-8")
    assert text.count("// [shop-command]") == 1 and text.count("// [/shop-command]") == 1
    assert text.index("// [shop-command]") < text.index("// Pre Pay Check By ACh") and text.index("BANNED") < text.index("// [shop-command]")      # after the bot's own ban / spam checks
    assert len(list((tmp_path / "bk").iterdir())) == 1 and (next((tmp_path / "bk").iterdir())).read_bytes() == before                                 # the old file is kept
    assert "no change" in _run(str(bot)).stdout and (bot / "bot.php").read_text(encoding="utf-8") == text                                    # again = nothing
    _run("--uninstall", str(bot))
    assert (bot / "bot.php").read_bytes() == before
    assert "not installed" in _run("--uninstall", str(bot)).stdout


def test_the_installer_keeps_windows_line_ends_and_refuses_a_file_it_does_not_know(bot):
    (bot / "bot.php").write_bytes(FAKE.replace("\n", "\r\n").encode())
    before = (bot / "bot.php").read_bytes()
    _run(str(bot))
    new = (bot / "bot.php").read_bytes()
    assert b"\n" not in new.replace(b"\r\n", b"") and b"[shop-command]" in new
    _run("--uninstall", str(bot))
    assert (bot / "bot.php").read_bytes() == before
    (bot / "bot.php").write_text(FAKE.replace("// Pre Pay Check By ACh", "// something else"), encoding="utf-8")
    r = _run(str(bot), check=False)
    assert r.returncode == 3 and "[shop-command]" not in (bot / "bot.php").read_text(encoding="utf-8")
    (bot / "bot.php").write_text(FAKE + "\n// Pre Pay Check By ACh\n", encoding="utf-8")
    assert _run(str(bot), check=False).returncode == 3          # two anchors: which one? none


def test_a_new_file_that_does_not_parse_never_replaces_the_old_one(bot, monkeypatch):
    (bot / "bot.php").write_text(FAKE + "\nfunction broken( {\n", encoding="utf-8")
    before = (bot / "bot.php").read_bytes()
    r = _run(str(bot), check=False)
    assert r.returncode != 0 and (bot / "bot.php").read_bytes() == before


def test_shop_in_a_private_chat_answers_with_the_button_and_stops_everything_else(bot):
    _run(str(bot))
    for text in ("/shop", "/SHOP", " /shop ", "/shop@nikatelshopbot"):
        out = _answer(bot, {"message": {"text": text, "chat": {"type": "private"}}})
        assert len(out) == 1 and "CONTINUED" not in out[0], (text, out)
        msg = json.loads(out[0])
        assert msg["parse"] == "HTML"
        btn = msg["key"]["inline_keyboard"][0][0]
        assert btn["web_app"] == {"url": "https://bot.example/app/"} and "فروشگاه" in btn["text"]


def test_everything_that_is_not_the_command_in_a_private_chat_goes_on_as_before(bot):
    _run(str(bot))
    cases = [{"message": {"text": "/shopping", "chat": {"type": "private"}}}, {"message": {"text": "/start", "chat": {"type": "private"}}}, {"message": {"text": "shop", "chat": {"type": "private"}}},
             {"message": {"text": "/shop now", "chat": {"type": "private"}}}, {"message": {"text": "/shop", "chat": {"type": "group"}}}, {"message": {"chat": {"type": "private"}}},
             {"callback_query": {"message": {"text": "/shop", "chat": {"type": "private"}}}}, {}]
    for upd in cases:
        assert _answer(bot, upd) == ["CONTINUED"], upd
    assert _answer(bot, {"message": {"text": "/shop", "chat": {"type": "private"}}}, BOTURL="") == ["CONTINUED"]          # no $botUrl: no Mini App to open, the bot answers as before
    assert _answer(bot, {"banned": True, "message": {"text": "/shop", "chat": {"type": "private"}}}) == ["BANNED"]          # a banned user never reaches the block


def test_the_panel_lists_the_command_with_its_others():
    src = (ROOT / "nikatel" / "services" / "shopbot_admin.py").read_text(encoding="utf-8")
    assert '{"command": "shop", "description": "فروشگاه"}' in src
