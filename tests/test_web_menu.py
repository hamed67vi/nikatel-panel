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
    assert _tabs("pgpanel") == [("settings", "General"), ("users", "Users"), ("configs", "Configs"), ("admins", "Admins")]      # General (the settings of the panel) is the first one (the owner, 2026-10-14); Hosts, Access and Core are ONE tab, Configs (1.54.0)
    assert "PAGES.pgpanel = async () => { if (window.labLockNow && await labLockNow()) return; renderSub(); return PAGES.panels(); }" in APP          # (the lock of the Panel is the first thing it asks)
    assert "settings: SI.cog}" in APP and "cog: '<svg" in APP          # (and its icon is a cog wheel, not the sun of SI.gear)


def test_the_servers_page_has_no_nodes_tab_any_more_the_nodes_are_in_the_server_window():
    block = APP.split("const SUB = {", 1)[1].split("\n};", 1)[0]
    assert "pgnodes" not in block and "pgTabs.nodes" not in "".join(f.read_text(encoding="utf-8") for f in WEB.glob("pg_*.js"))


def test_the_old_pages_of_the_sections_still_open_their_tab():
    alias = APP.split("const NAV_ALIAS = {", 1)[1].split("};", 1)[0]
    for page, tab in (("pghosts", "configs"), ("pgaccess", "configs"), ("pgcore", "configs"), ("pgadmins", "admins")):
        assert f'{page}: "pgpanel/{tab}"' in alias
    assert 'const NAV_PN = {pghosts: "hosts", pgaccess: "groups", pgcore: "cores"};' in APP and "if (NAV_PN[page]" in APP          # (an old name opens the inner tab it used to be)
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
    assert "...(OUTBOUND_UI ? [[\"gateway\", \"🌉 Outbound\", INFO_GW]] : [])" in APP           # (Outbound has no tab at the moment: it is hidden, see the next test)


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
    assert 'SUB_ACT["nodes/add"] = () => { if (labOn()) labNewProfile(); else { LAB.pending = "newprofile"; go("nodes", "lab"); } };' in lab and 'document.getElementById("nav-sub-nodes")' in lab
    assert 'for (const t of labTags())' in lab and 'any.onclick = () => labGoKind("any")' in lab          # (one item for each tag, drawn from the tags the page read)
    assert 'const labTags = () => LAB.tags && LAB.tags.length ? LAB.tags : [];' in lab and "any.hidden = true;" in lab and 'h.onclick = () => labGoKind("any")' in lab          # (no category and no "All" before there is a profile: the menu is only + Add profile)
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
    assert 'name="tag" id="lab-tag" onchange="labTagPick(this.value)"' in lab and 'Any (no profile)' in lab and "SERVER_FORM(null)" in lab and "labTagInput()" in (WEB / "app.js").read_text(encoding="utf-8")          # the old box of the Servers page, with one more field          # ONE Tag field: empty is Any, a datalist suggests
    assert 'all = [...new Set([...labTags(), ...(cur ? [cur] : [])])];' in lab          # the Profile field is a drop-down of every tag there is (and the open one)
    assert 'window.labTagPick = v => {' in lab and 'const on = labIsPgTag(v)' in lab          # a PasarGuard profile brings the install options of a PasarGuard node
    assert "const labRoleOf = " in lab and "labRoleOf(f.get(\"tag\"))" in (WEB / "app.js").read_text(encoding="utf-8") and "tag: String(f.get(\"tag\")" in (WEB / "app.js").read_text(encoding="utf-8")          # the tags Iran / Foreign say where a new server is; the tag goes to the API
    assert "window.labSetTag = " in lab and 'Move to profile…' in lab and '`/api/profiles/${id}/tag`' in lab          # a tag can be changed from the ⋯ menu


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
    assert "window.labLocked = () => false;" in lab and "window.labNewProfile = () =>" in lab and "Start with a profile" in lab and 'document.getElementById("demo-pill")' in lab          # (the public demo has no panel to connect: its Panel page stays open)
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
    assert '<table class="tbl lift srv-tbl joined lab-tbl">' in lab and ": SRV_IC}</i>" in lab and 'class="srv-ic"' in lab          # the table, the server icon and the flag of the old list
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


def _web(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_the_top_of_the_panel_page_is_one_row_that_only_switches_and_general_adds_edits_and_removes():
    """The owner, 2026-10-15: "in Users put all of it in one row; here only switch between the PasarGuard panels; edit and add only from General"."""
    assert 'const pnManage = () => state.page !== "pgpanel" || sub("pgpanel") === "settings";' in APP
    assert 'list = manage ? every : every.filter(x => x.type === "pasarguard")' in APP                      # outside General: the PasarGuard panels only
    bar = APP.split("const pnBar = (list, cur, row2, mode = ", 1)[1].split("/* outside General the strip", 1)[0]
    before_add = bar.split("const add = ", 1)[0]
    assert 'if (pick) return' in before_add and 'id="pn-ctl"' in before_add and "pnAddWin" not in before_add                    # the bar that only switches is returned before the + button is even built
    assert '${pick ? "" : `<button type="button" class="dns-x" onclick="pnDel(' in bar                                          # and the cross that removes a panel is drawn only outside it
    facts = APP.split("const facts = ", 1)[1].split("pnBar(list, p.id", 1)[0]
    assert "pnEditWin()" in facts and "pnCheck(" in facts and 'manage ? facts : "", manage ? "manage" : "pick"' in APP          # Check now and Edit: General only    assert "function pnFuse()" in APP and "slot.replaceChildren(...bars.childNodes)" in APP and "pnFuseWatch();" in APP
    assert 'body.querySelector(".pg-bar:not(.pg-usage .pg-bar)")' in APP and "slot.append(tool)" in APP                 # the own bar of Access, Core, Admins and Hosts goes up too (not the range bar of Usage)
    assert "pnFuse();" in APP.split("async function pnUsersLoad", 1)[1].split("async function pnHostsLoad", 1)[0]    # the list is measured after the controls moved up
    assert "pnFuse();" in APP.split("async function pnHostsLoad", 1)[1].split("window.hpCount", 1)[0]
    common = _web("pg_common.js")
    assert common.count("pnFuse()") == 3 and 'pn.tab === "usage" ? "" : pgBars()' in common                      # every paint of a tab moves its strip up; Usage carries its tab buttons in its own bar
    assert "${pnTabBtns()}<i class=\"pg-usage-sep\"></i>" in _web("pg_usage.js")
    css = _web("app.css")
    assert "html body #page .pn-ctl > .toolbar { display: contents !important; }" in css and "html body #page .pn-bar.pn-pick .pn-one { flex-wrap: nowrap; }" in css
    assert "html body #page .pn-facts button .bt { display: none; }" in css                                           # the buttons of General are icons


def test_one_window_makes_a_whole_kind_of_account_and_the_three_tabs_open_it():
    """The owner, 2026-10-15: "to make an account we only choose its kind and the panel builds it": the inbound (Core), the group (Access) and the host (Hosts) in one step."""
    js = _web("pg_kinds.js")
    for fn in ("pgKindNew", "pgKindPick", "pgKindCheck", "pgKindMake", "pgKindScan", "pgKindBtn"):
        assert f"window.{fn} = " in js, fn
    assert 'pgApi(id, "/kinds")' in js and '"/kinds/plan"' in js and '"/kinds/make"' in js and "confirm: restart || p.needs_confirm" in js
    for tab in ("pg_access.js", "pg_cores.js", "pg_hosts.js"):
        assert "pgKindBtn()" in _web(tab), tab
    assert '<script src="/static/pg_kinds.js"></script>' in INDEX and '<link rel="stylesheet" href="/static/pg_kinds.css">' in INDEX
    main = (WEB.parent / "main.py").read_text(encoding="utf-8")
    assert '"pg_kinds.js"' in main and '"pg_kinds.css"' in main
    assert 'html body #modal .pg-kind-chip.on' in _web("pg_kinds.css")
    assert 'name="template" checked' in js and "const pgKindTplSync" in js and "b.template = f.elements.template.checked" in js       # the user template is a tick of the same window
    assert "More (everything here is chosen for you)" in js and "<details class=\"pg-kind-adv\">" in js and "const pgKindFill = auto =>" in js and "Chosen for you:" in js      # (1.53.2, 1.55.0) only the protocol is asked (chips, or a pasted link): the details are folded and filled with what was chosen
    assert "required" not in js.split("pgKindDraw() {", 1)[1].split("window.pgKindPick", 1)[0]


def test_the_list_of_servers_has_an_ip_column_in_place_of_the_state_column_and_shows_the_traffic_right():
    """The owner, 2026-10-15: "remove this column" (State · last 24 h), "a column with the IP of the servers instead", "show the numbers of the traffic right"."""
    lab = _web("lab.js")
    assert "State · last 24 h" not in lab and "labStateHtml" not in lab and "labGreTag" not in lab and "labCells" not in lab and "LAB.strips" not in lab
    assert '<th data-prio="1">IP</th>' in lab and 'class="lab-ip mono ip-copy" data-ip="${esc(s.host)}" onclick="event.stopPropagation(); labCopyIp(this)"' in lab and 'const addr = d ? d.address : p.address' in lab          # (an address is copied by a click; a node shows no port)
    assert "/api/profiles/strips" not in lab                                                         # the list does not ask for the strips any more
    assert lab.count('<col style="width:') == 6 and "Traffic Mbit/s" in lab and "const labRate = v =>" in lab and "labRate(f.rx_mbps)" in lab
    assert ".lab-ip {" in _web("lab.css")


def test_outbound_and_the_shop_bot_are_hidden_for_now_and_come_back_with_one_switch_each():
    """The owner, 2026-10-15: "do not show the Outbound and the Shop bot in the panel until we get to them later". Not grey: not there."""
    assert "const OUTBOUND_UI = false;" in APP and "const SHOPBOT_UI = false;" in APP
    assert re.search(r'<a data-page="bot"[^>]* hidden[ >]', INDEX)                 # (the item is in the page but hidden: no flash before the script runs)
    assert """document.querySelectorAll('#nav a[data-page="bot"]').forEach(a => { a.hidden = !SHOPBOT_UI; });""" in APP
    assert '(page === "bot" && !SHOPBOT_UI)' in APP and "if (SHOPBOT_UI) SB_TABS.forEach" in APP                      # (not reachable, not found by the search of the dashboard)
    assert "nav#nav a[hidden] { display: none !important; }" in _web("app.css")


def test_the_account_type_button_is_not_shown_and_comes_back_with_one_switch():
    """The owner, 2026-10-15: "remove this completely" (the ＋ Account type button): the bars of Access, Hosts and Core ask pgKindBtn(), which answers nothing while PG_KINDS_UI is false."""
    js = _web("pg_kinds.js")
    assert "const PG_KINDS_UI = false;" in js and 'window.pgKindBtn = () => !PG_KINDS_UI || pnType() !== "pasarguard" ? "" :' in js


def test_the_templates_are_a_tab_of_general_not_of_access():
    """The owner, 2026-10-15: "move this part (Templates) to the General tab"."""
    subs = re.search(r"const PG_SUBS = \{(.*?)\};", APP, re.DOTALL).group(1)
    assert 'settings: ["usage", "system", "settings", "templates"]' in subs and '"templates"' not in subs.split("settings:")[0]          # (and Access is gone: its groups are in Configs)


def test_hosts_access_and_core_are_one_section_called_configs_with_an_add_tab_first():
    """The owner, 2026-10-15: "make these three sections one, so that we can easily add an account to the panel" (Hosts, Groups, Core Configs)."""
    tabs = _tabs("pgpanel")
    assert [k for k, _ in tabs] == ["settings", "users", "configs", "admins"], tabs
    subs = re.search(r"const PG_SUBS = \{(.*?)\};", APP, re.DOTALL).group(1)
    assert 'configs: ["kinds", "hosts", "groups", "cores"]' in subs and "hosts: [" not in subs.replace("configs:", "") and "access:" not in subs and "core:" not in subs
    js = _web("pg_kinds.js")
    assert 'window.pgTabs.kinds = {label: "Add"' in js and "function pgKindFormHtml(inline)" in js and 'state.build = "kinds"' in js
    assert "configs: SI.cpu" in APP


def test_the_menu_of_a_node_row_is_the_menu_of_the_node_window_in_one_column_with_groups():
    """The owner, 2026-10-15: "make this one like the other: in one column with groups" - the row of a node had a short menu without icons."""
    lab = _web("lab.js").split("function labNodeItems(", 1)[1].split("/* a section is drawn", 1)[0]
    assert 'ctx === "row" ? labItem(' not in lab and "ctx === \"row\" ? []" not in lab                                  # no short row version of any item any more
    assert 'labIt("Overview", "overview"' in lab and 'labIt("Check now", "check"' in lab and 'labIt("Move to profile…", "tag"' in lab      # (a row: the profile's own items first, with icons)
    for item in ("Log", "Usage", "Resources", "Latency", "Certificate…", "Edit…", "Restart…", "Resync users…", "Reset counters…", "Update the node…", "Update Xray…", "Update geo files…", "Off the page", "Delete the node…"):
        assert f'it("{item}"' in lab or 'it(off ? "Enable…" : "Disable…"' in lab, item


def test_the_first_page_asks_the_name_of_the_profile_in_its_own_card():
    """The owner, 2026-10-15: "when Add profile is pressed, ask a name for the profile tag in this same page"."""
    lab = _web("lab.js")
    assert 'const card = document.querySelector(".lab-first-card");' in lab and "Name of the new profile" in lab and 'onsubmit="return labNewProfileGo(event)"' in lab
    assert ".lab-first-form {" in _web("lab.css")


def test_the_core_tab_is_cards_like_pasarguard_with_no_chips_bar():
    """The owner, 2026-10-15: "remove this core section and bring PasarGuard's" (the cores as cards, a search, a way between cards and table)."""
    js = _web("pg_cores.js")
    assert "function pgCoresCardsHtml()" in js and "window.pgCoresBack = async () =>" in js and 'oninput="pgCoresQ(this.value)"' in js and "<b>Cores</b>" not in js.split("function pgCoresBarHtml()", 1)[1].split("function pgCoresCardsHtml", 1)[0]
    assert "st.list.length === 1) st.coreId" not in js                      # (one core is no longer opened at once: the cards come first)
    assert ".pg-core-card {" in _web("pg_cores.css")


def test_the_ui_kit_is_the_last_stylesheet_and_holds_the_buttons_fields_and_menus_of_the_owners_sheet():
    """The owner, 2026-10-15: "rebuild every element and button with this theme, and everything you build later too" (docs/design/ui-kit.md)."""
    index, kit = _web("index.html"), _web("uikit.css")
    assert index.rindex('href="/static/uikit.css"') > index.rindex('rel="stylesheet" href="/static/pg_usage.css"')           # loaded LAST: it only sets the look
    assert "uikit" in (WEB.parent / "main.py").read_text(encoding="utf-8")
    for part in ("button.pri", "button.danger", "button.ghost", ".tabs", ".lab-menu", "input:not([type=checkbox])", "--uk-blue", "--uk-ring"):
        assert part in kit, part
    assert "width:" not in kit and "height:" not in kit and "padding:" not in kit                  # the kit sets colour, border, shadow, radius - never a size or a place, so no page moves


def test_the_users_menu_and_every_opener_use_the_one_column_grouped_menu():
    """The owner, 2026-10-15: "all the options that open: this style, grouped, all in one column"."""
    users = _web("pg_users.js")
    assert 'onclick="nkMenu(this, pgUsersItems(), event)"' in users and "const pgUsersItems = () =>" in users
    assert "window.nkMenu = (btn, items, ev) =>" in _web("lab.js")


def test_the_add_tab_takes_a_pasted_link_and_picks_the_kind_of_it():
    """The owner, 2026-10-15: "ready configs to add, and my own config can be added too, very simple"."""
    js = _web("pg_kinds.js")
    assert 'id="pg-kind-link"' in js and "pgKindUseLink" in js and "const pgKindParseLink = text =>" in js
    kind_of = js.split("const pgKindLinkKind = p =>", 1)[1].split("const pgKindParseLink", 1)[0]
    for kid in ("shadowsocks", "vmess-ws-tls", "trojan-ws-tls", "trojan-tcp-pin", "vless-reality-vision", "vless-reality", "vless-xhttp-reality", "vless-xhttp-tls", "vless-ws-pin", "vless-httpupgrade-tls", "vless-tcp-http", "vless-mlkem"):
        assert f'"{kid}"' in kind_of, kid


def test_a_panel_page_without_a_panel_keeps_its_boxes():
    """The owner, 2026-10-15: "if no panel was added, the boxes stay" - the bar and the table of the Users tab are drawn empty, with the way to add a panel in General."""
    app = _web("app.js")
    assert "const pnEmptyPage = () =>" in app and "pnEmptyPage()" in app
    page = app.split("const pnEmptyPage = () =>", 1)[1].split("const pnBar = ", 1)[0]
    assert "usr-tbl" in page and "pn-sb" in page and "Add a panel in General" in page and "No PasarGuard panel is connected yet" in page


def test_the_core_cards_count_the_selected_ones_in_the_select_all_row():
    js = _web("pg_cores.js")
    assert 'id="pg-cores-cnt"' in js and 'cnt.textContent = `${k}/${(st.list || []).length}`' in js


def test_the_usage_tab_is_a_dashboard_of_two_columns_with_a_scale_on_its_chart():
    """The owner, 2026-10-15: "make the Usage section very professional" - one bar with the range buttons, a band of counters, two columns of cards, a chart with a scale; the page scrolls."""
    js, css = _web("pg_usage.js"), _web("pg_usage.css")
    for part in ("function pgUsageNice(m)", "const pgUsageStat = ", "pg-ukpi", "pg-usage-main", "pg-usage-side", 'class="pg-useg"', "Busiest node", "Subscription apps", "Users over time", "Top users", "pg-usage-gl"):
        assert part in js, part
    assert "pgUsageFit" not in js                                              # (no fixed one-screen height any more: the page scrolls, the cards keep their own height)
    assert ".main:has(.pg-usage) { overflow-y: auto; }" in css and ".pg-usage-grid {" in css and ".pg-useg button.on {" in css
    assert js.count('class="pg-usage-grid"') == 1                              # (the class of the columns is not the class of a grid line of the SVG)

def test_a_server_or_node_can_be_moved_to_any_profile_from_the_menu_or_by_dragging_its_row_onto_a_tab():
    """The owner, 2026-10-15: "a way to move a server to any profile we like"."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert 'labIt("Change tag…"' not in lab and lab.count('labIt("Move to profile…", "tag", () => labSetTag(p.id))') == 2             # (the row menu and the head of the node window)
    assert "window.labMoveTo = async (id, tag) =>" in lab and "/api/profiles/${id}/tag" in lab and 'class="lab-mv new" onclick="labMoveNew(${id})"' in lab
    assert lab.count("${labDragAttrs(p.id)}") == 3                                                      # (a server row, a node row, a row of what is gone)
    assert 'ondragover="labDropOver(event)"' in lab and 'ondrop="labDrop(event, this.dataset.k)"' in lab and 'labMoveTo(id, k === "any" ? "" : k)' in lab
    assert ".lab-mv-list {" in css and ".lab-kinds button.drop {" in css

def test_the_add_profile_box_is_small_and_offers_the_suggested_tags_and_a_pasarguard_profile_is_locked():
    """The owner, 2026-10-15: "make this box prettier, show the suggested tags" and "the PasarGuard profile cannot be deleted or renamed while its panel is connected"."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert 'const LAB_SUGGEST = ["Iran", "Foreign", "Marzban", "PasarGuard"]' in lab and "window.labNpFill = b =>" in lab and 'class="ghost lab-np-chip"' in lab
    assert 'showModal("<b>＋ Add profile</b>", labNpForm(false), "narrow")' in lab                                       # (a small box, not the wide one)
    assert ".lab-np .lab-np-in {" in css and ".lab-np .lab-np-chip {" in css
    assert 'const labTagLocked = t => labHasPg() && String(t).toLowerCase().startsWith("pasarguard")' in lab and lab.count("if (labTagLocked(cur)) {") == 2
    assert lab.count('${labTagLocked(cur) ? "disabled" : ""}') == 2                                                      # (the two buttons beside the tabs are off)

def test_deleting_a_node_asks_in_a_box_with_a_tick_that_also_deletes_it_from_the_pasarguard_panel():
    """The owner, 2026-10-15: "when we press Delete a box opens with a checkbox: if ticked, the node is deleted from the PasarGuard panel itself"."""
    app, nodes = _web("app.js"), _web("pg_nodes.js")
    assert "check = null, generate = true} = {}) {" in app and 'class="ask-check"' in app and "cb ? {checked: cb.checked} : true" in app          # (the tick is an option of the glass box; the answer is {checked}, or false on Cancel)
    branch = nodes.split('if (v === "delete") {', 1)[1].split("\n};", 1)[0]
    assert "Also delete the node from the PasarGuard panel itself" in branch and "r.checked" in branch
    assert branch.count('{method: "DELETE"}') == 3 and "/api/profiles/${prof.id}" in branch                          # (unticked: only the profile goes off the page; ticked: the panel's node first, then the profile)
    assert branch.index("await delPanel()") < branch.index("/api/profiles/${prof.id}")
    assert "if (!prof) {" in branch                                                                                   # (a node with no profile keeps the plain question)
    assert ".ask-check {" in _web("lab.css")

def test_a_pasarguard_profile_has_a_sync_button_beside_its_tabs_and_in_its_empty_page():
    lab = _web("lab.js")
    assert "window.labSyncNodes = async () =>" in lab and '"/api/profiles/sync"' in lab and lab.count("onclick=\"labSyncNodes()\"") == 2 and "${labSyncBtn(cur, IB)}" in lab

def test_deleting_a_server_that_carries_a_pasarguard_node_has_the_tick_in_its_own_box():
    """The owner, 2026-10-15: "when I press Delete server it did not bring the tick that deletes the node from the PasarGuard panel too"."""
    app, srv = _web("app.js"), _web("pg_srvnode.js")
    body = app.split("window.deleteServer = async (id, name) => {", 1)[1].split("window.forgetKey", 1)[0]
    assert "check: {label: `Also delete the node" in body and "alsoNode" in body and "pgSrvAfterDelete(name, nodes, alsoNode)" in body
    after = srv.split("window.pgSrvAfterDelete = async (sname, nodes, alsoNode) => {", 1)[1].split("/* ----", 1)[0]
    assert "if (alsoNode)" in after and "gconfirm" not in after          # (no second question: the tick of the first box says)

def test_a_server_that_carries_a_pasarguard_node_keeps_the_actions_of_the_node_in_its_menu():
    """The owner, 2026-10-15: "I did not say remove these capabilities" - the node was merged into the row of its server, and the Log, Usage, Certificate, Restart ... of the node were gone from the menu."""
    lab = _web("lab.js")
    row = lab.split('if (ctx === "row") {', 1)[1].split("return [labGrp(\"Server\")", 1)[0]
    assert 'x.type === "pasarguard" && x.node_id != null' in row and 'labNodeItems({panel_id: pgn.panel_id, node_id: pgn.node_id}, null, "node").slice(0, -1)' in row and '{grp: "Node · " + x.grp}' in row
    assert "labSep, ...nodeActs, gone, labSep, del]" in lab

def test_one_list_of_the_left_menu_is_open_at_a_time():
    """The owner, 2026-10-15: "two collapsible lists must not be open together: when I press Servers and Panel was open, it closes"."""
    app = _web("app.js")
    body = app.split("function navGroup(g, open, keep) {", 1)[1].split("\n}", 1)[0]
    assert 'if (open) document.querySelectorAll("#nav .nav-g").forEach(o => { if (o !== g && o.dataset.open === "1") navGroupSet(o, false, true); });' in body
    assert "function navGroupSet(g, open, keep) {" in app and "only the first stays" in app

def test_the_lab_looks_again_by_itself_so_a_new_server_does_not_stay_with_dashes():
    """The owner, 2026-10-15: "I added a normal server and it stayed like this" (dashes until the page was refreshed)."""
    lab = _web("lab.js")
    assert "labAutoT = setTimeout(() =>" in lab and "waiting ? 3500 : 30000" in lab and "s.enabled && !(s.facts && s.facts.cpu != null)" in lab and "!document.querySelector(\"#modal .modal\")" in lab


def test_fixed_boxes_names_only_buttons_disabled_without_items_and_the_server_icon_is_never_coloured():
    """The owner, 2026-10-15: "a fixed box" for the tunnels, every tab of the DNS page and the Doctor; "only their names"; disabled when there is nothing; "the icons of the servers never coloured: white, a quiet lamp behind them"; "the bar bigger, in the blue of the kit"."""
    app, css = _web("app.js"), _web("lab.css")
    tun = app.split("PAGES.tunnelsList = async () => {", 1)[1].split("applyQ(); fxFit();", 1)[0]
    assert '<div class="lbox tun-box fx-box">' in tun and "🔍" not in tun and '<span class="bt">Check all</span>' in tun and '<span class="bt">Restart all</span>' in tun
    assert '!list.length ? "disabled"' in tun and 'list.length ? "" : "disabled"' in tun and " is-empty" in tun
    assert "function fxFit()" in app and 'class="lbox full fx-box dns-box"' in app and 'class="card ap-tasks fx-box"' in app
    assert '!accounts.length ? "accounts"' in app and '!accounts.length && k !== "accounts" ? \'disabled title="Add an account first"\'' in app
    assert "html body #page .fx-box { height: calc(100vh - 290px)" in css and ".toolbar.is-empty :is(.tf-tabs, .tb-search)" in css and ".toolbar.joined .tf-tabs { align-self: center" in css
    assert "color: var(--text) !important" in css and "img.lab-logo { filter: grayscale(1)" in css and ".srv-ic::before { content: \"\"; position: absolute; top: -2px; right: -5px; width: 9px; height: 9px; border-radius: 50%;" in css and "tr.sev-yellow { --lamp: #ffc44d" in css
    assert ".lab-cpu .bar { flex: 0 1 96px" in css and "linear-gradient(90deg, #0a62ff, #4aa8ff)" in css


def test_the_lab_table_has_no_serves_column_the_address_is_next_to_the_name_and_the_profile_has_its_own_column():
    """The owner, 2026-10-15: "remove Serves, put the profile tag there instead, and the IP in the column nearest to the name"."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert "lab-serves" not in lab and "labServes" not in lab and ">Serves<" not in lab
    assert '<th>Name</th><th data-prio="1">IP</th><th data-prio="3">Profile</th>' in lab
    assert lab.count('<td class="lab-prof">${labTagChip(p.tag)}</td>') == 2 and "lab-tagp" not in lab
    assert "const labSub = s =>" in lab and "${labSub(s)}</div></div></td>" in lab
    assert ".lab-tbl .lab-tagc {" in css and ".lab-nm small .lab-busy" in css


def test_the_network_page_keeps_its_three_tabs_under_the_header_while_its_menu_list_is_open():
    """The owner, 2026-10-15: "Network opens with three tabs under the header"."""
    app = _web("app.js")
    assert 'const NAV_BAR_TOO = new Set(["tunnels"]);' in app and '&& !NAV_BAR_TOO.has(state.page) ? "1" : ""' in app