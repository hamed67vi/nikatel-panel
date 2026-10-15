/* PasarGuard tabs "Groups" and "Templates": every group and user template of a PasarGuard panel, with create / edit / delete (roadmap #94, area "access").
   A GROUP decides which inbounds a user gets; a TEMPLATE is the recipe for new users. Registers itself in pgTabs (see pg_common.js).
   The server (services/pg_access.py) does the work: it checks the input, sends the panel the whole body, saves a backup before a change that can lose something and
   can put it back. This file only draws and calls /api/panels/{id}/pg/groups, /pg/templates and /pg/access. Every top-level name starts with pgAccess. */

/* ---- pure: text (no DOM) */
const pgAccessN = v => { const n = Number(v); return Number.isFinite(n) ? String(Number(n.toFixed(3))) : ""; };      // 1.5, not 1.500
const pgAccessResets = {no_reset: "never", day: "daily", week: "weekly", month: "monthly", year: "yearly"};
const pgAccessDataText = t => (t.data_limit_gb > 0 ? pgAccessN(t.data_limit_gb) + " GB" : "∞") + (t.data_limit_reset_strategy && t.data_limit_reset_strategy !== "no_reset" ? " · " + (pgAccessResets[t.data_limit_reset_strategy] || t.data_limit_reset_strategy) : "");
const pgAccessDaysText = t => (t.expire_days > 0 ? pgAccessN(t.expire_days) + " d" : "∞") + (t.status === "on_hold" ? " · on hold" : "");
const pgAccessInfo = list => Object.fromEntries((list || []).map(i => [i.tag, [i.protocol, i.network].filter(Boolean).join(" · ")]));      // tag -> "vless · ws"
const pgAccessUserName = (t, v) => (t.username_prefix || "") + (v || "…") + (t.username_suffix || "");                          // what the panel will really name the user
const pgAccessIds = list => (list || []).map(Number).filter(Number.isFinite);
const pgAccessCount = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;                                                             // "1 group", "3 groups"

/* ---- state */
const pgAccess = {id: 0, g: null, t: null, gq: "", tq: "", gsel: new Set(), tsel: new Set(), pick: new Set(), tpick: new Set(), mcount: null};
const pgAccessG = gid => (pgAccess.g ? pgAccess.g.groups : []).find(g => g.id === gid);
const pgAccessT = tid => (pgAccess.t ? pgAccess.t.templates : []).find(t => t.id === tid);
const pgAccessLive = (box, tab) => box.isConnected && pn.tab === tab;                    // the user may have left the tab while the answer was on its way

/* a call that changes something: the toast says what happened, the open tab is read again; a refusal shows the panel's reason.
   `undo(answer)` returns the function an Undo button runs (or null when there is nothing to go back to). */
async function pgAccessChange(fn, okText, undo, closeWindow) {
  let r;
  try { r = await fn(); } catch (e) { toast("✕ " + e.message, "bad"); pgAccessRedraw(); return null; }
  if (closeWindow) closeModal();                                // the form is done: close it BEFORE the message, or the message would only show inside the closing window
  toast("✓ " + okText, "ok", (undo && undo(r)) || undefined);
  try { await pgAccessReload(true); } catch (e) { toast("✕ " + e.message, "bad"); }
  return r;
}
window.pgAccessReload = (quiet = false) => {
  const S = pgAccess, box = $("#pn-body");
  if (!box || !S.id) return Promise.resolve();
  if (pn.tab === "groups") return pgAccessGroups(S.id, box, quiet === true);
  if (pn.tab === "templates") return pgAccessTemplates(S.id, box, quiet === true);
  return Promise.resolve();
};
function pgAccessRedraw() { if (pn.tab === "groups") { pgAccessGroupsList(); } else if (pn.tab === "templates") { pgAccessTemplatesList(); } }
/* the way back of a change: the backup the server wrote first, restored for that one group / template only */
const pgAccessUndoRestore = (backup, kind, id) => async () => {
  try {
    const r = await pgApi(pgAccess.id, `/access/backups/${encodeURIComponent(backup)}/restore`, {method: "POST", body: {groups: kind === "group" ? [id] : [], templates: kind === "template" ? [id] : [], dry_run: false}});
    const bad = (r.items || []).find(i => i.action === "failed");          // the server answers 200 even when one item could not be put back: say so
    if (bad) toast("✕ Could not undo: " + bad.reason, "bad");
    else if (!r.created && !r.updated) toast("Nothing to undo: the panel already has it as it was", "ok");
    else toast("✓ Undone", "ok");
  } catch (e) { toast("✕ " + e.message, "bad"); }
  pgAccessReload(true);
};
const pgAccessUndoCreate = (path) => async () => {
  try { await pgApi(pgAccess.id, path, {method: "DELETE"}); toast("✓ Undone", "ok"); } catch (e) { toast("✕ " + e.message, "bad"); }
  pgAccessReload(true);
};

/* ---- shared drawing */
function pgAccessPills(tags, info, have) {
  if (!tags.length) return '<span class="mut">none</span>';
  const gone = t => have && have.size && !have.has(t);
  const pill = t => `<span class="td-type ${gone(t) ? "pg-access-gone" : ""}" title="${esc(gone(t) ? "not an inbound of the core any more" : (info && info[t]) || "")}">${esc(t)}</span>`;
  return `<div class="pg-access-tags" data-n="${tags.length}" title="${esc(tags.join(", "))}">${tags.slice(0, 60).map(pill).join("")}<small class="mut pg-access-more"></small></div>`;
}
/* show as many pills as the cell has room for: the rest is hidden and counted in "+N" (the full list is in the tooltip) */
function pgAccessFit(root) {
  root.querySelectorAll(".pg-access-tags").forEach(box => {
    const pills = [...box.querySelectorAll(".td-type")], more = box.querySelector(".pg-access-more"), total = Number(box.dataset.n) || pills.length;
    pills.forEach(p => { p.style.display = ""; });
    more.textContent = total > pills.length ? "+" + (total - pills.length) : "";
    let shown = pills.length;
    while (shown > 1 && box.scrollWidth > box.clientWidth + 1) { shown--; pills[shown].style.display = "none"; more.textContent = "+" + (total - shown); }
  });
}
let pgAccessResizeT = 0;
window.addEventListener("resize", () => { clearTimeout(pgAccessResizeT); pgAccessResizeT = setTimeout(() => { const el = $("#pg-access"); if (el) pgAccessFit(el); }, 150); });
document.addEventListener("nkfit", e => { const el = e.target.closest && e.target.closest("#pg-access"); if (el) pgAccessFit(el); });      // the fluid layer gave the columns new widths: the pills are counted again
/* keep the scroll position of the list box when its rows are drawn again */
function pgAccessFill(el, html) {
  const sc = el.querySelector(".lbox"), top = sc ? sc.scrollTop : 0;
  el.innerHTML = html;
  const nb = el.querySelector(".lbox"); if (nb) nb.scrollTop = top;
  pgAccessFit(el);
}
function pgAccessCopy(btn) {
  const v = btn.dataset.v || "";
  const done = () => toast("✓ Copied", "ok");
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(v).then(done, () => toast("✕ The browser did not allow copying: select the text instead", "bad"));
  else toast("✕ The browser did not allow copying: select the text instead", "bad");
}
window.pgAccessCopy = pgAccessCopy;

/* ================================================================== GROUPS */
async function pgAccessGroups(id, box, quiet) {
  const S = pgAccess;
  if (S.id !== id) { S.id = id; S.g = S.t = null; S.gsel.clear(); S.tsel.clear(); S.gq = S.tq = ""; }
  if (!quiet) pgLoading(box, "Reading the groups…");
  const d = await pgApi(id, "/groups");
  if (!pgAccessLive(box, "groups")) return;
  S.g = d; S.gsel = new Set([...S.gsel].filter(i => d.groups.some(g => g.id === i)));
  pgAccessGroupsDraw(box);
}
function pgAccessGroupsDraw(box) {
  const S = pgAccess;
  pgPaint(box, `<div id="pg-access" class="pg-access">${pgBar(
    `<input id="pg-access-gq" type="search" placeholder="Search name or inbound…" value="${esc(S.gq)}" oninput="pgAccessGQ(this.value)" autocomplete="off" spellcheck="false"><span class="mut pn-cnt" id="pg-access-gcnt"></span><button class="sm" onclick="pgAccessReload()" title="Read the groups from the panel again">↻</button>`,
    `<span id="pg-access-gbulk" class="pg-access-bulk"></span><button class="sm" onclick="pgAccessBackups()" title="What was saved before each change, and the way back">Backups…</button>${window.pgKindBtn ? pgKindBtn() : ""}<button class="sm pri" onclick="pgAccessGroupForm(0)">＋ New group</button>`)}
    ${S.g.warn ? `<div class="mut pg-access-warn">${esc(S.g.warn)}</div>` : ""}<div id="pg-access-glist"></div></div>`);
  pgAccessGroupsList();
}
function pgAccessGroupsList() {
  const S = pgAccess, el = $("#pg-access-glist"); if (!el || !S.g) return;
  const info = pgAccessInfo(S.g.inbounds), have = new Set(S.g.inbounds.map(i => i.tag)), q = S.gq.trim();
  const rows = S.g.groups.filter(g => nkHit([g.name, "#" + g.id, g.is_disabled ? "off disabled" : "on", ...g.inbound_tags].join(" "), q));
  const all = rows.length > 0 && rows.every(g => S.gsel.has(g.id));
  const row = g => `<tr class="${g.is_disabled ? "" : "up"}"><td>${pgSw(S.gsel.has(g.id), `pgAccessGSel(${g.id}, this.checked)`)}</td>
    <td class="name" title="${esc(g.name)}">${esc(g.name)} <small class="mut">#${g.id}</small></td><td>${pgAccessPills(g.inbound_tags, info, have)}</td>
    <td class="num">${g.users == null ? "—" : g.users}</td><td class="num" title="${esc((g.templates || []).join(", "))}">${(g.templates || []).length}</td>
    <td>${pgSw(!g.is_disabled, `pgAccessGroupOn(${g.id}, this.checked)`)}</td>
    <td class="pg-access-act"><button class="sm" onclick="pgAccessMembers(${g.id})" title="Add this group to users, or take it away">Users…</button><button class="sm" onclick="pgAccessGroupForm(${g.id})">Edit</button><button class="sm danger" onclick="pgAccessGroupDel(${g.id})" title="Delete this group">✕</button></td></tr>`;
  pgAccessFill(el, rows.length ? `<div class="lbox full pg-access-box"><table class="tbl lift"><colgroup><col style="width:72px"><col style="width:18%"><col><col style="width:64px"><col style="width:88px"><col style="width:68px"><col style="width:204px"></colgroup>
    <thead><tr><th>${pgSw(all, "pgAccessGAll(this.checked)", 'title="Tick every group shown"')}</th><th>Group</th><th>Inbounds</th><th>Users</th><th>Templates</th><th>On</th><th></th></tr></thead><tbody>${rows.map(row).join("")}</tbody></table></div>`
    : pgEmpty(S.g.groups.length ? "No group matches the search." : "No group yet. A group lists the inbounds its users get: make one with ＋ New group."));
  const cnt = $("#pg-access-gcnt"); if (cnt) cnt.textContent = `${rows.length}${rows.length === S.g.groups.length ? "" : " of " + S.g.groups.length} group${S.g.groups.length === 1 ? "" : "s"} · ${S.g.inbounds.length} inbound${S.g.inbounds.length === 1 ? "" : "s"}`;
  pgAccessGBulkBar();
}
function pgAccessGBulkBar() {
  const n = pgAccess.gsel.size, el = $("#pg-access-gbulk"); if (!el) return;
  el.innerHTML = n ? `<span class="mut">${n} selected</span><button class="sm" onclick="pgAccessGBulk('enable')">Enable</button><button class="sm" onclick="pgAccessGBulk('disable')">Disable</button><button class="sm danger" onclick="pgAccessGBulk('delete')">Delete</button><button class="sm" onclick="pgAccessGAll(false)" title="Untick everything">Clear</button>` : "";
}
window.pgAccessGQ = v => { pgAccess.gq = v; pgAccessGroupsList(); };
window.pgAccessGSel = (id, on) => { const S = pgAccess; if (on) S.gsel.add(id); else S.gsel.delete(id); pgAccessGroupsList(); };
window.pgAccessGAll = on => {
  const S = pgAccess, q = S.gq.trim();
  S.g.groups.filter(g => nkHit([g.name, "#" + g.id, g.is_disabled ? "off disabled" : "on", ...g.inbound_tags].join(" "), q)).forEach(g => { if (on) S.gsel.add(g.id); else S.gsel.delete(g.id); });
  pgAccessGroupsList();
};

/* the switch in a row: a group that is OFF gives its users nothing, so switching one off that users hold asks first */
window.pgAccessGroupOn = async (gid, on) => {
  const S = pgAccess, g = pgAccessG(gid); if (!g) return;
  if (!on && g.users && !await gconfirm(`Switch off group ${g.name}? Its ${g.users} users stop getting its inbounds until you switch it on again.`, {danger: true})) { pgAccessGroupsList(); return; }
  await pgAccessChange(() => pgApi(S.id, `/groups/${gid}`, {method: "PUT", body: {is_disabled: !on}}), `Group ${g.name} is ${on ? "on" : "off"}`,
    r => (r.backup ? pgAccessUndoRestore(r.backup, "group", gid) : null));
};

/* ---- the form of one group: a name, a switch, and the inbounds to pick (a tag the group holds that the core no longer has stays in the list so it can be taken off) */
function pgAccessPickRows(tags, info, have) {
  const S = pgAccess;
  return tags.map(t => `<div class="pg-access-prow" data-q="${esc(t + " " + (info[t] || ""))}">${pgSw(S.pick.has(t), "pgAccessPickTick(this)", `data-tag="${esc(t)}"`)}<span class="name ell" title="${esc(t)}">${esc(t)}</span><small class="mut ell">${esc(info[t] || "")}</small>${have.has(t) ? "" : '<span class="pill err">not in the core</span>'}</div>`).join("");
}
window.pgAccessGroupForm = gid => {
  const S = pgAccess, g = gid ? pgAccessG(gid) : null; if (gid && !g) return;
  const info = pgAccessInfo(S.g.inbounds), have = new Set(S.g.inbounds.map(i => i.tag));
  const tags = [...S.g.inbounds.map(i => i.tag), ...(g ? g.inbound_tags.filter(t => !have.has(t)) : [])];
  S.pick = new Set(g ? g.inbound_tags : []);
  showModal(`<b>${g ? "Edit group " + esc(g.name) : "New group"}</b>`, `<form class="frm pg-access-frm" onsubmit="return pgAccessGroupSave(event, ${gid})" autocomplete="off">
    <label style="grid-column: span 2">Name <small>(3 to 64 characters)</small><input name="nm" required minlength="3" maxlength="64" value="${esc(g ? g.name : "")}"></label>
    <div class="pg-access-line"><span>Enabled</span>${pgSw(!g || !g.is_disabled, "", 'name="on"')}</div>
    <div class="pg-access-pick"><div class="pg-access-pickbar"><b>Inbounds</b><span class="mut" id="pg-access-pickn"></span><span class="sp"></span><input type="search" placeholder="Filter…" oninput="pgAccessPickFilter(this.value)" onkeydown="if (event.key === 'Enter') event.preventDefault()" autocomplete="off"><button type="button" class="sm" onclick="pgAccessPickAll(true)">All shown</button><button type="button" class="sm" onclick="pgAccessPickAll(false)">None</button></div>
      <div class="pg-access-picklist" id="pg-access-picklist">${tags.length ? pgAccessPickRows(tags, info, have) : pgEmpty("The panel lists no inbounds: add one in its core config first.")}</div></div>
    <div class="frm-act"><button class="pri" type="submit">${g ? "Save group" : "Create group"}</button><button type="button" onclick="closeModal()">Cancel</button><span class="sp"></span><small class="mut">${g && g.users ? `${g.users} users hold this group: a change reaches them at once.` : ""}</small></div></form>`, "wide");
  pgAccessPickCount();
  setTimeout(() => { const i = document.querySelector("#modal .pg-access-frm input[name=nm]"); if (i) i.focus(); }, 40);
};
function pgAccessPickCount() {
  const el = $("#pg-access-pickn"), n = pgAccess.pick.size; if (el) el.textContent = `${n} picked`;
}
window.pgAccessPickTick = el => { const S = pgAccess, t = el.dataset.tag; if (el.checked) S.pick.add(t); else S.pick.delete(t); pgAccessPickCount(); };
window.pgAccessPickFilter = q => document.querySelectorAll("#pg-access-picklist .pg-access-prow").forEach(r => r.classList.toggle("pg-access-hide", !nkHit(r.dataset.q, q)));
window.pgAccessPickAll = on => document.querySelectorAll("#pg-access-picklist .pg-access-prow:not(.pg-access-hide) input[data-tag]").forEach(i => { i.checked = on; pgAccessPickTick(i); });
window.pgAccessGroupSave = async (e, gid) => {
  e.preventDefault();
  const S = pgAccess, f = e.target.elements, name = f.nm.value.trim(), on = f.on.checked, tags = [...S.pick];
  if (!tags.length) { toast("✕ Pick at least one inbound", "bad"); return false; }
  if (!gid) {
    await pgAccessChange(() => pgApi(S.id, "/groups", {method: "POST", body: {name, inbound_tags: tags, is_disabled: !on}}), `Group ${name} created`, r => pgAccessUndoCreate(`/groups/${r.group.id}`), true);
    return false;
  }
  const g = pgAccessG(gid), body = {}, core = new Set(S.g.inbounds.map(i => i.tag));
  const removed = g.inbound_tags.filter(t => !S.pick.has(t)), live = removed.filter(t => core.has(t));          // a tag the core no longer has gives nobody anything: no warning for it
  if (name !== g.name) body.name = name;
  if (on === g.is_disabled) body.is_disabled = !on;
  if (removed.length || tags.some(t => !g.inbound_tags.includes(t))) body.inbound_tags = tags;
  if (!Object.keys(body).length) { closeModal(); return false; }
  if (live.length && g.users && !await gconfirm(`Take ${live.length} inbound${live.length === 1 ? "" : "s"} (${live.slice(0, 3).join(", ")}${live.length > 3 ? "…" : ""}) off group ${g.name}? Its ${g.users} users lose ${live.length === 1 ? "it" : "them"} from their subscriptions.`, {danger: true})) return false;
  await pgAccessChange(() => pgApi(S.id, `/groups/${gid}`, {method: "PUT", body}), `Group ${name} saved`, r => (r.backup ? pgAccessUndoRestore(r.backup, "group", gid) : null), true);
  return false;
};

/* ---- delete: a group nobody uses is a plain yes; one that users or templates hold needs its name typed (the panel would delete it at once and they would lose it) */
window.pgAccessGroupDel = async gid => {
  const S = pgAccess, g = pgAccessG(gid); if (!g) return;
  const users = g.users || 0, tn = (g.templates || []).length;
  let force = false;
  if (users || tn) {
    const typed = await gprompt(`Delete group ${g.name}? ${pgAccessCount(users, "user")} ${users === 1 ? "holds" : "hold"} it${tn ? ` and ${pgAccessCount(tn, "template")} ${tn === 1 ? "uses" : "use"} it` : ""}: they lose it, and a user left with no group gets no inbound at all. The groups and templates are saved to a file first, but the users it held cannot be put back automatically. Type the group name to go on.`, "", {danger: true});
    if (typed === null) return;
    if (typed.trim() !== g.name) { toast("✕ The name does not match: nothing was deleted", "bad"); return; }
    force = true;
  } else if (!await gconfirm(`Delete group ${g.name}? Nobody holds it. It is saved to a file first and can be restored.`, {danger: true})) return;
  await pgAccessChange(() => pgApi(S.id, `/groups/${gid}?force=${force}`, {method: "DELETE"}), `Group ${g.name} deleted`, r => (users || tn ? null : pgAccessUndoRestore(r.backup, "group", gid)));
};

/* ---- the ticked groups: enable, disable, delete */
window.pgAccessGBulk = async action => {
  const S = pgAccess, rows = [...S.gsel].map(pgAccessG).filter(Boolean); if (!rows.length) return;
  const users = rows.reduce((n, g) => n + (g.users || 0), 0), tn = new Set(rows.flatMap(g => g.templates || [])).size, names = rows.map(g => g.name).join(", ");
  let force = false;
  if (action === "delete" && (users || tn)) {
    const typed = await gprompt(`Delete ${pgAccessCount(rows.length, "group")} (${names})? ${pgAccessCount(users, "user")} hold ${rows.length === 1 ? "it" : "them"}${tn ? ` and ${pgAccessCount(tn, "template")} use ${rows.length === 1 ? "it" : "them"}` : ""}: they lose ${rows.length === 1 ? "it" : "them"}. The groups and templates are saved to a file first. Type DELETE to go on.`, "", {danger: true});
    if (typed === null) return;
    if (typed.trim() !== "DELETE") { toast("✕ Not confirmed: nothing was deleted", "bad"); return; }
    force = true;
  } else if (action === "delete" && !await gconfirm(`Delete ${pgAccessCount(rows.length, "group")} (${names})? Nobody holds ${rows.length === 1 ? "it" : "them"}. ${rows.length === 1 ? "It is" : "They are"} saved to a file first.`, {danger: true})) return;
  else if (action === "disable" && users && !await gconfirm(`Switch off ${pgAccessCount(rows.length, "group")} (${names})? ${pgAccessCount(users, "user")} stop getting ${rows.length === 1 ? "its inbounds" : "their inbounds"} until you switch ${rows.length === 1 ? "it" : "them"} on again.`, {danger: true})) return;
  await pgAccessChange(() => pgApi(S.id, "/groups/bulk", {method: "POST", body: {action, ids: rows.map(g => g.id), force}}).then(r => { S.gsel.clear(); return r; }),      // the ticks go when the call worked, BEFORE the list is read again
    `${action === "enable" ? "Switched on" : action === "disable" ? "Switched off" : "Deleted"} ${pgAccessCount(rows.length, "group")}`);
};

/* ---- who holds a group: add it to users or take it away (the panel's bulk call). It counts first; nothing changes until Apply. */
window.pgAccessMembers = gid => {
  const S = pgAccess, g = pgAccessG(gid); if (!g) return;
  const others = S.g.groups.filter(x => x.id !== gid);
  S.mcount = null;
  showModal(`<b>Users of ${esc(g.name)}</b>`, `<div class="pg-access-mem"><div class="mut pg-access-memtxt">${g.users == null ? "Some" : g.users} users hold this group now. Add it to more users, or take it away. <b>Count</b> only asks the panel who would be reached; nothing changes until you press <b>Apply</b>.</div>
    <div class="frm one pg-access-frm">
      <label>Do<select id="pg-access-ma" onchange="pgAccessMemReset()"><option value="add">Add ${esc(g.name)} to</option><option value="remove">Take ${esc(g.name)} away from</option></select></label>
      <label>Users<select id="pg-access-mw" onchange="pgAccessMemWho()"><option value="has" ${others.length ? "" : "disabled"}>who hold another group</option><option value="none" ${others.length ? "" : "selected"}>who hold no group</option><option value="all">all users</option></select></label>
      <label id="pg-access-mgl" ${others.length ? "" : 'style="display:none"'}>That group<select id="pg-access-mg" onchange="pgAccessMemReset()">${others.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join("")}</select></label>
      <div class="frm-act"><button type="button" onclick="pgAccessMemRun(${gid}, true)">Count</button><button type="button" class="pri" id="pg-access-mapply" disabled onclick="pgAccessMemRun(${gid}, false)">Apply</button><button type="button" onclick="closeModal()">Close</button></div>
      <div class="pg-access-mres mut" id="pg-access-mres"></div></div></div>`, "narrow");
};
window.pgAccessMemReset = () => { pgAccess.mcount = null; const a = $("#pg-access-mapply"), r = $("#pg-access-mres"); if (a) a.disabled = true; if (r) r.textContent = ""; };
window.pgAccessMemWho = () => { const w = $("#pg-access-mw"), l = $("#pg-access-mgl"); if (l) l.style.display = w && w.value === "has" ? "" : "none"; pgAccessMemReset(); };
window.pgAccessMemRun = async (gid, dry) => {
  const S = pgAccess, g = pgAccessG(gid), act = $("#pg-access-ma").value, who = $("#pg-access-mw").value, other = Number(($("#pg-access-mg") || {}).value), res = $("#pg-access-mres");
  const body = {action: act, group_ids: [gid], dry_run: dry};
  if (who === "has") body.has_group_ids = [other]; else if (who === "none") body.has_no_group = true; else body.all_users = true;
  const og = pgAccessG(other), scope = who === "has" ? `the users who hold ${og ? og.name : "that group"}` : who === "none" ? "the users who hold no group" : "ALL users";
  if (!dry && !await gconfirm(`${act === "add" ? "Add" : "Take"} group ${g.name} ${act === "add" ? "to" : "away from"} ${scope}${S.mcount != null ? " (" + S.mcount + " users)" : ""}? ${act === "add" ? "They get its inbounds at once." : "They lose its inbounds at once."} There is no backup of who held what: to go back, do the opposite.`, {danger: true})) return;
  try {
    const r = await pgApi(S.id, "/groups/members", {method: "POST", body});
    if (dry) {
      S.mcount = r.users; $("#pg-access-mapply").disabled = !r.users;
      res.textContent = r.users == null ? "The panel did not say how many." : `${r.users} user${r.users === 1 ? "" : "s"} match this choice. Nothing has been changed yet.`;
    } else {
      pgAccessMemReset(); closeModal();                                      // the window first, then the message: a message sent while a window is open stays inside it
      toast(`✓ Done for ${scope} (${r.users == null ? "?" : r.users} users)`, "ok"); pgAccessReload(true);
    }
  } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* ================================================================== TEMPLATES */
async function pgAccessTemplates(id, box, quiet) {
  const S = pgAccess;
  if (S.id !== id) { S.id = id; S.g = S.t = null; S.gsel.clear(); S.tsel.clear(); S.gq = S.tq = ""; }
  if (!quiet) pgLoading(box, "Reading the templates…");
  const d = await pgApi(id, "/templates");
  if (!pgAccessLive(box, "templates")) return;
  S.t = d; S.tsel = new Set([...S.tsel].filter(i => d.templates.some(t => t.id === i)));
  pgAccessTemplatesDraw(box);
}
function pgAccessTemplatesDraw(box) {
  const S = pgAccess;
  pgPaint(box, `<div id="pg-access" class="pg-access">${pgBar(
    `${window.pgSetSeg ? pgSetSeg() : ""}<input id="pg-access-tq" type="search" placeholder="Search name or group…" value="${esc(S.tq)}" oninput="pgAccessTQ(this.value)" autocomplete="off" spellcheck="false"><span class="mut pn-cnt" id="pg-access-tcnt"></span><button class="sm" onclick="pgAccessReload()" title="Read the templates from the panel again">↻</button>`,
    `<span id="pg-access-tbulk" class="pg-access-bulk"></span><button class="sm" onclick="pgAccessBackups()" title="What was saved before each change, and the way back">Backups…</button><button class="sm pri" onclick="pgAccessTemplateForm(0)">＋ New template</button>`)}
    <div id="pg-access-tlist"></div></div>`);
  pgAccessTemplatesList();
}
const pgAccessGroupNames = (t) => t.group_ids.map(id => { const g = (pgAccess.t.groups || []).find(x => x.id === id); return g ? g.name : "#" + id; });
function pgAccessTemplatesList() {
  const S = pgAccess, el = $("#pg-access-tlist"); if (!el || !S.t) return;
  const q = S.tq.trim(), rows = S.t.templates.filter(t => nkHit([t.name, t.is_disabled ? "off disabled" : "on", t.status, t.username_prefix, t.username_suffix, ...pgAccessGroupNames(t)].join(" "), q));
  const all = rows.length > 0 && rows.every(t => S.tsel.has(t.id));
  const row = t => { const names = pgAccessGroupNames(t); return `<tr class="${t.is_disabled ? "" : "up"}"><td>${pgSw(S.tsel.has(t.id), `pgAccessTSel(${t.id}, this.checked)`)}</td>
    <td class="name" title="${esc(t.name)}">${esc(t.name)}</td>
    <td>${pgAccessPills(names, {}, null)}</td>
    <td class="num">${esc(pgAccessDataText(t))}</td><td class="num" title="${t.status === "on_hold" ? "counted from the first connection" : ""}">${esc(pgAccessDaysText(t))}</td>
    <td class="mono mut" title="what the panel puts around the name you type">${t.username_prefix || t.username_suffix ? esc(pgAccessUserName(t, "…")) : "—"}</td>
    <td>${pgSw(!t.is_disabled, `pgAccessTemplateOn(${t.id}, this.checked)`)}</td>
    <td class="pg-access-act"><button class="sm" ${t.is_disabled ? "disabled" : ""} onclick="pgAccessUserForm(${t.id})" title="${t.is_disabled ? "Switch the template on first" : "Make a user from this template"}">New user…</button><button class="sm" onclick="pgAccessTemplateForm(${t.id})">Edit</button><button class="sm danger" onclick="pgAccessTemplateDel(${t.id})" title="Delete this template">✕</button></td></tr>`; };
  pgAccessFill(el, rows.length ? `<div class="lbox full pg-access-box"><table class="tbl lift"><colgroup><col style="width:72px"><col style="width:15%"><col><col style="width:140px"><col style="width:112px"><col style="width:96px"><col style="width:68px"><col style="width:222px"></colgroup>
    <thead><tr><th>${pgSw(all, "pgAccessTAll(this.checked)", 'title="Tick every template shown"')}</th><th>Template</th><th>Groups</th><th>Data limit</th><th>Duration</th><th>Username</th><th>On</th><th></th></tr></thead><tbody>${rows.map(row).join("")}</tbody></table></div>`
    : pgEmpty(S.t.templates.length ? "No template matches the search." : "No template yet. A template is a recipe for new users (groups, data limit, days): make one with ＋ New template."));
  const cnt = $("#pg-access-tcnt"); if (cnt) cnt.textContent = `${rows.length}${rows.length === S.t.templates.length ? "" : " of " + S.t.templates.length} template${S.t.templates.length === 1 ? "" : "s"}`;
  pgAccessTBulkBar();
}
function pgAccessTBulkBar() {
  const n = pgAccess.tsel.size, el = $("#pg-access-tbulk"); if (!el) return;
  el.innerHTML = n ? `<span class="mut">${n} selected</span><button class="sm" onclick="pgAccessTBulk('enable')">Enable</button><button class="sm" onclick="pgAccessTBulk('disable')">Disable</button><button class="sm danger" onclick="pgAccessTBulk('delete')">Delete</button><button class="sm" onclick="pgAccessTAll(false)" title="Untick everything">Clear</button>` : "";
}
window.pgAccessTQ = v => { pgAccess.tq = v; pgAccessTemplatesList(); };
window.pgAccessTSel = (id, on) => { const S = pgAccess; if (on) S.tsel.add(id); else S.tsel.delete(id); pgAccessTemplatesList(); };
window.pgAccessTAll = on => {
  const S = pgAccess, q = S.tq.trim();
  S.t.templates.filter(t => nkHit([t.name, t.is_disabled ? "off disabled" : "on", t.status, t.username_prefix, t.username_suffix, ...pgAccessGroupNames(t)].join(" "), q)).forEach(t => { if (on) S.tsel.add(t.id); else S.tsel.delete(t.id); });
  pgAccessTemplatesList();
};
window.pgAccessTemplateOn = async (tid, on) => {
  const S = pgAccess, t = pgAccessT(tid); if (!t) return;
  await pgAccessChange(() => pgApi(S.id, `/templates/${tid}`, {method: "PUT", body: {is_disabled: !on}}), `Template ${t.name} is ${on ? "on" : "off"}`, r => (r.backup ? pgAccessUndoRestore(r.backup, "template", tid) : null));
};

/* ---- the form of one template: units are GB and days here, the server turns them into bytes and seconds */
window.pgAccessTemplateForm = tid => {
  const S = pgAccess, t = tid ? pgAccessT(tid) : null; if (tid && !t) return;
  const v = t || {name: "", group_ids: [], data_limit_gb: 0, expire_days: 0, username_prefix: "", username_suffix: "", status: "active", data_limit_reset_strategy: "no_reset", reset_usages: false, on_hold_days: 0, hwid_limit: null, method: "", is_disabled: false};
  const opts = (list, cur, label = x => x) => list.map(x => `<option value="${esc(x)}" ${x === cur ? "selected" : ""}>${esc(label(x))}</option>`).join("");
  S.tpick = new Set(v.group_ids);
  const groups = S.t.groups || [];
  showModal(`<b>${t ? "Edit template " + esc(t.name) : "New template"}</b>`, `<form class="frm pg-access-frm" onsubmit="return pgAccessTemplateSave(event, ${tid})" autocomplete="off">
    <label style="grid-column: span 2">Name <small>(up to 64 characters)</small><input name="nm" required maxlength="64" value="${esc(v.name)}"></label>
    <div class="pg-access-line"><span>Enabled</span>${pgSw(!v.is_disabled, "", 'name="on"')}</div>
    <label>Data limit <small>GB, empty = unlimited</small><input name="gb" type="number" min="0" step="any" value="${v.data_limit_gb ? pgAccessN(v.data_limit_gb) : ""}"></label>
    <label>Duration <small id="pg-access-dayshint">${v.status === "on_hold" ? "days from the first connection" : "days, empty = no end"}</small><input name="days" type="number" min="0" step="any" value="${v.expire_days ? pgAccessN(v.expire_days) : ""}"></label>
    <label>New users start<select name="status" onchange="pgAccessStatusChange(this.form)">${opts(S.t.statuses || ["active", "on_hold"], v.status, x => x === "on_hold" ? "on hold (clock starts at first use)" : x)}</select></label>
    <label>Data limit resets<select name="reset">${opts(S.t.resets || Object.keys(pgAccessResets), v.data_limit_reset_strategy, x => pgAccessResets[x] || x)}</select></label>
    <label>Username prefix <small>up to 20</small><input name="prefix" maxlength="20" value="${esc(v.username_prefix)}" placeholder="none"></label>
    <label>Username suffix <small>up to 20</small><input name="suffix" maxlength="20" value="${esc(v.username_suffix)}" placeholder="none"></label>
    <label>Device limit <small>empty = none</small><input name="hwid" type="number" min="0" step="1" value="${v.hwid_limit == null ? "" : v.hwid_limit}"></label>
    <label>On-hold timeout <small>days, 0 = never</small><input name="hold" type="number" min="0" step="any" value="${v.on_hold_days ? pgAccessN(v.on_hold_days) : ""}" ${v.status === "on_hold" ? "" : "disabled"}></label>
    <label>Shadowsocks method<select name="method">${opts([""].concat(S.t.methods || []), v.method, x => x || "default (chacha20-ietf-poly1305)")}</select></label>
    <div class="pg-access-line pg-access-full"><span>Reset the usage of a user this template is applied to</span>${pgSw(v.reset_usages, "", 'name="ru"')}</div>
    <div class="pg-access-pick"><div class="pg-access-pickbar"><b>Groups</b><span class="mut" id="pg-access-pickn"></span><span class="sp"></span><small class="mut">the inbounds of these groups go to every user made from the template</small></div>
      <div class="pg-access-picklist" id="pg-access-picklist">${groups.length ? groups.map(g => `<div class="pg-access-prow">${pgSw(S.tpick.has(g.id), "pgAccessGroupTick(this)", `data-gid="${g.id}"`)}<span class="name ell" title="${esc(g.name)}">${esc(g.name)}</span>${g.is_disabled ? '<span class="pill">off</span>' : ""}</div>`).join("") + (v.group_ids.filter(id => !groups.some(g => g.id === id)).map(id => `<div class="pg-access-prow">${pgSw(true, "pgAccessGroupTick(this)", `data-gid="${id}"`)}<span class="name ell">#${id}</span><span class="pill err">gone</span></div>`).join("")) : pgEmpty("No group on the panel yet: make one in the Groups tab first.")}</div></div>
    <div class="frm-act"><button class="pri" type="submit">${t ? "Save template" : "Create template"}</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide");
  pgAccessGroupTickCount();
  setTimeout(() => { const i = document.querySelector("#modal .pg-access-frm input[name=nm]"); if (i) i.focus(); }, 40);
};
function pgAccessGroupTickCount() { const el = $("#pg-access-pickn"); if (el) el.textContent = `${pgAccess.tpick.size} picked`; }
window.pgAccessGroupTick = el => { const S = pgAccess, id = Number(el.dataset.gid); if (el.checked) S.tpick.add(id); else S.tpick.delete(id); pgAccessGroupTickCount(); };
window.pgAccessStatusChange = form => {
  const hold = form.elements.hold, hint = $("#pg-access-dayshint"), onhold = form.elements.status.value === "on_hold";
  hold.disabled = !onhold; if (hint) hint.textContent = onhold ? "days from the first connection" : "days, empty = no end";
};
window.pgAccessTemplateSave = async (e, tid) => {
  e.preventDefault();
  const S = pgAccess, f = e.target.elements, num = x => (x === "" || x == null ? null : Number(x)), gids = pgAccessIds([...S.tpick]);
  if (!gids.length) { toast("✕ Pick at least one group", "bad"); return false; }
  const body = {name: f.nm.value.trim(), group_ids: gids, data_limit_gb: num(f.gb.value), expire_days: num(f.days.value), username_prefix: f.prefix.value.trim(), username_suffix: f.suffix.value.trim(),
    status: f.status.value, data_limit_reset_strategy: f.reset.value, reset_usages: f.ru.checked, hwid_limit: num(f.hwid.value), method: f.method.value, is_disabled: !f.on.checked};
  if (f.status.value === "on_hold") body.on_hold_days = num(f.hold.value);
  const name = body.name;
  if (tid) await pgAccessChange(() => pgApi(S.id, `/templates/${tid}`, {method: "PUT", body}), `Template ${name} saved`, r => (r.backup ? pgAccessUndoRestore(r.backup, "template", tid) : null), true);
  else await pgAccessChange(() => pgApi(S.id, "/templates", {method: "POST", body}), `Template ${name} created`, r => pgAccessUndoCreate(`/templates/${r.template.id}`), true);
  return false;
};
window.pgAccessTemplateDel = async tid => {
  const S = pgAccess, t = pgAccessT(tid); if (!t) return;
  if (!await gconfirm(`Delete template ${t.name}? Users made from it stay as they are; a user that has it queued as the next plan loses that plan. It is saved to a file first and can be restored.`, {danger: true})) return;
  await pgAccessChange(() => pgApi(S.id, `/templates/${tid}`, {method: "DELETE"}), `Template ${t.name} deleted`, r => pgAccessUndoRestore(r.backup, "template", tid));
};
window.pgAccessTBulk = async action => {
  const S = pgAccess, rows = [...S.tsel].map(pgAccessT).filter(Boolean); if (!rows.length) return;
  if (action === "delete" && !await gconfirm(`Delete ${rows.length} template${rows.length === 1 ? "" : "s"} (${rows.map(t => t.name).join(", ")})? Users made from them stay. They are saved to a file first.`, {danger: true})) return;
  await pgAccessChange(() => pgApi(S.id, "/templates/bulk", {method: "POST", body: {action, ids: rows.map(t => t.id)}}).then(r => { S.tsel.clear(); return r; }),
    `${action === "enable" ? "Switched on" : action === "disable" ? "Switched off" : "Deleted"} ${pgAccessCount(rows.length, "template")}`);
};

/* ---- a user from a template (the panel puts the template's prefix and suffix around the name) */
window.pgAccessUserForm = tid => {
  const t = pgAccessT(tid); if (!t) return;
  showModal(`<b>New user from ${esc(t.name)}</b>`, `<form class="frm one pg-access-frm" onsubmit="return pgAccessUserMake(event, ${tid})" autocomplete="off">
    <label>Username <small>3 to 128: letters, digits, - _ @ .</small><input name="un" required minlength="3" maxlength="128" oninput="pgAccessUserPrev(${tid}, this.value)"></label>
    <div class="mut pg-access-prev">The panel names the user <b class="mono" id="pg-access-uprev">${esc(pgAccessUserName(t, ""))}</b></div>
    <label>Note <small>optional</small><input name="note" maxlength="500"></label>
    <small class="mut">${esc(pgAccessDataText(t))} · ${esc(pgAccessDaysText(t))} · ${pgAccessGroupNames(t).map(esc).join(", ") || "no group"}</small>
    <div class="frm-act"><button class="pri" type="submit">Create user</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "narrow");
  setTimeout(() => { const i = document.querySelector("#modal .pg-access-frm input[name=un]"); if (i) i.focus(); }, 40);
};
window.pgAccessUserPrev = (tid, v) => { const t = pgAccessT(tid), el = $("#pg-access-uprev"); if (t && el) el.textContent = pgAccessUserName(t, v.trim()); };
window.pgAccessUserMake = async (e, tid) => {
  e.preventDefault();
  const S = pgAccess, t = pgAccessT(tid), f = e.target.elements, body = {username: f.un.value.trim(), note: f.note.value.trim()};
  try {
    const r = await pgApi(S.id, `/templates/${tid}/user`, {method: "POST", body});
    toast(`✓ User ${r.username} created`, "ok");
    showModal(`<b>New user from ${esc(t.name)}</b>`, `<div class="pg-access-done"><b class="gw-ok">✓ ${esc(r.username)} is created</b>
      ${r.user && r.user.sub ? `<div class="mono pg-access-link" title="the subscription link of the user">${esc(r.user.sub)}</div><div class="frm-act"><button class="sm" data-v="${esc(r.user.sub)}" onclick="pgAccessCopy(this)">Copy the link</button><button class="sm" onclick="pgAccessUserForm(${tid})">Make another</button><button class="pri" onclick="closeModal()">Done</button></div>`
        : `<div class="frm-act"><button class="sm" onclick="pgAccessUserForm(${tid})">Make another</button><button class="pri" onclick="closeModal()">Done</button></div>`}</div>`, "narrow");
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* ================================================================== BACKUPS: what was saved before each change, and the way back */
window.pgAccessBackups = async () => {
  const S = pgAccess;
  showModal("<b>Backups of groups and templates</b>", '<div class="loading"><i class="spin"></i><span>Reading the list…</span></div>', "wide");
  try {
    const list = (await pgApi(S.id, "/access/backups")).backups;
    pgAccessBackupsPaint(list);
  } catch (e) { showModal("<b>Backups of groups and templates</b>", `<div class="err">${esc(e.message)}</div>`, "wide"); }
};
function pgAccessBackupsPaint(list) {
  const row = b => `<tr><td class="mono mut">${esc(when(b.at))}</td><td class="name" title="${esc(b.reason)}">${esc(b.reason || "—")}</td><td class="num">${b.groups} groups · ${b.templates} templates</td><td class="pg-access-act"><button class="sm" onclick="pgAccessRestorePlan('${esc(b.name)}')" title="See what putting this file back would do (changes nothing yet)">Preview…</button></td></tr>`;
  showModal("<b>Backups of groups and templates</b>", `<div class="mut pg-access-memtxt">The groups and templates are saved here before every change that can lose something (the last 20 are kept). Putting a file back never deletes anything: it makes what is gone again (with a new id), and puts changed names, inbounds and settings back. The users a deleted group held are not put back by it.</div>
    ${list.length ? `<div class="lbox full pg-access-box pg-access-bk"><table class="tbl lift"><colgroup><col style="width:160px"><col><col style="width:200px"><col style="width:110px"></colgroup><thead><tr><th>Saved</th><th>Before</th><th>Holds</th><th></th></tr></thead><tbody>${list.map(row).join("")}</tbody></table></div>` : pgEmpty("Nothing saved yet: a file is written before the first change.")}`, "wide");
}
window.pgAccessRestorePlan = async (name, only) => {
  const S = pgAccess;
  showModal("<b>Put a backup back</b>", '<div class="loading"><i class="spin"></i><span>Comparing the file with the panel…</span></div>', "wide");
  try {
    const r = await pgApi(S.id, `/access/backups/${encodeURIComponent(name)}/restore`, {method: "POST", body: only ? {...only, dry_run: true} : {everything: true, dry_run: true}});
    pgAccessRestorePaint(name, r, only);
  } catch (e) { showModal("<b>Put a backup back</b>", `<div class="err">${esc(e.message)}</div><div class="frm-act"><button onclick="pgAccessBackups()">← Back</button></div>`, "wide"); }
};
const pgAccessActionText = {create: "make again", update: "put back", same: "unchanged", skip: "left alone", failed: "failed"};
function pgAccessRestorePaint(name, r, only) {
  const todo = r.items.filter(i => i.action === "create" || i.action === "update"), done = !r.dry_run;
  const row = i => `<tr><td><span class="td-type">${esc(i.kind)}</span></td><td class="name" title="${esc(i.name)}">${esc(i.name)}</td><td><span class="pill ${i.action === "failed" ? "err" : i.ok ? "ok" : ""}">${esc(done && i.ok ? (i.action === "create" ? "made again" : "put back") : pgAccessActionText[i.action] || i.action)}</span></td><td class="mut" title="${esc(i.reason)}">${esc(i.reason)}</td>
    <td class="pg-access-act">${!done && (i.action === "create" || i.action === "update") ? `<button class="sm" onclick="pgAccessRestoreGo('${esc(name)}', ${i.kind === "group" ? `{groups: [${i.id}], templates: []}` : `{groups: [], templates: [${i.id}]}`})">Only this</button>` : ""}</td></tr>`;
  showModal("<b>Put a backup back</b>", `<div class="mut pg-access-memtxt">${done ? `${r.failed ? "Finished with a problem" : "Done"}: ${r.created} made again, ${r.updated} put back${r.failed ? `, <b class="err">${r.failed} failed</b>` : ""}.${r.backup ? ` The state before is saved as ${esc(r.backup)}.` : ""}` : todo.length ? `${todo.length} thing${todo.length === 1 ? "" : "s"} would change. Nothing has been done yet; nothing is ever deleted.` : "The panel already has everything this file holds (or what is left is skipped): there is nothing to put back."}</div>
    ${r.items.length ? `<div class="lbox full pg-access-box pg-access-bk"><table class="tbl lift"><colgroup><col style="width:90px"><col style="width:26%"><col style="width:120px"><col><col style="width:100px"></colgroup><thead><tr><th>Kind</th><th>Name</th><th>${done ? "Result" : "Would be"}</th><th>Why</th><th></th></tr></thead><tbody>${r.items.map(row).join("")}</tbody></table></div>` : pgEmpty("Nothing in the file matches.")}
    <div class="frm-act">${!done && todo.length ? `<button class="pri" onclick="pgAccessRestoreGo('${esc(name)}', ${only ? JSON.stringify(only).replace(/"/g, "&quot;") : "null"})">Put back ${todo.length}</button>` : ""}<button onclick="pgAccessBackups()">← All backups</button><button onclick="closeModal()">Close</button></div>`, "wide");
}
window.pgAccessRestoreGo = async (name, only) => {
  const S = pgAccess;
  if (!await gconfirm(`Restore from this backup${only ? " (only the chosen item)" : ""}? What is gone is made again with a new id and what changed is put back; nothing is deleted. The state before is saved first.`)) return;
  try {
    const r = await pgApi(S.id, `/access/backups/${encodeURIComponent(name)}/restore`, {method: "POST", body: only ? {...only, dry_run: false} : {everything: true, dry_run: false}});
    toast(`${r.failed ? "✕" : "✓"} ${r.created} made again, ${r.updated} put back${r.failed ? `, ${r.failed} failed` : ""}`, r.failed ? "bad" : "ok");
    pgAccessRestorePaint(name, r, only);
    pgAccessReload(true);
  } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* ---- register the two tabs (app.js adds the buttons for a PasarGuard panel and calls render when one is opened) */
pgTabs.groups = {label: "Groups", order: 30, types: ["pasarguard"], render: (id, box) => pgAccessGroups(id, box)};
pgTabs.templates = {label: "Templates", order: 31, types: ["pasarguard"], render: (id, box) => pgAccessTemplates(id, box)};      // (the owner, 2026-10-15: only the USER template stays, as the last section of Settings; the client templates of pg_client.js have no door any more)
