"""The Persian language can be switched off with one flag (the owner, 2026-10-21: "not now, at the very end - switch the Persian language off for now"): `PERSIAN_ON` in i18n.js. Off: the page removes the
language buttons, a panel that was in Persian goes to English, the stylesheet hides the button before the script ran, and the Persian audit tests are skipped. The dictionaries and the engine are not touched."""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "nikatel" / "web"
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")


def test_one_flag_decides_and_the_page_obeys_it():
    assert "const PERSIAN_ON = " in I18N and I18N.count("const PERSIAN_ON = ") == 1
    load = I18N.split('document.addEventListener("DOMContentLoaded", () => {', 1)[1].split("});", 1)[0]
    assert 'document.documentElement.dataset.persian = PERSIAN_ON ? "on" : "off";' in load
    assert 'if (!PERSIAN_ON) { document.querySelectorAll(".lang-btn").forEach(b => b.remove());' in load and 'localStorage.setItem("nk.lang", "en")' in load
    assert load.index("if (!PERSIAN_ON)") < load.index("chrome();")                    # (English is chosen BEFORE the page is drawn in a language)


def test_the_stylesheet_hides_the_button_while_persian_is_off():
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert 'html[data-persian="off"] .lang-btn { display: none !important; }' in css


def test_the_audit_tests_follow_the_flag():
    for name in ("test_persian_audit.py", "test_ui_walk3.py"):
        src = (Path(__file__).parent / name).read_text(encoding="utf-8")
        assert '"const PERSIAN_ON = true;" in' in src, name
