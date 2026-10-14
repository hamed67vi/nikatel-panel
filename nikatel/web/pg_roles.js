/* PasarGuard Admins tab, third view: the ROLES of the panel (what an admin may do). Draws itself when the Admins tab is on its "Roles" view (pg_admin.js hands over to pgRolesRender).
   Everything goes through /api/panels/{id}/pg/roles. The permission matrix is the one of the API keys (pgAdminPermsHtml of pg_admin.js): no / yes for most actions, no / own / all for the
   user actions. The owner role is shown but never changed; a built-in role is never deleted; a role that admins still have is not deleted (the panel's answer names them). */
const pgRolesS = {id: 0, d: null, q: "", at: 0, bk: []};
const pgRolesOf = id => ((pgRolesS.d || {}).roles || []).find(r => r.id === +id);
const pgRolesLim = [["max_users", "Most users", "", 1], ["data_limit_min_gb", "Smallest data limit", "GB", 0.1], ["data_limit_max_gb", "Largest data limit", "GB", 0.1], ["expire_min_days", "Shortest time", "days", 0.5], ["expire_max_days", "Longest time", "days", 0.5],
  ["min_hwid_per_user", "Fewest devices", "", 1], ["max_hwid_per_user", "Most devices", "", 1], ["on_hold_timeout_min_days", "Shortest on-hold", "days", 0.5], ["on_hold_timeout_max_days", "Longest on-hold", "days", 0.5]];

window.pgRolesRender = async (id, box) => {
  const S = pgRolesS; if (S.id !== id) Object.assign(S, {id, d: null, q: "", at: 0, bk: []});
  if (!S.d || Date.now() - S.at > 25000) { pgLoading(box, "Reading the roles…"); try { S.d = await pgApi(id, "/roles"); S.at = Date.now(); } catch (e) { if (box.isConnected) pgError(box, e); return; } }
  if (!box.isConnected || S.id !== id || pn.tab !== "admins") return;
  pgRolesPaint(box);
};
window.pgRolesReload = async () => { pgRolesS.d = null; refresh(); };

function pgRolesPaint(box) {
  const S = pgRolesS, d = S.d, pill = (c, l, k = "") => `<span class="pill ${k}">${c} ${l}</span>`;
  const left = pgAdminViewSeg() + `<input type="search" class="pg-admin-q" placeholder="Search role…" value="${esc(S.q)}" oninput="pgRolesQ(this.value)" autocomplete="off" spellcheck="false"><span class="pg-admin-cnt">${pill(d.roles.length, "roles")}</span>`;
  const right = `<button class="sm" onclick="pgRolesBackups()" title="Roles saved here before a change or a delete">Backups</button><button class="sm" onclick="pgRolesReload()" title="Read the roles from the panel again">↻</button><button class="sm pri" onclick="pgRolesEdit(0)">＋ Add role</button>`;
  pgPaint(box, pgBar(left, right) + `<div class="lbox full pg-admin-box" id="pg-roles-list">${pgRolesTable()}</div>
    <small class="mut pg-admin-note2">A role decides what an admin may do: its permissions, limits, the templates and groups it may use, and the device rules. Changing a role changes every admin that has it, at once. The owner role is never changed from here.</small>`);
  const q = $(".pg-admin-q"); if (q && S.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
function pgRolesSum(r) {
  const l = r.limits, bits = [l.max_users != null ? `${l.max_users} users` : "", l.data_limit_max_gb != null ? `data ≤ ${l.data_limit_max_gb} GB` : "", l.expire_max_days != null ? `time ≤ ${l.expire_max_days} d` : "", r.access.require_template ? "needs a template" : "",
    r.hwid.mode !== "use_global" ? `devices: ${r.hwid.mode}` : ""].filter(Boolean);
  return bits.join(" · ") || "no limits";
}
function pgRolesTable() {
  const S = pgRolesS, rows = S.d.roles.filter(r => nkHit([r.name, r.admins.join(" ")].join(" "), S.q));
  return `<table class="tbl lift pg-admin-tbl"><colgroup><col style="width:24%"><col style="width:20%"><col style="width:120px"><col><col style="width:180px"></colgroup>
    <thead><tr><th>Role</th><th>Admins</th><th>Permissions</th><th>Limits and rules</th><th></th></tr></thead>
    <tbody>${rows.map(r => `<tr><td class="name"><div class="pg-admin-nm"><b class="pg-admin-link" onclick="pgRolesEdit(${r.id})" title="${r.is_owner ? "See this role" : "Edit this role"}">${esc(r.name)}</b>${r.is_owner ? '<span class="pill pg-admin-own">owner</span>' : r.builtin ? '<span class="pill" title="The panel keeps this role: it cannot be deleted">built in</span>' : ""}</div></td>
      <td class="mut ell" title="${esc(r.admins.join(", "))}">${r.admins.length ? esc(r.admins.slice(0, 4).join(", ")) + (r.admins.length > 4 ? ` +${r.admins.length - 4}` : "") : "—"}</td>
      <td><span class="pill ${r.is_owner ? "ok" : ""}">${r.is_owner ? "everything" : r.granted + " allowed"}</span></td><td class="mut ell" title="${esc(pgRolesSum(r))}">${esc(pgRolesSum(r))}</td>
      <td class="pg-admin-acts"><button class="sm" onclick="pgRolesEdit(${r.id})">${r.is_owner ? "View" : "Edit"}</button><button class="sm" onclick="pgRolesEdit(${r.id}, true)" title="Make a new role as a copy of this one">Copy</button><button class="sm danger" ${r.is_owner || r.builtin ? "disabled" : ""} onclick="pgRolesDelete(${r.id})" title="Delete this role">✕</button></td></tr>`).join("") || `<tr><td colspan="5" class="mut">${S.d.roles.length ? "no role matches" : "this panel has no role"}</td></tr>`}</tbody></table>`;
}
window.pgRolesQ = v => { pgRolesS.q = v.trim(); const el = $("#pg-roles-list"); if (el) el.innerHTML = pgRolesTable(); };

/* the window: one form with the whole role */
window.pgRolesEdit = (id, copy) => {
  const S = pgRolesS, d = S.d, src = id ? pgRolesOf(id) : null; if (id && !src) return;
  const v = src ? JSON.parse(JSON.stringify(src)) : {name: "", permissions: {}, limits: {}, features: {can_use_reset_strategy: true, can_use_next_plan: true}, access: {require_template: false, allowed_template_ids: null, allowed_group_ids: null},
    hwid: {enabled: true, forced: false, require_hwid_for_manual_sub: false, fallback_limit: null, min_limit: null, max_limit: null, mode: "use_global"}, disabled_when_limited: false, disconnect_users_when_limited: true, disconnect_users_when_disabled: true, is_owner: false, admins: []};
  if (copy) { v.name = v.name + " copy"; v.is_owner = false; v.admins = []; }
  const lock = v.is_owner && !copy, edit = !!src && !copy;
  const fld = (label, inner, tip = "") => `<label class="kv2 kf" title="${esc(tip)}"><span>${label}</span>${inner}</label>`;
  const sw = (name, on, text, tip = "") => `<div class="pg-admin-sw" title="${esc(tip)}"><span>${text}</span>${pgSw(on, "", `name="${name}" ${lock ? "disabled" : ""}`)}</div>`;
  const num = (name, val, step, ph) => `<input name="${name}" type="number" min="0" step="${step}" value="${val == null ? "" : esc(val)}" placeholder="${ph}" ${lock ? "disabled" : ""}>`;
  const lim = pgRolesLim.map(([k, l, unit, step]) => fld(`${l}${unit ? ` <small>${unit}</small>` : ""}`, num("lim_" + k, v.limits[k], step, "no limit"))).join("");
  const pick = (label, items, ids, name) => {
    const all = ids == null;
    return `<div class="pg-roles-pick" data-pick="${name}"><label class="chk"><input type="checkbox" name="${name}_all" ${all ? "checked" : ""} ${lock ? "disabled" : ""} onchange="pgRolesPickAll(this)"> All ${label} <small class="mut">(no restriction)</small></label>
      <div class="pg-roles-list" ${all ? "hidden" : ""}>${items.map(x => `<label class="chk"><input type="checkbox" name="${name}" value="${x.id}" ${(ids || []).includes(x.id) ? "checked" : ""} ${lock ? "disabled" : ""}> ${esc(x.name)}</label>`).join("") || `<span class="mut">no ${label} yet</span>`}</div></div>`;
  };
  const hw = v.hwid;
  showModal(edit ? `<b>${esc(v.name)}</b> ${v.is_owner ? '<span class="pill">owner</span>' : ""}` : "<b>New role</b>", `<form class="pg-admin-form2 pg-roles-form" onsubmit="return pgRolesSave(event, ${edit ? id : 0})"><div class="pg-admin-cols"><div>
    <div class="sbox"><h5><i>🏷</i>Role</h5>${fld("Name", `<input name="name" value="${esc(v.name)}" required maxlength="64" autocomplete="off" ${lock ? "disabled" : ""} placeholder="seller">`)}
      ${sw("disabled_when_limited", v.disabled_when_limited, "Switch the admin off when it is limited", "An admin that reaches its data limit is switched off")}
      ${sw("disconnect_users_when_limited", v.disconnect_users_when_limited, "Cut its users when it is limited")}${sw("disconnect_users_when_disabled", v.disconnect_users_when_disabled, "Cut its users when it is switched off")}
      ${sw("can_use_reset_strategy", v.features.can_use_reset_strategy, "May set a reset period on users")}${sw("can_use_next_plan", v.features.can_use_next_plan, "May give users a next plan")}</div>
    <div class="sbox"><h5><i>📱</i>Devices (HWID)</h5>${fld("Rule", pgAdminSelect("hwid_mode", [["use_global", "the panel's settings"], ["override", "this role's own rules"], ["disabled", "no device rules"]], hw.mode, lock ? "disabled" : ""))}
      ${sw("hwid_enabled", hw.enabled, "Device rules on")}${sw("hwid_forced", hw.forced, "A device id is required")}${sw("hwid_manual", hw.require_hwid_for_manual_sub, "Required for manual subscriptions")}
      ${fld("Default devices", num("hwid_fallback", hw.fallback_limit, 1, "panel default"))}${fld("Fewest", num("hwid_min", hw.min_limit, 1, "no minimum"))}${fld("Most", num("hwid_max", hw.max_limit, 1, "no maximum"))}</div></div>
    <div><div class="sbox"><h5><i>📏</i>Limits <small>empty = no limit</small></h5><div class="pg-roles-lim">${lim}</div></div>
    <div class="sbox"><h5><i>🔓</i>Access</h5>${sw("require_template", v.access.require_template, "Users must be made from a template")}
      <div class="pg-roles-h">Templates it may use</div>${pick("templates", d.templates || [], v.access.allowed_template_ids, "tpl")}<div class="pg-roles-h">Groups it may give</div>${pick("groups", d.groups || [], v.access.allowed_group_ids, "grp")}</div></div></div>
    <div class="sbox"><h5><i>🛡</i>Permissions</h5><div class="pg-admin-permbar" ${lock ? "hidden" : ""}><button class="sm" type="button" onclick="pgAdminKPreset(this, 'none')">Clear all</button><button class="sm" type="button" onclick="pgAdminKPreset(this, 'read')">Read only</button></div>
      ${lock ? '<small class="mut">The owner role can do everything; its list is not shown and cannot be changed.</small>' : ""}</div>
    <div class="pg-admin-permwrap" ${lock ? "hidden" : ""}>${pgAdminPermsHtml(v.permissions, d.permission_schema || [], false)}</div>
    ${v.admins.length ? `<small class="mut">Admins with this role: ${esc(v.admins.join(", "))}. A change reaches them at once.</small>` : ""}
    <div class="frm-act">${lock ? "" : `<button class="pri" type="submit">${edit ? "Save" : "Make the role"}</button>`}<button type="button" onclick="closeModal()">${lock ? "Close" : "Cancel"}</button></div></form>`, "wide keep pg-admin-win");
};
window.pgRolesPickAll = el => { const box = el.closest(".pg-roles-pick"); box.querySelector(".pg-roles-list").hidden = el.checked; };
function pgRolesBody(f) {
  const n = k => { const el = f.elements[k]; return !el || el.value === "" ? null : +el.value; }, on = k => !!(f.elements[k] && f.elements[k].checked), perms = {};
  f.querySelectorAll("select[data-res]").forEach(s => { if (s.value !== "no") (perms[s.dataset.res] = perms[s.dataset.res] || {})[s.dataset.act] = s.value; });
  const ids = (name) => on(name + "_all") ? null : [...f.querySelectorAll(`input[name="${name}"]:checked`)].map(i => +i.value);
  return {name: f.elements.name.value.trim(), permissions: perms, limits: Object.fromEntries(pgRolesLim.map(([k]) => [k, n("lim_" + k)])),
    features: {can_use_reset_strategy: on("can_use_reset_strategy"), can_use_next_plan: on("can_use_next_plan")}, access: {require_template: on("require_template"), allowed_template_ids: ids("tpl"), allowed_group_ids: ids("grp")},
    hwid: {mode: f.elements.hwid_mode.value, enabled: on("hwid_enabled"), forced: on("hwid_forced"), require_hwid_for_manual_sub: on("hwid_manual"), fallback_limit: n("hwid_fallback"), min_limit: n("hwid_min"), max_limit: n("hwid_max")},
    disabled_when_limited: on("disabled_when_limited"), disconnect_users_when_limited: on("disconnect_users_when_limited"), disconnect_users_when_disabled: on("disconnect_users_when_disabled")};
}
window.pgRolesSave = async (e, id) => {
  e.preventDefault(); const f = e.target, body = pgRolesBody(f);
  try {
    const r = await pgApi(pgRolesS.id, id ? `/roles/${id}` : "/roles", {method: id ? "PUT" : "POST", body});
    pgRolesS.d = null; closeModal(); toast(id ? `✓ ${body.name} saved${r.changed ? " (" + r.changed.join(", ") + ")" : ""}` : `✓ Role ${body.name} made`, "ok"); refresh();
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.pgRolesDelete = async id => {
  const r = pgRolesOf(id); if (!r) return;
  if (r.admins.length) { toast(`✕ ${r.admins.length} admin${r.admins.length === 1 ? " has" : "s have"} the role ${r.name} (${r.admins.slice(0, 4).join(", ")}): give them another role first`, "bad"); return; }
  if (!await gconfirm(`Delete the role ${r.name}? The panel cannot undo it; the role is saved here first (Backups can make it again).`, {danger: true})) return;
  await pgDo(() => pgApi(pgRolesS.id, `/roles/${id}?confirm=true`, {method: "DELETE"}), `${r.name} deleted (saved in Backups)`, () => { pgRolesS.d = null; return refresh(); });
};
window.pgRolesBackups = async () => {
  const draw = body => showModal("<b>Backups of the roles</b>", body, "wide");
  draw('<div class="loading"><i class="spin"></i><span>Reading…</span></div>');
  try {
    const r = await pgApi(pgRolesS.id, "/roles/backups"); pgRolesS.bk = r.backups;
    draw(`<div class="lbox full pg-admin-box"><table class="tbl lift"><colgroup><col style="width:200px"><col><col style="width:200px"><col style="width:120px"></colgroup><thead><tr><th>When</th><th>Role</th><th>Why</th><th></th></tr></thead><tbody>${r.backups.map((b, i) => `<tr><td class="mut">${b.at ? esc(new Date(b.at * 1000).toLocaleString()) : "—"}</td><td class="name">${esc(b.name || "#" + b.role_id)}</td><td class="mut">${esc(b.why)}</td><td><button class="sm" onclick="pgRolesRestore(${i})">Restore…</button></td></tr>`).join("") || '<tr><td colspan="4" class="mut">nothing saved yet: a role is saved before it is changed or deleted</td></tr>'}</tbody></table></div>
      <small class="mut">Restoring writes the role as it was, over the role that has its id now - or makes it again (with a new id) when it was deleted. The state before a restore is saved too.</small>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
window.pgRolesRestore = async i => {
  const b = (pgRolesS.bk || [])[i]; if (!b) return;
  if (!await gconfirm(`Put the role ${b.name || "#" + b.role_id} back as it was ${b.at ? new Date(b.at * 1000).toLocaleString() : ""}? It replaces the role that has this id now (saved first), or is made again when it was deleted. Admins that have it get the old rules at once.`, {danger: true})) return;
  try { const r = await pgApi(pgRolesS.id, `/roles/restore/${encodeURIComponent(b.file)}`, {method: "POST"}); closeModal(); toast(r.made_again ? `✓ ${r.role.name} made again` : `✓ ${r.role.name} put back`, "ok"); pgRolesS.d = null; refresh(); }
  catch (e) { toast("✕ " + e.message, "bad"); }
};
