/* PasarGuard Users: the window of ONE user (app.js hands over to pgUserOpen for a PasarGuard panel) and the tools of the Users list (the "More…" menu).
   The window: usage, the account (status, data limit, reset, end date or ON HOLD days, devices limit, owner, note), the groups, the NEXT PLAN, and the activity (devices, the last
   fetches of the subscription, what the subscription answers in each format, where the user is online now). Only what differs is sent; a next plan the user has goes back with every save (the panel deletes it otherwise).
   The tools: for the ticked users (owner, template, reset, new links, delete) and for many (shift end dates or data limits, or set the Shadowsocks method, by a filter - counted first -, clean up expired / limited /
   on hold / disabled users, new users from a template). Everything goes through /api/panels/{id}/pg/user and /pg/user-tools. */
const pgU = {id: 0, d: null, act: "devices", cache: {}, fmt: "links"};
const pgUMs = v => typeof v === "number" ? v * 1000 : Date.parse(v || "") || 0;
const pgUAgo = v => { const t = pgUMs(v); return t ? ago(t / 1000) + " ago" : "never"; };
const pgUWhen = v => { const t = pgUMs(v); return t ? new Date(t).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}) : "—"; };
const pgUEnc = () => encodeURIComponent(pgU.d.user.username);

/* ---------------------------------------------------------------- the window of one user */
window.pgUserOpen = async (id, name) => {
  let d; try { d = await pgApi(id, `/user/${encodeURIComponent(name)}`); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  Object.assign(pgU, {id, d, act: "devices", cache: {}});
  pgUserShow(); pgUserAct("devices");
};
async function pgUReload() {
  try { pgU.d = await pgApi(pgU.id, `/user/${pgUEnc()}`); pgU.cache = {}; pgUserShow(); pgUserAct(pgU.act); } catch (e) { toast("✕ " + e.message, "bad"); }
  pn.users = null; refresh();
}
const pgUPlanOf = u => { const p = u.next_plan || {}; return {template_id: p.template_id || null, data_gb: p.data_gb ?? null, expire_days: p.expire_days ?? null, add_remaining: !!p.add_remaining}; };

function pgUserShow() {      // the window of one user, made tight (1.47.0: the owner asked for it rebuilt and compact): the buttons are in the header, the account is two columns of fields, the usage beside it, the plan and the groups in one row
  const {user: u, options: o} = pgU.d, nm = esc(u.username), lim = u.limit || 0, used = u.used || 0, pct = lim ? Math.min(100, Math.round(used * 100 / lim)) : 0;
  const em = pgUMs(u.expire), left = em ? Math.ceil((em - Date.now()) / 86400000) : null, hold = u.status === "on_hold", off = u.status === "disabled", np = u.next_plan || {};
  const f = (label, inner, id = "", hid = false, cls = "", tip = "") => `<label class="pu-f ${cls}"${id ? ` id="${id}"` : ""}${hid ? " hidden" : ""}${tip ? ` title="${esc(tip)}"` : ""}><span>${label}</span>${inner}</label>`;
  const kv = (l, v) => `<div class="pu-kv"><span>${l}</span><b>${v}</b></div>`;
  const b = (svg, title, tip, click, cls = "") => `<button type="button" class="sm ${cls}" onclick="${click}" title="${esc(tip)}" aria-label="${esc(title)}"><i class="bi">${svg}</i><span class="bt">${title}</span></button>`;
  const sel = (name, opts, cur, attrs = "") => `<select name="${name}" ${attrs}>${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const sub = u.sub || "", odd = !["active", "disabled", "on_hold"].includes(u.status);
  const admins = o.admins.map(a => a.name); if (u.admin && !admins.includes(u.admin)) admins.unshift(u.admin);
  const pause = '<svg viewBox="0 0 24 24"><path d="M9 5v14M15 5v14"/></svg>', skip = '<svg viewBox="0 0 24 24"><path d="M5 5l9 7-9 7zM18 5v14"/></svg>';
  const lens = '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M16 16l4 4"/></svg>';
  const acts = `${b(lens, "Investigate", "Why the data went fast: usage by hour and server, devices, subscription fetches and a plain verdict", "pgUserInvestigate()")}${b(SI.reset, "Reset usage", "Set the used traffic back to zero", "pgUserDo('reset')")}${b(SI.key, "New link", "Revoke the subscription: a new link, the old one stops working", "pgUserDo('revoke_sub')")}${b(off ? SI.play : pause, off ? "Enable" : "Disable", off ? "Let the user connect again" : "Stop the user from connecting", `pgUserDo('${off ? "enable" : "disable"}')`)}`
    + `${u.next_plan ? b(skip, "Next plan", "Start the next plan now: its data and duration replace the current ones", "pgUserDo('active_next')") : ""}${b(SI.trash, "Delete", "Remove the user from the panel", "pgUserDo('delete')", "danger")}<i class="mh-sep"></i>`;
  const usage = `<div class="pu-big"><b>${fmtB(used)}</b><span>${lim ? "of " + gb(lim) + " · " + pct + " %" : "unlimited"}</span></div>${lim ? `<div class="pu-bar"><i class="${pct >= 95 ? "bad" : pct >= 80 ? "warn" : ""}" style="width:${pct}%"></i></div>` : ""}`
    + kv("Left", lim ? fmtB(Math.max(0, lim - used)) : "—")
    + kv("Expires", hold ? `after its first connection${u.on_hold_days ? ` <small class="mut">+${u.on_hold_days} d</small>` : ""}` : u.expire_day ? `${esc(u.expire_day)} <small class="${left < 0 ? "err" : "mut"}">${left < 0 ? "expired" : left + " d"}</small>` : "never")
    + kv("Online", esc(pgUAgo(u.online_at)))
    + `<small class="mut pu-meta">Lifetime ${fmtB(u.lifetime || 0)} · created ${esc(pgUWhen(u.created_at))}${u.edit_at ? " · edited " + esc(pgUAgo(u.edit_at)) : ""}</small>`;
  const account = f("Status", sel("status", [...(odd ? [[u.status, u.status + " (by the panel)"]] : []), ["active", "active"], ["disabled", "disabled"], ["on_hold", "on hold"]], u.status, `onchange="pgUserSt(this)"`))
    + f("Data limit", `<input name="limit_gb" type="number" step="0.1" min="0" value="${u.limit_gb ?? 0}"><em>GB</em>`, "", false, "", "GB, 0 = unlimited")
    + f("Reset", sel("reset", ["no_reset", "day", "week", "month", "year"].map(s => [s, s]), u.reset), "", false, "", "How often the used traffic is set back to zero")
    + f("Expires", `<input name="expire_day" type="date" value="${esc(u.expire_day)}">`, "pgu-f-exp", hold, "", "empty = never")
    + f("On hold for", `<input name="on_hold_days" type="number" step="0.5" min="0" value="${u.on_hold_days ?? ""}" placeholder="days"><em>days</em>`, "pgu-f-hold", !hold, "", "After its first connection the user stays valid for this many days")
    + f("Starts anyway", `<input name="on_hold_timeout_day" type="date" value="${esc(u.on_hold_timeout_day)}">`, "pgu-f-tmo", !hold, "", "A user on hold that has not connected by this day starts counting anyway. Empty = it waits")
    + f("Devices", `<input name="hwid_limit" type="number" step="1" min="0" value="${u.hwid_limit ?? ""}" placeholder="panel rule">`, "", false, "", "How many devices may use this user. Empty = the rule of the panel")
    + (admins.length ? f("Owner", sel("owner", admins.map(a => [a, a]), u.admin)) : "")
    + f("Note", `<input name="note" maxlength="500" value="${esc(u.note)}" placeholder="optional">`, "", false, "wide")
    + (sub ? `<div class="pu-f wide"><span>Link</span><span class="mono ell pu-link" title="Click to copy the subscription link" onclick="copyFrom(this, '${esc(sub)}')">${esc(sub)}</span></div>` : "");
  const plan = f("Template", sel("np_tpl", [["", "own values"], ...o.templates.map(t => [t.id, t.name])], np.template_id || "", `onchange="pgUserNp(this)"`))
    + `<label class="pu-f" title="Add what is left of the current data to the new plan"><span>Add the rest</span>${pgSw(!!np.add_remaining, "", 'name="np_add"')}</label>`
    + f("Data", `<input name="np_gb" type="number" step="0.1" min="0" value="${np.data_gb ?? ""}" placeholder="no limit"><em>GB</em>`, "pgu-f-npg", !!np.template_id, "", "GB; 0 or empty = no data limit")
    + f("Duration", `<input name="np_days" type="number" step="1" min="0" value="${np.expire_days ?? ""}" placeholder="no end"><em>days</em>`, "pgu-f-npd", !!np.template_id, "", "days; empty = no end date");
  const groups = o.groups.map(g => `<label class="pu-chip" title="${esc(g.name)}"><input type="checkbox" name="grp" value="${g.id}" ${u.group_ids.includes(g.id) ? "checked" : ""} onchange="pgUserGn()"><span>${esc(g.name)}</span></label>`).join("") || '<span class="mut">this panel has no group</span>';
  const act = `<section class="pu-box pgu-act"><h5>Activity<span class="sp"></span><span class="pg-admin-seg">${[["devices", "Devices"], ["subs", "Subscription"], ["format", "Formats"], ["online", "Online now"]].map(([k, l]) => `<button type="button" class="${pgU.act === k ? "on" : ""}" data-k="${k}" onclick="pgUserAct('${k}')">${l}</button>`).join("")}</span></h5><div id="pgu-actbody"></div></section>`;
  showModal(`<span class="st ${u.status === "active" ? "online" : u.status === "disabled" ? "" : "offline"}"></span><b>${nm}</b> <span class="pill">${esc(String(u.status).replace("_", " "))}</span>${u.admin ? ` <span class="pill" title="The admin that owns this user">${esc(u.admin)}</span>` : ""}`,
    `<form class="frm pu-form" onsubmit="return pgUserSave(event)">
      <div class="pu-top"><section class="pu-box"><h5>Account</h5><div class="pu-grid">${account}</div></section><section class="pu-box pu-use"><h5>Usage</h5>${usage}</section></div>
      <div class="pu-mid"><section class="pu-box"><h5 title="Starts by itself when the user runs out of data or time (or with “Next plan” above). Everything empty = no next plan.">Next plan</h5><div class="pu-grid">${plan}</div></section>
        <section class="pu-box"><h5 title="The groups decide which inbounds (and so which hosts) the user gets.">Groups <small class="mut pgu-gn"></small></h5><div class="pu-chips">${groups}</div></section></div>
      ${act}
      <div class="frm-act pu-save"><button type="button" onclick="closeModal()">Cancel</button><button class="pri" type="submit">Save</button></div></form>`, "wide ue pgu", acts);
  pgUserGn();
}
window.pgUserGn = () => { const xs = [...document.querySelectorAll('#modal input[name="grp"]')], c = document.querySelector("#modal .pgu-gn"); if (c) c.textContent = xs.length ? `${xs.filter(x => x.checked).length} of ${xs.length}` : ""; };
window.pgUserSt = sel => { const hold = sel.value === "on_hold"; [["pgu-f-exp", hold], ["pgu-f-hold", !hold], ["pgu-f-tmo", !hold]].forEach(([i, h]) => { const el = $("#" + i); if (el) el.hidden = h; }); };
window.pgUserNp = sel => { const own = !sel.value; ["pgu-f-npg", "pgu-f-npd"].forEach(i => { const el = $("#" + i); if (el) el.hidden = !own; }); };

window.pgUserSave = async e => {
  e.preventDefault();
  const form = e.target, f = form.elements, u = pgU.d.user, enc = pgUEnc(), body = {}, did = [];
  body.status = f.status.value; body.limit_gb = f.limit_gb.value === "" ? 0 : +f.limit_gb.value; body.reset = f.reset.value; body.note = f.note.value;
  body.group_ids = [...form.querySelectorAll('input[name="grp"]:checked')].map(x => +x.value);
  body.expire_day = body.status === "on_hold" ? "" : f.expire_day.value;
  if (body.status === "on_hold") { body.on_hold_days = f.on_hold_days.value === "" ? null : +f.on_hold_days.value; body.on_hold_timeout_day = f.on_hold_timeout_day.value; }
  if (f.hwid_limit.value !== "") body.hwid_limit = +f.hwid_limit.value; else if (u.hwid_limit != null) body.hwid_limit = null;      // (the panel answers that a limit is changed, not taken away)
  const plan = {template_id: f.np_tpl.value ? +f.np_tpl.value : null, data_gb: f.np_gb.value === "" ? null : +f.np_gb.value, expire_days: f.np_days.value === "" ? null : +f.np_days.value, add_remaining: f.np_add.checked};
  if (plan.template_id) { plan.data_gb = null; plan.expire_days = null; }
  const was = pgUPlanOf(u); if (was.template_id) { was.data_gb = null; was.expire_days = null; }
  if (JSON.stringify(plan) !== JSON.stringify(was)) body.next_plan = plan.template_id || plan.data_gb !== null || plan.expire_days !== null ? plan : null;      // an empty plan = none wanted
  const btn = form.querySelector("button[type=submit]"); btn.disabled = true;
  try {
    if (f.owner && f.owner.value && f.owner.value !== u.admin) { await pgApi(pgU.id, `/user/${enc}/action`, {method: "POST", body: {what: "set_owner", value: f.owner.value}}); did.push("owner"); }
    try { const r = await pgApi(pgU.id, `/user/${enc}`, {method: "PUT", body}); did.push(...r.changed); }
    catch (err) { if (!/nothing to change/i.test(err.message)) throw err; }
    if (!did.length) { toast("Nothing changed"); btn.disabled = false; return false; }
    toast(`✓ ${u.username} saved (${did.map(x => x.replace(/_/g, " ")).join(", ")})`, "ok"); pn.users = null; closeModal(); refresh();
  } catch (err) { toast("✕ " + err.message, "bad"); btn.disabled = false; if (did.length) pgUReload(); }
  return false;
};

window.pgUserDo = async what => {
  const nm = pgU.d.user.username, ask = {reset: `Set the used traffic of ${nm} back to zero?${pgU.d.user.next_plan ? " The panel also removes the next plan of the user when its usage is reset." : ""}`, revoke_sub: `Make a new subscription link for ${nm}? The old link stops working.`,
    active_next: `Start the next plan of ${nm} now? Its data and duration replace the current ones and the used traffic starts again from zero.`, delete: `Delete ${nm} from the panel? This cannot be undone.`}[what];
  if (ask && !await gconfirm(ask, {danger: what === "delete" || what === "revoke_sub"})) return;
  try {
    await pgApi(pgU.id, `/user/${pgUEnc()}/action`, {method: "POST", body: {what, confirm: what === "delete"}});
    toast(`✓ ${nm}: ${what.replace("_", " ")} done`, "ok"); pn.users = null;
    if (what === "delete") { closeModal(); refresh(); } else await pgUReload();
  } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* the activity box: devices, the last fetches of the subscription, where the user is online now (each read the first time it is opened) */
const pgUDevRow = d => [d.model, d.os && (d.os + (d.version ? " " + d.version : ""))].filter(Boolean).join(" · ") || "unknown device";
window.pgUserAct = async k => {
  pgU.act = k; document.querySelectorAll("#modal .pgu-act .pg-admin-seg button").forEach(x => x.classList.toggle("on", x.dataset.k === k));
  const box = $("#pgu-actbody"); if (!box) return;
  if (k === "format") { box.innerHTML = pgUFmtHtml(); return pgUserFmtLoad(pgU.fmt); }
  const path = {devices: "devices", subs: "sub-updates", online: "online"}[k];
  if (!pgU.cache[k]) {
    box.innerHTML = '<div class="loading"><i class="spin"></i><span>Reading…</span></div>';
    try { pgU.cache[k] = await pgApi(pgU.id, `/user/${pgUEnc()}/${path}`); } catch (e) { if (pgU.act === k && box.isConnected) box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  }
  if (pgU.act !== k || !box.isConnected) return;
  const r = pgU.cache[k], tb = (cols, rows, none) => `<table class="tbl pgu-tbl"><colgroup>${cols.map(c => `<col${c[1] ? ` style="width:${c[1]}"` : ""}>`).join("")}</colgroup><thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody>${rows || `<tr><td colspan="${cols.length}" class="mut">${none}</td></tr>`}</tbody></table>`;
  if (k === "devices") box.innerHTML = tb([["Device"], ["ID", "130px"], ["First seen", "100px"], ["Last used", "100px"], ["", "44px"]],
    r.devices.map((d, i) => `<tr><td class="name"><div class="ell" title="${esc(pgUDevRow(d))}">${esc(pgUDevRow(d))}</div></td><td class="mono mut ell" title="${esc(d.hwid)}">${esc(d.short)}</td><td class="mut">${esc(pgUAgo(d.first))}</td><td class="mut">${esc(pgUAgo(d.last))}</td><td><button type="button" class="sm danger" onclick="pgUserDev(${i})" title="Remove this device">✕</button></td></tr>`).join(""), "no device has connected yet")
    + (r.devices.length ? `<div class="pgu-foot"><button type="button" class="sm danger" onclick="pgUserDevAll()" title="Remove every device of this user">Remove all ${r.count} devices</button></div>` : "");
  else if (k === "subs") box.innerHTML = tb([["When", "150px"], ["App"], ["Address", "140px"]], r.updates.map(x => `<tr><td class="mut">${esc(pgUWhen(x.at))}</td><td class="name"><div class="ell" title="${esc(x.agent)}">${esc(x.agent || "—")}</div></td><td class="mono mut">${esc(x.ip || "—")}</td></tr>`).join(""), "the link has not been fetched yet")
    + (r.count > r.updates.length ? `<small class="mut">The last ${r.updates.length} of ${r.count} fetches.</small>` : "");
  else box.innerHTML = tb([["Node"], ["Address", "180px"], ["Last seen", "110px"]], r.online.map(x => `<tr><td class="name">${esc(x.node)}</td><td class="mono">${esc(x.ip)}</td><td class="mut">${x.last_seen ? esc(pgUAgo(x.last_seen)) : x.connections + " conn."}</td></tr>`).join(""), "not connected to any node right now");
};
/* "Formats": what the subscription of this user answers in each client format, as the panel renders it (to see why a link does not work in one app, or to copy the config by hand) */
const pgUFormats = [["links", "Links"], ["links_base64", "Links (base64)"], ["xray", "Xray JSON"], ["sing_box", "sing-box JSON"], ["clash", "Clash"], ["clash_meta", "Clash Meta"], ["outline", "Outline"], ["wireguard", "WireGuard (zip)"]];
const pgUFmtHtml = () => `<div class="pgu-fmtbar"><label>Show as<select onchange="pgUserFmtLoad(this.value)">${pgUFormats.map(([v, l]) => `<option value="${v}" ${v === pgU.fmt ? "selected" : ""}>${l}</option>`).join("")}</select></label><span class="mut" id="pgu-fmtinfo"></span><span class="sp"></span><button type="button" class="sm" id="pgu-fmtcopy" disabled onclick="pgUserFmtCopy(this)">Copy</button></div><div id="pgu-fmtbody"></div>`;
window.pgUserFmtLoad = async f => {
  pgU.fmt = f; const body = $("#pgu-fmtbody"), info = $("#pgu-fmtinfo"), cp = $("#pgu-fmtcopy"); if (!body) return;
  const key = "fmt:" + f;
  if (!pgU.cache[key]) {
    body.innerHTML = '<div class="loading"><i class="spin"></i><span>Asking the panel…</span></div>'; info.textContent = ""; cp.disabled = true;
    try { pgU.cache[key] = await pgApi(pgU.id, `/user/${pgUEnc()}/subscription?format=${encodeURIComponent(f)}`); }
    catch (e) { if (pgU.fmt === f && body.isConnected) body.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  }
  if (pgU.fmt !== f || !body.isConnected) return;
  const r = pgU.cache[key], bytes = r.size.toLocaleString() + " bytes";
  info.textContent = r.binary ? bytes + " · zip file" : `${bytes} · ${r.type}${r.truncated ? " · cut" : ""}`;
  cp.disabled = r.binary || r.empty;
  body.innerHTML = r.binary ? `<div class="mut pgu-fmtnote">WireGuard is answered as a zip file (${bytes}${r.empty ? "; empty: this user has no WireGuard config" : ""}). A browser downloads it from the subscription link of the user.</div>`
    : r.empty ? '<div class="mut pgu-fmtnote">The panel answers this format with nothing: the user has no config of this kind.</div>' : `<textarea class="pgu-fmtta" readonly spellcheck="false">${esc(r.text)}</textarea>`;
};
window.pgUserFmtCopy = btn => { const r = pgU.cache["fmt:" + pgU.fmt]; if (r && r.text) copyFrom(btn, r.text); };
window.pgUserDev = async i => {
  const d = (pgU.cache.devices || {}).devices[i]; if (!d) return;
  if (!await gconfirm(`Remove the device “${pgUDevRow(d)}” of ${pgU.d.user.username}? It can connect again (and then counts as a new device).`, {danger: true})) return;
  try { await pgApi(pgU.id, `/user/${pgUEnc()}/devices?hwid=${encodeURIComponent(d.hwid)}`, {method: "DELETE"}); toast("✓ Device removed", "ok"); delete pgU.cache.devices; pgUserAct("devices"); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.pgUserDevAll = async () => {
  if (!await gconfirm(`Remove every device of ${pgU.d.user.username}? All of them can connect again (and count as new devices).`, {danger: true})) return;
  try { await pgApi(pgU.id, `/user/${pgUEnc()}/devices/reset`, {method: "POST"}); toast("✓ All devices removed", "ok"); delete pgU.cache.devices; pgUserAct("devices"); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* "Investigate" (1.69.191; made compact in 1.69.194 - the owner: "a more compact, professional, elegant box with nothing to scroll inside"): why a customer says the data ended fast or goes by itself.
   A strip on top (the verdict and four figures), then four tabs - Overview (usage by hour, what was found, by server), Devices and addresses, Watch (one user followed for some hours, services/pg_watch.py) and the
   Message for the customer. Every list shows its first rows and says how many more there are (the full lists are in the Activity box of the user window). It only reads. */
const pgUInvTabs = [["overview", "Overview"], ["people", "Devices and addresses"], ["watch", "Watch"], ["message", "Message"]];
const pgUInvMax = 5;                                       // rows a list of the window shows before "+N more"
const pgUInvMore = (n, k = pgUInvMax) => n > k ? `<div class="pgi-more">+${n - k} more</div>` : "";
const pgUInvAddr = x => x.local ? '<span class="mut">not visible</span>' : x.cf ? '<span class="mut" title="Cloudflare passed the request on without the customer\'s address">Cloudflare</span>' : esc(x.ip) + (x.ours ? ` <small class="mut" title="Our server: a tunnel entrance, not the customer">our ${esc(x.ours)}</small>` : "");
window.pgUserInvestigate = async () => {
  const nm = pgU.d.user.username, head = `<b>${esc(nm)}</b> <span class="pill">Investigate</span>`, back = '<button type="button" class="sm" onclick="pgUserInvBack()">← Back to the user</button>';
  showModal(head, '<div class="loading"><i class="spin"></i><span>Reading the usage, the devices and the fetches…</span></div>', "wide pgu pgu-inv", back);
  let r; try { r = await pgApi(pgU.id, `/user/${pgUEnc()}/investigate`); } catch (e) { showModal(head, `<div class="err">${esc(e.message)}</div>`, "wide pgu pgu-inv", back); return; }
  pgU.inv = r; pgU.invTab = pgU.invTab || "overview"; pgU.invSrv = pgU.invSrv || "48h";
  showModal(head, pgUInvShell(r), "wide pgu pgu-inv", back + '<button type="button" class="sm" onclick="pgUserInvCopy(this)" title="Copy a short message for the customer (Persian)">Copy message for the customer</button>');
  pgUInvPane();
};
window.pgUserInvBack = () => { clearTimeout(pgU.wt); pgUserShow(); pgUserAct(pgU.act); };
window.pgUserInvCopy = btn => { if (pgU.inv && pgU.inv.text) copyFrom(btn, pgU.inv.text); };
window.pgUserInvTab = k => { pgU.invTab = k; pgUInvPane(); };
window.pgUserInvSrv = k => { pgU.invSrv = k; pgUInvPane(); };
window.pgUserInvDevAll = async () => {
  if (!await gconfirm(`Remove every device of ${pgU.d.user.username}? All of them can connect again (and count as new devices).`, {danger: true})) return;
  try { await pgApi(pgU.id, `/user/${pgUEnc()}/devices/reset`, {method: "POST"}); toast("✓ All devices removed", "ok"); delete pgU.cache.devices; pgUserInvestigate(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.pgUserInvNew = async () => { await pgUserDo("revoke_sub"); };
function pgUInvShell(r) {
  const u = r.user, v = r.verdict, st = (l, val, sub = "") => `<div class="pgi-st"><span>${l}</span><b>${val}</b>${sub ? `<small>${sub}</small>` : ""}</div>`;
  const age = u.age_days < 1 ? Math.round(u.age_days * 24) + " h" : u.age_days.toFixed(1) + " d";
  const top = `<div class="pgi-head ${v.level}"><div class="pgi-v" title="${esc(v.title)}"><i class="d ${v.level}"></i><span>${esc(v.title)}</span></div><div class="pgi-stats">${st("Used", fmtB(u.used), u.limit ? `of ${fmtB(u.limit)} · ${u.pct} %` : "unlimited")}${st("Age", age, esc(String(u.status).replace("_", " ")))}${st("Average", u.per_day + " GB/d")}${st("Online", esc(pgUAgo(u.online_at)))}</div></div>`;
  const tabs = `<div class="pgi-tabs pg-admin-seg">${pgUInvTabs.map(([k, l]) => `<button type="button" data-k="${k}" onclick="pgUserInvTab('${k}')">${l}</button>`).join("")}</div>`;
  return `<div class="pgi">${top}${tabs}<div id="pgi-pane"></div></div>`;
}
function pgUInvPane() {
  const pane = $("#pgi-pane"), r = pgU.inv; if (!pane || !r) return;
  document.querySelectorAll("#modal .pgi-tabs button").forEach(b => b.classList.toggle("on", b.dataset.k === pgU.invTab));
  pane.innerHTML = {overview: pgUInvOverview, people: pgUInvPeople, watch: pgUInvWatchPane, message: pgUInvMessage}[pgU.invTab](r);
  if (pgU.invTab === "watch") { if (pgU.watchData) pgUWatchDraw(pgU.watchData); pgUserWatchLoad(); }
}
function pgUInvOverview(r) {
  const us = r.usage, hs = us.hours, max = Math.max(1, ...hs.map(h => h.bytes)), hot = us.busiest ? [us.busiest.first, us.busiest.first + us.busiest.count] : [-1, -1];
  const bars = hs.length ? `<div class="pgi-bars">${hs.map((h, i) => `<i class="${i >= hot[0] && i < hot[1] ? "hot" : ""}" style="height:${Math.max(1, Math.round(h.bytes * 100 / max))}%" title="${esc(h.local.slice(5))} · ${fmtB(h.bytes)}${h.node ? " · " + esc(h.node) : ""}"></i>`).join("")}</div><div class="pgi-ax">${hs.map(h => `<span>${h.hour % 6 === 0 ? String(h.hour).padStart(2, "0") : ""}</span>`).join("")}</div>` : '<div class="mut">no hourly usage rows</div>';
  const chart = `<section class="pgi-card"><h6>Usage by hour, last 2 days (Tehran)<span class="sp"></span><small>${fmtB(us.total_48h)}${us.night_share ? " · " + Math.round(us.night_share * 100) + " % at night" : ""}</small></h6>${bars}</section>`;
  const fs = r.findings.filter(f => !(f.level === "ok" && r.verdict.level === "ok")), shown = fs.slice(0, pgUInvMax);
  const find = `<section class="pgi-card"><h6>What was found</h6>${shown.map((f, i) => `<div class="pgi-f" title="${esc(f.detail)}"><i class="d ${f.level}"></i><div><b>${esc(f.title)}</b>${i === 0 && (f.level === "warn" || f.level === "bad") ? `<div class="mut">${esc(f.detail)}</div>` : ""}</div></div>`).join("") || '<div class="mut">Nothing to add.</div>'}${pgUInvMore(fs.length)}</section>`;
  const rows = pgU.invSrv === "30d" ? us.by_node_30d : us.by_node_48h;
  const srv = `<section class="pgi-card"><h6>By server<span class="sp"></span><span class="pgi-seg">${[["48h", "2 days"], ["30d", "30 days"]].map(([k, l]) => `<button type="button" class="${pgU.invSrv === k ? "on" : ""}" onclick="pgUserInvSrv('${k}')">${l}</button>`).join("")}</span></h6>${rows.slice(0, 7).map(x => `<div class="pgi-r pgi-srv"><span class="n ell" title="${esc(x.name)}">${esc(x.name)}</span><span class="pgi-bar"><i style="width:${Math.max(2, Math.round(x.share * 100))}%"></i></span><span class="mut">${fmtB(x.bytes)}</span><span class="mut">${Math.round(x.share * 100)} %</span></div>`).join("") || '<div class="mut">no usage</div>'}${pgUInvMore(rows.length, 7)}</section>`;
  return `<div class="pgi-grid"><div class="pgi-col">${chart}${find}</div><div class="pgi-col">${srv}</div></div>`;
}
function pgUInvPeople(r) {
  const dv = r.devices, fe = r.fetches, over = dv.count > dv.limit;
  const devs = `<section class="pgi-card"><h6 class="${over ? "err" : ""}">Devices<small>${dv.count} of ${dv.limit}${dv.own_limit ? "" : " (panel rule)"}</small><span class="sp"></span>${dv.count ? '<button type="button" class="sm danger" onclick="pgUserInvDevAll()" title="Remove every device of this user">Remove all</button>' : ""}</h6>${dv.rows.slice(0, pgUInvMax).map(d => `<div class="pgi-r pgi-2"><span class="n ell" title="${esc(pgUDevRow(d))}">${esc(pgUDevRow(d))}</span><span class="mut">${esc(pgUAgo(d.last))}</span></div>`).join("") || '<div class="mut">no device has connected yet</div>'}${pgUInvMore(dv.rows.length)}</section>`;
  const subs = `<section class="pgi-card"><h6>Subscription fetches<small>${fe.addresses} address${fe.addresses === 1 ? "" : "es"} · ${fe.apps} app${fe.apps === 1 ? "" : "s"}${fe.count > fe.read ? " in the last " + fe.read + " of " + fe.count : ""}</small></h6>${fe.rows.slice(0, pgUInvMax).map(x => `<div class="pgi-r pgi-3"><span class="mut" title="${esc(pgUWhen(x.at))}">${esc(pgUAgo(x.at))}</span><span class="n ell" title="${esc(x.agent)}">${esc(x.agent || "—")}</span><span class="mono">${pgUInvAddr(x)}</span></div>`).join("") || '<div class="mut">the link has not been fetched yet</div>'}${pgUInvMore(fe.rows.length)}</section>`;
  const live = `<section class="pgi-card"><h6>Connected right now</h6>${r.online.slice(0, pgUInvMax).map(x => `<div class="pgi-r pgi-3"><span class="n ell">${esc(x.node)}</span><span class="mono">${pgUInvAddr(x)}</span><span class="mut">${x.last_seen ? esc(pgUAgo(x.last_seen)) : x.connections + " conn."}</span></div>`).join("") || '<div class="mut">not connected to any node right now</div>'}${pgUInvMore(r.online.length)}</section>`;
  return `<div class="pgi-grid three">${devs}${subs}${live}</div>`;
}
function pgUInvMessage(r) {
  return `<section class="pgi-card"><h6>Message for the customer<small>Persian, ready to paste</small><span class="sp"></span><button type="button" class="sm" onclick="pgUserInvCopy(this)">Copy</button><button type="button" class="sm danger" onclick="pgUserInvNew()" title="Revoke the subscription: a new link, the old one stops working">New link</button></h6><textarea class="pgi-msg" dir="rtl" readonly spellcheck="false">${esc(r.text)}</textarea></section>
    <div class="pgi-note mut">${r.limits.map(esc).join(" ")}${r.notes.length ? " " + r.notes.map(esc).join(" ") : ""}</div>`;
}

/* the Watch tab (1.69.193, the owner, 2026-11-03: "watch the user for some hours and see"): a watch reads the user every 2 minutes - his used traffic and the addresses he is connected from - into the panel's own
   database (services/pg_watch.py). The tab shows the usage in 10-minute bars (Tehran time), every address with when it was first and last seen, and the findings; while a watch runs it reloads itself every minute. */
const pgUTeh = t => new Date((t + 12600) * 1000), pgUHm = t => String(pgUTeh(t).getUTCHours()).padStart(2, "0") + ":" + String(pgUTeh(t).getUTCMinutes()).padStart(2, "0");
const pgUDur = m => m >= 90 ? (m / 60).toFixed(1).replace(/\.0$/, "") + " h" : m + " min";
const pgUInvWatchPane = () => '<div id="pgu-watch"><div class="loading"><i class="spin"></i><span>Reading…</span></div></div>';
window.pgUserWatchLoad = async () => {
  const box = $("#pgu-watch"); if (!box) return;
  let w; try { w = await pgApi(pgU.id, `/user/${pgUEnc()}/watch`); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (!$("#pgu-watch")) return;
  pgU.watchData = w; pgUWatchDraw(w); clearTimeout(pgU.wt);
  if (w.active) pgU.wt = setTimeout(() => { if ($("#pgu-watch")) pgUserWatchLoad(); }, 60000);
};
window.pgUserWatchStart = async h => {
  try { const w = await pgApi(pgU.id, `/user/${pgUEnc()}/watch`, {method: "POST", body: {hours: h}}); toast(`✓ Watching ${pgU.d.user.username}`, "ok"); pgU.watchData = w; pgUWatchDraw(w); pgUserWatchLoad(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
window.pgUserWatchStop = async () => {
  if (!await gconfirm(`Stop watching ${pgU.d.user.username}? What was seen stays for 14 days.`)) return;
  try { await pgApi(pgU.id, `/user/${pgUEnc()}/watch`, {method: "DELETE"}); toast("✓ Watch stopped", "ok"); pgUserWatchLoad(); } catch (e) { toast("✕ " + e.message, "bad"); }
};
function pgUWatchDraw(w) {
  const box = $("#pgu-watch"); if (!box) return;
  const wt = w.watch, sm = w.summary, on = w.active, hs = on ? [1, 6, 24, 72] : [3, 12, 24, 72];
  const btns = `<span class="sp"></span><span class="mut">${on ? "longer" : "watch for"}</span>${hs.map(h => `<button type="button" class="sm" onclick="pgUserWatchStart(${h})">${on ? "+" : ""}${h} h</button>`).join("")}${on ? '<button type="button" class="sm danger" onclick="pgUserWatchStop()">Stop</button>' : ""}`;
  if (!wt) { box.innerHTML = `<section class="pgi-card"><h6>Watch${btns}</h6><div class="mut">Not watched. A watch reads this user every 2 minutes (his used traffic and the addresses he is connected from) and keeps it for 14 days: leave it on for some hours to see what goes on while the customer says the data goes by itself.</div></section>`; return; }
  const line = `${on ? "since" : "from"} ${esc(pgUWhen(wt.started_at))} · ${on ? "until " + esc(pgUWhen(wt.ends_at)) : "ended " + esc(pgUAgo(wt.ends_at))} · ${sm.samples} read${sm.samples === 1 ? "" : "s"}${wt.last_at ? " · last " + esc(pgUAgo(wt.last_at)) : ""}`;
  const head = `<section class="pgi-card"><h6>Watch <span class="pill ${on ? "on" : ""}">${on ? "running" : "ended"}</span><small>${line}${wt.last_error ? ` · <span class="err">${esc(wt.last_error)}</span>` : ""}</small>${btns}</h6>`;
  if (!sm.samples) { box.innerHTML = head + '<div class="mut">Waiting for the first read…</div></section>'; return; }
  const own = sm.addresses.filter(a => !a.ours).length, st = (l, v, s = "") => `<div class="pgi-st"><span>${l}</span><b>${v}</b>${s ? `<small>${s}</small>` : ""}</div>`;
  const stats = `<div class="pgi-stats pgi-wstats">${st("Used", fmtB(sm.total), sm.span ? pgUDur(Math.round(sm.span / 60)) : "")}${st("Addresses", String(sm.addresses.length), own + " not ours")}${st("At once", String(sm.simultaneous), "most in one read")}${st("Busiest 10 min", sm.peak ? fmtB(sm.peak.bytes) : "—", sm.peak ? pgUHm(sm.peak.t) : "")}</div>`;
  let bars = "";
  if (sm.bars.length) {
    const by = Object.fromEntries(sm.bars.map(b => [b.t, b])), t0 = sm.bars[0].t, t1 = sm.bars[sm.bars.length - 1].t, xs = [], max = Math.max(1, ...sm.bars.map(b => b.bytes));
    for (let t = t0; t <= t1; t += 600) xs.push(t);
    bars = `<div class="pgi-bars short">${xs.map(t => { const b = by[t] || {bytes: 0, ips: 0}; return `<i class="${sm.peak && t === sm.peak.t ? "hot" : ""}" style="height:${Math.max(1, Math.round(b.bytes * 100 / max))}%" title="${pgUHm(t)} · ${fmtB(b.bytes)} · ${b.ips} address${b.ips === 1 ? "" : "es"}"></i>`; }).join("")}</div><div class="pgi-ax">${xs.map(t => `<span>${pgUTeh(t).getUTCMinutes() === 0 && pgUTeh(t).getUTCHours() % 3 === 0 ? pgUHm(t) : ""}</span>`).join("")}</div>`;
  }
  const find = sm.findings.slice(0, pgUInvMax).map(f => `<div class="pgi-f" title="${esc(f.detail)}"><i class="d ${f.level}"></i><div><b>${esc(f.title)}</b></div></div>`).join("") || '<div class="mut">Nothing to report yet.</div>';
  const rows = sm.addresses.slice(0, pgUInvMax).map(a => `<div class="pgi-r pgi-4"><span class="mono ell" title="${esc(a.ip)}">${esc(a.ip)}${a.ours ? ` <small class="mut">our ${esc(a.ours)}</small>` : ""}</span><span class="n ell">${esc(a.nodes.join(", ") || "—")}</span><span class="mut">${pgUHm(a.first)}-${pgUHm(a.last)}</span><span class="mut">${pgUDur(a.minutes)}</span></div>`).join("") || '<div class="mut">no address was listed in any read</div>';
  box.innerHTML = head + stats + bars + "</section>" + `<div class="pgi-grid"><section class="pgi-card"><h6>What was seen</h6>${find}${pgUInvMore(sm.findings.length)}</section><section class="pgi-card"><h6>Addresses<small>first-last · seen for</small></h6>${rows}${pgUInvMore(sm.addresses.length)}</section></div>`;
}

/* ---------------------------------------------------------------- the tools of the Users list */
window.pgUsersMenu = () => pnType() !== "pasarguard" ? "" : `<button type="button" class="sm pgu-tools" aria-haspopup="menu" title="More things to do with users" onclick="nkMenu(this, pgUsersItems(), event)">More… ▾</button>`;
const pgUsersItems = () => {      // the menu of the Users list: one column, groups, a line between them (the owner's picture); an item answers pgUsersTool like the old list did
  const it = (t, v, o) => labItem(t, () => pgUsersTool({value: v, selectedIndex: 0}), o);
  return [labGrp("The ticked users"), it("Set owner…", "owner"), it("Apply a template…", "template"), it("Reset usage…", "reset"), it("New subscription links…", "revoke"), it("Delete…", "delete", {danger: true}), labSep,
    labGrp("Many users"), it("Shift end dates…", "expire"), it("Shift data limits…", "data"), it("Shadowsocks method…", "ssmethod"), it("Clean up expired…", "clean"), labSep,
    labGrp("New users"), it("New user from a template…", "new1"), it("Many users from a template…", "newn")];
};
const pgUTicked = () => [...document.querySelectorAll(".pn-uchk:checked")].map(x => x.value);
const pgUOpts = async () => (pgUOpts.c && pgUOpts.c.id === pn.id && Date.now() - pgUOpts.c.at < 20000) ? pgUOpts.c.d : (pgUOpts.c = {id: pn.id, at: Date.now(), d: await pgApi(+pn.id, "/user-tools/options")}).d;
const pgUList = names => names.slice(0, 3).join(", ") + (names.length > 3 ? ` and ${names.length - 3} more` : "");
window.pgUsersTool = async sel => {
  const v = sel.value; sel.selectedIndex = 0; if (!v) return;
  const id = +pn.id;
  if (["expire", "data", "ssmethod"].includes(v)) return pgShiftOpen(v);
  if (v === "clean") return pgCleanOpen();
  if (v === "new1" || v === "newn") return pgNewOpen(v === "newn");
  const names = pgUTicked(); if (!names.length) { toast("Tick some users first", "bad"); return; }
  const done = async (what, value, ok) => { try { const r = await pgApi(id, "/user-tools/bulk", {method: "POST", body: {what, usernames: names, value, confirm: true}}); toast(`✓ ${ok} for ${r.count} user${r.count === 1 ? "" : "s"}`, "ok"); pn.users = null; refresh(); } catch (e) { toast("✕ " + e.message, "bad"); } };
  if (v === "reset") { if (await gconfirm(`Set the used traffic of ${names.length} user${names.length === 1 ? "" : "s"} (${pgUList(names)}) back to zero? The panel also removes the next plan of a user whose usage is reset.`)) done("reset", null, "Usage reset"); }
  else if (v === "revoke") { if (await gconfirm(`Make new subscription links for ${names.length} user${names.length === 1 ? "" : "s"} (${pgUList(names)})? The old links stop working.`, {danger: true})) done("revoke_sub", null, "New links made"); }
  else if (v === "delete") { const t = await gprompt(`Delete ${names.length} user${names.length === 1 ? "" : "s"} (${pgUList(names)}) for good? This cannot be undone. Type delete to confirm.`, "", {danger: true}); if (t && t.trim().toLowerCase() === "delete") done("delete", null, "Deleted"); else if (t !== null) toast("Not deleted: the word did not match", "bad"); }
  else if (v === "owner" || v === "template") {
    let o; try { o = await pgUOpts(); } catch (e) { toast("✕ " + e.message, "bad"); return; }
    const list = v === "owner" ? o.admins.map(a => [a.name, a.name]) : o.templates.map(t => [t.id, t.name]);
    if (!list.length) { toast(v === "owner" ? "This panel shows no admin" : "This panel has no user template", "bad"); return; }
    pgUPick(v === "owner" ? "Set the owner" : "Apply a template", `${names.length} user${names.length === 1 ? "" : "s"}: ${pgUList(names)}. ${v === "owner" ? "They become the users of this admin." : "The template replaces their data limit, end date, groups and status, and the panel removes a next plan they have."}`, list, v === "owner" ? "Set owner" : "Apply",
      val => done(v === "owner" ? "set_owner" : "apply_template", v === "owner" ? val : +val, v === "owner" ? "Owner set" : "Template applied"));
  }
};
/* a small window with one choice */
window.pgUPick = (title, text, list, okText, onOk) => {
  showModal(`<b>${esc(title)}</b>`, `<form class="pg-admin-form2" onsubmit="return pgUPickGo(event)"><p class="mut pgu-p">${esc(text)}</p><div class="sbox"><label class="kv2 kf"><span>Choose</span><select name="v">${list.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select></label></div>
    <div class="frm-act"><button class="pri" type="submit">${esc(okText)}</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "pgu-tool pgu-t-pick keep"); pgUPick.cb = onOk;
};
window.pgUPickGo = e => { e.preventDefault(); const v = e.target.elements.v.value, cb = pgUPick.cb; closeModal(); if (cb) cb(v); return false; };

/* shift the end dates or the data limits of many users, or set their Shadowsocks method: counted first, done after a confirm (the same "Who" for all three) */
const pgUStat = ["active", "disabled", "limited", "expired", "on_hold"];
const pgSsMethods = ["aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "xchacha20-poly1305"];      // twin of pg_users.SS_METHODS
window.pgShiftOpen = async kind => {
  let o; try { o = await pgUOpts(); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  const ss = kind === "ssmethod", days = kind === "expire", names = pgUTicked(), fld = (l, inner) => `<label class="kv2 kf"><span>${l}</span>${inner}</label>`;
  pgShift.kind = kind; pgShift.names = names; pgShift.n = null;
  const first = ss ? `<div class="sbox"><h5><i>🔐</i>Cipher of the Shadowsocks config</h5>
      ${fld("Method", `<select name="method">${pgSsMethods.map(m => `<option ${m === "chacha20-ietf-poly1305" ? "selected" : ""}>${m}</option>`).join("")}</select>`)}
      <small class="mut">Only users that have a Shadowsocks config are touched. Each keeps its own password; only the method changes, and a client takes the new one when it fetches its subscription again.</small></div>`
    : `<div class="sbox"><h5><i>${days ? "📅" : "📦"}</i>${days ? "Add days to the end date" : "Add GB to the data limit"}</h5>
      ${fld("Amount", `<input name="amount" type="number" step="${days ? 1 : 0.5}" required placeholder="${days ? "30, or -7 to take away" : "10, or -5 to take away"}"><em>${days ? "days" : "GB"}</em>`)}
      <small class="mut">${days ? "Users with no end date are not touched." : "Users with an unlimited data limit are not touched."} The panel adds the number to every user the choices below match.</small></div>`;
  showModal(`<b>${ss ? "Shadowsocks method" : days ? "Shift end dates" : "Shift data limits"}</b>`, `<form class="pg-admin-form2 pgu-shift" oninput="pgShiftDirty()" onchange="pgShiftDirty()" onsubmit="return pgShiftCount(event)">
    ${first}
    <div class="sbox"><h5><i>🎯</i>Who</h5>
      <div class="kv2 kf"><span>Status</span><div class="pgu-chips">${pgUStat.map(s => `<label class="chk"><input type="checkbox" name="st" value="${s}"><span>${s.replace("_", " ")}</span></label>`).join("")}</div></div>
      <div class="kv2 kf"><span>Groups</span><div class="pgu-groups">${o.groups.map(g => `<label class="chk"><input type="checkbox" name="grp" value="${g.id}"><span>${esc(g.name)}</span></label>`).join("") || '<span class="mut">no group</span>'}</div></div>
      ${fld("Admin", `<select name="admin"><option value="">any admin</option>${o.admins.map(a => `<option>${esc(a.name)}</option>`).join("")}</select>`)}
      ${fld("End date", `<span class="pgu-two"><input name="after" type="date" title="Ends on or after this day"><i>to</i><input name="before" type="date" title="Ends on or before this day"></span>`)}
      ${names.length ? `<label class="kv2 kf"><span>Ticked</span><span class="chk"><input type="checkbox" name="only" checked><span>only the ${names.length} ticked user${names.length === 1 ? "" : "s"} (${esc(pgUList(names))})</span></span></label>` : ""}
      <small class="mut">Nothing chosen = nobody: pick at least one of these.</small></div>
    <div class="pgu-res" id="pgu-res"></div>
    <div class="frm-act"><button class="pri" type="submit">Count the users</button><button type="button" id="pgu-go" disabled onclick="pgShiftGo()">Apply…</button><button type="button" onclick="closeModal()">Cancel</button></div></form>`, "pgu-tool pgu-t-shift keep");
};
const pgShift ={kind: "", names: [], n: null, body: null};
function pgShiftBody(form, dry) {
  const f = form.elements, filters = {}, st = [...form.querySelectorAll('input[name="st"]:checked')].map(x => x.value), gr = [...form.querySelectorAll('input[name="grp"]:checked')].map(x => +x.value);
  if (st.length) filters.status = st; if (gr.length) filters.group_ids = gr; if (f.admin.value) filters.admins = [f.admin.value]; if (f.after.value) filters.expire_after_day = f.after.value; if (f.before.value) filters.expire_before_day = f.before.value;
  if (f.only && f.only.checked) filters.usernames = pgShift.names;
  if (pgShift.kind === "ssmethod") return {method: f.method.value, filters, dry_run: dry, confirm: !dry};
  return {what: pgShift.kind, amount: +f.amount.value, filters, dry_run: dry, confirm: !dry};
}
const pgShiftPath = () => pgShift.kind === "ssmethod" ? "/user-tools/proxy-method" : "/user-tools/shift";
window.pgShiftDirty = () => { pgShift.n = null; const go = $("#pgu-go"), r = $("#pgu-res"); if (go) go.disabled = true; if (r) r.innerHTML = ""; };
window.pgShiftCount = async e => {
  e.preventDefault(); const r = $("#pgu-res"), body = pgShiftBody(e.target, true), btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
  try { const j = await pgApi(+pn.id, pgShiftPath(), {method: "POST", body}); pgShift.n = j.count; pgShift.body = body; r.innerHTML = `<div class="pgu-count ${j.count ? "" : "none"}"><b>${j.count ?? "?"}</b> user${j.count === 1 ? "" : "s"} match${j.count === 1 ? "es" : ""}${j.count ? "" : ": nothing would change"}</div>`; $("#pgu-go").disabled = !j.count; }
  catch (err) { r.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  btn.disabled = false; return false;
};
window.pgShiftGo = async () => {
  const b = pgShift.body, n = pgShift.n; if (!b || !n) return;
  const ss = pgShift.kind === "ssmethod", what = ss ? "" : pgShift.kind === "expire" ? `${b.amount > 0 ? "add" : "take away"} ${Math.abs(b.amount)} day${Math.abs(b.amount) === 1 ? "" : "s"} ${b.amount > 0 ? "to" : "from"} the end date` : `${b.amount > 0 ? "add" : "take away"} ${Math.abs(b.amount)} GB ${b.amount > 0 ? "to" : "from"} the data limit`;
  if (!await gconfirm(ss ? `Set the Shadowsocks method of ${n} user${n === 1 ? "" : "s"} to ${b.method}? Each keeps its own password; every client has to take its subscription again to get the new method. The panel cannot undo it (set the old method again to go back).`
    : `${what[0].toUpperCase() + what.slice(1)} of ${n} user${n === 1 ? "" : "s"}? The panel cannot undo it (do the opposite amount to go back).`, {danger: true})) return;
  try { const j = await pgApi(+pn.id, pgShiftPath(), {method: "POST", body: {...b, dry_run: false, confirm: true}}); toast(`✓ Done for ${j.count ?? n} user${(j.count ?? n) === 1 ? "" : "s"}`, "ok"); pn.users = null; closeModal(); refresh(); }
  catch (e) { toast("✕ " + e.message, "bad"); }
};

/* clean up the users that are expired, limited, on hold or disabled: listed first, deleted only with the state typed back */
window.pgCleanOpen = async () => {
  let o; try { o = await pgUOpts(); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  const fld = (l, inner) => `<label class="kv2 kf"><span>${l}</span>${inner}</label>`;
  showModal("<b>Clean up users</b>", `<form class="pg-admin-form2" oninput="pgCleanDirty()" onchange="pgCleanDirty()" onsubmit="return pgCleanList(event)"><div class="sbox"><h5><i>🧹</i>Which users</h5>
      ${fld("State", `<select name="target"><option value="expired">expired</option><option value="limited">limited (out of data)</option><option value="on_hold">on hold</option><option value="disabled">disabled</option></select>`)}
      ${fld("Admin", `<select name="admin"><option value="">any admin</option>${o.admins.map(a => `<option>${esc(a.name)}</option>`).join("")}</select>`)}
      ${fld("Since", `<span class="pgu-two"><input name="after" type="date" title="From this day"><i>to</i><input name="before" type="date" title="Until this day"></span>`)}
      <small class="mut">An expired user is picked by its end date; the others by the day they reached that state. Both days empty = every user in that state.</small></div>
    <div class="pgu-res" id="pgu-res"></div>
    <div class="frm-act"><button class="pri" type="submit">List the users</button><button type="button" id="pgu-go" class="danger" disabled onclick="pgCleanGo()">Delete them…</button><button type="button" onclick="closeModal()">Close</button></div></form>`, "pgu-tool pgu-t-clean keep");
};
const pgCleanQ =form => { const f = form.elements, q = new URLSearchParams({target: f.target.value}); if (f.admin.value) q.set("admin", f.admin.value); if (f.after.value) q.set("after_day", f.after.value); if (f.before.value) q.set("before_day", f.before.value); return q; };
window.pgCleanDirty = () => { const go = $("#pgu-go"), r = $("#pgu-res"); if (go) go.disabled = true; if (r) r.innerHTML = ""; pgCleanQ.q = null; };
window.pgCleanList = async e => {
  e.preventDefault(); const r = $("#pgu-res"), btn = e.target.querySelector("button[type=submit]"), q = pgCleanQ(e.target); btn.disabled = true;
  try {
    const j = await pgApi(+pn.id, "/user-tools/expired?" + q); pgCleanQ.q = q; pgCleanQ.n = j.count;
    r.innerHTML = `<div class="pgu-count ${j.count ? "" : "none"}"><b>${j.count}</b> user${j.count === 1 ? "" : "s"}${j.count ? "" : ": nothing to clean"}</div>${j.users.length ? `<div class="pgu-names">${j.users.map(n => `<span>${esc(n)}</span>`).join("")}${j.truncated ? `<em class="mut">… and ${j.count - j.users.length} more</em>` : ""}</div>` : ""}`;
    $("#pgu-go").disabled = !j.count;
  } catch (err) { r.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  btn.disabled = false; return false;
};
window.pgCleanGo = async () => {
  const q = pgCleanQ.q, n = pgCleanQ.n; if (!q || !n) return; const target = q.get("target");
  const t = await gprompt(`Delete ${n} ${target.replace("_", " ")} user${n === 1 ? "" : "s"} for good? Their accounts and their links are gone and it cannot be undone. Type ${target} to confirm.`, "", {danger: true});
  if (t === null) return; if (t.trim() !== target) { toast("Not deleted: the word did not match", "bad"); return; }
  q.set("typed", target);
  try { const j = await pgApi(+pn.id, "/user-tools/expired?" + q, {method: "DELETE"}); toast(`✓ ${j.count} user${j.count === 1 ? "" : "s"} deleted`, "ok"); pn.users = null; closeModal(); refresh(); } catch (e) { toast("✕ " + e.message, "bad"); }
};

/* new users from a template: one (a name) or many (random names, or a base and a number) */
window.pgNewOpen = async many => {
  let o; try { o = await pgUOpts(); } catch (e) { toast("✕ " + e.message, "bad"); return; }
  if (!o.templates.length) { toast("This panel has no user template: make one in Templates first", "bad"); return; }
  const fld = (l, inner, id = "", hid = false) => `<label class="kv2 kf"${id ? ` id="${id}"` : ""}${hid ? " hidden" : ""}><span>${l}</span>${inner}</label>`;
  showModal(`<b>${many ? "Many users from a template" : "New user from a template"}</b>`, `<form class="pg-admin-form2" onsubmit="return pgNewMake(event, ${many ? 1 : 0})"><div class="sbox"><h5><i>＋</i>${many ? "Users" : "User"}</h5>
      ${fld("Template", `<select name="tpl">${o.templates.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select>`)}
      ${many ? fld("How many", `<input name="count" type="number" min="1" max="500" step="1" value="10" required><em>1 to 500</em>`)
        + fld("Names", `<select name="strategy" onchange="pgNewSt(this)"><option value="random">random</option><option value="sequence">a base and a number</option></select>`)
        + fld("Base", `<input name="base" maxlength="120" placeholder="shop-" autocomplete="off">`, "pgu-f-base", true)
        + fld("First number", `<input name="start" type="number" min="0" step="1" value="1">`, "pgu-f-start", true)
      : fld("Username", `<input name="username" required minlength="3" maxlength="128" placeholder="3 to 128 letters, digits or - _ @ ." autocomplete="off">`)}
      ${fld("Note", `<input name="note" maxlength="500" placeholder="optional">`)}
      <small class="mut">The template decides the groups, the data limit, the duration and the status; its prefix and suffix are put around the name.</small></div>
    <div class="pgu-res" id="pgu-res"></div>
    <div class="frm-act"><button class="pri" type="submit">${many ? "Make the users" : "Make the user"}</button><button type="button" onclick="closeModal()">Close</button></div></form>`, "pgu-tool pgu-t-new keep");
};
window.pgNewSt =sel => { const seq = sel.value === "sequence"; ["pgu-f-base", "pgu-f-start"].forEach(i => { const el = $("#" + i); if (el) el.hidden = !seq; }); };
window.pgNewMake = async (e, many) => {
  e.preventDefault(); const f = e.target.elements, r = $("#pgu-res"), btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
  try {
    if (many) {
      const body = {template_id: +f.tpl.value, count: +f.count.value, strategy: f.strategy.value, note: f.note.value}; if (body.strategy === "sequence") { body.base = f.base.value; body.start_number = f.start.value === "" ? null : +f.start.value; }
      const j = await pgApi(+pn.id, "/user-tools/bulk-from-template", {method: "POST", body});
      r.innerHTML = `<div class="pgu-count"><b>${j.created}</b> user${j.created === 1 ? "" : "s"} made</div><label class="pgu-links">Subscription links<textarea readonly rows="6" onfocus="this.select()">${esc(j.links.join("\n"))}</textarea></label><button type="button" class="sm" onclick="navigator.clipboard.writeText($('.pgu-links textarea').value).then(()=>toast('✓ Links copied','ok'))">Copy the links</button>`;
      toast(`✓ ${j.created} users made`, "ok");
    } else {
      const j = await pgApi(+pn.id, "/user-tools/from-template", {method: "POST", body: {template_id: +f.tpl.value, username: f.username.value.trim(), note: f.note.value}});
      r.innerHTML = `<div class="pgu-count"><b>${esc(j.user.username)}</b> made</div>${j.user.sub ? `<div class="kv2"><span>Link</span><div><span class="mono ell ue-link" title="Click to copy" onclick="copyFrom(this, '${esc(j.user.sub)}')">${esc(j.user.sub)}</span></div></div>` : ""}`;
      toast(`✓ ${j.user.username} made`, "ok"); e.target.elements.username.value = "";
    }
    pn.users = null; refresh();
  } catch (err) { r.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  btn.disabled = false; return false;
};
