"""The Iran list of the build form has no "Automatic" heading and no "✨ Smart" entry any more (the owner, 2026-11-13: "this option must be removed entirely and not shown in the panel any more").
The page code is read as text, like the other web tests."""
import subprocess
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")


def test_the_iran_list_has_no_smart_entry_and_no_automatic_heading():
    assert "✨ Smart" not in APP
    assert 'optgroup label="Automatic"' not in APP


def test_the_iran_list_is_still_built_from_the_iran_servers():
    assert 'name="iran_id" required data-dd' in APP
    assert '<optgroup label="Iran servers">' in APP


def test_no_translation_of_the_old_entry_is_left_behind():
    for f in WEB.glob("i18n*.js"):
        text = f.read_text(encoding="utf-8")
        assert "✨ Smart" not in text, f.name
        assert "Smart test: starting" not in text, f.name


def test_a_test_without_an_iran_server_asks_for_one_instead_of_a_smart_test():
    assert "Smart test: starting" not in APP
    assert "Choose an Iran server first." in APP


def test_the_auto_link_beside_the_word_iran_stays():
    assert "autoIran(this.closest('form'))" in APP


def test_the_page_code_still_parses():
    r = subprocess.run(["node", "--check", str(WEB / "app.js")], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
