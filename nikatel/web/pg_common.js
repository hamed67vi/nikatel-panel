/* Shared helpers of the PasarGuard tabs of the Panels page (pg_access.js, pg_nodes.js, pg_cores.js, pg_admin.js, pg_usage.js).
   A tab file registers itself:   pgTabs.<key> = {label: "Groups", order: 30, types: ["pasarguard"], render: async (id, box) => {...}};
   app.js adds a button for every registered tab whose `types` contains the type of the open panel, and calls render(panelId, box) when the tab is opened (box = #pn-body).
   Uses the helpers of app.js: $, api, esc, toast, showModal, closeModal, gconfirm, gprompt, refresh, fmtB, gb, ago, nkHit, pn (state), pnTabs(). */
window.pgTabs = window.pgTabs || {};
const pgBars = () => `<div class="pn-bars">${pnTabs()}</div>`;
const pgSpin = (box, text) => { box.innerHTML = pgBars() + `<div class="loading"><i class="spin"></i><span>${esc(text)}</span></div>`; if (window.pnFuse) pnFuse(); };
window.pgPaint = (box, html) => {
  const same = box._qs && !nkInputAfter(box._qs) && box.dataset.tab === pn.tab && box._ph === html && !box.querySelector(":scope > .loading");      // a round of the timer brought the same tab and nobody touched the page since: nothing is drawn (no new set of boxes, nothing lost under the pointer)
  clearTimeout(box._lt); box.classList.remove("pn-busy"); box.dataset.tab = pn.tab;
  if (same) return;
  box._ph = html; box.innerHTML = (pn.tab === "usage" ? "" : pgBars()) + html; if (window.pnFuse) pnFuse();
};      // (the Usage tab carries its tab buttons in its own bar: pgUsageBarHtml)                                                                   // the tab bar, then the content of the tab
window.pgLoading = (box, text = "Loading…") => {      // the spinner only when the data is slow (350 ms) and the tab changed: until then what the tab that was open showed stays on the screen, dimmed by the page; the same tab read again never shows a spinner (the owner, 2026-10-16: no spinner between the tabs of the Panel)
  clearTimeout(box._lt);
  if (!box.querySelector(":scope > :not(.pn-bars)") || box.querySelector(":scope > .loading")) { pgSpin(box, text); return; }
  if (box.dataset.tab === pn.tab) return;
  box._lt = setTimeout(() => { if (box.isConnected && box.classList.contains("pn-busy")) pgSpin(box, text); }, 350);
};
window.pgError = (box, e) => { clearTimeout(box._lt); box.classList.remove("pn-busy"); box.innerHTML = pgBars() + `<div class="err">${esc((e && e.message) || String(e))}</div>`; if (window.pnFuse) pnFuse(); };
/* /api/panels/{id}/pg/<path>: `opts.body` may be an object */
window.pgApi = (id, path, opts = {}) => api(`/api/panels/${id}/pg${path}`, opts.body && typeof opts.body !== "string" ? {...opts, body: JSON.stringify(opts.body)} : opts);
/* the switch of the panel (a blue pill with a white knob when on) */
window.pgSw = (on, onchange, attrs = "") => `<label class="srv-sw ob2-sw ${on ? "on" : ""}"><input type="checkbox" ${on ? "checked" : ""} ${attrs} onchange="${onchange}"><i></i></label>`;
/* a bar of one row: what is on the left, a spacer, what is on the right */
window.pgBar = (left, right = "") => `<div class="toolbar pg-bar">${left}<span class="sp"></span>${right}</div>`;
window.pgEmpty = text => `<div class="pg-empty mut">${esc(text)}</div>`;
/* a call that changes something: the toast says what happened, the tab is drawn again when it worked */
window.pgDo = async (fn, okText, again) => { try { const r = await fn(); if (okText) toast("✓ " + okText, "ok"); if (again) await again(); return r; } catch (e) { toast("✕ " + e.message, "bad"); return null; } };