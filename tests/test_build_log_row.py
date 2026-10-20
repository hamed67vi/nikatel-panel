"""The log of the Create tunnel window is ONE row (1.69.45), not a box of 150 px: the owner, 2026-10-20, with a picture of the Create window whose log box covered the form: "the log must not push things up - put one row inside the box and show the log"."""
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"


def test_the_build_log_is_one_row_that_opens_on_a_click():
    css, app = (WEB / "app.css").read_text(encoding="utf-8"), (WEB / "app.js").read_text(encoding="utf-8")
    rule = next(x for x in css.splitlines() if x.startswith("#modal .bld-log { "))
    assert "height: 150px" not in rule and "max-height: 40px" in rule and "overflow: hidden" in rule                       # one row, never the old 150 px box
    assert "#modal .bld-log .fx-line { display: none; }" in css                                                          # every line is hidden ...
    assert "#modal .bld-log .fx-line:not(.run):not(:has(~ .fx-line:not(.run))) { display: flex;" in css                   # ... but the last step ...
    assert "#modal .bld-log .fx-line.run { display: flex; flex: none; padding: 0; font-size: 0; }" in css and "text-overflow: ellipsis" in css      # ... and the spinner of "Working…" stays
    assert "#modal .bld-log.open { display: block; max-height: 40vh; overflow: auto; }" in css and "#modal .bld-log.open .fx-line { display: flex; }" in css      # a click shows the whole log
    assert 'closest("#modal .bld-log")' in app and 'b.classList.toggle("open")' in app and 'id="build-story" class="fix bld-log" title="One line' in app