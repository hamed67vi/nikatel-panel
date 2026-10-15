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
    assert 'if (pick) return' in bar and 'id="pn-ctl"' in bar and 'const acts = pick ? "" : `<span class="lab-tabact">' in bar            # the bar that only switches has no + and no bin: they are built only outside it
    assert 'onclick="pnDel(${curP.id}' in bar and 'class="lab-ib"' in bar and "pn-b-edit" not in APP                                      # the bin takes the open panel off the list; there is no edit button (the API key pill opens the edit window)
    facts = APP.split("const facts = ", 1)[1].split("pnBar(list, p.id", 1)[0]
    assert "pnEditWin()" not in facts and "pnAuthPill(p)" in facts and "pnCheck(" not in facts and "pn-b-chk" not in facts and 'manage ? facts : "", manage ? "manage" : "pick"' in APP          # Check now and Edit: General only    assert "function pnFuse()" in APP and "slot.replaceChildren(...bars.childNodes)" in APP and "pnFuseWatch();" in APP
    assert 'body.querySelector(".pg-bar:not(.pg-usage .pg-bar)")' in APP and "slot.append(tool)" in APP                 # the own bar of Access, Core, Admins and Hosts goes up too (not the range bar of Usage)
    assert "pnFuse();" in APP.split("async function pnUsersLoad", 1)[1].split("async function pnHostsLoad", 1)[0]    # the list is measured after the controls moved up
    assert "pnFuse();" in APP.split("async function pnHostsLoad", 1)[1].split("window.hpCount", 1)[0]
    common = _web("pg_common.js")
    assert common.count("pnFuse()") == 3 and 'pn.tab === "usage" ? "" : pgBars()' in common                      # every paint of a tab moves its strip up; Usage carries its tab buttons in its own bar
    assert "pgBar(`${pnTabBtns()}`," in _web("pg_usage.js")
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
    assert 'name="template" checked' in js and "const pgKindTplSync" in js and "b.template = f.elements.template.checked" in js       # the user template is a tick of the same form (taken out and brought back on 2026-10-15)
    assert "More (everything here is chosen for you)" in js and "<details class=\"pg-kind-adv\">" in js and "const pgKindFill = auto =>" in js and "Chosen for you:" in js      # (1.53.2, 1.55.0) only the protocol is asked (chips, or a pasted link): the details are folded and filled with what was chosen
    assert "required" not in js.split("pgKindDraw() {", 1)[1].split("window.pgKindPick", 1)[0]


def test_the_list_of_servers_has_an_ip_column_in_place_of_the_state_column_and_shows_the_traffic_right():
    """The owner, 2026-10-15: "remove this column" (State · last 24 h), "a column with the IP of the servers instead", "show the numbers of the traffic right"."""
    lab = _web("lab.js")
    assert "State · last 24 h" not in lab and "labStateHtml" not in lab and "labGreTag" not in lab and "labCells" not in lab and "LAB.strips" not in lab
    assert '<th data-prio="1">IP</th>' in lab and 'class="lab-ip mono ip-copy" data-ip="${esc(s.host)}" onclick="event.stopPropagation(); labCopyIp(this)"' in lab and 'const addr = d ? d.address : p.address' in lab          # (an address is copied by a click; a node shows no port)
    assert "/api/profiles/strips" not in lab                                                         # the list does not ask for the strips any more
    assert lab.count('<col style="width:') == 7 and "Traffic Mbit/s" in lab and "const labRate = v =>" in lab and "labRate(f.rx_mbps)" in lab
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
    assert 'settings: ["usage", "settings", "templates"]' in subs and '"templates"' not in subs.split("settings:")[0]          # (and Access is gone: its groups are in Configs)


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
    lab = _web("lab.js").split("function labNodeParts(", 1)[1].split("/* a section is drawn", 1)[0]
    assert 'ctx === "row" ? labItem(' not in lab and "ctx === \"row\" ? []" not in lab                                  # no short row version of any item any more
    assert 'labIt("Overview", "overview"' in lab and 'labIt("Check now", "check"' in lab and 'labSubItem("Move to profile", "tag", labMoveItems(p.id))' in lab      # (a row: the profile's own items first, with icons)
    for item in ("Log", "Usage", "Resources", "Latency", "Certificate…", "Edit…", "Restart…", "Resync users…", "Reset counters…", "Update the node…", "Update Xray…", "Update geo files…", "Off the page", "Delete the node…"):
        assert f'it("{item}"' in lab or f'labIt("{item}"' in lab or 'it(off ? "Enable…" : "Disable…"' in lab, item
    assert lab.count("labSubItem(") == 4 and 'labSubItem("Monitor", "monitor", mon)' in lab and 'labSubItem("Manage", "manage", mng)' in lab and 'labSubItem("Update", "up", upd)' in lab      # (the flyouts: only Edit and Restart stand on their own)


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


def test_the_usage_tab_is_round_stats_in_sections_with_a_nodes_only_view():
    """The owner, 2026-10-15: "short, useful, packed; easy on the eye, like a round stats menu; a section with only the nodes' usage": rings and gauges on one screen, chips for the sections, a Nodes section."""
    js, css = _web("pg_usage.js"), _web("pg_usage.css")
    for part in ("function pgUsageNice(m)", "function pgUsageRingSvg(", "function pgUsageGaugeSvg(", "function pgUsageSparkSvg(", "const PGU_SECS = ", 'data-for="nodes"', 'data-for="ov"', "function pgUsageNodesSecHtml(", "window.pgUsageSec = ", 'class="pg-back"', "Busiest node", "Subscription apps", "Users over time", "Top users", "pg-usage-gl"):
        assert part in js, part
    assert "pgUsageFit" not in js and "pg-ukpi" not in js and "pg-usage-pair" not in js and "#pg-usage-cards" not in js
    assert ".main:has(.pg-usage) { overflow-y: auto; }" in css and ".pg-rcs {" in css and ".pg-ns {" in css and ".pg-useg button.on {" in css
    lab, labcss = _web("lab.js"), _web("lab.css")          # (the same day: the lamp of a server ignores a filtered address and the faults of the way to it)
    assert "const labLampTone = s =>" in lab and "labIssues(s).some(i => !i.path)" in lab and lab.count(", false, true);") == 3 and "true, true);" in lab and lab.count('data-lamp="') == 3
    assert 'tr[data-lamp="bad"] { --lamp: #ff6b8b; }' in labcss and 'tr[data-lamp="warn"] { --lamp: #ffc44d; }' in labcss

def test_a_server_or_node_can_be_moved_to_any_profile_from_the_menu_or_by_dragging_its_row_onto_a_tab():
    """The owner, 2026-10-15: "a way to move a server to any profile we like"."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert 'labIt("Change tag…"' not in lab and lab.count('labSubItem("Move to profile", "tag", labMoveItems(p.id))') == 2             # (the row menu and the head of the node window)
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
    assert 'x.type === "pasarguard" && x.node_id != null' in row and 'labNodeParts({panel_id: pgn.panel_id, node_id: pgn.node_id})' in row and 'labSubItem("PasarGuard node", "box", [' in row
    assert "...nodeFly, gone, labSep, del]" in lab

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
    assert "color: var(--text) !important" in css and "img.lab-logo { filter: grayscale(1)" in css and ".lab-lamp { display: block; width: 14px; height: 14px; border-radius: 50%;" in css and 'tr[data-lamp="warn"] { --lamp: #ffc44d' in css
    assert ".lab-cpu .bar { flex: 0 1 96px" in css and "linear-gradient(90deg, #0a62ff, #4aa8ff)" in css


def test_the_lab_table_has_no_serves_column_the_address_is_next_to_the_name_and_the_profile_has_its_own_column():
    """The owner, 2026-10-15: "remove Serves, put the profile tag there instead, and the IP in the column nearest to the name"."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert "lab-serves" not in lab and "labServes" not in lab and ">Serves<" not in lab
    assert '<th data-prio="0">Name</th><th data-prio="1">IP</th><th data-prio="3" title="What is wrong with the server: GRE, the filter, a cut, a load ...">Problems</th><th data-prio="2" class="lab-ld">Load</th>' in lab
    assert lab.count("${labTagChip(p.tag, 1)}</b>") == 2 and "lab-prof" not in lab and "lab-tagp" not in lab
    assert "const labSub = s =>" in lab and "${labSub(s)}</div></div></td>" in lab
    assert ".lab-tbl .lab-tagc {" in css and ".lab-nm small .lab-busy" in css


def test_the_network_page_keeps_its_three_tabs_under_the_header_while_its_menu_list_is_open():
    """The owner, 2026-10-15: "Network opens with three tabs under the header"."""
    app = _web("app.js")
    assert 'const NAV_BAR_TOO = new Set(["tunnels"]);' in app and '&& !NAV_BAR_TOO.has(state.page) ? "1" : ""' in app


def test_the_profile_tabs_give_way_with_ellipsis_and_the_buttons_beside_them_stay_in_view():
    """The owner, 2026-10-15: long profile names pushed Add profile out of the bar; the tab boxes shrink (the name ends with "...") and the buttons stay."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert '<span class="lab-kn">${k === "any" ? "All" : esc(k)}</span>' in lab
    assert ".lab-bar .lab-ksw { flex: 0 1 auto; min-width: 0; }" in css and ".lab-bar .lab-tabact { flex: none; }" in css and ".lab-kinds .lab-kn { min-width: 0; overflow: hidden; text-overflow: ellipsis;" in css


def test_move_to_profile_is_a_flyout_and_a_plain_server_can_be_made_a_pasarguard_node():
    """The owner, 2026-10-15: "Move to profile opens another menu beside it with the profile names; choosing one moves the server"; "a server added under All: a move that makes it a PasarGuard node"; the lamp is behind the icon, the name a little to the right."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert "window.labSubOpen = (i, btn, focus) =>" in lab and "const labSubHtml = " in lab and "function labSubClose()" in lab and 'class="lab-chev">›</span>' in lab
    assert "window.labMenuPick = (i, j) =>" in lab and "labSubEl && labSubEl.contains(e.target)" in lab
    assert "const labMoveItems = id =>" in lab and 'labItem("＋ New profile…", () => labMoveNew(id))' in lab and "on: t.toLowerCase() === cur" in lab
    assert 'labIt("Make a PasarGuard node…", "box", () => labMakeNode(p.id)' in lab and "window.labMakeNode = async id =>" in lab and "labCanMakeNode(s)" in lab and "await pgSrvMake(s.id, s.name," in lab
    assert ".lab-menu.lab-submenu { z-index: 71" in css and ".lab-mi.has-sub .lab-chev" in css and ".lab-tbl td.name .lab-nmw { margin-left: 12px; }" in css
    assert "--lab-pad: 16px; --lab-dot: 14px; --lab-gap: 20px;" in css and "srv-ic::before" not in css


def test_the_lab_row_is_the_owners_drawing_lamp_flag_name_provider_address_cpu_chart_traffic_and_two_buttons():
    """The owner, 2026-10-15: "make the design like this" (a lamp, the flag and the icon, the name with its PROVIDER under it - not the country, the address with a globe, the CPU number with a thin bar and RAM / Disk - the small CPU chart was taken out again, the traffic in two lines, the menu and a chevron)."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert lab.count('<i class="lab-lamp"') == 3 and "lab-lp" not in lab and "const labSub = s =>" in lab and "s.provider ?" in lab
    assert lab.count('<i class="lab-glb">${LAB_GLB}</i>') == 2 and "labSparkSvg" not in lab and "/api/profiles/spark" not in lab
    assert lab.count('<span class="tv"><b>') == 4 and ">Mb/s</small>" in lab and "const labGo = id =>" not in lab and "${labGo(" not in lab and "lab-go" not in lab
    assert ":is(td.lab-pb, td.lab-ip, td.lab-ld, td.lab-trf, td.lab-act)::before" in css and ".lab-ldx .lab-cpu b { order: -1;" in css and ".lab-spk" not in css and ".lab-go" not in css


def test_the_flag_fades_on_both_sides_there_is_no_check_button_and_add_server_is_the_kit_button():
    """The owner, 2026-10-15: "the flags fade on both sides", "remove Check", "the chevrons go", "Add server like the theme sheet: its theme, icon and style"."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert "labCheck()" not in lab and "window.labCheck = " not in lab and 'class="sm lab-chk"' not in lab and "window.labCheckOne = " in lab
    assert '<span class="lab-ic"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></span><span class="lab-lb">Add server</span>' in lab and "＋</span><span class=\"lab-lb\">Add server" not in lab
    assert "td.name.has-flag::before { left: calc(var(--lab-start) - 30px); right: auto; width: 110px;" in css and "button.lab-add::before" in css and ".lab-bar .lab-chk" not in css


def test_the_bar_of_a_page_is_in_the_kits_look_primary_secondary_chip_segmented_and_field():
    """The owner, 2026-10-15: "these buttons and this row: this style" (the sheet of the UI kit)."""
    css = _web("lab.css")
    assert "width:" not in _web("uikit.css")                                           # (the kit file sets looks only, never a size)
    assert ".toolbar.joined :is(button.cta, button.pri) { border-radius: 11px !important; background-image: linear-gradient(180deg, #1a74ff 0%, #0a5cf2 100%)" in css and ".toolbar.joined .cta-ic::before" in css
    assert ".toolbar.joined .tcount {" in css and ".toolbar.joined .tabs button.on { background: #2d3f68" in css and ".tb-search input:focus { border-color: var(--uk-blue)" in css
    assert 'html[data-mode="light"] body #page .toolbar.joined .tabs {' in css


def test_the_panel_bar_is_in_the_kits_look_and_its_box_of_panels_is_fixed():
    """The owner, 2026-10-15: "remove the refresh here", "make the box fixed: many panels must not go out of the page", "like a MacBook" (= the UI kit sheet)."""
    css = _web("lab.css")
    assert ".pn-bar { height: 56px; box-sizing: border-box; overflow: hidden; }" in css and ".pn-bar .pn-seg { flex: 0 1 auto; min-width: 0; overflow-x: auto;" in css and "pn-chips" not in css
    assert ".pn-bar .pn-seg > button { flex: 0 0 auto; }" in css and "button.pn-add.ico" not in css
    js, pcss = _web("pg_usage.js"), _web("pg_usage.css")
    assert "window.pgUsagePop = btn =>" in js and "window.pgUsageRel = d =>" in js and "window.pgUsageAbs = () =>" in js and "pgUsageDates" not in js and 'class="pg-useg" role="group" aria-label="Range"' not in js
    assert ".pg-pop.open { opacity: 1; transform: none;" in pcss and ".pg-pop-ul {" in pcss and "prefers-reduced-motion" in pcss


def test_the_usage_overview_cards_are_two_rows_of_three_in_fixed_boxes():
    """The owner, 2026-10-15: "put them in two rows, fixed boxes, like a MacBook" (the UI kit sheet)."""
    css = _web("pg_usage.css")
    assert "html body #page .pg-rcs { grid-template-columns: repeat(3, minmax(0, 1fr)); grid-auto-rows: 220px;" in css and ".pg-rc { height: 100%; box-sizing: border-box;" in css and ".pg-rc-go { width: 28px; height: 28px; border-radius: 9px;" in css


def test_flags_keep_their_shape_tabs_keep_one_size_the_open_one_is_blue_and_windows_are_in_the_kits_look():
    """The owner, 2026-10-15: "the flags less stretched, fading more, grey and in colour when the row is pointed at", "tabs never change size, the chosen one a different colour", "the Add a server window like a MacBook"."""
    css = _web("lab.css")
    assert "td.name.has-flag::before { left: calc(var(--lab-start) - 30px); right: auto; width: 110px; background-size: cover;" in css and "opacity: .24 !important; filter: grayscale(1) !important;" in css and ".lab-tbl tr:hover td.name.has-flag::before { opacity: .4 !important; filter: none !important; }" in css
    assert ".tabs button { box-sizing: border-box; border: 1px solid transparent; font-weight: 600 !important; }" in css and ".tabs button.on { background: #2d3f68 !important;" in css
    assert "html body #modal :is(input:not([type=checkbox])" in css and "#modal input[type=number]::-webkit-inner-spin-button" in css and "html body #modal button.pri { border-radius: 11px !important;" in css and "#modal .modal-head > button { border-radius: 11px" in css
    assert "${dates}" not in _web("pg_usage.js") and "pg-usage-dates" not in _web("pg_usage.js")


def test_the_load_of_a_row_is_centred_and_its_bar_is_the_owners_flat_soft_blue():
    """The owner, 2026-10-15: "in the middle, the bar a little longer, the colour like the picture I sent" (a thick round bar, a soft blue on a slate track)."""
    css = _web("lab.css")
    assert ".lab-tbl th.lab-ld, html body #page .lab-tbl td.lab-ld { text-align: center; }" in css and ".lab-ldx .lab-cpu .bar { max-width: 170px; height: 9px; }" in css
    assert ".bar i { min-width: 8px; background: #66a3ff !important; box-shadow: none; }" in css and ".bar { background: #2a3350 !important; box-shadow: none; }" in css


def test_the_menus_use_flyouts_and_the_usage_range_button_lives_in_the_row_of_the_sections():
    """The owner, 2026-10-15: "as much as you can like Move to profile (flyouts), only the important actions on their own"; "bring the date to the lower row and make them alike"."""
    lab, js, css = _web("lab.js"), _web("pg_usage.js"), _web("pg_usage.css")
    assert lab.count("labSubItem(") >= 8 and 'labSubItem("Access", "key", access)' in lab and 'labSubItem("Tests", "gre", tests, {off, tip})' in lab and 'labSubItem("Install", "box", install, {off, tip})' in lab
    assert "monitor: '<path d=\"M6 20V11M12 20V4M18 20v-7\"/>'" in lab and "manage: '<path d=\"M4 7h10M18 7h2" in lab
    assert '<div class="pg-usec-row">' in js and "${back}<span class=\"pg-usec-r\">" in js and "${pgUsageRangeBtn()}</span></div>" in js and 'class="pg-useg pg-usec"' not in js and 'data-for="look"' in js and 'class="pg-lk${cur === "look"' in js and '["look", "Look up", true]' in js and 'class="pg-rng"' not in js.split("function pgUsageBarHtml() {", 1)[1].split("\n}", 1)[0]
    assert ".pg-usec-row .pg-rng { height: 36px; margin-left: auto;" in css and ".pg-usec-row .pg-back {" in css and ".pg-usec-row .pg-lk {" in css and ".pg-usec-r {" in css


def test_the_panel_tab_buttons_are_one_size_with_the_kits_radius_and_no_halo():
    """The owner, 2026-10-15: "the buttons differ in size in every tab: make them fixed and alike", "fix their rounded corners, like a MacBook"."""
    css, ucss = _web("lab.css"), _web("pg_usage.css")
    assert ".tabs { height: 36px; box-sizing: border-box; align-items: center; }" in ucss and ".tabs button { height: 28px !important; border-radius: 8px !important;" in ucss
    assert "0 0 0 3px rgba(47, 123, 255, .14)" not in css and ".tabs button.on { background: #2d3f68 !important; border-color: rgba(120, 170, 255, .5) !important; color: #fff !important; box-shadow: inset 0 0 0 1px rgba(120, 170, 255, .4) !important; }" in css


def test_no_word_over_the_lamp_no_refresh_in_usage_and_the_two_rows_of_the_panel_page_are_one_surface():
    """The owner, 2026-10-15: "remove the word Status so the names come closer", "remove the Refresh button", "make the two rows alike, like a MacBook"."""
    lab, css, js = _web("lab.js"), _web("lab.css"), _web("pg_usage.js")
    assert ">Status<" not in lab and "pgUsageRefresh" not in js and "↻ Refresh" not in js
    assert ".pn-top.pn-bar, #pn-body .pg-bar, #pn-body > .pn-bars > .toolbar) { background: #141a2b !important;" in css


def test_an_autofilled_field_keeps_the_kits_colours_and_the_doctor_buttons_are_one_segmented_look():
    """The owner, 2026-10-15: "the password field is yellow and the eye cannot be seen", "these buttons: size and style like a MacBook, alike"."""
    uk, css, app = _web("uikit.css"), _web("lab.css"), _web("app.js")
    assert "input:-webkit-autofill" in uk and "-webkit-text-fill-color: #e8ebf7 !important" in uk and "html body .eye { color: var(--uk-mut); }" in uk and "width:" not in uk
    assert ".ap-ttabs, .ana-head .tabs, .bt-range) { display: inline-flex;" in css and ".ap-ttabs .tab-ic {" in css and ".ap-ttabs .tic" not in css and ".ap-thead button:not([data-k]) { height: 32px; border-radius: 11px;" in css
    assert '<span class="tab-ic">🔧</span> In progress' in app and '<span class="tab-ic">📊</span> Analyst' in app and '<i class="tic">' not in app.split("class=\"card ap-tasks fx-box\"", 1)[1].split("ap-tbody", 1)[0]          # (.tic is the round flag of a tunnel: it drew a ring around every icon)


def test_the_geometry_of_a_lab_row_is_three_numbers_and_every_row_has_one_cell_per_column():
    """The owner, 2026-10-15: "the distance from the lamp exact and engineered, checked with a test": the lamp is inside the name cell, pad + dot + gap is where the icon starts, and the header, the colgroup and every kind of row agree on the number of columns."""
    import re

    lab, css = _web("lab.js"), _web("lab.css")
    pad, dot, gap = (int(re.search(rf"--lab-{k}: (\d+)px;", css).group(1)) for k in ("pad", "dot", "gap"))
    assert (pad, dot, gap) == (16, 14, 20) and "--lab-start: calc(var(--lab-pad) + var(--lab-dot) + var(--lab-gap));" in css
    assert "td.name, html body #page .lab-tbl th:first-child { padding-left: var(--lab-start); }" in css and "left: var(--lab-pad); top: 50%; width: var(--lab-dot); height: var(--lab-dot); margin: calc(var(--lab-dot) / -2) 0 0;" in css
    assert ".lab-tbl td.name .lab-nmw { margin-left: 0; }" in css and ".lab-tbl td.name .srv-ic { width: var(--lab-icon); height: var(--lab-icon); }" in css
    cols = re.findall(r'<col style="width:(\d+)%">', lab)
    heads = re.findall(r"<th[ >]", lab.split("<thead>", 1)[1].split("</thead>", 1)[0])
    server = lab.split("function labServerRow(p) {", 1)[1].split("const LAB_AD", 1)[0]
    node = lab.split("function labNodeRowHtml(p) {", 1)[1].split("const labGoneRow", 1)[0]
    gone = lab.split("const labGoneRow = ", 1)[1].split("const labRowOf", 1)[0]
    assert sum(map(int, cols)) == 100 and len(cols) == len(heads) == 7
    assert len(re.findall(r"<td[ >]", server)) == len(re.findall(r"<td[ >]", node)) == len(re.findall(r"<td[ >]", gone)) == 7
    assert all(t.count('<i class="lab-lamp"') == 1 and t.index('<i class="lab-lamp"') < t.index('<div class="lab-nmw">') for t in (server, node, gone))     # the lamp is the first thing in the name cell


def test_the_lab_has_a_traffic_used_column_made_of_the_bytes_since_boot():
    """The owner, 2026-10-15: "put a traffic usage column here": the bytes the server's card carried since it started (a node: what its panel counted), value over unit, in the order Load | Traffic used | Traffic Mbit/s."""
    lab = _web("lab.js")
    assert '<th data-prio="3" title="Everything the network card has carried since the server started' in lab and ">Traffic used</th>" in lab
    assert lab.count('class="lab-trf lab-used"') == 2 and "labUsed(f.rx_bytes, f.tx_bytes)" in lab and "labUsed(I.d.downlink, I.d.uplink)" in lab and "const labUsed = (rx, tx) =>" in lab
    assert lab.index('class="lab-trf lab-used"') < lab.index('<td class="lab-trf">')                               # (the used cell comes before the speed cell)


def test_the_problems_of_a_server_are_tags_after_its_name_and_the_search_finds_them():
    """The owner, 2026-10-15: "when a server has a problem, put it as a tag in front of it: GRE x, Filtered ..." (the lamp ignores the way to the server, so the tags say it)."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert "const labIssueChips = s =>" in lab and "list.slice(0, 3)" in lab and '<td class="lab-pb">${labIssueChips(s)}</td>' in lab and "${labTagChip(p.tag, 1)}</b>" in lab and "...labIssues(s).map(i => i.txt)" in lab
    assert 'class="pill lab-iss ${i.sev}"' in lab and "LAB_ISS_IC.bad" in lab and '"GRE ✕"' in lab and 'add("bad", "Filtered"' in lab
    assert ".lab-tbl td.lab-pb .lab-iss.bad { color: #ff8a9b;" in css and ".lab-iss.warn { color: #f5b94a;" in css and "height: 18px; margin: 2px 4px 2px 0; padding: 0 7px;" in css and "font-size: 10.5px;" in css


def test_every_helper_of_the_lab_rows_starts_its_own_line_so_no_comment_swallows_it():
    """A // comment glued in front of the next declaration silently removes it (the page then says "LAB_ISS_IC is not defined"): the helpers of the rows each begin a line."""
    import re

    lab = _web("lab.js")

    for name in ("LAB_AD", "LAB_GLB", "LAB_ISS_IC", "labUsed", "labIssueChips", "labTagChip", "labSub", "labLampTone", "labBusyHtml", "labPgSrv", "labGoneRow", "labRowOf"):
        assert re.search(rf"^(const|function) {name}\b", lab, re.MULTILINE), name


def test_a_node_row_has_no_version_line():
    """The owner, 2026-10-15: "remove the node version altogether: one of them has it, two do not"."""
    import re

    lab = _web("lab.js")
    node = lab.split("function labNodeRowHtml(p) {", 1)[1].split("const labGoneRow", 1)[0]
    assert "ver" not in re.findall(r"\b(ver)\b", node) and "d.version" not in node and "<small>" not in node.split('class="lab-nm"', 1)[1].split("</div>", 1)[0]


def test_the_traffic_arrows_have_the_colour_of_the_small_text_under_the_load():
    """The owner, 2026-10-15: "only the colour of the arrows like the colour of this text (RAM 35 % · Disk 33 %)"."""
    css = _web("lab.css")
    assert ".lab-tbl .lab-trf .ta.d, html body #page .lab-tbl .lab-trf .ta.u { color: var(--mut); }" in css and ".lab-ldw small { color: var(--mut);" in css


def test_the_lab_list_is_a_box_of_one_fixed_height():
    """The owner, 2026-10-15: "the box of this part fixed too": the generic box fitter gives the Lab list a height, not only a maximum height."""
    app = _web("app.js")
    assert 'const fixed = !!pg.querySelector(".cf-top, .lab-root");' in app and "if (fixed) { b.style.height = mh + " in app


def test_the_segment_buttons_and_icon_buttons_of_the_panel_bar_fit_inside_their_track():
    """The owner, 2026-10-15: "fix the boxes here too" - measured in a real browser the 32 px buttons stuck out of the 36 px track: 28 px now."""
    css = _web("lab.css")
    assert ".pn-bar .pn-ksw .pn-seg { height: 36px; box-sizing: border-box; align-items: center; }" in css and ".pn-bar .pn-ksw .pn-seg > button { height: 28px; min-height: 0;" in css and ".pn-ksw .lab-tabact .lab-ib { width: 28px; height: 28px; min-height: 0; }" in css


def test_the_banner_of_a_server_window_is_one_slim_row():
    """The owner, 2026-10-15: "instead of this big box a more compact thing in one row": icon, title, what is wrong and the buttons in one line."""
    lab, css = _web("lab.js"), _web("lab.css")
    banner = lab.split("function labBanner(s) {", 1)[1].split("/* -----", 1)[0]
    assert 'class="lb-m ${i.lvl === "bad" ? "err" : ""}" title="${esc(i.text)}"' in banner and banner.count('<div class="lb-t">') == 4
    assert ".lab-ban { align-items: center; gap: 10px; min-height: 42px;" in css and ".lab-ban .lb-t > .lb-m { min-width: 0; overflow: hidden; text-overflow: ellipsis;" in css


def test_the_lookup_card_has_the_kits_look_and_an_icon_button_instead_of_show_usage():
    """The owner, 2026-10-15: "make this like a MacBook and put ... in place of its button" (read as: the text button becomes an icon button)."""
    js, css = _web("pg_usage.js"), _web("pg_usage.css")
    form = js.split("function pgUsageLookFormHtml() {", 1)[1].split("\n}", 1)[0]
    assert 'class="pg-ibtn"' in form and "Show usage</button>" not in form and 'aria-label="Show usage"' in form and "pgUsageIcon.search" in form
    assert ".pg-usage-look .pg-ibtn { flex: none; width: 36px; height: 36px;" in css and ".pg-usage-look .pg-usage-row input { flex: 1 1 auto;" in css and ".pg-usage-look .pg-useg { height: 36px;" in css


def test_the_kebab_dots_are_vertical_and_the_heard_over_ssh_strip_is_blue():
    """The owner, 2026-10-15: "make these dots vertical", "the green bar changes to this colour" (a bright blue, the quiet cells a slate blue)."""
    lab, css = _web("lab.js"), _web("lab.css")
    assert '<circle cx="12" cy="5" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="12" cy="19" r="1.9"/>' in lab and 'cx="5" cy="12"' not in lab.split("const LAB_DOTS = ", 1)[1].split("\n", 1)[0]
    assert ".lab-ups-row .c2 { background: #4d87f2; }" in css and ".lab-ups-row .cn { background: #2b3858; }" in css and ".lab-strip .c2 { background: #4d87f2; }" in css and "var(--ok); opacity: .75" not in css


def test_the_traffic_columns_have_smaller_type_and_arrows():
    """The owner, 2026-10-15: "make the fonts and the arrows smaller"."""
    css = _web("lab.css")
    assert ".lab-trf .tv b { font-size: 13px; }" in css and ".lab-tbl .lab-trf .ta { width: 14px; height: 14px; }" in css and ".lab-tbl .lab-trf .ta svg { width: 14px; height: 14px; stroke-width: 2; }" in css


def test_the_user_template_is_the_last_section_of_settings_and_the_gauges_are_soft_blue():
    """The owner, 2026-10-15: "remove the Templates tab and add only the user template inside Settings", "the green bars inside: blue like the bars I sent"."""
    app, admin, access, ucss = _web("app.js"), _web("pg_admin.js"), _web("pg_access.js"), _web("pg_usage.css")
    assert '.filter(([t]) => mine.includes(t) && t !== "templates")' in app and '(t === "settings" && pn.tab === "templates")' in app
    assert '["utpl", "User template", false, "", "pnTab(\'templates\')"]' in admin and "window.pgSetSeg = () =>" in admin and 'if (pn.tab !== "settings") { pnTab("settings"); return; }' in admin and "if (S.S.sections[i]) b.classList.toggle" in admin
    assert "render: (id, box) => pgAccessTemplates(id, box)}" in access and "pgCtplView" not in access and "pgSetSeg" in access
    assert ".pg-leg i).cool { --pc: #8db4ff; }" in ucss and 'cls: v > bad ? "bad" : v > warn ? "warn" : "cool"' in _web("pg_usage.js")


def test_the_load_of_a_row_is_smaller():
    """The owner, 2026-10-15: "make these smaller" (the number, the bar and the line under it)."""
    css = _web("lab.css")
    assert ".lab-ldx .lab-cpu b { font-size: 12px; }" in css and ".lab-ldx .lab-cpu .bar { max-width: 120px; height: 6px; }" in css and ".lab-ldw small { font-size: 10px; }" in css


def test_there_is_no_system_tab_and_its_users_box_is_in_usage_users():
    """The owner, 2026-10-15: "remove the System section and move that Users box to Usage > Users"."""
    app, js = _web("app.js"), _web("pg_usage.js")
    assert 'settings: ["usage", "settings", "templates"]};' in app and '"system"' not in app.split("const PG_SUBS = ", 1)[1].split("\n", 1)[0].split("//", 1)[0]
    assert "function pgUsageUsersBoxHtml(ov) {" in js and '<div data-for="users"><div id="pg-usage-ubox"></div>' in js and 'pgUsagePut("#pg-usage-ubox", s.ov ? pgUsageUsersBoxHtml(s.ov) : "");' in js


def test_autofill_stays_dark_inside_windows_and_the_panel_mode_switch_shows_the_chosen_one():
    """The owner, 2026-10-15: the Address field of the panel window was yellow with light text; "API key / token | Sign in once" showed no chosen one."""
    uk, css = _web("uikit.css"), _web("lab.css")
    assert "html body #modal input:-webkit-autofill:focus { -webkit-box-shadow: 0 0 0 1000px #0f1424 inset, 0 0 0 3px rgba(59, 130, 246, .30) !important;" in uk and "transition: background-color 9999s ease-out 0s !important; }" in uk and "width:" not in uk
    assert 'html[data-mode="light"] body #modal input:-webkit-autofill, ' in uk and "-webkit-text-fill-color: #14213d !important;" in uk
    assert "html body #modal .tabs.pn-mode button.on { background: #2d3f68 !important;" in css and "html body #modal .tabs.pn-mode { display: inline-flex;" in css


def test_the_add_form_has_a_drawer_per_protocol_a_core_field_and_the_user_template_tick():
    """The owner, 2026-10-15: "arrange the make-a-config part, the suggested configs in order, VLESS in a drawer, the core can be chosen from there" - and, a little later, "bring the user template back"."""
    js, css = _web("pg_kinds.js"), _web("pg_kinds.css")
    assert '<details class="pg-kind-dr" name="pg-kind-acc" data-p="${proto}"' in js and 'class="pg-dr-sel"' in js and "if (own) d.open = true;" in js and "const pgKindShort = k =>" in js
    assert 'name="template" checked' in js and "pgKindTplSync();" in js and "b.template = f.elements.template.checked;" in js and ".pg-kind-tpl .chk" in css
    form = js.split("function pgKindFormHtml(inline) {", 1)[1].split("</form>", 1)[0]
    assert form.count('name="core_id"') == 1 and form.index('name="core_id"') < form.index('<details class="pg-kind-adv">') and form.index('name="address"') < form.index('name="core_id"') < form.index('name="port"')
    assert ".pg-kind-dr > summary {" in css and ".pg-kind-dr[open] > summary::after" in css and ".pg-kind-quick { grid-template-columns: 1.1fr 1.2fr 1.2fr .8fr 1.1fr 1.7fr; }" in css


def test_settings_is_one_fixed_card_with_the_kits_segmented_sections_and_36_px_controls():
    """The owner, 2026-10-15: "redesign Settings like the other sections: neat and compact"."""
    js, css = _web("pg_admin.js"), _web("pg_admin.css")
    paint = js.split("function pgAdminPaintSettings(box) {", 1)[1].split("\nfunction pgAdminDraw", 1)[0]
    assert paint.rstrip().endswith("}") and "foot) */" in paint                              # (the function is closed on its own line: a // comment had swallowed the brace and only node --check noticed)
    assert '<div class="card pg-admin-card fx-box">' in paint and 'class="pg-admin-ib"' in paint and "One section is saved at a time." not in paint and "if (typeof fxFit === \"function\") fxFit();" in paint
    assert paint.index('class="pg-admin-sechead"') < paint.index('class="pg-admin-formbox"') < paint.index('class="pg-admin-foot"')
    assert ".pg-admin-seg { flex-wrap: nowrap; align-items: center; gap: 2px; height: 36px;" in css and ".pg-admin-seg button.on { background: #2d3f68;" in css and ".pg-admin-card .pg-admin-sw { height: 36px;" in css and "minmax(280px, 1fr)); gap: 14px 18px; align-items: end;" in css and ".pg-bar .pg-admin-ib { width: 36px; height: 36px; }" in css


def test_the_boxes_of_usage_users_are_alike_and_fixed():
    """The owner, 2026-10-15: "make the boxes of this part alike and fixed"."""
    css = _web("pg_usage.css")
    assert ".pg-ub { height: 128px; overflow: hidden; margin: 0 0 10px; }" in css and ".pg-usage-two { grid-auto-rows: 380px; }" in css
    assert '.pg-usage [data-for="users"] .pg-usage-card, html body #page .pg-ub { border-radius: 16px;' in css and ".pg-usage-two > div > .pg-usage-card { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }" in css
    assert ".pg-usage-two .pg-usage-row select, html body #page .pg-usage-two .pg-usage-row input { height: 36px;" in css