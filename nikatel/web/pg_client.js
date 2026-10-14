/* PasarGuard Templates tab, second view: the CLIENT templates (what the subscription answers are built from: Clash YAML, Xray or sing-box JSON, the User-Agent lists).
   The Templates tab (pg_access.js) shows a "User templates | Client templates" switch and hands over to pgCtplRender when "Client templates" is on. Everything goes through
   /api/panels/{id}/pg/client-templates; the panel checks the content (Jinja, then YAML / JSON) and its sentence is shown as it comes. A system template and the last template of a
   type are never deleted; the default of a type is moved, never switched off. A change and a delete save the template here first (Backups). */
const pgCtpl = {id: 0, on: false, d: null, q: "", at: 0, bk: []};
const pgCtplOf = id => ((pgCtpl.d || {}).templates || []).find(t => t.id === +id);
const pgCtplHints = {
  clash_subscription: "A YAML template (Jinja is allowed). The panel renders it and reads the result as YAML.",
  xray_subscription: "A JSON template with at least one inbound and one outbound. Jinja is allowed; the panel renders it and reads the result as JSON.",
  singbox_subscription: "A JSON template with at least one inbound and one outbound. Jinja is allowed; the panel renders it and reads the result as JSON.",
  user_agent: 'A JSON object with a list of User-Agent strings: {"list": ["Mozilla/5.0 …"]}.',
  grpc_user_agent: 'A JSON object with a list of User-Agent strings for gRPC: {"list": ["grpc-go/1.60 …"]}.',
};
window.pgCtplView = () => pgCtpl.on;
window.pgCtplSet = on => { pgCtpl.on = !!on; refresh(); };
window.pgCtplSeg = () => `<div class="pg-admin-seg"><button type="button" class="${pgCtpl.on ? "" : "on"}" onclick="pgCtplSet(false)">User templates</button><button type="button" class="${pgCtpl.on ? "on" : ""}" onclick="pgCtplSet(true)">Client templates</button></div>`;

window.pgCtplRender = async (id, box) => {
  const S = pgCtpl; if (S.id !== id) Object.assign(S, {id, d: null, q: "", at: 0, bk: []});
  if (!S.d || Date.now() - S.at > 25000) { pgLoading(box, "Reading the client templates…"); try { S.d = await pgApi(id, "/client-templates"); S.at = Date.now(); } catch (e) { if (box.isConnected) pgError(box, e); return; } }
  if (!box.isConnected || S.id !== id || pn.tab !== "templates") return;
  pgCtplPaint(box);
};
window.pgCtplReload = () => { pgCtpl.d = null; refresh(); };

function pgCtplPaint(box) {
  const S = pgCtpl, d = S.d;
  const left = pgCtplSeg() + `<input type="search" class="pg-admin-q" placeholder="Search name or type…" value="${esc(S.q)}" oninput="pgCtplQ(this.value)" autocomplete="off" spellcheck="false"><span class="pg-admin-cnt"><span class="pill">${d.total} templates</span></span>`;
  const right = `<button class="sm" onclick="pgCtplBackups()" title="Templates saved here before a change or a delete">Backups</button><button class="sm" onclick="pgCtplReload()" title="Read the templates from the panel again">↻</button><button class="sm pri" onclick="pgCtplEdit(0)">＋ New client template</button>`;
  pgPaint(box, pgBar(left, right) + `<div class="lbox full pg-admin-box" id="pg-ctpl-list">${pgCtplTable()}</div>
    <small class="mut pg-admin-note2">A client template is what a subscription link answers with for one kind of client. Every type has one DEFAULT; a host can use another template of the type for itself (Hosts). The templates the panel came with (system) can be edited but not deleted.</small>`);
  const q = $(".pg-admin-q"); if (q && S.q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
}
function pgCtplTable() {
  const S = pgCtpl, order = Object.fromEntries(S.d.types.map((t, i) => [t.key, i]));
  const rows = S.d.templates.filter(t => nkHit([t.name, t.label, t.type].join(" "), S.q)).sort((a, b) => (order[a.type] ?? 9) - (order[b.type] ?? 9) || b.default - a.default || a.name.localeCompare(b.name));
  return `<table class="tbl lift pg-admin-tbl"><colgroup><col style="width:28%"><col style="width:22%"><col style="width:110px"><col><col style="width:250px"></colgroup>
    <thead><tr><th>Template</th><th>Type</th><th>Default</th><th>Size</th><th></th></tr></thead>
    <tbody>${rows.map(t => `<tr class="${t.default ? "up" : ""}"><td class="name"><div class="pg-admin-nm"><b class="pg-admin-link" onclick="pgCtplEdit(${t.id})" title="Edit this template">${esc(t.name)}</b>${t.system ? '<span class="pill" title="Made by the panel: it can be edited but not deleted">system</span>' : ""}</div></td>
      <td><span class="td-type">${esc(t.label)}</span></td><td>${t.default ? '<span class="pill ok">default</span>' : `<button class="sm" onclick="pgCtplDefault(${t.id})" title="Make this the template that is used for ${esc(t.label)} clients">Make default</button>`}</td>
      <td class="mut">${t.size.toLocaleString()} characters · ${t.lines} lines</td>
      <td class="pg-admin-acts"><button class="sm" onclick="pgCtplEdit(${t.id})">Edit</button><button class="sm" onclick="pgCtplEdit(${t.id}, true)" title="Make a new template as a copy of this one">Copy</button><button class="sm danger" ${t.system ? "disabled" : ""} onclick="pgCtplDelete(${t.id})" title="${t.system ? "A system template is never deleted" : "Delete this template"}">✕</button></td></tr>`).join("") || `<tr><td colspan="5" class="mut">${S.d.templates.length ? "no template matches" : "this panel has no client template"}</td></tr>`}</tbody></table>`;
}
window.pgCtplQ = v => { pgCtpl.q = v.trim(); const el = $("#pg-ctpl-list"); if (el) el.innerHTML = pgCtplTable(); };

/* the window: a name, the type (fixed after it is made), the content, and whether it is the default of its type */
window.pgCtplEdit = async (id, copy) => {
  const S = pgCtpl, src = id ? pgCtplOf(id) : null; if (id && !src) return;
  let v = {name: "", type: "xray_subscription", content: "", default: false};
  if (src) { try { v = Object.assign({}, (await pgApi(S.id, `/client-templates/${id}`)).template); } catch (e) { toast("✕ " + e.message, "bad"); return; } }
  if (copy) { v.name = v.name + " copy"; v.default = false; v.system = false; }
  const edit = !!src && !copy, fld = (label, inner) => `<label class="kv2 kf"><span>${label}</span>${inner}</label>`;
  showModal(edit ? `<b>${esc(v.name)}</b> <span class="td-type">${esc(v.label)}</span>` : "<b>New client template</b>", `<form class="pg-admin-form2 pg-ctpl-form" onsubmit="return pgCtplSave(event, ${edit ? id : 0})">
    <div class="sbox"><h5><i>🧩</i>Template</h5>${fld("Name", `<input name="name" value="${esc(v.name)}" required maxlength="64" autocomplete="off">`)}
      ${fld("Type", edit ? `<b>${esc(v.label)}</b>` : pgAdminSelect("type", S.d.types.map(t => [t.key, t.label]), v.type, 'onchange="pgCtplHint(this)"'))}
      <div class="pg-admin-sw"><span>The default of its type</span>${pgSw(v.default, "", `name="is_default" ${edit && v.default ? 'disabled title="Make another template the default instead"' : ""}`)}</div>
      <small class="mut" id="pg-ctpl-hint">${esc(pgCtplHints[v.type] || "")}</small></div>
    <label class="pg-ctpl-ta">Content<textarea name="content" rows="22" spellcheck="false" required>${esc(v.content)}</textarea></label>
    <div class="frm-act"><button class="pri" type="submit">${edit ? "Save" : "Make the template"}</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "wide keep pg-admin-win");
};
window.pgCtplHint = sel => { const h = $("#pg-ctpl-hint"); if (h) h.textContent = pgCtplHints[sel.value] || ""; };
window.pgCtplSave = async (e, id) => {
  e.preventDefault(); const f = e.target, body = {name: f.elements.name.value.trim(), content: f.elements.content.value};
  if (!id) body.template_type = f.elements.type.value;
  if (f.elements.is_default && !f.elements.is_default.disabled) body.is_default = f.elements.is_default.checked;
  try {
    const r = await pgApi(pgCtpl.id, id ? `/client-templates/${id}` : "/client-templates", {method: id ? "PUT" : "POST", body});
    pgCtpl.d = null; closeModal(); toast(id ? `✓ ${body.name} saved (${r.changed.join(", ")})` : `✓ ${body.name} made`, "ok"); refresh();
  } catch (err) { toast("✕ " + err.message, "bad"); }
  return false;
};
window.pgCtplDefault = async id => {
  const t = pgCtplOf(id); if (!t) return;
  if (!await gconfirm(`Make “${t.name}” the default ${t.label} template? Every ${t.label} subscription that does not name a template of its own uses it from now on.`)) return;
  await pgDo(() => pgApi(pgCtpl.id, `/client-templates/${id}`, {method: "PUT", body: {is_default: true}}), `${t.name} is the default now`, () => { pgCtpl.d = null; return refresh(); });
};
window.pgCtplDelete = async id => {
  const t = pgCtplOf(id); if (!t) return;
  if (!await gconfirm(`Delete the template “${t.name}” (${t.label})?${t.default ? " It is the default of its type: another template of the type becomes the default." : ""} A host that used it falls back to the default. The panel cannot undo it; the template is saved here first (Backups can make it again).`, {danger: true})) return;
  await pgDo(() => pgApi(pgCtpl.id, `/client-templates/${id}?confirm=true`, {method: "DELETE"}), `${t.name} deleted (saved in Backups)`, () => { pgCtpl.d = null; return refresh(); });
};
window.pgCtplBackups = async () => {
  const draw = body => showModal("<b>Backups of the client templates</b>", body, "wide");
  draw('<div class="loading"><i class="spin"></i><span>Reading…</span></div>');
  try {
    const r = await pgApi(pgCtpl.id, "/client-templates/backups"); pgCtpl.bk = r.backups;
    draw(`<div class="lbox full pg-admin-box"><table class="tbl lift"><colgroup><col style="width:190px"><col><col style="width:150px"><col style="width:190px"><col style="width:110px"></colgroup><thead><tr><th>When</th><th>Template</th><th>Type</th><th>Why</th><th></th></tr></thead><tbody>${r.backups.map((b, i) => `<tr><td class="mut">${b.at ? esc(new Date(b.at * 1000).toLocaleString()) : "—"}</td><td class="name">${esc(b.name || "#" + b.id)}</td><td class="mut">${esc(b.type)}</td><td class="mut">${esc(b.why)}</td><td><button class="sm" onclick="pgCtplRestore(${i})">Restore…</button></td></tr>`).join("") || '<tr><td colspan="5" class="mut">nothing saved yet: a template is saved before it is changed or deleted</td></tr>'}</tbody></table></div>
      <small class="mut">Restoring writes the name and the content as they were, over the template that has its id now - or makes it again (with a new id) when it was deleted. The state before a restore is saved too.</small>`);
  } catch (e) { draw(`<div class="err">${esc(e.message)}</div>`); }
};
window.pgCtplRestore = async i => {
  const b = (pgCtpl.bk || [])[i]; if (!b) return;
  if (!await gconfirm(`Put “${b.name || "#" + b.id}” back as it was ${b.at ? new Date(b.at * 1000).toLocaleString() : ""}? It replaces the template that has this id now (saved first), or is made again when it was deleted.`, {danger: true})) return;
  try { const r = await pgApi(pgCtpl.id, `/client-templates/restore/${encodeURIComponent(b.file)}`, {method: "POST"}); closeModal(); toast(r.made_again ? `✓ ${r.template.name} made again` : `✓ ${r.template.name} put back`, "ok"); pgCtpl.d = null; refresh(); }
  catch (e) { toast("✕ " + e.message, "bad"); }
};
