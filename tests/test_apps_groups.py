"""The apps screen of the shop bot by system (the owner, 2026-10-23: "the categories separate: iOS, Android, Windows" and "show the recommended apps; NPV and Karing among them for iPhone and Android").
scripts/install-apps-groups.sh changes two places of the bot's handler of "reciveApplications" and copies bot/menu/apps_groups.php; these tests run the installer on a stand-in bot.php that holds the handler
exactly as the bot has it (tab and trailing spaces included) and the new screens under php with the bot's functions stubbed."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-apps-groups.sh"
PHP, BASH, PY = shutil.which("php"), shutil.which("bash"), shutil.which("python3")
pytestmark = pytest.mark.skipif(not (PHP and BASH and PY), reason="php, bash and python3 are needed")

FAKE = """<?php
class FakeRes { public $i = 0; function __construct(public array $rows) {} function fetch_assoc() { return $this->rows[$this->i++] ?? null; } function data_seek($n) { $this->i = $n; } }
$data = getenv("DATA");
$buttonValues = ['back_to_main' => 'BACK'];
function editText($id, $text, $kb, $mode) { echo json_encode(json_decode($kb, true)['inline_keyboard'], JSON_UNESCAPED_UNICODE), "\\n"; }
$message_id = 1; $supportID = "x";
if($data == 'reciveApplications') {
    $respd = new FakeRes(json_decode(getenv("APPS"), true));

    $keyboard = [];§
    while($file =  $respd->fetch_assoc()){§
        $link = $file['link'];
        $title = $file['title'];
        $keyboard[] = ['text' => "$title", 'url' => $link];
    }
\t$keyboard[] = ['text'=>$buttonValues['back_to_main'],'callback_data'=>"mainMenu"];
    $keyboard = array_chunk($keyboard,1);§
    editText($message_id, "the text", json_encode(['inline_keyboard'=>$keyboard]), "html");
}
echo "END\\n";
?>
""".replace("§", " ")      # (the bot's file has a space at the end of three of these lines: the installer must match them as they are)

APPS = [
    {"title": "Happ برای iPhone / iOS (پیشنهادی)", "link": "https://apps.apple.com/app/happ/id1"},
    {"title": "V2Box برای iPhone / iOS", "link": "https://apps.apple.com/app/v2box/id2"},
    {"title": "Karing برای iPhone / iOS (پیشنهادی)", "link": "https://apps.apple.com/app/karing/id3"},
    {"title": "V2rayNG برای اندروید (پیشنهادی)", "link": "https://github.com/2dust/v2rayNG/releases/latest"},
    {"title": "NPV Tunnel برای اندروید (پیشنهادی)", "link": "https://play.google.com/store/apps/details?id=npv"},
    {"title": "v2rayN برای ویندوز (پیشنهادی)", "link": "https://github.com/2dust/v2rayN/releases/latest"},
    {"title": "Happ برای مک (macOS)", "link": "https://apps.apple.com/app/happ/id1"},
    {"title": "آموزش V2rayNG", "link": "https://t.me/x"},
    {"title": "دانلود نرم افزارهای IOS", "link": "https://t.me/y"},
]


def _run(*args, check=True):
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, check=check)


@pytest.fixture
def bot(tmp_path, monkeypatch):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "bot.php").write_text(FAKE, encoding="utf-8")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "bk"))
    return d


def _screen(bot_dir, data, apps=APPS):
    r = subprocess.run([PHP, str(bot_dir / "bot.php")], capture_output=True, text=True, env=dict(os.environ, DATA=data, APPS=json.dumps(apps)), check=True)
    lines = r.stdout.strip().splitlines()
    assert lines and lines[-1] == "END", r.stdout + r.stderr
    return json.loads(lines[0]) if len(lines) > 1 else None


def _texts(kb):
    return [row[0]["text"] for row in kb]


def test_the_installer_changes_two_places_once_and_puts_them_back_byte_for_byte(bot, tmp_path):
    before = (bot / "bot.php").read_bytes()
    assert "nothing replaced" in _run("--dry-run", str(bot)).stdout and (bot / "bot.php").read_bytes() == before and not (bot / "apps_groups.php").exists()
    assert "two places changed" in _run(str(bot)).stdout
    text = (bot / "bot.php").read_text(encoding="utf-8")
    assert text.count("// [apps-groups]") == 2 and "// [/apps-groups]" in text and (bot / "apps_groups.php").is_file()
    assert (next((tmp_path / "bk").iterdir())).read_bytes() == before
    assert "already installed" in _run(str(bot)).stdout
    assert "back as they were" in _run("--uninstall", str(bot)).stdout
    assert (bot / "bot.php").read_bytes() == before and not (bot / "apps_groups.php").exists()
    assert "not installed" in _run("--uninstall", str(bot)).stdout


def test_the_installer_refuses_a_handler_it_does_not_know_and_a_file_that_does_not_parse(bot):
    (bot / "bot.php").write_text(FAKE.replace("while($file =  $respd", "while($file = $respd"), encoding="utf-8")
    r = _run(str(bot), check=False)
    assert r.returncode == 3 and "// [apps-groups]" not in (bot / "bot.php").read_text(encoding="utf-8") and not (bot / "apps_groups.php").exists()
    (bot / "bot.php").write_text(FAKE.replace("?>", "function broken( {\n?>"), encoding="utf-8")
    assert _run(str(bot), check=False).returncode != 0


def test_the_installer_keeps_windows_line_ends(bot):
    (bot / "bot.php").write_bytes(FAKE.replace("\n", "\r\n").encode())
    before = (bot / "bot.php").read_bytes()
    _run(str(bot))
    assert b"[apps-groups]" in (bot / "bot.php").read_bytes()
    _run("--uninstall", str(bot))
    assert (bot / "bot.php").read_bytes() == before


def test_the_first_screen_has_one_button_per_system_and_the_rows_without_a_system_as_they_were(bot):
    flat = _screen(bot, "reciveApplications")
    assert len(flat) == len(APPS) + 1                                                    # before: the flat list + back
    _run(str(bot))
    kb = _screen(bot, "reciveApplications")
    assert _texts(kb) == ["📱 آیفون / iOS (4)", "🤖 اندروید (2)", "🪟 ویندوز (1)", "💻 مک / macOS (1)", "آموزش V2rayNG", "BACK"]
    assert [r[0].get("callback_data") for r in kb[:4]] == ["reciveApplications_ios", "reciveApplications_android", "reciveApplications_windows", "reciveApplications_mac"]
    assert kb[4][0]["url"] == "https://t.me/x" and kb[-1][0]["callback_data"] == "mainMenu"      # ("دانلود نرم افزارهای IOS" names iOS: it is in the iOS group)


def test_a_system_opens_its_apps_with_the_recommended_ones_first_and_a_way_back(bot):
    _run(str(bot))
    ios = _screen(bot, "reciveApplications_ios")
    assert _texts(ios) == ["⭐ Happ برای iPhone / iOS", "⭐ Karing برای iPhone / iOS", "V2Box برای iPhone / iOS", "دانلود نرم افزارهای IOS", "🔙 دسته‌بندی برنامه‌ها", "BACK"]
    assert ios[0][0]["url"] == "https://apps.apple.com/app/happ/id1" and ios[4][0]["callback_data"] == "reciveApplications"
    android = _screen(bot, "reciveApplications_android")
    assert _texts(android)[:2] == ["⭐ V2rayNG برای اندروید", "⭐ NPV Tunnel برای اندروید"]                 # NPV Tunnel and Karing are among the recommended on iPhone and Android
    assert _texts(_screen(bot, "reciveApplications_windows"))[0] == "⭐ v2rayN برای ویندوز" and _texts(_screen(bot, "reciveApplications_mac"))[0] == "Happ برای مک (macOS)"
    assert _screen(bot, "reciveApplications_other") is None and _screen(bot, "something") is None      # anything else is not this handler
    assert _screen(bot, "reciveApplications_ios", apps=[]) == [[{"text": "🔙 دسته‌بندی برنامه‌ها", "callback_data": "reciveApplications"}], [{"text": "BACK", "callback_data": "mainMenu"}]]
    assert _texts(_screen(bot, "reciveApplications", apps=[])) == ["BACK"]


def test_when_the_new_file_fails_the_old_flat_list_is_shown(bot):
    _run(str(bot))
    (bot / "apps_groups.php").write_text("<?php\nfunction shopAppsKeyboard( {\n", encoding="utf-8")          # a parse error at require time
    kb = _screen(bot, "reciveApplications")
    assert len(kb) == len(APPS) + 1 and _texts(kb)[0] == "Happ برای iPhone / iOS (پیشنهادی)" and kb[0][0]["url"] == "https://apps.apple.com/app/happ/id1"
    (bot / "apps_groups.php").write_text("<?php\nfunction shopAppsKeyboard($r, $d) { throw new RuntimeException('x'); }\n", encoding="utf-8")
    assert len(_screen(bot, "reciveApplications")) == len(APPS) + 1
