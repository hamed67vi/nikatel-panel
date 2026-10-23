"""The replacement card of the build form (the owner, 2026-10-24, on "remove it first and replace it" and "then point the CDN records of the replaced tunnel at the new Iran server" shown as two separate lines: "I did not
understand - combine these options so they are not mistaken"): ONE card - what is found, the replacement as one tick per tunnel, and under it, indented, the CDN names; the CDN tick is grey while no replacement is ticked."""
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
APP, CSS = (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "lab.css").read_text(encoding="utf-8")


def test_the_replacement_and_the_cdn_names_are_one_card():
    fn = APP.split("function bkCheckRender(form) {", 1)[1].split("/* the Build button waits", 1)[0]
    assert 'class="bconf bgroup"' in fn and '<div class="bg-h">' in fn and "through another tunnel</div>" in fn
    conf = next(ln for ln in fn.splitlines() if ln.lstrip().startswith("const conf = "))
    assert conf.index('name="replace_${t.id}"') < conf.index("${dns}") < conf.rindex("</div>")                         # the CDN tick is INSIDE the card, after the replacement ticks
    assert 'class="chk sub"' in fn and "And move its CDN names to the new Iran server" in fn
    assert "then point the CDN records" not in APP and "remove it first and replace it" not in APP                    # the two old lines are gone
    assert "busy + stale + conf + (c.error" in fn and "conf + dns" not in fn                                              # it is not drawn a second time on its own
    assert "bkBtn(this.closest('form'))" in conf.split("${dns}", 1)[0]                                                     # ticking or unticking a replacement greys / un-greys the CDN tick


def test_the_cdn_tick_is_grey_while_no_replacement_is_ticked():
    fn = APP.split("window.bkBtn = form => {", 1)[1].split("window.bkKind", 1)[0]
    assert "input[name^=replace_]:checked" in fn and "d.disabled = !on" in fn and 'classList.toggle("off", !on)' in fn
    assert 'body.dns_follow = body.replace_ids.length > 0 && !!form.querySelector("input[name=dns_follow]:checked")' in APP          # (the server never gets "follow" without a replacement - as before)
    assert ".bconf.bgroup .chk.sub.off" in CSS and ".bconf.bgroup .bg-h" in CSS