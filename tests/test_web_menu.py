"""The left menu is plain HTML (web/index.html) plus the groups that app.js builds from the tabs of a page (navBuild: Servers, Tunnels, Panel and Settings), and the title of every page is set in
app.js (INFO.<page> = [...]): a typo in a data-page makes an item that opens the dashboard and no browser test of ours would notice, so the files are read here. The Panel page (the owner's wish,
1.47.0: "when we click Panel, show everything as tabs") has a tab for each section of PasarGuard - General, Users, Hosts, Access, Core, Admins - and every tab of a pg_*.js file has a place in it.
The nodes of a PasarGuard panel are not a tab of the Panel page nor of Servers any more (1.48.0): they are in the window of the server they run on."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
NAV = INDEX.split('<nav id="nav">', 1)[1].split("</nav>", 1)[0]
LINK = re.compile(r'<a data-page="([a-z]+)"[^>]*>.*?<span>([^<]+)</span></a>', re.DOTALL)


def _titled_pages() -> set[str]:
    """The pages that have a title: the entries of the INFO object and the later `INFO.<page> = [...]` lines."""
    block = APP.split("const INFO = {", 1)[1].split("\n};", 1)[0]
    return set(re.findall(r"^  ([a-z]+): \[", block, re.MULTILINE)) | set(re.findall(r"^INFO\.([a-z]+) = \[", APP, re.MULTILINE))


def _tabs(page: str) -> list[tuple[str, str]]:
    """The tabs of `SUB.<page> = [[key, label, ...], ...]` (the ones written as a list of lists, one tab to a line)."""
    block = APP.split(f"SUB.{page} = [", 1)[1].split("\n];", 1)[0]
    return re.findall(r'^\s*\["([a-z]+)", "([^"]+)"', block, re.MULTILINE)


def test_every_item_of_the_menu_opens_a_page_that_has_a_title():
    items = LINK.findall(NAV)
    assert [p for p, _ in items] == ["dashboard", "nodes", "tunnels", "pgpanel", "bot", "settings"]
    lacking = [p for p, _ in items if p not in _titled_pages()]
    assert not lacking, f"items of the menu whose page has no title in app.js: {lacking}"


def test_the_panel_page_has_a_tab_for_each_section_of_pasarguard_in_the_order_of_the_owner():
    assert _tabs("pgpanel") == [("settings", "General"), ("users", "Users"), ("hosts", "Hosts"), ("access", "Access"), ("core", "Core"), ("admins", "Admins")]      # General (the settings of the panel) is the first one: the owner, 2026-10-14
    assert "PAGES.pgpanel = async () => { if (window.labLockNow && await labLockNow()) return; renderSub(); return PAGES.panels(); }" in APP          # (the lock of the Panel is the first thing it asks)
    assert "settings: SI.cog}" in APP and "cog: '<svg" in APP          # (and its icon is a cog wheel, not the sun of SI.gear)


def test_the_servers_page_has_no_nodes_tab_any_more_the_nodes_are_in_the_server_window():
    block = APP.split("const SUB = {", 1)[1].split("\n};", 1)[0]
    assert "pgnodes" not in block and "pgTabs.nodes" not in "".join(f.read_text(encoding="utf-8") for f in WEB.glob("pg_*.js"))


def test_the_old_pages_of_the_sections_still_open_their_tab():
    alias = APP.split("const NAV_ALIAS = {", 1)[1].split("};", 1)[0]
    for page, tab in (("pghosts", "hosts"), ("pgaccess", "access"), ("pgcore", "core"), ("pgadmins", "admins")):
        assert f'{page}: "pgpanel/{tab}"' in alias
    assert 'panels: "pgpanel/users", configs: "pgpanel/users"' in APP          # (while the Configs list is off: the Users of the panels are the first tab of the Panel page)


def test_every_tab_a_pg_file_registers_has_a_place_on_the_panel_page_or_in_servers():
    subs = re.search(r"const PG_SUBS = \{(.*?)\};", APP, re.DOTALL)
    assert subs, "app.js has no PG_SUBS"
    placed = set(re.findall(r'"([a-z]+)"', subs.group(1)))
    registered = set()

    for f in WEB.glob("pg_*.js"):
        registered |= set(re.findall(r"pgTabs\.([a-z]+)\s*=", f.read_text(encoding="utf-8")))

    assert registered, "no pgTabs entry was found: the pattern is out of date"
    assert registered <= placed, f"tabs that no tab of the Panel page holds (they could not be opened): {sorted(registered - placed)}"


def test_the_groups_of_the_menu_are_built_from_the_tabs_of_their_pages():
    built = re.search(r'return \{nodes: of\("nodes"\), tunnels: of\("tunnels"\), pgpanel: of\("pgpanel"\), settings: Object\.entries\(SET_TABS\)', APP)
    assert built, "navTabs() no longer returns nodes, tunnels, pgpanel and settings"
    menu = {p for p, _ in LINK.findall(NAV)}
    assert {"nodes", "tunnels", "pgpanel", "settings"} <= menu
    assert re.search(r"^const SUB = \{\n  nodes: \[\[", APP, re.MULTILINE) and re.search(r"^  tunnels: \[\[", APP, re.MULTILINE) and "const SET_TABS = {" in APP


def test_go_takes_the_tab_the_menu_asks_for():
    assert re.search(r"function go\(page, tab\)", APP) and "navMark(true)" in APP and "function subSet(page, k)" in APP and "function tabSet(k)" in APP

def test_a_tab_that_is_switched_off_is_in_the_menu_grey_and_cannot_be_clicked():
    """The owner asked to see the rest of the tabs of Tunnels: Outbound is switched off (roadmap #92) and shows grey, like the Shop bot, instead of missing from the group."""
    assert '.map(t => [t[0], navPlain(t[1]), ic[t[0]] || SI.doc, t[3] === "off" ? "off" : t[3] === "arch" ? "arch" : false])' in APP
    assert 'class="nav-off" aria-disabled="true"' in APP and 'sub.querySelectorAll("a:not(.nav-off)")' in APP          # (and a click on it does nothing)
    assert re.search(r'\["gateway", "[^"]*Outbound", INFO_GW, "off"\]', APP)


def test_the_lab_is_the_first_tab_of_servers_and_the_old_lists_are_archived_but_still_open():
    """The owner (2026-10-14): "make a test page" with profiles, and "archive the current Servers section". The Lab is the first tab, so Servers opens on it; Foreign and Iran are the
    same lists as before, marked "arch" (quiet, with a small mark, still clickable) - a tab marked "off" would be grey and dead."""
    block = APP.split("const SUB = {", 1)[1].split("\n};", 1)[0]
    tabs = re.findall(r'^  nodes: \[(.*)\],$', block, re.MULTILINE)[0]
    assert tabs.index('["lab",') < tabs.index('["foreign",') < tabs.index('["iran",')
    assert re.search(r'\["foreign", "[^\n]*?", "arch"\]', tabs) and re.search(r'\["iran", "[^\n]*?", "arch"\]', tabs)
    assert 'if (s === "lab" && PAGES.lab) return PAGES.lab();' in APP          # (and the old list when lab.js did not load)
    assert 'off === "arch" ? " sub-arch" : ""' in APP and 'class="nav-arch"' in APP
    assert 'go("nodes", s ? s.role : undefined)' in APP          # a shortcut to a server (the dashboard, a diagnosis) opens the tab of its role, never the Lab by mistake
    assert "go('nodes');state.tab=" not in APP


def test_the_section_that_was_called_tunnels_is_called_network_and_starts_with_the_cdn():
    """The owner (2026-10-14): "this section's name becomes Network", "bring CDN above the Tunnel manager". CDN is the first tab, so a shortcut that used to land on the Tunnel manager by
    being the first tab must now say which tab it wants (go(page, tab)), else it would draw the CDN first and the tab it wanted second."""
    assert '<span>Network</span>' in NAV and 'data-short="Network"' in INDEX and "<span>Tunnels</span>" not in NAV
    assert re.search(r'^  tunnels: \["Network", ', APP, re.MULTILINE)
    block = APP.split("const SUB = {", 1)[1].split("\n};", 1)[0]
    tabs = re.findall(r'^  tunnels: \[(.*)\],?(?:      //.*)?$', block, re.MULTILINE)[0]
    assert tabs.index('["dns", "🌐 CDN"') < tabs.index('["list", "⇄ Tunnel manager"') < tabs.index('["repairs"') < tabs.index('["gateway"')
    assert not re.search(r"""go\((['"])tunnels\1\)""", APP), "go('tunnels') with no tab opens the CDN: name the tab"
    assert 'actions: "tunnels/repairs"' in APP


def test_the_panel_item_is_grey_until_a_pasarguard_profile_exists_and_says_why_when_it_is_clicked():
    """The owner (2026-10-14): "until a PasarGuard profile is added the Panel section shows disabled; click it and it says a PasarGuard profile must be added first; for now full compatibility with the
    PasarGuard panel". The lock is in go() (every way into the Panel passes there), in the Panel page itself (a reload on it) and on the menu (a grey item that still answers a click)."""
    assert 'if (page === "pgpanel" && window.labLocked && labLocked()) { labLockMsg(); return; }' in APP
    assert "PAGES.pgpanel = async () => { if (window.labLockNow && await labLockNow()) return;" in APP
    assert "if (window.labLockSync) labLockSync();" in APP
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert "window.labLocked = () => LAB.pg === false" in lab and 'document.getElementById("demo-pill")' in lab          # (the public demo has no PasarGuard panel: its Panel page stays open)
    assert 'a.classList.toggle("nav-lock", lock)' in lab and "p.kind === \"node\"" in lab
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert "nav a.nav-lock" in css and "pointer-events" not in css.split("nav a.nav-lock", 1)[1].split("}", 1)[0]          # (unlike a switched-off item, a click on it must arrive)


def test_the_lab_has_the_cards_and_icons_of_the_old_list_and_one_menu_in_place_of_many_buttons():
    """The owner (2026-10-14): "the model of the cards and their icons as before, only the contents of the columns new", "the flags as before, for my Iran servers too", "instead of many buttons"
    (the picture of a single ⋯ menu), "much more compact"."""
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert '<table class="tbl lift srv-tbl joined lab-tbl">' in lab and "${SRV_IC}" in lab and 'class="srv-ic"' in lab          # the table, the server icon and the flag of the old list
    assert 'cc = s.role === "iran" ? "ir"' in lab and "has-flag" in lab                                                        # Iran has its flag too
    assert ".lab-root" in css and not re.search(r"^\.lab \{", css, re.MULTILINE)                                              # (".lab" is hidden by the fluid layer on a narrow window)
    assert "const labKebab" in lab and lab.count("labKebab(") >= 4 and "window.labMenu = " in lab and "n > 10" in lab         # one menu, two columns when it is long
    assert "labItem(\"Delete server…\"" in lab and "danger: true" in lab and ".lab-mi.danger" in css                         # the dangerous item is last and red
    assert 'class="sm abtn' not in lab and "wtools" not in lab                                                                # no row of buttons, no tiles of tools
    assert "const labKinds = " in lab and "labKinds().length > 1" in lab and "kinds.length > 1 ?" in lab                      # the tabs are not made in advance: a kind has its tab only while a profile of it exists
    assert ".lab-bar { container: labbar" in css and "flex-wrap: nowrap" in css.split(".lab-bar {", 1)[1].split("}", 1)[0]    # the bar is one row at every width
    assert [k for k, _ in re.findall(r'\["(overview|node|monitor|tools)", "(\w+)"\]', lab.split("const LAB_SECS", 1)[1].split("\n", 1)[0])] == ["overview", "node", "monitor"]


def test_the_lab_files_are_loaded_and_versioned_like_the_others():
    index, main = INDEX, (WEB.parent / "main.py").read_text(encoding="utf-8")
    assert '<script src="/static/lab.js"></script>' in index and '<link rel="stylesheet" href="/static/lab.css">' in index
    assert index.index("/static/app.js") < index.index("/static/pg_nodes.js") < index.index("/static/lab.js")          # (the Lab uses the helpers of app.js and the Node view of pg_nodes.js)
    assert '"lab.js"' in main and '"lab.css"' in main
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert "PAGES.lab = async () =>" in lab and "window.labOn = " in lab and "window.labWinHtml = " in lab and "window.labWtab = " in lab
