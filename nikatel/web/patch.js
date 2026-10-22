/* The page is drawn IN PLACE (1.69.73). Until now every refresh of a page - the 8 or 15 second round, a tunnel or a server that changed its state, a tunnel being worked on - threw the whole page away and built it again
   from a new string of HTML: the lists lost their scroll, the pointer lost the button under it (its hover shine ran again), the flags and logos were made again, the animations began again, a selected text was gone -
   it looked like the page was reloading itself every few seconds (the owner, 2026-10-21: "in every page it is as if it is auto-refreshing, the page keeps jumping").
   Here the HTML a page makes is turned into a tree of its own (the RAW tree: what the page said, before anything else - the i18n, the list boxes, the fluid columns - touched the live nodes) and compared with the raw tree of the
   draw before. Only what the PAGE changed between the two draws is done to the live nodes, and only to the node it concerns: a text that changed is rewritten where it stands, a class that was added is added, a row that
   came is put in its place, a row that went is taken out. What the layers around the page did to the live nodes (the Persian text, the box around a table, the columns that were dropped, the heights, the mark of a row that
   the search hides) is not in the raw tree, so it is never touched, never undone and never done again.
   Anything that does not fit - the tree is not the one that was drawn (a page changed its own DOM in between, a node is gone from the document) - is not patched: the page is drawn from scratch, exactly as before.
   Used by app.js (the `innerHTML` of #page): NKPatch.make(el, setHtml) -> draw(html, patch) returns "same" | "patched" | "full". */
(function () {
  "use strict";

  var KEY = "__nkl";        // on a raw node: the live node it stands for
  var WRAP = "data-wrap";   // a box that a layer put around a node of the page (the list box around a table): the raw node is inside it, the raw parent is not

  var keyOf = function (n) { return n.nodeType === 1 ? (n.getAttribute("data-id") || n.id || "") : ""; };
  var same = function (a, b) { return a.nodeType === b.nodeType && a.nodeName === b.nodeName && keyOf(a) === keyOf(b); };
  var tokens = function (s) { return String(s || "").split(/\s+/).filter(Boolean); };

  /* style="a:b;c:d" as a list of [name, value]: a ";" inside brackets or quotes (an url) does not end a declaration */
  function decls(s) {
    var out = [], cur = "", depth = 0, q = "", i, c;

    s = String(s || "");

    for (i = 0; i <= s.length; i++) {
      c = i < s.length ? s.charAt(i) : ";";

      if (q) { cur += c; if (c === q) q = ""; continue; }

      if (c === '"' || c === "'") { q = c; cur += c; continue; }

      if (c === "(") depth++;
      else if (c === ")" && depth) depth--;

      if (c === ";" && !depth) {
        var k = cur.indexOf(":");

        if (k > 0) out.push([cur.slice(0, k).trim(), cur.slice(k + 1).trim()]);

        cur = "";
      } else cur += c;
    }

    return out;
  }

  function styles(live, from, to) {      // only the properties the page changed, added or took away: the heights and widths a layer set on the same node stay
    var a = {}, b = {};

    decls(from).forEach(function (d) { a[d[0]] = d[1]; });
    decls(to).forEach(function (d) { b[d[0]] = d[1]; });

    Object.keys(b).forEach(function (k) {
      if (a[k] === b[k]) return;

      var imp = /\s*!important\s*$/i.test(b[k]);

      live.style.setProperty(k, b[k].replace(/\s*!important\s*$/i, ""), imp ? "important" : "");
    });

    Object.keys(a).forEach(function (k) { if (!(k in b)) live.style.removeProperty(k); });
  }

  function classes(live, from, to) {      // only the classes the page added or took away: a class a layer put on the node (a dropped column, a box) stays
    var a = tokens(from), b = tokens(to);

    a.forEach(function (t) { if (b.indexOf(t) < 0) live.classList.remove(t); });
    b.forEach(function (t) { if (a.indexOf(t) < 0) live.classList.add(t); });
  }

  function attrs(a, b, live) {
    var i, at, ov;

    for (i = 0; i < b.attributes.length; i++) {
      at = b.attributes[i];
      ov = a.getAttribute(at.name);

      if (ov === at.value) continue;

      if (at.name === "class") classes(live, ov, at.value);
      else if (at.name === "style") styles(live, ov, at.value);
      else if (at.name === "value" && live.tagName === "INPUT") {
        var dirty = live.value !== (ov || "");      // typed into: the typed text is not touched

        live.setAttribute("value", at.value);

        if (!dirty) live.value = at.value;
      } else if (at.name === "checked" && live.tagName === "INPUT") {
        var moved = live.checked !== a.hasAttribute("checked");      // ticked or cleared by the person: it stays

        live.setAttribute("checked", at.value);

        if (!moved) live.checked = true;
      } else live.setAttribute(at.name, at.value);
    }

    for (i = 0; i < a.attributes.length; i++) {
      at = a.attributes[i];

      if (b.hasAttribute(at.name)) continue;

      if (at.name === "class") classes(live, at.value, "");
      else if (at.name === "style") styles(live, at.value, "");
      else if (at.name === "checked" && live.tagName === "INPUT") {
        var mv = live.checked !== true;

        live.removeAttribute("checked");

        if (!mv) live.checked = false;
      } else live.removeAttribute(at.name);
    }
  }

  function top(node, parent) {      // the node as a child of `parent`: a box a layer put around it counts as the node
    var t = node;

    while (t && t.parentNode !== parent && t.parentNode && t.parentNode.nodeType === 1 && t.parentNode.hasAttribute(WRAP)) t = t.parentNode;

    if (!t || t.parentNode !== parent) throw new Error("moved");

    return t;
  }

  function link(r, l) {      // right after a node was made from a raw node (a clone, or the same HTML parsed again): both have the same shape
    var rc = r.firstChild, lc = l.firstChild;

    r[KEY] = l;

    while (rc && lc) { link(rc, lc); rc = rc.nextSibling; lc = lc.nextSibling; }

    if (rc || lc) throw new Error("shape");
  }

  function make(el, setHtml) {
    var tag = el.tagName.toLowerCase(), raw = null, inert = document.implementation.createHTMLDocument("");      // (the raw trees live in a document of their own: nothing in them loads an image or runs anything)

    var parse = function (html) { var c = inert.createElement(tag); setHtml.call(c, html); return c; };

    function full(html) {
      setHtml.call(el, html);

      try { raw = parse(html); link(raw, el); } catch (e) { raw = null; }

      return "full";
    }

    var desc = function (r) { return !r ? "?" : r.nodeType === 3 ? "#text" : r.nodeType !== 1 ? "#" + r.nodeName : r.nodeName.toLowerCase() + (r.id ? "#" + r.id : r.getAttribute("class") ? "." + r.getAttribute("class").split(/\s+/)[0] : ""); };

    function alive(live, r) { if (!live || !live.isConnected) throw new Error("gone " + desc(r)); }      // (a node the page took out or replaced by itself: what it stands for is not on the screen any more)

    function drop(o, liveParent) {
      var live = o[KEY];

      alive(live, o);
      top(live, liveParent).remove();
    }

    function add(liveParent, b, before) {
      var c = b.cloneNode(true), ref = null;

      link(b, c);

      if (before) { alive(before[KEY], before); ref = top(before[KEY], liveParent); }

      liveParent.insertBefore(c, ref);
    }

    function kids(o, n) {
      var live = o[KEY], oc = Array.prototype.slice.call(o.childNodes), nc = Array.prototype.slice.call(n.childNodes), i = 0, j = 0, m, hit;

      alive(live, o);

      // the children of the live node are exactly the ones the raw tree knows (a box around a table counts as the table): the page added or took out nothing by itself in this node since the last draw; else it is not patched
      if (live.childNodes.length !== oc.length) throw new Error("shape " + desc(o));

      for (m = 0; m < oc.length; m++) {
        alive(oc[m][KEY], oc[m]);

        if (top(oc[m][KEY], live) !== live.childNodes[m]) throw new Error("shape " + desc(o));
      }

      while (i < nc.length) {
        var a = oc[j], b = nc[i];

        if (a && same(a, b)) { node(a, b); i++; j++; continue; }

        hit = -1;

        for (m = 1; a && m <= 8 && j + m < oc.length; m++) if (same(oc[j + m], b)) { hit = m; break; }      // the nodes in front of it are gone

        if (hit > 0) { for (m = 0; m < hit; m++) drop(oc[j + m], live); j += hit; continue; }

        add(live, b, a);      // a new node, before the one that is next
        i++;
      }

      while (j < oc.length) drop(oc[j++], live);
    }

    function node(a, b) {
      var live = a[KEY];

      b[KEY] = live;
      alive(live, a);

      if (a.nodeType === 3 || a.nodeType === 8) { if (a.data !== b.data) live.data = b.data; return; }

      if (a.nodeType !== 1) return;

      attrs(a, b, live);

      /* data-own: what is INSIDE this node is drawn by the page itself, later and by pieces (the body of a tab of the Panel page, the room its buttons move into): the draw of the page does not look inside it */
      if (a.hasAttribute("data-own") || b.hasAttribute("data-own")) {
        if (a.hasAttribute("data-own") !== b.hasAttribute("data-own")) throw new Error("own " + desc(a));

        return;
      }

      if (a.tagName === "TEXTAREA") {      // what is typed in it is not touched (the default changes, the typed text stays); a box nobody wrote in follows the page
        if (a.textContent !== b.textContent && document.activeElement !== live) live.defaultValue = b.textContent;

        link(b, live);
        return;
      }

      if (a.tagName === "SELECT") {
        var chosen = live.value, was = a.value;

        kids(a, b);

        if (document.activeElement !== live && chosen === was) live.value = b.value;
        else live.value = chosen;

        return;
      }

      kids(a, b);
    }

    /* html: what the page says now; patch: this draw may be done in place (a refresh nobody asked for); else the page is drawn from scratch */
    return function draw(html, patch) {
      draw.why = "";

      if (!patch || !raw || raw[KEY] !== el) return full(html);

      var next;

      try {
        next = parse(html);
        kids(raw, next);
      } catch (e) {
        draw.why = String(e && e.message);
        return full(html);      // some of it was done: a draw from scratch sets everything right
      }

      next[KEY] = el;
      raw = next;
      draw.why = "";
      return "patched";
    };
  }

  window.NKPatch = {make: make, decls: decls};
})();

