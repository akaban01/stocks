/* Spread Scanner — frontend: the Market regime tab.
 * One of the files in public/assets/app/ (see core.js for how they fit together).
 *
 * Renders data/regime.json, which a weekly workflow writes with
 * `python -m regime_backtest.run --json public/data/regime.json`. The pure
 * table/chart builders live in assets/regime.js so the node tests can run
 * them; this file only fetches, picks the cost, and wires the hover. */
(function (App) {
  "use strict";

  App.renderRegime = renderRegime;

  var G = window.SpreadRegime;
  var cost = null;            // the cost key on screen ("0", "5", "20")
  var drawnWidth = 0;         // the chart width last drawn at
  var resizeTimer = null;

  function renderRegime() {
    var host = App.$("#regime-body");
    if (host.dataset.done) return;
    App.load("regime").then(function (d) {
      host.dataset.done = "1";
      if (!G.schemaOk(d)) {
        host.innerHTML = '<p class="empty">This page expects regime schema ' + G.SCHEMA +
          " but data/regime.json says " + App.esc(d && d.regime_schema) +
          ". The next weekly run rewrites it; until then nothing here would be reliable.</p>";
        return;
      }
      var keys = G.costKeys(d);
      var saved = null;
      try { saved = localStorage.getItem("regimecost"); } catch (e) { saved = null; }
      cost = keys.indexOf(saved) !== -1 ? saved : String(Number(d.default_cost_bp));
      host.innerHTML = page(d);
      wireCost(d);
      drawCostParts(d);
      // Redraw the charts when the width they were drawn for changes enough to matter.
      window.addEventListener("resize", function () {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
          var host = App.$("#rg-equity");
          if (!host || !host.offsetParent) return;          // tab not on screen
          if (Math.abs((host.clientWidth || 0) - drawnWidth) > 40) drawCostParts(d);
        }, 200);
      });
    }).catch(function (e) {
      host.innerHTML = App.loadError(e, "regime", "python -m regime_backtest.run --json public/data/regime.json");
    });
  }

  // Rule name -> colour index, in the payload's order (buy-and-hold first, grey).
  function order(d) {
    var o = {};
    d.rules.forEach(function (r, i) { o[r.name] = i; });
    return o;
  }

  function page(d) {
    var esc = App.esc;
    var o = order(d);
    var cw = d.common_window;
    var credit = d.credit;
    var sw = d.sweep;
    var oos = d.oos;
    var ev = d.events;

    return '<div class="lede">Do common "get out of the market" rules beat simply holding the S&amp;P 500 ' +
        "(SPY) after trading costs? Each rule is either 100% SPY or 100% 3-month T-bills. Results through <b>" +
        esc(d.data_through) + "</b>, computed " + esc(App.localTime(d.generated_at)) + " from commit <code>" +
        esc(d.commit) + "</code>. Rerun weekly by GitHub Actions; nothing on this tab is recomputed in your " +
        "browser except the weekly drawdown curves.</div>" +

      (credit.is_proxy
        ? '<div class="notice"><b>The credit rules were tested on ' + esc(credit.label) +
          ", an investment-grade spread, not on high-yield (HY OAS).</b> FRED no longer serves the full HY OAS " +
          "history, so the two credit rows below say nothing about the high-yield version of the rule.</div>"
        : "") +

      "<h2>Bottom line</h2>" +
      '<p class="faint" style="font-size:.85rem">Common window ' + esc(cw.start) + " → " + esc(cw.end) + ", " +
        esc(d.default_cost_bp) + " bp per side. Sharpe differences under " + esc(d.material_sharpe) +
        " are treated as no difference; full reasoning per rule is under Verdicts.</p>" +
      G.bottomLine(d, o) +

      '<h2>Summary</h2><div class="filters" id="rg-costs" role="group" aria-label="Cost per switch">' +
        G.costKeys(d).map(function (k) {
          return '<button class="chip" data-cost="' + esc(k) + '" aria-pressed="false">' + esc(k) +
            " bp per side</button>";
        }).join("") + "</div>" +
      '<p class="faint" style="font-size:.85rem">The same dates for every rule, so rows compare directly.</p>' +
      '<div id="rg-summary"></div>' +

      "<h3>Growth of $1 (log scale)</h3>" +
      '<div class="rg-chartwrap" id="rg-equity"></div>' +
      '<div class="rg-legend" id="rg-legend"></div>' +
      "<h3>Drawdowns vs buy-and-hold</h3>" +
      '<p class="faint" style="font-size:.85rem">Grey area: buy-and-hold. Drawn from weekly closes, so a curve ' +
        "can sit a little above the worst daily close; the Max DD column above is the exact daily figure.</p>" +
      '<div id="rg-dd"></div>' +

      "<h2>Cost sensitivity</h2>" + G.costTable(d) +

      "<h2>Crisis windows</h2>" +
      '<p class="faint" style="font-size:.85rem">Return inside each window, and the worst drawdown inside it, at ' +
        esc(d.default_cost_bp) + " bp per side. — where a rule's history does not cover the window.</p>" +
      G.crisisTable(d.crisis) +

      "<h2>Each rule over its own longest history</h2>" +
      '<p class="faint" style="font-size:.85rem">Start dates differ, so compare each row with buy-and-hold over ' +
        "the same span (last three columns), not with the other rows.</p>" +
      G.longestTable(d.longest, o) +

      "<h2>Credit rule: parameter sweep</h2>" +
      '<p class="faint" style="font-size:.85rem">Headline rule: ' + esc(credit.rule) +
        " (outlined cell — the middle of the grid, fixed in advance). Each cell is a Sharpe ratio; blue beats " +
        "buy-and-hold over the same span, orange trails it.</p>" +
      '<div class="rg-heats">' + sw.panels.map(function (p) {
        return G.heatmap(p, sw.row_labels, sw.col_labels, G.defaultCell(sw));
      }).join("") + "</div>" +
      "<p>" + esc(sw.assessment) + "</p>" +

      "<h3>Out of sample</h3>" +
      "<p>Parameters chosen on the first half only (" + esc(oos.first_start) + " → " + esc(oos.mid) + "): <b>" +
        esc(oos.chosen) + "</b>, Sharpe " + App.num(oos.first_sharpe, 2) + " vs buy-and-hold " +
        App.num(oos.first_bh_sharpe, 2) + ". Frozen and run on the second half, it ranks <b>" +
        esc(oos.rank_second) + " of " + esc(oos.cells) + "</b> settings (buy-and-hold Sharpe " +
        App.num(oos.second_bh_sharpe, 2) + ").</p>" +
      G.summaryTable(oos.rows, null) +

      "<h2>Event studies</h2>" +
      '<p class="faint" style="font-size:.85rem">' + esc(ev.forward_note) + "</p>" +
      "<h3>Capitulation (" + ev.capitulation.rows.length + " events)</h3>" +
      "<p>" + esc(ev.capitulation.text) + "</p>" + G.eventTable(ev.capitulation.rows) +
      "<h3>Yield-curve re-steepening (" + ev.resteepening.rows.length + " events)</h3>" +
      "<p>" + esc(ev.resteepening.text) + "</p>" + G.eventTable(ev.resteepening.rows) +
      '<p class="faint" style="font-size:.85rem">' + esc(ev.peak_trough_note) + "</p>" +
      G.peakTroughTable(ev.resteepening.peak_trough) +

      "<h2>Verdicts</h2>" +
      '<p class="faint" style="font-size:.85rem">Common window, ' + esc(d.default_cost_bp) + " bp per side. " +
        "Intervals are a paired block bootstrap of the Sharpe difference.</p>" +
      G.verdictDetails(d) +

      "<h2>How to read this</h2><ul class=\"rg-glossary\">" + d.glossary.map(function (g) {
        // The glossary lines are "**Term**: text" — bold the term without trusting markup.
        var m = /^\*\*(.+?)\*\*:?\s*(.*)$/.exec(g);
        return m ? "<li><b>" + esc(m[1]) + "</b>: " + esc(m[2]) + "</li>" : "<li>" + esc(g) + "</li>";
      }).join("") + "</ul>" +

      "<h2>Data and caveats</h2>" +
      "<p>Credit spread: <b>" + esc(credit.source) + "</b>. Sources tried, in order:</p><ol>" +
        credit.log.map(function (l) { return "<li>" + esc(String(l).replace(/^\d+\.\s*/, "")) + "</li>"; }).join("") +
        "</ol>" +
      (d.notes || []).map(function (n) { return '<p class="faint">' + esc(n) + "</p>"; }).join("") +
      '<div class="tablewrap"><table class="stats"><thead><tr><th>Series</th><th class="r">First</th>' +
        '<th class="r">Last</th><th class="r">Rows</th></tr></thead><tbody>' +
        d.coverage.map(function (c) {
          return "<tr><td>" + esc(c.series) + '</td><td class="r">' + esc(c.first) + '</td><td class="r">' +
            esc(c.last) + '</td><td class="r">' + App.num(c.rows, 0) + "</td></tr>";
        }).join("") + "</tbody></table></div>" +
      "<ul>" + d.caveats.map(function (c) { return "<li>" + esc(c) + "</li>"; }).join("") + "</ul>";
  }

  function wireCost(d) {
    var buttons = document.querySelectorAll("#rg-costs button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () {
        cost = this.dataset.cost;
        try { localStorage.setItem("regimecost", cost); } catch (e) { /* private mode */ }
        drawCostParts(d);
      });
    }
  }

  // The parts of the tab that follow the cost switch: the summary table and both charts.
  function drawCostParts(d) {
    var buttons = document.querySelectorAll("#rg-costs button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].dataset.cost === cost ? "true" : "false");
    }
    var o = order(d);
    App.$("#rg-summary").innerHTML = G.summaryTable(d.summary[cost] || [], o);

    var dates = d.series.dates;
    var eq = d.series.equity[cost] || {};
    var names = d.rules.map(function (r) { return r.name; }).filter(function (n) { return eq[n]; });
    var lines = names.map(function (n) {
      return { name: n, values: eq[n], color: G.colorFor(o[n]), width: o[n] === 0 ? 2.6 : 1.6 };
    });
    var host = App.$("#rg-equity");
    // Drawn at the width it is shown at, so axis text stays readable on a phone
    // instead of a 900-unit chart scaled down to a third.
    var W = Math.max(300, Math.round(host.clientWidth || 900));
    drawnWidth = W;
    var chart = G.lineChart(dates, lines, { log: true, endLabels: true, width: W, height: W < 600 ? 260 : 340,
                                            label: "Growth of $1 for each rule, log scale" });
    host.innerHTML = chart.svg + '<div class="rg-tip" hidden></div>';
    hover(host, chart, dates, lines, function (v) { return App.num(v, 2) + "×"; });
    App.$("#rg-legend").innerHTML = names.map(function (n) {
      return '<span><span class="rg-swatch" style="background:' + G.colorFor(o[n]) + '"></span>' +
        App.esc(n) + "</span>";
    }).join("");

    var bench = G.drawdowns(eq[names[0]] || []);
    App.$("#rg-dd").innerHTML = names.slice(1).map(function (n, k) {
      return '<div class="rg-ddrow"><div class="rg-ddlabel">' + App.esc(n) + '</div>' +
        '<div class="rg-chartwrap" data-dd="' + k + '"></div></div>';
    }).join("");
    names.slice(1).forEach(function (n, k) {
      var dd = G.drawdowns(eq[n]);
      var series = [
        { name: names[0], values: bench, color: "rgba(154,165,177,.55)", fill: "rgba(154,165,177,.22)", width: 1 },
        { name: n, values: dd, color: G.colorFor(o[n]), width: 1.6 }
      ];
      var c = G.lineChart(dates, series, { width: W, height: W < 600 ? 110 : 120, padRight: 16,
                                           label: n + " drawdown vs buy-and-hold" });
      var wrap = document.querySelector('#rg-dd [data-dd="' + k + '"]');
      wrap.innerHTML = c.svg + '<div class="rg-tip" hidden></div>';
      hover(wrap, c, dates, series, function (v) { return G.fpct(v); });
    });
  }

  // Crosshair and tooltip. Hit target is the whole plot, not the 2px line.
  function hover(wrap, chart, dates, series, fmt) {
    var svg = wrap.querySelector("svg");
    var tip = wrap.querySelector(".rg-tip");
    var cross = svg.querySelector(".rg-cross");
    function move(e) {
      var box = svg.getBoundingClientRect();
      if (!box.width) return;
      var x = (e.clientX - box.left) * (chart.W / box.width);
      var i = chart.indexAt(x);
      var cx = chart.xAt(i).toFixed(1);
      cross.setAttribute("x1", cx); cross.setAttribute("x2", cx);
      cross.setAttribute("visibility", "visible");
      tip.innerHTML = G.tipHtml(dates[i], series.map(function (s) {
        return { name: s.name, color: s.fill ? "#9aa5b1" : s.color, value: s.values[i] };
      }), fmt);
      tip.hidden = false;
      var left = (chart.xAt(i) / chart.W) * box.width;
      tip.style.left = Math.min(Math.max(0, left + 12), box.width - tip.offsetWidth) + "px";
    }
    function leave() { tip.hidden = true; cross.setAttribute("visibility", "hidden"); }
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerdown", move);
    svg.addEventListener("pointerleave", leave);
  }
})(window.SpreadApp = window.SpreadApp || {});
