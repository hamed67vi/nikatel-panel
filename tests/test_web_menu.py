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

def test_the_section_that_was_called_servers_is_called_lab_its_list_is_any_the_tags_and_add_profile_plus_at_the_end():
    """The owner (2026-10-14): "the Servers section is called Lab", "the profiles item is called any", "+ Add profile" as the last item, then "make me three categories: Marzban, PasarGuard, Iran". The group
    of the menu is Lab: Any (every profile), one item for each TAG (the three categories are there from the start, any tag he types joins them) and "+ Add profile" as the LAST item: a door to the add flow,
    an "act" of the tabs (no page and no tab of its own, never the open one)."""
    assert re.search(r'<a data-page="nodes"[^>]*data-short="Servers"', INDEX) and re.search(r'<span>Servers</span></a>', NAV) and "<span>Lab</span>" not in NAV
    assert re.search(r'^  nodes: \["Servers", ', APP, re.MULTILINE)
    block = APP.split("const SUB = {", 1)[1].split("\n};", 1)[0]
    tabs = re.findall(r'^  nodes: \[(.*)\],$', block, re.MULTILINE)[0]
    assert tabs.index('["lab", "All"') < tabs.index('["add", "＋ Add profile"') and '"act"]' in tabs and "Profiles" not in tabs.split('["add"', 1)[0]          # (the list first, Add profile at the end)
    assert '<i class="ic"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></i><span>${esc(name)}</span>' in APP and "nav-ag" not in APP and "nav-act-k" not in APP          # ("+ Add profile": the + in front of its name, no arrow, no small group)
    assert "window.subAct = (page, k) =>" in APP and 'SUB[page].filter(x => x[3] !== "act")' in APP and "a.dataset.act ? subAct(a.dataset.page, a.dataset.sub)" in APP
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert 'SUB_ACT["nodes/add"] = () => { if (labOn()) labAdd(); else { LAB.pending = "add"; go("nodes", "lab"); } };' in lab and 'document.getElementById("nav-sub-nodes")' in lab
    assert 'for (const t of labTags())' in lab and 'any.onclick = () => labGoKind("any")' in lab          # (one item for each tag, drawn from the tags the page read)
    assert 'const labTags = () => LAB.tags && LAB.tags.length ? LAB.tags : ["Marzban", "PasarGuard Nodes", "Iran"];' in lab          # (the three categories are there before anything is read)
    assert lab.count("labNavSync();") >= 3          # (when the page is drawn, when a tab of the page is chosen, when the profiles were read in the background)
    assert 'if (k === "connect") labConnect(); else if (typeof k === "object") labAdd("new", k); else labAdd(); }' in lab          # (the page opens the add box once it is drawn, whichever way it was reached)
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert ".nav-act" in css and ".nav-ag" not in css and ".nav-ak" not in css


def test_a_profile_is_added_in_one_box_with_no_tabs_that_asks_its_name_and_its_tag_any_unless_he_chooses_one():
    """The owner (2026-10-14): "first we add a profile; a box opens and asks its name and its tag; the default is any, but a tag with a name of my own can be chosen; Iran and Foreign are suggested", then "remove
    these tabs; remove adding several servers at once and Already in the panel; for the tag put only one box, and when the tag PasarGuard is chosen the settings of a PasarGuard node come"."""
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert "labChooser" not in lab and "labAddKind" not in lab and "labServerStep" not in lab          # (no choice of three kinds)
    assert "lab-addtabs" not in lab and "labSaveMany" not in lab and "labPickGo" not in lab and "Already in the panel" not in lab and "Several at once" not in lab          # (no tabs, no several-at-once, no already-in-the-panel)
    assert 'name="tag" id="lab-tag" list="lab-taglist"' in lab and 'placeholder="Any"' in lab and "SERVER_FORM(null)" in lab and "labTagInput()" in (WEB / "app.js").read_text(encoding="utf-8")          # the old box of the Servers page, with one more field          # ONE Tag field: empty is Any, a datalist suggests
    assert '["Iran", "Foreign", ...labTags()]' in lab          # Iran and Foreign are suggested, then every tag there is
    assert 'onchange="labTagPick(this.value)"' in lab and 'window.labTagPick = v => { labTagDraft.v = v; if (String(v).trim().toLowerCase().startsWith("pasarguard")) labAddNode(); };' in lab          # the tag PasarGuard brings the node's settings
    assert "const labRoleOf = " in lab and "labRoleOf(f.get(\"tag\"))" in (WEB / "app.js").read_text(encoding="utf-8") and "tag: String(f.get(\"tag\")" in (WEB / "app.js").read_text(encoding="utf-8")          # the tags Iran / Foreign say where a new server is; the tag goes to the API
    assert "window.labSetTag = " in lab and 'Change tag…' in lab and '`/api/profiles/${id}/tag`' in lab          # a tag can be changed from the ⋯ menu


def test_the_pasarguard_node_settings_need_a_connected_pasarguard_panel_and_a_click_says_why():
    """The owner (2026-10-14): "this option is active when we have added a PasarGuard panel". Choosing the tag PasarGuard asks for a PasarGuard panel first when none is connected: it says why and leads to
    connecting one - with the API key, or a username and password, the same screen the locked Panel item leads to - and once one is connected the node settings come."""
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert "const labHasPg = () => !!(window.pgSrvPanels && pgSrvPanels().length);" in lab
    assert "window.labAddNode = () => { if (labHasPg()) labNodePanel(); else labPgFirst(); };" in lab          # (asking for the node settings without a panel says why)
    assert "window.labConnect = () => labNodePanel(true);" in lab and "state.labOnly = only === true;" in lab          # (only the panel is asked for: no node)
    assert "if (state.labOnly) { state.labOnly = false; closeModal(); refresh(); return false; }" in lab          # (and once it is connected the option is live)
    assert 'if (labOn()) labConnect(); else { LAB.pending = "connect"; go("nodes", "lab"); }' in lab          # (the locked Panel item leads to the same screen)
    assert "state.panelsCache = r; labLockSet(" in lab          # (the background look keeps the list of panels the option reads)
    assert "labTagDraft.v = \\'\\'; labAdd()" in lab          # (Back from the node settings does not jump into them again)


def test_nothing_puts_the_servers_the_panel_already_has_on_the_lab_by_itself():
    """The owner (2026-10-14): "I did not say to move the cards, I only said to bring the information of the servers"; later he removed the Already in the panel list too. No step of an upgrade puts a SERVER on
    the page: only the nodes of a connected PasarGuard panel come by themselves."""
    db = (WEB.parent / "db.py").read_text(encoding="utf-8")
    assert "backfill" not in db and "auto_all" in db


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
    assert "const labKebab" in lab and lab.count("labKebab(") >= 4 and "window.labMenu = " in lab and "const two = false" in lab         # one menu, always ONE column (the owner)
    assert "labIt(\"Delete server…\"" in lab and "danger: true" in lab and ".lab-mi.danger" in css                         # the dangerous item is last and red
    assert 'class="sm abtn' not in lab and "wtools" not in lab                                                                # no row of buttons, no tiles of tools
    assert "const labTags = " in lab and '["any", ...labTags()]' in lab                                                       # the tabs are the tags: Any, then Marzban, PasarGuard, Iran and every other tag
    assert ".lab-bar { container: labbar" in css and "flex-wrap: nowrap" in css.split(".lab-bar {", 1)[1].split("}", 1)[0]    # the bar is one row at every width
    assert [k for k, _ in re.findall(r'\["(overview|node|monitor|tools)", "(\w+)"\]', lab.split("const LAB_SECS", 1)[1].split("\n", 1)[0])] == ["overview", "node", "monitor"]


def test_the_lab_files_are_loaded_and_versioned_like_the_others():
    index, main = INDEX, (WEB.parent / "main.py").read_text(encoding="utf-8")
    assert '<script src="/static/lab.js"></script>' in index and '<link rel="stylesheet" href="/static/lab.css">' in index
    assert index.index("/static/app.js") < index.index("/static/pg_nodes.js") < index.index("/static/lab.js")          # (the Lab uses the helpers of app.js and the Node view of pg_nodes.js)
    assert '"lab.js"' in main and '"lab.css"' in main
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    assert "PAGES.lab = async () =>" in lab and "window.labOn = " in lab and "window.labWinHtml = " in lab and "window.labWtab = " in lab

def test_the_lab_names_the_button_add_server_has_a_plus_beside_the_tabs_and_the_logos_of_the_panels():
    """The owner (2026-10-14): the button is "Add server"; "a + beside the other profiles" (the tabs) makes a new profile (category); the emoji of PasarGuard is the winged lion, of Marzban the barbed wire; the menu of
    a node is grouped - only the important ones (Log, Edit, Restart, Disable) are not, and they stand in ONE ROW."""
    lab = (WEB / "lab.js").read_text(encoding="utf-8")
    css = (WEB / "lab.css").read_text(encoding="utf-8")
    assert 'aria-label="Add server"' in lab and '＋ Add server</button>' in lab and 'labModal("<b>Add a server</b>"' in lab
    assert 'onclick="labAddTag()"' in lab and "window.labAddTag = " in lab and '"/api/profiles/tags"' in lab and ".lab-ib" in css and "segmented control" in css
    assert (WEB / "logos" / "pasarguard.png").is_file() and (WEB / "logos" / "marzban.png").is_file() and "const labLogo = " in lab and "labLogo(k)" in lab and "labLogo(t)" in lab and ".lab-logo" in css
    assert "const I = d => `<svg" in lab and 'class="lab-mic"' in lab and ".lab-menu.flat" in css and 'it("Delete the node…"' in lab          # the node menu: one column, an icon before each item, a line between the groups
    assert "window.labRenameTag = " in lab and "window.labDeleteTag = " in lab and "ncc ? \"has-flag\"" in lab          # a profile can be renamed and deleted from beside the tabs; every row shows the flag of its server
