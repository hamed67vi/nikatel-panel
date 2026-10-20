/* The fluid layer's one script (1.44.0): a table keeps its columns as its box gets narrower - they shrink and cut their text - and when even that is not enough the LESS important columns
   drop out, the most important last (the first column, the status and the buttons at the end never). Nothing is turned into cards. It looks at the BOX the table sits in (not at the window), so
   the same table is right in the page, in a window, with the side bar as a rail or as a menu. The importance of a column comes from its header text (PRIO below, higher = drops out earlier) or from a data-prio attribute of the header;
   a header that is empty or holds a tick box is a column of switches or buttons and stays. A column has a least width (MINW, or what its buttons need): when the box cannot hold the least
   widths of all columns, the most droppable column goes and the sum is made again; then every column is given its wished width (the one in the colgroup), and when that is too much the columns
   shrink towards their least. `colspan` cells (a note over three columns) are narrowed to the columns that are still there. Runs after a table is drawn and when a box changes its size; does
   nothing when the fluid layout is off (early.js). */
(function () {
  if (!window.NK_FLUID) return;

  var PRIO = {status: 1, state: 1, ping: 2, "ping loss": 2, traffic: 2, used: 2, ram: 2, cpu: 2, expires: 2, "on panels": 2, value: 2, on: 2, "traffic used": 2, limit: 3, "data limit": 2,
    available: 2, verdict: 2, use: 2, now: 2, result: 2, "would be": 2, issues: 3, "iran node": 3, "foreign node": 3, cloud: 3, speed: 3, delay: 3, address: 3, when: 3, provider: 3, domains: 3,
    "tunnel node": 3, "iran server": 3, "our server": 3, where: 3, groups: 3, users: 3, inbounds: 3, outbounds: 3, hosts: 3, admins: 3, channel: 3, before: 3, holds: 3, route: 3, outages: 3,
    latency: 3, tunnels: 3, "address copy": 3, "goes to": 3, protocol: 3, default: 3, duration: 3, "last online": 3, "cpu at the peak": 2, "traffic at the peak": 3,
    user: 0, name: 0, server: 0, tunnel: 0, account: 0, config: 0, host: 0, job: 0, node: 0, group: 0, template: 0, remarks: 0, record: 0, role: 0, core: 0, outbound: 0, inbound: 0,
    saved: 0, day: 0, actions: 0, admin: 4, type: 4, ports: 4, port: 4, ipv4: 4, who: 4, transport: 4, message: 4, every: 4, "last ok": 4, templates: 4, rules: 4, coef: 4, network: 4, security: 4, why: 4,
    rights: 4, "last used": 4, permissions: 4, "limits and rules": 4, "origin port": 4, "its own network": 4, "real test from the panel server": 4, "what was about to happen": 4,
    panel: 5, disk: 5, ttl: 5, note: 5, tls: 5, key: 5, xray: 5, "last start": 5, took: 5, "last error": 5, "subs group": 5, username: 5, created: 5, updated: 5, backups: 5,
    "listens on": 5, source: 5, size: 5, "backup of": 5};

  var MINW = {status: 72, state: 72, expires: 104, "traffic used": 120, traffic: 84, used: 84, on: 56, cpu: 56, ram: 56, disk: 56, users: 56, port: 56, ttl: 48, coef: 56, rules: 56,
    hosts: 56, delay: 56, limit: 64, ping: 60, "ping loss": 84, inbounds: 96, outbounds: 82, templates: 80, groups: 96, "data limit": 76, duration: 70, username: 80,
    "bandwidthmb s": 118};      // (inbounds and groups hold the pills of a group or a template; "bandwidthmb s" is the header of the tunnels' Bandwidth column - "Bandwidth" and its unit: the cell holds the capacity, the use and a bar)

  var norm = function (s) { return String(s || "").toLowerCase().replace(/[^a-z ]+/g, " ").replace(/\s+/g, " ").trim(); };
  var num = function (s) { var v = parseFloat(s); return isNaN(v) ? 0 : v; };
  var px = function (v) { return Math.floor(v * 10) / 10 + "px"; };

  /* the room the columns have: the inside of the box, less what the table itself keeps for its padding, its border and the gaps between its columns */
  function room(tbl, n) {
    var b = tbl.parentElement, cs, ts;

    if (!b) return 0;

    cs = getComputedStyle(b);
    ts = getComputedStyle(tbl);

    return b.clientWidth - num(cs.paddingLeft) - num(cs.paddingRight) - num(ts.paddingLeft) - num(ts.paddingRight) - num(ts.borderLeftWidth) - num(ts.borderRightWidth)
      - (ts.borderCollapse === "separate" ? num(ts.borderSpacing) * (n + 1) : 0);
  }

  /* what a column of buttons needs: the cells of the first rows are copied into a hidden table of their own (inside the same box, so the same rules apply) that is as wide as its content;
     the buttons in the real cell may be squeezed already, so they can not be measured there */
  function natural(tbl, i, limit) {
    var rows = tbl.tBodies[0] ? tbl.tBodies[0].rows : [], best = 0, probe, body, k, c, tr;

    if (!tbl.parentElement) return 0;

    probe = document.createElement("table");
    probe.__nk = 1;
    probe.className = tbl.className;
    probe.setAttribute("aria-hidden", "true");
    probe.style.cssText = "position:absolute;left:0;top:0;visibility:hidden;pointer-events:none;table-layout:auto;width:auto;min-width:0";
    body = document.createElement("tbody");
    probe.appendChild(body);

    for (k = 0; k < rows.length && k < (limit || 3); k++) {
      c = rows[k].cells[i];

      if (!c || c.colSpan > 1) continue;

      tr = document.createElement("tr");
      tr.appendChild(c.cloneNode(true));
      body.appendChild(tr);
    }

    tbl.parentElement.appendChild(probe);
    Array.prototype.forEach.call(probe.querySelectorAll("td"), function (td) { best = Math.max(best, td.getBoundingClientRect().width); });
    probe.remove();

    return Math.ceil(best);
  }

  function describe(tbl, heads, cols, avail) {
    var n = heads.length;

    return heads.map(function (th, i) {
      var t = norm(window.I18N && window.I18N.source ? window.I18N.source(th) : th.textContent), car = cols[i] || th, w, kind, v, tool, act, d, dp;      // (the header as the code wrote it: in Persian its text has no a-z letters, so every column was "no title" - none ever dropped and the name column was squeezed to a lamp)

      if (car.__w0 === undefined) car.__w0 = car.style.width;

      w = car.__w0;
      kind = /px$/.test(w) ? "px" : /%$/.test(w) ? "pct" : "auto";
      v = num(w);
      tool = !!th.querySelector("input,button");
      act = i === n - 1 && (!t || t === "actions") && !th.querySelector("input");
      dp = th.getAttribute("data-prio");      // a header can say its own importance (a table whose first column is a tick box: the name column is then the second one)
      d = {i: i, car: car, w0: w, kind: kind, act: act, p: i === 0 || tool || !t ? 0 : (dp !== null && dp !== "" ? num(dp) : PRIO[t] !== undefined ? PRIO[t] : 3),
        want: kind === "px" ? v : kind === "pct" ? v / 100 * avail : 0, min: 0};

      if (act) d.min = Math.max(natural(tbl, i), kind === "px" ? Math.min(v, 40) : 40);
      else if (!t) d.min = i === 0 ? Math.min(v || 52, 52) : 40;
      else if (th.getAttribute("data-min")) d.min = num(th.getAttribute("data-min"));      // a header can say its own least width: a column of addresses is no use cut to "198.51.1…"
      else if (th.getAttribute("data-fit")) d.min = Math.min(Math.max(natural(tbl, i, 40), 92), num(th.getAttribute("data-fit")));      // ... or "as wide as its content, up to this": the name cell holds a lamp and an icon before the text, so 92 px left room for one letter
      else d.min = MINW[t] || (d.p <= 1 ? 92 : 72);

      return d;
    });
  }

  function fit(tbl, heads, cols) {
    var avail = room(tbl, heads.length);

    if (avail < 60) return null;                                    // not on the screen (yet): the box tells when it is

    var d = describe(tbl, heads, cols, avail), vis = d.map(function (x) { return x.p === 0; }),      // the columns that never drop are in; the others come in the order of their importance, each if its least width still fits
        sum = function (f) { var s = 0; d.forEach(function (x) { if (vis[x.i]) s += f(x); }); return s; },
        least = function (x) { return x.min; }, fin = function (x) { return x.fin; }, used = sum(least);

    d.filter(function (x) { return x.p > 0; }).sort(function (a, b) { return a.p - b.p || a.i - b.i; }).forEach(function (x) {
      if (used + x.min <= avail) { vis[x.i] = true; used += x.min; }
    });

    var fixed = sum(function (x) { return x.kind === "auto" ? 0 : Math.max(x.want, x.min); }),
        autos = d.filter(function (x) { return vis[x.i] && x.kind === "auto"; }),
        share = autos.length ? Math.max(0, (avail - fixed) / autos.length) : 0, total, give, s, flex, base;

    d.forEach(function (x) { x.fin = vis[x.i] ? Math.max(x.kind === "auto" ? share : x.want, x.min) : 0; });
    total = sum(fin);

    if (total > avail) {                                            // too much: every column gives a part of what it has above its least
      give = sum(function (x) { return x.fin - x.min; });
      s = give > 0 ? Math.min(1, (total - avail) / give) : 0;
      d.forEach(function (x) { if (vis[x.i]) x.fin -= s * (x.fin - x.min); });
      total = sum(fin);

      if (total > avail) d.forEach(function (x) { x.fin *= avail / total; });      // nothing left to cut: all shrink alike
    } else if (total < avail) {                                     // room left: the columns that were given a share (not a size) take it
      flex = d.filter(function (x) { return vis[x.i] && !x.act && x.kind !== "px"; });

      if (!flex.length) flex = d.filter(function (x) { return vis[x.i] && !x.act; });

      base = flex.reduce(function (a, x) { return a + x.fin; }, 0);
      flex.forEach(function (x) { x.fin += (avail - total) * (base > 0 ? x.fin / base : 1 / flex.length); });
    }

    return {vis: vis, d: d};
  }

  /* do the cells of the rows still carry the mark of their column (a row with a colspan is left out) */
  function marked(tbl, off) {
    var rows = tbl.tBodies[0] ? tbl.tBodies[0].rows : [], r, i, c;

    for (r = 0; r < rows.length; r++) {
      c = rows[r].cells;

      if (c.length !== off.length) continue;

      for (i = 0; i < c.length; i++) if (c[i].classList.contains("fl-off") !== !!off[i]) return false;
    }

    return true;
  }

  function mark(tbl) {
    var heads = Array.prototype.slice.call(tbl.querySelectorAll(":scope > thead > tr:first-child > th")),
        cols = Array.prototype.slice.call(tbl.querySelectorAll(":scope > colgroup > col"));

    if (heads.length < 2) return;

    if (window.ResizeObserver && !tbl.__ro && tbl.parentElement) { tbl.__ro = 1; watch.observe(tbl.parentElement); }

    var plan = fit(tbl, heads, cols);

    if (!plan) return;

    var off = plan.vis.map(function (v) { return !v; }), vsig = off.join(""), wsig = plan.d.map(function (x) { return plan.vis[x.i] ? Math.round(x.fin) : 0; }).join(","),
        body = tbl.tBodies[0], first = body && body.firstElementChild, last = body && body.lastElementChild,
        same = tbl.__f === first && tbl.__l === last && tbl.__n === tbl.rows.length;

    var kept = same && marked(tbl, off);                            // (a table that is redrawn in place - the same rows, new cells - lost the marks of its cells: the last column was then an empty one beside the dropped ones)

    if (tbl.__v === vsig && tbl.__w === wsig && kept) return;       // nothing changed since the last pass

    if (tbl.__v !== vsig || !kept) {
      heads.forEach(function (th, i) { th.classList.toggle("fl-off", off[i]); });
      cols.forEach(function (col, i) { col.classList.toggle("fl-off", !!off[i]); });
      Array.prototype.forEach.call(tbl.querySelectorAll(":scope > tbody > tr"), function (tr) {
        var i = 0;

        Array.prototype.forEach.call(tr.children, function (td) {
          if (!td.__cs) td.__cs = td.colSpan || 1;

          var span = td.__cs, vis = 0, k;

          for (k = i; k < i + span; k++) if (!off[k]) vis++;

          if (span === 1) td.classList.toggle("fl-off", !!off[i]);
          else { td.classList.toggle("fl-off", vis === 0); if (vis) td.colSpan = vis; }

          i += span;
        });
      });
    }

    plan.d.forEach(function (x) { x.car.style.width = plan.vis[x.i] ? px(x.fin) : x.w0; });
    tbl.__v = vsig; tbl.__w = wsig; tbl.__f = first; tbl.__l = last; tbl.__n = tbl.rows.length;
    tbl.dispatchEvent(new Event("nkfit", {bubbles: true}));         // whoever counts what fits in a cell (the pills of a group) does it again
  }

  function pass() { Array.prototype.forEach.call(document.querySelectorAll("#page table.tbl, #modal table.tbl"), mark); }

  var t = null, later = function () { clearTimeout(t); t = setTimeout(pass, 30); };
  var watch = window.ResizeObserver ? new ResizeObserver(later) : {observe: function () {}};

  new MutationObserver(function (recs) {                            // a table drawn (or its rows redrawn): fit it before the first paint
    var hit = false, i, j, a, r;

    for (i = 0; i < recs.length && !hit; i++) {
      r = recs[i];

      if ((r.addedNodes.length === 1 && r.addedNodes[0].__nk) || (r.removedNodes.length === 1 && r.removedNodes[0].__nk)) continue;      // our own measuring table

      if (r.target.closest && r.target.closest("table.tbl")) { hit = true; break; }

      for (j = 0; j < r.addedNodes.length; j++) {
        a = r.addedNodes[j];

        if (a.nodeType === 1 && (a.matches("table.tbl") || a.querySelector("table.tbl"))) { hit = true; break; }
      }
    }

    if (hit) pass();
  }).observe(document.body, {childList: true, subtree: true});

  /* a text that was cut with a dash tells all of itself in a tip (a long name, an address, a message): found when the pointer comes to it, so nothing is drawn in advance and a text that has a tip of its own keeps it */
  document.addEventListener("mouseover", function (e) {
    var el = e.target, i, t;

    for (i = 0; i < 4 && el && el !== document.body && el.nodeType === 1; i++, el = el.parentElement) {
      if (el.title && !el.__nkTip) continue;                          // (its own tip)

      if (el.scrollWidth <= el.clientWidth + 1 || getComputedStyle(el).textOverflow !== "ellipsis") continue;

      t = (el.textContent || "").replace(/\s+/g, " ").trim();

      if (t && el.title !== t) { el.title = t; el.__nkTip = 1; }

      return;
    }
  }, true);

  addEventListener("resize", later);
  document.addEventListener("DOMContentLoaded", later);
  pass();
})();
