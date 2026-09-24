/* Spread Scanner — frontend: loading the payloads and starting the page.
 * One of the files in public/assets/app/ (see core.js for how they fit together). */
(function (App) {
  "use strict";

  // ------------------------------------------------------------------ boot

  // Left/Right (and Home/End) move between tabs, as a tablist is expected to.
  function tabKeydown(e) {
    var keys = { ArrowLeft: -1, ArrowRight: 1, Left: -1, Right: 1 };
    var buttons = [].slice.call(document.querySelectorAll(".tabs button"));
    var here = buttons.indexOf(this);
    var next = null;
    if (e.key in keys) next = (here + keys[e.key] + buttons.length) % buttons.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = buttons.length - 1;
    if (next === null) return;
    e.preventDefault();
    App.showTab(buttons[next].dataset.tab);
    buttons[next].focus();
  }

  function boot() {
    var buttons = document.querySelectorAll(".tabs button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () { App.showTab(this.dataset.tab); });
      buttons[i].addEventListener("keydown", tabKeydown);
    }

    var views = document.querySelectorAll("#chartviews button");
    for (var v = 0; v < views.length; v++) {
      views[v].addEventListener("click", function () { App.showChartView(this.dataset.view); });
    }
    App.wireRepeat();
    App.wireSpread();
    App.wireBacktest();
    App.wireMoney();
    App.wireBacktestViews();
    // A fragment is an explicit request, so it outranks the last visit's tab.
    var linked = App.parseHash();
    var savedView = null, savedBtView = null;
    try { savedView = localStorage.getItem("chartview"); } catch (e) { savedView = null; }
    try { savedBtView = localStorage.getItem("backtestview"); } catch (e) { savedBtView = null; }
    // Nothing is on screen yet — the showTab below writes both halves at once.
    App.hashLock = true;
    // A view in the fragment belongs to whichever tab owns that name, so it is
    // only allowed to set the view of the tab it came with. Otherwise the last
    // visit's view stands.
    App.showChartView((linked && linked.tab === "charts" && linked.view) || savedView);
    App.showBacktestView((linked && linked.tab === "backtest" && linked.view) || savedBtView);
    // And the name the fragment asked for, before anything renders. A cold load
    // does not go through applyHash — that listens for hashchange, and arriving
    // at a URL is not a change — so a deep link into one name was parsed here
    // and then quietly dropped in favour of whatever was remembered.
    if (linked) App.applyName(linked.tab, linked.ticker);
    App.hashLock = false;

    window.addEventListener("hashchange", App.applyHash);

    App.load("scan").then(function (d) {
      if (!d || !d.schema_version) throw new Error("scan.json is missing or malformed");
      if (d.schema_version.split(".")[0] !== App.SCHEMA_MAJOR) {
        App.$("#schema-warning").hidden = false;
        App.$("#schema-warning").textContent =
          "This page expects scan schema " + App.SCHEMA_MAJOR + ".x but the data says " +
          d.schema_version +
          ". Some fields may not render.";
      }
      App.$("#loading").hidden = true;
      App.$("#app").hidden = false;
      App.renderPlaybook();
      App.renderScanner();
      var saved = null;
      try { saved = localStorage.getItem("tab"); } catch (e) { saved = null; }
      App.showTab((linked && linked.tab) || saved || "playbook");
    }).catch(function (e) {
      App.$("#loading").innerHTML =
        '<p class="empty">Could not load <code>data/scan.json</code> — ' + App.esc(e.message) + ".</p>" +
        '<p class="empty faint">The scan runs each weekday after the US close and writes it. ' +
        "Locally, run <code>python run.py</code> and serve this folder over HTTP " +
        "(<code>python -m http.server --directory public</code>) — <code>file://</code> blocks fetch.</p>";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(window.SpreadApp = window.SpreadApp || {});
