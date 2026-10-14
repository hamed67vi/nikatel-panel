/* Shared helpers of the PasarGuard tabs of the Panels page (pg_access.js, pg_nodes.js, pg_cores.js, pg_admin.js, pg_usage.js).
   A tab file registers itself:   pgTabs.<key> = {label: "Groups", order: 30, types: ["pasarguard"], render: async (id, box) => {...}};
   app.js adds a button for every registered tab whose `types` contains the type of the open panel, and calls render(panelId, box) when the tab is opened (box = #pn-body).
   Uses the helpers of app.js: $, api, esc, toast, showModal, closeModal, gconfirm, gprompt, refresh, fmtB, gb, ago, nkHit, pn (state), pnTabs(). */
window.pgTabs = window.pgTabs || {};
const pgBars = () => `<div class="pn-bars">${pnTabs()}</div>`;
window.pgPaint = (box, html) => { box.innerHTML = (pn.tab === "usage" ? "" : pgBars()) + html; if (window.pnFuse) pnFuse(); };      // (the Usage tab carries its tab buttons in its own bar: pgUsageBarHtml)                                                                   // the tab bar, then the content of the tab
window.pgLoading = (box, text = "Loading…") => { box.innerHTML = pgBars() + `<div class="loading"><i class="spin"></i><span>${esc(text)}</span></div>`; if (window.pnFuse) pnFuse(); };
window.pgError = (box, e) => { box.innerHTML = pgBars() + `<div class="err">${esc((e && e.message) || String(e))}</div>`; if (window.pnFuse) pnFuse(); };
/* /api/panels/{id}/pg/<path>: `opts.body` may be an object */
window.pgApi = (id, path, opts = {}) => api(`/api/panels/${id}/pg${path}`, opts.body && typeof opts.body !== "string" ? {...opts, body: JSON.stringify(opts.body)} : opts);
/* the switch of the panel (a blue pill with a white knob when on) */
window.pgSw = (on, onchange, attrs = "") => `<label class="srv-sw ob2-sw ${on ? "on" : ""}"><input type="checkbox" ${on ? "checked" : ""} ${attrs} onchange="${onchange}"><i></i></label>`;
/* a bar of one row: what is on the left, a spacer, what is on the right */
window.pgBar = (left, right = "") => `<div class="toolbar pg-bar">${left}<span class="sp"></span>${right}</div>`;
window.pgEmpty = text => `<div class="pg-empty mut">${esc(text)}</div>`;
/* a call that changes something: the toast says what happened, the tab is drawn again when it worked */
window.pgDo = async (fn, okText, again) => { try { const r = await fn(); if (okText) toast("✓ " + okText, "ok"); if (again) await again(); return r; } catch (e) { toast("✕ " + e.message, "bad"); return null; } };