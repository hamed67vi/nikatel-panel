"""A page that is opened never stays as the OLD page under the new header (the owner, 2026-10-31: "I opened Network and I am still in Servers"): the read has a time limit, and a page not drawn in 0.9 s is a loading box."""
from pathlib import Path

APP = (Path(__file__).resolve().parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")


def test_a_read_has_a_time_limit_and_a_silent_panel_is_a_transient_error():
    assert "const READ_MS = 60000;" in APP
    assert "if (!opts.slow) tm = setTimeout(() => { timedOut = true; own.abort(); }, READ_MS);" in APP
    assert 'if (e.name === "AbortError" && !timedOut) return new Promise(() => {});' in APP                       # (leaving the page still ends a read quietly)
    assert 'if (timedOut) e = new Error("The panel did not answer in time");' in APP
    assert "finally { clearTimeout(tm); if (gone) ctl.signal.removeEventListener(\"abort\", gone); }" in APP
    assert "Load failed|network|did not answer/i.test(e.message)" in APP                                           # (the page says so and asks again by itself)


def test_a_page_not_drawn_in_time_is_a_loading_box_not_the_old_page():
    assert "const staleGuard = () =>" in APP and "}, 900);" in APP.split("const staleGuard = () =>")[1].split("\n};")[0]
    assert "pg.firstElementChild !== first" in APP and "pg.innerHTML = loadingBox(state.page);" in APP
    assert "hideModal(); staleGuard();" in APP                                                                      # (a page was opened)
    assert "staleGuard(); }      // (another tab of the page that is open" in APP                                  # (a tab of the open page by the menu)
    assert "if (m) m.scrollTop = 0; staleGuard(); }\n  subSet(page, k); refresh();" in APP                         # (a tab pressed)
