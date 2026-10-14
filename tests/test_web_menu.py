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
    assert '.map(t => [t[0], navPlain(t[1]), ic[t[0]] || SI.doc, t[3] === "off" ? "off" : t[3] === "act" ? "act" : false])' in APP
    assert 'class="nav-off" aria-disabled="true"' in APP and 'sub.querySelectorAll("a:not(.nav-off)")' in APP          # (and a click on it does nothing)
    assert re.search(r'\["gateway", "[^"]*Outbound", INFO_GW, "off"\]', APP)


def test_the_old_foreign_and_iran_lists_are_out_of_the_project_and_the_lab_is_the_page_of_the_servers():
    """The owner (2026-10-14): "hide these two sections entirely - I mean take them out of the project, but let us have them in case we want them somewhere else". The Foreign and Iran lists are not
    tabs, not pages and not code of the panel any more (a copy of them is kept outside of the project); the Lab opens by itself, and a shortcut to a server (the dashboard, a diagnosis) opens its window on the Lab."""
    block = APP.split("const SUB = {", 1)[1].split("\n};", 1)[0]
    tabs = re.findall(r'^  nodes: \[(.*)\],$', block, re.MULTILINE)[0]
    assert '["foreign",' not in tabs and '["iran",' not in tabs          # (Foreign and Iran are kinds of profile, never tabs of the page)
    assert tabs.index('["lab",') < tabs.index('["add",')
    assert "PAGES.servers" not in APP and "srvManyGo" not in APP and "function addModal" not in APP and "window.addModal" not in APP          # the code of the old lists is gone, not hidden
    assert 'if (PAGES.lab) return PAGES.lab();' in APP and "lab.js) did not load" in APP          # (and a page that says so when lab.js did not load, never the old list)
    assert 'window.docOpen = (id, sec) => { go("nodes", "lab");' in APP and "state.tab = s.role" not in APP
    assert '"arch"' not in APP and "nav-arch" not in APP and "sub-arch" not in APP and "arch-tag" not in (WEB / "lab.css").read_text(encoding="utf-8")          # (no archive mark: there is no archive in the project)

def test_the_section_that_was_called_servers_is_called_lab_its_list_is_any_the_kinds_and_add_profile_plus_at_the_end():
    """The owner (2026-10-14): "the Servers section is called Lab", "show Add profile there when you click on it", "Add profile comes below the profiles; foreign, iran, pasargaurd; the profiles item is called
    any", then "Add profile comes at the END of the list, no arrow in front of it, a + after it". The group of the menu is Lab: Any (every profile), an item for each kind that has a profile (Foreign, Iran,
    PasarGuard - only with two kinds or more, like the tabs of the page), and Add profile + as the LAST item: a door to the add flow, an "act" of the tabs (no page and no tab of its own, never the open one)."""
    assert re.search(r'<a data-page="nodes"[^>]*data-short="Lab"', INDEX) and re.search(r'<span>Lab</span></a>', NAV) and "<span>Servers</span>" not in NAV
    assert re.search(r'^  nodes: \["Lab", ', APP, re.MULTILINE)
    block = APP.split("const SUB = {", 1)[1].split("\n};", 1)[0]
    tabs = re.findall(r'^  nodes: \[(.*)\],$', block, re.MULTILINE)[0]
    assert tabs.index('["lab", "Any"') < tabs.index('["add", "＋ Add profile"') and '"act"]' in tabs and "Profiles" not in tabs.split('["add"', 1)[0]          # (the list first, Add profile at the end)
    assert '<b class="nav-plus" aria-hidden="true">+</b>' in APP and "nav-ag" not in APP and "nav-act-k" not in APP          # (a + after its name, no arrow, no small group)
    assert "window.subAct = (page, k) =>" in APP and 'SUB[page].filter(x => x[3] !== "act")' in APP and "a.dataset.act ? subAct(a.dataset.page, a.dataset.sub)" in APP
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert 'SUB_ACT["nodes/add"] = () => { if (labOn()) labAdd(); else { LAB.pending = "add"; go("nodes", "lab"); } };' in lab and 'document.getElementById("nav-sub-nodes")' in lab
    assert 'const LAB_NAV = [["foreign", "Foreign"], ["iran", "Iran"], ["node", "PasarGuard"]]' in lab and "if (kinds.length > 1)" in lab and "any.onclick = () => labGoKind(\"any\")" in lab          # (the kinds follow the profiles)
    assert lab.count("labNavSync();") >= 3          # (when the page is drawn, when a tab of the page is chosen, when the profiles were read in the background)
    assert 'if (k === "add") labAdd(); else if (typeof k === "object") labServerStep(k.kind || "foreign", "new", k); else labAddKind(k);' in lab          # (the page opens the add flow once it is drawn, whichever way it was reached)
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert ".nav-plus" in css and ".nav-ag" not in css and ".nav-ak" not in css


def test_the_servers_the_panel_already_has_are_not_moved_to_the_lab_by_themselves_they_are_picked_in_add_profile():
    """The owner (2026-10-14): "I did not say to move the cards, I only said to bring the information of the servers". Nothing puts a server on the page by itself: Add profile lists the
    servers the panel already has (name, address, provider) under "Already in the panel", with a search and "Select all", and it opens on that list when there are some."""
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert "const labFree = " in lab and 'labServerStep(k, labFree(k).length ? "have" : "new")' in lab          # it opens on the list of the servers that are there
    assert 'Already in the panel<i class="cnt">' in lab and '<label class="lab-all">' in lab and "window.labPickAll = " in lab and "window.labPickFilter = " in lab
    assert "already in the panel</em>" in lab and "lab-first-note" in lab          # the first page and the cards of Add profile say how many there are
    assert "labSaveMany" in lab and '"/api/servers/add-many"' in lab          # several at once: lines of name and address
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert ".lab-pick-bar" in css and ".lab-all" in css and ".lab-have" in css
    db = (WEB.parent / "db.py").read_text(encoding="utf-8")
    assert "backfill" not in db and "(17," not in db          # (and no step of an upgrade puts them there)

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


def test_the_panel_item_is_locked_only_while_no_panel_is_connected_and_says_why_when_it_is_clicked():
    """The owner (2026-10-14): "we have a panel now, it must not be locked; the lock is for when no panel is connected". The lock is in go() (every way into the Panel passes there), in the Panel
    page itself (a reload on it) and on the menu (a grey item that still answers a click). It follows the list of panels - a panel that is connected - and NOT the nodes on the Lab page."""
    assert 'if (page === "pgpanel" && window.labLocked && labLocked()) { labLockMsg(); return; }' in APP
    assert "PAGES.pgpanel = async () => { if (window.labLockNow && await labLockNow()) return;" in APP
    assert "if (window.labLockSync) labLockSync();" in APP
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert "window.labLocked = () => LAB.pg === false" in lab and 'document.getElementById("demo-pill")' in lab          # (the public demo has no panel to connect: its Panel page stays open)
    assert 'a.classList.toggle("nav-lock", lock)' in lab
    sync = lab.split("window.labLockSync", 1)[1].split("window.labLockNow", 1)[0]
    assert 'api("/api/panels")' in sync and "(r.panels || []).length > 0" in sync          # a panel is connected: it is in the list of panels
    assert "if (pnl) labLockSet((pnl.panels || []).length > 0);" in lab and "labLockSet(LAB.profiles.some(" not in lab and 'p.kind === "node")' not in sync          # (a node profile has nothing to do with it)
    assert "labLockSet(true);      // (a panel is connected" in lab          # (connecting one opens the item at once)
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
