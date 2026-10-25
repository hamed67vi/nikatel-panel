"""A customer with an active agency plan takes no discount code (the owner, 2026-10-26, translated: "whoever has an active agency plan must not accept a discount code any more").

scripts/install-agent-no-code.sh puts one marked line into applyDiscountToPay() of the bot's config.php; these tests run the installer on a stand-in config.php (CRLF like the bot's own) and run the stand-in function with the line installed.
The bot's own function (cut out of a copy of the real file) runs in tests/support_section/bot_support.py, scenario AC1 (opt in, see tests/test_support_section.py); the Mini App has the same rule in api.php
(tests/test_miniapp_security.py, harness scenarios DC1 / DC2)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-agent-no-code.sh"
PHP, BASH, PY = shutil.which("php"), shutil.which("bash"), shutil.which("python3")
pytestmark = pytest.mark.skipif(not (PHP and BASH and PY), reason="php, bash and python3 are needed")

CONFIG = """<?php
function applyDiscountToPay($connection, $rowId, $discountCode, $from_id, $username, $first_name, $admin, $mainValues, $botState){
    $payInfo = ['hash_id' => 'h1', 'price' => 130000];
    $hash_id = $payInfo['hash_id'];
    $price   = (int)$payInfo['price'];
    $userRow = ['is_agent' => getenv("AGENT")];
    $isAgent = ($userRow && isset($userRow['is_agent'])) ? (int)$userRow['is_agent'] : 0;

    // the code is used up here, at the end
    echo "USED UP\\n";
    return ['success' => true, 'price' => $price - 13000, 'hash_id' => $hash_id, 'discount_value' => 13000, 'discount_text' => '', 'payInfo' => $payInfo];
}
function sendMessage($t) { echo "SEND: ", $t, "\\n"; }
echo json_encode(applyDiscountToPay(null, 1, 'X', 5, 'u', 'n', 1, [], [])), "\\n";
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


def test_the_installer_puts_one_marked_line_and_takes_it_out_byte_for_byte(bot, tmp_path):
    env = {"BACKUP_DIR": str(tmp_path / "bk")}
    before = (bot / "config.php").read_bytes()
    assert "nothing replaced" in _install("--dry-run", str(bot), env=env).stdout and (bot / "config.php").read_bytes() == before
    assert "1 line put in" in _install(str(bot), env=env).stdout
    after = (bot / "config.php").read_bytes()
    assert after.count(b"// [no-agent-code]") == 1 and after.count(b"\r\n") == before.count(b"\r\n") + 1 and after.replace(b"\r\n", b"").count(b"\n") == 0
    text = after.decode("utf-8")
    assert text.index("$isAgent = ($userRow") < text.index("[no-agent-code]") < text.index("USED UP")      # (after the agent is known, before the code is used up)
    assert oct((bot / "config.php").stat().st_mode & 0o777) == "0o640" and any(p.read_bytes() == before for p in (tmp_path / "bk").glob("config.php.before-no-agent-code-*"))
    assert "already installed" in _install(str(bot), env=env).stdout
    assert "taken out" in _install("--uninstall", str(bot), env=env).stdout and (bot / "config.php").read_bytes() == before
    assert "not installed" in _install("--uninstall", str(bot), env=env).stdout


def test_the_installer_refuses_when_the_place_is_missing_or_twice(bot, tmp_path):
    env = {"BACKUP_DIR": str(tmp_path / "bk")}
    original = (bot / "config.php").read_bytes().decode("utf-8")
    anchor = "    $isAgent = ($userRow && isset($userRow['is_agent'])) ? (int)$userRow['is_agent'] : 0;\r\n"
    for broken in (original.replace("$isAgent = ", "$isAgent2 = "), original.replace(anchor, anchor * 2)):
        (bot / "config.php").write_bytes(broken.encode("utf-8"))
        before = (bot / "config.php").read_bytes()
        r = _install(str(bot), check=False, env=env)
        assert r.returncode == 3 and "not installing" in r.stderr and (bot / "config.php").read_bytes() == before


def _run(bot, agent):
    r = subprocess.run([PHP, "-d", "display_errors=1", "config.php"], capture_output=True, text=True, cwd=bot, env=dict(os.environ, AGENT=agent), check=False)
    return r.stdout.splitlines(), r.stderr


def test_an_agent_is_refused_before_the_code_is_used_up_and_a_customer_is_not_touched(bot, tmp_path):
    _install(str(bot), env={"BACKUP_DIR": str(tmp_path / "bk")})
    lines, err = _run(bot, "1")
    assert "USED UP" not in lines and err == ""                                  # (the use of the code is not taken)
    assert lines[0].startswith("SEND: ") and "همکار" in lines[0]                 # (one sentence for him)
    res = json.loads(lines[-1])
    assert res == {"success": False, "price": 130000, "hash_id": "h1", "discount_value": 0, "discount_text": "", "payInfo": {"hash_id": "h1", "price": 130000}}      # (the shape of every other refusal of the function)
    for agent in ("0", "", "2"):
        lines, _ = _run(bot, agent)
        assert "USED UP" in lines and json.loads(lines[-1])["success"] is True and not any(ln.startswith("SEND") for ln in lines), agent      # (a customer who is not an agent: exactly as before)
