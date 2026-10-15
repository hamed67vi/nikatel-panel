/* PasarGuard tabs of the Panels page: Admins (with their API keys) and Settings. Registers itself in pgTabs (see pg_common.js).
   Everything goes through /api/panels/{id}/pg/admins, /pg/admin/api-keys and /pg/settings. A tab reads from the panel when it is opened (and after a change), never on every
   redraw. A secret (bot token, webhook secret, an API key) never comes to the browser: it is shown as set / not set, and it is sent only when a new value was typed. */
"use strict";

const pgAdminS = {id: 0, seen: {}, flight: {}, view: "admins", q: "", sort: "name", kq: "", A: null, K: null, S: null, sect: "telegram", draft: {}, clear: {}, sel: new Set(), usage: null};

/* ------------------------------------------------------------------ small helpers */
const pgAdminFind = id => ((pgAdminS.A || {}).admins || []).find(a => a.id === id);
const pgAdminKeyOf = id => ((pgAdminS.K || {}).keys || []).find(k => k.id === id);
const pgAdminTehranDay = (plus = 0) => new Date(Date.now() + 12600000 + plus * 86400000).toISOString().slice(0, 10);      // the Tehran calendar day (UTC+3:30), YYYY-MM-DD
const pgAdminDays = iso => iso ? Math.ceil((Date.parse(iso) - Date.now()) / 86400000) : null;
const pgAdminLabel = s => String(s || "").replace(/_/g, " ");
const pgAdminSelect = (name, options, cur, attrs = "") => `<select name="${name}" ${attrs}>${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
const pgAdminSeg = items => `<div class="pg-admin-seg">${items.map(([k, l, on, cls, click]) => `<button type="button" class="${on ? "on" : ""} ${cls || ""}" onclick="${click}">${esc(l)}</button>`).join("")}</div>`;
const pgAdminViewSeg = () => pgAdminSeg([["admins", "Admins", pgAdminS.view === "admins", "", "pgAdminView('admins')"], ["keys", "API keys", pgAdminS.view === "keys", "", "pgAdminView('keys')"], ["roles", "Roles", pgAdminS.view === "roles", "", "pgAdminView('roles')"]]);

/* the app draws a switch by the class "on" of its label, not by :checked: in the forms of these tabs (nothing is redrawn while a box is ticked) the class follows the box */
document.addEventListener("change", e => {
  const t = e.target; if (!(t instanceof HTMLInputElement) || t.type !== "checkbox") return;
  const sw = t.closest("label.srv-sw"); if (sw && t.closest(".pg-admin-form2, .pg-admin-formbox, .pg-admin-box")) sw.classList.toggle("on", t.checked);
});

/* a tab that was left for a while is read again when it comes back; the redraws the page makes by itself (every ~15 s) use what was read */
function pgAdminEnter(id, tab) {
  const S = pgAdminS, now = Date.now();
  if (S.id !== id) Object.assign(S, {id, seen: {}, flight: {}, A: null, K: null, S: null, draft: {}, clear: {}, q: "", kq: "", sel: new Set(), usage: null});
  const gap = now - (S.seen[tab] || 0);
  S.seen[tab] = now;
  if (gap > 25000) { if (tab === "admins") { S.A = null; S.K = null; } else S.S = null; }      // (an edit that was not saved is a draft of its own and stays)
}
async function pgAdminLoad(what) {
  const S = pgAdminS, id = S.id;
  if (!S.flight[what]) S.flight[what] = (async () => {
    try { const r = await pgApi(id, what === "A" ? "/admins" : what === "K" ? "/admin/api-keys" : "/settings"); if (S.id === id) S[what] = r; }
    finally { delete S.flight[what]; }
  })();
  await S.flight[what];
}
window.pgAdminReload = async () => { const S = pgAdminS; S.A = S.K = S.S = null; await refresh(); };
window.pgAdminView = v => { pgAdminS.view = v; pgAdminS.sel = new Set(); refresh(); };
const pgAdminFail = (e, box) => { if (box.isConnected) pgError(box, e); };
function pgAdminRepaint() { const box = $("#pn-body"), S = pgAdminS; if (!box) return; if (pn.tab === "admins" && S.A && S.view === "admins") pgAdminPaintAdmins(box); else if (pn.tab === "admins" && S.K && S.view === "keys") pgAdminPaintKeys(box); }

/* ------------------------------------------------------------------ the one-time secret / password window */
function pgAdminOnce(title, lead, label, value, note) {
  showModal(title, `<div class="pg-admin-once"><p>${lead}</p><div class="pg-admin-onebox"><span class="mut">${esc(label)}</span><input readonly value="${esc(value)}" onfocus="this.select()" spellcheck="false"><button class="sm pri" type="button" onclick="pgAdminCopyIn(this)">Copy</button></div>
    <p class="pg-admin-warn">${note}</p><div class="frm-act"><button class="pri" type="button" onclick="closeModal()">Done</button></div></div>`, "narrow keep");
}
window.pgAdminCopyIn = async btn => {
  const i = btn.parentElement.querySelector("input"); if (!i) return;
  try { await navigator.clipboard.writeText(i.value); } catch (e) { const was = i.type; i.type = "text"; i.select(); try { document.execCommand("copy"); } catch (e2) { /* no clipboard at all: the text stays selected */ } i.type = was; }
  toast("✓ Copied", "ok");
};

/* ------------------------------------------------------------------ the password helper: strong, made in the browser, shown once, never stored */
const pgAdminPw = {up: "ABCDEFGHJKLMNPQRSTUVWXYZ", lo: "abcdefghijkmnopqrstuvwxyz", di: "23456789", sp: "!@#$%^&*-_=+"};
const pgAdminRules = [["12+ characters", p => p.length >= 12], ["2 digits", p => (p.match(/\d/g) || []).length >= 2], ["2 capitals", p => (p.match(/[A-Z]/g) || []).length >= 2],
  ["2 small letters", p => (p.match(/[a-z]/g) || []).length >= 2], ["1 special", p => /[!@#$%^&*()\-_=+[\]{}|;:,.<>?/~`]/.test(p)], ['no "', p => !p.includes('"')],
  ["72 bytes at most", p => new TextEncoder().encode(p).length <= 72], ["not the username", (p, u) => !u || !p.toLowerCase().includes(u.toLowerCase())]];
function pgAdminRand(n) { const a = new Uint32Array(1), lim = Math.floor(4294967296 / n) * n; do { crypto.getRandomValues(a); } while (a[0] >= lim); return a[0] % n; }      // no bias
function pgAdminGen() {
  const pick = (s, k) => Array.from({length: k}, () => s[pgAdminRand(s.length)]);
  const c = [...pick(pgAdminPw.up, 3), ...pick(pgAdminPw.lo, 3), ...pick(pgAdminPw.di, 3), ...pick(pgAdminPw.sp, 2), ...pick(pgAdminPw.up + pgAdminPw.lo + pgAdminPw.di, 9)];
  for (let i = c.length - 1; i > 0; i--) { const j = pgAdminRand(i + 1); [c[i], c[j]] = [c[j], c[i]]; }
  return c.join("");
}
const pgAdminPwHtml = (hint = "") => `<div class="pg-admin-pw"><div class="pg-admin-pwrow"><input name="password" type="password" autocomplete="new-password" data-lpignore="true" data-1p-ignore spellcheck="false" placeholder="a password" oninput="pgAdminPwCheck(this)">
  <button class="sm" type="button" onclick="pgAdminPwShow(this)">Show</button><button class="sm" type="button" onclick="pgAdminPwGenerate(this)" title="A strong password made here in the browser">Generate</button><button class="sm" type="button" onclick="pgAdminCopyIn(this)">Copy</button></div>
  <div class="pg-admin-rules">${pgAdminRules.map(([l]) => `<span>${esc(l)}</span>`).join("")}</div>${hint ? `<small class="mut">${hint}</small>` : ""}</div>`;
window.pgAdminPwCheck = inp => {
  const f = inp.form, user = f && f.username ? f.username.value.trim() : (inp.dataset.user || ""), box = inp.closest(".pg-admin-pw");
  box.querySelectorAll(".pg-admin-rules span").forEach((s, i) => { s.className = inp.value ? (pgAdminRules[i][1](inp.value, user) ? "ok" : "bad") : ""; });
};
window.pgAdminPwShow = btn => { const i = btn.parentElement.querySelector("input"), show = i.type === "password"; i.type = show ? "text" : "password"; btn.textContent = show ? "Hide" : "Show"; };
window.pgAdminPwGenerate = btn => { const i = btn.parentElement.querySelector("input"); i.value = pgAdminGen(); i.type = "text"; btn.parentElement.querySelector("button").textContent = "Hide"; pgAdminPwCheck(i); };

/* ================================================================== ADMINS */
pgTabs.admins = {label: "Admins", order: 60, types: ["pasarguard"], render: async (id, box) => {
  const S = pgAdminS; pgAdminEnter(id, "admins");
  if (S.view === "roles" && window.pgRolesRender) return pgRolesRender(id, box);      // the third view: the roles (pg_roles.js)
  const w = S.view === "keys" ? "K" : "A";
  if (!S[w]) { pgLoading(box, w === "K" ? "Reading the API keys…" : "Reading the admins…"); try { await pgAdminLoad(w); } catch (e) { pgAdminFail(e, box); return; } }
  if (!box.isConnected || S.id !== id || !S[w]) return;
  if (w === "K") pgAdminPaintKeys(box); else pgAdminPaintAdmins(box);
}};

function pgAdminPaintAdmins(box) {
  const S = pgAdminS, A = S.A, me = A.me || {}, n = st => A.admins.filter(a => a.status === st).length, pill = (c, l, k = "") => `<span class="pill ${k}">${c} ${l}</span>`;
  const left = pgAdminViewSeg() + `<input type="search" class="pg-admin-q" placeholder="Search admin…" value="${esc(S.q)}" oninput="pgAdminQ(this.value)" autocomplete="off" spellcheck="false">`
    + `<span class="pg-admin-cnt">${pill(A.admins.length, "admins")}${pill(n("active"), "active", "ok")}${n("disabled") ? pill(n("disabled"), "off", "warn") : ""}${n("limited") ? pill(n("limited"), "limited", "err") : ""}</span>`;
  const ns = pgAdminTicked().length, bulk = `<select class="sm pg-admin-bulk" id="pg-admin-bulk" ${ns ? "" : "hidden"} onchange="pgAdminBulk(this)" title="One action for every ticked admin, done in one call (all of them or none)">${pgAdminBulkOpts(ns)}</select>`;
  const right = `${bulk}<button class="sm" onclick="pgAdminReload()" title="Read the admins from the panel again">↻</button><button class="sm pri" onclick="pgAdminEdit(0)">＋ Add admin</button>`;
  pgPaint(box, pgBar(left, right) + `<div class="lbox full pg-admin-box" id="pg-admin-list">${pgAdminTable()}</div>
    <small class="mut pg-admin-note2">Nikatel is signed in to this panel as <b>${esc(me.username || "?")}</b>${me.role && me.role.name ? " (" + esc(me.role.name) + ")" : ""}: that admin can not be switched off, deleted or given a new password from here.${A.truncated ? " The panel has more admins than are listed." : ""}</small>`);
  pgAdminBulkSync();
  const q = $(".pg-admin-q"); if (q && S.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
function pgAdminRows() {
  const S = pgAdminS, rows = S.A.admins.filter(a => nkHit([a.username, a.role.name, a.status, a.note, a.telegram_id].join(" "), S.q));
  const by = {name: a => a.username.toLowerCase(), users: a => -a.users, usage: a => -(a.limit ? a.used / a.limit : 0)}[S.sort] || (a => a.username.toLowerCase());
  return rows.sort((x, y) => (by(x) < by(y) ? -1 : by(x) > by(y) ? 1 : 0));
}
function pgAdminTable() {
  const S = pgAdminS, rows = pgAdminRows(), all = rows.length > 0 && rows.every(a => S.sel.has(a.id));
  const th = (k, l, extra = "", attrs = "") => `<th class="pg-admin-th ${S.sort === k ? "on" : ""}" ${attrs} onclick="pgAdminSort('${k}')" title="Order the list by this">${l}${S.sort === k ? " ▾" : ""}${extra}</th>`;
  return `<table class="tbl lift pg-admin-tbl"><colgroup><col style="width:34px"><col style="width:27%"><col style="width:150px"><col style="width:64px"><col><col style="width:200px"></colgroup>
    <thead><tr><th class="pg-ck"><input type="checkbox" class="ck" id="pg-admin-all" ${all ? "checked" : ""} ${rows.length ? "" : "disabled"} onchange="pgAdminSelAll(this.checked)" aria-label="Tick every admin in the list" title="Tick every admin in the list"></th>${th("name", "Admin", "", 'data-prio="0"')}<th>Status</th>${th("users", "Users")}${th("usage", "Traffic", " <small>used · left · limit</small>")}<th></th></tr></thead>
    <tbody>${rows.map(pgAdminRow).join("") || `<tr><td colspan="6" class="mut">${S.A.admins.length ? "no admin matches" : "this panel has no admin"}</td></tr>`}</tbody></table>`;
}
const pgAdminEmo = base => base || "🧑‍💼";      // ONE face for every admin (the owner, 2026-10-16: "one admin emoji, the same for all"); the state of a row is told by its switch and its pill; an API key has its own (a key)
function pgAdminRow(a) {
  const off = a.status === "disabled";
  const sub = [a.note, a.telegram_id ? "telegram " + a.telegram_id : "", a.has_discord ? "discord" : ""].filter(Boolean).join(" · ");
  const more = [["password", "Change password…", !a.me], ["reset", "Reset usage", true], ["udis", "Switch its users off…", true], ["uact", "Switch its users on…", true], ["udel", "Delete its users…", true], ["del", "Delete admin…", !a.me && !a.is_owner]].filter(x => x[2]);
  return `<tr class="${a.status === "active" ? "up" : off ? "" : "down"}"><td class="pg-ck"><input type="checkbox" class="ck pg-admin-achk" value="${a.id}" ${pgAdminS.sel.has(a.id) ? "checked" : ""} onchange="pgAdminSel(this)" aria-label="Tick ${esc(a.username)}"></td>
    <td class="name"><div class="pg-admin-nm"><i class="pg-admin-emo" title="${esc(a.is_owner ? "owner" : a.status)}">${pgAdminEmo()}</i><b class="pg-admin-link" onclick="pgAdminEdit(${a.id})" title="Edit this admin">${esc(a.username)}</b>
      ${a.is_owner ? '<span class="pill pg-admin-own">owner</span>' : `<span class="pill">${esc(a.role.name || "no role")}</span>`}${a.me ? '<span class="pill ok" title="Nikatel is signed in to the panel as this admin">you</span>' : ""}</div>${sub ? `<small class="mut ell" title="${esc(sub)}">${esc(sub)}</small>` : ""}</td>
    <td><span class="pg-admin-stat">${pgSw(!off, `pgAdminToggle(${a.id}, this.checked)`, a.me || a.is_owner ? 'disabled title="This admin can not be switched off from here"' : "")}<span class="spill ${esc(a.status)}">${esc(a.status)}</span></span></td>
    <td class="pg-admin-num">${a.users}</td><td class="trf" title="${a.lifetime == null ? "" : "Lifetime total: " + fmtB(a.lifetime)}">${pnTraffic(a)}</td>
    <td class="pg-admin-acts"><button class="sm" onclick="pgAdminEdit(${a.id})">Edit</button><button class="sm" onclick="pgAdminUsage(${a.id})">Usage</button>
      <select class="sm pg-admin-menu" onchange="pgAdminMenu(${a.id}, this)" title="More"><option value="">More</option>${more.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></td></tr>`;
}
const pgAdminDrawList = () => { const el = $("#pg-admin-list"); if (el) { el.innerHTML = pgAdminTable(); pgAdminBulkSync(); } };
window.pgAdminQ = v => { pgAdminS.q = v.trim(); pgAdminDrawList(); };
window.pgAdminSort = k => { pgAdminS.sort = k; pgAdminDrawList(); };

window.pgAdminToggle = async (id, on) => {
  const a = pgAdminFind(id); if (!a) return;
  if (!on) {
    const cut = a.role.disconnects && a.users ? ` The role ${a.role.name} disconnects the users of a switched-off admin: its ${a.users} users lose access at once.` : "";
    if (!await gconfirm(`Switch off the admin ${a.username}? It can not sign in to the panel.${cut}`, {danger: true})) { pgAdminDrawList(); return; }
  }
  const r = await pgDo(() => pgApi(pgAdminS.id, `/admins/${id}/status`, {method: "POST", body: {status: on ? "active" : "disabled"}}), `${a.username} is ${on ? "on" : "off"}`);
  if (r) Object.assign(a, r.admin, {me: a.me});
  pgAdminRepaint();
};
window.pgAdminMenu = async (id, sel) => {
  const v = sel.value, a = pgAdminFind(id); sel.selectedIndex = 0; if (!v || !a) return;
  const run = (path, okText, opts = {method: "POST"}) => pgDo(() => pgApi(pgAdminS.id, `/admins/${id}${path}`, opts), okText, pgAdminReload);
  if (v === "password") return pgAdminPassword(id);
  if (v === "del") return pgAdminDelete(id);
  if (v === "reset") { if (await gconfirm(`Reset the used traffic of ${a.username} to zero? A limited admin becomes active again; its lifetime total keeps the old figure.`)) run("/reset-usage", `usage of ${a.username} is zero`); return; }
  if (v === "udis") { if (await gconfirm(`Switch off all the active users of ${a.username} (${a.users} users in all)? They lose access at once.`, {danger: true})) run("/users/disable", `the users of ${a.username} are off`); return; }
  if (v === "uact") { if (await gconfirm(`Switch on EVERY disabled user of ${a.username}? That includes the users that were switched off on purpose.`, {danger: true})) run("/users/activate", `the users of ${a.username} are on`); return; }
  if (v === "udel") {
    const t = await gprompt(`Delete ALL ${a.users} users of ${a.username} for good? They are removed from the panel and from its nodes. Type ${a.username} to confirm.`, "");
    if (t !== null) run("/users/delete", `the users of ${a.username} are deleted`, {method: "POST", body: JSON.stringify({confirm: t.trim()})});
  }
};

/* ---- several admins at once: a tick box in the first column and one menu in the bar; the panel does the action for all of them in one call (all or none) */
const pgAdminTicked = () => [...pgAdminS.sel].filter(pgAdminFind);                   // (an admin that was deleted since it was ticked does not count)
const pgAdminBulkOpts = n => `<option value="">With the ${n} ticked…</option><option value="enable">Switch them on</option><option value="disable">Switch them off…</option><option value="reset">Reset their usage…</option>`
  + `<option value="users_disable">Switch their users off…</option><option value="users_activate">Switch their users on…</option><option value="users_delete">Delete their users…</option>`;
function pgAdminBulkSync() {
  const n = pgAdminTicked().length, m = $("#pg-admin-bulk"), all = $("#pg-admin-all");
  if (m) { m.hidden = !n; if (m.options.length) m.options[0].textContent = `With the ${n} ticked…`; }
  if (all) { const ids = pgAdminRows().map(a => a.id), k = ids.filter(i => pgAdminS.sel.has(i)).length; all.checked = k > 0 && k === ids.length; all.indeterminate = k > 0 && k < ids.length; }
}
window.pgAdminSel = el => { const S = pgAdminS, id = +el.value; if (el.checked) S.sel.add(id); else S.sel.delete(id); pgAdminBulkSync(); };
window.pgAdminSelAll = on => { const S = pgAdminS; pgAdminRows().forEach(a => { if (on) S.sel.add(a.id); else S.sel.delete(a.id); }); pgAdminDrawList(); };
window.pgAdminBulk = async sel => {
  const S = pgAdminS, v = sel.value, ids = pgAdminTicked(); sel.selectedIndex = 0; if (!v || !ids.length) return;
  const rows = ids.map(pgAdminFind), n = ids.length, names = rows.map(a => a.username), users = rows.reduce((s, a) => s + (a.users || 0), 0);
  const who = `${n} admin${n === 1 ? "" : "s"} (${names.slice(0, 5).join(", ")}${n > 5 ? ", …" : ""})`, body = {action: v, ids};
  const done = {enable: "switched on", disable: "switched off", reset: "usage is zero for", users_disable: "users switched off for", users_activate: "users switched on for", users_delete: "users deleted for"}[v];
  if (v === "disable") {
    const me = rows.find(a => a.me); if (me) { toast(`✕ Nikatel is signed in as ${me.username}: untick it first, it can not be switched off`, "bad"); return; }
    if (!await gconfirm(`Switch off ${who}? They can not sign in to the panel.`, {danger: true})) return;
  } else if (v === "enable") {
    if (!await gconfirm(`Switch on ${who}?`)) return;
  } else if (v === "reset") {
    if (!await gconfirm(`Reset the used traffic of ${who} to zero? A limited admin becomes active again; its lifetime total keeps the old figure.`)) return;
  } else if (v === "users_disable") {
    if (!await gconfirm(`Switch off all the active users of ${who} (${users} users in all)? They lose access at once.`, {danger: true})) return;
  } else if (v === "users_activate") {
    if (!await gconfirm(`Switch on EVERY disabled user of ${who}? That includes the users that were switched off on purpose.`, {danger: true})) return;
  } else if (v === "users_delete") {
    const t = await gprompt(`Delete ALL ${users} users of ${who} for good? They are removed from the panel and from its nodes; a record of each admin is saved first. Type delete to confirm.`, "");
    if (t === null) return;
    body.confirm = t.trim();
  }
  const r = await pgDo(() => pgApi(S.id, "/admins/bulk", {method: "POST", body}), "");
  if (!r) return;
  toast(`✓ ${done} ${r.count} admin${r.count === 1 ? "" : "s"}${r.records ? " · " + r.records + " record" + (r.records === 1 ? "" : "s") + " saved" : ""}`, "ok");
  S.sel = new Set();
  await pgAdminReload();
};

/* ---- add / edit */
function pgAdminFormHtml(a) {
  const A = pgAdminS.A, edit = !!a, v = a || {username: "", role: {}, limit: null, telegram_id: "", note: "", sub_domain: "", sub_template: "", profile_title: "", support_url: "", has_discord: false, notify: {}, overrides: {}};
  const roles = (A.roles || []).filter(r => !r.is_owner), lockRole = edit && (a.me || a.is_owner);
  const fld = (label, inner, tip = "") => `<label class="kv2 kf" title="${esc(tip)}"><span>${label}</span>${inner}</label>`;
  const inp = (name, val, attrs = "") => `<input name="${name}" value="${esc(val == null ? "" : val)}" autocomplete="off" spellcheck="false" ${attrs}>`;
  const notify = [["create", "A user is created"], ["modify", "A user is changed"], ["delete", "A user is deleted"], ["status_change", "A user changes status"], ["reset_data_usage", "A user's data is reset"], ["data_reset_by_next", "Reset by the next plan"], ["subscription_revoked", "A subscription is revoked"]];
  const ov = [["max_users", "Most users", ""], ["data_limit_min_gb", "Smallest data limit", "GB"], ["data_limit_max_gb", "Largest data limit", "GB"], ["expire_min_days", "Shortest expiry", "days"], ["expire_max_days", "Longest expiry", "days"],
    ["min_hwid_per_user", "Fewest devices", ""], ["max_hwid_per_user", "Most devices", ""], ["on_hold_timeout_min_days", "Shortest on-hold", "days"], ["on_hold_timeout_max_days", "Longest on-hold", "days"]];
  const roleCtl = A.roles_supported || roles.length
    ? pgAdminSelect("role_id", (edit ? [] : [["", "pick a role…"]]).concat(lockRole ? [[v.role.id, v.role.name]] : roles.map(r => [r.id, r.name])), v.role.id == null ? "" : v.role.id, lockRole ? "disabled" : "")
    : `<span class="pg-admin-sudo">${pgSw(false, "", 'name="is_sudo"')}<span class="mut">full access (sudo)</span></span>`;
  const account = `<div class="sbox"><h5><i>👤</i>Account</h5>${fld("Username", edit ? `<b class="mono">${esc(v.username)}</b>` : inp("username", "", 'required minlength="3" maxlength="34" oninput="pgAdminPwCheck(this.form.password)" placeholder="3 to 34 characters"'))}
    ${fld("Role", roleCtl, "What the admin may do on the panel")}${fld("Data limit", inp("data_limit_gb", v.limit ? +(v.limit / GB).toFixed(2) : 0, 'type="number" min="0" step="0.1"') + "<em>GB</em>", "All its users together; 0 = no limit")}
    ${fld("Note", inp("note", v.note, 'maxlength="500" placeholder="optional"'))}
    ${edit ? "" : `<div class="pg-admin-pwbox"><div class="mut pg-admin-pwlab">Password <small>(shown only here; Nikatel does not keep it)</small></div>${pgAdminPwHtml()}</div>`}</div>`;
  const contact = `<div class="sbox"><h5><i>✉</i>Contact</h5>${fld("Telegram id", inp("telegram_id", v.telegram_id, 'type="number" min="1" placeholder="a number"'), edit && v.telegram_id ? "The panel can change a Telegram id but not remove it" : "")}
    ${fld("Discord webhook", `<span class="pg-admin-secrow">${v.has_discord ? '<span class="pill ok">set</span>' : '<span class="pill">not set</span>'}<input name="discord_webhook" type="password" autocomplete="new-password" data-lpignore="true" data-1p-ignore spellcheck="false" maxlength="1024" placeholder="${v.has_discord ? "type a new address to replace it" : "https://discord.com/api/webhooks/…"}"></span>`)}
    ${edit && v.has_discord ? fld("Remove the webhook", pgSw(false, "", 'name="clear_discord"')) : ""}</div>`;
  const brand = `<div class="sbox"><h5><i>🔗</i>Its subscription pages</h5>${fld("Domain", inp("sub_domain", v.sub_domain, 'maxlength="256" placeholder="optional"'))}${fld("Template", inp("sub_template", v.sub_template, 'maxlength="1024" placeholder="optional"'))}
    ${fld("Profile title", inp("profile_title", v.profile_title, 'maxlength="512" placeholder="optional"'))}${fld("Support address", inp("support_url", v.support_url, 'maxlength="1024" placeholder="optional"'))}</div>`;
  const notes = `<div class="sbox"><h5><i>🔔</i>Alerts about its users</h5><div class="pg-admin-swgrid">${notify.map(([k, l]) => `<div class="pg-admin-sw"><span>${l}</span>${pgSw(v.notify[k] !== false, "", `name="n_${k}"`)}</div>`).join("")}</div></div>`;
  const limits = `<details class="sbox pg-admin-adv"><summary><b>Limits of its own</b> <small class="mut">on top of its role; empty = the role decides</small></summary><div class="pg-admin-ovgrid">${ov.map(([k, l, u]) => fld(l, inp("o_" + k, v.overrides[k], 'type="number" min="0" step="any"') + (u ? `<em>${u}</em>` : ""))).join("")}</div></details>`;
  return `<form class="pg-admin-form2" onsubmit="return pgAdminSave(event, ${edit ? a.id : 0})"><div class="pg-admin-cols"><div>${account}${contact}</div><div>${brand}${notes}</div></div>${limits}
    ${edit && lockRole ? `<div class="mut pg-admin-lockmsg">${a.me ? "Nikatel is signed in as this admin: its role stays." : "An owner's role can not be changed."}</div>` : ""}
    <div class="frm-act"><button class="pri" type="submit">${edit ? "Save" : "Create admin"}</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`;
}
window.pgAdminEdit = id => {
  const a = id ? pgAdminFind(id) : null; if (id && !a) return;
  showModal(a ? `<b>${esc(a.username)}</b> <span class="pill">${esc(a.role.name || "no role")}</span>` : "<b>New admin</b>", pgAdminFormHtml(a), "wide keep pg-admin-win");
  const f = $("#modal .pg-admin-form2"); if (f && f.username) f.username.focus();
};
window.pgAdminSave = async (e, id) => {
  e.preventDefault();
  const f = e.target, fd = new FormData(f), g = n => String(fd.get(n) == null ? "" : fd.get(n)).trim(), body = {};
  if (!id) { body.username = g("username"); body.password = String(fd.get("password") || ""); if (!body.password) { toast("✕ give the new admin a password", "bad"); return false; } }
  if (f.role_id && !f.role_id.disabled) { if (g("role_id")) body.role_id = +g("role_id"); else if (!id) { toast("✕ pick a role for the new admin", "bad"); return false; } }
  if (f.is_sudo) body.is_sudo = f.is_sudo.checked;
  if (g("data_limit_gb") !== "") body.data_limit_gb = +g("data_limit_gb");
  if (g("telegram_id") !== "") body.telegram_id = +g("telegram_id");
  for (const k of ["note", "sub_domain", "sub_template", "profile_title", "support_url"]) body[k] = g(k);
  if (g("discord_webhook")) body.discord_webhook = g("discord_webhook");
  if (f.clear_discord && f.clear_discord.checked) body.clear_discord = true;
  body.notify = Object.fromEntries(["create", "modify", "delete", "status_change", "reset_data_usage", "data_reset_by_next", "subscription_revoked"].map(k => [k, f["n_" + k].checked]));
  body.overrides = Object.fromEntries([...f.querySelectorAll("[name^=o_]")].map(i => [i.name.slice(2), i.value.trim() === "" ? null : +i.value]));
  try {
    const r = await pgApi(pgAdminS.id, id ? `/admins/${id}` : "/admins", {method: id ? "PUT" : "POST", body});
    pgAdminS.A = null;
    if (id) { toast(`✓ ${r.admin.username} saved`, "ok"); closeModal(); refresh(); return false; }
    refresh();
    pgAdminOnce(`<b>${esc(r.admin.username)}</b> is made`, `The admin <b>${esc(r.admin.username)}</b> (${esc(r.admin.role.name)}) was added to the panel.`, "Password", body.password, "Copy the password now: it is shown only here. Nikatel does not keep it and the panel can not show it again.");
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};

/* ---- password, delete, usage */
window.pgAdminPassword = id => {
  const a = pgAdminFind(id); if (!a) return;
  showModal(`<b>New password for ${esc(a.username)}</b>`, `<form class="pg-admin-form2" onsubmit="return pgAdminPasswordGo(event, ${id})">${pgAdminPwHtml("The admin signs in with it from now on; the old one stops working.").replace('name="password"', `name="password" data-user="${esc(a.username)}"`)}
    <div class="frm-act"><button class="pri" type="submit">Change password</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "narrow keep");
};
window.pgAdminPasswordGo = async (e, id) => {
  e.preventDefault(); const pw = String(new FormData(e.target).get("password") || ""), a = pgAdminFind(id);
  try { await pgApi(pgAdminS.id, `/admins/${id}/password`, {method: "POST", body: {password: pw}}); pgAdminOnce(`<b>Password changed</b>`, `The password of <b>${esc(a.username)}</b> is changed.`, "New password", pw, "Copy it now if you need it: Nikatel does not keep it."); }
  catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.pgAdminDelete = id => {
  const a = pgAdminFind(id); if (!a) return;
  showModal(`<b>Delete ${esc(a.username)}</b>`, `<form class="pg-admin-form2" onsubmit="return pgAdminDeleteGo(event, ${id})"><p class="pg-admin-note">This removes the admin <b>${esc(a.username)}</b> from the panel for good, together with its API keys. A record of the admin is kept on the Nikatel server.</p>
    ${a.users ? `<label class="kv2 kf"><span>Its ${a.users} user${a.users === 1 ? "" : "s"}</span>${pgAdminSelect("users", [["keep", "stay on the panel, without an owner"], ["disable", "are switched off first"], ["delete", "are deleted with it"]], "keep", 'onchange="pgAdminDelMode(this)"')}</label>
    <label class="kv2 kf pg-admin-typed" hidden><span>Type <b>${esc(a.username)}</b></span><input name="confirm" autocomplete="off" spellcheck="false"></label>` : ""}
    <div class="frm-act"><button class="danger" type="submit">Delete admin</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "narrow keep");
};
window.pgAdminDelMode = sel => { const t = sel.form.querySelector(".pg-admin-typed"); if (t) { t.hidden = sel.value !== "delete"; if (!t.hidden) t.querySelector("input").focus(); } };
window.pgAdminDeleteGo = async (e, id) => {
  e.preventDefault(); const a = pgAdminFind(id), fd = new FormData(e.target), users = String(fd.get("users") || "keep"), typed = String(fd.get("confirm") || "").trim();
  if (users === "delete" && typed !== a.username) { toast(`✕ type ${a.username} to confirm deleting its users`, "bad"); return false; }
  const what = users === "delete" ? `Delete the admin ${a.username} AND its ${a.users} users? This can not be undone.` : users === "disable" ? `Switch off the ${a.users} users of ${a.username} and delete the admin?` : `Delete the admin ${a.username}? Its ${a.users} users stay on the panel without an owner.`;
  if (!await gconfirm(what, {danger: true})) return false;
  try {
    const r = await pgApi(pgAdminS.id, `/admins/${id}?users=${encodeURIComponent(users)}&confirm=${encodeURIComponent(typed)}`, {method: "DELETE"});
    toast(`✓ ${r.deleted} deleted${r.users_deleted ? ` with ${r.users_deleted} users` : ""}`, "ok"); closeModal(); pgAdminReload();
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.pgAdminUsage = id => {
  const a = pgAdminFind(id); if (!a) return;
  pgAdminS.usage = {id};
  showModal(`<b>Usage of ${esc(a.username)}</b> <span class="pill">Tehran days</span>`, `<div class="pg-admin-usage"><div class="pg-admin-urange"><label class="kv2 kf"><span>From</span><input type="date" id="pg-admin-from" value="${pgAdminTehranDay(-6)}"></label>
    <label class="kv2 kf"><span>To</span><input type="date" id="pg-admin-to" value="${pgAdminTehranDay(0)}"></label><button class="sm pri" onclick="pgAdminUsageRun()">Show</button>
    <span class="pg-admin-presets"><button class="sm" onclick="pgAdminUsagePreset(1)">Today</button><button class="sm" onclick="pgAdminUsagePreset(7)">7 days</button><button class="sm" onclick="pgAdminUsagePreset(30)">30 days</button><button class="sm" onclick="pgAdminUsagePreset(0)">This month</button></span></div>
    <div id="pg-admin-use"><div class="loading"><i class="spin"></i><span>Reading…</span></div></div><small class="mut">A day is a Tehran day: it begins at 00:00 +03:30, which is 20:30 UTC of the day before. The figures are the panel's own.</small></div>`, "wide keep");
  pgAdminUsageRun();
};
window.pgAdminUsagePreset = n => {
  const to = pgAdminTehranDay(0), from = n === 0 ? to.slice(0, 8) + "01" : pgAdminTehranDay(1 - n);
  $("#pg-admin-from").value = from; $("#pg-admin-to").value = to; pgAdminUsageRun();
};
window.pgAdminUsageRun = async () => {
  const id = (pgAdminS.usage || {}).id, from = ($("#pg-admin-from") || {}).value, to = ($("#pg-admin-to") || {}).value, out = $("#pg-admin-use"); if (!id || !out) return;
  out.innerHTML = '<div class="loading"><i class="spin"></i><span>Reading…</span></div>';
  try {
    const r = await pgApi(pgAdminS.id, `/admins/${id}/usage?from=${from}&to=${to}`), max = Math.max(1, ...r.days.map(d => d.bytes));
    out.innerHTML = `<div class="pg-admin-usum"><span class="pill ok">${fmtB(r.total)} in ${r.days.length} day${r.days.length === 1 ? "" : "s"}</span>${r.peak ? `<span class="pill">busiest ${esc(r.peak.date)} · ${fmtB(r.peak.bytes)}</span>` : ""}<span class="mut">${esc(r.from_utc.replace("T", " ").replace("Z", ""))} → ${esc(r.to_utc.replace("T", " ").replace("Z", ""))} UTC</span></div>
      <div class="lbox full pg-admin-ubox"><table class="tbl lift"><colgroup><col style="width:130px"><col style="width:120px"><col></colgroup><thead><tr><th>Day</th><th>Traffic</th><th></th></tr></thead>
      <tbody>${r.days.slice().reverse().map(d => `<tr><td class="mono">${esc(d.date)}</td><td class="pg-admin-num">${d.bytes ? fmtB(d.bytes) : '<span class="mut">0</span>'}</td><td><i class="rbar"><u style="width:${Math.round(d.bytes * 100 / max)}%"></u></i></td></tr>`).join("")}</tbody></table></div>`;
  } catch (err) { out.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
};

/* ================================================================== API KEYS (a sub-view of the Admins tab) */
const pgAdminExp = k => {
  if (!k.expire_date) return '<span class="epill none">never</span>';
  const d = pgAdminDays(k.expire_date), cls = d <= 7 ? "bad" : d <= 30 ? "warn" : "ok";
  return `<span class="pg-admin-exp" title="${esc(k.expire_date)}"><span class="epill ${cls}">${d <= 0 ? "expired" : d + " d left"}</span><small class="mut">${esc(k.expire_day)}</small></span>`;
};
function pgAdminPaintKeys(box) {
  const S = pgAdminS, K = S.K, n = S.sel.size, pill = (c, l, k = "") => `<span class="pill ${k}">${c} ${l}</span>`;
  const act = K.keys.filter(k => k.status === "active").length, bad = K.keys.length - act;
  const left = pgAdminViewSeg() + `<input type="search" class="pg-admin-q" placeholder="Search key…" value="${esc(S.kq)}" oninput="pgAdminKQ(this.value)" autocomplete="off" spellcheck="false">`
    + `<span class="pg-admin-cnt">${pill(K.keys.length, "keys")}${pill(act, "active", "ok")}${bad ? pill(bad, "off or expired", "warn") : ""}</span>`;
  const right = `<button class="sm danger pg-admin-kdel" id="pg-admin-kdel" ${n ? "" : "hidden"} onclick="pgAdminKeysDelete()">Delete ${n} selected</button><button class="sm" onclick="pgAdminReload()" title="Read the keys from the panel again">↻</button><button class="sm pri" onclick="pgAdminKeyEdit(0)">＋ New key</button>`;
  pgPaint(box, pgBar(left, right) + `<div class="lbox full pg-admin-box" id="pg-admin-list">${pgAdminKeysTable()}</div><small class="mut pg-admin-note2">A key acts as its admin (the header <code>X-Api-Key: pg_key_…</code>). The panel shows the secret of a key once, when it is made or renewed; it can not show it again.${K.connected_known ? "" : " If Nikatel itself reaches this panel with a key, do not renew or delete that key."}</small>`);
  const q = $(".pg-admin-q"); if (q && S.kq) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
function pgAdminKeysTable() {
  const S = pgAdminS, rows = S.K.keys.filter(k => nkHit([k.name, k.note, k.admin, k.trimmed, k.status].join(" "), S.kq)), used = S.K.keys.some(k => k.last_used);
  const perm = k => k.inherit ? '<span class="pill" title="It can do whatever its admin can">admin\'s rights</span>'
    : `<span class="pill ok" title="${esc(Object.entries(k.permissions).map(([r, acts]) => [r, Object.entries(acts).filter(x => x[1] !== "no").map(x => x[0] + (x[1] === "yes" ? "" : " (" + x[1] + ")"))]).filter(x => x[1].length).map(x => pgAdminLabel(x[0]) + ": " + x[1].join(", ")).join("\n"))}">own · ${k.granted}</span>`;
  return `<table class="tbl lift pg-admin-tbl"><colgroup><col style="width:34px"><col style="width:24%"><col style="width:12%"><col style="width:11%"><col style="width:14%"><col>${used ? '<col style="width:9%">' : ""}<col style="width:164px"></colgroup>
    <thead><tr><th class="pg-ck"></th><th data-prio="0">Key</th><th>Admin</th><th>Status</th><th>Expires</th><th>Rights</th>${used ? "<th>Last used</th>" : ""}<th></th></tr></thead>
    <tbody>${rows.map(k => `<tr class="${k.status === "active" ? "up" : k.status === "disabled" ? "" : "down"}"><td class="pg-ck"><input type="checkbox" class="ck pg-admin-kchk" value="${k.id}" ${S.sel.has(k.id) ? "checked" : ""} ${k.connected ? "disabled" : ""} onchange="pgAdminKSel(this)" aria-label="Tick ${esc(k.name)}"></td>
      <td class="name"><div class="pg-admin-nm"><i class="pg-admin-emo" title="${esc(k.status)}">${pgAdminEmo("🔑")}</i><b class="pg-admin-link" onclick="pgAdminKeyEdit(${k.id})" title="Edit this key">${esc(k.name)}</b>${k.connected ? '<span class="pill ok" title="Nikatel is connected to this panel with this key">used by Nikatel</span>' : ""}</div>
        <small class="mut ell" title="${esc(k.note)}"><code>${esc(k.trimmed)}</code>${k.note ? " · " + esc(k.note) : ""}</small></td>
      <td>${esc(k.admin || "#" + k.admin_id)}</td><td><span class="spill ${esc(k.status)}">${esc(k.status)}</span></td><td>${pgAdminExp(k)}</td><td>${perm(k)}</td>${used ? `<td class="mut">${k.last_used ? esc(String(k.last_used).slice(0, 10)) : "—"}</td>` : ""}
      <td class="pg-admin-acts"><button class="sm" onclick="pgAdminKeyEdit(${k.id})">Edit</button><button class="sm" ${k.connected ? "disabled" : ""} onclick="pgAdminKeyRenew(${k.id})" title="Make a new secret; the old one stops working">Renew</button><button class="sm danger" ${k.connected ? "disabled" : ""} onclick="pgAdminKeyDelete(${k.id})" title="Delete this key">✕</button></td></tr>`).join("")
      || `<tr><td colspan="${used ? 8 : 7}" class="mut">${S.K.keys.length ? "no key matches" : "this panel has no API key yet"}</td></tr>`}</tbody></table>`;
}
window.pgAdminKQ = v => { pgAdminS.kq = v.trim(); const el = $("#pg-admin-list"); if (el) el.innerHTML = pgAdminKeysTable(); };
window.pgAdminKSel = el => { const S = pgAdminS, id = +el.value; if (el.checked) S.sel.add(id); else S.sel.delete(id); const b = $("#pg-admin-kdel"); if (b) { b.hidden = !S.sel.size; b.textContent = `Delete ${S.sel.size} selected`; } };

const pgAdminPermsHtml = (view, schema, locked) => `<div class="pg-admin-perm">${schema.map(r => `<fieldset class="pg-admin-res"><legend>${esc(pgAdminLabel(r.key))}</legend>${r.actions.map(a => {
  const cur = (view[r.key] || {})[a.key] || "no", opts = a.kind === "scope" ? [["no", "no"], ["own", "own"], ["all", "all"]] : [["no", "no"], ["yes", "yes"]];
  return `<label><span>${esc(pgAdminLabel(a.key))}</span>${pgAdminSelect("", opts, cur, `data-res="${esc(r.key)}" data-act="${esc(a.key)}" onchange="this.className = this.value === 'no' ? '' : 'on'" class="${cur === "no" ? "" : "on"}" ${locked ? "disabled" : ""}`)}</label>`;
}).join("")}</fieldset>`).join("")}</div>`;
window.pgAdminKeyEdit = id => {
  const K = pgAdminS.K, k = id ? pgAdminKeyOf(id) : null; if (id && !k) return;
  const v = k || {name: "", note: "", admin_id: (K.me || {}).id, expire_day: "", inherit: true, permissions: {}, status: "active", stored_status: "active", connected: false}, lock = !!v.connected;
  const owner = (K.me || {}).is_owner, admins = (K.admins || []).map(a => [a.id, a.username]);
  const fld = (label, inner, tip = "") => `<label class="kv2 kf" title="${esc(tip)}"><span>${label}</span>${inner}</label>`;
  showModal(k ? `<b>${esc(k.name)}</b> <span class="pill"><code>${esc(k.trimmed)}</code></span>` : "<b>New API key</b>", `<form class="pg-admin-form2" onsubmit="return pgAdminKeySave(event, ${id})"><div class="pg-admin-cols"><div><div class="sbox"><h5><i>🔑</i>Key</h5>
    ${fld("Name", `<input name="name" value="${esc(v.name)}" required maxlength="128" autocomplete="off" placeholder="what it is for">`)}${fld("Note", `<input name="note" value="${esc(v.note)}" maxlength="512" autocomplete="off" placeholder="optional">`)}
    ${fld("Belongs to", admins.length && owner ? pgAdminSelect("admin_id", admins, v.admin_id, lock ? "disabled" : "") : `<b>${esc(v.admin || (K.me || {}).username || "you")}</b>`, "The key acts as this admin")}
    ${fld("Expires", `<span class="pg-admin-exprow"><input name="expire" type="date" value="${esc(v.expire_day)}" min="${pgAdminTehranDay(1)}"><span class="pg-admin-chips"><button class="sm" type="button" onclick="pgAdminKExp(this, 30)">+30 d</button><button class="sm" type="button" onclick="pgAdminKExp(this, 90)">+90 d</button><button class="sm" type="button" onclick="pgAdminKExp(this, 365)">+1 y</button><button class="sm" type="button" onclick="pgAdminKExp(this, 0)">never</button></span></span>`, "The end of that day, Tehran time; empty = never")}
    ${k ? fld("Active", pgSw(v.stored_status !== "disabled", "", `name="active" ${lock ? "disabled" : ""}`), "A key that is off is refused by the panel") : ""}</div></div>
    <div><div class="sbox"><h5><i>🛡</i>Rights</h5><div class="pg-admin-sw"><span>Use the rights of its admin</span>${pgSw(v.inherit, "pgAdminKInherit(this)", `name="inherit" ${lock ? "disabled" : ""}`)}</div>
    <div class="pg-admin-permbar" ${v.inherit ? "hidden" : ""}><button class="sm" type="button" onclick="pgAdminKPreset(this, 'none')">Clear all</button><button class="sm" type="button" onclick="pgAdminKPreset(this, 'read')">Read only</button></div>
    <small class="mut">${v.inherit ? "The key can do whatever its admin can." : "Only what is picked below; a key can never do more than its admin."}</small></div>
    ${lock ? '<div class="mut pg-admin-lockmsg">Nikatel is connected to this panel with this key: only its name, note and a later expiry can be changed here.</div>' : ""}</div></div>
    <div class="pg-admin-permwrap" ${v.inherit ? "hidden" : ""}>${pgAdminPermsHtml(v.permissions, K.permission_schema || [], lock)}</div>
    <div class="frm-act"><button class="pri" type="submit">${k ? "Save" : "Make the key"}</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide keep pg-admin-win");
};
window.pgAdminKExp = (btn, days) => { const i = btn.closest(".pg-admin-exprow").querySelector("input"); i.value = days ? new Date(Date.now() + 12600000 + days * 86400000).toISOString().slice(0, 10) : ""; };
window.pgAdminKInherit = el => { el.form.querySelectorAll(".pg-admin-permbar, .pg-admin-permwrap").forEach(x => { x.hidden = el.checked; }); const s = el.closest(".sbox").querySelector("small"); if (s) s.textContent = el.checked ? "The key can do whatever its admin can." : "Only what is picked below; a key can never do more than its admin."; };
window.pgAdminKPreset = (btn, kind) => btn.form.querySelectorAll("select[data-res]").forEach(s => {
  /* "read only": every way of reading except settings.read: the panel's answer to that holds the secrets of the settings (bot token, webhook secrets) */
  const read = ["read", "read_simple", "read_general"].includes(s.dataset.act) && !(s.dataset.res === "settings" && s.dataset.act === "read");
  const want = kind === "none" || !read ? "no" : [...s.options].some(o => o.value === "all") ? "all" : "yes";
  s.value = want; s.className = want === "no" ? "" : "on";
});
window.pgAdminKeySave = async (e, id) => {
  e.preventDefault();
  const f = e.target, fd = new FormData(f), g = n => String(fd.get(n) == null ? "" : fd.get(n)).trim(), inherit = f.inherit.checked, perms = {};
  f.querySelectorAll("select[data-res]").forEach(s => { if (s.value !== "no") (perms[s.dataset.res] = perms[s.dataset.res] || {})[s.dataset.act] = s.value; });
  const body = {name: g("name"), note: g("note"), inherit_permissions: inherit, expire: g("expire")};
  if (!inherit) body.permissions = perms;
  if (f.admin_id && !f.admin_id.disabled && g("admin_id")) body.admin_id = +g("admin_id");
  if (id) body.status = f.active.checked ? "active" : "disabled"; else if (!body.expire) delete body.expire;
  try {
    const r = await pgApi(pgAdminS.id, id ? `/admin/api-keys/${id}` : "/admin/api-keys", {method: id ? "PUT" : "POST", body});
    pgAdminS.K = null; refresh();
    if (id) { toast(`✓ ${r.key.name} saved`, "ok"); closeModal(); return false; }
    pgAdminOnce(`<b>${esc(r.key.name)}</b> is made`, `The key <b>${esc(r.key.name)}</b> (<code>${esc(r.key.trimmed)}</code>) acts as <b>${esc(r.key.admin || "its admin")}</b>.`, "API key", r.secret, "<b>Shown only once.</b> Copy it now: Nikatel does not keep it and the panel can not show it again. Send it as the header <code>X-Api-Key</code>.");
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.pgAdminKeyRenew = async id => {
  const k = pgAdminKeyOf(id); if (!k) return;
  const off = k.status === "disabled" ? " It is switched off now; renewing makes it active again." : "";
  if (!await gconfirm(`Renew the key ${k.name}? The current secret stops working at once for everything that uses it, and a new secret is shown once.${off} If Nikatel itself uses this key to reach the panel, renew it on the panel instead.`, {danger: true})) return;
  try {
    const r = await pgApi(pgAdminS.id, `/admin/api-keys/${id}/revoke`, {method: "POST"});
    pgAdminS.K = null; refresh();
    pgAdminOnce(`<b>${esc(r.key.name)}</b> is renewed`, `The key <b>${esc(r.key.name)}</b> has a new secret (<code>${esc(r.key.trimmed)}</code>); the old one no longer works.`, "New API key", r.secret, "<b>Shown only once.</b> Copy it now: Nikatel does not keep it and the panel can not show it again.");
  } catch (err) { toast("✕ " + err.message, "bad"); }
};
window.pgAdminKeyDelete = async id => {
  const k = pgAdminKeyOf(id); if (!k) return;
  if (!await gconfirm(`Delete the API key ${k.name} (${k.trimmed}) for good? Whatever uses it stops working. If Nikatel itself uses this key to reach the panel, do not delete it.`, {danger: true})) return;
  await pgDo(() => pgApi(pgAdminS.id, `/admin/api-keys/${id}`, {method: "DELETE"}), `${k.name} is deleted`, pgAdminReload);
};
window.pgAdminKeysDelete = async () => {
  const S = pgAdminS, ids = [...S.sel], names = ids.map(i => (pgAdminKeyOf(i) || {}).name).filter(Boolean); if (!ids.length) return;
  if (!await gconfirm(`Delete ${ids.length} API key${ids.length === 1 ? "" : "s"} (${names.slice(0, 5).join(", ")}${names.length > 5 ? ", …" : ""}) for good? Whatever uses them stops working.`, {danger: true})) return;
  S.sel = new Set();
  await pgDo(() => pgApi(S.id, "/admin/api-keys/bulk-delete", {method: "POST", body: {ids}}), `${ids.length} key${ids.length === 1 ? "" : "s"} deleted`, pgAdminReload);
};

/* ================================================================== SETTINGS */
pgTabs.settings = {label: "Settings", order: 70, types: ["pasarguard"], render: async (id, box) => {
  const S = pgAdminS; pgAdminEnter(id, "settings");
  if (!S.S) { pgLoading(box, "Reading the settings…"); try { await pgAdminLoad("S"); } catch (e) { pgAdminFail(e, box); return; } }
  if (!box.isConnected || S.id !== id || !S.S) return;
  if (!S.S.sections.some(s => s.key === S.sect)) S.sect = S.S.sections[0].key;
  pgAdminPaintSettings(box);
}};
const pgAdminSpec = sec => pgAdminS.S.sections.find(s => s.key === sec);
const pgAdminSecSet = sec => { const S = pgAdminS; if (!(S.clear[sec] instanceof Set)) S.clear[sec] = new Set(); return S.clear[sec]; };
const pgAdminPathGet = (obj, parts) => parts.reduce((o, k) => (o == null ? o : o[k]), obj);
function pgAdminDraftFrom(fields, vals) {      // the values for the browser as an edit form holds them: a secret is an empty text (nothing typed), a list of ints is a text, JSON is a text
  const out = {};
  for (const f of fields) {
    const v = vals ? vals[f.key] : undefined;
    if (f.kind === "secret" || f.kind === "proxy") out[f.key] = "";
    else if (f.kind === "group") out[f.key] = pgAdminDraftFrom(f.fields, v || {});
    else if (f.kind === "rows") out[f.key] = (v || []).map(r => { const row = pgAdminDraftFrom(f.cols, r); if (r._id !== undefined) row._id = r._id; row._set = Object.fromEntries(f.cols.filter(c => c.kind === "secret" || c.kind === "proxy").map(c => [c.key, !!(r[c.key] && r[c.key].set)])); return row; });
    else if (f.kind === "json") out[f.key] = JSON.stringify(v == null ? (f.type === "object" ? {} : []) : v, null, 2);
    else if (f.kind === "ints") out[f.key] = Array.isArray(v) ? v.join(", ") : "";
    else out[f.key] = v === undefined ? null : v;
  }
  return out;
}
function pgAdminDraftOf(sec) { const S = pgAdminS; if (!S.draft[sec]) S.draft[sec] = pgAdminDraftFrom(pgAdminSpec(sec).fields, S.S.values[sec] || {}); return S.draft[sec]; }
function pgAdminDirtyOf(sec) {
  const S = pgAdminS; if (!S.S || !S.draft[sec]) return false;
  return pgAdminSecSet(sec).size > 0 || JSON.stringify(S.draft[sec]) !== JSON.stringify(pgAdminDraftFrom(pgAdminSpec(sec).fields, S.S.values[sec] || {}));
}
function pgAdminValues(fields, d) {      // the draft as the API wants it (a JSON field is parsed here; the server checks everything else)
  const out = {};
  for (const f of fields) {
    const v = d[f.key];
    if (f.kind === "group") out[f.key] = pgAdminValues(f.fields, v);
    else if (f.kind === "rows") out[f.key] = v.map(r => { const row = pgAdminValues(f.cols, r); if (r._id !== undefined) row._id = r._id; return row; });
    else if (f.kind === "json") { try { out[f.key] = JSON.parse(v); } catch (e) { throw new Error(`${f.label}: this is not valid JSON`); } }
    else out[f.key] = v;
  }
  return out;
}
const pgAdminMask = (sec, parts) => pgAdminPathGet(pgAdminS.S.values[sec], parts);
const pgAdminJsonOk = t => { try { JSON.parse(t); return true; } catch (e) { return false; } };

/* one control of a field (no label); `inRow`: a cell of a table (a secret there can be replaced, not removed alone) */
function pgAdminCtl(f, v, p, sec, inRow, rowSet) {
  const at = `data-p="${esc(p)}" data-k="${f.kind}"`, ev = 'oninput="pgAdminIn(this)"';
  if (f.kind === "bool") return pgSw(!!v, "pgAdminIn(this)", at);
  if (f.kind === "int") return `<input type="number" ${at} ${ev} value="${v == null ? "" : esc(v)}" ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} step="1" placeholder="${f.nullable ? "empty = none" : ""}">`;
  if (f.kind === "text") return `<input type="text" ${at} ${ev} value="${esc(v == null ? "" : v)}" maxlength="${f.max || 300}" spellcheck="false" autocomplete="off" placeholder="${f.url ? "https://…" : ""}">`;
  if (f.kind === "ints") return `<input type="text" ${at} ${ev} value="${esc(v || "")}" placeholder="3, 7" spellcheck="false" autocomplete="off">`;
  if (f.kind === "enum") return `<select ${at} onchange="pgAdminIn(this)">${f.options.map(([o, l]) => `<option value="${esc(o)}" ${o === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  if (f.kind === "json") return `<textarea ${at} ${ev} rows="${f.type === "object" ? 3 : 6}" spellcheck="false" class="mono pg-admin-json ${pgAdminJsonOk(v) ? "" : "bad"}">${esc(v)}</textarea>`;
  if (f.kind === "secret" || f.kind === "proxy") {
    const m = inRow ? {set: rowSet} : (pgAdminMask(sec, p.split(".")) || {}), set = !!m.set, gone = !inRow && pgAdminSecSet(sec).has(p);
    return `<span class="pg-admin-secrow">${inRow ? (set ? '<span class="pill ok">set</span>' : "") : ""}<input type="password" ${at} ${ev} value="${esc(v || "")}" ${gone ? "disabled" : ""} placeholder="${gone ? "will be removed" : set ? "set — type to replace" : "not set"}" autocomplete="new-password" data-lpignore="true" data-1p-ignore spellcheck="false">${set && !inRow ? `<button type="button" class="sm" onclick="pgAdminClear('${esc(p)}')" title="${gone ? "Keep the saved value" : "Remove the saved value"}">${gone ? "Keep" : "Remove"}</button>` : ""}</span>`;
  }
  return "";
}
function pgAdminFieldHtml(f, d, path, sec) {
  const p = path ? path + "." + f.key : f.key, v = d ? d[f.key] : undefined;
  if (f.kind === "bool") return `<div class="pg-admin-sw"${f.help ? ` title="${esc(f.help)}"` : ""}><span>${esc(f.label)}</span>${pgAdminCtl(f, v, p, sec)}</div>`;
  if (f.kind === "group") {
    if (f.layout === "table") return pgAdminGroupTable(f, v, p, sec);
    const small = f.fields.length <= 7 && f.fields.every(c => c.kind === "bool");      // a few switches: the groups sit side by side
    return `<fieldset class="pg-admin-grp ${small ? "" : "pg-admin-wide"}"><legend>${esc(f.label)}</legend><div class="pg-admin-fgrid">${f.fields.map(c => pgAdminFieldHtml(c, v, p, sec)).join("")}</div></fieldset>`;
  }
  if (f.kind === "rows") return pgAdminRowsHtml(f, v || [], p, sec);
  const secret = f.kind === "secret" || f.kind === "proxy", m = secret ? (pgAdminMask(sec, p.split(".")) || {}) : null, gone = secret && pgAdminSecSet(sec).has(p);
  const tag = secret ? (gone ? '<span class="pill err">will be removed</span>' : m.set ? '<span class="pill ok">set</span>' : '<span class="pill">not set</span>') : "";
  const hint = [f.help, f.kind === "proxy" && m && m.hint ? "now: " + m.hint : ""].filter(Boolean).join(" · ");
  return `<label class="pg-admin-f${f.kind === "json" ? " pg-admin-wide" : ""}"><span class="pg-admin-fl">${esc(f.label)}${tag}</span>${pgAdminCtl(f, v, p, sec)}${hint ? `<small class="mut">${esc(hint)}</small>` : ""}</label>`;
}
function pgAdminGroupTable(f, v, p, sec) {      // a group of groups with the same fields: one row each (the channels of the notifications)
  const cols = f.fields[0].fields;
  return `<fieldset class="pg-admin-grp pg-admin-wide"><legend>${esc(f.label)}</legend><table class="tbl pg-admin-gt"><thead><tr><th>Channel</th>${cols.map(c => `<th>${esc(c.label)}</th>`).join("")}</tr></thead>
    <tbody>${f.fields.map(g => `<tr><td class="name">${esc(g.label)}</td>${g.fields.map(c => `<td>${pgAdminCtl(c, (v[g.key] || {})[c.key], p + "." + g.key + "." + c.key, sec)}</td>`).join("")}</tr>`).join("")}</tbody></table></fieldset>`;
}
function pgAdminRowsHtml(f, rows, p, sec) {
  const head = f.cols.map(c => `<th>${esc(c.label)}</th>`).join("");
  const body = rows.map((r, i) => `<tr>${f.cols.map(c => `<td>${pgAdminCtl(c, r[c.key], `${p}.${i}.${c.key}`, sec, true, !!(r._set && r._set[c.key]))}</td>`).join("")}<td class="pg-admin-rx"><button type="button" class="sm danger" title="Remove this row" onclick="pgAdminRowDel('${esc(p)}', ${i})">✕</button></td></tr>`).join("");
  return `<fieldset class="pg-admin-grp pg-admin-wide"><legend>${esc(f.label)} <small class="mut">${rows.length}</small></legend><table class="tbl pg-admin-gt"><thead><tr>${head}<th></th></tr></thead><tbody>${body || `<tr><td colspan="${f.cols.length + 1}" class="mut">none</td></tr>`}</tbody></table>
    <div class="pg-admin-rowadd"><button type="button" class="sm" onclick="pgAdminRowAdd('${esc(p)}')">＋ Add a row</button>${f.max_rows ? `<small class="mut">up to ${f.max_rows}</small>` : ""}</div></fieldset>`;
}
function pgAdminFindSpec(fields, parts) {      // the description of the field at a path (the numbers of rows are skipped)
  let cur = null, list = fields;
  for (const part of parts) {
    if (/^\d+$/.test(part)) continue;
    cur = (list || []).find(f => f.key === part); if (!cur) return null;
    list = cur.kind === "group" ? cur.fields : cur.kind === "rows" ? cur.cols : null;
  }
  return cur;
}

function pgAdminPaintSettings(box) {
  const S = pgAdminS, sec = S.sect, spec = pgAdminSpec(sec), d = pgAdminDraftOf(sec), dirty = pgAdminDirtyOf(sec);
  const tabs = pgAdminSeg([...S.S.sections.map(s => [s.key, s.label, s.key === sec, pgAdminDirtyOf(s.key) ? "dirty" : "", `pgAdminSect('${s.key}')`]), ["utpl", "User template", false, "", "pnTab('templates')"]]);
  const ic = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 00-14-4L4 9M4 4v5h5M4 13a8 8 0 0014 4l2-2M20 20v-5h-5"/></svg>';
  pgPaint(box, pgBar(tabs, `<button type="button" class="pg-admin-ib" onclick="pgAdminSetReload()" title="Read the settings from the panel again (your unsaved edits stay)" aria-label="Read the settings again">${ic}</button>`) + `<div class="card pg-admin-card fx-box">
    <div class="pg-admin-sechead"><b>${esc(spec.label)}</b><span class="mut">${esc(spec.help)}</span>${spec.confirm ? `<span class="pill warn" title="${esc(spec.confirm)}">asks first</span>` : ""}</div>
    <div class="pg-admin-formbox"><div class="pg-admin-fgrid pg-admin-top">${spec.fields.map(f => pgAdminFieldHtml(f, d, "", sec)).join("")}</div></div>
    <div class="pg-admin-foot"><button class="pri" id="pg-admin-save" ${dirty ? "" : "disabled"} onclick="pgAdminSaveSet()" title="Each section is saved on its own">Save ${esc(spec.label)}</button><button id="pg-admin-discard" ${dirty ? "" : "disabled"} onclick="pgAdminDiscard()">Discard edits</button>
      <span class="pg-admin-dirty mut">${dirty ? "unsaved edits" : ""}</span><span class="sp"></span><button onclick="pgAdminHistory()" title="Put back the values from before an earlier save">Restore previous values…</button></div></div>`);
  if (typeof fxFit === "function") fxFit();      /* the card is a box of one fixed height (the fields scroll inside it, the buttons stay at its foot) */
}
function pgAdminDraw() {      // redraw after a row was added or a secret marked: the form keeps its scroll position
  const box = $("#pn-body"); if (!box || !pgAdminS.S) return;
  const old = box.querySelector(".pg-admin-formbox"), top = old ? old.scrollTop : 0;
  pgAdminPaintSettings(box);
  const fb = box.querySelector(".pg-admin-formbox"); if (fb) fb.scrollTop = top;
}
function pgAdminMark() {      // what changes while typing: the buttons and the dot of the section, not the form
  const S = pgAdminS, dirty = pgAdminDirtyOf(S.sect);
  const sv = $("#pg-admin-save"), dc = $("#pg-admin-discard"), t = $(".pg-admin-dirty"); if (sv) sv.disabled = !dirty; if (dc) dc.disabled = !dirty; if (t) t.textContent = dirty ? "unsaved edits" : "";
  document.querySelectorAll(".pg-admin-seg button").forEach((b, i) => { if (S.S.sections[i]) b.classList.toggle("dirty", pgAdminDirtyOf(S.S.sections[i].key)); });
}
window.pgAdminSect = k => { pgAdminS.sect = k; if (pn.tab !== "settings") { pnTab("settings"); return; } pgAdminDraw(); };      // (from the user template a click on a section goes back to Settings)
window.pgSetSeg = () => {      // the row of sections that leads the bar of the user template: the sections of Settings, and "User template" lit
  const S = pgAdminS, secs = S && S.S ? S.S.sections : null;
  return pgAdminSeg([...(secs ? secs.map(s => [s.key, s.label, false, "", `pgAdminSect('${s.key}')`]) : [["settings", "Settings", false, "", "pnTab('settings')"]]), ["utpl", "User template", true, "", "pnTab('templates')"]]);
};
window.pgAdminIn = el => {
  const S = pgAdminS, parts = el.dataset.p.split("."), k = el.dataset.k, last = parts.pop(), obj = pgAdminPathGet(pgAdminDraftOf(S.sect), parts); if (!obj) return;
  obj[last] = k === "bool" ? el.checked : k === "int" ? (el.value.trim() === "" ? null : Number(el.value)) : el.value;
  if (k === "json") el.classList.toggle("bad", !pgAdminJsonOk(el.value));
  pgAdminMark();
};
window.pgAdminClear = p => { const s = pgAdminSecSet(pgAdminS.sect); if (s.has(p)) s.delete(p); else s.add(p); pgAdminDraw(); };
window.pgAdminRowAdd = p => {
  const S = pgAdminS, spec = pgAdminFindSpec(pgAdminSpec(S.sect).fields, p.split(".")), arr = pgAdminPathGet(pgAdminDraftOf(S.sect), p.split("."));
  if (!spec || !Array.isArray(arr)) return;
  if (spec.max_rows && arr.length >= spec.max_rows) { toast(`✕ at most ${spec.max_rows} rows`, "bad"); return; }
  const row = pgAdminDraftFrom(spec.cols, {}); row._set = {}; arr.push(row); pgAdminDraw();
};
window.pgAdminRowDel = (p, i) => { const arr = pgAdminPathGet(pgAdminDraftOf(pgAdminS.sect), p.split(".")); if (Array.isArray(arr)) { arr.splice(i, 1); pgAdminDraw(); } };
window.pgAdminDiscard = async () => { const S = pgAdminS; if (!await gconfirm("Throw away the edits of this section?", {danger: true})) return; S.draft[S.sect] = null; S.clear[S.sect] = new Set(); pgAdminDraw(); };
window.pgAdminSetReload = async () => { pgAdminS.S = null; await refresh(); };

const pgAdminShort = v => v == null || v === "" ? "—" : v === true ? "on" : v === false ? "off" : typeof v === "object" ? (JSON.stringify(v).length > 44 ? JSON.stringify(v).slice(0, 42) + "…" : JSON.stringify(v)) : String(v);
const pgAdminChange = c => {
  if (c.secret) return `${c.label}: ${c.secret === "set" ? "will be set" : c.secret === "removed" ? "will be removed" : "will be replaced"}`;
  if (Array.isArray(c.before) && Array.isArray(c.after) && typeof (c.before[0] || c.after[0]) === "object") { const a = c.before.length, b = c.after.length; return `${c.label}: ${a === b ? a + " rows, edited" : a + " → " + b + " rows"}`; }      // a table: how many rows, not the rows
  return `${c.label}: ${pgAdminShort(c.before)} → ${pgAdminShort(c.after)}`;
};
window.pgAdminSaveSet = async () => {
  const S = pgAdminS, sec = S.sect, spec = pgAdminSpec(sec);
  let values; try { values = pgAdminValues(spec.fields, pgAdminDraftOf(sec)); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  const body = {values, clear: [...pgAdminSecSet(sec)]};
  try {
    const dry = await pgApi(S.id, `/settings/${sec}`, {method: "PUT", body: {...body, dry_run: true}});
    if (!dry.changes.length) { toast(`Nothing to change in ${spec.label}`); return; }
    if (spec.confirm) {
      const lines = dry.changes.map(pgAdminChange);
      if (!await gconfirm(`${spec.confirm} Save ${spec.label}? ${lines.slice(0, 8).join("; ")}${lines.length > 8 ? `; and ${lines.length - 8} more` : ""}. The previous values are saved first and can be put back.`)) return;
    }
    const r = await pgApi(S.id, `/settings/${sec}`, {method: "PUT", body: {...body, dry_run: false}});
    S.draft[sec] = null; S.clear[sec] = new Set(); S.S = null;
    const warn = r.warnings && r.warnings.length ? ` — ${r.warnings.join("; ")}` : "";
    toast(`✓ ${spec.label} saved (${r.changes.length} change${r.changes.length === 1 ? "" : "s"})${warn}`, warn ? "bad" : "ok", () => pgAdminRestore(r.backup, sec, true, spec.label));
    await refresh();
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.pgAdminHistory = () => {
  const S = pgAdminS, sec = S.sect, spec = pgAdminSpec(sec), list = (S.S.backups || []).filter(b => b.section === sec);
  showModal(`<b>Previous values</b> <span class="pill">${esc(spec.label)}</span>`, list.length
    ? `<p class="pg-admin-note">Each line is the state of the settings just before a save of this section. Restoring one puts back its values for ${esc(spec.label)} — secrets are never kept in a backup, the ones that are set now stay as they are. The state before the restore is saved too.</p>
      <div class="lbox full pg-admin-hist"><table class="tbl lift"><colgroup><col><col style="width:110px"></colgroup><tbody>${list.map((b, i) => `<tr><td>${esc(when(b.at))} <small class="mut">${i === 0 ? "· the last one" : ""}</small></td><td><button class="sm" onclick="pgAdminRestore('${esc(b.name)}', '${esc(sec)}')">Restore</button></td></tr>`).join("")}</tbody></table></div>`
    : `<div class="pg-empty mut">No backup of ${esc(spec.label)} yet. One is made before every save.</div><div class="frm-act"><button type="button" onclick="closeModal()">Close</button></div>`, "narrow");
};
window.pgAdminRestore = async (name, sec, quiet, label) => {
  const what = label || (pgAdminS.S ? pgAdminSpec(sec).label : sec);
  if (!quiet && !await gconfirm(`Put the previous values of ${what} back? Only values that are not secret are put back; the current ones are saved first.`)) return;
  try {
    const r = await pgApi(pgAdminS.id, "/settings/restore", {method: "POST", body: {name, section: sec}});
    pgAdminS.draft[sec] = null; pgAdminS.clear[sec] = new Set(); pgAdminS.S = null; closeModal();
    const skip = r.skipped && r.skipped.length ? ` — not put back: ${r.skipped.join("; ")}` : "";
    toast(`✓ ${what}: the previous values are back (${r.changes.length} change${r.changes.length === 1 ? "" : "s"})${skip}`, skip ? "bad" : "ok");
    await refresh();
  } catch (e) { toast("✕ " + e.message, "bad"); }
};
