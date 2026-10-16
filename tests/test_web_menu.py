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


def test_a_panel_page_without_a_panel_is_one_fixed_card_with_nothing_to_press():
    """The owner, 2026-10-15: "if no panel was added, the boxes stay" - and later "a fixed box, and remove the + Add a panel in General button from the middle of the page"."""
    app = _web("app.js")
    assert "const pnEmptyPage = () =>" in app and "pnEmptyPage()" in app
    page = app.split("const pnEmptyPage = () =>", 1)[1].split("const pnBar = ", 1)[0]
    assert '<div class="card pn-first fx-box">' in page and "No panel connected" in page and "button" not in page.split("`<div id=\"pn-body\">", 1)[1] and "Add a panel in General" not in app and "usr-tbl" not in page
    assert "if (!manage) pnFuse(); if (typeof fxFit === \"function\") fxFit(); return; }" in app


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
    assert "labAutoT = setTimeout(() =>" in lab and "}, 30000); };" in lab and "const labUnread = s => s.enabled && s.online == null && !(s.facts && s.facts.cpu != null);" in lab and "!document.querySelector(\"#modal .modal\")" in lab      # (a full look every 30 s; a server that is busy or never read is followed every second and a half by patching its row - 1.58.0)
    assert "if (list.some(labWaiting)) labFollow(); else labSchedule();" in lab and "setTimeout(labFollowStep, 1500)" in lab


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
    assert 'tabBtn("now", "wrench", "Active Tasks", nowN, "")' in app and 'tabBtn("ana", "bars", "Analytics", 0,' in app and '<i class="tic">' not in app.split("class=\"card ap-tasks fx-box\"", 1)[1].split("ap-tbody", 1)[0]          # (.tic is the round flag of a tunnel: it drew a ring around every icon; 1.57.33: the tabs after the owner's picture)


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
    assert '<details class="pg-kind-dr" name="pg-kind-acc" data-p="${esc(f.id)}"' in js and 'class="pg-dr-sel"' not in js and "if (own) d.open = true;" in js and "const pgKindShort = k =>" in js      # (a drawer per family: 1.58.0 - the four protocols and the ones the owner added)
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


def test_the_empty_panel_page_is_one_fixed_card():
    """The owner, 2026-10-15: "make a fixed box for this part too" (the Panel page with no panel yet)."""
    app, css = _web("app.js"), _web("lab.css")
    assert "const pnFirstCard = () => `<div class=\"card pn-first fx-box\">" in app and "pnBar([], \"\", \"\") + pnFirstCard()" in app and "<span class=\"pn-none mut\">No panel yet</span>" in app
    assert "its usage, its system" not in app and ".pn-first { display: flex; flex-direction: column; align-items: center; justify-content: center;" in css


def test_the_panel_page_helpers_each_start_their_own_line():
    """A // comment glued in front of the next declaration swallowed `const pnBar` once (the page then failed to load): the helpers of the Panel bar each begin a line."""
    import re

    app = _web("app.js")

    for name in ("pnFirstCard", "pnEmptyPage", "pnBar", "pnTabBtns", "pnTabs"):
        assert re.search(rf"^(const|function) {name}\b", app, re.MULTILINE), name


def test_the_kit_switch_the_fixed_activity_box_and_the_save_row_of_the_user_window():
    """The owner, 2026-10-15: "fix that button" (the switch of the picture), "a fixed box for Activity", "put Save and Cancel right", "the default is PasarGuard"."""
    css, users, app = _web("lab.css"), _web("pg_users.js"), _web("app.js")
    assert "label.ob2-sw i { display: block; position: relative; width: 40px; height: 24px;" in css and "label.ob2-sw:has(input:checked) i::after { transform: translateX(16px); }" in css and "linear-gradient(180deg, #2f9dff, #0a6cf0)" in css
    assert "#modal .pgu #pgu-actbody { height: 150px; max-height: none; overflow: auto; }" in css and "#modal .ue .pu-save { position: sticky; bottom: 0;" in css and "justify-content: flex-end;" in css
    assert 'onclick="closeModal()">Cancel</button><button class="pri" type="submit">Save</button></div></form>`, "wide ue pgu"' in users
    assert '<select name="type" onchange="pnHint(this.form)"><option value="pasarguard">PasarGuard</option><option value="marzban">Marzban</option></select>' in app


def test_the_roles_window_is_compact_and_the_admin_rows_have_a_face():
    """The owner, 2026-10-16: "an emoji instead of the green lines", "compact these too, fix their buttons"."""
    css, adm = _web("lab.css"), _web("pg_admin.js")
    assert 'const pgAdminEmo = base => base || "🧑‍💼"' in adm and '<i class="pg-admin-emo"' in adm and adm.count("pgAdminEmo(") >= 2 and "\nfunction pgAdminRow(a) {" in adm
    assert "#page .pg-admin-box .tbl tbody tr td:first-child { border-left: 0 !important; box-shadow: none !important; }" in css and ".pg-admin-nm .pg-admin-emo {" in css
    assert "#modal .pg-admin-form2 .frm-act { position: sticky; bottom: 0;" in css and "flex-direction: row-reverse;" in css and "#modal .pg-admin-permwrap { max-height: none; overflow: visible;" in css and "#modal .pg-admin-win .modal-body { overflow-x: hidden; }" in css
    assert "#modal .pg-roles-form .pg-roles-lim .kf > span:first-child { min-width: 0;" in css and "#modal .modal-head button > .bt { margin-left: 6px; }" in css


def test_every_admin_has_the_same_emoji():
    """The owner, 2026-10-16: "one admin emoji, the same for all"."""
    adm = _web("pg_admin.js")
    assert adm.count("${pgAdminEmo()}") == 1 and 'pgAdminEmo("🔑")' in adm and "owner ? " not in adm.split("const pgAdminEmo")[1].split("\n")[0]


def test_the_doctor_page_after_the_picture_and_the_tab_switch_that_does_not_jump():
    """The owner, 2026-10-16: the Tunnel Doctor after his design picture (three mode cards, Active Tasks / Completed / Auto Moves / Analytics, How it works, All nodes),
    a switch between CDN / Tunnel manager / Tunnel Doctor that does not jump (one bar shape, one width, no late paint of the old tab), Create tunnel without a redraw, the kit menu of its selects, the why line of a failed build."""
    app, lab, css, dr, index, main = _web("app.js"), _web("lab.js"), _web("lab.css"), _web("doctor.css"), INDEX, (WEB.parent / "main.py").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/static/doctor.css">' in index and '"doctor.css"' in main
    assert 'mcard("off", "bars", "Doctor Mode", "Only monitors and reports.' in app and 'mcard("fix", "wrench", "Auto Fix Tunnels"' in app and 'mcard("move", "server", "Auto Move to Iran Server"' in app and "window.apHow" not in app and "apNodePick" not in app and "dr-node" not in app and 'window.apStep = (btn, dir) =>' in app
    assert "`<div class=\"dr-top\">${storm}${disturb}${modes}</div>" in app and ".dr-head, .dr-top, .set-top" in app and 'if (pg0 && pg0.querySelector(".fx-box")) fxFit();' in app
    assert "navCtl.abort(); navCtl = new AbortController(); navSeq++; state.resetScroll = true; const m = $(\".main\"); if (m) m.scrollTop = 0; }\n  subSet(page, k); refresh();" in app
    assert "html body .main { scrollbar-gutter: stable; }" in css and "html body.tabbed #page > .dr-top:first-child { border-top-left-radius: 0; margin-top: 0; }" in dr and "html body #page .dr-card.on {" in dr and "html body #page .dr-empty {" in dr
    assert "const apModeDraw = (box, k) =>" in app and 'const bd = $("#ap-t-" + k); if (bd) bd.scrollTop = 0; fxFit();' in app and "html body #page .ap-tasks.fx-box { flex: none; overflow: hidden; }" in dr
    assert "window.openBuild = async () => {" in app and 'if (state.page === "tunnels" && $("#page .tun-box")) { state.build = true;' in app
    assert "function nkDdOpen(sel, anchor) {" in app and 'select[data-dd]' in app and '<select name="ttype" data-dd data-ddw="520"' in app and '<select name="iran_id" required data-dd' in app and "html body .nk-dd .nk-dd-i.on .nk-dd-ck { visibility: visible; }" in css and "function nkDdIc(name, title, g) {" in app and "html body .nk-dd .nk-dd-ic {" in css
    assert "const apWhy = s => {" in app and 'apWhyHtml(s)' in app and "html body .fx-line .fx-why {" in css
    assert 'a.classList.toggle("nav-nopanel", dim)' in lab and "window.labNoPanel = () =>" in lab and "html body nav a.nav-nopanel { opacity: .38;" in css and 'const nop = k => k !== "settings" && state.page === "pgpanel" && window.labNoPanel && labNoPanel();' in app


def test_about_and_update_after_the_picture_four_fixed_boxes_and_the_security_button():
    """The owner, 2026-10-16: "build these, all the boxes fixed" (the picture of About & update) and "a button Sign out of other devices under the password boxes"."""
    app, upd, css = _web("app.js"), _web("pg_update.js"), _web("lab.css")
    assert '`<div class="abt-wrap"><div class="abt-grid">`' in app and 'id="about-hero"' in app and 'id="about-pill"' in app and 'id="lic-head"' in app and 'id="eng-box"' in app and "${window.pgUpdBody ? pgUpdBody() : \"\"}</div>`" in app and '<div class="abt-foot"' in app and 'id="abt-env"' not in app and '"Works by", "API based"' in app and "Parallel SSH" not in app
    assert 'pgupd: ["🧩 PasarGuard"' not in app and "pgUpdCard" not in app and 'if (state.stab === "update") { aboutLoad(); enginesLoad(); licLoad(); if (window.pgUpdLoad) pgUpdLoad(); }' in app and "pgupd: SI.box" not in app
    assert 'window.pgUpdBody = () => `<div class="abt-card abt-pg">' in upd and "window.pgUpdCard" not in upd and 'id="pgu-pill"' in upd and 'id="pgu-box"' in upd
    assert "grid-template-rows: 156px 316px;" in css and "html body #page .abt-foot {" in css and 'html body #page .sf[data-name="About & update"] > h3 { display: none; }' in css and "html body #page .abt-card .eng-row {" in css
    assert 'void m.offsetWidth; m.classList.add("open");' in app and 'const hd = $("#lic-head"); if (hd) hd.innerHTML = head;' in app
    assert 'onclick="pwOutToggle(this)"' in app and 'window.pwOutToggle = b =>' in app and 'name="logout_others"' not in app and 'logout_others: out' in app and "html body #page .pwf-btns {" in css and 'class="pwf-out on" aria-pressed="true"' in app and "html body #page .pwf-btns .pwf-out {" in css and '<small>at least 10 characters</small></div><div class="pwf-f">' in app and "html body #page .si.pw .pwf-f .pwi, html body #page .si.pw .pwf-f .pwi .si-f { width: 100%;" in css and "html body #page .si.pw { grid-template-columns: 38px minmax(0, 1fr); }" in css and "html body #page .abt-foot { flex: none; display: flex; flex-wrap: nowrap; justify-content: space-between;" in css and "html[data-fluid] body #page .si.pw { grid-template-columns: 38px minmax(0, 1fr); }" in _web("fluid.css") and "html body #page .si.pw .si-b { padding-right: 46px; }" in css


def test_one_draw_at_a_time_the_bar_with_its_page_and_no_spinner_between_panel_tabs():
    """The whole-panel smoothness review, 2026-10-16: one draw at a time per page and tab; the bar of tabs shows together with its page; the Panel keeps the tab that was open, dimmed, until the new one has its data;
    a late answer never paints the users of the panel that was left; one poll per action; the engines box follows a check with a flag that dies with the box."""
    app, com, css = _web("app.js"), _web("pg_common.js"), _web("lab.css")
    assert "if (refresh.p && refresh.seq === navSeq) { refresh.again = true; return refresh.p; }" in app and "async function refreshNow() {" in app and app.count("async function refresh() {") == 1
    assert '$("#subnav").classList.toggle("hidden", !subBarOn(state.page));      // the bar of tabs shows (or goes) together with the page' in app and '$("#subnav").classList.toggle("hidden", !subBarOn(page));' not in app and 'box.classList.toggle("hidden", !subBarOn(state.page));' not in app
    assert '<div id="pn-body" class="${keep && changed ? "pn-busy" : ""}">' in app and 'c.querySelectorAll(":scope > .pn-bars").forEach(x => x.remove()); return c.innerHTML;' in app
    assert 'if (!box.isConnected || String(pn.id) !== String(id) || pn.tab !== "users") return;' in app and "const FOLLOWING = new Map();" in app and "if (FOLLOWING.has(aid)) { const f = FOLLOWING.get(aid); f.box = box; return f.p; }" in app
    assert "if (d.checking) { if (!box._follow) { box._follow = true; engineFollow(); } }" in app and 'finally { const b = $("#eng-box"); if (b) b._follow = false; }' in app
    assert "if (box.dataset.tab === pn.tab) return;" in com and "box._lt = setTimeout(() => { if (box.isConnected && box.classList.contains(\"pn-busy\")) pgSpin(box, text); }, 350);" in com and 'box.classList.remove("pn-busy"); box.dataset.tab = pn.tab;' in com
    assert "html body #page #pn-body.pn-busy > :not(.pn-bars) { opacity: .45; pointer-events: none;" in css


def test_the_shop_bot_is_seen_nowhere_in_the_menu_and_the_backup_password_is_a_switch():
    """The owner, 2026-10-16: everything of the Shop bot is off and seen nowhere in the menu; the backup password is a switch under the backup list; the schedule is one compact row."""
    app, css = _web("app.js"), _web("lab.css")
    assert 'const SET_OFF = new Set(["bot"]);' in app and "settings: Object.entries(SET_TABS).filter(([k]) => !SET_OFF.has(k)).map(([k, v]) => [k, navPlain(v[0]), TAB_SVG[k] || SI.gear])};" in app
    assert 'const cur = SET_TABS[state.stab] && !SET_OFF.has(state.stab) ? state.stab : "account";' in app and 'function tabSet(k) { if (SET_OFF.has(k)) return;' in app and "Object.entries(SET_TABS).filter(([k]) => !SET_OFF.has(k)).map(([k, v]) => `<button class=\"${k === cur ? \"on\" : \"\"}\" onclick=\"setTab('${k}')\">" in app
    assert 'Object.entries(bk.parts).filter(([k]) => k !== "bot").map(part).join("")' in app and "the shop bot: kept here" not in app
    assert 'const passRow = `<div class="bk1-r bk1-k' in app and "pgSw(!!bk.has_passphrase, \"bkPassToggle(this)\"" in app and "window.bkPassToggle = el =>" in app and 'id="bpw-form"' in app
    assert '<form class="bk1-sc bk-sched" onsubmit="return saveBackup(event)">' in app and 'name="enabled" title="Automatic backups"' in app and "html body #page .bk1 { border: 1px solid var(--uk-line); border-radius: 18px;" in css and "html body #page .bk1 .bk1-r { display: grid;" in css          # (1.59.0) the owner: "make it a box and one piece, unified, MacBook style": ONE box, a row each for the panel, the password and the schedule, a hairline between them
    assert '`<div class="bk1">${Object.entries(bk.parts).filter(([k]) => k !== "bot").map(part).join("")}${passRow}${sched}</div>`' in app and 'class="sl bpw"' not in app


def test_the_api_tab_of_settings_gathers_every_api_connection():
    """The owner, 2026-10-16: "make this section API and bring every API connection here": the tab AI became API - this panel's tokens (moved out of Security) and docs,
    the state of the AI key, the Telegram bot, the probe, the DNS accounts and the panels, each with the way to its place; the AI assistant card stays under it."""
    app = _web("app.js")
    assert 'ai: ["🔌 Api & Key",' in app and '["Api & Key"]],' in app and "ai: SI.link," in app
    assert 'sFrame("Api & Key", "This panel' in app and "async function apiLoad() {" in app and 'window.apiGo = n =>' not in app and 'const tokBox = abBox({icon: SI.key, title: "API tokens"' in app and 'const instBox = abBox({icon: SI.down, title: "Install a probe"' in app and '<div class="ab-list">${apiBox}${tokBox}${aiBox}${tgBox}${probeBox}${instBox}${dnsBox}${panelsBox}</div>' in app and "const abBox = o =>" in app
    assert '  const s = await api("/api/auth/security");' in app and '${gR}${gP}</div>${gW}`;' in app and '"narrow");\n    apiLoad();' in app and 'toast("Token revoked", "ok"); apiLoad();' in app
    assert 'const aiBody = sCat(' not in app and 'title: "Use"' not in app and '${backups}${apiFrame}${shop}' in app and 'window.aiSave = async (e, pid) =>' in app and 'window.saveAi' not in app and 'f.enabled.checked' not in app and 'onsubmit="return aiSave(event, \'${c.id}\')"' in app and 'id="${f.id}"' in app
    assert 'title: "Telegram bot", pill: tg.has_token' in app and 'form: {id: "tg-form", submit: "saveTelegram(event)"}' in app and 'alerts: ["🔔 Alerts"' not in app and 'const tgOn = !!(tg.enabled && tg.has_token && tg.chat_id);' in app and 'window.alertsWin = async kind =>' in app and "alertsWin('rules')" in app and "alertsWin('report')" in app and "alertsWin('windows')" in app and 'async function setData() {' in app and '${security}${backups}' in app
    assert "Manage in" not in app and 'const dnsBox = abBox({icon: SI.cloud, title: "DNS accounts"' in app and 'const panelsBox = abBox({icon: SI.cube, title: "Marzban / PasarGuard panels"' in app and 'const probeBox = abBox({icon: SI.probe, title: "Probe"' in app and 'form="probe-form"' in app and 'const aiBody' not in app and 'const probeBody' not in app and 'html body #page .ab-list {' in _web("lab.css") and 'html body #page .ab {' in _web("lab.css")
    assert "window.apiPanelEdit = id =>" in app and 'onclick="dnsAccForm()">${ib(SI.plus, "Add account")}' in app and 'onclick="pnAddWin()">${ib(SI.plus, "Add panel")}' in app and 'probe: ["🛰 Probe"' not in app and 'const probe = sFrame' not in app and "html body #page .sf .ss .sr.li" not in _web("lab.css") and "Open Settings → Api & Key" in app


def test_more_smoothness_the_tab_style_with_the_page_the_bar_in_place_and_quiet_timers():
    """The whole-panel smoothness review, 2026-10-16 (second batch)."""
    app = _web("app.js")
    assert 'const TABBED = new Set(["tunnels", "pgpanel", "configs"]);' in app and 'if (TABBED.has(page)) document.body.classList.add("tabbed");' in app
    assert "morphHtml(box, `<div class=\"tabs sub-tabs\">${list.map(" in app and "if (b.matches(\".fx-box\")) return; const mh = Math.max(150" in app
    assert "if (pnPS() !== pn.curPS) pnUsersLoad(+pn.id); else pnFitSoon(pn.curPS);" in app and '.nk-dd, .lab-menu, .pn-uchk:checked, .pn-hchk:checked' in app


def test_the_rows_of_active_tasks_after_the_picture():
    """The owner, 2026-10-16 (his picture): a dot with its word, buttons with icons - blue for what to do, dark for the rest."""
    app, dr = _web("app.js"), _web("doctor.css")
    assert '${apIc("wifi")}<span>Live</span>' in app and '${apIc("sync")}<span>Move now</span>' in app and '${apIc("spark")}<span>Fix with AI</span>' in app and "📡" not in app.split("PAGES.actions = async", 1)[1].split("window.apMode", 1)[0]
    assert '"down" : "degraded") : kind === "slow" ? "degraded" : kind === "users" ? (t.ux.grade === "bad" ? "down" : "degraded") : t.state}">' in app
    assert 'html body #page .ap-tasks .ap-row .st::before { content: ""; flex: none; width: 12px;' in dr and "html body #page .ap-tasks .ap-row button.pri { background: #2f7bf5;" in dr and "html body #page .ap-tasks .nw-box.sug-box {" in dr


def test_the_model_of_the_ai_is_a_field_with_a_list_that_opens_as_the_kit_menu():
    """The owner, 2026-10-16: any model the user wants can be added; the list drops down."""
    app, css = _web("app.js"), _web("lab.css")
    assert 'const AI_MODELS = [["claude-fable-5-1", "Fable 5.1' in app and "window.nkCombo = async btn =>" in app and '<span class="nk-combo" data-pid="${c.id}"><input name="model"' in app and 'onmousedown="event.preventDefault(); nkCombo(this)"' in app
    assert "function nkDdOpen(sel, anchor) {" in app and "if (s.__combo) { s.__combo.value = s.options[i].value;" in app and "aiRemember(pid, f.model.value);" in app and "html body #page .sf .nk-combo .nk-combo-b {" in css


def test_the_add_account_type_form_in_two_columns_with_pictures():
    """The owner, 2026-10-16 (his picture): the drawers on the left, one card with the note and the fields in pairs on the right; one drawer open at a time."""
    js, css = _web("pg_kinds.js"), _web("pg_kinds.css")
    assert '<div class="pg-kind-cols"><div class="pg-kind-groups">${chips}</div><div class="pg-kind-side">' in js and '</div></div>${inline ? subs : ""}</div>${inline ? "</div>" : ""}`;\n}' in js and "const PKI = {};" in js and "const pgKindIc = k =>" in js
    assert '<i class="pg-dr-i" aria-hidden="true">${pgKindFamIcon(f)}</i>' in js and '<span class="pg-kf"><i class="pg-kf-i">${PKI.user}</i>' in js and 'name="core_id" data-dd' in js and 'name="group" data-dd' in js and '${PKI.spark}<span>Make it…</span>${PKI.chev}' in js
    assert 'document.addEventListener("toggle", e => {' in js and ".pg-kind-dr[open]" in js
    assert "html body #page .pg-kind-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.05fr);" in css and "html body #page .pg-kind-side .pg-kind-quick { grid-template-columns: repeat(2, minmax(0, 1fr));" in css


def test_create_tunnel_waits_for_the_second_server():
    """The owner, 2026-10-16: with no server added the button is disabled; when the second server is added it becomes active."""
    app, css = _web("app.js"), _web("lab.css")
    assert "const nSrv = (srvAll || state.serversCache || []).length;" in app and '${nSrv >= 2 ? "" : "disabled"}' in app and "add an Iran server and a foreign server first (Servers)" in app
    assert "if (Array.isArray(state.serversCache) && state.serversCache.length < 2) { toast(" in app and "html body #page .toolbar.joined button.cta:disabled {" in css


def test_add_tab_is_one_frame_with_the_subscription_configs():
    """The owner, 2026-10-16: the Add sections in one frame of one size whichever drawer is open, and the empty right side shows the subscription configs (the hosts)."""
    js, css = _web("pg_kinds.js"), _web("pg_kinds.css")
    assert "inline ? '<div class=\"pg-kind-frame\">' : \"\"" in js and 'id="pg-kind-subs"' in js and "Subscription configs" in js
    assert "window.pgKindSubs = async () =>" in js and "pgKindPick(\"vless-reality\"); pgKindSubs();" in js and "/hosts`)).hosts" in js
    assert ".pg-kind-frame .pg-kind-cols { grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr) minmax(0, 1fr);" in css and "height: clamp(500px, calc(100vh - 260px), 640px)" in css
    assert ".pg-kind-frame .pg-kind-subs { overflow: hidden; }" in css and ".pg-ks-list { flex: 1 1 auto; overflow-y: auto;" in css


def test_security_is_one_column_again_like_the_owners_picture():
    """The owner, 2026-10-16 (his picture): Settings -> Security is one column, the boxes one under the other; Sign out of other devices is a button beside Change password; no two-column grid, no switch."""
    app, css = _web("app.js"), _web("lab.css")
    assert 'box.innerHTML = `${codes}${hero}${setup}<div class="sl">${sec.setup ? "" : g2}${gR}${gP}</div>${gW}`;' in app and "sec-cols" not in app and "sec-cols" not in css
    assert '<button type="button" class="pwf-out on" aria-pressed="true" onclick="pwOutToggle(this)"' in app and "pwf-sw" not in app and "pwf-sw" not in css and 'logout_others: out' in app and "html body #page .pwf-btns .pwf-out.on {" in css
    assert "chev: true" not in app.split("const gP = sItem(")[0].split("const g2 = sItem(")[1] and "const gR = sItem({ok: onR," in app and "const gP = sItem({ok: onP," in app      # (the three arrows of Security are gone, the owner, 2026-10-16)
    assert '${on2 ? "checked" : ""}' in app and '${on2 ? "secOff()" : "secSetup()"}' in app      # (the Two-step switch works: it shows the real state and starts the turn-on / turn-off)
    assert 'gprompt("Your password, to confirm:", "", {password: true, generate: false})' in app      # (the confirm-your-password box has no Generate button, the owner, 2026-10-16)


def test_the_settings_frame_keeps_its_old_width_when_the_row_of_tabs_gets_shorter():
    """The owner, 2026-10-16: with fewer tabs the frames of Appearance and About shrank - they are as wide as before (never narrower than SET_W_MIN)."""
    app = _web("app.js")
    assert "const SET_W_MIN = 880;" in app and 'Math.round(Math.max(SET_W_MIN, right - left + 40,' in app


def test_the_settings_frame_is_one_taller_height_and_about_has_room_for_its_boxes():
    """The owner, 2026-10-16: the sizes of About & update got mixed up (cut texts, a table running into itself) and the frame must be longer; every tab's frame is one height."""
    app, css = _web("app.js"), _web("lab.css")
    assert '<div class="abt-wrap">' in app and 'fx-box' not in app.split('const about = sFrame')[1].split('/* security: filled by secLoad */')[0]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: 156px 316px; gap: 12px; }" in css and "html body #page .set-wrap > .card.sf { min-height: 760px !important; }" in css and '<small class="abt-ds">' not in app and "html body #page .abt-ds, html body #page .abt-note { display: none; }" in css and '<small class="mut abt-note">' not in app and 'disabled title="Looking for a newer version…"' in app and 'ub.disabled = !!u.up_to_date;' in app and 'class="pgu-prow"' in _web("pg_update.js") and "Manage the panels and their nodes" not in _web("pg_update.js")
    assert "grid-template-columns: minmax(0, 1.1fr) 82px minmax(0, 1.4fr) auto; gap: 6px; padding: 5px 10px; }" in css and '<span>Installed</span>' not in app and "!have.length ? '<span class=\"mut\">not installed</span>'" in app and ".abt-card .eng-row > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in css


def test_the_add_form_starts_the_plain_websocket_at_80():
    """The owner, 2026-10-16: the default port of ws is 80, not 8080."""
    js = _web("pg_kinds.js")
    assert "kd.first_port ? kd.ports[0] : kd.ports.find(p => !core.ports.includes(p)) || kd.ports[0];" in js


def test_the_cores_and_the_pasarguard_lists_sit_in_one_fixed_box_that_scrolls():
    """The owner, 2026-10-16: the panels and nodes of PasarGuard (and the cores) in ONE box of a fixed size that scrolls inside."""
    app, upd, css = _web("app.js"), _web("pg_update.js"), _web("lab.css")
    assert '<div class="eng-list"><div class="eng-head" title=' in app and 'onclick="engineCheck()"' not in app and 'Date.now() / 1000 - d.checked_at > 600' in app and '<div class="pgu-list"><div class="pgu-cap">Panels</div>' in upd and "${nodesHtml}</div>`;" in upd
    assert "html body #page :is(.eng-list, .pgu-list) { flex: 1 1 auto; min-height: 0;" in css and "overflow-y: auto;" in css


def test_no_check_buttons_in_the_about_boxes_they_look_by_themselves():
    """The owner, 2026-10-16: Tunnel cores and PasarGuard have no Check button - they are asked again by themselves."""
    app, upd = _web("app.js"), _web("pg_update.js")
    assert 'onclick="engineCheck()"' not in app and "> 600)" in app and 'onclick="pgUpdLoad(true)"' not in upd
    assert "pgu.timer = setTimeout(() => { if ($(\"#pgu-box\")) pgUpdLoad(true); }, 600000);" in upd and "Date.now() - pgu.at > 600000" in upd


def test_the_update_and_xray_buttons_of_the_nodes_have_one_size():
    """The owner, 2026-10-16: the sizes of the Update / Xray buttons of the PasarGuard nodes are fixed."""
    css = _web("lab.css")
    assert "html body #page .abt-pg .pgu-a .sm, html body #page .abt-pg .pgu-a button { flex: none; box-sizing: border-box; width: 58px; height: 24px;" in css and "html body #page .abt-pg .pgu-prow > button { flex: none; box-sizing: border-box; width: 66px; height: 24px;" in css


def test_the_cards_of_the_kit_menu_stand_apart():
    """The owner, 2026-10-16: a gap between the cards of the kit menus (the server lists of Create tunnel)."""
    css = _web("lab.css")
    assert "html body .nk-dd .nk-dd-i { margin: 0 0 6px; border: 1px solid rgba(255, 255, 255, .07); border-radius: 14px; background: #141c2f; }" in css


def test_the_tunnel_window_is_built_like_the_server_window():
    """The owner, 2026-10-16: "rebuild this box and make it exactly like the server box": the avatar, the name, the two servers and the type in the header; the alerts switch and ONE menu; the tabs Overview | Monitor; the banner, the boxes, the strip."""
    app, lab, css = _web("app.js"), _web("lab.js"), _web("lab.css")
    assert 'showModal(who, `<div id="tun-detail-${t.id}">${tunWinHtml(t)}</div>`, "wide srvwin tunwin"' in app and 'labKebab("tun", t.id, "Actions of " + t.name)' in app and 'class="srv-sw ${muted ? "" : "on"}"' in app
    assert "const TUN_SECS = [[\"overview\", \"Overview\"], [\"monitor\", \"Monitor\"]];" in app and "function tunBanner(t) {" in app and "async function tunStrip(id) {" in app and "window.tunWtab = k =>" in app and "function tunAfter(t)" in app
    assert "function tunBar(" not in app and "function detailRow(" not in app and "function tunPartHtml(" not in app and "loadTunnelCharts(id); return; } }" not in app
    assert 'if (ctx === "tun") return tunMenuItems(id);' in lab and "function tunMenuItems(id) {" in lab and 'labIt("Remove tunnel…", "trash"' in lab and "{danger: true}" in lab
    assert "html body #modal .modal.srvwin.tunwin { min-height: min(776px, calc(100vh - 96px)); }" in css and "html body #modal .mh-flag.mh-tun {" in css


def test_the_sign_in_page_is_rebuilt_in_the_kit_look_with_an_eye_on_every_password_box():
    """The owner, 2026-10-16: the sign-in page built again from the start in the look of the kit, an eye (show / hide) on the password boxes; no inline handler on it."""
    index, app, css = INDEX, _web("app.js"), _web("login.css")
    main = (WEB.parent / "main.py").read_text(encoding="utf-8")
    block = index[index.index('<div id="login"'):index.index('<div id="app"')]
    assert 'class="lg-card"' in block and block.count('class="lg-eye"') == 3 and 'href="/static/login.css"' in index and '"login.css"' in main
    assert "onclick=" not in block and "onsubmit=" not in block and 'autocomplete="current-password"' in block and block.count('autocomplete="new-password"') == 2 and 'autocomplete="one-time-code"' in block and 'name="user" value="admin" autocomplete="username"' in block
    assert "function lgEye(b, on) {" in app and "if (ls.busy) return;" in app and 'getModifierState("CapsLock")' in app and 'btns.forEach(b => { b.disabled = true; });' in app and "lgMsg(" in app and "go(state.page);" in app and "go = [...form" not in app
    assert "html body .lg-in:focus-within { border-color: var(--uk-blue);" in css and "html body .lg-eye[aria-pressed=\"true\"] .e-off { display: block; }" in css and 'html[data-mode="light"] body .lg-card' in css and "inset-inline-end" in css


def test_the_dashboard_has_the_getting_started_card_and_no_empty_slots():
    """The owner, 2026-10-16: redesign the dashboard; a new panel asks the API keys at the start (GET /api/setup)."""
    app, css = _web("app.js"), _web("lab.css")
    assert "function setupCard(su) {" in app and 'api("/api/setup").catch(() => null)' in app and "${setupCard(su)}" in app and "window.setupSave = async (e, kind) =>" in app and "dashFree" not in app and "free space" not in app
    assert 'onsubmit="return setupSave(event, \'telegram\')"' in app and 'onsubmit="return setupSave(event, \'ai\')"' in app and 'onclick="setupShow()"' in app and "su && su.dismissed && su.done < su.total" in app
    assert "html body #page .dash-setup {" in css and "html body #page .su-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; align-items: start;" in css and "html body #page .dash-cols > .card { height: 376px;" in css


def test_the_light_theme_sheet_holds_only_light_rules_and_loads_last():
    """The owner, 2026-10-16: every element and font of the light theme clear. light.css is loaded after every sheet and every rule in it is a light-mode rule (the dark theme is untouched)."""
    index, main = INDEX, (WEB.parent / "main.py").read_text(encoding="utf-8")
    css = _web("light.css")
    assert index.index('href="/static/login.css"') < index.index('href="/static/light.css"') and '"light.css"' in main
    assert all(index.index('href="/static/light.css"') > index.index(f'href="/static/{n}"') for n in ("app.css", "lab.css", "doctor.css", "fluid.css", "uikit.css", "pg_kinds.css"))
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for chunk in body.split("}"):
        if "{" not in chunk:
            continue
        selector = chunk.split("{", 1)[0].strip()
        assert 'data-mode="light"' in selector, selector[:120]      # a rule that is not scoped to the light theme would change the dark one
    assert "--mut: #4d5b7c" in css and "--bad: #b3123a" in css and "--warn: #a15c00" in css and "--ok: #0a7a52" in css
    assert ".toolbar.joined button:not(.cta)" in css and ":hover { background-color: #eaf1fe !important;" in css      # the dark navy hover of the toolbar buttons was dark text on navy
    assert 'html[data-mode="light"] body #modal :is(button.pri, button.primary, .cta) {' in css and "color: #fff !important;" in css


def test_the_health_box_of_the_dashboard_keeps_each_detail_on_one_line():
    """A long list of stale jobs was three lines high and ran over the next row: one line, an ellipsis, the whole text as the tooltip."""
    app, css = _web("app.js"), _web("lab.css")
    assert '<small title="${sub}">${sub}</small>' in app
    assert "html body #page .dash-cols .dash-name small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in css


def test_the_page_tells_the_panel_about_its_script_errors_and_copies_the_diagnostics():
    """Review 2026-10-16: a script error vanished in the owner's browser; and a bug report had no single page of facts."""
    app, css = _web("app.js"), _web("lab.css")
    assert 'fetch("/api/client-error", {method: "POST"' in app and 'window.addEventListener("unhandledrejection"' in app and 'window.addEventListener("error"' in app and "sent >= 5 || seen.has(key)" in app and "ResizeObserver loop" in app and "Failed to fetch|NetworkError|Load failed|not logged in" in app and "enginesLoad().catch(() => {}); return; }" in app and 'r.name === "AbortError"' in app
    assert "window.abtDiag = async () =>" in app and 'api("/api/system/diagnostics")' in app and "navigator.clipboard.writeText(txt)" in app and 'class="sm abt-diag" onclick="abtDiag()"' in app
    assert "html body #page .abt-foot > button.abt-diag {" in css and "html body #page .abt-foot > span:nth-last-child(2) {" in css


def test_the_cores_box_does_not_ask_for_a_button_that_is_gone():
    """The Check button of the cores was taken away (the check runs by itself): its empty text and its status no longer say "press Check"."""
    app = _web("app.js")
    assert "press Check" not in app and "Press “Check for updates”" not in app and "it happens by itself, in a moment" in app


def test_the_add_table_has_an_emoji_on_every_drawer_short_names_and_a_row_to_add_a_protocol():
    """The owner, 2026-10-16: "write the names short, e.g. ws+tls", "every protocol its own emoji (VLESS, VMess ...)" and "let the user add other protocols to the table too"."""
    js, css = _web("pg_kinds.js"), _web("pg_kinds.css")
    assert "function pgKindDrawers(o)" in js and '<i class="pg-dr-i" aria-hidden="true">${pgKindFamIcon(f)}</i>' in js and "const pgKindFamIcon = f =>" in js and "(o.families || [])" in js and "o.kinds.filter(k => k.family === f.id)" in js          # one drawer per family, its own picture (a thin line icon of the kit; an emoji of your own is shown in black and white)
    from nikatel.services import pg_kinds
    icons = re.search(r"PG_CUST_ICONS = \[(.*?)\]", js).group(1)
    assert re.findall(r'"([a-z]+)"', icons) == list(pg_kinds.ICONS) and all(f"""["{k}", '<""" in js for k in pg_kinds.ICONS)      # the picker offers the icons of the server's list, and every one of them is drawn
    assert 'const pgKindShort = k => String((k && (k.short || k.title)) || "");' in js and "<span>${esc(pgKindShort(k))}</span>" in js and 'title="${esc(k.title + (k.note ? " — " + k.note : ""))}"' in js      # the short name on the button, the long one in its tooltip
    assert 'onclick="pgKindCustom()"' in js and "Add protocol</span></button>" in js and 'd.dataset.p' in js and "kd.family === d.dataset.p" in js          # the last row of the drawers adds a protocol; the drawer of a chosen kind opens
    for fn in ("pgKindCustom", "pgCustSave", "pgCustSync", "pgCustVia", "pgCustExample", "pgKindCustomDel"):
        assert f"window.{fn} = " in js, fn

    assert 'class="sm danger" onclick="pgKindCustomDel(pgKind.k)"' in js and "kd.custom ?" in js          # Edit and Delete sit on the note of a protocol of your own, and only there
    assert "protocol: g(\"protocol\"), network: g(\"network\")" in js and "b.text = f.elements.text.value" in js and '"/kinds/custom"' in js and "`/kinds/custom/${encodeURIComponent(kid)}`" in js
    assert "const pgKindLinkOwn = p =>" in js and "pgKindLinkKind(p) || pgKindLinkOwn(p)" in js          # a pasted link finds a protocol of your own too
    assert ".pg-kind-addp {" in css and ".pg-dr-i .pg-dr-e {" in css and "filter: grayscale(1)" in css and ".pg-kn-a {" in css and ".pg-cust-via {" in css and ".pg-ico.on {" in css
    assert js.index("window.pgKindCustom = ") > js.index("function pgKindDone(out)")          # (the form of a protocol of your own comes after the Add form: its fields are not part of the one the Add form tests read)


def test_every_subscription_config_has_a_switch_and_the_menu_modify_disable_duplicate_remove():
    """The owner, 2026-10-16: "for every config an on/off switch, edit and delete", and his picture of the menu: Modify, Disable, Duplicate, a line, Remove in red."""
    js, hosts, lab, app = _web("pg_kinds.js"), _web("pg_hosts.js"), _web("lab.js"), _web("app.js")
    row = js.split("const pgKindRow = x =>", 1)[1].split("const pgKindHostOf", 1)[0]
    assert "pgSw(!x.disabled, \"pgKindHostSw(this)\"" in row and 'onclick="pgKindHostMenu(this, event)"' in row and "${LAB_DOTS}" in row and 'class="lab-kb"' in row and "pg-ks-s" not in js          # the switch and the "⋯" button; the old on/off badge is gone
    menu = js.split("window.pgKindHostMenu = ", 1)[1].split("const pgKindHostSet", 1)[0]
    assert menu.index('labIt("Modify", "edit"') < menu.index('x.disabled ? "Enable" : "Disable"') < menu.index('labIt("Duplicate", "copy"') < menu.index("labSep") < menu.index('labIt("Remove", "trash"') and "{danger: true}" in menu
    assert "pgHostOpen(+pn.id, +x.key)" in menu and "pgHostClone(+pn.id, +x.key)" in menu and "pgHostDelete(+pn.id, +x.key, x.remark" in menu          # the same windows and calls as the Hosts tab: nothing is written twice
    icons = lab.split("const LAB_P = {", 1)[1].split("};", 1)[0]
    assert " off: '" in icons and " on: '" in icons and " copy: '" in icons
    assert hosts.count("pgHostsDone();") == 4 and "refresh(); } catch" not in hosts and "const pgHostsDone = () => { if (!(window.pgKindHostsDone && pgKindHostsDone())) refresh(); };" in hosts          # after a save, a copy, a delete or a restore the Add tab reads its list again - the form keeps what was typed
    assert "window.pgKindHostsDone = () => { if (!$(\"#pg-kind-subs\")) return false;" in js and "l.scrollTop = top;" in js
    assert 'state.build = document.querySelector("#pg-kind-page #pg-kind-form") ? "kinds" : null;' in app          # closing a window never lets the page draw itself again over the Add form


def test_adding_a_server_moves_nothing_and_says_what_is_being_done_from_the_first_moment():
    """The owner, 2026-10-16: "when you add a server the page jumps several times" and "the status of the server is not clear at all - from the very first moment show what is being done"."""
    lab, css, app = _web("lab.js"), _web("lab.css"), _web("app.js")
    assert 'class="lbox lab-box0"' in lab and "const labBox = shown =>" in lab and '<div id="lab-list">${labBox(shown)}</div>' in lab and ".lab-box0 { display: grid; place-items: center; }" in css      # the empty page is a box like the list: the first row changes nothing around it
    assert 'class="lab-list"' not in lab and '"lab-list"' not in lab.replace('id="lab-list"', "") and "\n.lab-list {" not in css      # (no second look for the empty page)
    assert "function labPatch()" in lab and "morphNode(tr, tpl.firstElementChild)" in lab and "morphHtml(st, labStatsHtml(shown))" in lab and "async function labTick()" in lab and "if (!labPatch()) refresh();" in lab      # the rows are rewritten where they stand
    assert "window.labFollow = (secs = 0) =>" in lab and "async function labFollowStep()" in lab and "setTimeout(labFollowStep, 1500)" in lab and "const labWaiting = s =>" in lab
    assert 'labBusyHtml({what: "node", detail: "Waiting for the first reading…"})' in lab and "labUnread(s) && !noLogin(s)" in lab      # a server that was never read says so
    assert ".lab-chip b { display: inline-block; min-width: var(--cw, 3ch);" in css and ".lab-chip.c-cpu b { --cw: 4ch; }" in css      # the numbers of the chips have their own room
    assert "window.labRoleOf = labRoleOf;" in lab      # the tag Iran / Foreign decides the role at once (it was never found: a top-level const is not on window)
    save = app.split("window.saveServer = async (e, id) => {", 1)[1].split("window.srvPassword", 1)[0]
    assert "/onboard" in save and 'const first = id ? ["gre"] : ["read", "gre"]' in save and 'f.get("x_cores") === "on"' in save and "labFollow(30)" in save and "labFollow(20)" in save and "/gre-check" not in save
    assert "if (later && cores && connected) await prep([\"cores\"]);" in save and "submit.textContent = id ? \"Saving…\" : \"Adding…\"" in save and "r.online === true" in save      # the cores come last after a node or extras; the button says Adding…; a key that does not log in is told
    assert 'name="x_cores" type="checkbox" checked' in app and "Put the tunnel cores on it" in app      # the tick of the Add form (on by default: "they must install by themselves")


def test_the_cores_box_has_an_install_button_and_a_server_has_tunnel_cores_in_its_menu():
    """The owner, 2026-10-16: "why are Paqet, Chisel and Xray not installed? They should install by themselves": a new server gets them at once; the ones added before get them from here."""
    app, lab = _web("app.js"), _web("lab.js")
    assert "⬇ Install</button>" in app and "engineUpdate('${k}', true)" in app and "window.engineUpdate = async (k, missing) =>" in app and "/update${missing ? \"?missing=true\" : \"\"}" in app and "enginesLoad.last = eng;" in app
    assert 'labIt("Tunnel cores…", "box", () => srvCores(s.id, nm))' in lab and "window.srvCores = async (id, name) =>" in lab and 'steps: ["cores"]' in lab
    install = (WEB.parent.parent / "install.sh").read_text(encoding="utf-8")
    assert "downloads the tunnel cores" in install and "engines" in install

def test_the_ai_models_have_a_row_each_and_the_page_knows_every_ai_of_the_server():
    """The owner, 2026-10-16: "add the other AI models too: DeepSeek, ChatGPT, Grok, Gemini and more" - one row for each AI (its key, its model, whether it is the one in use), five always there, the rest from "Add another AI"."""
    from nikatel.services import ai

    app, css = _web("app.js"), _web("lab.css")
    ids = list(ai.PROVIDERS)

    def keys(name):
        return re.findall(r'(\w+): "', re.search(r"const " + name + r" = \{(.*?)\};", app, re.DOTALL).group(1))

    assert keys("AIM_C") == ids and keys("AIM_M") == ids                                  # every AI of the server has its colour and its two letters
    main = re.search(r"const AI_MAIN = \[(.*?)\];", app).group(1)
    assert re.findall(r'"(\w+)"', main) == ids[:5] and keys("AIM_N") == ids[:5] and all(f'{k}: "{ai.PROVIDERS[k]["name"]}"' in app for k in ids[:5])
    assert 'const aiShown = aiCat.filter(c => c.has_key || AI_MAIN.includes(c.id) || aiOpen.has(c.id))' in app and '<div class="aim-list">${aiShown.map(aiRow).join("")}</div>' in app and 'onsubmit="return aiSave(event, \'${c.id}\')"' in app
    assert '<div class="aim-f">${fIc(SI.key,' in app and "c.custom ? fIc(SI.link," in app and 'data-pid="${c.id}"' in app and 'onclick="aiUse(\'${c.id}\')"' in app and 'onclick="aiTest(\'${c.id}\')"' in app and "onclick=\"aiForget('${c.id}', '${esc(c.name)}')\"" in app
    for fn in ("aiSave", "aiTest", "aiUse", "aiForget", "aiAddMenu"):
        assert f"window.{fn} = " in app, fn

    assert 'api("/api/settings/ai/use", {method: "POST", body: JSON.stringify({provider: pid})})' in app and 'aiPath(pid) + "/test"' in app and "const aiPath = pid => `/api/settings/ai/providers/${encodeURIComponent(pid)}`;" in app
    assert 'AI_MAIN.map(k => `<option value="${k}">${esc(AIM_N[k])}</option>`)' in app and "/api/settings/ai/providers/${encodeURIComponent(f.provider ? f.provider.value : \"anthropic\")}" in app      # the first-run card asks which AI the key is for
    assert "html body #page .aim { display: grid;" in css and "html body #page .aim-i" in css and "html body #page .abf {" in css and "html body #page .ab-b.flush {" in css and "color-mix(in srgb, var(--c) 16%, transparent)" in css


def test_the_boxes_of_api_and_key_are_cards_with_labelled_fields():
    """The owner, 2026-10-16: "put each part in a box so it is more orderly": a head (tile, name with its state, the buttons on the right) and the fields under it, each with its name above."""
    app, css = _web("app.js"), _web("lab.css")
    assert "const abF = (label, inner, cls = \"\") =>" in app and '<form id="${f.id}" class="ab ab-f" onsubmit="return ${f.submit}"><div class="ab-h">${head}</div>${body}</form>' in app
    tg = app.split("const tgBox = abBox(", 1)[1].split("const probeBox", 1)[0]
    assert 'abF("Bot token"' in tg and 'abF("Chat id"' in tg and "pgSw(!!tg.enabled" in tg and "ab-btns" in tg and 'type="submit"' in tg                                   # Save is in the head of its card, the fields are in its body
    probe = app.split("const probeBox = abBox(", 1)[1].split("const instBox", 1)[0]
    assert 'form="probe-form"' in probe and 'abF("Standby probes"' in probe and 'class="ab-grid" onsubmit="return saveProbe(event)"' in probe
    assert "pgSw(" in tg and "<label class=\"ab-sw\">" not in tg and "wrap.hidden" not in app.split("async function apiLoad() {", 1)[1].split("window.tokCreate", 1)[0]
    assert "html body #page .ab-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));" in css and "html body #page .ab-h { display: flex;" in css


def test_the_host_window_is_two_cards_of_rows_with_pills_and_one_size_of_switch():
    """The owner, 2026-10-16: "make this window better and more professional" and "fix the size of my switches so they do not overlap": the ticks (the users' states, ALPN) were drawn as 40 px switches in a label squeezed to 18 px by an old rule."""
    hosts, css, app_css = _web("pg_hosts.js"), _web("pg_admin.css"), _web("app.css")
    assert '<div class="pgh-grid">${where}${sec}</div>' in hosts and 'const row = (name, ctl, hint = "", tag = "label", cls = "") =>' in hosts and '<label class="pgh-chip"><input type="checkbox" name="${name}"' in hosts and 'class="chk"' not in hosts
    for name in ("st", "alpn"):
        assert f'chips("{name}"' in hosts                                                          # pgHostBody reads name="st" and name="alpn" as before
    for name in ("enabled", "use_sni_as_host", "random_user_agent"):
        assert f"""'name="{name}"'""" in hosts
    assert "html body #modal .pgh .pgh-chip input:checked + span {" in css and "html body #modal .pgh .srv-sw:has(input:checked) i {" in css and "width: 40px; min-width: 40px; max-width: 40px; height: 24px;" in css and "html body #modal .pgh .frm-act { position: sticky;" in css
    assert "#modal .kv2 i.chk {" in app_css and "#modal .kv2 .chk {" not in app_css and ".kv2 i.chk { width: 15px;" in app_css                  # the little round badge only: a tick with its words in a row of a window keeps its own size

def test_the_theme_previews_are_small_pictures_of_the_panel():
    """The owner, 2026-10-16: "make the theme previews prettier": each theme is a small panel (sidebar, top bar, two cards, a button) in its own colours, with its name and a line under it; the chosen one has a ring and a tick."""
    app, css = _web("app.js"), _web("lab.css")
    assert '<div class="thm">${THEMES.map(thCard).join("")}</div>' in app and '<s class="sb"></s><s class="tp"></s><s class="c1"></s><s class="c2"></s><s class="bt"></s>' in app and 'class="tsw' not in app and 'class="theme-grid"' not in app
    assert "const thLight = hex =>" in app and 'role="button" tabindex="0"' in app and 'aria-pressed="${LOOK.theme === k}"' in app and "onclick=\"lookSet('theme', '${k}'); refresh()\"" in app
    assert "html body #page .thm { display: grid;" in css and "html body #page .thc.on::after {" in css and "html body #page .thc-pv.lt {" in css and "html body #page .thc-pv .sb::after {" in css

def test_the_tunnel_menu_has_a_speed_test_that_opens_inside_the_window():
    """The owner, 2026-10-16: "a speed test for my tunnels - what speed does a user in Iran get - what the customer holds - the closest possible": in the menu of the tunnel, in the window of the tunnel (Back returns)."""
    lab, app, css = _web("lab.js"), _web("app.js"), _web("lab.css")
    menu = lab.split("function tunMenuItems(id) {", 1)[1].split("function labNodeParts", 1)[0]
    assert menu.index('labIt("Health check"') < menu.index('labIt("Speed test…", "monitor"') < menu.index('labIt("Restart…"') and "tunSpeed(t.id, t.name)" in menu
    assert "window.tunSpeed = async (id, name) =>" in app and "window.tunSpeedRun = async id =>" in app and "function tunSpeedResult(r, cap) {" in app and 'name="sp-mode"' in app
    assert 'api(`/api/tunnels/${id}/speed`, {method: "POST", body: JSON.stringify({mode})})' in app and "followStory(r.action_id, box, 400)" in app and 'onclick="tunBack(${id})"' in app.split("window.tunSpeed = ", 1)[1]
    assert "html body #modal .spd-opts {" in css and "html body #modal .spd-b.cap u" in css and "html body #modal .spd-res.fair .spd-g" in css


def test_the_forward_chisel_is_offered_in_the_build_form_and_known_by_the_tests():
    """The forward Chisel (the Iran server dials) of 1.59.2: a row in the guide, a hint in the drop-down, the kind keys of the pair test, and the label of the column."""
    app = _web("app.js")
    assert '"chisel:fws": "Iran dials: fast downloads", "chisel:fwss": "Iran dials: fast downloads, TLS"' in app and 'Customers\' downloads are slow", "Chisel · Forward WebSocket", "chisel", "fws"' in app
    assert '{wss: "chisel:tls", fws: "chisel:fwd", fwss: "chisel:fwdtls"}[trn] || "chisel"' in app
    assert '"chisel:fwd": "Chisel · forward WebSocket", "chisel:fwdtls": "Chisel · forward TLS WebSocket"' in app
    assert '["direct", "forward"].includes((t.record || {}).direction)' in app


def test_the_paqet_form_takes_the_window_and_the_buffer_and_the_mtu_stops_where_it_carries():
    """1.59.2: the Paqet window and buffer are fields of the form (one window for both directions, one buffer for a stream, the SMUX buffer twice that); the MTU stops at 1400."""
    app = _web("app.js")
    assert 'num("mtu", "KCP MTU", 1350, 500, 1400, "(1450 and more carry nothing)")' in app
    assert 'num("window", "Window (packets)", 4096, 128, 65535' in app and 'num("buffer_mb", "Buffer (MB)", 8, 1, 128' in app
    assert 'if (w) body.opts.rcvwnd = body.opts.sndwnd = w;' in app and 'body.opts.streambuf = mb * 1048576; body.opts.smuxbuf = mb * 2097152;' in app


def test_the_pages_read_the_loss_of_a_tunnel_through_one_function():
    """1.60.0: every ping lost while the tunnel carries data is "no ping", never "100 % of the data is lost" (the Doctor's page said so for a tunnel that worked)."""
    app = _web("app.js")
    assert "const tunLoss = t => {" in app and "if (l != null && l >= 100 && t.state === \"up\") return {v: null, blocked: true};" in app
    assert 'lossy = tl.filter(t => t.state === "up" && (tunLoss(t).v || 0) >= SHOW_LOSS)' in app and "loss = tunLoss(t).v || 0, why =" in app and "(tunLoss(t).v || 0) >= LOSS_SEVERE" in app
    assert 'tunLoss(t).blocked ? "no ping" : "—"' in app and "const lossTip = t =>" in app and "the path between its two servers (ping)" in app
    assert "const tunSev = t => { const loss = tunLoss(t).v || 0;" in app
    assert ".tlink .tl-top .loss.blk {" in _web("lab.css")


def test_the_tunnel_table_has_the_lamp_before_the_name_and_the_bandwidth_in_place_of_the_status():
    """The owner, 2026-10-16: "the status lamp goes behind the name of every tunnel, and instead of the status show the bandwidth of the tunnel, very simply; when it drops, the Doctor looks at it by itself and we understand"."""
    app, css, lab = _web("app.js"), _web("lab.css"), _web("lab.js")
    row = app.split("const row = t => {", 1)[1].split("const creating = false;", 1)[0]
    assert '<td class="name"><i class="tun-lamp ${esc(t.state)}"' in row and '<td class="bwc">${bwCell(t)}</td>' in row and "stText(t)}</span>" not in row.split('<td class="bwc">', 1)[0].split('<td class="name">', 1)[1].split("</td>", 1)[1]
    assert "<thead><tr><th>Tunnel</th><th>Type</th><th>Status</th>" not in app and "<thead><tr><th>Tunnel</th><th>Type</th><th title=\"How much this tunnel can carry" in app and ">Bandwidth</th>" in app
    cell = app.split("const bwCell = t => {", 1)[1].split("const row = t => {", 1)[0]
    assert "workMap.has(t.name)" in cell and "cutTag(t)" in cell and '<i class="bwbar"><u style="width:' in cell and "▼ ${bwFmt(" in cell and "b.state === \"drop\" ? \"drop\" : b.state === \"low\" ? \"low\" : \"ok\"" in cell
    assert "const bwFmt = v =>" in app and "const bwTip = t =>" in app and "const bwWhy = t =>" in app and "(t.bw || {}).state === \"drop\"" in app
    assert 'slow = tl.filter(t => t.state === "up" && (t.bw || {}).state === "drop" && !lossy.includes(t))' in app and 'kind === "slow" ? bwWhy(t)' in app and "${slow.map(t => row(t, \"slow\")).join(\"\")}" in app
    assert "kinds=fix,autopilot,balance,underuse,move,speed" in app and 'a.action === "speed" && /Where the problem is/.test' in app and "window.bwMeasure = async id =>" in app and 'api(`/api/tunnels/${id}/bandwidth`, {method: "POST"})' in app
    assert 'kv("Bandwidth", ' in app and 'onclick="bwMeasure(${t.id})"' in app
    assert "html body #page .tun-tbl td.name .tun-lamp {" in css and "--tun-start: calc(var(--tun-pad) + var(--tun-dot) + var(--tun-gap))" in css and ".tun-tbl .bw.drop b" in css and "html body #page .tun-tbl td.bwc" in css
    assert "Speed test…" in lab                                                                   # (the menu of a tunnel is as it was)

def test_the_doctor_says_what_the_users_get_and_asks_for_the_links_it_lacks():
    """1.61.0 (the owner, 2026-10-16: "I want the Doctor to truly say whether the user gets a quality internet"): the grade of what the configs through a tunnel do comes into the tunnel window, the
    tip of the bandwidth cell and the Doctor page (a row "users"); the configs that ride no known tunnel are counted with a button that ties them to one (the Configs page is switched off)."""
    app = _web("app.js")
    assert "const uxTip = t =>" in app and "const uxWhy = t =>" in app and "+ uxTip(t);" in app and 'kind === "users" ? uxWhy(t)' in app
    assert 'users = tl.filter(t => t.state === "up" && ["poor", "bad"].includes((t.ux || {}).grade)' in app and '${users.map(t => row(t, "users")).join("")}' in app and 'kv("Users", ' in app
    assert "const usersNote = u => u && u.total > u.linked ?" in app and "${usersNote(dc && dc.users)}" in app and 'onclick="apLinkConfigs()"' in app
    assert "window.apLinkConfigs = async () =>" in app and "window.apLinkSave = async () =>" in app and 'api("/api/configs/" + s.dataset.cid, {method: "PUT", body: JSON.stringify({tunnel: s.value})})' in app
    assert 'api("/api/doctor"), api("/api/configs"), api("/api/tunnels")' in app


def test_the_cut_of_a_tunnel_is_a_tiny_tag_that_says_which_end_and_a_tunnel_without_a_number_says_why():
    """The owner, 2026-10-16: not a red dot and "GRE link cut" but a small red tag that says which end is cut ("GRE foreign"); "some tunnels show no bandwidth", "it shows connected but has no bandwidth"."""
    app, css = _web("app.js"), _web("lab.css")
    cell = app.split("const bwCell = t => {", 1)[1].split("const row = t => {", 1)[0]
    assert "const CUT_TAGS = [" in app and "const cutTag = t =>" in app and "stShort" not in app and "WHY_SHORT" not in app
    assert '<span class="cut-tag ${t.state === "down" ? "" : "unk"}"' in cell and 'class="st ${t.state === "down" ? "down" : "unknown"} why"' not in cell
    assert "bwPending(t)" in cell and "measuring…" in cell and "stall = b.down != null && b.down < 1" in cell and "<small>no data</small>" in cell and "stall || b.state === \"drop\" ? \"drop\"" in cell
    assert "const bwPending = t => !(t.bw || {}).error &&" in app and 'NO DATA moved in the test' in app and "No data moves through this tunnel under load" in app and 'b.error ? "Could not be measured: "' in app
    assert ".tun-tbl .cut-tag {" in css and ".tun-tbl .cut-tag.unk" in css and ".tun-tbl .bw.pend" in css and 'html[data-mode="light"] body #page .tun-tbl .cut-tag {' in css
    assert 'udp: "the GRE link of this tunnel, counted with UDP packets' in app                      # a link that drops ping shows the loss the bandwidth test counted with UDP, not "no ping"


def test_the_tag_of_a_cut_tunnel_names_the_end_that_is_cut(tmp_path):
    import json
    import shutil
    import subprocess

    node = shutil.which("node")

    if node is None:
        import pytest

        pytest.skip("node is not installed here")

    app = _web("app.js")
    code = app[app.index("const CUT_TAGS = ["): app.index("\n", app.index("const cutTag = t =>"))]
    driver = tmp_path / "tag.js"
    driver.write_text("const stText = t => t.state === 'up' ? 'ACTIVE' : t.state === 'down' ? 'DOWN' : 'UNKNOWN';\n" + code + "\nconst cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
                      "process.stdout.write(JSON.stringify(cases.map(c => cutTag(c))));\n", encoding="utf-8")
    cases = [("no answer from the foreign end over the GRE link", "GRE foreign"), ("the GRE interface pl-ir3 is DOWN on the Iran server", "GRE Iran"),
             ("Iran: nikatel-gre-pl-ir3.service is inactive", "GRE Iran"), ("foreign: nikatel-gre-pl-ir3.service is failed", "GRE foreign"),
             ("Iran: backhaul-iran-tcpmux-9906.service is inactive", "Iran service"), ("foreign: backhaul-kharej-tcpmux-9906.service is dead", "foreign service"),
             ("both servers do not answer (timeout)", "both servers off"), ("the Iran server does not answer (timeout after 35s)", "Iran server off"), ("the foreign server does not answer (x)", "foreign server off"),
             ("port 8072 answers on the server but not from ir9: blocked in front of the server", "port blocked"), ("port 8075 is not listening on the Iran server", "port closed"),
             ("port 8075 accepts connections but no data comes back through the tunnel", "no data"), ("no data moves through the tunnel under load: the last tests carried 0 Mb/s", "no data"),
             ("the link flaps: 5 of the last 10 checks failed, users see cuts", "flapping"), ("something odd happened here that is long", "something odd happ…"), ("", "down")]
    r = subprocess.run([node, str(driver)], input=json.dumps([{"last_error": e, "state": "down"} for e, _ in cases]), capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert r.returncode == 0, r.stderr[-600:]
    assert json.loads(r.stdout) == [want for _, want in cases]


def test_the_doctor_page_has_a_knowledge_tab_for_what_it_knows_and_remembers():
    app, kn, index, main = _web("app.js"), _web("knowledge.js"), _web("index.html"), (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert 'tabBtn("know", "spark", "Knowledge"' in app and 'id="ap-t-know"' in app and 'id="kn-box"' in app and '["now", "done", "moves", "ana", "know"]' in app and 'if (k === "know") knLoad();' in app and 'if (apTab === "know") knLoad();' in app
    assert '<script src="/static/knowledge.js"></script>' in index and '"knowledge.js"' in main
    assert 'api("/api/doctor/knowledge")' in kn and '/api/doctor/knowledge/survey' in kn and '/api/doctor/knowledge/stop' in kn and 'window.knSet = async body =>' in kn and "What the Doctor remembers of cuts" in kn
    assert all(f'"{k}"' in app.split("const AP_LABEL = {", 1)[1].split("};", 1)[0] or f'{k}:' in app.split("const AP_LABEL = {", 1)[1].split("};", 1)[0] for k in ("bp:wss", "bp:ws", "bp:wsmux", "bp:kcp", "bp:quic", "bp:pck", "bp:xdi", "bpd:quic", "bpd:pck", "bpd:xdi", "tcpmux", "paqet", "chisel:fwd"))