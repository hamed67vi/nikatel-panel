"""The support section of the shop bot (the owner, 2026-10-23, translated: "a support section in the bot's options with both a ticket and a direct chat with the operator; sometimes only the
ticket must be active; and in the ticket, when the customer writes again his message must not go up").

scripts/install-support-section.sh puts five marked lines into the bot's bot.php / config.php and copies bot/menu/support_section.php; these tests run the installer on stand-in files that hold the
anchor lines exactly as the bot has them, run the stand-in with the bot's functions stubbed, and run the helper functions on their own. The whole bot (a copy of the real files, a private MariaDB and a fake
Telegram) is run by tests/support_section/bot_support.py (opt in: NIKATEL_SUPPORT_E2E=1 NIKATEL_SUPPORT_BOT_COPY=<dir with a copy of the bot's files>)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-support-section.sh"
HELPER = ROOT / "bot" / "menu" / "support_section.php"
PHP, BASH, PY = shutil.which("php"), shutil.which("bash"), shutil.which("python3")
pytestmark = pytest.mark.skipif(not (PHP and BASH and PY), reason="php, bash and python3 are needed")

BOT = """<?php

$data = getenv("DATA"); $text = getenv("TEXT") === false ? null : getenv("TEXT"); $from_id = (int) getenv("FROM"); $admin = 1; $message_id = 5;
$userInfo = ['step' => getenv("STEP") ?: 'none', 'isAdmin' => false];
$buttonValues = ['back_button' => 'BACK', 'cancel' => 'CANCEL'];
$botState = json_decode(getenv("STATE") ?: '{}', true);
function editText($id, $txt, $kb = null) { echo "EDIT\\n"; echo "KB ", $kb, "\\n"; }
function sendMessage($t, $k = null, $p = null, $c = null) { echo "SEND ", str_replace("\\n", " ", $t), "\\n"; }
function getMainKeys() { return "MAINKEYS"; }
function setUser($v = 'none', $f = 'step') { echo "SETUSER $v\\n"; }
function setSettings($f, $v) { global $botState; $botState[$f] = $v; echo "SETTING $f=$v\\n"; }
if($data=="supportSection"){
    editText($message_id,"OLD SUPPORT SCREEN",
        json_encode(['inline_keyboard'=>[
        [['text'=>"OLD",'callback_data'=>"usersNewTicket"]],
        [['text'=>$buttonValues['back_button'],'callback_data'=>"mainMenu"]]
        ]]));
}
if($data=="ticketsList" and ($from_id == $admin || $userInfo['isAdmin'] == true)){
    editText($message_id, "OLD TICKET MENU", "[]");
}
if(preg_match('/^sendMsg_(.*)/',$userInfo['step'],$match)  and $text!=$buttonValues['cancel']){
    echo "OLD HANDLER announces the message to the admins\\n";
}
echo "END\\n";
"""

CONFIG = """<?php
function getMainKeys(){
    global $buttonValues, $from_id, $admin;
    $mainKeys = array();
    $mainKeys = array_merge($mainKeys, [
        [['text'=>$buttonValues['sharj'],'callback_data'=>"increaseMyWallet"]],
        [['text'=>$buttonValues['application_links'],'callback_data'=>"reciveApplications"],['text'=>$buttonValues['my_info'],'callback_data'=>"myInfo"]],
        (($from_id != $admin) ? [['text'=>$buttonValues['invite_friends'],'callback_data'=>"inviteFriends"]] : []),
    ]);
    return json_encode(['inline_keyboard' => $mainKeys]);
}
"""


def _install(*args, check=True, env=None):
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, check=check, env=dict(os.environ, **(env or {})))


@pytest.fixture
def bot(tmp_path):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "bot.php").write_text(BOT, encoding="utf-8")
    (d / "config.php").write_text(CONFIG, encoding="utf-8")
    os.chmod(d / "bot.php", 0o640)
    return d


def _env(tmp_path):
    return {"BACKUP_DIR": str(tmp_path / "bk")}


def _buttons(out):
    kb = json.loads(next(ln for ln in out.splitlines() if ln.startswith("KB "))[3:])
    return [b for row in kb["inline_keyboard"] for b in row]


def _run_bot(d, **env):
    r = subprocess.run([PHP, str(d / "bot.php")], check=False, capture_output=True, text=True, cwd=d, env=dict(os.environ, **env))
    return r.stdout, r.stderr


# ----------------------------------------------------------------------------------------------- the installer
def test_the_installer_puts_five_marked_lines_and_takes_them_out_byte_for_byte(bot, tmp_path):
    env = _env(tmp_path)
    before = {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")}
    dry = _install("--dry-run", str(bot), env=env)
    assert "nothing replaced" in dry.stdout and (bot / "bot.php").read_bytes() == before["bot.php"] and not (bot / "support_section.php").exists()
    out = _install(str(bot), env=env).stdout
    assert "4 line(s) put in" in out and "1 line(s) put in" in out
    assert (bot / "bot.php").read_text(encoding="utf-8").count("// [support]") == 4 and (bot / "config.php").read_text(encoding="utf-8").count("// [support]") == 1
    assert (bot / "support_section.php").read_bytes() == HELPER.read_bytes()
    assert oct((bot / "bot.php").stat().st_mode & 0o777) == "0o640"                       # (the mode of the old file is kept)
    for n in ("bot.php", "config.php"):
        assert any(p.read_bytes() == before[n] for p in (tmp_path / "bk").glob(f"{n}.before-support-section-*"))      # (the old files are kept)
    again = _install(str(bot), env=env).stdout
    assert "already installed" in again and "no change" in again
    un = _install("--uninstall", str(bot), env=env).stdout
    assert "taken out" in un
    assert {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")} == before and not (bot / "support_section.php").exists()
    assert "not installed" in _install("--uninstall", str(bot), env=env).stdout


def test_the_installer_refuses_when_a_place_is_not_as_expected_and_changes_nothing(bot, tmp_path):
    env = _env(tmp_path)
    for name, old in (("bot.php", 'if($data=="supportSection"){\n'), ("bot.php", 'if(preg_match(\'/^sendMsg_(.*)/\',$userInfo[\'step\'],$match)  and $text!=$buttonValues[\'cancel\']){\n'), ("config.php", "'callback_data'=>\"myInfo\"]],\n")):
        original = (bot / name).read_text(encoding="utf-8")
        (bot / name).write_text(original.replace(old, old.replace("supportSection", "supportSectionX").replace("sendMsg_", "sendMsgX_").replace("myInfo", "myInfoX"), 1), encoding="utf-8")
        before = {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")}
        r = _install(str(bot), check=False, env=env)
        assert r.returncode == 3 and "not installing" in r.stderr, (name, r.stderr)
        assert {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")} == before and not (bot / "support_section.php").exists()
        (bot / name).write_text(original, encoding="utf-8")
    (bot / "bot.php").write_text("<?php\n" + BOT.split("<?php\n", 1)[1] + BOT.split("<?php\n", 1)[1], encoding="utf-8")      # (every place twice)
    assert _install(str(bot), check=False, env=env).returncode == 3


def test_a_half_installed_state_is_completed_and_a_broken_php_file_is_not_touched(bot, tmp_path):
    env = _env(tmp_path)
    _install(str(bot), env=env)
    cfg = (bot / "config.php").read_text(encoding="utf-8").replace(" // [support]", "")
    (bot / "config.php").write_text(cfg, encoding="utf-8")                      # (bot.php has its lines, config.php lost its row)
    out = _install(str(bot), env=env).stdout
    assert "bot.php: already installed" in out and "config.php: 1 line(s) put in" in out
    assert (bot / "bot.php").read_text(encoding="utf-8").count("// [support]") == 4 and (bot / "config.php").read_text(encoding="utf-8").count("// [support]") == 1
    _install("--uninstall", str(bot), env=env)
    (bot / "bot.php").write_text(BOT.rstrip("\n").rstrip(";"), encoding="utf-8")      # (a missing semicolon at the end: php -l must stop it)
    before = (bot / "bot.php").read_bytes()
    r = _install(str(bot), check=False, env=env)
    assert r.returncode != 0 and "php -l failed" in r.stdout and (bot / "bot.php").read_bytes() == before and not (bot / "support_section.php").exists()

def test_crlf_files_stay_crlf(bot, tmp_path):
    env = _env(tmp_path)
    for n in ("bot.php", "config.php"):
        (bot / n).write_bytes((bot / n).read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    before = {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")}
    _install(str(bot), env=env)
    assert b"\r\n" in (bot / "bot.php").read_bytes() and b"[support]" in (bot / "bot.php").read_bytes()
    assert (bot / "bot.php").read_bytes().replace(b"\r\n", b"").count(b"\n") == 0
    _install("--uninstall", str(bot), env=env)
    assert {n: (bot / n).read_bytes() for n in ("bot.php", "config.php")} == before


# ----------------------------------------------------------------------------------------------- the installed stand-in
def test_the_new_screens_answer_and_the_old_handlers_step_aside(bot, tmp_path):
    env = _env(tmp_path)
    _install(str(bot), env=env)
    out, err = _run_bot(bot, DATA="supportSection", FROM="7", STATE=json.dumps({"supportID": "@op_chat"}))
    flat = _buttons(out)
    assert out.count("EDIT") == 1 and "usersNewTicket" in [b.get("callback_data") for b in flat] and [b["url"] for b in flat if "url" in b] == ["https://t.me/op_chat"] and "END" in out, (out, err)      # (one screen: the new one, not the old one too)
    out, _ = _run_bot(bot, DATA="supportSection", FROM="7", STATE=json.dumps({"supportID": "@op_chat", "supportChat": "off"}))
    assert not [b for b in _buttons(out) if "url" in b] and "usersNewTicket" in [b.get("callback_data") for b in _buttons(out)]
    out, _ = _run_bot(bot, DATA="ticketsList", FROM="1", STATE="{}")             # (an admin)
    assert out.count("EDIT") == 1 and "toggleSupportChat" in out and "managePanel" in out
    out, _ = _run_bot(bot, DATA="toggleSupportChat", FROM="1", STATE="{}")
    kb = json.loads(next(ln for ln in out.splitlines() if ln.startswith("KB "))[3:])
    assert "SETTING supportChat=off" in out and out.count("EDIT") == 1 and "❌" in kb["inline_keyboard"][2][0]["text"]
    out, _ = _run_bot(bot, DATA="toggleSupportChat", FROM="1", STATE=json.dumps({"supportChat": "off"}))
    assert "SETTING supportChat=on" in out
    out, _ = _run_bot(bot, DATA="toggleSupportChat", FROM="7", STATE="{}")         # (a customer: nothing is switched, the old code answers nothing either)
    assert "SETTING" not in out and "END" in out
    cfg = subprocess.run([PHP, "-d", "display_errors=1", "-r", "$buttonValues = ['sharj' => 'A', 'application_links' => 'B', 'my_info' => 'C', 'invite_friends' => 'D']; $from_id = 1; $admin = 1; include 'config.php'; echo getMainKeys();"], check=False, capture_output=True, text=True, cwd=bot).stdout
    rows = json.loads(cfg)["inline_keyboard"]
    flat = [b["callback_data"] for r in rows for b in r]
    assert flat.index("supportSection") == flat.index("myInfo") + 1 and next(b["text"] for r in rows for b in r if b["callback_data"] == "supportSection") != ""      # (the row under "apps / my info")


def test_a_follow_up_in_a_ticket_does_not_reach_the_old_handler_but_a_first_message_does(bot, tmp_path):
    """the stand-in has no database: the helper's own queries are checked below with a fake connection; here only that the old handler is skipped when the helper says done"""
    env = _env(tmp_path)
    _install(str(bot), env=env)
    # without a connection the helper cannot decide (it returns false): the old handler runs as before
    out, _ = _run_bot(bot, STEP="sendMsg_7", FROM="7", TEXT="hello")
    assert "OLD HANDLER" in out and "END" in out
    # the plain text of the installed line
    assert "supQuietFollowUp()) $userInfo['step'] = 'none'; // [support]" in (bot / "bot.php").read_text(encoding="utf-8")


# ----------------------------------------------------------------------------------------------- the helper on its own
def _php(code: str) -> object:
    r = subprocess.run([PHP, "-d", "display_errors=1", "-r", f"require {json.dumps(str(HELPER))};\n{code}"], check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)

def test_the_operator_link_is_only_a_link_telegram_accepts():
    got = _php("""foreach (['@op_chat', 'op_chat', 'https://t.me/op_chat', 'http://t.me/+AbC123', ' @op_chat ', '', 'ab', '@a b', 'https://t.me/', 'javascript:alert(1)', '@1abc', 'https://evil.example/op_chat', 'tg://resolve?domain=x'] as $s) $o[$s] = supChatUrl(['supportID' => $s]); $o['none'] = supChatUrl([]); echo json_encode($o);""")
    assert got["@op_chat"] == got["op_chat"] == got[" @op_chat "] == "https://t.me/op_chat"
    assert got["https://t.me/op_chat"] == "https://t.me/op_chat" and got["http://t.me/+AbC123"] == "http://t.me/+AbC123"
    for bad in ("", "ab", "@a b", "https://t.me/", "javascript:alert(1)", "@1abc", "https://evil.example/op_chat", "tg://resolve?domain=x", "none"):
        assert got[bad] == "", bad


def test_the_screen_rows_and_the_switch():
    got = _php("""$bv = ['back_button' => 'BACK'];
$on = supScreenRows(['supportID' => '@op_chat'], $bv); $off = supScreenRows(['supportID' => '@op_chat', 'supportChat' => 'off'], $bv); $nolink = supScreenRows(['supportID' => ''], $bv);
$adm = supAdminRows([], $bv); $adm2 = supAdminRows(['supportChat' => 'off'], $bv);
echo json_encode([$on, $off, $nolink, $adm, $adm2, supChatOn([]), supChatOn(['supportChat' => 'on']), supChatOn(['supportChat' => 'off'])]);""")
    on, off, nolink, adm, adm2, d, o, f = got
    cb = lambda rows: [b.get("callback_data") for r in rows for b in r if "callback_data" in b]
    assert cb(on) == ["usersNewTicket", "usersOpenTickets", "userAllTickets", "mainMenu"] and on[-2][0]["url"] == "https://t.me/op_chat"      # (the chat button sits above the way back)
    assert cb(off) == cb(nolink) == cb(on) and not [b for r in off for b in r if "url" in b] and not [b for r in nolink for b in r if "url" in b]
    assert cb(adm) == ["openTickets", "newTickets", "allTickets", "ticketsCategory", "toggleSupportChat", "managePanel"]
    assert "✅" in adm[2][0]["text"] and "❌" in adm2[2][0]["text"] and (d, o, f) == (True, True, False)


FAKE_DB = r"""
class FakeRes { function __construct(public $row) {} function fetch_assoc() { return $this->row; } }
class FakeStmt {
  public $args = [];
  function __construct(public $sql) {}
  function bind_param($t, &...$a) { $this->args = $a; return true; }
  function execute() { global $LOG; if (str_starts_with(trim($this->sql), "INSERT")) $LOG[] = ["insert", $this->args]; return true; }
  function get_result() { global $cfg; if (str_contains($this->sql, "FROM `chats` WHERE")) return new FakeRes($cfg["chat"]); if (str_contains($this->sql, "FROM `chats_info`")) return new FakeRes($cfg["last"]); return new FakeRes(null); }
  function close() {}
}
class FakeConn { function prepare($sql) { return new FakeStmt($sql); } }
$LOG = [];
$connection = new FakeConn();
$userInfo = ['step' => $cfg['step']]; $from_id = $cfg['from']; $buttonValues = ['cancel' => 'CANCEL', 'back_button' => 'BACK'];
if (array_key_exists('text', $cfg)) $text = $cfg['text'];
if (isset($cfg['fileid'])) { $fileid = $cfg['fileid']; $caption = $cfg['caption'] ?? null; }
function sendMessage($t, $k = null, $p = null) { global $LOG; $LOG[] = ["send", $t, $k]; }
function getMainKeys() { return "KEYS"; }
function setUser($v) { global $LOG; $LOG[] = ["setUser", $v]; }
"""


def _quiet(**cfg):
    base = {"step": "sendMsg_7", "from": 5, "chat": {"user_id": 5, "state": 0}, "last": {"msg_type": "USER"}, "text": "hi"}
    base.update(cfg)
    for k in [k for k, v in base.items() if v is KeyError]:
        del base[k]
    code = f"$cfg = json_decode({json.dumps(json.dumps(base))}, true);\n{FAKE_DB}\necho json_encode([supQuietFollowUp(), $LOG]);"
    r = subprocess.run([PHP, "-d", "display_errors=1", "-r", f"require {json.dumps(str(HELPER))};\n{code}"], check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_a_follow_up_is_saved_quietly_when_the_last_message_is_the_customers_own():
    done, log = _quiet()
    assert done is True
    ins = [x for x in log if x[0] == "insert"]
    assert len(ins) == 1 and ins[0][1][0] == 7 and ins[0][1][2] == "hi" and isinstance(ins[0][1][1], int)       # (ticket 7, the time, the text as it was written - no backslashes)
    assert next(x for x in log if x[0] == "send")[1:] == ["پیام شما با موفقیت ثبت شد.", "KEYS"] and ["setUser", "none"] in log
    done, log = _quiet(text=KeyError, fileid="F1", caption="see")                                                # (a photo: the bot's own {file_id, caption} form)
    assert done is True and json.loads(next(x for x in log if x[0] == "insert")[1][2]) == {"file_id": "F1", "caption": "see"}
    done, log = _quiet(text="it's a \"quote\" / # test")
    assert next(x for x in log if x[0] == "insert")[1][2] == "it's a \"quote\" / # test"


def test_everything_else_is_left_to_the_old_handler():
    for what, cfg in (("the support answered last", {"last": {"msg_type": "ADMIN"}}), ("no message in the ticket", {"last": None}), ("somebody else's ticket", {"chat": {"user_id": 6, "state": 0}}),
                      ("a closed ticket", {"chat": {"user_id": 5, "state": 2}}), ("no such ticket", {"chat": None}), ("the cancel button", {"text": "CANCEL"}), ("not in a ticket", {"step": "none"}),
                      ("a step of another flow", {"step": "sendMsg_x"}), ("a step that only starts like it", {"step": "xsendMsg_7"}), ("nothing to save", {"text": KeyError})):
        done, log = _quiet(**cfg)
        assert done is False and log == [], what


def test_the_admin_menu_flips_the_switch_for_admins_only():
    code = """$botState = ['supportChat' => 'on']; $buttonValues = ['back_button' => 'BACK']; $message_id = 3; $admin = 1; $userInfo = ['isAdmin' => false]; $L = [];
function editText($id, $t, $k) { global $L; $L[] = ['edit', $id, $k]; }
function setSettings($f, $v) { global $botState, $L; $botState[$f] = $v; $L[] = ['set', $f, $v]; }
$data = 'toggleSupportChat'; $from_id = 1; $a = supHandleAdminMenu();
$from_id = 9; $b = supHandleAdminMenu();
$data = 'ticketsList'; $from_id = 1; $c = supHandleAdminMenu();
$userInfo['isAdmin'] = true; $from_id = 9; $d = supHandleAdminMenu();
echo json_encode([$a, $b, $c, $d, $botState, $L]);"""
    a, b, c, d, state, log = _php(code)
    assert (a, b, c, d) == (True, False, True, True) and state == {"supportChat": "off"} and [x for x in log if x[0] == "set"] == [["set", "supportChat", "off"]]
    edits = [x for x in log if x[0] == "edit"]
    assert len(edits) == 3 and "❌" in json.loads(edits[0][2])["inline_keyboard"][2][0]["text"]


def test_the_helper_never_throws_into_the_bot():
    got = _php("""function editText() { throw new RuntimeException('telegram down'); } $message_id = 1; $botState = []; $buttonValues = ['back_button' => 'B']; $data = 'ticketsList'; $from_id = 1; $admin = 1; $userInfo = [];
echo json_encode([supHandleScreen(), supHandleAdminMenu()]);""")
    assert got == [False, False]


# ----------------------------------------------------------------------------------------------- the Mini App follows the same two rules
MINI = ROOT / "bot" / "miniapp"


def test_the_mini_app_follows_the_switch_and_the_quiet_follow_up():
    api = (MINI / "api.php").read_text(encoding="utf-8")
    js = (MINI / "app.js").read_text(encoding="utf-8")
    assert "'support_chat' => on('supportChat', 'on')" in api
    case = api[api.index("case 'ticket_reply'"):api.index("case 'report'")]
    assert "$quiet = $last && (string) $last['msg_type'] === 'USER';" in case and "if (!$quiet) {" in case
    assert case.index("$quiet =") < case.index("INSERT INTO chats_info") < case.index("if (!$quiet)")      # (the rule is read BEFORE the new message is stored)
    assert "const chatOn = () => !!S.support && S.feat.support_chat !== false;" in js and js.count("chatOn()") == 3 and "${S.support ? `" not in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed here")
def test_the_mini_app_shows_the_operator_chat_only_while_it_is_switched_on():
    """the cards that open the operator's own chat (home / more, the ticket page) are drawn by default and gone with features.support_chat = false; the ticket page itself stays"""
    def run(mode):
        r = subprocess.run([shutil.which("node"), str(ROOT / "tests" / "miniapp_driver.js"), str(MINI / "app.js"), mode], capture_output=True, text=True, timeout=90, check=True)
        res = json.loads(r.stdout)
        assert "crash" not in res and res["errors"] == [], res
        return res["values"]["chat"]
    on, off = run("chat"), run("nochat")
    assert on["cards"] >= 3 and on["tickets"]
    assert off["cards"] == 0 and off["tickets"]

# ----------------------------------------------------------------------------------------------- the whole bot (opt in)
@pytest.mark.skipif(not os.environ.get("NIKATEL_SUPPORT_E2E") or not os.environ.get("NIKATEL_SUPPORT_BOT_COPY") or not shutil.which("mariadbd") or not shutil.which("openssl"),
                    reason="the whole-bot run needs NIKATEL_SUPPORT_E2E=1, NIKATEL_SUPPORT_BOT_COPY=<a copy of the bot's files with settings/values.php and settings/jdf.php>, mariadbd and openssl")
def test_the_whole_bot_with_the_support_section_finds_no_hole(tmp_path):
    harness = ROOT / "tests" / "support_section"
    env = dict(os.environ, SEC_SCRATCH=str(tmp_path / "sec"))
    subprocess.run(["bash", str(ROOT / "tests" / "miniapp_security" / "sec_setup_db.sh"), "start"], env=env, check=True, capture_output=True, timeout=300)
    try:
        r = subprocess.run(["python3", str(harness / "bot_support.py"), str(ROOT), os.environ["NIKATEL_SUPPORT_BOT_COPY"]], env=env, check=False, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0 and "after: 0 HOLE" in r.stdout, r.stdout[-3000:] + r.stderr[-500:]
    finally:
        subprocess.run(["bash", str(ROOT / "tests" / "miniapp_security" / "sec_setup_db.sh"), "stop"], env=env, check=False, capture_output=True, timeout=120)
