/* Two languages for the whole panel: English (the texts in the code) and Persian.
   The code keeps writing English; this file translates what reaches the screen, so every message - the ones the
   server sends too (events, errors, repair stories) - is covered by one dictionary (i18n.fa.*.js).

   A dictionary entry is "English text": "فارسی". An English text may contain {} for the parts that change (names,
   numbers, a nested message); the Persian text uses {} in the same order, or {1} {2} to reorder them. The parts are
   translated again by themselves, so "Config {} is not working: {}" also translates the error behind the colon.
   Texts with a leading symbol (✓ ✕ ⚠ 🔍 …) or a trailing "…" / ":" / "." are looked up without them.  */
(() => {
  const exact = Object.create(null), tpl = [], cache = new Map(), explainers = [];
  let sorted = true;
  const I18N = window.I18N = {
    lang: (() => { try { return localStorage.getItem("nk.lang") === "fa" ? "fa" : "en"; } catch (e) { return "en"; } })(),
    add(map) { for (const [k, v] of Object.entries(map)) { if (k.includes("{}")) { tpl.push({k, re: new RegExp("^" + k.replace(/[.*+?^$()|[\]\\]/g, "\\$&").replace(/\{\}/g, "(.+?)") + "$", "s"), to: v}); sorted = false; } else exact[k] = v; } cache.clear(); },
    /* functions (text) -> Persian or null, for families of messages a template cannot describe (curl codes, ssh errors …) */
    explain(fn) { explainers.push(fn); cache.clear(); },
  };

  const SYM = /^([^\p{L}\p{N}{(\[“"']*)(.*?)([\s:….!?]*)$/su;      // the leading symbols (✓ ✕ ⚠ …) are taken greedily: the text after them is what gets looked up
  function fill(to, caps, depth) {
    let i = 0;
    return to.replace(/\{(\d*)\}/g, (_, n) => { const c = caps[n ? +n - 1 : i++]; return c == null ? "" : (core(c, depth + 1) ?? c); });
  }
  function lookup(c, depth) {
    if (c in exact) return exact[c];
    if (!sorted) { tpl.sort((a, b) => b.k.length - a.k.length); sorted = true; }
    for (const t of tpl) { const m = t.re.exec(c); if (m) return fill(t.to, m.slice(1), depth); }
    for (const fn of explainers) { const r = fn(c, depth); if (r) return r; }
    return null;
  }
  function core(c, depth = 0) {
    if (!c || depth > 6) return null;
    let r = lookup(c, depth);
    if (r != null) return r;
    const m = SYM.exec(c);
    if (m && (m[1] || m[3]) && m[2]) {
      if (m[1]) { r = lookup(c.slice(m[1].length), depth); if (r != null) return m[1] + r; }
      r = lookup(m[2], depth); if (r != null) return m[1] + r + m[3].replace(/\.\.\./g, "…");
      if (m[3]) { r = lookup(m[2] + m[3].trimEnd().slice(0, 1), depth); if (r != null) return m[1] + r; }   // the sentence needs its full stop to match
    }
    const p = /^\((.*)\)$/s.exec(c);
    if (p) { r = core(p[1], depth + 1); if (r != null) return "(" + r + ")"; }
    // several sentences in one text: each one by itself
    if (c.length > 40 && /[.!?] /.test(c)) {
      const parts = c.split(/(?<=[.!?])\s+/); let hit = 0;
      const out = parts.map(s => { const t = core(s, depth + 1); if (t != null) hit++; return t ?? s; });
      if (hit) return out.join(" ");
    }
    // "a · b · c" lists
    if (c.includes(" · ")) { const parts = c.split(" · "); let hit = 0; const out = parts.map(s => { const t = core(s, depth + 1); if (t != null) hit++; return t ?? s; }); if (hit) return out.join(" · "); }
    return null;
  }
  /* the translation of one text (whitespace around it is kept); the text itself when there is none */
  I18N.tr = s => {
    if (I18N.lang !== "fa" || typeof s !== "string" || !s) return s;
    let v = cache.get(s);
    if (v === undefined) {
      const lead = /^\s*/.exec(s)[0], trail = /\s*$/.exec(s)[0], body = s.slice(lead.length, s.length - trail.length);
      const r = body && /[A-Za-z]{2}/.test(body) ? core(body) : null;
      v = r == null ? s : lead + r + trail;
      if (cache.size > 6000) cache.clear();
      cache.set(s, v);
    }
    return v;
  };
  I18N.t = I18N.tr;

  /* ---- the screen: text nodes and a few attributes, translated when they appear, restored when the language goes back */
  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE", "PRE", "NOSCRIPT", "TITLE"]);
  const ATTRS = ["title", "placeholder", "aria-label"];
  const orig = new WeakMap(), done = new WeakMap(), aorig = new WeakMap();
  const skipped = n => { for (let e = n.nodeType === 1 ? n : n.parentElement; e; e = e.parentElement) if (SKIP.has(e.tagName) || e.hasAttribute && e.hasAttribute("data-notr")) return true; return false; };
  function textNode(n) {
    if (skipped(n)) return;
    const v = n.nodeValue;
    if (I18N.lang === "fa") {
      if (done.get(n) === v) return;
      const t = I18N.tr(v);
      orig.set(n, v);
      if (t !== v) { done.set(n, t); n.nodeValue = t; } else done.delete(n);
    } else if (done.has(n)) { if (n.nodeValue === done.get(n)) n.nodeValue = orig.get(n); done.delete(n); }
  }
  function attrs(el) {
    if (el.nodeType !== 1 || el.hasAttribute("data-notr")) return;
    for (const a of ATTRS) {
      if (!el.hasAttribute(a)) continue;
      const v = el.getAttribute(a), m = aorig.get(el) || {};
      if (I18N.lang === "fa") {
        if (m[a] && m[a].fa === v) continue;
        const t = I18N.tr(v);
        if (t !== v) { m[a] = {en: v, fa: t}; aorig.set(el, m); el.setAttribute(a, t); } else if (m[a]) delete m[a];
      } else if (m[a] && el.getAttribute(a) === m[a].fa) { el.setAttribute(a, m[a].en); delete m[a]; }
    }
  }
  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1 || SKIP.has(root.tagName) || root.hasAttribute("data-notr")) return;
    attrs(root);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {acceptNode: n => n.nodeType === 1 && (SKIP.has(n.tagName) || n.hasAttribute("data-notr")) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
    for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeType === 3 ? textNode(n) : attrs(n);
  }
  let queue = new Set(), scheduled = false;
  const take = list => {
    for (const m of list) {
      if (m.type === "childList") m.addedNodes.forEach(n => queue.add(n));
      else if (m.type === "characterData") queue.add(m.target);
      else if (m.type === "attributes") queue.add(m.target);
    }
  };
  /* disconnect() throws away the changes that are waiting for the observer: a page that wrote its HTML in a microtask between the observer's call and this one stayed English (Settings → Api & Key in Persian)
     until the language was switched twice; takeRecords() hands them over first */
  const flush = () => { scheduled = false; take(obs.takeRecords()); const q = queue; queue = new Set(); obs.disconnect(); try { q.forEach(walk); } finally { watch(); } };
  const obs = new MutationObserver(list => {
    take(list);
    if (!scheduled) { scheduled = true; queueMicrotask(flush); }
  });
  const watch = () => obs.observe(document.documentElement, {childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ATTRS});

  function chrome() {
    const fa = I18N.lang === "fa";
    document.documentElement.lang = fa ? "fa" : "en";
    document.documentElement.dir = fa ? "rtl" : "ltr";
    document.title = fa ? "نیکاتل" : "Nikatel";
    document.querySelectorAll(".lang-btn").forEach(b => { b.textContent = fa ? "EN" : "فا"; b.title = fa ? "English" : "فارسی"; });
  }
  I18N.apply = () => { chrome(); obs.disconnect(); walk(document.body); watch(); };
  I18N.set = lang => { I18N.lang = lang === "fa" ? "fa" : "en"; try { localStorage.setItem("nk.lang", I18N.lang); } catch (e) { /* private mode */ } cache.clear(); I18N.apply(); if (window.onLangChange) window.onLangChange(I18N.lang); };
  window.toggleLang = () => I18N.set(I18N.lang === "fa" ? "en" : "fa");
  document.addEventListener("DOMContentLoaded", () => { chrome(); if (I18N.lang === "fa") walk(document.body); watch(); });
})();
