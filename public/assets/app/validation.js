/* Spread Scanner — frontend: the Does it work? tab and the Reference tab.
 * One of the files in public/assets/app/ (see core.js for how they fit together). */
(function (App) {
  "use strict";

  // Shared with the other frontend files (function declarations are hoisted).
  App.renderValidation = renderValidation;
  App.renderReference = renderReference;

  // ----------------------------------------------------------- validation


  function renderValidation() {
    var host = App.$("#validation-body");
    // Latched per panel, and only once that panel has actually rendered.
    // Setting one flag up front meant a single transient failure pinned the tab
    // on its error message until a reload — including the ordinary case where
    // calibration.json simply does not exist yet and appears after the next
    // scheduled run.
    if (!host.dataset.backtestDone) renderBacktestPanel(host);
    if (!host.dataset.calibrationDone) renderCalibrationPanel(host);
  }


  function renderBacktestPanel(host) {
    App.load("backtest").then(function (d) {
      host.dataset.backtestDone = "1";
      if (!d.ok) { App.$("#score-backtest").innerHTML = '<p class="empty">' + App.esc(d.note || "No backtest yet.") + "</p>"; return; }
      var b = d.buckets, s = d.squeeze;
      App.$("#score-backtest").innerHTML =
        '<p class="dim" style="font-size:.85rem">' + App.esc(d.universe) + " tickers · " + App.esc(d.history_years) +
        "y history · horizon " + App.esc(d.horizon_days) + " trading days · " + App.num(d.bars, 0) + " signal-bars</p>" +
        '<div class="panelcard"><p>' + App.esc(d.explainer) + "</p></div>" +
        "<h3 style=\"margin-top:18px\">By Setup Score</h3>" +
        App.statsTable([b.high, b.mid, b.low], "Score bucket", d.long_band_days) +
        '<div class="verdict ' + (d.verdict.holds ? "good" : "bad") + '">' + App.esc(d.verdict.text) + "</div>" +
        (d.verdict.long_band_text
          ? '<div class="verdict ' + (d.independent && d.independent.long_band &&
              d.independent.long_band.ci95_pts[0] > 0 ? "good" : "bad") + '">' +
            App.esc(d.verdict.long_band_text) + "</div>"
          : "") +
        (d.ex_earnings && d.ex_earnings.text
          ? '<p class="dim" style="font-size:.85rem">' + App.esc(d.ex_earnings.text) + "</p>"
          : "") +
        (d.independent
          ? '<p class="faint" style="font-size:.82rem">Verdicts use ' + App.num(d.independent.bars, 0) +
            " non-overlapping bars (one every " + App.esc(d.independent.step_days) +
            " trading days per name), with 95% intervals from resampling whole dates.</p>"
          : "") +
        "<h3>Squeeze on vs off</h3>" + App.statsTable([s.on, s.off], "State", d.long_band_days) +
        "<h3>Expected-move calibration</h3>" +
        "<p>Realized moves landed inside the ±1σ band <b>" + App.pct(d.coverage_pct, 0) +
        "</b> of the time against a theoretical 68%. " +
        (d.coverage_ok ? "The bands are well calibrated." : "The bands look mis-calibrated — consider tuning <code>vol_lookback</code>.") +
        "</p>" + App.impliedSection(d.implied) + App.directionSection(d.direction) +
        "<p class=\"faint\" style=\"font-size:.82rem\">" + App.esc(d.caveat) + "</p>";
    }).catch(function (e) {
      App.$("#score-backtest").innerHTML = App.loadError(e, "backtest", "python backtest.py --years 5");
    });
  }

  // Is the fit on this panel the one the scan on the other tabs actually used?
  // It need not be: weights.json is a working file and gitignored, while
  // calibration.json is committed — so a day when the calibration step fails
  // leaves yesterday's fit here beside a scan scored with the built-in weights.
  // Both facts were already on the page, on two different tabs, with nothing
  // reconciling them.
  function calibrationMismatch(d) {
    var w = (App.store.scan && App.store.scan.weights) || {};
    if (w.source === "auto-calibrated" && (!d.as_of || w.as_of === d.as_of)) return "";
    var used = w.source === "auto-calibrated"
      ? "weights calibrated " + App.esc(w.as_of || "on an earlier run")
      : "the built-in weights";
    return '<div class="notice" style="margin:14px 0 0">' +
      "<b>This fit is not what the current scan used.</b> The scan on the other tabs scored with " +
      used + ", while the calibration below is from " + App.esc(d.as_of || "an earlier run") +
      ". The calibration step is best-effort — when it cannot fetch its history the scan " +
      "falls back and says so, but the last good fit stays published here." +
      "</div>";
  }

  function renderCalibrationPanel(host) {
    App.load("calibration").then(function (d) {
      host.dataset.calibrationDone = "1";
      if (!d.ok) { App.$("#calibration").innerHTML = '<p class="empty">' + App.esc(d.note || "Not calibrated yet.") + "</p>"; return; }
      var sep = d.separation;
      var quint = d.separation_basis === "quintile";
      App.$("#calibration").innerHTML = calibrationMismatch(d) +
        '<div class="panelcard"><p>' + App.esc(d.method) + "</p></div>" +
        '<table class="stats" style="margin-top:14px"><thead><tr><th>Weights (from the train split)</th>' +
        (quint
          ? '<th class="r">top 20% of score</th><th class="r">bottom 20%</th>'
          : '<th class="r">score ≥ 60</th><th class="r">score &lt; 30</th>') +
        '<th class="r">separation</th>' +
        "</tr></thead><tbody>" +
        ["heuristic", "calibrated"].map(function (k) {
          var r = sep[k];
          return "<tr><td>" + App.esc(k) + " (" +
            Object.keys(r.weights).map(function (w) { return Math.round(r.weights[w] * 100); }).join("/") +
            ')</td><td class="r">' + App.pct(r.high_break_pct, 0) + '</td><td class="r">' +
            App.pct(r.low_break_pct, 0) + '</td><td class="r">' +
            (r.separation_pts >= 0 ? "+" : "") + App.num(r.separation_pts, 0) + " pts</td></tr>";
        }).join("") + "</tbody></table>" +
        '<div class="verdict ' + (d.verdict.holds ? "good" : "bad") + '">' + App.esc(d.verdict.text) + "</div>";
    }).catch(function (e) {
      App.$("#calibration").innerHTML = App.loadError(e, "calibration", "python calibrate.py");
    });
  }

  // ------------------------------------------------------------ reference

  function renderReference() {
    var host = App.$("#glossary");
    if (host.dataset.done) return;
    host.dataset.done = "1";
    var g = App.ref("glossary", {});
    var titles = {
      score: "Setup Score", iv_rank: "IV Rank", iv_percentile: "IV Percentile",
      premium_score: "Premium score", iv_hv_ratio: "IV / HV", vrp: "Volatility risk premium",
      implied_move_pct: "Implied move", hist_move_pct: "Realized (historical) move",
      term_structure: "Term structure", skew: "Skew", liquidity: "Liquidity",
      pop: "Probability of profit", fill: "Fill price", credit_to_width: "Credit to width",
      em_pct: "Expected move (±1σ)", squeeze: "TTM squeeze", lean: "Lean",
      earnings: "Earnings", debt_cash_ratio: "Debt % / Cash %",
      long_dated: "The long-dated expiry", leaps_vega: "Vega over a year",
      reward_to_risk: "Reward to risk", annualised_return: "Reward to risk, annualised",
      risk_form: "What secures a position"
    };
    // Any key without an entry above reads as a sentence rather than as the raw
    // snake_case name, so a new glossary term is never published looking like one.
    function title(k) {
      if (titles[k]) return titles[k];
      var words = k.replace(/_/g, " ");
      return words.charAt(0).toUpperCase() + words.slice(1);
    }
    host.innerHTML = "<dl class=\"glossary\">" + Object.keys(g).map(function (k) {
      return "<dt>" + App.esc(title(k)) + "</dt><dd>" + App.esc(g[k]) + "</dd>";
    }).join("") + "</dl>";

    var play = App.ref("playbook", {});
    App.$("#strategy-list").innerHTML = "<dl class=\"glossary\">" + Object.keys(play).map(function (k) {
      return "<dt>" + App.esc(k.replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); })) +
        "</dt><dd>" + App.esc(play[k]) + "</dd>";
    }).join("") + "</dl>";
  }
})(window.SpreadApp = window.SpreadApp || {});
