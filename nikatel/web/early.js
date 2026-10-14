/* Runs in the head, before the first paint: decides which phone layout is used. "Fluid" (the default) keeps rows as rows and lets bars and tabs shrink (mobile2.css + fluid.css);
   "classic" is the older phone layout (mobile.css: tables turned into cards, bars that wrap). Switch: localStorage "nk.fluid" = "0" (Settings → Appearance), or `?fluid=0` once. */
(function () {
  var on = true;

  try {
    var q = /[?&]fluid=([01])/.exec(location.search);

    if (q) localStorage.setItem("nk.fluid", q[1]);

    on = localStorage.getItem("nk.fluid") !== "0";
  } catch (e) { /* storage blocked: the default stays */ }

  var h = document.documentElement;

  if (on) h.setAttribute("data-fluid", "1");

  window.NK_FLUID = on;
  var swap = function (id, off) { var l = document.getElementById(id); if (l) { l.media = off ? "not all" : l.getAttribute("data-media") || "all"; } };
  document.addEventListener("DOMContentLoaded", function () { swap("css-classic", on); swap("css-fluid-phone", !on); });
  swap("css-classic", on); swap("css-fluid-phone", !on);
})();
