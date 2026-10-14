"""The left menu is plain HTML (web/index.html) and the title of every page is set in app.js (INFO.<page> = [...]): a typo in a data-page makes an item that opens the dashboard and no browser
test of ours would notice, so both are read here. The Panel group (the owner's wish, 1.46.1: like the sidebar of PasarGuard) is the head Panel with the sections Users, Hosts, Access, Core
and Admins under it, in that order, and the arrow that opens it."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
NAV = INDEX.split('<nav id="nav">', 1)[1].split("</nav>", 1)[0]
GROUP = NAV.split('<div class="nav-g"', 1)[1].split('<a data-page="bot"', 1)[0]
LINK = re.compile(r'<a data-page="([a-z]+)"[^>]*>.*?<span>([^<]+)</span></a>', re.DOTALL)


def _titled_pages() -> set[str]:
    """The pages that have a title: the entries of the INFO object and the later `INFO.<page> = [...]` lines."""
    block = APP.split("const INFO = {", 1)[1].split("\n};", 1)[0]
    return set(re.findall(r"^  ([a-z]+): \[", block, re.MULTILINE)) | set(re.findall(r"^INFO\.([a-z]+) = \[", APP, re.MULTILINE))


def test_every_item_of_the_menu_opens_a_page_that_has_a_title():
    items = LINK.findall(NAV)
    assert [p for p, _ in items][:3] == ["dashboard", "nodes", "tunnels"] and items[-1][0] == "settings"
    lacking = [p for p, _ in items if p not in _titled_pages()]
    assert not lacking, f"items of the menu whose page has no title in app.js: {lacking}"


def test_the_panel_group_is_the_head_panel_with_its_five_sections_under_it():
    head, sub = GROUP.split('<div class="nav-sub"', 1)
    assert [(p, t) for p, t in LINK.findall(head)] == [("pgpanel", "Panel")]
    assert [(p, t) for p, t in LINK.findall(sub)] == [("configs", "Users"), ("pghosts", "Hosts"), ("pgaccess", "Access"), ("pgcore", "Core"), ("pgadmins", "Admins")]
    assert 'class="nav-tg"' in head and 'aria-controls="nav-sub"' in head and 'id="nav-sub"' in sub          # the arrow that opens and closes it, and what it controls


def test_the_group_script_knows_the_same_pages_as_the_menu():
    names = re.search(r"const NAV_GROUP = \[([^\]]*)\]", APP)
    assert names, "app.js has no NAV_GROUP"
    assert re.findall(r'"([a-z]+)"', names.group(1)) == ["pgpanel", "configs", "pghosts", "pgaccess", "pgcore", "pgadmins"]
