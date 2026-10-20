"""The Create window (1.69.52): the owner, 2026-10-20, with pictures of a build that ended well: "if the tunnel succeeded, the last message must say the tunnel is established - done" and "after the build, the tick of the replacement goes by itself,
because it is replaced"."""
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")


def test_a_build_or_create_that_succeeded_ends_with_the_tunnel_is_established():
    story = APP.split("function storyHtml(s) {", 1)[1].split("\n}\n", 1)[0]
    assert '["build", "create"].includes(s.action) && s.ok && !s.dry_run ? "Tunnel established - done."' in story
    assert 'did not succeed' in story and '"Create"' in story          # a failed one still says what it says


def test_after_a_good_build_the_port_notice_and_the_replace_ticks_are_gone_and_the_next_change_checks_again():
    build = APP.split("window.startBuild = async e => {", 1)[1].split("window.openBuild", 1)[0] if "window.openBuild" in APP.split("window.startBuild = async e => {", 1)[1] else APP.split("window.startBuild = async e => {", 1)[1][:6000]
    assert "const done = await followStory(r.id, box, 600);" in build and "if (done && done.ok)" in build
    assert '$("#build-check")' in build and 'bk.ck = null; bk.ckKey = "";' in build and "/^(replace_|stale_|dns_follow)/" in build        # only the ticks of the replacement go; the options the owner set stay
    assert "bkBtn(form);" in build