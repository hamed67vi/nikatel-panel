"""A short message to the agent when the admin raises his discount (the owner, 2026-10-24, translated: "a short message must go to the user when we add a discount for him").

scripts/install-agent-discount-notice.sh puts two marked lines into bot.php and copies bot/menu/agent_discount_notice.php; these tests run the installer on a stand-in bot.php that holds the anchor lines exactly as the
bot has them, run the helper on its own with the bot's functions stubbed, and check that the installer lives together with the support-section installer. The whole bot (a copy of the real files, a private MariaDB
and a fake Telegram) is run by tests/support_section/bot_support.py, scenario AG1 (opt in, see tests/test_support_section.py)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-agent-discount-notice.sh"
SUPPORT_SCRIPT = ROOT / "scripts" / "install-support-section.sh"
HELPER = ROOT / "bot" / "menu" / "agent_discount_notice.php"
PHP, BASH, PY = shutil.which("php"), shutil.which("bash"), shutil.which("python3")
pytestmark = pytest.mark.skipif(not (PHP and BASH and PY), reason="php, bash and python3 are needed")

BOT = """<?php

$data = 'x'; $from_id = 1; $admin = 1; $message_id = 5; $userName = 'Ali';
$userInfo = ['step' => getenv("STEP") ?: 'none', 'isAdmin' => false];
$text = getenv("TEXT");
$mainValues = ['agent_discount_settings' => 'AGENT-NAME'];
$botState = json_decode(getenv("STATE") ?: '{}', true);
function sendMessage($t, $k = null, $p = null, $c = null) { echo "SEND to ", var_export($c, true), ": ", str_replace("\\n", " ", $t), "\\n"; }
function getAgentDiscounts($id) { return "[]"; }
if(preg_match('/^editAgentDiscount(Server|Plan|Normal)(\\d+)_(.*)/',$userInfo['step'],$match) && ($from_id == $admin || $userInfo['isAdmin'] == true) && $text != 'CANCEL'){
    if(is_numeric($text)){
        $info = ['discount_percent' => getenv("OLD")];
        $discountInfo = json_decode($info['discount_percent'],true);
        if($match[1] == "Server") $discountInfo['servers'][$match[3]] = $text;
        elseif($match[1] == "Plan") $discountInfo['plans'][$match[3]] = $text;
        elseif($match[1] == "Normal") $discountInfo['normal'] = $text;
        $text = json_encode($discountInfo);

        sendMessage("saved");
        sendMessage(str_replace("AGENT-NAME", $userName, $mainValues['agent_discount_settings']), getAgentDiscounts($match[2]));
        setUser();
    }else sendMessage("only a number");
}
echo "END\\n";
function setUser() {}
"""


def _install(script, *args, check=True, env=None):
    return subprocess.run([BASH, str(script), *args], capture_output=True, text=True, check=check, env=dict(os.environ, **(env or {})))


@pytest.fixture
def bot(tmp_path):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "bot.php").write_text(BOT, encoding="utf-8")
    os.chmod(d / "bot.php", 0o640)
    return d


def _env(tmp_path):
    return {"BACKUP_DIR": str(tmp_path / "bk")}


def test_the_installer_puts_two_marked_lines_and_takes_them_out_byte_for_byte(bot, tmp_path):
    env = _env(tmp_path)
    before = (bot / "bot.php").read_bytes()
    dry = _install(SCRIPT, "--dry-run", str(bot), env=env)
    assert "nothing replaced" in dry.stdout and (bot / "bot.php").read_bytes() == before and not (bot / "agent_discount_notice.php").exists()
    out = _install(SCRIPT, str(bot), env=env).stdout
    assert "2 line(s) put in" in out
    src = (bot / "bot.php").read_text(encoding="utf-8")
    assert src.count("// [agent-notice]") == 2 and "agDiscountNotice($match[1], $match[2], $match[3], $info['discount_percent'] ?? '', $discountInfo);" in src
    assert src.index("getAgentDiscounts($match[2]));") < src.index("agDiscountNotice(")      # (after the old lines of the handler, never in front of them)
    assert (bot / "agent_discount_notice.php").read_bytes() == HELPER.read_bytes() and oct((bot / "bot.php").stat().st_mode & 0o777) == "0o640"
    assert any(p.read_bytes() == before for p in (tmp_path / "bk").glob("bot.php.before-agent-notice-*"))
    assert "already installed" in _install(SCRIPT, str(bot), env=env).stdout
    assert "taken out" in _install(SCRIPT, "--uninstall", str(bot), env=env).stdout
    assert (bot / "bot.php").read_bytes() == before and not (bot / "agent_discount_notice.php").exists()
    assert "not installed" in _install(SCRIPT, "--uninstall", str(bot), env=env).stdout


def test_the_installer_refuses_a_bot_that_is_not_as_expected(bot, tmp_path):
    env = _env(tmp_path)
    original = (bot / "bot.php").read_text(encoding="utf-8")
    line = "        sendMessage(str_replace(\"AGENT-NAME\", $userName, $mainValues['agent_discount_settings']), getAgentDiscounts($match[2]));\n"
    for broken in (original.replace("getAgentDiscounts($match[2])", "getAgentDiscounts($match[1])"), original.replace(line, line * 2)):      # (the place is not there / it is there twice)
        (bot / "bot.php").write_text(broken, encoding="utf-8")
        before = (bot / "bot.php").read_bytes()
        r = _install(SCRIPT, str(bot), check=False, env=env)
        assert r.returncode in (1, 3) and (bot / "bot.php").read_bytes() == before and not (bot / "agent_discount_notice.php").exists(), r.stderr + r.stdout


def test_it_lives_together_with_the_support_section_installer(bot, tmp_path):
    """both put a line right under <?php; either can be taken out first and the file comes back byte for byte"""
    env = _env(tmp_path)
    (bot / "config.php").write_text("<?php\nfunction getMainKeys(){\n    $mainKeys = array_merge([], [\n        [['text'=>$buttonValues['application_links'],'callback_data'=>\"reciveApplications\"],['text'=>$buttonValues['my_info'],'callback_data'=>\"myInfo\"]],\n    ]);\n    return json_encode($mainKeys);\n}\n", encoding="utf-8")
    support = BOT.replace("if(preg_match('/^editAgentDiscount", "if($data==\"supportSection\"){\n    echo \"old\";\n}\nif($data==\"ticketsList\" and ($from_id == $admin || $userInfo['isAdmin'] == true)){\n    echo \"old\";\n}\nif(preg_match('/^sendMsg_(.*)/',$userInfo['step'],$match)  and $text!=$buttonValues['cancel']){\n    echo \"old\";\n}\nif(preg_match('/^editAgentDiscount", 1)
    (bot / "bot.php").write_text(support, encoding="utf-8")
    orig = {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")}
    _install(SUPPORT_SCRIPT, str(bot), env=env)
    _install(SCRIPT, str(bot), env=env)
    src = (bot / "bot.php").read_text(encoding="utf-8")
    assert src.count("// [support]") == 4 and src.count("// [agent-notice]") == 2
    for first in (SCRIPT, SUPPORT_SCRIPT):
        both = {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")}
        _install(first, "--uninstall", str(bot), env=env)
        _install(SUPPORT_SCRIPT if first == SCRIPT else SCRIPT, "--uninstall", str(bot), env=env)
        assert {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")} == orig
        _install(SUPPORT_SCRIPT, str(bot), env=env)
        _install(SCRIPT, str(bot), env=env)
        assert {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")} == both


def _run(bot, **env):
    r = subprocess.run([PHP, str(bot / "bot.php")], check=False, capture_output=True, text=True, cwd=bot, env=dict(os.environ, **env))
    return r.stdout + r.stderr


def test_the_installed_stand_in_tells_the_agent_only_when_the_discount_goes_up(bot, tmp_path):
    env = _env(tmp_path)
    _install(SCRIPT, str(bot), env=env)
    old = json.dumps({"normal": "10"})
    out = _run(bot, STEP="editAgentDiscountNormal77_0", TEXT="20", OLD=old)
    assert out.count("SEND to 77:") == 1 and "20" in out.split("SEND to 77:")[1] and out.count("SEND") == 3 and "END" in out      # (the admin's two lines are unchanged: "saved" and the discount screen)
    for text in ("10", "5", "0"):
        assert "SEND to 77" not in _run(bot, STEP="editAgentDiscountNormal77_0", TEXT=text, OLD=old), text      # (the same or a lower value: nothing)
    assert "SEND to 77" not in _run(bot, STEP="editAgentDiscountNormal77_0", TEXT="abc", OLD=old)               # (the old handler refuses it: nothing)
    assert "SEND to 77" not in _run(bot, STEP="editAgentDiscountNormal77_0", TEXT="20", OLD=old, STATE=json.dumps({"agentDiscountMsg": "off"}))
    assert "SEND to 77" in _run(bot, STEP="editAgentDiscountNormal77_0", TEXT="20", OLD=old, STATE=json.dumps({"agentDiscountMsg": "on"}))
    assert "SEND to 77" not in _run(bot, STEP="editAgentDiscountNormal77_0", TEXT="CANCEL", OLD=old)
    # the helper missing: the admin's flow is exactly the old one
    (bot / "agent_discount_notice.php").unlink()
    out = _run(bot, STEP="editAgentDiscountNormal77_0", TEXT="20", OLD=old)
    assert "SEND to 77" not in out and out.count("SEND") == 2 and "END" in out and "Fatal" not in out


def _php(code: str) -> object:
    r = subprocess.run([PHP, "-d", "display_errors=1", "-r", f"require {json.dumps(str(HELPER))};\n{code}"], check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_what_counts_as_a_higher_discount():
    got = _php("""$j = json_encode(['normal' => '10', 'servers' => ['3' => '25'], 'plans' => ['9' => '12']]);
echo json_encode([agDiscountValue($j, 'Normal', '0'), agDiscountValue($j, 'Server', '3'), agDiscountValue($j, 'Server', '4'), agDiscountValue($j, 'Plan', '9'), agDiscountValue($j, 'Plan', '8'), agDiscountValue('', 'Normal', '0'), agDiscountValue('not json', 'Server', '1'), agDiscountValue(['normal' => 7.5], 'Normal', '0'),
  agDiscountNumber(20.0), agDiscountNumber(7.5), agDiscountNumber(0), agDiscountNumber(12.25)]);""")
    assert got == [10.0, 25.0, 10.0, 12.0, 10.0, 0.0, 0.0, 7.5, "20", "7.5", "0", "12.25"]      # (a server / plan without a special value pays the general one: that is what he had before)


FAKE = r"""
class FakeRes { function __construct(public $row) {} function fetch_assoc() { return $this->row; } }
class FakeStmt { public $a; function __construct(public $sql) {} function bind_param($t, &...$a) { $this->a = $a; return true; } function execute() { return true; } function close() {}
  function get_result() { return new FakeRes(str_contains($this->sql, 'server_info') ? ['title' => 'Server <A&B>'] : (str_contains($this->sql, 'server_plans') ? ['title' => 'Plan 1'] : null)); } }
class FakeConn { function prepare($sql) { return new FakeStmt($sql); } }
$connection = new FakeConn(); $L = [];
function sendMessage($t, $k = null, $p = null, $c = null) { global $L; $L[] = [$c, $p, $t]; }
"""


def test_the_notice_goes_to_the_agent_with_the_right_words():
    got = _php(FAKE + """$botState = [];
$r = [agDiscountNotice('Normal', '77', '0', '{"normal":"10"}', ['normal' => '20']),
      agDiscountNotice('Server', '77', '3', '{"normal":"10"}', ['normal' => '10', 'servers' => ['3' => '30']]),
      agDiscountNotice('Plan', '77', '9', '{"normal":"10","plans":{"9":"15"}}', ['normal' => '10', 'plans' => ['9' => '40']]),
      agDiscountNotice('Normal', '77', '0', '{"normal":"10"}', ['normal' => '10']),
      agDiscountNotice('Normal', '77', '0', '{"normal":"10"}', ['normal' => '5']),
      agDiscountNotice('Server', '77', '3', '{"normal":"10","servers":{"3":"25"}}', ['normal' => '10', 'servers' => ['3' => '12']]),
      agDiscountNotice('Other', '77', '0', '{}', ['normal' => '50']), agDiscountNotice('Normal', 'x', '0', '{}', ['normal' => '50']), agDiscountNotice('Normal', '', '0', '{}', ['normal' => '50'])];
$botState = ['agentDiscountMsg' => 'off']; $r[] = agDiscountNotice('Normal', '77', '0', '{}', ['normal' => '50']);
echo json_encode([$r, $L]);""")
    results, sent = got
    assert results == [True, True, True, False, False, False, False, False, False, False]
    assert [s[0] for s in sent] == [77, 77, 77] and all(s[1] == "HTML" for s in sent)
    assert "20" in sent[0][2] and "Server &lt;A&amp;B&gt;" in sent[1][2] and "30" in sent[1][2] and "Plan 1" in sent[2][2] and "40" in sent[2][2]      # (a title is escaped for Telegram's HTML)
    assert all(len(s[2]) < 200 for s in sent)                                                                                                      # (short)


def test_the_notice_never_throws_into_the_admins_flow():
    got = _php("""function sendMessage() { throw new RuntimeException('telegram down'); } $botState = []; $connection = null;
echo json_encode([agDiscountNotice('Normal', '77', '0', '{}', ['normal' => '50']), agDiscountNotice('Server', '77', '3', '{}', ['normal' => '0', 'servers' => ['3' => '50']])]);""")
    assert got == [False, False]
